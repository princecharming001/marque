"""Model providers, bring-your-own-key and model-call tracing (ARCHITECTURE §9, plan §8).

``ModelSpec`` describes one model for one role. The house default is Anthropic (:mod:`studio.config`
model ids); BYOK supplies a spec for the **Director** only; critics stay house. :func:`build_model`
turns a spec into a **native pydantic-ai model** (``AnthropicModel``, ``OpenAIResponsesModel``,
``GoogleModel``, or ``OpenAIChatModel`` for OpenAI-compatible endpoints); :class:`TracedModel` wraps it
so every request lands in the job's ``trace.jsonl``.

Design choices (quality first; verified against the current Anthropic docs on 2026-09-27)
-------------------------------------------------------------------------------------
* **Effort, set explicitly on every call.** ``claude-fable-5-1`` and ``claude-opus-5-5`` accept
  ``output_config.effort`` in ``low | medium | high | xhigh | max``; Opus 5.5 *defaults to medium*, so an
  omitted effort would silently run the Director one level lower. The house Director runs at ``max``.
  pydantic-ai maps ``anthropic_effort`` to ``output_config.effort``.
* **Adaptive thinking, never disabled.** Both models think adaptively and return 400 for
  ``thinking: {type: "disabled"}`` or ``budget_tokens``. We send ``{type: "adaptive", display:
  "summarized"}``: display only controls visibility (billing and depth are unchanged), and readable
  summaries make the trace auditable. Older budget-only models get a budget below ``max_tokens``.
* **No forced tool choice.** Fable 5.1 / Mythos 5.1 / Opus 5.5 return 400 for ``tool_choice`` ``any`` or
  ``tool``; capabilities record ``forced_tool_choice=False`` (taken from pydantic-ai's model profile) so
  the Director steers with ``auto`` plus instructions.
* **Large output budget, always streamed.** At high effort ``max_tokens`` caps thinking *plus* text; we
  give the full 128K the 4.6+/5.x models allow and a 30-minute timeout, because single turns on hard
  edits can run many minutes. Anthropic requests are served through the streaming endpoint even when
  the caller runs a non-streaming agent (:class:`StreamingRequestModel`): a long silent HTTP request is
  what idle-connection drops kill, and pydantic-ai's streamed parser is the one that tolerates the
  ``fallback`` content block below (its non-streaming parser asserts on unknown blocks).
* **Refusals never end an edit silently.** Fable 5.1 / Opus 5.5 run safety classifiers that can decline
  benign requests (HTTP 200, ``stop_reason: "refusal"``). Models that support it get the server-side
  ``fallbacks: "default"`` opt-in (beta ``server-side-fallback-2026-07-01``, verified live on both
  house models), which re-runs a declined request on Anthropic's recommended model for that category.
  If the whole chain declines, the request raises pydantic-ai's ``ContentFilterError`` (also for a
  partial, mid-stream refusal, whose output must be discarded) and :func:`map_provider_error` reports
  it as ``kind="refusal"``.
* **Prompt caching with a 1 h TTL** on instructions, tools and the growing history: identical outputs,
  and a Director turn often outlasts the 5-minute default TTL.
* **Images** (verified against the vision docs): Claude 4.7+ reads images up to 2576 px on the long edge
  and 4784 visual tokens (``ceil(w/28)·ceil(h/28)``; 1568 px / 1568 tokens on older models) and downscales
  anything larger; above 20 images in one request (history included) every image must be ≤ 2000 px or the
  request is rejected; up to 600 images per request (100 on 200K-context models), 10 MB per image
  (base64) and 32 MB per request. :class:`Capabilities` carries these so the frame tools size contact
  sheets the API never resamples. :class:`ImageUploader` puts sheets in the Anthropic Files API (GA,
  expiring after 48 h) so a long Director conversation references them by ``file_id`` instead of
  re-sending base64 on every turn, which lifts the 32 MB request ceiling on how much the Director can look.
* **Keys stay in memory.** ``ModelSpec.api_key`` is excluded from repr and serialization and registered
  for redaction; SDK clients always receive the key explicitly (never an env fallback), so a BYOK run
  can never silently bill the house key. :func:`redact_secrets` masks registered keys, house keys and
  anything shaped like a provider key before it reaches a trace, log or error message.
* **Clear errors.** :func:`map_provider_error` turns SDK / HTTP / pydantic-ai errors into a
  :class:`ProviderError` with a kind (auth, billing, permission, not_found, too_large, rate_limit,
  quota, overloaded, bad_request, timeout, connection) and a plain-English, redacted message.
  :func:`validate_key` checks a key with a list-models call (plus a model lookup).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai.models.wrapper import WrapperModel

from studio.config import MissingKeyError, get_settings, redact

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
    from pydantic_ai.settings import ModelSettings

    from studio.config import Settings
    from studio.jobs import Job

__all__ = [
    "Provider", "Role", "EFFORTS", "PROVIDERS", "PROVIDER_ALIASES", "ROLE_EFFORT", "DEFAULT_WATCHER_MODEL",
    "OPENROUTER_BASE_URL", "SERVER_FALLBACK_BETA", "FILE_EXPIRY_S", "Capabilities", "ModelSpec", "ProviderError",
    "KeyCheck", "TracedModel", "StreamingRequestModel", "ImageUploader", "normalize_provider", "normalize_effort",
    "capabilities_for", "house_spec", "director_fallback_spec", "spec_from_cli", "model_settings", "build_model",
    "make_image_uploader", "map_provider_error", "validate_key", "validate_key_sync", "register_secret",
    "known_secrets", "redact_secrets", "mask_key_like", "trace_event", "response_summary", "visual_tokens",
    "native_model",
]

Provider = Literal["anthropic", "openai", "google", "openai_compat"]
Role = Literal["director", "critic", "watcher", "helper"]

PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "google", "openai_compat")
PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "anthropic", "claude": "anthropic",
    "openai": "openai", "gpt": "openai", "openai_responses": "openai", "openai-responses": "openai",
    "google": "google", "gemini": "google", "google-gla": "google", "google_gla": "google",
    "openai_compat": "openai_compat", "openai-compat": "openai_compat", "openai_compatible": "openai_compat",
    "openai-compatible": "openai_compat", "compat": "openai_compat", "openrouter": "openai_compat",
    "vllm": "openai_compat", "ollama": "openai_compat", "together": "openai_compat",
}
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GOOGLE_API_BASE = "https://generativelanguage.googleapis.com"

#: Effort names accepted in a spec (``none``/``minimal`` exist on OpenAI reasoning models only).
EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_EFFORT_RANK = {e: i for i, e in enumerate(EFFORTS)}

#: Default effort per role when a spec leaves it unset. The Director's comes from Settings
#: (``STUDIO_DIRECTOR_EFFORT``, default ``max``). Quality is the only goal, so judges also run at ``max``;
#: the watcher and helpers do perception/lookup work where ``high`` avoids overthinking.
ROLE_EFFORT: dict[str, str] = {"director": "max", "critic": "high", "watcher": "high", "helper": "high"}

#: Anthropic prompt-cache TTL for every role. Written tokens cost 2x the input price at 1h (1.25x at 5m); reads are
#: cheap. 1h keeps the Director's conversation warm across the caption previews and seam checks inside a stage.
#: Cost accounting (:mod:`studio.agent.cost`) prices writes at this TTL.
CACHE_TTL = "1h"

#: Watcher default when a Google key exists (plan §4: Gemini is the only frontier family that watches
#: and hears video; the bake-off between Flash and Pro is configurable via ``STUDIO_WATCHER_MODEL``).
DEFAULT_WATCHER_MODEL = "gemini-3.8-flash"

#: Request timeout (seconds) for long thinking turns; the SDK default of 10 min can cut a max-effort turn.
LONG_TIMEOUT_S = 1800.0

#: Beta header for the scalar ``fallbacks: "default"`` form (the array form uses ``-2026-06-01``).
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Models whose requests accept ``fallbacks: "default"`` (Fable/Mythos 5.x and the Opus 5 line).
_SERVER_FALLBACK_PREFIXES = ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5-5")
#: Models whose API behaviour pydantic-ai's profile table does not describe yet (checked 2026-10-09 against the
#: Claude docs): Haiku 5.5 runs adaptive thinking by default with effort low..max and a 1M context, and rejects
#: ``budget_tokens``; Sonnet 5.5 rejects forced ``tool_choice`` like Opus 5.5.
_PROFILE_OVERRIDES: dict[str, dict[str, Any]] = {
    "claude-haiku-5-5": {"anthropic_supports_adaptive_thinking": True, "anthropic_supports_effort": True,
                         "anthropic_supports_xhigh_effort": True},
    "claude-sonnet-5-5": {"anthropic_supports_forced_tool_choice": False},
}
#: Lifetime of images uploaded for the Director (Files API allows 1 h .. 90 days).
FILE_EXPIRY_S = 48 * 3600
_ANTHROPIC_API_HOSTS = ("https://api.anthropic.com",)

_TRUE_1M_CONTEXT_PREFIXES = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-5-5",
)
_HIGHRES_PREFIXES = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
)


# ============================================================================================ secrets
_secret_lock = threading.Lock()
_registered: set[str] = set()

_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-(?:proj-|svcacct-|or-v1-|or-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:x-api-key|x-goog-api-key|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{12,}"),
)
_REDACTED = "<redacted>"


def register_secret(value: str | None) -> None:
    """Remember an in-memory secret (a BYOK key) so :func:`redact_secrets` masks it everywhere."""
    if value and len(value) >= 6:
        with _secret_lock:
            _registered.add(value)


def known_secrets(settings: Settings | None = None) -> list[str]:
    """Registered BYOK keys plus the configured house keys (never log the result)."""
    try:
        s = settings or get_settings()
        house = s.secret_values()
    except Exception:  # pragma: no cover - settings always load
        house = []
    with _secret_lock:
        return [*house, *_registered]


def mask_key_like(text: str) -> str:
    """Mask substrings shaped like provider keys (``sk-…``, ``sk-ant-…``, ``AIza…``, bearer tokens)."""
    out = text
    for pat in _KEY_PATTERNS:
        if pat.groups:
            out = pat.sub(lambda m: (m.group(1) or "") + _REDACTED, out)
        else:
            out = pat.sub(_REDACTED, out)
    return out


def redact_secrets(obj: Any, settings: Settings | None = None) -> Any:
    """Redact known secret values (house + BYOK), secret-named mapping keys and key-shaped strings."""
    red = redact(obj, known_secrets(settings))
    return _mask_walk(red)


def _mask_walk(obj: Any) -> Any:
    if isinstance(obj, str):
        return mask_key_like(obj)
    if isinstance(obj, Mapping):
        return {k: _mask_walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_walk(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_mask_walk(v) for v in obj)
    return obj


def trace_event(job: Job | None, event: str, **fields: Any) -> dict[str, Any] | None:
    """Append a redacted event to ``job``'s ``trace.jsonl`` (no-op without a job; never raises)."""
    if job is None:
        return None
    try:
        from studio.jobs import to_jsonable

        return job.trace(event, **redact_secrets(to_jsonable(fields)))
    except Exception:  # pragma: no cover - tracing must never break an edit
        return None


# ============================================================================================ capabilities
class Capabilities(BaseModel):
    """What a model can take and how requests to it must be shaped (defaults: :func:`capabilities_for`)."""

    model_config = ConfigDict(extra="forbid")

    vision: bool = True
    video_input: bool = False
    strict_tools: bool = True
    forced_tool_choice: bool = True
    max_images: int = Field(default=100, ge=0, description="Images per request")
    max_image_edge_px: int = Field(default=1568, ge=64, description="Long edge the model sees natively")
    many_images_threshold: int | None = Field(default=20, description="Above this many images per request…")
    many_images_edge_px: int | None = Field(default=2000, description="…every image must fit this edge")
    max_image_bytes: int = Field(default=5_000_000, ge=1, description="Raw bytes per image")
    max_image_tokens: int | None = Field(default=None, ge=1,
                                         description="Visual tokens per image (28x28 px patches) the model sees "
                                                     "natively; larger images are downscaled by the provider")
    max_request_bytes: int = Field(default=32_000_000, ge=1)
    effort_levels: tuple[str, ...] = ()
    adaptive_thinking: bool = False
    max_output_tokens: int = Field(default=32_000, ge=256)
    context_tokens: int = Field(default=200_000, ge=1)
    server_fallbacks: bool = Field(default=False, description="Accepts the server-side refusal fallback opt-in")
    files_api: bool = Field(default=False, description="Images can be uploaded once and referenced by file id")

    def image_edge_limit(self) -> int:
        """Largest edge that is safe in any request (including many-image requests)."""
        lim = self.max_image_edge_px
        if self.many_images_edge_px:
            lim = min(lim, self.many_images_edge_px)
        return lim


def visual_tokens(width: int, height: int) -> int:
    """Claude's visual-token cost of an image: one token per 28x28 px patch."""
    return math.ceil(max(1, width) / 28) * math.ceil(max(1, height) / 28)


def _anthropic_profile(model: str) -> dict[str, Any]:
    try:
        from pydantic_ai.profiles.anthropic import anthropic_model_profile

        return dict(anthropic_model_profile(model) or {})
    except Exception:  # pragma: no cover - profile helper always importable with the extra
        return {}


def capabilities_for(provider: str, model: str) -> Capabilities:
    """Capability defaults for a provider/model (the table; explicit spec capabilities override it)."""
    p = normalize_provider(provider)
    m = (model or "").lower()
    if p == "anthropic":
        prof = _anthropic_profile(m)
        for prefix, over in _PROFILE_OVERRIDES.items():
            if m.startswith(prefix):
                prof.update(over)
        big_ctx = m.startswith(_TRUE_1M_CONTEXT_PREFIXES)
        levels: tuple[str, ...] = ()
        if prof.get("anthropic_supports_effort"):
            levels = ("low", "medium", "high", "max")
            if prof.get("anthropic_supports_xhigh_effort"):
                levels = ("low", "medium", "high", "xhigh", "max")
            if m.startswith("claude-opus-4-5"):
                levels = ("low", "medium", "high")
        modern = m.startswith(_TRUE_1M_CONTEXT_PREFIXES)
        highres = m.startswith(_HIGHRES_PREFIXES)
        return Capabilities(
            vision=True, video_input=False,
            strict_tools=bool(prof.get("supports_json_schema_output", True)),
            forced_tool_choice=bool(prof.get("anthropic_supports_forced_tool_choice", True)),
            max_images=600 if big_ctx else 100,
            max_image_edge_px=2576 if highres else 1568,
            max_image_tokens=4784 if highres else 1568,
            many_images_threshold=20, many_images_edge_px=2000,
            max_image_bytes=7_000_000,  # 10 MB base64-encoded
            max_request_bytes=32_000_000,
            effort_levels=levels,
            adaptive_thinking=bool(prof.get("anthropic_supports_adaptive_thinking", False)),
            max_output_tokens=128_000 if modern else 64_000,
            context_tokens=1_000_000 if big_ctx else 200_000,
            server_fallbacks=m.startswith(_SERVER_FALLBACK_PREFIXES),
            files_api=True,
        )
    if p == "openai":
        reasoning = m.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))
        return Capabilities(
            vision=True, video_input=False, strict_tools=True, forced_tool_choice=True,
            max_images=1500, max_image_edge_px=2048, many_images_threshold=None, many_images_edge_px=None,
            max_image_bytes=20_000_000, max_request_bytes=512_000_000,
            effort_levels=("minimal", "low", "medium", "high", "xhigh") if reasoning else (),
            adaptive_thinking=False, max_output_tokens=128_000 if reasoning else 16_384,
            context_tokens=400_000 if reasoning else 128_000,
        )
    if p == "google":
        return Capabilities(
            vision=True, video_input=True, strict_tools=False, forced_tool_choice=True,
            max_images=3600, max_image_edge_px=3072, many_images_threshold=None, many_images_edge_px=None,
            max_image_bytes=7_000_000, max_request_bytes=20_000_000,
            effort_levels=("low", "medium", "high"), adaptive_thinking=True,
            max_output_tokens=65_536, context_tokens=1_000_000,
        )
    # openai_compat: unknown server; conservative defaults the caller can override per spec
    return Capabilities(
        vision=True, video_input=False, strict_tools=False, forced_tool_choice=True,
        max_images=20, max_image_edge_px=2048, many_images_threshold=None, many_images_edge_px=None,
        max_image_bytes=5_000_000, max_request_bytes=20_000_000, effort_levels=(),
        adaptive_thinking=False, max_output_tokens=16_384, context_tokens=128_000,
    )


# ============================================================================================ spec
def normalize_provider(name: str) -> str:
    """Canonical provider name (``claude``→``anthropic``, ``gemini``→``google``, ``openrouter``→``openai_compat``)."""
    key = (name or "").strip().lower()
    if key not in PROVIDER_ALIASES:
        raise ValueError(f"unknown provider {name!r}; use one of {', '.join(PROVIDERS)}")
    return PROVIDER_ALIASES[key]


def normalize_effort(effort: str | None) -> str | None:
    if effort is None:
        return None
    e = str(effort).strip().lower()
    if not e:
        return None
    if e not in _EFFORT_RANK:
        raise ValueError(f"unknown effort {effort!r}; use one of {', '.join(EFFORTS)}")
    return e


class ModelSpec(BaseModel):
    """One model for one role. ``api_key`` lives in memory only (excluded from repr and dumps)."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    model: str = Field(min_length=1)
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    base_url: str | None = None
    capabilities: Capabilities = Field(default_factory=Capabilities)
    effort: str | None = None  # e.g. "max" for the Director
    byok: bool = False  # True when the key came from the creator (never switch billing silently)
    stream: bool | None = Field(default=None, description="Serve every request through the streaming endpoint "
                                                          "(None = provider default: on for Anthropic)")
    server_fallbacks: bool | None = Field(default=None, description="Server-side refusal fallback opt-in "
                                                                    "(None = on where the model supports it)")
    extra_settings: dict[str, Any] = Field(default_factory=dict,
                                           description="Merged last into the pydantic-ai model settings "
                                                       "(extra_body/extra_headers/anthropic_betas are merged)")

    @field_validator("provider", mode="before")
    @classmethod
    def _norm_provider(cls, v: Any) -> Any:
        return normalize_provider(v) if isinstance(v, str) else v

    @field_validator("effort", mode="before")
    @classmethod
    def _norm_effort(cls, v: Any) -> Any:
        return normalize_effort(v) if isinstance(v, str) or v is None else v

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", v):
            raise ValueError(f"base_url must be an http(s) URL, got {v!r}")
        return v

    @model_validator(mode="after")
    def _defaults(self) -> ModelSpec:
        if "capabilities" not in self.model_fields_set:
            self.capabilities = capabilities_for(self.provider, self.model)
        if self.provider == "openai_compat" and not self.base_url:
            raise ValueError("openai_compat needs base_url (e.g. https://openrouter.ai/api/v1)")
        register_secret(self.api_key)
        return self

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def public_dict(self) -> dict[str, Any]:
        """JSON-safe description for traces/reports (the key is shown only as set/unset)."""
        d = self.model_dump(mode="json")
        d["api_key"] = _REDACTED if self.api_key else "<unset>"
        return d

    def with_key(self, api_key: str | None) -> ModelSpec:
        return self.model_copy(update={"api_key": api_key})

    @property
    def official_endpoint(self) -> bool:
        """True when requests go to the provider's own API (not a proxy or gateway)."""
        if self.base_url is None:
            return True
        return self.provider == "anthropic" and self.base_url.startswith(_ANTHROPIC_API_HOSTS)

    def uses_streaming(self) -> bool:
        if self.stream is not None:
            return self.stream
        return self.provider == "anthropic"

    def uses_server_fallbacks(self) -> bool:
        """Server-side refusal fallback (another model may answer): off for a BYOK spec unless explicitly enabled,
        since the creator chose this model and pays for it."""
        if self.provider != "anthropic":
            return False
        if self.server_fallbacks is not None:
            return self.server_fallbacks
        if self.byok:
            return False
        return self.capabilities.server_fallbacks and self.official_endpoint


def _settings(settings: Settings | None) -> Settings:
    return settings if settings is not None else get_settings()


def house_spec(role: Role, *, settings: Settings | None = None) -> ModelSpec:
    """The house model for ``role`` (key from settings; ``None`` if not configured, which
    :func:`build_model` reports as a :class:`MissingKeyError`).

    * director: ``Settings.director_provider/model/effort`` (default ``claude-fable-5-1`` at ``max``);
    * critic: Anthropic ``Settings.critic_model`` at ``high`` (the frame judge; a different family is
      preferred by :mod:`studio.agent.critics` when a key for one exists). Judges answer a rubric with evidence
      they are shown; at ``max`` they spent ~100k thinking tokens per answer for no measured gain;
    * watcher: Gemini (``Settings.watcher_model`` or :data:`DEFAULT_WATCHER_MODEL`) with video input when a
      Google key exists, else the house critic model watching burned-ID frames;
    * helper: the Director's model at ``high``.
    """
    s = _settings(settings)
    if role == "director":
        prov = normalize_provider(s.director_provider)
        return ModelSpec(provider=prov, model=s.director_model, api_key=s.key(_key_name(prov)),
                         effort=s.director_effort or ROLE_EFFORT["director"])
    if role == "critic":
        return ModelSpec(provider="anthropic", model=s.critic_model, api_key=s.key("anthropic"),
                         effort=ROLE_EFFORT["critic"])
    if role == "watcher":
        if s.has_key("google"):
            return ModelSpec(provider="google", model=s.watcher_model or DEFAULT_WATCHER_MODEL,
                             api_key=s.key("google"), effort=ROLE_EFFORT["watcher"])
        return ModelSpec(provider="anthropic", model=s.critic_model, api_key=s.key("anthropic"),
                         effort=ROLE_EFFORT["watcher"])
    if role == "helper":
        prov = normalize_provider(s.director_provider)
        return ModelSpec(provider=prov, model=s.director_model, api_key=s.key(_key_name(prov)),
                         effort=ROLE_EFFORT["helper"])
    raise ValueError(f"unknown role {role!r}")


def director_fallback_spec(*, settings: Settings | None = None) -> ModelSpec:
    """House fallback Director (``Settings.director_fallback_model``, default ``claude-opus-5-5``), used when
    the primary is unavailable to the key (404) or the org lacks the retention Fable needs (400)."""
    s = _settings(settings)
    return ModelSpec(provider="anthropic", model=s.director_fallback_model, api_key=s.key("anthropic"),
                     effort=s.director_effort or ROLE_EFFORT["director"])


def _key_name(provider: str) -> str:
    return {"anthropic": "anthropic", "openai": "openai", "google": "google"}.get(provider, provider)


def spec_from_cli(provider: str, model: str, key_env: str | None, *, settings: Settings | None = None,
                  base_url: str | None = None, effort: str | None = None) -> ModelSpec:
    """BYOK Director spec from CLI flags; the key is read from env var ``key_env`` (process env first,
    then the ``STUDIO_ENV_FILE`` values, in memory). Never logged: errors name the variable, not the value.

    ``key_env=None`` uses the house key for that provider (not BYOK). ``openrouter`` implies its base URL.
    """
    s = _settings(settings)
    raw_provider = (provider or "").strip().lower()
    prov = normalize_provider(raw_provider)
    if prov == "openai_compat" and base_url is None:
        base_url = OPENROUTER_BASE_URL if raw_provider == "openrouter" else (os.environ.get("STUDIO_DIRECTOR_BASE_URL")
                                                                            or None)
        if base_url is None:
            raise ValueError("openai_compat needs a base URL (set STUDIO_DIRECTOR_BASE_URL or pass base_url)")
    key: str | None
    byok = key_env is not None
    if key_env is not None:
        name = key_env.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"invalid environment variable name {key_env!r}")
        key = (os.environ.get(name) or "").strip() or None
        if key is None and s.env_file is not None and s.env_file.is_file():
            from dotenv import dotenv_values

            key = (dotenv_values(s.env_file).get(name) or "").strip() or None
        if key is None and prov != "openai_compat":
            raise MissingKeyError(f"{prov} key not found: environment variable {name} is unset or empty")
    else:
        key = s.key(_key_name(prov)) if prov != "openai_compat" else None
    return ModelSpec(provider=prov, model=model, api_key=key, base_url=base_url,
                     effort=effort or s.director_effort or ROLE_EFFORT["director"], byok=byok)


# ============================================================================================ settings
def _resolve_effort(spec: ModelSpec, role: str | None) -> str | None:
    e = spec.effort or (ROLE_EFFORT.get(role) if role else None)
    return normalize_effort(e)


def _anthropic_effort(effort: str, caps: Capabilities) -> str | None:
    levels = caps.effort_levels
    if not levels:
        return None
    if effort in levels:
        return effort
    if effort == "xhigh" and "max" in levels:
        return "max"  # pydantic-ai's own mapping for models without xhigh
    if effort in ("none", "minimal"):
        return "low"
    # highest supported level not above the request
    ranked = sorted(levels, key=lambda x: _EFFORT_RANK[x])
    below = [x for x in ranked if _EFFORT_RANK[x] <= _EFFORT_RANK[effort]]
    return below[-1] if below else ranked[0]


def _unified_thinking(effort: str | None) -> Any:
    """pydantic-ai unified ``thinking`` value (it snaps to each model's supported levels)."""
    if effort is None:
        return None
    if effort == "none":
        return False
    return "xhigh" if effort == "max" else effort


def model_settings(spec: ModelSpec, role: str | None = None) -> dict[str, Any]:
    """pydantic-ai ``ModelSettings`` for ``spec`` (effort/thinking/caching/timeouts; see module docs)."""
    caps = spec.capabilities
    effort = _resolve_effort(spec, role)
    long_role = role in (None, "director", "critic", "watcher")
    out: dict[str, Any] = {"timeout": LONG_TIMEOUT_S if long_role else 600.0}
    if spec.provider == "anthropic":
        out["max_tokens"] = caps.max_output_tokens
        if effort is not None:
            eff = _anthropic_effort(effort, caps)
            if eff is not None:
                out["anthropic_effort"] = eff
        if caps.adaptive_thinking:
            out["anthropic_thinking"] = {"type": "adaptive", "display": "summarized"}
        elif effort is not None and effort not in ("none", "minimal", "low"):
            budget = {"medium": 10_000, "high": 16_384, "xhigh": 32_768, "max": 48_000}.get(effort, 10_000)
            budget = max(1024, min(budget, caps.max_output_tokens - 8_192))
            out["anthropic_thinking"] = {"type": "enabled", "budget_tokens": budget}
        out["anthropic_cache_instructions"] = CACHE_TTL
        out["anthropic_cache_tool_definitions"] = CACHE_TTL
        out["anthropic_cache"] = CACHE_TTL
        if spec.uses_server_fallbacks():
            out["anthropic_betas"] = [SERVER_FALLBACK_BETA]
            out["extra_body"] = {"fallbacks": "default"}
    elif spec.provider == "openai":
        out["max_tokens"] = caps.max_output_tokens
        if effort is not None and caps.effort_levels:
            out["thinking"] = _unified_thinking(effort)
    elif spec.provider == "google":
        out["max_tokens"] = caps.max_output_tokens
        if effort is not None:
            out["thinking"] = _unified_thinking("high" if effort in ("xhigh", "max") else effort)
    else:  # openai_compat: only send what was explicitly asked for (servers reject unknown params)
        out["max_tokens"] = caps.max_output_tokens
        if spec.effort is not None:
            out["thinking"] = _unified_thinking(spec.effort)
    return _merge_settings(out, spec.extra_settings)


_MERGED_DICT_SETTINGS = ("extra_body", "extra_headers")
_MERGED_LIST_SETTINGS = ("anthropic_betas",)


def _merge_settings(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """``base`` updated with ``extra``; request-body/header dicts and beta lists are merged, not replaced
    (so a caller's ``extra_body`` never silently drops the refusal-fallback opt-in)."""
    out = dict(base)
    for k, v in extra.items():
        if k in _MERGED_DICT_SETTINGS and isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = {**out[k], **v}
        elif k in _MERGED_LIST_SETTINGS and isinstance(v, (list, tuple)) and isinstance(out.get(k), list):
            out[k] = [*out[k], *(x for x in v if x not in out[k])]
        else:
            out[k] = v
    return out


# ============================================================================================ build
def _require_key(spec: ModelSpec) -> str:
    if spec.api_key:
        return spec.api_key
    names = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY"}
    raise MissingKeyError(f"{spec.provider} key not configured for {spec.model} "
                          f"(set {names.get(spec.provider, 'a key')} or STUDIO_ENV_FILE, or pass a BYOK key)")


def _native_model(spec: ModelSpec, role: str | None) -> Model:
    ms: Any = model_settings(spec, role)
    if spec.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        prov = AnthropicProvider(api_key=_require_key(spec), base_url=spec.base_url)
        return AnthropicModel(spec.model, provider=prov, settings=ms)
    if spec.provider == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        prov_o = OpenAIProvider(api_key=_require_key(spec), base_url=spec.base_url)
        return OpenAIResponsesModel(spec.model, provider=prov_o, settings=ms)
    if spec.provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        prov_g = GoogleProvider(api_key=_require_key(spec), base_url=spec.base_url)
        return GoogleModel(spec.model, provider=prov_g, settings=ms)
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # local servers need no key but the SDK requires a non-empty placeholder
    prov_c = OpenAIProvider(api_key=spec.api_key or "api-key-not-set", base_url=spec.base_url)
    return OpenAIChatModel(spec.model, provider=prov_c, settings=ms)


def _fallback_worthy(exc: BaseException) -> bool:
    """Fall back only when the *model* is unusable for this key/org (never on transient errors, which
    the SDK already retries, and never on our own bad requests)."""
    err = map_provider_error(exc)
    if err.kind in ("not_found", "permission"):
        return True
    return err.kind == "bad_request" and "retention" in err.raw_message.lower()


def build_model(spec: ModelSpec, *, role: str | None = None, job: Job | None = None, stage: str | None = None,
                fallback: ModelSpec | None = None) -> Model:
    """A pydantic-ai native model for ``spec`` with its settings baked in.

    ``fallback`` (e.g. :func:`director_fallback_spec`) is tried when the primary model is unavailable
    to the key (404/403) or blocked by the org's retention setting. Anthropic models (``spec.stream``) are
    wrapped in :class:`StreamingRequestModel`. With ``job`` the result is wrapped in :class:`TracedModel`
    so every request is traced (role/stage/provider/model/tokens/latency/tools).
    """
    model: Model = _native_model(spec, role)
    if fallback is not None:
        from pydantic_ai.models.fallback import FallbackModel

        model = FallbackModel(model, _native_model(fallback, role), fallback_on=_fallback_worthy)
    if spec.uses_streaming():
        model = StreamingRequestModel(model)
    if job is not None:
        model = TracedModel(model, job=job, role=role or "model", spec=spec, stage=stage, fallback=fallback)
    return model


def native_model(model: Model) -> Model:
    """The innermost model under :class:`TracedModel` / :class:`StreamingRequestModel` wrappers."""
    while isinstance(model, WrapperModel):
        model = model.wrapped
    return model


def _raise_on_refusal(resp: ModelResponse) -> None:
    """A declined request (``stop_reason: "refusal"``) must never be used, not even its partial output."""
    if resp.finish_reason != "content_filter":
        return
    from pydantic_ai.exceptions import ContentFilterError
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    details = resp.provider_details or {}
    msg = "Content filter triggered."
    if details.get("refusal_category"):
        msg += f" Category: {details['refusal_category']!r}."
    if details.get("refusal"):
        msg += f" Refusal: {details['refusal']!r}"
    if resp.parts:
        msg += " (partial output discarded)"
    try:
        body: str | None = ModelMessagesTypeAdapter.dump_json([resp]).decode()
    except Exception:  # pragma: no cover - serialization never fails for a parsed response
        body = None
    raise ContentFilterError(msg, body=body)


class StreamingRequestModel(WrapperModel):
    """Serves non-streaming ``request`` calls through the wrapped model's ``request_stream``.

    Long max-effort turns stay on a live SSE connection (no silent HTTP request for many minutes) and
    pydantic-ai's streamed parser tolerates the server-side ``fallback`` content block. A refusal that
    survives the server-side fallback chain raises ``ContentFilterError`` (partial output discarded).
    ``request_stream`` passes straight through.
    """

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        async with self.wrapped.request_stream(messages, model_settings, model_request_parameters) as stream:
            async for _event in stream:
                pass
            resp = stream.get()
        _raise_on_refusal(resp)
        return resp


# ============================================================================================ tracing
def response_summary(resp: ModelResponse) -> dict[str, Any]:
    """Compact, secret-free view of one model response for the trace."""
    from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart

    tools: list[str] = []
    text: list[str] = []
    thinking_chars = 0
    for part in resp.parts:
        if isinstance(part, ToolCallPart):
            tools.append(part.tool_name)
        elif isinstance(part, TextPart):
            text.append(part.content)
        elif isinstance(part, ThinkingPart):
            thinking_chars += len(part.content or "")
    u = resp.usage
    joined = " ".join(t.strip() for t in text if t.strip())
    details = resp.provider_details or {}
    out = {
        "model_resolved": resp.model_name, "provider_name": resp.provider_name,
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cache_read_tokens": u.cache_read_tokens, "cache_write_tokens": u.cache_write_tokens,
        "tool_calls": tools, "finish_reason": resp.finish_reason, "thinking_chars": thinking_chars,
        "text_chars": len(joined), "text_preview": joined[:400],
        "response_id": resp.provider_response_id,
    }
    if details.get("refusal_category") or details.get("refusal"):
        out["refusal_category"] = details.get("refusal_category")
    if details.get("input_transformations"):
        out["input_transformations"] = details["input_transformations"]  # e.g. thinking blocks dropped
    return out


def _request_facts(messages: list[ModelMessage], params: ModelRequestParameters) -> dict[str, Any]:
    from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart, UploadedFile, UserPromptPart

    images = uploaded = 0
    for m in messages:
        if not isinstance(m, ModelRequest):
            continue
        for p in m.parts:
            content: Any = p.content if isinstance(p, (UserPromptPart, ToolReturnPart)) else None
            items = content if isinstance(content, (list, tuple)) else [content]
            for c in items:
                if isinstance(c, BinaryContent) and c.is_image:
                    images += 1
                elif isinstance(c, UploadedFile) and c.media_type.startswith("image/"):
                    images += 1
                    uploaded += 1
    return {"messages": len(messages), "images_in_context": images, "uploaded_images_in_context": uploaded,
            "tools_offered": len(params.function_tools) if params is not None else 0}


class TracedModel(WrapperModel):
    """Wraps a model; appends one ``model_call`` record per request to the job's ``trace.jsonl``.

    Fields: role, stage, provider, model (requested and resolved, so fallbacks are visible), BYOK flag,
    effort, input/output/cache tokens, latency_ms, tool_calls (names), finish_reason, thinking/text sizes,
    a 400-char text preview and, on failure, the mapped error kind/message. Everything is redacted.
    ``stage`` may be reassigned between Director stages.
    """

    def __init__(self, wrapped: Model, *, job: Job, role: str, spec: ModelSpec | None = None,
                 stage: str | None = None, fallback: ModelSpec | None = None):
        super().__init__(wrapped)
        self.job = job
        self.role = role
        self.spec = spec
        self.stage = stage
        self.fallback_spec = fallback
        self.calls = 0

    def _base_fields(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "role": self.role, "stage": self.stage,
            "provider": spec.provider if spec else self.wrapped.system,
            "model": spec.model if spec else self.wrapped.model_name,
            "byok": bool(spec.byok) if spec else False,
            "effort": spec.effort if spec else None,
            "fallback_model": self.fallback_spec.model if self.fallback_spec else None,
            "server_fallbacks": bool(spec.uses_server_fallbacks()) if spec else False,
            "sse": isinstance(self.wrapped, StreamingRequestModel),
        }

    def _trace_ok(self, resp: ModelResponse, t0: float, facts: dict[str, Any], streamed: bool) -> None:
        self.calls += 1
        trace_event(self.job, "model_call", **self._base_fields(), **facts, **response_summary(resp),
                    latency_ms=round((time.perf_counter() - t0) * 1000), streamed=streamed, ok=True)

    def _trace_err(self, exc: BaseException, t0: float, facts: dict[str, Any], streamed: bool) -> None:
        self.calls += 1
        err = map_provider_error(exc, self.spec)
        trace_event(self.job, "model_call", **self._base_fields(), **facts,
                    latency_ms=round((time.perf_counter() - t0) * 1000), streamed=streamed, ok=False,
                    error_kind=err.kind, error_status=err.status, error=err.message)

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        t0 = time.perf_counter()
        facts = _request_facts(messages, model_request_parameters)
        try:
            resp = await self.wrapped.request(messages, model_settings, model_request_parameters)
        except BaseException as e:
            if not isinstance(e, asyncio.CancelledError):
                self._trace_err(e, t0, facts, False)
            raise
        self._trace_ok(resp, t0, facts, False)
        return resp

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                             model_request_parameters: ModelRequestParameters,
                             run_context: Any = None) -> AsyncGenerator[StreamedResponse]:
        t0 = time.perf_counter()
        facts = _request_facts(messages, model_request_parameters)
        stream = None
        try:
            async with self.wrapped.request_stream(messages, model_settings, model_request_parameters,
                                                   run_context) as stream:
                yield stream
        except BaseException as e:
            if not isinstance(e, (asyncio.CancelledError, GeneratorExit)):
                self._trace_err(e, t0, facts, True)
            raise
        if stream is not None:
            with contextlib.suppress(Exception):
                self._trace_ok(stream.get(), t0, facts, True)


# ============================================================================================ errors
ErrorKind = Literal["auth", "billing", "permission", "not_found", "too_large", "rate_limit", "quota", "overloaded",
                    "server", "bad_request", "timeout", "connection", "missing_key", "refusal", "unknown"]


class ProviderError(RuntimeError):
    """A provider failure with a clear, redacted message. ``retryable`` marks transient failures."""

    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None, provider: str | None = None,
                 model: str | None = None, retryable: bool = False, raw_message: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.provider = provider
        self.model = model
        self.retryable = retryable
        self.raw_message = raw_message

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "status": self.status, "provider": self.provider, "model": self.model,
                "retryable": self.retryable, "message": self.message}


def _body_message(body: Any) -> tuple[str, str]:
    """``(error_type, message)`` from Anthropic/OpenAI/Google error bodies."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        with contextlib.suppress(Exception):
            import json

            body = json.loads(body)
    if isinstance(body, Mapping):
        err = body.get("error", body)
        if isinstance(err, Mapping):
            etype = str(err.get("type") or err.get("status") or err.get("code") or "")
            msg = str(err.get("message") or "")
            details = err.get("details")
            if isinstance(details, list):
                for d in details:
                    if isinstance(d, Mapping) and d.get("reason"):
                        etype = f"{etype}:{d.get('reason')}" if etype else str(d.get("reason"))
            return etype, msg
        if isinstance(err, str):
            return "", err
    if isinstance(body, str):
        return "", body[:500]
    return "", ""


def _extract(exc: BaseException) -> tuple[int | None, str, str, bool]:
    """``(status, error_type, message, is_connection)`` from any supported exception type."""
    # pydantic-ai
    try:
        from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

        if isinstance(exc, ModelHTTPError):
            et, msg = _body_message(exc.body)
            return exc.status_code, et, msg or str(exc), False
        if isinstance(exc, ModelAPIError):
            cause = exc.__cause__
            if cause is not None and cause is not exc:
                st, et, msg, conn = _extract(cause)
                if st is not None or conn:
                    return st, et, msg, conn
            return None, "", exc.message, "connect" in exc.message.lower() or "timed out" in exc.message.lower()
    except Exception:  # pragma: no cover
        pass
    # official SDKs (anthropic / openai share the Stainless error shape)
    for mod in ("anthropic", "openai"):
        try:
            sdk = __import__(mod)
        except Exception:  # pragma: no cover
            continue
        if isinstance(exc, sdk.APIStatusError):
            et, msg = _body_message(getattr(exc, "body", None))
            et = et or str(getattr(exc, "type", "") or "")
            return exc.status_code, et, msg or getattr(exc, "message", str(exc)), False
        if isinstance(exc, sdk.APITimeoutError):
            return 408, "timeout", str(exc), True
        if isinstance(exc, sdk.APIConnectionError):
            return None, "", str(exc) or "connection error", True
    # httpx / httpx2
    for mod in ("httpx", "httpx2"):
        try:
            hx = __import__(mod)
        except Exception:  # pragma: no cover
            continue
        if isinstance(exc, hx.HTTPStatusError):
            body: Any = None
            with contextlib.suppress(Exception):
                body = exc.response.json()
            if body is None:
                with contextlib.suppress(Exception):
                    body = exc.response.text
            et, msg = _body_message(body)
            return exc.response.status_code, et, msg or str(exc), False
        if isinstance(exc, hx.TimeoutException):
            return 408, "timeout", str(exc) or "timed out", True
        if isinstance(exc, hx.RequestError):
            return None, "", str(exc) or type(exc).__name__, True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return None, "", str(exc) or type(exc).__name__, True
    return None, "", f"{type(exc).__name__}: {exc}", False


_PNAME = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google Gemini",
          "openai_compat": "The OpenAI-compatible endpoint"}


def _refusal_info(exc: BaseException) -> tuple[str | None, str] | None:
    """``(category, message)`` when ``exc`` is a safety refusal (pydantic-ai ``ContentFilterError``)."""
    try:
        from pydantic_ai.exceptions import ContentFilterError
    except Exception:  # pragma: no cover
        return None
    if not isinstance(exc, ContentFilterError):
        return None
    cat: str | None = None
    with contextlib.suppress(Exception):
        import json

        body = json.loads(exc.body or "null")
        first = body[0] if isinstance(body, list) and body else body
        details = (first or {}).get("provider_details") or {}
        cat = details.get("refusal_category") or details.get("block_reason") or details.get("finish_reason")
    if cat is None:
        m = re.search(r"Category: '([^']+)'", exc.message)
        cat = m.group(1) if m else None
    return (str(cat) if cat else None), exc.message


def map_provider_error(exc: BaseException, spec: ModelSpec | None = None) -> ProviderError:
    """Classify any provider/SDK/HTTP error into a :class:`ProviderError` with a clear, redacted message."""
    if isinstance(exc, ProviderError):
        return exc
    provider = spec.provider if spec else None
    model = spec.model if spec else None
    pname = _PNAME.get(provider or "", "The provider")
    mname = model or "the model"
    if isinstance(exc, MissingKeyError):
        return ProviderError("missing_key", mask_key_like(str(exc)), provider=provider, model=model,
                             raw_message=str(exc))
    refusal = _refusal_info(exc)
    if refusal is not None:
        cat, raw_msg = refusal
        chain = " and every server-side fallback model" if spec is not None and spec.uses_server_fallbacks() else ""
        text = (f"{mname}{chain} declined the request (safety classifier"
                + (f", category {cat!r}" if cat else "") + "). Any partial output was discarded. Rephrase the "
                "request (remove wording that reads as out-of-scope), or run this step on another model.")
        return ProviderError("refusal", mask_key_like(redact(text, known_secrets())), status=200, provider=provider,
                             model=model, raw_message=mask_key_like(redact(raw_msg, known_secrets())))
    status, etype, raw, is_conn = _extract(exc)
    raw = mask_key_like(redact(raw, known_secrets()))
    low = f"{etype} {raw}".lower()
    detail = f" Details: {raw}" if raw else ""

    def pe(kind: ErrorKind, msg: str, retryable: bool = False) -> ProviderError:
        return ProviderError(kind, mask_key_like(msg), status=status, provider=provider, model=model,
                             retryable=retryable, raw_message=raw)

    if is_conn and status in (None, 408):
        if status == 408 or "timeout" in low or "timed out" in low:
            return pe("timeout", f"{pname} did not answer in time (timeout). The request can be retried.", True)
        where = f" at {spec.base_url}" if spec and spec.base_url else ""
        return pe("connection", f"Could not reach {pname}{where} (network error).{detail}", True)
    if status is None:
        return pe("unknown", f"{pname} request failed.{detail}")
    if status == 400 and ("api_key_invalid" in low or "api key not valid" in low or "invalid api key" in low):
        return pe("auth", f"{pname} rejected the API key (400: the key is not valid). Check that the full key "
                          "was pasted and that it is active.")
    if status == 401:
        return pe("auth", f"{pname} rejected the API key (401 authentication error). Check that the key is "
                          f"complete, active and belongs to this provider.{detail}")
    if status == 402 or "billing" in etype.lower():
        return pe("billing", f"{pname} reports a billing problem on the account (402). Add a payment method or "
                             f"credits, then retry.{detail}")
    if status == 403:
        return pe("permission", f"The key is valid but is not allowed to use {mname} (403 permission error)."
                                f"{detail}")
    if status == 404:
        return pe("not_found", f"{mname} was not found or is not available to this key (404). Check the model "
                               f"ID and the account's model access.{detail}")
    if status in (408, 504):
        return pe("timeout", f"{pname} timed out ({status}). The request can be retried.{detail}", True)
    if status == 413 or "request_too_large" in low or "too large" in low:
        return pe("too_large", f"The request is too large for {pname} ({status}): send fewer or smaller frames "
                               f"per call.{detail}")
    if status == 429:
        if "insufficient_quota" in low or "quota" in low or "credit" in low:
            return pe("quota", f"The {pname} account is out of quota or credits (429). Top up or raise the "
                               f"limit, then retry.{detail}")
        return pe("rate_limit", f"{pname} rate limit reached (429); retry after a short wait.{detail}", True)
    if status == 529 or "overloaded" in low:
        return pe("overloaded", f"{pname} is temporarily overloaded ({status}); retry shortly.{detail}", True)
    if status >= 500:
        return pe("server", f"{pname} had a server error ({status}); retry shortly.{detail}", True)
    if status in (400, 422):
        hint = ""
        if "retention" in low:
            hint = (" This model requires 30-day data retention on the organization (zero-data-retention orgs "
                    "cannot use it); use the fallback Director model or change the org's retention setting.")
        elif "tool_choice" in low:
            hint = " This model does not support forced tool choice; use tool_choice=auto."
        elif "thinking" in low:
            hint = " This model only supports adaptive thinking; control depth with effort instead."
        elif "image" in low:
            hint = " An image is too large or there are too many images; send smaller contact sheets."
        return pe("bad_request", f"{pname} rejected the request ({status}).{hint}{detail}")
    return pe("unknown", f"{pname} returned HTTP {status}.{detail}")


# ============================================================================================ key validation
@dataclass
class KeyCheck:
    """Result of :func:`validate_key` (never contains the key)."""

    ok: bool
    provider: str
    model: str
    model_available: bool | None = None
    models_listed: int = 0
    kind: str = "ok"
    message: str = ""
    status: int | None = None
    sample_models: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "provider": self.provider, "model": self.model,
                "model_available": self.model_available, "models_listed": self.models_listed, "kind": self.kind,
                "message": self.message, "status": self.status}


def _ids_match(model: str, ids: Iterable[str]) -> bool:
    m = model.strip()
    return any(i == m or i.split("/")[-1] == m for i in ids)


async def _list_anthropic(spec: ModelSpec, http_client: Any, timeout: float) -> tuple[list[str], bool | None]:
    from anthropic import AsyncAnthropic, NotFoundError

    kwargs: dict[str, Any] = {"api_key": _require_key(spec), "max_retries": 1, "timeout": timeout}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if http_client is not None:
        kwargs["http_client"] = http_client
    client = AsyncAnthropic(**kwargs)
    ids: list[str] = []
    async for m in client.models.list(limit=100):
        ids.append(m.id)
        if len(ids) >= 1000:
            break
    if _ids_match(spec.model, ids):
        return ids, True
    try:  # aliases resolve through retrieve
        await client.models.retrieve(spec.model)
        return ids, True
    except NotFoundError:
        return ids, False


async def _list_openai(spec: ModelSpec, http_client: Any, timeout: float) -> tuple[list[str], bool | None]:
    from openai import AsyncOpenAI

    key = spec.api_key if spec.provider == "openai_compat" else _require_key(spec)
    kwargs: dict[str, Any] = {"api_key": key or "api-key-not-set", "max_retries": 1, "timeout": timeout}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if http_client is not None:
        kwargs["http_client"] = http_client
    client = AsyncOpenAI(**kwargs)
    ids: list[str] = []
    page = await client.models.list()
    async for m in page:
        ids.append(m.id)
        if len(ids) >= 5000:
            break
    return ids, _ids_match(spec.model, ids) if ids else None


async def _list_google(spec: ModelSpec, http_client: Any, timeout: float) -> tuple[list[str], bool | None]:
    import httpx

    base = (spec.base_url or GOOGLE_API_BASE).rstrip("/")
    headers = {"x-goog-api-key": _require_key(spec)}  # header, never the URL (URLs get logged)
    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout)
    try:
        ids: list[str] = []
        token: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            r = await client.get(f"{base}/v1beta/models", headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            ids.extend(str(m.get("name", "")).removeprefix("models/") for m in data.get("models", []))
            token = data.get("nextPageToken")
            if not token:
                break
        if _ids_match(spec.model, ids):
            return ids, True
        r = await client.get(f"{base}/v1beta/models/{spec.model}", headers=headers)
        if r.status_code == 404:
            return ids, False
        r.raise_for_status()
        return ids, True
    finally:
        if own:
            await client.aclose()


async def validate_key(spec: ModelSpec, *, http_client: Any = None, timeout: float = 30.0) -> KeyCheck:
    """Check that ``spec``'s key works (a list-models call) and whether ``spec.model`` is available.

    Never raises for provider problems: failures come back as ``ok=False`` with a clear ``message``
    (see :func:`map_provider_error`). ``http_client`` is for tests (an ``httpx2.AsyncClient`` for the
    Anthropic/OpenAI SDKs, an ``httpx.AsyncClient`` for Google).
    """
    try:
        if spec.provider == "anthropic":
            ids, avail = await _list_anthropic(spec, http_client, timeout)
        elif spec.provider == "google":
            ids, avail = await _list_google(spec, http_client, timeout)
        else:
            ids, avail = await _list_openai(spec, http_client, timeout)
    except BaseException as e:  # classify everything, including SDK errors
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        err = map_provider_error(e, spec)
        return KeyCheck(ok=False, provider=spec.provider, model=spec.model, kind=err.kind, message=err.message,
                        status=err.status)
    if avail is False:
        return KeyCheck(ok=False, provider=spec.provider, model=spec.model, model_available=False,
                        models_listed=len(ids), kind="not_found", sample_models=ids[:20],
                        message=f"The key works, but {spec.model} is not available to it "
                                f"({len(ids)} models listed).")
    return KeyCheck(ok=True, provider=spec.provider, model=spec.model, model_available=avail,
                    models_listed=len(ids), sample_models=ids[:20],
                    message=f"Key OK: {len(ids)} models listed; {spec.model} "
                            + ("available." if avail else "could not be confirmed from the listing."))


def validate_key_sync(spec: ModelSpec, **kwargs: Any) -> KeyCheck:
    """Blocking wrapper around :func:`validate_key` (for the CLI)."""
    return asyncio.run(validate_key(spec, **kwargs))


# ============================================================================================ image uploads
class ImageUploader:
    """Uploads images to the Anthropic Files API so tool results can reference them by ``file_id``.

    A Director conversation re-sends its whole history on every turn; inline base64 sheets therefore
    accumulate against the 32 MB request cap, which would cap how much the Director can look at. Uploaded
    images cost the same visual tokens but only a few bytes per turn. Files expire after ``expires_in_s``
    (default :data:`FILE_EXPIRY_S`) so frames of the creator's footage do not linger in the workspace;
    :meth:`delete_all` removes them earlier. ``upload`` is synchronous (the frame tools run in worker
    threads) and thread-safe. Every upload is traced (file id, bytes, media type; never the key).
    """

    provider_name = "anthropic"

    def __init__(self, spec: ModelSpec, *, job: Job | None = None, expires_in_s: int = FILE_EXPIRY_S,
                 http_client: Any = None, timeout: float = 120.0):
        if spec.provider != "anthropic":
            raise ValueError("ImageUploader supports the Anthropic Files API only")
        if not 3600 <= int(expires_in_s) <= 7_776_000:
            raise ValueError("expires_in_s must be between 3600 (1 h) and 7776000 (90 days)")
        self.spec = spec
        self.job = job
        self.expires_in_s = int(expires_in_s)
        self._http_client = http_client
        self._timeout = timeout
        self._client: Any = None
        self._lock = threading.Lock()
        self.file_ids: list[str] = []
        self.bytes_uploaded = 0

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                from anthropic import Anthropic

                kwargs: dict[str, Any] = {"api_key": _require_key(self.spec), "max_retries": 2,
                                          "timeout": self._timeout}
                if self.spec.base_url:
                    kwargs["base_url"] = self.spec.base_url
                if self._http_client is not None:
                    kwargs["http_client"] = self._http_client
                self._client = Anthropic(**kwargs)
            return self._client

    def upload(self, data: bytes, media_type: str, filename: str) -> str:
        """Upload one image; returns its ``file_id``. Raises :class:`ProviderError` on failure."""
        if not media_type.startswith("image/"):
            raise ValueError(f"not an image media type: {media_type!r}")
        name = re.sub(r'[<>:"|?*\\/\x00-\x1f]', "_", filename or "image")[:200] or "image"
        t0 = time.perf_counter()
        try:
            meta = self._get_client().files.upload(file=(name, data, media_type),
                                                   expires_in_seconds=self.expires_in_s)
        except Exception as e:
            err = map_provider_error(e, self.spec)
            trace_event(self.job, "file_upload", ok=False, provider="anthropic", media_type=media_type,
                        bytes=len(data), error_kind=err.kind, error=err.message,
                        latency_ms=round((time.perf_counter() - t0) * 1000))
            raise err from None
        fid = str(meta.id)
        with self._lock:
            self.file_ids.append(fid)
            self.bytes_uploaded += len(data)
        trace_event(self.job, "file_upload", ok=True, provider="anthropic", file_id=fid, media_type=media_type,
                    bytes=len(data), filename=name, expires_in_s=self.expires_in_s,
                    latency_ms=round((time.perf_counter() - t0) * 1000))
        return fid

    def delete_all(self) -> int:
        """Delete every file this uploader created (they would expire anyway); returns how many went."""
        with self._lock:
            ids, self.file_ids = list(self.file_ids), []
        done = 0
        for fid in ids:
            try:
                self._get_client().files.delete(fid)
                done += 1
            except Exception as e:
                err = map_provider_error(e, self.spec)
                trace_event(self.job, "file_delete", ok=False, file_id=fid, error_kind=err.kind)
        if ids:
            trace_event(self.job, "file_delete", ok=True, deleted=done, requested=len(ids))
        return done


def make_image_uploader(spec: ModelSpec, *, job: Job | None = None, expires_in_s: int = FILE_EXPIRY_S,
                        http_client: Any = None) -> ImageUploader | None:
    """An :class:`ImageUploader` for ``spec`` when its provider has a files API we use (Anthropic, official
    endpoint, key present); ``None`` otherwise (the frame tools then send images inline)."""
    if spec.provider != "anthropic" or not spec.capabilities.files_api or not spec.api_key:
        return None
    if not spec.official_endpoint:
        return None
    return ImageUploader(spec, job=job, expires_in_s=expires_in_s, http_client=http_client)
