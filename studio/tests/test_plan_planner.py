"""Tests for studio.plan.planner (the standard tier's one planner call) and studio.plan.prompt (its cached prefix).

Keyless and offline: the model is a scripted ``FunctionModel`` installed under :func:`providers.build_model` by
patching ``providers._native_model``, so the real wrapping (``StreamingRequestModel`` for Anthropic specs,
``TracedModel`` with role ``planner`` / stage ``plan``) is exercised. The take is the hand-built fixture index
(conftest): s001 hook; s002 false start (c01); s003 retake (c01 rec); s004 opens with ``{Um}``; s005 the spoken CTA.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from director_script import last_prompt
from pydantic_ai.exceptions import ContentFilterError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from studio.agent import providers as pv
from studio.agent.providers import ModelSpec
from studio.config import Settings
from studio.jobs import Job
from studio.perception.index import TakeIndex
from studio.plan import planner
from studio.plan.decision import PLAN_SCHEMA_JSON, PlanDecision
from studio.plan.digest import Digest, build_digest, estimate_tokens
from studio.plan.planner import FALLBACK_ENV, MAX_TOKENS, PLAN_LOG, PlanResult, plan
from studio.plan.prompt import (
    CHECKLIST_MAX_TOKENS,
    DOCTRINE_CHECKLIST,
    SCHEMA_INTRO,
    build_prefix,
    digest_prompt,
    retry_prompt,
)

FAKE_ANT = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE-0123456789"
FAKE_OAI = "sk-proj-FAKEFAKEFAKEFAKEFAKEFAKE0123456789"

#: A decision validate_decision accepts on the fixture take (mirrors test_plan_decision's sound decision).
VALID: dict[str, Any] = {
    "style_primary": "explainer", "goal": "teach restraint in one line", "hook_promise": "most people over-cut",
    "choose_take": [{"cluster_id": "c01", "sentence_id": "s003"}],
    "payoff": {"from_word": "w0018", "to_word": "w0018"}, "cta": {"from_word": "w0032", "to_word": "w0033"},
    "must_keep": [{"from_word": "w0020", "to_word": "w0028"}], "deliberate_beat_gap_ids": ["g0006"],
    "cut_filler_word_ids": ["w0019"], "caption_emphasis_word_ids": ["w0018", "w0024"],
    "notes": "keep the breath after restraint",
}
#: Drops the creator's spoken CTA as a tangent: the one thing the guards never let through.
DROPS_CTA: dict[str, Any] = {**VALID, "cta": None, "drop_sentences": [{"sentence_id": "s005", "reason": "tangent"}]}
CTA_REJECTION = "s005 is the creator's spoken call to action"


# ============================================================================================ scripted planner
class Script:
    """A scripted planner model: ``steps[i]`` answers call ``i`` (a dict is sent as JSON text, a str as raw text, an
    exception is raised); the last step repeats. Records what every call saw."""

    def __init__(self, *steps: Any, name: str = "scripted-planner"):
        self.steps = list(steps)
        self.name = name
        self.calls: list[dict[str, Any]] = []  # instructions, prompt (last user turn), model_settings, history

    def answer(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        k = len(self.calls)
        self.calls.append({"instructions": messages[0].instructions if hasattr(messages[0], "instructions") else None,
                           "prompt": last_prompt(messages), "model_settings": dict(info.model_settings or {}),
                           "history": list(messages)})
        step = self.steps[min(k, len(self.steps) - 1)]
        if isinstance(step, BaseException):
            raise step
        text = step if isinstance(step, str) else json.dumps(step)
        return ModelResponse(parts=[TextPart(text)],
                             usage=RequestUsage(input_tokens=1000 + k, output_tokens=200 + k,
                                                cache_read_tokens=800 if k else 0, cache_write_tokens=0 if k else 800))

    def model(self) -> FunctionModel:
        async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            resp = self.answer(messages, info)
            for p in resp.parts:
                if isinstance(p, TextPart):
                    yield p.content

        return FunctionModel(self.answer, stream_function=stream, model_name=self.name)


def install(monkeypatch: pytest.MonkeyPatch, script: Script, *,
            by_model: dict[str, Script] | None = None) -> list[tuple[ModelSpec, str | None]]:
    """Serve ``script`` (or ``by_model[spec.model]``) as the native model under ``build_model``; returns the
    ``(spec, role)`` pairs the planner asked for."""
    seen: list[tuple[ModelSpec, str | None]] = []

    def fake_native(spec: ModelSpec, role: str | None) -> FunctionModel:
        seen.append((spec, role))
        return (by_model or {}).get(spec.model, script).model()

    monkeypatch.setattr(pv, "_native_model", fake_native)
    return seen


def server_error() -> ModelHTTPError:
    return ModelHTTPError(status_code=500, model_name="claude-sonnet-5-5",
                          body={"error": {"type": "api_error", "message": "boom"}})


def model_calls(job: Job) -> list[dict[str, Any]]:
    return [r for r in job.read_trace() if r["event"] == "model_call"]


def events(job: Job, name: str) -> list[dict[str, Any]]:
    return [r for r in job.read_trace() if r["event"] == name]


@pytest.fixture
def digest(take_index: TakeIndex) -> Digest:
    return build_digest(take_index, platform="tiktok")


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    """House settings with an Anthropic key, so ``house_spec("planner")`` is buildable."""
    return settings.with_keys(anthropic=FAKE_ANT)


# ============================================================================================ prompt
def test_checklist_fits_the_1k_token_budget_and_carries_the_doctrine() -> None:
    assert CHECKLIST_MAX_TOKENS == 1000
    assert estimate_tokens(DOCTRINE_CHECKLIST) <= CHECKLIST_MAX_TOKENS
    text = DOCTRINE_CHECKLIST.lower()
    # SKILL.md core directives, condensed
    for needle in ("radio test", "false start only if a later re-delivery", "never invent a cta", "restraint",
                   "measured energy", "never write times"):
        assert needle in text, needle
    # the style picker and the drop reasons the schema enumerates
    for style in ("explainer", "tutorial", "listicle", "storytime", "comedy", "hot_take", "podcast_clip",
                  "sales_ugc", "founder_brand"):
        assert style in text, style
    for reason in ("preamble", "tangent", "after_payoff", "unfinished", "repeat", "repeats_hook"):
        assert reason in text, reason
    # every decision field is explained (the schema carries no descriptions)
    for name in PlanDecision.model_fields:
        if name not in ("style_primary", "style_secondary"):
            assert name in DOCTRINE_CHECKLIST, name


def test_prefix_is_checklist_schema_then_hints() -> None:
    plain = build_prefix()
    assert plain.startswith(DOCTRINE_CHECKLIST) and SCHEMA_INTRO in plain and plain.endswith(PLAN_SCHEMA_JSON)
    assert plain == build_prefix(brief=None, style=None) == build_prefix(brief="  ", style="")  # cache-stable bytes
    hinted = build_prefix(brief="sell the  course\nwithout hype", style="explainer")
    assert hinted.startswith(plain)  # hints come after the shared bytes
    assert "Creator brief" in hinted and "sell the course without hype" in hinted
    assert "Requested style" in hinted and hinted.rstrip().endswith("explainer")
    assert estimate_tokens(hinted) < 2500
    assert digest_prompt("DIGEST\n").startswith("DIGEST\n\n") and "PlanDecision" in digest_prompt("x")
    rp = retry_prompt(["a: bad", "b: worse"])
    assert "- a: bad\n- b: worse" in rp and "corrected PlanDecision" in rp


# ============================================================================================ the call
def test_valid_decision_on_the_first_try(job: Job, take_index: TakeIndex, digest: Digest, keyed: Settings,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(VALID)
    seen = install(monkeypatch, script)
    res = plan(job, take_index, digest, settings=keyed, brief="teach restraint", style="explainer")
    assert isinstance(res, PlanResult)
    assert res.decision == PlanDecision.model_validate(VALID)
    assert res.attempts == 1 and res.rejected == [] and "accepted on attempt 1" in res.note
    # the house planner spec, built for the planner role
    assert len(seen) == 1 and seen[0][1] == "planner"
    spec = seen[0][0]
    assert (spec.provider, spec.model, spec.effort, spec.byok) == ("anthropic", "claude-sonnet-5-5", "low", False)
    # the cached prefix is the instructions; the digest is the user turn; max_tokens is the planner's budget
    call = script.calls[0]
    assert call["instructions"] == build_prefix(brief="teach restraint", style="explainer")
    assert call["instructions"].startswith(DOCTRINE_CHECKLIST) and PLAN_SCHEMA_JSON in call["instructions"]
    assert "Take digest:" not in call["instructions"]
    assert call["prompt"] == digest_prompt(digest.text) and digest.text in call["prompt"]
    assert call["model_settings"]["max_tokens"] == MAX_TOKENS == 16_000
    # usage per attempt (streamed Anthropic call: the scripted stream's estimate, but present and attributed)
    assert [row["attempt"] for row in res.usage["attempts"]] == [1]
    row = res.usage["attempts"][0]
    assert row["model"] == "claude-sonnet-5-5" and row["ok"] and row["requests"] == 1
    assert res.usage["input_tokens"] == row["input_tokens"] > 0 and res.usage["output_tokens"] > 0
    # traced as model_call role planner / stage plan (TracedModel built with role="planner"), plus a plan summary
    recs = model_calls(job)
    assert len(recs) == 1
    rec = recs[0]
    assert (rec["role"], rec["stage"], rec["ok"]) == ("planner", "plan", True)
    assert rec["model"] == "claude-sonnet-5-5" and rec["provider"] == "anthropic" and rec["cache_ttl"] == "5m"
    assert rec["effort"] == "low" and rec["sse"] is True and rec["byok"] is False
    summary = events(job, "plan")
    assert len(summary) == 1 and summary[0]["decision"] is True and summary[0]["attempts"] == 1
    # logs/plan.json carries the decision, usage and sizes
    p = job.logs_dir / "plan.json"
    assert p.exists() and job.path(PLAN_LOG) == p
    saved = json.loads(p.read_text())
    assert saved["decision"] == res.decision.model_dump(mode="json")
    assert saved["attempts"] == 1 and saved["rejected"] == [] and saved["usage"] == res.usage
    assert saved["note"] == res.note and saved["fallback_model"] is None
    assert saved["model"] == {"provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "low", "byok": False}
    assert saved["brief"] == "teach restraint" and saved["style"] == "explainer"
    assert saved["prefix"]["tokens_est"] == estimate_tokens(call["instructions"])
    assert saved["digest"]["chars"] == len(digest.text) and saved["digest"]["stats"] == digest.stats


def test_rejected_answer_gets_one_retry_carrying_the_rejection_text(job: Job, take_index: TakeIndex, digest: Digest,
                                                                     keyed: Settings,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(DROPS_CTA, VALID)
    install(monkeypatch, script)
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision == PlanDecision.model_validate(VALID)
    assert res.attempts == 2 and "accepted on attempt 2" in res.note
    assert len(res.rejected) == 1 and CTA_REJECTION in res.rejected[0]
    # the retry's user turn carries the rejection line verbatim, on top of the first exchange
    assert len(script.calls) == 2
    retry = script.calls[1]
    assert retry["prompt"] == retry_prompt(res.rejected) and CTA_REJECTION in retry["prompt"]
    assert retry["instructions"] == script.calls[0]["instructions"]  # same cached prefix
    texts = [p.content for m in retry["history"] if isinstance(m, ModelResponse) for p in m.parts
             if isinstance(p, TextPart)]
    assert texts == [json.dumps(DROPS_CTA)]  # the model sees its own rejected answer
    assert digest.text in retry["history"][0].parts[0].content  # and the digest it answered
    # usage per attempt and the trace
    rows = res.usage["attempts"]
    assert [(r["attempt"], r["ok"]) for r in rows] == [(1, False), (2, True)]
    assert res.usage["input_tokens"] == rows[0]["input_tokens"] + rows[1]["input_tokens"]
    assert len(model_calls(job)) == 2 and all(r["role"] == "planner" and r["ok"] for r in model_calls(job))
    retries = events(job, "planner_retry")
    assert len(retries) == 1 and retries[0]["rejections"] == res.rejected
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["rejections_by_attempt"] == [res.rejected, []] and saved["attempts"] == 2


def test_two_rejected_answers_mean_no_decision(job: Job, take_index: TakeIndex, digest: Digest, keyed: Settings,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(DROPS_CTA)
    install(monkeypatch, script)
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision is None and res.attempts == 2 and len(script.calls) == 2
    assert CTA_REJECTION in res.rejected[0]
    assert "rejected after 2 attempt(s)" in res.note and "pure-code plan used" in res.note
    assert FALLBACK_ENV in res.note and "no other model was tried" in res.note
    assert CTA_REJECTION in res.note
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["decision"] is None and len(saved["rejections_by_attempt"]) == 2
    assert [r["ok"] for r in saved["usage"]["attempts"]] == [False, False]
    assert events(job, "plan")[0]["decision"] is False
    assert not events(job, "planner_fallback")  # never a silent escalation


def test_schema_invalid_output_spends_the_one_retry(job: Job, take_index: TakeIndex, digest: Digest,
                                                     keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script("I would rather not answer in JSON.", VALID)
    install(monkeypatch, script)
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision is not None and res.attempts == 2
    assert len(res.rejected) == 1 and "not a valid PlanDecision JSON" in res.rejected[0]
    assert "not a valid PlanDecision JSON" in script.calls[1]["prompt"]
    # two bad answers: no decision, and the second failure is reported
    job2 = Job.create("fixture-job-2", work_dir=job.root.parent)
    script2 = Script("nope", json.dumps({"goal": "no required keys"}))
    install(monkeypatch, script2)
    res2 = plan(job2, take_index, digest, settings=keyed)
    assert res2.decision is None and res2.attempts == 2 and len(script2.calls) == 2
    assert "rejected after 2 attempt(s)" in res2.note


def test_provider_error_and_refusal_mean_no_decision_and_no_retry(job: Job, take_index: TakeIndex, digest: Digest,
                                                                   keyed: Settings,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(server_error(), VALID)  # a retry would have succeeded: there must be none
    install(monkeypatch, script)
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision is None and res.attempts == 1 and len(script.calls) == 1 and res.rejected == []
    assert "planner server on claude-sonnet-5-5" in res.note and "server error (500)" in res.note
    assert "pure-code plan used" in res.note
    rec = model_calls(job)[-1]
    assert rec["ok"] is False and rec["error_kind"] == "server" and rec["role"] == "planner"
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["decision"] is None and saved["errors"][0]["kind"] == "server"
    # a refusal: the same, and the note names it
    job2 = Job.create("fixture-job-2", work_dir=job.root.parent)
    script2 = Script(ContentFilterError("Content filter triggered. Category: 'bio'."), VALID)
    install(monkeypatch, script2)
    res2 = plan(job2, take_index, digest, settings=keyed)
    assert res2.decision is None and res2.attempts == 1 and len(script2.calls) == 1
    assert "planner refusal" in res2.note and "declined the request" in res2.note


def test_missing_key_means_no_decision_without_a_call(job: Job, take_index: TakeIndex, digest: Digest,
                                                       settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """No key: nothing is called and nothing is escalated, even with a fallback named (it would lack the key too).
    The real ``_native_model`` runs here (no scripted model), so the key check is the one production uses."""
    monkeypatch.setenv(FALLBACK_ENV, "claude-haiku-5-5")
    assert not settings.has_key("anthropic")
    res = plan(job, take_index, digest, settings=settings)
    assert res.decision is None and res.attempts == 0 and res.rejected == []
    assert "planner missing_key" in res.note and "key not configured" in res.note and "pure-code plan" in res.note
    assert FAKE_ANT not in res.note and model_calls(job) == [] and not events(job, "planner_fallback")
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["decision"] is None and saved["attempts"] == 0 and saved["usage"]["attempts"][0]["requests"] == 0
    assert saved["errors"][0]["kind"] == "missing_key" and saved["fallback_model"] is None


def test_byok_spec_is_honoured(job: Job, take_index: TakeIndex, digest: Digest, settings: Settings,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A creator's spec drives the call as given (provider, model, key, effort), the house planner is never built,
    and the trace shows BYOK. A non-streaming spec also shows the exact token accounting."""
    spec = ModelSpec(provider="openai", model="gpt-5.5", api_key=FAKE_OAI, byok=True, effort="low")
    script = Script(VALID)
    seen = install(monkeypatch, script)
    res = plan(job, take_index, digest, spec=spec, settings=settings)  # settings carry no key: not needed
    assert res.decision is not None and res.attempts == 1
    assert len(seen) == 1 and seen[0][0] is spec and seen[0][1] == "planner"
    rec = model_calls(job)[0]
    assert (rec["provider"], rec["model"], rec["byok"], rec["role"], rec["stage"]) == (
        "openai", "gpt-5.5", True, "planner", "plan")
    assert rec["cache_ttl"] is None and rec["sse"] is False
    row = res.usage["attempts"][0]
    assert (row["input_tokens"], row["output_tokens"], row["cache_write_tokens"], row["cache_read_tokens"]) == (
        1000, 200, 800, 0)
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["model"] == {"provider": "openai", "model": "gpt-5.5", "effort": "low", "byok": True}
    assert FAKE_OAI not in json.dumps(saved) and FAKE_OAI not in json.dumps(job.read_trace())


def test_fallback_env_is_the_only_escalation(job: Job, take_index: TakeIndex, digest: Digest, keyed: Settings,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FALLBACK_ENV, "claude-opus-5-5")
    primary, fallback = Script(DROPS_CTA, name="primary"), Script(VALID, name="fallback")
    seen = install(monkeypatch, primary, by_model={"claude-opus-5-5": fallback})
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision == PlanDecision.model_validate(VALID)
    assert len(primary.calls) == 2 and len(fallback.calls) == 1 and res.attempts == 3
    assert CTA_REJECTION in res.rejected[0]  # the first attempt's rejections are kept
    assert "escalated to claude-opus-5-5" in res.note and FALLBACK_ENV in res.note
    # the fallback keeps the provider, key, effort and role; its calls are traced like the primary's
    assert [(s.model, s.effort, s.api_key, role) for s, role in seen] == [
        ("claude-sonnet-5-5", "low", FAKE_ANT, "planner"), ("claude-opus-5-5", "low", FAKE_ANT, "planner")]
    assert [r["model"] for r in model_calls(job)] == ["claude-sonnet-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    fb = events(job, "planner_fallback")
    assert len(fb) == 1 and (fb[0]["from_model"], fb[0]["to_model"]) == ("claude-sonnet-5-5", "claude-opus-5-5")
    assert [row["model"] for row in res.usage["attempts"]] == ["claude-sonnet-5-5", "claude-sonnet-5-5",
                                                               "claude-opus-5-5"]
    saved = json.loads((job.logs_dir / "plan.json").read_text())
    assert saved["fallback_model"] == "claude-opus-5-5" and saved["model"]["model"] == "claude-sonnet-5-5"
    # the fallback's cached prefix is the same bytes
    assert fallback.calls[0]["instructions"] == primary.calls[0]["instructions"]


def test_fallback_after_a_provider_error_and_its_own_failure(job: Job, take_index: TakeIndex, digest: Digest,
                                                              keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FALLBACK_ENV, "claude-haiku-5-5")
    primary, fallback = Script(server_error(), name="primary"), Script(DROPS_CTA, VALID, name="fallback")
    install(monkeypatch, primary, by_model={"claude-haiku-5-5": fallback})
    res = plan(job, take_index, digest, settings=keyed)
    assert res.decision is not None and res.attempts == 3 and len(fallback.calls) == 2
    assert res.rejected == []  # the first attempt errored; it had no rejections
    assert "escalated to claude-haiku-5-5" in res.note
    # the fallback fails too: the note says the escalation happened and still no decision
    job2 = Job.create("fixture-job-2", work_dir=job.root.parent)
    install(monkeypatch, Script(DROPS_CTA, name="primary"), by_model={"claude-haiku-5-5": Script(DROPS_CTA)})
    res2 = plan(job2, take_index, digest, settings=keyed)
    assert res2.decision is None and res2.attempts == 4
    assert f"(after {FALLBACK_ENV} escalation)" in res2.note and "claude-haiku-5-5" in res2.note
    # the same model named as fallback is not an escalation
    monkeypatch.setenv(FALLBACK_ENV, "claude-sonnet-5-5")
    job3 = Job.create("fixture-job-3", work_dir=job.root.parent)
    install(monkeypatch, Script(DROPS_CTA))
    res3 = plan(job3, take_index, digest, settings=keyed)
    assert res3.decision is None and res3.attempts == 2 and not events(job3, "planner_fallback")
    assert planner._fallback_spec(pv.house_spec("planner", settings=keyed)) is None
    # a BYOK primary keeps its own key and flag on the fallback (the env var is explicit; billing never switches)
    monkeypatch.setenv(FALLBACK_ENV, "claude-haiku-5-5")
    byok = ModelSpec(provider="anthropic", model="claude-sonnet-5-5", api_key=FAKE_OAI, byok=True, effort="medium",
                     stream=False)
    fb = planner._fallback_spec(byok)
    assert fb is not None and (fb.model, fb.api_key, fb.byok, fb.effort, fb.stream) == (
        "claude-haiku-5-5", FAKE_OAI, True, "medium", False)
    assert fb.capabilities == pv.capabilities_for("anthropic", "claude-haiku-5-5")


def test_plan_json_is_written_on_every_outcome_and_overwritten(job: Job, take_index: TakeIndex, digest: Digest,
                                                                keyed: Settings,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Script(VALID))
    plan(job, take_index, digest, settings=keyed)
    first = json.loads((job.logs_dir / "plan.json").read_text())
    assert first["decision"] is not None
    install(monkeypatch, Script(DROPS_CTA))
    plan(job, take_index, digest, settings=keyed)
    second = json.loads((job.logs_dir / "plan.json").read_text())
    assert second["decision"] is None and second["attempts"] == 2 and second != first
