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
    # Rendering hints. REAL model fields (not loose dict keys) because Pydantic
    # silently drops unknown keys on the EDL(**data) → model_dump() round-trip —
    # which the tweak flow does on every edit, and would otherwise lose them.
    caption_style: Optional[str] = None              # clean | bold-word | karaoke
    trim_aggressiveness: Optional[str] = None        # aggressive | None

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
            continue  # a cut straddles this window — skip rather than desync the source
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
        # `or "clean"` (not a dict default): the key is now always present from
        # model_dump() with value None when unset.
        "caption_style": edl.get("caption_style") or "clean",
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
    "set_caption_style", "set_captions_enabled", "cut_range", "restore_range",
    "remove_overlays", "add_punch_in", "add_text_card", "add_broll",
    "remove_broll", "set_split_fraction", "trim_start", "trim_end", "undo",
]

# Only these compositions actually draw the b-roll layer (render/src/compositions).
_BROLL_STYLES = {"broll_cutaway", "faceless"}
_MIN_DURATION_FRAMES = 60   # never let trims/cuts leave less than ~2s of footage


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
                if r is None:
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
                if r is None:
                    reason = "invalid or out-of-bounds range"
                elif not text:
                    reason = "text card needs text"
                else:
                    edl.setdefault("overlays", []).append(
                        {"type": "text_card", "src_in": r[0], "src_out": r[1], "scale": 1.0, "text": text[:80]})
                    applied = True

            elif t == "add_broll":
                if edl.get("style") not in _BROLL_STYLES:
                    reason = "b-roll isn't rendered in this video style"
                else:
                    r = clamp_range(op.get("start_frame") or 0, op.get("end_frame") or 0)
                    query = (op.get("query") or "").strip()
                    if r is None:
                        reason = "invalid or out-of-bounds range"
                    elif not query:
                        reason = "b-roll needs a search query"
                    else:
                        edl.setdefault("broll", []).append(
                            {"src_in": r[0], "src_out": r[1], "cue_text": query,
                             "asset_id": None, "broll_query": query, "source": "stock",
                             "resolved_url": None})
                        applied = True

            elif t == "remove_broll":
                r = None
                if op.get("start_frame") is not None and op.get("end_frame") is not None:
                    r = clamp_range(op["start_frame"], op["end_frame"])
                before = edl.get("broll") or []
                after = [b for b in before
                         if r and (b["src_out"] <= r[0] or b["src_in"] >= r[1])]
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
                    remaining = frames
                    if t == "trim_start":
                        while remaining > 0 and segs:
                            take = min(remaining, segs[0]["src_out"] - segs[0]["src_in"])
                            segs[0]["src_in"] += take
                            remaining -= take
                            if segs[0]["src_in"] >= segs[0]["src_out"]:
                                segs.pop(0)
                    else:
                        while remaining > 0 and segs:
                            take = min(remaining, segs[-1]["src_out"] - segs[-1]["src_in"])
                            segs[-1]["src_out"] -= take
                            remaining -= take
                            if segs[-1]["src_in"] >= segs[-1]["src_out"]:
                                segs.pop()
                    edl["segments"] = segs
                    segments = segs
                    applied = True

            elif t == "undo":
                reason = "handled by the server history stack"

            else:
                reason = f"unknown op '{t}'"
        except (TypeError, ValueError, KeyError) as e:
            applied, reason = False, f"malformed op ({type(e).__name__})"

        results.append({"type": t, "applied": applied, "reason": reason})

    return edl, results
