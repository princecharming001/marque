"""R2 (decider) — keyless deterministic tests: flag gating, silent-day degrades,
no-LLM-call on empty candidates, forced destinations, pill number-scrub, vitals
threshold helpers, candidate grounding (evidence only echoes inputs), and
briefing hero preference. No network, no key."""
from __future__ import annotations

import asyncio

from app import decider, palo_flags

ARMS = [
    {"dimension": "hook", "value": "question_hook", "n": 6, "lift": 0.5, "hit_rate": "0/6"},
    {"dimension": "style", "value": "meme_style", "n": 3, "lift": 0.44},
    {"dimension": "pillar", "value": "day_in_life", "n": 4, "lift": 2.6},
]
POSTS = [
    {"id": "p1", "title": "Alpha", "views": 5000, "age_days": 2.0},
    {"id": "p2", "title": "Bravo", "views": 1000, "age_days": 6.0},
    {"id": "p3", "title": "Charlie", "views": 900, "age_days": 9.0},
]
BRIEFS = [{"id": "b1", "title": "Saved banger", "summary": "s", "status": "new", "score": 0.85}]

CANDS = [{"kind": "view_spike", "subject": "Alpha", "ref": "view_spike:p1",
          "detected_reason": "'Alpha' is spiking.",
          "evidence": {"views": 1100, "multiple": 2.0, "n": 3},
          "salience": 0.5, "candidate_responses": ["GENERATE_IDEAS", "REVIVE_PROJECT"]}]


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


def _arm_flag(monkeypatch, on=True):
    monkeypatch.setattr(palo_flags, "PALO_PORT", on)
    monkeypatch.setattr(palo_flags, "DECIDER", on)


def _bomb(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("LLM must not be called on this path")
    monkeypatch.setattr(decider, "anthropic_cached_json", boom)


def _decision(**over):
    d = {"signal_ref": "view_spike:p1", "response_type": "GENERATE_IDEAS", "rank": 1,
         "rationale": "r", "generator_instruction": "g", "destination": "idea_bank",
         "headline": "H", "body": "B", "pills": [], "confidence": "likely"}
    d.update(over)
    return d


def _fake_llm(monkeypatch, payload):
    async def fake(system, user, schema, model, max_tokens=0, temperature=None):
        return payload
    monkeypatch.setattr(decider, "anthropic_cached_json", fake)


# ── vitals threshold helpers (ported constants) ───────────────────────────────

def test_decisive_negative_gate():
    assert decider.is_decisive_negative(0, 5)            # 0 of n>=5 -> retire
    assert not decider.is_decisive_negative(0, 4)        # under-sampled
    assert not decider.is_decisive_negative(1, 5)        # something beat baseline


def test_weakest_needs_min_samples():
    assert decider.is_weakest_eligible(0.44, 3)
    assert not decider.is_weakest_eligible(0.44, 2)      # a question, not a verdict
    assert not decider.is_weakest_eligible(0.7, 5)       # not weak enough
    assert not decider.is_weakest_eligible(None, 5)


def test_posting_gap_math():
    assert decider.posting_gap_threshold(1.0) == 4       # floor: never nag at 2 days
    assert decider.posting_gap_threshold(3.0) == 6.0     # usual_gap x 2
    assert decider.posting_gap_fires(6.0, 3.0)
    assert not decider.posting_gap_fires(5.9, 3.0)
    assert decider.posting_gap_fires(4.0, 1.0)
    assert not decider.posting_gap_fires(3.9, 1.0)


def test_hit_beats_and_median():
    assert decider.hit_beats("0/6") == 0
    assert decider.hit_beats("3/7") == 3
    assert decider.hit_beats(None) is None
    assert decider.hit_beats("x/") is None
    assert decider.median_views([5000, 1000, 900]) == 1000
    assert decider.median_views([]) == 0.0
    assert decider.median_views([0, 0]) == 0.0           # zero-view posts excluded


# ── build_candidates (pure sensors) ───────────────────────────────────────────

def test_build_candidates_kinds_and_suppression():
    kinds = {c["kind"] for c in decider.build_candidates(ARMS, POSTS, BRIEFS)}
    # decisive on question_hook, weakest on meme_style, breakout, spike; gap quiet
    assert kinds == {"decisive_negative", "weakest_performer", "breakout", "view_spike"}
    # the decisive bucket never double-fires as weakest
    only = decider.build_candidates([ARMS[0]], [], [])
    assert [c["kind"] for c in only] == ["decisive_negative"]
    assert only[0]["subject"] == "question_hook"


def test_build_candidates_evidence_echoes_inputs_only():
    cands = {c["kind"]: c for c in decider.build_candidates(ARMS, POSTS, BRIEFS)}
    spike = cands["view_spike"]["evidence"]
    assert spike["views"] == 5000                        # exact input echo
    assert spike["median_views"] == 1000                 # median of input views
    assert spike["multiple"] == 5.0                      # 5000/1000, derived only
    assert spike["age_days"] == 2.0
    weak = cands["weakest_performer"]["evidence"]
    assert weak["lift"] == 0.44 and weak["n"] == 3
    dec = cands["decisive_negative"]["evidence"]
    assert dec["hit_rate"] == "0/6" and dec["n"] == 6
    hot = cands["breakout"]["evidence"]
    assert hot["lift"] == 2.6
    assert [b["title"] for b in hot["saved_briefs"]] == ["Saved banger"]
    for c in cands.values():                             # no fabricated metric keys
        assert "engagement" not in c["evidence"] and "followers" not in c["evidence"]


def test_build_candidates_min_samples_no_candidate():
    assert decider.build_candidates(
        [{"dimension": "style", "value": "memes", "n": 2, "lift": 0.4}], [], []) == []


def test_posting_gap_candidate_and_threshold():
    quiet = [{"id": "p1", "title": "Old", "views": 100, "age_days": 9.0},
             {"id": "p2", "title": "Older", "views": 100, "age_days": 12.0}]
    out = decider.build_candidates([], quiet, [], usual_gap_days=3.0)
    assert [c["kind"] for c in out] == ["posting_gap"]
    ev = out[0]["evidence"]
    assert ev["days_since_last_post"] == 9.0 and ev["gap_threshold_days"] == 6.0
    # usual gap 5 -> threshold 10 -> 9 days quiet is not a gap
    assert decider.build_candidates([], quiet, [], usual_gap_days=5.0) == []


def test_view_spike_needs_freshness():
    stale = [{"id": "p1", "title": "Old spike", "views": 5000, "age_days": 20.0},
             {"id": "p2", "title": "B", "views": 1000, "age_days": 21.0},
             {"id": "p3", "title": "C", "views": 900, "age_days": 22.0}]
    assert not any(c["kind"] == "view_spike"
                   for c in decider.build_candidates([], stale, []))


def test_revive_offered_only_with_saved_briefs():
    with_briefs = decider.build_candidates([ARMS[2]], [], BRIEFS)
    without = decider.build_candidates([ARMS[2]], [], [])
    assert "REVIVE_PROJECT" in with_briefs[0]["candidate_responses"]
    assert "REVIVE_PROJECT" not in without[0]["candidate_responses"]


# ── decide (flag gate, silent degrades, forced destinations, pill scrub) ──────

def test_flag_off_is_silent_and_calls_nothing(monkeypatch):
    _arm_flag(monkeypatch, on=False)
    _bomb(monkeypatch)
    out = _run(decider.decide(FakeStore(), "c1", CANDS))
    assert out["decisions"] == []


def test_empty_candidates_silent_day_without_llm_call(monkeypatch):
    _arm_flag(monkeypatch)
    _bomb(monkeypatch)                                   # raises if the LLM is touched
    out = _run(decider.decide(FakeStore(), "c1", []))
    assert out == {"day_header": "", "day_summary": "", "decisions": []}


def test_keyless_never_raises_silent_day(monkeypatch):
    _arm_flag(monkeypatch)
    monkeypatch.setattr("app.palo_llm._KEY", "")         # true keyless: helper -> None
    out = _run(decider.decide(None, "c1", CANDS))
    assert out["decisions"] == []


def test_parse_garbage_is_silent(monkeypatch):
    _arm_flag(monkeypatch)
    for garbage in (None, "not json", ["list"], 42):
        _fake_llm(monkeypatch, garbage)
        assert _run(decider.decide(FakeStore(), "c1", CANDS))["decisions"] == []


def test_dest_forced_from_response_type(monkeypatch):
    _arm_flag(monkeypatch)
    _fake_llm(monkeypatch, {"day_header": "h", "day_summary": "s", "decisions": [
        _decision(response_type="OBSERVE_REVIEW", destination="idea_bank", rank=1),
        _decision(response_type="REVIVE_PROJECT", destination="CHAT", rank=2),
        _decision(response_type="NUDGE_ONLY", destination="teardown", rank=3),
    ]})
    out = _run(decider.decide(FakeStore(), "c1", CANDS))
    assert [d["destination"] for d in out["decisions"]] == \
        ["teardown", "resurface_brief", "coach_card"]    # model slips overridden
    assert out["day_header"] == "h"


def test_unroutable_response_type_dropped(monkeypatch):
    _arm_flag(monkeypatch)
    _fake_llm(monkeypatch, {"day_header": "", "day_summary": "", "decisions": [
        _decision(response_type="STRATEGY_TAKE", rank=1),
        _decision(response_type="GENERATE_ALT_IDEA", rank=2),
    ]})
    out = _run(decider.decide(FakeStore(), "c1", CANDS))
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["response_type"] == "GENERATE_ALT_IDEA"
    assert out["decisions"][0]["destination"] == "idea_bank"


def test_pills_never_invent_numbers(monkeypatch):
    _arm_flag(monkeypatch)
    _fake_llm(monkeypatch, {"day_header": "", "day_summary": "", "decisions": [
        _decision(pills=["📉 1,100 views", "🚀 9,999 views", "✦ 3 ideas"]),
    ]})
    out = _run(decider.decide(FakeStore(), "c1", CANDS))
    # 1100 and 3 exist in the candidate evidence; 9,999 was invented -> scrubbed
    assert out["decisions"][0]["pills"] == ["📉 1,100 views", "✦ 3 ideas"]


def test_cap_three_ranked_with_provenance_and_usage(monkeypatch):
    _arm_flag(monkeypatch)
    _fake_llm(monkeypatch, {"day_header": "", "day_summary": "", "decisions": [
        _decision(rank=4, headline="d"), _decision(rank=2, headline="b"),
        _decision(rank=1, headline="a", confidence="certain"),
        _decision(rank=3, headline="c"),
    ]})
    store = FakeStore()
    out = _run(decider.decide(store, "c1", CANDS))
    assert [d["headline"] for d in out["decisions"]] == ["a", "b", "c"]   # <=3, rank asc
    top = out["decisions"][0]
    assert top["confidence"] == "hypothesis"             # out-of-enum clamped
    assert top["provenance"] == {"noticed": "view_spike:p1", "diagnosis": "r",
                                 "action": "GENERATE_IDEAS"}
    assert len(store.usage) == 1 and store.usage[0]["operation"] == "pulse.decide"


# ── shape_briefing (pure) ─────────────────────────────────────────────────────

def test_hero_artifact_beats_nudge():
    decisions = [{"response_type": "NUDGE_ONLY", "headline": "n"},
                 {"response_type": "REVIVE_PROJECT", "headline": "r"}]
    out = decider.shape_briefing(decisions, [])
    assert out["hero"]["headline"] == "r"                # no ranks -> preference decides
    # but an explicit decider rank always wins over preference (Palo semantics)
    ranked = [{"response_type": "NUDGE_ONLY", "headline": "n", "rank": 1},
              {"response_type": "REVIVE_PROJECT", "headline": "r", "rank": 2}]
    assert decider.shape_briefing(ranked, [])["hero"]["headline"] == "n"


def test_briefing_lanes_and_provenance_on_every_card():
    decisions = [
        {"response_type": "OBSERVE_REVIEW", "signal_ref": "weakest_performer:hooks",
         "rationale": "execution leaked", "headline": "review", "rank": 1},
        {"response_type": "GENERATE_ALT_IDEA", "signal_ref": "weakest_performer:hooks",
         "rationale": "swap subject", "headline": "alt", "rank": 2},
    ]
    out = decider.shape_briefing(decisions, BRIEFS)
    assert out["hero"]["headline"] == "review"
    assert [c["headline"] for c in out["lanes"]["reviews"]] == ["review"]
    idea_heads = [c["headline"] for c in out["lanes"]["ideas"]]
    assert idea_heads == ["alt", "Saved banger"]         # promoted brief joins the lane
    brief_card = out["lanes"]["ideas"][1]
    assert brief_card["destination"] == "idea_bank" and brief_card["brief_id"] == "b1"
    all_cards = [out["hero"]] + [c for lane in out["lanes"].values() for c in lane]
    for card in all_cards:
        prov = card["provenance"]
        assert set(prov) >= {"noticed", "diagnosis", "action"}
    assert out["hero"]["provenance"]["noticed"] == "weakest_performer:hooks"
    assert out["hero"]["provenance"]["action"] == "OBSERVE_REVIEW"


def test_briefing_empty_is_empty():
    out = decider.shape_briefing([], [])
    assert out["hero"] is None
    assert all(v == [] for v in out["lanes"].values())
