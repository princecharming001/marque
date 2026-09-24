"""Production smoke test of every AI-backed Marque (a.k.a. Yunicorn) backend endpoint.

Answers "are the AI capabilities actually working in prod?" with an honest per-route
verdict:

    LIVE   real, specific model output (mode live / live_fast / cached-real)
    MOCK   keyless / failure fallback: canned template copy, or a route that could not
           reach its real path (e.g. analyze-video "live_structure" on a canned transcript)
    ERROR  non-2xx, transport exception, or a live-labeled response with no content
    N/A    the route legitimately has nothing for a brand-new creator (needs posts /
           settled metrics / briefs), or its Palo feature flag is off (mode "off")

Only the public API is called (no keys needed), and only the allow-listed routes in
_ALLOWED below: it never publishes, never touches /v1/social/*, /v1/uploads/*, clips,
devices, telemetry, feed feedback, suggestion tracking, reels warm, or /internal/*.
At most MAX_INFLIGHT (3) backend requests are in flight at once: prod is a single
small Render instance serving real users.

Run order matters (state is per creator_id on the server):
  A  health            /healthz, /readyz
  B  reels             GET /v1/reels for 4 niches (cursor 0 and 1) + ranged-GET probes of
                       every served video_url (Supabase vs raw CDN, 200/206 + video/*)
  C  two lanes         lane 1: feed POST/GET cursor 0/1, then GET polls (~95s) watching for
                       the background Opus upgrade live_fast -> live, then the onboarding
                       digest (POST + polls up to 4 min). lanes 2-3: every other route.
                       (The digest persists a derived brand, so it runs after the feed polls.)
  D  post-digest reads ideas / insights / strategy / morning-brief / today (+ trends recheck)
  E  brand-scan LAST   it persists the scraped creator's posts onto this creator_id, which
                       would otherwise leak into mimic/converse/feed voice exemplars.

    cd backend && .venv/bin/python -m eval.prod_smoke
    cd backend && .venv/bin/python -m eval.prod_smoke --base http://localhost:8002
    cd backend && .venv/bin/python -m eval.prod_smoke --creator qa-smoke-1001 --skip-scrape

    cd backend && .venv/bin/python -m eval.prod_smoke --rejudge eval/out/prod_smoke_2026-09-23.json

--creator defaults to qa-smoke-<MMDD>, so a rerun after the next deploy gets a fresh,
never-seen creator (the coach card is 1/day and feed pages cache for 6h per creator).
Do not reuse a creator_id another test already used: e.g. a clip job under the same id
becomes strategy-compiler evidence and the strategy will talk about that video.
--rejudge re-scores a saved report offline with the current judges (no network).
--skip-scrape skips the Apify-backed calls (emulate, channel-read, analyze-video,
brand-scan). Writes eval/out/prod_smoke_<date>.json (full bodies + verdicts + timelines)
and prints a summary table. Line refs in notes point at main.py of commit 176da41
(branch upload-liveness-v2), the deploy this was written against.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import re
import statistics
import sys
import time
from collections import Counter
from typing import Any, Callable

import httpx

DEFAULT_BASE = "https://marque-api.onrender.com"
DEPLOYED_COMMIT = "176da41"
MAX_INFLIGHT = 3
SLOW_S = 60.0
WPM = 165                 # prompts.py's own "~165 wpm default" pace assumption
TARGET_MIN_S = 35         # a 35-55s talking-head video needs ~96-151 spoken words at 165 wpm
TARGET_MAX_S = 55
FEED_POLL_WINDOW_S = 95
FEED_POLL_EVERY_S = 15
DIGEST_POLL_WINDOW_S = 240
DIGEST_POLL_EVERY_S = 10

# A realistic THIN brand, like most real accounts have (no voice, no catchphrases).
BRAND = {"niche": "Fitness", "audience": "Busy professionals", "goal": "Get clients",
         "known_for": "no-nonsense strength training for people with desk jobs"}
REEL_NICHES = ["fitness", "personal finance", "beauty", "startups"]
SCAN_HANDLE = "garyvee"            # brand-scan (exactly one call) + channel-read
EMULATE_HANDLE = "jeffnippard"     # a real large fitness creator a user would emulate

SAMPLE_SCRIPT = {
    "title": "the desk hunch fix",
    "hook": "Your back doesn't hurt because you sit. It hurts because nothing pulls you back.",
    "body": ("Eight hours at a desk and everything on the front of your body gets short and tight.\n\n"
             "The fix isn't more stretching. It's more rowing. Three sets of rows, twice a week, "
             "heavier than feels polite.\n\n"
             "Most people who add that one lift stop rubbing their neck within a few weeks. Not "
             "because rows are magic, but because the muscles that hold your shoulders back finally "
             "have a job."),
    "cta": "Add rows this week and tell me how your neck feels on Friday.",
    "style": "talking_head", "formatId": "myth-buster", "hookSignal": "contrarian",
}

# ---------------------------------------------------------------------------
# Safety: the only routes this script may ever call on the backend.
# ---------------------------------------------------------------------------
_ALLOWED = {
    ("GET", "/healthz"), ("GET", "/readyz"),
    ("GET", "/v1/coach/today"), ("GET", "/v1/suggestions/next-idea"),
    ("POST", "/v1/pillars"), ("POST", "/v1/scripts"), ("POST", "/v1/hooks"),
    ("POST", "/v1/steer"), ("POST", "/v1/captions"), ("POST", "/v1/social-caption"),
    ("POST", "/v1/teardown"), ("POST", "/v1/insights"), ("POST", "/v1/score"),
    ("GET", "/v1/trends"), ("POST", "/v1/emulate/analyze"), ("POST", "/v1/brand-scan/handle"),
    ("POST", "/v1/onboarding/digest"), ("POST", "/v1/voice-onboarding/session"),
    ("POST", "/v1/voice-onboarding/finalize"), ("POST", "/v1/connect/channel-read"),
    ("GET", "/v1/morning-brief"), ("GET", "/v1/today"),
    ("POST", "/v1/onboarding/direction-options"), ("POST", "/v1/scripts/tutorial"),
    ("POST", "/v1/media/analyze"), ("POST", "/v1/broll/match"),
    ("GET", "/v1/recommendations"), ("GET", "/v1/insights/learned"),
    ("POST", "/v1/converse"), ("POST", "/v1/memory/distill"), ("POST", "/v1/tts"),
    ("GET", "/v1/reels"), ("GET", "/v1/feed"), ("POST", "/v1/feed"),
    ("POST", "/v1/write/turn"), ("POST", "/v1/write/from-brief"), ("POST", "/v1/ideas"),
    ("GET", "/v1/insights"), ("GET", "/v1/strategy"), ("POST", "/v1/mimic"),
    ("POST", "/v1/analyze-video"), ("POST", "/v1/brand-summary"), ("GET", "/v1/styles"),
    ("GET", "/v1/reels/examples"),
}
_DIGEST_POLL_RE = re.compile(r"^/v1/onboarding/digest/[0-9a-f-]{36}$")


def _assert_allowed(method: str, path: str) -> None:
    if (method, path) in _ALLOWED or (method == "GET" and _DIGEST_POLL_RE.match(path)):
        return
    raise RuntimeError(f"refusing to call non-allow-listed route {method} {path}")


# ---------------------------------------------------------------------------
# Template / dash / length detectors
# ---------------------------------------------------------------------------
# Substrings that only exist in the backend's deterministic copy (main.py / prompts.py at
# 176da41). "fallback" = keyless/failure mock copy: a hit inside a live-labeled payload is
# template copy leaking into live output. "by_design" = deterministic copy that is
# legitimately served in that context (cold-start reasons, heuristic trends, setup card).
TEMPLATE_SIGS: dict[str, tuple[str, str]] = {
    "mock_scripts.contrarian": ("fallback", "advice is backwards. Here's what the top 1% actually do"),
    "mock_scripts.specificity": ("fallback", "decides most of your progress in"),
    "mock_scripts.authority": ("fallback", "nobody warns beginners about"),
    "mock_scripts.cta": ("fallback", "Follow for more, I break this down every week"),
    "mock_hooks": ("fallback", "mistake nobody warns you about"),
    "mock_mimic.body": ("fallback", "get completely backwards"),
    "mock_mimic.cta": ("fallback", "Follow for the next one."),
    "mock_insights": ("fallback", "Your contrarian hooks are outperforming. Make two more"),
    "mock_teardown": ("fallback", "Solid clip, here's the read"),
    "mock_teardown.detail": ("fallback", "The hook lands in the first 2 seconds and the format keeps"),
    "mock_brand_summary": ("fallback", "Marque's read: your best content happens"),
    "mock_media": ("fallback", "asset suitable for B-roll use"),
    "mock_emulation": ("fallback", "The thing nobody tells you about being @"),
    "mock_trends": ("fallback", "is spiking in"),
    "mock_trends.2": ("fallback", "experiments are pulling huge saves"),
    "mock_next_idea": ("fallback", "to open your data loop"),
    "mock_day_plan": ("fallback", "tell Marque today's angle so scripts stay sharp"),
    "mock_converse.fact": ("fallback", "The more you tell me like this, the sharper your scripts get"),
    "mock_converse.idea": ("fallback", "Saved to your idea bank. The specific version"),
    "mock_converse.script": ("fallback", "in your voice, hook-first. It's attached below"),
    "mock_converse.greeting": ("fallback", "Tell me what's on your mind: an idea, a frustration"),
    "mock_video_transcript": ("fallback", "Hook: a bold claim delivered in the first second"),
    "mock_analyze_video": ("fallback", "a double pattern-interrupt that stops both sound-on"),
    "mock_social_caption": ("fallback", "Save this for your next fitness session."),
    "mock_direction": ("fallback", "Quick tips to camera, one concept per video, casual proof"),
    "mock_tutorial.question": ("fallback", "The first line asks something the viewer cannot answer yet"),
    "mock_tutorial.number": ("fallback", "The first line stakes a specific, countable claim"),
    "mock_tutorial.contrarian": ("fallback", "The first line pushes against something most viewers assume"),
    "heuristic_trends_no_llm": ("by_design", "reels right now are"),
    "cold_start_reason": ("by_design", "niche baseline"),
    "coach_setup_card": ("by_design", "Post one to start learning"),
}
MOCK_PILLAR_NAMES = {"Teach the fundamentals", "Myth-busting", "Behind the scenes", "Hot takes",
                     "Proof & results"}
_MOCK_SCRIPT_SIG_KEYS = ("mock_scripts.contrarian", "mock_scripts.specificity",
                         "mock_scripts.authority", "mock_scripts.cta", "mock_mimic.body")
_TEMPLATE_SKIP_KEYS = {"transcript", "reel", "reels", "agent_system", "mimicked_from",
                       "hook_text", "why_trending", "request"}
_DASH_SKIP_KEYS = {"transcript", "reel", "reels", "hook_text", "why_trending", "why_match",
                   "selection_reason", "creator_handle", "mimicked_from", "agent_system",
                   "request", "highlight_text"}
_DASH_RE = re.compile("[—–]")      # em dash, en dash


def _walk_strings(obj: Any, skip: set[str], path: str = "$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip or (isinstance(k, str) and k.endswith("url")):
                continue
            yield from _walk_strings(v, skip, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, skip, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def template_hits(obj: Any) -> list[str]:
    hits: list[str] = []
    for _p, s in _walk_strings(obj, _TEMPLATE_SKIP_KEYS):
        for name, (_kind, sig) in TEMPLATE_SIGS.items():
            if sig in s and name not in hits:
                hits.append(name)
    return hits


def fallback_hits(hits: list[str]) -> list[str]:
    return [h for h in hits if TEMPLATE_SIGS.get(h, ("", ""))[0] == "fallback"]


def dash_hits(obj: Any) -> list[dict]:
    out = []
    for p, s in _walk_strings(obj, _DASH_SKIP_KEYS):
        m = _DASH_RE.search(s)
        if m:
            a = max(0, m.start() - 50)
            out.append({"path": p, "snippet": s[a:m.start() + 50]})
    return out


def is_template_script(sc: Any) -> bool:
    if not isinstance(sc, dict):
        return False
    txt = " ".join(_hook_text(sc.get(k)) for k in ("hook", "body", "cta"))
    return any(TEMPLATE_SIGS[k][1] in txt for k in _MOCK_SCRIPT_SIG_KEYS)


def _hook_text(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or "")
    return str(v or "")


def spoken_words(sc: dict) -> int:
    """Words a creator actually says: hook + body + cta, minus [broll: ...] editor cues."""
    txt = " ".join(_hook_text(sc.get(k)) for k in ("hook", "body", "cta"))
    txt = re.sub(r"\[[^\]]*\]", " ", txt)
    return len(re.findall(r"[A-Za-z0-9$%'’]+", txt))


def fingerprint(sc: dict) -> str:
    return hashlib.sha1((str(sc.get("title", "")) + "|" + _hook_text(sc.get("hook"))).lower()
                        .encode()).hexdigest()[:12]


def clip(s: Any, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _script_sample(sc: Any) -> str:
    if not isinstance(sc, dict):
        return ""
    return f"{_hook_text(sc.get('hook'))} | {sc.get('body') or ''}"


def _fix_422(body: Any, params: dict | None, detail: Any) -> tuple[Any, dict | None, bool]:
    """Drop every field FastAPI's 422 names (loc ['body'|'query', field, ...]) unless it is a
    missing required field, so the pydantic default applies on the one retry."""
    errs = detail.get("detail") if isinstance(detail, dict) else None
    if not isinstance(errs, list):
        return body, params, False
    body = dict(body) if isinstance(body, dict) else body
    params = dict(params) if isinstance(params, dict) else params
    changed = False
    for e in errs:
        loc = e.get("loc") if isinstance(e, dict) else None
        if not (isinstance(loc, list) and len(loc) >= 2) or e.get("type") == "missing":
            continue
        where, field = loc[0], loc[1]
        if where == "body" and isinstance(body, dict) and field in body:
            body.pop(field)
            changed = True
        elif where == "query" and isinstance(params, dict) and field in params:
            params.pop(field)
            changed = True
    return body, params, changed


def url_host_class(u: str) -> str:
    u = (u or "").lower()
    if not u:
        return "empty"
    if "supabase.co/storage" in u:
        return "supabase"
    if "cdninstagram" in u or "fbcdn" in u:
        return "instagram_cdn"
    if "tiktok" in u:
        return "tiktok_cdn"
    return "other"


# ---------------------------------------------------------------------------
# Judges: (response_body, smoke) -> (verdict, reason_code, note, sample)
# ---------------------------------------------------------------------------
Verdict = tuple[str, str, str, str]


def _mode(b: dict) -> str | None:
    return b.get("mode") if isinstance(b, dict) else None


def mode_judge(sample_fn: Callable[[dict], str], mock_ref: str,
               off_note: str = "", live_extra: Callable[[dict], Verdict | None] | None = None
               ) -> Callable[[dict, Any], Verdict]:
    """Generic: live/live_fast/cached -> LIVE (content required), mock -> MOCK (cites the
    fallback path), off -> N/A (flag off)."""
    def j(b: dict, _s: Any) -> Verdict:
        if "_raw" in b:
            return ("ERROR", "non_json", f"2xx but non-JSON body: {clip(b['_raw'], 120)}", "")
        mode = _mode(b)
        sample = sample_fn(b) or ""
        if mode in ("live", "live_fast", "cached"):
            if live_extra is not None:
                v = live_extra(b)
                if v is not None:
                    return v
            if not sample.strip():
                return ("ERROR", "live_empty", "mode is live but the generated content is empty", sample)
            fb = fallback_hits(template_hits(b))
            note = f"template copy inside live payload: {fb}" if fb else ""
            return ("LIVE", mode, note, sample)
        if mode == "mock":
            return ("MOCK", "mock", mock_ref, sample)
        if mode == "off":
            return ("N/A", "flag_off", off_note or "feature flag off in prod (route returns mode 'off')",
                    sample)
        return ("MOCK", f"mode_{mode}", f"unrecognized mode {mode!r}; {mock_ref}", sample)
    return j


def j_health(b: dict, _s: Any) -> Verdict:
    return ("N/A", "infra", f"infra probe: {clip(json.dumps(b), 200)}", clip(json.dumps(b), 160))


def j_coach(b: dict, _s: Any) -> Verdict:
    card = b.get("card")
    if card is None:
        return ("N/A", "silent", "card:null. The daily gate (<=1 card/24h) already fired for this "
                "creator, or no honest insight exists (main.py:1041-1067)", "")
    sample = f"{card.get('headline', '')}: {card.get('body', '')}"
    if card.get("kind") == "setup":
        return ("N/A", "new_creator", "brand-new creator (0 settled posts) gets the deterministic setup "
                "nudge by design, no LLM call (main.py:1057-1065)", sample)
    if card.get("mode") == "live":
        return ("LIVE", "live", "", sample)
    return ("MOCK", "mock", "insight card phrased by the deterministic template: LLM output rejected "
            "(lift not quoted verbatim) or HTTPException (main.py:1068-1085)", sample)


def j_next_idea(b: dict, _s: Any) -> Verdict:
    idea = b.get("idea") or {}
    sample = f"{idea.get('title', '')} | {idea.get('hook', '')}"
    if idea.get("mode") == "live":
        fb = fallback_hits(template_hits(idea))
        return ("LIVE", "live", f"template copy inside live payload: {fb}" if fb else "", sample)
    return ("MOCK", "mock", "prompts.mock_next_idea: LLM output rejected (needs title+hook+>=3 beats) "
            "or HTTPException (main.py:1142-1157)", sample)


def j_pillars(b: dict, _s: Any) -> Verdict:
    pillars = b.get("pillars") or []
    names = [p.get("name", "") for p in pillars if isinstance(p, dict)]
    sample = "; ".join(f"{p.get('name', '')}: {p.get('summary', '')}" for p in pillars[:2]
                       if isinstance(p, dict))
    if _mode(b) == "live":
        if names and set(names) <= MOCK_PILLAR_NAMES:
            return ("MOCK", "live_label_template", "mode live but every pillar is a mock_pillars name",
                    sample)
        if not names:
            return ("ERROR", "live_empty", "mode live but no pillars", sample)
        return ("LIVE", "live", f"{len(names)} pillars: {names}", sample)
    return ("MOCK", "mock", "mock_pillars: empty parse or HTTPException in generate_pillars "
            "(main.py:1955-1966)", sample)


def _scripts_verdict(scripts: list, mode: str | None, mock_ref: str) -> Verdict:
    sample = _script_sample(scripts[0]) if scripts else ""
    n_t = sum(1 for sc in scripts if is_template_script(sc))
    words = [spoken_words(sc) for sc in scripts if isinstance(sc, dict)]
    wnote = f"spoken words per script {words}" if words else ""
    if mode in ("live", "live_fast"):
        if not scripts:
            return ("ERROR", "live_empty", "mode live but no scripts", sample)
        if n_t == len(scripts):
            return ("MOCK", "all_template", f"mode {mode} but all {n_t} scripts are mock_scripts template "
                    f"copy; {wnote}", sample)
        extra = (f"; {n_t}/{len(scripts)} scripts are mock_scripts backfill (main.py:2364-2365 / "
                 f"12769-12778)" if n_t else "")
        return ("LIVE", mode, f"{len(scripts)} scripts; {wnote}{extra}", sample)
    return ("MOCK", "mock", f"{mock_ref}; {wnote}", sample)


def j_scripts(b: dict, _s: Any) -> Verdict:
    return _scripts_verdict(b.get("scripts") or [], _mode(b),
                            "mock_scripts: keyless / empty write / HTTPException (main.py:2320-2373)")


def j_hooks(b: dict, _s: Any) -> Verdict:
    hooks = b.get("hooks") or []
    sample = " / ".join(h.get("text", "") for h in hooks[:3] if isinstance(h, dict))
    if _mode(b) == "live":
        if not hooks:
            return ("ERROR", "live_empty", "mode live but no hooks (judge dropped all?)", sample)
        return ("LIVE", "live", f"{len(hooks)} hooks, strengths "
                f"{[h.get('strength') for h in hooks if isinstance(h, dict)]}", sample)
    return ("MOCK", "mock", "single canned hook: keyless or HTTPException (main.py:2378-2393)", sample)


def j_steer(b: dict, _s: Any) -> Verdict:
    sc = b.get("script") or {}
    sample = _script_sample(sc)
    if _mode(b) == "live":
        if (sc.get("hook") == SAMPLE_SCRIPT["hook"] and sc.get("body") == SAMPLE_SCRIPT["body"]):
            return ("MOCK", "live_label_input_echo", "mode live but the script came back UNCHANGED: "
                    "`out or req.script` (parse failure) or the speakability guard fell back to the "
                    "input (main.py:2404-2410)", sample)
        return ("LIVE", "live", f"rewritten, {spoken_words(sc)} spoken words", sample)
    return ("MOCK", "mock", "returns the input script: keyless or HTTPException (main.py:2398-2412)",
            sample)


def j_teardown(b: dict, _s: Any) -> Verdict:
    sample = f"{b.get('headline', '')}: {b.get('detail', '')}"
    if _mode(b) == "live":
        if not (b.get("headline") or b.get("detail")):
            return ("ERROR", "live_empty", "mode live but headline/detail empty: extract_json failed "
                    "and `or {}` shipped blanks (main.py:2569-2574)", sample)
        return ("LIVE", "live", f"liftPercent={b.get('liftPercent')!r} (must be null: no metrics sent)",
                sample)
    return ("MOCK", "mock", "canned teardown: keyless or HTTPException (main.py:2560-2579)", sample)


def j_insights_post(b: dict, _s: Any) -> Verdict:
    txt = b.get("coaching") or ""
    if _mode(b) == "live":
        if TEMPLATE_SIGS["mock_insights"][1] in txt:
            return ("MOCK", "live_label_template", "mode live but coaching is _MOCK_COACHING "
                    "(`txt or _MOCK_COACHING`, main.py:2609)", txt)
        return ("LIVE", "live", "cached" if b.get("cached") else "", txt)
    return ("MOCK", "mock", "_MOCK_COACHING: keyless or HTTPException (main.py:2595-2611)", txt)


def j_score(b: dict, _s: Any) -> Verdict:
    sample = (f"hook={b.get('hook')} fluff={b.get('fluff')} satisfaction={b.get('satisfaction')} "
              f"overall={b.get('overall')} fix={b.get('fix', '')}")
    if _mode(b) == "live":
        return ("LIVE", "live", "", sample)
    return ("MOCK", "mock", "_mock_score_script heuristic: keyless, HTTPException, or a reply "
            "without a valid hook rating (main.py:2662-2675)", sample)


def j_trends(b: dict, _s: Any) -> Verdict:
    trends = b.get("trends") or []
    sample = "; ".join(f"{t.get('title', '')}: {t.get('why', '')}" for t in trends[:2]
                       if isinstance(t, dict))
    if _mode(b) == "live":
        if "heuristic_trends_no_llm" in template_hits(b):
            return ("LIVE", "live_heuristic", "live but HEURISTIC clustering copy (the HAIKU naming pass "
                    "failed/was skipped; 'why' carries an em dash by construction, main.py:12132)",
                    sample)
        return ("LIVE", "live", f"{len(trends)} trends", sample)
    return ("MOCK", "mock", "rotated mock_trends: no live trends cached for this niche in this process "
            "yet; the first request only kicks the background refresh (main.py:2682-2687, "
            "12169-12179)", sample)


def j_emulate(b: dict, _s: Any) -> Verdict:
    mode = _mode(b)
    if mode == "live":
        return ("LIVE", "live", "scraped + analyzed a real profile now (HAIKU)", f"ok={b.get('ok')}")
    if mode == "cached":
        return ("LIVE", "cached", "a real profile for this handle already exists (memory/Supabase); only "
                "REAL analyses are cached (main.py:7962-7968, 7998-8003)", f"ok={b.get('ok')}")
    return ("MOCK", "mock", "mock emulation profile: the scrape+transcribe exceeded the 25s direct-call "
            "budget or came back empty, or the LLM failed (main.py:7970-7992); not cached, client may "
            "re-trigger", f"ok={b.get('ok')}")


def j_brand_scan(b: dict, _s: Any) -> Verdict:
    scan = b.get("scan") or {}
    n = b.get("scanned_posts", 0)
    pillars = [p.get("name", "") for p in (scan.get("pillars") or []) if isinstance(p, dict)]
    sample = f"niche={scan.get('niche', '')!r} audience={scan.get('audience', '')!r} pillars={pillars[:3]}"
    if _mode(b) == "live":
        return ("LIVE", "live", f"derived from {n} scraped posts", sample)
    why = ("scrape returned 0 posts within the 25s budget (asyncio.wait_for, main.py:8040-8045) -> "
           "mock_derive (main.py:8051-8053)" if not n else
           "LLM derive failed -> mock_derive (main.py:8061-8062)")
    return ("MOCK", "mock", f"{why}; scanned_posts={n}", sample)


def j_voice_session(b: dict, _s: Any) -> Verdict:
    tok = b.get("conversation_token") or ""
    agent = b.get("agent_id") or ""
    sample = f"agent_id={'set' if agent else 'empty'} token={'set' if tok else 'empty'} " \
             f"session_id={b.get('session_id', '')[:8]}"
    if tok:
        return ("LIVE", "live", "a conversation token came back", sample)
    if agent:
        return ("MOCK", "token_mint_unimplemented", "ElevenLabs IS configured (agent_id returned) but the "
                "signed-url/token mint was never implemented: always mode mock with an empty token "
                "(main.py:8278-8282)", sample)
    return ("MOCK", "keyless", "ELEVENLABS_AGENT_ID/ELEVENLABS_API_KEY not set (main.py:8275-8277)", sample)


def _scan_summary(scan: dict) -> tuple[str, bool, bool]:
    voice = scan.get("voice") or {}
    mock_voice = (voice.get("funnyToSerious") == 0.5 and voice.get("polishedToRaw") == 0.5
                  and voice.get("teacherToPeer") == 0.5 and not scan.get("bannedWords")
                  and not scan.get("catchphrases"))
    names = [p.get("name", "") for p in (scan.get("pillars") or []) if isinstance(p, dict)]
    mock_pillars = bool(names) and set(names) <= MOCK_PILLAR_NAMES
    sample = f"niche={scan.get('niche', '')!r} voice={voice} pillars={names[:5]}"
    return sample, mock_voice, mock_pillars


def j_voice_finalize(b: dict, _s: Any) -> Verdict:
    scan = b.get("scan") or {}
    sample, mock_voice, mock_pillars = _scan_summary(scan)
    if _mode(b) == "live":
        if mock_voice and mock_pillars:
            return ("MOCK", "live_label_template", "mode live but scan == mock_derive (extract_json "
                    "failed -> `or mock_derive`, main.py:8292)", sample)
        return ("LIVE", "live", "derived from the interview transcript (OPUS) + judged pillars", sample)
    return ("MOCK", "mock", "mock_derive: keyless / empty transcript / HTTPException (main.py:8288-8298)",
            sample)


def j_channel_read(b: dict, _s: Any) -> Verdict:
    lines = b.get("lines") or []
    sample = " / ".join(lines[:2])
    if _mode(b) == "live" and lines:
        return ("LIVE", "live", f"{len(lines)} lines", sample)
    return ("MOCK", "mock", "empty lines: keyless, Apify profile scrape returned no captioned posts "
            "(main.py:8402-8413), the model returned no lines (8422-8424), or an exception (8425-8429)",
            sample)


def _promoted_briefs(s: Any) -> int:
    for r in getattr(s, "results", []) or []:
        if r.get("label") == "/v1/ideas" and isinstance(r.get("response"), dict):
            return sum(1 for x in r["response"].get("briefs") or [] if isinstance(x, dict) and x.get("promoted"))
    return 0


def j_morning_brief(b: dict, s: Any) -> Verdict:
    body = b.get("body") or ""
    if body:
        return ("LIVE", "live", f"mentioned={b.get('mentioned')}", body)
    n_prom = _promoted_briefs(s)
    if n_prom and _latency(s) < 1.5:
        return ("N/A", "flag_off_inferred", f"empty body in {_latency(s)}s although /v1/ideas shows {n_prom} "
                "promoted briefs for this creator (a titled artifact means comms.morning_brief would call "
                "HAIKU): the MORNING_BRIEF flag is almost certainly off in prod (app/comms.py "
                "morning_brief returns empty first thing when disabled; main.py:8453-8479)", "")
    return ("N/A", "empty", "honest empty body: no promoted briefs / undelivered insights for this creator, "
            "so comms.morning_brief returns '' with zero LLM spend; main.py:8453-8479", "")


def j_today(b: dict, _s: Any) -> Verdict:
    hero = b.get("hero") or {}
    sample = f"{b.get('day_header', '')} | {b.get('day_summary', '')} | hero={clip(json.dumps(hero), 80)}"
    if _mode(b) == "live":
        return ("LIVE", "live", f"{len(b.get('lanes') or [])} lanes", sample)
    extra = (f"; the briefing is NOT empty: hero = promoted idea-bank brief {hero.get('headline')!r} "
             f"(written by the digest's background ideas.suggest_ideas, not by this route)"
             if hero.get("source") == "idea_bank" else "")
    return ("N/A", "quiet", "decider silent: no arms/settled posts -> no decider candidates, zero LLM spend "
            "(or DECIDER flag off); main.py:8482-8524, app/decider.py decide()" + extra, sample)


def j_direction(b: dict, _s: Any) -> Verdict:
    opts = b.get("options") or []
    ids = [o.get("id") for o in opts if isinstance(o, dict)]
    sample = " / ".join(o.get("label", "") for o in opts[:2] if isinstance(o, dict))
    if _mode(b) == "live":
        if set(ids) == {"tips_to_camera", "story_with_payoff", "myth_test"}:
            return ("MOCK", "live_label_template", "mode live but options are the canned mock set", sample)
        return ("LIVE", "live", f"{len(opts)} lanes, recommendation={b.get('recommendation')!r}", sample)
    return ("MOCK", "mock", "canned talking-head lane set: keyless, unparseable reply, or HTTPException "
            "(main.py:8554-8564)", sample)


def _latency(s: Any) -> float:
    return float(((getattr(s, "cur", None) or {}).get("latency_s")) or 0.0)


def j_tutorial(b: dict, s: Any) -> Verdict:
    steps = b.get("steps") or []
    sample = " | ".join(f"{st.get('title', '')}: {st.get('explanation', '')}" for st in steps[:2]
                        if isinstance(st, dict))
    if _mode(b) == "live":
        return ("LIVE", "live", f"{len(steps)} steps (highlights validated as exact substrings)", sample)
    if _latency(s) < 1.0:
        return ("MOCK", "flag_off_inferred", f"deterministic 2-step template returned in {_latency(s)}s, i.e. "
                "no LLM attempt: the TUTORIAL flag is off in prod (app/tutorial.py pregen_tutorial "
                "returns _mock_tutorial first thing when disabled; main.py:8574-8582)", sample)
    return ("MOCK", "mock", "deterministic 2-step template: vendor failure, or every LLM highlight failed "
            "the exact-substring check (app/tutorial.py pregen_tutorial; main.py:8574-8582)", sample)


def j_media(b: dict, _s: Any) -> Verdict:
    sample = f"{b.get('description', '')} (suitability={b.get('broll_suitability')}, " \
             f"motion={b.get('motion')}, tags={b.get('tags')})"
    mode = _mode(b)
    if mode in ("live", "cached"):
        if TEMPLATE_SIGS["mock_media"][1] in str(b.get("description", "")):
            return ("MOCK", "cached_mock", "mode cached but the stored analysis is the mock (poisoned cache)",
                    sample)
        extra = {k: b.get(k) for k in ("duration_s", "width", "height", "fps", "loudness_lufs")
                 if b.get(k) is not None}
        return ("LIVE", mode or "", f"vision analysis; probe stats {extra}" if extra else "vision analysis",
                sample)
    return ("MOCK", "mock", "mock analysis: no key/URL (main.py:9134-9137) or the vision call failed / "
            "non-200 / unparseable (main.py:9148-9170)", sample)


def j_broll_tiebreak(b: dict, _s: Any) -> Verdict:
    m = b.get("matches") or []
    order = [x.get("asset_id") for x in m if isinstance(x, dict)]
    sample = f"order={order}"
    if not m:
        return ("ERROR", "empty", "no matches for a 2-asset corpus", sample)
    if order and order[0] == "real-lift":
        return ("LIVE", "live", "HAIKU tie-break promoted the semantically right asset over a keyword-"
                "identical decoy (scores tied, main.py:9220-9247)", sample)
    return ("MOCK", "no_reorder", "order unchanged on an exact score tie: HAIKU chose the decoy, or the "
            "tie-break call failed silently (`except Exception: pass`, main.py:9242-9249). Note mode is "
            "'live' whenever a key exists (main.py:9258) and proves nothing", sample)


def j_broll_pexels(b: dict, s: Any) -> Verdict:
    m = b.get("matches") or []
    top = m[0] if m and isinstance(m[0], dict) else {}
    sample = f"source={top.get('source')} pexels_url={clip(top.get('pexels_url'), 90)}"
    if top.get("source") == "pexels" and top.get("pexels_url"):
        return ("LIVE", "live", "Pexels search + vision re-rank affirmatively picked a clip "
                "(main.py:9252-9256, 9654-9689)", sample)
    if top.get("source") == "pexels" and _latency(s) >= 2.0:
        return ("N/A", "no_pick_inconclusive", f"no clip picked, but the path clearly ran ({_latency(s)}s = "
                "Pexels search + thumbnail fetch + per-candidate HAIKU vision scoring); nothing cleared "
                "the relevance floor/subject gates (_broll_vision_pick, main.py:9609-9651). A correct "
                "reject and a scoring failure look identical from outside: inconclusive", sample)
    if top.get("source") == "pexels":
        return ("MOCK", "no_pick", f"returned in {_latency(s)}s with no clip: Pexels keyless or zero results "
                "(_fetch_pexels_candidates, main.py:9278-9299)", sample)
    return ("MOCK", "no_fallback", "Pexels fallback did not run (top score >= 0.3?)", sample)


def j_recs(b: dict, _s: Any) -> Verdict:
    arms = b.get("arms") or []
    sample = "; ".join(f"{a.get('pillar')} / {a.get('style')}: {a.get('reason')}" for a in arms[:1]
                       if isinstance(a, dict))
    if _mode(b) == "live":
        return ("LIVE", "live", f"{len(arms)} Thompson-sampled arms from real data (no LLM)", sample)
    return ("N/A", "new_creator", "no learned arms for a new creator -> honest cold-start niche prior; "
            "this route makes no LLM call (main.py:10721-10742, 10790-10796)", sample)


def j_learned(b: dict, _s: Any) -> Verdict:
    sample = f"posts_learned={b.get('posts_learned')} winning_formula={b.get('winning_formula')!r}"
    if _mode(b) == "live" and b.get("insights"):
        return ("LIVE", "live", "", sample)
    return ("N/A", "new_creator", "no settled posts/arms for a new creator -> empty by design, no LLM "
            "(main.py:10799-10809)", sample)


def j_converse(b: dict, _s: Any) -> Verdict:
    sample = b.get("reply") or ""
    payload = b.get("payload") or {}
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    note = f"intent={b.get('intent')!r} memory_updates={len(b.get('memory_updates') or [])}"
    if scripts is not None:
        n_t = sum(1 for sc in scripts if is_template_script(sc))
        note += f" chained_scripts={len(scripts)} (template {n_t})"
    if _mode(b) == "live":
        fb = fallback_hits(template_hits(b))
        if fb:
            note += f"; template copy inside live payload: {fb}"
        if not sample.strip():
            return ("ERROR", "live_empty", "mode live but empty reply", sample)
        return ("LIVE", "live", note, sample)
    return ("MOCK", "mock", f"mock_converse: envelope missing/empty reply or HTTPException "
            f"(main.py:11166-11172); {note}", sample)


def j_distill(b: dict, _s: Any) -> Verdict:
    ups = b.get("memory_updates") or []
    sample = " / ".join(f"{u.get('field')}: {u.get('value')}" for u in ups[:3] if isinstance(u, dict))
    if _mode(b) == "live":
        if not ups:
            return ("MOCK", "live_label_empty", "mode live but ZERO updates from a transcript full of durable "
                    "facts: the model found nothing, or anthropic_json raised HTTPException and the route "
                    "still answers mode 'live' with [] (main.py:11217-11224)", sample)
        return ("LIVE", "live", f"{len(ups)} updates", sample)
    return ("MOCK", "mock", "keyless or <2 user turns (main.py:11215-11216)", sample)


def j_tts(b: dict, _s: Any) -> Verdict:
    if isinstance(b, dict) and b.get("_binary"):
        ok = str(b.get("content_type", "")).startswith("audio/") and int(b.get("bytes", 0)) > 1000
        return ("LIVE" if ok else "ERROR", "audio" if ok else "bad_audio",
                f"{b.get('content_type')} {b.get('bytes')} bytes", f"<audio {b.get('bytes')} bytes>")
    return ("MOCK", "mock", "JSON {mode: mock}: provider keyless or synth failed/network error "
            "(main.py:11293-11309); client falls back to on-device speech", "")


def j_reels(b: dict, _s: Any) -> Verdict:
    reels = b.get("reels") or []
    n_vid = sum(1 for r in reels if r.get("video_url"))
    sample = (f"{len(reels)} reels; first @{reels[0].get('creator_handle')}: {reels[0].get('title')}"
              if reels else "")
    if _mode(b) == "mock":
        return ("MOCK", "mock", "keyless _mock_reels (main.py:12410-12413)", sample)
    if not reels:
        return ("N/A", "empty", "no servable reels (serve gate needs video_url + transcribed + talking head, "
                "main.py:12392-12402)", sample)
    note = f"{n_vid}/{len(reels)} have a video_url; off_niche={b.get('off_niche')}"
    if n_vid == 0:
        note += ("; ALL thumbnail-only: tier-3 fallback, no playable row in this niche "
                 "(main.py:12395-12401)")
    return ("LIVE", "live", note, sample)


def j_feed(b: dict, _s: Any) -> Verdict:
    items = b.get("items") or []
    scripts = [it.get("script") for it in items if isinstance(it, dict) and it.get("type") == "script"]
    n_reels = sum(1 for it in items if isinstance(it, dict) and it.get("type") == "reel")
    briefs = sum(1 for it in items if isinstance(it, dict) and it.get("type") not in
                 ("script", "reel", "trend"))
    trend = next((it.get("trend") for it in items if isinstance(it, dict) and it.get("type") == "trend"),
                 None) or {}
    tr_hits = template_hits(trend)
    trend_kind = ("mock_trends" if fallback_hits(tr_hits) else
                  "heuristic" if "heuristic_trends_no_llm" in tr_hits else "live" if trend else "none")
    v = _scripts_verdict(scripts, _mode(b), "fast paint fell back to mock_scripts: HAIKU timeout (>10s) / "
                         "empty / HTTPException (main.py:12751-12757, 12783-12784)")
    note = (f"{v[2]}; reels={n_reels} trend={trend_kind} idea_bank_items={briefs} "
            f"next_cursor={b.get('next_cursor')}")
    return (v[0], v[1], note, v[3])


def j_write_turn(b: dict, _s: Any) -> Verdict:
    acts = b.get("actions") or []
    sample = b.get("answer") or clip(json.dumps(acts), 160)
    if _mode(b) == "off":
        return ("N/A", "flag_off", "WRITE_AGENT flag off in prod: {mode: off}, no actions "
                "(main.py:13160-13161)", sample)
    if _mode(b) == "live":
        applied = sum(1 for a in acts if isinstance(a, dict) and a.get("applied"))
        return ("LIVE", "live", f"{len(acts)} actions ({applied} applied), invariants={b.get('invariants')}",
                sample)
    return ("MOCK", "mock", "mock <answer>: keyless / empty model reply (app/write_agent.py write_turn)",
            sample)


def j_from_brief(b: dict, _s: Any) -> Verdict:
    sample = f"{b.get('title', '')} | {b.get('body', '')}"
    if _mode(b) == "off":
        return ("N/A", "flag_off", "WRITE_AGENT flag off: the body is the brief's own beats glued together "
                "(app/write_agent.py:162-163), no LLM", sample)
    if _mode(b) == "live":
        return ("LIVE", "live", f"{spoken_words(b)} words", sample)
    return ("MOCK", "mock", "brief-beat assembly: LLM returned no script, or the speakability guard fell "
            "back (app/write_agent.py:181, main.py:13185-13189)", sample)


def j_ideas(b: dict, _s: Any) -> Verdict:
    briefs = b.get("briefs") or []
    sample = "; ".join(str(x.get("title", "")) for x in briefs[:3] if isinstance(x, dict))
    if _mode(b) == "off":
        return ("N/A", "flag_off", "IDEA_BANK flag off (main.py:13197-13198)", sample)
    if briefs:
        return ("LIVE", _mode(b) or "", f"{len(briefs)} briefs (onboarding digest seeds the bank)", sample)
    return ("N/A", "empty", "flag on but no briefs for this creator yet (the digest's background "
            "ideas.suggest_ideas has not produced any)", sample)


def j_insights_get(b: dict, _s: Any) -> Verdict:
    rows = b.get("insights") or []
    sample = clip(json.dumps(rows[:1]), 160) if rows else ""
    if _mode(b) == "off":
        return ("N/A", "flag_off", "TRACK_INSIGHTS flag off (main.py:13207-13208)", sample)
    if rows:
        return ("LIVE", _mode(b) or "", f"{len(rows)} insight rows", sample)
    return ("N/A", "new_creator", "no settled posts -> no track insights for a new creator "
            "(main.py:13213-13217)", sample)


def j_strategy(b: dict, _s: Any) -> Verdict:
    st = b.get("strategy")
    if _mode(b) == "off":
        return ("N/A", "flag_off", "STRATEGY_COMPILER flag off (main.py:13224-13226)", "")
    if not st:
        return ("N/A", "none", "no compiled strategy for a new creator yet", "")
    sample = (st.get("strategy_markdown") if isinstance(st, dict) else str(st)) or ""
    if isinstance(st, dict) and st.get("is_template"):
        return ("N/A", "template", "strategy row is the deterministic placeholder (is_template, "
                "main.py:13235-13242): no analyzed videos yet", sample)
    return ("LIVE", "live", f"{len(b.get('updates') or [])} updates", sample)


def j_mimic(b: dict, _s: Any) -> Verdict:
    sc = b.get("script") or {}
    sample = _script_sample(sc)
    if _mode(b) == "live":
        if is_template_script(sc):
            return ("MOCK", "live_label_template", "mode live but script is _mock_mimic (speakability "
                    "fallback, main.py:13335-13337)", sample)
        return ("LIVE", "live", f"{spoken_words(sc)} spoken words", sample)
    return ("MOCK", "mock", "_mock_mimic: keyless / unparseable / HTTPException (main.py:13324-13340)",
            sample)


def j_analyze_video(b: dict, _s: Any) -> Verdict:
    yv = b.get("your_version") or {}
    sample = f"{b.get('hook_analysis', '')} || your_version: {_hook_text(yv.get('hook'))}"
    mode = _mode(b)
    if mode == "live":
        return ("LIVE", "live", "real transcript (Apify resolve + AssemblyAI) analyzed by OPUS", sample)
    if mode == "live_structure":
        return ("MOCK", "live_structure", "OPUS ran, but on the canned _MOCK_VIDEO_TRANSCRIPT: the URL "
                "could not be resolved+transcribed (main.py:13415-13417, 13447). _resolve_post_media only "
                "understands TikTok/IG POST permalinks via Apify post scrapers; a direct media URL (all "
                "/v1/reels exposes) can never reach the real path", sample)
    return ("MOCK", "mock", "canned teardown + _mock_mimic: keyless or HTTPException (main.py:13451-13463)",
            sample)


def j_brand_summary(b: dict, _s: Any) -> Verdict:
    sample = b.get("summary") or ""
    if _mode(b) == "live":
        return ("LIVE", "live", f"traits={b.get('traits')}", sample)
    return ("MOCK", "mock", "templated summary: keyless, no 'summary' in reply, or HTTPException "
            "(main.py:13477-13494)", sample)


def j_styles(b: dict, _s: Any) -> Verdict:
    st = b.get("styles") or []
    n_sample = sum(1 for x in st if isinstance(x, dict) and x.get("sample"))
    sample = "; ".join(f"{x.get('theme_id')}=@{x.get('handle') or '-'}" for x in st[:5] if isinstance(x, dict))
    if _mode(b) == "live":
        return ("LIVE", "live", f"{len(st) - n_sample}/{len(st)} styles have a real demo reel (no LLM)",
                sample)
    return ("MOCK", "mock", "no playable talking-head reel in any cached niche -> every style sample:true "
            "(main.py:2830-2841)", sample)


def j_examples(b: dict, _s: Any) -> Verdict:
    reels = b.get("reels") or []
    sample = "; ".join(f"@{r.get('creator_handle')}: {r.get('title')}" for r in reels[:2] if isinstance(r, dict))
    if _mode(b) == "live":
        n_vid = sum(1 for r in reels if isinstance(r, dict) and r.get("video_url"))
        return ("LIVE", "live", f"{len(reels)} real matches, {n_vid} with video_url (no LLM)", sample)
    return ("MOCK", "mock", "curated sample cards (picsum thumbs, empty video_url): no cached reel matched "
            "this treatment (main.py:3055-3062)", sample)


def j_digest_start(b: dict, _s: Any) -> Verdict:
    if b.get("job_id") and b.get("status") in ("running", "ready"):
        return ("LIVE" if _mode(b) == "live" else "MOCK", _mode(b) or "",
                f"job {b.get('status')}", f"job_id={b.get('job_id')}")
    return ("ERROR", "no_job", "no job_id", clip(json.dumps(b), 160))


def j_digest_final(b: dict, s: Any) -> Verdict:
    status = b.get("status")
    if status == "failed":
        return ("ERROR", "job_failed", f"digest job failed: {b.get('error')}", "")
    if status != "ready":
        return ("ERROR", "timeout", f"still {status!r} at stage {b.get('stage')!r} after "
                f"{DIGEST_POLL_WINDOW_S}s of polling", "")
    scan = b.get("scan") or {}
    scan_sample, mock_voice, mock_pillars = _scan_summary(scan)
    scripts = b.get("scripts") or []
    if not scripts:
        return ("ERROR", "no_scripts", "digest ready but shipped zero starter scripts", scan_sample)
    v = _scripts_verdict(scripts, _mode(b), "digest scripts are mock_scripts (script pipeline degraded)")
    notes = [v[2]]
    if mock_voice:
        notes.append("scan voice/catchphrases/bannedWords are mock_derive defaults: with no handle and "
                     "no voice_transcript (a thin account) the LLM derive step never runs "
                     "(main.py:8227-8237)")
    notes.append("pillars: mock_pillars names (HAIKU judge passed the templates)" if mock_pillars else
                 "pillars: regenerated by OPUS after the HAIKU judge rejected the template set "
                 "(judge_and_fix_pillars)" if scan.get("pillars") else "pillars: none")
    timings = s.digest_timeline[-1]["t"] if s.digest_timeline else None
    notes.append(f"ready after ~{timings}s of polling")
    return (v[0], v[1], "; ".join(notes), f"{v[3]} || scan: {scan_sample}")


# Generic samples
def _s_social(b: dict) -> str:
    return b.get("caption") or ""


def _s_captions(b: dict) -> str:
    return " / ".join(str(x) for x in (b.get("lines") or [])[:6])


j_captions = mode_judge(_s_captions, "sentence-chunked hook/body: keyless, unparseable, or HTTPException "
                        "(main.py:2426-2439)")
j_captions.__name__ = "j_captions"
j_social = mode_judge(_s_social, "_mock_social_caption skeleton: keyless, empty reply, or HTTPException "
                      "(main.py:2544-2554)")
j_social.__name__ = "j_social"

# Label -> judge, for --rejudge of a saved report whose records predate the "judge" field.
# Ordered: first prefix match wins (so /v1/reels/examples precedes /v1/reels, etc.).
_LABEL_JUDGES: list[tuple[str, Callable[[dict, Any], Verdict]]] = [
    ("/healthz", j_health), ("/readyz", j_health),
    ("/v1/reels/examples", j_examples), ("/v1/reels", j_reels), ("/v1/feed", j_feed),
    ("/v1/connect/channel-read", j_channel_read), ("/v1/analyze-video", j_analyze_video),
    ("/v1/emulate/analyze", j_emulate), ("/v1/converse", j_converse),
    ("/v1/onboarding/digest POST", j_digest_start), ("/v1/onboarding/digest GET", j_digest_final),
    ("/v1/scripts/tutorial", j_tutorial), ("/v1/scripts", j_scripts), ("/v1/mimic", j_mimic),
    ("/v1/voice-onboarding/finalize", j_voice_finalize),
    ("/v1/voice-onboarding/session", j_voice_session), ("/v1/media/analyze", j_media),
    ("/v1/pillars", j_pillars), ("/v1/broll/match [pexels", j_broll_pexels),
    ("/v1/broll/match [tie", j_broll_tiebreak), ("/v1/hooks", j_hooks), ("/v1/steer", j_steer),
    ("/v1/suggestions/next-idea", j_next_idea), ("/v1/coach/today", j_coach),
    ("/v1/teardown", j_teardown), ("/v1/insights [POST", j_insights_post),
    ("/v1/insights [GET", j_insights_get), ("/v1/insights/learned", j_learned),
    ("/v1/score", j_score), ("/v1/captions", j_captions), ("/v1/social-caption", j_social),
    ("/v1/onboarding/direction-options", j_direction), ("/v1/memory/distill", j_distill),
    ("/v1/tts", j_tts), ("/v1/write/turn", j_write_turn), ("/v1/write/from-brief", j_from_brief),
    ("/v1/brand-summary", j_brand_summary), ("/v1/trends", j_trends), ("/v1/styles", j_styles),
    ("/v1/recommendations", j_recs), ("/v1/ideas", j_ideas), ("/v1/strategy", j_strategy),
    ("/v1/morning-brief", j_morning_brief), ("/v1/today", j_today),
    ("/v1/brand-scan/handle", j_brand_scan),
]
_NO_DASH_SCAN = ("/healthz", "/readyz", "/v1/reels", "/v1/styles", "/v1/voice-onboarding/session")


def judge_for(label: str, name: str = "") -> Callable[[dict, Any], Verdict]:
    fn = globals().get(name) if name else None
    if callable(fn):
        return fn
    for prefix, j in _LABEL_JUDGES:
        if label.startswith(prefix):
            return j
    raise KeyError(f"no judge for {label!r}")


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------
class Smoke:
    def __init__(self, base: str, creator: str, out_path: pathlib.Path, skip_scrape: bool,
                 skip_digest: bool) -> None:
        self.base = base.rstrip("/")
        self.creator = creator
        self.out_path = out_path
        self.skip_scrape = skip_scrape
        self.skip_digest = skip_digest
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.t_start = time.monotonic()
        self.results: list[dict] = []
        self.ctx: dict[str, Any] = {}
        self.feed_timeline: list[dict] = []
        self.digest_timeline: list[dict] = []
        self.reels_findings: dict[str, dict] = {}
        self.thumb_probes: list[dict] = []
        self.cur: dict | None = None              # the record being judged (latency-aware judges)
        self.quiet = False
        self.meta_override: dict = {}             # --rejudge keeps the original run's timing
        self.sem: asyncio.Semaphore
        self.media_sem: asyncio.Semaphore
        self.client: httpx.AsyncClient

    # -- plumbing -----------------------------------------------------------
    def log(self, msg: str) -> None:
        if not self.quiet:
            print(f"[{time.monotonic() - self.t_start:7.1f}s] {msg}", flush=True)

    async def request(self, method: str, path: str, *, json_body: Any = None,
                      params: dict | None = None, timeout: float = 150.0,
                      binary_ok: bool = False) -> dict:
        _assert_allowed(method, path)
        async with self.sem:                       # hard cap: <= MAX_INFLIGHT on the backend
            t0 = time.monotonic()
            try:
                r = await self.client.request(method, self.base + path, json=json_body,
                                              params=params, timeout=timeout)
            except Exception as e:
                return {"status": None, "latency_s": round(time.monotonic() - t0, 2),
                        "error": f"{type(e).__name__}: {e}"[:300], "body": None, "headers": {}}
            latency = round(time.monotonic() - t0, 2)
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                body: Any = r.json()
            except ValueError:
                body = {"_raw": r.text[:2000]}
        elif binary_ok and r.status_code < 300:
            body = {"_binary": True, "content_type": ct, "bytes": len(r.content),
                    "sha1": hashlib.sha1(r.content).hexdigest()[:12]}
        else:
            body = {"_raw": r.text[:2000]}
        hdrs = {k: r.headers[k] for k in ("rndr-id", "x-render-origin-server") if k in r.headers}
        return {"status": r.status_code, "latency_s": latency, "error": None, "body": body,
                "headers": hdrs}

    def finish(self, label: str, method: str, path: str, group: str, req: dict, res: dict,
               judge: Callable[[dict, Any], Verdict], *, scan_dashes: bool = True,
               retried: bool = False) -> dict:
        body = res.get("body")
        mode = None
        if isinstance(body, dict):                   # next-idea / coach nest their mode
            mode = (body.get("mode") or (body.get("idea") or {}).get("mode")
                    or (body.get("card") or {}).get("mode")
                    or ("audio" if body.get("_binary") else None))
        rec: dict[str, Any] = {
            "label": label, "group": group, "route": f"{method} {path}", "request": req,
            "status": res.get("status"), "latency_s": res.get("latency_s"), "error": res.get("error"),
            "mode": mode, "judge": getattr(judge, "__name__", ""),
            "headers": res.get("headers"), "retried_after_422": retried, "response": body,
        }
        self.cur = rec                               # latency-aware judges read this
        status = res.get("status")
        if res.get("error") or status is None or not (200 <= status < 300):
            v: Verdict = ("ERROR", "http_error",
                          f"HTTP {status}" + (f" {res['error']}" if res.get("error") else "")
                          + f": {clip(json.dumps(body) if body is not None else '', 200)}", "")
        else:
            try:
                v = judge(body if isinstance(body, dict) else {"_raw": str(body)}, self)
            except Exception as e:                   # a judge bug must not kill the run
                v = ("ERROR", "judge_exception", f"judge crashed: {type(e).__name__}: {e}", "")
        rec["verdict"], rec["reason_code"], rec["note"], rec["sample"] = v[0], v[1], v[2], clip(v[3])
        is_json = isinstance(body, dict) and "_binary" not in body and "_raw" not in body
        rec["template_hits"] = template_hits(body) if is_json else []
        rec["dash_hits"] = dash_hits(body) if (is_json and scan_dashes) else []
        rec["slow"] = bool(rec["latency_s"] and rec["latency_s"] > SLOW_S)
        self.results.append(rec)
        self.log(f"<- {label}: HTTP {rec['status']} {rec['latency_s']}s mode={rec['mode']} "
                 f"=> {rec['verdict']} {clip(rec['note'], 90)}")
        return rec

    async def check(self, label: str, method: str, path: str, judge: Callable[[dict, Any], Verdict],
                    *, body: Any = None, params: dict | None = None, timeout: float = 150.0,
                    binary_ok: bool = False, group: str | None = None,
                    scan_dashes: bool = True) -> dict:
        self.log(f"-> {label}")
        res = await self.request(method, path, json_body=body, params=params, timeout=timeout,
                                 binary_ok=binary_ok)
        retried = False
        if res.get("status") == 422:
            # Fix-and-retry ONCE: drop every body/query field pydantic rejected (type/value
            # errors) so the model default applies. A missing REQUIRED field can't be guessed,
            # so that case is recorded as ERROR with the validation detail in the note.
            self.log(f"   422 on {label}: {clip(json.dumps(res.get('body')), 300)}")
            fixed_body, fixed_params, changed = _fix_422(body, params, res.get("body"))
            if changed:
                body, params = fixed_body, fixed_params
                res = await self.request(method, path, json_body=body, params=params, timeout=timeout,
                                         binary_ok=binary_ok)
                retried = True
        return self.finish(label, method, path, group or label, {"params": params, "json": body}, res,
                           judge, scan_dashes=scan_dashes, retried=retried)

    def skip(self, label: str, route: str, reason: str) -> None:
        self.log(f"-- {label}: skipped ({reason})")
        self.results.append({
            "label": label, "group": label, "route": route, "request": None, "status": None,
            "latency_s": None, "error": None, "mode": None, "headers": {}, "retried_after_422": False,
            "response": None, "verdict": "N/A", "reason_code": "skipped", "note": f"not called: {reason}",
            "sample": "", "template_hits": [], "dash_hits": [], "slow": False})

    async def run_pool(self, factories: list[Callable[[], Any]], workers: int) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        for f in factories:
            queue.put_nowait(f)

        async def worker() -> None:
            while True:
                try:
                    f = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await f()
                except Exception as e:
                    self.log(f"!! task crashed: {type(e).__name__}: {e}")
        await asyncio.gather(*(worker() for _ in range(workers)))

    # -- media probes (Supabase / CDN hosts, NOT the backend) ------------------
    async def probe_media(self, url: str, want: str) -> dict:
        async with self.media_sem:
            t0 = time.monotonic()
            try:
                async with self.client.stream("GET", url, headers={"Range": "bytes=0-4095"},
                                              timeout=30, follow_redirects=True) as r:
                    first = b""
                    async for chunk in r.aiter_bytes():
                        first = chunk[:16]
                        break
                    ct = r.headers.get("content-type", "")
                    return {"status": r.status_code, "content_type": ct,
                            "content_range": r.headers.get("content-range"),
                            "is_mp4": first[4:8] == b"ftyp" if want == "video" else None,
                            "latency_s": round(time.monotonic() - t0, 2),
                            "ok": r.status_code in (200, 206) and ct.startswith(want + "/")}
            except Exception as e:
                return {"status": None, "error": f"{type(e).__name__}: {e}"[:200],
                        "latency_s": round(time.monotonic() - t0, 2), "ok": False}

    # -- phases ---------------------------------------------------------------
    async def phase_health(self) -> None:
        await self.check("/healthz", "GET", "/healthz", j_health, timeout=60, scan_dashes=False)
        rec = await self.check("/readyz", "GET", "/readyz", j_health, timeout=60, scan_dashes=False)
        self.ctx["readyz"] = rec.get("response")

    async def phase_reels(self) -> None:
        pages: list[tuple[str, int, dict]] = []

        async def one(niche: str, cursor: int) -> None:
            rec = await self.check(f"/v1/reels [{niche}] c{cursor}", "GET", "/v1/reels", j_reels,
                                   params={"niche": niche, "creator_id": self.creator, "cursor": cursor},
                                   timeout=90, group="/v1/reels", scan_dashes=False)
            pages.append((niche, cursor, rec))
        await asyncio.gather(*(one(n, c) for n in REEL_NICHES for c in (0, 1)))

        # Per-niche findings over the unique reels served on pages 0+1.
        by_niche: dict[str, list[dict]] = {n: [] for n in REEL_NICHES}
        meta: dict[str, dict] = {n: {"pages": []} for n in REEL_NICHES}
        for niche, cursor, rec in sorted(pages, key=lambda t: (t[0], t[1])):
            b = rec.get("response") if isinstance(rec.get("response"), dict) else {}
            reels = b.get("reels") or []
            meta[niche]["pages"].append({"cursor": cursor, "status": rec.get("status"),
                                         "mode": b.get("mode"), "off_niche": b.get("off_niche"),
                                         "n": len(reels), "next_cursor": b.get("next_cursor"),
                                         "cycled": any("#p" in str(r.get("id", "")) for r in reels)})
            seen = {r["base_id"] for r in by_niche[niche]}
            for r in reels:
                base_id = str(r.get("id", "")).split("#p")[0]
                if base_id in seen:
                    continue
                seen.add(base_id)
                by_niche[niche].append({
                    "base_id": base_id, "creator_handle": r.get("creator_handle"),
                    "title": clip(r.get("title"), 80), "transcribed": r.get("transcribed"),
                    "edit_format": r.get("edit_format"), "views": r.get("views"),
                    "video_url": r.get("video_url") or "", "thumbnail_url": r.get("thumbnail_url") or "",
                    "video_host": url_host_class(r.get("video_url") or ""),
                    "thumb_host": url_host_class(r.get("thumbnail_url") or ""),
                    "_reel": r,
                })

        # Probe every unique non-empty video_url (ranged GET; 200/206 + video/* = playable).
        async def probe(entry: dict) -> None:
            entry["video_probe"] = await self.probe_media(entry["video_url"], "video")
        await asyncio.gather(*(probe(e) for n in REEL_NICHES for e in by_niche[n] if e["video_url"]))

        for n in REEL_NICHES:
            rows = by_niche[n]
            with_url = [e for e in rows if e["video_url"]]
            ok = [e for e in with_url if (e.get("video_probe") or {}).get("ok")]
            self.reels_findings[n] = {
                **meta[n],
                "unique_reels": len(rows),
                "with_video_url": len(with_url),
                "empty_video_url": len(rows) - len(with_url),
                "video_hosts": dict(Counter(e["video_host"] for e in rows)),
                "thumb_hosts": dict(Counter(e["thumb_host"] for e in rows)),
                "video_probe_ok": len(ok),
                "video_probe_statuses": dict(Counter(
                    f"{(e.get('video_probe') or {}).get('status')} "
                    f"{((e.get('video_probe') or {}).get('content_type') or '').split(';')[0]}"
                    for e in with_url)),
                "reels": [{k: v for k, v in e.items() if k != "_reel"} for e in rows],
            }
            self.log(f"   reels[{n}]: {len(rows)} unique, {len(with_url)} with video_url, "
                     f"{len(ok)} playable; hosts={self.reels_findings[n]['video_hosts']}")

        # Inputs for mimic / media-analyze / analyze-video, fitness first, Supabase first.
        order = ["fitness"] + [n for n in REEL_NICHES if n != "fitness"]
        flat = [e for n in order for e in by_niche[n]]
        self.ctx["mimic_reel"] = next((e["_reel"] for e in flat), None)
        vids = [e for e in flat if (e.get("video_probe") or {}).get("ok")]
        vids.sort(key=lambda e: 0 if e["video_host"] == "supabase" else 1)
        self.ctx["video_url"] = vids[0]["video_url"] if vids else next(
            (e["video_url"] for e in flat if e["video_url"]), "")
        self.ctx["video_url_playable"] = bool(vids)
        thumbs = sorted((e for e in flat if e["thumbnail_url"]),
                        key=lambda e: 0 if e["thumb_host"] == "supabase" else 1)
        self.ctx["thumb_url"] = ""
        for e in thumbs[:6]:
            p = await self.probe_media(e["thumbnail_url"], "image")
            self.thumb_probes.append({"url": e["thumbnail_url"], **p})
            if p.get("ok"):
                self.ctx["thumb_url"] = e["thumbnail_url"]
                break
        self.log(f"   inputs: mimic_reel={'yes' if self.ctx['mimic_reel'] else 'NO'} "
                 f"video_url={url_host_class(self.ctx['video_url'])} "
                 f"(playable={self.ctx['video_url_playable']}) thumb={url_host_class(self.ctx['thumb_url'])}")

    def _feed_point(self, label: str, rec: dict, t0: float | None) -> None:
        b = rec.get("response") if isinstance(rec.get("response"), dict) else {}
        items = b.get("items") or []
        scripts = [it.get("script") for it in items if isinstance(it, dict) and it.get("type") == "script"]
        self.feed_timeline.append({
            "label": label, "t_since_cold_paint_s": round(time.monotonic() - t0, 1) if t0 else 0.0,
            "status": rec.get("status"), "latency_s": rec.get("latency_s"), "mode": b.get("mode"),
            "script_fingerprints": [fingerprint(sc) for sc in scripts if isinstance(sc, dict)],
            "n_scripts": len(scripts),
            "n_template_scripts": sum(1 for sc in scripts if is_template_script(sc)),
            "words": [spoken_words(sc) for sc in scripts if isinstance(sc, dict)],
        })

    async def feed_lane(self) -> None:
        post_body = {**BRAND, "creator_id": self.creator, "styles": "", "watched": "", "memory": {},
                     "fresh": 0}
        get_params = {"creator_id": self.creator, "niche": BRAND["niche"], "audience": BRAND["audience"],
                      "known_for": BRAND["known_for"], "goal": BRAND["goal"], "styles": "", "watched": ""}
        rec = await self.check("/v1/feed POST c0", "POST", "/v1/feed", j_feed,
                               body={**post_body, "cursor": 0}, timeout=150, group="/v1/feed")
        t0 = time.monotonic()
        self.ctx["feed_t0"] = t0
        self._feed_point("POST c0 (cold paint)", rec, None)
        for label, method, cur in (("/v1/feed POST c1", "POST", 1), ("/v1/feed GET c0", "GET", 0),
                                   ("/v1/feed GET c1", "GET", 1)):
            if method == "POST":
                r = await self.check(label, "POST", "/v1/feed", j_feed, body={**post_body, "cursor": cur},
                                     timeout=150, group="/v1/feed")
            else:
                r = await self.check(label, "GET", "/v1/feed", j_feed, params={**get_params, "cursor": cur},
                                     timeout=150, group="/v1/feed")
            self._feed_point(label.replace("/v1/feed ", ""), r, t0)
        last_mode = self.feed_timeline[-2]["mode"]          # GET c0
        n = 0
        while last_mode != "live" and time.monotonic() - t0 < FEED_POLL_WINDOW_S:
            await asyncio.sleep(FEED_POLL_EVERY_S)
            n += 1
            r = await self.check(f"/v1/feed GET c0 poll{n}", "GET", "/v1/feed", j_feed,
                                 params={**get_params, "cursor": 0}, timeout=150, group="/v1/feed")
            self._feed_point(f"GET c0 poll{n}", r, t0)
            last_mode = (r.get("response") or {}).get("mode") if isinstance(r.get("response"), dict) else None
        # Page 1 after the window: its live_fast entry has a 90s TTL, then SWR upgrades it.
        r = await self.check("/v1/feed GET c1 (after polls)", "GET", "/v1/feed", j_feed,
                             params={**get_params, "cursor": 1}, timeout=150, group="/v1/feed")
        self._feed_point("GET c1 (after polls)", r, t0)

    async def digest_lane(self) -> None:
        if self.skip_digest:
            self.skip("/v1/onboarding/digest", "POST /v1/onboarding/digest", "--skip-digest")
            return
        body = {**BRAND, "creator_id": self.creator}        # thin account: no handle, no transcript
        start = await self.check("/v1/onboarding/digest POST", "POST", "/v1/onboarding/digest",
                                 j_digest_start, body=body, timeout=60, group="/v1/onboarding/digest")
        sb = start.get("response") if isinstance(start.get("response"), dict) else {}
        job_id = sb.get("job_id")
        if not job_id:
            return
        path = f"/v1/onboarding/digest/{job_id}"
        t0 = time.monotonic()
        last: dict | None = None
        while time.monotonic() - t0 < DIGEST_POLL_WINDOW_S:
            await asyncio.sleep(DIGEST_POLL_EVERY_S)
            res = await self.request("GET", path, timeout=30)
            b = res.get("body") if isinstance(res.get("body"), dict) else {}
            self.digest_timeline.append({"t": round(time.monotonic() - t0, 1), "http": res.get("status"),
                                         "latency_s": res.get("latency_s"), "status": b.get("status"),
                                         "stage": b.get("stage")})
            last = res
            if res.get("status") != 200 or b.get("status") in ("ready", "failed"):
                break
        self.log(f"   digest polls: {[(p['t'], p['stage']) for p in self.digest_timeline]}")
        if last is not None:
            self.finish("/v1/onboarding/digest GET (final poll)", "GET", path, "/v1/onboarding/digest",
                        {"params": None, "json": None, "polls": len(self.digest_timeline)}, last,
                        j_digest_final)

    def main_tasks(self) -> list[Callable[[], Any]]:
        c, B = self.creator, BRAND
        S = SAMPLE_SCRIPT
        tasks: list[Callable[[], Any]] = []

        def add(label, method, path, judge, **kw):
            tasks.append(lambda: self.check(label, method, path, judge, **kw))

        # Heavy (Apify-backed) first so their latency overlaps everything else.
        if not self.skip_scrape:
            add("/v1/connect/channel-read", "POST", "/v1/connect/channel-read", j_channel_read,
                body={"handle": SCAN_HANDLE, "platform": "instagram"}, timeout=300)
            if self.ctx.get("video_url"):
                add("/v1/analyze-video", "POST", "/v1/analyze-video", j_analyze_video,
                    body={"url": self.ctx["video_url"], "brand": B, "memory": {}, "creator_id": c},
                    timeout=420)
            else:
                self.skip("/v1/analyze-video", "POST /v1/analyze-video",
                          "no non-empty reel video_url in any niche to test with (see reels findings)")
            add("/v1/emulate/analyze", "POST", "/v1/emulate/analyze", j_emulate,
                body={"handle": EMULATE_HANDLE, "platform": "instagram"}, timeout=180)
        else:
            for lbl, rt in (("/v1/connect/channel-read", "POST /v1/connect/channel-read"),
                            ("/v1/analyze-video", "POST /v1/analyze-video"),
                            ("/v1/emulate/analyze", "POST /v1/emulate/analyze")):
                self.skip(lbl, rt, "--skip-scrape")
        add("/v1/scripts", "POST", "/v1/scripts", j_scripts,
            body={**B, "creator_id": c, "count": 3, "style": "talking_head",
                  "pillar": "Strength for desk workers",
                  "pillar_summary": "Short, specific strength fixes for people who sit all day"},
            timeout=240)
        tasks.append(self.converse_task)
        if self.ctx.get("mimic_reel"):
            add("/v1/mimic", "POST", "/v1/mimic", j_mimic,
                body={"reel": self.ctx["mimic_reel"], "brand": B, "memory": {}, "creator_id": c},
                timeout=240)
        else:
            self.skip("/v1/mimic", "POST /v1/mimic", "no reel came back from GET /v1/reels to mimic")
        add("/v1/pillars", "POST", "/v1/pillars", j_pillars, body=dict(B), timeout=240)
        add("/v1/voice-onboarding/finalize", "POST", "/v1/voice-onboarding/finalize", j_voice_finalize,
            body={**B, "transcript": VOICE_TRANSCRIPT}, timeout=240)
        if self.ctx.get("video_url"):
            add("/v1/media/analyze [video]", "POST", "/v1/media/analyze", j_media,
                body={"content_hash": hashlib.sha256(f"{self.ctx['video_url']}|{self.run_id}".encode())
                      .hexdigest(), "filename": "reel.mp4", "kind": "video",
                      "public_url": self.ctx["video_url"], "creator_id": c},
                timeout=180, group="/v1/media/analyze")
        else:
            self.skip("/v1/media/analyze [video]", "POST /v1/media/analyze",
                      "no non-empty reel video_url in any niche to test with (see reels findings)")
        if self.ctx.get("thumb_url"):
            add("/v1/media/analyze [photo]", "POST", "/v1/media/analyze", j_media,
                body={"content_hash": hashlib.sha256(f"{self.ctx['thumb_url']}|{self.run_id}".encode())
                      .hexdigest(), "filename": "reel-thumb.jpg", "kind": "photo",
                      "public_url": self.ctx["thumb_url"], "creator_id": c},
                timeout=120, group="/v1/media/analyze")
        else:
            self.skip("/v1/media/analyze [photo]", "POST /v1/media/analyze",
                      "no reel thumbnail_url returned 200 image/* to test with")
        add("/v1/broll/match [pexels+vision]", "POST", "/v1/broll/match", j_broll_pexels,
            body={"cue_text": "woman doing rows with a resistance band at an office desk",
                  "style": "talking_head", "top_k": 3,
                  "corpus": [{"asset_id": "beach", "description": "sunset over a quiet beach",
                              "tags": ["beach", "sunset"], "broll_suitability": 10}]},
            timeout=120, group="/v1/broll/match")
        add("/v1/broll/match [tie-break]", "POST", "/v1/broll/match", j_broll_tiebreak,
            body={"cue_text": "lifter pulling a heavy deadlift close-up", "style": "talking_head",
                  "top_k": 5, "corpus": [
                      {"asset_id": "decoy-sign", "description": "close-up of a heavy deadlift sign on a "
                       "locked gym door, no lifter pulling anything", "tags": [], "broll_suitability": 80},
                      {"asset_id": "real-lift", "description": "close-up of a lifter pulling a heavy "
                       "deadlift off the floor", "tags": [], "broll_suitability": 80}]},
            timeout=90, group="/v1/broll/match")
        add("/v1/hooks", "POST", "/v1/hooks", j_hooks,
            body={**B, "topic": "why rows fix desk posture better than stretching",
                  "style": "talking_head", "creator_id": c}, timeout=180)
        add("/v1/steer", "POST", "/v1/steer", j_steer,
            body={**B, "script": S, "creator_id": c,
                  "instruction": "Make it more casual and a bit shorter, like I'm talking to a friend "
                                 "at the gym."}, timeout=180)
        add("/v1/suggestions/next-idea", "GET", "/v1/suggestions/next-idea", j_next_idea,
            params={"creator_id": c, "niche": B["niche"]}, timeout=120)
        add("/v1/coach/today", "GET", "/v1/coach/today", j_coach, params={"creator_id": c}, timeout=90)
        add("/v1/teardown", "POST", "/v1/teardown", j_teardown,
            body={"clip": {"formatName": "myth-buster", "caption": S["hook"], "predictedScore": 72,
                           "metrics": {}}}, timeout=120)
        add("/v1/insights [POST coach]", "POST", "/v1/insights", j_insights_post,
            body={**B, "persona": "closer", "summary": INSIGHTS_SUMMARY}, timeout=120)
        add("/v1/score", "POST", "/v1/score", j_score,
            body={"hook": S["hook"], "body": S["body"], "style": "talking_head"}, timeout=90)
        add("/v1/captions", "POST", "/v1/captions", j_captions,
            body={"hook": S["hook"], "body": S["body"]}, timeout=90)
        add("/v1/social-caption", "POST", "/v1/social-caption", j_social,
            body={"hook": S["hook"], "body": S["body"], "cta": S["cta"], "niche": B["niche"],
                  "audience": B["audience"], "platform": "instagram", "creator_id": c}, timeout=90)
        add("/v1/onboarding/direction-options", "POST", "/v1/onboarding/direction-options", j_direction,
            body={"creator_id": c, "signals": DIRECTION_SIGNALS}, timeout=120)
        add("/v1/scripts/tutorial", "POST", "/v1/scripts/tutorial", j_tutorial,
            body={"creator_id": c, "script": S, "reasoning": TUTORIAL_REASONING, "knowledge": "basic"},
            timeout=120)
        add("/v1/memory/distill", "POST", "/v1/memory/distill", j_distill,
            body={"creator_id": c, "transcript": DISTILL_TRANSCRIPT, "memory": {}, "brand": B},
            timeout=120)
        add("/v1/tts", "POST", "/v1/tts", j_tts,
            body={"text": "Rows fix the desk hunch. Three sets, twice a week."}, timeout=60,
            binary_ok=True)
        add("/v1/write/turn", "POST", "/v1/write/turn", j_write_turn,
            body={"creator_id": c, "script": {"title": S["title"], "body": S["body"]},
                  "instruction": "Make the second paragraph punchier and add one concrete number."},
            timeout=180)
        add("/v1/write/from-brief", "POST", "/v1/write/from-brief", j_from_brief,
            body={"creator_id": c, "brief": FROM_BRIEF, "brand": B}, timeout=180)
        add("/v1/brand-summary", "POST", "/v1/brand-summary",
            j_brand_summary, body={"brand": B, "memory": {}, "creator_id": c}, timeout=120)
        add("/v1/voice-onboarding/session", "POST", "/v1/voice-onboarding/session", j_voice_session,
            body=dict(B), timeout=60, scan_dashes=False)
        add("/v1/trends", "GET", "/v1/trends", j_trends, params={"niche": B["niche"]}, timeout=60,
            group="/v1/trends")
        add("/v1/styles", "GET", "/v1/styles", j_styles, params={"niche": B["niche"]}, timeout=60,
            scan_dashes=False)
        add("/v1/reels/examples", "GET", "/v1/reels/examples", j_examples,
            params={"format": "talking_head", "niche": B["niche"]}, timeout=60, scan_dashes=False)
        add("/v1/recommendations", "GET", "/v1/recommendations", j_recs,
            params={"niche": B["niche"], "creator_id": c}, timeout=60)
        add("/v1/insights/learned", "GET", "/v1/insights/learned", j_learned,
            params={"creator_id": c}, timeout=60)
        return tasks

    async def converse_task(self) -> None:
        """A 3-turn text chat: direction -> script request (should chain /v1/scripts) -> save idea."""
        c = self.creator
        turns = [
            "I coach desk workers on strength training. I'm stuck on what to post this week, give me a "
            "direction.",
            "Love it. Write me a script about why rows fix the desk hunch better than stretching.",
            "Save this idea for later: a 5-minute lunch-break strength circuit you can do in office clothes.",
        ]
        messages: list[dict] = []
        for i, text in enumerate(turns, 1):
            messages.append({"role": "user", "content": text})
            rec = await self.check(f"/v1/converse turn{i}", "POST", "/v1/converse", j_converse,
                                   body={"creator_id": c, "mode": "chat", "messages": list(messages),
                                         "brand": BRAND, "memory": {}, "persona": "closer",
                                         "response_length": "medium"},
                                   timeout=300, group="/v1/converse")
            b = rec.get("response") if isinstance(rec.get("response"), dict) else {}
            messages.append({"role": "assistant", "content": b.get("reply") or ""})

    def post_digest_tasks(self) -> list[Callable[[], Any]]:
        c = self.creator
        tasks: list[Callable[[], Any]] = [
            lambda: self.check("/v1/insights [GET feed]", "GET", "/v1/insights", j_insights_get,
                               params={"creator_id": c, "limit": 20}, timeout=60),
            lambda: self.check("/v1/strategy", "GET", "/v1/strategy", j_strategy,
                               params={"creator_id": c}, timeout=60),
            lambda: self.check("/v1/morning-brief", "GET", "/v1/morning-brief", j_morning_brief,
                               params={"creator_id": c}, timeout=90),
            lambda: self.check("/v1/today", "GET", "/v1/today", j_today, params={"creator_id": c},
                               timeout=120),
        ]
        first = next((r for r in self.results if r["label"] == "/v1/trends"), None)
        if first is None or first.get("reason_code") != "live":
            tasks.append(lambda: self.check("/v1/trends (recheck)", "GET", "/v1/trends", j_trends,
                                            params={"niche": BRAND["niche"]}, timeout=60,
                                            group="/v1/trends"))
        # Did page 0 EVER upgrade to the Opus-quality "live" set? If the ~95s poll window
        # ended on live_fast, look once more now (minutes later) to tell "slow" from "never".
        polls = [p for p in self.feed_timeline if p["label"].startswith(("GET c0", "POST c0"))]
        if polls and polls[-1]["mode"] != "live":
            async def late_feed() -> None:
                r = await self.check("/v1/feed GET c0 (late check)", "GET", "/v1/feed", j_feed,
                                     params={"creator_id": c, "niche": BRAND["niche"],
                                             "audience": BRAND["audience"],
                                             "known_for": BRAND["known_for"], "goal": BRAND["goal"],
                                             "styles": "", "watched": "", "cursor": 0},
                                     timeout=150, group="/v1/feed")
                self._feed_point("GET c0 (late check)", r, self.ctx.get("feed_t0"))
            tasks.append(late_feed)
        return tasks

    async def phase_brand_scan(self) -> None:
        if self.skip_scrape:
            self.skip("/v1/brand-scan/handle", "POST /v1/brand-scan/handle", "--skip-scrape")
            return
        await self.check("/v1/brand-scan/handle", "POST", "/v1/brand-scan/handle", j_brand_scan,
                         body={**BRAND, "handle": SCAN_HANDLE, "platform": "instagram",
                               "creator_id": self.creator}, timeout=300)

    # -- summary ----------------------------------------------------------------
    def script_lengths(self) -> dict:
        groups: dict[str, list[dict]] = {}

        def add(group: str, sc: Any, src: str) -> None:
            if not isinstance(sc, dict) or not (sc.get("body") or sc.get("hook")):
                return
            w = spoken_words(sc)
            groups.setdefault(group, []).append({
                "src": src, "title": sc.get("title"), "words": w,
                "implied_s_at_165wpm": round(w / WPM * 60, 1),
                "durationSeconds": sc.get("durationSeconds"), "targetSeconds": sc.get("targetSeconds"),
                "style": sc.get("style"), "template": is_template_script(sc)})

        feed_seen: set[str] = set()
        for r in self.results:
            b = r.get("response") if isinstance(r.get("response"), dict) else {}
            if r.get("status") != 200:
                continue
            if r["label"] == "/v1/scripts":
                for sc in b.get("scripts") or []:
                    add("/v1/scripts", sc, r["label"])
            elif r["group"] == "/v1/feed":
                for it in b.get("items") or []:
                    if isinstance(it, dict) and it.get("type") == "script":
                        sc = it.get("script") or {}
                        fp = fingerprint(sc)
                        if fp in feed_seen:
                            continue
                        feed_seen.add(fp)
                        add(f"feed ({b.get('mode')})", sc, r["label"])
            elif r["group"] == "/v1/converse":
                for sc in ((b.get("payload") or {}).get("scripts") or []):
                    add("converse chained /v1/scripts", sc, r["label"])
            elif r["label"].startswith("/v1/onboarding/digest GET"):
                for sc in b.get("scripts") or []:
                    add("digest starter scripts", sc, r["label"])
            elif r["label"] == "/v1/mimic":
                add("mimic", b.get("script"), r["label"])
            elif r["label"] == "/v1/analyze-video":
                add("analyze-video your_version", b.get("your_version"), r["label"])
            elif r["label"] == "/v1/steer":
                add("steer", b.get("script"), r["label"])
            elif r["label"] == "/v1/write/from-brief":
                add("write/from-brief", b, r["label"])
        floor = round(TARGET_MIN_S * WPM / 60)
        out = {"target": f"{TARGET_MIN_S}-{TARGET_MAX_S}s talking head at {WPM} wpm = "
                         f"{floor}-{round(TARGET_MAX_S * WPM / 60)} spoken words"}
        for g, rows in groups.items():
            ws = [x["words"] for x in rows]
            out[g] = {"n": len(ws), "median_words": statistics.median(ws) if ws else None,
                      "min": min(ws) if ws else None, "max": max(ws) if ws else None,
                      "median_implied_s": round(statistics.median(ws) / WPM * 60, 1) if ws else None,
                      "n_under_floor": sum(1 for w in ws if w < floor), "scripts": rows}
        return out

    def alarms(self, lengths: dict) -> list[str]:
        out: list[str] = []
        dash_seen: set[tuple] = set()
        for r in self.results:
            st = r.get("status")
            if st is not None and st >= 500:
                out.append(f"5xx: {r['label']} -> HTTP {st}")
            if r.get("error"):
                out.append(f"transport error: {r['label']}: {r['error']}")
            if r.get("slow"):
                out.append(f"slow (>{int(SLOW_S)}s): {r['label']} took {r['latency_s']}s")
            fb = fallback_hits(r.get("template_hits") or [])
            if fb and r.get("mode") in ("live", "live_fast", "live_structure", "cached"):
                out.append(f"template copy inside a live-labeled payload: {r['label']}: {fb}")
            if r.get("dash_hits"):
                # Template (deterministic) copy vs model-written copy: the cold-start "why" line
                # and any mock payload are code-authored; everything else came from a model.
                tpl = [h for h in r["dash_hits"] if TEMPLATE_SIGS["cold_start_reason"][1] in h["snippet"]
                       or r.get("mode") == "mock"]
                llm = [h for h in r["dash_hits"] if h not in tpl]
                for kind, hits in (("LLM-written", llm), ("deterministic template", tpl)):
                    if not hits:
                        continue
                    paths = tuple(sorted({re.sub(r"\[\d+\]", "[]", h["path"]) for h in hits}))
                    key = (kind, r.get("group"), paths)
                    if key in dash_seen:
                        continue
                    dash_seen.add(key)
                    out.append(f"em/en dash in {kind} copy: {r['label']}: {list(paths)[:6]} "
                               f"e.g. {clip(hits[0]['snippet'], 90)!r}")
            if r.get("reason_code", "").startswith("live_label"):
                out.append(f"live-labeled fallback: {r['label']}: {r['note']}")
        floor = round(TARGET_MIN_S * WPM / 60)
        for g, v in lengths.items():
            if isinstance(v, dict) and v.get("median_words") is not None and v["median_words"] < floor:
                out.append(f"short scripts: {g} median {v['median_words']} words (~{v['median_implied_s']}s "
                           f"at {WPM} wpm) < {floor} words needed for {TARGET_MIN_S}s")
        rz = self.ctx.get("readyz") or {}
        if isinstance(rz, dict) and rz.get("ai") != "live":
            out.append(f"/readyz reports ai={rz.get('ai')!r}")
        return out

    def write(self) -> dict:
        lengths = self.script_lengths()
        alarms = self.alarms(lengths)
        report = {
            "meta": {"base": self.base, "creator_id": self.creator, "run_id": self.run_id,
                     "started_at": self.started_at,
                     "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "wall_s": round(time.monotonic() - self.t_start, 1),
                     "deployed_commit_assumed": DEPLOYED_COMMIT, "brand": BRAND,
                     "readyz": self.ctx.get("readyz"), "max_inflight": MAX_INFLIGHT,
                     "skip_scrape": self.skip_scrape, "skip_digest": self.skip_digest,
                     "inputs": {"video_url": self.ctx.get("video_url"),
                                "video_url_playable": self.ctx.get("video_url_playable"),
                                "thumb_url": self.ctx.get("thumb_url"),
                                "mimic_reel_id": (self.ctx.get("mimic_reel") or {}).get("id")}},
            "summary": {"verdicts": dict(Counter(r["verdict"] for r in self.results)),
                        # backend requests actually sent: every called row + every digest poll
                        # (the digest "final poll" row IS one of those polls, so not twice)
                        "calls": sum(1 for r in self.results if r.get("status") is not None
                                     and not r["label"].startswith("/v1/onboarding/digest GET"))
                                 + len(self.digest_timeline),
                        "not_live": [{"label": r["label"], "verdict": r["verdict"],
                                      "reason_code": r["reason_code"], "note": r["note"]}
                                     for r in self.results
                                     if r["verdict"] != "LIVE" and r.get("reason_code") != "infra"]},
            "alarms": alarms,
            "results": [{k: v for k, v in r.items()} for r in self.results],
            "feed_timeline": self.feed_timeline,
            "digest_timeline": self.digest_timeline,
            "reels_findings": self.reels_findings,
            "thumb_probes": self.thumb_probes,
            "script_lengths": lengths,
        }
        report["meta"].update(self.meta_override)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return report

    def print_table(self, report: dict) -> None:
        print("\n" + "=" * 150)
        print(f"{'route':44} {'st':>4} {'mode':>14} {'lat_s':>7}  {'verdict':7}  note")
        print("-" * 150)
        for r in self.results:
            print(f"{clip(r['label'], 44):44} {str(r['status']):>4} {str(r['mode']):>14} "
                  f"{str(r['latency_s']):>7}  {r['verdict']:7}  {clip(r['note'], 70)}")
        print("-" * 150)
        print("verdicts:", report["summary"]["verdicts"])
        print("\nALARMS:")
        for a in report["alarms"]:
            print("  -", a)
        print("\nscript lengths (median spoken words):")
        for g, v in report["script_lengths"].items():
            if isinstance(v, dict):
                print(f"  {g:34} n={v['n']:>2} median={v['median_words']} min={v['min']} max={v['max']} "
                      f"(~{v['median_implied_s']}s) under_floor={v['n_under_floor']}")
        print("\nreels:")
        for n, f in report["reels_findings"].items():
            print(f"  {n:18} unique={f['unique_reels']:>2} with_video_url={f['with_video_url']:>2} "
                  f"playable={f['video_probe_ok']:>2} hosts={f['video_hosts']} "
                  f"probes={f['video_probe_statuses']} off_niche={[p['off_niche'] for p in f['pages']]}")
        print(f"\nwrote {self.out_path}")

    async def run(self) -> dict:
        self.sem = asyncio.Semaphore(MAX_INFLIGHT)
        self.media_sem = asyncio.Semaphore(3)
        async with httpx.AsyncClient(headers={"User-Agent": "marque-prod-smoke/1 (eval/prod_smoke.py)"},
                                     timeout=150) as client:
            self.client = client
            self.log(f"base={self.base} creator={self.creator} run_id={self.run_id}")
            await self.phase_health()
            await self.phase_reels()
            # Lane 1: feed (+ upgrade polls) then the digest. Lanes 2-3: everything else.
            # Global semaphore still caps the backend at MAX_INFLIGHT.

            async def lane1() -> None:
                await self.feed_lane()
                await self.digest_lane()
            await asyncio.gather(lane1(), self.run_pool(self.main_tasks(), workers=MAX_INFLIGHT - 1))
            # /v1/ideas first: the digest seeds the idea bank, and the morning-brief verdict
            # reads its promoted briefs to tell "nothing to say" from "flag off".
            await self.check("/v1/ideas", "POST", "/v1/ideas", j_ideas,
                             body={"creator_id": self.creator, "limit": 5}, timeout=60)
            await self.run_pool(self.post_digest_tasks(), workers=MAX_INFLIGHT)
            await self.phase_brand_scan()
        report = self.write()
        self.print_table(report)
        return report


VOICE_TRANSCRIPT = [
    {"role": "agent", "text": "What kind of content do you make?"},
    {"role": "user", "text": "Strength training for people with desk jobs. Mostly engineers and "
                             "accountants who sit ten hours a day."},
    {"role": "agent", "text": "Who do you most want watching?"},
    {"role": "user", "text": "Busy professionals who think they don't have time to train. I want them "
                             "to hire me as their online coach."},
    {"role": "agent", "text": "What do you want to be known for?"},
    {"role": "user", "text": "No-nonsense programs. Three short sessions a week, heavy basics, no fads, "
                             "and I never sell supplements."},
    {"role": "agent", "text": "Any content ideas you're excited about?"},
    {"role": "user", "text": "Why rows beat stretching for posture, a lunch-break workout in office "
                             "clothes, and getting your first pull-up after thirty."},
]
DISTILL_TRANSCRIPT = [
    {"role": "user", "text": "So I mostly train software engineers and accountants, people who sit ten "
                             "hours a day."},
    {"role": "assistant", "text": "What do they struggle with most?"},
    {"role": "user", "text": "Honestly their upper back is shot, and they have maybe thirty minutes, three "
                             "times a week, at lunch."},
    {"role": "assistant", "text": "What do you want to be known for?"},
    {"role": "user", "text": "I want to be the guy who gets desk workers their first real pull-up. I hate "
                             "fad workouts and I never sell supplements."},
]
INSIGHTS_SUMMARY = ("Last 7 days: 3 posts. Myth-buster 'rows beat stretching': 14.2K views, 610 likes, "
                    "88 saves, 41% avg watch. Listicle '3 desk stretches': 3.1K views, 95 likes, 22% avg "
                    "watch. POV story about a client's first pull-up: 2.2K views, 140 likes, 31 comments.")
DIRECTION_SIGNALS = ("I coach busy desk workers on strength training. I talk to camera about fixing posture "
                     "and getting strong in three short sessions a week, and I want online coaching "
                     "clients.")
TUTORIAL_REASONING = ("Contrarian hook reframes the cause of desk back pain. The body escalates from the "
                      "cause to one prescriptive fix with a concrete dose, then pays off with a felt "
                      "result the viewer can check on Friday.")
FROM_BRIEF = {"title": "Rows beat stretching for desk posture",
              "summary": "Why pulling strength, not stretching, fixes the desk hunch.",
              "beginning": "Stretching isn't fixing your posture.",
              "middle": "Desk work shortens the front of your body. Rows train the upper back to hold your "
                        "shoulders where they belong. Three sets of ten, twice a week.",
              "ending": "Add rows for two weeks and notice your neck."}


def rejudge(path: pathlib.Path) -> dict:
    """Re-score a saved report OFFLINE (no network): re-run the current judges/alarms over the
    stored response bodies and rewrite the file. Lets the verdict logic evolve without paying
    for (or loading prod with) another full run."""
    old = json.loads(path.read_text())
    meta = old["meta"]
    s = Smoke(meta["base"], meta["creator_id"], path, bool(meta.get("skip_scrape")),
              bool(meta.get("skip_digest")))
    s.quiet = True
    s.run_id, s.started_at = meta.get("run_id", s.run_id), meta.get("started_at", s.started_at)
    inputs = meta.get("inputs") or {}
    s.ctx = {"readyz": meta.get("readyz"), "video_url": inputs.get("video_url"),
             "video_url_playable": inputs.get("video_url_playable"), "thumb_url": inputs.get("thumb_url"),
             "mimic_reel": {"id": inputs.get("mimic_reel_id")}}
    s.feed_timeline = old.get("feed_timeline") or []
    s.digest_timeline = old.get("digest_timeline") or []
    s.reels_findings = old.get("reels_findings") or {}
    s.thumb_probes = old.get("thumb_probes") or []
    for r in old["results"]:
        if r.get("reason_code") == "skipped":
            s.results.append(r)
            continue
        method, route_path = r["route"].split(" ", 1)
        res = {"status": r.get("status"), "latency_s": r.get("latency_s"), "error": r.get("error"),
               "body": r.get("response"), "headers": r.get("headers")}
        s.finish(r["label"], method, route_path, r.get("group") or r["label"], r.get("request"), res,
                 judge_for(r["label"], r.get("judge", "")),
                 scan_dashes=not r["label"].startswith(_NO_DASH_SCAN),
                 retried=bool(r.get("retried_after_422")))
    s.meta_override = {"finished_at": meta.get("finished_at"), "wall_s": meta.get("wall_s"),
                       "rejudged_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    report = s.write()
    s.print_table(report)
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    today = dt.date.today()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"backend base URL (default {DEFAULT_BASE})")
    ap.add_argument("--creator", default=f"qa-smoke-{today:%m%d}",
                    help="creator_id to use everywhere (default qa-smoke-MMDD: fresh per day)")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).parent / "out" /
                                         f"prod_smoke_{today.isoformat()}.json"))
    ap.add_argument("--skip-scrape", action="store_true",
                    help="skip Apify-backed calls (emulate, channel-read, analyze-video, brand-scan)")
    ap.add_argument("--skip-digest", action="store_true", help="skip the onboarding digest job")
    ap.add_argument("--rejudge", metavar="JSON",
                    help="offline: re-score a saved report with the current judges (no network)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.rejudge:
        report = rejudge(pathlib.Path(args.rejudge))
    else:
        smoke = Smoke(args.base, args.creator, pathlib.Path(args.out), args.skip_scrape, args.skip_digest)
        report = asyncio.run(smoke.run())
    return 1 if report["summary"]["verdicts"].get("ERROR") else 0


if __name__ == "__main__":
    sys.exit(main())
