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

    # Phase 3 — metric pollers (deterministic row shaping + tier chain order)
    from app import metrics_pollers as _mp
    _r = _mp.poll_apify("c1", "h", captured_at="T") if False else _mp._rows(
        "c1", "p1", {"views": 100, "likes": None}, "apify", "T")
    out.append(("metrics.rows", len(_r) == 1 and _r[0]["metric"] == "views"
                and _r[0]["source"] == "apify" and _r[0]["value"] == 100.0))
    out.append(("metrics.chain", _t.metrics_sources(_t.STUDIO) == ("ig_graph", "postforme", "apify")))
    from app import track_insights as _ti
    out.append(("insight.milestones", _ti.crossed_milestones(9000, 60000, _ti.VIEW_MILESTONES) == [10000, 25000, 50000]))
    out.append(("insight.spike", _ti.detect_spike(30, [10, 10, 10]) is True
                and _ti.detect_spike(20, [10, 10, 10]) is False))
    out.append(("insight.underperformer", _ti.is_underperformer(50, 1000) is True
                and _ti.is_underperformer(500, 1000) is False))
    _e = {"type": "view_milestone", "value": 100000}
    out.append(("insight.dedup", _ti._dedup_hash("c1", _e) == _ti._dedup_hash("c1", _e)
                and _ti._dedup_hash("c1", _e) != _ti._dedup_hash("c2", _e)))
    out.append(("insight.card", "100,000 views" in _ti._template_card(_e)["title"]))
    _rows = [{"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 100},
             {"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 900}]
    out.append(("insight.snapshot", _ti._snapshot_from_metrics(_rows)["videos"][0]["history"] == [100]))
    out.append(("insight.settle", _ti.settle_candidates(
        [{"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 1000}], 500) == [("p1", 1.0)]))

    # Phase 4 — dossier adapter (RISK #1)
    from app import dossier_adapter as _da
    _blk = _da.dossier_to_analysis_block({"title": "T", "views": 1000,
        "dossier": {"first_frame": {"desc": "hook", "pattern_interrupt": True}}})
    out.append(("strategy.adapter", "T (1,000 views)" in _blk and "pattern interrupt" in _blk))
    _vids = _da.videos_from_clip_sessions(
        [{"job_id": "j1", "script": {"title": "V"}, "words": [{"word": "hi"}], "dossier": {"x": 1}},
         {"job_id": "j2", "script": {"title": "empty"}}], views_by_id={"j1": 42})
    out.append(("strategy.evidence", len(_vids) == 1 and _vids[0]["title"] == "V" and _vids[0]["views"] == 42))
    from app import strategy_compiler as _sc
    _sm = _sc._template_strategy({"niche": "chess"})
    out.append(("strategy.sections", _sc.validate_sections(_sm) is True
                and _sc.validate_sections("## Insights\nx") is False))
    out.append(("strategy.regime", prompt_assembly.infer_craft_regime(_sm).startswith("sub-breakout")))
    out.append(("strategy.freshness", _sc.is_compile_due(_t.GROWTH, None, 1e7) is True
                and _sc.is_compile_due(_t.STUDIO, _sc._now_iso(), 0) is False))

    # Phase 5 — write agent action parsing
    from app import write_agent as _wa
    _acts = _wa.parse_write_actions("<edit><old>a</old><new>b</new></edit><answer>hi</answer>")
    out.append(("write.parse", [a["op"] for a in _acts] == ["edit", "answer"]
                and _acts[0]["old"] == "a" and _acts[0]["new"] == "b"))
    _nb, _oc = _wa.apply_actions("hello world", [{"op": "edit", "old": "hello", "new": "hey"}])
    out.append(("write.apply", _nb == "hey world" and _oc[0]["applied"] is True))
    _skip = _wa.apply_actions("hello world", [{"op": "edit", "old": "nope", "new": "x"}])
    out.append(("write.apply_skip", _skip[0] == "hello world" and _skip[1][0]["applied"] is False))
    _ms = _wa._mock_script_from_brief({"title": "T", "beginning": "a", "ending": "b"})
    out.append(("write.from_brief", _ms["title"] == "T" and _ms["body"] == "a\nb"))
    out.append(("write.leak_firewall", "leaked" in " ".join(
        _wa.check_invariants("", [{"op": "fill", "content": "REGIME: x"}]))))

    # Phase 6 — exemplar bank retrieval (lift-ordered index)
    from app import exemplar as _ex
    _flat = _ex._flatten({"hook": [{"id": "h1", "mechanism": "m1", "lift": 2.0},
                                   {"id": "h2", "mechanism": "m2", "lift": 3.0}]})
    out.append(("exemplar.order", [p["id"] for p in _flat] == ["h2", "h1"]))
    out.append(("exemplar.render", "[hook:h2] lift 3.0" in _ex.render_index(_flat)))
    out.append(("exemplar.build_template", _ex._valid_bank(_ex._template_bank({"niche": "x"})) is True
                and _ex._valid_bank({}) is False))

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
