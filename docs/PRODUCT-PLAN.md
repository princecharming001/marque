# Marque — Master Product & Architecture Plan
## From "Half a Loop" to a Learning Creator Engine

This plan synthesizes five code-grounded analyses into one execution-ready blueprint. The organizing insight across all five: **Marque has already built the hardest-to-fake front half of the vision — a real server-side Claude brain that turns a login-less creator into niche insights → styled script ideas → a teleprompter capture — and then stops dead at the exact moment its differentiation begins.** Everything after "I finished filming" (transcript-driven per-style editing, B-roll from owned footage, real publish, performance learning) is mock or scaffold. The product is a loop with the back half drawn in pencil. This plan makes that loop real, and closes it so the app gets measurably smarter with every post.

---

## PART 1 — THE CREATOR'S JOURNEY & THE BIGGEST GAPS

### 1.1 What a real creator wants to do (the verb loop)

A creator does not think in nouns (scripts, clips, footage, calendar). They think in one repeating verb loop:

> **See what's working → get an idea in my style → film it easily → have it cut for me → schedule it with the exact caption → publish → learn what hit → do it again, better.**

Marque's current 5-tab IA (Today / Studio / Library / Calendar / Coach) is organized by object type, so this single loop is smeared across all five tabs, and the app breaks context at every hand-off.

### 1.2 What's genuinely built (DONE — do not rebuild)

- **Login-less onboarding → brand graph → pillars → styled scripts.** Real server-side Claude with per-style rubrics + exemplars (`prompts.py` `STYLES`), a generate-then-judge specificity gate on pillars (`main.py:206`), and Steer/Hook-Lab script editing. This is the strongest area and the template for everything else.
- **Teleprompter capture.** Real AVFoundation camera + auto-scrolling teleprompter with speed control (`RecordView.Teleprompter`).
- **Account link *preview*.** `/v1/connect/preview` scrapes the real public IG/TikTok profile (avatar, follower count) — genuine verification.
- **Scheduling & caption UX.** `CalendarView` week grid + `SchedulePickerSheet` + `PostEditorSheet` with a fully editable caption. The caption-exactly-as-posted requirement is already met at the UI layer.
- **Thin-client / server-brain architecture with keyless-mock degradation.** iOS holds no vendor keys; FastAPI holds all keys and returns `{"mode":"live"|"mock"}`. Every route degrades to a deterministic mock so the whole app runs at $0 in dev/CI. **This is the single most important asset in the codebase** — every new capability must preserve it.

### 1.3 The biggest gaps, prioritized (what blocks the vision)

| # | Gap | State | Why it's blocking |
|---|-----|-------|-------------------|
| **1** | **Transcript-driven, per-style auto-edit** | **Mock end-to-end.** `LiveClipEngine.makeClips/render` just call `MockClipEngine`; `submitTranscription`/`submitRender` are defined but never called; no backend transcribe/render/orchestration route exists. | This *is* the product. Everything downstream (real publish, captions, learning) is blocked on it. Filming currently produces a placeholder clip pointing at raw footage. |
| **2** | **Real publish + public media URL** | Partial / not production-safe. No render → no public `remoteURL` → Ayrshare would post caption-only. Client-side Ayrshare key ships on-device (violates the trust model). Backend `/v1/publish` exists but iOS never calls it. `canPublish` defined but never gates anything. | Even a perfect schedule can't post a video. |
| **3** | **Performance tracking + learning loop** | **Missing.** `PostMetrics` is modeled but never written. No analytics fetch, no backend analytics route, no learning of any kind. All "performance" is projected from `predictedScore`. | "Tracks all performance and LEARNS" is the entire promise of the flywheel — currently fiction. |
| **4** | **Auth / durable per-user identity** | Missing. `RootView` gates on `hasOnboarded`; rows keyed by device UUID; `token`/RLS stubbed. | Prerequisite for a per-user learning loop, multi-device, and safe publishing. |
| **5** | **Grounding onboarding in real evidence** | Partial. `analyzePage()` calls `generatePillars` with the brand form only — it **never passes real scraped posts**, and the purpose-built `/v1/brand-scan/handle` + `/v1/voice-onboarding/*` endpoints have **zero iOS callers**. Linking is scrape-based, not OAuth (can't publish on the user's behalf). | The two best backend features are already built and simply unwired. Highest ROI per line of code. |
| **6** | **Media/footage content analysis + B-roll retrieval** | Missing. No vision pass on uploads; `mediaContext` is only a user-typed tag string. No asset→beat matching. | "Pull B-roll from owned footage" can't be real until the corpus is analyzed and indexed. |
| **7** | **Inline teleprompter editing while filming** | Missing. The prompter is read-only; the only "edits" are AI rewrites in the reader. | Explicitly in the vision ("easy to edit the script on a teleprompter while recording"). Small but visible. |

**The through-line:** fix #1 and #2 to make the loop *complete*; fix #3 to make it *smart*; fix #4/#5 to make it *personal and safe*; fix #6/#7 to make it *feel intelligent and seamless*.

---

## PART 2 — THE REDESIGNED SEAMLESS FLOW (STEP BY STEP)

The redesigned experience collapses the 8-surface journey (Studio → sheet → sheet → reader → cover → Library → Calendar → sheet) into **one uninterrupted Create flow** wrapped by a home feed that already knows what you should post, and a plan surface that shows you what worked.

### The narrative, end to end

**Onboarding (first run).** Two paths, both already backed server-side, now wired:
- **Established creator:** links their handle → `/v1/connect/preview` verifies it → **`analyzePage()` now calls `/v1/brand-scan/handle` with the real scraped posts** (currently it doesn't), deriving voice + pillars from what they actually post, and seeding cold-start priors from what visibly over-performed on their page.
- **Small/no account:** taps **"Teach Marque your voice"** → the existing `/v1/voice-onboarding/session` ElevenLabs flow (currently unwired in iOS) lets them *talk* about what they want to post; `/finalize` produces the same brand graph.
- Either way they land on **Home** with real pillars and preferred styles set.

**1. Open the app → Home tells you what to post.** The momentum card shows real (once data exists) or projected numbers with a coaching line ("Your myth-busting talking-heads get 2× saves — leaning in"). Below it, the **pillar carousel** (promoted up from Studio) shows *your* content angles annotated with *why now* (trend + teardown join). A **"What to post next"** section surfaces the top 3 bandit-ranked arms as ready-to-generate cards. This turns the passive `todayDirective` into an active, personalized queue.

**2. Tap the center Create FAB → Pick (idea + style, fused).** One screen replaces the two sheets. Choose an idea (a pillar, a trend, or "blank — talk it out") and a style, shown as 9:16 `StylePreview` schematics so you *see* talking-head vs 3-way-split vs faceless. Preferred styles float first; the bandit marks one **"Best for you"** with a one-line reason. One tap → generate 3 scripts.

**3. Read + edit the script (now editable).** `ScriptReaderView` keeps hook/format/body/shot-plan/Steer, but **body text becomes directly editable** (tap-to-edit). CTA: "Film this →" advances the *same* flow (no context-breaking full-screen cover).

**4. Teleprompter record (now editable while filming).** Camera + auto-scroll + speed control as today, plus the missing capability: **tap any teleprompter line to pause scroll, edit it inline, resume.** Because the auto-edit is transcript-driven, what you *say* is what's cut — so fixing a clunky line mid-take is exactly right. After stop: a keep/retake bar. The format picker is **removed from this screen** (cognitive-load spike at the worst moment) and moved to review.

**5. Submit → "Make my clips."** Saves the raw take to Footage, mints an R2 upload URL, uploads bytes **direct to object storage** (skipping the backend), and creates a `clip_jobs` row. The screen dismisses immediately — the creator is free; nothing blocks. Behind the scenes the job graph runs: `transcribing → editing(per style) → rendering(per style) → qa → ready`.

**6. Cook → live status, never a bare spinner.** In Library / the Create review, clip cards show real stage labels ("Listening to your take…", "Cutting the talking-head…", "Rendering…") from the job model, with calm "we're cutting your week, we'll ping you" copy.

**7. Auto-edit + review.** When ready, a review card shows the rendered vertical preview, kinetic captions burned from **what you actually said**, the **B-roll chips the AI pulled from your Library** ("+ desk clip, + gym b-roll"), an optional "we removed 6 ums, tightened 4s" teardown (straight off the EDL `drops`), and the **per-style caption, editable exactly as it will post.**

**8. Schedule inline (no tab jump).** The scheduler slides up *within the flow*: time, platform chips showing real connected accounts with avatars, auto-captions toggle, caption pre-filled. Confirm → `scheduleClip` (now gated on `canPublish`) → celebration → back to Home with the ring advanced. **Batch loop:** the celebration offers **"Film the next one"** → jumps straight back to step 2 with the next pillar queued. Film five in one sitting without leaving the flow.

**9. Publish seamlessly (server-side).** Scheduled posts publish via the backend `/v1/publish` path (iOS wired to it, client-side Ayrshare key retired), fed the real rendered public URL, mapped from the creator's verified handle to their Ayrshare/OAuth profile.

**10. Track & learn.** Backend cron polls analytics at T+1h/6h/24h/72h/7d; once a post "settles," it computes the outcome label `y` and updates the per-creator model. Home's coaching line, the pillar annotations, the "Best for you" style badge, and Plan's inline teardowns all get sharper — visibly.

---

## PART 3 — LAYOUT / NAVIGATION REDESIGN (SCREEN BY SCREEN)

**Core move: 5 noun-tabs → 4 verb-tabs + a raised center Create FAB.** The single most-repeated action gets the single most-reachable target. Insights move *out* of the buried Coach tab to the two moments they matter: **Home** (deciding what to make) and **Plan** (seeing what worked).

| New tab | Absorbs | Purpose |
|---|---|---|
| **Home** (`sun.max`) | Today + top of Studio (pillars) + Coach's trend/teardown teasers | One feed that answers "what should I post" using insights |
| **Create** (center FAB, raised) | Studio scripts + ScriptReader + Record + auto-edit + review + schedule | The guided linear flow — a launch button, not a browse tab |
| **Plan** (`calendar`) | Calendar + the "ready clips" tray | Drop finished clips on the week, publish, see results inline |
| **Library** (`rectangle.stack`) | Footage + Media + Clips archive | Owned assets, **now auto-analyzed**, + past work |

### HOME — "what to post"
Reuses `TodayView`'s scaffold, re-sequenced around the decision: **insight (momentum) → directive (your move) → fuel (pillar carousel with "why now") → opportunity (trend teaser).** Cold-start variant: for a no-account creator, the momentum card is replaced by a **"Teach Marque your voice"** card launching the voice-onboarding flow. A small **"learning" meter** ("Marque has learned from 12 of your posts — recommendations sharpen at 15") explains *why* early recs are generic and motivates posting.

### CREATE — the guided flow (`CreateFlowView` coordinator)
A horizontal step machine that owns its own progress rail and hides the tab bar (`router.hideTabBar = true`). Steps ①Pick (idea+style fused) → ②Read+edit (editable body) → ③Teleprompter (editable while filming) → ④Auto-edit + review (preview, caption, B-roll chips, teardown) → ⑤Schedule (inline sheet) → Celebration → "Film the next one" loop. This is the heart of the redesign: **one FAB tap → 5 linear steps → done.**

### PLAN — schedule & publish the week
Keep `CalendarView`'s 7-day list + `PostEditorSheet`/`postNow`. Add: (a) a **"ready to schedule" tray** — horizontal strip of `.ready` clip posters you **drag onto a day**; (b) **post-publish learning inline** — once a post has `metrics`, its row shows views + a ▲/▼ vs the creator's median, tapping opens the `TeardownCard` right there. Closes the loop where the creator is already looking.

### LIBRARY — owned assets, now analyzed
Keep the Clips/Footage/Media tabs + bulk import. The upgrade: **auto-analyze on import** (no more forced manual tagging). A banner reads **"Marque can use N of your clips as B-roll,"** and `MediaEditSheet` shows the AI's description + editable auto-tags + a suitability meter + "used in N clips." The corpus becomes a visibly *working* asset library. Clips sub-tab becomes the archive (active scheduling moved to Plan).

**Nav files to change:** `MarqueTabBar.swift`, `RootTabView.swift`, `AppRouter.swift` (add `.create`, drop `.coach`).

---

## PART 4 — THE LEARNING ALGORITHM ("The Algorithm")

A per-creator recommendation engine that ingests real performance and feeds back into script generation, style choice, posting time, trend selection, and a ranked "what to post next" feed — with **zero new vendor keys on device**. All state and math live in FastAPI + Supabase, exactly like `prompts.py`/`main.py` today. iOS never computes the ranking; it POSTs metrics and GETs decisions, so the algorithm improves without an app release.

### 4.1 The unit of learning: the **arm**
Every post is already a labeled experiment — Marque's whole ontology is the tuple the creator bets on *before* generation, and those fields already exist on `Script`/`Clip`:

```
arm = (pillar, style, formatId, hookSignal, length_bucket, dow_slot)
```

Full 6-D arms are too sparse, so we learn a **marginal model** (one effect table per dimension) plus a few content-meaningful **2-way interactions** (`pillar×style`, `style×hookSignal`, `pillar×hookSignal`). Partial pooling: one post updates *all* the marginals it belongs to, so 20 posts give signal on every dimension even though no 6-D cell has 20 samples.

### 4.2 Signals + reach-normalization
Extend `PostMetrics` with the two most-predictive fields Marque isn't capturing: **`saves`** ("I'll act on this") and **`avg_watch_pct`** (retention — the single strongest algorithm signal), plus `reach`, `link_clicks`. Everything is **reach-normalized against the creator's own trailing median** (robust to viral flukes), so a 500-follower and a 500k-follower creator both get honest per-arm learning:

```
save_rate = saves / max(reach,1);  follow_rate = follows_gained / max(reach,1);  etc.
creator_baseline_reach = trailing median reach of last ~10 posts
```

### 4.3 The outcome label `y ∈ [0,1]`
One scalar per post so every downstream model is 1-D — a **goal-weighted, robust-z-scored blend**:

```
y = σ( Σ_k  w_k(goal) · z_k ),   z_k = (x − median)/(1.4826·MAD)
```

Weights `w_k` depend on `BrandGraph.goal` (Grow audience weights follows+shares; Build authority weights saves; Get clients weights comments+link_clicks; Monetize weights saves+link_clicks). Because it's z-scored against the creator's own history, `y≈0.5` = "typical for you," `y>0.7` = "a hit for you," independent of follower count. **This label replaces the fake `predictedScore` projections** in `weekViews`/`weekFollows` once data exists.

### 4.4 Phase 1 — transparent weighted score (ships first)
Per dimension value, keep a **shrunk mean** (empirical Bayes, no dependencies):

```
effect(v) = ( Σ y_i + κ·prior(v) ) / ( n_v + κ ),   κ≈5
lift(arm)  = base + Σ_dim (effect(v)−0.5) + Σ_pairs interaction(pair)
```

Transparent and directly renders Coach copy ("myth-busting: +38% vs your average"), same shape as the existing `TeardownCard.liftPercent`. **Confidence gate:** no claim until `n_v ≥ 4`; "early read" between 4–8, "confirmed" past ~8.

### 4.5 Phase 2 — contextual Thompson sampling (~15–20 posts in)
Keep a **Beta-Bernoulli bandit per dimension value** (`α=1+Σy, β=1+Σ(1−y)` — conjugate, trivial, no matrix math). To rank candidates: sample `θ_dim ~ Beta` per dimension, `score(arm)=Σθ_dim + interactions`, argmax the samples. Under-tested arms (wide Beta) occasionally win → principled exploration; proven winners exploit.

**Guardrails (ported from the hardened Cadence learning loop):**
- **Idempotent, settled rewards only** — a post feeds the bandit exactly once, after metrics settle (dedupe on `post_id`; without this, every poll double-counts and the bandit corrupts).
- **Freshness decay** `w = 0.97^weeks_ago` — track the creator's evolution, not their 6-month-old self.
- **Exploration floor** — keep ≥15% mass on non-winners so the creator isn't trapped posting only myth-busters.
- **Min-reach guard** — posts with `reach < 20` logged but excluded (noise floor).

### 4.6 Cold-start (new / small creators)
Layered fallback, strongest-available wins: (1) **onboarding scan** seeds weak per-arm priors from what visibly worked on their existing page; (2) **niche population priors** (`niche_priors`, cross-creator) — a brand-new fitness creator inherits "myth-buster talking-heads over-index on saves for fitness coaches," so the *first* feed is informed, not random; (3) **format-catalog defaults** (`bestHooks`); (4) priors auto-wash-out as `n_v` grows (no graduation logic). First ~15 posts run a **deliberate exploration schedule** across the creator's preferred styles/pillars so the bandit gets coverage.

### 4.7 Feeding it back into the 5 surfaces
- **(a) Script generation** — inject a `learning_block` into `scripts_prompt`: "myth-buster + contrarian hooks outperform this creator by 38% (12 posts); faceless underperforms −22%. Lean contrarian, ≤30s." The judge additionally rejects known-loser hook signals; `predictedScore` gets **calibrated** against real `y`.
- **(b) Style recommendation** — `StylePickerSheet` marks the top bandit-sampled style "Best for you" with a reason (creator still chooses).
- **(c) Posting time** — `time_slots` suggests the top slot ("Thu 6–7pm — your Thu evenings get 1.8× reach").
- **(d) Trend selection** — re-rank niche trends by `effect(style_for(formatId))` so trends the creator is *good at executing* float up.
- **(e) Ranked home feed** — Thompson-sample N candidate arms, return top 3 as ready-to-generate cards deep-linking into Create pre-seeded with that arm.

### 4.8 How it *visibly* gets smarter (the trust surface)
Learning must be legible or it feels like a black box: Coach "What worked" cards become data-driven `TeardownCard` rows gated by `n_v≥4`; **"early read" vs "confirmed" chips** show the model maturing; an Insights **"Your winning formula"** block renders the top `arm_stats` rows; a per-script **"matches what works for you"** indicator closes the loop visibly; the **learning progress meter** sets expectations. The thing the algorithm learned becomes the thing it's now generating — and the UI says so.

---

## PART 5 — MEDIA/FOOTAGE ANALYSIS + AUTO-B-ROLL

Today `mediaContext` is the *only* place media touches the AI — a flattened string of user-typed tags. The vision needs the corpus to be **understood** so the editor can pull the right clip into the right beat. This bolts onto the existing seams with one analysis pass on import, a semantic record, object storage, a retrieval endpoint, and one new field on the EDL beat.

### 5.1 Analysis pipeline (runs on import, once per content hash)
On `store.addMedia`, per asset:
1. **Device-side prep (free, no keys):** SHA-256 content hash (dedupe/idempotency), thumbnail, and for video **local keyframe extraction** via `AVAssetImageGenerator` (~6 frames — this is what the vision model sees, so we never upload/decode full video just to look at it), plus duration/orientation/audio-track and on-device dominant-color palette (`CIAreaAverage`) and blur score (Laplacian variance).
2. **Upload bytes to object storage** → durable `storage_key` + signed URL.
3. **Backend analysis — one multimodal Claude call per asset** (`POST /v1/media/analyze`) over the image or keyframe strip, returning structured JSON: `description` (written in the same concrete/searchable register as `shotPlan` cues), `scene`, `subjects`+`has_face`, **`on_screen_text` (OCR done by the multimodal model — no separate OCR service)**, `motion`, `quality`, `dominant_colors`, **`broll_suitability` 0–100 + why**, `usable_as` (a to-camera clip is a *take*, not B-roll), `suggested_kind`, `tags`.
4. **Embedding** — embed the caption text (`description`+`tags`+`scene`+`on_screen_text`) with **Voyage `voyage-3`** into pgvector. We embed *text*, not pixels, because the query side is always text (a `shotPlan` cue) — one vector space, plain cosine match. (CLIP image embeddings are an optional later add.)

**One Claude vision call, not a pipeline of CV services** — it returns scene+subjects+OCR+motion+description+suitability in one structured JSON, in the creator's-domain vocabulary, at one cost, paid **once per asset ever** (not per render). Device handles only what's genuinely cheaper locally. Async + resilient: asset shows immediately (`status: analyzing`), fields fill in later, degrades to `local_only` on failure, content-hash gate means retries never re-bill.

### 5.2 Retrieval — beat → clip semantic match
**The query is already written:** faceless/fast-cut `shotPlan` entries are concrete visual cues (`"0–3s: close-up of dense torn crumb"`) — parse the timestamp off, keep the description as `cue_text`. Per beat: embed `cue_text` → pgvector cosine ANN (hnsw, top-K≈15) → **re-rank** by `0.55·similarity + 0.20·suitability + 0.10·motion/style-fit + 0.10·diversity/novelty + 0.05·palette-harmony`, with hard filters first (correct `usable_as`, min sharpness, 9:16-compatible, duration ≥ beat). **Threshold + fallback:** score ≥ τ (~0.62) → owned asset; else **Pexels/Pixabay stock** (query = `cue_text`); else generative. Optional cheap **Haiku tie-break** when top candidates are close, producing the UX rationale ("Picked your dough close-up — matches beat 2"). This is the same generate-then-judge discipline as pillars, reused as select-then-justify.

### 5.3 UX
Grid cells shimmer while analyzing then gain an auto-tag row + suitability dot; the corpus counter gains "12 ready for B-roll · 3 analyzing." `MediaEditSheet` shows the AI description (read-only), removable AI tags + add-field (editing re-embeds only, ~1000× cheaper than re-analyzing), a suitability meter with the model's "why," detected on-screen text, and **"used in N clips."** In the EDL review, each beat renders its matched owned thumbnail with a swap button opening a **beat-scoped, semantically-ranked picker** (owned → stock → generate). **Onboarding hook:** after bulk import, Marque can say *"I see kitchen + hands + dough footage — I'll lean your faceless scripts on visuals you already own."*

---

## PART 6 — TRANSCRIPT-DRIVEN PER-STYLE AUTO-EDITING (the core)

**The EDL is the contract, and it is per-style.** The editor is not one monolithic AI editor — it's **five specialized editors that all emit the same artifact**: an Edit Decision List (JSON, frame-coordinate cuts/captions/overlays/B-roll). The EDL *schema* is universal (one Pydantic model, one validator, one renderer dispatch); how it's *produced* is per-style (five prompts + five few-shot EDL exemplars + five Remotion compositions). This mirrors the existing `STYLES` dict exactly — we add a symmetric `edit_rubric` + `edl_exemplar` to the same five keys, so a style can never drift between its script prompt and its editor prompt.

> **Renderer decision: Remotion Lambda, not Shotstack.** The locked pipeline plan supersedes the Shotstack references in `docs/09` + `LiveClipEngine.swift`. The vestigial `submitRender` Shotstack call is dead and should be deleted — it's the one place the codebase contradicts the locked decision.

### 6.1 How each style is "trained" (prompt-and-template specialization, not fine-tune)
Four layers make each style's editor an expert:
- **Per-style `edit_rubric` (system prompt).** *talking_head:* keep the whole take, cut only filler/dead-air/false-starts, exactly one punch-in (1.0→1.08) on the load-bearing line, kinetic word-by-word captions, never B-roll over the face during the hook. *faceless:* no speaker, every second has a visual mapped from the 3 beats to owned B-roll (fallback Pexels), captions are the primary channel. *split_three:* detect the three "Solution 1/2/3" boundaries, render each in its vertical third, best segment gets most time. *fast_cuts:* hard cut on every numbered line, trim inter-line silence ≤80ms, pop caption cards. *green_screen:* speaker keyed over a reference screenshot pinned as background, highlight box on point-gestures.
- **Per-style few-shot `edl_exemplar`** — one worked transcript→EDL example per style (the single highest-leverage quality lever; the model copies the *shape*).
- **Per-style Remotion composition** — the EDL is the props object; layout intelligence (split panels, punch-in interpolation, caption typography) lives in the composition, so the model describes *decisions*, never pixels.
- **Style-scoped inputs** — green_screen gets the reference list; faceless gets the B-roll corpus; talking_head gets neither. The model can't misuse tools the style lacks.

### 6.2 How the transcript drives the cuts
AssemblyAI returns word-level `{word, start_ms, end_ms}` + `auto_highlights` + pause structure. Everything downstream is in **frames at 30fps** (`frame = round(ms/1000*30)`); the model reasons in seconds, a deterministic post-pass snaps to frames + word boundaries. The Claude pass produces the KEEP `segments` by: dropping fillers (model + a deterministic lexicon/regex, defense in depth), dropping dead-air/false-starts (gap thresholds: talking_head 350ms, fast_cuts 80ms), selecting strongest moments if over length (rank by salience + hook-proximity + pillar-noun presence; hook + CTA pinned, never dropped), tightening pacing, placing **kinetic captions from what was actually said** (replacing today's naive script-text chunking), placing B-roll per beat (§5), and applying the style layout. **The cuts are a direct function of what was said and when.**

### 6.3 The EDL schema (universal contract)
One Pydantic model (`extra="forbid"`): `segments` (KEEP list, source-frame `src_in/src_out`, concatenated = the cut), `drops` (audit trail → powers "we cut 6 ums"), `captions` (per-word timings for active-word highlight), `overlays` (punch-in, text-card), `broll` (per-beat `asset_id` owned | `broll_query` pexels), `layout` (style-specific block), `audio` (LUFS normalize). Source-frame indices make the EDL **idempotent, inspectable, diffable, and re-renderable without re-invoking Claude.**

### 6.4 Job model + QA gate
Extends `ClipStatus` with sub-states; server-truth in a `clip_jobs` row, client `Clip.status` is a projection. Job graph (parent + per-style fan-out, **partial success allowed**): `uploaded → transcribing → editing(style) → rendering(style) → qa → ready`. Rules: **idempotent on `source_id`** (retry never re-transcribes/double-bills), per-style retry, webhooks HMAC-verified before any DB write, EDL persisted so a render retry skips the Claude call. iOS polls `GET /v1/clips/{job_id}` and fills existing `Clip` fields — **no `Models.swift` change needed.**

**Two-guard QA (generate-then-judge applied to edits):** (1) deterministic schema+invariant validator (segments monotonic + in-bounds, hook/CTA present, duration in band, split_three has exactly 3 segments, green_screen has a ref) → one repair round → else a **safe default EDL** (whole take, filler-strip, timed captions) so a clip is *never* blocked; (2) post-render ffprobe QA (1080×1920, H.264+AAC, faststart, ≤ platform max) → re-render once → else mark that style `failed`.

### 6.5 The one seam this snaps into
`LiveClipEngine` already conforms to `ClipEngineProtocol` and just forwards to the mock. Deliver the whole pipeline by making `makeClips` = mint+upload+create-job and `render` = poll `GET /v1/clips/{job_id}`. **Zero changes to `Adapters.swift` protocols, `Models.swift`, `RecordView`, or the `AppStore.makeClips` call site.** The `clipEngine` gate flips from "Shotstack keys present" to "backend available."

---

## PART 7 — DATA MODEL + BACKEND + INFRASTRUCTURE

### 7.1 Trust model (invariant — preserve at all costs)
Three planes, unchanged: **device holds no vendor keys**; **backend (FastAPI) holds all keys** and returns `{"mode":"live"|"mock"}`; **every route degrades to a deterministic keyless mock**. iOS uploads bytes **direct to object storage** via backend-minted presigned URLs (fast, doesn't proxy through FastAPI). All new routes follow the established Pydantic-request / `if not KEY: return mock` / `{"mode":...}` envelope pattern.

### 7.2 Storage
**Cloudflare R2 for media bytes** (photos, source videos, keyframes, rendered clips), **Supabase Postgres for metadata + pgvector + auth**. Rationale: `Clip.remoteURL`/`ScheduledPost.mediaURL` already assume public HTTP URLs (Ayrshare, Remotion, the vision model all pull the same asset repeatedly) and R2 has **zero egress fees** — decisive when B-roll is re-read on every render. (Supabase Storage is a one-line swap if the founder wants to collapse vendors.) Device never holds R2 keys: `POST /v1/media/upload-url` / `POST /v1/uploads/mint` return presigned PUTs.

### 7.3 Supabase schema (RLS scoped `user_id = auth.uid()`)

**Auth & identity:** `creators` (id, niche, goal, baseline_reach/mad — the learning-loop prerequisite; replaces device-UUID keying).

**Learning:** `posts` (one row per published post = one labeled experiment, arm denormalized for fast group-bys), `post_metrics` (time-series snapshots with `settled` flag), `post_outcomes` (the label `y` + normalized rates), `arm_stats` (the entire brain — ~30–60 rows/creator: `dimension, value, n, sum_y, alpha, beta, effect`), `arm_interactions`, `niche_priors` (cross-creator cold-start), `time_slots` (posting-time model).

**Media:** `media_asset` (semantic fields + `content_hash` unique per user for dedupe + `storage_key`/`remote_url`), `media_embedding` (`vector(1024)`, hnsw cosine index), `broll_usage` (media↔clip↔beat, `match_score`, `chosen_source` — powers "used in N clips" and seeds the learning loop with which owned assets end up in high-performing posts).

**Editing:** `clip_jobs` (job graph state, idempotent on `source_id`, persists the EDL).

### 7.4 iOS model deltas (minimal)
- `PostMetrics` gains `saves`, `reach`, `avgWatchPct`, `linkClicks` (the arm fields already exist on `Script`/`Clip`).
- `MediaAsset` gains `contentHash`, `storageKey`, `remoteURL`, an embedded `MediaAnalysis` struct, `analysisStatus` (round-trips through the existing Snapshot/SupabaseStore persistence with no new plumbing). `note` stays the *user's* editable tag; `aiTags` is the model's suggestion.
- **No change** to `ClipEngineProtocol`, `Clip`, `RecordView`, or the `makeClips` call site.

### 7.5 New backend routes (all mock-degrading)
Auth: `POST /v1/auth/*` (Sign-in-with-Apple → Supabase JWT → set `BackendClient.token`). Onboarding: wire the existing `/v1/brand-scan/handle` (with real posts) and `/v1/voice-onboarding/*`. Media: `/v1/media/upload-url`, `/v1/media/analyze`, `/v1/broll/match`, `/v1/media/index`. Editing: `/v1/uploads/mint`, `/v1/clips`, `/v1/edit` (internal worker), `GET /v1/clips/{job_id}`. Publish: wire iOS to existing `/v1/publish`; retire client-side Ayrshare. Learning: `/v1/posts/register`, `/v1/metrics/ingest`, `GET /v1/recommendations`, `GET /v1/insights/learned`; personalize `/v1/trends`; the poll/settle cron. Prompts: add `media_analyze_prompt`, `edl_prompt(style,…)`, `edl_judge`, and the `learning_block` into `scripts_prompt`/`hooks_prompt`, all in `prompts.py`.

### 7.6 Worker / async
A small job/queue behind the analyze + clip routes (bulk import fans out 40 items concurrently; clip fan-out runs per-style in parallel). AssemblyAI + Remotion via HMAC-verified webhooks; iOS polls `GET` endpoints (Supabase Realtime later).

---

## PART 8 — PHASED ROADMAP (DONE vs NEW)

Each phase is independently shippable and degrades gracefully (keyless-mock everywhere), consistent with how the codebase already gates live-vs-mock.

### ✅ DONE (do not rebuild)
Login-less onboarding → brand graph → judged pillars → per-style scripts; Steer/Hook-Lab editing; teleprompter capture; account-link *preview* scrape; schedule + editable caption UX; thin-client/server-brain + keyless mocks; backend `/v1/brand-scan/handle` and `/v1/voice-onboarding/*` (built but unwired); `/v1/publish` (built but uncalled).

### Phase 0 — Foundations & cheap wins (unblocks everything)
- **NEW:** Auth (Sign-in-with-Apple → Supabase JWT → set `token`, key rows by user not device UUID, turn on RLS). *(Gap #4)*
- **WIRE (already built):** `analyzePage()` → `/v1/brand-scan/handle` with **real scraped posts**; add the **voice-onboarding** entry point on Home. *(Gap #5)*
- **NEW (small):** inline-editable teleprompter + editable script body. *(Gap #7)*
- **NEW:** enforce `canPublish` on schedule/publish. *(Gap #2)*

### Phase 1 — The editing pipeline (the product)
- **NEW:** R2 + presigned upload; `clip_jobs`; `LiveClipEngine.makeClips`=mint+upload+job, `render`=poll; **delete dead Shotstack `submitRender`.**
- **NEW:** AssemblyAI transcription; the 5 `edit_rubric`+`edl_exemplar` in `STYLES`; `backend/app/edl.py` (model+validator+repair+filler-lexicon+frame-snap); `edl_prompt`; the two-guard QA gate.
- **NEW:** `render/` Remotion project — 5 compositions + shared `<Captions/>`.
- **NEW:** live job-status UI (never a bare spinner). *(Gap #1)*

### Phase 2 — Real publish
- **REWIRE:** iOS → backend `/v1/publish`; retire on-device Ayrshare key; feed the real rendered public URL; map verified handle → publish profile (OAuth for post-on-behalf). *(Gap #2)*

### Phase 3 — Media analysis + auto-B-roll
- **NEW:** device keyframe/palette/hash; `/v1/media/analyze` (one Claude vision call) + `media_analyze_prompt`; extend `MediaAsset` + `MediaEditSheet`.
- **NEW:** Voyage embeddings + pgvector hnsw + `/v1/broll/match`; parse `shotPlan`→beats, `beat.broll` field into Remotion merge slots; Pexels→generative fallback; `broll_usage` + "used in N clips." *(Gaps #6)*

### Phase 4 — The learning loop
- **NEW:** extend `PostMetrics` (saves, watch_pct); `/v1/posts/register` from `scheduleClip`/`postNow`; `/v1/metrics/ingest` + poll/settle cron + manual-entry sheet; `post_outcomes` (`y`).
- **NEW:** Phase-1 transparent `arm_stats` → power Coach "what worked" + calibrate `predictedScore`; `learning_block` into generation; style/time/trend personalization.
- **NEW:** Phase-2 Thompson bandit + `niche_priors` cold-start + ranked "what to post next" home feed; the trust-surface UI (early-read/confirmed chips, learning meter, "winning formula"). *(Gap #3)*

### Phase 5 — Layout consolidation
- **NEW:** 5 noun-tabs → 4 verb-tabs + center Create FAB; fuse the Create coordinator; move insights to Home + Plan; ready-clip tray + inline teardowns in Plan. *(Ships incrementally alongside earlier phases; the Create coordinator can wrap existing screens before they're individually upgraded.)*

---

## PART 9 — KEY RISKS & EXTERNAL SERVICES/KEYS

### External services & keys (all backend-only, all mock-degrading)
| Service | Purpose | Notes |
|---|---|---|
| **Anthropic (Claude)** | scripts/pillars/EDL generation, media vision analysis, Haiku tie-break/tagging | Opus for generation, Haiku for judge/caption/insights/tagging (cost tier) |
| **AssemblyAI** | word-level transcription + auto_highlights | transcribe once per take, edit N styles |
| **Remotion Lambda** | per-style rendering (~$0.05/clip) | ~10× cheaper self-hosted (OpenMontage) past ~3–5k/mo — swap-safe |
| **Cloudflare R2** | media bytes + rendered clips | zero egress; keys `R2_ACCOUNT_ID/ACCESS_KEY/SECRET/BUCKET/PUBLIC_BASE` |
| **Voyage** | `voyage-3` text embeddings for B-roll retrieval | `voyage-3-lite` if volume grows |
| **Supabase** | Postgres + pgvector + auth + RLS | system-of-record + learning brain |
| **Ayrshare / IG OAuth** | publish + analytics | move server-side; OAuth needed to post-on-behalf |
| **Pexels/Pixabay** | B-roll fallback | keyed, backend |
| **ElevenLabs** | voice onboarding | already prompt-designed server-side |

### Top risks & mitigations
1. **Edit quality is the make-or-break.** A bad auto-cut kills trust instantly. *Mitigation:* per-style few-shot EDL exemplars (highest-leverage lever), the two-guard QA gate, and the safe-default EDL so a clip is never blocked. The EDL is inspectable/diffable so failures are debuggable.
2. **Publish reliability & OAuth scope.** Scrape-based linking can read follower count but *cannot publish on the user's behalf* — real IG posting needs OAuth + a Business/Creator account. *Mitigation:* Phase 2 adds OAuth; until then, scheduling works, publish is gated.
3. **Cost creep at scale.** Render dominates (~$0.08–0.12 all-in per finished clip). *Mitigation:* transcribe-once/edit-many, Haiku for classify/judge, content-hash gate so re-imports never re-bill, R2 no-egress, Remotion self-host past a volume threshold.
4. **Cold-start feels generic.** A no-account creator with zero posts. *Mitigation:* niche population priors + onboarding-scan seeds + the honest learning meter ("sharpens at 15 posts").
5. **Learning-loop corruption.** Double-counting metrics silently poisons the bandit. *Mitigation:* settled-only + once-per-post idempotency + min-reach guard + freshness decay — all ported from the hardened Cadence loop.
6. **Media privacy & moderation.** Owned footage may contain third parties or sensitive content. *Mitigation:* per-user RLS, content-hash dedupe, suitability rubric that down-ranks watermarks/others-talking, and never auto-publishing without the review step.
7. **Scope discipline.** The temptation is to rebuild the working front half. *Mitigation:* this plan's DONE list — the front half and two unwired backend features (`brand-scan`, `voice-onboarding`) are assets to *wire*, not rebuild.

---

### The one-sentence thesis
Marque already has a real brain and a real capture surface; this plan builds the **transcript→EDL→render→publish→learn** back half onto the exact seams that already exist (`ClipEngineProtocol`, `STYLES`, `PostMetrics`, `shotPlan`, the mock-degrading route pattern), so the app becomes a single seamless verb-loop that **measurably gets smarter with every post** — without ever breaking the keyless-mock, no-keys-on-device architecture that makes it testable and safe.

---

**Load-bearing files (all absolute):** pipeline — `/Users/home/Marque/ios/Marque/Adapters/LiveClipEngine.swift`, `/Users/home/Marque/backend/prompts.py` (`STYLES`), new `/Users/home/Marque/backend/app/edl.py`, new `/Users/home/Marque/render/`; media — `/Users/home/Marque/ios/Marque/Features/Media.swift`, `LibraryView.swift`, `/Users/home/Marque/ios/Marque/Models/Models.swift` (`MediaAsset`); learning — `/Users/home/Marque/ios/Marque/State/AppStore.swift` (`makeClips`, `mediaContext`, `weekViews/weekFollows`), `PostMetrics`; onboarding wiring — `/Users/home/Marque/ios/Marque/State/AppStore.swift` (`analyzePage`), `/Users/home/Marque/backend/main.py` (`/v1/brand-scan/handle`, `/v1/voice-onboarding/*`, `/v1/publish` — all built, uncalled); nav — `/Users/home/Marque/ios/Marque/App/MarqueTabBar.swift`, `RootTabView.swift`, `Navigation/AppRouter.swift`; trust/config — `/Users/home/Marque/ios/Marque/Adapters/Config.swift`, `SupabaseStore.swift`, `/Users/home/Marque/backend/main.py`.