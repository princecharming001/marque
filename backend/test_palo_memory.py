"""Phase 1 (memory + ledger) — keyless tests.

Pure functions run offline; the extract→reconcile→store path uses a fake in-memory
store + a monkeypatched LLM, proving: insights are dropped in CODE, reconcile does
ADD/UPDATE/NOOP correctly, scope filters, ranking weights, and every path is
flag-gated + keyless-green.
"""
from __future__ import annotations

import asyncio

import pytest

from app import memory_v2, palo_flags, recall_ledger


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, memories=None, ledger=None):
        self._mem = list(memories or [])
        self._ledger = list(ledger or [])
        self.upserts: list[dict] = []
        self.appended: list[dict] = []

    async def load_prompt_override(self, key):
        return None

    async def load_memories(self, creator_id, scope=""):
        return [m for m in self._mem if not scope or m.get("scope") == scope]

    async def upsert_memory(self, row):
        self.upserts.append(row)
        return True

    async def match_memories(self, creator_id, embedding, scope="", limit=8):
        return []

    async def record_ai_usage(self, row):
        return True

    async def append_ledger(self, creator_id, entries):
        self.appended.extend(entries)
        return True

    async def load_ledger(self, creator_id, limit=200):
        return self._ledger


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "MEMORY_V2", True)


# --- cue gate + drop-in-code ---------------------------------------------------

def test_should_extract_cue_gate():
    assert memory_v2._should_extract("Remember that my name is Ada") is True
    assert memory_v2._should_extract("i prefer no emojis") is True
    assert memory_v2._should_extract("give me some ideas") is False
    assert memory_v2._should_extract("") is False


def test_extract_drops_insights_in_code(on, monkeypatch):
    async def fake_llm(system, user, schema, model, max_tokens=0, temperature=None):
        return {"memories": [
            {"type": "content_context", "key": "name", "value": "Name is Ada",
             "confidence": 1.0, "scope": "user"},
            {"type": "conversation_insight", "key": "x", "value": "trampoline videos win",
             "confidence": 0.9, "scope": "channel"},   # must be dropped in code
        ]}
    monkeypatch.setattr(memory_v2, "anthropic_cached_json", fake_llm)
    store = FakeStore()
    applied = _run(memory_v2.remember(store, "c1", "Remember my name is Ada", "ok"))
    assert applied == 1
    assert store.upserts[0]["value"] == "Name is Ada"
    assert all("trampoline" not in u.get("value", "") for u in store.upserts)


# --- reconcile ADD / UPDATE / NOOP --------------------------------------------

def test_reconcile_add_update_noop():
    existing = [{"id": "m1", "scope": "user", "type": "content_context",
                 "key": "loc", "value": "London", "confidence": 0.8}]
    cands = [
        {"scope": "user", "type": "content_context", "key": "loc",
         "value": "Berlin", "confidence": 0.9},                       # UPDATE (changed)
        {"scope": "user", "type": "content_context", "key": "loc",
         "value": "London", "confidence": 0.8},                       # NOOP (same)
        {"scope": "user", "type": "creative_preference", "key": "no_emoji",
         "value": "no emojis", "confidence": 1.0},                    # ADD (new key)
    ]
    ops = memory_v2.reconcile(existing, cands)
    kinds = [o["op"] for o in ops]
    assert kinds.count("update") == 1 and kinds.count("add") == 1 and len(ops) == 2
    upd = next(o for o in ops if o["op"] == "update")
    assert upd["row"]["id"] == "m1" and upd["row"]["value"] == "Berlin"


def test_rank_weights_similarity_and_confidence():
    mems = [
        {"value": "low", "similarity": 0.1, "confidence": 0.7},
        {"value": "high", "similarity": 0.95, "confidence": 0.9},
    ]
    ranked = memory_v2._rank(mems)
    assert ranked[0]["value"] == "high"


def test_memory_block_render():
    assert memory_v2.memory_block([]) == ""
    block = memory_v2.memory_block([{"value": "Name is Ada"}])
    assert "<memory>" in block and "Name is Ada" in block


# --- ledger --------------------------------------------------------------------

def test_ulid_shape_and_unique():
    a, b = recall_ledger.new_ulid(), recall_ledger.new_ulid()
    assert len(a) == 26 and a != b
    assert all(ch in recall_ledger._CROCKFORD for ch in a)


def test_ledger_block_render(on):
    store = FakeStore(ledger=[{"kind": "idea", "summary": "Reframe X as meeting future self"}])
    block = _run(recall_ledger.ledger_block(store, "c1"))
    assert "prior_recommendations" in block and "future self" in block


# --- flag / keyless guards -----------------------------------------------------

def test_flag_off_is_noop():
    # default flags OFF -> everything short-circuits, no store touched
    store = FakeStore()
    assert _run(memory_v2.remember(store, "c1", "remember my name is Ada", "ok")) == 0
    assert _run(memory_v2.retrieve(store, "c1", "who am i")) == []
    assert _run(recall_ledger.record(store, "c1", "u", "a")) == 0


def test_no_store_is_noop(on):
    assert _run(memory_v2.remember(None, "c1", "remember x", "a")) == 0
    assert _run(memory_v2.retrieve(None, "c1", "q")) == []
    assert _run(recall_ledger.ledger_block(None, "c1")) == ""
