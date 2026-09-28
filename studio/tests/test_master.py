"""Master tests: composite, x264/AAC delivery encodes, A/V sync, cover, SRT, render_document."""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from studio.compile import audio as audio_mod
from studio.compile import overlays as overlays_mod
from studio.compile.audio import AudioRender
from studio.compile.master import (
    PLATFORM_PROFILES,
    aac_encoder,
    aac_priming,
    choose_cover_time,
    encode_settings,
    master,
    render_document,
    write_srt,
)
from studio.compile.timeline import compile
from studio.compile.video import RenderError
from studio.doc.model import AssetRef, Deliverable, Insert, Licence, Segment
from studio.perception.index import VisualEvent

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")

W, H = 216, 384
FLASH = 100  # frame index of the white flash (and the audio click)


def _write_aroll(path: Path, n: int) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-s", f"{W}x{H}",
           "-framerate", "30", "-i", "pipe:0", "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    uv = np.full((H, W // 2), 512, np.uint16).tobytes()
    yy = np.linspace(200, 600, H, dtype=np.float64)[:, None] * np.ones((1, W))
    for i in range(n):
        y = np.full((H, W), 940, np.uint16) if i == FLASH else yy.astype(np.uint16)
        p.stdin.write(y.tobytes() + uv + uv)
    p.stdin.close()
    assert p.wait() == 0
    return path


def _write_overlay(path: Path, n: int) -> Path:
    """ProRes 4444 with a 50 % white box in the top-left quarter (straight alpha)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuva444p10le", "-s", f"{W}x{H}",
           "-framerate", "30", "-i", "pipe:0", "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    y = np.full((H, W), 940, np.uint16)
    c = np.full((H, W), 512, np.uint16)
    a = np.zeros((H, W), np.uint16)
    a[: H // 4, : W // 2] = 512  # ~50 %
    frame = y.tobytes() + c.tobytes() + c.tobytes() + a.tobytes()
    for _ in range(n):
        p.stdin.write(frame)
    p.stdin.close()
    assert p.wait() == 0
    return path


def _write_mix(path: Path, n_samples: int, click_at: int | None, level: float = 0.05, mono: bool = False) -> Path:
    rng = np.random.default_rng(1)
    x = (rng.standard_normal(n_samples) * level).astype(np.float32)
    x = np.convolve(x, np.ones(8, np.float32) / 8, mode="same")  # soft noise bed
    if click_at is not None:
        x[:] *= 0.02
        x[click_at:click_at + 24] = 0.8
    data = x if mono else np.stack([x, x], axis=1)
    sf.write(path, data, 48_000, subtype="FLOAT")
    return path


@pytest.fixture
def setup(job, take_index, cut_doc):
    doc = cut_doc.model_copy(update={"deliverables": [Deliverable(platform="tiktok"), Deliverable(platform="reels"),
                                                      Deliverable(platform="shorts")]})
    tl = compile(doc, take_index, job=job, width=W, height=H)
    rd = job.new_render_dir()
    n = tl.frame_count
    aroll = _write_aroll(rd / "aroll.mov", n)
    ovl = _write_overlay(rd / "overlays.mov", n)
    samples = round(tl.duration * 48_000)
    mix = _write_mix(rd / "mix.wav", samples, click_at=round(F(FLASH, 30) * 48_000))
    nomusic = _write_mix(rd / "mix_nomusic.wav", samples, click_at=None, mono=True)
    return doc, tl, rd, aroll, ovl, AudioRender(mix=mix, mix_nomusic=nomusic)


def _streams(path: Path) -> list[dict]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)["streams"]


def _frames_info(path: Path) -> list[tuple[int, str]]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "frame=key_frame,pict_type", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout
    rows = []
    for line in out.split():
        k, t = line.split(",")[:2]
        rows.append((int(k), t))
    return rows


# ============================================================================================ pure
def test_encode_settings():
    s = encode_settings("tiktok", 30.0, F(30))
    assert (s["preset"], s["crf"], s["keyint"], s["bframes"], s["maxrate"]) == ("veryslow", 15, 15, 2, 25_000_000)
    assert encode_settings("shorts", 10, F(60000, 1001))["keyint"] == 30
    assert encode_settings("shorts", 10, F(30000, 1001))["keyint"] == 15
    long = encode_settings("reels", 600.0, F(30))
    assert long["maxrate"] == int(0.9 * 300_000_000 * 8 / 600) and long["bufsize"] == 2 * long["maxrate"]
    assert encode_settings("reels", 30.0, F(30))["maxrate"] == 25_000_000
    p = encode_settings("reels", 30.0, F(30), preview=True)
    assert p["preset"] == "faster" and p["maxrate"] is None
    assert set(PLATFORM_PROFILES) == {"tiktok", "reels", "shorts"}


def test_aac_priming_is_measured():
    enc = aac_encoder()
    assert enc in ("aac", "aac_at")
    assert aac_priming(enc) in (1024, 2112)


def test_write_srt(take_index, cut_doc, tmp_path):
    tl = compile(cut_doc, take_index)
    p = write_srt(tl, tmp_path / "c.srt")
    blocks = p.read_text().strip().split("\n\n")
    assert len(blocks) == len(tl.captions)
    first = blocks[0].splitlines()
    page = tl.captions[0]

    def ts(t):
        ms = round(t * 1000)
        return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    assert first[0] == "1" and first[2] == (page.text or " ".join(w.text for w in page.words))
    assert first[1] == f"{ts(page.out_start)} --> {ts(page.out_end)}"


def test_cover_avoids_blinks_and_cutaways(take_index, cut_doc, job, tmp_path):
    tl = compile(cut_doc, take_index, job=job, width=W, height=H)
    aroll = _write_aroll(tmp_path / "a.mov", tl.frame_count)
    ix = take_index.model_copy(deep=True)
    for s in ix.visual.samples:
        if s.t_us < 1_600_000:
            s.eyes_open = 0.1  # eyes closed through the first 1.3 s of output
    ix.visual.events.append(VisualEvent(kind="blink", start_us=1_700_000, end_us=1_800_000))
    t = choose_cover_time(tl, ix, aroll)
    assert t >= F(13, 10)
    src = tl.segment_at(t).out_to_src_us(t)
    assert not (1_700_000 <= src <= 1_800_000)
    # a full-screen cutaway over the whole hook pushes the cover past it
    ins = Insert(id="i001", anchor_from_word="w0001", anchor_to_word="w0008", mode="full", job="x",
                 asset=AssetRef(source="local", path="nope.mov", licence=Licence(name="t")))
    tl2 = compile(cut_doc.model_copy(update={"inserts": [ins]}), take_index, width=W, height=H)
    assert choose_cover_time(tl2, ix, aroll) >= tl2.inserts[0].out_end


# ============================================================================================ renders
def test_master_delivery_files(setup, job):
    doc, tl, rd, aroll, ovl, audio = setup
    res = master(job, aroll, ovl, audio.mix, audio.mix_nomusic, [d.platform for d in doc.deliverables], timeline=tl)
    assert set(res.finals) == {"tiktok", "reels", "shorts"}
    assert res.nomusic is not None and res.cover is not None and res.srt is not None
    for path in [*res.finals.values(), res.nomusic]:
        st = _streams(path)
        v = next(s for s in st if s["codec_type"] == "video")
        a = next(s for s in st if s["codec_type"] == "audio")
        assert (v["codec_name"], v["profile"], v["pix_fmt"]) == ("h264", "High", "yuv420p")
        assert (v["color_primaries"], v["color_transfer"], v["color_space"], v["color_range"]) == (
            "bt709", "bt709", "bt709", "tv")
        assert v.get("chroma_location") == "left"
        assert (v["width"], v["height"], v["r_frame_rate"]) == (W, H, "30/1")
        assert int(v["nb_frames"]) == tl.frame_count
        assert abs(float(v["duration"]) - float(tl.duration)) <= 1 / 30
        assert float(v["start_time"]) == 0.0
        assert v["has_b_frames"] <= 2
        assert (a["codec_name"], a["profile"], int(a["sample_rate"]), a["channels"]) == ("aac", "LC", 48000, 2)
        assert abs(float(a["duration"]) - float(tl.duration)) <= 1 / 30
        data = path.read_bytes()
        assert b"elst" not in data  # no edit lists (Instagram / YouTube)
        assert data.find(b"moov") < data.find(b"mdat")  # +faststart
    # closed GOP of fps/2 with scene-cut keyframes disabled; at most 2 consecutive B-frames
    info = _frames_info(res.finals["tiktok"])
    keys = [i for i, (k, _) in enumerate(info) if k]
    assert keys == list(range(0, tl.frame_count, 15))
    run = best = 0
    for _, t in info:
        run = run + 1 if t == "B" else 0
        best = max(best, run)
    assert 1 <= best <= 2
    # the three platforms share one encode when their caps coincide (identical video bitstreams)
    def vmd5(p):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
                              capture_output=True, text=True, check=True).stdout
    assert vmd5(res.finals["tiktok"]) == vmd5(res.finals["shorts"]) == vmd5(res.finals["reels"])
    # sidecars
    from PIL import Image

    assert Image.open(res.cover).size == (W, H)
    srt = res.srt.read_text()
    assert srt.lstrip().startswith("1") and srt.count(" --> ") == len(tl.captions)
    assert not list(rd.glob("_video_*.mp4"))  # temporaries cleaned up


def test_av_sync_beep_and_flash(setup, job):
    doc, tl, rd, aroll, _ovl, audio = setup
    res = master(job, aroll, None, audio, platform="tiktok", timeline=tl, render_dir=rd)
    final = res.finals["tiktok"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(final), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    flash = int(np.argmax(frames.mean(axis=(1, 2))))
    assert flash == FLASH
    wav = rd / "decoded.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(final), "-c:a", "pcm_f32le", str(wav)], check=True)
    y, sr = sf.read(wav)
    click = int(np.argmax(np.abs(y[:, 0]) > 0.3))
    expected = round(F(FLASH, 30) * sr)
    assert abs(click - expected) <= 48  # within 1 ms: priming pre-trimmed, no edit list needed


def test_overlay_composited_over_aroll(setup, job):
    doc, tl, rd, aroll, ovl, audio = setup
    res = master(job, aroll, ovl, audio.mix, audio.mix_nomusic, "shorts", timeline=tl)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(res.finals["shorts"]), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw[: W * H], np.uint8).reshape(H, W).astype(float)  # native (TV-range) luma codes
    box = img[10:H // 4 - 10, 10:W // 2 - 10].mean()
    plain = img[10:H // 4 - 10, W // 2 + 10:W - 10].mean()
    assert box == pytest.approx((plain + 235) / 2, abs=4)  # 50 % white blended once, in limited range
    assert res.cover is not None


def _cover_luma(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("L"), dtype=float)


def test_cover_layer_excludes_running_captions(setup, job, monkeypatch):
    """The cover carries the hook title/texts/cards but not the running captions, rendered only up to the
    cover frame with every item's timing unchanged."""
    doc, tl, rd, aroll, ovl, audio = setup
    assert tl.captions and tl.texts
    seen = {}

    def fake_render(props, out_path, **kw):
        seen["props"] = props
        n = props.duration_in_frames
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuva444p10le", "-s", f"{W}x{H}",
               "-framerate", "30", "-i", "pipe:0", "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt",
               "yuva444p10le", str(out_path)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        y, c, a = (np.full((H, W), v, np.uint16) for v in (940, 512, 0))
        a[H // 2:, :] = 1023  # opaque white bottom half: distinguishable from the full layer's top-left box
        for _ in range(n):
            p.stdin.write(y.tobytes() + c.tobytes() + c.tobytes() + a.tobytes())
        p.stdin.close()
        assert p.wait() == 0
        return Path(out_path)

    monkeypatch.setattr(overlays_mod, "render_overlay_props", fake_render)
    res = master(job, aroll, ovl, audio.mix, audio.mix_nomusic, "tiktok", timeline=tl)
    props = seen["props"]
    tc = choose_cover_time(tl, job.load_index(), aroll)
    assert props.captions == [] and props.texts  # captions dropped, the hook title kept
    assert props.duration_in_frames == round(tc * 30) + 1
    full = overlays_mod.build_overlay_props(tl, index=job.load_index(), platforms=["tiktok"])
    assert [(t.start, t.end) for t in props.texts] == [(t.start, t.end) for t in full.texts]  # timing unchanged
    img = _cover_luma(res.cover)
    assert img[H // 2 + 10:, :].mean() > 225  # the cover layer was composited …
    box, plain = img[10:H // 4 - 10, 10:W // 2 - 10].mean(), img[10:H // 4 - 10, W // 2 + 10:].mean()
    assert box < plain + 12  # … not the full one (whose top-left box is 50 % white)
    assert not (res.render_dir / "_cover_overlay.mov").exists()


def test_cover_falls_back_to_full_layer(setup, job, monkeypatch):
    doc, tl, rd, aroll, ovl, audio = setup

    def broken(*a, **kw):
        raise overlays_mod.OverlayRenderError("no node")

    monkeypatch.setattr(overlays_mod, "render_overlay_props", broken)
    res = master(job, aroll, ovl, audio.mix, audio.mix_nomusic, "tiktok", timeline=tl)
    img = _cover_luma(res.cover)
    assert img[10:H // 4 - 10, 10:W // 2 - 10].mean() > img[10:H // 4 - 10, W // 2 + 10:].mean() + 20  # full layer
    assert any("caption-free cover layer failed" in (e.get("note") or "") for e in job.read_trace())


def test_cover_avoids_title_animation(take_index, cut_doc, job, tmp_path):
    from studio.compile.master import _text_factor

    doc = cut_doc.model_copy(update={"texts": [cut_doc.texts[0].model_copy(update={"anchor_from_word": "w0004"})]})
    tl = compile(doc, take_index, job=job, width=W, height=H)
    title = tl.texts[0]
    assert title.out_start > 0
    hook = F(3)
    assert _text_factor(tl, title.out_start + F(1, 10), hook) == pytest.approx(0.3)  # popping in
    assert _text_factor(tl, title.out_start + F(6, 10), hook) == 1.0  # settled
    assert _text_factor(tl, title.out_end - F(1, 30), hook) == pytest.approx(0.3)  # fading out
    assert _text_factor(tl, title.out_start - F(1, 10), hook) == pytest.approx(0.9)  # title not up yet
    aroll = _write_aroll(tmp_path / "a.mov", tl.frame_count)
    t = choose_cover_time(tl, take_index, aroll)
    assert title.out_start + F(1, 2) <= t < title.out_end - F(1, 5)


def test_mono_mix_keeps_bs1770_loudness(setup, job):
    import pyloudnorm as pyln

    doc, tl, rd, aroll, _ovl, _audio = setup
    samples = round(tl.duration * 48_000)
    mono = _write_mix(rd / "mono.wav", samples, click_at=None, level=0.2, mono=True)
    x, sr = sf.read(mono)
    target = pyln.Meter(sr).integrated_loudness(x)
    with pytest.raises(RenderError):  # no timeline given and none next to the A-roll
        master(job, aroll, None, mono, mono, "tiktok")
    tl.save(rd / "timeline.json")  # the spec-style call: files only, the timeline is read from the render dir
    res = master(job, aroll, None, mono, mono, "tiktok")
    wav = rd / "dec.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(res.finals["tiktok"]), "-c:a", "pcm_f32le", str(wav)],
                   check=True)
    y, sr2 = sf.read(wav)
    assert y.shape[1] == 2 and np.allclose(y[:, 0], y[:, 1], atol=1e-3)
    assert pyln.Meter(sr2).integrated_loudness(y) == pytest.approx(target, abs=0.5)


def test_render_document_end_to_end(job, take_index, cut_doc, monkeypatch):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=432x768:r=30:d=4", "-c:v",
                    "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-colorspace", "bt709", str(job.mezz_path)], check=True)
    doc = cut_doc.model_copy(update={"segments": [Segment(id="seg001", from_word="w0001", to_word="w0008")],
                                     "inserts": [], "texts": []})
    doc.audio.sfx = []

    def fake_overlays(job_, timeline, out_dir, *, preview=False, **kw):
        return None

    def fake_audio(job_, doc_, timeline, out_dir, *, preview=False, **kw):
        n = round(timeline.duration * 48_000)
        m = _write_mix(Path(out_dir) / "mix.wav", n, click_at=None)
        nm = _write_mix(Path(out_dir) / "mix_nomusic.wav", n, click_at=None)
        return AudioRender(mix=m, mix_nomusic=nm)

    monkeypatch.setattr(overlays_mod, "render_overlays", fake_overlays)
    monkeypatch.setattr(audio_mod, "render_audio", fake_audio)
    res = render_document(job, doc, take_index)
    rd = res.render_dir
    assert rd.name == "r1" and rd.parent == job.renders_dir
    for name in ("timeline.json", "aroll.mov", "final_tiktok.mp4", "final_nomusic.mp4", "cover.jpg", "captions.srt",
                 "render.json"):
        assert (rd / name).exists(), name
    man = json.loads((rd / "render.json").read_text())
    tl = compile(doc, take_index, job=job)
    assert man["frames"] == tl.frame_count and man["doc_version"] == doc.version and man["fps"] == "30/1"
    v = next(s for s in _streams(rd / "final_tiktok.mp4") if s["codec_type"] == "video")
    assert (v["width"], v["height"], int(v["nb_frames"])) == (1080, 1920, tl.frame_count)
    assert any(e.get("stage") == "master" for e in job.read_trace())
    shutil.rmtree(rd, ignore_errors=True)  # full-resolution intermediates are large; free the disk
