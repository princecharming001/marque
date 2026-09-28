"""Timeline models: the compiled, output-time view of a CutDocument (ARCHITECTURE §6).

Produced by :func:`studio.compile.timeline.compile` and consumed by every render stage and QA check.

Time conventions
----------------
* Output times (``out_*``, ``duration``, ``seams``) are exact :class:`fractions.Fraction` seconds that
  lie on the output frame grid (``n / fps``). They serialize to JSON as ``"num/den"`` strings.
* Source times (``src_*_us``, ``audio_src_*_us``) are integer microseconds on the source clock.
* Audio sample positions must be derived from the same Fractions via :mod:`studio.timebase`
  (``sample_index(out_t)``) so audio and video can never drift.

A segment plays source ``[src_in_us, src_out_us)`` at ``speed`` into output ``[out_start, out_end)``,
so ``(src_out_us - src_in_us) / 1e6 / speed == out_end - out_start`` up to frame snapping. J/L cuts
shift the audio window: ``audio_src_in_us`` < ``src_in_us`` means the audio leads (J cut);
``audio_src_out_us`` > ``src_out_us`` means it lags (L cut). Framing keyframes are in output time
with the crop centre ``(cx, cy)`` normalized to the upright source frame.
"""

from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.doc.model import (
    AssetRef,
    CaptionStyle,
    CardSpec,
    SeamTreatment,
    TextStyle,
    Transition,
)
from studio.timebase import (
    SAMPLE_RATE,
    Rational,
    frame_count,
    frame_index,
    normalize_fps,
    round_fraction,
    sample_index,
    to_fraction,
)

__all__ = [
    "FramingKey", "TimelineSegment", "TimelineInsert", "TimelineCaptionWord", "TimelineCaptionPage",
    "TimelineText", "TimelineSfx", "TimelineMusic", "WordSpan", "Timeline",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FramingKey(_Model):
    """Crop keyframe: at output time ``out_t`` the crop is ``scale``x the base 9:16 fill, centred at
    ``(cx, cy)`` (normalized source coords). Interpolation between keys uses ``ease``."""

    out_t: Rational
    scale: float = Field(default=1.0, ge=1.0, le=1.8)
    cx: float = 0.5
    cy: float = 0.5
    ease: Literal["hold", "linear", "ease_in_out"] = "hold"


class TimelineSegment(_Model):
    seg_id: str
    src_in_us: int = Field(ge=0)
    src_out_us: int = Field(ge=0)
    out_start: Rational
    out_end: Rational
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    audio_src_in_us: int = Field(ge=0)
    audio_src_out_us: int = Field(ge=0)
    framing: list[FramingKey] = Field(default_factory=list)
    seam_in: SeamTreatment = Field(default_factory=SeamTreatment)
    word_ids: list[str] = Field(default_factory=list)
    source: str = "main"

    @model_validator(mode="after")
    def _check(self) -> TimelineSegment:
        if self.src_out_us <= self.src_in_us:
            raise ValueError(f"{self.seg_id}: empty source range")
        if self.out_end <= self.out_start:
            raise ValueError(f"{self.seg_id}: empty output range")
        if self.audio_src_out_us <= self.audio_src_in_us:
            raise ValueError(f"{self.seg_id}: empty audio range")
        return self

    @property
    def out_duration(self) -> Fraction:
        return self.out_end - self.out_start

    @property
    def src_duration_us(self) -> int:
        return self.src_out_us - self.src_in_us

    @property
    def audio_lead_us(self) -> int:
        """> 0 when the audio starts before the picture (J cut)."""
        return self.src_in_us - self.audio_src_in_us

    @property
    def audio_lag_us(self) -> int:
        """> 0 when the audio continues after the picture (L cut)."""
        return self.audio_src_out_us - self.src_out_us

    def out_to_src_us(self, out_t: Fraction) -> int:
        """Map an output time inside this segment to source microseconds (speed-aware, exact, half-up)."""
        return self.src_in_us + round_fraction((to_fraction(out_t) - self.out_start) * to_fraction(self.speed)
                                               * 1_000_000)


class TimelineInsert(_Model):
    insert_id: str
    mode: Literal["full", "split_top", "split_bottom", "pip", "card"]
    out_start: Rational
    out_end: Rational
    asset: Annotated[AssetRef | CardSpec, Field(discriminator="type")]
    asset_path: str | None = None  # absolute path of the conformed asset, if any
    asset_in_us: int = 0  # start inside the asset
    audio: Literal["voice_only", "duck", "with_sfx"] = "voice_only"
    transition_in: Transition = Field(default_factory=Transition)
    transition_out: Transition = Field(default_factory=Transition)
    rect: tuple[float, float, float, float] | None = None  # x, y, w, h normalized output (split/pip)
    z: int = 0

    @property
    def out_duration(self) -> Fraction:
        return self.out_end - self.out_start


class TimelineCaptionWord(_Model):
    word_id: str
    text: str
    out_start: Rational
    out_end: Rational
    emphasis: bool = False


class TimelineCaptionPage(_Model):
    page_id: str | None = None
    words: list[TimelineCaptionWord] = Field(min_length=1)
    text: str | None = None  # display override
    out_start: Rational
    out_end: Rational
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    y_norm: float = Field(default=0.7, ge=0.0, le=1.0)  # vertical centre of the caption block


class TimelineText(_Model):
    text_id: str
    kind: str
    text: str
    items: list[str] = Field(default_factory=list)
    out_start: Rational
    out_end: Rational
    x_norm: float = 0.5
    y_norm: float = 0.2
    style: TextStyle = Field(default_factory=TextStyle)
    animation: str = "pop"


class TimelineSfx(_Model):
    sfx_id: str
    kind: str
    out_t: Rational
    asset: AssetRef | None = None
    asset_path: str | None = None
    gain_db: float = -12.0


class TimelineMusic(_Model):
    asset: AssetRef | None = None
    asset_path: str | None = None
    out_start: Rational
    out_end: Rational
    asset_in_us: int = 0
    level_lu_under_speech: float = -18.0
    duck: bool = True
    duck_db: float = 6.0
    fade_in_ms: int = 500
    fade_out_ms: int = 1500
    hits: list[Rational] = Field(default_factory=list)


class WordSpan(_Model):
    out_start: Rational
    out_end: Rational


class Timeline(_Model):
    """Compiled timeline for one document version."""

    fps: Rational
    width: int = 1080
    height: int = 1920
    duration: Rational
    sample_rate: int = SAMPLE_RATE
    doc_version: int | None = None
    job_id: str = ""
    source_path: str | None = None  # the mezzanine the A-roll reads
    segments: list[TimelineSegment] = Field(default_factory=list)
    inserts: list[TimelineInsert] = Field(default_factory=list)
    captions: list[TimelineCaptionPage] = Field(default_factory=list)
    texts: list[TimelineText] = Field(default_factory=list)
    sfx: list[TimelineSfx] = Field(default_factory=list)
    music: TimelineMusic | None = None
    word_map: dict[str, WordSpan | None] = Field(default_factory=dict)
    seams: list[Rational] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Timeline:
        self.fps = normalize_fps(self.fps)
        if self.duration < 0:
            raise ValueError("negative duration")
        prev_end = Fraction(0)
        for s in self.segments:
            if s.out_start < prev_end:
                raise ValueError(f"segment {s.seg_id} overlaps the previous one in output time")
            prev_end = s.out_end
        bad = [k for k in self.grid_violations() if k.startswith(("segment", "duration", "seam"))]
        if bad:
            raise ValueError(f"times off the {self.fps} fps frame grid: {', '.join(bad[:5])}")
        return self

    def on_grid(self, t: Fraction) -> bool:
        """True when ``t`` is exactly a frame boundary ``n / fps``."""
        return (Fraction(t) * self.fps).denominator == 1

    def grid_violations(self) -> list[str]:
        """Labels of output times not on the frame grid. Segments, seams and the duration must be on it
        (enforced); inserts, captions and texts should be (reported here for QA); SFX are
        sample-accurate and exempt."""
        out: list[str] = []
        if not self.on_grid(self.duration):
            out.append("duration")
        for s in self.segments:
            for name in ("out_start", "out_end"):
                if not self.on_grid(getattr(s, name)):
                    out.append(f"segment {s.seg_id}.{name}")
        for i, t in enumerate(self.seams):
            if not self.on_grid(t):
                out.append(f"seam #{i}")
        for x in self.inserts:
            for name in ("out_start", "out_end"):
                if not self.on_grid(getattr(x, name)):
                    out.append(f"insert {x.insert_id}.{name}")
        for k, p in enumerate(self.captions):
            if not (self.on_grid(p.out_start) and self.on_grid(p.out_end)):
                out.append(f"caption page {p.page_id or k}")
        for t in self.texts:
            if not (self.on_grid(t.out_start) and self.on_grid(t.out_end)):
                out.append(f"text {t.text_id}")
        return out

    # ------------------------------------------------------------------ frames/samples
    @property
    def frame_count(self) -> int:
        return frame_count(self.duration, self.fps)

    @property
    def sample_count(self) -> int:
        return sample_index(self.duration, self.sample_rate)

    def frame_of(self, out_t: Fraction) -> int:
        return frame_index(out_t, self.fps)

    def sample_of(self, out_t: Fraction) -> int:
        return sample_index(out_t, self.sample_rate)

    def segment_at(self, out_t: Fraction) -> TimelineSegment | None:
        t = Fraction(out_t)
        for s in self.segments:
            if s.out_start <= t < s.out_end:
                return s
        return None

    def word_span(self, word_id: str) -> WordSpan | None:
        return self.word_map.get(word_id)

    # ------------------------------------------------------------------ io
    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes) -> Timeline:
        return cls.model_validate_json(data)

    def save(self, path: str | os.PathLike[str]) -> Path:
        from studio.jobs import write_json_atomic

        return write_json_atomic(Path(path), self)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Timeline:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
