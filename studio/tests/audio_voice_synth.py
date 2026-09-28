"""Synthetic speech-like audio + matching Take Index / Timeline builders for the audio_voice tests.

Keyless and offline: everything is generated with numpy/scipy. "Words" are harmonic stacks with a
formant-ish tilt, a soft attack/decay and optional sibilant bursts; the room is pink noise at a chosen
level. The index built for a signal has the words' exact times, inter-word gaps and room-tone ranges.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from fractions import Fraction

import numpy as np
from scipy import signal as sps

from studio.compile.models import Timeline, TimelineSegment, WordSpan
from studio.doc.model import SeamTreatment
from studio.media.models import AudioInfo, ColorInfo, MediaInfo
from studio.perception.index import AudioMetrics, Gap, TakeIndex, Word, gap_id, word_id
from studio.timebase import frame_time, snap_to_frame

SR = 48_000


def db(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    return float(10 * np.log10(max(float(np.mean(x * x)), 1e-20)))


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n + 4096)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    y = sps.lfilter(b, a, w)[4096:]
    return y / np.sqrt(np.mean(y**2))


def word_signal(n: int, sr: int, f0: float, rng: np.random.Generator, *, sibilant: bool = False,
                sib_rel_db: float = -6.0, n_harm: int = 40) -> np.ndarray:
    t = np.arange(n) / sr
    # intonation contour (±10 %) + vibrato: real speech smears harmonics across the spectrum
    vib = (1 + 0.10 * np.sin(2 * np.pi * 1.7 * t + rng.uniform(0, 6))) * (1 + 0.01 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(f0 * vib) / sr
    y = np.zeros(n)
    for k in range(1, n_harm + 1):
        if k * f0 > 7000:
            break
        amp = 1.0 / k
        amp *= 1.0 + 1.5 * np.exp(-((k * f0 - 600) / 300) ** 2) + 0.8 * np.exp(-((k * f0 - 1800) / 500) ** 2)
        y += amp * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    y /= np.sqrt(np.mean(y**2)) + 1e-12
    if sibilant:
        half = n // 2
        hf = sps.sosfilt(sps.butter(4, [5000, 9000], "bandpass", fs=sr, output="sos"), rng.standard_normal(n - half))
        hf /= np.sqrt(np.mean(hf**2)) + 1e-12
        y[half:] *= 0.15
        y[half:] += 10 ** (sib_rel_db / 20) * hf
    env = np.ones(n)
    a, r = min(n // 3, round(0.02 * sr)), min(n // 3, round(0.04 * sr))
    env[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    env[n - r:] = np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return y * env


def synth_voice(dur_s: float, words: Sequence[tuple[float, float]], *, sr: int = SR, level_db: float = -20.0,
                noise_db: float = -60.0, f0: float = 140.0, seed: int = 0, sibilant: Sequence[int] = (),
                sib_rel_db: float = -6.0, word_levels_db: Sequence[float] | None = None,
                breaths: Sequence[tuple[float, float, float]] = ()) -> np.ndarray:
    """Room noise at ``noise_db`` (RMS dBFS) plus words at ``level_db`` (per-word override
    ``word_levels_db``). ``breaths``: ``(start_s, end_s, level_db)`` broadband breath noise."""
    rng = np.random.default_rng(seed)
    n = round(dur_s * sr)
    x = pink(n, rng) * 10 ** (noise_db / 20)
    for i, (a, b) in enumerate(words):
        s, e = round(a * sr), round(b * sr)
        w = word_signal(e - s, sr, f0 * (1 + 0.05 * ((i % 3) - 1)), rng, sibilant=i in sibilant, sib_rel_db=sib_rel_db)
        lv = word_levels_db[i] if word_levels_db is not None else level_db
        x[s:e] += w * 10 ** (lv / 20)
    for a, b, lv in breaths:
        s, e = round(a * sr), round(b * sr)
        br = sps.sosfilt(sps.butter(2, [300, 6000], "bandpass", fs=sr, output="sos"), rng.standard_normal(e - s))
        br /= np.sqrt(np.mean(br**2))
        env = np.hanning(e - s)
        x[s:e] += br * env / np.sqrt(np.mean(env**2)) * 10 ** (lv / 20)
    return x


def evenly_spaced_words(dur_s: float, *, word_s: float = 0.35, gap_s: float = 0.25, lead_s: float = 0.4,
                        tail_s: float = 0.4) -> list[tuple[float, float]]:
    out, t = [], lead_s
    while t + word_s <= dur_s - tail_s:
        out.append((round(t, 4), round(t + word_s, 4)))
        t += word_s + gap_s
    return out


def media_info(duration_s: float, fps: Fraction = Fraction(30)) -> MediaInfo:
    us = round(duration_s * 1e6)
    return MediaInfo(
        path="/synthetic/take.mov", container="mov,mp4,m4a,3gp,3g2,mj2", width=1080, height=1920, coded_width=1080,
        coded_height=1920, rotation=0, display_aspect=Fraction(9, 16), fps=fps, r_fps=fps, avg_fps=fps, vfr=False,
        duration_us=us, nb_frames=None, video_codec="h264",
        color=ColorInfo(primaries="bt709", transfer="bt709", matrix="bt709", range="tv", pix_fmt="yuv420p",
                        bit_depth=8),
        audio=AudioInfo(stream_index=1, codec="pcm_f32le", sample_rate=SR, channels=1, channel_layout="mono",
                        duration_us=us),
    )


def make_index(words: Sequence[tuple[float, float]], duration_s: float, *, breaths: Sequence[tuple[float, float]] = (),
               snr_db: float | None = None, music_in_room: bool = False,
               kinds: Sequence[str] | None = None) -> TakeIndex:
    ws = [Word(id=word_id(i + 1), text=f"word{i + 1}", start_us=round(a * 1e6), end_us=round(b * 1e6),
               kind=(kinds[i] if kinds else "word"))  # type: ignore[arg-type]
          for i, (a, b) in enumerate(words)]
    gaps: list[Gap] = []
    br = [(round(a * 1e6), round(b * 1e6)) for a, b in breaths]

    def is_breath(a: int, b: int) -> bool:
        return any(x < b and y > a for x, y in br)

    if ws and ws[0].start_us > 0:
        gaps.append(Gap(id="g0000", after_word_id=None, before_word_id=ws[0].id, start_us=0, end_us=ws[0].start_us,
                        kind="silence", snap_us=ws[0].start_us // 2))
    for a, b in itertools.pairwise(ws):
        if b.start_us - a.end_us >= 40_000:
            bflag = is_breath(a.end_us, b.start_us)
            gaps.append(Gap(id="g0000", after_word_id=a.id, before_word_id=b.id, start_us=a.end_us, end_us=b.start_us,
                            kind="breath" if bflag else "pause", snap_us=(a.end_us + b.start_us) // 2,
                            has_breath=bflag))
    end_us = round(duration_s * 1e6)
    if ws and end_us > ws[-1].end_us:
        gaps.append(Gap(id="g0000", after_word_id=ws[-1].id, before_word_id=None, start_us=ws[-1].end_us,
                        end_us=end_us, kind="silence", snap_us=(ws[-1].end_us + end_us) // 2))
    gaps = [g.model_copy(update={"id": gap_id(k)}) for k, g in enumerate(gaps, start=1)]
    rt = [(g.start_us, g.end_us) for g in gaps if g.duration_us >= 200_000 and not g.has_breath]
    return TakeIndex(media=media_info(duration_s), words=ws, gaps=gaps,
                     audio=AudioMetrics(snr_db=snr_db, music_in_room=music_in_room, room_tone_ranges_us=rt))


def make_timeline(specs: Sequence[dict], *, fps: Fraction = Fraction(30), words: Sequence[tuple[float, float]] = (),
                  tail_frames: int = 0, lead_frames: int = 0) -> Timeline:
    """Build a timeline from ``[{src_in, src_out, speed=1, lead_ms=0, lag_ms=0, kind="cut", gap_frames=0}]``
    (seconds). Output times are frame-snapped and cumulative; ``word_map`` maps every word inside a
    segment through ``out_start + (t - src_in) / speed``."""
    segs: list[TimelineSegment] = []
    word_map: dict[str, WordSpan | None] = {}
    t = frame_time(lead_frames, fps)
    for k, s in enumerate(specs, start=1):
        t += frame_time(s.get("gap_frames", 0), fps)
        sp = Fraction(str(s.get("speed", 1.0)))
        src_in, src_out = round(s["src_in"] * 1e6), round(s["src_out"] * 1e6)
        dur = snap_to_frame(Fraction(src_out - src_in, 1_000_000) / sp, fps)
        lead, lag = round(s.get("lead_ms", 0) * 1000), round(s.get("lag_ms", 0) * 1000)
        kind = s.get("kind", "jcut" if lead else "lcut" if lag else "cut")
        segs.append(TimelineSegment(
            seg_id=f"seg{k:03d}", src_in_us=src_in, src_out_us=src_out, out_start=t, out_end=t + dur, speed=float(sp),
            audio_src_in_us=src_in - lead, audio_src_out_us=src_out + lag,
            seam_in=SeamTreatment(kind=kind, lead_ms=max(s.get("lead_ms", 0), s.get("lag_ms", 0))),
            word_ids=[word_id(i + 1) for i, (a, b) in enumerate(words) if a >= s["src_in"] and b <= s["src_out"]],
        ))
        for i, (a, b) in enumerate(words):
            if a >= s["src_in"] and b <= s["src_out"]:
                o0 = t + (Fraction(round(a * 1e6), 1_000_000) - Fraction(src_in, 1_000_000)) / sp
                o1 = t + (Fraction(round(b * 1e6), 1_000_000) - Fraction(src_in, 1_000_000)) / sp
                word_map[word_id(i + 1)] = WordSpan(out_start=o0, out_end=o1)
        t += dur
    for i in range(len(words)):
        word_map.setdefault(word_id(i + 1), None)
    t += frame_time(tail_frames, fps)
    return Timeline(fps=fps, duration=t, segments=segs, word_map=word_map, seams=[s.out_start for s in segs[1:]])
