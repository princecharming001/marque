"""Cut-quality LLM judge: ONE text-only Sonnet call per video over a numbered
KEEP/CUT sentence table. Catches what token math can't: paraphrased retakes,
content that only makes sense with its cut setup, seams that break grammar.
Keyless -> [] (fail-soft, same convention as the vision graders)."""
from __future__ import annotations

import asyncio
import json
import os

import httpx

from eval.campaign_common import finding
from eval.cut_qc import sentences_with_keep

_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-5"
_SEM = asyncio.Semaphore(4)


async def judge(edl: dict, words: list[dict], *, video: str = "", job_id: str = "") -> list[dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return []
    sents = sentences_with_keep(edl, words)
    if not sents:
        return []
    # Show the judge what the VIEWER gets, not the raw transcript. A partially-kept
    # sentence used to be printed in full next to a "KEEP part" marker, so a stumble
    # the edit had ALREADY cut still read as present — the judge dutifully flagged it
    # every round (verified: identical EDLs scored clean and dirty across rounds).
    # Now a partial keep prints its KEPT words, with the dropped ones listed after so
    # the judge can still catch a bad boundary.
    def _row(s: dict) -> str:
        kept_ratio = s["kept_ratio"]
        mark = "KEEP" if kept_ratio >= 0.5 else "CUT "
        part = " part" if 0 < kept_ratio < 1 else "     "
        if 0 < kept_ratio < 1:
            kept_txt = " ".join(w for w, _f, k in s["words"] if k)
            cut_txt = " ".join(w for w, _f, k in s["words"] if not k)
            body = f"{kept_txt[:160]}"
            if cut_txt:
                body += f"   (cut: {cut_txt[:80]})"
        else:
            body = s["text"][:160]
        return f"[{s['idx']:>3}] {mark}{part} | {body}"

    table = "\n".join(_row(s) for s in sents)
    body = {
        "model": _MODEL, "max_tokens": 900,
        "system": "You are a ruthless assistant editor reviewing a rough cut of a "
                  "talking-head short. The editor's ONLY job here is keep/cut. "
                  "Stumbles, false starts, filler and duplicate takes must be CUT; "
                  "unique substantive content must be KEPT; a partially-kept sentence "
                  "is almost always a mistake. Be precise; do not invent problems.",
        "messages": [{"role": "user", "content":
            "Sentence-level cut sheet (KEEP/CUT as decided by the AI editor; 'part' "
            "means partially kept):\n\n" + table + "\n\n"
            "Flag ONLY genuine errors:\n"
            "- overcut: a CUT sentence carrying unique substantive content that does "
            "not reappear later (losing it damages the argument)\n"
            "- undercut: a KEPT sentence that is clearly a stumble, false start, "
            "pure filler, or a duplicate of another kept take\n"
            "- seam: a keep/cut boundary that breaks grammar or logic (e.g. a kept "
            "sentence depends on a cut setup; a partial keep strands a clause)\n"
            "Empty lists are the expected answer for a good edit."}],
        "output_config": {"format": {"type": "json_schema", "schema": {
            "type": "object", "additionalProperties": False,
            "required": ["overcut", "undercut", "seam"],
            "properties": {k: {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["sentence", "reason"],
                "properties": {"sentence": {"type": "integer"},
                               "reason": {"type": "string"}}}}
                for k in ("overcut", "undercut", "seam")}}}},
    }
    async with _SEM:
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(_URL, json=body, headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01",
                    "content-type": "application/json"})
        except httpx.HTTPError:
            return []
    if r.status_code != 200:
        return []
    try:
        v = json.loads("".join(b.get("text", "") for b in r.json().get("content", [])))
    except ValueError:
        return []
    out: list[dict] = []
    by_idx = {s["idx"]: s for s in sents}
    for kind, cls in (("overcut", "cut_judge_overcut"), ("undercut", "cut_judge_undercut"),
                      ("seam", "cut_judge_seam")):
        for item in (v.get(kind) or [])[:8]:
            s = by_idx.get(item.get("sentence"))
            out.append(finding(video, job_id, cls,
                t=(s["start_f"] / 30 if s else None),
                evidence=f"[s{item.get('sentence')}] {str(item.get('reason'))[:140]}"
                         + (f" — \"{s['text'][:70]}\"" if s else ""),
                source="cut_judge", extra=item))
    return out
