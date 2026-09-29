"""The Take Index: models, ID-based query API, persistence and the build orchestrator (ARCHITECTURE §4).

Everything downstream addresses media by **ID**, never by model-supplied time:

* words ``w0001`` … (source order = list order = time order),
* gaps ``g0001`` … (inter-word silences with a measured snap point),
* sentences ``s001`` …, retake clusters ``c01`` ….

Times are integer microseconds (``*_us``) on the source clock (see :mod:`studio.timebase`).

Query API (all on :class:`TakeIndex`, JSON-friendly, used by agent tools)::

    ix.get_words("w0010", "w0020")      # inclusive source range
    ix.get_sentences(); ix.get_clusters()
    ix.get_gaps(min_ms=250)
    ix.get_prosody(["w0012", "w0013"])  # [{id, text, f0_z, int_z, dur_z, emphasis}]
    ix.get_visual_events("blink")
    ix.render_transcript("full" | "compact")   # IDs inline, [g0012 0.62s] pauses, {um} fillers
    ix.word("w0012"); ix.gap("g0003"); ix.word_pos("w0012"); ix.gap_after("w0012")
    ix.face_at(t_us)                    # interpolated smoothed face box (normalized) or None

Persistence: :func:`save_index` / :func:`load_index` (``index/take_index.json``).
Orchestration: :func:`build_index` runs the perception modules in order.

Treat a built index as immutable. Lookup maps are cached and rebuilt automatically if the ``words``/
``gaps``/``sentences``/``clusters`` lists are replaced or change length; call :meth:`TakeIndex.reindex`
after in-place edits of IDs.
"""

from __future__ import annotations

import os
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from studio.media.models import MediaInfo
from studio.timebase import format_us

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job

__all__ = [
    "INDEX_VERSION",
    "WORD_ID_RE", "GAP_ID_RE", "SENTENCE_ID_RE", "CLUSTER_ID_RE",
    "word_id", "gap_id", "sentence_id", "cluster_id",
    "WordKind", "GapKind", "VisualEventKind",
    "CharTime", "Prosody", "Word", "Sentence", "Cluster", "Gap",
    "FaceBox", "VisualSample", "VisualEvent", "FaceTrackPoint", "FaceTrack", "HeadTop", "Visual",
    "AudioMetrics", "Energy", "AsrInfo", "AsrResult", "TakeIndex",
    "save_index", "load_index", "build_index",
]

INDEX_VERSION = 1

WORD_ID_RE = re.compile(r"^w\d{4,}$")
GAP_ID_RE = re.compile(r"^g\d{4,}$")
SENTENCE_ID_RE = re.compile(r"^s\d{3,}$")
CLUSTER_ID_RE = re.compile(r"^c\d{2,}$")

WORD_ID_PATTERN = WORD_ID_RE.pattern
GAP_ID_PATTERN = GAP_ID_RE.pattern
SENTENCE_ID_PATTERN = SENTENCE_ID_RE.pattern
CLUSTER_ID_PATTERN = CLUSTER_ID_RE.pattern


def word_id(n: int) -> str:
    """1-based word number → ``"w0001"``."""
    return f"w{n:04d}"


def gap_id(n: int) -> str:
    """1-based gap number → ``"g0001"``."""
    return f"g{n:04d}"


def sentence_id(n: int) -> str:
    """1-based sentence number → ``"s001"``."""
    return f"s{n:03d}"


def cluster_id(n: int) -> str:
    """1-based cluster number → ``"c01"``."""
    return f"c{n:02d}"


WordKind = Literal["word", "filler", "event", "cutoff"]
GapKind = Literal["pause", "breath", "silence", "noise"]
VisualEventKind = Literal["blink", "look_away", "face_lost", "reading"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------- words
class CharTime(_Model):
    """Character-level timing from ASR (Scribe ``timestamps_granularity=character``)."""

    text: str
    start_us: int
    end_us: int


class Prosody(_Model):
    """Per-word prosody z-scored against the creator's own baseline (None = not measurable)."""

    f0_z: float | None = None
    int_z: float | None = None
    dur_z: float | None = None


class Word(_Model):
    id: str = Field(pattern=WORD_ID_PATTERN)
    text: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    kind: WordKind = "word"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    speaker: str | None = None
    sentence_id: str | None = None
    cluster_id: str | None = None
    prosody: Prosody | None = None
    emphasis: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "main"  # source/take file id; ranges must stay within one source
    chars: list[CharTime] | None = None
    #: the recording itself cuts this word off (a digital-silence dropout): "end" = its end is lost ("meaningfu-"),
    #: "start" = its onset is lost ("-nough"), "both". Such words also have ``kind="cutoff"``.
    truncated: Literal["start", "end", "both"] | None = None

    @model_validator(mode="after")
    def _order(self) -> Word:
        if self.end_us < self.start_us:
            raise ValueError(f"{self.id}: end_us {self.end_us} < start_us {self.start_us}")
        return self

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    @property
    def is_spoken(self) -> bool:
        """True for lexical words, fillers and cut-offs (not audio events)."""
        return self.kind != "event"

    def display(self) -> str:
        """Transcript token: ``{um}`` for fillers, ``(laughter)`` for events, ``restr-`` for cut-offs."""
        t = self.text.strip()
        if self.kind == "filler":
            return "{" + (t.strip(".,!?;:") or t) + "}"
        if self.kind == "event":
            return t if (t.startswith("(") or t.startswith("[")) else f"({t})"
        if self.kind == "cutoff":
            if self.truncated == "start":
                return t if t.startswith(("-", "—", "–")) else f"-{t}"
            if self.truncated == "both":
                return f"-{t}-"
            return t if t.endswith(("-", "—", "–")) else f"{t}-"
        return t


class Sentence(_Model):
    id: str = Field(pattern=SENTENCE_ID_PATTERN)
    word_ids: list[str] = Field(min_length=1)
    text: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    complete: bool = True
    cluster_id: str | None = None
    speaker: str | None = None

    @property
    def first_word(self) -> str:
        return self.word_ids[0]

    @property
    def last_word(self) -> str:
        return self.word_ids[-1]


class Cluster(_Model):
    """Retakes/rephrasings of the same line, in time order."""

    id: str = Field(pattern=CLUSTER_ID_PATTERN)
    sentence_ids: list[str] = Field(min_length=1)
    similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_sentence_id: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _rec(self) -> Cluster:
        if self.recommended_sentence_id is not None and self.recommended_sentence_id not in self.sentence_ids:
            raise ValueError(f"{self.id}: recommended {self.recommended_sentence_id} not in cluster")
        return self


class Gap(_Model):
    """Silence between two words (or before the first / after the last: the missing side is None)."""

    id: str = Field(pattern=GAP_ID_PATTERN)
    after_word_id: str | None = None
    before_word_id: str | None = None
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    kind: GapKind = "pause"
    snap_us: int  # quietest point (RMS minimum inside the VAD non-speech region)
    energy_db: float | None = None
    has_breath: bool = False
    #: measured breath spans inside the gap (source µs, time order). Empty when none were measured (e.g. a
    #: hand-built index): consumers then treat the whole gap as the breath region.
    breaths_us: list[tuple[int, int]] = Field(default_factory=list)
    #: digital-silence runs inside the gap (a recording dropout, not room tone), source µs
    dropouts_us: list[tuple[int, int]] = Field(default_factory=list)
    #: non-word sound inside the gap (a fragment of a lost word beside a dropout, an isolated noise), source µs;
    #: cut pads never reach into it
    sound_us: list[tuple[int, int]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Gap:
        if self.end_us < self.start_us:
            raise ValueError(f"{self.id}: end_us < start_us")
        if not (self.start_us <= self.snap_us <= self.end_us):
            raise ValueError(f"{self.id}: snap_us {self.snap_us} outside [{self.start_us}, {self.end_us}]")
        if self.after_word_id is None and self.before_word_id is None:
            raise ValueError(f"{self.id}: gap must border at least one word")
        for a, b in self.breaths_us:
            if not (self.start_us <= a < b <= self.end_us):
                raise ValueError(f"{self.id}: breath span ({a}, {b}) outside [{self.start_us}, {self.end_us}]")
        for name, spans in (("dropout", self.dropouts_us), ("sound", self.sound_us)):
            for a, b in spans:
                if not (self.start_us <= a < b <= self.end_us):
                    raise ValueError(f"{self.id}: {name} span ({a}, {b}) outside [{self.start_us}, {self.end_us}]")
        return self

    @property
    def is_dropout(self) -> bool:
        """The gap holds a recording dropout (digital silence), not room tone."""
        return bool(self.dropouts_us)

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    @property
    def duration_ms(self) -> float:
        return self.duration_us / 1000.0

    @property
    def is_inner(self) -> bool:
        """Between two words (not leading/trailing)."""
        return self.after_word_id is not None and self.before_word_id is not None


# ---------------------------------------------------------------------------------------------- visual
class FaceBox(_Model):
    """Face box normalized to the upright display frame (0..1, top-left origin)."""

    x: float
    y: float
    w: float = Field(ge=0.0)
    h: float = Field(ge=0.0)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @classmethod
    def from_center(cls, cx: float, cy: float, w: float, h: float) -> FaceBox:
        return cls(x=cx - w / 2, y=cy - h / 2, w=w, h=h)


class VisualSample(_Model):
    """One visual measurement (≈10 fps from the mezzanine)."""

    t_us: int = Field(ge=0)
    face_box: FaceBox | None = None
    face_conf: float = Field(default=0.0, ge=0.0, le=1.0)
    eyes_open: float | None = None  # 0 closed .. 1 open
    gaze_off: float | None = None  # 0 at lens .. 1 clearly away
    mouth_open: float | None = None  # 0..1
    head_yaw: float | None = None  # degrees
    head_pitch: float | None = None  # degrees (negative = looking down)
    blur: float | None = None
    luma: float | None = None  # mean luma 0..1


class VisualEvent(_Model):
    kind: VisualEventKind
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str = ""

    @model_validator(mode="after")
    def _order(self) -> VisualEvent:
        if self.end_us < self.start_us:
            raise ValueError("visual event end_us < start_us")
        return self


class FaceTrackPoint(_Model):
    """Smoothed face centre/size at ``t_us`` (normalized display coords)."""

    t_us: int = Field(ge=0)
    cx: float
    cy: float
    w: float = Field(ge=0.0)
    h: float = Field(ge=0.0)
    conf: float = Field(default=1.0, ge=0.0, le=1.0)


class FaceTrack(_Model):
    points: list[FaceTrackPoint] = Field(default_factory=list)  # ascending t_us
    method: str = ""  # e.g. "one_euro" / "l1_path"
    params: dict[str, Any] = Field(default_factory=dict)

    def at(self, t_us: int) -> FaceBox | None:
        """Linear interpolation between neighbouring points; clamps outside the track; None if empty."""
        pts = self.points
        if not pts:
            return None
        if t_us <= pts[0].t_us:
            p = pts[0]
            return FaceBox.from_center(p.cx, p.cy, p.w, p.h)
        if t_us >= pts[-1].t_us:
            p = pts[-1]
            return FaceBox.from_center(p.cx, p.cy, p.w, p.h)
        times = [p.t_us for p in pts]
        i = bisect_right(times, t_us)
        a, b = pts[i - 1], pts[i]
        span = b.t_us - a.t_us
        f = 0.0 if span <= 0 else (t_us - a.t_us) / span
        lerp = lambda u, v: u + (v - u) * f  # noqa: E731
        return FaceBox.from_center(lerp(a.cx, b.cx), lerp(a.cy, b.cy), lerp(a.w, b.w), lerp(a.h, b.h))


class HeadTop(_Model):
    """One measured top of the head (hair included) at ``t_us``: ``y`` normalized to the upright frame,
    ``k`` = how far it sits above the landmark box top, in landmark-box heights."""

    t_us: int = Field(ge=0)
    y: float
    k: float
    touches_top: bool = False  # the head runs off the top of the frame (``k`` is then a lower bound)


class Visual(_Model):
    sample_fps: float = 10.0
    samples: list[VisualSample] = Field(default_factory=list)
    events: list[VisualEvent] = Field(default_factory=list)
    face_track: FaceTrack | None = None
    # The landmark box runs from the upper forehead to the chin; hair and crown sit above it by a
    # per-person amount. ``head_top_ratio`` is that height in landmark-box heights, measured over the take
    # (None: not measured, callers use a prior); ``head_tops`` are the raw measurements.
    head_top_ratio: float | None = None
    head_tops: list[HeadTop] = Field(default_factory=list)

    def head_top_y(self, box: FaceBox, prior: float = 0.55) -> float:
        """Top of the head (hair included), normalized y, for a landmark ``box``."""
        k = self.head_top_ratio if self.head_top_ratio is not None else prior
        return box.y - k * box.h


# ---------------------------------------------------------------------------------------------- audio / energy
class AudioMetrics(_Model):
    noise_floor_db: float | None = None
    snr_db: float | None = None
    clipping_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    lra_lu: float | None = None
    rt60_est: float | None = None
    music_in_room: bool = False
    room_tone_ranges_us: list[tuple[int, int]] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)  # c50, dnsmos, … (advice only)


class Energy(_Model):
    """Measured delivery energy (:func:`studio.perception.prosody.analyze_prosody`)."""

    wpm: float = 0.0  # words per minute of speaking time (pauses ≥ 1 s excluded)
    f0_var: float = 0.0  # robust SD of per-word pitch, semitones
    loudness_var: float = 0.0  # robust SD of per-word peak level, dB
    overall: float = Field(default=0.0, ge=0.0, le=1.0)  # 0.40 rate + 0.35 pitch + 0.25 loudness, 0..1


class AsrInfo(_Model):
    provider: str = "unknown"  # "elevenlabs" | "assemblyai" | "fixture" …
    model: str = "unknown"  # e.g. "scribe_v2"
    language: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    raw_path: str | None = None  # job-relative path of the raw response json


class AsrResult(_Model):
    """What :func:`studio.perception.transcribe.transcribe` returns: IDs assigned (``w0001``…) in time
    order; ``sentence_id``/``cluster_id``/``prosody`` still unset."""

    words: list[Word]
    asr: AsrInfo
    language: str | None = None


# ---------------------------------------------------------------------------------------------- index
class TakeIndex(_Model):
    version: int = INDEX_VERSION
    media: MediaInfo
    words: list[Word]
    sentences: list[Sentence] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    visual: Visual = Field(default_factory=Visual)
    audio: AudioMetrics | None = None
    energy: Energy = Field(default_factory=Energy)
    transcript_text: str = ""
    asr: AsrInfo = Field(default_factory=AsrInfo)

    _cache_key: tuple[Any, ...] | None = PrivateAttr(default=None)
    _words_by_id: dict[str, Word] = PrivateAttr(default_factory=dict)
    _word_pos: dict[str, int] = PrivateAttr(default_factory=dict)
    _gaps_by_id: dict[str, Gap] = PrivateAttr(default_factory=dict)
    _gap_after: dict[str, Gap] = PrivateAttr(default_factory=dict)
    _gap_before: dict[str, Gap] = PrivateAttr(default_factory=dict)
    _sentences_by_id: dict[str, Sentence] = PrivateAttr(default_factory=dict)
    _clusters_by_id: dict[str, Cluster] = PrivateAttr(default_factory=dict)
    _word_starts: list[int] = PrivateAttr(default_factory=list)

    # ------------------------------------------------------------------ validation
    @model_validator(mode="after")
    def _consistency(self) -> TakeIndex:
        ids: set[str] = set()
        prev_start = -1
        for w in self.words:
            if w.id in ids:
                raise ValueError(f"duplicate word id {w.id}")
            ids.add(w.id)
            if w.start_us < prev_start:
                raise ValueError(f"words not in time order at {w.id}")
            prev_start = w.start_us
        sids = set()
        for s in self.sentences:
            if s.id in sids:
                raise ValueError(f"duplicate sentence id {s.id}")
            sids.add(s.id)
            for wid in s.word_ids:
                if wid not in ids:
                    raise ValueError(f"sentence {s.id} references unknown word {wid}")
        cids = set()
        for c in self.clusters:
            if c.id in cids:
                raise ValueError(f"duplicate cluster id {c.id}")
            cids.add(c.id)
            for sid in c.sentence_ids:
                if sid not in sids:
                    raise ValueError(f"cluster {c.id} references unknown sentence {sid}")
        for w in self.words:
            if w.sentence_id is not None and w.sentence_id not in sids:
                raise ValueError(f"word {w.id} references unknown sentence {w.sentence_id}")
            if w.cluster_id is not None and w.cluster_id not in cids:
                raise ValueError(f"word {w.id} references unknown cluster {w.cluster_id}")
        for s in self.sentences:
            if s.cluster_id is not None and s.cluster_id not in cids:
                raise ValueError(f"sentence {s.id} references unknown cluster {s.cluster_id}")
        gids = set()
        for g in self.gaps:
            if g.id in gids:
                raise ValueError(f"duplicate gap id {g.id}")
            gids.add(g.id)
            for ref in (g.after_word_id, g.before_word_id):
                if ref is not None and ref not in ids:
                    raise ValueError(f"gap {g.id} references unknown word {ref}")
        return self

    def __eq__(self, other: object) -> bool:
        """Field equality (lookup caches are ignored, unlike Pydantic's default which compares them)."""
        if not isinstance(other, TakeIndex):
            return NotImplemented
        return self.__dict__ == other.__dict__

    __hash__ = None  # type: ignore[assignment]

    # ------------------------------------------------------------------ caches
    def reindex(self) -> None:
        """Rebuild lookup maps (automatic on most changes; call after in-place ID edits)."""
        self._words_by_id = {w.id: w for w in self.words}
        self._word_pos = {w.id: i for i, w in enumerate(self.words)}
        self._word_starts = [w.start_us for w in self.words]
        self._gaps_by_id = {g.id: g for g in self.gaps}
        self._gap_after = {g.after_word_id: g for g in self.gaps if g.after_word_id is not None}
        self._gap_before = {g.before_word_id: g for g in self.gaps if g.before_word_id is not None}
        self._sentences_by_id = {s.id: s for s in self.sentences}
        self._clusters_by_id = {c.id: c for c in self.clusters}
        self._cache_key = self._current_key()

    def _current_key(self) -> tuple[Any, ...]:
        return (id(self.words), len(self.words), id(self.gaps), len(self.gaps),
                id(self.sentences), len(self.sentences), id(self.clusters), len(self.clusters))

    def _ensure(self) -> None:
        if self._cache_key != self._current_key():
            self.reindex()

    # ------------------------------------------------------------------ lookups
    @property
    def word_map(self) -> dict[str, Word]:
        """``{word_id: Word}`` (do not mutate)."""
        self._ensure()
        return self._words_by_id

    @property
    def gap_map(self) -> dict[str, Gap]:
        """``{gap_id: Gap}`` (do not mutate)."""
        self._ensure()
        return self._gaps_by_id

    @property
    def sentence_map(self) -> dict[str, Sentence]:
        self._ensure()
        return self._sentences_by_id

    @property
    def cluster_map(self) -> dict[str, Cluster]:
        self._ensure()
        return self._clusters_by_id

    def has_word(self, wid: str) -> bool:
        return wid in self.word_map

    def word(self, wid: str) -> Word:
        try:
            return self.word_map[wid]
        except KeyError:
            raise KeyError(f"unknown word id {wid!r}") from None

    def word_pos(self, wid: str) -> int:
        """0-based source position of a word (list order = time order)."""
        self._ensure()
        try:
            return self._word_pos[wid]
        except KeyError:
            raise KeyError(f"unknown word id {wid!r}") from None

    def gap(self, gid: str) -> Gap:
        try:
            return self.gap_map[gid]
        except KeyError:
            raise KeyError(f"unknown gap id {gid!r}") from None

    def sentence(self, sid: str) -> Sentence:
        try:
            return self.sentence_map[sid]
        except KeyError:
            raise KeyError(f"unknown sentence id {sid!r}") from None

    def cluster(self, cid: str) -> Cluster:
        try:
            return self.cluster_map[cid]
        except KeyError:
            raise KeyError(f"unknown cluster id {cid!r}") from None

    def gap_after(self, wid: str) -> Gap | None:
        """The gap following word ``wid`` (None when words abut)."""
        self._ensure()
        return self._gap_after.get(wid)

    def gap_before(self, wid: str) -> Gap | None:
        """The gap preceding word ``wid`` (None when words abut)."""
        self._ensure()
        return self._gap_before.get(wid)

    def next_word(self, wid: str) -> Word | None:
        i = self.word_pos(wid)
        return self.words[i + 1] if i + 1 < len(self.words) else None

    def prev_word(self, wid: str) -> Word | None:
        i = self.word_pos(wid)
        return self.words[i - 1] if i > 0 else None

    # ------------------------------------------------------------------ query API
    def get_words(self, from_id: str | None = None, to_id: str | None = None) -> list[Word]:
        """Words from ``from_id`` to ``to_id`` inclusive in source order (defaults: first / last).

        Raises ``KeyError`` for unknown IDs and ``ValueError`` if ``from_id`` comes after ``to_id``.
        """
        if not self.words:
            return []
        a = 0 if from_id is None else self.word_pos(from_id)
        b = len(self.words) - 1 if to_id is None else self.word_pos(to_id)
        if a > b:
            raise ValueError(f"range reversed: {from_id} comes after {to_id}")
        return self.words[a:b + 1]

    def word_ids(self, from_id: str | None = None, to_id: str | None = None) -> list[str]:
        return [w.id for w in self.get_words(from_id, to_id)]

    def get_sentences(self) -> list[Sentence]:
        return list(self.sentences)

    def get_clusters(self) -> list[Cluster]:
        return list(self.clusters)

    def get_gaps(self, min_ms: float = 0.0) -> list[Gap]:
        """All gaps at least ``min_ms`` long, in time order."""
        lim = min_ms * 1000.0
        return [g for g in self.gaps if g.duration_us >= lim]

    def gaps_between(self, from_id: str, to_id: str) -> list[Gap]:
        """Inner gaps whose both neighbours lie in the inclusive source range ``from_id..to_id``."""
        a, b = self.word_pos(from_id), self.word_pos(to_id)
        if a > b:
            raise ValueError(f"range reversed: {from_id} comes after {to_id}")
        pos = self._word_pos
        return [g for g in self.gaps if g.is_inner
                and a <= pos[g.after_word_id] and pos[g.before_word_id] <= b]  # type: ignore[index]

    def get_prosody(self, word_ids: Iterable[str]) -> list[dict[str, Any]]:
        """``[{id, text, kind, f0_z, int_z, dur_z, emphasis}]`` for the given words (unknown IDs raise)."""
        out = []
        for wid in word_ids:
            w = self.word(wid)
            p = w.prosody or Prosody()
            out.append({"id": w.id, "text": w.text, "kind": w.kind, "f0_z": p.f0_z, "int_z": p.int_z,
                        "dur_z": p.dur_z, "emphasis": w.emphasis})
        return out

    def get_visual_events(self, kind: str | None = None, *, from_word: str | None = None,
                          to_word: str | None = None) -> list[VisualEvent]:
        """Visual events, optionally of one ``kind`` and overlapping a word range's time span."""
        evs = [e for e in self.visual.events if kind is None or e.kind == kind]
        if from_word is not None or to_word is not None:
            ws = self.get_words(from_word, to_word)
            if not ws:
                return []
            lo, hi = ws[0].start_us, ws[-1].end_us
            evs = [e for e in evs if e.end_us >= lo and e.start_us <= hi]
        return evs

    def words_between_us(self, start_us: int, end_us: int) -> list[Word]:
        """Words overlapping the half-open source interval ``[start_us, end_us)`` (maps measured
        spans such as visual events onto word IDs)."""
        self._ensure()
        hi = bisect_left(self._word_starts, end_us)
        return [w for w in self.words[:hi] if w.end_us > start_us]

    def word_at(self, t_us: int) -> Word | None:
        """The word whose span contains ``t_us`` (None inside gaps)."""
        self._ensure()
        i = bisect_right(self._word_starts, t_us) - 1
        if i >= 0 and self.words[i].start_us <= t_us < max(self.words[i].end_us, self.words[i].start_us + 1):
            return self.words[i]
        return None

    def sentence_of(self, wid: str) -> Sentence | None:
        sid = self.word(wid).sentence_id
        return self.sentence_map.get(sid) if sid else None

    def face_at(self, t_us: int) -> FaceBox | None:
        """Face box at ``t_us``: interpolated smoothed track, else nearest raw sample with a face."""
        if self.visual.face_track is not None and self.visual.face_track.points:
            return self.visual.face_track.at(t_us)
        faced = [s for s in self.visual.samples if s.face_box is not None]
        if not faced:
            return None
        best = min(faced, key=lambda s: abs(s.t_us - t_us))
        return best.face_box

    @property
    def duration_us(self) -> int:
        return self.media.duration_us

    # ------------------------------------------------------------------ transcript rendering
    def render_transcript(
        self,
        view: Literal["full", "compact"] = "full",
        *,
        word_ids: Iterable[str] | None = None,
        min_gap_ms: float | None = None,
    ) -> str:
        """Readable transcript for models.

        ``full``: one line per sentence, a header ``s003 0:04.10-0:05.90 [c01 take 2/2, recommended]``
        followed by every word as ``w0014 The``; fillers ``{um}``, events ``(laughter)``, cut-offs
        ``restr-``; gaps inline as ``[g0012 0.62s]`` (kind appended unless ``pause``).

        ``compact``: one line per sentence ``s003 (w0014-w0018) [c01 2/2 rec] The real secret is
        restraint. [g0004 0.80s]`` — word IDs only as ranges; gaps below ``min_gap_ms`` (default 250)
        omitted.

        ``word_ids`` restricts output to those words (e.g. the kept words of a cut); a gap is shown only
        when it lies between two included words (leading/trailing silences are omitted).
        ``min_gap_ms`` defaults to 0 (full) / 250 (compact).
        """
        if view not in ("full", "compact"):
            raise ValueError(f"unknown view {view!r}")
        self._ensure()
        include = None if word_ids is None else set(word_ids)
        if include is not None:
            unknown = [w for w in include if w not in self._words_by_id]
            if unknown:
                raise KeyError(f"unknown word ids: {sorted(unknown)[:5]}")
        gap_min = (0.0 if view == "full" else 250.0) if min_gap_ms is None else float(min_gap_ms)

        def inc(wid: str | None) -> bool:
            return wid is not None and (include is None or wid in include)

        def gap_token(g: Gap) -> str | None:
            if g.duration_ms < gap_min:
                return None
            if include is not None and not g.is_inner:
                return None  # leading/trailing silence is not part of a filtered (cut) view
            neighbours = [x for x in (g.after_word_id, g.before_word_id) if x is not None]
            if not all(inc(x) for x in neighbours):
                return None
            suffix = "" if g.kind == "pause" else f" {g.kind}"
            if g.dropouts_us:
                lost = sum(b - a for a, b in g.dropouts_us) / 1e6
                suffix += f" DROPOUT {lost:.2f}s lost"
            if g.sound_us and g.kind != "noise":
                suffix += f" +{sum(b - a for a, b in g.sound_us) / 1e6:.2f}s untranscribed sound"
            return f"[{g.id} {g.duration_us / 1e6:.2f}s{suffix}]"

        # group words by sentence (words without a sentence form their own pseudo-groups)
        groups: list[tuple[Sentence | None, list[Word]]] = []
        for w in self.words:
            s = self._sentences_by_id.get(w.sentence_id) if w.sentence_id else None
            if groups and groups[-1][0] is s and (s is not None or groups[-1][1][-1].sentence_id is None):
                groups[-1][1].append(w)
            else:
                groups.append((s, [w]))

        lines: list[str] = []
        leading = [g for g in self.gaps if g.after_word_id is None]
        for g in leading:
            tok = gap_token(g)
            if tok:
                lines.append(tok)
        for s, ws in groups:
            shown = [w for w in ws if inc(w.id)]
            if not shown:
                continue
            tokens: list[str] = []
            for w in shown:
                tokens.append(f"{w.id} {w.display()}" if view == "full" else w.display())
                g = self._gap_after.get(w.id)
                if g is not None:
                    tok = gap_token(g)
                    if tok:
                        tokens.append(tok)
            header = self._sentence_header(s, shown, view)
            if view == "full":
                lines.append(header)
                lines.append(" ".join(tokens))
            else:
                lines.append(f"{header} {' '.join(tokens)}")
        return "\n".join(lines)

    def _sentence_header(self, s: Sentence | None, shown: Sequence[Word], view: str) -> str:
        tags: list[str] = []
        if s is not None and s.cluster_id and s.cluster_id in self._clusters_by_id:
            c = self._clusters_by_id[s.cluster_id]
            k = c.sentence_ids.index(s.id) + 1 if s.id in c.sentence_ids else 0
            rec = s.id == c.recommended_sentence_id
            if view == "full":
                tags.append(f"{c.id} take {k}/{len(c.sentence_ids)}" + (", recommended" if rec else ""))
            else:
                tags.append(f"{c.id} {k}/{len(c.sentence_ids)}" + (" rec" if rec else ""))
        if s is not None and not s.complete:
            tags.append("incomplete")
        tag = f" [{', '.join(tags)}]" if tags else ""
        sid = s.id if s is not None else "s---"
        if view == "full":
            return f"{sid} {format_us(shown[0].start_us)}-{format_us(shown[-1].end_us)}{tag}"
        rng = shown[0].id if len(shown) == 1 else f"{shown[0].id}-{shown[-1].id}"
        return f"{sid} ({rng}){tag}"

    # ------------------------------------------------------------------ misc
    def plain_text(self, *, include_fillers: bool = True) -> str:
        """Space-joined word texts (events skipped)."""
        return " ".join(w.text for w in self.words
                        if w.kind != "event" and (include_fillers or w.kind != "filler"))


# ---------------------------------------------------------------------------------------------- persistence
def _index_path(target: Job | str | os.PathLike[str]) -> Path:
    from studio.jobs import Job

    if isinstance(target, Job):
        return target.index_path
    p = Path(target)
    return p / "index" / "take_index.json" if p.is_dir() else p


def save_index(index: TakeIndex, target: Job | str | os.PathLike[str]) -> Path:
    """Write the index to ``<job>/index/take_index.json`` (or to an explicit file path)."""
    from studio.jobs import write_json_atomic

    return write_json_atomic(_index_path(target), index)


def load_index(target: Job | str | os.PathLike[str]) -> TakeIndex:
    """Load an index from a job, a job directory or a json file path."""
    from studio.jobs import read_json

    return TakeIndex.model_validate(read_json(_index_path(target)))


# ---------------------------------------------------------------------------------------------- orchestration
def build_index(
    job: Job,
    *,
    asr_provider: str | None = None,
    keyterms: Sequence[str] | None = None,
    settings: Settings | None = None,
    save: bool = True,
) -> TakeIndex:
    """Run perception in order and return (and by default save) the :class:`TakeIndex`.

    Order: media info (from ingest) → ASR → acoustic boundary refinement → gaps → sentences →
    retake clusters → prosody/energy → audio metrics → visual. Each step lives in its own module; this
    function only wires them together.
    """
    from studio.perception import (
        audio_metrics,
        fill,
        gaps,
        prosody,
        takes,
        transcribe,
        visual,
    )

    media = job.load_media_info()
    job.trace("stage", stage="index", step="transcribe")
    asr = transcribe.transcribe(job, provider=asr_provider, keyterms=keyterms, settings=settings)
    words = gaps.refine_word_boundaries(job, asr.words)
    gap_list = gaps.detect_gaps(job, words)
    # speech the ASR left out (a dropped verbatim restart) gets a second pass on its own; what stays untranscribed is
    # marked on its gap (see studio.perception.fill)
    try:
        extra, fill_report = fill.fill_untranscribed(job, words, gap_list, provider=asr_provider, settings=settings)
        if extra:
            merged = sorted([*asr.words, *extra], key=lambda w: (w.start_us, w.end_us))
            merged = [w.model_copy(update={"id": word_id(k + 1)}) for k, w in enumerate(merged)]
            words = gaps.refine_word_boundaries(job, merged)
            gap_list = gaps.detect_gaps(job, words)
        left = fill.untranscribed_speech(job, words, gap_list)
        if left:
            gap_list = fill.mark_untranscribed(gap_list, left)
        if fill_report or left:
            job.trace("stage", stage="index", step="fill_untranscribed", regions=fill_report[:20],
                      added=len(extra), still_untranscribed=[(r.gap_id, r.start_us, r.end_us) for r in left][:20])
    except Exception as e:  # an optional pass: the first transcript stands
        job.trace("stage", stage="index", step="fill_untranscribed", error=f"{type(e).__name__}: {str(e)[:300]}")
    words, sentences = takes.segment_sentences(words, dropouts=[d for g in gap_list for d in g.dropouts_us])
    words, sentences, clusters = takes.cluster_takes(words, sentences, settings=settings)
    words, energy = prosody.analyze_prosody(job, words, sentences)
    audio = audio_metrics.measure_audio(job, gaps=gap_list, words=words) if media.has_audio else None
    vis = visual.analyze_visual(job)
    index = TakeIndex(
        media=media,
        words=words,
        sentences=sentences,
        clusters=clusters,
        gaps=gap_list,
        visual=vis,
        audio=audio,
        energy=energy,
        transcript_text=" ".join(s.text for s in sentences) if sentences else " ".join(w.text for w in words),
        asr=asr.asr,
    )
    if save:
        save_index(index, job)
    job.trace("stage", stage="index", step="done", words=len(words), gaps=len(gap_list),
              sentences=len(sentences), clusters=len(clusters))
    return index
