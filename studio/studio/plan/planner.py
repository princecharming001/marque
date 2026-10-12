"""The standard tier's one planner call (standard-tier plan §2 step 5; ``docs/standard-tier-interfaces.md``
"Planner"): :func:`plan` asks the planner model for a :class:`~studio.plan.decision.PlanDecision` over the digest
and returns a :class:`PlanResult` the code plan consumes.

How the call is shaped, and why:

* **One ``pydantic_ai.Agent`` with ``NativeOutput(PlanDecision)``** through :func:`studio.agent.providers.build_model`
  (``role="planner"``): the role fixes effort ``low``, the 5m cache TTL and the ``model_call`` trace fields
  (``role="planner"``, ``stage="plan"``) in one place; the critics use the same NativeOutput pattern. ``max_tokens``
  is :data:`MAX_TOKENS` (8k: 2-3x the plan's 3k-token criterion, so low-effort thinking does not truncate a 2k-token
  answer, while one call on Sonnet 5.5 stays under $0.09) and never the 128k the Director gets. Server-side refusal
  fallbacks are off for the role (:func:`studio.agent.providers.house_spec`).
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
  ``planner_fallback`` trace event say so. Without a key nothing is escalated (the fallback would lack it too), and
  a fallback priced above the primary (or unpriced, against a priced primary) is refused unless
  ``STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER=1``.
* **A spend cap.** ``max_usd`` (``--max-usd``; default :data:`DEFAULT_MAX_USD`, or ``STUDIO_MAX_MODEL_USD`` when that
  is lower) caps the model spend of one plan: the first call runs while nothing is spent past the cap, and the retry
  and the fallback run only when what is spent plus that call's worst case (its prompt uncached, :data:`MAX_TOKENS`
  out, at list prices) stays under it. A first answer cut off at ``max_tokens`` is not retried either (the retry
  would hit the same limit and pay twice). A skipped call is named in the note.
* **Everything is written down.** ``logs/plan.json`` holds the decision (or null), attempts, rejections, usage per
  attempt, the note and the prefix/digest sizes; the trace carries one ``model_call`` per attempt (via
  :class:`~studio.agent.providers.TracedModel`) and one ``plan`` summary event.
"""

from __future__ import annotations

import contextlib
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

__all__ = ["ALLOW_PRICIER_ENV", "DEFAULT_MAX_USD", "FALLBACK_ENV", "MAX_ATTEMPTS", "MAX_TOKENS", "PLAN_LOG",
           "PlanResult", "SpendCap", "plan", "spend_cap"]

#: Output budget per call: thinking plus the ~2k-token answer (2-3x the plan's 3k-token criterion at effort low);
#: never the Director's 128k. At 16k a truncated answer cost $0.17 on Sonnet 5.5 and spent the retry on the same limit.
MAX_TOKENS = 8_000
#: The standard tier's model-spend cap for one plan (or one chat) when no ``--max-usd`` is given: the tier promises
#: under $0.20 a video all-in, and the planner is its only model call.
DEFAULT_MAX_USD = 0.15
#: ``STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER=1`` lets the fallback be a model priced above the primary.
ALLOW_PRICIER_ENV = "STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER"
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
    truncated: bool = False  # the answer stopped at max_tokens (a retry would hit the same limit)
    skipped: str | None = None  # why the retry after this attempt was not made (spend cap, truncation)

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


# ============================================================================================ spend cap
def _model_spend(job: Job) -> float:
    """The job's model spend at list prices (ASR and paid isolation are not model calls)."""
    from studio.agent.cost import job_cost

    return job_cost(job).model_usd


def worst_call_usd(spec: ModelSpec, input_tokens: int, *, max_tokens: int = MAX_TOKENS) -> float:
    """One call's worst case at list prices: ``input_tokens`` uncached and ``max_tokens`` out; 0.0 for an unpriced
    model (the cap cannot price it)."""
    from studio.agent.cost import call_cost

    c = call_cost({"model": spec.model, "input_tokens": int(input_tokens), "output_tokens": int(max_tokens),
                   "cache_ttl": "5m"})
    return float(c or 0.0)


@dataclass
class SpendCap:
    """A model-spend cap over one plan or one chat (spend counted from when it was made)."""

    job: Job
    cap: float
    base: float = 0.0

    def spent(self) -> float:
        return max(0.0, _model_spend(self.job) - self.base)

    def first_call(self) -> str | None:
        """Why the first call is not made (the cap is already reached), else None."""
        spent = self.spent()
        if spent >= self.cap:
            return f"spend cap ${self.cap:.2f} reached (${spent:.3f} spent): no call made"
        return None

    def another_call(self, spec: ModelSpec, input_tokens: int, *, max_tokens: int = MAX_TOKENS) -> str | None:
        """Why one more call (a retry, a fallback) is not made, else None."""
        spent, worst = self.spent(), worst_call_usd(spec, input_tokens, max_tokens=max_tokens)
        if spent + worst > self.cap + 1e-9:
            return (f"skipped: ${spent:.3f} spent plus up to ${worst:.3f} for one more {spec.model} call would pass "
                    f"the ${self.cap:.2f} spend cap")
        return None


def spend_cap(job: Job, max_usd: float | None) -> SpendCap:
    """The cap for one plan or chat on ``job``: ``max_usd`` when given, else :data:`DEFAULT_MAX_USD` (or
    ``STUDIO_MAX_MODEL_USD`` when that is lower: a stricter global cap is honoured, a looser one meant for the
    Director's loop never loosens the tier's)."""
    if max_usd is not None:
        cap = max(0.0, float(max_usd))
    else:
        cap = DEFAULT_MAX_USD
        env = (os.environ.get("STUDIO_MAX_MODEL_USD") or "").strip()
        if env:
            with contextlib.suppress(ValueError):
                cap = min(cap, max(0.0, float(env)))
    base = 0.0
    try:
        base = _model_spend(job)
    except Exception:  # accounting never blocks a call; the cap then counts the whole job
        base = 0.0
    return SpendCap(job=job, cap=cap, base=base)


def _truncated(messages: list[Any]) -> bool:
    from pydantic_ai.messages import ModelResponse

    return any(isinstance(m, ModelResponse) and m.finish_reason == "length" for m in messages)


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
            # the answer was not a PlanDecision (no JSON, or JSON the schema rejects): spend the retry on it, unless
            # it was cut off at max_tokens (the retry would hit the same limit and pay twice)
            att.messages = list(captured)
            att.usage = _usage_of(att.messages[len(history):])
            att.truncated = _truncated(att.messages[len(history):])
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


def _run_model(spec: ModelSpec, job: Job, index: TakeIndex, digest: Digest, prefix: str,
               cap: SpendCap | None = None) -> list[_Attempt]:
    """The procedure on one model: a first call, then at most one retry carrying the rejection lines (unless the
    first answer was cut off at ``max_tokens`` or the retry's worst case would pass ``cap``)."""
    try:
        agent = _agent(spec, job, prefix)
    except Exception as e:  # a missing key or an unusable spec: nothing was called
        err = map_provider_error(e, spec)
        return [_Attempt(model=spec.model, error=err.message, error_kind=err.kind)]
    attempts = [_run_once(agent, spec, index, digest_prompt(digest.text), [])]
    first = attempts[0]
    if first.retryable and len(attempts) < MAX_ATTEMPTS:
        retry = retry_prompt(first.rejections)
        if first.truncated:
            first.skipped = f"retry skipped: the answer stopped at max_tokens ({MAX_TOKENS})"
        elif cap is not None:
            prompt_tokens = estimate_tokens(prefix) + estimate_tokens(digest.text) + estimate_tokens(retry) \
                + int(first.usage.get("output_tokens", 0))
            why = cap.another_call(spec, prompt_tokens)
            first.skipped = f"retry {why}" if why else None
        if first.skipped:
            trace_event(job, "planner_retry_skipped", stage="plan", model=spec.model, reason=first.skipped)
        else:
            trace_event(job, "planner_retry", stage="plan", model=spec.model, rejections=first.rejections)
            attempts.append(_run_once(agent, spec, index, retry, first.messages))
    return attempts


def _fallback_spec(primary: ModelSpec) -> ModelSpec | None:
    """The ``STUDIO_PLANNER_FALLBACK`` model on the primary's provider, key and settings; None when unset or the
    same model. Capabilities are re-derived for the new model name."""
    name = (os.environ.get(FALLBACK_ENV) or "").strip()
    if not name or name == primary.model:
        return None
    return ModelSpec(provider=primary.provider, model=name, api_key=primary.api_key, base_url=primary.base_url,
                     effort=primary.effort, byok=primary.byok, stream=primary.stream,
                     server_fallbacks=False, extra_settings=dict(primary.extra_settings))


def _pricier(fallback: ModelSpec, primary: ModelSpec) -> str | None:
    """Why ``fallback`` is refused on price (dearer per input or output token than the primary, or unpriced against
    a priced primary), unless ``STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER=1``; None when it may run."""
    from studio.agent.cost import _price

    if (os.environ.get(ALLOW_PRICIER_ENV) or "").strip().lower() in ("1", "true", "yes"):
        return None
    pp, fp = _price(primary.model), _price(fallback.model)
    if pp is None:
        return None  # the primary is unpriced (a creator's model): nothing to compare against
    if fp is None:
        return f"{fallback.model} has no list price to compare with {primary.model}'s ({ALLOW_PRICIER_ENV}=1 allows it)"
    if fp[0] > pp[0] or fp[1] > pp[1]:
        return (f"{fallback.model} (${fp[0]:g}/${fp[1]:g} per MTok) costs more than {primary.model} "
                f"(${pp[0]:g}/${pp[1]:g}) ({ALLOW_PRICIER_ENV}=1 allows it)")
    return None


def _note(attempts: list[_Attempt], *, fallback: ModelSpec | None, escalated: bool,
          fallback_refused: str | None = None) -> str:
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
    if last.skipped:
        why += f" ({last.skipped})"
    if escalated:
        return f"{why} (after {FALLBACK_ENV} escalation); pure-code plan used"
    tail = "; pure-code plan used"
    if fallback_refused:
        tail += f" ({FALLBACK_ENV} not tried: {fallback_refused})"
    elif fallback is None and last.error_kind != "missing_key":
        tail += f" (no {FALLBACK_ENV} set: no other model was tried)"
    return why + tail


def _slim_spec(spec: ModelSpec) -> dict[str, Any]:
    return {"provider": spec.provider, "model": spec.model, "effort": spec.effort, "byok": bool(spec.byok)}


def plan(job: Job, index: TakeIndex, digest: Digest, *, spec: ModelSpec | None = None,
         settings: Settings | None = None, brief: str | None = None, style: str | None = None,
         max_usd: float | None = None) -> PlanResult:
    """Ask the planner model for the take's :class:`PlanDecision` (see the module docstring for the procedure).

    ``spec`` is the planner model (BYOK or house); None means :func:`house_spec` ``"planner"`` from ``settings``.
    ``brief`` and ``style`` go into the cached prefix as hints. ``max_usd`` caps this plan's model spend
    (:func:`spend_cap`). The result's ``decision`` is None whenever the model did not produce a valid decision within
    one retry; the caller then uses the pure-code plan. Always writes ``logs/plan.json`` and a ``plan`` trace event;
    never raises for a model failure.
    """
    s = settings if settings is not None else get_settings()
    primary = spec if spec is not None else house_spec("planner", settings=s)
    prefix = build_prefix(brief=brief, style=style)
    cap = spend_cap(job, max_usd)
    blocked = cap.first_call()
    if blocked:
        attempts = [_Attempt(model=primary.model, error=blocked, error_kind="spend_cap")]
        trace_event(job, "planner_skipped", stage="plan", model=primary.model, reason=blocked)
    else:
        attempts = _run_model(primary, job, index, digest, prefix, cap)
    fallback = _fallback_spec(primary)
    escalated = False
    fallback_refused: str | None = None
    if attempts[-1].decision is None and fallback is not None and attempts[-1].error_kind not in ("missing_key",
                                                                                                  "spend_cap"):
        fallback_refused = _pricier(fallback, primary) or cap.another_call(
            fallback, estimate_tokens(prefix) + estimate_tokens(digest.text))
        if fallback_refused:
            trace_event(job, "planner_fallback_refused", stage="plan", from_model=primary.model,
                        to_model=fallback.model, reason=fallback_refused, env=FALLBACK_ENV)
        else:
            escalated = True
            trace_event(job, "planner_fallback", stage="plan", from_model=primary.model, to_model=fallback.model,
                        reason=attempts[-1].error or "rejected after retry", env=FALLBACK_ENV)
            attempts.extend(_run_model(fallback, job, index, digest, prefix, cap))
    last = attempts[-1]
    note = _note(attempts, fallback=fallback, escalated=escalated, fallback_refused=fallback_refused)
    usage = _usage_summary(attempts)
    result = PlanResult(decision=last.decision, attempts=sum(1 for a in attempts if a.called),
                        rejected=list(attempts[0].rejections), usage=usage, note=note)
    record = {
        "decision": last.decision.model_dump(mode="json") if last.decision is not None else None,
        "attempts": result.attempts, "rejected": result.rejected,
        "rejections_by_attempt": [a.rejections for a in attempts],
        "errors": [{"model": a.model, "kind": a.error_kind, "message": a.error} for a in attempts if a.error],
        "usage": usage, "note": note, "max_usd": round(cap.cap, 4),
        "skipped": [a.skipped for a in attempts if a.skipped],
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
