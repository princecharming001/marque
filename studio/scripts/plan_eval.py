#!/usr/bin/env python3
"""Offline planner eval: replay finished jobs through the standard tier's keyless half and compare the result with
the document the Director delivered (standard-tier plan §3 step 11).

Why a replay and not a live run: the standard tier's quality question is "how far is one code plan (plus, later, one
planner call) from the reviewed agentic edit?", and every input to that question is already on disk for the real
jobs: the Take Index, the delivered CutDocument and, once credits exist, the planner's ``logs/plan.json``. Nothing
here calls a model, renders or touches media, so it runs in seconds and can be re-run after every code-plan change.

Per job (a directory with ``index/take_index.json`` and a reference document):

* **reference**: the delivered Director document (the highest ``deliver/edit_v*.json``); a job that never delivered
  falls back to the champion loop's ``champion_doc`` (``logs/loop_state.json`` -> ``doc/v{n}.json``), marked
  ``champion``.
* **digest**: :func:`studio.plan.digest.build_digest` for the job's platform/brief/style; its size in estimated tokens
  (``len / 3.5``) and the sentences it had to omit.
* **code plan**: :func:`studio.plan.code_plan.code_plan` with ``decision=None`` applied to a fresh document (imported
  lazily: a missing or broken module is reported as "code plan unavailable", not a crash).
* **comparison** with the reference: kept-word Jaccard (spoken words; lexical words alone too), the reference's
  payoff/CTA pins still kept, the same out-point (last spoken word) and in-point, sentences dropped by one side only
  with each side's ``removed`` reason, caption size/pages/placer placements (from a job-less compile), estimated and
  compiled output duration, and the listener's radio blockers on both.
* **planner decision**: when ``logs/plan.json`` holds a decision, :func:`studio.plan.decision.validate_decision`
  (rejections, unknown-ID count), the decision's own agreement with the reference (story Jaccard, drops, payoff/CTA,
  emphasis, punch-in, structure markers, take choices) and the code plan built from it, compared the same way.

Output: a table on stdout (``--json``: the records as JSON instead) and ``<job>/logs/plan_eval.json`` per job
(``--no-write`` skips it). The exit status is non-zero only when the harness itself crashed on a job (an unexpected
exception; the job's record carries the traceback); disagreement is a measurement, never a failure, and a code plan
that refuses a take (``CodePlanError``) is a recorded outcome.

Usage (from the repo root)::

    .venv/bin/python scripts/plan_eval.py                      # every job under the work dir with a reference
    .venv/bin/python scripts/plan_eval.py /root/studio-work/20261008-225344-08453c --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex
    from studio.plan.decision import PlanDecision

__all__ = [
    "EVAL_LOG", "FALLBACK_WORK_DIR", "PLAN_LOG", "DocView", "Reference", "compare_views", "discover_jobs",
    "evaluate_decision", "evaluate_job", "find_reference", "format_table", "jaccard", "main", "run_code_plan",
    "view_doc",
]

#: Where each job's eval record is written (relative to the job root).
EVAL_LOG = "logs/plan_eval.json"
#: The planner's record (``studio.plan.planner.PLAN_LOG``, repeated so the harness never imports the planner).
PLAN_LOG = "logs/plan.json"
#: Scanned when no job is named and the configured work dir does not exist (the dev box's real jobs live here).
FALLBACK_WORK_DIR = Path("/root/studio-work")
#: Transcript excerpts in the report are cut to this many characters.
_EXCERPT_CHARS = 70
#: The planner's per-call token counts the eval carries over from ``logs/plan.json``.
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


# ============================================================================================ reference document
@dataclass
class Reference:
    doc: CutDocument
    path: Path
    kind: str  # "delivered" (deliver/edit_v*.json) or "champion" (the loop's champion doc, never delivered)
    version: int

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version, "path": str(self.path)}


def _edit_version(p: Path) -> int:
    try:
        return int(p.stem.removeprefix("edit_v"))
    except ValueError:
        return -1


def _reference_path(root: Path) -> tuple[Path, str] | None:
    delivered = sorted((p for p in (root / "deliver").glob("edit_v*.json") if _edit_version(p) >= 0),
                       key=_edit_version)
    if delivered:
        return delivered[-1], "delivered"
    state = root / "logs" / "loop_state.json"
    if state.is_file():
        try:
            v = json.loads(state.read_text(encoding="utf-8")).get("champion_doc")
        except (OSError, ValueError):
            v = None
        if isinstance(v, int) and (root / "doc" / f"v{v}.json").is_file():
            return root / "doc" / f"v{v}.json", "champion"
    return None


def find_reference(root: Path) -> Reference | None:
    """The document the job shipped (or its champion when it never delivered); None when it has neither."""
    from studio.doc.model import CutDocument

    found = _reference_path(root)
    if found is None:
        return None
    path, kind = found
    doc = CutDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return Reference(doc=doc, path=path, kind=kind, version=doc.version)


def discover_jobs(work_dir: Path) -> list[Path]:
    """Job directories under ``work_dir`` with a Take Index and a reference document, in name order."""
    if not work_dir.is_dir():
        return []
    return [p for p in sorted(work_dir.iterdir())
            if p.is_dir() and (p / "index" / "take_index.json").is_file() and _reference_path(p) is not None]


# ============================================================================================ document views
@dataclass
class DocView:
    """What the eval reads off one document: its story as word-ID sets, and the handful of decisions it is compared
    on. Built once per document by :func:`view_doc`; :meth:`to_json` is the record form (no full ID lists)."""

    kept: list[str]  # kept word IDs in output order
    spoken: set[str]  # kept words that are spoken (lexical, filler, cut-off)
    lexical: set[str]  # kept lexical words only
    in_point: str | None
    out_point: str | None
    pins: dict[str, list[str]]  # payoff / cta / must_keep
    dropped: dict[str, str]  # sentence id -> the removed reason (no word of it kept)
    trimmed: dict[str, tuple[list[str], str]]  # partly kept sentence id -> (its spoken words removed, reason)
    removed_ranges: int
    captions: dict[str, Any]
    emphasis: list[str]
    estimated_s: float
    compiled_s: float | None
    punch_ins: list[dict[str, Any]]
    texts: list[str]
    radio: list[str]
    notes: list[str] = field(default_factory=list)

    def to_json(self, ix: TakeIndex) -> dict[str, Any]:
        return {
            "kept_spoken": len(self.spoken), "kept_lexical": len(self.lexical),
            "in_point": _word_label(ix, self.in_point), "out_point": _word_label(ix, self.out_point),
            "pins": self.pins,
            "dropped_sentences": [{"id": sid, "text": _excerpt(ix.sentence_map[sid].text), "reason": why}
                                  for sid, why in self.dropped.items()],
            "trimmed_sentences": [{"id": sid, "removed_words": len(lost), "reason": why}
                                  for sid, (lost, why) in self.trimmed.items()],
            "removed_ranges": self.removed_ranges,
            "captions": self.captions,
            "estimated_s": round(self.estimated_s, 2),
            "compiled_s": None if self.compiled_s is None else round(self.compiled_s, 3),
            "punch_ins": self.punch_ins, "texts": self.texts,
            "radio_blockers": self.radio, "notes": self.notes,
        }


def _excerpt(text: str, n: int = _EXCERPT_CHARS) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 3].rstrip() + "..."


def _word_label(ix: TakeIndex, wid: str | None) -> str | None:
    if wid is None:
        return None
    return f"{wid} {ix.word(wid).display()}" if ix.has_word(wid) else wid


def _removed_reasons(doc: CutDocument, ix: TakeIndex) -> dict[str, str]:
    """``{word id: reason}`` from the document's ``removed`` ranges (the last range naming a word wins)."""
    out: dict[str, str] = {}
    for r in doc.removed:
        if ix.has_word(r.from_word) and ix.has_word(r.to_word) and ix.word_pos(r.from_word) <= ix.word_pos(r.to_word):
            for wid in ix.word_ids(r.from_word, r.to_word):
                out[wid] = r.reason or "(no reason given)"
    return out


def _compile(doc: CutDocument, ix: TakeIndex, notes: list[str]) -> Timeline | None:
    """A job-less compile (word spans and the caption placer on the index's face track); None, noted, on failure."""
    try:
        from studio.compile.timeline import compile as compile_timeline

        return compile_timeline(doc, ix, job=None)
    except Exception as e:  # a document the compiler refuses is a finding about the document, not a harness crash
        notes.append(f"compile failed: {type(e).__name__}: {_excerpt(str(e), 160)}")
        return None


def _captions(doc: CutDocument, tl: Timeline | None) -> tuple[dict[str, Any], list[str]]:
    c = doc.captions
    if c is None or not c.enabled or not c.pages:
        return {"enabled": False}, []
    emphasis = [w for p in c.pages for w in p.emphasis_word_ids]
    out: dict[str, Any] = {"enabled": True, "size_px": c.style.size_px, "pages": len(c.pages), "position": c.position,
                           "page_position_overrides": dict(Counter(p.position for p in c.pages if p.position)),
                           "emphasis_words": len(emphasis)}
    if tl is not None and tl.captions:
        out["placements"] = dict(Counter(p.placement or "unknown" for p in tl.captions).most_common())
        out["rendered_sizes_px"] = sorted({p.style.size_px for p in tl.captions})
    return out, emphasis


def view_doc(doc: CutDocument, ix: TakeIndex) -> DocView:
    """Read ``doc`` against ``ix`` (see :class:`DocView`). Never raises for a compile problem (noted instead)."""
    from studio.doc.validate import radio_blockers

    notes: list[str] = []
    kept = doc.kept_word_ids(ix)
    kept_set = set(kept)
    wmap = ix.word_map
    spoken = {w for w in kept_set if wmap[w].is_spoken}
    lexical = {w for w in kept_set if wmap[w].kind == "word"}
    spoken_order = [w for w in kept if wmap[w].is_spoken]
    reasons = _removed_reasons(doc, ix)
    dropped: dict[str, str] = {}
    trimmed: dict[str, tuple[list[str], str]] = {}
    for s in ix.sentences:
        lost = [w for w in s.word_ids if w not in kept_set and wmap[w].is_spoken]
        if not lost:
            continue
        why = next((reasons[w] for w in lost if w in reasons), "(no removed range names it)")
        if not any(w in kept_set for w in s.word_ids):
            dropped[s.id] = why
        else:
            trimmed[s.id] = (lost, why)
    tl = _compile(doc, ix, notes)
    captions, emphasis = _captions(doc, tl)
    punch_ins = [{"segment": s.id, "scale": s.framing.scale, "anchor_word": s.framing.anchor_word,
                  "first_word": s.from_word}
                 for s in doc.segments if s.framing is not None and s.framing.scale > 1.0 + 1e-6]
    try:
        radio = radio_blockers(doc, ix)
    except Exception as e:  # the listener's gate on a malformed story is a finding, not a crash
        radio = [f"radio_blockers failed: {type(e).__name__}: {e}"]
    return DocView(
        kept=kept, spoken=spoken, lexical=lexical,
        in_point=spoken_order[0] if spoken_order else None, out_point=spoken_order[-1] if spoken_order else None,
        pins={"payoff": list(doc.pins.payoff_word_ids), "cta": list(doc.pins.cta_word_ids),
              "must_keep": list(doc.pins.must_keep_word_ids)},
        dropped=dropped, trimmed=trimmed, removed_ranges=len(doc.removed), captions=captions, emphasis=emphasis,
        estimated_s=doc.estimated_duration_us(ix) / 1e6 if doc.segments else 0.0,
        compiled_s=float(tl.duration) if tl is not None else None,
        punch_ins=punch_ins, texts=[f"{t.kind}:{t.text}" for t in doc.texts], radio=radio, notes=notes)


# ============================================================================================ comparison
def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """``|A ∩ B| / |A ∪ B|``; two empty sets agree completely (1.0)."""
    sa, sb = set(a), set(b)
    union = sa | sb
    return 1.0 if not union else len(sa & sb) / len(union)


def _spans(ids: Iterable[str], ix: TakeIndex) -> list[dict[str, Any]]:
    """Word IDs grouped into source-contiguous runs with their transcript, e.g. ``w0001-w0002 "Part one."``."""
    pos = sorted(ix.word_pos(w) for w in ids if ix.has_word(w))
    runs: list[list[int]] = []
    for p in pos:
        if runs and p == runs[-1][-1] + 1:
            runs[-1].append(p)
        else:
            runs.append([p])
    out = []
    for run in runs:
        ws = [ix.words[p] for p in run]
        out.append({"from": ws[0].id, "to": ws[-1].id, "words": len(ws),
                    "text": _excerpt(" ".join(w.display() for w in ws), 60)})
    return out


def _pins_kept(pins: Sequence[str], kept: set[str]) -> dict[str, Any]:
    missing = [w for w in pins if w not in kept]
    return {"pinned": len(pins), "kept": len(pins) - len(missing), "missing": missing}


def compare_views(ref: DocView, cand: DocView, ix: TakeIndex) -> dict[str, Any]:
    """How far ``cand`` is from ``ref`` (the delivered document) on the metrics in the module docstring."""
    cand_kept = set(cand.kept)
    payoff = _pins_kept(ref.pins["payoff"], cand_kept)
    cta = _pins_kept(ref.pins["cta"], cand_kept)
    both = [sid for sid in ref.dropped if sid in cand.dropped]
    rc, cc = ref.captions, cand.captions
    return {
        "jaccard": round(jaccard(ref.spoken, cand.spoken), 4),
        "jaccard_lexical": round(jaccard(ref.lexical, cand.lexical), 4),
        "only_in_reference": _spans(ref.spoken - cand.spoken, ix),
        "only_in_candidate": _spans(cand.spoken - ref.spoken, ix),
        "pins_kept": {"payoff": payoff, "cta": cta,
                      "all": payoff["kept"] + cta["kept"] == payoff["pinned"] + cta["pinned"]},
        "same_out_point": ref.out_point == cand.out_point,
        "out_point": {"reference": _word_label(ix, ref.out_point), "candidate": _word_label(ix, cand.out_point)},
        "same_in_point": ref.in_point == cand.in_point,
        "in_point": {"reference": _word_label(ix, ref.in_point), "candidate": _word_label(ix, cand.in_point)},
        "dropped_sentences": {
            "both": both,
            "only_reference": {sid: ref.dropped[sid] for sid in ref.dropped if sid not in cand.dropped},
            "only_candidate": {sid: cand.dropped[sid] for sid in cand.dropped if sid not in ref.dropped},
        },
        "trimmed_sentences": {
            "both_same_words": [sid for sid in ref.trimmed
                                if sid in cand.trimmed and set(ref.trimmed[sid][0]) == set(cand.trimmed[sid][0])],
            "both_other_words": [sid for sid in ref.trimmed
                                 if sid in cand.trimmed and set(ref.trimmed[sid][0]) != set(cand.trimmed[sid][0])],
            "only_reference": {sid: why for sid, (_l, why) in ref.trimmed.items() if sid not in cand.trimmed},
            "only_candidate": {sid: why for sid, (_l, why) in cand.trimmed.items() if sid not in ref.trimmed},
        },
        "duration_s": {"reference_estimated": round(ref.estimated_s, 2),
                       "candidate_estimated": round(cand.estimated_s, 2),
                       "reference_compiled": None if ref.compiled_s is None else round(ref.compiled_s, 3),
                       "candidate_compiled": None if cand.compiled_s is None else round(cand.compiled_s, 3)},
        "captions": {"reference_size_px": rc.get("size_px"), "candidate_size_px": cc.get("size_px"),
                     "reference_pages": rc.get("pages", 0), "candidate_pages": cc.get("pages", 0)},
        "emphasis_overlap": sorted(set(ref.emphasis) & set(cand.emphasis)),
        "radio_blockers": {"reference": len(ref.radio), "candidate": len(cand.radio)},
    }


# ============================================================================================ code plan
def run_code_plan(ix: TakeIndex, *, platform: str, decision: PlanDecision | None, brief: str | None = None,
                  style: str | None = None, job: Job | None = None) -> tuple[dict[str, Any], CutDocument | None]:
    """The code plan's op list applied to a fresh document: ``(info, doc)``; ``doc`` is None when the module is not
    installed (``status`` "unavailable") or refuses the take (``CodePlanError``, ``status`` "failed"). Any other
    import failure (a SyntaxError, a broken import inside the module) is a regression, not an outcome: it reaches the
    job's ``errors`` and the run exits non-zero."""
    try:
        from studio.plan.code_plan import CodePlanError, code_plan
    except ModuleNotFoundError as e:
        if e.name not in ("studio.plan.code_plan", "studio.plan"):
            raise  # the module exists but one of its own imports is missing: a regression
        why = f"{type(e).__name__}: {_excerpt(str(e), 120)}"
        return {"status": "unavailable", "note": f"code plan unavailable ({why})"}, None
    from studio.doc.model import new_document
    from studio.doc.ops import apply_ops

    try:
        ops = code_plan(ix, platform=platform, decision=decision, brief=brief, style=style, job=job)
    except CodePlanError as e:
        return {"status": "failed", "note": f"code plan refused the take: {e}"}, None
    base = new_document(job.id if job is not None else "", created_by="code_plan",
                        platforms=(platform,))  # type: ignore[arg-type]
    doc, results = apply_ops(base, ops, ix, by="plan_eval", persist=False)
    rejected = [f"{r.op}: {r.reason}" for r in results if not r.applied and r.reason != "no change"]
    summary = next((str(op.get("text", "")) for op in reversed(ops) if op.get("op") == "note"), "")
    return {"status": "ok", "ops": len(ops), "op_counts": dict(Counter(str(op.get("op")) for op in ops)),
            "rejected_ops": rejected, "summary": summary}, doc


def _plan_section(ix: TakeIndex, ref: DocView, *, platform: str, decision: PlanDecision | None, brief: str | None,
                  style: str | None, job: Job | None) -> tuple[dict[str, Any], DocView | None]:
    info, doc = run_code_plan(ix, platform=platform, decision=decision, brief=brief, style=style, job=job)
    if doc is None:
        return info, None
    view = view_doc(doc, ix)
    info["doc"] = view.to_json(ix)
    info["vs_reference"] = compare_views(ref, view, ix)
    return info, view


# ============================================================================================ planner decision
def _range_ids(ix: TakeIndex, a: str, b: str) -> list[str]:
    if ix.has_word(a) and ix.has_word(b) and ix.word_pos(a) <= ix.word_pos(b):
        return ix.word_ids(a, b)
    return []


def _kept_take(ix: TakeIndex, sentence_ids: Sequence[str], kept: set[str]) -> str | None:
    """The take of a cluster the reference keeps (most kept words); None when it keeps none."""
    smap = ix.sentence_map
    scored = [(sum(w in kept for w in smap[sid].word_ids), sid) for sid in sentence_ids if sid in smap]
    best = max(scored, default=(0, None))
    return best[1] if best[0] > 0 else None


def evaluate_decision(d: PlanDecision, ix: TakeIndex, ref: DocView) -> dict[str, Any]:
    """The decision's guards and its own agreement with the reference (before the code plan applies it): the story it
    keeps (its sentence drops, take choices and filler cuts), drops, payoff/CTA, emphasis, punch-in, labels."""
    from studio.plan.decision import kept_word_ids, validate_decision

    rejections = validate_decision(d, ix)
    unknown = [r for r in rejections if " is not a " in r and "of this take" in r]
    fillers = set(d.cut_filler_word_ids)
    story = {w for w in kept_word_ids(d, ix) if w not in fillers}
    wmap = ix.word_map
    story_spoken = {w for w in story if wmap[w].is_spoken}
    dropped = {s.id for s in ix.sentences if not any(w in story for w in s.word_ids)}
    reasons = {x.sentence_id: x.reason for x in d.drop_sentences}
    ref_kept = set(ref.kept)

    def ranged(r: Any, pins: list[str]) -> dict[str, Any]:
        ids = _range_ids(ix, r.from_word, r.to_word) if r is not None else []
        return {"decision": f"{r.from_word}-{r.to_word}" if r is not None else None,
                "reference_pins": len(pins), "overlap": len(set(ids) & set(pins)),
                "same_end": bool(ids and pins and ids[-1] == pins[-1]),
                "kept_by_reference": bool(ids) and all(w in ref_kept for w in ids)}

    punch = d.punch_in.anchor_word if d.punch_in is not None else None
    ref_anchors = [p["anchor_word"] or p["first_word"] for p in ref.punch_ins]
    punch_sentence = wmap[punch].sentence_id if punch and punch in wmap else None
    takes = {}
    for c in ix.clusters:
        chosen = next((tc.sentence_id for tc in d.choose_take if tc.cluster_id == c.id), None)
        takes[c.id] = {"decision": chosen, "reference": _kept_take(ix, c.sentence_ids, ref_kept)}
    return {
        "valid": not rejections, "rejections": rejections, "unknown_id_rejections": len(unknown),
        "style": d.style_primary, "goal": d.goal,
        "story_jaccard": round(jaccard(ref.spoken, story_spoken), 4),
        "drops": {"both": sorted(dropped & set(ref.dropped)),
                  "only_decision": {sid: reasons.get(sid, "retake not chosen")
                                    for sid in sorted(dropped - set(ref.dropped))},
                  "only_reference": {sid: ref.dropped[sid] for sid in ref.dropped if sid not in dropped}},
        "payoff": ranged(d.payoff, ref.pins["payoff"]),
        "cta": ranged(d.cta, ref.pins["cta"]),
        "emphasis": {"decision": list(d.caption_emphasis_word_ids), "reference": ref.emphasis,
                     "overlap": sorted(set(d.caption_emphasis_word_ids) & set(ref.emphasis))},
        "punch_in": {"decision": punch, "reference": ref_anchors,
                     "same_sentence": (any(wmap[a].sentence_id == punch_sentence for a in ref_anchors if a in wmap)
                                       if punch_sentence else None)},
        "structure_markers": {"decision": [m.label for m in d.structure_markers],
                              "reference_texts": [t for t in ref.texts]},
        "choose_take": takes,
    }


def _planner_section(root: Path, ix: TakeIndex, ref: DocView, *, platform: str, brief: str | None,
                     style: str | None, job: Job | None) -> dict[str, Any]:
    path = root / PLAN_LOG
    if not path.is_file():
        return {"status": "absent", "note": f"no {PLAN_LOG}"}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"status": "unreadable", "note": f"{PLAN_LOG} is not JSON: {e}"}
    if not isinstance(record, dict):
        return {"status": "unreadable", "note": f"{PLAN_LOG} is not a planner record (a JSON object)"}
    raw, note = record.get("decision"), str(record.get("note") or "")
    if raw is None:
        return {"status": "no decision", "note": note or "the planner left no decision (pure-code plan shipped)"}
    from pydantic import ValidationError

    from studio.plan.decision import PlanDecision

    try:
        d = PlanDecision.model_validate(raw)
    except ValidationError as e:
        return {"status": "unreadable", "note": f"the decision is not a PlanDecision: {_excerpt(str(e), 200)}"}
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    out: dict[str, Any] = {"status": "evaluated", "note": note, "attempts": record.get("attempts"),
                           "model": model.get("model"), "usage": {k: usage.get(k) for k in _USAGE_KEYS}}
    out.update(evaluate_decision(d, ix, ref))
    out["code_plan"], _ = _plan_section(ix, ref, platform=platform, decision=d, brief=brief, style=style, job=job)
    return out


# ============================================================================================ one job
def _meta(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    meta = data.get("meta") if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


def evaluate_job(root: Path, *, with_media: bool = False) -> dict[str, Any]:
    """The eval record of one job directory (see the module docstring). Unexpected exceptions are caught and
    recorded under ``errors`` with their traceback (the caller turns them into a non-zero exit)."""
    from studio.jobs import Job
    from studio.perception.index import TakeIndex
    from studio.plan.digest import build_digest

    t0 = time.monotonic()
    root = Path(root).resolve()
    rec: dict[str, Any] = {"job": root.name, "path": str(root), "errors": []}
    try:
        meta = _meta(root)
        ix = TakeIndex.model_validate(json.loads((root / "index" / "take_index.json").read_text(encoding="utf-8")))
        ref = find_reference(root)
        platform = str(meta.get("platform") or (ref.doc.deliverables[0].platform if ref and ref.doc.deliverables
                                                 else "tiktok"))
        brief, style = meta.get("brief") or None, meta.get("style") or None
        rec.update({"source": meta.get("source_name"), "platform": platform, "brief": brief, "style": style,
                    "reference": ref.to_json() if ref is not None else None,
                    "index": {"duration_s": round(ix.duration_us / 1e6, 2), "words": len(ix.words),
                              "spoken_words": sum(1 for w in ix.words if w.is_spoken),
                              "sentences": len(ix.sentences), "clusters": len(ix.clusters)}})
        dg = build_digest(ix, platform=platform, brief=brief, style_hint=style)
        rec["digest"] = {"chars": dg.stats["chars"], "tokens_est": dg.stats["tokens_est"],
                         "sentences_shown": len(dg.sentence_ids),
                         "omitted_sentence_ids": list(dg.stats.get("omitted_sentence_ids", []))}
        if ref is None:
            rec["code_plan"] = {"status": "skipped", "note": "no reference document (nothing delivered, no champion)"}
            return rec
        ref_view = view_doc(ref.doc, ix)
        rec["reference_doc"] = ref_view.to_json(ix)
        job = Job(root) if with_media else None  # Job(root) creates nothing; Job.open would add missing dirs
        rec["code_plan"], _ = _plan_section(ix, ref_view, platform=platform, decision=None, brief=brief, style=style,
                                            job=job)
        rec["planner"] = _planner_section(root, ix, ref_view, platform=platform, brief=brief, style=style, job=job)
    except Exception as e:
        rec["errors"].append({"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
    finally:
        rec["elapsed_s"] = round(time.monotonic() - t0, 2)
    return rec


# ============================================================================================ report
def _fmt_s(x: float | None) -> str:
    return "?" if x is None else f"{x:.1f}"


def _row(rec: dict[str, Any]) -> list[str]:
    ref = rec.get("reference") or {}
    dg = rec.get("digest") or {}
    cp = rec.get("code_plan") or {}
    pl = rec.get("planner") or {}
    refkind = f"v{ref['version']} {'dlv' if ref['kind'] == 'delivered' else 'chmp'}" if ref else "-"
    src = _fmt_s((rec.get("index") or {}).get("duration_s"))
    digest = f"{dg['tokens_est']}t" if dg else "-"
    if rec.get("errors") and not cp:
        return [rec["job"], refkind, src, digest, "CRASH", "", "", "", "", "", "", ""]
    if cp.get("status") != "ok":
        return [rec["job"], refkind, src, digest, cp.get("status", "-"), "", "", "", "", "", "", _planner_cell(pl)]
    vs = cp["vs_reference"]
    pins = vs["pins_kept"]
    pinned = pins["payoff"]["pinned"] + pins["cta"]["pinned"]
    kept = pins["payoff"]["kept"] + pins["cta"]["kept"]
    out = "same" if vs["same_out_point"] else "DIFF"
    rd = rec["reference_doc"]
    n_both = len(vs["dropped_sentences"]["both"])
    drops = f"{len(rd['dropped_sentences'])}|{len(cp['doc']['dropped_sentences'])}|{n_both}"
    d = vs["duration_s"]
    dur = f"{_fmt_s(d['reference_compiled'] or d['reference_estimated'])}/" \
          f"{_fmt_s(d['candidate_compiled'] or d['candidate_estimated'])}"
    c = vs["captions"]
    caps = (f"{c['reference_size_px'] or '-'}x{c['reference_pages']}/"
            f"{c['candidate_size_px'] or '-'}x{c['candidate_pages']}")
    radio = f"{vs['radio_blockers']['reference']}/{vs['radio_blockers']['candidate']}"
    return [rec["job"], refkind, src, digest, f"{vs['jaccard']:.3f}", f"{kept}/{pinned}", out, drops, dur, caps,
            radio, _planner_cell(pl)]


def _planner_cell(pl: dict[str, Any]) -> str:
    st = pl.get("status", "-")
    if st != "evaluated":
        return st
    cp = pl.get("code_plan") or {}
    j = cp.get("vs_reference", {}).get("jaccard")
    return f"J {j:.3f} rej {len(pl.get('rejections', []))}" if j is not None else f"rej {len(pl.get('rejections', []))}"


_HEADER = ["job", "reference", "src s", "digest", "jaccard", "pins", "out", "drops r|c|both", "dur r/c s",
           "caps px x pages r/c", "radio r/c", "planner"]


def format_table(records: Sequence[dict[str, Any]]) -> str:
    """The summary table plus a few detail lines per job (what each side dropped and why, out-points)."""
    rows = [_HEADER, *(_row(r) for r in records)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(_HEADER))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    lines.append("")
    lines.append("jaccard = kept spoken-word Jaccard, code plan (decision=None) vs reference; pins = reference "
                 "payoff+CTA pins the code plan keeps; out = same last spoken word; drops = whole sentences dropped "
                 "by the reference | code plan | both; dur = compiled output seconds; radio = radio blockers.")
    for rec in records:
        lines.append("")
        lines.extend(_details(rec))
    return "\n".join(lines)


def _details(rec: dict[str, Any]) -> list[str]:
    head = f"{rec['job']}  ({rec.get('source') or '?'}; {rec.get('platform') or '?'})"
    out = [head]
    for err in rec.get("errors", []):
        out.append(f"  CRASH: {err['error']}")
    ref = rec.get("reference")
    if ref is None:
        if not rec.get("errors"):
            out.append("  no reference document")
        return out
    dg = rec.get("digest") or {}
    if dg:
        omitted = f", omitted {', '.join(dg['omitted_sentence_ids'])}" if dg.get("omitted_sentence_ids") else ""
        out.append(f"  digest: {dg['chars']} chars ~{dg['tokens_est']} tokens, {dg['sentences_shown']} "
                   f"sentences{omitted}")
    rd = rec.get("reference_doc")
    if rd:
        line = (f"  reference {ref['kind']} v{ref['version']}: out {rd['out_point']}; captions "
                f"{_caps_text(rd['captions'])}")
        if rd["punch_ins"]:
            line += "; punch-ins " + ", ".join(f"x{p['scale']:.2f}@{p['anchor_word'] or p['first_word']}"
                                               for p in rd["punch_ins"])
        if rd["texts"]:
            line += f"; texts {', '.join(rd['texts'])}"
        out.append(line)
    cp = rec.get("code_plan") or {}
    if cp.get("status") != "ok":
        out.append(f"  code plan: {cp.get('note', cp.get('status'))}")
    else:
        out.extend(_comparison_lines("code plan", cp))
    pl = rec.get("planner") or {}
    if pl.get("status") == "evaluated":
        out.append(f"  planner ({pl.get('model') or '?'}, {pl.get('attempts')} attempt(s)): valid {pl['valid']}, "
                   f"{len(pl['rejections'])} rejection(s) ({pl['unknown_id_rejections']} unknown IDs); story Jaccard "
                   f"{pl['story_jaccard']:.3f}; payoff same end {pl['payoff']['same_end']}; drops only-decision "
                   f"{list(pl['drops']['only_decision'])} only-reference {list(pl['drops']['only_reference'])}")
        for r in pl["rejections"][:4]:
            out.append(f"    rejected: {r}")
        pcp = pl.get("code_plan") or {}
        if pcp.get("status") == "ok":
            out.extend(_comparison_lines("code plan + decision", pcp))
        else:
            out.append(f"  code plan + decision: {pcp.get('note', pcp.get('status'))}")
    elif pl:
        out.append(f"  planner: {pl.get('status')} ({pl.get('note', '')})")
    return out


def _caps_text(c: dict[str, Any]) -> str:
    if not c.get("enabled"):
        return "none"
    placed = ", ".join(f"{k} {v}" for k, v in (c.get("placements") or {}).items())
    return f"{c['size_px']}px x{c['pages']} pages" + (f" ({placed})" if placed else "")


def _comparison_lines(label: str, cp: dict[str, Any]) -> list[str]:
    vs, doc = cp["vs_reference"], cp["doc"]
    out_ref = "same" if vs["same_out_point"] else f"reference {vs['out_point']['reference']}"
    ip = vs["in_point"]
    in_pt = "same" if vs["same_in_point"] else f"{ip['candidate']} (reference {ip['reference']})"
    out = [f"  {label}: jaccard {vs['jaccard']:.3f} (lexical {vs['jaccard_lexical']:.3f}); out "
           f"{vs['out_point']['candidate']} ({out_ref}); in {in_pt}; captions {_caps_text(doc['captions'])}"]
    pins = vs["pins_kept"]
    miss = pins["payoff"]["missing"] + pins["cta"]["missing"]
    if miss:
        out.append(f"    reference pins not kept: {', '.join(miss)}")
    for sid, why in vs["dropped_sentences"]["only_reference"].items():
        out.append(f"    dropped only by the reference: {sid} ({_excerpt(why, 110)})")
    for sid, why in vs["dropped_sentences"]["only_candidate"].items():
        out.append(f"    dropped only by the {label}: {sid} ({_excerpt(why, 110)})")
    tr = vs["trimmed_sentences"]
    if tr["both_same_words"] or tr["both_other_words"]:
        out.append("    trimmed by both: " + ", ".join([*(f"{sid} (same words)" for sid in tr["both_same_words"]),
                                                        *(f"{sid} (other words)" for sid in tr["both_other_words"])]))
    for sid, why in tr["only_reference"].items():
        out.append(f"    trimmed only by the reference: {sid} ({_excerpt(why, 110)})")
    for sid, why in tr["only_candidate"].items():
        out.append(f"    trimmed only by the {label}: {sid} ({_excerpt(why, 110)})")
    for span in vs["only_in_reference"][:6]:
        out.append(f"    kept only by the reference: {span['from']}-{span['to']} \"{span['text']}\"")
    for span in vs["only_in_candidate"][:6]:
        out.append(f"    kept only by the {label}: {span['from']}-{span['to']} \"{span['text']}\"")
    for b in doc["radio_blockers"][:3]:
        out.append(f"    radio blocker: {_excerpt(b, 140)}")
    for r in cp.get("rejected_ops", [])[:3]:
        out.append(f"    rejected op: {_excerpt(r, 140)}")
    return out


# ============================================================================================ CLI
def _default_work_dir() -> Path:
    try:
        from studio.config import get_settings

        wd = Path(get_settings().work_dir)
    except Exception:  # settings that do not load must not stop an offline eval
        wd = FALLBACK_WORK_DIR
    return wd if wd.is_dir() else FALLBACK_WORK_DIR


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plan_eval", description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("jobs", nargs="*", type=Path, help="job directories (default: every job under --work-dir with a "
                                                         "Take Index and a delivered or champion document)")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help=f"where to look for jobs when none is named (default: the configured work dir, else "
                         f"{FALLBACK_WORK_DIR})")
    ap.add_argument("--json", action="store_true", help="print the records as JSON instead of the table")
    ap.add_argument("--no-write", action="store_true", help=f"do not write <job>/{EVAL_LOG}")
    ap.add_argument("--with-media", action="store_true",
                    help="let the code plan read the job's media (audio.wav voice chain, mezzanine colour, dense "
                         "visual track); default: the index alone")
    args = ap.parse_args(argv)

    roots = list(args.jobs) or discover_jobs(args.work_dir or _default_work_dir())
    if not roots:
        print("plan_eval: no jobs to evaluate (none named, none found with a Take Index and a reference document)",
              file=sys.stderr)
        return 0
    records = []
    crashed = False
    for root in roots:
        rec = evaluate_job(root, with_media=args.with_media)
        crashed |= bool(rec["errors"])
        if not args.no_write and (Path(root) / "job.json").is_file():
            try:
                from studio.jobs import write_json_atomic

                write_json_atomic(Path(root) / EVAL_LOG, rec)
            except OSError as e:
                rec["errors"].append({"error": f"could not write {EVAL_LOG}: {e}", "traceback": ""})
                crashed = True
        records.append(rec)
        for err in rec["errors"]:
            print(f"plan_eval: {rec['job']}: {err['error']}", file=sys.stderr)
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_table(records))
    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
