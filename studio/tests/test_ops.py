from __future__ import annotations

import json
from typing import Any

import pytest

from studio.doc import ops as O
from studio.doc.model import (
    AssetRef,
    CardSpec,
    CutDocument,
    Framing,
    Licence,
    Segment,
    new_document,
)
from studio.doc.ops import OpResult, apply_ops
from studio.doc.validate import has_errors, validate_document
from studio.jobs import Job
from studio.perception.index import TakeIndex

LIC = Licence(name="Pexels License", source="pexels", url="https://www.pexels.com/license/")
ASSETS = {
    "px_42": AssetRef(source="pexels", source_id="42", path="assets/broll/px_42.mp4", licence=LIC),
    "px_43": AssetRef(source="pexels", source_id="43", path="assets/broll/px_43.mp4", licence=LIC),
    "bed_01": AssetRef(kind="audio", source="elevenlabs", path="assets/music/bed_01.wav",
                       licence=Licence(name="ElevenLabs Music Terms")),
    "pop_01": AssetRef(kind="audio", source="kenney", path="assets/sfx/pop_01.wav", licence=Licence(name="CC0")),
    "nolic": AssetRef(source="somewhere", path="assets/broll/nolic.mp4"),
}


def run(doc: CutDocument, ops: list[Any], ix: TakeIndex, **kw: Any) -> tuple[CutDocument, list[OpResult]]:
    if "job" not in kw:
        kw.setdefault("assets", ASSETS)
    return apply_ops(doc, ops, ix, **kw)


def ok(results: list[OpResult]) -> None:
    bad = [(r.op, r.reason) for r in results if not r.applied]
    assert not bad, bad


def rejected(results: list[OpResult], contains: str = "") -> str:
    assert len(results) == 1 and not results[0].applied, results
    assert contains.lower() in results[0].reason.lower(), results[0].reason
    return results[0].reason


def segs(doc: CutDocument) -> list[tuple[str, str, str]]:
    return [(s.id, s.from_word, s.to_word) for s in doc.segments]


def story_doc(ix: TakeIndex, ranges: list[tuple[str, str]]) -> CutDocument:
    doc, res = run(new_document("j"), [{"op": "set_story", "segments": [
        {"from_word": a, "to_word": b} for a, b in ranges]}], ix)
    ok(res)
    return doc


# ============================================================================================ parsing
def test_parse_rejects_unknown_missing_and_extra(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [
        {"op": "teleport", "to": "w0001"},
        {"from_word": "w0001"},
        {"op": "cut_words", "from_word": "w0001", "to_word": "w0002", "reason": "x", "start_s": 1.25},
        {"op": "cut_words", "from_word": "word1", "to_word": "w0002", "reason": "x"},
        "cut everything",
        ["cut_words"],
    ], take_index)
    assert doc is cut_doc
    assert [r.applied for r in res] == [False] * 6
    assert "unknown op 'teleport'" in res[0].reason and "cut_words" in res[0].reason
    assert "missing 'op'" in res[1].reason
    assert "start_s" in res[2].reason and "extra" in res[2].reason.lower()  # no model timestamps (invariant 3)
    assert "from_word" in res[3].reason
    assert "must be an object" in res[4].reason and "must be an object" in res[5].reason
    assert [r.index for r in res] == list(range(6))


def test_accepts_models_and_dicts(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [O.SetSpeed(seg_id="seg001", speed=1.1),
                             {"op": "set_speed", "seg_id": "seg002", "speed": 1.05}], take_index)
    ok(res)
    assert doc.segment("seg001").speed == 1.1 and doc.segment("seg002").speed == 1.05


def test_parse_op_helper():
    assert isinstance(O.parse_op({"op": "undo"}), O.Undo)
    with pytest.raises(ValueError, match="set_speed"):
        O.parse_op({"op": "set_speed", "seg_id": "seg001", "speed": 9})
    assert set(O.OP_CLASSES) == {c.model_fields["op"].default for f in O.OP_FAMILIES.values() for c in f}


# ============================================================================================ versioning
def test_versioning_and_input_not_mutated(cut_doc: CutDocument, take_index: TakeIndex):
    before = cut_doc.model_dump()
    doc, res = run(cut_doc, [{"op": "set_speed", "seg_id": "seg001", "speed": 1.2}], take_index)
    ok(res)
    assert doc.version == 2 and doc.parent_version == 1
    assert cut_doc.model_dump() == before
    doc2, res2 = run(doc, [{"op": "set_speed", "seg_id": "seg001", "speed": 9}], take_index)
    assert doc2 is doc and doc2.version == 2
    assert not res2[0].applied


def test_no_change_is_not_applied(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_speed", "seg_id": "seg001", "speed": 1.0}], take_index)
    rejected(res, "no change")
    assert doc is cut_doc


def test_partial_batch_and_sequential_semantics(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [
        {"op": "cut_words", "from_word": "w0006", "to_word": "w0007", "reason": "tighten"},
        {"op": "set_speed", "seg_id": "seg999", "speed": 1.1},  # bad: does not block the others
        {"op": "set_speed", "seg_id": "seg005", "speed": 1.1},  # the segment created by the first op
    ], take_index)
    assert [r.applied for r in res] == [True, False, True]
    assert res[0].warnings and "seg005" in res[0].warnings[0]
    assert doc.segment("seg005").speed == 1.1 and doc.version == 2


def test_persistence_saves_version_and_oplog(job: Job, cut_doc: CutDocument, take_index: TakeIndex):
    job.save_doc(cut_doc)
    doc, res = run(cut_doc, [{"op": "note", "text": "tightened the hook"},
                             {"op": "set_gap", "gap_id": "g9999", "ms": 10}], take_index, job=job, by="tester")
    assert [r.applied for r in res] == [True, False]
    assert job.doc_versions() == [1, 2]
    assert job.load_doc(2) == doc
    log = job.read_oplog()
    assert len(log) == 2
    assert log[0]["op"]["op"] == "note" and log[0]["applied"] and log[0]["version"] == 2 and log[0]["by"] == "tester"
    assert not log[1]["applied"] and log[1]["version"] == 1 and "g9999" in log[1]["reason"]
    assert doc.notes[-1].by == "tester" and doc.notes[-1].version == 2
    # nothing applied: oplog grows, no new version
    run(doc, [{"op": "set_gap", "gap_id": "g9999", "ms": 10}], take_index, job=job)
    assert job.doc_versions() == [1, 2] and len(job.read_oplog()) == 3
    # persist=False writes nothing
    run(doc, [{"op": "note", "text": "x"}], take_index, job=job, persist=False)
    assert job.doc_versions() == [1, 2] and len(job.read_oplog()) == 3


def test_internal_errors_become_rejections(monkeypatch: pytest.MonkeyPatch, cut_doc: CutDocument,
                                           take_index: TakeIndex, job: Job):
    def boom(doc, op, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(O._HANDLERS, "note", boom)
    doc, res = run(cut_doc, [{"op": "note", "text": "x"}], take_index, job=job)
    rejected(res, "internal error RuntimeError: kaboom")
    assert any(r["event"] == "op_internal_error" for r in job.read_trace())


# ============================================================================================ set_story
def test_set_story_first_cut(take_index: TakeIndex):
    ix = take_index
    d0 = new_document("j", created_by="claude-fable-5-1")
    doc, res = run(d0, [{"op": "set_story", "reason": "first cut", "segments": [
        {"from_word": "w0001", "to_word": "w0008"},
        {"from_word": "w0014", "to_word": "w0018", "gap_overrides": [{"gap_id": "g0005", "ms": 60}]},
        {"from_word": "w0020", "to_word": "w0028", "seam_in": {"kind": "jcut", "lead_ms": 160}},
        {"from_word": "w0029", "to_word": "w0041", "speed": 1.05,
         "framing": {"scale": 1.2, "anchor_word": "w0032"}},
    ], "removed": [{"from_word": "w0009", "to_word": "w0013", "reason": "false start"}]}], ix)
    ok(res)
    assert res[0].ids == ["seg001", "seg002", "seg003", "seg004"]
    assert doc.version == 1 and doc.parent_version == 0 and doc.created_by == "claude-fable-5-1"
    assert segs(doc) == [("seg001", "w0001", "w0008"), ("seg002", "w0014", "w0018"),
                         ("seg003", "w0020", "w0028"), ("seg004", "w0029", "w0041")]
    assert doc.segment("seg002").gap_overrides == {"g0005": 60}
    assert doc.segment("seg003").seam_in.kind == "jcut"
    assert doc.segment("seg004").framing.anchor_word == "w0032"
    assert [(r.from_word, r.to_word, r.reason) for r in doc.removed] == [
        ("w0009", "w0013", "false start"), ("w0019", "w0019", "first cut")]
    assert not has_errors(validate_document(doc, ix))


def test_set_story_coerces_first_seam_and_reorders(take_index: TakeIndex):
    doc, res = run(new_document(), [{"op": "set_story", "segments": [
        {"from_word": "w0029", "to_word": "w0041", "seam_in": {"kind": "lcut", "lead_ms": 100}},
        {"from_word": "w0001", "to_word": "w0008"}]}], take_index)
    ok(res)
    assert doc.segments[0].seam_in.kind == "cut" and "replaced by 'cut'" in res[0].warnings[0]
    assert doc.kept_word_ids(take_index)[0] == "w0029"
    assert doc.removed[0].reason == "not selected for the story"


@pytest.mark.parametrize("segments,needle", [
    ([{"from_word": "w0001", "to_word": "w0010"}, {"from_word": "w0010", "to_word": "w0012"}], "each word may appear"),
    ([{"from_word": "w0010", "to_word": "w0005"}], "reversed"),
    ([{"from_word": "w0001", "to_word": "w0999"}], "unknown word id w0999"),
    ([{"from_word": "w0001", "to_word": "w0008", "gap_overrides": [{"gap_id": "g0005", "ms": 10}]}],
     "not inside segment"),
    ([{"from_word": "w0014", "to_word": "w0018", "gap_overrides": [{"gap_id": "g0005", "ms": 500}]}],
     "cannot lengthen"),
    ([{"from_word": "w0014", "to_word": "w0018", "gap_overrides": [{"gap_id": "g0999", "ms": 5}]}], "unknown gap"),
    ([{"from_word": "w0014", "to_word": "w0018", "framing": {"scale": 1.2, "anchor_word": "w0020"}}],
     "outside segment"),
    ([], "segments"),
])
def test_set_story_rejections(take_index: TakeIndex, segments, needle):
    d0 = new_document()
    doc, res = run(d0, [{"op": "set_story", "segments": segments}], take_index)
    rejected(res, needle)
    assert doc is d0


def test_set_story_respects_pins(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_story", "segments": [{"from_word": "w0001", "to_word": "w0008"}]}],
                   take_index)
    reason = rejected(res, "pinned")
    assert "payoff w0018 'restraint.'" in reason and "cta w0032" in reason


def test_set_story_reanchors_and_drops(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [
        {"op": "unpin", "word_ids": ["w0018"], "kind": "payoff"},
        {"op": "set_story", "segments": [{"from_word": "w0001", "to_word": "w0008"},
                                          {"from_word": "w0022", "to_word": "w0041"}]},
    ], take_index)
    ok(res)
    w = " | ".join(res[1].warnings)
    assert "insert i001: re-anchored w0020-w0024 -> w0022-w0024" in w
    assert "sfx fx001 dropped" in w
    assert doc.insert("i001").anchor_from_word == "w0022"
    assert doc.audio.sfx == []
    assert all(set(p.word_ids) <= set(doc.kept_word_ids(take_index)) for p in doc.captions.pages)
    assert res[1].ids == ["seg005", "seg006"]  # IDs are never reused
    assert not has_errors(validate_document(doc, take_index))


# ============================================================================================ cut_words
def test_cut_words_splits_segment(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "cut_words", "from_word": "w0034", "to_word": "w0036",
                              "reason": "redundant"}], take_index)
    ok(res)
    assert segs(doc)[3:] == [("seg004", "w0029", "w0033"), ("seg005", "w0037", "w0041")]
    assert doc.segment("seg005").seam_in.kind == "cut"
    assert ("w0034", "w0036", "redundant") in [(r.from_word, r.to_word, r.reason) for r in doc.removed]
    assert doc.counters["seg"] == 5
    pages_words = [w for p in doc.captions.pages for w in p.word_ids]
    assert "w0035" not in pages_words


def test_cut_words_at_segment_start_keeps_id_and_seam(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[2].seam_in = O.SeamTreatment(kind="jcut", lead_ms=150)
    doc, res = run(d, [{"op": "cut_words", "from_word": "w0020", "to_word": "w0021", "reason": "slow start"}],
                   take_index)
    ok(res)
    s = doc.segment("seg003")
    assert (s.from_word, s.to_word, s.seam_in.kind) == ("w0022", "w0028", "jcut")
    assert "insert i001: re-anchored w0020-w0024 -> w0022-w0024" in res[0].warnings


def test_cut_whole_segment_and_across_segments(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "cut_words", "from_word": "w0001", "to_word": "w0008", "reason": "weak hook"}],
                   take_index)
    ok(res)
    assert "seg001 removed" in res[0].warnings[0]
    assert "text t001 dropped" in " ".join(res[0].warnings)
    assert [s.id for s in doc.segments] == ["seg002", "seg003", "seg004"]
    doc2, res2 = run(cut_doc, [{"op": "cut_words", "from_word": "w0024", "to_word": "w0030", "reason": "x"}],
                     take_index)
    ok(res2)
    assert segs(doc2)[2:] == [("seg003", "w0020", "w0023"), ("seg004", "w0031", "w0041")]
    assert [(r.from_word, r.to_word) for r in doc2.removed if r.reason == "x"] == [("w0024", "w0030")]


def test_cut_words_gap_overrides_and_framing_follow_pieces(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[3].gap_overrides = {"g0010": 100}
    d.segments[3].framing = Framing(scale=1.2, anchor_word="w0038")
    doc, res = run(d, [{"op": "cut_words", "from_word": "w0034", "to_word": "w0035", "reason": "x"}], take_index)
    ok(res)
    left, right = doc.segment("seg004"), doc.segment("seg005")
    assert left.gap_overrides == {"g0010": 100} and right.gap_overrides == {}
    assert left.framing.anchor_word is None and right.framing.anchor_word == "w0038"


@pytest.mark.parametrize("op,needle", [
    ({"op": "cut_words", "from_word": "w0009", "to_word": "w0013", "reason": "x"}, "nothing to cut"),
    ({"op": "cut_words", "from_word": "w0010", "to_word": "w0002", "reason": "x"}, "reversed"),
    ({"op": "cut_words", "from_word": "w0001", "to_word": "w9999", "reason": "x"}, "unknown word id"),
    ({"op": "cut_words", "from_word": "w0001", "to_word": "w0002"}, "reason"),
    ({"op": "cut_words", "from_word": "w0001", "to_word": "w0002", "reason": ""}, "reason"),
    ({"op": "cut_words", "from_word": "w0017", "to_word": "w0018", "reason": "x"}, "payoff w0018"),
    ({"op": "cut_words", "from_word": "w0033", "to_word": "w0033", "reason": "x"}, "cta w0033"),
])
def test_cut_words_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    doc, res = run(cut_doc, [op], take_index)
    rejected(res, needle)
    assert doc is cut_doc


# ============================================================================================ restore_words
def test_restore_filler_merges_neighbours(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "restore_words", "from_word": "w0019", "to_word": "w0019"}], take_index)
    ok(res)
    assert segs(doc) == [("seg001", "w0001", "w0008"), ("seg002", "w0014", "w0028"), ("seg004", "w0029", "w0041")]
    assert "seg003 merged into seg002" in res[0].warnings
    assert [(r.from_word, r.to_word) for r in doc.removed] == [("w0009", "w0013")]


def test_restore_partial_range_only_restores_missing(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "restore_words", "from_word": "w0005", "to_word": "w0011"}], take_index)
    ok(res)
    assert segs(doc)[0] == ("seg001", "w0001", "w0011")
    assert [(r.from_word, r.to_word) for r in doc.removed] == [("w0012", "w0013"), ("w0019", "w0019")]


def test_restore_extends_following_segment(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "restore_words", "from_word": "w0012", "to_word": "w0013"}], take_index)
    ok(res)
    assert ("seg002", "w0012", "w0018") in segs(doc)


def test_restore_without_neighbours_inserts_by_source_order(take_index: TakeIndex):
    doc = story_doc(take_index, [("w0001", "w0005"), ("w0030", "w0041")])
    doc2, res = run(doc, [{"op": "restore_words", "from_word": "w0015", "to_word": "w0018"}], take_index)
    ok(res)
    assert res[0].ids == ["seg003"]
    assert segs(doc2) == [("seg001", "w0001", "w0005"), ("seg003", "w0015", "w0018"), ("seg002", "w0030", "w0041")]


def test_restore_incompatible_neighbours_extend_left_and_reset_seam(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[2].speed = 1.2
    d.segments[2].seam_in = O.SeamTreatment(kind="jcut", lead_ms=120)
    doc, res = run(d, [{"op": "restore_words", "from_word": "w0019", "to_word": "w0019"}], take_index)
    ok(res)
    assert doc.segment("seg002").to_word == "w0019"
    assert doc.segment("seg003").seam_in.kind == "cut"
    assert any("seam reset" in w for w in res[0].warnings)


def test_restore_already_kept_is_no_change(cut_doc: CutDocument, take_index: TakeIndex):
    rejected(run(cut_doc, [{"op": "restore_words", "from_word": "w0001", "to_word": "w0003"}], take_index)[1],
             "already in the story")


# ============================================================================================ choose_take
def test_choose_take_on_raw_story(take_index: TakeIndex):
    doc = story_doc(take_index, [("w0001", "w0041")])
    doc2, res = run(doc, [{"op": "choose_take", "cluster_id": "c01", "sentence_id": "s003"}], take_index)
    ok(res)
    assert segs(doc2) == [("seg001", "w0001", "w0008"), ("seg002", "w0014", "w0041")]
    assert res[0].ids == ["seg002"]
    assert [(r.from_word, r.to_word, r.reason) for r in doc2.removed] == [
        ("w0009", "w0013", "retake: chose s003 in c01")]


def test_choose_take_swaps_takes_in_place(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "choose_take", "cluster_id": "c01", "sentence_id": "s002"}], take_index)
    rejected(res, "pinned")  # the chosen take would drop the pinned payoff
    doc, res = run(cut_doc, [{"op": "unpin", "word_ids": ["w0018"], "kind": "payoff"},
                             {"op": "choose_take", "cluster_id": "c01", "sentence_id": "s002",
                              "reason": "more energy"}], take_index)
    ok(res)
    # the false start directly follows the hook in the source, so no seam is needed: it merges into seg001
    assert segs(doc) == [("seg001", "w0001", "w0013"), ("seg003", "w0020", "w0028"), ("seg004", "w0029", "w0041")]
    assert res[1].ids == ["seg001"] and "seg005 merged into seg001" in res[1].warnings
    assert ("w0014", "w0018", "more energy") in [(r.from_word, r.to_word, r.reason) for r in doc.removed]
    assert all(r.from_word != "w0009" for r in doc.removed)
    assert "sfx fx001 dropped" in " ".join(res[1].warnings)
    # and back again
    doc2, res2 = run(doc, [{"op": "choose_take", "cluster_id": "c01", "sentence_id": "s003"}], take_index)
    ok(res2)
    assert [s[1:] for s in segs(doc2)] == [("w0001", "w0008"), ("w0014", "w0018"), ("w0020", "w0028"),
                                          ("w0029", "w0041")]


def test_choose_take_when_no_take_is_kept(take_index: TakeIndex):
    doc = story_doc(take_index, [("w0001", "w0008"), ("w0020", "w0041")])
    doc2, res = run(doc, [{"op": "choose_take", "cluster_id": "c01", "sentence_id": "s003"}], take_index)
    ok(res)
    assert [s[1:] for s in segs(doc2)] == [("w0001", "w0008"), ("w0014", "w0018"), ("w0020", "w0041")]


@pytest.mark.parametrize("op,needle", [
    ({"op": "choose_take", "cluster_id": "c01", "sentence_id": "s003"}, "no change"),
    ({"op": "choose_take", "cluster_id": "c09", "sentence_id": "s003"}, "unknown cluster"),
    ({"op": "choose_take", "cluster_id": "c01", "sentence_id": "s004"}, "not a take of c01"),
])
def test_choose_take_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


# ============================================================================================ move / gap / speed / seam
def test_move_segment(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "move_segment", "seg_id": "seg004", "after": None}], take_index)
    ok(res)
    assert [s.id for s in doc.segments] == ["seg004", "seg001", "seg002", "seg003"]
    doc, res = run(cut_doc, [{"op": "move_segment", "seg_id": "seg001", "after": "seg003"}], take_index)
    ok(res)
    assert [s.id for s in doc.segments] == ["seg002", "seg003", "seg001", "seg004"]


def test_move_segment_resets_first_seam_and_warns_on_inverted_anchors(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[2].seam_in = O.SeamTreatment(kind="lcut", lead_ms=100)
    d, res = run(d, [{"op": "add_text", "text": "spans", "anchor_from_word": "w0014", "anchor_to_word": "w0024"}],
                 take_index)
    ok(res)
    doc, res = run(d, [{"op": "move_segment", "seg_id": "seg003", "after": None}], take_index)
    ok(res)
    w = " | ".join(res[0].warnings)
    assert "seam reset to cut" in w and "text t002: anchors w0014-w0024 are now out of output order" in w
    assert doc.segments[0].seam_in.kind == "cut"
    assert has_errors(validate_document(doc, take_index))  # validator flags the inverted text


@pytest.mark.parametrize("op,needle", [
    ({"op": "move_segment", "seg_id": "seg001", "after": None}, "no change"),
    ({"op": "move_segment", "seg_id": "seg009", "after": None}, "unknown segment"),
    ({"op": "move_segment", "seg_id": "seg001", "after": "seg009"}, "unknown segment"),
    ({"op": "move_segment", "seg_id": "seg001", "after": "seg001"}, "after itself"),
])
def test_move_segment_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


def test_set_gap(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_gap", "gap_id": "g0005", "ms": 40}], take_index)
    ok(res)
    assert doc.segment("seg002").gap_overrides == {"g0005": 40}
    doc2, res2 = run(doc, [{"op": "set_gap", "gap_id": "g0005", "ms": 120}], take_index)  # natural length
    ok(res2)
    assert doc2.segment("seg002").gap_overrides == {}
    doc3, res3 = run(cut_doc, [{"op": "set_gap", "gap_id": "g0010", "ms": 0}], take_index)
    ok(res3)
    assert doc3.segment("seg004").gap_overrides == {"g0010": 0}
    # a gap at a continuous join (seg003 → seg004 continue the source, e.g. a framing split): the compiler trims it
    # like an inner pause, so the target is kept on the left segment
    doc4, res4 = run(cut_doc, [{"op": "set_gap", "gap_id": "g0009", "ms": 100}], take_index)
    ok(res4)
    assert doc4.segment("seg003").gap_overrides == {"g0009": 100}


@pytest.mark.parametrize("op,needle", [
    ({"op": "set_gap", "gap_id": "g0005", "ms": 400}, "cannot lengthen"),
    ({"op": "set_gap", "gap_id": "g0003", "ms": 100}, "which is cut: the pause is part of a cut edge"),  # after w0008
    ({"op": "set_gap", "gap_id": "g0001", "ms": 100}, "leading/trailing"),
    ({"op": "set_gap", "gap_id": "g0999", "ms": 100}, "unknown gap"),
    ({"op": "set_gap", "gap_id": "g0005", "ms": -1}, "ms"),
])
def test_set_gap_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


def test_set_speed_and_ranges(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_speed", "seg_id": "seg004", "speed": 1.15}], take_index)
    ok(res)
    assert doc.segment("seg004").speed == 1.15
    for bad in (0.49, 2.01):
        rejected(run(cut_doc, [{"op": "set_speed", "seg_id": "seg004", "speed": bad}], take_index)[1], "speed")
    rejected(run(cut_doc, [{"op": "set_speed", "seg_id": "seg777", "speed": 1.1}], take_index)[1], "unknown segment")


def test_set_seam(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_seam", "seg_id": "seg003", "treatment": {"kind": "jcut", "lead_ms": 200}},
                             {"op": "set_seam", "seg_id": "seg001", "treatment": {"kind": "punch"}}], take_index)
    ok(res)
    assert doc.segment("seg003").seam_in.lead_ms == 200 and doc.segment("seg001").seam_in.kind == "punch"
    rejected(run(cut_doc, [{"op": "set_seam", "seg_id": "seg003", "treatment": {"kind": "lcut"}}], take_index)[1],
             "lead_ms > 0")
    rejected(run(cut_doc, [{"op": "set_seam", "seg_id": "seg001",
                            "treatment": {"kind": "jcut", "lead_ms": 100}}], take_index)[1], "first segment")
    rejected(run(cut_doc, [{"op": "set_seam", "seg_id": "seg002", "treatment": {"kind": "wipe"}}], take_index)[1],
             "kind")


# ============================================================================================ framing
def test_set_framing_whole_segment(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_framing", "seg_id": "seg002", "scale": 1.3, "ease": "push"}], take_index)
    ok(res)
    f = doc.segment("seg002").framing
    assert (f.scale, f.center, f.ease) == (1.3, "face", "push")


def test_set_framing_word_range_splits_and_clear_merges(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_framing", "from_word": "w0032", "to_word": "w0033", "scale": 1.25,
                              "center": {"x": 0.5, "y": 0.35}}], take_index)
    ok(res)
    assert segs(doc)[3:] == [("seg004", "w0029", "w0031"), ("seg005", "w0032", "w0033"),
                             ("seg006", "w0034", "w0041")]
    mid = doc.segment("seg005")
    assert mid.framing.scale == 1.25 and mid.seam_in.kind == "punch" and doc.segment("seg006").seam_in.kind == "punch"
    assert doc.segment("seg004").framing is None and doc.segment("seg006").framing is None
    assert res[0].ids == ["seg005"]
    back, res2 = run(doc, [{"op": "clear_framing", "seg_id": "seg005"}], take_index)
    ok(res2)
    assert segs(back)[3:] == [("seg004", "w0029", "w0041")]
    assert back.kept_word_ids(take_index) == cut_doc.kept_word_ids(take_index)


def test_set_framing_range_at_segment_start(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_framing", "from_word": "w0029", "to_word": "w0031", "scale": 1.2,
                              "anchor_word": "w0030", "ease": "smooth"}], take_index)
    ok(res)
    assert segs(doc)[3:] == [("seg004", "w0029", "w0031"), ("seg005", "w0032", "w0041")]
    assert doc.segment("seg004").framing.anchor_word == "w0030"
    assert doc.segment("seg005").framing is None and doc.segment("seg005").seam_in.kind == "punch"


def test_clear_framing_by_range(cut_doc: CutDocument, take_index: TakeIndex):
    d, res = run(cut_doc, [{"op": "set_framing", "seg_id": "seg001", "scale": 1.2},
                           {"op": "set_framing", "seg_id": "seg002", "scale": 1.2}], take_index)
    ok(res)
    doc, res = run(d, [{"op": "clear_framing", "from_word": "w0005", "to_word": "w0015"}], take_index)
    ok(res)
    assert doc.segment("seg001").framing is None and doc.segment("seg002").framing is None
    rejected(run(d, [{"op": "clear_framing", "from_word": "w0009", "to_word": "w0012"}], take_index)[1],
             "not in the story")


@pytest.mark.parametrize("op,needle", [
    ({"op": "set_framing", "from_word": "w0020", "to_word": "w0030", "scale": 1.2}, "spans seg003, seg004"),
    ({"op": "set_framing", "from_word": "w0008", "to_word": "w0009", "scale": 1.2}, "not in the story"),
    ({"op": "set_framing", "seg_id": "seg002", "scale": 1.2, "anchor_word": "w0030"}, "outside the framed range"),
    ({"op": "set_framing", "seg_id": "seg002", "scale": 1.9}, "scale"),
    ({"op": "set_framing", "seg_id": "seg002"}, "scale"),
    ({"op": "set_framing", "seg_id": "seg002", "from_word": "w0014", "to_word": "w0015", "scale": 1.2},
     "either seg_id"),
    ({"op": "set_framing", "scale": 1.2}, "either seg_id"),
    ({"op": "set_framing", "from_word": "w0014", "scale": 1.2}, "both from_word and to_word"),
    ({"op": "set_framing", "seg_id": "seg009", "scale": 1.2}, "unknown segment"),
])
def test_set_framing_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


# ============================================================================================ inserts
def test_add_update_remove_insert(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0005",
                              "mode": "full", "asset_id": "px_42", "job": "show someone cutting",
                              "alternate_asset_ids": ["px_43"], "transition_in": {"kind": "fade", "ms": 120}}],
                   take_index)
    ok(res)
    assert res[0].ids == ["i002"]
    ins = doc.insert("i002")
    assert ins.effective_licence.name == "Pexels License" and ins.transition_in.kind == "fade"
    assert ins.asset.id == "px_42" and ins.asset.path == "assets/broll/px_42.mp4"
    assert [a.id for a in ins.alternates] == ["px_43"]
    doc2, res2 = run(doc, [{"op": "update_insert", "id": "i002", "anchor_to_word": "w0007", "audio": "duck"}],
                     take_index)
    ok(res2)
    assert doc2.insert("i002").anchor_to_word == "w0007" and doc2.insert("i002").audio == "duck"
    assert doc2.insert("i002").job == "show someone cutting"
    doc3, res3 = run(doc2, [{"op": "remove_insert", "id": "i002", "reason": "distracting"}], take_index)
    ok(res3)
    assert [i.id for i in doc3.inserts] == ["i001"]
    # ids are not reused after removal
    doc4, res4 = run(doc3, [{"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004",
                             "mode": "card", "card": {"template": "number", "number": "19"},
                             "job": "the 1 vs 19 stat"}], take_index)
    ok(res4)
    assert res4[0].ids == ["i003"]


@pytest.mark.parametrize("op,needle", [
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0005", "asset_id": "nolic", "job": "x"},
     "no licence record"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0005", "asset_id": "px_999",
      "job": "x"}, "unknown asset px_999"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0005", "asset_id": "px_42",
      "alternate_asset_ids": ["nolic"], "job": "x"}, "no licence record"),
    ({"op": "add_insert", "anchor_from_word": "w0010", "anchor_to_word": "w0012", "asset_id": "px_42",
      "job": "x"}, "not in the story"),
    ({"op": "add_insert", "anchor_from_word": "w0005", "anchor_to_word": "w0003", "asset_id": "px_42",
      "job": "x"}, "comes after"),
    ({"op": "add_insert", "anchor_from_word": "w0022", "anchor_to_word": "w0026", "asset_id": "px_42",
      "job": "x"}, "overlaps insert(s) i001"),
    ({"op": "add_insert", "anchor_from_word": "w0022", "anchor_to_word": "w0026", "mode": "pip",
      "asset_id": "px_42", "job": "x"}, "overlaps"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "mode": "card",
      "asset_id": "px_42", "job": "x"}, "card asset"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "job": "x"}, "exactly one"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "asset_id": "px_42",
      "card": {"title": "x"}, "job": "x"}, "exactly one"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "asset_id": "px_42",
      "job": ""}, "job"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "asset_id": "px_42",
      "job": "x", "out_s": 2.5}, "out_s"),
    ({"op": "add_insert", "anchor_from_word": "w0003", "anchor_to_word": "w0004", "asset_id": "../etc",
      "job": "x"}, "asset_id"),
    ({"op": "update_insert", "id": "i001"}, "no fields"),
    ({"op": "update_insert", "id": "i001", "asset_id": "px_42", "card": {"title": "x"}}, "not both"),
    ({"op": "update_insert", "id": "i009", "job": "x"}, "unknown insert"),
    ({"op": "remove_insert", "id": "i009"}, "unknown insert"),
])
def test_insert_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


def test_insert_layering_rules(cut_doc: CutDocument, take_index: TakeIndex):
    base, res = run(cut_doc, [{"op": "add_insert", "anchor_from_word": "w0001", "anchor_to_word": "w0005",
                               "mode": "pip", "asset_id": "px_42", "job": "a"}], take_index)
    ok(res)
    rejected(run(base, [{"op": "add_insert", "anchor_from_word": "w0004", "anchor_to_word": "w0006",
                         "mode": "pip", "asset_id": "px_43", "job": "b"}], take_index)[1], "overlaps")
    doc, res = run(base, [{"op": "add_insert", "anchor_from_word": "w0004", "anchor_to_word": "w0006",
                           "mode": "split_top", "asset_id": "px_43", "job": "b"}], take_index)
    ok(res)  # pip + split may coexist; validate() warns
    fs = validate_document(doc, take_index)
    assert any(f.code == "insert_overlap" and f.level == "warning" for f in fs)
    rejected(run(base, [{"op": "update_insert", "id": "i001", "anchor_from_word": "w0003",
                         "mode": "full"}], take_index)[1], "overlaps")


def test_cutting_anchor_words_reanchors_or_drops_insert(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "cut_words", "from_word": "w0020", "to_word": "w0024", "reason": "x"}],
                   take_index)
    ok(res)
    assert doc.inserts == [] and "insert i001 dropped" in " ".join(res[0].warnings)


# ============================================================================================ captions / text
def test_set_captions_assigns_ids_and_validates(cut_doc: CutDocument, take_index: TakeIndex):
    plan = {"pages": [{"word_ids": ["w0001", "w0002", "w0003"], "emphasis_word_ids": ["w0003"]},
                      {"id": "p050", "word_ids": ["w0004", "w0005"]},
                      {"word_ids": ["w0006", "w0007", "w0008"]}],
            "style": {"font": "Anton", "size_px": 80, "case": "upper"}}
    doc, res = run(cut_doc, [{"op": "set_captions", "plan": plan}], take_index)
    ok(res)
    assert [p.id for p in doc.captions.pages] == ["p051", "p050", "p052"]
    assert res[0].ids == ["p051", "p052"]
    assert doc.captions.style.font == "Anton"
    assert any(f.code == "uncaptioned_words" for f in validate_document(doc, take_index))


@pytest.mark.parametrize("pages,needle", [
    ([{"word_ids": ["w0010"]}], "not in the story"),
    ([{"word_ids": ["w0002", "w0001"]}], "out of output order"),
    ([{"word_ids": ["w0001", "w0002"]}, {"word_ids": ["w0002", "w0003"]}], "out of output order"),
    ([{"word_ids": ["w0001"], "emphasis_word_ids": ["w0002"]}], "emphasis"),
    ([{"id": "p001", "word_ids": ["w0001"]}, {"id": "p001", "word_ids": ["w0002"]}], "duplicate"),
])
def test_set_captions_rejections(cut_doc: CutDocument, take_index: TakeIndex, pages, needle):
    rejected(run(cut_doc, [{"op": "set_captions", "plan": {"pages": pages}}], take_index)[1], needle)


def test_edit_caption_page(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "edit_caption_page", "page_id": "p001", "emphasis_word_ids": ["w0001"],
                              "text": "MOST people", "position": "lower_third"}], take_index)
    ok(res)
    p = doc.caption_page("p001")
    assert p.emphasis_word_ids == ["w0001"] and p.text == "MOST people" and p.position == "lower_third"
    doc2, res2 = run(doc, [{"op": "edit_caption_page", "page_id": "p001", "text": ""}], take_index)
    ok(res2)
    assert doc2.caption_page("p001").text is None
    # changing words keeps only emphasis still on the page
    doc3, res3 = run(doc, [{"op": "edit_caption_page", "page_id": "p001", "word_ids": ["w0002", "w0003"]}],
                     take_index)
    ok(res3)
    assert doc3.caption_page("p001").emphasis_word_ids == []
    rejected(run(cut_doc, [{"op": "edit_caption_page", "page_id": "p001", "emphasis_word_ids": ["w0030"]}],
                 take_index)[1], "emphasis")
    rejected(run(cut_doc, [{"op": "edit_caption_page", "page_id": "p001", "word_ids": ["w0004"]}],
                 take_index)[1], "out of output order")
    rejected(run(cut_doc, [{"op": "edit_caption_page", "page_id": "p999", "text": "x"}], take_index)[1], "unknown")
    rejected(run(cut_doc, [{"op": "edit_caption_page", "page_id": "p001"}], take_index)[1], "no fields")
    no_caps = cut_doc.model_copy(update={"captions": None})
    rejected(run(no_caps, [{"op": "edit_caption_page", "page_id": "p001", "text": "x"}], take_index)[1],
             "set_captions first")


def test_set_caption_style(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_caption_style", "style": {"size_px": 90, "animation": "karaoke"},
                              "position": "below_chin"}], take_index)
    ok(res)
    assert doc.captions.style.size_px == 90 and doc.captions.position == "below_chin"
    assert len(doc.captions.pages) == len(cut_doc.captions.pages)
    no_caps = cut_doc.model_copy(update={"captions": None})
    doc2, res2 = run(no_caps, [{"op": "set_caption_style", "style": {}, "enabled": False}], take_index)
    ok(res2)
    assert doc2.captions.enabled is False and "no caption pages" in res2[0].warnings[0]


def test_text_overlays(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "add_text", "kind": "list", "text": "3 rules", "items": ["a", "b"],
                              "anchor_from_word": "w0020", "anchor_to_word": "w0028",
                              "position": {"x": 0.5, "y": 0.2}}], take_index)
    ok(res)
    assert res[0].ids == ["t002"]
    doc2, res2 = run(doc, [{"op": "update_text", "id": "t002", "text": "Three rules"},
                           {"op": "remove_text", "id": "t001"}], take_index)
    ok(res2)
    assert [t.id for t in doc2.texts] == ["t002"] and doc2.text("t002").text == "Three rules"
    for op, needle in (
        ({"op": "add_text", "text": "x", "anchor_from_word": "w0010", "anchor_to_word": "w0011"}, "not in the story"),
        ({"op": "add_text", "text": "", "anchor_from_word": "w0001", "anchor_to_word": "w0002"}, "text"),
        ({"op": "update_text", "id": "t009", "text": "x"}, "unknown text"),
        ({"op": "update_text", "id": "t001"}, "no fields"),
        ({"op": "update_text", "id": "t001", "anchor_to_word": "w0012"}, "not in the story"),
        ({"op": "remove_text", "id": "t009"}, "unknown text"),
    ):
        rejected(run(cut_doc, [op], take_index)[1], needle)


# ============================================================================================ audio / color
def test_audio_ops(cut_doc: CutDocument, take_index: TakeIndex):
    music = {"asset_id": "bed_01", "source": "elevenlabs", "start_word": "w0014", "end_word": "w0041",
             "hit_word_ids": ["w0018"]}
    doc, res = run(cut_doc, [
        {"op": "set_voice_chain", "spec": {"hpf_hz": 90, "deess_db": 3, "eq": [{"freq_hz": 3000, "gain_db": 2}]}},
        {"op": "set_music", "spec": music},
        {"op": "add_sfx", "kind": "whoosh", "anchor_word": "w0020", "offset_ms": -80, "asset_id": "pop_01"},
        {"op": "remove_sfx", "id": "fx001"},
        {"op": "set_loudness", "lufs": -16, "true_peak_dbtp": -1.5},
    ], take_index)
    ok(res)
    assert doc.audio.voice.hpf_hz == 90 and doc.audio.voice.eq[0].freq_hz == 3000
    assert doc.audio.music.start_word == "w0014"
    assert doc.audio.music.asset.id == "bed_01" and doc.audio.music.asset.licence.name == "ElevenLabs Music Terms"
    assert [x.id for x in doc.audio.sfx] == ["fx002"] and res[2].ids == ["fx002"]
    assert doc.audio.sfx[0].asset.id == "pop_01"
    assert doc.audio.loudness_target_lufs == -16 and doc.audio.true_peak_dbtp == -1.5
    doc2, res2 = run(doc, [{"op": "set_music", "spec": None}], take_index)
    ok(res2)
    assert doc2.audio.music is None


@pytest.mark.parametrize("op,needle", [
    ({"op": "set_music", "spec": {"asset_id": "nolic"}}, "licence"),
    ({"op": "set_music", "spec": {"asset_id": "zzz"}}, "unknown asset"),
    ({"op": "set_music", "spec": {"asset": {"type": "asset", "source": "x"}}}, "asset"),
    ({"op": "set_music", "spec": {"start_word": "w0030", "end_word": "w0020"}}, "comes after"),
    ({"op": "set_music", "spec": {"start_word": "w0010"}}, "not in the story"),
    ({"op": "add_sfx", "kind": "pop", "anchor_word": "w0019"}, "not in the story"),
    ({"op": "add_sfx", "kind": "pop", "anchor_word": "w0018", "asset_id": "nolic"}, "licence"),
    ({"op": "add_sfx", "kind": "pop", "anchor_word": "w0018", "offset_ms": 900}, "offset_ms"),
    ({"op": "remove_sfx", "id": "fx009"}, "unknown sfx"),
    ({"op": "set_loudness", "lufs": -16, "true_peak_dbtp": -0.5}, "true_peak"),
    ({"op": "set_loudness", "lufs": -30}, "lufs"),
    ({"op": "set_voice_chain", "spec": {"hpf_hz": 5}}, "hpf_hz"),
])
def test_audio_rejections(cut_doc: CutDocument, take_index: TakeIndex, op, needle):
    rejected(run(cut_doc, [op], take_index)[1], needle)


def test_color_ops(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "set_color", "spec": {"exposure": 0.2, "temp": 0.1, "look": "warm_film",
                                                          "lut_strength": 0.5}}], take_index)
    ok(res)
    assert doc.color.look == "warm_film"
    doc2, res2 = run(doc, [{"op": "set_color", "spec": None}], take_index)
    ok(res2)
    assert doc2.color is None
    rejected(run(cut_doc, [{"op": "set_color", "spec": {"saturation": 3}}], take_index)[1], "saturation")


# ============================================================================================ meta
def test_brief_style_note_deliverables_hooks(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [
        {"op": "set_brief", "brief": {"goal": "teach restraint", "cta": "follow", "target_length_s": 20,
                                      "rubric": ["Is the hook under 3 s?"]}},
        {"op": "set_style", "style": {"primary": "educational", "dials": {"energy": 0.7}}},
        {"op": "note", "text": "kept the breath before the payoff", "ref": "g0006"},
        {"op": "set_deliverables", "deliverables": [{"platform": "tiktok"}, {"platform": "shorts"}]},
        {"op": "set_hook_alternates", "alternates": [
            {"ranges": [{"from_word": "w0014", "to_word": "w0018"}], "title_text": "The secret"},
            {"ranges": [{"from_word": "w0009", "to_word": "w0013"}, {"from_word": "w0001", "to_word": "w0008"}]}]},
    ], take_index, by="claude-fable-5-1")
    ok(res)
    assert doc.brief.goal == "teach restraint" and doc.style.dials.energy == 0.7
    assert doc.notes[-1].ref == "g0006" and doc.notes[-1].by == "claude-fable-5-1"
    assert [d.platform for d in doc.deliverables] == ["tiktok", "shorts"]
    assert [h.id for h in doc.hook_alternates] == ["h01", "h02"] and res[4].ids == ["h01", "h02"]
    rejected(run(cut_doc, [{"op": "set_deliverables", "deliverables": [{"platform": "tiktok"},
                                                                       {"platform": "tiktok"}]}], take_index)[1],
             "duplicate")
    rejected(run(cut_doc, [{"op": "set_deliverables", "deliverables": []}], take_index)[1], "deliverables")
    rejected(run(cut_doc, [{"op": "set_hook_alternates", "alternates": [
        {"ranges": [{"from_word": "w0010", "to_word": "w0002"}]}]}], take_index)[1], "reversed")
    rejected(run(cut_doc, [{"op": "set_style", "style": {"dials": {"energy": 3}}}], take_index)[1], "energy")
    rejected(run(cut_doc, [{"op": "note", "text": ""}], take_index)[1], "text")


def test_pin_and_unpin(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "pin", "word_ids": ["w0001", "w0002"], "kind": "must_keep"}], take_index)
    ok(res)
    assert doc.pins.must_keep_word_ids == ["w0001", "w0002"]
    rejected(run(doc, [{"op": "cut_words", "from_word": "w0002", "to_word": "w0003", "reason": "x"}],
                 take_index)[1], "must_keep w0002")
    rejected(run(doc, [{"op": "pin", "word_ids": ["w0010"], "kind": "payoff"}], take_index)[1], "not in the story")
    rejected(run(doc, [{"op": "pin", "word_ids": ["w0001"], "kind": "must_keep"}], take_index)[1], "no change")
    rejected(run(doc, [{"op": "unpin", "word_ids": ["w0030"], "kind": "cta"}], take_index)[1], "no change")
    doc2, res2 = run(doc, [{"op": "unpin", "word_ids": ["w0001", "w0030"], "kind": "must_keep"}], take_index)
    ok(res2)
    assert doc2.pins.must_keep_word_ids == ["w0002"] and "not pinned" in res2[0].warnings[0]
    rejected(run(doc, [{"op": "pin", "word_ids": [], "kind": "cta"}], take_index)[1], "word_ids")
    rejected(run(doc, [{"op": "pin", "word_ids": ["w0001"], "kind": "hook"}], take_index)[1], "kind")


# ============================================================================================ undo
def test_undo_via_job_history(job: Job, cut_doc: CutDocument, take_index: TakeIndex):
    ix = take_index
    job.save_doc(cut_doc)  # v1
    v2, r = run(cut_doc, [{"op": "cut_words", "from_word": "w0034", "to_word": "w0036", "reason": "x"}], ix, job=job)
    ok(r)
    v3, r = run(v2, [{"op": "add_sfx", "kind": "pop", "anchor_word": "w0020"}], ix, job=job)
    ok(r)
    assert v3.version == 3 and v3.counters["fx"] == 2
    v4, r = run(v3, [{"op": "undo", "n": 2}], ix, job=job)
    ok(r)
    assert r[0].reason == "restored content of v1"
    assert v4.version == 4 and v4.parent_version == 3
    assert segs(v4) == segs(cut_doc) and v4.audio.sfx == cut_doc.audio.sfx
    assert v4.counters["seg"] == 5 and v4.counters["fx"] == 2  # IDs are never re-issued after undo
    v5, r = run(v4, [{"op": "undo"}], ix, job=job)  # undoing the undo = redo
    ok(r)
    assert segs(v5) == segs(v3) and v5.version == 5
    assert job.doc_versions() == [1, 2, 3, 4, 5]
    v6, r = run(v5, [{"op": "add_sfx", "kind": "ding", "anchor_word": "w0041"}], ix, job=job)
    assert r[0].ids == ["fx003"]
    assert [e["op"]["op"] for e in job.read_oplog()] == ["cut_words", "add_sfx", "undo", "undo", "add_sfx"]


def test_undo_helper_and_rejections(job: Job, cut_doc: CutDocument, take_index: TakeIndex):
    ix = take_index
    rejected(run(cut_doc, [{"op": "undo"}], ix)[1], "needs the job history")
    job.save_doc(cut_doc)
    rejected(run(cut_doc, [{"op": "undo", "n": 5}], ix, job=job)[1], "only 0 earlier version(s)")
    doc, res = run(cut_doc, [{"op": "undo"}, {"op": "note", "text": "x"}], ix, job=job)
    assert not res[0].applied and "only op" in res[0].reason and res[1].applied
    assert doc.version == 2
    v3, _ = run(cut_doc, [{"op": "note", "text": "a"}], ix, job=job)  # a second branch from v1
    assert v3.version == 3 and v3.parent_version == 1
    back, r = O.undo(v3, ix, job)
    assert r.applied and back.notes == cut_doc.notes and back.version == 4 and back.parent_version == 3
    assert r.reason == "restored content of v1"
    # history callable instead of a job
    hist = {1: cut_doc}
    back2, r2 = run(v3, [{"op": "undo"}], ix, history=hist.get)
    assert r2[0].applied and back2.notes == cut_doc.notes and back2.version == 4


def test_branching_revisions_get_unique_versions(job: Job, cut_doc: CutDocument, take_index: TakeIndex):
    job.save_doc(cut_doc)
    a, _ = run(cut_doc, [{"op": "set_speed", "seg_id": "seg001", "speed": 1.1}], take_index, job=job)
    b, _ = run(cut_doc, [{"op": "set_speed", "seg_id": "seg001", "speed": 1.2}], take_index, job=job)
    assert (a.version, b.version) == (2, 3) and a.parent_version == b.parent_version == 1
    assert job.load_doc(2).segment("seg001").speed == 1.1 and job.load_doc(3).segment("seg001").speed == 1.2
    back, r = run(b, [{"op": "undo"}], take_index, job=job)
    assert r[0].applied and back.segment("seg001").speed == 1.0 and back.version == 4


# ============================================================================================ invariants of the story
def test_random_op_sequences_keep_documents_valid(cut_doc: CutDocument, take_index: TakeIndex):
    """Property-style: whatever sequence of story ops applies, the result validates and words stay unique."""
    import random

    rng = random.Random(1234)
    ix = take_index
    ids = [w.id for w in ix.words]
    doc = cut_doc.model_copy(update={"pins": cut_doc.pins.model_copy(update={"payoff_word_ids": [],
                                                                            "cta_word_ids": []})})
    applied = 0
    for _ in range(250):
        a, b = sorted(rng.sample(range(len(ids)), 2))
        choice = rng.random()
        if choice < 0.35:
            op = {"op": "cut_words", "from_word": ids[a], "to_word": ids[min(b, a + 4)], "reason": "fuzz"}
        elif choice < 0.7:
            op = {"op": "restore_words", "from_word": ids[a], "to_word": ids[min(b, a + 6)]}
        elif choice < 0.8 and doc.segments:
            s = rng.choice(doc.segments).id
            after = rng.choice([None] + [x.id for x in doc.segments if x.id != s])
            op = {"op": "move_segment", "seg_id": s, "after": after}
        elif choice < 0.9 and doc.segments:
            op = {"op": "set_framing", "seg_id": rng.choice(doc.segments).id, "scale": 1.2}
        else:
            op = {"op": "choose_take", "cluster_id": "c01", "sentence_id": rng.choice(["s002", "s003"])}
        doc, res = run(doc, [op], ix)
        applied += res[0].applied
        kept = doc.kept_word_ids(ix)
        assert len(kept) == len(set(kept))
        removed_ids = set(doc.removed_word_ids(ix))
        recorded = {w for r in doc.removed for w in ix.word_ids(r.from_word, r.to_word)}
        assert recorded == removed_ids, (op, res)
        # judgment-level errors ops may leave for the Director to resolve (order after a move; a cut-off word a random
        # cut leaves at a join): the Director's stage exits and invariant 1 block them, not the ops
        fs = [f for f in validate_document(doc, ix) if f.level == "error" and f.code not in ("anchor_order",
                                                                                             "caption_order",
                                                                                             "cutoff_at_join")]
        assert not fs, (op, fs)
    assert applied > 100  # the sequence really exercised the mutator


# ============================================================================================ schemas
def test_family_schemas_cover_all_ops():
    for fam, classes in O.OP_FAMILIES.items():
        schema = O.op_json_schema(fam)
        text = json.dumps(schema)
        for cls in classes:
            assert f'"{cls.model_fields["op"].default}"' in text
        defs = [schema, *schema.get("$defs", {}).values()]
        for node in defs:
            props = node.get("properties", {})
            if "op" in props and "const" in props["op"]:
                assert node["required"][0] == "op"
    assert "anyOf" in json.dumps(O.op_json_schema(None)) or "oneOf" in json.dumps(O.op_json_schema(None))
    with pytest.raises(KeyError):
        O.op_json_schema("nope")


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_default_tool_definitions():
    tools = O.tool_definitions()
    assert [t["name"] for t in tools] == ["cut_ops", "framing_ops", "inserts_ops", "captions_ops", "audio_ops",
                                          "color_ops", "meta_ops"]
    assert not any(t["strict"] for t in tools)  # strict at this schema size exceeds Anthropic's grammar limit
    assert [t["strict"] for t in O.tool_definitions(strict=["cut"])] == [True] + [False] * 6
    non_strict = json.dumps([t["input_schema"] for t in tools if not t["strict"]])
    assert '"oneOf"' not in non_strict and '"discriminator"' not in non_strict
    assert '"minimum"' in non_strict or '"exclusiveMinimum"' in non_strict  # constraints kept when not strict
    story = O.tool_definitions(families=O.STAGE_FAMILIES["story"], strict=False)
    assert [t["name"] for t in story] == ["cut_ops", "meta_ops"] and not any(t["strict"] for t in story)
    with pytest.raises(KeyError):
        O.tool_definitions(families=["cut", "nope"])


def test_strict_tool_schemas_are_strict_compatible():
    tools = O.tool_definitions(strict=True)
    assert all(t["strict"] for t in tools)
    for t in tools:
        schema = t["input_schema"]
        assert t["description"]
        assert schema["required"] == ["ops"] and schema["additionalProperties"] is False
        for node in _walk(schema):
            for banned in ("oneOf", "discriminator", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                           "minLength", "maxLength", "maxItems"):
                assert banned not in node, (t["name"], banned, node)
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False, (t["name"], node)
            if "minItems" in node:
                assert node["minItems"] in (0, 1)
            if "format" in node:
                assert node["format"] in {"date-time", "time", "date", "duration", "email", "hostname", "uri",
                                          "ipv4", "ipv6", "uuid"}
        # constraints survive as descriptions
    cut = json.dumps(O.family_tool_schema("cut"))
    assert "at least 0.5" in cut and "at most 2.0" in cut


def test_non_strict_schema_keeps_constraints():
    s = json.dumps(O.family_tool_schema("cut", strict=False))
    assert '"minimum"' in s or '"exclusiveMinimum"' in s


def test_strict_schema_rejects_open_maps():
    with pytest.raises(ValueError):
        O.strict_schema({"type": "object", "additionalProperties": {"type": "integer"}})


def test_example_tool_payloads_parse(cut_doc: CutDocument, take_index: TakeIndex):
    """A payload shaped like a strict tool call (``{"ops": [...]}``) goes straight into apply_ops."""
    payload = json.loads(json.dumps({"ops": [
        {"op": "set_gap", "gap_id": "g0005", "ms": 50},
        {"op": "set_seam", "seg_id": "seg003", "treatment": {"kind": "lcut", "lead_ms": 120}},
    ]}))
    doc, res = run(cut_doc, payload["ops"], take_index)
    ok(res)


def test_card_spec_roundtrip_through_ops(cut_doc: CutDocument, take_index: TakeIndex):
    doc, res = run(cut_doc, [{"op": "update_insert", "id": "i001",
                              "card": {"template": "list", "items": ["one", "two"]}}], take_index)
    ok(res)
    assert isinstance(doc.insert("i001").asset, CardSpec) and doc.insert("i001").asset.items == ["one", "two"]
    assert CutDocument.model_validate_json(doc.model_dump_json()) == doc


def test_segment_model_is_not_accepted_as_op_input(cut_doc: CutDocument, take_index: TakeIndex):
    seg = Segment(id="seg001", from_word="w0001", to_word="w0002").model_dump()
    rejected(run(cut_doc, [{"op": "set_story", "segments": [seg]}], take_index)[1], "id")


def test_asset_ids_resolve_through_the_job_registry(job: Job, cut_doc: CutDocument, take_index: TakeIndex):
    rejected(apply_ops(cut_doc, [{"op": "add_sfx", "kind": "pop", "anchor_word": "w0020", "asset_id": "px_42"}],
                       take_index)[1], "asset registry")
    job.register_asset(ASSETS["px_42"], asset_id="px_42")
    doc, res = apply_ops(cut_doc, [{"op": "add_insert", "anchor_from_word": "w0001", "anchor_to_word": "w0003",
                                    "asset_id": "px_42", "job": "illustrate"}], take_index, job=job)
    ok(res)
    assert doc.insert("i002").asset.licence.name == "Pexels License"
    rejected(apply_ops(cut_doc, [{"op": "add_insert", "anchor_from_word": "w0001", "anchor_to_word": "w0003",
                                  "asset_id": "px_43", "job": "x"}], take_index, job=job)[1], "unknown asset")
    doc2, res2 = apply_ops(cut_doc, [{"op": "add_sfx", "kind": "pop", "anchor_word": "w0020", "asset_id": "q"}],
                           take_index, assets=lambda aid: ASSETS["pop_01"] if aid == "q" else None)
    ok(res2)
    assert doc2.audio.sfx[-1].asset.id == "q"
