"""Talking-head gate: speech checks + the model speech label (2026-09-23).

A prod sweep of 140 cached, transcribed reels found song lyrics, "um um um" filler,
workout countdowns and non-English takes passing the talking-head gate. The strings
below are shortened versions of those real transcripts.
"""
import asyncio

import pytest

import main

TH = ("If you sit at a desk all day, your hips get tight and your back starts to round, and "
      "that's why your squat feels awful the first time you try it. Here's what I'd do instead. "
      "Before you load anything heavy, spend five minutes opening your hips up with a couple of "
      "slow lunges and a deep squat hold. Try it for two weeks and tell me how it feels.")


def _reel(transcript, **kw):
    return {"id": "r", "video_url": "https://cdn/v.mp4", "transcribed": True,
            "edit_format": "talking_head", "duration_s": 30, "transcript": transcript, **kw}


@pytest.mark.parametrize("name,transcript", [
    ("chorus", "I hit him with, I hit him with, I hit him with, I hit him with, I hit him with, "
               "I hit him with, I hit him with a left and a right and a left and a right."),
    ("filler", " ".join(["um"] * 37)),
    ("portuguese_lyrics", "No baile é cagão, mina linda perigosa, caga meu coração. Vai peidando, "
                          "vai cagando, no baile é cagão, mina linda perigosa, caga meu coração."),
    ("spanish_take", "Hola, amores. Hoy quiero compartir con vosotros cómo me preparo para ir al "
                     "gimnasio por la mañana antes de trabajar, con un desayuno rápido y fácil."),
    ("countdown", "You ready? 5, 4, 3, 2, 1, go! Round 1. 3, 2, 1, stop! Round 2. 5, 4, 3, 2, 1, "
                  "go! Round 3. 3, 2, 1, stop! Round 4. 5, 4, 3, 2, 1, go! Halfway there."),
])
def test_non_speech_transcripts_fail_the_gate(name, transcript):
    assert main._speech_red_flag(transcript), name
    assert main._is_talking_head_reel(_reel(transcript)) is False, name


def test_a_real_talking_head_passes():
    assert main._speech_red_flag(TH) is None
    assert main._is_talking_head_reel(_reel(TH)) is True


@pytest.mark.parametrize("kind,expected", [("talking", True), ("lyrics", False),
                                           ("skit", False), ("other", False), ("", True)])
def test_model_speech_label_overrides(kind, expected):
    assert main._is_talking_head_reel(_reel(TH, speech_kind=kind)) is expected


def test_classifier_labels_in_place_and_skips_labeled(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    from app import palo_llm
    seen = []

    async def fake(sys, user, schema, model, max_tokens=4000, temperature=None):
        assert temperature == 0                                # labels must be stable
        seen.append(user)
        return {"items": [{"i": 0, "kind": "lyrics"}, {"i": 1, "kind": "talking"},
                          {"i": 7, "kind": "skit"}]}          # out-of-range index ignored

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", fake)
    posts = [{"transcript": "some say you will love me one day"}, {"transcript": TH},
             {"transcript": TH, "speech_kind": "talking"},     # already labeled: not re-sent
             {"transcript": ""}]                               # nothing to judge
    asyncio.run(main._classify_reel_speech(posts))
    assert [p.get("speech_kind") for p in posts] == ["lyrics", "talking", "talking", None]
    assert len(seen) == 1 and "[0]" in seen[0] and "[1]" in seen[0] and "[2]" not in seen[0]


def test_classifier_failure_leaves_posts_unlabeled(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    from app import palo_llm

    async def boom(*a, **k):
        return None                                            # the helper never raises

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", boom)
    posts = [{"transcript": TH}]
    asyncio.run(main._classify_reel_speech(posts))
    assert "speech_kind" not in posts[0]


def test_classifier_keyless_is_a_noop(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    posts = [{"transcript": TH}]
    asyncio.run(main._classify_reel_speech(posts))
    assert "speech_kind" not in posts[0]


def test_label_carries_forward_with_the_transcript(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example.co")
    post = {"platform": "instagram", "author": "a", "id": "1"}
    rid = main._reel_public_id(post, "a", "instagram", 0)
    prev = [{"id": rid, "transcribed": True, "transcript": TH, "speech_kind": "lyrics"}]
    main._merge_prev_reel_work([post], prev)
    assert post["transcript"] == TH and post["speech_kind"] == "lyrics"
    served = main._reel_from_post(post, "a", "instagram", 0, False)
    assert served["speech_kind"] == "lyrics" and main._is_talking_head_reel(served) is False
