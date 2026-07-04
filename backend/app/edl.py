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
    src_in: int          # TIMELINE window (when the cutaway appears), source-frame coords
    src_out: int
    cue_text: str        # the scripted cue; doubles as the Pexels query + human label
    asset_id: Optional[str] = None
    broll_query: Optional[str] = None
    source: str = "stock"           # stock (Pexels) | own_media
    resolved_url: Optional[str] = None   # filled by the backend Pexels-resolve step


class ReactSource(BaseModel):
    """The reacted-to clip for duet_split — the second video/image the creator responds to."""
    resolved_url: Optional[str] = None   # a direct, renderable video/image URL
    kind: str = "video"                  # video | image (screenshot)
    credit_label: str = ""               # e.g. "@originalcreator" for the attribution chip


class ReactWindow(BaseModel):
    """One entry in the top panel's play/freeze schedule (duet_split)."""
    state: str           # play | freeze
    src_in: int          # TIMELINE window start (when this state is active), source-frame coords
    src_out: int         # TIMELINE window end
    clip_from: int = 0   # which frame of the source to play-from / freeze-on
    audio_gain: float = 1.0   # source audio level in this window (play ~1.0, freeze ~0.15)


class Layout(BaseModel):
    style: str
    panels: int = 1
    panel_boundaries: list[int] = []  # frame boundaries for split_three
    split_fraction: float = 0.58      # duet_split: top (source) panel height fraction


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
    react_source: Optional[ReactSource] = None       # duet_split only
    react_schedule: list[ReactWindow] = []           # duet_split only
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


# ---------------------------------------------------------------------------
# Render plan — the editorial EDL is authored in SOURCE-video frame coordinates
# (AssemblyAI word timestamps). Once we actually CUT the footage (keep `segments`,
# remove `drops`), the output timeline is shorter, so a caption at source-frame 300
# might land at output-frame 150 if 150 frames were cut before it. This builds a
# render-ready plan the Remotion compositions consume directly:
#   - clips[]   : the kept intervals in SOURCE coords → OffthreadVideo trimBefore/trimAfter
#   - captions[]: remapped to OUTPUT coords (dropped if they fall inside a cut)
#   - overlays[]: remapped to OUTPUT coords (dropped if fully cut, clamped if straddling)
#   - total_frames: the output duration → the composition's durationInFrames
# Owning the remap here (one tested function) rather than in each of the 5 React
# compositions keeps them dumb renderers and the editorial EDL untouched for the API.
# ---------------------------------------------------------------------------

def _kept_intervals(segments: list[dict], drops: list[dict]) -> list[tuple[int, int]]:
    """Effective footage to keep = segments with drop ranges subtracted (source frames)."""
    drop_ranges = sorted((d["src_in"], d["src_out"]) for d in drops if d["src_out"] > d["src_in"])
    kept: list[tuple[int, int]] = []
    for seg in sorted(segments, key=lambda s: s["src_in"]):
        cur, end = seg["src_in"], seg["src_out"]
        for d_in, d_out in drop_ranges:
            if d_out <= cur or d_in >= end:
                continue
            if d_in > cur:
                kept.append((cur, min(d_in, end)))
            cur = max(cur, d_out)
            if cur >= end:
                break
        if cur < end:
            kept.append((cur, end))
    return [(a, b) for a, b in kept if b > a]


def build_render_plan(edl: dict) -> dict:
    """Transform an editorial EDL (source coords) into a render-ready plan (see above)."""
    clips_src = _kept_intervals(edl.get("segments") or [], edl.get("drops") or [])

    # cumulative output offset per kept interval
    clips: list[dict] = []
    index: list[tuple[int, int, int]] = []   # (src_in, src_out, out_start)
    out_cursor = 0
    for s_in, s_out in clips_src:
        clips.append({"src_in": s_in, "src_out": s_out})
        index.append((s_in, s_out, out_cursor))
        out_cursor += s_out - s_in
    total_frames = out_cursor

    def map_point(f: int) -> int | None:
        """Source frame → output frame, or None if f lands in a cut region."""
        for s_in, s_out, out_start in index:
            if s_in <= f < s_out:
                return out_start + (f - s_in)
        return None

    def map_range(a: int, b: int) -> tuple[int, int] | None:
        """Source [a,b) → output [in,out); None if no kept footage overlaps it."""
        spans = []
        for s_in, s_out, out_start in index:
            lo, hi = max(a, s_in), min(b, s_out)
            if lo < hi:
                spans.append((out_start + (lo - s_in), out_start + (hi - s_in)))
        if not spans:
            return None
        return min(s[0] for s in spans), max(s[1] for s in spans)

    captions = []
    for c in edl.get("captions") or []:
        of = map_point(c["frame"])
        if of is not None:
            captions.append({"word": c["word"], "frame": of})

    overlays = []
    for o in edl.get("overlays") or []:
        mapped = map_range(o["src_in"], o["src_out"])
        if mapped is not None:
            overlays.append({
                "type": o.get("type", "punch_in"),
                "frame_in": mapped[0], "frame_out": mapped[1],
                "scale": o.get("scale", 1.08), "text": o.get("text", ""),
            })

    broll = []
    for b in edl.get("broll") or []:
        mapped = map_range(b["src_in"], b["src_out"])
        if mapped is not None:
            broll.append({
                "frame_in": mapped[0], "frame_out": mapped[1],
                "cue_text": b.get("cue_text", ""),
                "asset_id": b.get("asset_id"), "broll_query": b.get("broll_query"),
                "source": b.get("source", "stock"),
                "resolved_url": b.get("resolved_url"),
            })

    # duet_split: remap the top-panel play/freeze schedule to output coords, carry the
    # source. Windows landing entirely inside a cut are dropped.
    react_schedule = []
    for w in edl.get("react_schedule") or []:
        mapped = map_range(w["src_in"], w["src_out"])
        if mapped is not None:
            react_schedule.append({
                "state": w.get("state", "play"),
                "frame_in": mapped[0], "frame_out": mapped[1],
                "clip_from": w.get("clip_from", 0),
                "audio_gain": w.get("audio_gain", 1.0),
            })

    return {
        "style": edl.get("style", "talking_head"),
        "format_id": edl.get("format_id", ""),
        "clips": clips,
        "captions": captions,
        "overlays": overlays,
        "broll": broll,
        "react_source": edl.get("react_source"),
        "react_schedule": react_schedule,
        "layout": edl.get("layout") or {"style": edl.get("style", "talking_head"), "panels": 1, "panel_boundaries": []},
        "caption_style": edl.get("caption_style", "clean"),
        # Remotion requires durationInFrames >= 1; an all-cut plan still needs a valid frame.
        "total_frames": max(1, total_frames),
    }
