"""Tests for scripts/plan_eval.py (the offline planner eval harness, standard-tier plan §3 step 11).

Keyless and offline: the job is the hand-built fixture take (conftest: s001 hook; s002 false start in c01; s003 the
recommended retake; s004 opens with ``{Um}``; s005 the spoken CTA) with ``build_cut_doc`` saved as the delivered
document (it keeps s001, s003, s004 without the filler and s005; pins payoff w0018 and CTA w0032-w0033). The planner
record comes from the real :func:`studio.plan.planner.plan` with a scripted ``FunctionModel`` (no API call). Nothing
renders: the harness compiles without media.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from studio.agent import providers as pv
from studio.compile.timeline import compile as compile_timeline
from studio.config import Settings
from studio.doc.model import CutDocument, RemovedRange, Segment, new_document
from studio.doc.ops import apply_ops
from studio.jobs import Job, write_json_atomic
from studio.perception.index import TakeIndex
from studio.plan import planner
from studio.plan.code_plan import CodePlanError, code_plan
from studio.plan.digest import build_digest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plan_eval.py"
FAKE_ANT = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE-0123456789"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("studio_plan_eval_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


pe = _load_script()

#: A decision validate_decision accepts on the fixture take: the retake, payoff, CTA, filler cut and two accents.
VALID: dict[str, Any] = {
    "style_primary": "explainer", "goal": "teach restraint in one line", "hook_promise": "most people over-cut",
    "choose_take": [{"cluster_id": "c01", "sentence_id": "s003"}],
    "payoff": {"from_word": "w0018", "to_word": "w0018"}, "cta": {"from_word": "w0032", "to_word": "w0033"},
    "must_keep": [{"from_word": "w0020", "to_word": "w0028"}], "deliberate_beat_gap_ids": ["g0006"],
    "cut_filler_word_ids": ["w0019"], "caption_emphasis_word_ids": ["w0018", "w0024"],
    "notes": "keep the breath after restraint",
}


# ============================================================================================ helpers
def deliver(job: Job, doc: CutDocument) -> Path:
    """Save ``doc`` the way the pipeline delivers it (``deliver/edit_v{n}.json``)."""
    return write_json_atomic(job.path("deliver", f"edit_v{doc.version}.json"), doc)


@pytest.fixture
def eval_job(job: Job, cut_doc: CutDocument) -> Job:
    """The fixture job with a platform in its meta and ``build_cut_doc`` delivered."""
    job.update_meta(platform="tiktok", source_name="fixture.mov", brief=None, style=None)
    deliver(job, cut_doc)
    return job


def spoken_kept(doc: CutDocument, ix: TakeIndex) -> set[str]:
    return {w for w in doc.kept_word_ids(ix) if ix.word(w).is_spoken}


def scripted_planner(answer: dict[str, Any]) -> FunctionModel:
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps(answer))],
                             usage=RequestUsage(input_tokens=1200, output_tokens=300))

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield json.dumps(answer)

    return FunctionModel(reply, stream_function=stream, model_name="scripted-planner")


# ============================================================================================ the reference
def test_reference_view_reads_the_delivered_story(eval_job: Job, take_index: TakeIndex, cut_doc: CutDocument) -> None:
    rec = pe.evaluate_job(eval_job.root)
    assert rec["errors"] == []
    assert rec["job"] == eval_job.id and rec["platform"] == "tiktok" and rec["source"] == "fixture.mov"
    assert rec["reference"] == {"kind": "delivered", "version": 1,
                                "path": str(eval_job.root / "deliver" / "edit_v1.json")}
    assert rec["index"] == {"duration_s": round(take_index.duration_us / 1e6, 2), "words": 41, "spoken_words": 41,
                            "sentences": 5, "clusters": 1}
    # the digest is the planner's own, sized in estimated tokens
    dg = build_digest(take_index, platform="tiktok")
    assert rec["digest"] == {"chars": len(dg.text), "tokens_est": dg.stats["tokens_est"], "sentences_shown": 5,
                             "omitted_sentence_ids": []}
    assert 0 < rec["digest"]["tokens_est"] < 2000

    rd = rec["reference_doc"]
    assert rd["kept_spoken"] == rd["kept_lexical"] == 35  # 41 words - the s002 false start (5) - {Um}
    assert rd["in_point"] == "w0001 Most" and rd["out_point"] == "w0041 week."
    assert rd["pins"] == {"payoff": ["w0018"], "cta": ["w0032", "w0033"], "must_keep": []}
    # sentences gone whole carry the document's removed reason; a partly kept one is "trimmed"
    assert [(d["id"], d["reason"]) for d in rd["dropped_sentences"]] == [("s002", "false start (c01 retake chosen)")]
    assert rd["trimmed_sentences"] == [{"id": "s004", "removed_words": 1, "reason": "filler"}]
    assert rd["removed_ranges"] == 2
    # captions: the document's size and pages, plus where the placer put them on a job-less compile
    tl = compile_timeline(cut_doc, take_index, job=None)
    caps = rd["captions"]
    assert caps["enabled"] and caps["size_px"] == 88 and caps["pages"] == len(cut_doc.captions.pages)
    assert caps["emphasis_words"] == 3 and caps["position"] == "auto"
    assert sum(caps["placements"].values()) == len(tl.captions)
    assert rd["estimated_s"] == round(cut_doc.estimated_duration_us(take_index) / 1e6, 2)
    assert rd["compiled_s"] == round(float(tl.duration), 3)
    assert rd["texts"] == ["hook_title:Stop over-editing"] and rd["punch_ins"] == []
    assert rd["radio_blockers"] == []
    assert rec["planner"] == {"status": "absent", "note": "no logs/plan.json"}


def test_code_plan_is_replayed_and_compared(eval_job: Job, take_index: TakeIndex, cut_doc: CutDocument) -> None:
    rec = pe.evaluate_job(eval_job.root)
    cp = rec["code_plan"]
    assert cp["status"] == "ok" and cp["rejected_ops"] == [] and cp["op_counts"]["set_story"] == 1
    assert cp["summary"].startswith("code plan (no decision")
    # the same plan, applied independently, gives the same agreement numbers
    ops = code_plan(take_index, platform="tiktok", decision=None)
    doc, _ = apply_ops(new_document(platforms=("tiktok",)), ops, take_index, by="test", persist=False)
    ref, mine = spoken_kept(cut_doc, take_index), spoken_kept(doc, take_index)
    vs = cp["vs_reference"]
    assert cp["ops"] == len(ops)
    assert vs["jaccard"] == round(len(ref & mine) / len(ref | mine), 4)
    assert cp["doc"]["kept_spoken"] == len(mine)
    assert vs["duration_s"]["candidate_estimated"] == round(doc.estimated_duration_us(take_index) / 1e6, 2)
    # what the doctrine fixes on this take, whatever the code plan's tuning: the retake wins, the payoff and the
    # spoken CTA stay, the take ends on the CTA, and the story is the reference's to within a filler
    assert vs["dropped_sentences"]["both"] == ["s002"] and vs["dropped_sentences"]["only_reference"] == {}
    assert set(vs["trimmed_sentences"]) == {"both_same_words", "both_other_words", "only_reference", "only_candidate"}
    assert vs["pins_kept"]["all"] is True
    assert vs["pins_kept"]["payoff"] == {"pinned": 1, "kept": 1, "missing": []}
    assert vs["pins_kept"]["cta"] == {"pinned": 2, "kept": 2, "missing": []}
    assert vs["same_out_point"] is True and vs["out_point"]["candidate"] == "w0041 week."
    assert vs["jaccard"] >= 0.9
    assert vs["captions"]["reference_size_px"] == 88 and vs["captions"]["candidate_size_px"] == cp["doc"]["captions"][
        "size_px"]
    assert vs["radio_blockers"] == {"reference": 0, "candidate": len(cp["doc"]["radio_blockers"])}


def test_compare_views_measures_hand_built_differences(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    ix = take_index
    ref = pe.view_doc(cut_doc, ix)
    same = pe.compare_views(ref, ref, ix)
    assert same["jaccard"] == 1.0 and same["same_out_point"] and same["same_in_point"]
    assert same["only_in_reference"] == same["only_in_candidate"] == []
    assert same["dropped_sentences"] == {"both": ["s002"], "only_reference": {}, "only_candidate": {}}
    assert same["trimmed_sentences"] == {"both_same_words": ["s004"], "both_other_words": [], "only_reference": {},
                                         "only_candidate": {}}

    # trims: keeping the {Um} but cutting "the rest." trims s004 on other words; keeping all of s004 does not trim it
    other = cut_doc.model_copy(update={
        "segments": [*cut_doc.segments[:2], Segment(id="seg003", from_word="w0019", to_word="w0026"),
                     cut_doc.segments[3]],
        "removed": [cut_doc.removed[0], RemovedRange(from_word="w0027", to_word="w0028", reason="test trim")]})
    tr = pe.compare_views(ref, pe.view_doc(other, ix), ix)["trimmed_sentences"]
    assert tr["both_other_words"] == ["s004"] and tr["both_same_words"] == []
    whole = cut_doc.model_copy(update={
        "segments": [*cut_doc.segments[:2], Segment(id="seg003", from_word="w0019", to_word="w0028"),
                     cut_doc.segments[3]],
        "removed": [cut_doc.removed[0]]})
    tr = pe.compare_views(ref, pe.view_doc(whole, ix), ix)["trimmed_sentences"]
    assert tr == {"both_same_words": [], "both_other_words": [], "only_reference": {"s004": "filler"},
                  "only_candidate": {}}

    # a candidate that ends on the payoff's sentence: s005 (the CTA, 13 words) is gone
    cut = cut_doc.model_copy(update={
        "segments": cut_doc.segments[:3],
        "removed": [*cut_doc.removed, RemovedRange(from_word="w0029", to_word="w0041", reason="after_payoff: test")],
        "captions": None,
    })
    vs = pe.compare_views(ref, pe.view_doc(cut, ix), ix)
    assert vs["jaccard"] == round(22 / 35, 4)
    (span,) = vs["only_in_reference"]
    assert (span["from"], span["to"], span["words"]) == ("w0029", "w0041", 13)
    assert span["text"].startswith("If this helped, follow me for more") and span["text"].endswith("...")
    assert len(span["text"]) <= 60
    assert vs["only_in_candidate"] == []
    assert vs["pins_kept"]["cta"] == {"pinned": 2, "kept": 0, "missing": ["w0032", "w0033"]}
    assert vs["pins_kept"]["payoff"]["kept"] == 1 and vs["pins_kept"]["all"] is False
    assert vs["same_out_point"] is False and vs["out_point"] == {"reference": "w0041 week.", "candidate": "w0028 rest."}
    assert vs["dropped_sentences"]["only_candidate"] == {"s005": "after_payoff: test"}
    assert vs["captions"]["candidate_size_px"] is None and vs["captions"]["candidate_pages"] == 0
    # the listener's gate sees the dropped CTA pins on the candidate
    assert vs["radio_blockers"]["candidate"] == 1

    assert pe.jaccard([], []) == 1.0 and pe.jaccard(["a"], ["b"]) == 0.0 and pe.jaccard("ab", "bc") == 1 / 3


# ============================================================================================ planner decision
def test_planner_decision_in_plan_json_is_evaluated(eval_job: Job, take_index: TakeIndex, settings: Settings,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pv, "_native_model", lambda spec, role: scripted_planner(VALID))
    res = planner.plan(eval_job, take_index, build_digest(take_index, platform="tiktok"),
                       settings=settings.with_keys(anthropic=FAKE_ANT))
    assert res.decision is not None and (eval_job.root / "logs" / "plan.json").is_file()

    pl = pe.evaluate_job(eval_job.root)["planner"]
    assert pl["status"] == "evaluated" and pl["attempts"] == 1 and pl["model"] == "claude-sonnet-5-5"
    usage = json.loads((eval_job.root / "logs" / "plan.json").read_text())["usage"]
    assert pl["usage"] == {k: usage[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                                 "cache_write_tokens")}
    assert pl["usage"]["input_tokens"] > 0
    assert pl["valid"] is True and pl["rejections"] == [] and pl["unknown_id_rejections"] == 0
    assert pl["style"] == "explainer"
    # the decision's own story (retake chosen, filler cut) is exactly the delivered one
    assert pl["story_jaccard"] == 1.0
    assert pl["drops"] == {"both": ["s002"], "only_decision": {}, "only_reference": {}}
    assert pl["payoff"] == {"decision": "w0018-w0018", "reference_pins": 1, "overlap": 1, "same_end": True,
                            "kept_by_reference": True}
    assert pl["cta"]["overlap"] == 2 and pl["cta"]["same_end"] is True
    assert pl["emphasis"]["overlap"] == ["w0018", "w0024"]
    assert pl["punch_in"] == {"decision": None, "reference": [], "same_sentence": None}
    assert pl["choose_take"] == {"c01": {"decision": "s003", "reference": "s003"}}
    # and the code plan built from it is compared like the pure-code plan
    pcp = pl["code_plan"]
    assert pcp["status"] == "ok" and pcp["summary"].startswith("code plan (planner decision")
    assert "w0018" in pcp["doc"]["pins"]["payoff"]
    assert pcp["vs_reference"]["pins_kept"]["all"] and pcp["vs_reference"]["same_out_point"]
    assert pcp["vs_reference"]["jaccard"] >= 0.9


def test_rejected_or_missing_decisions(eval_job: Job, take_index: TakeIndex) -> None:
    bad = {**VALID, "payoff": {"from_word": "w0018", "to_word": "w9999"},
           "drop_sentences": [{"sentence_id": "s099", "reason": "tangent"}]}
    write_json_atomic(eval_job.root / "logs" / "plan.json", {"decision": bad, "attempts": 2, "note": "hand-made"})
    pl = pe.evaluate_job(eval_job.root)["planner"]
    assert pl["status"] == "evaluated" and pl["valid"] is False
    assert pl["unknown_id_rejections"] == 2 and len(pl["rejections"]) == 2
    assert pl["payoff"]["overlap"] == 0 and pl["payoff"]["same_end"] is False
    assert pl["code_plan"]["status"] == "ok"  # unknown IDs are skipped by the code plan, never fatal

    write_json_atomic(eval_job.root / "logs" / "plan.json", {"decision": None, "note": "planner refusal; pure-code"})
    assert pe.evaluate_job(eval_job.root)["planner"] == {"status": "no decision", "note": "planner refusal; pure-code"}

    write_json_atomic(eval_job.root / "logs" / "plan.json", {"decision": {"goal": 3}})
    pl = pe.evaluate_job(eval_job.root)["planner"]
    assert pl["status"] == "unreadable" and "not a PlanDecision" in pl["note"]

    write_json_atomic(eval_job.root / "logs" / "plan.json", ["not", "a", "record"])
    assert pe.evaluate_job(eval_job.root)["planner"]["status"] == "unreadable"


# ============================================================================================ CLI and outcomes
def test_main_prints_json_and_writes_the_record(eval_job: Job, capsys: pytest.CaptureFixture[str]) -> None:
    assert pe.main([str(eval_job.root), "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [r["job"] for r in records] == [eval_job.id]
    written = json.loads((eval_job.root / "logs" / "plan_eval.json").read_text())
    assert written == records[0]

    assert pe.main([str(eval_job.root), "--no-write"]) == 0
    table = capsys.readouterr().out
    assert table.splitlines()[0].split()[:5] == ["job", "reference", "src", "s", "digest"]
    row = next(ln for ln in table.splitlines() if ln.startswith(eval_job.id))
    assert "v1 dlv" in row and "same" in row and "absent" in row and f"{records[0]['digest']['tokens_est']}t" in row
    assert "code plan: jaccard" in table

    (eval_job.root / "logs" / "plan_eval.json").unlink()
    assert pe.main([str(eval_job.root), "--no-write", "--json"]) == 0
    capsys.readouterr()
    assert not (eval_job.root / "logs" / "plan_eval.json").exists()


def test_code_plan_unavailable_is_reported_not_a_crash(eval_job: Job, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "studio.plan.code_plan", None)  # the import fails
    assert pe.main([str(eval_job.root), "--no-write", "--json"]) == 0
    rec = json.loads(capsys.readouterr().out)[0]
    assert rec["errors"] == [] and rec["code_plan"]["status"] == "unavailable"
    assert rec["code_plan"]["note"].startswith("code plan unavailable")
    assert "reference_doc" in rec and rec["digest"]["tokens_est"] > 0  # the rest of the eval still runs


def test_code_plan_import_regression_exits_nonzero(eval_job: Job, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """The module exists now: a SyntaxError or a broken import inside it is a regression the offline eval must
    report (exit 1), not hide as "unavailable"."""
    import builtins

    real_import = builtins.__import__

    def broken(name: str, *a: Any, **k: Any) -> Any:
        if name == "studio.plan.code_plan":
            raise SyntaxError("invalid syntax (code_plan.py, line 42)")
        return real_import(name, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(builtins, "__import__", broken)
        assert pe.main([str(eval_job.root), "--no-write", "--json"]) == 1
    rec = json.loads(capsys.readouterr().out)[0]
    assert rec["errors"] and "SyntaxError" in rec["errors"][0]["error"]


def test_code_plan_error_is_an_outcome_and_a_crash_exits_nonzero(eval_job: Job, work_dir: Path, cut_doc: CutDocument,
                                                                 monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    import studio.plan.code_plan as cp_mod

    def refuse(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        raise CodePlanError("every word of the take is removed")

    with monkeypatch.context() as m:  # scoped: undo() would also revert the conftest's settings isolation
        m.setattr(cp_mod, "code_plan", refuse)
        assert pe.main([str(eval_job.root), "--no-write", "--json"]) == 0
    rec = json.loads(capsys.readouterr().out)[0]
    assert rec["code_plan"] == {"status": "failed", "note": "code plan refused the take: every word of the take is "
                                                            "removed"}

    broken = Job.create("broken-job", work_dir=work_dir, meta={"platform": "tiktok"})
    write_json_atomic(broken.index_path, {"words": "not a list"})
    deliver(broken, cut_doc)
    assert pe.main([str(eval_job.root), str(broken.root), "--json"]) == 1
    out = capsys.readouterr()
    good, bad = json.loads(out.out)
    assert good["errors"] == [] and good["code_plan"]["status"] == "ok"
    assert bad["errors"] and "ValidationError" in bad["errors"][0]["error"] and bad["errors"][0]["traceback"]
    assert "broken-job" in out.err
    assert json.loads((broken.root / "logs" / "plan_eval.json").read_text())["errors"]  # the crash is on record
    assert pe.main([str(broken.root), "--no-write"]) == 1
    assert "CRASH" in capsys.readouterr().out


def test_discovery_and_the_champion_fallback(work_dir: Path, take_index: TakeIndex, cut_doc: CutDocument,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    a = Job.create("a-delivered", work_dir=work_dir, meta={"platform": "tiktok"})
    a.save_index(take_index)
    for v in (9, 10):  # numeric order, not lexical: edit_v10 is the delivered one
        deliver(a, cut_doc.model_copy(update={"version": v}))
    b = Job.create("b-champion", work_dir=work_dir, meta={"platform": "reels"})
    b.save_index(take_index)
    b.save_doc(cut_doc.model_copy(update={"version": 2}))
    b.save_doc(cut_doc.model_copy(update={"version": 3, "segments": cut_doc.segments[:2]}))
    b.save_json("logs/loop_state.json", {"champion_doc": 2})
    c = Job.create("c-no-reference", work_dir=work_dir)
    c.save_index(take_index)
    (work_dir / "stray.log").write_text("not a job")

    assert pe.discover_jobs(work_dir) == [a.root, b.root]
    assert pe.discover_jobs(work_dir / "missing") == []
    ra, rb = pe.find_reference(a.root), pe.find_reference(b.root)
    assert (ra.kind, ra.version, ra.path.name) == ("delivered", 10, "edit_v10.json")
    assert (rb.kind, rb.version, rb.path.name) == ("champion", 2, "v2.json")
    assert pe.find_reference(c.root) is None

    assert pe.main(["--work-dir", str(work_dir), "--json", "--no-write"]) == 0
    recs = json.loads(capsys.readouterr().out)
    assert [(r["job"], r["platform"], r["reference"]["kind"]) for r in recs] == [
        ("a-delivered", "tiktok", "delivered"), ("b-champion", "reels", "champion")]
    assert recs[1]["code_plan"]["vs_reference"]["same_out_point"] is True  # v2 (whole story), not the latest v3

    # a named job without any reference is still digested, and skipped for the comparison
    assert pe.main([str(c.root), "--json", "--no-write"]) == 0
    rc = json.loads(capsys.readouterr().out)[0]
    assert rc["reference"] is None and rc["code_plan"]["status"] == "skipped" and rc["digest"]["tokens_est"] > 0

    empty = work_dir / "empty"
    empty.mkdir()
    assert pe.main(["--work-dir", str(empty)]) == 0
    assert "no jobs to evaluate" in capsys.readouterr().err


def test_runs_as_a_script(eval_job: Job) -> None:
    proc = subprocess.run([sys.executable, str(SCRIPT), str(eval_job.root), "--json", "--no-write"],
                          capture_output=True, text=True, env=dict(os.environ), timeout=300)
    assert proc.returncode == 0, proc.stderr
    rec = json.loads(proc.stdout)[0]
    assert rec["job"] == eval_job.id and rec["code_plan"]["status"] == "ok"
    assert not (eval_job.root / "logs" / "plan_eval.json").exists()
