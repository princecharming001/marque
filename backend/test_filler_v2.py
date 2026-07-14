"""Filler v2 (2026-07-12 retention-editor upgrade, WS1) — regression tests for
TRIM_LEVELS, detect_disfluencies, and strip_fillers_v2. Every test is keyless and
pure (no seams to monkeypatch — these are plain functions over word lists).

Covers: multi-word discourse phrases (with content-word guards), stutter/repeat
detection (exact + partial + bigram-restart), false starts, trailing sign-offs,
confidence-aware cuts, the TRIM_LEVELS table itself, and silence tightening.
"""
from app.edl import (
    TRIM_LEVELS, ms_to_frame, strip_fillers, strip_fillers_v2, detect_disfluencies,
)


def _w(word, start_ms, end_ms, **extra):
    d = {"word": word, "start_ms": start_ms, "end_ms": end_ms}
    d.update(extra)
    return d


def _covered(drops, start_ms, end_ms, reason=None):
    lo, hi = ms_to_frame(start_ms), ms_to_frame(end_ms)
    return any(d.src_in <= lo and hi <= d.src_out and (reason is None or d.reason == reason)
               for d in drops)


# ---------------------------------------------------------------------------
# Multi-word discourse phrases
# ---------------------------------------------------------------------------

def test_phrase_you_know_cut_at_clause_boundary():
    words = [_w("You", 0, 150), _w("know", 150, 350), _w("there's", 350, 600),
             _w("a", 600, 650), _w("trick", 650, 900)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 0, 350, reason="filler")


def test_phrase_you_know_kept_when_content():
    # "Do you know" — a real question, not verbal filler. Flanked by real pauses
    # on both sides (which alone would pass the loosened phrase_mode>=1 rule), but
    # the explicit you-know guard (preceded by "do") must still block it.
    words = [_w("Do", 0, 200), _w("you", 350, 550), _w("know", 550, 750),
             _w("the", 900, 1000), _w("answer", 1000, 1300)]
    drops = detect_disfluencies(words, "default")
    assert not _covered(drops, 350, 750)


def test_kind_of_requires_pause_flank():
    # "what kind of dog" — tight, no pauses. "kind of" must survive even though
    # it's in FILLER_PHRASES, because it's legitimately content here.
    words = [_w("what", 0, 150), _w("kind", 150, 300), _w("of", 300, 450), _w("dog", 450, 700)]
    drops = detect_disfluencies(words, "default")
    assert not _covered(drops, 150, 450)


def test_kind_of_cut_when_pause_flanked():
    words = [_w("well", 0, 200), _w("kind", 350, 500), _w("of", 500, 650),
             _w("today", 800, 1100)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 350, 650, reason="filler")


# ---------------------------------------------------------------------------
# Stutter / word-repeat
# ---------------------------------------------------------------------------

def test_stutter_repeat_drops_earlier_instance():
    words = [_w("I", 0, 100), _w("I", 120, 220), _w("think", 220, 500)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 0, 100, reason="filler")
    assert not _covered(drops, 120, 220)   # the SECOND "I" (the one actually kept) survives


def test_stutter_partial_hyphen_prefix():
    words = [_w("Wai-", 0, 100), _w("Wait", 120, 300), _w("up", 300, 450)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 0, 100, reason="filler")


def test_bigram_restart_dropped():
    words = [_w("I", 0, 100), _w("think", 100, 350), _w("I", 450, 550),
             _w("think", 550, 800), _w("it's", 800, 1000)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 0, 350, reason="filler")


# ---------------------------------------------------------------------------
# False starts
# ---------------------------------------------------------------------------

def test_false_start_fragment_then_restart():
    words = [_w("So", 0, 200), _w("So", 550, 750), _w("today", 750, 950),
             _w("we're", 950, 1100), _w("starting", 1100, 1400)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 0, 200, reason="false_start")


def test_no_false_start_without_a_following_pause():
    # A repeated word with NO real pause after the fragment is just normal speech
    # cadence, not a false start — must not be flagged.
    words = [_w("So", 0, 200), _w("So", 210, 410), _w("today", 410, 650)]
    drops = detect_disfluencies(words, "default")
    assert not _covered(drops, 0, 200, reason="false_start")


# ---------------------------------------------------------------------------
# Trailing discourse sign-off
# ---------------------------------------------------------------------------

def test_trailing_so_yeah_dropped():
    words = [_w("we", 0, 150), _w("shipped", 150, 500), _w("it", 500, 650),
             _w("so", 1050, 1200), _w("yeah", 1200, 1400)]
    drops = detect_disfluencies(words, "default")
    assert _covered(drops, 1050, 1400, reason="filler")


def test_trailing_cta_word_not_dropped():
    # "...that's right." with NO real pause before "right" — it's the actual
    # payoff word, not a verbal tic sign-off.
    words = [_w("that's", 0, 300), _w("right", 400, 650)]
    drops = detect_disfluencies(words, "default")
    assert not _covered(drops, 400, 650)


# ---------------------------------------------------------------------------
# Confidence-aware cut
# ---------------------------------------------------------------------------

def test_confidence_cut_respects_emphasis():
    words = [
        _w("garbled", 0, 100, confidence=0.1, is_emphasized=True),
        _w("mumble", 200, 300, confidence=0.1, is_emphasized=False),
    ]
    drops = detect_disfluencies(words, "default")
    assert not _covered(drops, 0, 100)          # emphasized -> protected
    assert _covered(drops, 200, 300, reason="filler")   # not emphasized -> cut


# ---------------------------------------------------------------------------
# TRIM_LEVELS table
# ---------------------------------------------------------------------------

def test_trim_levels_table_monotonic():
    c, d, a = TRIM_LEVELS["conservative"], TRIM_LEVELS["default"], TRIM_LEVELS["aggressive"]
    assert c["gap_ms"] > d["gap_ms"] > a["gap_ms"]
    assert c["keep_pause_frames"] > d["keep_pause_frames"] > a["keep_pause_frames"]
    assert c["conf_cut"] < d["conf_cut"] < a["conf_cut"]
    assert c["phrase_mode"] < d["phrase_mode"] < a["phrase_mode"]


def test_aggressive_level_tightens_gap_threshold():
    # A 300ms gap: below aggressive's 250ms threshold trigger point (300>250, cuts)
    # but below conservative's 450ms threshold (300<450, no cut).
    words = [_w("hello", 0, 500), _w("world", 800, 1100)]
    _, agg_drops = strip_fillers_v2(words, "aggressive")
    _, cons_drops = strip_fillers_v2(words, "conservative")
    assert any(d.reason == "dead_air" for d in agg_drops)
    assert not any(d.reason == "dead_air" for d in cons_drops)


# ---------------------------------------------------------------------------
# Silence tightening (strip_fillers itself, #1b)
# ---------------------------------------------------------------------------

def test_silence_tighten_leaves_200ms_pause():
    words = [_w("hello", 0, 600), _w("world", 1000, 1200)]
    _, drops = strip_fillers(words, gap_ms=300, keep_pause_frames=6)
    dead = [d for d in drops if d.reason == "dead_air"]
    assert len(dead) == 1
    gap_start_f, gap_end_f = ms_to_frame(600), ms_to_frame(1000)
    # exactly keep_pause_frames (6) of the original gap survives uncut
    assert (dead[0].src_in - gap_start_f) + (gap_end_f - dead[0].src_out) == 6
    assert dead[0].src_in >= gap_start_f and dead[0].src_out <= gap_end_f


def test_silence_tighten_skips_when_too_little_room():
    # A gap only just over the threshold leaves < 4 droppable frames after
    # reserving keep_pause_frames on both sides — must not tighten at all.
    words = [_w("hello", 0, 500), _w("world", 810, 1000)]   # 310ms gap, gap_ms=300
    _, drops = strip_fillers(words, gap_ms=300, keep_pause_frames=6)
    assert not any(d.reason == "dead_air" for d in drops)


# ---------------------------------------------------------------------------
# Missed-word guard: dead-air trim only cuts VERIFIED-silent gaps
# ---------------------------------------------------------------------------

def test_dead_air_cut_when_gap_is_verified_silent():
    # A real 1s pause between two words — silencedetect confirms it's silent, so the
    # dead-air tighten fires exactly as before.
    words = [_w("hello", 0, 600), _w("world", 1600, 1800)]
    silent = [(600, 1600)]   # the whole gap is silence
    _, drops = strip_fillers(words, gap_ms=300, keep_pause_frames=6, silent_spans=silent)
    assert any(d.reason == "dead_air" for d in drops)


def test_dead_air_spared_when_gap_has_speech_energy():
    # Same-sized gap, but silencedetect found NO silence there → the transcriber dropped
    # a word whose audio still lives in the gap. The trim must NOT cut it (the word's
    # audio survives so the sentence still makes sense — the exact freestyle complaint).
    words = [_w("hello", 0, 600), _w("world", 1600, 1800)]
    _, drops = strip_fillers(words, gap_ms=300, keep_pause_frames=6, silent_spans=[])
    assert not any(d.reason == "dead_air" for d in drops)


def test_dead_air_unchanged_when_no_silence_measurement():
    # silent_spans=None (no ffmpeg / unfetchable) → prior timestamp-only behavior.
    words = [_w("hello", 0, 600), _w("world", 1600, 1800)]
    _, with_none = strip_fillers(words, gap_ms=300, keep_pause_frames=6, silent_spans=None)
    _, without = strip_fillers(words, gap_ms=300, keep_pause_frames=6)
    assert [d.reason for d in with_none] == [d.reason for d in without]
    assert any(d.reason == "dead_air" for d in with_none)


# ---------------------------------------------------------------------------
# v2 is a strict superset of v1's catches
# ---------------------------------------------------------------------------

def test_strip_fillers_v2_superset_of_v1_drops():
    words = [_w("um", 0, 100, type="filler"), _w("You", 150, 300), _w("know", 300, 500),
             _w("this", 500, 650), _w("works", 650, 900)]
    _, v1_drops = strip_fillers(words)
    _, v2_drops = strip_fillers_v2(words, "default")
    assert any(d.reason == "filler" for d in v1_drops)   # "um" caught by both
    assert len(v2_drops) > len(v1_drops)                 # v2 ALSO catches "you know"
    assert _covered(v2_drops, 150, 500, reason="filler")
