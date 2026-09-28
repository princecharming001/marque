"""Overlay props (Timeline → Remotion props), schema parity, output verification and a real render.

Keyless. The schema-parity tests need Node + the installed overlay project (skipped otherwise); the
render tests are marked ``slow`` (headless Chrome + ffmpeg, ~15-40 s).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction as F
from pathlib import Path

import pytest
from captions_fixtures import simple_timeline

from studio.compile import captions as C
from studio.compile import overlays as O
from studio.compile.models import Timeline, TimelineCaptionPage, TimelineCaptionWord, TimelineInsert, TimelineText
from studio.config import get_settings
from studio.doc.model import CaptionStyle, CardSpec, TextStyle, Transition
from studio.jobs import Job
from studio.perception.index import TakeIndex

OVERLAY_DIR = get_settings().overlay_dir
HAVE_NODE = shutil.which("node") is not None
HAVE_PROJECT = (OVERLAY_DIR / "node_modules" / ".bin" / "remotion").exists()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_project = pytest.mark.skipif(not (HAVE_NODE and HAVE_PROJECT), reason="overlay node project not installed")
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not available")


def _page(pid: str, t0: F, words: list[str], *, emph: str | None = None, style: CaptionStyle | None = None,
          y: float = 0.6, dur: F = F(1, 3)) -> TimelineCaptionPage:
    ws, t = [], t0
    for k, w in enumerate(words):
        ws.append(TimelineCaptionWord(word_id=f"w{int(pid[1:]) * 10 + k:04d}", text=w, out_start=t, out_end=t + dur,
                                      emphasis=w == emph))
        t += dur
    return TimelineCaptionPage(page_id=pid, words=ws, out_start=t0, out_end=t, style=style or CaptionStyle(),
                               y_norm=y)


def small_timeline(fps: F | int = 30, seconds: int = 3) -> Timeline:
    """3 s: two caption pages, a hook title, a stat card and nothing else."""
    fps = F(fps)
    return Timeline(
        fps=fps, duration=F(seconds),
        captions=[_page("p001", F(0), ["Most", "people", "fail"]),
                  _page("p002", F(1), ["for", "one", "reason."], emph="one")],
        texts=[TimelineText(text_id="t001", kind="hook_title", text="Stop over-editing", out_start=F(0),
                            out_end=F(2), x_norm=0.5, y_norm=0.21)],
        inserts=[TimelineInsert(insert_id="i001", mode="card", out_start=F(2), out_end=F(3),
                                asset=CardSpec(template="stat", number="73%", title="drop off early"),
                                transition_in=Transition(kind="zoom", ms=230))],
    )


# ============================================================================================== props
def test_props_are_frame_quantized_and_camel_case():
    tl = small_timeline()
    d = O.overlay_props(tl)
    assert d["version"] == 1 and d["fps"] == 30.0 and d["durationInFrames"] == 90
    assert (d["width"], d["height"]) == (1080, 1920)
    assert d["safe"] == {"top": 288.0, "bottom": 672.0, "left": 65.0, "right": 192.0}
    assert d["captionLayout"]["rightPx"] == 888.0 and d["captionLayout"]["maxWidthPx"] == 690.0
    p1, p2 = d["captions"]
    assert (p1["start"], p1["end"], p2["start"], p2["end"]) == (0, 30, 30, 60)
    assert [w["start"] for w in p2["words"]] == [30, 40, 50] and p2["words"][1]["emphasis"] is True
    assert p1["style"]["highlightColor"] == "#FFD400" and p1["style"]["maxLines"] == 1
    assert "strokeColor" in p1["style"] and "y_norm" not in p1 and p1["yNorm"] == 0.6
    card = d["cards"][0]
    assert (card["start"], card["end"], card["rect"]) == (60, 90, None)
    assert card["transitionIn"] == {"kind": "zoom", "frames": 7}
    assert card["reserveBottomPx"] == 0.0  # no caption during the card
    json.dumps(d)  # plain JSON


def test_ntsc_and_60fps_timing():
    fps = F(30000, 1001)
    tl = Timeline(fps=fps, duration=F(90) / fps,
                  captions=[_page("p001", F(10) / fps, ["hello", "there"], dur=F(15) / fps)])
    d = O.overlay_props(tl)
    assert d["fps"] == pytest.approx(29.97002997) and d["durationInFrames"] == 90
    assert (d["captions"][0]["start"], d["captions"][0]["end"]) == (10, 40)
    # highlight_lead_frames is specified at 30 fps and scaled to the output rate
    tl60 = small_timeline(60)
    d60 = O.overlay_props(tl60)
    assert d60["durationInFrames"] == 180
    assert d60["captions"][0]["style"]["highlightLeadFrames"] == 4


def test_caption_case_and_display_override():
    st = CaptionStyle(case="upper", font="tiktok sans")
    tl = Timeline(fps=30, duration=F(2), captions=[
        _page("p001", F(0), ["most", "people"], style=st),
        _page("p002", F(1), ["sixty", "dollars"]).model_copy(update={"text": "$60."}),
    ])
    d = O.overlay_props(tl)
    assert [w["text"] for w in d["captions"][0]["words"]] == ["MOST", "PEOPLE"]
    assert d["captions"][0]["style"]["font"] == "TikTok Sans"
    over = d["captions"][1]["words"]
    assert [w["text"] for w in over] == ["$60."] and over[0]["start"] == 30


def test_text_house_look_and_legibility():
    tl = Timeline(fps=30, duration=F(4), texts=[
        TimelineText(text_id="t001", kind="hook_title", text="Hook", out_start=F(0), out_end=F(2)),
        TimelineText(text_id="t002", kind="callout", text="73% drop", out_start=F(1), out_end=F(2)),
        TimelineText(text_id="t003", kind="label", text="custom", out_start=F(1), out_end=F(2),
                     style=TextStyle(size_px=50, color="#FF0000")),
        TimelineText(text_id="t004", kind="weird_kind", text="x", out_start=F(1), out_end=F(2), animation="spin"),
    ])
    d = O.overlay_props(tl)
    hook, callout, label, weird = d["texts"]
    assert hook["style"]["weight"] == 900 and hook["style"]["sizePx"] == 84 and hook["style"]["strokePx"] > 0
    assert callout["style"]["background"] == "#FFD400" and callout["style"]["color"] == "#111111"
    assert label["style"]["color"] == "#FF0000" and label["style"]["strokePx"] == pytest.approx(4.5)
    assert weird["kind"] == "label" and weird["animation"] == "pop"
    assert hook["maxWidthPx"] == pytest.approx((1080 - 65 - 192) * 0.96)


def test_card_mapping_rects_reserve_and_defaults():
    tl = small_timeline()
    tl = tl.model_copy(update={
        "captions": [_page("p001", F(2), ["over", "the", "card"])],
        "inserts": [
            TimelineInsert(insert_id="i001", mode="card", out_start=F(2), out_end=F(3),
                           asset=CardSpec(template="quote", title="q", accent="#00E5FF", background="#101010"),
                           transition_out=Transition(kind="fade", ms=0)),
            TimelineInsert(insert_id="i002", mode="split_top", out_start=F(0), out_end=F(1),
                           asset=CardSpec(template="list", items=["a", "b"])),
            TimelineInsert(insert_id="i003", mode="full", out_start=F(0), out_end=F(1), asset=CardSpec(),
                           asset_path=None),
        ]})
    d = O.overlay_props(tl)
    q, split, full = d["cards"]
    assert q["accent"] == "#00E5FF" and q["background"] == "#101010"
    assert q["reserveBottomPx"] == pytest.approx(230.0)  # captions run over the card
    assert q["transitionOut"]["frames"] > 0  # a fade with ms=0 still gets a real duration
    assert split["rect"] == [0.0, 0.0, 1.0, 0.5] and split["reserveBottomPx"] == 0.0
    assert full["rect"] is None and full["background"] == O.HOUSE_CARD_BACKGROUND
    assert full["displayFont"] == "Anton"


def test_graphics_and_validation():
    tl = Timeline(fps=30, duration=F(2))
    g = [O.OverlayGraphic(id="g1", kind="arrow", out_start=F(0), out_end=F(1), from_pt=(0.2, 0.3), to_pt=(0.4, 0.4)),
         O.OverlayGraphic(id="g2", kind="circle", out_start=F(1), out_end=F(2), x=0.3, y=0.3, w=0.2, h=0.1,
                          draw_ms=300)]
    d = O.overlay_props(tl, graphics=g)
    arrow, circle = d["graphics"]
    assert arrow["from"] == {"x": 0.2, "y": 0.3} and arrow["to"] == {"x": 0.4, "y": 0.4}
    assert circle["drawFrames"] == 9 and circle["color"] == "#FFD400" and circle["from"] is None
    with pytest.raises(ValueError, match="arrow"):
        O.overlay_props(tl, graphics=[O.OverlayGraphic(id="g3", kind="arrow", out_start=F(0), out_end=F(1))])


def test_list_items_reveal_on_spoken_words(take_index: TakeIndex, cut_doc):
    doc = cut_doc.model_copy(update={"captions": None})
    tl = simple_timeline(doc, take_index)
    a = tl.word_map["w0029"].out_start  # "If this helped, follow me for more simple editing tips…"
    t = TimelineText(text_id="t001", kind="list", text="Do this", items=["follow me", "editing tips", "nothing"],
                     out_start=a, out_end=tl.duration)
    tl = tl.model_copy(update={"texts": [t]})
    d = O.overlay_props(tl, index=take_index)
    starts = d["texts"][0]["itemStarts"]
    fol = int(tl.word_map["w0032"].out_start * 30) - 2
    edit = int(tl.word_map["w0037"].out_start * 30) - 2
    assert starts == [fol, edit, None]


def test_empty_timeline_renders_nothing(tmp_path: Path, work_dir: Path):
    job = Job.create("ovl-empty", work_dir=work_dir)
    tl = Timeline(fps=30, duration=F(2))
    assert not O.has_overlay_content(O.build_overlay_props(tl))
    out = O.render_overlays(job, tl, tmp_path / "r1")
    assert out is None and (tmp_path / "r1" / "overlay_props.json").exists()


def test_platform_zone_follows_document(take_index: TakeIndex, cut_doc, work_dir: Path):
    job = Job.create("ovl-plat", work_dir=work_dir)
    job.save_index(take_index)
    job.save_doc(cut_doc)  # deliverables: tiktok only
    tl = simple_timeline(cut_doc, take_index).model_copy(update={"doc_version": cut_doc.version})
    d = O.overlay_props(tl, job=job)
    assert d["safe"]["top"] == 200.0 and d["safe"]["bottom"] == 480.0


def test_default_text_anchor():
    z = C.safe_zone_for("all")
    x, y = O.default_text_anchor("hook_title", "auto", z)
    assert x == pytest.approx((65 + 888) / 2 / 1080) and 288 / 1920 < y < 600 / 1920
    assert O.default_text_anchor("label", (0.3, 0.4), z) == (0.3, 0.4)
    _, y_low = O.default_text_anchor("callout", "lower_third", z)
    assert y_low * 1920 < 1248


def test_transparent_probe_frame_avoids_opaque_cards():
    props = O.build_overlay_props(small_timeline())
    f = O.transparent_probe_frame(props)
    assert f is not None and not (60 <= f < 90)
    only_card = Timeline(fps=30, duration=F(1), inserts=[TimelineInsert(
        insert_id="i001", mode="card", out_start=F(0), out_end=F(1), asset=CardSpec())])
    assert O.transparent_probe_frame(O.build_overlay_props(only_card)) is None


# ============================================================================================== schema parity (node)
@needs_project
def test_generated_props_match_the_zod_schema(take_index: TakeIndex, cut_doc):
    doc = cut_doc.model_copy(update={"captions": None})
    tl = simple_timeline(doc, take_index)
    tl = tl.model_copy(update={"captions": C.build_caption_pages(take_index, doc, tl),
                               "texts": small_timeline().texts, "inserts": small_timeline().inserts})
    g = [O.OverlayGraphic(id="g1", kind="arrow", out_start=F(0), out_end=F(1), from_pt=(0.2, 0.3), to_pt=(0.4, 0.4))]
    props = O.build_overlay_props(tl, index=take_index, graphics=g)
    ok, issues = O.validate_props_with_schema(props)
    assert ok, issues
    bad = props.to_json_dict()
    bad["captions"][0]["style"]["animation"] = "bounce"
    del bad["theme"]
    ok, issues = O.validate_props_with_schema(bad)
    assert not ok and any("theme" in i for i in issues) and any("animation" in i for i in issues)


@needs_project
def test_overlay_project_is_installed():
    root = O.ensure_overlay_project(install=False)
    assert (root / "src" / "fonts-manifest.json").exists()
    fams = {f["family"] for f in json.loads((root / "src" / "fonts-manifest.json").read_text())["fonts"]}
    assert {"Montserrat", "Inter", "Anton", "TikTok Sans", "Archivo", "Noto Color Emoji"} <= fams
    assert len(O.bundle_key()) == 20


# ============================================================================================== verification (ffmpeg)
def _prores(path: Path, *, alpha: str | None, frames: int = 12) -> Path:
    """Tiny ProRes file: alpha='half' (transparent left half), 'opaque' (alpha 255) or None (422 HQ)."""
    if alpha is None:
        vf, prof, pix = "format=yuv422p10le", "3", "yuv422p10le"
    else:
        a = "if(lt(X,W/2),0,255)" if alpha == "half" else "255"
        vf, prof, pix = f"format=rgba,geq=r=200:g=100:b=50:a='{a}',format=yuva444p10le", "4", "yuva444p10le"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x96:r=30:d={frames / 30}",
                    "-vf", vf, "-c:v", "prores_ks", "-profile:v", prof, "-pix_fmt", pix, "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709", str(path)], check=True)
    return path


@needs_ffmpeg
def test_verify_overlay_accepts_alpha_and_rejects_opaque(tmp_path: Path):
    ok = O.verify_overlay(_prores(tmp_path / "a.mov", alpha="half"), frames=12, width=64, height=96, fps=30)
    assert ok.ok and ok.alpha_min == 0 and ok.alpha_max == 255 and "yuva" in ok.pix_fmt
    with pytest.raises(O.OverlayRenderError, match="no alpha"):
        O.verify_overlay(_prores(tmp_path / "b.mov", alpha=None))
    with pytest.raises(O.OverlayRenderError, match="transparent"):
        O.verify_overlay(_prores(tmp_path / "c.mov", alpha="opaque"))
    chk = O.verify_overlay(tmp_path / "a.mov", frames=13, fps=F(30000, 1001), raise_on_error=False)
    assert not chk.ok and any("frames" in p for p in chk.problems) and any("frame rate" in p for p in chk.problems)


# ============================================================================================== real renders
@pytest.mark.slow
@needs_project
@needs_ffmpeg
def test_render_3s_overlay_has_alpha_and_exact_frames(work_dir: Path, tmp_path: Path):
    job = Job.create("ovl-render", work_dir=work_dir)
    tl = small_timeline()
    out = O.render_overlays(job, tl, tmp_path / "r1")
    assert out is not None and out.name == "overlays.mov"
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                                       "-show_streams", "-of", "json", str(out)], capture_output=True, text=True,
                                      check=True).stdout)["streams"][0]
    assert probe["codec_name"] == "prores" and probe["profile"] == "4444"
    assert probe["pix_fmt"].startswith("yuva")  # alpha channel present
    assert int(probe["nb_read_packets"]) == 90 == tl.frame_count
    assert (probe["width"], probe["height"], probe["r_frame_rate"]) == (1080, 1920, "30/1")
    assert probe.get("color_space") == "bt709"
    # a caption frame: transparent background and opaque glyphs
    chk = O.verify_overlay(out, frames=90, content_frame=15)
    assert chk.alpha_min == 0 and chk.alpha_max == 255
    # the card frame is opaque (full-frame designed card)
    amin, _ = O._alpha_stats(out, 80, 1080, 1920, None)
    assert amin == 255
    assert json.loads((tmp_path / "r1" / "overlay_props.json").read_text())["durationInFrames"] == 90
    # unchanged props re-use the cached layer (no second render)
    out2 = O.render_overlays(job, tl, tmp_path / "r2")
    assert out2 is not None and out2.stat().st_ino == out.stat().st_ino
    assert [e for e in job.read_trace() if e["event"] == "overlay_render"][-1]["cached"] is True


@pytest.mark.slow
@needs_project
@needs_ffmpeg
def test_render_ntsc_keeps_exact_rate(tmp_path: Path):
    fps = F(30000, 1001)
    tl = Timeline(fps=fps, duration=F(45) / fps, captions=[_page("p001", F(0), ["exact", "rate"], dur=F(15) / fps)])
    out = O.render_overlay_props(O.build_overlay_props(tl), tmp_path / "ntsc.mov")
    chk = O.verify_overlay(out, frames=45, fps=fps, content_frame=10)
    assert chk.fps == fps and chk.frames == 45
