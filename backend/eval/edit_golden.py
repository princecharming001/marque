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
