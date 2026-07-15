"""Deterministic, key-free quality invariants for generated scripts.

This is the publish-gate + structure floor from docs/07-ai-system.md §8.2/§8.5:
concrete, citable checks — not vibes — that run in CI without an API key. They are
the regression spine. A prompt or schema change that starts emitting AI-slop hooks,
banned phrases, invalid formats, or fake scores fails HERE, before it ships.

Two tiers:
  - GATE checks  → structural + publish-safety. A failure means "do not ship."
  - QUALITY flags → craft signals (slop opener, question opener, stacked CTA).
    Tracked as a rate; a spike is a regression even when nothing hard-fails.
"""
from __future__ import annotations

from prompts import ACTIVE_STYLES, FORMAT_IDS, SIGNALS

SIGNAL_SET = {s.strip() for s in SIGNALS.strip("[]").split(",")}

# AI-tell hook openers — mirror the slop list the runtime judge uses
# (prompts.script_judge_prompt / hook_judge_prompt) so offline + online agree.
SLOP_OPENERS = (
    "in this video", "in today's video", "in todays video", "let me tell you",
    "here's the thing", "heres the thing", "ever wondered", "picture this",
    "buckle up", "welcome back", "hey guys", "what's up", "whats up",
    "let's dive in", "lets dive in", "without further ado", "today i want to talk",
)
CTA_TOKENS = ("follow", "save", "comment", "share", "subscribe", "link in bio", "dm ")


def _s(script: dict, key: str) -> str:
    v = script.get(key, "")
    return v.strip() if isinstance(v, str) else ""


# --- GATE checks: (name, fn(script, brand) -> (ok: bool, reason: str)) ----------

def _has_hook(sc, brand):
    h = _s(sc, "hook")
    return (len(h) >= 8, "hook missing or too short" if len(h) < 8 else "")


def _has_body(sc, brand):
    return (len(_s(sc, "body")) >= 12, "body missing or too short")


def _has_cta(sc, brand):
    return (len(_s(sc, "cta")) >= 3, "cta missing")


def _format_valid(sc, brand):
    f = sc.get("formatId", "")
    return (f in FORMAT_IDS, f"formatId '{f}' not in FORMAT_IDS")


def _style_valid(sc, brand):
    st = sc.get("style", "")
    return (st in ACTIVE_STYLES, f"style '{st}' not an active style")


def _signal_valid(sc, brand):
    sig = sc.get("hookSignal", "")
    return (sig in SIGNAL_SET, f"hookSignal '{sig}' not a known signal")


def _score_in_range(sc, brand):
    v = sc.get("predictedScore", None)
    ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100
    return (ok, f"predictedScore '{v}' not an int in 0..100")


def _no_banned_phrase(sc, brand):
    banned = [b.strip().lower() for b in (brand.get("non_negotiables") or []) if b.strip()]
    hay = " ".join(_s(sc, k) for k in ("hook", "body", "cta")).lower()
    hit = next((b for b in banned if b and b in hay), None)
    return (hit is None, f"uses banned phrase '{hit}'")


GATE_CHECKS = [
    ("has_hook", _has_hook), ("has_body", _has_body), ("has_cta", _has_cta),
    ("format_valid", _format_valid), ("style_valid", _style_valid),
    ("signal_valid", _signal_valid), ("score_in_range", _score_in_range),
    ("no_banned_phrase", _no_banned_phrase),
]


# --- QUALITY flags: craft regressions (rate-tracked, not hard-gated) -------------

def _flag_slop(sc, brand):
    h = _s(sc, "hook").lower()
    return next((f"slop opener: '{o}'" for o in SLOP_OPENERS if h.startswith(o)), None)


def _flag_question_opener(sc, brand):
    # VIRALITY_BLOCK: "Question-openers underperform statements."
    return "hook is a question" if _s(sc, "hook").endswith("?") else None


def _flag_stacked_cta(sc, brand):
    cta = _s(sc, "cta").lower()
    n = sum(1 for t in CTA_TOKENS if t in cta)
    return "stacked CTA (>1 ask)" if n > 1 else None


import re as _re
_UNGROUNDED_VERB = _re.compile(
    r"\bI (tracked|tested|tried|ran|made|earned|lost|gained|spent|posted|coached|helped|built|grew)\b[^.!?]*\d",
    _re.I)
_CLIENT_RECEIPT = _re.compile(r"\bmy client\b[^.!?]*(\$|\d)", _re.I)


def _flag_ungrounded_receipt(sc, brand):
    """W3: a first-person past-tense claim with a number, or a client-testimonial with a
    figure — a fabricated personal receipt. Suppressed when a bracketed fill-in is present."""
    for field in ("hook", "body"):
        text = _s(sc, field)
        for sentence in _re.split(r"(?<=[.!?])\s", text):
            if "[" in sentence:
                continue
            if _UNGROUNDED_VERB.search(sentence) or _CLIENT_RECEIPT.search(sentence):
                return "ungrounded receipt"
    return None


def _flag_wall_of_text(sc, brand):
    """B-3: a long body with no paragraph break reads as an unfilmable wall of text.
    Flag bodies over ~40 words that contain no blank-line separator."""
    body = _s(sc, "body")
    if len(body.split()) > 40 and "\n\n" not in body and "\n" not in body:
        return "wall of text (no paragraph breaks)"
    return None


def _flag_stage_direction(sc, brand):
    """The body must be the words the creator SAYS, not a description of what to say.
    Shares the exact runtime lint (prompts.flag_stage_direction) so the eval tracks the
    same rule the pipeline enforces."""
    try:
        from prompts import flag_stage_direction
    except Exception:
        return None
    return flag_stage_direction(_s(sc, "body"), _s(sc, "style"))


# B4: cheap deterministic relevance FLOOR — the live judge's relevance_to_creator axis
# is the real signal (this is a soft backstop for the golden-fixture tripwire and prod
# batch monitoring, not a hard gate: synonyms legitimately evade naive token overlap,
# e.g. "budgeting" vs "personal finance" share zero literal terms).
_STOPWORDS = frozenset((
    "the", "a", "an", "and", "or", "but", "for", "with", "about", "your", "you", "this",
    "that", "from", "into", "onto", "their", "them", "they", "how", "what", "why", "when",
    "who", "which", "its", "of", "to", "in", "on", "at", "is", "are", "was", "were", "be",
    "been", "being", "do", "does", "did", "not", "no", "yes", "get", "got", "one", "two",
    "three", "here", "there", "just", "like", "will", "would", "could", "should", "have",
    "has", "had", "more", "most", "some", "any", "all", "if", "so", "than",
))
_WORD_RE = _re.compile(r"[a-zA-Z']+")
_STEM_SUFFIXES = ("ing", "ers", "er", "es", "ed", "s")


def _content_terms(text: str) -> set[str]:
    terms = set()
    for w in _WORD_RE.findall((text or "").lower()):
        w = w.strip("'")
        if len(w) < 4 or w in _STOPWORDS:
            continue
        stem = w
        for suf in _STEM_SUFFIXES:
            if stem.endswith(suf) and len(stem) - len(suf) >= 3:
                stem = stem[:-len(suf)]
                break
        terms.add(stem)
    return terms


def _flag_offbrand(sc, brand):
    brand_text = " ".join(str(brand.get(k, "")) for k in ("niche", "known_for", "what_you_do"))
    brand_terms = _content_terms(brand_text)
    if not brand_terms:
        return None   # nothing to compare against -> never a false positive
    script_terms = _content_terms(_s(sc, "hook") + " " + _s(sc, "body"))
    if brand_terms & script_terms:
        return None
    return "offbrand: no niche/known-for/what-you-do term overlap"


QUALITY_FLAGS = [_flag_slop, _flag_question_opener, _flag_stacked_cta, _flag_ungrounded_receipt,
                 _flag_wall_of_text, _flag_stage_direction, _flag_offbrand]


def evaluate_script(script: dict, brand: dict | None = None) -> dict:
    """Run every invariant on one script. Returns gate pass/fail + quality flags."""
    brand = brand or {}
    failures = []
    for name, fn in GATE_CHECKS:
        try:
            ok, reason = fn(script, brand)
        except Exception as e:                       # a malformed field is itself a failure
            ok, reason = False, f"{name} raised {type(e).__name__}"
        if not ok:
            failures.append(f"{name}: {reason}")
    flags = [f for f in (fn(script, brand) for fn in QUALITY_FLAGS) if f]
    return {"gate_passed": not failures, "failures": failures, "quality_flags": flags}


def evaluate_batch(scripts: list[dict], brand: dict | None = None) -> dict:
    """Aggregate scorecard over a batch: gate pass-rate + slop/quality-flag rate."""
    results = [evaluate_script(s, brand) for s in scripts]
    n = len(results) or 1
    passed = sum(1 for r in results if r["gate_passed"])
    flagged = sum(1 for r in results if r["quality_flags"])
    return {
        "n": len(results),
        "gate_pass_rate": round(passed / n, 3),
        "quality_flag_rate": round(flagged / n, 3),
        "results": results,
    }
