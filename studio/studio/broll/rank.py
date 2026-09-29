"""B-roll retrieval, quality gates and the thresholded judgment sheet.

The funnel (``skills/editing/broll-sourcing.md`` §6–7): 50–200 candidates → **embedding recall** (top ~20
by rank) → **code gates** over the range that will actually be used → Director/critic judges ≤ 8 on a
**contact sheet at the final crop** with a written bar, where "none" is always a valid answer.

Recall: :class:`ClipEmbedder`
-----------------------------
``open_clip`` **PE-Core-B-16** (Meta Perception Encoder, Apache-2.0; weights in ``models/open_clip``). The
research picked PE-Core over SigLIP2 for recall (it beats SigLIP2 zero-shot and on video retrieval) and
the B/16 size keeps the download at 1.8 GB. MobileCLIP was rejected (Apple research-only weights). PE-Core
text context is **32 tokens**, so the need is embedded as a short concrete phrase, ensembled over prompt
templates and the query variants (averaged, re-normalised). Frames are embedded as **overlapping square
tiles along the long axis** (a 9:16 frame → 3 squares) instead of the model's square squash or centre
crop: nothing is distorted or dropped, and the per-tile scores localise the subject for the crop.
Cosines are compared only against the same need text, never across queries (they are not calibrated
across texts). Look-alikes the brief names (``expected_false``) are scored zero-shot: softmax over
``[need, *expected_false]`` gives ``p_need`` per frame.

Gates (hard; a failed gate rejects, it is not a penalty)
-------------------------------------------------------
Measured on frames sampled every ~0.33 s across the used range at an analysis size of 540 px short side
("sharpness at phone size"):

* **resolution** — source pixels inside the final crop window vs the output size: upscale ≤ 1.25×
  (creator media ≤ 1.5×), using the *delivered rendition* size and the active picture after letterbox
  removal.
* **sharpness** — 90th percentile of per-tile Laplacian standard deviation (a 4x6 grid), median over
  frames. The *sharpest region* decides, so shallow depth of field (in-focus subject, bokeh background) is
  never mistaken for blur.
* **letterbox** — bars that are dark and flat in every frame; the active area is removed before cropping.
* **text / watermark** — Apple Vision OCR (``VNRecognizeTextRequest``, accurate) when available (macOS),
  else an MSER text-line heuristic: stock-vendor and platform watermark strings or ``@handles`` reject;
  prominent text rejects stock (≥ 1.5 % of the frame; a headline/sign line ≥ 4 % tall and 15 % wide; or a
  legible word line — ≥ 5 letters, ≥ 2 % tall (≈ 40 px on the phone) and ≥ 12 % wide, e.g. a café's name
  on a cup: a brand/trademark and a caption competitor); small scene text (key legends) is reported only.
  Screenshots are allowed text. For video with camera motion, a motion-compensated **overlay detector**
  (static-in-frame vs moving-with-camera hypotheses) flags burned-in logos/subtitles as a soft warning
  on the sheet (a tracked subject can look the same, so it is not a hard gate).
* **faces** — YuNet (OpenCV Zoo, MIT) face count and size; a recognisable stranger (face ≥ 6 % of frame
  height) rejects when the need says ``allow_faces=False`` (negative/medical narration: Pexels bars
  showing people in a bad light).
* **shot change** — a hard cut inside the used range rejects (HSV histogram distance between samples).
* **crop** — the subject must fit the mode's window: a saliency map (spectral residual + faces + a weak
  centre prior, fused with the CLIP tile relevance to the need) gives the best window per frame, with a
  penalty on windows that slice through a face (keep it whole or leave it out); the
  path is smoothed into a static crop or a slow pan. ≥ 60 % of the top-saliency mass must stay inside the
  window and the cropped frames must keep ≥ 88 % of the full-frame relevance, else use split/PiP.
* **duration** — the clip must cover ``duration x speed`` from the chosen in-point. The in-point is
  chosen by code: the window with the best mean relevance that contains no shot change and skips
  fades/settles at the head.

* **grade** (only with an ``a_roll_reference``) — the insert's near-neutral pixels (chroma < 12, mid-tones)
  must land within ΔE*ab 5 of the A-roll's neutrals *after* the conform's partial-strength match
  (``broll.reject_neutral_delta_e``); without enough neutral pixels on either side it is no measurement
  and is not gated (a red car filling the frame says nothing about white balance).

The chosen use range is written onto the registered asset (``in_ms``/``out_ms``): ops copy the registry
record, and a model never supplies an in-point (invariant 3).

Frame-rate (< 30 fps with motion into a 60 fps timeline, or slow motion below the timeline rate), subject
size (< ⅓ of the frame) and a
black-and-white picture in a colour A-roll are soft penalties and are reported. Everything measured is in
the diagnostics dict, so the Director sees the numbers next to the sheet.

Judgment: :func:`contact_sheet_for_judgment` renders 4–6 frames per candidate across the used range, **at
the final crop** and — given the A-roll — **grade-matched exactly as the conform will**, bracketed by the
A-roll frames either side of the cut (the doctrine judges inserts after conform, in context). Each tile
is burned with the candidate ID and timecode, and the rubric prompt is returned; :func:`parse_judgment`
enforces the hard threshold in code (every rubric item ≥ 4/5 or the candidate is not accepted; "none" is
valid; a pick below the bar is overridden to none).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from studio.broll.sources import BrollCandidate

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job

__all__ = [
    "Need",
    "GateConfig",
    "Embedder",
    "ClipEmbedder",
    "get_embedder",
    "set_embedder",
    "MediaProbe",
    "probe_media",
    "sample_frames",
    "CropPlan",
    "mode_aspect",
    "mode_size",
    "plan_crop",
    "saliency_map",
    "detect_faces",
    "detect_letterbox",
    "sharpness",
    "static_overlay",
    "ocr_text",
    "text_regions",
    "analyze_candidate",
    "retrieve",
    "rank_candidates",
    "JudgmentSheet",
    "Judgment",
    "RUBRIC_ITEMS",
    "contact_sheet_for_judgment",
    "parse_judgment",
]

#: Output geometry of the 9:16 master.
OUT_W, OUT_H = 1080, 1920
ANALYSIS_SHORT = 540

WATERMARK_TERMS: tuple[str, ...] = (
    "shutterstock", "istock", "gettyimages", "getty images", "adobe stock", "adobestock", "dreamstime", "alamy",
    "depositphotos", "123rf", "pond5", "storyblocks", "videoblocks", "envato", "motion array", "motionarray",
    "artgrid", "vecteezy", "freepik", "canva", "pixabay", "giphy", "tenor", "klipy", "tiktok", "capcut",
    "watermark", "preview only", "sample",
)
PLATFORM_TERMS: tuple[str, ...] = ("instagram", "reels", "youtube", "shorts", "snapchat", "likee", "kwai")
_HANDLE_RE = re.compile(r"(?:^|\s)@[A-Za-z0-9_.]{3,}")


# ============================================================================================ config
@dataclass(frozen=True)
class GateConfig:
    """Gate thresholds (priors from ``skills/editing/constants.yaml`` where they exist)."""

    upscale_max: float = 1.25  # broll.sourcing.upscale_max_x
    own_upscale_max: float = 1.5  # broll.sourcing.own_footage_upscale_max_x
    min_sharpness: float = 7.0  # p90 tile Laplacian std at 540 px (0–255 scale)
    min_sharpness_own: float = 4.5
    max_text_frac: float = 0.015  # OCR text area / frame area that makes stock "text-heavy"
    headline_h: float = 0.04  # a single text line this tall …
    headline_w: float = 0.15  # … and this wide is a headline/sign/brand: stock with it is rejected
    legible_h: float = 0.02  # a word line this tall (≈ 40 px in a 1920 frame) …
    legible_w: float = 0.12  # … this wide …
    legible_letters: int = 5  # … with this many letters reads as a brand/sign (keyboard legends never do)
    min_text_height: float = 0.012  # OCR boxes shorter than this (of frame height) are ignored
    max_overlay_frac: float = 0.0015  # static-overlay area / frame area
    face_recognisable_h: float = 0.06  # face height / frame height
    min_crop_coverage: float = 0.60
    min_crop_keep: float = 0.88  # cropped-frame relevance / full-frame relevance
    shot_change_dist: float = 0.55  # HSV Bhattacharyya distance between consecutive samples
    min_fps_for_motion: float = 30.0  # broll.sourcing.min_fps_for_motion
    subject_min_frac: float = 0.33  # broll.subject_min_frac (soft)
    max_neutral_delta_e: float = 5.0  # broll.reject_neutral_delta_e (after the partial-strength grade match)
    sample_spacing_s: float = 0.33  # broll.sourcing.gate_sampling_s ∈ [0.25, 0.5]
    max_samples: int = 12
    coarse_samples: int = 12


@dataclass
class Need:
    """What the insert must show (the visual brief), and how it will be used.

    ``text`` is one short concrete phrase (subject + action + setting; PE-Core reads 32 tokens).
    ``queries`` are the search variants (ensembled into the need embedding). ``expected_false`` are the
    look-alikes that would be wrong, written before looking. ``allow_text`` None = auto (True for
    screenshots). ``allow_faces`` False rejects recognisable strangers (negative/medical narration).
    """

    text: str
    queries: Sequence[str] = ()
    expected_false: Sequence[str] = ()
    mode: str = "full"
    duration_s: float | None = None
    speed: float = 1.0
    split_ratio: float = 0.5
    allow_text: bool | None = None
    allow_faces: bool = True
    output_fps: float | None = None

    @classmethod
    def coerce(cls, need: Need | str | Mapping[str, Any], **overrides: Any) -> Need:
        if isinstance(need, Need):
            n = need
        elif isinstance(need, str):
            n = cls(text=need)
        else:
            n = cls(**dict(need))
        if overrides:
            n = Need(**{**n.__dict__, **{k: v for k, v in overrides.items() if v is not None}})
        if not n.text.strip():
            raise ValueError("need.text is empty")
        return n

    @property
    def source_span_s(self) -> float | None:
        return None if self.duration_s is None else float(self.duration_s) * float(self.speed)


def mode_size(mode: str, *, split_ratio: float = 0.5, pip_width: float = 0.40, source_aspect: float | None = None,
              out_w: int = OUT_W, out_h: int = OUT_H) -> tuple[int, int]:
    """Pixel size of the insert layer for ``mode`` (even numbers). PiP keeps the source aspect."""
    if mode in ("full", "card"):
        return out_w, out_h
    if mode in ("split_top", "split_bottom"):
        return out_w, _even(out_h * split_ratio)
    if mode == "pip":
        a = source_aspect or (9 / 16)
        w = out_w * pip_width
        h = w / a
        if h > out_h * 0.28 * 1.6:  # never taller than the compile default max (0.28 H) by much
            h = out_h * 0.28 * 1.6
            w = h * a
        # render PiP at 2x its on-screen size (the compositor scales down with Lanczos)
        return _even(min(out_w, w * 2)), _even(min(out_h, h * 2))
    raise ValueError(f"unknown insert mode {mode!r}")


def mode_aspect(mode: str, *, split_ratio: float = 0.5, source_aspect: float | None = None) -> float:
    """Target width/height of the crop window for ``mode`` (PiP: the source aspect, i.e. no crop)."""
    if mode == "pip":
        return source_aspect or (9 / 16)
    w, h = mode_size(mode, split_ratio=split_ratio)
    return w / h


def _even(x: float) -> int:
    return max(2, round(x / 2.0) * 2)


# ============================================================================================ embedder
class Embedder(Protocol):
    name: str
    logit_scale: float

    def embed_images(self, images: Sequence[np.ndarray]) -> np.ndarray: ...

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...


class ClipEmbedder:
    """open_clip image/text embedder (default PE-Core-B-16), lazily loaded, MPS when available.

    Images are RGB uint8 arrays; each is resized (antialiased bicubic) to the model's square input. Use
    :func:`tiles` to feed non-square frames without distortion.
    """

    def __init__(self, model: str | None = None, pretrained: str | None = None, *, cache_dir: str | None = None,
                 device: str | None = None, batch_size: int = 32):
        self.model_name = model or os.environ.get("STUDIO_BROLL_CLIP_MODEL", "PE-Core-B-16")
        self.pretrained = pretrained or os.environ.get("STUDIO_BROLL_CLIP_PRETRAINED", "meta")
        if cache_dir is None:
            from studio.config import get_settings

            cache_dir = str(get_settings().models_dir / "open_clip")
        self.cache_dir = cache_dir
        self._device = device
        self.batch_size = batch_size
        self.name = f"open_clip:{self.model_name}/{self.pretrained}"
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()  # MPS/Metal command buffers are not thread-safe
        self._model: Any = None
        self._tok: Any = None
        self._size = 224
        self._mean = (0.5, 0.5, 0.5)
        self._std = (0.5, 0.5, 0.5)
        self.logit_scale = 100.0
        self._text_cache: dict[str, np.ndarray] = {}

    def _load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            import open_clip
            import torch

            dev = self._device or ("mps" if torch.backends.mps.is_available() else "cpu")
            # use the cached weight file directly when present: no Hub round-trip, works offline
            pretrained: str = self._local_weights() or self.pretrained
            with _skip_random_init():  # every parameter is overwritten by the (strict) checkpoint load
                model, _, preprocess = open_clip.create_model_and_transforms(
                    self.model_name, pretrained=pretrained, cache_dir=self.cache_dir, device=dev)
            model.eval()
            self._tok = open_clip.get_tokenizer(self.model_name, cache_dir=self.cache_dir)
            for t in getattr(preprocess, "transforms", []):
                if type(t).__name__ == "Normalize":
                    self._mean, self._std = tuple(t.mean), tuple(t.std)
            size = getattr(getattr(model, "visual", None), "image_size", 224)
            self._size = int(size[0] if isinstance(size, (tuple, list)) else size)
            with contextlib.suppress(Exception):
                self.logit_scale = float(model.logit_scale.exp().item())
            self._device = dev
            self._model = model

    def _local_weights(self) -> str | None:
        """Path of the already-downloaded weight file for this model/tag, if any."""
        with contextlib.suppress(Exception):
            import open_clip
            from huggingface_hub import try_to_load_from_cache

            cfg = open_clip.get_pretrained_cfg(self.model_name, self.pretrained) or {}
            repo = (cfg.get("hf_hub") or "").strip("/")
            if repo:
                for fn in ("open_clip_model.safetensors", "open_clip_pytorch_model.bin"):
                    hit = try_to_load_from_cache(repo, fn, cache_dir=self.cache_dir)
                    if isinstance(hit, str) and os.path.exists(hit):
                        return hit
        return None

    def embed_images(self, images: Sequence[np.ndarray]) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        self._load()
        if not images:
            return np.zeros((0, 1), np.float32)
        outs = []
        mean = torch.tensor(self._mean).view(1, 3, 1, 1)
        std = torch.tensor(self._std).view(1, 3, 1, 1)
        for i in range(0, len(images), self.batch_size):
            batch = []
            for im in images[i:i + self.batch_size]:
                arr = im[..., :3]
                if not (arr.flags.writeable and arr.flags.c_contiguous):
                    arr = np.array(arr, copy=True, order="C")
                t = torch.from_numpy(arr).permute(2, 0, 1).float().div_(255.0)
                t = F.interpolate(t[None], size=(self._size, self._size), mode="bicubic", antialias=True,
                                  align_corners=False).clamp_(0, 1)
                batch.append(t)
            x = (torch.cat(batch) - mean) / std
            with self._infer_lock, torch.no_grad():
                f = self._model.encode_image(x.to(self._device))
                f = F.normalize(f.float(), dim=-1).cpu().numpy()
            outs.append(f)
        return np.concatenate(outs).astype(np.float32)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        self._load()
        with self._infer_lock:
            todo = [t for t in dict.fromkeys(texts) if t not in self._text_cache]
            if todo:
                tok = self._tok(list(todo)).to(self._device)
                with torch.no_grad():
                    f = F.normalize(self._model.encode_text(tok).float(), dim=-1).cpu().numpy()
                for t, v in zip(todo, f, strict=True):
                    self._text_cache[t] = v.astype(np.float32)
        return np.stack([self._text_cache[t] for t in texts]) if texts else np.zeros((0, 1), np.float32)


@contextlib.contextmanager
def _skip_random_init() -> Any:
    """Turn ``torch.nn.init`` fillers into no-ops while a model is built for a strict checkpoint load
    (random ``normal_`` init of PE-Core's text tower alone costs ~30 s on CPU)."""
    import torch.nn.init as init

    names = ("normal_", "trunc_normal_", "uniform_", "xavier_uniform_", "xavier_normal_", "kaiming_uniform_",
             "kaiming_normal_", "orthogonal_")
    saved = {n: getattr(init, n) for n in names if hasattr(init, n)}
    try:
        for n in saved:
            setattr(init, n, lambda tensor, *a, **k: tensor)
        yield
    finally:
        for n, f in saved.items():
            setattr(init, n, f)


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """The process-wide default embedder (PE-Core-B-16, loaded on first use)."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = ClipEmbedder()
        return _embedder


def set_embedder(e: Embedder | None) -> None:
    """Install (or clear) the default embedder (tests, alternative models)."""
    global _embedder
    with _embedder_lock:
        _embedder = e


_TEMPLATES = ("{}", "a photo of {}", "a video frame showing {}")


def text_embedding(embedder: Embedder, phrases: Sequence[str], *, weights: Sequence[float] | None = None) -> np.ndarray:
    """Prompt-ensembled, normalised embedding of ``phrases`` (each expanded over the templates)."""
    texts, w = [], []
    for i, p in enumerate(phrases):
        p = " ".join(p.split())
        if not p:
            continue
        for t in _TEMPLATES:
            texts.append(t.format(p))
            w.append(weights[i] if weights is not None else 1.0)
    if not texts:
        raise ValueError("no text to embed")
    e = embedder.embed_texts(texts)
    v = (e * np.asarray(w, np.float32)[:, None]).sum(0)
    return (v / (np.linalg.norm(v) + 1e-8)).astype(np.float32)


def tiles(img: np.ndarray, *, overlap: float = 0.25) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
    """Square tiles covering ``img`` along its long axis (with overlap): ``[(tile, (x, y, w, h))]``."""
    h, w = img.shape[:2]
    s = min(h, w)
    long = max(h, w)
    if long <= s * 1.25:
        return [(img, (0, 0, w, h))]
    n = max(2, math.ceil((long - s) / (s * (1 - overlap))) + 1)
    out = []
    for k in range(n):
        off = round((long - s) * k / (n - 1))
        if h >= w:
            out.append((img[off:off + s, :], (0, off, s, s)))
        else:
            out.append((img[:, off:off + s], (off, 0, s, s)))
    return out


def frame_embeddings(embedder: Embedder, frames: Sequence[np.ndarray]) -> tuple[np.ndarray, list[list[tuple[int, ...]]],
                                                                                    list[np.ndarray]]:
    """``(frame_vecs [n,d], tile_rects per frame, tile_vecs per frame)``; a frame vector is the normalised
    mean of its tile vectors."""
    all_tiles, owners, rects = [], [], []
    for i, f in enumerate(frames):
        ts = tiles(f)
        rects.append([r for _, r in ts])
        for t, _ in ts:
            all_tiles.append(t)
            owners.append(i)
    if not all_tiles:
        return np.zeros((0, 1), np.float32), [], []
    emb = embedder.embed_images(all_tiles)
    owners_a = np.asarray(owners)
    fv, tv = [], []
    for i in range(len(frames)):
        e = emb[owners_a == i]
        tv.append(e)
        m = e.mean(0)
        fv.append(m / (np.linalg.norm(m) + 1e-8))
    return np.stack(fv).astype(np.float32), rects, tv


# ============================================================================================ media access
@dataclass
class MediaProbe:
    src: str
    width: int
    height: int
    fps: float | None
    duration_s: float
    is_image: bool
    transfer: str | None = None
    matrix: str | None = None

    @property
    def hdr(self) -> bool:
        return (self.transfer or "").lower() in ("arib-std-b67", "smpte2084")


_probe_cache: dict[str, MediaProbe] = {}
_IMAGE_CODECS = {"png", "mjpeg", "jpeg2000", "webp", "bmp", "tiff", "gif", "heif", "hevc_image", "jpegls"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif"}


def _tools() -> tuple[str, str]:
    from studio.config import get_settings

    s = get_settings()
    return s.ffmpeg, s.ffprobe


def probe_media(src: str | os.PathLike[str]) -> MediaProbe:
    """Display size (after rotation), fps, duration and colour tags of a local file or URL (ffprobe)."""
    key = str(src)
    if not key.startswith(("http://", "https://")):
        p = Path(key)
        st = p.stat()
        key = f"{p.resolve()}|{st.st_mtime}|{st.st_size}"
    hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    _, ffprobe = _tools()
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(src)],
                             check=True, capture_output=True, text=True, timeout=60).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise ValueError(f"cannot probe {src}: {e}") from e
    data = json.loads(out)
    vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
               and not (s.get("disposition") or {}).get("attached_pic")), None)
    if vs is None:  # an image file's only "video" stream may be flagged attached_pic in some containers
        vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if vs is None:
        raise ValueError(f"no picture in {src}")
    w, h = int(vs["width"]), int(vs["height"])
    rot = 0
    for sd in vs.get("side_data_list") or []:
        if "rotation" in sd:
            with contextlib.suppress(TypeError, ValueError):
                rot = round(float(sd["rotation"])) % 360
    if rot in (90, 270):
        w, h = h, w
    fps = None
    for k in ("r_frame_rate", "avg_frame_rate"):
        with contextlib.suppress(ValueError, ZeroDivisionError, TypeError):
            f = Fraction(vs.get(k) or "0/1")
            if f > 0:
                fps = float(f)
                break
    dur = 0.0
    for cand in (vs.get("duration"), (data.get("format") or {}).get("duration")):
        with contextlib.suppress(TypeError, ValueError):
            if cand is not None and float(cand) > 0:
                dur = float(cand)
                break
    ext = Path(str(src).split("?")[0]).suffix.lower()
    nb = vs.get("nb_frames")
    is_image = (vs.get("codec_name") in _IMAGE_CODECS and (dur <= 0.05 or str(nb) == "1")) or \
        (ext in _IMAGE_EXTS and dur <= 0.05)
    mp = MediaProbe(src=str(src), width=w, height=h, fps=fps, duration_s=dur, is_image=bool(is_image),
                    transfer=vs.get("color_transfer"), matrix=vs.get("color_space"))
    _probe_cache[key] = mp
    return mp


def _analysis_size(w: int, h: int, short: int = ANALYSIS_SHORT) -> tuple[int, int]:
    s = short / min(w, h)
    if s >= 1:
        return _even(w), _even(h)
    return _even(w * s), _even(h * s)


def _swscale_matrix(matrix: str | None) -> str:
    m = (matrix or "").lower()
    if m.startswith("bt2020"):
        return "bt2020"
    if m in ("smpte170m", "bt470bg"):
        return "bt601"
    return "bt709"


def _rgb_chain(mp: MediaProbe, w: int, h: int, *, pix_fmt: str = "rgb24") -> str:
    """Decode chain → display-referred BT.709 RGB at ``w x h`` (HDR tone-mapped like the mezzanine)."""
    if mp.hdr:
        tin = "arib-std-b67" if (mp.transfer or "").lower() == "arib-std-b67" else "smpte2084"
        return (f"zscale=w={w}:h={h}:tin={tin}:t=linear:npl=203:p=bt709:agamma=0:f=spline36,format=gbrpf32le,"
                "setparams=color_primaries=bt709:color_trc=linear:colorspace=bt709,"
                "tonemap=mobius:param=0.6:desat=2,"
                f"zscale=tin=linear:t=bt709:p=bt709:m=bt709:r=pc:agamma=0:dither=error_diffusion,format={pix_fmt}")
    return (f"scale={w}:{h}:flags=area+accurate_rnd+full_chroma_int:in_color_matrix={_swscale_matrix(mp.matrix)}"
            f":out_range=pc,format={pix_fmt}")


def load_image(src: str | os.PathLike[str] | bytes, *, max_side: int | None = None) -> np.ndarray:
    """Decode a still to RGB uint8: EXIF orientation applied, embedded ICC profile converted to sRGB
    (iPhone Display-P3 stills otherwise look desaturated), optional Lanczos downscale."""
    from PIL import Image, ImageCms, ImageOps

    from studio.broll.sources import register_heif

    register_heif()
    fh: Any = io.BytesIO(src) if isinstance(src, bytes) else src
    with Image.open(fh) as im0:
        im = ImageOps.exif_transpose(im0)
        icc = im.info.get("icc_profile")
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P", "CMYK", "I;16", "I", "F"):
            im = im.convert("RGB")
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
            im = Image.alpha_composite(bg, im)
        if icc and im.mode in ("RGB", "RGBA", "CMYK"):
            try:
                src_prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                im = ImageCms.profileToProfile(im.convert("RGB") if im.mode == "RGBA" else im, src_prof,
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception:
                im = im.convert("RGB")
        im = im.convert("RGB")
        if max_side and max(im.size) > max_side:
            s = max_side / max(im.size)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
        return np.asarray(im, dtype=np.uint8).copy()


def _fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    import httpx

    from studio.broll.sources import USER_AGENT

    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def _decode_frame(mp: MediaProbe, t: float, w: int, h: int) -> np.ndarray | None:
    ffmpeg, _ = _tools()
    t = max(0.0, min(t, max(0.0, mp.duration_s - 1.5 / (mp.fps or 30))))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.4f}", "-i", mp.src,
           "-frames:v", "1", "-an", "-sn", "-vf", _rgb_chain(mp, w, h), "-f", "rawvideo", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return None
    need = w * h * 3
    if r.returncode != 0 or len(r.stdout) < need:
        return None
    return np.frombuffer(r.stdout[:need], np.uint8).reshape(h, w, 3).copy()


def sample_frames(src: str | os.PathLike[str], times_s: Sequence[float], *, short_side: int = ANALYSIS_SHORT,
                  probe: MediaProbe | None = None, workers: int = 6) -> list[np.ndarray | None]:
    """RGB uint8 frames at ``times_s`` (display orientation, BT.709, HDR tone-mapped) scaled so the short
    side is ``short_side`` (never upscaled). Stills return the same picture for every time. Works on local
    paths and http(s) URLs (ffmpeg range-reads remote mp4s, so gates never download whole masters)."""
    s = str(src)
    ext = Path(s.split("?")[0]).suffix.lower()
    if ext in _IMAGE_EXTS and ext != ".gif":  # an animated GIF plays: probe it like a video
        img = _load_still_any(s)
        img = _resize_short(img, short_side)
        return [img for _ in times_s]
    mp = probe or probe_media(s)
    if mp.is_image:
        img = _load_still_any(s)
        img = _resize_short(img, short_side)
        return [img for _ in times_s]
    w, h = _analysis_size(mp.width, mp.height, short_side)
    if not times_s:
        return []
    ts = [float(t) for t in times_s]
    span = max(ts) - min(ts)
    # dense samples (a short used range): one sequential decode beats N seeks that each decode a GOP
    if len(ts) >= 3 and (span <= 8.0 or span / (len(ts) - 1) <= 1.0):
        got = _decode_range(mp, ts, w, h)
        if got is not None:
            return got
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ts)))) as ex:
        return list(ex.map(lambda t: _decode_frame(mp, t, w, h), ts))


def _decode_range(mp: MediaProbe, times: Sequence[float], w: int, h: int) -> list[np.ndarray | None] | None:
    """Decode ``[min(times), max(times)]`` once at the source's CFR rate and keep the frames nearest to
    ``times`` (±½ frame). Returns None when the decode fails (callers fall back to seeks)."""
    ffmpeg, _ = _tools()
    fps = mp.fps or 30.0
    last = max(0.0, mp.duration_s - 1.5 / fps)
    ts = [min(max(0.0, t), last) for t in times]
    t0 = min(ts)
    span = max(ts) - t0 + 2.0 / fps
    fr = Fraction(fps).limit_denominator(1001)
    chain = f"setpts=PTS-STARTPTS,fps=fps={fr.numerator}/{fr.denominator}:round=near," + _rgb_chain(mp, w, h)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t0:.4f}", "-t", f"{span:.4f}",
           "-i", mp.src, "-an", "-sn", "-vf", chain, "-f", "rawvideo", "-"]
    want: dict[int, list[int]] = {}
    for i, t in enumerate(ts):
        want.setdefault(round((t - t0) * fps), []).append(i)
    out: list[np.ndarray | None] = [None] * len(ts)
    size = w * h * 3
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    try:
        assert proc.stdout is not None
        j = 0
        last_frame = None
        while j <= max(want):
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            if j in want:
                arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
                for i in want[j]:
                    out[i] = arr
            last_frame = buf
            j += 1
        # requests past the decoded end (rounding at the tail) get the last decoded frame
        if last_frame is not None:
            arr = np.frombuffer(last_frame, np.uint8).reshape(h, w, 3).copy()
            for k, idxs in want.items():
                if k >= j:
                    for i in idxs:
                        out[i] = arr
    finally:
        with contextlib.suppress(Exception):
            proc.kill()
        proc.wait()
    return out if any(o is not None for o in out) else None


def _load_still_any(src: str) -> np.ndarray:
    if src.startswith(("http://", "https://")):
        return load_image(_fetch_bytes(src))
    try:
        return load_image(src)
    except Exception:
        mp = probe_media(src)
        f = _decode_frame(mp, 0.0, _even(mp.width), _even(mp.height))
        if f is None:
            raise
        return f


def _resize_short(img: np.ndarray, short: int) -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    if min(h, w) <= short:
        return img
    tw, th = _analysis_size(w, h, short)
    return cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)


# ============================================================================================ measurements
def sharpness(frame: np.ndarray, grid: tuple[int, int] = (4, 6)) -> float:
    """90th percentile over a grid of tiles of the Laplacian standard deviation (0–255 luma scale)."""
    import cv2

    g = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    h, w = g.shape
    cols, rows = (grid if w >= h else grid[::-1])
    vals = []
    for r in range(rows):
        for c in range(cols):
            t = lap[r * h // rows:(r + 1) * h // rows, c * w // cols:(c + 1) * w // cols]
            if t.size:
                vals.append(float(t.std()))
    return float(np.percentile(vals, 90)) if vals else 0.0


def detect_letterbox(frames: Sequence[np.ndarray], *, dark: float = 24.0, flat: float = 6.0
                     ) -> tuple[float, float, float, float]:
    """Active picture ``(x0, y0, x1, y1)`` normalised: rows/columns that are dark and flat in *every* frame
    (bars) are excluded. Returns the full frame when no bars are found."""
    if not frames:
        return (0.0, 0.0, 1.0, 1.0)
    gs = np.stack([f.mean(axis=2) for f in frames if f is not None]).astype(np.float32)
    if gs.size == 0:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = gs.shape[1:]
    row_max = gs.max(axis=(0, 2))
    row_std = np.max(gs.std(axis=2), axis=0)
    col_max = gs.max(axis=(0, 1))
    col_std = np.max(gs.std(axis=1), axis=0)
    rbar = (row_max < dark) & (row_std < flat)
    cbar = (col_max < dark) & (col_std < flat)

    def extent(bar: np.ndarray, n: int) -> tuple[int, int]:
        a = 0
        while a < n and bar[a]:
            a += 1
        b = n
        while b > a and bar[b - 1]:
            b -= 1
        # only count bars of at least 2 % (noise rows at the edge are not letterboxing)
        if a < n * 0.02:
            a = 0
        if n - b < n * 0.02:
            b = n
        return a, b

    y0, y1 = extent(rbar, h)
    x0, x1 = extent(cbar, w)
    if y1 - y0 < h * 0.3 or x1 - x0 < w * 0.3:  # an all-dark shot is not a letterbox
        return (0.0, 0.0, 1.0, 1.0)
    return (x0 / w, y0 / h, x1 / w, y1 / h)


_yunet_local = threading.local()
_yunet_create_lock = threading.Lock()


def _yunet() -> Any | None:
    import cv2

    det = getattr(_yunet_local, "det", None)
    if det is not None:
        return det
    from studio.config import get_settings

    p = get_settings().models_dir / "broll" / "face_detection_yunet_2023mar.onnx"
    if not p.exists():
        _yunet_local.det = False
        return False
    lg = getattr(getattr(cv2, "utils", None), "logging", None)
    with _yunet_create_lock:  # the log level is process-global: save/restore under one lock
        level = lg.getLogLevel() if lg is not None else None
        try:
            if lg is not None:  # OpenCV 5's DNN engine warns "Targets are not supported" on every create
                lg.setLogLevel(lg.LOG_LEVEL_ERROR)
            det = cv2.FaceDetectorYN.create(str(p), "", (320, 320), 0.7, 0.3, 50)
        except Exception:
            det = False
        finally:
            if lg is not None and level is not None:
                lg.setLogLevel(level)
    _yunet_local.det = det
    return det


def detect_faces(frame: np.ndarray, *, min_score: float = 0.75) -> list[tuple[float, float, float, float, float]]:
    """Faces as ``(x, y, w, h, score)`` normalised (YuNet; empty list if the model file is missing)."""
    import cv2

    det = _yunet()
    if not det:
        return []
    h, w = frame.shape[:2]
    det.setInputSize((w, h))
    try:
        _, faces = det.detect(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    except cv2.error:
        return []
    out = []
    for f in faces if faces is not None else []:
        x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
        if score >= min_score and fw > 2 and fh > 2:
            out.append((max(0.0, x / w), max(0.0, y / h), fw / w, fh / h, score))
    return out


def saliency_map(frame: np.ndarray, faces: Sequence[tuple[float, ...]] = (), *, size: int = 96) -> np.ndarray:
    """Normalised (sum 1) saliency at ``size`` px on the short side: spectral-residual saliency (structure)
    averaged with frequency-tuned colour contrast (Achanta et al. 2009: Lab distance of the blurred image
    from the frame mean — a distinct subject against its background), plus face blobs (×3: people look at
    faces first) and a weak centre prior."""
    import cv2

    h, w = frame.shape[:2]
    sw, sh = (size, max(8, round(size * h / w))) if w <= h else (max(8, round(size * w / h)), size)
    small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
    sal = None
    with contextlib.suppress(Exception):
        sr = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, m = sr.computeSaliency(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
        if ok:
            sal = m.astype(np.float32)
    if sal is None:  # fallback: gradient energy
        g = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32)
        sal = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    sal = cv2.GaussianBlur(sal, (0, 0), max(1.0, 0.02 * max(sw, sh)))
    sal = sal / (sal.max() + 1e-8)
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (0, 0), 1.0).astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)
    ft = np.linalg.norm(lab - lab.reshape(-1, 3).mean(axis=0), axis=2)
    ft = ft / (ft.max() + 1e-8)
    sal = 0.5 * sal + 0.5 * ft
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    centre = np.exp(-(((xx / sw - 0.5) / 0.35) ** 2 + ((yy / sh - 0.5) / 0.35) ** 2) / 2)
    m = sal + 0.15 * centre
    for fx, fy, fw, fh, *_ in faces:
        cx, cy = (fx + fw / 2) * sw, (fy + fh / 2) * sh
        sx, sy = max(1.0, fw * sw * 0.6), max(1.0, fh * sh * 0.6)
        m += 3.0 * np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2) / 2)
    m = np.maximum(m, 0)
    return (m / (m.sum() + 1e-8)).astype(np.float32)


def _camera_transforms(g: np.ndarray) -> tuple[list[np.ndarray | None], float]:
    """Global camera motion between consecutive frames: ``[None, M_1, …]`` where ``M_k`` is a 2x3 similarity
    mapping frame k-1 pixel coordinates to frame k (ORB features + RANSAC, so a moving subject over a
    locked-off background reads ~identity) or None without trackable structure; plus the largest
    per-step motion as a fraction of the short side."""
    import cv2

    k, h, w = g.shape
    orb = cv2.ORB_create(nfeatures=1000)
    feats = [orb.detectAndCompute((x * 255).astype(np.uint8), None) for x in g]
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    short = min(h, w)
    out: list[np.ndarray | None] = [None]
    best = 0.0
    for i in range(1, k):
        (kp0, d0), (kp1, d1) = feats[i - 1], feats[i]
        M = None
        if d0 is not None and d1 is not None and len(kp0) >= 20 and len(kp1) >= 20:
            m = bf.match(d0, d1)
            if len(m) >= 15:
                a = np.float32([kp0[j.queryIdx].pt for j in m])
                b = np.float32([kp1[j.trainIdx].pt for j in m])
                M, inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=3.0)
                if M is None or inl is None or inl.sum() < max(12, 0.3 * len(m)):
                    M = None
        out.append(M)
        if M is not None:
            sc = math.hypot(M[0, 0], M[1, 0])
            best = max(best, (math.hypot(M[0, 2], M[1, 2]) + abs(sc - 1.0) * short / 2) / short)
    return out, best


def static_overlay(frames: Sequence[np.ndarray], *, min_camera_motion: float = 0.03) -> dict[str, Any]:
    """Burned-in overlay detector for video (logos, watermarks, subtitles composited over the picture).

    Two hypotheses are compared per pixel between consecutive samples: *static in frame coordinates*
    (``|f_k − f_{k-1}|``) and *moving with the camera* (``|f_k − warp(f_{k-1}, camera_k)|``, the global
    similarity from ORB+RANSAC). Scene content follows the camera; an overlay stays put. A pixel is overlay evidence
    when the static hypothesis explains it much better than the camera one; pixels with that evidence in
    ≥ 70 % of the frames form the overlay. Edges parallel to the motion (the aperture problem) fit both
    hypotheses and are never flagged. This needs the camera to move (≥ ``min_camera_motion`` of the short
    side): on a locked-off shot static scene content and an overlay are indistinguishable, so the result
    is inconclusive and OCR carries the watermark check (a naive "edges present in every frame" test was
    tried first and rejected clean Pexels tripod shots of keyboards)."""
    import cv2

    fr = [f for f in frames if f is not None]
    if len(fr) < 5:
        return {"conclusive": False, "overlay_frac": 0.0, "boxes": [], "camera_motion": None}
    g = np.stack([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0 for f in fr])
    Ms, cam = _camera_transforms(g)
    base = {"camera_motion": round(cam, 4)}
    if cam < min_camera_motion:
        return {"conclusive": False, "overlay_frac": 0.0, "boxes": [], **base}
    H, W = g.shape[1:]
    blurred = [cv2.GaussianBlur(x, (0, 0), 1.5) for x in g]
    votes = np.zeros((H, W), np.float32)
    counted = np.zeros((H, W), np.float32)
    ones = np.ones((H, W), np.float32)
    for k in range(1, len(g)):
        M = Ms[k]
        if M is None:
            continue
        sc = math.hypot(M[0, 0], M[1, 0])
        if (math.hypot(M[0, 2], M[1, 2]) + abs(sc - 1.0) * min(H, W) / 2) < 0.02 * min(H, W):
            continue  # too little motion between these frames to tell the hypotheses apart
        f0, fk = blurred[k - 1], blurred[k]
        w0 = cv2.warpAffine(f0, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
        valid = cv2.warpAffine(ones, M, (W, H), flags=cv2.INTER_NEAREST, borderValue=0) > 0.5
        e_static = cv2.GaussianBlur(np.abs(fk - f0), (0, 0), 2.0)
        e_scene = cv2.GaussianBlur(np.abs(fk - w0), (0, 0), 2.0)
        ev = (e_scene - e_static > 0.06) & (e_static < 0.04) & valid
        votes += ev
        counted += valid
    if counted.max() < 3:
        return {"conclusive": False, "overlay_frac": 0.0, "boxes": [], **base}
    mask = ((votes / np.maximum(counted, 1)) >= 0.7) & (counted >= 3)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    footprint = np.zeros((H, W), bool)
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if a < max(40, H * W * 0.0005):
            continue
        boxes.append([round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)])
        footprint[y:y + h, x:x + w] = True
    boxes.sort(key=lambda b: -(b[2] * b[3]))
    return {"conclusive": True, "overlay_frac": round(float(footprint.mean()), 5), "boxes": boxes[:8], **base}


_ocr_lock = threading.Lock()
_ocr_ok: bool | None = None


_rapid_engine: Any = None
_rapid_ok: bool | None = None


def ocr_backend() -> str:
    """The OCR backend :func:`ocr_text` uses on this host: ``vision``, ``rapidocr`` or ``none``."""
    if _ocr_ok is not False:
        try:
            import Vision  # noqa: F401
            return "vision"
        except ImportError:
            pass
    return "rapidocr" if _rapid() is not None else "none"


def _rapid() -> Any:
    """RapidOCR (PaddleOCR det/rec models on ONNX Runtime, Apache-2.0; weights ship in the wheel): the OCR on hosts
    without Apple Vision (Linux servers)."""
    global _rapid_engine, _rapid_ok
    if _rapid_ok is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _rapid_engine = RapidOCR()
            _rapid_ok = True
        except Exception:
            _rapid_ok = False
    return _rapid_engine if _rapid_ok else None


def _ocr_rapid(frame: np.ndarray) -> list[tuple[str, float, tuple[float, float, float, float]]] | None:
    eng = _rapid()
    if eng is None:
        return None
    H, W = frame.shape[:2]
    with _ocr_lock:
        result, _elapse = eng(np.ascontiguousarray(frame[:, :, ::-1]))  # the engine expects BGR (OpenCV order)
    out: list[tuple[str, float, tuple[float, float, float, float]]] = []
    for box, txt, conf in result or []:
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
        out.append((str(txt), float(conf), (x0 / W, y0 / H, (min(W, max(xs)) - x0) / W, (min(H, max(ys)) - y0) / H)))
    return out


def ocr_text(frame: np.ndarray) -> list[tuple[str, float, tuple[float, float, float, float]]] | None:
    """OCR: ``[(text, confidence, (x, y, w, h) normalised, top-left origin)]``. Apple Vision on macOS, RapidOCR
    elsewhere; None when neither backend is available."""
    global _ocr_ok
    if _ocr_ok is False:
        return _ocr_rapid(frame)
    try:
        import Foundation
        import objc
        import Vision
    except ImportError:
        _ocr_ok = False
        return _ocr_rapid(frame)
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, format="PNG")
    raw = buf.getvalue()
    out: list[tuple[str, float, tuple[float, float, float, float]]] = []
    with _ocr_lock, objc.autorelease_pool():
        data = Foundation.NSData.dataWithBytes_length_(raw, len(raw))
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        ok, _err = handler.performRequests_error_([req], None)
        if not ok:
            return out
        for o in req.results() or []:
            cands = o.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            bb = o.boundingBox()
            x, y, w, h = float(bb.origin.x), float(bb.origin.y), float(bb.size.width), float(bb.size.height)
            out.append((str(c.string()), float(c.confidence()), (x, 1.0 - y - h, w, h)))
    _ocr_ok = True
    return out


def text_regions(frame: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Fallback text-line detector (MSER character candidates grouped into horizontal lines): normalised
    boxes. Used when OCR is unavailable."""
    import cv2

    g = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    H, W = g.shape
    mser = cv2.MSER_create(delta=5, min_area=max(12, int(H * W * 0.00002)), max_area=int(H * W * 0.01))
    regions, bboxes = mser.detectRegions(g)
    chars = []
    for x, y, w, h in bboxes:
        if h < H * 0.012 or h > H * 0.12:
            continue
        ar = w / max(1, h)
        if not (0.1 <= ar <= 1.4):
            continue
        chars.append((x, y, w, h))
    if not chars:
        return []
    chars.sort(key=lambda b: (b[1] + b[3] / 2, b[0]))
    lines: list[list[tuple[int, int, int, int]]] = []
    for b in chars:
        cy = b[1] + b[3] / 2
        placed = False
        for ln in lines:
            ly = np.mean([c[1] + c[3] / 2 for c in ln])
            lh = np.mean([c[3] for c in ln])
            near = min(abs(b[0] - (c[0] + c[2])) for c in ln) < 1.5 * lh
            if abs(cy - ly) < 0.4 * lh and 0.6 < b[3] / lh < 1.6 and near:
                ln.append(b)
                placed = True
                break
        if not placed:
            lines.append([b])
    out = []
    for ln in lines:
        if len(ln) < 4:
            continue
        x0 = min(c[0] for c in ln)
        y0 = min(c[1] for c in ln)
        x1 = max(c[0] + c[2] for c in ln)
        y1 = max(c[1] + c[3] for c in ln)
        if (x1 - x0) < 2.5 * (y1 - y0):
            continue
        out.append((x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H))
    return out


def _hsv_hist(frame: np.ndarray) -> np.ndarray:
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [16, 8, 8], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()


def _hist_dist(a: np.ndarray, b: np.ndarray) -> float:
    import cv2

    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))


# ============================================================================================ crop planning
@dataclass
class CropPlan:
    """Where the insert's crop window sits over time (all normalised to the display frame).

    ``window`` = (w, h) of the crop window; ``path`` = ``[(t_s, cx, cy)]`` window centres at absolute source
    times (clamped inside ``active``); one entry = static crop. ``coverage`` = share of the top-saliency
    mass inside the window along the path; ``drift`` = range of the centre along the free axis.
    """

    aspect: float
    active: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    window: tuple[float, float] = (1.0, 1.0)
    path: list[tuple[float, float, float]] = field(default_factory=list)
    coverage: float = 1.0
    drift: float = 0.0
    face_cut: bool = False
    source_size: tuple[int, int] | None = None

    def center_at(self, t_s: float) -> tuple[float, float]:
        if not self.path:
            ax0, ay0, ax1, ay1 = self.active
            return ((ax0 + ax1) / 2, (ay0 + ay1) / 2)
        if len(self.path) == 1 or t_s <= self.path[0][0]:
            return self.path[0][1], self.path[0][2]
        if t_s >= self.path[-1][0]:
            return self.path[-1][1], self.path[-1][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(self.path, self.path[1:], strict=False):
            if t0 <= t_s <= t1:
                u = 0.0 if t1 <= t0 else (t_s - t0) / (t1 - t0)
                u = u * u * (3 - 2 * u)  # smoothstep between keys: no velocity kinks at keys
                return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
        return self.path[-1][1], self.path[-1][2]

    def rect_at(self, t_s: float) -> tuple[float, float, float, float]:
        """``(x, y, w, h)`` of the window at ``t_s`` (normalised, clamped to the active area)."""
        cx, cy = self.center_at(t_s)
        ww, wh = self.window
        ax0, ay0, ax1, ay1 = self.active
        x = min(max(cx - ww / 2, ax0), ax1 - ww)
        y = min(max(cy - wh / 2, ay0), ay1 - wh)
        return (x, y, ww, wh)

    @property
    def is_static(self) -> bool:
        return len(self.path) <= 1 or self.drift < 1e-3

    def to_dict(self) -> dict[str, Any]:
        return {"aspect": round(self.aspect, 6), "active": [round(v, 5) for v in self.active],
                "window": [round(v, 5) for v in self.window],
                "path": [[round(t, 4), round(x, 5), round(y, 5)] for t, x, y in self.path],
                "coverage": round(self.coverage, 4), "drift": round(self.drift, 4), "face_cut": self.face_cut,
                "source_size": list(self.source_size) if self.source_size else None}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CropPlan:
        return cls(aspect=float(d["aspect"]), active=tuple(d.get("active") or (0, 0, 1, 1)),  # type: ignore[arg-type]
                   window=tuple(d.get("window") or (1, 1)),  # type: ignore[arg-type]
                   path=[(float(t), float(x), float(y)) for t, x, y in d.get("path") or []],
                   coverage=float(d.get("coverage", 1.0)), drift=float(d.get("drift", 0.0)),
                   face_cut=bool(d.get("face_cut", False)),
                   source_size=tuple(d["source_size"]) if d.get("source_size") else None)  # type: ignore[arg-type]


def _window_dims(aspect: float, active: tuple[float, float, float, float], frame_aspect: float) -> tuple[float, float]:
    """Largest window of pixel aspect ``aspect`` inside ``active`` (normalised units of the frame)."""
    ax0, ay0, ax1, ay1 = active
    aw, ah = ax1 - ax0, ay1 - ay0
    # in pixels: frame W = frame_aspect * H; window pixel aspect = (ww*W)/(wh*H) = ww/wh * frame_aspect
    if (aw * frame_aspect) / ah > aspect:  # active area wider than target → height-limited
        wh = ah
        ww = aspect * wh / frame_aspect
    else:
        ww = aw
        wh = ww * frame_aspect / aspect
    return (min(ww, aw), min(wh, ah))


_SEM_NOISE = 0.003  # within-frame window cosine spread that is noise
_SEM_RAMP = 0.012  # spread above the noise at which the semantic term reaches full weight


def plan_crop(frames: Sequence[np.ndarray | None], times_s: Sequence[float], aspect: float, *,
              active: tuple[float, float, float, float] | None = None,
              faces: Sequence[Sequence[tuple[float, ...]]] | None = None,
              semantic: np.ndarray | None = None, max_pan_per_s: float = 0.12, static_keep: float = 0.9,
              source_size: tuple[int, int] | None = None) -> CropPlan:
    """Best crop-window path for ``aspect`` over ``frames`` (at ``times_s``, absolute source seconds).

    For each frame and each of 33 window positions along the free axis the score is the share of the
    frame's top-saliency mass inside the window (faces dominate the saliency). ``semantic`` (the need's
    CLIP relevance of windows at the same positions, see :func:`semantic_positions`) re-weights it: when
    the windows differ clearly in relevance (a cosine spread ≥ 0.015; ≤ 0.003 is noise) the semantic term
    carries up to 65 % of the score, so the window follows *what the voice names*, not whatever is busiest
    (backlit keys out-shout the hands typing on them in any saliency map). Windows that slice through a
    face are penalised (:func:`_face_cut_penalty`, relative to the least-cutting position): a face is kept
    whole or left out, never cut at the mouth or eyes. A single static
    window is used when it keeps ≥ ``static_keep`` of the per-frame optimum in every frame; otherwise a
    Viterbi path (smoothness cost, velocity ≤ ``max_pan_per_s`` of the frame per second) gives a slow,
    steady pan that follows the subject.
    """
    fr = [(f, t, (faces[i] if faces is not None else ())) for i, (f, t) in enumerate(zip(frames, times_s, strict=True))
          if f is not None]
    if not fr:
        return CropPlan(aspect=aspect)
    h0, w0 = fr[0][0].shape[:2]
    frame_aspect = w0 / h0
    act = active or (0.0, 0.0, 1.0, 1.0)
    ww, wh = _window_dims(aspect, act, frame_aspect)
    ax0, ay0, ax1, ay1 = act
    horizontal = (ax1 - ax0) - ww > (ay1 - ay0) - wh  # free axis
    lo = (ax0 + ww / 2) if horizontal else (ay0 + wh / 2)
    hi = (ax1 - ww / 2) if horizontal else (ay1 - wh / 2)
    fixed = ((ay0 + ay1) / 2) if horizontal else ((ax0 + ax1) / 2)
    n_pos = 33
    grid = np.linspace(lo, hi, n_pos) if hi > lo + 1e-6 else np.array([(lo + hi) / 2])
    times = [t for _, t, _ in fr]
    sem_w = 0.0
    s_norm = None
    if semantic is not None and len(semantic) == len(grid) and len(grid) > 1:
        spread = float(semantic.max() - semantic.min())
        if spread > 1e-6:
            s_norm = (semantic - semantic.min()) / spread
            # PE-Core cosines of windows *within one frame* differ by ~0.003 from noise alone and by 0.01–0.03
            # when one window really holds the named subject (hands vs the keyboard below them): ramp the
            # semantic weight in over that range
            sem_w = 0.65 * min(1.0, max(0.0, (spread - _SEM_NOISE) / _SEM_RAMP))
    covs, scores = [], []
    for f, _t, fcs in fr:
        sal = saliency_map(f, fcs)
        sh, sw = sal.shape
        thr = np.percentile(sal, 80)  # top-saliency mass carries the subject
        top = np.where(sal >= thr, sal, 0.0)
        top_total = top.sum() + 1e-8
        prof = top.sum(axis=0) if horizontal else top.sum(axis=1)
        n = sw if horizontal else sh
        cum = np.concatenate([[0.0], np.cumsum(prof)])
        span = (ww if horizontal else wh) * n
        cov = np.asarray([(_interp_cum(cum, c * n + span / 2) - _interp_cum(cum, c * n - span / 2)) / top_total
                          for c in grid], np.float32)
        covs.append(cov)
        cn = cov / (cov.max() + 1e-8)
        sc = cn if s_norm is None else (1 - sem_w) * cn + sem_w * s_norm
        pen = _face_cut_penalty(fcs, grid, ww if horizontal else wh, horizontal)
        scores.append(np.maximum(sc - _FACE_CUT_WEIGHT * (pen - pen.min()), 0.0))
    S = np.stack(scores)  # [frames, positions]
    C = np.stack(covs)
    best_per_frame = S.max(axis=1)
    mean_curve = S.mean(axis=0)
    k_static = int(np.argmax(mean_curve))
    keeps = np.where(best_per_frame > 1e-6, S[:, k_static] / np.maximum(best_per_frame, 1e-6), 1.0)
    face_cut = False
    if len(grid) == 1 or float(keeps.min()) >= static_keep or len(fr) == 1:
        idx = [k_static] * len(fr)
        static = True
    else:
        idx = _viterbi(S, grid, times, max_pan_per_s)
        static = len(set(idx)) == 1
    path_c = [float(grid[k]) for k in idx]
    if static:
        path = [(times[0], *_xy(path_c[0], fixed, horizontal))]
        drift = 0.0
    else:
        # light smoothing of the discrete path (the renderer interpolates between keys with smoothstep)
        pc = np.asarray(path_c)
        if len(pc) >= 3:
            pc = np.convolve(np.pad(pc, 1, mode="edge"), np.ones(3) / 3, mode="valid")
        path_c = pc.tolist()
        path = [(t, *_xy(c, fixed, horizontal)) for t, c in zip(times, path_c, strict=True)]
        drift = float(max(path_c) - min(path_c))
    coverage = float(np.mean([np.interp(c, grid, cv) if len(grid) > 1 else cv[0] for c, cv in zip(path_c, C,
                                                                                                   strict=True)]))
    for (_f, _t, fcs), c in zip(fr, path_c, strict=True):
        for fx, fy, fw, fh, *_ in fcs:
            a0, a1, lo_e, ext = (c - ww / 2, c + ww / 2, fx, fw) if horizontal else (c - wh / 2, c + wh / 2, fy, fh)
            inside = min(lo_e + ext, a1) - max(lo_e, a0)
            if 0 < inside < ext * 0.85:
                face_cut = True
    return CropPlan(aspect=aspect, active=act, window=(ww, wh), path=path, coverage=coverage, drift=drift,
                    face_cut=face_cut, source_size=source_size)


_FACE_CUT_WEIGHT = 1.2  # a window through a face loses more than any saliency/semantic gain can win back


def _face_cut_penalty(faces: Sequence[tuple[float, ...]], grid: np.ndarray, span: float, horizontal: bool
                      ) -> np.ndarray:
    """Per window position: how badly the window slices through a face (0 = every face fully in or fully out;
    up to 1 = the window keeps a sliver of a face). Faces shorter than 8 % of the frame count
    proportionally less (a distant bystander cut by the frame edge is normal framing). Where no position
    avoids a cut (a face larger than the window) every position pays alike, so the choice is unchanged."""
    pen = np.zeros(len(grid), np.float32)
    for f in faces:
        lo_e, ext = (f[0], f[2]) if horizontal else (f[1], f[3])
        if ext <= 1e-6:
            continue
        # detector boxes run brow-to-chin: pad them so a chin or crown on the window edge also reads as cut
        lo_e, ext = lo_e - 0.12 * ext, ext * 1.24
        a0, a1 = grid - span / 2, grid + span / 2
        frac = np.clip(np.minimum(lo_e + ext, a1) - np.maximum(lo_e, a0), 0.0, None) / ext
        partial = (frac > 0.02) & (frac < 0.9)
        weight = min(1.0, (f[3] if len(f) > 3 else ext) / 0.08)
        pen = np.maximum(pen, np.where(partial, (1.0 - frac) * weight + 0.25 * weight, 0.0).astype(np.float32))
    return pen


def _viterbi(S: np.ndarray, grid: np.ndarray, times: Sequence[float], vmax: float, smooth: float = 2.0) -> list[int]:
    """Max-score path through positions with a linear movement cost and a hard velocity limit."""
    n_f, n_p = S.shape
    acc = S[0].astype(np.float64).copy()
    back = np.zeros((n_f, n_p), np.int64)
    dpos = np.abs(grid[:, None] - grid[None, :])  # [to, from]
    for i in range(1, n_f):
        dt = max(1e-3, times[i] - times[i - 1])
        trans = acc[None, :] - smooth * dpos
        trans[dpos > vmax * dt + 1e-9] = -np.inf
        back[i] = np.argmax(trans, axis=1)
        acc = trans[np.arange(n_p), back[i]] + S[i]
    k = int(np.argmax(acc))
    out = [k]
    for i in range(n_f - 1, 0, -1):
        k = int(back[i][k])
        out.append(k)
    return out[::-1]


def _interp_cum(cum: np.ndarray, x: float) -> float:
    x = min(max(x, 0.0), len(cum) - 1.0)
    i = math.floor(x)
    if i >= len(cum) - 1:
        return float(cum[-1])
    return float(cum[i] + (cum[i + 1] - cum[i]) * (x - i))


def _xy(c: float, fixed: float, horizontal: bool) -> tuple[float, float]:
    return (c, fixed) if horizontal else (fixed, c)


def semantic_positions(embedder: Embedder, frame: np.ndarray | Sequence[np.ndarray], need_vec: np.ndarray,
                       aspect: float, active: tuple[float, float, float, float], n_grid: int = 33,
                       n_eval: int = 9) -> np.ndarray:
    """Relevance to the need of ``n_eval`` crop windows along the free axis (averaged over the given
    frame(s)), interpolated to the ``n_grid`` positions :func:`plan_crop` evaluates: the query-aware part
    of the crop."""
    if not isinstance(frame, np.ndarray):
        fl = [f for f in frame if f is not None]
        if not fl:
            return np.zeros(1, np.float32)
        curves = [semantic_positions(embedder, f, need_vec, aspect, active, n_grid, n_eval) for f in fl]
        return np.mean(np.stack(curves), axis=0).astype(np.float32)
    h, w = frame.shape[:2]
    ww, wh = _window_dims(aspect, active, w / h)
    ax0, ay0, ax1, ay1 = active
    horizontal = (ax1 - ax0) - ww > (ay1 - ay0) - wh
    lo = (ax0 + ww / 2) if horizontal else (ay0 + wh / 2)
    hi = (ax1 - ww / 2) if horizontal else (ay1 - wh / 2)
    if hi <= lo + 1e-6:
        return np.zeros(1, np.float32)
    evals = np.linspace(lo, hi, n_eval)
    crops = []
    for c in evals:
        if horizontal:
            x0 = round((c - ww / 2) * w)
            y0 = round(ay0 * h)
            crops.append(frame[y0:y0 + max(2, round(wh * h)), max(0, x0):max(0, x0) + max(2, round(ww * w))])
        else:
            y0 = round((c - wh / 2) * h)
            x0 = round(ax0 * w)
            crops.append(frame[max(0, y0):max(0, y0) + max(2, round(wh * h)), x0:x0 + max(2, round(ww * w))])
    vecs, _, _ = frame_embeddings(embedder, crops)
    sims = vecs @ need_vec
    return np.interp(np.linspace(lo, hi, n_grid), evals, sims).astype(np.float32)


def crop_frame(frame: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, rw, rh = rect
    x0, y0 = max(0, round(x * w)), max(0, round(y * h))
    x1, y1 = min(w, round((x + rw) * w)), min(h, round((y + rh) * h))
    return frame[y0:max(y0 + 2, y1), x0:max(x0 + 2, x1)]


# ============================================================================================ analysis
def _analysis_source(c: BrollCandidate) -> str | None:
    if c.local_path and Path(c.local_path).exists():
        return c.local_path
    if c.asset is not None and c.asset.path and c.local_path:
        return c.local_path
    return c.analysis_url or c.url or None


def _preview_frames(c: BrollCandidate, n: int = 5) -> list[np.ndarray]:
    """Cheap recall frames: provider thumbnails (remote) or sampled frames (local)."""
    src = _analysis_source(c)
    if c.local_path and src == c.local_path:
        mp = probe_media(src)
        dur = mp.duration_s if not mp.is_image else 0.0
        ts = [dur * (i + 0.5) / n for i in range(n)] if dur > 0 else [0.0]
        return [f for f in sample_frames(src, ts, short_side=336, probe=mp) if f is not None]
    urls = list(dict.fromkeys(c.preview_urls or ([c.preview_url] if c.preview_url else [])))
    if len(urls) > n:
        idx = np.linspace(0, len(urls) - 1, n).round().astype(int)
        urls = [urls[i] for i in dict.fromkeys(idx.tolist())]
    out = []
    for u in urls:
        with contextlib.suppress(Exception):
            out.append(_resize_short(load_image(_fetch_bytes(u)), 336))
    if not out and src:
        with contextlib.suppress(Exception):
            out = [f for f in sample_frames(src, [0.5], short_side=336) if f is not None]
    return out


def retrieve(candidates: Sequence[BrollCandidate], need: Need | str, *, top_k: int = 20,
             embedder: Embedder | None = None, frames_per_candidate: int = 5
             ) -> list[tuple[BrollCandidate, float, dict[str, Any]]]:
    """Recall stage: relevance of each candidate's preview frames to the need; best ``top_k`` by rank."""
    nd = Need.coerce(need)
    emb = embedder or get_embedder()
    need_vec = _need_vector(emb, nd)
    res = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        previews = list(ex.map(lambda c: _safe(lambda: _preview_frames(c, frames_per_candidate), []), candidates))
    for c, frames in zip(candidates, previews, strict=True):
        if not frames:
            res.append((c, -1.0, {"recall": None, "recall_error": "no preview frames"}))
            continue
        fv, _, _ = frame_embeddings(emb, frames)
        sims = fv @ need_vec
        rel = float(0.75 * sims.mean() + 0.25 * sims.min())
        res.append((c, rel, {"recall": round(rel, 4), "recall_frames": len(frames)}))
    res.sort(key=lambda r: -r[1])
    for i, (_, _, d) in enumerate(res):
        d["recall_rank"] = i + 1
    return res[:top_k]


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception:
        return default


def _need_vector(emb: Embedder, nd: Need) -> np.ndarray:
    phrases = [nd.text, *[q for q in nd.queries if q.strip() and q.strip() != nd.text]]
    weights = [2.0] + [1.0] * (len(phrases) - 1)
    return text_embedding(emb, phrases, weights=weights)


def _choose_range(times: Sequence[float], rel: Sequence[float], cuts: Sequence[tuple[float, float]],
                  lumas: Sequence[float], span: float, duration: float) -> float:
    """In-point (source seconds) of the best ``span`` window: highest mean relevance of the samples inside,
    overlapping no shot-change interval ``(lo, hi)``, not starting/ending in a fade (luma well below the
    clip median), preferably clear of the first/last 0.3 s; ties go to the middle of the clip."""
    if span >= duration - 1e-3 or len(times) < 2:
        return 0.0
    med = float(np.median(lumas)) if lumas else 0.0
    best, best_s = None, -1e9
    starts = np.arange(0.0, max(0.0, duration - span) + 1e-9, 0.05)
    for s0 in starts:
        s1 = s0 + span
        if any(s0 < hi and s1 > lo for lo, hi in cuts):
            continue
        idx = [i for i, t in enumerate(times) if s0 - 1e-6 <= t <= s1 + 1e-6]
        if not idx:
            continue
        sc = float(np.mean([rel[i] for i in idx]))
        if lumas and (lumas[idx[0]] < 0.6 * med or lumas[idx[-1]] < 0.6 * med):
            sc -= 0.02  # starts/ends in a fade
        if s0 < 0.3 or s1 > duration - 0.3:
            sc -= 0.004  # settles at the head, run-outs at the tail
        sc -= 0.002 * abs((s0 + s1) / 2 - duration / 2) / max(duration, 1e-6)  # ties → the middle
        if sc > best_s:
            best_s, best = sc, float(s0)
    return 0.0 if best is None else best


def _refine_cut(src: str, mp: MediaProbe, lo: float, hi: float, dist: float, n: int = 6) -> tuple[float, float]:
    """Narrow a shot-change interval ``(lo, hi)`` between two samples to ~(hi - lo) / n by sampling inside it."""
    ts = [lo + (hi - lo) * i / n for i in range(n + 1)]
    frs = sample_frames(src, ts, probe=mp, short_side=180)
    if any(f is None for f in frs):
        return lo, hi
    hs = [_hsv_hist(f) for f in frs]  # type: ignore[arg-type]
    ds = [_hist_dist(hs[i - 1], hs[i]) for i in range(1, len(hs))]
    k = int(np.argmax(ds))
    if ds[k] < dist * 0.5:  # a gradual change (dissolve, pan): keep the whole interval
        return lo, hi
    return ts[k], ts[k + 1]


def analyze_candidate(c: BrollCandidate, need: Need | str, *, embedder: Embedder | None = None,
                      gates: GateConfig | None = None, use_ocr: bool = True, in_s: float | None = None,
                      job: Job | None = None, a_roll_reference: Any = None) -> dict[str, Any]:
    """Full gate analysis of one candidate over the range it would be used for. Returns diagnostics with
    ``gates`` (name → {passed, value, threshold, note}), ``rejected_by``, ``score``, ``in_s`` and ``crop``
    (a :meth:`CropPlan.to_dict`). Also stores ``in_ms``/``crop``/``analysis`` in ``c.meta``.

    ``a_roll_reference`` (the job, the mezzanine path, an :class:`~studio.broll.conform.ARollReference` or
    precomputed :class:`~studio.broll.conform.ColorStats`) adds the **grade** gate: the insert's neutrals
    must land within ΔE ≈ 5 of the A-roll's after the conform's partial-strength match.

    A remote video is analysed from its small analysis rendition, fetched once into a temporary file (a
    dozen HTTPS range-seeks cost more than the whole ~2–5 MB file) and deleted afterwards: no persistent
    copy of provider media is kept (Pexels terms)."""
    src = _analysis_source(c)
    tmp: Path | None = None
    if src and src.startswith(("http://", "https://")) and c.is_video:
        tmp = _fetch_to_temp(src)
        if tmp is not None:
            src = str(tmp)
    try:
        return _analyze_at(c, need, src, embedder=embedder, gates=gates, use_ocr=use_ocr, in_s=in_s, job=job,
                           ref_stats=_ref_stats(a_roll_reference))
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
                tmp.parent.rmdir()


def _fetch_to_temp(url: str, max_bytes: int = 200 << 20) -> Path | None:
    import tempfile

    import httpx

    from studio.broll.sources import USER_AGENT

    d = Path(tempfile.mkdtemp(prefix="broll_an_"))
    out = d / ("a" + (Path(url.split("?")[0]).suffix or ".mp4"))
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as cl, cl.stream("GET", url) as r, open(out, "wb") as fh:
            if r.status_code >= 400:
                raise OSError(f"HTTP {r.status_code}")
            n = 0
            for chunk in r.iter_bytes(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise OSError("analysis rendition too large")
                fh.write(chunk)
        return out
    except Exception:
        with contextlib.suppress(OSError):
            out.unlink()
            d.rmdir()
        return None


def _ref_stats(ref: Any) -> Any:
    """A-roll colour statistics from any reference form (None stays None)."""
    if ref is None:
        return None
    from studio.broll.conform import aroll_color_stats

    return aroll_color_stats(ref)


def _color_fit(c: BrollCandidate, crops: Sequence[np.ndarray], ref: Any,
               g: GateConfig) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``(diagnostics, grade_gate | None)``: the insert's Lab statistics at the final crop, whether it is
    black-and-white, and — against the A-roll — the neutral ΔE before and after the conform's grade match."""
    from studio.broll.conform import DEFAULT_STRENGTH, ColorStats, ColorTransfer, neutral_delta_e

    st = ColorStats.from_frames(crops)
    d: dict[str, Any] = {"l_mean": round(st.l_mean, 2), "l_std": round(st.l_std, 2),
                         "chroma_mean": round(st.chroma_mean, 2), "monochrome": st.monochrome}
    if ref is None:
        return d, None
    strength = DEFAULT_STRENGTH.get(c.kind, 0.5)
    d["aroll_monochrome"] = ref.monochrome
    d["match_strength"] = strength
    if strength <= 0:  # screenshots/UI are never colour-matched: nothing to gate
        return d, None
    t = ColorTransfer.between(st, ref, strength)
    before = neutral_delta_e(crops, ref)
    after = neutral_delta_e(crops, ref, t)
    d["neutral_delta_e_before"] = None if before is None else round(before, 2)
    d["neutral_delta_e_after"] = None if after is None else round(after, 2)
    d["exposure_shift_l"] = round(t.l_shift, 2)
    if after is None:
        return d, None
    return d, {"passed": after <= g.max_neutral_delta_e, "value": round(after, 2), "threshold": g.max_neutral_delta_e,
               "note": "neutral ΔE*ab to the A-roll after the partial-strength grade match"}


def _analyze_at(c: BrollCandidate, need: Need | str, src: str | None, *, embedder: Embedder | None,
                gates: GateConfig | None, use_ocr: bool, in_s: float | None, job: Job | None,
                ref_stats: Any = None) -> dict[str, Any]:
    nd = Need.coerce(need)
    g = gates or GateConfig()
    emb = embedder if embedder is not None else _safe(get_embedder, None)
    diag: dict[str, Any] = {"id": c.id, "source": c.source, "kind": c.kind, "mode": nd.mode}
    gl: dict[str, dict[str, Any]] = {}
    diag["gates"] = gl
    if not src:
        gl["media"] = {"passed": False, "note": "no media to analyse"}
        diag["rejected_by"] = ["media"]
        diag["score"] = -1.0
        return diag
    try:
        mp = probe_media(src)
    except Exception as e:
        gl["media"] = {"passed": False, "note": f"unreadable: {e}"}
        diag["rejected_by"] = ["media"]
        diag["score"] = -1.0
        return diag
    still = mp.is_image or c.is_still
    creator = c.source == "creator"
    allow_text = nd.allow_text if nd.allow_text is not None else c.kind in ("screenshot",)
    dur = 0.0 if still else (mp.duration_s or (c.duration_ms or 0) / 1000)
    span = nd.source_span_s
    need_vec: np.ndarray | None = None
    false_vecs: list[np.ndarray] = []
    if emb is not None:
        try:
            need_vec = _need_vector(emb, nd)
            false_vecs = [text_embedding(emb, [f]) for f in nd.expected_false if f.strip()]
        except Exception as e:  # no weights / offline: gates still run, relevance is reported as None
            diag["embedder_error"] = str(e)[:200]
            emb, need_vec, false_vecs = None, None, []

    # ---------------------------------------------------------------- sampling (coarse → choose range → fine)
    cf: list[np.ndarray] = []
    if still:
        times = [0.0]
        frames = sample_frames(src, times, probe=mp)
        chosen_in = 0.0
    else:
        n_coarse = max(4, min(g.coarse_samples, math.ceil(dur / g.sample_spacing_s)))
        coarse_t = [dur * (i + 0.5) / n_coarse for i in range(n_coarse)]
        coarse = sample_frames(src, coarse_t, probe=mp)
        ok = [(t, f) for t, f in zip(coarse_t, coarse, strict=True) if f is not None]
        if not ok:
            gl["media"] = {"passed": False, "note": "no frames decoded"}
            diag["rejected_by"] = ["media"]
            diag["score"] = -1.0
            return diag
        ct = [t for t, _ in ok]
        cf = [f for _, f in ok]
        if need_vec is not None:
            cv, _, _ = frame_embeddings(emb, cf)  # type: ignore[arg-type]
            crel = (cv @ need_vec).tolist()
        else:
            crel = [0.0] * len(cf)
        hists = [_hsv_hist(f) for f in cf]
        cut_iv = [_refine_cut(src, mp, ct[i - 1], ct[i], g.shot_change_dist) for i in range(1, len(cf))
                  if _hist_dist(hists[i - 1], hists[i]) > g.shot_change_dist]
        diag["shot_changes_s"] = [[round(a, 3), round(b, 3)] for a, b in cut_iv]
        lumas = [float(f.mean()) for f in cf]
        if in_s is not None:
            chosen_in = float(in_s)
        elif span is not None:
            chosen_in = _choose_range(ct, crel, cut_iv, lumas, span, dur)
        else:
            chosen_in = 0.0
        use_end = min(dur, chosen_in + (span if span is not None else dur))
        use_len = max(0.04, use_end - chosen_in)
        n_fine = max(4, min(g.max_samples, math.ceil(use_len / g.sample_spacing_s) + 1))
        times = [chosen_in + use_len * (i + 0.5) / n_fine for i in range(n_fine)]
        frames = sample_frames(src, times, probe=mp)
    pairs = [(t, f) for t, f in zip(times, frames, strict=True) if f is not None]
    if not pairs:
        gl["media"] = {"passed": False, "note": "no frames decoded in the used range"}
        diag["rejected_by"] = ["media"]
        diag["score"] = -1.0
        return diag
    times = [t for t, _ in pairs]
    frames = [f for _, f in pairs]
    diag["in_s"] = round(chosen_in, 3)
    diag["sample_times"] = [round(t, 3) for t in times]

    # ---------------------------------------------------------------- duration
    if span is not None and not still:
        avail = dur - chosen_in
        gl["duration"] = {"passed": avail + 1e-3 >= span, "value": round(avail, 3), "threshold": round(span, 3),
                          "note": "source seconds available from the in-point vs needed (duration x speed)"}

    # ---------------------------------------------------------------- letterbox / geometry
    active = detect_letterbox(frames) if not still and len(frames) >= 3 else (0.0, 0.0, 1.0, 1.0)
    diag["letterbox"] = {"active": [round(v, 4) for v in active],
                         "bars": active != (0.0, 0.0, 1.0, 1.0)}
    fh, fw = frames[0].shape[:2]
    src_w = c.width or mp.width
    src_h = c.height or mp.height
    src_aspect = (src_w * (active[2] - active[0])) / max(1, src_h * (active[3] - active[1]))
    aspect = mode_aspect(nd.mode, split_ratio=nd.split_ratio, source_aspect=src_aspect)
    out_w, out_h = mode_size(nd.mode, split_ratio=nd.split_ratio, source_aspect=src_aspect)

    # ---------------------------------------------------------------- faces
    faces = [detect_faces(f) for f in frames]
    n_faces = max((len(x) for x in faces), default=0)
    face_h = max((fc[3] for x in faces for fc in x), default=0.0)
    recognisable = face_h >= g.face_recognisable_h
    diag["faces"] = {"max_count": n_faces, "max_height_frac": round(face_h, 4), "recognisable": recognisable,
                     "frames_with_faces": sum(1 for x in faces if x)}
    if not nd.allow_faces:
        gl["faces"] = {"passed": not recognisable, "value": round(face_h, 4), "threshold": g.face_recognisable_h,
                       "note": "recognisable stranger not allowed under this narration"}

    # ---------------------------------------------------------------- relevance (full frame + tiles)
    rel_full = None
    frame_vecs = None
    if need_vec is not None:
        frame_vecs, _, _ = frame_embeddings(emb, frames)  # type: ignore[arg-type]
        sims = frame_vecs @ need_vec
        rel_full = float(0.75 * sims.mean() + 0.25 * sims.min())

    # ---------------------------------------------------------------- crop plan
    semantic = None
    if need_vec is not None and nd.mode != "pip":
        picks = [frames[0], frames[len(frames) // 2], frames[-1]] if len(frames) >= 3 else list(frames)
        semantic = _safe(lambda: semantic_positions(emb, picks, need_vec, aspect, active), None)  # type: ignore[arg-type]
    plan = plan_crop(frames, times, aspect, active=active, faces=faces, semantic=semantic,
                     source_size=(int(src_w), int(src_h))) if nd.mode != "pip" else \
        CropPlan(aspect=aspect, active=active, window=(active[2] - active[0], active[3] - active[1]),
                 path=[(times[0], (active[0] + active[2]) / 2, (active[1] + active[3]) / 2)],
                 source_size=(int(src_w), int(src_h)))
    diag["crop"] = plan.to_dict()
    crops = [crop_frame(f, plan.rect_at(t)) for f, t in zip(frames, times, strict=True)]
    rel_crop = rel_full
    p_need = None
    if need_vec is not None:
        cvecs, _, _ = frame_embeddings(emb, crops)  # type: ignore[arg-type]
        csims = cvecs @ need_vec
        rel_crop = float(0.75 * csims.mean() + 0.25 * csims.min())
        if false_vecs:
            ls = float(getattr(emb, "logit_scale", 100.0))
            mat = np.stack([need_vec, *false_vecs])
            logits = ls * (cvecs @ mat.T)
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            p_need = float(p[:, 0].mean())
    diag["relevance"] = {"full": None if rel_full is None else round(rel_full, 4),
                         "crop": None if rel_crop is None else round(rel_crop, 4),
                         "p_need_vs_lookalikes": None if p_need is None else round(p_need, 4)}
    if nd.mode != "pip":
        keep = None if (rel_full is None or rel_crop is None or rel_full <= 0) else rel_crop / rel_full
        if keep is not None:  # semantic check: the crop keeps what the need names
            passed = keep >= g.min_crop_keep and not plan.face_cut
            value, thr, basis = keep, g.min_crop_keep, "crop relevance / full-frame relevance"
        else:  # no embedder: saliency coverage
            passed = plan.coverage >= g.min_crop_coverage and not plan.face_cut
            value, thr, basis = plan.coverage, g.min_crop_coverage, "top-saliency mass inside the window"
        gl["crop"] = {"passed": passed, "value": round(value, 4), "threshold": thr, "basis": basis,
                      "coverage": round(plan.coverage, 4), "face_cut": plan.face_cut,
                      "note": "subject must stay inside the window; else use split/PiP"}

    # ---------------------------------------------------------------- resolution after the crop
    x, y, ww, wh = plan.rect_at(times[0])
    crop_px_w = ww * src_w
    crop_px_h = wh * src_h
    upscale = max(out_w / max(1.0, crop_px_w), out_h / max(1.0, crop_px_h))
    lim = g.own_upscale_max if creator else g.upscale_max
    gl["resolution"] = {"passed": upscale <= lim + 1e-6, "value": round(upscale, 3), "threshold": lim,
                        "note": f"{round(crop_px_w)}x{round(crop_px_h)} source px → {out_w}x{out_h}"}

    # ---------------------------------------------------------------- sharpness (on the crop)
    sh_vals = [sharpness(_resize_short(cr, min(ANALYSIS_SHORT, min(cr.shape[:2])))) for cr in crops]
    sh = float(np.median(sh_vals)) if sh_vals else 0.0
    slim = g.min_sharpness_own if creator else g.min_sharpness
    gl["sharpness"] = {"passed": sh >= slim, "value": round(sh, 2), "threshold": slim,
                       "note": "p90 tile Laplacian std of the cropped frames at 540 px"}

    # ---------------------------------------------------------------- shot changes in the used range
    if not still and len(frames) > 1:
        hists = [_hsv_hist(f) for f in frames]
        dists = [_hist_dist(hists[i - 1], hists[i]) for i in range(1, len(frames))]
        n_cuts = sum(1 for d in dists if d > g.shot_change_dist)
        gl["shot_change"] = {"passed": n_cuts == 0, "value": n_cuts, "threshold": 0,
                             "max_dist": round(max(dists), 3) if dists else 0.0}

    # ---------------------------------------------------------------- text / watermark
    ocr_frames = [crops[0], crops[len(crops) // 2], crops[-1]] if len(crops) >= 3 else crops
    ocr_hits: list[dict[str, Any]] = []
    text_frac = 0.0
    backend = "none"
    if use_ocr:
        for of in ocr_frames:
            big = _resize_short(of, 720) if min(of.shape[:2]) > 720 else of
            res = ocr_text(big)
            if res is None:
                boxes = text_regions(big)
                backend = "mser"
                text_frac = max(text_frac, sum(b[2] * b[3] for b in boxes))
                continue
            backend = ocr_backend()
            area = 0.0
            for txt, conf, (bx, by, bw, bh) in res:
                if conf < 0.5 or bh < g.min_text_height:
                    continue
                area += bw * bh
                ocr_hits.append({"text": txt[:60], "conf": round(conf, 2), "box": [round(bx, 3), round(by, 3),
                                                                                   round(bw, 3), round(bh, 3)]})
            text_frac = max(text_frac, area)
    texts = " ".join(h["text"] for h in ocr_hits).lower()
    vendor = sorted({t for t in WATERMARK_TERMS if t in texts})
    platform = sorted({t for t in PLATFORM_TERMS if re.search(rf"\b{re.escape(t)}\b", texts)})
    handles = sorted({m.strip() for m in _HANDLE_RE.findall(" ".join(h["text"] for h in ocr_hits))})
    # the overlay check needs ≥ 7 frames: use the coarse whole-clip samples (a watermark spans the clip)
    ov_frames = cf if (not still and len(cf) >= 7) else frames
    overlay = static_overlay(ov_frames) if not still else {"conclusive": False, "overlay_frac": 0.0, "boxes": []}
    diag["text"] = {"backend": backend, "text_frac": round(text_frac, 5), "ocr": ocr_hits[:12],
                    "watermark_terms": vendor, "platform_terms": platform, "handles": handles[:5],
                    "static_overlay": overlay}
    wm = bool(vendor) or bool(handles) or (bool(platform) and not allow_text)
    gl["watermark"] = {"passed": not wm, "value": vendor + handles + (platform if not allow_text else []),
                       "note": "stock/platform watermark or @handle read by OCR"}
    # a burned-in overlay found by motion analysis is a strong hint, not proof (a tracked subject or a car
    # dashboard is also static in frame coordinates): it is flagged for the judge and costs score
    overlay_suspected = bool(overlay.get("conclusive") and overlay.get("overlay_frac", 0.0) > g.max_overlay_frac
                             and not allow_text)
    if not allow_text:
        def _letters(t: str) -> int:
            return sum(ch.isalpha() for ch in t)

        headlines = list(dict.fromkeys(
            h["text"] for h in ocr_hits
            if (h["box"][3] >= g.headline_h and h["box"][2] >= g.headline_w and _letters(h["text"]) >= 3)
            or (h["box"][3] >= g.legible_h and h["box"][2] >= g.legible_w
                and _letters(h["text"]) >= g.legible_letters)))
        gl["text"] = {"passed": text_frac < g.max_text_frac and not headlines, "value": round(text_frac, 5),
                      "threshold": g.max_text_frac, "headlines": headlines[:5],
                      "note": "prominent text in stock (brand/trademark, competes with captions); small scene "
                              "text such as key legends is reported but allowed"}

    # ---------------------------------------------------------------- colour (the A-roll's camera world)
    color_d, grade_gate = _safe(lambda: _color_fit(c, crops, ref_stats, g), ({"error": "colour stats failed"}, None))
    diag["color"] = color_d
    if grade_gate is not None:
        gl["grade"] = grade_gate
    monochrome = bool(color_d.get("monochrome")) and not bool(color_d.get("aroll_monochrome"))

    # ---------------------------------------------------------------- soft factors
    motion = 0.0
    if not still and len(frames) > 1:
        import cv2

        gs = [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32) for f in frames]
        motion = float(np.mean([np.abs(gs[i] - gs[i - 1]).mean() for i in range(1, len(gs))]) / 255.0)
    src_fps = c.fps or mp.fps or 0.0
    out_fps = nd.output_fps or 0.0
    eff_fps = src_fps * float(nd.speed or 1.0)  # new pictures per output second (slow motion lowers it)
    cadence_risk = bool(not still and out_fps and src_fps and motion > 0.02
                        and ((src_fps < g.min_fps_for_motion and out_fps > src_fps * 1.5)
                             or (nd.speed < 1.0 and eff_fps < out_fps * 0.98)))
    subj = _subject_frac(crops[len(crops) // 2], faces[len(faces) // 2] if faces else ())
    diag["soft"] = {"fps": src_fps or None, "effective_fps": round(eff_fps, 3) or None, "cadence_risk": cadence_risk,
                    "motion": round(motion, 4),
                    "overlay_suspected": overlay_suspected, "monochrome": monochrome,
                    "subject_frac": round(subj, 3), "orientation": c.orientation,
                    "native_portrait": bool(src_h > src_w)}

    rejected = [k for k, v in gl.items() if not v.get("passed", True)]
    diag["rejected_by"] = rejected
    quality = min(1.0, sh / (slim * 3)) * 0.5 + min(1.0, subj / g.subject_min_frac) * 0.3 + \
        (0.0 if cadence_risk else 0.2)
    base = rel_crop if rel_crop is not None else 0.0
    penalty = (0.0 if p_need is None else max(0.0, 0.5 - p_need) * 0.1) + (0.05 if overlay_suspected else 0.0) + \
        (0.02 if monochrome else 0.0)
    diag["quality"] = round(quality, 4)
    diag["score"] = round(base * (0.85 + 0.15 * quality) - penalty, 5) if not rejected else round(base - 1.0, 5)
    c.meta["in_ms"] = round(chosen_in * 1000)
    c.meta["use_ms"] = [c.meta["in_ms"], round((chosen_in + span) * 1000) if span is not None and not still else None]
    c.meta["crop"] = diag["crop"]
    c.meta["analysis"] = {k: diag[k] for k in ("gates", "rejected_by", "score", "relevance", "faces", "text",
                                                "soft", "letterbox", "color", "in_s") if k in diag}
    if job is not None:
        with contextlib.suppress(Exception):
            job.save_json(f"assets/broll/analysis/{c.id}.json", {**diag, "need": nd.__dict__ | {
                "queries": list(nd.queries), "expected_false": list(nd.expected_false)}})
        _register_use_range(c, job)
    return diag


def _register_use_range(c: BrollCandidate, job: Job) -> None:
    """Write the code-chosen use range onto an already-registered asset (ops copy ``in_ms`` from the registry,
    and a model never supplies an in-point: invariant 3). Assets downloaded later get it in ``download``."""
    if c.asset is None or not c.asset.id or c.is_still:
        return
    in_ms, out_ms = (c.meta.get("use_ms") or [c.meta.get("in_ms", 0), None])[:2]
    with contextlib.suppress(Exception):
        c.asset = job.register_asset(c.asset.model_copy(update={"in_ms": int(in_ms or 0), "out_ms": out_ms}),
                                     asset_id=c.asset.id, overwrite=True)


def _subject_frac(frame: np.ndarray, faces: Sequence[tuple[float, ...]]) -> float:
    """Share of the frame covered by the salient subject (bounding box of the top-saliency region)."""
    import cv2

    sal = saliency_map(frame, faces, size=64)
    thr = sal.max() * 0.5
    m = (sal >= thr).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return 0.0
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in stats[i][:4])
    return float((w * h) / (sal.shape[0] * sal.shape[1]))


def rank_candidates(candidates: Sequence[BrollCandidate], *, need: Need | str, job: Job | None = None,
                    top_k: int = 20, embedder: Embedder | None = None, gates: GateConfig | None = None,
                    include_rejected: bool = False, mode: str | None = None, duration_s: float | None = None,
                    expected_false: Sequence[str] | None = None, use_ocr: bool = True, recall: bool = True,
                    workers: int = 4, a_roll_reference: Any = None
                    ) -> list[tuple[BrollCandidate, float, dict[str, Any]]]:
    """Recall (embedding, top ``top_k`` by rank) then gates on the survivors; ``[(candidate, score, diag)]``
    best first. Rejected candidates are dropped unless ``include_rejected`` (they sort last, with
    ``diag["rejected_by"]``). An empty list is a valid outcome: nothing cleared the bar.
    ``a_roll_reference`` (job / mezzanine path / colour stats) enables the grade-match gate."""
    nd = Need.coerce(need, mode=mode, duration_s=duration_s,
                     expected_false=tuple(expected_false) if expected_false is not None else None)
    ref_stats = _ref_stats(a_roll_reference)
    emb = embedder
    if recall and len(candidates) > top_k:
        if emb is None:
            emb = get_embedder()
        shortlisted = [c for c, _, _ in retrieve(candidates, nd, top_k=top_k, embedder=emb)]
    else:
        shortlisted = list(candidates)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        diags = list(ex.map(lambda c: _safe(lambda: analyze_candidate(c, nd, embedder=emb, gates=gates,
                                                                      use_ocr=use_ocr, job=job,
                                                                      a_roll_reference=ref_stats),
                                            {"id": c.id, "rejected_by": ["analysis_error"], "score": -2.0,
                                             "gates": {}}), shortlisted))
    out = [(c, float(d.get("score", -2.0)), d) for c, d in zip(shortlisted, diags, strict=True)]
    out.sort(key=lambda r: (bool(r[2].get("rejected_by")), -r[1]))
    if not include_rejected:
        out = [r for r in out if not r[2].get("rejected_by")]
    return out


# ============================================================================================ judgment sheet
RUBRIC_ITEMS: tuple[tuple[str, str], ...] = (
    ("subject", "shows the specific thing the line is about (not a look-alike or generic stand-in)"),
    ("readable", "subject is large and clear enough to read at a glance on a phone (≥ ⅓ of the frame) and sits "
                 "near where the eye already was (the face position: eye-trace across the cut)"),
    ("clean", "no text, watermark, logo, platform UI or person shown in a bad/negative context"),
    ("look", "fits the creator's camera world (light, colour, energy); not staged/cheesy stock"),
    ("sharp", "sharp and stable at phone size across all frames (no blur, warping, judder)"),
)


@dataclass
class JudgmentSheet:
    """Contact sheet(s) for a thresholded judgment: PNG ``paths`` (≤ 4 candidates per page), the candidate
    IDs in display order, the rubric ``prompt`` and the acceptance ``threshold`` (every item ≥ it)."""

    paths: list[Path]
    candidate_ids: list[str]
    prompt: str
    threshold: int = 4
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Judgment:
    pick: str | None
    accepted: list[str]
    scores: dict[str, dict[str, int]]
    reason: str = ""
    overridden: bool = False
    errors: list[str] = field(default_factory=list)


_FONT_CANDIDATES = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Menlo.ttc",
                    "/System/Library/Fonts/SFNSMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
_font_cache: dict[int, Any] = {}


def _font(size: int) -> Any:
    from PIL import ImageFont

    f = _font_cache.get(size)
    if f is None:
        for p in _FONT_CANDIDATES:
            if os.path.exists(p):
                with contextlib.suppress(OSError):
                    f = ImageFont.truetype(p, size)
                    break
        if f is None:
            f = ImageFont.load_default(size=size)
        _font_cache[size] = f
    return f


def _fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def contact_sheet_for_judgment(candidates: Sequence[BrollCandidate | tuple[BrollCandidate, float, dict[str, Any]]],
                               *, need: Need | str | None = None, out_path: str | os.PathLike[str] | None = None,
                               job: Job | None = None, frames_per_candidate: int = 5, threshold: int = 4,
                               max_per_page: int = 4, thumb_h: int = 300, order: str = "ranked",
                               seed: int = 0, context: str = "", a_roll_reference: Any = None,
                               a_roll_context: Sequence[Any] | None = None) -> JudgmentSheet:
    """Render candidates as labelled rows of frames **at the final crop** across the used range.

    Each row: an ID panel (candidate ID large, source/size/fps, gate notes) + ``frames_per_candidate``
    thumbnails with the ID and source timecode burned in. Pages hold ≤ ``max_per_page`` rows so the image
    stays legible under a model's ~1568 px downsampling. ``order="shuffled"`` randomises row order
    (position bias). The returned prompt asks for an independent 1–5 score per rubric item per
    candidate and allows ``"none"``; :func:`parse_judgment` enforces the bar in code.

    Judged **as conformed** (doctrine: "conform, then judge"): with ``a_roll_reference`` (job, mezzanine
    path, :class:`~studio.broll.conform.ARollReference` or colour stats) every thumbnail carries the exact
    partial-strength grade match the conform will apply. ``a_roll_context`` = ``(before, after)`` A-roll
    frames (RGB arrays or ``(path, t_s)``) framed as they will play; each row is bracketed by them, so the
    judge sees the cut into and out of the insert (look, size and position against the face). ``need``
    defaults to the first candidate's query/description.
    """
    from PIL import Image, ImageDraw

    rows: list[tuple[BrollCandidate, dict[str, Any]]] = []
    for item in candidates:
        if isinstance(item, tuple):
            rows.append((item[0], item[2] if len(item) > 2 else {}))
        else:
            rows.append((item, dict(item.meta.get("analysis") or {})))
    if need is None:
        first = rows[0][0] if rows else None
        need = (first.query or first.description) if first is not None else ""
        need = need or "b-roll insert"
    nd = Need.coerce(need)
    if order == "shuffled":
        import random

        random.Random(seed).shuffle(rows)
    ids = [c.id for c, _ in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate candidate ids on a judgment sheet")
    ref_stats = _ref_stats(a_roll_reference)
    ctx = _context_frames(a_roll_context)
    if out_path is None:
        base = (job.critique_dir / "broll") if job is not None else Path(os.environ.get("TMPDIR", "/tmp"))
        base.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(("|".join(ids) + nd.text).encode()).hexdigest()[:10]
        out_path = base / f"broll_sheet_{key}.png"
    out_path = Path(out_path)
    pages = [rows[i:i + max_per_page] for i in range(0, len(rows), max_per_page)] or [[]]
    paths = []
    layout: list[dict[str, Any]] = []
    grades: dict[str, Any] = {}
    n = max(1, frames_per_candidate)
    label_w, gut, head_h, sep = 250, 8, 70, 10
    k = len(ctx)
    for pi, page in enumerate(pages):
        tiles_rows = []
        for c, d in page:
            frs, grade = _judgment_frames(c, d, nd, n, ref_stats)
            tiles_rows.append((c, d, frs))
            if grade is not None:
                grades[c.id] = grade
        aspect = mode_aspect(nd.mode, split_ratio=nd.split_ratio,
                             source_aspect=(tiles_rows[0][0].aspect if tiles_rows and nd.mode == "pip" else None))
        # one row height for the page: candidate tiles at the mode aspect, A-roll tiles at 9:16, ≤ 1600 px wide
        fixed = label_w + gut + (n + k) * gut + (sep * 2 if k else 0)
        th = int(min(thumb_h, (1600 - fixed) / (n * aspect + k * 9 / 16)))
        tw, aw = max(24, round(th * aspect)), round(th * 9 / 16)
        W = fixed + n * tw + k * aw
        H = head_h + len(tiles_rows) * (th + gut) + gut
        img = Image.new("RGB", (W, max(H, head_h + 40)), (18, 18, 22))
        dr = ImageDraw.Draw(img)
        dr.text((gut, 6), f"B-ROLL CANDIDATES for: {nd.text}"[:150], font=_font(22), fill=(240, 240, 240))
        sub = f"mode={nd.mode}  page {pi + 1}/{len(pages)}  judge each row alone; 'none' is valid"
        if ref_stats is not None:
            sub += "  |  graded to match the A-roll"
        if k:
            sub += "  |  grey tiles = A-ROLL either side of the cut"
        if nd.expected_false:
            sub += f"  |  wrong if: {', '.join(nd.expected_false)}"
        dr.text((gut, 36), sub[:200], font=_font(15), fill=(170, 170, 180))
        for ri, (c, d, frs) in enumerate(tiles_rows):
            y0 = head_h + gut + ri * (th + gut)
            dr.rectangle([gut, y0, label_w - 4, y0 + th - 1], fill=(34, 34, 42))
            dr.text((gut + 8, y0 + 6), c.id, font=_font(24), fill=(255, 214, 0))
            lines = _label_lines(c, d, grades.get(c.id))
            for li, ln in enumerate(lines):
                if 40 + li * 19 + 16 > th:
                    break
                dr.text((gut + 8, y0 + 40 + li * 19), ln[:30], font=_font(14), fill=(200, 200, 210))
            x = label_w
            ctx_rects = []
            if k:
                x = _paste_context(img, dr, ctx[0], x, y0, aw, th, "A-ROLL before")
                ctx_rects.append([x - aw - gut, y0, aw, th])
                x += sep
            rects = []
            for t, fr in frs:
                if fr is None:
                    dr.rectangle([x, y0, x + tw - 1, y0 + th - 1], fill=(70, 20, 20))
                else:
                    img.paste(Image.fromarray(fr).resize((tw, th), Image.Resampling.LANCZOS), (x, y0))
                tag = f"{c.id} {_fmt_t(t)}"
                dr.rectangle([x, y0 + th - 20, x + min(tw, 8 + 8 * len(tag)), y0 + th - 1], fill=(0, 0, 0))
                dr.text((x + 4, y0 + th - 18), tag, font=_font(13), fill=(255, 255, 255))
                rects.append([x, y0, tw, th])
                x += tw + gut
            if k > 1:
                x += sep
                _paste_context(img, dr, ctx[1], x, y0, aw, th, "A-ROLL after")
                ctx_rects.append([x, y0, aw, th])
            layout.append({"id": c.id, "page": pi, "row": ri, "rects": rects, "aroll_rects": ctx_rects,
                           "times": [round(t, 3) for t, _ in frs]})
        p = out_path if len(pages) == 1 else out_path.with_name(f"{out_path.stem}_p{pi + 1}{out_path.suffix}")
        _save_png(img, p, {"kind": "broll_judgment", "need": nd.text, "ids": [c.id for c, _, _ in tiles_rows],
                           "graded": ref_stats is not None, "aroll_context": k,
                           "layout": [x for x in layout if x["page"] == pi]})
        paths.append(p)
    prompt = _judgment_prompt(nd, ids, threshold, context, graded=ref_stats is not None, aroll_context=k > 0)
    return JudgmentSheet(paths=paths, candidate_ids=ids, prompt=prompt, threshold=threshold,
                         meta={"layout": layout, "mode": nd.mode, "grades": grades})


def _context_frames(ctx: Sequence[Any] | None) -> list[np.ndarray]:
    """Decode ``(before, after)`` A-roll frames (arrays or ``(path, t_s)``) and frame them 9:16 (a face-centred
    crop when the frame is wider)."""
    out: list[np.ndarray] = []
    for item in list(ctx or [])[:2]:
        fr: np.ndarray | None
        if isinstance(item, np.ndarray):
            fr = item[..., :3]
        else:
            path, t = item
            fr = sample_frames(str(path), [float(t)], short_side=720)[0]
        if fr is None:
            continue
        h, w = fr.shape[:2]
        if w / h > 9 / 16 * 1.02:
            cw = round(h * 9 / 16)
            faces = detect_faces(fr)
            cx = (faces[0][0] + faces[0][2] / 2) * w if faces else w / 2
            x0 = int(min(max(0, round(cx - cw / 2)), w - cw))
            fr = fr[:, x0:x0 + cw]
        out.append(np.ascontiguousarray(fr))
    return out


def _paste_context(img: Any, dr: Any, fr: np.ndarray, x: int, y: int, w: int, h: int, tag: str) -> int:
    """Paste an A-roll context tile (grey frame + tag) at ``x``; returns the x after it and its gutter."""
    from PIL import Image

    tile = Image.fromarray(fr).resize((w, h), Image.Resampling.LANCZOS)
    img.paste(tile, (x, y))
    dr.rectangle([x, y, x + w - 1, y + h - 1], outline=(150, 150, 150), width=3)
    dr.rectangle([x, y + h - 20, x + min(w, 8 + 8 * len(tag)), y + h - 1], fill=(90, 90, 90))
    dr.text((x + 4, y + h - 18), tag, font=_font(13), fill=(255, 255, 255))
    return x + w + 8


def _label_lines(c: BrollCandidate, d: Mapping[str, Any], grade: Mapping[str, Any] | None = None) -> list[str]:
    lines = [f"{c.source} {c.kind}"]
    if c.width and c.height:
        lines.append(f"{c.width}x{c.height}" + (f" {c.fps:g}fps" if c.fps else ""))
    if c.duration_ms:
        lines.append(f"{c.duration_ms / 1000:.1f}s  in {d.get('in_s', 0):.2f}s")
    fc = (d.get("faces") or {})
    if fc.get("max_count"):
        lines.append(f"faces {fc['max_count']}" + (" (recognisable)" if fc.get("recognisable") else ""))
    tx = (d.get("text") or {})
    if tx.get("text_frac"):
        words = list(dict.fromkeys(h.get("text", "") for h in tx.get("ocr") or [] if h.get("text")))
        lines.append(f"text {tx['text_frac'] * 100:.2f}%" + (f' "{" / ".join(words)[:18]}"' if words else ""))
    soft = d.get("soft") or {}
    if soft.get("overlay_suspected"):
        lines.append("OVERLAY? check frames")
    if soft.get("cadence_risk"):
        lines.append("low fps: judder risk")
    if soft.get("monochrome") or (d.get("color") or {}).get("monochrome"):
        lines.append("black & white")
    de = (grade or {}).get("neutral_delta_e_after")
    if de is None:
        de = (d.get("color") or {}).get("neutral_delta_e_after")
    if de is not None:
        lines.append(f"grade ΔE {de:.1f}" + (" (too far)" if de > 5.0 else ""))
    if c.meta.get("disclosure_required"):
        lines.append("PHOTOREAL AI: disclose")
    elif c.meta.get("ai_generated"):
        lines.append("AI still (stylized)")
    rej = d.get("rejected_by") or []
    if rej:
        lines.append("GATE FAIL: " + ",".join(rej))
    return lines[:10]


def _judgment_frames(c: BrollCandidate, d: Mapping[str, Any], nd: Need, n: int, ref_stats: Any = None
                     ) -> tuple[list[tuple[float, np.ndarray | None]], dict[str, Any] | None]:
    """``(frames, grade)``: ``n`` frames across the used range at the final crop (stills show the Ken Burns
    push), grade-matched to ``ref_stats`` like the conform when given (``grade`` = the transfer used)."""
    src = _analysis_source(c)
    if not src:
        return [(0.0, None)] * n, None
    try:
        mp = probe_media(src)
    except Exception:
        return [(0.0, None)] * n, None
    in_s = float(d.get("in_s", c.meta.get("in_ms", 0) / 1000 if c.meta.get("in_ms") else 0.0))
    if mp.is_image or c.is_still:
        ts = [0.0] * n
    else:
        span = nd.source_span_s or max(0.1, mp.duration_s - in_s)
        span = min(span, max(0.1, mp.duration_s - in_s))
        ts = [in_s + span * (i + 0.5) / n for i in range(n)]
    frames = sample_frames(src, ts, short_side=720, probe=mp)
    crop = d.get("crop") or c.meta.get("crop")
    plan = CropPlan.from_dict(crop) if crop else None
    out: list[tuple[float, np.ndarray | None]] = []
    for i, (t, f) in enumerate(zip(ts, frames, strict=True)):
        if f is None:
            out.append((t, None))
            continue
        if plan is not None:
            if mp.is_image or c.is_still:
                # show the Ken Burns push the conform will apply: from the full window to a 7 % tighter one
                x, y, w, h = plan.rect_at(0.0)
                z = 1.0 + 0.07 * (i / max(1, n - 1))
                cx, cy = x + w / 2, y + h / 2
                f = crop_frame(f, (cx - w / z / 2, cy - h / z / 2, w / z, h / z))
            else:
                f = crop_frame(f, plan.rect_at(t))
        out.append((t, f))
    grade = None
    if ref_stats is not None:
        grade = _safe(lambda: _grade_preview(c, out, ref_stats), None)
    return out, grade


def _grade_preview(c: BrollCandidate, frames: list[tuple[float, np.ndarray | None]], ref: Any) -> dict[str, Any]:
    """Apply (in place) the conform's grade match to the sheet frames; returns the transfer + neutral ΔE."""
    from studio.broll.conform import DEFAULT_STRENGTH, ColorStats, ColorTransfer, neutral_delta_e

    strength = DEFAULT_STRENGTH.get(c.kind, 0.5)
    fl = [f for _, f in frames if f is not None]
    if not fl or strength <= 0:
        return {"strength": strength, "transfer": None, "neutral_delta_e_after": None}
    st = ColorStats.from_frames(fl)
    t = ColorTransfer.between(st, ref, strength)
    de = neutral_delta_e(fl, ref, t)
    if not t.is_identity:
        for i, (ts, f) in enumerate(frames):
            if f is not None:
                g = t.apply(f.astype(np.float32) / 255.0)
                frames[i] = (ts, (np.clip(g, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
    return {"strength": strength, "transfer": t.to_dict(), "monochrome": st.monochrome,
            "neutral_delta_e_after": None if de is None else round(de, 2)}


def _judgment_prompt(nd: Need, ids: Sequence[str], threshold: int, context: str, *, graded: bool = False,
                     aroll_context: bool = False) -> str:
    items = "\n".join(f"- {k}: {v}" for k, v in RUBRIC_ITEMS)
    ef = (f"\nLook-alikes that are WRONG (score subject ≤ 2 if shown instead): {', '.join(nd.expected_false)}."
          if nd.expected_false else "")
    ctx = f"\nContext: {context}" if context else ""
    seen = ("frames are shown at the final crop" + (", already grade-matched to the A-roll," if graded else "")
            + " across the range that would be used")
    arl = (" The grey-framed tiles at each end of a row are the creator's A-roll just before and just after the "
           "insert: judge the cut into and out of it (look, energy, where the eye lands) — they are not candidates."
           if aroll_context else "")
    return (
        f"The contact sheet shows b-roll candidates for this insert ({nd.mode}, "
        f"{f'{nd.duration_s:.1f} s' if nd.duration_s else 'duration tbd'}): \"{nd.text}\".{ef}{ctx}\n"
        f"Each row is one candidate (its ID is in yellow and burned on every frame); {seen}.{arl} "
        "Judge EACH candidate on its own against the bar, not against the others. Score every item 1–5:\n"
        f"{items}\n"
        f"A candidate is acceptable only if EVERY item is ≥ {threshold}. If none is acceptable, answer "
        "\"none\": no insert (punch-in or the face) is often the right edit. Never pick the best of a weak set.\n"
        "Answer with JSON only: {\"scores\": {\"<id>\": {\"subject\": n, \"readable\": n, \"clean\": n, "
        "\"look\": n, \"sharp\": n}, ...}, \"pick\": \"<id>\" | \"none\", \"reason\": \"one line\"}\n"
        f"Candidate IDs: {', '.join(ids)}"
    )


def parse_judgment(answer: str | Mapping[str, Any], sheet: JudgmentSheet | Sequence[str], *,
                   threshold: int | None = None) -> Judgment:
    """Validate a judge's answer against the sheet and enforce the bar in code.

    Unknown IDs are ignored (reported in ``errors``); a candidate is *accepted* only when every rubric item
    is present and ≥ threshold. The pick is kept only if it is accepted; a pick below the bar (or not on
    the sheet) is overridden to ``None`` (``overridden``), never silently swapped for another candidate —
    ``accepted`` lists what did clear the bar so the Director can decide. "none" is always respected."""
    ids = list(sheet.candidate_ids if isinstance(sheet, JudgmentSheet) else sheet)
    thr = threshold if threshold is not None else (sheet.threshold if isinstance(sheet, JudgmentSheet) else 4)
    errors: list[str] = []
    data: Mapping[str, Any]
    if isinstance(answer, str):
        m = re.search(r"\{.*\}", answer, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            data = {}
            errors.append("answer is not valid JSON")
    else:
        data = answer
    raw_scores = data.get("scores") or {}
    scores: dict[str, dict[str, int]] = {}
    for cid, sc in raw_scores.items() if isinstance(raw_scores, Mapping) else []:
        if cid not in ids:
            errors.append(f"unknown candidate id {cid!r}")
            continue
        if not isinstance(sc, Mapping):
            errors.append(f"{cid}: scores must be an object")
            continue
        clean: dict[str, int] = {}
        for k, _ in RUBRIC_ITEMS:
            v = sc.get(k)
            try:
                clean[k] = round(float(v))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                errors.append(f"{cid}: missing/invalid '{k}'")
        scores[cid] = clean
    accepted = [cid for cid in ids if cid in scores and len(scores[cid]) == len(RUBRIC_ITEMS)
                and min(scores[cid].values()) >= thr]
    pick_raw = data.get("pick")
    pick = None if pick_raw in (None, "", "none", "None", "null") else str(pick_raw)
    overridden = False
    if pick is not None and pick not in accepted:
        overridden = True
        errors.append(f"pick {pick!r} is not on the sheet" if pick not in ids
                      else f"pick {pick!r} is below the bar (every item must be >= {thr}); overridden to none")
        pick = None
    return Judgment(pick=pick, accepted=accepted, scores=scores, reason=str(data.get("reason") or "")[:500],
                    overridden=overridden, errors=errors)


def _save_png(img: Any, path: Path, meta: Mapping[str, Any]) -> Path:
    from PIL.PngImagePlugin import PngInfo

    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    info.add_text("studio", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    tmp = path.with_name(f".{path.name}.tmp.png")
    img.save(tmp, format="PNG", compress_level=6, pnginfo=info)
    os.replace(tmp, path)
    return path


def read_sheet_meta(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Layout metadata stored in a judgment sheet PNG."""
    from PIL import Image

    with Image.open(path) as im:
        raw = getattr(im, "text", {}).get("studio") or im.info.get("studio")
    return json.loads(raw) if raw else {}
