"""ffprobe wrapper → :class:`~studio.media.models.MediaInfo` (ARCHITECTURE §3).

:func:`probe` = :func:`probe_full` (ffprobe JSON + a few cheap measurements) → :func:`media_info_from_probe`
(a pure function, unit-testable without media). ``probe.json`` in the job is exactly what
:func:`probe_full` returns: the raw ``ffprobe -show_format -show_streams`` document plus one extra
top-level key, ``"studio"``, holding the measurements below (so every decision is reproducible).

Measurements (``probe_data["studio"]``)
---------------------------------------
* ``video_timing`` — presentation timestamps of every non-discarded video packet (no decoding):
  delta histogram, percentiles, first/last pts. Used for VFR detection and the CFR conform rate.
* ``first_video`` / ``first_audio`` — pts of the first *decoded* frame of the chosen streams (after
  MOV edit lists and AAC priming ``skip_samples`` are applied by libavformat/libavcodec) plus the first
  video frame's side data (Dolby Vision RPU, mastering display, content light level).
* ``audio_skip_samples`` — AAC priming signalled in the first audio packet's side data.
* ``edit_lists`` — MOV/MP4 ``elst`` boxes per track, parsed directly from the ``moov`` box.

Conventions
-----------
* **Geometry** is display geometry in square pixels: ``width``/``height`` after rotation (and after
  applying a non-square SAR, which ingest resamples to square pixels in the mezzanine).
* **Rotation** is the *clockwise* rotation, in degrees normalized to {0, 90, 180, 270}, that turns the
  coded picture upright. ffprobe reports the display-matrix angle counter-clockwise, so an iPhone
  portrait take (ffprobe ``rotation=-90``) has ``rotation=90``. The angle is computed from the matrix
  with any mirror removed (ffprobe calls a pure front-camera flip "180°"); mirroring is reported in the
  notes and applied by ffmpeg's autorotate, which also strips the matrix from the mezzanine.
* **Timeline zero** is the first presented video frame. ``MediaInfo.start_us`` is that frame's
  container time; ``AudioInfo.start_us`` is the container time of the first decoded audio sample. Ingest
  aligns ``audio.wav`` so its sample 0 is timeline zero.
* **fps** is the CFR rate of the mezzanine. CFR sources keep their exact rate (mean of the regular frame
  intervals, cleaned by :func:`studio.timebase.normalize_fps`: 29.97 stays ``30000/1001``). VFR sources
  (more than 1 % of intervals off the median by >10 %) are conformed to the *nominal* capture rate: the
  5th-percentile frame interval snapped with :func:`studio.timebase.nearest_standard_fps` (preferring
  ``r_frame_rate`` when it agrees within 5 %). Using the short intervals rather than the average keeps
  every captured frame when a phone drops its rate in low light (30 → 24/15 fps) instead of halving
  motion resolution.
* **duration_us** is the conformed timeline length: ``round(source_video_duration × fps)`` frames at
  ``fps`` (within half a frame of the source), so ``MediaInfo.frame_count`` equals the mezzanine's frame
  count and ``audio.wav`` has exactly the matching number of samples.
* **HDR**: ``color_transfer`` ``arib-std-b67`` (HLG) or ``smpte2084`` (PQ), or Dolby Vision (DOVI
  configuration record / RPU side data). iPhone DV 8.4 carries an HLG base layer, so it is reported as
  ``hdr_format="hlg", dolby_vision=True`` and tone-mapped from that base. DV profile 5 has no backward
  compatible base layer and cannot be tone-mapped correctly without libplacebo: it is flagged.
* **Audio**: the AAC stream is chosen explicitly (default disposition, then codec, then channels); APAC
  (Apple spatial audio) and data/timecode tracks are never selected.

Flags (strings in ``MediaInfo.notes`` starting with ``flag:``; test with :func:`has_flag`) tell later
stages what ingest had to do: ``flag:no_audio``, ``flag:needs_reframe``, ``flag:landscape``,
``flag:vfr_conformed``, ``flag:hdr_tonemapped``, ``flag:dv_profile5_unsupported``,
``flag:mezz_codec_fallback``.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.media.models import HDR_TRANSFERS, AudioInfo, ColorInfo, MediaInfo
from studio.timebase import (
    frame_count,
    nearest_standard_fps,
    normalize_fps,
    seconds_to_us,
    to_fraction,
)

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings

__all__ = [
    "FLAG_NO_AUDIO",
    "FLAG_NEEDS_REFRAME",
    "FLAG_LANDSCAPE",
    "FLAG_VFR",
    "FLAG_HDR_TONEMAPPED",
    "FLAG_DV_UNSUPPORTED",
    "FLAG_MEZZ_FALLBACK",
    "ProbeError",
    "has_flag",
    "flags",
    "ffprobe_json",
    "probe_full",
    "measure",
    "media_info_from_probe",
    "probe",
    "select_video_stream",
    "select_audio_stream",
    "rotation_from_stream",
    "timing_summary",
    "decide_fps",
    "parse_mp4_edit_lists",
    "hdr_peak_nits",
    "dovi_config",
    "VFR_IRREGULAR_SHARE",
]

FLAG_NO_AUDIO = "flag:no_audio"  # source has no usable audio; audio.wav is generated silence
FLAG_NEEDS_REFRAME = "flag:needs_reframe"  # display aspect is not 9:16; the A-roll must be reframed
FLAG_LANDSCAPE = "flag:landscape"  # width > height
FLAG_VFR = "flag:vfr_conformed"  # variable frame rate conformed to CFR ``fps`` in the mezzanine
FLAG_HDR_TONEMAPPED = "flag:hdr_tonemapped"  # HLG/PQ tone-mapped to SDR BT.709 once, at ingest
FLAG_DV_UNSUPPORTED = "flag:dv_profile5_unsupported"  # DV without compatible base layer (colours unreliable)
FLAG_MEZZ_FALLBACK = "flag:mezz_codec_fallback"  # disk space forced a lighter mezzanine codec

#: share of frame intervals that must deviate (>10 % from the median) for a stream to count as VFR
VFR_IRREGULAR_SHARE = 0.01
_JITTER_REL = Fraction(1, 10)  # interval within ±10 % of the median (or ±1 tick) = regular jitter
_NINE_SIXTEEN = Fraction(9, 16)
_ASPECT_TOL = Fraction(1, 100)

_MOV_FORMATS = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")
_NEVER_AUDIO = {"apac"}  # Apple Positional Audio Codec (spatial audio companion track)
_SD_MAX_HEIGHT = 576
_MAX_PRIMING = 4096  # AAC encoder delay is 1024–2112 samples; larger edit offsets are trims


class ProbeError(RuntimeError):
    """ffprobe failed or the file has no usable video stream."""


def flags(info_or_notes: MediaInfo | Iterable[str]) -> list[str]:
    notes = info_or_notes.notes if isinstance(info_or_notes, MediaInfo) else list(info_or_notes)
    return [n.split(" ", 1)[0] for n in notes if n.startswith("flag:")]


def has_flag(info_or_notes: MediaInfo | Iterable[str], flag: str) -> bool:
    """True when ``flag`` (e.g. :data:`FLAG_NO_AUDIO`) is among the notes."""
    return flag in flags(info_or_notes)


# ============================================================================================ running
def _bin(settings: Settings | None, name: str) -> str:
    if settings is not None:
        return str(getattr(settings, name))
    from studio.config import get_settings

    return str(getattr(get_settings(), name))


def _run(cmd: Sequence[str], *, what: str) -> str:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, check=False)
    except FileNotFoundError as e:  # pragma: no cover - environment
        raise ProbeError(f"{what}: executable not found ({cmd[0]})") from e
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise ProbeError(f"{what} failed (exit {proc.returncode}): {tail}")
    return proc.stdout


def ffprobe_json(path: str | os.PathLike[str], *, settings: Settings | None = None) -> dict[str, Any]:
    """Raw ``ffprobe -show_format -show_streams`` JSON for ``path`` (stream side data included)."""
    p = Path(path)
    if not p.is_file():
        raise ProbeError(f"no such file: {p}")
    out = _run([_bin(settings, "ffprobe"), "-v", "error", "-hide_banner", "-print_format", "json",
                "-show_format", "-show_streams", str(p)], what="ffprobe")
    data = json.loads(out or "{}")
    if not data.get("streams"):
        raise ProbeError(f"ffprobe found no streams in {p}")
    return data


def _frames_json(path: Path, stream_index: int, entries: str, interval: str, settings: Settings | None,
                 *, packets: bool = False) -> list[dict[str, Any]]:
    kind = "-show_packets" if packets else "-show_frames"
    out = _run([_bin(settings, "ffprobe"), "-v", "error", "-hide_banner", "-print_format", "json",
                "-select_streams", str(stream_index), "-read_intervals", interval, kind,
                "-show_entries", entries, str(path)], what="ffprobe frames")
    data = json.loads(out or "{}")
    return list(data.get("packets" if packets else "frames", []) or [])


def _video_packets(path: Path, stream_index: int, settings: Settings | None) -> tuple[list[int], list[int]]:
    """``(pts, durations)`` of the non-discarded packets of one video stream, sorted by pts (ticks)."""
    out = _run([_bin(settings, "ffprobe"), "-v", "error", "-hide_banner", "-select_streams", str(stream_index),
                "-show_entries", "packet=pts,dts,duration,flags", "-of", "csv=p=0", str(path)],
               what="ffprobe packets")
    rows: list[tuple[int, int]] = []
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 4:
            continue
        pts_s, dts_s, dur_s, fl = parts[0], parts[1], parts[2], parts[3]
        if "D" in fl:  # discarded by an edit list
            continue
        t = pts_s if pts_s not in ("", "N/A") else dts_s
        if t in ("", "N/A"):
            continue
        rows.append((int(t), int(dur_s) if dur_s not in ("", "N/A") else 0))
    rows.sort()
    return [r[0] for r in rows], [r[1] for r in rows]


# ============================================================================================ pure helpers
def _disp(stream: Mapping[str, Any], key: str) -> int:
    return int((stream.get("disposition") or {}).get(key, 0) or 0)


def select_video_stream(streams: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The main picture stream: a video stream that is not cover art, default disposition first."""
    cands = [s for s in streams if s.get("codec_type") == "video" and not _disp(s, "attached_pic")
             and not _disp(s, "still_image") and int(s.get("width") or 0) > 0]
    if not cands:
        return None
    return sorted(cands, key=lambda s: (-_disp(s, "default"), int(s.get("index", 0))))[0]


def select_audio_stream(streams: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The dialogue stream: never APAC; default disposition, then AAC/PCM/ALAC, then ≤2 channels, then bitrate."""
    cands = [s for s in streams if s.get("codec_type") == "audio"
             and (s.get("codec_name") or "").lower() not in _NEVER_AUDIO
             and int(s.get("channels") or 0) > 0 and (s.get("codec_name") or "none") != "none"]
    if not cands:
        return None

    def codec_rank(s: Mapping[str, Any]) -> int:
        c = (s.get("codec_name") or "").lower()
        if c == "aac":
            return 0
        if c.startswith("pcm_") or c in ("alac", "flac"):
            return 1
        return 2

    def key(s: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
        ch = int(s.get("channels") or 0)
        return (-_disp(s, "default"), codec_rank(s), 0 if ch <= 2 else 1, -int(s.get("bit_rate") or 0),
                int(s.get("index", 0)))

    return sorted(cands, key=key)[0]


def _side_data(obj: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return list((obj or {}).get("side_data_list") or [])


def _parse_displaymatrix(matrix: str | None) -> list[int] | None:
    """The 9 entries (row-major) of ffprobe's display-matrix text dump, or None."""
    if not matrix:
        return None
    nums = [int(x) for x in re.findall(r"-?\d+", matrix)]
    vals = [n for i, n in enumerate(nums) if i % 4 != 0]  # drop the "0000000k:" row labels
    return vals if len(vals) == 9 else None


def rotation_from_stream(stream: Mapping[str, Any]) -> tuple[int, bool]:
    """``(clockwise_rotation in {0,90,180,270}, mirrored)`` from display-matrix side data (or the legacy
    ``rotate`` tag).

    The angle is computed from the matrix itself with any mirror removed first: ffprobe's ``rotation``
    for a mirrored matrix is misleading (a pure horizontal flip reads as 180°).
    """
    ccw: float | None = None
    flip = False
    for sd in _side_data(stream):
        if "rotation" not in sd and "displaymatrix" not in sd:
            continue
        m = _parse_displaymatrix(sd.get("displaymatrix"))
        if m is not None and (m[0] or m[3]) and (m[1] or m[4]):
            flip = m[0] * m[4] - m[1] * m[3] < 0
            if flip:  # undo av_display_matrix_flip(hflip): negate the first column
                m[0], m[3], m[6] = -m[0], -m[3], -m[6]
            s0, s1 = math.hypot(m[0], m[3]), math.hypot(m[1], m[4])
            ccw = -math.degrees(math.atan2(m[1] / s1, m[0] / s0))  # av_display_rotation_get()
        else:
            try:
                ccw = float(sd["rotation"])
            except (KeyError, TypeError, ValueError):
                ccw = None
        break
    if ccw is None:
        tag = (stream.get("tags") or {}).get("rotate")
        if tag is not None:
            try:
                ccw = -float(tag)  # legacy tag is clockwise
            except ValueError:
                ccw = None
    if ccw is None or not math.isfinite(ccw):
        return 0, flip
    cw = round(-ccw / 90.0) * 90 % 360
    return cw, flip


def _pct(sorted_vals: Sequence[int], q: float) -> int:
    if not sorted_vals:
        return 0
    k = min(len(sorted_vals) - 1, max(0, math.floor(q * (len(sorted_vals) - 1) + 0.5)))
    return int(sorted_vals[k])


def timing_summary(pts: Sequence[int], time_base: Fraction | str, *, last_duration: int | None = None,
                   max_hist: int = 64) -> dict[str, Any]:
    """Summarize sorted presentation timestamps (stream ``time_base`` ticks) for :func:`decide_fps`."""
    tb = to_fraction(time_base)
    p = sorted(int(x) for x in pts)
    deltas = [b - a for a, b in pairwise(p) if b > a]
    sd = sorted(deltas)
    hist = Counter(deltas)
    med = _pct(sd, 0.5)
    tol = max(1, int(med * _JITTER_REL)) if med else 1
    regular = [d for d in deltas if abs(d - med) <= tol]
    irregular = len(deltas) - len(regular)
    if last_duration is None or last_duration <= 0:
        last_duration = med
    return {
        "time_base": f"{tb.numerator}/{tb.denominator}",
        "n_frames": len(p),
        "first_pts": p[0] if p else None,
        "last_pts": p[-1] if p else None,
        "last_duration": int(last_duration or 0),
        "median": med,
        "p01": _pct(sd, 0.01),
        "p05": _pct(sd, 0.05),
        "p95": _pct(sd, 0.95),
        "max": sd[-1] if sd else 0,
        "regular_sum": int(sum(regular)),
        "regular_count": len(regular),
        "irregular_count": irregular,
        "hist": {str(k): v for k, v in hist.most_common(max_hist)},
    }


def decide_fps(timing: Mapping[str, Any] | None, r_fps: Fraction | None, avg_fps: Fraction | None
               ) -> tuple[Fraction, bool, str]:
    """``(cfr_fps, vfr, reason)`` from a :func:`timing_summary` (falls back to stream rates)."""
    if timing and timing.get("n_frames", 0) >= 3 and timing.get("median"):
        tb = to_fraction(timing["time_base"])
        n_int = timing["regular_count"] + timing["irregular_count"]
        irregular_share = timing["irregular_count"] / max(1, n_int)
        if irregular_share <= VFR_IRREGULAR_SHARE and timing["regular_count"] > 0:
            mean_delta = Fraction(timing["regular_sum"], timing["regular_count"]) * tb
            fps = normalize_fps(1 / mean_delta)
            return fps, False, f"cfr: mean regular interval → {float(fps):.4f} fps"
        nominal = normalize_fps(1 / (Fraction(timing["p05"]) * tb))
        fps = nearest_standard_fps(nominal)
        if r_fps:
            r = normalize_fps(r_fps)
            if abs(r - nominal) / nominal <= Fraction(5, 100) and nearest_standard_fps(r) == r:
                fps = r
        return fps, True, (f"vfr: {irregular_share:.1%} irregular intervals; nominal {float(nominal):.3f} fps "
                           f"(5th-pct interval) → conform to {float(fps):.4f}")
    for cand in (r_fps, avg_fps):
        if cand:
            return normalize_fps(cand), False, "stream frame rate (no packet timing)"
    raise ProbeError("cannot determine the video frame rate")


def _frac_or_none(v: Any) -> Fraction | None:
    if v in (None, "", "N/A", "0/0"):
        return None
    try:
        f = to_fraction(str(v))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def _time_or_none(v: Any) -> Fraction | None:
    """A (possibly zero or negative) ffprobe time in seconds, or None."""
    if v in (None, "", "N/A"):
        return None
    try:
        return to_fraction(str(v))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _bit_depth(stream: Mapping[str, Any]) -> int | None:
    raw = _int_or_none(stream.get("bits_per_raw_sample"))
    if raw:
        return raw
    pf = stream.get("pix_fmt") or ""
    m = re.search(r"p(\d{2})(le|be)?$", pf)
    if m:
        return int(m.group(1))
    if pf:
        return 8
    return None


def dovi_config(stream: Mapping[str, Any]) -> dict[str, Any] | None:
    """The Dolby Vision configuration record from stream side data, or None."""
    for sd in _side_data(stream):
        t = str(sd.get("side_data_type", "")).lower()
        if "dovi" in t or "dolby vision configuration" in t:
            return dict(sd)
    return None


def _has_dv_rpu(frame_side: Iterable[Mapping[str, Any]]) -> bool:
    return any("dolby vision" in str(sd.get("side_data_type", "")).lower() for sd in frame_side)


def _luminance(v: Any) -> float | None:
    f = _frac_or_none(v)
    return float(f) if f is not None else None


def hdr_peak_nits(probe_data: Mapping[str, Any], hdr_format: str | None = None) -> float | None:
    """Source peak luminance (cd/m²) for the tone map: PQ → MaxCLL, else mastering-display max, else
    1000; HLG → 1000 (the nominal display peak HLG is defined against). None for SDR."""
    streams = probe_data.get("streams", [])
    vs = select_video_stream(streams)
    if vs is None:
        return None
    if hdr_format is None:
        hdr_format = HDR_TRANSFERS.get((vs.get("color_transfer") or "").lower())
    if hdr_format is None:
        return None
    if hdr_format == "hlg":
        return 1000.0
    side = list(_side_data(vs)) + list(((probe_data.get("studio") or {}).get("first_video") or {})
                                        .get("side_data", []))
    cll = mdl = None
    for sd in side:
        t = str(sd.get("side_data_type", "")).lower()
        if "content light level" in t and cll is None:
            v = _float_or_none(sd.get("max_content"))
            cll = v if v and v > 0 else None
        if "mastering display" in t and mdl is None:
            v = _luminance(sd.get("max_luminance"))
            mdl = v if v and v > 0 else None
    peak = cll or mdl or 1000.0
    return float(min(10000.0, max(100.0, peak)))


# ============================================================================================ MP4 elst
_CONTAINER_BOXES = {b"moov", b"trak", b"mdia", b"edts", b"minf", b"stbl"}


def _iter_boxes(buf: bytes, start: int, end: int) -> Iterable[tuple[bytes, int, int]]:
    """Yield ``(type, payload_start, box_end)`` for boxes in ``buf[start:end]``."""
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack(">I4s", buf[pos:pos + 8])
        hdr = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            return
        yield btype, pos + hdr, pos + size
        pos += size


def _read_moov(path: Path, max_bytes: int = 64 << 20) -> bytes | None:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_end = fh.tell()
        pos = 0
        while pos + 8 <= file_end:
            fh.seek(pos)
            hdr = fh.read(16)
            if len(hdr) < 8:
                return None
            size, btype = struct.unpack(">I4s", hdr[:8])
            hlen = 8
            if size == 1:
                if len(hdr) < 16:
                    return None
                size = struct.unpack(">Q", hdr[8:16])[0]
                hlen = 16
            elif size == 0:
                size = file_end - pos
            if size < hlen:
                return None
            if btype == b"moov":
                if size > max_bytes:
                    return None
                fh.seek(pos)
                return fh.read(size)
            pos += size
    return None


def parse_mp4_edit_lists(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Edit lists of every track in a MOV/MP4 file (empty list if none / not an ISO-BMFF file).

    Each item: ``{"track_id", "handler", "media_timescale", "movie_timescale", "entries":
    [{"segment_duration", "media_time", "rate"}]}`` (segment durations in movie timescale, media
    times in the track's media timescale; ``media_time == -1`` is an empty edit, i.e. a delay).
    """
    try:
        moov = _read_moov(Path(path))
    except OSError:
        return []
    if not moov:
        return []
    out: list[dict[str, Any]] = []
    movie_ts: int | None = None
    for btype, ps, pe in _iter_boxes(moov, 8 if moov[4:8] == b"moov" else 0, len(moov)):
        if btype == b"mvhd":
            ver = moov[ps]
            movie_ts = struct.unpack(">I", moov[ps + 20:ps + 24] if ver == 1 else moov[ps + 12:ps + 16])[0]
        if btype != b"trak":
            continue
        track: dict[str, Any] = {"track_id": None, "handler": None, "media_timescale": None, "entries": None}
        for t2, p2, e2 in _iter_boxes(moov, ps, pe):
            if t2 == b"tkhd":
                ver = moov[p2]
                off = p2 + (20 if ver == 1 else 12)
                track["track_id"] = struct.unpack(">I", moov[off:off + 4])[0]
            elif t2 == b"edts":
                for t3, p3, _e3 in _iter_boxes(moov, p2, e2):
                    if t3 != b"elst":
                        continue
                    ver = moov[p3]
                    n = struct.unpack(">I", moov[p3 + 4:p3 + 8])[0]
                    pos = p3 + 8
                    entries = []
                    for _ in range(min(n, 4096)):
                        if ver == 1:
                            dur, mt = struct.unpack(">Qq", moov[pos:pos + 16])
                            pos += 16
                        else:
                            dur, mt = struct.unpack(">Ii", moov[pos:pos + 8])
                            pos += 8
                        ri, rf = struct.unpack(">hh", moov[pos:pos + 4])
                        pos += 4
                        entries.append({"segment_duration": int(dur), "media_time": int(mt),
                                        "rate": ri + rf / 65536.0})
                    track["entries"] = entries
            elif t2 == b"mdia":
                for t3, p3, _e3 in _iter_boxes(moov, p2, e2):
                    if t3 == b"mdhd":
                        ver = moov[p3]
                        off = p3 + (20 if ver == 1 else 12)
                        track["media_timescale"] = struct.unpack(">I", moov[off:off + 4])[0]
                    elif t3 == b"hdlr":
                        track["handler"] = moov[p3 + 8:p3 + 12].decode("latin-1")
        track["movie_timescale"] = movie_ts
        if track["entries"] is not None:
            out.append(track)
    return out


def _edit_is_trivial(track: Mapping[str, Any]) -> bool:
    ents = track.get("entries") or []
    return len(ents) == 1 and ents[0]["media_time"] == 0 and abs(ents[0]["rate"] - 1.0) < 1e-9


# ============================================================================================ measure
def measure(path: str | os.PathLike[str], probe_data: Mapping[str, Any], *,
            settings: Settings | None = None) -> dict[str, Any]:
    """Cheap measurements beyond ``-show_streams`` (see module docstring); stored as ``probe["studio"]``."""
    p = Path(path)
    streams = probe_data.get("streams", [])
    vs = select_video_stream(streams)
    if vs is None:
        raise ProbeError(f"no video stream in {p}")
    out: dict[str, Any] = {"video_stream_index": int(vs["index"])}
    a_s = select_audio_stream(streams)
    out["audio_stream_index"] = int(a_s["index"]) if a_s is not None else None

    # video packet timing (no decode)
    pts, durs = _video_packets(p, int(vs["index"]), settings)
    if pts:
        out["video_timing"] = timing_summary(pts, vs.get("time_base") or "1/90000",
                                             last_duration=durs[-1] if durs else None)
    # first decoded video frame (+ side data)
    frames = _frames_json(p, int(vs["index"]), "frame=pts,best_effort_timestamp:frame_side_data", "%+#6",
                          settings)
    fpts = [_int_or_none(f.get("pts")) if f.get("pts") is not None else _int_or_none(f.get("best_effort_timestamp"))
            for f in frames]
    fpts_valid = [x for x in fpts if x is not None]
    first_side = list(frames[0].get("side_data_list") or []) if frames else []
    out["first_video"] = {
        "pts": min(fpts_valid) if fpts_valid else (pts[0] if pts else None),
        "time_base": vs.get("time_base"),
        "side_data": [{k: v for k, v in sd.items() if k != "data"} for sd in first_side],
    }
    if a_s is not None:
        aframes = _frames_json(p, int(a_s["index"]), "frame=pts,best_effort_timestamp,nb_samples", "%+2",
                               settings)
        apts = [_int_or_none(f.get("pts")) if f.get("pts") is not None else _int_or_none(f.get("best_effort_timestamp"))
                for f in aframes]
        apts_v = [x for x in apts if x is not None]
        out["first_audio"] = {"pts": min(apts_v) if apts_v else _int_or_none(a_s.get("start_pts")),
                              "time_base": a_s.get("time_base")}
        pk = _frames_json(p, int(a_s["index"]), "packet=pts,flags:packet_side_data", "%+#4", settings,
                          packets=True)
        skip = 0
        for pkt in pk:
            for sd in pkt.get("side_data_list") or []:
                if "skip_samples" in sd:
                    skip = max(skip, int(sd.get("skip_samples") or 0))
        out["audio_skip_samples"] = skip or None
    fmt = (probe_data.get("format") or {}).get("format_name") or ""
    if any(x in fmt.split(",") for x in _MOV_FORMATS):
        out["edit_lists"] = parse_mp4_edit_lists(p)
    return out


def probe_full(path: str | os.PathLike[str], *, settings: Settings | None = None) -> dict[str, Any]:
    """ffprobe JSON plus :func:`measure` results under ``"studio"`` (this is what ``probe.json`` holds)."""
    data = ffprobe_json(path, settings=settings)
    data["studio"] = measure(path, data, settings=settings)
    return data


# ============================================================================================ MediaInfo
def _ts_us(pts: int | None, time_base: Any) -> int | None:
    if pts is None or time_base in (None, "", "0/0"):
        return None
    return seconds_to_us(Fraction(pts) * to_fraction(str(time_base)))


def _color_info(vs: Mapping[str, Any], studio: Mapping[str, Any], notes: list[str]) -> ColorInfo:
    def tag(key: str) -> str | None:
        v = (vs.get(key) or "").strip().lower()
        return None if v in ("", "unknown", "unspecified", "reserved") else v

    primaries, transfer, matrix = tag("color_primaries"), tag("color_transfer"), tag("color_space")
    rng = tag("color_range")
    rng_lit = rng if rng in ("tv", "pc") else None
    pix_fmt = vs.get("pix_fmt")
    if pix_fmt and pix_fmt.startswith("yuvj"):
        rng_lit = "pc"
    depth = _bit_depth(vs)

    frame_side = list((studio.get("first_video") or {}).get("side_data") or [])
    dv = dovi_config(vs)
    dv_rpu = _has_dv_rpu(frame_side)
    codec_tag = (vs.get("codec_tag_string") or "").lower()
    dolby_vision = dv is not None or dv_rpu or codec_tag in ("dvh1", "dvhe", "dav1", "dva1", "dvav")

    hdr, fmt = ColorInfo.hdr_from_transfer(transfer)
    if dolby_vision:
        profile = _int_or_none((dv or {}).get("dv_profile"))
        compat = _int_or_none((dv or {}).get("dv_bl_signal_compatibility_id"))
        if not hdr:
            if compat == 4:
                transfer, hdr, fmt = "arib-std-b67", True, "hlg"
                notes.append("Dolby Vision base layer is HLG (compatibility id 4) but untagged: using HLG")
            elif compat in (1, 6):
                transfer, hdr, fmt = "smpte2084", True, "pq"
                notes.append(f"Dolby Vision base layer is PQ (compatibility id {compat}) but untagged: using PQ")
            elif profile == 5 or compat == 0:
                transfer, hdr, fmt = transfer or "smpte2084", True, "dolby_vision"
                notes.append(f"{FLAG_DV_UNSUPPORTED} Dolby Vision profile {profile} has no backward-compatible "
                             "base layer; tone-mapped as PQ without the RPU (colours may be wrong)")
        desc = f"profile {profile}" if profile is not None else "RPU"
        notes.append(f"Dolby Vision {desc} present: tone-mapped from the "
                     f"{fmt.upper() if fmt else 'base'} base layer (RPU ignored; no libplacebo)")
    if not hdr and transfer is None and (primaries == "bt2020" or (matrix or "").startswith("bt2020")) \
            and (depth or 8) >= 10:
        transfer, hdr, fmt = "arib-std-b67", True, "hlg"
        notes.append("untagged transfer on 10-bit BT.2020 video: assumed HLG (iPhone default)")
    if hdr:
        primaries = primaries or "bt2020"
        matrix = matrix or "bt2020nc"
        rng_lit = rng_lit or "tv"
    else:
        sd = int(vs.get("height") or 0) <= _SD_MAX_HEIGHT and int(vs.get("width") or 0) <= 720
        assumed = []
        if matrix is None:
            matrix = "smpte170m" if sd else "bt709"
            assumed.append(f"matrix={matrix}")
        if primaries is None:
            primaries = "smpte170m" if sd else "bt709"
            assumed.append(f"primaries={primaries}")
        if transfer is None:
            transfer = "bt709"
            assumed.append("transfer=bt709")
        if rng_lit is None:
            rng_lit = "tv"
            assumed.append("range=tv")
        if assumed:
            notes.append("untagged colour, assumed " + ", ".join(assumed))
        if (matrix or "").startswith("bt2020") and primaries == "bt709":
            notes.append("inconsistent colour tags (BT.2020 matrix with BT.709 primaries/transfer): honoured "
                         "as tagged, treated as SDR")
    return ColorInfo(primaries=primaries, transfer=transfer, matrix=matrix, range=rng_lit, pix_fmt=pix_fmt,
                     bit_depth=depth, hdr=hdr, hdr_format=fmt,  # type: ignore[arg-type]
                     dolby_vision=dolby_vision)


def _audio_info(a_s: Mapping[str, Any], studio: Mapping[str, Any], notes: list[str]) -> AudioInfo:
    first = studio.get("first_audio") or {}
    start = _ts_us(first.get("pts"), first.get("time_base") or a_s.get("time_base"))
    if start is None:
        st = _time_or_none(a_s.get("start_time"))
        start = seconds_to_us(st) if st is not None else 0
    dur = _frac_or_none(a_s.get("duration"))
    priming: int | None = None
    for tr in studio.get("edit_lists") or []:
        if tr.get("handler") == "soun" and tr.get("entries"):
            media = [e["media_time"] for e in tr["entries"] if e["media_time"] >= 0]
            mt = media[0] if media else 0
            if 0 < mt <= _MAX_PRIMING:
                priming = int(mt)
                notes.append(f"audio edit list skips {mt} priming samples (honoured by the demuxer)")
            elif mt > _MAX_PRIMING:
                notes.append(f"audio edit list starts {mt} samples into the stream (trim, honoured by the demuxer)")
            break
    skip = studio.get("audio_skip_samples")
    if priming is None and skip and int(skip) <= _MAX_PRIMING:
        priming = int(skip)
        notes.append(f"AAC priming {skip} samples signalled as skip_samples (honoured by the decoder)")
    return AudioInfo(
        stream_index=int(a_s["index"]), codec=a_s.get("codec_name"), sample_rate=_int_or_none(a_s.get("sample_rate")),
        channels=_int_or_none(a_s.get("channels")), channel_layout=a_s.get("channel_layout"),
        bit_rate=_int_or_none(a_s.get("bit_rate")), duration_us=seconds_to_us(dur) if dur else None,
        start_us=int(start or 0), priming_samples=priming,
    )


def media_info_from_probe(probe_data: dict[str, Any], path: str | os.PathLike[str]) -> MediaInfo:
    """Build :class:`MediaInfo` from ffprobe JSON (pure function; unit-testable without media).

    ``probe_data`` may carry the :func:`measure` results under ``"studio"``; without them the stream-level
    fields (``r_frame_rate``, ``start_time``, ``duration``) are used.
    """
    streams = probe_data.get("streams") or []
    fmt = probe_data.get("format") or {}
    studio = probe_data.get("studio") or {}
    vs = select_video_stream(streams)
    if vs is None:
        raise ProbeError(f"no video stream in {path}")
    notes: list[str] = []

    # --- geometry
    cw, ch = int(vs["width"]), int(vs["height"])
    rotation, mirrored = rotation_from_stream(vs)
    if mirrored:
        notes.append("display matrix is mirrored (front camera): ffmpeg autorotate applies the flip")
    sar = _frac_or_none((vs.get("sample_aspect_ratio") or "").replace(":", "/")) or Fraction(1)
    dw, dh = cw, ch  # unrotated display size in square pixels (never downsampled)
    if sar > 1:
        dw = max(2, round(cw * sar / 2) * 2)
    elif sar < 1:
        dh = max(2, round(ch / sar / 2) * 2)
    if sar != 1:
        notes.append(f"non-square pixels (SAR {sar.numerator}:{sar.denominator}) resampled to square in the "
                     f"mezzanine ({dw}x{dh} before rotation)")
    w, h = (dh, dw) if rotation in (90, 270) else (dw, dh)
    aspect = Fraction(w, h)
    if rotation:
        notes.append(f"rotated {rotation}° clockwise to upright (coded {cw}x{ch} → {w}x{h})")
    if w > h:
        notes.append(f"{FLAG_LANDSCAPE} landscape source kept at native aspect in the mezzanine")
    if abs(aspect - _NINE_SIXTEEN) / _NINE_SIXTEEN > _ASPECT_TOL:
        notes.append(f"{FLAG_NEEDS_REFRAME} display aspect {aspect.numerator}:{aspect.denominator} is not 9:16; "
                     "the A-roll must be reframed for vertical delivery")

    # --- timing
    r_fps = _frac_or_none(vs.get("r_frame_rate"))
    avg_fps = _frac_or_none(vs.get("avg_frame_rate"))
    timing = studio.get("video_timing")
    fps, vfr, reason = decide_fps(timing, r_fps, avg_fps)
    if vfr:
        notes.append(f"{FLAG_VFR} {reason}")
    tb_v = vs.get("time_base")
    src_dur: Fraction | None = None
    if timing and timing.get("n_frames", 0) >= 1 and timing.get("first_pts") is not None:
        tb = to_fraction(timing["time_base"])
        end = timing["last_pts"] + (timing.get("last_duration") or timing.get("median") or 0)
        src_dur = Fraction(end - timing["first_pts"]) * tb
    if not src_dur or src_dur <= 0:
        src_dur = _frac_or_none(vs.get("duration")) or _frac_or_none(fmt.get("duration"))
    if not src_dur or src_dur <= 0:
        nbf = _int_or_none(vs.get("nb_frames"))
        if nbf:
            src_dur = Fraction(nbf) / fps
    if not src_dur or src_dur <= 0:
        raise ProbeError(f"cannot determine the duration of {path}")
    n_frames = max(1, frame_count(src_dur, fps))
    duration_us = seconds_to_us(Fraction(n_frames) / fps)
    if abs(Fraction(n_frames) / fps - src_dur) > Fraction(1, 1000):
        notes.append(f"source video duration {float(src_dur):.6f}s conformed to {n_frames} frames at "
                     f"{fps.numerator}/{fps.denominator} fps")
    first_v = (studio.get("first_video") or {}).get("pts")
    start_us = _ts_us(first_v, (studio.get("first_video") or {}).get("time_base") or tb_v)
    if start_us is None:
        st = _time_or_none(vs.get("start_time"))
        start_us = seconds_to_us(st) if st is not None else 0

    # --- colour
    color = _color_info(vs, studio, notes)

    # --- audio
    if "audio_stream_index" in studio:
        a_idx = studio["audio_stream_index"]
        a_s = next((s for s in streams if s.get("index") == a_idx), None) if a_idx is not None else None
    else:
        a_s = select_audio_stream(streams)
    skipped = [s for s in streams if s.get("codec_type") == "audio" and (s.get("codec_name") or "").lower()
               in _NEVER_AUDIO]
    if skipped:
        notes.append(f"ignored {len(skipped)} APAC (spatial audio) stream(s); dialogue taken from the AAC stream")
    audio = _audio_info(a_s, studio, notes) if a_s is not None else None
    if audio is None:
        notes.append(f"{FLAG_NO_AUDIO} source has no usable audio stream; a silent 48 kHz track is generated")
    elif audio.start_us != start_us:
        off = audio.start_us - start_us
        notes.append(f"audio starts {off / 1000:+.3f} ms relative to the first video frame (aligned in audio.wav)")

    # --- edit lists
    edit_list = False
    for tr in studio.get("edit_lists") or []:
        if not _edit_is_trivial(tr):
            edit_list = True
            if tr.get("handler") == "vide":
                ents = tr.get("entries") or []
                empty = sum(e["segment_duration"] for e in ents if e["media_time"] == -1)
                if empty and tr.get("movie_timescale"):
                    notes.append(f"video edit list delays the picture by {empty / tr['movie_timescale'] * 1000:.1f} ms "
                                 "(empty edit, honoured)")
                elif len(ents) > 1:
                    notes.append(f"video edit list has {len(ents)} entries (honoured by the demuxer)")

    return MediaInfo(
        path=str(Path(path).resolve()) if Path(path).exists() else str(path),
        container=fmt.get("format_name"),
        width=w, height=h, coded_width=cw, coded_height=ch, rotation=rotation, sar=sar,
        display_aspect=aspect, fps=fps, r_fps=r_fps, avg_fps=avg_fps, vfr=vfr,
        duration_us=duration_us, start_us=int(start_us or 0),
        nb_frames=_int_or_none(vs.get("nb_frames")) or (timing or {}).get("n_frames"),
        video_codec=vs.get("codec_name"), video_profile=vs.get("profile"),
        video_bit_rate=_int_or_none(vs.get("bit_rate")),
        color=color, audio=audio, edit_list=edit_list, notes=notes,
    )


def probe(path: str | os.PathLike[str], *, settings: Settings | None = None) -> MediaInfo:
    """``media_info_from_probe(probe_full(path), path)``."""
    return media_info_from_probe(probe_full(path, settings=settings), path)

