"""Build the CTA SWIPER deck v2: 5 curated, finished-feeling CTA cards.

Owner directive (build 63): the onboarding CTA swiper shows FIVE options for now — each
one unique (its own copy, not five flavors of "Follow for more"), each with its own
music, and each built on a generated cinematic base video (Higgsfield / kling3_0_turbo,
prompted for no-text/no-faces backgrounds) instead of the v1 silhouette stage. The REAL
template still draws the words — what the creator swipes right on maps 1:1 to a
`cta_style_id` + seeded `outro_text` the pipeline renders on their actual reel.

Per entry: render Marque-CtaPreviewV2 (base video + template + music bed, 5s) locally,
upload to demo-assets/cta-styles/v2/{style_id}.mp4, and write the committed manifest
backend/assets/cta_deck_v2.json that /v1/cta-styles serves as the swiper deck.

The 20-template library (Manage sheet / profile) keeps its v1 previews untouched.

CLI:  python3 scripts/gen_cta_deck_v2.py [--only id,...] [--no-upload] [--audit]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asset_store as store  # noqa: E402

MANIFEST = store.ASSETS_DIR / "cta_deck_v2.json"
DEFAULT_OUT = store.RENDER_DIR / "out" / "cta_deck_v2"
STORAGE_PREFIX = "demo-assets/cta-styles/v2"
COMPOSITION = "Marque-CtaPreviewV2"
DECK_VERSION = 1
RENDER_TIMEOUT_S = 600           # OffthreadVideo pulls the base over the network
MIN_DURATION_S, MAX_DURATION_S = 4.0, 6.0

_SH = "https://www.soundhelix.com/examples/mp3"

# The five cards. `base` is the generated Higgsfield clip (CloudFront, durable on their
# CDN but ALSO baked into our uploaded preview, so the swiper never depends on it);
# `music` is a distinct bed from the app's own catalog tags (main.py MUSIC_TRACKS).
DECK = [
    {
        "style_id": "part_two", "label": "Part two tease",
        "text": "Follow for part 2", "handle": "",
        "blurb": "Neon-lit tease that promises the next video.",
        "base": "https://d8j0ntlcm91z4.cloudfront.net/user_39251CB4hzXYTJZ4Y86ynBBlAmC/hf_20260802_074406_d7a1c25c-cd8d-4bb0-ad04-2f924f36e7f3.mp4",
        "music": f"{_SH}/SoundHelix-Song-1.mp3",   # Momentum — driving 126
    },
    {
        "style_id": "pill", "label": "Save prompt",
        "text": "Save this for later", "handle": "",
        "blurb": "Calm, minimal — asks for the save, not the follow.",
        "base": "https://d8j0ntlcm91z4.cloudfront.net/user_39251CB4hzXYTJZ4Y86ynBBlAmC/hf_20260802_074414_3ad3032c-e551-43b4-8ded-fe6a49467e17.mp4",
        "music": f"{_SH}/SoundHelix-Song-3.mp3",   # Still Air — calm 90
    },
    {
        "style_id": "bar_sweep", "label": "Comment trigger",
        "text": "Comment GUIDE and it's yours", "handle": "",
        "blurb": "Broadcast lower-third that trades a comment for a resource.",
        "base": "https://d8j0ntlcm91z4.cloudfront.net/user_39251CB4hzXYTJZ4Y86ynBBlAmC/hf_20260802_074422_723724ff-b8f5-49fc-95ed-2786a76772ac.mp4",
        "music": f"{_SH}/SoundHelix-Song-8.mp3",   # Assured — confident 116
    },
    {
        "style_id": "serif_line", "label": "Share nudge",
        "text": "Send this to a founder", "handle": "",
        "blurb": "Editorial one-liner that asks for the share.",
        "base": "https://d8j0ntlcm91z4.cloudfront.net/user_39251CB4hzXYTJZ4Y86ynBBlAmC/hf_20260802_074428_8b8a2492-6680-4aaa-b0bd-e54ac02bde70.mp4",
        "music": f"{_SH}/SoundHelix-Song-6.mp3",   # Reflect — calm 84
    },
    {
        "style_id": "corner_tag", "label": "Schedule stamp",
        "text": "New drops every Tuesday", "handle": "",
        "blurb": "Persistent corner tag that trains the audience's calendar.",
        "base": "https://d8j0ntlcm91z4.cloudfront.net/user_39251CB4hzXYTJZ4Y86ynBBlAmC/hf_20260802_074437_e24b0ed6-7bec-4551-b511-b91515d6aeea.mp4",
        "music": f"{_SH}/SoundHelix-Song-5.mp3",   # Uplift — upbeat 128
    },
]


def render_one(entry: dict, out_dir: Path) -> tuple[bool, str]:
    out_path = out_dir / f"{entry['style_id']}.mp4"
    props = json.dumps({
        "styleId": entry["style_id"], "text": entry["text"], "handle": entry["handle"],
        "logoUrl": None, "videoSrc": entry["base"], "audioSrc": entry["music"],
    })
    try:
        result = subprocess.run(
            ["npx", "remotion", "render", "src/index.ts", COMPOSITION, str(out_path),
             f"--props={props}", "--scale=0.5", "--crf=28"],
            cwd=str(store.RENDER_DIR), capture_output=True, text=True,
            timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"render timed out after {RENDER_TIMEOUT_S}s"
    if result.returncode != 0:
        return False, f"remotion rc={result.returncode}: {(result.stderr or result.stdout)[-400:]}"
    if not out_path.is_file() or out_path.stat().st_size < store.MIN_ASSET_BYTES:
        return False, "render produced no/undersized file"
    return True, str(out_path)


def cmd_generate(only: set[str] | None, do_upload: bool) -> int:
    entries, failures = [], []
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    deck = [e for e in DECK if not only or e["style_id"] in only]
    for i, e in enumerate(deck, 1):
        if e["base"].startswith("BASE_URL"):
            failures.append((e["style_id"], "base video URL not filled in yet"))
            print(f"  SKIP  [{i}/{len(deck)}] {e['style_id']:12} base URL placeholder")
            continue
        t0 = time.time()
        ok, detail = render_one(e, DEFAULT_OUT)
        if not ok:
            failures.append((e["style_id"], detail))
            print(f"  FAIL  [{i}/{len(deck)}] {e['style_id']:12} {detail}")
            continue
        local = Path(detail)
        probe_ok, probe_detail = store.check_video(str(local), MIN_DURATION_S, MAX_DURATION_S)
        if not probe_ok:
            failures.append((e["style_id"], f"probe: {probe_detail}"))
            print(f"  FAIL  [{i}/{len(deck)}] {e['style_id']:12} probe: {probe_detail}")
            continue
        # The swipe card must have SOUND — a silent card is the exact bug this bank fixes.
        meta = store.ffprobe(str(local))
        if not meta.get("has_audio", True):
            failures.append((e["style_id"], "rendered preview has NO audio stream"))
            print(f"  FAIL  [{i}/{len(deck)}] {e['style_id']:12} no audio stream")
            continue
        row = {"style_id": e["style_id"], "label": e["label"], "text": e["text"],
               "handle": e["handle"], "blurb": e["blurb"], "video_url": "",
               "bytes": local.stat().st_size,
               "duration_s": meta.get("duration_s", 5.0)}
        if do_upload:
            url, up_detail = store.upload(local, f"{STORAGE_PREFIX}/{e['style_id']}.mp4")
            if not url:
                failures.append((e["style_id"], f"upload: {up_detail}"))
                print(f"  FAIL  [{i}/{len(deck)}] {e['style_id']:12} upload: {up_detail}")
                continue
            row["video_url"] = url
        entries.append(row)
        print(f"  ok    [{i}/{len(deck)}] {e['style_id']:12} {row['bytes']/1024:.0f} kB "
              f"{time.time()-t0:.0f}s")
    if failures:
        print(f"--- {len(failures)} failure(s); manifest NOT written")
        return 1
    if not only and do_upload:
        MANIFEST.write_text(json.dumps(
            {"deck_version": DECK_VERSION, "entries": entries}, indent=2))
        print(f"manifest -> {MANIFEST} ({len(entries)} cards)")
    return 0


def cmd_audit() -> int:
    if not MANIFEST.is_file():
        print("no manifest — run generate first")
        return 1
    m = json.loads(MANIFEST.read_text())
    bad = 0
    for e in m.get("entries", []):
        head_ok, head_detail, _ = store.head(e["video_url"])
        probe_ok, probe_detail = (store.check_video(e["video_url"], MIN_DURATION_S,
                                                    MAX_DURATION_S)
                                  if head_ok else (False, "skipped"))
        has_audio = store.ffprobe(e["video_url"]).get("has_audio") if probe_ok else False
        ok = head_ok and probe_ok and has_audio
        if not ok:
            bad += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {e['style_id']:12} {head_detail} | "
              f"{probe_detail} | audio={'yes' if has_audio else 'NO'}")
    print(f"--- CTA DECK V2 AUDIT: {'PASS' if bad == 0 else f'{bad} FAILURES'}")
    return 0 if bad == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args(argv)
    if a.audit:
        return cmd_audit()
    only = {s.strip() for s in a.only.split(",") if s.strip()} or None
    return cmd_generate(only, not a.no_upload)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
