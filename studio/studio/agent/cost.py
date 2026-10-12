"""Estimated API spend of a job, from ``trace.jsonl`` ``model_call`` and ``asr`` events and provider list prices.

Every model call the engine makes is traced with its token usage (input, output, cache read, cache write). This
module prices those at Anthropic's public list prices (USD per million tokens, as of :data:`PRICES_AS_OF`), with cache
writes at the engine's cache TTL (:data:`studio.agent.providers.CACHE_TTL`: 1h writes cost 2x the input price, 5m
writes 1.25x). Other providers' models are counted but reported as unpriced.

Every speech-to-text call (the Take Index pass, the fill excerpts, the QA round trips) is traced as an ``asr`` event
with its ``audio_seconds`` (see :mod:`studio.perception.transcribe`, *Accounting*). Those are priced by the audio hour
(:data:`ASR_PRICES_PER_HOUR`: ElevenLabs Scribe $0.22, AssemblyAI $0.15) under the role ``asr`` with the call's
purpose as its stage (``asr:index`` | ``asr:fill`` | ``asr:qa``); a cached re-parse costs nothing. ASR is a few
tenths of a cent per video, which is exactly why the standard tier can afford to measure it rather than guess: the
providers bill by the started minute in some plans, so the exact-seconds figure here is the floor, not the invoice.

The figure is an estimate for the report and the CLI summary, not a bill: check the provider console for the invoice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from studio.agent.providers import CACHE_TTL

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job

__all__ = ["PRICES", "PRICES_AS_OF", "ASR_PRICES_PER_HOUR", "CostSummary", "call_cost", "asr_cost", "job_cost",
           "format_cost", "format_usd"]

PRICES_AS_OF = "2026-10-09"

#: USD per hour of audio transcribed, by ASR provider (as of :data:`PRICES_AS_OF`).
ASR_PRICES_PER_HOUR: dict[str, float] = {
    "elevenlabs": 0.22,  # Scribe v2 (elevenlabs.io/pricing/api)
    "assemblyai": 0.15,  # Universal (assemblyai.com/pricing)
}
#: ASR models whose provider an ``asr`` event may leave out (older traces, hand-written events).
_ASR_PROVIDER_OF_MODEL: dict[str, str] = {
    "scribe_v2": "elevenlabs", "scribe_v1": "elevenlabs",
    "universal-3-5-pro": "assemblyai", "universal-2": "assemblyai", "universal": "assemblyai",
}

#: USD per million tokens: (input, output, cache read). Cache writes derive from the input price by TTL.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}

_WRITE_MULTIPLIER = {"5m": 1.25, "1h": 2.0}

#: Models priced higher once the prompt exceeds a size: model -> (prompt tokens, input, output, cache read).
LONG_PROMPT_PRICES: dict[str, tuple[int, float, float, float]] = {
    "claude-haiku-5-5": (100_000, 0.50, 2.50, 0.05),
}


def _price(model: str | None) -> tuple[float, float, float] | None:
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    base = model.rsplit("-", 1)[0]  # a dated snapshot id ("claude-opus-4-6-20260101")
    return PRICES.get(base)


def call_cost(event: dict[str, Any], *, ttl: str = CACHE_TTL) -> float | None:
    """USD for one ``model_call`` trace event, or None when its model is unpriced or the call carried no usage."""
    model = str(event.get("model_resolved") or event.get("model") or "")
    p = _price(model)
    if p is None:
        return None
    try:
        inp = int(event.get("input_tokens") or 0)
        out = int(event.get("output_tokens") or 0)
        cr = int(event.get("cache_read_tokens") or 0)
        cw = int(event.get("cache_write_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if not (inp or out):
        return None
    tier = LONG_PROMPT_PRICES.get(model) or LONG_PROMPT_PRICES.get(model.rsplit("-", 1)[0])
    if tier is not None and inp > tier[0]:
        p = tier[1:]
    uncached = max(0, inp - cr - cw)
    write = p[0] * _WRITE_MULTIPLIER.get(str(event.get("cache_ttl") or ttl), 2.0)
    return (uncached * p[0] + out * p[1] + cr * p[2] + cw * write) / 1e6


def _asr_provider(event: dict[str, Any]) -> str | None:
    p = str(event.get("provider") or "").strip().lower()
    if p in ASR_PRICES_PER_HOUR:
        return p
    return _ASR_PROVIDER_OF_MODEL.get(str(event.get("model") or "").strip().lower())


def asr_cost(event: dict[str, Any]) -> float | None:
    """USD for one ``asr`` trace event: ``audio_seconds`` at the provider's hourly price; ``0.0`` for a cached
    re-parse; None when the call failed (nothing transcribed), carried no audio length or names an unpriced
    provider."""
    if event.get("status") == "failed":
        return None
    p = _asr_provider(event)
    if p is None:
        return None
    if event.get("cached"):
        return 0.0
    try:
        seconds = float(event.get("audio_seconds"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds / 3600.0 * ASR_PRICES_PER_HOUR[p]


@dataclass
class CostSummary:
    total_usd: float = 0.0
    calls: int = 0  # model calls
    priced_calls: int = 0
    by_role: dict[str, float] = field(default_factory=dict)  # model roles, plus "asr" for speech-to-text
    by_stage: dict[str, float] = field(default_factory=dict)  # "role:stage"; ASR: "asr:<purpose>"
    by_model: dict[str, float] = field(default_factory=dict)
    unpriced_models: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)  # uncached_input, output, cache_read, cache_write
    asr_calls: int = 0  # successful ASR calls, cached re-parses included
    asr_cached_calls: int = 0
    asr_seconds: float = 0.0  # audio seconds actually sent to a provider (cached re-parses excluded)

    def to_dict(self) -> dict[str, Any]:
        return {"total_usd": round(self.total_usd, 2), "calls": self.calls, "priced_calls": self.priced_calls,
                "by_role": {k: round(v, 2) for k, v in self.by_role.items()},
                "by_stage": {k: round(v, 2) for k, v in self.by_stage.items()},
                "by_model": {k: round(v, 2) for k, v in self.by_model.items()},
                "unpriced_models": list(self.unpriced_models), "tokens": dict(self.tokens),
                "asr_calls": self.asr_calls, "asr_cached_calls": self.asr_cached_calls,
                "asr_seconds": round(self.asr_seconds, 1), "asr_usd": round(self.by_role.get("asr", 0.0), 4),
                "prices_as_of": PRICES_AS_OF, "cache_ttl": CACHE_TTL}


def job_cost(job: Job | list[dict[str, Any]], *, ttl: str = CACHE_TTL) -> CostSummary:
    """Sum :func:`call_cost` over the ``model_call`` events and :func:`asr_cost` over the ``asr`` events of a
    job's trace (or a list of trace events)."""
    events = job if isinstance(job, list) else job.read_trace()
    cs = CostSummary()
    role: dict[str, float] = defaultdict(float)
    stage: dict[str, float] = defaultdict(float)
    model: dict[str, float] = defaultdict(float)
    tokens: dict[str, int] = defaultdict(int)
    unpriced: set[str] = set()
    for e in events:
        if e.get("event") == "asr":
            if e.get("status") == "failed":
                continue  # a 5xx, a missing key: nothing was transcribed, nothing is billed
            cs.asr_calls += 1
            m = str(e.get("model") or e.get("provider") or "?")
            c = asr_cost(e)
            if c is None:
                unpriced.add(m)
                continue
            if e.get("cached"):
                cs.asr_cached_calls += 1
            else:
                cs.asr_seconds += float(e.get("audio_seconds") or 0.0)
            cs.total_usd += c
            role["asr"] += c
            stage[f"asr:{e.get('purpose') or '?'}"] += c
            model[m] += c
            continue
        if e.get("event") != "model_call":
            continue
        cs.calls += 1
        m = str(e.get("model_resolved") or e.get("model") or "?")
        c = call_cost(e, ttl=ttl)
        if c is None:
            if _price(m) is None:
                unpriced.add(m)
            continue
        cs.priced_calls += 1
        cs.total_usd += c
        role[str(e.get("role") or "?")] += c
        stage[f"{e.get('role') or '?'}:{str(e.get('stage') or '?').split(':')[0]}"] += c
        model[m] += c
        inp, cr, cw = (int(e.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"))
        tokens["uncached_input"] += max(0, inp - cr - cw)
        tokens["output"] += int(e.get("output_tokens") or 0)
        tokens["cache_read"] += cr
        tokens["cache_write"] += cw
    cs.by_role = dict(sorted(role.items(), key=lambda kv: -kv[1]))
    cs.by_stage = dict(sorted(stage.items(), key=lambda kv: -kv[1]))
    cs.by_model = dict(sorted(model.items(), key=lambda kv: -kv[1]))
    cs.unpriced_models = sorted(unpriced)
    cs.tokens = dict(tokens)
    return cs


def format_usd(v: float) -> str:
    """``$0.03`` for cents and above; ASR lines are tenths of a cent, so below one cent the figure keeps three
    (then four) decimals rather than rounding a real charge to ``$0.00``."""
    if v >= 0.01 or v == 0:
        return f"${v:.2f}"
    return f"${v:.3f}" if v >= 0.001 else f"${v:.4f}"


def format_cost(cs: CostSummary) -> str:
    """One line for the CLI summary."""
    if not cs.priced_calls and not cs.asr_calls:
        return "no priced model calls"
    roles = ", ".join(f"asr {format_usd(v)} ({cs.asr_seconds:.0f} s audio)" if r == "asr" else f"{r} ${v:.2f}"
                      for r, v in cs.by_role.items())
    calls = f"{cs.priced_calls} calls" + (f" and {cs.asr_calls} ASR calls" if cs.asr_calls else "")
    note = f"; unpriced: {', '.join(cs.unpriced_models)}" if cs.unpriced_models else ""
    return f"${cs.total_usd:.2f} over {calls} ({roles}; list prices {PRICES_AS_OF}{note})"
