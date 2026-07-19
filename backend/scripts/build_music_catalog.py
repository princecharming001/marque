#!/usr/bin/env python3
"""Offline music-catalog builder for the Marque backend.

Pixabay has NO public API for music — the owner hand-downloads tracks from
pixabay.com/music (commercial-OK, no attribution required) into a local folder,
then runs this script once. For each audio file it:

  1. computes an offline BEAT GRID with librosa (bpm + per-beat timestamps +
     a 0-1 confidence proxy) so the editor can cut on-beat without any
     runtime audio analysis or network dependency;
  2. uploads the file to the public Supabase bucket at music/{id}.{ext}
     (same bucket + upsert pattern as main._rehost_media, so URLs are durable
     and range-served — AVPlayer and the Remotion render both need 206s);
  3. emits/updates backend/scripts/music_catalog.json in the EXACT track-dict
     shape the backend consumes (main._BUILTIN_MUSIC_TRACKS / /v1/music:
     name, url, vibe, tone, bpm, energy) plus the new beat_grid/beat_conf
     fields. Extra keys are safe: _load_music_catalog only validates `url`,
     and /v1/music projects the six known keys.

The catalog then goes live with ZERO code change: set the Render env var
MUSIC_CATALOG to the file's JSON content (the script prints the instruction).

Why beat_conf matters: beat_track always returns *a* grid, even on ambient
pads with no percussive onsets — snapping cuts to a hallucinated grid is worse
than not snapping at all. Consumers MUST treat a low-confidence grid
(beat_conf < ~0.5) as "no grid" and fall back to plain cuts. The proxy is the
mean onset-envelope strength at the chosen beat frames, normalized by the 95th
percentile of the whole envelope (clamped 0..1): a real beat sits on strong
onsets, a hallucinated one averages over noise-floor frames.

Per-file metadata comes from a sidecar catalog_meta.json in the audio folder
(id, title, vibe, tone, energy, license_note, source_url — keep the Pixabay
page URL for licence provenance), with sensible filename-derived defaults for
anything missing. Graceful by design: no Supabase envs -> forced dry-run;
librosa missing -> install hint; any single-file failure skips that file and
never aborts the batch (a 10-track run should survive one corrupt download).

Usage:
    python3 build_music_catalog.py ~/Downloads/pixabay-music --dry-run
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
        python3 build_music_catalog.py ~/Downloads/pixabay-music
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

AUDIO_EXTS = (".mp3", ".wav", ".m4a")

# Real container types, keyed by extension. We keep the source extension in the
# storage key (music/{id}.mp3 for the normal Pixabay case, which serves mp3)
# instead of blindly renaming everything to .mp3 — a wav served with an
# audio/mpeg label breaks AVPlayer range playback.
CONTENT_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}

# Filename-keyword -> (vibe, tone, energy) defaults, aligned to the exact
# vocabulary the backend's _select_music_track matches on:
#   vibe  : driving | steady | chill | upbeat
#   tone  : calm | confident | energetic
#   energy: low | medium | high
# Order matters — first hit wins, so the calmer/more specific words go first.
_KEYWORD_TAGS = [
    (("lofi", "lo-fi", "chill", "calm", "ambient", "soft", "relax", "sleep"),
     ("chill", "calm", "low")),
    (("upbeat", "happy", "fun", "pop", "dance", "party", "summer", "bright"),
     ("upbeat", "energetic", "high")),
    (("drive", "driving", "rock", "trap", "hype", "sport", "power", "action"),
     ("driving", "energetic", "high")),
    (("cinematic", "epic", "inspir", "uplift", "corporate", "motivat", "piano"),
     ("steady", "confident", "medium")),
]
_DEFAULT_TAGS = ("steady", "confident", "medium")


def _slug(text: str) -> str:
    """Filesystem/URL-safe id from a filename stem (lowercase, dash-separated)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "track"


def _guess_tags(stem: str) -> tuple[str, str, str]:
    low = stem.lower()
    for keywords, tags in _KEYWORD_TAGS:
        if any(k in low for k in keywords):
            return tags
    return _DEFAULT_TAGS


def _load_meta(meta_path: str) -> dict:
    """Load catalog_meta.json -> {basename: meta dict}. Accepts either a JSON
    object keyed by filename, or a list of dicts each carrying a "file" key
    (both shapes are natural to hand-write; be liberal in what we accept)."""
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (ValueError, OSError) as e:
        print(f"[meta] WARNING: could not parse {meta_path} ({e}) — "
              "falling back to filename-derived defaults for every track")
        return {}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        out = {}
        for entry in raw:
            if isinstance(entry, dict) and entry.get("file"):
                out[str(entry["file"])] = entry
        return out
    return {}


def _analyze(path: str, librosa, np, sr: int = 22050) -> tuple[float, list[float], float]:
    """Beat-track one file offline. Returns (bpm, beat_grid, beat_conf).

    beat_conf is a 0-1 proxy: mean onset-envelope strength at the chosen beat
    frames, normalized by the 95th percentile of the envelope, clamped 0..1.
    Low values (< ~0.5) mean the tracker guessed a grid on weak evidence —
    consumers must NOT snap cuts to such a grid (see module docstring)."""
    y, _ = librosa.load(path, sr=sr, mono=True)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_times = librosa.beat.beat_track(
        y=y, sr=sr, onset_envelope=onset_env, units="time")
    # librosa returns tempo as a scalar or a shape-(1,) array depending on
    # version/aggregation — normalize to a plain float either way.
    bpm = round(float(np.atleast_1d(tempo)[0]), 1)
    grid = [round(float(t), 3) for t in np.atleast_1d(beat_times)]
    conf = 0.0
    if len(grid) and onset_env.size:
        p95 = float(np.percentile(onset_env, 95))
        if p95 > 0:
            frames = librosa.time_to_frames(np.atleast_1d(beat_times), sr=sr)
            frames = np.clip(frames, 0, onset_env.size - 1)
            conf = float(np.mean(onset_env[frames])) / p95
    return bpm, grid, max(0.0, min(1.0, round(conf, 3)))


def _upload(base_url: str, service_key: str, bucket: str, key: str,
            path: str, content_type: str) -> str | None:
    """POST the file to Supabase Storage (same endpoint/headers/upsert pattern
    as main._rehost_media). Returns the durable public URL, or None on any
    failure — caller skips the track rather than shipping a dead URL."""
    base = base_url.rstrip("/")
    with open(path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        f"{base}/storage/v1/object/{bucket}/{key}",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {service_key}", "apikey": service_key,
                 "Content-Type": content_type, "x-upsert": "true"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if 200 <= resp.status < 300:
                return f"{base}/storage/v1/object/public/{bucket}/{key}"
            print(f"[upload] {key}: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"[upload] {key}: HTTP {e.code} — {e.read()[:200]!r}")
    except (urllib.error.URLError, OSError) as e:
        print(f"[upload] {key}: {e}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the Marque music catalog (beat grids + Supabase "
                    "upload) from a folder of hand-downloaded Pixabay tracks. "
                    "See backend/scripts/README_MUSIC.md.")
    ap.add_argument("audio_dir", help="folder of .mp3/.wav/.m4a files")
    ap.add_argument("--meta", default=None,
                    help="path to catalog_meta.json (default: <audio_dir>/catalog_meta.json)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "music_catalog.json"),
                    help="catalog JSON to emit/update (default: backend/scripts/music_catalog.json)")
    ap.add_argument("--bucket", default=os.environ.get("SUPABASE_STORAGE_BUCKET", "marque-clips"),
                    help="Supabase Storage bucket (default: marque-clips)")
    ap.add_argument("--dry-run", action="store_true",
                    help="analyze + write catalog but skip the Supabase upload")
    args = ap.parse_args()

    # librosa/numpy are heavyweight optional deps — import late so `--help`
    # works everywhere, and fail with the exact install command.
    try:
        import numpy as np
        import librosa
    except ImportError as e:
        print(f"ERROR: {e}\nBeat analysis needs librosa. Install it with:\n"
              "    pip install librosa soundfile")
        return 1

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    dry_run = args.dry_run
    if not dry_run and not (supabase_url and service_key):
        print("WARNING: SUPABASE_URL / SUPABASE_SERVICE_KEY not set — forcing "
              "--dry-run (catalog URLs will be placeholders; do NOT ship them).")
        dry_run = True

    audio_dir = os.path.abspath(args.audio_dir)
    if not os.path.isdir(audio_dir):
        print(f"ERROR: not a directory: {audio_dir}")
        return 1
    files = sorted(f for f in os.listdir(audio_dir)
                   if f.lower().endswith(AUDIO_EXTS) and not f.startswith("."))
    if not files:
        print(f"ERROR: no {'/'.join(AUDIO_EXTS)} files in {audio_dir}")
        return 1

    meta_path = args.meta or os.path.join(audio_dir, "catalog_meta.json")
    meta_by_file = _load_meta(meta_path)

    # Merge-update: keep previously cataloged tracks so the owner can add a few
    # files at a time without re-analyzing/re-uploading the whole library.
    existing: dict[str, dict] = {}
    if os.path.isfile(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                for t in json.load(f):
                    if isinstance(t, dict) and t.get("id"):
                        existing[t["id"]] = t
        except (ValueError, OSError) as e:
            print(f"[catalog] WARNING: could not parse existing {args.out} ({e}) — starting fresh")

    url_base = (supabase_url.rstrip("/") if supabase_url
                else "https://YOUR-PROJECT.supabase.co")
    built, skipped = 0, 0
    for fname in files:
        path = os.path.join(audio_dir, fname)
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        meta = meta_by_file.get(fname) or meta_by_file.get(stem) or {}
        track_id = _slug(str(meta.get("id") or stem))
        title = str(meta.get("title") or stem.replace("_", " ").replace("-", " ").strip().title())
        vibe, tone, energy = _guess_tags(stem)
        vibe = str(meta.get("vibe") or vibe)
        tone = str(meta.get("tone") or tone)
        energy = str(meta.get("energy") or energy)

        print(f"[{fname}] analyzing…")
        try:
            bpm, grid, conf = _analyze(path, librosa, np)
        except Exception as e:  # decode failures, exotic codecs, corrupt files
            # some decode errors str() to "" — always show at least the type
            print(f"[{fname}] SKIP — analysis failed: {type(e).__name__}: {e}\n"
                  "    (m4a/AAC may need ffmpeg on PATH, or convert to mp3/wav first)")
            skipped += 1
            continue
        # Half/double-tempo confusion is the classic beat-tracker failure; if
        # the owner supplied Pixabay's listed BPM and we disagree wildly, say
        # so — the measured grid still wins (it is what the cuts snap to).
        meta_bpm = meta.get("bpm")
        if isinstance(meta_bpm, (int, float)) and meta_bpm > 0 and \
                abs(bpm - float(meta_bpm)) / float(meta_bpm) > 0.1:
            print(f"[{fname}] note: measured {bpm} bpm vs listed {meta_bpm} "
                  "(half/double-tempo ambiguity is common; measured grid is used)")
        if conf < 0.5:
            print(f"[{fname}] note: beat_conf={conf} is LOW — editors must not "
                  "snap to this grid")

        key = f"music/{track_id}{ext}"
        if dry_run:
            url = f"{url_base}/storage/v1/object/public/{args.bucket}/{key}"
            print(f"[{fname}] dry-run: would upload -> {key}")
        else:
            url = _upload(supabase_url, service_key, args.bucket, key, path,
                          CONTENT_TYPES.get(ext, "application/octet-stream"))
            if not url:
                print(f"[{fname}] SKIP — upload failed (track left out of catalog)")
                skipped += 1
                continue
            print(f"[{fname}] uploaded -> {url}")

        # "name" is the field the backend actually reads (/v1/music,
        # _BUILTIN_MUSIC_TRACKS); "title"/"id" and the provenance/beat fields
        # ride along untouched (_load_music_catalog only validates `url`).
        existing[track_id] = {
            "id": track_id, "name": title, "title": title, "url": url,
            "vibe": vibe, "tone": tone, "bpm": bpm, "energy": energy,
            "beat_grid": grid, "beat_conf": conf,
            "license_note": str(meta.get("license_note")
                                or "Pixabay Content License — commercial OK, no attribution"),
            "source_url": str(meta.get("source_url") or ""),
        }
        print(f"[{fname}] ok: id={track_id} bpm={bpm} beats={len(grid)} "
              f"conf={conf} vibe={vibe} tone={tone} energy={energy}")
        built += 1

    catalog = [existing[k] for k in sorted(existing)]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
        f.write("\n")

    print(f"\nDone: {built} track(s) cataloged, {skipped} skipped -> {args.out}"
          + (" (DRY RUN — nothing uploaded)" if dry_run else ""))
    print("\nNEXT STEP: on Render, set the env var MUSIC_CATALOG to the compact "
          "JSON below (one line), then restart the service:")
    print(json.dumps(catalog, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
