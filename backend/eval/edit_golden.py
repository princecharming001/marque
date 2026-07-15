"""Golden data for the EDL invariant suite (edl_eval.py).

Two tripwires, mirroring eval/golden.py's KNOWN_GOOD / KNOWN_BAD shape:

- `reference_edl(fixture)` builds the EDL a competent editor/assembler SHOULD produce for
  a take: filler + dead-air dropped, the buried hook pulled to the front, one caption per
  kept word (with end_frame). Every fixture's reference EDL must pass ALL invariants clean.
- `KNOWN_BAD` are crafted render-plans (or EDLs) each carrying exactly one defect, tagged
  with the invariant `code` that must catch it. These test the CHECKERS themselves — so if
  someone later weakens the min-clip guard or the hook logic, the tripwire still fires even
  though the guard used to mask it.
"""
from __future__ import annotations

from app.edl import ms_to_frame, strip_fillers, strip_fillers_v2, build_render_plan
from eval.edit_fixtures import FIXTURES, take_total_frames


def reference_edl(fx: dict) -> dict:
    """The EDL a good assembler should emit for this fixture."""
    words = fx["words"]
    total = take_total_frames(words)
    # stutter-heavy needs the detect_disfluencies layer too: a word-repeat stutter
    # ("I I") and a multi-word discourse phrase ("you know") are both invisible to
    # strip_fillers' single-token lexicon. strip_fillers_v2 is exactly "strip_fillers
    # + detect_disfluencies, merged" (app/edl.py) — what a competent editor's cut list
    # actually covers for this kind of take.
    if fx["category"] == "stutter-heavy":
        kept, drops = strip_fillers_v2(words)
    else:
        kept, drops = strip_fillers(words)
    drop_dicts = [d.model_dump() for d in drops]

    # Buried hook: a good edit pulls the payoff forward by dropping the intro
    # throat-clearing (source 0 → hook). Everything after the hook plays in order.
    hook_ms = fx.get("hook_ms") or 0
    hook_frame = ms_to_frame(hook_ms)
    if fx["category"] == "buried-hook" and hook_frame > 0:
        drop_dicts.append({"src_in": 0, "src_out": hook_frame, "reason": "intro"})

    captions = [
        {"word": w["word"], "frame": ms_to_frame(w["start_ms"]),
         "end_frame": ms_to_frame(w["end_ms"])}
        for w in kept
    ]
    return {
        "style": fx["style"],
        "format_id": "reel",
        "segments": [{"src_in": 0, "src_out": total}],
        "drops": drop_dicts,
        "captions": captions,
        "speech_frames": [c["frame"] for c in captions],
        "layout": {"style": fx["style"]},
        "audio": {"music": None, "lufs_target": -14.0, "gain": 0.0},
    }


def known_good() -> list[dict]:
    """One case per fixture: {id, edl, words, hook_ms}."""
    return [
        {"id": fx["id"], "edl": reference_edl(fx), "words": fx["words"], "hook_ms": fx.get("hook_ms") or 0}
        for fx in FIXTURES
    ]


# --- Known-bad crafted plans/EDLs — each must be caught by its `code` ----------
# A minimal well-formed base plan we mutate per case.
def _base_plan() -> dict:
    fx = FIXTURES[0]
    return build_render_plan(reference_edl(fx))


def known_bad() -> list[dict]:
    cases: list[dict] = []

    # 1. Sliver clip in the output (a 4-frame clip). The guard should prevent this;
    #    the checker must catch it if the guard is ever weakened.
    p = _base_plan()
    p["clips"] = [{"src_in": 0, "src_out": 300, "speed": 1.0, "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0},
                  {"src_in": 400, "src_out": 404, "speed": 1.0, "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0}]
    cases.append({"code": "sliver", "why": "4-frame output clip", "plan": p})

    # 2. Buried hook not pulled forward: hook lands at output frame ~540 (18s).
    fx = next(f for f in FIXTURES if f["category"] == "buried-hook")
    edl = reference_edl(fx)
    edl["drops"] = [d for d in edl["drops"] if d.get("reason") != "intro"]  # un-pull the hook
    cases.append({"code": "hook_late", "why": "buried hook kept in place",
                  "edl": edl, "words": fx["words"], "hook_ms": fx["hook_ms"]})

    # 3. Missing captions: kept words but empty caption track.
    fx2 = FIXTURES[0]
    edl2 = reference_edl(fx2)
    edl2["captions"] = edl2["captions"][:3]   # only 3 of ~56 kept words captioned
    cases.append({"code": "caption_gap", "why": "captions truncated to 3 words",
                  "edl": edl2, "words": fx2["words"], "hook_ms": 0})

    # 4a. B-roll hold too long (5s).
    p2 = _base_plan()
    p2["broll"] = [{"frame_in": 120, "frame_out": 270, "query": "city", "source": "pexels"}]
    cases.append({"code": "broll_hold", "why": "5s b-roll hold", "plan": p2})

    # 4b. B-roll over the hook (first 90 output frames).
    p3 = _base_plan()
    p3["broll"] = [{"frame_in": 10, "frame_out": 70, "query": "intro", "source": "pexels"}]
    cases.append({"code": "broll_hook", "why": "b-roll covers the hook", "plan": p3})

    # 4c. Two b-rolls spaced < 90 frames apart.
    p4 = _base_plan()
    p4["broll"] = [{"frame_in": 150, "frame_out": 210, "query": "a", "source": "pexels"},
                   {"frame_in": 230, "frame_out": 290, "query": "b", "source": "pexels"}]
    cases.append({"code": "broll_spacing", "why": "b-rolls 20f apart", "plan": p4})

    # 5. Drop outside the take (src_out beyond total frames).
    fx3 = FIXTURES[0]
    edl3 = reference_edl(fx3)
    total = take_total_frames(fx3["words"])
    edl3["drops"].append({"src_in": total + 50, "src_out": total + 90, "reason": "bogus"})
    cases.append({"code": "drop_out_of_take", "why": "drop past end of take",
                  "edl": edl3, "words": fx3["words"], "hook_ms": 0, "total_override": total})

    # 6. Structurally invalid EDL (overlapping / non-monotonic segments).
    fx4 = FIXTURES[0]
    edl4 = reference_edl(fx4)
    edl4["segments"] = [{"src_in": 0, "src_out": 300}, {"src_in": 100, "src_out": 500}]
    cases.append({"code": "edl_invalid", "why": "overlapping segments",
                  "edl": edl4, "words": fx4["words"], "hook_ms": 0})

    # 7. Residual filler: the stutter-heavy reference edit correctly drops its
    #    lexicon filler ("um") — delete JUST that one drop (the stutter + "you know"
    #    phrase drops are left intact, so this carries exactly one defect like every
    #    other case here) and restore its caption, so "um" survives as a rendered
    #    caption. check_residual_filler must catch it even though every other
    #    invariant still passes clean.
    fx5 = next(f for f in FIXTURES if f["category"] == "stutter-heavy")
    edl5 = reference_edl(fx5)
    um_word = next(w for w in fx5["words"] if w["word"] == "um")
    um_frame = ms_to_frame(um_word["start_ms"])
    edl5["drops"] = [d for d in edl5["drops"] if not (d["src_in"] <= um_frame < d["src_out"])]
    # reference_edl() built captions from the FILTERED `kept` words, which excluded
    # "um" — deleting the drop alone doesn't resurrect a caption that was never
    # emitted, so add it back explicitly to make this a real repro of the bug
    # (residual audio AND a lingering caption for it).
    edl5["captions"] = sorted(
        edl5["captions"] + [{"word": "um", "frame": um_frame,
                             "end_frame": ms_to_frame(um_word["end_ms"])}],
        key=lambda c: c["frame"])
    cases.append({"code": "residual_filler", "why": "um survives after its drop is deleted",
                  "edl": edl5, "words": fx5["words"], "hook_ms": 0})

    return cases


# ---------------------------------------------------------------------------
# A1: deterministic edit-lint tripwires (app.edit_lint). Unlike the checks above,
# these grade a FULLY-ASSEMBLED, retention-passed edit — a bare single-segment
# `reference_edl` (no overlays at all) is legitimately "static" by lint's own
# definition, so these fixtures are hand-built with realistic overlay density and
# tested SEPARATELY (eval.edl_eval.self_check_lint), never mixed into the bare
# reference_edl known_good()/known_bad() tripwires above.
# ---------------------------------------------------------------------------

def _lint_words(n: int = 120, step_ms: int = 120) -> list[dict]:
    """A long, evenly-spaced word track (14.4s @ n=120) so fixtures have plenty of
    source frames to place overlays/segments/transitions across."""
    return [{"word": "word", "start_ms": i * step_ms, "end_ms": i * step_ms + 90} for i in range(n)]


def _lint_base_edl(words: list[dict]) -> dict:
    """A DENSE, well-formed EDL: varied framing across 3 segments, alternating
    word-anchored overlays every ~130 output frames, one font, a short transition.
    Every lint ERROR check should be clean on this — mutate exactly one thing per
    known-bad case below."""
    total = int(words[-1]["end_ms"] / 1000 * 30) + 60
    third = total // 3
    overlays = []
    otype = "punch_in"
    for f in range(30, total - 60, 130):
        w = min(words, key=lambda w: abs(w["start_ms"] / 1000 * 30 - f))
        anchor = int(w["start_ms"] / 1000 * 30)
        overlays.append({"type": otype, "src_in": anchor, "src_out": anchor + 30,
                         "scale": 1.08, "text": "hi" if otype == "text_sticker" else "",
                         "font": "inter"})
        otype = "text_sticker" if otype == "punch_in" else "punch_in"
    return {
        "style": "talking_head", "format_id": "myth-buster",
        "segments": [
            {"src_in": 0, "src_out": third, "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0},
            {"src_in": third, "src_out": 2 * third, "tx_scale": 1.18, "tx_x": 0.0, "tx_y": -0.02},
            {"src_in": 2 * third, "src_out": total, "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0},
        ],
        "drops": [], "captions": [], "speech_frames": [],
        "overlays": overlays, "broll": [],
        "layout": {"style": "talking_head"},
        "audio": {"lufs_target": -14.0, "gain": 0.0, "sfx": []},
        "caption_options": {"font": "inter"},
        "transitions": [{"after_segment": 0, "style": "fade_black", "frames": 10},
                        {"after_segment": 1, "style": "fade_black", "frames": 10}],
        "look": None, "end_card": None,
    }


def known_bad_lint() -> list[dict]:
    """One case per lint ERROR code: {code, why, edl, words}. Each mutates the dense
    base fixture with exactly one defect."""
    cases: list[dict] = []
    words = _lint_words()

    # static_window: strip every overlay -> long dead stretches with zero events.
    edl = _lint_base_edl(words)
    edl["overlays"] = []
    cases.append({"code": "static_window", "why": "no overlays -> 150f+ dead stretches",
                  "edl": edl, "words": words})

    # static_open: push every overlay past the first 1.5s.
    edl = _lint_base_edl(words)
    edl["overlays"] = [o for o in edl["overlays"] if o["src_in"] > 60]
    cases.append({"code": "static_open", "why": "no event in the opening 45 output frames",
                  "edl": edl, "words": words})

    # same_framing_adjacent: make segment 1's tx_scale match segment 0's (no delta at the cut).
    edl = _lint_base_edl(words)
    edl["segments"][1]["tx_scale"] = edl["segments"][0]["tx_scale"]
    cases.append({"code": "same_framing_adjacent", "why": "adjacent segments share tx_scale",
                  "edl": edl, "words": words})

    # long_dissolve: one transition way past the 15f cap.
    edl = _lint_base_edl(words)
    edl["transitions"][0]["frames"] = 40
    cases.append({"code": "long_dissolve", "why": "40f transition (> 15f cap)",
                  "edl": edl, "words": words})

    # tail_rules: an end_card on fast_cuts, which never renders one.
    edl = _lint_base_edl(words)
    edl["style"] = "fast_cuts"
    edl["end_card"] = {"text": "Follow for more", "frames": 75, "show_handle": True}
    cases.append({"code": "tail_rules", "why": "end_card present on fast_cuts",
                  "edl": edl, "words": words})

    # bundle_coherence: a second text_sticker font mixed into an otherwise one-font EDL.
    edl = _lint_base_edl(words)
    for o in edl["overlays"]:
        if o["type"] == "text_sticker":
            o["font"] = "anton"
            break
    cases.append({"code": "bundle_coherence", "why": "mixed fonts (inter + anton) in one EDL",
                  "edl": edl, "words": words})

    return cases


def known_good_lint() -> dict:
    """The dense, well-formed base fixture itself — every lint ERROR check must be
    clean on it (the false-positive tripwire for the lint's error tier)."""
    words = _lint_words()
    return {"id": "lint-dense-clean", "edl": _lint_base_edl(words), "words": words}
