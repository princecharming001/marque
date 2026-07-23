"""Ralph campaign grader: audio splice pops/clicks + loudness + bed separation.

Approach (architect-verified): two ffmpeg astats decode passes over the WHOLE
render (not per seam) —
  1. broadband 10ms-frame timeline (RMS/peak/max-sample-difference)
  2. the same chain behind a 4kHz highpass — the CLICK channel. A splice
     discontinuity is broadband (strong HF energy); plosives (p/b/t) are
     LF-dominant, so the HF view suppresses the main false-positive source.
Then a per-seam outlier test with two guards: a word-ONSET within ±30ms of the
seam raises the threshold (legit attack energy), and a fade/dip transition
within ±3f downgrades to P2 (the dip masks the seam by design).

Loudness: reuse app.audio.probe_loudness verbatim; |measured − target| >2 LU
→ P1, >6 LU → P0. Bed separation (only when music is planned): median speech
RMS − median gap RMS < 8 dB → P1 music_over_speech; bed inaudible → P2.

Argv builders and parsers are split (app/audio.py's keyless-testable pattern)
so tests run on canned stderr with no ffmpeg.
"""
from __future__ import annotations

import asyncio
import re
import shutil

from app.audio import probe_loudness
from app.edl import ms_to_frame
from eval.campaign_common import FPS, finding, seam_out_frames, src_to_out

_FRAME_S = 0.010                    # 10ms astats frames
_POP_HF_DB = 12.0                   # HF spike over neighbors
_POP_HF_DB_ONSET = 18.0             # raised threshold at word onsets
_POP_MAXDIFF_MULT = 3.0             # vs 95th percentile of broadband maxdiff
_SEP_MIN_DB = 8.0                   # speech-over-bed floor
_LUFS_P1, _LUFS_P0 = 2.0, 6.0


def astats_argv(path: str, highpass: bool) -> list[str]:
    af = "aresample=48000,asetnsamples=n=480,astats=metadata=1:reset=1," \
         "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level," \
         "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level," \
         "ametadata=mode=print:key=lavfi.astats.Overall.Max_difference"
    if highpass:
        af = "highpass=f=4000," + af
    return ["ffmpeg", "-v", "info", "-i", path, "-vn", "-af", af, "-f", "null", "-"]


_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
_KEY_RE = re.compile(r"lavfi\.astats\.Overall\.(RMS_level|Peak_level|Max_difference)=(-?[\d.]+|[-+]?inf|nan)")


def parse_astats(stderr: str) -> list[dict]:
    """[{t, rms, peak, maxdiff}] per 10ms frame from ametadata stderr output."""
    frames: list[dict] = []
    cur: dict | None = None
    for line in stderr.splitlines():
        m = _PTS_RE.search(line)
        if m:
            if cur is not None and len(cur) > 1:
                frames.append(cur)
            cur = {"t": float(m.group(1))}
            continue
        if cur is None:
            continue
        k = _KEY_RE.search(line)
        if k:
            try:
                val = float(k.group(2))
            except ValueError:
                val = -120.0
            key = {"RMS_level": "rms", "Peak_level": "peak", "Max_difference": "maxdiff"}[k.group(1)]
            cur[key] = val
    if cur is not None and len(cur) > 1:
        frames.append(cur)
    return frames


async def _run_astats(path: str, highpass: bool) -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        *astats_argv(path, highpass),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    return parse_astats(err.decode(errors="replace"))


def _window(frames: list[dict], t0: float, t1: float) -> list[dict]:
    return [f for f in frames if t0 <= f["t"] <= t1]


def _median(vals: list[float]) -> float:
    if not vals:
        return -120.0
    s = sorted(vals)
    return s[len(s) // 2]


def detect_seam_pops(tl_hf: list[dict], tl_bb: list[dict], plan: dict,
                     words: list[dict], *, video: str = "", job_id: str = "") -> list[dict]:
    findings: list[dict] = []
    if not tl_hf or not tl_bb:
        return findings
    # 95th percentile of broadband maxdiff (discontinuity baseline).
    diffs = sorted(f.get("maxdiff", 0.0) for f in tl_bb)
    p95 = diffs[int(len(diffs) * 0.95)] if diffs else 0.0

    # Word onsets in output seconds (for the plosive guard).
    onsets: list[float] = []
    for w in words or []:
        of = src_to_out(plan, ms_to_frame(w.get("start_ms", 0)))
        if of is not None:
            onsets.append(of / FPS)

    # Transitions near seams downgrade severity (the dip masks the seam).
    trans_frames = {int(t.get("at_frame", -99)) for t in (plan.get("transitions") or [])}

    for seam in seam_out_frames(plan):
        t = seam / FPS
        B = _window(tl_hf, t - 0.010, t + 0.010)
        A = _window(tl_hf, t - 0.070, t - 0.020)
        C = _window(tl_hf, t + 0.020, t + 0.070)
        Bbb = _window(tl_bb, t - 0.010, t + 0.010)
        if not B or not (A or C):
            continue
        peak_b = max(f.get("peak", -120.0) for f in B)
        floor = max(_median([f.get("rms", -120.0) for f in A]),
                    _median([f.get("rms", -120.0) for f in C]))
        thresh = _POP_HF_DB_ONSET if any(abs(o - t) <= 0.030 for o in onsets) else _POP_HF_DB
        hf_spike = peak_b > floor + thresh
        discont = Bbb and max(f.get("maxdiff", 0.0) for f in Bbb) > _POP_MAXDIFF_MULT * max(p95, 1e-6)
        if hf_spike and discont:
            masked = any(abs(seam - tf) <= 3 for tf in trans_frames)
            f = finding(video, job_id, "audio_pop", t=t,
                evidence=f"seam at out {seam}: HF peak {peak_b:.1f} dB vs floor {floor:.1f} "
                         f"(Δ{peak_b - floor:.1f} > {thresh}) + maxdiff {max(x.get('maxdiff', 0) for x in Bbb):.3f} "
                         f"> {_POP_MAXDIFF_MULT}×p95 {p95:.3f}",
                source="audio_qc", extra={"seam": seam, "masked_by_transition": masked})
            if masked:
                f["severity"] = "P2"
            findings.append(f)
    return findings


def check_bed_separation(tl_bb: list[dict], plan: dict, *,
                         video: str = "", job_id: str = "") -> list[dict]:
    music = (plan.get("audio") or {}).get("music")
    if not music or not music.get("url"):
        return []
    speech = [f / FPS for f in (plan.get("audio") or {}).get("speech_frames") or []]
    if not speech:
        return []
    speech_rms, gap_rms = [], []
    speech_set = sorted(speech)
    for fr in tl_bb:
        t = fr["t"]
        near = any(abs(t - s) <= 0.150 for s in speech_set if abs(t - s) <= 0.2)
        (speech_rms if near else gap_rms).append(fr.get("rms", -120.0))
    if not speech_rms or not gap_rms:
        return []
    sep = _median(speech_rms) - _median(gap_rms)
    if sep < _SEP_MIN_DB:
        return [finding(video, job_id, "music_over_speech",
            evidence=f"speech-over-bed separation {sep:.1f} dB < {_SEP_MIN_DB} dB "
                     f"(speech {_median(speech_rms):.1f} / gaps {_median(gap_rms):.1f})",
            source="audio_qc")]
    if _median(gap_rms) < -60.0:
        return [finding(video, job_id, "music_missing",
            evidence=f"bed configured but gap RMS {_median(gap_rms):.1f} dBFS — inaudible",
            source="audio_qc")]
    return []


async def grade_audio(render_path: str, plan: dict, words: list[dict], *,
                      video: str = "", job_id: str = "") -> list[dict]:
    if shutil.which("ffmpeg") is None:
        return [finding(video, job_id, "grader_crashed",
                        evidence="ffmpeg not on PATH", source="audio_qc")]
    findings: list[dict] = []
    tl_bb, tl_hf = await asyncio.gather(_run_astats(render_path, False),
                                        _run_astats(render_path, True))
    findings += detect_seam_pops(tl_hf, tl_bb, plan, words, video=video, job_id=job_id)
    findings += check_bed_separation(tl_bb, plan, video=video, job_id=job_id)
    lufs = await probe_loudness(render_path)
    target = float((plan.get("audio") or {}).get("lufs_target") or -14.0)
    if lufs is not None:
        drift = abs(lufs - target)
        if drift > _LUFS_P0:
            findings.append(finding(video, job_id, "lufs_broken",
                evidence=f"integrated {lufs:.1f} LUFS vs target {target:.1f} (drift {drift:.1f})",
                source="audio_qc"))
        elif drift > _LUFS_P1:
            findings.append(finding(video, job_id, "lufs_drift",
                evidence=f"integrated {lufs:.1f} LUFS vs target {target:.1f} (drift {drift:.1f})",
                source="audio_qc"))
    return findings
