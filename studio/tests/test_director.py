"""Director (studio.agent.director): staged runs over an EditSession, driven by scripted pydantic-ai models.

Keyless and offline: the Director's model is a ``FunctionModel`` script (``tests/director_script.py``) that
plays a complete, valid edit of the hand-built fixture take; failures (refusals, overloads, a model that
never finishes) are scripted too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from director_script import BRIEF_OPS, CARD, STORY, ScriptedDirector, full_plan
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from studio.agent import director as dr
from studio.agent.director import (
    DIRECTOR_TOOL_NAMES,
    FINISHING_PASSES,
    STAGE_ORDER,
    Director,
    DirectorOptions,
    DirectorSession,
    director_spec,
    render_relevant,
)
from studio.agent.providers import ModelSpec
from studio.agent.tools import TOOL_NAMES
from studio.doc.model import AssetRef, Licence
from studio.jobs import Job
from studio.perception.index import TakeIndex

FFMPEG = shutil.which("ffmpeg")


def make_director(job: Job, index: TakeIndex, plan: dict[str, list[Any]] | None = None, **kw: Any
                  ) -> tuple[Director, ScriptedDirector]:
    sc = ScriptedDirector(plan if plan is not None else full_plan())
    d = Director(job, index, model=sc.model(), sleep=lambda _s: None, **kw)
    sc.director = d
    return d, sc


@pytest.fixture(scope="module")
def proxy_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("director_media") / "proxy17.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=30:duration=17", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture
def media_job(job: Job, proxy_video: Path) -> Job:
    shutil.copy(proxy_video, job.proxy_path)
    shutil.copy(proxy_video, job.mezz_path)  # frames decode by content; the fixture index is 1080x1920 portrait
    return job


# ============================================================================================ full run
def test_full_scripted_edit_runs_every_stage_in_order(job: Job, take_index: TakeIndex) -> None:
    d, sc = make_director(job, take_index, brief="teach one editing tip", style="educational")
    doc = d.run()
    assert [r.stage for r in d.state.completed] == list(STAGE_ORDER)
    assert all(not r.forced for r in d.state.completed), [(r.stage, r.note) for r in d.state.completed]
    # the document carries every stage's work
    assert doc.brief is not None and doc.brief.goal.startswith("Keep the pauses") and len(doc.brief.rubric) == 3
    assert [(s.from_word, s.to_word) for s in doc.segments] == [(x["from_word"], x["to_word"]) for x in STORY]
    assert doc.segments[2].gap_overrides == {"g0009": 350}
    assert [i.id for i in doc.inserts] == ["i001"] and doc.captions is not None and len(doc.captions.pages) >= 8
    assert doc.pins.payoff_word_ids == ["w0018"] and doc.pins.cta_word_ids == ["w0032", "w0033"]
    assert doc.audio.music is None
    # the story stage refused to finish before the radio test, then passed it
    fins = sc.results_for("finish_stage", "story")
    assert fins[0].startswith("NOT FINISHED") and "radio_test" in fins[0] and fins[1].startswith("Stage story finished")
    radio = sc.results_for("radio_test")[0]
    assert "RADIO TEST" in radio and "removed 5 words" in radio and "The real secret is restr-" in radio
    # compile_check reports the pause asked → kept
    cc = sc.results_for("compile_check", "fine_cut")[0]
    assert "g0009" in cc and "asked 350 ms → kept" in cc and "Seams (3)" in cc and "pause trim" in cc
    # every stage prompt arrived in order, the brief carries the creator's words
    stages = [s for s, _p in sc.prompts]
    assert stages == list(STAGE_ORDER)
    assert "teach one editing tip" in sc.prompts[0][1] and "educational" in sc.prompts[0][1]
    # state + conversation saved for resume; the trace has one start/done pair per stage
    state = json.loads((job.logs_dir / "director_state.json").read_text())
    assert [r["stage"] for r in state["completed"]] == list(STAGE_ORDER)
    assert (job.logs_dir / "director_messages.json").exists()
    trace = job.read_trace()
    done = [e["stage"] for e in trace if e["event"] == "director_stage" and e["status"] == "done"]
    assert done == list(STAGE_ORDER)
    assert any(e["event"] == "model_call" and e["role"] == "director" for e in trace)


def test_tool_list_is_stable_and_complete(job: Job, take_index: TakeIndex) -> None:
    d, _sc = make_director(job, take_index)
    names = [t.name for t in d.tools]
    assert set(names) == set(TOOL_NAMES) | set(DIRECTOR_TOOL_NAMES)
    assert len(names) == len(set(names))
    before = [id(t) for t in d.tools]
    d.run(stages=("brief",))
    assert [id(t) for t in d.tools] == before  # never rebuilt between stages


# ============================================================================================ gating + exits
def test_stage_gating_follows_doctrine_order(job: Job, take_index: TakeIndex) -> None:
    s = DirectorSession(job, take_index)
    s.set_stage("story")
    out = s.apply_ops([{"op": "set_story", "segments": STORY}, {"op": "set_color", "spec": {"exposure": 0.2}}])
    assert out.applied == 1 and "not available in the story stage" in out.text
    s.set_stage("reframe")
    out = s.apply_ops([{"op": "cut_words", "from_word": "w0020", "to_word": "w0020", "reason": "x"}])
    assert out.applied == 0 and "cut family" in out.text
    s.set_stage("captions")  # earlier finishing layers stay adjustable, later ones are closed
    assert set(s.allowed_families()) == {"framing", "inserts", "captions", "meta"}
    s.set_stage("color")
    assert "color" in s.allowed_families() and "cut" not in s.allowed_families()
    for st in ("revise", "chat", "finalize"):
        s.set_stage(st)
        assert "cut" in s.allowed_families()
    with pytest.raises(ValueError):
        s.set_stage("polish")
    assert FINISHING_PASSES == ("reframe", "broll", "captions", "sound", "color")


def test_brief_exit_requires_goal_and_rubric(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.set_stage("brief")
    assert "brief is not written" in d.t_finish_stage("a summary of nothing")
    d.session.apply_ops([{"op": "set_brief", "brief": {"goal": "one idea"}}])
    assert "no rubric" in d.t_finish_stage("brief without rubric")
    d.session.apply_ops([{"op": "set_brief", "brief": {"goal": "one idea", "rubric": ["hook by 3 s?"]}}])
    assert d.t_finish_stage("brief written").startswith("Stage brief finished")
    assert d.session.stage_done and d.session.stage_summary == "brief written"
    assert d.t_finish_stage("x").startswith("NOT FINISHED")  # too short


def test_fine_cut_exit_needs_compile_check_and_seam_frames(media_job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(media_job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    d.session.set_stage("fine_cut")
    msg = d.t_finish_stage("fine cut done")
    assert "compile_check" in msg and "check_seams" in msg
    d.t_compile_check()
    assert "check_seams" in d.t_finish_stage("fine cut done") and "compile_check" not in d.t_finish_stage("xxxxxxxxx")
    out = d.t_check_seams()
    assert isinstance(out, list) and any("seam 1" in str(x) for x in out if isinstance(x, str))
    assert any(not isinstance(x, str) for x in out)  # the seam sheet image
    assert d.t_finish_stage("fine cut done").startswith("Stage fine_cut finished")
    # a later cut change invalidates both checks
    d.session.apply_ops([{"op": "set_gap", "gap_id": "g0009", "ms": 300}])
    assert "compile_check" in d.t_finish_stage("fine cut done")


def test_story_radio_test_blockers(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.set_stage("story")
    # both takes of c01 kept and the story ends on the false start's cut-off word
    d.session.apply_ops([{"op": "set_story", "segments": [{"from_word": "w0001", "to_word": "w0013"}]}])
    radio = d.t_radio_test()
    assert "ends on a cut-off word" in radio and "[blocker]" in radio and "incomplete" in radio
    assert "ends on a cut-off word" in d.t_finish_stage("verdict: fine")
    d.session.apply_ops([{"op": "set_story", "segments": [{"from_word": "w0001", "to_word": "w0018"}]}])
    radio = d.t_radio_test()
    assert "c01: 2 takes kept" in radio
    assert d.t_finish_stage("both takes kept on purpose").startswith("Stage story finished")


def test_output_validator_keeps_the_stage_open_until_finish(job: Job, take_index: TakeIndex) -> None:
    plan = {"brief": ["I think we are done", [("meta_ops", {"ops": BRIEF_OPS})],
                      [("finish_stage", {"summary": "brief written with rubric"})], "done"]}
    d, sc = make_director(job, take_index, plan)
    rec = d.run_stage("brief")
    assert not rec.forced and rec.summary == "brief written with rubric"
    assert sc.calls == 4  # text → retry → ops → finish → final text


def test_a_model_that_never_finishes_is_ended_by_the_harness(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index, {"brief": ["nope"] * 20},
                         options=DirectorOptions(request_limits={"brief": 4}))
    rec = d.run_stage("brief")
    assert rec.forced and "harness" in rec.note
    assert d.state.done("brief")  # recorded (forced) so a resume moves on
    ev = [e for e in job.read_trace() if e["event"] == "director_stage" and e.get("status") == "forced"]
    assert ev and ev[0]["stage"] == "brief"


def test_ask_creator_once_and_only_in_the_brief(job: Job, take_index: TakeIndex) -> None:
    import asyncio

    d, _ = make_director(job, take_index)
    s = d.session
    s.set_stage("story")
    assert asyncio.run(s.call_ask_creator("Keep the CTA?", None)).startswith("NOT ASKED")
    s.set_stage("brief")
    assert asyncio.run(s.call_ask_creator("Keep the CTA?", ["yes", "no"])).startswith("NO ANSWER")
    assert asyncio.run(s.call_ask_creator("And the title?", None)).startswith("NOT ASKED")
    assert len(s.questions) == 1


# ============================================================================================ tools
def test_compile_check_reports_kept_pauses_and_clamped_punch(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    s = d.session
    s.apply_ops([{"op": "set_story", "segments": STORY}, {"op": "set_gap", "gap_id": "g0009", "ms": 250},
                 {"op": "set_framing", "seg_id": "seg003", "scale": 1.6}])
    out = d.t_compile_check()
    assert "g0009 (600 ms measured): asked 250 ms → kept" in out
    assert "seg003" in out and "asked x1.60 → rendered x" in out
    rendered = float(out.split("asked x1.60 → rendered x")[1][:4])
    assert rendered <= 1.25 + 1e-6 and "CLAMPED" in out  # 1080p source: face-safe ceiling
    assert "Captions:" in out and "Seams (" in out


def test_auto_captions_applies_a_plan_and_reports_reading_speed(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    d.session.set_stage("captions")
    out = d.t_auto_captions(case="upper", size_px=80)
    assert "Applied 1 of 1 ops" in out and "CPS" in out and "80px" in out
    doc = d.session.doc
    assert doc.captions is not None and doc.captions.style.case == "upper" and doc.captions.pages
    proposed = d.t_auto_captions(apply=False)
    assert "proposed, not applied" in proposed and d.session.doc.version == doc.version


def test_music_voice_and_generation_degrade_without_keys(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    assert "unavailable" in d.t_music_options()
    assert d.t_voice_plan().startswith("ERROR")  # no media/audio.wav in the fixture job
    assert "unavailable" in d.t_broll_generate("a keyboard")
    assert "no key" in d.t_broll_search("hands typing", ["typing"], "w0020", "w0024")
    assert "pop" in d.t_sfx_guidance("pop").lower()


def test_measure_color_reads_the_mezzanine(media_job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(media_job, take_index)
    out = d.t_measure_color(["w0001", "w0030"])
    assert "w0001: frame Y' mean" in out and "face Y'" in out and "a*" in out


def test_broll_search_and_use_enforce_the_bar(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
    from studio.broll import rank, sources
    from studio.broll.rank import JudgmentSheet
    from studio.broll.sources import BrollCandidate

    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    d.session.set_stage("broll")
    d.settings = d.settings.with_keys(pexels="test-key-123456")
    cand = BrollCandidate(source="pexels", source_id="42", kind="video", url="https://x/42.mp4", width=1080,
                          height=1920, duration_ms=6000, description="hands typing on a keyboard",
                          licence={"name": "Pexels License", "url": "https://www.pexels.com/license/"})
    seen: dict[str, Any] = {}
    monkeypatch.setattr(sources, "search_many", lambda qs, **kw: seen.setdefault("queries", qs) and [cand])
    monkeypatch.setattr(rank, "rank_candidates", lambda cands, **kw: [(cand, 0.8, {"score": 0.8})])
    sheet = tmp_path / "sheet.png"
    from PIL import Image

    Image.new("RGB", (400, 300), (40, 40, 40)).save(sheet)

    def fake_sheet(top: Any, **kw: Any) -> JudgmentSheet:
        seen["need"] = kw["need"]
        return JudgmentSheet(paths=[sheet], candidate_ids=[c.id for c, _s, _d in top], prompt="score 1-5 …")

    monkeypatch.setattr(rank, "contact_sheet_for_judgment", fake_sheet)

    def fake_download(c: BrollCandidate, job: Job, **kw: Any) -> AssetRef:
        return job.register_asset(AssetRef(kind="video", source="pexels", source_id=c.source_id, path="assets/broll/x",
                                           licence=Licence(name="Pexels License")), asset_id=c.id)

    monkeypatch.setattr(sources, "download", fake_download)
    out = d.t_broll_search("hands typing on a keyboard", ["typing", "keyboard"], "w0020", "w0024", mode="split_top")
    assert isinstance(out, list) and "pxv_42" in out[0] and "score 1-5" in out[0]
    assert seen["need"].mode == "split_top" and seen["need"].duration_s and seen["need"].duration_s > 0.5
    assert (job.assets_dir / "broll" / "pool.json").exists()
    low = d.t_broll_use("pxv_42", "w0020", "w0024", "show the typing", {"subject": 5, "readable": 3, "clean": 5,
                                                                        "look": 5, "sharp": 5})
    assert low.startswith("NOT USED") and not d.session.doc.inserts
    ok = d.t_broll_use("pxv_42", "w0020", "w0024", "show the typing", dict.fromkeys(dr._RUBRIC_ITEMS, 4))
    assert "Registered pxv_42" in ok and "Applied 1 of 1" in ok
    ins = d.session.doc.inserts[0]
    lic = ins.effective_licence
    assert ins.mode == "split_top" and lic is not None and lic.name == "Pexels License"
    assert "unknown candidate" in d.t_broll_use("nope", "w0020", "w0024", "x", {})
    # the pool survives a new Director on the same job
    d2, _ = make_director(job, take_index)
    assert "pxv_42" in d2.broll_pool


# ============================================================================================ revise / chat
def test_revise_branches_from_the_champion_and_reports_change(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    champion = d.session.doc.version
    d.session.apply_ops([{"op": "cut_words", "from_word": "w0030", "to_word": "w0031", "reason": "challenger"}])
    plan = {"revise": [[("inserts_ops", {"ops": [CARD]})], [("finish_stage", {"summary": "added the rule card"})],
                       "ok"]}
    d2, sc = make_director(job, take_index, plan)
    res = d2.revise("[P1][broll][w0020] the rule is abstract", base_version=champion, round_no=1)
    assert res.changed and res.doc.parent_version == champion and res.base_version == champion
    assert len(res.doc.kept_word_ids(take_index)) == 35  # the losing branch's cut is not inherited
    assert "the rule is abstract" in sc.prompts[0][1] and "RENDER REVIEW" in sc.prompts[0][1]
    # a revision that only leaves a note changes nothing that renders
    plan2 = {"revise": [[("meta_ops", {"ops": [{"op": "note", "text": "declined the taste note"}]})],
                        [("finish_stage", {"summary": "declined: taste"})], "ok"]}
    d3, _ = make_director(job, take_index, plan2)
    res2 = d3.revise("[P2 taste] maybe music", base_version=res.doc.version, round_no=2)
    assert not res2.changed and res2.doc.version > res.doc.version
    assert render_relevant(res2.doc) == render_relevant(res.doc)


def test_chat_applies_the_instruction(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    base = d.session.doc.version
    plan = {"chat": [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}}]})],
                     [("caption_preview", {})], [("finish_stage", {"summary": "captions bigger (96 px)"})], "ok"]}
    d2, sc = make_director(job, take_index, plan)
    out = d2.chat("make the captions bigger", base_version=base)
    assert out["changed"] and out["summary"] == "captions bigger (96 px)" and out["base_version"] == base
    assert out["doc"].captions is not None and out["doc"].captions.style.size_px == 96
    assert any(e.get("applied") for e in out["results"])
    assert "make the captions bigger" in sc.prompts[0][1]


# ============================================================================================ models
def _overloaded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise ModelHTTPError(status_code=529, model_name="claude-fable-5-1",
                         body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})


def test_persistent_overload_switches_to_the_fallback_in_a_fresh_context(job: Job, take_index: TakeIndex) -> None:
    d0, _ = make_director(job, take_index, {"brief": [[("meta_ops", {"ops": BRIEF_OPS})],
                                                      [("finish_stage", {"summary": "brief written ok"})], "ok"]})
    d0.run(stages=("brief",))
    fb = ScriptedDirector({"story": [[("cut_ops", {"ops": [{"op": "set_story", "segments": STORY}]})],
                                     [("radio_test", {})], [("finish_stage", {"summary": "story passes radio"})],
                                     "ok"]}, name="fallback")
    d = Director(job, take_index, model=FunctionModel(_overloaded, model_name="primary"), fallback_model=fb.model(),
                 sleep=lambda _s: None)
    fb.director = d
    rec = d.run_stage("story")
    assert d.using_fallback and d.state.fallback_active and not rec.forced
    assert "taking over this edit" in fb.prompts[0][1] and "brief (v" in fb.prompts[0][1]
    ev = [e for e in job.read_trace() if e["event"] == "director_fallback"]
    assert ev and ev[0]["reason"] == "overloaded"
    errs = [e for e in job.read_trace() if e["event"] == "director_error"]
    assert len(errs) == 3  # two transient retries, then the switch


def test_error_without_fallback_raises_a_clear_error(job: Job, take_index: TakeIndex) -> None:
    d = Director(job, take_index, model=FunctionModel(_overloaded, model_name="primary"), sleep=lambda _s: None)
    with pytest.raises(dr.DirectorError, match="overloaded"):
        d.run_stage("brief")


def test_byok_director_never_falls_back_to_a_house_model(job: Job, take_index: TakeIndex) -> None:
    spec = ModelSpec(provider="openai", model="gpt-6-astra", api_key="sk-test-byok-key-000000000", byok=True)
    d = Director(job, take_index, spec=spec, model=FunctionModel(_overloaded, model_name="byok"),
                 sleep=lambda _s: None)
    assert d._fallback_spec() is None
    house = Director(job, take_index, model=FunctionModel(_overloaded, model_name="x"))
    fb = house._fallback_spec()
    assert fb is not None and fb.model == "claude-opus-5-5" and fb.effort == house.spec.effort


def test_director_spec_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STUDIO_DIRECTOR_EFFORT", raising=False)
    spec = director_spec()
    assert spec.model == "claude-fable-5-1" and spec.effort == "max" and spec.provider == "anthropic"
    monkeypatch.setenv("STUDIO_DIRECTOR_EFFORT", "high")
    assert director_spec().effort == "high"


def test_resume_skips_finished_stages_and_restores_the_conversation(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.run(stages=("brief", "story"))
    n_hist = len(d.history)
    d2, sc2 = make_director(job, take_index)
    assert d2.state.done("brief") and d2.state.done("story") and len(d2.history) == n_hist
    d2.run(stages=("brief", "story", "fine_cut"))
    assert [s for s, _p in sc2.prompts] == ["fine_cut"]  # the finished stages were skipped
    assert d2.session.doc.segments  # the story from the first run carried over
    # a lost conversation resumes in a fresh context with the stage summaries
    (job.logs_dir / "director_messages.json").unlink()
    d3, sc3 = make_director(job, take_index, {"reframe": [[("finish_stage", {"summary": "none needed here"})], "ok"]})
    assert d3.history == []
    d3.run(stages=("reframe",))
    assert "taking over this edit" in sc3.prompts[0][1] and "story (v" in sc3.prompts[0][1]


def test_tool_errors_never_kill_the_run(job: Job, take_index: TakeIndex) -> None:
    plan = {"story": [[("radio_test", {}), ("compile_check", {}), ("broll_use", {"candidate_id": "x",
                                                                                "anchor_from_word": "w0001",
                                                                                "anchor_to_word": "w0002", "job": "j",
                                                                                "scores": {}})],
                      [("cut_ops", {"ops": [{"op": "set_story", "segments": STORY}]})], [("radio_test", {})],
                      [("finish_stage", {"summary": "story passes the radio test"})], "ok"]}
    d, sc = make_director(job, take_index, plan)
    rec = d.run_stage("story")
    assert not rec.forced
    assert sc.results_for("radio_test")[0].startswith("ERROR")
    assert sc.results_for("compile_check")[0].startswith("ERROR")
    calls = [e for e in job.read_trace() if e["event"] == "tool_call"]
    assert {"radio_test", "compile_check", "broll_use", "finish_stage"} <= {c["tool"] for c in calls}


def test_scripted_text_parts_are_accepted_after_finish(job: Job, take_index: TakeIndex) -> None:
    """A final response that mixes text and a tool call does not break the stage (graceful end)."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from director_script import responses_since_prompt

        k = responses_since_prompt(messages)
        if k == 0:
            return ModelResponse(parts=[ToolCallPart("meta_ops", {"ops": BRIEF_OPS})])
        if k == 1:
            return ModelResponse(parts=[TextPart("finishing"), ToolCallPart("finish_stage",
                                                                            {"summary": "brief done properly"})])
        return ModelResponse(parts=[TextPart("ok")])

    d = Director(job, take_index, model=FunctionModel(fn, model_name="mixed"), sleep=lambda _s: None)
    rec = d.run_stage("brief")
    assert not rec.forced and rec.summary == "brief done properly"


def test_changing_the_document_after_finish_reopens_the_stage(job: Job, take_index: TakeIndex) -> None:
    plan = {"brief": [[("meta_ops", {"ops": BRIEF_OPS})], [("finish_stage", {"summary": "brief written first"})],
                      [("meta_ops", {"ops": [{"op": "note", "text": "one more thought"}]})], "done?",
                      [("finish_stage", {"summary": "brief written, note added"})], "done"]}
    d, sc = make_director(job, take_index, plan)
    rec = d.run_stage("brief")
    assert not rec.forced and rec.summary == "brief written, note added"
    assert d.session.finished_version == d.session.doc.version


def test_a_rejected_old_conversation_continues_in_a_fresh_context(job: Job, take_index: TakeIndex) -> None:
    d0, _ = make_director(job, take_index, {"brief": [[("meta_ops", {"ops": BRIEF_OPS})],
                                                      [("finish_stage", {"summary": "brief written ok"})], "ok"]})
    d0.run(stages=("brief",))
    seen: dict[str, Any] = {"calls": 0}
    inner = ScriptedDirector({"story": [[("apply_ops", {"ops": [{"op": "set_story", "segments": STORY}]})],
                                        [("radio_test", {})], [("finish_stage", {"summary": "story passes"})], "ok"]})

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["calls"] += 1
        from director_script import prompt_text

        if "STAGE 1/9" in prompt_text(messages[:1]):  # the restored history (an expired file id, say) is rejected
            raise ModelHTTPError(status_code=400, model_name="m", body={"type": "error", "error": {
                "type": "invalid_request_error", "message": "file_id not found or expired"}})
        return inner(messages, info)

    d = Director(job, take_index, model=FunctionModel(fn, model_name="m"), sleep=lambda _s: None)
    inner.director = d
    assert d.history  # restored from the brief run
    rec = d.run_stage("story")
    assert not rec.forced and rec.summary == "story passes"
    assert "taking over this edit" in inner.prompts[0][1]
    notes = [e for e in job.read_trace() if e["event"] == "director_note"]
    assert any("fresh context" in n["note"] for n in notes)


def test_schema_generated_arguments_never_crash_the_director_tools(job: Job, take_index: TakeIndex) -> None:
    """pydantic-ai's TestModel calls every Director tool with schema-generated (mostly nonsense) arguments: each
    comes back as a readable result or ERROR line, never an exception that ends the run."""
    from pydantic_ai.models.test import TestModel

    d = Director(job, take_index, model=TestModel(call_tools=list(DIRECTOR_TOOL_NAMES)), sleep=lambda _s: None,
                 options=DirectorOptions(request_limits={"story": 6}))
    rec = d.run_stage("story")
    calls = [e for e in job.read_trace() if e["event"] == "tool_call"]
    assert set(DIRECTOR_TOOL_NAMES) <= {c["tool"] for c in calls}
    assert all("internally" not in (c.get("error") or "") for c in calls), [c for c in calls if c.get("error")]
    assert rec.stage == "story"


# ============================================================================================ review-loop fixes
def test_caption_report_offers_measured_options_not_instructions(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    d.session.set_stage("captions")
    out = d.t_auto_captions()
    assert "keep them above the head" not in out
    assert "placement options on this take" in out and "(a) just under the chin" in out
    assert "(c) a base reframe" in out and "(d) above the head" in out and "caption_preview" in out


def test_captions_stage_needs_a_look_at_the_rendered_captions(media_job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(media_job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    d.session.set_stage("captions")
    d.t_auto_captions()
    msg = d.t_finish_stage("auto-paged captions, default style")
    assert msg.startswith("NOT FINISHED") and "caption_preview" in msg
    out = d.t_caption_preview(["p002"])
    text = out[0] if isinstance(out, list) else out
    assert "CAPTION PREVIEW" in text and "approx" in text and "Caption geometry" in text
    sheet = next(iter((media_job.critique_dir / "caption_preview").rglob("caption_preview_*.png")))
    from PIL import Image

    assert Image.open(sheet).width >= 540
    assert d.t_finish_stage("auto-paged captions, default style").startswith("Stage captions finished")
    # a later caption change needs another look
    d.session.set_stage("captions")
    d.session.apply_ops([{"op": "set_caption_style", "style": {"size_px": 92}}])
    assert "caption_preview" in d.t_finish_stage("captions at 92 px")


def test_revise_and_chat_exit_tests_follow_what_changed(job: Job, take_index: TakeIndex) -> None:
    d, _ = make_director(job, take_index)
    d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    base = d.session.doc
    d.session.base_doc = base
    d.session.set_stage("revise")
    d.session.apply_ops([{"op": "set_story", "segments": [STORY[0], STORY[2]]}])  # a story change with a new seam
    msg = d.t_finish_stage("cut the middle beat after the note")
    assert "radio_test" in msg and "compile_check" in msg and "check_seams" in msg
    assert "caption_preview" in msg  # the story re-pages the captions
    d.t_radio_test()
    d.t_compile_check()
    d.session.seams_unavailable = True
    out = d.t_caption_preview()  # no video in this fixture job: the preview reports it and the exit test moves on
    assert "unavailable" in (out if isinstance(out, str) else out[0])
    assert d.t_finish_stage("cut the middle beat after the note").startswith("Stage revise finished")
    # questions for the critics are kept with the stage
    d.session.set_stage("revise")
    d.t_finish_stage("nothing else to change here", ["At w0008->w0020, does the head jump?"])
    assert d.session.critic_questions == ["At w0008->w0020, does the head jump?"]


def test_brief_prompt_asks_for_viewer_checks_not_plan_restatements() -> None:
    p = dr._stage_prompt("brief", {})
    assert "what a viewer experiences" in p and "Never restate your own add/skip decisions" in p
    f = dr._stage_prompt("finalize", {})
    assert "rendered at full quality and judged pairwise" in f and "questions_for_critics" in f
