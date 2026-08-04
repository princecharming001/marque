"""R2 (decider) — daily diagnosis → the RIGHT response type for each signal.

Ported from Palo's pulse layer (gaps-suggestions #3): pulse/decide.py (the LLM
judgment layer whose in-code system prompt IS prod — no LD flag exists for it),
pulse/vitals.py (the deterministic sensor thresholds), and pulse/briefing.py
(hero preference + provenance shaping). The point: a weak video in a proven
bucket means the EXECUTION leaked → review it, don't generate more ideas; a
failing format → ideas back in a proven format; a cooling subject on a strong
format → keep the format, swap the subject; an overperformer → ride it now (or
revive a fitting saved brief). This is what makes suggestions read as a
strategist instead of a slot machine — and it rations spend (≤3 decisions/day).

Wiring contract (all plain dicts, no main.py imports):
  build_candidates(arms, posts, briefs)  — pure sensors → grounded CandidateSignals
  decide(store, creator_id, candidates)  — ≤3 decisions, destination FORCED in code
  shape_briefing(decisions, briefs)      — pure Today shaping (hero + lanes)

Keyless-green: no key ⇒ anthropic_cached_json returns None ⇒ silent day; empty
candidates ⇒ early-return silent day WITHOUT an LLM call; any failure or parse
garbage ⇒ {"decisions": []} — a silent day is always a safe degrade. Flag
DECIDER gates the LLM entry point; the pure helpers are always callable.
"""
from __future__ import annotations

import json
import logging
import re

from app import ai_usage, palo_flags
from app.palo_llm import CACHE_BREAKPOINT, anthropic_cached_json
from app.prompt_store import get_prompt
from prompts import SONNET

# ── vitals thresholds (ported constants — Palo pulse/vitals.py:37-55) ─────────
WEAK_LIFT_MAX = 0.6         # bucket lift ≤ this ⇒ "weak"
BREAKOUT_LIFT_MIN = 2.0     # bucket lift ≥ this ⇒ breakout
WEAK_MIN_SAMPLE = 3         # weakest needs ≥3 samples — a 1-video 0.44x bucket is
                            # a retest question, not "the channel's weakest"
DECISIVE_NEG_MIN_N = 5      # decisive_negative: 0 of n≥5 beat baseline
VIEW_SPIKE_MULT = 2.0       # fresh post ≥ 2x the recent median ⇒ spike
VIEW_SPIKE_AGE_DAYS = 10    # "fresh" = published within this window
GAP_FACTOR = 2.0            # posting_gap fires at usual_gap × 2 …
GAP_FLOOR_DAYS = 4          # … but never nag a channel only a couple days quiet


def median_views(vals: list[float]) -> float:
    """Median of >0 values — port of vitals._median (the ONE-median rule's core)."""
    xs = sorted(v for v in vals if v and v > 0)
    if not xs:
        return 0.0
    n = len(xs)
    if n % 2 == 1:
        return float(xs[n // 2])
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def hit_beats(hit_rate) -> int | None:
    """Numerator of a 'k/n' hit_rate string (posts that beat baseline), or None."""
    if not isinstance(hit_rate, str) or "/" not in hit_rate:
        return None
    try:
        return int(hit_rate.split("/", 1)[0].strip())
    except ValueError:
        return None


def is_decisive_negative(beats: int, n: int) -> bool:
    """A hard "this doesn't work": ZERO of n≥5 posts beat baseline — retire or
    rework, don't retest. Distinct from weakest (which is relative)."""
    return beats == 0 and n >= DECISIVE_NEG_MIN_N


def is_weakest_eligible(lift, n: int) -> bool:
    """Weak enough AND sampled enough to be a verdict rather than a question."""
    return isinstance(lift, (int, float)) and lift <= WEAK_LIFT_MAX and n >= WEAK_MIN_SAMPLE


def is_breakout(lift) -> bool:
    return isinstance(lift, (int, float)) and lift >= BREAKOUT_LIFT_MIN


def posting_gap_threshold(usual_gap_days: float) -> float:
    """Days quiet before the gap sensor fires: max(4, usual_gap × 2) — a 2×/week
    creator is nagged sooner than a weekly one, but nobody is nagged at 3 days."""
    return max(GAP_FLOOR_DAYS, usual_gap_days * GAP_FACTOR)


def posting_gap_fires(days_since_last_post: float, usual_gap_days: float) -> bool:
    return days_since_last_post >= posting_gap_threshold(usual_gap_days)


# ── response_type → Marque destination (FORCED in code) ───────────────────────
# Ported pattern from Palo decide.py:36-40/163-167: FE routing is keyed off
# destination, so code overwrites whatever the model emitted — a model slip
# (e.g. ideas → teardown) can never misroute a card. Response types with no
# Marque surface yet (FIX_TITLE_HOOK / SURFACE_STUDY / STRATEGY_TAKE) are
# dropped rather than guessed at; build_candidates never offers them anyway.
_DEST = {
    "OBSERVE_REVIEW": "teardown",
    "GENERATE_IDEAS": "idea_bank",
    "GENERATE_ALT_IDEA": "idea_bank",
    "REVIVE_PROJECT": "resurface_brief",
    "NUDGE_ONLY": "coach_card",
}
_CONFIDENCE = ("hypothesis", "likely", "validated")

# Ported VERBATIM from /Users/home/Palo_Server/palo_python/pulse/decide.py
# (_SYSTEM, lines 42-105). The in-code prompt IS prod there — the LD flag
# `pulse-decider-prompt` was never created. The destination line names Palo's
# FE surfaces (REVIEW/CHAT/OUTLINE/STUDY); harmless here because _DEST above
# overwrites destination in code regardless of what the model writes.
_SYSTEM = """\
You are Palo's proactive editor. Once a day, you look at the real things that
happened on a creator's channel and decide what — if anything — is worth telling
them, and what the RIGHT response is for each. You are talking to a peer who
believes you simply know their channel; never expose the machinery.

YOU ARE GIVEN (all already grounded — real, retrieved evidence; never invent):
- candidates: the day's signals, each with an observation + real evidence
  (metrics, the video's analysis, the bucket, trends) + `candidate_responses`
  (the ONLY responses you may choose for that signal) + a `salience` hint.
- strategy: the creator's Conclusion (insights, directive incl. their growth
  LEVER + GROUNDING type, buckets, brand bets, not-doing). Reason from this.
- vitals: light current state (cadence, recent uploads, 7d deltas).
- history: what you've already sent recently — do NOT repeat it.

DECIDE, in order:
1. SALIENCE — which candidates genuinely matter today? Aim for ~3 decisions on a
   channel with real activity (useful, not a flood). Fewer only when there's
   little worth saying; full silence ([]) only on a genuinely dead day. Don't pad
   with weak observations to hit 3 — but don't over-suppress; they should hear
   from you most days.
2. RESPONSE TYPE — for each kept candidate, choose the RIGHT response from its
   `candidate_responses` by reasoning about WHY. This is the judgment:
   - a weak video in a PROVEN/strong bucket → the concept is fine, the EXECUTION
     leaked → OBSERVE_REVIEW (review that video), NOT GENERATE_IDEAS.
   - a weak video because the FORMAT itself is failing → GENERATE_IDEAS back in a
     proven format.
   - a cooling SUBJECT but a strong FORMAT → keep the format, swap the subject →
     GENERATE_ALT_IDEA.
   - an overperformer → GENERATE_IDEAS to ride it (or REVIVE a fitting saved one).
   Never choose a response not in that signal's candidate_responses.
3. RANK — order by what matters most to the creator's growth lever; cap at 3.
4. SYNTHESIZE THE DAY — write `day_header` (one-line read of the whole day, the
   verdict) and `day_summary` (a short free-form paragraph in your voice, like a
   colleague catching them up across the kept signals).

GROUNDING: every rationale and instruction must rest on the candidate's real
evidence + the strategy. Cite the real numbers/videos. Do not assert a metric you
weren't given (e.g. don't claim "low engagement" if only views were provided).

For each decision write a `generator_instruction`: the concrete brief the
downstream generator will build from — what to make + the constraints (e.g.
"Creative Review of <video_id>; focus the open on the cold-start hook" or
"3 ideas in the CHALLENGE bucket, vary the setting, keep build→payoff").

OUTPUT — ONLY valid JSON (no prose, no code fence):
{
  "day_header": "one-line verdict for the whole day",
  "day_summary": "short free-form paragraph, your voice",
  "decisions": [
    {
      "signal_ref": "<the candidate's sensor+id>",
      "response_type": "OBSERVE_REVIEW|GENERATE_IDEAS|GENERATE_ALT_IDEA|FIX_TITLE_HOOK|REVIVE_PROJECT|SURFACE_STUDY|STRATEGY_TAKE|NUDGE_ONLY",
      "rank": 1,
      "rationale": "diagnosis → therefore this response (cite the real evidence)",
      "generator_instruction": "the concrete brief for the generator",
      "destination": "MUST match the response_type — OBSERVE_REVIEW→REVIEW · GENERATE_IDEAS/GENERATE_ALT_IDEA/FIX_TITLE_HOOK/STRATEGY_TAKE/NUDGE_ONLY→CHAT (ideas live in chat) · REVIVE_PROJECT→OUTLINE or SCRIPT · SURFACE_STUDY→STUDY",
      "headline": "the card headline — the action you took, self-sufficient",
      "body": "one real paragraph; cite the real numbers/videos",
      "pills": ["short scannable flares, e.g. '📉 1,100 views', '2x below baseline', '✦ 3 ideas'"],
      "confidence": "hypothesis|likely|validated"
    }
  ]
}"""

_DECIDE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["day_header", "day_summary", "decisions"],
    "properties": {
        "day_header": {"type": "string"},
        "day_summary": {"type": "string"},
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["signal_ref", "response_type", "rank", "rationale",
                         "generator_instruction", "destination", "headline",
                         "body", "pills", "confidence"],
            "properties": {
                "signal_ref": {"type": "string"},
                "response_type": {"type": "string", "enum": [
                    "OBSERVE_REVIEW", "GENERATE_IDEAS", "GENERATE_ALT_IDEA",
                    "FIX_TITLE_HOOK", "REVIVE_PROJECT", "SURFACE_STUDY",
                    "STRATEGY_TAKE", "NUDGE_ONLY"]},
                "rank": {"type": "integer"},
                "rationale": {"type": "string"},
                "generator_instruction": {"type": "string"},
                "destination": {"type": "string"},
                "headline": {"type": "string"},
                "body": {"type": "string"},
                "pills": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string",
                               "enum": ["hypothesis", "likely", "validated"]},
            }}},
    },
}


# ── sensors: arm/post/brief snapshots → grounded CandidateSignals (pure) ──────

def _as_count(x: float):
    i = int(x)
    return i if i == x else x


def _norm_arm(a) -> dict | None:
    """Tolerant read of one arm-stats snapshot row (main.py `_creator_stats` shape
    or a raw arm dict). lift is a MULTIPLE (0.6x/2.0x); a percent `lift_pct` is
    converted only when the row claims a grounded lift (`has_lift` not False)."""
    if not isinstance(a, dict):
        return None
    subject = str(a.get("value") or a.get("name") or a.get("arm") or "").strip()
    dimension = str(a.get("dimension") or "").strip()
    if not dimension and ":" in subject:
        dimension, subject = subject.split(":", 1)
    if not subject:
        return None
    n = a.get("n") if isinstance(a.get("n"), int) else a.get("samples")
    n = n if isinstance(n, int) else 0
    lift = a.get("lift") if isinstance(a.get("lift"), (int, float)) else None
    if lift is None and isinstance(a.get("lift_pct"), (int, float)) \
            and a.get("has_lift", True):
        lift = round(1.0 + a["lift_pct"] / 100.0, 2)
    beats = a.get("beats") if isinstance(a.get("beats"), int) else hit_beats(a.get("hit_rate"))
    return {"subject": subject, "dimension": dimension, "n": n, "lift": lift,
            "beats": beats, "hit_rate": a.get("hit_rate"), "avg_views": a.get("avg_views")}


def _norm_post(p) -> dict | None:
    """Tolerant read of one post-registry snapshot row. age_days must be supplied
    by the snapshotter (keeps this function pure — no clock reads here)."""
    if not isinstance(p, dict):
        return None
    views = p.get("views")
    if not isinstance(views, (int, float)) and isinstance(p.get("metrics"), dict):
        views = p["metrics"].get("views")
    age = p.get("age_days")
    return {"id": str(p.get("id") or p.get("post_id") or p.get("clip_id") or ""),
            "title": str(p.get("title") or ""),
            "views": float(views) if isinstance(views, (int, float)) else 0.0,
            "age_days": float(age) if isinstance(age, (int, float)) else None}


def _cand(kind: str, subject: str, reason: str, evidence: dict, salience: float,
          responses: list[str]) -> dict:
    return {"kind": kind, "subject": subject, "ref": f"{kind}:{subject}",
            "detected_reason": reason, "evidence": evidence,
            "salience": max(0.0, min(1.0, salience)),
            "candidate_responses": responses}


def build_candidates(arms: list[dict], posts: list[dict], briefs: list[dict],
                     usual_gap_days: float = 3.0) -> list[dict]:
    """Pure deterministic sensors → grounded CandidateSignal dicts
    {kind, subject, evidence, detected_reason (+ ref, salience,
    candidate_responses)}. Evidence only ECHOES input numbers (plus the median /
    multiple derived from them) — never an invented metric. REVIVE_PROJECT is
    offered only when saved briefs actually exist to revive."""
    cands: list[dict] = []
    narms = [x for x in (_norm_arm(a) for a in arms or []) if x]
    nposts = [x for x in (_norm_post(p) for p in posts or []) if x]
    saved = [{"id": b.get("id"), "title": str(b.get("title") or "")}
             for b in (briefs or [])
             if isinstance(b, dict) and b.get("title")
             and str(b.get("status") or "new") == "new"][:3]
    revive = ["REVIVE_PROJECT"] if saved else []

    # decisive_negative — computed first; suppresses a redundant weakest on the
    # same bucket (it's the stronger claim: "retire, don't retest").
    decisive = sorted((a for a in narms
                       if a["beats"] is not None and is_decisive_negative(a["beats"], a["n"])),
                      key=lambda a: a["lift"] if a["lift"] is not None else 1.0)
    decisive_subject = None
    if decisive:
        a = decisive[0]
        decisive_subject = a["subject"]
        ev = {"n": a["n"], "hit_rate": a["hit_rate"] or f"0/{a['n']}"}
        if a["lift"] is not None:
            ev["lift"] = a["lift"]
        if a["dimension"]:
            ev["dimension"] = a["dimension"]
        cands.append(_cand(
            "decisive_negative", a["subject"],
            f"'{a['subject']}' has failed decisively — 0 of {a['n']} posts beat "
            f"your baseline. Retire or rework, don't retest.",
            ev, 0.9, ["OBSERVE_REVIEW", "NUDGE_ONLY"]))

    # weakest_performer — clearest underperforming bucket WITH enough samples to
    # be a verdict; salience scales with sample size (full confidence at n≥5).
    weak = sorted((a for a in narms
                   if a["subject"] != decisive_subject
                   and is_weakest_eligible(a["lift"], a["n"])),
                  key=lambda a: a["lift"])
    if weak:
        a = weak[0]
        ev = {"lift": a["lift"], "n": a["n"]}
        if a["dimension"]:
            ev["dimension"] = a["dimension"]
        if isinstance(a["avg_views"], (int, float)):
            ev["avg_views"] = a["avg_views"]
        cands.append(_cand(
            "weakest_performer", a["subject"],
            f"'{a['subject']}' is your weakest bucket right now "
            f"(lift {a['lift']:.2f}x baseline, {a['n']} posts).",
            ev, (1.0 - a["lift"]) * min(1.0, a["n"] / 5.0),
            ["OBSERVE_REVIEW", "GENERATE_ALT_IDEA", "NUDGE_ONLY"]))

    # breakout — clearest winning bucket; ride it (or revive a fitting saved idea)
    hot = sorted((a for a in narms if is_breakout(a["lift"])), key=lambda a: -a["lift"])
    if hot:
        a = hot[0]
        ev = {"lift": a["lift"], "n": a["n"]}
        if a["dimension"]:
            ev["dimension"] = a["dimension"]
        if saved:
            ev["saved_briefs"] = saved
        cands.append(_cand(
            "breakout", a["subject"],
            f"'{a['subject']}' is a breakout right now (lift {a['lift']:.2f}x baseline).",
            ev, (a["lift"] - 1.0) / 4.0, ["GENERATE_IDEAS"] + revive))

    # view_spike — a fresh post ≥2x the recent median (L10 rule: baseline is the
    # median of the ~10 most recent posts). One spike max; the freshest wins.
    aged = sorted((p for p in nposts if p["age_days"] is not None),
                  key=lambda p: p["age_days"])
    pool = (aged or nposts)[:10]
    base = median_views([p["views"] for p in pool])
    if base > 0:
        for p in aged:
            if p["age_days"] <= VIEW_SPIKE_AGE_DAYS and p["views"] >= base * VIEW_SPIKE_MULT:
                mult = round(p["views"] / base, 2)
                ev = {"post_id": p["id"], "title": p["title"],
                      "views": _as_count(p["views"]), "median_views": _as_count(base),
                      "multiple": mult, "age_days": p["age_days"]}
                if saved:
                    ev["saved_briefs"] = saved
                cands.append(_cand(
                    "view_spike", p["title"] or p["id"],
                    f"'{p['title'][:80]}' is spiking — {int(p['views']):,} views, "
                    f"{mult:.1f}x your recent median, {p['age_days']:g}d old.",
                    ev, (mult - 1.0) / 4.0, ["GENERATE_IDEAS"] + revive))
                break

    # posting_gap — quiet longer than max(4 days, usual_gap × 2)
    if aged and isinstance(usual_gap_days, (int, float)) and usual_gap_days > 0:
        dsl = aged[0]["age_days"]
        if posting_gap_fires(dsl, usual_gap_days):
            ev = {"days_since_last_post": dsl, "usual_gap_days": usual_gap_days,
                  "gap_threshold_days": posting_gap_threshold(usual_gap_days)}
            if saved:
                ev["saved_briefs"] = saved
            cands.append(_cand(
                "posting_gap", "posting cadence",
                f"It's been {dsl:g} days since your last post — you usually post "
                f"about every {usual_gap_days:g} days.",
                ev, dsl / (usual_gap_days * 4.0),
                ["GENERATE_IDEAS", "NUDGE_ONLY"] + revive))

    return cands


# ── the decider (LLM judgment, flag-gated, silent-day degrade) ────────────────

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num_tokens(text: str) -> set[str]:
    """Normalized number tokens in a string: '1,100'→'1100', '5.0'→{'5.0','5'}."""
    out: set[str] = set()
    for m in _NUM_RE.finditer(text or ""):
        tok = m.group(0).replace(",", "")
        out.add(tok)
        if "." in tok:
            stripped = tok.rstrip("0").rstrip(".")
            if stripped:
                out.add(stripped)
    return out


def _candidate_numbers(candidate) -> set[str]:
    try:
        return _num_tokens(json.dumps(candidate, default=str))
    except Exception:
        return set()


def _match_candidate(signal_ref: str, candidates: list[dict]) -> dict | None:
    ref = (signal_ref or "").strip().lower()
    for c in candidates:
        if isinstance(c, dict):
            cref = str(c.get("ref") or "").lower()
            if cref and (cref == ref or cref in ref or ref in cref):
                return c
    for c in candidates:
        if isinstance(c, dict):
            kind = str(c.get("kind") or "").lower()
            if kind and kind in ref:
                return c
    return None


def _harden(raw, candidates: list[dict]) -> list[dict]:
    """Code-enforced invariants on the model's decisions: destination FORCED from
    response_type (unroutable types dropped), pills scrubbed of any number not
    present in the matched candidate (never invent a metric), confidence clamped
    to the enum, provenance {noticed → diagnosis → action} attached, ranked, ≤3."""
    if not isinstance(raw, list):
        return []
    all_nums: set[str] = set()
    for c in candidates:
        all_nums |= _candidate_numbers(c)
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        rt = d.get("response_type")
        if rt not in _DEST:                      # no Marque surface → drop, never misroute
            continue
        d = dict(d)
        d["destination"] = _DEST[rt]
        if d.get("confidence") not in _CONFIDENCE:
            d["confidence"] = "hypothesis"
        cand = _match_candidate(str(d.get("signal_ref") or ""), candidates)
        nums = _candidate_numbers(cand) if cand else all_nums
        pills = d.get("pills") if isinstance(d.get("pills"), list) else []
        d["pills"] = [p for p in pills if isinstance(p, str) and _num_tokens(p) <= nums]
        d["provenance"] = {"noticed": str(d.get("signal_ref") or ""),
                           "diagnosis": str(d.get("rationale") or ""),
                           "action": rt}
        out.append(d)
    out.sort(key=lambda d: d["rank"] if isinstance(d.get("rank"), int) else 1_000)
    return out[:3]


def _silent() -> dict:
    return {"day_header": "", "day_summary": "", "decisions": []}


async def decide(store, creator_id: str, candidates: list[dict],
                 strategy_text: str = "", vitals_line: str = "",
                 history: list[dict] | None = None) -> dict:
    """Run the daily decider over grounded candidates. Returns {day_header,
    day_summary, decisions[≤3]} with destination forced in code. Flag off, empty
    candidates (zero LLM spend), keyless, vendor failure, or parse garbage all
    degrade to the silent day. Never raises."""
    if not palo_flags.enabled(palo_flags.DECIDER):
        return _silent()
    if not candidates:                 # a genuinely dead day: no call, no spend
        return _silent()
    try:
        system = await get_prompt("palo.pulse.decide", _SYSTEM, store=store)
        if strategy_text:
            system += "\n\n=== CREATOR STRATEGY (Conclusion) ===\n" + strategy_text
        system += "\n" + CACHE_BREAKPOINT      # cache the whole (mostly static) prefix
        user = json.dumps({"candidates": candidates, "vitals": vitals_line,
                           "history": history or []}, default=str)
        data = await anthropic_cached_json(system, user, _DECIDE_SCHEMA, SONNET,
                                           max_tokens=2500)
        if not isinstance(data, dict):
            return _silent()                   # keyless / vendor error / garbage
        await ai_usage.record(store, creator_id, "pulse.decide", SONNET, 3500, 900)
        return {"day_header": str(data.get("day_header") or ""),
                "day_summary": str(data.get("day_summary") or ""),
                "decisions": _harden(data.get("decisions"), candidates)}
    except Exception as e:
        logging.warning("[decider] decide failed: %s", e)
        return _silent()


# ── Today-briefing shaping (pure — Palo pulse/briefing.py) ────────────────────

# Hero preference when ranks tie / are absent: a concrete artifact the creator
# can act on beats a pure nudge. Lower index = stronger hero candidate.
# Verbatim order from briefing.py _HERO_PREFERENCE.
_HERO_PREFERENCE = [
    "REVIVE_PROJECT", "GENERATE_IDEAS", "OBSERVE_REVIEW",
    "GENERATE_ALT_IDEA", "FIX_TITLE_HOOK", "SURFACE_STUDY",
    "STRATEGY_TAKE", "NUDGE_ONLY",
]


def _pref(response_type: str) -> int:
    try:
        return _HERO_PREFERENCE.index(response_type)
    except ValueError:
        return len(_HERO_PREFERENCE)


def _hero_sort_key(card: dict):
    rank = card.get("rank")
    return (rank if isinstance(rank, int) else 1_000, _pref(card.get("response_type", "")))


def _card_from_decision(d: dict) -> dict:
    rt = str(d.get("response_type") or "")
    prov = d.get("provenance") if isinstance(d.get("provenance"), dict) else {
        "noticed": str(d.get("signal_ref") or ""),
        "diagnosis": str(d.get("rationale") or ""),
        "action": rt}
    return {"source": "decider", "response_type": rt,
            "destination": _DEST.get(rt, str(d.get("destination") or "")),
            "rank": d.get("rank") if isinstance(d.get("rank"), int) else None,
            "confidence": d.get("confidence") if d.get("confidence") in _CONFIDENCE
            else "hypothesis",
            "headline": str(d.get("headline") or ""),
            "body": str(d.get("body") or ""),
            "pills": d.get("pills") if isinstance(d.get("pills"), list) else [],
            "generator_instruction": str(d.get("generator_instruction") or ""),
            "provenance": prov}


def _card_from_brief(b: dict) -> dict:
    diagnosis = "overnight idea promoted by the judge"
    if isinstance(b.get("score"), (int, float)):
        diagnosis += f" (score {b['score']})"   # echoes the brief's own number only
    return {"source": "idea_bank", "response_type": "GENERATE_IDEAS",
            "destination": "idea_bank", "rank": None, "confidence": "hypothesis",
            "headline": str(b.get("title") or ""),
            "body": str(b.get("summary") or ""),
            "pills": [], "brief_id": b.get("id"), "generator_instruction": "",
            "provenance": {"noticed": "idea_bank", "diagnosis": diagnosis,
                           "action": "GENERATE_IDEAS"}}


def shape_briefing(decisions: list[dict], promoted_briefs: list[dict]) -> dict:
    """Pure Today shaping: decider decisions + judge-promoted idea briefs →
    {"hero": card|None, "lanes": {...}}. Hero = rank asc (None last), then
    artifact-strength preference (a concrete artifact beats a pure nudge).
    Every card carries its provenance {noticed, diagnosis, action}."""
    cards = [_card_from_decision(d) for d in (decisions or []) if isinstance(d, dict)]
    cards += [_card_from_brief(b) for b in (promoted_briefs or [])
              if isinstance(b, dict) and b.get("title")]
    ranked = sorted(cards, key=_hero_sort_key)
    lanes = {
        "ideas": [c for c in ranked
                  if c["response_type"] in ("GENERATE_IDEAS", "GENERATE_ALT_IDEA")],
        "reviews": [c for c in ranked if c["response_type"] == "OBSERVE_REVIEW"],
        "revives": [c for c in ranked if c["response_type"] == "REVIVE_PROJECT"],
        "nudges": [c for c in ranked if c["response_type"] == "NUDGE_ONLY"],
    }
    return {"hero": ranked[0] if ranked else None, "lanes": lanes}
