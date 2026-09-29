# Yunicorn "Studio" editing engine v2: architecture plan

## 0. Design stance

- **Code perceives, measures and enforces. Models judge and decide.** No timestamp from an LLM or VLM (Gemini, Claude, Qwen, Twelve Labs) ever becomes an edit coordinate. Every edit is addressed by word ID or by a measured boundary.
- **One writer.** A single Director agent owns the cut document. Specialists only propose, and critics only critique (Cognition 2026, GLANCE, EditDuet).
- **Craft is loaded as guidance, with evidence tiers.** Only true invariants become validators. The pro cut used 1 splice where the old heuristic made 19, so rules must not drive edits.
- **Extra compute goes to four places:** planning; divergent candidate cuts; fresh-context critics from several model families; and render-and-watch rounds. A new version must beat the current best to be kept.
- **Every default below is a starting bet.** It becomes the default only after it wins on Yunicorn's own takes (§8).

---

## 1. End-to-end flow (stages)

| Stage | What happens | Key tools (why) |
|---|---|---|
| **S0 Capture (iOS)** | Pre-record noise check. Offer AirPods high-quality recording, AVInputPickerInteraction, and the system mic-mode sheet (Voice Isolation). Capture 4K (for punch-in headroom) with locked exposure/white balance, Auto-FPS off, and a teleprompter near the lens. Upload the original untouched, plus the AUAudioMix dialog stem for Spatial Audio recordings. | Capture is the largest quality lever. Degraded audio causally lowers credibility (Newman & Schwarz). |
| **S1 Ingest / conform** | Assert color tags with ffprobe/colordetect. Map the AAC stream explicitly (never APAC). Put everything on one media clock at the source's constant frame rate. Tone-map Dolby Vision 8.4/HLG **once** with FFmpeg 8 libplacebo (RPU on, `peak_detect=0`, explicit BT.709 output, dithering) into a 10-bit ProRes 422HQ BT.709 mezzanine at native resolution. Export 48 kHz float WAV. Measure and correct the audio/video offset with SyncNet. Make a proxy with burned-in timecode. | A controlled tone map, instead of Remotion's automatic zscale conversion. The operator is chosen by A/B (spline, bt.2390, tuned Hable, Apple's AVFoundation export), anchored to 203-nit reference white. |
| **S2 Perception** | Build the Take Index (§3). | |
| **S3 Voice restoration** (parallel with S2) | Run best-of-N voice candidates, then gate them. Build a loopable room tone from the take's own pauses. | See the voice row below. |
| **S4 Brief** | The Director writes the Edit Brief: goal, profile, hook, beat sheet, length, pacing, visual/sound plan, don'ts, and justified deviations. From it, it derives a per-edit binary rubric (VideoArgus-style) and 3–4 divergent directions. It asks the creator at most one question, and only if the take choice or length is truly ambiguous and not already in the profile. | |
| **S5 Story cut ×4** | One candidate per direction, optionally one from a second-family Director. Each covers take choice, retake resolution (a false start counts only if a *later* re-delivery exists), structure, hook and ending. Each must pass a "radio test": an audio-only render plus reading the kept transcript. | |
| **S6 Select + fuse** | The jury ranks list-wise, then the top two are confirmed with position-swapped pairwise judging. The Director fuses the best parts (for example, B's hook into A's body). | List-wise beat scoring and voting; FusioN beat best-of-N. |
| **S7 Fine cut → picture lock** | Place seams, apply pause policy, filler decisions, acoustic snapping, J/L audio, speed segments. The picture-lock gate is: invariants, fine-cut metrics (§8), and a jury "story works" check. | |
| **S8 Finishing** (post-house order) | Reframes/punch-ins → b-roll and graphics → captions and on-screen text → sound (music, SFX, ducking) → color (match takes, then apply a look). Specialists propose and the Director applies. A later stage may reopen picture lock if it states a reason. | |
| **S9 Render-and-watch** | Rounds described in §2. | |
| **S10 Master + QC** | Final render, audio graph, own mux, per-platform encodes, QA gates (§5). | |
| **S11 Deliver** | Cut document, master and platform files, a no-music version, 2–3 hook alternates, a per-operation rationale, and the trace. | |
| **S12 Edit** | The in-app timeline and chat both emit operations on the same document, followed by a recompile and an incremental re-render. The diff feeds creator memory. | |

**Tools and settings by finishing area:**

- **Voice.**
  - Candidates:
    - no-op;
    - ClearerVoice MossFormer2_SE_48K;
    - DeepFilterNet3 with `atten_lim` 12–24 dB;
    - BS-RoFormer via python-audio-separator (only when music is in the room);
    - a hybrid chain: Sidon or Resemble Enhance restoration, then a MossFormer2 refine (ICASSP 2026 URGENT favours hybrid);
    - ElevenLabs Voice Isolator, ai-coustics and Auphonic;
    - the Apple dialog stem;
    - nara_wpe for dereverberation.
  - Gates:
    - ASR word error rate rises by at most 1 point;
    - ECAPA speaker similarity passes a calibrated threshold;
    - DNSMOS, UTMOSv2, SCOREQ and Audiobox Aesthetics do not regress;
    - lip sync stays within ±10 ms, checked by cross-correlation.
  - After selection, a pedalboard chain: high-pass, measured EQ, "just enough" de-essing, 3–6 dB compression, a leveler to ±2 LU, and target-mode breath attenuation.
- **Captions.**
  - Timing: re-time the corrected transcript with MFA 3.x, fall back to Qwen3-ForcedAligner, then to ASR times. Spell numbers out for alignment and display them as digits.
  - Rendering: `@remotion/captions` plus `layout-utils` fit checks.
  - Defaults:
    - one line of 2–4 words, 20 characters or fewer, split at phrase boundaries (Li 2026);
    - a heavy OFL sans (Montserrat, Inter, Anton or TikTok Sans) with a paint-order stroke;
    - one accent word, chosen from meaning plus prosody z-scores;
    - a 150–200 ms entry pop, with the highlight leading speech by 0–1 frame;
    - anchored below the chin inside the band x 65–888, y 288–1248;
    - hook title of 7 words or fewer in y 288–600;
    - Noto Color Emoji.
- **B-roll.**
  - Route each beat to an asset class:
    - creator media or requested pickups;
    - Playwright screenshots;
    - designed cards (numbers, lists);
    - premium stock (Storyblocks, Shutterstock, Adobe Stock; Pexels/Pixabay as fallback);
    - stylized AI stills (GPT Image 2.5, Nano Banana) with Depth Anything V2 Small parallax;
    - at most 1–2 photoreal hero shots (Gemini Omni Flash, Veo 3.1), flagged for disclosure.
  - Retrieval:
    1. 5–10 query variants, fanned out to 50–200 candidates.
    2. Embed with Qwen3-VL-Embedding-8B or PE-Core.
    3. Rerank with Qwen3-VL-Reranker-8B.
    4. Technical gate with UVQ plus blurdetect.
    5. Claude scores each candidate on its own against the rubric on a contact sheet, with a threshold or a refusal.
  - Conform: libplacebo, then Practical-RIFE, then color-matcher MKL, then SeedVR2/FlashVSR for low-resolution b-roll only.
  - Matting: BiRefNet (MIT), which leads HUG-VIS.
  - Memes: creator-supplied only.
- **Music and SFX.**
  - Epidemic Sound Partner API and MCP on the Scale tier with Safelisting:
    - Soundmatch;
    - Beats (downbeat times);
    - Versions (exact durations);
    - stems.
  - ElevenLabs Music, using `composition_plan` aligned to the hook and payoff, with `force_instrumental`.
  - Lyria 3.5.
  - Stable Audio 3 (self-host) and its Small-SFX variant.
  - Default: no music, or an unfamiliar instrumental starting 18–20 LU under speech. The final level is set by an ESTOI(stem, mix) and WER gate.
- **Color.** FFmpeg primitives (colortemperature, colorbalance, curves, lut3d), plus OpenColorIO/colour-science and color-matcher for matching takes. Face luma is checked against BT.2408 skin ranges.
- **Reframe and stabilization.** MediaPipe face track, smoothed with an L1-optimal path or a 1€ filter with a dead zone. vid.stab only for bad handheld footage.

---

## 2. Agent design

| Role | House default (decided by eval) | Sees | Writes | Tools |
|---|---|---|---|---|
| **Director** (sole writer) | Blind A/B of Claude Fable 5.1 (high/xhigh) against Opus 5.5 (xhigh/max). GPT-6 Astra is a certified alternate. | The whole Take Index as text (for takes up to about 2 min; query tools beyond that), contact sheets, creator profile and memory, style card, skills on demand | The cut document, through typed operations only | `get_words/gaps/prosody/visual_events`, `view_frames(range)` (burned frame numbers and word IDs), `crop_zoom`, `redecode(range)`, `ask_watcher(clip_ids, question)`, `load_skill`, `apply_ops` (returns validator plus metrics report), `render_review`, `request_proposal(specialist, brief)`, `ask_creator` (S4 only) |
| **Specialists** (read-only, fresh context): B-roll producer, Caption & text designer, Sound designer/music supervisor, Colorist, Motion-graphics author | Same model as the Director unless an eval says otherwise | Picture-locked document, index slice, their own skill, brief | Structured proposals: each item has a job, a one-line reason, 2–3 alternates, and a measurement report | Their own pipelines (retrieval/generation, conform, paging, music APIs, grade preview); the graphics author compiles its `custom_graphic` and checks a rendered frame |
| **Watcher** (tool, not an agent) | Gemini 3.8 Flash in agentic mode against Gemini 3.1 Pro (preview) | Native video and audio of clip IDs cut by code | Judgments only; code maps MM:SS answers to frames | — |
| **Jury** (fresh context, at least 2 families other than the Director's) | Gemini watches and hears the render. Claude or GPT-6 Astra judge high-resolution frames, the transcript and metrics. | Render, rubric, brief, metrics packet | Binary rubric results plus P0/P1/P2 notes mapped to word IDs | — |
| **Helpers** | Any certified model | — | Keyterms, query variants, spelling of names and brands | — |

**How planning works.**
- Reasoning stays in thinking blocks.
- The brief is emitted as free text and then compiled to a schema in a separate step.
- Never prompt the model to restate its reasoning in the reply; on Opus 5.5 that can trigger a `reasoning_extraction` refusal.

**Iteration budgets, chosen for quality:**
- **S5:** 4 candidates, each allowed 3 compile-and-validate rounds.
- **S7:** up to 3 rounds driven by metrics.
- **B-roll:** up to 3 retrieval or generation rounds per cue. After that the answer is a punch-in or nothing.
- **Music:** up to 3 candidate beds.
- **S9:** up to 6 rounds, and only while a P0 or P1 issue remains.
  - A revision is kept only if it (a) wins a position-swapped pairwise comparison against the current champion and (b) passes every deterministic gate.
  - Stop after 2 consecutive rounds with no win. The champion is always what ships.
- **Escalation:** Opus 5.5 can escalate to Fable 5.1 inside the same conversation, because Fable reads Opus thinking.

**What the render-and-watch loop inspects:**
- **Review renders:**
  - a 540×960 review render with burned frame numbers and word IDs (NumPro);
  - a clean full-resolution render, for caption legibility.
- **Gemini:**
  - a whole-clip pass with audio at 5 fps and high `media_resolution`;
  - a clipped ±1 s check at maximum fps around every seam, music hit and caption page change (using start/end offsets).
- **Claude:**
  - out/in frame pairs at each seam;
  - caption stills with the platform interface masks overlaid;
  - 1:1 crops for noise and sharpness.
- **Metrics packet:** all the §8 metrics.
- **How critics are asked:** concrete questions, not scores (VQQA). For example: "At W143→W151, does head position visibly jump?" The Director filters the notes against the brief.

**Harness:**
- **Model layer:** Pydantic AI (v2.51+), with escape hatches to the native anthropic, openai and google-genai SDKs.
- **Durable execution:** Temporal (or DBOS), so each model and tool call is a durable, resumable step.
- **Excluded:** LangGraph, LiteLLM, the Claude Agent SDK and the OpenAI Agents SDK.

---

## 3. Perception layer: the Take Index

All layers share one clock (microseconds plus native frame index), keyed by word ID.

| Layer | Contents | Tools |
|---|---|---|
| **L0 Media** | fps/VFR, transfer function, rotation, R128 loudness, clipping, noise floor, A/V offset, SNR/C50, quality scores | ffprobe/colordetect, pyloudnorm, SyncNet, Brouhaha, DNSMOS, UTMOSv2, Audiobox Aesthetics |
| **L1 Words** | Verbatim text; type (word, filler, repetition, cut-off, event); per-voter times; consensus time; logprob; sentence ID; retake-cluster ID; disagreement flags | **ElevenLabs Scribe v2**: `no_verbatim` off, `timestamps_granularity=character`, `tag_audio_events`, `diarize`, up to 1,000 keyterms from the creator profile. **Second voter:** MAI-Transcribe-2 (verbatim default), or CrisperWhisper 2.0 Pro if licensed; AssemblyAI U3.5 Pro with the "Mandatory: Preserve…" prompt as fallback. **Timing voters:** Scribe character times, MFA 3.x, CrisperWhisper. |
| **L2 Gaps** | Duration, VAD probability, RMS-minimum snap point, breath flag, room-tone RMS, pause class (sentence-final vs hesitation) | Silero VAD v6, Parselmouth F0 fall plus intensity |
| **L3 Prosody** | f0, intensity and duration z-scored against the creator's baseline; words per minute; stress probability; arousal | Parselmouth, WhiStress, emotion2vec+ (optional) |
| **L4 Visual** (15 fps on the tone-mapped proxy, smoothed) | Blinks, look-away/reading notes, face-lost, mouth-open onset, stillness windows, gesture energy, face and chin boxes, blur/exposure | MediaPipe Face Landmarker (478 landmarks plus blendshapes), Pose/Hand landmarkers, FFmpeg signalstats/blurdetect. TalkNet-ASD only when a second face or off-camera voice appears. TransNetV2 only for multi-shot uploads or b-roll. |
| **L5 Semantics** | The Director's reading, plus Gemini verdicts per clip ID: which retake reads more confident, reading notes, what is held in hand | Gemini (judgments only) |
| **L6 Seams** (on demand) | Landmark and pose delta between out-frame and in-frame, room-tone match, recommended way to hide the cut | Code |

**Cut edges.** The final edge is always placed by acoustic snapping inside the gap (Silero non-speech region plus RMS minimum), padded 30–200 ms. Where voters disagree, the slice is re-decoded in isolation.

**Bake-off before locking the stack.**
- **Data:** 30–50 labeled creator takes. Labels come from Rev human verbatim transcripts plus in-house cut-point annotation, including about 200 hand-labeled word boundaries.
- **Measures:** verbatim F1 and cut-edge error.
- **Consent:** get written consent from AssemblyAI and xAI before including them, because their terms bar benchmarking.

---

## 4. Timeline representation: the Cut Document

**Model.** A typed, versioned JSON document, event-sourced as an operation log. It is defined once as Pydantic models, which generate the TypeScript types and Swift Codable types.

**Contents:**
- **Sources:** mezzanine, proxy, stems.
- **A-roll:** items anchored to word-ID ranges with source times in microseconds, the chosen take, and speed segments.
- **Transforms:** punch-in and reframe keyframe paths.
- **Overlays:**
  - B-roll: layout (full, split, PiP, matted), asset, licence record, conform recipe, **job**, **reason**, alternates.
  - Graphics: one of `template`, `custom_graphic` (agent-authored React plus a Zod schema), or `lottie`/`rive`.
- **Caption plan:** explicit page text, line breaks, font size, emphasis word and anchor. The iOS editor reproduces these and never re-wraps.
- **Audio:**
  - voice stem;
  - per-seam crossfades and room-tone fills;
  - music with edit points and **sample-rate** automation curves;
  - SFX anchored to word IDs.
- **Color:** per-take correction, plus a look and its strength.
- **Deliverable profiles:** platform, protected safe-zone rectangles, GOP, bitrate cap, loudness target.
- **Pinned versions:** tone-map operator, doctrine version and model IDs.
- **Provenance per item:** author (agent or creator), reason, and a `creator_pinned` flag.

**Operations.** A small typed, ID-addressed vocabulary:
- `keep/remove(word_ids)`, `set_gap(gap_id, ms)`, `choose_take`, `reorder_beats`
- `set_speed(range, ≤1.2)`, `punch(range, scale, anchor)`, `set_layout`
- `add/replace/remove_broll`, `edit_caption_page`, `set_caption_style`
- `music_*`, `sfx_*`, `set_grade`, `pin/unpin`

Every operation is validated on application.

**Compiler.** One compiler turns the document into:
- a frame-quantized Remotion render plan;
- the FFmpeg audio graph;
- a preview manifest;
- a QA manifest;
- OTIO and FCPXML exports.

**In-app editor (replacing the current one).**
- A native SwiftUI timeline plus **transcript editing**: tap words to cut, drag to reorder beats, tap a gap to set its length.
- Every gesture becomes an operation.
- **Preview** is an AVFoundation composition of the proxy, with overlays drawn in one of two ways:
  - templated graphics as native Lottie/Rive;
  - captions and custom graphics using the *same* React components as Remotion, in a transparent WKWebView.
- Operations apply locally for instant preview. The server validates them and produces the authoritative render.
- **Parity CI** diffs app frames against server snapshots.

**Chat editor.**
1. The message is appended to the job's Director conversation.
2. The Director restates its interpretation in one line.
3. It emits operations, re-runs the gates of the affected stage, and does at most 2 render-and-watch rounds scoped to the changed region.
4. The creator gets a preview with one-tap undo. An ambiguous request returns 2 variants instead of a question.
5. Creator-pinned items are changed only when the creator explicitly asks.

---

## 5. Renderer and export

**Compositor.**
- **Remotion, pinned to ≥4.0.527** (the first release with the JPEG/bt709 colour fix, #11331).
- **Deployment:** a **new site name, serve URL and Lambda function**. The legacy `marque-render` site is never touched.
- **Settings:**
  - `imageFormat: 'png'`, `colorSpace: 'bt709'`;
  - OffthreadVideo for final renders (not `@remotion/media` until frame-diff parity passes);
  - `muted: true`;
  - ProRes 4444 intermediate;
  - text at output scale (A/B supersampling);
  - render at source fps, with a cap of 60.
- **Inputs:** only BT.709-tagged SDR mezzanine, so no second tone map happens.
- **Resolution:** punch-ins crop from the 4K mezzanine.
- **Licence:** Automators tier.

**Audio: entirely outside Remotion.**
1. One sample-accurate FFmpeg graph:
   - `atrim` per kept range;
   - 10–30 ms equal-power crossfades, with level and noise floor matched across the seam;
   - room-tone fills, never digital silence;
   - Rubber Band R3 for speed changes (A/B against atempo);
   - sample-rate ducking envelopes;
   - `amix normalize=0`.
2. Dynamics, then pyloudnorm linear gain, then a 4× oversampled true-peak limiter.
3. Targets: **−14 LUFS** integrated (A/B against −16), true peak ≤ −1 dBTP. Re-measured after the AAC encode.

**Mux.**
- Own mux with `-use_editlist 0`.
- Pre-trim the measured encoder priming delay (for example, 2048 samples for libfdk_aac).
- Verify with a beep-and-flash fixture.

**Per-platform encodes.**
- **Video:** x264 High, 8-bit yuv420p, BT.709, TV range.
- **Encoder settings:** preset `veryslow`, CRF 14–16, closed GOP with `keyint = fps/2` and `bf=2`, source fps, `+faststart`.
- **Bitrate cap:** `maxrate = min(25 Mbps, the Instagram 300 MB budget for the clip's duration)`. Use HEVC for long takes.
- **Audio:** AAC-LC 48 kHz; 384 kbps for YouTube and TikTok, 128 kbps for Instagram per its spec.
- **Resolution:** 1080×1920 by default; A/B 1440×2560.
- **Versions:** always also render a no-music version.
- **HDR:** a tracked experiment, not the default. First confirm the posting path preserves Dolby Vision 8.4; if it does, prototype with HyperFrames or an FFmpeg overlay stage at 203-nit graphics white.

**Output QA on every file:**
- ffprobe tags, constant frame rate, no edit list;
- size against the 300 MB cap;
- A/V offset within +5/−15 ms;
- integrated loudness and true peak;
- a colour round-trip chart check (ΔE);
- VMAF of the delivery encode against the master, and of a simulated low-bitrate platform re-encode;
- safe-zone and face-collision checks.

---

## 6. Multi-provider and bring-your-own-key

| Role | Anthropic | OpenAI | Google | OpenAI-compatible (OpenRouter, open models) |
|---|---|---|---|---|
| Director and specialists | Fable 5.1 / Opus 5.5 (default) | GPT-6 Astra (certified alternate) | Gemini 3.1 Pro / 3.8 Flash (needs certification) | Experimental label only |
| Watcher | — | — | **Gemini, always on the house key** | Qwen3.8-Omni-Flash, or self-hosted Qwen3-Omni-Instruct, as eval-gated fallbacks |
| Jury | Frames, metrics and transcript judge | Frames judge | Render watch-and-listen judge | — |
| Helpers | Any certified model | Any certified model | Any certified model | Yes |

**BYOK policy.**
- The house holds keys for every provider.
- A creator's key fills the **Director and specialist** slots, so "their model edits", and only with a model certified for that role.
- **The Watcher, the jury and perception always stay on house keys.** BYOK can therefore never downgrade perception or critique, and the house jury still gates the output.
- If a BYOK run cannot pass the gates within its budget, offer a house-key re-run. **Never switch billing silently.**
- The consent screen names every provider that receives data (App Store rule 5.1.2(i)).

**Reasoning-state rules.**
- One append-only, provider-native history per role per job.
- The only mid-conversation model changes allowed are Claude-to-Claude escalation and refusal fallback.
- Echo Gemini thought signatures and OpenAI reasoning items exactly, and Anthropic thinking blocks unmodified.
- Treat any Pydantic AI `drop_block` retry as an alerting bug, because it silently discards reasoning.
- Handle `stop_reason: "refusal"` explicitly and log which fallback model answered.
- Tune prompts, tool descriptions and frame-strip sizing per provider:
  - Claude: 2576 px long edge, and 2000 px per image once a request carries more than 20 images;
  - OpenAI: up to 1,500 images per request;
  - Gemini: native video.

**Capability fallbacks:**

| Missing capability | Fallback |
|---|---|
| Video or audio input | Dense burned-ID frame strips plus L3/L4 features |
| Images inside tool results | Tagged user message |
| Strict schemas | Pydantic validation plus readable retry errors |
| Parallel tool calls | Sequential calls |
| Small context | Index query tools plus compaction |
| Weaker model | Macro tools and tighter validators |

**Key handling.**
- **iOS:** Keychain with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. (Today the app stores auth tokens in UserDefaults; move those too.)
- **Transport:** per request over TLS in a header (Anthropic `Authorization: Bearer`, Gemini `x-goog-api-key`).
- **Server:**
  - Hold the key in worker memory for the job's lifetime only.
  - For durable restarts, use a KMS envelope-encrypted blob with a job-lifetime TTL that is deleted on completion.
  - **Never** put the key in Temporal step inputs or queue payloads.
  - Scrub headers from logs, Sentry and OTel. Pin dependencies by hash.
- **House Anthropic key:** Workload Identity Federation instead of a static secret.
- **Validation:** list-models plus a live probe. Map spend-limit 400, 401, 402, 403, 413 and capped 429 errors to clear messages.
- **Connection UX:** OpenRouter via OAuth PKCE. Guide users to a scoped, expiring, spend-limited personal key.
- **Prerequisite:** the backend has no user auth today. Add auth (plus App Attest) **before** accepting keys.
- **Gemini BYOK:** free keys allow training and human review, the EEA/UK/Switzerland need paid keys, and the API is labelled "not for consumer use". Gemini BYOK therefore waits on legal review.

---

## 7. Craft doctrine as guidance, and creator shaping

**Format.** Agent Skills (agentskills.io spec), loaded through the harness's own `load_skill` tool. About 100 tokens of metadata are always in context; each SKILL.md stays under 5k tokens, with references one level deep.

**Skill library:**
- `story-and-takes`, `hooks`, `pacing-pauses-fillers`, `seams-and-jump-cuts`, `punch-ins-reframe`, `speed`
- `broll`, `captions-text`, `music`, `sfx`, `voice-polish`, `color`, `endings-loops`, `platform-packaging`
- 7 content-type profiles: educational, storytime, comedy, hot take, tutorial, sales/UGC, podcast clip
- Opt-in signature styles: Hormozi, Abdaal, DOAC

**Contents of each skill:**
- principles with reasons;
- ranges tagged with an **evidence tier** (lab, ad-platform, vendor n≈3–13, internal);
- worked before/after examples, including the pro-cut calibration case;
- "when to break this";
- the metrics the critic will check.

The Director must name its profile in the brief and justify any deviation. Seed the skills from the existing kb-2026.11 knowledge base plus the verified research, rewritten with the corrections applied. Examples:
- a tempo-structure rule replaces the "CV<0.3" rule;
- greetings with direct address are kept;
- deliberate repetition is kept.

**Validators hold only invariants:**
- no cut inside a word or in a gap under 150 ms;
- no removal of a CTA or payoff the creator actually said;
- no digital silence;
- speed ≤ 1.25× on speech, and never on the hook;
- no text in protected rectangles;
- loudness and true peak;
- licence present for every asset;
- no reused clip;
- no VLM timestamps;
- A/V sync.

**Creator Profile.** The Director reasons over these values; they are not applied mechanically.
- **Style dials:** energy, cut density, pause policy, caption style, text load, b-roll ratio, zoom and SFX budgets, music, polish, length.
- **Signature:** catchphrases, and flubs to keep.
- **Brand:** fonts, colours, keyterms.
- **Measured baselines:** words per minute, f0, arousal.
- **Energy matching:** calm speakers get fewer seams and plainer captions. Speed-ups are never used to fake energy.

**Memory (CIPHER-style).**
1. After each export, diff the agent's document against the creator's final one, plus chat feedback.
2. Induce context-tagged preference statements.
3. Show them to the creator to edit.
4. Retrieve about k=5 into each brief.

**Reference videos → style card.**
- **Measured:** TransNetV2 cut intervals, a face-box scale tracker for punch-in rate, caption density and position, speech rate, LUFS, b-roll ratio.
- **Described:** Gemini adds a qualitative description.
- **Used as:** brief constraints, rubric items, and a pairwise "closer to the reference?" question.
- This stays an A/B-tested experiment.

**Anti-template.** Vary treatment across creators, to stay clear of YouTube's inauthentic-content policy and Instagram's originality ranking.

---

## 8. Evaluation

**Golden set.**
- 200 real takes, stratified by content type, audio quality, HDR/60 fps and length.
- Plus 40 takes cut by 2–3 hired professional editors, as human references and calibration.

**Primary metric: blind human pairwise preference, watched on a phone.**
- Raters: trained raters plus creators.
- Sample sizes: about 200 independent pairs detects 60/40; about 800 detects 55/45.
- Analysis: Bradley–Terry or mixed effects, with clip and rater as random effects.
- Never let a Claude-only judge score Claude output.

**Automated.**
- **Per-edit binary rubric grader:** used only after it reaches at least 90% agreement with held-out human labels. Never prune rubric items using the test set.
- **Cross-family PoLL jury.**
- **Deterministic metrics:**
  - Cut: seams per minute (pro: 1–4 per 60 s), median kept boundary pause (250–550 ms), share of cuts inside a clause, within-section and between-section tempo structure, longest static stretch (flag over 8 s).
  - Hook and ending: time to first speech or payoff (≤2.5 s) and to the proposition (≤3 s); final word to end ≤0.5 s; CTA and payoff words retained.
  - Treatments: punch-in count and scale against source resolution; share of speech sped up and its maximum factor.
  - ASR round-trip diff on the render.
  - Audio gates.
  - Visual do-no-harm checks: signalstats, blurdetect/blockdetect, flicker, VMAF, UVQ.
  - B-roll: wrong-subject rate, relevance precision, budget, reuse, conform failures.
  - Captions: caption WER, page timing, CPS as a monitored metric.

**Component bake-offs.** ASR and timing, voice chain, tone-map operator, matting, caption styles, −14 vs −16 LUFS, 1080 vs 1440 wide.

**Ablations.** Planner, N candidates, fusion, render-watch critic vs text critic, Gemini Watcher vs frames-only, and each skill individually.

**Field feedback (with creator consent).**
- Accept-without-edit rate, edit distance, chat corrections.
- Instagram `reels_skip_rate`, and **Trial Reels** (`trial_params`) for organic A/B tests.
- YouTube `engagedViews` and `audienceWatchRatio`.
- Each video compared against the creator's own median.
- LMM-EVQA as an engagement-continuation (ECR) proxy, used only to rank hook alternates.

**Adopting new models.**
- A certification suite per role, in Inspect AI or Pydantic Evals:
  - contract tests: schema, tool use, thinking round-trip, refusal path, image-in-tool-result;
  - a golden-set run.
- Thresholds:
  - "certified" (usable for BYOK) at ≥45% pairwise preference against the incumbent;
  - "new default" requires a win whose confidence interval lower bound is at least 50%, plus a live shadow period.
- Stamp every job with model, doctrine and pipeline versions.
- Use AgenticVBench as an external sanity check.

---

## 9. Preserving and routing to the static engine

1. **Tag and freeze.**
   - Tag `static-engine-v1` at `1c07c18`.
   - Keep the `marque-render` site and Lambda 4.0.484 untouched.
   - Build v2 as a separate package (`backend/studio/`) with its own Remotion site, function and bucket prefix.
2. **Resolve the engine once per job.**
   - Set `job["engine"] ∈ {static, studio}` at create time in the job dict at `main.py` ~3894. It persists through `_persist_clip_job`.
   - Resolution order: global kill switch env, then a per-creator allowlist (copy the `STRATEGY_ALLOWLIST` pattern), then a percentage ramp.
3. **Branch at the spawn sites:** create auto (~3970), create analyze (~3985), confirm (4142), and retry (4194–4203).
   - `/tweak`, `/retheme` and `/suggested-edits` reject studio jobs with 409 and point to the new `/v2/clips/{id}/ops` and `/v2/clips/{id}/chat` endpoints.
   - Legacy jobs keep the old EDL and ProEditorView.
4. **Keep the polling contract.**
   - Keep `AnalyzeJobResponse` and the existing status strings, so polling in `LiveClipEngine` keeps working.
   - Add optional `engine` and `stage` fields for richer progress.
   - The app opens the new editor only when `engine == "studio"`.
5. **Shadow mode.** Reuse the `_log_shadow_diff` pattern: for consented creators, run studio in the background on static jobs and store both outputs for pairwise evals.
6. **Automatic fallback.** If a studio job crashes, or fails its P0 gates after its budgets, re-run it on the static engine and flag it.
7. **Revert.** Flipping the default flag reverts all new jobs. Existing jobs keep working on the engine that made them.

---

## 10. What not to build (reuse)

**Reuse:**
- Harness: Pydantic AI plus Temporal.
- Perception: Scribe/MAI APIs, MFA/Qwen3-FA, Silero, MediaPipe, Parselmouth, WhiStress, SyncNet.
- Video processing: libplacebo/FFmpeg.
- Rendering: Remotion plus `@remotion/captions`. Port the existing Captions, BrollLayer, PunchZoom, EndCard, CTA registry and Watermark components as starting points.
- Audio: pyloudnorm, Rubber Band, pedalboard, the voice models listed in §1, Epidemic Sound, ElevenLabs Music, Lyria, Stable Audio 3.
- Visual: stock APIs, Qwen3-VL embedder and reranker, BiRefNet, RIFE, color-matcher.
- Evaluation: VMAF, UVQ, VERSA, Inspect AI.
- Interchange: OTIO.

**Do not build or ship:**
- Custom ASR, aligner, beat tracker, stabilizer or renderer.
- LiteLLM, or a multi-agent graph framework.
- Fine-tuned models in v1.
- Eye-contact correction, skin smoothing, generative face restoration, AI upscaling of the speaker, added grain.
- Server-side GIPHY/KLIPY memes; remove the Tenor code paths.
- A persistent index of stock-provider content.
- Tools with non-commercial or otherwise blocked licences: NISQA, DOVER, MatAnyone, InsightFace models, MusicGen, MMAudio, madmom models, Kling, Sora, Qwen-Image, FLUX.2 [dev]/klein 9B weights, MiniMax H3 weights, AGPL whisper-timestamped.
- The current SoundHelix/codeskulptor music beds and SFX files with no licence note. Don't carry them into v2.

---

## 11. Phased build plan

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Foundations** | Static-engine tag and freeze; `job.engine` routing, kill switch and fallback; backend auth; licence audit; eval harness; golden set and pro reference cuts started | One flag flip reverts everything. 200 takes collected, 40 pro cuts commissioned. Human-eval app on phones works. |
| **P1 Perception + audio** | Ingest/tone map, Take Index, ASR and timing bake-off, voice best-of-N, room tone, audio graph, own mux | ASR and timing chosen on measured verbatim F1 and cut-edge error. The voice chain beats no-op on quality metrics with WER Δ≤1. A/V offset within +5/−15 ms. Tone-map operator chosen by blind A/B. |
| **P2 Cut core** | Cut Document, compiler, Director, S4–S7, jury, S9, captions, loudness, platform encodes and QA | ≥60% blind pairwise win against the static engine (n≈200). Zero invariant violations. Seams per minute and pause medians in the professional range. |
| **P3 Finishing** | Punch-ins/reframe, b-roll (retrieval, generation, screenshots, pickups, conform), graphics, music/SFX, color | Each specialist's ablation is non-negative. B-roll wrong-subject rate under 5%. Full engine ≥65% against static and ≥45% against pro cuts. |
| **P4 Editing + personalization** | New iOS timeline and transcript editor, chat, preview parity, creator memory, style cards, BYOK certification | Frame-diff parity passes. Chat intent adherence ≥90% on a labeled request set. Certification suites green for Claude, GPT and Gemini. |
| **P5 Rollout** | Shadow → allowlist → percentage ramp; Trial Reels field tests; HDR experiment | Field metrics at or above static on the creator-median comparison. Fallback rate under 2%. Accept-without-edit rate at or above static. |
| **Ongoing** | Model adoption via certification; ablating one skill at a time | Documented in §8. |

**Plain-language explainer doc (visuals to include):**
1. A pipeline strip, S0–S12.
2. The "who writes and who advises" agent diagram.
3. A Take Index layer stack.
4. The render-and-watch champion loop.
5. A BYOK role matrix.
6. A before/after cut timeline from the pro-cut calibration take.

---

## 12. Top risks and mitigations

1. **Over-editing** (the 19-splices failure).
   - Doctrine stresses restraint, and "no insert" or a punch-in is always a valid answer.
   - Budgets and seams per minute are in the critic packet.
   - Calibrate against the pro cuts.
2. **Perception errors** (lost fillers, drifting timing).
   - Two ASR voters, a timing vote, acoustic snapping, disagreement re-decode, and an ASR round-trip diff on every render.
3. **Critic unreliability and self-preference.**
   - Cross-family jury, calibrated binary rubrics, position swaps, human evals as the arbiter, and a critic prompt tuned from its own logs.
4. **VLM timestamp drift.** Timestamps are never used; everything is clip-ID and word-ID mapping.
5. **Licensing and platform penalties.**
   - Epidemic Safelisting, a licence ledger per asset, no server-side GIPHY/KLIPY, AI-disclosure flags (SynthID survives re-rendering), and rejection of watermarked stock.
6. **Double tone mapping or colour errors.** Assert tags at ingest, run the round-trip chart test, and pin the Remotion version.
7. **Preview and render drift.** Shared React overlays, Lottie/Rive, explicit caption layout, and frame-diff CI.
8. **Provider churn** (thinking-block rules, refusals, preview models). Pinned model IDs, contract tests, refusal fallback, and native-SDK escape hatches.
9. **BYOK security and App Store review.**
   - Keychain storage, no persisted keys, auth first, and every feature also available through in-app purchase.
   - Gemini BYOK waits on legal review.
10. **Job fragility over long runs.** Temporal durable steps, the champion always saved, and automatic fallback to the static engine.
11. **Eval validity.** Power-analysed sample sizes, clustering corrections, pro references, and field data.
12. **Template sameness.** Per-creator profiles and memory, and variation across creators, checked in QA.

---

### Critical files for implementation

- `/Users/home/Marque-wt/editor-audit/backend/main.py`: job creation (3852/3894), spawn sites (~3970, ~3985, 4142, 4194–4203), the `EDL_AUTHOR` shadow pattern (6124, 6148), and the render bridge (8193–8355).
- `/Users/home/Marque-wt/editor-audit/backend/app/edl.py`: the legacy EDL, `TWEAK_OP_TYPES` (1287) and `build_render_plan` (910), which must stay intact for static jobs.
- `/Users/home/Marque-wt/editor-audit/render/src/lambda-render.ts` and `/Users/home/Marque-wt/editor-audit/render/src/types.ts`: the Lambda bridge and render-plan types to fork under a new site.
- `/Users/home/Marque-wt/editor-audit/ios/Marque/Adapters/LiveClipEngine.swift` and `/Users/home/Marque-wt/editor-audit/ios/Marque/Adapters/BackendClient.swift`: the job and polling contract and the BYOK header path.
- `/Users/home/Marque-wt/editor-audit/backend/knowledge/`: the kb-2026.11 craft docs to rewrite as evidence-tiered Agent Skills.