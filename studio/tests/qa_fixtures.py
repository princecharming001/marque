"""Synthetic renders for the QA tests (keyless, offline).

* :func:`synth_source_audio` — a dialogue track for a Take Index: pink room tone at about −62 dBFS
  plus a voiced burst on every word span (harmonic series with a moving formant, a noisy onset for
  consonants, 8 ms ramps), so seams in pauses sit in room tone and words are clearly speech-like.
* :func:`assemble_from_timeline` — cuts that source exactly as the compiled timeline says (sample-exact
  audio windows, J/L leads, 10 ms equal-power crossfades at true seams, room tone where nothing plays).
* :func:`encode_final` — an MP4 laid out like :mod:`studio.compile.master` writes it: x264 High
  yuv420p BT.709-tagged at the timeline's exact frame count, AAC 48 kHz stereo with the encoder priming
  pre-trimmed (decoded sample k = output time k/sr), ``+faststart``, no edit list. Faults can be
  injected (black stretch, frozen stretch).
* :func:`make_render` — a render directory (``timeline.json``, ``mix.wav``, ``stems/dialogue.wav``,
  ``final_<platform>.mp4``, ``render.json``) with optional audio faults (a click at a seam, digital
  silence under a word, a gain error, an audio offset).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from studio.compile.models import Timeline
from studio.perception.index import TakeIndex
from studio.timebase import sample_index

SR = 48_000
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n)
    f = np.fft.rfft(w)
    k = np.arange(f.size)
    k[0] = 1
    f /= np.sqrt(k)
    x = np.fft.irfft(f, n)
    return x / (np.sqrt(np.mean(x * x)) + 1e-12)


def synth_source_audio(index: TakeIndex, *, sr: int = SR, seed: int = 0, tone_dbfs: float = -62.0,
                       speech_dbfs: float = -20.0) -> np.ndarray:
    """Mono float64 dialogue track as long as the index media."""
    rng = np.random.default_rng(seed)
    n = round(index.media.duration_us * sr / 1_000_000)
    x = _pink(n, rng) * 10 ** (tone_dbfs / 20)
    for i, w in enumerate(index.words):
        a, b = round(w.start_us * sr / 1e6), round(w.end_us * sr / 1e6)
        if b - a < 64:
            continue
        t = np.arange(b - a) / sr
        f0 = 120.0 + 25.0 * math.sin(i * 1.7) + 15.0 * t / max(t[-1], 1e-3)
        ph = 2 * np.pi * np.cumsum(np.full(t.size, 1.0) * f0) / sr
        v = np.zeros(t.size)
        fmt = 700.0 + 500.0 * math.sin(i * 0.9)
        for h in range(1, 30):
            fh = f0 * h
            amp = 1.0 / h * (1.0 + 3.0 * np.exp(-((fh - fmt) / 250.0) ** 2))
            v += amp * np.sin(h * ph)
        cons = rng.standard_normal(t.size) * np.exp(-t / 0.03) * 0.8  # consonant-ish onset
        y = v / (np.sqrt(np.mean(v * v)) + 1e-12) + cons
        env = np.ones(t.size)
        r = min(round(0.008 * sr), t.size // 2)
        env[:r] = np.sin(np.linspace(0, np.pi / 2, r)) ** 2
        env[-r:] = np.cos(np.linspace(0, np.pi / 2, r)) ** 2
        env *= 0.7 + 0.3 * np.sin(2 * np.pi * 4.0 * t) ** 2  # syllabic modulation
        x[a:b] += y * env * 10 ** (speech_dbfs / 20)
    return x


def assemble_from_timeline(timeline: Timeline, source: np.ndarray, *, sr: int = SR, xfade_ms: float = 10.0,
                           tone: np.ndarray | None = None, hard_seams: set[int] | None = None) -> np.ndarray:
    """Mono dialogue of exactly ``timeline.sample_count`` samples (speed 1.0 pieces only): each piece's
    audio window at its (J/L-shifted) output position, equal-power crossfades centred on true seams
    (``hard_seams``: seam numbers joined without a fade), room tone where nothing plays."""
    N = timeline.sample_count
    out = np.zeros(N)
    X = max(2, round(xfade_ms * sr / 1000))
    covered = np.zeros(N, bool)
    segs = timeline.segments
    hard = hard_seams or set()

    def cont(a: Any, b: Any) -> bool:
        return a.audio_src_out_us == b.audio_src_in_us

    seam_no = -1
    for i, s in enumerate(segs):
        a0 = sample_index(Fraction(s.out_start) - Fraction(s.audio_lead_us, 1_000_000), sr)
        n = round((s.audio_src_out_us - s.audio_src_in_us) * sr / 1e6)
        src0 = round(s.audio_src_in_us * sr / 1e6)
        seam_in = i > 0 and not cont(segs[i - 1], s)
        if seam_in:
            seam_no += 1
        seam_out = i + 1 < len(segs) and not cont(s, segs[i + 1])
        hin = X // 2 if seam_in and seam_no not in hard else 0
        hout = X // 2 if seam_out and (seam_no + 1) not in hard else 0
        piece = source[max(0, src0 - hin): src0 + n + hout]
        piece = np.pad(piece, (0, (n + hin + hout) - piece.size))
        g = np.ones(piece.size)
        if hin:
            g[: 2 * hin] = np.sin((np.arange(2 * hin) + 0.5) / (2 * hin) * np.pi / 2)
        if hout:
            g[-2 * hout:] *= np.cos((np.arange(2 * hout) + 0.5) / (2 * hout) * np.pi / 2)
        lo = a0 - hin
        i0, i1 = max(0, lo), min(N, lo + piece.size)
        out[i0:i1] += (piece * g)[i0 - lo: i1 - lo]
        covered[max(0, a0): min(N, a0 + n)] = True
    if tone is not None:
        t = np.resize(tone, N)
        out[~covered] += t[~covered]
    return out


def to_stereo(x: np.ndarray) -> np.ndarray:
    return np.vstack([x, x]) * (1 / math.sqrt(2))


def master(x2: np.ndarray, *, target_lufs: float = -14.0) -> np.ndarray:
    from studio.compile.audio import master_loudness

    y, _ = master_loudness(x2, SR, target_lufs=target_lufs, ceiling_dbtp=-1.5)
    return y


def encode_final(path: Path, audio2: np.ndarray, timeline: Timeline, *, size: tuple[int, int] | None = None,
                 black: tuple[float, float] | None = None, freeze: tuple[float, float] | None = None,
                 priming: bool = True, faststart: bool = True, editlist: bool = False, tag: bool = True,
                 profile_high: bool = True) -> Path:
    """Encode an MP4 deliverable like the master stage (see module docstring)."""
    from studio.compile.master import aac_priming

    W, H = size or (timeline.width, timeline.height)
    fps = Fraction(timeline.fps)
    N = timeline.frame_count
    wav = path.with_suffix(".wav")
    sf.write(str(wav), np.asarray(audio2, dtype=np.float64).T.astype(np.float32), SR, subtype="FLOAT")
    total = timeline.sample_count
    P = aac_priming("aac") if priming else 0
    # a moving test pattern drawn at quarter size and upscaled with nearest-neighbour: full delivery size,
    # always in motion (no false freezes), but it compresses ~10x smaller than full-size testsrc2
    chain = [f"testsrc2=size={max(2, W // 8 * 2)}x{max(2, H // 8 * 2)}:rate={fps.numerator}/{fps.denominator}"]
    vf = [f"scale={W}:{H}:flags=neighbor"]
    if freeze is not None:
        f1, f2 = round(freeze[0] * fps), round(freeze[1] * fps)
        vf.append(f"split[fa][fb];[fa][fb]freezeframes=first={f1}:last={f2}:replace={f1}")
    if black is not None:
        vf.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{black[0]},{black[1]})'")
    if tag:
        vf.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    graph = "[0:v]" + ",".join(["null", *vf, "format=yuv420p"]) + "[v];"
    graph += f"[1:a]atrim=start_sample={P},asetpts=N/SR/TB,apad=whole_len={max(1, total - P)}[a]"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", chain[0], "-i", str(wav),
           "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-frames:v", str(N),
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"]
    if profile_high:
        cmd += ["-x264-params", "8x8dct=1:cabac=1:bframes=2", "-profile:v", "high"]
    cmd += ["-pix_fmt", "yuv420p"]
    if tag:
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    cmd += ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"]
    if faststart:
        cmd += ["-movflags", "+faststart+negative_cts_offsets"]
    else:
        cmd += ["-movflags", "+negative_cts_offsets"]
    if not editlist:
        cmd += ["-use_editlist", "0"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    wav.unlink(missing_ok=True)
    return path


def make_render(render_dir: Path, timeline: Timeline, index: TakeIndex, *, platform: str = "tiktok",
                source: np.ndarray | None = None, click_at_seam: int | None = None, click_kind: str = "spike",
                silence_word: str | None = None, silence_ms: float = 80.0, gain_db: float = 0.0,
                offset_ms: float = 0.0, target_lufs: float = -14.0, size: tuple[int, int] | None = None,
                black: tuple[float, float] | None = None, freeze: tuple[float, float] | None = None,
                encode_kw: dict[str, Any] | None = None) -> dict[str, Path]:
    """A render dir with injected faults; returns paths."""
    from studio.compile.timeline import audio_seams

    render_dir.mkdir(parents=True, exist_ok=True)
    src = synth_source_audio(index) if source is None else source
    dia = assemble_from_timeline(timeline, src, tone=_pink(SR * 2, np.random.default_rng(3)) * 10 ** (-62 / 20))
    mixed = master(to_stereo(dia), target_lufs=target_lufs)
    g = 10 ** (gain_db / 20)
    mixed = mixed * g
    if offset_ms:
        k = round(offset_ms * SR / 1000)
        mixed = np.roll(mixed, k, axis=1)
    if click_at_seam is not None:
        t = audio_seams(timeline)[click_at_seam]
        n = sample_index(t, SR)
        if click_kind == "spike":
            mixed[:, n] += 0.3
        else:  # a DC step (discontinuity) that decays over 30 ms
            m = min(mixed.shape[1] - n, round(0.03 * SR))
            mixed[:, n:n + m] += 0.12 * np.exp(-np.arange(m) / (0.004 * SR))
    if silence_word is not None:
        span = timeline.word_map[silence_word]
        mid = (Fraction(span.out_start) + Fraction(span.out_end)) / 2
        a = sample_index(mid, SR) - round(silence_ms * SR / 2000)
        mixed[:, a: a + round(silence_ms * SR / 1000)] = 0.0
    sf.write(str(render_dir / "mix.wav"), mixed.T.astype(np.float32), SR, subtype="FLOAT")
    (render_dir / "stems").mkdir(exist_ok=True)
    sf.write(str(render_dir / "stems" / "dialogue.wav"), (dia * 10 ** ((target_lufs + 14) / 20)).astype(np.float32),
             SR, subtype="FLOAT")
    timeline.save(render_dir / "timeline.json")
    final = encode_final(render_dir / f"final_{platform}.mp4", mixed, timeline, size=size, black=black, freeze=freeze,
                         **(encode_kw or {}))
    (render_dir / "render.json").write_text(json.dumps({
        "doc_version": timeline.doc_version, "job_id": timeline.job_id, "preview": False,
        "frames": timeline.frame_count, "finals": {platform: final.name}, "overlays": None, "mix": "mix.wav",
    }, indent=2))
    return {"final": final, "mix": render_dir / "mix.wav", "dialogue": render_dir / "stems" / "dialogue.wav",
            "timeline": render_dir / "timeline.json", "source": src}  # type: ignore[dict-item]
