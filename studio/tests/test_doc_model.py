from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.doc.model import (
    AssetRef,
    AudioPlan,
    Brief,
    CaptionPage,
    CaptionPlan,
    CardSpec,
    ColorSpec,
    CutDocument,
    Deliverable,
    Framing,
    Insert,
    Licence,
    MusicSpec,
    Pins,
    Point,
    SeamTreatment,
    Segment,
    Style,
    StyleDials,
    format_id,
    id_number,
    new_document,
)
from studio.perception.index import TakeIndex


def test_defaults():
    d = new_document("job1", created_by="claude-fable-5-1", platforms=("tiktok", "reels"))
    assert d.version == 0 and d.parent_version is None
    assert d.job_id == "job1" and d.created_by == "claude-fable-5-1"
    assert d.segments == [] and d.removed == [] and d.inserts == [] and d.texts == []
    assert d.captions is None and d.color is None and d.brief is None
    assert d.audio.loudness_target_lufs == -14.0
    assert d.audio.true_peak_dbtp == -1.0
    assert d.audio.room_tone is True
    assert d.audio.voice.hpf_hz == 80.0
    assert [x.platform for x in d.deliverables] == ["tiktok", "reels"]
    assert CutDocument().deliverables == [Deliverable(platform="tiktok", variant="main")]
    assert d.style.dials.energy == 0.5


def test_roundtrip_json(cut_doc: CutDocument):
    js = cut_doc.model_dump_json()
    back = CutDocument.model_validate_json(js)
    assert back == cut_doc
    assert isinstance(back.inserts[0].asset, CardSpec)


def test_extra_fields_forbidden_everywhere():
    with pytest.raises(ValidationError):
        CutDocument(bogus=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Segment(id="seg001", from_word="w0001", to_word="w0002", start_s=1.2)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Brief(goal="x", timestamp=3)  # type: ignore[call-arg]


@pytest.mark.parametrize("kwargs", [
    dict(id="s1", from_word="w0001", to_word="w0002"),
    dict(id="seg001", from_word="word1", to_word="w0002"),
    dict(id="seg001", from_word="w0001", to_word="w0002", speed=2.5),
    dict(id="seg001", from_word="w0001", to_word="w0002", speed=0.4),
    dict(id="seg001", from_word="w0001", to_word="w0002", gap_overrides={"x1": 100}),
    dict(id="seg001", from_word="w0001", to_word="w0002", gap_overrides={"g0001": -5}),
])
def test_segment_validation(kwargs):
    with pytest.raises(ValidationError):
        Segment(**kwargs)


def test_framing_and_seam_ranges():
    Framing(scale=1.8, center=Point(x=0.5, y=0.3))
    Framing(scale=1.0, center="face", anchor_word="w0003", ease="smooth")
    for bad in (dict(scale=1.9), dict(scale=0.9), dict(center="nose"), dict(center={"x": 1.2, "y": 0.5}),
                dict(ease="bounce")):
        with pytest.raises(ValidationError):
            Framing(**bad)
    SeamTreatment(kind="jcut", lead_ms=200)
    with pytest.raises(ValidationError):
        SeamTreatment(kind="wipe")
    with pytest.raises(ValidationError):
        SeamTreatment(kind="lcut", lead_ms=5000)


def test_insert_licence_helpers():
    lic = Licence(name="Pexels License", source="pexels", url="https://www.pexels.com/license/")
    a = AssetRef(source="pexels", source_id="123", path="assets/broll/px_123.mp4", licence=lic)
    ins = Insert(id="i001", anchor_from_word="w0001", anchor_to_word="w0002", asset=a, job="show the thing")
    assert ins.needs_licence and ins.effective_licence == lic
    ins2 = Insert(id="i002", anchor_from_word="w0001", anchor_to_word="w0002",
                  asset=AssetRef(source="creator"), job="x", licence=Licence(name="creator-owned"))
    assert ins2.effective_licence.name == "creator-owned"
    card = Insert(id="i003", anchor_from_word="w0001", anchor_to_word="w0002", mode="card",
                  asset={"type": "card", "template": "number", "number": "3"}, job="count")
    assert isinstance(card.asset, CardSpec) and not card.needs_licence and card.effective_licence is None
    with pytest.raises(ValidationError):
        Insert(id="i004", anchor_from_word="w0001", anchor_to_word="w0002", asset=a, job="")
    with pytest.raises(ValidationError):
        Insert(id="i005", anchor_from_word="w0001", anchor_to_word="w0002", asset={"type": "gif"}, job="x")
    with pytest.raises(ValidationError):
        Licence(name="")


def test_caption_page_validation():
    CaptionPage(word_ids=["w0001", "w0002"], emphasis_word_ids=["w0002"])
    with pytest.raises(ValidationError):
        CaptionPage(word_ids=[])
    with pytest.raises(ValidationError):
        CaptionPage(word_ids=["w0001"], emphasis_word_ids=["w0002"])
    with pytest.raises(ValidationError):
        CaptionPage(word_ids=["w0001", "w0001"])
    with pytest.raises(ValidationError):
        CaptionPage(id="page1", word_ids=["w0001"])


def test_audio_and_color_ranges():
    with pytest.raises(ValidationError):
        AudioPlan(true_peak_dbtp=-0.5)  # invariant 8 ceiling
    with pytest.raises(ValidationError):
        AudioPlan(loudness_target_lufs=-5)
    AudioPlan(loudness_target_lufs=-16.0, true_peak_dbtp=-1.5)
    with pytest.raises(ValidationError):
        MusicSpec(level_lu_under_speech=0)
    ColorSpec(exposure=-0.3, white_balance_k=5600, saturation=1.1, look="warm_film", lut_strength=0.6)
    with pytest.raises(ValidationError):
        ColorSpec(exposure=3)
    with pytest.raises(ValidationError):
        ColorSpec(white_balance_k=1000)


def test_style_dials():
    s = Style(primary="educational", blend=["storytime"], dials=StyleDials(energy=0.8, extra=[
        {"name": "warmth", "value": 0.7}]))
    assert s.dials.get("energy") == 0.8 and s.dials.get("warmth") == 0.7 and s.dials.get("zzz") is None
    with pytest.raises(ValidationError):
        StyleDials(pace=1.2)
    with pytest.raises(ValidationError):
        StyleDials(extra=[{"name": "x", "value": 2}])


def test_pins_helpers():
    p = Pins(must_keep_word_ids=["w0001", "w0018"], payoff_word_ids=["w0018"], cta_word_ids=["w0032"])
    assert p.of_kind("cta") == ["w0032"]
    assert p.all() == {"w0001": "must_keep", "w0018": "payoff", "w0032": "cta"}
    with pytest.raises(ValidationError):
        Pins(payoff_word_ids=["18"])


def test_unique_ids_enforced(cut_doc: CutDocument):
    data = cut_doc.model_dump()
    data["segments"][1]["id"] = "seg001"
    with pytest.raises(ValidationError, match="duplicate segment id"):
        CutDocument.model_validate(data)
    data = cut_doc.model_dump()
    data["captions"]["pages"][1]["id"] = data["captions"]["pages"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate caption page id"):
        CutDocument.model_validate(data)


def test_lookups_and_story_views(cut_doc: CutDocument, take_index: TakeIndex):
    d, ix = cut_doc, take_index
    assert d.segment("seg002").from_word == "w0014" and d.segment_index("seg003") == 2
    assert d.insert("i001").mode == "card" and d.text("t001").kind == "hook_title"
    assert d.sfx("fx001").kind == "pop" and d.caption_page("p001").word_ids[0] == "w0001"
    for fn, arg in ((d.segment, "seg999"), (d.insert, "i999"), (d.text, "t999"), (d.sfx, "fx999"),
                    (d.caption_page, "p999"), (d.segment_index, "seg999")):
        with pytest.raises(KeyError):
            fn(arg)
    kept = d.kept_word_ids(ix)
    assert kept[:8] == ix.word_ids("w0001", "w0008") and kept[8] == "w0014"
    assert "w0009" not in kept and "w0019" not in kept
    assert len(kept) == 41 - 5 - 1
    pos = d.output_positions(ix)
    assert pos["w0014"] == 8 and pos["w0020"] == 13
    assert d.segment_of_word("w0016", ix).id == "seg002"
    assert d.segment_of_word("w0010", ix) is None
    assert d.removed_word_ids(ix) == ["w0009", "w0010", "w0011", "w0012", "w0013", "w0019"]
    assert d.segment_word_ids("seg002", ix) == ix.word_ids("w0014", "w0018")


def test_estimated_duration_respects_speed_and_gaps(cut_doc: CutDocument, take_index: TakeIndex):
    ix = take_index
    base = cut_doc.estimated_duration_us(ix)
    spans = sum(ix.word(s.to_word).end_us - ix.word(s.from_word).start_us for s in cut_doc.segments)
    # seg003 → seg004 continue the source (w0028 | w0029): the compiler plays g0009 between them
    assert base == spans + ix.gap("g0009").duration_us
    d2 = cut_doc.model_copy(deep=True)
    d2.segments[1].gap_overrides = {"g0005": 20}  # 120 ms → 20 ms
    assert d2.estimated_duration_us(ix) == base - 100_000
    d2.segments[3].speed = 2.0
    seg4 = ix.word("w0041").end_us - ix.word("w0029").start_us
    assert d2.estimated_duration_us(ix) == base - 100_000 - seg4 // 2


def test_render_story(cut_doc: CutDocument, take_index: TakeIndex):
    d = cut_doc.model_copy(deep=True)
    d.segments[1].framing = Framing(scale=1.25)
    d.segments[1].gap_overrides = {"g0005": 60}
    d.segments[2].seam_in = SeamTreatment(kind="jcut", lead_ms=180)
    txt = d.render_story(take_index)
    lines = txt.splitlines()
    assert lines[0] == "seg001 (w0001-w0008) speed 1.00 · seam cut"
    assert "seg002 (w0014-w0018) speed 1.00 · seam cut · framing x1.25 face cut · gaps g0005=60ms" in txt
    assert "seg003 (w0020-w0028) speed 1.00 · seam jcut 180ms" in txt
    assert "g0001" not in txt and "g0011" not in txt
    full = d.render_story(take_index, view="full")
    assert "w0014 The" in full


def test_id_helpers():
    assert format_id("seg", 3) == "seg003" and format_id("i", 12) == "i012" and format_id("h", 2) == "h02"
    assert format_id("fx", 1) == "fx001" and format_id("p", 1000) == "p1000"
    assert id_number("seg012") == 12 and id_number("x") is None


def test_caption_plan_defaults():
    plan = CaptionPlan()
    assert plan.enabled and plan.pages == [] and plan.style.font == "Montserrat"
    assert plan.style.max_words_per_page == 4 and plan.style.highlight_lead_frames == 2
