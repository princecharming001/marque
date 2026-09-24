"""Phase 2 (box 1) — idea bank: onboarding idea generation + eval gate → briefs.

Ported from Palo onboarding_agent/idea_generation.py + idea_eval.py. Generates 3
niche-specific video ideas (safest bet / creative stretch / high ceiling) by adapting
proven exemplar structures, then a cheap HAIKU eval gate drops any idea with zero
niche connection (Palo's guard against the "Minecraft creator gets a morning-routine
idea" failure). Survivors become `briefs` rows the feed reads from.

Keyless-green: no key ⇒ deterministic mock ideas + pass-through eval; no store ⇒ ideas
returned but not persisted. Flag IDEA_BANK gates the on-demand entry point.
"""
from __future__ import annotations

import logging
import math
import re

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached, anthropic_cached_json
from app.prompt_store import get_prompt
from app.recall_ledger import new_ulid
from prompts import HAIKU, SONNET

_IDEASET_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ideas"],
    "properties": {
        "ideas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "content"],
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}}}},
        "justification": {"type": "string"}},
}

_EVAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["idea_index", "pass"],
        "properties": {"idea_index": {"type": "integer"}, "pass": {"type": "boolean"},
                       "reason": {"type": "string"}}}}},
}

# Palo pulse/judge.py emit shape (score = sum of the four axes, 0-10).
_JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["specificity", "non_obvious", "evidence_grounded", "actionable", "score", "notes"],
    "properties": {"specificity": {"type": "integer"}, "non_obvious": {"type": "integer"},
                   "evidence_grounded": {"type": "integer"}, "actionable": {"type": "integer"},
                   "score": {"type": "integer"}, "notes": {"type": "string"}},
}

# Palo's promotion threshold: >=8.0 is a "banger" worth interrupting the creator for
# (proactive surfacing); everything else stays passive in-app discovery.
PROMOTE_THRESHOLD = 8.0

# The judge's four axes and their maxima (palo_prompts.IDEA_JUDGE_SYSTEM). The brief's
# score is COMPUTED from these (clamped) rather than read from the model's own `score`
# field, which it can mis-add or inflate; and an idea is promoted (surfaced proactively,
# i.e. pushed at the creator unasked) only when evidence_grounded is at its max. A 9/10
# idea that leans on a personal result the creator never gave us is exactly the one we
# must not interrupt them with (2026-09-23 honesty pass).
_JUDGE_AXES = {"specificity": 3, "non_obvious": 3, "evidence_grounded": 2, "actionable": 2}


def _judge_verdict(data) -> dict | None:
    """Pure: the judge's raw JSON -> {"score": 0-10 float, "grounded": bool, "axes": {...}},
    or None when the output is unusable (not a dict, or any axis missing / non-numeric).
    None keeps the caller's positional score and never promotes."""
    if not isinstance(data, dict):
        return None
    axes: dict[str, int] = {}
    for key, hi in _JUDGE_AXES.items():
        v = data.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        axes[key] = max(0, min(hi, int(v)))
    return {"score": float(sum(axes.values())),
            "grounded": axes["evidence_grounded"] == _JUDGE_AXES["evidence_grounded"],
            "axes": axes}


def _context_from_brand(brand: dict) -> tuple[str, str, str, str]:
    """(creator_signals, channel_identity, topic, format) from Marque's Brand dict."""
    niche = (brand.get("niche") or "").strip()
    signals = "; ".join(x for x in [
        f"niche: {niche}" if niche else "",
        f"known for: {brand.get('known_for', '')}" if brand.get("known_for") else "",
        f"catchphrases: {', '.join(brand.get('catchphrases', []))}" if brand.get("catchphrases") else "",
    ] if x)
    identity = "; ".join(x for x in [
        f"audience: {brand.get('audience', '')}" if brand.get("audience") else "",
        f"voice: {brand.get('voice', '')}" if brand.get("voice") else "",
        f"platform: {brand.get('primary_platform', '')}" if brand.get("primary_platform") else "",
    ] if x)
    fmt = brand.get("primary_platform") or brand.get("camera_comfort") or "short-form"
    return signals or "(none)", identity or "(none)", niche or "content", fmt


def mock_ideas(brand: dict) -> list[dict]:
    """Deterministic fallback ideas. OWNER MANDATE: every idea is TOLD to camera — one
    take, the creator talking; the AI editor adds all other visuals. No idea may require
    filming demos, montages, or extra footage.

    These are persisted as REAL briefs (and script_from_brief writes from them), so they
    must be honest for any creator: no experiment they never ran ("i tried X for a
    week"), no tenure they never claimed ("what 100 hours taught me"), no test results.
    Takes, mistakes and mechanisms any credible creator in the niche can speak to."""
    niche = (brand.get("niche") or "").strip() or "your niche"
    # Mid-sentence casing (mirrors main._niche_mid): "Fitness" -> "fitness" in a
    # sentence-case title, while "AI tools" / "New York real estate" keep theirs.
    if niche[0].isupper() and niche[1:] == niche[1:].lower():
        niche = niche[0].lower() + niche[1:]
    return [
        {"title": f"the {niche} advice most people get backwards",
         "content": "Open on the belief most people hold, say plainly why it's wrong, then give "
                    "the better move with one everyday example the viewer will recognize. One "
                    "take, talking to camera."},
        {"title": f"the {niche} mistake hiding in plain sight",
         "content": "Hook on the moment the viewer is living ('you do this, and then that "
                    "happens'), explain why it happens, and land the one fix last. One take, "
                    "talking to camera."},
        {"title": f"three {niche} rules nobody explains",
         "content": f"Three counterintuitive rules as spoken beats, each with the reason it "
                    f"works, saving the one that reaches beyond {niche} for last. One take, "
                    f"talking to camera."},
    ]


async def generate_ideas(store, brand: dict, exemplars: str = "",
                         knowledge: str = "basic", creator_id: str = "",
                         structural_patterns: str = "",
                         recent_catalog: str = "") -> list[dict]:
    if not exemplars and creator_id:            # draw on the exemplar bank's proven patterns
        try:
            from app import exemplar
            exemplars = await exemplar.exemplar_block(store, creator_id)
        except Exception:
            exemplars = ""
    if not structural_patterns:
        # Cold-start structural grounding: NICHE_PRIORS formats stand in for the exemplar
        # corpus until real per-niche video analyses exist. Honest by construction — these
        # are format priors, not fabricated performance claims.
        try:
            import prompts as _p
            structural_patterns = _p.niche_prior_block(brand.get("niche", ""))
        except Exception:
            structural_patterns = ""
    signals, identity, _topic, _fmt = _context_from_brand(brand)
    # The {channel_identity} slot's intended payload is the full identity DOC (macro
    # dials, voice anchors, data_confidence) — the 3-field join is the cold fallback.
    try:
        if creator_id and palo_flags.enabled(palo_flags.CHANNEL_IDENTITY):
            from app import channel_identity
            doc_block = channel_identity.identity_block(
                await channel_identity.load_identity(store, creator_id))
            if doc_block:
                identity = doc_block
    except Exception:
        pass
    base_sys, user = palo_prompts.idea_generation_prompt(
        signals, identity, exemplars, knowledge,
        structural_patterns=structural_patterns, recent_catalog=recent_catalog)
    # Engagement loop (Palo): the soft-no list (ideas the creator saw and ignored —
    # inaction is an answer; collision runs on the ENGINE) + the tier digest ("ignoring
    # earns fewer, better ones"). Both "" when the ENGAGEMENT flag is off.
    try:
        from app import engagement
        soft = await engagement.soft_no_block(store, creator_id)
        feed = await engagement.feedback_block(store, creator_id)
        extra = "\n\n".join(x for x in (soft, feed) if x)
        if extra:
            base_sys = f"{base_sys}\n\n{extra}"
    except Exception:
        pass
    system = await get_prompt("palo.idea.generate", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _IDEASET_SCHEMA, SONNET, max_tokens=1400)
    if not isinstance(data, dict) or not data.get("ideas"):
        return mock_ideas(brand)                       # keyless / failure fallback
    await ai_usage.record(store, creator_id, "idea.generate", SONNET, 3000, 900)  # the call ran
    ideas = [{"title": i.get("title", ""), "content": i.get("content", "")}
             for i in data["ideas"] if i.get("title")][:3]
    if len(ideas) < 3:
        return mock_ideas(brand)
    return ideas


async def eval_ideas(store, ideas: list[dict], topic: str, fmt: str,
                     creator_id: str = "") -> list[bool]:
    """Per-idea pass flags. Keyless ⇒ all pass (never drop ideas we can't judge)."""
    if not ideas:
        return []
    base_sys, user = palo_prompts.idea_eval_prompt(topic, fmt, ideas)
    system = await get_prompt("palo.idea.eval", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _EVAL_SCHEMA, HAIKU, max_tokens=500)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return [True] * len(ideas)
    verdict = {r.get("idea_index"): bool(r.get("pass", True)) for r in data["results"]}
    await ai_usage.record(store, creator_id, "idea.eval", HAIKU, 700, 200)
    # idea_index is 1-based in the prompt; default to pass if the judge omitted one.
    return [verdict.get(i + 1, verdict.get(i, True)) for i in range(len(ideas))]


async def judge_idea_verdicts(store, ideas: list[dict], brand: dict,
                              recent_titles: list[str] | None = None,
                              creator_id: str = "") -> list[dict | None]:
    """Judge each idea with the ported pulse judge (specificity / non_obvious /
    evidence_grounded / actionable, axis-cap rejection rules) and return one
    `_judge_verdict` per idea: {"score" (axis sum, 0-10), "grounded", "axes"}, or None
    when keyless / the call failed / the output lacked an axis. Runs AFTER the binary
    eval gate: eval kills off-niche, this ranks what survived."""
    if not ideas:
        return []
    signals, identity, _t, _f = _context_from_brand(brand)
    context = f"{signals}\n{identity}"
    try:                                       # judge sees the engagement tier (Palo policy)
        from app import engagement
        tier = await engagement.engagement_tier(store, creator_id)
        if tier and tier != "skimming":
            context += (f"\nENGAGEMENT: {tier} — engaged earns more ideas; "
                        "ignoring earns fewer, better ones (judge accordingly).")
    except Exception:
        pass
    verdicts: list[dict | None] = []
    for idea in ideas:
        base_sys, user = palo_prompts.idea_judge_prompt(idea, context, recent_titles)
        system = await get_prompt("palo.idea.judge", base_sys, store=store)
        data = await anthropic_cached_json(system, user, _JUDGE_SCHEMA, HAIKU, max_tokens=300)
        verdict = _judge_verdict(data)
        claimed = data.get("score") if verdict is not None else None
        if claimed is not None and claimed != verdict["score"]:
            logging.info("[ideas] judge self-score %r != axis sum %s; using the axis sum",
                         claimed, verdict["score"])
        verdicts.append(verdict)
    if any(v is not None for v in verdicts):
        await ai_usage.record(store, creator_id, "idea.judge", HAIKU,
                              900 * len(ideas), 120 * len(ideas))
    return verdicts


async def judge_ideas(store, ideas: list[dict], brand: dict,
                      recent_titles: list[str] | None = None,
                      creator_id: str = "") -> list[float]:
    """Score each idea 0-10 (the sum of the judge's clamped axes, never its self-reported
    `score`). Keyless / failure ⇒ -1.0 sentinel per idea (callers keep their positional
    score, never fabricate a judged score we didn't compute)."""
    verdicts = await judge_idea_verdicts(store, ideas, brand, recent_titles, creator_id)
    return [v["score"] if v is not None else -1.0 for v in verdicts]


def to_briefs(creator_id: str, ideas: list[dict], source: str = "onboarding") -> list[dict]:
    """Handles both idea shapes: onboarding ideas (title + content) and spitfire briefs
    (title + summary + beginning/middle/end)."""
    briefs = []
    for i, idea in enumerate(ideas):
        briefs.append({
            "id": new_ulid(), "creator_id": creator_id, "source": source,
            "title": idea.get("title", ""),
            "summary": idea.get("summary") or idea.get("content", ""),
            "beginning": idea.get("beginning", ""),
            "middle": idea.get("middle", ""),
            "ending": idea.get("ending") or idea.get("end", ""),
            "score": round(1.0 - i * 0.1, 3), "status": "new",
        })
    return briefs


# --- spitfire chain (overnight ideation) --------------------------------------
_OPEN, _CLOSE = "<OPEN>", "<CLOSE>"
_NEW_RE = re.compile(r"^\s*TITLE:\s*(?P<title>.+?)\s*\nCONTENT:\s*(?P<content>.+?)\s*$", re.DOTALL)
_LEGACY_RE = re.compile(
    r"^\s*TITLE:\s*(?P<title>.+?)\s*\nSUMMARY:\s*(?P<summary>.+?)\s*\n"
    r"BEGINNING:\s*(?P<beginning>.+?)\s*\nMIDDLE:\s*(?P<middle>.+?)\s*\nEND:\s*(?P<end>.+?)\s*$",
    re.DOTALL)


def parse_thinking_output(output) -> dict | None:
    """Port of Palo nightly_utils.parse_thinking_output. Parses one <OPEN>…<CLOSE>
    block in either the new (TITLE+CONTENT) or legacy (TITLE/SUMMARY/BEGINNING/MIDDLE/
    END) format. Returns a normalized idea dict or None."""
    if not isinstance(output, str):
        return None
    s, e = output.find(_OPEN), output.find(_CLOSE)
    if s == -1 or e == -1 or s > e:
        return None
    content = output[s + len(_OPEN):e].strip()
    m = _NEW_RE.match(content)
    if m:
        p = m.groupdict()
        return {"title": p["title"].strip(), "content": p["content"].strip()}
    m = _LEGACY_RE.match(content)
    if not m:
        return None
    p = m.groupdict()
    return {"title": p["title"].strip(), "summary": p["summary"].strip(),
            "beginning": p["beginning"].strip(), "middle": p["middle"].strip(),
            "ending": p["end"].strip()}


def parse_all(output: str) -> list[dict]:
    """Parse every <OPEN>…<CLOSE> block in a multi-idea generation."""
    out = []
    for chunk in (output or "").split(_OPEN)[1:]:
        parsed = parse_thinking_output(_OPEN + chunk)
        if parsed and parsed.get("title"):
            out.append(parsed)
    return out


def _parse_ranking(text: str, n: int) -> list[int]:
    """'[3] > [1] > [2]' -> [2,0,1] (0-based, valid, deduped). Missing indices appended
    in original order so nothing is dropped."""
    order, seen = [], set()
    for m in re.finditer(r"\[(\d+)\]", text or ""):
        idx = int(m.group(1)) - 1
        if 0 <= idx < n and idx not in seen:
            order.append(idx)
            seen.add(idx)
    for i in range(n):
        if i not in seen:
            order.append(i)
    return order


def _channel_analysis(brand: dict) -> str:
    signals, identity, topic, fmt = _context_from_brand(brand)
    return f"topic: {topic}; format: {fmt}; {signals}; {identity}"


def _candidates_text(cands: list[dict]) -> str:
    return "".join(f"\n[{i + 1}] {c.get('title', '')}: {c.get('summary') or c.get('content', '')}"
                   for i, c in enumerate(cands))


async def spitfire(store, creator_id: str, brand: dict, exemplar: str = "",
                   n: int = 3) -> list[dict]:
    """Generator -> Critic -> Editor -> Ranker (<=4 LLM calls). Returns ranked brief
    dicts. Keyless / any-failure ⇒ deterministic mock. Never raises."""
    try:
        ca = _channel_analysis(brand)
        gsys, guser = palo_prompts.spitfire_generator_prompt(ca, exemplar, n)
        gen = await anthropic_cached(gsys, guser, SONNET, max_tokens=1600, temperature=1.0)
        cands = parse_all(gen) if gen else []
        if len(cands) < n:                                # keyless / parse-thin fallback
            cands = mock_ideas(brand)[:n]
            await ai_usage.record(store, creator_id, "spitfire.mock", SONNET, 0, 0)
            return to_briefs(creator_id, cands, source="spitfire")
        await ai_usage.record(store, creator_id, "spitfire.generate", SONNET, 3000, 1200)

        ctext = _candidates_text(cands)
        csys, cuser = palo_prompts.spitfire_critic_prompt(ctext, ca)
        crit = await anthropic_cached(csys, cuser, SONNET, max_tokens=800) or ""
        if crit:
            await ai_usage.record(store, creator_id, "spitfire.critic", SONNET, 1500, 400)

        esys, euser = palo_prompts.spitfire_editor_prompt(gen, crit, ca)
        edited = await anthropic_cached(esys, euser, SONNET, max_tokens=1600)
        edited_cands = parse_all(edited) if edited else []
        if len(edited_cands) == len(cands):
            cands = edited_cands
            await ai_usage.record(store, creator_id, "spitfire.editor", SONNET, 2000, 1200)

        rsys, ruser = palo_prompts.spitfire_ranker_prompt(_candidates_text(cands), ca, crit)
        rank_txt = await anthropic_cached(rsys, ruser, HAIKU, max_tokens=100) or ""
        order = _parse_ranking(rank_txt, len(cands))
        if rank_txt:
            await ai_usage.record(store, creator_id, "spitfire.rank", HAIKU, 600, 40)
        ranked = [cands[i] for i in order]
        return to_briefs(creator_id, ranked, source="spitfire")
    except Exception as e:
        logging.warning("[ideas] spitfire failed: %s", e)
        return to_briefs(creator_id, mock_ideas(brand)[:n], source="spitfire")


# --- scheduling (tier cadence) -------------------------------------------------
# Render hits /internal/cron/ideate daily; each creator's tier cadence decides whether
# they're actually DUE, tracked by a per-creator watermark (metric_watermarks table).
_IDEATE_INTERVAL_DAYS = {"nightly": 1.0, "daily": 1.0, "3xweek": 7.0 / 3.0,
                         "weekly": 7.0, "biweekly": 14.0, "monthly": 30.0, "off": 0.0}


def ideate_interval_days(schedule: str) -> float:
    return _IDEATE_INTERVAL_DAYS.get(schedule, 0.0)


def is_ideate_due(tier: str, last_epoch: float, now_epoch: float) -> bool:
    from app import tiers
    interval = ideate_interval_days(tiers.cadence(tier, "ideas"))
    if interval <= 0:                       # 'off' cadence -> never
        return False
    if not last_epoch:                      # never generated -> due
        return True
    return (now_epoch - last_epoch) >= interval * 86400


async def run_ideate_for(store, creator_id: str, brand: dict, tier: str,
                         now_epoch: float, exemplar: str = "") -> int:
    """Generate + persist a fresh idea batch for one creator IF their tier cadence says
    they're due. The reusable primitive for both the cron and event-driven (_spawn on a
    new dossier) triggers. Returns #briefs written (0 when not due / off / no store)."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK) or store is None or not creator_id:
        return 0
    # …and never WRITE into it either: a shared row that keeps accumulating briefs is
    # what made the pool worth reading in the first place.
    if not palo_flags.real_creator(creator_id):
        return 0
    try:
        last = await store.get_watermark(creator_id, "ideate_last_run") or 0
        if not is_ideate_due(tier, float(last), now_epoch):
            return 0
        briefs: list[dict] = []
        if palo_flags.enabled(palo_flags.SKETCH_IDEAS):
            # Palo's sketch→idea bake-off (supersedes the spitfire chain). Its
            # NO-FALLBACK-COPY contract returns [] on any failure, so falling through
            # to spitfire (which has its own mock floor) is always safe.
            try:
                from app import sketch_ideas
                recent = [b.get("title", "") for b in
                          await store.load_briefs(creator_id, limit=20)]
                identity_ctx = ""
                try:
                    from app import channel_identity
                    identity_ctx = channel_identity.identity_block(
                        await channel_identity.load_identity(store, creator_id))
                except Exception:
                    pass
                briefs = await sketch_ideas.bake_ideas(
                    store, creator_id, brand, identity_context=identity_ctx,
                    exemplars=exemplar, recent_titles=[t for t in recent if t])
            except Exception as e:
                logging.warning("[ideas] bake_ideas failed, spitfire fallback: %s", e)
        if not briefs:
            briefs = await spitfire(store, creator_id, brand, exemplar)
        try:
            await store.upsert_briefs(briefs)             # one array POST, not one per brief
        except Exception as e:
            logging.warning("[ideas] cron upsert_briefs failed: %s", e)
        await store.set_watermark(creator_id, "ideate_last_run", float(now_epoch))
        return len(briefs)
    except Exception as e:
        logging.warning("[ideas] run_ideate_for failed: %s", e)
        return 0


async def brief_feed_items(store, creator_id: str, limit: int = 6,
                           min_score: float = 0.0) -> list[dict]:
    """Stored briefs as feed items, ranked by score (the ideate-rank) and gated by a
    min-score threshold (pulse-judge-lite). Flag-gated + keyless => empty (the feed then
    shows only its script items, unchanged)."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK) or store is None or not creator_id:
        return []
    # Fail closed on the shared bucket (see strategy_compiler.strategy_block for the
    # full story): onboarding is pre-auth, so creator_id is "default" for everyone,
    # and serving that row's briefs handed one creator's idea bank to the next. The
    # feed degrades to its script items — honest, and niche-correct.
    if not palo_flags.real_creator(creator_id):
        return []
    briefs = await store.load_briefs(creator_id, status="new", limit=limit * 2)
    # Palo's promotion split: judge-promoted bangers surface FIRST (they're the ones
    # worth interrupting for); within each tier the stored score orders.
    briefs = sorted(briefs, key=lambda b: (not bool(b.get("promoted")),
                                           -float(b.get("score", 0) or 0)))
    items = [{"id": b.get("id"), "kind": "idea", "source": "idea_bank",
              "title": b.get("title", ""), "summary": b.get("pitch") or b.get("summary", ""),
              "score": b.get("score", 0), "brief_id": b.get("id"),
              "promoted": bool(b.get("promoted"))}
             for b in briefs if float(b.get("score", 0) or 0) >= min_score]
    # Outcome reranker (flag-gated, measured-signal): silent stable rerank when a
    # trained per-creator model exists; identity otherwise. Callers gate the flag
    # before any work (outcome_ranker convention).
    try:
        if palo_flags.enabled(palo_flags.OUTCOME_RANKER):
            from app import outcome_ranker
            model = await outcome_ranker.load_model(store, creator_id)
            if model:
                items = outcome_ranker.rerank(items, model)
    except Exception:
        pass
    return items[:limit]


def merge_briefs_into_feed(items: list, brief_items: list[dict], max_briefs: int = 3) -> list:
    """Prepend up to `max_briefs` score-ranked idea items ahead of the script feed,
    deduped by id. Pure — the /v1/feed integration point."""
    if not brief_items:
        return items
    seen = {i.get("id") for i in items if isinstance(i, dict)}
    fresh = [b for b in brief_items if b.get("id") not in seen][:max_briefs]
    return fresh + list(items)


async def run_ideate_cron(store, now_epoch: float) -> int:
    """Sweep every creator, generating for those whose tier cadence is due. Returns the
    total briefs written across the fleet. Flag-gated + keyless no-op."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK) or store is None:
        return 0
    from app import tiers
    total = 0
    for c in await store.load_all_creators():
        cid = c.get("creator_id")
        # Shared pre-auth bucket: run_ideate_for already fails closed on it, so skip here
        # too rather than pay a tier lookup per cron for a creator that can never ideate.
        if not cid or not palo_flags.real_creator(cid):
            continue
        tier = await tiers.tier_for(cid, store)
        brand = {"niche": c.get("niche", ""), "goal": c.get("goal", "")}
        total += await run_ideate_for(store, cid, brand, tier, now_epoch)
    return total


async def suggest_ideas(store, creator_id: str, brand: dict, source: str = "onboarding",
                        exemplars: str = "") -> list[dict]:
    """Full pipeline: generate → eval-filter → briefs → persist → return. Flag-gated.
    Never returns empty when generation produced ideas (keeps the top idea if the gate
    would drop them all). Swallows persistence errors."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK):
        return []
    try:
        _, _, topic, fmt = _context_from_brand(brand)
        ideas = await generate_ideas(store, brand, exemplars, creator_id=creator_id)
        # Cheapest gate first (Palo gate.go): hedged suggestion copy is dead on arrival —
        # a deterministic regex kill before any LLM spend. Never drop below one idea.
        unhedged = [i for i in ideas
                    if not palo_prompts.hedges(f"{i.get('title', '')} {i.get('content', '')}")]
        ideas = unhedged or ideas[:1]
        passes = await eval_ideas(store, ideas, topic, fmt, creator_id=creator_id)
        kept = [idea for idea, ok in zip(ideas, passes) if ok] or ideas[:1]
        # Quality scoring (pulse judge port): the axis sum (0-10) ranks the survivors; an
        # unjudged idea (keyless / vendor error / missing axis) keeps its positional score
        # and is never promoted.
        verdicts = await judge_idea_verdicts(store, kept, brand, creator_id=creator_id)
        briefs = to_briefs(creator_id, kept, source)
        for b, v in zip(briefs, verdicts):
            if v is not None:
                b["score"] = round(v["score"] / 10.0, 3)  # same 0..1 scale as positional
                # banger worth proactive surfacing: high score AND fully grounded
                b["promoted"] = v["grounded"] and v["score"] >= PROMOTE_THRESHOLD
        briefs.sort(key=lambda b: float(b.get("score", 0) or 0), reverse=True)
        if store is not None:
            try:
                await store.upsert_briefs(briefs)         # one array POST, not one per brief
            except Exception as e:
                logging.warning("[ideas] upsert_briefs failed: %s", e)
        return briefs
    except Exception as e:
        logging.warning("[ideas] suggest_ideas failed: %s", e)
        return []
