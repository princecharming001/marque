"""Caption size and placement on a real face track (keyless, offline).

Regression for the first real edit (job 20260928-100903-90b336, qa-editor-d030-h264.mov on TikTok): captions
rendered at ~40 px cap height (72 px pages shrunk to ~52 px) and sat on the hair over the forehead
(y_norm ≈ 0.33). The creator filmed a close-up selfie: the chin is at y ≈ 1420–1620, below TikTok's strict
band floor (y 1440), so "just below the chin" cannot fit, and the doctrine's fallbacks (smaller text, the
lower band) cannot either. The captions must then sit above the head, clear of the measured hair, at
legible size, inside the band, never over eyes or mouth, and at one height while the head bobs.

``captions_d030_face.json`` is that job's Take Index without per-sample visuals (the smoothed landmark face
track is kept), plus the head tops measured on its mezzanine by ``studio.perception.visual.measure_head_top``,
and the Director's caption plan from the delivered document (v15).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.compile import captions as C
from studio.compile.timeline import compile as compile_timeline
from studio.doc.model import CaptionPlan, CutDocument, Deliverable, Segment
from studio.perception.index import TakeIndex

FIXTURE = Path(__file__).with_name("captions_d030_face.json")
TIKTOK = C.PLATFORM_SAFE_ZONES["tiktok"]
BAND_TOP, BAND_BOTTOM = TIKTOK.top, 1920 - TIKTOK.bottom


@pytest.fixture(scope="module")
def d030() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _index(d: dict, *, measured: bool = True) -> TakeIndex:
    data = {k: v for k, v in d.items() if k not in ("source", "director_captions_v15", "segments_v15")}
    ix = TakeIndex.model_validate(data)
    if not measured:
        ix = ix.model_copy(update={"visual": ix.visual.model_copy(update={"head_top_ratio": None, "head_tops": []})})
    return ix


def _doc(d: dict, captions: CaptionPlan | None) -> CutDocument:
    segs = [Segment.model_validate(s) for s in d["segments_v15"]]
    return CutDocument(version=1, segments=segs, captions=captions, deliverables=[Deliverable(platform="tiktok")])


def _faces(tl, ix, pg):
    return C._faces_during(tl, ix, C.to_fraction(pg.out_start), C.to_fraction(pg.out_end), 0.1)


def _block(pg, W: int = 1080) -> tuple[float, float, float, float, C.CaptionFit]:
    text = pg.text or " ".join(w.text for w in pg.words)
    fit = C.caption_fit(pg.style, text, W, max_width=W - TIKTOK.left - TIKTOK.right)
    cx = min(max(W / 2, TIKTOK.left + fit.width / 2), W - TIKTOK.right - fit.width / 2)
    cy = pg.y_norm * 1920
    return cx - fit.width / 2, cy - fit.height / 2, cx + fit.width / 2, cy + fit.height / 2, fit


def _check_pages(tl, ix, *, min_px: float = 64.0, hair_ok: bool = True) -> list[float]:
    """Every page: legible size, inside TikTok's band, never over eyes or mouth, and clear of the hair (grazing
    its very top allowed) — except pages the placer had to settle for (``least_bad``: the head rose so high
    that no room is left above it), which may sit on the top of the hair but never lower than the brows'
    band; with ``hair_ok`` there are at most two of those."""
    params = C.CaptionParams()
    tops = []
    assert tl.captions
    for pg in tl.captions:
        x0, y0, x1, y1, fit = _block(pg)
        tops.append(round(y0))
        assert fit.size_px >= min_px - 0.5, (pg.page_id, fit)  # legible on a phone: never small print
        assert y0 >= BAND_TOP - 0.5 and y1 <= BAND_BOTTOM + 0.5, (pg.page_id, y0, y1)  # inside TikTok's band
        for f in _faces(tl, ix, pg):
            prot = f.protected(params)
            assert C._overlap((x0, y0, x1, y1), prot) == 0, (pg.page_id, "covers eyes/mouth")
            if pg.placement not in ("least_bad", "on_hair"):
                assert y1 <= f.head_top(params) + params.head_overlap_tol_frac * f.h + 0.5, (
                    pg.page_id, pg.placement, y1, f.head_top(params))
            else:  # the head rose into a held caption: on the hair for a moment, never on the forehead
                assert y1 <= f.forehead + 0.5, (pg.page_id, "on the forehead")
    issues = C.caption_placement_issues(tl, ix, platform="tiktok")
    hard = [i for i in issues if i["kind"] in ("covers_face", "outside_safe_zone", "too_wide", "below_size_floor")]
    assert hard == []
    if hair_ok:
        assert sum(pg.placement in ("least_bad", "on_hair") for pg in tl.captions) <= 4
    return tops


def test_d030_face_is_a_close_up_whose_chin_sits_below_the_band(d030: dict):
    """The premise: below-the-chin placement cannot fit this footage on TikTok."""
    ix = _index(d030)
    tl = compile_timeline(_doc(d030, None), ix)
    # on every page, even the floor size cannot sit 40 px under the chin (the lowest chin while it is up)
    for pg in tl.captions:
        assert max(f.chin for f in _faces(tl, ix, pg)) + 40 + 64 > BAND_BOTTOM, pg.page_id
    assert ix.visual.head_top_ratio == pytest.approx(0.5, abs=0.05)  # measured hair above the landmark box


def test_d030_auto_captions_sit_above_the_hair_at_legible_size(d030: dict):
    ix = _index(d030)
    tl = compile_timeline(_doc(d030, None), ix)
    tops = _check_pages(tl, ix)
    kinds = [pg.placement for pg in tl.captions]
    assert set(kinds) <= {"above_head", "on_hair"} and kinds.count("above_head") >= len(kinds) - 4
    # the house size throughout: when the head rises into the held caption (y ≈ 260 at 22 s) it keeps its
    # place and size on the top of the hair rather than shrinking in the middle of the answer
    assert all(pg.style.size_px == 88 for pg in tl.captions)
    assert len(set(tops)) <= 2  # stays put while the head bobs (one height per framing section)
    # the old failure: a block centred at y ≈ 640, on the hair
    assert all(pg.y_norm < 0.25 for pg in tl.captions)


def test_d030_director_plan_at_72px_never_renders_small(d030: dict):
    """The delivered document's own plan (72 px, one line, pages up to 22 characters): the renderer used to
    shrink 'everyone says you need' to ~52 px. Now nothing renders under the 64 px floor (a page too wide
    for one line at the floor wraps instead), and placement still clears the face and hair."""
    ix = _index(d030)
    plan = CaptionPlan.model_validate(d030["director_captions_v15"])
    assert plan.style.size_px == 72 and plan.style.lines == 1
    tl = compile_timeline(_doc(d030, plan), ix)
    _check_pages(tl, ix)
    wide = [pg for pg in tl.captions if " ".join(w.text for w in pg.words) == "everyone says you need"]
    assert wide and _block(wide[0])[4].size_px >= 64


def test_d030_without_measured_head_tops_uses_a_safe_prior(d030: dict):
    """An index built before head tops were measured falls back to the prior ratio (0.55 box heights of
    hair), which clears this creator's hair (measured 0.50) and keeps every other guarantee."""
    ix = _index(d030, measured=False)
    assert ix.visual.head_top_ratio is None
    tl = compile_timeline(_doc(d030, None), ix)
    _check_pages(tl, ix, hair_ok=False)
    # the prior assumes more hair than this creator has: judged against the measured hair, every page that
    # found room above the head clears it
    measured = _index(d030)
    tl2 = compile_timeline(_doc(d030, None), measured)
    for pg in tl.captions:
        if pg.placement in ("least_bad", "on_hair"):
            continue
        for f in _faces(tl2, measured, pg):
            assert _block(pg)[3] <= f.head_top() + 0.05 * f.h + 0.5, pg.page_id
    assert sum(pg.placement in ("least_bad", "on_hair") for pg in tl.captions) <= 6  # only while the head is raised


def test_d030_placement_is_below_the_chin_when_the_face_sits_higher(d030: dict):
    """Same real track moved up by 30 % of the frame (a creator who frames with headroom): the default
    applies again — top edge 40–120 px under the chin, full size."""
    ix = _index(d030)
    pts = [p.model_copy(update={"cy": p.cy - 0.3}) for p in ix.visual.face_track.points]
    vis = ix.visual.model_copy(update={"face_track": ix.visual.face_track.model_copy(update={"points": pts})})
    ix = ix.model_copy(update={"visual": vis})
    tl = compile_timeline(_doc(d030, None), ix)
    params = C.CaptionParams()
    for pg in tl.captions:
        _x0, y0, _x1, y1, fit = _block(pg)
        chin = max(f.chin for f in _faces(tl, ix, pg))
        assert pg.placement in ("below_chin",)
        assert y0 - chin >= params.chin_gap_min_px - 1  # never on the chin
        assert y1 <= BAND_BOTTOM + 0.5
        assert pg.style.size_px == 88 and fit.size_px >= 64  # house size (a long page may shrink to fit)
    first = [f for f in _faces(tl, ix, tl.captions[0])]
    assert _block(tl.captions[0])[1] - max(f.chin for f in first) <= params.chin_gap_max_px + 1


def test_d030_auto_pages_do_not_flicker_at_house_size(d030: dict):
    """At 88 px a heavy face holds ~13 characters a line, and this creator talks at ~194 wpm: one-line pages
    alone flashed 7-9 pages under 0.5 s ('They buy' 0.33 s | 'the outcome' | 'they want,'), which pushed the
    Director to 9-word two-line pages. Wrapped pages now compete with flicker (doctrine: larger two-line
    pages rather than faster flicker), and a two-line style wraps rather than visibly shrinking a page."""
    ix = _index(d030)
    tl = compile_timeline(_doc(d030, None), ix)
    short = [pg for pg in tl.captions if len(pg.words) > 1 and float(pg.out_end - pg.out_start) < 0.5]
    assert len(short) <= 3
    fits = [C.caption_fit(pg.style, " ".join(w.text for w in pg.words), 1080) for pg in tl.captions]
    assert all(f.lines <= 2 for f in fits)
    assert sum(f.lines == 1 for f in fits) >= len(fits) // 2  # one line stays the norm
    assert all(len(pg.words) <= 5 for pg in tl.captions)


# ============================================================================================ choices, not rules
def test_d030_below_chin_is_honoured_and_the_options_are_measured(d030: dict):
    """The Director may choose to keep captions under the chin (position='below_chin'): pages then never go above
    the head while any spot under the chin exists (the relaxed caption floor, the chin grazing the block's top), and
    never cover eyes or mouth. The options it chose from are measured, not imposed."""
    from studio.doc.model import CaptionStyle, Framing, Point

    ix = _index(d030)
    plan = CaptionPlan(enabled=True, style=CaptionStyle(size_px=88), position="below_chin")
    tl = compile_timeline(_doc(d030, plan), ix)
    params = C.CaptionParams()
    floor = 1920 - TIKTOK.relaxed_bottom
    assert TIKTOK.relaxed_bottom == 320 and TIKTOK.bottom == 480  # TikTok: strict 480, relaxed floor y 1600
    below = 0
    for pg in tl.captions:
        x0, y0, x1, y1, _fit = _block(pg)
        assert y1 <= floor + 0.5, pg.page_id
        for f in _faces(tl, ix, pg):
            assert C._overlap((x0, y0, x1, y1), f.protected(params)) == 0, (pg.page_id, "covers eyes/mouth")
        below += pg.placement in ("below_chin", "below_chin_relaxed")
    assert below >= len(tl.captions) - 6
    opts = C.placement_options(ix, style=CaptionStyle(size_px=88), zone=C.safe_zone_for("tiktok"))
    assert opts is not None and opts["chin_px"] > opts["strict_band_bottom_px"]
    assert not opts["below_chin"]["fits_strict"] and opts["smaller_text_px"] is None
    r = opts["reframe"]
    assert 1.0 < r["relaxed_scale"] <= r["clean_scale"] < r["strict_scale"]  # a clean reframe lifts the chin enough
    # taking that option: a base reframe with the crop low plus below_chin puts nearly every page under the chin at
    # the full 88 px
    doc = _doc(d030, plan)
    fr = Framing(scale=round(r["relaxed_scale"] + 0.03, 2), center=Point(x=0.5, y=round(r["centre_y"], 3)))
    doc = doc.model_copy(update={"segments": [s.model_copy(update={"framing": fr}) for s in doc.segments]})
    tl2 = compile_timeline(doc, ix)
    geo = C.caption_geometry(tl2, ix, platform="tiktok", style_px=88)
    rel = geo["summary"]["relation"]
    # under the chin, or on the relaxed floor grazing the chin when the head dips (never over eyes or mouth)
    assert rel.get("below_chin", 0) + rel.get("over_lower_face", 0) >= len(tl2.captions) - 4
    assert rel.get("below_chin", 0) >= len(tl2.captions) // 2 and geo["summary"]["size_px"]["median"] >= 76


def test_d030_geometry_names_the_old_defect(d030: dict):
    """The delivered plan (72 px) sat on the hair: the geometry the critics read says so in numbers."""
    ix = _index(d030)
    plan = CaptionPlan.model_validate(d030["director_captions_v15"])
    tl = compile_timeline(_doc(d030, plan), ix)
    geo = C.caption_geometry(tl, ix, platform="tiktok", style_px=72)
    s = geo["summary"]
    assert s["share_above_eyes"] == 1.0 and s["share_below_chin_in_window"] == 0.0
    assert s["cap_height_px"]["max"] <= 72 * 0.7 + 0.5
    assert "above the eye line 100%" in C.caption_geometry_text(geo)
