# 05 — Core Screens II: Record, Library/Clip Editor, Calendar, Coach / Insights / Trends / Brand Profile

> **Scope.** This document specifies the *production half* of Marque — every screen that takes a creator from a script in their pocket to clips scheduled across Instagram and TikTok, then closes the loop with performance learning. It is the implementation contract for iOS engineers and product designers building these screens.
>
> **Sibling docs (authoritative for the systems referenced here):**
> - `01-information-architecture.md` — adapter contracts (`ClipEngine`, `Publisher`, `Insights`), Trigger.dev job topology, FastAPI orchestrator surface.
> - `02-design-system.md` — color tokens, type ramp, motion curves, component primitives.
> - `12-backend-data-security.md` — canonical Supabase schema authority for `brand_graph`, `clips`, `scheduled_posts`, `recordings`. Schema blocks below are *screen-facing projections*; defer to `12-backend-data-security.md` on conflict.
> - `04-screens-create.md` — Today, Onboarding/brand-ingest, Script reader + Hook Lab (these screens *hand off* into Record).
> - `10-social-publishing.md` — Publisher/Insights adapter internals, IG/TikTok platform compliance, quota accounting.
> - `07-ai-system.md` — Claude Opus 4.8 / Haiku 4.5 prompts, prompt caching, Brand Graph injection, virality scoring contract.
>
> **Locked aesthetic (see `02-design-system.md`).** Warm cream `#F4F1EA` (light) / near-black `#0E0E10` (dark). Serif display (Playfair/Tiempos) for titles; grotesque (Inter/Söhne/Matter) for UI. Single warm gold accent `#C9A227`, used sparingly. Huge whitespace, one idea per screen, slow eased "breathing" motion, soft shadows, subtle paper texture. Never pure white/black, never a red error — errors are calm and declarative.
>
> **Anti-clutter doctrine.** These are *working* screens, denser than Today by necessity — but density is earned through **progressive disclosure** and **granular per-item state**, never a full-screen spinner and never a wall of controls. One primary action per screen; secondary actions one layer deep.

---

## 0. Where these screens live in the flow

```
Today ──"Film today's batch"──▶  RECORD ──"Make my clips"──▶  LIBRARY / CLIP EDITOR
  ▲                                  ▲                              │
  │                          "Upload existing"                "Schedule"
  │                                  │                              ▼
  └──────── COACH ◀── teardown ── INSIGHTS ◀── performance ── CALENDAR ──▶ (publish)
                                       ▲                                       │
                                  TRENDS  ◀───────── Brand Graph ─────────────┘
                                       │                  ▲
                                  BRAND PROFILE (editable Brand Graph) ── feeds every Claude call
```

Tab bar (5 tabs, see `02-design-system.md`): **Today · Create · Calendar · Coach · Profile**. Record and the Clip Editor are *pushed* destinations off Create/Today, not tabs — they are full-screen, focused modes. Trends and Insights are pushed off Today's trend line and Coach respectively.

---

## 1. RECORD

The hero of the entire app: **film once → post all week**. A creator opens Record with a batch of scripts already written (in their voice, by Claude Opus 4.8 — see `07-ai-system.md`), records one talking-head take per script in a single sitting, and leaves. Everything downstream is asynchronous.

### 1.1 Responsibilities

1. A calm, full-bleed **vertical (9:16) camera** with a legible **teleprompter** overlay.
2. **Multi-take** per script, with a frictionless keeper-selection.
3. **Batch session cycling** — auto-advance through the script queue ("Next: …").
4. A second source: **"Upload existing"** long video for repurposing (differentiator #6).
5. A single terminal CTA: **"Make my clips"** that enqueues durable processing and *releases the user*.

### 1.2 Architecture (AVFoundation + SwiftUI + Observation)

Mirror Apple's [AVCam sample architecture](https://developer.apple.com/documentation/avfoundation/avcam-building-a-camera-app): the View is dumb; an `@Observable` camera model owns the session.

```
RecordScreen (SwiftUI)
├── CameraPreview            // UIViewRepresentable wrapping AVCaptureVideoPreviewLayer
├── TeleprompterOverlay      // SwiftUI offset-driven crawl (CADisplayLink) + scrim — NOT burned into video; see §1.4
├── RecordControls           // record/stop, take counter, speed stepper, source toggle
└── ScriptQueueRail          // "Next: <title>" + dots for batch progress

CameraModel : @Observable     // owns AVCaptureSession; NEVER held by the View hierarchy
├── session: AVCaptureSession
├── sessionQueue: DispatchQueue (serial, .userInitiated)   // ALL config + start/stop here
├── movieOutput: AVCaptureMovieFileOutput
├── state: CameraState (enum, see §1.8)
└── currentSession: RecordingSession
```

**Hard rules (do not violate):**

- `session.startRunning()` is **thread-blocking** and must run on a dedicated **background serial queue**, never main — it will jank the UI otherwise ([AVCaptureSession docs](https://developer.apple.com/documentation/avfoundation/avcapturesession); [createwithswift setup pattern](https://www.createwithswift.com/camera-capture-setup-in-a-swiftui-app/)). The same applies to every `beginConfiguration()`/`commitConfiguration()` mutation.
- Recording output for v1 is **`AVCaptureMovieFileOutput`** — it yields a finished `.mov` with the least ceremony. The teleprompter is a SwiftUI overlay and is **never** composited into the recording. (Reserve `AVCaptureVideoDataOutput` + `AVAssetWriter` for a future version that needs burned-in overlays or custom bitrate.)
- Capture **vertical, lock 9:16**. On export, set the composition track's `preferredTransform` for correct orientation. **Do not** combine `AVAssetExportPresetPassthrough` with a video-composition transform — passthrough ignores composition instructions ([Apple QA1744](https://developer.apple.com/library/archive/qa/qa1744/_index.html); [AVAssetExportSession](https://developer.apple.com/documentation/avfoundation/avassetexportsession)). Target **1080×1920**, H.264 or HEVC.

### 1.3 Permissions

`Info.plist` **must** declare `NSCameraUsageDescription` and `NSMicrophoneUsageDescription` or the app hard-crashes on first access. Request **just-in-time** when the user enters Record — never at launch.

| Permission | Copy (declarative, calm) |
| --- | --- |
| Camera (`NSCameraUsageDescription`) | "Marque uses the camera so you can film your scripts." |
| Microphone (`NSMicrophoneUsageDescription`) | "Marque uses the microphone to record your voice." |

Denied → not a red error. A cream panel: *"Marque needs the camera to film. You can turn it on in Settings."* + a gold **Open Settings** button deep-linking to `UIApplication.openSettingsURLString`.

### 1.4 Teleprompter

A SwiftUI overlay, not AVFoundation. It lives in a top-center band (eyeline near the lens — classic teleprompter ergonomics). A semi-transparent dark scrim sits behind the text so it stays legible over any background, including the cream UI chrome.

**Two distinct scroll mechanisms — keep them separate.** A teleprompter needs a *smooth, constant-velocity crawl*; the shot-plan beat jump (§3.4 hand-off / "skip to next beat") needs a *discrete jump to a known line*. These are different primitives and must not be conflated:

1. **Continuous crawl (primary).** Drive the crawl from a **content offset**, never from `ScrollViewReader`. `ScrollViewReader`'s only API is `proxy.scrollTo(id:anchor:)`, which snaps to discrete child views — it cannot express an arbitrary sub-pixel offset, and calling it per `CADisplayLink` frame yields stepped/animated-jump motion (and chained `withAnimation` blocks fight each other), not a crawl. Implement the crawl as one of:
   - **(Preferred for v1) Explicit `.offset(y:)` on the text layer**, advanced each tick. Render the script as a single `Text`/`VStack` block inside the band, hold a `@State var scrollOffset: CGFloat`, and on each `CADisplayLink`/`TimelineView` tick advance `scrollOffset -= pointsPerSecond * frameDelta`. No animation modifier — the per-frame offset *is* the animation, so motion is exactly constant-velocity. Drive ticks with a `CADisplayLink` (preferred: gives true `targetTimestamp`/`duration` for jitter-free pacing and pauses cleanly on `isPaused`) or a `TimelineView(.animation)` whose context date supplies `frameDelta`.
   - **(Alternative) `UIScrollView` via `UIViewRepresentable`**, setting `contentOffset` directly each frame (`setContentOffset(_:animated:false)`). Use this if the script is long enough that the offset-layer approach risks layout cost; a `UIScrollView` recycles cleanly and lets the user also flick-scroll manually.
   - On **iOS 17**, `ScrollView` + `.scrollPosition(id:)`/`.scrollTargetLayout()` is appropriate for the *discrete* mechanism below, **not** for the continuous crawl — `scrollPosition` binds to an *item id*, not a continuous offset, so it shares `ScrollViewReader`'s limitation for smooth motion.
   - **WPM → points/second mapping.** `pointsPerSecond = (wpm / 60) * averagePointsPerWord`, where `averagePointsPerWord` is measured from the laid-out text (band width ÷ words-per-line × line height), recomputed on font/size/Dynamic-Type change so pacing stays honest across script lengths. Default **130 WPM**; **persist WPM per user** (Supabase `profiles.teleprompter_wpm`).
2. **Discrete beat jump (secondary).** *Only* for jumping to a known line — the shot-plan beat hand-off (§3.4) and "skip to next/previous beat." This is the **one** legitimate use of `ScrollViewReader` (or iOS 17 `.scrollPosition(id:)`): tag each beat's first line with an `id` and call `proxy.scrollTo(beatID, anchor: .top)` inside `withAnimation(.easeInOut)` for a single eased jump. A beat jump pauses the crawl, jumps, then resumes from the new offset — it never runs concurrently with the per-frame crawl.

- Speed control: a discreet gold `+ / −` stepper (not a loud slider) at the band edge that adjusts `wpm` (and thus `pointsPerSecond`) live. Tap anywhere on the band to **pause/resume** — pausing sets `CADisplayLink.isPaused = true` (or freezes the offset), which halts the crawl instantly with no residual animation to unwind.
- Current line gets a subtle gold weight bump; upcoming lines dim. At script end, the crawl **auto-stops** (offset clamps at the final line; `CADisplayLink` paused) with a gentle success haptic (`UINotificationFeedbackGenerator.success`).

**Teleprompter states:** `idle` · `scrolling` (continuous crawl) · `paused` · `speed-adjusting` (transient) · `beat-jumping` (transient discrete jump) · `finished` (auto-stopped).

### 1.5 Multi-take + batch cycling (the HERO mechanic)

```
RecordingSession
  id: UUID
  source: .captured | .imported
  scriptQueue: [ScriptRef]        // ordered batch from the script reader
  cursor: Int                     // which script we're on
  takes: [ScriptTake]

ScriptTake
  id: UUID
  scriptRef: ScriptRef
  localURL: URL                   // on-disk .mov, written IMMEDIATELY, never held in memory
  duration: TimeInterval
  isKeeper: Bool
  uploadState: .pending | .uploading(progress) | .uploaded | .failed
  r2UploadId: String?             // R2/S3 multipart UploadId (after CreateMultipartUpload)
  r2Key: String?                  // Cloudflare R2 object key
  uploadedParts: [PartRecord]     // {partNumber, etag, byteOffset, byteLength} — client-owned resume ledger
```

`PartRecord` (the resume ledger) is persisted to the `recordings` row so an interrupted upload can resume by re-issuing only the missing parts (see upload bullet below).

- Each script may have **N takes**. After stopping a take, a slim review strip slides up: *Keep · Retake · Next*. Choosing **Keep** marks `isKeeper` and auto-advances the cursor → the queue rail animates to **"Next: <title>"**. This auto-advance *is* the batch loop.
- Persist each take to **disk immediately** on stop, and insert a `recordings` row (upload `pending`). Never accumulate raw video in memory.
- **Background upload of keeper takes to Cloudflare R2 — application-owned resumable multipart.** iOS provides **no automatic upload restart**: `URLSession` background tasks neither resume a half-finished upload nor restart on relaunch on their own. Resumability is therefore implemented at the *application* layer, not assumed from the platform:
  - Use a shared `URLSessionConfiguration.background(withIdentifier:)` session, and upload **from the on-disk take file** via `uploadTask(with:fromFile:)` — **never** `uploadTask(with:from: Data)`. Background-configuration upload tasks **require a file body**; in-memory `Data` is not permitted and the take is already on disk ([Apple — URLSession background config](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/1407496-background); [downloading-files-in-the-background](https://developer.apple.com/documentation/foundation/url-loading-system/downloading-files-in-the-background)).
  - Implement R2 multipart as **explicit per-part PUTs** (R2 is S3-compatible: `CreateMultipartUpload` → N × `UploadPart` → `CompleteMultipartUpload` ([Cloudflare R2 multipart](https://developers.cloudflare.com/r2/objects/multipart-objects/))). Slice the take file into fixed-size parts; **each part is its own background `uploadTask(with:fromFile:)`** against a part body sliced to a temp file (so every task still has a file body). On each part's success, append `{partNumber, etag, byteOffset, byteLength}` to `uploadedParts` and **persist the ledger to the `recordings` row** immediately.
  - **Resume rule:** on relaunch, reconnect the background session, reattach the completion handler stashed in the `AppDelegate`/`SceneDelegate` (`handleEventsForBackgroundURLSession` / `application(_:handleEventsForBackgroundURLSessionWithIdentifier:completionHandler:)`), diff `uploadedParts` against the file's part plan, and **re-issue only the missing parts** — never restart the whole upload. When all parts are present, send `CompleteMultipartUpload` and flip `uploadState → .uploaded`.
  - **Foreground vs. suspended.** Kicking parts off *while the user keeps filming* (foreground) is fine and gives live per-take progress. But background-session transfers are **discretionary** and the system may **defer** them while the app is suspended/terminated (battery, network, scheduling) — so the resumability guarantee comes from the client-side part ledger above, not from the foreground path. Surface this as a calm per-take progress chip, never a blocking modal.
- Batch progress dots in the queue rail: filled = a keeper exists, hollow = not yet, gold ring = current.

### 1.6 "Upload existing" (repurpose source — differentiator #6)

A secondary source toggle at the top of Record: **Film ⇄ Upload**.

- Upload uses `PhotosPicker` (PhotosUI) for camera-roll video, or `UIDocumentPicker` for files app. The selected long video becomes a `RecordingSession(source: .imported)` with a single synthetic `ScriptTake` (no teleprompter).
- It feeds the **exact same downstream pipeline** — R2 upload → AssemblyAI transcript → ClipEngine. The only divergence is provenance (`source: .imported`), used later for analytics and to skip teleprompter UI.
- Large imports show a determinate copy/upload progress and may exceed R2 single-PUT limits → **always multipart**, using the identical application-owned resumable-multipart path as captured takes (§1.5): on-disk file body, per-part background tasks, client-side `uploadedParts` ledger, resume-missing-parts on relaunch.

### 1.7 "Make my clips"

The single terminal CTA. On tap:

1. Ensure all keeper takes are uploaded to R2 (or queued offline).
2. Call FastAPI `POST /sessions/{id}/process` → enqueues a **Trigger.dev** durable job (see `01-information-architecture.md`): AssemblyAI transcription → ClipEngine clip generation.
3. **Release the user immediately.** Navigate to the Library with a calm banner: *"Cutting your clips — we'll let you know."* The user can leave the app entirely; an APNs push fires on completion.

Never block the UI on upload or processing. If upload is incomplete (offline), the job is queued and fires on reconnect.

### 1.8 States — Record

| State | Trigger | UI |
| --- | --- | --- |
| `configuring` | entering screen | Cream view, soft pulsing gold dot, "Setting up the camera." Session starting on background queue. |
| `camera-denied` | permission denied | Calm panel + **Open Settings**. No preview. |
| `mic-denied` | mic denied | Same pattern; recording disabled with a one-line note. |
| `ready` | session running | Live preview + teleprompter + controls. |
| `recording` | record tapped | Subtle gold timer, teleprompter scrolls, stop button. |
| `paused` | teleprompter tap | Scroll halts; recording continues (pause is teleprompter-only in v1). |
| `take-saved` | stop tapped | Review strip: Keep · Retake · Next. |
| `uploading` | keeper exists | Per-take progress chip on the queue rail. |
| `upload-failed` | R2 error | Per-take amber dot + tap-to-retry. Other takes unaffected. |
| `offline` | no connectivity | "Saved on your phone — we'll upload when you're back online." Takes queue locally. |
| `processing-enqueued` | "Make my clips" | Banner + nav to Library. |

**Acceptance criteria (Record):**
- AC-R1: `startRunning()` and all session config run off the main thread (verified by main-thread checker; no hangs).
- AC-R2: Recorded files are 1080×1920, 9:16, correctly oriented on export (no sideways/upside-down output).
- AC-R3: Each take is persisted to disk before the next take can begin; force-quitting mid-session loses at most the in-progress take.
- AC-R4: Denied camera/mic never shows a red error; always a calm panel with a Settings deep-link.
- AC-R5: "Make my clips" returns the user to Library in < 300 ms regardless of upload state.
- AC-R6: Imported video flows through the identical pipeline as captured takes.
- AC-R7: Keeper/import uploads use a background `URLSession` with a **file body** (`uploadTask(with:fromFile:)`); no upload is ever issued from in-memory `Data`.
- AC-R8: Killing the app mid-upload and relaunching resumes by re-issuing **only the missing parts** (verified against the persisted `uploadedParts` ledger), never re-uploading completed parts or restarting from zero.

---

## 2. LIBRARY / CLIP EDITOR

Where the AI's cuts land and the creator shapes them. This screen consumes the ClipEngine output and the Shotstack renders, and is the launchpad into the Calendar.

### 2.1 The pipeline feeding this screen

- **AssemblyAI** returns **word-level timestamps on every transcript**, plus `auto_chapters`, `auto_highlights` (key phrases), `iab_categories`, `sentiment`, and `entity_detection` — the raw signal for moment/clip-boundary detection ([AssemblyAI STT](https://www.assemblyai.com/products/speech-to-text); [Speech Understanding](https://www.assemblyai.com/products/speech-understanding)). Word timestamps drive **caption sync, trim-handle snapping, and jump-to-moment** ([timestamps guide](https://www.assemblyai.com/blog/how-to-transcribe-audio-with-timestamps)).
- **ClipEngine adapter** (MCP creative toolchain behind a one-file adapter, see `01-information-architecture.md`) selects moments, produces draft clips, and returns a **predicted virality score** per clip via the `virality_predictor` tool (hook strength, retention risk, attention).
- **Shotstack** is the renderer. Its **JSON Edit schema** (tracks → clips → assets) plus **reusable Templates + merge fields** means **one format template renders thousands of variations from a single request** ([Templates endpoint](https://shotstack.io/learn/introducing-templates-endpoint/); [templates guide](https://shotstack.io/docs/guide/architecting-an-application/templates/)). Captions are transcribed then **burned in**; overlays, lower-thirds, kinetic/animated text, transitions, and motion effects are all expressible in the edit JSON ([overlays use-case](https://shotstack.io/use-cases/scenarios/api/generate-videos-with-overlays/); [API ref](https://shotstack.io/docs/api/)).

### 2.2 FORMAT LIBRARY = render-recipes

This is the core differentiator: formats are **structured Shotstack templates**, not blank talking heads. Each format maps to a **named Shotstack template** whose merge fields are the variable slots.

| Format | Shotstack template id (canonical) | Key merge fields |
| --- | --- | --- |
| Talking head (clean) | `fmt_talking_head` | `clip_src`, `caption_style`, `hook_text` |
| Split-screen | `fmt_split_screen` | `clip_src`, `secondary_src`, `caption_style`, `hook_text` |
| 3-up talking heads | `fmt_threeup` | `clip_src_a/b/c`, `caption_style` |
| Green-screen | `fmt_green_screen` | `clip_src`, `bg_asset`, `caption_style` |
| Faceless AI-visual | `fmt_faceless` | `vo_src`, `ai_visual_urls[]`, `caption_style`, `hook_text` |
| Before/after | `fmt_before_after` | `clip_src`, `before_asset`, `after_asset`, `label_style` |
| Myth-buster | `fmt_myth_buster` | `clip_src`, `myth_text`, `truth_text`, `caption_style` |
| Listicle | `fmt_listicle` | `clip_src`, `items[]`, `caption_style` |
| POV | `fmt_pov` | `clip_src`, `pov_text`, `caption_style` |
| Reaction | `fmt_reaction` | `clip_src`, `reaction_src`, `caption_style` |
| B-roll + caption-hook | `fmt_broll_hook` | `clip_src`, `broll_urls[]`, `hook_text`, `caption_style` |

**Format switch = re-render with a different template id + the same merge fields.** AI-visual / B-roll assets come from the ClipEngine adapter's MCP image/video-generation tools; their **output URLs become Shotstack asset merge fields**. (Caption styles stay inside the locked Tiempos/Inter + gold system — see `02-design-system.md`.)

### 2.3 Screen-facing data projection

> Authority: `12-backend-data-security.md`. Shown here as the projection the editor reads/writes.

```
clips
  id              uuid pk
  session_id      uuid → recordings session
  take_id         uuid → ScriptTake
  source_in_ms    int        // trim start, snapped to word boundary
  source_out_ms   int        // trim end
  format_id       text       // e.g. 'fmt_split_screen'
  caption_style   text       // 'editorial' | 'kinetic' | 'minimal' | ...
  merge_fields    jsonb      // current Shotstack merge payload
  predicted_score numeric    // 0..100, from virality_predictor
  score_factors   jsonb      // {hook, retention_risk, attention}
  render_state    text       // see §2.6
  render_url      text       // last GOOD render (Cloudflare Stream/R2)
  render_job_id   text       // Trigger.dev job
  created_at      timestamptz
  updated_at      timestamptz
```

### 2.4 Layout & component spec

**Library (clip tray).** A horizontally scrollable rail of **clip cards**:

```
ClipCard
├── thumbnail (9:16, rounded, soft shadow, paper-edge)
├── duration pill (bottom-left)
├── score chip (bottom-right) — calm, "82" in gold on cream, NOT a loud gauge
├── format glyph (top-left) — small line icon
└── render-state veil (when re-rendering: frosted + slow gold shimmer)
```

Tapping a card opens the **per-clip editor** (pushed full-screen).

**Per-clip editor.** One idea at a time; controls revealed via a bottom segmented bar, each opening a single-purpose tray:

| Control | Behavior | Async? |
| --- | --- | --- |
| **Trim** | Two handles over a word-timeline; handles **snap to AssemblyAI word boundaries**. Scrub head jumps to word. | Re-render |
| **Format / layout** | Picker of the §2.2 templates with live thumbnails. Select → template swap. | Re-render |
| **Caption style** | Font/color/animation presets within the locked type system. | Re-render |
| **AI-visual / B-roll insert** | Pick from library OR generate (MCP) → asset slot in merge fields. | Re-render (asset gen first) |
| **Regenerate** | Re-run ClipEngine on the whole clip (new moment / hook). | New clip job |
| **Predicted score** | Read-only chip + one-line factor breakdown ("Strong hook, retention dips at 0:06"). | — |

**Re-render rule.** Every edit that changes render output is an **async Trigger.dev re-render** scoped to *that card only* — optimistic "re-rendering…" veil on the single card, never the whole tray, never full-screen. **Always cache the last good render** (`render_url`) so a card never goes blank during a re-render.

### 2.5 Predicted score presentation

Calm, not gamified. A single gold number 0–100 on the card; in the editor, one declarative line derived from `score_factors`. No leaderboards, no confetti. A low score never blocks scheduling — it nudges via copy ("Want a punchier hook? Try regenerate.").

### 2.6 States — Library / Clip Editor

| State | Scope | UI |
| --- | --- | --- |
| `clips-generating` | screen | Skeleton tray of card placeholders, "Cutting your clips…" Driven by the Trigger.dev job. |
| `partial` | screen | Ready cards render in place; remaining cards stream in with per-card skeletons. |
| `clip-ready` | card | Thumbnail + score + format glyph. |
| `re-rendering` | card | Frosted veil + slow gold shimmer; prior `render_url` still tappable/preview-able. |
| `render-failed` | card | Amber dot + tap-to-retry; **keeps the prior good version**. |
| `empty` | screen | No usable moments found → "We couldn't find a clean clip. Trim one yourself?" → opens manual trim on the full take. |
| `offline` | screen | Cached renders shown; edits queue locally and dispatch on reconnect. |

**Acceptance criteria (Clip Editor):**
- AC-C1: Trim handles snap to word boundaries from the AssemblyAI transcript (never mid-word cuts by default).
- AC-C2: A format/caption/insert change re-renders only the affected card; the rest of the tray stays interactive.
- AC-C3: During any re-render, the card shows the previous good render, never a blank/black frame.
- AC-C4: A failed render preserves the last good `render_url` and offers retry without losing edits.
- AC-C5: Switching format reuses the same merge fields against a new template id (no re-upload of source).

---

## 3. CALENDAR / SCHEDULER

Where clips become scheduled posts across Instagram and TikTok. The scheduler UI is shaped as much by **platform compliance and rate limits** as by interaction design — those constraints are first-class here.

### 3.1 Drag-to-schedule

- Week view = **7 columns** (days), time bands within each day. Clip chips drag from a bottom tray into day/time slots.
- Use SwiftUI **`.draggable()` + `.dropDestination()`** with `Transferable` clip identifiers ([Apple drag-and-drop](https://developer.apple.com/documentation/SwiftUI/Adopting-drag-and-drop-using-SwiftUI); [draggable calendar walkthrough](https://michaelabadi.com/articles/create-calendar-view-swiftui/)). On drop, an eased "settle" animation honors the locked breathing motion.
- **iOS 17 baseline:** stick to `draggable`/`dropDestination` + `onMove` for reordering. The newer `reorderable()` / `reorderContainer` modifiers are **iOS 27** and out of scope ([Livsy on iOS 27 reorderable](https://livsycode.com/swiftui/swiftui-reorderable-containers-in-ios-27/)).

### 3.2 Per-platform toggles + optimal-time hints

- Each scheduled item carries an independent **per-platform set** (Instagram, TikTok) with enable toggles. Per-platform **caption variants** are an open question (see §6) — v1 assumes a shared caption with the data model leaving room for variants.
- **Optimal-time hints** come from the **Insights adapter** (Phyllo/Ayrshare pullback → best-time model). Render as a **faint gold suggestion glyph** on candidate slots; **never auto-commit** without an explicit drop.

### 3.3 Screen-facing data projection

```
scheduled_posts
  id            uuid pk
  clip_id       uuid → clips
  platform      text         // 'instagram' | 'tiktok'
  scheduled_at  timestamptz
  caption       text
  status        text         // see §3.6
  publisher_ref text         // Ayrshare post id
  tiktok_opts   jsonb        // {privacy, allow_comment, allow_duet, allow_stitch, commercial_content, your_brand, branded_content}
  fail_reason   text         // normalized, calm
  created_at    timestamptz
  updated_at    timestamptz
```

A `clip_id` can have **multiple** `scheduled_posts` (one per platform) — the per-platform toggles map 1:1 to rows.

### 3.4 HARD publishing constraints the UI must enforce

These live behind the **Publisher (Ayrshare)** adapter (`10-social-publishing.md`), but the **scheduler UI must surface remaining quota and prevent over-scheduling**, because the platforms reject overflow.

**Instagram Graph API — content publishing.**
- **25 posts / rolling 24h** per IG professional account; exceeding returns **error code 9**, enforced on a **rolling timestamp window, NOT a calendar-day reset** ([Meta content_publishing_limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/); [Ayrshare error-9](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/)).
- Publishing is **2-step**: create media container → publish container (poll container status; video containers take time to process). The adapter owns this; the UI reflects `publishing` while it polls.
- **Compute remaining quota from actual publish timestamps over the trailing 24h**, never a midnight reset.

**TikTok Content Posting API.**
- **~15–25 videos/day** (varies by creator); **6 requests/min per access_token** ([TikTok rate limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit); [direct-post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)).
- **Mandatory pre-post UX, audited** ([content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)):
  - Call **Query Creator Info** and render a **content preview** before any Direct Post.
  - If an interaction (comment/duet/stitch) is disabled in the creator's settings, the corresponding checkbox **must be greyed out and default unchecked**.
  - Surface a **Commercial Content / branded-content disclosure** toggle (**off by default**) with "Your brand" / "Branded content" sub-options.
  - **Must NOT superimpose any Marque watermark/logo/promo text** on TikTok-bound content — violation risks deletion / account disable.
  - **Unaudited apps: max 5 users may post in 24h, and direct-post visibility may be restricted** until the TikTok audit passes — flagged as a launch blocker in §6.

**Normalization.** The Publisher adapter normalizes platform errors into calm, actionable UI strings — e.g. *"You've reached today's Instagram limit — scheduled for tomorrow, 9:00 AM."* Ayrshare itself surfaces the IG 25/day error, which the adapter intercepts ([Ayrshare error-9](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/)).

### 3.5 TikTok pre-post sheet (component spec)

Before any TikTok `scheduled_post` is confirmed, a **pre-post sheet** (driven by Query Creator Info) renders:

```
TikTokPrePostSheet
├── content preview (thumbnail + caption)
├── privacy level picker        // from creator-info allowed set
├── Allow comments  [toggle]     // greyed+off if disabled in creator settings
├── Allow Duet      [toggle]     // greyed+off if disabled
├── Allow Stitch    [toggle]     // greyed+off if disabled
├── "Disclose content"  [toggle, OFF by default]
│     ├── Your brand        [toggle]
│     └── Branded content   [toggle]
└── compliance note (no Marque watermark guarantee)
```

### 3.6 Realtime status

Subscribe scheduled-post status (`queued → publishing → published → failed`) via **Supabase Realtime**. **Prefer `broadcast_changes()` from a Postgres trigger on a private channel** over raw Postgres Changes — Broadcast scales; Postgres Changes is simpler but does not ([Supabase Broadcast](https://supabase.com/docs/guides/realtime/broadcast); [subscribing to DB changes](https://supabase.com/docs/guides/realtime/subscribing-to-database-changes)). Client uses supabase-swift `.subscribe()` ([Swift subscribe ref](https://supabase.com/docs/reference/swift/subscribe)).

### 3.7 States — Calendar

| State | UI |
| --- | --- |
| `loading` | Week skeleton, gold dot. |
| `empty` | Calm: "Drop a clip onto a day to schedule it." Tray of ready clips below. |
| `scheduled` | Chip in slot with platform glyphs + time. |
| `publishing` | Live gold shimmer on the chip (IG container polling / TikTok upload). |
| `published` | Solid chip + small check; tappable to the post. |
| `publish-failed` | Amber chip + normalized reason + retry. |
| `quota-reached` | Per-platform: candidate slots for that platform dim; banner with the next available time. |
| `offline` | Read-only cached week; edits queue and dispatch on reconnect. |
| `token-expired` | Calm re-auth prompt for the affected platform (IG/TikTok). |

**Acceptance criteria (Calendar):**
- AC-S1: Per-platform remaining quota is computed over a **trailing rolling 24h** from real publish timestamps, not a midnight reset.
- AC-S2: Dropping a TikTok post always presents the Query-Creator-Info pre-post sheet; disabled interactions are greyed and unchecked.
- AC-S3: The disclosure toggle defaults OFF; no Marque branding is ever composited into TikTok-bound video.
- AC-S4: Exceeding IG's 25/24h is intercepted and surfaced as a calm reschedule, never a raw error-9.
- AC-S5: Status transitions arrive live via Supabase Realtime without a manual refresh.
- AC-S6: Drag-to-schedule works on iOS 17 (no iOS 27-only APIs).

---

## 4. COACH

The relationship layer: a calm chat plus an insights feed and **performance teardown cards**.

### 4.1 Composition

- **Chat** backed by **Claude Opus 4.8** (teardowns, brand reasoning) with **prompt caching** on the system + **Brand Graph** context block, and **Haiku 4.5** for cheap bulk classification / voice checks (see `07-ai-system.md`). The Brand Graph + recent performance are sent as a **cached prefix** so every Coach turn is cheap.
- **Insights feed** — a vertical stream of teardown cards and nudges, newest first.
- **Teardown cards** — a post-hoc breakdown of a *published* clip (what worked / hook / retention), generated when Insights data lands. Also delivered as **one APNs push** (Section-8 feature #4) and **archived in Insights** (§5).

### 4.2 Teardown card spec

```
TeardownCard
├── clip thumbnail + platform glyph
├── headline verdict (serif, one line)   // "Your hook held — 71% watched past 3s."
├── 2–3 factor rows (hook · retention · reach), each a calm line
├── one suggested next action (gold link)  // "Try this hook style again →"
└── state: generating | ready | not-enough-data-yet
```

### 4.3 States — Coach

| State | UI |
| --- | --- |
| `loading` | Cream, gentle gold dot. |
| `empty` | "Ask me anything about your content." + 2–3 starter prompts. |
| `chat-thinking` | Slow breathing gold ellipsis (Opus turn). |
| `feed-populated` | Teardown cards + nudges, newest first. |
| `teardown-generating` | Card skeleton, "Reading the numbers…" |
| `not-enough-data-yet` | Card placeholder: "Too early — check back after more views." |
| `offline` | Cached chat + cards, composer disabled with a calm note. |

**Acceptance criteria (Coach):**
- AC-K1: The Brand Graph context is sent as a cached prefix on every Coach turn (verified via cache-hit telemetry in `07-ai-system.md`).
- AC-K2: A teardown card is generated once per published clip when Insights data first lands, and is mirrored into Insights.
- AC-K3: Editing the Brand Graph (§7) invalidates the cached prefix so the next turn reflects new context.

---

## 5. INSIGHTS

The performance archive. Analytics pull via **Phyllo / Ayrshare behind the Insights adapter** (`10-social-publishing.md`).

- **Design for stale-but-cached.** Phyllo/Ayrshare carry their own refresh latency; always show a timestamp — *"Updated 3h ago"* — and never imply real-time numbers.
- Surfaces: per-clip performance (views, watch-through, saves, shares where available), the **teardown archive**, and aggregate trends feeding the optimal-time hints in §3.

### 5.1 States — Insights

| State | UI |
| --- | --- |
| `loading` | Skeleton metrics. |
| `fresh` | Numbers + "Updated just now." |
| `stale` | Numbers + "Updated 3h ago" + quiet refresh affordance. |
| `no-data-yet` | "This clip is still gathering views." (clip too new) |
| `platform-disconnected` | Calm re-auth prompt for the affected platform. |
| `offline` | Cached numbers + "Showing your last synced data." |

**Acceptance criteria (Insights):**
- AC-I1: Every metric view carries an explicit freshness timestamp.
- AC-I2: A disconnected platform shows a calm re-auth path, not an error.
- AC-I3: Teardown cards generated in Coach are retrievable here.

---

## 6. TRENDS (Trend Radar)

**One line on Today → a dedicated Trends screen** (Section-8 feature #2). Today shows a single trend line; tapping opens the full radar — a calm list of trending formats/sounds/topics relevant to the creator's pillars, each openable into a "make this" hand-off that pre-seeds a script in the Create flow.

> **Data source (committed, `08-format-virality.md §7.0`).** v1 = a **scheduled Claude + web-search external pass per active niche** (Haiku 4.5 + Message Batches, daily 06:00 UTC cron, cached and fanned out from cache — cost is flat in creator count). First-party signal (aggregated Marque-creator lift via the Insights pullback) **blends in per niche as volume accrues**, but is *not* a launch substitute — it has no data on day one. **Refresh cadence = daily per niche; the Today trend line is never blank** because the external pass always produces a cached row.

### 6.1 States — Trends

| State | UI |
| --- | --- |
| `loading` | Skeleton list. |
| `list` | Ranked trend rows; each → "make this." |
| `empty` | **Never blank at GA** — the external pass always yields a cached row. Only if a niche was activated <24h ago (before its first cron) show "Trends are still learning your space — we'll surface them shortly," and kick an on-demand external pass so the next read is populated. |
| `offline-cached` | Last-synced list + "Showing your last synced trends." |

---

## 7. BRAND PROFILE — the editable Brand Graph

The persistent **CONTEXT LAYER** differentiator, made editable. The Brand Graph is the single source injected (cached) into **every** Claude call (scripts, teardowns, voice checks) — see `07-ai-system.md`. Brand Profile is its calm, editable face.

### 7.1 Sections (one idea per section, generous whitespace)

| Section | Content | Editable |
| --- | --- | --- |
| What you want to be known for | The north-star line ("What do you want to be known for?") | ✓ |
| Voice & tone | Adjectives, sample phrasings, banned words | ✓ |
| Pillars | 3–5 content pillars | ✓ |
| Audience | Who it's for, their pains | ✓ |
| Do / Don't | Explicit guardrails | ✓ |
| Visual identity | Palette, on-camera notes | ✓ |
| Best posts | A small set of exemplars the AI learns voice from | ✓ |

### 7.2 Data projection

> Authority: `12-backend-data-security.md` (`brand_graph`).

```
brand_graph
  id            uuid pk
  user_id       uuid
  known_for     text
  voice         jsonb     // {adjectives[], sample_phrases[], banned_words[]}
  pillars       text[]
  audience      jsonb
  do_donts      jsonb     // {do[], dont[]}
  visual        jsonb
  exemplars     jsonb[]   // refs to best posts
  updated_at    timestamptz
```

### 7.3 Edit behavior

- **Autosave, optimistic.** Edits write to `brand_graph` rows and **invalidate the Coach/Script prompt cache** so new context propagates on the next Claude call (`07-ai-system.md`).
- Inline editing per section; no giant form. Save is silent (a small "Saved" whisper), failures are calm and retryable.

### 7.4 States — Brand Profile

| State | UI |
| --- | --- |
| `loading` | Section skeletons. |
| `populated` | Read view, serif section headers, generous spacing. |
| `editing` | Inline field active, optimistic autosave. |
| `save-failed` | Calm "Couldn't save — tap to retry"; local edit preserved. |
| `empty` | Onboarding incomplete → route to **brand-ingest** (`04-screens-create.md`). |
| `offline` | Read-only; edits disabled with a calm note. |

**Acceptance criteria (Brand Profile):**
- AC-B1: Saving any section invalidates the Claude prompt cache; the next Coach/Script call reflects the change.
- AC-B2: Edits are optimistic and survive a save failure (local copy not lost).
- AC-B3: An empty Brand Graph routes to brand-ingest rather than showing blank sections.

---

## 8. Cross-cutting DO / DON'T (applies to every screen above)

**DO**
- Treat every render / publish / transcribe as a **durable Trigger.dev job** with a Supabase status row + Realtime broadcast; never block the UI on a long job.
- Keep async state **granular** — per clip card, per scheduled post — never a full-screen spinner. This *is* the anti-clutter doctrine in motion.
- Compute IG/TikTok quotas from **actual publish timestamps over a trailing rolling 24h**.
- Cache the last good render / last synced numbers so a card or metric never goes blank.

**DON'T**
- Burn any **Marque branding into TikTok-bound video** (ToS violation, account-disable risk).
- Use **`AVAssetExportPresetPassthrough`** when a transform/overlay must apply.
- Run **`session.startRunning()`** or session config on **main**.
- Assume a **calendar-day reset** for IG/TikTok limits — it's a rolling 24h window.
- Ship **TikTok Direct Post** without the Query-Creator-Info preview + disclosure UI + a passing TikTok audit (otherwise capped at 5 users / restricted visibility).

---

## Open questions

1. **TikTok audit — launch blocker.** Unaudited apps are capped at 5 users / 24h with possibly restricted direct-post visibility. Does **Ayrshare's managed integration absorb TikTok's app audit**, or must Marque undergo TikTok's audit directly? This determines whether the publishing path is Ayrshare-managed or Marque's own TikTok app, and gates GA. (`10-social-publishing.md`)
2. **Trend Radar data source — DECIDED (`08-format-virality.md §7.0`).** v1 = the **Claude + web-search external pass per active niche** (daily cron, cached, flat cost), with first-party Marque-creator lift blending in per niche as volume accrues. Refresh = daily; the Today line is never blank. *Remaining open call lives in 08 Open Q2:* whether to additionally license a paid external trend feed post-GA for richer audio coverage.
3. **Render boundary — MCP ClipEngine vs. Shotstack.** Does **Shotstack** perform the final template render (with MCP only generating B-roll/AI-visual assets + virality scores), or does the MCP `personal-clipper` produce finished clips? This decides whether a format swap in the Clip Editor is a **Shotstack template re-render** or an **MCP re-clip**. (`01-information-architecture.md`, `07-ai-system.md`)
4. **Per-platform caption variants.** At schedule time, is the caption shared across IG/TikTok or platform-specific? Affects the `scheduled_posts` model and the per-platform toggle UI. (v1 assumes shared; model leaves room.)
5. **Optimal-time model ownership.** Computed in the **FastAPI orchestrator** from Insights pullback, or surfaced directly by Ayrshare/Phyllo? Affects where the hint logic lives and its latency.

---

## Sources

1. [Apple — AVCam: Building a Camera App](https://developer.apple.com/documentation/avfoundation/avcam-building-a-camera-app) — canonical camera-app architecture to mirror for Record.
2. [Apple — AVCaptureSession](https://developer.apple.com/documentation/avfoundation/avcapturesession) — `startRunning()` is blocking; configure off-main.
3. [createwithswift — Camera capture setup in a SwiftUI app](https://www.createwithswift.com/camera-capture-setup-in-a-swiftui-app/) — `UIViewRepresentable` + preview layer + background queue pattern.
4. [Apple — QA1744: setting `preferredTransform`](https://developer.apple.com/library/archive/qa/qa1744/_index.html) — orientation transform; passthrough ignores composition instructions.
5. [Apple — AVAssetExportSession](https://developer.apple.com/documentation/avfoundation/avassetexportsession) — export presets, trim time range, composition export for 9:16.
6. [AssemblyAI — Speech-to-Text](https://www.assemblyai.com/products/speech-to-text) — word-level timestamps, speaker labels, auto-chapters; basis for clip boundaries + caption sync.
7. [AssemblyAI — How to transcribe audio with timestamps](https://www.assemblyai.com/blog/how-to-transcribe-audio-with-timestamps) — word timestamps → caption sync + jump-to-moment.
8. [AssemblyAI — Speech Understanding](https://www.assemblyai.com/products/speech-understanding) — auto-highlights, sentiment, entity/IAB categories.
9. [Shotstack — Introducing the Templates endpoint](https://shotstack.io/learn/introducing-templates-endpoint/) — Templates + merge fields = the FORMAT-LIBRARY render-recipe model.
10. [Shotstack — Generate videos with overlays](https://shotstack.io/use-cases/scenarios/api/generate-videos-with-overlays/) — overlays, lower-thirds, kinetic text, burned-in captions via JSON edit.
11. [Shotstack — API reference](https://shotstack.io/docs/api/) — edit schema (tracks → clips → assets).
12. [Shotstack — Architecting with templates](https://shotstack.io/docs/guide/architecting-an-application/templates/) — one template → many variations.
13. [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) — mandatory query-creator-info, content preview, grey-out disabled interactions, commercial disclosure, no watermark, unaudited 5-user cap.
14. [TikTok — API v2 Rate Limits](https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit) — ~15–25 posts/day, 6 req/min/token.
15. [TikTok — Content Posting API: Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — direct-post flow.
16. [Meta — Instagram `content_publishing_limit`](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/) — 25 posts / rolling 24h; 2-step container→publish.
17. [Ayrshare — Instagram Graph API error 9 (25-post limit)](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/) — error-9 semantics; adapter normalization.
18. [Apple — Adopting drag and drop using SwiftUI](https://developer.apple.com/documentation/SwiftUI/Adopting-drag-and-drop-using-SwiftUI) — `.draggable()` / `.dropDestination()` for drag-to-schedule (iOS 17 baseline).
19. [Michael Abadi — Create a calendar view in SwiftUI](https://michaelabadi.com/articles/create-calendar-view-swiftui/) — draggable calendar walkthrough.
20. [Livsy — SwiftUI reorderable containers in iOS 27](https://livsycode.com/swiftui/swiftui-reorderable-containers-in-ios-27/) — `reorderable()` is iOS 27; use `draggable`/`onMove` on iOS 17.
21. [Supabase — Realtime Broadcast](https://supabase.com/docs/guides/realtime/broadcast) — prefer `broadcast_changes()` trigger over Postgres Changes for scalable status updates.
22. [Supabase — Subscribing to database changes](https://supabase.com/docs/guides/realtime/subscribing-to-database-changes) — Broadcast vs Postgres Changes tradeoffs.
23. [Supabase — Swift `subscribe` reference](https://supabase.com/docs/reference/swift/subscribe) — supabase-swift channel subscribe for live render/publish status.
24. [Apple — URLSessionConfiguration.background(withIdentifier:)](https://developer.apple.com/documentation/foundation/urlsessionconfiguration/1407496-background) — background config; upload tasks require a file body, transfers are discretionary/deferrable.
25. [Apple — Downloading files in the background](https://developer.apple.com/documentation/foundation/url-loading-system/downloading-files-in-the-background) — `handleEventsForBackgroundURLSession` relaunch handling; no automatic upload restart.
26. [Cloudflare — R2 multipart objects](https://developers.cloudflare.com/r2/objects/multipart-objects/) — S3-compatible CreateMultipartUpload / UploadPart / CompleteMultipartUpload for application-owned resumable upload.
