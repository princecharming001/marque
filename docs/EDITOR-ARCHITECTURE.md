# Editor + long-video architecture map

Written 2026-09-24 at the start of the editor audit, before any code change (base `b27624a` on
branch `editor-audit`). Line numbers refer to that base. Prod flags at the time:
`EDL_AUTHOR=plan`, `RETENTION_PASSES=all,framing,hook_pack,jitter,cold_open,dropout,beat_snap`,
`SELF_REVIEW=1`, `AUDIO_FINALIZE=1`, `VIDEO_UNDERSTANDING=off`, `BROLL_CANDIDATES=10`,
`REMOTION_FUNCTION_NAME=remotion-render-4-0-484-mem3008mb-disk2048mb-240sec`, one 512 MiB / 0.5 CPU
Render instance, one uvicorn worker.

## 1. The pipeline end to end

```
iOS capture/import ─▶ MediaCompressor ─▶ POST /v1/uploads/mint ─▶ PUT signed URL (Supabase Storage)
   ─▶ POST /v1/clips {analyze_first, auto_confirm} ─▶ transcribe (AssemblyAI) ─▶ analyze (brief)
   ─▶ edit (plan LLM → assemble_edl → retention passes → b-roll resolve → self-review)
   ─▶ render (build_render_plan → node bridge → Remotion Lambda) ─▶ finalize (loudnorm, poster)
   ─▶ ready ─▶ iOS polls, caches the render ─▶ editor (GET ?include_words=1, local preview)
   ─▶ Save = POST /tweak {ops} ─▶ re-render ─▶ store-owned watcher ─▶ versions / share / post
```

## 2. Clip job states (server, `backend/main.py`)

| Status | Set at | Owner | Terminal? |
|---|---|---|---|
| `processing` | one-tap create 3900, retry 4135, resume 5297 | server | no |
| `transcribing` | `_transcribe_job` 5572, retry 4138, resume 5303 | server | no |
| `analyzing` | analyze-first create 3919, after transcribe 5648, resume 5300 | server | no |
| `brief_ready` | `_run_analysis` 5676 (waits for POST /confirm) | server (user action) | yes (for the sweeper) |
| `editing` | confirm 4077, `_run_edit` 6055, resume 5294 | server | no |
| `rendering` | `_run_edit` 6726, retry 4129 | server | no |
| `ready` / `failed` | 6735, 4153, `_fail_job` 5310-5319, re-attach 3377-3380 | server | yes |
| `mock_ready` | keyless only (3895, 4066, demo 4724) | server | yes |

Per clip: `status` queued→…→rendering→ready|failed, `render_gen` (stale-writer guard 5322-5336),
`render_id`/`bucket_name`/`render_total_frames`/`render_budget_s` (persisted right after submit
7437-7446), `render_url`, `thumbnail_url`, `last_render_failed`, `warnings[]`, preview fields.
Job-level: `words`, `edit_brief`, `edl`, `edl_history` (25 in memory / 5 durable), `tweaks` (20),
`pipeline_gen` (ownership 5339-5352), `stage_started_at`, `resume_count`.

## 3. Ownership and durability

| State | Lives in | Survives app kill | Survives backend deploy/restart |
|---|---|---|---|
| Server job | `_clip_jobs` (3247) + Supabase `clip_edit_sessions` JSONB (upserted on every stage change, 3264-3299) | yes (server) | yes: lazy `_restore_clip_job` (3301-3344) on any route; non-terminal jobs resumed by `_liveness_sweeper` (every 60s, 5509-5553) up to 2 resumes/job; in-flight Lambda renders re-attached by render_id (3347-3398) |
| Owning task registry, render semaphore, idempotency cache (900s), b-roll caches | memory only | n/a | no |
| iOS clip list (`Clip`: status, pipelineStage, jobId, remoteURL, renderHistory ≤10) | UserDefaults snapshot (AppStore.swift:3028-3111; 1s debounce, flushed on background) + Supabase snapshot every ≤10s | yes | yes (client re-polls; 404/410 ⇒ `job_expired`) |
| Upload journal (queued→compressing→uploading→putComplete→jobCreated / failedRetryable) | `Application Support/uploads/journal.json` (UploadJournal.swift) | yes: `reconcileUploads` (AppStore.swift:1629-1706) re-attaches live transfers, finalizes putComplete, else re-uploads | n/a |
| Background PUT | background URLSession (BackgroundUploader.swift) | yes if crashed/jetsammed; **no if force-quit** (system cancels) | n/a (goes straight to storage) |
| Editor draft (EditorSession committed/draft/undo/redo) | `@State` in ProEditorView (PEV:32) | **no — not persisted** | n/a |
| Tweak/retheme re-render watcher | store-owned Task (AppStore.swift:2380-2424) | no (a relaunch re-poll resolves it without notification) | 404 ⇒ loop stops, clip left `.rendering` |

Known durability gaps found by this audit: `clip_edit_sessions.updated_at` is never refreshed
(no trigger; upsert sends only `job_id,state`) so the stale-orphan scan keys on creation time;
a restart before `render_id` is persisted leaves a clip that only a manual retry recovers;
a resume from `analyzing`/one-tap re-buys transcription.

## 4. Editor session (iOS, `ios/Marque/Features/Editor`)

`ProEditorView` phases: `.loading` → (GET ok) `.editing` → Save → `.applying` → needs render ⇒
`setClipRendering` + store watcher + dismiss / else dismiss. `.failed` from load or any
non-transient save error. `.rendering` only for retheme (view-owned 60×5s poll).
Preview = one AVPlayer on the raw take (else `source_url`) seeking through kept intervals +
SwiftUI overlays; music on a second unsynced AVPlayer. Undo/redo = unbounded full-document
snapshots, destroyed on dismiss. Entry points: Library clip → ClipDetailSheet → "Edit manually"
(LibraryView.swift:735-740), tweak chat capsule (685-703), `-demoClip`.

## 5. Every cap, timeout, poll, TTL and size limit on the long-video path

Server (`backend/main.py` unless noted):

| Limit | Value | Where |
|---|---|---|
| MAX_UPLOAD_BYTES (advertised to the client at mint) | 150,000,000 | 143, 3436, 3491 |
| **Real object limit (Supabase project, measured)** | 52,428,800 (50 MiB); bucket says 256 MiB but is not binding | dashboard setting |
| Signed upload TTL (echoed only) | 7200 s | 3233 |
| Source probe | 5 s × 3 | 5084, 5355-5381 |
| AssemblyAI submit / poll | 30 s × 3 / every 5 s, `TRANSCRIBE_MAX_S`=300 (flat), 6 blips | 7508-7542, 5085, 7587-7628 |
| Loudness probe / silencedetect | 60 s each, full video decode (no `-vn`) | app/audio.py:19-44, 60-92 |
| Anthropic client | httpx timeout 90 s, 4 attempts (0.5/2/8 s) | 1236, 1273-1338 |
| Brief / plan / legacy EDL max_tokens | min(6000,1600+2.5w) / min(16000,3000+4w) / min(32000,4000+12w) | 4851-4876 |
| Verify / repair | Haiku 900 tok, EDL cut to 6000 chars / Sonnet 4000 tok | 7694-7706, prompts.py:865,885 |
| Tweak LLM | Sonnet 1000 tok, transcript `words[:800]` | 4288-4293, prompts.py:1031-1035 |
| B-roll | 3 cues concurrent per job, ≤10 candidates, ≤8 vision calls/round, ≤3 rounds, Higgsfield 40 s | 10209, 9822, 10372-10454 |
| Self-review | full-length half-res preview render + ≤10 frames | 6762-6763, 6977-7052 |
| Render slots | 3 per process (held through submit + poll) | 5125-5126 |
| Render submit / poll call | 90 s / 30 s, 3 failures fatal | 5100-5103, 5088 |
| Render poll budget | min(1200, 240 + 0.12 s/frame) (flat above 8000 frames) | 5094-5117 |
| Render stall | min(225, 75 + 0.03 s/frame) | 5087, 5113-5116 |
| Per-clip render watchdog | max(480, render budget) from `render_started_at` (stamped before queueing) | 5089, 5402-5434, 7416-7432 |
| Job watchdog | 2 × 480 = 960 s per stage, applied even to live pipelines | 5446-5496 |
| Liveness sweeper | 60 s, grace 30 s, ≤2 resumes/job, ≤2 per sweep call, scan >300 s, 30-row window, 7-day floor | 5264-5268, 5389, 5509-5553; supabase_persistence.py:445-471 |
| Loudness finalize (AUDIO_FINALIZE) | pass 1 180 s, pass 2 90 s (children not killed), S3 upload no timeout | 7176-7267 |
| Poster | 3 seeks × 30 s, 45 s upload, 8 s inline wait | 7316-7375 |
| Job TTL / idempotency | 24 h from last activity / 900 s, cap 2000 | 1714-1754, 3762-3784 |
| ETA | stage baseline × min(3, dur/60), floored 20 s | 5206-5224 |
| Remotion Lambda | 3008 MB, disk 2048 MB, 240 s per function (whole render must finish inside the launch function); framesPerLambda default (≤150 renderers); per-frame delayRender 28 s; props >194 KB auto-offloaded to S3 | render/src/lambda-render.ts:50-64; REMOTION_FUNCTION_NAME |
| Music loop automation | `<Audio loop>` volume callback gets loop-local frames (default "repeat") | render/src/components/AudioMix.tsx:139-143 |

iOS (`ios/Marque`):

| Limit | Value | Where |
|---|---|---|
| In-app recording | H.264 12 Mbps + AAC 96k mono (~90 MB/min), no max duration, no free-space check | Features/CameraModel.swift:216-256 |
| Upload cap fallback | 48,000,000 if mint omits it | Adapters/LiveClipEngine.swift:399 |
| Skip compression | when size ≤ cap | LiveClipEngine.swift:455-456 |
| HEVC transcode path | only takes ≤150 s; 3.8 Mbps (≤90 s) / 2.6 Mbps; floor 1.2 Mbps | 401, 472-485 |
| Export preset ladder (>150 s) | 1280×720 → 960×540 (measured ≈4.9 Mbps at 540p), 720p skipped when dur×2.5 Mbps/8 > cap | 489-515 |
| Compression budget | clamp(0.55 s/MB, 240, 420) s; HEVC deadline 0.6×budget; per-export min(180, max(45, remaining)) | 440-443, 482, 510-512 |
| Background PUT | resource timeout 20 min; foreground stall watchdog clamp(60+0.2 s/MB, 60, 240); 4 connections/host | Adapters/BackgroundUploader.swift:66-74, 177-197 |
| Retry policy | 6 attempts/call, 10 lifetime (never reset); 400/404/409/413 fatal; 403 re-mint | Adapters/UploadRetryPolicy.swift:10-66 |
| Submit ceiling | min(900, 480 + compression budget) s | State/AppStore.swift:1331-1336 |
| Job poll | 5 s, then 10 s after 5 min; **20 min wall clock** ⇒ `edit_timeout` | AppStore.swift:1792, 1857-1885 |
| pollForBrief | 360 s | AppStore.swift:1173-1183 |
| Tweak watcher / retheme / tweak chat | 120×5 s ≈10 min / 60×5 s / preview 40×3 s, render 60×5 s | AppStore.swift:2392; ProEditorView+Actions.swift:935; TweakChatSheet.swift:299,339 |
| Render cache | ≤200 MB, checked after the full download | AppStore.swift:2455, 2593-2604 |
| Editor load GET | 30 s, no retry | Adapters/BackendClient.swift:69-78 |
| Timeline zoom | 6…110 pt/s (≤ ~55 s visible); filmstrip 8/12/20/32 thumbs per clip | Features/Editor/EditorTimeline.swift:740, 863-873 |
| Filmstrip cache | NSCache 24 MB (bypassed by per-cell @State) | FilmstripCache.swift:29; EditorTimeline.swift:861 |
