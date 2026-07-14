"""Phase 0 (Palo port) — keyless foundation tests.

Proves the shared seams every later phase depends on work offline with no keys and no
DB: feature flags default OFF, the tier entitlement matrix, the prompt override store's
code-fallback, strategy/doctrine assembly, the cache-breakpoint splitter, cost
accounting + the compile kill-switch, and that the Palo store is keyless-green.
"""
from __future__ import annotations

import asyncio

from app import ai_usage, doctrine, palo_flags, palo_llm, prompt_assembly, prompt_store, tiers
from app.palo_persistence import PaloStore, make_store


def _run(coro):
    return asyncio.run(coro)


# --- flags default OFF (whole port ships dark) --------------------------------

def test_flags_default_off():
    assert palo_flags.PALO_PORT is False
    assert palo_flags.enabled(True) is False          # PALO_PORT gate dominates
    assert palo_flags.enabled(palo_flags.MEMORY_V2) is False


def test_real_creator_blocks_shared_bucket():
    assert palo_flags.real_creator("creator-123") is True
    assert palo_flags.real_creator("default") is False    # unauthed shared bucket
    assert palo_flags.real_creator("demo") is False
    assert palo_flags.real_creator("") is False


# --- tier entitlement matrix --------------------------------------------------

def test_tier_matrix():
    assert tiers.normalize("nonsense") == tiers.DEFAULT_TIER
    assert tiers.entitlements(tiers.STUDIO)["video_brain"] is True
    assert tiers.entitlements(tiers.GROWTH)["video_brain"] is False
    assert tiers.has_feature(tiers.STUDIO, "exemplar_bank") is True
    assert tiers.has_feature(tiers.STARTER, "exemplar_bank") is False
    assert tiers.at_least(tiers.GROWTH, tiers.STARTER) is True
    assert tiers.at_least(tiers.STARTER, tiers.STUDIO) is False
    assert tiers.cadence(tiers.STUDIO, "compile") == "weekly"
    assert tiers.metrics_sources(tiers.STUDIO) == ("ig_graph", "postforme", "apify")
    assert tiers.metrics_sources(tiers.STARTER) == ("apify",)
    assert tiers.runs_per_month("weekly") == 4.3
    assert tiers.runs_per_month("off") == 0.0


def test_tier_for_no_store_is_default():
    assert _run(tiers.tier_for("creator-1", store=None)) == tiers.DEFAULT_TIER


# --- prompt override store falls back to code --------------------------------

def test_prompt_store_falls_back_to_code():
    prompt_store.clear_cache()
    got = _run(prompt_store.get_prompt("write.system", "CODE DEFAULT", store=None))
    assert got == "CODE DEFAULT"


# --- strategy + doctrine assembly --------------------------------------------

_STRATEGY_MD = """## Insights
Hooks that name the viewer outperform.
## Plan
REGIME: sub-breakout
LEVER: escape the 2k view band by widening the hook
## Buckets
- day in the life
"""


def test_strategy_section_slicing():
    tmpl = "A {STRATEGY_INSIGHTS} B {STRATEGY_DIRECTIVE} C {STRATEGY_NOT_DOING}"
    out = prompt_assembly.replace_strategy_sections(tmpl, _STRATEGY_MD)
    assert "{STRATEGY_INSIGHTS}" not in out and "{STRATEGY_DIRECTIVE}" not in out
    assert "name the viewer" in out
    assert "not available" in out                      # Not-Doing missing -> marker


def test_infer_craft_regime():
    assert prompt_assembly.infer_craft_regime(_STRATEGY_MD).startswith("sub-breakout")
    assert "escape the 2k" in prompt_assembly.infer_craft_regime(_STRATEGY_MD)
    assert prompt_assembly.infer_craft_regime("") == "unknown"


def test_doctrine_blocks_render():
    doctrine.doctrine_block.cache_clear()
    core = doctrine.doctrine_block("core")
    assert core and "voice" in core.lower()            # worldview/guards mention voice
    assert doctrine.doctrine_block("nonexistent") == ""
    filled = prompt_assembly.replace_doctrine_blocks("X {DOCTRINE_CORE} Y")
    assert "{DOCTRINE_CORE}" not in filled


# --- cache-breakpoint splitter -----------------------------------------------

def test_cache_breakpoint_split():
    plain = palo_llm.build_system("no marker here")
    assert plain == "no marker here"
    blocks = palo_llm.build_system("STATIC PREFIX" + palo_llm.CACHE_BREAKPOINT + "dynamic tail")
    assert isinstance(blocks, list) and len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "STATIC PREFIX"
    assert blocks[1]["text"] == "dynamic tail"


def test_anthropic_cached_keyless_returns_none():
    assert _run(palo_llm.anthropic_cached("s", "u", "claude-sonnet-4-6")) is None


# --- cost accounting + compile kill-switch -----------------------------------

def test_estimate_cost():
    assert ai_usage.estimate_cost("claude-opus-4-8", 1_000_000, 0) == 15.0
    assert ai_usage.estimate_cost("claude-opus-4-8", 0, 1_000_000) == 75.0
    assert ai_usage.estimate_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_compile_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("STRATEGY_ALLOWLIST", raising=False)
    assert ai_usage.compile_allowed("c1", is_paying=True) is False       # empty allowlist
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "c1,c2")
    assert ai_usage.compile_allowed("c1", is_paying=True) is True
    assert ai_usage.compile_allowed("c1", is_paying=False) is False      # paying gate
    assert ai_usage.compile_allowed("c9", is_paying=True) is False
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "*")
    assert ai_usage.compile_allowed("anyone", is_paying=True) is True


def test_record_usage_no_store_returns_cost():
    cost = _run(ai_usage.record(None, "c1", "compile", "claude-opus-4-8", 1000, 1000))
    assert cost == ai_usage.estimate_cost("claude-opus-4-8", 1000, 1000)


# --- Palo store is keyless-green ---------------------------------------------

def test_store_keyless_is_none():
    assert make_store("", "") is None


def test_disabled_store_methods_are_falsy():
    store = PaloStore("", "")            # not enabled (no key)
    assert store.enabled is False
    assert _run(store.load_memories("c1")) == []
    assert _run(store.load_strategy("c1")) is None
    assert _run(store.load_briefs("c1")) == []
    assert _run(store.load_creator_tier("c1")) is None
