"""Outcome ranker (R2 port, gaps #7) — deterministic, keyless, no LLM, no numpy.

Covers: fixed-length deterministic featurizer; MIN_RATIO + right-censor pair hygiene;
learnability on a synthetic separable dataset (held-out, time-split, >80% and beats the
50% balanced-random floor); the <MIN_PAIRS honesty refusal; rerank stability/identity/
never-drops; anchor_brief with-and-without a model; persistence degrade paths.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

import pytest

from app import outcome_ranker as ranker
from app import palo_flags


def _run(coro):
    return asyncio.run(coro)


T0 = datetime(2026, 1, 1, 9, 0, 0)


def _iso(days: float) -> str:
    return (T0 + timedelta(days=days)).isoformat()


# --- fake PostgREST store (palo_persistence _request contract) ------------------------

class FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else []

    def json(self):
        return self._body


class FakeStore:
    """Just enough of PaloStore._request for creators.outcome_model round-trips."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def _request(self, method, path, *, params=None, json=None, headers=None):
        assert path == "/creators"
        if method == "GET":
            cid = (params or {}).get("creator_id", "").removeprefix("eq.")
            if cid in self.rows:
                return FakeResp(200, [{"outcome_model": self.rows[cid]}])
            return FakeResp(200, [])
        if method == "POST":
            self.rows[json["creator_id"]] = json["outcome_model"]
            return FakeResp(201, [])
        return FakeResp(400, [])


class ExplodingStore:
    async def _request(self, *a, **kw):
        raise RuntimeError("db down")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "OUTCOME_RANKER", True)


# --- synthetic datasets ---------------------------------------------------------------

def _synth_separable(n=60, seed=7):
    """Feature dim 0 drives views (8x spread); dim 1 is noise; rest zero. All settled
    so the censor guard keeps everything; one sample per day keeps every pair inside
    PAIR_WINDOW_DAYS."""
    rng = random.Random(seed)
    samples = []
    for i in range(n):
        latent = rng.uniform(-1.0, 1.0)
        feats = [0.0] * ranker.FEATURE_DIM
        feats[0] = latent + rng.gauss(0.0, 0.05)
        feats[1] = rng.gauss(0.0, 1.0)
        samples.append({"views": 1000.0 * (8.0 ** latent), "features": feats,
                        "ts": _iso(i), "settled": True})
    return samples


_WIN_TITLES = ["5 mistakes killing your reach?", "3 hooks that won this week?",
               "7 edits you skip daily?", "9 second rule for retention?",
               "4 openers that always land?"]
_LOSE_TITLES = ["my thoughts today", "just a little update from me",
                "rambling about the week again", "some feelings on creating stuff",
                "another day another video diary"]


def _synth_posts(n=20):
    """Real-featurizer dataset: digit+question titles win 10x. Alternating so pair
    orientation parity mixes both classes."""
    posts = []
    for i in range(n):
        win = i % 2 == 0
        title = (_WIN_TITLES if win else _LOSE_TITLES)[(i // 2) % 5]
        posts.append({"title": title, "summary": "notes on the take",
                      "views": 5000.0 if win else 500.0, "ts": _iso(i),
                      "settled": True, "style": "talking_head",
                      "hook_signal": "curiosity" if win else "narrative"})
    return posts


def _samples_from_posts(posts):
    return [{"views": p["views"],
             "features": ranker.featurize(p["title"], p["summary"],
                                          {"style": p["style"],
                                           "hook_signal": p["hook_signal"]}),
             "ts": p["ts"], "settled": p["settled"]} for p in posts]


# --- featurizer -----------------------------------------------------------------------

def test_featurize_fixed_length_and_deterministic():
    a = ranker.featurize("5 hooks that WIN?", "Here is why you fail.",
                         {"style": "faceless", "hook_signal": "stakes", "hour": 19})
    b = ranker.featurize("5 hooks that WIN?", "Here is why you fail.",
                         {"style": "faceless", "hook_signal": "stakes", "hour": 19})
    assert a == b
    assert len(a) == ranker.FEATURE_DIM == len(ranker.FEATURE_NAMES)
    assert all(isinstance(x, float) for x in a)


def test_featurize_text_dims():
    v = ranker.featurize("Stop doing this: 3 fixes?", 'I said "never again" to you')
    names = ranker.FEATURE_NAMES
    assert v[names.index("title_has_digit")] == 1.0
    assert v[names.index("title_has_question")] == 1.0
    assert v[names.index("title_negation")] == 1.0
    assert v[names.index("title_colon_list")] == 1.0
    assert v[names.index("has_quote")] == 1.0
    assert v[names.index("first_person")] == 1.0
    assert v[names.index("second_person")] == 1.0


def test_featurize_meta_one_hots_capped_and_unknown_safe():
    v = ranker.featurize("t", meta={"style": "talking_head",
                                    "hook_signal": "patternInterrupt", "hour": 3})
    names = ranker.FEATURE_NAMES
    assert v[names.index("style_talking_head")] == 1.0
    assert v[names.index("hook_patternInterrupt")] == 1.0
    assert v[names.index("hour_0_5")] == 1.0
    assert sum(v[names.index("style_talking_head"):]) == 3.0  # exactly one hot per block
    # unknown vocab / absent meta degrade to all-zero blocks, same length
    u = ranker.featurize("t", meta={"style": "vlog??", "hook_signal": "x", "hour": 99})
    assert len(u) == ranker.FEATURE_DIM
    assert sum(u[len(ranker._TEXT_FEATURES):]) == 0.0
    assert len(ranker.featurize(None, None, None)) == ranker.FEATURE_DIM


# --- pair hygiene ---------------------------------------------------------------------

def test_pairs_respect_min_ratio():
    lo = {"views": 1000.0, "features": [1.0, 0.0], "ts": _iso(0), "settled": True}
    near = {"views": 1900.0, "features": [2.0, 0.0], "ts": _iso(1), "settled": True}
    win = {"views": 2000.0, "features": [3.0, 0.0], "ts": _iso(2), "settled": True}
    assert ranker.build_pairs([lo, near]) == []                  # 1.9x < MIN_RATIO
    pairs = ranker.build_pairs([lo, win])                        # exactly 2.0x qualifies
    assert len(pairs) == 1
    diff, y = pairs[0]
    assert y == 1.0 and diff == [2.0, 0.0]                       # winner-minus-loser


def test_pair_window_and_orientation_alternation():
    s = [{"views": 1000.0 * (4.0 ** i), "features": [float(i)], "ts": _iso(i * 2),
          "settled": True} for i in range(4)]
    pairs = ranker.build_pairs(s)
    labels = [y for _, y in pairs]
    assert 0.0 in labels and 1.0 in labels                       # RNG-free class balance
    far = [{"views": 1000.0, "features": [0.0], "ts": _iso(0), "settled": True},
           {"views": 9000.0, "features": [1.0], "ts": _iso(120), "settled": True}]
    assert ranker.build_pairs(far) == []                         # outside PAIR_WINDOW_DAYS


def test_censor_guard_drops_young_unless_settled():
    old = {"views": 1000.0, "features": [1.0], "ts": _iso(0), "settled": False}
    young = {"views": 9000.0, "features": [2.0], "ts": _iso(15), "settled": False}
    newest = {"views": 500.0, "features": [3.0], "ts": _iso(40), "settled": False}
    kept = ranker.usable_samples([old, young, newest])
    assert [s["features"] for s in kept] == [[1.0]]              # young ones censored
    young["settled"] = True                                       # settled => censor-free
    kept = ranker.usable_samples([old, young, newest])
    assert [s["features"] for s in kept] == [[1.0], [2.0]]


def test_usable_samples_floor_and_junk():
    assert ranker.usable_samples([
        {"views": ranker.MIN_VIEWS - 1, "features": [1.0], "ts": ""},   # below floor
        {"views": "not-a-number", "features": [1.0], "ts": ""},
        {"views": 1000.0, "features": [], "ts": ""},
        "junk", None,
    ]) == []


# --- training -------------------------------------------------------------------------

def test_train_learns_separable_heldout_above_80():
    train_s, test_s = ranker.time_split(_synth_separable(), test_frac=0.2)
    model = ranker.train_pairwise(train_s, trained_at="2026-03-01T00:00:00")
    assert model is not None
    assert set(model) == {"w", "mean", "std", "n_pairs", "trained_at", "version"}
    assert model["version"] == ranker.MODEL_VERSION
    assert model["trained_at"] == "2026-03-01T00:00:00"
    assert model["n_pairs"] >= ranker.MIN_PAIRS
    test_pairs = ranker.build_pairs(ranker.usable_samples(test_s))
    assert len(test_pairs) >= 5
    labels = [y for _, y in test_pairs]
    assert 0.25 <= sum(labels) / len(labels) <= 0.75             # balanced => 50% floor
    acc = ranker.pairwise_accuracy(model, test_pairs)
    assert acc > 0.8                                             # >80% AND beats random
    zero = dict(model, w=[0.0] * len(model["w"]))                # score-less baseline
    assert acc > ranker.pairwise_accuracy(zero, test_pairs)


def test_train_is_deterministic():
    s = _synth_separable()
    m1 = ranker.train_pairwise(s, trained_at="t")
    m2 = ranker.train_pairwise(s, trained_at="t")
    assert m1 == m2


def test_too_few_pairs_returns_none_never_junk():
    assert ranker.train_pairwise([]) is None
    few = [{"views": 1000.0 * (4.0 ** i), "features": [float(i), 1.0], "ts": _iso(i),
            "settled": True} for i in range(4)]                  # 6 pairs < MIN_PAIRS
    assert len(ranker.build_pairs(few)) < ranker.MIN_PAIRS
    assert ranker.train_pairwise(few) is None


# --- scoring + rerank -----------------------------------------------------------------

def test_score_none_model_and_shape_mismatch():
    assert ranker.score(None, [1.0] * ranker.FEATURE_DIM) is None
    assert ranker.score_idea_text(None, "title", "body") is None
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    assert model is not None
    assert ranker.score(model, [1.0, 2.0]) is None               # dim drift => None
    assert ranker.score(model, "junk") is None
    got = ranker.score_idea_text(model, "3 hooks you need?", "why they work")
    assert isinstance(got, float)


def test_rerank_orders_by_own_outcomes():
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    briefs = [{"id": "b1", "title": "a slow diary entry about my week", "summary": ""},
              {"id": "b2", "title": "7 hooks that doubled reach?", "summary": ""},
              {"id": "b3", "title": "thinking out loud again", "summary": ""}]
    out = ranker.rerank(briefs, model)
    assert out[0]["id"] == "b2"                                  # digit+question wins
    assert {b["id"] for b in out} == {"b1", "b2", "b3"}          # never drops
    assert out[0] is briefs[1]                                   # same objects, reordered


def test_rerank_identity_when_no_model_and_stable_on_ties():
    briefs = [{"id": f"b{i}", "title": "same title", "summary": "same"} for i in range(5)]
    out = ranker.rerank(briefs, None)
    assert out == briefs and out[0] is briefs[0]                 # identity, same objects
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    tied = ranker.rerank(briefs, model)
    assert [b["id"] for b in tied] == [f"b{i}" for i in range(5)]  # stable on equal scores
    assert ranker.rerank([], model) == []


def test_rerank_keeps_unscoreable_items():
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    briefs = [{"id": "ok", "title": "5 fixes for flat hooks?"}, "not-a-dict", None]
    out = ranker.rerank(briefs, model)
    assert len(out) == 3 and out[0] == briefs[0]
    assert "not-a-dict" in out and None in out                   # sink, never dropped


# --- anchors --------------------------------------------------------------------------

def test_anchor_brief_requires_model():
    assert ranker.anchor_brief(None) == ""
    assert ranker.anchor_brief({"w": "junk"}) == ""


def test_anchor_brief_renders_with_trained_model():
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    brief = ranker.anchor_brief(model)
    assert "WIN here" in brief and "LOSE here" in brief
    stmt_lines = [l for l in brief.splitlines() if l.startswith("  ")]
    assert len(stmt_lines) == 10                                 # top 5 + bottom 5
    assert all(any(stmt in l for stmt in ranker.ANCHOR_STATEMENTS) for l in stmt_lines)


# --- persistence ----------------------------------------------------------------------

def test_save_load_roundtrip_and_guards():
    store = FakeStore()
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    assert _run(ranker.save_model(store, "c1", model)) is True
    loaded = _run(ranker.load_model(store, "c1"))
    assert loaded == model
    assert _run(ranker.load_model(store, "nobody")) is None      # no row
    # real_creator guard: demo/default ids never own a learned model
    for cid in ("default", "demo", "demo-abc", ""):
        assert _run(ranker.save_model(store, cid, model)) is False
        assert _run(ranker.load_model(store, cid)) is None
    assert _run(ranker.save_model(store, "c2", {"w": "junk"})) is False  # invalid model


def test_persistence_degrades_keyless_and_on_errors():
    model = ranker.train_pairwise(_samples_from_posts(_synth_posts()))
    assert _run(ranker.load_model(None, "c1")) is None           # store=None
    assert _run(ranker.save_model(None, "c1", model)) is False
    boom = ExplodingStore()
    assert _run(ranker.load_model(boom, "c1")) is None           # exception swallowed
    assert _run(ranker.save_model(boom, "c1", model)) is False


def test_load_rejects_malformed_stored_model():
    store = FakeStore()
    store.rows["c1"] = {"w": [1.0], "mean": [0.0]}               # std missing
    assert _run(ranker.load_model(store, "c1")) is None


# --- train_for (end-to-end) -----------------------------------------------------------

def test_train_for_flag_gated():
    assert _run(ranker.train_for(FakeStore(), "c1", _synth_posts())) is None  # flag OFF


def test_train_for_end_to_end(on):
    store = FakeStore()
    model = _run(ranker.train_for(store, "c1", _synth_posts()))
    assert model is not None and model["n_pairs"] >= ranker.MIN_PAIRS
    assert store.rows["c1"] == model                             # persisted
    assert model["trained_at"] == _iso(19)                       # newest label ts, no clock
    assert _run(ranker.load_model(store, "c1")) == model
    briefs = [{"id": "a", "title": "quiet vlog thoughts"},
              {"id": "b", "title": "6 cuts that hold viewers?"}]
    assert ranker.rerank(briefs, model)[0]["id"] == "b"


def test_train_for_guards_and_degrades(on):
    assert _run(ranker.train_for(FakeStore(), "demo-xyz", _synth_posts())) is None
    assert _run(ranker.train_for(FakeStore(), "c1", _synth_posts()[:3])) is None  # thin
    assert _run(ranker.train_for(FakeStore(), "c1", [{"title": "no views"}])) is None
    # keyless: model still trains in-memory, only persistence degrades
    model = _run(ranker.train_for(None, "c1", _synth_posts()))
    assert model is not None
    # a store that explodes on save must not sink the trained model
    model = _run(ranker.train_for(ExplodingStore(), "c1", _synth_posts()))
    assert model is not None
