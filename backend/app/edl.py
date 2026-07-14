"""EDL (Edit Decision List) — the universal contract between the AI editor and the renderer."""
from __future__ import annotations
from pydantic import BaseModel, model_validator
from typing import Optional
import re

# #19: version stamp for the render plan contract. build_render_plan stamps it into
# every plan (→ inputProps.edl.schema_version); the Remotion compositions compare it
# against their own compiled-in PLAN_SCHEMA_VERSION (render/src/types.ts) and warn in
# the Lambda logs on a mismatch — catching "backend deployed, site not redeployed"
# prop-drift that otherwise fails silently. BUMP BOTH SIDES together when the plan
# shape changes.
# v2 (P4): added end_card, progress_bar, audio.sfx — all additive/defaulted, so a
# stale v1 site just never renders them (no crash), and checkPlanSchema still warns.
PLAN_SCHEMA_VERSION = 2

MS_PER_FRAME = 1000.0 / 30.0  # 30fps

# P0.3: a kept clip shorter than this in OUTPUT frames (12 = 400ms @ 30fps) reads as a
# jarring sliver — build_render_plan drops such intervals (unless it's the only one).
# Mirrored in ios/.../EditorModel.swift keptIntervalsWithSpeed for preview parity.
MIN_CLIP_OUTPUT_FRAMES = 12

# Unambiguous fillers — never content words, safe to cut wherever they appear.
ALWAYS_FILLERS = frozenset({"um", "uh"})
# Discourse markers — filler ONLY at a clause boundary ("So, today we're..."), but
# content mid-sentence ("turn right here", "I feel like this works"). The old
# behavior cut EVERY occurrence, which deleted meaning-bearing words and littered
# the take with mid-phrase jump cuts; strip_fillers now cuts these only when they
# open a clause (first word, after a ≥250ms pause, or right after another filler).
DISCOURSE_MARKERS = frozenset({
    "like", "so", "basically", "literally", "actually",
    "right", "okay", "ok", "yeah", "yep", "well",
})
# Legacy union — kept for callers/tests that treat "is this word ever a filler"
# as a set-membership question. (Multi-word entries could never match the
# single-token text they were compared against, so they're gone.)
FILLER_WORDS = ALWAYS_FILLERS | DISCOURSE_MARKERS

# Function words a transcript routinely mistimes / marks low-confidence, yet which
# carry the SENTENCE'S MEANING when cut ("is", "the", "and", "to", "a"). The
# confidence-cut and false-start heuristics must NEVER eat these — cutting one turns
# "protein is not the problem" into "protein not problem" (the "cuts a word or two so
# it doesn't make sense" report). A garble worth cutting is almost always a genuine
# non-word stutter, not a real function word the recognizer under-scored.
PROTECTED_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am",
    "to", "of", "in", "on", "at", "it", "its", "i", "you", "he", "she", "we",
    "they", "and", "or", "but", "not", "no", "if", "as", "my", "your", "our",
    "this", "that", "do", "does", "did", "has", "have", "had", "can", "will",
    "for", "with", "me", "us", "them", "him", "her", "up", "out", "so",
})

# Multi-word discourse phrases ("you know", "kind of", ...) — never content on their
# own, but only really SAFE to cut without a pause on either side for the first two
# (the "so"/"like" family already has clause-boundary logic; these need their own
# guard since e.g. "kind of" is legitimately content in "what kind of dog is that").
FILLER_PHRASES: tuple[tuple[str, ...], ...] = (
    ("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of"),
    ("or", "whatever"), ("at", "the", "end", "of", "the", "day"),
    ("if", "that", "makes", "sense"),
)
# These two are content FAR more often than they're filler ("what kind of dog",
# "sort of blue") — require a pause on both sides even at the loosest trim level.
_PHRASE_ALWAYS_PAUSE_FLANKED = frozenset({("kind", "of"), ("sort", "of")})
# Never treat "you know" as filler right after these — "do you know", "did you know",
# "don't you know", "you'd know" are genuine questions/claims, not verbal filler.
_YOU_KNOW_GUARD_PRECEDING = frozenset({"do", "does", "did", "don't", "you'd", "didn't"})
# Trailing discourse words dropped off the END of a take/clause ("...so yeah.").
TRAILING_DISCOURSE = frozenset({"so", "yeah", "right", "okay", "ok", "cool", "alright", "anyway"})

# #1a: trim-aggressiveness levels — the single source of truth for every filler/
# silence knob (was previously just a hint the LLM prompt carried with no code-side
# effect; `trim_aggressiveness="aggressive"` used to be a complete no-op). Every
# level's gap_ms also drives strip_fillers' dead-air detection.
TRIM_LEVELS: dict[str, dict[str, float]] = {
    "conservative": {"gap_ms": 450, "keep_pause_frames": 7, "stutter_ms": 250, "phrase_mode": 0, "conf_cut": 0.0},
    "default":      {"gap_ms": 350, "keep_pause_frames": 6, "stutter_ms": 350, "phrase_mode": 1, "conf_cut": 0.35},
    "aggressive":   {"gap_ms": 250, "keep_pause_frames": 4, "stutter_ms": 450, "phrase_mode": 2, "conf_cut": 0.50},
}
# phrase_mode: 0 = clause-boundary only, 1 = clause-boundary OR pause-flanked,
# 2 = any pause-flanked occurrence (still never bare mid-sentence).

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
    frame: int                        # start frame for active-word highlight
    end_frame: Optional[int] = None   # P0.7: word END frame (from AssemblyAI end_ms).
                                      # Lets Captions.tsx hide the block after the last word
                                      # and during long silences. Additive + optional so old
                                      # EDLs round-trip unchanged (None → frame-based fallback).


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


class EndCard(BaseModel):
    """P4: a tail-of-video CTA card (dark fill + text + optional @handle), mutually
    exclusive with a loop-friendly trimmed tail (trim_loop_tail) — place_end_card
    enforces that. Tail-anchored, not source-anchored: build_render_plan appends
    its `frames` to total_frames rather than remapping a src_in/src_out window."""
    text: str = ""
    frames: int = 75             # ~2.5s @ 30fps
    show_handle: bool = True


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


class SfxCue(BaseModel):
    """P4: one deterministically-authored sound effect one-shot (whoosh/pop/hit),
    anchored to a SOURCE frame like everything else in the EDL (a transition point,
    an interrupt punch start, the hook overlay) — build_render_plan maps it through
    the same map_point as captions/speech_frames, so it silently disappears if the
    anchor frame gets cut. `url` is resolved from SFX_ASSETS (main.py) by
    synthesize_sfx at authoring time, not by build_render_plan."""
    src_in: int
    kind: str             # whoosh | pop | hit
    gain: float = 0.7
    url: Optional[str] = None


class Audio(BaseModel):
    # P0.6: loudness normalization is now live. app/audio.probe_loudness measures the
    # take's integrated LUFS (ffmpeg loudnorm analysis pass) during _run_analysis, and
    # _run_edit sets `gain` = clamp(lufs_target − measured, ±12dB). The render applies it
    # as a linear multiplier 10^(gain/20) on the source audio (CutVideo.tsx). `gain` is
    # ADDITIVE + OPTIONAL (default 0.0 = untouched), so old EDLs round-trip unchanged and
    # a box without ffmpeg / an unmeasurable take just leaves it at 0.
    lufs_target: float = -14.0
    gain: float = 0.0            # dB gain applied to source audio (loudness normalization)
    music: Optional[MusicTrack] = None
    volume_ranges: list[VolumeRange] = []
    sfx: list[SfxCue] = []        # P4: deterministic SFX one-shots (see SfxCue)


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
    grouping: str = "phrase"          # word | phrase (~3 words, DEFAULT) | line (stable chunks)
                                      # P0.7: default is `phrase` — stable 3-word chunks read
                                      # better than the old sliding `line` window.
    highlight_words: list[str] = []   # normalized lowercase words rendered in the accent color
                                      # (CapCut "auto-highlight keywords")


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
    end_card: Optional[EndCard] = None                # P4: tail CTA card (see EndCard)
    progress_bar: bool = False                        # P4: thin watch-progress bar overlay
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
    """Fallback EDL: keep the whole take, strip filler words, add timed captions.

    The filler DROPS are included (not just filtered captions): the plan-author
    path uses this EDL as-is and surfaces "safe default cut (fillers stripped)"
    to the creator — without real drops that message was a lie (footage kept
    every "um" while the captions pretended otherwise). The legacy path merges
    the same deterministic drops afterwards; _merge_drops coalesces duplicates,
    so including them here is idempotent there."""
    segments = [Segment(src_in=0, src_out=total_frames)]
    clean_words, filler_drops = strip_fillers(words)
    captions = [
        CaptionWord(word=w["word"], frame=ms_to_frame(w.get("start_ms", 0)),
                    end_frame=ms_to_frame(w["end_ms"]) if w.get("end_ms") else None)
        for w in clean_words if w.get("word")
    ]
    return EDL(
        style=style,
        format_id=format_id,
        segments=segments,
        drops=filler_drops,
        captions=captions,
        speech_frames=[c.frame for c in captions],
        layout=Layout(style=style),
    )


def clamp_edl_to_source(edl_data: dict, total_frames: int) -> dict:
    """Clamp every source-coordinate range in an LLM-authored EDL dict to the real
    source extent. The legacy author path trusted the model's frame numbers —
    a hallucinated src_out past the end of the video reached OffthreadVideo's
    trimAfter and broke or froze the Lambda render. Mutates and returns edl_data.
    Segments that vanish entirely (src_in beyond the source) are dropped; if ALL
    of them vanish, one whole-take segment is substituted so the EDL stays valid."""
    if total_frames <= 0:
        return edl_data
    segs = []
    for s in edl_data.get("segments") or []:
        s_in = max(0, min(int(s.get("src_in", 0)), total_frames - 1))
        s_out = max(0, min(int(s.get("src_out", 0)), total_frames))
        if s_out > s_in:
            segs.append({**s, "src_in": s_in, "src_out": s_out})
    edl_data["segments"] = segs or [{"src_in": 0, "src_out": total_frames}]
    for key in ("drops", "overlays", "broll"):
        kept_items = []
        for item in edl_data.get(key) or []:
            a = max(0, min(int(item.get("src_in", 0)), total_frames))
            b = max(0, min(int(item.get("src_out", 0)), total_frames))
            if b > a:
                kept_items.append({**item, "src_in": a, "src_out": b})
        if key in edl_data or kept_items:
            edl_data[key] = kept_items
    if edl_data.get("captions"):
        edl_data["captions"] = [c for c in edl_data["captions"]
                                if int(c.get("frame", 0)) < total_frames]
    return edl_data


def _covered_by_silence(a_ms: float, b_ms: float,
                        spans: list[tuple[int, int]], tol_ms: int = 80) -> bool:
    """True if [a_ms, b_ms] sits (within tol) inside one verified-silent span. Used to
    keep the dead-air trim from cutting a gap that actually carries speech energy — the
    tell-tale of a word the transcriber dropped."""
    for s0, s1 in spans:
        if s0 - tol_ms <= a_ms and b_ms <= s1 + tol_ms:
            return True
    return False


def strip_fillers(words: list[dict], gap_ms: int = 300,
                  use_disfluency_type: bool = True,
                  keep_pause_frames: int = 6,
                  silent_spans: list[tuple[int, int]] | None = None) -> tuple[list[dict], list[Drop]]:
    """Remove filler words and tighten (not eliminate) dead-air gaps; return clean
    word list + drop list.

    Disfluency detection is the SOURCE OF TRUTH: when AssemblyAI is asked for
    `disfluencies` it tags filler tokens with `type == "filler"`, which is far more
    reliable than string-matching (it catches false starts and context-dependent
    "like"/"so"). We honor that tag first, then the lexicon:
      - ALWAYS_FILLERS (um/uh) cut wherever they appear;
      - DISCOURSE_MARKERS (so/like/right/...) cut ONLY at a clause boundary —
        first word, after a ≥250ms pause, or immediately after another filler.
        Mid-sentence they are content ("turn right here", "I feel like it works");
        cutting them deleted meaning and left mid-phrase jump cuts.

    #1b silence tightening: a gap longer than gap_ms is no longer removed WHOLE —
    a hard butt-splice between two words with zero air between them reads as an
    obvious edit. Instead `keep_pause_frames` (~200ms at the default level) of
    natural pause survives, split roughly 2/3 before the cut and 1/3 after (so the
    residual breathes right up to the next word's onset rather than lingering after
    the previous one). Below ~4 remaining frames of droppable middle the tighten is
    skipped entirely — not enough room to matter, and forcing it risks a click."""
    kept, drops = [], []
    prev_end = 0
    prev_was_filler = False
    for w in words:
        text = w.get("word", "").lower().strip(".,!?")
        start = w.get("start_ms", 0)
        end = w.get("end_ms", start + 100)
        clause_boundary = (prev_end == 0                       # first word of the take
                           or start - prev_end >= 250          # follows a real pause
                           or prev_was_filler)                 # "um, so, ..." chains cut whole
        is_filler = ((use_disfluency_type and w.get("type") == "filler")
                     or text in ALWAYS_FILLERS
                     or (text in DISCOURSE_MARKERS and clause_boundary))
        # Dead air before this word (measured from the previous word's end, filler or not).
        if prev_end > 0 and start - prev_end > gap_ms:
            gap_start_f, gap_end_f = ms_to_frame(prev_end), ms_to_frame(start)
            lead = max(1, round(keep_pause_frames * 2 / 3))
            tail = max(1, keep_pause_frames - lead)
            drop_start, drop_end = gap_start_f + lead, gap_end_f - tail
            # Missed-word guard: when we have measured silence, only cut a gap that is
            # VERIFIED silent. A gap the transcriber left because it dropped a word still
            # has speech energy → it won't be a silent span → we keep it (the word's audio
            # survives, so the sentence still makes sense). No measurement → prior behavior.
            drop_ok = drop_end - drop_start >= 4 and (
                silent_spans is None
                or _covered_by_silence(_frame_to_ms(drop_start), _frame_to_ms(drop_end), silent_spans))
            if drop_ok:
                drops.append(Drop(src_in=drop_start, src_out=drop_end, reason="dead_air"))
            # else: too little room to usefully tighten — leave the natural gap as-is.
        if is_filler:
            drops.append(Drop(src_in=ms_to_frame(start), src_out=ms_to_frame(end), reason="filler"))
        else:
            kept.append(w)
        # Advance past THIS word regardless of filler status — otherwise a run of
        # fillers before a gap makes the dead-air drop start at a stale prev_end and
        # overlap the filler drops (violates the non-overlapping-drops invariant).
        prev_end = end
        prev_was_filler = is_filler
    return kept, drops


def _norm_word(text: str) -> str:
    return text.lower().strip(".,!?-")


def detect_disfluencies(words: list[dict], level: str = "default") -> list[Drop]:
    """#1c: layered ON TOP of strip_fillers (never replaces it — callers that need
    both use strip_fillers_v2 below). Catches what the single-token lexicon can't:
    multi-word discourse phrases, stutters/word-repeats, false starts, and trailing
    "so yeah"-style sign-offs — plus a confidence-aware cut for near-silent garbles.
    Pure and side-effect-free; never mutates `words`."""
    cfg = TRIM_LEVELS.get(level, TRIM_LEVELS["default"])
    phrase_mode = int(cfg["phrase_mode"])
    stutter_ms = cfg["stutter_ms"]
    conf_cut = cfg["conf_cut"]
    n = len(words)
    norms = [_norm_word(w.get("word", "")) for w in words]
    drops: list[Drop] = []

    def pause_before(i: int) -> float:
        if i <= 0:
            return 10_000.0
        return words[i].get("start_ms", 0) - words[i - 1].get("end_ms", 0)

    def pause_after(i: int) -> float:
        if i >= n - 1:
            return 10_000.0
        return words[i + 1].get("start_ms", 0) - words[i].get("end_ms", 0)

    # --- multi-word phrases ---
    i = 0
    while i < n:
        matched = None
        for phrase in FILLER_PHRASES:
            m = len(phrase)
            if i + m <= n and tuple(norms[i:i + m]) == phrase:
                matched = phrase
                break
        if matched:
            m = len(matched)
            flanked = pause_before(i) >= 120 and pause_after(i + m - 1) >= 120
            at_clause = (i == 0 or pause_before(i) >= 250
                         or (i > 0 and norms[i - 1] in ALWAYS_FILLERS))
            allow = flanked if (phrase_mode >= 2 or matched in _PHRASE_ALWAYS_PAUSE_FLANKED) \
                else (at_clause or (phrase_mode >= 1 and flanked))
            guarded = matched == ("you", "know") and i > 0 and norms[i - 1] in _YOU_KNOW_GUARD_PRECEDING
            if allow and not guarded:
                drops.append(Drop(src_in=ms_to_frame(words[i].get("start_ms", 0)),
                                  src_out=ms_to_frame(words[i + m - 1].get("end_ms", 0)),
                                  reason="filler"))
                i += m
                continue
        i += 1

    # --- stutter / word-repeat ---
    i = 0
    while i < n - 1:
        gap = words[i + 1].get("start_ms", 0) - words[i].get("end_ms", 0)
        same = norms[i] == norms[i + 1] and norms[i] != ""
        partial = (not same and norms[i] and norms[i + 1]
                   and (words[i].get("word", "").endswith("-")
                        or (len(norms[i]) >= 2 and norms[i + 1].startswith(norms[i]))))
        if (same or partial) and gap <= stutter_ms:
            drops.append(Drop(src_in=ms_to_frame(words[i].get("start_ms", 0)),
                              src_out=ms_to_frame(words[i].get("end_ms", 0)), reason="filler"))
            i += 1
            continue
        # bigram restart: "I think— I think" — same two-word run repeats within 1200ms.
        if i + 3 < n:
            first_pair = (norms[i], norms[i + 1])
            second_pair = (norms[i + 2], norms[i + 3])
            restart_gap = words[i + 2].get("start_ms", 0) - words[i + 1].get("end_ms", 0)
            if first_pair == second_pair and first_pair[0] and restart_gap <= 1200:
                drops.append(Drop(src_in=ms_to_frame(words[i].get("start_ms", 0)),
                                  src_out=ms_to_frame(words[i + 1].get("end_ms", 0)), reason="filler"))
                i += 2
                continue
        i += 1

    # --- false start: a short fragment (≤4 words) opening at take-start or after a
    # real pause, itself followed by a real pause, whose restart echoes it ---
    i = 0
    while i < n:
        if not (i == 0 or pause_before(i) >= 500):
            i += 1
            continue
        for frag_len in range(1, 5):
            j = i + frag_len
            if j >= n or pause_after(j - 1) < 300:
                continue
            frag_start_words = set(norms[i:min(i + 2, j)])
            restart_words = set(norms[j:j + 2])
            overlap = frag_start_words & restart_words
            # A real restart echoes either a CONTENT word or a discourse marker
            # ("So— So today…", "Like— Like the thing is…"). A purely grammatical
            # stopword echo ("I the" … "I the plan is") is a coincidence, not a
            # restart — cutting the fragment there ate a real clause (word-eater).
            content_or_marker = (overlap - PROTECTED_STOPWORDS) or (overlap & DISCOURSE_MARKERS)
            if overlap and content_or_marker:
                drops.append(Drop(src_in=ms_to_frame(words[i].get("start_ms", 0)),
                                  src_out=ms_to_frame(words[j - 1].get("end_ms", 0)), reason="false_start"))
                i = j
                break
        else:
            i += 1
            continue
        continue

    # --- trailing discourse sign-off ("...so yeah.") ---
    if n:
        j = n - 1
        run_start = n
        while j >= 0 and (n - 1 - j) < 3 and norms[j] in TRAILING_DISCOURSE:
            run_start = j
            j -= 1
        if run_start < n and pause_before(run_start) >= 400:
            drops.append(Drop(src_in=ms_to_frame(words[run_start].get("start_ms", 0)),
                              src_out=ms_to_frame(words[n - 1].get("end_ms", 0)), reason="filler"))

    # --- confidence-aware cut: short, unemphasized, low-confidence garbles ---
    if conf_cut > 0:
        for idx, w in enumerate(words):
            dur = w.get("end_ms", 0) - w.get("start_ms", 0)
            conf = w.get("confidence", 1.0)
            # Never cut a real function word the recognizer merely under-scored — those
            # carry the sentence's grammar; cutting one produces the "doesn't make sense"
            # garble. A genuinely cuttable low-confidence token is a non-word noise.
            if _norm_word(w.get("word", "")) in PROTECTED_STOPWORDS:
                continue
            if conf < conf_cut and 0 < dur <= 250 and not w.get("is_emphasized"):
                drops.append(Drop(src_in=ms_to_frame(w.get("start_ms", 0)),
                                  src_out=ms_to_frame(w.get("end_ms", 0)), reason="filler"))

    return drops


def strip_fillers_v2(words: list[dict], level: str = "default") -> tuple[list[dict], list[Drop]]:
    """#1d: strip_fillers (lexicon + tightened dead-air) plus detect_disfluencies
    (phrases/stutters/false-starts/trailing sign-offs/confidence), merged and
    recomputed against the same word list so `kept` reflects BOTH passes.

    `kept` is derived from FRAME COVERAGE against the final coalesced drop set, not
    from each drop's `reason` tag — _coalesce_drops merges adjacent/overlapping
    drops and keeps only the EARLIER one's reason, so a stutter-dropped word
    immediately following a tightened dead-air gap can end up inside a span still
    labeled "dead_air". Reason-filtering `kept` would wrongly leave that word in
    the transcript (a caption for audio that's actually been cut); any drop that
    fully contains a word's frame span means that word's audio is gone from the
    delivered output, full stop, regardless of which reason label survived."""
    cfg = TRIM_LEVELS.get(level, TRIM_LEVELS["default"])
    _, base_drops = strip_fillers(words, gap_ms=int(cfg["gap_ms"]),
                                  keep_pause_frames=int(cfg["keep_pause_frames"]))
    extra_drops = detect_disfluencies(words, level)
    drops = _coalesce_drops([d.model_dump() for d in base_drops] + [d.model_dump() for d in extra_drops])
    drops = [Drop(**d) for d in drops]
    dropped_ranges = [(d.src_in, d.src_out) for d in drops]
    kept = [w for w in words if not any(
        ms_to_frame(w.get("start_ms", 0)) >= lo and ms_to_frame(w.get("end_ms", w.get("start_ms", 0) + 100)) <= hi
        for lo, hi in dropped_ranges)]
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
    #
    # P0.3 min-clip guard: a kept interval whose OUTPUT length is < 12 frames (400ms)
    # renders as a jarring sub-half-second sliver — and the old `max(1, round(...))`
    # even minted 1-frame clips from tiny leftover slices between drops. Collect the
    # candidate intervals first, drop the slivers, and only then lay out the plan — but
    # never drop ALL of them (if every candidate is a sliver, keep the single longest
    # so a heavily-cut take still produces non-empty output).
    candidates: list[tuple[dict, int, int, float]] = []   # (seg, src_in, src_out, speed)
    for seg in ordered_segments:
        speed = min(3.0, max(0.5, float(seg.get("speed") or 1.0)))
        for s_in, s_out in _kept_intervals([seg], drops):
            candidates.append((seg, s_in, s_out, speed))

    def _out_len(s_in: int, s_out: int, speed: float) -> int:
        return max(1, round((s_out - s_in) / speed))

    kept_candidates = [c for c in candidates if _out_len(c[1], c[2], c[3]) >= MIN_CLIP_OUTPUT_FRAMES]
    if not kept_candidates and candidates:
        longest = max(candidates, key=lambda c: _out_len(c[1], c[2], c[3]))
        kept_candidates = [longest]
        # #26: every kept interval is a sub-400ms sliver — the cut removed almost
        # everything. We still deliver the longest (non-empty output beats a 1-frame
        # render), but this is a degenerate edit, not a polished clip: surface it as a
        # warning (same channel as broll_unresolved) so it never ships as a silent
        # "ready" sub-second video.
        if warnings is not None:
            secs = _out_len(longest[1], longest[2], longest[3]) / 30.0
            warnings.append(
                f"degenerate_edit: every kept segment was a sub-400ms sliver; delivered "
                f"the longest (~{secs:.1f}s) — the cut likely removed too much")

    clips: list[dict] = []
    index: list[tuple[int, int, int, float]] = []   # (src_in, src_out, out_start, speed)
    out_cursor = 0
    for seg, s_in, s_out, speed in kept_candidates:
        clips.append({"src_in": s_in, "src_out": s_out, "speed": speed,
                      # canvas transform travels with every kept piece of the clip
                      "tx_scale": min(3.0, max(0.5, float(seg.get("tx_scale") or 1.0))),
                      "tx_x": min(0.5, max(-0.5, float(seg.get("tx_x") or 0.0))),
                      "tx_y": min(0.5, max(-0.5, float(seg.get("tx_y") or 0.0)))})
        index.append((s_in, s_out, out_cursor, speed))
        out_cursor += _out_len(s_in, s_out, speed)
    total_frames = out_cursor

    def map_point(f: int) -> int | None:
        """Source frame → output frame, or None if f lands in a cut region.
        Clamped INSIDE the clip's output span: at speed>1 the division can round
        a tail-of-clip source frame to out_start+out_len (the NEXT clip's first
        frame), sliding captions/speech_frames onto the wrong clip."""
        for s_in, s_out, out_start, speed in index:
            if s_in <= f < s_out:
                return min(out_start + round((f - s_in) / speed),
                           out_start + _out_len(s_in, s_out, speed) - 1)
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
            cap = {"word": c["word"], "frame": of}
            # P0.7: remap the word END through the same source→output mapping. If the end
            # lands inside a cut (None) or before the start, clamp to start+1 so the block
            # still has a positive on-screen span.
            if c.get("end_frame") is not None:
                oe = map_point(c["end_frame"])
                cap["end_frame"] = oe if (oe is not None and oe > of) else of + 1
            captions.append(cap)
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
                                "frames": max(4, min(45, int(t.get("frames") or 12)))})
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

    # P4: SFX cues map through map_point exactly like speech_frames (a single
    # source-frame anchor, not a range) — a cue anchored to a punch-in/transition
    # that later gets cut just silently disappears rather than firing at the
    # wrong moment. Cues synthesize_sfx couldn't resolve a URL for (no hosted
    # asset configured for that kind) are dropped here too — fail-soft, same
    # philosophy as unresolved b-roll above.
    sfx_out = []
    for cue in audio_src.get("sfx") or []:
        url = cue.get("url")
        if not url:
            continue
        of = map_point(cue["src_in"])
        if of is None:
            continue
        sfx_out.append({"frame": of, "kind": cue.get("kind", ""),
                        "gain": float(cue.get("gain") or 0.7), "url": url})

    audio_plan = {
        "lufs_target": audio_src.get("lufs_target", -14.0),
        "gain": float(audio_src.get("gain") or 0.0),   # P0.6: loudness-normalization dB
        "music": audio_src.get("music"),
        "volume_ranges": volume_ranges_out,
        "speech_frames": speech_frames_out,
        "sfx": sfx_out,
    }

    # P4: end_card is TAIL-anchored (not source-coord), so it skips map_point
    # entirely — it always starts exactly where the last kept clip ends and
    # extends the OUTPUT total_frames by its own length. Computed here (after
    # every other pass already used the pre-extension `total_frames` for its
    # own bounds checks, e.g. the transitions_out "final clip" check above) so
    # extending it can't retroactively change any earlier decision.
    end_card_src = edl.get("end_card")
    end_card_out = None
    tail_frames = 0
    if end_card_src and (end_card_src.get("text") or "").strip():
        ec_frames = max(30, min(150, int(end_card_src.get("frames") or 75)))
        end_card_out = {"text": end_card_src["text"], "start_frame": total_frames,
                        "frames": ec_frames, "show_handle": bool(end_card_src.get("show_handle", True))}
        tail_frames = ec_frames

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
        "end_card": end_card_out,                       # P4: None when absent (G1: key always present)
        "progress_bar": bool(edl.get("progress_bar", False)),   # P4
        # Remotion requires durationInFrames >= 1; an all-cut plan still needs a valid frame.
        # +tail_frames: the end-card's own duration, appended AFTER every other
        # computation above already used the pre-extension total_frames.
        "total_frames": max(1, total_frames + tail_frames),
        # #19: contract version — the compositions warn if this ≠ their compiled value.
        "schema_version": PLAN_SCHEMA_VERSION,
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
# WS3 (retention-editor upgrade): expanded from {talking_head, duet_split} once
# PunchZoom.tsx landed in GreenScreen/BrollCutaway (inner-card/spine zoom) and
# SplitThree (whole-canvas zoom) — fast_cuts/faceless still excluded (fast_cuts'
# native cut cadence doesn't want a zoom on top; faceless has no face to zoom).
_PUNCH_STYLES = {"talking_head", "duet_split", "green_screen", "broll_cutaway", "split_three"}
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


def split_segment_in_place(edl: dict, idx: int, at_frame: int) -> bool:
    """Split segment `idx` at source frame `at_frame` into two, updating `edl` IN
    PLACE (segments, segment_order, transitions all kept consistent). Returns
    False (no-op) if idx/at_frame are invalid; True on success.

    Shared by apply_edl_ops' `split_segment` tweak-op AND the retention pacing
    pass (WS2) — one implementation, so #45 (both halves inherit the parent's
    speed/transform) and #10 (transitions remap to the boundary that survives)
    can never drift between the two call sites."""
    segments = edl.get("segments") or []
    if not (isinstance(idx, int) and 0 <= idx < len(segments)):
        return False
    seg = segments[idx]
    if not (isinstance(at_frame, int) and seg["src_in"] < at_frame < seg["src_out"]):
        return False
    # #45: both halves inherit the parent's per-segment settings (speed, canvas
    # transform, …) — otherwise splitting a sped-up/repositioned clip silently
    # resets it to defaults on the delivered render.
    carry = {k: v for k, v in seg.items() if k not in ("src_in", "src_out")}
    halves = [{**carry, "src_in": seg["src_in"], "src_out": at_frame},
              {**carry, "src_in": at_frame, "src_out": seg["src_out"]}]
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
    # #10: the insert shifts every source index at/after idx by +1, so a
    # transition anchored there moves with the second half — otherwise the fade
    # jumps to the wrong boundary in the render.
    if edl.get("transitions"):
        edl["transitions"] = [
            {**tr, "after_segment": tr["after_segment"] + 1}
            if isinstance(tr.get("after_segment"), int) and tr["after_segment"] >= idx
            else tr
            for tr in edl["transitions"]
        ]
    edl["segments"] = new_segs
    return True


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
                # Continuous canvas overrides (drag pos_y / pinch scale) — clamped.
                if not bad and op.get("pos_y") is not None:
                    try:
                        cur["pos_y"] = min(0.85, max(0.15, float(op["pos_y"])))
                        changed.append("pos_y")
                    except (TypeError, ValueError):
                        bad = f"bad pos_y '{op.get('pos_y')}'"
                if not bad and op.get("scale") is not None:
                    try:
                        cur["scale"] = min(2.0, max(0.5, float(op["scale"])))
                        changed.append("scale")
                    except (TypeError, ValueError):
                        bad = f"bad scale '{op.get('scale')}'"
                # Keyword highlight list — normalized to lowercase alphanumerics, capped.
                if not bad and op.get("highlight_words") is not None:
                    hw = op.get("highlight_words")
                    if isinstance(hw, list) and all(isinstance(x, str) for x in hw):
                        norm = [re.sub(r"[^a-z0-9]", "", x.lower()) for x in hw]
                        cur["highlight_words"] = [x for x in norm if x][:40]
                        changed.append("highlight_words")
                    else:
                        bad = "highlight_words must be a list of strings"
                # Newest intent wins: a discrete position/size clears its continuous override.
                if "position" in changed:
                    cur["pos_y"] = None
                if "size" in changed:
                    cur["scale"] = None
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
                            {"word": w["word"], "frame": ms_to_frame(w.get("start_ms", 0)),
                             "end_frame": ms_to_frame(w["end_ms"]) if w.get("end_ms") else None}
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
                ranged = op.get("start_frame") is not None and op.get("end_frame") is not None
                if ranged:
                    r = clamp_range(op["start_frame"], op["end_frame"])
                if ranged and r is None:
                    # The creator scoped the removal to a range and the range is
                    # garbage — failing open (r=None reads as "no range") would
                    # silently wipe EVERY overlay of that kind instead.
                    reason = "invalid or out-of-bounds range"
                else:
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
                    # ATOMIC (audit #9): stage into a copy and commit only if every
                    # field validates — a later bad value used to leave earlier fields
                    # already written into the persisted EDL while reporting applied=False.
                    staged = dict(segs[idx])
                    changed = False
                    for key, lo_v, hi_v in (("tx_scale", 0.5, 3.0), ("tx_x", -0.5, 0.5), ("tx_y", -0.5, 0.5)):
                        # ops carry the short names scale/off_x/off_y
                        short = {"tx_scale": "scale", "tx_x": "off_x", "tx_y": "off_y"}[key]
                        v = op.get(short)
                        if v is None:
                            continue
                        try:
                            staged[key] = min(hi_v, max(lo_v, float(v)))
                            changed = True
                        except (TypeError, ValueError):
                            reason = f"bad {short} value"
                            break
                    if changed and not reason:
                        segs[idx] = staged
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
                                      "frames": max(4, min(45, int(op.get("frames") or 12)))})
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
                        # #10: transitions anchor to a SOURCE segment index. Drop any whose
                        # leading segment was fully consumed, and shift the survivors down so
                        # a fade stays on the boundary the creator set (not a neighbour's).
                        if edl.get("transitions"):
                            edl["transitions"] = [
                                {**tr, "after_segment": _remap(tr["after_segment"])}
                                for tr in edl["transitions"]
                                if isinstance(tr.get("after_segment"), int)
                                and tr["after_segment"] not in gone_set
                            ]
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
                    split_segment_in_place(edl, idx, at)
                    segments = edl["segments"]
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
                    # ATOMIC (audit #9): stage into a copy; a later invalid field
                    # (e.g. a bad pos_x) used to report applied=False yet leave the
                    # earlier text/window edits written into the persisted EDL. Commit
                    # the copy only when every provided field validated.
                    ov = dict(ovs[idx])
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
                            break
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
                        ovs[idx] = ov                    # commit atomically
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


# ---------------------------------------------------------------------------
# Phase 3 — "LLM decides, code assembles". assemble_edl() turns a typed EDIT PLAN
# (prompts.EDIT_PLAN_JSON_SCHEMA) into a full EDL. The model never authors captions,
# filler drops, or b-roll frame math again — those are enforced here in code.
# ---------------------------------------------------------------------------

# B-roll grammar (frames @30fps) — the numbers live in knowledge/broll.md; mirrored here
# because the assembler must ENFORCE them, not just prompt for them.
_BROLL_JCUT_LEAD = 12
_BROLL_MIN_HOLD, _BROLL_MAX_HOLD = 60, 90     # 2–3s
_BROLL_MIN_SPACING = 90                        # ≥3s between cutaways
_BROLL_HOOK_PROTECT = 90                        # no b-roll over the hook (face styles)
_BROLL_CTA_PROTECT = 60                         # …or the CTA
_FACELESS_STYLES = {"faceless"}                # b-roll IS the visual channel → hook coverage ok
_PUNCH_SCALE_MIN, _PUNCH_SCALE_MAX = 1.03, 1.12
_PUNCH_HOLD = 30
_TEXTCARD_HOLD = 60


def _clamp_range(a: int, b: int, lo: int, hi: int) -> tuple[int, int] | None:
    a, b = max(lo, min(a, hi)), max(lo, min(b, hi))
    if b <= a:
        return None
    return a, b


def assemble_edl(plan: dict, words: list[dict], style: str, format_id: str,
                 prefs: dict | None = None, brief: dict | None = None,
                 silent_spans: list[tuple[int, int]] | None = None) -> EDL:
    """Pure function: (typed plan, transcript words) -> a valid EDL.

    Guarantees the LLM cannot violate:
      - captions ALWAYS derived from the cleaned word list (never from the plan);
      - deterministic filler/dead-air drops always win (strip_fillers);
      - cut boundaries snap to word boundaries (±3 frames via snap_to_word);
      - b-roll grammar enforced in code (J-cut lead, 2–3s holds, ≥3s spacing, hook/CTA
        protection for face styles);
      - min-clip guard + clamps + layout synthesis + speech_frames regeneration.
    """
    plan = plan or {}
    prefs = prefs or {}
    # No-words fallback is 30000 MS through the same conversion (=900 frames @30fps).
    # A bare `30000` here was FRAMES — a 16-minute phantom timeline that minted
    # 1000-second whole-take segments from an empty transcript.
    total = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000)) if words else ms_to_frame(30000)

    # --- drops: deterministic fillers + editorial cuts, all snapped + clamped ---
    clean_words, filler_drops = strip_fillers(words, silent_spans=silent_spans)
    drops: list[dict] = [d.model_dump() for d in filler_drops]
    if prefs.get("filler_trim") == "off":
        drops = []
    for c in (plan.get("cuts") or []):
        rng = c.get("range") or []
        if len(rng) != 2:
            continue
        s_in = snap_to_word(_frame_to_ms(rng[0]), words, "start")
        s_out = snap_to_word(_frame_to_ms(rng[1]), words, "end")
        cl = _clamp_range(s_in, s_out, 0, total)
        if cl:
            reason = c.get("reason") or "false_start"
            reason = reason if reason in ("filler", "dead_air", "false_start") else "false_start"
            drops.append({"src_in": cl[0], "src_out": cl[1], "reason": reason})
    # brief editorial cuts (flub/ramble/tangent) also honored
    for cr in ((brief or {}).get("cut_regions") or []):
        if cr.get("reason") in ("flub", "ramble", "tangent") and cr.get("end_frame", 0) > cr.get("start_frame", 0):
            cl = _clamp_range(cr["start_frame"], cr["end_frame"], 0, total)
            if cl:
                drops.append({"src_in": cl[0], "src_out": cl[1], "reason": "false_start"})
    drops = _coalesce_drops(drops)

    # --- segments: keeps (or whole take), snapped/clamped/merged, monotonic ---
    keeps = []
    for k in (plan.get("keeps") or []):
        if len(k) == 2:
            cl = _clamp_range(snap_to_word(_frame_to_ms(k[0]), words, "start"),
                              snap_to_word(_frame_to_ms(k[1]), words, "end"), 0, total)
            if cl:
                keeps.append(cl)
    if not keeps:
        keeps = [(0, total)]
    keeps.sort()
    merged = [list(keeps[0])]
    for a, b in keeps[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    # --- open_on: pull a buried hook forward by dropping the pre-hook intro ---
    open_on = plan.get("open_on") or {}
    hook_start = open_on.get("start")
    if isinstance(hook_start, int) and hook_start > _BROLL_HOOK_PROTECT and merged and hook_start < merged[-1][1]:
        # only when the hook sits meaningfully after the first kept frame — and
        # GUARDED: an LLM-hallucinated "hook" near the END of the take would
        # delete almost everything in one drop (the <3s kept-duration invariant
        # is deliberately soft, so nothing downstream catches it). Pulling a
        # genuinely buried hook is the feature; the guard is on what REMAINS:
        # refuse the pull if it would leave under ~6s of kept footage.
        kept_span = sum(b - a for a, b in merged)
        pre_hook_kept = sum(min(b, hook_start) - a for a, b in merged if a < hook_start)
        if (hook_start > merged[0][0] + _BROLL_HOOK_PROTECT
                and kept_span - pre_hook_kept >= 180):
            drops.append({"src_in": merged[0][0], "src_out": hook_start, "reason": "false_start"})
            drops = _coalesce_drops(drops)

    segments = [{"src_in": a, "src_out": b} for a, b in merged]

    # --- P4.2 loop-friendly ending: trim trailing dead-air after the last spoken word to
    # ≤10 frames so the autoplay loop cuts clean back to the first frame. Only trims the
    # final segment's tail, never below the last word (and never inverts the segment). ---
    if clean_words:
        last_word_end = ms_to_frame(clean_words[-1].get("end_ms") or clean_words[-1].get("start_ms", 0))
        tail = segments[-1]
        if tail["src_out"] - last_word_end > 10 and last_word_end + 10 > tail["src_in"]:
            tail["src_out"] = min(tail["src_out"], last_word_end + 10)

    # --- captions: ALWAYS from the cleaned words (never the plan) ---
    captions = [
        {"word": w["word"], "frame": ms_to_frame(w.get("start_ms", 0)),
         "end_frame": ms_to_frame(w["end_ms"]) if w.get("end_ms") else None}
        for w in clean_words if w.get("word")
    ]

    # --- b-roll: grammar enforced in code ---
    broll: list[dict] = []
    if prefs.get("broll") is not False:
        face_style = style not in _FACELESS_STYLES
        last_out = None
        for b in (plan.get("broll") or []):
            rng = b.get("range") or []
            if len(rng) != 2:
                continue
            cue_f = int(rng[0])
            s_in = max(0, cue_f - _BROLL_JCUT_LEAD)                 # J-cut lead
            hold = max(_BROLL_MIN_HOLD, min(_BROLL_MAX_HOLD, int(rng[1]) - int(rng[0]) or _BROLL_MIN_HOLD))
            s_out = min(total, s_in + hold)
            if face_style and s_in < _BROLL_HOOK_PROTECT:           # protect the hook face
                continue
            if face_style and s_out > total - _BROLL_CTA_PROTECT:   # protect the CTA face
                continue
            if last_out is not None and s_in - last_out < _BROLL_MIN_SPACING:   # spacing
                continue
            if s_out - s_in < _BROLL_MIN_HOLD:
                continue
            broll.append({"src_in": s_in, "src_out": s_out, "cue_text": b.get("cue", ""),
                          "broll_query": b.get("query") or b.get("cue", ""),
                          "source": b.get("source") if b.get("source") in ("stock", "own_media") else "stock"})
            last_out = s_out

    # --- overlays: punch-ins + text cards ---
    overlays: list[dict] = []
    if prefs.get("punch_ins") is not False and style in _PUNCH_STYLES:
        for p in (plan.get("punch_ins") or []):
            f = int(p.get("frame", 0))
            cl = _clamp_range(f, f + _PUNCH_HOLD, 0, total)
            if cl:
                scale = max(_PUNCH_SCALE_MIN, min(_PUNCH_SCALE_MAX, float(p.get("scale") or 1.08)))
                overlays.append({"type": "punch_in", "src_in": cl[0], "src_out": cl[1],
                                 "scale": scale, "text": ""})
    if style in _TEXTCARD_STYLES:
        for tc in (plan.get("text_cards") or []):
            f = int(tc.get("frame", 0))
            cl = _clamp_range(f, f + _TEXTCARD_HOLD, 0, total)
            if cl and tc.get("text"):
                overlays.append({"type": "text_card", "src_in": cl[0], "src_out": cl[1],
                                 "scale": 1.0, "text": str(tc["text"])[:200]})

    # --- caption plan → options/style (prefs override) ---
    cp = plan.get("caption_plan") or {}
    caption_style = prefs.get("caption_style") or cp.get("style") or "clean"
    grouping = cp.get("grouping") if cp.get("grouping") in ("word", "phrase", "line") else "phrase"
    # Same normalization the renderer applies (Captions.tsx normWord strips
    # non-alphanumerics): plain .lower() left "A.I." as "a.i.", which the
    # renderer's "ai" could never match — the highlight silently never fired.
    _hw = [re.sub(r"[^a-z0-9]", "", str(w).lower()) for w in (cp.get("highlight_words") or [])]
    caption_options = {"grouping": grouping,
                       "highlight_words": [w for w in _hw if w][:12]}
    if prefs.get("auto_captions") is False:
        captions = []

    # --- segment_order: only a valid permutation of the segment count ---
    order = plan.get("order")
    segment_order = None
    if isinstance(order, list) and len(order) == len(segments) and sorted(order) == list(range(len(segments))):
        segment_order = order

    # --- layout synthesis ---
    layout = {"style": style}
    if style == "split_three":
        layout["panels"] = 3

    # --- duet_split: minimal play-then-freeze react schedule. The plan author has
    # no react_schedule concept, and an empty schedule meant the top panel played
    # its full audio over the creator's entire rebuttal. Grammar: play the claim
    # for the first ~2.5s of kept footage (viewer hears what's being rebutted),
    # then hold a freeze while the creator talks. Every window sits INSIDE one
    # kept interval, so build_render_plan's length-preservation guard can never
    # drop them (a window fully inside kept footage maps 1:1 to output).
    react_schedule: list[ReactWindow] = []
    if style == "duet_split":
        kept_iv = _kept_intervals(segments, drops)
        if kept_iv:
            first_a, first_b = kept_iv[0]
            play_end = min(first_a + 75, first_b)          # ≤2.5s of the claim
            played = play_end - first_a                    # react-video cursor after play
            if play_end > first_a:
                react_schedule.append(ReactWindow(
                    state="play", src_in=first_a, src_out=play_end,
                    clip_from=0, audio_gain=1.0))
            freeze_pieces = ([(play_end, first_b)] if first_b > play_end else []) \
                + [(a, b) for a, b in kept_iv[1:]]
            for a, b in freeze_pieces:
                react_schedule.append(ReactWindow(
                    state="freeze", src_in=a, src_out=b,
                    clip_from=max(0, played), audio_gain=0.15))

    edl = EDL(
        style=style, format_id=format_id or "myth-buster",
        segments=[Segment(**s) for s in segments],
        drops=[Drop(**d) for d in drops],
        captions=[CaptionWord(**c) for c in captions],
        speech_frames=[ms_to_frame(w.get("start_ms", 0)) for w in clean_words if w.get("word")],
        overlays=[Overlay(**o) for o in overlays],
        broll=[BRoll(**b) for b in broll],
        react_schedule=react_schedule,
        layout=Layout(**layout),
        audio=Audio(lufs_target=-14.0),
        caption_style=caption_style,
        caption_options=CaptionOptions(**caption_options),
        segment_order=segment_order,
    )
    return edl


def _frame_to_ms(frame: int) -> int:
    """Inverse of ms_to_frame (30fps) — for snapping plan frame anchors back to words."""
    return int(round(frame * 1000.0 / 30.0))


# ---------------------------------------------------------------------------
# Phase 3 — deterministic invariant checker (the edl_verify_prompt checklist as code).
# Returns hard-issue strings; empty == pass. The LLM verify is reserved for reorder
# coherence only (a judgment call code can't make).
# ---------------------------------------------------------------------------

def check_edl_invariants(edl: dict, words: list[dict] | None = None) -> list[str]:
    issues: list[str] = []
    segments = edl.get("segments") or []
    drops = edl.get("drops") or []
    last_frame = ms_to_frame(max((w.get("end_ms", 0) for w in (words or [])), default=30000)) if words else None

    if not segments:
        issues.append("no segments")
        return issues

    # segment/drop sanity + bounds
    prev_out = -1
    for i, s in enumerate(segments):
        if s["src_out"] <= s["src_in"]:
            issues.append(f"segment {i} has src_out<=src_in")
        if last_frame is not None and s["src_out"] > last_frame + 1:
            issues.append(f"segment {i} extends past source last frame")
        if s["src_in"] < prev_out:
            issues.append(f"segment {i} overlaps the previous segment")
        prev_out = max(prev_out, s["src_out"])
    for j, d in enumerate(drops):
        if d["src_out"] <= d["src_in"]:
            issues.append(f"drop {j} has src_out<=src_in")

    # kept intervals for the "falls outside every kept segment" checks
    kept = _kept_intervals(segments, drops)
    def _in_kept(f: int) -> bool:
        return any(a <= f < b for a, b in kept)

    for k, ov in enumerate(edl.get("overlays") or []):
        if not (_in_kept(ov["src_in"]) or any(a <= ov["src_out"] <= b for a, b in kept)):
            issues.append(f"overlay {k} window falls outside every kept segment")
    for m, b in enumerate(edl.get("broll") or []):
        if not (_in_kept(b["src_in"]) or any(a <= b["src_out"] <= b2 for a, b2 in kept)):
            issues.append(f"broll {m} window falls outside every kept segment")

    # total kept duration plausibility (<3s is almost always a bug)
    kept_frames = sum(b - a for a, b in kept)
    if kept_frames < 90:
        issues.append(f"kept duration {kept_frames}f < 3s")

    # style-specific
    style = edl.get("style")
    if style != "duet_split" and (edl.get("react_source") or edl.get("react_schedule")):
        issues.append("react_source/schedule present on a non-duet style")
    if style == "split_three":
        panels = (edl.get("layout") or {}).get("panels")
        if panels != 3:
            issues.append("split_three must have panels=3")

    # segment_order must be a permutation
    order = edl.get("segment_order")
    if order is not None and sorted(order) != list(range(len(segments))):
        issues.append("segment_order is not a valid permutation")

    return issues
