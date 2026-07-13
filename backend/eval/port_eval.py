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

    # Phase 1 — memory + ledger parity
    from app import memory_v2, recall_ledger
    _ex = [{"id": "m1", "scope": "user", "type": "content_context", "key": "loc",
            "value": "London", "confidence": 0.8}]
    _ops = memory_v2.reconcile(_ex, [
        {"scope": "user", "type": "content_context", "key": "loc", "value": "Berlin", "confidence": 0.9},
        {"scope": "user", "type": "creative_preference", "key": "no_emoji", "value": "no emojis", "confidence": 1.0}])
    out.append(("memory.reconcile", [o["op"] for o in _ops] == ["update", "add"]))
    out.append(("memory.drop_insight", "insight" in memory_v2._DROP_TYPES
                and "conversation_insight" in memory_v2._DROP_TYPES))
    out.append(("memory.rank", memory_v2._rank(
        [{"value": "lo", "similarity": 0.1, "confidence": 0.7},
         {"value": "hi", "similarity": 0.95, "confidence": 0.9}])[0]["value"] == "hi"))
    out.append(("ledger.ulid", len(recall_ledger.new_ulid()) == 26))

    # Phase 2 — idea bank parity (deterministic, no LLM)
    from app import ideas
    _mi = ideas.mock_ideas({"niche": "chess"})
    out.append(("ideas.mock", len(_mi) == 3 and "chess" in _mi[0]["title"].lower()))
    _bf = ideas.to_briefs("c1", _mi)
    out.append(("ideas.briefs", len(_bf) == 3 and _bf[0]["score"] > _bf[2]["score"]
                and len({b["id"] for b in _bf}) == 3))
    _p = ideas.parse_thinking_output("<OPEN>\nTITLE: X\nSUMMARY: s\nBEGINNING: b\nMIDDLE: m\nEND: e\n<CLOSE>")
    out.append(("ideas.parse", _p is not None and _p["title"] == "X" and _p["ending"] == "e"))
    out.append(("ideas.rank", ideas._parse_ranking("[3] > [1] > [2]", 3) == [2, 0, 1]))
    from app import tiers as _t
    out.append(("ideas.cadence", ideas.is_ideate_due(_t.STUDIO, 0, 1e7) is True
                and ideas.is_ideate_due(_t.STARTER, 1e7 - 3 * 86400, 1e7) is False))
    _mg = ideas.merge_briefs_into_feed([{"id": "s1"}, {"id": "b2"}],
                                       [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}], max_briefs=2)
    out.append(("ideas.feedmerge", [m["id"] for m in _mg] == ["b1", "b3", "s1", "b2"]))

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
