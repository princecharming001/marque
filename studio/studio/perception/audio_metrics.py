"""Audio measurements for one take (Take Index layer L0 audio): the numbers the voice chain, the loudness
stage and the critics read, since no model in the loop can hear.

* ``noise_floor_db`` — power-mean full-band (≥50 Hz) level of the take's room tone (the stationary
  stretches from :func:`studio.perception.gaps.room_tone_ranges`), else the median of VAD non-speech
  frames; -120 when the pauses are digital silence (a gated / voice-isolated phone recording).
* ``snr_db`` — active speech level minus noise floor, both full band: the speech level is the power
  mean of active frames inside words (or outside gaps / VAD speech when no words are given), as in
  ITU-T P.56. Capped at 100 dB. ``extras.snr_voiceband_db`` repeats it on 250 Hz-12 kHz (what is left
  after the chain's high-pass; rooms are usually rumble-dominated) and ``extras.snr_p10_db`` is the
  10th percentile over words / speech runs (the quiet words a noisy room eats first).
* ``clipping_ratio`` — fraction of samples at |x| ≥ 0.985 (-0.13 dBFS) or inside flat-topped runs (≥4
  identical samples at |x| ≥ 0.5: clipping that was later turned down).
* ``integrated_lufs`` / ``lra_lu`` — ITU-R BS.1770-4 / EBU Tech 3342 via pyloudnorm (mono is one
  channel, as ffmpeg ``ebur128`` treats it).
* ``true_peak_dbtp`` — BS.1770-4 Annex 2 true peak: 4× oversampling with soxr's VHQ linear-phase
  resampler (a better reconstruction filter than the reference 48-tap FIR), streamed in chunks.
* ``rt60_est`` — a blind *proxy*: free decays after word offsets into pauses ≥200 ms are fitted
  T20-style (from -5 to -25 dB below the offset peak, 300 Hz-3 kHz, R² ≥ 0.8); the median of the
  fastest third is reported (speech offsets can only be slower than the room). None with fewer than
  three usable decays. Advice only.
* ``music_in_room`` — pauses that are loud relative to speech (dynamic range < 22.5 dB) *and* periodic
  (Praat voicing), or pauses whose spectra carry ≥3 sharp tonal partials in most frames, counting only
  partials within 30 dB of the speech's strongest harmonics and ignoring stationary tones (mains hum,
  fan whine) — the second test catches quiet music that would audibly jump at cuts.
  ``extras.music`` holds the components.
* ``room_tone_ranges_us`` — see :func:`studio.perception.gaps.room_tone_ranges` (≥300 ms; when a clean
  take has no pause that long, stretches ≥150 ms, with ``extras.room_tone_min_ms`` = 150).
* ``extras`` — ``speech_level_db``, ``sample_peak_dbfs``, ``digital_silence_ratio``, ``gated_floor``
  (floor below -75 dBFS: the phone's voice isolation already ran, do not denoise again),
  ``hum`` (50/60 Hz mains hum level above the room-tone spectrum), ``sibilance_ratio_db``
  (5-9 kHz vs 0.1-5 kHz in speech), ``bandwidth_hz`` (where the speech spectrum falls 50 dB below its
  peak: codec/lowpass cut-off), ``breaths`` (count and median level re speech), ``rt60_decays``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import numpy as np

from studio.perception import gaps as _gaps

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job
    from studio.perception.index import AudioMetrics, Gap, Word

__all__ = [
    "measure_audio",
    "measure_audio_file",
    "measure_features",
    "integrated_loudness",
    "loudness_range",
    "true_peak_dbtp",
    "sample_peak_dbfs",
    "clipping_ratio",
    "estimate_rt60",
    "music_in_room",
]

CLIP_LEVEL = 0.985
FLAT_RUN = 4
SNR_CAP_DB = 100.0
GATED_FLOOR_DB = -75.0
MUSIC_DR_DB = 22.5
ROOM_TONE_FALLBACK_MS = 150


# ---------------------------------------------------------------------------------------------- loudness / peaks
def integrated_loudness(x: np.ndarray, sr: int) -> float | None:
    """BS.1770-4 integrated loudness (LUFS) of a mono signal; None if shorter than 0.4 s or silent."""
    import pyloudnorm as pyln

    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if x.size < int(0.4 * sr) + 1:
        return None
    val = pyln.Meter(sr).integrated_loudness(x)
    return float(val) if np.isfinite(val) else None


def loudness_range(x: np.ndarray, sr: int) -> float | None:
    """EBU Tech 3342 loudness range (LU); None if shorter than 3 s or too quiet."""
    import pyloudnorm as pyln

    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if x.size < 3 * sr:
        return None
    try:
        val = pyln.Meter(sr).loudness_range(x)
    except Exception:
        return None
    return float(val) if np.isfinite(val) else None


def sample_peak_dbfs(x: np.ndarray) -> float:
    peak = float(np.max(np.abs(x))) if np.size(x) else 0.0
    return float(20 * np.log10(peak)) if peak > 0 else _gaps.DB_FLOOR


def true_peak_dbtp(x: np.ndarray, sr: int, *, oversample: int = 4) -> float:
    """True peak (dBTP) by ``oversample``× band-limited interpolation (soxr VHQ), chunked."""
    import soxr

    x = np.asarray(x, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return _gaps.DB_FLOOR
    rs = soxr.ResampleStream(sr, sr * oversample, 1, dtype="float32", quality="VHQ")
    chunk = max(sr, 1)
    peak = 0.0
    for i in range(0, x.size, chunk):
        part = x[i:i + chunk]
        y = rs.resample_chunk(part, last=i + chunk >= x.size)
        if y.size:
            peak = max(peak, float(np.max(np.abs(y))))
    peak = max(peak, float(np.max(np.abs(x))))  # never below the sample peak
    return float(20 * np.log10(peak)) if peak > 0 else _gaps.DB_FLOOR


def clipping_ratio(x: np.ndarray) -> tuple[float, int]:
    """(fraction of clipped samples, number of clipped runs). Clipped = |x| ≥ 0.985 or a flat top
    (≥4 identical consecutive samples at |x| ≥ 0.5)."""
    a = np.abs(np.asarray(x, dtype=np.float32).reshape(-1))
    if a.size == 0:
        return 0.0, 0
    clipped = a >= CLIP_LEVEL
    hot = a >= 0.5
    if hot.any():
        same = np.concatenate(([False], a[1:] == a[:-1])) & hot
        # mark runs of ≥FLAT_RUN identical hot samples
        for s, e in _gaps._runs(same):
            if e - s + 1 >= FLAT_RUN:
                clipped[s - 1:e] = True
    runs = len(_gaps._runs(clipped))
    return float(clipped.mean()), runs


# ---------------------------------------------------------------------------------------------- masks
def _gap_interior(feat: _gaps.AudioFeatures, gaps: list[Gap] | None, *, after_guard_us: int = 100_000,
                  before_guard_us: int = 30_000) -> np.ndarray | None:
    if not gaps:
        return None
    m = np.zeros(feat.n_frames, dtype=bool)
    for g in gaps:
        s = g.start_us + (after_guard_us if g.after_word_id is not None else 0)
        e = g.end_us - (before_guard_us if g.before_word_id is not None else 0)
        if e > s:
            m[feat.frame_ceil(s):feat.frame_floor(e) + 1] = True
    return m


def _word_mask(feat: _gaps.AudioFeatures, words: list[Word] | None) -> np.ndarray | None:
    if not words:
        return None
    m = np.zeros(feat.n_frames, dtype=bool)
    for w in words:
        if w.kind != "event" and w.end_us > w.start_us:
            m[feat.frame_ceil(w.start_us):feat.frame_floor(w.end_us) + 1] = True
    return m


def _speech_mask(feat: _gaps.AudioFeatures, gaps: list[Gap] | None, words: list[Word] | None) -> np.ndarray:
    thr = _gaps._thresholds(feat, words)
    nd = ~feat.digital
    wm = _word_mask(feat, words)
    if wm is not None:
        m = wm & thr.active & nd
    else:
        gm = _gap_interior(feat, gaps, after_guard_us=0, before_guard_us=0)
        m = (~gm & thr.active & nd) if gm is not None else (feat.vad >= 0.5) & thr.active & nd
    if m.sum() < 10:
        m = (feat.vad >= 0.5) & nd
    if m.sum() < 10:
        m = nd & (feat.vb_db >= np.percentile(feat.vb_db[nd], 70)) if nd.any() else nd
    return m


# ---------------------------------------------------------------------------------------------- rt60
def estimate_rt60(feat: _gaps.AudioFeatures, gaps: list[Gap] | None = None) -> tuple[float | None, int]:
    """Blind RT60 proxy (s) from free decays after word offsets, and the number of decays used."""
    band = 10 * np.log10(np.power(10, feat.band_db[:, 0] / 10) + np.power(10, feat.band_db[:, 1] / 10) + 1e-12)
    band[feat.digital] = _gaps.DB_FLOOR
    ns = _gaps._noise_frames(feat, band)
    noise = float(np.median(band[ns])) if ns.any() else _gaps.DB_FLOOR
    starts: list[int] = []
    if gaps:
        starts = [g.start_us for g in gaps if g.after_word_id is not None and g.duration_us >= 200_000]
    else:
        v = feat.vad >= 0.5
        for a, b in _gaps._runs(~v):
            if b - a >= 20 and a > 0:
                starts.append(feat.frame_time(a))
    hop_s = feat.hop / feat.sr
    rts: list[float] = []
    for t0 in starts:
        a = feat.frame_ceil(t0 - 150_000)
        b = feat.frame_floor(t0 + 20_000)
        if b <= a:
            continue
        p = a + int(np.argmax(band[a:b + 1]))
        top = float(band[p])
        floor_lim = noise + 5.0
        if top - floor_lim < 15.0:
            continue
        end = min(feat.n_frames, p + int(0.6 / hop_s))
        y = band[p:end]
        i0 = np.flatnonzero(y <= top - 5.0)
        if i0.size == 0:
            continue
        i0 = int(i0[0])
        target = max(top - 25.0, floor_lim)
        i1c = np.flatnonzero(y[i0:] <= target)
        if i1c.size == 0:
            continue
        i1 = i0 + int(i1c[0])
        if i1 - i0 < 3 or top - target < 12.0:
            continue
        seg = y[i0:i1 + 1]
        tt = np.arange(seg.size) * hop_s
        slope, icpt = np.polyfit(tt, seg, 1)
        fit = slope * tt + icpt
        ss_res = float(np.sum((seg - fit) ** 2))
        ss_tot = float(np.sum((seg - np.mean(seg)) ** 2)) + 1e-12
        if slope >= -1e-6 or 1 - ss_res / ss_tot < 0.8:
            continue
        rts.append(float(-60.0 / slope))
    if len(rts) < 3:
        return None, len(rts)
    rts.sort()
    fastest = rts[: max(1, len(rts) // 3)]
    return float(np.clip(np.median(fastest), 0.05, 3.0)), len(rts)


# ---------------------------------------------------------------------------------------------- music
def _tonal_fraction(feat: _gaps.AudioFeatures, mask: np.ndarray, speech_mask: np.ndarray) -> tuple[float, int]:
    """Fraction of 85 ms spectra over ``mask`` runs (≥200 ms) holding ≥3 sharp tonal partials (≥10 dB above
    the local spectral median, 150 Hz-5 kHz) that are loud enough to matter — within 30 dB of the speech's
    own strongest harmonic — and not stationary (a partial present, ±2 bins, in ≥60 % of spectra is a
    machine tone or mains hum, not music). Also returns the number of spectra examined."""
    from scipy.ndimage import median_filter
    from scipy.signal import get_window

    sr = feat.sr
    nfft = 1 << int(np.ceil(np.log2(0.085 * sr)))
    hop = nfft // 4
    w = get_window("hann", nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    band = (freqs >= 150) & (freqs <= 5000)

    def spectra(m: np.ndarray, step: int, min_len: int):
        for a, b in _gaps._runs(m):
            s0, s1 = a * feat.hop, min(len(feat.x_hp), b * feat.hop)
            if s1 - s0 < min_len:
                continue
            seg = feat.x_hp[s0:s1].astype(np.float64)
            for i in range(0, seg.size - nfft + 1, step):
                yield 10 * np.log10(np.abs(np.fft.rfft(seg[i:i + nfft] * w)) ** 2 + 1e-20)

    tops = [float(np.max(ldb[band])) for ldb in spectra(speech_mask, 2 * nfft, nfft)]
    if not tops:
        return 0.0, 0
    floor_level = float(np.median(tops)) - 30.0
    peak_sets: list[np.ndarray] = []
    for ldb in spectra(mask, hop, max(nfft, int(0.2 * sr))):
        prom = ldb - median_filter(ldb, size=31, mode="nearest")
        is_peak = np.zeros(ldb.size, dtype=bool)
        is_peak[1:-1] = ((prom[1:-1] >= 10.0) & (ldb[1:-1] >= ldb[:-2]) & (ldb[1:-1] >= ldb[2:])
                         & (ldb[1:-1] >= floor_level))
        peak_sets.append(np.flatnonzero(is_peak & band))
    total = len(peak_sets)
    if total == 0:
        return 0.0, 0
    presence = np.zeros(freqs.size)
    for bins in peak_sets:
        hit = np.zeros(freqs.size, dtype=bool)
        for d in (-2, -1, 0, 1, 2):
            hit[np.clip(bins + d, 0, freqs.size - 1)] = True
        presence += hit
    stationary = presence >= 0.6 * total if total >= 10 else np.zeros(freqs.size, dtype=bool)
    tonal = sum(int(np.sum(~stationary[bins])) >= 3 for bins in peak_sets)
    return tonal / total, total


def music_in_room(feat: _gaps.AudioFeatures, gaps: list[Gap] | None = None, words: list[Word] | None = None
                  ) -> tuple[bool, dict[str, Any]]:
    """Heuristic: is there music (or other periodic background) under the voice? Returns (flag, detail)."""
    nd = ~feat.digital
    gm = _gap_interior(feat, gaps)
    if gm is not None:
        q = gm & nd & ~_gaps._dilate(feat.breath_mask, 2)
    else:
        q = nd & (feat.vad < 0.3)
    src = "gaps" if gm is not None else "vad"
    if q.sum() < 50:
        q = nd & (feat.vb_smooth <= np.percentile(feat.vb_smooth[nd], 20)) if nd.any() else nd
        src = "quietest20"
    detail: dict[str, Any] = {"source": src, "frames": int(q.sum())}
    if q.sum() < 20:
        detail["reason"] = "not enough pause audio"
        return False, detail
    sp = _speech_mask(feat, gaps, words)
    speech_vb = _gaps._power_mean_db(feat.vb_db[sp])
    pause_vb = _gaps._power_mean_db(feat.vb_db[q])
    dr = speech_vb - pause_vb
    voiced = float(np.mean(feat.pitch.voiced[q]))
    tonal, n_spec = _tonal_fraction(feat, q, sp)
    loud_periodic = dr < MUSIC_DR_DB and voiced >= 0.15
    tonal_partials = n_spec >= 6 and tonal >= 0.5
    score = max(float(np.clip((30.0 - dr) / 15.0, 0, 1)) * float(np.clip(voiced / 0.3, 0, 1)),
                float(np.clip((tonal - 0.2) / 0.5, 0, 1)) if n_spec >= 6 else 0.0)
    detail.update({"pause_dr_db": round(dr, 2), "pause_voiced": round(voiced, 3), "tonal_fraction": round(tonal, 3),
                   "tonal_spectra": n_spec, "score": round(score, 3)})
    return bool(loud_periodic or tonal_partials), detail


# ---------------------------------------------------------------------------------------------- spectra extras
def _welch_mean(feat: _gaps.AudioFeatures, mask: np.ndarray, nperseg: int) -> tuple[np.ndarray, np.ndarray] | None:
    from scipy.signal import welch

    acc = None
    weight = 0
    freqs = None
    for a, b in _gaps._runs(mask):
        s0, s1 = a * feat.hop, min(len(feat.x), b * feat.hop)
        if s1 - s0 < nperseg:
            continue
        f, p = welch(feat.x[s0:s1].astype(np.float64), fs=feat.sr, nperseg=nperseg)
        n = s1 - s0
        acc = p * n if acc is None else acc + p * n
        weight += n
        freqs = f
    if acc is None or freqs is None:
        return None
    return freqs, acc / weight


def _hum(feat: _gaps.AudioFeatures, room_mask: np.ndarray) -> dict[str, Any] | None:
    res = _welch_mean(feat, room_mask, 16384)
    if res is None:
        return None
    f, p = res
    ldb = 10 * np.log10(p + 1e-20)
    best: dict[str, Any] | None = None
    for base in (50.0, 60.0):
        prom = []
        for h in (1, 2, 3):
            fk = base * h
            k = int(np.argmin(np.abs(f - fk)))
            lo, hi = max(0, k - 40), min(f.size, k + 41)
            ring = np.r_[ldb[lo:max(lo, k - 5)], ldb[min(hi, k + 6):hi]]
            if ring.size == 0:
                continue
            prom.append(float(np.max(ldb[max(0, k - 2):k + 3]) - np.median(ring)))
        if prom:
            m = float(max(prom))
            if best is None or m > best["prominence_db"]:
                best = {"hz": base, "prominence_db": round(m, 1)}
    if best is None:
        return None
    best["present"] = bool(best["prominence_db"] >= 12.0)
    return best


def _speech_spectrum_extras(feat: _gaps.AudioFeatures, speech_mask: np.ndarray) -> dict[str, Any]:
    res = _welch_mean(feat, speech_mask, 4096)
    out: dict[str, Any] = {}
    if res is None:
        return out
    f, p = res
    lo = (f >= 100) & (f < 5000)
    sib = (f >= 5000) & (f < 9000)
    if lo.any() and sib.any():
        out["sibilance_ratio_db"] = round(float(10 * np.log10(p[sib].sum() / (p[lo].sum() + 1e-20) + 1e-20)), 2)
    ldb = 10 * np.log10(p + 1e-20)
    sm = np.convolve(ldb, np.ones(9) / 9, mode="same")
    peak = float(np.max(sm[(f >= 100) & (f <= 5000)])) if lo.any() else float(np.max(sm))
    above = np.flatnonzero((sm >= peak - 50.0) & (f >= 1000))
    if above.size:
        out["bandwidth_hz"] = int(round(float(f[above[-1]]) / 100.0) * 100)
    return out


# ---------------------------------------------------------------------------------------------- main
def measure_features(feat: _gaps.AudioFeatures, *, gaps: list[Gap] | None = None, words: list[Word] | None = None
                     ) -> AudioMetrics:
    """All measurements for analysed audio (see module docstring)."""
    from studio.perception.index import AudioMetrics

    x = feat.x
    sr = feat.sr
    nd = ~feat.digital
    room = _gaps.room_tone_ranges(feat, gaps)
    room_min_ms = _gaps.ROOM_TONE_MIN_US // 1000
    if not room:  # clean takes with only short pauses: shorter loopable stretches beat none
        room = _gaps.room_tone_ranges(feat, gaps, min_ms=ROOM_TONE_FALLBACK_MS)
        room_min_ms = ROOM_TONE_FALLBACK_MS
    room_mask = np.zeros(feat.n_frames, dtype=bool)
    for s, e in room:
        room_mask[feat.frame_ceil(s):feat.frame_floor(e) + 1] = True
    room_mask &= nd
    if room_mask.sum() >= 10:
        noise_db = _gaps._power_mean_db(feat.db[room_mask])
        noise_vb = _gaps._power_mean_db(feat.vb_db[room_mask])
    elif nd.any() and not (feat.digital.mean() > 0.2 and _gaps._noise_frames(feat, feat.db).sum() < 10):
        noise_db = feat.noise_db
        noise_vb = feat.vbs.noise_med if feat.vbs is not None else _gaps.DB_FLOOR
    else:
        noise_db = noise_vb = _gaps.DB_FLOOR
    gap_frames = _gap_interior(feat, gaps, after_guard_us=0, before_guard_us=0)
    if gaps and gap_frames is not None and gap_frames.any():
        gap_digital = float(np.mean(feat.digital[gap_frames]))
        if gap_digital >= 0.5 and room_mask.sum() < 10:
            noise_db = noise_vb = _gaps.DB_FLOOR  # pauses are digital silence

    sp = _speech_mask(feat, gaps, words)
    speech_db = _gaps._power_mean_db(feat.db[sp])
    speech_vb = _gaps._power_mean_db(feat.vb_db[sp])
    snr = float(min(SNR_CAP_DB, speech_db - noise_db))
    snr_vb = float(min(SNR_CAP_DB, speech_vb - noise_db if noise_vb <= _gaps.DB_FLOOR else speech_vb - noise_vb))

    # per-word (or per speech run) SNR, 10th percentile
    levels: list[float] = []
    if words:
        for w in words:
            if w.kind == "event" or w.end_us - w.start_us < 60_000:
                continue
            fr = slice(feat.frame_ceil(w.start_us), feat.frame_floor(w.end_us) + 1)
            seg = feat.db[fr][nd[fr]]
            if seg.size:
                levels.append(_gaps._power_mean_db(seg))
    else:
        for a, b in _gaps._runs(sp):
            if b - a >= 10:
                levels.append(_gaps._power_mean_db(feat.db[a:b]))
    snr_p10 = float(min(SNR_CAP_DB, np.percentile(levels, 10) - noise_db)) if levels else None

    clip, clip_runs = clipping_ratio(x)
    lufs = integrated_loudness(x, sr)
    lra = loudness_range(x, sr)
    tp = true_peak_dbtp(x, sr)
    rt60, n_decays = estimate_rt60(feat, gaps)
    music, music_detail = music_in_room(feat, gaps, words)
    breaths = _gaps.find_breaths(feat, gaps) if gaps else _gaps.find_breaths(feat)

    extras: dict[str, Any] = {
        "speech_level_db": round(speech_db, 2),
        "snr_voiceband_db": round(snr_vb, 2),
        "snr_p10_db": None if snr_p10 is None else round(snr_p10, 2),
        "sample_peak_dbfs": round(sample_peak_dbfs(x), 2),
        "clipped_runs": clip_runs,
        "digital_silence_ratio": round(float(feat.digital.mean()), 4),
        "gated_floor": bool(noise_db <= GATED_FLOOR_DB),
        "room_tone_s": round(sum(e - s for s, e in room) / 1e6, 3),
        "room_tone_min_ms": room_min_ms,
        "rt60_decays": n_decays,
        "music": music_detail,
        "breaths": {"count": len(breaths),
                    "median_rel_speech_db": round(float(np.median([b.rel_speech_db for b in breaths])), 2)
                    if breaths else None},
    }
    hum = _hum(feat, room_mask) if room_mask.sum() >= 30 else None
    if hum is not None:
        extras["hum"] = hum
    extras.update(_speech_spectrum_extras(feat, sp))

    def r(v: float | None, nd_: int = 2) -> float | None:
        return None if v is None else round(float(v), nd_)

    return AudioMetrics(
        noise_floor_db=r(noise_db),
        snr_db=r(snr),
        clipping_ratio=round(float(np.clip(clip, 0.0, 1.0)), 6),
        integrated_lufs=r(lufs),
        true_peak_dbtp=r(tp),
        lra_lu=r(lra),
        rt60_est=r(rt60, 3),
        music_in_room=music,
        room_tone_ranges_us=[(int(s), int(e)) for s, e in room],
        extras=extras,
    )


def measure_audio_file(path: str | os.PathLike[str], *, gaps: list[Gap] | None = None,
                       words: list[Word] | None = None) -> AudioMetrics:
    """Measure any audio/video file (decoded with soundfile or ffmpeg)."""
    return measure_features(_gaps.load_features(path), gaps=gaps, words=words)


def measure_audio(job: Job, *, gaps: list[Gap] | None = None, words: list[Word] | None = None) -> AudioMetrics:
    """Measure ``job.audio_path``; ``gaps`` locate room tone and pauses, ``words`` the speech (both optional)."""
    path = job.audio_path
    if not path.exists():
        raise FileNotFoundError(f"no dialogue audio at {path} (run ingest first)")
    return measure_features(_gaps.load_features(path), gaps=gaps, words=words)
