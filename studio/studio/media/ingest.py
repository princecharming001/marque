"""Ingest: original → mezzanine, dialogue WAV and proxy (ARCHITECTURE §3).

``ingest(src_path, job)`` writes into the job's ``media/`` directory:

* ``original.<ext>`` — hard link to the source (copy when linking is impossible); never modified.
* ``probe.json`` — ffprobe JSON + measurements (:func:`studio.media.probe.probe_full`).
* ``media_info.json`` — the :class:`~studio.media.models.MediaInfo` (via ``job.save_media_info``).
* ``mezz.mov`` — the **mezzanine** every later video operation reads: upright, square pixels, full
  resolution, CFR at the source rate, SDR BT.709 (tv range, tagged), ProRes 422 HQ ``yuv422p10le``.
* ``audio.wav`` — 48 kHz float32 **mono dialogue** track, sample-aligned to the mezzanine (sample 0 = first
  video frame) and exactly as long as it; ``audio_stereo.wav`` keeps the stereo mix when the source is
  stereo (or multichannel, downmixed).
* ``proxy.mp4`` — 540x960 (or the native-aspect equivalent) H.264 with burned SMPTE timecode, mono AAC.

Quality decisions (only final-video quality matters)
-----------------------------------------------------
**Tone mapping (HLG/PQ → SDR BT.709), exactly once, here.** The installed ffmpeg has no libplacebo, so the
chain is zimg + ``tonemap``:

``zscale`` (source matrix/transfer/primaries → *linear* BT.709 RGB, ``npl=203``) → ``gbrpf32le`` →
``setparams colorspace=bt709`` → ``tonemap=mobius:param=0.6:desat=2:peak=<src peak/203>`` → ``zscale``
(linear → BT.709, tv range, **error-diffusion dither**) → ``yuv422p10le``.

* ``npl=203`` anchors the conversion at the BT.2408 HDR reference/graphics white (75 % HLG = 203 cd/m²):
  linear 1.0 is diffuse white, which lands on SDR 100 % before the curve; HLG's nominal 1000-nit peak is
  4.93. Everything is computed in float with exact transfer functions (``agamma=0``); zimg's BT.709 output
  is the display-referred BT.1886 inverse EOTF (measured), so relative display luminance is preserved.
* **Operator: Möbius with the knee at j=0.6** (≈122 cd/m² — above the BT.2408 light-skin range, 55–65 %
  HLG ≈ 0.3–0.55 linear). Möbius is the only curve in ffmpeg's ``tonemap`` that is *exactly identity below
  its knee* and C¹-continuous above it, so faces keep the reference exposure, contrast and saturation
  while speculars and windows roll off smoothly into the top of the range. Measured on a real face by
  round-tripping SDR → HLG (203-nit mapping) → this chain: face mean error ≤ 0.2 code values with j=0.6;
  j=0.3 (ffmpeg's default) darkens the face ~2 codes and the frame ~7; ``hable`` (normalised to peak, as
  ffmpeg implements it) darkens skin by ~34 codes (≈ −1 stop, the "grey HDR" look Meta had to tune away);
  ``reinhard`` flattens mid-tone contrast; ``clip`` blows every highlight above diffuse white.
  All of it is configurable (:class:`ToneMapConfig`, env ``STUDIO_TONEMAP_*``) for the planned blind A/B.
* Primaries are converted to BT.709 in linear light *before* the curve, so the curve sees the final RGB
  peaks and nothing clips afterwards. ``tonemap`` computes desaturation luma from the frame's
  ``colorspace``; zscale's RGB frames carry none, which silently turns ``desat`` into a highlight-to-white
  smear on saturated skin (white blotches). ``setparams=colorspace=bt709`` fixes the coefficients.
* Dithering (Floyd–Steinberg) is applied only on the HDR path, where float → 10-bit quantisation of a
  compressed tone curve would otherwise band. SDR sources go 8/10-bit → 10-bit without dither.
* Dolby Vision 8.4 (iPhone) is tone-mapped from its HLG base layer (RPU ignored; libplacebo would be needed
  to apply it). DV profile 5 has no compatible base: flagged ``flag:dv_profile5_unsupported``.

**Mezzanine.** ffmpeg autorotate transposes (lossless) and strips the display matrix; ``setpts`` rebases the
first frame to 0; ``fps=…:round=near`` conforms VFR/jittery timestamps to the CFR grid by nearest frame;
``tpad`` + ``-frames:v N`` makes the frame count exactly ``MediaInfo.frame_count``. SDR colour conversions
(e.g. BT.601 or BT.2020-matrix sources, full range) run through zimg with spline36 chroma resampling —
higher quality than swscale's defaults. ProRes 422 HQ is encoded with Apple's own encoder
(``prores_videotoolbox``; measured +0.2 dB luma / +1.5 dB chroma PSNR over ``prores_ks`` at the same
profile and ~3x faster), falling back to ``prores_ks`` when VideoToolbox is unavailable or fails. FFV1
(lossless) and ProRes 422 are selectable. Sizes are large (ProRes HQ ≈ 26 MB/s at 1080p30, ≈ 210 MB/s at
2160x3840p60), so a disk guard estimates the size first and falls back HQ → 422 (still ~3x an iPhone HEVC
source's bitrate) only when HQ would not fit, flagging it; if 422 does not fit either, ingest refuses.

**Audio.** Decoded honouring edit lists and AAC priming; resampled with soxr (VHQ, ``precision=28``);
then a second ``aresample`` at 48 kHz aligns the stream to the first video frame with ``first_pts`` (pad
or trim, sample-exact) and hard-fills timestamp gaps ≥20 ms with silence so sync holds after dropped
packets. Doing the alignment in a separate 48k→48k stage matters: a single resampling ``aresample`` with
``first_pts`` misaligns by the resampler latency (~24 ms measured at 44.1 kHz). Stereo sources get a
*dialogue* downmix (:func:`dialogue_downmix`): coherent channels are delay-aligned (±2 ms) then averaged
(no comb filtering, +3 dB SNR on diffuse noise); a dead, near-silent or much weaker channel is dropped;
inverted polarity is corrected; uncorrelated channels with very different SNR keep the cleaner one.
No-audio sources get a silent track and ``flag:no_audio``.

**Pre-roll.** Creators often start talking on the first frame. Every deliverable's first ~21–44 ms of audio
is AAC encoder priming (the files carry no edit list), so a word on the first frame loses its onset and sits
over digital silence (invariant 9), and the doctrine wants the first word at 0.1–0.5 s. When the dialogue is
already at speech level in its first ``preroll_onset_ms`` (:func:`detect_speech_at_start`), ingest holds the
first frame for ``preroll_s`` (whole frames, ``tpad`` clone in the mezzanine) and puts the take's own room
tone under it (its quietest window, joined with a 5 ms equal-power crossfade). Everything downstream reads the
padded mezzanine/audio, so source times are consistent; ``flag:preroll`` and ``meta.ingest.preroll`` record
it. ``STUDIO_INGEST_PREROLL_S=0`` disables it.

**Proxy.** From the mezzanine (so it inherits rotation, tone map and CFR): Lanczos downscale to 540x960
(native aspect for non-9:16 sources, e.g. 960x540 for landscape, so normalised face boxes map 1:1),
x264 CRF 20 with 1 s GOPs for fast seeks, ``HH:MM:SS:FF`` timecode burned bottom-left (non-drop, same
counting as :func:`studio.timebase.format_timecode`) when ``drawtext`` is available.

Configuration: :class:`IngestOptions` / :class:`ToneMapConfig`; when no options are passed they are read
from ``STUDIO_TONEMAP_{OPERATOR,PARAM,DESAT,NPL,PEAK_NITS,DITHER}``, ``STUDIO_MEZZ_CODEC``
(``prores_hq`` | ``prores`` | ``ffv1``) and ``STUDIO_PRORES_ENCODER`` (``auto`` | ``prores_videotoolbox`` |
``prores_ks``). Every ffmpeg command line and its stderr is kept in ``logs/ingest_*.log``; the filter graphs,
encoder, tone-map parameters, downmix decision and verification report are recorded under
``job.json → meta.ingest``.
"""

from __future__ import annotations

import contextlib
import functools
import math
import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from studio.media.models import MediaInfo
from studio.media.probe import (
    FLAG_DV_UNSUPPORTED,
    FLAG_HDR_TONEMAPPED,
    FLAG_LANDSCAPE,
    FLAG_MEZZ_FALLBACK,
    FLAG_NEEDS_REFRAME,
    FLAG_NO_AUDIO,
    FLAG_VFR,
    ProbeError,
    ffprobe_json,
    has_flag,
    hdr_peak_nits,
    media_info_from_probe,
    probe_full,
    select_video_stream,
)
from studio.timebase import SAMPLE_RATE, frame_to_sample, us_to_sample

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job

__all__ = [
    "IngestError",
    "ToneMapConfig",
    "IngestOptions",
    "TONEMAP_OPERATORS",
    "MEZZ_CODECS",
    "ingest",
    "make_mezzanine",
    "extract_audio",
    "make_proxy",
    "verify_ingest",
    "mezz_video_filter",
    "mezz_codec_args",
    "estimate_mezz_bytes",
    "proxy_size",
    "dialogue_downmix",
    "stereo_audio_path",
    "FLAG_NO_AUDIO",
    "FLAG_NEEDS_REFRAME",
    "FLAG_LANDSCAPE",
    "FLAG_VFR",
    "FLAG_HDR_TONEMAPPED",
    "FLAG_DV_UNSUPPORTED",
    "FLAG_MEZZ_FALLBACK",
    "FLAG_PREROLL",
    "has_flag",
    "detect_speech_at_start",
]

FLAG_PREROLL = "flag:preroll"  # the first frame is held (with room tone) because speech starts on it

TONEMAP_OPERATORS: tuple[str, ...] = ("mobius", "hable", "reinhard", "clip", "linear", "gamma", "none")
_DEFAULT_TONEMAP_PARAM: dict[str, float] = {"mobius": 0.6}  # our tuned knee; others use ffmpeg's default
_DITHERS = ("none", "ordered", "random", "error_diffusion")

MezzCodec = Literal["prores_hq", "prores", "ffv1"]
MEZZ_CODECS: tuple[str, ...] = ("prores_hq", "prores", "ffv1")
#: approximate bitrates at 1920x1080 @ 29.97 (Apple ProRes white paper; FFV1 measured-ish), scaled by area × fps
_MEZZ_MBPS_1080P30: dict[str, float] = {"prores_hq": 220.0, "prores": 147.0, "ffv1": 600.0}

_MEZZ_PIX_FMT = "yuv422p10le"
_BT709_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
_SETPARAMS_709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"

# ffprobe colour vocabulary → zscale option values
_ZS_TRANSFER = {"bt709": "bt709", "gamma22": "bt470m", "gamma28": "bt470bg", "smpte170m": "smpte170m",
                "smpte240m": "smpte240m", "linear": "linear", "log100": "log100", "log316": "log316",
                "iec61966-2-4": "iec61966-2-4", "bt1361e": "bt709", "iec61966-2-1": "iec61966-2-1",
                "bt2020-10": "bt2020-10", "bt2020-12": "bt2020-12", "smpte2084": "smpte2084",
                "arib-std-b67": "arib-std-b67"}
_ZS_MATRIX = {"rgb": "gbr", "gbr": "gbr", "bt709": "bt709", "fcc": "fcc", "bt470bg": "bt470bg",
              "smpte170m": "smpte170m", "smpte240m": "smpte240m", "ycgco": "ycgco", "bt2020nc": "bt2020nc",
              "bt2020c": "bt2020c", "chroma-derived-nc": "chroma-derived-nc", "chroma-derived-c": "chroma-derived-c",
              "ictcp": "ictcp"}
_ZS_PRIMARIES = {"bt709": "bt709", "bt470m": "bt470m", "bt470bg": "bt470bg", "smpte170m": "smpte170m",
                 "smpte240m": "smpte240m", "film": "film", "bt2020": "bt2020", "smpte428": "smpte428",
                 "smpte431": "smpte431", "smpte432": "smpte432", "jedec-p22": "jedec-p22", "ebu3213": "ebu3213"}
_ZS_CHROMA = {"left", "center", "topleft", "top", "bottomleft", "bottom"}
_INTERLACED = {"tt", "bb", "tb", "bt"}  # ffprobe field_order values of interlaced streams

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
)


class IngestError(RuntimeError):
    """Ingest failed (ffmpeg error, verification failure, insufficient disk …)."""


# ============================================================================================ config
@dataclass(frozen=True)
class ToneMapConfig:
    """HDR → SDR tone-map settings (see module docstring for why these defaults).

    ``param`` None means the studio default for the operator (Möbius knee 0.6; ffmpeg's own default for
    the others). ``peak_nits`` overrides the source peak (HLG 1000, PQ MaxCLL/mastering max).
    """

    operator: str = "mobius"
    param: float | None = None
    desat: float = 2.0
    npl: float = 203.0
    peak_nits: float | None = None
    dither: str = "error_diffusion"

    def __post_init__(self) -> None:
        if self.operator not in TONEMAP_OPERATORS:
            raise ValueError(f"unknown tone-map operator {self.operator!r} (one of {', '.join(TONEMAP_OPERATORS)})")
        if not (self.npl > 0 and math.isfinite(self.npl)):
            raise ValueError("npl must be a positive number of cd/m²")
        if self.desat < 0:
            raise ValueError("desat must be >= 0")
        if self.dither not in _DITHERS:
            raise ValueError(f"dither must be one of {_DITHERS}")
        if self.peak_nits is not None and self.peak_nits <= 0:
            raise ValueError("peak_nits must be positive")

    @property
    def effective_param(self) -> float | None:
        return self.param if self.param is not None else _DEFAULT_TONEMAP_PARAM.get(self.operator)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ToneMapConfig:
        """Read ``STUDIO_TONEMAP_{OPERATOR,PARAM,DESAT,NPL,PEAK_NITS,DITHER}`` (unset → defaults)."""
        e = os.environ if env is None else env

        def num(name: str) -> float | None:
            v = (e.get(name) or "").strip()
            return float(v) if v else None

        kw: dict[str, Any] = {}
        if (op := (e.get("STUDIO_TONEMAP_OPERATOR") or "").strip().lower()):
            kw["operator"] = op
        for key, name in (("param", "STUDIO_TONEMAP_PARAM"), ("desat", "STUDIO_TONEMAP_DESAT"),
                          ("npl", "STUDIO_TONEMAP_NPL"), ("peak_nits", "STUDIO_TONEMAP_PEAK_NITS")):
            v = num(name)
            if v is not None:
                kw[key] = v
        if (d := (e.get("STUDIO_TONEMAP_DITHER") or "").strip().lower()):
            kw["dither"] = d
        return cls(**kw)

    def signal_peak(self, source_peak_nits: float | None) -> float:
        """Peak in linear units (1.0 = ``npl``) passed to ``tonemap``; never below 1."""
        nits = self.peak_nits or source_peak_nits or 1000.0
        return max(1.0, float(nits) / self.npl)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["effective_param"] = self.effective_param
        return d


@dataclass(frozen=True)
class IngestOptions:
    """Knobs for :func:`ingest` (defaults are the quality path)."""

    tonemap: ToneMapConfig = field(default_factory=ToneMapConfig)
    mezz_codec: str = "prores_hq"
    prores_encoder: str = "auto"  # auto | prores_videotoolbox | prores_ks
    allow_codec_fallback: bool = True  # HQ → ProRes 422 only when HQ would not fit on disk
    min_free_bytes: int = 512 << 20  # disk headroom kept free after all outputs
    resampler_precision: int = 28  # soxr precision bits (28 = VHQ)
    gap_fill_s: float = 0.020  # timestamp gaps ≥ this are filled with silence to keep sync
    proxy: bool = True
    proxy_crf: int = 20
    burn_timecode: bool = True
    link_original: bool = True
    verify: bool = True
    preroll_s: float = 0.15  # hold of the first frame when speech starts on it (0 disables)
    preroll_onset_ms: float = 80.0  # speech this close to the first frame triggers the pre-roll

    def __post_init__(self) -> None:
        if self.mezz_codec not in MEZZ_CODECS:
            raise ValueError(f"mezz_codec must be one of {MEZZ_CODECS}")
        if self.prores_encoder not in ("auto", "prores_videotoolbox", "prores_ks"):
            raise ValueError("prores_encoder must be auto, prores_videotoolbox or prores_ks")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> IngestOptions:
        """Defaults plus ``STUDIO_TONEMAP_*`` and ``STUDIO_MEZZ_CODEC``."""
        e = os.environ if env is None else env
        kw: dict[str, Any] = {"tonemap": ToneMapConfig.from_env(e)}
        if (c := (e.get("STUDIO_MEZZ_CODEC") or "").strip().lower()):
            kw["mezz_codec"] = c
        if (enc := (e.get("STUDIO_PRORES_ENCODER") or "").strip().lower()):
            kw["prores_encoder"] = enc
        if (pr := (e.get("STUDIO_INGEST_PREROLL_S") or "").strip()):
            with contextlib.suppress(ValueError):
                kw["preroll_s"] = max(0.0, float(pr))
        return cls(**kw)


# ============================================================================================ helpers
def _settings(settings: Settings | None) -> Settings:
    if settings is not None:
        return settings
    from studio.config import get_settings

    return get_settings()


def stereo_audio_path(job: Job) -> Path:
    """``media/audio_stereo.wav`` (present only for stereo/multichannel sources)."""
    return job.media_dir / "audio_stereo.wav"


def _tmp(path: Path) -> Path:
    return path.with_name(f".{path.stem}.tmp{path.suffix}")


def _run_ffmpeg(settings: Settings, args: Sequence[str], log_path: Path, *, what: str) -> None:
    cmd = [str(settings.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "warning", "-y", *args]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(_shell_quote(a) for a in cmd) + "\n")
        log.flush()
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, check=False)
        except FileNotFoundError as e:  # pragma: no cover - environment
            raise IngestError(f"{what}: ffmpeg not found ({settings.ffmpeg})") from e
        log.write(f"\n# exit {proc.returncode} in {time.monotonic() - t0:.1f}s\n")
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:])
        raise IngestError(f"{what} failed (exit {proc.returncode}); see {log_path}\n{tail}")


def _shell_quote(a: str) -> str:
    return a if a and all(c.isalnum() or c in "-_./:=,@+%" for c in a) else "'" + a.replace("'", "'\\''") + "'"


@functools.lru_cache(maxsize=8)
def _has_filter(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:  # pragma: no cover
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 2)


def _find_font() -> str | None:
    for f in _FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    return None


def _fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def _zs(mapping: Mapping[str, str], value: str | None) -> str | None:
    return mapping.get((value or "").lower()) if value else None


# ============================================================================================ filters
def mezz_video_filter(info: MediaInfo, *, tonemap: ToneMapConfig | None = None, peak_nits: float | None = None,
                      chroma_location: str | None = None, field_order: str | None = None,
                      pre_frames: int = 0) -> str:
    """The ``-vf`` graph that turns the (auto-rotated) original into the mezzanine picture.

    Every zscale output property is set explicitly: zscale's defaults are "same as the input frame", which
    silently skips conversions when frames carry tags that differ from the options. Explicitly interlaced,
    unrotated sources are deinterlaced first with ``bwdif`` (one frame per frame, so ``fps`` is unchanged).
    """
    c = info.color
    fps = info.fps
    parts = []
    if (field_order or "").lower() in _INTERLACED and info.rotation == 0:
        parts.append("bwdif=mode=send_frame:parity=auto:deint=all")
    pad = f"start={int(pre_frames)}:start_mode=clone:" if pre_frames > 0 else ""
    parts += ["setpts=PTS-STARTPTS", f"fps=fps={_fps_str(fps)}:round=near", f"tpad={pad}stop_mode=clone:stop=3"]
    size = ""
    if info.sar != 1:
        size = f":w={info.width}:h={info.height}"
    zin = []
    if (t := _zs(_ZS_TRANSFER, c.transfer)) is not None:
        zin.append(f"tin={t}")
    if (m := _zs(_ZS_MATRIX, c.matrix)) is not None:
        zin.append(f"min={m}")
    if (p := _zs(_ZS_PRIMARIES, c.primaries)) is not None:
        zin.append(f"pin={p}")
    if c.range in ("tv", "pc"):
        zin.append(f"rin={c.range}")
    if chroma_location and chroma_location.lower() in _ZS_CHROMA:
        zin.append(f"cin={chroma_location.lower()}")
    zin_s = ":".join(zin)
    if c.hdr:
        tm = tonemap or ToneMapConfig()
        peak = tm.signal_peak(peak_nits)
        op = tm.operator if peak > 1.0 + 1e-6 or tm.operator == "none" else "clip"
        prm = tm.effective_param
        tm_opts = f"tonemap={op}" + (f":param={prm:g}" if prm is not None and op == tm.operator else "")
        tm_opts += f":desat={tm.desat:g}:peak={peak:.6f}"
        if "tin=" not in zin_s:  # HDR always has a transfer after probing; keep the graph valid regardless
            zin_s = f"tin={'smpte2084' if c.hdr_format != 'hlg' else 'arib-std-b67'}" + (f":{zin_s}" if zin_s else "")
        parts += [
            f"zscale={zin_s}:t=linear:npl={tm.npl:g}:p=bt709:agamma=0:f=spline36{size}",
            "format=gbrpf32le",
            "setparams=color_primaries=bt709:color_trc=linear:colorspace=bt709",
            f"tonemap={tm_opts}",
            f"zscale=tin=linear:pin=bt709:t=bt709:p=bt709:m=bt709:r=tv:c=left:agamma=0:dither={tm.dither}"
            ":f=spline36",
            f"format={_MEZZ_PIX_FMT}",
        ]
    else:
        pre = (zin_s + ":") if zin_s else ""
        parts += [f"zscale={pre}t=bt709:p=bt709:m=bt709:r=tv:c=left:agamma=0:f=spline36{size}",
                  f"format={_MEZZ_PIX_FMT}"]
    if size:
        parts.append("setsar=1")
    parts.append(_SETPARAMS_709)
    return ",".join(parts)


@functools.lru_cache(maxsize=8)
def _has_encoder(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:  # pragma: no cover
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 2)


def mezz_codec_args(codec: str, *, encoder: str | None = None) -> list[str]:
    """Encoder arguments for a mezzanine codec (always 10-bit 4:2:2, BT.709 tagged).

    ProRes uses ``prores_videotoolbox`` (Apple's own encoder; measured +0.2 dB luma / +1.5 dB chroma PSNR
    over prores_ks at the same profile and ~3x faster) when ``encoder`` says so, else ``prores_ks``.
    """
    if codec in ("prores_hq", "prores"):
        profile = "3" if codec == "prores_hq" else "2"
        if encoder == "prores_videotoolbox":
            enc = ["-c:v", "prores_videotoolbox", "-profile:v", profile]
        else:
            enc = ["-c:v", "prores_ks", "-profile:v", profile, "-vendor", "apl0"]
    elif codec == "ffv1":
        enc = ["-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1", "-slices", "16",
               "-slicecrc", "1"]
    else:
        raise ValueError(f"unknown mezzanine codec {codec!r}")
    return [*enc, "-pix_fmt", _MEZZ_PIX_FMT, *_BT709_TAGS]


def _prores_encoders(ffmpeg: str, preference: str) -> list[str]:
    """Encoders to try in order for ProRes (``auto`` = VideoToolbox first when present)."""
    if preference == "prores_ks":
        return ["prores_ks"]
    if preference == "prores_videotoolbox":
        return ["prores_videotoolbox", "prores_ks"]
    return (["prores_videotoolbox"] if _has_encoder(ffmpeg, "prores_videotoolbox") else []) + ["prores_ks"]


def estimate_mezz_bytes(info: MediaInfo, codec: str) -> int:
    """Rough mezzanine size (bytes) for the disk guard: white-paper bitrate scaled by area and rate."""
    rate = _MEZZ_MBPS_1080P30[codec] * 1e6 * (info.width * info.height) / (1920 * 1080) \
        * float(info.fps) / (30000 / 1001)
    return int(rate / 8 * float(info.duration_s) * 1.05) + (1 << 20)


def proxy_size(width: int, height: int, *, long_side: int = 960, short_side: int = 540) -> tuple[int, int]:
    """Proxy dimensions: fit the display aspect inside 540x960 (portrait) / 960x540 (landscape), even."""
    if width <= 0 or height <= 0:
        raise ValueError("dimensions must be positive")
    box_w, box_h = (short_side, long_side) if height >= width else (long_side, short_side)
    scale = min(box_w / width, box_h / height)
    pw = max(2, round(width * scale / 2) * 2)
    ph = max(2, round(height * scale / 2) * 2)
    return min(pw, box_w), min(ph, box_h)


# ============================================================================================ downmix
def _frame_rms(x: np.ndarray, hop: int) -> np.ndarray:
    n = (len(x) // hop) * hop
    if n == 0:
        return np.sqrt(np.mean(np.square(x, dtype=np.float64))) * np.ones(1)
    return np.sqrt(np.mean(np.square(x[:n].reshape(-1, hop), dtype=np.float64), axis=1))


def _db(x: float) -> float:
    return 20.0 * math.log10(max(x, 1e-12))


def _xcorr(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalised cross-correlation ``r[k] = Σ a[i+k]·b[i] / (|a||b|)`` for lags ``-max_lag..max_lag``."""
    n = len(a)
    nfft = 1 << (int(2 * n - 1).bit_length())
    fa = np.fft.rfft(a.astype(np.float64), nfft)
    fb = np.fft.rfft(b.astype(np.float64), nfft)
    cc = np.fft.irfft(fa * np.conj(fb), nfft)
    lags = np.arange(-max_lag, max_lag + 1)
    vals = np.concatenate([cc[nfft - max_lag:], cc[:max_lag + 1]]) if max_lag > 0 else cc[:1]
    denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b))) or 1.0
    return lags, vals / denom


def _shift(x: np.ndarray, lag: int) -> np.ndarray:
    """Delay ``x`` by ``lag`` samples (negative = advance), zero-filled, same length."""
    if lag == 0:
        return x
    out = np.zeros_like(x)
    if lag > 0:
        out[lag:] = x[:-lag]
    else:
        out[:lag] = x[-lag:]
    return out


def dialogue_downmix(stereo: np.ndarray, sr: int = SAMPLE_RATE, *, max_lag_ms: float = 2.0,
                     analysis_s: float = 120.0) -> tuple[np.ndarray, dict[str, Any]]:
    """Mono dialogue from a ``(n, 2)`` float array. Returns ``(mono float32, decision)``.

    * a digitally silent or ≥20 dB quieter channel is dropped (lav on one channel, dead mic);
    * coherent channels (|r| ≥ 0.3 at the best lag within ±``max_lag_ms``) are polarity-corrected,
      delay-aligned to the left channel and averaged, unless one is ≥6 dB weaker (then the stronger alone);
    * incoherent channels keep the one with ≥6 dB better SNR, else are averaged (two voices, two mics).
    """
    x = np.asarray(stereo, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != 2:
        raise ValueError("expected an (n, 2) array")
    left, right = x[:, 0], x[:, 1]
    n = len(left)
    info: dict[str, Any] = {"mode": "", "corr": None, "lag_samples": 0, "level_diff_db": None,
                            "polarity_inverted": False}
    if n == 0:
        info["mode"] = "empty"
        return np.zeros(0, dtype=np.float32), info
    pk_l, pk_r = float(np.max(np.abs(left))), float(np.max(np.abs(right)))
    if pk_l == 0.0 and pk_r == 0.0:
        info["mode"] = "silent"
        return left.copy(), info
    if pk_r == 0.0 or pk_l == 0.0:
        info["mode"] = "left_only" if pk_r == 0.0 else "right_only"
        info["reason"] = "other channel is digitally silent"
        return (left if pk_r == 0.0 else right).copy(), info

    hop = max(1, sr // 50)  # 20 ms frames
    rl, rr = _frame_rms(left, hop), _frame_rms(right, hop)
    loud = np.maximum(rl, rr)
    thr = float(np.percentile(loud, 95)) * 10 ** (-30 / 20)
    active = loud > thr
    if active.sum() < 3:
        active = np.ones_like(loud, dtype=bool)
    lev_l = _db(float(np.sqrt(np.mean(np.square(rl[active])))))
    lev_r = _db(float(np.sqrt(np.mean(np.square(rr[active])))))
    diff = lev_l - lev_r
    info["level_diff_db"] = round(diff, 2)
    if abs(diff) >= 20.0:
        info["mode"] = "left_only" if diff > 0 else "right_only"
        info["reason"] = "other channel is near-silent (≥20 dB weaker)"
        return (left if diff > 0 else right).copy(), info

    # analysis excerpt: the active frames, up to analysis_s seconds
    idx = np.flatnonzero(active)[: int(analysis_s * sr / hop)]
    sel = (idx[:, None] * hop + np.arange(hop)[None, :]).reshape(-1)
    sel = sel[sel < n]
    a, b = left[sel], right[sel]
    max_lag = round(max_lag_ms * sr / 1000.0) if len(sel) > 4 * sr // 10 else 0
    lags, r = _xcorr(a, b, max_lag)
    k = int(np.argmax(np.abs(r)))
    best, lag = float(r[k]), int(lags[k])
    info["corr"] = round(best, 4)
    if abs(best) >= 0.3:
        sign = 1.0 if best > 0 else -1.0
        info["polarity_inverted"] = sign < 0
        if abs(diff) >= 6.0:
            info["mode"] = "left_only" if diff > 0 else "right_only"
            info["reason"] = f"coherent but {abs(diff):.1f} dB weaker channel adds noise"
            return (left if diff > 0 else right).copy(), info
        info["lag_samples"] = lag
        aligned = _shift(right, lag) * np.float32(sign)
        mono = (left + aligned) * np.float32(0.5)
        info["mode"] = "coherent_average"
        return mono.astype(np.float32), info
    # incoherent: compare SNR (active level vs 10th-percentile frame level)
    snr_l = lev_l - _db(float(np.percentile(rl, 10)))
    snr_r = lev_r - _db(float(np.percentile(rr, 10)))
    info["snr_db"] = [round(snr_l, 1), round(snr_r, 1)]
    if abs(snr_l - snr_r) >= 6.0:
        info["mode"] = "left_only" if snr_l > snr_r else "right_only"
        info["reason"] = "incoherent channels; kept the cleaner one"
        return (left if snr_l > snr_r else right).copy(), info
    info["mode"] = "incoherent_average"
    return ((left + right) * np.float32(0.5)).astype(np.float32), info


# ============================================================================================ stages
def _place_original(src: Path, job: Job, *, link: bool) -> Path:
    dest = job.media_dir / f"original{src.suffix.lower() or '.bin'}"
    if dest.exists() and dest.samefile(src):
        return dest
    for old in job.media_dir.glob("original.*"):
        old.unlink()
    if link:
        try:
            os.link(src, dest)
            return dest
        except OSError:
            pass
    shutil.copy2(src, dest)
    return dest


def _probe_data(job: Job, settings: Settings) -> dict[str, Any]:
    if job.probe_path.exists():
        return job.load_json("media/probe.json")
    orig = job.original_path
    if orig is None:
        raise IngestError(f"job {job.id} has no media/original.*")
    data = probe_full(orig, settings=settings)
    job.save_json("media/probe.json", data)
    return data


def _source_peak(info: MediaInfo, probe_data: Mapping[str, Any]) -> float | None:
    """Source peak luminance for the tone map (None for SDR)."""
    if not info.color.hdr:
        return None
    fmt = info.color.hdr_format
    return hdr_peak_nits(probe_data, "pq" if fmt in (None, "dolby_vision") else fmt)


def _vstream_field(probe_data: Mapping[str, Any], key: str) -> str | None:
    vs = select_video_stream(probe_data.get("streams") or [])
    return (vs or {}).get(key)


def _source_path(job: Job, info: MediaInfo) -> Path:
    orig = job.original_path
    if orig is not None:
        return orig
    p = Path(info.path)
    if p.is_file():
        return p
    raise IngestError(f"job {job.id}: original media not found")


def _make_mezzanine(job: Job, info: MediaInfo, settings: Settings, options: IngestOptions,
                    probe_data: Mapping[str, Any], *, pre_frames: int = 0) -> tuple[Path, list[str], dict[str, Any]]:
    src = _source_path(job, info)
    notes: list[str] = []
    codec = options.mezz_codec
    free = shutil.disk_usage(job.media_dir).free
    if job.mezz_path.exists():  # replaced by this run
        free += job.mezz_path.stat().st_size
    audio_bytes = int(float(info.duration_s) * SAMPLE_RATE * 4 * 3) + (8 << 20)
    need = estimate_mezz_bytes(info, codec) + audio_bytes + options.min_free_bytes
    if need > free:
        lighter = "prores" if codec == "prores_hq" else None
        if options.allow_codec_fallback and lighter and \
                estimate_mezz_bytes(info, lighter) + audio_bytes + options.min_free_bytes <= free:
            notes.append(f"{FLAG_MEZZ_FALLBACK} ProRes 422 HQ (~{estimate_mezz_bytes(info, codec) / 1e9:.1f} GB) "
                         f"would not fit in {free / 1e9:.1f} GB free; mezzanine written as ProRes 422")
            codec = lighter
        else:
            raise IngestError(f"insufficient disk space for the {codec} mezzanine: need ~{need / 1e9:.1f} GB, "
                              f"{free / 1e9:.1f} GB free in {job.media_dir}")
    peak = _source_peak(info, probe_data)
    field_order = _vstream_field(probe_data, "field_order")
    vf = mezz_video_filter(info, tonemap=options.tonemap, peak_nits=peak,
                           chroma_location=_vstream_field(probe_data, "chroma_location"), field_order=field_order,
                           pre_frames=pre_frames)
    if (field_order or "").lower() in _INTERLACED:
        notes.append(f"interlaced source (field order {field_order}): "
                     + ("deinterlaced with bwdif in the mezzanine" if info.rotation == 0
                        else "not deinterlaced (rotated interlaced video is unsupported)"))
    vidx = (probe_data.get("studio") or {}).get("video_stream_index")
    vmap = f"0:{vidx}" if vidx is not None else "0:v:0"
    out = job.mezz_path
    tmp = _tmp(out)
    encoders = _prores_encoders(str(settings.ffmpeg), options.prores_encoder) if codec != "ffv1" else ["ffv1"]
    t0 = time.monotonic()
    used = None
    for i, enc in enumerate(encoders):
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        args = ["-i", str(src), "-map", vmap, "-vf", vf, "-fps_mode", "passthrough",
                "-frames:v", str(info.frame_count), *mezz_codec_args(codec, encoder=enc), "-an", "-sn", "-dn",
                "-map_metadata", "-1", "-map_chapters", "-1", "-f", "mov", str(tmp)]
        try:
            _run_ffmpeg(settings, args, job.log_path("ingest_mezz" if i == 0 else f"ingest_mezz_{enc}"),
                        what=f"mezzanine encode ({enc})")
            used = enc
            break
        except IngestError:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            if i == len(encoders) - 1:
                raise
            notes.append(f"{enc} failed; mezzanine re-encoded with {encoders[i + 1]}")
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise
    os.replace(tmp, out)
    meta = {"codec": codec, "encoder": used, "filter": vf, "seconds": round(time.monotonic() - t0, 2),
            "bytes": out.stat().st_size, "peak_nits": peak}
    if info.color.hdr:
        meta["tonemap"] = options.tonemap.as_dict()
    return out, notes, meta


def make_mezzanine(job: Job, info: MediaInfo, *, settings: Settings | None = None,
                   options: IngestOptions | None = None) -> os.PathLike[str]:
    """Write ``media/mezz.mov`` from the original (rotation, tone map, CFR conform)."""
    s = _settings(settings)
    path, _notes, _meta = _make_mezzanine(job, info, s, options or IngestOptions.from_env(), _probe_data(job, s))
    return path


def _extract_audio(job: Job, info: MediaInfo, settings: Settings, options: IngestOptions
                   ) -> tuple[Path, list[str], dict[str, Any]]:
    import soundfile as sf

    out = job.audio_path
    stereo_out = stereo_audio_path(job)
    total = frame_to_sample(info.frame_count, info.fps)
    notes: list[str] = []
    meta: dict[str, Any] = {"samples": total, "sample_rate": SAMPLE_RATE}
    tmp = _tmp(out)
    if info.audio is None:
        sf.write(tmp, np.zeros(total, dtype=np.float32), SAMPLE_RATE, subtype="FLOAT", format="WAV")
        os.replace(tmp, out)
        with contextlib.suppress(FileNotFoundError):
            stereo_out.unlink()
        meta["mode"] = "generated_silence"
        return out, notes, meta

    src = _source_path(job, info)
    first_pts = us_to_sample(info.start_us)  # container time of the first video frame, in 48 kHz samples
    channels = 1 if (info.audio.channels or 1) == 1 else 2
    af = (f"aresample={SAMPLE_RATE}:resampler=soxr:precision={options.resampler_precision},"
          f"aresample={SAMPLE_RATE}:async=1:min_hard_comp={options.gap_fill_s:g}:first_pts={first_pts},"
          f"apad,atrim=end_sample={total}")
    raw = _tmp(stereo_out) if channels == 2 else tmp
    with contextlib.suppress(FileNotFoundError):
        raw.unlink()
    args = ["-copyts", "-i", str(src), "-map", f"0:{info.audio.stream_index}", "-vn", "-sn", "-dn",
            "-af", af, "-ac", str(channels), "-ar", str(SAMPLE_RATE), "-c:a", "pcm_f32le", "-rf64", "auto",
            "-f", "wav", str(raw)]
    try:
        _run_ffmpeg(settings, args, job.log_path("ingest_audio"), what="audio extract")
        meta.update({"filter": af, "first_pts_samples": first_pts, "source_channels": info.audio.channels})
        data, sr = sf.read(raw, dtype="float32", always_2d=True)
        if sr != SAMPLE_RATE or len(data) != total:
            raise IngestError(f"audio extract produced {len(data)} samples @ {sr} Hz, expected {total} @ {SAMPLE_RATE}")
        if channels == 2:
            mono, decision = dialogue_downmix(data, SAMPLE_RATE)
            sf.write(tmp, mono, SAMPLE_RATE, subtype="FLOAT", format="WAV")
            os.replace(raw, stereo_out)
            meta["downmix"] = decision
            desc = decision["mode"].replace("_", " ")
            extra = f", R aligned {decision['lag_samples']:+d} samples" if decision.get("lag_samples") else ""
            pol = ", polarity corrected" if decision.get("polarity_inverted") else ""
            notes.append(f"stereo dialogue downmix: {desc}{extra}{pol}"
                         + (f" ({decision['reason']})" if decision.get("reason") else ""))
        else:
            with contextlib.suppress(FileNotFoundError):
                stereo_out.unlink()
            mono = data[:, 0]
        if len(mono) == 0 or float(np.max(np.abs(mono))) == 0.0:
            notes.append("audio track is digitally silent")
        os.replace(tmp, out)
    except BaseException:
        for p in (tmp, raw):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        raise
    return out, notes, meta


def extract_audio(job: Job, info: MediaInfo, *, settings: Settings | None = None,
                  options: IngestOptions | None = None) -> os.PathLike[str]:
    """Write ``media/audio.wav`` (48 kHz float32 mono dialogue; honours edit lists and AAC priming)."""
    path, _notes, _meta = _extract_audio(job, info, _settings(settings), options or IngestOptions.from_env())
    return path


def detect_speech_at_start(audio: np.ndarray, sr: int = SAMPLE_RATE, *, onset_ms: float = 80.0,
                           analyse_s: float = 12.0, min_spread_db: float = 15.0, frac: float = 0.6) -> dict[str, Any]:
    """Is the dialogue already at speech level in its first ``onset_ms``? Levels are 10 ms RMS over the first
    ``analyse_s``: floor = 10th percentile, speech = 95th; the head counts as speech when it reaches
    ``floor + frac·(speech − floor)``. A take whose level spread is under ``min_spread_db`` (music, constant
    noise) is undetermined and reported as no speech."""
    x = np.asarray(audio, dtype=np.float64).reshape(-1)[: int(analyse_s * sr)]
    hop = max(1, sr // 100)
    n = len(x) // hop
    if n < 20:
        return {"speech_at_start": False, "reason": "too short"}
    db = 10.0 * np.log10(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    floor, loud = float(np.percentile(db, 10)), float(np.percentile(db, 95))
    head = float(db[: max(1, round(onset_ms / 10.0))].max())
    spread = loud - floor
    speech = bool(spread >= min_spread_db and head >= floor + frac * spread)
    return {"speech_at_start": speech, "floor_db": round(floor, 1), "speech_db": round(loud, 1),
            "head_db": round(head, 1), "spread_db": round(spread, 1)}


def _room_tone(x: np.ndarray, sr: int, n: int, *, win_s: float = 0.15, skip_s: float = 0.3,
               search_s: float = 30.0) -> np.ndarray:
    """``n`` samples of the take's own room tone: its quietest ``win_s`` window (tiled with equal-power joins
    when shorter than ``n``)."""
    x = np.asarray(x, dtype=np.float32)
    w = max(1, int(win_s * sr))
    lo, hi = min(len(x), int(skip_s * sr)), min(len(x), int(search_s * sr))
    if hi - lo < w:
        lo, hi = 0, len(x)
    if hi - lo < w:
        return np.zeros(n, np.float32)
    hop = max(1, sr // 100)
    starts = np.arange(lo, hi - w + 1, hop)
    energy = np.array([float(np.mean(x[a:a + w].astype(np.float64) ** 2)) for a in starts])
    a = int(starts[int(np.argmin(energy))])
    seg = x[a:a + w].copy()
    if n <= w:
        return seg[:n]
    xf = max(1, min(w // 4, int(0.01 * sr)))
    ramp = np.sin(np.linspace(0.0, np.pi / 2, xf, dtype=np.float32)) ** 2
    out = seg.copy()
    while len(out) < n:
        head = out[:-xf]
        join = out[-xf:] * np.sqrt(1.0 - ramp) + seg[:xf] * np.sqrt(ramp)
        out = np.concatenate([head, join, seg[xf:]])
    return out[:n]


def _prepend_room_tone(path: Path, pad: int, sr: int) -> None:
    """Prepend ``pad`` samples of room tone to a WAV (mono or multichannel), joined with a 5 ms equal-power
    crossfade into the original first samples."""
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if rate != sr:
        raise IngestError(f"{path.name}: {rate} Hz, expected {sr}")
    xf = min(max(1, int(0.005 * sr)), pad, len(data))
    chans = []
    t = np.linspace(0.0, np.pi / 2, xf, dtype=np.float32)
    fade_in, fade_out = np.sin(t) ** 2, np.cos(t) ** 2
    for c in range(data.shape[1]):
        x = data[:, c]
        tone = _room_tone(x, sr, pad + xf)
        head = tone[:pad].copy()
        body = x.copy()
        body[:xf] = x[:xf] * np.sqrt(fade_in) + tone[pad:pad + xf] * np.sqrt(fade_out)
        chans.append(np.concatenate([head, body]))
    out = np.stack(chans, axis=1)
    tmp = _tmp(path)
    sf.write(str(tmp), out if out.shape[1] > 1 else out[:, 0], sr, subtype="FLOAT", format="WAV")
    os.replace(tmp, path)


def _preroll(job: Job, info: MediaInfo, options: IngestOptions) -> tuple[int, dict[str, Any]]:
    """Frames of first-frame hold the take needs (0 when its dialogue does not start on the first frame)."""
    import soundfile as sf

    if options.preroll_s <= 0 or info.audio is None or not job.audio_path.exists():
        return 0, {"applied": False, "reason": "disabled" if options.preroll_s <= 0 else "no audio"}
    x, sr = sf.read(str(job.audio_path), dtype="float32", frames=int(12.5 * SAMPLE_RATE))
    det = detect_speech_at_start(x, sr, onset_ms=options.preroll_onset_ms)
    if not det.get("speech_at_start"):
        return 0, {"applied": False, **det}
    frames = max(1, math.ceil(options.preroll_s * float(info.fps) - 1e-9))
    return frames, {"applied": True, "frames": frames, **det}


def _padded_info(info: MediaInfo, frames: int) -> MediaInfo:
    """``info`` with ``frames`` more frames of duration (exactly ``frame_count + frames``)."""
    target = info.frame_count + frames
    dur = info.duration_us + round(frames / float(info.fps) * 1_000_000)
    for _ in range(64):
        cand = info.model_copy(update={"duration_us": dur})
        if cand.frame_count == target:
            return cand
        dur += 1 if cand.frame_count < target else -1
    raise IngestError("could not express the pre-roll as whole frames")


def _timecode_filter(info: MediaInfo, ph: int, ffmpeg: str) -> str | None:
    if not _has_filter(ffmpeg, "drawtext"):
        return None
    font = _find_font()
    size = max(12, round(ph * 0.026))
    face = f"fontfile='{font}'" if font else "font='monospace'"
    return (f"drawtext={face}:timecode='00\\:00\\:00\\:00':rate={_fps_str(info.fps)}:fontsize={size}"
            f":fontcolor=white:box=1:boxcolor=black@0.55:boxborderw={max(3, size // 5)}"
            f":x={max(6, size // 2)}:y=h-th-{max(8, size)}")


def _make_proxy(job: Job, info: MediaInfo, settings: Settings, options: IngestOptions
                ) -> tuple[Path, dict[str, Any]]:
    if not job.mezz_path.exists():
        raise IngestError("proxy needs the mezzanine; run make_mezzanine first")
    if not job.audio_path.exists():
        raise IngestError("proxy needs audio.wav; run extract_audio first")
    pw, ph = proxy_size(info.width, info.height)
    vf = f"scale={pw}:{ph}:flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp,format=yuv420p,setsar=1"
    tc = _timecode_filter(info, ph, str(settings.ffmpeg)) if options.burn_timecode else None
    if tc:
        vf += "," + tc
    vf += "," + _SETPARAMS_709
    gop = max(1, round(float(info.fps)))
    out = job.proxy_path
    tmp = _tmp(out)
    args = ["-i", str(job.mezz_path), "-i", str(job.audio_path), "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", str(options.proxy_crf), "-g", str(gop),
            "-bf", "2", "-pix_fmt", "yuv420p", *_BT709_TAGS, "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE),
            "-ac", "1", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        _run_ffmpeg(settings, args, job.log_path("ingest_proxy"), what="proxy encode")
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    os.replace(tmp, out)
    return out, {"size": [pw, ph], "timecode": bool(tc), "crf": options.proxy_crf, "gop": gop}


def make_proxy(job: Job, info: MediaInfo, *, settings: Settings | None = None,
               options: IngestOptions | None = None) -> os.PathLike[str]:
    """Write ``media/proxy.mp4`` (540x960 H.264, burned timecode) from the mezzanine."""
    path, _meta = _make_proxy(job, info, _settings(settings), options or IngestOptions.from_env())
    return path


# ============================================================================================ verify
def verify_ingest(job: Job, info: MediaInfo, *, settings: Settings | None = None,
                  check_proxy: bool = True) -> dict[str, Any]:
    """Check the written media against ``info``; raises :class:`IngestError` listing every failure."""
    import soundfile as sf

    s = _settings(settings)
    problems: list[str] = []
    report: dict[str, Any] = {}
    try:
        mz = ffprobe_json(job.mezz_path, settings=s)
    except ProbeError as e:
        raise IngestError(f"mezzanine unreadable: {e}") from e
    v = select_video_stream(mz.get("streams") or [])
    if v is None:
        raise IngestError("mezzanine has no video stream")
    got = {k: v.get(k) for k in ("codec_name", "width", "height", "pix_fmt", "color_primaries", "color_transfer",
                                 "color_space", "r_frame_rate", "nb_frames")}
    report["mezz"] = got
    if (int(v["width"]), int(v["height"])) != (info.width, info.height):
        problems.append(f"mezz size {v['width']}x{v['height']} != {info.width}x{info.height}")
    if v.get("pix_fmt") != _MEZZ_PIX_FMT:
        problems.append(f"mezz pix_fmt {v.get('pix_fmt')} != {_MEZZ_PIX_FMT}")
    for key in ("color_primaries", "color_transfer", "color_space"):
        if v.get(key) != "bt709":
            problems.append(f"mezz {key}={v.get(key)} (expected bt709)")
    if any("rotation" in sd for sd in v.get("side_data_list") or []):
        problems.append("mezz still carries a display matrix (double-rotation risk)")
    try:
        r = Fraction(str(v.get("r_frame_rate")))
    except (ValueError, ZeroDivisionError):
        r = Fraction(0)
    if r != info.fps:
        problems.append(f"mezz frame rate {v.get('r_frame_rate')} != {_fps_str(info.fps)}")
    nbf = int(v.get("nb_frames") or 0)
    if nbf and nbf != info.frame_count:
        problems.append(f"mezz has {nbf} frames, expected {info.frame_count}")
    ai = sf.info(str(job.audio_path))
    total = frame_to_sample(info.frame_count, info.fps)
    report["audio"] = {"samplerate": ai.samplerate, "channels": ai.channels, "frames": ai.frames,
                       "subtype": ai.subtype}
    if ai.samplerate != SAMPLE_RATE or ai.channels != 1 or ai.subtype != "FLOAT":
        problems.append(f"audio.wav is {ai.samplerate} Hz x{ai.channels} {ai.subtype}, expected 48000 Hz mono FLOAT")
    if ai.frames != total:
        problems.append(f"audio.wav has {ai.frames} samples, expected {total}")
    st = stereo_audio_path(job)
    if st.exists():
        si = sf.info(str(st))
        report["audio_stereo"] = {"channels": si.channels, "frames": si.frames}
        if si.channels != 2 or si.frames != total:
            problems.append(f"audio_stereo.wav is x{si.channels} / {si.frames} samples")
    if check_proxy and job.proxy_path.exists():
        pv = select_video_stream(ffprobe_json(job.proxy_path, settings=s).get("streams") or [])
        exp = proxy_size(info.width, info.height)
        report["proxy"] = {"size": [pv.get("width"), pv.get("height")] if pv else None,
                           "nb_frames": (pv or {}).get("nb_frames")}
        if pv is None or (int(pv["width"]), int(pv["height"])) != exp:
            problems.append(f"proxy size {report['proxy']['size']} != {list(exp)}")
        elif int(pv.get("nb_frames") or info.frame_count) != info.frame_count:
            problems.append(f"proxy has {pv.get('nb_frames')} frames, expected {info.frame_count}")
    if problems:
        raise IngestError("ingest verification failed: " + "; ".join(problems))
    report["ok"] = True
    return report


# ============================================================================================ ingest
def ingest(src_path: str | os.PathLike[str], job: Job, *, settings: Settings | None = None,
           options: IngestOptions | None = None) -> MediaInfo:
    """Run the full ingest and return the saved :class:`MediaInfo` (see module docstring)."""
    s = _settings(settings)
    opts = options or IngestOptions.from_env()
    src = Path(src_path).expanduser()
    if not src.is_file():
        raise IngestError(f"no such file: {src}")
    src = src.resolve()
    t0 = time.monotonic()
    original = _place_original(src, job, link=opts.link_original)
    try:
        data = probe_full(original, settings=s)
        job.save_json("media/probe.json", data)
        info = media_info_from_probe(data, original)
    except ProbeError as e:
        raise IngestError(str(e)) from e
    notes = list(info.notes)
    if info.color.hdr:
        tm = opts.tonemap
        peak = _source_peak(info, data)
        notes.append(f"{FLAG_HDR_TONEMAPPED} {info.color.hdr_format} → SDR BT.709 once at ingest "
                     f"({tm.operator}, param {tm.effective_param}, npl {tm.npl:g}, source peak {peak:g} cd/m²)")

    _wav, a_notes, a_meta = _extract_audio(job, info, s, opts)
    notes += a_notes
    pre_frames, pre_meta = _preroll(job, info, opts)
    if pre_frames:
        padded = _padded_info(info, pre_frames)
        pad = frame_to_sample(padded.frame_count, padded.fps) - frame_to_sample(info.frame_count, info.fps)
        _prepend_room_tone(job.audio_path, pad, SAMPLE_RATE)
        if stereo_audio_path(job).exists():
            _prepend_room_tone(stereo_audio_path(job), pad, SAMPLE_RATE)
        pre_meta.update({"samples": pad, "ms": round(pad / SAMPLE_RATE * 1000, 1)})
        notes.append(f"{FLAG_PREROLL} speech starts on the first frame: held it for {pre_frames} frames "
                     f"({pre_meta['ms']:.0f} ms) over the take's room tone, so the first word survives AAC "
                     "priming and lands after 0.1 s")
        info = padded
    _mezz, mz_notes, mz_meta = _make_mezzanine(job, info, s, opts, data, pre_frames=pre_frames)
    notes += mz_notes
    info = info.model_copy(update={"notes": notes})
    job.save_media_info(info)
    p_meta: dict[str, Any] | None = None
    if opts.proxy:
        _proxy, p_meta = _make_proxy(job, info, s, opts)
    else:
        with contextlib.suppress(FileNotFoundError):
            job.proxy_path.unlink()  # never leave a proxy of an older mezzanine behind
    report = verify_ingest(job, info, settings=s, check_proxy=opts.proxy) if opts.verify else {"ok": None}
    elapsed = round(time.monotonic() - t0, 2)
    job.update_meta(ingest={
        "source_path": str(src), "original": original.name, "seconds": elapsed,
        "flags": [n.split(" ", 1)[0] for n in notes if n.startswith("flag:")],
        "mezz": mz_meta, "audio": a_meta, "proxy": p_meta, "verify": report, "preroll": pre_meta,
    })
    job.trace("ingest", source=src.name, seconds=elapsed, width=info.width, height=info.height,
              fps=_fps_str(info.fps), frames=info.frame_count, hdr=info.color.hdr, vfr=info.vfr,
              flags=[n.split(" ", 1)[0] for n in notes if n.startswith("flag:")], mezz_codec=mz_meta["codec"])
    return info
