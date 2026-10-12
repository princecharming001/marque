"""The standard tier in the pipeline: ``edit(tier="standard")``, the standard chat and the upgrade to the Director tier.

Fast tests run the real digest, planner (a scripted ``FunctionModel`` under :func:`providers.build_model`), code plan,
validation and radio gate, loop state, report and delivery over the fixture job; the render and QA are fakes (a render
directory holding a real compiled timeline and placeholder files; QA results written where the report reads them), and
so is repair where a test needs it. The slow test runs a whole standard edit and a chat on a synthetic take with real
renders and QA (only the ASR and the planner are scripted).
"""

from __future__ import annotations

import json
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from director_script import ScriptedDirector
from qa_fixtures import SR, synth_source_audio
from test_plan_planner import FAKE_ANT, VALID, Script, install, server_error

from studio import pipeline
from studio.agent.loop import STANDARD_REVIEW_NOTE, STANDARD_STOP_REASON, LoopState
from studio.config import reset_settings
from studio.doc.model import CutDocument
from studio.jobs import Job
from studio.media.models import MediaInfo
from studio.perception.index import TakeIndex
from studio.plan import repair as repair_mod
from studio.plan.repair import RepairResult
from studio.qa.invariants import INVARIANTS, InvariantResult, QaRun

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "0")


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A house Anthropic key in the environment (the planner model itself is scripted)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANT)
    reset_settings()


@pytest.fixture
def ready_job(job: Job, take_index: TakeIndex, tmp_path: Path) -> Job:
    """The fixture job as if ingest + perception had run: a mezzanine (a 2 s lavfi clip is enough for the colour
    line), the proxy and the dialogue on disk."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    import subprocess

    clip = tmp_path / "clip.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=30:duration=17", "-c:v", "libx264", "-preset", "ultrafast", "-crf",
                    "32", "-pix_fmt", "yuv420p", str(clip)], check=True)
    shutil.copy(clip, job.proxy_path)
    shutil.copy(clip, job.mezz_path)
    sf.write(str(job.audio_path), synth_source_audio(take_index).astype(np.float32), SR, subtype="FLOAT")
    job.update_meta(source_path="/nowhere/take.mov", brief="teach restraint", style="explainer", platform="tiktok")
    job.trace("asr", provider="elevenlabs", model="scribe_v2", audio_seconds=17.0, purpose="index", cached=False)
    return job


class Fakes:
    """Fake ``render_full`` and ``evaluate_render``. A render holds the document's real compiled timeline and
    placeholder finals/intermediates; QA fails invariant 7 on the render names in ``fail`` and writes its results
    where the report reads them."""

    def __init__(self, fail: set[str] | None = None, fail_n: int = 7):
        self.fail = set(fail or ())
        self.fail_n = fail_n  # the invariant a failing render fails
        self.renders: list[tuple[int, str, str]] = []  # (doc version, profile, render name)
        self.qa: list[str] = []

    def render_full(self, job: Job, doc: CutDocument, index: TakeIndex | None = None, *, preview: bool = False,
                    min_free: int | None = None, on_wait: Any = None, profile: str = "final") -> Path:
        from studio.compile.timeline import compile as compile_timeline

        rd = job.new_render_dir()
        compile_timeline(doc, index or job.load_index(), job=job).save(rd / "timeline.json")
        for name in ("final_tiktok.mp4", "final_nomusic.mp4", "cover.jpg", "captions.srt", "aroll.mov", "mix.wav"):
            (rd / name).write_bytes(name.encode())
        (rd / "render.json").write_text(json.dumps({"doc_version": doc.version, "profile": profile}))
        self.renders.append((doc.version, profile, rd.name))
        return rd

    def evaluate_render(self, job: Job, render_dir: Any = None, *, settings: Any = None, **kw: Any) -> QaRun:
        rd = Path(render_dir)
        v = json.loads((rd / "render.json").read_text())["doc_version"]
        bad = rd.name in self.fail
        k = self.fail_n
        res = [InvariantResult(id=INVARIANTS[n][0], number=n, name=INVARIANTS[n][1], passed=not (bad and n == k),
                               detail="p002 over the UI strip" if bad and n == k else "",
                               refs=["p002"] if bad and n == k else []) for n in range(1, 11)]
        (rd / "qa").mkdir(exist_ok=True)
        (rd / "qa" / "invariants.json").write_text(json.dumps({"render_dir": str(rd), "doc_version": v,
                                                               "passed": not bad,
                                                               "results": [r.to_dict() for r in res]}))
        self.qa.append(rd.name)
        return QaRun(render_dir=rd, doc_version=v, results=res)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Fakes:
        import studio.agent.loop as loop_mod
        import studio.qa.invariants as inv_mod

        monkeypatch.setattr(loop_mod, "render_full", self.render_full)
        monkeypatch.setattr(inv_mod, "evaluate_render", self.evaluate_render)
        return self


def _planner(*steps: Any) -> Script:
    """A scripted planner answering as the house planner model (so its calls are priced in the cost line)."""
    return Script(*steps, name="claude-sonnet-5-5")


def _model_calls(job: Job) -> list[dict[str, Any]]:
    return [e for e in job.read_trace() if e["event"] == "model_call"]


def _record(job: Job) -> dict[str, Any]:
    return json.loads((job.logs_dir / "standard.json").read_text())


# ============================================================================================ edit
def test_standard_edit_plans_once_renders_once_and_delivers_one_final(ready_job: Job, keyed: None, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    msgs: list[str] = []
    res = pipeline.edit(ready_job.root, tier="standard", out=tmp_path / "out", log=msgs.append)
    # the result: standard tier, NOT REVIEWED, one render, no rounds
    assert res.tier == "standard" and res.reviewed is False and res.review_note == STANDARD_REVIEW_NOTE
    assert res.invariants_passed is True and res.rounds == 0 and res.stop_reason == STANDARD_STOP_REASON
    assert res.render == "r1" and res.compute_seconds is not None and res.compute_seconds >= 0
    assert len(script.calls) == 1 and fk.renders == [(res.doc_version, "standard", "r1")] and fk.qa == ["r1"]
    # the planner's decision reached the document (take choice, pins, the false start out)
    doc = ready_job.load_doc(res.doc_version)
    kept = doc.kept_word_ids(ready_job.load_index())
    assert "w0018" in doc.pins.payoff_word_ids and {"w0032", "w0033"} <= set(doc.pins.cta_word_ids)
    assert "w0009" not in kept and "w0014" in kept and "w0019" not in kept  # retake chosen, the {Um} cut
    assert doc.audio.music is None and not doc.inserts and not doc.audio.sfx
    # the digest carried the colour line (the mezzanine exists)
    rec = _record(ready_job)
    assert rec["plan_source"] == "planner decision" and rec["done"] and rec["passed"] and rec["render"] == "r1"
    assert rec["colour"].startswith("Colour") and rec["digest"]["tokens_est"] > 100
    assert rec["planner"]["decision"] and rec["planner"]["attempts"] == 1
    assert rec["cost"]["model_usd"] > 0 and rec["cost"]["asr_usd"] > 0 and rec["cost"]["isolation_usd"] == 0
    assert "Colour (" in script.calls[0]["prompt"]
    # one final: no byte-identical no-music master without music
    assert set(res.finals) == {"tiktok"} and res.finals["tiktok"].exists() and "nomusic" not in res.extras
    assert {"cover", "srt", "doc", "report"} <= set(res.extras)
    assert not (ready_job.root / "deliver" / "final_nomusic.mp4").exists()
    assert (tmp_path / "out" / "final_tiktok.mp4").exists() and not (tmp_path / "out" / "final_nomusic.mp4").exists()
    # the champion is recorded in the loop's state; it keeps its intermediates (a chat's overlay pass reads them)
    st = LoopState.load(ready_job)
    assert (st.champion_render, st.champion_doc, st.champion_passed, st.done) == ("r1", res.doc_version, True, True)
    assert not st.reviewed and st.review_note == STANDARD_REVIEW_NOTE and st.stop_reason == STANDARD_STOP_REASON
    assert (ready_job.renders_dir / "r1" / "aroll.mov").exists() and ready_job.mezz_path.exists()
    meta = ready_job.meta["meta"]
    assert meta["tier"] == "standard" and meta["champion_render"] == "r1" and meta["reviewed"] is False
    # no Director anywhere; the only model calls are the planner's
    assert not (ready_job.logs_dir / "director_state.json").exists()
    assert {(e["role"], e["stage"]) for e in _model_calls(ready_job)} == {("planner", "plan")}
    assert any("NOT REVIEWED" in m for m in msgs) and any(m.startswith("planner (claude-sonnet-5-5") for m in msgs)
    # the cost line: the planner and the ASR, nothing else
    cost = res.estimated_cost
    assert "planner $" in cost and "asr $" in cost and "17 s audio" in cost
    assert "director" not in cost and "critic" not in cost and "watcher" not in cost


def test_report_has_the_plan_section_and_the_standard_not_reviewed_line(ready_job: Job, keyed: None,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    res = pipeline.edit(ready_job.root, tier="standard")
    text = res.extras["report"].read_text()
    assert "- **Review:** **NOT REVIEWED: standard tier** — code QA only" in text
    assert "the critics could not review" not in text
    assert "**all ten invariants pass**" in text
    plan = text.split("## Plan (standard tier)", 1)[1].split("\n## ", 1)[0]
    assert "- **Plan:** planner decision" in plan
    assert "- **Decision:** style explainer; goal: teach restraint in one line" in plan
    assert "take choices: c01 → s003" in plan and "payoff: w0018–w0018 \"restraint.\"" in plan
    assert "fillers cut: w0019" in plan and "punch-in: none" in plan
    assert "- **Digest:**" in plan and "tokens)" in plan and "nothing omitted" in plan
    assert "- **Colour (digest line):** Colour (" in plan
    assert "- **Planner:** claude-sonnet-5-5, 1 call(s);" in plan and "at list prices (plan $" in plan
    assert "- **Outcome:** standard tier: one render" in plan
    assert "### Seams" in plan and "What the join removes" in plan and "word(s) removed" in plan
    # the plan section comes before the brief
    assert text.index("## Plan (standard tier)") < text.index("## Brief")


def test_a_finished_standard_edit_resumes_without_paying_again(ready_job: Job, keyed: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    res = pipeline.edit(ready_job.root, tier="standard")
    res2 = pipeline.edit(ready_job.root)  # no flag: the job's own tier
    assert res2.tier == "standard" and res2.doc_version == res.doc_version and res2.render == "r1"
    assert len(script.calls) == 1 and len(fk.renders) == 1  # no model call, no render
    assert res2.finals["tiktok"].exists() and res2.invariants_passed


def test_a_crashed_standard_edit_reuses_the_recorded_planner_answer(ready_job: Job, keyed: None,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.agent.loop as loop_mod

    fk = Fakes()
    fk.install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)

    def crash(*a: Any, **k: Any) -> Path:
        raise RuntimeError("power cut during the render")

    monkeypatch.setattr(loop_mod, "render_full", crash)
    with pytest.raises(RuntimeError, match="power cut"):
        pipeline.edit(ready_job.root, tier="standard")
    assert len(script.calls) == 1 and not _record(ready_job).get("done")
    monkeypatch.setattr(loop_mod, "render_full", fk.render_full)
    res = pipeline.edit(ready_job.root)
    assert res.invariants_passed and len(script.calls) == 1  # the answer came from logs/plan.json
    rec = _record(ready_job)
    assert rec["planner"]["reused"] and rec["plan_source"] == "planner decision"


def test_planner_failure_falls_back_to_the_pure_code_plan(ready_job: Job, keyed: None,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    Fakes().install(monkeypatch)
    install(monkeypatch, _planner(server_error()))
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.invariants_passed and res.finals["tiktok"].exists()
    rec = _record(ready_job)
    assert rec["plan_source"] == "pure-code plan" and not rec["planner"]["decision"]
    assert "pure-code plan used" in rec["planner"]["note"]
    doc = ready_job.load_doc(res.doc_version)
    assert {"w0032", "w0033"} <= set(doc.pins.cta_word_ids)  # the spoken CTA is pinned without a decision too
    text = res.extras["report"].read_text()
    assert "- **Plan:** pure-code plan" in text and "- **Decision:** none — pure-code plan (planner" in text


def test_no_planner_key_means_the_pure_code_plan_and_no_model_spend(ready_job: Job,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    Fakes().install(monkeypatch)
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.invariants_passed and _record(ready_job)["plan_source"] == "pure-code plan"
    assert _model_calls(ready_job) == [] and "planner" not in res.estimated_cost and "asr $" in res.estimated_cost


def test_a_radio_blocked_decision_drops_to_the_pure_code_plan(ready_job: Job, keyed: None,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.plan.code_plan as cp

    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    real = cp.plan_document
    calls: list[bool] = []

    def gated(job: Job, index: TakeIndex, platform: str, decision: Any, **kw: Any) -> CutDocument:
        calls.append(decision is not None)
        if decision is not None:
            raise cp.RadioBlocked(["the story ends on w0028 before the CTA s005: restore w0029-w0041"])
        return real(job, index, platform, decision, **kw)

    monkeypatch.setattr(cp, "plan_document", gated)
    res = pipeline.edit(ready_job.root, tier="standard")
    assert calls == [True, False] and res.invariants_passed
    rec = _record(ready_job)
    assert rec["plan_source"] == "pure-code plan" and "fails the radio test" in rec["plan_notes"][0]
    assert "restore w0029-w0041" in res.extras["report"].read_text()

    # when the pure-code plan is blocked too, the edit fails clearly and nothing is rendered
    def blocked(*a: Any, **k: Any) -> CutDocument:
        raise cp.RadioBlocked(["nothing pays off the hook"])

    monkeypatch.setattr(cp, "plan_document", blocked)
    renders = len(fk.renders)
    fresh = _copy_job(ready_job, "blocked")
    with pytest.raises(pipeline.PipelineError, match="no plan passes validation and the radio test") as e:
        pipeline.edit(fresh.root, tier="standard")
    assert "nothing pays off the hook" in str(e.value) and "--tier director" in str(e.value)
    assert len(fk.renders) == renders


def _copy_job(src: Job, name: str) -> Job:
    j = Job.create(name, work_dir=src.root.parent)
    for p in (src.mezz_path, src.proxy_path, src.audio_path):
        shutil.copy(p, j.root / p.relative_to(src.root))
    j.save_media_info(src.load_media_info())
    j.save_index(src.load_index())
    j.update_meta(platform="tiktok")
    return j


def test_a_render_still_failing_after_repair_is_not_delivered(ready_job: Job, keyed: None,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes(fail={"r1", "r2", "r3"}).install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    repairs: list[tuple[str, list[int]]] = []

    def fake_repair(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, failures: list[dict[str, Any]],
                    **kw: Any) -> RepairResult:
        assert kw["profile"] == "standard" and (rd / "aroll.mov").exists()  # intermediates kept for the repair
        repairs.append((rd.name, [f["number"] for f in failures]))
        new = fk.render_full(job, doc, index, profile="standard")
        run = fk.evaluate_render(job, new)
        return RepairResult(doc=doc, render_dir=new, passed=False, fixed=[], remaining=["7 text_safe"],
                            rerendered=True, mode="overlay", notes=["p002: no clean height left"],
                            failures=[r.to_dict() for r in run.results if not r.passed])

    monkeypatch.setattr(repair_mod, "repair", fake_repair)
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.invariants_passed is False and res.finals == {} and "report" in res.extras
    assert res.stop_reason == "standard tier: the render fails invariants after repair (7 text_safe); nothing delivered"
    assert not (ready_job.root / "deliver").exists()
    # one repair (on r1), then the pure-code second attempt (r3) which also fails
    assert repairs == [("r1", [7])] and [r[2] for r in fk.renders] == ["r1", "r2", "r3"]
    rec = _record(ready_job)
    assert rec["repair"]["mode"] == "overlay" and rec["second_attempt"]["passed"] is False
    assert rec["second_attempt"]["render"] == "r3" and rec["done"] and not rec["passed"]
    st = LoopState.load(ready_job)
    assert st.champion_render == "r2" and st.champion_passed is False and st.done
    # replaced renders lose everything; the reported one keeps its finals for inspection, not its intermediates
    assert not (ready_job.renders_dir / "r1" / "final_tiktok.mp4").exists()
    assert not (ready_job.renders_dir / "r3" / "final_tiktok.mp4").exists()
    assert (ready_job.renders_dir / "r2" / "final_tiktok.mp4").exists()
    assert not (ready_job.renders_dir / "r2" / "aroll.mov").exists()
    text = res.extras["report"].read_text()
    assert "NOT REVIEWED: standard tier" in text and "- **Repair:** overlay pass on r1 → r2; still fails" in text
    assert "- **Second attempt (pure-code plan):**" in text and "still fails (7 text_safe)" in text


def test_repair_or_the_second_attempt_can_rescue_the_edit(ready_job: Job, keyed: None,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes(fail={"r1", "r2"}).install(monkeypatch)
    install(monkeypatch, _planner(VALID))

    def failing_repair(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, failures: Any,
                       **kw: Any) -> RepairResult:
        new = fk.render_full(job, doc, index, profile="standard")
        run = fk.evaluate_render(job, new)
        return RepairResult(doc=doc, render_dir=new, passed=False, fixed=[], remaining=["7 text_safe"],
                            rerendered=True, mode="overlay", failures=[r.to_dict() for r in run.results
                                                                       if not r.passed])

    monkeypatch.setattr(repair_mod, "repair", failing_repair)
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.invariants_passed and res.render == "r3" and res.finals["tiktok"].exists()
    assert _record(ready_job)["plan_source"] == "pure-code plan (second attempt)"
    assert LoopState.load(ready_job).champion_render == "r3"
    assert not (ready_job.renders_dir / "r2" / "final_tiktok.mp4").exists()


def test_the_standard_tier_caps_the_mezzanine(media_info: MediaInfo, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4K source becomes a 1080p30 mezzanine in the standard tier (1920 high in portrait, 1080 in landscape: the
    orientation is probed first); the Director tier passes no caps."""
    import studio.media.ingest as ingest_mod
    import studio.media.probe as probe_mod

    class Stop(Exception):
        pass

    seen: list[dict[str, Any]] = []
    geometry: dict[str, int] = {}

    def fake_probe(path: Any, *, settings: Any = None) -> MediaInfo:
        return media_info.model_copy(update={**geometry, "fps": Fraction(60)})

    def fake_ingest(src: Any, job: Job, *, settings: Any = None, options: Any = None, **kw: Any) -> MediaInfo:
        seen.append(kw)
        raise Stop

    monkeypatch.setattr(probe_mod, "probe", fake_probe)
    monkeypatch.setattr(ingest_mod, "ingest", fake_ingest)
    video = tmp_path / "take.mov"
    video.write_bytes(b"not really a video")
    for (w, h), tier, want in (((3840, 2160), "standard", {"max_height": 1080, "max_fps": 30}),
                               ((2160, 3840), "standard", {"max_height": 1920, "max_fps": 30}),
                               ((1080, 1080), "standard", {"max_height": 1920, "max_fps": 30}),
                               ((3840, 2160), "director", {})):
        geometry.update(width=w, height=h)
        with pytest.raises(Stop):
            pipeline.edit(video, tier=tier)
        assert seen[-1] == want, (w, h, tier)
    geometry.update(width=1080, height=1920)
    assert pipeline.standard_ingest_caps(video) == {"max_height": 1920, "max_fps": 30}


def test_a_new_job_takes_STUDIO_TIER_and_a_chat_stays_standard(ready_job: Job, keyed: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    Fakes().install(monkeypatch)
    seen = install(monkeypatch, _planner(VALID))
    monkeypatch.setenv("STUDIO_TIER", "standard")
    reset_settings()
    res = pipeline.edit(ready_job.root)
    assert res.tier == "standard" and ready_job.meta["meta"]["tier"] == "standard"
    assert ready_job.meta["meta"]["planner"]["model"] == "claude-sonnet-5-5"
    assert [(s.model, s.effort, role) for s, role in seen] == [("claude-sonnet-5-5", "low", "planner")]
    # a chat on it stays in its tier with the job's planner, unless --planner-model is given now
    seen1 = install(monkeypatch, _planner({"ops": [], "summary": "Already as asked."}))
    pipeline.chat(ready_job.root, "keep it as it is")
    assert seen1[-1][0].model == "claude-sonnet-5-5" and not (ready_job.logs_dir / "director_state.json").exists()
    seen2 = install(monkeypatch, _planner({"ops": [], "summary": "Already as asked."}))
    out = pipeline.chat(ready_job.root, "keep it as it is", planner_model="claude-haiku-5-5")
    assert seen2[-1][0].model == "claude-haiku-5-5" and out.summary == "Already as asked." and out.finals == {}


# ============================================================================================ chat
def test_standard_chat_rerenders_the_overlay_only_and_delivers(ready_job: Job, keyed: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    first = pipeline.edit(ready_job.root, tier="standard")
    overlays: list[tuple[str, int]] = []

    def overlay(job: Job, doc: CutDocument, index: TakeIndex, old: Path, **kw: Any) -> Path:
        assert kw["profile"] == "standard" and (old / "aroll.mov").exists()
        overlays.append((old.name, doc.version))
        return fk.render_full(job, doc, index, profile="standard")

    monkeypatch.setattr(repair_mod, "_overlay_render", overlay)
    script = _planner({"ops": [{"op": "set_caption_style", "style": {"size_px": 72}}],
                     "summary": "Captions are a little smaller."})
    install(monkeypatch, script)
    res = pipeline.chat(ready_job.root, "make the captions a bit smaller")
    assert res.invariants_passed and res.summary == "Captions are a little smaller."
    assert res.critic_note == pipeline.STANDARD_NO_CRITIC and any("caption style" in c for c in res.changes)
    assert overlays == [("r1", res.doc_version)] and fk.renders[-1][2] == "r2"
    assert res.finals["tiktok"].exists() and not (ready_job.root / "deliver" / "final_nomusic.mp4").exists()
    st = LoopState.load(ready_job)
    assert (st.champion_render, st.champion_doc) == ("r2", res.doc_version) and st.review_note == STANDARD_REVIEW_NOTE
    assert not (ready_job.renders_dir / "r1" / "aroll.mov").exists()  # the old champion keeps only its finals
    assert (ready_job.renders_dir / "r1" / "final_tiktok.mp4").exists()
    chat_calls = [e for e in _model_calls(ready_job) if e["stage"] == "chat"]
    assert len(chat_calls) == 1 and chat_calls[0]["role"] == "planner"
    assert ready_job.load_doc(res.doc_version).captions.style.size_px == 72 and res.doc_version > first.doc_version
    # a change to the cut is a full standard render
    install(monkeypatch, _planner({"ops": [{"op": "set_gap", "gap_id": "g0009", "ms": 300}],
                                   "summary": "The pause before the CTA is shorter."}))
    res2 = pipeline.chat(ready_job.root, "tighten the pause before the call to action")
    assert res2.invariants_passed and len(overlays) == 1 and fk.renders[-1][1:] == ("standard", "r3")
    assert ready_job.load_doc(res2.doc_version).segments[-1].gap_overrides == {"g0009": 300}
    # music is refused with a price and nothing is called or rendered
    renders = len(fk.renders)
    install(monkeypatch, _planner(server_error()))
    calls = len(_model_calls(ready_job))
    res3 = pipeline.chat(ready_job.root, "add some lo-fi music under it")
    assert "--tier director" in res3.summary and res3.finals == {} and res3.invariants_passed is None
    assert len(fk.renders) == renders and len(_model_calls(ready_job)) == calls


def test_a_failing_chat_render_is_not_delivered(ready_job: Job, keyed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    pipeline.edit(ready_job.root, tier="standard")
    fk.fail = {"r2", "r3"}

    def no_fix(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, failures: Any, **kw: Any) -> RepairResult:
        return RepairResult(doc=doc, render_dir=rd, passed=False, fixed=[], remaining=["7 text_safe"],
                            rerendered=False, failures=list(failures))

    monkeypatch.setattr(repair_mod, "repair", no_fix)
    install(monkeypatch, _planner({"ops": [{"op": "set_gap", "gap_id": "g0009", "ms": 300}], "summary": "Tighter."}))
    res = pipeline.chat(ready_job.root, "tighten the pause before the call to action")
    assert res.invariants_passed is False and res.finals == {} and "NOT DELIVERED" in res.summary
    assert "7 text_safe" in res.summary and LoopState.load(ready_job).champion_render == "r1"
    assert (ready_job.root / "deliver" / "final_tiktok.mp4").exists()  # the earlier delivery stays


# ============================================================================================ upgrade
def _revise_plan() -> dict[str, list[Any]]:
    return {"revise": [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 80}}]})],
                       [("caption_preview", {})], [("finish_stage", {"summary": "captions at 80 px after the note"})],
                       "ok"]}


def _loop_kwargs(job: Job, fk: Fakes, winners: list[str]) -> dict[str, Any]:
    index = job.load_index()
    return {"renderer": lambda d: fk.render_full(job, d, index, profile="final"), "qa": lambda rd: True,
            "critic": lambda rd, doc: [{"by": "frame_judge", "severity": "P1", "area": "captions", "refs": ["p001"],
                                        "text": "captions crowd the face", "confirmed_by": ["metrics:caption"]}],
            "judge": lambda a, b: {"winner": winners.pop(0) if winners else "tie"},
            "final_watch": lambda rd: {"verdict": "ship"}}


def test_upgrade_to_the_director_starts_at_the_review_rounds(ready_job: Job, keyed: None,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    std = pipeline.edit(ready_job.root, tier="standard")
    sc = ScriptedDirector(_revise_plan())
    msgs: list[str] = []
    res = pipeline.edit(ready_job.root, tier="director", director_model_override=sc.model(),
                        loop_kwargs=_loop_kwargs(ready_job, fk, ["b", "tie", "tie"]), log=msgs.append)
    # the Director never ran its first-pass stages: it started at the render review of the standard champion
    assert [s for s, _p in sc.prompts] == ["revise", "revise", "revise"]
    first = sc.prompts[0][1]
    assert "Standard tier (one planner call" in first and "RENDER REVIEW" in first and "code plan (" in first
    # the standard render was the first champion (not rendered again); the challengers render at full quality
    st = LoopState.load(ready_job)
    assert st.rounds[0].champion == "r1" and st.rounds[0].champion_doc == std.doc_version
    # round 1: the 80 px challenger wins; rounds 2-3: the same op changes nothing (two winless rounds)
    assert [r[1:] for r in fk.renders] == [("standard", "r1"), ("final", "r2")]
    assert [r.outcome for r in st.rounds] == ["win", "no_change", "no_change"]
    assert res.tier == "director" and res.render == "r2" and res.reviewed and res.rounds == 3
    assert res.stop_reason == "2 winless rounds" and len(script.calls) == 1  # the planner was not called again
    meta = ready_job.meta["meta"]
    assert meta["tier"] == "director" and meta["upgraded_from"]["champion_render"] == "r1"
    up = [e for e in ready_job.read_trace() if e["event"] == "upgrade"]
    assert len(up) == 1 and up[0]["start"] == "revise" and any("upgrade from the standard tier" in m for m in msgs)
    report = res.extras["report"].read_text()
    assert "upgraded to the director tier" in report and "NOT REVIEWED: standard tier" not in report
    roles = {e["role"] for e in _model_calls(ready_job)}
    assert roles == {"planner", "director"}


def test_upgrade_honours_the_spend_cap(ready_job: Job, keyed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    pipeline.edit(ready_job.root, tier="standard")
    sc = ScriptedDirector(_revise_plan())
    res = pipeline.edit(ready_job.root, tier="director", max_usd=0.0, director_model_override=sc.model(),
                        loop_kwargs=_loop_kwargs(ready_job, fk, []))
    assert sc.prompts == [] and res.stop_reason.startswith("model spend cap $0.00 reached")
    assert res.render == "r1" and len(fk.renders) == 1 and res.finals["tiktok"].exists()


# ============================================================================================ real renders (slow)
@pytest.mark.slow
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_real_standard_edit_and_chat_on_a_synthetic_take(tmp_path: Path, keyed: None,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest (capped) → index (ASR stubbed) → digest → scripted planner → code plan → one standard render → the ten
    invariants → deliver → report; then a caption chat re-rendered on the champion's A-roll and delivered."""
    from test_integration_smoke import _overlay_ready, _synth_take, _write_source

    from studio.compile import overlays as overlays_mod
    from studio.perception import transcribe as transcribe_mod
    from studio.perception.index import AsrInfo, AsrResult

    audio, words = _synth_take()
    src = _write_source(tmp_path / "take.mov", audio)
    monkeypatch.setattr(transcribe_mod, "transcribe", lambda job_, **kw: AsrResult(
        words=[w.model_copy() for w in words], asr=AsrInfo(provider="fixture", model="truth", language="en"),
        language="en"))
    if not _overlay_ready():
        monkeypatch.setattr(overlays_mod, "render_overlays", lambda *a, **k: None)
    install(monkeypatch, _planner({"style_primary": "explainer", "goal": "keep the pauses that matter",
                                 "hook_promise": "a better edit"}))
    res = pipeline.edit(src, tier="standard", platform="tiktok", out=tmp_path / "out")
    job = Job.open(res.job_dir)
    inv = json.loads((job.renders_dir / res.render / "qa" / "invariants.json").read_text())
    assert res.invariants_passed, inv
    assert res.tier == "standard" and not res.reviewed and res.finals["tiktok"].stat().st_size > 10_000
    assert "nomusic" not in res.extras and (tmp_path / "out" / "final_tiktok.mp4").exists()
    man = json.loads((job.renders_dir / res.render / "render.json").read_text())
    assert man["profile"] == "standard" and man["aroll_codec"] == "lean"
    assert job.load_media_info().width == 1080 and (job.media_dir / "probe.json").exists()
    report = (job.root / "report.md").read_text()
    assert "NOT REVIEWED: standard tier" in report and "## Plan (standard tier)" in report
    assert "planner decision" in report
    # a caption chat: overlay + master on the champion's own A-roll and mix
    install(monkeypatch, _planner({"ops": [{"op": "set_caption_style", "style": {"size_px": 80}}],
                                 "summary": "Captions at 80 px."}))
    chat = pipeline.chat(job.root, "make the captions a little smaller")
    assert chat.invariants_passed and chat.finals["tiktok"].exists(), chat.summary
    st = LoopState.load(job)
    man2 = json.loads((job.renders_dir / st.champion_render / "render.json").read_text())
    assert (man2.get("repair") or {}).get("mode") == "overlay" and man2["profile"] == "standard"
    assert job.load_doc(chat.doc_version).captions.style.size_px == 80


# ============================================================================================ review fixes
def test_an_interrupted_upgrade_is_resumed_as_an_upgrade(ready_job: Job, keyed: None,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """job.json says "director" as soon as the upgrade starts; when the first `--tier director` run stops before the
    hand-over (no disk for the renders here), the retry must still hand the standard champion to the review rounds,
    not run the Director's nine first-pass stages and re-deliver the standard render as a reviewed result."""
    import studio.storage as storage_mod

    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    std = pipeline.edit(ready_job.root, tier="standard")
    real = storage_mod.ensure_space
    hits: list[str] = []

    def flaky(path: Any, need: int, *, what: str = "a render", **kw: Any) -> Any:
        if "this edit's renders" in what and not hits:
            hits.append(what)
            raise storage_mod.DiskSpaceError(f"not enough free disk for {what}")
        return real(path, need, what=what, **kw)

    monkeypatch.setattr(storage_mod, "ensure_space", flaky)
    sc = ScriptedDirector(_revise_plan())
    with pytest.raises(storage_mod.DiskSpaceError):
        pipeline.edit(ready_job.root, tier="director", director_model_override=sc.model(),
                      loop_kwargs=_loop_kwargs(ready_job, fk, ["b", "tie", "tie"]))
    assert ready_job.meta["meta"]["tier"] == "director" and sc.prompts == []
    res = pipeline.edit(ready_job.root, tier="director", director_model_override=sc.model(),
                        loop_kwargs=_loop_kwargs(ready_job, fk, ["b", "tie", "tie"]))
    assert [s for s, _p in sc.prompts] == ["revise", "revise", "revise"]  # no first-pass stage was prompted
    assert len([e for e in ready_job.read_trace() if e["event"] == "upgrade"]) == 1
    st = LoopState.load(ready_job)
    assert st.rounds[0].champion == "r1" and st.rounds[0].champion_doc == std.doc_version
    assert res.render == "r2" and res.reviewed and res.rounds == 3 and res.stop_reason == "2 winless rounds"
    # a later resume of the upgraded job never seeds the hand-over again
    pipeline.edit(ready_job.root, director_model_override=sc.model(), loop_kwargs=_loop_kwargs(ready_job, fk, []))
    assert len([e for e in ready_job.read_trace() if e["event"] == "upgrade"]) == 1


def test_a_failed_planner_call_is_made_again_on_a_crash_resume(ready_job: Job, keyed: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A planner call that got no answer (a 5xx here) was not paid for: a crash-resume asks again instead of keeping
    the pure-code plan; an answer that was obtained is still reused (never paid twice)."""
    import studio.agent.loop as loop_mod

    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(server_error()))

    def crash(*a: Any, **k: Any) -> Path:
        raise RuntimeError("power cut during the render")

    monkeypatch.setattr(loop_mod, "render_full", crash)
    with pytest.raises(RuntimeError, match="power cut"):
        pipeline.edit(ready_job.root, tier="standard")
    monkeypatch.setattr(loop_mod, "render_full", fk.render_full)
    script = _planner(VALID)
    install(monkeypatch, script)
    res = pipeline.edit(ready_job.root)
    rec = _record(ready_job)
    assert len(script.calls) == 1 and res.invariants_passed
    assert rec["plan_source"] == "planner decision" and not rec["planner"].get("reused")
    # an answer validation rejected after its retry was paid for: it is reused like a decision
    assert pipeline._paid_answer({"decision": None, "attempts": 2, "errors": [],
                                  "rejections_by_attempt": [["x"], ["y"]]})
    assert not pipeline._paid_answer({"decision": None, "attempts": 1, "errors": [{"kind": "server"}],
                                      "rejections_by_attempt": [[]]})


def test_a_standard_edit_after_an_upgrade_replans_instead_of_relabelling(ready_job: Job, keyed: None,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """`--tier standard` on an upgraded job must not re-deliver the Director's reviewed champion as an unreviewed
    standard result (job.json and loop_state.json would disagree): it runs a fresh standard plan (the recorded planner
    answer is reused, so nothing is paid again) and records that render as the standard champion."""
    fk = Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    pipeline.edit(ready_job.root, tier="standard")
    sc = ScriptedDirector(_revise_plan())
    up = pipeline.edit(ready_job.root, tier="director", director_model_override=sc.model(),
                       loop_kwargs=_loop_kwargs(ready_job, fk, ["b", "tie", "tie"]))
    assert up.render == "r2" and up.reviewed
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.render == "r3" and res.tier == "standard" and res.stop_reason == STANDARD_STOP_REASON
    assert len(script.calls) == 1 and fk.renders[-1][1:] == ("standard", "r3")
    st = LoopState.load(ready_job)
    meta = ready_job.meta["meta"]
    assert st.champion_render == meta["champion_render"] == "r3"
    assert st.reviewed is False and meta["reviewed"] is False and st.review_note == meta["review_note"]


def test_the_standard_tier_refuses_a_source_over_10_minutes(media_info: MediaInfo, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.media.ingest as ingest_mod
    import studio.media.probe as probe_mod

    seen: list[Any] = []
    monkeypatch.setattr(probe_mod, "probe", lambda path, *, settings=None: media_info.model_copy(
        update={"duration_us": 11 * 60 * 1_000_000}))
    monkeypatch.setattr(ingest_mod, "ingest", lambda *a, **k: seen.append(a))
    video = tmp_path / "long.mov"
    video.write_bytes(b"not really a video")
    with pytest.raises(pipeline.PipelineError, match=r"11\.0 min long, over the tier's 10 min limit") as e:
        pipeline.edit(video, tier="standard")
    assert "--tier director" in str(e.value) and seen == []  # refused before ingest (and so before any ASR)


def test_a_take_no_plan_can_fit_is_refused_before_the_planner(ready_job: Job, keyed: None,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.plan.code_plan as cp

    fk = Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    monkeypatch.setattr(cp, "story_speech_s", lambda index, **kw: 200.0)
    with pytest.raises(pipeline.PipelineError, match="no plan can bring it under the tier's 90 s output limit"):
        pipeline.edit(ready_job.root, tier="standard")
    assert script.calls == [] and fk.renders == []


def test_a_plan_over_90_seconds_is_not_rendered(ready_job: Job, keyed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    monkeypatch.setattr(CutDocument, "estimated_duration_us", lambda self, index: 120_000_000)
    with pytest.raises(pipeline.PipelineError, match=r"runs about 120 s, over the tier's 90 s output limit"):
        pipeline.edit(ready_job.root, tier="standard")
    assert fk.renders == []


def test_the_second_attempt_runs_only_when_the_plan_can_matter(ready_job: Job, keyed: None,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """An engine or media fault (here invariant 10, the delivery format) would fail the pure-code render too: no third
    render. Neither does a pure-code plan that renders exactly what failed."""
    fk = Fakes(fail={"r1", "r2", "r3"}, fail_n=10).install(monkeypatch)
    install(monkeypatch, _planner(VALID))

    def no_fix(job: Job, doc: CutDocument, index: TakeIndex, rd: Path, failures: Any, **kw: Any) -> RepairResult:
        return RepairResult(doc=doc, render_dir=rd, passed=False, fixed=[], remaining=["10 delivery_format"],
                            rerendered=False, failures=list(failures))

    monkeypatch.setattr(repair_mod, "repair", no_fix)
    res = pipeline.edit(ready_job.root, tier="standard")
    assert res.invariants_passed is False and [r[2] for r in fk.renders] == ["r1"]
    assert "do not depend on the plan (10 delivery_format)" in _record(ready_job)["second_attempt"]["skipped"]
    assert "- **Second attempt (pure-code plan):** not run: the remaining failures do not depend on the plan" \
        in res.extras["report"].read_text()
    # a plan-dependent failure, but a decision that changes nothing the pure-code plan would not: no second render
    fk2 = Fakes(fail={"r1", "r2", "r3"}).install(monkeypatch)
    fresh = _copy_job(ready_job, "same-render")
    fresh.update_meta(brief="teach restraint", style="explainer")
    install(monkeypatch, _planner({"style_primary": "explainer", "goal": "teach restraint", "hook_promise": "x"}))
    res2 = pipeline.edit(fresh.root, tier="standard")
    assert res2.invariants_passed is False and [r[2] for r in fk2.renders] == ["r1"]
    assert "renders the same picture and sound" in _record(fresh)["second_attempt"]["skipped"]


def test_the_standard_tier_honours_max_usd(ready_job: Job, keyed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    Fakes().install(monkeypatch)
    script = _planner(VALID)
    install(monkeypatch, script)
    res = pipeline.edit(ready_job.root, tier="standard", max_usd=0.0)
    assert res.invariants_passed and script.calls == [] and _record(ready_job)["plan_source"] == "pure-code plan"
    assert "spend cap $0.00" in _record(ready_job)["planner"]["note"]


def test_standard_chat_traces_a_failed_report_and_plans_a_cut_naming_footage(ready_job: Job, keyed: None,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.qa.report as report_mod

    Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    pipeline.edit(ready_job.root, tier="standard")

    def broken(*a: Any, **k: Any) -> Path:
        raise ValueError("report template exploded")

    monkeypatch.setattr(report_mod, "write_report", broken)
    script = _planner({"ops": [{"op": "cut_words", "from_word": "w0006", "to_word": "w0007",
                                "reason": "creator: the cough"}], "summary": "The cough is gone."})
    install(monkeypatch, script)
    res = pipeline.chat(ready_job.root, "remove the footage of me coughing")
    assert len(script.calls) == 1 and res.invariants_passed and res.finals["tiktok"].exists()
    assert "not part of the standard tier" not in res.summary
    notes = [e["note"] for e in ready_job.read_trace() if e["event"] == "pipeline_note"]
    assert any(n.startswith("report failed: ValueError: report template exploded") for n in notes)


def test_render_uses_the_jobs_tier_profile_and_index_caps_the_standard_mezzanine(
        ready_job: Job, keyed: None, media_info: MediaInfo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import studio.media.ingest as ingest_mod
    import studio.media.probe as probe_mod
    import studio.perception.index as index_mod

    fk = Fakes().install(monkeypatch)
    install(monkeypatch, _planner(VALID))
    pipeline.edit(ready_job.root, tier="standard")
    pipeline.render(ready_job.root)
    assert fk.renders[-1][1] == "standard"
    ready_job.update_meta(tier="director")
    pipeline.render(ready_job.root)
    assert fk.renders[-1][1] == "final"
    # `studio index` in the standard tier ingests with the tier's caps (a later standard edit expects them)
    seen: list[dict[str, Any]] = []

    class Stop(Exception):
        pass

    monkeypatch.setattr(probe_mod, "probe", lambda path, *, settings=None: media_info.model_copy(
        update={"width": 2160, "height": 3840, "fps": Fraction(60)}))

    def fake_ingest(src: Any, job: Job, *, settings: Any = None, **kw: Any) -> None:
        seen.append(kw)
        raise Stop

    monkeypatch.setattr(ingest_mod, "ingest", fake_ingest)
    monkeypatch.setattr(index_mod, "build_index", lambda *a, **k: None)
    video = tmp_path / "take.mov"
    video.write_bytes(b"not really a video")
    with pytest.raises(Stop):
        pipeline.index(video, tier="standard")
    with pytest.raises(Stop):
        pipeline.index(video, tier="director")
    assert seen == [{"max_height": 1920, "max_fps": 30}, {}]
