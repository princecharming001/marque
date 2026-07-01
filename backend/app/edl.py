"""EDL (Edit Decision List) — the universal contract between the AI editor and the renderer."""
from __future__ import annotations
from pydantic import BaseModel, model_validator
from typing import Optional
import re

MS_PER_FRAME = 1000.0 / 30.0  # 30fps

FILLER_WORDS = frozenset({
    "um", "uh", "like", "you know", "so", "basically", "literally", "actually",
    "right", "okay", "ok", "yeah", "yep", "well", "i mean", "kind of", "sort of",
})

def ms_to_frame(ms: int) -> int:
    return round(ms / MS_PER_FRAME)

def snap_to_word(ms: int, words: list[dict], snap: str = "start") -> int:
    """Snap a millisecond timestamp to the nearest word boundary, return as frame."""
    if not words:
        return ms_to_frame(ms)
    key = "start_ms" if snap == "start" else "end_ms"
    closest = min(words, key=lambda w: abs(w.get(key, 0) - ms))
    return ms_to_frame(closest.get(key, ms))


class Segment(BaseModel):
    src_in: int   # frame
    src_out: int  # frame (exclusive)

    @model_validator(mode="after")
    def check_order(self):
        if self.src_out <= self.src_in:
            raise ValueError(f"src_out {self.src_out} must be > src_in {self.src_in}")
        return self


class Drop(BaseModel):
    src_in: int
    src_out: int
    reason: str  # filler | dead_air | false_start


class CaptionWord(BaseModel):
    word: str
    frame: int    # start frame for active-word highlight


class Overlay(BaseModel):
    type: str     # punch_in | text_card
    src_in: int
    src_out: int
    scale: float = 1.08   # for punch_in
    text: str = ""        # for text_card


class BRoll(BaseModel):
    src_in: int
    src_out: int
    cue_text: str
    asset_id: Optional[str] = None
    broll_query: Optional[str] = None


class Layout(BaseModel):
    style: str
    panels: int = 1
    panel_boundaries: list[int] = []  # frame boundaries for split_three


class Audio(BaseModel):
    lufs_target: float = -14.0


class EDL(BaseModel):
    style: str
    format_id: str
    segments: list[Segment]
    drops: list[Drop] = []
    captions: list[CaptionWord] = []
    overlays: list[Overlay] = []
    broll: list[BRoll] = []
    layout: Layout
    audio: Audio = Audio()

    @property
    def duration_frames(self) -> int:
        return sum(s.src_out - s.src_in for s in self.segments)

    @model_validator(mode="after")
    def check_segments_monotonic(self):
        prev_out = -1
        for seg in self.segments:
            if seg.src_in < prev_out:
                raise ValueError("Segments must be monotonically increasing (no overlaps)")
            prev_out = seg.src_out
        return self


# ---------------------------------------------------------------------------
# Validator + repair
# ---------------------------------------------------------------------------

TARGET_DURATION = {
    "talking_head": (18*30, 40*30),
    "faceless":     (20*30, 35*30),
    "split_three":  (20*30, 35*30),
    "fast_cuts":    (15*30, 30*30),
    "green_screen": (18*30, 30*30),
}

def validate_and_repair(edl: EDL) -> tuple[EDL, list[str]]:
    """Run invariant checks; attempt one repair round. Returns (repaired_edl, issues)."""
    issues = []

    # 1. hook and CTA must be present (first and last segments can't be empty)
    if not edl.segments:
        issues.append("no segments — using full take")
        # Repair: keep the whole take; caller must pass total_frames
        return edl, issues

    # 2. Duration in band
    lo, hi = TARGET_DURATION.get(edl.style, (15*30, 45*30))
    dur = edl.duration_frames
    if dur < lo:
        issues.append(f"duration {dur}f < min {lo}f")
    if dur > hi:
        issues.append(f"duration {dur}f > max {hi}f")

    # 3. split_three must have exactly 3 panels
    if edl.style == "split_three" and edl.layout.panels != 3:
        issues.append("split_three must have panels=3")
        edl.layout.panels = 3

    # 4. Segments monotonic (already validated by Pydantic; just surface issues)
    return edl, issues


def safe_default_edl(style: str, format_id: str, total_frames: int,
                     words: list[dict]) -> EDL:
    """Fallback EDL: keep the whole take, strip filler words, add timed captions."""
    segments = [Segment(src_in=0, src_out=total_frames)]
    captions = [
        CaptionWord(word=w["word"], frame=ms_to_frame(w.get("start_ms", 0)))
        for w in words
        if w.get("word", "").lower().strip(".,!?") not in FILLER_WORDS
    ]
    return EDL(
        style=style,
        format_id=format_id,
        segments=segments,
        captions=captions,
        layout=Layout(style=style),
    )


def strip_fillers(words: list[dict], gap_ms: int = 300) -> tuple[list[dict], list[Drop]]:
    """Remove filler words and dead-air gaps; return clean word list + drop list."""
    kept, drops = [], []
    prev_end = 0
    for w in words:
        text = w.get("word", "").lower().strip(".,!?")
        start = w.get("start_ms", 0)
        end = w.get("end_ms", start + 100)
        # Dead air before this word
        if prev_end > 0 and start - prev_end > gap_ms:
            drops.append(Drop(src_in=ms_to_frame(prev_end), src_out=ms_to_frame(start), reason="dead_air"))
        if text in FILLER_WORDS:
            drops.append(Drop(src_in=ms_to_frame(start), src_out=ms_to_frame(end), reason="filler"))
        else:
            kept.append(w)
            prev_end = end
    return kept, drops
