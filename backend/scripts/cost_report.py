"""LLM spend report from the ai_usage table (real tokens, metered by app/llm_usage.py).

Usage (needs the Supabase env the API uses):
    cd backend && set -a && . ./.env && set +a
    .venv/bin/python scripts/cost_report.py                 # last 7 days
    .venv/bin/python scripts/cost_report.py --days 1        # today
    .venv/bin/python scripts/cost_report.py --creator <id>  # one creator
    .venv/bin/python scripts/cost_report.py --json          # machine-readable

Prints $ by operation (the route label each call set), by model, by creator per day,
and the daily total. Rows written before the metering landed (hard-coded estimates
from the background modules) are included as-is; they carry their old operation names.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_persistence import SupabaseClient  # noqa: E402

_PAGE = 1000


async def fetch(client: SupabaseClient, since: dt.datetime, creator: str | None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        params = {"select": "creator_id,operation,model,input_tokens,output_tokens,cost_usd,created_at",
                  "created_at": f"gte.{since.isoformat()}", "order": "created_at.asc",
                  "limit": str(_PAGE), "offset": str(offset)}
        if creator:
            params["creator_id"] = f"eq.{creator}"
        r = await client._request("GET", "/ai_usage", params=params)
        if r is None or r.status_code >= 300:
            break
        page = r.json() or []
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


def summarize(rows: list[dict]) -> dict:
    by_op: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "usd": 0.0})
    by_creator_day: dict[tuple[str, str], float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        usd = float(r.get("cost_usd") or 0)
        day = str(r.get("created_at") or "")[:10]
        op = r.get("operation") or "unlabeled"
        by_op[op]["calls"] += 1
        by_op[op]["in"] += int(r.get("input_tokens") or 0)
        by_op[op]["out"] += int(r.get("output_tokens") or 0)
        by_op[op]["usd"] += usd
        by_model[r.get("model") or "?"]["calls"] += 1
        by_model[r.get("model") or "?"]["usd"] += usd
        by_creator_day[(r.get("creator_id") or "", day)] += usd
        by_day[day] += usd
    return {"by_operation": dict(by_op), "by_model": dict(by_model),
            "by_creator_day": {f"{c}|{d}": v for (c, d), v in by_creator_day.items()},
            "by_day": dict(by_day), "rows": len(rows),
            "total_usd": round(sum(by_day.values()), 4)}


def print_report(s: dict, days: int) -> None:
    print(f"ai_usage: {s['rows']} rows, last {days} day(s), total ${s['total_usd']:.4f}\n")
    print(f"{'operation':34s} {'calls':>6s} {'in_tok':>10s} {'out_tok':>9s} {'$':>9s} {'$/call':>8s}")
    for op, v in sorted(s["by_operation"].items(), key=lambda kv: -kv[1]["usd"]):
        per = v["usd"] / v["calls"] if v["calls"] else 0
        print(f"{op[:34]:34s} {v['calls']:6d} {v['in']:10d} {v['out']:9d} {v['usd']:9.4f} {per:8.4f}")
    print(f"\n{'model':34s} {'calls':>6s} {'$':>9s}")
    for m, v in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["usd"]):
        print(f"{m[:34]:34s} {v['calls']:6d} {v['usd']:9.4f}")
    print(f"\n{'day':12s} {'$':>9s}")
    for d, v in sorted(s["by_day"].items()):
        print(f"{d:12s} {v:9.4f}")
    top = sorted(s["by_creator_day"].items(), key=lambda kv: -kv[1])[:20]
    if top:
        print(f"\n{'creator | day':48s} {'$':>9s}   (top 20)")
        for k, v in top:
            print(f"{k[:48]:48s} {v:9.4f}")


async def main_async(args: argparse.Namespace) -> int:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_KEY", "")
    client = SupabaseClient(url, key)
    if not client.enabled:
        print("SUPABASE_URL / SUPABASE_KEY not set; nothing to report.", file=sys.stderr)
        return 2
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    rows = await fetch(client, since, args.creator)
    s = summarize(rows)
    if args.json:
        print(json.dumps(s, indent=2, sort_keys=True))
    else:
        print_report(s, args.days)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--creator", default=None)
    ap.add_argument("--json", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
