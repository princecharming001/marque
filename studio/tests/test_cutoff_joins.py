"""Cut-off words never survive a join.

Regression for the var-silences E2E: at two joins the Director kept words the source cuts off mid-word
("meaningfu-|-nough"), audible as a broken word and a click, and nothing caught it. Words a recording dropout chops
(``Word.truncated``) were already gated; a plain ASR fragment (``kind == "cutoff"``, ``restr-``) at a seam was not.
A join is a seam between segments that are not contiguous in the source, or the story's first/last word; a fragment
inside continuous kept speech stays a judgment call.
"""

from __future__ import annotations

from conftest import build_cut_doc

from studio.compile.timeline import compile as compile_timeline
from studio.doc.model import CutDocument, Segment
from studio.doc.validate import cutoffs_at_joins, validate_document
from studio.perception.index import TakeIndex
from studio.qa import metrics as M


def _cut(ix: TakeIndex, *ids: str, truncated: str | None = None) -> TakeIndex:
    words = [w.model_copy(update={"kind": "cutoff", "truncated": truncated}) if w.id in ids else w for w in ix.words]
    return ix.model_copy(update={"words": words})


def _errs(doc: CutDocument, ix: TakeIndex) -> list[str]:
    return [f.code for f in validate_document(doc, ix) if f.level == "error"]


# ---------------------------------------------------------------------------------------------- document
def test_a_fragment_ending_a_segment_before_a_seam_is_an_error(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0008")  # seg001 ends on it, seg002 starts at w0014: a true seam
    assert cutoffs_at_joins(cut_doc, ix) == [
        {"word_id": "w0008", "text": ix.word("w0008").display(), "seg": "seg001", "side": "end", "other": "w0014"}]
    f = next(f for f in validate_document(cut_doc, ix) if f.code == "cutoff_at_join")
    assert f.level == "error" and f.refs == ["seg001", "w0008"]
    assert "cut_words w0008 w0008" in f.message and "w0007" in f.message


def test_a_fragment_starting_a_segment_after_a_seam_is_an_error(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0014")
    [c] = cutoffs_at_joins(cut_doc, ix)
    assert (c["word_id"], c["side"], c["seg"], c["other"]) == ("w0014", "start", "seg002", "w0008")
    assert "cutoff_at_join" in _errs(cut_doc, ix)


def test_story_edges_are_joins(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0001", "w0041")
    got = [(c["word_id"], c["side"], c["other"]) for c in cutoffs_at_joins(cut_doc, ix)]
    assert got == [("w0001", "start", None), ("w0041", "end", None)]


def test_a_fragment_inside_continuous_speech_is_a_judgment_call(take_index: TakeIndex,
                                                               cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0005")  # mid seg001
    assert cutoffs_at_joins(cut_doc, ix) == []
    assert "cutoff_at_join" not in _errs(cut_doc, ix)


def test_a_contiguous_split_is_not_a_join(take_index: TakeIndex) -> None:
    """A segment split for framing continues the take's own sound: its edge is not a join."""
    ix = _cut(take_index, "w0004")
    doc = build_cut_doc(ix)
    doc.segments[0:1] = [Segment(id="seg001", from_word="w0001", to_word="w0004"),
                         Segment(id="seg005", from_word="w0005", to_word="w0008")]
    assert cutoffs_at_joins(doc, ix) == []
    assert "cutoff_at_join" not in _errs(doc, ix)


def test_a_one_word_segment_is_reported_once(take_index: TakeIndex) -> None:
    ix = _cut(take_index, "w0014")
    doc = build_cut_doc(ix)
    doc.segments[1] = Segment(id="seg002", from_word="w0014", to_word="w0014")
    assert [c["word_id"] for c in cutoffs_at_joins(doc, ix)] == ["w0014"]


def test_cutting_the_fragment_clears_the_error(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    from studio.doc.ops import apply_ops

    ix = _cut(take_index, "w0008")
    doc, res = apply_ops(cut_doc, [{"op": "cut_words", "from_word": "w0008", "to_word": "w0008",
                                    "reason": "cut-off fragment at the join"}], ix)
    assert res[0].applied, res[0].reason
    assert "cutoff_at_join" not in _errs(doc, ix)


# ---------------------------------------------------------------------------------------------- render gate
def test_word_integrity_lists_fragments_at_the_edges_of_audio_runs(take_index: TakeIndex,
                                                                  cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0008", "w0005")
    integ = M.word_integrity(compile_timeline(cut_doc, ix), ix)
    assert [(d["word_id"], d["side"], d["seg"]) for d in integ.cutoff_at_join] == [("w0008", "end", "seg001")]
    assert integ.truncated == []


def test_chopped_words_are_not_double_reported(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    ix = _cut(take_index, "w0008", truncated="end")
    integ = M.word_integrity(compile_timeline(cut_doc, ix), ix)
    assert integ.cutoff_at_join == [] and [d["word_id"] for d in integ.truncated] == ["w0008"]
    assert "cutoff_at_join" in _errs(cut_doc, ix)  # the document check still names it at the join


# ---------------------------------------------------------------------------------------------- Director feedback
def test_the_director_is_blocked_and_told_how_to_fix_it(job, take_index: TakeIndex, cut_doc: CutDocument) -> None:
    from director_script import ScriptedDirector

    from studio.agent.director import Director, _story_sig

    ix = _cut(take_index, "w0008")
    d = Director(job, ix, model=ScriptedDirector({}).model(), sleep=lambda _s: None)
    d.session.doc = cut_doc
    blockers = d._radio_blockers()
    assert len(blockers) == 1 and "w0008" in blockers[0] and "cut_words w0008 w0008" in blockers[0]
    d.session.radio_sig = _story_sig(cut_doc)  # the radio test ran on this story
    story = d._exit_problems("story")
    assert sum("w0008" in p for p in story) == 1  # radio blocker only, not repeated as a validation error
    assert any("cutoff_at_join" in p for p in d._exit_problems("fine_cut"))
