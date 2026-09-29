"""Master + deliverables: composite once, encode once per bitrate profile, mux per audio mix.

``master(job, aroll, overlays|None, mix_wav, mix_nomusic_wav, platform(s)) -> MasterResult`` turns the
render stages' outputs into the delivery files in the render dir (the timeline is read from
``timeline.json`` beside the A-roll unless passed):

* **Composite** — the overlay layer (ProRes 4444 with straight alpha) is blended onto the A-roll exactly
  once, in 10-bit 4:4:4 Y'CbCr (``overlay=format=yuv444p10``), frames paired by index (both streams are
  re-timed to ``setpts=N`` on the same 1/fps time base, so they cannot slip).
* **One 8-bit conversion** — 10-bit 4:4:4 → 8-bit 4:2:0 with zscale (spline36 chroma filter, MPEG-2
  "left" chroma siting, error-diffusion dither), which keeps gradients (skies, walls, soft backgrounds)
  from banding after the platform re-encode.
* **Video encode** — x264 High, yuv420p, BT.709 primaries/transfer/matrix, TV range, CRF 15, preset
  ``veryslow`` (previews: ``faster``), closed GOP with ``keyint = min-keyint = fps/2`` and no scene-cut
  keyframes (YouTube's spec: "closed GOP, GOP of half the frame rate, 2 consecutive B-frames"),
  ``bframes=2``, ``aq-mode=3`` (variance AQ biased to dark areas: less banding in shadows at 8-bit), VBV
  capped at 25 Mbps — and for Reels at ``0.9 x 300 MB / duration`` so long takes stay under Instagram's
  300 MB API limit. Platforms whose caps coincide share one encode.
* **Mux without edit lists** — Instagram requires "no edit lists" and YouTube advises against them, so
  files are written with ``-use_editlist 0``. The two things an edit list would normally hide are
  handled explicitly: B-frame reordering uses ISO/IEC 14496-12 version-1 ``ctts`` (negative composition
  offsets, so the first frame is presented at t=0), and the AAC encoder's priming delay (measured once
  per encoder by a click round-trip; 2112 samples for AudioToolbox, 1024 for FFmpeg's native encoder) is
  pre-trimmed from the input so decoded audio is sample-aligned with the picture. Apple's AudioToolbox
  AAC-LC (``aac_at``, constrained VBR, quality 0) is used when available, else FFmpeg's ``aac``; 320 kbps,
  48 kHz, stereo. A mono mix is duplicated to both channels at −3.01 dB so the BS.1770 loudness of the
  stereo file equals the loudness measured on the mono mix. ``+faststart`` puts the moov atom first.
* **Deliverables** — ``final_<platform>.mp4`` for every requested platform, ``final_nomusic.mp4``,
  ``cover.jpg`` (the sharpest eyes-open, facing-camera frame in the hook, never mid-title-animation,
  composited with the hook title/texts/cards but **not** the running captions — a caption fragment on a
  thumbnail reads as a mistake), ``captions.srt`` from the timeline's caption pages, and ``render.json``
  (manifest).

``render_document`` runs the whole chain for one document version into a new ``renders/r{n}/``.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from studio.compile.models import Timeline
from studio.compile.video import FFMPEG, FFPROBE, RenderError, probe_video
from studio.timebase import round_fraction, sample_index

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.audio import AudioRender
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = ["MasterResult", "master", "render_document", "PLATFORM_PROFILES", "aac_encoder", "aac_priming",
           "encode_settings", "choose_cover_time", "write_srt"]

#: Delivery profiles (``skills/editing/platforms.md``): 25 Mbps VBV everywhere; Reels also 0.9 x 300 MB.
PLATFORM_PROFILES: dict[str, dict[str, Any]] = {
    "tiktok": {"maxrate_bps": 25_000_000, "file_cap_bytes": None},
    "reels": {"maxrate_bps": 25_000_000, "file_cap_bytes": 300_000_000},
    "shorts": {"maxrate_bps": 25_000_000, "file_cap_bytes": None},
}
CRF_FINAL = 15
CRF_PREVIEW = 20
AUDIO_BITRATE = "320k"
_KNOWN_PRIMING = {"aac": 1024, "aac_at": 2112}


@dataclass
class MasterResult:
    render_dir: Path
    finals: dict[str, Path] = field(default_factory=dict)  # platform -> final_<platform>.mp4
    nomusic: Path | None = None
    cover: Path | None = None
    srt: Path | None = None
    extras: dict[str, Path] = field(default_factory=dict)  # aroll, overlays, mix, timeline, manifest …


# ============================================================================================ helpers
def _run(cmd: list[str], log: Path | None, what: str, cwd: Path | None = None) -> None:
    if log is not None:
        with open(log, "ab") as fh:
            fh.write(("\n$ " + " ".join(cmd) + "\n").encode())
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=fh, cwd=cwd)
    else:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=cwd)
    if r.returncode != 0:
        tail = log.read_text(errors="replace")[-2500:] if log is not None and log.exists() else (
            (r.stderr or b"").decode(errors="replace")[-2500:])
        raise RenderError(f"{what} failed (rc={r.returncode}):\n{tail}")


def _probe_streams(path: Path) -> list[dict[str, Any]]:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True,
                       text=True)
    if r.returncode != 0:
        raise RenderError(f"ffprobe failed on {path}: {r.stderr[-400:]}")
    return json.loads(r.stdout).get("streams", [])


@functools.lru_cache(maxsize=1)
def _encoders() -> str:
    r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return r.stdout


def aac_encoder() -> str:
    """AudioToolbox AAC when this FFmpeg has it (higher quality than the native encoder), else ``aac``."""
    return "aac_at" if " aac_at " in _encoders() else "aac"


def _aac_args(encoder: str) -> list[str]:
    if encoder == "aac_at":
        return ["-c:a", "aac_at", "-aac_at_mode", "cvbr", "-aac_at_quality", "0", "-b:a", AUDIO_BITRATE]
    return ["-c:a", "aac", "-b:a", AUDIO_BITRATE]


@functools.lru_cache(maxsize=4)
def aac_priming(encoder: str) -> int:
    """Encoder delay in samples, measured by encoding a click and decoding the edit-list-free MP4."""
    import soundfile as sf

    sr = 48_000
    with tempfile.TemporaryDirectory(prefix="aacprime") as td:
        d = Path(td)
        x = np.zeros((sr * 2, 2), np.float32)
        x[sr:sr + 24, :] = 0.8
        sf.write(d / "c.wav", x, sr, subtype="FLOAT")
        try:
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(d / "c.wav"), *_aac_args(encoder),
                            "-use_editlist", "0", str(d / "c.mp4")], check=True, capture_output=True)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(d / "c.mp4"), "-c:a", "pcm_f32le",
                            str(d / "d.wav")], check=True, capture_output=True)
            y, _ = sf.read(d / "d.wav")
            idx = int(np.argmax(np.abs(y[:, 0]) > 0.3))
            delay = idx - sr
            if 0 <= delay <= 8192:
                return delay
        except (subprocess.CalledProcessError, OSError, ValueError):
            pass
    return _KNOWN_PRIMING.get(encoder, 1024)


def _keyint(fps: Fraction) -> int:
    return max(1, round_fraction(fps / 2))


def encode_settings(platform: str, duration_s: float, fps: Fraction, *, preview: bool = False) -> dict[str, Any]:
    """x264 settings for a deliverable (see module docstring)."""
    k = _keyint(fps)
    prof = PLATFORM_PROFILES.get(platform, PLATFORM_PROFILES["tiktok"])
    maxrate = None if preview else int(prof["maxrate_bps"])
    if not preview and prof.get("file_cap_bytes") and duration_s > 0:
        maxrate = min(maxrate or 10**12, int(0.9 * prof["file_cap_bytes"] * 8 / duration_s))
    return {"preset": "faster" if preview else "veryslow", "crf": CRF_PREVIEW if preview else CRF_FINAL,
            "keyint": k, "bframes": 2, "maxrate": maxrate, "bufsize": (2 * maxrate) if maxrate else None}


def _x264_args(st: dict[str, Any]) -> list[str]:
    params = [f"keyint={st['keyint']}", f"min-keyint={st['keyint']}", "scenecut=0", f"bframes={st['bframes']}",
              "open-gop=0", "aq-mode=3", "colorprim=bt709", "transfer=bt709", "colormatrix=bt709",
              "chromaloc=0", "force-cfr=1"]
    args = ["-c:v", "libx264", "-preset", st["preset"], "-crf", str(st["crf"]), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-x264-params", ":".join(params), "-flags", "+cgop"]
    if st.get("maxrate"):
        args += ["-maxrate", str(st["maxrate"]), "-bufsize", str(st["bufsize"])]
    return args


_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
         "-chroma_sample_location", "left"]
_MUXFLAGS = ["-movflags", "+faststart+negative_cts_offsets", "-use_editlist", "0"]
_SETP = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def _composite_chain(timeline: Timeline, aroll: Path, overlays: Path | None, out_size: tuple[int, int]) -> str:
    fps = timeline.fps
    tb = f"{fps.denominator}/{fps.numerator}"
    W, H = out_size
    chains = [f"[0:v]{_SETP},settb={tb},setpts=N,scale={W}:{H}:flags=lanczos+accurate_rnd+full_chroma_int,"
              f"zscale=filter=spline36,format=yuv444p10le[a]"]
    last = "a"
    if overlays is not None:
        chains.append(f"[1:v]{_overlay_color(overlays)},settb={tb},setpts=N,"
                      f"scale={W}:{H}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuva444p10le[o]")
        # frames are paired by index; a layer one frame short repeats its last frame rather than
        # dropping captions on the final frame (larger mismatches are rejected before encoding)
        chains.append("[a][o]overlay=format=yuv444p10:alpha=straight:eof_action=repeat:repeatlast=1[c]")
        last = "c"
    chains.append(f"[{last}]zscale=m=709:t=709:p=709:r=limited:c=left:filter=spline36:d=error_diffusion,"
                  f"format=yuv420p,{_SETP}[v]")
    return ";".join(chains)


def _overlay_color(overlays: Path) -> str:
    """Interpret the overlay layer as BT.709 limited range (the overlay contract); convert only if it is
    explicitly tagged BT.601."""
    try:
        cs = (probe_video(overlays).color_space or "").lower()
    except RenderError:
        cs = ""
    if cs in ("smpte170m", "bt470bg"):
        return ("zscale=min=170m:rin=limited:m=709:r=limited:filter=spline36,format=yuva444p10le,"
                f"{_SETP}")
    return _SETP


def _audio_chain(mix: Path, total_samples: int, priming: int) -> str:
    """Resample to 48 kHz, stereo (mono duplicated at −3.01 dB), pre-trim the encoder priming and pad or
    cut so the decoded stream (priming + content) covers exactly the picture."""
    ch = 2
    try:
        for s in _probe_streams(mix):
            if s.get("codec_type") == "audio":
                ch = int(s.get("channels") or 2)
                break
    except RenderError:
        pass
    parts = ["aresample=48000:resampler=soxr:precision=28"]
    if ch == 1:
        parts.append("pan=stereo|c0=0.70710678*c0|c1=0.70710678*c0")
    elif ch > 2:
        parts.append("pan=stereo|FL<FL+0.707*FC+0.707*BL+0.707*SL|FR<FR+0.707*FC+0.707*BR+0.707*SR")
    content = max(1, total_samples - priming)
    parts += [f"atrim=start_sample={priming}:end_sample={priming + content}", "asetpts=N/SR/TB",
              f"apad=whole_len={content}"]
    return ",".join(parts)


# ============================================================================================ cover + srt
def _fmt_srt_time(t: Fraction) -> str:
    ms = max(0, round_fraction(Fraction(t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(timeline: Timeline, path: str | os.PathLike[str]) -> Path:
    """SRT sidecar from the caption pages (verbatim text or the page's display override)."""
    lines: list[str] = []
    for n, page in enumerate(timeline.captions, start=1):
        text = page.text or " ".join(w.text for w in page.words)
        lines += [str(n), f"{_fmt_srt_time(page.out_start)} --> {_fmt_srt_time(page.out_end)}", text.strip(), ""]
    p = Path(path)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _covered(timeline: Timeline, t: Fraction) -> bool:
    for ins in timeline.inserts:
        if ins.mode in ("full", "card", "split_top", "split_bottom") and ins.out_start <= t < ins.out_end:
            return True
    return False


def _index_score(index: TakeIndex | None, src_us: int) -> float:
    if index is None or not index.visual.samples:
        return 0.5
    for e in index.visual.events:
        if e.kind in ("blink", "look_away", "face_lost") and e.start_us <= src_us <= e.end_us:
            return 0.0
    s = min(index.visual.samples, key=lambda v: abs(v.t_us - src_us))
    if abs(s.t_us - src_us) > 300_000 or s.face_box is None:
        return 0.05
    eyes = 0.6 if s.eyes_open is None else s.eyes_open
    mouth = 0.3 if s.mouth_open is None else s.mouth_open
    gaze = 0.0 if s.gaze_off is None else s.gaze_off
    yaw = 0.0 if s.head_yaw is None else abs(s.head_yaw)
    score = s.face_conf * eyes * (1 - 0.4 * max(0.0, mouth - 0.45)) * (1 - 0.5 * min(1.0, gaze))
    return score * (1 - 0.4 * min(1.0, yaw / 30.0))


def _grab_gray(path: Path, fps: Fraction, frame: int, size: tuple[int, int]) -> np.ndarray | None:
    W, H = size
    cmd = [FFMPEG, "-v", "error", "-nostdin"]
    if frame > 0:
        cmd += ["-ss", f"{float((Fraction(frame) - Fraction(1, 2)) / fps):.9f}"]
    cmd += ["-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-fps_mode", "passthrough", "-vf", f"scale={W}:{H}",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or len(r.stdout) < W * H:
        return None
    return np.frombuffer(r.stdout[:W * H], np.uint8).reshape(H, W).astype(np.float32)


def _sharpness(img: np.ndarray, box: tuple[float, float, float, float] | None) -> float:
    H, W = img.shape
    if box is not None:
        x, y, w, h = box
        x0, y0 = int(max(0, x * W)), int(max(0, y * H))
        x1, y1 = int(min(W, (x + w) * W)), int(min(H, (y + h) * H))
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            img = img[y0:y1, x0:x1]
    lap = (img[1:-1, 2:] + img[1:-1, :-2] + img[2:, 1:-1] + img[:-2, 1:-1] - 4 * img[1:-1, 1:-1])
    return float(lap.var())


def _text_factor(timeline: Timeline, t: Fraction, hook_end: Fraction) -> float:
    """Cover preference from the text layer at ``t``: a title caught mid-entrance or mid-exit (partly
    scaled or faded) makes a broken thumbnail (x0.3); when the hook has a hook title, frames that show it
    are mildly preferred (the thumbnail headline)."""
    factor = 1.0
    title_in_hook = title_on = False
    for x in timeline.texts:
        if x.kind == "hook_title" and x.out_start < hook_end:
            title_in_hook = True
        if not (x.out_start <= t < x.out_end):
            continue
        settle = Fraction(9, 10) if x.animation == "typewriter" else Fraction(1, 2)
        entering = x.out_start > 0 and x.animation != "none" and t < x.out_start + settle
        if entering or t >= x.out_end - Fraction(1, 5):
            factor = min(factor, 0.3)
        title_on = title_on or x.kind == "hook_title"
    if title_in_hook and not title_on:
        factor *= 0.9
    return factor


def choose_cover_time(timeline: Timeline, index: TakeIndex | None, aroll: Path, *, hook_s: float = 3.0,
                      top_k: int = 8) -> Fraction:
    """Best cover frame in the hook: face present, eyes open, facing the lens, mouth not mid-vowel, not
    during a blink/look-away, not under a cutaway and not while a title animates in or out; ties broken by
    measured sharpness of the face."""
    from studio.compile.timeline import map_source_point

    fps = timeline.fps
    n_max = max(1, min(timeline.frame_count, round_fraction(Fraction(repr(hook_s)) * fps)))
    hook_end = Fraction(n_max) / fps
    cands: list[tuple[float, int, Any]] = []
    for f in range(0, n_max, 2):
        t = Fraction(f) / fps
        if _covered(timeline, t):
            continue
        seg = timeline.segment_at(t)
        if seg is None:
            continue
        su = seg.out_to_src_us(t)
        cands.append((_index_score(index, su) * _text_factor(timeline, t, hook_end), f, su))
    if not cands:
        return Fraction(0)
    cands.sort(key=lambda c: (-c[0], c[1]))
    best = cands[:top_k]
    try:
        p = probe_video(aroll)
        gw = max(64, p.width // 4 // 2 * 2)
        gh = max(64, p.height // 4 // 2 * 2)
    except RenderError:
        return Fraction(best[0][1]) / fps
    scored: list[tuple[float, int]] = []
    sharp_vals = []
    for s, f, su in best:
        img = _grab_gray(aroll, fps, f, (gw, gh))
        box = None
        if index is not None:
            fb = index.face_at(su)
            if fb is not None:
                t = Fraction(f) / fps
                a = map_source_point(timeline, t, fb.x, fb.y, index.media.width, index.media.height)
                b = map_source_point(timeline, t, fb.x + fb.w, fb.y + fb.h, index.media.width, index.media.height)
                if a is not None and b is not None:
                    box = (a[0], a[1], b[0] - a[0], b[1] - a[1])
        sh = _sharpness(img, box) if img is not None else 0.0
        sharp_vals.append(sh)
        scored.append((s, f))
    mx = max(sharp_vals) or 1.0
    final = [(s * (0.5 + 0.5 * sh / mx), f) for (s, f), sh in zip(scored, sharp_vals, strict=True)]
    final.sort(key=lambda x: (-x[0], x[1]))
    return Fraction(final[0][1]) / fps


def _cover_overlay(job: Job, timeline: Timeline, t: Fraction, rd: Path, index: TakeIndex | None,
                   platforms: Sequence[str], log: Path | None) -> Path | None:
    """The overlay layer for the cover frame **without the running captions**: a caption fragment
    ("brand story.") reads as a stray mistake on a thumbnail, while the hook title, callouts and cards are
    exactly what a cover should carry. Renders frames 0..f only (components animate from their own
    sequence start, so shortening the composition does not change their state at frame f). Returns None
    when nothing else is on screen at ``t``."""
    from studio.compile import overlays as overlays_mod

    f = round_fraction(t * timeline.fps)
    props = overlays_mod.build_overlay_props(timeline.model_copy(update={"captions": []}), index=index,
                                             platforms=list(platforms))
    items = [*props.texts, *props.cards, *props.graphics]
    if not any(x.start <= f < x.end for x in items):
        return None
    props = props.model_copy(update={"duration_in_frames": f + 1})
    return overlays_mod.render_overlay_props(props, rd / "_cover_overlay.mov", log=log, verify=False)


def _render_cover(aroll: Path, overlays: Path | None, timeline: Timeline, t: Fraction, out: Path,
                  size: tuple[int, int], log: Path | None) -> Path:
    from PIL import Image

    fps = timeline.fps
    f = round_fraction(t * fps)
    W, H = size
    ss = ["-ss", f"{float((Fraction(f) - Fraction(1, 2)) / fps):.9f}"] if f > 0 else []
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *ss, "-i", str(aroll)]
    graph = (f"[0:v]{_SETP},scale={W}:{H}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv444p10le[a]")
    if overlays is not None:
        cmd += [*ss, "-i", str(overlays)]
        graph += (f";[1:v]{_SETP},scale={W}:{H}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuva444p10le[o];"
                  "[a][o]overlay=format=yuv444p10:alpha=straight[c]")
        last = "c"
    else:
        last = "a"
    graph += f";[{last}]zscale=rin=limited:min=709:r=full:filter=spline36,format=gbrp[v]"
    png = out.with_suffix(".png")
    cmd += ["-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-fps_mode", "passthrough", str(png)]
    _run(cmd, log, "cover extraction")
    Image.open(png).convert("RGB").save(out, "JPEG", quality=95, subsampling=0, optimize=True)
    png.unlink(missing_ok=True)
    return out


# ============================================================================================ master
PathLike = str | os.PathLike[str]


def master(job: Job | None, aroll: PathLike, overlays: PathLike | None, mix: PathLike | AudioRender,
           mix_nomusic: PathLike | None = None, platform: str | Sequence[str] = "tiktok", *,
           timeline: Timeline | None = None, render_dir: PathLike | None = None, preview: bool = False,
           index: TakeIndex | None = None) -> MasterResult:
    """Composite ``overlays`` (ProRes 4444 + alpha, or None) onto ``aroll`` once and encode
    ``final_<platform>.mp4`` for every platform in ``platform`` (a name or a list), plus
    ``final_nomusic.mp4`` (from ``mix_nomusic``), ``cover.jpg`` and ``captions.srt``.

    ``mix`` is ``mix.wav`` (or the :class:`~studio.compile.audio.AudioRender`, which also supplies
    ``mix_nomusic``). ``timeline`` defaults to ``timeline.json`` next to the A-roll and ``render_dir`` to
    the A-roll's directory. ``preview`` keeps the A-roll's (preview) size and uses the fast preset.
    Returns the paths as a :class:`MasterResult`."""
    t0 = time.monotonic()
    aroll = Path(aroll)
    overlays = Path(overlays) if overlays is not None else None
    if not isinstance(mix, (str, os.PathLike)):  # an AudioRender
        if mix_nomusic is None:
            mix_nomusic = getattr(mix, "mix_nomusic", None)
        mix = mix.mix
    mix = Path(mix)
    nomusic_path = Path(mix_nomusic) if mix_nomusic is not None else None
    rd = Path(render_dir) if render_dir is not None else aroll.parent
    rd.mkdir(parents=True, exist_ok=True)
    if timeline is None:
        tl_path = aroll.parent / "timeline.json"
        if not tl_path.exists():
            raise RenderError(f"no timeline given and {tl_path} does not exist")
        timeline = Timeline.load(tl_path)
    for what, path in (("A-roll", aroll), ("mix", mix)):
        if not path.exists():
            raise RenderError(f"{what} not found: {path}")
    platforms = [platform] if isinstance(platform, str) else list(platform)
    platforms = list(dict.fromkeys(platforms)) or ["tiktok"]
    log = job.log_path("master") if job is not None else rd / "master.log"
    fps = timeline.fps
    N = timeline.frame_count
    if N <= 0:
        raise RenderError("empty timeline")
    ap = probe_video(aroll)
    if ap.frame_count and ap.frame_count != N:
        raise RenderError(f"A-roll has {ap.frame_count} frames, timeline expects {N}")
    if overlays is not None:
        op = probe_video(overlays)
        if op.frame_count and abs(op.frame_count - N) > 1:
            raise RenderError(f"overlay layer has {op.frame_count} frames, timeline expects {N}")
    size = (ap.width, ap.height) if preview else (timeline.width, timeline.height)
    dur_s = float(timeline.duration)
    total_samples = sample_index(timeline.duration, 48_000)

    # ---- video: composite + one 8-bit conversion + one encode per distinct profile
    graph = _composite_chain(timeline, aroll, overlays, size)
    inputs = ["-i", str(aroll)] + (["-i", str(overlays)] if overlays is not None else [])
    encodes: dict[tuple[Any, ...], Path] = {}
    per_platform: dict[str, Path] = {}
    for plat in platforms:
        st = encode_settings(plat, dur_s, fps, preview=preview)
        key = (st["preset"], st["crf"], st["keyint"], st["maxrate"])
        if key not in encodes:
            vid = rd / f"_video_{len(encodes)}.mp4"
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-y", *inputs, "-filter_complex", graph,
                   "-map", "[v]", "-frames:v", str(N), "-fps_mode", "passthrough", *_x264_args(st), *_TAGS,
                   "-an", *_MUXFLAGS, str(vid)]
            _run(cmd, log, f"video encode ({plat})")
            encodes[key] = vid
        per_platform[plat] = encodes[key]

    # ---- mux per platform (+ no-music)
    enc = aac_encoder()
    priming = aac_priming(enc)
    result = MasterResult(render_dir=rd)

    def mux(video: Path, wav: Path, out: Path) -> Path:
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video), "-i", str(wav),
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", _audio_chain(wav, total_samples, priming),
               *_aac_args(enc), "-ar", "48000", "-ac", "2", *_MUXFLAGS, "-metadata:s:a:0", "language=und",
               str(out)]
        _run(cmd, log, f"mux {out.name}")
        return out

    try:
        for plat in platforms:
            result.finals[plat] = mux(per_platform[plat], mix, rd / f"final_{plat}.mp4")
        if nomusic_path is not None and nomusic_path.exists():
            result.nomusic = mux(per_platform[platforms[0]], nomusic_path, rd / "final_nomusic.mp4")
    finally:
        for v in encodes.values():
            v.unlink(missing_ok=True)

    # ---- verify the delivery files
    for path in [*result.finals.values(), *([result.nomusic] if result.nomusic else [])]:
        _verify_delivery(path, timeline, size)

    # ---- cover + srt
    if index is None and job is not None:
        try:
            index = job.load_index()
        except Exception:
            index = None
    cover_ovl: Path | None = None
    try:
        tc = choose_cover_time(timeline, index, aroll)
        layer = overlays
        # finals: a caption-free layer for the cover (one more Remotion render). A preview's cover is not a
        # deliverable, so previews reuse the full layer and skip that render.
        if overlays is not None and timeline.captions and job is not None and not preview:
            try:
                layer = cover_ovl = _cover_overlay(job, timeline, tc, rd, index, platforms, log)
            except Exception as e:  # the full layer (with captions) is still a valid cover
                job.trace("render_note", stage="master", note=f"caption-free cover layer failed ({e}); "
                                                                "using the full overlay layer")
                layer = overlays
        result.cover = _render_cover(aroll, layer, timeline, tc, rd / "cover.jpg", size, log)
    except RenderError as e:
        if job is not None:
            job.trace("render_note", stage="master", note=f"cover failed: {e}")
    finally:
        if cover_ovl is not None:
            cover_ovl.unlink(missing_ok=True)
    srt = rd / "captions.srt"
    try:
        from studio.compile import captions as _captions

        result.srt = Path(_captions.write_srt(timeline, srt))
    except NotImplementedError:
        result.srt = write_srt(timeline, srt)
    result.extras.update({"aroll": aroll, "mix": mix})
    if nomusic_path is not None:
        result.extras["mix_nomusic"] = nomusic_path
    if overlays is not None:
        result.extras["overlays"] = overlays
    if job is not None:
        job.trace("render", stage="master", preview=preview, platforms=platforms,
                  seconds=round(time.monotonic() - t0, 2), aac=enc, priming=priming,
                  finals={k: str(v) for k, v in result.finals.items()})
    return result


def _verify_delivery(path: Path, timeline: Timeline, size: tuple[int, int]) -> None:
    streams = _probe_streams(path)
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    problems = []
    if v is None:
        problems.append("no video stream")
    else:
        exp = {"codec_name": "h264", "pix_fmt": "yuv420p", "color_space": "bt709", "color_transfer": "bt709",
               "color_primaries": "bt709", "color_range": "tv"}
        for k, want in exp.items():
            if v.get(k) != want:
                problems.append(f"{k}={v.get(k)} (want {want})")
        if (int(v.get("width", 0)), int(v.get("height", 0))) != size:
            problems.append(f"size {v.get('width')}x{v.get('height')} (want {size[0]}x{size[1]})")
        nb = v.get("nb_frames")
        if nb not in (None, "N/A") and int(nb) != timeline.frame_count:
            problems.append(f"{nb} frames (want {timeline.frame_count})")
    if a is None:
        problems.append("no audio stream")
    elif a.get("codec_name") != "aac" or int(a.get("sample_rate", 0)) != 48_000:
        problems.append(f"audio {a.get('codec_name')} {a.get('sample_rate')} Hz")
    with open(path, "rb") as fh:
        head = fh.read(1 << 16)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    if moov < 0 or (mdat >= 0 and mdat < moov):
        problems.append("moov atom not before mdat (+faststart)")
    if problems:
        raise RenderError(f"{path.name} failed delivery checks: " + "; ".join(problems))


# ============================================================================================ render_document
def render_document(job: Job, doc: CutDocument, index: TakeIndex, *, preview: bool = False,
                    aroll_codec: str | None = None) -> MasterResult:
    """Full render of one document version into a new ``renders/r{n}/`` (compile → A-roll → overlays →
    audio → master). ``aroll_codec``: ``prores_hq`` (default) or ``lean`` (the disk-budget intermediate)."""
    from studio.compile import audio as audio_mod
    from studio.compile import overlays as overlays_mod
    from studio.compile import timeline as timeline_mod
    from studio.compile import video as video_mod

    t0 = time.monotonic()
    rd = job.new_render_dir()
    # The compiler places captions once (captions.build_caption_pages: the strictest safe zone over all the
    # document's deliverables, honouring the Director's per-page positions). A second placement pass here
    # would drop those positions and re-shrink an already fitted style.
    timeline = timeline_mod.compile(doc, index, job=job)
    platforms = [d.platform for d in doc.deliverables] or ["tiktok"]
    tl_path = timeline.save(rd / "timeline.json")
    aroll = video_mod.render_aroll(job, timeline, rd, preview=preview, color=doc.color,
                                   codec=aroll_codec or "prores_hq")
    ovl = overlays_mod.render_overlays(job, timeline, rd, preview=preview, index=index, platforms=platforms)
    aud = audio_mod.render_audio(job, doc, timeline, rd, preview=preview, index=index)
    result = master(job, aroll, ovl, aud.mix, aud.mix_nomusic, platforms, timeline=timeline, render_dir=rd,
                    preview=preview, index=index)
    result.extras["timeline"] = tl_path
    manifest = {
        "doc_version": doc.version, "job_id": job.id, "preview": preview, "frames": timeline.frame_count,
        "fps": f"{timeline.fps.numerator}/{timeline.fps.denominator}", "duration": float(timeline.duration),
        "finals": {k: v.name for k, v in result.finals.items()},
        "nomusic": result.nomusic.name if result.nomusic else None,
        "cover": result.cover.name if result.cover else None, "srt": result.srt.name if result.srt else None,
        "aroll": Path(aroll).name, "aroll_codec": None if preview else (aroll_codec or "prores_hq"),
        "overlays": Path(ovl).name if ovl else None,
        "mix": Path(aud.mix).name, "seconds": round(time.monotonic() - t0, 2),
    }
    mp = rd / "render.json"
    mp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result.extras["manifest"] = mp
    return result

