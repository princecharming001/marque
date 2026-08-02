#!/usr/bin/env python3
"""CP-2 — pre-render the CTA preview bank the iOS CTA picker plays.

The picker can't render 20 Remotion templates on-device, and rendering one on
demand per tap would put a cold Lambda in front of a UI gesture. So the bank is
baked ONCE here, uploaded to the public Supabase bucket, and the app just plays
20 short mp4s off a committed manifest.

What it renders: every style in render/src/components/cta/cta_styles.json,
through the `Marque-CtaPreview` composition (1080x1920 / 30fps / 150 frames),
at --scale=0.5 --crf=30 -> 540x960, 5.0s, ~100-400 kB each. 540x960 is 2x the
widest slot the picker shows a preview in, so it still looks sharp on a 3x
screen while keeping the whole 20-clip bank around 4 MB; crf=30 is the same
"good enough for a thumbnail-sized loop" trade format_eval already makes for
local renders.

Every preview carries the SAME sample copy ("Follow for more" / "@yourname",
no logo) on purpose: the picker's job is to let a creator compare TEMPLATES,
and varying the words between cards would make the differences read as copy
differences rather than style differences.

cta_styles_sha in the manifest is sha256 of the catalog file. A template tweak
that ships without a re-render is otherwise invisible — the app would keep
playing previews of the OLD design. --audit fails on a mismatch, so the drift
is caught by the recurring checkpoint instead of by a confused creator.

Usage:
    python3 backend/scripts/gen_cta_previews.py                       # render locally only
    python3 backend/scripts/gen_cta_previews.py --only pill,glitch
    python3 backend/scripts/gen_cta_previews.py --upload              # render + upload + manifest
    python3 backend/scripts/gen_cta_previews.py --audit               # verify the shipped bank
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asset_store as store  # noqa: E402

CATALOG = store.RENDER_DIR / "src" / "components" / "cta" / "cta_styles.json"
MANIFEST = store.ASSETS_DIR / "cta_previews.json"
DEFAULT_OUT = store.RENDER_DIR / "out" / "cta_previews"
STORAGE_PREFIX = "demo-assets/cta-styles"

BANK_VERSION = 1
COMPOSITION = "Marque-CtaPreview"

# The composition is a fixed 150 frames @ 30fps = 5.0s. The audit band is
# deliberately wide (4-6s) rather than exact: container duration rounds against
# the encoder's own frame timing, and a preview that came back 4.9s is fine —
# what the check is really guarding against is a 0s stub or a truncated upload.
EXPECTED_DURATION_S = 5.0
MIN_DURATION_S, MAX_DURATION_S = 4.0, 6.0

# Same copy for every card — see module docstring.
SAMPLE_PROPS = {"text": "Follow for more", "handle": "@yourname", "logoUrl": None}

RENDER_TIMEOUT_S = 300


def load_styles(only: set[str] | None = None) -> list[dict]:
    catalog = json.loads(CATALOG.read_text())
    styles = catalog.get("styles") or []
    if only:
        styles = [s for s in styles if s.get("id") in only]
    return styles


def render_one(style_id: str, out_dir: Path) -> tuple[bool, str]:
    """One `npx remotion render`, run from render/ (where node_modules live)."""
    out_path = out_dir / f"{style_id}.mp4"
    props = json.dumps({"styleId": style_id, **SAMPLE_PROPS})
    try:
        result = subprocess.run(
            ["npx", "remotion", "render", "src/index.ts", COMPOSITION, str(out_path),
             f"--props={props}", "--scale=0.5", "--crf=30"],
            cwd=str(store.RENDER_DIR), capture_output=True, text=True,
            timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"render timed out after {RENDER_TIMEOUT_S}s"
    except OSError as e:
        return False, f"could not run npx: {e}"
    if result.returncode != 0:
        return False, f"remotion rc={result.returncode}: {(result.stderr or result.stdout)[-400:]}"
    if not out_path.is_file() or out_path.stat().st_size < store.MIN_ASSET_BYTES:
        return False, "render produced no/undersized file"
    return True, str(out_path)


def cmd_generate(only: set[str] | None, out_dir: Path, do_upload: bool) -> int:
    styles = load_styles(only)
    if not styles:
        print(f"[cta] no styles matched (only={sorted(only) if only else None})")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    failures: list[tuple[str, str]] = []
    started = time.time()

    for i, style in enumerate(styles, 1):
        sid = style["id"]
        t0 = time.time()
        ok, detail = render_one(sid, out_dir)
        if not ok:
            # A single bad template must not cost the other 19 renders — collect
            # and keep going, then exit nonzero at the end.
            print(f"  FAIL  [{i:2}/{len(styles)}] {sid:18} render: {detail}")
            failures.append((sid, detail))
            continue
        local = Path(detail)
        probe_ok, probe_detail = store.check_video(str(local), MIN_DURATION_S, MAX_DURATION_S)
        if not probe_ok:
            print(f"  FAIL  [{i:2}/{len(styles)}] {sid:18} probe: {probe_detail}")
            failures.append((sid, f"probe: {probe_detail}"))
            continue

        entry = {"id": sid, "video_url": "", "bytes": local.stat().st_size,
                 "duration_s": store.ffprobe(str(local)).get("duration_s", EXPECTED_DURATION_S)}
        if do_upload:
            url, up_detail = store.upload(local, f"{STORAGE_PREFIX}/{sid}.mp4")
            if not url:
                print(f"  FAIL  [{i:2}/{len(styles)}] {sid:18} upload: {up_detail}")
                failures.append((sid, f"upload: {up_detail}"))
                continue
            entry["video_url"] = url
        entries.append(entry)
        print(f"  ok    [{i:2}/{len(styles)}] {sid:18} {probe_detail}  "
              f"{entry['bytes'] / 1024:.0f} kB  {time.time() - t0:.1f}s")

    print("---")
    print(f"rendered {len(entries)}/{len(styles)} previews in {time.time() - started:.0f}s")

    if do_upload:
        if failures:
            # Writing a manifest that is missing styles would make the picker
            # silently short a card; refuse to publish a partial bank.
            print("[cta] NOT writing manifest — the bank is incomplete")
        elif only:
            # A partial re-render must patch, not truncate, the shipped bank.
            _merge_manifest(entries)
        else:
            _write_manifest(entries)

    if failures:
        print(f"CTA PREVIEWS: {len(failures)} FAILURES")
        for sid, why in failures:
            print(f"  - {sid}: {why}")
        return 1
    print("CTA PREVIEWS: PASS")
    return 0


def _write_manifest(entries: list[dict]) -> None:
    store.write_manifest(MANIFEST, {
        "bank_version": BANK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cta_styles_sha": store.sha256_file(CATALOG),
        "previews": entries,
    })
    print(f"[cta] wrote {MANIFEST} ({len(entries)} previews)")


def _merge_manifest(entries: list[dict]) -> None:
    existing = store.read_manifest(MANIFEST) or {}
    by_id = {e["id"]: e for e in existing.get("previews") or []}
    by_id.update({e["id"]: e for e in entries})
    # Keep catalog order so the manifest reads the way the picker lays out.
    order = [s["id"] for s in load_styles()]
    merged = [by_id[i] for i in order if i in by_id]
    _write_manifest(merged)


def cmd_audit() -> int:
    """Verify the SHIPPED bank: every manifest URL is live, decodes, and is the
    right length — plus the catalog hasn't changed underneath it."""
    manifest = store.read_manifest(MANIFEST)
    if not manifest:
        print(f"[cta-audit] no manifest at {MANIFEST} — run with --upload first")
        return 1

    failures: list[str] = []

    sha_now = store.sha256_file(CATALOG)
    sha_baked = manifest.get("cta_styles_sha", "")
    if sha_now != sha_baked:
        failures.append(
            f"cta_styles.json changed since the bank was rendered "
            f"(manifest {sha_baked[:12]}… vs now {sha_now[:12]}…) — re-run with --upload")
        print(f"  FAIL  cta_styles_sha  stale (manifest {sha_baked[:12]}… != now {sha_now[:12]}…)")
    else:
        print(f"  ok    cta_styles_sha  {sha_now[:12]}…")

    catalog_ids = {s["id"] for s in load_styles()}
    manifest_ids = {e.get("id") for e in manifest.get("previews") or []}
    for missing in sorted(catalog_ids - manifest_ids):
        failures.append(f"{missing}: in the catalog but has no preview")
        print(f"  FAIL  {missing:18} no preview in the manifest")
    for extra in sorted(manifest_ids - catalog_ids):
        failures.append(f"{extra}: preview for a style no longer in the catalog")
        print(f"  FAIL  {extra:18} style no longer exists in the catalog")

    for entry in manifest.get("previews") or []:
        sid, url = entry.get("id", "?"), entry.get("video_url", "")
        if not url:
            failures.append(f"{sid}: empty video_url")
            print(f"  FAIL  {sid:18} empty video_url")
            continue
        head_ok, head_detail, _ = store.head(url)
        if not head_ok:
            failures.append(f"{sid}: HEAD {head_detail}")
            print(f"  FAIL  {sid:18} HEAD {head_detail}")
            continue
        probe_ok, probe_detail = store.check_video(url, MIN_DURATION_S, MAX_DURATION_S)
        if not probe_ok:
            failures.append(f"{sid}: ffprobe {probe_detail}")
            print(f"  FAIL  {sid:18} ffprobe {probe_detail}")
            continue
        print(f"  ok    {sid:18} {head_detail} | {probe_detail}")

    print("---")
    n = len(manifest.get("previews") or [])
    if failures:
        print(f"CTA PREVIEW AUDIT: {len(failures)} FAILURES across {n} previews")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"CTA PREVIEW AUDIT: PASS ({n} previews live)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="comma-separated style ids")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="local render output dir")
    ap.add_argument("--upload", action="store_true",
                    help="upload each preview + write backend/assets/cta_previews.json")
    ap.add_argument("--audit", action="store_true",
                    help="verify the shipped bank (HEAD + ffprobe + catalog sha); renders nothing")
    args = ap.parse_args(argv)

    if args.audit:
        return cmd_audit()
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    return cmd_generate(only, Path(args.out), args.upload)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
