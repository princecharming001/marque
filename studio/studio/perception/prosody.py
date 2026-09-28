"""Prosody and energy (Take Index layer L3).

Audio LLMs sit near chance on paralinguistics, so emphasis and energy are *measured* here with Praat
(Parselmouth) and handed to the Director as numbers per word ID.

Per word (lexical words, fillers and cut-offs; audio events get ``prosody=None``):

* **f0** — the speaker-adapted two-pass Praat pitch track from :mod:`studio.perception.gaps`
  (pass 2 range ``0.75·q1 .. 2·q3``), in semitones re the speaker's median f0. Frames more than 9 st
  from the speaker median or 6 st from the word median are dropped as octave errors. Feature: the
  word's pitch *peak* (90th percentile; accents are peaks) and its range (p90 − p10: pitch movement).
  Needs ≥3 voiced frames (30 ms), else ``f0_z`` is None.
* **intensity** — Praat intensity (min pitch = the speaker's pitch floor), feature = the word's peak
  (90th percentile of the contour inside the word).
* **duration** — log duration regressed on log syllable count across the creator's own words
  (Theil-Sen), so a long word is not "emphatic" just for having four syllables; the residual is the
  feature.

``Prosody.f0_z/int_z/dur_z`` are robust z-scores (median / 1.4826·MAD, with a floor on the scale so a
monotone speaker does not blow up the z) against the creator's own lexical words, clipped to ±5.

**Emphasis** 0..1 combines *global* (whole take) and *local* (the word's sentence, or ±2.5 s)
prominence, because a pitch accent is prominent relative to its neighbours, not to the whole take:
``prom = ½·z_global + ½·z_local`` per feature, then
``P = 0.30·int⁺ + 0.25·f0⁺ + 0.15·range⁺ + 0.30·dur⁺`` (positive parts; weights renormalised over the
features a word has). Loudness and duration carry the most weight: they predict perceived
prominence in English conversational speech better than f0 alone (Kochanski et al. 2005). Duration is
discounted before a pause (×0.4, pre-boundary lengthening is not emphasis) and on function words
followed by a pause (×0.2, hesitation lengthening "theee…"). Function words get a ×0.7 prior;
fillers/cut-offs/events get 0. ``emphasis = sigmoid(2.2·(P − 1.1))`` (≈0.18 typical word, ≈0.5 at +1.1σ,
≈0.9 at +2σ on every cue). The Director combines this with meaning; it never chooses on prosody alone.

**Energy** (``Energy`` in the Take Index):

* ``wpm`` — lexical words per minute of speaking time (word runs split at pauses ≥1 s, so dead air and
  retake stalls do not deflate the rate);
* ``f0_var`` — pitch variability: robust standard deviation (IQR/1.349) of f0 in **semitones** over
  voiced frames inside lexical words (monotone ≈1.5-2 st, animated ≥4.5 st);
* ``loudness_var`` — robust standard deviation in **dB** of the per-word intensity peaks (flat ≈2 dB,
  punchy ≥6 dB);
* ``overall`` 0..1 = ``0.40·rate + 0.35·pitch + 0.25·loudness`` with each component mapped linearly
  between anchors (110→190 wpm, 1.5→5 st, 2→7 dB) and clipped. The anchors are priors for talking-head
  delivery (calm educator ≈0.2-0.35, conversational founder ≈0.4-0.6, hot take / sales ≥0.7); the doctrine
  matches cut density and pause length to this number and never fakes it with speed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from studio.perception import gaps as _gaps

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job
    from studio.perception.index import Energy, Sentence, Word

__all__ = [
    "WordMeasure",
    "analyze_prosody",
    "analyze_prosody_features",
    "measure_words",
    "compute_energy",
    "count_syllables",
    "FUNCTION_WORDS",
    "ENERGY_ANCHORS",
]

# weights / anchors (priors; see module docstring)
W_INT, W_F0, W_RANGE, W_DUR = 0.30, 0.25, 0.15, 0.30
EMPH_CENTRE, EMPH_SLOPE = 1.1, 2.2
FUNCTION_PRIOR = 0.7
FINAL_DUR_WEIGHT = 0.4
HESITATION_DUR_WEIGHT = 0.2
FINAL_PAUSE_US = 150_000
HESITATION_PAUSE_US = 200_000
LOCAL_WINDOW_US = 2_500_000
RUN_SPLIT_US = 1_000_000  # pauses at least this long are not "speaking time" for wpm
MIN_SCALE = {"f0": 0.75, "range": 0.75, "int": 1.5, "dur": 0.10}  # floors for robust z scales
ENERGY_ANCHORS = {"wpm": (110.0, 190.0), "f0_var": (1.5, 5.0), "loudness_var": (2.0, 7.0)}
ENERGY_WEIGHTS = {"wpm": 0.40, "f0_var": 0.35, "loudness_var": 0.25}

FUNCTION_WORDS = frozenset([
    "a", "an", "the", "and", "or", "but", "nor", "so", "yet", "of", "to", "in", "on", "at", "by", "for", "from",
    "with", "as", "into", "onto", "upon", "about", "over", "under", "than", "then", "that", "this", "these",
    "those", "there", "here", "it", "its", "i", "me", "my", "mine", "you", "your", "yours", "he", "him", "his",
    "she", "her", "hers", "we", "us", "our", "ours", "they", "them", "their", "theirs", "is", "am", "are", "was",
    "were", "be", "been", "being", "do", "does", "did", "have", "has", "had", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "just", "like", "um", "uh", "if", "when", "while", "which", "who",
    "whom", "whose", "what", "where",
])

_PUNCT_END = (".", "?", "!", ",", ";", ":", "—", "–", "…")


# ---------------------------------------------------------------------------------------------- helpers
def count_syllables(text: str) -> int:
    """Rough syllable count (English vowel groups; CJK/Kana one per character; digits ~1.5 each)."""
    t = text.strip().lower()
    if not t:
        return 1
    cjk = sum(1 for ch in t if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" or "가" <= ch <= "힯")
    if cjk:
        return max(1, cjk)
    digits = sum(ch.isdigit() for ch in t)
    letters = re.sub(r"[^a-zà-ÿ]", "", t)
    if not letters:
        return max(1, round(1.5 * digits)) if digits else 1
    groups = re.findall(r"[aeiouyà-ÿ]+", letters)
    n = len(groups)
    if letters.endswith("e") and not letters.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1  # silent final e
    if letters.endswith("ed") and not letters.endswith(("ted", "ded")) and n > 1:
        n -= 1  # "jumped"
    return max(1, n + (round(1.5 * digits) if digits else 0))


def _norm_token(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower()).strip("'")


def _robust_scale(values: np.ndarray, floor: float) -> float:
    if values.size < 2:
        return floor
    med = float(np.median(values))
    s = 1.4826 * float(np.median(np.abs(values - med)))
    if s < 1e-9:
        s = float(np.std(values))
    return max(s, floor)


def _iqr_sigma(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    q1, q3 = np.percentile(values, [25, 75])
    return float((q3 - q1) / 1.349)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class WordMeasure:
    """Raw per-word prosodic measurements (before z-scoring)."""

    id: str
    text: str
    kind: str
    start_us: int
    end_us: int
    n_syll: int
    f0_peak_st: float | None = None
    f0_range_st: float | None = None
    int_peak_db: float | None = None
    log_dur: float | None = None
    dur_resid: float | None = None
    voiced_frames: int = 0
    final: bool = False
    function_word: bool = False
    pause_after_us: int = 0
    sentence_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def lexical(self) -> bool:
        return self.kind == "word"


# ---------------------------------------------------------------------------------------------- measure
def _intensity_track(feat: _gaps.AudioFeatures, floor_hz: float) -> np.ndarray:
    """Praat intensity (dB) on the feature grid; NaN where undefined."""
    import parselmouth

    n = feat.n_frames
    out = np.full(n, np.nan)
    if feat.x.size < int(0.1 * feat.sr):
        return out
    snd = parselmouth.Sound(feat.x.astype(np.float64), sampling_frequency=float(feat.sr))
    it = snd.to_intensity(minimum_pitch=max(50.0, float(floor_hz)), time_step=_gaps.HOP_S, subtract_mean=True)
    vals = np.asarray(it.values, dtype=np.float64).reshape(-1)
    if vals.size == 0:
        return out
    x1 = float(it.xs()[0])
    dt = float(it.time_step)
    idx = np.rint((feat.t_us / 1_000_000.0 - x1) / dt).astype(np.int64)
    ok = (idx >= 0) & (idx < vals.size)
    out[ok] = vals[idx[ok]]
    out[feat.digital] = np.nan
    out[~np.isfinite(out)] = np.nan
    return out


def _word_frames(feat: _gaps.AudioFeatures, w: Word) -> np.ndarray:
    a = feat.frame_ceil(w.start_us)
    b = feat.frame_floor(w.end_us)
    if b < a:
        k = feat.frame_at((w.start_us + w.end_us) // 2)
        return np.array([k])
    return np.arange(a, b + 1)


def measure_words(feat: _gaps.AudioFeatures, words: list[Word], sentences: list[Sentence] | None = None
                  ) -> tuple[list[WordMeasure], float | None, np.ndarray]:
    """Raw measurements per word, the speaker's reference f0 (Hz) and the cleaned semitone track."""
    from scipy.stats import theilslopes

    pitch = feat.pitch
    f0 = pitch.f0_hz
    inten = _intensity_track(feat, pitch.floor_hz)
    sent_of: dict[str, str] = {}
    last_of_sentence: set[str] = set()
    for s in sentences or []:
        for wid in s.word_ids:
            sent_of[wid] = s.id
        if s.word_ids:
            last_of_sentence.add(s.word_ids[-1])

    # speaker reference f0: median over voiced frames inside lexical words
    lex_mask = np.zeros(feat.n_frames, dtype=bool)
    for w in words:
        if w.kind == "word":
            lex_mask[_word_frames(feat, w)] = True
    voiced = f0 > 0
    ref_frames = f0[lex_mask & voiced]
    if ref_frames.size < 3:
        ref_frames = f0[voiced]
    ref_hz = float(np.median(ref_frames)) if ref_frames.size >= 3 else None
    st = np.full(feat.n_frames, np.nan)
    if ref_hz is not None:
        st[voiced] = 12.0 * np.log2(f0[voiced] / ref_hz)
        st[np.abs(st) > 9.0] = np.nan  # octave jumps / tracking errors relative to the speaker

    out: list[WordMeasure] = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause_after = max(0, nxt.start_us - w.end_us) if nxt is not None else 10**9
        tok = _norm_token(w.text)
        m = WordMeasure(
            id=w.id, text=w.text, kind=w.kind, start_us=w.start_us, end_us=w.end_us,
            n_syll=count_syllables(w.text), function_word=tok in FUNCTION_WORDS,
            pause_after_us=int(min(pause_after, 10**9)),
            sentence_id=w.sentence_id or sent_of.get(w.id),
        )
        m.final = (pause_after >= FINAL_PAUSE_US or w.id in last_of_sentence
                   or w.text.strip().endswith(_PUNCT_END))
        if w.kind == "event":
            out.append(m)
            continue
        fr = _word_frames(feat, w)
        sv = st[fr]
        sv = sv[np.isfinite(sv)]
        if sv.size >= 3:
            med = float(np.median(sv))
            sv = sv[np.abs(sv - med) <= 6.0]
        m.voiced_frames = int(sv.size)
        if sv.size >= 3:
            m.f0_peak_st = float(np.percentile(sv, 90))
            m.f0_range_st = float(np.percentile(sv, 90) - np.percentile(sv, 10))
        iv = inten[fr]
        iv = iv[np.isfinite(iv)]
        if iv.size:
            m.int_peak_db = float(np.percentile(iv, 90))
        dur_s = max(w.end_us - w.start_us, 1_000) / 1e6
        m.log_dur = math.log(dur_s)
        out.append(m)

    # duration: residual of log(dur) on log(syllables) across the creator's lexical words
    lex = [m for m in out if m.lexical and m.log_dur is not None]
    slope, icpt = 1.0, None
    if len(lex) >= 8:
        xs = np.log(np.array([m.n_syll for m in lex], dtype=np.float64))
        ys = np.array([m.log_dur for m in lex], dtype=np.float64)
        if np.ptp(xs) > 0:
            res = theilslopes(ys, xs)
            slope = float(np.clip(res[0], 0.3, 1.2))
            icpt = float(np.median(ys - slope * xs))
    if icpt is None and lex:
        icpt = float(np.median([m.log_dur - slope * math.log(m.n_syll) for m in lex]))  # type: ignore[operator]
    for m in out:
        if m.log_dur is not None and icpt is not None and m.kind != "event":
            m.dur_resid = m.log_dur - (icpt + slope * math.log(m.n_syll))
    return out, ref_hz, st


# ---------------------------------------------------------------------------------------------- z / emphasis
def _z_tables(measures: list[WordMeasure]) -> dict[str, dict[str, float | None]]:
    """Global robust z per feature for every non-event word, against lexical words."""
    feats = {"f0": "f0_peak_st", "range": "f0_range_st", "int": "int_peak_db", "dur": "dur_resid"}
    out: dict[str, dict[str, float | None]] = {k: {} for k in feats}
    for key, attr in feats.items():
        base = np.array([getattr(m, attr) for m in measures if m.lexical and getattr(m, attr) is not None],
                        dtype=np.float64)
        if base.size < 3:
            continue
        med = float(np.median(base))
        scale = _robust_scale(base, MIN_SCALE[key])
        for m in measures:
            v = getattr(m, attr)
            if m.kind != "event" and v is not None:
                out[key][m.id] = float(np.clip((v - med) / scale, -5.0, 5.0))
    return out


def _local_z(measures: list[WordMeasure], key: str, attr: str, scale: float) -> dict[str, float]:
    """z of each word against the median of its neighbours (same sentence, else ±2.5 s)."""
    out: dict[str, float] = {}
    lex = [m for m in measures if m.lexical and getattr(m, attr) is not None]
    by_sent: dict[str, list[WordMeasure]] = {}
    for m in lex:
        if m.sentence_id:
            by_sent.setdefault(m.sentence_id, []).append(m)
    for m in measures:
        v = getattr(m, attr)
        if m.kind == "event" or v is None:
            continue
        pool = [o for o in by_sent.get(m.sentence_id or "", []) if o.id != m.id]
        if len(pool) < 3:
            centre = (m.start_us + m.end_us) // 2
            pool = [o for o in lex if o.id != m.id and abs((o.start_us + o.end_us) // 2 - centre) <= LOCAL_WINDOW_US]
        if len(pool) < 2:
            continue
        med = float(np.median([getattr(o, attr) for o in pool]))
        out[m.id] = float(np.clip((v - med) / scale, -5.0, 5.0))
    return out


def _emphasis(measures: list[WordMeasure]) -> tuple[dict[str, float], dict[str, dict[str, float | None]]]:
    feats = {"f0": "f0_peak_st", "range": "f0_range_st", "int": "int_peak_db", "dur": "dur_resid"}
    weights = {"int": W_INT, "f0": W_F0, "range": W_RANGE, "dur": W_DUR}
    zg = _z_tables(measures)
    zl: dict[str, dict[str, float]] = {}
    for key, attr in feats.items():
        base = np.array([getattr(m, attr) for m in measures if m.lexical and getattr(m, attr) is not None],
                        dtype=np.float64)
        scale = _robust_scale(base, MIN_SCALE[key]) if base.size >= 3 else MIN_SCALE[key]
        zl[key] = _local_z(measures, key, attr, scale)
    emph: dict[str, float] = {}
    for m in measures:
        if m.kind in ("event", "filler", "cutoff"):
            emph[m.id] = 0.0
            continue
        total_w = 0.0
        p = 0.0
        for key, wgt in weights.items():
            g = zg[key].get(m.id)
            if g is None:
                continue
            loc = zl[key].get(m.id, g)
            prom = max(0.0, 0.5 * g + 0.5 * loc)
            if key == "dur":
                if m.function_word and m.pause_after_us >= HESITATION_PAUSE_US:
                    prom *= HESITATION_DUR_WEIGHT
                elif m.final:
                    prom *= FINAL_DUR_WEIGHT
            p += wgt * min(prom, 4.0)
            total_w += wgt
        if total_w <= 0:
            emph[m.id] = 0.0
            continue
        p /= total_w
        e = _sigmoid(EMPH_SLOPE * (p - EMPH_CENTRE))
        if m.function_word:
            e *= FUNCTION_PRIOR
        emph[m.id] = round(float(np.clip(e, 0.0, 1.0)), 3)
    return emph, zg  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------- energy
def _speaking_time_us(words: list[Word]) -> int:
    spoken = [w for w in words if w.kind != "event"]
    if not spoken:
        return 0
    total = 0
    run_start = spoken[0].start_us
    run_end = spoken[0].end_us
    for w in spoken[1:]:
        if w.start_us - run_end >= RUN_SPLIT_US:
            total += run_end - run_start
            run_start = w.start_us
        run_end = max(run_end, w.end_us)
    total += run_end - run_start
    return max(total, 0)


def _anchor(value: float, key: str) -> float:
    lo, hi = ENERGY_ANCHORS[key]
    return float(np.clip((value - lo) / (hi - lo), 0.0, 1.0))


def compute_energy(words: list[Word], measures: list[WordMeasure], st_track: np.ndarray,
                   feat: _gaps.AudioFeatures) -> Energy:
    from studio.perception.index import Energy

    n_lex = sum(1 for w in words if w.kind == "word")
    t_us = _speaking_time_us(words)
    wpm = 60.0 * n_lex / (t_us / 1e6) if t_us > 0 else 0.0
    lex_mask = np.zeros(feat.n_frames, dtype=bool)
    for w in words:
        if w.kind == "word":
            lex_mask[_word_frames(feat, w)] = True
    stv = st_track[lex_mask]
    stv = stv[np.isfinite(stv)]
    f0_var = _iqr_sigma(stv) if stv.size >= 20 else 0.0
    ints = np.array([m.int_peak_db for m in measures if m.lexical and m.int_peak_db is not None], dtype=np.float64)
    loud_var = _iqr_sigma(ints) if ints.size >= 3 else 0.0
    parts = {"wpm": _anchor(wpm, "wpm") if n_lex else 0.0,
             "f0_var": _anchor(f0_var, "f0_var") if stv.size >= 20 else 0.0,
             "loudness_var": _anchor(loud_var, "loudness_var") if ints.size >= 3 else 0.0}
    overall = sum(ENERGY_WEIGHTS[k] * v for k, v in parts.items())
    return Energy(wpm=round(wpm, 1), f0_var=round(f0_var, 2), loudness_var=round(loud_var, 2),
                  overall=round(float(np.clip(overall, 0.0, 1.0)), 3))


# ---------------------------------------------------------------------------------------------- public
def analyze_prosody_features(feat: _gaps.AudioFeatures, words: list[Word], sentences: list[Sentence] | None = None
                             ) -> tuple[list[Word], Energy]:
    """Prosody/emphasis for ``words`` (order and IDs preserved) and the take energy."""
    from studio.perception.index import Energy, Prosody

    if not words:
        return [], Energy()
    measures, _ref, st_track = measure_words(feat, words, sentences)
    emph, zg = _emphasis(measures)
    out: list[Word] = []
    for w in words:
        if w.kind == "event":
            out.append(w.model_copy(update={"prosody": None, "emphasis": 0.0}))
            continue
        f0z, iz, dz = zg["f0"].get(w.id), zg["int"].get(w.id), zg["dur"].get(w.id)
        pros = None
        if f0z is not None or iz is not None or dz is not None:
            pros = Prosody(f0_z=None if f0z is None else round(f0z, 3), int_z=None if iz is None else round(iz, 3),
                           dur_z=None if dz is None else round(dz, 3))
        out.append(w.model_copy(update={"prosody": pros, "emphasis": emph.get(w.id, 0.0)}))
    return out, compute_energy(words, measures, st_track, feat)


def analyze_prosody(job: Job | _gaps.AudioFeatures | str, words: list[Word], sentences: list[Sentence]
                    ) -> tuple[list[Word], Energy]:
    """Return words with ``prosody``/``emphasis`` filled, and the take energy.

    ``job`` may be a :class:`~studio.jobs.Job` (reads ``media/audio.wav``), an audio path or
    :class:`~studio.perception.gaps.AudioFeatures`. Without audio the words come back unchanged with a
    rate-only :class:`Energy`.
    """
    from studio.perception.index import Energy

    if not words:
        return [], Energy()
    if not _gaps._audio_available(job):
        n_lex = sum(1 for w in words if w.kind == "word")
        t_us = _speaking_time_us(words)
        wpm = 60.0 * n_lex / (t_us / 1e6) if t_us > 0 else 0.0
        return list(words), Energy(wpm=round(wpm, 1), overall=round(0.4 * _anchor(wpm, "wpm"), 3))
    return analyze_prosody_features(_gaps._features(job), list(words), list(sentences or []))
