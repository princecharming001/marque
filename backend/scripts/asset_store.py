"""CP-2 shared plumbing for the pre-rendered asset banks (CTA previews, style
samples, style deck).

The three CP-2 generators all need the same four things — read Supabase creds
out of backend/.env, PUT a file into the public bucket, HEAD the resulting
public URL, and ffprobe a video — so they live here once instead of being
copy-pasted three times and drifting.

Deliberately stdlib-only (urllib, subprocess). These are committed OPERATOR
scripts run from a laptop, not backend request-path code: keeping them free of
the backend's dependency graph means `python3 backend/scripts/...` works from a
bare checkout without installing requirements.txt, which matches the existing
precedent in scripts/build_music_catalog.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
RENDER_DIR = REPO_ROOT / "render"
ASSETS_DIR = BACKEND_DIR / "assets"

# Same default as main.SUPABASE_STORAGE_BUCKET — the banks live in the same
# public bucket the app already plays clips from, so no new bucket/CORS/policy
# setup is needed for the iOS pickers to stream them.
DEFAULT_BUCKET = "marque-clips"


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------

def load_env(path: Path | None = None) -> None:
    """Populate os.environ from backend/.env for any key not ALREADY set.

    Mirrors the repo's `set -a; source .env` convention. Real environment
    variables win so a one-off `SUPABASE_URL=... python3 ...` override behaves
    the way an operator expects. Secrets are never defaulted or logged here —
    a missing key surfaces as a clear "not configured" message at the call
    site instead.
    """
    env_path = path or (BACKEND_DIR / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def supabase_config() -> tuple[str, str, str] | None:
    """(base_url, key, bucket) or None when Supabase isn't configured.

    Accepts any of the three key names that appear across this repo's envs
    (SUPABASE_SERVICE_KEY is what backend/.env and Render carry; the anon key
    is enough for a PUBLIC bucket if that's all an operator has).
    """
    load_env()
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_ANON_KEY")
           or os.environ.get("SUPABASE_KEY") or "")
    if not (base and key):
        return None
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", DEFAULT_BUCKET)
    return base, key, bucket


def public_url(base: str, bucket: str, key: str) -> str:
    return f"{base.rstrip('/')}/storage/v1/object/public/{bucket}/{key}"


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def upload(local_path: Path, key: str, content_type: str = "video/mp4",
           timeout: int = 300) -> tuple[str | None, str]:
    """PUT one file to the public Supabase bucket at `key`. -> (public_url, detail).

    Same endpoint/header/upsert contract as main._rehost_media, so re-running a
    generator overwrites the previous bank in place (x-upsert: true) rather than
    accumulating orphans — the manifests' URLs are stable across regenerations,
    which is the whole point of a pre-rendered bank the app hardcodes links to.
    """
    cfg = supabase_config()
    if not cfg:
        return None, "supabase not configured (SUPABASE_URL + a key required)"
    base, skey, bucket = cfg
    try:
        body = local_path.read_bytes()
    except OSError as e:
        return None, f"read failed: {e}"
    req = urllib.request.Request(
        f"{base}/storage/v1/object/{bucket}/{key}",
        data=body, method="PUT",
        headers={"Authorization": f"Bearer {skey}", "apikey": skey,
                 "Content-Type": content_type, "x-upsert": "true"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return public_url(base, bucket, key), f"HTTP {resp.status}"
            return None, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.read()[:200]!r}"
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


# A CDN error page or a truncated PUT can still answer 200; anything under this
# is not a playable 5-10s H.264 clip (the smallest real CTA preview measured
# during the CP-2 build was ~90 kB), so treat a tiny body as a dead asset.
MIN_ASSET_BYTES = 20_000


def head(url: str, timeout: int = 30) -> tuple[bool, str, int]:
    """HEAD a public URL -> (ok, detail, content_length).

    ok requires 200 AND a non-trivial content-length: a bucket that 404s as a
    JSON error body, or an upload that silently truncated, must fail the audit
    loudly rather than being reported as a live asset.
    """
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = int(resp.headers.get("Content-Length") or 0)
            if resp.status != 200:
                return False, f"HTTP {resp.status}", length
            if length < MIN_ASSET_BYTES:
                return False, f"HTTP 200 but only {length} bytes (< {MIN_ASSET_BYTES})", length
            return True, f"HTTP 200, {length} bytes", length
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", 0
    except (urllib.error.URLError, OSError) as e:
        return False, str(e), 0


# ---------------------------------------------------------------------------
# ffprobe
# ---------------------------------------------------------------------------

def ffprobe(target: str, timeout: int = 120) -> dict:
    """Probe a local path OR an http URL. -> {ok, duration_s, has_video, has_audio, ...}.

    Probing the REMOTE url (not just the local file we uploaded) is the point of
    the audit: it proves the bytes Supabase actually serves decode, which a
    local-file check cannot.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height",
             "-of", "json", target],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "reason": f"ffprobe failed to run: {e}"}
    if out.returncode != 0:
        return {"ok": False, "reason": f"ffprobe rc={out.returncode}: {(out.stderr or '')[-200:]}"}
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        return {"ok": False, "reason": "ffprobe emitted unparseable json"}
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "ok": True,
        "duration_s": round(duration, 3),
        "has_video": bool(video),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": video[0].get("width") if video else None,
        "height": video[0].get("height") if video else None,
        "codec": video[0].get("codec_name") if video else None,
    }


def check_video(target: str, min_s: float, max_s: float) -> tuple[bool, str]:
    """ffprobe + assert "this is a playable clip of roughly the right length"."""
    probe = ffprobe(target)
    if not probe.get("ok"):
        return False, probe.get("reason", "probe failed")
    if not probe["has_video"]:
        return False, "no video stream"
    dur = probe["duration_s"]
    if not (min_s <= dur <= max_s):
        return False, f"duration {dur}s outside [{min_s}, {max_s}]"
    return True, f"{dur}s {probe['width']}x{probe['height']} {probe['codec']}"


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "item"


def read_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def write_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
