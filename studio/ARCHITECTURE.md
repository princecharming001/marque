# Yunicorn Studio — engine contract (v1)

This document is the shared contract every module is built against. The full rationale lives in
`/Users/home/URAP - Lead - Levine/reports/Yunicorn Studio plan.md` (the approved plan) and the verified
research in `/Users/home/URAP - Lead - Levine/reports/Yunicorn Studio design inputs/`. When this
contract and the plan disagree on an interface, this contract wins; when they disagree on craft, the
doctrine in `skills/editing/` wins.

**Only goal: the quality of the final video.** Ignore cost and latency. Never trade quality for speed
in the default path. (Previews may be faster; finals may not.)

**Standalone.** Studio is not wired into the app or the old backend (`backend/`). Do not import from
`backend/` or modify anything outside `studio/`. The old static engine is frozen at git tag
`static-engine-v1`.

---

## 0. Ground rules

1. **Code perceives, measures and enforces; models judge and decide.** A model never produces a
   timestamp that becomes an edit coordinate. Models address **word IDs** (`w0001`…), **gap IDs**
   (`g0001`…), **sentence IDs** (`s001`…), **take-cluster IDs** (`c01`…), **insert IDs** (`i001`…) and
   **segment IDs** (`seg001`…). Code converts IDs to times using measured boundaries.
2. **One writer.** Only the Director's ops mutate the CutDocument, through `studio.doc.ops.apply_ops`.
   Specialists and critics return proposals/notes, never documents.
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
- Output frame rate = source frame rate (29.97/30/59.94/60 preserved; VFR conformed to nearest CFR).

## 3. Ingest (`studio.media`)

`ingest(src_path, job) -> MediaInfo` produces in the job dir:
- `media/original.<ext>` (hard link or copy; never modified)
- `media/probe.json` (ffprobe), `MediaInfo`: width/height after rotation, display aspect, fps
  (Fraction), vfr flag, duration_us, color (primaries/transfer/matrix/range; `hdr: bool`), audio
  (codec, sample rate, channels), rotation, edit-list/priming notes.
- `media/mezz.mov` — the **mezzanine**: rotated upright, SDR BT.709 (HLG/PQ tone-mapped exactly once
  with zscale+tonemap, 203-nit reference white, dither), CFR at source fps, full resolution,
  ProRes 422 HQ (or FFV1) — every later video operation reads this, never the original. When the disk budget
  (`studio.storage`: the mezzanine *plus* the edit's renders) does not fit, the codec steps down ProRes 422 →
  the lean intermediate (HEVC 4:2:2 10-bit VideoToolbox q85, x264 4:2:2 10-bit CRF 4 fallback; ≥ 56 dB PSNR).
  A delivered (or idle) job's mezzanine is regenerable: it may be reclaimed and is rebuilt frame-identically
  from `media/original.*` before the next render (`storage.ensure_mezz`).
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
   (full / split / pip / card-underlay), output 1080x1920 @ source fps, ProRes 422 HQ intermediate (the lean
   intermediate when the disk budget says so; recorded as `aroll_codec` in `render.json`).
2. **Overlays** — Remotion project `studio/overlay/` renders captions, text overlays, designed cards
   and graphics from `overlay_props.json` as ProRes 4444 with alpha (bt709, png intermediates), same
   fps/duration.
3. **Audio** — dialogue segments cut from `audio.wav` with 10–30 ms equal-power crossfades and
   room-tone fill; speed via `rubberband -3` (pitch preserved); voice chain (pedalboard: HPF, EQ,
   de-ess, compression, optional denoise/isolation); music bed (ducked under speech by sidechain
   envelope), SFX; loudness normalize to target with a 4x-oversampled true-peak limiter. Outputs
   `mix.wav` and `mix_nomusic.wav` (48 kHz).
4. **Master** — ffmpeg composite A-roll + overlays + audio → `final_<platform>.mp4`: x264 High
   8-bit 4:2:0 BT.709 tagged, CRF 14–16, preset veryslow (preview: faster preset), `+faststart`, AAC
   ≥256 kbps 48 kHz; also `final_nomusic.mp4`, `cover.jpg`, `captions.srt`.
Preview renders use the proxy and a fast preset; the champion and finals always use full quality.

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

## 8. Job directory

`$STUDIO_WORK_DIR/<job_id>/` (default `/Users/home/studio-work/`):
`media/`, `index/`, `doc/`, `renders/`, `critique/`, `assets/` (b-roll, music, sfx with licence
json), `logs/`, `trace.jsonl` (every model call: role, provider, model, tokens, latency, tool calls),
`report.md`.

## 9. Agents (`studio.agent`)

- Providers (`providers.py`): `ModelSpec{provider: anthropic|openai|google|openai_compat, model,
  api_key (in-memory), base_url, capabilities{vision, video_input, strict_tools}}`; house default
  Anthropic; BYOK supplies a spec for the **Director** role; critics stay house. Built on
  pydantic-ai native model classes.
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
  champion the critics could not review ships marked NOT REVIEWED.

## 10. CLI (`studio.cli`, entry point `studio`)

- `studio edit <video> [--brief TEXT] [--style NAME] [--platform tiktok|reels|shorts]
  [--director-provider P --director-model M --director-key-env VAR | --house] [--rounds N] [--out DIR]`
  → runs everything, prints the job dir and final file paths.
- `studio chat <job_dir> "<instruction>"` → Director applies ops for the instruction, re-renders,
  re-judges, prints what changed.
- `studio index <video>`, `studio render <job_dir> [--version N] [--preview]`, `studio report <job_dir>`,
  `studio qa <job_dir>`.
