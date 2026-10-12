"""Tests for :mod:`studio.plan.repair` (the standard tier's code-only repair).

Keyless tests drive the fix map with the real document ops, compiler and caption geometry, and fake the heavy
passes (``_full_render`` / ``_overlay_render`` / ``_audio_render`` / ``_evaluate``) so they can count renders: every
call renders at most once, picks the cheapest pass that carries every fix, and renders nothing when a failure has
no code fix. Two slow tests run the real passes on synthetic media: a caption page forced into the TikTok UI strip
(y 0.95, as ``test_qa`` does) that the overlay + master pass moves so invariant 7 passes, and a quiet mix that the
audio re-master + re-mux brings back to the loudness target without re-encoding the picture.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from conftest import _chunk_pages, build_cut_doc, build_take_index
from qa_fixtures import SR, make_render, synth_source_audio

from studio.compile import captions as cap
from studio.compile import timeline as timeline_mod
from studio.compile.models import Timeline
from studio.compile.timeline import compile as compile_timeline
from studio.compile.video import RenderError
from studio.config import get_settings
from studio.doc.model import CaptionPage, CaptionPlan, CutDocument, Deliverable, Pins, RemovedRange, Segment
from studio.doc.validate import cutoffs_at_joins
from studio.jobs import Job
from studio.perception.index import TakeIndex
from studio.plan import repair as rp
from studio.qa import invariants as inv
from studio.qa import metrics as qm

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
_REAL_OVERLAY_RENDER: Callable[..., Path] = rp._overlay_render
HAVE_OVERLAYS = shutil.which("node") is not None and (
    get_settings().overlay_dir / "node_modules" / ".bin" / "remotion").exists()


# ============================================================================================ helpers
def _doc(ix: TakeIndex, segs: list[tuple[str, str]], *, pins: Pins | None = None, pages: bool = True,
         version: int = 1) -> CutDocument:
    segments = [Segment(id=f"seg{k:03d}", from_word=a, to_word=b) for k, (a, b) in enumerate(segs, start=1)]
    kept = [w for s in segments for w in ix.word_ids(s.from_word, s.to_word)]
    plan = None
    if pages:
        plan = CaptionPlan(pages=[CaptionPage(id=f"p{k:03d}", word_ids=ws)
                                  for k, ws in enumerate(_chunk_pages(kept, ix), start=1)])
    return CutDocument(version=version, parent_version=version - 1, job_id="fixture-job", created_by="fixture",
                       pins=pins or Pins(), segments=segments, captions=plan,
                       deliverables=[Deliverable(platform="tiktok")],
                       counters={"seg": len(segments), "p": len(plan.pages) if plan else 0})


def _render_dir(job: Job, doc: CutDocument, ix: TakeIndex, *, move: dict[str, float] | None = None) -> tuple[
        Path, Timeline]:
    """A render dir holding the document's compiled timeline (page ``y_norm`` overridden by ``move``) and a manifest:
    all the planning half of repair reads from a render."""
    tl = compile_timeline(doc, ix, job=job)
    if move:
        tl = tl.model_copy(update={"captions": [p.model_copy(update={"y_norm": move[p.page_id]})
                                                if p.page_id in move else p for p in tl.captions]})
    rd = job.new_render_dir()
    tl.save(rd / "timeline.json")
    (rd / "render.json").write_text(json.dumps({"doc_version": doc.version, "profile": "standard"}))
    return rd, tl


def _fail7(tl: Timeline, ix: TakeIndex, issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Invariant 7 as QA reports it: per-platform text issues (planned boxes unless ``issues`` are given)."""
    if issues is None:
        issues = [b.model_dump() for b in qm.planned_text_boxes(tl, ix, "tiktok") if b.issues]
    refs = [r for it in issues for r in (it["id"], *it.get("refs", [])[:2])]
    return {"id": "text_safe", "number": 7, "name": inv.INVARIANTS[7][1], "passed": False,
            "detail": "; ".join(f"tiktok: caption {it['id']} {'/'.join(it['issues'])}" for it in issues),
            "refs": refs, "data": {"platforms": {"tiktok": {"source": issues[0].get("source", "planned"),
                                                            "boxes": len(tl.captions), "issues": issues}}}}


def _fail(n: int, refs: list[str] | None = None, **data: Any) -> dict[str, Any]:
    return {"id": inv.INVARIANTS[n][0], "number": n, "name": inv.INVARIANTS[n][1], "passed": False,
            "detail": "fixture", "refs": refs or [], "data": data}


class Passes:
    """Fakes for the heavy passes: each records its calls and makes a new render dir; ``qa`` scripts the verdict."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, qa: list[tuple[bool, list[dict[str, Any]]]] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.qa = list(qa or [])
        monkeypatch.setattr(rp, "_full_render", self.full)
        monkeypatch.setattr(rp, "_overlay_render", self.overlay)
        monkeypatch.setattr(rp, "_audio_render", self.audio)
        monkeypatch.setattr(rp, "_evaluate", self.evaluate)

    def _new(self, kind: str, job: Job, **kw: Any) -> Path:
        self.calls.append((kind, kw))
        return job.new_render_dir()

    def full(self, job: Job, doc: CutDocument, index: TakeIndex, *, profile: str) -> Path:
        return self._new("full", job, doc=doc, profile=profile)

    def overlay(self, job: Job, doc: CutDocument, index: TakeIndex, old: Path, *, profile: str,
                corrections: dict[str, Any], notes: list[str]) -> Path:
        return self._new("overlay", job, doc=doc, old=old, profile=profile, corrections=corrections)

    def audio(self, job: Job, doc: CutDocument, old: Path, *, corrections: dict[str, Any], notes: list[str]) -> Path:
        return self._new("audio", job, doc=doc, old=old, corrections=corrections)

    def evaluate(self, job: Job, rd: Path, s: Any) -> tuple[bool, list[dict[str, Any]]]:
        self.calls.append(("qa", {"rd": rd}))
        return self.qa.pop(0) if self.qa else (True, [])

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


def _oplog(job: Job) -> list[dict[str, Any]]:
    p = job.root / "doc" / "oplog.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


# ============================================================================================ entry rules
def test_invariant_label() -> None:
    assert rp.invariant_label(7) == "7 text_safe" and rp.invariant_label(1) == "1 no_seam_click"
    assert set(rp.REPAIRABLE) == {1, 4, 7, 8}


def test_nothing_failed_is_a_pass(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008")])
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [{**_fail(8), "passed": True}])
    assert res.passed and not res.rerendered and res.render_dir == rd and res.doc is doc
    assert passes.calls == []


def test_unrepairable_failure_renders_nothing(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch
                                              ) -> None:
    """A failure without a code fix (here A/V alignment) means the repaired render could not pass: nothing is
    rendered, not even the fixable caption page, and every failure comes back in ``remaining``."""
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    res = rp.repair(job, doc, take_index, rd, [_fail(2), _fail7(tl, take_index)])
    assert passes.calls == []
    assert not res.passed and not res.rerendered and res.mode == "none" and res.render_dir == rd
    assert res.remaining == ["2 av_alignment", "7 text_safe"] and res.fixed == []
    assert any("2 av_alignment" in n and "no re-render" in n for n in res.notes)
    assert job.latest_doc_version() == doc.version
    ev = [e for e in job.read_trace() if e["event"] == "repair"][-1]
    assert ev["rerendered"] is False and ev["remaining"] == res.remaining


# ============================================================================================ 7 captions
def test_caption_in_the_ui_strip_is_an_overlay_pass(job: Job, take_index: TakeIndex,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """A page drawn in the UI strip where the placer would not put it: the placer's own spot is clean and new, so
    the document keeps its automatic placement and only the overlay + master are re-rendered."""
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index)])
    assert passes.kinds() == ["overlay", "qa"]
    assert passes.calls[0][1]["old"] == rd and passes.calls[0][1]["profile"] == "standard"
    assert res.passed and res.rerendered and res.mode == "overlay" and res.fixed == ["7 text_safe"]
    assert res.remaining == [] and res.render_dir == passes.calls[1][1]["rd"] != rd
    assert any("p002 re-placed by the placer" in n for n in res.notes)
    assert res.doc.version == doc.version  # the recompiled placement is the fix; nothing to write


def test_explicit_height_too_close_to_the_face_goes_back_to_the_placer(job: Job, take_index: TakeIndex,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A page pinned just under the eyes-to-mouth box (valid for the placer, 14 px of air) rendered 34 px taller
    than its estimate and touched the face: the placer's own spot clears the measured reach, so the pin is cleared
    (``set_captions``: ``edit_caption_page`` cannot clear a field) and the page goes back to the placer."""
    passes = Passes(monkeypatch)
    base = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    tl0 = compile_timeline(base, take_index, job=job)
    pg0 = next(p for p in tl0.captions if p.page_id == "p002")
    zone, params = cap.safe_zone_for("tiktok"), cap.load_caption_params()
    bh = rp._planned_box(pg0, tl0.width, tl0.height, zone, params)
    bh = bh[3] - bh[1]
    doc = rp._with_page(base, "p002", y_norm=round((790.0 + bh / 2) / 1920, 5))
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index)
    pg = next(p for p in tl.captions if p.page_id == "p002")
    assert pg.placement == "fixed" and pg.y_norm < pg0.y_norm
    P = rp._planned_box(pg, tl.width, tl.height, zone, params)
    face = qm.face_protected_rect(tl, take_index, (Fraction(pg.out_start) + Fraction(pg.out_end)) / 2)
    assert face is not None and 0 < P[1] - face[3] < 20
    issue = {"kind": "caption", "id": "p002", "source": "rendered", "box": [P[0], face[3] - 20.0, P[2], P[1] + 40],
             "issues": ["covers_face"], "platform": "tiktok", "refs": [w.word_id for w in pg.words]}
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index, [issue])])
    assert passes.kinds() == ["overlay", "qa"] and res.passed
    page = next(p for p in res.doc.captions.pages if p.id == "p002")
    assert page.y_norm is None and page.position is None
    assert [e["op"]["op"] for e in _oplog(job) if e.get("by") == "repair"] == ["set_captions"]
    new_pg = next(p for p in compile_timeline(res.doc, take_index, job=job).captions if p.page_id == "p002")
    assert new_pg.y_norm == pytest.approx(pg0.y_norm) and any("re-placed by the placer" in n for n in res.notes)


def test_page_named_only_in_refs_is_never_put_back(job: Job, take_index: TakeIndex,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Without per-box evidence (an older QA record names the page in its refs only), the placer's spot is where
    the page failed, so repair pins the nearest clean height at least a few px away (another page's height first)."""
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index)
    pg = next(p for p in tl.captions if p.page_id == "p002")
    f7 = {**_fail(7, ["p002", "w0004"]), "data": {}}
    res = rp.repair(job, doc, take_index, rd, [f7])
    assert passes.kinds() == ["overlay", "qa"] and res.passed
    page = next(p for p in res.doc.captions.pages if p.id == "p002")
    assert page.y_norm is not None and abs(page.y_norm - pg.y_norm) * 1920 >= 4.0
    assert [e["op"]["op"] for e in _oplog(job) if e.get("by") == "repair"] == ["edit_caption_page"]


def test_caption_where_the_placer_failed_is_pinned_lower(job: Job, take_index: TakeIndex,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The rendered glyphs reached up into the face from the placer's own spot (QA's box runs past the plan; the
    reach counts up to one block height): putting the page back there would fail again, so repair pins the nearest
    height whose box, widened by that reach, clears the face, writes it with ``edit_caption_page`` and re-renders
    the overlay."""
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index)
    pg = next(p for p in tl.captions if p.page_id == "p002")
    zone = cap.safe_zone_for("tiktok")
    P = rp._planned_box(pg, tl.width, tl.height, zone, cap.load_caption_params())
    face = qm.face_protected_rect(tl, take_index, (Fraction(pg.out_start) + Fraction(pg.out_end)) / 2)
    assert face is not None and face[3] < P[1]  # the plan was clear of the face
    drawn = [P[0], face[3] - 20.0, P[2], P[3]]  # what QA saw: glyph pixels 20 px into the eyes-to-mouth box
    issue = {"kind": "caption", "id": "p002", "source": "rendered", "out_start": float(pg.out_start),
             "out_end": float(pg.out_end), "box": drawn, "issues": ["covers_face"], "face_overlap_px": 4000.0,
             "platform": "tiktok", "refs": [w.word_id for w in pg.words]}
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index, [issue])])
    assert passes.kinds() == ["overlay", "qa"] and res.passed and res.mode == "overlay"
    assert res.doc.version == doc.version + 1 == job.latest_doc_version()
    page = next(p for p in res.doc.captions.pages if p.id == "p002")
    assert page.y_norm is not None and page.y_norm > pg.y_norm  # moved down, away from the face
    entries = [e for e in _oplog(job) if e.get("by") == "repair"]
    assert [e["op"]["op"] for e in entries] == ["edit_caption_page"] and entries[0]["applied"]
    new_tl = compile_timeline(res.doc, take_index, job=job)
    new_pg = next(p for p in new_tl.captions if p.page_id == "p002")
    assert new_pg.y_norm == pytest.approx(page.y_norm, abs=2e-3)
    newP = rp._planned_box(new_pg, tl.width, tl.height, zone, cap.load_caption_params())
    reach = P[1] - drawn[1]  # the glyphs' measured reach above the planned box, now clear of the face
    for t in (Fraction(new_pg.out_start), Fraction(new_pg.out_end) - Fraction(1, 30)):
        f = qm.face_protected_rect(new_tl, take_index, t)
        assert f is None or newP[1] - min(reach, newP[3] - newP[1]) >= f[3]
    assert not [b for b in qm.planned_text_boxes(new_tl, take_index, "tiktok") if b.issues]


def test_caption_too_wide_steps_the_size_down(job: Job, take_index: TakeIndex,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index)
    pg = next(p for p in tl.captions if p.page_id == "p002")
    issue = {"kind": "caption", "id": "p002", "source": "planned", "box": [0, 0, 1, 1], "issues": ["too_wide"],
             "platform": "tiktok", "refs": []}
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index, [issue])])
    assert passes.kinds() == ["overlay", "qa"] and res.passed
    assert res.doc.captions.style.size_px == round(88 * 0.9) == 79
    assert res.doc.captions.style.stroke_px == pytest.approx(9.0 * 79 / 88, abs=0.05)
    assert [e["op"]["op"] for e in _oplog(job) if e.get("by") == "repair"] == ["set_caption_style"]
    assert pg.style.size_px == 88 and any("88 -> 79 px" in n for n in res.notes)


def test_text_overlay_failure_is_not_a_caption_fix(job: Job, take_index: TakeIndex,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index)
    issue = {"kind": "text", "id": "t001", "source": "rendered", "box": [0, 0, 10, 10],
             "issues": ["outside_safe_zone"], "platform": "tiktok"}
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index, [issue])])
    assert passes.calls == [] and not res.rerendered and res.remaining == ["7 text_safe"]
    assert any("not a caption page" in n for n in res.notes)


# ============================================================================================ 1 / 4 story
def test_cut_off_fragment_at_a_join_is_cut_then_fully_rendered(job: Job, take_index: TakeIndex,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0009", "w0013"), ("w0020", "w0028")], pages=False)
    job.save_doc(doc)
    rd, _ = _render_dir(job, doc, take_index)
    assert [c["word_id"] for c in cutoffs_at_joins(doc, take_index)] == ["w0013"]
    f1 = _fail(1, ["w0013", "w0020"], cutoff_at_join=[{"word_id": "w0013", "text": "restr-", "seg": "seg002",
                                                      "side": "end"}], truncated=[])
    res = rp.repair(job, doc, take_index, rd, [f1], profile="standard")
    assert passes.kinds() == ["full", "qa"] and passes.calls[0][1]["profile"] == "standard"
    assert res.passed and res.mode == "full" and res.fixed == ["1 no_seam_click"]
    assert passes.calls[0][1]["doc"] is res.doc and res.doc.version == doc.version + 1
    assert "w0013" not in res.doc.kept_word_ids(take_index) and cutoffs_at_joins(res.doc, take_index) == []
    ops = [e["op"] for e in _oplog(job) if e.get("by") == "repair"]
    assert ops == [{"op": "cut_words", "from_word": "w0013", "to_word": "w0013",
                    "reason": ops[0]["reason"]}] and "invariant 1" in ops[0]["reason"]


def test_pinned_fragment_is_not_cut(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0009", "w0013"), ("w0020", "w0028")], pages=False,
               pins=Pins(must_keep_word_ids=["w0013"]))
    job.save_doc(doc)
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [_fail(1, ["w0013"])])
    assert passes.calls == [] and res.remaining == ["1 no_seam_click"]
    assert any("w0013" in n for n in res.notes)


def test_click_without_a_fragment_is_unrepairable(job: Job, take_index: TakeIndex,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")], pages=False)
    job.save_doc(doc)
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [_fail(1, ["w0008", "w0014"], clicks=[{"seam": 0}])])
    assert passes.calls == [] and res.remaining == ["1 no_seam_click"]


def test_missing_pins_are_restored_then_fully_rendered(job: Job, take_index: TakeIndex,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0017"), ("w0020", "w0028")], pages=False,
               pins=Pins(payoff_word_ids=["w0018"]))
    doc = doc.model_copy(update={"removed": [RemovedRange(from_word="w0018", to_word="w0019", reason="trim")]})
    job.save_doc(doc)
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [_fail(4, ["w0018"])])
    assert passes.kinds() == ["full", "qa"] and res.passed and res.fixed == ["4 pins_present"]
    assert "w0018" in res.doc.kept_word_ids(take_index)
    assert [e["op"]["op"] for e in _oplog(job) if e.get("by") == "repair"] == ["restore_words"]


# ============================================================================================ 8 loudness
def test_loudness_failure_is_an_audio_pass(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    corr = {"mix.wav": {"final": "final_tiktok.mp4", "target": -11.2, "ceiling": -1.8, "note": "quiet"}}
    monkeypatch.setattr(rp, "_loudness_corrections", lambda doc, rd, s: corr)
    doc = _doc(take_index, [("w0001", "w0008")])
    job.save_doc(doc)
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [_fail(8)])
    assert passes.kinds() == ["audio", "qa"] and passes.calls[0][1]["corrections"] == corr
    assert res.passed and res.mode == "audio" and res.fixed == ["8 loudness"] and res.doc is doc


def test_loudness_already_within_target_renders_nothing(job: Job, take_index: TakeIndex,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    monkeypatch.setattr(rp, "_loudness_corrections", lambda doc, rd, s: {})
    doc = _doc(take_index, [("w0001", "w0008")])
    rd, _ = _render_dir(job, doc, take_index)
    res = rp.repair(job, doc, take_index, rd, [_fail(8)])
    assert passes.calls == [] and res.remaining == ["8 loudness"]


# ============================================================================================ one re-render
def test_combined_failures_take_one_full_render(job: Job, take_index: TakeIndex,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """1 + 7 + 8: the cut changes, so one full render carries the caption re-placement too, and the loudness is
    checked on that render's own encoded file (corrected in place only if it still misses)."""
    passes = Passes(monkeypatch)
    seen: list[Path] = []

    def corrections(doc: CutDocument, rd: Path, s: Any) -> dict[str, Any]:
        seen.append(rd)
        return {}

    monkeypatch.setattr(rp, "_loudness_corrections", corrections)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0009", "w0013"), ("w0020", "w0028")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    f1 = _fail(1, ["w0013"], cutoff_at_join=[{"word_id": "w0013", "text": "restr-", "seg": "seg002", "side": "end"}])
    res = rp.repair(job, doc, take_index, rd, [f1, _fail7(tl, take_index), _fail(8)])
    assert passes.kinds() == ["full", "qa"]
    assert res.mode == "full" and res.passed and res.fixed == ["1 no_seam_click", "7 text_safe", "8 loudness"]
    assert seen == [res.render_dir]  # measured on the new render, not the failed one
    assert any("within the loudness target" in n for n in res.notes)


def test_caption_and_loudness_share_the_overlay_pass(job: Job, take_index: TakeIndex,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)
    corr = {"mix.wav": {"final": "final_tiktok.mp4", "target": -13.0, "ceiling": -2.0, "note": "hot"}}
    monkeypatch.setattr(rp, "_loudness_corrections", lambda doc, rd, s: corr)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index), _fail(8)])
    assert passes.kinds() == ["overlay", "qa"] and passes.calls[0][1]["corrections"] == corr
    assert res.fixed == ["7 text_safe", "8 loudness"]


def test_a_second_failure_is_returned_not_retried(job: Job, take_index: TakeIndex,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    still = [_fail7(tl, take_index), _fail(9, ["w0004"])]
    passes = Passes(monkeypatch, qa=[(False, still)])
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index)])
    assert passes.kinds() == ["overlay", "qa"]  # one render, one QA: no second attempt
    assert not res.passed and res.rerendered and res.fixed == []
    assert res.remaining == ["7 text_safe", "9 no_digital_silence"] and res.failures == still
    assert res.render_dir != rd  # the repaired render is returned even when it fails; the caller decides


def test_overlay_pass_without_intermediates_falls_back_to_one_full_render(job: Job, take_index: TakeIndex,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The real overlay pass needs the failed render's aroll.mov + mix.wav; when they were pruned it falls back to
    a full render (still the only re-render)."""
    passes = Passes(monkeypatch)
    monkeypatch.setattr(rp, "_overlay_render", _REAL_OVERLAY_RENDER)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index)])
    assert passes.kinds() == ["full", "qa"] and res.mode == "full" and res.passed
    assert any("aroll.mov, mix.wav no longer in" in n and "full render instead" in n for n in res.notes)



def test_a_failing_render_is_a_failed_repair(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch) -> None:
    passes = Passes(monkeypatch)

    def boom(*a: Any, **k: Any) -> Path:
        raise RenderError("overlay render crashed")

    monkeypatch.setattr(rp, "_overlay_render", boom)
    doc = _doc(take_index, [("w0001", "w0008"), ("w0014", "w0018")])
    job.save_doc(doc)
    rd, tl = _render_dir(job, doc, take_index, move={"p002": 0.95})
    res = rp.repair(job, doc, take_index, rd, [_fail7(tl, take_index)])
    assert not res.passed and not res.rerendered and res.render_dir == rd and res.remaining == ["7 text_safe"]
    assert any("overlay re-render failed: RenderError" in n for n in res.notes) and passes.calls == []


# ============================================================================================ helpers
def test_size_ladder_and_caption_ops(take_index: TakeIndex) -> None:
    assert rp._size_ladder(88, 64.0) == [88, 79, 70, 64]
    assert rp._size_ladder(70, 64.0) == [70, 64] and rp._size_ladder(64, 64.0) == [64]
    doc = _doc(take_index, [("w0001", "w0008")])
    assert rp._caption_ops(doc, doc.captions) == []
    pinned = rp._with_page(doc, "p001", y_norm=0.4)
    assert rp._caption_ops(doc, pinned.captions) == [{"op": "edit_caption_page", "page_id": "p001", "y_norm": 0.4}]
    back = rp._caption_ops(pinned, doc.captions)  # clearing a pin needs the whole plan
    assert [o["op"] for o in back] == ["set_captions"]
    auto = _doc(take_index, [("w0001", "w0008")], pages=False)
    tl = compile_timeline(auto, take_index)
    written = rp._explicit_plan(auto, tl)
    assert [p.id for p in written.pages] == [p.page_id for p in tl.captions]
    assert rp._caption_ops(auto, written) == []  # the automatic pages written out are no change


# ============================================================================================ slow: real passes
@pytest.fixture(scope="module")
def media_job(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A job with the fixture index, a short captioned document (no inserts/SFX), the synthetic dialogue as
    ``media/audio.wav`` and a small SDR testsrc mezzanine: enough for the real render chain."""
    root = tmp_path_factory.mktemp("repair-media")
    ix = build_take_index()
    doc = _doc(ix, [("w0001", "w0008"), ("w0014", "w0018")], pins=Pins(payoff_word_ids=["w0018"]))
    doc = doc.model_copy(update={"job_id": "repair-job",
                                 "removed": [RemovedRange(from_word="w0009", to_word="w0013", reason="false start")]})
    job = Job.create("repair-job", work_dir=root)
    job.save_media_info(ix.media)
    job.save_index(ix)
    job.save_doc(doc)
    sf.write(str(job.audio_path), synth_source_audio(ix).astype(np.float32), SR, subtype="FLOAT")
    dur = ix.media.duration_us / 1e6
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=432x768:r=30:d={dur:.3f}",
                    "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-colorspace", "bt709", str(job.mezz_path)], check=True)
    yield SimpleNamespace(job=job, index=ix, doc=doc)
    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_OVERLAYS), reason="needs ffmpeg and the overlay node project")
def test_caption_in_the_ui_strip_is_moved_by_a_real_overlay_pass(media_job: Any, monkeypatch: pytest.MonkeyPatch
                                                                 ) -> None:
    """End to end on synthetic media: the standard render draws p002 in the TikTok UI strip (its timeline forced to
    y 0.95), QA fails invariant 7 on the rendered overlay layer, and repair re-renders only the overlay + master on
    that render's own A-roll and mix (hard links, no new A-roll or mix) so invariant 7 passes."""
    from studio.compile.master import render_document

    job, ix, doc = media_job.job, media_job.index, media_job.doc
    real = timeline_mod.compile

    def forced(d: CutDocument, i: TakeIndex, **kw: Any) -> Timeline:
        tl = real(d, i, **kw)
        return tl.model_copy(update={"captions": [p.model_copy(update={"y_norm": 0.95}) if p.page_id == "p002" else p
                                                  for p in tl.captions]})

    with monkeypatch.context() as m:
        m.setattr(timeline_mod, "compile", forced)
        first = render_document(job, doc, ix, profile="standard", aroll_codec="lean").render_dir
    run = inv.evaluate_render(job, first)
    failing = {r.number: r for r in run.results if not r.passed}
    assert 7 in failing and failing[7].data["platforms"]["tiktok"]["source"] == "rendered"
    assert "p002" in failing[7].refs

    res = rp.repair(job, doc, ix, first, [failing[7].to_dict()], profile="standard")
    assert res.rerendered and res.mode == "overlay" and "7 text_safe" in res.fixed
    assert "7 text_safe" not in res.remaining
    new = res.render_dir
    assert new != first and (new / "render.json").exists()
    man = json.loads((new / "render.json").read_text())
    assert man["repair"] == {"from": first.name, "mode": "overlay"} and man["profile"] == "standard"
    for name in ("aroll.mov", "mix.wav"):  # reused, not re-rendered
        assert (new / name).stat().st_ino == (first / name).stat().st_ino
    assert (new / "overlays.mov").stat().st_ino != (first / "overlays.mov").stat().st_ino
    tl = Timeline.load(new / "timeline.json")
    assert next(p for p in tl.captions if p.page_id == "p002").y_norm < 0.8
    again = {r.number: r for r in inv.evaluate_render(job, new).results}
    assert again[7].passed, again[7].detail
    assert {n for n, r in again.items() if not r.passed} <= set(failing) - {7}


@pytest.mark.slow
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not available")
def test_quiet_mix_is_remastered_and_remuxed_without_a_picture_encode(tmp_path: Path) -> None:
    """A render 3 dB under target fails invariant 8 only; repair re-masters the mix against the encoded file and
    re-muxes it onto the same encoded picture (the video stream is copied bit for bit)."""
    ix = build_take_index()
    doc = build_cut_doc(ix)
    job = Job.create("loud-job", work_dir=tmp_path)
    job.save_media_info(ix.media)
    job.save_index(ix)
    job.save_doc(doc)
    src = synth_source_audio(ix)
    sf.write(str(job.audio_path), src.astype(np.float32), SR, subtype="FLOAT")
    tl = compile_timeline(doc, ix, job=job)
    rd = job.renders_dir / "r1"
    make_render(rd, tl, ix, source=src, gain_db=-3.0)
    run = inv.evaluate_render(job, rd)
    failing = [r.to_dict() for r in run.results if not r.passed]
    assert [f["number"] for f in failing] == [8], inv.summarize(run.results)

    res = rp.repair(job, doc, ix, rd, failing)
    assert res.passed and res.mode == "audio" and res.fixed == ["8 loudness"] and res.remaining == []
    assert res.render_dir.name == "r2" and json.loads((res.render_dir / "render.json").read_text())["repair"] == {
        "from": "r1", "mode": "audio"}

    def video_md5(p: Path) -> str:
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
                              capture_output=True, text=True, check=True).stdout.strip()

    assert video_md5(res.render_dir / "final_tiktok.mp4") == video_md5(rd / "final_tiktok.mp4")
    L = qm.measure_loudness(qm.decode_final_audio(res.render_dir / "final_tiktok.mp4"))
    assert L.integrated_lufs == pytest.approx(-14.0, abs=0.3)
    x, _ = sf.read(str(rd / "mix.wav"), always_2d=True)  # the failed render's mix is untouched
    assert qm.measure_loudness(x.T).integrated_lufs == pytest.approx(-17.0, abs=0.3)


def test_an_overlay_pass_carries_the_qa_asr_cache(tmp_path: Path) -> None:
    """The overlay pass re-uses the failed render's mix: its QA ASR raw responses (keyed on the audio's SHA-256) are
    linked into the new render so identical audio is re-parsed, not billed again; the work WAV is not linked (it is
    rewritten in place, which would change the old render's file through a hard link)."""
    old, new = tmp_path / "r1", tmp_path / "r2"
    asr = old / "qa" / "asr_final_tiktok"
    asr.mkdir(parents=True)
    new.mkdir()
    for name in ("asr_raw_elevenlabs.json", "asr_raw_elevenlabs.meta.json"):
        (asr / name).write_text("{}")
    (asr / "asr_input.wav").write_bytes(b"RIFF")
    (old / "qa" / "invariants.json").write_text("{}")
    assert rp._link_asr_cache(old, new) == 2
    got = new / "qa" / "asr_final_tiktok"
    assert sorted(p.name for p in got.iterdir()) == ["asr_raw_elevenlabs.json", "asr_raw_elevenlabs.meta.json"]
    assert (got / "asr_raw_elevenlabs.json").stat().st_ino == (asr / "asr_raw_elevenlabs.json").stat().st_ino
    assert not (new / "qa" / "invariants.json").exists()
    assert rp._link_asr_cache(tmp_path / "nothing", new) == 0
