"""Sentence segmentation and retake clustering (studio.perception.takes) — keyless, offline."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from studio.perception.index import TakeIndex, Word
from studio.perception.takes import (
    TakeParams,
    analyze_takes,
    cluster_takes,
    line_similarity,
    segment_sentences,
)
from studio.perception.transcribe import RawToken, build_words, parse_elevenlabs

HERE = Path(__file__).parent
REF = HERE / "takes_multitake_retake_join_words.json"
SCRIBE_D030 = HERE / "transcribe_scribe_v2_d030.json"


# ---------------------------------------------------------------------------------------------- helpers
def mk(script: str, *, speaker: str | None = "S1", t0_ms: int = 200, gap_ms: int = 60) -> list[Word]:
    """Words from a compact script. ``[600]`` = a 600 ms pause before the next token; ``@S2`` switches speaker;
    ``(laughter)`` tokens become events. Kinds come from the real classifier in ``build_words``."""
    toks: list[RawToken] = []
    t = t0_ms * 1000
    pending_gap = gap_ms
    spk = speaker
    for tok in script.split():
        m = re.fullmatch(r"\[(\d+)\]", tok)
        if m:
            pending_gap = int(m.group(1))
            continue
        if tok.startswith("@"):
            spk = tok[1:]
            continue
        if toks:
            t += pending_gap * 1000
        pending_gap = gap_ms
        dur = (120 + 35 * len(tok.strip(".,!?-—"))) * 1000
        toks.append(RawToken(text=tok, start_us=t, end_us=t + dur, speaker=spk, order=len(toks)))
        t += dur
    words, _ = build_words(toks)
    return words


def texts(sentences):
    return [s.text for s in sentences]


def analyze(script: str, **kw):
    return analyze_takes(mk(script, **kw))


def load_reference(refined: bool) -> list[Word]:
    """The prod multitake transcript (verbatim paragraph retake + "months— people will buy." false start).
    ``refined`` emulates acoustic boundary refinement: word edges are pulled out of the measured silences."""
    d = json.loads(REF.read_text())
    toks = []
    for k, (text, s, e, sp) in enumerate(d["words"]):
        s, e = s * 1000, e * 1000
        if refined:
            for a, b in d["silent_spans_ms"]:
                a, b = a * 1000, b * 1000
                if a <= s < b:
                    s = b
                if a < e <= b:
                    e = a
            e = max(e, s)
        toks.append(RawToken(text=text, start_us=s, end_us=e, speaker=sp, order=k))
    words, _ = build_words(toks)
    return words


# ---------------------------------------------------------------------------------------------- fixture index
def test_hand_built_index_is_reproduced(take_index: TakeIndex):
    bare = [w.model_copy(update={"sentence_id": None, "cluster_id": None}) for w in take_index.words]
    words, sentences, clusters = analyze_takes(bare)
    assert texts(sentences) == texts(take_index.sentences)
    assert [s.complete for s in sentences] == [s.complete for s in take_index.sentences]
    assert [s.word_ids for s in sentences] == [s.word_ids for s in take_index.sentences]
    assert len(clusters) == 1
    c = clusters[0]
    assert c.id == "c01" and c.sentence_ids == ["s002", "s003"] and c.recommended_sentence_id == "s003"
    assert "false start" in c.notes
    assert {w.cluster_id for w in words if w.sentence_id in ("s002", "s003")} == {"c01"}
    assert all(w.cluster_id is None for w in words if w.sentence_id not in ("s002", "s003"))

    ix = take_index.model_copy(update={"words": words, "sentences": sentences, "clusters": clusters})
    ix = TakeIndex.model_validate(ix.model_dump())
    out = ix.render_transcript("full")
    assert "c01 take 2/2, recommended" in out and "incomplete" in out


# ---------------------------------------------------------------------------------------------- real multitake
@pytest.mark.parametrize("refined", [False, True])
def test_reference_multitake_retakes_false_start_and_pickup(refined: bool):
    words, sentences, clusters = analyze_takes(load_reference(refined))
    by_text = {s.text: s for s in sentences}

    false_start = by_text["So founders spend months—"]
    pickup = by_text["people will buy."]
    full_belief = next(s for s in sentences if s.text.startswith("The belief is"))
    full_founders = next(s for s in sentences if s.text.startswith("So founders spend months worshipping"))
    assert not false_start.complete
    assert pickup.complete and full_belief.complete and full_founders.complete

    outcome = [s for s in sentences if s.text.startswith("They buy the outcome")]
    frame = [s for s in sentences if s.text.startswith("Your story is just the frame")]
    assert len(outcome) == 2 and len(frame) == 2

    got = {tuple(c.sentence_ids): c for c in clusters}
    expected = {
        (full_belief.id, pickup.id): full_belief.id,  # pickup of the tail: the full line stays recommended
        (false_start.id, full_founders.id): full_founders.id,  # false start → the re-delivery
        (outcome[0].id, outcome[1].id): outcome[1].id,  # verbatim paragraph retake → last complete take
        (frame[0].id, frame[1].id): frame[1].id,
    }
    assert set(got) == set(expected)
    for ids, rec in expected.items():
        assert got[ids].recommended_sentence_id == rec

    pick = got[(full_belief.id, pickup.id)]
    assert "pickup" in pick.notes and f"choose_take({pickup.id})" in pick.notes
    # the splice hint names the word just before the re-said tail ("you," = w0027)
    assert f"keep {full_belief.id} through w0027 then {pickup.id}" in pick.notes
    # the repeated two-sentence passage is flagged; the pickup/false-start pair is not a "passage"
    assert "Passage with" in got[(outcome[0].id, outcome[1].id)].notes
    assert "Passage with" in got[(frame[0].id, frame[1].id)].notes
    assert "Passage with" not in pick.notes
    assert "Passage with" not in got[(false_start.id, full_founders.id)].notes
    # the final unpunctuated line is not called incomplete (no evidence of abandonment)
    assert sentences[-1].text.endswith("different truth") and sentences[-1].complete

    # the Director's intended edit (choose the recommended take of every cluster) reads cleanly
    drop = {sid for c in clusters for sid in c.sentence_ids if sid != c.recommended_sentence_id}
    kept = " ".join(s.text for s in sentences if s.id not in drop)
    assert "true to you, people will buy. So founders spend months worshipping" in kept
    assert kept.count("They buy the outcome") == 1 and kept.count("frame you hang") == 1


def test_scribe_d030_clean_read_has_no_clusters():
    words, _ = parse_elevenlabs(json.loads(SCRIBE_D030.read_text()))
    words, sentences, clusters = analyze_takes(words)
    assert clusters == []
    assert len(sentences) == 10 and all(s.complete for s in sentences)
    assert sentences[1].text == "Everyone says you need an authentic brand story."
    assert all(w.sentence_id for w in words)


# ---------------------------------------------------------------------------------------------- restarts
def test_restart_without_dash_is_split_and_clustered():
    _, ss, cs = analyze("So founders spend months so founders spend months worshipping their origin story.")
    assert texts(ss) == ["So founders spend months",
                         "so founders spend months worshipping their origin story."]
    assert [s.complete for s in ss] == [False, True]
    assert len(cs) == 1 and cs[0].recommended_sentence_id == "s002" and "false start" in cs[0].notes


def test_cutoff_restart_after_silence():
    _, ss, cs = analyze("The real secret is restr- [900] the real secret is restraint.")
    assert texts(ss) == ["The real secret is restr-", "the real secret is restraint."]
    assert [s.complete for s in ss] == [False, True]
    assert cs[0].sentence_ids == ["s001", "s002"] and cs[0].recommended_sentence_id == "s002"


def test_repeated_restarts_form_one_cluster():
    _, ss, cs = analyze("So the, so the, so the answer is simple.")
    assert texts(ss) == ["So the,", "so the,", "so the answer is simple."]
    assert [s.complete for s in ss] == [False, False, True]
    assert len(cs) == 1 and cs[0].sentence_ids == ["s001", "s002", "s003"]
    assert cs[0].recommended_sentence_id == "s003"


def test_filler_between_attempt_and_restart_leaves_with_the_attempt():
    ws, ss, cs = analyze("We need to start, um, [400] we need to start today.")
    assert texts(ss) == ["We need to start, um,", "we need to start today."]
    assert ws[4].kind == "filler" and ws[4].sentence_id == "s001"
    assert cs[0].recommended_sentence_id == "s002"


@pytest.mark.parametrize("script", [
    "Buy it now, buy it now!",  # deliberate repeat, not extended
    "It's very, very good.",
    "I think that the product is great and I think that you should buy it.",  # parallel clauses
    "We fight on the beaches, [300] we fight on the landing grounds.",  # anaphora with a normal comma pause
    "We need to b- build it.",  # a stutter inside the line
    "The key— and this matters— is timing.",  # parenthetical dashes
])
def test_not_a_restart(script: str):
    _, ss, cs = analyze(script)
    assert len(ss) == 1 and ss[0].complete
    assert cs == []


def test_leading_stutter_merges_into_its_line():
    ws, ss, cs = analyze("I- I think we should go.")
    assert texts(ss) == ["I- I think we should go."] and ss[0].complete
    assert ws[0].kind == "cutoff" and cs == []


def test_dash_interruption_confirmed_by_long_pause():
    _, ss, cs = analyze("I was going to say— [700] anyway, the point is simple.")
    assert texts(ss) == ["I was going to say—", "anyway, the point is simple."]
    assert [s.complete for s in ss] == [False, True]
    assert cs == []  # nothing re-delivered: incomplete, but not a false start


def test_dash_interruption_confirmed_by_capitalised_restart():
    _, ss, _ = analyze("The thing about growth is— [300] Look, nobody tells you this.")
    assert [s.complete for s in ss] == [False, True]


# ---------------------------------------------------------------------------------------------- deliberate vs retake
def test_short_verbatim_repeat_is_deliberate():
    _, ss, cs = analyze("Two weeks. [400] Two weeks. [400] That is all it took.")
    assert len(ss) == 3 and cs == []


def test_parallel_structure_is_not_a_retake():
    _, ss, cs = analyze("They buy the outcome they want. They buy the identity they want. "
                        "They buy the proof you can deliver.")
    assert len(ss) == 3 and cs == []


def test_anaphora_sentences_are_not_clustered():
    _, _, cs = analyze("We fight on the beaches. We fight on the landing grounds.")
    assert cs == []


def test_extension_is_clustered_with_a_build_up_note():
    _, ss, cs = analyze("Most people fail. [500] Most people fail because they quit too early.")
    assert len(cs) == 1 and cs[0].recommended_sentence_id == "s002"
    assert "build-up" in cs[0].notes
    assert ss[0].complete  # a complete sentence is not relabelled


def test_later_abandoned_attempt_keeps_the_complete_take():
    _, ss, cs = analyze("Follow me for more tips every week. [1000] Follow me for more—")
    assert [s.complete for s in ss] == [True, False]
    assert cs[0].sentence_ids == ["s001", "s002"] and cs[0].recommended_sentence_id == "s001"
    assert "abandoned" in cs[0].notes and "later s002 incomplete" in cs[0].notes


def test_last_complete_take_unless_a_later_one_is_incomplete():
    _, ss, cs = analyze("Here is the real secret to growth. [1200] Here is the real secret to growth. [1200] "
                        "Here is the real—")
    assert [s.complete for s in ss] == [True, True, False]
    assert len(cs) == 1 and cs[0].sentence_ids == ["s001", "s002", "s003"]
    assert cs[0].recommended_sentence_id == "s002"


def test_rephrase_after_a_reset_pause():
    _, _, cs = analyze("The problem is that people never start. [1200] "
                       "The real problem is that most people never start at all.")
    assert len(cs) == 1 and cs[0].recommended_sentence_id == "s002"
    assert "rephrases" in cs[0].notes


def test_close_restatement_clusters_without_a_pause():
    _, _, cs = analyze("The problem is that people never start. "
                       "The real problem is that most people never start at all.")
    assert len(cs) == 1 and cs[0].recommended_sentence_id == "s002"


def test_moderate_rephrase_needs_a_reset_signal():
    script = "You need to post every day. {gap}You need to post every single day without fail."
    assert analyze(script.format(gap=""))[2] == []
    _, _, cs = analyze(script.format(gap="[1200] "))
    assert len(cs) == 1 and "rephrases" in cs[0].notes


@pytest.mark.parametrize("a,b", [
    ("People never start because they fear failure.", "People never finish because they fear success."),
    ("My first business failed in six months.", "My second business failed in six weeks."),
    ("The best time to post is in the morning.", "The worst time to post is late at night."),
    ("If it is real, people will buy.", "If it is fake, people will still buy."),
])
def test_antithesis_is_not_a_rephrase_even_after_a_pause(a: str, b: str):
    assert analyze(f"{a} [1200] {b}")[2] == []


def test_distant_repeat_is_not_a_retake():
    filler_lines = ("Cameras got cheaper every year. Editing apps followed. Nobody talks about lighting. "
                    "Sound matters more than pixels. Hooks decide the first second. Most viewers never unmute. "
                    "Captions carry the story. Retention graphs tell the truth. Posting time barely matters.")
    _, _, cs = analyze("The real secret is restraint and patience. " + filler_lines
                       + " The real secret is restraint and patience.")
    assert cs == []


def test_whole_script_recorded_twice():
    script = ("Cameras got cheaper every single year for a decade. Editing apps followed right behind them. "
              "Nobody talks about lighting even though it matters most. Sound quality matters far more than pixels. "
              "Hooks decide what happens in the first second. Most viewers never unmute a video at all. "
              "Captions carry the whole story for them. Retention graphs always tell you the truth. "
              "Posting time barely matters compared to the idea itself. Follow for more.")
    _, ss, cs = analyze(script + " [2500] " + script)
    assert len(ss) == 20
    long_lines = [i for i in range(10) if len(ss[i].word_ids) >= 6]
    assert len(cs) == len(long_lines)  # "Follow for more." and short lines are not called retakes
    for c in cs:
        a, b = c.sentence_ids
        assert int(b[1:]) - int(a[1:]) == 10 and c.recommended_sentence_id == b
        assert "second pass" in c.notes and "Passage with" in c.notes


def test_different_speakers_never_cluster():
    _, ss, cs = analyze("Here is the real secret to growth. [1000] @S2 Here is the real secret to growth.")
    assert [s.speaker for s in ss] == ["S1", "S2"]
    assert cs == []


# ---------------------------------------------------------------------------------------------- boundaries
def test_fragment_before_a_long_silence_then_redelivered():
    _, ss, cs = analyze("And the winner is [2500] and the winner is nobody at all.")
    assert texts(ss) == ["And the winner is", "and the winner is nobody at all."]
    assert [s.complete for s in ss] == [False, True]
    assert cs[0].recommended_sentence_id == "s002" and "false start" in cs[0].notes


def test_speaker_change_and_hard_pause_split():
    _, ss, _ = analyze("and the winner is [2500] nobody at all @S2 really")
    assert texts(ss) == ["and the winner is", "nobody at all", "really"]
    assert all(s.complete for s in ss)


def test_abbreviations_do_not_split():
    _, ss, _ = analyze("Dr. Smith said hi to Mr. Jones. [300] I like apples, pears, etc. [300] Then we left.")
    assert texts(ss) == ["Dr. Smith said hi to Mr. Jones.", "I like apples, pears, etc.", "Then we left."]


def test_ellipsis_only_ends_before_capital_or_long_pause():
    _, ss, _ = analyze("And then... we won. [200] It was over... [900] everyone left.")
    assert texts(ss) == ["And then... we won.", "It was over...", "everyone left."]


def test_filler_only_sentence_joins_the_next_line():
    ws, ss, _ = analyze("Um. [400] So the answer is yes.")
    assert texts(ss) == ["Um. So the answer is yes."]
    assert ws[0].kind == "filler" and ws[0].sentence_id == "s001"


def test_events_attach_to_the_nearer_sentence():
    words = mk("That was great. (laughter) [900] Anyway, moving on.")
    ws, ss, _ = analyze_takes(words)
    ev = next(w for w in ws if w.kind == "event")
    assert ev.sentence_id == "s001" and ss[0].text == "That was great. (laughter)"
    words = mk("That was great. [900] (laughter) Anyway, moving on.")
    ws, ss, _ = analyze_takes(words)
    ev = next(w for w in ws if w.kind == "event")
    assert ev.sentence_id == "s002"
    # sentences stay contiguous runs of the word list
    ids = [w.id for w in ws]
    for s in ss:
        a = ids.index(s.word_ids[0])
        assert s.word_ids == ids[a:a + len(s.word_ids)]


def test_event_only_and_empty_inputs():
    assert segment_sentences([]) == ([], [])
    assert cluster_takes([], []) == ([], [], [])
    words = mk("(music)")
    ws, ss = segment_sentences(words)
    assert ss == [] and ws[0].sentence_id is None


def test_every_word_is_assigned_and_ids_are_ordered():
    ws, ss, cs = analyze_takes(load_reference(False))
    assert all(w.sentence_id for w in ws)
    assert [s.id for s in ss] == [f"s{i:03d}" for i in range(1, len(ss) + 1)]
    assert [c.id for c in cs] == [f"c{i:02d}" for i in range(1, len(cs) + 1)]
    assert sum(len(s.word_ids) for s in ss) == len(ws)
    for c in cs:
        assert 0.0 <= c.similarity <= 1.0 and c.recommended_sentence_id in c.sentence_ids


def test_params_override():
    script = "and the winner is [1500] nobody."
    assert len(analyze(script)[1]) == 1
    ws, ss = segment_sentences(mk(script), params=TakeParams(hard_pause_ms=1000))
    assert len(ss) == 2


def test_line_similarity():
    char, content = line_similarity(["they", "buy", "the", "outcome"], ["they", "buy", "the", "identity"])
    assert char < 0.85 and content < 0.7
    assert line_similarity(["a", "b"], ["a", "b"]) == (1.0, 1.0)
    assert line_similarity([], ["x"]) == (0.0, 0.0)


def test_inputs_are_not_mutated():
    words = mk("So the, so the answer is simple.")
    before = [w.model_dump() for w in words]
    analyze_takes(words)
    assert [w.model_dump() for w in words] == before


def test_scribe_multitake_trailing_false_start_and_pickup():
    """Real Scribe v2 transcript of qa-editor-var-multitake.mov: "…true to you, people will buy. So founders
    spend months... [0.9 s] People will buy. So founders spend months worshiping…". The ellipsis line is a
    false start and "People will buy." a pickup of the belief line's tail (it was left unclustered, so the
    duplicate could survive the cut); the repeated paragraph keeps its last complete take."""
    d = json.loads((HERE / "takes_multitake_scribe_words.json").read_text())
    toks = [RawToken(text=t, start_us=s, end_us=e, speaker=sp, order=k) for k, (t, s, e, sp) in enumerate(d["words"])]
    words, _ = build_words(toks)
    words, sentences, clusters = analyze_takes(words)
    by = {s.text: s for s in sentences}
    belief = next(s for s in sentences if s.text.startswith("The belief is"))
    pickup = by["People will buy."]
    false_start = by["So founders spend months..."]
    full = next(s for s in sentences if s.text.startswith("So founders spend months worshiping"))
    assert not false_start.complete
    got = {tuple(c.sentence_ids): c.recommended_sentence_id for c in clusters}
    assert got[(belief.id, pickup.id)] == belief.id  # the pickup is clustered; the full line stays recommended
    assert got[(false_start.id, full.id)] == full.id
    outcome = [s for s in sentences if s.text.startswith("They buy the outcome")]
    assert len(outcome) == 2 and got[(outcome[0].id, outcome[1].id)] == outcome[1].id  # last complete take
    frame = [s for s in sentences if s.text.startswith("Your story is just the frame")]
    assert got[(frame[0].id, frame[1].id)] == frame[1].id
    # the first pass broke off in "proof..." and the whole passage was re-delivered: keep the second pass whole
    notes = {c.id: c.notes for c in clusters}
    c_out = next(c for c in clusters if outcome[0].id in c.sentence_ids)
    assert "first pass abandoned" in c_out.notes and "keep the second pass whole" in c_out.notes
    assert sum("keep the second pass whole" in n for n in notes.values()) == 2
    drop = {sid for c in clusters for sid in c.sentence_ids if sid != c.recommended_sentence_id}
    kept = " ".join(s.text for s in sentences if s.id not in drop)
    assert kept.count("people will buy") + kept.count("People will buy") == 1
    assert kept.count("They buy the outcome") == 1 and kept.count("frame you hang") == 1


def test_a_phrase_restarted_mid_sentence_after_a_reset_is_a_false_start():
    # real-take40: the list marker stays, the abandoned phrase clusters with its restart
    words, sents, clusters = analyze("One, [380] do the two cuisines, [1250] do the two cuisines share a base fat "
                                     "or a base acid?")
    assert texts(sents) == ["One,", "do the two cuisines,", "do the two cuisines share a base fat or a base acid?"]
    assert [s.complete for s in sents] == [True, False, True]
    assert len(clusters) == 1 and clusters[0].sentence_ids == [sents[1].id, sents[2].id]
    assert clusters[0].recommended_sentence_id == sents[2].id
    # anaphora (a short, rhetorical beat between the repeats) is not a restart
    _w, sents2, cl2 = analyze("we shall fight on the beaches, [250] we shall fight on the landing grounds.")
    assert len(sents2) == 1 and not cl2
