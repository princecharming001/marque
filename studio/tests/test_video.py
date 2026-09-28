"""A-roll renderer tests: real (small) ffmpeg renders from a synthetic mezzanine.

The synthetic mezzanine encodes its frame number in the background luma (so every output frame can be
traced back to its source frame) and carries a small white marker at the fixture's face position
(0.5, 0.32), so framing transforms can be measured on real pixels.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import pytest

from studio.compile.timeline import compile
from studio.compile.video import (
    _FMTS,
    RenderError,
    _plane_box,
    build_grade_lut,
    grade_is_identity,
    plan_chunks,
    probe_video,
    render_aroll,
    source_frame_map,
    write_cube,
)
from studio.doc.model import AssetRef, ColorSpec, Framing, Insert, Licence, SeamTreatment, Segment, Transition

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")

W, H = 216, 384  # timeline/output size used by most tests (fast)
MW, MH = 432, 768  # mezzanine size (2x: punches downscale, like a 4K source)
FPS = 30
MARK = (0.5, 0.32)
MARK_R = 0.035  # half-size of the marker, fraction of width


def _code(n: int) -> int:
    return 16 + (n % 200)


def _make_mezz(path: Path, seconds: float = 17.0) -> Path:
    """Frame-coded ProRes mezzanine. It also carries a (silent) audio track: with more than one stream in
    the input, ffmpeg's default CFR output mode duplicates the first frame after an accurate seek, so a
    decoder that does not force passthrough timing would be caught off by one frame here."""
    n = int(seconds * FPS)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-s", f"{MW}x{MH}",
           "-framerate", str(FPS), "-i", "pipe:0", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
           "-map", "0:v", "-map", "1:a", "-t", f"{n / FPS:.6f}", "-c:a", "pcm_s16le",
           "-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
           "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    mx, my = int(MARK[0] * MW), int(MARK[1] * MH)
    r = int(MARK_R * MW)
    uv = np.full((MH, MW // 2), 512, np.uint16).tobytes()
    for i in range(n):
        y = np.full((MH, MW), _code(i) * 4, np.uint16)
        y[my - r:my + r, mx - r:mx + r] = 940
        proc.stdin.write(y.tobytes() + uv + uv)
    proc.stdin.close()
    assert proc.wait() == 0
    return path


@pytest.fixture(scope="session")
def mezz_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_mezz(tmp_path_factory.mktemp("mezz") / "mezz.mov")


@pytest.fixture
def rjob(job, mezz_file):
    try:  # read-only use: a hard link costs no disk space
        os.link(mezz_file, job.mezz_path)
    except OSError:
        shutil.copyfile(mezz_file, job.mezz_path)
    return job


def _decode(path: Path, w: int, h: int, fmt: str = "yuv422p10le") -> list[np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", fmt, "-"],
                         capture_output=True, check=True).stdout
    if fmt == "yuv422p10le":
        fs = w * h * 2 * 2
        return [np.frombuffer(raw[i * fs:(i + 1) * fs], "<u2") for i in range(len(raw) // fs)]
    fs = w * h * 3 * 2
    return [np.frombuffer(raw[i * fs:(i + 1) * fs], "<u2").reshape(3, h, w) for i in range(len(raw) // fs)]


def _luma(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    return frame[: w * h].reshape(h, w)


def _frame_code(y: np.ndarray) -> int:
    return round(float(np.median(y)) / 4)


def _marker(y: np.ndarray) -> tuple[float, float, float]:
    """(cx, cy, width) of the bright marker, sub-pixel (luma-weighted)."""
    bg = np.median(y)
    wgt = np.clip((y.astype(np.float64) - bg) / (940 - bg), 0, 1)
    tot = wgt.sum()
    ys, xs = np.mgrid[0:y.shape[0], 0:y.shape[1]]
    cx, cy = (wgt * xs).sum() / tot, (wgt * ys).sum() / tot
    return cx, cy, float(np.sqrt(tot))


def _probe_stream(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,nb_frames,color_space,"
                          "color_transfer,color_primaries", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


# ============================================================================================ pure helpers
def test_plane_box_maps_luma_and_cosited_chroma():
    fmt = _FMTS["yuv422p10le"]
    win = (20.0, 50.0, 400.0, 711.0)
    assert _plane_box(win, (216, 384), 0, fmt, (-0.5, 0.0), (432, 768)) == pytest.approx((20, 50, 420, 761))
    # chroma: output sample j (luma 2j) must map to source luma x0 + (2j + 0.5) * r - 0.5 → chroma /2
    bx0, by0, bx1, by1 = _plane_box(win, (216, 384), 1, fmt, (-0.5, 0.0), (216, 768))
    r = 400 / 216
    assert bx1 - bx0 == pytest.approx(200)
    j = 7  # centre of output chroma sample j in Pillow's convention is j + 0.5
    pil_c = bx0 + (j + 0.5) * (bx1 - bx0) / 108
    # output sample j sits at luma edge 2j+0.5 → source luma edge x0+(2j+0.5)r → cosited chroma (L+0.5)/2
    true_c = (20 + (2 * j + 0.5) * r + 0.5) / 2
    assert pil_c == pytest.approx(true_c)
    assert (by0, by1) == pytest.approx((50, 761))


def test_source_frame_map_nearest_frame_retiming(take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[2] = segs[2].model_copy(update={"speed": 1.5})
    tl = compile(cut_doc.model_copy(update={"segments": segs}), take_index)
    fm = source_frame_map(tl)
    assert len(fm) == tl.frame_count
    s3 = tl.segments[2]
    f0 = round(s3.out_start * 30)
    k0 = round(F(s3.src_in_us, 1_000_000) * 30)
    ks = [k for si, k in fm[f0:f0 + 6]]
    assert ks == [k0 + d for d in (0, 2, 3, 5, 6, 8)]  # round-half-up of 1.5 * j


def test_plan_chunks():
    assert plan_chunks([(0, list(range(30)))], max_len=12) == [(0, list(range(12))), (0, list(range(12, 24))),
                                                                (0, list(range(24, 30)))]
    assert plan_chunks([(0, [1, 2, 3, 20, 21]), (1, [5])], max_len=12, max_gap=6) == [
        (0, [1, 2, 3]), (0, [20, 21]), (1, [5])]


def test_grade_lut_math(tmp_path):
    assert build_grade_lut(None) is None and grade_is_identity(None)
    assert build_grade_lut(ColorSpec()) is None and grade_is_identity(ColorSpec())
    lut = build_grade_lut(ColorSpec(exposure=1.0), size=17)
    assert lut.shape == (17, 17, 17, 3)
    mid = lut[8, 8, 8]
    assert np.all(mid > 0.5) and np.allclose(mid, mid[0], atol=1e-6)  # brighter, still neutral
    assert np.all(lut <= 1.0)  # soft shoulder: no hard clipping beyond range
    top = lut[16, 16, 16][0]
    assert 0.97 < top <= 1.0
    # contrast pivots at mid-grey; saturation 0 is grey
    c = build_grade_lut(ColorSpec(contrast=1.3), size=21)
    assert c[0, 0, 0] == pytest.approx([0, 0, 0], abs=1e-9) and c[20, 20, 20] == pytest.approx([1, 1, 1])
    g = build_grade_lut(ColorSpec(saturation=0.0), size=9)
    assert np.allclose(g[8, 0, 0], g[8, 0, 0][0])
    # white balance "set to 3200 K" neutralizes tungsten: blue up, red down
    wb = build_grade_lut(ColorSpec(white_balance_k=3200), size=9)[4, 4, 4]
    assert wb[2] > wb[1] > wb[0]
    warm = build_grade_lut(ColorSpec(temp=0.5), size=9)[4, 4, 4]
    assert warm[0] > warm[2]
    bw = build_grade_lut(ColorSpec(look="bw", lut_strength=1.0), size=9)[8, 0, 0]
    assert np.allclose(bw, bw[0])
    assert build_grade_lut(ColorSpec(look="clean", lut_strength=0.3), look_only=True, size=9) is not None
    assert build_grade_lut(ColorSpec(exposure=0.5), look_only=True) is None  # no look → footage untouched
    p = write_cube(tmp_path / "g.cube", lut)
    from studio.compile.video import _read_cube

    assert np.allclose(_read_cube(p), lut, atol=1e-6)
    # a creator .cube look is blended at lut_strength
    look = build_grade_lut(ColorSpec(look=str(p), lut_strength=0.5), size=17)
    assert look[8, 8, 8][0] == pytest.approx((8 / 16 + lut[8, 8, 8][0]) / 2, abs=2e-3)


# ============================================================================================ renders
def test_identity_render_is_frame_exact(rjob, take_index, cut_doc):
    tl = compile(cut_doc, take_index, job=rjob, width=W, height=H)
    out = render_aroll(rjob, tl, rjob.path("renders", "r1"))
    assert out.name == "aroll.mov" and out.exists()
    st = _probe_stream(out)
    assert st["codec_name"] == "prores" and st["profile"] == "HQ" and st["pix_fmt"] == "yuv422p10le"
    assert (st["width"], st["height"]) == (W, H) and st["r_frame_rate"] == "30/1"
    assert (st["color_space"], st["color_transfer"], st["color_primaries"]) == ("bt709",) * 3
    assert int(st["nb_frames"]) == tl.frame_count
    frames = _decode(out, W, H)
    assert len(frames) == tl.frame_count
    exp = [_code(k) for _, k in source_frame_map(tl)]
    got = [_frame_code(_luma(f, W, H)) for f in frames]
    assert got == exp


def test_punch_speed_and_jcut_frames(rjob, take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"framing": Framing(scale=1.4),
                                         "seam_in": SeamTreatment(kind="jcut", lead_ms=100)})
    segs[2] = segs[2].model_copy(update={"speed": 1.5})
    tl = compile(cut_doc.model_copy(update={"segments": segs}), take_index, job=rjob, width=W, height=H)
    out = render_aroll(rjob, tl, rjob.path("renders", "r2"))
    frames = _decode(out, W, H)
    assert len(frames) == tl.frame_count
    assert [_frame_code(_luma(f, W, H)) for f in frames] == [_code(k) for _, k in source_frame_map(tl)]
    # fixation-preserving punch: the marker stays put and grows 1.4x
    f1 = round(tl.segments[0].out_start * 30) + 10
    f2 = round(tl.segments[1].out_start * 30) + 10
    x1, y1, s1 = _marker(_luma(frames[f1], W, H))
    x2, y2, s2 = _marker(_luma(frames[f2], W, H))
    assert (x1, y1) == pytest.approx((MARK[0] * W - 0.5, MARK[1] * H - 0.5), abs=1.0)
    assert (x2, y2) == pytest.approx((x1, y1), abs=1.0)
    assert s2 / s1 == pytest.approx(1.4, rel=0.06)


def test_push_is_smooth_without_jitter(rjob, take_index, cut_doc):
    segs = [Segment(id="seg001", from_word="w0020", to_word="w0028", framing=Framing(scale=1.25, ease="push"))]
    doc = cut_doc.model_copy(update={"segments": segs, "inserts": [], "texts": [], "captions": None})
    doc.audio.sfx = []
    tl = compile(doc, take_index, job=rjob, width=W, height=H)
    frames = _decode(render_aroll(rjob, tl, rjob.path("renders", "r3")), W, H)
    sizes = np.array([_marker(_luma(f, W, H))[2] for f in frames])
    assert sizes[-1] / sizes[0] == pytest.approx(1.25, rel=0.05)
    d = np.diff(sizes)
    assert np.all(d > -0.05)  # monotonic growth, no stair-step reversals
    # smooth: frame-to-frame growth varies gently (a whole-pixel zoompan would jump 0 ↔ 1 px)
    assert np.max(np.abs(np.diff(d))) < 0.08


def _clip(path: Path, color: str, size: str = "320x568", seconds: int = 4) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}",
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(path)], check=True)
    return path


def test_broll_layers(rjob, take_index, cut_doc):
    broll = rjob.assets_dir / "broll"
    _clip(broll / "red.mov", "red")
    _clip(broll / "blue.mov", "blue", "640x360")
    _clip(broll / "green.mov", "green")
    lic = Licence(name="test")
    inserts = [
        Insert(id="i001", anchor_from_word="w0003", anchor_to_word="w0005", mode="full", job="x",
               asset=AssetRef(source="local", path="assets/broll/red.mov", licence=lic),
               transition_in=Transition(kind="fade", ms=200)),
        Insert(id="i002", anchor_from_word="w0020", anchor_to_word="w0024", mode="pip", job="x",
               asset=AssetRef(source="local", path="assets/broll/blue.mov", width=640, height=360, licence=lic)),
        Insert(id="i003", anchor_from_word="w0030", anchor_to_word="w0036", mode="split_top", job="x",
               asset=AssetRef(source="local", path="assets/broll/green.mov", licence=lic)),
        Insert(id="i004", anchor_from_word="w0014", anchor_to_word="w0015", mode="card", job="designed card",
               asset=__import__("studio.doc.model", fromlist=["CardSpec"]).CardSpec(title="x")),
    ]
    tl = compile(cut_doc.model_copy(update={"inserts": inserts}), take_index, job=rjob, width=W, height=H)
    out = render_aroll(rjob, tl, rjob.path("renders", "r4"), conform=False)  # the in-graph fallback conform
    frames = _decode(out, W, H, "yuv444p10le")
    assert len(frames) == tl.frame_count
    full, pip, split, card = tl.inserts

    def fr(t):
        return round(t * 30)

    # full-screen cutaway: red during the hold, A-roll before it, partial blend during the fade-in
    a = frames[fr(full.out_start) + 10]
    assert a[2].mean() > 850 and a[1].mean() < 450  # Cr high, Cb low: red
    before = frames[fr(full.out_start) - 1]
    assert abs(before[1].mean() - 512) < 3
    mid_fade = frames[fr(full.out_start) + 3]
    assert 540 < mid_fade[2].mean() < 900
    # PiP: blue inside its rect, the speaker (neutral) elsewhere
    p = frames[fr(pip.out_start) + 5]
    x, y, w, h = pip.rect
    cx, cy = int((x + w / 2) * W), int((y + h / 2) * H)
    assert p[1, cy, cx] > 800  # Cb high: blue
    assert abs(int(p[1, int(0.9 * H), int(0.1 * W)]) - 512) < 4
    # split: green content on top, speaker re-framed into the bottom half (marker visible there)
    s = frames[fr(split.out_start) + 5]
    assert s[1, int(0.2 * H), W // 2] < 450 and s[2, int(0.2 * H), W // 2] < 450  # green
    bottom = s[0, H // 2 + 2:, :]
    mx, my, _ = _marker(bottom)
    assert 0.3 * (H / 2) < my < 0.7 * (H / 2)
    # designed card: drawn by the overlay renderer, the A-roll is untouched underneath
    c = frames[fr(card.out_start) + 2]
    assert abs(c[1].mean() - 512) < 2 and abs(c[2].mean() - 512) < 2


def test_still_insert_gets_a_subpixel_push(rjob, take_index, cut_doc):
    from PIL import Image

    img = np.zeros((600, 400, 3), np.uint8)
    img[:, :] = (30, 30, 30)
    img[280:320, 180:220] = (250, 250, 250)
    Image.fromarray(img).save(rjob.assets_dir / "broll" / "still.png")
    ins = Insert(id="i001", anchor_from_word="w0020", anchor_to_word="w0028", mode="full", job="x",
                 asset=AssetRef(source="local", kind="image", path="assets/broll/still.png", licence=Licence(name="t")))
    tl = compile(cut_doc.model_copy(update={"inserts": [ins]}), take_index, job=rjob, width=W, height=H)
    frames = _decode(render_aroll(rjob, tl, rjob.path("renders", "r5"), conform=False), W, H)
    s, e = round(tl.inserts[0].out_start * 30), round(tl.inserts[0].out_end * 30)
    first = _marker(_luma(frames[s], W, H))
    last = _marker(_luma(frames[e - 1], W, H))
    assert last[2] / first[2] == pytest.approx(1.06, rel=0.03)
    assert (last[0], last[1]) == pytest.approx((first[0], first[1]), abs=1.0)


def test_conformed_broll_is_used_and_graded_like_the_aroll(rjob, take_index, cut_doc, monkeypatch):
    from studio.broll import conform as conform_mod

    _clip(rjob.assets_dir / "broll" / "raw.mov", "red")
    calls = []

    def fake_conform(job, asset, *, fps, width, height, duration_s, mode=None, **kw):
        calls.append({"size": (width, height), "duration": duration_s, "mode": mode, "path": asset.path, **kw})
        out = job.assets_dir / "broll" / f"conformed_{len(calls)}.mov"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x606060:s={width}x{height}:r=30:d={float(duration_s) + 0.5:.3f}", "-c:v",
                        "prores_ks", "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709", "-color_trc", "bt709",
                        "-colorspace", "bt709", str(out)], check=True)
        return out

    monkeypatch.setattr(conform_mod, "conform_asset", fake_conform)
    ins = Insert(id="i001", anchor_from_word="w0020", anchor_to_word="w0024", mode="split_bottom", job="x",
                 split_ratio=0.4, asset=AssetRef(source="local", path="assets/broll/raw.mov", in_ms=700,
                                                 licence=Licence(name="t")))
    tl = compile(cut_doc.model_copy(update={"inserts": [ins]}), take_index, job=rjob, width=W, height=H)
    ti = tl.inserts[0]
    plain = _decode(render_aroll(rjob, tl, rjob.path("renders", "r9")), W, H, "yuv444p10le")
    (call,) = calls
    x, y, w, h = ti.rect
    assert call["size"] == (W, round(h * H / 2) * 2) and call["mode"] == "split_bottom"
    assert call["duration"] == ti.out_end - ti.out_start and call["split_ratio"] == pytest.approx(0.4)
    assert Path(call["path"]).is_absolute()
    f = round(ti.out_start * 30) + 5
    probe_y = int((y + h / 2) * H)
    v_plain = plain[f][0][probe_y, W // 2]
    assert abs(int(plain[f][1][probe_y, W // 2]) - 512) < 3  # the grey conformed clip, not the red raw file
    graded = _decode(render_aroll(rjob, tl, rjob.path("renders", "r10"), color=ColorSpec(exposure=0.5)), W, H,
                     "yuv444p10le")
    assert graded[f][0][probe_y, W // 2] > v_plain + 10  # conformed footage gets the A-roll's grade
    # a failing conform falls back to the raw source with the in-graph conform
    def broken(*a, **k):
        raise RuntimeError("no model")

    monkeypatch.setattr(conform_mod, "conform_asset", broken)
    fb = _decode(render_aroll(rjob, tl, rjob.path("renders", "r11")), W, H, "yuv444p10le")
    assert fb[f][2][probe_y, W // 2] > 850  # red raw clip


def test_color_grade_applied_with_range_handling(rjob, take_index, cut_doc):
    segs = [Segment(id="seg001", from_word="w0020", to_word="w0028")]
    doc = cut_doc.model_copy(update={"segments": segs, "inserts": [], "texts": [], "captions": None})
    doc.audio.sfx = []
    tl = compile(doc, take_index, job=rjob, width=W, height=H)
    plain = _decode(render_aroll(rjob, tl, rjob.path("renders", "r6")), W, H, "yuv444p10le")
    graded = _decode(render_aroll(rjob, tl, rjob.path("renders", "r7"), color=ColorSpec(exposure=0.5)), W, H,
                     "yuv444p10le")
    assert (rjob.path("renders", "r7") / "grade.cube").exists()
    for f in (5, 70):  # a dark and a mid-grey frame
        ya, yb = np.median(plain[f][0]), np.median(graded[f][0])
        v = (ya - 64) / 876  # +0.5 EV in linear light (BT.1886 gamma 2.4), limited range in and out
        expected = 64 + 876 * (v ** 2.4 * 2 ** 0.5) ** (1 / 2.4)
        assert yb > ya and yb == pytest.approx(expected, abs=3)
    f = 70
    assert abs(np.median(graded[f][1]) - 512) < 3  # neutral stays neutral
    assert graded[f][0].max() <= 940 + 4  # limited range preserved (no super-whites from the grade)
    assert graded[f][0].min() >= 64 - 4


def test_preview_render(rjob, take_index, cut_doc):
    tl = compile(cut_doc, take_index, job=rjob, width=W, height=H)
    out = render_aroll(rjob, tl, rjob.path("renders", "r8"), preview=True)
    st = _probe_stream(out)
    assert out.name == "aroll_preview.mp4" and st["codec_name"] == "h264"
    assert (st["width"], st["height"]) == (W // 2, H // 2)
    assert int(st["nb_frames"]) == tl.frame_count


def test_missing_mezzanine_raises(job, take_index, cut_doc):
    tl = compile(cut_doc, take_index, job=job, width=W, height=H)
    with pytest.raises(RenderError):
        render_aroll(job, tl, job.path("renders", "r1"))


def test_probe_video(mezz_file):
    p = probe_video(mezz_file)
    assert (p.width, p.height, p.pix_fmt, p.fps) == (MW, MH, "yuv422p10le", F(30))
    assert p.frame_count == 510


# ============================================================================================ real footage (slow)
TESTDATA = Path("/Users/home/studio-testdata")
REAL_TAKE = TESTDATA / "qa-editor-real-take40.mov"


def _decode_frames(path: Path, w: int, h: int, frames: list[int]) -> list[np.ndarray]:
    """Only the listed frame indices (ascending), as flat yuv422p10le arrays."""
    sel = "+".join(f"eq(n\\,{n})" for n in frames)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select='{sel}'", "-fps_mode",
                          "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-"],
                         capture_output=True, check=True).stdout
    fs = w * h * 2 * 2
    out = [np.frombuffer(raw[i * fs:(i + 1) * fs], "<u2") for i in range(len(raw) // fs)]
    assert len(out) == len(frames)
    return out


def _real_index(job, info):
    """Real visual analysis (MediaPipe, local model) + a uniform synthetic word grid (no ASR, no network)."""
    from studio.perception import visual as visual_mod
    from studio.perception.index import TakeIndex, Word, word_id

    words = []
    t, n = 150_000, 0
    while t + 250_000 < info.duration_us - 150_000:
        n += 1
        words.append(Word(id=word_id(n), text=f"w{n}", start_us=t, end_us=t + 250_000, kind="word",
                          confidence=0.95, speaker="S1"))
        t += 300_000
    vis = visual_mod.analyze_visual(job)
    return TakeIndex(media=info, words=words, visual=vis)


@pytest.mark.slow
@pytest.mark.skipif(not REAL_TAKE.exists(), reason="QA take not available")
def test_real_take_render_is_frame_exact_and_face_framed(job, tmp_path):
    """Rotated HEVC phone take → ingest → real face track → compile (cut, punch seam, split) → A-roll:
    frame count, ProRes/BT.709 delivery, bit-exact identity frames and the eye line in the split."""
    from studio.compile.timeline import map_source_point, region_for_time
    from studio.doc.model import CardSpec
    from studio.media.ingest import IngestOptions, ingest
    from studio.perception.visual import ensure_face_landmarker_model

    try:
        ensure_face_landmarker_model(allow_download=False)
    except FileNotFoundError:
        pytest.skip("face landmarker model not in the models dir (keyless tests never download)")
    if shutil.disk_usage(tmp_path).free < 900_000_000:  # ~300 MB of files + ingest headroom
        pytest.skip("needs ~0.9 GB free disk")
    clip = tmp_path / "take.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "12", "-i", str(REAL_TAKE), "-t", "5", "-c", "copy",
                    str(clip)], check=True)
    info = ingest(clip, job, options=IngestOptions(proxy=False))
    assert (info.width, info.height, info.fps) == (1080, 1920, 30)
    ix = _real_index(job, info)
    assert ix.visual.face_track is not None and len(ix.visual.face_track.points) > 10
    ids = [w.id for w in ix.words]
    segs = [Segment(id="seg001", from_word=ids[0], to_word=ids[5]),
            Segment(id="seg002", from_word=ids[8], to_word=ids[-1], seam_in=SeamTreatment(kind="punch"))]
    split = Insert(id="i001", anchor_from_word=ids[10], anchor_to_word=ids[12], mode="split_top", job="proof",
                   asset=CardSpec(template="stat", number="3"), split_ratio=0.5)
    from studio.doc.model import CutDocument

    doc = CutDocument(version=1, job_id=job.id, created_by="test", segments=segs, inserts=[split])
    tl = compile(doc, ix, job=job)
    out = render_aroll(job, tl, job.path("renders", "real"))
    st = _probe_stream(out)
    assert (st["codec_name"], st["profile"], st["pix_fmt"]) == ("prores", "HQ", "yuv422p10le")
    assert (st["width"], st["height"], st["r_frame_rate"]) == (1080, 1920, "30/1")
    assert (st["color_primaries"], st["color_transfer"], st["color_space"]) == ("bt709", "bt709", "bt709")
    assert int(st["nb_frames"]) == tl.frame_count
    # identity-framed frames are the planned mezzanine frames: the pixels are passed through untouched, so
    # only ProRes HQ's own (near-transparent) re-encode separates them — and the neighbours differ more
    fmap = source_frame_map(tl)
    s1 = tl.segments[0]
    picks = list(range(1, round(s1.out_end * FPS) - 1, 7))
    rendered = dict(zip(picks, _decode_frames(out, 1080, 1920, picks), strict=True))
    src_idx = sorted({fmap[f][1] + d for f in picks for d in (-1, 0, 1)})
    mezz = dict(zip(src_idx, _decode_frames(job.mezz_path, 1080, 1920, src_idx), strict=True))
    checked = 0
    for f in picks:
        _si, k = fmap[f]
        err = {d: float(np.abs(rendered[f].astype(np.int32) - mezz[k + d].astype(np.int32)).mean())
               for d in (-1, 0, 1)}
        assert err[0] < 1.0, (f, err)  # < 1 code of 1023 on average
        assert err[0] < min(err[-1], err[1]), (f, err)
        checked += 1
    assert checked >= 5
    # the punch seam lands on a larger crop, and inside the split the eyes sit between the split line and
    # the bottom UI band
    s2 = tl.segments[1]
    assert s2.framing and max(k.scale for k in s2.framing) >= 1.2
    t = (tl.inserts[0].out_start + tl.inserts[0].out_end) / 2
    assert region_for_time(tl, t)[1] == pytest.approx(0.5)
    box = ix.face_at(tl.segment_at(t).out_to_src_us(t))
    eyes = map_source_point(tl, t, box.cx, box.y + 0.27 * box.h, 1080, 1920)[1]
    assert 0.5 < eyes <= 0.62 + 1e-6
    shutil.rmtree(job.root / "renders", ignore_errors=True)
    job.mezz_path.unlink(missing_ok=True)
