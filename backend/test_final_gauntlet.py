"""F.2 — end-to-end mock walkthrough across VIDEO_UNDERSTANDING modes.

Exercises the full keyless pipeline seam for each provider mode
(off / claude_frames(mocked) / twelvelabs(mocked HTTP)):
    dossier → brief (dossier present when on) → confirm toggles → assemble EDL →
    build_render_plan, asserting the edl_eval invariants (no slivers, hook ≤90 out,
    caption coverage, drops ⊆ take) hold at the END of the pipeline.
"""
from __future__ import annotations

import asyncio
import pytest

import main
import prompts
from app import dossier as D
from app.edl import assemble_edl, build_render_plan, ms_to_frame
from eval import edl_eval
from eval.edit_fixtures import FIXTURES, fixture


def _run(coro):
    return asyncio.run(coro)


# --- provider seams: make each mode resolve keyless via mocks --------------------

def _mock_twelvelabs(monkeypatch):
    monkeypatch.setattr(D, "TWELVELABS_KEY", "tl")
    monkeypatch.setattr(D, "TWELVELABS_INDEX_ID", "idx")

    async def fake_sleep(s): return None
    async def fake_req(method, path, **kw):
        if path == "/tasks":
            return {"_id": "t1"}
        if path.startswith("/tasks/"):
            return {"status": "ready", "video_id": "v1"}
        if path == "/generate":
            return {"data": {
                "first_frame": {"desc": "creator on camera", "pattern_interrupt": True, "score": 0.7},
                "delivery_curve": [{"t0": 0, "t1": 3, "energy": 0.8, "note": "open"}],
                "visual_events": [{"t0": 1, "t1": 1.5, "kind": "gesture", "desc": "point"}],
                "framing": {"shot": "mid", "eye_contact": True, "lighting": "soft"},
                "broll_visual_opportunities": [{"t0": 2, "t1": 4, "cue": "result", "why": "named"}]}}
        return {}
    monkeypatch.setattr(D, "_sleep", fake_sleep)
    monkeypatch.setattr(D, "_tl_request", fake_req)


def _mock_claude_frames(monkeypatch):
    monkeypatch.setattr(D, "TWELVELABS_KEY", "")
    monkeypatch.setattr(D, "ANTHROPIC_KEY", "sk")

    async def fake_frames(url, dur):
        return [(0, b"jpeg1"), (2000, b"jpeg2")]
    async def fake_vision(system, user, images, schema):
        return {"first_frame": {"desc": "on camera", "pattern_interrupt": True, "score": 0.6},
                "framing": {"shot": "mid", "eye_contact": True}}
    monkeypatch.setattr(D, "_extract_keyframes", fake_frames)
    monkeypatch.setattr(D, "_vision_json", fake_vision)


@pytest.mark.parametrize("mode", ["off", "claude_frames", "twelvelabs"])
def test_full_pipeline_walkthrough_per_mode(mode, monkeypatch):
    if mode == "twelvelabs":
        _mock_twelvelabs(monkeypatch)
    elif mode == "claude_frames":
        _mock_claude_frames(monkeypatch)

    for fx in FIXTURES:
        # 1. DOSSIER (parallel-with-transcription in prod; here direct)
        dossier = _run(D.generate_dossier("http://x/v.mp4", 30000, provider=mode))
        if mode == "off":
            assert dossier is None
        else:
            assert dossier is not None and dossier["provider"] == mode

        # 2. BRIEF — keyless mock brief; the fusion prompt swaps the visual clause when a
        #    dossier is present (verified structurally).
        system, _ = prompts.edit_brief_prompt(fx["words"], dossier=dossier)
        if dossier:
            assert "VIDEO DOSSIER" in system and "cannot see the video" not in system
        else:
            assert "cannot see the video" in system

        # 3. CONFIRM toggles honored + 4. ASSEMBLE EDL (a competent plan opens on the hook)
        hook_f = ms_to_frame(fx["hook_ms"]) if fx.get("hook_ms") else 0
        plan = {"open_on": {"start": hook_f, "end": hook_f + 60, "why": "hook"}} if hook_f else {}
        prefs = {"broll": False}   # a toggle the assembler must honor
        edl = assemble_edl(plan, fx["words"], fx["style"], "reel", prefs=prefs)
        d = edl.model_dump()
        assert d["broll"] == []    # toggle honored

        # 5. build_render_plan + END-OF-PIPELINE invariants
        r = edl_eval.evaluate_edl(d, fx["words"], fx.get("hook_ms") or 0)
        assert r["failures"] == [], f"[{mode}/{fx['id']}] {r['failures']}"
        plan_out = build_render_plan(d)
        assert plan_out["total_frames"] > 0
        assert len(plan_out["clips"]) >= 1
