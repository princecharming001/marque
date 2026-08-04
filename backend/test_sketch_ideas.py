"""R2 (SKETCH_IDEAS) — keyless tests: flag gating, no-fallback-copy, sketchbook
parser salvage, vocab firewall, scaffold discard, similarity dedup, and the full
sketch→bake-off pipeline via fakes (style of test_palo_ideas.py)."""
from __future__ import annotations

import asyncio

from app import palo_flags, sketch_ideas

BRAND = {"niche": "chess", "known_for": "speedruns", "audience": "beginners"}

# Scaffold literals are built by concatenation so the source never contains raw
# tool-markup sequences.
_ANTML_TAG = "</" + "antml>"
_PARAM_TAG = "<" + 'parameter name="brief">'


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.usage = []

    async def load_prompt_override(self, key):
        return None

    async def record_ai_usage(self, row):
        self.usage.append(row)
        return True


def _flags_on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "SKETCH_IDEAS", True)


_SKETCHBOOK = """<sketches>
<sketch n="1">
the history of the london system
Everyone plays it, almost nobody knows where it came from. The video rides that gap.
</sketch>
<sketch n="2">
every gambit at its peak
One-second snapshots of famous gambits, the wildest saved for last.
</sketch>
<sketch n="3">
the opening that never loses
The longshot: hold one unbeaten claim game after game and wait for the loss.
</sketch>
</sketches>"""


def _idea(title, concept=None, pitch=None, brief=None):
    return {
        "title": title,
        "concept": concept or ("Your endgame videos carry this one.\n\n"
                               "Hook → the queen hangs, you leave it hanging.\n\n"
                               "Payoff → mate in three, the sacrifice explained."),
        "pitch": pitch or ("Your endgame videos keep pulling new viewers. "
                           "Hang the queen on purpose, then land the mate."),
        "brief": brief or ("Hold the hang-then-mate structure. Verify the line is "
                           "sound before building. Venue open. Beat the tier-list "
                           "rival: no payoff."),
    }


_THREE_CLEAN = [_idea("The Queen Sacrifice Nobody Plays"),
                _idea("I Tried the Oldest Chess Trap in History"),
                _idea("Five Moves That Beat a Grandmaster")]


# --- flag gating + no-fallback-copy ---------------------------------------------

def test_flag_off_returns_empty_without_llm(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "SKETCH_IDEAS", False)

    async def boom(*a, **k):
        raise AssertionError("LLM must not be called when the flag is off")
    monkeypatch.setattr(sketch_ideas, "anthropic_cached", boom)
    monkeypatch.setattr(sketch_ideas, "anthropic_cached_json", boom)
    assert _run(sketch_ideas.bake_ideas(FakeStore(), "c1", BRAND)) == []


def test_keyless_returns_empty_never_fallback_copy(monkeypatch):
    """NO-FALLBACK-COPY: keyless (both passes return None) must yield EXACTLY [] —
    this path never mints mock/template briefs; the caller falls back to spitfire."""
    _flags_on(monkeypatch)

    async def none_text(*a, **k):
        return None

    async def none_json(*a, **k):
        return None
    monkeypatch.setattr(sketch_ideas, "anthropic_cached", none_text)
    monkeypatch.setattr(sketch_ideas, "anthropic_cached_json", none_json)
    out = _run(sketch_ideas.bake_ideas(None, "c1", BRAND))
    assert out == []                                     # not mock ideas, not partial


# --- sketchbook parser salvage --------------------------------------------------

def test_parse_sketchbook_live_contract():
    out = sketch_ideas.parse_sketchbook(_SKETCHBOOK)
    assert [s["title"] for s in out] == [
        "the history of the london system", "every gambit at its peak",
        "the opening that never loses"]
    assert "rides that gap" in out[0]["note"]


def test_parse_sketchbook_unclosed_trailing_sketch():
    cut = '<sketches>\n<sketch n="1">\ntitle a\nnote a\n</sketch>\n' \
          '<sketch n="2">\ntitle b\nnote b that got cut mid-str'
    out = sketch_ideas.parse_sketchbook(cut)
    assert [s["title"] for s in out] == ["title a", "title b"]


def test_parse_sketchbook_legacy_contract_and_unclosed_child():
    legacy = ('<sketch n="1"><premise>P1</premise><hook>H1</hook>'
              '<payoff>PO1</payoff><engine>E1</engine></sketch>')
    out = sketch_ideas.parse_sketchbook(legacy)
    assert out == [{"title": "P1", "note": "H1 PO1 E1"}]
    # truncated stream: unclosed <sketch> AND unclosed <premise> still salvaged
    out2 = sketch_ideas.parse_sketchbook('<sketch n="1"><premise>only this')
    assert out2 and out2[0]["title"] == "only this"


def test_parse_sketchbook_garbage_and_json_yield_nothing():
    assert sketch_ideas.parse_sketchbook("no tags here at all") == []
    assert sketch_ideas.parse_sketchbook('{"sketches": [{"title": "x"}]}') == []
    assert sketch_ideas.parse_sketchbook("") == []
    assert sketch_ideas.parse_sketchbook(None) == []


# --- vocab firewall -------------------------------------------------------------

def test_vocab_leaks_multiplier_and_stat_word_with_digit():
    leaks = sketch_ideas.vocab_leaks("this format runs 2.3x baseline on your channel")
    assert "2.3x" in leaks and "baseline" in leaks
    assert sketch_ideas.vocab_leaks("a 7/7 hit rate across reps")  # ratio + stat word


def test_vocab_firewall_spares_plain_english():
    assert sketch_ideas.vocab_leaks("lift the pallet a few feet off the truck") == []
    assert sketch_ideas.vocab_leaks("the median viewer bails at three seconds") == []
    assert sketch_ideas.vocab_leaks("set the baseline expectation early") == []
    # snake_case internal spelling always counts, digit or not
    assert sketch_ideas.vocab_leaks("baseline_multiplier looks strong") == ["baseline_multiplier"]


def test_scrub_vocab():
    out = sketch_ideas.scrub_vocab("Your best open (3.63x baseline) still works")
    assert "3.63x" not in out and "baseline" not in out and "still works" in out
    # a no-digit parenthetical is legitimate copy and survives
    assert sketch_ideas.scrub_vocab("(the proven way you always start)") \
        == "(the proven way you always start)"


# --- scaffold discard -----------------------------------------------------------

def test_is_scaffold():
    assert sketch_ideas.is_scaffold(_ANTML_TAG + " Let me redo this properly.")
    assert sketch_ideas.is_scaffold(_PARAM_TAG + "placeholder")
    assert sketch_ideas.is_scaffold("Let me try that again")
    assert not sketch_ideas.is_scaffold("Hook → the queen hangs, you leave it hanging.")
    assert not sketch_ideas.is_scaffold("")


# --- similarity dedup -----------------------------------------------------------

def test_too_similar_token_containment():
    assert sketch_ideas.too_similar("Fire Alarm Chaos at the World Cup",
                                    "World Cup Fire Alarm Prank: The Ultimate Dream Stunt")
    assert not sketch_ideas.too_similar("The Queen Sacrifice Nobody Plays",
                                        "I Tried the Oldest Chess Trap in History")
    # one shared significant token is never a duplicate
    assert not sketch_ideas.too_similar("banned from chess", "banned forever")


# --- pipeline (monkeypatched LLM) -----------------------------------------------

def _fake_llms(monkeypatch, json_results, sketch_text=_SKETCHBOOK):
    """Wire fakes; returns dicts capturing calls. json_results is consumed in order
    (last one repeats)."""
    seen = {"sketch_users": [], "idea_users": [], "json_calls": 0}

    async def fake_cached(system, user, model, max_tokens=0, temperature=None):
        seen["sketch_users"].append(user)
        return sketch_text

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        seen["idea_users"].append(user)
        i = min(seen["json_calls"], len(json_results) - 1)
        seen["json_calls"] += 1
        return json_results[i]
    monkeypatch.setattr(sketch_ideas, "anthropic_cached", fake_cached)
    monkeypatch.setattr(sketch_ideas, "anthropic_cached_json", fake_json)
    return seen


def test_happy_path_three_ideas(monkeypatch):
    _flags_on(monkeypatch)
    seen = _fake_llms(monkeypatch, [{"ideas": list(_THREE_CLEAN)}])
    store = FakeStore()
    briefs = _run(sketch_ideas.bake_ideas(store, "c1", BRAND, mix="one gambit, one story",
                                          recent_titles=["My Rating Climb"]))
    assert len(briefs) == 3
    for b in briefs:
        assert b["creator_id"] == "c1" and b["source"] == "sketch" and b["status"] == "new"
        assert b["title"] and b["concept"] and b["pitch"] and b["brief"]
        assert b["summary"] == b["pitch"]                     # summary = the card pitch
        # firewall-clean creator-facing copy
        assert sketch_ideas.vocab_leaks(f"{b['title']} {b['concept']} {b['pitch']}") == []
    assert len({b["id"] for b in briefs}) == 3                # unique ULIDs
    assert briefs[0]["score"] > briefs[2]["score"]            # ranked positional score
    assert seen["json_calls"] == 1                            # no retry needed
    assert "SKETCH PASS" in seen["idea_users"][0]             # sketchbook reached pass 2
    assert any(u["operation"] == "sketch.idea" for u in store.usage)


def test_cold_start_sketch_absent_still_bakes(monkeypatch):
    """Sketch pass fails (keyless/unparseable) → the idea pass drafts its own rivals:
    the user message flags the absent sketchbook and ideas still come back."""
    _flags_on(monkeypatch)
    seen = _fake_llms(monkeypatch, [{"ideas": list(_THREE_CLEAN)}], sketch_text=None)

    async def none_text(system, user, model, max_tokens=0, temperature=None):
        return None
    monkeypatch.setattr(sketch_ideas, "anthropic_cached", none_text)
    briefs = _run(sketch_ideas.bake_ideas(None, "c1", BRAND))
    assert len(briefs) == 3
    assert "absent" in seen["idea_users"][0]


def test_vocab_leak_triggers_one_bounded_retry(monkeypatch):
    _flags_on(monkeypatch)
    leaking = {"ideas": [_idea("This Format Runs 2.3x Baseline For You")]}
    clean = {"ideas": [_idea("The Comeback Nobody Saw Coming")]}
    seen = _fake_llms(monkeypatch, [leaking, clean])
    briefs = _run(sketch_ideas.bake_ideas(FakeStore(), "c1", BRAND))
    assert seen["json_calls"] == 2                            # exactly one retry
    assert "REJECTED" in seen["idea_users"][1]
    assert "plain creator language" in seen["idea_users"][1]
    assert [b["title"] for b in briefs] == ["The Comeback Nobody Saw Coming"]


def test_vocab_leak_after_retry_is_scrubbed_not_repeated(monkeypatch):
    _flags_on(monkeypatch)
    leaking = {"ideas": [_idea("Chess Is Running 2.3x Baseline Right Now")]}
    seen = _fake_llms(monkeypatch, [leaking, leaking])        # retry still leaks
    briefs = _run(sketch_ideas.bake_ideas(FakeStore(), "c1", BRAND))
    assert seen["json_calls"] == 2                            # bounded: never a third
    assert len(briefs) == 1
    assert sketch_ideas.vocab_leaks(
        f"{briefs[0]['title']} {briefs[0]['concept']} {briefs[0]['pitch']}") == []


def test_dedup_against_recent_titles(monkeypatch):
    _flags_on(monkeypatch)
    ideas = {"ideas": [_idea("Fire Alarm Chaos at the World Cup"),
                       _idea("The Queen Sacrifice Nobody Plays")]}
    _fake_llms(monkeypatch, [ideas])
    briefs = _run(sketch_ideas.bake_ideas(
        None, "c1", BRAND, recent_titles=["World Cup Fire Alarm Prank"]))
    assert [b["title"] for b in briefs] == ["The Queen Sacrifice Nobody Plays"]


def test_scaffolded_fields_discarded_never_repaired(monkeypatch):
    _flags_on(monkeypatch)
    broken_title = _idea(_ANTML_TAG + " Let me redo this properly.")
    blank_pitch = _idea("Five Moves That Beat a Grandmaster",
                        pitch=_PARAM_TAG + "placeholder")
    _fake_llms(monkeypatch, [{"ideas": [broken_title, blank_pitch]}])
    briefs = _run(sketch_ideas.bake_ideas(None, "c1", BRAND))
    assert len(briefs) == 1                                   # scaffolded title ⇒ dropped
    b = briefs[0]
    assert b["pitch"] == ""                                   # discarded, not repaired
    assert b["summary"] == "Your endgame videos carry this one."  # falls back to primer
    assert b["title"] == "Five Moves That Beat a Grandmaster"


def test_never_raises_on_llm_exception(monkeypatch):
    _flags_on(monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("vendor down")
    monkeypatch.setattr(sketch_ideas, "anthropic_cached", boom)
    monkeypatch.setattr(sketch_ideas, "anthropic_cached_json", boom)
    assert _run(sketch_ideas.bake_ideas(FakeStore(), "c1", BRAND)) == []
