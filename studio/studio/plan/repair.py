"""Code-only repair of a failed render (docs/standard-tier-interfaces.md, "Repair"; plan section 2, stage 9).

``repair(job, doc, index, render_dir, failures, profile=, settings=) -> RepairResult``

The standard tier has no Director to read a failed invariant and fix the document, so this module turns the
failures code can fix into an ID-level document change (applied through :meth:`EditSession.apply_ops`, so the
oplog records it and invariant 3 re-validates it) and the cheapest re-render that carries that change:

==== ================================================================ ===========================================
inv  fix                                                              re-render
==== ================================================================ ===========================================
1    ``cut_words`` the cut-off fragment at a join and any word the    full render at the profile
     recording itself chops (:func:`studio.doc.validate.cutoffs_at_joins`,
     ``Word.truncated``), repeated while a cut exposes another one
4    ``restore_words`` the pinned words that are not in the story     full render at the profile
7    re-place the offending caption pages: the placer's own ladder    overlay + master only, on the failed
     when its spot is clean and new, else the nearest clean height    render's own ``aroll.mov`` + ``mix.wav``
     (another page's height first), then a caption size step down
     toward the 64 px floor (``edit_caption_page`` y / style size)
8    no document change: the mix is re-mastered against the codec's   audio re-master + mux (the encoded picture
     measured loudness / true-peak offset on the encoded file         stream is copied, not re-encoded)
==== ================================================================ ===========================================

Rules:

* **One re-render per call**, the cheapest one carrying every planned fix (full > overlay > audio); its
  :func:`~studio.qa.invariants.evaluate_render` is the verdict. A second failure is returned as
  ``passed=False`` with ``remaining`` (the pipeline's next step is the pure-code plan, else FAILED): repair never
  loops.
* **No render that cannot pass.** When any failed invariant has no code fix (2, 3, 5, 6, 10; 1 with nothing to
  cut; 7 on a text overlay; 9 unless the planned cuts remove the words it names) nothing is rendered and every
  failure comes back in ``remaining``.
* **Never put a page back where it failed**: a re-placed caption page must move (or change size), and it must
  clear the safe zone and the face under the same geometry QA uses (``planned_text_boxes`` /
  ``face_protected_rect``), widened by how far the renderer drew past the plan on the failed render.
* **Intermediates.** The overlay and audio passes read the failed render's intermediates (hard-linked into a new
  ``renders/r{n}/``, so the failed render stays intact and no bytes are copied); when they are gone the pass falls
  back to a full render. :func:`studio.agent.loop.render_full` does not prune; the caller must not prune the
  failed render between ``evaluate_render`` and ``repair`` (prune both after repair).
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.captions import CaptionParams, SafeZone
    from studio.compile.models import Timeline, TimelineCaptionPage
    from studio.config import Settings
    from studio.doc.model import CaptionPlan, CaptionStyle, CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = ["PLAN_DEPENDENT", "REPAIRABLE", "RepairResult", "invariant_label", "repair"]

#: invariant number -> the code fix (what the report says repair can do)
REPAIRABLE: dict[int, str] = {
    1: "cut the cut-off fragment at the join, then a full re-render",
    4: "restore the pinned words, then a full re-render",
    7: "re-place the caption pages, then an overlay + master re-render",
    8: "re-master the audio against the encoded file, then a re-mux",
}
#: why the others are not repaired in code (they point at an engine fault or need judgment)
_UNREPAIRABLE: dict[int, str] = {
    2: "audio/picture alignment is an engine fault, not a document choice",
    3: "a timestamp in the document or oplog is an engine fault",
    5: "a missing licence needs a licensed asset, not a code change",
    6: "HDR handling is an ingest fault",
    9: "digital silence under speech is not caused by a word the repair cuts",
    10: "the delivery format is an encoder fault",
}
#: Invariants whose failure can depend on the plan (the cut, the pins, the caption geometry, the voice chain's
#: mix, silence the cut exposes). Only these make the pipeline's pure-code second attempt worth a render: the others
#: (2, 3, 5, 6, 10) are engine or media faults that would fail any plan's render the same way.
PLAN_DEPENDENT: frozenset[int] = frozenset({1, 4, 7, 8, 9})
_PAGE_RE = re.compile(r"^p\d{3,}$")
_SIZE_FACTORS = (0.9, 0.8)  # the placer's own size steps (captions._Placer._scales), then the floor
_FRAGMENT_ROUNDS = 3  # cutting a fragment can expose another one at the new join
_CANDIDATES = 8  # compiled checks per page and caption size
_STEP_PX = 8.0  # height scan step (px at 1920 high)
_MIN_MOVE_PX = 4.0  # a re-placed page must move at least this far (px at 1920 high) unless its size changed
_PAD_PX = 8.0  # extra clearance (px at 1920 high) when the render drew past the planned box
_TP_MARGIN_DB = 0.3  # true-peak headroom kept over the codec's measured overshoot
_MAX_CEILING_DROP_DB = 4.0


def invariant_label(n: int) -> str:
    """``"7 text_safe"``: the number (sortable, parseable) and the invariant's id (readable)."""
    from studio.qa.invariants import INVARIANTS

    return f"{n} {INVARIANTS[n][0]}" if n in INVARIANTS else str(n)


@dataclass
class RepairResult:
    """What one repair call did. ``doc``/``render_dir`` are the repaired pair (the inputs when nothing was
    rendered); ``fixed`` / ``remaining`` are invariant labels (:func:`invariant_label`): failed before and passing
    now / failing now. ``failures`` are the invariants still failing, in the dict shape QA produces."""

    doc: CutDocument
    render_dir: Path
    passed: bool
    fixed: list[str]
    remaining: list[str]
    rerendered: bool
    mode: str = "none"  # none | full | overlay | audio
    notes: list[str] = field(default_factory=list)  # one line per decision, for the report
    failures: list[dict[str, Any]] = field(default_factory=list)


class _NeedFull(Exception):
    """A partial pass cannot run (an intermediate is gone, the compiled cut changed): render fully instead."""


@dataclass
class _Offence:
    """One caption page invariant 7 named, with how far the render drew past its planned box (px: left, top,
    right, bottom) so the new spot clears what was actually drawn, not only the estimate."""

    page_id: str
    words: list[str]
    kinds: set[str] = field(default_factory=set)
    ext: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    shrink: bool = False  # drawn wider than the band: only a smaller size helps
    old_center: float = 0.0  # px
    old_size: int = 0


@dataclass
class _Plan:
    ops: list[dict[str, Any]] = field(default_factory=list)
    story: bool = False  # the cut changes: full render
    captions: bool = False  # caption pages re-placed: overlay + master
    audio: bool = False  # re-master the mix
    loudness: dict[str, dict[str, Any]] = field(default_factory=dict)  # mix name -> re-master target/ceiling
    notes: list[str] = field(default_factory=list)
    unrepairable: dict[int, str] = field(default_factory=dict)


# ============================================================================================ entry point
def repair(job: Job, doc: CutDocument, index: TakeIndex, render_dir: Path, failures: list[dict[str, Any]], *,
           profile: str = "standard", settings: Settings | None = None) -> RepairResult:
    """Fix what code can fix in ``render_dir`` (rendered from ``doc``) and re-render once (see module docstring).

    ``failures`` are failed-invariant dicts as :func:`studio.qa.invariants.evaluate_render` produces them
    (``InvariantResult.to_dict()``: ``number``, ``refs``, ``data`` …); passed entries are ignored."""
    from studio.compile.master import render_profile
    from studio.config import get_settings
    from studio.storage import DiskSpaceError

    s = settings or get_settings()
    profile = render_profile(profile)
    rd = Path(render_dir)
    failed: dict[int, dict[str, Any]] = {}
    for f in failures:
        n = f.get("number")
        if isinstance(n, int) and not f.get("passed", False):
            failed.setdefault(n, f)
    before = [invariant_label(n) for n in sorted(failed)]
    if not failed:
        return RepairResult(doc=doc, render_dir=rd, passed=True, fixed=[], remaining=[], rerendered=False,
                            notes=["no failed invariant to repair"])
    t0 = time.monotonic()
    plan = _plan(job, doc, index, rd, failed, s)
    notes = list(plan.notes)

    def unrendered(extra: list[str]) -> RepairResult:
        notes.extend(extra)
        job.trace("repair", mode="none", render=rd.name, failed=sorted(failed), fixed=[], remaining=before,
                  rerendered=False, notes=notes[:20], seconds=round(time.monotonic() - t0, 2))
        return RepairResult(doc=doc, render_dir=rd, passed=False, fixed=[], remaining=before, rerendered=False,
                            notes=notes, failures=list(failed.values()))

    if plan.unrepairable:
        return unrendered([f"{invariant_label(n)}: {why}; no re-render attempted"
                           for n, why in sorted(plan.unrepairable.items())])
    if not (plan.story or plan.captions or plan.audio):
        return unrendered(["nothing to change in code; no re-render attempted"])

    new_doc = doc
    if plan.ops:
        from studio.agent.tools import EditSession

        out = EditSession(job, index, doc=doc, settings=s, by="repair").apply_ops(plan.ops)
        bad = [r for r in out.results if not r.applied]
        notes += [f"op {r.op} rejected: {r.reason}" for r in bad]
        if bad:  # the dry run accepted every op: a rejection now means the document moved under us
            return unrendered(["repair ops were rejected; no re-render attempted"])
        new_doc = out.doc
        notes.append(f"document v{out.base_version} -> v{out.new_version} ({len(plan.ops)} ops)")

    mode = "full" if plan.story else "overlay" if plan.captions else "audio"
    try:
        new_rd, mode = _rerender(job, new_doc, index, rd, mode, profile=profile, remaster=plan.audio,
                                 corrections=plan.loudness, settings=s, notes=notes)
    except DiskSpaceError:
        raise
    except Exception as e:  # a render that fails is a failed repair, never a crashed pipeline
        return unrendered([f"{mode} re-render failed: {type(e).__name__}: {str(e)[:300]}"])

    try:
        passed, after = _evaluate(job, new_rd, s)
    except Exception as e:  # QA that cannot run is not a pass; the repaired render is still handed back
        notes.append(f"QA of {new_rd.name} failed: {type(e).__name__}: {str(e)[:300]}")
        passed, after = False, list(failed.values())
    now = sorted({int(f["number"]) for f in after if isinstance(f.get("number"), int)})
    fixed = [invariant_label(n) for n in sorted(failed) if n not in now]
    remaining = [invariant_label(n) for n in now]
    job.trace("repair", mode=mode, render=rd.name, repaired_render=new_rd.name, doc_version=new_doc.version,
              failed=sorted(failed), fixed=fixed, remaining=remaining, rerendered=True, passed=passed,
              notes=notes[:20], seconds=round(time.monotonic() - t0, 2))
    return RepairResult(doc=new_doc, render_dir=new_rd, passed=passed, fixed=fixed, remaining=remaining,
                        rerendered=True, mode=mode, notes=notes, failures=after)


# ============================================================================================ planning
def _plan(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, failed: dict[int, dict[str, Any]],
          s: Settings) -> _Plan:
    """Decide every fix on dry runs (no version is saved until the whole batch is known to apply)."""
    from studio.doc.ops import apply_ops
    from studio.doc.validate import validate_document

    plan = _Plan()
    for n in failed:
        if n not in REPAIRABLE and n != 9:
            plan.unrepairable[n] = _UNREPAIRABLE.get(n, "no code fix")
    work = doc
    story_ops: list[dict[str, Any]] = []
    if 4 in failed:
        ops, why = _restore_pins(doc, index, failed[4])
        if ops:
            story_ops += ops
            spans = ", ".join(f"{o['from_word']}-{o['to_word']}" for o in ops)
            plan.notes.append(f"4: restore {spans}")
        if why or not ops:
            plan.unrepairable[4] = why or "every pinned word is already in the story"
    if story_ops:
        work, res = apply_ops(doc, story_ops, index)
        if not all(r.applied for r in res):
            plan.unrepairable[4] = "; ".join(f"{r.op}: {r.reason}" for r in res if not r.applied)
            story_ops, work = [], doc
    cut_ids: set[str] = set()
    if 1 in failed or story_ops:  # a restore can expose a fragment at its new join
        ops, cut_ids, why, work = _cut_fragments(work, index, failed.get(1))
        story_ops += ops
        if ops:
            plan.notes.append("1: cut " + ", ".join(f"{w} {index.word(w).display()!r}" for w in sorted(
                cut_ids, key=index.word_pos)))
        if 1 in failed and (why or not ops):
            plan.unrepairable[1] = why or "no cut-off fragment or chopped word to cut (a click or clipped word at " \
                                          "a seam needs judgment)"
    if 9 in failed:
        refs = set(failed[9].get("refs") or [])
        if not refs or not refs <= cut_ids:
            plan.unrepairable[9] = _UNREPAIRABLE[9]
    if story_ops:
        before = {(f.code, tuple(f.refs)) for f in validate_document(doc, index, job) if f.level == "error"}
        new = [f for f in validate_document(work, index, job) if f.level == "error"
               and (f.code, tuple(f.refs)) not in before]
        if new:
            for n in (1, 4):
                if n in failed:
                    plan.unrepairable[n] = "the fix leaves the document invalid: " + "; ".join(
                        f"{f.code} {f.message}" for f in new[:3])
            story_ops, work = [], doc
    plan.ops += story_ops
    plan.story = bool(story_ops)

    if 7 in failed:
        try:
            ops, ok, msgs = _caption_fix(job, doc, work, index, rd, failed[7], s)
        except Exception as e:  # pragma: no cover - a geometry failure must not crash the pipeline
            ops, ok, msgs = [], False, [f"caption re-placement failed: {type(e).__name__}: {e}"]
        plan.notes += [f"7: {m}" for m in msgs]
        if ok:
            plan.ops += ops
            plan.captions = True
        else:
            plan.unrepairable[7] = "; ".join(msgs[-2:]) or "no clean caption placement found"
    if 8 in failed:
        if plan.story:
            plan.audio = True  # corrected on the new render after it exists (its own measurement)
        else:
            corr = _loudness_corrections(doc, rd, s)
            if corr:
                plan.audio, plan.loudness = True, corr
                plan.notes += [f"8: {c['note']}" for c in corr.values()]
            else:
                plan.unrepairable[8] = ("the encoded files measure within target now, or the render's mix/finals "
                                        "are gone")
    return plan


def _restore_pins(doc: CutDocument, index: TakeIndex, fail: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """``restore_words`` for every pinned word that is not in the story, one op per contiguous source run.
    Returns ``(ops, why_not)``: ``why_not`` names pinned words a restore cannot help (kept but unmapped)."""
    kept = set(doc.kept_word_ids(index))
    pins = doc.pins.all()
    missing = sorted({w for w in pins if index.has_word(w) and w not in kept}, key=index.word_pos)
    refs = [w for w in (fail.get("refs") or []) if index.has_word(w)]
    stuck = [w for w in refs if w in kept]  # kept, yet "no output span" / "outside the video": a compile fault
    ops: list[dict[str, Any]] = []
    run: list[str] = []
    for w in missing:
        if run and index.word_pos(w) != index.word_pos(run[-1]) + 1:
            ops.append(_restore_op(run, pins))
            run = []
        run.append(w)
    if run:
        ops.append(_restore_op(run, pins))
    why = f"pinned {', '.join(stuck)} kept but not mapped into the video" if stuck else ""
    return ops, why


def _restore_op(run: list[str], pins: dict[str, str]) -> dict[str, Any]:
    kinds = "/".join(sorted({pins[w] for w in run}))
    return {"op": "restore_words", "from_word": run[0], "to_word": run[-1],
            "reason": f"repair: pinned {kinds} word(s) back in the story (invariant 4)"}


def _cut_fragments(doc: CutDocument, index: TakeIndex, fail: dict[str, Any] | None
                   ) -> tuple[list[dict[str, Any]], set[str], str, CutDocument]:
    """``cut_words`` for every cut-off fragment at a join and every kept word the recording chops, from the
    document itself (:func:`cutoffs_at_joins`, ``Word.truncated``) and from invariant 1's evidence; repeated while
    a cut exposes another fragment. Returns ``(ops, cut word ids, why_not, the dry-run document)``."""
    from studio.doc.ops import apply_ops
    from studio.doc.validate import cutoffs_at_joins

    pinned = set(doc.pins.all())
    data = (fail or {}).get("data") or {}
    named = {d.get("word_id") for k in ("cutoff_at_join", "truncated") for d in data.get(k) or []
             if isinstance(d, dict)}
    named |= {w for w in (fail or {}).get("refs") or []
              if index.has_word(w) and (index.word(w).kind == "cutoff" or index.word(w).truncated)}
    ops: list[dict[str, Any]] = []
    cut: set[str] = set()
    stuck: set[str] = set()
    work = doc
    for _ in range(_FRAGMENT_ROUNDS):
        kept = work.kept_word_ids(index)
        keep = set(kept)
        found: dict[str, str] = {}
        for c in cutoffs_at_joins(work, index):
            wid = str(c["word_id"])
            found.setdefault(wid, f"cut-off fragment {c['text']!r} at a join ({c['seg']} {c['side']})")
        for w in kept:
            if index.word(w).truncated:
                found.setdefault(w, f"{index.word(w).display()!r} chopped by a recording dropout")
        for w in named:
            if w and w in keep and index.has_word(w):
                found.setdefault(w, f"{index.word(w).display()!r} named by invariant 1")
        todo = {w: why for w, why in found.items() if w not in cut and w not in stuck}
        stuck |= {w for w in todo if w in pinned}
        todo = {w: why for w, why in todo.items() if w not in pinned}
        if not todo:
            break
        new_ops = [{"op": "cut_words", "from_word": w, "to_word": w, "reason": f"repair: {why} (invariant 1)"}
                   for w, why in sorted(todo.items(), key=lambda kv: index.word_pos(kv[0]))]
        nxt, res = apply_ops(work, new_ops, index)
        applied = [o for o, r in zip(new_ops, res, strict=True) if r.applied]
        stuck |= {o["from_word"] for o, r in zip(new_ops, res, strict=True) if not r.applied}
        if not applied:
            break
        if not nxt.segments:
            return [], set(), "cutting the fragments would empty the story", doc
        ops += applied
        cut |= {o["from_word"] for o in applied}
        work = nxt
    why = f"pinned or uncuttable fragment(s) {', '.join(sorted(stuck, key=index.word_pos))}" if stuck else ""
    return ops, cut, why, work


# ============================================================================================ captions (7)
def _caption_fix(job: Job, doc: CutDocument, work: CutDocument, index: TakeIndex, rd: Path, fail: dict[str, Any],
                 s: Settings) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Re-place every caption page invariant 7 named on ``work`` (``doc`` after any story fix). Returns ``(ops,
    ok, notes)``; ``ok`` is False when a named element is not a caption page or no clean spot exists down to the
    size floor."""
    from studio.compile import captions as cap
    from studio.compile.models import Timeline
    from studio.compile.timeline import compile as compile_timeline

    if work.captions is not None and not work.captions.enabled:
        return [], False, ["captions are off: the failing text is not a caption page"]
    tl_path = rd / "timeline.json"
    old_tl = Timeline.load(tl_path) if tl_path.exists() else compile_timeline(doc, index, job=job)
    consts = cap.load_constants(s)
    params = cap.load_caption_params(s, consts)
    platforms = _platforms(work)
    offences, other = _offences(fail, old_tl, platforms, consts, params)
    if other:
        return [], False, [f"not a caption page: {', '.join(other[:4])}"]
    if not offences:
        return [], False, ["invariant 7 names no caption page"]
    base_tl = compile_timeline(work, index, job=job)
    base_plan = _explicit_plan(work, base_tl)
    W, H = base_tl.width, base_tl.height
    zones = {p: cap.safe_zone_for(p, width=W, height=H, constants=consts) for p in platforms}
    union = cap.safe_zone_for(platforms, width=W, height=H, constants=consts)
    base_issues = _caption_issues(base_tl, index, platforms, s)
    style0 = base_plan.style
    sizes = _size_ladder(style0.size_px, params.min_size_px)
    if any(o.shrink for o in offences.values()):
        sizes = sizes[1:]
    notes: list[str] = []
    for size in sizes:
        style = style0 if size == style0.size_px else _resized(style0, size)
        trial = work.model_copy(update={"captions": base_plan.model_copy(update={"style": style})})
        tl = compile_timeline(trial, index, job=job) if trial.captions != work.captions else base_tl
        targets = _map_offences(offences, tl)
        if not targets:
            return [], True, ["the named pages are gone after the story fix"]
        placed: list[str] = []
        for pid, off in targets.items():
            got = _place_page(job, trial, index, tl, pid, off, set(targets), union, zones, params, base_issues, s)
            if got is None:
                placed = []
                notes.append(f"no clean spot for {pid} at {size} px")
                break
            trial, tl, how = got
            placed.append(how)
        if placed:
            ops = _caption_ops(work, trial.captions)
            if size != style0.size_px:
                placed.append(f"captions {style0.size_px} -> {size} px")
            return ops, True, [*notes, *placed]
    return [], False, notes


def _offences(fail: dict[str, Any], tl: Timeline, platforms: list[str], consts: dict[str, Any],
              params: CaptionParams) -> tuple[dict[str, _Offence], list[str]]:
    """Caption pages invariant 7 named (from its per-platform text issues, else the page IDs in its refs), and
    the non-caption elements it named (texts, unattributed pixels), which re-placing captions cannot fix."""
    from studio.compile.captions import RENDER_BLEED_PX, safe_zone_for

    W, H = tl.width, tl.height
    pages = {p.page_id: p for p in tl.captions}
    found: list[tuple[str | None, dict[str, Any]]] = []
    for plat, rec in ((fail.get("data") or {}).get("platforms") or {}).items():
        for it in (rec or {}).get("issues") or []:
            if isinstance(it, dict):
                found.append((plat, it))
    if not found:  # no structured evidence (an older QA record): the page IDs in the refs
        found = [(None, {"kind": "caption", "id": r, "issues": [], "box": []})
                 for r in fail.get("refs") or [] if _PAGE_RE.match(str(r)) and r in pages]
    out: dict[str, _Offence] = {}
    other: list[str] = []
    fringe = RENDER_BLEED_PX * W / 1080
    for plat, it in found:
        pid = it.get("id")
        if it.get("kind") != "caption" or pid not in pages:
            other.append(f"{it.get('kind')} {pid} {'/'.join(it.get('issues') or [])}".strip())
            continue
        pg = pages[pid]
        zone = safe_zone_for(plat or platforms, width=W, height=H, constants=consts)
        P = _planned_box(pg, W, H, zone, params)
        bh = P[3] - P[1]
        off = out.setdefault(pid, _Offence(page_id=pid, words=[w.word_id for w in pg.words],
                                           old_center=pg.y_norm * H, old_size=pg.style.size_px))
        kinds = set(it.get("issues") or [])
        off.kinds |= kinds
        if "too_wide" in kinds:
            off.shrink = True
        B = it.get("box") or []
        if len(B) != 4 or it.get("source") != "rendered":
            continue
        left, top, right, bottom = off.ext
        if "outside_safe_zone" in kinds:
            floor = H - min(zone.bottom, zone.caption_floor)
            if B[3] > floor:
                bottom = max(bottom, min(bh, B[3] - P[3]))
            if B[1] < zone.top:
                top = max(top, min(bh, P[1] - B[1]))
            if B[0] < zone.left - fringe or B[2] > W - zone.right + fringe:
                off.shrink = True
        if "covers_face" in kinds:  # glyphs on the face while the estimate was clear: the render ran past it
            left, top = max(left, min(bh, P[0] - B[0])), max(top, min(bh, P[1] - B[1]))
            right, bottom = max(right, min(bh, B[2] - P[2])), max(bottom, min(bh, B[3] - P[3]))
        off.ext = (max(0.0, left), max(0.0, top), max(0.0, right), max(0.0, bottom))
    return out, other


def _map_offences(offences: dict[str, _Offence], tl: Timeline) -> dict[str, _Offence]:
    """The offending pages in ``tl`` (by page ID when it still holds the page's words, else the page holding most
    of them: a story fix can re-page)."""
    out: dict[str, _Offence] = {}
    for off in offences.values():
        words = set(off.words)
        best, n = None, 0
        for p in tl.captions:
            k = len(words & {w.word_id for w in p.words}) + (0.5 if p.page_id == off.page_id else 0)
            if k > n and k >= 1:
                best, n = p, k
        if best is None:
            continue
        if best.page_id in out:  # two offences on one page: the stricter clearance of both
            o = out[best.page_id]
            out[best.page_id] = replace(o, kinds=o.kinds | off.kinds, shrink=o.shrink or off.shrink,
                                        ext=tuple(max(a, b) for a, b in zip(o.ext, off.ext, strict=True)))
        else:
            out[best.page_id] = replace(off, page_id=best.page_id)
    return out


def _place_page(job: Job, doc: CutDocument, index: TakeIndex, tl: Timeline, pid: str, off: _Offence,
                targets: set[str], union: SafeZone, zones: dict[str, SafeZone], params: CaptionParams,
                base_issues: set[tuple[str, str, str]], s: Settings) -> tuple[CutDocument, Timeline, str] | None:
    """A clean spot for page ``pid``: the placer's own (the page's explicit position cleared) when it is clean
    and new, else the nearest clean pinned height. Every candidate is compiled and checked; the first that passes
    returns ``(doc, timeline, note)``."""
    from studio.compile import captions as cap
    from studio.compile.timeline import compile as compile_timeline

    assert doc.captions is not None
    W, H = tl.width, tl.height
    hs = H / 1920

    def check(t: Timeline) -> bool:
        pg = next((p for p in t.captions if p.page_id == pid), None)
        if pg is None or not _page_clear(t, index, pg, off, zones, params):
            return False
        moved = abs(pg.y_norm * H - off.old_center) >= _MIN_MOVE_PX * hs or pg.style.size_px != off.old_size
        new = {i for i in _caption_issues(t, index, list(zones), s) - base_issues if i[1] not in targets}
        return moved and not new

    # 1. the placer's ladder for this page (its own spot, per-page shrink included)
    auto_doc = _with_page(doc, pid, y_norm=None, position=None)
    auto_tl = tl if auto_doc.captions == doc.captions else compile_timeline(auto_doc, index, job=job)
    auto_pg = next((p for p in auto_tl.captions if p.page_id == pid), None)
    if auto_pg is not None and check(auto_tl):
        return auto_doc, auto_tl, (f"{pid} re-placed by the placer at y {auto_pg.y_norm:.3f} "
                                   f"({auto_pg.placement or 'auto'}, {auto_pg.style.size_px} px)")
    # 2. a pinned height: other pages' heights first (fewer distinct caption heights), then the nearest to the
    #    placer's spot; pinned pages render at the style size (the placer's fixed branch never shrinks)
    pg0 = auto_pg or next(p for p in tl.captions if p.page_id == pid)
    text = pg0.text or " ".join(w.text for w in pg0.words)
    style = doc.captions.style
    fit = cap.caption_fit(style, text, W, params, max_width=W - union.left - union.right)
    if fit.width > W - union.left - union.right + 0.5:
        return None
    bh = fit.height
    pad = _PAD_PX * hs if any(off.ext) else 0.0
    lo = union.top + bh / 2 + off.ext[1] + pad
    hi = H - union.relaxed_bottom - cap.RENDER_BLEED_PX * hs - bh / 2 - off.ext[3] - pad
    if lo > hi:
        return None
    a, b = Fraction(pg0.out_start), Fraction(pg0.out_end)
    faces = _qa_faces(auto_tl, index, a, b)
    obstacles = _obstacles(auto_tl, a, b, union, params)
    start = pg0.y_norm * H
    others = [round(p.y_norm * H, 1) for p in tl.captions if p.page_id not in targets
              and p.style.size_px == style.size_px]
    seen: set[float] = set()
    ordered = sorted(set(others), key=lambda y: (-others.count(y), abs(y - start)))
    k = 0
    while start - k * _STEP_PX * hs >= lo or start + k * _STEP_PX * hs <= hi:
        ordered += [start - k * _STEP_PX * hs, start + k * _STEP_PX * hs] if k else [start]
        k += 1
    clean: list[float] = []
    soft: list[float] = []
    for c in ordered:
        c = round(c, 1)
        if c in seen or not (lo - 0.01 <= c <= hi + 0.01):
            continue
        seen.add(c)
        if style.size_px == off.old_size and abs(c - off.old_center) < _MIN_MOVE_PX * hs:
            continue
        box = _centered_box(W, union, fit.width, c, bh)
        ebox = _expand(box, off.ext, pad)
        if any(_overlap(ebox, r) > 0 for r in faces):
            continue
        (soft if any(_overlap(box, o) > 0 for o in obstacles) else clean).append(c)
        if len(clean) >= _CANDIDATES:
            break
    for c in (clean + soft)[:_CANDIDATES]:
        y = round(c / H, 4)
        d = _with_page(doc, pid, y_norm=y)
        t = compile_timeline(d, index, job=job)
        pg = next((p for p in t.captions if p.page_id == pid), None)
        if pg is None or abs(pg.y_norm - y) > 2e-3:  # the placer refused the height (face/band): not honoured
            continue
        if check(t):
            return d, t, f"{pid} pinned at y {y:.3f} ({style.size_px} px; it failed at y {off.old_center / H:.3f})"
    return None


def _page_clear(tl: Timeline, index: TakeIndex, pg: TimelineCaptionPage, off: _Offence,
                zones: dict[str, SafeZone], params: CaptionParams) -> bool:
    """QA's planned check for one page on every deliverable platform (safe zone with the caption floor, too wide,
    the eyes-to-mouth box after framing at 0.1 s steps), on the planned box widened by what the render drew past
    it on the failed render."""
    W, H = tl.width, tl.height
    pad = _PAD_PX * H / 1920 if any(off.ext) else 0.0
    faces = _qa_faces(tl, index, Fraction(pg.out_start), Fraction(pg.out_end))
    for zone in zones.values():
        P = _planned_box(pg, W, H, zone, params)
        if P[2] - P[0] > W - zone.left - zone.right + 0.5:
            return False
        E = _expand(P, off.ext, pad)
        if E[1] < zone.top - 0.5 or E[3] > H - min(zone.bottom, zone.caption_floor) + 0.5:
            return False
        if any(_overlap(E, r) > 0 for r in faces):
            return False
    return True


def _caption_issues(tl: Timeline, index: TakeIndex, platforms: list[str], s: Settings) -> set[tuple[str, str, str]]:
    """``{(platform, page_id, issue)}`` from QA's planned caption check."""
    from studio.qa.metrics import planned_text_boxes

    out: set[tuple[str, str, str]] = set()
    for plat in platforms:
        for b in planned_text_boxes(tl, index, plat, settings=s):
            if b.kind == "caption":
                out |= {(plat, b.id, i) for i in b.issues}
    return out


def _qa_faces(tl: Timeline, index: TakeIndex, a: Fraction, b: Fraction) -> list[tuple[float, float, float, float]]:
    """QA's eyes-to-mouth boxes over ``[a, b)`` (the 0.1 s sampling of ``planned_text_boxes``)."""
    from studio.qa.metrics import face_protected_rect

    out = []
    t, step = a, Fraction(1, 10)
    times: list[Fraction] = []
    while t < b:
        times.append(t)
        t += step
    times.append(max(a, b - Fraction(1, 1000)))
    for t in times:
        r = face_protected_rect(tl, index, t)
        if r is not None:
            out.append(r)
    return out


def _obstacles(tl: Timeline, a: Fraction, b: Fraction, zone: SafeZone, params: CaptionParams
               ) -> list[tuple[float, float, float, float]]:
    """Other overlays on screen during the page (the placer's own notion); a soft preference only."""
    from studio.compile import captions as cap

    try:
        return list(cap._obstacles(tl, a, b, zone, params))
    except Exception:  # pragma: no cover - an internal helper's signature drift must not block a repair
        return []


def _planned_box(pg: TimelineCaptionPage, W: int, H: int, zone: SafeZone, params: CaptionParams
                 ) -> tuple[float, float, float, float]:
    """QA's estimated caption block (``metrics._caption_box``): the caption fit centred on the page's y and
    clamped horizontally into the band."""
    from studio.compile.captions import caption_fit

    text = pg.text or " ".join(w.text for w in pg.words)
    fit = caption_fit(pg.style, text, W, params, max_width=W - zone.left - zone.right)
    return _centered_box(W, zone, fit.width, pg.y_norm * H, fit.height)


def _centered_box(W: int, zone: SafeZone, bw: float, cy: float, bh: float) -> tuple[float, float, float, float]:
    left, right = zone.left, W - zone.right
    cx = min(max(W / 2, left + bw / 2), right - bw / 2) if bw <= right - left + 0.5 else W / 2
    return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def _expand(box: tuple[float, float, float, float], ext: tuple[float, float, float, float], pad: float
            ) -> tuple[float, float, float, float]:
    return (box[0] - ext[0] - (pad if ext[0] else 0.0), box[1] - ext[1] - (pad if ext[1] else 0.0),
            box[2] + ext[2] + (pad if ext[2] else 0.0), box[3] + ext[3] + (pad if ext[3] else 0.0))


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _platforms(doc: CutDocument) -> list[str]:
    return list(dict.fromkeys(d.platform for d in doc.deliverables)) or ["tiktok"]


def _explicit_plan(doc: CutDocument, tl: Timeline) -> CaptionPlan:
    """The document's caption plan with explicit pages: its own, or the compiled automatic pages written out
    (same IDs, words, accents and display text) so single pages can be addressed."""
    from studio.doc.model import CaptionPage, CaptionPlan, CaptionStyle

    if doc.captions is not None and doc.captions.pages:
        return doc.captions
    pages = [CaptionPage(id=p.page_id, word_ids=[w.word_id for w in p.words],
                         emphasis_word_ids=[w.word_id for w in p.words if w.emphasis], text=p.text)
             for p in tl.captions]
    style = doc.captions.style if doc.captions is not None else CaptionStyle()
    position = doc.captions.position if doc.captions is not None else "auto"
    return CaptionPlan(enabled=True, pages=pages, style=style, position=position)


def _with_page(doc: CutDocument, pid: str, **update: Any) -> CutDocument:
    assert doc.captions is not None
    pages = [p.model_copy(update=update) if p.id == pid else p for p in doc.captions.pages]
    return doc.model_copy(update={"captions": doc.captions.model_copy(update={"pages": pages})})


def _size_ladder(size: int, floor: float) -> list[int]:
    """Caption sizes to try: the current one, the placer's 0.9/0.8 steps, then the legibility floor."""
    fl = math.ceil(floor)
    out = [size]
    for f in _SIZE_FACTORS:
        v = round(size * f)
        if fl < v < out[-1]:
            out.append(v)
    if fl < out[-1]:
        out.append(fl)
    return out


def _resized(style: CaptionStyle, size: int) -> CaptionStyle:
    k = size / style.size_px if style.size_px else 1.0
    return style.model_copy(update={"size_px": size, "stroke_px": round(style.stroke_px * k, 1)})


def _caption_ops(doc: CutDocument, plan: CaptionPlan | None) -> list[dict[str, Any]]:
    """The smallest op batch turning ``doc``'s captions into ``plan``: page ``y_norm`` pins and a style change as
    ``edit_caption_page`` / ``set_caption_style`` when the document already lists the same pages, else one
    ``set_captions``. Nothing when only the automatic pages were written out."""
    old = doc.captions
    if plan is None or old == plan:
        return []

    def key(p: Any) -> tuple[Any, ...]:
        return p.id, tuple(p.word_ids), tuple(p.emphasis_word_ids), p.text

    if old is None or not old.pages:
        pinned = any(p.y_norm is not None or p.position is not None for p in plan.pages)
        if not pinned and plan.style == (old.style if old is not None else plan.style):
            return []  # the automatic pages, written out: no change to make
        return [{"op": "set_captions", "plan": plan.model_dump(mode="json")}]
    if [key(p) for p in old.pages] != [key(p) for p in plan.pages] or (old.enabled, old.position) != (
            plan.enabled, plan.position):
        return [{"op": "set_captions", "plan": plan.model_dump(mode="json")}]
    ops: list[dict[str, Any]] = []
    if old.style != plan.style:
        ops.append({"op": "set_caption_style", "style": plan.style.model_dump(mode="json")})
    for po, pn in zip(old.pages, plan.pages, strict=True):
        if (po.y_norm, po.position) == (pn.y_norm, pn.position):
            continue
        if pn.y_norm is None:  # back to the placer: edit_caption_page cannot clear a field
            return [{"op": "set_captions", "plan": plan.model_dump(mode="json")}]
        ops.append({"op": "edit_caption_page", "page_id": pn.id, "y_norm": pn.y_norm})
    return ops


# ============================================================================================ loudness (8)
def _loudness_corrections(doc: CutDocument, rd: Path, s: Settings) -> dict[str, dict[str, Any]]:
    """Per mix (``mix.wav`` -> the primary final, ``mix_nomusic.wav`` -> ``final_nomusic.mp4``) whose encoded file
    misses the loudness target or the true-peak ceiling, measured with QA's meters: the re-master target and
    ceiling that land the encoded file on target, from the codec's measured offset (encoded minus mix). Empty when
    nothing needs it (or the files are gone)."""
    from studio.compile import audio as caudio
    from studio.qa import metrics as qm

    target, ceiling = float(doc.audio.loudness_target_lufs), float(doc.audio.true_peak_dbtp)
    tol = float(qm.prior(qm.load_priors(s), "loudness.gate_tolerance_lu", 1.0))
    platforms = _platforms(doc)
    out: dict[str, dict[str, Any]] = {}
    for mix_name, final_name in (("mix.wav", f"final_{platforms[0]}.mp4"), ("mix_nomusic.wav", "final_nomusic.mp4")):
        mix, final = rd / mix_name, rd / final_name
        if not (mix.exists() and final.exists()):
            continue
        enc = qm.measure_loudness(qm.decode_final_audio(final), path=final, target_lufs=target,
                                  ceiling_dbtp=ceiling, tolerance_lu=tol)
        if enc.integrated_lufs is None or enc.true_peak_dbtp is None or (enc.within_target and enc.true_peak_ok):
            continue
        x = _read_mix(mix)
        mix_lufs = caudio.integrated_loudness(x, 48_000)
        if not math.isfinite(mix_lufs):
            continue
        over = max(0.0, float(enc.true_peak_dbtp) - caudio.true_peak_dbtp(x, 48_000))  # the codec's peak overshoot
        new_target = target - (float(enc.integrated_lufs) - mix_lufs)
        new_ceiling = min(ceiling - caudio.MixParams().true_peak_margin_db, ceiling - over - _TP_MARGIN_DB)
        new_ceiling = max(new_ceiling, ceiling - _MAX_CEILING_DROP_DB)
        out[mix_name] = {"final": final_name, "target": round(new_target, 3), "ceiling": round(new_ceiling, 3),
                         "note": f"{final_name} measures {enc.integrated_lufs} LUFS / {enc.true_peak_dbtp} dBTP: "
                                 f"re-master {mix_name} to {new_target:.2f} LUFS, ceiling {new_ceiling:.2f} dBTP"}
    return out


def _read_mix(path: Path) -> Any:
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float64", always_2d=True)
    if sr != 48_000:
        raise RuntimeError(f"{path.name} is {sr} Hz, not 48 kHz")
    return np.ascontiguousarray(x.T)


def _remaster(corr: dict[str, dict[str, Any]], src_dir: Path, out_dir: Path) -> list[str]:
    """Re-master each corrected mix from ``src_dir`` into ``out_dir``. The source is read before the target is
    replaced (a new file: a hard link to the failed render's mix is never written through)."""
    from studio.audio.voice import write_audio
    from studio.compile import audio as caudio

    done = []
    for mix_name, c in corr.items():
        x = _read_mix(src_dir / mix_name)
        y, _info = caudio.master_loudness(x, 48_000, target_lufs=c["target"], ceiling_dbtp=c["ceiling"])
        dst = out_dir / mix_name
        dst.unlink(missing_ok=True)
        write_audio(dst, y if y.shape[0] > 1 else y[0], 48_000)
        done.append(mix_name)
    return done


def _mux(job: Job, video_src: Path, wav: Path, out: Path, timeline: Timeline) -> Path:
    """``out`` = ``video_src``'s encoded picture (stream-copied) + ``wav`` encoded exactly as the master stage
    muxes it (:func:`studio.compile.master.master`: priming pre-trimmed, padded to the picture, no edit list,
    faststart), then the master's delivery checks. ``out`` may be ``video_src`` (written beside, then replaced)."""
    from studio.compile import master as m
    from studio.compile.video import FFMPEG
    from studio.timebase import sample_index

    total = sample_index(timeline.duration, 48_000)
    enc = m.aac_encoder()
    priming = m.aac_priming(enc)
    tmp = out.with_name(f".{out.stem}.muxing.mp4")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video_src), "-i", str(wav),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", m._audio_chain(wav, total, priming),
           *m._aac_args(enc, mono=m._channels(wav) == 1), "-ar", "48000", "-ac", "2", *m._MUXFLAGS,
           "-metadata:s:a:0", "language=und", str(tmp)]
    try:
        m._run(cmd, job.log_path("master"), f"mux {out.name}")
        m._verify_delivery(tmp, timeline, (timeline.width, timeline.height))
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


# ============================================================================================ re-render
def _rerender(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, mode: str, *, profile: str, remaster: bool,
              corrections: dict[str, dict[str, Any]], settings: Settings, notes: list[str]) -> tuple[Path, str]:
    """Exactly one re-render: the partial pass for ``mode`` (a full render when it cannot run), or a full render.
    ``remaster``: invariant 8 failed; a partial pass applies ``corrections`` (measured on the failed render), a full
    render measures its own encoded file and corrects its mixes in place only when that file still misses."""
    if mode in ("overlay", "audio"):
        try:
            if mode == "overlay":
                return _overlay_render(job, doc, index, rd, profile=profile, corrections=corrections,
                                       notes=notes), mode
            return _audio_render(job, doc, rd, corrections=corrections, notes=notes), mode
        except _NeedFull as e:
            notes.append(f"{mode} pass not possible ({e}): full render instead")
    new_rd = _full_render(job, doc, index, profile=profile)
    if remaster:
        _correct_in_place(job, doc, new_rd, settings, notes)
    return new_rd, "full"


def _full_render(job: Job, doc: CutDocument, index: TakeIndex, *, profile: str) -> Path:
    from studio.agent import loop

    return loop.render_full(job, doc, index, profile=profile)


def _evaluate(job: Job, rd: Path, s: Settings) -> tuple[bool, list[dict[str, Any]]]:
    from studio.qa import invariants as inv

    run = inv.evaluate_render(job, rd, settings=s)
    return run.passed, [r.to_dict() for r in run.results if not r.passed]


def _picture_sound(tl: Timeline) -> dict[str, Any]:
    """Everything in a timeline the A-roll and the mix depend on (all but the captions and the version)."""
    return tl.model_dump(mode="json", exclude={"captions", "doc_version"})


def _manifest(rd: Path) -> dict[str, Any]:
    with contextlib.suppress(OSError, ValueError):
        return dict(json.loads((rd / "render.json").read_text(encoding="utf-8")))
    return {}


def _link(src: Path, dst: Path) -> None:
    """Hard-link (copy across devices) a file or a directory tree of files."""
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for p in src.iterdir():
            _link(p, dst / p.name)
        return
    dst.unlink(missing_ok=True)
    try:
        dst.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dst)


def _keep_reclaim(job: Job, *keep: Path) -> Callable[[int], int]:
    """Disk reclaim for a partial pass: every other render's intermediates, never the ones it reads or writes."""
    from studio import storage

    names = frozenset(p.name for p in keep)

    def run(_short: int) -> int:
        return storage.reclaim_job(job, keep_intermediates=names, reason="a repair render needs the space")
    return run


def _link_asr_cache(old: Path, new: Path) -> int:
    """Carry the failed render's QA ASR raw responses (``qa/asr_*/asr_raw_*.json``, keyed on the SHA-256 of the
    audio sent) into the new render: an overlay pass re-uses the same mix, so when the re-encoded audio decodes to
    the same samples the round trip re-parses the cache instead of paying Scribe again for identical audio (and pays
    as before when it does not). Only the JSON (written atomically, so a hard link is never rewritten in place) is
    linked; the work WAV is written fresh. Returns the number of files linked."""
    n = 0
    qa = old / "qa"
    if not qa.is_dir():
        return 0
    for d in qa.glob("asr_*"):
        if not d.is_dir():
            continue
        for f in d.glob("asr_raw_*.json"):
            (new / "qa" / d.name).mkdir(parents=True, exist_ok=True)
            _link(f, new / "qa" / d.name / f.name)
            n += 1
    return n


def _overlay_render(job: Job, doc: CutDocument, index: TakeIndex, old: Path, *, profile: str,
                    corrections: dict[str, dict[str, Any]], notes: list[str]) -> Path:
    """Captions changed and nothing else: a new render dir holding the failed render's ``aroll.mov`` and mixes
    (hard links), the document's recompiled timeline, a new overlay layer and the master encode at ``profile``
    (with the mixes re-mastered first when ``corrections`` name them)."""
    from studio import storage
    from studio.compile import master as master_mod
    from studio.compile import overlays as overlays_mod
    from studio.compile.models import Timeline
    from studio.compile.timeline import compile as compile_timeline

    gone = [n for n in ("aroll.mov", "mix.wav", "timeline.json") if not (old / n).exists()]
    if gone:
        raise _NeedFull(f"{', '.join(gone)} no longer in {old.name}")
    old_tl = Timeline.load(old / "timeline.json")
    timeline = compile_timeline(doc, index, job=job)
    if _picture_sound(timeline) != _picture_sound(old_tl):
        raise _NeedFull("the recompiled cut differs from the render's")
    old_doc = None
    with contextlib.suppress(Exception):
        old_doc = job.load_doc(old_tl.doc_version)
    if old_doc is not None and (old_doc.color, old_doc.audio) != (doc.color, doc.audio):
        raise _NeedFull("colour or audio changed")
    platforms = _platforms(doc)
    man = _manifest(old)
    t0 = time.monotonic()
    with storage.render_slot():
        new = job.new_render_dir()
        try:
            need = storage.render_need_bytes(float(timeline.duration), width=timeline.width, height=timeline.height,
                                             fps=float(timeline.fps), aroll="lean", finals=len(platforms) + 1)
            storage.ensure_space(job.root, need, what="a caption repair render", reclaim=_keep_reclaim(job, old, new),
                                 work_dir=job.root.parent, exclude=job.root)
            for n in ("aroll.mov", "mix.wav", "mix_nomusic.wav", "stems", "audio_report.json"):
                if (old / n).exists():
                    _link(old / n, new / n)
            if not corrections:
                _link_asr_cache(old, new)
            timeline.save(new / "timeline.json")
            ovl = overlays_mod.render_overlays(job, timeline, new, index=index, platforms=platforms)
            if corrections:
                notes += [f"8: re-mastered {n}" for n in _remaster(corrections, old, new)]
            nomusic = new / "mix_nomusic.wav"
            res = master_mod.master(job, new / "aroll.mov", ovl, new / "mix.wav",
                                    nomusic if nomusic.exists() else None, platforms, timeline=timeline,
                                    render_dir=new, profile=profile, index=index)
            _write_manifest(new, doc, timeline, profile=profile, finals=res.finals, nomusic=res.nomusic,
                            cover=res.cover, srt=res.srt, overlays=ovl, aroll_codec=man.get("aroll_codec"),
                            seconds=time.monotonic() - t0, repair={"from": old.name, "mode": "overlay"})
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
    notes.append(f"overlay + master re-rendered into {new.name} on {old.name}'s A-roll and mix")
    job.trace("render_done", render=new.name, doc_version=doc.version, preview=False, profile=profile,
              seconds=round(time.monotonic() - t0, 1), aroll_codec=man.get("aroll_codec"), repair="overlay")
    return new


def _audio_render(job: Job, doc: CutDocument, old: Path, *, corrections: dict[str, dict[str, Any]],
                  notes: list[str]) -> Path:
    """Loudness only: a new render dir with the failed render's picture and every other file (hard links), the
    corrected mixes re-mastered and their finals re-muxed (the encoded picture stream is copied)."""
    from studio import storage
    from studio.compile.models import Timeline

    platforms = _platforms(doc)
    finals = {p: old / f"final_{p}.mp4" for p in platforms}
    gone = [p.name for p in (*finals.values(), old / "mix.wav", old / "timeline.json") if not p.exists()]
    if gone:
        raise _NeedFull(f"{', '.join(gone)} no longer in {old.name}")
    if not corrections:
        raise _NeedFull("no loudness correction measured")
    timeline = Timeline.load(old / "timeline.json")
    man = _manifest(old)
    t0 = time.monotonic()
    with storage.render_slot():
        new = job.new_render_dir()
        try:
            for n in ("aroll.mov", "overlays.mov", "overlay_props.json", "mix.wav", "mix_nomusic.wav", "stems",
                      "audio_report.json", "timeline.json", "cover.jpg", "captions.srt", "final_nomusic.mp4",
                      *(f.name for f in finals.values())):
                if (old / n).exists():
                    _link(old / n, new / n)
            notes += [f"8: re-mastered {n}" for n in _remaster(corrections, old, new)]
            if "mix.wav" in corrections:
                for p, src in finals.items():
                    _mux(job, src, new / "mix.wav", new / f"final_{p}.mp4", timeline)
            if "mix_nomusic.wav" in corrections and (new / "final_nomusic.mp4").exists():
                _mux(job, finals[platforms[0]], new / "mix_nomusic.wav", new / "final_nomusic.mp4", timeline)
            man.update({"seconds": round(time.monotonic() - t0, 2), "repair": {"from": old.name, "mode": "audio"}})
            (new / "render.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
    notes.append(f"audio re-mastered and re-muxed into {new.name} on {old.name}'s picture")
    job.trace("render_done", render=new.name, doc_version=doc.version, preview=False, profile=man.get("profile"),
              seconds=round(time.monotonic() - t0, 1), repair="audio")
    return new


def _correct_in_place(job: Job, doc: CutDocument, rd: Path, s: Settings, notes: list[str]) -> None:
    """After a full repair render: when its own encoded file still misses the loudness target, re-master its
    mixes and re-mux its finals in place (the picture stream is copied)."""
    from studio.compile.models import Timeline

    corr = _loudness_corrections(doc, rd, s)
    if not corr:
        notes.append("8: the full re-render measures within the loudness target")
        return
    timeline = Timeline.load(rd / "timeline.json")
    platforms = _platforms(doc)
    _remaster(corr, rd, rd)
    primary = rd / f"final_{platforms[0]}.mp4"
    if "mix_nomusic.wav" in corr and (rd / "final_nomusic.mp4").exists():
        _mux(job, primary, rd / "mix_nomusic.wav", rd / "final_nomusic.mp4", timeline)
    if "mix.wav" in corr:
        for p in platforms:
            final = rd / f"final_{p}.mp4"
            if final.exists():
                _mux(job, final, rd / "mix.wav", final, timeline)
    notes += [f"8: {c['note']}" for c in corr.values()]


def _write_manifest(rd: Path, doc: CutDocument, timeline: Timeline, *, profile: str, finals: dict[str, Path],
                    nomusic: Path | None, cover: Path | None, srt: Path | None, overlays: Path | None,
                    aroll_codec: Any, seconds: float, repair: dict[str, Any]) -> Path:
    """``render.json`` in :func:`studio.compile.master.render_document`'s shape, plus where the repair came from.
    Written last: a render dir without it is an interrupted render and is removed on resume."""
    man = {
        "doc_version": doc.version, "job_id": timeline.job_id, "preview": False, "profile": profile,
        "frames": timeline.frame_count, "fps": f"{timeline.fps.numerator}/{timeline.fps.denominator}",
        "duration": float(timeline.duration), "finals": {k: v.name for k, v in finals.items()},
        "nomusic": nomusic.name if nomusic else None, "cover": cover.name if cover else None,
        "srt": srt.name if srt else None, "aroll": "aroll.mov", "aroll_codec": aroll_codec,
        "overlays": overlays.name if overlays else None, "mix": "mix.wav", "seconds": round(seconds, 2),
        "repair": repair,
    }
    p = rd / "render.json"
    p.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return p
