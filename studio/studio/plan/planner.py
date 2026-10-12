"""The standard tier's one planner call (standard-tier plan §2 step 5; ``docs/standard-tier-interfaces.md``
"Planner"): :func:`plan` asks the planner model for a :class:`~studio.plan.decision.PlanDecision` over the digest
and returns a :class:`PlanResult` the code plan consumes.

How the call is shaped, and why:

* **One ``pydantic_ai.Agent`` with ``NativeOutput(PlanDecision)``** through :func:`studio.agent.providers.build_model`
  (``role="planner"``): the role fixes effort ``low``, the 5m cache TTL and the ``model_call`` trace fields
  (``role="planner"``, ``stage="plan"``) in one place; the critics use the same NativeOutput pattern. ``max_tokens``
  is :data:`MAX_TOKENS` (16k) so adaptive thinking never truncates a 2k-token answer and never runs to the 128k the
  Director gets.
* **The cached prefix is the agent's instructions** (:func:`studio.plan.prompt.build_prefix`: the condensed doctrine
  checklist, the compact schema, the brief/style hints); the digest is the user turn. Anthropic caches the
  instructions block (``anthropic_cache_instructions``), so the retry seconds later re-reads it at the read price.
* **Validation, then one retry.** :func:`studio.plan.decision.validate_decision` rejects what code could not apply
  or doctrine forbids; the retry carries every rejection line verbatim (:func:`studio.plan.prompt.retry_prompt`)
  on top of the first exchange, so the model sees its own answer. An answer that is not valid PlanDecision JSON is
  treated the same way (one retry, the parse error as the rejection line). A second failure ends the call.
* **Failure means the pure-code plan, never a silent escalation.** A refusal, a provider error, a missing key or a
  second rejected answer returns ``PlanResult(decision=None, note=...)``: the caller ships the deterministic plan and
  the report says why. The only escalation is ``STUDIO_PLANNER_FALLBACK=<model>``: when it names another model,
  the same procedure (one call, one retry) runs on it with the same provider and key, and the note and a
  ``planner_fallback`` trace event say so. Without a key nothing is escalated (the fallback would lack it too).
* **Everything is written down.** ``logs/plan.json`` holds the decision (or null), attempts, rejections, usage per
  attempt, the note and the prefix/digest sizes; the trace carries one ``model_call`` per attempt (via
  :class:`~studio.agent.providers.TracedModel`) and one ``plan`` summary event.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from studio.agent.providers import ModelSpec, build_model, house_spec, map_provider_error, trace_event
from studio.config import get_settings
from studio.plan.decision import PlanDecision, validate_decision
from studio.plan.digest import Digest, estimate_tokens
from studio.plan.prompt import build_prefix, digest_prompt, retry_prompt

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # the CLI prints its own progress

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai.messages import ModelMessage

    from studio.config import Settings
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = ["FALLBACK_ENV", "MAX_ATTEMPTS", "MAX_TOKENS", "PLAN_LOG", "PlanResult", "plan"]

#: Output budget per call: thinking plus the ~2k-token answer; never the Director's 128k.
MAX_TOKENS = 16_000
#: One call and one retry per model (the retry carries the rejection lines).
MAX_ATTEMPTS = 2
#: The only escalation: the model to run the same procedure on when the planner fails outright.
FALLBACK_ENV = "STUDIO_PLANNER_FALLBACK"
#: Where the decision, usage and note are saved (relative to the job root).
PLAN_LOG = "logs/plan.json"

_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


@dataclass
class PlanResult:
    decision: PlanDecision | None  # None: the pure-code plan is used (refusal, invalid output after one retry, no key)
    attempts: int
    rejected: list[str]  # validate_decision rejections of the first attempt, if any
    usage: dict[str, Any]  # input/output/cache tokens of every attempt
    note: str


@dataclass
class _Attempt:
    """One model call's outcome: a validated decision, or why there is none."""

    model: str
    called: bool = False  # False when the model could not even be built (no key, bad spec)
    decision: PlanDecision | None = None
    rejections: list[str] = field(default_factory=list)
    error: str | None = None  # a provider error, refusal or missing key: the procedure stops here
    error_kind: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    messages: list[ModelMessage] = field(default_factory=list)  # the exchange so far (the retry's history)

    @property
    def retryable(self) -> bool:
        return self.called and self.decision is None and self.error is None


# ============================================================================================ usage
def _usage_of(messages: list[Any]) -> dict[str, Any]:
    """Token counts summed over the model responses in ``messages`` (one call normally, so one response)."""
    from pydantic_ai.messages import ModelResponse

    out: dict[str, Any] = dict.fromkeys(_USAGE_KEYS, 0)
    requests = 0
    for m in messages:
        if isinstance(m, ModelResponse):
            requests += 1
            for k in _USAGE_KEYS:
                out[k] += int(getattr(m.usage, k, 0) or 0)
    out["requests"] = requests
    return out


def _usage_summary(attempts: list[_Attempt]) -> dict[str, Any]:
    """Totals at the top level, one row per attempt under ``attempts`` (model, tokens, ok)."""
    total: dict[str, Any] = dict.fromkeys(_USAGE_KEYS, 0)
    rows = []
    for i, a in enumerate(attempts, start=1):
        row = {"attempt": i, "model": a.model, **{k: int(a.usage.get(k, 0)) for k in _USAGE_KEYS},
               "requests": int(a.usage.get("requests", 0)), "ok": a.decision is not None}
        rows.append(row)
        for k in _USAGE_KEYS:
            total[k] += row[k]
    return {**total, "attempts": rows}


# ============================================================================================ the call
def _agent(spec: ModelSpec, job: Job, instructions: str) -> Any:
    from pydantic_ai import Agent, NativeOutput, PromptedOutput

    model = build_model(spec, role="planner", job=job, stage="plan")
    output = PromptedOutput(PlanDecision) if spec.provider == "openai_compat" else NativeOutput(PlanDecision)
    return Agent(model, output_type=output, instructions=instructions, retries=0, name="planner",
                 model_settings={"max_tokens": MAX_TOKENS})


def _run_once(agent: Any, spec: ModelSpec, index: TakeIndex, prompt: str, history: list[ModelMessage]) -> _Attempt:
    """One model call on top of ``history``: the agent's answer validated, or the failure classified."""
    from pydantic_ai import capture_run_messages
    from pydantic_ai.exceptions import ContentFilterError, UnexpectedModelBehavior

    att = _Attempt(model=spec.model, called=True)
    with capture_run_messages() as captured:
        try:
            result = agent.run_sync(prompt, message_history=list(history))
        except ContentFilterError as e:  # a refusal (subclass of UnexpectedModelBehavior): never retried here
            att.messages = list(captured)
            att.usage = _usage_of(att.messages[len(history):])
            err = map_provider_error(e, spec)
            att.error, att.error_kind = err.message, err.kind
            return att
        except UnexpectedModelBehavior as e:
            # the answer was not a PlanDecision (no JSON, or JSON the schema rejects): spend the retry on it
            att.messages = list(captured)
            att.usage = _usage_of(att.messages[len(history):])
            att.rejections = [f"the answer was not a valid PlanDecision JSON object ({e}); return exactly one JSON "
                              "object with the schema's keys"]
            return att
        except Exception as e:  # provider/network error: no retry here (the SDK already retried transient ones)
            att.messages = list(captured)
            att.usage = _usage_of(att.messages[len(history):])
            err = map_provider_error(e, spec)
            att.error, att.error_kind = err.message, err.kind
            return att
    att.messages = list(result.all_messages())
    att.usage = _usage_of(list(result.new_messages()))
    decision: PlanDecision = result.output
    att.rejections = validate_decision(decision, index)
    if not att.rejections:
        att.decision = decision
    return att


def _run_model(spec: ModelSpec, job: Job, index: TakeIndex, digest: Digest, prefix: str) -> list[_Attempt]:
    """The procedure on one model: a first call, then at most one retry carrying the rejection lines."""
    try:
        agent = _agent(spec, job, prefix)
    except Exception as e:  # a missing key or an unusable spec: nothing was called
        err = map_provider_error(e, spec)
        return [_Attempt(model=spec.model, error=err.message, error_kind=err.kind)]
    attempts = [_run_once(agent, spec, index, digest_prompt(digest.text), [])]
    first = attempts[0]
    if first.retryable and len(attempts) < MAX_ATTEMPTS:
        trace_event(job, "planner_retry", stage="plan", model=spec.model, rejections=first.rejections)
        attempts.append(_run_once(agent, spec, index, retry_prompt(first.rejections), first.messages))
    return attempts


def _fallback_spec(primary: ModelSpec) -> ModelSpec | None:
    """The ``STUDIO_PLANNER_FALLBACK`` model on the primary's provider, key and settings; None when unset or the
    same model. Capabilities are re-derived for the new model name."""
    name = (os.environ.get(FALLBACK_ENV) or "").strip()
    if not name or name == primary.model:
        return None
    return ModelSpec(provider=primary.provider, model=name, api_key=primary.api_key, base_url=primary.base_url,
                     effort=primary.effort, byok=primary.byok, stream=primary.stream,
                     server_fallbacks=primary.server_fallbacks, extra_settings=dict(primary.extra_settings))


def _note(attempts: list[_Attempt], *, fallback: ModelSpec | None, escalated: bool) -> str:
    last = attempts[-1]
    if last.decision is not None:
        calls = sum(1 for a in attempts if a.called)
        note = f"planner decision accepted on attempt {calls} ({last.model})"
        return note + (f"; escalated to {last.model} ({FALLBACK_ENV})" if escalated else "")
    if last.error is not None:
        why = f"planner {last.error_kind or 'error'} on {last.model}: {last.error}"
    else:
        calls = sum(1 for a in attempts if a.called and a.model == last.model)
        why = f"planner answer rejected after {calls} attempt(s) on {last.model}: " + " | ".join(last.rejections)
    if escalated:
        return f"{why} (after {FALLBACK_ENV} escalation); pure-code plan used"
    tail = "; pure-code plan used"
    if fallback is None and last.error_kind != "missing_key":
        tail += f" (no {FALLBACK_ENV} set: no other model was tried)"
    return why + tail


def _slim_spec(spec: ModelSpec) -> dict[str, Any]:
    return {"provider": spec.provider, "model": spec.model, "effort": spec.effort, "byok": bool(spec.byok)}


def plan(job: Job, index: TakeIndex, digest: Digest, *, spec: ModelSpec | None = None,
         settings: Settings | None = None, brief: str | None = None, style: str | None = None) -> PlanResult:
    """Ask the planner model for the take's :class:`PlanDecision` (see the module docstring for the procedure).

    ``spec`` is the planner model (BYOK or house); None means :func:`house_spec` ``"planner"`` from ``settings``.
    ``brief`` and ``style`` go into the cached prefix as hints. The result's ``decision`` is None whenever the model
    did not produce a valid decision within one retry; the caller then uses the pure-code plan. Always writes
    ``logs/plan.json`` and a ``plan`` trace event; never raises for a model failure.
    """
    s = settings if settings is not None else get_settings()
    primary = spec if spec is not None else house_spec("planner", settings=s)
    prefix = build_prefix(brief=brief, style=style)
    attempts = _run_model(primary, job, index, digest, prefix)
    fallback = _fallback_spec(primary)
    escalated = False
    if attempts[-1].decision is None and fallback is not None and attempts[-1].error_kind != "missing_key":
        escalated = True
        trace_event(job, "planner_fallback", stage="plan", from_model=primary.model, to_model=fallback.model,
                    reason=attempts[-1].error or "rejected after retry", env=FALLBACK_ENV)
        attempts.extend(_run_model(fallback, job, index, digest, prefix))
    last = attempts[-1]
    note = _note(attempts, fallback=fallback, escalated=escalated)
    usage = _usage_summary(attempts)
    result = PlanResult(decision=last.decision, attempts=sum(1 for a in attempts if a.called),
                        rejected=list(attempts[0].rejections), usage=usage, note=note)
    record = {
        "decision": last.decision.model_dump(mode="json") if last.decision is not None else None,
        "attempts": result.attempts, "rejected": result.rejected,
        "rejections_by_attempt": [a.rejections for a in attempts],
        "errors": [{"model": a.model, "kind": a.error_kind, "message": a.error} for a in attempts if a.error],
        "usage": usage, "note": note,
        "model": _slim_spec(primary), "fallback_model": fallback.model if escalated else None,
        "brief": brief, "style": style,
        "prefix": {"chars": len(prefix), "tokens_est": estimate_tokens(prefix)},
        "digest": {"chars": len(digest.text), "tokens_est": estimate_tokens(digest.text), "stats": digest.stats},
    }
    job.save_json(PLAN_LOG, record)
    trace_event(job, "plan", stage="plan", model=primary.model, attempts=result.attempts,
                decision=last.decision is not None, rejected=result.rejected, note=note,
                **{k: usage[k] for k in _USAGE_KEYS})
    return result
