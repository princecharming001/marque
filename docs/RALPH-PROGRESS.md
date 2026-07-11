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

- [ ] P0.1 — iOS 1080p HEVC duration-driven bitrate upload ladder (`LiveClipEngine.swift` MediaCompressor; server-drivable `maxUploadBytes`)
- [ ] P0.2 — render encode quality: `crf 17`, `jpegQuality 95`, env knobs (`lambda-render.ts`, `remotion.config.ts`)
- [ ] P0.3 — 12-frame min-clip guard in kept intervals + Swift mirror + golden tests (`edl.py`, `LocalEDLEngine.swift`)
- [ ] P0.4 — FastCuts flash → 0.10 opacity over 3 frames, rate-limited ≥45 output frames (`FastCuts.tsx`)
- [ ] P0.5 — GreenScreen: delete multiply blend; speaker-card layout (`GreenScreen.tsx`)
- [ ] P0.6 — loudness: ffmpeg in Dockerfile; `backend/app/audio.py` probe in `_run_analysis`; `audio.gain` end-to-end; AudioMix crossfading loop + smoothed duck
- [ ] P0.7 — captions: `end_frame` field; hide after last word +12 and in >30-frame silences; `phrase` grouping default; stable precomputed `line` mode; Swift default mirrored
- [ ] P0.8 — real temperature grade via SVG feColorMatrix warm/cool (`CutVideo.tsx`, `Grade.tsx`)
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
