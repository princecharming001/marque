"""Visual perception: pure signal logic (synthetic), model management (mocked HTTP), the full
``analyze_visual`` pipeline with a scripted face analyzer on synthetic video, MediaPipe on a no-face video,
and a slow real-footage check on qa-editor-real-take40.mov."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from studio.jobs import Job
from studio.perception import visual as V
from studio.perception.index import TakeIndex, Visual
from studio.perception.visual import FaceObs

FFMPEG = shutil.which("ffmpeg")
REAL_TAKE = Path("/Users/home/studio-testdata/qa-editor-real-take40.mov")
FPS = 30.0
FRAME_US = 1e6 / FPS


def _ff(args: list[str]) -> None:
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _t(n: int) -> np.ndarray:
    return np.round(np.arange(n) * FRAME_US).astype(np.int64)


def _model_or_skip() -> Path:
    try:
        return V.ensure_face_landmarker_model(allow_download=False)
    except FileNotFoundError:
        pytest.skip("face_landmarker.task not downloaded (run once with network to fetch it)")


# ---------------------------------------------------------------------------------------------- geometry
def test_eye_aspect_ratio():
    pts = np.zeros((6, 2))
    # p1 (0,0) — p4 (10,0) horizontal; lids at ±1.5 → vertical distances 3 and 3
    pts[0], pts[3] = (0, 0), (10, 0)
    pts[1], pts[5] = (3, -1.5), (3, 1.5)
    pts[2], pts[4] = (7, -1.5), (7, 1.5)
    assert V.eye_aspect_ratio(pts, (0, 1, 2, 3, 4, 5)) == pytest.approx(0.3)
    pts[1:3, 1] = 0.0
    pts[4:6, 1] = 0.0
    assert V.eye_aspect_ratio(pts, (0, 1, 2, 3, 4, 5)) == pytest.approx(0.0)


def _matrix(fwd: tuple[float, float, float], t: tuple[float, float, float], scale: float = 1.0) -> np.ndarray:
    f = np.asarray(fwd, float) / np.linalg.norm(fwd)
    up = np.array([0.0, 1.0, 0.0])
    x = np.cross(up, f)
    x /= np.linalg.norm(x)
    y = np.cross(f, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = x * scale, y * scale, f * scale
    m[:3, 3] = t
    return m


def test_head_angles_relative_to_camera_ray():
    center = (0.0, 0.0, -40.0)
    assert V.head_angles(_matrix((0, 0, 1), center)) == pytest.approx((0.0, 0.0), abs=1e-9)
    s, c = math.sin(math.radians(20)), math.cos(math.radians(20))
    yaw, pitch = V.head_angles(_matrix((s, 0, c), center))
    assert yaw == pytest.approx(20.0) and pitch == pytest.approx(0.0, abs=1e-9)  # turned toward frame right
    yaw, pitch = V.head_angles(_matrix((0, -math.sin(math.radians(15)), math.cos(math.radians(15))), center, 1.7))
    assert pitch == pytest.approx(-15.0) and yaw == pytest.approx(0.0, abs=1e-9)  # looking down, scale-free
    # an off-centre face looking straight at the lens reads (0, 0)
    off = (10.0, -6.0, -40.0)
    ray = -np.asarray(off) / np.linalg.norm(off)
    assert V.head_angles(_matrix(tuple(ray), off)) == pytest.approx((0.0, 0.0), abs=1e-9)
    assert V.head_angles(np.full((4, 4), np.nan)) == (0.0, 0.0)


def _landmarks(cx: float = 0.5, cy: float = 0.4, w: float = 0.3, ear_px: float = 0.28) -> np.ndarray:
    """478 synthetic landmarks inside a box, with both eyes shaped to a given EAR on a 1000x1000 frame."""
    rng = np.random.default_rng(1)
    lm = np.column_stack([rng.uniform(cx - w / 2, cx + w / 2, 478), rng.uniform(cy - w / 2, cy + w / 2, 478),
                          np.zeros(478)])
    for idx, ex in ((V.RIGHT_EYE, cx - 0.06), (V.LEFT_EYE, cx + 0.06)):
        half = 0.02
        dy = ear_px * half  # EAR = (2dy + 2dy) / (2 * 2half) = dy / half
        p1, p2, p3, p4, p5, p6 = idx
        lm[p1, :2] = (ex - half, cy)
        lm[p4, :2] = (ex + half, cy)
        lm[p2, :2] = (ex - half / 3, cy - dy)
        lm[p6, :2] = (ex - half / 3, cy + dy)
        lm[p3, :2] = (ex + half / 3, cy - dy)
        lm[p5, :2] = (ex + half / 3, cy + dy)
    return lm


def test_obs_from_landmarks():
    lm = _landmarks()
    bs = {"eyeBlinkLeft": 0.2, "eyeBlinkRight": 0.4, "jawOpen": 0.3, "eyeLookOutLeft": 0.5,
          "eyeLookInRight": 0.3, "eyeLookDownLeft": 0.4, "eyeLookDownRight": 0.2}
    o = V.obs_from_landmarks(lm, bs, _matrix((0, 0, 1), (0, 0, -40)), 1000, 1000)
    x0, y0, x1, y1 = o.box
    assert x0 >= 0.35 - 1e-9 and x1 <= 0.65 + 1e-9 and (x1 - x0) > 0.25
    assert o.ear == pytest.approx(0.28, rel=1e-6)
    assert o.blink == pytest.approx(0.3) and o.jaw == pytest.approx(0.3)
    assert o.eye_h == pytest.approx(0.4)  # both eyes toward frame right
    assert o.eye_v == pytest.approx(-0.3)  # looking down
    assert o.in_frame == 1.0 and (o.yaw, o.pitch) == pytest.approx((0.0, 0.0), abs=1e-9)
    half_out = lm.copy()
    half_out[:239, 0] -= 0.6
    assert V.obs_from_landmarks(half_out, {}, None, 1000, 1000).in_frame < 0.6


def test_select_primary():
    big = FaceObs(box=(0.1, 0.1, 0.6, 0.6))
    small = FaceObs(box=(0.7, 0.7, 0.9, 0.9))
    mid = FaceObs(box=(0.62, 0.1, 0.99, 0.5))
    assert V.select_primary([], None) is None
    assert V.select_primary([small, big], None) is big
    # continuity: the tracked face wins over a slightly larger stranger
    assert V.select_primary([big, mid], mid.box) is mid
    assert V.select_primary([small, big], small.box) is big  # but not over a much larger face


# ---------------------------------------------------------------------------------------------- filters
def test_one_euro_zero_phase_smooths_without_lag():
    rng = np.random.default_rng(0)
    n = 600
    t = np.arange(n) / FPS
    noisy = 0.5 + rng.normal(0, 0.004, n)
    sm = V.one_euro_zero_phase(noisy, t, min_cutoff=0.3, beta=6.0)
    assert np.std(np.diff(sm)) < np.std(np.diff(noisy)) / 12
    assert abs(sm.mean() - 0.5) < 0.002
    # a real move (the head swaying 0.05 of the frame at 0.4 Hz) is followed without lag
    truth = 0.5 + 0.05 * np.sin(2 * np.pi * 0.4 * t)
    moving = truth + rng.normal(0, 0.002, n)
    zp = V.one_euro_zero_phase(moving, t, min_cutoff=0.3, beta=6.0)
    causal = V.one_euro(moving, t, min_cutoff=0.3, beta=6.0)

    def lag_frames(y: np.ndarray) -> int:
        a, b = truth[60:-60] - truth.mean(), y[60:-60] - y.mean()
        return int(np.argmax([np.dot(a[: a.size - k], b[k:]) for k in range(0, 15)]))

    assert lag_frames(zp) == 0 and lag_frames(causal) >= 2
    assert np.sqrt(np.mean((zp - truth)[30:-30] ** 2)) < 0.005
    # a jump (0.2 of the frame) is followed symmetrically around the jump
    step = np.where(t < 10.0, 0.4, 0.6) + rng.normal(0, 0.002, n)
    zs = V.one_euro_zero_phase(step, t, min_cutoff=0.3, beta=6.0)
    assert abs(t[np.argmax(zs > 0.5)] - 10.0) <= 0.05
    assert abs(zs[-1] - 0.6) < 0.01 and abs(zs[0] - 0.4) < 0.01
    assert V.one_euro(np.array([]), np.array([]), min_cutoff=1, beta=0).size == 0


def test_runs_and_hysteresis():
    assert V._runs(np.array([0, 1, 1, 0, 1], bool)) == [(1, 3), (4, 5)]
    x = np.array([0.0, 0.4, 0.6, 0.4, 0.0, 0.45, 0.3, np.nan, 0.9, 0.5])
    assert V._hysteresis_runs(x, 0.55, 0.35) == [(1, 4), (8, 10)]


# ---------------------------------------------------------------------------------------------- eyes / blinks
def _eye_series(n: int, blinks: list[tuple[int, int]], rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    ear = 0.26 + rng.normal(0, 0.006, n)
    bs = 0.15 + rng.normal(0, 0.02, n)
    for s, e in blinks:
        ear[s:e] = 0.06
        bs[s:e] = 0.58
    return ear, bs


def test_eyes_open_and_blinks():
    rng = np.random.default_rng(3)
    n = 300
    blinks = [(60, 63), (150, 154), (230, 250)]  # 100 ms, 133 ms, 667 ms closure
    ear, bs = _eye_series(n, blinks, rng)
    ear[100] = 0.20  # a single-frame half-closure is not a blink
    ear[180:210] *= 0.8  # a squint (smile) lowers EAR for a second: local reference adapts
    valid = np.ones(n, bool)
    eyes = V.eyes_open_signal(ear, bs, valid, FPS)
    assert np.nanmedian(eyes) > 0.9 and eyes[61] < 0.1
    t = _t(n)
    evs = V.detect_blinks(t, eyes, FRAME_US)
    assert [(e.start_us, e.end_us) for e in evs] == [
        (t[s], round(t[e - 1] + FRAME_US)) for s, e in blinks]
    assert all(e.kind == "blink" for e in evs) and evs[0].confidence > 0.8
    assert evs[2].note.startswith("eyes closed 0.67s") and evs[0].note.startswith("blink 100ms")
    # no face → NaN and no events
    none = V.eyes_open_signal(ear, bs, np.zeros(n, bool), FPS)
    assert np.isnan(none).all() and V.detect_blinks(t, none, FRAME_US) == []


def test_eyes_open_without_ear_uses_blendshapes():
    n = 90
    bs = np.full(n, 0.12)
    bs[40:43] = 0.6
    eyes = V.eyes_open_signal(np.full(n, np.nan), bs, np.ones(n, bool), FPS)
    assert eyes[0] == pytest.approx(1.0) and eyes[41] < 0.1
    assert len(V.detect_blinks(_t(n), eyes, FRAME_US)) == 1


# ---------------------------------------------------------------------------------------------- gaze
def _gaze_case(n: int = 600) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    yaw = 6.0 + rng.normal(0, 1.0, n)
    pitch = -6.0 + rng.normal(0, 1.0, n)
    eh = rng.normal(0, 0.02, n)
    ev = -0.25 + rng.normal(0, 0.02, n)
    # 3.0–4.0 s: reads notes below the camera (head down 12°, eyes down)
    pitch[90:120] -= 12
    ev[90:120] -= 0.6
    # 8.0–8.5 s: looks off to frame-left (head + eyes)
    yaw[240:255] -= 14
    eh[240:255] -= 0.4
    # 12.0–12.1 s: a 100 ms glance (too short to matter)
    yaw[360:363] += 25
    # 15–17 s: head turns right 16° but the eyes counter-rotate (VOR) → still at the lens
    yaw[450:510] += 16
    eh[450:510] -= 16 / 25
    return {"yaw": yaw, "pitch": pitch, "eh": eh, "ev": ev}


def test_gaze_events_reading_look_away_and_vor():
    g = _gaze_case()
    n = g["yaw"].size
    t = _t(n)
    sig = V.gaze_signals(t, g["yaw"], g["pitch"], g["eh"], g["ev"], np.ones(n, bool), None)
    assert sig.baseline[0] == pytest.approx(6.0, abs=1.0)
    assert sig.baseline[1] == pytest.approx(-6.0 - 18 * 0.25, abs=1.0)
    assert np.nanmedian(sig.gaze_off) < 0.05 and sig.gaze_off[100] > 0.95
    evs = V.detect_gaze_events(t, sig, FRAME_US)
    assert [e.kind for e in evs] == ["reading", "look_away"]
    rd, la = evs
    assert abs(rd.start_us - 3_000_000) <= 100_000 and abs(rd.end_us - 4_000_000) <= 100_000
    assert "down" in rd.note and rd.confidence >= 0.5
    assert abs(la.start_us - 8_000_000) <= 100_000 and abs(la.end_us - 8_500_000) <= 100_000
    assert "frame-left" in la.note
    assert np.nanmax(sig.dev[455:505]) < 9.0  # VOR compensation: not a look-away


def test_blink_frames_do_not_fake_a_downward_look():
    n = 300
    t = _t(n)
    yaw, pitch = np.full(n, 5.0), np.full(n, -5.0)
    eh, ev = np.zeros(n), np.full(n, -0.2)
    ev[100:106] = -1.0  # closing lids push eyeLookDown up for 200 ms
    eyes = np.ones(n)
    eyes[100:106] = 0.05
    sig = V.gaze_signals(t, yaw, pitch, eh, ev, np.ones(n, bool), eyes)
    assert np.nanmax(sig.dev) < 1.0
    assert V.detect_gaze_events(t, sig, FRAME_US) == []
    # without the lid mask the same frames would read as a look away
    raw = V.gaze_signals(t, yaw, pitch, eh, ev, np.ones(n, bool), None)
    assert np.nanmax(raw.dev) > 12


def test_face_lost_events():
    n = 150
    t = _t(n)
    valid = np.ones(n, bool)
    valid[30:45] = False  # 0.5 s
    valid[80] = False  # 1 frame (33 ms): ignored
    valid[140:] = False  # tail
    evs = V.detect_face_lost(t, valid, FRAME_US)
    want = [(t[30], round(t[44] + FRAME_US)), (t[140], round(t[149] + FRAME_US))]
    assert [(e.start_us, e.end_us) for e in evs] == want
    assert "mid-take" in evs[0].note and "at the end" in evs[1].note
    assert V.detect_face_lost(t, np.zeros(n, bool), FRAME_US) == []  # never a face: no events


# ---------------------------------------------------------------------------------------------- track
def test_smooth_face_track_jitter_gaps_and_outliers():
    rng = np.random.default_rng(11)
    n = 300
    t = _t(n)
    cx = 0.5 + rng.normal(0, 0.004, n)
    cy = 0.35 + rng.normal(0, 0.004, n)
    w = 0.4 + rng.normal(0, 0.004, n)
    boxes = np.column_stack([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2])
    boxes[50] = (0.0, 0.0, 0.1, 0.1)  # one mis-fit frame
    valid = np.ones(n, bool)
    valid[100:112] = False  # 0.4 s gap: interpolated
    valid[200:260] = False  # 2 s gap: held
    out_t = t[::3].tolist()
    tr = V.smooth_face_track(t, boxes, valid, out_t)
    assert tr is not None and tr.method == "one_euro_zero_phase" and len(tr.points) == len(out_t)
    cxs = np.array([p.cx for p in tr.points])
    assert np.abs(cxs - 0.5).max() < 0.01  # outlier rejected, jitter gone
    assert np.std(np.diff(cxs)) < 0.001
    confs = {p.t_us: p.conf for p in tr.points}
    assert confs[int(t[0])] == 1.0 and confs[int(t[105])] == 0.5 and confs[int(t[231])] == 0.0
    box = tr.at(int(t[150]))
    assert box is not None and box.w == pytest.approx(0.4, abs=0.01)
    assert V.smooth_face_track(t, boxes, np.zeros(n, bool), out_t) is None


def test_blur_and_luma():
    rng = np.random.default_rng(5)
    sharp = rng.integers(0, 256, (400, 300, 3), dtype=np.uint8)
    import cv2

    soft = cv2.GaussianBlur(sharp, (0, 0), 4)
    b_sharp, v_sharp = V.blur_score(sharp, None)
    b_soft, v_soft = V.blur_score(soft, None)
    assert b_sharp < 0.1 < b_soft and v_sharp > v_soft
    b_face, _ = V.blur_score(sharp, (0.2, 0.2, 0.6, 0.6))
    assert 0.0 <= b_face < 0.1
    assert V.mean_luma(np.full((64, 64, 3), 128, np.uint8)) == pytest.approx(128 / 255, abs=1e-6)
    assert V.mean_luma(np.zeros((8, 8, 3), np.uint8)) == 0.0


# ---------------------------------------------------------------------------------------------- model management
class _FakeStream:
    def __init__(self, payload: bytes, status: int = 200):
        self.payload, self.status = payload, status

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def iter_bytes(self, n: int):
        for i in range(0, len(self.payload), n):
            yield self.payload[i:i + n]


def test_model_download_verifies_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import httpx

    payload = b"model-bytes" * 1000
    monkeypatch.setattr(V, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(V, "MODEL_SIZE", len(payload))
    calls: list[str] = []

    def fake_stream(method: str, url: str, **kw: Any) -> _FakeStream:
        calls.append(url)
        return _FakeStream(payload)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    with pytest.raises(FileNotFoundError):
        V.ensure_face_landmarker_model(models_dir=tmp_path, allow_download=False)
    p = V.ensure_face_landmarker_model(models_dir=tmp_path)
    assert p == tmp_path / "face_landmarker.task" and p.read_bytes() == payload and calls == [V.MODEL_URL]
    assert V.ensure_face_landmarker_model(models_dir=tmp_path) == p and len(calls) == 1  # cached, verified
    # a corrupted file is discarded and re-fetched
    p.write_bytes(b"garbage")
    assert V.ensure_face_landmarker_model(models_dir=tmp_path).read_bytes() == payload and len(calls) == 2
    # a tampered download is rejected and leaves nothing behind
    p.unlink()
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(payload[:-1] + b"X"))
    with pytest.raises(ValueError, match="verification"):
        V.ensure_face_landmarker_model(models_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_runtime_selftest_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model = tmp_path / "face_landmarker.task"
    model.write_bytes(b"x")
    monkeypatch.setattr(V, "_selftest_ok", {})

    class Aborted:
        returncode, stdout, stderr = -6, "", "F0000 graph_service.h:139] Check failed: service_ Service is unavailable."

    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: Aborted())
    with pytest.raises(RuntimeError, match=r"mediapipe==0\.10\.35"):
        V._ensure_runtime(model)
    assert not list(tmp_path.glob(".mediapipe_selftest_*"))

    class Passed:
        returncode, stdout, stderr = 0, "ok\n", ""

    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: Passed())
    V._ensure_runtime(model)
    assert len(list(tmp_path.glob(".mediapipe_selftest_*.ok"))) == 1
    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: pytest.fail("self-test must be cached"))
    V._ensure_runtime(model)


def test_runtime_selftest_real_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = _model_or_skip()
    model = tmp_path / "face_landmarker.task"
    shutil.copy(src, model)
    monkeypatch.setattr(V, "_selftest_ok", {})
    V._ensure_runtime(model)  # the pinned MediaPipe builds and runs the graph in a child process
    assert len(list(tmp_path.glob(".mediapipe_selftest_*.ok"))) == 1


def test_pinned_model_constants():
    assert V.MODEL_URL.startswith("https://storage.googleapis.com/mediapipe-models/face_landmarker/")
    assert "/float16/1/" in V.MODEL_URL and len(V.MODEL_SHA256) == 64 and V.MODEL_SIZE == 3_758_596


# ------------------------------------------------------------------------------------------ pipeline (fake analyzer)
class ScriptedAnalyzer:
    """Face present except 1.5–2.0 s; blink at 1.0 s; looks down (reading) 2.5–3.3 s; drifts right."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.closed = False

    def analyze(self, rgb: np.ndarray, t_ms: int) -> list[FaceObs]:
        self.calls.append(t_ms)
        assert rgb.dtype == np.uint8 and rgb.ndim == 3
        t = t_ms / 1000
        if 1.5 <= t < 2.0:
            return []
        cx = 0.5 + 0.02 * t
        box = (cx - 0.15, 0.2, cx + 0.15, 0.45)
        blink = 0.998 <= t < 1.1
        down = 2.5 <= t < 3.3
        stranger = FaceObs(box=(0.85, 0.85, 0.9, 0.9))
        return [stranger, FaceObs(box=box, ear=0.06 if blink else 0.27, blink=0.6 if blink else 0.14, jaw=0.2,
                                  eye_h=0.0, eye_v=-0.9 if down else -0.25, yaw=4.0, pitch=-20.0 if down else -5.0,
                                  in_frame=1.0)]

    def close(self) -> None:
        self.closed = True


@pytest.fixture(scope="module")
def synth_mezz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("visual_media") / "mezz_4s.mov"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=4", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(out)])
    return out


def test_analyze_visual_pipeline_with_scripted_faces(job: Job, synth_mezz: Path):
    shutil.copy(synth_mezz, job.mezz_path)
    fake = ScriptedAnalyzer()
    vis = V.analyze_visual(job, analyzer=fake)
    assert isinstance(vis, Visual) and vis.sample_fps == 10.0
    # media_info (hand-built index, ~17 s) is longer than the 4 s mezz: the decoded video bounds the analysis
    assert len(fake.calls) == 120 and fake.calls == sorted(fake.calls) and not fake.closed
    assert len(vis.samples) == 40
    assert [s.t_us for s in vis.samples[:3]] == [0, 100_000, 200_000]
    assert all(s.luma is not None and 0 < s.luma < 1 and s.blur is not None for s in vis.samples)
    lost = [s for s in vis.samples if s.face_box is None]
    assert [s.t_us for s in lost] == [1_500_000, 1_600_000, 1_700_000, 1_800_000, 1_900_000]
    assert all(s.eyes_open is None and s.gaze_off is None for s in lost)
    s0 = vis.samples[0]
    assert s0.face_box is not None and s0.face_box.w == pytest.approx(0.3) and s0.face_box.cx == pytest.approx(0.5)
    assert s0.face_conf == pytest.approx(1.0) and s0.eyes_open > 0.9 and s0.mouth_open == pytest.approx(0.2)
    assert s0.head_yaw == pytest.approx(4.0) and s0.head_pitch == pytest.approx(-5.0)
    kinds = [(e.kind, round(e.start_us / 1e6, 2), round(e.end_us / 1e6, 2)) for e in vis.events]
    assert ("blink", 1.0, 1.1) in kinds
    assert ("face_lost", 1.5, 2.0) in kinds
    reading = [k for k in kinds if k[0] == "reading"]
    assert len(reading) == 1 and abs(reading[0][1] - 2.5) <= 0.1 and abs(reading[0][2] - 3.3) <= 0.1
    assert len(kinds) == 3
    # the smoothed track follows the speaker (not the stranger), holds through the gap, stays in [0, 1]
    tr = vis.face_track
    assert tr is not None and len(tr.points) == 40
    assert tr.at(3_000_000).cx == pytest.approx(0.56, abs=0.01)
    assert all(0.0 <= p.cx <= 1.0 for p in tr.points)
    assert {p.conf for p in tr.points if 1_500_000 <= p.t_us < 2_000_000} == {0.5}
    # side files
    meta = json.loads((job.index_dir / "visual_meta.json").read_text())
    assert meta["frames"] == 120 and meta["face_frames"] == 105 and meta["multi_face_ratio"] == pytest.approx(105 / 120)
    assert meta["events"] == {"blink": 1, "face_lost": 1, "reading": 1} and meta["source_is_mezz"] is True
    dense = V.load_dense(job)
    assert dense is not None and dense["t_us"].size == 120 and dense["face"].sum() == 105
    assert list(dense["fps"]) == [30, 1]
    # the Visual round-trips through the Take Index JSON
    ix = TakeIndex.model_validate_json(job.index_path.read_text())
    ix.visual = vis
    again = TakeIndex.model_validate(ix.model_dump(mode="json"))
    assert again.visual == vis and again.face_at(3_000_000) is not None


def test_analyze_visual_factory_and_no_save(job: Job, synth_mezz: Path):
    shutil.copy(synth_mezz, job.mezz_path)
    made: list[ScriptedAnalyzer] = []

    def factory() -> ScriptedAnalyzer:
        made.append(ScriptedAnalyzer())
        return made[-1]

    vis = V.analyze_visual(job, analyzer=factory, sample_fps=5.0, save=False)
    assert len(made) == 1 and made[0].closed  # owned analyzers are closed
    assert len(vis.samples) == 20 and not (job.index_dir / "visual_meta.json").exists()


def test_analyze_visual_downsamples_high_fps(job: Job, tmp_path: Path):
    src = tmp_path / "hfr.mp4"
    _ff(["-f", "lavfi", "-i", "testsrc2=size=160x284:rate=60:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(src)])
    fake = ScriptedAnalyzer()
    vis = V.analyze_visual(job, source=src, analyzer=fake, save=False)
    assert len(fake.calls) == 30  # 60p analysed at 30 fps
    assert len(vis.samples) == 10


def test_analysis_rate_and_decode_dims():
    from fractions import Fraction

    assert V._analysis_rate(60, 30.5) == (Fraction(30), 2)
    assert V._analysis_rate(59.94, 30.5)[0] == Fraction(30000, 1001)
    assert V._analysis_rate(29.97, 30.5) == (Fraction(30000, 1001), 1)
    assert V._analysis_rate(120, 30.5) == (Fraction(30), 4)
    assert V._analysis_rate(25, 30.5) == (Fraction(25), 1)
    from studio.perception.frames import VideoProbe

    vp4k = VideoProbe(path="x", width=2160, height=3840, fps=Fraction(60), duration_us=1)
    assert V._decode_dims(vp4k, 1080) == (1080, 1920)
    land = VideoProbe(path="x", width=1920, height=1080, fps=Fraction(30), duration_us=1)
    assert V._decode_dims(land, 1080) == (1920, 1080)
    small = VideoProbe(path="x", width=540, height=960, fps=Fraction(30), duration_us=1)
    assert V._decode_dims(small, 1080) == (540, 960)


# ---------------------------------------------------------------------------------------------- MediaPipe (local model)
def test_mediapipe_no_face_video_is_graceful(job: Job, synth_mezz: Path):
    _model_or_skip()
    shutil.copy(synth_mezz, job.mezz_path)
    vis = V.analyze_visual(job)
    assert len(vis.samples) == 40
    assert all(s.face_box is None and s.eyes_open is None and s.face_conf == 0.0 for s in vis.samples)
    assert vis.events == [] and vis.face_track is None
    meta = json.loads((job.index_dir / "visual_meta.json").read_text())
    assert meta["face_found_ratio"] == 0.0 and meta["model"]["sha256"] == V.MODEL_SHA256
    ix = TakeIndex.model_validate_json(job.index_path.read_text())
    ix.visual = vis
    assert ix.face_at(1_000_000) is None


def test_detect_face_boxes_on_synthetic_frames():
    _model_or_skip()
    frames = [np.zeros((192, 108, 3), np.uint8), np.full((192, 108, 3), 200, np.uint8)]
    assert V.detect_face_boxes(frames) == [None, None]


@pytest.mark.slow
def test_real_take40_face_track(tmp_path: Path):
    if not REAL_TAKE.exists():
        pytest.skip("real test take not available")
    _model_or_skip()
    job = Job.create("real-take40", work_dir=tmp_path)
    vis = V.analyze_visual(job, source=REAL_TAKE)
    n = len(vis.samples)
    assert 400 <= n <= 410  # 40.5 s at 10 fps
    faced = [s for s in vis.samples if s.face_box is not None]
    assert len(faced) / n > 0.9
    # a close selfie: face in the middle band, eyes open most of the time, facing the lens
    assert 0.3 < np.median([s.face_box.cx for s in faced]) < 0.7
    assert np.median([s.eyes_open for s in faced]) > 0.8
    assert np.median([s.gaze_off for s in faced]) < 0.2
    assert all(s.blur is not None and s.luma is not None for s in vis.samples)
    blinks = [e for e in vis.events if e.kind == "blink"]
    assert 3 <= len(blinks) <= 20 and all(50_000 <= e.end_us - e.start_us <= 700_000 for e in blinks)
    assert not [e for e in vis.events if e.kind == "face_lost"]
    # smoothed track: no jitter, and it stays on the raw boxes
    tr = vis.face_track
    assert tr is not None and len(tr.points) == n
    cx = np.array([p.cx for p in tr.points])
    raw = np.array([s.face_box.cx for s in vis.samples])
    # handheld selfie: the face really moves (up to ~0.2 of the frame in 0.3 s), so the track must follow
    # it closely while removing the landmark jerk (second difference)
    assert np.median(np.abs(cx - raw)) < 0.01
    assert np.std(np.diff(cx, 2)) < 0.8 * np.std(np.diff(raw, 2))
    dense = V.load_dense(job)
    assert dense is not None and dense["t_us"].size == 1216 and dense["face"].all()
    meta = json.loads((job.index_dir / "visual_meta.json").read_text())
    assert meta["analysis_fps"] == "30/1" and meta["decode_size"] == [1080, 1920]
    # seam sheets measure the face across a cut (IMAGE-mode landmarker on the two frames)
    from studio.perception.frames import read_sheet_meta, seam_pairs

    sheet = seam_pairs(REAL_TAKE, [5_000_000], out_path=tmp_path / "seams.png")
    seam = read_sheet_meta(sheet)["seams"][0]
    assert seam["face_out"] and seam["face_in"] and seam["face_shift_pct_h"] < 3.0
    assert seam["face_scale"] == pytest.approx(1.0, abs=0.05)
