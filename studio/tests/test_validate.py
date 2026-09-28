from __future__ import annotations

import pytest

from studio.doc.model import (
    AssetRef,
    Brief,
    CaptionPage,
    CaptionPlan,
    CutDocument,
    Framing,
    HookAlternate,
    Insert,
    Licence,
    MusicSpec,
    SeamTreatment,
    Segment,
    SfxCue,
    TextOverlay,
    WordRange,
)
from studio.doc.validate import (
    Finding,
    errors,
    format_findings,
    has_errors,
    validate_document,
)
from studio.jobs import Job
from studio.perception.index import TakeIndex

LIC = Licence(name="Pexels License")


def codes(fs: list[Finding], level: str | None = None) -> set[str]:
    return {f.code for f in fs if level is None or f.level == level}


def test_fixture_document_is_clean(cut_doc: CutDocument, take_index: TakeIndex):
    fs = validate_document(cut_doc, take_index)
    assert not has_errors(fs), format_findings(fs)
    assert codes(fs) == {"estimated_duration"}
    est = next(f for f in fs if f.code == "estimated_duration")
    assert est.level == "info" and "over 4 segments" in est.message


def test_empty_story(take_index: TakeIndex):
    fs = validate_document(CutDocument(), take_index)
    assert "empty_story" in codes(fs, "error")


def test_segment_structure_errors(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments.append(Segment(id="seg009", from_word="w0005", to_word="w0006"))  # reuses words
    d.segments.append(Segment(id="seg010", from_word="w0012", to_word="w0010"))  # reversed
    d.segments.append(Segment(id="seg011", from_word="w0001", to_word="w9999"))  # unknown
    fs = validate_document(d, take_index)
    e = codes(fs, "error")
    assert {"word_reused", "segment_reversed", "segment_unknown_word"} <= e
    reused = next(f for f in fs if f.code == "word_reused")
    assert "seg001" in reused.refs and "seg009" in reused.refs


def test_two_sources(take_index: TakeIndex, cut_doc: CutDocument):
    ix = take_index.model_copy(deep=True)
    for w in ix.words[20:]:
        w.source = "take2"
    fs = validate_document(cut_doc, ix)
    assert "segment_two_sources" in codes(fs, "error")  # seg003 w0020-w0028 now spans sources


def test_gap_override_and_framing_anchor(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[0].gap_overrides = {"g0005": 50}  # g0005 is inside seg002, not seg001
    d.segments[1].gap_overrides = {"g0005": 500}  # longer than measured 120 ms
    d.segments[2].framing = Framing(scale=1.2, anchor_word="w0035")
    fs = validate_document(d, take_index)
    assert {"gap_override_outside", "gap_override_longer", "framing_anchor_outside"} <= codes(fs, "error")


def test_seam_warnings(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[0].seam_in = SeamTreatment(kind="jcut", lead_ms=100)
    d.segments[2].seam_in = SeamTreatment(kind="lcut", lead_ms=0)
    fs = validate_document(d, take_index)
    assert {"first_seam", "seam_no_lead"} <= codes(fs, "warning")
    assert not has_errors(fs)


def test_pinned_words_missing_is_invariant_4(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments = [s for s in d.segments if s.id != "seg002"]  # drops payoff w0018
    d.pins.must_keep_word_ids = ["w9999"]
    fs = validate_document(d, take_index)
    miss = [f for f in fs if f.code == "pinned_word_missing"]
    assert miss and miss[0].refs == ["w0018"] and "invariant 4" in miss[0].message
    assert "pin_unknown_word" in codes(fs, "error")


def test_insert_checks(cut_doc: CutDocument, take_index: TakeIndex, job: Job):
    d = cut_doc.model_copy(deep=True)
    d.inserts += [
        Insert(id="i002", anchor_from_word="w0001", anchor_to_word="w0003", asset=AssetRef(source="pexels",
               path="assets/broll/missing.mp4"), job="x"),  # no licence + missing file
        Insert(id="i003", anchor_from_word="w0022", anchor_to_word="w0026", asset=AssetRef(source="creator",
               licence=Licence(name="creator-owned"), url="https://x"), job="x"),  # full over card i001
        Insert(id="i004", anchor_from_word="w0010", anchor_to_word="w0012", mode="card",
               asset=AssetRef(source="x", licence=LIC, url="u"), job="x"),  # cut anchors + card mode w/ asset
        Insert(id="i005", anchor_from_word="w0030", anchor_to_word="w0029", asset={"type": "card"}, mode="pip",
               job="x"),  # reversed
        Insert(id="i006", anchor_from_word="w0035", anchor_to_word="w0036", mode="pip", job="x",
               asset=AssetRef(source="creator", licence=LIC)),  # no path/url (job given)
    ]
    fs = validate_document(d, take_index, job=job)
    e = codes(fs, "error")
    assert {"missing_licence", "asset_missing", "fullscreen_overlap", "anchor_not_kept", "card_mode_asset",
            "anchor_order", "asset_unlocated"} <= e
    overlap = next(f for f in fs if f.code == "fullscreen_overlap")
    assert set(overlap.refs) == {"i001", "i003"}
    # without a job the file checks are skipped
    assert "asset_missing" not in codes(validate_document(d, take_index))


def test_asset_present_in_job_is_ok(cut_doc: CutDocument, take_index: TakeIndex, job: Job):
    (job.assets_dir / "broll" / "px_1.mp4").write_bytes(b"\x00")
    d = cut_doc.model_copy(deep=True)
    d.inserts.append(Insert(id="i002", anchor_from_word="w0001", anchor_to_word="w0003",
                            asset=AssetRef(source="pexels", path="assets/broll/px_1.mp4", licence=LIC), job="show"))
    assert not has_errors(validate_document(d, take_index, job=job))


def test_insert_non_fullscreen_overlap_is_warning(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.inserts.append(Insert(id="i002", anchor_from_word="w0024", anchor_to_word="w0026", mode="pip",
                            asset=AssetRef(source="creator", licence=LIC, url="u"), job="x"))
    fs = validate_document(d, take_index)
    assert "insert_overlap" in codes(fs, "warning") and not has_errors(fs)


def test_text_caption_sfx_music_anchors(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.texts.append(TextOverlay(id="t002", text="x", anchor_from_word="w0019", anchor_to_word="w0020"))
    d.captions.pages.append(CaptionPage(id="p099", word_ids=["w0012"]))
    d.captions.pages.append(CaptionPage(id="p100", word_ids=["w0001"]))  # out of order at the end
    d.audio.sfx.append(SfxCue(id="fx002", kind="pop", anchor_word="w0009"))
    d.audio.sfx.append(SfxCue(id="fx003", kind="pop", anchor_word="w0020", asset=AssetRef(source="x")))
    d.audio.music = MusicSpec(asset=AssetRef(source="x", kind="audio"), start_word="w0030", end_word="w0020",
                              hit_word_ids=["w0011"])
    fs = validate_document(d, take_index)
    e = codes(fs, "error")
    assert {"anchor_not_kept", "caption_word_cut", "caption_order", "missing_licence", "anchor_order"} <= e
    msgs = format_findings(fs)
    assert "text t002" in msgs and "sfx fx002" in msgs and "music anchor w0011" in msgs


def test_caption_coverage_and_length(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.captions.pages = d.captions.pages[:2]
    d.captions.pages[0] = CaptionPage(id="p001", word_ids=["w0001", "w0002", "w0003", "w0004", "w0005"])
    fs = validate_document(d, take_index)
    assert {"uncaptioned_words", "caption_long_page"} <= codes(fs, "warning")
    d.captions = CaptionPlan(pages=[])
    assert "captions_no_pages" in codes(validate_document(d, take_index), "warning")
    d.captions = CaptionPlan(enabled=False, pages=[])
    assert "captions_no_pages" not in codes(validate_document(d, take_index))


def test_loudness_and_true_peak_invariant_8(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.audio.true_peak_dbtp = -0.2  # bypass model validation (hand-edited document)
    d.audio.loudness_target_lufs = -30
    fs = validate_document(d, take_index)
    assert {"true_peak_ceiling", "loudness_target"} <= codes(fs, "error")


def test_hooks_deliverables_and_length(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.hook_alternates = [HookAlternate(id="h01", ranges=[WordRange(from_word="w0012", to_word="w0010")]),
                         HookAlternate(id="h02", ranges=[WordRange(from_word="w0001", to_word="w0999")])]
    d.deliverables = []
    d.brief = Brief(target_length_s=5)
    fs = validate_document(d, take_index)
    assert {"hook_reversed", "hook_unknown_word"} <= codes(fs, "error")
    assert "no_deliverables" in codes(fs, "warning")
    d.hook_alternates = []
    fs = validate_document(d, take_index)
    assert "over_length" in codes(fs, "warning")


def test_insert_without_job_text_warns(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.inserts[0].job = "   "
    assert "insert_no_job" in codes(validate_document(d, take_index), "warning")


def test_helpers():
    fs = [Finding(level="warning", code="a", message="m"), Finding(level="error", code="b", message="n", refs=["x"])]
    assert has_errors(fs) and [f.code for f in errors(fs)] == ["b"]
    assert format_findings(fs) == "[warning] a: m\n[error] b: n (x)"
    assert not has_errors([])


@pytest.mark.parametrize("bad_field", ["from_word", "to_word"])
def test_never_raises_on_unknown_ids(cut_doc: CutDocument, take_index: TakeIndex, bad_field: str):
    d = cut_doc.model_copy(deep=True)
    setattr(d.segments[1], bad_field, "w8888")
    d.inserts[0].anchor_from_word = "w7777"
    fs = validate_document(d, take_index)
    assert has_errors(fs)
