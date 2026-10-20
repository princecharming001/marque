"""Niche angles: what the top talking-head reels in a niche are about THIS week.

Trends in most products are marketing (opaque "trend scores", hashtag lists). The only
approaches reviewers call useful ground in real winning content. We already scrape and
transcribe each niche's top reels (reels_cache); this turns that corpus into <= 8 angles,
each with:
  title      what the reels are about, as a plain headline
  promise    what viewers are being told they'll get
  openers    real spoken first lines from member reels (for the planner's avoid list and the
             trend card; the writer is never asked to copy them)
  velocity   new | rising | steady vs the previous refresh (honest: a computed comparison)
  reels / views  real counts, the only numbers a trend card may show

The planner uses angles for TOPICS (hooks stay the creator's own); the material step can
hand a slot the angle's promise as a `niche_trend` particular.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from app.scripts_v2 import flags, llm

FORMATS = ["myth-buster", "listicle", "pov-story", "green-screen", "do-this-not-that"]
MAX_ANGLES = 8

SYSTEM = (
    "You read the top talking-head reels in one niche from the last few days and say what they are "
    "ABOUT, grouped. An angle is a topic plus the take, not a format and not a hook style: 'protein "
    "timing doesn't matter as much as total protein' is an angle, 'contrarian takes' is not. Group "
    "reels that make the same point. For each angle give: title (a plain headline, under 10 words), "
    "promise (what the viewer is told they'll get, one plain sentence), reels (the ids of the reels in "
    "it), format (closest of: " + ", ".join(FORMATS) + "). Only angles at least one reel actually "
    "makes. Never invent numbers or results."
)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["angles"],
    "properties": {"angles": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "promise", "reels", "format"],
        "properties": {"title": {"type": "string"}, "promise": {"type": "string"},
                       "reels": {"type": "array", "items": {"type": "integer"}},
                       "format": {"type": "string", "enum": FORMATS}}}}},
}

_STOP = set("the a an and or but to of in on for with is are was be it that this you your i my "
            "we they not no do at as by so if just than then from what why how".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def angle_id(niche: str, title: str) -> str:
    return hashlib.sha1(f"{niche.strip().lower()}|{' '.join(sorted(_words(title)))}".encode()).hexdigest()[:10]


def _first_sentence(t: str) -> str:
    t = re.sub(r"\s+", " ", (t or "").strip())
    return re.split(r"(?<=[.!?])\s+", t, maxsplit=1)[0][:160].strip() if t else ""


def velocity(title: str, views: int, prev: list[dict] | None) -> str:
    """new | rising | steady, from a content-word match against the previous refresh."""
    best, w = None, _words(title)
    for p in prev or []:
        pw = _words(p.get("title", ""))
        if w and pw and len(w & pw) / len(w | pw) >= 0.5:
            best = p
            break
    if best is None:
        return "new"
    return "rising" if views > 1.2 * int(best.get("views") or 0) else "steady"


def _reels_for_prompt(reels: list[dict]) -> str:
    rows = []
    for i, r in enumerate(reels):
        rows.append(f"[{i}] views={int(r.get('views') or 0)} :: {str(r.get('transcript') or '')[:420]}")
    return "\n".join(rows)


async def build(niche: str, reels: list[dict], prev: list[dict] | None = None,
                *, today: dt.date | None = None) -> list[dict]:
    """reels: transcribed niche reels ({transcript, views, ...}). [] on failure / too few."""
    reels = [r for r in reels if (r.get("transcript") or "").strip()][:24]
    if len(reels) < 3:
        return []
    out, _u = await llm.call(
        model=flags.helper_model(), system=SYSTEM,
        messages=[llm.text_turn("user", f"NICHE: {niche}\n\nREELS:\n{_reels_for_prompt(reels)}\n\n"
                                        "Return {\"angles\": [...]}, strongest first.")],
        schema=SCHEMA, max_tokens=1600, route="scripts_v2.angles")
    raw = (out or {}).get("angles") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        return []
    as_of = (today or dt.date.today()).isoformat()
    seen: set[str] = set()
    res: list[dict] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        title = re.sub(r"\s+", " ", str(a.get("title") or "")).strip().rstrip(".")[:80]
        ids = [i for i in (a.get("reels") or []) if isinstance(i, int) and 0 <= i < len(reels)]
        if not title or not ids:
            continue
        aid = angle_id(niche, title)
        if aid in seen:
            continue
        seen.add(aid)
        members = [reels[i] for i in dict.fromkeys(ids)]
        views = sum(int(m.get("views") or 0) for m in members)
        openers = [o for o in (_first_sentence(m.get("transcript", "")) for m in members) if len(o) >= 8][:3]
        res.append({"angle_id": aid, "title": title[:1].upper() + title[1:],
                    "promise": re.sub(r"\s+", " ", str(a.get("promise") or "")).strip()[:200],
                    "format": a.get("format") if a.get("format") in FORMATS else "pov-story",
                    "openers": openers, "reels": len(members), "views": views,
                    "velocity": velocity(title, views, prev), "as_of": as_of})
        if len(res) >= MAX_ANGLES:
            break
    res.sort(key=lambda x: (x["velocity"] == "rising", x["views"]), reverse=True)
    return res


def _compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def as_trends(angles: list[dict], niche: str, total_reels: int) -> list[dict]:
    """Angles in the /v1/trends shape. The `why` line uses only computed counts."""
    out = []
    for a in angles:
        noun = "reel" if a.get("reels") == 1 else "reels"
        why = (f"{a.get('reels', 0)} of the top {max(total_reels, a.get('reels', 0))} {niche or 'niche'} {noun} "
               f"this week make this point, {_compact(int(a.get('views') or 0))} views between them.")
        out.append({"title": a["title"], "why": why, "formatId": a.get("format", "pov-story"),
                    "angleId": a["angle_id"], "promise": a.get("promise", ""),
                    "velocity": a.get("velocity", "new"), "openers": a.get("openers", []),
                    "asOf": a.get("as_of", "")})
    return out
