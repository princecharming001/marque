"""Studio settings: paths, house model ids and provider keys.

Design
------
* No pydantic-settings. Values come from ``os.environ`` and, optionally, from the dotenv file named by
  ``STUDIO_ENV_FILE``. The file is parsed with :func:`dotenv.dotenv_values` and is **never** exported
  into ``os.environ`` (so child processes and logs never see its keys).
* Precedence per name: non-empty process env var  >  value in ``STUDIO_ENV_FILE``  >  built-in default.
* Secrets (API keys) are held on the frozen :class:`Settings` object, excluded from ``repr`` and from
  :meth:`Settings.public_dict`. Use :func:`redact` before logging anything that might contain a key.

Usage::

    from studio.config import get_settings
    s = get_settings()
    s.work_dir               # Path("/Users/home/studio-work") by default
    s.key("anthropic")       # the Anthropic key or None
    s.require_key("elevenlabs")  # raises MissingKeyError (message names the env var, never a value)

Tests build isolated settings with ``Settings.load(env={...})`` or call :func:`reset_settings`.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

__all__ = [
    "DEFAULT_WORK_DIR",
    "DIRECTOR_MODEL",
    "DIRECTOR_FALLBACK_MODEL",
    "CRITIC_MODEL",
    "KEY_ENV_NAMES",
    "MissingKeyError",
    "Settings",
    "get_settings",
    "reset_settings",
    "redact",
    "mask",
    "STUDIO_ROOT",
]

#: Root of the studio project (the directory holding ``pyproject.toml``).
STUDIO_ROOT: Path = Path(__file__).resolve().parent.parent

DEFAULT_WORK_DIR = Path("/Users/home/studio-work")

# House model ids (plan §4 / ARCHITECTURE §9). Overridable by env, see Settings.load.
DIRECTOR_PROVIDER = "anthropic"
DIRECTOR_MODEL = "claude-fable-5-1"
DIRECTOR_FALLBACK_MODEL = "claude-opus-5-5"
DIRECTOR_EFFORT = "max"
CRITIC_MODEL = "claude-opus-5-5"

#: Logical key name -> accepted env var names (first non-empty wins).
KEY_ENV_NAMES: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "elevenlabs": ("ELEVENLABS_API_KEY", "ELEVENLABS_KEY"),
    "assemblyai": ("ASSEMBLYAI_KEY", "ASSEMBLYAI_API_KEY", "ASSEMBLY_KEY"),
    "pexels": ("PEXELS_KEY", "PEXELS_API_KEY"),
    "higgsfield": ("HIGGSFIELD_KEY", "HIGGSFIELD_API_KEY"),
}

_REDACTED = "<redacted>"
_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|authorization|access[_-]?token|refresh[_-]?token"
    r"|(^|[_-])token$|(^|[_-])key$)",
    re.IGNORECASE,
)


class MissingKeyError(RuntimeError):
    """Raised by :meth:`Settings.require_key` when a provider key is not configured."""


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration. Build with :meth:`load`; never construct from untrusted data."""

    work_dir: Path = DEFAULT_WORK_DIR
    models_dir: Path = STUDIO_ROOT / "models"
    skills_dir: Path = STUDIO_ROOT / "skills" / "editing"
    overlay_dir: Path = STUDIO_ROOT / "overlay"
    env_file: Path | None = None
    real: bool = False

    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    rubberband: str = "rubberband"
    #: headless Chrome for Remotion; empty = Remotion's own download (``remotion browser ensure``)
    remotion_browser: str | None = None

    director_provider: str = DIRECTOR_PROVIDER
    director_model: str = DIRECTOR_MODEL
    director_fallback_model: str = DIRECTOR_FALLBACK_MODEL
    director_effort: str = DIRECTOR_EFFORT
    critic_model: str = CRITIC_MODEL
    watcher_model: str | None = None

    # logical name -> secret value; never shown in repr
    _keys: Mapping[str, str] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ construction
    @classmethod
    def load(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: str | os.PathLike[str] | None = None,
    ) -> Settings:
        """Build settings from ``env`` (default ``os.environ``) plus an optional dotenv file.

        ``env_file`` defaults to ``env["STUDIO_ENV_FILE"]``. A missing file is ignored (keys simply
        stay unset). The file's values are read into memory only.
        """
        environ: Mapping[str, str] = os.environ if env is None else env
        file_path = env_file if env_file is not None else (environ.get("STUDIO_ENV_FILE") or None)
        file_vals: dict[str, str] = {}
        resolved_file: Path | None = None
        if file_path:
            resolved_file = Path(file_path).expanduser()
            if resolved_file.is_file():
                file_vals = {k: v for k, v in dotenv_values(resolved_file).items() if v is not None}

        def get(name: str) -> str | None:
            v = environ.get(name)
            if v is not None and v.strip() != "":
                return v.strip()
            v = file_vals.get(name)
            if v is not None and v.strip() != "":
                return v.strip()
            return None

        keys: dict[str, str] = {}
        for logical, names in KEY_ENV_NAMES.items():
            for n in names:
                val = get(n)
                if val:
                    keys[logical] = val
                    break

        def path(name: str, default: Path) -> Path:
            v = get(name)
            return Path(v).expanduser() if v else default

        return cls(
            work_dir=path("STUDIO_WORK_DIR", DEFAULT_WORK_DIR),
            models_dir=path("STUDIO_MODELS_DIR", STUDIO_ROOT / "models"),
            skills_dir=path("STUDIO_SKILLS_DIR", STUDIO_ROOT / "skills" / "editing"),
            overlay_dir=path("STUDIO_OVERLAY_DIR", STUDIO_ROOT / "overlay"),
            env_file=resolved_file,
            real=_truthy(get("STUDIO_REAL")),
            ffmpeg=get("STUDIO_FFMPEG") or "ffmpeg",
            ffprobe=get("STUDIO_FFPROBE") or "ffprobe",
            rubberband=get("STUDIO_RUBBERBAND") or "rubberband",
            remotion_browser=get("STUDIO_REMOTION_BROWSER") or None,
            director_provider=get("STUDIO_DIRECTOR_PROVIDER") or DIRECTOR_PROVIDER,
            director_model=get("STUDIO_DIRECTOR_MODEL") or DIRECTOR_MODEL,
            director_fallback_model=get("STUDIO_DIRECTOR_FALLBACK_MODEL") or DIRECTOR_FALLBACK_MODEL,
            director_effort=get("STUDIO_DIRECTOR_EFFORT") or DIRECTOR_EFFORT,
            critic_model=get("STUDIO_CRITIC_MODEL") or CRITIC_MODEL,
            watcher_model=get("STUDIO_WATCHER_MODEL"),
            _keys=keys,
        )

    def with_overrides(self, **changes: Any) -> Settings:
        """Return a copy with some public fields replaced (e.g. ``work_dir=tmp_path``)."""
        if "_keys" in changes:
            raise ValueError("use with_keys() to change secrets")
        return replace(self, **changes)

    def with_keys(self, **keys: str | None) -> Settings:
        """Return a copy with in-memory keys added/removed (BYOK, tests). ``None`` removes a key."""
        merged = dict(self._keys)
        for k, v in keys.items():
            if v:
                merged[k] = v
            else:
                merged.pop(k, None)
        return replace(self, _keys=merged)

    # ------------------------------------------------------------------ keys
    def key(self, name: str) -> str | None:
        """Secret for a logical provider name (``"anthropic"``, ``"elevenlabs"``, …) or None."""
        return self._keys.get(name)

    def has_key(self, name: str) -> bool:
        return bool(self._keys.get(name))

    def require_key(self, name: str) -> str:
        """Return the key or raise :class:`MissingKeyError` naming the env var(s) to set."""
        v = self._keys.get(name)
        if not v:
            names = " or ".join(KEY_ENV_NAMES.get(name, (name,)))
            raise MissingKeyError(f"{name} key not configured (set {names} or STUDIO_ENV_FILE)")
        return v

    def secret_values(self) -> list[str]:
        """All configured secret values (for :func:`redact`). Never log the result."""
        return [v for v in self._keys.values() if v]

    @property
    def available_keys(self) -> list[str]:
        """Logical names of configured keys (names only; safe to log)."""
        return sorted(k for k, v in self._keys.items() if v)

    # ------------------------------------------------------------------ safe views
    def public_dict(self) -> dict[str, Any]:
        """JSON-safe settings without secrets (key *names* that are configured are listed)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            v = getattr(self, f.name)
            out[f.name] = str(v) if isinstance(v, Path) else v
        out["available_keys"] = self.available_keys
        return out

    def __repr__(self) -> str:
        items = ", ".join(f"{k}={v!r}" for k, v in self.public_dict().items())
        return f"Settings({items})"


_lock = threading.Lock()
_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings, loaded once from the environment."""
    global _settings
    with _lock:
        if _settings is None:
            _settings = Settings.load()
        return _settings


def reset_settings(settings: Settings | None = None) -> None:
    """Drop the cached settings (next :func:`get_settings` reloads) or install ``settings``."""
    global _settings
    with _lock:
        _settings = settings


def mask(value: str | None) -> str:
    """Mask a secret for display: ``"<redacted>"`` if set, ``"<unset>"`` otherwise. Reveals nothing."""
    return _REDACTED if value else "<unset>"


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name))


def redact(obj: Any, secrets: Iterable[str] | None = None) -> Any:
    """Return a copy of ``obj`` that is safe to log.

    * strings: every occurrence of a known secret value is replaced by ``"<redacted>"``;
    * mappings: values under secret-looking keys (``*api_key*``, ``*token*``, ``*secret*``,
      ``authorization``, ``*_KEY`` …) are replaced wholesale; other values are redacted recursively;
    * lists/tuples/sets: redacted element-wise; other objects are returned unchanged.

    ``secrets`` defaults to the values configured in :func:`get_settings`. Secrets shorter than 6
    characters are ignored for substring replacement (too collision-prone) but still masked by key name.
    """
    if secrets is None:
        secrets = get_settings().secret_values()
    vals = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)
    return _redact(obj, vals)


def _redact(obj: Any, vals: list[str]) -> Any:
    if isinstance(obj, str):
        out = obj
        for v in vals:
            if v in out:
                out = out.replace(v, _REDACTED)
        return out
    if isinstance(obj, Mapping):
        red: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and isinstance(v, (str, bytes)) and v and _is_secret_name(k):
                red[k] = _REDACTED
            else:
                red[k] = _redact(v, vals)
        return red
    if isinstance(obj, list):
        return [_redact(v, vals) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v, vals) for v in obj)
    if isinstance(obj, (set, frozenset)):
        return type(obj)(_redact(v, vals) for v in obj)
    return obj
