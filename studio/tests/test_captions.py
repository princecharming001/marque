"""Caption paging, emphasis, timing, placement and SRT (studio.compile.captions). Keyless and offline."""

from __future__ import annotations

import math
from fractions import Fraction as F
from itertools import pairwise
from pathlib import Path

import pytest
from captions_fixtures import simple_timeline, with_face, without_face

from studio.compile import captions as C
from studio.compile.models import FramingKey, TimelineCaptionPage, TimelineCaptionWord, TimelineInsert
from studio.config import Settings
from studio.doc.model import CaptionPage, CaptionPlan, CaptionStyle, CardSpec, CutDocument, Pins, Segment
from studio.perception.index import Prosody, TakeIndex, Word
from studio.timebase import frame_time

FPS = 30
HOUSE = C.PLATFORM_SAFE_ZONES["all"]


def auto_doc(doc: CutDocument) -> CutDocument:
    """The fixture document with captions left to the auto pager (and cross-platform deliverables)."""
    return doc.model_copy(update={"captions": None})


def page_texts(pages: list[TimelineCaptionPage]) -> list[str]:
    return [" ".join(w.text for w in p.words) for p in pages]


def block_top_px(p: TimelineCaptionPage, params: C.CaptionParams | None = None, height: int = 1920) -> float:
    params = params or C.CaptionParams()
    text = " ".join(w.text for w in p.words)
    _, bh = C._page_block(p.style, text, 1080, params)
    return p.y_norm * height - bh / 2


@pytest.fixture
def auto_pages(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, take_index)
    return doc, tl, C.build_caption_pages(take_index, doc, tl, platforms="all")


# ============================================================================================== safe zones/params
def test_safe_zone_union_is_house_band():
    z = C.safe_zone_for(["tiktok", "reels", "shorts"])
    assert (z.top, z.bottom, z.left, z.right) == (288, 672, 65, 192)
    assert z.band(1080, 1920) == (65, 288, 888, 1248)
    t = C.safe_zone_for("tiktok")
    assert t.top == 200 and t.bottom == 480
    assert C.safe_zone_for(None) == C.safe_zone_for("all")
    # scales with the output size
    big = C.safe_zone_for("all", width=1440, height=2560)
    assert big.top == pytest.approx(288 * 2560 / 1920) and big.left == pytest.approx(65 * 1440 / 1080)


def test_constants_yaml_overrides(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "constants.yaml").write_text(
        "captions:\n  lead_ms: 60\n  max_cps: 18\n  chin_gap_px: 70\n"
        "safe_zones:\n  tiktok: {top: 150, bottom: 400, left: 50, right: 150}\n", encoding="utf-8")
    s = Settings.load(env={"STUDIO_SKILLS_DIR": str(skills)})
    consts = C.load_constants(s)
    params = C.load_caption_params(s, consts)
    assert params.lead_s == pytest.approx(0.06) and params.max_cps == 18 and params.chin_gap_px == 70
    z = C.safe_zone_for("tiktok", constants=consts)
    assert (z.top, z.bottom, z.left, z.right) == (150, 400, 50, 150)
    # missing file -> defaults
    assert C.load_caption_params(Settings.load(env={"STUDIO_SKILLS_DIR": str(tmp_path / "nope")})) == C.CaptionParams()


def test_text_width_estimate_is_monotonic_and_case_aware():
    a = C.estimate_text_width("most people", 72)
    b = C.estimate_text_width("most people fail", 72)
    assert 0 < a < b
    assert C.estimate_text_width("MOST", 72) > C.estimate_text_width("most", 72)
    assert C.estimate_text_width("most", 72, case="upper") == C.estimate_text_width("MOST", 72)
    assert C.estimate_text_width("most", 72, font="Anton") < C.estimate_text_width("most", 72, font="Montserrat")


# ============================================================================================== paging
def test_auto_pages_cover_kept_lexical_words_in_order(auto_pages, take_index: TakeIndex):
    doc, _tl, pages = auto_pages
    shown = [w.word_id for p in pages for w in p.words]
    expected = [w for w in doc.kept_word_ids(take_index) if take_index.word(w).kind not in ("filler", "event")]
    assert shown == expected  # every lexical word exactly once, in output order; nothing paraphrased
    assert all(1 <= len(p.words) <= 4 for p in pages)
    assert "Um," not in " ".join(page_texts(pages))  # clean verbatim


def test_pages_break_at_sentences_and_before_payoff(auto_pages, take_index: TakeIndex):
    _doc, _tl, pages = auto_pages
    for p in pages:
        for w in p.words[:-1]:  # no sentence end inside a page
            assert not w.text.endswith((".", "!", "?")), page_texts(pages)
    # the pinned payoff "restraint." (w0018) starts its own page: setup and punchline never share one
    first_ids = [p.words[0].word_id for p in pages]
    assert "w0018" in first_ids


def test_pages_avoid_syntactic_splits(auto_pages):
    _doc, _tl, pages = auto_pages
    bad_endings = {"the", "a", "an", "to", "of", "is", "and", "my", "your"}
    for p, nxt in pairwise(pages):
        if nxt.words[0].word_id == "w0018":
            continue  # forced: the payoff always opens its own page ("The real secret is / restraint.")
        last = p.words[-1].text.lower().strip(",.")
        assert last not in bad_endings, page_texts(pages)


def test_fast_speech_uses_bigger_pages(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    normal = C.build_caption_pages(take_index, doc, simple_timeline(doc, take_index))
    fast_doc = doc.model_copy(update={"segments": [s.model_copy(update={"speed": 2.0}) for s in doc.segments]})
    fast = C.build_caption_pages(take_index, fast_doc, simple_timeline(fast_doc, take_index))
    assert max(len(p.words) for p in fast) <= 5
    assert len(fast) <= len(normal)
    assert sum(len(p.words) for p in fast) / len(fast) >= sum(len(p.words) for p in normal) / len(normal)


def test_long_words_never_make_an_unfittable_page(take_index: TakeIndex, cut_doc: CutDocument):
    style = CaptionStyle(size_px=120, max_words_per_page=4)
    doc = auto_doc(cut_doc).model_copy(update={"captions": CaptionPlan(style=style)})
    pages = C.build_caption_pages(take_index, doc, simple_timeline(doc, take_index))
    for p in pages:
        text = " ".join(w.text for w in p.words)
        if len(p.words) > 1:  # a multi-word page renders at full size, on one line or wrapped to two
            fit = C.caption_fit(p.style, text, 1080)
            assert fit.shrink == 1.0 or (fit.lines == 1 and fit.size_px >= 64 - 1e-6)
            assert fit.lines <= 2 and fit.width <= 690 + 1e-6


# ============================================================================================== emphasis
def test_emphasis_is_rare_supported_and_spaced(auto_pages, take_index: TakeIndex):
    _doc, _tl, pages = auto_pages
    accented = [(p, w) for p in pages for w in p.words if w.emphasis]
    assert all(sum(w.emphasis for w in p.words) <= 1 for p in pages)
    assert len(accented) <= math.ceil(0.25 * len(pages))
    starts = sorted(float(w.out_start) for _p, w in accented)
    assert all(b - a >= 2.0 for a, b in pairwise(starts))
    for _p, w in accented:
        word = take_index.word(w.word_id)
        z = max(v for v in (word.prosody.f0_z, word.prosody.int_z, word.prosody.dur_z) if v is not None)
        assert word.emphasis >= 0.6 or z >= 1.5
        assert w.text.lower().strip(",.") not in {"the", "a", "is", "and", "me"}
    assert any(w.text == "matter" for _p, w in accented)
    # w0018 carries the SFX pop in the fixture document: one device per beat, no caption accent
    assert all(w.word_id != "w0018" for _p, w in accented)


def test_payoff_accented_without_competing_device(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc).model_copy(update={"audio": cut_doc.audio.model_copy(update={"sfx": []})})
    pages = C.build_caption_pages(take_index, doc, simple_timeline(doc, take_index))
    assert any(w.word_id == "w0018" and w.emphasis for p in pages for w in p.words)


def test_flat_prosody_gets_no_accents(take_index: TakeIndex, cut_doc: CutDocument):
    flat_words = [w.model_copy(update={"emphasis": 0.1, "prosody": Prosody(f0_z=0.0, int_z=0.0, dur_z=0.0)})
                  for w in take_index.words]
    ix = take_index.model_copy(update={"words": flat_words})
    doc = auto_doc(cut_doc)
    pages = C.build_caption_pages(ix, doc, simple_timeline(doc, ix))
    assert not any(w.emphasis for p in pages for w in p.words)  # zero accents is a fine result


# ============================================================================================== plan pages
def test_director_plan_pages_are_honoured(take_index: TakeIndex, cut_doc: CutDocument):
    tl = simple_timeline(cut_doc, take_index)
    pages = C.build_caption_pages(take_index, cut_doc, tl)
    plan = cut_doc.captions
    assert plan is not None
    got = [[w.word_id for w in p.words] for p in pages]
    want = [[w for w in pg.word_ids if take_index.word(w).kind not in ("filler", "event")] for pg in plan.pages]
    assert got == [g for g in want if g]
    assert [p.page_id for p in pages] == [pg.id for pg in plan.pages if any(
        take_index.word(w).kind != "filler" for w in pg.word_ids)]
    emph = {w.word_id for p in pages for w in p.words if w.emphasis}
    assert emph == {w for pg in plan.pages for w in pg.emphasis_word_ids}


def test_text_override_and_disabled(take_index: TakeIndex, cut_doc: CutDocument):
    plan = CaptionPlan(pages=[CaptionPage(id="p001", word_ids=["w0001", "w0002", "w0003"], text="MOST PEOPLE CUT"),
                              CaptionPage(id="p002", word_ids=["w0004", "w0005"])])
    doc = cut_doc.model_copy(update={"captions": plan})
    pages = C.build_caption_pages(take_index, doc, simple_timeline(doc, take_index))
    assert pages[0].text == "MOST PEOPLE CUT" and len(pages) == 2
    off = cut_doc.model_copy(update={"captions": CaptionPlan(enabled=False)})
    assert C.build_caption_pages(take_index, off, simple_timeline(off, take_index)) == []


def test_auto_caption_plan_is_a_valid_set_captions_payload(take_index: TakeIndex, cut_doc: CutDocument):
    from studio.doc.ops import apply_ops

    doc = auto_doc(cut_doc)
    plan = C.auto_caption_plan(doc, take_index)
    assert plan.pages and plan.pages[0].id == "p001"
    assert all(set(p.emphasis_word_ids) <= set(p.word_ids) for p in plan.pages)
    new, results = apply_ops(doc, [{"op": "set_captions", "plan": plan.model_dump(mode="json")}], take_index,
                             persist=False)
    assert results[0].applied, results[0].reason
    assert new.captions is not None and len(new.captions.pages) == len(plan.pages)
    # the plan from estimated spans and from a compiled timeline page the same way here
    plan_tl = C.auto_caption_plan(doc, take_index, timeline=simple_timeline(doc, take_index))
    assert [p.word_ids for p in plan_tl.pages] == [p.word_ids for p in plan.pages]


# ============================================================================================== timing
def test_timing_leads_onset_on_grid_and_never_overlaps(auto_pages):
    _doc, tl, pages = auto_pages
    fps = F(FPS)
    seams = set(C._visible_seams(tl))
    for k, p in enumerate(pages):
        assert tl.on_grid(p.out_start) and tl.on_grid(p.out_end)
        onset = p.words[0].out_start
        lead = onset - p.out_start
        assert lead > -F(1, FPS)  # never lags the voice by a frame or more
        assert lead <= F(125, 1000) + F(1, 2 * FPS)
        if k + 1 < len(pages):
            nxt = pages[k + 1]
            assert p.out_end <= nxt.out_start
            gap = nxt.out_start - p.out_end
            assert (gap == 0 and nxt.out_start in seams) or gap >= 2 / fps, (k, gap)
        assert p.out_end <= tl.duration
    # pages are at least 0.5 s except one-word punch pages (>= 0.25 s)
    for p in pages:
        dur = p.out_end - p.out_start
        assert dur >= F(1, 4) - F(1, FPS)


def test_page_change_snaps_onto_a_nearby_seam(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, take_index)
    pages = C.build_caption_pages(take_index, doc, tl)
    starts = {p.out_start for p in pages}
    # segment joins sit PAD before the next word; at 80 ms pad the page-in (~90 ms lead) lands on the cut
    for seam in tl.seams:
        assert seam in starts
        prev = [p for p in pages if p.out_end <= seam]
        assert prev and prev[-1].out_end == seam  # the outgoing page ends exactly on the cut


def test_hold_after_run_and_close_short_gaps(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, take_index)
    pages = C.build_caption_pages(take_index, doc, tl)
    last = pages[-1]
    hold = last.out_end - max(w.out_end for w in last.words)
    assert F(3, 10) - F(1, FPS) <= hold <= F(1, 2) + F(1, FPS) or last.out_end == tl.duration


# ============================================================================================== placement
def _protected(face_cx: float, face_cy: float, w: float, h: float) -> tuple[float, float, float, float]:
    p = C.CaptionParams()
    x0, y0 = (face_cx - w / 2) * 1080, (face_cy - h / 2) * 1920
    fw, fh = w * 1080, h * 1920
    return (x0 + p.protect_side_frac * fw, y0 + p.protect_top_frac * fh, x0 + (1 - p.protect_side_frac) * fw,
            y0 + p.protect_bottom_frac * fh)


def test_captions_sit_below_the_chin_inside_the_band(auto_pages):
    _doc, _tl, pages = auto_pages
    chin = (0.32 + 0.1) * 1920  # fixture face: cy 0.32, h 0.2
    for p in pages:
        top = block_top_px(p)
        assert chin + 40 - 1 <= top <= chin + 120 + 1
        assert top >= 288 and top + (p.y_norm * 1920 - top) * 2 <= 1248 + 1
    # static face -> captions never move (the anchor is the block's top edge; a page shrunk to fit is shorter)
    assert len({round(block_top_px(p)) for p in pages}) == 1


def test_hysteresis_ignores_face_jitter(take_index: TakeIndex, cut_doc: CutDocument):
    ix = with_face(take_index, 0.5, 0.33, jitter=0.006)  # ±11 px of chin jitter
    doc = auto_doc(cut_doc)
    pages = C.build_caption_pages(ix, doc, simple_timeline(doc, ix), platforms="all")
    assert len({round(block_top_px(p)) for p in pages}) == 1


def test_punch_in_moves_captions_only_when_needed(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    base = simple_timeline(doc, take_index)
    seg3 = base.segments[2]
    # 1.35x punch around the face on seg003: the chin drops ~140 px -> captions must re-anchor lower
    keys = {"seg003": [FramingKey(out_t=seg3.out_start, scale=1.35, cx=0.5, cy=0.32, ease="hold")]}
    tl = simple_timeline(doc, take_index, framing=keys)
    pages = C.build_caption_pages(take_index, doc, tl, platforms="all")
    faces_ok = C.caption_placement_issues(tl.model_copy(update={"captions": pages}), take_index, platform="all")
    assert faces_ok == []
    in_punch = [p for p in pages if seg3.out_start <= p.out_start < seg3.out_end]
    before = [p for p in pages if p.out_end <= seg3.out_start]
    assert in_punch and before
    assert in_punch[0].y_norm > before[-1].y_norm  # moved down with the chin, and only there


def test_low_face_goes_above_the_head_never_over_the_mouth(take_index: TakeIndex, cut_doc: CutDocument):
    ix = with_face(take_index, 0.5, 0.58, w=0.3, h=0.18)  # chin at ~1287 px: below the strict band
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, ix)
    pages = C.build_caption_pages(ix, doc, tl, platforms="all")
    prot = _protected(0.5, 0.58, 0.3, 0.18)
    for p in pages:
        top = block_top_px(p)
        bh = (p.y_norm * 1920 - top) * 2
        assert not (top < prot[3] and top + bh > prot[1]), (top, prot)  # vertical clearance of eyes/mouth
        assert top >= 288 - 1 and top + bh <= 1920 - 484 + 1
    assert C.caption_placement_issues(tl.model_copy(update={"captions": pages}), ix, platform="all") == []


def test_no_face_uses_default_band(take_index: TakeIndex, cut_doc: CutDocument):
    ix = without_face(take_index)
    doc = auto_doc(cut_doc)
    pages = C.build_caption_pages(ix, doc, simple_timeline(doc, ix), platforms="all")
    # one anchor (the block top) for the whole video, set by the first page centred on y 1100
    assert len({round(block_top_px(p)) for p in pages}) == 1
    assert abs(pages[0].y_norm * 1920 - 1100) < 2


def test_captions_clear_a_card_and_pip(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    base = simple_timeline(doc, take_index)
    a, b = base.segments[2].out_start, base.segments[2].out_end
    card = TimelineInsert(insert_id="i001", mode="card", out_start=a, out_end=b, asset=CardSpec(template="stat"))
    tl = simple_timeline(doc, take_index, inserts=[card])
    pages = C.build_caption_pages(take_index, doc, tl, platforms="all")
    card_bottom = 1920 - 672 - 230
    for p in pages:
        if a <= p.out_start and p.out_end <= b:
            assert block_top_px(p) >= card_bottom  # below the card's content area
    # PiP in the lower band during the first sentence: caption avoids the rect
    pip = TimelineInsert(insert_id="i002", mode="pip", out_start=F(0), out_end=base.segments[0].out_end,
                         asset=CardSpec(template="title"), rect=(0.1, 0.44, 0.8, 0.12))
    tl2 = simple_timeline(doc, take_index, inserts=[pip])
    pages2 = C.build_caption_pages(take_index, doc, tl2, platforms="all")
    y0, y1 = 0.44 * 1920, 0.56 * 1920
    for p in pages2:
        if p.out_end <= base.segments[0].out_end:
            top = block_top_px(p)
            bh = (p.y_norm * 1920 - top) * 2
            assert top >= y1 or top + bh <= y0


def test_explicit_y_norm_and_position_policy(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, take_index)
    plan = CaptionPlan(position="lower_third", pages=C.auto_caption_plan(doc, take_index).pages)
    pages = C.build_caption_pages(take_index, doc.model_copy(update={"captions": plan}), tl, platforms="all")
    band_y = 288 + 0.82 * (1248 - 288)
    assert all(abs(p.y_norm * 1920 - band_y) < 2 for p in pages)
    # a Director y over the eyes is refused (invariant 7) and falls back to face-aware placement
    bad = plan.model_copy(update={"position": "auto", "pages": [pg.model_copy(update={"y_norm": 0.3})
                                                                   for pg in plan.pages]})
    pages2 = C.build_caption_pages(take_index, doc.model_copy(update={"captions": bad}), tl, platforms="all")
    assert all(p.y_norm > 0.42 for p in pages2)


def test_place_captions_keeps_timing(auto_pages, take_index: TakeIndex):
    _doc, tl, pages = auto_pages
    moved = [p.model_copy(update={"y_norm": 0.2}) for p in pages]
    tl2 = C.place_captions(tl.model_copy(update={"captions": moved}), take_index, platform="all")
    assert [(p.out_start, p.out_end, p.words) for p in tl2.captions] == [(p.out_start, p.out_end, p.words)
                                                                         for p in pages]
    assert [p.y_norm for p in tl2.captions] == [p.y_norm for p in pages]


def test_placement_issues_flag_face_and_band(auto_pages, take_index: TakeIndex):
    _doc, tl, pages = auto_pages
    over_face = [pages[0].model_copy(update={"y_norm": 0.32})]
    issues = C.caption_placement_issues(tl.model_copy(update={"captions": over_face}), take_index, platform="all")
    assert {i["kind"] for i in issues} >= {"covers_face"}
    low = [pages[0].model_copy(update={"y_norm": 0.97})]
    issues = C.caption_placement_issues(tl.model_copy(update={"captions": low}), take_index, platform="all")
    assert "outside_safe_zone" in {i["kind"] for i in issues}


def test_geometry_crop_and_mapping():
    # landscape 1920x1080 source: base 9:16 crop is 607.5x1080 centred
    x0, y0, w, h = C.crop_rect(1920, 1080, 1080, 1920)
    assert (round(w, 1), h, round(x0, 2), y0) == (607.5, 1080.0, 656.25, 0.0)
    # a 2x punch centred on the corner is clamped inside the source
    x0, y0, w, h = C.crop_rect(1080, 1920, 1080, 1920, 2.0, 0.0, 0.0)
    assert (x0, y0, w, h) == (0.0, 0.0, 540.0, 960.0)
    assert C.source_to_output((0.25, 0.25), (0.0, 0.0, 540.0, 960.0), 1080, 1920, 1080, 1920) == (540.0, 960.0)


# ============================================================================================== SRT
def test_srt_output(auto_pages, tmp_path: Path):
    _doc, tl, pages = auto_pages
    tl2 = tl.model_copy(update={"captions": pages})
    body = C.srt_text(tl2)
    blocks = body.strip().split("\n\n")
    assert len(blocks) == len(pages)
    first = blocks[0].splitlines()
    assert first[0] == "1" and first[1].startswith("00:00:00,") and " --> " in first[1]
    assert first[2] == " ".join(w.text for w in pages[0].words)
    p = C.write_srt(tl2, tmp_path / "sub" / "captions.srt")
    assert p.read_text(encoding="utf-8") == body


def test_srt_time_format():
    assert C._srt_time(F(3723, 1) + F(456, 1000)) == "01:02:03,456"
    assert C._srt_time(frame_time(1, F(30000, 1001))) == "00:00:00,033"


# ============================================================================================== edge cases
def test_single_word_and_event_only_story(take_index: TakeIndex):
    words = list(take_index.words)
    ev = Word(id="w0042", text="(laughs)", start_us=words[-1].end_us + 600_000,
              end_us=words[-1].end_us + 900_000, kind="event", sentence_id=None)
    ix = take_index.model_copy(update={"words": [*words, ev]})
    doc = CutDocument(segments=[Segment(id="seg001", from_word="w0018", to_word="w0018")],
                      pins=Pins(payoff_word_ids=["w0018"]))
    tl = simple_timeline(doc, ix)
    pages = C.build_caption_pages(ix, doc, tl)
    assert len(pages) == 1 and pages[0].words[0].text == "restraint."
    assert pages[0].out_end <= tl.duration and pages[0].out_start >= 0
    only_event = CutDocument(segments=[Segment(id="seg001", from_word="w0042", to_word="w0042")])
    assert C.build_caption_pages(ix, only_event, simple_timeline(only_event, ix)) == []


def test_caption_word_times_are_measured_spans(auto_pages):
    _doc, tl, pages = auto_pages
    for p in pages:
        for w in p.words:
            span = tl.word_map[w.word_id]
            assert span is not None
            assert (w.out_start, w.out_end) == (span.out_start, span.out_end)
            assert isinstance(w, TimelineCaptionWord)


def test_pages_break_where_a_full_frame_insert_cuts_in(take_index: TakeIndex, cut_doc: CutDocument):
    from studio.doc.model import Insert

    # a card anchored mid-phrase ("pauses that matter"): a page must start on w0023 and end on w0024
    card = Insert(id="i001", anchor_from_word="w0023", anchor_to_word="w0024", mode="card",
                  asset=CardSpec(template="title", title="x"), job="test")
    doc = auto_doc(cut_doc).model_copy(update={"inserts": [card]})
    pages = C.build_caption_pages(take_index, doc, simple_timeline(doc, take_index))
    firsts = [p.words[0].word_id for p in pages]
    lasts = [p.words[-1].word_id for p in pages]
    assert "w0023" in firsts and "w0024" in lasts


def test_placer_returns_to_an_earlier_position(take_index: TakeIndex, cut_doc: CutDocument):
    doc = auto_doc(cut_doc)
    base = simple_timeline(doc, take_index)
    a, b = base.segments[1].out_start, base.segments[1].out_end
    card = TimelineInsert(insert_id="i001", mode="card", out_start=a, out_end=b, asset=CardSpec(template="stat"))
    tl = simple_timeline(doc, take_index, inserts=[card])
    pages = C.build_caption_pages(take_index, doc, tl, platforms="all")
    ys = [round(block_top_px(p)) for p in pages]  # the anchor is the block's top edge
    before = [round(block_top_px(p)) for p in pages if p.out_end <= a]
    after = [round(block_top_px(p)) for p in pages if p.out_start >= b]
    during = [round(block_top_px(p)) for p in pages if a <= p.out_start and p.out_end <= b]
    assert before and after and during
    assert set(before) == set(after)  # back to the same height after the card, not a new one
    assert all(y > before[0] for y in during)
    assert len(set(ys)) == 2


def test_extreme_speech_rate_never_overlaps_pages(take_index: TakeIndex, cut_doc: CutDocument):
    # squeeze every word to 45 ms with 5 ms gaps (~100 words/s): pages must still be valid and ordered
    words, t = [], 300_000
    for w in take_index.words:
        words.append(w.model_copy(update={"start_us": t, "end_us": t + 45_000}))
        t += 50_000
    ix = take_index.model_copy(update={"words": words, "gaps": []})
    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, ix)
    pages = C.build_caption_pages(ix, doc, tl)
    assert pages
    for a, b in pairwise(pages):
        assert a.out_start < a.out_end <= b.out_start
    shown = [w.word_id for p in pages for w in p.words]
    assert len(shown) == len(set(shown))


# ============================================================================================== fast speech
def test_fast_speech_pages_stay_readable(take_index: TakeIndex):
    """Regression (real d030 render: 'The belief' shown for 0.36 s at 30 CPS): the pager judges a page by its
    time on screen and merges short, fast pages instead of flickering them (never dropping words)."""
    from studio.compile.timeline import compile as compile_timeline
    from studio.doc.model import Deliverable
    from studio.perception.index import Sentence

    sentence = ("They're wrong. The belief is that if your why is real and vulnerable and true to you, people "
                "will buy. But here's what actually moves customers.")
    text = sentence.split(" ")
    words, t = [], 300_000
    for k, w in enumerate(text, start=1):
        dur = 110_000 + 20_000 * len(w.strip(".,"))  # ~4 words/s: a fast delivery
        words.append(Word(id=f"w{k:04d}", text=w, start_us=t, end_us=t + dur, kind="word", confidence=0.97,
                          sentence_id="s001", emphasis=0.1))
        t += dur + (150_000 if w.endswith((".", ",")) else 25_000)
    media = take_index.media.model_copy(update={"duration_us": t + 400_000})
    ix = TakeIndex(media=media, words=words, sentences=[Sentence(
        id="s001", word_ids=[w.id for w in words], text=" ".join(text), start_us=words[0].start_us,
        end_us=words[-1].end_us, complete=True)], transcript_text=" ".join(text))
    doc = CutDocument(version=1, segments=[Segment(id="seg001", from_word="w0001", to_word=words[-1].id)],
                      deliverables=[Deliverable(platform="tiktok")])
    tl = compile_timeline(doc, ix)
    pages = tl.captions
    assert [w.word_id for p in pages for w in p.words] == [w.id for w in words]  # every word, in order
    short = [t for p, t in zip(pages, page_texts(pages), strict=True)
             if len(p.words) > 1 and float(p.out_end - p.out_start) < 0.5 - 1e-6]
    assert len(short) <= 1, short  # the old pager flashed three (e.g. 'The belief' for 0.40 s)
    assert "The belief" not in page_texts(pages) and "But here's" not in page_texts(pages)


# ============================================================================================== size / fit
def test_house_caption_style_is_large_and_heavy():
    st = CaptionStyle()
    assert 84 <= st.size_px <= 96 and st.weight >= 800  # doctrine phrase range 64-96, heavy sans
    assert 0.08 <= st.stroke_px / st.size_px <= 0.12  # outline 8-12 % of the size


def test_caption_fit_never_shrinks_below_the_floor_it_wraps():
    """Regression (d030 r2): a 72 px one-line page 'everyone says you need' was shrunk to ~52 px."""
    st = CaptionStyle(size_px=72, weight=800, stroke_px=6, lines=1)
    fit = C.caption_fit(st, "everyone says you need", 1080)
    assert fit.size_px >= 64 - 1e-6
    assert fit.lines == 2 and fit.width <= 690 + 1e-6
    short = C.caption_fit(st, "brand story.", 1080)
    assert short.lines == 1 and short.size_px == 72 and short.shrink == 1.0
    # a page that fits once shrunk (not under the floor) stays on one line
    mid = C.caption_fit(CaptionStyle(size_px=88), "the identity", 1080)
    assert mid.lines == 1 and 64 <= mid.size_px <= 88
    # a Director size under the floor is honoured (never shrunk further), wrapping instead
    tiny = C.caption_fit(CaptionStyle(size_px=56), "and the proof that you can", 1080)
    assert tiny.size_px == 56 and tiny.lines >= 2


def test_overlay_props_carry_the_legibility_floor(take_index: TakeIndex, cut_doc: CutDocument):
    from studio.compile.overlays import build_overlay_props

    doc = auto_doc(cut_doc)
    tl = simple_timeline(doc, take_index)
    tl = tl.model_copy(update={"captions": C.build_caption_pages(take_index, doc, tl, platforms="tiktok")})
    props = build_overlay_props(tl, index=take_index, platforms="tiktok").to_json_dict()
    assert props["captionLayout"]["minSizePx"] == pytest.approx(64.0)
    assert props["captionLayout"]["overflowLines"] == 2


def test_auto_pages_fit_at_house_size(auto_pages):
    """The pager pages for the size it will render at: most pages keep the full size on one line."""
    _doc, _tl, pages = auto_pages
    fits = [C.caption_fit(p.style, " ".join(w.text for w in p.words), 1080) for p in pages]
    assert all(f.size_px >= 64 - 1e-6 for f in fits)
    assert sum(f.shrink > 0.9 and f.lines == 1 for f in fits) >= 0.75 * len(fits)


def test_relaxed_floor_is_never_stricter_than_the_band():
    for name, z in C.PLATFORM_SAFE_ZONES.items():
        assert z.relaxed_bottom <= z.bottom, name
