from __future__ import annotations

import json
from fractions import Fraction as F
from pathlib import Path

import pytest
from pydantic import ValidationError

from studio.compile.models import (
    FramingKey,
    Timeline,
    TimelineCaptionPage,
    TimelineCaptionWord,
    TimelineInsert,
    TimelineMusic,
    TimelineSegment,
    TimelineSfx,
    TimelineText,
    WordSpan,
)
from studio.doc.model import AssetRef, CardSpec, Licence, SeamTreatment
from studio.timebase import frame_time

NTSC = F(30000, 1001)


def fr(n: int, fps: F = NTSC) -> F:
    return frame_time(n, fps)


def make_timeline(fps: F = NTSC) -> Timeline:
    seg1 = TimelineSegment(seg_id="seg001", src_in_us=250_000, src_out_us=2_800_000, out_start=fr(0, fps),
                           out_end=fr(76, fps), speed=1.0, audio_src_in_us=250_000, audio_src_out_us=2_950_000,
                           framing=[FramingKey(out_t=fr(0, fps)), FramingKey(out_t=fr(30, fps), scale=1.2, cx=0.48,
                                                                             cy=0.33, ease="ease_in_out")],
                           word_ids=["w0001", "w0002"])
    seg2 = TimelineSegment(seg_id="seg002", src_in_us=5_700_000, src_out_us=7_400_000, out_start=fr(76, fps),
                           out_end=fr(118, fps), speed=1.2, audio_src_in_us=5_550_000, audio_src_out_us=7_400_000,
                           seam_in=SeamTreatment(kind="jcut", lead_ms=150), word_ids=["w0014"])
    return Timeline(
        fps=fps, duration=fr(118, fps), doc_version=3, job_id="j", source_path="/x/media/mezz.mov",
        segments=[seg1, seg2],
        inserts=[TimelineInsert(insert_id="i001", mode="card", out_start=fr(80, fps), out_end=fr(110, fps),
                                asset=CardSpec(template="stat", number="1", title="splice")),
                 TimelineInsert(insert_id="i002", mode="pip", out_start=fr(10, fps), out_end=fr(40, fps),
                                asset=AssetRef(source="pexels", licence=Licence(name="Pexels License")),
                                asset_path="/x/assets/broll/c.mov", rect=(0.6, 0.05, 0.35, 0.2))],
        captions=[TimelineCaptionPage(page_id="p001", out_start=fr(0, fps), out_end=fr(20, fps), y_norm=0.72,
                                      words=[TimelineCaptionWord(word_id="w0001", text="Most", out_start=fr(0, fps),
                                                                 out_end=fr(9, fps)),
                                             TimelineCaptionWord(word_id="w0002", text="people", emphasis=True,
                                                                 out_start=fr(9, fps), out_end=fr(20, fps))])],
        texts=[TimelineText(text_id="t001", kind="hook_title", text="Stop over-editing", out_start=fr(0, fps),
                            out_end=fr(60, fps))],
        sfx=[TimelineSfx(sfx_id="fx001", kind="pop", out_t=F(12345, 48000), gain_db=-10)],
        music=TimelineMusic(out_start=fr(0, fps), out_end=fr(118, fps), hits=[fr(76, fps)]),
        word_map={"w0001": WordSpan(out_start=fr(0, fps), out_end=fr(9, fps)), "w0009": None},
        seams=[fr(76, fps)],
    )


def test_json_roundtrip_uses_num_den_strings(tmp_path: Path):
    tl = make_timeline()
    js = tl.to_json()
    data = json.loads(js)
    assert data["fps"] == "30000/1001"
    assert data["duration"] == "59059/15000"  # 118 NTSC frames, reduced
    assert data["segments"][1]["out_start"] == f"{fr(76).numerator}/{fr(76).denominator}"
    assert data["sfx"][0]["out_t"] == "823/3200"  # 12345/48000 reduced
    assert data["word_map"]["w0009"] is None
    assert data["inserts"][0]["asset"]["type"] == "card"
    back = Timeline.from_json(js)
    assert back == tl
    assert isinstance(back.segments[0].out_end, F) and back.segments[0].out_end == fr(76)
    assert isinstance(back.inserts[0].asset, CardSpec) and isinstance(back.inserts[1].asset, AssetRef)
    assert back.inserts[1].rect == (0.6, 0.05, 0.35, 0.2)
    p = tl.save(tmp_path / "timeline.json")
    assert Timeline.load(p) == tl


def test_fraction_inputs_accept_strings_and_floats():
    tl = Timeline(fps="29.97", duration="0/1")
    assert tl.fps == NTSC and tl.duration == 0
    tl2 = Timeline(fps=30, duration=2.5)
    assert tl2.duration == F(5, 2) and tl2.frame_count == 75


def test_frame_and_sample_helpers():
    tl = make_timeline()
    assert tl.frame_count == 118
    assert tl.sample_count == round(118 * 1001 * 48000 / 30000)
    assert tl.frame_of(fr(76)) == 76
    assert tl.sample_of(fr(5)) == 8008
    assert tl.segment_at(fr(0)).seg_id == "seg001"
    assert tl.segment_at(fr(76)).seg_id == "seg002"  # half-open intervals
    assert tl.segment_at(fr(200)) is None
    assert tl.word_span("w0001").out_end == fr(9) and tl.word_span("w0009") is None
    assert tl.word_span("w0404") is None


def test_segment_properties():
    s = make_timeline().segments[1]
    assert s.out_duration == fr(42)
    assert s.src_duration_us == 1_700_000
    assert s.audio_lead_us == 150_000 and s.audio_lag_us == 0
    assert s.out_to_src_us(s.out_start) == s.src_in_us
    assert s.out_to_src_us(s.out_start + F(1)) == s.src_in_us + 1_200_000  # speed 1.2


def test_segment_validation():
    base = dict(seg_id="s", src_in_us=0, src_out_us=10, out_start=0, out_end=F(1, 30), audio_src_in_us=0,
                audio_src_out_us=10)
    TimelineSegment(**base)
    for bad in (dict(src_out_us=0), dict(out_end=0), dict(audio_src_out_us=0), dict(speed=3.0),
                dict(src_in_us=-5)):
        with pytest.raises(ValidationError):
            TimelineSegment(**{**base, **bad})
    with pytest.raises(ValidationError):
        FramingKey(out_t=0, scale=2.0)


def test_timeline_rejects_off_grid_and_overlapping_segments():
    tl = make_timeline()
    data = tl.model_dump()
    data["segments"][1]["out_start"] = "1/7"  # not on the 29.97 grid (and overlaps)
    with pytest.raises(ValidationError):
        Timeline.model_validate(data)
    data = tl.model_dump()
    data["duration"] = "1/3"
    with pytest.raises(ValidationError, match="frame grid"):
        Timeline.model_validate(data)
    data = tl.model_dump()
    data["seams"] = ["1/1000"]
    with pytest.raises(ValidationError, match="frame grid"):
        Timeline.model_validate(data)
    data = tl.model_dump()
    data["segments"][1]["out_start"] = data["segments"][0]["out_start"]
    with pytest.raises(ValidationError, match="overlaps"):
        Timeline.model_validate(data)
    with pytest.raises(ValidationError):
        Timeline(fps=30, duration=-1)


def test_grid_violations_reports_overlays_but_exempts_sfx():
    tl = make_timeline()
    assert tl.grid_violations() == []
    tl.inserts[0].out_end = F(1, 7)
    tl.captions[0].out_start = F(1, 11)
    tl.texts[0].out_end = F(1, 13)
    v = tl.grid_violations()
    assert "insert i001.out_end" in v and any(x.startswith("caption page") for x in v) and "text t001" in v
    assert not any("sfx" in x for x in v)
    assert tl.on_grid(fr(3)) and not tl.on_grid(F(1, 7))


def test_integer_fps_grid():
    tl = Timeline(fps=30, duration=F(2), segments=[TimelineSegment(
        seg_id="seg001", src_in_us=0, src_out_us=2_000_000, out_start=0, out_end=2, audio_src_in_us=0,
        audio_src_out_us=2_000_000)], seams=[F(1)])
    assert tl.frame_count == 60 and tl.sample_count == 96_000


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Timeline(fps=30, duration=1, bogus=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TimelineCaptionPage(words=[], out_start=0, out_end=1)
