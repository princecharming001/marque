"""Pre-render document checks (ARCHITECTURE §5, §7).

``validate_document(doc, index, job=None) -> list[Finding]``

Findings are ``error`` (render must not proceed / an invariant would fail), ``warning`` (likely a
mistake, or doctrine advice a critic should see) or ``info``. Ops keep documents valid, so errors here
usually mean a document was edited by hand, loaded against a different index, or went stale.

Checks: non-empty story; segment IDs/ranges/sources; no word in two segments; gap overrides; speed
and framing ranges; seams (first segment, J/L leads); pinned words present (invariant 4); inserts
(anchors resolvable, kept and ordered, licence per invariant 5, asset files present when a job is given,
no overlapping full-screen inserts); text/caption/SFX/music anchors; caption coverage; loudness target
and true-peak ceiling (invariant 8); deliverables.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from studio.doc.model import FULLSCREEN_MODES, CutDocument

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = ["Finding", "validate_document", "has_errors", "errors", "format_findings"]

Level = Literal["error", "warning", "info"]


class Finding(BaseModel):
    level: Level
    code: str
    message: str
    refs: list[str] = Field(default_factory=list)


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.level == "error" for f in findings)


def errors(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "error"]


def format_findings(findings: Iterable[Finding]) -> str:
    return "\n".join(f"[{f.level}] {f.code}: {f.message}" + (f" ({', '.join(f.refs)})" if f.refs else "")
                     for f in findings)


def validate_document(doc: CutDocument, index: TakeIndex, job: Job | None = None) -> list[Finding]:
    """Run every document check; never raises for document problems."""
    out: list[Finding] = []

    def add(level: Level, code: str, message: str, *refs: str) -> None:
        out.append(Finding(level=level, code=code, message=message, refs=[r for r in refs if r]))

    ix = index
    words = ix.word_map

    # ------------------------------------------------------------------ story
    if not doc.segments:
        add("error", "empty_story", "the story has no segments")

    coverage: dict[str, str] = {}
    output_order: list[str] = []
    reused: dict[tuple[str, str], list[str]] = {}
    for k, s in enumerate(doc.segments):
        if s.from_word not in words or s.to_word not in words:
            add("error", "segment_unknown_word", f"{s.id} references unknown words {s.from_word}-{s.to_word}", s.id)
            continue
        a, b = ix.word_pos(s.from_word), ix.word_pos(s.to_word)
        if a > b:
            add("error", "segment_reversed", f"{s.id}: {s.from_word} comes after {s.to_word}", s.id)
            continue
        if ix.words[a].source != ix.words[b].source:
            add("error", "segment_two_sources", f"{s.id} spans two sources", s.id)
        for p in range(a, b + 1):
            wid = ix.words[p].id
            if wid in coverage:
                reused.setdefault((coverage[wid], s.id), []).append(wid)
            else:
                coverage[wid] = s.id
            output_order.append(wid)
        inner = {g.id: g for g in ix.gaps_between(s.from_word, s.to_word)}
        for gid, ms in s.gap_overrides.items():
            g = inner.get(gid)
            if g is None:
                add("error", "gap_override_outside", f"{s.id}: gap {gid} is not inside the segment", s.id, gid)
            elif ms * 1000 > g.duration_us + 999:
                add("error", "gap_override_longer", f"{s.id}: {gid} target {ms} ms > measured "
                    f"{g.duration_ms:.0f} ms", s.id, gid)
        if s.framing is not None and s.framing.anchor_word is not None:
            aw = s.framing.anchor_word
            if aw not in words or not (a <= ix.word_pos(aw) <= b):
                add("error", "framing_anchor_outside", f"{s.id}: framing anchor {aw} is outside the segment",
                    s.id, aw)
        if k == 0 and s.seam_in.kind not in ("cut", "punch"):
            add("warning", "first_seam", f"{s.id} is first; seam '{s.seam_in.kind}' will be ignored", s.id)
        if s.seam_in.kind in ("jcut", "lcut", "crossfade") and s.seam_in.lead_ms <= 0:
            add("warning", "seam_no_lead", f"{s.id}: '{s.seam_in.kind}' with lead_ms 0 acts like a cut", s.id)

    for (sa, sb), ws in reused.items():
        add("error", "word_reused", f"{len(ws)} word(s) ({ws[0]}…{ws[-1]}) are in both {sa} and {sb}; "
            "a word may appear once", sa, sb, *ws[:10])

    pos: dict[str, int] = {}
    for i, w in enumerate(output_order):
        pos.setdefault(w, i)

    # ------------------------------------------------------------------ pins (invariant 4)
    for wid, kind in doc.pins.all().items():
        if wid not in words:
            add("error", "pin_unknown_word", f"pinned {kind} word {wid} does not exist", wid)
        elif wid not in pos:
            add("error", "pinned_word_missing", f"pinned {kind} word {wid} '{words[wid].text}' is not in the "
                "story (invariant 4)", wid)

    def span(fr: str, to: str, label: str, ref: str) -> tuple[int, int] | None:
        for w in (fr, to):
            if w not in words:
                add("error", "anchor_unknown_word", f"{label}: anchor {w} does not exist", ref, w)
                return None
            if w not in pos:
                add("error", "anchor_not_kept", f"{label}: anchor {w} is not in the story", ref, w)
                return None
        if pos[fr] > pos[to]:
            add("error", "anchor_order", f"{label}: {fr} comes after {to} in the cut", ref)
            return None
        return pos[fr], pos[to]

    # ------------------------------------------------------------------ inserts
    spans: list[tuple[str, str, tuple[int, int]]] = []
    for ins in doc.inserts:
        sp = span(ins.anchor_from_word, ins.anchor_to_word, f"insert {ins.id}", ins.id)
        if ins.needs_licence and ins.effective_licence is None:
            add("error", "missing_licence", f"insert {ins.id} has no licence record (invariant 5)", ins.id)
        if ins.mode == "card" and ins.asset.type != "card":
            add("error", "card_mode_asset", f"insert {ins.id}: mode 'card' needs a card asset", ins.id)
        if not ins.job.strip():
            add("warning", "insert_no_job", f"insert {ins.id} has no stated job", ins.id)
        if job is not None and ins.asset.type == "asset":
            if ins.asset.path:
                p = job.root / ins.asset.path
                if not p.exists():
                    add("error", "asset_missing", f"insert {ins.id}: asset file {ins.asset.path} not found", ins.id)
            elif not ins.asset.url:
                add("error", "asset_unlocated", f"insert {ins.id}: asset has neither path nor url", ins.id)
        if sp is not None:
            spans.append((ins.id, ins.mode, sp))
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            (ia, ma, sa), (ib, mb, sb) = spans[i], spans[j]
            if sa[0] <= sb[1] and sb[0] <= sa[1]:
                if ma in FULLSCREEN_MODES and mb in FULLSCREEN_MODES:
                    add("error", "fullscreen_overlap", f"full-screen inserts {ia} and {ib} overlap", ia, ib)
                else:
                    add("warning", "insert_overlap", f"inserts {ia} ({ma}) and {ib} ({mb}) overlap", ia, ib)

    # ------------------------------------------------------------------ texts
    for t in doc.texts:
        span(t.anchor_from_word, t.anchor_to_word, f"text {t.id}", t.id)

    # ------------------------------------------------------------------ captions
    cap = doc.captions
    if cap is not None:
        last = -1
        captioned: set[str] = set()
        for k, p in enumerate(cap.pages):
            label = p.id or f"page #{k + 1}"
            for w in p.word_ids:
                if w not in words:
                    add("error", "caption_unknown_word", f"caption {label}: {w} does not exist", label, w)
                    continue
                if w not in pos:
                    add("error", "caption_word_cut", f"caption {label}: {w} is not in the story", label, w)
                    continue
                if pos[w] <= last:
                    add("error", "caption_order", f"caption {label}: {w} is out of output order", label, w)
                last = max(last, pos[w])
                captioned.add(w)
            if p.text is None:
                chars = sum(len(words[w].text) + 1 for w in p.word_ids if w in words)
                limit = cap.style.max_chars_per_line * cap.style.lines
                if chars - 1 > limit:
                    add("warning", "caption_long_page", f"caption {label}: {chars - 1} chars > {limit}", label)
        if cap.enabled:
            if not cap.pages:
                add("warning", "captions_no_pages", "captions are enabled but have no pages")
            else:
                spoken = [w for w in output_order if words[w].kind in ("word", "cutoff")]
                missing = [w for w in spoken if w not in captioned]
                if missing:
                    add("warning", "uncaptioned_words", f"{len(missing)} spoken words have no caption page",
                        *missing[:10])

    # ------------------------------------------------------------------ audio
    for x in doc.audio.sfx:
        if x.anchor_word not in words:
            add("error", "anchor_unknown_word", f"sfx {x.id}: anchor {x.anchor_word} does not exist", x.id)
        elif x.anchor_word not in pos:
            add("error", "anchor_not_kept", f"sfx {x.id}: anchor {x.anchor_word} is not in the story", x.id)
        if x.asset is not None and x.asset.licence is None:
            add("error", "missing_licence", f"sfx {x.id} asset has no licence record (invariant 5)", x.id)
    m = doc.audio.music
    if m is not None:
        if m.asset is not None and m.asset.licence is None:
            add("error", "missing_licence", "music asset has no licence record (invariant 5)", "music")
        for w in [m.start_word, m.end_word, *m.hit_word_ids]:
            if w is not None and w not in pos:
                add("error", "anchor_not_kept", f"music anchor {w} is not in the story", "music", w)
        if m.start_word and m.end_word and m.start_word in pos and m.end_word in pos \
                and pos[m.start_word] > pos[m.end_word]:
            add("error", "anchor_order", "music start comes after its end", "music")
    if doc.audio.true_peak_dbtp > -1.0:
        add("error", "true_peak_ceiling", f"true peak ceiling {doc.audio.true_peak_dbtp} dBTP > -1 (invariant 8)")
    if not (-24.0 <= doc.audio.loudness_target_lufs <= -9.0):
        add("error", "loudness_target", f"loudness target {doc.audio.loudness_target_lufs} LUFS out of range")

    # ------------------------------------------------------------------ hook alternates
    for h in doc.hook_alternates:
        for r in h.ranges:
            if r.from_word not in words or r.to_word not in words:
                add("error", "hook_unknown_word", f"hook {h.id}: unknown words {r.from_word}-{r.to_word}", h.id)
            elif ix.word_pos(r.from_word) > ix.word_pos(r.to_word):
                add("error", "hook_reversed", f"hook {h.id}: range {r.from_word}-{r.to_word} reversed", h.id)

    # ------------------------------------------------------------------ deliverables / meta
    if not doc.deliverables:
        add("warning", "no_deliverables", "no deliverables; tiktok will be assumed")
    if doc.segments and output_order:
        est = doc.estimated_duration_us(ix) / 1e6 if not has_errors(out) else None
        if est is not None:
            add("info", "estimated_duration", f"estimated output length {est:.1f}s over {len(doc.segments)} "
                "segments")
            if doc.brief and doc.brief.target_length_s and est > doc.brief.target_length_s * 1.25:
                add("warning", "over_length", f"estimated {est:.1f}s vs brief target "
                    f"{doc.brief.target_length_s:.0f}s")
    return out
