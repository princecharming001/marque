"""LOOP U — vision tier for app/UI formatting QA (optional, paid, ask-first).

Scores each screenshot .maestro/format-audit.yaml produced against
backend/knowledge/ui_rubric.md, one Sonnet-vision call per CHANGED screenshot
(sha256-cached in .shots/.ui_eval_cache.json — an unchanged screenshot reuses
its last score rather than re-paying for an identical judgment, per the plan's
explicit "over changed screenshots (hash-skip unchanged)" design for this
loop specifically).

This is NEVER run implicitly: scripts/gate.sh only reaches it inside
`paid_tier()`, which already requires --paid AND a set $ANTHROPIC_API_KEY
before this module is even invoked (`need_env` exits 2 first) — the check
here is a direct-invocation safety net, not the primary gate.

Usage:
    python3 -m eval.ui_eval                # score .shots/format-audit-*.png
    python3 -m eval.ui_eval --suffix=xxl    # score the XXL Dynamic Type pass
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / ".maestro" / "ui-manifest.json"
SHOTS_DIR = REPO_ROOT / ".shots"
CACHE_PATH = SHOTS_DIR / ".ui_eval_cache.json"
UI_RUBRIC_PATH = REPO_ROOT / "backend" / "knowledge" / "ui_rubric.md"

UI_SCORE_MIN = 70


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _screenshot_path(screen: dict, suffix: str) -> Path:
    name = screen["screenshot"]
    if suffix:
        name = name.replace(".png", f"-{suffix}.png")
    return SHOTS_DIR / name


def score_screenshot(screen: dict, image_path: Path, api_key: str) -> dict:
    """One Sonnet-vision call scoring a single screenshot against ui_rubric.md.
    Never raises — degrades to {"ok": False, "reason": ...} on any API/parse
    failure so one bad screen fails just that entry, not the whole pass."""
    from prompts import SONNET   # lazy: keep this eval tool's keyless import path light

    rubric = UI_RUBRIC_PATH.read_text() if UI_RUBRIC_PATH.exists() else ""
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["score_0_100", "issues"],
        "properties": {
            "score_0_100": {"type": "integer"},
            "issues": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "screen_id", "description"],
                "properties": {
                    "code": {"type": "string"}, "screen_id": {"type": "string"},
                    "description": {"type": "string"},
                },
            }},
        },
    }
    content = [{"type": "text", "text":
        f"UI RUBRIC:\n{rubric}\n\nSCREEN: {screen['id']} — {screen.get('label', '')}\n\n"
        f"Score this screenshot 0-100 against the rubric and list any issues found."},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(image_path.read_bytes()).decode("ascii")}}]
    body = {"model": SONNET, "max_tokens": 800, "temperature": 0.0,
            "system": "You are a strict SwiftUI app-formatting QA reviewer. Score against the rubric.",
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json=body, timeout=60.0)
        if r.status_code != 200:
            return {"ok": False, "reason": f"anthropic http {r.status_code}"}
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        data = json.loads(text)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return {"ok": False, "reason": f"vision call failed: {e}"}
    score = data.get("score_0_100", 0)
    return {"ok": score >= UI_SCORE_MIN, "score_0_100": score, "issues": data.get("issues", [])}


def run(suffix: str = "", api_key: str | None = None) -> bool:
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ui_eval] SKIPPED — $ANTHROPIC_API_KEY not set "
             "(the vision tier costs real money and never runs without an explicit key)")
        return False
    manifest = _load_manifest()
    cache = _load_cache()
    all_ok = True
    for screen in manifest["screens"]:
        path = _screenshot_path(screen, suffix)
        if not path.exists():
            print(f"  SKIP  {screen['id']:28} screenshot not found: {path.name} "
                 f"(run scripts/ui_audit.sh first)")
            continue
        digest = _sha256(path)
        cache_key = path.name
        cached = cache.get(cache_key)
        if cached and cached.get("sha256") == digest:
            result = cached["result"]
            print(f"  CACHE {screen['id']:28} score={result.get('score_0_100', '?')} (unchanged)")
        else:
            result = score_screenshot(screen, path, api_key)
            cache[cache_key] = {"sha256": digest, "result": result}
            status = "PASS" if result.get("ok") else "FAIL"
            print(f"  {status} {screen['id']:28} score={result.get('score_0_100', '?')}")
        all_ok = all_ok and bool(result.get("ok"))
    _save_cache(cache)
    return all_ok


def main(argv: list[str]) -> int:
    suffix = ""
    for arg in argv:
        if arg.startswith("--suffix="):
            suffix = arg.split("=", 1)[1]
    ok = run(suffix=suffix)
    print(f"[ui_eval] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
