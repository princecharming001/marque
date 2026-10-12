"""Tests for studio.plan.chat (the standard tier's chat edit: one planner call → ``{ops, summary}`` → one version).

Keyless and offline: the chat model is the scripted ``FunctionModel`` of ``test_plan_planner`` installed under
:func:`providers.build_model` (so the real ``TracedModel`` role ``planner`` / stage ``chat`` wrapping runs). The base
document is the fixture's ``cut_doc`` (4 segments, 13 caption pages at 88 px, the hook title ``t001``, CTA pins on
w0032-w0033). Renders are fakes: :func:`render_change` is tested for which pass it picks and what it does on a failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_plan_planner import FAKE_ANT, Script, install, server_error

from studio.agent import providers as pv
from studio.agent.providers import house_spec
from studio.config import Settings
from studio.doc.model import CutDocument
from studio.jobs import Job
from studio.perception.index import TakeIndex
from studio.plan import chat
from studio.plan import repair as repair_mod
from studio.plan.chat import (
    CHAT_BY,
    CHAT_LOG,
    CHAT_OPS,
    ChatOps,
    build_prefix,
    chat_context,
    cut_unchanged,
    edit_document,
    has_other_request,
    refusal_line,
    refused_topics,
    render_change,
)
from studio.plan.repair import RepairResult
from studio.qa.invariants import INVARIANTS, InvariantResult, QaRun

_REAL_NATIVE = pv._native_model
BIGGER = {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}}], "summary": "Captions are bigger now."}


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    return settings.with_keys(anthropic=FAKE_ANT)


@pytest.fixture
def base(job: Job, cut_doc: CutDocument) -> CutDocument:
    job.save_doc(cut_doc)
    return job.load_doc(cut_doc.version)


def _spec(s: Settings) -> Any:
    return house_spec("planner", settings=s)


def _calls(job: Job, stage: str = "chat") -> list[dict[str, Any]]:
    return [e for e in job.read_trace() if e["event"] == "model_call" and e.get("stage") == stage]


class NoCall(Script):
    def answer(self, messages: Any, info: Any) -> Any:  # pragma: no cover - a refusal must not call
        raise AssertionError("the model must not be called")


# ============================================================================================ prompt pieces
def test_refusals_are_recognised_and_priced(take_index: TakeIndex) -> None:
    assert refused_topics("add some upbeat background music") == ["music"]
    assert refused_topics("put b-roll of a laptop over the middle, and a whoosh") == ["sound effects", "b-roll"]
    assert refused_topics("Isolate my voice please") == ["paid voice isolation"]
    assert refused_topics("make the captions bigger") == []
    assert refused_topics("remove the background noise") == []  # the local light denoise is allowed
    # a clause that asks for something else means the planner is called for that part
    assert not has_other_request("add some upbeat background music")
    assert not has_other_request("add music, thanks")
    assert has_other_request("make the captions bigger and add some music")
    line = refusal_line(["paid voice isolation"], take_index)
    assert "not part of the standard tier" in line and "--tier director --max-usd 3`" in line
    assert "$0.12 per source minute" in line and "$0.03" in line and "17 s take" in line  # 17 s source
    assert "music and b-roll are" in refusal_line(["music", "b-roll"], take_index).lower()
    assert refusal_line([], take_index) == ""


@pytest.mark.parametrize("instruction", [
    "cut the sentence about my song",
    "remove the footage of me coughing",
    "isolate the word budget on its own caption page",
    "cut where I say music is overrated",
    "trim the part about stock photos",
    "drop the part about the song",
    "add a caption saying I love this song",
    "use the song part as the hook",
])
def test_a_topic_named_as_content_is_an_ordinary_edit(instruction: str) -> None:
    """Creators point at content with the refused topics' words: those requests go to the planner, never to the
    priced refusal (a removal verb, or no additive verb asking for the thing)."""
    assert refused_topics(instruction) == [] and has_other_request(instruction)


def test_additive_requests_are_still_refused() -> None:
    assert refused_topics("add music and sound effects") == ["music", "sound effects"]
    assert refused_topics("add a whoosh on every cut") == ["sound effects"]  # "every cut" is a noun
    assert refused_topics("use some lo-fi music") == ["music"]
    assert refused_topics("use voice isolation") == ["paid voice isolation"]
    assert refused_topics("add stock footage of a beach") == ["b-roll"]
    assert refused_topics("play a song in the background") == ["music"]
    assert refused_topics("cut the intro and add a whoosh on each cut") == ["sound effects"]
    assert has_other_request("cut the intro and add a whoosh on each cut")
    assert not has_other_request("put some chill music under the intro")


def test_a_cut_request_naming_footage_reaches_the_planner(job: Job, take_index: TakeIndex, base: CutDocument,
                                                          keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script({"ops": [{"op": "cut_words", "from_word": "w0039", "to_word": "w0041",
                              "reason": "creator: the cough"}], "summary": "The cough is gone."})
    install(monkeypatch, script)
    ed = edit_document(job, take_index, base, "remove the footage of me coughing", spec=_spec(keyed), settings=keyed)
    assert ed.called and ed.changed and ed.refused == [] and len(script.calls) == 1
    assert "not part of the standard tier" not in ed.summary and ed.doc.version == base.version + 1


def test_prefix_lists_exactly_the_allowed_ops() -> None:
    from studio.plan.digest import estimate_tokens

    p = build_prefix()
    assert p == build_prefix()  # cache-stable bytes
    for name in CHAT_OPS:
        assert f'"const":"{name}"' in p, name
    for name in ("set_music", "add_sfx", "add_insert", "set_color", "set_story", "choose_take"):
        assert f'"const":"{name}"' not in p, name
    assert "Never write times" in p and "Paid voice isolation is not available" in p
    assert estimate_tokens(p) < 6000  # the plan measured ~5.3k tokens for 15 ops
    assert set(ChatOps.model_fields) == {"ops", "summary"}


def test_context_carries_the_cut_the_removed_words_pauses_and_captions(job: Job, take_index: TakeIndex,
                                                                     base: CutDocument) -> None:
    text = chat_context(job, take_index, base, "make the  captions\nbigger", refused=["music"])
    assert text.startswith('CREATOR REQUEST: "make the captions bigger"')
    assert "also mentions music" in text
    assert "seg001" in text and "w0032" in text  # the cut view (summary + story in output order)
    assert "WORDS NOT IN THE CUT" in text and "w0009" in text  # the false start is out: restorable by ID
    assert "PAUSES IN THE CUT" in text
    assert "CAPTIONS" in text and "p001" in text and "88px" in text and "on screen:" in text  # page list + layout
    assert "ON-SCREEN TEXT" in text and "t001 hook_title" in text


# ============================================================================================ the call
def test_one_call_applies_one_version(job: Job, take_index: TakeIndex, base: CutDocument, keyed: Settings,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(BIGGER)
    seen = install(monkeypatch, script)
    ed = edit_document(job, take_index, base, "make the captions bigger", spec=_spec(keyed), settings=keyed)
    assert ed.called and ed.changed and ed.attempts == 1 and ed.applied == 1 and ed.rejected == []
    assert ed.summary == "Captions are bigger now."
    assert ed.doc.version == base.version + 1 and ed.doc.captions.style.size_px == 96
    assert job.latest_doc_version() == ed.doc.version
    assert seen[0][1] == "planner" and len(script.calls) == 1
    call = script.calls[0]
    assert build_prefix() in (call["instructions"] or "") and "make the captions bigger" in call["prompt"]
    assert call["model_settings"]["max_tokens"] == chat.MAX_TOKENS
    recs = _calls(job)
    assert len(recs) == 1 and recs[0]["role"] == "planner" and recs[0]["cache_ttl"] == "5m"
    # the oplog names the chat as the author; the chat log records the exchange
    assert any(e.get("by") == CHAT_BY and e.get("applied") for e in job.read_oplog())
    log = job.read_jsonl(CHAT_LOG)
    assert log[-1]["applied"] == 1 and log[-1]["doc_version"] == ed.doc.version and log[-1]["usage"]["input_tokens"]


def test_rejected_ops_get_one_retry_with_the_reasons(job: Job, take_index: TakeIndex, base: CutDocument,
                                                     keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    first = {"ops": [{"op": "set_music", "spec": {"source": "x"}},
                     {"op": "cut_words", "from_word": "w9998", "to_word": "w9999", "reason": "shorter"},
                     {"op": "set_caption_style", "style": {"size_px": 96}}], "summary": "music and shorter"}
    second = {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}},
                      {"op": "cut_words", "from_word": "w0039", "to_word": "w0041", "reason": "creator: shorter end"}],
              "summary": "Bigger captions, and the last three words are gone."}
    script = Script(first, second)
    install(monkeypatch, script)
    ed = edit_document(job, take_index, base, "bigger captions and cut the end", spec=_spec(keyed), settings=keyed)
    assert ed.attempts == 2 and ed.rejected == [] and ed.applied == 2
    retry = script.calls[1]["prompt"]
    assert "set_music: not available in a standard-tier chat" in retry and "w9998" in retry
    assert "corrected complete answer" in retry
    # one version for the whole chat, built from the corrected answer
    assert ed.doc.version == base.version + 1
    assert ed.doc.captions.style.size_px == 96 and "w0041" not in ed.doc.kept_word_ids(take_index)
    assert len(_calls(job)) == 2
    assert next(e for e in job.read_trace() if e["event"] == "planner_retry")["stage"] == "chat"


def test_still_rejected_after_the_retry_applies_what_it_can(job: Job, take_index: TakeIndex, base: CutDocument,
                                                            keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = {"ops": [{"op": "set_caption_style", "style": {"size_px": 96}},
                      {"op": "set_voice_chain", "spec": {"denoise": "isolate"}}], "summary": "Done."}
    install(monkeypatch, Script(answer))
    ed = edit_document(job, take_index, base, "bigger captions and a cleaner voice", spec=_spec(keyed),
                       settings=keyed)
    assert ed.attempts == 2 and ed.applied == 1 and ed.changed
    assert any("paid voice isolation" in r for r in ed.rejected)
    assert "Not done (rejected after one retry)" in ed.summary
    assert ed.doc.audio.voice.denoise != "isolate" and ed.doc.captions.style.size_px == 96


def test_the_chat_spend_cap_skips_the_retry_or_the_call(job: Job, take_index: TakeIndex, base: CutDocument,
                                                        keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    first = {"ops": [{"op": "set_music", "spec": {"source": "x"}}, {"op": "set_caption_style",
                                                                  "style": {"size_px": 96}}], "summary": "Done."}
    script = Script(first, BIGGER, name="claude-sonnet-5-5")
    install(monkeypatch, script)
    ed = edit_document(job, take_index, base, "bigger captions", spec=_spec(keyed), settings=keyed, max_usd=0.05)
    assert len(script.calls) == 1 and ed.attempts == 1 and ed.applied == 1 and ed.changed
    assert "retry skipped" in ed.summary and "$0.05 spend cap" in ed.summary
    assert any(e["event"] == "planner_retry_skipped" for e in job.read_trace())
    # a cap already reached: no call at all, nothing changes
    script2 = Script(BIGGER)
    install(monkeypatch, script2)
    ed2 = edit_document(job, take_index, ed.doc, "bigger captions", spec=_spec(keyed), settings=keyed, max_usd=0.0)
    assert script2.calls == [] and not ed2.called and not ed2.changed and "spend cap $0.00" in ed2.summary


def test_nothing_to_change_saves_no_version(job: Job, take_index: TakeIndex, base: CutDocument, keyed: Settings,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Script({"ops": [], "summary": "The captions are already as large as they fit."}))
    ed = edit_document(job, take_index, base, "bigger captions", spec=_spec(keyed), settings=keyed)
    assert not ed.changed and ed.doc.version == base.version and job.latest_doc_version() == base.version
    assert ed.summary.startswith("The captions are already")
    # a note alone is recorded but changes nothing that renders
    install(monkeypatch, Script({"ops": [{"op": "note", "text": "creator asked; nothing to do"}], "summary": "ok"}))
    ed2 = edit_document(job, take_index, base, "check it", spec=_spec(keyed), settings=keyed)
    assert not ed2.changed and ed2.applied == 1


def test_an_unreadable_answer_spends_the_retry(job: Job, take_index: TakeIndex, base: CutDocument, keyed: Settings,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script("Sure! I made the captions bigger.", BIGGER)
    install(monkeypatch, script)
    ed = edit_document(job, take_index, base, "bigger captions", spec=_spec(keyed), settings=keyed)
    assert ed.attempts == 2 and ed.changed and "not a valid JSON object" in script.calls[1]["prompt"]


def test_provider_failure_changes_nothing(job: Job, take_index: TakeIndex, base: CutDocument, keyed: Settings,
                                          settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Script(server_error()))
    ed = edit_document(job, take_index, base, "bigger captions", spec=_spec(keyed), settings=keyed)
    assert ed.called and not ed.changed and ed.attempts == 1 and ed.doc.version == base.version
    assert ed.summary.startswith("The chat edit could not run") and "nothing changed" in ed.summary
    # no key: nothing is called at all (the real model builder refuses a spec without a key)
    monkeypatch.setattr(pv, "_native_model", _REAL_NATIVE)
    ed2 = edit_document(job, take_index, base, "bigger captions", spec=_spec(settings), settings=settings)
    assert not ed2.called and not ed2.changed and "could not run" in ed2.summary


def test_refusal_makes_no_call_and_a_mixed_request_is_planned(job: Job, take_index: TakeIndex, base: CutDocument,
                                                              keyed: Settings,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, NoCall())
    ed = edit_document(job, take_index, base, "add some upbeat background music", spec=_spec(keyed), settings=keyed)
    assert not ed.called and not ed.changed and ed.refused == ["music"] and "--tier director" in ed.summary
    assert _calls(job) == [] and job.read_jsonl(CHAT_LOG)[-1]["called"] is False
    script = Script(BIGGER)
    install(monkeypatch, script)
    ed2 = edit_document(job, take_index, base, "make the captions bigger and add music", spec=_spec(keyed),
                        settings=keyed)
    assert ed2.called and ed2.changed and ed2.refused == ["music"]
    assert "ignore that part" in script.calls[0]["prompt"]
    assert ed2.summary.startswith("Captions are bigger now.") and "not part of the standard tier" in ed2.summary


# ============================================================================================ render
class FakeRenders:
    """Fake renders (``render_full`` / the overlay pass) and QA; ``fail`` lists the render names whose QA fails."""

    def __init__(self, job: Job, fail: set[str] | None = None):
        self.job = job
        self.fail = fail or set()
        self.calls: list[tuple[str, int, str]] = []  # (kind, doc version, render)

    def _rd(self, doc: CutDocument, kind: str) -> Path:
        rd = self.job.new_render_dir()
        (rd / "render.json").write_text(json.dumps({"doc_version": doc.version}))
        for n in ("final_tiktok.mp4", "aroll.mov", "mix.wav"):
            (rd / n).write_bytes(n.encode())
        self.calls.append((kind, doc.version, rd.name))
        return rd

    def render_full(self, job: Job, doc: CutDocument, index: Any = None, **kw: Any) -> Path:
        assert kw.get("profile") == "standard"
        return self._rd(doc, "full")

    def overlay(self, job: Job, doc: CutDocument, index: Any, old: Path, **kw: Any) -> Path:
        assert kw.get("profile") == "standard" and old.is_dir()
        return self._rd(doc, "overlay")

    def evaluate(self, job: Job, rd: Any, **kw: Any) -> QaRun:
        rd = Path(rd)
        bad = rd.name in self.fail
        res = [InvariantResult(id=INVARIANTS[n][0], number=n, name=INVARIANTS[n][1], passed=not (bad and n == 7))
               for n in range(1, 11)]
        return QaRun(render_dir=rd, doc_version=None, results=res)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import studio.agent.loop as loop_mod
        import studio.qa.invariants as inv_mod

        monkeypatch.setattr(loop_mod, "render_full", self.render_full)
        monkeypatch.setattr(repair_mod, "_overlay_render", self.overlay)
        monkeypatch.setattr(inv_mod, "evaluate_render", self.evaluate)


def test_cut_unchanged(base: CutDocument) -> None:
    caps = base.model_copy(deep=True)
    caps.captions.style.size_px = 96
    caps.texts = []
    assert cut_unchanged(base, caps)
    cut = base.model_copy(deep=True)
    cut.segments[0].gap_overrides = {"g0003": 300}
    assert not cut_unchanged(base, cut)
    loud = base.model_copy(deep=True)
    loud.audio.loudness_target_lufs = -16.0
    assert not cut_unchanged(base, loud)


def test_render_change_picks_the_cheapest_pass(job: Job, take_index: TakeIndex, base: CutDocument,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fk = FakeRenders(job)
    fk.install(monkeypatch)
    champ = job.new_render_dir()
    caps = base.model_copy(deep=True, update={"version": base.version + 1})
    caps.captions.style.size_px = 96
    r = render_change(job, take_index, base, caps, champ)
    assert r.mode == "overlay" and r.passed and fk.calls[-1][0] == "overlay"
    # the cut changed: a full standard render
    cut = base.model_copy(deep=True, update={"version": base.version + 1})
    cut.segments[0].gap_overrides = {"g0003": 300}
    r2 = render_change(job, take_index, base, cut, champ)
    assert r2.mode == "full" and fk.calls[-1][0] == "full"
    # no champion render to build on: full
    assert render_change(job, take_index, base, caps, None).mode == "full"

    # the overlay pass cannot run (intermediates gone, a text changed): full instead, with the reason noted
    def need_full(*a: Any, **k: Any) -> Path:
        raise repair_mod._NeedFull("aroll.mov no longer in r1")

    monkeypatch.setattr(repair_mod, "_overlay_render", need_full)
    r3 = render_change(job, take_index, base, caps, champ)
    assert r3.mode == "full" and any("aroll.mov no longer" in n for n in r3.notes)


def test_a_failed_chat_render_gets_one_code_repair(job: Job, take_index: TakeIndex, base: CutDocument,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    fk = FakeRenders(job)
    fk.install(monkeypatch)
    seen: list[list[dict[str, Any]]] = []

    def fake_repair(job_: Job, doc: CutDocument, index: Any, rd: Path, failures: list[dict[str, Any]],
                    **kw: Any) -> RepairResult:
        assert kw["profile"] == "standard"
        seen.append(failures)
        new = fk._rd(doc, "repair")
        return RepairResult(doc=doc, render_dir=new, passed=True, fixed=["7 text_safe"], remaining=[], rerendered=True,
                            mode="overlay", notes=["p003 moved under the chin"])

    monkeypatch.setattr(repair_mod, "repair", fake_repair)
    fk.fail = {f"r{job.render_numbers()[-1] + 1 if job.render_numbers() else 1}"}
    cut = base.model_copy(deep=True, update={"version": base.version + 1})
    cut.segments[0].gap_overrides = {"g0003": 300}
    r = render_change(job, take_index, base, cut, None)
    assert r.passed and r.repair is not None and r.repair.fixed == ["7 text_safe"]
    assert seen and seen[0][0]["number"] == 7
    assert [c[0] for c in fk.calls] == ["full", "repair"] and r.render_dir.name == fk.calls[-1][2]
    # the failed render was replaced: its finals and intermediates are gone
    failed = job.renders_dir / fk.calls[0][2]
    assert not (failed / "final_tiktok.mp4").exists() and not (failed / "aroll.mov").exists()
    assert "p003 moved under the chin" in r.notes
