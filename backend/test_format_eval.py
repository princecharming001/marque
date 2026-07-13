"""Tests for the LOOP F render-formatting harness (eval/format_eval.py).

Keyless: never shells out to ffmpeg/ffprobe/remotion. The subprocess/decode
boundaries (_signalstats_frames, _ffprobe_duration_s) are monkeypatched so the
threshold/parsing logic is exercised directly — the SAME contract-verification
approach used elsewhere in this eval suite (see test_edl_eval.py, test_pitch_check.py).
"""
from __future__ import annotations

import json

from eval import format_eval
from eval.make_format_corpus import build_corpus


def test_composition_id_covers_every_corpus_style():
    """Drift guard: if make_format_corpus ever adds a style, format_eval's render
    driver must know which Remotion composition to render it with, or fixtures
    silently fall into the 'no composition mapped' failure branch."""
    corpus_styles = {fx["edl"]["style"] for fx in build_corpus().values()}
    assert corpus_styles.issubset(format_eval.COMPOSITION_ID.keys())


def test_load_fixtures_returns_full_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(format_eval, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr("eval.make_format_corpus.GOLDEN_DIR", tmp_path / "golden")
    fixtures = format_eval._load_fixtures()
    expected_ids = set(build_corpus().keys())
    assert {fx["id"] for fx in fixtures} == expected_ids
    assert len(fixtures) == 15


def test_load_fixtures_only_filters_to_one(tmp_path, monkeypatch):
    monkeypatch.setattr(format_eval, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr("eval.make_format_corpus.GOLDEN_DIR", tmp_path / "golden")
    fixtures = format_eval._load_fixtures(only="adv-speed-2x-pitch")
    assert [fx["id"] for fx in fixtures] == ["adv-speed-2x-pitch"]


def _fake_ffmpeg_signalstats_stdout(yavg: float, satavg: float, n_frames: int) -> str:
    lines = []
    for i in range(n_frames):
        lines.append(f"frame:{i}    pts:{i * 33} pts_time:{i * 0.033}")
        lines.append(f"lavfi.signalstats.YAVG={yavg}")
        lines.append(f"lavfi.signalstats.SATAVG={satavg}")
    return "\n".join(lines)


def test_signalstats_frames_parses_ffmpeg_metadata_print(monkeypatch):
    class FakeResult:
        stdout = _fake_ffmpeg_signalstats_stdout(120.5, 30.25, 3)
        stderr = ""

    monkeypatch.setattr(format_eval.subprocess, "run", lambda *a, **kw: FakeResult())
    frames = format_eval._signalstats_frames(format_eval.Path("fake.mp4"))
    assert len(frames) == 3
    assert frames[0]["YAVG"] == 120.5
    assert frames[0]["SATAVG"] == 30.25


def test_check_duration_within_tolerance(monkeypatch):
    monkeypatch.setattr(format_eval, "_ffprobe_duration_s", lambda path: 5.0)
    result = format_eval.check_duration(format_eval.Path("fake.mp4"), expected_frames=150)
    assert result["ok"] is True


def test_check_duration_outside_tolerance(monkeypatch):
    monkeypatch.setattr(format_eval, "_ffprobe_duration_s", lambda path: 6.0)
    result = format_eval.check_duration(format_eval.Path("fake.mp4"), expected_frames=150)
    assert result["ok"] is False


def test_check_duration_handles_ffprobe_failure(monkeypatch):
    monkeypatch.setattr(format_eval, "_ffprobe_duration_s", lambda path: None)
    result = format_eval.check_duration(format_eval.Path("fake.mp4"), expected_frames=150)
    assert result["ok"] is False
    assert "reason" in result


def test_check_non_black_passes_when_bright(monkeypatch):
    frames = [{"YAVG": 100.0} for _ in range(30)]
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: frames)
    result = format_eval.check_non_black(format_eval.Path("fake.mp4"))
    assert result["ok"] is True
    assert result["fraction_non_black"] == 1.0


def test_check_non_black_fails_when_mostly_dark(monkeypatch):
    frames = [{"YAVG": 1.0} for _ in range(30)]
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: frames)
    result = format_eval.check_non_black(format_eval.Path("fake.mp4"))
    assert result["ok"] is False


def test_check_non_black_handles_no_frames(monkeypatch):
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: [])
    result = format_eval.check_non_black(format_eval.Path("fake.mp4"))
    assert result["ok"] is False
    assert "reason" in result


def test_check_faceless_mono_desaturated_passes_when_low_saturation(monkeypatch):
    frames = [{"SATAVG": 5.0} for _ in range(30)]
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: frames)
    result = format_eval.check_faceless_mono_desaturated(format_eval.Path("fake.mp4"))
    assert result["ok"] is True


def test_check_faceless_mono_desaturated_fails_when_colorful(monkeypatch):
    frames = [{"SATAVG": 80.0} for _ in range(30)]
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: frames)
    result = format_eval.check_faceless_mono_desaturated(format_eval.Path("fake.mp4"))
    assert result["ok"] is False


def test_score_fixture_only_checks_pitch_on_the_speed_fixture(monkeypatch):
    monkeypatch.setattr(format_eval, "_ffprobe_duration_s", lambda path: 1.5)
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: [{"YAVG": 100.0, "SATAVG": 50.0}])
    monkeypatch.setattr(format_eval, "check_pitch_preserved", lambda path: {"ok": True})

    fx_other = {"id": "talking_head-default", "edl": {"total_frames": 45}}
    checks_other = format_eval._score_fixture(fx_other, format_eval.Path("fake.mp4"))
    assert "pitch_preserved" not in checks_other
    assert "faceless_mono_desaturated" not in checks_other

    fx_speed = {"id": "adv-speed-2x-pitch", "edl": {"total_frames": 45}}
    checks_speed = format_eval._score_fixture(fx_speed, format_eval.Path("fake.mp4"))
    assert "pitch_preserved" in checks_speed


def test_main_exits_2_when_score_requested_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert format_eval.main(["--render", "--score"]) == 2


def test_score_fixture_vision_no_frames_returns_ok_false():
    result = format_eval.score_fixture_vision({"id": "x", "edl": {"style": "talking_head"}}, [], "fake-key")
    assert result["ok"] is False
    assert "reason" in result


def test_score_fixture_vision_parses_a_passing_response(monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"text": json.dumps({"score_0_100": 92, "issues": []})}]}

    monkeypatch.setattr(format_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    result = format_eval.score_fixture_vision(
        {"id": "talking_head-default", "edl": {"style": "talking_head"}}, [b"fake-jpeg-bytes"], "fake-key")
    assert result["ok"] is True
    assert result["score_0_100"] == 92


def test_score_fixture_vision_fails_below_threshold(monkeypatch):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"text": json.dumps({
                "score_0_100": 40, "issues": [{"code": "collision", "frame": 10, "description": "x"}]})}]}

    monkeypatch.setattr(format_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    result = format_eval.score_fixture_vision(
        {"id": "talking_head-default", "edl": {"style": "talking_head"}}, [b"fake-jpeg-bytes"], "fake-key")
    assert result["ok"] is False
    assert result["score_0_100"] == 40


def test_score_fixture_vision_handles_non_200(monkeypatch):
    class FakeResponse:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(format_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    result = format_eval.score_fixture_vision(
        {"id": "x", "edl": {"style": "talking_head"}}, [b"fake-jpeg-bytes"], "fake-key")
    assert result["ok"] is False
    assert "reason" in result


def test_substitute_placeholder_replaces_nested_resolved_url():
    """Regression guard: the placeholder shows up nested inside broll[].resolved_url
    and react_source.resolved_url, not just the top-level sourceUrl — a render driver
    that only patches the top-level key ships a literal '__SOURCE__' string to
    OffthreadVideo/Img for those fixtures and the render fails outright."""
    fx = {
        "sourceUrl": "__SOURCE__",
        "edl": {
            "broll": [{"resolved_url": "__SOURCE__", "cue_text": "x"}],
            "react_source": {"resolved_url": "__SOURCE__", "kind": "video"},
            "style": "talking_head",
        },
    }
    out = format_eval._substitute_placeholder(fx, "http://127.0.0.1:8799/source.mp4")
    assert out["sourceUrl"] == "http://127.0.0.1:8799/source.mp4"
    assert out["edl"]["broll"][0]["resolved_url"] == "http://127.0.0.1:8799/source.mp4"
    assert out["edl"]["react_source"]["resolved_url"] == "http://127.0.0.1:8799/source.mp4"
    assert out["edl"]["broll"][0]["cue_text"] == "x"   # untouched sibling field


def test_score_fixture_only_checks_saturation_on_faceless_mono_fixture(monkeypatch):
    monkeypatch.setattr(format_eval, "_ffprobe_duration_s", lambda path: 1.5)
    monkeypatch.setattr(format_eval, "_signalstats_frames", lambda path: [{"YAVG": 100.0, "SATAVG": 50.0}])

    fx = {"id": "adv-faceless-mono-broll", "edl": {"total_frames": 45}}
    checks = format_eval._score_fixture(fx, format_eval.Path("fake.mp4"))
    assert "faceless_mono_desaturated" in checks
