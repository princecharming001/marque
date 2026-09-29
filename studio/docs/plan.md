# Yunicorn Studio: agentic editing engine v2 — implementation plan

## 1. Context

Creators film talking-head takes on iPhone (15–90 s, up to 10 min; retakes, fillers, pauses; often HDR/HLG, 60 fps, 9:16). Today a static engine edits each take. The owner wants the engine and the in-app editor rebuilt from scratch as an open-ended, iterative, agentic editor: Claude-first but able to run on a creator's Anthropic, OpenAI, Google or OpenAI-compatible key; optimised only for final video quality; guided by research, not a rule set; built from proven open-source tools, paid only where clearly better; with the static engine kept intact for instant revert. Calibration fact: on a reference take a pro made **1 splice where the old heuristic made 19**. Over-editing is the failure to design against.

## 2. Principles

1. **Code perceives, measures and enforces; models judge and decide.** No model timestamp ever becomes an edit coordinate (VLM temporal localisation tIoU ≤0.11). Edits address word IDs or measured boundaries.
2. **One writer.** The Director owns the Cut Document; everything else proposes or critiques.
3. **Only measured-outcome invariants gate (§10).** Craft numbers are doctrine (priors with evidence tiers); metrics are advice to critics. All numbers live in one `constants.yaml` read by validators and skills.
4. **Iterate to convergence, not to "no defects".** Round limits are runaway guards far above normal use.
5. **Every default is a bet** settled by blind human preference on Yunicorn's own takes. Start with 1–2 components per category; add one only when a bake-off shows a win.

## 3. Pipeline

| Stage | What happens |
|---|---|
| S0 Capture (iOS) | Noise check; Voice Isolation/AirPods HQ; 4K, locked exposure/white balance, Auto-FPS off; teleprompter near lens; optional intent card (goal, CTA, audience, vibe). Upload the untouched original plus the AUAudioMix dialog stem. |
| S1 Ingest | Assert colour tags; honour MOV edit lists and AAC priming; map AAC, never APAC; one rational clock; conform mixed 30/60 fps takes; per-take tone-map plan (Apple Log via Apple's LUT); 48 kHz float WAV; tone-mapped proxy with burned timecode. |
| S2 Perception | Take Index (§5). |
| S3 Voice | Measure, then one voice chain per recording session; room tone from the take's own pauses. |
| S4 Brief | Goal, style (blend or none allowed), hook, beats, length, **pinned CTA/payoff word IDs**, visual/sound plan, justified deviations → per-edit binary rubric + 2–3 hook alternates. At most one creator question. May request a **spoken pickup** of a missing or unrecoverable line. |
| S5 Story cut | Phrase-level comping across takes; a false start only when a later re-delivery exists; structure; ending. Must pass a radio test (audio-only render + transcript). One cut by default; 1 vs 3 divergent cuts is an ablation. No fusion. |
| S6 Fine cut → lock | Seams and seam treatments, pauses, fillers, J/L cuts, speed. Gate: invariants + jury "story works". |
| S7 Finishing | Reframe/punch → b-roll/graphics → captions/text → music/SFX → colour. At most one picture-lock reopen (P0 only); items re-anchor by word ID, music backtiming recomputed in code. |
| S8 Render-and-watch | Champion loop (§4). |
| S9 Master + QC | Composite, audio graph, own mux, encodes, QA (§7). |
| S10 Deliver | Platform files, no-music version, hook alternates, cover frame, SRT sidecar, per-platform edit variants when briefed (e.g. ≥60 s TikTok cut), per-op rationale, trace. |
| S11 Edit | Timeline and chat emit ops on the same document (§13). |

## 4. Agents and the champion loop

| Role | Model | Output |
|---|---|---|
| Director (sole writer) | Blind bake-off winner: `claude-fable-5-1` vs `claude-opus-5-5` vs GPT-6 Astra, at `max` effort (`xhigh` if it wins), set explicitly every call (Opus 5.5 defaults to medium). No cost-driven escalation. | Ops |
| Specialists (b-roll, captions, sound, colour, graphics) | Skills loaded into the Director; fresh-context proposer agents are an ablation arm | Proposals |
| Watcher `watch(clip_ids, question)` | Gemini 3.8 Flash (agentic video) vs 3.1 Pro by bake-off; one tool for perception and review. Paid: only frontier family that watches and hears video | Judgments; code maps them to IDs |
| Frame judge | A family other than the Director's | Rubric results by word ID |
| Helpers (keyterms, name spelling, queries) | The Director's model | Data |

**Tools**: query tools (`get_words`, `get_gaps`, `get_prosody`, `get_visual_events`, `view_frames(range)` with burned frame numbers and word IDs, `crop_zoom`, `redecode`), `watch`, `render_review`, `ask_creator`, `request_pickup`, and **one small strict-schema tool per op family** (cut, overlay, caption, audio, grade). The legacy full-union schema in `edl.py` was rejected as "schema too complex", and chat tweaks silently fell back to canned text. Use `tool_choice: auto` + `strict: true` (forced tool choice returns a 400 on Fable 5.1/Opus 5.5).

**Champion loop**
- Each round reviews the actual platform encode at full resolution plus a burned-ID copy: a Gemini whole-clip pass with audio; Gemini ±1 s clips at max fps around every seam, music hit and caption page change; frame-judge stills (seam pairs, captions under platform UI masks, 1:1 crops); the metrics packet.
- Critics answer localised questions ("At W143→W151, does the head jump?") against the rubric. An issue counts as P0/P1 only if a metric or second critic confirms it; P2 polish continues.
- A revision becomes champion only if it wins position-swapped pairwise **in both orders from ≥2 families** (ties = no change), regresses no metric and passes all invariants. Each round is also compared with the picture-lock version to catch drift.
- Stop after 2 winless rounds confirmed by a fresh jury; runaway guard 12. The champion always ships. End with one continuous 1× watch: "Does anything pull attention from the speaker?"
- Chat edits re-run affected gates, then re-judge the whole video.

**Harness**: Pydantic AI ≥2.51 (MIT) with native Anthropic, OpenAI Responses and Google models; native SDKs as escape hatches; durable steps via its DBOS integration on the existing Postgres. Frames, index and histories live in object storage by content hash, passed by reference; images reach Claude by Files API ID. Not used: LangGraph, LiteLLM (strips signed thinking blocks; March 2026 PyPI compromise), Claude Agent SDK, OpenAI Agents SDK, Temporal (payload/history limits).

**Reasoning state**: append-only provider-native history per role per job; never edit earlier turns (preserved thinking; post-2026-08-31 accounts get a 400); only server-side compaction/context editing, under a contract test; echo Gemini thought signatures and OpenAI reasoning items; alert on Pydantic AI `drop_block`; handle `refusal` with server-side fallbacks (an Opus 5.5 → Opus 5 fallback loses the thinking: log it, treat as fresh context); never ask the model to restate its reasoning (`reasoning_extraction` classifier). Fable 5.1 needs 30-day retention; disclose it. On Claude, load doctrine via the Skills API with code execution and use programmatic tool calling for bulk index queries (non-strict query tools); other providers use the harness's `load_skill`.

## 5. Perception: the Take Index

One rational clock (µs + native frame index), keyed by word ID.

| Layer | Contents | Tools |
|---|---|---|
| L0 Media | fps/VFR, transfer, loudness, clipping, SNR/C50 | ffprobe/colordetect, pyloudnorm, Brouhaha (MIT); SyncNet (MIT) only on VFR/edit-list anomalies, flagging ≥40 ms (its resolution) |
| L1 Words | Verbatim words, fillers, repetitions, cut-offs, events; character times; logprob; retake clusters | ElevenLabs Scribe v2 (paid: Whisper-family filler F1 is 9.4; open CrisperWhisper weights are non-commercial). `no_verbatim` off, character timestamps, `tag_audio_events`, `diarize`, ≤1,000 keyterms. Second voter (MAI-Transcribe-2 or licensed CrisperWhisper 2.0 Pro) only if the bake-off shows misses. |
| L2 Gaps | VAD, RMS-minimum snap point, breaths, room tone, pause class, ≥20 ms stop-closures | Silero VAD v6 (MIT), Parselmouth |
| L3 Prosody | f0, intensity, duration z-scored to the creator's baseline; WPM | Parselmouth (GPL-3.0); WhiStress/emotion2vec+ ablation only |
| L4 Visual (native fps) | Blinks, look-away/reading, face-lost, mouth onset, stillness, face/chin boxes, blur | MediaPipe Face Landmarker (Apache-2.0), signalstats/blurdetect; TalkNet-ASD for multi-speaker, TransNetV2 for multi-shot/b-roll (MIT) |
| L5 Semantics | Director reading + `watch` verdicts | Gemini |
| Seams | Landmark, framing, exposure, WB, room-tone deltas | Code, on demand |

Cut edges come from an acoustic snap inside the gap; disputed slices are re-decoded in isolation. Caption timing: Scribe character times refined by acoustic-onset snap (±80 ms); MFA 3.x (MIT; English model CC-BY-4.0) or Qwen3-ForcedAligner-0.6B (Apache-2.0, multilingual) as fallback; align per utterance; pre-seed name/brand pronunciations. Bake-off set: 30–50 takes with Rev human verbatim and ~200 hand-labelled boundaries, scored on verbatim F1 and cut-edge error (AssemblyAI/xAI only with written consent).

## 6. Cut Document and compiler

- Typed, versioned JSON with an event-sourced op log; defined in Pydantic, generating TypeScript and Swift types.
- Holds sources; A-roll on word-ID ranges (rational times), takes, speed segments; transforms and seam treatments; layouts; overlays (b-roll with job, reason, alternates, licence, conform recipe; template or Lottie graphics); an explicit caption plan (pages, breaks, size, emphasis, anchor); audio (voice chain, crossfades, room-tone fills, music edits with sample-rate automation, SFX on word IDs); colour; deliverable profiles; pinned CTA/payoff IDs; pinned versions; per-item provenance and `creator_pinned`.
- Ops keep legacy `TWEAK_OP_TYPES` names and semantics (`cut_range`, `restore_range`, `set_segment_speed`, `add_punch_in`, `add_broll`, `edit_caption`, `set_music`, `reorder_segments`, `undo`…) re-addressed to word IDs, plus `set_gap`, `choose_take`, `set_layout`, `set_seam_treatment`, `set_grade`, `pin`. One mutator validates every op.
- The compiler keeps output time as a cumulative rational, snaps each edit to the output frame grid once and derives audio sample positions from the same instants (the old pyRound drift class). It emits the FFmpeg footage graph, the Remotion overlay plan, the FFmpeg audio graph, and preview and QA manifests. OTIO export only on request.

## 7. Stack by stage

| Stage | Pick | Licence / why paid |
|---|---|---|
| Footage | FFmpeg 8 + libplacebo builds all footage (A-roll, b-roll, splits, PiP, mattes) in high precision from originals: per-take tone map (operator by blind phone A/B: spline, bt.2390, tuned Hable, pure-HLG; Apple export as reference; DV 8.4 RPU on, `peak_detect=0`, 203-nit white, BT.709, dither); EWA-Lanczos punch/reframe from 4K | LGPL-2.1 (GPL with x264) |
| Colour | FFmpeg colour filters + `lut3d`; OpenColorIO/colour-science; color-matcher on face-mask and neutral regions; face luma vs BT.2408 | BSD-3; GPL-3.0 |
| Reframe/stabilise | MediaPipe boxes + L1-optimal path or 1€ filter; vid.stab only on measured shake | Apache-2.0; LGPL |
| Overlays | Remotion ≥4.0.527 renders captions, text and graphics only, as ProRes 4444 alpha on **self-hosted** Chrome (Lambda's 10 GB disk can't hold 4K60 ProRes at ~13 GB/min); new site; `png`, `bt709`, no audio; `@remotion/captions`, `layout-utils`, `@remotion/lottie` | Source-available, Automators tier |
| Composite | FFmpeg, once; single 8-bit conversion with error-diffusion dither; CAMBI gate | — |
| Voice | Measure first; clean audio gets the chain only. Else no-op vs ClearerVoice MossFormer2_SE_48K vs one paid isolator picked by human listening (ElevenLabs, ai-coustics, Auphonic, Adobe Enhance Speech if API). Generative restorers only below SNR/C50 thresholds, if they win, with a word-level ASR diff. Chain via pedalboard: HPF, measured EQ, de-ess, 3–6 dB compression, leveler, breath attenuation | Apache-2.0; GPL-3.0; paid only if it wins |
| Speed | Rubber Band R3 via CLI `--fine`/librubberband (the FFmpeg filter can't select R3), delay-compensated, sample-exact | GPL-2.0+ |
| Loudness | pyloudnorm + 4× oversampled true-peak limiter; −14 LUFS/−1 dBTP (A/B −16), measured after AAC | MIT |
| Captions | Montserrat, Inter, Anton, TikTok Sans; Noto Color Emoji | OFL |
| B-roll sources | Creator media/pickups → Playwright screenshots → designed cards → Shutterstock → Pexels → stylized stills (GPT Image 2.5, Nano Banana) + Depth Anything V2 Small parallax. Photoreal AI video (Omni Flash, Veo 3.1) deferred, later ≤2 flagged shots | Apache-2.0; Shutterstock paid (vertical, 4K, per-asset licence); Pexels credit, no persistent index |
| B-roll ranking/conform | 5–10 queries → 50–200 candidates → Qwen3-VL-Embedding-8B top-20 → UVQ + blur/blockdetect → sampling every 0.25–0.5 s of the used range (OCR, logo, face hard gates; subject kept inside the 9:16 crop, else split/PiP) → conform (frame repeat for cadence; RIFE gated with warp check; exposure/WB/contrast match, MKL ≤0.5) → Director threshold judgment → Gemini watches the insert composited in the edit | Apache-2.0; RIFE MIT |
| Music/SFX | Epidemic Sound API + MCP (Safelisting, Beats, Versions, stems, SFX); per-destination safelist check, else ElevenLabs Music (`composition_plan`, instrumental) or a no-music master | Paid: only source with sublicensing + Content ID safelisting |
| Encode/mux | x264 High 8-bit 4:2:0 BT.709, `veryslow`, CRF 14–16, closed GOP `keyint=fps/2`, `bf=2`, `+faststart`, `maxrate=min(25 Mbps, 300 MB÷duration)`; HEVC for long takes; own mux `-use_editlist 0`, priming pre-trimmed; native `aac` ≥256 kbps | GPL-2.0 |
| Eval | Inspect AI, VMAF/CAMBI, UVQ, pystoi, UTMOSv2, ECAPA, VERSA | MIT/BSD/Apache |

**QA on every file**: tags, CFR, no edit list, size cap; loudness, true and inter-sample peaks, mono fold; click detector ±5 ms at each seam; spectral rolloff and 5–9 kHz sibilance vs original; ducking gain-reduction rate; ASR round-trip; render audio vs compiled graph (sample-exact); dropped/duplicate frames; CAMBI; VMAF vs master and a simulated platform re-encode; caption placement vs per-frame landmarks after transforms; safe zones; ESTOI/WER through the mix and a phone-speaker simulation.

**Director-owned finishing choices**: seam treatment (jump cut, J/L cut, cutaway, ≥15–20% punch, face-scale normalisation at take joins, A/B-gated RIFE morph cut); caption placement per section with hysteresis, highlights leading speech 2–4 frames, verbatim text; b-roll with a named job, "none" always valid, memes only creator-supplied (GIPHY/KLIPY terms exclude server renders); music fitted after the speech cut, never moving a cut to a beat. Off by default but eval-gated creator opt-ins, not bans: eye-contact correction, subtle skin smoothing, speaker upscaling, grain, consented voice patching of a flubbed word. Graphics v1 = templates + Lottie; sandboxed agent-authored graphics later. HDR delivery: phone-viewed HDR-vs-SDR bake-off in P3.

## 8. Providers and bring-your-own-key

| Role | Creator key? | Default |
|---|---|---|
| Director + finishing (all editing decisions) | Anthropic, OpenAI, Google or OpenAI-compatible | House Claude |
| Watcher | Only in opt-in "all on my account" mode with a Google key | House Gemini |
| Frame judge, perception | No | House |

- Say it plainly: a creator key runs the editing decisions. The house jury and perception stay on house keys, so BYOK can't lower critique and the house gate still applies. The consent screen names every provider (App Store 5.1.2(i)).
- Certified = contract tests + golden run ≥45% vs incumbent. OpenRouter models are certified one by one; others run labelled "uncertified", gated by the house jury. If a BYOK run can't converge, offer a house re-run; never switch billing silently.
- Google: Vertex credentials or billing-enabled AI Studio keys; reject free-tier keys (they train on data; EEA/UK/CH need paid). Legal sign-off on consumer and 18+ terms is a P4 exit item.
- Fallbacks: no video input → burned-ID frame strips + L3/L4 text; no strict schemas → Pydantic validation with readable retries; small context → query tools + compaction.
- Keys: iOS Keychain (`…AfterFirstUnlockThisDeviceOnly`; move today's UserDefaults tokens too); per-request header over TLS; worker memory only, with a KMS-envelope TTL blob for restarts; never in step inputs, queues, logs, Sentry or OTel. House Anthropic key via Workload Identity Federation. Validate with list-models + probe; map 400/401/402/403/413/429 to clear messages; OpenRouter via OAuth PKCE. **Backend auth + App Attest ship before any key is accepted.**

## 9. Doctrine pack and creator shaping

- Agent Skills format: ~100-token metadata always loaded; `SKILL.md` <5k tokens; topic files one level deep.
- Files: `SKILL.md` (directives, evidence legend, stage order, style picker); `story-and-hook`, `cutting-and-pacing`, `speed`, `framing-and-zooms`, `broll`, `broll-sourcing`, `captions-and-text`, `music`, `sfx`, `voice-and-loudness`, `color-and-look`, `transitions-and-graphics`, `endings-loops-ctas`, `platforms`, `multi-speaker`, `non-english`, `critique`; nine `styles/` (educational, storytime, comedy, hot take, listicle, tutorial, podcast, sales/UGC, founder; blends allowed); `examples/` (incl. 1-vs-19); `evidence.md` (tier, n, domain, re-check date). Signatures (Hormozi, Abdaal, DOAC; n=3–4) later, opt-in.
- Each file: principles with reasons; ranges tagged [L] lab, [A] ads/platform, [V] vendor, [I] internal, [X] inference; judgment calls; when to break; a worked example; critic questions.
- Directives: story before polish; every addition has a job; cut where a thought ends; voice before visuals; match measured energy, never fake it with speed; protect what only this creator could say; resolve ambiguity with variants. "Lean raw" and "no music" are open bake-off questions, not defaults.
- People author it from verified research, correcting kb-2026.11 first (tempo structure replaces "CV<0.3"; direct-address greetings and deliberate repetition kept). Automation drafts PRs; an editor approves (human-written skills beat judge-evolved, 0.620 vs 0.582).
- Creator Profile: style dials, signature (catchphrases, flubs to keep), brand, measured baselines; it is the source of variation across creators. Memory (CIPHER-style, after the core cut wins): diff agent vs final document and chat → editable preference statements → ~5 per brief. Memory overrides doctrine, never invariants. An override-rate dashboard flags principles for review; promotion needs a human rewrite, a blind pairwise win and no golden regression. Style cards from reference videos: P5 experiment.

## 10. Invariants: the only hard gates

1. No audible click or clipped phoneme at any seam (click detector + ASR round-trip).
2. Render audio matches the compiled graph sample-exactly; sync fixtures pass.
3. No model timestamp used as an edit coordinate.
4. Pinned CTA/payoff words present unless the creator unpinned them.
5. Licence record for every asset; per-destination safelist for catalogue music.
6. HDR tone-mapped exactly once.
7. Text inside the chosen platform's safe zone, off eyes and mouth, after transforms.
8. Selected loudness target; true peak ≤ −1 dBTP after encode.
9. No digital silence under speech.
10. Delivery format checks.

Everything else (150 ms gap rule, speed caps, seams/min, pause medians, reuse) is doctrine. Cuts at ≥20 ms stop-closures are allowed with 5–10 ms crossfades when invariant 1 passes.

## 11. Evaluation

- **Primary**: blind pairwise preference on phones (trained raters + creators), Bradley–Terry/mixed effects with clip and rater effects; ~200 pairs detect 60/40, ~800 detect 55/45. A Claude-only judge never scores Claude.
- **Golden set**: 200 takes stratified by content, audio, HDR/60 fps, length, language, speakers; 40 cuts by 2–3 hired pros.
- **Listening panel** (phone speaker + earbuds) for voice/music bake-offs and a weekly sample; Gemini audio is 16 kbps mono and MOS predictors run at 16 kHz.
- **Metrics packet** (advice): port `edit_lint.py`, `eval/invariants.py`, `cut_qc.py`, `audio_qc.py`, `layout_qc.py`, `pro_cut_reference.py`, `golden.py`/`corpus.json` to word IDs; add seams/min, pause median, cuts inside clauses, tempo structure, static stretches, time to first speech, final-word-to-end, b-roll wrong-subject rate, caption WER, ESTOI, VMAF, UVQ, CAMBI. Rubric grader only after acceptable Cohen's κ and fail-class recall.
- **Bake-offs**: ASR, voice, tone map, Director, Watcher, captions, −14 vs −16 LUFS, music per style, stock hit rate, HDR. **Ablations**: N story cuts, specialist agents, Gemini vs frames-only critic, each skill.
- **Field (consented)**: accept-without-edit, edit distance; Instagram `reels_skip_rate` + Trial Reels; YouTube `engagedViews`, `audienceWatchRatio`; vs the creator's median.
- **Model adoption**: Inspect AI certification per role (schema, tools, thinking round-trip, no history edits, refusal, image-in-tool-result) + golden run. New default needs CI lower bound ≥50% plus shadow. Jobs stamped with model, doctrine and pipeline versions.

## 12. Keeping the static engine

1. **Freeze four artifacts**: tag `static-engine-v1` at `1c07c18`; pin the Render image by digest; leave `marque-render` and Lambda 4.0.484 untouched; archive iOS build 91 with ProEditorView.
2. **Isolate Studio**: separate service and image, own Remotion site, tables and buckets; static schema frozen.
3. **Route once at create**: `job.engine ∈ {static, studio}` in the `main.py` job dict (~3894), persisted by `_persist_clip_job`. Order: kill switch → app capability (older builds always static) → allowlist (`STRATEGY_ALLOWLIST` pattern) → percentage ramp.
4. **Static unchanged**: spawn sites (~3970, ~3985, 4142, 4194–4203), EDL, `/tweak`, `/retheme`, `/suggested-edits`, `AnalyzeJobResponse` polling. Studio uses `/v2` (jobs, ops, chat, progress); legacy endpoints return a 409 for studio jobs.
5. **iOS**: ProEditorView stays in every future build (native Swift can't be OTA'd).
6. **Shadow** via `_log_shadow_diff` for consented creators. **Fallback**: on crash or invariant failure, run static, tell the creator, offer the Studio champion alongside.
7. **Revert** = one server flag; drilled end to end in P0 and every phase.

## 13. In-app editor

SwiftUI timeline plus transcript editing (tap words to cut, drag beats, tap a gap). Gestures become ops, applied locally and validated by the server. Preview: AVFoundation proxy composition for cuts, speed (AVAudioUnitTimePitch; GPL can't ship in-app) and audio, plus a server-rendered alpha overlay for changed regions from the same Remotion code; captions drawn natively from the caption plan while a region re-renders. Parity tested on geometry and timing (±1 frame). Chat: one-line interpretation, ops, whole-video re-judge, one-tap undo, 2 variants for ambiguous requests, pinned items untouched unless asked.

## 14. Reuse and non-goals

- **Reuse**: eval checks and golden corpus; `TWEAK_OP_TYPES` semantics and single mutator; `seam_declick_filter` and loudness helpers (`app/audio.py`); routing and shadow patterns. Old Captions/TextCardOverlay/Watermark ported only if they beat a fresh build.
- **Don't port**: EndCard (contradicts "end ≤0.5 s after payoff"), Grade, AudioMix, PunchZoom, BrollLayer, the nine compositions, SoundHelix/codeskulptor beds, unlicensed SFX, Matter font, GIPHY/KLIPY/Tenor paths.
- **Build only**: Take Index and tools, acoustic snapper, crop smoother, Cut Document, mutator, compiler, caption placer, gate orchestration, champion loop, skills, v2 API, iOS editor.
- **Never build**: ASR, aligner, VAD, beat tracker, stabiliser, tone-mapper, renderer, music library, stock search, vector DB, fine-tuned models (v1), agent framework, FCPXML.
- **Licence-excluded**: NISQA, DOVER, MatAnyone, InsightFace, MusicGen, MMAudio, madmom models, Kling, Sora, Qwen-Image-2.1, FLUX.2 [dev]/klein 9B, MiniMax H3, HunyuanVideo, AGPL whisper-timestamped, MMS weights, Depth Anything V2 Base/Large, Freesound, Brandfetch free API. GPL tools stay server-side.

## 15. Phases and verification

| Phase | Scope | Exit criteria |
|---|---|---|
| P0 Foundations | Freeze artifacts; Studio service; routing, kill switch, fallback; auth + App Attest; licence audit; eval port; phone rating app; golden set; 40 pro cuts; sync fixtures | Revert drill passes; 200 takes; raters calibrated |
| P1 Perception + audio | Ingest, Take Index, ASR and voice bake-offs, audio graph, mux, compiler core | ASR chosen on F1 and cut-edge error; voice chain beats no-op for listeners with WER Δ≤1; fixtures sample-exact; tone map chosen by blind A/B |
| P2 Cut core | Cut Document, op tools, Director bake-off, S4–S6, champion loop, captions, encodes, QA | ≥60% blind win vs static (n≈200); zero invariant violations |
| P3 Finishing | Reframe, seam treatments, b-roll, graphics, music/SFX, colour, HDR bake-off | Each ablation non-negative; b-roll wrong-subject <5%; ≥65% vs static; ≥50% vs pros tracked as target |
| P4 Editor + BYOK | iOS editor, chat, memory, certification for all four provider types, Google legal, security review | Timing parity ±1 frame; chat intent ≥90%; Claude, GPT, Gemini certified |
| P5 Rollout | Shadow → allowlist → ramp; Trial Reels; deferred items by bake-off | Field metrics ≥ static on creator medians; fallback <2%; accept-without-edit ≥ static |

**Verification**
- **Per commit**: mutator/invariant and rational-time property tests, plus provider contract tests.
- **Per release**: beep-and-flash fixtures per iPhone mode and a 10-min 59.94 fps fixture with speed segments and 30 cuts; colour-chart ΔE; golden regression; revert drill.
- **Per phase**: powered blind phone studies.
- **In production**: per-job QA manifests, weekly human samples, override dashboard, fallback rate.

## 16. Key risks

| Risk | Mitigation |
|---|---|
| Over-editing | Restraint doctrine, pro calibration |
| Critic noise and self-preference | Two-family both-order wins; humans arbitrate |
| Perception errors | Acoustic snap, ASR round-trip |
| Audio defects models can't hear | Full-band checks, listening panel |
| Sync drift | Rational compiler, fixtures |
| Banding or double tone map | FFmpeg owns footage; CAMBI |
| Mismatched takes at seams | Seam deltas, normalisation |
| Licensing | Ledger, safelist checks |
| Provider churn | Pinned IDs, contract tests |
| BYOK security and App Store review | Auth first, Keychain, IAP parity |
| Static contamination | Isolation, drills |
| Preview drift | Server-rendered overlays |

## 17. Key sources

Newman & Schwarz 2018; Sundararajan & Adesope 2020; Cutting 2010; Magliano & Zacks 2011; Nakano 2009; Fraundorf & Watson; Li 2026 (captions, n=211); Lang 1999; VEBench; NumPro; VQQA; VideoArgus; PoLL; CIPHER; VideoWeaver; EditDuet; ICASSP 2026 URGENT; HUG-VIS; ITU-R BT.1359/BT.2408; AES TD1008; Scribe v2 verbatim benchmark; Epidemic Partner API; Remotion #11331 and Lambda disk docs; FFmpeg rubberband docs; Anthropic migration notes.

**Critical files** (in `/Users/home/Marque-wt/editor-audit/`):
- `backend/main.py`: job create 3852/3894, spawn sites, shadow diff 6124/6148, render bridge 8193–8355.
- `backend/app/edl.py`: `TWEAK_OP_TYPES` 1287.
- `backend/app/edit_lint.py`, `backend/app/audio.py`, `backend/eval/`, `backend/knowledge/`.
- `render/src/types.ts`.
- `ios/Marque/Adapters/LiveClipEngine.swift`, `ios/Marque/Adapters/BackendClient.swift`.

## 18. Research trail

- Verified research tracks (10 web, each fact-checked) and the codebase map: workflow run `wf_bbb9e6ca-a5d` journal.
- Design inputs kept alongside this plan in `reports/Yunicorn Studio design inputs/`: architecture, doctrine outline, toolchain, and three critiques.
- Earlier report: `reports/Agentic video editing engine.md`.
