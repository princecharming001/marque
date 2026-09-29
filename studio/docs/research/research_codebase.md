**Engine-split map for `/Users/home/Marque-wt/editor-audit` (branch `feedback-0925`)**

Paths are relative to the repo root. `main.py` means `backend/main.py`.

## 1. Preserving the current engine and routing jobs

**Where a job enters (all in `main.py`)**
- `POST /v1/clips` (3830) calls `_create_clip_job_impl` (3852). It returns 426 unless `analyze_first` is set. It builds the job dict at 3894–3920.
  - With `auto_confirm`, it spawns `_run_auto_pipeline` (6060) at ~3970.
  - Otherwise it spawns `_run_analysis` (6040), which stops at `brief_ready`.
- `POST /v1/clips/{id}/confirm` (4108) spawns `_run_edit` at 4142.
- `POST /v1/clips/{id}/retry` (4148) runs `_retry_render`, `_run_auto_pipeline` or `_run_pipeline` (6092), at 4194–4203.
- These operate on an existing EDL: `/tweak` (4300, applies ops through `apply_edl_ops`, `app/edl.py:1427`), `/retheme` (4519), `/suggested-edits` (4233).
- All authoring paths meet in `_run_edit` (6462). The render tail is `_render_all_clips` (7850, called at 7151), which calls `_submit_remotion_render` (8256), which calls `_run_render_bridge` (8193).

**The `EDL_AUTHOR` flag**
- Defined at `main.py:6124` as `plan | legacy | shadow`, default `legacy`. It is read once at import, so it applies to the whole process, not per job.
- The branch is at 6608: `plan` goes to `_author_edl_via_plan` (6241); everything else takes the legacy structured-output path (6620–6690).
- `shadow` (6711) ships the legacy edit and fires `_log_shadow_diff` (6148) in the background. That is a ready-made pattern for comparing a new engine against the old one without user impact.
- Other env flags follow the same style: `EDIT_LINT`, `AUDIO_FINALIZE`, `VOICE_POLISH`, `EDIT_THEMES` (6129–6147), and the default-off flags in `app/palo_flags.py`.

**A clean switch point**
- Resolve the engine once at create time and store it on the job, e.g. `job["engine"]`, in the dict at 3894. The whole job dict is persisted to Supabase (`_persist_clip_job` 3323, `_restore_clip_job` 3360), so the choice survives restarts.
- Branch at the spawn sites: create (auto ~3970, analyze ~3985), confirm (4142) and retry (4194–4203). `/tweak` and `/retheme` also need a guard or a new-engine version.
- `_run_edit` (6462) is the narrowest single point if the new engine reuses the current transcribe and brief phase.
- For per-user routing: `creator_id` is set by the client (`ClipJobRequest` at 1661; the backend has no auth). Existing gating helpers to copy:
  - `STRATEGY_ALLOWLIST` (`app/ai_usage.py:54`)
  - `tiers.tier_for()` (`app/tiers.py`)
  - `/v1/dev/tier` (287)

**Git state**
- Only two tags exist: `redesign-base` (5e9c6d0) and `backup-upload-liveness-v2-pre-filter` (231304e). Neither marks the current engine.
- `HEAD` is `origin/main` at 1c07c18 ("Build 91").
- Local `main` is stale and has diverged (205 commits not on HEAD, 527 behind).
- The main checkout at `/Users/home/Marque` is on `redesign-stoic`.
- Many feature branches exist (`retention-editor-upgrade`, `engine-prompts`, `editor-audit`, `backup-editor-audit-*`, etc.).
- To make the current engine revertible, tag it (e.g. `static-engine-v1` at 1c07c18).

## 2. The Remotion renderer (`render/`)

**Compositions** (`render/src/Root.tsx`, all 1080×1920 at 30fps, length set from `total_frames`)
- Render targets: `Marque-TalkingHead`, `-Faceless`, `-SplitThree`, `-FastCuts`, `-GreenScreen`, `-BrollCutaway`, `-DuetSplit`, `-SourcePip`.
- Preview-only (not used for real jobs): `Marque-CtaPreview` and `-CtaPreviewV2`.
- The ID comes from the style name at `main.py:8268`: `Marque-{style.title().replace('_','')}`. A duet with a PIP speaker treatment switches to `source_pip`.

**Input props** (`render/src/types.ts`)
- The shape is `{sourceUrl, edl: RenderPlan, formatId}`.
- `RenderPlan` contains: `clips` (source-frame ranges plus speed and transform), `captions`, `overlays`, `broll`, `layout`, `caption_style`, `caption_options`, `transitions`, `look`, `audio` (music, `volume_ranges`, `speech_frames`, `sfx`, `duck`, `room_tone`, `gain`), `end_card`, `progress_bar`, `watermark`, `montage`, `react_source`/`react_schedule`, `total_frames`, `schema_version`.
- The backend builds it with `build_render_plan` (`app/edl.py:910`). `PLAN_SCHEMA_VERSION = 8` must match in `edl.py:21` and `types.ts`.
- `pyRound` means the frame rounding must match Python's `round()` exactly.

**Visual features**
- **Captions** (`Captions.tsx`):
  - Styles: `clean`, `bold-word`, `karaoke`.
  - Fonts: Inter, Archivo Black, Baloo 2, Montserrat, Anton, via `@remotion/google-fonts` (lines 3–7).
  - Options: position, size, uppercase, grouping (word/phrase/line), highlight words, stroke, background pill, sync lead.
- **B-roll** (`BrollLayer.tsx:48`): modes `full`, `panel`, `card`, and `smart` (face-aware inset via `app/faces.py:131`). Includes Ken Burns for stills and KLIPY/GIPHY attribution badges.
- **Punch-ins**: `PunchZoom.tsx`, eased, capped at 1.2×.
- **Transitions** (`Grade.tsx:58–65`): `fade_black`, `fade_white`, `flash`, `whip`, `zoom_punch`.
- **Looks**: there are no LUTs. Looks are CSS-filter presets (`CutVideo.tsx:87–95`: vivid, film, mono, golden, warm, cool, finishing) plus adjust knobs, vignette and grain (`Grade.tsx`).
- **Text**: stickers (`TextStickers.tsx`), text cards (`TextCardOverlay.tsx`), listicle montage (`MontageIntro.tsx`).
- **End cards and CTAs**: `EndCard.tsx` plus a 20-template library (`components/cta/cta_styles.json`: 6 tail cards, 14 overlays; `registry.tsx`).
- **Other**: progress bar, watermark.
- **Audio** (`AudioMix.tsx`): music bed with speech-driven ducking, ramps, fades and punchline dropouts; SFX one-shots; looped room tone.
- **Loudness**:
  - Per-source gain toward −14 LUFS (`app/audio.py:17`, `CutVideo.tsx:117`).
  - Optional two-pass loudnorm after render (`AUDIO_FINALIZE`, `_finalize_audio_loudness` `main.py:7632`), plus `VOICE_POLISH` and `VOICE_ENHANCE` (fal DeepFilterNet3).

**Deployment**
- Site name `marque-render` (`package.json` `deploy:site`), region us-east-1.
- The Lambda function is pinned to version 4.0.484 (Dockerfile uses `npm ci`).
- Bucket `remotionlambda-useast1-f1312k4kb3` (`main.py:7711`).
- The backend shells out to `render/dist/lambda-render.js` (submit/poll; props go in on stdin; the preview mode is half scale at CRF 30).
- Env vars: `REMOTION_SERVE_URL`, `REMOTION_FUNCTION_NAME`, `REMOTION_AWS_*` (`main.py:3295–3299`).
- Risk: redeploying the same site name overwrites what legacy renders use. A new engine should deploy under its own site name and serve URL.

**What a new engine can reuse**
- Directly reusable:
  - the Lambda bridge (`lambda-render.ts`) and the Python submit/poll/stall handling (8193–8355)
  - `AudioMix`, `Captions`, `BrollLayer`, `PunchZoom`, `Grade`, the CTA registry, `EndCard`, `TextStickers`, `Watermark`, `ProgressBar`
  - `app/audio.py` (loudness) and `app/faces.py` (face box)
- Tied to the current engine: `CutVideo` and the per-style compositions, which depend on the `RenderPlan` source-to-output remap.
- The cheapest option is for the new engine to emit the same `RenderPlan`. That reuses the whole renderer as-is.

## 3. Asset libraries

- **Music**:
  - `_BUILTIN_MUSIC_TRACKS` (`main.py:3578–3602`): 8 beds (7 SoundHelix examples plus 1 Google codeskulptor demo mp3), tagged by vibe, tone, bpm and energy.
  - Override with the `MUSIC_CATALOG` env var or the Supabase `music/` bucket. Served by `GET /v1/music` (2452).
  - The iOS copy is at `ios/Marque/Features/Editor/MusicCatalog.swift:14`.
  - Licensing: the comment calls them "royalty-free", but there is no license file, and SoundHelix examples and codeskulptor assets are not clearly cleared for commercial use.
- **SFX**:
  - `SFX_ASSETS` (`main.py:3740`), 10 kinds, stored at `supabase …/marque-clips/sfx/*.mp3` and overridable with `SFX_URL_*`.
  - Licenses noted in the comments: whoosh and pop are Mixkit Free License; hit is Kenney CC0.
  - typing, click, shutter, sparkle and riser have no license note.
  - `fahh` and `sus` are switched off because their provenance is proprietary.
- **Fonts**:
  - Renderer: the Google Fonts listed above (OFL).
  - iOS bundle (`ios/Marque/Resources/Fonts`): Inter, Matter (a commercial foundry font; no license in the repo), Fraunces, Playfair Display.
- **Themes**: `app/themes.py`, six bundles (`clean_creator`, `hormozi_punch`, `docu_calm`, `energetic_pop`, `faceless_explainer`, `premium_brand`). Gated by `EDIT_THEMES`; served by `/v1/themes` (2429).
- **LUTs**: none (no `.cube` files).
- **Other**:
  - `backend/assets/`: `style_deck.json`, `style_samples.json`, `style_archetypes.json`, `cta_previews.json`, `cta_deck_v2.json`, `face_detection_yunet_2023mar.onnx`.
  - Demo clips at `marque-clips/demo-assets/composition-styles` (`main.py:3062`).
  - Craft docs under `backend/knowledge/`.
- No LICENSE files exist anywhere in the repo.

## 4. External services already wired in

- **Anthropic**: `main.py:115–116`, raw calls with `x-api-key` at 1347, 7346, 7397, 9722, 9818, 10191, 10264; `anthropic_json` at 1427; `app/dossier.py:296`; `app/palo_llm.py:26,117`.
- **AssemblyAI**: `ASSEMBLY_KEY` at 3294; submit at 7978 and poll at 8050. Without a key, jobs fall back to mock mode.
- **Pexels**: key at 123, search at 9948.
- **GIPHY**: key at 131, search at 9976.
- **KLIPY**: key at 132; clips and gifs search at 10001; trending at 10440. `BROLL_MEMES` flag at 133.
- **Wikimedia Commons** (CC0/public-domain stills only): 10081.
- **Higgsfield**: `app/higgsfield.py:27–41` (`HIGGSFIELD_KEY` or `_KEY_ID`/`_KEY_SECRET`), capped per job at `main.py:134`.
- **fal**:
  - Flux Pro stills: `FAL_KEY` at 10078, call at 10141.
  - DeepFilterNet3 denoise: `app/enhance.py:22–24`.
- **Supabase**:
  - URL and service key: 135–136. Bucket `marque-clips`: 3289.
  - Signed upload mint: 3480–3525. Multipart upload through its S3-compatible API (boto3): `app/multipart.py:36–85`.
  - Job persistence: 3323/3360. Media rehost: 12625. Posters and audio: 7569–7619, 7830.
- **AWS S3** (Remotion bucket, boto3): 7711–7718.
- **Also present**:
  - TwelveLabs (`dossier.py:30`)
  - OpenAI embeddings (`memory_v2.py:53`)
  - ElevenLabs/Cartesia (11963)
  - Apify (122, 8407)
  - PostForMe (120)
  - APNs (`push.py:23`)
- Deploy config: `render.yaml` (Render web service `marque-api`, Docker).

## 5. What the iOS app needs from any engine

**Creating a job**
- `LiveClipEngine.createAnalyzeJob` (`Adapters/LiveClipEngine.swift:842–918`) calls `POST /v1/clips` with:
  - `analyze_first=true`, `auto_confirm`, `toggles`, `edit_format`, `theme_id`
  - `config` (`is_pro`, `broll_coverage`, `cta_style_id`, `composition_style`)
  - `reference_reel`, `corpus`, `react_source_url`, `creator_id`, `edit_prefs`
  - an `Idempotency-Key` header
- The response is decoded as `AnalyzeJobResponse` (`Models/Models.swift:1440`): `job_id`, `status`, `mode`, `edit_brief`, `toggles`, `error`, `clips[{clip_id, format, status}]`, `eta_seconds`.
- Related calls: `/confirm` (1016), `/retry` (1058; status codes handled in `JobPolicies.swift:41`), `/retheme` (992; handles 404/409/410/422/503).

**Polling**
- `pollClipJobWithStatus` (1039), called from `State/AppStore.swift` at 1628, 1850, 1979 and 2582.
- The app reads `clips[].clip_id`, `status`, `render_url`, `thumbnail_url`, `error`, `error_detail`, `warnings`, plus the job-level `eta_seconds` and `error`.
- A 404 or 410 means the job is gone.
- Statuses the app handles: transcribing, analyzing, brief_ready, processing, editing, rendering, ready, failed, mock_ready.

**The in-app editor depends on the EDL**
- `ProEditorView+Actions.swift:18` polls with `include_words=1`.
- `EditorModel.swift:148+` parses the source-coordinate EDL: segments, drops, captions, overlays, `broll` with `inset_rect`, music, `volume_ranges`, transitions, look, `segment_order`, `speech_frames`.
- `LocalEDLEngine.swift` replays `WireOp`s locally for instant preview, using the op set in `TWEAK_OP_TYPES` (`app/edl.py:1287`).
- Save sends `POST /tweak` with the ops (`ProEditorView+Actions.swift:1110`; `?preview=1` at `LiveClipEngine.swift:1130`). Chat tweaks go through `TweakChatSheet.swift:304`.
- The editor also uses `source_url` for rough-cut preview.
- So a new engine must either emit the same EDL model (`edl.py:407`) and accept those ops, or the editor must be hidden for new-engine jobs.
- Also fetched: `/v1/editor/capabilities`, `/v1/themes`, `/v1/music`, `/v1/cta-styles`, `/v1/broll-styles`, `/v1/style-deck`.

**Where a "bring your own AI key" setting could live**
- The iOS app has no Keychain usage today. The only Security API call is `SecRandomCopyBytes` at `AuthManager.swift:237`.
- Auth tokens are stored in UserDefaults under `marque.auth.v1` (`AuthManager.swift:28, 252`).
- Candidate screens:
  - `Features/SettingsView.swift`: a `YOUR ACCOUNT & APP` section at :37, built with the `row()` (:403) and `DSSection` (:430) helpers.
  - `Features/Profile/EditingStyleSheet.swift` (`YOUR SIGNATURE CUT`, :98), whose `store.editPrefs` flows into `BackendClient.editPrefs` (`BackendClient.swift:28`) on every job.
- A key would need a new Keychain wrapper and a request header from `BackendClient`.
- On the backend, every Anthropic call reads the global `ANTHROPIC_KEY` (sites listed in section 4), so each would need to accept a per-request key. The backend has no auth, so a user-supplied key would have to be handled carefully.