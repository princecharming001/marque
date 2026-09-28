from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from studio import cli, pipeline
from studio.jobs import Job


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "clip.mov"
    p.write_bytes(b"\x00")
    return p


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, tuple, dict]]:
    rec: list[tuple[str, tuple, dict]] = []
    jd = tmp_path / "job"

    def fake(name: str, result: Any):
        def f(*args: Any, **kwargs: Any) -> Any:
            rec.append((name, args, kwargs))
            return result
        return f

    monkeypatch.setattr(pipeline, "edit", fake("edit", pipeline.EditResult(
        job_dir=jd, finals={"tiktok": jd / "renders/r3/final_tiktok.mp4"}, doc_version=7)))
    monkeypatch.setattr(pipeline, "chat", fake("chat", pipeline.ChatResult(job_dir=jd, summary="tightened",
                                                                           doc_version=8)))
    monkeypatch.setattr(pipeline, "index", fake("index", pipeline.IndexResult(
        job_dir=jd, index_path=jd / "index/x.json", words=41, duration_s=16.5)))
    monkeypatch.setattr(pipeline, "render", fake("render", pipeline.RenderResult(job_dir=jd,
                                                                                 render_dir=jd / "renders/r1")))
    monkeypatch.setattr(pipeline, "report", fake("report", jd / "report.md"))
    monkeypatch.setattr(pipeline, "qa", fake("qa", pipeline.QaResult(job_dir=jd, passed=True, results=[{"n": 1}])))
    return rec


def test_help_and_version(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for cmd in ("edit", "chat", "index", "render", "report", "qa"):
        assert cmd in out
    assert cli.main(["--version"]) == 0
    assert "studio 0.1.0" in capsys.readouterr().out
    assert cli.main([]) == 2
    assert cli.main(["frobnicate"]) == 2


def test_edit_dispatch_with_all_flags(calls, video: Path, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-secret-value-000")
    rc = cli.main(["--work-dir", str(tmp_path / "wd"), "edit", str(video), "--brief", "teach one tip",
                   "--style", "educational", "--platform", "reels", "--director-provider", "openai",
                   "--director-model", "gpt-6-astra", "--director-key-env", "MY_KEY", "--rounds", "4",
                   "--out", str(tmp_path / "out")])
    assert rc == 0
    name, args, kw = calls[0]
    assert name == "edit" and args == (video,)
    assert kw["brief"] == "teach one tip" and kw["style"] == "educational" and kw["platform"] == "reels"
    assert kw["director_provider"] == "openai" and kw["director_model"] == "gpt-6-astra"
    assert kw["director_key_env"] == "MY_KEY"  # the NAME is passed, never the value
    assert kw["rounds"] == 4 and kw["out"] == tmp_path / "out"
    assert kw["settings"].work_dir == tmp_path / "wd"
    out = capsys.readouterr().out
    assert "job_dir:" in out and "final_tiktok.mp4" in out and "doc_version: 7" in out
    assert "sk-secret-value-000" not in out


def test_edit_defaults(calls, video: Path):
    assert cli.main(["edit", str(video)]) == 0
    kw = calls[0][2]
    assert kw["platform"] == "tiktok" and kw["brief"] is None and kw["rounds"] is None
    assert kw["director_provider"] is None


def test_other_commands_dispatch(calls, video: Path, tmp_path: Path, capsys):
    jd = tmp_path / "some_job"
    jd.mkdir()
    assert cli.main(["chat", str(jd), "make captions bigger"]) == 0
    assert cli.main(["index", str(video), "--asr-provider", "assemblyai"]) == 0
    assert cli.main(["render", str(jd), "--version", "3", "--preview"]) == 0
    assert cli.main(["render", str(jd)]) == 0
    assert cli.main(["report", str(jd)]) == 0
    assert cli.main(["qa", str(jd), "--render", "2"]) == 0
    names = [c[0] for c in calls]
    assert names == ["chat", "index", "render", "render", "report", "qa"]
    assert calls[0][1] == (jd, "make captions bigger")
    assert calls[1][2]["asr_provider"] == "assemblyai"
    assert calls[2][2]["version"] == 3 and calls[2][2]["preview"] is True
    assert calls[3][2]["version"] is None and calls[3][2]["preview"] is False
    assert calls[5][2]["render"] == 2
    out = capsys.readouterr().out
    assert "summary: tightened" in out and "report.md" in out and "passed: True" in out


def test_json_output(calls, video: Path, capsys):
    assert cli.main(["--json", "index", str(video)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["command"] == "index" and data["result"]["words"] == 41
    assert data["result"]["index_path"].endswith("index/x.json")


@pytest.mark.parametrize("argv,needle", [
    (["edit", "/nope/missing.mov"], "video not found"),
    (["render", "/nope/job"], "job directory not found"),
])
def test_input_checks(calls, argv, needle, capsys):
    assert cli.main(argv) == 2
    assert needle in capsys.readouterr().err
    assert calls == []


def test_director_flag_consistency(calls, video: Path, capsys):
    assert cli.main(["edit", str(video), "--director-model", "m"]) == 2
    assert "--director-provider" in capsys.readouterr().err
    assert cli.main(["edit", str(video), "--director-key-env", "X"]) == 2


@pytest.mark.parametrize("argv", [
    ["edit", "v.mov", "--platform", "youtube"],
    ["edit", "v.mov", "--rounds", "0"],
    ["edit", "v.mov", "--director-provider", "mistral"],
    ["render", "j", "--version", "-1"],
    ["qa", "j", "--render", "0"],
    ["chat", "j"],
])
def test_usage_errors(argv):
    assert cli.main(argv) == 2


def test_not_implemented_stage_exit_code(video: Path, capsys, monkeypatch):
    def nope(*a, **k):
        raise NotImplementedError("studio.pipeline.edit")

    monkeypatch.setattr(pipeline, "edit", nope)
    assert cli.main(["edit", str(video)]) == 3
    assert "not implemented yet" in capsys.readouterr().err


def test_errors_are_redacted(video: Path, capsys, monkeypatch):
    secret = "sk-ant-cli-secret-123456"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    from studio.config import reset_settings

    reset_settings()

    def boom(*a, **k):
        raise RuntimeError(f"401 for key {secret}")

    monkeypatch.setattr(pipeline, "index", boom)
    assert cli.main(["index", str(video)]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: 401 for key <redacted>" in err and secret not in err


def test_real_stubs_raise_not_implemented_until_built(video: Path, work_dir: Path):
    """With no builder output yet the placeholder pipeline reports exit code 3 (or runs, once built)."""
    rc = cli.main(["--work-dir", str(work_dir), "report", str(Job.create("r", work_dir=work_dir).root)])
    assert rc in (0, 1, 3)


def test_console_script_entry_point():
    exe = Path(sys.executable).parent / "studio"
    if not exe.exists():
        pytest.skip("console script not installed")
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "studio 0.1.0"


def test_module_invocation():
    out = subprocess.run([sys.executable, "-m", "studio.cli", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "edit" in out.stdout
