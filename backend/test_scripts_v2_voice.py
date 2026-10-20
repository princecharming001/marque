"""Scripts v2 voice profile (app/scripts_v2/voice.py). No network: llm.call is faked."""
from __future__ import annotations

import asyncio
import copy

import pytest

from app.scripts_v2 import voice

# A fitness coach who says "you guys", "honestly", "do the boring thing".
T1 = ("okay you guys so um I stalled on bench for four months honestly same weight every single week "
      "and I was doing everything right or I thought I was. turns out I was adding five pounds whenever "
      "I felt good. which was never. so here's what fixed it. I picked a weight I could do for eight and "
      "I just did the boring thing. every week. small jumps. you don't need a new program you guys, "
      "you need to do the boring thing for longer than feels fun.")
T2 = ("you guys I get this question every day. what's the best ab workout. honestly? there isn't one. "
      "your abs are already there, they're just under a layer you haven't dealt with yet. so eat a little "
      "less, walk a little more, and do the boring thing for twelve weeks. I know it's not sexy. it works. "
      "it's the same boring thing I tell every client and it's the same thing that worked for me.")
T3 = ("stop skipping leg day. I I mean it. honestly I skipped legs for two years and my knees paid for it. "
      "you don't have to squat heavy, you guys. start with goblet squats, three sets of ten, twice a week. "
      "that's it. do the boring thing and in a month the stairs won't feel like a hill anymore. "
      "and your back will thank you too.")
T4 = ("hey guys quick one. creatine. it's not a steroid, it's not gonna make you bloated forever, it's "
      "one of the most studied things in the gym. five grams a day, every day, doesn't matter when. "
      "honestly that's the whole trick. people overthink it. do the boring thing, take it daily, "
      "and stop reading labels at two a.m.")
T5 = ("you guys asked about my morning so here it is. I wake up at six, I drink a big glass of water, "
      "I walk for twenty minutes before I look at my phone. that's it. nothing fancy. honestly the walk "
      "is the part that changed everything for me, my sleep got better, my lifts got better. "
      "do the boring thing first and the rest of the day gets easier.")

POSTS = [
    {"caption": "Bench plateau fix #fitness", "transcript": T1, "likes": 900, "comments": 40, "views": 52000},
    {"caption": "Abs truth", "transcript": T2, "likes": 400, "comments": 20, "views": 31000},
    {"caption": "Leg day!!", "transcript": T3, "likes": 300, "comments": 10, "views": 18000},
    {"caption": "Creatine 101", "transcript": T4, "likes": 700, "comments": 30, "views": 44000},
    {"caption": "Link in bio for my program!! New drop today #gym #grind #coach #fitness #fitfam "
                "this is a written caption with plenty of words but nobody spoke it out loud at all, "
                "it is marketing copy and it should not count as speech in any way shape or form.",
     "transcript": "", "likes": 50, "comments": 2, "views": 99000},
]
MEDIA = [{"transcript": T5, "duration_s": 42, "kind": "video"},
         {"transcript": "this is a photo caption that should be ignored " * 4, "duration_s": 0, "kind": "photo"}]

GOOD_LLM = {
    "paragraph": "You call people 'you guys'. You start with the problem you had. You keep sentences short. "
                 "You say 'honestly' a lot. You end with the same rule: do the boring thing.",
    "phrases": ["do the boring thing", "honestly", "you guys", "unlock your potential"],
    "openers_style": "You open by naming a mistake you made.",
    "closers_style": "You end by repeating the rule.",
    "cta_habit": "none",
    "never_say": ["synergy", "unlock", "synergy"],
}


def _fake(result, calls=None):
    async def fake(**kw):
        if calls is not None:
            calls.append(kw)
        return copy.deepcopy(result), {}
    return fake


def _profile(monkeypatch, result=GOOD_LLM, posts=POSTS, media=MEDIA):
    monkeypatch.setattr(voice.llm, "call", _fake(result))
    return asyncio.run(voice.build_profile("c1", {"niche": "Fitness"}, posts, media))


# --- cleaning -------------------------------------------------------------------------

def test_clean_transcript_drops_fillers_repeats_and_fixes_ends():
    out = voice.clean_transcript("  um so I I   think uh, the the bar   path matters  ")
    assert out == "So I think the bar path matters."
    assert voice.clean_transcript("") == ""
    assert voice.clean_transcript("Already fine!") == "Already fine!"
    assert voice.clean_transcript("drinking umbrella drinks") == "Drinking umbrella drinks."  # 'um' inside a word stays


# --- samples --------------------------------------------------------------------------

def test_spoken_samples_ignore_caption_only_and_keep_uploads():
    s = voice.spoken_samples(POSTS, MEDIA)
    assert len(s) == 5
    assert {x["source"] for x in s} == {"reel", "upload"}
    assert all("Link in bio" not in x["text"] for x in s)
    assert s[0]["views"] == 52000 and s[0]["words"] == len(s[0]["text"].split())
    assert all(voice._MIN_WORDS <= x["words"] <= voice._MAX_WORDS for x in s)


def test_spoken_samples_dedupe_and_length_bounds():
    posts = [{"transcript": T1, "views": 1}, {"transcript": T1.upper(), "views": 2},
             {"transcript": "too short to count", "views": 5}, {"transcript": "word " * 500, "views": 5}]
    s = voice.spoken_samples(posts)
    assert len(s) == 1
    assert voice.spoken_samples(None, None) == []


# --- stats ----------------------------------------------------------------------------

def test_stats_address_contractions_phrases():
    st = voice.compute_stats(voice.spoken_samples(POSTS, MEDIA))
    assert st["n"] == 5
    assert st["address_form"] == "you guys"
    assert st["contraction_rate"] is not None and st["contraction_rate"] > 0.8
    assert any("boring thing" in p for p in st["frequent_phrases"])
    assert st["swearing"] == "none"
    assert len(st["openers"]) == 5 and all(len(o.split()) <= 20 for o in st["openers"])
    assert st["median_sentence_words"] > 0 and st["p90_sentence_words"] >= st["median_sentence_words"]
    assert 0 <= st["fragment_rate"] <= 1 and st["typical_words"] > 40


def test_stats_contraction_rate_none_and_low():
    assert voice.compute_stats([{"text": "Lift heavy weights. Eat good food."}])["contraction_rate"] is None
    low = voice.compute_stats([{"text": "It is simple. Do not skip it. You are fine. I am here."}])
    assert low["contraction_rate"] == 0.0


def test_stats_frequent_phrases_need_two_samples():
    one = voice.compute_stats([{"text": "Protein shakers rattle. Protein shakers rattle loudly."},
                               {"text": "Nothing about that here at all."}])
    assert not any("protein shakers" in p for p in one["frequent_phrases"])
    two = voice.compute_stats([{"text": "Protein shakers rattle."}, {"text": "My protein shakers leak."}])
    assert "protein shakers" in two["frequent_phrases"]
    # stopword-only n-grams never count
    sw = voice.compute_stats([{"text": "and it is in the"}, {"text": "and it is in the"}])
    assert sw["frequent_phrases"] == []


def test_stats_swearing_levels_and_address_default():
    words = ["lift"] * 199
    light = voice.compute_stats([{"text": " ".join(words + ["damn"])}])
    heavy = voice.compute_stats([{"text": "this shit is hard as hell, damn it. fuck."}])
    assert light["swearing"] == "light" and heavy["swearing"] == "heavy"
    assert light["address_form"] == "you"
    assert voice.compute_stats([{"text": "Alright bro, listen. Bro, eat."}])["address_form"] == "bro"
    empty = voice.compute_stats([])
    assert empty["n"] == 0 and empty["address_form"] == "you" and empty["contraction_rate"] is None


# --- exemplars ------------------------------------------------------------------------

def _samples(n):
    return [{"text": f"sample {i} " + "word " * (50 + i), "source": "reel", "views": 1000 - i, "words": 52 + i}
            for i in range(n)]


def test_select_exemplars_deterministic_and_alt_differs():
    pool = _samples(8)
    a = voice.select_exemplars(pool, 3)
    assert a == voice.select_exemplars(list(reversed(pool)), 3)
    assert [x["views"] for x in a] == [1000, 999, 998]
    b = voice.select_exemplars(pool, 3, alt=True)
    assert {x["text"] for x in a}.isdisjoint({x["text"] for x in b})
    small = _samples(4)
    sa, sb = voice.select_exemplars(small, 3), voice.select_exemplars(small, 3, alt=True)
    assert len(sb) == 3 and sa != sb
    assert voice.select_exemplars([], 3, alt=True) == []


def test_select_exemplars_prefers_40_to_220_words():
    long = {"text": "long", "source": "reel", "views": 10**6, "words": 300}
    mid = {"text": "mid", "source": "reel", "views": 10, "words": 90}
    assert voice.select_exemplars([long, mid], 1)[0]["text"] == "mid"


# --- cold start -----------------------------------------------------------------------

def test_cold_start_from_sliders():
    p = voice.cold_start_profile({"niche": "Fitness", "voice": {"funnyToSerious": 0.1, "polishedToRaw": 0.9,
                                                                 "teacherToPeer": 0.5},
                                  "catchphrases": ["Do the boring thing", "do the boring thing", ""],
                                  "non_negotiables": ["no diet talk"]})
    assert p["data_confidence"] == "low"
    assert "joke" in p["paragraph"] and "off the cuff" in p["paragraph"] and "fitness" in p["paragraph"]
    assert p["phrases"] == [{"text": "Do the boring thing", "on": True}]
    assert p["never_say"] == []
    bare = voice.cold_start_profile({})
    assert bare["paragraph"] and bare["version"] == voice.PROFILE_VERSION


# --- build ----------------------------------------------------------------------------

def test_build_profile_validates_phrases_and_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(voice.llm, "call", _fake(GOOD_LLM, calls))
    p = asyncio.run(voice.build_profile("c1", {"niche": "Fitness"}, POSTS, MEDIA))
    assert len(calls) == 1
    kw = calls[0]
    assert kw["route"] == "voice.build" and kw["effort"] == "low" and kw["max_tokens"] == 1600
    assert kw["model"] == voice.flags.writer_model()
    texts = [x["text"] for x in p["phrases"]]
    assert "do the boring thing" in texts and "honestly" in texts
    assert "unlock your potential" not in texts  # never said -> dropped
    assert p["never_say"] == ["synergy", "unlock"]
    assert p["address"] == "you guys" and p["data_confidence"] == "medium"
    assert p["built_from"] == {"reels": 5, "transcribed": 4, "uploads": 1}
    assert 1 <= len(p["exemplars"]) <= 8 and p["paragraph_edited"] is False
    assert p["creator_id"] == "c1" and p["updated_at"].endswith("+00:00")
    for key in ("openers", "closers", "openers_style", "closers_style", "cta_habit", "swearing",
                "typical_words", "corrections", "stats"):
        assert key in p


def test_build_profile_llm_none_still_has_paragraph(monkeypatch):
    p = _profile(monkeypatch, result=None)
    assert "you guys" in p["paragraph"]
    assert p["phrases"] and all(x["on"] for x in p["phrases"])
    assert p["openers_style"] and p["cta_habit"] in ("none", "soft", "explicit")


def test_build_profile_cold_start_when_too_few_samples(monkeypatch):
    calls = []
    monkeypatch.setattr(voice.llm, "call", _fake(GOOD_LLM, calls))
    p = asyncio.run(voice.build_profile("c9", {"niche": "Cooking"}, [{"transcript": T1, "views": 3}], None))
    assert not calls and p["data_confidence"] == "low" and p["creator_id"] == "c9"
    assert p["built_from"] == {"reels": 1, "transcribed": 1, "uploads": 0}


# --- edits / merge ---------------------------------------------------------------------

def test_apply_edit_immutable_and_caps(monkeypatch):
    p = _profile(monkeypatch)
    before = copy.deepcopy(p)
    q = voice.apply_edit(p, paragraph="You talk fast.", never_say=[" synergy ", "Synergy", "leverage"] + [f"w{i}" for i in range(30)],
                         phrases=[{"text": "honestly", "on": False}, {"text": "let's go", "on": True},
                                  {"text": "made up", "on": False}, {"text": "", "on": True}],
                         correction="I never say 'fam'.")
    assert p == before
    assert q["paragraph"] == "You talk fast." and q["paragraph_edited"] is True
    assert {"text": "honestly", "on": False} in q["phrases"]
    assert any(x["text"] == "let's go" and x["on"] for x in q["phrases"])
    assert not any(x["text"] == "made up" for x in q["phrases"])
    assert q["never_say"][:2] == ["synergy", "leverage"] and len(q["never_say"]) == 20
    assert q["corrections"] == ["I never say 'fam'."]
    many = voice.apply_edit(q, phrases=[{"text": f"new {i}", "on": True} for i in range(20)])
    assert len(many["phrases"]) == 12


def test_add_correction_dedupe_and_keep_last_12():
    p = {"corrections": []}
    for i in range(15):
        p = voice.add_correction(p, f"note {i}")
    p = voice.add_correction(p, "NOTE 5")
    assert len(p["corrections"]) == 12 and p["corrections"][-1] == "NOTE 5"
    assert sum(c.lower() == "note 5" for c in p["corrections"]) == 1
    assert len(voice.add_correction({}, "x" * 500)["corrections"][0]) == 200
    assert voice.add_correction({}, "   ")["corrections"] == []


def test_merge_rebuild_keeps_user_edits(monkeypatch):
    old = _profile(monkeypatch)
    old = voice.apply_edit(old, paragraph="Mine.", phrases=[{"text": "honestly", "on": False},
                                                            {"text": "let's go", "on": True}],
                           never_say=["grindset"], correction="Shorter openers.")
    new = _profile(monkeypatch)
    m = voice.merge_rebuild(old, new)
    assert m["paragraph"] == "Mine." and m["paragraph_edited"]
    assert {"text": "honestly", "on": False} in [{"text": x["text"], "on": x["on"]} for x in m["phrases"]]
    assert any(x["text"] == "let's go" for x in m["phrases"])
    assert m["never_say"] == ["grindset"] and m["corrections"] == ["Shorter openers."]
    assert m["exemplars"] == new["exemplars"]
    assert voice.merge_rebuild(None, new) == new
    # unedited LLM never_say is refreshed from the new build
    assert voice.merge_rebuild(new, {**new, "never_say": ["fresh"]})["never_say"] == ["fresh"]


# --- views ----------------------------------------------------------------------------

def test_prompt_block_stable_corrections_verbatim_and_toggles(monkeypatch):
    p = voice.apply_edit(_profile(monkeypatch), phrases=[{"text": "honestly", "on": False}],
                         correction="Don't say 'let's dive in', ever.")
    a, b = voice.prompt_block(p), voice.prompt_block(copy.deepcopy(p))
    assert a == b
    assert voice.prompt_block({**p, "updated_at": "2020-01-01T00:00:00+00:00"}) == a
    assert "Don't say 'let's dive in', ever." in a
    assert '"do the boring thing"' in a and '"honestly"' not in a
    assert "How they start:" in a and "How they end:" in a and "They never say: synergy" in a
    assert not a.startswith(voice._LOW_NOTE)
    assert voice.prompt_block(None) == "" and voice.prompt_block({}) == ""
    low = voice.prompt_block(voice.cold_start_profile({"niche": "Fitness"}))
    assert low.startswith(voice._LOW_NOTE)


def test_prompt_block_budget_keeps_corrections(monkeypatch):
    p = _profile(monkeypatch)
    p = voice.apply_edit(p, paragraph=" ".join(f"You do thing number {i}." for i in range(80)),
                         correction="Keep my swearing.")
    out = voice.prompt_block(p, max_chars=1400)
    assert len(out) <= 1400 and "Keep my swearing." in out


def test_lint_profile_and_public_view(monkeypatch):
    p = _profile(monkeypatch)
    lp = voice.lint_profile(p)
    assert lp["address_form"] == "you guys" and lp["exemplar_texts"] == [e["text"] for e in p["exemplars"][:8]]
    assert voice.lint_profile({"address": "you"})["address_form"] is None
    assert voice.lint_profile(None) == {"address_form": None, "exemplar_texts": []}
    v = voice.public_view(p)
    assert set(v) == {"paragraph", "phrases", "openers", "closers", "address", "swearing", "typical_words",
                      "never_say", "corrections", "confidence", "built_from", "updated_at", "version"}
    assert v["confidence"] == "medium" and "exemplars" not in v
    assert voice.public_view(None) == {}


# --- storage --------------------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.rows = {}

    async def upsert_reels_cache(self, key, entry):
        self.rows[key] = copy.deepcopy(entry)
        return True

    async def load_reels_cache(self, key):
        return copy.deepcopy(self.rows.get(key))


class BrokenStore:
    async def upsert_reels_cache(self, key, entry):
        raise RuntimeError("db down")

    async def load_reels_cache(self, key):
        raise RuntimeError("db down")


def test_load_save_roundtrip_and_keyless(monkeypatch):
    p = _profile(monkeypatch)
    store = FakeStore()
    assert asyncio.run(voice.save(store, "c1", p)) is True
    assert list(store.rows) == ["voice:c1"]
    assert asyncio.run(voice.load(store, "c1")) == p
    assert asyncio.run(voice.load(store, "nobody")) is None
    assert asyncio.run(voice.load(None, "c1")) is None
    assert asyncio.run(voice.save(None, "c1", p)) is False
    assert asyncio.run(voice.load(BrokenStore(), "c1")) is None
    assert asyncio.run(voice.save(BrokenStore(), "c1", p)) is False


# --- correction from edit ---------------------------------------------------------------

def test_correction_from_edit_skips_trivial_without_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(voice.llm, "call", _fake({"correction": "x"}, calls))
    for before, after in (("Here's why it works.", "here's why it works"), ("same", "same"),
                          ("", "something"), ("something", "  ")):
        assert asyncio.run(voice.correction_from_edit(before, after)) is None
    assert calls == []


def test_correction_from_edit_calls_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(voice.llm, "call", _fake({"correction": "Changed 'here's why' to 'and like'."}, calls))
    out = asyncio.run(voice.correction_from_edit("Here's why it works.", "And like, it works." + "x" * 900))
    assert out == "Changed 'here's why' to 'and like'."
    kw = calls[0]
    assert kw["route"] == "voice.correction" and kw["max_tokens"] == 200
    assert kw["model"] == voice.flags.helper_model()
    assert len(kw["messages"][0]["content"][0]["text"]) < 700
    monkeypatch.setattr(voice.llm, "call", _fake({"correction": ""}))
    assert asyncio.run(voice.correction_from_edit("I lift.", "I squat.")) is None
    monkeypatch.setattr(voice.llm, "call", _fake(None))
    assert asyncio.run(voice.correction_from_edit("I lift.", "I squat.")) is None


# --- style negatives (gated) ------------------------------------------------------------

def test_style_negatives_skipped_when_env_unset(monkeypatch):
    monkeypatch.delenv("VOICE_NEGATIVES", raising=False)
    calls = []
    monkeypatch.setattr(voice.llm, "call", _fake({"draft": "d", "why": "w"}, calls))
    p = asyncio.run(voice.build_profile("c1", {}, POSTS, MEDIA))
    assert "negatives" not in p and len(calls) == 1
    assert asyncio.run(voice.style_negatives(p, voice.spoken_samples(POSTS, MEDIA))) == []
    assert len(calls) == 1


def test_style_negatives_when_enabled_requires_real_quote(monkeypatch):
    monkeypatch.setenv("VOICE_NEGATIVES", "1")
    samples = voice.spoken_samples(POSTS, MEDIA)
    prof = {**voice.cold_start_profile({}), "exemplars": samples[:2]}

    async def fake(**kw):
        if "draft" in kw["schema"]["properties"]:
            return {"draft": "Let's dive into creatine today."}, {}
        real = kw["messages"][0]["content"][0]["text"]
        ok = "do the boring thing" in real.lower()
        return {"why": 'Too polished; they say "do the boring thing".' if ok else 'Says "synergy" a lot.'}, {}

    monkeypatch.setattr(voice.llm, "call", fake)
    negs = asyncio.run(voice.style_negatives(prof, samples))
    assert 1 <= len(negs) <= 3 and all("do the boring thing" in n["why"] for n in negs)
    block = voice.prompt_block({**prof, "negatives": negs})
    assert "Drafts that missed their voice, and why:" in block
    assert block.count("\n- ") <= 2
