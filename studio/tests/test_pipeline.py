"""Pipeline (studio.pipeline) and CLI wiring for ``studio edit`` / ``studio chat``.

Fast tests drive the real Director (scripted model) over the fixture job with the champion loop's renderer, QA,
critics and judges injected. The slow test runs a whole edit on a synthetic take with real renders (only the ASR
call and the models are scripted).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from director_script import ScriptedDirector, full_plan, structured
from qa_fixtures import SR, synth_source_audio

from studio import cli, pipeline
from studio.agent.critics import Judge, JudgePanel
from studio.agent.loop import LoopState
from studio.doc.model import CutDocument
from studio.jobs import Job
from studio.perception.index import TakeIndex

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def _slots(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_SLOTS_DIR", str(tmp_path_factory.mktemp("slots")))
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "0")


@pytest.fixture(scope="module")
def small_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    out = tmp_path_factory.mktemp("pipeline_media") / "proxy17.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=30:duration=17", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture
def ready_job(job: Job, take_index: TakeIndex, small_video: Path) -> Job:
    """The fixture job as if ingest + perception had run (mezzanine, proxy and dialogue on disk)."""
    shutil.copy(small_video, job.proxy_path)
    shutil.copy(small_video, job.mezz_path)
    sf.write(str(job.audio_path), synth_source_audio(take_index).astype(np.float32), SR, subtype="FLOAT")
    job.update_meta(source_path="/nowhere/take.mov", brief="teach restraint", style="educational", platform="tiktok")
    return job


class LoopFakes:
    def __init__(self, job: Job, winners: list[str] | None = None, qa_seq: list[bool] | None = None):
        self.job = job
        self.renders: list[int] = []
        self.winners = list(winners or ["tie", "tie"])
        self.qa_seq = list(qa_seq or [])

    def renderer(self, doc: CutDocument) -> Path:
        rd = self.job.new_render_dir()
        (rd / "render.json").write_text(json.dumps({"doc_version": doc.version}))
        (rd / "timeline.json").write_text("{}")
        for name in ("final_tiktok.mp4", "final_nomusic.mp4", "cover.jpg", "captions.srt", "aroll.mov", "mix.wav"):
            (rd / name).write_bytes(name.encode())
        self.renders.append(doc.version)
        return rd

    def qa(self, rd: Path) -> bool:
        return self.qa_seq.pop(0) if self.qa_seq else True

    def kwargs(self) -> dict[str, Any]:
        return {"renderer": self.renderer, "qa": self.qa,
                "critic": lambda rd, doc: [{"by": "frame_judge", "severity": "P1", "area": "pacing",
                                            "refs": ["w0028"], "text": "slow after 'rest.'",
                                            "confirmed_by": ["metrics:long_pause"]}],
                "judge": lambda a, b: {"winner": self.winners.pop(0) if self.winners else "tie"},
                "final_watch": lambda rd: {"verdict": "ship"}}


def _plan() -> dict[str, list[Any]]:
    plan = full_plan()
    plan["revise"] = [[("cut_ops", {"ops": [{"op": "set_gap", "gap_id": "g0009", "ms": 300}]})],
                      [("finish_stage", {"summary": "trimmed g0009 after the note"})], "ok"]
    plan["chat"] = [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}}]})],
                    [("caption_preview", {})], [("finish_stage", {"summary": "captions at 96 px"})], "ok"]
    return plan


# ============================================================================================ edit
def test_edit_runs_every_step_then_resumes_without_redoing_work(ready_job: Job, tmp_path: Path) -> None:
    sc = ScriptedDirector(_plan())
    fk = LoopFakes(ready_job)
    msgs: list[str] = []
    res = pipeline.edit(ready_job.root, director_model_override=sc.model(), loop_kwargs=fk.kwargs(),
                        out=tmp_path / "out", log=msgs.append)
    assert res.job_dir == ready_job.root and res.doc_version is not None
    assert res.finals["tiktok"] == ready_job.root / "deliver" / "final_tiktok.mp4" and res.finals["tiktok"].exists()
    assert {"nomusic", "cover", "srt", "doc"} <= set(res.extras)
    doc_json = json.loads(res.extras["doc"].read_text())
    assert doc_json["version"] == res.doc_version and doc_json["brief"]["goal"].startswith("Keep the pauses")
    assert (tmp_path / "out" / "final_tiktok.mp4").exists() and (tmp_path / "out" / res.extras["doc"].name).exists()
    assert res.rounds == 2 and res.stop_reason == "2 winless rounds" and res.render == "r1"
    assert any("Director" in m for m in msgs) and any("resumed" in m for m in msgs)
    # the brief/style stored on the job reached the Director
    assert "teach restraint" in sc.prompts[0][1] and "educational" in sc.prompts[0][1]
    stages = [s for s, _p in sc.prompts]
    assert stages[:9] == ["brief", "story", "fine_cut", "reframe", "broll", "captions", "sound", "color", "finalize"]
    assert stages[9:] == ["revise", "revise"]
    # the delivered champion's intermediates are gone
    assert not (ready_job.renders_dir / "r1" / "aroll.mov").exists()
    meta = ready_job.meta["meta"]
    assert meta["champion_render"] == "r1" and meta["champion_doc"] == res.doc_version
    # a second run finds everything done: no model call, no render
    calls, renders = sc.calls, list(fk.renders)
    res2 = pipeline.edit(ready_job.root, director_model_override=sc.model(), loop_kwargs=fk.kwargs())
    assert sc.calls == calls and fk.renders == renders and res2.doc_version == res.doc_version


def test_edit_of_a_new_video_ingests_and_indexes(job: Job, take_index: TakeIndex, small_video: Path,
                                                 monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import studio.media.ingest as ingest_mod
    import studio.perception.index as index_mod

    seen: dict[str, Any] = {}

    def fake_ingest(src: Any, j: Job, **kw: Any) -> Any:
        seen["src"] = str(src)
        j.save_media_info(take_index.media)
        shutil.copy(small_video, j.mezz_path)
        shutil.copy(small_video, j.proxy_path)
        sf.write(str(j.audio_path), synth_source_audio(take_index).astype(np.float32), SR, subtype="FLOAT")
        return take_index.media

    def fake_index(j: Job, **kw: Any) -> TakeIndex:
        j.save_index(take_index)
        return take_index

    monkeypatch.setattr(ingest_mod, "ingest", fake_ingest)
    monkeypatch.setattr(index_mod, "build_index", fake_index)
    video = tmp_path / "take.mov"
    shutil.copy(small_video, video)
    fk = LoopFakes(job)
    res = pipeline.edit(video, brief="one tip", style="storytime", platform="reels",
                        director_model_override=ScriptedDirector(_plan()).model(), loop_kwargs=fk.kwargs(), rounds=1)
    assert seen["src"] == str(video.resolve()) and res.job_dir != job.root
    new = Job.open(res.job_dir)
    meta = new.meta["meta"]
    assert meta["brief"] == "one tip" and meta["style"] == "storytime" and meta["platform"] == "reels"
    assert new.load_doc().deliverables[0].platform == "reels" and res.rounds == 1


def test_edit_refuses_to_ingest_on_a_full_disk(tmp_path: Path, small_video: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.agent.loop import DiskSpaceError

    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "1000000")
    video = tmp_path / "t.mov"
    shutil.copy(small_video, video)
    with pytest.raises(DiskSpaceError, match="GB free"):
        pipeline.edit(video)


# ============================================================================================ chat
def test_chat_rerenders_gates_on_invariants_and_delivers(ready_job: Job) -> None:
    sc = ScriptedDirector(_plan())
    pipeline.edit(ready_job.root, director_model_override=sc.model(), loop_kwargs=LoopFakes(ready_job).kwargs())
    before = LoopState.load(ready_job)
    fk = LoopFakes(ready_job, qa_seq=[False, True])
    plan = _plan()
    plan["chat"] = [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}}]})],
                    [("caption_preview", {})], [("finish_stage", {"summary": "captions at 96 px"})], "ok",
                    # the fix attempt after the failed invariants (a new prompt restarts the steps)
                    ]
    fix_plan = [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 90}}]})],
                [("caption_preview", {})],
                [("finish_stage", {"summary": "90 px keeps the page inside the safe zone"})], "ok"]

    class ChatScript(ScriptedDirector):
        def __call__(self, messages: Any, info: Any) -> Any:
            from director_script import last_prompt

            self.plan["chat"] = fix_plan if "FAILS hard invariants" in last_prompt(messages) else plan["chat"]
            return super().__call__(messages, info)

    cs = ChatScript(dict(plan))
    res = pipeline.chat(ready_job.root, "make the captions bigger", director_model_override=cs.model(),
                        renderer=fk.renderer,
                        qa=lambda rd: (fk.qa(rd), [{"number": 7, "name": "safe zone", "detail": "p003 outside",
                                                    "refs": ["p003"]}]),
                        critic=lambda rd, qs: [{"by": "frame_judge", "severity": "P2", "area": "captions",
                                                "refs": [], "text": "bigger, fine"}])
    assert res.invariants_passed and res.finals["tiktok"].exists()
    assert res.summary.startswith("captions at 96 px") and "90 px keeps the page inside the safe zone" in res.summary
    assert any("caption style" in c for c in res.changes) and "no defect" in res.critic_note
    after = LoopState.load(ready_job)
    assert after.champion_render != before.champion_render and after.champion_doc == res.doc_version
    doc = ready_job.load_doc(res.doc_version)
    assert doc.captions is not None and doc.captions.style.size_px == 90
    assert len(fk.renders) == 2  # the failing render and its fix


def test_chat_with_nothing_to_change_renders_nothing(ready_job: Job) -> None:
    sc = ScriptedDirector(_plan())
    pipeline.edit(ready_job.root, director_model_override=sc.model(), loop_kwargs=LoopFakes(ready_job).kwargs())
    plan = {"chat": [[("finish_stage", {"summary": "already as asked: nothing to change"})], "ok"]}
    fk = LoopFakes(ready_job)
    res = pipeline.chat(ready_job.root, "keep it as it is", director_model_override=ScriptedDirector(plan).model(),
                        renderer=fk.renderer)
    assert fk.renders == [] and res.finals == {} and "nothing to change" in res.summary


def test_chat_needs_a_document(job: Job) -> None:
    with pytest.raises(pipeline.PipelineError, match="no document"):
        pipeline.chat(job.root, "shorter")
    with pytest.raises(pipeline.PipelineError, match="empty"):
        pipeline.chat(job.root, "  ")


# ============================================================================================ helpers
def test_describe_change(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    after = cut_doc.model_copy(deep=True)
    after.segments = after.segments[:3]
    after.inserts = []
    after.audio.music = None
    lines = pipeline.describe_change(cut_doc, after, take_index)
    assert any(line.startswith("removed 13 words") for line in lines) and "removed inserts i001" in lines
    assert pipeline.describe_change(cut_doc, cut_doc, take_index) == ["no render-relevant change"]


def test_edit_slots_limit_concurrency() -> None:
    order: list[str] = []
    started = threading.Event()

    def hold() -> None:
        with pipeline.edit_slot(max_concurrent=1):
            order.append("a-in")
            started.set()
            time.sleep(0.3)
            order.append("a-out")

    t = threading.Thread(target=hold)
    t.start()
    started.wait(5)
    waited: list[bool] = []
    with pipeline.edit_slot(max_concurrent=1, poll_s=0.05, on_wait=lambda: waited.append(True)):
        order.append("b-in")
    t.join()
    assert order == ["a-in", "a-out", "b-in"] and waited == [True]


def test_director_spec_for(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.config import Settings

    s = Settings.load(env={"ANTHROPIC_API_KEY": "sk-ant-test-0000000000"})
    assert pipeline.director_spec_for(None, None, None, s) is None
    spec = pipeline.director_spec_for("anthropic", None, None, s)
    assert spec is not None and spec.model == "claude-fable-5-1" and not spec.byok and spec.effort == "max"
    with pytest.raises(pipeline.PipelineError, match="needs --director-model"):
        pipeline.director_spec_for("openai", None, "MY_KEY", s)
    monkeypatch.setenv("MY_KEY", "sk-proj-abcdefghijklmnopqrstuvwxyz")
    byok = pipeline.director_spec_for("openai", "gpt-6-astra", "MY_KEY", s)
    assert byok is not None and byok.byok and byok.provider == "openai"


def test_cli_edit_resume_and_errors(ready_job: Job, capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    got: dict[str, Any] = {}

    def fake_edit(video: Any, **kw: Any) -> pipeline.EditResult:
        got.update(kw, video=video)
        return pipeline.EditResult(job_dir=ready_job.root, finals={"tiktok": Path("x.mp4")}, doc_version=3)

    monkeypatch.setattr(pipeline, "edit", fake_edit)
    media = tmp_path / "broll.mp4"
    media.write_bytes(b"x")
    assert cli.main(["edit", str(ready_job.root), "--media", str(media)]) == 0
    assert got["platform"] is None and got["creator_media"] == [media] and callable(got["log"])
    empty = tmp_path / "not_a_job"
    empty.mkdir()
    assert cli.main(["edit", str(empty)]) == 2
    assert "not a video file or a job directory" in capsys.readouterr().err
    assert cli.main(["edit", str(ready_job.root), "--media", str(tmp_path / "missing.mov")]) == 2

    def full_disk(video: Any, **kw: Any) -> Any:
        from studio.agent.loop import DiskSpaceError

        raise DiskSpaceError("only 0.20 GB free on the work volume")

    monkeypatch.setattr(pipeline, "edit", full_disk)
    assert cli.main(["edit", str(ready_job.root)]) == 1
    assert "0.20 GB free" in capsys.readouterr().err


# ============================================================================================ real renders (slow)
@pytest.mark.slow
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_real_edit_and_chat_on_a_synthetic_take(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest → index (ASR stubbed) → scripted Director → full-quality renders → QA → critics (scripted judges on
    real contact sheets) → pairwise → deliver → report; then a chat edit re-rendered and delivered."""
    from test_integration_smoke import _synth_take, _write_source

    from studio.compile import overlays as overlays_mod
    from studio.perception import transcribe as transcribe_mod
    from studio.perception.index import AsrInfo, AsrResult

    audio, words = _synth_take()
    src = _write_source(tmp_path / "take.mov", audio)
    monkeypatch.setattr(transcribe_mod, "transcribe", lambda job_, **kw: AsrResult(
        words=[w.model_copy() for w in words], asr=AsrInfo(provider="fixture", model="truth", language="en"),
        language="en"))
    from test_integration_smoke import _overlay_ready

    if not _overlay_ready():
        monkeypatch.setattr(overlays_mod, "render_overlays", lambda *a, **k: None)
    story = [{"from_word": "w0004", "to_word": "w0011"}, {"from_word": "w0013", "to_word": "w0014"}]
    plan: dict[str, list[Any]] = {
        "brief": [[("meta_ops", {"ops": [{"op": "set_brief", "brief": {"goal": "Keep the pauses that matter",
                                                                       "rubric": ["hook by 3 s?"]}}]})],
                  [("finish_stage", {"summary": "brief: one editing tip"})], "ok"],
        "story": [[("apply_ops", {"ops": [{"op": "set_story", "segments": story},
                                          {"op": "pin", "word_ids": ["w0008"], "kind": "payoff"}]})],
                  [("radio_test", {})], [("finish_stage", {"summary": "retake chosen, filler out; passes"})], "ok"],
        "fine_cut": [[("compile_check", {}), ("check_seams", {})],
                     [("finish_stage", {"summary": "seams between thoughts"})], "ok"],
        "reframe": [[("finish_stage", {"summary": "none"})], "ok"],
        "broll": [[("finish_stage", {"summary": "none"})], "ok"],
        "captions": [[("auto_captions", {})], [("caption_preview", {})],
                     [("finish_stage", {"summary": "auto captions"})], "ok"],
        "sound": [[("finish_stage", {"summary": "default chain, no music"})], "ok"],
        "color": [[("finish_stage", {"summary": "none"})], "ok"],
        "finalize": [[("finish_stage", {"summary": "ready"})], "ok"],
        "revise": [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 80}}]})],
                   [("caption_preview", {})], [("finish_stage", {"summary": "captions slightly larger"})], "ok"],
        "chat": [[("captions_ops", {"ops": [{"op": "set_caption_style", "style": {"size_px": 88, "case": "upper"}}]})],
                 [("caption_preview", {})], [("finish_stage", {"summary": "captions bigger, upper case"})], "ok"],
    }

    def frame(messages: Any, info: Any) -> dict[str, Any]:
        schema = json.dumps(info.output_tools[0].parameters_json_schema)
        if "reactions" in schema:
            return {"reactions": ["clean"], "remembered_sentence": "keep the pauses that matter"}
        if "pulls_attention" in schema:
            return {"pulls_attention": "no", "verdict": "ship"}
        if '"overall"' in schema:
            return {"areas": [{"area": "captions", "verdict": "same"}], "overall": "same", "reason": "equal"}
        return {"notes": [{"severity": "P2", "area": "captions", "refs": ["w0009"], "text": "captions a bit small",
                           "taste": True}], "verdict": "revise"}

    panel = JudgePanel(frame_judge=Judge("frame_judge", model=structured(frame)),
                       second_judge=Judge("second_judge", model=structured(frame, name="second")))
    res = pipeline.edit(src, platform="tiktok", director_model_override=ScriptedDirector(plan).model(), panel=panel,
                        rounds=1, out=tmp_path / "out")
    job = Job.open(res.job_dir)
    assert res.invariants_passed, json.loads((job.renders_dir / res.render / "qa" / "invariants.json").read_text())
    for key in ("nomusic", "cover", "srt", "doc", "report"):
        assert res.extras[key].exists(), key
    assert res.finals["tiktok"].stat().st_size > 10_000 and (tmp_path / "out" / "final_tiktok.mp4").exists()
    assert res.rounds == 1 and res.render == "r1"  # the challenger tied: the champion ships
    assert not (job.renders_dir / "r1" / "aroll.mov").exists() and not (job.renders_dir / "r2" / "aroll.mov").exists()
    report = (job.root / "report.md").read_text()
    assert "Critique history" in report and "captions a bit small" in report and "Pairwise" in report
    sheets = job.critique_dir / "r1" / "frames"
    assert (sheets / "hook.png").exists() and (sheets / "overview.png").exists()
    # chat: user intent wins (no pairwise), invariants gate, the new render is delivered
    chat = pipeline.chat(job.root, "make the captions bigger and upper case",
                         director_model_override=ScriptedDirector(plan).model(), panel=panel)
    assert chat.invariants_passed and chat.finals["tiktok"].exists()
    assert any("caption style" in c for c in chat.changes)
    assert job.load_doc(chat.doc_version).captions.style.case == "upper"


# ============================================================================================ BYOK + alternates
def test_byok_flags_persist_and_a_missing_key_refuses(job: Job, monkeypatch: pytest.MonkeyPatch) -> None:
    job.update_meta(director={"provider": "openai", "model": "gpt-6-astra", "key_env": "CREATOR_OAI_KEY"})
    # a resume or chat without flags reuses the job's own Director (the key's env var NAME is stored, never the key)
    assert pipeline._director_flags(job, None, None, None, False) == ("openai", "gpt-6-astra", "CREATOR_OAI_KEY")
    # --house switches explicitly; flags given now win
    assert pipeline._director_flags(job, None, None, None, True) == (None, None, None)
    assert pipeline._director_flags(job, "anthropic", None, None, False)[0] == "anthropic"
    from studio.config import Settings

    s = Settings.load(env={"ANTHROPIC_API_KEY": "sk-ant-test-0000000000"})
    monkeypatch.delenv("CREATOR_OAI_KEY", raising=False)
    with pytest.raises(pipeline.PipelineError, match="--house"):
        pipeline._spec_or_refuse("openai", "gpt-6-astra", "CREATOR_OAI_KEY", s)
    assert "CREATOR_OAI_KEY" in json.dumps(job.meta) and "sk-" not in json.dumps(job.meta)


def test_byok_specs_never_use_server_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.agent.providers import spec_from_cli
    from studio.config import Settings

    s = Settings.load(env={"ANTHROPIC_API_KEY": "sk-ant-test-0000000000"})
    monkeypatch.setenv("CREATOR_ANT_KEY", "sk-ant-creator-0000000000")
    byok = spec_from_cli("anthropic", "claude-fable-5-1", "CREATOR_ANT_KEY", settings=s)
    house = spec_from_cli("anthropic", "claude-fable-5-1", None, settings=s)
    assert byok.byok and not byok.uses_server_fallbacks() and house.uses_server_fallbacks()
    assert byok.model_copy(update={"server_fallbacks": True}).uses_server_fallbacks()  # an explicit opt-in


def test_deliver_puts_alternates_beside_the_finals(job: Job, tmp_path: Path) -> None:
    from studio.doc.model import CutDocument

    champ, alt = job.new_render_dir(), job.new_render_dir()
    for rd in (champ, alt):
        for n in ("final_tiktok.mp4", "final_nomusic.mp4", "cover.jpg"):
            (rd / n).write_bytes(rd.name.encode())
    out = tmp_path / "out"
    finals, extras = pipeline.deliver(job, champ, CutDocument(job_id=job.id), out=out, alternates=[("h01", alt)])
    assert finals["tiktok"].read_bytes() == champ.name.encode()
    a = extras["alt:h01"]
    assert a == job.root / "deliver" / "alternates" / "h01" / "final_tiktok.mp4" and a.read_bytes() == alt.name.encode()
    assert (out / "alternates" / "h01" / "final_tiktok.mp4").exists() and (out / "final_tiktok.mp4").exists()
    # the delivery folder names its job: chat / resume / report work on it as on the job directory
    assert Job.open(out) == job
