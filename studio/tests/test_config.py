from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from studio import config
from studio.config import (
    CRITIC_MODEL,
    DEFAULT_WORK_DIR,
    DIRECTOR_FALLBACK_MODEL,
    DIRECTOR_MODEL,
    MissingKeyError,
    Settings,
    get_settings,
    mask,
    redact,
    reset_settings,
)

FAKE_ANTHROPIC = "sk-ant-test-0123456789abcdef"
FAKE_ELEVEN = "el-test-abcdef0123456789"


def test_defaults_from_empty_env():
    s = Settings.load(env={})
    assert s.work_dir == DEFAULT_WORK_DIR == Path("/Users/home/studio-work")
    assert s.director_model == DIRECTOR_MODEL == "claude-fable-5-1"
    assert s.director_fallback_model == DIRECTOR_FALLBACK_MODEL == "claude-opus-5-5"
    assert s.critic_model == CRITIC_MODEL == "claude-opus-5-5"
    assert s.director_provider == "anthropic"
    assert s.director_effort == "max"
    assert s.real is False
    assert s.env_file is None
    assert s.available_keys == []
    assert s.key("anthropic") is None
    assert s.models_dir == config.STUDIO_ROOT / "models"
    assert s.skills_dir == config.STUDIO_ROOT / "skills" / "editing"


def test_env_overrides_and_real_flag(tmp_path: Path):
    s = Settings.load(env={
        "STUDIO_WORK_DIR": str(tmp_path / "w"),
        "STUDIO_REAL": "1",
        "STUDIO_DIRECTOR_MODEL": "claude-opus-5-5",
        "STUDIO_CRITIC_MODEL": "gpt-x",
        "STUDIO_FFMPEG": "/opt/ffmpeg",
    })
    assert s.work_dir == tmp_path / "w"
    assert s.real is True
    assert s.director_model == "claude-opus-5-5"
    assert s.critic_model == "gpt-x"
    assert s.ffmpeg == "/opt/ffmpeg"
    for v in ("true", "yes", "on", "TRUE"):
        assert Settings.load(env={"STUDIO_REAL": v}).real
    for v in ("0", "", "no", "false"):
        assert not Settings.load(env={"STUDIO_REAL": v}).real


def test_env_file_is_read_but_not_exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    f = tmp_path / "keys.env"
    f.write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nELEVENLABS_API_KEY='{FAKE_ELEVEN}'\nPEXELS_KEY=\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings.load(env={"STUDIO_ENV_FILE": str(f)})
    assert s.env_file == f
    assert s.key("anthropic") == FAKE_ANTHROPIC
    assert s.key("elevenlabs") == FAKE_ELEVEN
    assert s.key("pexels") is None  # empty value = unset
    assert s.available_keys == ["anthropic", "elevenlabs"]
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ.get("ELEVENLABS_API_KEY") != FAKE_ELEVEN


def test_process_env_wins_over_file(tmp_path: Path):
    f = tmp_path / "k.env"
    f.write_text("ANTHROPIC_API_KEY=from-file-123456\nASSEMBLYAI_KEY=aai-file-123456\n")
    s = Settings.load(env={"STUDIO_ENV_FILE": str(f), "ANTHROPIC_API_KEY": "from-env-123456",
                           "ASSEMBLYAI_KEY": "  "})
    assert s.key("anthropic") == "from-env-123456"
    assert s.key("assemblyai") == "aai-file-123456"  # blank env value falls through to the file


def test_explicit_env_file_argument_and_missing_file(tmp_path: Path):
    f = tmp_path / "x.env"
    f.write_text("PEXELS_KEY=px-123456789\n")
    assert Settings.load(env={}, env_file=f).key("pexels") == "px-123456789"
    s = Settings.load(env={"STUDIO_ENV_FILE": str(tmp_path / "missing.env")})
    assert s.available_keys == []


def test_key_aliases():
    s = Settings.load(env={"ASSEMBLY_KEY": "aai-alias-12345", "GEMINI_API_KEY": "gem-12345678"})
    assert s.key("assemblyai") == "aai-alias-12345"
    assert s.key("google") == "gem-12345678"


def test_require_key_message_names_env_var_not_value():
    s = Settings.load(env={})
    with pytest.raises(MissingKeyError) as e:
        s.require_key("elevenlabs")
    assert "ELEVENLABS_API_KEY" in str(e.value)
    s2 = Settings.load(env={"ELEVENLABS_API_KEY": FAKE_ELEVEN})
    assert s2.require_key("elevenlabs") == FAKE_ELEVEN


def test_repr_and_public_dict_hide_secrets():
    s = Settings.load(env={"ANTHROPIC_API_KEY": FAKE_ANTHROPIC})
    assert FAKE_ANTHROPIC not in repr(s)
    assert FAKE_ANTHROPIC not in str(s)
    pub = s.public_dict()
    assert FAKE_ANTHROPIC not in str(pub)
    assert pub["available_keys"] == ["anthropic"]
    assert pub["director_model"] == DIRECTOR_MODEL
    assert "_keys" not in pub


def test_with_keys_and_overrides(tmp_path: Path):
    s = Settings.load(env={})
    s2 = s.with_keys(openai="sk-openai-123456")
    assert s2.key("openai") == "sk-openai-123456" and s.key("openai") is None
    assert s2.with_keys(openai=None).key("openai") is None
    s3 = s.with_overrides(work_dir=tmp_path)
    assert s3.work_dir == tmp_path and s.work_dir != tmp_path
    with pytest.raises(ValueError):
        s.with_overrides(_keys={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.work_dir = tmp_path  # type: ignore[misc]  # frozen


def test_get_settings_cache_and_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("STUDIO_WORK_DIR", str(tmp_path / "a"))
    reset_settings()
    a = get_settings()
    assert a.work_dir == tmp_path / "a"
    monkeypatch.setenv("STUDIO_WORK_DIR", str(tmp_path / "b"))
    assert get_settings() is a  # cached
    reset_settings()
    assert get_settings().work_dir == tmp_path / "b"
    custom = Settings.load(env={"STUDIO_WORK_DIR": str(tmp_path / "c")})
    reset_settings(custom)
    assert get_settings() is custom


def test_mask():
    assert mask(FAKE_ANTHROPIC) == "<redacted>"
    assert mask(None) == "<unset>"
    assert mask("") == "<unset>"


def test_redact_strings_mappings_and_nesting():
    secrets = [FAKE_ANTHROPIC, FAKE_ELEVEN, "abc"]  # too-short secrets are not substring-replaced
    msg = f"error calling api with key {FAKE_ANTHROPIC}; abc stays"
    assert redact(msg, secrets) == "error calling api with key <redacted>; abc stays"
    obj = {
        "headers": {"x-api-key": "anything", "Authorization": "Bearer zzz", "content-type": "json"},
        "ANTHROPIC_API_KEY": "whatever",
        "PEXELS_KEY": "px",
        "input_tokens": 1234,
        "access_token": "tok",
        "cache_hit": True,
        "list": [f"k={FAKE_ELEVEN}", 3, None],
        "tuple": (FAKE_ANTHROPIC,),
        "nested": {"api_key": None, "sort": "ok"},
    }
    out = redact(obj, secrets)
    assert out["headers"]["x-api-key"] == "<redacted>"
    assert out["headers"]["Authorization"] == "<redacted>"
    assert out["headers"]["content-type"] == "json"
    assert out["ANTHROPIC_API_KEY"] == "<redacted>"
    assert out["PEXELS_KEY"] == "<redacted>"
    assert out["access_token"] == "<redacted>"
    assert out["input_tokens"] == 1234  # counts are not secrets
    assert out["cache_hit"] is True
    assert out["list"] == ["k=<redacted>", 3, None]
    assert out["tuple"] == ("<redacted>",)
    assert out["nested"] == {"api_key": None, "sort": "ok"}
    assert obj["ANTHROPIC_API_KEY"] == "whatever"  # input untouched


def test_redact_defaults_to_configured_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    reset_settings()
    assert redact(f"boom {FAKE_ANTHROPIC}") == "boom <redacted>"
