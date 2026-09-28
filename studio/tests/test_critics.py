"""Critics (studio.agent.critics): packet, frame judge, watcher, confirmation rule and position-swapped pairwise.

Renders are synthetic (``qa_fixtures.make_render``: a moving test pattern at the delivery size with a speech-like
dialogue track cut exactly as the compiled timeline says); judges are scripted ``FunctionModel``s.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from conftest import build_cut_doc, build_take_index
from director_script import prompt_text, structured
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo
from qa_fixtures import SR, make_render, synth_source_audio

from studio.agent import critics
from studio.agent.critics import Judge, JudgePanel
from studio.compile.timeline import compile as compile_timeline
from studio.config import Settings
from studio.jobs import Job
from studio.qa import invariants as inv
from studio.qa import metrics as qm

FAST = {"asr": False, "banding": False, "estoi": False, "video_events": False}


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    root = tmp_path_factory.mktemp("critics")
    ix = build_take_index()
    doc = build_cut_doc(ix)
    job = Job.create("critics-job", work_dir=root)
    job.save_media_info(ix.media)
    job.save_index(ix)
    job.save_doc(doc)
    tl = compile_timeline(doc, ix, job=job)
    src = synth_source_audio(ix)
    sf.write(str(job.audio_path), src.astype(np.float32), SR, subtype="FLOAT")
    renders = {}
    for name, kw in (("r1", {}), ("r2", {"click_at_seam": 0})):
        rd = job.renders_dir / name
        paths = make_render(rd, tl, ix, source=src, **kw)
        pk = qm.measure(job, tl, paths["final"], doc=doc, index=ix, render_dir=rd, **FAST)
        inv.check_invariants(job, doc, ix, tl, rd, metrics=pk, asr=False)
        renders[name] = rd
    yield SimpleNamespace(job=job, index=ix, doc=doc, timeline=tl, renders=renders)
    shutil.rmtree(root, ignore_errors=True)


def _judge(fn: Any, role: str = "frame_judge", same_family: bool = True) -> Judge:
    return Judge(role, model=structured(fn, name=role), same_family=same_family)


# ============================================================================================ packet
def test_packet_reuses_qa_and_cuts_sheets_from_the_final(env: SimpleNamespace) -> None:
    pk = critics.build_packet(env.job, env.renders["r1"], index=env.index)
    assert pk.passed and pk.doc_version == env.doc.version and pk.final.name == "final_tiktok.mp4"
    assert pk.metric_notes == [] and len(pk.invariants) == 10
    assert set(pk.sheets) >= {"overview", "hook", "seams", "captions", "inserts"}
    for paths in pk.sheets.values():
        assert paths and all(p.exists() for p in paths)
    assert "‖ seg002" in pk.transcript and "w0018 restraint." in pk.transcript and "w0009" not in pk.transcript
    assert "did not run" in pk.asr_note or "skipped" in pk.asr_note
    frames = env.job.critique_dir / "r1" / "frames"
    assert (frames / "packet.json").exists() and json.loads((frames / "packet.json").read_text())["passed"]
    from studio.perception.frames import read_sheet_meta

    assert read_sheet_meta(pk.sheets["captions"][0]).get("ui_mask") == "tiktok"
    assert set(pk.page_words) == {p.id for p in env.doc.captions.pages}


def test_packet_turns_invariant_failures_into_confirmed_p0_notes(env: SimpleNamespace) -> None:
    pk = critics.build_packet(env.job, env.renders["r2"], index=env.index, sheets=False)
    assert not pk.passed
    first = pk.metric_notes[0]
    assert first["severity"] == "P0" and first["confirmed_by"] == ["metrics"]
    assert any(e["kind"] == "click" for e in pk.evidence)


def test_render_transcript_marks_cuts_and_pauses(env: SimpleNamespace) -> None:
    txt = critics.render_transcript(env.timeline, env.index)
    lines = txt.splitlines()
    assert lines[0].startswith("seg001 @ 0.00s") and lines[1].startswith("‖ seg002")
    assert "[0." in txt  # kept pauses of 0.3 s or more


# ============================================================================================ critique
def test_critique_confirmation_rule(env: SimpleNamespace) -> None:
    rd = env.renders["r2"]
    click_ev = next(e for e in critics.build_packet(env.job, rd, index=env.index, sheets=False).evidence
                    if e["kind"] == "click")
    seam_words = click_ev["words"]

    def frame(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        if info.output_tools[0].parameters_json_schema.get("properties", {}).get("reactions") is not None:
            return {"reactions": ["the pop at the cut"], "swipe_moments": [], "remembered_sentence": "keep the pauses"}
        return {"rubric": [{"question": "C3", "answer": "yes", "refs": seam_words}],
                "notes": [
                    {"severity": "P0", "area": "audio", "refs": seam_words, "text": "a click at the first cut"},
                    {"severity": "P1", "area": "captions", "refs": ["p002", "zz99"], "text": "page p002 flashes by"},
                    {"severity": "P1", "area": "pacing", "refs": ["w0030"], "text": "a dead stretch at w0030"},
                    {"severity": "P2", "area": "music", "refs": [], "text": "maybe a bed", "taste": True}],
                "keep": ["the smile after w0018"], "verdict": "revise"}

    asked: dict[str, Any] = {}

    def confirm(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        asked["text"] = prompt_text(messages)
        return {"items": [{"note": 0, "confirmed": "yes", "reason": "p002 is 0.3 s"},
                          {"note": 1, "confirmed": "no"}]}

    panel = JudgePanel(frame_judge=_judge(frame), second_judge=_judge(confirm, "second_judge"))
    notes = critics.critique(env.job, None, env.index, rd, panel=panel, label="t_confirm")
    by_text = {n["text"]: n for n in notes}
    # the invariant failure leads, confirmed by metrics
    assert notes[0]["by"] == "metrics" and notes[0]["severity"] == "P0"
    click = by_text["a click at the first cut"]
    assert click["severity"] == "P0" and any(c.startswith("metrics:click") for c in click["confirmed_by"])
    cap = by_text["page p002 flashes by"]
    assert cap["severity"] == "P1" and cap["confirmed_by"] == ["critic:test:second_judge"]
    assert "zz99" in cap["evidence"] and cap["refs"] == ["p002"]
    dead = by_text["a dead stretch at w0030"]
    assert dead["severity"] == "P2" and dead["unconfirmed_severity"] == "P1" and not dead["confirmed_by"]
    assert by_text["maybe a bed"]["taste"] and by_text["maybe a bed"].get("unlocalized")
    # the confirmer only saw the unconfirmed P0/P1 notes
    assert "page p002 flashes by" in asked["text"] and "a click at the first cut" not in asked["text"]
    rec = json.loads((env.job.critique_dir / "t_confirm" / "notes.json").read_text())
    assert rec["naive"]["remembered_sentence"] == "keep the pauses" and rec["keep"] == ["the smile after w0018"]
    assert rec["watcher"]["ran"] is False and "no Google key" in rec["watcher"]["reason"]
    text = critics.format_notes(notes)
    assert "[P0][audio]" in text and "(was P1, unconfirmed)" in text


def test_director_questions_reach_the_judge(env: SimpleNamespace) -> None:
    seen: dict[str, str] = {}

    def frame(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        seen["prompt"] = prompt_text(messages)
        if "reactions" in json.dumps(info.output_tools[0].parameters_json_schema):
            return {"reactions": [], "remembered_sentence": ""}
        return {"notes": [], "answers": ["no jump"], "verdict": "ship_it"}

    panel = JudgePanel(frame_judge=_judge(frame), second_judge=_judge(lambda m, i: {"items": []}, "second_judge"))
    notes = critics.critique(env.job, env.doc, env.index, env.renders["r1"], panel=panel, label="t_questions",
                             questions=["At w0008->w0014, does the head jump?"], naive=False)
    assert notes == []
    assert "At w0008->w0014, does the head jump?" in seen["prompt"] and "RUBRIC" in seen["prompt"]
    assert "Is any word or word edge clipped" in seen["prompt"]  # critique.md's critic questions
    rec = json.loads((env.job.critique_dir / "t_questions" / "notes.json").read_text())
    assert rec["answers"] == ["no jump"] and rec["verdict"] == "ship_it"


def test_watcher_notes_cross_confirm_the_frame_judge(env: SimpleNamespace) -> None:
    def frame(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        return {"notes": [{"severity": "P1", "area": "seams", "refs": ["w0020"], "text": "jump into keep"}]}

    def watch(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

        vids = [c for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)
                for c in (p.content if isinstance(p.content, list) else []) if isinstance(c, BinaryContent)]
        assert vids and vids[0].media_type == "video/mp4"
        return {"notes": [{"severity": "P1", "area": "seams", "refs": ["w0020", "w0021"], "text": "abrupt join"}],
                "attention_left_speaker": ["the card"]}

    panel = JudgePanel(frame_judge=_judge(frame), second_judge=_judge(lambda m, i: {"items": []}, "second_judge"),
                       watcher=_judge(watch, "watcher", same_family=False))
    notes = critics.critique(env.job, env.doc, env.index, env.renders["r1"], panel=panel, label="t_watch",
                             naive=False)
    jump = next(n for n in notes if n["text"] == "jump into keep")
    assert jump["severity"] == "P1" and "critic:watcher:test:watcher" in jump["confirmed_by"]
    assert (env.job.critique_dir / "r1" / "frames" / "review_ids.mp4").exists()
    rec = json.loads((env.job.critique_dir / "t_watch" / "notes.json").read_text())
    assert rec["watcher"]["ran"] and rec["watcher"]["attention_left_speaker"] == ["the card"]


def test_a_failing_judge_is_recorded_not_raised(env: SimpleNamespace) -> None:
    def boom(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        raise RuntimeError("provider down")

    panel = JudgePanel(frame_judge=_judge(boom), second_judge=_judge(boom, "second_judge"))
    notes = critics.critique(env.job, env.doc, env.index, env.renders["r2"], panel=panel, label="t_fail")
    assert notes and all(n["by"] == "metrics" for n in notes)  # invariant P0s survive a critic outage
    rec = json.loads((env.job.critique_dir / "t_fail" / "notes.json").read_text())
    assert any("frame judge failed" in e for e in rec["errors"])


# ============================================================================================ pairwise
def _prefers_clean(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
    """A consistent judge: prefers the version whose measurements show no click."""
    text = prompt_text(messages)
    a_block = text.split("=== VERSION B ===")[0]
    a_has_click = '"clicks": [{' in a_block
    winner = "B" if a_has_click else "A"
    return {"areas": [{"area": "seams", "verdict": winner, "reason": "no click"}], "overall": winner,
            "reason": "the clean version"}


def _always_a(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
    return {"areas": [], "overall": "A", "reason": "first is best"}


def test_pairwise_needs_both_orders_and_both_judges(env: SimpleNamespace) -> None:
    ra, rb = env.renders["r2"], env.renders["r1"]  # a = the clicking render, b = the clean one
    panel = JudgePanel(frame_judge=_judge(_prefers_clean), second_judge=_judge(_prefers_clean, "second_judge"))
    out = critics.pairwise(env.job, ra, rb, panel=panel, label="t_consistent")
    assert out["winner"] == "b" and set(out["per_judge"].values()) == {"b"}
    assert len(out["votes"]) == 4 and {v["order"] for v in out["votes"]} == {"a first", "b first"}
    assert out["regressions"]["b"] == [] and any("clicks" in r for r in out["regressions"]["a"])
    saved = json.loads((env.job.critique_dir / "pairwise_t_consistent.json").read_text())
    assert saved["winner"] == "b"
    # a position-biased judge flips with the order: its vote is a tie, so the champion stays
    panel2 = JudgePanel(frame_judge=_judge(_prefers_clean), second_judge=_judge(_always_a, "second_judge"))
    out2 = critics.pairwise(env.job, ra, rb, panel=panel2, label="t_biased")
    assert out2["winner"] == "tie" and out2["per_judge"]["test:second_judge"] == "tie"


def test_pairwise_metric_regression_blocks_a_win(env: SimpleNamespace) -> None:
    # both judges prefer the clicking render (b) in both orders, but b regresses a measured defect
    def prefers_click(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        text = prompt_text(messages)
        a_block = text.split("=== VERSION B ===")[0]
        winner = "A" if '"clicks": [{' in a_block else "B"
        return {"areas": [], "overall": winner, "reason": "busier"}

    panel = JudgePanel(frame_judge=_judge(prefers_click), second_judge=_judge(prefers_click, "second_judge"))
    out = critics.pairwise(env.job, env.renders["r1"], env.renders["r2"], panel=panel, label="t_regress")
    assert set(out["per_judge"].values()) == {"b"} and out["winner"] == "tie"
    assert any("invariants_failed" in r for r in out["regressions"]["b"])


def test_attention_check(env: SimpleNamespace) -> None:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> dict[str, Any]:
        return {"pulls_attention": "yes", "moments": [{"severity": "P2", "area": "broll", "refs": ["i001"],
                                                       "text": "the card holds a beat too long"}],
                "verdict": "ship"}

    panel = JudgePanel(frame_judge=_judge(fn), second_judge=_judge(fn, "second_judge"))
    out = critics.attention_check(env.job, env.renders["r1"], panel=panel)
    assert out["pulls_attention"] == "yes" and out["notes"][0]["refs"] == ["i001"]
    assert (env.job.critique_dir / "final_watch.json").exists()


# ============================================================================================ panel
def test_default_panel_families(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings.load(env={"ANTHROPIC_API_KEY": "sk-ant-test-000000000000"})
    p = critics.default_panel(s)
    assert p.frame_judge.spec is not None and p.frame_judge.spec.model == "claude-opus-5-5"
    assert p.frame_judge.same_family and p.second_judge.spec.model == critics.SECOND_JUDGE_MODEL
    assert p.watcher is None and any("watcher did not run" in n for n in p.notes)
    s2 = Settings.load(env={"ANTHROPIC_API_KEY": "sk-ant-test-000000000000", "GOOGLE_API_KEY": "AIza" + "x" * 35})
    p2 = critics.default_panel(s2)
    assert p2.frame_judge.provider == "google" and not p2.frame_judge.same_family
    assert p2.watcher is not None and p2.watcher.spec.capabilities.video_input
    monkeypatch.setenv("STUDIO_SECOND_JUDGE_MODEL", "claude-opus-5")
    assert critics.default_panel(s).second_judge.spec.model == "claude-opus-5"


def test_critic_questions_come_from_the_doctrine() -> None:
    qs = critics.critic_questions()
    assert len(qs) >= 10 and qs[0].startswith("On the first uninterrupted")


def test_overview_samples_avoid_caption_page_changes(env: SimpleNamespace) -> None:
    """A still taken in the 2-frame gap between caption pages (or on a page's first, animated frame) reads as
    "no captions"; the overview moves such samples to the middle of the page."""
    from fractions import Fraction

    tl = env.timeline
    fps = Fraction(tl.fps)
    ts = critics.overview_times(tl)
    assert ts[0] <= Fraction(1, 2) and len(ts) >= int(float(tl.duration) / 2)
    guard = Fraction(3) / fps
    for t in ts:
        near_change = any(abs(t - Fraction(p.out_start)) < guard or abs(t - Fraction(p.out_end)) < guard
                          for p in tl.captions)
        assert not near_change, float(t)
