"""One page of v2 scripts: material -> payoff-first writes -> lint -> (full tier) judge + one fix.

The caller (main) owns everything that needs app state: the plan (its `_plan_slots`), the
creator's posts / uploads / voice profile, niche openers and angles, and turning the result
into the app's ScriptDTO. This module only needs plain data, so it is testable without main.

Tiers:
  fast  first paint. Helper-model material (short timeout), one first-paint-model write per
        slot in parallel, lint. No judge, no rewrite (latency); the background full pass
        replaces it.
  full  background upgrade and /v1/scripts. Writer model; the first slot is written alone so
        the doctrine + exemplar prefix is cached before the others read it; optional best-of-2
        from a different exemplar set; one judge call for the page; one targeted rewrite per
        script that fails the lint or the judge, kept only if it comes back cleaner.
"""
from __future__ import annotations

import asyncio
import logging
import re

import prompts
from app.scripts_v2 import beats as v2beats, doctrine, flags, judge, lint, material, writer


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (t or "").lower()))


def _lint(s: dict, *, lint_profile: dict, allowed: list[str], avoid: list[str] | None,
          length: dict, quoted: list[str] | None = None) -> list:
    """Lint one script. `quoted` = own_reel particulars: the material step hands the writer
    the creator's own words on purpose, so reusing THOSE is not exemplar copying."""
    prof = dict(lint_profile or {})
    if allowed:
        prof["allowed_claims"] = allowed
    found = lint.lint_script(s, profile=prof, recent_openers=avoid or None,
                             target_words=(int(length.get("lo", 90)), int(length.get("hi", 130))))
    if quoted:
        qs = [_norm(q) for q in quoted if q]
        found = [f for f in found if not (f.code == "exemplar_overlap" and f.span
                                          and any(_norm(f.span) in q for q in qs))]
    return found


def _quoted(particulars: list[dict]) -> list[str]:
    return [p.get("text", "") for p in particulars or [] if p.get("source") == "own_reel"]


def _as_script(w: dict, slot: dict) -> dict:
    body, cta = writer.split_cta(w["body"])
    return {"hook": w["hook"], "body": body or w["body"], "cta": cta, "last_line": w.get("last_line", ""),
            "formatId": slot.get("formatId", ""), "payoff": slot.get("payoff", "")}


async def write_page(*, brand: dict, slots: list[dict], samples: list[dict], memory: dict | None,
                     angles: list[dict] | None, avoid_openers: list[str] | None, length: dict,
                     voice_block: str, exemplars: list[dict], exemplars_alt: list[dict] | None,
                     lint_profile: dict, voice_confidence: str, tier: str, niche: str,
                     slot_timeout: float | None = None,
                     material_timeout: float | None = None) -> list[dict]:
    if not slots:
        return []
    full = tier == "full"
    model = flags.writer_model() if full else flags.first_paint_model()

    mats = await material.gather(slots, brand=brand, samples=samples, memory=memory, angles=angles,
                                 timeout=material_timeout if material_timeout else (None if full else 5.0))
    if len(mats) != len(slots):
        mats = [{"particulars": [], "ask_user": ""} for _ in slots]

    briefs: list[str] = []
    for sl, m in zip(slots, mats):
        briefs.append(writer.slot_brief(
            sl, material_text=material.render(m["particulars"], m["ask_user"]), length=length,
            avoid_openers=avoid_openers,
            register_desc=prompts.SLOT_REGISTERS.get(sl.get("register") or "", ""),
            frame_desc=prompts.OPENER_FRAMES.get(sl.get("opener_frame") or "", ""),
            extra=v2beats.beats_block(sl) if sl.get("beats") else ""))

    async def _one(i: int, ex: list[dict]) -> dict | None:
        return await writer.write(brand=brand, voice_block=voice_block, exemplars=ex, brief=briefs[i],
                                  model=model, timeout=slot_timeout,
                                  route="scripts_v2.write" if full else "scripts_v2.write_fast")

    drafts: list[dict | None]
    if full and len(slots) > 1:
        first = await _one(0, exemplars)               # warm the cached prefix
        rest = await asyncio.gather(*(_one(i, exemplars) for i in range(1, len(slots))))
        drafts = [first, *rest]
    else:
        drafts = list(await asyncio.gather(*(_one(i, exemplars) for i in range(len(slots)))))

    if full and flags.best_of() >= 2 and exemplars_alt and exemplars_alt != exemplars:
        alts = await asyncio.gather(*(_one(i, exemplars_alt) for i in range(len(slots))))
        for i, alt in enumerate(alts):
            if not alt:
                continue
            if not drafts[i]:
                drafts[i] = alt
                continue
            a, b = _as_script(drafts[i], slots[i]), _as_script(alt, slots[i])
            pick = await judge.pick(a, b, niche=niche)
            if pick is None:
                allowed = material.allowed_claims(mats[i]["particulars"])
                ha = lint.summarize(_lint(a, lint_profile=lint_profile, allowed=allowed,
                                          avoid=avoid_openers, length=length))["hard"]
                hb = lint.summarize(_lint(b, lint_profile=lint_profile, allowed=allowed,
                                          avoid=avoid_openers, length=length))["hard"]
                pick = 1 if hb < ha else 0
            if pick == 1:
                drafts[i] = alt

    out: list[dict] = []
    for i, d in enumerate(drafts):
        if not d:
            logging.info("[v2] slot %d dropped (%s)", i, tier)
            continue
        s = _as_script(d, slots[i])
        allowed = material.allowed_claims(mats[i]["particulars"])
        findings = _lint(s, lint_profile=lint_profile, allowed=allowed, avoid=avoid_openers, length=length,
                         quoted=_quoted(mats[i]["particulars"]))
        s.update({"_i": i, "_draft": d, "_allowed": allowed, "_findings": findings})
        out.append(s)

    if full and out:
        verdicts = await judge.review(out, niche=niche)

        async def _fix(k: int, s: dict) -> None:
            hard = [f for f in s["_findings"] if f.severity == lint.HARD]
            problems = [f"{f.code}: {f.detail}" for f in hard] + judge.problems_from(verdicts.get(k))
            s["_judge"] = verdicts.get(k) or {}
            if not problems:
                return
            fixed = await writer.rewrite(s["_draft"], problems, brand=brand, voice_block=voice_block,
                                         exemplars=exemplars, brief=briefs[s["_i"]], model=model)
            if not fixed:
                return
            cand = _as_script(fixed, slots[s["_i"]])
            cf = _lint(cand, lint_profile=lint_profile, allowed=s["_allowed"], avoid=avoid_openers,
                       length=length, quoted=_quoted(mats[s["_i"]]["particulars"]))
            n_old = sum(1 for f in s["_findings"] if f.severity == lint.HARD)
            n_new = sum(1 for f in cf if f.severity == lint.HARD)
            judged_only = n_old == 0
            if n_new < n_old or (judged_only and n_new == 0):
                cand.update({k2: s[k2] for k2 in ("_i", "_draft", "_allowed")})
                cand["_findings"], cand["_judge"], cand["_rewritten"] = cf, s["_judge"], True
                s.clear()
                s.update(cand)

        await asyncio.gather(*(_fix(k, s) for k, s in enumerate(out)))

    res: list[dict] = []
    for s in out:
        i = s["_i"]
        sl, m = slots[i], mats[i]
        summ = lint.summarize(s["_findings"])
        res.append({
            "hook": s["hook"], "body": s["body"], "cta": s["cta"], "last_line": s.get("last_line", ""),
            "slot": sl, "particulars": m["particulars"], "ask_user": m["ask_user"],
            "lint_codes": summ["codes"], "lint_hard": summ["hard"],
            "rewritten": bool(s.get("_rewritten")), "judge": s.get("_judge") or {},
            "voice_confidence": voice_confidence, "doctrine_version": doctrine.version(),
            "model": model, "tier": tier,
        })
    return res
