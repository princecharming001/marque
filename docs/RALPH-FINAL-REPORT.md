# RALPH FINAL REPORT — AI Editor Overhaul

Autonomous execution of `docs/PLAN-AI-EDITOR.md` — "Make Marque's AI Editor one of the best
on the market." All phases complete on branch `claude/yunicorn-marque-latest-push-t018mx`
(17 unit commits + this report), each unit implemented → verified → committed → pushed.

**Headline:** the editor went from *editing blind* (transcript-only, Haiku@temp-1.0 raw-EDL,
discarded intelligence, quality-degrading render) to a grounded **understand → judge →
assemble → render → self-review** pipeline: Twelve Labs / Claude-frames visual dossier, a
versioned editing-craft knowledge base, "LLM decides / code assembles" with an uninventable
assembler, vision-re-ranked b-roll, and a closed-loop self-review — all gated by an
invariant + scorecard eval harness, all additive behind the EDL contract.

---

## What shipped, per phase

### Phase 0 — Render + wiring fixes (flag-free, shipping now)
| Unit | Change |
|---|---|
| P0.1 | iOS 1080p HEVC duration-driven upload ladder (kills the upscale blur on faces) + server-driven `max_upload_bytes` |
| P0.2 | Final-render `crf 17` + `jpegQuality 95` (env-tunable) — kills caption halos |
| P0.3 | 12-frame min-clip guard in `build_render_plan` + Swift preview mirror + golden tests |
| P0.4 | FastCuts strobe → soft 0.10 rate-limited accent flash |
| P0.5 | GreenScreen: deleted the fake `multiply` key; speaker in a rounded PiP card |
| P0.6 | Loudness normalization (`probe_loudness` → `audio.gain`) + smoothed duck + comp fades |
| P0.7 | Caption `end_frame` + hide-after-last/in-silence + stable line + `phrase` default |
| P0.8 | Real temperature grade (SVG `feColorMatrix`, not sepia/hue-rotate) |
| P0.9 | AI wiring: moments threaded, toggles honored, full transcript, structured Sonnet EDL call |

**P0.REVIEW: 92/100.**

### Phase 5a — Eval harness (gates every downstream author)
`eval/edit_fixtures.py` (5 category takes), `eval/edit_golden.py` (reference-good + 8 crafted
known-bad), `eval/edl_eval.py` (plan-level invariant checkers: no slivers, hook ≤90 out frames,
caption coverage, b-roll grammar, drops ⊆ take, edl valid) + keyless self-check + live scorecard
(independent Sonnet judge vs rubric, per `knowledge_version`, `MIN_GATE_PASS_RATE`). Invariants
assert on the render PLAN → author-agnostic (gates the current path AND `assemble_edl`).
**P5a.REVIEW: green.**

### Phase 1 — Understanding layer: Twelve Labs video dossier
`app/dossier.py` — `generate_dossier()` provider chain `twelvelabs → claude_frames → off`
(fail-down), TL index lifecycle + Pegasus generate, Claude-frames ffmpeg fallback, seconds→`[fN]`
normalization to dossier v1. Plumbed into both analysis gathers in parallel with transcription
(staged `dossier_status`), fused into the brief (swaps "you cannot see the video" for dossier-only
visual grounding). Flag `VIDEO_UNDERSTANDING=off` default. **P1.REVIEW: 88/100.**

### Phase 2 — Editing knowledge base
`knowledge/*.md` (retention/hooks/pacing/broll/captions/audio/review_rubric) + `MANIFEST.json`
(`kb-2026.07`) — operational craft numbers as the single source of truth. `app/knowledge.py`
`digest(style, video_type, call)` (~1000-tok, call-scoped) wired into brief + edit-plan prompts;
`knowledge_version` stamped into jobs. Reference-reel patterns: run a trending reel through the same
dossier adapter (cached per URL) → measured `reference_patterns` fed to the prompts.
**P2.REVIEW: 90/100.**

### Phase 3 — "LLM decides, code assembles"
`EDIT_PLAN_JSON_SCHEMA` + `edit_plan_prompt` (typed decisions only). `assemble_edl()` pure fn:
captions ALWAYS from cleaned words, deterministic drops fold, plan/brief cuts snap to word
boundaries, buried hook pulled forward, **b-roll grammar enforced in code** (J-cut 12f, 60–90f
holds, ≥90f spacing, hook/CTA protection), min-clip guard, layout synthesis, `speech_frames`
regen. `check_edl_invariants()` = the verify checklist as code. `EDL_AUTHOR=plan|legacy` flag
(default legacy). **P3.REVIEW: 89/100.**

### Phase 4 — Execution polish
B-roll multi-candidate (`BROLL_CANDIDATES=6`) + Haiku-vision re-rank vs cue + a-roll
palette/energy (`_rerank_broll`, top-1 fallback); `/v1/broll/match` own-media vision re-rank.
Punch-in exit easing (symmetric ~8-frame ramp) in TalkingHead + DuetSplit; loop-friendly endings
(assembler trims trailing dead-air ≤10f). **P4.REVIEW: 90/100.**

### Phase 5b — Self-review loop
`_self_review_edl`: preview render → ffmpeg frame sample → Sonnet vision score vs
`review_rubric.md` → one tweak-envelope revision if score < `SELF_REVIEW_THRESHOLD(70)`. Flag
`SELF_REVIEW` (default off), one revision, never on re-renders/tweaks. **P5b.REVIEW: 88/100.**

---

## Evidence index

- **Backend:** 569 tests pass keyless (`.venv/bin/pytest`) — was 479 at baseline (+90).
  New suites: `test_edl_eval`, `test_knowledge`, `test_dossier`, `test_dossier_fusion`,
  `test_reference_patterns`, `test_assemble_edl`, `test_broll_rerank`, `test_self_review`,
  `test_final_gauntlet`.
- **Eval:** `python -m eval.edl_eval` → PASS (5 good, 8 bad); `python -m eval.run_eval` → PASS.
- **Render:** `tsc --noEmit` rc=0; `build:bridge` rc=0; F.3 rendered all 7 compositions at
  1080×1920 (stills inspected).
- **iOS:** `./scripts/dev.sh build` → BUILD SUCCEEDED (Swift compiled on this Mac).
- **Guardrails:** exactly two additive EDL fields across the whole run (`end_frame`, `audio.gain`,
  both Phase 0); WireOp parity intact; manual timeline + conversational tweak untouched.

## Cost-per-clip accounting (vs the $0.25 ceiling)

| Layer | Flag | Δ/clip | Cumulative |
|---|---|---|---|
| Phase 0 (Sonnet EDL author) | none | +$0.04 | ~$0.06 |
| Phase 1 (TL index + Pegasus, 90s take) | `VIDEO_UNDERSTANDING` | +$0.10 | ~$0.16 |
| Phase 2 (KB tokens; reel dossiers cached one-time) | `knowledge_version` | ~+$0.01 | ~$0.17 |
| Phase 3 (Sonnet edit-plan replaces EDL author) | `EDL_AUTHOR` | +$0.04 | ~$0.21 |
| Phase 4 (Haiku b-roll re-rank) | `BROLL_CANDIDATES` | +$0.02 | ~$0.23 |
| Phase 5b (preview vision score) | `SELF_REVIEW` | +$0.04 | **~$0.25** |

At the ceiling with Twelve Labs as the premium understanding engine. All premium layers are
flag-gated OFF by default → the shipping default cost is ~$0.06/clip; each layer flips on only
after the eval scorecard clears it.

## Known gaps / deferred (honest)

- **Live-only paths not exercised keyless** (environmental — no vendor keys in this run):
  real Twelve Labs indexing + Pegasus response, Claude-frames vision, the plan-path live LLM
  edit plan, b-roll vision re-rank, and self-review vision scoring are all **seam-tested with
  canned payloads and proven fail-soft**, but not run against live services. Every one degrades
  to a deterministic keyless path (mock dossier / whole-take assemble / top-1 b-roll / no
  self-review) that IS verified.
- **KB numbers are researched defaults**, not yet A/B-validated against the live scorecard
  (needs keys + a real eval-video bucket).
- **Fixtures are synthetic word-timing takes**, not the plan's 8–12 real source videos — enough
  for the keyless invariant suite (which needs only timings), not for the live scorecard.
- **`EDL_AUTHOR=plan`** diverges from 1 legacy hardening test that monkeypatches the
  `safe_default_edl` fallback the assembler routes around; the underlying warnings
  (`ai_edit_unavailable`, `react_window_dropped`) both still fire on the plan path (verified).
- **P0.6 per-seam music crossfade** was delivered as a composition fade (documented) —
  `useAudioData` is CORS-gated and breaks the render on the catalog's no-CORS tracks.

## Rollout order (all flags default safe)

Ship Phase 0 now (flag-free). Then flip, eval-gated in order:
`VIDEO_UNDERSTANDING=twelvelabs` → `knowledge_version` → `EDL_AUTHOR=plan` →
`BROLL_CANDIDATES=6` → `SELF_REVIEW=1`, running the live scorecard between each.

Requires (not set in this run): `TWELVELABS_KEY`, `TWELVELABS_INDEX_ID`, `PEXELS_KEY`, and a
Supabase `eval` bucket of real takes for the live scorecard.
