"""LOOP G — Golden Parity gate for the Palo -> Yunicorn port.

Keyless tier (default, CI-safe): replays deterministic golden fixtures recorded from
Palo behaviours being ported and asserts parity through the new FastAPI code — the
pure-function IP where Palo shipped bugs (strategy section slicing, cache-breakpoint
split, cost math, the compile kill-switch, doctrine assembly, and — as later phases
land — memory reconcile decisions, spitfire parsing, milestone/watermark math).

    cd backend && python -m eval.port_eval            # keyless parity gate
    ANTHROPIC_API_KEY=... python -m eval.port_eval --live   # + prompt-intent judge

Exit codes match scripts/gate.sh: 0 pass, 1 a golden regressed, 2 --live requested
without a key (never silently downgrades). Grows one CHECKS block per phase.
"""
from __future__ import annotations

import os
import sys

from app import ai_usage, doctrine, palo_llm, prompt_assembly, tiers

_STRATEGY_MD = "## Insights\nname the viewer\n## Plan\nREGIME: breakout\nLEVER: extend territory\n"


def _checks() -> list[tuple[str, bool]]:
    """(label, passed) golden invariants. Add a block per phase as modules land."""
    out: list[tuple[str, bool]] = []

    # Phase 0 — shared infra parity
    out.append(("tier.matrix", tiers.entitlements("studio")["video_brain"] is True
                and tiers.metrics_sources("starter") == ("apify",)))
    out.append(("strategy.slice", "name the viewer" in
                prompt_assembly.replace_strategy_sections("{STRATEGY_INSIGHTS}", _STRATEGY_MD)))
    out.append(("regime.parse", prompt_assembly.infer_craft_regime(_STRATEGY_MD).startswith("breakout")))
    out.append(("doctrine.core", bool(doctrine.doctrine_block("core"))))
    blocks = palo_llm.build_system("PRE" + palo_llm.CACHE_BREAKPOINT + "POST")
    out.append(("cache.split", isinstance(blocks, list)
                and blocks[0].get("cache_control") == {"type": "ephemeral"}))
    out.append(("cost.opus_in", ai_usage.estimate_cost("claude-opus-4-8", 1_000_000, 0) == 15.0))
    out.append(("gate.default_off", ai_usage.compile_allowed("x", is_paying=True) is False))

    # Phase 1+ blocks appended here as memory/ideas/insights/strategy/write land.
    return out


def main() -> int:
    live = "--live" in sys.argv
    if live and not os.environ.get("ANTHROPIC_API_KEY"):
        print("[port_eval] --live requested but ANTHROPIC_API_KEY unset", file=sys.stderr)
        return 2

    # Keyless allowlist must be empty for the default-off golden — don't let a dev env
    # with STRATEGY_ALLOWLIST=* flip the gate.default_off check.
    os.environ.pop("STRATEGY_ALLOWLIST", None)

    checks = _checks()
    failed = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(f"[port_eval] {'PASS' if ok else 'FAIL'} {label}")
    print(f"[port_eval] {len(checks) - len(failed)}/{len(checks)} golden checks passed")
    if failed:
        print(f"[port_eval] REGRESSION: {', '.join(failed)}", file=sys.stderr)
        return 1
    if live:
        print("[port_eval] --live prompt-intent judge: no ported prompts yet (Phase 1+)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
