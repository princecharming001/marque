# Make Marque's AI Editor One of the Best on the Market

## Context

The AI auto-edit underdelivers. Deep pipeline traces found three root causes:
1. **The AI edits blind** — transcript-only; the prompt says "you cannot see the video" (`backend/prompts.py:534`). No framing, energy, faces, or visual awareness in any edit decision.
2. **Wiring bugs squander existing intelligence** — the brief's `broll_moments`/`punch_in_moments` are computed then discarded (`prompts.py:458-475`); b-roll/punch-in toggles written but never read (`main.py:2063` vs `_apply_edit_prefs` `main.py:1898`); transcript truncated to 200 words (`prompts.py:489`); the actual cut is authored by Haiku at temp 1.0 without structured outputs (`main.py:2988`).
3. **The render pipeline degrades quality mechanically** — 540–720p upload upscaled to 1080p (soft faces, b-roll sharper than speaker), JPEG q80 intermediate frames (caption halos), sub-second sliver cuts, FastCuts strobe, fake green-screen multiply blend, no loudness normalization, jittery caption reflow.

Goal: comprehensive video understanding, nuanced judgment grounded in professional editing craft, proper execution. **User decisions:** Twelve Labs as the understanding engine (latency accepted with eyes open: +1–3 min indexing per edit); ~$0.25/clip AI cost ceiling; "research" = curated knowledge base + runtime reference-reel analysis. Whole-pipeline replacement APIs (OpusClip/Vizard) rejected — they'd eat the compressed upload, return opaque files, and destroy the EDL/tweak/timeline moat.

**Target architecture:** understand (Twelve Labs dossier + AssemblyAI words) → judge (Sonnet temp-0 structured calls steered by a versioned editing-craft knowledge base) → assemble (LLM decides, code assembles the EDL) → render (fixed Remotion pipeline) → self-review (score against rubric, ≤1 revision). EDL stays the single source of truth; manual timeline + conversational tweak untouched.

---

## Phase 0 — Render + wiring fixes (~1 week, parallelizable, no new vendors)

### 0.1 Upload quality ladder — `ios/Marque/Adapters/LiveClipEngine.swift:152-197`
Replace the 720p/540p preset ladder with duration-driven HEVC bitrate targeting under the 48MB cap: ≤90s → 1080p @ ~3.8 Mbps; 90–150s → 1080p @ ~2.6 Mbps; >150s → 720p ladder as today. Use `AVAssetWriter` with explicit `AVVideoAverageBitRateKey` (budget_bps = 48e6×8/duration − ~96kbps audio). Keep retry ladder as safety net. Make `maxUploadBytes` server-driven via a capabilities response so raising Supabase tier later is backend-only. **Biggest single visible win — eliminates the upscale blur on faces.**

### 0.2 Render encoding — `render/src/lambda-render.ts:34-45` + `render/remotion.config.ts`
Non-preview renders: `crf: 17`, `jpegQuality: 95` (env-tunable `REMOTION_IMAGE_FORMAT`/`REMOTION_JPEG_QUALITY`). Kills caption halos at ~10% Lambda cost.

### 0.3 Min-clip guard — `backend/app/edl.py` (~line 399 kept-interval computation)
Drop any kept interval < 12 frames (400ms) by merging into the adjacent drop (unless it's the only interval); fixes the `max(1, round(...))` 1-frame clips at line 406. Mirror in `ios/.../LocalEDLEngine.swift` for preview parity. Golden tests.

### 0.4 FastCuts strobe — `render/src/compositions/FastCuts.tsx:23-40`
Flash → opacity 0.10 fading over 3 frames, rate-limited to boundaries ≥45 output frames since the last flash (filler micro-cuts get none).

### 0.5 GreenScreen — `render/src/compositions/GreenScreen.tsx:31-33`
Delete `mixBlendMode: "multiply"`. Redesign as speaker in a rounded card (bottom 55%, cover, drop shadow) over the reference backdrop. No blend modes. (True segmentation keying = later option, don't block.)

### 0.6 Loudness + music seam — new `backend/app/audio.py`, `render/src/components/AudioMix.tsx`
Add ffmpeg to backend `Dockerfile`. `probe_loudness(url)` via `loudnorm=print_format=json` run in `_run_analysis` parallel with transcription. In `_run_edit`, `gain_db = clamp(lufs_target − integrated_lufs, ±12)` → new optional `audio.gain` field (`app/edl.py:160`, `render/src/types.ts`); `CutVideo.tsx` applies `10^(gain/20)`. AudioMix: replace `loop` with staggered self-crossfading Sequences (30-frame equal-power seam, fade in/out); smooth the duck step over ±8 frames.

### 0.7 Captions — `backend/app/edl.py` `CaptionWord` + `render/src/components/Captions.tsx`
Add optional `end_frame` (from AssemblyAI end_ms) everywhere captions are built/remapped. Hide block after last word `end_frame`+12 and during >30-frame silences. Default grouping → `phrase` (stable 3-word chunks, already implemented); rewrite `line` mode to precomputed stable breaks instead of the per-frame sliding window (`Captions.tsx:145`). Mirror default in `LocalEDLEngine.swift`.

### 0.8 Temperature grade — `render/src/components/CutVideo.tsx:58-59` + `Grade.tsx`
Replace sepia/hue-rotate with SVG `feColorMatrix` warm/cool filters (R/B channel gain scaled by intensity), composed into `lookFilterCSS`.

### 0.9 AI wiring — `backend/main.py`, `backend/prompts.py`
- Pass `broll_moments`/`punch_in_moments` into `edl_prompt`'s brief block, frame-anchored.
- Wire toggles in `_apply_edit_prefs` (`main.py:1898`): `broll=False` → strip broll; `punch_ins=False` → strip punch-ins; `punch_ins=True` + none → synthesize from top emphasis span. Add matching prompt hints in `_run_edit`.
- Kill `[:200]`: use existing `_frame_anchored_transcript` (`prompts.py:499`) — full-length, pre-computed frame math.
- EDL call (`main.py:2988`): → `anthropic_json(..., EDL_JSON_SCHEMA, SONNET, temperature=0.0)`. Define `EDL_JSON_SCHEMA` in prompts.py mirroring the inline schema block (pattern: `EDIT_BRIEF_SCHEMA`). Interim until Phase 3 replaces raw-EDL authoring.

**Phase 0 cost delta:** +~$0.04/clip. Ship flag-free.

---

## Phase 5a — Eval harness FIRST (~4 days; gates everything after)

`backend/eval/edl_eval.py` + `eval/edit_golden.py`, mirroring the existing `eval/run_eval.py` pattern (thresholds, known-good/known-bad tripwires, keyless CI mode):
- **Fixture set**: 8–12 real takes (word-timing JSON checked in; source URLs in a Supabase `eval` bucket) covering scripted / rambling / listicle / low-energy / buried-hook.
- **Keyless invariants**: brief→plan→assembler with mocks; assert no slivers, hook ≤90 output frames in, caption coverage == kept words, b-roll grammar, drops ⊆ take.
- **Live scorecard**: full stack per fixture, independent LLM judge scores against the KB rubric; report hook-time / kept-ratio / cut-cadence / judge score per `knowledge_version` + prompt version. Regression thresholds gate deploy (pattern: `MIN_GATE_PASS_RATE`).

---

## Phase 1 — Understanding layer: Twelve Labs Video Dossier (~1.5 weeks)

### Adapter — new `backend/app/dossier.py`
`async generate_dossier(source_url, duration_ms) -> dict | None` with provider chain **`twelvelabs → claude_frames → none`** (env `VIDEO_UNDERSTANDING=twelvelabs|claude_frames|off`, auto fail-down per existing fail-soft doctrine).

**Twelve Labs path**: one app-level index (create-on-first-use, id cached in env/DB); upload take as an indexing task; poll until ready; one **Pegasus 1.2** generate call with a JSON-schema prompt for the dossier; optionally store the Marengo embedding id for later library search. Env keys: `TWELVELABS_KEY`, `TWELVELABS_INDEX_ID` (convention: `ASSEMBLYAI_KEY` at `main.py:1737`).
**Latency handling (user accepted 1–3 min)**: kick off TL indexing *immediately after upload completes* (before/parallel with `/v1/clips`), run concurrently with AssemblyAI via `asyncio.gather`; surface staged progress ("watching your take…") through the existing job-status polling so the wait reads as intelligence, not lag. If TL exceeds `DOSSIER_TIMEOUT_S` (default 240), fail down to `claude_frames` (ffmpeg 0.5fps keyframes + first frame full-res → one Sonnet/Haiku vision call reusing the `media_analyze_prompt` shape, `main.py:4154-4166`).
All timestamps converted to `[fN]` frame anchors via `ms_to_frame` — same convention as the transcript.

### Dossier schema v1 (stored `job["dossier"]`, persisted via `_persist_clip_job`)
`{version, provider, first_frame{desc, pattern_interrupt, score}, delivery_curve[{f0,f1,energy,note}], visual_events[{f0,f1,kind,desc}] (gesture|prop|demo|framing_change|glance_away|flub_visual), scenes[], on_screen_text[], framing{shot,eye_contact,headroom_ok,stability,lighting,quality_flags}, broll_visual_opportunities[{f0,f1,cue,why}], gaffes[]}`

### Fusion
`edit_brief_prompt` gains `dossier` param: replace the "you cannot see the video" clause with "visual facts come ONLY from the VIDEO DOSSIER — never invent beyond it"; append compact dossier block. Grounding discipline (verbatim quotes, [fN], absence-is-valid) extends to visual claims.

**Cost:** ~$0.10/clip (index $0.063 + Pegasus ~$0.04 for a 90s take). Flag default `off` until the eval scorecard clears.

---

## Phase 2 — Editing knowledge layer (~1 week; KB writing parallelizes with Phase 1)

### `backend/knowledge/` — versioned craft KB (operational rules with numbers, not vibes)
- `retention.md` — 0–3s win conditions (pattern interrupt, first-frame design, hook promise; hooks in first second ≈ +41% retention, motion in first frame ≈ +35% watch); condensed AIDA (0–2s attention / 2–8s interest / 8–20s desire / last 3–5s action); never static >3s; loop-friendly endings.
- `hooks.md` — five-layer hook (visual+text overlay+caption+voiceover+audio); taxonomy mapped to existing `SIGNAL_LIST`; when to pull a buried hook forward.
- `pacing.md` — cut-cadence per video_type (entertainment 1–2s, education 2–4s); energy-matched cadence keyed off the dossier `delivery_curve`; dead-air budgets; beat-timed cuts (+23% completion).
- `broll.md` — cut on concrete nouns/actions; J-cut lead ~12 frames; 2–3s holds; ≥3s spacing; protect hook/CTA face time; match a-roll palette/energy.
- `captions.md` — 3-word phrase grouping; safe zones; emphasis highlighting; 55–75pt @1080×1920; high contrast.
- `audio.md` — −14 LUFS, music-under-voice ratios, duck curves, seam rules.
- `review_rubric.md` — the self-review scoring rubric (Phase 5b).
- `MANIFEST.json` — `{"version": "kb-2026.07", files}` stamped into `job["knowledge_version"]` + eval scorecard → KB changes A/B-able and revertible like prompt changes.

### Loader — new `backend/app/knowledge.py`
`digest(style, video_type, call) -> str` (~600–1000 tokens): per-style core rules (successor of `EDIT_RUBRICS` `prompts.py:270`, which becomes a thin KB wrapper) + pacing row + call-relevant domains (brief: retention+hooks; edit-plan: pacing+broll+captions; review: rubric). Rule: craft numbers live ONLY in the KB.

### Runtime trend research
Extend `_reference_reel_block` (`prompts.py:77`): when the Apify reference reel has a playable URL, run it through the same dossier adapter (cached per reel URL; TL indexing latency fine here — it's async/background) → `reference_patterns`: measured cut density, caption style, overlay usage, hook layer construction, energy curve. Feed structured block to brief + edit-plan ("match these measured patterns"). Store `job["reference_reel"]["patterns"]`.

---

## Phase 3 — Judgment layer: "LLM decides, code assembles" (~2 weeks)

| Call | Target |
|---|---|
| Brief | Sonnet, temp 0, structured outputs, + dossier + KB digest + full transcript |
| **Edit Plan** (replaces raw-EDL authoring) | Sonnet, temp 0, SO over `EDIT_PLAN_JSON_SCHEMA` — typed decisions only |
| Verify | deterministic `check_edl_invariants()` in code (the checklist in `edl_verify_prompt` `prompts.py:572-586` as code); LLM pass only for reorder coherence |
| Repair | Sonnet, only for semantic-pass failures (assembler can't emit invalid EDLs) |
| Tweak | unchanged — it's the precedent (`TWEAK_ENVELOPE_JSON_SCHEMA` `prompts.py:625`) |

**`EDIT_PLAN_JSON_SCHEMA`** (new, prompts.py): `{open_on{start,end,why}, keeps[], cuts[{range,reason,quote}], order[], punch_ins[{frame,scale,why}], broll[{range,cue,query,source}], caption_plan{style,grouping,highlight_words}, text_cards[], music{wanted,vibe}, pacing_intent}`. Every citable frame must appear in its inputs (anchored transcript, brief, dossier, disfluency/emphasis spans).

**`assemble_edl()`** (new, `backend/app/edl.py`) — pure function `(plan, words, style, prefs, brief) -> EDL`:
- captions ALWAYS derived from `_clean_words` (LLMs never author caption arrays again);
- deterministic filler/dead-air drops win (unchanged `_merge_drops` semantics);
- b-roll grammar enforced in code (J-cut lead, holds, spacing, hook/CTA protection — currently only *begged for* in the rubric `prompts.py:304-313`);
- cut boundaries snap to word boundaries ±3 frames (no mid-word cuts);
- min-clip guard, clamps, layout synthesis, `speech_frames` regeneration (`main.py:3067` behavior kept).

Rollout: `EDL_AUTHOR=plan|legacy` flag, evaled head-to-head via Phase 5a. Safe-default EDL fallback unchanged.

---

## Phase 4 — Execution part 2 (~4 days)

- **B-roll multi-candidate + vision re-rank**: `_fetch_pexels` (`main.py:4226`) → `per_page: 6` (env `BROLL_CANDIDATES`); new `_rerank_broll(cue, candidates, dossier)` — one Haiku vision call per slot (thumbnails as image blocks) scoring cue-match + a-roll palette/energy match; cache; fall back to top-1. Upgrade `/v1/broll/match` (`main.py:4182`) from text-only tie-break to the same vision re-rank for own-media.
- **Punch-in release easing** (`TalkingHead.tsx`/`DuetSplit.tsx`): mirror the 8-frame ramp on exit (entry already eases; exit is a snap).
- **Loop-friendly endings**: assembler trims trailing dead-air ≤10 frames; optional last-caption hold.

---

## Phase 5b — Self-review loop (~4 days; flag `SELF_REVIEW`)

In `_render_all_clips`: render the existing preview variant first (`preview="1"`, half-scale — `lambda-render.ts:44`) → score it with **Claude vision on sampled frames + the render plan** (NOT Twelve Labs here — indexing the preview would add another 1–3 min + cost; frames are enough to check sync/levels/flashes) against `knowledge/review_rubric.md`: hook lands 0–3s, caption sync ±3 frames, no jarring jump-cuts, b-roll relevance, audio levels, no black/flash frames. Output `{score_0_100, issues[{code, frame, fix_op}]}` with `fix_op` drawn from the tweak-envelope op set, applied via existing `apply_edl_ops`, then one final render. Hard limits: one revision max, only if score < `SELF_REVIEW_THRESHOLD` (70), never on re-renders/tweaks. +~60–120s latency, +~$0.04.

---

## Rollout, cost, dependencies

| Order | Phase | Effort | Flags | Cumulative AI cost/clip |
|---|---|---|---|---|
| 1 | 0 render+wiring | ~1 wk | none | ~$0.06 |
| 2 | 5a eval harness | ~4 d | — | — |
| 3 | 1 TL dossier | ~1.5 wk | `VIDEO_UNDERSTANDING` | ~$0.16 |
| 4 | 2 knowledge base | ~1 wk (∥ Phase 1) | `knowledge_version` | ~$0.17 |
| 5 | 3 edit plan + assembler | ~2 wk | `EDL_AUTHOR` | ~$0.21 |
| 6 | 4 b-roll re-rank + polish | ~4 d | `BROLL_CANDIDATES` | ~$0.23 |
| 7 | 5b self-review | ~4 d | `SELF_REVIEW` | ~$0.25 |

At ceiling with TL as engine (TL is the premium choice — Gemini Flash alternative would land ~$0.17 total). If squeeze needed: reference-reel dossiers are cached one-time costs; self-review flag per-tier.

**New env keys**: `TWELVELABS_KEY`, `TWELVELABS_INDEX_ID`, `VIDEO_UNDERSTANDING`, `DOSSIER_TIMEOUT_S`, `BROLL_CANDIDATES`, `EDL_AUTHOR`, `SELF_REVIEW`, `SELF_REVIEW_THRESHOLD`, `REMOTION_IMAGE_FORMAT`, `REMOTION_JPEG_QUALITY`.
**New files**: `backend/app/dossier.py`, `backend/app/knowledge.py`, `backend/app/audio.py`, `backend/knowledge/*.md`+`MANIFEST.json`, `backend/eval/edl_eval.py`, `backend/eval/edit_golden.py`.
**Reused**: `anthropic_json` structured-outputs helper, `_frame_anchored_transcript` (`prompts.py:499`), `media_analyze_prompt` vision shape (`prompts.py:1533`), `apply_edl_ops`, tweak-envelope pattern, existing eval harness pattern, existing fail-soft/mock degradation.
**Untouched**: manual timeline, conversational tweak, WireOp parity (all changes land behind the EDL contract; only additive optional fields `end_frame`, `audio.gain`).

## Verification

1. **Unit/golden**: `cd backend && make test` — new tests for min-clip guard, assembler invariants, caption end_frame round-trip, toggle wiring, loudness gain math; EDL golden files.
2. **Keyless eval (CI)**: `python backend/eval/edl_eval.py --keyless` — invariant suite over fixtures; known-bad EDLs must be caught.
3. **Live eval**: `python backend/eval/edl_eval.py --live` with keys — scorecard must beat the pre-change baseline (run baseline BEFORE Phase 1+ flips on).
4. **Render smoke**: `cd render && npx remotion still` / preview render of one fixture EDL per composition; visually check captions (no halos, stable grouping), FastCuts (no strobe), GreenScreen card layout, loudness.
5. **End-to-end**: point the app at dev backend, record a 60–90s take, run analyze→confirm with `VIDEO_UNDERSTANDING=twelvelabs`, verify dossier in job payload, edit references visual moments, final render sharp at 1080p, self-review triggers on a deliberately bad take.
6. **iOS parity**: LocalEDLEngine preview matches backend plan for min-clip + caption defaults (existing parity tests pattern).
