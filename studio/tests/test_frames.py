"""Frame grabs, contact sheets, seam pairs and filmstrips (keyless, offline, synthetic media)."""

from __future__ import annotations

import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from studio.compile.models import Timeline, WordSpan
from studio.jobs import Job
from studio.perception import frames as F
from studio.perception.frames import FrameRequest, ResolvedFrame
from studio.perception.index import TakeIndex

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def _ff(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # type: ignore[list-item]


def _gray_coded(path: Path, rate: str, seconds: float = 6.0) -> Path:
    """64x64 video whose frame n has gray level (2n mod 250), lossless, with B-frames and long GOPs."""
    _ff(["-f", "lavfi", "-i", f"color=black:s=64x64:r={rate}:d={seconds},format=gray,geq=lum='mod(N*2\\,250)'",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", "-g", "48", "-bf", "3", str(path)])
    return path


def _frame_no(rgb: np.ndarray) -> float:
    """Gray level back to the frame number (tv-range encode, full-range RGB decode)."""
    return float(rgb[..., 0].astype(np.float64).mean()) / 2.0


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("frames_media")
    out = {
        "ntsc": _gray_coded(d / "gray_ntsc.mp4", "30000/1001"),
        "p30": _gray_coded(d / "gray_30.mp4", "30"),
    }
    # a 20 s 270x480 "proxy" with a moving pattern and a tone + noise track
    proxy = d / "proxy.mp4"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=20",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(proxy)])
    out["proxy"] = proxy
    # a render with hard colour changes at 1 s and 2 s (red → blue → green)
    render = d / "render.mp4"
    _ff(["-f", "lavfi", "-i", "color=red:s=108x192:r=30:d=1", "-f", "lavfi", "-i", "color=blue:s=108x192:r=30:d=1",
         "-f", "lavfi", "-i", "color=green:s=108x192:r=30:d=1",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", str(render)])
    out["render"] = render
    return out


@pytest.fixture
def job_with_media(job: Job, media: dict[str, Path], take_index: TakeIndex) -> Job:
    """The conftest job (hand-built index) plus a proxy and a dialogue WAV with a word-shaped envelope."""
    shutil.copy(media["proxy"], job.proxy_path)
    sr = 48_000
    n = int(take_index.duration_us * sr / 1_000_000) + sr
    sig = np.zeros(n, np.float32)
    rng = np.random.default_rng(0)
    sig += rng.normal(0, 0.002, n).astype(np.float32)
    for w in take_index.words:
        a, b = w.start_us * sr // 1_000_000, w.end_us * sr // 1_000_000
        t = np.arange(b - a) / sr
        sig[a:b] += (0.3 * np.sin(2 * np.pi * 180 * t) * np.hanning(b - a)).astype(np.float32)
    sf.write(job.audio_path, sig, sr, subtype="FLOAT")
    return job


# ---------------------------------------------------------------------------------------------- frame math
@pytest.mark.parametrize("fps", [Fraction(30000, 1001), Fraction(30), Fraction(24000, 1001), Fraction(60)])
def test_frame_math_round_trips(fps: Fraction):
    for n in (0, 1, 2, 29, 30, 1799, 107_892):
        t = F.frame_time_us(n, fps)
        assert F.frame_at(t, fps) == n  # the exact (rounded) instant of frame n maps back to n
        assert F.frame_at(t + 1000, fps) == n  # 1 ms into the frame is still frame n
        assert F.frame_at(F.frame_time_us(n + 1, fps) - 10, fps) == n  # 10 µs before the next frame
    assert F.frame_at(-5, fps) == 0


def test_probe_video(media: dict[str, Path], synth_rotated: Path):
    vp = F.probe_video(media["ntsc"])
    assert vp.fps == Fraction(30000, 1001) and (vp.width, vp.height) == (64, 64)
    assert vp.duration_us == pytest.approx(6_006_000, abs=40_000) and not vp.has_audio
    rot = F.probe_video(synth_rotated)
    assert (rot.width, rot.height) == (1080, 1920) and rot.rotation in (90, 270) and rot.portrait
    assert F.probe_video(media["proxy"]).has_audio
    assert F.probe_video(synth_rotated) is rot  # cached


# ---------------------------------------------------------------------------------------------- decoding
@pytest.mark.parametrize("name", ["ntsc", "p30"])
def test_grab_frame_is_frame_exact(media: dict[str, Path], name: str):
    path = media[name]
    vp = F.probe_video(path)
    for n in (0, 1, 2, 47, 48, 49, 100, 124):
        t = F.frame_time_us(n, vp.fps)
        for probe_t in (t, t + int(vp.frame_us * 0.9)):
            got = F.grab_frame(path, probe_t)
            assert got.shape == (64, 64, 3) and got.dtype == np.uint8
            assert _frame_no(got) == pytest.approx(n, abs=0.6), (name, n, probe_t)


def test_grab_frames_batch_and_frames_arg(media: dict[str, Path]):
    path = media["p30"]
    imgs = F.grab_frames(path, [0, 1_000_000, 1_000_000, 2_500_000])
    assert [round(_frame_no(i)) for i in imgs] == [0, 30, 30, 75]
    by_idx = F.grab_frames(path, [], frames=[5, 6])
    assert [round(_frame_no(i)) for i in by_idx] == [5, 6]
    small = F.grab_frames(path, [0], width=32)[0]
    assert small.shape == (32, 32, 3)
    # past the end: clamps to the last frame instead of failing
    last = F.grab_frames(path, [99_000_000])[0]
    assert _frame_no(last) == pytest.approx(179 % 125, abs=0.6)


@pytest.mark.parametrize("name", ["ntsc", "p30"])
def test_iter_frames_grid(media: dict[str, Path], name: str):
    path = media[name]
    vp = F.probe_video(path)
    got = list(F.iter_frames(path))
    assert len(got) == vp.frame_count == 180
    assert [k for k, _, _ in got] == list(range(180))
    assert all(t == F.frame_time_us(k, vp.fps) for k, t, _ in got)
    assert all(abs(_frame_no(a) - (k * 2 % 250) / 2) < 0.6 for k, _, a in got)
    # seek start re-anchors the grid on the frame shown at start_us
    part = list(F.iter_frames(path, start_us=2_000_000, end_us=3_000_000))
    k0 = F.frame_at(2_000_000, vp.fps)
    assert part[0][0] == k0 and abs(_frame_no(part[0][2]) - (k0 * 2 % 250) / 2) < 0.6
    assert part[-1][1] < 3_000_000 and len(part) == math.ceil(Fraction(3) * vp.fps) - k0
    # decimated analysis grid (15 fps from 30) picks every other source frame
    if name == "p30":
        half = list(F.iter_frames(path, fps=Fraction(15), end_us=1_000_000))
        assert [round(_frame_no(a)) for _, _, a in half] == list(range(0, 30, 2))


def test_iter_frames_early_close_is_clean(media: dict[str, Path]):
    gen = F.iter_frames(media["p30"])
    k, t, a = next(gen)
    assert (k, t) == (0, 0)
    gen.close()  # consumer stops early: ffmpeg is killed, no error


def test_iter_frames_truncated_file_is_an_error(tmp_path: Path):
    good = tmp_path / "fs.mp4"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=64x64:r=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(good)])
    bad = tmp_path / "fs_trunc.mp4"
    data = good.read_bytes()
    bad.write_bytes(data[: len(data) // 2])  # header intact (faststart), half the samples missing
    assert F.probe_video(bad).frame_count == 180
    with pytest.raises(RuntimeError, match="decoded only"):
        list(F.iter_frames(bad))
    assert 40 < sum(1 for _ in F.iter_frames(bad, strict=False)) < 180


def test_iter_frames_rotated_display_geometry(synth_rotated: Path):
    k, t, rgb = next(iter(F.iter_frames(synth_rotated, width=108)))
    assert (k, t) == (0, 0) and rgb.shape == (192, 108, 3)  # upright portrait


def test_resolve_source_fallbacks(job: Job, media: dict[str, Path]):
    with pytest.raises(FileNotFoundError):
        F.resolve_source(job, "proxy")
    shutil.copy(media["proxy"], job.mezz_path)
    assert F.resolve_source(job, "proxy") == job.mezz_path  # proxy not made yet → mezzanine
    assert F.resolve_source(job, "mezz") == job.mezz_path
    assert F.resolve_source(None, media["p30"]) == media["p30"]
    with pytest.raises(FileNotFoundError):
        F.resolve_source(job, "/nope/render.mp4")
    with pytest.raises(ValueError):
        F.resolve_source(None, "proxy")


# ---------------------------------------------------------------------------------------------- items
def test_resolve_items_ids_and_times(take_index: TakeIndex):
    ix = take_index
    w1, w14, g5, s3 = ix.word("w0001"), ix.word("w0014"), ix.gaps[4], ix.sentence("s003")
    got = F.resolve_items(["w0001", "w0014:end", "w0014:mid", g5.id, f"{g5.id}:start", f"{g5.id}:end", "s003",
                           FrameRequest(1_234_567, "note"), 2_000_000], index=ix)
    assert [r.kind for r in got] == ["word", "word", "word", "gap", "gap", "gap", "sentence", "time", "time"]
    assert got[0].t_us == w1.start_us and got[0].text == "Most"
    assert got[1].t_us == w14.end_us - 1 and got[2].t_us == (w14.start_us + w14.end_us) // 2
    assert got[3].t_us == g5.snap_us and got[4].t_us == g5.start_us and got[5].t_us == g5.end_us - 1
    assert got[6].t_us == s3.start_us and got[6].text.startswith("The real secret")
    assert got[7].t_us == 1_234_567 and got[7].label == "note" and got[8].t_us == 2_000_000
    with pytest.raises(KeyError):
        F.resolve_items(["w9999"], index=ix)
    with pytest.raises(KeyError):
        F.resolve_items(["banana"], index=ix)
    with pytest.raises(ValueError):
        F.resolve_items(["w0001:later"], index=ix)
    with pytest.raises(ValueError):  # IDs need an index
        F.resolve_items(["w0001"])


def test_resolve_items_through_timeline(take_index: TakeIndex):
    tl = Timeline(fps=Fraction(30), duration=Fraction(10), word_map={
        "w0001": WordSpan(out_start=Fraction(0), out_end=Fraction(1, 2)),
        "w0002": WordSpan(out_start=Fraction(1, 2), out_end=Fraction(1)),
        "w0009": None,  # cut
    })
    got = F.resolve_items(["w0002", "w0002:end", "w0009", "s002"], index=take_index, timeline=tl)
    assert got[0].t_us == 500_000 and got[1].t_us == 999_999
    assert got[2].t_us is None and got[3].t_us is None  # the false start is not in the render


# ---------------------------------------------------------------------------------------------- sheets
def test_contact_sheet_layout_and_content(job_with_media: Job, take_index: TakeIndex):
    job = job_with_media
    seen: list[ResolvedFrame] = []

    def lab(r: ResolvedFrame) -> str | None:
        seen.append(r)
        return "custom" if r.kind == "sentence" else None

    items = ["w0001", "w0014:end", "g0005", "s003", FrameRequest(1_000_000, "one second"), 2_000_000]
    out = F.contact_sheet(job, items, label=lab, columns=3, thumb_w=200, title="Sheet")
    assert out.exists() and out.parent == job.critique_dir / "frames" and out.suffix == ".png"
    img = Image.open(out)
    assert img.mode == "RGB"
    assert img.width == 3 * 200 + 4 * F._GUTTER
    assert [r.ref for r in seen] == ["w0001", "w0014:end", "g0005", "s003", "t=1.000s", "t=2.000s"]
    meta = F.read_sheet_meta(out)
    assert meta["kind"] == "contact_sheet" and meta["fps"] == "30/1" and len(meta["tiles"]) == 6
    t0 = meta["tiles"][0]
    assert t0["ref"] == "w0001" and t0["t_us"] == take_index.word("w0001").start_us
    assert t0["frame"] == F.frame_at(t0["t_us"], Fraction(30))
    assert meta["tiles"][4]["rect"][1] > t0["rect"][1]  # second row
    # the first tile is exactly the frame at w0001's onset
    x, y, w, h = t0["rect"]
    arr = np.asarray(img).astype(np.int16)
    ref = F.grab_frame(job, take_index.word("w0001").start_us, width=200).astype(np.int16)
    assert (w, h) == (ref.shape[1], ref.shape[0])
    assert np.abs(arr[y:y + h, x:x + w] - ref).mean() < 1.0
    # deterministic default name; explicit path honoured
    assert F.contact_sheet(job, items, label=lab, columns=3, thumb_w=200, title="Sheet") == out
    explicit = job.path("critique", "mine.png")
    assert F.contact_sheet(job, ["w0001"], out_path=explicit) == explicit and explicit.exists()


def test_contact_sheet_render_source(job_with_media: Job, media: dict[str, Path], take_index: TakeIndex):
    render = media["render"]
    with pytest.raises(ValueError):
        F.contact_sheet(job_with_media, ["w0001"], source=render)  # IDs on a render need its timeline
    tl = Timeline(fps=Fraction(30), duration=Fraction(3), word_map={
        "w0001": WordSpan(out_start=Fraction(0), out_end=Fraction(1, 2)),
        "w0002": WordSpan(out_start=Fraction(3, 2), out_end=Fraction(2)),
        "w0009": None,
    })
    out = F.contact_sheet(job_with_media, ["w0001", "w0002", "w0009", 2_500_000], source=render, timeline=tl,
                          index=take_index, columns=4, thumb_w=100)
    arr = np.asarray(Image.open(out))
    assert arr.shape[1] == 4 * 100 + 5 * F._GUTTER
    meta = F.read_sheet_meta(out)
    assert [t["t_us"] for t in meta["tiles"]] == [0, 1_500_000, None, 2_500_000]
    # tile colours: red (w0001 @ 0 s), blue (w0002 @ 1.5 s), missing (cut), green (2.5 s)
    samples = [arr[t["rect"][1] + t["rect"][3] // 2, t["rect"][0] + t["rect"][2] // 2].astype(int)
               for t in meta["tiles"]]
    assert samples[0][0] > 180 and samples[0][2] < 80
    assert samples[1][2] > 180 and samples[1][0] < 80
    assert tuple(samples[2]) == F._MISSING
    assert samples[3][1] > 90 and samples[3][0] < 80


def test_seam_pairs_out_in_frames(media: dict[str, Path], tmp_path: Path):
    render = media["render"]
    out = F.seam_pairs(render, [Fraction(1), 2_000_000], face_delta=False, thumb_w=100, columns=1)
    assert out == render.with_name("render_seams.png") and out.exists()
    arr = np.asarray(Image.open(out)).astype(int)
    W = arr.shape[1]
    assert W == 3 * 100 + 4 * F._GUTTER  # OUT | IN | onion per row
    meta = F.read_sheet_meta(out)
    assert meta["kind"] == "seam_pairs" and len(meta["seams"]) == 2
    s1, s2 = meta["seams"]
    assert (s1["frame_out"], s1["frame_in"], s1["t_us"]) == (29, 30, 1_000_000)
    assert (s2["frame_out"], s2["frame_in"]) == (59, 60)
    assert s1["px_delta"] > 10 and s1["face_shift_pct_h"] is None

    def colour(row: int, col: int) -> np.ndarray:
        x, y, w, h = meta["seams"][row]["rects"][("out", "in", "onion")[col]]
        return arr[y + h // 2, x + w // 2]

    o1, i1, on1 = colour(0, 0), colour(0, 1), colour(0, 2)
    assert o1[0] > 180 and o1[2] < 80  # last red frame
    assert i1[2] > 180 and i1[0] < 80  # first blue frame
    assert 60 < on1[0] < 200 and 60 < on1[2] < 200  # onion = both
    o2, i2 = colour(1, 0), colour(1, 1)
    assert o2[2] > 180 and i2[1] > 90 and i2[2] < 80
    # explicit output + labels + no onion; timeline default seams
    tl = Timeline(fps=Fraction(30), duration=Fraction(3), seams=[Fraction(1), Fraction(2)])
    p2 = F.seam_pairs(render, None, timeline=tl, onion=False, face_delta=False, labels=["a", "b"],
                      out_path=tmp_path / "s.png", thumb_w=100)
    assert p2 == tmp_path / "s.png" and Image.open(p2).width == 2 * 2 * 100 + 5 * F._GUTTER
    with pytest.raises(ValueError):
        F.seam_pairs(render, [], face_delta=False)


def test_seam_pairs_face_delta_is_graceful_without_faces(media: dict[str, Path], tmp_path: Path):
    out = F.seam_pairs(media["render"], [Fraction(1)], thumb_w=100, out_path=tmp_path / "f.png")
    assert out.exists()  # no faces in flat colour → no face line, no crash (model or not)


def test_pixel_delta():
    a = np.zeros((40, 30, 3), np.uint8)
    b = np.full((40, 30, 3), 255, np.uint8)
    assert F._pixel_delta(a, a) == 0.0
    assert F._pixel_delta(a, b) == pytest.approx(100.0, abs=0.5)


def test_filmstrip_sentence_and_range(job_with_media: Job, take_index: TakeIndex):
    job = job_with_media
    out = F.filmstrip(job, "s003", n_frames=6)
    img = Image.open(out)
    assert out.exists() and img.width <= F._MAX_SHEET_W + 10
    arr = np.asarray(img).astype(int)
    meta = F.read_sheet_meta(out)
    s3 = take_index.sentence("s003")
    assert meta["kind"] == "filmstrip" and (meta["start_us"], meta["end_us"]) == (s3.start_us, s3.end_us)
    assert len(meta["frames"]) == 6 and [w["id"] for w in meta["words"]] == s3.word_ids
    ts = [f["t_us"] for f in meta["frames"]]
    assert ts == sorted(ts) and s3.start_us - 40_000 <= ts[0] and ts[-1] <= s3.end_us
    # waveform panel has plotted samples, not just background
    x, y, w, h = meta["waveform_rect"]
    panel = arr[y:y + h, x:x + w]
    wave = (np.abs(panel - np.array(F._WAVE)).sum(axis=2) < 30).sum()
    assert wave > 200
    rng = F.filmstrip(job, ("w0001", "w0008"), n_frames=4, pad_ms=200, out_path=job.path("critique", "fs.png"))
    assert rng == job.path("critique", "fs.png") and Image.open(rng).width == 4 * 240 + 5 * F._GUTTER
    # a single time item becomes one frame-long span; gaps and words resolve too
    assert F.filmstrip(job, "g0005", n_frames=2).exists()
    assert F.filmstrip(job, (FrameRequest(1_000_000), 3_000_000), n_frames=3).exists()


def test_filmstrip_without_audio(job: Job, media: dict[str, Path]):
    # no audio.wav and a silent video: the strip still renders with a "no audio" panel
    shutil.copy(media["p30"], job.proxy_path)
    out = F.filmstrip(job, (0, 2_000_000), n_frames=3)
    assert out.exists()


def test_filmstrip_on_render_with_timeline(job_with_media: Job, media: dict[str, Path], take_index: TakeIndex):
    tl = Timeline(fps=Fraction(30), duration=Fraction(3), word_map={
        "w0001": WordSpan(out_start=Fraction(0), out_end=Fraction(1, 2)),
        "w0002": WordSpan(out_start=Fraction(1, 2), out_end=Fraction(1)),
    })
    out = F.filmstrip(job_with_media, ("w0001", "w0002"), source=media["render"], timeline=tl, index=take_index,
                      n_frames=3)
    meta = F.read_sheet_meta(out)
    assert (meta["start_us"], meta["end_us"]) == (0, 1_000_000)
    assert [w["id"] for w in meta["words"]] == ["w0001", "w0002"]
    with pytest.raises(ValueError):  # IDs on a render need the timeline
        F.filmstrip(job_with_media, "w0001", source=media["render"])
    assert F.filmstrip(job_with_media, (0, 2_000_000), source=media["render"], n_frames=2).exists()
