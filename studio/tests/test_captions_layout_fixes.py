"""Caption/framing fixes from the E2E runs: syntax-aware line breaks handed to the renderer, phrase pages that split
instead of wrapping, light-verb accents, the under-the-chin reframe hint, punch-ins that zoom about the head top when
captions sit above it, and a steady frame 0."""

from __future__ import annotations

from fractions import Fraction

import pytest

from studio.compile import captions as C
from studio.compile.overlays import build_overlay_props
from studio.compile.timeline import compile as compile_timeline
from studio.compile.timeline import map_source_point
from studio.doc.model import CaptionPlan, CaptionStyle, CutDocument, Framing, Segment
from studio.perception.index import FaceBox, TakeIndex


def _two_line(text: str, width: float = 690.0, size: int = 76) -> C.CaptionFit:
    st = CaptionStyle(size_px=size, lines=2, weight=800)  # real-take40's style: ~16 characters per 690 px line
    return C.caption_fit(st, text, 1080, max_width=width)


@pytest.mark.parametrize("text,bad", [
    ("survive the marriage?", "survive the / marriage?"),
    ("If I put butter chicken", "If I put butter / chicken"),
    ("then you need a bridge,", "then you / need a bridge,"),
    ("Fusion is an addition.", "Fusion is an / addition."),
    ("through three questions", "through three / questions"),
])
def test_two_line_pages_break_between_phrases_not_inside_them(text: str, bad: str) -> None:
    fit = _two_line(text)
    assert fit.lines == 2, fit
    assert fit.display.upper() != bad.upper(), fit.display
    assert len(fit.breaks) == 1 and fit.display.count("/") == 1
    first, second = fit.line_texts
    bad_at = len(bad.split(" / ")[0].split())
    assert C.line_break_cost(text.split(), fit.breaks[0]) < C.line_break_cost(text.split(), bad_at), fit.display
    assert (first + " " + second).lower() == text.lower()


def test_line_break_cost_lexicon() -> None:
    w = ["survive", "the", "marriage?"]
    assert C.line_break_cost(w, 2) == 3.0 and C.line_break_cost(w, 1) < 1.0
    w = ["If", "I", "put", "butter", "chicken"]
    assert C.line_break_cost(w, 3) == 0.6 and C.line_break_cost(w, 4) == 1.8
    assert C.line_break_cost(["staring", "at", "their", "own", "life,", "trying"], 5) == 0.0  # after a comma
    assert C.line_break_cost(["Fusion", "is", "an", "addition."], 2) == 1.0  # after the copula, before its NP


def test_overlay_props_carry_the_planned_breaks(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    style = CaptionStyle(size_px=96, lines=2, weight=900)
    doc = cut_doc.model_copy(update={"captions": cut_doc.captions.model_copy(update={"style": style})})
    tl = compile_timeline(doc, take_index)
    props = build_overlay_props(tl, index=take_index, platforms=["tiktok"])
    for pg, cp in zip(sorted(tl.captions, key=lambda p: p.out_start), props.captions, strict=True):
        fit = C.caption_fit(pg.style, pg.text or " ".join(w.text for w in pg.words), tl.width,
                            max_width=tl.width - C.safe_zone_for(["tiktok"]).left - C.safe_zone_for(["tiktok"]).right)
        assert cp.lines == fit.lines
        assert (cp.breaks or []) == list(fit.breaks)


def test_phrase_pages_of_three_words_or_fewer_never_wrap(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    style = CaptionStyle(size_px=120, lines=1, weight=900)  # huge text: short pages would need two lines
    plan = C.auto_caption_plan(cut_doc, take_index, style=style)
    for pg in plan.pages:
        words = [take_index.word(w).text for w in pg.word_ids]
        fit = C.caption_fit(style, " ".join(words), 1080)
        if len(words) <= 3 and len(words) > 1:
            assert fit.lines == 1, (words, fit)


def test_light_verbs_get_no_accent(take_index: TakeIndex) -> None:
    w = take_index.word("w0020").model_copy(update={"text": "build", "emphasis": 0.95})
    tok = C._Tok(wid=w.id, text="build", start=Fraction(0), end=Fraction(1, 2), word=w, seg=0)
    assert C._accent_score(tok, C.CaptionParams()) == 0.0
    tok2 = C._Tok(wid=w.id, text="proof", start=Fraction(0), end=Fraction(1, 2),
                  word=w.model_copy(update={"text": "proof"}), seg=0)
    assert C._accent_score(tok2, C.CaptionParams()) > 0.7


# ---------------------------------------------------------------------------------------------- above the head
def _low_face(ix: TakeIndex, cy: float = 0.62, h: float = 0.3) -> TakeIndex:
    """A close selfie: the chin sits under the platform's caption band (like real-take40 / var-silences)."""
    vis = ix.visual
    samples = [s.model_copy(update={"face_box": FaceBox.from_center(s.face_box.cx, cy, 0.4, h)})
               for s in vis.samples]
    track = vis.face_track.model_copy(update={"points": [p.model_copy(update={"cy": cy, "h": h, "w": 0.4})
                                                         for p in vis.face_track.points]})
    return ix.model_copy(update={"visual": vis.model_copy(update={"samples": samples, "face_track": track,
                                                                  "head_top_ratio": 0.5})})


def test_under_chin_reframe_hint(take_index: TakeIndex) -> None:
    zone = C.safe_zone_for(["reels"])
    ok = C.under_chin_reframe(take_index, style=CaptionStyle(), zone=zone)
    assert ok is not None and ok["scale"] == 1.0 and ok["chin_px"] < ok["target_px"]
    low = C.under_chin_reframe(_low_face(take_index), style=CaptionStyle(), zone=zone)
    assert low is not None and low["chin_px"] > low["target_px"] and low["scale"] > 1.2
    # the formula: with the crop pushed to the bottom, the chin lands on the target line at that scale
    s, src_h = low["scale"], take_index.media.height
    chin = (0.62 + 0.15) * src_h
    y0 = src_h - src_h / s
    assert (chin - y0) / (src_h / s) * 1920 == pytest.approx(low["target_px"], abs=1.0)


def _head_top_out(tl, ix: TakeIndex, t: Fraction) -> float:
    fb = ix.face_at(tl.segment_at(t).out_to_src_us(t))
    ht = ix.visual.head_top_y(fb)
    return map_source_point(tl, t, fb.cx, ht, ix.media.width, ix.media.height)[1] * tl.height


def test_punch_ins_zoom_about_the_head_top_when_captions_sit_above_it(take_index: TakeIndex) -> None:
    ix = _low_face(take_index)
    segs = [Segment(id="seg001", from_word="w0020", to_word="w0028"),
            Segment(id="seg002", from_word="w0029", to_word="w0041", framing=Framing(scale=1.25))]
    doc = CutDocument(version=1, job_id="j", segments=segs, counters={"seg": 2})
    tl = compile_timeline(doc, ix)
    a = tl.word_map["w0024"].out_start
    b = tl.word_map["w0036"].out_start
    base, punched = _head_top_out(tl, ix, a), _head_top_out(tl, ix, b)
    assert abs(punched - base) <= 6.0, (base, punched)
    # without captions the punch keeps the face (fixation) and the head top rises
    off = doc.model_copy(update={"captions": CaptionPlan(enabled=False)})
    tl2 = compile_timeline(off, ix)
    assert _head_top_out(tl2, ix, tl2.word_map["w0036"].out_start) < base - 30


def test_frame_zero_skips_a_soft_frame(take_index: TakeIndex) -> None:
    # the story opens on w0014, after the 900 ms silence g0004: the lead-in window is [onset-0.5, onset-0.1] s
    doc = CutDocument(version=1, job_id="j", segments=[Segment(id="seg001", from_word="w0014", to_word="w0018")],
                      counters={"seg": 1})
    onset = take_index.word("w0014").start_us
    base = compile_timeline(doc, take_index).segments[0].src_in_us
    assert onset - 300_000 < base < onset - 100_000
    vis = take_index.visual
    soft = [q.model_copy(update={"blur": 0.6 if onset - 350_000 <= q.t_us <= onset - 100_000 else 0.1})
            for q in vis.samples]
    ix = take_index.model_copy(update={"visual": vis.model_copy(update={"samples": soft})})
    tl = compile_timeline(doc, ix)
    start = tl.segments[0].src_in_us
    assert start < onset - 380_000 and start >= onset - 500_000  # the nearest sharp frame in the window
    assert 0.1 <= float(tl.word_map["w0014"].out_start) <= 0.5
    # a steady opening is left alone
    steady = [q.model_copy(update={"blur": 0.1}) for q in vis.samples]
    ix2 = take_index.model_copy(update={"visual": vis.model_copy(update={"samples": steady})})
    assert compile_timeline(doc, ix2).segments[0].src_in_us == base


def test_the_last_frame_does_not_catch_the_mouth_open(take_index: TakeIndex) -> None:
    doc = CutDocument(version=1, job_id="j", segments=[Segment(id="seg001", from_word="w0029", to_word="w0041")],
                      counters={"seg": 1})
    end = take_index.word("w0041").end_us
    base = compile_timeline(doc, take_index).segments[-1].src_out_us
    vis = take_index.visual
    talky = [q.model_copy(update={"mouth_open": 0.8 if q.t_us <= base - 20_000 else 0.05}) for q in vis.samples]
    ix = take_index.model_copy(update={"visual": vis.model_copy(update={"samples": talky})})
    out = compile_timeline(doc, ix).segments[-1].src_out_us
    assert out > base and out - end >= 150_000
    # a closed mouth already: unchanged
    shut = [q.model_copy(update={"mouth_open": 0.1}) for q in vis.samples]
    ix2 = take_index.model_copy(update={"visual": vis.model_copy(update={"samples": shut})})
    assert compile_timeline(doc, ix2).segments[-1].src_out_us == base


def test_a_recording_that_stops_on_the_last_word_gets_a_held_tail(take_index: TakeIndex) -> None:
    from studio.compile.video import source_frame_map

    last = take_index.word("w0041")
    media = take_index.media.model_copy(update={"duration_us": last.end_us + 30_000})
    gaps = [g for g in take_index.gaps if g.before_word_id is not None]  # no trailing gap: the file ends
    ix = take_index.model_copy(update={"media": media, "gaps": gaps})
    doc = CutDocument(version=1, job_id="j", segments=[Segment(id="seg001", from_word="w0029", to_word="w0041")],
                      counters={"seg": 1})
    tl = compile_timeline(doc, ix)
    seg = tl.segments[-1]
    tail = float(tl.duration - tl.word_map["w0041"].out_end)
    assert 0.15 <= tail <= 0.5
    assert seg.src_out_us > media.duration_us and seg.audio_src_out_us <= media.duration_us + 1
    # the picture past the source end repeats the last source frame
    n_src = media.frame_count
    fmap = source_frame_map(tl, src_frames=n_src)
    assert fmap[-1][1] == n_src - 1 and len(fmap) == tl.frame_count
