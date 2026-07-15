"""Editing-quality eval — the spec-QC gate for the AI cut.

Runs several messy raw-take fixtures through the REAL edit-plan model + the deterministic
assembler and asserts the short-form talking-head spec's core cut criteria: greetings /
flubbed retakes / sign-offs are removed, the real substance is retained (no silent
over-cut), and the hook is a scored line. This is the convergence harness the owner asked
for — run it, fix, re-run until clean.

Paid tier (needs ANTHROPIC_API_KEY). Keyless: prints "skipped (no key)" and exits 0 so it
never blocks the free gate.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prompts  # noqa: E402
import app.edl as edl  # noqa: E402

MODEL = "claude-sonnet-4-6"


def _key():
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k:
        return k
    try:
        for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def _words(lines, wps=400, pause=600):
    words, t = [], 0
    for line in lines:
        for w in line:
            words.append({"word": w, "start_ms": t, "end_ms": t + wps - 60,
                          "confidence": 0.98, "type": None, "is_emphasized": False})
            t += wps
        t += pause
    return words


# Each fixture: raw lines + which junk phrases MUST be gone + substance that MUST survive.
FIXTURES = [
    {
        "name": "greeting+retake+signoff",
        "brand": {"niche": "personal finance"},
        "lines": [
            ("hey", "guys", "welcome", "back", "to", "the", "channel"),
            ("the", "number", "one", "mistake", "is", "uh"),
            ("the", "number", "one", "mistake", "is", "saving", "before", "you", "invest"),
            ("you", "will", "lose", "fifty", "thousand", "dollars", "over", "ten", "years"),
            ("so", "yeah", "hope", "this", "helped", "thanks", "for", "watching"),
        ],
        "must_cut": ["welcome", "hope", "watching"],
        "must_keep": ["fifty", "thousand", "invest"],
    },
    {
        "name": "buried-hook+ramble",
        "brand": {"niche": "fitness"},
        "lines": [
            ("so", "today", "i", "want", "to", "chat", "about", "protein"),
            ("um", "you", "know", "it", "kind", "of", "depends", "i", "guess"),
            ("most", "people", "eat", "way", "too", "little", "protein", "to", "build", "muscle"),
            ("you", "need", "one", "gram", "per", "pound", "of", "body", "weight"),
            ("that", "is", "basically", "it", "for", "today"),
        ],
        "must_cut": ["chat", "basically"],
        "must_keep": ["gram", "pound", "protein"],
    },
    {
        "name": "double-retake",
        "brand": {"niche": "productivity"},
        "lines": [
            ("stop", "using", "to", "do", "lists", "they", "are"),
            ("okay", "let", "me", "restart"),
            ("stop", "using", "to", "do", "lists", "they", "make", "you", "less", "productive"),
            ("use", "a", "calendar", "and", "time", "block", "everything", "instead"),
        ],
        "must_cut": ["restart"],
        "must_keep": ["calendar", "block", "productive"],
    },
]


def _plan(key, brand, words):
    sys_p, usr = prompts.edit_plan_prompt("talking_head", words,
                                          {"hook": {"text": ""}, "body": "", "cta": ""}, brand)
    body = json.dumps({"model": MODEL, "max_tokens": 3000, "temperature": 0.0,
                       "system": sys_p, "messages": [{"role": "user", "content": usr}],
                       "output_config": {"format": {"type": "json_schema",
                                                     "schema": prompts.EDIT_PLAN_JSON_SCHEMA}}}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    r = urllib.request.urlopen(req, timeout=60)
    return json.loads("".join(b.get("text", "") for b in json.load(r).get("content", [])))


def _surviving(words, e):
    kept = edl._kept_intervals(e["segments"], e["drops"])
    return " ".join(w["word"] for w in words
                    if any(a <= edl.ms_to_frame(w["start_ms"]) < b for a, b in kept))


def main():
    key = _key()
    if not key:
        print("[edit_quality_eval] skipped (no key)")
        return 0
    failures = []
    for fx in FIXTURES:
        words = _words(fx["lines"])
        try:
            plan = _plan(key, fx["brand"], words)
        except Exception as e:
            failures.append(f"{fx['name']}: plan call failed: {e}")
            continue
        e = edl.assemble_edl(plan, words, "talking_head", "myth-buster").model_dump()
        surviving = _surviving(words, e).lower()
        total = edl.ms_to_frame(words[-1]["end_ms"])
        kept_frac = edl._kept_frames(e) / max(1, total)
        # QC assertions
        for junk in fx["must_cut"]:
            if junk in surviving:
                failures.append(f"{fx['name']}: junk '{junk}' survived — {surviving[:90]}")
        for keep in fx["must_keep"]:
            if keep not in surviving:
                failures.append(f"{fx['name']}: substance '{keep}' was CUT — {surviving[:90]}")
        if kept_frac < 0.20:
            failures.append(f"{fx['name']}: over-cut, only {kept_frac:.0%} kept")
        hook = plan.get("open_on") or {}
        if not isinstance(hook.get("start"), int):
            failures.append(f"{fx['name']}: no hook selected")
        print(f"[{fx['name']}] kept {kept_frac:.0%} | hook@f{hook.get('start')} | {surviving[:80]}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"\n[edit_quality_eval] PASS — {len(FIXTURES)} fixtures, spec QC clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
