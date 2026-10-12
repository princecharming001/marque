"""Tests for studio.plan.digest (the standard tier's planner digest). Keyless and offline: the hand-built fixture
index from conftest, plus a synthetic long take for the size cap."""

from __future__ import annotations

from fractions import Fraction

import pytest

from studio.perception.index import (
    AudioMetrics,
    Energy,
    Gap,
    Sentence,
    TakeIndex,
    Visual,
    Word,
    gap_id,
    sentence_id,
    word_id,
)
from studio.plan.digest import (
    CHARS_PER_TOKEN,
    INLINE_PAUSE_MIN_MS,
    STANDARD_MAX_OUTPUT_S,
    Digest,
    build_digest,
    estimate_tokens,
)


# ============================================================================================ helpers
def _lines(d: Digest) -> list[str]:
    return d.text.splitlines()


def _sentence_line(d: Digest, sid: str) -> str:
    hits = [ln for ln in _lines(d) if ln.startswith(sid + " ")]
    assert len(hits) == 1, f"{sid}: {hits}"
    return hits[0]


def build_long_index(base: TakeIndex, n_sentences: int, words_per_sentence: int = 8) -> TakeIndex:
    """A valid TakeIndex with many plain sentences (one 400 ms pause after each), reusing the fixture's media."""
    words: list[Word] = []
    gaps: list[Gap] = []
    sentences: list[Sentence] = []
    t = 200_000
    gaps.append(Gap(id="g0000", after_word_id=None, before_word_id="w0001", start_us=0, end_us=t, kind="silence",
                    snap_us=100_000))
    n = 0
    for si in range(1, n_sentences + 1):
        sid = sentence_id(si)
        ids: list[str] = []
        start = t
        for k in range(words_per_sentence):
            n += 1
            wid = word_id(n)
            text = f"word{k}" + ("." if k == words_per_sentence - 1 else "")
            words.append(Word(id=wid, text=text, start_us=t, end_us=t + 240_000, sentence_id=sid, emphasis=0.1))
            ids.append(wid)
            t += 240_000 + 40_000
        sentences.append(Sentence(id=sid, word_ids=ids, text=" ".join(w.text for w in words[-words_per_sentence:]),
                                  start_us=start, end_us=words[-1].end_us))
        g_start = t - 40_000
        t = g_start + 400_000
        gaps.append(Gap(id="g0000", after_word_id=ids[-1], before_word_id=None, start_us=g_start, end_us=t,
                        snap_us=g_start + 200_000))
    for i in range(1, len(gaps) - 1):
        gaps[i] = gaps[i].model_copy(update={"before_word_id": sentences[i].word_ids[0]})
    gaps = [g.model_copy(update={"id": gap_id(k)}) for k, g in enumerate(gaps, start=1)]
    media = base.media.model_copy(update={"duration_us": t, "audio": base.media.audio.model_copy(
        update={"duration_us": t})})
    return TakeIndex(media=media, words=words, sentences=sentences, gaps=gaps, visual=Visual(),
                     audio=AudioMetrics(snr_db=28.0, noise_floor_db=-58.0), energy=Energy(wpm=150.0, overall=0.5))


# ============================================================================================ content
def test_sentence_lines_follow_the_grammar(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    s1 = _sentence_line(d, "s001")
    assert s1.startswith("s001 (0.3-2.7s) w0001 Most w0002 people")
    assert s1.endswith("w0008 much. [g0003 0.70s]")  # the pause after the sentence stays on its line
    assert "[g0002" not in s1  # 180 ms: below the inline threshold
    s2 = _sentence_line(d, "s002")
    assert s2.startswith("s002 [c01 1/2] (3.4-4.8s) [incomplete] w0009 The")
    assert s2.endswith("w0013 restr- [g0004 0.90s silence]")  # cut-off and silence kind
    s3 = _sentence_line(d, "s003")
    assert "[c01 2/2 rec]" in s3 and "[incomplete]" not in s3
    assert "[g0005" not in s3  # 120 ms inside the sentence is not shown
    assert s3.endswith("w0018 restraint. [g0006 0.80s breath]")
    s4 = _sentence_line(d, "s004")
    assert "w0019 {Um} [g0007 0.25s] w0020 keep" in s4  # filler in braces; 250 ms is the inclusive threshold
    s5 = _sentence_line(d, "s005")
    assert "w0031 helped, [g0010 0.30s] w0032 follow" in s5
    assert "[g0011" not in s5  # the trailing silence is not a pause between words
    assert "[g0001" not in d.text  # neither is the leading one
    assert d.sentence_ids == ["s001", "s002", "s003", "s004", "s005"]
    assert d.cluster_recs == {"c01": "s003"}


def test_head_block_names_platform_brief_and_style(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="reels", brief="Teach  restraint\nin editing", style_hint="tutorial")
    lines = _lines(d)
    assert lines[0] == ("Take digest: 16.6 s source, 41 spoken words, 5 sentences, 1 retake cluster(s); "
                        "platform reels.")
    assert lines[1] == "Brief: Teach restraint in editing"  # whitespace normalised
    assert lines[2] == "Style hint: tutorial"
    assert lines[3].startswith("Grammar:") and "{um} filler" in lines[3] and "never times" in lines[3]
    assert lines[4] == "Transcript:"
    plain = build_digest(take_index, platform="tiktok")
    assert not any(ln.startswith(("Brief:", "Style hint:")) for ln in _lines(plain))


def test_measured_block(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    text = d.text
    assert "Retake clusters" in text
    assert "c01: s002 incomplete -> s003; rec s003; similarity 0.92; false start then clean retake" in text
    assert "Energy 0.55 (wpm 158, pitch var 0.42 st, loudness var 0.31 dB)" in text
    # inner pauses >= 250 ms: 700, 900, 800, 250, 600, 300 -> median 650, longest g0004
    assert "pauses >= 250 ms: 6, median 650 ms, longest g0004 900 ms silence" in text
    assert "Audio: SNR 31.0 dB, noise floor -62.0 dBFS, music in room: no, clipping 0.00%" in text
    assert "Fillers: 1 (w0019 {Um})" in text
    assert "Cut-offs: 1 (w0013 restr-)" in text
    assert "Dropout-chopped: none" in text
    emph = next(ln for ln in _lines(d) if ln.strip().startswith("Emphasis top"))
    assert "w0018 restraint. 0.95, w0024 matter 0.70, w0008 much. 0.60, w0032 follow 0.55, w0001 Most 0.40" in emph
    assert emph.count(" w0") == 12
    # the stats the report prints
    s = d.stats
    assert s["wpm"] == 158.0 and s["energy"] == 0.55 and s["pause_median_ms"] == 650.0 and s["snr_db"] == 31.0
    assert s["duration_s"] == pytest.approx(16.566) and s["words"] == 41 and s["fillers"] == 1
    assert s["cutoffs"] == 1 and s["chopped"] == 0 and s["music_in_room"] is False
    assert s["sentences"] == 5 and s["clusters"] == 1 and s["omitted_sentence_ids"] == []
    assert s["chars"] == len(text) and s["tokens_est"] == estimate_tokens(text)


def test_caption_placement_options_are_measured_not_instructed(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    lines = [ln.strip() for ln in _lines(d)]
    head = next(ln for ln in lines if ln.startswith("Caption placement options"))
    assert "88 px one-line block 108 px tall" in head and "chin y806" in head and "relaxed caption floor y1600" in head
    assert "(a) under the chin: top y886, bottom y994: fits the strict band" in lines
    assert "(b) smaller text under the chin: 88 px fits the strict band" in lines
    assert "(c) reframe lifting the chin: not needed; this source stays clean to x1.20" in lines
    assert "(d) above the head: top y200, bottom y308, on the hair" in lines
    assert "auto_captions" not in d.text and "caption_preview" not in d.text  # no Director tool talk


def test_voice_chain_note_comes_from_the_index_and_is_omitted_without_audio(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    voice = next(ln for ln in _lines(d) if ln.strip().startswith("Voice chain"))
    assert "denoise none" in voice and "HPF 80 Hz" in voice and "no denoise: SNR 31.0 dB (index) >= 20" in voice
    noisy = take_index.model_copy(update={"audio": take_index.audio.model_copy(update={"snr_db": 15.0})})
    v2 = next(ln for ln in _lines(build_digest(noisy, platform="tiktok")) if ln.strip().startswith("Voice chain"))
    assert "denoise light" in v2 and "SNR 15.0 dB (index) in 12-20" in v2
    music = take_index.model_copy(update={"audio": take_index.audio.model_copy(update={"music_in_room": True})})
    dm = build_digest(music, platform="tiktok")
    v3 = next(ln for ln in _lines(dm) if ln.strip().startswith("Voice chain"))
    assert "denoise isolate (elevenlabs)" in v3 and "music in room: yes" in dm.text and dm.stats["music_in_room"]
    silent = take_index.model_copy(update={"audio": None})
    ds = build_digest(silent, platform="tiktok")
    assert not any(ln.strip().startswith("Voice chain") for ln in _lines(ds))
    assert "Audio: not measured" in ds.text and ds.stats["snr_db"] is None and ds.stats["music_in_room"] is False


def test_no_colour_line_without_a_free_measurement(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    assert "Colour" not in d.text and "Color" not in d.text and "measure_color" not in d.text


def test_length_window_by_platform_and_style(take_index: TakeIndex) -> None:
    tiktok = next(ln for ln in _lines(build_digest(take_index, platform="tiktok")) if "Length:" in ln)
    assert "platform tiktok (up to 600 s; Creator Rewards" in tiktok
    assert "style window 30-60 s (default)" in tiktok
    assert f"standard tier ships at most {STANDARD_MAX_OUTPUT_S} s" in tiktok and "source 16.6 s" in tiktok
    shorts = next(ln for ln in _lines(build_digest(take_index, platform="shorts", style_hint="Hot take"))
                  if "Length:" in ln)
    assert "platform shorts (up to 180 s" in shorts and "style window 15-45 s (hot_take)" in shorts
    reels = next(ln for ln in _lines(build_digest(take_index, platform="reels", style_hint="listicle"))
                 if "Length:" in ln)
    assert "platform reels (up to 900 s" in reels and "style window 30-75 s (listicle)" in reels
    unknown = next(ln for ln in _lines(build_digest(take_index, platform="tiktok", style_hint="noir western"))
                   if "Length:" in ln)
    assert "style window 30-60 s (default)" in unknown


def test_chopped_words_are_tagged(take_index: TakeIndex) -> None:
    words = list(take_index.words)
    i = take_index.word_pos("w0024")
    words[i] = words[i].model_copy(update={"text": "matt", "kind": "cutoff", "truncated": "end"})
    gaps = list(take_index.gaps)
    k = next(j for j, g in enumerate(gaps) if g.id == "g0009")  # the 600 ms pause after s004 holds a dropout
    gaps[k] = gaps[k].model_copy(update={"dropouts_us": [(gaps[k].start_us, gaps[k].start_us + 300_000)]})
    ix = take_index.model_copy(update={"words": words, "gaps": gaps})
    d = build_digest(ix, platform="tiktok")
    s4 = _sentence_line(d, "s004")
    assert "w0024 matt- [chopped]" in s4
    assert s4.endswith("w0028 rest. [g0009 0.60s DROPOUT 0.30s lost]")
    assert "Dropout-chopped: 1 (w0024 matt-)" in d.text
    assert "Cut-offs: 1 (w0013 restr-)" in d.text  # a plain ASR fragment is counted apart from a dropout chop
    assert d.stats["chopped"] == 1 and d.stats["cutoffs"] == 1
    # the dropout is not one of the speaker's pauses: 5 remain (250, 300, 700, 800, 900 ms), median 700
    assert "pauses >= 250 ms: 5, median 700 ms, longest g0004 900 ms silence" in d.text
    assert d.stats["pause_median_ms"] == 700.0


def test_without_a_face_track_the_placement_line_says_so(take_index: TakeIndex) -> None:
    ix = take_index.model_copy(update={"visual": Visual()})
    d = build_digest(ix, platform="tiktok")
    assert "Caption placement: no face track" in d.text
    assert "(a) under the chin" not in d.text


def test_words_without_a_sentence_form_their_own_line(take_index: TakeIndex) -> None:
    words = [w.model_copy(update={"sentence_id": None}) if w.id in ("w0029", "w0030") else w
             for w in take_index.words]
    sents = [s.model_copy(update={"word_ids": s.word_ids[2:]}) if s.id == "s005" else s for s in take_index.sentences]
    ix = take_index.model_copy(update={"words": words, "sentences": sents})
    d = build_digest(ix, platform="tiktok")
    assert any(ln.startswith("s--- (11.9-") and "w0029 If w0030 this" in ln for ln in _lines(d))
    assert d.sentence_ids == ["s001", "s002", "s003", "s004", "s005"]  # pseudo-groups are not sentences


# ============================================================================================ size cap
def test_token_estimate_is_chars_over_3_5() -> None:
    assert CHARS_PER_TOKEN == 3.5
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 35) == 10
    assert estimate_tokens("a" * 36) == 11
    assert INLINE_PAUSE_MIN_MS == 250.0


def test_default_cap_leaves_a_short_take_whole(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok")
    assert d.stats["tokens_est"] <= 12_000 and "omitted" not in d.text
    assert len(d.sentence_ids) == 5


def test_cap_drops_middle_sentences_first_and_never_the_ends(take_index: TakeIndex) -> None:
    full = build_digest(take_index, platform="tiktok")
    cap = full.stats["tokens_est"] - 4  # just under the whole digest: one sentence must go (its marker is shorter)
    d = build_digest(take_index, platform="tiktok", max_tokens=cap)
    assert d.stats["tokens_est"] <= cap
    assert d.sentence_ids == ["s001", "s002", "s004", "s005"]  # s003 sits in the middle of five
    assert d.stats["omitted_sentence_ids"] == ["s003"]
    assert f"[s003 omitted: 1 sentence, 1.6 s, to fit the {cap}-token digest cap]" in d.text
    assert _sentence_line(d, "s001").startswith("s001 ") and _sentence_line(d, "s005").startswith("s005 ")
    assert "[c01 2/2 rec]" not in d.text  # the dropped line is gone
    assert d.cluster_recs == {"c01": "s003"}  # the recommendation survives in the structured part
    # the marker sits where the sentences were
    lines = _lines(d)
    i3 = next(i for i, ln in enumerate(lines) if ln.startswith("[s003 omitted"))
    assert lines[i3 - 1].startswith("s002 ") and lines[i3 + 1].startswith("s004 ")


def test_cap_keeps_first_and_last_even_when_nothing_fits(take_index: TakeIndex) -> None:
    d = build_digest(take_index, platform="tiktok", max_tokens=10)
    assert d.sentence_ids == ["s001", "s005"]
    assert d.stats["omitted_sentence_ids"] == ["s002", "s003", "s004"]
    assert "[s002-s004 omitted: 3 sentences," in d.text
    assert d.stats["tokens_est"] > 10  # the floor is the ends plus the measured block, documented not hidden


def test_long_take_is_capped_at_12k_tokens_by_default(take_index: TakeIndex) -> None:
    ix = build_long_index(take_index, n_sentences=700)
    assert estimate_tokens(ix.render_transcript("full")) > 12_000  # the raw transcript would not fit
    d = build_digest(ix, platform="tiktok")
    assert d.stats["tokens_est"] <= 12_000
    assert d.sentence_ids[0] == "s001" and d.sentence_ids[-1] == "s700"
    assert 100 < len(d.sentence_ids) < 700
    omitted = d.stats["omitted_sentence_ids"]
    assert omitted and all(sid not in d.sentence_ids for sid in omitted)
    # a single contiguous hole around the middle, one marker
    nums = sorted(int(s[1:]) for s in omitted)
    assert nums == list(range(nums[0], nums[-1] + 1))
    assert nums[0] > 100 and nums[-1] < 600
    assert d.text.count(" omitted: ") == 1
    # the cap is used, not overshot by much: adding the first omitted sentence back would exceed it
    d2 = build_digest(ix, platform="tiktok", max_tokens=d.stats["tokens_est"] + 1)
    assert len(d2.sentence_ids) <= len(d.sentence_ids) + 1


def test_long_take_with_a_large_budget_is_whole(take_index: TakeIndex) -> None:
    ix = build_long_index(take_index, n_sentences=120)
    d = build_digest(ix, platform="tiktok", max_tokens=100_000)
    assert len(d.sentence_ids) == 120 and "omitted" not in d.text
    assert "pauses >= 250 ms: 119, median 400 ms" in d.text
    assert isinstance(ix.media.fps, Fraction)
