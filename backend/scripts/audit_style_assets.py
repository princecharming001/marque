#!/usr/bin/env python3
"""CP-2 — the recurring checkpoint over every pre-rendered asset bank.

The banks are the one part of the style system that can rot WITHOUT anything in
the repo changing: a bucket policy flips, a key gets rotated, an object is
deleted, a re-upload truncates. None of that shows up in tests, in CI, or in a
render — it shows up as a blank card in the picker on a creator's phone. So the
banks need a check that goes out to the network and looks.

Audits, in one run:
  backend/assets/cta_previews.json    20 CTA template previews
  backend/assets/style_samples.json   12 style-archetype samples
  backend/assets/style_deck.json      the swipe deck's reels (audited only if
                                      the file is present — it is built
                                      separately by eval/study/deck_export.py
                                      and is not a CP-2 deliverable)

Per asset: HTTP HEAD (200 + a non-trivial content-length) and ffprobe of the
REMOTE url (decodes, has a video stream, plausible duration). Probing the
remote rather than the local render is the whole point — only that proves the
bytes Supabase actually serves are playable.

Also cross-checks the two CP-2 manifests against their own sources of truth:
the CTA bank against cta_styles.json (via its baked sha, so a template edit
that shipped without a re-render is caught) and the sample bank against
style_archetypes.json (so an archetype added without a sample is caught).

Exit code is nonzero if ANY asset fails, so this can be a gate step.

Usage:
    python3 backend/scripts/audit_style_assets.py
    python3 backend/scripts/audit_style_assets.py --bank cta
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import asset_store as store  # noqa: E402

CTA_MANIFEST = store.ASSETS_DIR / "cta_previews.json"
SAMPLES_MANIFEST = store.ASSETS_DIR / "style_samples.json"
DECK_MANIFEST = store.ASSETS_DIR / "style_deck.json"
ARCHETYPES = store.ASSETS_DIR / "style_archetypes.json"
CTA_CATALOG = store.RENDER_DIR / "src" / "components" / "cta" / "cta_styles.json"

# Per-bank duration bands. Wider than the generators' own targets on purpose:
# this audit is asking "is this a live, playable asset of about the right
# shape", not re-litigating the render settings.
CTA_BAND = (4.0, 6.0)        # composition is a fixed 150 frames @ 30fps
SAMPLE_BAND = (7.0, 14.0)    # ~10s samples
# Real scraped reels, so the band only has to say "a playable video of plausible
# post length". The ceiling is deliberately generous: IG/TikTok both allow 10
# minutes, and a real 180.0s deck reel tripped a tighter bound during the CP-2
# build — a false FAIL on a healthy asset is worse than a loose upper bound,
# because it trains the operator to ignore this gate.
DECK_BAND = (2.0, 660.0)


class Report:
    def __init__(self) -> None:
        self.checked = 0
        self.failures: list[str] = []

    def ok(self, bank: str, name: str, detail: str) -> None:
        self.checked += 1
        print(f"  ok    {bank:8} {name:26} {detail}")

    def fail(self, bank: str, name: str, detail: str) -> None:
        self.checked += 1
        self.failures.append(f"{bank}/{name}: {detail}")
        print(f"  FAIL  {bank:8} {name:26} {detail}")


def audit_asset(rep: Report, bank: str, name: str, url: str,
                band: tuple[float, float]) -> None:
    if not url:
        rep.fail(bank, name, "empty url in the manifest")
        return
    head_ok, head_detail, _ = store.head(url)
    if not head_ok:
        rep.fail(bank, name, f"HEAD {head_detail}")
        return
    probe_ok, probe_detail = store.check_video(url, *band)
    if not probe_ok:
        rep.fail(bank, name, f"ffprobe {probe_detail}")
        return
    rep.ok(bank, name, f"{head_detail} | {probe_detail}")


def audit_cta(rep: Report) -> None:
    manifest = store.read_manifest(CTA_MANIFEST)
    if not manifest:
        rep.fail("cta", "(manifest)", f"missing/unreadable {CTA_MANIFEST}")
        return
    print(f"[cta] {CTA_MANIFEST.name} — bank_version "
          f"{manifest.get('bank_version')}, generated {manifest.get('generated_at')}")

    # A template edit that never got re-rendered leaves the app playing previews
    # of the OLD design — invisible unless we compare hashes.
    if CTA_CATALOG.is_file():
        now = store.sha256_file(CTA_CATALOG)
        baked = manifest.get("cta_styles_sha", "")
        if now != baked:
            rep.fail("cta", "cta_styles_sha",
                     f"catalog changed since render (baked {baked[:12]}… vs now {now[:12]}…) "
                     "— re-run gen_cta_previews.py --upload")
        else:
            rep.ok("cta", "cta_styles_sha", f"{now[:12]}… matches")

        import json
        catalog_ids = {s["id"] for s in json.loads(CTA_CATALOG.read_text()).get("styles") or []}
        have = {e.get("id") for e in manifest.get("previews") or []}
        for missing in sorted(catalog_ids - have):
            rep.fail("cta", missing, "style in the catalog has no preview")
        for extra in sorted(have - catalog_ids):
            rep.fail("cta", extra, "preview for a style no longer in the catalog")

    for entry in manifest.get("previews") or []:
        audit_asset(rep, "cta", entry.get("id", "?"), entry.get("video_url", ""), CTA_BAND)


def audit_samples(rep: Report) -> None:
    manifest = store.read_manifest(SAMPLES_MANIFEST)
    if not manifest:
        rep.fail("samples", "(manifest)", f"missing/unreadable {SAMPLES_MANIFEST}")
        return
    print(f"[samples] {SAMPLES_MANIFEST.name} — bank_version "
          f"{manifest.get('bank_version')}, base take {manifest.get('base_take')!r}")

    have = {e.get("archetype_id") for e in manifest.get("samples") or []}
    arcs = store.read_manifest(ARCHETYPES)
    if arcs:
        want = {a.get("id") for a in arcs.get("archetypes") or []}
        for missing in sorted(want - have):
            rep.fail("samples", missing, "archetype has no rendered sample")
        for extra in sorted(have - want):
            rep.fail("samples", extra, "sample for an archetype that no longer exists")
    else:
        rep.fail("samples", "(archetypes)", f"missing {ARCHETYPES} — run build_style_samples.py")

    for entry in manifest.get("samples") or []:
        audit_asset(rep, "samples", entry.get("archetype_id", "?"),
                    entry.get("video_url", ""), SAMPLE_BAND)


def audit_deck(rep: Report) -> None:
    manifest = store.read_manifest(DECK_MANIFEST)
    if not manifest:
        # Not a CP-2 deliverable — absence is not a failure, but say so plainly
        # rather than silently auditing nothing.
        print(f"[deck] {DECK_MANIFEST.name} not present — skipped "
              "(built separately by eval/study/deck_export.py)")
        return
    print(f"[deck] {DECK_MANIFEST.name} — deck_version {manifest.get('deck_version')}")
    for reel in manifest.get("reels") or []:
        name = str(reel.get("id") or reel.get("reel_id") or "?")
        url = reel.get("video_url") or reel.get("url") or ""
        audit_asset(rep, "deck", name, url, DECK_BAND)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bank", default="all", choices=["all", "cta", "samples", "deck"])
    args = ap.parse_args(argv)

    rep = Report()
    if args.bank in ("all", "cta"):
        audit_cta(rep)
    if args.bank in ("all", "samples"):
        audit_samples(rep)
    if args.bank in ("all", "deck"):
        audit_deck(rep)

    print("---")
    if rep.failures:
        print(f"STYLE ASSET AUDIT: {len(rep.failures)} FAILURES across {rep.checked} checks")
        for f in rep.failures:
            print(f"  - {f}")
        return 1
    print(f"STYLE ASSET AUDIT: PASS ({rep.checked} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
