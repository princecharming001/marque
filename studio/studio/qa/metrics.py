"""Metrics packet: measurements of a rendered deliverable, advice for critics (never a gate by itself).

``measure(job, timeline, final_path) -> MetricsPacket`` measures **the encoded file** (what a viewer
gets), cross-checked against the compiled :class:`~studio.compile.models.Timeline` and the Take Index.
The hard gates in :mod:`studio.qa.invariants` read their evidence from this packet; everything else is
information for the Director and the critics, with doctrine priors from ``skills/editing/constants.yaml``
turned into plain-language ``advice`` lines.

What is measured, and why this way
----------------------------------
* **Loudness** — BS.1770-4 integrated loudness, short-term/momentary maxima and a 4× (soxr VHQ) true
  peak on the *decoded AAC* (the codec's overshoot is part of what the platform receives), cross-checked
  by an independent meter (FFmpeg ``ebur128=peak=true``). The gating true peak is the **higher** of the
  two meters; the integrated value is ours (the two agree to ~0.1 LU; a larger disagreement is reported
  as an error). Mono fold-down loudness change is reported for phone speakers.
* **Clicks at every audio seam** (±5 ms around the J/L-shifted audio edit from
  :func:`studio.compile.timeline.audio_seams`), per channel, two complementary detectors calibrated on
  real speech from the QA takes (1 600+ natural positions and 10/5 ms crossfaded splices produced no
  false positive):

  - *discontinuity* — a 2 kHz high-passed first derivative: the ±5 ms window's crest factor must be
    ≥ 16 dB **and** its peak ≥ 3 dB above the 99.5th percentile of *both* sides of the seam (40 ms
    each). Comparing against each side separately is what separates a click from an abrupt onset (a
    fricative starting at the cut raises the right side's level, a click does not). This catches ~77 %
    of hard (unfaded) mid-speech splices whose step exceeds −10 dB of the local level;
  - *impulse* — the residual of order-32 linear prediction fitted separately on each side (forward
    with the left model, backward with the right, the smaller residual kept): speech is predictable,
    an impulse is not. Crest ≥ 24 dB and peak ≥ 12 dB over both sides' 99.5th percentile: on real
    speech at −20 dBFS RMS this catches ~94 % of single-sample spikes at −40 dBFS (20 dB below the
    speech RMS; the derivative rule alone catches almost none of those) and ~99 % at −30 dBFS.

  A click must also be audible: the window's high-passed peak must exceed −60 dBFS. Every seam gets a
  continuous ``margin_db`` (positive = click) so critics can rank near-misses.
* **Digital silence** — runs ≥ 10 ms where every sample of every channel is below −90 dBFS (real room
  tone never stays under that for 480 consecutive samples, digital zero always does), classified as
  *under speech* (touching a kept word's output span ±20 ms) or *in programme* (between the first and
  last spoken word). The AAC priming region before the first word is reported but is neither. The
  deliverable's own PCM mix (``mix.wav`` / ``mix_nomusic.wav``, sample-exact) is scanned too: AAC's
  2048-sample MDCT smears a dropout shorter than ~40 ms above the floor, but the hole is still audible,
  so a run found only in the mix is reported with ``source="mix"``.
* **Black frames / freezes** — FFmpeg ``blackdetect`` (≥ 0.1 s, pixel ≤ 10 %, 98 % of the picture) and
  ``freezedetect`` (−60 dB, ≥ 0.5 s) in one decode; freezes under designed cards and still-image inserts
  are marked *expected*, and a freeze that is really a black stretch is reported once, as black.
* **A/V** — video frame count, audio sample count, stream start offset and durations vs the timeline;
  ``mix.wav`` must be sample-exact; and a **measured sync probe per segment**: the band-passed source
  dialogue (``media/audio.wav``) under each segment's kept words is cross-correlated against the final's
  audio at the output position the timeline predicts (normalized, sub-sample peak). A lag is a real
  audio-vs-picture offset, whatever caused it (priming, a stretch, a misplaced piece).
* **Text placement** (invariant 7) — against the deliverable platform's safe zone and the eyes-to-mouth
  box of the face track *after framing transforms* (the compiler's shared ``map_source_point``, the
  same geometry the A-roll renderer uses). Two sources: the *rendered* overlay layer (``overlays.mov``
  alpha plane, sampled every ~0.1 s — the pixels the viewer sees; authoritative) and the *planned* boxes
  estimated from the timeline (always available; attribution and advice). Captions may use the relaxed
  organic caption floor; every other text must stay in the strict band.
* **Edit structure** — every seam classified (pause trim / filler / retake / reorder / content), cuts
  inside words (the *audio* edit within a kept word's heard span — a J/L cut's picture seam lands inside
  a word on purpose) and inside clauses (left word does not end a clause and the right word does not
  start a sentence), word integrity (a kept word not fully inside its audio window is *clipped*; a
  removed word audible inside a window is *leaked*), the face across each picture seam judged against
  the take-matching priors (size within 5 %, eye line within 2 % of the frame, or a ≥ ×1.25 change;
  a same-size jump is fine between beats, worth hiding inside a thought), word level jump across each
  seam, seams per minute, pause median (output vs the creator's own), time to first speech, final word
  to end, WPM, fillers, longest static stretch, caption reading speed.
* **ASR round-trip** (real key needed) — the final's audio is transcribed with Scribe v2 (AssemblyAI
  fallback) through :func:`studio.perception.transcribe.transcribe_file`; WER vs the expected kept words
  (jiwer), plus a similarity-weighted alignment that localizes every deleted/substituted word to its
  word ID and flags those adjacent to a seam (*seam damage*: the clipped-phoneme signature). Music hits
  and SFX sit on seams by design and can mask a word for the ASR, so when the mix carries them the
  render's dialogue stem is transcribed too and only a word lost in both counts; one heard in the
  dialogue alone is reported as *masked* (a balance note, not a clipped phoneme).
* **Intelligibility** — ESTOI (pystoi) of the dialogue stem vs the final mix, full band and through a
  phone-speaker high-pass (music masking), when ``stems/dialogue.wav`` exists.
* **Banding** — CAMBI (libvmaf) on frames sampled twice a second.

The packet is saved as ``<render_dir>/qa/metrics_<file stem>.json``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import itertools
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import signal as sps

from studio.timebase import sample_index, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline, TimelineSegment
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import AsrResult, FaceBox, TakeIndex

__all__ = [
    "PACKET_VERSION", "SR", "ClickParams", "Loudness", "SeamClick", "EdgeLevels", "SilenceRun", "VideoEvent",
    "SyncProbe", "AvCheck", "TextBox", "CutCheck", "Integrity", "Pacing", "AsrWordDiff", "AsrRoundTrip",
    "Intelligibility", "Banding", "MetricsPacket",
    "measure", "metrics_packet", "platform_of", "load_priors", "prior",
    "decode_final_audio", "measure_loudness", "ffmpeg_ebur128", "click_features", "detect_seam_clicks",
    "edge_levels", "digital_silence_runs", "classify_silence", "mix_only_silence", "detect_black_freeze",
    "probe_streams",
    "check_av", "sync_probes", "planned_text_boxes", "rendered_text_boxes", "face_protected_rect",
    "cut_checks", "word_integrity", "pacing_metrics", "align_tokens", "asr_round_trip", "intelligibility",
    "cambi", "spoken_word_spans", "seam_pairs", "normalize_asr_token",
]

PACKET_VERSION = 1
SR = 48_000
_EPS = 1e-12
_SPOKEN = ("word", "filler", "cutoff")
_CLAUSE_END = re.compile(r"[,;:.!?…—–]+[\"'”’)\]]*$|-$")
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*$")
_STATIC_ASSET_KINDS = ("image", "screenshot", "generated_image")


# ============================================================================================ models
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Loudness(_M):
    integrated_lufs: float | None = None
    integrated_ffmpeg_lufs: float | None = None
    true_peak_dbtp: float | None = None  # gating value: max of the two meters
    true_peak_4x_dbtp: float | None = None
    true_peak_ffmpeg_dbtp: float | None = None
    sample_peak_dbfs: float | None = None
    lra_lu: float | None = None
    short_term_max_lufs: float | None = None
    short_term_over_integrated_lu: float | None = None
    momentary_max_lufs: float | None = None
    mono_fold_delta_lu: float | None = None
    channels: int = 0
    target_lufs: float = -14.0
    ceiling_dbtp: float = -1.0
    tolerance_lu: float = 1.0
    within_target: bool | None = None
    true_peak_ok: bool | None = None


class SeamClick(_M):
    """Click analysis at one audio seam (``margin_db`` > 0 ⇒ click)."""

    seam: int
    out_t: float
    sample: int
    left_word: str | None = None
    right_word: str | None = None
    hf_spike_db: float | None = None
    hf_crest_db: float | None = None
    lpc_spike_db: float | None = None
    lpc_crest_db: float | None = None
    level_dbfs: float | None = None
    margin_db: float | None = None
    source_margin_db: float | None = None  # the same analysis on the source at the join points
    click: bool = False
    rule: str = ""  # discontinuity | impulse | inherited (a source transient, not the edit)
    channel: int = 0


class EdgeLevels(_M):
    """First/last sample levels (a loop replays last → first: a large step clicks)."""

    first_sample_dbfs: float | None = None
    last_sample_dbfs: float | None = None
    loop_step_dbfs: float | None = None


class SilenceRun(_M):
    start_s: float
    end_s: float
    duration_ms: float
    under_speech: bool = False
    in_program: bool = False
    word_ids: list[str] = Field(default_factory=list)
    source: Literal["final", "mix"] = "final"  # mix: found only in the PCM mix (too short to survive AAC)


class VideoEvent(_M):
    kind: Literal["black", "freeze"]
    start_s: float
    end_s: float
    duration_s: float
    expected: bool = False
    reason: str = ""
    refs: list[str] = Field(default_factory=list)


class SyncProbe(_M):
    seg_id: str
    out_t: float
    lag_ms: float | None = None  # > 0: the audio is late vs the picture/timeline
    corr: float | None = None
    reliable: bool = False


class AvCheck(_M):
    fps: str | None = None
    cfr: bool | None = None
    timeline_frames: int = 0
    timeline_duration_s: float = 0.0
    timeline_samples: int = 0
    video_frames: int | None = None
    video_duration_s: float | None = None
    video_start_s: float | None = None
    audio_samples: int | None = None
    audio_duration_s: float | None = None
    audio_start_s: float | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    frame_diff: int | None = None
    sample_diff: int | None = None
    start_offset_ms: float | None = None
    duration_diff_ms: float | None = None
    codec_tail_allowance: int = 0  # samples: an AAC stream ends on a whole codec frame (≤ 1 frame of padding)
    tail_peak_dbfs: float | None = None  # level of the decoded samples past the timeline's end (padding)
    mix_samples: int | None = None
    mix_exact: bool | None = None
    sync: list[SyncProbe] = Field(default_factory=list)
    sync_max_abs_ms: float | None = None
    tolerance_ms: float = 0.0
    ok: bool | None = None
    problems: list[str] = Field(default_factory=list)


class TextBox(_M):
    kind: Literal["caption", "text"]
    id: str
    source: Literal["planned", "rendered"]
    out_start: float
    out_end: float
    box: list[float] = Field(default_factory=list)  # x0, y0, x1, y1 in output px
    issues: list[str] = Field(default_factory=list)  # outside_safe_zone | covers_face | too_wide
    face_overlap_px: float = 0.0
    outside_px: float = 0.0
    at_s: float | None = None  # rendered: time of the worst frame
    platform: str = ""
    refs: list[str] = Field(default_factory=list)


class CutCheck(_M):
    seam: int
    out_t: float  # picture seam
    audio_out_t: float  # audio seam (J/L-shifted)
    kind: Literal["pause_trim", "filler", "retake", "reorder", "content"]
    left_word: str | None = None
    right_word: str | None = None
    left_text: str = ""
    right_text: str = ""
    removed_word_ids: list[str] = Field(default_factory=list)
    removed_s: float = 0.0
    inside_word: bool = False  # the audio seam falls inside a kept word's heard span
    inside_clause: bool = False
    beat_boundary: bool = False  # the right word starts a sentence or the left one ends one
    face_shift_px: float | None = None
    face_scale_ratio: float | None = None
    level_jump_db: float | None = None


class Integrity(_M):
    clipped: list[dict[str, Any]] = Field(default_factory=list)  # kept word not fully in its audio window
    leaked: list[dict[str, Any]] = Field(default_factory=list)  # removed word audible inside a window
    #: kept words the recording itself cuts off (a digital-silence dropout at their start/end): a chopped phoneme
    truncated: list[dict[str, Any]] = Field(default_factory=list)
    #: non-word sound (a fragment of a lost word, a noise; ``Gap.sound_us``) inside a kept audio window: in a cut pad
    #: the audio stage mutes it under room tone (``muted``); between kept words it plays (reported, not a gate)
    sound_leaks: list[dict[str, Any]] = Field(default_factory=list)
    #: kept cut-off words (``kind == "cutoff"``: a fragment such as "restr-") at a join, i.e. the edge of a continuous
    #: audio run (a true seam or the story's first/last word): a broken word, then a jump (invariant 1). Words the
    #: recording chops are listed under ``truncated`` instead.
    cutoff_at_join: list[dict[str, Any]] = Field(default_factory=list)


class Pacing(_M):
    duration_s: float = 0.0
    seams: int = 0
    audio_seams: int = 0
    content_seams: int = 0
    seams_per_min: float = 0.0
    content_seams_per_min: float = 0.0
    pause_median_ms: float | None = None
    source_pause_median_ms: float | None = None
    pause_ratio: float | None = None
    long_pauses: list[dict[str, Any]] = Field(default_factory=list)
    time_to_first_speech_s: float | None = None
    final_word_to_end_s: float | None = None
    payoff_position_frac: float | None = None
    words_kept: int = 0
    words_removed: int = 0
    removed_source_s: float = 0.0
    wpm: float | None = None
    fillers_kept: int = 0
    fillers_per_min: float = 0.0
    longest_static_s: float = 0.0
    static_stretches: list[dict[str, float]] = Field(default_factory=list)
    caption_cps_max: float | None = None
    caption_cps_median: float | None = None
    cuts_inside_clauses: int = 0
    cuts_inside_words: int = 0


class AsrWordDiff(_M):
    word_id: str
    text: str
    op: Literal["deleted", "substituted"]
    heard: str = ""
    near_seam: bool = False
    confidence: float | None = None


class AsrRoundTrip(_M):
    ran: bool = False
    skipped_reason: str = ""
    provider: str | None = None
    model: str | None = None
    wer: float | None = None
    expected_words: int = 0
    hypothesis_words: int = 0
    hits: int = 0
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    diffs: list[AsrWordDiff] = Field(default_factory=list)
    inserted: list[str] = Field(default_factory=list)
    seam_damage: list[str] = Field(default_factory=list)  # lost in the final and (when checked) in the dialogue
    masked: list[str] = Field(default_factory=list)  # lost in the final, heard in the dialogue stem alone
    confirm_ran: bool = False  # the dialogue stem was transcribed to confirm seam-damage candidates
    confirm_wer: float | None = None
    pinned_missing: list[str] = Field(default_factory=list)
    timing_offset_ms_median: float | None = None
    timing_offset_ms_p90_abs: float | None = None
    expected_text: str = ""
    hypothesis_text: str = ""


class Intelligibility(_M):
    estoi: float | None = None
    estoi_phone: float | None = None
    reference: str = ""


class Banding(_M):
    cambi_mean: float | None = None
    cambi_max: float | None = None
    cambi_p95: float | None = None
    frames: int = 0


class MetricsPacket(_M):
    version: int = PACKET_VERSION
    created_at: str = ""
    job_id: str = ""
    doc_version: int | None = None
    render_dir: str | None = None
    final_path: str = ""
    platform: str = "tiktok"
    light: bool = False
    duration_s: float = 0.0
    loudness: Loudness | None = None
    clicks: list[SeamClick] = Field(default_factory=list)
    #: the click detector at every recording-dropout edge (sound ↔ digital zero) inside kept audio
    edge_clicks: list[SeamClick] = Field(default_factory=list)
    edges: EdgeLevels | None = None
    digital_silence: list[SilenceRun] = Field(default_factory=list)
    video_events: list[VideoEvent] = Field(default_factory=list)
    av: AvCheck | None = None
    text: list[TextBox] = Field(default_factory=list)
    text_source: Literal["rendered", "planned", "none"] = "none"
    text_planned: list[TextBox] = Field(default_factory=list)
    cuts: list[CutCheck] = Field(default_factory=list)
    integrity: Integrity = Field(default_factory=Integrity)
    pacing: Pacing | None = None
    asr: AsrRoundTrip | None = None
    intelligibility: Intelligibility | None = None
    banding: Banding | None = None
    advice: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ views
    @property
    def clicks_found(self) -> list[SeamClick]:
        return [c for c in self.clicks if c.click]

    @property
    def edge_clicks_found(self) -> list[SeamClick]:
        return [c for c in self.edge_clicks if c.click]

    @property
    def silence_under_speech(self) -> list[SilenceRun]:
        return [s for s in self.digital_silence if s.under_speech or s.in_program]

    @property
    def text_issues(self) -> list[TextBox]:
        return [t for t in self.text if t.issues]

    @property
    def unexpected_video_events(self) -> list[VideoEvent]:
        return [e for e in self.video_events if not e.expected]

    def summary(self) -> dict[str, Any]:
        """Compact JSON-safe view for critics (numbers + advice, no per-page lists)."""
        L = self.loudness
        p = self.pacing
        a = self.asr
        return {
            "file": Path(self.final_path).name, "platform": self.platform, "duration_s": self.duration_s,
            "loudness": None if L is None else {"integrated_lufs": L.integrated_lufs,
                                                "true_peak_dbtp": L.true_peak_dbtp,
                                                "lra_lu": L.lra_lu, "short_term_over_integrated_lu":
                                                    L.short_term_over_integrated_lu},
            "clicks": [{"seam": c.seam, "t": c.out_t, "words": [c.left_word, c.right_word], "margin_db": c.margin_db}
                       for c in self.clicks_found],
            "worst_click_margin_db": max((c.margin_db for c in self.clicks if c.margin_db is not None), default=None),
            "digital_silence_under_speech": [s.model_dump() for s in self.silence_under_speech],
            "video_events": [e.model_dump() for e in self.unexpected_video_events],
            "av": None if self.av is None else {"ok": self.av.ok, "sync_max_abs_ms": self.av.sync_max_abs_ms,
                                                "frame_diff": self.av.frame_diff, "sample_diff": self.av.sample_diff,
                                                "problems": self.av.problems},
            "text_issues": [{"id": t.id, "kind": t.kind, "issues": t.issues, "at_s": t.at_s} for t in self.text_issues],
            "cuts_inside_words": [c.seam for c in self.cuts if c.inside_word],
            "cuts_inside_clauses": [{"seam": c.seam, "kind": c.kind, "words": [c.left_word, c.right_word]}
                                    for c in self.cuts if c.inside_clause and c.kind != "pause_trim"],
            "integrity": {"clipped": self.integrity.clipped, "leaked": self.integrity.leaked,
                          "truncated_by_recording": self.integrity.truncated,
                          "cutoff_at_join": self.integrity.cutoff_at_join,
                          "untranscribed_sound_kept": self.integrity.sound_leaks},
            "dropout_edge_clicks": [{"t": c.out_t, "words": [c.left_word, c.right_word], "margin_db": c.margin_db}
                                    for c in self.edge_clicks_found],
            "pacing": None if p is None else p.model_dump(exclude={"long_pauses", "static_stretches"}),
            "asr": None if a is None else {"ran": a.ran, "wer": a.wer, "seam_damage": a.seam_damage, "masked": a.masked,
                                           "missing": [d.word_id for d in a.diffs if d.op == "deleted"],
                                           "skipped_reason": a.skipped_reason},
            "intelligibility": None if self.intelligibility is None else self.intelligibility.model_dump(),
            "banding": None if self.banding is None else self.banding.model_dump(),
            "advice": self.advice, "errors": self.errors,
        }

    def save(self, path: str | os.PathLike[str]) -> Path:
        from studio.jobs import write_json_atomic

        return write_json_atomic(Path(path), self)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> MetricsPacket:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# ============================================================================================ small helpers
def _db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), _EPS))


def _r(x: float | None, nd: int = 2) -> float | None:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf):
        return None
    if math.isinf(xf):
        return -999.0 if xf < 0 else 999.0
    return round(xf, nd)


def _f(t: Any) -> float:
    return float(to_fraction(t))


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ffmpeg() -> str:
    from studio.config import get_settings

    return get_settings().ffmpeg


def _ffprobe() -> str:
    from studio.config import get_settings

    return get_settings().ffprobe


def platform_of(final_path: str | os.PathLike[str], doc: CutDocument | None = None, default: str = "tiktok") -> str:
    """Platform named by ``final_<platform>.mp4``; else the document's first deliverable; else ``default``."""
    m = re.match(r"^final_(tiktok|reels|shorts)$", Path(final_path).stem)
    if m:
        return m.group(1)
    if doc is not None and doc.deliverables:
        return doc.deliverables[0].platform
    return default


# ---------------------------------------------------------------------------------------------- priors
def load_priors(settings: Settings | None = None) -> dict[str, Any]:
    """``skills/editing/constants.yaml`` ({} when absent)."""
    try:
        from studio.compile.captions import load_constants

        return load_constants(settings) or {}
    except Exception:  # pragma: no cover - doctrine file mid-edit: fall back to built-in defaults
        return {}


def prior(priors: Mapping[str, Any], dotted: str, default: Any) -> Any:
    """``prior(p, "hook.first_word_target_s", [0.1, 0.5])`` — the doctrine value or ``default``."""
    cur: Any = priors
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def _range(v: Any, default: tuple[float, float]) -> tuple[float, float]:
    try:
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return float(v[0]), float(v[1])
    except (TypeError, ValueError):
        pass
    return default


# ============================================================================================ audio decode
def decode_final_audio(path: str | os.PathLike[str], sr: int = SR) -> np.ndarray:
    """Decode the first audio stream to float64 ``(channels, n)`` at ``sr`` (no downmix).

    MP4s written by :mod:`studio.compile.master` carry no edit list and have the AAC priming pre-trimmed,
    so decoded sample ``k`` is output time ``k / sr``.
    """
    p = Path(path)
    ch = 2
    info = probe_streams(p)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if a is None:
        raise ValueError(f"{p.name} has no audio stream")
    with contextlib.suppress(TypeError, ValueError):
        ch = max(1, min(2, int(a.get("channels") or 2)))
    cmd = [_ffmpeg(), "-nostdin", "-v", "error", "-i", str(p), "-map", "0:a:0", "-vn", "-ac", str(ch), "-ar", str(sr),
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode audio of {p.name}: {r.stderr.decode(errors='replace')[-300:]}")
    raw = np.frombuffer(r.stdout, dtype="<f4").astype(np.float64)
    n = raw.size // ch
    return np.ascontiguousarray(raw[: n * ch].reshape(n, ch).T)


def _mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x if x.ndim == 1 else x.mean(axis=0)


def _read_wav_mono(path: Path, sr: int = SR) -> np.ndarray:
    import soundfile as sf

    x, fsr = sf.read(str(path), dtype="float64", always_2d=True)
    m = x.mean(axis=1)
    if fsr != sr:
        import soxr

        m = soxr.resample(m, fsr, sr, quality="VHQ")
    return m


# ============================================================================================ loudness
def ffmpeg_ebur128(path: str | os.PathLike[str]) -> dict[str, float | None]:
    """Independent EBU R128 meter (FFmpeg ``ebur128=peak=true``): ``{I, LRA, TP}`` from the summary."""
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-vn",
           "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    txt = r.stderr or ""
    tail = txt[txt.rfind("Summary:"):] if "Summary:" in txt else txt

    def grab(pattern: str) -> float | None:
        m = re.search(pattern, tail)
        if not m:
            return None
        with contextlib.suppress(ValueError):
            return float(m.group(1))
        return None

    return {"I": grab(r"I:\s+(-?[\d.]+|-inf)\s+LUFS"), "LRA": grab(r"LRA:\s+(-?[\d.]+)\s+LU\b"),
            "TP": grab(r"True peak:\s*\n\s*Peak:\s+(-?[\d.]+|-inf)\s+dBFS")}


def measure_loudness(x: np.ndarray, sr: int = SR, *, path: str | os.PathLike[str] | None = None,
                     target_lufs: float = -14.0, ceiling_dbtp: float = -1.0, tolerance_lu: float = 1.0) -> Loudness:
    """Loudness of decoded audio ``(ch, n)``; ``path`` adds the FFmpeg cross-check."""
    from studio.compile import audio as caudio

    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    out = Loudness(channels=int(x2.shape[0]), target_lufs=target_lufs, ceiling_dbtp=ceiling_dbtp,
                   tolerance_lu=tolerance_lu)
    if x2.size == 0:
        return out
    integ = caudio.integrated_loudness(x2, sr)
    out.integrated_lufs = _r(integ)
    out.true_peak_4x_dbtp = _r(caudio.true_peak_dbtp(x2, sr, oversample=4))
    out.sample_peak_dbfs = _r(_db(float(np.max(np.abs(x2)))))
    _, st = caudio.loudness_curve(x2, sr, window_s=3.0, hop_s=0.1)
    _, mo = caudio.loudness_curve(x2, sr, window_s=0.4, hop_s=0.1)
    if st.size:
        out.short_term_max_lufs = _r(float(np.max(st)))
        if math.isfinite(integ):
            out.short_term_over_integrated_lu = _r(float(np.max(st)) - integ)
    if mo.size:
        out.momentary_max_lufs = _r(float(np.max(mo)))
    if x2.shape[0] == 2 and math.isfinite(integ):
        fold = caudio.integrated_loudness(np.vstack([x2.mean(axis=0)] * 2), sr)
        if math.isfinite(fold):
            out.mono_fold_delta_lu = _r(fold - integ)
    tps = [v for v in (out.true_peak_4x_dbtp,) if v is not None]
    if path is not None:
        try:
            fm = ffmpeg_ebur128(path)
            out.integrated_ffmpeg_lufs = _r(fm["I"])
            out.true_peak_ffmpeg_dbtp = _r(fm["TP"])
            out.lra_lu = _r(fm["LRA"])
            if fm["TP"] is not None:
                tps.append(float(fm["TP"]))
        except (OSError, subprocess.SubprocessError):
            pass
    if tps:
        out.true_peak_dbtp = _r(max(tps))
    if out.integrated_lufs is not None and math.isfinite(integ):
        out.within_target = abs(integ - target_lufs) <= tolerance_lu + 1e-9
    if out.true_peak_dbtp is not None:
        out.true_peak_ok = out.true_peak_dbtp <= ceiling_dbtp + 1e-9
    return out


# ============================================================================================ clicks
@dataclass(frozen=True)
class ClickParams:
    """Seam click detector settings (calibrated on the QA takes; see module docstring)."""

    win_ms: float = 5.0  # ± window around the seam
    ctx_ms: float = 40.0  # context on each side
    guard_ms: float = 2.0  # gap between window and context
    hp_hz: float = 2000.0
    hf_crest_db: float = 16.0
    hf_spike_db: float = 3.0
    lpc_order: int = 32
    lpc_crest_db: float = 24.0
    lpc_spike_db: float = 12.0
    min_level_dbfs: float = -60.0
    inherited_margin_db: float = 3.0  # an output transient must beat the source's own by this much


_SOS_CACHE: dict[tuple[int, float, str], np.ndarray] = {}


def _sos(sr: int, hz: float, kind: str = "highpass", order: int = 4) -> np.ndarray:
    key = (sr, hz, f"{kind}{order}")
    s = _SOS_CACHE.get(key)
    if s is None:
        s = sps.butter(order, hz, kind, fs=sr, output="sos")
        _SOS_CACHE[key] = s
    return s


def _lpc(x: np.ndarray, order: int) -> np.ndarray | None:
    """Autocorrelation-method LPC (Hann window, Levinson via Toeplitz solve, 1e-9 white-noise
    correction): ``[1, a1, …, ap]`` or None for silent/degenerate input."""
    from scipy.linalg import solve_toeplitz

    xw = x * np.hanning(x.size)
    r = np.correlate(xw, xw, mode="full")[x.size - 1: x.size + order]
    if r[0] <= 1e-18:
        return None
    r = r.copy()
    r[0] *= 1.0 + 1e-9
    try:
        a = solve_toeplitz((r[:order], r[:order]), -r[1: order + 1])
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not np.all(np.isfinite(a)):
        return None
    return np.concatenate(([1.0], a))


def _lpc_residual(seg: np.ndarray, left: np.ndarray, right: np.ndarray, order: int) -> np.ndarray | None:
    """min(|forward residual (left model)|, |backward residual (right model, time-reversed)|) over ``seg``."""
    if left.size <= 4 * order or right.size <= 4 * order:
        return None
    if float(np.max(np.abs(left))) < 1e-7 or float(np.max(np.abs(right))) < 1e-7:
        return None
    aL = _lpc(left, order)
    aR = _lpc(right[::-1], order)
    if aL is None or aR is None:
        return None
    eL = sps.lfilter(aL, [1.0], seg)
    eR = sps.lfilter(aR, [1.0], seg[::-1])[::-1]
    return np.minimum(np.abs(eL), np.abs(eR))


def click_features(x: np.ndarray, sr: int, n: int, params: ClickParams | None = None) -> dict[str, Any] | None:
    """Click features at sample ``n`` of mono ``x`` (None when there is not enough context)."""
    p = params or ClickParams()
    x = np.asarray(x, dtype=np.float64)
    w = max(1, round(p.win_ms * sr / 1000))
    c = round(p.ctx_ms * sr / 1000)
    g = round(p.guard_ms * sr / 1000)
    pad = round(0.01 * sr)
    a, b = n - c - w - g - pad, n + c + w + g + pad
    if a < 0 or b > x.size:
        # near the file edges: shrink the context symmetrically
        room = min(n, x.size - n) - w - g - pad
        if room < round(0.01 * sr):
            return None
        c = room
        a, b = n - c - w - g - pad, n + c + w + g + pad
    seg = x[a:b]
    m = n - a
    hp = sps.sosfiltfilt(_sos(sr, p.hp_hz), seg)
    d = np.abs(np.diff(hp, prepend=hp[0]))
    inner = d[m - w: m + w]
    L = d[pad: m - w - g]
    R = d[m + w + g: d.size - pad]
    if inner.size == 0 or L.size < 16 or R.size < 16:
        return None
    pk = float(inner.max())
    ref = max(float(np.percentile(L, 99.5)), float(np.percentile(R, 99.5)))
    rin = math.sqrt(float(np.mean(inner * inner)))
    level = _db(float(np.max(np.abs(hp[m - w: m + w]))))
    feats: dict[str, Any] = {
        "hf_spike_db": _db(pk) - _db(ref), "hf_crest_db": _db(pk) - _db(rin), "level_dbfs": level,
        "lpc_spike_db": None, "lpc_crest_db": None,
    }
    res = _lpc_residual(seg, seg[: m - w - g], seg[m + w + g:], p.lpc_order)
    if res is not None:
        o = p.lpc_order
        e_in = res[m - w: m + w]
        e_L = res[pad + o: m - w - g]
        e_R = res[m + w + g: res.size - pad - o]
        if e_L.size >= 16 and e_R.size >= 16:
            pk2 = float(e_in.max())
            ref2 = max(float(np.percentile(e_L, 99.5)), float(np.percentile(e_R, 99.5)))
            feats["lpc_spike_db"] = _db(pk2) - _db(ref2)
            feats["lpc_crest_db"] = _db(pk2) - _db(math.sqrt(float(np.mean(e_in * e_in))))
    m1 = min(feats["hf_crest_db"] - p.hf_crest_db, feats["hf_spike_db"] - p.hf_spike_db)
    m2 = -math.inf
    if feats["lpc_spike_db"] is not None:
        m2 = min(feats["lpc_crest_db"] - p.lpc_crest_db, feats["lpc_spike_db"] - p.lpc_spike_db)
    margin = max(m1, m2)
    audible = level >= p.min_level_dbfs
    feats["margin_db"] = margin
    feats["click"] = bool(margin >= 0 and audible)
    feats["rule"] = ("discontinuity" if m1 >= m2 else "impulse") if feats["click"] else ""
    return feats


def detect_seam_clicks(x: np.ndarray, sr: int, times: Sequence[Any], *, params: ClickParams | None = None,
                       labels: Sequence[tuple[str | None, str | None]] | None = None,
                       source: np.ndarray | None = None,
                       source_samples: Sequence[tuple[int | None, int | None]] | None = None) -> list[SeamClick]:
    """Run :func:`click_features` at each seam time (seconds), per channel; the worst channel is kept.

    With ``source`` (the mono source dialogue) and ``source_samples`` (per seam: the source sample where
    the outgoing piece ends and where the incoming piece starts), a transient the source already has at
    that point (a plosive release landing on the seam) is *inherited*, not introduced by the edit: the
    output must beat the source's own margin by ``inherited_margin_db`` to count as a click."""
    p = params or ClickParams()
    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    src = None if source is None else _mono(source)
    out: list[SeamClick] = []
    for i, t in enumerate(times):
        n = sample_index(to_fraction(t), sr)
        best: tuple[float, int, dict[str, Any]] | None = None
        for ch in range(x2.shape[0]):
            f = click_features(x2[ch], sr, n, p)
            if f is None:
                continue
            key = f["margin_db"] + (1000.0 if f["click"] else 0.0)
            if best is None or key > best[0]:
                best = (key, ch, f)
        lw, rw = (labels[i] if labels is not None and i < len(labels) else (None, None))
        if best is None:
            out.append(SeamClick(seam=i, out_t=round(_f(t), 4), sample=n, left_word=lw, right_word=rw))
            continue
        _, ch, f = best
        inherited = None
        if src is not None and source_samples is not None and i < len(source_samples):
            ms = []
            for sn in source_samples[i]:
                if sn is None:
                    continue
                g = click_features(src, sr, int(sn), p)
                if g is not None:
                    ms.append(g["margin_db"])
            if ms:
                inherited = max(ms)
        click = bool(f["click"])
        rule = f["rule"]
        if click and inherited is not None and f["margin_db"] < inherited + p.inherited_margin_db:
            click, rule = False, "inherited"
        out.append(SeamClick(
            seam=i, out_t=round(_f(t), 4), sample=n, left_word=lw, right_word=rw,
            hf_spike_db=_r(f["hf_spike_db"]), hf_crest_db=_r(f["hf_crest_db"]), lpc_spike_db=_r(f["lpc_spike_db"]),
            lpc_crest_db=_r(f["lpc_crest_db"]), level_dbfs=_r(f["level_dbfs"]), margin_db=_r(f["margin_db"]),
            source_margin_db=_r(inherited), click=click, rule=rule, channel=ch))
    return out


def edge_levels(x: np.ndarray) -> EdgeLevels:
    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    if x2.shape[1] == 0:
        return EdgeLevels()
    first = float(np.max(np.abs(x2[:, 0])))
    last = float(np.max(np.abs(x2[:, -1])))
    step = float(np.max(np.abs(x2[:, -1] - x2[:, 0])))
    return EdgeLevels(first_sample_dbfs=_r(_db(first)), last_sample_dbfs=_r(_db(last)), loop_step_dbfs=_r(_db(step)))


# ============================================================================================ digital silence
def digital_silence_runs(x: np.ndarray, sr: int = SR, *, floor_dbfs: float = -90.0,
                         min_ms: float = 10.0) -> list[tuple[int, int]]:
    """``[(start, end)]`` sample runs ≥ ``min_ms`` where every channel stays below ``floor_dbfs``."""
    x2 = np.atleast_2d(np.asarray(x))  # float32 PCM is compared as is (no float64 copy of a long mix)
    if x2.shape[1] == 0:
        return []
    a = np.max(np.abs(x2), axis=0)
    quiet = (a <= 10 ** (floor_dbfs / 20.0)).astype(np.int8)
    d = np.diff(np.concatenate(([0], quiet, [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    min_len = max(1, round(min_ms * sr / 1000))
    return [(int(s), int(e)) for s, e in zip(starts, ends, strict=True) if e - s >= min_len]


def spoken_word_spans(timeline: Timeline, index: TakeIndex | None) -> list[tuple[str, float, float]]:
    """Kept spoken words ``(word_id, out_start_s, out_end_s)`` in output order (events excluded)."""
    out: list[tuple[str, float, float]] = []
    for wid, span in timeline.word_map.items():
        if span is None:
            continue
        if index is not None and index.has_word(wid) and index.word(wid).kind not in _SPOKEN:
            continue
        out.append((wid, _f(span.out_start), _f(span.out_end)))
    out.sort(key=lambda r: (r[1], r[2]))
    return out


def classify_silence(runs: Sequence[tuple[int, int]], sr: int, words: Sequence[tuple[str, float, float]], *,
                     margin_s: float = 0.02, source: Literal["final", "mix"] = "final") -> list[SilenceRun]:
    """Mark runs that touch a kept word (± ``margin_s``) or lie between the first and last word."""
    first = words[0][1] if words else None
    last = max((w[2] for w in words), default=None)
    out: list[SilenceRun] = []
    for s, e in runs:
        t0, t1 = s / sr, e / sr
        hit = [w[0] for w in words if w[1] - margin_s < t1 and t0 < w[2] + margin_s]
        in_prog = first is not None and last is not None and t1 > first and t0 < last
        out.append(SilenceRun(start_s=round(t0, 4), end_s=round(t1, 4), duration_ms=round((e - s) * 1000 / sr, 2),
                              under_speech=bool(hit), in_program=bool(in_prog), word_ids=hit, source=source))
    return out


def mix_only_silence(final_runs: Sequence[tuple[int, int]], mix: np.ndarray, sr: int = SR, *,
                     floor_dbfs: float = -90.0, min_ms: float = 10.0) -> list[tuple[int, int]]:
    """Digital-silence runs of the PCM ``mix`` that no run of the encoded file overlaps (the dropouts
    too short to survive AAC)."""
    out = []
    for s, e in digital_silence_runs(mix, sr, floor_dbfs=floor_dbfs, min_ms=min_ms):
        if not any(a < e and s < b for a, b in final_runs):
            out.append((s, e))
    return out


# ============================================================================================ video
def probe_streams(path: str | os.PathLike[str], *, count_packets: bool = False) -> dict[str, Any]:
    """ffprobe ``-show_streams -show_format`` (+ ``-count_packets``) as JSON."""
    cmd = [_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    if count_packets:
        cmd.insert(3, "-count_packets")
    cmd.append(str(path))
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {Path(path).name}: {r.stderr[-300:]}")
    return json.loads(r.stdout or "{}")


def detect_black_freeze(path: str | os.PathLike[str], *, black_min_s: float = 0.1, pix_th: float = 0.10,
                        pic_th: float = 0.98, freeze_noise_db: float = -60.0,
                        freeze_min_s: float = 0.5) -> list[VideoEvent]:
    """Black stretches and frozen pictures from one decode (FFmpeg ``blackdetect`` + ``freezedetect``)."""
    # a quarter-resolution area downscale first: black/frozen pictures stay black/frozen, the decode-bound
    # detectors run ~10x faster, and a talking head's motion stays far above the −60 dB freeze floor
    vf = ("scale=trunc(iw/8)*2:trunc(ih/8)*2:flags=area,"
          f"blackdetect=d={black_min_s}:pix_th={pix_th}:pic_th={pic_th},"
          f"freezedetect=n={freeze_noise_db}dB:d={freeze_min_s}")
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(path), "-map", "0:v:0",
           "-an", "-vf", vf, "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"blackdetect/freezedetect failed on {Path(path).name}: {r.stderr[-300:]}")
    txt = r.stderr or ""
    dur = None
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    events: list[VideoEvent] = []
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)", txt):
        s, e = float(m.group(1)), float(m.group(2))
        events.append(VideoEvent(kind="black", start_s=round(s, 3), end_s=round(e, 3), duration_s=round(e - s, 3)))
    starts = [float(v) for v in re.findall(r"freeze_start:\s*([\d.]+)", txt)]
    ends = [float(v) for v in re.findall(r"freeze_end:\s*([\d.]+)", txt)]
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else (dur if dur is not None else s)
        events.append(VideoEvent(kind="freeze", start_s=round(s, 3), end_s=round(e, 3), duration_s=round(e - s, 3)))
    events.sort(key=lambda ev: ev.start_s)
    return events


def _classify_video_events(events: list[VideoEvent], timeline: Timeline) -> list[VideoEvent]:
    from studio.doc.model import CardSpec

    out = []
    blacks = [e for e in events if e.kind == "black"]
    for ev in events:
        ev = ev.model_copy()
        if ev.kind == "freeze" and ev.duration_s > 0 and sum(
                max(0.0, min(ev.end_s, b.end_s) - max(ev.start_s, b.start_s)) for b in blacks) >= 0.8 * ev.duration_s:
            continue  # a black stretch is also a still picture: report it once, as black
        if ev.kind == "freeze":
            cover = 0.0
            refs: list[str] = []
            for ins in timeline.inserts:
                if ins.mode not in ("full", "card"):
                    continue
                static = isinstance(ins.asset, CardSpec) or getattr(ins.asset, "kind", "") in _STATIC_ASSET_KINDS
                if not static:
                    continue
                ov = min(ev.end_s, _f(ins.out_end)) - max(ev.start_s, _f(ins.out_start))
                if ov > 0:
                    cover += ov
                    refs.append(ins.insert_id)
            if ev.duration_s > 0 and cover >= 0.5 * ev.duration_s:
                ev.expected = True
                ev.reason = "static card/still insert"
                ev.refs = refs
            else:
                seg = timeline.segment_at(Fraction(ev.start_s + ev.duration_s / 2).limit_denominator(100000))
                ev.reason = "frozen picture" + (f" in {seg.seg_id}" if seg is not None else "")
                if seg is not None:
                    ev.refs = [seg.seg_id]
        else:
            ev.reason = "black frames"
            seg = timeline.segment_at(Fraction(ev.start_s + ev.duration_s / 2).limit_denominator(100000))
            if seg is not None:
                ev.refs = [seg.seg_id]
        out.append(ev)
    return out


# ============================================================================================ A/V
def _fps_of(stream: Mapping[str, Any], key: str) -> Fraction | None:
    v = stream.get(key)
    try:
        f = Fraction(str(v))
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _codec_frame_samples(a: Mapping[str, Any] | None) -> int:
    """Samples per codec frame for an audio stream whose decoded length is rounded up to whole frames
    (AAC-LC 1024, HE-AAC 2048; PCM and unknown codecs 0)."""
    if a is None or a.get("codec_name") != "aac":
        return 0
    return 2048 if "HE" in str(a.get("profile") or "") else 1024


def check_av(timeline: Timeline, probe: Mapping[str, Any], audio_samples: int | None, *,
             mix_path: Path | None = None, sync: Sequence[SyncProbe] = (),
             tail_peak_dbfs: float | None = None) -> AvCheck:
    """Durations, counts and start offsets of the encoded file vs the timeline (±1 frame).

    An MP4 without an edit list cannot signal end trimming, so the decoded AAC stream runs to the end of
    its last codec frame: up to one AAC frame (1024 samples, 21 ms) of encoder padding past the
    timeline is inherent and allowed on top of the ±1 video frame (at 60 fps one frame is only 800
    samples). A *short* audio stream gets no such allowance. The padding's level is reported
    (``tail_peak_dbfs``) — programme audio there would mean content runs past the picture."""
    fps = to_fraction(timeline.fps)
    frame_ms = 1000.0 / float(fps)
    av = AvCheck(timeline_frames=timeline.frame_count, timeline_duration_s=round(_f(timeline.duration), 6),
                 timeline_samples=timeline.sample_count, tolerance_ms=round(frame_ms, 3))
    streams = probe.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is not None:
        r, avg = _fps_of(v, "r_frame_rate"), _fps_of(v, "avg_frame_rate")
        av.fps = f"{r.numerator}/{r.denominator}" if r else None
        av.cfr = (r is not None and avg is not None and abs(float(r - avg)) <= 0.001 * float(r))
        nb = v.get("nb_read_packets") or v.get("nb_frames")
        with contextlib.suppress(TypeError, ValueError):
            av.video_frames = int(nb)
        av.video_duration_s = _r(_float(v.get("duration")), 6)
        av.video_start_s = _r(_float(v.get("start_time")), 6)
        if av.video_frames is not None:
            av.frame_diff = av.video_frames - timeline.frame_count
    else:
        av.problems.append("no video stream")
    if a is not None:
        av.audio_start_s = _r(_float(a.get("start_time")), 6)
        av.audio_duration_s = _r(_float(a.get("duration")), 6)
        with contextlib.suppress(TypeError, ValueError):
            av.audio_sample_rate = int(a.get("sample_rate"))
        with contextlib.suppress(TypeError, ValueError):
            av.audio_channels = int(a.get("channels"))
    else:
        av.problems.append("no audio stream")
    av.codec_tail_allowance = _codec_frame_samples(a)
    av.tail_peak_dbfs = _r(tail_peak_dbfs)
    if audio_samples is not None:
        av.audio_samples = int(audio_samples)
        av.sample_diff = int(audio_samples) - timeline.sample_count
    if av.video_start_s is not None and av.audio_start_s is not None:
        av.start_offset_ms = _r((av.audio_start_s - av.video_start_s) * 1000.0, 3)
    vd = (av.video_frames / float(fps)) if av.video_frames is not None else av.video_duration_s
    ad = (av.audio_samples / (av.audio_sample_rate or SR)) if av.audio_samples is not None else av.audio_duration_s
    if vd is not None and ad is not None:
        av.duration_diff_ms = _r((ad - vd) * 1000.0, 3)
    if mix_path is not None and mix_path.exists():
        try:
            import soundfile as sf

            info = sf.info(str(mix_path))
            av.mix_samples = int(info.frames)
            av.mix_exact = info.frames == timeline.sample_count and int(info.samplerate) == timeline.sample_rate
        except RuntimeError:
            av.problems.append(f"{mix_path.name} unreadable")
    av.sync = list(sync)
    rel = [s.lag_ms for s in sync if s.reliable and s.lag_ms is not None]
    av.sync_max_abs_ms = _r(max((abs(x) for x in rel), default=0.0), 3) if rel else None
    # ---- verdict (±1 frame)
    if av.frame_diff not in (None, 0):
        av.problems.append(f"{av.video_frames} video frames vs {timeline.frame_count} in the timeline")
    frame_samples = round(SR * frame_ms / 1000)
    tail = av.codec_tail_allowance
    if av.sample_diff is not None and not (-frame_samples <= av.sample_diff <= frame_samples + tail):
        av.problems.append(f"{av.audio_samples} audio samples vs {timeline.sample_count} "
                           f"({av.sample_diff * 1000 / SR:+.1f} ms)")
    if av.start_offset_ms is not None and abs(av.start_offset_ms) > frame_ms:
        av.problems.append(f"audio stream starts {av.start_offset_ms:+.1f} ms from the video stream")
    if av.duration_diff_ms is not None and not (-frame_ms <= av.duration_diff_ms <= frame_ms + tail * 1000 / SR):
        av.problems.append(f"audio/video durations differ by {av.duration_diff_ms:+.1f} ms")
    if av.mix_exact is False:
        av.problems.append(f"mix.wav has {av.mix_samples} samples, timeline {timeline.sample_count}")
    if av.sync_max_abs_ms is not None and av.sync_max_abs_ms > frame_ms:
        worst = max((s for s in sync if s.reliable and s.lag_ms is not None), key=lambda s: abs(s.lag_ms or 0))
        av.problems.append(f"measured audio offset {worst.lag_ms:+.1f} ms in {worst.seg_id}")
    av.ok = not av.problems
    return av


def _bandpass(x: np.ndarray, sr: int, lo: float = 150.0, hi: float = 4000.0) -> np.ndarray:
    return sps.sosfiltfilt(sps.butter(4, [lo, hi], "bandpass", fs=sr, output="sos"), x)


def sync_probes(timeline: Timeline, index: TakeIndex | None, source: np.ndarray, final: np.ndarray, sr: int = SR, *,
                max_lag_ms: float = 120.0, max_window_s: float = 2.0, min_window_s: float = 0.3,
                min_corr: float = 0.4) -> list[SyncProbe]:
    """Measured audio offset per segment: source dialogue under the kept words vs the final at the output
    position the timeline predicts (``lag_ms`` > 0 = the final's audio is late). Speed-changed segments
    are skipped (their waveform is re-synthesized)."""
    src = _mono(source)
    fin = _mono(final)
    M = round(max_lag_ms * sr / 1000)
    out: list[SyncProbe] = []
    for seg in timeline.segments:
        if abs(float(seg.speed) - 1.0) > 1e-6:
            continue
        lo_us, hi_us = seg.audio_src_in_us, seg.audio_src_out_us
        if index is not None and seg.word_ids:
            ws = [index.word(w) for w in seg.word_ids if index.has_word(w)]
            if ws:
                lo_us = max(lo_us, ws[0].start_us)
                hi_us = min(hi_us, ws[-1].end_us)
        lo_us += 30_000
        hi_us -= 30_000
        if hi_us - lo_us < min_window_s * 1e6:
            continue
        if hi_us - lo_us > max_window_s * 1e6:
            mid = (lo_us + hi_us) // 2
            lo_us, hi_us = mid - int(max_window_s * 5e5), mid + int(max_window_s * 5e5)
        a = round(lo_us * sr / 1e6)
        b = round(hi_us * sr / 1e6)
        out_t = to_fraction(seg.out_start) + Fraction(lo_us - seg.src_in_us, 1_000_000)
        o = sample_index(out_t, sr)
        if a < 0 or b > src.size or o - M < 0 or o + (b - a) + M > fin.size:
            continue
        pad = round(0.05 * sr)
        ref = _bandpass(src[max(0, a - pad): b + pad], sr)[a - max(0, a - pad): a - max(0, a - pad) + (b - a)]
        t0 = max(0, o - M - pad)
        tgt_full = _bandpass(fin[t0: o + (b - a) + M + pad], sr)
        tgt = tgt_full[(o - M) - t0: (o - M) - t0 + (b - a) + 2 * M]
        nr = float(np.linalg.norm(ref))
        if nr < 1e-9 or tgt.size < ref.size:
            out.append(SyncProbe(seg_id=seg.seg_id, out_t=round(float(out_t), 4)))
            continue
        cc = sps.correlate(tgt, ref, mode="valid", method="fft")
        e = np.concatenate(([0.0], np.cumsum(tgt * tgt)))
        win = np.sqrt(np.maximum(e[ref.size:] - e[: e.size - ref.size], 1e-18))[: cc.size]
        ncc = cc / (nr * win)
        k = int(np.argmax(ncc))
        frac = 0.0
        if 0 < k < ncc.size - 1:
            y0, y1, y2 = ncc[k - 1], ncc[k], ncc[k + 1]
            den = y0 - 2 * y1 + y2
            if abs(den) > 1e-12:
                frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
        lag = (k + frac - M) * 1000.0 / sr + 0.0
        corr = float(ncc[k])
        out.append(SyncProbe(seg_id=seg.seg_id, out_t=round(float(out_t), 4), lag_ms=round(lag, 3) + 0.0,
                             corr=round(corr, 3), reliable=corr >= min_corr and abs(k - M) < M))
    return out


# ============================================================================================ text placement
def _face_box_src(index: TakeIndex, t_us: int) -> FaceBox | None:
    """Face at a source time (same visibility rules as caption placement): None during ``face_lost`` or
    beyond the track (±0.4 s)."""
    for ev in index.visual.events:
        if ev.kind == "face_lost" and ev.start_us <= t_us <= ev.end_us:
            return None
    track = index.visual.face_track
    if track is not None and track.points:
        tol = 400_000
        if track.points[0].t_us - tol <= t_us <= track.points[-1].t_us + tol:
            return track.at(t_us)
        return None
    faced = [s for s in index.visual.samples if s.face_box is not None and s.face_conf >= 0.3]
    if not faced:
        return None
    best = min(faced, key=lambda s: abs(s.t_us - t_us))
    return best.face_box if abs(best.t_us - t_us) <= 300_000 else None


def _fullscreen_at(timeline: Timeline, t: Fraction) -> bool:
    return any(ins.mode in ("full", "card") and to_fraction(ins.out_start) <= t < to_fraction(ins.out_end)
               for ins in timeline.inserts)


def face_protected_rect(timeline: Timeline, index: TakeIndex, t: Any) -> tuple[float, float, float, float] | None:
    """Eyes-to-mouth box (x 10–90 %, y 22–92 % of the face box) in output px at output time ``t`` after the
    framing transform; None when no face is on screen (full-screen insert, face lost, outside the story)."""
    from studio.compile.timeline import map_source_point

    tt = to_fraction(t)
    if _fullscreen_at(timeline, tt):
        return None
    seg = timeline.segment_at(tt)
    if seg is None:
        return None
    src_us = min(max(seg.out_to_src_us(tt), seg.src_in_us), seg.src_out_us)
    fb = _face_box_src(index, src_us)
    if fb is None or fb.w <= 0 or fb.h <= 0:
        return None
    sw, sh = index.media.width, index.media.height
    p0 = map_source_point(timeline, tt, fb.x, fb.y, sw, sh)
    p1 = map_source_point(timeline, tt, fb.x + fb.w, fb.y + fb.h, sw, sh)
    if p0 is None or p1 is None:
        return None
    W, H = timeline.width, timeline.height
    x0, y0, x1, y1 = p0[0] * W, p0[1] * H, p1[0] * W, p1[1] * H
    if x1 <= 0 or y1 <= 0 or x0 >= W or y0 >= H:
        return None
    w, h = x1 - x0, y1 - y0
    return (x0 + 0.1 * w, y0 + 0.22 * h, x1 - 0.1 * w, y0 + 0.92 * h)


def _overlap_area(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _zone(platform: str | None, timeline: Timeline, settings: Settings | None) -> Any:
    from studio.compile.captions import load_constants, safe_zone_for

    try:
        constants = load_constants(settings)
    except Exception:  # pragma: no cover
        constants = {}
    return safe_zone_for(platform or "all", width=timeline.width, height=timeline.height, constants=constants)


def _times(a: Fraction, b: Fraction, step_s: float) -> list[Fraction]:
    step = Fraction(step_s).limit_denominator(1000)
    out = []
    t = a
    while t < b:
        out.append(t)
        t += step
    out.append(max(a, b - Fraction(1, 1000)))
    return out


def _caption_box(pg: Any, W: int, H: int, zone: Any, params: Any) -> tuple[list[float], bool]:
    """Estimated caption block (px) — the caption module's layout estimate (:func:`caption_fit`: shrink to
    the legibility floor, then wrap), centred and clamped horizontally into the band like the renderer;
    returns (box, too_wide)."""
    from studio.compile.captions import caption_fit

    text = pg.text or " ".join(w.text for w in pg.words)
    fit = caption_fit(pg.style, text, W, params, max_width=W - zone.left - zone.right)
    bw, bh = fit.width, fit.height
    left, right = zone.left, W - zone.right
    too_wide = bw > right - left + 0.5
    cx = min(max(W / 2, left + bw / 2), right - bw / 2) if not too_wide else W / 2
    cy = pg.y_norm * H
    return [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], too_wide


def _text_box(t: Any, W: int, H: int, zone: Any) -> tuple[list[float], bool]:
    """Estimated text-overlay block (px), clamped into the strict band like the renderer."""
    from studio.compile.captions import estimate_text_width

    size = float(t.style.size_px) * W / 1080.0
    band_w = W - zone.left - zone.right
    full = estimate_text_width(t.text, size, t.style.font, stroke_px=float(t.style.stroke_px), case=t.style.case)
    lines = max(1, math.ceil(full / max(band_w, 1.0)))
    est = min(full, band_w)
    if t.kind == "list" and t.items:
        lines += len(t.items)
        est = band_w * 0.9
    h = lines * size * 1.2 + size * 0.5
    cx, cy = t.x_norm * W, t.y_norm * H
    x0 = min(max(cx - est / 2, zone.left), W - zone.right - est)
    y0 = min(max(cy - h / 2, zone.top), H - zone.bottom - h)
    too_tall = h > H - zone.top - zone.bottom
    return [x0, y0, x0 + est, y0 + h], too_tall


def planned_text_boxes(timeline: Timeline, index: TakeIndex | None, platform: str | None = None, *,
                       settings: Settings | None = None, face_step_s: float = 0.1) -> list[TextBox]:
    """Estimated caption/text boxes from the timeline vs the safe zone and the face after transforms."""
    from studio.compile.captions import load_caption_params

    W, H = timeline.width, timeline.height
    zone = _zone(platform, timeline, settings)
    params = load_caption_params(settings)
    out: list[TextBox] = []

    def faces(a: Fraction, b: Fraction) -> list[tuple[float, float, float, float]]:
        if index is None:
            return []
        rects = []
        for t in _times(a, b, face_step_s):
            r = face_protected_rect(timeline, index, t)
            if r is not None:
                rects.append(r)
        return rects

    for k, pg in enumerate(timeline.captions):
        a, b = to_fraction(pg.out_start), to_fraction(pg.out_end)
        box, too_wide = _caption_box(pg, W, H, zone, params)
        issues: list[str] = []
        floor = H - min(zone.bottom, zone.caption_floor)
        outside = max(0.0, zone.top - box[1]) + max(0.0, box[3] - floor) + max(0.0, zone.left - box[0]) + \
            max(0.0, box[2] - (W - zone.right))
        if outside > 0.5:
            issues.append("outside_safe_zone")
        if too_wide:
            issues.append("too_wide")
        ov = max((_overlap_area(box, r) for r in faces(a, b)), default=0.0)
        if ov > 0.0:
            issues.append("covers_face")
        out.append(TextBox(kind="caption", id=pg.page_id or f"page{k + 1}", source="planned",
                           out_start=round(float(a), 4),
                           out_end=round(float(b), 4), box=[round(v, 1) for v in box], issues=issues,
                           face_overlap_px=round(ov, 1), outside_px=round(outside, 1), platform=platform or "all",
                           refs=[w.word_id for w in pg.words]))
    for t in timeline.texts:
        a, b = to_fraction(t.out_start), to_fraction(t.out_end)
        box, too_tall = _text_box(t, W, H, zone)
        issues = []
        outside = max(0.0, zone.top - box[1]) + max(0.0, box[3] - (H - zone.bottom))
        if outside > 0.5:
            issues.append("outside_safe_zone")
        if too_tall:
            issues.append("too_wide")
        ov = max((_overlap_area(box, r) for r in faces(a, b)), default=0.0)
        if ov > 0.0:
            issues.append("covers_face")
        out.append(TextBox(kind="text", id=t.text_id, source="planned", out_start=round(float(a), 4),
                           out_end=round(float(b), 4), box=[round(v, 1) for v in box], issues=issues,
                           face_overlap_px=round(ov, 1), outside_px=round(outside, 1), platform=platform or "all"))
    return out


def _alpha_frames(path: Path, stride: int, w: int, h: int) -> Iterable[tuple[int, np.ndarray]]:
    """Stream ``(frame_index, alpha uint8 HxW)`` for every ``stride``-th frame of an alpha video."""
    vf = f"select='not(mod(n\\,{stride}))',alphaextract,scale={w}:{h}:flags=area"
    cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-v", "error", "-i", str(path), "-map", "0:v:0", "-an",
           "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    size = w * h
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        assert proc.stdout is not None
        k = 0
        try:
            while True:
                buf = proc.stdout.read(size)
                if not buf or len(buf) < size:
                    break
                yield k * stride, np.frombuffer(buf, np.uint8).reshape(h, w)
                k += 1
        finally:
            with contextlib.suppress(Exception):
                proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            rc = proc.wait()
        if rc not in (0, -9) and k == 0:
            err.seek(0)
            raise RuntimeError(f"alpha decode failed on {path.name}: {err.read().decode(errors='replace')[-300:]}")


def rendered_text_boxes(timeline: Timeline, index: TakeIndex | None, overlay_path: str | os.PathLike[str],
                        platform: str | None = None, *, settings: Settings | None = None, stride_s: float = 0.1,
                        alpha_threshold: float = 0.25, scale: float = 0.5, min_px: float = 150.0,
                        dilate_px: float = 24.0) -> list[TextBox]:
    """Check the **rendered** overlay layer (alpha plane) against the safe zone and the transformed face.

    Frames are sampled every ``stride_s``; frames under full-screen inserts/cards are skipped (the face is
    not on screen and cards fill the frame by design). A pixel counts as text when alpha ≥
    ``alpha_threshold``; violations smaller than ``min_px`` (px² at the 1080-wide reference, scaled to the
    output size: anti-aliased stroke tips) are ignored. Each violation is attributed to the active caption
    page/text whose planned box (dilated by ``dilate_px`` at the reference size) holds most of it, else to
    the nearest active element (planned boxes are estimates); pixels drawn while no caption/text is active
    are reported per violation kind as ``unattributed_outside`` / ``unattributed_face``."""
    from studio.compile.captions import load_caption_params
    from studio.compile.video import probe_video

    path = Path(overlay_path)
    W, H = timeline.width, timeline.height
    ref = W / 1080.0
    min_area = min_px * ref * ref
    dilate = dilate_px * ref
    zone = _zone(platform, timeline, settings)
    params = load_caption_params(settings)
    vp = probe_video(path)
    fps = to_fraction(vp.fps) if getattr(vp, "fps", None) else to_fraction(timeline.fps)
    sw = max(2, round(W * scale / 2) * 2)
    shh = max(2, round(H * scale / 2) * 2)
    sx, sy = sw / W, shh / H
    px_area = 1.0 / (sx * sy)
    stride = max(1, round(float(fps) * stride_s))
    thr = round(alpha_threshold * 255)

    # planned boxes (for attribution) in full-res px
    elems: list[tuple[str, str, Fraction, Fraction, list[float], list[str]]] = []
    for k, pg in enumerate(timeline.captions):
        box, _ = _caption_box(pg, W, H, zone, params)
        elems.append(("caption", pg.page_id or f"page{k + 1}", to_fraction(pg.out_start), to_fraction(pg.out_end), box,
                      [w.word_id for w in pg.words]))
    for t in timeline.texts:
        box, _ = _text_box(t, W, H, zone)
        elems.append(("text", t.text_id, to_fraction(t.out_start), to_fraction(t.out_end), box, []))

    yy, xx = np.mgrid[0:shh, 0:sw]
    xs = (xx + 0.5) / sx
    ys = (yy + 0.5) / sy
    in_band_x = (xs >= zone.left) & (xs <= W - zone.right)
    strict = in_band_x & (ys >= zone.top) & (ys <= H - zone.bottom)
    relaxed = in_band_x & (ys >= zone.top) & (ys <= H - min(zone.bottom, zone.caption_floor))

    def box_mask(box: Sequence[float], d: float) -> np.ndarray:
        return (xs >= box[0] - d) & (xs <= box[2] + d) & (ys >= box[1] - d) & (ys <= box[3] + d)

    def row_mask(box: Sequence[float], d: float) -> np.ndarray:
        return (ys >= box[1] - d) & (ys <= box[3] + d)

    def bbox(m: np.ndarray) -> list[float] | None:
        ys_i, xs_i = np.nonzero(m)
        if not ys_i.size:
            return None
        return [float(xs_i.min() / sx), float(ys_i.min() / sy), float((xs_i.max() + 1) / sx),
                float((ys_i.max() + 1) / sy)]

    def gap_to(a: Sequence[float], b: Sequence[float]) -> float:
        dx = max(b[0] - a[2], a[0] - b[2], 0.0)
        dy = max(b[1] - a[3], a[1] - b[3], 0.0)
        return math.hypot(dx, dy)

    def new_rec(e: Any) -> dict[str, Any]:
        return {"elem": e, "outside": 0.0, "face": 0.0, "at": None, "box": None, "first": None, "last": None}

    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for n, alpha in _alpha_frames(path, stride, sw, shh):
        t = Fraction(n) / fps
        if t >= to_fraction(timeline.duration):
            break
        if _fullscreen_at(timeline, t):
            continue
        mask = alpha >= thr
        if not mask.any():
            continue
        active = [e for e in elems if e[2] <= t < e[3]]
        cap_allowed = np.zeros_like(mask)
        emasks = []
        for e in active:
            bm = box_mask(e[4], dilate)
            emasks.append((e, bm))
            if e[0] == "caption":
                # a caption may use the relaxed floor on the rows the planner put it on, across the band: the
                # planned box is a width *estimate* and the renderer (real font metrics, the pop/emphasis scale)
                # draws the line wider; a page rendered far from its planned rows is still outside
                cap_allowed |= row_mask(e[4], dilate + 0.5 * (e[4][3] - e[4][1])) & relaxed
        outside = mask & ~(strict | cap_allowed)
        prot = face_protected_rect(timeline, index, t) if index is not None else None
        face_hit = np.zeros_like(mask)
        if prot is not None:
            face_hit = mask & (xs >= prot[0]) & (xs <= prot[2]) & (ys >= prot[1]) & (ys <= prot[3])
        # per element bookkeeping (also records clean elements)
        for e, bm in emasks:
            rec = agg.setdefault((e[0], e[1]), new_rec(e))
            if rec["box"] is None:
                rec["box"] = bbox(mask & bm)
        for kind, hit in (("outside", outside), ("face", face_hit)):
            cnt = float(hit.sum()) * px_area
            if cnt < min_area:
                continue
            hb = bbox(hit)
            best_e, best_n = None, 0.0
            for e, bm in emasks:
                nn = float((hit & bm).sum())
                if nn > best_n:
                    best_e, best_n = e, nn
            if best_e is None and emasks and hb is not None:  # planned boxes are estimates: nearest active
                best_e = min((e for e, _ in emasks), key=lambda e: gap_to(hb, e[4]))
            key = (best_e[0], best_e[1]) if best_e is not None else ("text", f"unattributed_{kind}")
            rec = agg.setdefault(key, new_rec(best_e))
            rec["first"] = float(t) if rec["first"] is None else rec["first"]
            rec["last"] = float(t)
            if cnt > rec[kind]:
                rec[kind] = cnt
                rec["at"] = float(t)
                rec["box"] = hb
    out: list[TextBox] = []
    for (kind, eid), rec in agg.items():
        e = rec["elem"]
        issues = []
        if rec["outside"] >= min_area:
            issues.append("outside_safe_zone")
        if rec["face"] >= min_area:
            issues.append("covers_face")
        if e is not None:
            t0, t1 = float(e[2]), float(e[3])
        else:  # the span the stray pixels were seen over
            t0 = rec["first"] or 0.0
            t1 = (rec["last"] or t0) + stride / float(fps)
        out.append(TextBox(
            kind=kind if kind in ("caption", "text") else "text", id=eid, source="rendered",
            out_start=round(t0, 4), out_end=round(t1, 4),
            box=[round(v, 1) for v in (rec["box"] or [])], issues=issues, face_overlap_px=round(rec["face"], 1),
            outside_px=round(rec["outside"], 1), at_s=None if rec["at"] is None else round(rec["at"], 4),
            platform=platform or "all", refs=list(e[5]) if e else []))
    out.sort(key=lambda b: (b.out_start, b.id))
    return out


# ============================================================================================ cuts / integrity
def _is_continuous(a: TimelineSegment, b: TimelineSegment) -> bool:
    return a.source == b.source and a.audio_src_out_us == b.audio_src_in_us


def _audio_seam_t(b: TimelineSegment) -> Fraction:
    return to_fraction(b.out_start) - Fraction(b.audio_lead_us, 1_000_000) / to_fraction(b.speed)


def seam_pairs(timeline: Timeline) -> list[tuple[int, TimelineSegment, TimelineSegment]]:
    """``(i, left, right)`` for every true seam (continuous joins excluded), in output order."""
    out = []
    k = 0
    for a, b in zip(timeline.segments, timeline.segments[1:], strict=False):
        if _is_continuous(a, b):
            continue
        out.append((k, a, b))
        k += 1
    return out


def _clause_end(text: str) -> bool:
    return bool(_CLAUSE_END.search((text or "").strip()))


def _kind_of_seam(index: TakeIndex, left: str | None, right: str | None, kept: set[str]) -> tuple[str, list[str]]:
    """``(kind, removed word IDs)`` for the seam between kept words ``left`` and ``right``."""
    if left is None or right is None or not index.has_word(left) or not index.has_word(right):
        return "content", []
    pl, pr = index.word_pos(left), index.word_pos(right)
    if pr == pl + 1:
        return "pause_trim", []
    if pr <= pl:
        return "reorder", []
    between = [w for w in index.words[pl + 1: pr] if w.id not in kept]
    if not between:
        return "content", []
    ids = [w.id for w in between]
    if all(w.kind in ("filler", "event", "cutoff") for w in between):
        return "filler", ids
    kept_clusters = {index.word(w).cluster_id for w in kept if index.has_word(w) and index.word(w).cluster_id}
    removed_clusters = {w.cluster_id for w in between if w.cluster_id}
    incomplete = any(not index.sentence_map[w.sentence_id].complete for w in between
                     if w.sentence_id and w.sentence_id in index.sentence_map)
    if (removed_clusters & kept_clusters) or incomplete:
        return "retake", ids
    return "content", ids


def _punct_start(index: TakeIndex, wid: str) -> bool:
    """``wid`` begins a sentence by its punctuation: first word, the source word before it ends a clause, or it is
    capitalised (not "I")."""
    prev = index.prev_word(wid)
    if prev is None:
        return True
    if _clause_end(prev.text):
        return True
    t = index.word(wid).text.strip().lstrip("\"'“‘(¿¡-")
    return bool(t[:1].isupper()) and t.rstrip(".,!?;:'’\"”") not in ("I", "I'm", "I’m", "I've", "I’ve", "I'll",
                                                                        "I’ll", "I'd", "I’d")


def _word_level_db(x: np.ndarray, sr: int, a: float, b: float) -> float | None:
    i, j = max(0, round(a * sr)), min(x.size, round(b * sr))
    if j - i < round(0.03 * sr):
        return None
    seg = sps.sosfiltfilt(_sos(sr, 100.0), x[max(0, i - 2400): j + 2400])[i - max(0, i - 2400):][: j - i]
    return _db(math.sqrt(float(np.mean(seg * seg))))


def cut_checks(timeline: Timeline, index: TakeIndex, *, audio: np.ndarray | None = None, sr: int = SR,
               kept: set[str] | None = None) -> list[CutCheck]:
    """Classify every seam and flag cuts inside words/clauses; face shift/scale and word level jump
    across each seam (``audio`` = decoded final, for the level jump)."""
    kept = set(kept) if kept is not None else {w for w, s in timeline.word_map.items() if s is not None}
    spans = [(w, _f(s.out_start), _f(s.out_end)) for w, s in timeline.word_map.items() if s is not None]
    mono = _mono(audio) if audio is not None else None
    frame = Fraction(1) / to_fraction(timeline.fps)
    sentence_starts = {s.word_ids[0] for s in index.sentences if s.word_ids}
    out: list[CutCheck] = []
    for k, a, b in seam_pairs(timeline):
        left = a.word_ids[-1] if a.word_ids else None
        right = b.word_ids[0] if b.word_ids else None
        kind, removed = _kind_of_seam(index, left, right, kept)
        t_pic = to_fraction(b.out_start)
        t_aud = _audio_seam_t(b)
        # the *audio* edit is what can clip a phoneme; in a J/L cut the picture seam lands inside a word on
        # purpose (the voice runs across it), so the picture seam is not checked here
        ta = float(t_aud)
        inside_word = [w for w, s, e in spans if s + 0.010 < ta < e - 0.010]
        ltxt = index.word(left).text if left and index.has_word(left) else ""
        rtxt = index.word(right).text if right and index.has_word(right) else ""
        # a sentence start counts only when the words' punctuation agrees (the source word before it ends a clause,
        # or it is capitalised): a split the segmenter made at a pause or dropout alone is not a beat
        starts = bool(right and right in sentence_starts and _punct_start(index, right))
        inside_clause = bool(left and right) and not _clause_end(ltxt) and not starts
        beat = starts or bool(_SENTENCE_END.search((ltxt or "").strip()))
        removed_s = 0.0
        if removed:
            ws = [index.word(w) for w in removed]
            removed_s = (ws[-1].end_us - ws[0].start_us) / 1e6
        cc = CutCheck(seam=k, out_t=round(float(t_pic), 4), audio_out_t=round(float(t_aud), 4), kind=kind,  # type: ignore[arg-type]
                      left_word=left, right_word=right, left_text=ltxt, right_text=rtxt, removed_word_ids=removed,
                      removed_s=round(removed_s, 3), inside_word=bool(inside_word), inside_clause=inside_clause,
                      beat_boundary=beat)
        # face across the picture seam
        before = face_protected_rect(timeline, index, t_pic - frame / 2)
        after = face_protected_rect(timeline, index, t_pic + frame / 2)
        if before is not None and after is not None:
            cb = ((before[0] + before[2]) / 2, (before[1] + before[3]) / 2)
            ca = ((after[0] + after[2]) / 2, (after[1] + after[3]) / 2)
            cc.face_shift_px = round(math.hypot(ca[0] - cb[0], ca[1] - cb[1]), 1)
            hb, ha = before[3] - before[1], after[3] - after[1]
            if hb > 0:
                cc.face_scale_ratio = round(ha / hb, 3)
        if mono is not None and left and right and timeline.word_map.get(left) and timeline.word_map.get(right):
            ls, rs = timeline.word_map[left], timeline.word_map[right]
            la = _word_level_db(mono, sr, _f(ls.out_start), _f(ls.out_end))  # type: ignore[union-attr]
            ra = _word_level_db(mono, sr, _f(rs.out_start), _f(rs.out_end))  # type: ignore[union-attr]
            if la is not None and ra is not None:
                cc.level_jump_db = round(ra - la, 2)
        out.append(cc)
    return out


def dropout_edges_in_output(timeline: Timeline, index: TakeIndex | None
                            ) -> list[tuple[Fraction, str | None, str | None]]:
    """``(output time, kept word before, kept word after)`` for every recording-dropout edge (``Gap.dropouts_us``)
    that lies inside a kept audio window (a hard stop or start the listener hears)."""
    if index is None:
        return []
    edges = sorted({int(e) for g in index.gaps for d in g.dropouts_us for e in d})
    if not edges:
        return []
    out: list[tuple[Fraction, str | None, str | None]] = []
    for seg in timeline.segments:
        for e in edges:
            if seg.audio_src_in_us + 2_000 < e < seg.audio_src_out_us - 2_000:
                t = to_fraction(seg.out_start) + Fraction(e - seg.src_in_us, 1_000_000) / to_fraction(seg.speed)
                before = [w for w in seg.word_ids if index.has_word(w) and index.word(w).end_us <= e + 1_000]
                after = [w for w in seg.word_ids if index.has_word(w) and index.word(w).start_us >= e - 1_000]
                out.append((t, before[-1] if before else None, after[0] if after else None))
    return sorted(out, key=lambda r: r[0])


def word_integrity(timeline: Timeline, index: TakeIndex, *, tol_ms: float = 2.0,
                   leak_tol_ms: float = 15.0, sound_tol_ms: float = 25.0) -> Integrity:
    """Kept words must lie inside their audio window (continuous joins merged into one run); removed spoken
    words must not be audible inside any window beyond ``leak_tol_ms`` (a crossfade's reach); kept words the
    recording cuts off are listed (``truncated``), and non-word sound the Take Index measured in gaps
    (``Gap.sound_us``) must not play inside a window beyond ``sound_tol_ms`` (a seam fade's reach)."""
    runs: list[tuple[int, int, list[str], list[str]]] = []  # (audio in, audio out, words, seg ids)
    prev: TimelineSegment | None = None
    for s in timeline.segments:
        if prev is not None and runs and _is_continuous(prev, s):
            a, _b, ws, ids = runs[-1]
            runs[-1] = (a, s.audio_src_out_us, ws + list(s.word_ids), [*ids, s.seg_id])
        else:
            runs.append((s.audio_src_in_us, s.audio_src_out_us, list(s.word_ids), [s.seg_id]))
        prev = s
    kept = {w for _a, _b, ws, _ in runs for w in ws}
    res = Integrity()
    tol = tol_ms * 1000
    for a, b, ws, ids in runs:
        seg_ref: str | list[str] = ids[0] if len(set(ids)) == 1 else sorted(set(ids), key=ids.index)
        for wid in ws:
            if not index.has_word(wid):
                continue
            w = index.word(wid)
            if w.kind not in _SPOKEN:
                continue
            miss_l = max(0, a - w.start_us)
            miss_r = max(0, w.end_us - b)
            if miss_l > tol or miss_r > tol:
                res.clipped.append({"word_id": wid, "text": w.text, "seg": seg_ref,
                                    "missing_ms": round((miss_l + miss_r) / 1000, 1),
                                    "side": "start" if miss_l >= miss_r else "end"})
        for w in index.words:
            if w.id in kept or w.kind not in _SPOKEN:
                continue
            ov = min(b, w.end_us) - max(a, w.start_us)
            if ov > leak_tol_ms * 1000:
                res.leaked.append({"word_id": w.id, "text": w.text, "seg": seg_ref,
                                   "overlap_ms": round(ov / 1000, 1)})
        spoken = [index.word(w) for w in ws if index.has_word(w)]
        k0 = min((w.start_us for w in spoken), default=a)
        k1 = max((w.end_us for w in spoken), default=b)
        for g in index.gaps:
            for sa, sb in g.sound_us:
                ov = min(b, sb) - max(a, sa)
                if ov > sound_tol_ms * 1000:
                    inner = min(k1, sb) - max(k0, sa)
                    res.sound_leaks.append({"gap_id": g.id, "seg": seg_ref, "overlap_ms": round(ov / 1000, 1),
                                            "before_word": g.before_word_id, "after_word": g.after_word_id,
                                            "muted": inner <= sound_tol_ms * 1000})
    for wid in sorted(kept, key=lambda w: index.word_pos(w) if index.has_word(w) else 0):
        if index.has_word(wid) and index.word(wid).truncated:
            w = index.word(wid)
            res.truncated.append({"word_id": wid, "text": w.text, "side": w.truncated})
    for _a, _b, ws, ids in runs:  # each run's edges are joins: a fragment there is a broken word, then a jump
        edge = [index.word(w) for w in ws if index.has_word(w) and index.word(w).kind != "event"]
        if not edge:
            continue
        for side, w in (("start", edge[0]), ("end", edge[-1])):
            if w.kind == "cutoff" and not w.truncated \
                    and not any(d["word_id"] == w.id for d in res.cutoff_at_join):
                res.cutoff_at_join.append({"word_id": w.id, "text": w.display(), "side": side,
                                           "seg": ids[0] if side == "start" else ids[-1]})
    return res


# ============================================================================================ pacing
def pacing_metrics(timeline: Timeline, index: TakeIndex, *, cuts: Sequence[CutCheck] = (),
                   doc: CutDocument | None = None, pause_min_ms: float = 150.0,
                   long_pause_ms: float = 1000.0, static_s: float = 8.0) -> Pacing:
    """Seams/min, pauses, first/last word timing, WPM, fillers, static stretches, caption reading speed."""
    from studio.compile.timeline import audio_seams

    dur = _f(timeline.duration)
    mins = dur / 60.0 if dur > 0 else 0.0
    p = Pacing(duration_s=round(dur, 4))
    p.seams = len(timeline.seams)
    p.audio_seams = len(audio_seams(timeline))
    p.content_seams = sum(1 for c in cuts if c.kind != "pause_trim")
    if mins > 0:
        p.seams_per_min = round(p.seams / mins, 2)
        p.content_seams_per_min = round(p.content_seams / mins, 2)
    p.cuts_inside_clauses = sum(1 for c in cuts if c.inside_clause and c.kind != "pause_trim")
    p.cuts_inside_words = sum(1 for c in cuts if c.inside_word)
    words = spoken_word_spans(timeline, index)
    lexical = [w for w in words if index.has_word(w[0]) and index.word(w[0]).kind == "word"]
    p.words_kept = len(words)
    kept_ids = {w for w, s in timeline.word_map.items() if s is not None}
    removed = [w for w in index.words if w.id not in kept_ids]
    p.words_removed = len(removed)
    p.removed_source_s = round(sum(w.duration_us for w in removed) / 1e6, 3)
    if words:
        p.time_to_first_speech_s = round(words[0][1], 4)
        p.final_word_to_end_s = round(dur - max(w[2] for w in words), 4)
    if mins > 0:
        p.wpm = round(len(lexical) / mins, 1)
        p.fillers_kept = sum(1 for w in words if index.has_word(w[0]) and index.word(w[0]).kind == "filler")
        p.fillers_per_min = round(p.fillers_kept / mins, 2)
    pauses = []
    for (w1, _s1, e1), (w2, s2, _e2) in itertools.pairwise(words):
        g = (s2 - e1) * 1000.0
        if g >= pause_min_ms:
            pauses.append((g, w1, w2, e1))
    if pauses:
        p.pause_median_ms = round(statistics.median(g for g, *_ in pauses), 1)
        p.long_pauses = [{"after": w1, "before": w2, "at_s": round(t, 3), "ms": round(g, 1)}
                         for g, w1, w2, t in pauses if g >= long_pause_ms]
    src = [g.duration_ms for g in index.gaps if g.is_inner and g.duration_ms >= pause_min_ms]
    if src:
        p.source_pause_median_ms = round(statistics.median(src), 1)
        if p.pause_median_ms is not None and p.source_pause_median_ms > 0:
            p.pause_ratio = round(p.pause_median_ms / p.source_pause_median_ms, 3)
    if doc is not None and doc.pins.payoff_word_ids and dur > 0:
        starts = [_f(timeline.word_map[w].out_start) for w in doc.pins.payoff_word_ids  # type: ignore[union-attr]
                  if timeline.word_map.get(w) is not None]
        if starts:
            p.payoff_position_frac = round(min(starts) / dur, 3)
    # static stretches: time between visual changes (cuts, inserts, framing changes, text overlays)
    events = {0.0, dur}
    events.update(_f(t) for t in timeline.seams)
    for ins in timeline.inserts:
        events.update((_f(ins.out_start), _f(ins.out_end)))
    for t in timeline.texts:
        events.add(_f(t.out_start))
    for s in timeline.segments:
        prev = None
        for key in s.framing:
            state = (round(key.scale, 3), round(key.cx, 3), round(key.cy, 3))
            if prev is not None and state != prev:
                events.add(_f(key.out_t))
            prev = state
    ev = sorted(e for e in events if 0.0 <= e <= dur)
    stretches = [(a, b) for a, b in itertools.pairwise(ev) if b - a > 0]
    if stretches:
        a, b = max(stretches, key=lambda ab: ab[1] - ab[0])
        p.longest_static_s = round(b - a, 3)
        p.static_stretches = [{"start_s": round(a, 3), "end_s": round(b, 3)} for a, b in stretches if b - a >= static_s]
    cps = []
    for pg in timeline.captions:
        d = _f(pg.out_end) - _f(pg.out_start)
        if d > 0:
            txt = pg.text or " ".join(w.text for w in pg.words)
            cps.append(len(txt) / d)
    if cps:
        p.caption_cps_max = round(max(cps), 2)
        p.caption_cps_median = round(statistics.median(cps), 2)
    return p


# ============================================================================================ ASR round trip
def normalize_asr_token(text: str) -> list[str]:
    """Lower-case tokens without edge punctuation; hyphenated compounds split (``"follow-up"`` →
    ``["follow", "up"]``); cut-off hyphens dropped."""
    from studio.perception.transcribe import normalize_token

    t = (text or "").replace("—", " ").replace("–", " ")
    toks = []
    for part in re.split(r"[\s\-/]+", t):
        n = normalize_token(part)
        if n:
            toks.append(n)
    return toks


def align_tokens(ref: Sequence[str], hyp: Sequence[str]) -> list[tuple[str, int | None, int | None]]:
    """Similarity-weighted edit alignment: ``[(op, ref_i, hyp_j)]`` with op in equal/substitute/delete/insert.

    A substitution costs ``1 + 0.5·(1 − sim)`` (sim = character similarity, so always < a deletion plus an
    insertion): among alignments with the same number of edits the one that pairs *similar* words wins,
    so when the ASR drops one word and mishears its neighbour ("keep the pauses" → "keeps pauses") the
    deletion is charged to the word that is really missing ("the"), not to whichever word plain
    Levenshtein happens to pick — which is what localizes seam damage to the right word ID."""
    from rapidfuzz.distance import Levenshtein

    n, m = len(ref), len(hyp)
    INF = float("inf")
    D = np.full((n + 1, m + 1), INF)
    D[0, :] = np.arange(m + 1)
    D[:, 0] = np.arange(n + 1)
    back = np.zeros((n + 1, m + 1), dtype=np.int8)  # 0 diag, 1 up (delete), 2 left (insert)
    back[0, 1:] = 2
    back[1:, 0] = 1
    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            hj = hyp[j - 1]
            if ri == hj:
                c = 0.0
            else:
                sim = Levenshtein.normalized_similarity(ri, hj)
                c = 1.0 + 0.5 * (1.0 - sim)
            best, arg = D[i - 1, j - 1] + c, 0
            if D[i - 1, j] + 1.0 < best:
                best, arg = D[i - 1, j] + 1.0, 1
            if D[i, j - 1] + 1.0 < best:
                best, arg = D[i, j - 1] + 1.0, 2
            D[i, j] = best
            back[i, j] = arg
    ops: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        b = back[i, j]
        if i > 0 and j > 0 and b == 0:
            ops.append(("equal" if ref[i - 1] == hyp[j - 1] else "substitute", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and (j == 0 or b == 1):
            ops.append(("delete", i - 1, None))
            i -= 1
        else:
            ops.append(("insert", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


Transcriber = Callable[[Path], "AsrResult"]


def _default_transcriber(settings: Settings | None, provider: str | None, cache_dir: Path | None = None) -> Transcriber:
    from studio.perception.transcribe import transcribe_file

    def run(wav: Path) -> AsrResult:  # raw responses cached next to each input (one cache per audio)
        return transcribe_file(wav, provider=provider, settings=settings, cache_dir=cache_dir or wav.parent)

    return run


def _expected_tokens(timeline: Timeline, index: TakeIndex) -> tuple[list[str], list[str], dict[str, float]]:
    """Normalized tokens of the kept spoken words in output order, the word ID of each token, and each
    word's output start (s)."""
    toks: list[str] = []
    ids: list[str] = []
    starts: dict[str, float] = {}
    for wid, s, _e in spoken_word_spans(timeline, index):
        starts[wid] = s
        for tok in normalize_asr_token(index.word(wid).text):
            toks.append(tok)
            ids.append(wid)
    return toks, ids, starts


def _hear(run: Transcriber, audio: np.ndarray, sr: int, wav: Path) -> tuple[AsrResult, list[str], list[float]]:
    """Transcribe mono float32 ``audio`` written to ``wav``: (result, tokens, token start times in s)."""
    import soundfile as sf

    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(wav), _mono(audio).astype(np.float32), sr, subtype="FLOAT")
    asr = run(wav)
    toks: list[str] = []
    times: list[float] = []
    for w in asr.words:
        if w.kind == "event":
            continue
        for tok in normalize_asr_token(w.text):
            toks.append(tok)
            times.append(w.start_us / 1e6)
    return asr, toks, times


def _lost_words(exp_ids: Sequence[str], exp_tokens: Sequence[str], hyp_tokens: Sequence[str]) -> set[str]:
    """Word IDs with a deleted token, or a substituted token that shares < 50 % of its characters."""
    from rapidfuzz.distance import Levenshtein

    lost: set[str] = set()
    for op, i, j in align_tokens(exp_tokens, hyp_tokens):
        if op == "delete" or (op == "substitute" and Levenshtein.normalized_similarity(
                exp_tokens[i], hyp_tokens[j]) < 0.5):  # type: ignore[index]
            lost.add(exp_ids[i])  # type: ignore[index]
    return lost


def asr_round_trip(timeline: Timeline, index: TakeIndex, audio: np.ndarray, *, sr: int = SR,
                   transcriber: Transcriber | None = None, settings: Settings | None = None,
                   work_dir: Path | None = None, provider: str | None = None, cuts: Sequence[CutCheck] = (),
                   pinned: Iterable[str] = (), confirm_audio: np.ndarray | None = None) -> AsrRoundTrip:
    """Transcribe the final's audio and diff it against the expected kept words (by word ID).

    A confidently recognised source word next to a seam that the render's ASR loses is *seam damage*
    (the clipped-phoneme signature). Music hits and SFX are often placed on seams and can mask a word
    for the ASR without anything being clipped, so when ``confirm_audio`` (the render's dialogue stem,
    sample-aligned with the final) is given, each candidate is re-checked on the dialogue alone: lost
    there too → seam damage; heard there → ``masked`` (a mix-balance note, not a clipped phoneme)."""
    import jiwer
    from rapidfuzz.distance import Levenshtein

    res = AsrRoundTrip()
    exp_tokens, exp_ids, starts = _expected_tokens(timeline, index)
    res.expected_words = len(exp_tokens)
    res.expected_text = " ".join(exp_tokens)
    wd = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="qa_asr_"))
    wd.mkdir(parents=True, exist_ok=True)
    run = transcriber or _default_transcriber(settings, provider)
    asr, hyp_tokens, hyp_times = _hear(run, audio, sr, wd / "asr_input.wav")
    res.ran = True
    res.provider = asr.asr.provider
    res.model = asr.asr.model
    res.hypothesis_words = len(hyp_tokens)
    res.hypothesis_text = " ".join(hyp_tokens)
    if exp_tokens and hyp_tokens:
        o = jiwer.process_words(" ".join(exp_tokens), " ".join(hyp_tokens))
        res.wer = round(float(o.wer), 4)
        res.hits, res.substitutions, res.deletions, res.insertions = o.hits, o.substitutions, o.deletions, o.insertions
    elif exp_tokens:
        res.wer = 1.0
        res.deletions = len(exp_tokens)
    near: set[str] = set()
    for c in cuts:
        for w in (c.left_word, c.right_word):
            if w:
                near.add(w)
    pinned_set = set(pinned)
    offsets = []
    by_word: dict[str, AsrWordDiff] = {}
    for op, i, j in align_tokens(exp_tokens, hyp_tokens):
        if op == "insert":
            res.inserted.append(hyp_tokens[j])  # type: ignore[index]
            continue
        wid = exp_ids[i]  # type: ignore[index]
        w = index.word(wid)
        if op == "equal":
            offsets.append((hyp_times[j] - starts[wid]) * 1000.0)  # type: ignore[index]
            continue
        heard = hyp_tokens[j] if j is not None else ""  # type: ignore[index]
        d = by_word.get(wid)
        if d is None:
            d = AsrWordDiff(word_id=wid, text=w.text, op="deleted" if op == "delete" else "substituted", heard=heard,
                            near_seam=wid in near, confidence=round(w.confidence, 3))
            by_word[wid] = d
        elif heard:
            d.heard = (d.heard + " " + heard).strip()
            d.op = "substituted" if op == "substitute" else d.op
    res.diffs = list(by_word.values())
    candidates: list[str] = []
    for d in res.diffs:
        if not d.near_seam or index.word(d.word_id).kind != "word" or (d.confidence or 0) < 0.6:
            continue
        sim = Levenshtein.normalized_similarity(" ".join(normalize_asr_token(d.text)), d.heard) if d.heard else 0.0
        if d.op == "deleted" or sim < 0.5:
            candidates.append(d.word_id)
    res.seam_damage = candidates
    if candidates and confirm_audio is not None:
        _asr2, hyp2, _t2 = _hear(run, confirm_audio, sr, wd / "dialogue" / "asr_input.wav")
        lost = _lost_words(exp_ids, exp_tokens, hyp2)
        res.confirm_ran = True
        res.confirm_wer = round(float(jiwer.wer(" ".join(exp_tokens), " ".join(hyp2))), 4) if hyp2 else 1.0
        res.seam_damage = [w for w in candidates if w in lost]
        res.masked = [w for w in candidates if w not in lost]
    res.pinned_missing = [d.word_id for d in res.diffs if d.word_id in pinned_set and d.op == "deleted"]
    if offsets:
        res.timing_offset_ms_median = round(float(statistics.median(offsets)), 1)
        res.timing_offset_ms_p90_abs = round(float(np.percentile(np.abs(offsets), 90)), 1)
    return res


# ============================================================================================ intelligibility / banding
def intelligibility(clean: np.ndarray, mix: np.ndarray, sr: int = SR, *, phone_hp_hz: float = 350.0) -> Intelligibility:
    """ESTOI(dialogue stem, final mix) full band and through a phone-speaker high-pass."""
    from pystoi import stoi

    c = _mono(clean)
    m = _mono(mix)
    n = min(c.size, m.size)
    out = Intelligibility(reference="stems/dialogue.wav")
    if n < sr:  # ESTOI needs ≥ ~1 s of speech
        return out
    c, m = c[:n], m[:n]
    with np.errstate(all="ignore"):
        out.estoi = _r(float(stoi(c, m, sr, extended=True)), 4)
        sos = _sos(sr, phone_hp_hz)
        out.estoi_phone = _r(float(stoi(sps.sosfilt(sos, c), sps.sosfilt(sos, m), sr, extended=True)), 4)
    return out


def cambi(path: str | os.PathLike[str], *, per_second: float = 2.0, fps: Fraction | None = None,
          threads: int | None = None) -> Banding:
    """CAMBI banding index (libvmaf) on frames sampled ``per_second``."""
    p = Path(path)
    if fps is None:
        v = next((s for s in probe_streams(p).get("streams", []) if s.get("codec_type") == "video"), {})
        fps = _fps_of(v, "avg_frame_rate") or _fps_of(v, "r_frame_rate") or Fraction(30)
    stride = max(1, round(float(fps) / per_second))
    th = threads or max(1, min(8, (os.cpu_count() or 4)))
    with tempfile.TemporaryDirectory(prefix="qa_cambi_") as td:
        log = Path(td) / "cambi.json"
        sel = f"select='not(mod(n\\,{stride}))',setpts=N/TB"
        graph = (f"[0:v]{sel}[a];[1:v]{sel}[b];[a][b]libvmaf=feature=name=cambi:log_fmt=json:"
                 f"log_path={log}:n_threads={th}")
        cmd = [_ffmpeg(), "-nostdin", "-hide_banner", "-v", "error", "-i", str(p), "-i", str(p), "-lavfi", graph,
               "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0 or not log.exists():
            raise RuntimeError(f"CAMBI failed: {(r.stderr or '')[-300:]}")
        data = json.loads(log.read_text())
    vals = [float(f["metrics"]["cambi"]) for f in data.get("frames", []) if "cambi" in f.get("metrics", {})]
    if not vals:
        return Banding()
    return Banding(cambi_mean=_r(float(np.mean(vals)), 3), cambi_max=_r(max(vals), 3),
                   cambi_p95=_r(float(np.percentile(vals, 95)), 3), frames=len(vals))


# ============================================================================================ advice
def _face_seam_advice(c: CutCheck, priors: Mapping[str, Any], height: int) -> list[str]:
    """The face across a picture seam vs the take-matching and seam-hiding priors (framing-and-zooms.md):
    a size change under ×1.25 is a bump (an accident under ×1.10, a lean-in at ×1.15–1.25), not a punch;
    at the same size, an eye-line move over ~2 % of the frame height is a visible jump — fine between
    beats, worth hiding inside a thought."""
    if c.face_scale_ratio is None or c.face_shift_px is None or c.face_scale_ratio <= 0:
        return []
    r = max(c.face_scale_ratio, 1 / c.face_scale_ratio)
    hide = float(prior(priors, "framing.punch_seam_hide_min_x",
                       prior(priors, "seams.scale_change_to_hide_seam_min_x", 1.25)))
    size_pct = float(prior(priors, "framing.take_match_face_size_pct", 5))
    eye_pct = float(prior(priors, "framing.take_match_eye_line_pct", 2))
    accident = float(prior(priors, "framing.punch_accident_below_x", 1.10))
    lean = _range(prior(priors, "framing.punch_lean_in_x", [1.15, 1.25]), (1.15, 1.25))
    where = f"Seam {c.seam} ({c.left_word}→{c.right_word}, {c.out_t:.2f} s)"
    if r >= hide - 1e-6:
        return []
    if r > 1 + size_pct / 100:
        feel = ("reads as an accident" if r < accident else "reads as a lean-in, not a new shot" if r >= lean[0]
                else "a bump")
        return [f"{where}: face size changes ×{r:.2f} — {feel}; match the takes within {size_pct:g} % or change "
                f"by ≥ ×{hide:g} to hide the seam."]
    shift_pct = 100.0 * c.face_shift_px / max(height, 1)
    if shift_pct > eye_pct and not c.beat_boundary:
        return [f"{where} is inside a thought and the face jumps {c.face_shift_px:.0f} px ({shift_pct:.1f} % of "
                f"the frame) at the same size: hide it with a ≥ ×{hide:g} punch or a cutaway, or cut at a beat."]
    return []


def _advice(pk: MetricsPacket, priors: Mapping[str, Any], doc: CutDocument | None, *,
            height: int = 1920) -> list[str]:
    out: list[str] = []
    L = pk.loudness
    if L is not None:
        if L.integrated_lufs is not None and L.within_target is False:
            out.append(f"Loudness {L.integrated_lufs:.1f} LUFS is outside {L.target_lufs:.0f} ±{L.tolerance_lu:g} LU "
                       f"(invariant 8).")
        if L.true_peak_dbtp is not None and L.true_peak_ok is False:
            out.append(f"True peak {L.true_peak_dbtp:.2f} dBTP exceeds {L.ceiling_dbtp:g} dBTP after encode "
                       f"(invariant 8).")
        st_max = float(prior(priors, "loudness.max_short_term_over_integrated_lu", 5))
        if L.short_term_over_integrated_lu is not None and L.short_term_over_integrated_lu > st_max:
            out.append(f"Short-term loudness peaks {L.short_term_over_integrated_lu:.1f} LU above integrated "
                       f"(prior ≤ {st_max:g}): a loud SFX/music swell or an unlevelled phrase.")
        fold = float(prior(priors, "voice.mono_fold_voice_change_max_db", 1))
        if L.mono_fold_delta_lu is not None and abs(L.mono_fold_delta_lu) > fold:
            out.append(f"Mono fold-down changes loudness by {L.mono_fold_delta_lu:+.1f} LU (prior ±{fold:g}): "
                       f"check stereo music/SFX phase for phone speakers.")
        if L.integrated_ffmpeg_lufs is not None and L.integrated_lufs is not None and \
                abs(L.integrated_ffmpeg_lufs - L.integrated_lufs) > 0.3:
            pk.errors.append(f"loudness meters disagree: {L.integrated_lufs} vs ffmpeg {L.integrated_ffmpeg_lufs} LUFS")
    for c in pk.clicks_found:
        out.append(f"Click at seam {c.seam} ({c.out_t:.2f} s, {c.left_word}→{c.right_word}): {c.rule}, "
                   f"margin {c.margin_db:+.1f} dB (invariant 1).")
    near = [c for c in pk.clicks if not c.click and c.margin_db is not None and c.margin_db > -3.0]
    for c in near:
        out.append(f"Near-click at seam {c.seam} ({c.out_t:.2f} s): margin {c.margin_db:+.1f} dB; listen to it.")
    for s in pk.silence_under_speech:
        where = f"under {', '.join(s.word_ids[:3])}" if s.word_ids else "between words"
        src = " in the PCM mix (too short to survive AAC)" if s.source == "mix" else ""
        out.append(f"Digital silence {s.duration_ms:.0f} ms at {s.start_s:.2f} s {where}{src} (invariant 9).")
    for e in pk.unexpected_video_events:
        out.append(f"{e.kind.title()} {e.start_s:.2f}–{e.end_s:.2f} s ({e.reason}).")
    if pk.av is not None and pk.av.problems:
        out.append("A/V: " + "; ".join(pk.av.problems) + " (invariant 2).")
    elif pk.av is not None and pk.av.sync_max_abs_ms is not None and pk.av.sync_max_abs_ms > 2.0:
        out.append(f"Measured audio offset up to {pk.av.sync_max_abs_ms:.1f} ms vs the timeline "
                   f"(expected sample-exact).")
    for t in pk.text_issues:
        out.append(f"{t.kind.title()} {t.id} ({t.source}) {', '.join(t.issues)}"
                   + (f" at {t.at_s:.2f} s" if t.at_s is not None else "") + " (invariant 7).")
    if pk.text_source == "rendered":
        planned = [t for t in pk.text_planned if t.issues]
        for t in planned[:5]:
            out.append(f"Planned {t.kind} {t.id} is estimated to be {', '.join(t.issues)} (rendered layer is clean).")
    for c in pk.cuts:
        if c.inside_word:
            out.append(f"Seam {c.seam} at {c.out_t:.2f} s falls inside a kept word (clipped phoneme risk).")
    ic = [c for c in pk.cuts if c.inside_clause and c.kind in ("content", "reorder", "retake")]
    for c in ic[:6]:
        out.append(f"Seam {c.seam} ({c.kind}) joins “{c.left_text} | {c.right_text}” inside a clause: does the "
                   f"thought still read?")
    jump_db = _range(prior(priors, "audio_seam.level_jump_audible_db", [4, 5]), (4.0, 5.0))[0]
    for c in pk.cuts:
        if c.level_jump_db is not None and abs(c.level_jump_db) >= jump_db:
            out.append(f"Level jump {c.level_jump_db:+.1f} dB across seam {c.seam} ({c.left_word}→{c.right_word}); "
                       f"audible from ~{jump_db:g} dB.")
        out += _face_seam_advice(c, priors, height)
    for d in pk.integrity.clipped:
        out.append(f"Word {d['word_id']} “{d['text']}” is clipped by {d['missing_ms']} ms at its {d['side']} "
                   f"(invariant 1).")
    for d in pk.integrity.leaked:
        out.append(f"Removed word {d['word_id']} “{d['text']}” is audible for {d['overlap_ms']} ms inside {d['seg']}.")
    for d in pk.integrity.cutoff_at_join:
        out.append(f"Cut-off word {d['word_id']} “{d['text']}” sits at a join ({d['seg']} {d['side']}): a broken word, "
                   "then a jump (invariant 1).")
    p = pk.pacing
    if p is not None:
        lo, hi = _range(prior(priors, "hook.first_word_target_s", [0.1, 0.5]), (0.1, 0.5))
        if p.time_to_first_speech_s is not None and not (lo <= p.time_to_first_speech_s <= hi):
            out.append(f"First word at {p.time_to_first_speech_s:.2f} s (prior {lo:g}–{hi:g} s).")
        lo, hi = _range(prior(priors, "endings.last_word_to_end_s", [0.15, 0.5]), (0.15, 0.5))
        if p.final_word_to_end_s is not None and not (lo <= p.final_word_to_end_s <= hi):
            out.append(f"Last word to end {p.final_word_to_end_s:.2f} s (prior {lo:g}–{hi:g} s).")
        lo, hi = _range(prior(priors, "seams.per_60s_clean_take", [1, 4]), (1.0, 4.0))
        if p.content_seams_per_min > hi:
            out.append(f"{p.content_seams_per_min:.1f} content seams per minute (clean-take prior {lo:g}–{hi:g}): "
                       f"every seam needs a named problem.")
        dead = float(prior(priors, "pauses.dead_air_cut_over_s", 1.5))
        for lp in p.long_pauses:
            if lp["ms"] / 1000 > dead:
                out.append(f"Pause {lp['ms'] / 1000:.2f} s after {lp['after']} (dead air over {dead:g} s unless "
                           f"deliberate).")
        if p.pause_ratio is not None:
            lo, hi = _range(prior(priors, "pauses.relative_keep_x_median", [0.7, 1.3]), (0.7, 1.3))
            if not (lo <= p.pause_ratio <= hi):
                out.append(f"Output pause median is ×{p.pause_ratio:.2f} the creator's own (prior ×{lo:g}–{hi:g}).")
        q = float(prior(priors, "seams.static_stretch_question_s", 8))
        for s in p.static_stretches:
            out.append(f"No visual change {s['start_s']:.1f}–{s['end_s']:.1f} s ({s['end_s'] - s['start_s']:.1f} s ≥ "
                       f"{q:g} s): is the talking head carrying it?")
        lo, hi = _range(prior(priors, "hook.payoff_position_frac", [0.55, 0.85]), (0.55, 0.85))
        if p.payoff_position_frac is not None and not (lo <= p.payoff_position_frac <= hi):
            out.append(f"Payoff lands at {p.payoff_position_frac:.0%} of the runtime (prior {lo:.0%}–{hi:.0%}).")
        fmax = float(prior(priors, "fillers.acceptable_per_min", 5))
        if p.fillers_per_min > fmax:
            out.append(f"{p.fillers_per_min:.1f} kept fillers per minute (prior ≤ {fmax:g}).")
        cps_max = float(prior(priors, "captions.cps_max", 20))
        if p.caption_cps_max is not None and p.caption_cps_max > cps_max:
            out.append(f"A caption page runs at {p.caption_cps_max:.0f} characters/s (prior ≤ {cps_max:g}: hard to "
                       f"read in time).")
    a = pk.asr
    if a is not None and a.ran:
        if a.seam_damage:
            out.append(f"ASR round-trip lost seam words {', '.join(a.seam_damage)} (clipped-phoneme signature, "
                       f"invariant 1).")
        if a.masked:
            out.append(f"ASR loses seam words {', '.join(a.masked)} in the mix but hears them in the dialogue alone: "
                       f"music/SFX masks them there.")
        if a.wer is not None and a.wer > 0.08:
            out.append(f"ASR round-trip WER {a.wer:.1%} vs the kept transcript.")
        if a.timing_offset_ms_median is not None and abs(a.timing_offset_ms_median) > 150:
            out.append(f"ASR word timing is offset {a.timing_offset_ms_median:+.0f} ms from the timeline.")
    it = pk.intelligibility
    if it is not None and it.estoi is not None:
        if it.estoi < 0.85:
            out.append(f"ESTOI {it.estoi:.2f} through the mix: music/SFX may mask speech.")
        if it.estoi_phone is not None and it.estoi_phone < 0.8:
            out.append(f"ESTOI {it.estoi_phone:.2f} through a phone-speaker simulation.")
    b = pk.banding
    cmax = float(prior(priors, "color.cambi_max", 3))
    if b is not None and b.cambi_max is not None and b.cambi_max > cmax:
        out.append(f"CAMBI banding up to {b.cambi_max:.1f} (prior ≤ {cmax:g}).")
    e = pk.edges
    if e is not None and e.loop_step_dbfs is not None and e.loop_step_dbfs > -40:
        out.append(f"Last→first sample step {e.loop_step_dbfs:.0f} dBFS: the loop point may click.")
    del doc
    return out


# ============================================================================================ measure
def measure(job: Job | None, timeline: Timeline, final_path: str | os.PathLike[str], *,
            doc: CutDocument | None = None, index: TakeIndex | None = None,
            render_dir: str | os.PathLike[str] | None = None, platform: str | None = None,
            asr: bool | None = None, light: bool = False, settings: Settings | None = None,
            transcriber: Transcriber | None = None, asr_provider: str | None = None, estoi: bool | None = None,
            banding: bool | None = None, rendered_text: bool | None = None, video_events: bool | None = None,
            click_params: ClickParams | None = None, save: bool = True) -> MetricsPacket:
    """Measure one deliverable (see module docstring).

    ``asr``: None = run when an ASR key is configured, False = skip, True = run (errors recorded).
    ``light`` skips the expensive whole-file analyses (ASR, ESTOI, CAMBI, rendered text, black/freeze)
    — used for secondary deliverables that share the primary's picture. Measurements never raise:
    failures are recorded in ``errors``.
    """
    from studio.config import get_settings

    settings = settings or get_settings()
    final = Path(final_path)
    rd = Path(render_dir) if render_dir is not None else final.parent
    if index is None and job is not None:
        with contextlib.suppress(Exception):
            index = job.load_index()
    if doc is None and job is not None:
        with contextlib.suppress(Exception):
            doc = job.load_doc(timeline.doc_version) if timeline.doc_version is not None else job.load_doc()
    plat = platform or platform_of(final, doc)
    priors = load_priors(settings)
    pk = MetricsPacket(created_at=_now_iso(), job_id=timeline.job_id or (job.id if job is not None else ""),
                       doc_version=timeline.doc_version, render_dir=str(rd), final_path=str(final), platform=plat,
                       light=light, duration_s=round(_f(timeline.duration), 4))
    target = (doc.audio.loudness_target_lufs if doc is not None
              else float(prior(priors, "loudness.integrated_lufs", -14.0)))
    ceiling = doc.audio.true_peak_dbtp if doc is not None else float(prior(priors, "loudness.true_peak_max_dbtp", -1.0))
    tol = float(prior(priors, "loudness.gate_tolerance_lu", 1.0))

    def attempt(what: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as e:  # measurement failures are data, never crashes
            pk.errors.append(f"{what}: {type(e).__name__}: {str(e)[:300]}")
            return None

    audio = attempt("decode audio", lambda: decode_final_audio(final))
    _src_cache: dict[str, np.ndarray | None] = {}

    def source_audio() -> np.ndarray | None:
        """The job's source dialogue (``media/audio.wav``), mono, loaded once."""
        if "x" not in _src_cache:
            _src_cache["x"] = None
            if job is not None and job.audio_path.exists():
                try:
                    _src_cache["x"] = _read_wav_mono(job.audio_path)
                except Exception as e:
                    pk.errors.append(f"source audio: {type(e).__name__}: {str(e)[:200]}")
        return _src_cache["x"]

    probe = attempt("probe", lambda: probe_streams(final, count_packets=True)) or {}
    if audio is not None:
        pk.loudness = attempt("loudness", lambda: measure_loudness(audio, SR, path=final, target_lufs=target,
                                                                  ceiling_dbtp=ceiling, tolerance_lu=tol))
        pk.edges = attempt("edges", lambda: edge_levels(audio))
        # ---- clicks at every audio seam
        def _clicks() -> list[SeamClick]:
            pairs = seam_pairs(timeline)
            times = [_audio_seam_t(b) for _k, _a, b in pairs]
            labels = [(a.word_ids[-1] if a.word_ids else None, b.word_ids[0] if b.word_ids else None)
                      for _k, a, b in pairs]
            src_pos = [(round(a.audio_src_out_us * SR / 1e6), round(b.audio_src_in_us * SR / 1e6))
                       for _k, a, b in pairs]
            return detect_seam_clicks(audio, SR, times, params=click_params, labels=labels, source=source_audio(),
                                      source_samples=src_pos)

        pk.clicks = attempt("clicks", _clicks) or []

        # ---- clicks at recording-dropout edges inside kept audio (a hard stop is not a seam, but it clicks)
        def _edge_clicks() -> list[SeamClick]:
            edges = dropout_edges_in_output(timeline, index)
            if not edges:
                return []
            return detect_seam_clicks(audio, SR, [t for t, _l, _r in edges], params=click_params,
                                      labels=[(lw, rw) for _t, lw, rw in edges])

        pk.edge_clicks = attempt("dropout edges", _edge_clicks) or []
        # ---- digital silence
        def _silence() -> list[SilenceRun]:
            runs = digital_silence_runs(audio[:, : timeline.sample_count], SR)  # past the end: AAC padding
            words = spoken_word_spans(timeline, index)
            out = classify_silence(runs, SR, words)
            mix = _mix_for(final, rd)
            if mix is not None:
                import soundfile as sf

                x, msr = sf.read(str(mix), dtype="float32", always_2d=True)
                if msr == SR:
                    out += classify_silence(mix_only_silence(runs, x.T, SR), SR, words, source="mix")
                else:
                    pk.errors.append(f"digital silence: {mix.name} is {msr} Hz, not {SR}")
            out.sort(key=lambda r: r.start_s)
            return out

        pk.digital_silence = attempt("digital silence", _silence) or []
    # ---- black / freeze
    if video_events if video_events is not None else not light:
        ev = attempt("black/freeze", lambda: detect_black_freeze(final))
        if ev is not None:
            pk.video_events = _classify_video_events(ev, timeline)
    # ---- A/V
    def _av() -> AvCheck:
        sync: list[SyncProbe] = []
        src = source_audio()
        if audio is not None and src is not None:
            sync = sync_probes(timeline, index, src, audio, SR)
        tail_db = None
        if audio is not None and audio.shape[1] > timeline.sample_count:
            tail_db = _db(float(np.max(np.abs(audio[:, timeline.sample_count:]))))
        return check_av(timeline, probe, None if audio is None else int(audio.shape[1]), mix_path=_mix_for(final, rd),
                        sync=sync, tail_peak_dbfs=tail_db)

    pk.av = attempt("a/v", _av)
    # ---- text placement
    if index is not None:
        pk.text_planned = attempt("planned text", lambda: planned_text_boxes(timeline, index, plat,
                                                                             settings=settings)) or []
    else:
        pk.text_planned = attempt("planned text", lambda: planned_text_boxes(timeline, None, plat,
                                                                             settings=settings)) or []
    ovl = rd / "overlays.mov"
    manifest = _manifest(rd)
    use_rendered = rendered_text if rendered_text is not None else not light
    if ovl.exists() and use_rendered:
        boxes = attempt("rendered text", lambda: rendered_text_boxes(timeline, index, ovl, plat, settings=settings))
        if boxes is not None:
            pk.text, pk.text_source = boxes, "rendered"
    if pk.text_source == "none":
        if manifest is not None and "overlays" in manifest and manifest["overlays"] in (None, "") \
                and not ovl.exists():
            pk.text_source = "none"  # nothing drawn over the picture
        elif timeline.captions or timeline.texts:
            pk.text, pk.text_source = pk.text_planned, "planned"
    # ---- edit structure
    if index is not None:
        pk.cuts = attempt("cuts", lambda: cut_checks(timeline, index, audio=audio)) or []
        pk.integrity = attempt("integrity", lambda: word_integrity(timeline, index)) or Integrity()
        pk.pacing = attempt("pacing", lambda: pacing_metrics(timeline, index, cuts=pk.cuts, doc=doc))
    # ---- ASR round trip
    run_asr = asr if asr is not None else (transcriber is not None or settings.has_key("elevenlabs")
                                           or settings.has_key("assemblyai"))
    if light and asr is None:
        run_asr = False
    if index is None:
        pk.asr = AsrRoundTrip(skipped_reason="no take index")
    elif not run_asr:
        pk.asr = AsrRoundTrip(skipped_reason="light measure" if light else "no ASR key configured")
    elif audio is None:
        pk.asr = AsrRoundTrip(skipped_reason="no decodable audio")
    else:
        pins = list(doc.pins.payoff_word_ids) + list(doc.pins.cta_word_ids) if doc is not None else []
        stems = rd / "stems"
        confirm = None  # the dialogue alone, when the mix also carries music/SFX/ambience that could mask
        others = any((stems / f"{k}.wav").exists() for k in ("music", "sfx", "ambience"))
        if (stems / "dialogue.wav").exists() and others:
            confirm = attempt("dialogue stem", lambda: _read_wav_mono(stems / "dialogue.wav"))
        pk.asr = attempt("asr", lambda: asr_round_trip(
            timeline, index, audio, transcriber=transcriber, settings=settings,
            work_dir=rd / "qa" / f"asr_{final.stem}",
            provider=asr_provider, cuts=pk.cuts, pinned=pins, confirm_audio=confirm)) or AsrRoundTrip(
            skipped_reason="asr failed")
    # ---- intelligibility
    stem = rd / "stems" / "dialogue.wav"
    if audio is not None and stem.exists() and (estoi if estoi is not None else not light):
        pk.intelligibility = attempt("estoi", lambda: intelligibility(_read_wav_mono(stem), audio, SR))
    # ---- banding
    if banding if banding is not None else not light:
        pk.banding = attempt("cambi", lambda: cambi(final, fps=to_fraction(timeline.fps)))
    pk.advice = _advice(pk, priors, doc, height=timeline.height)
    if save:
        with contextlib.suppress(OSError):
            pk.save(rd / "qa" / f"metrics_{final.stem}.json")
    return pk


def _mix_for(final: Path, rd: Path) -> Path | None:
    name = "mix_nomusic.wav" if final.stem == "final_nomusic" else "mix.wav"
    p = rd / name
    return p if p.exists() else None


def _manifest(rd: Path) -> dict[str, Any] | None:
    p = rd / "render.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def metrics_packet(job: Job, doc: CutDocument, index: TakeIndex, timeline: Timeline,
                   render_dir: Path | None = None, **kw: Any) -> dict[str, Any]:
    """JSON-safe metrics for a document version. With ``render_dir`` the render's primary final
    (``final_<first deliverable>.mp4``) is measured; without it only the timeline-level metrics (cuts,
    integrity, pacing, planned text) are returned."""
    if render_dir is not None:
        rd = Path(render_dir)
        plat = doc.deliverables[0].platform if doc.deliverables else "tiktok"
        final = rd / f"final_{plat}.mp4"
        if not final.exists():
            finals = sorted(rd.glob("final_*.mp4"))
            final = next((f for f in finals if f.stem != "final_nomusic"), finals[0] if finals else final)
        if final.exists():
            return measure(job, timeline, final, doc=doc, index=index, render_dir=rd, **kw).model_dump(mode="json")
    pk = MetricsPacket(created_at=_now_iso(), job_id=timeline.job_id, doc_version=timeline.doc_version,
                       platform=doc.deliverables[0].platform if doc.deliverables else "tiktok",
                       duration_s=round(_f(timeline.duration), 4))
    pk.cuts = cut_checks(timeline, index)
    pk.integrity = word_integrity(timeline, index)
    pk.pacing = pacing_metrics(timeline, index, cuts=pk.cuts, doc=doc)
    pk.text_planned = planned_text_boxes(timeline, index, pk.platform)
    pk.text, pk.text_source = pk.text_planned, "planned"
    pk.advice = _advice(pk, load_priors(), doc, height=timeline.height)
    return pk.model_dump(mode="json")
