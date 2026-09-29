"""Visual perception: face track, blinks, look-aways, reading, face-lost, blur and luma (ARCHITECTURE §4).

``analyze_visual(job)`` decodes the **mezzanine** (upright, SDR BT.709, CFR) once, runs the MediaPipe
Face Landmarker (478 landmarks + 52 blendshapes + facial transformation matrix, VIDEO mode so the mesh
is tracked frame to frame) on every frame of a dense analysis grid, and returns a
:class:`~studio.perception.index.Visual`: 10 fps ``samples``, measured ``events`` and a smoothed
``face_track``.

Choices (quality first)
-----------------------
* **Dense analysis, sparse samples.** Blinks last 100–150 ms, so the landmarker runs at the source rate
  (capped near 30 fps: 60p → 30, 59.94 → 29.97) and events get frame-accurate boundaries; the stored
  samples are decimated to ``sample_fps`` (10) as the contract asks. The dense per-frame signals are
  also written to ``index/visual_dense.npz`` (see :func:`load_dense`) so seam code can check "is this
  exact frame mid-blink / mouth open" without re-running the model.
* **Resolution.** Frames are decoded at the display size with the short side capped at 1080 px (area
  downscale, tagged YUV matrix). The mesh model works on a 256 px face crop, so a 1080-wide decode keeps
  small or distant faces above that size; it costs no more than 720 px on an M4 (~16 ms/frame).
* **Eyes open = fused.** The ``eyeBlink`` blendshapes saturate around 0.6 for many faces and are biased
  by downward gaze; the eye aspect ratio (EAR, Soukupová & Čech 2016) has ~4x dynamic range but moves
  with head pose. Each is normalized to a *local* open reference (rolling 80th/25th percentile over
  ±1.5 s, so pose drift and squints do not read as closure) and fused 0.6·EAR + 0.4·blendshape. Blinks
  are hysteresis runs of closure (≥0.55 peak, ≥0.35 extent); a closure over 0.5 s is reported as a blink
  with an "eyes closed" note.
* **Gaze = head + eyes, against the creator's own lens direction.** Head yaw/pitch come from the facial
  transformation matrix and are measured *relative to the ray from the face to the camera* (0°,0° =
  facing the lens even when the face sits off-centre). Eye rotation comes from the ``eyeLook*``
  blendshapes, scaled 25°/unit horizontally and 18°/unit vertically: fitted on a real take where head
  and eye movements cancel while the speaker holds eye contact (vestibulo-ocular reflex, r = −0.81).
  The "at lens" direction is the creator's own robust mode (median, then mean-shift within 10°), which
  absorbs the phone-below-the-eyes selfie angle. ``gaze_off`` ramps 0→1 between 6° and 18° off that
  direction; look-aways are hysteresis runs ≥12° peak / ≥9° extent lasting ≥250 ms; a mostly-downward
  run of ≥0.5 s is ``reading``. Blink frames are excluded from gaze (closing lids fake a downward look),
  and signals are median-filtered over 5 frames before thresholding (the Blendshape V2 model card
  documents jitter; MAD ≈ 0.2).
* **Face lost** = no face for ≥ 100 ms after a face has been seen somewhere in the take (the model drops
  faces beyond ~80° look-away, so this is also a look-away signal). A take with no face at all returns
  no events and ``face_track=None`` rather than one giant "lost" event.
* **Face track** = the landmark bounding box (visible part, normalized to the upright frame), Hampel
  outlier rejection, short gaps (≤1 s) interpolated and long ones held, then a **zero-phase One-Euro
  filter** (Casiez et al. 2012; the forward and time-reversed passes are averaged, which cancels the
  filter's lag: we are offline). Centres: min cutoff 0.3 Hz, β 6; size: 0.15 Hz, β 1.5; still heads are
  rock steady and real moves are followed without lag. Crop-path design (dead zones, L1-optimal paths)
  belongs to the reframer; this track is the measured face.
* **Blur** = variance of the Laplacian of the face region rescaled to 256 px wide (whole frame when no
  face), mapped to 0..1 blurriness as ``150 / (150 + var)``: ≈0.1–0.2 sharp, ≈0.3 slightly soft (σ≈2 px
  at 1080p), ≥0.6 clearly blurred. **Luma** = mean BT.709 Y' of the frame, 0..1.
* **Head top** (hair included) is measured every 0.5 s (≤ 90 per take) by a GrabCut segmentation seeded
  by the landmark box (:func:`measure_head_top`), and summarized as one per-take ratio above the landmark
  box (:func:`head_top_ratio`), so text placed above the head clears the hair, not just the forehead.
* **Multiple faces.** Up to two faces are tracked; the primary is the largest, with continuity (IoU with
  the previous primary) winning ties, so a face in the background never steals the track.
* **Sample fields.** ``face_box`` is the visible landmark box; ``face_conf`` (the task API exposes no
  score) is the fraction of landmarks inside the frame, reduced beyond 45° of head yaw where landmark
  fits degrade; ``mouth_open`` is the ``jawOpen`` blendshape (3-frame median); ``head_yaw``/``head_pitch``
  are per-frame degrees relative to the camera ray (yaw + = toward frame right, pitch − = down);
  ``eyes_open``/``gaze_off``/``blur``/``luma`` as described. Run-level calibration (gaze baseline, face
  found ratio, EAR, blur median) goes to ``index/visual_meta.json``.

MediaPipe
---------
Pinned to ``mediapipe==0.10.35``: 1.0.1 aborts the whole process (SIGABRT, uncatchable) when it builds
any face graph on macOS CPU (google-ai-edge/mediapipe#6356). A one-time subprocess self-test guards
against an unpinned upgrade. The model bundle (``face_landmarker.task``, float16 v1, Apache-2.0) is
downloaded on first use to ``<models_dir>/face_landmarker.task`` and verified by size + SHA-256.
The Tasks library's built-in usage logger (Clearcut uploads to ``play.googleapis.com``) is starved of
its TLS CA bundle so it cannot phone home; set ``STUDIO_MEDIAPIPE_TELEMETRY=1`` to allow it.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from studio.perception.frames import VideoProbe, iter_frames, probe_video, resolve_source
from studio.perception.index import FaceBox, FaceTrack, FaceTrackPoint, HeadTop, Visual, VisualEvent, VisualSample
from studio.timebase import US_PER_S, normalize_fps

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job

__all__ = [
    "MODEL_URL",
    "MODEL_SHA256",
    "MODEL_SIZE",
    "VisualParams",
    "FaceObs",
    "FaceAnalyzer",
    "MediaPipeFaceAnalyzer",
    "ensure_face_landmarker_model",
    "analyze_visual",
    "load_dense",
    "detect_face_boxes",
    "eye_aspect_ratio",
    "head_angles",
    "one_euro",
    "one_euro_zero_phase",
    "eyes_open_signal",
    "detect_blinks",
    "gaze_signals",
    "detect_gaze_events",
    "detect_face_lost",
    "smooth_face_track",
    "blur_score",
    "mean_luma",
    "measure_head_top",
    "head_top_ratio",
    "select_primary",
]

# ---------------------------------------------------------------------------------------------- model
MODEL_FILENAME = "face_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
             "face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_SIZE = 3_758_596
PINNED_MEDIAPIPE = "0.10.35"

# Landmark indices (MediaPipe face mesh topology). "right" = the subject's right = image left.
RIGHT_EYE = (33, 160, 158, 133, 153, 144)  # p1 outer, p2, p3 upper lid, p4 inner, p5, p6 lower lid
LEFT_EYE = (362, 385, 387, 263, 373, 380)  # p1 inner, p2, p3 upper lid, p4 outer, p5, p6 lower lid

_verified: set[tuple[str, float, int]] = set()
_verify_lock = threading.Lock()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_valid_model(path: Path, sha256: str, size: int) -> bool:
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    key = (str(path), st.st_mtime, st.st_size)
    with _verify_lock:
        if key in _verified:
            return True
    if st.st_size != size or _sha256(path) != sha256:
        return False
    with _verify_lock:
        _verified.add(key)
    return True


def _download(url: str, dest: Path, sha256: str, size: int, *, timeout: float = 120.0) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    h = hashlib.sha256()
    n = 0
    try:
        with os.fdopen(fd, "wb") as fh, httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
                h.update(chunk)
                n += len(chunk)
                if n > size * 2:
                    raise ValueError(f"model download from {url} exceeds the expected {size} bytes")
            fh.flush()
            os.fsync(fh.fileno())
        if n != size or h.hexdigest() != sha256:
            raise ValueError(f"model download from {url} failed verification "
                             f"(got {n} bytes, sha256 {h.hexdigest()[:16]}…; expected {size} bytes, {sha256[:16]}…)")
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def ensure_face_landmarker_model(
    *,
    settings: Settings | None = None,
    models_dir: str | os.PathLike[str] | None = None,
    allow_download: bool = True,
) -> Path:
    """Path of a verified ``face_landmarker.task`` (downloaded once to the models dir, size + SHA-256
    checked; a corrupt file is replaced). ``allow_download=False`` raises ``FileNotFoundError`` instead
    of touching the network."""
    if models_dir is None:
        from studio.config import get_settings

        models_dir = (settings or get_settings()).models_dir
    path = Path(models_dir) / MODEL_FILENAME
    if _is_valid_model(path, MODEL_SHA256, MODEL_SIZE):
        return path
    if path.exists():
        with contextlib.suppress(FileNotFoundError):
            path.unlink()  # wrong size/hash: never run an unverified model
    if not allow_download:
        raise FileNotFoundError(f"{path} missing (download disabled)")
    _download(MODEL_URL, path, MODEL_SHA256, MODEL_SIZE)
    if not _is_valid_model(path, MODEL_SHA256, MODEL_SIZE):  # pragma: no cover - _download verified already
        raise RuntimeError(f"model at {path} failed verification after download")
    return path


# ---------------------------------------------------------------------------------------------- mediapipe runtime
_selftest_ok: dict[str, bool] = {}
_selftest_lock = threading.Lock()


def _import_mediapipe() -> Any:
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import mediapipe as mp

    return mp


def _base_options(model_path: str | os.PathLike[str]) -> Any:
    """MediaPipe ``BaseOptions`` for a local model. The Tasks C library ships a usage logger that posts to
    ``play.googleapis.com`` over TLS using the CA bundle passed in these options; unless
    ``STUDIO_MEDIAPIPE_TELEMETRY=1`` we pass a non-existent bundle so those uploads cannot connect (the
    bundle is not used for anything else when the model is a local file)."""
    _import_mediapipe()
    from mediapipe.tasks.python.core.base_options import BaseOptions

    if os.environ.get("STUDIO_MEDIAPIPE_TELEMETRY") == "1":
        return BaseOptions(model_asset_path=str(model_path))

    class _NoTelemetryBaseOptions(BaseOptions):
        def to_ctypes(self) -> Any:
            opts = super().to_ctypes()
            if hasattr(opts, "ca_bundle_path"):
                opts.ca_bundle_path = b"/nonexistent/studio-no-telemetry.pem"
            return opts

    return _NoTelemetryBaseOptions(model_asset_path=str(model_path))


def _selftest_child(model_path: str) -> None:  # pragma: no cover - runs in a subprocess
    mp = _import_mediapipe()
    from mediapipe.tasks.python import vision

    opts = vision.FaceLandmarkerOptions(base_options=_base_options(model_path),
                                        running_mode=vision.RunningMode.VIDEO, num_faces=1,
                                        output_face_blendshapes=True, output_facial_transformation_matrixes=True)
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((64, 64, 3), np.uint8))
        lm.detect_for_video(img, 0)
    print("ok")


def _ensure_runtime(model_path: Path) -> None:
    """Run a tiny landmarker graph once in a subprocess: some MediaPipe builds abort the process (no Python
    exception) when a face graph is built, and that must not take the pipeline down with it."""
    mp = _import_mediapipe()
    version = getattr(mp, "__version__", "unknown")
    with _selftest_lock:
        if _selftest_ok.get(version):
            return
    marker = model_path.parent / f".mediapipe_selftest_{version}_{sys.platform}.ok"
    if marker.exists():
        with _selftest_lock:
            _selftest_ok[version] = True
        return
    from studio.config import STUDIO_ROOT

    code = ("import sys; from studio.perception.visual import _selftest_child; "
            f"_selftest_child({str(model_path)!r})")
    env = {**os.environ, "GLOG_minloglevel": "2",
           "PYTHONPATH": os.pathsep.join(x for x in (str(STUDIO_ROOT), os.environ.get("PYTHONPATH", "")) if x)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=180)
    if r.returncode == 1 and "Error" in (r.stderr or "") and "Traceback" in (r.stderr or ""):
        raise RuntimeError(f"MediaPipe self-test failed with a Python error: {r.stderr.strip().splitlines()[-1]}")
    if r.returncode != 0 or "ok" not in r.stdout:
        raise RuntimeError(
            f"MediaPipe {version} cannot build the face landmarker graph on this platform "
            f"(exit {r.returncode}; see google-ai-edge/mediapipe#6356). Install mediapipe=={PINNED_MEDIAPIPE}. "
            f"stderr tail: {r.stderr.strip().splitlines()[-1:] if r.stderr else ''}")
    with contextlib.suppress(OSError):
        marker.write_text("ok\n", encoding="utf-8")
    with _selftest_lock:
        _selftest_ok[version] = True


# ---------------------------------------------------------------------------------------------- params
@dataclass(frozen=True)
class VisualParams:
    """Tunables (recorded in ``index/visual_meta.json``). Defaults are the calibrated house values."""

    analysis_fps_max: float = 30.5  # analyse at the source rate, decimated by an integer to ≤ this
    decode_short_side_max: int = 1080
    num_faces: int = 2
    min_detection_conf: float = 0.5
    min_presence_conf: float = 0.5
    min_tracking_conf: float = 0.5
    # eyes
    ref_window_s: float = 1.5
    ear_closed_frac: float = 0.2  # EAR at full closure ≈ 0.2 × open EAR
    bs_closed_span: float = 0.4  # eyeBlink rise from open to closed
    ear_weight: float = 0.6
    blink_hi: float = 0.55
    blink_lo: float = 0.35
    blink_single_frame_peak: float = 0.7
    blink_long_us: int = 500_000
    # gaze
    eye_yaw_deg_per_unit: float = 25.0
    eye_pitch_deg_per_unit: float = 18.0
    gaze_median_frames: int = 5
    gaze_off_lo_deg: float = 6.0
    gaze_off_hi_deg: float = 18.0
    look_hi_deg: float = 12.0
    look_lo_deg: float = 9.0
    look_min_us: int = 250_000
    look_merge_us: int = 200_000
    reading_min_us: int = 500_000
    reading_down_deg: float = 8.0
    # face lost
    lost_min_us: int = 100_000
    lost_merge_us: int = 150_000
    # track
    track_interp_max_us: int = 1_000_000
    center_min_cutoff: float = 0.3
    center_beta: float = 6.0
    size_min_cutoff: float = 0.15
    size_beta: float = 1.5
    d_cutoff: float = 1.0
    hampel_half: int = 7
    hampel_k: float = 4.0
    hampel_floor: float = 0.02  # normalized frame units (doubled for width/height)
    # blur
    blur_face_w: int = 256
    blur_var_ref: float = 150.0
    # head top (hair included), for text placed above the head
    head_top_interval_s: float = 0.5
    head_top_max_count: int = 90


# ---------------------------------------------------------------------------------------------- per-frame measures
@dataclass
class FaceObs:
    """One face in one frame (normalized upright-frame coords; angles in degrees)."""

    box: tuple[float, float, float, float]  # x0, y0, x1, y1 of the landmark bounding box (unclipped)
    ear: float | None = None  # mean eye aspect ratio (pixel space)
    blink: float = 0.0  # mean eyeBlink blendshape
    jaw: float = 0.0  # jawOpen blendshape
    eye_h: float = 0.0  # eyeLook horizontal, + = toward frame right
    eye_v: float = 0.0  # eyeLook vertical, + = up
    yaw: float = 0.0  # head yaw vs the camera ray, + = face turned toward frame right
    pitch: float = 0.0  # head pitch vs the camera ray, + = up, - = down
    in_frame: float = 1.0  # fraction of landmarks inside the frame

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.box
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)


class FaceAnalyzer(Protocol):
    """Per-frame face measurement (the MediaPipe implementation, or a fake in tests)."""

    def analyze(self, rgb: np.ndarray, t_ms: int) -> list[FaceObs]: ...

    def close(self) -> None: ...


def eye_aspect_ratio(pts: np.ndarray, idx: Sequence[int]) -> float:
    """EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|) for pixel-space landmark array ``pts`` (N x 2+)."""
    p1, p2, p3, p4, p5, p6 = (pts[i, :2] for i in idx)
    horiz = float(np.linalg.norm(p1 - p4))
    if horiz <= 1e-9:
        return 0.0
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2.0 * horiz))


def head_angles(matrix: np.ndarray) -> tuple[float, float]:
    """Head ``(yaw, pitch)`` in degrees from MediaPipe's facial transformation matrix, relative to the ray
    from the face to the camera (so 0, 0 = facing the lens wherever the face sits in frame).

    Metric space: x right, y up, camera at the origin looking down −z; the face's forward axis is the
    third column of the (scale-normalized) rotation. Yaw + = turned toward frame right; pitch − = down.
    """
    m = np.asarray(matrix, dtype=np.float64)
    fwd = m[:3, 2]
    nf = float(np.linalg.norm(fwd))
    if nf <= 1e-9 or not np.isfinite(nf):
        return 0.0, 0.0
    fwd = fwd / nf
    t = m[:3, 3]
    nt = float(np.linalg.norm(t))
    ray = -t / nt if nt > 1e-9 else np.array([0.0, 0.0, 1.0])

    def yp(v: np.ndarray) -> tuple[float, float]:
        return (math.degrees(math.atan2(v[0], v[2])), math.degrees(math.atan2(v[1], math.hypot(v[0], v[2]))))

    fy, fp = yp(fwd)
    ry, rp = yp(ray)
    dy = (fy - ry + 180.0) % 360.0 - 180.0
    return dy, fp - rp


def obs_from_landmarks(landmarks: np.ndarray, blendshapes: dict[str, float], matrix: np.ndarray | None,
                       width: int, height: int) -> FaceObs:
    """Build a :class:`FaceObs` from normalized landmarks (N x 3), blendshape scores by name and the
    transformation matrix, for a ``width``×``height`` analysis frame."""
    lm = np.asarray(landmarks, dtype=np.float64)
    x0, y0 = float(lm[:, 0].min()), float(lm[:, 1].min())
    x1, y1 = float(lm[:, 0].max()), float(lm[:, 1].max())
    inside = (lm[:, 0] >= 0) & (lm[:, 0] <= 1) & (lm[:, 1] >= 0) & (lm[:, 1] <= 1)
    ear = None
    if lm.shape[0] > max(max(RIGHT_EYE), max(LEFT_EYE)):
        px = lm[:, :2] * np.array([width, height], dtype=np.float64)
        ear = 0.5 * (eye_aspect_ratio(px, RIGHT_EYE) + eye_aspect_ratio(px, LEFT_EYE))
    b = blendshapes.get
    eye_h = 0.5 * ((b("eyeLookOutLeft", 0.0) - b("eyeLookInLeft", 0.0))
                   + (b("eyeLookInRight", 0.0) - b("eyeLookOutRight", 0.0)))
    eye_v = 0.5 * ((b("eyeLookUpLeft", 0.0) + b("eyeLookUpRight", 0.0))
                   - (b("eyeLookDownLeft", 0.0) + b("eyeLookDownRight", 0.0)))
    yaw, pitch = head_angles(matrix) if matrix is not None else (0.0, 0.0)
    return FaceObs(
        box=(x0, y0, x1, y1), ear=ear,
        blink=0.5 * (b("eyeBlinkLeft", 0.0) + b("eyeBlinkRight", 0.0)),
        jaw=float(b("jawOpen", 0.0)), eye_h=float(eye_h), eye_v=float(eye_v), yaw=yaw, pitch=pitch,
        in_frame=float(inside.mean()) if lm.size else 0.0,
    )


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def select_primary(faces: Sequence[FaceObs], prev_box: tuple[float, float, float, float] | None) -> FaceObs | None:
    """The speaker: largest face, with continuity (IoU with the previous primary) weighted in."""
    if not faces:
        return None
    if len(faces) == 1:
        return faces[0]
    return max(faces, key=lambda f: f.area * (1.0 + 2.0 * (_iou(f.box, prev_box) if prev_box else 0.0)))


class MediaPipeFaceAnalyzer:
    """MediaPipe Face Landmarker (VIDEO or IMAGE mode) → :class:`FaceObs` list per frame."""

    def __init__(self, model_path: str | os.PathLike[str], *, params: VisualParams | None = None,
                 video: bool = True, check_runtime: bool = True):
        p = params or VisualParams()
        if check_runtime:
            _ensure_runtime(Path(model_path))
        self._mp = _import_mediapipe()
        from mediapipe.tasks.python import vision

        self._video = video
        opts = vision.FaceLandmarkerOptions(
            base_options=_base_options(model_path),
            running_mode=vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE,
            num_faces=p.num_faces,
            min_face_detection_confidence=p.min_detection_conf,
            min_face_presence_confidence=p.min_presence_conf,
            min_tracking_confidence=p.min_tracking_conf,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._lm = vision.FaceLandmarker.create_from_options(opts)
        self._last_ms = -1

    def analyze(self, rgb: np.ndarray, t_ms: int) -> list[FaceObs]:
        mp = self._mp
        h, w = rgb.shape[:2]
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        if self._video:
            t_ms = max(int(t_ms), self._last_ms + 1)  # VIDEO mode needs strictly increasing timestamps
            self._last_ms = t_ms
            res = self._lm.detect_for_video(img, t_ms)
        else:
            res = self._lm.detect(img)
        out: list[FaceObs] = []
        for i, lms in enumerate(res.face_landmarks):
            arr = np.array([(q.x, q.y, q.z) for q in lms], dtype=np.float64)
            bs = {c.category_name: float(c.score) for c in res.face_blendshapes[i]} if res.face_blendshapes else {}
            mat = res.facial_transformation_matrixes[i] if res.facial_transformation_matrixes else None
            out.append(obs_from_landmarks(arr, bs, mat, w, h))
        return out

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._lm.close()


_image_analyzer: MediaPipeFaceAnalyzer | None = None
_image_lock = threading.Lock()


def detect_face_boxes(frames: Sequence[np.ndarray], *, allow_download: bool = False,
                      settings: Settings | None = None) -> list[tuple[float, float, float, float] | None]:
    """Largest face per still frame as normalized ``(cx, cy, w, h)`` (IMAGE mode; for seam checks)."""
    global _image_analyzer
    model = ensure_face_landmarker_model(settings=settings, allow_download=allow_download)
    out: list[tuple[float, float, float, float] | None] = []
    with _image_lock:
        if _image_analyzer is None:
            _image_analyzer = MediaPipeFaceAnalyzer(model, video=False)
        for fr in frames:
            faces = _image_analyzer.analyze(fr, 0)
            f = select_primary(faces, None)
            if f is None:
                out.append(None)
                continue
            x0, y0, x1, y1 = (min(1.0, max(0.0, v)) for v in f.box)
            out.append(((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0))
    return out


def mean_luma(rgb: np.ndarray) -> float:
    """Mean BT.709 Y' (gamma-encoded) of an RGB frame, 0..1 (every 4th pixel; exact enough)."""
    sub = rgb[::4, ::4].astype(np.float32)
    y = sub[..., 0] * 0.2126 + sub[..., 1] * 0.7152 + sub[..., 2] * 0.0722
    return float(y.mean() / 255.0)


def blur_score(rgb: np.ndarray, box: tuple[float, float, float, float] | None, *, face_w: int = 256,
               var_ref: float = 150.0) -> tuple[float, float]:
    """``(blurriness 0..1, laplacian variance)`` on the face region (10 % margin) rescaled to ``face_w``
    wide, or the whole frame when ``box`` is None. Blurriness = ``var_ref / (var_ref + var)``."""
    import cv2

    h, w = rgb.shape[:2]
    if box is not None:
        x0, y0, x1, y1 = box
        mx, my = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
        a, b = int(max(0, (x0 - mx) * w)), int(min(w, math.ceil((x1 + mx) * w)))
        c, d = int(max(0, (y0 - my) * h)), int(min(h, math.ceil((y1 + my) * h)))
        crop = rgb[c:d, a:b] if (b - a) >= 8 and (d - c) >= 8 else rgb
    else:
        crop = rgb
    gray = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2GRAY)
    scale = face_w / gray.shape[1]
    nh = max(8, round(gray.shape[0] * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    g = cv2.resize(gray, (face_w, nh), interpolation=interp).astype(np.float32)
    var = float(cv2.Laplacian(g, cv2.CV_32F, ksize=3).var())
    return var_ref / (var_ref + var), var


def measure_head_top(rgb: np.ndarray, box: tuple[float, float, float, float], *, work_w: int = 144,
                     iters: int = 3) -> tuple[float, bool] | None:
    """Top of the head, hair included, above a landmark ``box`` (normalized x0, y0, x1, y1).

    The landmark box stops at the upper forehead, and hair can add half a face height or more. The head
    is segmented with GrabCut, a colour-model graph cut that needs no learned weights. It works on a crop
    from the frame top to the chin, about 144 px wide (≈0.1 s). Seeds: the face below the brows and a
    thin band at the hairline are head; strips far to the side above the face and rows more than 1.25 box
    heights above it are background; a column over the face is probably head. The head top is the highest
    row where the component holding the face covers at least 15 % of the central columns. Returns
    ``(y, touches_top)`` in normalized frame coordinates, or None when the segmentation fails.
    """
    import cv2

    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = box
    fw, fh = (x1 - x0) * W, (y1 - y0) * H
    if fw < 8 or fh < 8 or y1 <= 0:
        return None
    ca, cb = int(max(0, x0 * W - 0.7 * fw)), int(min(W, x1 * W + 0.7 * fw))
    cd = int(min(H, round(y1 * H)))
    if cd < 8 or cb - ca < 8:
        return None
    crop = rgb[0:cd, ca:cb]
    s = min(1.0, work_w / crop.shape[1])
    cw, ch = max(8, round(crop.shape[1] * s)), max(8, round(crop.shape[0] * s))
    img = cv2.cvtColor(cv2.resize(np.ascontiguousarray(crop), (cw, ch), interpolation=cv2.INTER_AREA),
                       cv2.COLOR_RGB2BGR)
    bx0, bx1 = (x0 * W - ca) * s, (x1 * W - ca) * s
    by0, by1 = y0 * H * s, y1 * H * s
    bw, bh = bx1 - bx0, by1 - by0
    m = np.full((ch, cw), cv2.GC_PR_BGD, np.uint8)

    def rect(xa: float, ya: float, xb: float, yb: float, v: int) -> None:
        ia, ib = int(max(0, round(xa))), int(min(cw, round(xb)))
        ja, jb = int(max(0, round(ya))), int(min(ch, round(yb)))
        if ib > ia and jb > ja:
            m[ja:jb, ia:ib] = v

    rect(bx0 - 0.2 * bw, by0 - 1.1 * bh, bx1 + 0.2 * bw, by1, cv2.GC_PR_FGD)
    rect(0, 0, bx0 - 0.55 * bw, by0 - 0.05 * bh, cv2.GC_BGD)
    rect(bx1 + 0.55 * bw, 0, cw, by0 - 0.05 * bh, cv2.GC_BGD)
    rect(0, 0, cw, by0 - 1.25 * bh, cv2.GC_BGD)
    rect(bx0 + 0.2 * bw, by0 + 0.12 * bh, bx1 - 0.2 * bw, by1 - 0.08 * bh, cv2.GC_FGD)
    rect(bx0 + 0.35 * bw, by0 - 0.04 * bh, bx1 - 0.35 * bw, by0 + 0.12 * bh, cv2.GC_FGD)
    if not (m == cv2.GC_FGD).any():
        return None
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(img, m, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    fg = ((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)).astype(np.uint8)
    _n, lab = cv2.connectedComponents(fg, connectivity=4)
    fx, fy = int(min(cw - 1, max(0, (bx0 + bx1) / 2))), int(min(ch - 1, max(0, by0 + 0.5 * bh)))
    face_lab = lab[fy, fx]
    if face_lab == 0:
        return None
    head = lab == face_lab
    a, b = int(max(0, bx0 + 0.15 * bw)), int(min(cw, bx1 - 0.15 * bw))
    if b <= a:
        return None
    rows = np.nonzero(head[:, a:b].mean(axis=1) >= 0.15)[0]
    if rows.size == 0:
        return None
    top = int(rows.min())
    return (top / s) / H, top <= 1


def head_top_ratio(tops: Sequence[HeadTop], *, q: float = 80.0, margin: float = 0.02,
                   min_count: int = 5) -> float | None:
    """How far the head top sits above the landmark box top, in box heights, for the whole take.

    Single measurements are noisy: motion blur loses hair, and a dark background can merge with it. So
    the take gets one robust ratio, the ``q``-th percentile of the plausible measurements (0.15–1.3) plus
    a small margin, which leans towards more hair. Placement applies it to every frame's landmark box.
    When the head often runs off the top of the frame, those measurements are lower bounds and the ratio
    is at least their ``q``-th percentile. Returns None with fewer than ``min_count`` usable measurements
    (callers then use a prior)."""
    ok = [t.k for t in tops if not t.touches_top and 0.15 <= t.k <= 1.3]
    touching = [t.k for t in tops if t.touches_top and t.k > 0]
    r: float | None = None
    if len(ok) >= min_count:
        r = float(np.percentile(ok, q)) + margin
    if touching and len(touching) >= max(2, 0.3 * len(tops)):
        lb = float(np.percentile(touching, q))
        r = lb if r is None else max(r, lb)
    return None if r is None else round(min(max(r, 0.2), 1.5), 4)


# ---------------------------------------------------------------------------------------------- signal helpers
def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open index runs ``[s, e)`` where ``mask`` is True."""
    m = np.asarray(mask, dtype=bool)
    if m.size == 0:
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _hysteresis_runs(x: np.ndarray, hi: float, lo: float, valid: np.ndarray | None = None) -> list[tuple[int, int]]:
    """Runs where ``x >= lo`` that contain at least one ``x >= hi`` (NaN / invalid frames break runs)."""
    x = np.asarray(x, dtype=np.float64)
    ok = np.isfinite(x) & (valid if valid is not None else True)
    above = ok & (np.nan_to_num(x, nan=-np.inf) >= lo)
    return [(s, e) for s, e in _runs(above) if np.nanmax(x[s:e]) >= hi]


def _merge_runs(runs: list[tuple[int, int]], t_us: np.ndarray, frame_us: float, gap_us: float) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in runs:
        if out and (t_us[s] - (t_us[out[-1][1] - 1] + frame_us)) <= gap_us:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _median_filter(x: np.ndarray, k: int) -> np.ndarray:
    """NaN-aware centred median over ``k`` frames (odd); NaNs stay NaN."""
    x = np.asarray(x, dtype=np.float64)
    if k <= 1 or x.size == 0:
        return x.copy()
    import warnings

    h = k // 2
    pad = np.pad(x, h, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(win, axis=1)
    out[~np.isfinite(x)] = np.nan
    return out


def _rolling_percentile(x: np.ndarray, half: int, q: float) -> np.ndarray:
    """NaN-aware centred rolling percentile over ``2*half+1`` frames (all-NaN windows → NaN)."""
    import warnings

    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    pad = np.pad(x, half, mode="constant", constant_values=np.nan)
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * half + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanpercentile(win, q, axis=1)


def _interp_nans(x: np.ndarray, t: np.ndarray, *, max_gap_us: float | None = None) -> np.ndarray:
    """Linear interpolation over NaNs (edges held); gaps longer than ``max_gap_us`` stay NaN."""
    x = np.asarray(x, dtype=np.float64).copy()
    ok = np.isfinite(x)
    if ok.all() or not ok.any():
        return x
    filled = np.interp(t, t[ok], x[ok])
    if max_gap_us is not None:
        for s, e in _runs(~ok):
            left = t[s - 1] if s > 0 else None
            right = t[e] if e < len(t) else None
            span = (right - left) if (left is not None and right is not None) else math.inf
            if span > max_gap_us:
                filled[s:e] = np.nan
    return filled


def _smoothstep(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    u = np.clip((np.asarray(x, dtype=np.float64) - lo) / (hi - lo), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


# ---------------------------------------------------------------------------------------------- one euro
def one_euro(x: np.ndarray, t_s: np.ndarray, *, min_cutoff: float, beta: float, d_cutoff: float = 1.0) -> np.ndarray:
    """Causal One-Euro filter (Casiez, Roussel & Vogel, CHI 2012) over samples ``x`` at times ``t_s`` (s)."""
    x = np.asarray(x, dtype=np.float64)
    t_s = np.asarray(t_s, dtype=np.float64)
    out = np.empty_like(x)
    if x.size == 0:
        return out

    def alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    xh = float(x[0])
    dxh = 0.0
    out[0] = xh
    for i in range(1, x.size):
        dt = float(t_s[i] - t_s[i - 1])
        if dt <= 0:
            out[i] = xh
            continue
        dx = (float(x[i]) - xh) / dt
        dxh = dxh + alpha(d_cutoff, dt) * (dx - dxh)
        cutoff = min_cutoff + beta * abs(dxh)
        xh = xh + alpha(cutoff, dt) * (float(x[i]) - xh)
        out[i] = xh
    return out


def one_euro_zero_phase(x: np.ndarray, t_s: np.ndarray, *, min_cutoff: float, beta: float,
                        d_cutoff: float = 1.0) -> np.ndarray:
    """Offline, lag-free One-Euro: mean of the forward pass and the time-reversed pass."""
    x = np.asarray(x, dtype=np.float64)
    t_s = np.asarray(t_s, dtype=np.float64)
    fwd = one_euro(x, t_s, min_cutoff=min_cutoff, beta=beta, d_cutoff=d_cutoff)
    bwd = one_euro(x[::-1], (t_s[-1] - t_s)[::-1] if t_s.size else t_s, min_cutoff=min_cutoff, beta=beta,
                   d_cutoff=d_cutoff)[::-1]
    return 0.5 * (fwd + bwd)


# ---------------------------------------------------------------------------------------------- eyes
def eyes_open_signal(ear: np.ndarray, blink_bs: np.ndarray, valid: np.ndarray, fps: float,
                     params: VisualParams | None = None) -> np.ndarray:
    """Fused eyes-open 0..1 (1 = this creator's normal open eyes) per frame; NaN where no face."""
    p = params or VisualParams()
    valid = np.asarray(valid, dtype=bool)
    ear = np.where(valid, np.asarray(ear, dtype=np.float64), np.nan)
    bs = np.where(valid, np.asarray(blink_bs, dtype=np.float64), np.nan)
    out = np.full(ear.shape, np.nan)
    if not valid.any():
        return out
    half = max(1, round(p.ref_window_s * fps))
    have_ear = np.isfinite(ear)
    if have_ear.any():
        g80 = float(np.nanpercentile(ear, 80))
        ref = _rolling_percentile(ear, half, 80)
        ref = np.where(np.isfinite(ref), ref, g80)
        ref = np.maximum(ref, 0.75 * g80)
        closed = p.ear_closed_frac * ref
        eo_ear = np.clip((ear - closed) / np.maximum(ref - closed, 1e-6), 0.0, 1.0)
    else:
        eo_ear = np.full(ear.shape, np.nan)
    bref = _rolling_percentile(bs, half, 25)
    bref = np.where(np.isfinite(bref), bref, float(np.nanpercentile(bs, 25)))
    eo_bs = np.clip(1.0 - (bs - bref) / p.bs_closed_span, 0.0, 1.0)
    fused = np.where(np.isfinite(eo_ear), p.ear_weight * eo_ear + (1.0 - p.ear_weight) * eo_bs, eo_bs)
    out[valid] = fused[valid]
    return out


def detect_blinks(t_us: np.ndarray, eyes_open: np.ndarray, frame_us: float,
                  params: VisualParams | None = None) -> list[VisualEvent]:
    """Blink events from the fused eyes-open signal (hysteresis on closure = 1 - eyes_open)."""
    p = params or VisualParams()
    closure = 1.0 - np.asarray(eyes_open, dtype=np.float64)
    evs: list[VisualEvent] = []
    for s, e in _hysteresis_runs(closure, p.blink_hi, p.blink_lo):
        peak = float(np.nanmax(closure[s:e]))
        if e - s == 1 and peak < p.blink_single_frame_peak:
            continue  # single-frame flicker
        start = int(t_us[s])
        end = round(t_us[e - 1] + frame_us)
        dur = end - start
        conf = float(np.clip((peak - p.blink_lo) / (1.0 - p.blink_lo), 0.2, 1.0))
        note = (f"eyes closed {dur / US_PER_S:.2f}s" if dur > p.blink_long_us
                else f"blink {dur / 1000:.0f}ms, closure {peak:.2f}")
        evs.append(VisualEvent(kind="blink", start_us=start, end_us=end, confidence=round(conf, 3), note=note))
    return evs


# ---------------------------------------------------------------------------------------------- gaze
def _robust_mode(a: np.ndarray, b: np.ndarray, radius: float = 10.0, iters: int = 5) -> tuple[float, float]:
    ok = np.isfinite(a) & np.isfinite(b)
    if not ok.any():
        return 0.0, 0.0
    a, b = a[ok], b[ok]
    ca, cb = float(np.median(a)), float(np.median(b))
    for _ in range(iters):
        near = np.hypot(a - ca, b - cb) <= radius
        if near.sum() < max(5, 0.1 * a.size):
            break
        na, nb = float(np.median(a[near])), float(np.median(b[near]))
        if abs(na - ca) < 1e-3 and abs(nb - cb) < 1e-3:
            break
        ca, cb = na, nb
    return ca, cb


@dataclass
class GazeSignals:
    gaze_yaw: np.ndarray  # degrees, head + eyes, relative to the creator's lens direction (+ = frame right)
    gaze_pitch: np.ndarray  # degrees (+ = up)
    dev: np.ndarray  # angular deviation from the lens direction
    gaze_off: np.ndarray  # 0..1
    baseline: tuple[float, float]  # raw (yaw, pitch) of the lens direction


def gaze_signals(t_us: np.ndarray, yaw: np.ndarray, pitch: np.ndarray, eye_h: np.ndarray, eye_v: np.ndarray,
                 valid: np.ndarray, eyes_open: np.ndarray | None, params: VisualParams | None = None) -> GazeSignals:
    """Combined head+eye gaze relative to the creator's own at-lens direction (see module docstring)."""
    p = params or VisualParams()
    valid = np.asarray(valid, dtype=bool)
    gy = np.where(valid, np.asarray(yaw) + p.eye_yaw_deg_per_unit * np.asarray(eye_h), np.nan)
    gp = np.where(valid, np.asarray(pitch) + p.eye_pitch_deg_per_unit * np.asarray(eye_v), np.nan)
    if eyes_open is not None:
        lid = np.asarray(eyes_open, dtype=np.float64)
        shut = np.isfinite(lid) & ((1.0 - lid) > p.blink_lo)
        # closing lids fake a downward look: bridge blink frames from their neighbours
        gy_b, gp_b = gy.copy(), gp.copy()
        gy_b[shut], gp_b[shut] = np.nan, np.nan
        gy_i = _interp_nans(gy_b, t_us.astype(np.float64), max_gap_us=600_000)
        gp_i = _interp_nans(gp_b, t_us.astype(np.float64), max_gap_us=600_000)
        gy = np.where(valid, np.where(np.isfinite(gy_i), gy_i, gy), np.nan)
        gp = np.where(valid, np.where(np.isfinite(gp_i), gp_i, gp), np.nan)
    k = p.gaze_median_frames | 1
    gy = _median_filter(gy, k)
    gp = _median_filter(gp, k)
    by, bp = _robust_mode(gy, gp)
    dy, dp = gy - by, gp - bp
    dev = np.hypot(dy, dp)
    off = _smoothstep(dev, p.gaze_off_lo_deg, p.gaze_off_hi_deg)
    off[~np.isfinite(dev)] = np.nan
    return GazeSignals(gaze_yaw=dy, gaze_pitch=dp, dev=dev, gaze_off=off, baseline=(by, bp))


def _direction(dy: float, dp: float) -> str:
    parts = []
    if abs(dp) >= 0.5 * abs(dy) and abs(dp) >= 3:
        parts.append("down" if dp < 0 else "up")
    if abs(dy) >= 0.5 * abs(dp) and abs(dy) >= 3:
        parts.append("frame-right" if dy > 0 else "frame-left")
    return "-".join(parts) or "off-lens"


def detect_gaze_events(t_us: np.ndarray, g: GazeSignals, frame_us: float,
                       params: VisualParams | None = None) -> list[VisualEvent]:
    """``look_away`` / ``reading`` events from the gaze deviation (hysteresis, min duration, merge)."""
    p = params or VisualParams()
    runs = _hysteresis_runs(g.dev, p.look_hi_deg, p.look_lo_deg)
    runs = _merge_runs(runs, t_us, frame_us, p.look_merge_us)
    evs: list[VisualEvent] = []
    for s, e in runs:
        start = int(t_us[s])
        end = round(t_us[e - 1] + frame_us)
        dur = end - start
        if dur < p.look_min_us:
            continue
        with np.errstate(all="ignore"):
            my = float(np.nanmean(g.gaze_yaw[s:e]))
            mp_ = float(np.nanmean(g.gaze_pitch[s:e]))
            peak = float(np.nanmax(g.dev[s:e]))
        downward = mp_ <= -p.reading_down_deg and abs(mp_) >= abs(my)
        kind = "reading" if (downward and dur >= p.reading_min_us) else "look_away"
        conf = float(np.clip((peak - p.look_lo_deg) / 12.0, 0.3, 1.0) * min(1.0, 0.5 + dur / 1_000_000))
        note = f"gaze {_direction(my, mp_)} {peak:.0f}° for {dur / US_PER_S:.2f}s"
        evs.append(VisualEvent(kind=kind, start_us=start, end_us=end, confidence=round(conf, 3), note=note))
    return evs


def detect_face_lost(t_us: np.ndarray, valid: np.ndarray, frame_us: float,
                     params: VisualParams | None = None) -> list[VisualEvent]:
    """``face_lost`` runs (only meaningful when a face exists somewhere in the take)."""
    p = params or VisualParams()
    valid = np.asarray(valid, dtype=bool)
    if not valid.any():
        return []
    runs = _merge_runs(_runs(~valid), t_us, frame_us, p.lost_merge_us)
    evs = []
    for s, e in runs:
        start = int(t_us[s])
        end = round(t_us[e - 1] + frame_us)
        if end - start < p.lost_min_us:
            continue
        where = "at the start" if s == 0 else ("at the end" if e == len(valid) else "mid-take")
        evs.append(VisualEvent(kind="face_lost", start_us=start, end_us=end, confidence=1.0,
                               note=f"no face detected {where} for {(end - start) / US_PER_S:.2f}s"))
    return evs


# ---------------------------------------------------------------------------------------------- face track
def _hampel(x: np.ndarray, half: int, k: float, floor: float) -> np.ndarray:
    """Boolean mask of gross outliers: ``|x - rolling median| > max(k * 1.4826 * rolling MAD, floor)``
    (NaN-aware). The absolute ``floor`` keeps landmark jitter (the smoother's job) from being flagged."""
    import warnings

    x = np.asarray(x, dtype=np.float64)
    if x.size < 3:
        return np.zeros(x.shape, bool)
    pad = np.pad(x, half, mode="constant", constant_values=np.nan)
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * half + 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(win, axis=1)
        mad = np.nanmedian(np.abs(win - med[:, None]), axis=1)
    thr = np.maximum(k * 1.4826 * np.nan_to_num(mad, nan=0.0), floor)
    return np.isfinite(x) & np.isfinite(med) & (np.abs(x - med) > thr)


def smooth_face_track(t_us: np.ndarray, boxes: np.ndarray, valid: np.ndarray, out_t_us: Sequence[int],
                      params: VisualParams | None = None) -> FaceTrack | None:
    """Zero-phase One-Euro-smoothed face track sampled at ``out_t_us``.

    ``boxes`` is ``(n, 4)`` visible face boxes ``x0, y0, x1, y1`` (normalized, clipped); rows where
    ``valid`` is False are ignored. Short gaps are interpolated (conf 0.5), long gaps and the edges are
    held (conf 0.0), measured frames have conf 1.0. None when no frame has a face.
    """
    p = params or VisualParams()
    valid = np.asarray(valid, dtype=bool).copy()
    if not valid.any():
        return None
    t = np.asarray(t_us, dtype=np.float64)
    bx = np.asarray(boxes, dtype=np.float64)
    ch = {
        "cx": (bx[:, 0] + bx[:, 2]) / 2, "cy": (bx[:, 1] + bx[:, 3]) / 2,
        "w": bx[:, 2] - bx[:, 0], "h": bx[:, 3] - bx[:, 1],
    }
    for k in ch:
        ch[k] = np.where(valid, ch[k], np.nan)
    out_mask = np.zeros(valid.shape, bool)
    for k in ch:
        floor = p.hampel_floor if k in ("cx", "cy") else p.hampel_floor * 2
        out_mask |= _hampel(ch[k], p.hampel_half, p.hampel_k, floor)
    # a lone mis-fit in a well-tracked stretch is an outlier; if many frames disagree it is real motion
    if out_mask.sum() <= max(1, int(0.1 * valid.sum())):
        valid &= ~out_mask
        for k in ch:
            ch[k] = np.where(valid, ch[k], np.nan)
    conf = np.where(valid, 1.0, 0.0)
    short = _interp_nans(ch["cx"], t, max_gap_us=p.track_interp_max_us)
    conf = np.where(~valid & np.isfinite(short), 0.5, conf)
    t_s = t / US_PER_S
    sm: dict[str, np.ndarray] = {}
    for k, x in ch.items():
        filled = _interp_nans(x, t, max_gap_us=p.track_interp_max_us)
        # hold across long gaps / edges (nearest measured value, never a guessed drift)
        if not np.isfinite(filled).all():
            idx = np.arange(filled.size)
            okk = np.isfinite(filled)
            prev_i = np.maximum.accumulate(np.where(okk, idx, -1))
            nxt = np.where(okk, idx, filled.size)
            next_i = np.minimum.accumulate(nxt[::-1])[::-1]
            use = np.where(prev_i >= 0, prev_i, next_i)
            filled = filled[np.clip(use, 0, filled.size - 1)]
        if k in ("cx", "cy"):
            sm[k] = one_euro_zero_phase(filled, t_s, min_cutoff=p.center_min_cutoff, beta=p.center_beta,
                                        d_cutoff=p.d_cutoff)
        else:
            sm[k] = one_euro_zero_phase(filled, t_s, min_cutoff=p.size_min_cutoff, beta=p.size_beta,
                                        d_cutoff=p.d_cutoff)
    out_t = np.asarray(list(out_t_us), dtype=np.float64)
    pts: list[FaceTrackPoint] = []
    near = np.clip(np.searchsorted(t, out_t), 0, t.size - 1)
    prev = np.clip(near - 1, 0, t.size - 1)
    near = np.where(np.abs(t[prev] - out_t) < np.abs(t[near] - out_t), prev, near)
    for j, tt in enumerate(out_t):
        cx = float(np.interp(tt, t, sm["cx"]))
        cy = float(np.interp(tt, t, sm["cy"]))
        w = max(0.0, float(np.interp(tt, t, sm["w"])))
        h = max(0.0, float(np.interp(tt, t, sm["h"])))
        pts.append(FaceTrackPoint(t_us=int(tt), cx=round(min(1.0, max(0.0, cx)), 5),
                                  cy=round(min(1.0, max(0.0, cy)), 5), w=round(min(1.0, w), 5),
                                  h=round(min(1.0, h), 5), conf=float(conf[near[j]])))
    return FaceTrack(points=pts, method="one_euro_zero_phase", params={
        "center_min_cutoff_hz": p.center_min_cutoff, "center_beta": p.center_beta,
        "size_min_cutoff_hz": p.size_min_cutoff, "size_beta": p.size_beta, "d_cutoff_hz": p.d_cutoff,
        "interp_max_s": p.track_interp_max_us / US_PER_S, "box": "landmark bbox (visible part)",
    })


# ---------------------------------------------------------------------------------------------- orchestration
@dataclass
class _Dense:
    t_us: np.ndarray
    valid: np.ndarray
    boxes: np.ndarray  # clipped x0 y0 x1 y1
    ear: np.ndarray
    blink: np.ndarray
    jaw: np.ndarray
    eye_h: np.ndarray
    eye_v: np.ndarray
    yaw: np.ndarray
    pitch: np.ndarray
    in_frame: np.ndarray
    n_faces: np.ndarray
    blur: np.ndarray
    blur_var: np.ndarray
    luma: np.ndarray
    head_tops: list[HeadTop] | None = None


def _analysis_rate(fps: float, cap: float) -> tuple[Any, int]:
    from fractions import Fraction

    f = normalize_fps(fps)
    step = max(1, math.ceil(float(f) / cap - 1e-9))
    return Fraction(f) / step, step


def _decode_dims(vp: VideoProbe, short_max: int) -> tuple[int, int]:
    short = min(vp.width, vp.height)
    if short <= short_max:
        return vp.width, vp.height
    s = short_max / short
    return max(2, round(vp.width * s / 2) * 2), max(2, round(vp.height * s / 2) * 2)


def _measure(path: Path, vp: VideoProbe, analyzer: FaceAnalyzer, p: VisualParams,
             duration_us: int) -> tuple[_Dense, Any]:
    rate, _step = _analysis_rate(float(vp.fps), p.analysis_fps_max)
    w, h = _decode_dims(vp, p.decode_short_side_max)
    rows: list[tuple] = []
    prev_box: tuple[float, float, float, float] | None = None
    # head tops: sparse (every head_top_interval_s, at most head_top_max_count per take)
    ht_step = max(p.head_top_interval_s * US_PER_S, duration_us / max(1, p.head_top_max_count))
    ht_next = 0.0
    head_tops: list[HeadTop] = []
    for _k, t_us, rgb in iter_frames(path, fps=rate, width=w, height=h, end_us=duration_us):
        faces = analyzer.analyze(rgb, t_us // 1000)
        f = select_primary(faces, prev_box)
        lum = mean_luma(rgb)
        if f is None:
            bl, bv = blur_score(rgb, None, face_w=p.blur_face_w, var_ref=p.blur_var_ref)
            rows.append((t_us, False, (np.nan,) * 4, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, 0.0,
                         len(faces), bl, bv, lum))
            continue
        prev_box = f.box
        cb = tuple(min(1.0, max(0.0, v)) for v in f.box)
        bl, bv = blur_score(rgb, cb, face_w=p.blur_face_w, var_ref=p.blur_var_ref)  # type: ignore[arg-type]
        if t_us >= ht_next and cb[3] - cb[1] > 0.02:
            ht_next = t_us + ht_step
            with contextlib.suppress(Exception):  # a failed segmentation only loses one measurement
                m = measure_head_top(rgb, cb)  # type: ignore[arg-type]
                if m is not None:
                    k = (cb[1] - m[0]) / (cb[3] - cb[1])
                    head_tops.append(HeadTop(t_us=int(t_us), y=round(m[0], 5), k=round(float(k), 4),
                                             touches_top=m[1]))
        rows.append((t_us, True, cb, np.nan if f.ear is None else f.ear, f.blink, f.jaw, f.eye_h, f.eye_v, f.yaw,
                     f.pitch, f.in_frame, len(faces), bl, bv, lum))
    n = len(rows)

    def col(i: int, dtype: Any = np.float64) -> np.ndarray:
        return np.array([r[i] for r in rows], dtype=dtype) if n else np.zeros(0, dtype)

    dense = _Dense(
        t_us=col(0, np.int64), valid=col(1, bool),
        boxes=np.array([r[2] for r in rows], dtype=np.float64).reshape(n, 4),
        ear=col(3), blink=col(4), jaw=col(5), eye_h=col(6), eye_v=col(7), yaw=col(8), pitch=col(9),
        in_frame=col(10), n_faces=col(11, np.int16), blur=col(12), blur_var=col(13), luma=col(14),
        head_tops=head_tops,
    )
    return dense, rate


def _sample_indices(t_us: np.ndarray, sample_fps: float, duration_us: int, frame_us: float) -> list[int]:
    """Analysis frames nearest to the sample grid ``j / sample_fps`` (targets past the last frame's display
    interval are dropped; no duplicates)."""
    if t_us.size == 0:
        return []
    n = max(1, math.floor(duration_us * sample_fps / US_PER_S + 1e-6) + 1)
    targets = np.arange(n, dtype=np.float64) * (US_PER_S / sample_fps)
    targets = targets[targets < float(t_us[-1]) + frame_us / 2]
    idx = np.clip(np.searchsorted(t_us, targets), 0, t_us.size - 1)
    prev = np.clip(idx - 1, 0, t_us.size - 1)
    idx = np.where(np.abs(t_us[prev] - targets) <= np.abs(t_us[idx] - targets), prev, idx)
    out: list[int] = []
    for i in idx.tolist():
        if not out or i != out[-1]:
            out.append(i)
    return out


def _r(x: float, nd: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def analyze_visual(
    job: Job,
    *,
    sample_fps: float = 10.0,
    source: str | os.PathLike[str] | None = None,
    settings: Settings | None = None,
    analyzer: FaceAnalyzer | Callable[[], FaceAnalyzer] | None = None,
    params: VisualParams | None = None,
    save: bool = True,
) -> Visual:
    """Sample the mezzanine and return samples, events and the smoothed face track.

    ``source`` overrides the decoded video (default: ``media/mezz.mov``, falling back to the original when
    ingest has not produced a mezzanine). ``analyzer`` injects a face analyzer (or a factory); default is
    MediaPipe with the verified model. With ``save`` the dense per-frame signals and the calibration go
    to ``index/visual_dense.npz`` and ``index/visual_meta.json``.
    """
    p = params or VisualParams()
    t0 = time.monotonic()
    path = resolve_source(job, source if source is not None else "mezz")
    vp = probe_video(path)
    duration_us = vp.duration_us
    with contextlib.suppress(Exception):
        mi = job.load_media_info()
        if mi.duration_us > 0:
            duration_us = min(duration_us, mi.duration_us) if duration_us > 0 else mi.duration_us
    own = False
    model_path = None
    if analyzer is None:
        model_path = ensure_face_landmarker_model(settings=settings)
        fa: FaceAnalyzer = MediaPipeFaceAnalyzer(model_path, params=p)
        own = True
    elif callable(analyzer) and not hasattr(analyzer, "analyze"):
        fa = analyzer()  # type: ignore[call-arg]
        own = True
    else:
        fa = analyzer  # type: ignore[assignment]
    try:
        d, rate = _measure(path, vp, fa, p, duration_us)
    finally:
        if own:
            fa.close()
    frame_us = US_PER_S / float(rate)
    fps = float(rate)

    # ---- derived dense signals
    eyes = eyes_open_signal(d.ear, d.blink, d.valid, fps, p)
    gaze = gaze_signals(d.t_us, d.yaw, d.pitch, d.eye_h, d.eye_v, d.valid, eyes, p)
    jaw = np.where(d.valid, np.clip(_median_filter(np.where(d.valid, d.jaw, np.nan), 3), 0.0, 1.0), np.nan)
    face_conf = np.where(d.valid, np.clip(d.in_frame, 0.0, 1.0)
                         * np.clip(1.0 - np.maximum(0.0, np.abs(np.nan_to_num(d.yaw)) - 45.0) / 45.0, 0.2, 1.0),
                         0.0)

    # ---- events
    events: list[VisualEvent] = []
    if d.t_us.size:
        events += detect_blinks(d.t_us, eyes, frame_us, p)
        events += detect_gaze_events(d.t_us, gaze, frame_us, p)
        events += detect_face_lost(d.t_us, d.valid, frame_us, p)
    events.sort(key=lambda e: (e.start_us, e.kind))

    # ---- samples (decimated)
    sidx = _sample_indices(d.t_us, sample_fps, duration_us, frame_us)
    samples: list[VisualSample] = []
    for i in sidx:
        box = None
        if d.valid[i]:
            x0, y0, x1, y1 = d.boxes[i]
            box = FaceBox(x=round(float(x0), 5), y=round(float(y0), 5), w=round(float(x1 - x0), 5),
                          h=round(float(y1 - y0), 5))
        samples.append(VisualSample(
            t_us=int(d.t_us[i]), face_box=box, face_conf=round(float(face_conf[i]), 4),
            eyes_open=_r(eyes[i]) if d.valid[i] else None,
            gaze_off=_r(gaze.gaze_off[i]) if d.valid[i] else None,
            mouth_open=_r(jaw[i]) if d.valid[i] else None,
            head_yaw=_r(d.yaw[i], 2) if d.valid[i] else None,
            head_pitch=_r(d.pitch[i], 2) if d.valid[i] else None,
            blur=_r(d.blur[i]), luma=_r(d.luma[i]),
        ))
    track = smooth_face_track(d.t_us, d.boxes, d.valid, [s.t_us for s in samples], p) if samples else None
    tops = list(d.head_tops or [])
    visual = Visual(sample_fps=float(sample_fps), samples=samples, events=events, face_track=track,
                    head_top_ratio=head_top_ratio(tops) if track is not None else None, head_tops=tops)

    if save:
        _save_side_files(job, d, eyes, gaze, jaw, rate, vp, path, p, model_path, visual, time.monotonic() - t0)
    return visual


def _save_side_files(job: Job, d: _Dense, eyes: np.ndarray, gaze: GazeSignals, jaw: np.ndarray, rate: Any,
                     vp: VideoProbe, path: Path, p: VisualParams, model_path: Path | None, visual: Visual,
                     elapsed_s: float) -> None:
    from studio.jobs import write_json_atomic

    job.index_dir.mkdir(parents=True, exist_ok=True)
    dense_path = job.index_dir / "visual_dense.npz"
    tmp = dense_path.with_name(".visual_dense.tmp.npz")
    np.savez_compressed(
        tmp, t_us=d.t_us, face=d.valid, boxes=d.boxes.astype(np.float32), eyes_open=eyes.astype(np.float32),
        gaze_off=gaze.gaze_off.astype(np.float32), gaze_dev_deg=gaze.dev.astype(np.float32),
        gaze_yaw_deg=gaze.gaze_yaw.astype(np.float32), gaze_pitch_deg=gaze.gaze_pitch.astype(np.float32),
        mouth_open=jaw.astype(np.float32), head_yaw=d.yaw.astype(np.float32), head_pitch=d.pitch.astype(np.float32),
        blur=d.blur.astype(np.float32), luma=d.luma.astype(np.float32), n_faces=d.n_faces,
        fps=np.array([rate.numerator, rate.denominator], np.int64),
    )
    os.replace(tmp, dense_path)
    n = int(d.t_us.size)
    faced = int(d.valid.sum())
    kinds: dict[str, int] = {}
    for e in visual.events:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    try:
        mp_version = _import_mediapipe().__version__ if model_path is not None else None
    except Exception:  # pragma: no cover
        mp_version = None
    with np.errstate(all="ignore"):
        meta = {
            "source": str(path),
            "source_is_mezz": Path(path) == job.mezz_path,
            "source_geometry": {"width": vp.width, "height": vp.height,
                                "fps": f"{vp.fps.numerator}/{vp.fps.denominator}", "duration_us": vp.duration_us},
            "analysis_fps": f"{rate.numerator}/{rate.denominator}",
            "decode_size": list(_decode_dims(vp, p.decode_short_side_max)),
            "frames": n,
            "face_frames": faced,
            "face_found_ratio": round(faced / n, 4) if n else 0.0,
            "multi_face_ratio": round(float((d.n_faces > 1).mean()), 4) if n else 0.0,
            "gaze_baseline_deg": {"yaw": round(gaze.baseline[0], 2), "pitch": round(gaze.baseline[1], 2)},
            "ear_p50": _r(float(np.nanmedian(d.ear)) if faced else float("nan")),
            "blur_var_median": _r(float(np.nanmedian(d.blur_var)) if n else float("nan"), 1),
            "luma_median": _r(float(np.nanmedian(d.luma)) if n else float("nan")),
            "events": kinds,
            "head_top": {"ratio": visual.head_top_ratio, "measured": len(visual.head_tops),
                         "touches_top": sum(1 for t in visual.head_tops if t.touches_top), "method": "grabcut"},
            "model": {"file": MODEL_FILENAME, "sha256": MODEL_SHA256, "mediapipe": mp_version} if model_path else
                     {"analyzer": "injected"},
            "params": asdict(p),
            "elapsed_s": round(elapsed_s, 2),
        }
    write_json_atomic(job.index_dir / "visual_meta.json", meta)


def load_dense(job: Job) -> dict[str, np.ndarray] | None:
    """Per-analysis-frame signals saved by :func:`analyze_visual` (``t_us``, ``face``, ``boxes``,
    ``eyes_open``, ``gaze_off``, ``gaze_dev_deg``, ``mouth_open``, ``head_yaw``/``head_pitch``, ``blur``,
    ``luma``, ``fps`` [num, den]); None if not computed."""
    p = job.index_dir / "visual_dense.npz"
    if not p.exists():
        return None
    with np.load(p) as z:
        return {k: z[k] for k in z.files}

