"""First-person claim detector for creator scripts (2026-09-23).

The creator reads every script on camera, so an invented personal event or result ("I
tracked my lifts for 90 days", "we killed custom pricing and revenue went up") makes them
lie in public. The realism eval found these in 4 of 23 unjudged first-paint drafts.

What is NOT a claim: first-person opinions, methods and habits ("here's what I'd do",
"I think", "I always tell people"), and second-person stories ("you open the fridge").
What IS: a past-tense event or result the creator supposedly lived, a named client, or a
personal before/after number. Pure + deterministic; tuned for precision (a false
positive costs a repair call, a miss costs honesty).
"""
from __future__ import annotations

import re

_EVENT_VERBS = (
    "tracked|tried|tested|lost|gained|spent|saved|paid|dropped|doubled|tripled|killed|"
    "launched|grew|quit|started|stopped|switched|cut|raised|hired|fired|sold|bought|signed|"
    "landed|closed|ran|hit|went|made|built|failed|finished|posted|filmed|moved|"
    "increased|decreased|cleared|paid off|deleted|replaced"
)
# "I/we + (adverb) + past-tense event verb", e.g. "I tracked", "we finally killed", "I just hit".
_I_EVENT = re.compile(
    r"\b(?:i|we)\s+(?:(?:finally|just|once|actually|literally|recently|then|also|even)\s+)?"
    r"(?:" + _EVENT_VERBS + r")\b", re.I)
# "I went from 185 to 225", "my revenue went from", "our sales doubled".
_FROM_TO = re.compile(r"\b(?:i|we|my|our)\b[^.!?\n]{0,40}\bfrom\s+\$?\d[\d,.]*k?\b[^.!?\n]{0,20}\bto\s+\$?\d", re.I)
_OUR_RESULT = re.compile(
    r"\b(?:my|our)\s+(?:revenue|sales|income|savings|net worth|followers|views|clients?|"
    r"customers|business|numbers|results?|deadlift|squat|bench|weight|body ?fat|credit score|"
    r"debt|portfolio)\s+(?:went|grew|doubled|tripled|jumped|dropped|hit|is now|was)\b", re.I)
_MY_CLIENT = re.compile(r"\b(?:my|our|a)\s+(?:client|clients|customer|patient|student)\s+"
                        r"(?:told|said|came|lost|gained|went|hit|asked|had|was)\b", re.I)
_TIMEFRAME = re.compile(r"\b(?:i|we)\b[^.!?\n]{0,30}\bfor\s+(?:\d+|a|one|two|three|six|thirty|ninety)\s+"
                        r"(?:days?|weeks?|months?|years?)\b", re.I)
_LAST_TIME = re.compile(r"\b(?:last|this past)\s+(?:week|month|year|summer|winter)\b[^.!?\n]{0,40}\b(?:i|we)\s+\w+ed\b", re.I)

# Opinions / methods / hypotheticals that share surface words with events.
_SAFE = re.compile(
    r"\b(?:i'd|i would|i will|i'll|i think|i always|i never|i usually|i tell|i see|i want|i mean|"
    r"if i|when i coach|i recommend|i'm not|i get it|i know)\b", re.I)


def flag_first_person_claim(text: str) -> str | None:
    """Return the first invented-sounding first-person event/result phrase, or None."""
    if not text:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = sentence.strip()
        if not s:
            continue
        for rx in (_FROM_TO, _OUR_RESULT, _MY_CLIENT, _TIMEFRAME, _LAST_TIME, _I_EVENT):
            m = rx.search(s)
            if not m:
                continue
            # "If I tried…", "I'd cut…": a hypothetical/opinion right at the match isn't a claim.
            window = s[max(0, m.start() - 12):m.end()]
            if rx is _I_EVENT and _SAFE.search(window):
                continue
            return m.group(0)
    return None


def script_claims(script: dict) -> str | None:
    """First-person claim anywhere in a script's spoken copy (hook, body, cta)."""
    for field in ("hook", "body", "cta"):
        v = script.get(field)
        hit = flag_first_person_claim(v if isinstance(v, str) else "")
        if hit:
            return hit
    return None
