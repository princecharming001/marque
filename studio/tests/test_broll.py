"""Tests for studio.broll (sources, rank, conform).

Keyless tests are offline: HTTP goes through ``httpx.MockTransport``, the embedder is a deterministic
colour-based fake (installed for every test), media are synthesized with ffmpeg/PIL. Apple Vision OCR,
YuNet and Playwright run locally when installed (skipped otherwise). ``@pytest.mark.real`` tests hit Pexels
(and one Higgsfield generation) with real keys.
"""

from __future__ import annotations

import io
import itertools
import json
import math
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from studio.broll import conform as cf
from studio.broll import rank, sources
from studio.broll.sources import BrollCandidate
from studio.config import MissingKeyError, Settings
from studio.doc.model import AssetRef, Licence
from studio.jobs import Job

FFMPEG = shutil.which("ffmpeg")
REAL_TAKE = Path("/Users/home/studio-testdata/qa-editor-real-take40.mov")
REAL_HLG = Path("/Users/home/studio-testdata/qa-editor-var-hdr_hlg.mov")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


# ============================================================================================ helpers
class FakeEmbedder:
    """Deterministic 4-d embedding: share of red / green / blue 'subject' pixels + background."""

    name = "fake"
    logit_scale = 100.0

    @staticmethod
    def _vec(r: float, g: float, b: float) -> np.ndarray:
        v = np.array([r, g, b, 0.05 + max(0.0, 1 - r - g - b) * 0.3], np.float32)
        return v / (np.linalg.norm(v) + 1e-8)

    def embed_images(self, images: Any) -> np.ndarray:
        out = []
        for im in images:
            m = np.asarray(im)[..., :3].reshape(-1, 3).astype(np.int32)
            red = ((m[:, 0] > 170) & (m[:, 1] < 80) & (m[:, 2] < 80)).mean()
            green = ((m[:, 1] > 170) & (m[:, 0] < 80) & (m[:, 2] < 80)).mean()
            blue = ((m[:, 2] > 170) & (m[:, 0] < 80) & (m[:, 1] < 80)).mean()
            out.append(self._vec(red, green, blue))
        return np.stack(out)

    def embed_texts(self, texts: Any) -> np.ndarray:
        out = []
        for t in texts:
            t = t.lower()
            out.append(self._vec(float("red" in t), float("green" in t), float("blue" in t)))
        return np.stack(out)


@pytest.fixture(autouse=True)
def _fake_embedder():
    rank.set_embedder(FakeEmbedder())
    yield
    rank.set_embedder(None)


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def read_frame(path: Path, t: float, w: int, h: int) -> np.ndarray:
    out = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}:in_color_matrix=bt709:out_range=pc,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3)


_TAGS = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Synthetic b-roll: landscape clip with a red square on the right, a blurred copy, a blue clip, a clip
    with a hard cut, a letterboxed clip, a clip with a static logo over moving noise, stills."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    d = tmp_path_factory.mktemp("broll_media")
    m: dict[str, Path] = {}
    enc = ["-c:v", "libx264", "-crf", "28", "-g", "25", "-preset", "veryfast"]
    # textured background (a fine grid) so the clip is sharp everywhere, like real footage
    bg = "color=c=0x305070:size=960x540:rate=25:duration=4,drawgrid=w=24:h=24:t=1:c=0x7090B0"
    sq = "color=c=0xE01010:size=150x150:rate=25:duration=4"
    m["red"] = d / "red_square.mp4"  # square spans x ≈ 0.672–0.859 of the width
    ffmpeg("-f", "lavfi", "-i", bg, "-f", "lavfi", "-i", sq, "-filter_complex",
           f"[0:v]noise=alls=6:allf=t[b];[b][1:v]overlay=x='675+30*sin(t)':y=195,format=yuv420p,{_TAGS}[v]",
           "-map", "[v]", *enc, str(m["red"]))
    m["red_blur"] = d / "red_blur.mp4"
    ffmpeg("-i", str(m["red"]), "-vf", f"gblur=sigma=7,{_TAGS}", *enc, str(m["red_blur"]))
    m["blue"] = d / "blue.mp4"
    ffmpeg("-f", "lavfi", "-i", bg, "-f", "lavfi", "-i", "color=c=0x1010E0:size=150x150:rate=25:duration=4",
           "-filter_complex", f"[0:v]noise=alls=6:allf=t[b];[b][1:v]overlay=x=100:y=195,format=yuv420p,{_TAGS}[v]",
           "-map", "[v]", *enc, str(m["blue"]))
    # hard cut at 2.0 s: red shot, then a green shot
    m["cut"] = d / "cut.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=0xE01010:size=540x960:rate=25:duration=2",
           "-f", "lavfi", "-i", "color=c=0x10C010:size=540x960:rate=25:duration=2",
           "-filter_complex", f"[0:v][1:v]concat=n=2:v=1,noise=alls=6:allf=t,format=yuv420p,{_TAGS}[v]",
           "-map", "[v]", *enc, str(m["cut"]))
    # letterboxed: 960x540 frame with a 960x400 picture (bars of 70 px)
    m["letterbox"] = d / "letterbox.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=960x400:rate=25:duration=2", "-vf",
           f"pad=960:540:0:70:black,format=yuv420p,{_TAGS}", *enc, str(m["letterbox"]))
    # a camera pan over a textured scene, with and without a static white logo burned in (an overlay)
    from PIL import Image, ImageDraw, ImageFont

    rng = np.random.default_rng(3)
    pano = Image.new("RGB", (1400, 960), (90, 100, 110))
    dr = ImageDraw.Draw(pano)
    for _ in range(900):
        x, y, r = int(rng.integers(0, 1400)), int(rng.integers(0, 960)), int(rng.integers(4, 40))
        col = tuple(int(v) for v in rng.integers(20, 235, 3))
        (dr.ellipse if rng.random() < 0.5 else dr.rectangle)((x - r, y - r, x + r, y + r), fill=col)
    pano_path = d / "pano.png"
    pano.save(pano_path)
    pan = "crop=540:960:x='t*250':y=0"
    logo = "drawbox=x=350:y=825:w=150:h=60:color=white@0.9:t=5,drawbox=x=380:y=845:w=90:h=20:color=white@0.9:t=fill"
    m["overlay"] = d / "pan_logo.mp4"
    ffmpeg("-loop", "1", "-framerate", "25", "-i", str(pano_path), "-t", "3", "-vf",
           f"{pan},{logo},format=yuv420p,{_TAGS}", *enc, str(m["overlay"]))
    m["clean_pan"] = d / "pan_clean.mp4"
    ffmpeg("-loop", "1", "-framerate", "25", "-i", str(pano_path), "-t", "3", "-vf",
           f"{pan},format=yuv420p,{_TAGS}", *enc, str(m["clean_pan"]))
    m["tripod"] = d / "tripod.mp4"
    ffmpeg("-loop", "1", "-framerate", "25", "-i", str(pano_path), "-t", "3", "-vf",
           f"crop=540:960:x=300:y=0,{logo},format=yuv420p,{_TAGS}", *enc, str(m["tripod"]))
    # dark bluish clip and a warm A-roll for the colour-match test
    m["dark_blue"] = d / "dark_blue.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=0x303a58:size=540x960:rate=30:duration=2", "-vf",
           f"noise=alls=4:allf=t,format=yuv420p,{_TAGS}", *enc, str(m["dark_blue"]))
    m["aroll_warm"] = d / "aroll_warm.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=0xB8A088:size=540x960:rate=30:duration=2", "-vf",
           f"noise=alls=4:allf=t,format=yuv420p,{_TAGS}", *enc, str(m["aroll_warm"]))
    # stills
    im = Image.new("RGB", (2400, 1600), (40, 70, 100))
    dr = ImageDraw.Draw(im)
    dr.ellipse((300, 500, 900, 1100), fill=(230, 20, 20))
    for i in range(0, 2400, 40):  # texture so sharpness/saliency have structure
        dr.line((i, 0, i, 1600), fill=(46, 76, 106), width=2)
    m["still"] = d / "still.png"
    im.save(m["still"])
    tall = Image.new("RGB", (1080, 4000), (250, 250, 250))
    dr = ImageDraw.Draw(tall)
    for y in range(100, 4000, 200):
        dr.rectangle((80, y, 1000, y + 120), fill=(30, 30, 30))
    m["tall"] = d / "tall.png"
    tall.save(m["tall"])
    wm = Image.new("RGB", (1080, 1920), (40, 90, 140))
    dr = ImageDraw.Draw(wm)
    font = None
    for fp in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, 110)
            break
    dr.text((150, 880), "shutterstock", font=font or ImageFont.load_default(), fill=(255, 255, 255))
    m["wm_still"] = d / "wm_still.png"
    wm.save(m["wm_still"])
    return m


@pytest.fixture
def job(tmp_path: Path) -> Job:
    return Job.create("broll-test", work_dir=tmp_path / "work")


def cand(path: Path, *, source: str = "pexels", kind: str = "video", w: int | None = None, h: int | None = None,
         fps: float | None = 25.0, sid: str | None = None) -> BrollCandidate:
    mp = rank.probe_media(path)
    return BrollCandidate(source=source, source_id=sid or path.stem, kind=kind, url="", local_path=str(path),
                          width=w or mp.width, height=h or mp.height, fps=fps,
                          duration_ms=round(mp.duration_s * 1000) if not mp.is_image else None,
                          licence={"name": "test"})


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ============================================================================================ sources: Pexels
def _video(vid: int, w: int, h: int, files: list[tuple[int | None, int | None, str]], dur: int = 8) -> dict:
    return {
        "id": vid, "width": w, "height": h, "duration": dur,
        "url": f"https://www.pexels.com/video/person-typing-on-a-laptop-{vid}/",
        "image": f"https://images.pexels.com/videos/{vid}/poster.jpeg",
        "user": {"id": 7, "name": "Jane Doe", "url": "https://www.pexels.com/@jane"},
        "video_files": [{"id": i, "quality": q, "file_type": "video/mp4", "width": fw, "height": fh,
                         "fps": 29.97 if fw else None, "link": f"https://videos.pexels.com/{vid}/{i}.mp4"}
                        for i, (fw, fh, q) in enumerate(files)],
        "video_pictures": [{"id": k, "nr": k, "picture": f"https://images.pexels.com/videos/{vid}/pic-{k}.jpg"}
                           for k in range(3)],
    }


def test_best_video_file_prefers_largest_covering_rendition() -> None:
    v = _video(1, 2160, 3840, [(None, None, "hls"), (640, 1138, "sd"), (1080, 1920, "hd"), (2160, 3840, "uhd"),
                               (4320, 7680, "uhd")])
    best, analysis = sources.best_video_file(v)
    assert (best["width"], best["height"]) == (2160, 3840)  # ≤ UHD cap, covers 1080x1920
    assert (analysis["width"], analysis["height"]) == (640, 1138)  # smallest with short side ≥ 540
    land = _video(2, 3840, 2160, [(1920, 1080, "hd"), (3840, 2160, "uhd"), (960, 540, "sd")])
    best, analysis = sources.best_video_file(land)
    assert (best["width"], best["height"]) == (3840, 2160)
    assert analysis["width"] == 960
    small = _video(3, 1280, 720, [(1280, 720, "hd"), (640, 360, "sd")])
    best, _ = sources.best_video_file(small)
    assert best["width"] == 1280  # nothing covers: the largest available
    assert sources.best_video_file({"video_files": [{"link": "x", "width": None, "height": None}]}) == (None, None)


def test_search_videos_mocked(settings: Settings) -> None:
    s = settings.with_keys(pexels="pk_test_123")
    calls: list[httpx.Request] = []
    portrait = [_video(10, 1080, 1920, [(1080, 1920, "hd"), (540, 960, "sd")]),
                _video(11, 720, 1280, [(720, 1280, "hd")])]  # too small → dropped
    anyorient = [_video(12, 3840, 2160, [(3840, 2160, "uhd"), (1920, 1080, "hd")]),
                 _video(10, 1080, 1920, [(1080, 1920, "hd")])]  # duplicate of 10

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.url.host == "api.pexels.com" and req.url.path == "/v1/videos/search"
        assert req.headers["Authorization"] == "pk_test_123"
        q = dict(req.url.params)
        assert q["query"] == "laptop typing" and q["size"] == "medium"
        vids = portrait if q.get("orientation") == "portrait" else anyorient
        return httpx.Response(200, json={"videos": vids, "page": 1, "per_page": 15},
                              headers={"X-Ratelimit-Remaining": "199"})

    res = sources.search("laptop typing", kind="video", n=5, settings=s, client=mock_client(handler))
    assert [c.id for c in res] == ["pxv_10", "pxv_12"]  # portrait first, then the landscape 4K fallback
    assert len(calls) == 2 and "orientation" not in dict(calls[1].url.params)
    c = res[0]
    assert c.kind == "video" and (c.width, c.height) == (1080, 1920) and c.fps == 29.97
    assert c.duration_ms == 8000 and c.author == "Jane Doe" and c.page_url.endswith("-10/")
    assert c.analysis_url.endswith("/1.mp4") and len(c.preview_urls) == 4
    assert c.licence["name"] == "Pexels License" and c.licence["url"] == sources.PEXELS_LICENSE_URL
    assert c.licence["holder"] == "Jane Doe" and "Pexels" in c.licence["attribution"]
    assert "person typing on a laptop" in c.description
    assert res[1].orientation == "landscape"


def test_search_photos_mocked(settings: Settings) -> None:
    s = settings.with_keys(pexels="pk")
    photo = {"id": 55, "width": 4000, "height": 6000, "url": "https://www.pexels.com/photo/red-apple-55/",
             "photographer": "Ann", "photographer_url": "https://www.pexels.com/@ann", "alt": "Red apple on a table",
             "avg_color": "#AA2211", "src": {"original": "https://images.pexels.com/photos/55/o.jpeg",
                                             "large2x": "https://images.pexels.com/photos/55/l2.jpeg",
                                             "large": "https://images.pexels.com/photos/55/l.jpeg"}}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/search"
        return httpx.Response(200, json={"photos": [photo]})

    res = sources.search("apple", kind="photo", n=3, settings=s, client=mock_client(handler),
                         fallback_any_orientation=False)
    assert len(res) == 1 and res[0].id == "pxp_55" and res[0].kind == "image"
    assert res[0].url.endswith("/o.jpeg") and res[0].description == "Red apple on a table"
    assert res[0].licence["attribution"] == "Photo by Ann on Pexels"


def test_search_errors(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MissingKeyError):
        sources.search("x", settings=settings)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    s = settings.with_keys(pexels="pk")
    with pytest.raises(sources.SourceError, match="rejected the API key"):
        sources.search("x", kind="video", settings=s, client=mock_client(lambda r: httpx.Response(401)))
    n = {"c": 0}

    def limited(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(429)

    with pytest.raises(sources.SourceError, match="rate limit"):
        sources.search("x", kind="video", settings=s, client=mock_client(limited))
    assert n["c"] == 3  # bounded retries
    with pytest.raises(ValueError):
        sources.search("  ", settings=s)


def test_search_many_dedups_and_records_queries(settings: Settings) -> None:
    s = settings.with_keys(pexels="pk")

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        vids = [_video(1, 1080, 1920, [(1080, 1920, "hd")])]
        if q == "b":
            vids.append(_video(2, 1080, 1920, [(1080, 1920, "hd")]))
        return httpx.Response(200, json={"videos": vids})

    res = sources.search_many(["a", "b"], settings=s, client=mock_client(handler), fallback_any_orientation=False)
    assert [c.id for c in res] == ["pxv_1", "pxv_2"]
    assert res[0].meta["queries"] == ["a", "b"]


def test_download_registers_asset_with_licence(job: Job, media: dict[str, Path]) -> None:
    payload = media["red"].read_bytes()
    c = BrollCandidate(source="pexels", source_id="999", kind="video",
                       url="https://videos.pexels.com/999/uhd.mp4?token=abc", width=1920, height=1080,
                       page_url="https://www.pexels.com/video/red-999/", author="Jane",
                       licence=sources._pexels_licence("https://www.pexels.com/video/red-999/", "Jane", None, "video"),
                       query="red square")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "videos.pexels.com"
        return httpx.Response(200, content=payload, headers={"content-type": "video/mp4"})

    a = sources.download(c, job, client=mock_client(handler))
    assert a.id == "pxv_999" and a.path == "assets/broll/pxv_999.mp4" and a.kind == "video"
    assert (a.width, a.height) == (960, 540) and abs(a.duration_ms - 4000) <= 50
    assert a.licence is not None and a.licence.name == "Pexels License"
    assert a.licence.record_path == "assets/broll/pxv_999.licence.json"
    rec = job.load_json(a.licence.record_path)
    assert rec["author"] == "Jane" and rec["download_url"] == "https://videos.pexels.com/999/uhd.mp4"  # no query
    assert rec["media"]["width"] == 960 and len(rec["sha256"]) == 64
    assert job.load_asset("pxv_999") == a
    assert c.asset == a and Path(c.local_path).exists()
    assert os.stat(c.local_path).st_mode & 0o777 == 0o644  # not mkstemp's 0600
    # idempotent: second download reuses the file
    a2 = sources.download(c, job, client=mock_client(lambda r: httpx.Response(500)))
    assert a2.path == a.path


def test_download_rejects_html(job: Job) -> None:
    c = BrollCandidate(source="pexels", source_id="1", kind="video", url="https://videos.pexels.com/1.mp4")
    h = mock_client(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))
    with pytest.raises(sources.SourceError, match="not media"):
        sources.download(c, job, client=h)


def test_creator_media_registers(job: Job, media: dict[str, Path]) -> None:
    cs = sources.creator_media([media["still"], media["red"]], job=job, description="my desk")
    assert [c.kind for c in cs] == ["image", "video"]
    for c in cs:
        assert c.id.startswith("cr_") and c.licence["name"] == "creator-owned"
        a = job.load_asset(c.id)
        assert a is not None and a.licence.name == "creator-owned" and (job.root / a.path).exists()
    with pytest.raises(sources.SourceError):
        sources.creator_media("/nonexistent/file.mp4")


# ============================================================================================ sources: Higgsfield
def _png_bytes(w: int = 64, h: int = 112) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _hf_handler(statuses: list[dict], seen: dict[str, Any]):
    polls = iter(statuses)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/cancel"):
            seen["cancel"] = True
            return httpx.Response(200, json={"status": "canceled"})
        if req.method == "POST":
            seen["submit_path"] = req.url.path
            seen["auth"] = req.headers["Authorization"]
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={
                "status": "queued", "request_id": "req-123",
                "status_url": "https://api.higgsfield.ai/requests/req-123/status",
                "cancel_url": "https://api.higgsfield.ai/requests/req-123/cancel"})
        if req.url.host == "api.higgsfield.ai":
            assert req.url.path == "/requests/req-123/status"
            seen["polls"] = seen.get("polls", 0) + 1
            return httpx.Response(200, json=next(polls))
        seen["download"] = str(req.url)
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    return handler


def test_generate_still_stylized_mocked(job: Job, settings: Settings) -> None:
    s = settings.with_keys(higgsfield="kid:ksecret")
    seen: dict[str, Any] = {}
    statuses = [{"status": "queued"}, {"status": "in_progress"},
                {"status": "completed", "request_id": "req-123", "images": [{"url": "https://cdn.hf.ai/out.png"}]}]
    c = sources.generate_still("a hand-drawn piggy bank slowly filling with coins", job=job, settings=s,
                               client=mock_client(_hf_handler(statuses, seen)), sleep=lambda x: None)
    assert seen["submit_path"] == "/recraft/v4.1/pro/text-to-image"
    assert seen["auth"] == "Key kid:ksecret"
    body = seen["body"]
    assert body["aspect_ratio"] == "9:16" and body["resolution"] == "2k"
    assert "no watermark" in body["prompt"] and body["prompt"].startswith("a hand-drawn piggy bank")
    assert seen["polls"] == 3 and seen["download"] == "https://cdn.hf.ai/out.png"
    assert c.kind == "generated_image" and c.id == "hf_req-123"
    assert c.meta["ai_generated"] and not c.meta["disclosure_required"]
    a = job.load_asset(c.id)
    assert a is not None and a.kind == "generated_image" and a.licence.name.startswith("AI-generated")
    rec = job.load_json(a.licence.record_path)
    assert rec["ai_generated"] is True and rec["meta"]["model"] == "recraft/v4.1/pro/text-to-image"
    assert c.meta["output_size"] == [64, 112] and "aspect_mismatch" not in c.meta  # 64x112 ≈ 9:16
    assert sources._ratio("9:16") == pytest.approx(0.5625) and sources._ratio("bad") is None


def test_generate_still_photo_flags_disclosure(settings: Settings) -> None:
    s = settings.with_keys(higgsfield="kid:ks")
    seen: dict[str, Any] = {}
    statuses = [{"status": "completed", "images": [{"url": "https://cdn.hf.ai/p.png"}]}]
    c = sources.generate_still("espresso pouring", style="photo", seed=7, settings=s,
                               client=mock_client(_hf_handler(statuses, seen)), sleep=lambda x: None)
    assert seen["submit_path"] == "/higgsfield-ai/soul/cinema"
    assert seen["body"]["resolution"] == "1080p" and seen["body"]["seed"] == 7 and seen["body"]["batch_size"] == 1
    assert c.meta["disclosure_required"] and c.meta["photoreal"] and "PHOTOREAL" in c.licence["notes"]
    assert c.asset is None and c.url == "https://cdn.hf.ai/p.png"


def test_generate_still_failures(settings: Settings) -> None:
    s = settings.with_keys(higgsfield="kid:ks")
    with pytest.raises(sources.GenerationFailed) as ei:
        sources.generate_still("x", settings=s, sleep=lambda x: None,
                               client=mock_client(_hf_handler([{"status": "nsfw"}], {})))
    assert ei.value.status == "nsfw" and ei.value.request_id == "req-123"
    seen: dict[str, Any] = {}
    with pytest.raises(sources.GenerationFailed, match="timed out"):
        sources.generate_still("x", settings=s, timeout_s=0.0, sleep=lambda x: None,
                               client=mock_client(_hf_handler([{"status": "queued"}] * 5, seen)))
    assert seen.get("cancel") is True
    with pytest.raises(sources.SourceError, match="key_id"):
        sources.generate_still("x", settings=settings.with_keys(higgsfield="nocolon"))
    with pytest.raises(MissingKeyError):
        sources.generate_still("x", settings=settings)


def test_generate_still_retries_concurrency_limit(settings: Settings) -> None:
    s = settings.with_keys(higgsfield="kid:ks")
    n = {"submit": 0}
    inner = _hf_handler([{"status": "completed", "images": [{"url": "https://cdn.hf.ai/p.png"}]}], {})

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and n["submit"] == 0:
            n["submit"] += 1
            return httpx.Response(400, json={"detail": "Maximum number of concurrent requests (4) has been reached"})
        return inner(req)

    c = sources.generate_still("x", settings=s, sleep=lambda x: None, client=mock_client(handler))
    assert c.url == "https://cdn.hf.ai/p.png"
    with pytest.raises(sources.SourceError, match="insufficient credits"):
        sources.generate_still("x", settings=s, sleep=lambda x: None,
                               client=mock_client(lambda r: httpx.Response(403, json={"detail": "not_enough_credits"})))


# ============================================================================================ sources: screenshots
def test_screenshot_not_available(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(sources, "playwright_available", lambda s=None: (False, "playwright is not installed"))
    with pytest.raises(sources.NotAvailable, match="not installed"):
        sources.screenshot("https://example.com", settings=settings)
    with pytest.raises(ValueError):
        sources.screenshot("javascript:alert(1)", settings=settings)


@pytest.mark.slow
def test_screenshot_local_page(job: Job, tmp_path: Path) -> None:
    ok, why = sources.playwright_available()
    if not ok:
        pytest.skip(why)
    page = tmp_path / "p.html"
    page.write_text("<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head>"
                    "<body style='margin:0;background:#fff'><h1 id='t' style='font:bold 40px sans-serif'>"
                    "Signups doubled</h1><p>proof</p></body></html>")
    c = sources.screenshot(page.as_uri(), job=job, settle_ms=0, wait_until="load")
    assert (c.width, c.height) == (1440, 2560)  # 360x640 CSS px at DPR 4: the conform only ever downsamples
    assert c.kind == "screenshot" and c.id.startswith("ss_") and c.meta["capture_url"] == page.as_uri()
    a = job.load_asset(c.id)
    assert a is not None and a.kind == "screenshot" and "capture" in a.licence.name
    el = sources.screenshot(page.as_uri(), selector="#t", settle_ms=0, wait_until="load")
    assert el.height < 500 and el.width <= 1440


# ============================================================================================ rank: measurements
def test_tiles_cover_frame() -> None:
    img = np.zeros((960, 540, 3), np.uint8)
    ts = rank.tiles(img)
    assert len(ts) == 3 and all(t.shape[:2] == (540, 540) for t, _ in ts)
    assert ts[0][1][1] == 0 and ts[-1][1][1] == 420
    assert len(rank.tiles(np.zeros((500, 520, 3), np.uint8))) == 1


def test_sharpness_and_letterbox(media: dict[str, Path]) -> None:
    sharp = rank.sample_frames(media["red"], [1.0])[0]
    blur = rank.sample_frames(media["red_blur"], [1.0])[0]
    g = rank.GateConfig()
    assert rank.sharpness(sharp) > g.min_sharpness > rank.sharpness(blur)
    frames = rank.sample_frames(media["letterbox"], [0.3, 0.9, 1.5])
    x0, y0, x1, y1 = rank.detect_letterbox(frames)
    assert (x0, x1) == (0.0, 1.0) and abs(y0 - 70 / 540) < 0.01 and abs(y1 - 470 / 540) < 0.01
    assert rank.detect_letterbox(rank.sample_frames(media["red"], [0.5, 1.5, 2.5])) == (0.0, 0.0, 1.0, 1.0)


def test_sample_frames_range_and_seek_agree(media: dict[str, Path]) -> None:
    ts = [0.52, 0.84, 1.16, 1.48]
    dense = rank.sample_frames(media["red"], ts)  # single-pass decode
    seek = [rank.sample_frames(media["red"], [t])[0] for t in ts]  # per-time seeks
    for a, b in zip(dense, seek, strict=True):
        assert a is not None and b is not None and a.shape == (540, 960, 3)
        assert np.abs(a.astype(int) - b.astype(int)).mean() < 3.0


def test_static_overlay_detector(media: dict[str, Path]) -> None:
    ts = [0.1 + 2.7 * i / 7 for i in range(8)]
    ov = rank.static_overlay(rank.sample_frames(media["overlay"], ts))
    assert ov["conclusive"] and ov["camera_motion"] > 0.03
    assert ov["overlay_frac"] > 0.0015 and ov["boxes"]
    bx = ov["boxes"][0]
    assert bx[0] > 0.6 and bx[1] > 0.85  # the bottom-right logo (x 350–500, y 825–885 of 540x960)
    clean = rank.static_overlay(rank.sample_frames(media["clean_pan"], ts))
    assert clean["conclusive"] and clean["overlay_frac"] == 0.0
    # a locked-off shot cannot separate an overlay from static scene content: inconclusive, never a reject
    tri = rank.static_overlay(rank.sample_frames(media["tripod"], ts))
    assert not tri["conclusive"] and tri["overlay_frac"] == 0.0
    assert not rank.static_overlay(rank.sample_frames(media["overlay"], ts[:4]))["conclusive"]  # too few frames


def test_text_detection_on_watermark(media: dict[str, Path]) -> None:
    img = rank.load_image(str(media["wm_still"]))
    res = rank.ocr_text(img)
    if res is None:  # no Apple Vision: the MSER fallback must still find a text line
        assert rank.text_regions(img)
    else:
        assert any("shutterstock" in t.lower() for t, _, _ in res)


def test_faces_on_real_take() -> None:
    if not REAL_TAKE.exists():
        pytest.skip("real talking-head take not available")
    f = rank.sample_frames(REAL_TAKE, [5.0])[0]
    faces = rank.detect_faces(f)
    if not rank._yunet():
        pytest.skip("YuNet model not installed")
    assert len(faces) >= 1 and max(fc[3] for fc in faces) > 0.08
    assert rank.detect_faces(np.full((320, 180, 3), 90, np.uint8)) == []


def test_plan_crop_follows_named_subject(media: dict[str, Path]) -> None:
    ts = [0.5, 1.0, 1.5, 2.0, 2.5]
    frames = rank.sample_frames(media["red"], ts)
    emb = FakeEmbedder()
    need = rank.text_embedding(emb, ["red square"])
    sem = rank.semantic_positions(emb, frames[2], need, 9 / 16, (0, 0, 1, 1))
    plan = rank.plan_crop(frames, ts, 9 / 16, faces=[[]] * len(ts), semantic=sem)
    assert plan.is_static and abs(plan.window[0] - 0.3164) < 1e-3 and plan.window[1] == 1.0
    x, _, w, _ = plan.rect_at(1.5)
    # the square spans x ≈ 0.672–0.859 (1350 ± 60 px + 300 px of 1920): it must be inside the window
    assert x <= 0.675 and x + w >= 0.855
    rt = rank.CropPlan.from_dict(plan.to_dict())
    assert rt.rect_at(1.5) == pytest.approx(plan.rect_at(1.5), abs=1e-4)


def test_plan_crop_pans_for_a_moving_subject() -> None:
    ts = [i * 0.5 for i in range(9)]
    frames = []
    for i in range(9):
        f = np.full((540, 960, 3), (48, 80, 112), np.uint8)
        x = 60 + i * 95  # the subject crosses the frame
        f[200:340, x:x + 140] = (230, 20, 20)
        frames.append(f)
    plan = rank.plan_crop(frames, ts, 9 / 16, faces=[[]] * 9, max_pan_per_s=0.3)
    assert not plan.is_static and plan.drift > 0.3
    xs = [plan.center_at(t)[0] for t in ts]
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(xs))  # monotone pan, no jitter
    steps = np.diff(xs) / 0.5
    assert np.max(np.abs(steps)) <= 0.3 + 1e-6  # velocity limit


def test_plan_crop_semantic_weight_ramps_with_spread() -> None:
    """A loud blob at the bottom wins on saliency; a clear CLIP preference for the top overrides it, a
    noise-level one does not."""
    rng = np.random.default_rng(0)
    frames = []
    for _ in range(3):
        f = np.clip(rng.normal(120, 4, (960, 540, 3)), 0, 255).astype(np.uint8)
        f[620:860, 150:390] = (230, 20, 20)  # the salient (but unnamed) thing at the bottom
        frames.append(f)
    ts = [0.0, 0.5, 1.0]
    aspect = 1080 / 960  # split half
    base = rank.plan_crop(frames, ts, aspect, faces=[[]] * 3)
    assert base.path[0][2] > 0.6  # saliency alone goes for the blob
    grid = np.linspace(0, 1, 33)
    clear = (0.30 - 0.02 * grid).astype(np.float32)  # top windows 0.02 more relevant
    noise = (0.30 - 0.002 * grid).astype(np.float32)
    assert rank.plan_crop(frames, ts, aspect, faces=[[]] * 3, semantic=clear).path[0][2] < 0.4
    assert rank.plan_crop(frames, ts, aspect, faces=[[]] * 3, semantic=noise).path[0][2] > 0.6


# ============================================================================================ rank: gates & ranking
def test_analyze_candidate_gates_and_modes(media: dict[str, Path]) -> None:
    c_hd = cand(media["red"])  # 960x540: a 9:16 cover crop is only 304 px wide
    d = rank.analyze_candidate(c_hd, rank.Need("red square", duration_s=1.5), use_ocr=False)
    assert "resolution" in d["rejected_by"] and d["gates"]["resolution"]["value"] > 1.7
    assert d["gates"]["crop"]["passed"] and d["gates"]["sharpness"]["passed"]
    assert d["gates"]["duration"]["passed"] and d["gates"]["shot_change"]["passed"]
    assert 0.0 <= d["in_s"] <= 2.5 and c_hd.meta["in_ms"] == round(d["in_s"] * 1000)
    # the same clip delivered as a 4K rendition passes; as a split top half HD is plenty
    d4k = rank.analyze_candidate(cand(media["red"], w=3840, h=2160, sid="red4k"),
                                 rank.Need("red square", duration_s=1.5), use_ocr=False)
    assert not d4k["rejected_by"], d4k["rejected_by"]
    dsplit = rank.analyze_candidate(cand(media["red"], w=1920, h=1080, sid="redsplit"),
                                    rank.Need("red square", duration_s=1.5, mode="split_top"), use_ocr=False)
    assert dsplit["gates"]["resolution"]["passed"]
    # too long a need → duration gate
    dlong = rank.analyze_candidate(cand(media["red"], w=3840, h=2160, sid="long"),
                                   rank.Need("red square", duration_s=6.0), use_ocr=False)
    assert "duration" in dlong["rejected_by"]


def test_analyze_rejects_watermark_overlay_and_text(media: dict[str, Path]) -> None:
    d = rank.analyze_candidate(cand(media["overlay"], w=2160, h=3840, sid="ov"),
                               rank.Need("colourful shapes", duration_s=2.0))
    assert d["soft"]["overlay_suspected"] and d["text"]["static_overlay"]["overlay_frac"] > 0.0015
    assert "watermark" not in d["rejected_by"]  # motion evidence is a flag for the judge, not a hard gate
    ws = cand(media["wm_still"], kind="image", fps=None, sid="wm")
    dw = rank.analyze_candidate(ws, rank.Need("blue background", duration_s=2.0))
    assert "watermark" in dw["rejected_by"] or "text" in dw["rejected_by"]
    # a screenshot is allowed text (but never a stock watermark)
    shot = cand(media["tall"], source="playwright", kind="screenshot", fps=None, sid="shot")
    dshot = rank.analyze_candidate(shot, rank.Need("article", duration_s=2.0))
    assert "text" not in dshot["gates"]


def test_choose_range_avoids_shot_change(media: dict[str, Path]) -> None:
    c = cand(media["cut"], sid="cut")
    d = rank.analyze_candidate(c, rank.Need("red", duration_s=1.2), use_ocr=False)
    assert d["in_s"] + 1.2 <= 2.0 + 0.1  # the red shot, before the cut at 2.0 s
    assert d["gates"]["shot_change"]["passed"]
    whole = rank.analyze_candidate(cand(media["cut"], sid="cut2"), rank.Need("red", duration_s=3.8), use_ocr=False)
    assert "shot_change" in whole["rejected_by"]


def test_faces_gate_when_not_allowed() -> None:
    if not REAL_TAKE.exists():
        pytest.skip("real talking-head take not available")
    if not rank._yunet():
        pytest.skip("YuNet model not installed")
    c = cand(REAL_TAKE, source="pexels", sid="face")
    d = rank.analyze_candidate(c, rank.Need("a person", duration_s=1.0, allow_faces=False), use_ocr=False)
    assert d["faces"]["recognisable"] and "faces" in d["rejected_by"]


def test_rank_candidates_orders_and_drops_rejects(media: dict[str, Path]) -> None:
    cs = [cand(media["blue"], w=3840, h=2160, sid="blue"), cand(media["red_blur"], w=3840, h=2160, sid="blur"),
          cand(media["red"], w=3840, h=2160, sid="red")]
    out = rank.rank_candidates(cs, need=rank.Need("red square", duration_s=1.5, expected_false=["blue square"]),
                               top_k=3, use_ocr=False)
    ids = [c.source_id for c, _, _ in out]
    assert ids[0] == "red" and "blur" not in ids
    top = out[0][2]
    assert top["relevance"]["p_need_vs_lookalikes"] > 0.5
    full = rank.rank_candidates(cs, need="red square", top_k=3, include_rejected=True, use_ocr=False)
    assert {c.source_id for c, _, _ in full} == {"blue", "blur", "red"}
    assert "sharpness" in next(d for c, _, d in full if c.source_id == "blur")["rejected_by"]
    # recall stage keeps the best by rank
    rec = rank.retrieve(cs, "red square", top_k=2)
    assert len(rec) == 2 and rec[0][0].source_id in ("red", "blur")


def test_contact_sheet_and_judgment(media: dict[str, Path], tmp_path: Path) -> None:
    need = rank.Need("red square", duration_s=1.5, expected_false=["blue square"])
    cs = [cand(media["red"], w=3840, h=2160, sid="red"), cand(media["blue"], w=3840, h=2160, sid="blue"),
          cand(media["still"], kind="image", fps=None, sid="still")]
    ranked = rank.rank_candidates(cs, need=need, include_rejected=True, use_ocr=False)
    sheet = rank.contact_sheet_for_judgment(ranked, need=need, out_path=tmp_path / "sheet.png",
                                            frames_per_candidate=4, max_per_page=2)
    assert len(sheet.paths) == 2 and all(p.exists() for p in sheet.paths)
    from PIL import Image

    with Image.open(sheet.paths[0]) as im:
        assert im.width <= 1600
    meta = rank.read_sheet_meta(sheet.paths[0])
    assert meta["kind"] == "broll_judgment" and len(meta["ids"]) == 2
    assert set(sheet.candidate_ids) == {c.id for c in cs}
    assert "none" in sheet.prompt and all(i in sheet.prompt for i in sheet.candidate_ids)
    red_id = cs[0].id
    blue_id = cs[1].id
    good = {k: 5 for k, _ in rank.RUBRIC_ITEMS}
    weak = {**good, "subject": 3}
    j = rank.parse_judgment(json.dumps({"scores": {red_id: good, blue_id: weak}, "pick": red_id, "reason": "ok"}),
                            sheet)
    assert j.pick == red_id and j.accepted == [red_id] and not j.overridden
    j2 = rank.parse_judgment({"scores": {red_id: weak, blue_id: weak}, "pick": blue_id}, sheet)
    assert j2.pick is None and j2.overridden and j2.accepted == []
    j3 = rank.parse_judgment("Here you go: {\"scores\": {\"zz\": {}}, \"pick\": \"none\"}", sheet)
    assert j3.pick is None and any("unknown" in e for e in j3.errors)
    j4 = rank.parse_judgment("not json", sheet)
    assert j4.pick is None and j4.accepted == []


# ============================================================================================ conform
def _asset(path: Path, **kw: Any) -> AssetRef:
    kind = kw.pop("kind", "video")
    return AssetRef(kind=kind, source=kw.pop("source", "pexels"), source_id=kw.pop("sid", path.stem),
                    path=str(path), licence=Licence(name="test"), **kw)


def test_conform_full_crop_on_subject(media: dict[str, Path], tmp_path: Path) -> None:
    a = _asset(media["red"], query="red square", in_ms=500)
    r = cf.conform_insert(a, "full", 1.0, Fraction(30000, 1001), None, out_path=tmp_path / "full.mov",
                          size=(270, 480))
    info = probe(r.path)
    vs = info["streams"][0]
    assert len(info["streams"]) == 1  # stock audio muted
    assert vs["codec_name"] == "prores" and vs["profile"] == "HQ" and vs["pix_fmt"] == "yuv422p10le"
    assert (vs["width"], vs["height"]) == (270, 480) and vs["r_frame_rate"] == "30000/1001"
    assert vs["color_space"] == "bt709" and vs["color_transfer"] == "bt709" and vs["color_primaries"] == "bt709"
    assert int(vs["nb_read_packets"]) == r.frames == math.ceil(1.5 * 30000 / 1001)  # 1 s + 0.5 s tail handle
    assert r.recipe["cadence"]["uneven"] and r.warnings  # 25 → 29.97 fps is reported
    f = read_frame(r.path, 0.3, 270, 480)
    red = (f[..., 0] > 170) & (f[..., 1] < 80) & (f[..., 2] < 80)
    assert red.mean() > 0.10  # the subject is in the 9:16 window
    sidecar = json.loads(r.path.with_suffix(".mov.json").read_text())
    assert sidecar["key"] == r.recipe["key"] and sidecar["source_in_s"] == 0.5
    assert r.asset.in_ms == 0 and r.asset.out_ms == 1000 and r.asset.width == 270
    again = cf.conform_insert(a, "full", 1.0, Fraction(30000, 1001), None, out_path=tmp_path / "full.mov",
                              size=(270, 480))
    assert again.cached


def test_conform_modes_speed_and_handles(media: dict[str, Path], tmp_path: Path) -> None:
    a = _asset(media["red"], in_ms=1000)
    r = cf.conform_insert(a, "split_top", 1.0, 30, None, out_path=tmp_path / "split.mov", speed=2.0,
                          handles=(0.25, 0.5), semantic_crop=False, size=(270, 240))
    assert r.recipe["size"] == [270, 240]
    head, tail = r.recipe["handles_s"]
    # 1 s of source is left after the 2 s used (1 s x speed 2): a 0.5 s output tail; the head costs 0.5 s
    assert head == pytest.approx(0.25) and tail == pytest.approx(0.5)
    assert r.recipe["source_first_s"] == pytest.approx(0.5)
    assert r.frames == math.ceil((0.25 + 1.0 + 0.5) * 30 - 1e-9) and r.asset.in_ms == 250
    short = cf.conform_insert(_asset(media["red"], in_ms=3000), "full", 0.8, 30, None,
                              out_path=tmp_path / "short.mov", semantic_crop=False, size=(270, 480))
    assert short.recipe["handles_s"][1] == pytest.approx(0.2)  # tail clipped to the source end
    pip = cf.conform_insert(_asset(media["red"]), "pip", 1.0, 30, None, out_path=tmp_path / "pip.mov",
                            semantic_crop=False, handles=(0, 0))
    w, h = pip.recipe["size"]
    assert abs(w / h - 16 / 9) < 0.02  # PiP keeps the source aspect
    with pytest.raises(cf.ConformError, match="only"):
        cf.conform_insert(_asset(media["red"], in_ms=3500), "full", 2.0, 30, None, out_path=tmp_path / "x.mov")
    with pytest.raises(ValueError):
        cf.conform_insert(_asset(media["red"]), "sideways", 1.0, 30, None)


def test_conform_still_ken_burns_push(media: dict[str, Path], tmp_path: Path) -> None:
    a = _asset(media["still"], kind="image", query="red circle")
    r = cf.conform_insert(a, "full", 1.0, 30, None, out_path=tmp_path / "kb.mov", size=(270, 480),
                          handles=(0.0, 0.0))
    assert r.recipe["ken_burns"]["kind"] == "push" and r.frames == 30
    first = read_frame(r.path, 0.0, 270, 480)
    last = read_frame(r.path, 29 / 30, 270, 480)

    def red_area(f: np.ndarray) -> float:
        return float(((f[..., 0] > 170) & (f[..., 1] < 80)).sum())

    ratio = red_area(last) / max(1.0, red_area(first))
    zoom_last = math.exp(math.log(1.07) * 29 / 30)
    assert red_area(first) > 500
    assert ratio == pytest.approx(zoom_last ** 2, rel=0.06)  # the circle grows by the push² (area)


def test_conform_tall_screenshot_scrolls(media: dict[str, Path], tmp_path: Path) -> None:
    a = _asset(media["tall"], kind="screenshot", source="playwright")
    r = cf.conform_insert(a, "full", 1.0, 30, "/nonexistent-is-ignored-for-screenshots.mov",
                          out_path=tmp_path / "scroll.mov", size=(270, 480), handles=(0, 0))
    assert r.recipe["ken_burns"]["kind"] == "scroll"
    assert r.recipe["color"]["strength"] == 0.0 and r.recipe["color"]["transfer"] is None  # UI white stays white
    f0 = read_frame(r.path, 0.0, 270, 480)
    f1 = read_frame(r.path, 29 / 30, 270, 480)
    assert np.abs(f0.astype(int) - f1.astype(int)).mean() > 5
    # a screen-sized (9:16) capture cropped for a split holds on the element with a push: no scrolling text
    from PIL import Image

    shot = tmp_path / "viewport.png"
    Image.open(media["tall"]).crop((0, 0, 1080, 1920)).save(shot)
    sp = cf.conform_insert(_asset(shot, kind="screenshot", source="playwright"), "split_top", 1.0, 30, None,
                           out_path=tmp_path / "vp.mov", size=(270, 240), handles=(0, 0), semantic_crop=False)
    assert sp.recipe["ken_burns"]["kind"] == "push"


def test_color_transfer_moves_toward_reference(media: dict[str, Path], tmp_path: Path) -> None:
    ref = cf.aroll_color_stats(media["aroll_warm"])
    src = cf.aroll_color_stats(cf.ARollReference(str(media["dark_blue"])))
    t = cf.ColorTransfer.between(src, ref, 0.5)
    assert 0 < t.l_shift <= cf.ColorTransfer.MAX_L_SHIFT and t.b_shift > 0  # brighter, warmer
    assert cf.ColorTransfer.between(src, ref, 0.0).is_identity
    # black and white points never move
    bw = np.array([[[0, 0, 0], [1, 1, 1]]], np.float32)
    assert np.allclose(t.apply(bw), bw, atol=2e-3)
    r = cf.conform_insert(_asset(media["dark_blue"]), "full", 1.0, 30, media["aroll_warm"],
                          out_path=tmp_path / "graded.mov", size=(270, 480), handles=(0, 0), semantic_crop=False)
    import cv2

    before = rank.sample_frames(media["dark_blue"], [0.5])[0].astype(np.float32) / 255
    after = read_frame(r.path, 0.5, 270, 480).astype(np.float32) / 255
    tr = cf.ColorTransfer(**r.recipe["color"]["transfer"])
    lab_b = cv2.cvtColor(before, cv2.COLOR_RGB2Lab).reshape(-1, 3).mean(0)
    lab_x = cv2.cvtColor(tr.apply(before), cv2.COLOR_RGB2Lab).reshape(-1, 3).mean(0)
    lab_a = cv2.cvtColor(after, cv2.COLOR_RGB2Lab).reshape(-1, 3).mean(0)
    assert np.abs(lab_a - lab_x).max() < 1.5  # the render applies exactly the recorded transfer
    assert lab_a[0] > lab_b[0] + 5 and lab_a[2] > lab_b[2] + 2  # brighter and warmer, toward the A-roll
    assert r.recipe["color"]["strength"] == 0.5


def test_conform_semantic_phrases_prefer_the_ranking_need(job: Job, media: dict[str, Path]) -> None:
    c = cand(media["red"], w=3840, h=2160, sid="phr")
    c.query, c.description = "red", "a red square on a blue grid"
    rank.analyze_candidate(c, rank.Need("red square sliding", queries=["red square"], duration_s=1.0),
                           use_ocr=False, job=job)
    a = sources.download(c, job)
    assert cf._semantic_phrases(a, job) == ["red square sliding", "red square", "a red square on a blue grid", "red"]
    assert cf._semantic_phrases(a.model_copy(update={"id": None}), None) == ["a red square on a blue grid", "red"]


def test_conform_asset_wrapper_and_registry(job: Job, media: dict[str, Path]) -> None:
    c = cand(media["red"], w=1920, h=1080, sid="wrap")
    a = sources.download(c, job)
    p = cf.conform_asset(job, a, fps=Fraction(30), width=1080, height=960, duration_s=Fraction(1), semantic_crop=False)
    assert p.exists() and p.parent == job.assets_dir / "broll" / "conformed"
    rec = json.loads(p.with_suffix(".mov.json").read_text())
    assert rec["mode"] == "split_top" and rec["size"] == [1080, 960]
    assert rec["handles_s"][0] == 0.0 and rec["asset_id"] == a.id
    assert (job.logs_dir).exists()


def test_conform_uses_ranking_crop(job: Job, media: dict[str, Path]) -> None:
    c = cand(media["red"], w=3840, h=2160, sid="rk")
    rank.analyze_candidate(c, rank.Need("red square", duration_s=1.0), use_ocr=False, job=job)
    a = sources.download(c, job)
    r = cf.conform_insert(a, "full", 1.0, 25, None, job=job, size=(270, 480), handles=(0, 0))
    assert r.recipe["crop"] == c.meta["crop"]


def test_conform_hdr_creator_clip_is_tonemapped(synth_hlg: Path, tmp_path: Path) -> None:
    a = _asset(synth_hlg, source="creator")
    r = cf.conform_insert(a, "full", 1.0, 30, None, out_path=tmp_path / "hlg.mov", size=(270, 480),
                          handles=(0, 0), semantic_crop=False)
    vs = probe(r.path)["streams"][0]
    assert r.recipe["source"]["hdr_tonemapped"] and vs["color_transfer"] == "bt709"


# ======================================================================================== colour fit & grade preview
@pytest.fixture(scope="session")
def grade_media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Near-neutral clips: a warm A-roll (neutral b* ≈ +8), a cool insert (b* ≈ -8), a grey insert, a B&W one."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    d = tmp_path_factory.mktemp("grade_media")
    m: dict[str, Path] = {}
    enc = ["-c:v", "libx264", "-crf", "20", "-g", "30", "-preset", "veryfast"]
    for name, col in (("aroll_warm", "0x8C8074"), ("cool", "0x74808E"), ("grey", "0x808080")):
        m[name] = d / f"{name}.mp4"
        ffmpeg("-f", "lavfi", "-i", f"color=c={col}:size=540x960:rate=30:duration=2", "-vf",
               f"noise=alls=3:allf=t,format=yuv420p,{_TAGS}", *enc, str(m[name]))
    m["bw"] = d / "bw.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=2", "-vf",
           f"hue=s=0,format=yuv420p,{_TAGS}", *enc, str(m["bw"]))
    return m


def test_neutral_delta_e_and_grade_gate(grade_media: dict[str, Path]) -> None:
    ref = cf.aroll_color_stats(grade_media["aroll_warm"])
    assert ref.neutral_reliable and 6 < ref.wb_b < 11 and not ref.monochrome
    cool = rank.sample_frames(grade_media["cool"], [0.5, 1.0, 1.5])
    st = cf.ColorStats.from_frames(cool)
    t = cf.ColorTransfer.between(st, ref, 0.5)
    before, after = cf.neutral_delta_e(cool, ref), cf.neutral_delta_e(cool, ref, t)
    assert before is not None and after is not None and before > 14 and 5 < after < before
    # a colourful frame (no neutral pixels) is no measurement: never gated on white balance
    red = [np.full((96, 54, 3), (220, 30, 30), np.uint8)]
    assert cf.neutral_delta_e(red, ref) is None
    need = rank.Need("a grey wall", duration_s=1.0)
    d_cool = rank.analyze_candidate(cand(grade_media["cool"], w=2160, h=3840, sid="cool", fps=30.0), need,
                                    use_ocr=False, a_roll_reference=grade_media["aroll_warm"])
    assert "grade" in d_cool["rejected_by"] and d_cool["gates"]["grade"]["value"] > 5
    assert d_cool["color"]["neutral_delta_e_before"] > d_cool["color"]["neutral_delta_e_after"]
    d_grey = rank.analyze_candidate(cand(grade_media["grey"], w=2160, h=3840, sid="grey", fps=30.0), need,
                                    use_ocr=False, a_roll_reference=ref)
    assert d_grey["gates"]["grade"]["passed"] and d_grey["gates"]["grade"]["value"] < 5
    # without a reference there is no grade gate, but the colour stats are still reported
    d_plain = rank.analyze_candidate(cand(grade_media["grey"], w=2160, h=3840, sid="grey2", fps=30.0), need,
                                     use_ocr=False)
    assert "grade" not in d_plain["gates"] and "chroma_mean" in d_plain["color"]
    # a screenshot is never colour-matched, so never grade-gated
    shot = cand(grade_media["cool"], source="playwright", kind="screenshot", w=2160, h=3840, sid="shotc")
    d_shot = rank.analyze_candidate(shot, need, use_ocr=False, a_roll_reference=ref)
    assert "grade" not in d_shot["gates"]


def test_monochrome_flag(grade_media: dict[str, Path]) -> None:
    need = rank.Need("test pattern", duration_s=1.0)
    d = rank.analyze_candidate(cand(grade_media["bw"], w=2160, h=3840, sid="bw", fps=30.0), need, use_ocr=False,
                               a_roll_reference=grade_media["aroll_warm"])
    assert d["color"]["monochrome"] and d["soft"]["monochrome"]
    assert "black & white" in rank._label_lines(cand(grade_media["bw"], sid="bw"), d)


def test_contact_sheet_graded_with_aroll_context(grade_media: dict[str, Path], tmp_path: Path) -> None:
    from PIL import Image

    need = rank.Need("a grey wall", duration_s=1.0)
    cs = [cand(grade_media["cool"], w=2160, h=3840, sid="cool", fps=30.0),
          cand(grade_media["grey"], w=2160, h=3840, sid="grey", fps=30.0)]
    ranked = rank.rank_candidates(cs, need=need, include_rejected=True, use_ocr=False,
                                  a_roll_reference=grade_media["aroll_warm"])
    landscape_ctx = np.zeros((360, 640, 3), np.uint8)
    landscape_ctx[:, 200:440] = (200, 60, 60)  # a wider A-roll frame is centre-cropped to 9:16 (x 219–421)
    sheet = rank.contact_sheet_for_judgment(
        ranked, need=need, out_path=tmp_path / "graded.png", frames_per_candidate=4,
        a_roll_reference=grade_media["aroll_warm"],
        a_roll_context=[(grade_media["aroll_warm"], 0.5), landscape_ctx])
    meta = rank.read_sheet_meta(sheet.paths[0])
    assert meta["graded"] and meta["aroll_context"] == 2
    row = meta["layout"][0]
    assert len(row["rects"]) == 4 and len(row["aroll_rects"]) == 2
    assert "A-roll" in sheet.prompt and "grade-matched" in sheet.prompt
    cool_id = cs[0].id
    grade = sheet.meta["grades"][cool_id]
    assert grade["transfer"]["b_shift"] > 0 and grade["neutral_delta_e_after"] > 5
    with Image.open(sheet.paths[0]) as im:
        arr = np.asarray(im.convert("RGB"))
        assert im.width <= 1600
    cool_row = next(r for r in meta["layout"] if r["id"] == cool_id)
    x, y, w, h = cool_row["rects"][0]
    tile = arr[y + 10:y + h - 30, x + 5:x + w - 5].reshape(-1, 3).astype(float).mean(0)
    raw = rank.sample_frames(grade_media["cool"], [1.0])[0].reshape(-1, 3).astype(float).mean(0)
    assert tile[0] - tile[2] > raw[0] - raw[2] + 4  # the shown frame was warmed toward the A-roll
    x, y, w, h = cool_row["aroll_rects"][1]
    ctx = arr[y + 10:y + h - 30, x + 6:x + w - 6].reshape(-1, 3).astype(float).mean(0)
    assert ctx[0] > 150 and ctx[1] < 90  # the red centre of the landscape context frame
    # need defaults to the first candidate's query/description
    cs[0].query = "grey wall"
    s2 = rank.contact_sheet_for_judgment([cs[0]], out_path=tmp_path / "d.png", frames_per_candidate=2)
    assert "grey wall" in s2.prompt


def test_conform_recipe_reports_neutral_delta_e(grade_media: dict[str, Path], tmp_path: Path) -> None:
    r = cf.conform_insert(_asset(grade_media["cool"]), "full", 1.0, 30, grade_media["aroll_warm"],
                          out_path=tmp_path / "cool.mov", size=(270, 480), handles=(0, 0), semantic_crop=False)
    col = r.recipe["color"]
    assert col["neutral_delta_e_before"] > col["neutral_delta_e_after"] > 5
    assert any("ΔE" in w for w in r.warnings) and r.recipe["warnings"] == r.warnings
    again = cf.conform_insert(_asset(grade_media["cool"]), "full", 1.0, 30, grade_media["aroll_warm"],
                              out_path=tmp_path / "cool.mov", size=(270, 480), handles=(0, 0), semantic_crop=False)
    assert again.cached and again.warnings == r.warnings


# ============================================================================================ review hardening
def test_cadence_note_accounts_for_speed() -> None:
    uneven = cf._cadence_note(25.0, Fraction(30), 1.0, False)
    assert uneven["uneven"] and not uneven["slowmo_stutter"] and uneven["effective_fps"] == 25.0
    clean = cf._cadence_note(30.0, Fraction(60), 1.0, False)
    assert not clean["uneven"] and clean["ratio"] == 2.0 and clean["note"] == "cadence clean"
    fast = cf._cadence_note(25.0, Fraction(30), 2.0, False)  # 2x shows 50 new frames/s: frames drop, no judder
    assert fast["effective_fps"] == 50.0 and not fast["uneven"] and not fast["slowmo_stutter"]
    slow = cf._cadence_note(30.0, Fraction(30), 0.5, False)  # 0.5x of 30 fps = 15 new frames/s: stutter
    assert slow["slowmo_stutter"] and "60 fps source" in slow["note"]
    hfr = cf._cadence_note(120.0, Fraction(60), 0.5, False)  # 120 fps Slo-mo at 0.5x into 60 fps: clean
    assert not hfr["slowmo_stutter"] and not hfr["uneven"]
    assert not cf._cadence_note(None, Fraction(30), 1.0, True)["uneven"]


def test_exact_aspect_trims_window_about_its_centre() -> None:
    plan = rank.CropPlan(aspect=0.56, window=(0.32, 1.0), path=[(0.0, 0.5, 0.5)])
    out = cf._exact_aspect(plan, 9 / 16, 16 / 9)
    ww, wh = out.window
    assert (ww * 16 / 9) / wh == pytest.approx(9 / 16) and wh == 1.0 and ww < 0.32 + 1e-9
    tall = cf._exact_aspect(rank.CropPlan(aspect=1.0, window=(1.0, 1.0)), 2.0, 1.0)
    assert tall.window == pytest.approx((1.0, 0.5))
    same = rank.CropPlan(aspect=9 / 16, window=(9 / 16 * 9 / 16, 1.0))
    assert cf._exact_aspect(same, 9 / 16, 16 / 9) is same


def test_conform_pip_with_given_size_is_cover_cropped_not_squeezed(media: dict[str, Path], tmp_path: Path) -> None:
    """The compiler hands PiP an exact layer size: a 16:9 clip into a square layer is cropped, never squeezed
    (the 150x150 red square must stay square)."""
    a = _asset(media["red"], query="red square")
    r = cf.conform_insert(a, "pip", 1.0, 25, None, out_path=tmp_path / "pip_sq.mov", size=(200, 200),
                          handles=(0, 0))
    ww, wh = r.recipe["crop"]["window"]
    assert (ww * 960) / (wh * 540) == pytest.approx(1.0, rel=1e-3)
    f = read_frame(r.path, 0.5, 200, 200)
    red = (f[..., 0] > 170) & (f[..., 1] < 80) & (f[..., 2] < 80)
    ys, xs = np.nonzero(red)
    assert red.sum() > 500
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert bw / bh == pytest.approx(1.0, abs=0.08)


def test_face_cut_penalty_and_plan_crop_keeps_face_whole() -> None:
    face = (0.3, 0.45, 0.4, 0.35, 0.9)
    grid = np.linspace(0.25, 0.75, 5)
    pen = rank._face_cut_penalty([face], grid, 0.5, False)
    # face 0.45–0.80, padded 0.41–0.84: only the window 0.375–0.875 holds it whole; 0.5–1.0 clips the crown
    assert pen[0] > pen[1] > pen[2] > 0 and pen[3] == 0.0 and pen[4] > 0
    assert rank._face_cut_penalty([], grid, 0.5, False).tolist() == [0.0] * 5
    tiny = rank._face_cut_penalty([(0.3, 0.45, 0.02, 0.02, 0.9)], np.array([0.46]), 0.02, False)
    assert tiny[0] < 0.4  # a distant bystander counts for little
    # a clear semantic preference for the top would slice through the face: the penalty keeps it whole
    rng = np.random.default_rng(0)
    frames = []
    for _ in range(3):
        f = np.clip(rng.normal(120, 4, (960, 540, 3)), 0, 255).astype(np.uint8)
        f[430:770, 150:390] = (190, 150, 125)
        frames.append(f)
    fc = (150 / 540, 430 / 960, 240 / 540, 340 / 960, 0.9)
    sem = (0.30 - 0.02 * np.linspace(0, 1, 33)).astype(np.float32)
    plan = rank.plan_crop(frames, [0.0, 0.5, 1.0], 1080 / 960, faces=[[fc]] * 3, semantic=sem)
    _, y, _, h = plan.rect_at(0.5)
    assert not plan.face_cut and y <= fc[1] and y + h >= fc[1] + fc[3]


def test_legible_brand_text_rejects_stock_but_key_legends_pass(media: dict[str, Path],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    need = rank.Need("red square", duration_s=1.0)
    monkeypatch.setattr(rank, "ocr_text", lambda f: [("TWENTY ONE", 0.99, (0.40, 0.80, 0.20, 0.023)),
                                                     ("CAFE", 0.99, (0.45, 0.83, 0.08, 0.019))])
    d = rank.analyze_candidate(cand(media["red"], w=3840, h=2160, sid="brand"), need)
    assert "text" in d["rejected_by"] and d["gates"]["text"]["headlines"] == ["TWENTY ONE"]
    assert 'TWENTY ONE' in " ".join(rank._label_lines(cand(media["red"], sid="brand"), d))
    monkeypatch.setattr(rank, "ocr_text", lambda f: [("shift", 0.99, (0.10, 0.50, 0.05, 0.015)),
                                                     ("return", 0.99, (0.70, 0.50, 0.06, 0.016))])
    d2 = rank.analyze_candidate(cand(media["red"], w=3840, h=2160, sid="keys"), need)
    assert "text" not in d2["rejected_by"] and d2["gates"]["text"]["headlines"] == []


def test_rank_candidates_leaves_callers_need_untouched(media: dict[str, Path]) -> None:
    need = rank.Need("red square", duration_s=1.0)
    out = rank.rank_candidates([cand(media["red"], w=3840, h=2160, sid="nm")], need=need,
                               expected_false=["blue square"], use_ocr=False, include_rejected=True)
    assert need.expected_false == () and out[0][2]["relevance"]["p_need_vs_lookalikes"] is not None


def test_cadence_risk_flags_slow_motion_from_low_fps(media: dict[str, Path]) -> None:
    c = cand(media["overlay"], w=2160, h=3840, fps=25.0, sid="slow")  # a moving pan
    d = rank.analyze_candidate(c, rank.Need("shapes", duration_s=1.0, speed=0.5, output_fps=25), use_ocr=False)
    assert d["soft"]["cadence_risk"] and d["soft"]["effective_fps"] == 12.5


def test_generate_still_seed_range_cancel_url_and_soul_disclosure(settings: Settings) -> None:
    s = settings.with_keys(higgsfield="kid:ks")
    with pytest.raises(ValueError, match="seed"):
        sources.generate_still("x", seed=0, settings=s)
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/cancel"):
            seen["cancel_path"] = req.url.path
            return httpx.Response(202)
        if req.method == "POST":
            return httpx.Response(200, json={
                "status": "queued", "request_id": "r9",
                "status_url": "https://api.higgsfield.ai/requests/r9/status",
                "cancel_url": "https://api.higgsfield.ai/requests/r9/cancel-now/cancel"})
        return httpx.Response(200, json={"status": "queued"})

    with pytest.raises(sources.GenerationFailed, match="timed out"):
        sources.generate_still("x", settings=s, timeout_s=0.0, sleep=lambda x: None, client=mock_client(handler))
    assert seen["cancel_path"] == "/requests/r9/cancel-now/cancel"  # the submit's cancel_url is used as given
    done = [{"status": "completed", "images": [{"url": "https://cdn.hf.ai/s.png"}]}]
    c = sources.generate_still("a street at dusk", style="soul", settings=s, sleep=lambda x: None,
                               client=mock_client(_hf_handler(done, {})))
    assert c.meta["photoreal"] and c.meta["disclosure_required"]


def test_creator_media_has_no_filename_description(media: dict[str, Path]) -> None:
    c = sources.creator_media(media["still"])[0]
    assert c.description == "" and c.meta["original_name"] == media["still"].name
    assert media["still"].name in c.summary()


def test_conform_loops_an_animated_gif(tmp_path: Path) -> None:
    gif = tmp_path / "loop.gif"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1.2", "-vf",
           "split[a][b];[a]palettegen[p];[b][p]paletteuse", str(gif))
    c = sources.creator_media(gif)[0]
    assert c.kind == "video" and c.duration_ms == 1200  # animated: it plays, it is not a still
    a = AssetRef(kind="gif", source="creator", source_id="gif", path=str(gif), licence=Licence(name="test"))
    r = cf.conform_insert(a, "pip", 3.0, 30, None, out_path=tmp_path / "gif.mov", size=(320, 180), handles=(0, 0.5),
                          semantic_crop=False)
    assert r.recipe["loop"] and r.frames == 105  # 3 s + a full 0.5 s tail: a loop never runs out of source
    assert int(probe(r.path)["streams"][0]["nb_read_packets"]) == 105
    f0, f_loop = read_frame(r.path, 0.2, 320, 180), read_frame(r.path, 1.4, 320, 180)  # 1.4 s = 0.2 s + one loop
    assert np.abs(f0.astype(int) - f_loop.astype(int)).mean() < 6


def test_heic_still_from_an_iphone_is_readable(tmp_path: Path) -> None:
    sips = shutil.which("sips")
    if sips is None or not sources.register_heif():
        pytest.skip("needs macOS sips (to write HEIC) and pillow-heif")
    from PIL import Image

    png, heic = tmp_path / "p.png", tmp_path / "p.heic"
    im = Image.new("RGB", (800, 600), (40, 70, 100))
    im.paste((220, 30, 30), (300, 200, 500, 400))
    im.save(png)
    subprocess.run([sips, "-s", "format", "heic", str(png), "--out", str(heic)], check=True, capture_output=True)
    c = sources.creator_media(heic)[0]
    assert (c.kind, c.width, c.height) == ("image", 800, 600)
    img = rank.load_image(str(heic))
    assert img.shape == (600, 800, 3) and img[300, 400, 0] > 180
    r = cf.conform_insert(AssetRef(kind="image", source="creator", source_id="heic", path=str(heic),
                                   licence=Licence(name="test")), "full", 1.0, 30, None,
                          out_path=tmp_path / "heic.mov", size=(270, 480), handles=(0, 0), semantic_crop=False)
    assert r.frames == 30 and r.recipe["ken_burns"]["kind"] == "push"


def test_ranked_use_range_travels_with_the_registered_asset(job: Job, media: dict[str, Path]) -> None:
    """Ops copy the registry record, so the code-chosen in-point must live there (never a model timestamp)."""
    need = rank.Need("red", duration_s=1.2)
    late = cand(media["cut"], w=1080, h=1920, sid="late")
    d = rank.analyze_candidate(late, need, use_ocr=False, job=job)
    a = sources.download(late, job)  # analysed first, downloaded after
    assert a.in_ms == late.meta["in_ms"] == round(d["in_s"] * 1000)
    assert a.out_ms == a.in_ms + 1200 and job.load_asset(a.id).in_ms == a.in_ms
    early = cand(media["cut"], w=1080, h=1920, sid="early")
    b = sources.download(early, job)  # downloaded first (creator media, captures), analysed after
    assert b.in_ms == 0
    rank.analyze_candidate(early, need, use_ocr=False, job=job)
    assert job.load_asset(b.id).in_ms == early.meta["in_ms"] and early.asset.in_ms == early.meta["in_ms"]


def test_conform_without_a_readable_aroll_warns_instead_of_failing(media: dict[str, Path], tmp_path: Path) -> None:
    r = cf.conform_insert(_asset(media["red"]), "full", 0.5, 25, tmp_path / "missing_mezz.mov",
                          out_path=tmp_path / "nomezz.mov", size=(270, 480), handles=(0, 0), semantic_crop=False)
    assert r.path.exists() and r.recipe["color"]["transfer"] is None
    assert any("not grade-matched" in w for w in r.warnings)


def test_candidate_round_trips_through_json(job: Job, media: dict[str, Path]) -> None:
    c = cand(media["red"], w=1920, h=1080, sid="rt")
    c.meta["in_ms"] = 500
    sources.download(c, job)
    d = json.loads(json.dumps(c.to_dict()))
    back = sources.BrollCandidate.from_dict(d, job=job)
    assert back.id == c.id and back.meta["in_ms"] == 500 and back.asset is not None and back.asset.id == c.id
    assert sources.BrollCandidate.from_dict(d).asset is None


# ============================================================================================ real
@pytest.mark.real
def test_real_pexels_search_download_rank_conform(tmp_path: Path) -> None:
    rank.set_embedder(None)  # the real PE-Core model
    job = Job.create("broll-real", work_dir=tmp_path / "work")
    res = sources.search("laptop typing", kind="video", n=6)
    assert res, "no Pexels results"
    for c in res:
        assert c.licence["name"] == "Pexels License" and c.author and c.page_url.startswith("https://www.pexels.com/")
        assert min(c.width, c.height) >= 1080 and c.url.startswith("https://")
    need = rank.Need("hands typing on a laptop keyboard", queries=["laptop typing"], duration_s=1.5,
                     expected_false=["a phone", "a desktop computer"], output_fps=30)
    ranked = rank.rank_candidates(res[:4], need=need, job=job, include_rejected=True)
    assert ranked and all("gates" in d for _, _, d in ranked)
    print("\n" + "\n".join(f"{c.summary()} score={s:.4f} rejected={d['rejected_by']} in={d.get('in_s')}"
                            for c, s, d in ranked))
    ok = [c for c, _, d in ranked if not d["rejected_by"]] or [ranked[0][0]]
    best = min(ok, key=lambda c: (c.duration_ms or 0) * (c.width or 1) * (c.height or 1))  # smallest download
    sheet = rank.contact_sheet_for_judgment(ranked[:4], need=need, job=job)
    assert sheet.paths[0].exists()
    try:
        a = sources.download(best, job)
        assert a.licence is not None and (job.root / a.path).exists()
        r = cf.conform_insert(a.model_copy(update={"in_ms": best.meta.get("in_ms", 0)}), "full", 1.0, 30,
                              None, job=job, handles=(0.0, 0.0))
        vs = probe(r.path)["streams"][0]
        assert (vs["width"], vs["height"]) == (1080, 1920) and vs["codec_name"] == "prores"
        assert int(vs["nb_read_packets"]) == r.frames == 30
        print(json.dumps({k: r.recipe[k] for k in ("crop", "cadence", "source")}, default=str))
    finally:
        shutil.rmtree(job.assets_dir, ignore_errors=True)  # keep the shared disk lean


@pytest.mark.real
def test_real_higgsfield_still(tmp_path: Path) -> None:
    job = Job.create("broll-real-hf", work_dir=tmp_path / "work")
    # free check first: Higgsfield validates the body before billing, so a bad field is a 400 naming it
    with pytest.raises(sources.SourceError, match="is not one of"):
        sources.generate_still("a paper boat", extra={"resolution": "9k"}, timeout_s=30)
    try:
        c = sources.generate_still("a paper boat on calm blue water, minimal flat illustration", job=job)
    except sources.SourceError as e:
        if "not_enough_credits" in str(e):  # auth + endpoint + body accepted; only billing refused
            pytest.skip("Higgsfield account has no credits (request passed validation)")
        raise
    a = job.load_asset(c.id)
    assert a is not None and a.kind == "generated_image" and min(a.width, a.height) >= 1080
