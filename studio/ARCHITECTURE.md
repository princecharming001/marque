# Yunicorn Studio — engine contract (v1)

This document is the shared contract every module is built against. The full rationale lives in
`/Users/home/URAP - Lead - Levine/reports/Yunicorn Studio plan.md` (the approved plan) and the verified
research in `/Users/home/URAP - Lead - Levine/reports/Yunicorn Studio design inputs/`. When this
contract and the plan disagree on an interface, this contract wins; when they disagree on craft, the
doctrine in `skills/editing/` wins.

**Only goal: the quality of the final video.** Ignore cost and latency. Never trade quality for speed
in the default path. (Previews may be faster; finals may not.)

**Tiers (owner requirement, 2026-10-09).** That goal is the **director** tier. The **standard** tier, the default
for new jobs, must cost under $0.20 a video. It keeps the same document, compiler, renderer and all ten invariants
(§7), and gives up the review: no model watches the rendered file, and it ships marked "NOT REVIEWED: standard
tier". It trades encode speed (x264 `medium`, a 1080p30 working copy) and the critic loop for cost, never the hard
gates. See §11, `docs/standard-tier-plan.md` and `docs/standard-tier-interfaces.md`.

**Standalone.** Studio is not wired into the app or the old backend (`backend/`). Do not import from
`backend/` or modify anything outside `studio/`. The old static engine is frozen at git tag
`static-engine-v1`.

---

## 0. Ground rules

1. **Code perceives, measures and enforces; models judge and decide.** A model never produces a
   timestamp that becomes an edit coordinate. Models address **word IDs** (`w0001`…), **gap IDs**
   (`g0001`…), **sentence IDs** (`s001`…), **take-cluster IDs** (`c01`…), **insert IDs** (`i001`…) and
   **segment IDs** (`seg001`…). Code converts IDs to times using measured boundaries.
2. **One writer.** Only ops mutate the CutDocument, through `studio.doc.ops.apply_ops` (via
   `studio.agent.tools.EditSession`, which writes the versions and the oplog). The writer is the Director in
   the director tier, and in the standard tier code: the code plan, which applies the planner's ID-only
   decision, the code repair, and the standard chat's op list. Specialists, critics and the planner return
   proposals, notes or decisions, never documents.
3. **Guidance, not rules.** Craft numbers live in `skills/editing/` and `skills/editing/constants.yaml`
   as priors. Only the invariants in §7 are hard gates.
4. **Keys.** All secrets come from `studio.config.Settings` (env vars, optionally loaded from the file
   named by `STUDIO_ENV_FILE`). Never print, log or persist a key. BYOK keys are passed in memory.
5. **Everything is reproducible from the job directory** (see §8): inputs, index, document versions,
   renders, logs, trace.
6. **Tests.** Every module ships pytest unit tests that run **keyless and offline** (mock HTTP; small
   synthetic media generated with ffmpeg/numpy in fixtures). Tests that need real keys or network are
   marked `@pytest.mark.real` and skipped unless `STUDIO_REAL=1`.

## 1. Environment

- Python 3.12 venv at `studio/.venv` (already created; torch 2.14 w/ MPS, mediapipe 1.0.1, pedalboard,
  praat-parselmouth, pyloudnorm, silero-vad, open_clip_torch, opencv-python-headless, librosa,
  pydantic 2, pydantic-ai-slim[anthropic,openai,google], httpx, rapidfuzz, jiwer, pytest).
  Add new deps to `pyproject.toml` and install them into the venv; prefer permissive licenses; GPL
  tools may be used server-side (CLI invocation). Do not use license-excluded tools listed in the plan
  §14 (e.g., MusicGen, MMAudio, madmom models, FLUX.2 dev, Kling, InsightFace, NISQA, DOVER, MatAnyone).
- System tools: `ffmpeg`/`ffprobe` 8.0.1 (has zscale, tonemap, lut3d, rubberband filter, loudnorm,
  vidstab, libass; **no libplacebo**), `rubberband` CLI (use `-3`/R3 engine for speech), Node 24 + npx
  for the Remotion overlay project in `studio/overlay/`.
- Hardware: Apple M4, 24 GB RAM, ~11 GB free disk. Keep caches and model downloads lean.
- Keys available via `/Users/home/Marque/backend/.env` (loaded only when `STUDIO_ENV_FILE` points to
  it): `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` (Scribe v2 ASR, audio isolation, Music, SFX),
  `ASSEMBLYAI_KEY` (fallback ASR), `PEXELS_KEY` (stock), `HIGGSFIELD_KEY` (image generation).
  **Not available:** Google/Gemini, OpenAI — build and unit-test those providers with mocks; they
  become live when a key is supplied.
- Test media: `/Users/home/studio-testdata/*.mov|mp4` (real talking-head QA takes: real-take40 40 s
  1920x1080 rotated portrait; var-multitake 51 s with a verbatim retake + false start; var-silences
  105 s; d030 30 s; d060 60 s; plus 4k60 HEVC, landscape, VFR, no-audio, music-in-room variants).

## 2. Time

- `studio.timebase`: all media times are **integer microseconds** (`int`, name suffix `_us`) in the
  Take Index; the compiler works in `fractions.Fraction` seconds and snaps each edit once to the output
  frame grid; audio sample positions derive from the same instants (no float drift).
- Output frame rate = source frame rate (29.97/30/59.94/60 preserved; VFR conformed to nearest CFR) in the
  director tier. The standard tier caps it at 30 fps by dropping whole frames in a regular pattern (60 → 30,
  59.94 → 29.97, 50 → 25; never resampled); slower sources keep their rate. See §3.

## 3. Ingest (`studio.media`)

`ingest(src_path, job, *, max_height=None, max_fps=None) -> MediaInfo` produces in the job dir:
- `media/original.<ext>` (hard link or copy; never modified)
- `media/probe.json` (ffprobe), `MediaInfo`: width/height after rotation, display aspect, fps
  (Fraction), vfr flag, duration_us, color (primaries/transfer/matrix/range; `hdr: bool`), audio
  (codec, sample rate, channels), rotation, edit-list/priming notes.
- `media/mezz.mov` — the **mezzanine**: rotated upright, SDR BT.709 (HLG/PQ tone-mapped exactly once
  with zscale+tonemap, 203-nit reference white, dither). Every later video operation reads this, never the
  original. Its size and rate depend on the tier:
  - **Director tier: full resolution**, CFR at the source fps, ProRes 422 HQ (or FFV1).
  - **Standard tier: 1080p30, lean.** The pipeline passes `max_height` (1920 for a portrait picture, 1080 for a
    landscape one) and `max_fps=30`. The height is capped (aspect kept, even width). The rate is divided by the
    smallest integer that brings it under 30 (60 → 30, 59.94 → 29.97). A cap also selects the lean intermediate
    codec as the tier's budget rather than a disk fallback. The resize is one more zscale step in the same graph,
    so HDR is still tone-mapped exactly once (invariant 6). `MediaInfo` width/height/fps describe the mezzanine,
    and `MediaInfo.conform` records the source geometry and rate. A 4K60 source costs ~165-180 vCPU-min through a
    full-resolution mezzanine, which is the reason for the cap; the output canvas is 1080x1920 either way.

  In the director tier, when the disk budget (`studio.storage`: the mezzanine *plus* the edit's renders) does
  not fit, the codec steps down ProRes 422 → the lean intermediate (HEVC 4:2:2 10-bit VideoToolbox q85, x264
  4:2:2 10-bit CRF 4 fallback; ≥ 56 dB PSNR).
  A delivered (or idle) job's mezzanine is regenerable: it may be reclaimed and is rebuilt frame-identically
  from `media/original.*` before the next render (`storage.ensure_mezz`, at the size and rate it was first
  built with, so a standard job stays 1080p30).
- `media/audio.wav` — 48 kHz float32 mono (and stereo if source is stereo) dialogue track.
- `media/proxy.mp4` — 540x960 H.264 with burned timecode, for fast frame grabs and previews.

## 4. Perception: the Take Index (`studio.perception`)

`build_index(job) -> TakeIndex`, saved to `index/take_index.json`. Pydantic models (in
`studio/perception/index.py`):

```
Word:      id "w0001", text, start_us, end_us, kind: "word"|"filler"|"event"|"cutoff",
           confidence, speaker, sentence_id, cluster_id|None, prosody: {f0_z, int_z, dur_z}|None,
           emphasis: float 0..1, truncated: "start"|"end"|"both"|None (a digital-silence dropout
           in the recording chops the word; also kind "cutoff"; edges snap to the dropout edge)
Sentence:  id "s001", word_ids[], text, start_us, end_us, complete: bool, cluster_id|None
Cluster:   id "c01", sentence_ids[] (retakes/rephrasings of the same line, in time order),
           similarity, recommended_sentence_id (last complete take by default), notes
Gap:       id "g0001", after_word_id, before_word_id, start_us, end_us, kind:
           "pause"|"breath"|"silence"|"noise", snap_us (quietest point, RMS-min), energy_db,
           has_breath, breaths_us, dropouts_us (digital-silence runs: the recording lost them;
           never pause time for sentence segmentation), sound_us (non-word sound — a fragment of a lost
           word, a noise — that cut pads and pause trims never keep)
Visual:    per-sample (10 fps from mezz): t_us, face_box (x,y,w,h normalized)|None, face_conf,
           landmarks-derived: eyes_open, gaze_off, mouth_open, head_yaw/pitch; blur, luma
           Events: blink, look_away, face_lost, reading (gaze down) with start/end us
Audio:     noise_floor_db, snr_db, clipping_ratio, integrated_lufs, true_peak_dbtp, rt60_est|None,
           music_in_room: bool, room_tone_ranges_us[]
TakeIndex: version, media: MediaInfo, words[], sentences[], clusters[], gaps[], visual (samples +
           events + smoothed face track), audio, energy: {wpm, f0_var, loudness_var, overall 0..1},
           transcript_text, asr: {provider, model}
```

ASR: **ElevenLabs Scribe v2** verbatim (fillers, false starts, audio events, word timestamps,
diarization) is the default; AssemblyAI is the fallback when ElevenLabs is unavailable. Boundaries
are refined by acoustic snapping (Silero VAD + RMS minima) — never by a model.

Query API (used by agent tools), all ID-based: `get_words(from_id, to_id)`, `get_sentences()`,
`get_clusters()`, `get_gaps(min_ms)`, `get_prosody(word_ids)`, `get_visual_events(kind)`,
`render_transcript(view="full"|"compact")` (word IDs inline, pauses marked `[g0012 0.62s]`, fillers
marked `{um}`), and `studio.perception.frames.contact_sheet(job, items, label=...)` which renders
PNG grids of frames with burned word ID + timecode (for the Director and critics).

## 5. The CutDocument (`studio.doc`)

Pydantic, versioned, serialized to `doc/v{n}.json` plus an append-only `doc/oplog.jsonl`.

```
CutDocument
  version: int; job_id; created_by (model id); brief: Brief|None
  style: {primary, blend[], dials: {energy, pace, polish, humor, …} 0..1}
  pins: {must_keep_word_ids[], payoff_word_ids[], cta_word_ids[]}
  segments: [Segment]          # the story, in output order
     Segment: id "seg001", from_word, to_word (inclusive, same source), speed: float (1.0),
              gap_overrides: {gap_id: target_ms}, framing: Framing|None, seam_in: SeamTreatment
  removed: [{from_word, to_word, reason}]   # explicit record of what was cut and why
  inserts: [Insert]            # b-roll and cards, anchored to word IDs (output-time derived)
     Insert: id "i001", anchor_from_word, anchor_to_word, mode: "full"|"split_top"|"split_bottom"|
             "pip"|"card", asset: AssetRef|CardSpec, job (why it's there), audio: "voice_only"|
             "duck"|"with_sfx", transition_in/out, licence
  captions: CaptionPlan|None   # pages [{word_ids[], emphasis_word_ids[]}], style (font, size,
                               # stroke, colors, case, animation), position policy, enabled
  texts: [TextOverlay]         # hook title, callouts, lists, lower thirds (anchored to word IDs)
  audio: {voice: VoiceChainSpec, music: MusicSpec|None, sfx: [SfxCue], room_tone: bool,
          loudness_target_lufs: -14.0, true_peak_dbtp: -1.0}
  color: {exposure, white_balance_k|temp/tint, contrast, saturation, look: str|None,
          lut_strength} | None
  deliverables: [{platform: "tiktok"|"reels"|"shorts", variant}], hook_alternates: [...]
  notes: [{by, text}]          # rationale, per decision
Framing:        scale (1.0-1.6), center: "face"|{x,y}, anchor_word (punch timing), ease
SeamTreatment:  kind: "cut"|"jcut"|"lcut"|"punch"|"cutaway"|"crossfade", lead_ms (for J/L)
```

Ops (`studio.doc.ops`) — the ONLY way to change a document; every op validated; `apply_ops(doc, ops,
index) -> (doc', results[{op, applied: bool, reason}])`; never raises on bad ops; produces version
n+1 and appends to the oplog. Op families (small strict schemas):
- cut: `set_story(segments)` (whole story in one op, for the first cut), `cut_words(from,to,reason)`,
  `restore_words(from,to)`, `choose_take(cluster_id, sentence_id)`, `move_segment(seg, after)`,
  `set_gap(gap_id, ms)`, `set_speed(seg, speed)`, `set_seam(seg, treatment)`
- framing: `set_framing(seg|word range, scale, center, ease)`, `clear_framing(...)`
- inserts: `add_insert(...)`, `update_insert(id, ...)`, `remove_insert(id)`
- captions/text: `set_captions(plan)`, `edit_caption_page(...)`, `add_text(...)`, `update_text`,
  `remove_text`
- audio: `set_voice_chain(spec)`, `set_music(spec|None)`, `add_sfx(...)`, `remove_sfx(id)`,
  `set_loudness(lufs)`
- color: `set_color(spec)`
- meta: `set_brief`, `set_style`, `pin(word_ids, kind)`, `note(text)`, `undo(n)`

## 6. Compiler and render (`studio.compile`)

`compile(doc, index) -> Timeline` resolves IDs to output time: each segment's source range is
[start of first word − lead pad, end of last word + tail pad] snapped to gap snap points; kept gaps
are trimmed to target lengths around their snap point; J/L cuts offset audio vs video; speed maps
output time; inserts/texts/captions/sfx get output-time spans from their anchor words. Output frame
grid snapping happens once here.

Render stages (each writes into `renders/r{n}/`):
1. **A-roll video** — ffmpeg filtergraph from `mezz.mov`: trims, speed (setpts), framing transforms
   (crop+scale with Lanczos from the full-res mezz, face-tracked centers smoothed), b-roll layers
   (full / split / pip / card-underlay), output 1080x1920 @ the mezzanine's fps, ProRes 422 HQ intermediate (the
   lean intermediate when the disk budget says so, and always in the standard tier; recorded as `aroll_codec` in
   `render.json`).
2. **Overlays** — Remotion project `studio/overlay/` renders captions, text overlays, designed cards
   and graphics from `overlay_props.json` as ProRes 4444 with alpha (bt709, png intermediates), same
   fps/duration; `STUDIO_REMOTION_CONCURRENCY` browser tabs at once (default: the CPU count).
3. **Audio** — dialogue segments cut from `audio.wav` with 10–30 ms equal-power crossfades and
   room-tone fill; speed via `rubberband -3` (pitch preserved); voice chain (pedalboard: HPF, EQ,
   de-ess, compression, optional denoise/isolation); music bed (ducked under speech by sidechain
   envelope), SFX; loudness normalize to target with a 4x-oversampled true-peak limiter. Outputs
   `mix.wav` and `mix_nomusic.wav` (48 kHz).
4. **Master** — ffmpeg composite A-roll + overlays + audio → `final_<platform>.mp4`: x264 High
   8-bit 4:2:0 BT.709 tagged, `+faststart`, AAC ≥256 kbps 48 kHz; also `final_nomusic.mp4`, `cover.jpg`,
   `captions.srt`. Preset and CRF come from the render profile (`master.RENDER_PROFILES`, threaded through
   `render_full`/`render_document`/`master(profile=...)` and recorded in `render.json`): `final` = veryslow
   CRF 15 (the director tier's champion and finals), `standard` = medium CRF 16 (the standard tier's one render),
   `preview` = faster CRF 20. GOP, VBV caps and colour tags are shared, so invariant 10 is the same for all.
Preview renders use the proxy and a fast preset; the director tier's champion and finals always use full quality.

## 7. Invariants (the only hard gates; `studio.qa`)

1. No audible click or clipped phoneme at any seam (click detector ±5 ms + ASR round-trip of the render), nor
   at a recording-dropout edge inside kept audio; no kept word the recording itself chops (`Word.truncated`).
   (Untranscribed sound in a cut pad, `Gap.sound_us`, is kept out by the compiler and muted under room tone by
   the audio stage where the frame grid leaves a sliver of it.)
2. Rendered audio duration/sample alignment matches the compiled timeline (±1 frame A/V sync).
3. No model-provided timestamp used as an edit coordinate (enforced by the op schemas).
4. Pinned payoff/CTA words present unless unpinned.
5. Licence record for every inserted asset / music / sfx.
6. HDR tone-mapped exactly once (mezz only).
7. Captions/text inside the platform safe zone and not covering eyes/mouth (checked against the face track after transforms).
8. Loudness at target ±1 LU; true peak ≤ −1 dBTP measured on the encoded file.
9. No digital silence under speech.
10. Delivery format checks (codec, pix_fmt, color tags, fps, duration, faststart).

The ten invariants are **the same in both tiers**. They gate every render that ships: the director tier's
champion, and the standard tier's one render, its repair render and its chat renders. The standard tier does not
relax, skip or add any of them. Its repair (§11) is code that edits the document or re-renders a layer until they
pass. It never waives one.

## 8. Job directory

`$STUDIO_WORK_DIR/<job_id>/` (default `/Users/home/studio-work/`):
`media/`, `index/`, `doc/`, `renders/`, `critique/`, `assets/` (b-roll, music, sfx with licence
json), `logs/`, `trace.jsonl` (every model call: role, provider, model, tokens, latency, tool calls; every ASR
call as an `asr` event with its audio seconds), `report.md`. The standard tier adds `logs/plan.json` (the
planner's decision, attempts, rejections, usage). `job.json` records the tier, the planner (model, effort) and
the Director, so a resume or a chat stays in the tier and on the models it started with.

## 9. Agents (`studio.agent`)

- Providers (`providers.py`): `ModelSpec{provider: anthropic|openai|google|openai_compat, model,
  api_key (in-memory), base_url, capabilities{vision, video_input, strict_tools}}`; house default
  Anthropic; BYOK supplies a spec for the **Director** role; critics stay house. Built on
  pydantic-ai native model classes. Roles and their efforts are in `ROLE_EFFORT`: director `max`, critic `high`,
  planner `low`, among others. The planner role uses a 5-minute cache TTL (one call plus at most one retry);
  the others use 1 h.
- Director (`director.py`): stages brief → story cut → fine cut → finishing → finalize; tools are
  thin wrappers over the Take Index query API, `contact_sheet`, `load_skill(name)`, `apply_ops`,
  `render_preview(scope)`, `ask_creator` (returns "no answer" in batch mode), and critique requests.
- Skills (`skills.py`): `SKILL.md` always in the system prompt; topic files loaded on demand via
  `load_skill`; `constants.yaml` available to critics/metrics.
- Critics (`critics.py`): frame judge (a different model family when available, else a
  fresh-context house model marked `same_family=true`), watcher (Gemini with video when a key
  exists, else frames + metrics), metrics packet from `studio.qa` plus caption geometry (where
  every page sits against the face after framing, how big it renders). Notes are localized by word
  ID and name their claim; a metric confirms only the claim it measures. The rubric is critique.md's
  questions + the topic files of the layers present + the brief's viewer checks. A failed review
  (after retries) raises `CritiqueUnavailable`: it is never "nothing to change".
- Champion loop (`loop.py`): render → critique → Director revision (it sees the champion render and
  the critics' sheets) → position-swapped pairwise comparison (≥2 judges with equal evidence and a
  neutral diff; a judge saying "same" both ways abstains; taste changes need every judge, a fix of a
  confirmed P0/P1 wins when nothing regresses and no judge prefers the champion; ties = keep
  champion) → keep/stop after 2 winless rounds (guard 12); recorded hook alternates are built,
  rendered and judged as variants; the closing watch's confirmed P0/P1 opens one more round; a
  champion the critics could not review ships marked NOT REVIEWED. `LoopConfig.max_model_usd`
  (`--max-usd`, `STUDIO_MAX_MODEL_USD`) stops opening rounds once the job's model spend at list prices
  reaches the cap; the champion ships with stop reason `model spend cap`.
- Planner (`studio/plan/planner.py`, standard tier only): one structured-output call returning a
  `PlanDecision` of IDs, one retry carrying the rejection text, then the pure-code plan; never a tool
  loop, never images, never a critic. See §11.

## 10. CLI (`studio.cli`, entry point `studio`)

- `studio edit <video | job_dir> [--brief TEXT] [--style NAME] [--platform tiktok|reels|shorts]
  [--tier standard|director] [--planner-model M] [--max-usd USD]
  [--director-provider P --director-model M --director-key-env VAR | --house] [--rounds N] [--out DIR]`
  → runs everything in the tier (`--tier`, else the job's recorded tier, else `STUDIO_TIER`, default
  `standard`), prints the job dir, the tier and final file paths. A job directory resumes it. A standard job given
  `--tier director` is upgraded to the Director (§11).
- `studio chat <job_dir> "<instruction>" [--planner-model M]` → in the job's tier. Director tier: the Director
  applies ops for the instruction, re-renders, re-judges, prints what changed. Standard tier: one planner-model
  call returns ops (§11).
- `studio index <video>`, `studio render <job_dir> [--version N] [--preview]`, `studio report <job_dir>`,
  `studio qa <job_dir>`.

## 11. The standard tier (`studio.plan`)

The default tier for new jobs. It is the deterministic engine above plus **one** model call, **one** render, the ten
invariants and **code-only** repair, shipped marked "NOT REVIEWED: standard tier". The target is a marginal model +
ASR cost of about $0.04 a video (Sonnet 5.5 planner), about $0.01 with Haiku 5.5, and under $0.20 all-in with
compute. These figures are estimated from list prices and the measured digest sizes, and are not yet verified with
real credits (`docs/standard-tier-plan.md` §4). The binding signatures, with "As built" notes, are in
`docs/standard-tier-interfaces.md`.

**Flow** (`pipeline.edit` with tier `standard`; `pipeline._edit_standard`):
1. **Ingest** with `max_height`/`max_fps`: the 1080p30 lean mezzanine (§3).
2. **Take Index**, unchanged (§4).
3. **Digest** (`plan/digest.py`, `build_digest`). A text view of the index, typically 0.8-1.3k estimated tokens and
   capped at 12k:
   - one line per sentence, with every word ID inline, fillers `{um}`, cut-offs, `[cNN rec]` retake tags,
     `[incomplete]` and `[chopped]`, and pauses `[g0012 0.62s]`;
   - the measured stats: energy, wpm, pauses, SNR, music in the room;
   - the top emphasis words, the caption placement options and the voice-chain note;
   - the colour line, when a mezzanine exists.
4. **Planner** (`plan/planner.py`, `plan`). One `pydantic_ai.Agent(output_type=NativeOutput(PlanDecision))` call,
   role `planner` (Sonnet 5.5 at effort `low` by default, 8k max tokens, 5 m cache TTL, no server-side refusal
   fallback, a $0.15 spend cap per plan unless `--max-usd` says otherwise). The cached prefix is
   a doctrine checklist of under 1k tokens plus the compact schema; the digest is the user turn.
   - `validate_decision` rejects what code cannot apply or the doctrine forbids: unknown IDs, drops past half the
     spoken words, dropping the spoken CTA, a repeat with no later take, more than one punch-in, more than eight
     accents.
   - One retry carries the rejection lines.
   - A refusal, a provider error, a missing key or a second rejection gives `decision=None`. Nothing escalates unless
     `STUDIO_PLANNER_FALLBACK` names a model.
   - Everything is written to `logs/plan.json` and traced.
5. **Code plan** (`plan/code_plan.py`, `plan_document`). The ordered op list, applied through `EditSession` to a
   fresh document. Story:
   - one take per retake cluster (the decision's choice, else the index's recommendation);
   - false starts and abandoned lines dropped, plus the decision's drops;
   - cut-off and dropout-chopped words removed back to the nearest clause edge, else the sentence dropped.

   Everything else:
   - fillers and pauses from the `constants.yaml` priors, never below the speaker's median pause;
   - pins for the payoff, the CTA and must-keep words;
   - at most one punch-in, only where the source's clean scale allows it;
   - structure-marker labels;
   - captions at one uniform size: the largest in 88-64 px that pages cleanly, re-paging rather than shrinking;
   - the measured voice chain (paid isolation only for music in the room on a source of at most 60 s);
   - colour only on a measured fault;
   - music, SFX and b-roll never.

   The radio test (`validate.radio_blockers`) is a gate before compiling. With no decision, the same code builds the
   pure-code plan.
6. **One render**, `render_full(profile="standard")`: lean A-roll, Remotion overlays at
   `STUDIO_REMOTION_CONCURRENCY`, x264 medium CRF 16.
7. **QA**: `evaluate_render`, the ten invariants on the encoded file (§7).
8. **Repair** (`plan/repair.py`, `repair`). Code only, at most one re-render, before any prune:
   - invariant 1: cut the fragment at the join, then a full render;
   - invariant 4: restore the pins, then a full render;
   - invariant 7: re-place the caption pages, then an overlay + master pass on the failed render's hard-linked
     `aroll.mov` and mixes;
   - invariant 8: re-master the mix and re-mux onto the stream-copied video.

   Failures with no code fix render nothing and are reported. If repair does not pass, the second attempt is the
   pure-code plan; otherwise the job FAILS. It never ships a failing render.
9. **Deliver** one final (no byte-identical `final_nomusic.mp4`, since there is never music). `LoopState` is written
   with the champion render and document, `reviewed=False` and `review_note="standard tier: code QA only"`. The
   report has the Plan section, the seams table and the NOT REVIEWED line.

**Chat** (`pipeline._chat_standard`). One planner-model call turns the instruction into an op list, given the
document summary, the cut transcript and the caption geometry. The ops are allowed for the cut, captions, text,
framing and audio levels. Music, b-roll and isolation get a priced refusal. Then `apply_ops`, with one retry on
rejected ops, and an overlay + master render when the cut is unchanged, else a full standard render. Then the
invariants, repair and delivery.

**Upgrade.** `studio edit <job> --tier director` resumes a standard job into the Director's champion loop from the
standard champion. The media and Take Index are reused (so a 4K source stays at the 1080p30 mezzanine), and the first
pass is not paid again.

**What it keeps and what it gives up.** It keeps the verbatim Take Index, retake clusters, gap snap points,
crossfades and room tone, the face-aware caption placer, the measured voice chain, loudness, the CutDocument and
oplog (so chat, `studio qa` and the upgrade are resumes), and all ten invariants. It gives up any review of the
rendered file: taste defects can ship, which is what "NOT REVIEWED: standard tier" says. It also gives up a heard
pacing pass, b-roll, cards, music, SFX, looks, variants and pairwise judging.

**Status (2026-10-12).** Every stage above exists as a module and is keyless-tested: the ingest conform, the digest,
the planner, the code plan, the render profile and repair. Phase C is being wired as this is written. It covers
`_edit_standard`, which chains the stages and passes the ingest caps, `_chat_standard`, and the upgrade. Nothing has
run against a real model or real ASR. `scripts/plan_eval.py` compares the code plan with the Director's delivered
documents offline (HANDOFF §6c).
