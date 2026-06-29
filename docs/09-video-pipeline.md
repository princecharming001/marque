# 09 · Video Capture, Processing & Rendering Pipeline

> **Scope.** This document specifies how Marque turns one batch talking-head recording (or one uploaded long video) into many platform-ready, publish-compliant vertical clips. It covers the full chain: on-device capture (AVFoundation), resumable background upload, storage and delivery (Cloudflare R2 + Stream), transcription and word-timings (AssemblyAI), moment/cut detection, the MCP creative toolchain (`personal_clipper` + `reframe` + AI-visual generation), templated rendering of format recipes via Shotstack, faceless AI-visual composites per beat, durable job orchestration (Trigger.dev), cost/latency budgets, output QA, and the v1-on-MCP → in-house migration path.
>
> **What lives elsewhere.**
> - The **orchestration *reasoning*** — which model picks the N best beats, maps them to format recipes, and writes per-beat visual prompts — is owned by the AI System (`07-ai-system.md`, §2.5 Clip Engine orchestration). This document owns the *mechanics* that reasoning drives.
> - **Format recipes** (the structured render-recipe definitions: split-screen, 3-up, green-screen, faceless, before/after, myth-buster, listicle, POV, reaction, B-roll+hook) are catalogued in the Format Library (`08-format-virality.md`). This document specifies how a recipe *compiles to* a Shotstack render and an MCP job graph.
> - **Publishing + scheduling** (Ayrshare → Instagram Graph API / TikTok Content Posting API, privacy selection, scheduling) is owned by `10-social-publishing.md`. This document owns producing a **compliant master + public URL** that the Publisher adapter consumes.
> - The **`clips` row state machine, Supabase schema, RLS, and Edge Functions** are owned by the Data Model (`12-backend-data-security.md`); reproduced here only where the pipeline drives a transition.
> - **Capture UI / Record + Produce screens** are owned by `04-screens-create.md` and `05-screens-produce.md`; this document owns the capture *specs and engine*, not the layout.
> - The **aesthetic doctrine** — cream surfaces, one idea per screen, gold accent, slow breathing motion, never a bare spinner — is owned by `02-design-system.md` and is inherited by every progress and error surface below.

---

## 1. Design principles

1. **Bytes never touch our backend.** The app uploads video **directly to Cloudflare Stream** via a one-time URL minted by a Supabase Edge Function. FastAPI and Supabase Postgres orchestrate *product rules and state*; Stream and R2 own *bytes*; Trigger.dev owns *long jobs*. We never proxy a video stream through an application server. This is the proven Cloudflare Stream + Supabase split ([Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/)).
2. **Every vendor sits behind an adapter.** `ClipEngine` (MCP toolchain), `Renderer` (Shotstack), `Transcriber` (AssemblyAI), `BlobStore` (R2/Stream), `Publisher` (Ayrshare). Swapping any vendor is a one-file change — this is what makes the §12 in-house migration tractable.
3. **Webhooks are the only triggers.** Processing starts when Stream confirms ingest, advances when AssemblyAI/Shotstack/MCP call back. We **wait on tokens, we do not poll-loop** inside durable runs. Every inbound webhook is signature-verified *before* any Postgres write.
4. **Idempotency on every billable step.** Transcription, each render, each AI-visual generation carries an idempotency key so a parent retry never double-bills.
5. **Never a bare spinner.** Long jobs are durable and surfaced as live progress with a real ETA and a stage label, consistent with the calm doctrine. The creator's only directive after recording is "we're cutting your week — we'll ping you," not a frozen screen.
6. **Produce a compliant master, always.** Every render is validated against the strictest publish target (Instagram Reels API: ≤90s, 9:16, H.264, AAC, faststart) before it is allowed to reach the Publisher. A non-compliant render is re-rendered or flagged, never published.

---

## 2. End-to-end pipeline shape

Six lanes. Each lane has a single owner and a single hand-off contract.

```
 ┌── CAPTURE (iOS / AVFoundation) ───────────────────────────────────────────┐
 │  record 1080×1920@30 H.264 .mov → disk (never memory)                      │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 │ (a) request upload URL
                 ▼
 ┌── UPLOAD (tus, resumable, background URLSession) ─────────────────────────┐
 │  Supabase Edge Fn mints one-time Direct Creator Upload URL (caps/kill)    │
 │  → app uploads CHUNKS direct to Cloudflare Stream (bytes skip backend)    │
 │  → Supabase inserts clips row @ `uploading`                               │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 │ (b) Stream finishes transcode → HMAC webhook
                 ▼
 ┌── INGEST CONFIRM (Supabase Edge Fn, HMAC-verified) ──────────────────────┐
 │  flip clips row → `ready`; trigger Trigger.dev parent task               │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 ▼
 ┌── ORCHESTRATION (Trigger.dev v3 · processBatchSession) ───────────────────┐
 │  1. transcribe (AssemblyAI, webhook → wait.forToken)                      │
 │  2. moment/cut detection (highlights + utterances + MCP virality)        │
 │  3. AI plan: pick N beats → map to format recipes  [calls 07-ai-system]  │
 │  4. batchTriggerAndWait → renderClip × N (parallel):                      │
 │       • MCP personal_clipper / reframe (source prep)                     │
 │       • faceless AI-visual gen per beat (idempotent, cached)             │
 │       • Shotstack render (recipe → JSON, webhook → wait.forToken)        │
 │       • output QA validator                                              │
 │  5. land master in R2 (+ Stream preview); register clip; notify          │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 │ public R2/Stream URL + compliant master
                 ▼
 ┌── PUBLISH (Publisher adapter → Ayrshare) ────────────────────────────────┐
 │  IG Graph Content Publishing / TikTok Content Posting  [see 11-publishing]│
 └──────────────────────────────────────────────────────────────────────────┘
```

**Hand-off contracts (the only inter-lane interfaces):**

| From → To | Contract |
|---|---|
| Capture → Upload | local `.mov` file URL + duration + byte size |
| Upload → Ingest | Stream `media-id`, `clips.id` |
| Ingest → Orchestration | `clips.id` at `ready`; signed source URL |
| Orchestration → Publish | R2 public URL + `RenderManifest` (codec/dims/duration/QA result) |

---

## 3. Capture (iOS / AVFoundation, iOS 17+)

The capture engine is a Swift Concurrency–driven `CaptureService` actor wrapping `AVCaptureSession`. The teleprompter and on-device guidance are pure SwiftUI overlays that must **never** block the capture queue.

### 3.1 Capture specs (v1 target)

| Parameter | v1 value | Rationale |
|---|---|---|
| Resolution | **1080×1920 (9:16)** | What IG Reels & TikTok both want; 9:16 required for the Reels tab ([Meta — IG Media/Reels specs](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media), [Phyllo — IG Reels API](https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api)) |
| Frame rate | **30fps, constant** | Safe across both platforms (IG accepts 23–60fps) ([Phyllo](https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api)) |
| Codec | **H.264** preferred output; capture may be HEVC | iPhones default to HEVC, which **fails silently in mobile Safari / some players** — we always transcode to H.264 downstream ([Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/)) |
| GOP | Closed | IG Reels requires closed GOP ([Phyllo](https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api)) |
| Recording API | **`AVCaptureMovieFileOutput`** | Auto-handles H.264 level/bitrate; don't hand-roll `AVAssetWriter` for v1 ([Apple — Media Capture](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/04_MediaCapture.html)) |
| 4K | **Opt-in only** ("max quality" toggle) — see Open Q4 | 4K 4×'s upload time/cost and gets downscaled; not the default |

### 3.2 Session configuration (exact, ordered)

The framerate-lock ordering is a known footgun — a preset reset silently overrides framerate set too early ([objc.io — Capturing Video](https://www.objc.io/issues/23-video/capturing-video), [Apple — Media Capture](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/04_MediaCapture.html)).

```swift
session.beginConfiguration()
session.sessionPreset = .hd1920x1080            // or InputPriority for activeFormat control

// 1. Add device input FIRST
let videoInput = try AVCaptureDeviceInput(device: camera)
session.addInput(videoInput)
session.addInput(audioInput)

// 2. Add the movie file output
session.addOutput(movieOutput)                  // AVCaptureMovieFileOutput

// 3. Lock the format, THEN the framerate (after input + format chosen)
try camera.lockForConfiguration()
camera.activeFormat = chosen1080p30Format       // from device.formats
let frameDuration = CMTime(value: 1, timescale: 30)
camera.activeVideoMinFrameDuration = frameDuration
camera.activeVideoMaxFrameDuration = frameDuration   // BOTH → constant FPS
camera.unlockForConfiguration()

// 4. Stabilization on the connection
if let conn = movieOutput.connection(with: .video),
   conn.isVideoStabilizationSupported {
    conn.preferredVideoStabilizationMode = .standard   // .cinematicExtendedEnhanced for cinematic
}
session.commitConfiguration()
```

**Rules:** set framerate **after** adding input and choosing the format ([objc.io](https://www.objc.io/issues/23-video/capturing-video)); setting `activeFormat` from `device.formats` auto-switches the session to `AVCaptureSessionPresetInputPriority` under `lockForConfiguration()` ([Apple — Media Capture](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/04_MediaCapture.html)). Stabilization mode follows WWDC25 guidance ([Apple WWDC25 — Capture cinematic video](https://developer.apple.com/videos/play/wwdc2025/319/)).

### 3.3 Teleprompter (hero-loop UX)

AVFoundation has no teleprompter primitive. Implement as a SwiftUI scrolling-text overlay **above** `AVCaptureVideoPreviewLayer`:

- Scroll speed tied to estimated WPM (creator-adjustable; default ~140 wpm).
- Translucent scrim behind text for legibility on cream/dark backgrounds (inherits `02-design-system.md` glassy scrim tokens).
- The prompter is **pure UI** — it runs on the main actor and must not enqueue work on the capture session queue.
- Source text = the approved `ScriptCandidate` for this beat (from `07-ai-system.md` Script Studio), with per-beat boundaries so the prompter can highlight the current beat.

### 3.4 On-device guidance signals

| Signal | How | Surfaced as |
|---|---|---|
| Face centering | Vision face-rectangle on a downsampled `AVCaptureVideoDataOutput` tap | Gentle gold center reticle that fades when centered |
| Low light | Simple luma average from the data-output tap (or scene-monitoring KVO if cinematic is adopted) ([Apple WWDC25](https://developer.apple.com/videos/play/wwdc2025/319/)) | "A little more light?" quiet hint |
| Countdown | 3-2-1 before record | Full-bleed numerals, slow ease |

### 3.5 Capture states

| State | Trigger | Behavior |
|---|---|---|
| **permission-denied (camera)** | `AVCaptureDevice.authorizationStatus(.video)` ≠ authorized | Calm explainer + deep-link to Settings; **camera and mic are two separate prompts** |
| **permission-denied (mic)** | `.audio` not authorized | Same pattern; block record (talking-head needs audio) |
| **low-storage** | Estimate bytes before record (~a few MB/s at 1080p30 H.264); compare to free space | Warn before record; suggest shorter batch |
| **interruption** | `AVCaptureSession.wasInterruptedNotification` (call, Control Center) | Pause prompter, hold partial file, resume on `interruptionEnded` |
| **thermal throttling** | `ProcessInfo.thermalState` ≥ `.serious` | Quiet banner; offer to drop to 1080p if 4K was on; never hard-crash |
| **offline** | Record always works offline; upload defers (§4) | Record completes; clip queues `uploading`, retries when connectivity returns |

---

## 4. Resumable / background upload (iOS → Cloudflare Stream)

### 4.1 Why tus, and when

Use **tus (resumable)**, not raw POST. Cloudflare Stream **requires tus for files >200MB**, and tus is strongly preferred even under 200MB on flaky networks. A single 60–90s 1080p clip is usually <200MB, but a **batch session ("film once → post all week") exceeds it** — so we **default to tus for every upload** ([Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/), [Cloudflare — Resumable uploads](https://developers.cloudflare.com/stream/uploading-videos/resumable-uploads/)).

### 4.2 tus chunk rules (exact)

| Rule | Value |
|---|---|
| Min chunk | **5,242,880 bytes (5 MB)** (unless the whole file is smaller) |
| Recommended chunk | **52,428,800 bytes (50 MB)** on reliable connections |
| Max chunk | **209,715,200 bytes (200 MB)** |
| Divisibility | Chunk size **must be divisible by 256 KiB (262,144)** — **except the final chunk** |
| Retry delays | `retryDelays: [0, 3000, 5000, 10000, 20000]` ms |

Source: [Cloudflare — Resumable & large files (tus)](https://developers.cloudflare.com/stream/uploading-videos/resumable-uploads/).

### 4.3 Direct Creator Upload flow

The Supabase Edge Function (`mint-upload-url`) mints a one-time upload URL so the app uploads straight to Stream and our backend never sees the bytes.

```
1. App → Edge Fn `mint-upload-url`  { duration, byteSize, sessionId }
   Edge Fn enforces caps + kill-switch, then:
2. Edge Fn → Cloudflare  POST /accounts/{id}/stream
      Tus-Resumable: 1.0.0
      Upload-Length: <bytes>
      Upload-Metadata: <base64 KV>   # name, maxdurationseconds, requiresignedurls, expiry, scheduleddeletion
3. Cloudflare → Edge Fn
      Location:        <one-time upload URL>   # READ FROM HEADER, not body
      stream-media-id: <video id>              # READ FROM HEADER, do NOT parse Location
4. Edge Fn inserts clips row @ `uploading` (stores media-id), returns upload URL + clips.id
5. App uploads CHUNKS to the upload URL via tus
```

**Gotchas (do exactly this):**
- The one-time upload URL is in the **`Location` response header**, not the body ([Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/)).
- Read the video ID from the **`stream-media-id` response header** — do **not** parse it out of the Location URL ([Cloudflare — Resumable uploads](https://developers.cloudflare.com/stream/uploading-videos/resumable-uploads/)).
- **`maxDurationSeconds` + `expiry` are mandatory** to provision the upload and only count on the **first/creation** request (ignored on subsequent chunk requests). Use `maxDurationSeconds` to cap abuse ([Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/), [Cloudflare API — Initiate TUS upload](https://developers.cloudflare.com/api/resources/stream/methods/create/)).
- Caps + kill-switch live in the Edge Function (per `12-backend-data-security.md`): max duration, max batch bytes, per-plan quota (`11-monetization.md`), and a global "pause uploads" flag.

### 4.4 iOS implementation: tus over a background URLSession

iOS 17 `URLSession` natively supports the IETF resumable-upload protocol draft — **but only if the server speaks that exact protocol**. tus is a *different* protocol, so we use a **tus client (e.g. TUSKit) running over a background `URLSessionConfiguration`**, which survives app suspension/termination ([Apple — Pausing and resuming uploads](https://developer.apple.com/documentation/foundation/pausing-and-resuming-uploads), [Apple WWDC23 — Robust and resumable file transfers](https://developer.apple.com/videos/play/wwdc2023/10006/)).

Background-session best practices ([WWDC23](https://developer.apple.com/videos/play/wwdc2023/10006/), [Apple Forums — fewer, larger transfers](https://developer.apple.com/forums/thread/14853)):
- Background sessions are optimized for **few, large** transfers — a batch is exactly this shape.
- `isDiscretionary = false` — a user-initiated "post my week" upload is **not** discretionary; they're waiting.
- Set `countOfBytesClientExpectsToSend` to help the scheduler.
- For manual pause/resume on a non-background task: `cancel(byProducingResumeData:)` → persist `resumeData` → `uploadTask(withResumeData:)`. On failure pull resume data from `error.userInfo[NSURLSessionUploadTaskResumeData]` ([Apple — Pausing and resuming uploads](https://developer.apple.com/documentation/foundation/pausing-and-resuming-uploads)).

**Do NOT** use `PHBackgroundResourceUploadExtension` / `createJob(destination:)` — it is fire-and-forget, single-request, can't chunk, and assumes one-URL ingest; wrong tool for tus ([Apple Forums — uploading large videos](https://developer.apple.com/forums/thread/818566)).

### 4.5 `clips` row state machine (upload + processing)

Owned by `12-backend-data-security.md`; reproduced for the pipeline contract.

```
uploading ──tus complete──▶ uploaded(stream)
                                   │ Stream transcode + HMAC webhook
                                   ▼
                              transcoding ──▶ ready
                                                 │ Trigger.dev parent
                                                 ▼
                                            processing ──▶ rendered ──▶ published
```

If tus exhausts retries, the row stays `uploading`, **no webhook fires**, and nothing downstream starts — a clean, observable failure ([Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/)). A reaper job surfaces stuck `uploading` rows to the creator with a one-tap retry.

### 4.6 Upload states

| State | Behavior |
|---|---|
| **uploading (progress)** | Live % via tus progress; app may background — transfer continues |
| **paused** | User backgrounds with no connectivity; resume token persisted; auto-resumes |
| **offline** | Queued; background session resumes on connectivity |
| **failed-after-retries** | Row stays `uploading`; calm "tap to retry" surface; resume from last chunk |

---

## 5. Storage & delivery (Cloudflare R2 + Stream)

### 5.1 The split

| Concern | Stream | R2 |
|---|---|---|
| Ingest + preview | ✅ handles encode, ABR (HLS/DASH), thumbnails, **signed playback** | — |
| Source archive | — | ✅ cheap, **zero egress** |
| Final render archive | — | ✅ source of truth |
| Public URL origin for IG/TikTok pulls | (possible) | ✅ **zero egress** — preferred origin |

**Decision:** **Stream for ingest + in-app preview** (kills the HEVC-fails-silently bug class), **R2 for archival of source + final renders and as the public-URL origin** that Ayrshare/Meta/TikTok pull from ([Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/), [Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/)). R2's zero egress is why it — not Stream — is the publish origin: IG and TikTok pull the file by URL, and we don't want to pay egress on every pull.

### 5.2 Pricing model (unit economics)

| Item | Cost |
|---|---|
| Stream — stored | **$5 per 1,000 minutes** |
| Stream — delivered | **$1 per 1,000 minutes** |
| R2 — egress | **$0** |

Source: [APIScout — Cloudflare Stream 2026](https://apiscout.dev/guides/how-to-stream-video-cloudflare-stream-2026), [Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/).

### 5.3 Signed playback + webhook security

- **Signed in-app preview:** Stream supports **RS256 JWT signed URLs** so private previews don't proxy through us; set `requiresignedurls` in upload metadata ([APIScout](https://apiscout.dev/guides/how-to-stream-video-cloudflare-stream-2026), [Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/)).
- **Webhook verification (mandatory):** Stream's processing-complete webhook to the Supabase Edge Function **must** be verified via the **`Webhook-Signature` HMAC-SHA256** header *before any Postgres write* — an unsigned handler lets anyone who guesses the URL flip clips to `ready`. Scope via per-product Webhook Subscriptions ([Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/)).

---

## 6. Transcription + word-timings (AssemblyAI)

### 6.1 Submit pattern

Async **submit + webhook**, never poll-blocking inside a durable run. POST `/v2/transcript` with `audio_url` (a Stream/R2 signed URL of the source) + `webhook_url`. Use the SDK's **`submit()`, not `transcribe()`**, to avoid blocking ([AssemblyAI — Webhooks + Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks), [AssemblyAI — Key Phrases](https://www.assemblyai.com/docs/speech-understanding/key-phrases)).

### 6.2 Params for Marque

| Param | Value | Why |
|---|---|---|
| `speaker_labels` | `true` (+ optional `speakers_expected` 1–20) | `utterances[]` with per-utterance + per-word `start`/`end`/`speaker`/`confidence` (ms) — essential for split-screen / reaction formats ([AssemblyAI — Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks)) |
| word-level timings | always returned (`words[]`, ms) | drives kinetic captions + precise cut points ([AssemblyAI — Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks)) |
| `auto_highlights` | `true` | `auto_highlights_result.results[]` → `text` + `rank` + `count` + `timestamps[]`; **ready-made moment-detection signal** for the Virality Engine / Hook Lab ([AssemblyAI — Key Phrases](https://www.assemblyai.com/docs/speech-understanding/key-phrases)) |
| `disfluencies` | `false` | drop "um"s from caption + cut copy |
| `sentiment_analysis`, `iab_categories`, `content_safety` | optional, per format need | feed Coach/teardown signals (`07-ai-system.md`) |

### 6.3 Webhook contract

- Receiver must return **2xx within 10s** or AssemblyAI retries — **up to 10 attempts**.
- A **4xx = permanent failure, no retry.**
- Payload contains only `transcript_id` + `status`; we then **GET `/v2/transcript/{id}`** to fetch text/words/error.
- Authenticate inbound webhooks via `webhook_auth_header_name` / `webhook_auth_header_value`.

Source: [AssemblyAI — Webhooks + Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks). In Trigger.dev this webhook **completes a wait token** (§9.5) rather than the run polling.

### 6.4 Caption source-of-truth (decision pending — Open Q2)

Two paths:
1. **AssemblyAI word-timings → custom Shotstack kinetic-caption template** (more on-brand styling; more build).
2. **Shotstack Ingest API self-transcribes** to SRT/VTT (`outputs.transcription.format: "srt"|"vtt"`) → Shotstack `caption` asset (simpler) ([Shotstack — Generate SRT/VTT](https://shotstack.io/learn/generate-srt-vtt-subtitles-api/)).

We **keep AssemblyAI regardless** as the **word-timing + highlights source for moment detection and voice checks** — Shotstack's transcription is caption-shaped, not analysis-shaped. The open question is only which drives the *visible captions* in v1.

---

## 7. Moment / cut detection

A cheap, no-custom-ML v1 signal stack feeding the AI plan (§3 of orchestration, owned by `07-ai-system.md`):

| Signal | Source |
|---|---|
| Highlight ranks | AssemblyAI `auto_highlights` (`rank`, `timestamps[]`) |
| Sentence / utterance boundaries | AssemblyAI `utterances[]` |
| Silence gaps | derived from word-to-word `end`→`start` gaps |
| Hook strength / retention risk | MCP `virality_predictor`, `video_analysis_create` |

**Final selection reasoning** ("pick the N best beats, map each to a format recipe") is done by **Claude Opus 4.8** over the transcript + highlights JSON; **Haiku 4.5** does bulk voice-check / classification. (Both via the `LLMRouter` — see `07-ai-system.md`.)

**Hard rule:** cut boundaries **snap to word `end` timestamps (ms)** — never cut on arbitrary time offsets, or you clip mid-syllable ([AssemblyAI — Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks)).

---

## 8. Rendering pipeline

### 8.1 MCP clipper + reframe (v1 ClipEngine)

The MCP creative toolchain exposes exactly the async, job-based primitives the spec names — each `create → status` so they slot cleanly behind Trigger.dev subtasks, hidden behind the `ClipEngine` adapter:

| MCP tool | Use |
|---|---|
| `personal_clipper_create` / `_status` / `_jobs` | extract clip candidates from the source |
| `reframe` | **content-aware aspect change** — landscape repurpose-in → 9:16, and any re-crop format. **Do not reimplement crop on-device.** |
| `video_analysis_create` / `_status` | retention / scene analysis |
| `virality_predictor` | hook-strength / retention scoring (§7) |
| `generate_image` / `generate_video` | faceless AI-visual beats (§8.4) |
| `remove_background` | green-screen / cutout without a literal green screen |
| `outpaint_image` | fill a still to 9:16 |
| `upscale_video`, `motion_control` | quality / motion polish where a recipe needs it |

### 8.2 Templated rendering via Shotstack — recipe → render JSON

**Mental model.** A Shotstack edit = a `timeline` (`tracks[]` → `clips[]` → `asset`) + an `output` (format / resolution / size / aspectRatio). **Tracks are z-ordered layers** — topmost overlays/obscures — which is exactly how split-screen, 3-up, and overlays compose ([Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/), [Shotstack — API Reference](https://shotstack.io/docs/api/)).

**Format recipe → Shotstack Template.** Each FORMAT RECIPE in `08-format-virality.md` is implemented **once** as a Shotstack **Template** with `{{ HANDLEBAR }}` merge fields (`POST /templates`); we then render many via template ID + a `merge[]` array in a single request. This is the literal implementation of "formats are structured render-recipes, not blank talking heads" ([Shotstack — Templates](https://shotstack.io/docs/guide/architecting-an-application/templates/)).

**Asset types we use:**

| Asset | Use |
|---|---|
| `video` | the talking-head clip — supports `trim`, `crop`, `chromaKey` `{color, threshold, halo}` (green-screen), `offset`, `scale`, `position` (split-screen tiling) ([Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/)) |
| `caption` | smart captions; point `src` at SRT/VTT; with `length: "end"` Shotstack auto-sets start/length ([Shotstack — Burn subtitles](https://shotstack.io/learn/burn-subtitles-captions-api/)) |
| `title` / `html` / `text` | kinetic text, hooks, lower-thirds ([Shotstack — Edit with Code](https://shotstack.io/docs/guide/getting-started/core-concepts/)) |
| `image` | overlays, B-roll stills, AI-visuals; `luma`/alpha `.mov` for transitions ([Shotstack — json-examples](https://github.com/shotstack/json-examples)) |

**Recipe → composition mapping:**

| Recipe | Shotstack composition |
|---|---|
| Split-screen / 3-up | N `video` clips on stacked tracks, each with `offset` + `scale` + `position` (+ `crop`) tiling the 9:16 frame |
| Green-screen | `chromaKey` on the talking-head track over an AI-visual background track |
| Before/after | two clips side-by-side, or a wipe transition |
| Faceless AI-visual | AI-gen `image`/`video` on the background/overlay track, timed to the beat's word-window; captions on top |
| Listicle / myth-buster / POV / reaction / B-roll+hook | `title`/`html` kinetic text + B-roll `image`/`video` overlays keyed to beat timestamps |

Source: [Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/).

**Smart clips** (`length: "auto"` / `"end"`) let recipes compose without hand-computing durations — Shotstack infers from asset length/position. Use for captions and variable-length beats ([Shotstack — Burn subtitles](https://shotstack.io/learn/burn-subtitles-captions-api/)).

**Output for our platforms:**
```json
"output": {
  "format": "mp4",
  "size": { "width": 1080, "height": 1920 },
  "fps": 30,
  "poster": { "capture": <coverFrameSeconds> }   // → IG thumb_offset / cover_url
}
```
(Equivalently `resolution` + `aspectRatio: "9:16"`.) ([Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/), [Shotstack — Edit with Code](https://shotstack.io/docs/guide/getting-started/core-concepts/))

**Preprocessing.** Shotstack auto-downloads + caches assets and **auto-fixes compatibility (HEVC / orientation / container)** — this is our safety net for raw iPhone HEVC sources. Force with `"transcode": true` on a problem video asset; set `"cache": true` to reuse downloaded assets across a batch's renders ([Shotstack — API Reference](https://shotstack.io/docs/api/), [Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/)).

**Async render.** `POST /edit/{version}/render` → render ID → **pass a `callback` URL** for webhook completion (preferred inside Trigger.dev — wait on a token, don't poll). Statuses: `queued → fetching → rendering → done | failed`. Output URL is in the response ([Shotstack — API Reference](https://shotstack.io/docs/api/), [Shotstack — Burn subtitles](https://shotstack.io/learn/burn-subtitles-captions-api/)).

**Destinations.** Shotstack *can* deliver directly to a store, but for our stack we **render → pull into R2/Stream** so we own the public URL for publishing (§5).

**Environments.** Use the **`stage`/sandbox** endpoint (free, watermarked) for dev and the **`v1`/production** endpoint for real renders — **two separate API keys** ([Shotstack — Templates](https://shotstack.io/docs/guide/architecting-an-application/templates/), [Shotstack — Burn subtitles](https://shotstack.io/learn/burn-subtitles-captions-api/)).

### 8.3 Component spec — `renderClip` (per-clip subtask)

| Field | Value |
|---|---|
| Input | `{ clipId, sourceMediaId, recipeId, beats[], captionSrc }` |
| Idempotency key | `clipId` (run scope) — see §9.2 |
| Steps | (1) MCP source prep (`personal_clipper` / `reframe`); (2) per-beat AI-visual gen (§8.4); (3) compile recipe → Shotstack merge payload; (4) `POST render` + `callback`; (5) `wait.forToken` on Shotstack webhook; (6) pull master → R2; (7) §11 QA validator |
| Output | `RenderManifest { r2Url, streamId, codec, width, height, durationMs, hasAudio, qaPassed }` |
| Retry | exponential backoff; cached subtask results reused via idempotency keys |

### 8.4 Faceless AI-visual generation per beat

Per-beat flow: **Claude writes a visual prompt per beat** (`07-ai-system.md`) → MCP `generate_image` / `generate_video` (async) → `outpaint_image` to fill 9:16 and/or `remove_background` to composite the creator over it → drop the asset onto a Shotstack background/overlay track **timed to that beat's word-timestamp window**.

**Cost discipline:** AI-visual generation is the **most expensive + slowest** step.
- **Gate** it behind recipes that actually need it (faceless, green-screen) — not every clip.
- Make each generation an **idempotent subtask keyed by `(clipId, beatIndex)`** so retries never re-bill.
- **Cache by prompt hash** so identical beats across clips reuse one asset.

---

## 9. Job orchestration (Trigger.dev v3)

### 9.1 Why v3

Durable execution with **no timeout** and **CRIU checkpoint-resume** — a run pauses at a waitpoint, **releases its concurrency slot**, and resumes later; you don't pay while waiting. Trigger.dev's docs explicitly model a video / transcode / coordinator pipeline like ours ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing), [Trigger.dev — How it works](https://trigger.mintlify.dev/docs/how-it-works), [Trigger.dev v3 announcement](https://trigger.dev/blog/v3-announcement)).

### 9.2 Idempotency (everywhere billable)

`idempotencyKeys.create(clipId)` passed into `triggerAndWait` / `batchTriggerAndWait` prevents duplicate child runs — and duplicate AI/render billing — when a parent retries.

| Scope | Semantics |
|---|---|
| `run` (default) | key + parentRunId — dedupes within a run |
| `attempt` | re-runs on each retry |
| `global` | once ever |

Keys are **per-task** (same key on different tasks does **not** dedupe). Default `idempotencyKeyTTL` = 30 days ([Trigger.dev — Idempotency](https://trigger.dev/docs/idempotency)). We use **`run` scope** for transcription + each `renderClip` + each AI-visual gen; **`global`** for the publish hand-off so a re-run never double-posts.

### 9.3 Retries

Per-task `retry: { maxAttempts, minTimeoutInMs, maxTimeoutInMs, factor }` (exponential backoff). On retry the parent restarts from the top but **cached subtask results are reused via idempotency keys** — completed transcription/renders are not repeated. Wrap non-idempotent external mutations (publish, billing) behind `global`-scoped keys ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing), [Trigger.dev — How it works](https://trigger.mintlify.dev/docs/how-it-works)).

### 9.4 Fan-out + concurrency

- **`batchTriggerAndWait`** renders N clips in parallel; the parent **checkpoints and releases its slot** while waiting — no deadlock ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing), [Trigger.dev — Concurrency & Queues](https://trigger.dev/docs/queue-concurrency)).
- **Custom queues** with `concurrencyLimit` + `concurrencyKey` keyed by `userId` stop one creator's batch from starving others **and** respect Shotstack / AssemblyAI / MCP rate limits. Only actively-executing runs consume slots — **WAITING (checkpointed) runs don't** ([Trigger.dev — Concurrency & Queues](https://trigger.dev/docs/queue-concurrency)).

### 9.5 Waitpoints for async vendors

Use **`wait.forToken`** and complete the token from the AssemblyAI / Shotstack / MCP webhook — the idiomatic "notify-on-complete" resume — instead of polling loops (`wait.forRequest` / `wait.until` / `wait.for` also available) ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing), [Trigger.dev v3 announcement](https://trigger.dev/blog/v3-announcement)).

### 9.6 Notify-on-complete + human-in-the-loop

- The final subtask publishes a **Supabase Realtime** event (live progress in the Produce screen, `05-screens-produce.md`) and triggers an **APNs push** ("Your week of clips is ready") via the backend.
- A **`wait.forToken` approval gate** maps to Marque's "review before publish" — the run pauses until the creator approves, costing nothing while waiting ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing)).

### 9.7 Parent task spec — `processBatchSession`

```
processBatchSession(sessionId):
  source = loadReadyClips(sessionId)                       # gated on `ready`
  transcript = transcribe(source)                          # AssemblyAI, wait.forToken
  moments   = detectMoments(transcript)                    # §7
  plan      = aiPlan(transcript, moments)                  # 07-ai-system, Opus 4.8
  manifests = batchTriggerAndWait(                         # §9.4 fan-out
                plan.clips.map(c =>
                  renderClip.trigger(c,
                    { idempotencyKey: idempotencyKeys.create(c.clipId) })))
  approved  = wait.forToken(reviewToken)                   # §9.6 review gate
  for m in approved: register(m); enqueuePublish(m)        # → 11-publishing
  notify(sessionId)                                        # Realtime + APNs
```

---

## 10. Cost + latency budgets

### 10.1 Cost (the numbers to model)

| Service | Unit cost | Notes |
|---|---|---|
| Cloudflare Stream — stored | $5 / 1,000 min | preview path |
| Cloudflare Stream — delivered | $1 / 1,000 min | in-app preview |
| Cloudflare R2 — egress | **$0** | **make R2 the publish origin** to avoid egress on IG/TikTok pulls |
| AssemblyAI | per audio-minute | `speaker_labels` / `auto_highlights` add model cost — **enable selectively per format** |
| Shotstack | per rendered output minute / credits | sandbox free + watermarked; clips are 15–60s so render minutes are small |
| MCP AI-visual gen | per asset (most expensive) | **gate by recipe + cache by prompt hash** (§8.4) |

Sources: [APIScout — Cloudflare Stream 2026](https://apiscout.dev/guides/how-to-stream-video-cloudflare-stream-2026), [Cloudflare Stream + Supabase pipeline](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/).

**Cost takeaway:** render minutes are cheap (short clips); **AI-visual generation dominates** — it is the lever to control, via recipe-gating and prompt-hash caching.

### 10.2 Latency

End-to-end for one batch = upload (network-bound, tus 50 MB chunks) + AssemblyAI (≈ async, roughly real-time-ish) + **N parallel** Shotstack renders (Shotstack markets "7× faster"; budget tens of seconds to a few minutes/clip) + AI-visual gen (slowest, tens of seconds–minutes/asset).

| Target | Proposed v1 | Driver |
|---|---|---|
| Per-clip SLA (no AI-visual) | ≤ ~2 min | transcription + single Shotstack render |
| Per-clip SLA (with AI-visual) | ≤ ~5 min | + gen + composite |
| Batch SLA (8–10 clips, parallel) | **see Open Q3** | concurrency limit + whether AI-visual is sync or deferred-and-notified |

**Surface a real progress UI, not a spinner** — Trigger.dev streams run status to the UI; map each subtask stage to a calm progress line (`05-screens-produce.md`). Parallelize aggressively via `batchTriggerAndWait` ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing), [APIScout](https://apiscout.dev/guides/how-to-stream-video-cloudflare-stream-2026)).

---

## 11. Output QA + publish-target constraints

Every render is validated against the strictest publish target before it can reach the Publisher (`10-social-publishing.md`). Bake constraints into the Shotstack `output` **and** a post-render validator.

### 11.1 Platform constraints

**Instagram Reels (Graph API)** ([Meta — IG Media/Reels](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media), [Phyllo — IG Reels API](https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api), [Postproxy — Reels vs TikTok vs Shorts](https://www.postproxy.dev/blog/instagram-reels-vs-tiktok-vs-youtube-shorts-publishing-via-api/)):
- Container **MOV/MP4**, **moov atom at front (faststart), no edit lists**.
- Video **H.264/HEVC**, progressive, **closed GOP**, **4:2:0**; **VBR ≤ 5 Mbps**. Audio **AAC, ≤ 48 kHz, mono/stereo, 128 kbps**.
- Frame rate **23–60fps**; max horizontal pixels **1920**; aspect 0.01:1–10:1, **recommend 9:16**; **file size ≤ 100 MB** (target ≤100MB to be safe).
- **API duration cap = 90s hard** (some accounts 60s) even though the native app allows 3 min. **A clip >90s cannot be published as a Reel via API — enforce in the renderer.**
- Publish = **container model**: file at a **public URL** → `POST /{ig-user-id}/media?media_type=REELS&video_url=...` → poll container status → `POST /media_publish` (optional `share_to_feed`, `cover_url`/`thumb_offset`).

**TikTok (Content Posting API)** ([Postproxy](https://www.postproxy.dev/blog/instagram-reels-vs-tiktok-vs-youtube-shorts-publishing-via-api/)):
- **No fixed duration limit**, but you **must call the Creator Info endpoint before every post** (returns `max_video_post_duration_sec`, available privacy levels, duet/stitch/comment toggles) — settings can change per post.
- **`video.upload` and `video.publish` are separate OAuth scopes** — need both.
- Upload: init → chunked PUT with **`Content-Range`** (files >5 MB chunked, **5–64 MB chunks, sequential, no parallel**); upload URLs expire ~1h — or pull-by-URL.
- **The user must actively choose a privacy level** — pre-selecting a default gets the app **rejected in review**.

All three platforms: **9:16 required, 1080×1920 recommended** ([Postproxy](https://www.postproxy.dev/blog/instagram-reels-vs-tiktok-vs-youtube-shorts-publishing-via-api/)).

> **Ayrshare note.** Marque publishes via Ayrshare (`10-social-publishing.md`), which normalizes much of this. But the renderer must still produce a **compliant master (9:16, H.264, ≤90s for Reels, AAC, moov-at-front)** and a **public URL**, because Ayrshare/Meta/TikTok all pull by URL. Whether Ayrshare fully satisfies the TikTok Creator-Info-before-every-post + must-choose-privacy requirement, or whether we surface a privacy choice in our own publish UI, is Open Q5 (owned jointly with `10-social-publishing.md`).

### 11.2 Post-render QA validator (automated)

Run on the master after pull-to-R2; on fail → re-render or flag (never publish).

| Check | Pass condition |
|---|---|
| Duration | ≤ 90,000 ms (**hard gate for IG Reels API**) |
| Aspect | exactly 9:16 |
| Audio track | present, **AAC** |
| Resolution | wide-axis ≥ 1080 |
| File size | ≤ 100 MB |
| Codec | H.264 |
| Faststart | **moov atom at front** |

(Probe via FFprobe in the QA subtask.) On fail, the run flags the clip in a calm "needs a re-cut" state rather than crashing the batch.

---

## 12. v1-on-MCP → in-house migration path

### 12.1 v1 adapters (every vendor is one file)

| Adapter | Wraps |
|---|---|
| `ClipEngine` | MCP toolchain (`personal_clipper`, `reframe`, `video_analysis`, `virality_predictor`, `generate_image`/`generate_video`, `remove_background`, `outpaint_image`) |
| `Renderer` | Shotstack |
| `Transcriber` | AssemblyAI |
| `BlobStore` | Cloudflare R2 + Stream |
| `Publisher` | Ayrshare |

### 12.2 Migration triggers

- Cost per render exceeds budget.
- MCP rate limits / queue depth become a bottleneck.
- Latency SLA misses (§10.2).

### 12.3 In-house v2

- **Replace Shotstack** with a self-hosted **FFmpeg / Remotion render farm on Trigger.dev** — their media-processing use-case explicitly supports multi-hour FFmpeg with no timeout ([Trigger.dev — Media processing](https://trigger.dev/docs/guides/use-cases/media-processing)).
- **Replace the MCP clipper** with our own moment-detection model fed by AssemblyAI word-timings (§7).
- **Keep AssemblyAI + R2/Stream** — commodity infrastructure, no reason to rebuild.

Because every vendor is isolated behind an adapter (§12.1), each swap is a **one-file change**, per the spec's doctrine.

---

## 13. Pipeline do's / don'ts (callouts)

- **DO** upload direct-to-Stream via tus with a backend-minted one-time URL. **DON'T** POST large files — >200 MB **requires** tus.
- **DO** verify Stream + AssemblyAI + Shotstack webhooks (HMAC / auth header) **before any DB write**.
- **DO** put an idempotency key on every billable subtask (render, AI-gen, transcription). **DON'T** retry without one — you'll double-bill.
- **DO** use `wait.forToken` + vendor callbacks. **DON'T** poll-loop inside a durable run.
- **DO** enforce the **90s IG Reels API cap** + 9:16 / H.264 / AAC / faststart in the renderer **and** a post-render validator.
- **DON'T** preselect TikTok privacy (auto-rejected). **DO** call TikTok Creator Info before every post.
- **DO** transcode away HEVC before delivery — raw iPhone HEVC fails silently in some players.
- **DO** set capture framerate **after** adding the input. **DON'T** rely on a preset to hold it.

---

## Open questions

1. **Preview transcode cost.** Do we pay for Stream on *every* captured source (clean, HEVC-safe preview) or only on final renders — archiving sources raw in R2 to cut Stream minutes? (Cost vs. preview-UX tradeoff.)
2. **Captions source of truth (v1).** AssemblyAI word-timings driving a custom Shotstack kinetic-caption template (more on-brand, more build) **vs.** Shotstack's built-in `caption` asset from SRT (simpler) — which is the v1 default? (We keep AssemblyAI for analysis either way.)
3. **Batch SLA target.** What's the promised wall-clock from "finish recording" to "clips ready"? This drives concurrency limits and whether AI-visual beats render synchronously in the batch or are deferred-and-notified-later.
4. **4K capture.** Offer at all in v1, or hard-lock 1080×1920 to bound upload/render cost?
5. **Ayrshare vs. direct IG/TikTok privacy/Creator-Info.** Confirm Ayrshare handles TikTok's Creator-Info-before-every-post + must-choose-privacy, or whether we surface privacy choice in our own publish UI to stay review-compliant. (Joint with `10-social-publishing.md`.)
6. **Self-host Trigger.dev?** v3 self-host needs a CRIU-compatible host; cloud is simpler — decide before infra commitments.
7. **Doc numbering.** `07-ai-system.md` references the clip pipeline as `09-video-pipeline.md`; this file is `09-video-pipeline.md`. Confirm the canonical filename and reconcile cross-references across the spec set.

## Sources

- [Apple — Pausing and resuming uploads](https://developer.apple.com/documentation/foundation/pausing-and-resuming-uploads) — iOS 17 `URLSession` resumable uploads, `cancel(byProducingResumeData:)`, background sessions auto-resume only if the server supports the protocol.
- [Apple WWDC23 — Build robust and resumable file transfers](https://developer.apple.com/videos/play/wwdc2023/10006/) — background `URLSession` for large files, `isDiscretionary`, `countOfBytesClientExpectsToSend`, survives suspension.
- [Apple Developer Forums — Moving to fewer, larger transfers](https://developer.apple.com/forums/thread/14853) — background sessions optimized for few large resumable transfers.
- [Apple Forums — uploading large videos / PHBackgroundResourceUploadExtension](https://developer.apple.com/forums/thread/818566) — why the system-daemon upload API is wrong for chunked/tus.
- [Apple — Still and Video Media Capture (AVFoundation)](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/04_MediaCapture.html) — `AVCaptureMovieFileOutput` auto H.264 level/bitrate, preset selection, min/max frame duration.
- [objc.io — Capturing Video on iOS](https://www.objc.io/issues/23-video/capturing-video) — presets, `activeFormat`, constant-framerate via min/max frame duration + `lockForConfiguration` (set framerate after input).
- [Apple WWDC25 — Capture cinematic video](https://developer.apple.com/videos/play/wwdc2025/319/) — `preferredVideoStabilizationMode`, scene-monitoring KVO for low-light, 1080p/4K@30.
- [AssemblyAI — Webhooks + Speaker Diarization](https://www.assemblyai.com/docs/pre-recorded-audio/webhooks) — async `submit()`, webhook 2xx-in-10s / 10 retries / 4xx=permanent, payload id+status only; `utterances[]` + word-level ms timings; `speaker_labels`.
- [AssemblyAI — Key Phrases (auto_highlights)](https://www.assemblyai.com/docs/speech-understanding/key-phrases) — ranked phrases + ms timestamps in `auto_highlights_result` as a moment-detection signal.
- [Cloudflare — Direct creator uploads](https://developers.cloudflare.com/stream/uploading-videos/direct-creator-uploads/) — one-time upload URLs, >200MB→tus, `Upload-Length`/`Upload-Metadata`, URL in `Location` header, mandatory `maxDurationSeconds`/`expiry`.
- [Cloudflare — Resumable & large files (tus)](https://developers.cloudflare.com/stream/uploading-videos/resumable-uploads/) — exact chunk rules (5MB min / 50MB rec / 200MB max, ÷256KiB), `stream-media-id` header, retry delays.
- [Cloudflare API — Initiate TUS upload](https://developers.cloudflare.com/api/resources/stream/methods/create/) — exact tus headers and `Upload-Metadata` supported keys.
- [Building a Video Upload Pipeline with Cloudflare Stream + Supabase](https://kashifaziz.me/blog/cloudflare-stream-supabase-video-pipeline/) — the three-lane architecture, HMAC webhook verification, Stream-vs-R2 (HEVC fails silently), kill-switch/caps in Supabase.
- [APIScout — Cloudflare Stream in 2026](https://apiscout.dev/guides/how-to-stream-video-cloudflare-stream-2026) — pricing ($5/1k stored, $1/1k delivered), RS256 signed URLs, direct-creator-upload + tus retry pattern.
- [Shotstack — Core Concepts](https://shotstack.io/docs/guide/getting-started/core-concepts/) — timeline/tracks(layers)/clips/assets; `trim`/`crop`/`chromaKey`/`offset`/`scale`/`position` for split-screen + green-screen; output format/size.
- [Shotstack — Edit Videos Using Code](https://shotstack.io/docs/guide/getting-started/core-concepts/) — output resolution/size, title/text assets for kinetic text.
- [Shotstack — Templates](https://shotstack.io/docs/guide/architecting-an-application/templates/) — `{{handlebar}}` merge fields, create-once/render-many; stage vs v1 environments + keys.
- [Shotstack — Burn subtitles/captions API](https://shotstack.io/learn/burn-subtitles-captions-api/) — `caption` asset, smart clips (`length: "end"`/`auto`), render lifecycle + callback.
- [Shotstack — Generate SRT/VTT subtitles via Ingest API](https://shotstack.io/learn/generate-srt-vtt-subtitles-api/) — Ingest can transcribe to SRT/VTT (`outputs.transcription.format`).
- [Shotstack — v1 API Reference](https://shotstack.io/docs/api/) — Edit/Serve/Ingest, render lifecycle (validate→download→preprocess→render→output), `transcode`, `cache`, callback.
- [Shotstack — json-examples (GitHub)](https://github.com/shotstack/json-examples) — overlay/luma-matte/stitched/captions example payloads.
- [Trigger.dev — Media processing use-case](https://trigger.dev/docs/guides/use-cases/media-processing) — no-timeout video processing, coordinator/router/human-in-loop, `batchTriggerAndWait`, FFmpeg multi-hour.
- [Trigger.dev — How it works](https://trigger.mintlify.dev/docs/how-it-works) — CRIU checkpoint-resume, idempotency-key result caching, durable execution.
- [Trigger.dev v3 announcement](https://trigger.dev/blog/v3-announcement) — no timeouts, `wait.forToken`/`forRequest`/`until`, `triggerAndWait`/`batchTriggerAndWait`, pay-nothing-while-waiting.
- [Trigger.dev — Idempotency](https://trigger.dev/docs/idempotency) — `idempotencyKeys.create`, scopes (run/attempt/global), `idempotencyKeyTTL` 30d default, per-task isolation.
- [Trigger.dev — Concurrency & Queues](https://trigger.dev/docs/queue-concurrency) — `concurrencyLimit`/`concurrencyKey`, WAITING runs release slots, no deadlock on `triggerAndWait`.
- [Meta — IG User Media / Reel Specifications](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media) — Reels container model, 9:16, ≤100MB, URL-pull publish, `cover_url`/`thumb_offset`.
- [Phyllo — Instagram Reels API guide (2026)](https://www.getphyllo.com/post/a-complete-guide-to-the-instagram-reels-api) — encode specs: MOV/MP4 moov-at-front, H.264/HEVC closed-GOP 4:2:0, ≤5Mbps VBR, AAC 48kHz 128kbps, 90s cap.
- [Postproxy — Reels vs TikTok vs Shorts publishing APIs (2026)](https://www.postproxy.dev/blog/instagram-reels-vs-tiktok-vs-youtube-shorts-publishing-via-api/) — 9:16 everywhere, IG 90s API cap, TikTok Creator-Info-before-every-post, separate upload/publish scopes, must-choose-privacy.
