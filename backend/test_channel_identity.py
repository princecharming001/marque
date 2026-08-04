"""R2 (channel identity) — keyless tests: deterministic cold doc specificity, the
block renderer, anti-horoscope + ladder text in the cold prompt, established-recipe
selection, persistence degradation, flag/real_creator gating, and LLM-shape
validation via a fake structured call."""
from __future__ import annotations

import asyncio

from app import channel_identity as ci
from app import palo_flags

BRAND = {
    "niche": "chess", "what_you_do": "teach chess openings", "audience": "beginners",
    "known_for": "blitz speedruns", "catchphrases": ["check the ladder"],
    "voice": {"funnyToSerious": 0.2, "polishedToRaw": 0.9, "teacherToPeer": 0.3},
    "non_negotiables": ["never use engine lines without saying so"],
    "primary_platform": "tiktok",
}

POSTS = [
    {"title": "How I hit 1800 blitz in 90 days", "views": 12000,
     "summary": "Speedrun recap with three opening traps."},
    {"title": "The London System is a crutch", "views": 40000},
]

_LLM_DOC = {
    "niche_role": "The blitz-speedrun chess teacher",
    "primary_function": "Teach beginners real opening plans through blitz speedruns",
    "content_type": "60-second talking-head recaps of ranked climbs with board overlays",
    "voice_and_tone": "Fast, cocky-but-kind blitz talk: opens mid-thought, jokes about "
                      "blunders, then lands one concrete plan per clip.",
    "voice_anchors": ["This opening wins games you deserve to lose", "check the ladder"],
    "macro_style": {"verbal_primacy": "high", "visual_primacy": "low",
                    "content_originality": "high", "production_level": "low",
                    "methodical_planning": "mid", "factuality_level": "high"},
    "creator_context": "Chess coach who streams ranked blitz climbs and teaches openings",
    "durable_constraints": ["never use engine lines without saying so"],
    "data_confidence": "high",
}


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status_code=200, rows=None):
        self.status_code = status_code
        self._rows = rows if rows is not None else []

    def json(self):
        return self._rows


class FakeStore:
    """Records every _request so tests can assert exactly what hit the DB."""

    def __init__(self, identity_row=None):
        self.requests = []
        self.identity_row = identity_row
        self.usage = []

    async def _request(self, method, path, *, params=None, json=None, headers=None):
        self.requests.append((method, path, params, json))
        if method == "GET" and path == "/creators":
            return _Resp(200, [{"channel_identity": self.identity_row}])
        return _Resp(204, [])

    async def load_prompt_override(self, key):
        return None

    async def record_ai_usage(self, row):
        self.usage.append(row)
        return True


def _flags_on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "CHANNEL_IDENTITY", True)


# --- keyless deterministic doc --------------------------------------------------

def test_keyless_build_is_specific_low_confidence():
    doc = _run(ci.build_identity(None, "c1", BRAND))
    assert doc["data_confidence"] == "low" and doc["built_from"] == "cold"
    text = " ".join([doc["voice_and_tone"], doc["content_type"],
                     doc["primary_function"], doc["niche_role"]]).lower()
    assert "chess" in text                                    # their niche, not filler
    assert "check the ladder" in doc["voice_and_tone"]        # catchphrase verbatim
    assert "authentic, relatable" not in text                 # anti-horoscope
    assert set(doc["macro_style"]) == set(ci._MACRO_DIALS)
    assert all(v in ("low", "mid", "high") for v in doc["macro_style"].values())
    assert doc["macro_style"]["verbal_primacy"] == "high"     # talking-head-first
    assert doc["macro_style"]["production_level"] == "low"    # polishedToRaw 0.9 = raw
    assert doc["durable_constraints"] == BRAND["non_negotiables"]
    assert doc["voice_anchors"]


def test_keyless_established_fallback_uses_real_titles():
    doc = _run(ci.build_identity(None, "c1", BRAND, posts=POSTS))
    assert doc["built_from"] == "established"
    assert "How I hit 1800 blitz in 90 days" in doc["voice_anchors"]
    assert doc["data_confidence"] == "low"                    # no analysis ran


def test_keyless_empty_brand_never_raises():
    doc = _run(ci.build_identity(None, "", {}))
    assert doc["voice_and_tone"] and doc["data_confidence"] == "low"
    block = ci.identity_block(doc)
    assert block.startswith("CHANNEL IDENTITY")


# --- identity_block renderer -----------------------------------------------------

def test_identity_block_renders_and_empty_safe():
    doc = _run(ci.build_identity(None, "c1", BRAND))
    block = ci.identity_block(doc)
    assert block.startswith("CHANNEL IDENTITY (confidence: low")
    assert "built from the creator's own words" in block
    assert "Voice anchors" in block and "Style dials" in block
    assert "verbal_primacy=high" in block
    assert "never cite it as observed performance" in block   # low-confidence honesty tail
    assert "never use engine lines without saying so" in block
    assert ci.identity_block(None) == ""
    assert ci.identity_block({}) == ""
    assert ci.identity_block({"unknown_key": 1}) == ""        # nothing substantive


def test_identity_block_high_confidence_drops_honesty_tail():
    block = ci.identity_block(_LLM_DOC)
    assert "confidence: high — grounded in the creator's analyzed posts" in block
    assert "never cite it as observed performance" not in block


# --- prompt text (the ported verbatim blocks) ------------------------------------

def test_cold_prompt_carries_anti_horoscope_and_ladder():
    system, user = ci.cold_identity_prompt(
        BRAND, chat_history=[{"role": "user", "content": "yo i teach chess lol"}])
    assert "FAILED IDENTITY MARKERS" in system                # anti-horoscope block
    assert "Could this describe a different creator in the same niche?" in system
    assert "STRONG DATA" in system and "PARTIAL DATA" in system and "THIN DATA" in system
    assert "CREATOR PROFILE CHECK" in system                  # stage-variation ladder
    assert 'data_confidence: "high" (STRONG), "medium" (PARTIAL), or "low" (THIN)' in system
    assert "yo i teach chess lol" in system                   # voice inferred from HOW they write
    assert "teach chess openings" in system                   # brand signals present
    assert "no exemplar or niche data" in system              # honest THIN default
    assert "JSON" in user


# --- recipe selection + LLM shape -------------------------------------------------

def test_established_recipe_selected_when_posts_passed(monkeypatch):
    _flags_on(monkeypatch)
    seen = {}

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        seen["system"] = system
        return dict(_LLM_DOC)
    monkeypatch.setattr(ci, "anthropic_cached_json", fake_json)

    store = FakeStore()
    doc = _run(ci.build_identity(store, "c1", BRAND, posts=POSTS))
    assert "brief a ghostwriter" in seen["system"]            # established prompt used
    assert "LAYER 1" in seen["system"] and "LAYER 2" in seen["system"]
    assert "How I hit 1800 blitz in 90 days" in seen["system"]  # posts in context
    assert doc["built_from"] == "established"
    assert doc["data_confidence"] == "high"                   # real posts ⇒ confidence allowed
    assert store.usage and store.usage[0]["operation"] == "identity.build"


def test_llm_doc_validated_and_honesty_clamped(monkeypatch):
    _flags_on(monkeypatch)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        bad = dict(_LLM_DOC)
        bad["macro_style"] = dict(_LLM_DOC["macro_style"], verbal_primacy="extreme")
        bad["data_confidence"] = "high"                       # a lie for a chat-only build
        return bad
    monkeypatch.setattr(ci, "anthropic_cached_json", fake_json)

    doc = _run(ci.build_identity(FakeStore(), "c1", BRAND))   # no posts, no exemplars
    assert doc["built_from"] == "cold"
    assert doc["macro_style"]["verbal_primacy"] == "mid"      # invalid level clamped
    assert doc["data_confidence"] == "low"                    # honesty clamp wins
    assert doc["voice_anchors"] == _LLM_DOC["voice_anchors"]


def test_llm_thin_output_falls_back_deterministic(monkeypatch):
    _flags_on(monkeypatch)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        return {"niche_role": "x"}                            # missing the core fields
    monkeypatch.setattr(ci, "anthropic_cached_json", fake_json)

    store = FakeStore()
    doc = _run(ci.build_identity(store, "c1", BRAND))
    assert "chess" in doc["voice_and_tone"].lower()           # deterministic doc
    assert store.usage == []                                  # no usage row for a discard


# --- persistence degradation + gating ---------------------------------------------

def test_save_load_degrade_without_store():
    assert _run(ci.save_identity(None, "c1", {"niche_role": "x"})) is False
    assert _run(ci.load_identity(None, "c1")) is None


def test_no_persistence_for_default_or_demo_creators():
    store = FakeStore()
    assert _run(ci.save_identity(store, "default", {"niche_role": "x"})) is False
    assert _run(ci.save_identity(store, "demo-abc", {"niche_role": "x"})) is False
    assert _run(ci.load_identity(store, "default")) is None
    assert store.requests == []                               # DB never touched


def test_flag_off_ensure_is_deterministic_without_persistence(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", False)
    store = FakeStore()
    doc = _run(ci.ensure_identity(store, "c1", BRAND))
    assert doc["data_confidence"] == "low" and doc["built_from"] == "cold"
    assert store.requests == []                               # flag off ⇒ no DB traffic


def test_ensure_builds_and_saves_when_flagged(monkeypatch):
    _flags_on(monkeypatch)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        return dict(_LLM_DOC)
    monkeypatch.setattr(ci, "anthropic_cached_json", fake_json)

    store = FakeStore()
    doc = _run(ci.ensure_identity(store, "c1", BRAND, posts=POSTS))
    patches = [r for r in store.requests if r[0] == "PATCH" and r[1] == "/creators"]
    assert len(patches) == 1
    assert patches[0][2] == {"creator_id": "eq.c1"}
    assert patches[0][3] == {"channel_identity": doc}         # exact column write
    assert store.requests[0][0] == "GET"                      # load-first


def test_ensure_returns_existing_without_rebuild(monkeypatch):
    _flags_on(monkeypatch)

    async def boom(*a, **k):
        raise AssertionError("must not rebuild when a stored identity exists")
    monkeypatch.setattr(ci, "anthropic_cached_json", boom)

    stored = {"niche_role": "x", "data_confidence": "high"}
    store = FakeStore(identity_row=stored)
    assert _run(ci.ensure_identity(store, "c1", BRAND)) == stored
    assert [r[0] for r in store.requests] == ["GET"]          # one read, no write


def test_ensure_never_raises_on_store_explosion(monkeypatch):
    _flags_on(monkeypatch)

    class ExplodingStore(FakeStore):
        async def _request(self, *a, **k):
            raise RuntimeError("db down")

    doc = _run(ci.ensure_identity(ExplodingStore(), "c1", BRAND))
    assert doc["data_confidence"] == "low"                    # degraded, not raised
