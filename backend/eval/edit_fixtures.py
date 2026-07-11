"""Edit-eval fixture takes — word-timing JSON for the EDL invariant + scorecard suite.

These are the "8–12 real takes" the plan (docs/PLAN-AI-EDITOR.md Phase 5a) calls for,
represented the way the pipeline actually consumes a take: an AssemblyAI-shaped word
list (`{word, start_ms, end_ms, type?}`). The source *videos* live in a Supabase `eval`
bucket keyed by `source_key`; only the word timings are checked in here (that is all the
KEYLESS invariant suite needs — brief→plan→assembler runs on transcript timings, not
pixels). The live scorecard (edl_eval.py --live) resolves `source_key` to a signed URL.

Takes are laid out deterministically by `_take()` so the fixtures are byte-stable across
runs (no Date/random — same constraint as the rest of this repo). Five categories per the
plan: scripted / rambling / listicle / low-energy / buried-hook. `hook_ms` marks where the
real hook lands (0 for well-structured takes; late for the buried-hook take the editor is
supposed to pull forward).
"""
from __future__ import annotations

# 30fps everywhere (ms_to_frame divides by 33.3667). Words are laid out at a steady
# cadence with explicit filler tokens (type="filler") and dead-air gaps so the fixtures
# exercise strip_fillers + the dead-air drops + the min-clip guard.

def _take(rows: list[tuple], wpm: float = 150.0) -> list[dict]:
    """Build a word list from (text, kind) rows.

    kind: "" normal word · "filler" a disfluency (tagged type=filler like AssemblyAI's
    disfluencies model) · "gap:N" insert an N-ms dead-air gap BEFORE this word (silence).
    Cadence: 60000/wpm ms per word, 40ms inter-word. Deterministic, no wall clock.
    """
    per = 60000.0 / wpm
    t = 0.0
    out: list[dict] = []
    for text, kind in rows:
        if isinstance(kind, str) and kind.startswith("gap:"):
            t += float(kind.split(":", 1)[1])
            kind = ""
        start = int(round(t))
        end = int(round(t + per))
        w = {"word": text, "start_ms": start, "end_ms": end}
        if kind == "filler":
            w["type"] = "filler"
        out.append(w)
        t = end + 40.0
    return out


def _sentence(text: str, kind_map: dict | None = None) -> list[tuple]:
    """Split a plain sentence into (word, "") rows; kind_map overrides by index."""
    words = text.split()
    kind_map = kind_map or {}
    return [(w, kind_map.get(i, "")) for i, w in enumerate(words)]


# --- Take 1: scripted — tight, hook-first, minimal filler ---------------------
_SCRIPTED = _take(
    _sentence("Here is the one mistake that is quietly killing your reach on every single post you make")
    + _sentence("The algorithm rewards watch time not likes so your first three seconds decide everything")
    + [("um", "filler")]
    + _sentence("Stop opening with a slow intro and lead with the payoff instead")
    + _sentence("Do that and your retention curve stops falling off a cliff at second two")
)

# --- Take 2: rambling — heavy filler, false starts, dead air ------------------
_RAMBLING = _take(
    [("so", "filler"), ("um", "filler")]
    + _sentence("I wanted to kind of talk about the thing with posting")
    + [("like", "filler")]
    + _sentence("basically what happens is people give up way too early on their content")
    + [("uh", "gap:900")]
    + _sentence("and you know the trick is you just have to keep showing up consistently")
    + [("um", "filler"), ("like", "filler")]
    + _sentence("that is honestly the whole secret nobody wants to hear it")
)

# --- Take 3: listicle — enumerated, punchy, natural b-roll cues ---------------
_LISTICLE = _take(
    _sentence("Three tools that replaced my entire editing workflow this year")
    + _sentence("Number one a teleprompter app so I never lose my place on camera")
    + _sentence("Number two an auto caption tool that syncs every word perfectly")
    + _sentence("Number three a b roll library so every claim has a visual behind it")
    + _sentence("Save this before you forget all three")
)

# --- Take 4: low-energy — slow cadence, long pauses, flat delivery ------------
_LOW_ENERGY = _take(
    _sentence("today I want to share something I have been thinking about for a while")
    + [("well", "gap:1400")]
    + _sentence("it is that rest is actually part of the work not the opposite of it")
    + [("honestly", "gap:1100")]
    + _sentence("we treat recovery like a reward when it is really the foundation"),
    wpm=110.0,
)

# --- Take 5: buried-hook — the strong line lands late, editor should pull it up -
_BURIED_HOOK = _take(
    _sentence("okay so let me just get set up here and find my notes real quick")
    + [("um", "filler")]
    + _sentence("I guess I should start by introducing myself and what this channel is about")
    + [("gap:800", "")]
    + _sentence("but here is the part that actually matters I doubled my income in ninety days")
    + _sentence("by doing one boring thing every morning before anyone else was awake"),
    wpm=145.0,
)


FIXTURES: list[dict] = [
    {"id": "scripted-01", "category": "scripted", "style": "talking_head",
     "source_key": "eval/scripted-01.mp4", "hook_ms": 0, "words": _SCRIPTED},
    {"id": "rambling-01", "category": "rambling", "style": "talking_head",
     "source_key": "eval/rambling-01.mp4", "hook_ms": 0, "words": _RAMBLING},
    {"id": "listicle-01", "category": "listicle", "style": "faceless",
     "source_key": "eval/listicle-01.mp4", "hook_ms": 0, "words": _LISTICLE},
    {"id": "low-energy-01", "category": "low-energy", "style": "talking_head",
     "source_key": "eval/low-energy-01.mp4", "hook_ms": 0, "words": _LOW_ENERGY},
    {"id": "buried-hook-01", "category": "buried-hook", "style": "talking_head",
     "source_key": "eval/buried-hook-01.mp4",
     # the real hook ("I doubled my income…") lands well after the intro throat-clearing
     "hook_ms": next((w["start_ms"] for w in _BURIED_HOOK if w["word"] == "doubled"), 6000),
     "words": _BURIED_HOOK},
]


def fixture(fid: str) -> dict:
    for f in FIXTURES:
        if f["id"] == fid:
            return f
    raise KeyError(fid)


def take_total_frames(words: list[dict]) -> int:
    from app.edl import ms_to_frame
    if not words:
        return 1
    return ms_to_frame(words[-1]["end_ms"]) + 1
