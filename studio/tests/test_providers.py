"""Tests for studio.agent.providers (specs, BYOK, settings mapping, tracing, errors, key validation).

Keyless and offline: HTTP is served by mock transports; models are pydantic-ai FunctionModel/TestModel.
The ``real`` tests make one small list-models call and one tiny model call with the house key.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from studio.agent import providers as pv
from studio.agent.providers import Capabilities, ModelSpec
from studio.config import MissingKeyError, Settings
from studio.jobs import Job

FAKE_ANT = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE-0123456789"
FAKE_OAI = "sk-proj-FAKEFAKEFAKEFAKEFAKEFAKE0123456789"
FAKE_GOO = "AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings.load(env={"STUDIO_WORK_DIR": str(tmp_path), **env})


# ============================================================================================ specs
def test_capabilities_table_anthropic() -> None:
    fable = ModelSpec(provider="anthropic", model="claude-fable-5-1")
    c = fable.capabilities
    assert c.forced_tool_choice is False and c.adaptive_thinking and c.vision and not c.video_input
    assert c.effort_levels == ("low", "medium", "high", "xhigh", "max")
    assert c.max_images == 600 and c.max_image_edge_px == 2576 and c.image_edge_limit() == 2000
    assert c.max_output_tokens == 128_000 and c.context_tokens == 1_000_000
    opus = ModelSpec(provider="anthropic", model="claude-opus-5-5").capabilities
    assert opus.forced_tool_choice is False and "max" in opus.effort_levels
    o46 = pv.capabilities_for("anthropic", "claude-opus-4-6")
    assert o46.forced_tool_choice and "xhigh" not in o46.effort_levels and o46.max_image_edge_px == 1568
    haiku = pv.capabilities_for("anthropic", "claude-haiku-4-5")
    assert haiku.max_images == 100 and haiku.effort_levels == () and not haiku.adaptive_thinking


def test_capabilities_table_other_providers() -> None:
    g = pv.capabilities_for("google", "gemini-3.8-flash")
    assert g.video_input and g.max_images == 3600
    o = pv.capabilities_for("openai", "gpt-5.5")
    assert o.max_images == 1500 and o.effort_levels and o.strict_tools
    c = ModelSpec(provider="openai_compat", model="llama", base_url="http://localhost:8000/v1").capabilities
    assert not c.strict_tools and c.max_images == 20


def test_explicit_capabilities_win() -> None:
    caps = Capabilities(vision=False, max_images=3)
    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", capabilities=caps)
    assert spec.capabilities.vision is False and spec.capabilities.max_images == 3


def test_provider_aliases_and_validation() -> None:
    assert ModelSpec(provider="claude", model="x").provider == "anthropic"  # type: ignore[arg-type]
    assert ModelSpec(provider="gemini", model="x").provider == "google"  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ModelSpec(provider="mystery", model="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="base_url"):
        ModelSpec(provider="openai_compat", model="x")
    with pytest.raises(ValueError, match="http"):
        ModelSpec(provider="openai_compat", model="x", base_url="ftp://nope")
    assert ModelSpec(provider="anthropic", model="x", effort="MAX").effort == "max"
    with pytest.raises(ValueError):
        ModelSpec(provider="anthropic", model="x", effort="ludicrous")


def test_key_never_serialized_and_registered_for_redaction() -> None:
    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT)
    assert FAKE_ANT not in repr(spec)
    assert FAKE_ANT not in spec.model_dump_json()
    assert "api_key" not in spec.model_dump()
    assert spec.public_dict()["api_key"] == "<redacted>"
    assert FAKE_ANT not in json.dumps(pv.redact_secrets({"msg": f"key={FAKE_ANT} and more"}))
    assert FAKE_ANT in pv.known_secrets()


def test_mask_key_like_patterns() -> None:
    text = (f"a {FAKE_OAI} b {FAKE_GOO} c Bearer abcdefghijklmnopqrstuvwxyz0123 d x-api-key: ABCDEFGHIJKLMNOP123 "
            "e sk-or-v1-0123456789abcdef0123456789")
    out = pv.mask_key_like(text)
    for secret in (FAKE_OAI, FAKE_GOO, "abcdefghijklmnopqrstuvwxyz0123", "ABCDEFGHIJKLMNOP123",
                   "0123456789abcdef0123456789"):
        assert secret not in out
    assert out.count("<redacted>") >= 5 and "Bearer <redacted>" in out


# ============================================================================================ house / BYOK
def test_house_specs(tmp_path: Path) -> None:
    s = _settings(tmp_path, ANTHROPIC_API_KEY=FAKE_ANT)
    d = pv.house_spec("director", settings=s)
    assert (d.provider, d.model, d.effort) == ("anthropic", "claude-fable-5-1", "max")
    assert d.api_key == FAKE_ANT and not d.byok
    c = pv.house_spec("critic", settings=s)
    assert c.model == "claude-opus-5-5" and c.effort == "high"
    w = pv.house_spec("watcher", settings=s)
    assert w.provider == "anthropic" and not w.capabilities.video_input
    h = pv.house_spec("helper", settings=s)
    assert h.effort == "high"
    fb = pv.director_fallback_spec(settings=s)
    assert fb.model == "claude-opus-5-5" and fb.effort == "max"
    s2 = _settings(tmp_path, ANTHROPIC_API_KEY=FAKE_ANT, GOOGLE_API_KEY=FAKE_GOO,
                   STUDIO_WATCHER_MODEL="gemini-3.1-pro")
    w2 = pv.house_spec("watcher", settings=s2)
    assert w2.provider == "google" and w2.model == "gemini-3.1-pro" and w2.capabilities.video_input
    with pytest.raises(ValueError):
        pv.house_spec("janitor", settings=s)  # type: ignore[arg-type]


def test_spec_from_cli_byok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(tmp_path)
    monkeypatch.setenv("MY_OPENAI_KEY", FAKE_OAI)
    spec = pv.spec_from_cli("gpt", "gpt-5.5", "MY_OPENAI_KEY", settings=s)
    assert spec.provider == "openai" and spec.api_key == FAKE_OAI and spec.byok and spec.effort == "max"
    with pytest.raises(MissingKeyError) as ei:
        pv.spec_from_cli("openai", "gpt-5.5", "NOT_SET_ANYWHERE", settings=s)
    assert "NOT_SET_ANYWHERE" in str(ei.value) and FAKE_OAI not in str(ei.value)
    with pytest.raises(ValueError):
        pv.spec_from_cli("openai", "gpt-5.5", "bad name;rm", settings=s)
    orr = pv.spec_from_cli("openrouter", "anthropic/claude-opus-5-5", "MY_OPENAI_KEY", settings=s)
    assert orr.provider == "openai_compat" and orr.base_url == pv.OPENROUTER_BASE_URL


def test_spec_from_cli_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    envf = tmp_path / "keys.env"
    envf.write_text(f"CREATOR_GEMINI={FAKE_GOO}\n", encoding="utf-8")
    monkeypatch.delenv("CREATOR_GEMINI", raising=False)
    s = Settings.load(env={"STUDIO_ENV_FILE": str(envf)})
    spec = pv.spec_from_cli("gemini", "gemini-3.1-pro", "CREATOR_GEMINI", settings=s, effort="high")
    assert spec.api_key == FAKE_GOO and spec.provider == "google" and spec.effort == "high"
    assert "CREATOR_GEMINI" not in os.environ  # the file is never exported


def test_spec_from_cli_house_key(tmp_path: Path) -> None:
    s = _settings(tmp_path, ANTHROPIC_API_KEY=FAKE_ANT)
    spec = pv.spec_from_cli("anthropic", "claude-opus-5-5", None, settings=s)
    assert spec.api_key == FAKE_ANT and not spec.byok


# ============================================================================================ settings mapping
def test_model_settings_anthropic_fable_max() -> None:
    ms = pv.model_settings(ModelSpec(provider="anthropic", model="claude-fable-5-1", effort="max"), "director")
    assert ms["anthropic_effort"] == "max"
    assert ms["anthropic_thinking"] == {"type": "adaptive", "display": "summarized"}
    assert ms["max_tokens"] == 128_000 and ms["timeout"] == pv.LONG_TIMEOUT_S
    assert ms["anthropic_cache_instructions"] == "1h" and ms["anthropic_cache"] == "1h"
    assert "temperature" not in ms and "thinking" not in ms


def test_model_settings_effort_defaults_and_downmapping() -> None:
    # Opus 5.5 defaults to medium on the API: the role default must be sent explicitly
    ms = pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-5-5"), "critic")
    assert ms["anthropic_effort"] == "high"
    ms46 = pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-4-6", effort="xhigh"), "director")
    assert ms46["anthropic_effort"] == "max"
    o45 = pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-4-5-20251101", effort="max"))
    assert o45["anthropic_effort"] == "high"
    haiku = pv.model_settings(ModelSpec(provider="anthropic", model="claude-haiku-4-5", effort="high"))
    assert "anthropic_effort" not in haiku
    assert haiku["anthropic_thinking"]["type"] == "enabled"
    assert haiku["anthropic_thinking"]["budget_tokens"] < haiku["max_tokens"]
    extra = pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-5-5", effort="low",
                                        extra_settings={"max_tokens": 2048}))
    assert extra["max_tokens"] == 2048 and extra["anthropic_effort"] == "low"


def test_capabilities_images_fallbacks_files() -> None:
    fable = pv.capabilities_for("anthropic", "claude-fable-5-1")
    assert fable.max_image_tokens == 4784 and fable.server_fallbacks and fable.files_api
    assert pv.capabilities_for("anthropic", "claude-opus-5-5").server_fallbacks
    assert pv.capabilities_for("anthropic", "claude-opus-5").server_fallbacks
    o48 = pv.capabilities_for("anthropic", "claude-opus-4-8")
    assert not o48.server_fallbacks and o48.max_image_tokens == 4784
    assert pv.capabilities_for("anthropic", "claude-opus-4-6").max_image_tokens == 1568
    g = pv.capabilities_for("google", "gemini-3.8-flash")
    assert g.max_image_tokens is None and not g.server_fallbacks and not g.files_api
    assert pv.visual_tokens(28, 28) == 1 and pv.visual_tokens(29, 28) == 2 and pv.visual_tokens(1920, 1080) == 2691


def test_model_settings_server_fallbacks_and_merging() -> None:
    ms = pv.model_settings(ModelSpec(provider="anthropic", model="claude-fable-5-1"), "director")
    assert ms["anthropic_betas"] == [pv.SERVER_FALLBACK_BETA] and ms["extra_body"] == {"fallbacks": "default"}
    assert "extra_body" not in pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-4-8"))
    off = ModelSpec(provider="anthropic", model="claude-opus-5-5", server_fallbacks=False)
    assert "anthropic_betas" not in pv.model_settings(off) and not off.uses_server_fallbacks()
    gateway = ModelSpec(provider="anthropic", model="claude-opus-5-5", base_url="https://gw.example.com/anthropic")
    assert not gateway.uses_server_fallbacks() and "extra_body" not in pv.model_settings(gateway)
    official = ModelSpec(provider="anthropic", model="claude-opus-5-5", base_url="https://api.anthropic.com")
    assert official.uses_server_fallbacks()
    # caller extras are merged into (not over) the opt-in
    merged = pv.model_settings(ModelSpec(provider="anthropic", model="claude-opus-5-5", extra_settings={
        "extra_body": {"metadata": {"user_id": "job-1"}}, "anthropic_betas": ["context-management-2025-06-27"],
        "max_tokens": 4096}))
    assert merged["extra_body"] == {"fallbacks": "default", "metadata": {"user_id": "job-1"}}
    assert merged["anthropic_betas"] == [pv.SERVER_FALLBACK_BETA, "context-management-2025-06-27"]
    assert merged["max_tokens"] == 4096
    # streaming defaults: Anthropic on, others off, explicit wins
    assert ModelSpec(provider="anthropic", model="x").uses_streaming()
    assert not ModelSpec(provider="openai", model="gpt-5.5").uses_streaming()
    assert ModelSpec(provider="google", model="g", stream=True).uses_streaming()


def test_model_settings_other_providers() -> None:
    o = pv.model_settings(ModelSpec(provider="openai", model="gpt-5.5", effort="max"), "director")
    assert o["thinking"] == "xhigh" and "anthropic_effort" not in o
    g = pv.model_settings(ModelSpec(provider="google", model="gemini-3.1-pro", effort="max"), "director")
    assert g["thinking"] == "high"
    c = pv.model_settings(ModelSpec(provider="openai_compat", model="m", base_url="http://localhost:1/v1"),
                          "director")
    assert "thinking" not in c  # never send params a compat server did not ask for
    c2 = pv.model_settings(ModelSpec(provider="openai_compat", model="m", base_url="http://localhost:1/v1",
                                     effort="medium"))
    assert c2["thinking"] == "medium"


# ============================================================================================ build
def test_build_model_native_classes() -> None:
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

    a = pv.build_model(ModelSpec(provider="anthropic", model="claude-fable-5-1", api_key=FAKE_ANT, effort="max"),
                       role="director")
    assert isinstance(a, pv.StreamingRequestModel)  # Anthropic requests always go through the SSE endpoint
    na = pv.native_model(a)
    assert isinstance(na, AnthropicModel) and a.model_name == "claude-fable-5-1" == na.model_name
    assert a.settings is not None and a.settings["anthropic_effort"] == "max"  # type: ignore[typeddict-item]
    plain = pv.build_model(ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT, stream=False))
    assert isinstance(plain, AnthropicModel)
    o = pv.build_model(ModelSpec(provider="openai", model="gpt-5.5", api_key=FAKE_OAI))
    assert isinstance(o, OpenAIResponsesModel)
    g = pv.build_model(ModelSpec(provider="google", model="gemini-3.1-pro", api_key=FAKE_GOO))
    assert isinstance(g, GoogleModel)
    c = pv.build_model(ModelSpec(provider="openai_compat", model="llama-4", base_url="http://localhost:9/v1"))
    assert isinstance(c, OpenAIChatModel) and "localhost:9" in c.base_url
    fb = pv.build_model(ModelSpec(provider="anthropic", model="claude-fable-5-1", api_key=FAKE_ANT),
                        fallback=ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT))
    assert isinstance(fb, pv.StreamingRequestModel) and isinstance(fb.wrapped, FallbackModel)
    assert len(fb.wrapped.models) == 2


def test_build_model_requires_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANT)  # an env key must never be picked up silently
    with pytest.raises(MissingKeyError, match="anthropic key not configured"):
        pv.build_model(ModelSpec(provider="anthropic", model="claude-opus-5-5"))


def test_fallback_trigger_classification() -> None:
    assert pv._fallback_worthy(ModelHTTPError(404, "claude-fable-5-1", {"error": {"type": "not_found_error"}}))
    assert pv._fallback_worthy(ModelHTTPError(400, "claude-fable-5-1", {"error": {
        "type": "invalid_request_error",
        "message": "In order to access this model, your organization must have data retention enabled."}}))
    assert not pv._fallback_worthy(ModelHTTPError(400, "m", {"error": {"message": "bad tool schema"}}))
    assert not pv._fallback_worthy(ModelHTTPError(429, "m", {"error": {"type": "rate_limit_error"}}))


# ============================================================================================ tracing
def _job(tmp_path: Path) -> Job:
    return Job.create("trace-job", work_dir=tmp_path)


def test_traced_model_records_calls(tmp_path: Path) -> None:
    job = _job(tmp_path)
    secret = "sk-ant-api03-TRACESECRET-0123456789abcdef"
    pv.register_secret(secret)
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"q": "x"})],
                                 usage=RequestUsage(input_tokens=11, output_tokens=5))
        return ModelResponse(parts=[TextPart(f"done, leaked {secret}")],
                             usage=RequestUsage(input_tokens=20, output_tokens=7, cache_read_tokens=3))

    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", effort="max", api_key=FAKE_ANT)
    model = pv.TracedModel(FunctionModel(fn), job=job, role="director", spec=spec, stage="story")
    agent = Agent(model)

    @agent.tool_plain
    def lookup(q: str) -> str:
        return "answer"

    out = agent.run_sync("hi")
    assert "done" in out.output
    recs = [r for r in job.read_trace() if r["event"] == "model_call"]
    assert len(recs) == 2 and model.calls == 2
    r0, r1 = recs
    assert r0["role"] == "director" and r0["stage"] == "story" and r0["provider"] == "anthropic"
    assert r0["model"] == "claude-opus-5-5" and r0["effort"] == "max" and r0["ok"] is True
    assert r0["tool_calls"] == ["lookup"] and r0["input_tokens"] == 11 and r0["output_tokens"] == 5
    assert r1["cache_read_tokens"] == 3 and r1["tools_offered"] == 1 and isinstance(r1["latency_ms"], int)
    raw = job.trace_path.read_text()
    assert secret not in raw and FAKE_ANT not in raw
    assert "<redacted>" in r1["text_preview"]


def test_traced_model_records_errors(tmp_path: Path) -> None:
    job = _job(tmp_path)

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(401, "claude-opus-5-5", {"error": {"type": "authentication_error",
                                                                "message": f"invalid x-api-key {FAKE_ANT}"}})

    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5")
    agent = Agent(pv.TracedModel(FunctionModel(boom), job=job, role="critic", spec=spec))
    with pytest.raises(ModelHTTPError):
        agent.run_sync("hi")
    rec = [r for r in job.read_trace() if r["event"] == "model_call"][-1]
    assert rec["ok"] is False and rec["error_kind"] == "auth" and rec["error_status"] == 401
    assert FAKE_ANT not in job.trace_path.read_text()


def test_traced_model_streaming(tmp_path: Path) -> None:
    job = _job(tmp_path)

    async def stream_fn(messages: list[ModelMessage], info: AgentInfo):  # type: ignore[no-untyped-def]
        yield "hello "
        yield "world"

    model = pv.TracedModel(FunctionModel(stream_function=stream_fn), job=job, role="helper")

    async def go() -> str:
        async with Agent(model).run_stream("hi") as run:
            return await run.get_output()

    assert asyncio.run(go()) == "hello world"
    rec = [r for r in job.read_trace() if r["event"] == "model_call"][-1]
    assert rec["streamed"] is True and rec["ok"] is True and rec["text_preview"] == "hello world"


def test_build_model_with_job_is_traced(tmp_path: Path) -> None:
    job = _job(tmp_path)
    m = pv.build_model(ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT), role="critic",
                       job=job, stage="review")
    assert isinstance(m, pv.TracedModel) and m.role == "critic" and m.stage == "review"
    assert m.model_name == "claude-opus-5-5" and isinstance(m.wrapped, pv.StreamingRequestModel)


# ============================================================================================ streaming + refusals
def test_streaming_request_model_serves_non_streaming_runs(tmp_path: Path) -> None:
    """A plain ``run_sync`` must travel through ``request_stream`` (the SSE endpoint)."""
    job = _job(tmp_path)
    used = {"stream": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:  # pragma: no cover - must not run
        raise AssertionError("non-streaming request used")

    async def sfn(messages: list[ModelMessage], info: AgentInfo):  # type: ignore[no-untyped-def]
        used["stream"] += 1
        if used["stream"] == 1:
            from pydantic_ai.models.function import DeltaToolCall

            yield {0: DeltaToolCall(name="lookup", json_args='{"q": "x"}', tool_call_id="c1")}
            return
        yield "all "
        yield "done"

    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT)
    model = pv.TracedModel(pv.StreamingRequestModel(FunctionModel(fn, stream_function=sfn)), job=job,
                           role="director", spec=spec)
    agent = Agent(model)

    @agent.tool_plain
    def lookup(q: str) -> str:
        return "answer"

    assert agent.run_sync("hi").output == "all done" and used["stream"] == 2
    recs = [r for r in job.read_trace() if r["event"] == "model_call"]
    assert len(recs) == 2 and recs[0]["tool_calls"] == ["lookup"] and recs[1]["text_preview"] == "all done"
    assert all(r["sse"] and r["server_fallbacks"] for r in recs)


class _RefusingModel(FunctionModel):
    """Streams nothing and ends with ``stop_reason: refusal`` after partial output (a mid-stream decline)."""

    def __init__(self, category: str | None = "bio") -> None:
        super().__init__(lambda m, i: ModelResponse(parts=[TextPart("unused")]))
        self.category = category

    @asynccontextmanager
    async def request_stream(self, messages: Any, model_settings: Any, model_request_parameters: Any,
                             run_context: Any = None):  # type: ignore[no-untyped-def]
        cat = self.category

        class _S:
            def __aiter__(self) -> _S:
                return self

            async def __anext__(self) -> Any:
                raise StopAsyncIteration

            def get(self) -> ModelResponse:
                details = {"refusal_category": cat} if cat else {}
                return ModelResponse(parts=[ToolCallPart("cut_ops", {"ops": []}), TextPart("partial")],
                                     finish_reason="content_filter", provider_details=details)

        yield _S()


def test_refusal_is_raised_traced_and_mapped(tmp_path: Path) -> None:
    from pydantic_ai.exceptions import ContentFilterError

    job = _job(tmp_path)
    spec = ModelSpec(provider="anthropic", model="claude-fable-5-1", api_key=FAKE_ANT)
    agent = Agent(pv.TracedModel(pv.StreamingRequestModel(_RefusingModel("bio")), job=job, role="director",
                                 spec=spec))
    with pytest.raises(ContentFilterError) as ei:
        agent.run_sync("cut the video")  # the partial tool call must never execute
    err = pv.map_provider_error(ei.value, spec)
    assert err.kind == "refusal" and not err.retryable and "'bio'" in err.message
    assert "every server-side fallback model" in err.message and "partial output was discarded" in err.message
    rec = [r for r in job.read_trace() if r["event"] == "model_call"][-1]
    assert rec["ok"] is False and rec["error_kind"] == "refusal"
    # the graph's own ContentFilterError (empty refusal) maps the same way, category from the body
    body = json.dumps([{"parts": [], "provider_details": {"refusal_category": "cyber"}}])
    e2 = pv.map_provider_error(ContentFilterError("Content filter triggered.", body=body))
    assert e2.kind == "refusal" and "'cyber'" in e2.message
    assert pv.map_provider_error(ContentFilterError("Content filter triggered.")).kind == "refusal"


def test_refusal_summary_fields() -> None:
    resp = ModelResponse(parts=[TextPart("x")], finish_reason="content_filter",
                         provider_details={"refusal_category": "cyber", "refusal": "declined"})
    s = pv.response_summary(resp)
    assert s["refusal_category"] == "cyber" and s["finish_reason"] == "content_filter"


# ============================================================================================ image uploads
def _files_handler(seen: list[httpx2.Request], *, status: int = 200) -> Any:
    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {"type": "authentication_error",
                                                                              "message": "invalid x-api-key"}})
        if req.method == "POST":
            n = len([r for r in seen if r.method == "POST"])
            return httpx2.Response(200, json={"id": f"file_{n:03d}", "type": "file", "filename": "s.png",
                                              "mime_type": "image/png", "size_bytes": 4,
                                              "created_at": "2026-09-27T00:00:00Z", "downloadable": False,
                                              "expires_at": "2026-09-29T00:00:00Z"})
        return httpx2.Response(200, json={"id": req.url.path.rsplit("/", 1)[-1], "type": "file_deleted"})
    return handler


def test_image_uploader_uploads_with_expiry_and_traces(tmp_path: Path) -> None:
    job = _job(tmp_path)
    seen: list[httpx2.Request] = []
    spec = ModelSpec(provider="anthropic", model="claude-fable-5-1", api_key=FAKE_ANT)
    up = pv.make_image_uploader(spec, job=job,
                                http_client=httpx2.Client(transport=httpx2.MockTransport(_files_handler(seen))))
    assert isinstance(up, pv.ImageUploader) and up.provider_name == "anthropic"
    fid = up.upload(b"\x89PNG fake", "image/png", 'sheet:"1"/x.png')
    assert fid == "file_001" and up.file_ids == ["file_001"] and up.bytes_uploaded == 9
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/files" and req.headers["x-api-key"] == FAKE_ANT
    body = req.content
    assert b'name="expires_in_seconds"\r\n\r\n172800' in body and b'filename="sheet__1__x.png"' in body
    assert "anthropic-beta" not in req.headers  # the Files API is GA
    assert up.delete_all() == 1 and seen[-1].method == "DELETE" and seen[-1].url.path == "/v1/files/file_001"
    recs = [r for r in job.read_trace() if r["event"] in ("file_upload", "file_delete")]
    assert recs[0]["ok"] and recs[0]["file_id"] == "file_001" and recs[0]["bytes"] == 9
    assert FAKE_ANT not in job.trace_path.read_text()
    with pytest.raises(ValueError):
        up.upload(b"x", "text/plain", "a.txt")


def test_image_uploader_errors_and_eligibility(tmp_path: Path) -> None:
    job = _job(tmp_path)
    seen: list[httpx2.Request] = []
    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT)
    up = pv.ImageUploader(spec, job=job, http_client=httpx2.Client(
        transport=httpx2.MockTransport(_files_handler(seen, status=401))))
    with pytest.raises(pv.ProviderError) as ei:
        up.upload(b"\x89PNG", "image/png", "s.png")
    assert ei.value.kind == "auth" and FAKE_ANT not in str(ei.value)
    rec = [r for r in job.read_trace() if r["event"] == "file_upload"][-1]
    assert rec["ok"] is False and rec["error_kind"] == "auth"
    assert pv.make_image_uploader(ModelSpec(provider="openai", model="gpt-5.5", api_key=FAKE_OAI)) is None
    assert pv.make_image_uploader(ModelSpec(provider="anthropic", model="claude-opus-5-5")) is None  # no key
    assert pv.make_image_uploader(ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT,
                                            base_url="https://proxy.example.com")) is None
    with pytest.raises(ValueError):
        pv.ImageUploader(spec, expires_in_s=60)


# ============================================================================================ error mapping
def _anth_err(status: int, etype: str, msg: str) -> Exception:
    import anthropic

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": etype, "message": msg}}
    resp = httpx2.Response(status, request=req, json=body)
    cls = {400: anthropic.BadRequestError, 401: anthropic.AuthenticationError, 403: anthropic.PermissionDeniedError,
           404: anthropic.NotFoundError, 413: anthropic.RequestTooLargeError, 429: anthropic.RateLimitError,
           529: anthropic.OverloadedError}.get(status, anthropic.APIStatusError)
    return cls(msg, response=resp, body=body)


@pytest.mark.parametrize(("status", "etype", "msg", "kind", "retryable"), [
    (400, "invalid_request_error", "tool_choice: type any not supported", "bad_request", False),
    (400, "invalid_request_error", "organization must have data retention enabled", "bad_request", False),
    (401, "authentication_error", "invalid x-api-key", "auth", False),
    (402, "billing_error", "credit balance too low", "billing", False),
    (403, "permission_error", "not allowed", "permission", False),
    (404, "not_found_error", "model: claude-x", "not_found", False),
    (413, "request_too_large", "too large", "too_large", False),
    (429, "rate_limit_error", "slow down", "rate_limit", True),
    (500, "api_error", "oops", "server", True),
    (529, "overloaded_error", "overloaded", "overloaded", True),
])
def test_map_anthropic_sdk_errors(status: int, etype: str, msg: str, kind: str, retryable: bool) -> None:
    spec = ModelSpec(provider="anthropic", model="claude-fable-5-1")
    err = pv.map_provider_error(_anth_err(status, etype, msg), spec)
    assert err.kind == kind and err.status == status and err.retryable is retryable
    assert err.provider == "anthropic" and err.model == "claude-fable-5-1" and err.message
    if "retention" in msg:
        assert "30-day data retention" in err.message
    if "tool_choice" in msg:
        assert "tool_choice=auto" in err.message


def test_map_other_error_shapes() -> None:
    spec = ModelSpec(provider="openai", model="gpt-5.5", api_key=FAKE_OAI)
    quota = ModelHTTPError(429, "gpt-5.5", {"error": {"type": "insufficient_quota", "code": "insufficient_quota",
                                                       "message": "You exceeded your current quota"}})
    e = pv.map_provider_error(quota, spec)
    assert e.kind == "quota" and not e.retryable
    goo = ModelHTTPError(400, "gemini", {"error": {"code": 400, "message": "API key not valid. Please pass a valid "
                                                   "API key.", "status": "INVALID_ARGUMENT",
                                                   "details": [{"reason": "API_KEY_INVALID"}]}})
    assert pv.map_provider_error(goo).kind == "auth"
    conn = ModelAPIError("gpt-5.5", "Connection error.")
    assert pv.map_provider_error(conn, spec).kind == "connection"
    hx = httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://x"),
                               response=httpx.Response(403, json={"error": {"message": f"denied {FAKE_GOO}"}}))
    e3 = pv.map_provider_error(hx)
    assert e3.kind == "permission" and FAKE_GOO not in e3.message
    assert pv.map_provider_error(httpx.ConnectTimeout("t")).kind == "timeout"
    assert pv.map_provider_error(MissingKeyError("anthropic key not configured")).kind == "missing_key"
    assert pv.map_provider_error(RuntimeError("weird")).kind == "unknown"
    leak = ModelHTTPError(401, "gpt-5.5", {"error": {"message": f"Incorrect API key provided: {FAKE_OAI}"}})
    assert FAKE_OAI not in pv.map_provider_error(leak, spec).message


# ============================================================================================ key validation
def _anthropic_handler(models: list[str], *, status: int = 200) -> Any:
    seen: dict[str, Any] = {"headers": []}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["headers"].append(dict(request.headers))
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {
                "type": "authentication_error", "message": "invalid x-api-key"}})
        path = request.url.path
        if path.endswith("/v1/models"):
            data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"}
                    for m in models]
            return httpx2.Response(200, json={"data": data, "has_more": False,
                                              "first_id": models[0] if models else None,
                                              "last_id": models[-1] if models else None})
        mid = path.rsplit("/", 1)[-1]
        if mid in models:
            return httpx2.Response(200, json={"type": "model", "id": mid, "display_name": mid,
                                              "created_at": "2026-01-01T00:00:00Z"})
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error",
                                                                     "message": f"model: {mid}"}})

    return handler, seen


def test_validate_key_anthropic_ok() -> None:
    handler, seen = _anthropic_handler(["claude-fable-5-1", "claude-opus-5-5"])
    spec = ModelSpec(provider="anthropic", model="claude-opus-5-5", api_key=FAKE_ANT)
    res = asyncio.run(pv.validate_key(spec, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    assert res.ok and res.model_available and res.models_listed == 2 and res.kind == "ok"
    assert seen["headers"][0]["x-api-key"] == FAKE_ANT  # sent to the provider…
    assert FAKE_ANT not in json.dumps(res.as_dict())  # …never returned


def test_validate_key_anthropic_model_missing_and_bad_key() -> None:
    handler, _ = _anthropic_handler(["claude-opus-5-5"])
    spec = ModelSpec(provider="anthropic", model="claude-fable-5-1", api_key=FAKE_ANT)
    res = asyncio.run(pv.validate_key(spec, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    assert not res.ok and res.model_available is False and res.kind == "not_found" and "claude-fable-5-1" in res.message
    bad, _ = _anthropic_handler([], status=401)
    res2 = asyncio.run(pv.validate_key(spec, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(bad))))
    assert not res2.ok and res2.kind == "auth" and res2.status == 401 and FAKE_ANT not in res2.message


def test_validate_key_missing_key() -> None:
    res = asyncio.run(pv.validate_key(ModelSpec(provider="anthropic", model="claude-opus-5-5")))
    assert not res.ok and res.kind == "missing_key"


def test_validate_key_openai_and_compat() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["authorization"] == f"Bearer {FAKE_OAI}"
        return httpx2.Response(200, json={"object": "list", "data": [
            {"id": "gpt-5.5", "object": "model", "created": 1, "owned_by": "openai"},
            {"id": "anthropic/claude-opus-5-5", "object": "model", "created": 1, "owned_by": "x"}]})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    ok = asyncio.run(pv.validate_key(ModelSpec(provider="openai", model="gpt-5.5", api_key=FAKE_OAI),
                                     http_client=client))
    assert ok.ok and ok.model_available
    compat = ModelSpec(provider="openai_compat", model="anthropic/claude-opus-5-5", api_key=FAKE_OAI,
                       base_url="https://openrouter.ai/api/v1")
    ok2 = asyncio.run(pv.validate_key(compat, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    assert ok2.ok and ok2.models_listed == 2
    missing = ModelSpec(provider="openai", model="gpt-9", api_key=FAKE_OAI)
    r = asyncio.run(pv.validate_key(missing, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    assert not r.ok and r.kind == "not_found"


def test_validate_key_google_header_auth() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert request.headers["x-goog-api-key"] == FAKE_GOO
        if request.url.path == "/v1beta/models":
            return httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash",
                                                         "supportedGenerationMethods": ["generateContent"]}]})
        return httpx.Response(404, json={"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = asyncio.run(pv.validate_key(ModelSpec(provider="google", model="gemini-3.8-flash", api_key=FAKE_GOO),
                                      http_client=client))
    assert res.ok and res.model_available
    assert all(FAKE_GOO not in u for u in urls)  # never in a URL
    client2 = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res2 = asyncio.run(pv.validate_key(ModelSpec(provider="google", model="gemini-1", api_key=FAKE_GOO),
                                       http_client=client2))
    assert not res2.ok and res2.kind == "not_found"


def test_validate_key_connection_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host", request=request)

    spec = ModelSpec(provider="openai_compat", model="m", base_url="http://10.255.255.1:9/v1")
    res = asyncio.run(pv.validate_key(spec, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    assert not res.ok and res.kind == "connection" and "10.255.255.1" in res.message


# ============================================================================================ real
@pytest.mark.real
def test_real_validate_house_anthropic_key() -> None:
    spec = pv.house_spec("critic")
    res = pv.validate_key_sync(spec)
    assert res.ok, res.message
    assert res.models_listed > 0


@pytest.mark.real
def test_real_director_settings_accepted(tmp_path: Path) -> None:
    """One tiny request with the exact settings the Director sends (effort, adaptive thinking with summarized
    display, 1 h caching, server-side refusal fallbacks, SSE transport) so a 400 from the API shows up here
    rather than mid-edit."""
    job = Job.create("real-provider", work_dir=tmp_path)
    spec = pv.house_spec("critic").model_copy(update={"effort": "low", "extra_settings": {"max_tokens": 2048}})
    assert spec.uses_server_fallbacks() and spec.uses_streaming()
    model = pv.build_model(spec, role="critic", job=job, stage="smoke")
    out = Agent(model, instructions="Answer with one word.").run_sync("Say OK.")
    assert out.output.strip()
    rec = [r for r in job.read_trace() if r["event"] == "model_call"][-1]
    assert rec["ok"] and rec["output_tokens"] > 0 and rec["sse"] and rec["server_fallbacks"]
    assert rec["model_resolved"].startswith(spec.model)
