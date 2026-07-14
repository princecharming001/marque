"""Phase 4 box 2 — strategy compiler: split/validate, downstream-usable template,
gates, revision UPSERT. Keyless."""
from __future__ import annotations

import asyncio

import pytest

from app import palo_flags, prompt_assembly
from app import strategy_compiler as sc


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, prev=None):
        self.prev = prev
        self.upserted = None

    async def load_prompt_override(self, key):
        return None

    async def load_strategy(self, cid):
        return self.prev

    async def upsert_strategy(self, cid, fields):
        self.upserted = fields
        return True

    async def record_ai_usage(self, row):
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "STRATEGY_COMPILER", True)


# --- section splitter + validation --------------------------------------------

def test_split_and_validate():
    md = sc._template_strategy({"niche": "chess"})
    sections = sc.split_sections(md)
    assert set(sc._REQUIRED).issubset(sections)
    assert sc.validate_sections(md) is True
    assert sc.validate_sections("## Insights\nonly one section") is False


def test_template_is_downstream_usable():
    md = sc._template_strategy({"niche": "chess"})
    filled = prompt_assembly.replace_strategy_sections("{STRATEGY_INSIGHTS} | {STRATEGY_DIRECTIVE}", md)
    assert "{STRATEGY" not in filled and "chess" in filled
    assert prompt_assembly.infer_craft_regime(md).startswith("sub-breakout")


# --- gates --------------------------------------------------------------------

def test_flag_off_returns_none():
    assert _run(sc.compile_strategy(FakeStore(), "c1", [], {"niche": "chess"})) is None


def test_allowlist_gate_blocks_by_default(on, monkeypatch):
    monkeypatch.delenv("STRATEGY_ALLOWLIST", raising=False)   # empty allowlist -> nobody compiles
    assert _run(sc.compile_strategy(FakeStore(), "c1", [], {"niche": "chess"})) is None


# --- full compile persists with a bumped revision -----------------------------

def test_compile_persists_and_bumps_revision(on, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "*")
    store = FakeStore(prev={"strategy_revision": 2})
    md = _run(sc.compile_strategy(store, "c1", [{"title": "v", "views": 100}], {"niche": "chess"}))
    assert md and sc.validate_sections(md)                    # keyless -> template, still valid
    assert store.upserted["strategy_revision"] == 3           # 2 -> 3
    assert store.upserted["strategy_markdown"] == md


def test_compile_first_revision(on, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "c1")
    store = FakeStore(prev=None)
    _run(sc.compile_strategy(store, "c1", [], {"niche": "chess"}))
    assert store.upserted["strategy_revision"] == 1


def test_compile_empty_evidence_skips_llm(on, monkeypatch):
    # No analyzed videos -> deterministic template, NO Sonnet/Opus spend.
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "*")

    async def boom(*a, **k):
        raise AssertionError("must not call digest/synthesize on empty evidence")
    monkeypatch.setattr(sc, "digest", boom)
    monkeypatch.setattr(sc, "synthesize", boom)
    store = FakeStore(prev=None)
    md = _run(sc.compile_strategy(store, "c1", [], {"niche": "chess"}))
    assert md and sc.validate_sections(md) and store.upserted["strategy_revision"] == 1
