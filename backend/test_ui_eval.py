"""Tests for the LOOP U app/UI vision-tier harness (eval/ui_eval.py).

Keyless: never calls the real Anthropic API. httpx.post is monkeypatched so
the caching/wiring logic is exercised directly — same approach as
test_format_eval.py/test_pitch_check.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from eval import ui_eval

FLOW_PATH = ui_eval.REPO_ROOT / ".maestro" / "format-audit.yaml"


def test_manifest_loads_and_has_twelve_screens():
    manifest = ui_eval._load_manifest()
    assert len(manifest["screens"]) == 12
    ids = [s["id"] for s in manifest["screens"]]
    assert len(ids) == len(set(ids))   # no duplicate ids
    for s in manifest["screens"]:
        assert s["screenshot"].endswith(".png")


def test_format_audit_flow_asserts_every_manifest_id_and_screenshot():
    # Drift guard: ui-manifest.json and format-audit.yaml are a hand-maintained
    # pair (same convention as edl_eval's fixtures/goldens) — this catches a
    # manifest entry added without a matching flow step, or vice versa, rather
    # than letting the two silently diverge.
    flow_text = FLOW_PATH.read_text()
    manifest = ui_eval._load_manifest()
    missing_ids, missing_text, missing_shots = [], [], []
    for screen in manifest["screens"]:
        for expect_id in screen.get("expect_visible_ids", []):
            if f'"{expect_id}"' not in flow_text:
                missing_ids.append(f"{screen['id']}: {expect_id}")
        for expect_text in screen.get("expect_visible_text", []):
            if f'"{expect_text}"' not in flow_text:
                missing_text.append(f"{screen['id']}: {expect_text}")
        base_shot = Path(screen["screenshot"]).stem   # e.g. "format-audit-home"
        if base_shot not in flow_text:
            missing_shots.append(screen["id"])
    assert not missing_ids, f"format-audit.yaml never asserts these manifest ids: {missing_ids}"
    assert not missing_text, f"format-audit.yaml never asserts this manifest text: {missing_text}"
    assert not missing_shots, f"format-audit.yaml never takes these manifest screenshots: {missing_shots}"


def test_run_skipped_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ui_eval.run(api_key=None) is False


def test_screenshot_path_applies_suffix():
    screen = {"screenshot": "format-audit-home.png"}
    assert ui_eval._screenshot_path(screen, "") == ui_eval.SHOTS_DIR / "format-audit-home.png"
    assert ui_eval._screenshot_path(screen, "xxl") == ui_eval.SHOTS_DIR / "format-audit-home-xxl.png"


def test_score_screenshot_parses_a_passing_response(monkeypatch, tmp_path):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"text": json.dumps({"score_0_100": 88, "issues": []})}]}

    monkeypatch.setattr(ui_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    img = tmp_path / "fake.png"
    img.write_bytes(b"not a real png but bytes are bytes for this test")
    result = ui_eval.score_screenshot({"id": "home", "label": "Home"}, img, "fake-key")
    assert result["ok"] is True
    assert result["score_0_100"] == 88


def test_score_screenshot_fails_below_threshold(monkeypatch, tmp_path):
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"text": json.dumps({
                "score_0_100": 40,
                "issues": [{"code": "layout_broken", "screen_id": "home", "description": "x"}]})}]}

    monkeypatch.setattr(ui_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    img = tmp_path / "fake.png"
    img.write_bytes(b"fake")
    result = ui_eval.score_screenshot({"id": "home", "label": "Home"}, img, "fake-key")
    assert result["ok"] is False
    assert result["score_0_100"] == 40


def test_score_screenshot_handles_non_200(monkeypatch, tmp_path):
    class FakeResponse:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(ui_eval.httpx, "post", lambda *a, **kw: FakeResponse())
    img = tmp_path / "fake.png"
    img.write_bytes(b"fake")
    result = ui_eval.score_screenshot({"id": "home", "label": "Home"}, img, "fake-key")
    assert result["ok"] is False
    assert "reason" in result


def test_run_skips_missing_screenshots_without_crashing(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_eval, "SHOTS_DIR", tmp_path)
    monkeypatch.setattr(ui_eval, "CACHE_PATH", tmp_path / ".ui_eval_cache.json")
    # no screenshots exist in tmp_path at all
    ok = ui_eval.run(suffix="", api_key="fake-key")
    assert ok is True   # nothing found -> nothing failed (vacuously true), no crash


def test_run_uses_cache_for_unchanged_screenshot(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_eval, "SHOTS_DIR", tmp_path)
    cache_path = tmp_path / ".ui_eval_cache.json"
    monkeypatch.setattr(ui_eval, "CACHE_PATH", cache_path)

    manifest = ui_eval._load_manifest()
    first_screen = manifest["screens"][0]
    img = tmp_path / first_screen["screenshot"]
    img.write_bytes(b"stable content")
    digest = ui_eval._sha256(img)
    cache_path.write_text(json.dumps({
        first_screen["screenshot"]: {"sha256": digest, "result": {"ok": True, "score_0_100": 95}},
    }))

    calls = []
    monkeypatch.setattr(ui_eval, "score_screenshot", lambda *a, **kw: calls.append(1) or {"ok": False})
    ui_eval.run(suffix="", api_key="fake-key")
    # only this one screenshot exists in tmp_path (the other 11 are correctly
    # skipped as missing, per test_run_skips_missing_screenshots_without_crashing)
    # and it's a cache hit, so score_screenshot must never be called at all.
    assert len(calls) == 0
