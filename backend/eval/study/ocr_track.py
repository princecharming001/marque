"""Caption-track extraction: burned-in caption text/geometry/timing from frames.

Frames are sampled at cfg.ocr_fps (5fps, 720px wide — Apple Vision and PaddleOCR
both need >=720p for stylized text) in ONE ffmpeg pass, then read by an OcrEngine.
Engines:
  - ocrmac: Apple Vision (VNRecognizeTextRequest) via the study venv's python,
    invoked as ONE subprocess per reel over the whole frame list (keeps pyobjc
    out of the backend interpreter). arm64-native, excellent on heavy-stroke text.
  - fake: injectable for tests — a dict of frame-index -> [OcrLine].

Geometry rule (faces.py's documented finding): coordinates come ONLY from OCR;
Claude vision is used for style labels elsewhere, never boxes.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from eval.study.common import STUDY_DIR, norm_token

VENV_PY = STUDY_DIR / ".venv-study" / "bin" / "python"

# OcrLine: {"text": str, "conf": float, "bbox": (x0, y0, x1, y1) normalized, y up-down}
MIN_CONF = 0.5
CHUNK_SIM = 0.80          # SequenceMatcher ratio to merge consecutive frames
BAND_OVERLAP = 0.5        # y-band overlap required to merge


@dataclass
class FrameRec:
    t: float
    lines: list          # list[dict OcrLine]


def extract_frames(video_path: str, fps: float, workdir: str) -> list[tuple[float, str]]:
    """One ffmpeg pass -> (timestamp, jpeg_path) list."""
    out_pat = str(Path(workdir) / "f%05d.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps={fps},scale=720:-2",
         "-q:v", "3", out_pat],
        capture_output=True, timeout=600, check=True)
    frames = sorted(Path(workdir).glob("f*.jpg"))
    return [((i + 0.5) / fps, str(p)) for i, p in enumerate(frames)]


_OCRMAC_SCRIPT = r"""
import json, sys
from ocrmac import ocrmac
out = []
for path in sys.argv[1:]:
    try:
        anns = ocrmac.OCR(path, recognition_level="accurate").recognize()
    except Exception:
        anns = []
    lines = []
    for text, conf, bbox in anns:
        # ocrmac bbox: (x, y, w, h) normalized with ORIGIN BOTTOM-LEFT (Vision
        # convention) -> convert to top-left x0,y0,x1,y1
        x, y, w, h = bbox
        lines.append({"text": text, "conf": float(conf),
                      "bbox": [x, 1.0 - y - h, x + w, 1.0 - y]})
    out.append(lines)
print(json.dumps(out))
"""


class OcrmacEngine:
    name = "ocrmac:apple-vision"

    def read_batch(self, jpeg_paths: list[str]) -> list[list[dict]]:
        if not jpeg_paths:
            return []
        out: list[list[dict]] = []
        # chunk argv to stay under ARG_MAX; one venv boot per ~200 frames
        for i in range(0, len(jpeg_paths), 200):
            batch = jpeg_paths[i:i + 200]
            r = subprocess.run([str(VENV_PY), "-c", _OCRMAC_SCRIPT, *batch],
                               capture_output=True, timeout=1800, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ocrmac batch failed: {r.stderr[-300:]}")
            out.extend(json.loads(r.stdout))
        return out


class FakeEngine:
    """Tests: frames[i] -> canned lines."""
    name = "fake"

    def __init__(self, canned: dict[int, list[dict]]):
        self.canned = canned

    def read_batch(self, jpeg_paths: list[str]) -> list[list[dict]]:
        return [self.canned.get(i, []) for i in range(len(jpeg_paths))]


def get_engine(name: str, canned: dict | None = None):
    if name == "ocrmac":
        return OcrmacEngine()
    if name == "fake":
        return FakeEngine(canned or {})
    raise ValueError(f"unknown ocr engine {name}")


def read_caption_track(video_path: str, fps: float, engine) -> list[FrameRec]:
    with tempfile.TemporaryDirectory() as td:
        frames = extract_frames(video_path, fps, td)
        lines_per_frame = engine.read_batch([p for _, p in frames])
    return [FrameRec(t=t, lines=[l for l in lines
                                 if l.get("conf", 0) >= MIN_CONF and l.get("text", "").strip()])
            for (t, _), lines in zip(frames, lines_per_frame)]


def caption_band(frames: list[FrameRec]) -> tuple[float, float] | None:
    """The modal y-band where caption text lives: histogram of line y-centers
    (0.05 bins); the dominant bin ± one bin. Lines far outside it are candidate
    title-card/CTA text and are routed to cards.py, not the caption track."""
    centers = [((l["bbox"][1] + l["bbox"][3]) / 2)
               for f in frames for l in f.lines]
    if len(centers) < 5:
        return None
    bins: dict[int, int] = {}
    for c in centers:
        bins[int(c / 0.05)] = bins.get(int(c / 0.05), 0) + 1
    top = max(bins, key=lambda k: bins[k])
    return (max(0.0, (top - 1) * 0.05), min(1.0, (top + 2) * 0.05))


def band_lines(f: FrameRec, band: tuple[float, float]) -> list[dict]:
    lo, hi = band
    return [l for l in f.lines if lo <= (l["bbox"][1] + l["bbox"][3]) / 2 <= hi]


def _frame_text(lines: list[dict]) -> str:
    return " ".join(l["text"] for l in sorted(lines, key=lambda l: l["bbox"][1]))


def _norm_text(s: str) -> str:
    return " ".join(norm_token(t) for t in s.split() if norm_token(t))


def chunk_caption_track(frames: list[FrameRec], fps: float,
                        band: tuple[float, float] | None = None) -> list[dict]:
    """Merge consecutive frames into caption chunks. Karaoke highlight sweeps
    keep the TEXT stable frame-to-frame (only color changes, which OCR ignores)
    so they don't split chunks — style detection happens in the vision stage."""
    band = band or caption_band(frames)
    if band is None:
        return []
    half = 0.5 / fps
    chunks: list[dict] = []
    cur: dict | None = None
    for f in frames:
        lines = band_lines(f, band)
        text = _frame_text(lines)
        norm = _norm_text(text)
        if not norm:
            if cur:
                chunks.append(cur)
                cur = None
            continue
        if cur:
            sim = SequenceMatcher(None, _norm_text(cur["text"]), norm).ratio()
            y_ov = _y_overlap(cur["bbox"], _union_bbox(lines))
            if sim >= CHUNK_SIM and y_ov >= BAND_OVERLAP:
                cur["t1"] = f.t + half
                # keep the LONGEST observed text (karaoke reveals grow)
                if len(norm) > len(_norm_text(cur["text"])):
                    cur["text"] = text
                cur["bbox"] = _union_bbox(lines, cur["bbox"])
                continue
            chunks.append(cur)
        cur = {"text": text, "t0": max(0.0, f.t - half), "t1": f.t + half,
               "bbox": _union_bbox(lines)}
    if cur:
        chunks.append(cur)
    for c in chunks:
        toks = [t for t in c["text"].split() if norm_token(t)]
        alpha = [ch for ch in c["text"] if ch.isalpha()]
        upper = sum(1 for ch in alpha if ch.isupper())
        c["n_words"] = len(toks)
        c["y_center"] = (c["bbox"][1] + c["bbox"][3]) / 2
        c["case"] = ("upper" if alpha and upper / len(alpha) >= 0.8
                     else "lower" if alpha and upper / len(alpha) <= 0.2 else "mixed")
    return chunks


def _union_bbox(lines: list[dict], prev: list | None = None) -> list:
    boxes = [l["bbox"] for l in lines] + ([prev] if prev else [])
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _y_overlap(a: list, b: list) -> float:
    lo, hi = max(a[1], b[1]), min(a[3], b[3])
    shorter = min(a[3] - a[1], b[3] - b[1]) or 1e-6
    return max(0.0, hi - lo) / shorter


def align_captions_to_speech(chunks: list[dict], words: list[dict]) -> float:
    """Set lead_ms per chunk (negative = caption LEADS speech). Token-stream
    alignment via SequenceMatcher, constrained to ±2500ms so duplicate words
    ("the") can't cross-match. Returns the match rate (data-quality stat)."""
    if not chunks or not words:
        for c in chunks:
            c["lead_ms"] = None
        return 0.0
    wtoks = [(norm_token(w.get("word", "")), w.get("start_ms", 0)) for w in words]
    wseq = [t for t, _ in wtoks]
    matched = 0
    for c in chunks:
        first = next((norm_token(t) for t in c["text"].split() if norm_token(t)), "")
        c["lead_ms"] = None
        if not first:
            continue
        t0_ms = c["t0"] * 1000
        best = None
        for tok, start_ms in wtoks:
            if tok == first and abs(start_ms - t0_ms) <= 2500:
                if best is None or abs(start_ms - t0_ms) < abs(best - t0_ms):
                    best = start_ms
        if best is not None:
            c["lead_ms"] = round(t0_ms - best)
            matched += 1
    _ = SequenceMatcher(None, wseq, wseq)   # reserved for a stricter pass
    return matched / len(chunks)
