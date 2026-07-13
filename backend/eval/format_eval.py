"""LOOP F — render-formatting eval harness.

Renders the golden render-plan corpus (make_format_corpus.py) LOCALLY via
`npx remotion render` (no Lambda, no AWS — free, ~seconds per fixture at
scale=0.5) against a synthetic source video, then runs DETERMINISTIC pixel/
audio checks against the output: duration matches the plan's total_frames,
no black/frozen render, the faceless+mono fixture actually reads desaturated
(formatting fix #7 regression guard), and the speed-2x fixture's audio pitch
is preserved (formatting risk #8 regression guard — see pitch_check.py).

This is the FREE default tier (per the user's "closest free alternative"
budget decision). A vision-scored tier (--score) also exists for the cases the
deterministic checks and the pure-math layout tests structurally can't cover —
real font-metric overflow, actual visual collisions, typography/legibility
judgment — but it costs real money and is never run implicitly: it requires
BOTH the --score flag AND ANTHROPIC_API_KEY, exiting 2 (not a silent skip) if
the flag is passed without a key, matching edl_eval's --live convention and
scripts/gate.sh's "never silently downgrade a tier" contract.

Usage:
    python3 -m eval.format_eval --render                  # free tier (default)
    python3 -m eval.format_eval --render --only=adv-speed-2x-pitch
    ANTHROPIC_API_KEY=... python3 -m eval.format_eval --render --score   # + vision tier
"""
from __future__ import annotations
import base64
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import httpx

from eval.make_format_corpus import GOLDEN_DIR, PLACEHOLDER_SOURCE, write_corpus
from eval.pitch_check import check_pitch_preserved

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RENDER_DIR = REPO_ROOT / "render"
FIXTURES_DIR = RENDER_DIR / "fixtures"
SOURCE_VIDEO = FIXTURES_DIR / "source.mp4"
OUT_DIR = RENDER_DIR / "out"
HTTP_PORT = 8799
FPS = 30

# style -> the Composition id in render/src/Root.tsx
COMPOSITION_ID = {
    "talking_head": "Marque-TalkingHead", "faceless": "Marque-Faceless",
    "split_three": "Marque-SplitThree", "fast_cuts": "Marque-FastCuts",
    "green_screen": "Marque-GreenScreen", "broll_cutaway": "Marque-BrollCutaway",
    "duet_split": "Marque-DuetSplit",
}

DURATION_TOLERANCE_S = 0.3
NON_BLACK_MIN_YAVG = 4.0
NON_BLACK_MIN_FRACTION = 0.95
FACELESS_MONO_MAX_SATAVG = 20.0

FORMAT_RUBRIC_PATH = REPO_ROOT / "backend" / "knowledge" / "format_rubric.md"
VISION_SAMPLE_FRAMES = 6
VISION_SCORE_MIN = 70   # matches SELF_REVIEW_THRESHOLD's convention in review_rubric.md


# ---------------------------------------------------------------------------
# Local HTTP server for the synthetic source (so sourceUrl is a real http://
# URL the Chrome-headless render process can fetch, with zero prod-code
# changes — no different from any other externally-hosted source).
# ---------------------------------------------------------------------------

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):   # noqa: A002 - matches base signature
        pass

    def handle_error(self, request, client_address):
        # A render subprocess that dies mid-fetch (e.g. a bundle error) leaves the
        # socket half-closed; that's a normal consequence of the OTHER failure, not
        # a bug in this harness — don't let a BrokenPipeError traceback bury the
        # actual per-fixture report above it.
        pass


def _start_source_server() -> http.server.HTTPServer:
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(FIXTURES_DIR), **kw)
    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# ffmpeg-based deterministic checks
# ---------------------------------------------------------------------------

def _ffprobe_duration_s(video_path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, check=True, text=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def _signalstats_frames(video_path: Path) -> list[dict[str, float]]:
    """Per-frame {YAVG, SATAVG, ...} via ffmpeg's signalstats filter."""
    proc = subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    frames: list[dict[str, float]] = []
    current: dict[str, float] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("frame:"):
            if current:
                frames.append(current)
            current = {}
        m = re.search(r"lavfi\.signalstats\.(\w+)=([\d.eE+-]+)", line)
        if m:
            try:
                current[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    if current:
        frames.append(current)
    return frames


def check_duration(video_path: Path, expected_frames: int) -> dict:
    actual_s = _ffprobe_duration_s(video_path)
    expected_s = expected_frames / FPS
    if actual_s is None:
        return {"ok": False, "reason": "ffprobe failed to read duration"}
    ok = abs(actual_s - expected_s) <= DURATION_TOLERANCE_S
    return {"ok": ok, "expected_s": round(expected_s, 3), "actual_s": round(actual_s, 3)}


def check_non_black(video_path: Path) -> dict:
    frames = _signalstats_frames(video_path)
    if not frames:
        return {"ok": False, "reason": "no frames decoded for signalstats"}
    ok_frames = [f for f in frames if f.get("YAVG", 0) > NON_BLACK_MIN_YAVG]
    fraction = len(ok_frames) / len(frames)
    return {"ok": fraction >= NON_BLACK_MIN_FRACTION, "fraction_non_black": round(fraction, 3),
            "total_frames_sampled": len(frames)}


def check_faceless_mono_desaturated(video_path: Path) -> dict:
    frames = _signalstats_frames(video_path)
    if not frames:
        return {"ok": False, "reason": "no frames decoded for signalstats"}
    avg_sat = sum(f.get("SATAVG", 0) for f in frames) / len(frames)
    return {"ok": avg_sat <= FACELESS_MONO_MAX_SATAVG, "avg_saturation": round(avg_sat, 2),
            "max_allowed": FACELESS_MONO_MAX_SATAVG}


# ---------------------------------------------------------------------------
# Render driver
# ---------------------------------------------------------------------------

def _load_fixtures(only: str | None = None) -> list[dict]:
    if not GOLDEN_DIR.exists() or not any(GOLDEN_DIR.glob("*.json")):
        write_corpus()
    fixtures = []
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        fx = json.loads(path.read_text())
        if only and fx["id"] != only:
            continue
        fixtures.append(fx)
    return fixtures


def _substitute_placeholder(obj, source_url: str):
    """Recursively replace every occurrence of PLACEHOLDER_SOURCE with the real
    local server URL — the placeholder shows up not just as the top-level
    sourceUrl but also nested inside broll[].resolved_url / react_source.resolved_url
    for fixtures that exercise those features."""
    if isinstance(obj, dict):
        return {k: _substitute_placeholder(v, source_url) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_placeholder(v, source_url) for v in obj]
    if obj == PLACEHOLDER_SOURCE:
        return source_url
    return obj


def _render_fixture(fx: dict, source_url: str) -> tuple[bool, str]:
    style = fx["edl"]["style"]
    comp_id = COMPOSITION_ID.get(style)
    if not comp_id:
        return False, f"no composition mapped for style {style!r}"
    props = _substitute_placeholder(fx, source_url)
    props_path = OUT_DIR / f"{fx['id']}.props.json"
    out_path = OUT_DIR / f"{fx['id']}.mp4"
    props_path.write_text(json.dumps(props))
    result = subprocess.run(
        ["npx", "remotion", "render", "src/index.ts", comp_id, str(out_path),
         f"--props={props_path}", "--scale=0.5", "--concurrency=2"],
        cwd=str(RENDER_DIR), capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-500:]
        return False, f"remotion render failed: {tail}"
    return True, str(out_path)


def _score_fixture(fx: dict, out_path: Path) -> dict:
    checks: dict[str, dict] = {}
    checks["duration"] = check_duration(out_path, fx["edl"]["total_frames"])
    checks["non_black"] = check_non_black(out_path)
    if fx["id"] == "adv-faceless-mono-broll":
        checks["faceless_mono_desaturated"] = check_faceless_mono_desaturated(out_path)
    if fx["id"] == "adv-speed-2x-pitch":
        checks["pitch_preserved"] = check_pitch_preserved(str(out_path))
    return checks


# ---------------------------------------------------------------------------
# Vision tier (optional, paid, --score) — see module docstring for the gating
# contract. Deliberately NOT cached (sha256(plan+rubric) caching was floated in
# design but this tier is explicitly rare/ask-first; a cache is a performance
# optimization for a path that isn't meant to run often, so it's skipped here
# rather than gold-plating an optional tier).
# ---------------------------------------------------------------------------

def _sample_frames(video_path: Path, n: int = VISION_SAMPLE_FRAMES) -> list[bytes]:
    """ffmpeg-sample n evenly-spaced frames -> jpeg bytes. Mirrors main.py's
    _sample_render_frames (kept as an independent copy rather than importing
    main.py, which would drag the whole FastAPI app's import graph — DB
    clients, env requirements — into a lightweight eval CLI)."""
    with tempfile.TemporaryDirectory() as td:
        pattern = str(Path(td) / "f_%03d.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-vf", "fps=1,scale=360:-1", "-q:v", "5", pattern],
                capture_output=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            return []
        paths = sorted(Path(td).glob("f_*.jpg"))
        step = max(1, len(paths) // max(1, n))
        return [p.read_bytes() for p in paths[::step][:n]]


def score_fixture_vision(fx: dict, frames: list[bytes], api_key: str) -> dict:
    """One Sonnet-vision call scoring sampled frames against format_rubric.md.
    Never raises — any API/parse failure degrades to {"ok": False, "reason": ...}
    so a transient issue fails that fixture's vision check rather than crashing
    the whole --score pass."""
    if not frames:
        return {"ok": False, "reason": "no frames sampled"}
    from prompts import SONNET   # lazy: keep this eval tool's keyless import path light

    rubric = FORMAT_RUBRIC_PATH.read_text() if FORMAT_RUBRIC_PATH.exists() else ""
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["score_0_100", "issues"],
        "properties": {
            "score_0_100": {"type": "integer"},
            "issues": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "frame", "description"],
                "properties": {
                    "code": {"type": "string"}, "frame": {"type": "integer"},
                    "description": {"type": "string"},
                },
            }},
        },
    }
    content: list[dict] = [{"type": "text", "text":
        f"FORMAT RUBRIC:\n{rubric}\n\nFIXTURE: {fx['id']} (style={fx['edl']['style']})\n\n"
        f"The {len(frames)} images are evenly-sampled frames of this rendered composition. "
        f"Score 0-100 against the rubric and list any issues found."}]
    for jpeg in frames[:8]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii")}})
    body = {"model": SONNET, "max_tokens": 1200, "temperature": 0.0,
            "system": "You are a strict short-form video FORMATTING QA reviewer. Score against the rubric.",
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
    return {"ok": score >= VISION_SCORE_MIN, "score_0_100": score, "issues": data.get("issues", [])}


def run(only: str | None = None, score: bool = False, api_key: str | None = None) -> bool:
    SOURCE_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    if not SOURCE_VIDEO.exists():
        subprocess.run(["bash", str(REPO_ROOT / "scripts" / "make_eval_source.sh")], check=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = _load_fixtures(only)
    if not fixtures:
        print(f"[format_eval] no fixtures matched (only={only!r})")
        return False

    server = _start_source_server()
    source_url = f"http://127.0.0.1:{HTTP_PORT}/source.mp4"
    all_ok = True
    try:
        for fx in fixtures:
            ok, detail = _render_fixture(fx, source_url)
            if not ok:
                print(f"  FAIL {fx['id']:32} render error: {detail}")
                all_ok = False
                continue
            out_path = Path(detail)
            checks = _score_fixture(fx, out_path)
            if score:
                frames = _sample_frames(out_path)
                checks["vision"] = score_fixture_vision(fx, frames, api_key)
            fixture_ok = all(c["ok"] for c in checks.values())
            all_ok = all_ok and fixture_ok
            status = "PASS" if fixture_ok else "FAIL"
            print(f"  {status} {fx['id']:32} " +
                 " ".join(f"{name}={'ok' if c['ok'] else 'FAIL(' + json.dumps(c) + ')'}"
                          for name, c in checks.items()))
    finally:
        server.shutdown()
    return all_ok


def main(argv: list[str]) -> int:
    if "--render" not in argv:
        print("[format_eval] pass --render to actually render + check the corpus "
             "(nothing runs without it — this is a local render, not free of wall-clock cost)")
        return 2
    score = "--score" in argv
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if score and not api_key:
        print("[format_eval] --score SKIPPED — $ANTHROPIC_API_KEY not set "
             "(the vision tier costs real money and never runs without an explicit key)")
        return 2
    only = None
    for arg in argv:
        if arg.startswith("--only="):
            only = arg.split("=", 1)[1]
    ok = run(only=only, score=score, api_key=api_key)
    print(f"[format_eval] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
