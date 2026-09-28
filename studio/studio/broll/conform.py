"""B-roll conform: make an insert look and move like it was shot in the creator's camera world.

``conform(asset, insert_mode, duration, fps, a_roll_reference) -> Path`` writes a **ProRes 422 HQ**
(10-bit 4:2:2, BT.709 tv-range, tagged; the same intermediate as the A-roll mezzanine) clip that the
renderer layers as-is: the exact layer size for the insert mode, CFR at the timeline rate, the used range
plus optional handles, stock audio muted (the voice never changes). A JSON sidecar records the recipe.

Pipeline (quality decisions)
----------------------------
1. **Decode through the mezzanine chain** (:func:`studio.media.ingest.mezz_video_filter`): the same
   VFR→CFR conform and the same HLG/PQ tone map (zscale + Möbius, 203-nit reference white) as the A-roll,
   so an HDR creator clip and the A-roll land on one transfer function — tone-mapped exactly once.
2. **Cadence**: ``setpts`` for speed, then ``fps=<timeline>:round=near`` — frame repeat/drop, never
   blending (doctrine: native cadence by repeating frames; interpolation only when gated, and RIFE is not
   available here). The rate of new pictures is ``source fps x speed``: non-integer ratios (24→60) are
   reported as ``uneven`` and slow motion from a low-rate source as ``slowmo_stutter`` (both warn). An
   animated GIF (a reaction loop) is played with ``-stream_loop`` and never runs out of source.
3. **Geometry**: letterbox bars are removed, then a cover crop of the mode's aspect (full 9:16, split
   halves 1080x960 at ratio 0.5, PiP keeps the source aspect; a caller-given layer size is always matched
   *exactly* — the window is trimmed to its pixel aspect, never squeezed) placed by the subject-aware
   :class:`~studio.broll.rank.CropPlan` (from ranking, or recomputed from saliency + faces + CLIP window
   relevance). Static crops are cut and scaled once by ffmpeg (Lanczos, 16-bit RGB). Moving windows (a
   slow pan that follows the subject, a Ken Burns push, a screenshot scroll) are rendered in Python with
   ``cv2.warpAffine`` (Lanczos-4, float32) per frame: **sub-pixel window positions**, so motion is smooth
   (ffmpeg's integer ``crop``/``zoompan`` steps visibly jitter on slow pushes). The source is pre-cropped
   to the union of windows and pre-scaled (Lanczos) so the per-frame warp never minifies by more than the
   push factor (no aliasing).
4. **Stills** get a subtle Ken Burns push (default 7 %, the doctrine's 5–10 %) toward the salient point,
   zoom interpolated **geometrically** (constant perceived speed) and running through the handles at the
   same rate; full-page screenshots (≥ 2.2:1 tall) scroll top→down instead, eased and capped at 0.6 window
   heights per second so the lines stay trackable; a screen-sized capture cropped for a split holds with a
   push. Stills are decoded with EXIF orientation and their ICC profile converted to sRGB (iPhone P3 photos
   otherwise look flat; HEIC via pillow-heif), and prescaled by area-averaging in 16 bits (no banding).
5. **Grade match to the A-roll** (``a_roll_reference``): CIE-Lab statistics, **exposure, white balance and
   contrast only** (never saturation, never a look), at partial strength (default 0.5 =
   ``broll.grade_transfer_max_strength``): L mean/std transfer about the clip's own mean; the white-balance
   estimate uses near-neutral pixels (chroma < 12, mid-tones) instead of grey-world, so a red car or a
   green field does not read as a cast; every shift is clamped and faded to zero at black and white so
   highlights never clip and blacks never lift. Screenshots are never colour-matched (UI white stays
   white); AI stills at reduced strength. Matching is against the **pre-grade mezzanine**, so the render's
   look applies to A-roll and inserts alike. The recipe records the neutral ΔE*ab to the A-roll before and
   after the match; above 5 after (``broll.reject_neutral_delta_e``) it warns — ranking gates on the same
   number, so a conformed insert that still fails was placed against the ranking's advice. A
   black-and-white insert in a colour A-roll also warns (a partial match never restores colour; MKL/full
   covariance transfer was rejected for the same reason: it would change saturation and hue, which the
   doctrine keeps off-limits for inserts).
6. **Encode**: 16-bit RGB → zimg → ``yuv422p10le`` BT.709 limited range → ProRes 422 HQ (Apple's
   ``prores_videotoolbox`` when present, else ``prores_ks``), exact frame count
   ``ceil((head + duration + tail) x fps)``.

The returned clip starts ``head`` seconds before the use range (default 0) — the conformed
:class:`~studio.doc.model.AssetRef` from :func:`conform_insert` carries that offset as ``in_ms``.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from studio.broll.rank import (
    CropPlan,
    Embedder,
    crop_frame,
    detect_faces,
    detect_letterbox,
    load_image,
    mode_aspect,
    mode_size,
    plan_crop,
    probe_media,
    saliency_map,
    sample_frames,
    semantic_positions,
    text_embedding,
)

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import AssetRef
    from studio.jobs import Job

__all__ = [
    "ConformError",
    "ARollReference",
    "ColorStats",
    "ColorTransfer",
    "ConformResult",
    "KenBurns",
    "conform",
    "conform_insert",
    "conform_asset",
    "aroll_color_stats",
    "neutral_delta_e",
    "DEFAULT_STRENGTH",
    "NEUTRAL_DELTA_E_MAX",
]

CONFORM_VERSION = 1
#: ``broll.reject_neutral_delta_e``: neutrals further than this (ΔE*ab) from the A-roll after the match reject
NEUTRAL_DELTA_E_MAX = 5.0
#: default grade-match strength per asset kind (screenshots/graphics are never matched)
DEFAULT_STRENGTH: dict[str, float] = {"video": 0.5, "image": 0.5, "generated_video": 0.5, "generated_image": 0.35,
                                      "screenshot": 0.0, "gif": 0.0, "audio": 0.0}
_STILL_KINDS = {"image", "screenshot", "generated_image"}
#: fastest scroll of a full-page capture, in window heights per second (readable while moving)
SCROLL_MAX_WIN_PER_S = 0.6


class ConformError(RuntimeError):
    """The asset cannot be conformed as asked (missing file, too short, decode/encode failure)."""


# ============================================================================================ colour
@dataclass(frozen=True)
class ColorStats:
    """CIE-Lab statistics of a set of frames (L 0–100; a/b ≈ ±127)."""

    l_mean: float
    l_std: float
    l_p02: float
    l_p98: float
    wb_a: float  # white-balance cast estimate (near-neutral pixels)
    wb_b: float
    neutral_frac: float
    chroma_mean: float = 0.0  # mean Lab chroma (≈ 0 for black-and-white footage)
    neutral_reliable: bool = False  # enough near-neutral pixels for wb_a/wb_b to be a measurement

    @classmethod
    def from_frames(cls, frames: Sequence[np.ndarray]) -> ColorStats:
        Ls, As, Bs, neutral = [], [], [], []
        for f in frames:
            if f is None:
                continue
            lab = _to_lab(f)
            L, a, b = lab[..., 0].ravel(), lab[..., 1].ravel(), lab[..., 2].ravel()
            Ls.append(L)
            As.append(a)
            Bs.append(b)
            neutral.append(_neutral_mask(L, a, b))
        if not Ls:
            raise ConformError("no frames for colour statistics")
        L = np.concatenate(Ls)
        A = np.concatenate(As)
        B = np.concatenate(Bs)
        N = np.concatenate(neutral)
        nf = float(N.mean())
        reliable = bool(N.sum() >= max(200, 0.03 * N.size))
        if reliable:
            wa, wb = float(np.mean(A[N])), float(np.mean(B[N]))
        else:  # few neutral pixels: weak grey-world estimate
            wa, wb = 0.5 * float(np.median(A)), 0.5 * float(np.median(B))
        return cls(l_mean=float(L.mean()), l_std=float(L.std()), l_p02=float(np.percentile(L, 2)),
                   l_p98=float(np.percentile(L, 98)), wb_a=wa, wb_b=wb, neutral_frac=nf,
                   chroma_mean=float(np.hypot(A, B).mean()), neutral_reliable=reliable)

    @property
    def monochrome(self) -> bool:
        """Black-and-white / fully desaturated picture (a grade match never adds saturation back)."""
        return self.chroma_mean < 4.0

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _to_lab(f: np.ndarray) -> np.ndarray:
    """RGB uint8/uint16/float [0, 1] → float32 CIE-Lab (OpenCV: L 0–100, a/b signed)."""
    import cv2

    return cv2.cvtColor(np.ascontiguousarray(_to_unit_rgb(f)), cv2.COLOR_RGB2Lab)


def _neutral_mask(L: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Near-neutral mid-tone pixels (walls, shirts, paper, grey sky): where a white-balance cast is read."""
    return (np.hypot(a, b) < 12.0) & (L > 20.0) & (L < 90.0)


def neutral_delta_e(frames: Sequence[np.ndarray], ref: ColorStats, transfer: ColorTransfer | None = None
                    ) -> float | None:
    """Chroma ΔE*ab between the insert's neutrals and the A-roll's, **after** ``transfer`` (the conform's grade
    match), measured on the insert's own near-neutral pixels. None when either side has too few neutral
    pixels to be a measurement (a red car filling the frame says nothing about white balance).

    The doctrine (``color-and-look.md``, ``broll.reject_neutral_delta_e``): reject an insert whose neutrals
    stay above about ΔE 5 from the A-roll — beyond that a partial-strength match cannot hide the jump."""
    if not ref.neutral_reliable:
        return None
    sa, sb, n = 0.0, 0.0, 0
    total = 0
    for f in frames:
        if f is None:
            continue
        x = _to_unit_rgb(f)
        lab = _to_lab(x)
        total += lab.shape[0] * lab.shape[1]
        m = _neutral_mask(lab[..., 0], lab[..., 1], lab[..., 2])
        if not m.any():
            continue
        if transfer is not None and not transfer.is_identity:
            lab = _to_lab(transfer.apply(x))
        sa += float(lab[..., 1][m].sum())
        sb += float(lab[..., 2][m].sum())
        n += int(m.sum())
    if total == 0 or n < max(200, 0.03 * total):
        return None
    return float(math.hypot(sa / n - ref.wb_a, sb / n - ref.wb_b))


def _to_unit_rgb(f: np.ndarray) -> np.ndarray:
    if f.dtype == np.uint16:
        return f[..., :3].astype(np.float32) / 65535.0
    if f.dtype == np.uint8:
        return f[..., :3].astype(np.float32) / 255.0
    return np.ascontiguousarray(f[..., :3], dtype=np.float32)


@dataclass(frozen=True)
class ColorTransfer:
    """Partial-strength exposure/WB/contrast transfer in Lab, faded out at black and white."""

    l_src_mean: float
    l_shift: float
    l_gain: float
    a_shift: float
    b_shift: float
    strength: float

    MAX_L_SHIFT = 12.0
    MAX_AB_SHIFT = 8.0
    GAIN_RANGE = (0.8, 1.25)

    @classmethod
    def between(cls, src: ColorStats, ref: ColorStats, strength: float) -> ColorTransfer:
        s = float(min(max(strength, 0.0), 1.0))
        l_shift = float(np.clip(s * (ref.l_mean - src.l_mean), -cls.MAX_L_SHIFT, cls.MAX_L_SHIFT))
        ratio = float(np.clip(ref.l_std / max(src.l_std, 1e-3), *cls.GAIN_RANGE))
        gain = 1.0 + s * (ratio - 1.0)
        a_shift = float(np.clip(s * (ref.wb_a - src.wb_a), -cls.MAX_AB_SHIFT, cls.MAX_AB_SHIFT))
        b_shift = float(np.clip(s * (ref.wb_b - src.wb_b), -cls.MAX_AB_SHIFT, cls.MAX_AB_SHIFT))
        return cls(l_src_mean=src.l_mean, l_shift=l_shift, l_gain=gain, a_shift=a_shift, b_shift=b_shift,
                   strength=s)

    @property
    def is_identity(self) -> bool:
        return abs(self.l_shift) < 0.05 and abs(self.l_gain - 1) < 1e-3 and abs(self.a_shift) < 0.05 \
            and abs(self.b_shift) < 0.05

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """``rgb`` float32 [0, 1] (H, W, 3) → transformed float32 [0, 1]."""
        import cv2

        if self.is_identity:
            return rgb
        lab = cv2.cvtColor(np.ascontiguousarray(rgb, dtype=np.float32), cv2.COLOR_RGB2Lab)
        L = lab[..., 0]
        target = (L - self.l_src_mean) * self.l_gain + self.l_src_mean + self.l_shift
        # fade every adjustment to zero at the black and white points: no clipping, no lifted blacks
        w = np.sqrt(np.clip(np.sin(np.pi * np.clip(L, 0.0, 100.0) / 100.0), 0.0, 1.0))
        lab[..., 0] = L + (target - L) * w
        lab[..., 1] += self.a_shift * w
        lab[..., 2] += self.b_shift * w
        out = cv2.cvtColor(lab, cv2.COLOR_Lab2RGB)
        return np.clip(out, 0.0, 1.0)

    def to_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in asdict(self).items()}


@dataclass
class ARollReference:
    """Where to read the A-roll's look: the mezzanine ``path`` and optional ``times_s`` (frames either
    side of the insert); default = 8 frames spread over the whole take (one grade for every insert)."""

    path: str
    times_s: Sequence[float] | None = None
    n: int = 8


_stats_cache: dict[tuple[str, float, tuple[float, ...]], ColorStats] = {}


def aroll_color_stats(ref: ARollReference | str | os.PathLike[str] | Job | ColorStats) -> ColorStats:
    """Lab statistics of the A-roll reference (cached per file + times)."""
    if isinstance(ref, ColorStats):
        return ref
    if hasattr(ref, "mezz_path"):
        ref = ARollReference(str(ref.mezz_path))  # type: ignore[union-attr]
    if not isinstance(ref, ARollReference):
        ref = ARollReference(str(ref))
    p = Path(ref.path)
    if not p.exists():
        raise ConformError(f"A-roll reference not found: {p}")
    mp = probe_media(p)
    times = tuple(float(t) for t in ref.times_s) if ref.times_s else \
        tuple(mp.duration_s * (i + 0.5) / ref.n for i in range(ref.n))
    key = (str(p.resolve()), p.stat().st_mtime, times)
    hit = _stats_cache.get(key)
    if hit is not None:
        return hit
    frames = [f for f in sample_frames(p, times, short_side=270, probe=mp) if f is not None]
    st = ColorStats.from_frames(frames)
    _stats_cache[key] = st
    return st


# ============================================================================================ motion
@dataclass(frozen=True)
class KenBurns:
    """Still motion: ``push`` (zoom in by ``zoom`` over the use range toward the subject), ``pull`` (the
    reverse), ``scroll`` (top → bottom pan of a tall capture, eased) or ``none``."""

    kind: str = "push"
    zoom: float = 1.07
    ease: str = "linear"  # linear | in_out (scroll always eases)

    def __post_init__(self) -> None:
        if self.kind not in ("push", "pull", "scroll", "none"):
            raise ValueError(f"unknown Ken Burns kind {self.kind!r}")
        if not (1.0 <= self.zoom <= 1.5):
            raise ValueError("zoom must be within 1.0–1.5")


def _smoothstep(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


# ============================================================================================ result
@dataclass
class ConformResult:
    path: Path
    asset: AssetRef
    recipe: dict[str, Any]
    frames: int
    cached: bool = False
    warnings: list[str] = field(default_factory=list)


# ============================================================================================ helpers
def _settings() -> Any:
    from studio.config import get_settings

    return get_settings()


@functools.lru_cache(maxsize=4)
def _encoders(ffmpeg: str) -> tuple[str, ...]:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             check=False).stdout
    except FileNotFoundError:  # pragma: no cover
        return ()
    return tuple(line.split()[1] for line in out.splitlines() if len(line.split()) > 2 and line.startswith(" V"))


def _prores_args(ffmpeg: str) -> list[list[str]]:
    from studio.media.ingest import mezz_codec_args

    encs = _encoders(ffmpeg)
    order = (["prores_videotoolbox"] if "prores_videotoolbox" in encs else []) + ["prores_ks"]
    return [mezz_codec_args("prores_hq", encoder=e) for e in order]


def _asset_file(asset: AssetRef, job: Job | None) -> Path:
    rec = asset
    if rec.path is None and rec.id and job is not None:
        loaded = job.load_asset(rec.id)
        if loaded is not None:
            rec = loaded
    if rec.path is None:
        raise ConformError(f"asset {asset.id or asset.source_id} has no local file (download it first)")
    p = Path(rec.path)
    if not p.is_absolute():
        if job is None:
            raise ConformError(f"asset path {rec.path} is job-relative but no job was given")
        p = job.root / p
    if not p.exists():
        raise ConformError(f"asset file missing: {p}")
    return p


def _load_analysis_crop(asset: AssetRef, job: Job | None, aspect: float) -> CropPlan | None:
    if job is None or not asset.id:
        return None
    p = job.assets_dir / "broll" / "analysis" / f"{asset.id}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        plan = CropPlan.from_dict(d["crop"])
    except Exception:
        return None
    return plan if abs(plan.aspect - aspect) / aspect < 0.01 else None


def _even_floor(x: float) -> int:
    return max(2, math.floor(x / 2.0) * 2)


def _fps_frac(fps: Fraction | float | int | str) -> Fraction:
    from studio.timebase import normalize_fps

    return normalize_fps(fps)


def _hash(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _affine(win: tuple[float, float, float, float], out_w: int, out_h: int) -> np.ndarray:
    """Forward affine mapping the window ``(x, y, w, h)`` (pixel units, pixel-centre convention) onto the
    ``out_w x out_h`` output."""
    x, y, w, h = win
    sx, sy = out_w / w, out_h / h
    return np.array([[sx, 0.0, (0.5 - x) * sx - 0.5], [0.0, sy, (0.5 - y) * sy - 0.5]], np.float64)


# ============================================================================================ planning
def _plan_for(asset: AssetRef, src: Path, *, mode: str, aspect: float, in_s: float, span_s: float, still: bool,
              job: Job | None, crop: CropPlan | Mapping[str, Any] | None, embedder: Embedder | None,
              semantic_crop: bool) -> tuple[CropPlan, list[np.ndarray], list[float]]:
    """Crop plan (given, from the ranking analysis, or computed) + the analysis frames used for stats."""
    mp = probe_media(src)
    if still:
        times = [0.0]
    else:
        n = max(4, min(10, math.ceil(span_s / 0.33) + 1))
        times = [in_s + span_s * (i + 0.5) / n for i in range(n)]
    frames = [f for f in sample_frames(src, times, short_side=540, probe=mp)]
    pairs = [(t, f) for t, f in zip(times, frames, strict=True) if f is not None]
    if not pairs:
        raise ConformError(f"could not decode frames from {src.name}")
    times = [t for t, _ in pairs]
    frames = [f for _, f in pairs]
    if crop is not None:
        plan = crop if isinstance(crop, CropPlan) else CropPlan.from_dict(crop)
        return plan, frames, times
    loaded = _load_analysis_crop(asset, job, aspect)
    if loaded is not None and mode != "pip":
        return loaded, frames, times
    # bars are only trusted on video (a single still's dark sky must not be cropped as a letterbox)
    active = detect_letterbox(frames) if not still and len(frames) >= 3 else (0.0, 0.0, 1.0, 1.0)
    if mode == "pip":
        return (CropPlan(aspect=aspect, active=active, window=(active[2] - active[0], active[3] - active[1]),
                         path=[(times[0], (active[0] + active[2]) / 2, (active[1] + active[3]) / 2)]),
                frames, times)
    faces = [detect_faces(f) for f in frames]
    semantic = None
    phrases = _semantic_phrases(asset, job)
    if semantic_crop and phrases:
        emb = embedder
        if emb is None:
            from studio.broll.rank import get_embedder

            emb = get_embedder()
        with contextlib.suppress(Exception):
            vec = text_embedding(emb, [p[:120] for p in phrases], weights=[2.0] + [1.0] * (len(phrases) - 1))
            picks = [frames[0], frames[len(frames) // 2], frames[-1]] if len(frames) >= 3 else list(frames)
            semantic = semantic_positions(emb, picks, vec, aspect, active)
    plan = plan_crop(frames, times, aspect, active=active, faces=faces, semantic=semantic,
                     source_size=(mp.width, mp.height))
    return plan, frames, times


def _semantic_phrases(asset: AssetRef, job: Job | None) -> list[str]:
    """What the crop must keep, best phrase first: the need the ranking judged this asset against (the
    Director's concrete phrase + query variants, from ``assets/broll/analysis/<id>.json``), else the asset's
    description and search query (a terse query like "laptop typing" names the keyboard, not the hands)."""
    out: list[str] = []
    if job is not None and asset.id:
        with contextlib.suppress(Exception):
            need = json.loads((job.assets_dir / "broll" / "analysis" / f"{asset.id}.json").read_text()).get("need")
            if isinstance(need, Mapping):
                out += [str(need.get("text") or "")] + [str(q) for q in need.get("queries") or []]
    out += [asset.description or "", asset.query or ""]
    seen: dict[str, None] = {}
    for p in out:
        p = " ".join(p.split())
        if p and p.lower() not in {k.lower() for k in seen}:
            seen[p] = None
    return list(seen)[:4]


# ============================================================================================ main entry
def conform_insert(asset: AssetRef, insert_mode: str, duration: float | Fraction, fps: Fraction | float | str,
                   a_roll_reference: ARollReference | str | os.PathLike[str] | ColorStats | Job | None = None, *,
                   job: Job | None = None, out_path: str | os.PathLike[str] | None = None, speed: float = 1.0,
                   handles: tuple[float, float] = (0.0, 0.5), size: tuple[int, int] | None = None,
                   split_ratio: float = 0.5, crop: CropPlan | Mapping[str, Any] | None = None,
                   strength: float | None = None, ken_burns: KenBurns | str | None = None,
                   embedder: Embedder | None = None, semantic_crop: bool = True, overwrite: bool = False
                   ) -> ConformResult:
    """Conform ``asset`` for an insert (see the module docstring); returns path, conformed AssetRef and recipe.

    ``duration`` is the on-screen length (output seconds); the source range is ``asset.in_ms`` onward,
    ``duration x speed`` long. ``handles`` = (head, tail) extra output seconds, taken only where the source
    has frames (stills always have them). ``size`` overrides the layer size (default from the mode).
    ``strength`` None = per-kind default (:data:`DEFAULT_STRENGTH`). ``ken_burns`` applies to stills by
    default (``push``; ``scroll`` for tall screenshots); pass ``"none"`` to hold a still, or a
    :class:`KenBurns` for video to push in on a static shot.
    """
    s = _settings()
    ffmpeg = s.ffmpeg
    fps_f = _fps_frac(fps)
    dur = float(duration)
    if dur <= 0:
        raise ValueError("duration must be positive")
    if not (0.25 <= speed <= 4.0):
        raise ValueError("speed must be within 0.25–4")
    if insert_mode not in ("full", "card", "split_top", "split_bottom", "pip"):
        raise ValueError(f"unknown insert mode {insert_mode!r}")
    src = _asset_file(asset, job)
    mp = probe_media(src)
    still = mp.is_image or asset.kind in _STILL_KINDS
    kind = "screenshot" if asset.kind == "screenshot" else ("image" if still and asset.kind not in _STILL_KINDS
                                                             else asset.kind)
    head, tail = (max(0.0, float(handles[0])), max(0.0, float(handles[1])))
    in_s = (asset.in_ms or 0) / 1000.0
    warnings: list[str] = []

    # ---------------------------------------------------------------- source range & handles
    # an animated GIF (a reaction/meme loop) plays as a loop: it never runs out of source
    loop = not still and (asset.kind == "gif" or src.suffix.lower() == ".gif")
    if not still:
        avail_after = mp.duration_s - in_s
        need_src = dur * speed
        if not loop and avail_after + (1.0 / (mp.fps or 30)) < need_src - 1e-3:
            raise ConformError(f"{src.name}: only {avail_after:.2f}s of source after the in-point, "
                               f"{need_src:.2f}s needed (duration {dur:.2f}s x speed {speed:g})")
        head = min(head, in_s / speed)
        if not loop:
            tail = max(0.0, min(tail, (avail_after - need_src) / speed))
    total = head + dur + tail
    n_frames = math.ceil(total * fps_f - 1e-9)
    src_in = in_s - head * speed  # source seconds at output frame 0

    # ---------------------------------------------------------------- geometry
    # A given layer size is always filled with a cover crop at exactly its aspect (never a squeeze): the
    # compositor places the clip 1:1. Without a size, PiP keeps the source's active-picture aspect.
    src_aspect = mp.width / mp.height
    if size is not None:
        if size[0] % 2 or size[1] % 2 or min(size) < 2:
            raise ValueError("layer size must be even")
        aspect = size[0] / size[1]
        plan_mode = "full" if insert_mode in ("pip", "card") else insert_mode
    else:
        aspect = mode_aspect(insert_mode, split_ratio=split_ratio, source_aspect=src_aspect)
        plan_mode = insert_mode
    plan, frames, ftimes = _plan_for(asset, src, mode=plan_mode, aspect=aspect, in_s=in_s,
                                     span_s=max(0.04, dur * speed), still=still, job=job, crop=crop,
                                     embedder=embedder, semantic_crop=semantic_crop)
    if size is None and insert_mode == "pip":
        act = plan.active
        src_aspect = (mp.width * (act[2] - act[0])) / (mp.height * (act[3] - act[1]))
    out_w, out_h = size or mode_size(insert_mode, split_ratio=split_ratio, source_aspect=src_aspect)
    if out_w % 2 or out_h % 2:
        raise ValueError("layer size must be even")
    plan = _exact_aspect(plan, out_w / out_h, mp.width / mp.height)

    # ---------------------------------------------------------------- motion
    kb: KenBurns | None
    if isinstance(ken_burns, str):
        kb = KenBurns(kind=ken_burns)
    else:
        kb = ken_burns
    if kb is None and still:
        # scroll only a *full-page* capture (much taller than a phone screen); a screen-sized capture that
        # is cropped for a split holds on the chosen element with a push (moving text is hard to read)
        act_h_px = (plan.active[3] - plan.active[1]) * mp.height
        act_w_px = (plan.active[2] - plan.active[0]) * mp.width
        full_page = act_h_px / max(1.0, act_w_px) >= 2.2 and plan.window[1] < (plan.active[3] - plan.active[1]) * 0.75
        kb = KenBurns(kind="scroll" if kind == "screenshot" and full_page else "push")
    if kb is not None and kb.kind == "none":
        kb = None

    # window (normalised) for each output frame
    def window_at(i: int) -> tuple[float, float, float, float]:
        t_out = i / float(fps_f)  # seconds from clip start
        t_src = src_in + t_out * speed
        x, y, w, h = plan.rect_at(t_src)
        if kb is None:
            return (x, y, w, h)
        ax0, ay0, ax1, ay1 = plan.active
        if kb.kind == "scroll":
            # eased top → down pan, at most SCROLL_MAX_WIN_PER_S window heights per second so lines stay
            # trackable: a very long page scrolls through its top part, never whips past everything
            u = _smoothstep(t_out / max(1e-6, total))
            travel = min(ay1 - ay0 - h, SCROLL_MAX_WIN_PER_S * h * total)
            return (x, ay0 + travel * u, w, h)
        # push/pull: geometric zoom at a constant rate chosen so the *use range* gets exactly `zoom`,
        # continuing at the same rate through the handles; the centre drifts toward the subject point in
        # proportion to the zoom progress (so the push lands on the subject, never a lateral whip)
        rate = math.log(kb.zoom) / dur
        z_max = math.exp(rate * total)
        z = math.exp(rate * (t_out if kb.kind == "push" else (total - t_out)))
        prog = (z - 1.0) / max(1e-9, z_max - 1.0)
        tx, ty = subject_pt
        cx, cy = x + w / 2, y + h / 2
        cxz = cx + (tx - cx) * prog
        cyz = cy + (ty - cy) * prog
        wz, hz = w / z, h / z
        xz = min(max(cxz - wz / 2, ax0), ax1 - wz)
        yz = min(max(cyz - hz / 2, ay0), ay1 - hz)
        return (xz, yz, wz, hz)

    subject_pt = _subject_point(frames[len(frames) // 2], plan, ftimes[len(ftimes) // 2]) if kb else (0.5, 0.5)
    windows = [window_at(i) for i in range(n_frames)]
    dynamic = kb is not None or not plan.is_static

    # ---------------------------------------------------------------- colour
    if strength is None:
        strength = DEFAULT_STRENGTH.get(kind, 0.5)
    transfer: ColorTransfer | None = None
    ref_stats: ColorStats | None = None
    src_stats: ColorStats | None = None
    de_before = de_after = None
    if a_roll_reference is not None and strength > 0:
        try:
            ref_stats = aroll_color_stats(a_roll_reference)
        except (ConformError, ValueError, OSError) as e:  # an ungraded insert beats a failed render: say so
            warnings.append(f"A-roll reference unavailable ({e}); insert not grade-matched")
    if ref_stats is not None:
        crops = [crop_frame(f, plan.rect_at(t)) for f, t in zip(frames, ftimes, strict=True)]
        src_stats = ColorStats.from_frames(crops)
        transfer = ColorTransfer.between(src_stats, ref_stats, strength)
        if transfer.is_identity:
            transfer = None
        de_before = neutral_delta_e(crops, ref_stats)
        de_after = neutral_delta_e(crops, ref_stats, transfer)
        if de_after is not None and de_after > NEUTRAL_DELTA_E_MAX:
            warnings.append(f"neutrals stay ΔE {de_after:.1f} from the A-roll after the match "
                            f"(> {NEUTRAL_DELTA_E_MAX:g}: the doctrine rejects this insert)")
        if src_stats.monochrome and not ref_stats.monochrome:
            warnings.append("black-and-white insert in a colour A-roll (a grade match never adds colour back)")

    # ---------------------------------------------------------------- cache key / output path
    stat = src.stat()
    key = {"v": CONFORM_VERSION, "src": str(src.resolve()), "mtime": stat.st_mtime, "size": stat.st_size,
           "mode": insert_mode, "wh": [out_w, out_h], "fps": str(fps_f), "dur": round(dur, 6), "speed": speed,
           "in_s": round(in_s, 6), "head": round(head, 6), "tail": round(tail, 6), "plan": plan.to_dict(),
           "kb": asdict(kb) if kb else None, "transfer": transfer.to_dict() if transfer else None}
    h = _hash(key)
    aid = asset.id or re.sub(r"[^A-Za-z0-9._-]+", "-", f"{asset.source}_{asset.source_id or src.stem}")[:48]
    if out_path is None:
        base = (job.assets_dir / "broll" / "conformed") if job is not None else (src.parent / "conformed")
        out = base / f"{aid}_{insert_mode}_{out_w}x{out_h}_{float(fps_f):.3f}_{h}.mov"
    else:
        out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sidecar = out.with_suffix(out.suffix + ".json")

    recipe: dict[str, Any] = {
        "conform_version": CONFORM_VERSION, "source_path": str(src), "asset_id": asset.id, "kind": kind,
        "mode": insert_mode, "size": [out_w, out_h], "fps": f"{fps_f.numerator}/{fps_f.denominator}",
        "frames": n_frames, "duration_s": round(dur, 6), "speed": speed, "handles_s": [round(head, 6), round(tail, 6)],
        "source_in_s": round(in_s, 6), "source_first_s": round(src_in, 6), "still": still,
        "source": {"width": mp.width, "height": mp.height, "fps": mp.fps, "duration_s": mp.duration_s,
                   "transfer": mp.transfer, "hdr_tonemapped": mp.hdr},
        "crop": plan.to_dict(), "ken_burns": asdict(kb) if kb else None, "subject_point": list(subject_pt),
        "color": {"strength": strength, "reference": ref_stats.to_dict() if ref_stats else None,
                  "source": src_stats.to_dict() if src_stats else None,
                  "transfer": transfer.to_dict() if transfer else None,
                  "neutral_delta_e_before": None if de_before is None else round(de_before, 2),
                  "neutral_delta_e_after": None if de_after is None else round(de_after, 2)},
        "cadence": _cadence_note(mp.fps, fps_f, speed, still), "audio": "muted", "loop": loop, "key": h,
    }
    if recipe["cadence"].get("uneven") or recipe["cadence"].get("slowmo_stutter"):
        warnings.append(recipe["cadence"]["note"])
    recipe["warnings"] = warnings
    if out.exists() and sidecar.exists() and not overwrite:
        with contextlib.suppress(Exception):
            if json.loads(sidecar.read_text()).get("key") == h:
                return ConformResult(path=out, asset=_conformed_asset(asset, out, job, head, dur, total, out_w,
                                                                      out_h), recipe=recipe, frames=n_frames,
                                     cached=True, warnings=list(warnings))

    # ---------------------------------------------------------------- render
    log = (job.log_path(f"broll_conform_{aid}") if job is not None else out.with_suffix(".log"))
    tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
    try:
        if still:
            _render_still(src, windows, out_w, out_h, n_frames, fps_f, transfer, tmp, log, ffmpeg)
        else:
            _render_video(src, mp, windows, dynamic, out_w, out_h, n_frames, fps_f, speed, src_in,
                          total * speed, transfer, tmp, log, ffmpeg, loop=loop)
        os.replace(tmp, out)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    got = _count_frames(out)
    if got is not None and got != n_frames:
        raise ConformError(f"conform wrote {got} frames, expected {n_frames} ({out.name})")
    sidecar.write_text(json.dumps(recipe, indent=2, default=str))
    return ConformResult(path=out, asset=_conformed_asset(asset, out, job, head, dur, total, out_w, out_h),
                         recipe=recipe, frames=n_frames, warnings=warnings)



def conform(asset: AssetRef, insert_mode: str, duration: float | Fraction, fps: Fraction | float | str,
            a_roll_reference: ARollReference | str | os.PathLike[str] | ColorStats | Job | None = None,
            **kw: Any) -> Path:
    """:func:`conform_insert` returning only the path of the conformed ProRes clip."""
    return conform_insert(asset, insert_mode, duration, fps, a_roll_reference, **kw).path


def conform_asset(job: Job, asset: AssetRef, *, fps: Fraction, width: int, height: int, duration_s: Fraction,
                  mode: str | None = None, a_roll_reference: Any = "auto", **kw: Any) -> Path:
    """Renderer-facing wrapper: a clip of exactly ``width x height`` that **starts at the asset's use range**
    (``asset.in_ms``; no head handle) for ``duration_s`` plus a 0.5 s tail handle where the source allows.
    ``mode`` defaults from the size (1080x1920 → full, full-width shorter → split, else PiP).
    ``a_roll_reference="auto"`` matches the grade to the job's mezzanine when it exists."""
    if mode is None:
        mode = "full" if (width, height) == (1080, 1920) or width / height == 9 / 16 else \
            ("split_top" if width == 1080 else "pip")
    if isinstance(a_roll_reference, str) and a_roll_reference == "auto":
        a_roll_reference = job.mezz_path if job.mezz_path.exists() else None
    split_ratio = kw.pop("split_ratio", height / 1920 if mode.startswith("split") else 0.5)
    kw.setdefault("handles", (0.0, 0.5))
    return conform_insert(asset, mode, duration_s, fps, a_roll_reference, job=job, size=(width, height),
                          split_ratio=split_ratio, **kw).path


# ============================================================================================ internals
def _cadence_note(src_fps: float | None, out_fps: Fraction, speed: float, still: bool) -> dict[str, Any]:
    """How source frames map onto the timeline. ``speed`` is source seconds per output second, so the rate of
    *new* pictures on screen is ``src_fps x speed`` (2x fast-forward of 30 fps = 60 new frames/s; 0.5x slow
    motion of 30 fps = 15). Below the timeline rate frames repeat: evenly for integer ratios (30 → 60),
    unevenly otherwise (24 → 30/60: judder on motion). Slow motion below the timeline rate is a stutter
    (doctrine ``speed.md``: the slow-down factor must not exceed source fps ÷ output fps)."""
    if still or not src_fps:
        return {"uneven": False, "slowmo_stutter": False, "note": "still" if still else "unknown source fps"}
    eff = src_fps * speed
    ratio = float(out_fps) / eff
    uneven = abs(ratio - round(ratio)) > 0.02 and ratio > 1.0
    slowmo = speed < 1.0 - 1e-6 and ratio > 1.0 + 0.02
    if slowmo:
        note = (f"{speed:g}x slow motion of a {src_fps:.3f} fps source gives {eff:.3f} new frames/s in a "
                f"{float(out_fps):.3f} fps timeline: frames repeat (stutter); needs a ≥ "
                f"{float(out_fps) / speed:.0f} fps source")
    elif uneven:
        note = (f"{eff:.3f} fps source into a {float(out_fps):.3f} fps timeline repeats frames unevenly "
                "(judder on motion)")
    else:
        note = "cadence clean"
    return {"uneven": uneven, "slowmo_stutter": slowmo, "source_fps": round(src_fps, 3),
            "effective_fps": round(eff, 3), "ratio": round(ratio, 4), "note": note}


def _exact_aspect(plan: CropPlan, aspect: float, frame_aspect: float) -> CropPlan:
    """``plan`` with its window trimmed (about its centre path) to exactly the pixel ``aspect`` of the output
    layer, so the scale to the layer is uniform. Only ever shrinks one side (a cover crop), never grows past
    the active picture. Plans from ranking or from a caller can be off by rounding or target another size."""
    ww, wh = plan.window
    cur = (ww * frame_aspect) / max(wh, 1e-9)
    if abs(cur / aspect - 1.0) < 1e-6:
        return plan
    if cur > aspect:  # too wide: narrow it
        ww = aspect * wh / frame_aspect
    else:  # too tall: shorten it
        wh = ww * frame_aspect / aspect
    return replace(plan, window=(ww, wh), aspect=aspect)


def _subject_point(frame: np.ndarray, plan: CropPlan, t: float) -> tuple[float, float]:
    """Saliency peak inside the base window (normalised frame coordinates): where a push lands."""
    import cv2

    x, y, w, h = plan.rect_at(t)
    crop = crop_frame(frame, (x, y, w, h))
    faces = detect_faces(crop)
    sal = saliency_map(crop, faces, size=64)
    sal = cv2.GaussianBlur(sal, (0, 0), 3)
    iy, ix = np.unravel_index(int(np.argmax(sal)), sal.shape)
    px = (ix + 0.5) / sal.shape[1]
    py = (iy + 0.5) / sal.shape[0]
    # move only part of the way toward the peak (a push, not a reframe): at most the push margin
    px = 0.5 + (px - 0.5) * 0.5
    py = 0.5 + (py - 0.5) * 0.5
    return (x + px * w, y + py * h)


def _union_px(windows: Sequence[tuple[float, float, float, float]], W: int, H: int) -> tuple[int, int, int, int]:
    x0 = min(w[0] for w in windows)
    y0 = min(w[1] for w in windows)
    x1 = max(w[0] + w[2] for w in windows)
    y1 = max(w[1] + w[3] for w in windows)
    px0 = max(0, math.floor(x0 * W) - 2)
    py0 = max(0, math.floor(y0 * H) - 2)
    px1 = min(W, math.ceil(x1 * W) + 2)
    py1 = min(H, math.ceil(y1 * H) + 2)
    return px0, py0, px1 - px0, py1 - py0


def _encoder_cmd(ffmpeg: str, w: int, h: int, fps: Fraction, out: Path, codec_args: list[str]) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}",
            "-framerate", f"{fps.numerator}/{fps.denominator}",
            "-i", "-", "-an",
            "-vf", "zscale=rin=full:tin=709:pin=709:r=limited:m=709:t=709:p=709:f=spline36:dither=error_diffusion,"
                   "format=yuv422p10le,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
            *codec_args, "-r", f"{fps.numerator}/{fps.denominator}", "-f", "mov", str(out)]


def _run_encoder(ffmpeg: str, w: int, h: int, fps: Fraction, out: Path, log: Path,
                 make_frames: Callable[[], Iterator[np.ndarray]]) -> None:
    """Feed uint16 RGB frames from ``make_frames()`` into a ProRes encoder; if Apple's encoder fails the
    frames are regenerated and encoded with ``prores_ks``."""
    errors = []
    for codec_args in _prores_args(ffmpeg):
        frames = make_frames()
        cmd = _encoder_cmd(ffmpeg, w, h, fps, out, codec_args)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as lf:
            lf.write(("\n$ " + " ".join(cmd) + "\n").encode())
            lf.flush()
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=lf)
            try:
                for fr in frames:
                    assert proc.stdin is not None
                    proc.stdin.write(np.ascontiguousarray(fr, dtype="<u2").tobytes())
                assert proc.stdin is not None
                proc.stdin.close()
                rc = proc.wait()
            except BrokenPipeError:
                rc = proc.wait()
            except BaseException:
                proc.kill()
                proc.wait()
                raise
        with contextlib.suppress(Exception):
            frames.close()  # type: ignore[attr-defined]  # stop the decoder of an abandoned attempt
        if rc == 0 and out.exists() and out.stat().st_size > 0:
            return
        errors.append(f"{codec_args[1]} rc={rc}")
    raise ConformError(f"ProRes encode failed ({', '.join(errors)}); see {log}")


def _render_still(src: Path, windows: Sequence[tuple[float, float, float, float]], out_w: int, out_h: int,
                  n: int, fps: Fraction, transfer: ColorTransfer | None, out: Path, log: Path, ffmpeg: str) -> None:
    import cv2

    img = None
    if src.suffix.lower() not in (".mp4", ".mov"):
        with contextlib.suppress(Exception):  # Pillow (EXIF orientation, ICC → sRGB, HEIC via pillow-heif)
            img = load_image(str(src))
    if img is None:  # ffmpeg's decoder (containers Pillow cannot open)
        f = sample_frames(src, [0.0], short_side=100000)[0]
        if f is None:
            raise ConformError(f"cannot decode still {src.name}")
        img = f
    H, W = img.shape[:2]
    bx, by, bw, bh = _union_px(windows, W, H)
    # prescale so the smallest window maps to at least the output size (never minify by more than the push).
    # Area-average in 16 bits (sub-8-bit precision survives into the warp: no banding in skies) and before
    # any float copy (a 50 MP stock photo would otherwise cost ~0.6 GB as float32).
    min_w_px = min(w[2] for w in windows) * W
    min_h_px = min(w[3] for w in windows) * H
    f = max(out_w / min_w_px, out_h / min_h_px)
    scale = 1.0
    region16 = img[by:by + bh, bx:bx + bw].astype(np.uint16) * 257
    if f < 1.0:
        scale = f
        region16 = cv2.resize(region16, (max(2, round(bw * scale)), max(2, round(bh * scale))),
                              interpolation=cv2.INTER_AREA)
    region = region16.astype(np.float32) / 65535.0
    del region16
    if transfer is not None:
        region = transfer.apply(region)  # static picture: colour once, then warp

    def frames() -> Any:
        for (x, y, w, h) in windows:
            win = ((x * W - bx) * scale, (y * H - by) * scale, w * W * scale, h * H * scale)
            M = _affine(win, out_w, out_h)
            fr = cv2.warpAffine(region, M, (out_w, out_h), flags=cv2.INTER_LANCZOS4,
                                borderMode=cv2.BORDER_REFLECT101)
            yield (np.clip(fr, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)

    _run_encoder(ffmpeg, out_w, out_h, fps, out, log, frames)


def _render_video(src: Path, mp: Any, windows: Sequence[tuple[float, float, float, float]], dynamic: bool,
                  out_w: int, out_h: int, n: int, fps: Fraction, speed: float, src_in: float, src_len: float,
                  transfer: ColorTransfer | None, out: Path, log: Path, ffmpeg: str, *, loop: bool = False) -> None:
    import cv2

    from studio.media.ingest import mezz_video_filter
    from studio.media.probe import probe

    try:
        info = probe(src)
        base = mezz_video_filter(info)
    except Exception as e:  # probe failure (odd container, GIF): explicit BT.709 limited-range conversion
        base = ("setpts=PTS-STARTPTS,scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,"
                "format=yuv422p10le")
        with open(log, "ab") as lf:
            lf.write(f"\n[mezz chain unavailable: {e}]\n".encode())
    W, H = mp.width, mp.height
    to_rgb = "zscale=rin=limited:r=full:min=709:m=709:tin=709:t=709:pin=709:p=709:f=spline36,format=gbrp16le," \
             "format=rgb48le"
    cadence = f"setpts=(PTS-STARTPTS)/{speed:.6f},fps=fps={fps.numerator}/{fps.denominator}:round=near"
    if not dynamic:
        x, y, w, h = windows[0]
        cw, ch = max(2, round(w * W)), max(2, round(h * H))
        cx, cy = min(max(0, round(x * W)), W - cw), min(max(0, round(y * H)), H - ch)
        geo = f"crop={cw}:{ch}:{cx}:{cy}:exact=1"
        if (cw, ch) != (out_w, out_h):
            geo += f",scale={out_w}:{out_h}:flags=lanczos+accurate_rnd+full_chroma_int"
        fw, fh = out_w, out_h
        scale, bx, by = 1.0, 0, 0
    else:
        bx, by, bw, bh = _union_px(windows, W, H)
        bw, bh = max(2, bw), max(2, bh)
        min_w_px = min(w[2] for w in windows) * W
        min_h_px = min(w[3] for w in windows) * H
        f = max(out_w / min_w_px, out_h / min_h_px)
        scale = f if f < 1.0 else 1.0
        fw, fh = (max(2, round(bw * scale)), max(2, round(bh * scale))) if scale < 1.0 else (bw, bh)
        geo = f"crop={bw}:{bh}:{bx}:{by}:exact=1"
        if scale < 1.0:
            geo += f",scale={fw}:{fh}:flags=lanczos+accurate_rnd+full_chroma_int"
    vf = f"{base},{cadence},{to_rgb},{geo},tpad=stop_mode=clone:stop=-1"
    ss = max(0.0, src_in)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *(["-stream_loop", "-1"] if loop else []),
           "-ss", f"{ss:.6f}", "-t", f"{src_len + 1.0:.6f}", "-i", str(src), "-an", "-sn", "-dn", "-vf", vf,
           "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"]
    with open(log, "ab") as lf:
        lf.write(("\n$ " + " ".join(cmd) + "\n").encode())
    frame_bytes = fw * fh * 6

    def frames() -> Any:
        with open(log, "ab") as lf:
            dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf)
            try:
                assert dec.stdout is not None
                for i in range(n):
                    buf = _read_exact(dec.stdout, frame_bytes)
                    if buf is None:
                        raise ConformError(f"decoder stopped after {i} of {n} frames ({src.name}); see {log}")
                    fr = np.frombuffer(buf, "<u2").reshape(fh, fw, 3)
                    if not dynamic and transfer is None:
                        yield fr
                        continue
                    x = fr.astype(np.float32) / 65535.0
                    if dynamic:
                        wx, wy, ww, wh = windows[i]
                        win = ((wx * W - bx) * scale, (wy * H - by) * scale, ww * W * scale, wh * H * scale)
                        x = cv2.warpAffine(x, _affine(win, out_w, out_h), (out_w, out_h),
                                           flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT101)
                    if transfer is not None:
                        x = transfer.apply(x)
                    yield (np.clip(x, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
            finally:
                with contextlib.suppress(Exception):
                    dec.kill()
                dec.wait()

    _run_encoder(ffmpeg, out_w, out_h, fps, out, log, frames)


def _read_exact(fh: Any, n: int) -> bytes | None:
    chunks, got = [], 0
    while got < n:
        b = fh.read(n - got)
        if not b:
            return None
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def _count_frames(path: Path) -> int | None:
    from studio.config import get_settings

    try:
        out = subprocess.run([get_settings().ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                              "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        return int(out.split(",")[0]) if out else None
    except Exception:
        return None


def _conformed_asset(asset: AssetRef, out: Path, job: Job | None, head: float, dur: float, total: float,
                     w: int, h: int) -> AssetRef:
    rel = str(out)
    if job is not None:
        with contextlib.suppress(ValueError):
            rel = out.resolve().relative_to(job.root).as_posix()
    in_ms = round(head * 1000)
    return asset.model_copy(update={
        "path": rel, "in_ms": in_ms, "out_ms": in_ms + round(dur * 1000), "width": w, "height": h,
        "duration_ms": round(total * 1000),
        "kind": "video" if asset.kind in ("image", "screenshot", "generated_image") else asset.kind,
    })


def _tempdir() -> Path:  # pragma: no cover - helper for ad-hoc use
    return Path(tempfile.mkdtemp(prefix="conform_"))
