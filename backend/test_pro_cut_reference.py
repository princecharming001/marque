"""Pro-cut grammar enforcement — the pipeline is graded against a REAL professional
editor's cut of a REAL raw take (eval/pro_cut_reference.py has the full story).

Four invariants, each traceable to the measured pro behavior:
1. Natural sentence pauses (300-550ms) SURVIVE dead-air trimming — the pro kept ~17/22
   verbatim; the old 350ms->200ms policy compressed nearly all of them (choppiness).
2. Seam budget: dead-air micro-splices on this take drop from ~19 to a handful — only
   genuinely long stalls (>600ms) get tightened.
3. The ENDING is sacred: a plan cut that swallows the closing CTA/payoff is rejected
   unless it's a genuine sign-off; the pro kept the CTA in full.
4. False-started opening takes are still removable (the one thing both editors cut).
"""
from __future__ import annotations

from app.edl import (TRIM_LEVELS, assemble_edl, ms_to_frame, strip_fillers)
from eval.pro_cut_reference import RAW_WORDS, natural_pauses


def _dead_air_drops(drops):
    return [d for d in drops if d.reason == "dead_air"]


def test_natural_pauses_survive_dead_air_trim():
    # The pro preserved every 300-550ms pause. With the calibrated thresholds
    # (gap_ms=600), NONE of them should even trigger a dead-air cut.
    clean, drops = strip_fillers(RAW_WORDS)
    da = _dead_air_drops(drops)
    pauses = natural_pauses(RAW_WORDS, 300, 550)
    assert len(pauses) >= 15, "fixture sanity: the take has many natural pauses"
    for end_ms, start_ms, gap in pauses:
        a_f, b_f = ms_to_frame(end_ms), ms_to_frame(start_ms)
        overlapping = [d for d in da if d.src_in < b_f and d.src_out > a_f]
        assert not overlapping, (
            f"natural {gap}ms pause at {end_ms/1000:.1f}s was dead-air-cut — the pro kept "
            f"these verbatim; only >600ms stalls may be tightened")


def test_seam_budget_matches_pro_scale():
    # Old policy: ~19 dead-air splices on this take. Pro: ~2 tightenings. Allow a
    # small margin but stay on the pro's order of magnitude.
    _, drops = strip_fillers(RAW_WORDS)
    da = _dead_air_drops(drops)
    assert len(da) <= 4, f"{len(da)} dead-air splices — the pro made ~2 on this take"


def test_residual_pause_is_a_real_beat():
    # When a long stall IS tightened, the residual must be a breath (~333ms), not a
    # 200ms gasp: keep_pause_frames >= 10 at the default level.
    assert TRIM_LEVELS["default"]["keep_pause_frames"] >= 10
    assert TRIM_LEVELS["default"]["gap_ms"] >= 550
    assert TRIM_LEVELS["aggressive"]["gap_ms"] >= 400, \
        "even 'aggressive' must not machine-gun natural sentence pauses"


def test_ending_cut_rejected_but_opening_false_starts_removed():
    total = ms_to_frame(RAW_WORDS[-1]["end_ms"])
    # The EXACT failure observed live: the plan cut the closing 5.3s ("...it works.
    # Rate that version 7 out of 10. Follow for the next collision test.") as a
    # "false_start", plus the legitimate opening false-start cut.
    plan = {"cuts": [
        {"range": [ms_to_frame(0), ms_to_frame(8800)], "reason": "false_start",
         "quote": "Most fusion— most fusion—"},
        {"range": [ms_to_frame(44700), total], "reason": "false_start",
         "quote": "it works. Rate that version 7 out of 10..."},
    ]}
    edl = assemble_edl(plan, RAW_WORDS, "talking_head", "myth-buster").model_dump()
    kept_spans = []
    drops = edl["drops"]
    def _in_drop(f):
        return any(d["src_in"] <= f < d["src_out"] for d in drops)
    # Opening false start removed:
    first_flub_f = ms_to_frame(RAW_WORDS[1]["start_ms"])
    assert _in_drop(first_flub_f), "opening false-start take must still be cut"
    # Ending retained: the last word ("test.") must NOT be inside any drop.
    last_word_f = ms_to_frame(RAW_WORDS[-1]["start_ms"])
    assert not _in_drop(last_word_f), \
        "the closing CTA was swallowed by a mislabeled false_start cut — must be rejected"


def test_genuine_signoff_still_cuttable_at_end():
    # Guard must NOT protect a real sign-off: synthesize a take ending in
    # "thanks for watching" and cut it — allowed.
    words = list(RAW_WORDS[:20])
    t = words[-1]["end_ms"] + 400
    for w in ("thanks", "for", "watching"):
        words.append({"word": w, "start_ms": t, "end_ms": t + 250})
        t += 300
    total = ms_to_frame(words[-1]["end_ms"])
    plan = {"cuts": [{"range": [ms_to_frame(words[20]["start_ms"]), total],
                      "reason": "filler", "quote": "thanks for watching"}]}
    edl = assemble_edl(plan, words, "talking_head", "myth-buster").model_dump()
    last_f = ms_to_frame(words[-1]["start_ms"])
    assert any(d["src_in"] <= last_f < d["src_out"] for d in edl["drops"]), \
        "a genuine trailing sign-off must remain cuttable"


def _w(word, start_ms, end_ms):
    return {"word": word, "start_ms": start_ms, "end_ms": end_ms}


# 5. Mid-sentence integrity (prod job 90813e10): "Everyone tries to pair fusion by
#    taste." lost "to pair fusion by" to a hallucinated false_start. The removed
#    words are NOT re-delivered anywhere after the cut, and the seam starts
#    mid-sentence ("tries" has no terminal punctuation) — the cut must be vetoed.
_FUSION_WORDS = [
    _w("Everyone", 13267, 13497), _w("tries", 13497, 13770),
    _w("to", 13770, 13882), _w("pair", 13882, 14123), _w("fusion", 14123, 14363),
    _w("by", 14833, 14933), _w("taste.", 14933, 15266),
    _w("Gochujang", 15633, 16366), _w("tastes", 16700, 16900),
    _w("bold,", 17000, 17333), _w("carbonara", 17566, 18066),
    _w("tastes", 18066, 18366), _w("rich.", 18366, 18600),
]


def test_hallucinated_midsentence_false_start_is_vetoed():
    cut_in = ms_to_frame(13770)   # "to"
    cut_out = ms_to_frame(14933)  # through "by"
    edl = assemble_edl({"cuts": [{"range": [cut_in, cut_out], "reason": "false_start"}]},
                       _FUSION_WORDS, "talking_head", "hot_take")
    pair_in, pair_out = ms_to_frame(13882), ms_to_frame(14363)   # "pair fusion"
    overlapping = [d for d in edl.drops
                   if d.reason == "false_start" and d.src_in < pair_out and d.src_out > pair_in]
    assert not overlapping, \
        "mid-sentence, never-re-delivered words were cut — interior guard failed"


def test_real_midsentence_retake_still_cut():
    # A genuine stumble re-delivers: "you're not— you're not matching flavors" —
    # the removed tokens all reappear right after, so the cut stays allowed.
    ws = [
        _w("Look,", 0, 300), _w("you're", 400, 600), _w("not—", 600, 900),
        _w("you're", 1400, 1600), _w("not", 1600, 1800),
        _w("matching", 1800, 2200), _w("flavors.", 2200, 2700),
        _w("Fat,", 3100, 3400), _w("acid,", 3500, 3800), _w("and", 3900, 4000),
        _w("heat", 4000, 4300), _w("carry", 4300, 4600), _w("the", 4600, 4700),
        _w("dish.", 4700, 5100), _w("That", 5600, 5800), _w("is", 5800, 5900),
        _w("the", 5900, 6000), _w("whole", 6000, 6300), _w("trick.", 6300, 6700),
    ]
    cut_in = ms_to_frame(400)
    cut_out = ms_to_frame(1400)
    edl = assemble_edl({"cuts": [{"range": [cut_in, cut_out], "reason": "false_start"}]},
                       ws, "talking_head", "hot_take")
    drops = [d.model_dump() for d in edl.drops]
    assert any(d["reason"] == "false_start" and d["src_out"] > d["src_in"] + 20 for d in drops), \
        "a genuine re-delivered retake should still be cuttable"


def test_sentence_boundary_cut_still_allowed():
    # A whole-sentence tangent cut whose seam sits on terminal punctuation needs no
    # re-delivery — classic false-start/tangent shape stays cuttable.
    ws = [
        _w("Great.", 0, 400),
        _w("Anyway", 1000, 1300), _w("random", 1300, 1700), _w("tangent", 1700, 2100),
        _w("here.", 2100, 2500),
        _w("The", 3100, 3300), _w("point", 3300, 3600), _w("stands.", 3600, 4000),
        _w("Structure", 4600, 5000), _w("beats", 5000, 5300), _w("flavor", 5300, 5700),
        _w("every", 5700, 6000), _w("single", 6000, 6300), _w("time", 6300, 6600),
        _w("you", 6600, 6700), _w("cook.", 6700, 7100),
    ]
    edl = assemble_edl({"cuts": [{"range": [ms_to_frame(1000), ms_to_frame(2600)],
                                  "reason": "tangent"}]},
                       ws, "talking_head", "hot_take")
    t_in, t_out = ms_to_frame(1700), ms_to_frame(2100)           # "tangent"
    covered = any(d.src_in <= t_in and d.src_out >= t_out for d in edl.drops)
    assert covered, "sentence-boundary tangent cut was wrongly vetoed"


def test_head_cut_snaps_back_to_sentence_start():
    # Prod job 41a4579c: the opening false_start cut [0,300] swallowed the first six
    # words of the clean re-delivery ("most fusion fails for the same") and the video
    # OPENED mid-sentence ("…reason. You're matching"). The head cut must shrink back
    # to the last sentence boundary inside it (the stumble dash on "you're—").
    ws = [
        _w("Most", 1033, 1366), _w("fusion—", 1366, 1800),
        _w("most", 3200, 3700), _w("fusion", 3700, 5600),
        _w("fails", 5633, 5966), _w("for", 6033, 6133), _w("the", 6133, 6233),
        _w("same", 6233, 6466), _w("reason.", 6500, 6733),
        _w("You're", 7066, 7200), _w("not", 7200, 7366), _w("matching—", 7433, 7766),
        _w("you're—", 8300, 8833),
        # clean re-delivery begins here — the cut below wrongly extends into it
        _w("most", 8866, 9033), _w("fusion", 9033, 9300), _w("fails", 9400, 9633),
        _w("for", 9733, 9800), _w("the", 9800, 9833), _w("same", 9833, 10000),
        _w("reason.", 10066, 10300),
        _w("You're", 10566, 10666), _w("matching", 10666, 10933),
        _w("flavors", 10966, 11333), _w("instead", 11366, 11600),
        _w("of", 11600, 11733), _w("fat,", 11833, 12133),
        _w("acid,", 12300, 12566), _w("and", 12666, 12733), _w("heat.", 12733, 12900),
    ]
    cut = [0, ms_to_frame(10000)]        # planner overshoots into "…for the same"
    edl = assemble_edl({"cuts": [{"range": cut, "reason": "false_start"}]},
                       ws, "talking_head", "hot_take")
    head_drop = max((d.src_out for d in edl.drops if d.src_in <= 35), default=0)
    redelivery_start = ms_to_frame(8866)   # second "most"
    assert head_drop <= redelivery_start, (
        f"head cut ends at {head_drop}, swallowing the re-delivery that starts at "
        f"{redelivery_start} — the video would open mid-sentence")
    assert head_drop >= ms_to_frame(8300), "the real false-start block must still be cut"
