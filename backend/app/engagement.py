"""R2 — engagement feedback loop + soft-no (ported from Palo's suggestion organism).

Suggestions that notice they're being ignored. Three ported pieces:

1. Outbox tracking — every suggestion card the app shows gets a `suggestion_outbox`
   row (shown creates it; opened/saved/dismissed PATCH flags onto it). Idempotent:
   re-showing can't duplicate, re-opening can't un-open.
2. Engagement tier — Palo offline/judge.py `_engagement_tier` policy verbatim, over
   the last 10 shown: "engaged" = any save OR >=50% opened; "ignoring" = >=4 shown
   with 0 opens; else "skimming". Fewer than 3 shown => "skimming" (cold default —
   never punish or flatter a creator we barely have data on).
3. Soft-no — offline-idea-prompt rule 5 / orchestrator rule 6: an idea the creator
   demonstrably SAW (opened or dismissed) and never acted on (saved) is a SOFT NO —
   inaction is an answer, and the collision test runs on the ENGINE, not the title.
   The o1 gate rides along: when engagement prints zero card opens overall, untouched
   suggestions are undelivered mail, not declined ideas — the soft-no list is EMPTY.

`feedback_block` / `soft_no_block` are prompt-injectable digests (idea generation,
idea judge, converse). Keyless-green: store=None => no-op / "" / cold default, never
raises; every write is gated by ENGAGEMENT + real_creator. The `suggestion_outbox`
table lands via migration — every call degrades if it doesn't exist yet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import palo_flags
from app.recall_ledger import new_ulid

_VALID_EVENTS = {"shown", "opened", "saved", "dismissed"}
_FLAG_EVENTS = {"opened", "saved", "dismissed"}
_ROW_COLS = "suggestion_id,kind,title,opened,saved,dismissed,shown_at"

# Digest = the last 10 shown (Palo's chat-context window); the soft-no sweep reads a
# deeper window so a card ignored 3 weeks ago still blocks its own engine.
DIGEST_LIMIT = 10
SOFT_NO_LIMIT = 30

# Palo's tuning framing, verbatim: offline-judge-prompt rule 10 + interaction agent.py.
_TUNING = ("Engaged earns more ideas; ignoring earns fewer, better ones. "
           "A high dismiss rate means you've been off the mark; tune accordingly.")

_SOFT_NO_HEADER = (
    "SOFT NOS — ideas the creator saw and did not act on; their inaction is an "
    "answer. Do not re-pitch the same engine — the collision test runs on the "
    "ENGINE, not the title (a light rewording or a venue swap is the same idea):")


def _guarded(store, creator_id: str) -> bool:
    """True when this module may touch the outbox at all."""
    return (palo_flags.enabled(palo_flags.ENGAGEMENT)
            and store is not None
            and palo_flags.real_creator(creator_id))


# --- tracking ------------------------------------------------------------------

async def track(store, creator_id: str, suggestion_id: str, kind: str,
                title: str, event: str) -> bool:
    """Record one outcome event for a suggestion card. `shown` creates the row
    (idempotent — a re-show can't duplicate or reset later flags); opened/saved/
    dismissed PATCH their flag to True (idempotent by construction). Returns True
    on a successful write, False on any guard/miss — never raises."""
    if not _guarded(store, creator_id) or not suggestion_id or event not in _VALID_EVENTS:
        return False
    try:
        if event == "shown":
            r = await store._request(
                "POST", "/suggestion_outbox",
                params={"on_conflict": "creator_id,suggestion_id"},
                json={"id": new_ulid(), "creator_id": creator_id,
                      "suggestion_id": suggestion_id, "kind": kind, "title": title,
                      "shown_at": datetime.now(timezone.utc).isoformat(),
                      "opened": False, "saved": False, "dismissed": False},
                # ignore-duplicates: a second `shown` must never clobber flags an
                # earlier open/save already set (merge would rewind them to False).
                headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})
        else:
            r = await store._request(
                "PATCH", "/suggestion_outbox",
                params={"creator_id": f"eq.{creator_id}",
                        "suggestion_id": f"eq.{suggestion_id}"},
                json={event: True},
                headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)
    except Exception as e:
        logging.warning("[engagement] track(%s) failed: %s", event, e)
        return False


async def _load_recent(store, creator_id: str, limit: int) -> list[dict]:
    """Newest-first shown rows. Empty on any failure (table may not exist yet)."""
    try:
        r = await store._request(
            "GET", "/suggestion_outbox",
            params={"creator_id": f"eq.{creator_id}", "select": _ROW_COLS,
                    "order": "shown_at.desc", "limit": str(limit)})
        if not (r and r.status_code == 200):
            return []
        rows = r.json()
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logging.warning("[engagement] load_recent failed: %s", e)
        return []


# --- tier (Palo offline/judge.py _engagement_tier, verbatim policy) -------------

def tier_from_rows(rows: list[dict]) -> str:
    """engaged | skimming | ignoring from shown-card outcomes — the POLICY signal
    for cadence (no raw stats, just the tier). Pure."""
    shown = len(rows)
    if shown < 3:                                   # cold default — not enough signal
        return "skimming"
    opened = sum(1 for r in rows if r.get("opened"))
    saved = sum(1 for r in rows if r.get("saved"))
    if saved > 0 or (shown and opened / shown >= 0.5):
        return "engaged"
    if shown >= 4 and opened == 0:
        return "ignoring"
    return "skimming"


async def engagement_tier(store, creator_id: str) -> str:
    """Tier over the last 10 shown. Keyless / flag-off / unknown creator => the
    cold default 'skimming' (steers nothing, punishes nothing)."""
    if not _guarded(store, creator_id):
        return "skimming"
    return tier_from_rows(await _load_recent(store, creator_id, DIGEST_LIMIT))


# --- feedback digest (prompt-injectable; modeled on Palo format_recent_pulses) --

def _row_line(i: int, row: dict) -> str:
    tags = [t for t in ("opened", "saved", "dismissed") if row.get(t)]
    tag_str = " [" + ", ".join(tags) + "]" if tags else ""
    kind = (row.get("kind") or "suggestion").strip()
    title = (row.get("title") or "(untitled)").strip()
    return f"{i}. [{kind}] {title}{tag_str}"


async def feedback_block(store, creator_id: str) -> str:
    """The last 10 shown suggestions with their outcome flags, the engagement tier,
    and Palo's tuning framing — the meta signal first, then the rows. "" when
    keyless / flag-off / nothing shown yet."""
    if not _guarded(store, creator_id):
        return ""
    try:
        rows = await _load_recent(store, creator_id, DIGEST_LIMIT)
        if not rows:
            return ""
        tier = tier_from_rows(rows)
        shown = len(rows)
        opened = sum(1 for r in rows if r.get("opened"))
        saved = sum(1 for r in rows if r.get("saved"))
        dismissed = sum(1 for r in rows if r.get("dismissed"))
        lines = [
            f"SUGGESTION ENGAGEMENT (last {shown} shown): tier={tier} · "
            f"opened {opened}/{shown} · saved {saved} · dismissed {dismissed}",
            f"({_TUNING})",
            "",
        ]
        lines += [_row_line(i, r) for i, r in enumerate(rows, 1)]
        return "\n".join(lines)
    except Exception as e:
        logging.warning("[engagement] feedback_block failed: %s", e)
        return ""


# --- soft-no (rule 5/6: inaction is an answer — from a creator who is LOOKING) --

def soft_no_titles(rows: list[dict]) -> list[str]:
    """Titles the creator demonstrably SAW (opened or dismissed) and never acted on
    (saved). THE GATE: a soft-no read requires evidence the creator is actually
    seeing the output — when the rows print zero card opens overall, untouched
    suggestions are undelivered mail, not declined ideas => []. Pure; deduped,
    order-preserving."""
    if not any(r.get("opened") for r in rows):
        return []                                   # nobody is looking — no testimony
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not (r.get("opened") or r.get("dismissed")) or r.get("saved"):
            continue
        title = (r.get("title") or "").strip()
        if title and title not in seen:
            out.append(title)
            seen.add(title)
    return out


async def soft_no_block(store, creator_id: str) -> str:
    """Prompt-injectable soft-no list for ideation/judge context. "" when keyless /
    flag-off / no soft-nos (including the zero-opens undelivered-mail state)."""
    if not _guarded(store, creator_id):
        return ""
    try:
        titles = soft_no_titles(await _load_recent(store, creator_id, SOFT_NO_LIMIT))
        if not titles:
            return ""
        return "\n".join([_SOFT_NO_HEADER] + [f"- {t}" for t in titles])
    except Exception as e:
        logging.warning("[engagement] soft_no_block failed: %s", e)
        return ""
