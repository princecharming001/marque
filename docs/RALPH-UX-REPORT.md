# RALPH UX REPORT — 7-fix UX overhaul

Autonomous run executing `docs/PLAN-UX-FIXES.md` on branch `claude/ux-fixes-seven`
(PR #2, off main f6e1bf9 — post-AI-editor-overhaul). Every unit implemented → verified →
committed → pushed. Slice reviews: S1 93 · S2 92 · S3 91 · S4 91 · S5 92 · S6 92 · S7 90.

**Baseline → final:** backend suite 569 → **610 passed keyless** (+41 tests, zero
weakened/deleted); `edl_eval` PASS (5 good, 8 bad) — identical to baseline (E3 hard gate);
`run_eval` PASS; render `build:bridge` rc=0; iOS **BUILD SUCCEEDED** (compiled on the Mac
after every iOS slice).

## What shipped, per fix

### 1. Library plays the RENDER (UX-C1/C2)
`Clip.renderLocalPath` + `isServerRendered`/`playbackLocalPath`/`playbackRemoteURL` gating —
the detail player, share sheet, and both thumbnails can no longer reach the raw take for a
server-rendered clip. `AppStore.cacheRender` downloads the render on `.ready` (single-flight,
200MB cap, poster regenerated FROM the render, URL-change invalidation + mid-download race
guard), hooked at pollJob/pollClipStatuses/applyTweakResult. Drafts/imported stay local-first.

### 2. Instant home feed (UX-F1/F2)
FeedStore hoisted to MarqueApp (survives RootTabView's per-tab teardown) + a disk snapshot
(`Documents/marque.feed.v1.json`, debounced writes) → instant paint on tab-switch AND
relaunch, silent revalidate (no skeletons when cache exists), scenePhase >15min staleness
refresh.

### 3. One-tap submit (UX-B1a/B1b)
`auto_confirm` runs the whole pipeline with no brief_ready stop — `_apply_confirm_to_job`
extracted verbatim (confirm endpoint byte-identical), `_analyze_to_brief` factored (shared
dossier+loudness gather; briefless-on-brief-failure, fail-as-today on transcription),
create response includes stable clip ids. iOS: `.recorded` is the single context screen
(format grid + vibe + capability-gated toggles + instructions moved in), submit →
`trackSubmittedClips` → straight to Library; old backends fall back to the untouched brief
flow.

### 4. APNs push (UX-B2a/B2b)
`app/push.py` (ES256 provider JWT cached 40min, HTTP/2, sandbox/prod routing, clips_ready
category + `marque://library/clip/{id}` deeplink, 410 soft-disable, keyless no-op),
`device_tokens` table + `POST /v1/devices`, once-per-job send hook at the ready landing
(tweaks never push). iOS: aps-environment entitlement, PushManager registration +
tap-routing → Library clip detail, PushPrimerSheet (explain-then-ask, 72h cooldown, max 3)
replacing the cold permission prompt, local notifications as the deduped fallback.

### 5. Honest inspiration reels (UX-A1/A2/A3)
`_classify_edit_format` heuristic + dossier tier-2 (top-K by watching, carried forward,
paid once per reel) → reels carry `edit_format`/`fmt_source`/`why_match`. `/v1/reels/examples`
rewritten: format-true matching (dossier > heuristic > legacy style), engagement +
transcribed + rehosted ranking, playable cards lead, live NEVER pads with fabricated cards,
exemplars honestly `sample:true` + `selection_reason` on every card. iOS mimic cards
autoplay muted/looping (visible-only), poster fallback, whyMatch line, SAMPLE chip
(FailableVideoPlayer extracted to DesignSystem).

### 6. Chat on clip preview (UX-D1/D2)
Input-shaped tweak affordance under the detail player → TweakChatSheet (medium/large
detents, composer focused). Preview-first: `?preview=1` stages a candidate + cheap proof
render, backend echoes the full typed ops, client polls preview_status → `Clip.previewURL`
(transient) with a PREVIEW badge, Apply commits deterministically via direct-ops, Discard
is a local no-op (server staged nothing — EDL-byte-identical proven by test). Keyless/undo
fall back to the unchanged direct flow.

### 7. Reasoned feed (UX-G1/G2)
`_top_arms` factored from the Thompson recommendations (cold → honest niche priors);
`_feed_sreq` consumes arms for pillar/style with template fallback; `why_picked` stamped on
every script across fast/stale-while-revalidate/full-refresh/prefetch paths; next-idea
routes through the same arms (pillar steer + honest deterministic grounding). iOS shows the
why line on pick cards + ScriptReaderView.

### KB v2 (UX-E1/E2/E3)
transitions.md / sound_design.md / hook_visual.md / format_playbooks.md (researched +
seed-corpus distilled, numbers only) + 4 files deepened; MANIFEST → kb-2026.08; digest()
v2 routing incl. the style-matched playbook section; budgets 998/983/839 tok asserted.
**E3 hard gate: eval identical to baseline.**

## Evidence index
- Progress + per-unit evidence: `docs/RALPH-UX-PROGRESS.md`
- New test files: test_auto_confirm (11) · test_push (6) · test_reel_examples (12) ·
  test_reasoned_feed (6) · test_ux_gauntlet (1 end-to-end walkthrough) · +5 KB tests
- F.2 walkthrough: devices upsert → one-tap keyless submit (format toggles honored, EDL
  built) → per-format SAMPLE-flagged examples → why_picked on every feed pick → preview
  tweak (ops echoed, EDL untouched, apply commits once) → token enabled for live push.

## Deferred manual steps (need the user / real device)
1. **Real-device APNs E2E** — set `APNS_KEY_ID`/`APNS_TEAM_ID`/`APNS_P8` (PEM contents)/
   `APNS_TOPIC=com.getmarque.app` on Render, install on a device, background the app
   during an edit, confirm the push + deeplink. (Everything is fail-soft keyless.)
2. **`xcodegen` regeneration note** — project.yml carries aps-environment + version 14;
   the checked-in entitlements/Info.plist already match, so no regen is required, but the
   next `xcodegen` run is now consistent.
3. **TestFlight primer flow** — first clips-ready moment should show the branded primer
   (not the cold system prompt); verify the 72h/3-shows cooldown.
4. **On-device playback checks** — Library plays the render (not the raw take) for
   server-rendered clips; mimic cards autoplay muted; preview badge appears on staged
   tweaks.

## Known gaps
- Dossier tier-2 reel classification + live b-roll/vision paths need vendor keys (seam-
  tested, fail-soft, carried forward once paid).
- `EDL_AUTHOR=plan`-mode interaction with auto_confirm inherits the overhaul's plan-path
  status (default legacy in code; prod has plan enabled — the auto pipeline calls the same
  `_run_edit` either way).
- Feed disk snapshot caps nothing by size (scripts+reels JSON is small; revisit if reels
  grow embedded payloads).
