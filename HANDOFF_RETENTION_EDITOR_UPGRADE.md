# Marque retention-editor upgrade — session handoff (2026-07-13)

## Goal (verbatim from the user)
*"the editing logic technically works but it does the bare minimum... this is going to be
a production-ready editor that will be the #1 AI editor out there... study how to go above
and beyond... other than that there are a lot of formatting issues across the platform that
need thorough testing and improvement (might be wise to plan ralph loops for this)."*

A full plan was written (`~/.claude/plans/ok-the-editing-logic-lucky-peacock.md`), then
executed **in its entirety, autonomously, with no approval checkpoints** per explicit
follow-up instruction. This document is the result.

## Where the work lives
- **Repo:** `/Users/home/Marque` — branch `retention-editor-upgrade`, based on `main`.
- **All 15 commits are LOCAL ONLY.** Nothing pushed, nothing deployed (standing rule:
  Marque/Yunicorn is LIVE; deploy only on explicit "push it now"). `git log --oneline
  main..HEAD` lists everything; the last 6 are this initiative's own phases, the first 9
  are prior work already on this branch (r7 hardening + earlier WS-A/P1/P2/P3/LOOP-E/WS-B
  phases from before this conversation's summary point).
- **Everything new is flag-gated OFF by default** — `RETENTION_PASSES` (env, default `""`)
  and `EDL_AUTHOR` (env, default `"legacy"`) both need explicit values for any of this to
  affect a real edit. Today's production behavior is completely unchanged until someone
  sets these.

## What shipped, phase by phase

### WS-A — Foundations
Single-source layout constants (`render/src/layout.json`/`.ts` ↔ `backend/app/
layout_constants.py` ↔ iOS `LayoutConstants.swift`, cross-checked by
`backend/test_layout_parity.py`), a `node --test` runner for pure-TS layout math (zero new
JS deps), and `scripts/gate.sh` (`--fast`/`--full`/`--paid` tiers) as the one entry point
every later phase gates on.

### WS-B — Formatting fixes (rendered video)
Caption overflow (shrink-to-fit + safe-area clamping unified across all 3 caption styles),
sticker/caption-band collision avoidance, `system-ui` font non-determinism removed,
SplitThree's hardcoded English labels removed, Faceless's color-grade no-op fixed, karaoke
active-word animation fixed (CSS `transition` is a no-op in Remotion's frame-by-frame
render), plus the iOS editor's composition-framing preview (green_screen/duet_split/
split_three now show layout-accurate chrome instead of a generic player) and transition-dip
preview.

### P1 — Filler v2
`TRIM_LEVELS` (conservative/default/aggressive, makes the previously-dead
`trim_aggressiveness` field real), multi-word discourse-phrase detection ("you know", "kind
of", ...), stutter/repeat/false-start detection, a residual-filler sweep that force-drops
anything the author missed regardless of which path authored the EDL, legacy loop-tail
trimming.

### P2 — Pacing engine (`backend/app/retention.py: plan_pacing`)
Global speed "lift" (none/subtle/medium → 1.00/1.03/1.06x) plus per-segment speed-up on the
single most-qualifying low-information stretch, expressed entirely via the EDL's existing
`Segment.speed` field (no schema change) — confirmed via direct inspection of the installed
Remotion renderer that speed changes are pitch-preserved server-side (FFmpeg `atempo`), not
naive resampling, so there's no chipmunk-voice risk. Spoken speech capped at 1.35x total;
`duet_split` hard-excluded (its react-window length-preservation guard forbids any speed
change on that style).

### P3 — Pattern-interrupt scheduler (`schedule_interrupts`)
Guarantees a visual change (punch-in zoom, or a keyword-sticker pop for faceless, which has
no face to zoom) at least every N output frames, style/density-dependent, filling only the
gaps real cuts/overlays don't already cover. `PunchZoom.tsx` factored out so 5 of 7
compositions (previously just 2) render punch-ins.

### LOOP E — Editing-quality eval (`backend/eval/edl_eval.py`)
Extended with an `--author` tier (evaluates what the CODE assembles from a fixture, not a
hand-written golden), new invariants (`check_residual_filler`, cadence/speed-cap checks),
new fixtures (`stutter-heavy-01`, `long-pause-01`). Fully keyless, ~0.2s, runs in
`gate.sh --fast`.

### WS0/WS7 — Wiring
`apply_retention_passes` inserted into `_run_edit` upstream of `build_render_plan` (so both
the legacy AND plan author paths benefit identically), each pass individually fail-soft
(`_safe_pass`: reverts to input on exception or a NEW hard invariant violation — a pass can
never turn a working pipeline into a failure). `EDL_AUTHOR=shadow` mode: ships legacy
unchanged but also fire-and-forgets a plan-author attempt + structured diff log, for
gathering real-traffic evidence before ever flipping the default (P6, still pending — see
below).

### LOOP F — Render-formatting eval (`backend/eval/format_eval.py`)
Renders the golden render-plan corpus (17 fixtures, `backend/eval/make_format_corpus.py`)
**locally** via `npx remotion render` (confirmed fully viable without Lambda — this is what
makes the whole free-first QA philosophy work) against a synthetic ffmpeg-generated source,
then checks: duration matches plan, non-black via `signalstats`, faceless+mono actually
desaturated (a real regression this loop would have caught), and a from-scratch Goertzel
pitch detector confirms the speed-2x fixture's audio isn't pitch-shifted. A `--score`
vision tier exists (Sonnet vs `backend/knowledge/format_rubric.md`) but never runs without
both the flag AND `$ANTHROPIC_API_KEY` set. **All 17 fixtures pass.**

### P4 — Schema v2 (`PLAN_SCHEMA_VERSION` 1→2, one coordinated bump)
New EDL fields: `end_card` (tail CTA card), `progress_bar`, `audio.sfx` (deterministic SFX
cues) — added as REAL Pydantic fields (not loose dict keys) specifically so the tweak
flow's `EDL(**data)` round-trip doesn't silently strip them on a creator's first
post-generation edit (a real bug class this codebase already had a comment warning about).
Four new retention passes: `align_emphasis`, `place_hook_overlay`, `place_end_card`,
`synthesize_sfx`. New render components `EndCard.tsx`/`ProgressBar.tsx`, mounted in all 7
compositions. Fixed a real `AudioMix.tsx` bug where a clip with SFX but no music track
would render silent.

**SFX_ASSETS ships with `None` URLs** (`main.py`, read from `SFX_URL_WHOOSH`/`SFX_URL_POP`/
`SFX_URL_HIT` env vars) — no royalty-free SFX files were sourced (inventing/guessing asset
URLs isn't something to do unilaterally), so this feature is currently a fully safe, inert
no-op. **To activate:** source 3 short (≤1s) royalty-free one-shot SFX files, host them
somewhere reachable (Supabase public storage is the established pattern — see
`_rehost_media` in `main.py` for the upload mechanics), and set those 3 env vars.

### P5 — Plan schema fields wired through
`EDIT_PLAN_JSON_SCHEMA` gained `pacing`/`interrupt_density`/`hook_text`/`end_card` (the
plan-authoring LLM now actually emits these); `_author_edl_via_plan` returns the raw plan as
a 3rd tuple element so `_run_edit` can extract retention hints from it (previously these
fields were collected by the schema and silently dropped on the floor). `place_hook_overlay`
already works TODAY via a `script.hook` fallback, independent of this wiring.
`music.wanted`/`vibe` now actually picks a track (deterministic vibe→`MUSIC_TRACKS` map)
when the creator hasn't explicitly toggled the music preference either way.

**Found and fixed a real pre-existing fragility**: the KB digest budget
(`app/knowledge.py _MAX_TOKENS`) was already within ~70 chars of its trim ceiling before
this phase touched anything; a small `retention.md` addition silently truncated
`hook_visual.md` out of the "brief" call's context entirely. Bumped 1000→2200 tokens and
added a test (`test_digest_has_headroom_before_trim_boundary`) that would have caught this
proactively instead of via an unrelated content-presence assertion.

### LOOP U — App/UI formatting QA
`.maestro/ui-manifest.json` (12 screens) + `.maestro/format-audit.yaml` (one Maestro flow,
6 app relaunches) + `scripts/ui_audit.sh` (runs it twice: default text size and
accessibility "extra-extra-large"). **Verified end-to-end against a real simulator** — this
took 5 fix-and-retry cycles against real issues (see "Bugs found" below). A vision tier
(`backend/eval/ui_eval.py` + `backend/knowledge/ui_rubric.md`) exists, sha256-caches
screenshots so an unchanged screenshot never re-pays for a repeat vision call, and — like
every paid tier in this codebase — never runs without both an explicit flag/gate AND
`$ANTHROPIC_API_KEY`.

## Bugs found ALONG THE WAY (worth knowing about even though most are now fixed)
- **Real, confirmed SwiftUI accessibility bug** (NOT fixed — flagged as a spawned
  background task, `task_4f1e1f4c`): the editor's cleanup-panel Cancel button
  (`ProEditorView+Actions.swift` `cleanupPanel`) renders correctly on screen but its
  `.accessibilityIdentifier("editorPro.cleanup.cancel")` never surfaces in the
  accessibility tree — confirmed via two full Maestro runs (one with an 8-second wait) that
  never once found it. Likely a ZStack accessibility-element-merge dropping the Button's
  own identifier in favor of an adjacent unlabeled `Text`. This may also mean real VoiceOver
  users can't navigate to this button correctly — worth a dedicated look, not just a test
  inconvenience. `format-audit.yaml` works around it with a text-based (`"Cancel"`) tap/assert
  instead of the id, same mechanism the flow already uses for the tab bar.
- Two existing (pre-this-session) Maestro flows are stale and would fail if run today:
  `editor-flow.yaml` (asserts `editor.cut`/`editor.apply`/`"Segments"` — a pre-"Pro editor"
  UI shape the codebase deliberately never fully renamed away from) and `settings-shot.yaml`
  (asserts `today.settings`/`"API keys"` — references a "Today" screen that no longer
  exists). Neither was touched — out of scope for this initiative, flagging here so the
  next person who runs the full Maestro suite isn't surprised.

## Current status — GREEN
- **Backend: 844 tests pass** keyless (`cd backend && python3 -m pytest -q`).
- **`scripts/gate.sh --fast`**: green (backend pytest + `edl_eval` + `run_eval` + render
  typecheck/`build:bridge`/`node --test`, all keyless, ~1 min).
- **`scripts/gate.sh --full`: ALL STAGES PASSED** (confirmed 2026-07-13) — iOS
  `BUILD SUCCEEDED`, all 27 render node-tests pass, all 17 LOOP F fixtures pass their
  deterministic checks (duration/non-black/desaturation/pitch), no Lambda/AWS involved,
  genuinely free. (One transient failure during this session's verification was
  `ENOSPC` — the machine's disk was at 98%/399MB free; cleared `render/out/`,
  `.maestro/tests/`, and stale Remotion temp dirs to free ~8GB, then it passed clean. If
  you hit this again, check `df -h /` first before assuming a code regression.)
- **LOOP F**: 17/17 golden fixtures pass their deterministic checks.
- **LOOP U**: all 12 manifest screens pass against a real simulator + real local backend.
- **`scripts/gate.sh --paid`**: NOT run this session (needs `$ANTHROPIC_API_KEY` + explicit
  opt-in per the user's "closest free alternative" budget preference — every paid tier in
  this codebase, `edl_eval --live`/`format_eval --score`/`ui_eval`, is wired and ready
  whenever you want to spend the ~$3-5 it'd cost for one full pass across all three).

## What's genuinely NOT done / deliberately deferred
1. **P6 — the actual point of all this de-risking work — hasn't run yet.** The plan's
   final gate is: run `EDL_AUTHOR=shadow` in production for 1-2 weeks (ships legacy
   unchanged, silently logs a plan-vs-legacy comparison for every real job), review that
   evidence, THEN flip the default to `"plan"`. This can't be simulated locally — it needs
   real production traffic. **Next step for you:** when ready, set `EDL_AUTHOR=shadow` on
   the deployed backend, let it run, then come back and I'll help review the `[shadow]`
   log lines and decide whether to flip the default.
2. **`RETENTION_PASSES` is off in production.** Even once you're ready to ship the
   retention-editing upgrades (filler v2/pacing/interrupts/emphasis/structure/sfx), you
   choose when — e.g. `RETENTION_PASSES=filler,pacing,interrupts,structure` (sfx is a no-op
   anyway without real assets; emphasis is safe to include).
3. **SFX assets** — see P4 above. Needs you to actually pick/license 3 sound files.
4. **The SwiftUI accessibility bug** — see "Bugs found" above; already spawned as its own
   task if you want to start it.
5. **Deploy** — nothing pushed or deployed anywhere. When you're ready: backend → Render
   (manual deploy, webhook is dead, see `docs/DEPLOY.md`); render pipeline → a NEW Remotion
   Lambda site (`npx remotion lambda sites create`, since `PLAN_SCHEMA_VERSION` bumped to 2
   — a stale site would silently ignore end_card/progress_bar/sfx, though `checkPlanSchema`
   would at least log a warning); iOS → a new TestFlight build (this branch includes real
   Swift changes: `AppStore.swift`'s `demoClipStyle`, none of the editor-preview work from
   WS-B needs a NEW build beyond what's already on whatever TestFlight build is current —
   check `git log` against build 18's commit to see the delta).

## Fast resume checklist for the next conversation
```bash
cd /Users/home/Marque/backend && python3 -m pytest -q         # expect 844 pass
cd /Users/home/Marque && ./scripts/gate.sh --fast              # expect all green
cd /Users/home/Marque && ./scripts/gate.sh --full               # expect all green (~3-5 min, real render+iOS build)
cd /Users/home/Marque && git log --oneline main..HEAD          # the 15 commits, this branch vs main
```
