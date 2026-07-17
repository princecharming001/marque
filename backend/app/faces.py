"""Speaker face detection + smart b-roll inset placement (v3, research-backed).

Detection: OpenCV YuNet (cv2.FaceDetectorYN) — the ranked winner for a CPU-only
Render instance: 105fps @640x480 on desktop CPU, 0.834 AP_easy on WIDER Face, MIT
license, ships inside opencv-python-headless with a vendored ~232KB ONNX
(assets/face_detection_yunet_2023mar.onnx, opencv_zoo). LLM-vision bounding boxes
were explicitly rejected (documented near-zero bbox accuracy: 0/200 correct boxes
in independent GPT-4o tests; token-space coordinate degradation). dlib rejected
(no manylinux wheel — source builds fail on Render); mediapipe rejected (~75MB of
deps for no accuracy gain, legacy API deprecated).

Sampling: a talking head is near-static — sample ~1fps-equivalent keyframes across
the take (VisAug, arXiv 2508.03410, samples at 1fps and OR-accumulates), take the
MEDIAN box (robust to a stray gesture frame), lock it for the whole video (the
research recommendation: lock at shot start, never re-target mid-shot).

Placement: the broadcast over-the-shoulder (OTS) rule — the inset goes on the side
OPPOSITE the face, in the negative space, never covering the padded face box, the
caption band, or platform UI safe zones (TikTok: top ~130px, bottom ~484px, right
~140px, left ~44px; IG Reels top up to ~220px — the union governs). Candidate is
rejected/shrunk progressively (patent US6778224 overlap-threshold pattern; VisAug
shrink-until-clear loop) and degrades to the standard panel when nothing fits.

Everything fail-soft: no cv2 / no faces / download failure → None → callers fall
back to the fixed panel geometry. Deterministic per input.
"""
from __future__ import annotations

import logging
import os
import statistics
import subprocess
import tempfile

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "assets",
                           "face_detection_yunet_2023mar.onnx")

# 1080x1920 frame constants (normalized). Union of TikTok + IG Reels UI safe zones.
_SAFE_TOP = 140 / 1920        # clears TikTok ~130px top UI (IG top handled by band choice)
_SAFE_LEFT = 48 / 1080
_SAFE_RIGHT = 140 / 1080      # TikTok engagement rail
_CAPTION_BAND = (0.53, 0.71)  # default captions pos_y 0.62 ± band (Conbersa 35-70% convention)
_FACE_PAD = 0.18              # pad the face box by ~18% of face height (research: 15-20%)
_INSET_W = 0.42               # 40-55% width band (research) — start at 42%
_INSET_AR = 16 / 9            # inner media aspect (w/h)
_SHRINK_STEPS = 3             # VisAug: shrink progressively until a clear spot exists
_SHRINK_FACTOR = 0.85


def detect_face_box(video_path_or_url: str, samples: int = 8,
                    duration_s: float | None = None) -> dict | None:
    """Median normalized face box {x,y,w,h} over `samples` frames spread across the
    take, or None (fail-soft). Frames are decoded at 480p for speed (YuNet detects
    10-300px faces; a talking head at 480p is well inside that)."""
    try:
        import cv2  # noqa: F401 — optional dependency; absence degrades gracefully
    except ImportError:
        return None
    if not os.path.exists(_MODEL_PATH):
        logging.warning("[faces] YuNet model missing at %s", _MODEL_PATH)
        return None
    try:
        return _detect(video_path_or_url, samples, duration_s)
    except Exception as e:
        logging.warning("[faces] detection failed (fail-soft): %s", e)
        return None


def _detect(src: str, samples: int, duration_s: float | None) -> dict | None:
    import cv2
    import numpy as np  # bundled with opencv

    if duration_s is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True, timeout=60)
        try:
            duration_s = float(probe.stdout.strip())
        except ValueError:
            duration_s = 30.0
    times = [duration_s * (i + 0.5) / samples for i in range(samples)]

    det = cv2.FaceDetectorYN.create(_MODEL_PATH, "", (480, 854), 0.6)
    boxes: list[tuple[float, float, float, float]] = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            frame_path = os.path.join(td, f"f{i}.jpg")
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", src,
                 "-frames:v", "1", "-vf", "scale=-2:854", "-q:v", "4", frame_path],
                capture_output=True, timeout=60)
            if r.returncode != 0 or not os.path.exists(frame_path):
                continue
            img = cv2.imread(frame_path)
            if img is None:
                continue
            h, w = img.shape[:2]
            det.setInputSize((w, h))
            _, faces = det.detect(img)
            if faces is None or len(faces) == 0:
                continue
            # Largest face wins (research: lock onto the primary subject).
            fx, fy, fw, fh = max(
                ((f[0], f[1], f[2], f[3]) for f in faces), key=lambda b: b[2] * b[3])
            boxes.append((fx / w, fy / h, fw / w, fh / h))
    if len(boxes) < max(2, samples // 4):     # too few hits → unreliable → fail-soft
        return None
    # float() casts: cv2 returns numpy float32s, which are NOT JSON-serializable and
    # would poison the persisted EDL.
    return {
        "x": round(float(statistics.median(b[0] for b in boxes)), 4),
        "y": round(float(statistics.median(b[1] for b in boxes)), 4),
        "w": round(float(statistics.median(b[2] for b in boxes)), 4),
        "h": round(float(statistics.median(b[3] for b in boxes)), 4),
    }


def _intersects(a: tuple[float, float, float, float],
                b: tuple[float, float, float, float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def smart_inset_rect(face_box: dict | None,
                     caption_band: tuple[float, float] = _CAPTION_BAND) -> dict | None:
    """Choose the inset rect (normalized {x,y,w,h}) for a smart-placed b-roll item.

    OTS rule: face center x < 0.45 → inset RIGHT; > 0.55 → inset LEFT; centered →
    top-band OTS-wide centered above the head. Hard rejection tests per candidate
    (US6778224 pattern): padded face box, caption band, platform safe zones. Shrinks
    up to _SHRINK_STEPS before giving up (caller degrades to panel). None when no
    face box (caller keeps standard panel geometry)."""
    if not face_box:
        return None
    pad = face_box["h"] * _FACE_PAD
    face = (face_box["x"] - pad, face_box["y"] - pad,
            face_box["w"] + 2 * pad, face_box["h"] + 2 * pad)
    cx = face_box["x"] + face_box["w"] / 2

    w = _INSET_W
    for _ in range(_SHRINK_STEPS + 1):
        h = (w * 1080 / _INSET_AR) / 1920          # height in frame fraction, 16:9 inner
        if cx > 0.55:
            x = _SAFE_LEFT                          # face right → inset LEFT
        elif cx < 0.45:
            x = 1.0 - _SAFE_RIGHT - w               # face left → inset RIGHT
        else:
            x = (1.0 - w) / 2                       # centered → OTS-wide top-center
        y = _SAFE_TOP
        cand = (x, y, w, h)
        cap = (0.0, caption_band[0], 1.0, caption_band[1] - caption_band[0])
        if not _intersects(cand, face) and not _intersects(cand, cap):
            return {"x": round(x, 4), "y": round(y, 4),
                    "w": round(w, 4), "h": round(h, 4)}
        w *= _SHRINK_FACTOR
    return None
