# Marque editing-pipeline hardening — session handoff (2026-07-12)

## ⏩ SESSION 2 (continuation) — what got finished
Picked up the STILL OPEN list below and closed **all CRITICAL, all HIGH, and 8 MEDIUM**
findings. Everything is UNCOMMITTED in the working tree (same standing deploy rule).
**Full green gate re-verified:** `667` backend tests pass (was 654; +13 new in
`test_editing_hardening_r3.py` and updated `test_editor_hardening.py`), `tsc --noEmit`
clean on both configs, `npm run build:bridge` clean, **iOS `BUILD SUCCEEDED`**.

Fixed this session:
- **#6/#43 CRITICAL (iOS)** — `ProEditorView.save()` only defers the render for a
  genuinely pixel-identical **split-only** batch now; cuts/reorders/mutes/volume all
  re-render, so Library no longer plays/publishes the pre-edit video. Kept
  `saveNeedsRender` (the "Render"/"Save" button label) in lockstep with the new rule.
- **#8/#44 CRITICAL (iOS)** — `TweakChatSheet.startPolling` checks `last_render_failed`
  before claiming "the new cut is live", and handles a hard `failed` status. No more
  false success on a failed re-render.
- **#17 (backend)** — poll + stall budgets scale with output `total_frames`
  (`_scaled_render_budgets`), so a 3-min take isn't killed as timed-out/stalled.
- **#18 (backend)** — submit no longer double-dispatches: renderMediaOnLambda starts the
  render inside the submit call, so the timeout-retry is removed and replaced with one
  generous cold-start budget (`RENDER_SUBMIT_TIMEOUT_S=90`). No more orphaned 2nd render.
- **#19 (backend+render)** — `schema_version` stamped in the plan (`edl.py
  PLAN_SCHEMA_VERSION`) and checked once per render in `types.ts checkPlanSchema`
  (via `planDuration`) → warns in Lambda logs on backend/site deploy skew.
- **#9 (backend)** — `edit_overlay` / `set_segment_transform` now stage into a copy and
  commit atomically; a later invalid field no longer half-writes the persisted EDL.
- **#10 (backend + iOS mirror)** — `split_segment` / `trim_*` remap
  `transitions[].after_segment` (drop popped, shift `>=idx` by +1) so fades stay on the
  right boundary; mirrored in `LocalEDLEngine` so the preview matches the render.
- **#45 (backend + iOS mirror)** — `split_segment` carries the parent's speed + canvas
  transform onto both halves (both `edl.py` and `LocalEDLEngine`).
- **#14 (iOS)** — `MusicCatalog` swapped the two Ogg Vorbis tracks (AVPlayer can't decode
  → silent) for range-serving `audio/mpeg` mp3s (verified reachable).
- **#5 (backend)** — `verify_and_repair_edl` re-runs `clamp_edl_to_source` on the SONNET
  repair (it sits downstream of the primary clamp) so a repair can't reintroduce an
  out-of-bounds `src_out`.
- **#16 (backend)** — a tweak `add_broll` that resolves to nothing now surfaces a
  `broll_unresolved` warning (refreshed, not appended) instead of a silent identical render.
- **#47 (iOS)** — dismissing the editor mid-save no longer cancels the commit/poll task
  (it owns the server commit and writes back to the store, which outlives the view), so
  the render isn't orphaned and Library reflects the finished edit.
- **#26 LOW (backend)** — an all-sliver edit still delivers the longest fragment but now
  emits a `degenerate_edit` plan warning (surfaced to the clip) instead of shipping a
  silent sub-second "ready" clip.

Also reconciled **#7**: the branch it named (`main.py` needs_render=False when a clip is
"rendering") is **unreachable** — the tweak endpoint 409s at both guards when the clip is
rendering, so the previous session's guard work already turned the silent-stale into an
honest 409. The real-world manifestation of that class was #6/#43 (deferred change never
renders), now fixed.

### STILL OPEN after session 2 (deferred — rationale each)
- **#20 (backend, mid-render re-attach)** — a backend restart mid-render can't re-attach
  to the in-flight Lambda (bucket_name isn't stored on the clip; render task is dead).
  Real value (manual deploys restart often) but it's a delicate feature touching the
  watchdog + `render_gen` concurrency and the restore path — deferred rather than ship
  unverified. *Sketch:* store `clip["bucket_name"]` next to `render_id` in
  `_rerender_clip`/`_render_all_clips`; in `_restore_clip_job` (or the sweep), for a clip
  `status=="rendering"` with `render_id`+`bucket_name`, `_bump_render_gen` and spawn a
  poll-only resume of `_rerender_clip` instead of letting the watchdog fail→retry.
- **#46 (iOS, AppStore.swift ~661)** — a `confirmClips` transport failure falls back to
  `makeClips` (a whole new job) even if the confirm actually landed server-side → duplicate
  upload+render. Fix needs a job-status probe before the fallback (adopt the in-flight job
  if it moved past `brief_ready`); left for a session that can drive the sim to verify.
- **#3 / #11 (iOS editor)** — reorder no-op when snapping changes segment count; "tighten
  the ends" computes lead/tail in source order but trims in play order. Couldn't pin the
  exact current call sites cleanly; low-frequency, needs sim verification.
- **#48 (iOS, AppStore.swift ~787)** — `pollJob` 5-min cap abandons long one-tap
  pipelines; Library repolls can stack duplicate pollers.
- **#15 (backend, higgsfield)** — a Higgsfield TIMEOUT poisons the `_broll_gen_failed`
  negative cache (transient treated as permanent). Narrow (Higgsfield-gated, initial
  pipeline only, self-heals at 5000-entry reset); a correct fix needs `generate_broll` /
  `_poll_request` to distinguish timeout from genuine failure (multi-fn signal change).
- **#13 (infra)** — renders hot-link third-party media (Pexels/music URLs); link rot =
  broken render. Music fallback partially de-risked via #14 but the broader
  cache/proxy-your-assets work is infra-scoped.

## Goal
User: *"the editing builds are failing, refine the editing logic until it is flawless...
do copious research and testing."* This is the AI editor / EDL / Remotion-render pipeline
that turns a recorded take into a finished vertical clip.

## Where the work lives
- **Repo:** `/Users/home/Marque` — branch `main`, baseline commit `3b45a7e` ("iOS build 17").
- **All changes are UNCOMMITTED in the working tree.** Nothing pushed, nothing deployed
  (standing rule: Marque is LIVE, deploy only on explicit "push it now").
- Modified files (editing-relevant):
  - `backend/app/edl.py` (+171 lines)
  - `backend/main.py` (+329)
  - `backend/prompts.py` (+5)
  - `backend/supabase_persistence.py` (+8)
  - `render/src/components/CutVideo.tsx`, `render/src/types.ts`
  - `render/src/compositions/{Faceless,GreenScreen,DuetSplit}.tsx`
  - `render/src/lambda-render.ts` (rebuilt into `render/dist/lambda-render.js`)
- New test file: `backend/test_editing_hardening_r2.py` (32 regression tests).
- Modified test file: `backend/test_editor_hardening.py` (fake-bridge signatures updated
  for the new stdin arg — `**kwargs` / `communicate(self, input=None)`).

## Current status — GREEN
- **654 backend tests pass** (`cd backend && .venv/bin/python -m pytest -q`).
- **TS typechecks clean** (`cd render && npx tsc --noEmit && npx tsc --noEmit -p tsconfig.bridge.json`).
- **Bridge rebuilds clean** (`cd render && npm run build:bridge`).
- **EDL eval PASS**, incl. live scorecard with real keys: gate_pass_rate=1.00,
  slop_rate=0.0, judge hook 80/spec 86/voice 88 (`.venv/bin/python -m eval.run_eval`
  after `source .env`).
- **iOS builds** (`xcodebuild -project ios/Marque.xcodeproj -scheme Marque
  -destination 'generic/platform=iOS Simulator' build` → BUILD SUCCEEDED) — but note
  I made NO iOS changes; the critical iOS bugs below are untouched.

## Diagnosis of the original failure
No single smoking gun; prod infra is actually healthy:
- Prod `/readyz` = live; deployed Lambda functions are `4.0.484`, client pinned `4.0.484`
  (no version skew — the old skew bug is fixed and stayed fixed).
- Deployed site bundle contains the current feature markers.
- Local code == `origin/main` == build 17.
The "builds failing" is a **cluster of latent correctness/robustness bugs** in the
pipeline that a deep multi-agent review surfaced — stranding vectors, races, contract
drift, and a cross-language rounding mismatch. Fixes below.

## How the review was run
A `Workflow` fan-out (`marque-editing-flawless`): 5 layer-mappers + 2 researchers →
9-dimension finder rounds → 3-lens adversarial verification. It surfaced **49 findings**;
verification confirmed several as REAL before the run hit the **11pm PT session limit**
(75 agents done, 106 errored on the limit). The full result + per-agent journal:
- `/private/tmp/claude-501/-Users-home-URAP---Lead---Levine/609c7940-fd4b-4534-a3ce-5a488ad5ae69/tasks/w6w7i5b2l.output`
- `.../subagents/workflows/wf_1dcc3207-e06/journal.jsonl`
Resume the workflow (cached agents replay): `Workflow({scriptPath:
'.../workflows/scripts/marque-editing-flawless-wf_1dcc3207-e06.js',
resumeFromRunId: 'wf_1dcc3207-e06'})` — but **most findings are already fixed**, so a
fresh targeted pass on the OPEN list below is more useful.

---

## FIXED (backend + render TS) — all covered by new tests

### Terminal-state / stranding / races (backend/main.py)
1. **Job-level `pipeline_gen` guard** — the big one. asyncio never cancels a
   watchdog-failed pipeline task; when it woke it clobbered a healthy retry's state.
   Added `_bump_pipeline_gen`/`_owns_pipeline`; every job-level write in
   `_run_analysis`/`_run_auto_pipeline`/`_run_pipeline`/`_run_edit`/`_retry_render` is
   now gated. Retry/confirm/watchdog bump the gen to take ownership.
2. **Watchdog anchor** now = latest of created_at/stage_started_at (via `_mark_stage`),
   so a long brief-review then confirm no longer insta-fails as `pipeline_interrupted`;
   sweep also spares a job while a clip is actively rendering within its own budget.
3. **Retry stamps the stage** (`_mark_stage`) so the fresh attempt's watchdog/ETA don't
   inherit the failed run's stale clock.
4. **Semaphore queue-time exemption** — `render_started_at` re-stamped at semaphore
   acquisition in all three render fns, so burst queue-wait can't trip the render
   watchdog; superseded attempts `continue`/`return` before spending a Lambda render.
5. **Retry 409 guard** now includes `analyzing`/`processing`; **confirm sets status
   synchronously** + bumps pipeline_gen so a same-tick double-confirm 409s.
6. **Tweak race re-check** after the LLM await (409 if a pipeline started meanwhile).
7. **auto_confirm retry** routes back through `_run_auto_pipeline` (was silently
   dropping toggles/brief via the legacy pipeline). Stored `auto_confirm` on the job.
8. **TTL sweep** anchors at latest activity (+ `restored_at`) and never evicts in-flight
   jobs — a restored days-old session no longer gets re-evicted on the next poll.
9. **Restore returns 503** (`session_storage_unavailable`) on a Supabase transport
   failure instead of 404/410 — a network blip no longer tells iOS the edit session
   expired. `supabase_persistence.load_clip_job` now returns `UNAVAILABLE` sentinel.

### Render robustness (backend/main.py + render bridge)
10. **Submit props via stdin** (`argv "-"` + `stdin_data`) — long caption-heavy plans
    exceeded Linux's 128KB `MAX_ARG_STRLEN` and failed execve. `lambda-render.ts` reads
    stdin; bridge rebuilt. Smoke-tested with a 400KB payload.
11. **Poll tolerates 3 consecutive transient bridge errors** — a single missed poll
    (node OOM/AWS throttle/blip) no longer fails a render that's succeeding server-side.
12. **Partial REMOTION_* env → `render_misconfigured`** structured failure instead of
    silently shipping the raw unedited source as a "ready" edit. (Fully-keyless still
    mocks source-passthrough for dev.)
13. **Bridge stderr parsed before truncation** — node runtime warnings prefixing stderr
    no longer mask the real Remotion error (`rfind('{"error"')`).
14. New error codes: `pipeline_interrupted`, `render_misconfigured`.

### EDL correctness (backend/app/edl.py)
15. **Cross-language rounding** — `pyRound` (banker's/half-to-even, matching Python
    `round()`) added to `render/src/types.ts`; `CutVideo.clipOutFrames` uses it instead
    of `Math.round`. Proven: 100 mismatches at x.5 lengths → 0. **This is the fix most
    likely tied to visible "wrong" renders (drifting captions, truncated endings).**
16. **`assemble_edl` empty-words fallback** was `30000` FRAMES (16m40s) not ms → now
    `ms_to_frame(30000)` (=900f/30s).
17. **`map_point` clamps inside the clip's output span** — speed>1 no longer rounds a
    tail frame onto the next clip.
18. **`open_on` hook-pull guarded** — refuses a pull that would leave <6s kept (a
    hallucinated deep "hook" could delete most of the take; kept-duration invariant is
    deliberately soft so nothing else caught it).
19. **`clamp_edl_to_source`** (new) — clamps every source-coord range in an LLM EDL to
    the real source extent before render (hallucinated `src_out` past end broke Lambda).
    Called in the legacy author path.
20. **Context-aware filler stripping** — `um/uh` always cut; discourse markers
    (`so/like/right/...`) cut ONLY at a clause boundary (first word / after ≥250ms pause
    / after another filler). Stops mid-sentence content deletion ("turn **right** here",
    "I feel **like** it works") and mid-phrase jump cuts. `safe_default_edl` now includes
    real filler drops (was captions-only, so "fillers stripped" copy was a lie).
21. **`highlight_words` normalized** to match the renderer (`[^a-z0-9]` strip) — "A.I."
    now yields "ai" instead of an unmatchable "a.i.".
22. **`remove_overlays` with an invalid range is skipped**, not fail-open wiping ALL
    overlays of that kind.
23. **One incoherent overlay/broll window strips just those windows**, not nuking the
    whole tailored plan to the untailored safe default.
24. **EDL author `max_tokens` 4000→8000 for long takes** (>400 words) — the legacy
    schema echoes captions, so long takes truncated → parse-fail → silent whole-take
    safe default. Also rebuild captions deterministically if the model echoed none.

### Compositions (render/src)
25. **DuetSplit**: `assemble_edl` now synthesizes a play(≤2.5s)→freeze react schedule
    for `duet_split` (plan author had no react_schedule concept, so the top panel played
    full-volume over the whole rebuttal). Empty-schedule fallback also ducks to 0.12.
26. **GreenScreen**: dropped the permanent "Reference post" placeholder card burned into
    the video when no text_card exists.
27. **Faceless**: soft dark gradient ground instead of flat `#000` (a no-b-roll faceless
    edit was an all-black delivered "success").

### Prompts
28. **`set_music`** prompt made honest — there is no server-side music search, so a
    query-only op is rejected; prompt now tells the model to point at the picker.

---

## STILL OPEN (prioritized) — NOT fixed this session

### CRITICAL — iOS (I made zero iOS changes; these are the scariest)
- **[#6/#43] Manual-editor structural saves defer render forever.**
  `ios/.../Editor/ProEditorView+Actions.swift:674` — cuts/reorders/mutes committed with
  `defer_render` and no follow-up render, so **Library plays and publishes the pre-edit
  video.** Backend `defer_render` contract assumes "next apply renders" — verify the
  next-render actually fires, or force a render on Save.
- **[#8/#44] TweakChatSheet false success.** `ios/.../TweakChatSheet.swift:333` — reports
  "Done — the new cut is live" when the re-render FAILED and the old URL was restored
  (`last_render_failed`/`last_render_error` exist on the clip but aren't surfaced).

### HIGH — backend
- **[#7] Tweak committed while a render is in flight is never rendered** (main.py ~2521):
  `needs_render=False` when a clip is already rendering, comment says "picked up by the
  next render" but there is no next render. My race work touched adjacent code but did
  NOT close this — needs an explicit re-render-after-current-completes.
- **[#17] Poll budget/stall are duration-independent** (RENDER_POLL_MAX_S=240,
  RENDER_STALL_S=75): long/heavy renders detected as stalled/timed-out. Scale budgets by
  `total_frames`. (I added transient-error tolerance, NOT duration scaling.)
- **[#18/#31] Submit-timeout retry double-invokes a non-idempotent Lambda render** — the
  killed first bridge may have already dispatched; retry starts a second, orphaned render.
- **[#19] No schema/version stamp in inputProps** — backend-vs-deployed-site prop drift
  fails silently. Add a plan `schema_version` and assert it in the compositions.
- **[#13] Renders hard-depend on hot-linked third-party media** (Google demo-bucket
  music URL, Pexels/Higgsfield cached URLs) — link rot / expiry = broken render.

### MEDIUM — backend/EDL
- **[#20]** Mid-render restart can't re-attach (bucket_name/render_id not persisted).
- **[#3]** `plan.order` discarded when snapping changes segment count (reorder no-ops).
- **[#5]** `verify_and_repair_edl` reinjects unvalidated free-form LLM output downstream
  of the deterministic checks.
- **[#9]** `edit_overlay`/`set_segment_transform` half-apply (field writes persist even
  when the op reports `applied=false`).
- **[#10]** `trim_start/trim_end/split_segment` never remap `transitions[].after_segment`
  → fades move to the wrong boundary.
- **[#11]** Suggested-edits "Tighten the ends" computes lead/tail in SOURCE order but
  trims cut in PLAY order (wrong under a reorder).
- **[#15]** Higgsfield timeout poisons the negative cache + bills abandoned DoP jobs.
- **[#16]** Tweak `add_broll` reports `applied=true` but can render pixel-identical
  (previews never show b-roll).
- **[#26 LOW]** min-clip sliver fallback can render a sub-second "ready" video.

### MEDIUM — iOS
- **[#45]** `split_segment` resets segment speed + canvas transform (LocalEDLEngine.swift
  :156, both mirror and backend — check backend `split_segment` too).
- **[#46]** `confirmClips` transport failure silently creates a SECOND live job
  (duplicate upload + render spend) — AppStore.swift:661.
- **[#47]** Dismissing the editor while Save's POST is in flight orphans the render —
  ProEditorView.swift:203.
- **[#14]** 2 of 3 MusicCatalog tracks are Ogg Vorbis — unplayable by AVPlayer (silent
  previews) — MusicCatalog.swift:12.
- **[#48 LOW]** pollJob 5-min cap abandons long one-tap pipelines; Library repolls stack
  duplicate pollers — AppStore.swift:787.

---

## DEPLOY NOTES (when the user says "push it now")
Two independent deploy channels — **both** are needed for the full fix set:
1. **Backend** (`backend/`, `render/dist` bridge, Dockerfile) → Render deploy. The
   webhook is DEAD; deploy manually per `docs/DEPLOY.md`
   (`POST .../services/srv-d94rk95ckfvc73ag4990/deploys`, needs `$RENDER_API_KEY` from
   the account owner — not on this machine).
2. **Remotion site bundle** (`render/src/*.tsx` + `types.ts`, incl. the `pyRound` fix)
   → `cd render && npx remotion lambda sites create src/index.ts --site-name=marque-render`.
   **A backend deploy alone does NOT update the deployed composition code** — the rounding
   fix, Faceless/GreenScreen/DuetSplit changes all live in the site bundle and require this
   redeploy. AWS creds are in `backend/.env` (REMOTION_*).

## Fast resume checklist for the next conversation
```bash
cd /Users/home/Marque/backend && .venv/bin/python -m pytest -q            # expect 654 pass
cd /Users/home/Marque/render && npx tsc --noEmit && npm run build:bridge  # expect clean
cd /Users/home/Marque && git status -s                                    # see the uncommitted set
```
Then pick from STILL OPEN — start with the two CRITICAL iOS bugs (#6/#43, #8/#44), which
are the most likely to read to a user as "the build failed / played the wrong video."
Regression-test pattern lives in `backend/test_editing_hardening_r2.py` (keyless, monkeypatch
the external seams — mirror `test_editor_hardening.py`).
