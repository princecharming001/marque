# RALPH-PROGRESS — AI Editor Overhaul

Progress tracker for the Ralph loop executing `docs/PLAN-AI-EDITOR.md`. One work unit per
run: implement → verify (objective + nuanced) → commit → stop.

**Legend:** `[ ]` todo · `[~]` in progress · `[x]` done (+ one line of evidence) ·
`[!]` blocked (+ reason).

**Ordering rule:** pick the first `[ ]` whose dependencies are `[x]`, in strict checklist
order. Each `*.REVIEW` gate depends on all units in its phase. Phase 5a (eval harness) gates
every unit after it. Phases 1 and 2 may proceed in parallel once 5a is green; 3 depends on 1+2;
4 depends on 3; 5b depends on 4; FINAL depends on everything.

**Environment note:** this branch (`claude/yunicorn-marque-latest-push-t018mx`) = `main` +
the plan doc. Ralph plan assumes a Linux CI sandbox (`make setup`, `.venv`); when running on
the dev Mac, adapt commands (backend venv/pytest, `render` npm) but keep the same objective
gates. Backend tests must pass KEYLESS after every unit. Swift can't compile on Linux — verify
Swift via backend↔Swift golden parity fixtures + line-by-line mirror review.

---

## Phase 0 — Render + wiring fixes (no new vendors)

- [x] P0.1 — iOS 1080p HEVC duration-driven bitrate upload ladder + server-driven cap. MediaCompressor now transcodes ≤150s takes to native-1080p HEVC (AVAssetReader→AVAssetWriter, explicit AVVideoAverageBitRateKey, ≤90s→3.8Mbps / 90–150s→2.6Mbps, cap-budget clamp w/ 8% mux margin, short-edge capped at 1080); >150s + overshoot/failure fall to the 720p→540p preset ladder. `max_upload_bytes` added to all mint responses (env `MAX_UPLOAD_BYTES`), threaded into all 3 `forUpload` callers. Evidence: `make test` 480 passed (incl. new `test_mint_upload_url_cap_is_env_driven` + cap assert on `test_mint_upload_url`); iOS `dev.sh build` BUILD SUCCEEDED (Swift compiled on this Mac — stronger than golden-parity).
- [x] P0.2 — render encode quality. `lambda-render.ts`: final (non-preview) renders now pass `crf:17` + `jpegQuality` (env `REMOTION_JPEG_QUALITY`, default 95) + `imageFormat` (env `REMOTION_IMAGE_FORMAT`, default jpeg); preview path (scale 0.5/crf 30) untouched. `remotion.config.ts`: `setVideoImageFormat`/`setJpegQuality` from the same env (CLI-render parity). Evidence: `npm run build:bridge` tsc rc=0; `npx remotion still Marque-Faceless` (authentic build_render_plan fixture, karaoke captions) rendered clean to /tmp/p02-faceless-f48.png (inspected — config loads, `setJpegQuality` valid, captions crisp).
- [x] P0.3 — 12-frame min-clip guard. `build_render_plan` now collects candidate kept intervals, drops any whose OUTPUT length < `MIN_CLIP_OUTPUT_FRAMES` (12 = 400ms), and keeps the single longest if every candidate is a sliver (never empties a heavily-cut take). Swift mirror in `EditorModel.keptIntervalsWithSpeed` (used by EditorPlayerController → preview playback now skips the same slivers the render drops) with `kMinClipOutputFrames = 12`; identical filter + banker's-rounding output length + first-on-tie fallback (line-by-line verified). Evidence: `make test` 484 passed incl. 4 new golden tests (sub-12 sliver dropped, exactly-12 kept, all-slivers→longest, 2x-speed measured in output frames); iOS BUILD SUCCEEDED.
- [x] P0.4 — FastCuts strobe fix. Flash is now rate-limited (only fires at a cut ≥45 output frames since the last flashed cut — filler micro-cuts get none) and softened (peak 0.10 opacity on the boundary frame, linear fade to 0 across 3 frames). Evidence: `tsc --noEmit` rc=0; rendered a fixture with cutStarts=[100,120] — frame 100 avg_grey 29 (≈0.10 flash), frame 101 grey 19 (mid-fade), frame 103 grey 1 (ended, no strobe), frame 120 grey 1 (micro-cut 20f<45 → no flash). Measured via ffmpeg.
- [x] P0.5 — GreenScreen redesign. Deleted `mixBlendMode:"multiply"` (fake key that muddied the speaker into the backdrop). Speaker now sits in a rounded (28px), white-bordered, drop-shadowed card filling the bottom ~54% (object-fit cover via CutVideo); reference text card centered in the top 45%, clear of the speaker card. No blend modes. Evidence: `tsc --noEmit` rc=0; rendered /tmp/p05-greenscreen.png (inspected) — clean PiP, text card legible top, speaker card framed bottom, no collision, no multiply muddiness.
- [x] P0.6 — loudness + audio polish. New `backend/app/audio.py`: `probe_loudness(url)` via `ffmpeg -af loudnorm=...:print_format=json` (fails soft — no ffmpeg/url/parse → None) + `gain_db` (clamp target−measured to ±12). Probed in `_run_analysis` AND `_run_pipeline` parallel with transcription (asyncio.gather); `_run_edit` sets `audio.gain` = gain_db(job loudness). `Audio.gain` added (additive optional) → build_render_plan → types.ts AudioPlan → `CutVideo.tsx` applies 10^(gain/20) linear multiplier (threaded through all 8 CutVideo call sites). ffmpeg added to Dockerfile. AudioMix: smoothed duck (±8-frame ramp, no more per-word pumping) + composition fade in/out. **DEVIATION (documented in code + here):** the plan's per-seam equal-power crossfade needs the music LOOP PERIOD, only obtainable via `useAudioData`/`getAudioDurationInSeconds` (CORS-gated decodeAudioData) — the catalog music (googleapis, no CORS) makes that FAIL THE RENDER (verified). Adding `music.duration` is disallowed (only end_frame/audio.gain additive). Kept CORS-safe `<Audio loop>` + delivered fade in/out instead. Evidence: `make test` 489 passed (+5 loudness tests, TS-contract `_TS_AUDIO_PLAN_KEYS` grown); `tsc --noEmit` rc=0; rendered /tmp/p06-audio.mp4 with a real music track — succeeds with audio+video streams (the useAudioData version errored on CORS).
- [x] P0.7 — captions. `CaptionWord.end_frame` (additive optional, from AssemblyAI end_ms) populated at all build sites + remapped through map_point in build_render_plan (clamps into cut). `Captions.tsx`: hides the block after the last word's end_frame +12 and during >30-frame silences; `line` mode is now stable 5-word chunks (was a per-frame sliding window); DEFAULTS grouping → `phrase`. CaptionOptions.grouping default `line`→`phrase` (backend + Swift EditorCaptionOptions + parse fallback); sim `line` mirrored to stable 5-word chunks. Evidence: `make test` 491 passed (+2 end_frame remap tests; TS `_TS_CAPTION_KEYS` grown to include end_frame w/ a fixture caption exercising it; grouping-default assertions → phrase); `tsc` rc=0; iOS BUILD SUCCEEDED; rendered captions — f15 shows the 3-word phrase (active word highlighted), f40 blank in the silence, f135 blank after the last word.
- [x] P0.8 — real temperature grade. Replaced warm=`sepia()` / cool=`hue-rotate()` / adjust.temperature hacks with a diagonal SVG `feColorMatrix` that scales R/B channels oppositely (warm +R/−B, cool −R/+B, gain = 0.3·temp). `temperatureFilter(look)` computes the def (preset + adjust.temperature, clamped ±1); `lookFilterCSS` references it via `url(#id)`; CutVideo renders the `<filter>` def (Grade.tsx returns null for temp-only looks so the def lives with the video). Evidence: `tsc` rc=0; rendered on a neutral gray source — neutral RGB (126,126,126), warm (145,125,107) R↑/B↓, cool (110,130,148) R↓/B↑, green ~constant = a true white-balance shift (not the old all-hue muddying). (Grade.tsx unchanged — it does vignette/dips, not color; noted the plan's file ref was approximate.)
- [ ] P0.9 — AI wiring: pass `broll_moments`/`punch_in_moments` into `edl_prompt`; wire `broll`/`punch_ins` toggles in `_apply_edit_prefs`; replace `transcript_words[:200]` with `_frame_anchored_transcript`; EDL call → `anthropic_json` + new `EDL_JSON_SCHEMA` + Sonnet + temperature 0
- [ ] P0.REVIEW — full-phase diff review, full suite, grade vs plan intent 0–100; <85 → file fix units before proceeding

## Phase 5a — Eval harness (gates everything after)

- [ ] P5a.1 — fixture set: 8–12 takes' word-timing JSON (scripted/rambling/listicle/low-energy/buried-hook) as checked-in fixtures
- [ ] P5a.2 — `backend/eval/edl_eval.py` keyless invariants (no slivers, hook ≤90 output frames, caption coverage == kept words, b-roll grammar, drops ⊆ take, known-bad caught) + `eval/edit_golden.py`
- [ ] P5a.3 — live scorecard mode (LLM judge vs rubric; per knowledge_version/prompt version; regression thresholds) — runnable keyless as a no-op
- [ ] P5a.REVIEW

## Phase 1 — Twelve Labs video dossier

- [ ] P1.1 — `backend/app/dossier.py`: provider chain twelvelabs → claude_frames → off; TL index lifecycle (create-once, upload task, poll, Pegasus generate with dossier JSON schema, `DOSSIER_TIMEOUT_S` fail-down); claude_frames fallback (ffmpeg 0.5fps keyframes + full-res first frame → one vision call); all timestamps → `[fN]` via `ms_to_frame`; mocks + tests keyless
- [ ] P1.2 — plumb into `_run_analysis` via `asyncio.gather` with transcription; persist `job["dossier"]`; staged progress statuses
- [ ] P1.3 — brief fusion: dossier param in `edit_brief_prompt`; replace the "you cannot see the video" clause with dossier-only visual grounding; grounding discipline extended to visual claims
- [ ] P1.REVIEW

## Phase 2 — Editing knowledge base

- [ ] P2.1 — `backend/knowledge/` v1: retention.md, hooks.md, pacing.md, broll.md, captions.md, audio.md, review_rubric.md, MANIFEST.json — operational rules with numbers (seed data in the plan's research section)
- [ ] P2.2 — `backend/app/knowledge.py` loader + `digest(style, video_type, call)` (~600–1000 tokens); `EDIT_RUBRICS` becomes a thin KB wrapper; `knowledge_version` stamped into jobs + eval scorecard
- [ ] P2.3 — reference-reel patterns: playable Apify reel → same dossier adapter (cached per URL) → measured `reference_patterns` block fed to brief + edit-plan
- [ ] P2.REVIEW

## Phase 3 — Judgment ("LLM decides, code assembles")

- [ ] P3.1 — `EDIT_PLAN_JSON_SCHEMA` typed-op schema in prompts.py (open_on/keeps/cuts/order/punch_ins/broll/caption_plan/text_cards/music/pacing_intent); every citable frame must exist in the inputs
- [ ] P3.2 — `assemble_edl()` pure assembler in `edl.py`: captions ALWAYS from `_clean_words`; deterministic drops win; code-enforced b-roll grammar (J-cut lead ~12f, 2–3s holds, ≥90f spacing, hook/CTA protection); cut boundaries snap to word boundaries ±3 frames; min-clip guard; layout synthesis; `speech_frames` regeneration
- [ ] P3.3 — `check_edl_invariants()` in code (the `edl_verify_prompt` checklist as functions); LLM verify only for reorder coherence; Sonnet repair only on semantic failures; `EDL_AUTHOR=plan|legacy` flag, both paths eval'd head-to-head via P5a
- [ ] P3.REVIEW

## Phase 4 — Execution polish

- [ ] P4.1 — b-roll multi-candidate: Pexels `per_page` = `BROLL_CANDIDATES` (6); `_rerank_broll` Haiku-vision scoring vs cue + dossier palette/energy; cache; top-1 fallback; upgrade `/v1/broll/match` for own-media
- [ ] P4.2 — punch-in exit easing (`TalkingHead.tsx`, `DuetSplit.tsx`); loop-friendly endings (assembler trims trailing dead-air ≤10 frames)
- [ ] P4.REVIEW

## Phase 5b — Self-review loop

- [ ] P5b.1 — in `_render_all_clips`: preview render → Claude-vision frame scoring vs `review_rubric.md` → `{score, issues[{code, frame, fix_op}]}` with fix_ops from the tweak-envelope set → apply via `apply_edl_ops` → one final render; hard limits (one revision, only if score < `SELF_REVIEW_THRESHOLD` 70, never on re-renders/tweaks); `SELF_REVIEW` flag
- [ ] P5b.REVIEW

## FINAL gauntlet

- [ ] F.1 — full backend suite + keyless eval + render typecheck, all green in one clean run
- [ ] F.2 — end-to-end mock walkthrough on fixtures for `VIDEO_UNDERSTANDING` = off / claude_frames(mocked) / twelvelabs(mocked HTTP): clips → brief (dossier present) → confirm (toggles honored) → EDL (no slivers, captions covered) → `build_render_plan`
- [ ] F.3 — render one fixture video per composition style; inspect each output; note verdicts
- [ ] F.4 — write `docs/RALPH-FINAL-REPORT.md`: what shipped per phase, evidence index, cost-per-clip accounting vs the $0.25 ceiling, known gaps/deferred items
- [ ] FINAL — print `ALL_PHASES_COMPLETE`

---

## Run log

- (bootstrap) Created this progress file from the plan checklist. Branch confirmed = `main` + plan
  doc (clean superset, no divergence). Next: P0.1.
