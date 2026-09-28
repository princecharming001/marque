"""Media description models (ARCHITECTURE §3).

:class:`MediaInfo` is produced by :func:`studio.media.ingest.ingest` (from ffprobe via
:mod:`studio.media.probe`) and persisted as ``media/media_info.json`` (see
:meth:`studio.jobs.Job.save_media_info`). It is embedded in the Take Index.

Geometry is **display** geometry: ``width``/``height`` are after applying ``rotation`` (a 1920x1080
coded iPhone portrait take with rotation 90 reports width=1080, height=1920). ``coded_width``/
``coded_height`` keep the stored dimensions.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.timebase import Rational, frame_count, us_to_seconds

__all__ = ["ColorInfo", "AudioInfo", "MediaInfo", "HDR_TRANSFERS"]

#: ffprobe ``color_transfer`` values that mean HDR.
HDR_TRANSFERS: dict[str, str] = {"arib-std-b67": "hlg", "smpte2084": "pq"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColorInfo(_Model):
    """Colour description of the (original) video stream, ffprobe vocabulary."""

    primaries: str | None = None  # e.g. "bt709", "bt2020"
    transfer: str | None = None  # e.g. "bt709", "arib-std-b67" (HLG), "smpte2084" (PQ)
    matrix: str | None = None  # e.g. "bt709", "bt2020nc"
    range: Literal["tv", "pc"] | None = None
    pix_fmt: str | None = None  # e.g. "yuv420p10le"
    bit_depth: int | None = None
    hdr: bool = False
    hdr_format: Literal["hlg", "pq", "dolby_vision"] | None = None
    dolby_vision: bool = False  # DV RPU side data present (profile 8.4 on iPhone)

    @classmethod
    def hdr_from_transfer(cls, transfer: str | None) -> tuple[bool, str | None]:
        """``(hdr, hdr_format)`` implied by an ffprobe transfer name."""
        fmt = HDR_TRANSFERS.get((transfer or "").lower())
        return (fmt is not None, fmt)


class AudioInfo(_Model):
    """The primary audio stream (AAC mapped explicitly; APAC/spatial streams are listed in notes)."""

    stream_index: int | None = None
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bit_rate: int | None = None
    duration_us: int | None = None
    start_us: int = 0  # stream start_time relative to container zero
    priming_samples: int | None = None  # AAC encoder delay if known


class MediaInfo(_Model):
    """Everything later stages need to know about a source file."""

    path: str  # absolute path of the original as ingested
    container: str | None = None  # ffprobe format_name
    width: int = Field(gt=0)  # display width (after rotation)
    height: int = Field(gt=0)  # display height (after rotation)
    coded_width: int | None = None
    coded_height: int | None = None
    rotation: int = 0  # display rotation in degrees, normalized to {0, 90, 180, 270}
    sar: Rational = Fraction(1)  # sample aspect ratio
    display_aspect: Rational | None = None  # width/height of the displayed picture, e.g. 9/16
    fps: Rational  # CFR output rate (normalized; the conform target for VFR sources)
    r_fps: Rational | None = None  # ffprobe r_frame_rate
    avg_fps: Rational | None = None  # ffprobe avg_frame_rate
    vfr: bool = False
    duration_us: int = Field(ge=0)
    start_us: int = 0  # container start_time
    nb_frames: int | None = None
    video_codec: str | None = None
    video_profile: str | None = None
    video_bit_rate: int | None = None
    color: ColorInfo = Field(default_factory=ColorInfo)
    audio: AudioInfo | None = None
    edit_list: bool = False  # MOV edit list present / honoured
    notes: list[str] = Field(default_factory=list)  # edit-list, priming, APAC, VFR … remarks

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def hdr(self) -> bool:
        return self.color.hdr

    @property
    def duration_s(self) -> Fraction:
        return us_to_seconds(self.duration_us)

    @property
    def aspect(self) -> Fraction:
        """Display aspect ratio width/height (falls back to width*sar/height)."""
        if self.display_aspect is not None:
            return self.display_aspect
        return Fraction(self.width) * self.sar / self.height

    @property
    def frame_count(self) -> int:
        """Frames at the CFR rate ``fps`` over ``duration_us``."""
        return frame_count(self.duration_s, self.fps)
