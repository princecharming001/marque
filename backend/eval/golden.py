"""The golden set (docs/07-ai-system.md §8.5).

Three things live here:
  - CASES      : generation fixtures (brand × pillar × style) the live harness
                 generates against and then scores.
  - KNOWN_GOOD : hand-written scripts that MUST pass every gate with zero quality
                 flags. If the invariants ever fail these, the harness is broken.
  - KNOWN_BAD  : scripts that MUST be caught, each annotated with the check that
                 should catch it. This is the regression tripwire — it proves the
                 gate actually rejects slop/banned/invalid output.
"""
from __future__ import annotations

_FIT_BRAND = {
    "niche": "fitness for busy parents", "audience": "overwhelmed parents",
    "known_for": "15-minute home workouts", "what_you_do": "coach at-home fitness",
    "goal": "Grow my audience", "voice": {"funnyToSerious": 0.4, "polishedToRaw": 0.7},
    "non_negotiables": ["shredded", "no pain no gain"],
    "catchphrases": ["small reps, big life"],
}
_FIN_BRAND = {
    "niche": "personal finance", "audience": "first-time investors",
    "known_for": "plain-English money advice", "what_you_do": "explain investing",
    "goal": "Grow my audience", "voice": {"funnyToSerious": 0.6, "polishedToRaw": 0.4},
    "non_negotiables": ["get rich quick", "guaranteed returns"],
}

# Posts the live generator can mine for verbatim voice exemplars.
_FIT_POSTS = [
    {"caption": "Stop counting reps. Count the days you showed up.", "likes": 900, "comments": 60},
    {"caption": "Your kids don't need a perfect parent. They need one who moves.", "likes": 700, "comments": 30},
]

CASES = [
    {"id": "fit-myth-talkinghead", "brand": _FIT_BRAND, "posts": _FIT_POSTS,
     "pillar": "Myth-busting", "pillar_summary": "Correct bad fitness advice",
     "pillar_angle": "You debunk gym-bro myths for time-poor parents",
     "style": "talking_head", "count": 2},
    {"id": "fin-listicle-faceless", "brand": _FIN_BRAND, "posts": [],
     "pillar": "Teach the fundamentals", "pillar_summary": "Explain one money idea",
     "pillar_angle": "You make one investing concept click in 30s",
     "style": "faceless", "count": 2},
    {"id": "fit-hottake-greenscreen", "brand": _FIT_BRAND, "posts": _FIT_POSTS,
     "pillar": "Hot takes", "pillar_summary": "Stake a contrarian position",
     "pillar_angle": "You argue against a popular fitness trend",
     "style": "green_screen", "count": 2},
]

KNOWN_GOOD = [
    {"brand": _FIT_BRAND, "script": {
        "title": "The 15-minute lie", "summary": "Debunk the hour-long-workout myth.",
        "hook": "You don't need an hour. You need 15 honest minutes, 4 times a week.",
        "hookSignal": "contrarian", "formatId": "myth-buster",
        "body": "Everyone sells the 60-minute grind. For a parent that's a fantasy. Here's the 15-minute block that actually moves the needle: 5 squats, 5 push-ups, 5 rows, repeat.",
        "cta": "Save this for tomorrow morning.", "predictedScore": 82, "style": "talking_head"}},
    {"brand": _FIN_BRAND, "script": {
        "title": "Index funds, plainly", "summary": "One concept, 30 seconds.",
        "hook": "An index fund is just buying a tiny slice of 500 companies at once.",
        "hookSignal": "authority", "formatId": "faceless",
        "body": "Instead of betting on one stock, you own a sliver of all of them. When the market grows, you grow with it. That's it. No stock-picking, no timing.",
        "cta": "Follow for the next one.", "predictedScore": 78, "style": "faceless"}},
]

# Each: script + the check name(s) it must trip.
KNOWN_BAD = [
    {"why": "fabricated client testimonial", "expect_flag": "ungrounded receipt", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "My client Sarah lost 40 pounds in 12 weeks with this.",
        "hookSignal": "authority", "formatId": "before-after",
        "body": "She did exactly what I'm about to show you and the results were undeniable.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "fabricated personal experiment", "expect_flag": "ungrounded receipt", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "I tested 5 diets for 30 days and tracked every macro.",
        "hookSignal": "specificity", "formatId": "myth-buster",
        "body": "The results shocked me and they'll shock you too — here's what happened.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "slop opener", "expect_flag": "slop opener", "gate_ok": True,
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "In this video I'll show you three workouts.",
        "hookSignal": "curiosity", "formatId": "myth-buster",
        "body": "Some body text that is definitely long enough.", "cta": "Follow me.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "banned phrase in body", "expect_gate": "no_banned_phrase",
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "The one move that fixes your posture in a week.",
        "hookSignal": "stakes", "formatId": "myth-buster",
        "body": "No pain no gain — push until it hurts and you'll get shredded.", "cta": "Follow.",
        "predictedScore": 80, "style": "talking_head"}},
    {"why": "invalid format", "expect_gate": "format_valid",
     "brand": _FIN_BRAND, "script": {
        "title": "x", "hook": "Three money habits that quietly compound.",
        "hookSignal": "specificity", "formatId": "totally-made-up",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Save this.",
        "predictedScore": 70, "style": "faceless"}},
    {"why": "score out of range", "expect_gate": "score_in_range",
     "brand": _FIN_BRAND, "script": {
        "title": "x", "hook": "The compounding trick banks won't advertise.",
        "hookSignal": "curiosity", "formatId": "listicle",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Save this.",
        "predictedScore": 140, "style": "faceless"}},
    {"why": "empty hook", "expect_gate": "has_hook",
     "brand": _FIT_BRAND, "script": {
        "title": "x", "hook": "", "hookSignal": "stakes", "formatId": "myth-buster",
        "body": "A perfectly reasonable body of at least twelve characters.", "cta": "Follow.",
        "predictedScore": 60, "style": "talking_head"}},
]
