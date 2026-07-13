"""Phase 2 (idea bank) — keyless tests: generation fallback, eval gate filters,
briefs shape, flag gating, and the full pipeline via a fake store + fake LLM."""
from __future__ import annotations

import asyncio

from app import ideas, palo_flags

BRAND = {"niche": "chess", "known_for": "speedruns", "audience": "beginners"}


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.upserts = []

    async def load_prompt_override(self, key):
        return None

    async def upsert_brief(self, b):
        self.upserts.append(b)
        return True

    async def record_ai_usage(self, row):
        return True


def test_mock_ideas_niche_specific():
    out = ideas.mock_ideas(BRAND)
    assert len(out) == 3 and all("chess" in i["title"].lower() for i in out)


def test_generate_keyless_returns_mock():
    out = _run(ideas.generate_ideas(None, BRAND))     # no key -> None -> mock
    assert len(out) == 3 and out[0]["title"]


def test_eval_keyless_all_pass():
    out = _run(ideas.eval_ideas(None, ideas.mock_ideas(BRAND), "chess", "short-form"))
    assert out == [True, True, True]


def test_to_briefs_shape():
    briefs = ideas.to_briefs("c1", ideas.mock_ideas(BRAND), source="chat")
    assert len(briefs) == 3
    assert briefs[0]["score"] > briefs[2]["score"]          # safest-bet ranks first
    assert len({b["id"] for b in briefs}) == 3               # unique ULIDs
    assert all(b["source"] == "chat" and b["creator_id"] == "c1" for b in briefs)


def test_suggest_flag_off_is_noop():
    assert _run(ideas.suggest_ideas(FakeStore(), "c1", BRAND)) == []


def test_suggest_pipeline_filters_and_persists(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        if "ideas" in schema.get("required", []):
            return {"ideas": [{"title": f"Chess idea {i}", "content": "c"} for i in range(3)]}
        # eval: drop idea #2 (off-niche)
        return {"results": [{"idea_index": 1, "pass": True},
                            {"idea_index": 2, "pass": False},
                            {"idea_index": 3, "pass": True}]}
    monkeypatch.setattr(ideas, "anthropic_cached_json", fake_json)

    store = FakeStore()
    briefs = _run(ideas.suggest_ideas(store, "c1", BRAND))
    assert len(briefs) == 2                                   # #2 filtered out
    assert len(store.upserts) == 2
    assert {b["title"] for b in briefs} == {"Chess idea 0", "Chess idea 2"}


# --- spitfire chain + parser ---------------------------------------------------

_BLOCK = "<OPEN>\nTITLE: {t}\nSUMMARY: s{t}\nBEGINNING: b{t}\nMIDDLE: m{t}\nEND: e{t}\n<CLOSE>"


def test_parse_thinking_output_legacy_and_new():
    legacy = ideas.parse_thinking_output(_BLOCK.format(t="A"))
    assert legacy["title"] == "A" and legacy["beginning"] == "bA" and legacy["ending"] == "eA"
    new = ideas.parse_thinking_output("<OPEN>\nTITLE: X\nCONTENT: hello world\n<CLOSE>")
    assert new["title"] == "X" and new["content"] == "hello world"
    assert ideas.parse_thinking_output("no tags here") is None
    assert ideas.parse_thinking_output("<CLOSE>garbage<OPEN>") is None   # inverted


def test_parse_all_and_ranking():
    text = _BLOCK.format(t="A") + "\n" + _BLOCK.format(t="B") + "\n" + _BLOCK.format(t="C")
    cands = ideas.parse_all(text)
    assert [c["title"] for c in cands] == ["A", "B", "C"]
    assert ideas._parse_ranking("[3] > [1] > [2]", 3) == [2, 0, 1]
    assert ideas._parse_ranking("garbage", 3) == [0, 1, 2]              # identity fallback
    assert ideas._parse_ranking("[2]", 3) == [1, 0, 2]                  # missing appended


def test_spitfire_keyless_returns_mock_briefs():
    briefs = _run(ideas.spitfire(None, "c1", BRAND))
    assert len(briefs) == 3 and all(b["source"] == "spitfire" for b in briefs)


def test_spitfire_pipeline_ranks_and_budgets(monkeypatch):
    calls = {"n": 0}
    three = _BLOCK.format(t="A") + _BLOCK.format(t="B") + _BLOCK.format(t="C")

    async def fake_cached(system, user, model, max_tokens=0, temperature=None):
        calls["n"] += 1
        if "ideation engine" in system:
            return three                                   # generator
        if "Critique each candidate" in system:
            return "crit"
        if "Rewrite the idea" in system:
            return three                                   # editor (same 3 blocks)
        if "Rank the ideas" in system:
            return "[2] > [3] > [1]"                        # -> B, C, A
        return None
    monkeypatch.setattr(ideas, "anthropic_cached", fake_cached)

    briefs = _run(ideas.spitfire(FakeStore(), "c1", BRAND))
    assert [b["title"] for b in briefs] == ["B", "C", "A"]
    assert calls["n"] == 4                                  # ideate chain budget <= 4
