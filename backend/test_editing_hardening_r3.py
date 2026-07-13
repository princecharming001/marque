"""Editing-pipeline hardening, round 3 — regression tests for the 2026-07-12
continuation batch (finishing the "editing builds are failing" job). Every test
runs KEYLESS (same seams as test_editor_hardening.py / _r2).

Covered fixes:
  #9  edit_overlay / set_segment_transform apply ATOMICALLY — a later invalid
      field no longer leaves earlier fields written into the persisted EDL.
  #10 split_segment / trim_* remap transitions[].after_segment so a fade stays on
      the boundary the creator set instead of jumping to a neighbour.
  #45 split_segment carries the parent's speed + canvas transform onto both halves.
  #17 _scaled_render_budgets grows the poll/stall budgets with output length.
  #19 build_render_plan stamps schema_version (backend↔Remotion drift guard).
  #5  verify_and_repair_edl re-clamps the SONNET repair's EDL to the real source
      extent (the repair runs downstream of clamp_edl_to_source).
  #16 a tweak add_broll that resolves to nothing surfaces a broll_unresolved
      warning instead of a silent pixel-identical re-render.
(#18 and the submit total_frames handoff are covered in test_editor_hardening.py.)
"""
import asyncio
import json
import time
import uuid

import main
from app.edl import apply_edl_ops, build_render_plan, PLAN_SCHEMA_VERSION


def _base_edl(**extra):
    return {
        "style": "talking_head", "format_id": "myth-buster",
        "segments": [{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200},
                     {"src_in": 200, "src_out": 300}],
        "layout": {"style": "talking_head"},
        **extra,
    }


def _job(job_id=None, **over):
    job_id = job_id or str(uuid.uuid4())
    job = {
        "job_id": job_id, "status": "ready", "created_at": time.time(),
        "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "ready"}],
        "edl": None, "words": [], "edl_history": [], "tweaks": [],
        "script": {"hook": "h", "formatId": "myth-buster"}, "style": "talking_head",
        "source_url": "mock://x", "edit_prefs": {},
    }
    job.update(over)
    main._clip_jobs[job_id] = job
    return job_id


def _cleanup(job_id):
    main._clip_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# #9 — atomic op application (no half-writes on a later bad field)
# ---------------------------------------------------------------------------

def test_edit_overlay_is_atomic_when_a_later_field_is_invalid():
    edl = _base_edl(overlays=[{"type": "text_sticker", "src_in": 0, "src_out": 60,
                               "text": "hi", "pos_x": 0.5, "pos_y": 0.5, "scale": 1.0}])
    # A valid text change + an INVALID pos_x. The op must be rejected AND leave the
    # overlay completely untouched — no half-applied text.
    new_edl, results = apply_edl_ops(
        edl, [{"type": "edit_overlay", "index": 0, "text": "CHANGED", "pos_x": "junk"}])
    assert results[0]["applied"] is False
    assert new_edl["overlays"][0]["text"] == "hi"          # text NOT half-committed


def test_set_segment_transform_is_atomic_when_a_later_field_is_invalid():
    edl = _base_edl()
    # scale valid, off_x junk → whole op rejected, scale must NOT be written.
    new_edl, results = apply_edl_ops(
        edl, [{"type": "set_segment_transform", "index": 0, "scale": 2.0, "off_x": "junk"}])
    assert results[0]["applied"] is False
    assert "tx_scale" not in new_edl["segments"][0]        # scale NOT half-committed


def test_edit_overlay_still_commits_a_fully_valid_change():
    edl = _base_edl(overlays=[{"type": "text_sticker", "src_in": 0, "src_out": 60,
                               "text": "hi", "pos_x": 0.5, "pos_y": 0.5, "scale": 1.0}])
    new_edl, results = apply_edl_ops(
        edl, [{"type": "edit_overlay", "index": 0, "text": "there", "pos_x": 0.3}])
    assert results[0]["applied"] is True
    assert new_edl["overlays"][0]["text"] == "there"
    assert abs(new_edl["overlays"][0]["pos_x"] - 0.3) < 1e-9


# ---------------------------------------------------------------------------
# #10 — transitions travel with their boundary through split / trim
# ---------------------------------------------------------------------------

def test_split_shifts_transitions_after_the_cut():
    edl = _base_edl(transitions=[{"after_segment": 1, "style": "fade_black", "frames": 12}])
    # split segment 0 inserts a half at index 1; every source index >= 0 shifts +1, so
    # the transition anchored at 1 (an unrelated boundary) re-indexes to 2.
    new_edl, results = apply_edl_ops(edl, [{"type": "split_segment", "index": 0, "at_frame": 50}])
    assert results[0]["applied"] is True
    assert new_edl["transitions"][0]["after_segment"] == 2


def test_split_at_a_transition_boundary_keeps_it_on_the_second_half():
    edl = _base_edl(transitions=[{"after_segment": 0, "style": "fade_black", "frames": 12}])
    # a transition after seg 0 sits at the end of seg-0's footage; after the split that
    # footage ends at the SECOND half (new index 1), so the anchor becomes 1.
    new_edl, results = apply_edl_ops(edl, [{"type": "split_segment", "index": 0, "at_frame": 50}])
    assert results[0]["applied"] is True
    assert new_edl["transitions"][0]["after_segment"] == 1


def test_trim_start_drops_the_popped_boundary_and_remaps_the_rest():
    edl = _base_edl(transitions=[{"after_segment": 0, "style": "fade_black", "frames": 12},
                                 {"after_segment": 1, "style": "flash", "frames": 8}])
    # trim 100 frames off the start consumes segment 0 entirely → it's popped.
    new_edl, results = apply_edl_ops(edl, [{"type": "trim_start", "frames": 100}])
    assert results[0]["applied"] is True
    afters = [t["after_segment"] for t in new_edl["transitions"]]
    # transition anchored at the popped segment 0 is dropped; the one at 1 shifts to 0.
    assert afters == [0]


# ---------------------------------------------------------------------------
# #45 — split inherits per-segment speed + transform
# ---------------------------------------------------------------------------

def test_split_preserves_speed_and_transform_on_both_halves():
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 200, "speed": 2.0,
                               "tx_scale": 1.5, "tx_x": 0.1, "tx_y": -0.2}])
    new_edl, results = apply_edl_ops(edl, [{"type": "split_segment", "index": 0, "at_frame": 100}])
    assert results[0]["applied"] is True
    a, b = new_edl["segments"][0], new_edl["segments"][1]
    for seg in (a, b):
        assert seg["speed"] == 2.0
        assert seg["tx_scale"] == 1.5 and seg["tx_x"] == 0.1 and seg["tx_y"] == -0.2
    assert (a["src_in"], a["src_out"]) == (0, 100)
    assert (b["src_in"], b["src_out"]) == (100, 200)


# ---------------------------------------------------------------------------
# #17 — poll/stall budgets scale with output length
# ---------------------------------------------------------------------------

def test_scaled_render_budgets_grow_with_frames_and_cap():
    assert main._scaled_render_budgets(0) == (main.RENDER_POLL_MAX_S, main.RENDER_STALL_S)
    assert main._scaled_render_budgets(None) == (main.RENDER_POLL_MAX_S, main.RENDER_STALL_S)
    short = main._scaled_render_budgets(300)       # ~10s clip
    long = main._scaled_render_budgets(5400)       # ~3min clip
    assert short[0] > main.RENDER_POLL_MAX_S        # a real length lifts the poll budget
    assert long[0] > short[0]                       # and a longer take lifts it further
    assert long[1] >= short[1] >= main.RENDER_STALL_S
    assert long[0] <= main.RENDER_POLL_CEIL_S        # capped so it can't run unbounded


# ---------------------------------------------------------------------------
# #19 — plan carries the schema version stamp
# ---------------------------------------------------------------------------

def test_build_render_plan_stamps_schema_version():
    plan = build_render_plan(_base_edl())
    assert plan["schema_version"] == PLAN_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# #26 — an all-sliver edit still renders, but says so (never a silent sub-second clip)
# ---------------------------------------------------------------------------

def test_all_sliver_edit_delivers_longest_with_a_warning():
    # A take cut down to nothing but sub-400ms islands: [0,8) and [150,158), both
    # 8 output frames (< the 12-frame / 400ms floor).
    edl = {"style": "talking_head", "format_id": "x",
           "segments": [{"src_in": 0, "src_out": 300}],
           "drops": [{"src_in": 8, "src_out": 150, "reason": "x"},
                     {"src_in": 158, "src_out": 300, "reason": "x"}],
           "layout": {"style": "talking_head"}}
    warnings: list = []
    plan = build_render_plan(edl, warnings)
    assert plan["total_frames"] >= 1                         # still non-empty
    assert any("degenerate_edit" in w for w in warnings)     # surfaced, not silent


# ---------------------------------------------------------------------------
# #5 — verify_and_repair_edl re-clamps the repair's EDL to the source extent
# ---------------------------------------------------------------------------

def test_verify_and_repair_reclamps_out_of_bounds_repair(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    calls = {"n": 0}

    async def fake_anthropic(sysp, usrp, model, max_tokens, **kw):
        calls["n"] += 1
        if calls["n"] == 1:                          # verdict: fail with an issue
            return json.dumps({"verdict": "fail", "issues": ["segment runs past the end"]})
        # repair hallucinates a src_out FAR past the ~300-frame source
        return json.dumps({"style": "talking_head", "format_id": "myth-buster",
                           "segments": [{"src_in": 0, "src_out": 99999}],
                           "layout": {"style": "talking_head"}})

    monkeypatch.setattr(main, "anthropic", fake_anthropic)
    words = [{"word": "hi", "start_ms": 0, "end_ms": 10_000}]     # → 300 source frames
    edl_in = {"style": "talking_head", "format_id": "myth-buster",
              "segments": [{"src_in": 0, "src_out": 300}], "layout": {"style": "talking_head"}}
    out = asyncio.run(main.verify_and_repair_edl("talking_head", edl_in, words,
                                                 {"formatId": "myth-buster"}))
    # the repair's runaway src_out must be clamped back to the real extent, not shipped.
    assert max(s["src_out"] for s in out["segments"]) <= 300


# ---------------------------------------------------------------------------
# #16 — a tweak add_broll that resolves to nothing warns instead of no-op-lying
# ---------------------------------------------------------------------------

def test_tweak_rerender_warns_when_added_broll_unresolved(monkeypatch):
    async def fake_resolve(edl, **kw):
        e = dict(edl)
        e["broll"] = [{"src_in": 0, "src_out": 60, "broll_query": "sunset", "source": "stock"}]
        return e                                     # left UNRESOLVED (no resolved_url)

    async def fake_submit(url, edl, fmt, style, preview=False):
        return {"render_id": "r", "bucket_name": "b", "plan_warnings": [], "total_frames": 300}

    async def fake_poll(rid, bkt, **kw):
        return "https://cdn/out.mp4"

    monkeypatch.setattr(main, "_resolve_broll", fake_resolve)
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "_poll_remotion_render", fake_poll)
    job_id = _job(edl={"style": "talking_head", "format_id": "x",
                       "segments": [{"src_in": 0, "src_out": 300}],
                       "layout": {"style": "talking_head"}})
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip["status"] = "rendering"
    clip["render_url"] = "https://old.mp4"
    gen = main._bump_render_gen(clip)
    asyncio.run(main._rerender_clip(job_id, "c1", gen, resolve_broll=True))
    assert any("broll_unresolved" in str(w) for w in clip.get("warnings", []))
    _cleanup(job_id)
