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
    # Playback rate for this clip (CapCut "Speed → Normal"). Output duration =
    # kept_frames / speed; every source→output remap divides by it. 1.0 = untouched.
    speed: float = 1.0
    # Canvas transform (CapCut: pinch the preview to zoom the clip, drag to
    # reposition). scale is a zoom factor; off_x/off_y are offsets as fractions
    # of frame size. Identity (1, 0, 0) = untouched.
    tx_scale: float = 1.0
    tx_x: float = 0.0
    tx_y: float = 0.0

    @model_validator(mode="after")
    def check_order(self):
        if self.src_out <= self.src_in:
            raise ValueError(f"src_out {self.src_out} must be > src_in {self.src_in}")
        self.speed = min(3.0, max(0.5, self.speed or 1.0))
        self.tx_scale = min(3.0, max(0.5, self.tx_scale or 1.0))
        self.tx_x = min(0.5, max(-0.5, self.tx_x or 0.0))
        self.tx_y = min(0.5, max(-0.5, self.tx_y or 0.0))
        return self


class Drop(BaseModel):
    src_in: int
    src_out: int
    reason: str  # filler | dead_air | false_start


class CaptionWord(BaseModel):
    word: str
    frame: int    # start frame for active-word highlight


class Overlay(BaseModel):
    type: str     # punch_in | text_card | text_sticker
    src_in: int
    src_out: int
    scale: float = 1.08   # punch_in zoom factor; text_sticker size multiplier
    text: str = ""        # text_card / text_sticker content
    # text_sticker placement + look (TikTok text tool: drag anywhere, pinch, rotate).
    # Fractions of frame size for position; defaults center. Ignored by other types.
    pos_x: float = 0.5
    pos_y: float = 0.5
    rotation: float = 0.0            # degrees
    color: Optional[str] = None      # #RRGGBB; None = white
    bg: str = "none"                 # none | box (dark label plate behind the text)
    font: str = "inter"              # inter | archivo | baloo


class Transition(BaseModel):
    """A boundary treatment where one clip hands off to the next (CapCut drag-a-
    transition-between-clips). Anchored to the SOURCE segment whose end it follows,
    so trims keep it attached; deleting that segment drops it. v1 styles need no
    video overlap (they composite a color/flash dip over the boundary)."""
    after_segment: int               # source index of the leading segment
    style: str = "fade_black"        # fade_black | fade_white | flash
    frames: int = 12                 # total dip duration, centered on the boundary


class Adjust(BaseModel):
    """Manual color knobs (CapCut Adjust), each -0.5..0.5 except vignette 0..1.
    Rendered as a CSS filter chain + vignette overlay; 0 = untouched."""
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    temperature: float = 0.0         # + warm / - cool
    vignette: float = 0.0


class Look(BaseModel):
    """Whole-video color treatment: a named filter preset blended by intensity,
    composed with manual Adjust knobs (preset first, knobs on top)."""
    filter: Optional[str] = None     # vivid | film | mono | golden | warm | cool
    intensity: float = 1.0           # 0..1 preset strength
    adjust: Adjust = Adjust()


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


class MusicTrack(BaseModel):
    """Background music. `url` is a direct audio URL (the app ships a small bundled
    catalog); `query` stores intent for future server-side resolution."""
    url: Optional[str] = None
    query: Optional[str] = None
    volume: float = 0.15          # clamped 0.0–1.0 on apply
    duck_voice: bool = True       # duck music under speech (caption-activity proxy)


class VolumeRange(BaseModel):
    """Per-range source-audio volume (0.0 = mute), source-frame coords."""
    src_in: int
    src_out: int
    volume: float                 # clamped 0.0–2.0 on apply

    @model_validator(mode="after")
    def check_order(self):
        if self.src_out <= self.src_in:
            raise ValueError(f"src_out {self.src_out} must be > src_in {self.src_in}")
        return self


class Audio(BaseModel):
    # G4 (deliberately deferred, not a bug): -14 LUFS matches TikTok/YouTube's
    # published loudness targets, but nothing in the pipeline actually MEASURES
    # or normalizes to it yet — real loudness normalization needs either an
    # ffmpeg loudnorm two-pass (analyze then apply gain) in the render bridge,
    # or an equivalent Lambda-side audio analysis step; neither exists today.
    # The field is captured end-to-end (Pydantic → render plan → AudioPlan in
    # types.ts) so the contract is ready for that work, but it currently has NO
    # effect on the mix. Documented + pinned rather than silently ignored.
    lufs_target: float = -14.0
    music: Optional[MusicTrack] = None
    volume_ranges: list[VolumeRange] = []


class CaptionOptions(BaseModel):
    """Rendering knobs for burned-in captions, composable UNDER caption_style (the
    preset picks the look; these tune it). All defaulted so old EDLs round-trip
    unchanged; accent=None means 'the style's own default color'.

    Position/size have TWO representations (TikTok model — drag/pinch on the canvas):
    continuous `pos_y`/`scale` from direct manipulation override the discrete
    `position`/`size` words when set; a later discrete op (chat: "captions at the
    bottom") clears its continuous override so the newest intent always wins."""
    position: str = "bottom"          # top | middle | bottom
    size: str = "medium"              # small | medium | large
    pos_y: Optional[float] = None     # caption block center, fraction of frame height (clamped 0.15-0.85)
    scale: Optional[float] = None     # font-size multiplier from pinch (clamped 0.5-2.0)
    accent: Optional[str] = None      # #RRGGBB for the hot word / karaoke fill; None = style default
    uppercase: bool = False           # force ALL CAPS
    font: str = "inter"               # inter | archivo | baloo (mirrors render/src fonts)
    grouping: str = "line"            # word | phrase (~3 words) | line (sliding window)


class EDL(BaseModel):
    style: str
    format_id: str
    segments: list[Segment]
    drops: list[Drop] = []
    captions: list[CaptionWord] = []
    # G3: word-start frames for the music-ducking heuristic, INDEPENDENT of the
    # visual captions toggle. set_captions_enabled(False) clears `captions` (the
    # on-screen text) but must not also silently kill ducking — a creator can
    # want captions off and voice-ducked music at the same time. Populated once
    # from the transcript at generation time and never cleared by that toggle.
    speech_frames: list[int] = []
    overlays: list[Overlay] = []
    broll: list[BRoll] = []
    react_source: Optional[ReactSource] = None       # duet_split only
    react_schedule: list[ReactWindow] = []           # duet_split only
    layout: Layout
    audio: Audio = Audio()
    # Rendering hints. REAL model fields (not loose dict keys) because Pydantic
    # silently drops unknown keys on the EDL(**data) → model_dump() round-trip —
    # which the tweak flow does on every edit, and would otherwise lose them.
    caption_style: Optional[str] = None              # clean | bold-word | karaoke
    caption_options: Optional[CaptionOptions] = None # position/size/accent/case/font tuning
    transitions: list[Transition] = []               # boundary dips between clips
    look: Optional[Look] = None                      # whole-video filter + adjust
    trim_aggressiveness: Optional[str] = None        # aggressive | None
    # Playback order of segments as a PERMUTATION of indices. Segments themselves
    # stay monotonic in source coords (the validator below is untouched) — physical
    # reordering would break every source-coord invariant; the render plan walks
    # this order instead. None = source order.
    segment_order: Optional[list[int]] = None

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

    @model_validator(mode="after")
    def check_segment_order(self):
        if self.segment_order is not None:
            if sorted(self.segment_order) != list(range(len(self.segments))):
                raise ValueError("segment_order must be a permutation of segment indices")
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
        speech_frames=[c.frame for c in captions],
        layout=Layout(style=style),
    )


def strip_fillers(words: list[dict], gap_ms: int = 300,
                  use_disfluency_type: bool = True) -> tuple[list[dict], list[Drop]]:
    """Remove filler words and dead-air gaps; return clean word list + drop list.

    Disfluency detection is the SOURCE OF TRUTH: when AssemblyAI is asked for
    `disfluencies` it tags filler tokens with `type == "filler"`, which is far more
    reliable than string-matching (it catches false starts and context-dependent
    "like"/"so"). We honor that tag first and fall back to the FILLER_WORDS lexicon
    for legacy/mocked transcripts that carry no type."""
    kept, drops = [], []
    prev_end = 0
    for w in words:
        text = w.get("word", "").lower().strip(".,!?")
        start = w.get("start_ms", 0)
        end = w.get("end_ms", start + 100)
        is_filler = (use_disfluency_type and w.get("type") == "filler") or text in FILLER_WORDS
        # Dead air before this word (measured from the previous word's end, filler or not).
        if prev_end > 0 and start - prev_end > gap_ms:
            drops.append(Drop(src_in=ms_to_frame(prev_end), src_out=ms_to_frame(start), reason="dead_air"))
        if is_filler:
            drops.append(Drop(src_in=ms_to_frame(start), src_out=ms_to_frame(end), reason="filler"))
        else:
            kept.append(w)
        # Advance past THIS word regardless of filler status — otherwise a run of
        # fillers before a gap makes the dead-air drop start at a stale prev_end and
        # overlap the filler drops (violates the non-overlapping-drops invariant).
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


def build_render_plan(edl: dict, warnings: list[str] | None = None) -> dict:
    """Transform an editorial EDL (source coords) into a render-ready plan (see above).

    Segments are walked in `segment_order` (a permutation; None = source order), so
    reordered segments land at their new output position — and because captions/
    overlays/broll are mapped through the same source→output index, they travel with
    their segment automatically. With identity order the index is exactly the flat
    kept-interval list this function always produced, so plans are byte-identical.

    `warnings`, if passed, collects human-readable strings for content this function
    silently drops that the caller should surface to the creator (mirrors the
    `warnings[]` clip field used for unresolved b-roll) — currently just the
    react_schedule desync-drop below. Optional and additive: omitting it reproduces
    the exact prior behavior."""
    segments = edl.get("segments") or []
    drops = edl.get("drops") or []
    order = edl.get("segment_order")
    if order is not None and sorted(order) == list(range(len(segments))):
        ordered_segments = [segments[i] for i in order]
    else:
        ordered_segments = sorted(segments, key=lambda s: s["src_in"])

    # cumulative output offset per kept interval, per segment IN PLAYBACK ORDER.
    # Each interval carries its segment's playback speed: output duration =
    # round(kept/speed), and every source→output mapping divides by it.
    clips: list[dict] = []
    index: list[tuple[int, int, int, float]] = []   # (src_in, src_out, out_start, speed)
    out_cursor = 0
    for seg in ordered_segments:
        speed = min(3.0, max(0.5, float(seg.get("speed") or 1.0)))
        for s_in, s_out in _kept_intervals([seg], drops):
            clips.append({"src_in": s_in, "src_out": s_out, "speed": speed,
                          # canvas transform travels with every kept piece of the clip
                          "tx_scale": min(3.0, max(0.5, float(seg.get("tx_scale") or 1.0))),
                          "tx_x": min(0.5, max(-0.5, float(seg.get("tx_x") or 0.0))),
                          "tx_y": min(0.5, max(-0.5, float(seg.get("tx_y") or 0.0)))})
            index.append((s_in, s_out, out_cursor, speed))
            out_cursor += max(1, round((s_out - s_in) / speed))
    total_frames = out_cursor

    def map_point(f: int) -> int | None:
        """Source frame → output frame, or None if f lands in a cut region."""
        for s_in, s_out, out_start, speed in index:
            if s_in <= f < s_out:
                return out_start + round((f - s_in) / speed)
        return None

    def _map_range_merged(a: int, b: int) -> list[list[int]]:
        """Source [a,b) → merged output spans: adjacent/overlapping pieces combined
        into one (so a single-segment drop still yields one continuous span), but
        genuinely non-adjacent pieces (a reorder scattering the range across the
        output) are kept separate rather than collapsed to the longest."""
        spans = []
        for s_in, s_out, out_start, speed in index:
            lo, hi = max(a, s_in), min(b, s_out)
            if lo < hi:
                spans.append((out_start + round((lo - s_in) / speed),
                              out_start + round((hi - s_in) / speed)))
        if not spans:
            return []
        spans.sort()
        merged = [list(spans[0])]
        for lo, hi in spans[1:]:
            if lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return merged

    def map_range(a: int, b: int) -> tuple[int, int] | None:
        """Source [a,b) → output [in,out); None if no kept footage overlaps it.
        Under a reorder, one source range can land in non-contiguous output pieces —
        this returns only the LONGEST merged one (global min/max would smear an
        overlay across unrelated reordered content). Callers that must not silently
        drop the other pieces (overlays, b-roll) should use map_range_all instead."""
        merged = _map_range_merged(a, b)
        if not merged:
            return None
        best = max(merged, key=lambda m: m[1] - m[0])
        return best[0], best[1]

    def map_range_all(a: int, b: int) -> list[tuple[int, int]]:
        """Source [a,b) → EVERY merged output piece (adjacent pieces combined,
        non-adjacent reordered pieces kept separate). Overlays/b-roll need every
        piece so a reorder never silently drops half the effect (F3)."""
        return [(lo, hi) for lo, hi in _map_range_merged(a, b)]

    def map_range_pieces(a: int, b: int) -> list[tuple[int, int]]:
        """Source [a,b) → EVERY output piece (unmerged). Volume ranges need this —
        a mute must not swallow reordered content that lands between its pieces."""
        pieces = []
        for s_in, s_out, out_start, speed in index:
            lo, hi = max(a, s_in), min(b, s_out)
            if lo < hi:
                pieces.append((out_start + round((lo - s_in) / speed),
                               out_start + round((hi - s_in) / speed)))
        return sorted(pieces)

    captions = []
    for c in edl.get("captions") or []:
        of = map_point(c["frame"])
        if of is not None:
            captions.append({"word": c["word"], "frame": of})
    # Sort by OUTPUT frame (D1): captions are emitted in source-list order, but under a
    # reorder_segments the played order differs — and Captions.tsx scans with an early
    # `break` that assumes ascending frames, so an unsorted list drops the first-played
    # segment's captions. Identity order is already ascending, so this is contract-neutral.
    captions.sort(key=lambda c: c["frame"])

    # Overlays/b-roll use map_range_all (every MERGED piece), not map_range
    # (longest-only) — under a reorder a single source range can land in TWO
    # non-adjacent output pieces, and keeping only the longest silently dropped
    # the other one entirely (F3). Adjacent pieces (the common single-segment-
    # drop case) still merge into one, matching prior behavior exactly.
    overlays = []
    for o in edl.get("overlays") or []:
        for lo, hi in map_range_all(o["src_in"], o["src_out"]):
            overlays.append({
                "type": o.get("type", "punch_in"),
                "frame_in": lo, "frame_out": hi,
                "scale": o.get("scale", 1.08), "text": o.get("text", ""),
                # text_sticker placement/look — defaults for older overlay dicts.
                "pos_x": o.get("pos_x", 0.5), "pos_y": o.get("pos_y", 0.5),
                "rotation": o.get("rotation", 0.0), "color": o.get("color"),
                "bg": o.get("bg", "none"), "font": o.get("font", "inter"),
            })
    overlays.sort(key=lambda o: o["frame_in"])       # ascending output order (D1, same reorder hazard)

    # Boundary transitions → output anchor frames. Anchored to the leading SOURCE
    # segment: the dip centers where that segment's last kept footage ends. Dropped
    # when the segment has no kept footage or is the final clip (nothing to hand to).
    transitions_out = []
    seg_count = len(segments)
    for t in edl.get("transitions") or []:
        si = t.get("after_segment", -1)
        if not (0 <= si < seg_count):
            continue
        seg = segments[si]
        pieces = map_range_pieces(seg["src_in"], seg["src_out"])
        if not pieces:
            continue
        end = max(hi for _, hi in pieces)
        if end >= total_frames:                       # final clip — no next clip to dip into
            continue
        transitions_out.append({"at_frame": end,
                                "style": t.get("style", "fade_black"),
                                "frames": max(4, min(30, int(t.get("frames") or 12)))})
    transitions_out.sort(key=lambda t: t["at_frame"])

    broll = []
    for b in edl.get("broll") or []:
        if not b.get("resolved_url"):
            continue  # F6: unresolved (e.g. Pexels failed/no key) — fail-soft, skip
                      # the layer entirely rather than emit a None-URL render instruction.
        for lo, hi in map_range_all(b["src_in"], b["src_out"]):
            broll.append({
                "frame_in": lo, "frame_out": hi,
                "cue_text": b.get("cue_text", ""),
                "asset_id": b.get("asset_id"), "broll_query": b.get("broll_query"),
                "source": b.get("source", "stock"),
                "resolved_url": b.get("resolved_url"),
            })

    # duet_split: remap the top-panel play/freeze schedule to output coords, carry the
    # source. Windows landing entirely inside a cut are dropped. A window that a drop
    # only PARTIALLY compresses is also dropped: its output length would shrink while
    # clip_from (the source cursor) stays fixed, so the source would jump/desync at the
    # next window. Requiring length preservation keeps the source cursor continuous.
    react_schedule = []
    for w in edl.get("react_schedule") or []:
        mapped = map_range(w["src_in"], w["src_out"])
        if mapped is None:
            continue
        if (mapped[1] - mapped[0]) != (w["src_out"] - w["src_in"]):
            if warnings is not None:
                warnings.append(
                    "react_window_dropped: cut/reorder would have desynced the reaction video")
            continue  # a cut straddles this window — skip rather than desync the source
        react_schedule.append({
            "state": w.get("state", "play"),
            "frame_in": mapped[0], "frame_out": mapped[1],
            "clip_from": w.get("clip_from", 0),
            "audio_gain": w.get("audio_gain", 1.0),
        })

    # Audio plan: music passes through; per-range source volume remaps to output
    # coords as SPLIT pieces (never merged — see map_range_pieces).
    audio_src = edl.get("audio") or {}
    volume_ranges_out = []
    for vr in audio_src.get("volume_ranges") or []:
        for lo, hi in map_range_pieces(vr["src_in"], vr["src_out"]):
            volume_ranges_out.append({"frame_in": lo, "frame_out": hi,
                                      "volume": vr.get("volume", 1.0)})
    # G3: speech_frames map the same way captions do (source→output via map_point),
    # so a cut/dropped word's frame correctly disappears from the ducking signal
    # too — but unlike captions, this list is NEVER cleared by the visual
    # captions-enabled toggle, so ducking keeps working when captions are off.
    speech_frames_out = [f for f in
                         (map_point(f) for f in (edl.get("speech_frames") or []))
                         if f is not None]

    audio_plan = {
        "lufs_target": audio_src.get("lufs_target", -14.0),
        "music": audio_src.get("music"),
        "volume_ranges": volume_ranges_out,
        "speech_frames": speech_frames_out,
    }

    return {
        "style": edl.get("style", "talking_head"),
        "format_id": edl.get("format_id", ""),
        "clips": clips,
        "captions": captions,
        "overlays": overlays,
        "broll": broll,
        "react_source": edl.get("react_source"),
        "react_schedule": react_schedule,
        # G1: normalize through the Layout model rather than passing the raw dict
        # through as-is — a caller that builds/patches a layout dict by hand (e.g.
        # set_split_fraction's edl.setdefault("layout", {})[...] = ...) can produce
        # one missing panels/panel_boundaries, which the render bridge's Layout
        # interface declares as REQUIRED (a missing key reads as `undefined` at
        # the JS runtime, not "use the default"). `style` folded in as a fallback
        # (not an override) since Layout.style has no default of its own.
        "layout": Layout(**{"style": edl.get("style", "talking_head"),
                            **(edl.get("layout") or {})}).model_dump(),
        # `or "clean"` (not a dict default): the key is now always present from
        # model_dump() with value None when unset.
        "caption_style": edl.get("caption_style") or "clean",
        # Always a fully-populated dict (defaults filled) so the render bridge's
        # TS interface never sees undefined keys.
        "caption_options": CaptionOptions(**(edl.get("caption_options") or {})).model_dump(),
        "transitions": transitions_out,
        "look": Look(**(edl.get("look") or {})).model_dump(),
        "audio": audio_plan,
        # Remotion requires durationInFrames >= 1; an all-cut plan still needs a valid frame.
        "total_frames": max(1, total_frames),
    }


# ---------------------------------------------------------------------------
# Tweak ops — deterministic application of typed edit operations to an EDL dict.
# The conversational tweak endpoint has the LLM emit these small typed ops
# (structured outputs); this function is the ONLY thing that mutates the EDL,
# so every change is bounded, auditable, and invariant-safe. Works on plain
# dicts (the job-store representation); caller re-validates via EDL(**data).
# ---------------------------------------------------------------------------

TWEAK_OP_TYPES = [
    "set_caption_style", "set_caption_options", "set_captions_enabled",
    "cut_range", "restore_range",
    "remove_overlays", "add_punch_in", "add_text_card", "add_text_sticker",
    "add_broll", "remove_broll", "set_split_fraction", "trim_start", "trim_end",
    "undo", "reorder_segments", "set_music", "set_segment_volume", "mute_range",
    "split_segment", "edit_caption", "edit_overlay",
    "set_segment_speed", "set_segment_transform", "set_transition", "set_filter", "set_adjust",
]

# Only these compositions actually draw the b-roll layer (render/src/compositions).
_BROLL_STYLES = {"broll_cutaway", "faceless"}
# G-04: only these styles' Remotion comps actually DRAW punch-in / text-card overlays;
# an op for any other style spends a re-render for a pixel-identical video, so gate it.
_PUNCH_STYLES = {"talking_head", "duet_split"}
_TEXTCARD_STYLES = {"green_screen", "duet_split"}
_MIN_DURATION_FRAMES = 60   # never let trims/cuts leave less than ~2s of footage


def style_capabilities(style: str) -> dict:
    """Which optional edit ops a style can actually render — the iOS editor queries this
    to hide toggles that would be silent no-ops (audit D4)."""
    return {
        "broll": True,   # BrollLayer is drawn by every composition now
        "punch_ins": style in _PUNCH_STYLES,
        "text_cards": style in _TEXTCARD_STYLES,
        # music/captions/volume/trim/cut/reorder are style-agnostic
        "music": True, "captions": True, "volume": True,
    }


def _coalesce_drops(drops: list[dict]) -> list[dict]:
    """Sort + union-merge overlapping/adjacent drops (manual cuts may swallow
    smaller filler drops — the union is what the user meant)."""
    ordered = sorted((dict(d) for d in drops), key=lambda d: d["src_in"])
    out: list[dict] = []
    for d in ordered:
        if out and d["src_in"] <= out[-1]["src_out"]:
            out[-1]["src_out"] = max(out[-1]["src_out"], d["src_out"])
            if d.get("reason") == "manual":
                out[-1]["reason"] = "manual"
        else:
            out.append(d)
    return out


def _kept_frames(edl: dict) -> int:
    """True playable duration: segment frames minus only the drop portions that
    actually OVERLAP a segment. (Naive seg_total - drop_total is wrong whenever a
    drop extends outside segment bounds — e.g. pipeline filler drops on footage
    the LLM's segments never included — and could even go negative.)"""
    # Union-merge drops first so overlapping drops can't double-subtract.
    merged: list[tuple[int, int]] = []
    for d in sorted(edl.get("drops") or [], key=lambda d: d["src_in"]):
        if merged and d["src_in"] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], d["src_out"]))
        else:
            merged.append((d["src_in"], d["src_out"]))
    total = 0
    for s in edl.get("segments") or []:
        seg_len = s["src_out"] - s["src_in"]
        cut = sum(max(0, min(s["src_out"], b) - max(s["src_in"], a)) for a, b in merged)
        total += max(0, seg_len - cut)
    return total


def apply_edl_ops(edl: dict, ops: list[dict], words: list[dict] | None = None
                  ) -> tuple[dict, list[dict]]:
    """Apply typed tweak ops to an EDL dict. Returns (new_edl, results) where each
    result is {"type", "applied", "reason"}. Never raises on a bad op — it's
    reported as skipped. `undo` is a server-level op (needs the history stack)
    and is always reported as skipped here."""
    import copy
    edl = copy.deepcopy(edl)
    results: list[dict] = []
    segments = edl.get("segments") or []
    src_extent = max((s["src_out"] for s in segments), default=0)

    def clamp_range(a, b):
        a = max(0, int(a)); b = min(src_extent, int(b))
        return (a, b) if b > a else None

    for op in ops or []:
        t = op.get("type", "")
        applied, reason = False, ""
        try:
            if t == "set_caption_style":
                style = op.get("style") or ""
                if style in ("clean", "bold-word", "karaoke"):
                    edl["caption_style"] = style
                    applied = True
                else:
                    reason = f"unknown caption style '{style}'"

            elif t == "set_caption_options":
                # Partial merge: only the keys present in the op change; every
                # provided value is validated (one bad value rejects the op whole,
                # so a typo can't half-apply).
                cur = dict(edl.get("caption_options") or {})
                allowed = {"position": ("top", "middle", "bottom"),
                           "size": ("small", "medium", "large"),
                           "font": ("inter", "archivo", "baloo"),
                           "grouping": ("word", "phrase", "line")}
                changed, bad = [], ""
                for key, values in allowed.items():
                    v = op.get(key)
                    if v is None:
                        continue
                    if v in values:
                        cur[key] = v
                        changed.append(key)
                    else:
                        bad = f"unknown {key} '{v}'"
                        break
                if not bad and op.get("accent") is not None:
                    accent = op.get("accent")
                    if accent == "default":              # chat-friendly reset
                        cur["accent"] = None
                        changed.append("accent")
                    elif isinstance(accent, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
                        cur["accent"] = accent
                        changed.append("accent")
                    else:
                        bad = f"bad accent '{accent}' (want #RRGGBB)"
                if not bad and op.get("uppercase") is not None:
                    cur["uppercase"] = bool(op.get("uppercase"))
                    changed.append("uppercase")
                if bad:
                    reason = bad
                elif changed:
                    edl["caption_options"] = CaptionOptions(**cur).model_dump()
                    applied = True
                else:
                    reason = "no caption option given"

            elif t == "set_captions_enabled":
                if op.get("enabled") is False:
                    edl["captions"] = []
                    applied = True
                elif op.get("enabled") is True:
                    if not words:
                        reason = "no transcript available to rebuild captions"
                    else:
                        kept, _ = strip_fillers(words)
                        edl["captions"] = [
                            {"word": w["word"], "frame": ms_to_frame(w.get("start_ms", 0))}
                            for w in kept if w.get("word")
                        ]
                        applied = True
                else:
                    reason = "enabled must be true or false"

            elif t == "cut_range":
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                if r is None:
                    reason = "invalid or out-of-bounds range"
                else:
                    candidate = _coalesce_drops(
                        (edl.get("drops") or []) + [{"src_in": r[0], "src_out": r[1], "reason": "manual"}])
                    trial = dict(edl); trial["drops"] = candidate
                    if _kept_frames(trial) < _MIN_DURATION_FRAMES:
                        reason = "cut would leave less than 2 seconds of footage"
                    else:
                        edl["drops"] = candidate
                        applied = True

            elif t == "restore_range":
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                if r is None:
                    reason = "invalid or out-of-bounds range"
                else:
                    s, e = r
                    new_drops, touched = [], False
                    for d in edl.get("drops") or []:
                        if d["src_out"] <= s or d["src_in"] >= e:
                            new_drops.append(d)          # no overlap
                        else:
                            touched = True
                            if d["src_in"] < s:          # left remainder survives
                                new_drops.append({**d, "src_out": s})
                            if d["src_out"] > e:         # right remainder survives
                                new_drops.append({**d, "src_in": e})
                    if touched:
                        edl["drops"] = sorted(new_drops, key=lambda d: d["src_in"])
                        applied = True
                    else:
                        reason = "no cuts found in that range"

            elif t == "remove_overlays":
                kind = op.get("kind") or "all"
                r = None
                if op.get("start_frame") is not None and op.get("end_frame") is not None:
                    r = clamp_range(op["start_frame"], op["end_frame"])
                before = edl.get("overlays") or []

                def keep(o):
                    if kind != "all" and o.get("type") != kind:
                        return True
                    if r and (o["src_out"] <= r[0] or o["src_in"] >= r[1]):
                        return True
                    return False
                after = [o for o in before if keep(o)]
                if len(after) < len(before):
                    edl["overlays"] = after
                    applied = True
                else:
                    reason = "no matching overlays found"

            elif t == "add_punch_in":
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                if edl.get("style") not in _PUNCH_STYLES:
                    reason = "punch-ins aren't rendered in this video style"
                elif r is None:
                    reason = "invalid or out-of-bounds range"
                else:
                    scale = op.get("scale") or 1.08
                    scale = max(1.02, min(1.35, float(scale)))
                    edl.setdefault("overlays", []).append(
                        {"type": "punch_in", "src_in": r[0], "src_out": r[1], "scale": scale, "text": ""})
                    applied = True

            elif t == "add_text_card":
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                text = (op.get("text") or "").strip()
                if edl.get("style") not in _TEXTCARD_STYLES:
                    reason = "text cards aren't rendered in this video style"
                elif r is None:
                    reason = "invalid or out-of-bounds range"
                elif not text:
                    reason = "text card needs text"
                else:
                    edl.setdefault("overlays", []).append(
                        {"type": "text_card", "src_in": r[0], "src_out": r[1], "scale": 1.0, "text": text[:80]})
                    applied = True

            elif t == "add_text_sticker":
                # Style-AGNOSTIC free-position text (the TikTok text tool) — every
                # composition renders it, unlike the style-gated text_card slab.
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                text = (op.get("text") or "").strip()
                if r is None:
                    reason = "invalid or out-of-bounds range"
                elif not text:
                    reason = "text sticker needs text"
                else:
                    color = op.get("color")
                    if color is not None and not (isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
                        color = None
                    edl.setdefault("overlays", []).append({
                        "type": "text_sticker", "src_in": r[0], "src_out": r[1],
                        "scale": min(3.0, max(0.4, float(op.get("scale") or 1.0))),
                        "text": text[:120],
                        "pos_x": min(0.95, max(0.05, float(op.get("pos_x") or 0.5))),
                        "pos_y": min(0.92, max(0.08, float(op.get("pos_y") or 0.5))),
                        "rotation": max(-45.0, min(45.0, float(op.get("rotation") or 0.0))),
                        "color": color,
                        "bg": op.get("bg") if op.get("bg") in ("none", "box") else "none",
                        "font": op.get("font") if op.get("font") in ("inter", "archivo", "baloo") else "inter",
                    })
                    applied = True

            elif t == "set_segment_transform":
                # Canvas drag/pinch on the video itself (partial merge; identity resets).
                idx = op.get("index")
                segs = edl.get("segments") or []
                if not (isinstance(idx, int) and 0 <= idx < len(segs)):
                    reason = f"no segment at index {idx}"
                else:
                    changed = False
                    for key, lo_v, hi_v in (("tx_scale", 0.5, 3.0), ("tx_x", -0.5, 0.5), ("tx_y", -0.5, 0.5)):
                        # ops carry the short names scale/off_x/off_y
                        short = {"tx_scale": "scale", "tx_x": "off_x", "tx_y": "off_y"}[key]
                        v = op.get(short)
                        if v is None:
                            continue
                        try:
                            segs[idx][key] = min(hi_v, max(lo_v, float(v)))
                            changed = True
                        except (TypeError, ValueError):
                            reason = f"bad {short} value"
                            changed = False
                            break
                    if changed:
                        applied = True
                    elif not reason:
                        reason = "no transform value given"

            elif t == "set_segment_speed":
                idx = op.get("index")
                segs = edl.get("segments") or []
                if not (isinstance(idx, int) and 0 <= idx < len(segs)):
                    reason = f"no segment at index {idx}"
                else:
                    try:
                        speed = float(op.get("speed") or 1.0)
                    except (TypeError, ValueError):
                        speed = 0.0
                    if not (0.5 <= speed <= 3.0):
                        reason = f"speed {op.get('speed')} out of range (0.5–3.0)"
                    else:
                        segs[idx]["speed"] = speed
                        applied = True

            elif t == "set_transition":
                idx = op.get("after_segment")
                segs = edl.get("segments") or []
                style_v = op.get("style") or "fade_black"
                if not (isinstance(idx, int) and 0 <= idx < len(segs)):
                    reason = f"no segment at index {idx}"
                elif style_v not in ("none", "fade_black", "fade_white", "flash"):
                    reason = f"unknown transition '{style_v}'"
                else:
                    trans = [tr for tr in (edl.get("transitions") or []) if tr.get("after_segment") != idx]
                    if style_v != "none":
                        trans.append({"after_segment": idx, "style": style_v,
                                      "frames": max(4, min(30, int(op.get("frames") or 12)))})
                    edl["transitions"] = trans
                    applied = True

            elif t == "set_filter":
                name = op.get("name")
                if name in (None, "", "none"):
                    look = dict(edl.get("look") or {})
                    look["filter"] = None
                    edl["look"] = Look(**look).model_dump()
                    applied = True
                elif name not in ("vivid", "film", "mono", "golden", "warm", "cool"):
                    reason = f"unknown filter '{name}'"
                else:
                    look = dict(edl.get("look") or {})
                    look["filter"] = name
                    try:
                        look["intensity"] = min(1.0, max(0.0, float(op.get("intensity") if op.get("intensity") is not None else 1.0)))
                    except (TypeError, ValueError):
                        look["intensity"] = 1.0
                    edl["look"] = Look(**look).model_dump()
                    applied = True

            elif t == "set_adjust":
                # Partial merge of the manual color knobs; anything absent stays put.
                look = dict(edl.get("look") or {})
                adjust = dict(look.get("adjust") or {})
                changed = False
                for knob, lo_v, hi_v in (("brightness", -0.5, 0.5), ("contrast", -0.5, 0.5),
                                         ("saturation", -0.5, 0.5), ("temperature", -0.5, 0.5),
                                         ("vignette", 0.0, 1.0)):
                    v = op.get(knob)
                    if v is None:
                        continue
                    try:
                        adjust[knob] = min(hi_v, max(lo_v, float(v)))
                        changed = True
                    except (TypeError, ValueError):
                        reason = f"bad {knob} value"
                        changed = False
                        break
                if changed:
                    look["adjust"] = adjust
                    edl["look"] = Look(**look).model_dump()
                    applied = True
                elif not reason:
                    reason = "no adjust knob given"

            elif t == "add_broll":
                # Media rolls are UNIVERSAL now — every composition draws BrollLayer,
                # so the old _BROLL_STYLES gate is gone. Two flavors: a stock QUERY
                # (Pexels-resolved at render) or the creator's OWN media via a direct
                # `url` (photo/video already uploaded by the app).
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                query = (op.get("query") or "").strip()
                url = (op.get("url") or "").strip()
                if r is None:
                    reason = "invalid or out-of-bounds range"
                elif not query and not url:
                    reason = "b-roll needs a search query or a media url"
                elif url and not url.lower().startswith(("http://", "https://")):
                    reason = "media url must be http(s)"
                else:
                    edl.setdefault("broll", []).append(
                        {"src_in": r[0], "src_out": r[1],
                         "cue_text": query or "your media",
                         "asset_id": None,
                         "broll_query": query or None,
                         "source": "own_media" if url else "stock",
                         "resolved_url": url or None})
                    applied = True

            elif t == "remove_broll":
                ranged = op.get("start_frame") is not None and op.get("end_frame") is not None
                r = clamp_range(op["start_frame"], op["end_frame"]) if ranged else None
                if ranged and r is None:
                    # AF-5 (audit): a PROVIDED-but-invalid range made the filter below
                    # falsy for every cue — a targeted removal silently wiped ALL
                    # b-roll and reported applied=True. Reject it like remove_overlays.
                    reason = "invalid or out-of-bounds range"
                else:
                    before = edl.get("broll") or []
                    # No range → remove all; ranged → remove only overlapping cues.
                    after = [] if r is None else \
                        [b for b in before if b["src_out"] <= r[0] or b["src_in"] >= r[1]]
                    if len(after) < len(before):
                        edl["broll"] = after
                        applied = True
                    else:
                        reason = "no b-roll found" + (" in that range" if r else "")

            elif t == "set_split_fraction":
                if edl.get("style") != "duet_split":
                    reason = "split sizing only applies to duet-style edits"
                else:
                    v = op.get("value")
                    if v is None:
                        reason = "missing split value"
                    else:
                        edl.setdefault("layout", {})["split_fraction"] = max(0.3, min(0.75, float(v)))
                        applied = True

            elif t in ("trim_start", "trim_end"):
                frames = int(op.get("frames") or 0)
                if frames <= 0:
                    reason = "trim needs a positive frame count"
                elif not segments:
                    reason = "no segments to trim"
                elif _kept_frames(edl) - frames < _MIN_DURATION_FRAMES:
                    reason = "trim would leave less than 2 seconds of footage"
                else:
                    segs = [dict(s) for s in edl["segments"]]
                    order = edl.get("segment_order")
                    # "Trim the start/end" means the start/end the VIEWER sees, so
                    # walk segments in PLAY order (segment_order), not array order —
                    # otherwise trimming after a reorder cuts the wrong segment.
                    play_order = list(order) if order is not None else list(range(len(segs)))
                    walk = play_order if t == "trim_start" else list(reversed(play_order))
                    popped: list[int] = []           # original indices fully consumed
                    remaining = frames
                    for orig_idx in walk:
                        if remaining <= 0:
                            break
                        seg = segs[orig_idx]
                        span = seg["src_out"] - seg["src_in"]
                        if span <= 0:
                            continue
                        take = min(remaining, span)
                        if t == "trim_start":
                            seg["src_in"] += take
                        else:
                            seg["src_out"] -= take
                        remaining -= take
                        if seg["src_in"] >= seg["src_out"]:
                            popped.append(orig_idx)
                    for gone in sorted(popped, reverse=True):
                        segs.pop(gone)
                    if popped:
                        gone_set = set(popped)
                        def _remap(i: int) -> int:
                            return i - sum(1 for g in popped if g < i)
                        new_order = [_remap(i) for i in play_order if i not in gone_set]
                        identity = list(range(len(segs)))
                        edl["segment_order"] = new_order if new_order != identity else None
                    edl["segments"] = segs
                    segments = segs
                    applied = True

            elif t == "split_segment":
                idx = op.get("index")
                at = op.get("at_frame")
                if not isinstance(idx, int) or not (0 <= idx < len(segments)):
                    reason = "index out of range"
                elif not isinstance(at, int) or not (segments[idx]["src_in"] < at < segments[idx]["src_out"]):
                    reason = "at_frame must be strictly inside the segment"
                else:
                    seg = segments[idx]
                    halves = [{"src_in": seg["src_in"], "src_out": at},
                              {"src_in": at, "src_out": seg["src_out"]}]
                    new_segs = segments[:idx] + halves + segments[idx + 1:]
                    old_order = edl.get("segment_order")
                    if old_order:
                        # remap the permutation: indices after idx shift +1; the new half
                        # (idx+1) plays right after its first half in the play order.
                        new_order: list[int] = []
                        for i in old_order:
                            new_order.append(i if i <= idx else i + 1)
                            if i == idx:
                                new_order.append(idx + 1)
                        edl["segment_order"] = new_order
                    edl["segments"] = new_segs
                    segments = new_segs
                    applied = True

            elif t == "edit_caption":
                frame = op.get("frame")
                word = op.get("word")
                if not isinstance(frame, int):
                    reason = "frame required"
                else:
                    caps = edl.setdefault("captions", [])
                    existing = next((c for c in caps if c.get("frame") == frame), None)
                    if word is None or (isinstance(word, str) and not word.strip()):
                        if existing:                     # empty word → remove the caption
                            caps.remove(existing)
                            applied = True
                        else:
                            reason = "no caption at that frame to remove"
                    elif existing:
                        existing["word"] = word.strip()[:60]
                        applied = True
                    else:                                 # add, keep captions frame-monotonic
                        caps.append({"word": word.strip()[:60], "frame": frame})
                        caps.sort(key=lambda c: c["frame"])
                        applied = True

            elif t == "edit_overlay":
                idx = op.get("index")
                ovs = edl.get("overlays") or []
                if not isinstance(idx, int) or not (0 <= idx < len(ovs)):
                    reason = "index out of range"
                else:
                    ov = ovs[idx]
                    changed = False
                    if op.get("text") is not None:
                        ov["text"] = str(op["text"])[:120]
                        changed = True
                    fi, fo = op.get("frame_in"), op.get("frame_out")
                    if fi is not None or fo is not None:
                        r = clamp_range(fi if fi is not None else ov["src_in"],
                                        fo if fo is not None else ov["src_out"])
                        if r is None:
                            reason = "invalid overlay window"
                        else:
                            ov["src_in"], ov["src_out"] = r
                            changed = True
                    # text_sticker placement/look (canvas drag/pinch/rotate + styling).
                    for key, lo_v, hi_v in (("pos_x", 0.05, 0.95), ("pos_y", 0.08, 0.92),
                                            ("scale", 0.4, 3.0), ("rotation", -45.0, 45.0)):
                        v = op.get(key)
                        if v is None:
                            continue
                        try:
                            ov[key] = min(hi_v, max(lo_v, float(v)))
                            changed = True
                        except (TypeError, ValueError):
                            reason = f"bad {key} value"
                    if op.get("color") is not None:
                        c = op.get("color")
                        if c == "default":
                            ov["color"] = None
                            changed = True
                        elif isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                            ov["color"] = c
                            changed = True
                    if op.get("bg") in ("none", "box"):
                        ov["bg"] = op["bg"]
                        changed = True
                    if op.get("font") in ("inter", "archivo", "baloo"):
                        ov["font"] = op["font"]
                        changed = True
                    if changed and not reason:
                        applied = True
                    elif not reason:
                        reason = "nothing to change"

            elif t == "reorder_segments":
                order = op.get("order")
                if not isinstance(order, list) or sorted(order) != list(range(len(segments))):
                    reason = "order must be a permutation of segment indices"
                elif order == list(range(len(segments))):
                    # Identity — also clears any prior reorder back to source order.
                    if edl.get("segment_order") is not None:
                        edl["segment_order"] = None
                        applied = True
                    else:
                        reason = "already in that order"
                else:
                    edl["segment_order"] = [int(i) for i in order]
                    applied = True

            elif t == "set_music":
                enabled = op.get("enabled")
                audio = edl.setdefault("audio", {})
                if enabled is False:
                    if audio.get("music"):
                        audio["music"] = None
                        applied = True
                    else:
                        reason = "no music to remove"
                elif enabled is True or op.get("url") or op.get("query"):
                    url = (op.get("url") or "").strip() or None
                    if url:
                        volume = max(0.0, min(1.0, float(op.get("volume", 0.15))))
                        audio["music"] = {"url": url, "query": None, "volume": volume,
                                          "duck_voice": bool(op.get("duck_voice", True))}
                        applied = True
                    else:
                        # G-03: a search query with no url can't be rendered — there's no
                        # server-side music search — so reject honestly instead of storing
                        # intent that AudioMix silently plays as nothing (D3).
                        reason = "pick a track to add music (a search term alone can't be played yet)"
                else:
                    reason = "enabled must be true or false"

            elif t in ("set_segment_volume", "mute_range"):
                r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                if r is None:
                    reason = "invalid or out-of-bounds range"
                else:
                    volume = 0.0 if t == "mute_range" else max(0.0, min(2.0, float(op.get("volume", 1.0))))
                    audio = edl.setdefault("audio", {})
                    existing = audio.get("volume_ranges") or []
                    # New range REPLACES the overlapped portion of existing ranges
                    # (split remainders survive) — last write wins, deterministic.
                    s, e = r
                    rebuilt: list[dict] = []
                    for vr in existing:
                        if vr["src_out"] <= s or vr["src_in"] >= e:
                            rebuilt.append(vr)
                        else:
                            if vr["src_in"] < s:
                                rebuilt.append({**vr, "src_out": s})
                            if vr["src_out"] > e:
                                rebuilt.append({**vr, "src_in": e})
                    rebuilt.append({"src_in": s, "src_out": e, "volume": volume})
                    audio["volume_ranges"] = sorted(rebuilt, key=lambda v: v["src_in"])
                    applied = True

            elif t == "undo":
                reason = "handled by the server history stack"

            else:
                reason = f"unknown op '{t}'"
        except (TypeError, ValueError, KeyError) as e:
            applied, reason = False, f"malformed op ({type(e).__name__})"

        results.append({"type": t, "applied": applied, "reason": reason})

    return edl, results
