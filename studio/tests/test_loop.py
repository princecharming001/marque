"""Champion loop (studio.agent.loop): keep/stop rules, pruning, disk floor and resume.

Renders, QA, critics and judges are injected fakes; the Director is a scripted ``FunctionModel`` whose revisions
are real ops on the fixture document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from director_script import STORY, ScriptedDirector

from studio.agent import loop as lp
from studio.agent.director import Director
from studio.agent.loop import ChampionLoop, DiskSpaceError, LoopConfig, LoopState
from studio.doc.model import CutDocument
from studio.jobs import Job
from studio.perception.index import TakeIndex

GAP_300 = {"revise": [[("cut_ops", {"ops": [{"op": "set_gap", "gap_id": "g0009", "ms": 300}]})],
                      [("finish_stage", {"summary": "trimmed g0009 (dead stretch note)"})], "ok"]}
NOOP = {"revise": [[("finish_stage", {"summary": "declined every note: taste only"})], "ok"]}


class Fakes:
    """Recording fake renderer / QA / critic / judge."""

    def __init__(self, job: Job, *, qa: dict[int, bool] | None = None, winners: list[str] | None = None,
                 notes: list[dict[str, Any]] | None = None):
        self.job = job
        self.rendered: list[int] = []
        self.qa_map = qa or {}
        self.winners = list(winners or [])
        self.notes = notes if notes is not None else [{"by": "frame_judge", "severity": "P1", "area": "pacing",
                                                       "refs": ["w0028"], "text": "dead stretch after 'rest.'",
                                                       "confirmed_by": ["metrics:long_pause"]}]
        self.critiqued: list[str] = []
        self.judged: list[tuple[str, str]] = []
        self.watched: list[str] = []

    def renderer(self, doc: CutDocument) -> Path:
        rd = self.job.new_render_dir()
        (rd / "render.json").write_text(json.dumps({"doc_version": doc.version}))
        (rd / "timeline.json").write_text("{}")
        (rd / "final_tiktok.mp4").write_bytes(b"final")
        (rd / "aroll.mov").write_bytes(b"x" * 4096)
        (rd / "mix.wav").write_bytes(b"x" * 1024)
        (rd / "stems").mkdir()
        (rd / "stems" / "dialogue.wav").write_bytes(b"x" * 512)
        self.rendered.append(doc.version)
        return rd

    def qa(self, rd: Path) -> bool:
        return self.qa_map.get(int(rd.name[1:]), True)

    def critic(self, rd: Path, doc: CutDocument) -> list[dict[str, Any]]:
        self.critiqued.append(rd.name)
        return list(self.notes)

    def judge(self, a: Path, b: Path) -> dict[str, Any]:
        self.judged.append((a.name, b.name))
        w = self.winners.pop(0) if self.winners else "tie"
        return {"winner": w, "votes": [{"judge": "j1", "winner": w, "reason": "because"}], "regressions": {"b": []}}

    def final_watch(self, rd: Path) -> dict[str, Any]:
        self.watched.append(rd.name)
        return {"pulls_attention": "no", "verdict": "ship"}

    def kwargs(self) -> dict[str, Any]:
        return {"renderer": self.renderer, "qa": self.qa, "critic": self.critic, "judge": self.judge,
                "final_watch": self.final_watch}


def _director(job: Job, index: TakeIndex, plan: dict[str, list[Any]]) -> tuple[Director, ScriptedDirector]:
    sc = ScriptedDirector(plan)
    d = Director(job, index, model=sc.model(), sleep=lambda _s: None)
    sc.director = d
    if not d.session.doc.segments:
        d.session.apply_ops([{"op": "set_story", "segments": STORY}])
    return d, sc


@pytest.fixture(autouse=True)
def _no_disk_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "0")


def test_ties_keep_the_champion_and_two_winless_rounds_stop(job: Job, take_index: TakeIndex) -> None:
    d, sc = _director(job, take_index, GAP_300)
    fk = Fakes(job, winners=["tie", "a"])
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert [r.outcome for r in res.rounds] == ["tie", "loss"]
    assert res.champion_render.name == "r1" and res.stop_reason == "2 winless rounds"
    assert fk.judged == [("r1", "r2"), ("r1", "r3")]
    assert fk.critiqued == ["r1", "r1"] and fk.watched == ["r1"]
    # the losers' intermediates are pruned as soon as their round is decided; finals kept
    for name in ("r2", "r3"):
        rd = job.renders_dir / name
        assert not (rd / "aroll.mov").exists() and not (rd / "stems").exists() and (rd / "final_tiktok.mp4").exists()
        assert json.loads((rd / "pruned.json").read_text())["bytes"] > 0
    assert (job.renders_dir / "r1" / "aroll.mov").exists()  # the champion keeps its intermediates during the loop
    # every revision branched from the champion
    for r in res.rounds:
        assert job.load_doc(r.challenger_doc).parent_version == res.champion_doc
    # the revision prompt carried the notes and, in round 2, how round 1 went
    revise_prompts = [p for s, p in sc.prompts if s == "revise"]
    assert "dead stretch after 'rest.'" in revise_prompts[0] and "round 1: v" in revise_prompts[1]
    st = LoopState.load(job)
    assert st.done and st.round == 2 and st.winless == 2


def test_a_win_promotes_the_challenger_and_prunes_the_old_champion(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job, winners=["b"])
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    # round 1 wins; round 2's revision repeats the same op → nothing changes → no render; round 3 the same
    assert [r.outcome for r in res.rounds] == ["win", "no_change", "no_change"]
    assert res.champion_render.name == "r2" and fk.rendered == [res.rounds[0].champion_doc, res.champion_doc]
    assert not (job.renders_dir / "r1" / "aroll.mov").exists()
    assert fk.critiqued == ["r1", "r2", "r2"] and d.session.doc.version == res.champion_doc


def test_challenger_failing_an_invariant_loses_without_a_jury(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job, qa={2: False, 3: False})
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert [r.outcome for r in res.rounds] == ["invariant_fail", "invariant_fail"] and fk.judged == []
    assert res.champion_render.name == "r1"


def test_a_failing_champion_is_replaced_by_a_passing_tie(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job, qa={1: False, 2: True}, winners=["tie"])
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert res.rounds[0].outcome == "replaced_failing" and res.champion_render.name == "r2" and res.champion_passed


def test_no_change_revisions_cost_no_render(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, NOOP)
    fk = Fakes(job)
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert [r.outcome for r in res.rounds] == ["no_change", "no_change"] and fk.rendered == [res.champion_doc]


def test_critics_with_nothing_to_say_end_the_loop(job: Job, take_index: TakeIndex) -> None:
    d, sc = _director(job, take_index, GAP_300)
    fk = Fakes(job, notes=[])
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert res.stop_reason == "critics found nothing to change" and len(res.rounds) == 1
    assert not [p for s, p in sc.prompts if s == "revise"]  # the Director was not asked to revise


def test_round_guard_and_cli_override(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job, winners=["tie"])
    res = ChampionLoop(job, d, take_index, rounds=1, **fk.kwargs()).run()
    assert len(res.rounds) == 1 and res.stop_reason == "round guard (1) reached"
    assert LoopConfig().guard == 12 and LoopConfig().winless_stop == 2


def test_low_disk_stops_the_loop_and_the_champion_ships(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job)
    calls = {"n": 0}

    def renderer(doc: CutDocument) -> Path:
        calls["n"] += 1
        if calls["n"] > 1:
            raise DiskSpaceError("only 0.10 GB free")
        return fk.renderer(doc)

    res = ChampionLoop(job, d, take_index, **{**fk.kwargs(), "renderer": renderer}).run()
    assert res.rounds[-1].outcome == "disk" and res.stop_reason.startswith("disk:")
    assert res.champion_render.name == "r1"


def test_the_loop_resumes_after_a_crash(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job, winners=["tie"])

    def crashing_judge(a: Path, b: Path) -> dict[str, Any]:
        if len(fk.judged) >= 1:
            raise RuntimeError("power cut")
        return fk.judge(a, b)

    with pytest.raises(RuntimeError, match="power cut"):
        ChampionLoop(job, d, take_index, **{**fk.kwargs(), "judge": crashing_judge}).run()
    st = LoopState.load(job)
    assert st.round == 1 and not st.done and st.champion_render == "r1"
    # a render the crash left half-written is removed on resume
    half = job.new_render_dir()
    (half / "aroll.mov").write_bytes(b"x")
    d2, _ = _director(job, take_index, GAP_300)
    fk2 = Fakes(job, winners=["tie"])
    res = ChampionLoop(job, d2, take_index, **fk2.kwargs()).run()
    cleaned = [e for e in job.read_trace() if e["event"] == "prune" and e.get("renders")]
    assert cleaned and cleaned[-1]["renders"] == [half.name]
    # round 2 (interrupted before its verdict) is redone; its orphaned render lost its intermediates
    assert [r.round for r in res.rounds] == [1, 2] and res.stop_reason == "2 winless rounds"
    r3 = job.renders_dir / "r3"
    assert not (r3 / "aroll.mov").exists() and (r3 / "final_tiktok.mp4").exists()
    assert fk2.rendered and fk2.rendered[0] != res.champion_doc  # the champion was not re-rendered


# ============================================================================================ helpers
def test_ensure_disk_and_render_full_floor(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_MIN_FREE_GB", "1000000")
    with pytest.raises(DiskSpaceError, match="GB free"):
        lp.ensure_disk(job.root)
    with pytest.raises(DiskSpaceError):
        lp.render_full(job, job.load_doc() if job.latest_doc_version() is not None else CutDocument(), take_index)
    assert lp.ensure_disk(job.root, min_free=0) > 0


def test_prune_helpers(job: Job) -> None:
    rd = job.new_render_dir()
    for name in ("aroll.mov", "overlays.mov", "mix.wav", "mix_nomusic.wav", "final_tiktok.mp4", "cover.jpg",
                 "timeline.json", "render.json"):
        (rd / name).write_bytes(b"x" * 100)
    (rd / "stems").mkdir()
    (rd / "stems" / "music.wav").write_bytes(b"x" * 100)
    freed = lp.prune_render(rd, job=job, reason="test")
    assert freed == 500 and sorted(p.name for p in rd.iterdir()) == [
        "cover.jpg", "final_tiktok.mp4", "pruned.json", "render.json", "timeline.json"]
    lp.prune_render(rd, keep_finals=False)
    assert not (rd / "final_tiktok.mp4").exists() and (rd / "timeline.json").exists()
    cache = job.renders_dir / "_overlay_cache"
    cache.mkdir()
    old, new, linked = cache / "a.mov", cache / "b.mov", cache / "c.mov"
    for p in (old, new, linked):
        p.write_bytes(b"x" * 10)
    os.link(linked, rd / "overlays.mov")
    os.utime(old, (1, 1))
    assert lp.prune_overlay_cache(job, keep=1) > 0 and linked.exists() and not old.exists()
    bad = job.new_render_dir()
    assert lp.clean_incomplete_renders(job) == [bad.name] and rd.exists()


def test_a_director_outage_ships_the_champion(job: Job, take_index: TakeIndex) -> None:
    d, _ = _director(job, take_index, GAP_300)
    fk = Fakes(job)

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("provider down")

    d.revise = broken  # type: ignore[method-assign]
    res = ChampionLoop(job, d, take_index, **fk.kwargs()).run()
    assert res.rounds[-1].outcome == "error" and "could not revise" in res.stop_reason
    assert res.champion_render.name == "r1" and fk.watched == ["r1"]
