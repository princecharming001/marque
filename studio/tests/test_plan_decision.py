"""Tests for studio.plan.decision: the PlanDecision models, the compact schema string and every validate_decision
guard with a passing and a failing case. Keyless and offline on the hand-built fixture index (conftest):
s001 hook; s002 false start (c01 take 1, incomplete, ends in ``restr-``); s003 retake (c01 rec); s004 opens with
the filler ``Um``; s005 the spoken CTA ("follow me")."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from studio.perception.index import Cluster, TakeIndex
from studio.plan.decision import (
    CTA_LEXICON,
    MAX_DROP_SHARE,
    MAX_EMPHASIS_WORDS,
    PLAN_SCHEMA_JSON,
    PlanDecision,
    PunchIn,
    SentenceDrop,
    StructureMarker,
    TakeChoice,
    TextFix,
    WordRange,
    is_cta_sentence,
    kept_word_ids,
    validate_decision,
)

BASE = {"style_primary": "explainer", "goal": "teach restraint in one line", "hook_promise": "most people over-cut"}


def decision(**over: object) -> PlanDecision:
    return PlanDecision(**{**BASE, **over})  # type: ignore[arg-type]


def rng(a: str, b: str | None = None) -> WordRange:
    return WordRange(from_word=a, to_word=b or a)


def drops(*pairs: tuple[str, str]) -> list[SentenceDrop]:
    return [SentenceDrop(sentence_id=s, reason=r) for s, r in pairs]  # type: ignore[arg-type]


def rejections(ix: TakeIndex, needle: str, **over: object) -> list[str]:
    return [r for r in validate_decision(decision(**over), ix) if needle in r]


# ============================================================================================ models + schema
def test_minimal_decision_and_defaults() -> None:
    d = decision()
    assert d.style_secondary is None and d.payoff is None and d.cta is None and d.punch_in is None
    assert d.drop_sentences == [] and d.must_keep == [] and d.caption_emphasis_word_ids == [] and d.notes == ""
    full = decision(
        style_secondary="founder_brand", drop_sentences=drops(("s002", "repeat")),
        choose_take=[TakeChoice(cluster_id="c01", sentence_id="s003")], payoff=rng("w0018"),
        cta=rng("w0032", "w0033"), must_keep=[rng("w0020", "w0028")], deliberate_beat_gap_ids=["g0006"],
        cut_filler_word_ids=["w0019"], punch_in=PunchIn(anchor_word="w0014", reason="lean in on the payoff line"),
        caption_emphasis_word_ids=["w0018", "w0024"], caption_text_fixes=[TextFix(word_id="w0041", text="week")],
        structure_markers=[StructureMarker(anchor_from_word="w0020", anchor_to_word="w0028", label="Step 1")],
        notes="keep the breath after restraint",
    )
    assert PlanDecision.model_validate_json(full.model_dump_json()) == full


def test_models_reject_unknown_fields_bad_reasons_and_a_list_of_punch_ins() -> None:
    with pytest.raises(ValidationError):
        PlanDecision(**BASE, drop_sentence_ids=["s001"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SentenceDrop(sentence_id="s001", reason="boring")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        decision(punch_in=[PunchIn(anchor_word="w0014", reason="a"), PunchIn(anchor_word="w0020", reason="b")])
    with pytest.raises(ValidationError):
        decision(payoff={"from_word": "w0018", "to_word": "w0018", "at_s": 5.7})  # no model timestamps
    with pytest.raises(ValidationError):
        PlanDecision(style_primary="x", goal="y")  # type: ignore[call-arg]  # hook_promise is required


def test_schema_is_compact_and_keeps_the_enums() -> None:
    assert len(PLAN_SCHEMA_JSON) <= 3000
    schema = json.loads(PLAN_SCHEMA_JSON)
    assert "title" not in PLAN_SCHEMA_JSON and "description" not in PLAN_SCHEMA_JSON
    assert schema["$defs"]["SentenceDrop"]["properties"]["reason"]["enum"] == [
        "preamble", "tangent", "after_payoff", "unfinished", "repeat", "repeats_hook"]
    assert set(schema["required"]) == {"style_primary", "goal", "hook_promise"}
    assert set(schema["properties"]) == set(PlanDecision.model_fields)
    assert schema["additionalProperties"] is False
    # no grammar features Anthropic's structured outputs reject
    for bad in ("pattern", "maxItems", "minItems", "maxLength", "minLength", "format"):
        assert f'"{bad}"' not in PLAN_SCHEMA_JSON


# ============================================================================================ guards
def test_a_sound_decision_passes(take_index: TakeIndex) -> None:
    d = decision(
        drop_sentences=drops(("s002", "repeat")), choose_take=[TakeChoice(cluster_id="c01", sentence_id="s003")],
        payoff=rng("w0018"), cta=rng("w0032", "w0033"), must_keep=[rng("w0020", "w0028")],
        deliberate_beat_gap_ids=["g0006"], cut_filler_word_ids=["w0019"],
        punch_in=PunchIn(anchor_word="w0014", reason="lean in"), caption_emphasis_word_ids=["w0018", "w0024"],
        caption_text_fixes=[TextFix(word_id="w0041", text="week")],
        structure_markers=[StructureMarker(anchor_from_word="w0020", anchor_to_word="w0028", label="1/1")],
    )
    assert validate_decision(d, take_index) == []
    assert validate_decision(decision(), take_index) == []  # nothing decided is valid too


def test_every_id_must_exist(take_index: TakeIndex) -> None:
    ix = take_index
    assert rejections(ix, "s999", drop_sentences=drops(("s999", "tangent")))
    assert not rejections(ix, "s004", drop_sentences=drops(("s004", "tangent")))
    assert rejections(ix, "c02 is not a retake cluster", choose_take=[TakeChoice(cluster_id="c02", sentence_id="s003")])
    assert rejections(ix, "s001 is not a take of c01", choose_take=[TakeChoice(cluster_id="c01", sentence_id="s001")])
    assert not rejections(ix, "choose_take", choose_take=[TakeChoice(cluster_id="c01", sentence_id="s002")])
    assert rejections(ix, "payoff: w9999", payoff=rng("w9999"))
    assert rejections(ix, "cta: w9999", cta=rng("w0032", "w9999"))
    assert rejections(ix, "must_keep: w9999", must_keep=[rng("w9999")])
    assert rejections(ix, "deliberate_beat_gap_ids: g9999", deliberate_beat_gap_ids=["g9999"])
    assert not rejections(ix, "deliberate_beat_gap_ids", deliberate_beat_gap_ids=["g0006"])
    assert rejections(ix, "cut_filler_word_ids: w9999", cut_filler_word_ids=["w9999"])
    assert rejections(ix, "punch_in: w9999", punch_in=PunchIn(anchor_word="w9999", reason="x"))
    assert rejections(ix, "caption_emphasis_word_ids: w9999", caption_emphasis_word_ids=["w9999"])
    assert rejections(ix, "caption_text_fixes: w9999", caption_text_fixes=[TextFix(word_id="w9999", text="x")])
    assert rejections(ix, "structure_markers: w9999",
                      structure_markers=[StructureMarker(anchor_from_word="w0001", anchor_to_word="w9999", label="1")])
    # one rejection per missing ID, each naming its field
    out = validate_decision(decision(payoff=rng("w9998", "w9999"), caption_emphasis_word_ids=["w9997"]), ix)
    assert len(out) == 3 and all(r.split(":")[0] in ("payoff", "caption_emphasis_word_ids") for r in out)


def test_ranges_must_run_forward(take_index: TakeIndex) -> None:
    assert rejections(take_index, "reversed", payoff=rng("w0018", "w0014"))
    assert not rejections(take_index, "reversed", payoff=rng("w0014", "w0018"))
    assert rejections(take_index, "structure_markers: range w0028-w0020 is reversed",
                      structure_markers=[StructureMarker(anchor_from_word="w0028", anchor_to_word="w0020", label="2")])


def test_drops_never_remove_more_than_half_the_spoken_words(take_index: TakeIndex) -> None:
    # 41 spoken words: s001 8, s002 5, s003 5, s004 10, s005 13
    assert MAX_DROP_SHARE == 0.5
    ok = rejections(take_index, "50%", drop_sentences=drops(("s001", "preamble"), ("s004", "tangent")))  # 18/41
    assert ok == []
    bad = rejections(take_index, "50%", drop_sentences=drops(("s001", "preamble"), ("s003", "tangent"),
                                                              ("s004", "tangent")))  # 23/41
    assert len(bad) == 1 and "23 of 41 spoken words (56%)" in bad[0]
    # exactly half is allowed (the guard is "more than")
    words = list(take_index.words)
    i = take_index.word_pos("w0041")
    words[i] = words[i].model_copy(update={"kind": "event", "text": "(laughs)"})  # 40 spoken words
    ix = take_index.model_copy(update={"words": words})
    assert rejections(ix, "50%", drop_sentences=drops(("s004", "tangent"), ("s003", "tangent"),
                                                      ("s002", "unfinished"))) == []  # 10+5+5 = 20 of 40
    assert rejections(ix, "50%", drop_sentences=drops(("s004", "tangent"), ("s003", "tangent"),
                                                      ("s002", "unfinished"), ("s001", "preamble")))  # 28 of 40


def test_a_repeat_drop_needs_a_later_kept_take_in_its_cluster(take_index: TakeIndex) -> None:
    ix = take_index
    assert rejections(ix, "reason repeat", drop_sentences=drops(("s002", "repeat"))) == []  # s003 follows in c01
    bad = rejections(ix, "reason repeat", drop_sentences=drops(("s003", "repeat")))  # the last take of c01
    assert len(bad) == 1 and "(c01)" in bad[0]
    bad = rejections(ix, "reason repeat", drop_sentences=drops(("s004", "repeat")))  # no cluster at all
    assert len(bad) == 1 and "in no cluster" in bad[0]
    # dropping both takes: the later one is not kept, so the earlier repeat has nothing to point to
    bad = rejections(ix, "reason repeat", drop_sentences=drops(("s002", "repeat"), ("s003", "unfinished")))
    assert len(bad) == 1 and bad[0].startswith("drop_sentences: s002")
    # the decision keeps the earlier take: the later one is then the repeat (redundant with choose_take, coherent)
    assert rejections(ix, "reason repeat", drop_sentences=drops(("s003", "repeat")),
                      choose_take=[TakeChoice(cluster_id="c01", sentence_id="s002")]) == []
    # the other reasons need no cluster evidence
    assert rejections(ix, "reason", drop_sentences=drops(("s004", "tangent"), ("s001", "preamble"))) == []


def test_a_repeats_hook_drop_needs_the_stated_hook_it_repeats(take_index: TakeIndex) -> None:
    ix = take_index
    assert rejections(ix, "repeats_hook", drop_sentences=drops(("s004", "repeats_hook"))) == []
    assert rejections(ix, "repeats_hook", hook_promise="   ", drop_sentences=drops(("s004", "repeats_hook")))
    # the first sentence cannot repeat a hook that comes before it
    assert rejections(ix, "repeats_hook", drop_sentences=drops(("s001", "repeats_hook")))
    # ... unless everything before it is dropped too: then nothing kept carries the hook
    assert rejections(ix, "repeats_hook", drop_sentences=drops(("s001", "preamble"), ("s003", "repeats_hook")))
    # cluster evidence also satisfies it (a later retake exists)
    assert rejections(ix, "repeats_hook", hook_promise="", drop_sentences=drops(("s002", "repeats_hook"))) == []


def test_payoff_cta_and_must_keep_lie_inside_kept_words(take_index: TakeIndex) -> None:
    ix = take_index
    assert rejections(ix, "kept words", payoff=rng("w0018"), cta=rng("w0032", "w0033")) == []
    # the false start (c01 take 1) is not kept by default: a payoff in it is rejected
    bad = rejections(ix, "kept words", payoff=rng("w0013"))
    assert len(bad) == 1 and bad[0].startswith("payoff: w0013-w0013") and "dropped s002" in bad[0]
    # choosing that take makes it kept (and drops s003 instead)
    assert rejections(ix, "kept words", payoff=rng("w0013"),
                      choose_take=[TakeChoice(cluster_id="c01", sentence_id="s002")]) == []
    assert rejections(ix, "kept words", payoff=rng("w0018"),
                      choose_take=[TakeChoice(cluster_id="c01", sentence_id="s002")])
    # a range that crosses into a dropped sentence
    bad = rejections(ix, "kept words", must_keep=[rng("w0020", "w0030")], drop_sentences=drops(("s004", "tangent")))
    assert len(bad) == 1 and "w0020 and 8 more in dropped s004" in bad[0]
    bad = rejections(ix, "kept words", cta=rng("w0032", "w0033"), drop_sentences=drops(("s005", "repeat")))
    assert len(bad) == 1 and bad[0].startswith("cta:")


def test_the_spoken_cta_is_never_dropped_unless_as_a_repeat(take_index: TakeIndex) -> None:
    ix = take_index
    for reason in ("preamble", "tangent", "after_payoff", "unfinished", "repeats_hook"):
        bad = rejections(ix, "call to action", drop_sentences=drops(("s005", reason)))
        assert len(bad) == 1 and "follow me" in bad[0], reason
    assert rejections(ix, "call to action", drop_sentences=drops(("s005", "repeat"))) == []
    assert rejections(ix, "call to action", drop_sentences=drops(("s001", "preamble"), ("s004", "tangent"))) == []
    # a retake of the CTA: clustered, the earlier one dropped as repeat is fine end to end
    sents = [s.model_copy(update={"cluster_id": "c02"}) if s.id in ("s004", "s005") else s for s in ix.sentences]
    words = [w.model_copy(update={"cluster_id": "c02"}) if w.sentence_id in ("s004", "s005") else w for w in ix.words]
    sents[3] = sents[3].model_copy(update={"text": "Follow me, um, for more tips."})
    ix2 = ix.model_copy(update={"sentences": sents, "words": words, "clusters": [
        *ix.clusters, Cluster(id="c02", sentence_ids=["s004", "s005"], recommended_sentence_id="s005")]})
    assert validate_decision(decision(drop_sentences=drops(("s004", "repeat"))), ix2) == []
    assert rejections(ix2, "call to action", drop_sentences=drops(("s004", "tangent")))


def test_is_cta_sentence_lexicon() -> None:
    assert len(CTA_LEXICON) >= 10
    for text in ("If this helped, follow me for more.", "Comment below.", "The link is in my bio.", "Part two drops "
                 "Friday.", "Save this for later!", "Check out the next video.", "Turn on post notifications",
                 "Subscribe, like and share."):
        assert is_cta_sentence(text), text
    for text in ("Most people cut their videos way too much.", "The real secret is restraint.", "They buy the outcome.",
                 "Market share fell.", "I followed the recipe."):
        assert not is_cta_sentence(text), text


def test_filler_cuts_only_name_fillers_or_events(take_index: TakeIndex) -> None:
    assert rejections(take_index, "not a filler", cut_filler_word_ids=["w0019"]) == []
    bad = rejections(take_index, "not a filler", cut_filler_word_ids=["w0020"])
    assert len(bad) == 1 and '"keep" is a spoken word' in bad[0]


def test_at_most_one_punch_in(take_index: TakeIndex) -> None:
    assert validate_decision(decision(punch_in=PunchIn(anchor_word="w0014", reason="lean in")), take_index) == []
    with pytest.raises(ValidationError):
        decision(punch_in=[PunchIn(anchor_word="w0014", reason="a")])
    punch = json.loads(PLAN_SCHEMA_JSON)["properties"]["punch_in"]
    assert punch["anyOf"] == [{"$ref": "#/$defs/PunchIn"}, {"type": "null"}]  # one object or none, never an array


def test_at_most_eight_emphasis_words(take_index: TakeIndex) -> None:
    assert MAX_EMPHASIS_WORDS == 8
    eight = [f"w00{n:02d}" for n in range(1, 9)]
    assert rejections(take_index, "at most 8", caption_emphasis_word_ids=eight) == []
    bad = rejections(take_index, "at most 8", caption_emphasis_word_ids=[*eight, "w0014"])
    assert len(bad) == 1 and bad[0].startswith("caption_emphasis_word_ids: 9 words")


def test_a_chosen_take_cannot_also_be_dropped(take_index: TakeIndex) -> None:
    bad = rejections(take_index, "also dropped", choose_take=[TakeChoice(cluster_id="c01", sentence_id="s003")],
                     drop_sentences=drops(("s003", "tangent")))
    assert len(bad) == 1
    assert rejections(take_index, "also dropped", choose_take=[TakeChoice(cluster_id="c01", sentence_id="s003")],
                      drop_sentences=drops(("s002", "repeat"))) == []


def test_kept_word_ids(take_index: TakeIndex) -> None:
    ix = take_index
    all_ids = [w.id for w in ix.words]
    kept = kept_word_ids(decision(), ix)
    assert kept == [w for w in all_ids if w not in {"w0009", "w0010", "w0011", "w0012", "w0013"}]  # c01 keeps s003
    kept2 = kept_word_ids(decision(choose_take=[TakeChoice(cluster_id="c01", sentence_id="s002")]), ix)
    assert "w0013" in kept2 and "w0018" not in kept2
    kept3 = kept_word_ids(decision(drop_sentences=drops(("s001", "preamble"), ("s005", "repeat"))), ix)
    assert kept3 == ix.word_ids("w0014", "w0028")
    # an unknown sentence in the drops is ignored here (validate_decision reports it)
    assert kept_word_ids(decision(drop_sentences=drops(("s999", "preamble"))), ix) == kept
    # a cluster without a recommendation keeps its last complete take
    clusters = [c.model_copy(update={"recommended_sentence_id": None}) for c in ix.clusters]
    assert kept_word_ids(decision(), ix.model_copy(update={"clusters": clusters})) == kept


def test_rejections_are_sentences_the_retry_can_act_on(take_index: TakeIndex) -> None:
    out = validate_decision(decision(drop_sentences=drops(("s005", "preamble")), payoff=rng("w0013"),
                                     caption_emphasis_word_ids=["w9999"],
                                     punch_in=PunchIn(anchor_word="w9999", reason="x")), take_index)
    assert len(out) == 4  # CTA dropped; payoff in the unkept take; unknown emphasis word; unknown punch-in anchor
    for r in out:
        field = r.split(":")[0]
        assert field in PlanDecision.model_fields, r
        assert len(r) < 400 and "\n" not in r
