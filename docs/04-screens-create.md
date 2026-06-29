# 04 · Core Screens I — Today, Studio, Script Reader & Hook Lab

> **Status:** Implementation-grade spec. Section owner: Core Create surface.
> **Reads with:** `02-design-system.md` (tokens, type ramp, motion), `01-information-architecture.md` (nav + routing), `05-screens-produce.md` (Studio → Record handoff, teleprompter, repurpose-in), `05-screens-produce.md` (schedule, "next scheduled post", Publisher), `07-ai-system.md` (Claude prompts, BrandGraph, Structured Outputs, ClipEngine), `12-backend-data-security.md` (Postgres schema, RLS), `09-video-pipeline.md` (Trigger.dev orchestration, Supabase Realtime/Broadcast), `15-infra-observability-testing.md` (PostHog events).

This document specifies the four screens that carry Marque's **create** loop: **Today** (the calm home), **Studio** (where the batch is conceived), the **Script Reader** (where a single script is read, steered, and committed), and the **Hook Lab** (the ranked-hook surface nested inside the Reader). These four screens sit *upstream* of Record and Distribute; they are where the creator decides **what** to say before they ever press record.

Everything below obeys the **anti-clutter doctrine**: one idea per screen, features one layer deep, slow eased motion, never a feed. Where a feature could bolt onto Today, it does not — it is surfaced contextually or lives in its own calm screen.

---

## 0 · Shared foundations (applies to all four screens)

### 0.1 State architecture — Observation framework, no exceptions

Every screen's view-model is an `@Observable @MainActor final class`. We do **not** use `ObservableObject` / `@Published` / `@StateObject` in any new code. The Observation framework gives **property-level change tracking**: a view re-renders only when a stored property its `body` actually reads changes. This is not a nicety — it is what makes the anti-clutter doctrine *mechanically* true. The Today screen body reads `todayVM.directive`; mutating an unrelated `streakCount` must not redraw the directive's breathing animation ([Apple — Migrating to the Observable macro](https://developer.apple.com/documentation/swiftui/migrating-from-the-observable-object-protocol-to-the-observable-macro)).

Canonical API mapping (hand these to engineers verbatim):

| Concern | API | Example |
|---|---|---|
| View-model declaration | `@Observable final class` | `@Observable @MainActor final class StudioModel { … }` |
| Owning a VM in a view | `@State` (preserves identity across redraws) | `@State private var model = TodayModel()` |
| Two-way binding into a child | `@Bindable` | `@Bindable var model: ScriptReaderModel` (steer toggles, editable body text) |
| Shared/injected context | `@Environment(_:)` + `.environment(_:)` | `@Environment(BrandGraph.self) private var brandGraph` |
| Non-observed fields (caches, services, raw transcript) | `@ObservationIgnored` | `@ObservationIgnored private var clipEngine: ClipEngine` |

**Hard rules:**

1. Mark transcript arrays, ClipEngine handles, network clients, and large histories `@ObservationIgnored` so mutating them never triggers a redraw ([tanaschita — Migrating to Observation](https://tanaschita.com/swiftui-observation-migrating-to-observation/)).
2. Keep `body` property reads **minimal**. Reading a computed property in `body` that internally touches 10 stored props registers all 10 for observation, silently negating the perf win — this "bites a lot of people" ([Swift Crafted — @Observable Guide 2026](https://swiftcrafted.dev/article/swiftui-observable-macro-complete-guide-observation-framework)).
3. View-models are `@MainActor`. LLM calls, ClipEngine jobs, and transcription run off-main in `Task {}`; results hop back via main-actor methods. Shared mutable state lives behind an `actor`. No `DispatchQueue.main.async` ([Swift Crafted — @Observable Guide 2026](https://swiftcrafted.dev/article/swiftui-observable-macro-complete-guide-observation-framework)).
4. iOS 17 is the floor; `@Observable` ships natively, no Perception back-port needed.

### 0.2 Claude integration contract (scripts + hooks)

All script and hook generation goes through the **Claude adapter** (`07-ai-system.md`) using **Structured Outputs** — never "please return JSON" prompting, which is now an anti-pattern. Both locked models are supported: **Opus 4.8** (script/hook reasoning, teardowns) and **Haiku 4.5** (bulk voice-checks, classification) ([Claude Platform Docs — Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).

- Script objects use `output_config.format` (JSON Schema → JSON output). The schema is **compiled into a grammar that restricts token generation**, so the model literally cannot emit a schema-violating token — this eliminates the entire "malformed JSON broke the Reader" bug class ([Towards Data Science — Anthropic Structured Output](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/)).
- Steer controls and format-swap are **`strict: true` tool calls** so params always match schema.
- **Warm the schema cache on deploy.** First compile is ~100–300 ms; the compiled schema is then cached for 24h. A deploy-time dummy request per schema means no real user eats the penalty. Schemas are **stable + versioned**; we do not churn them ([Towards Data Science — Anthropic Structured Output](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/)).
- **Prompt-cache the BrandGraph prefix** (voice profile, content pillars, past winners) across a Studio batch run so N script calls reuse one cached prefix.
- Always inspect `stop_reason`. A steer like "shorter" that hits `max_tokens`, or a refusal, surfaces as the screen's **error state** (§ per-screen states), not a silent empty box.

### 0.3 Realtime contract ("next scheduled post", live job status)

Live state (the Today "next scheduled post" row; render/publish status) comes from **Supabase Realtime**. Non-negotiable client rules ([Supabase Swift — subscribe reference](https://supabase.com/docs/reference/swift/subscribe)):

1. **Register all callbacks before `subscribe()`.** Adding postgres-change/presence handlers after subscribing is rejected.
2. **Tear channels down on screen exit** (`channel.unsubscribe()` in `.onDisappear`/`task` cancellation). Auto-cleanup is only 30s post-disconnect; leaked channels degrade the service.
3. **Do not subscribe the client directly to high-churn `jobs`/`clips` tables** via Postgres Changes. The Trigger.dev orchestrator writes status to a row server-side and re-streams via **Broadcast** (`realtime.broadcast_changes()`), which is private-by-default. Today subscribes to one cheap broadcast topic for its single next-post row ([Supabase — Broadcast](https://supabase.com/docs/guides/realtime/broadcast), [Supabase — Postgres Changes scale guidance](https://supabase.com/docs/guides/realtime/postgres-changes)). See `09-video-pipeline.md`.

### 0.4 Aesthetic + motion primitives (from `02-design-system.md`)

| Token | Light | Dark |
|---|---|---|
| `surface.canvas` | `#F4F1EA` (warm cream) | `#0E0E10` (near-black) |
| `text.primary` | `#1A1916` | `#ECE8DE` |
| `accent.gold` | `#C9A227` | `#C9A227` |
| `hairline` | `#E2DCCF` | `#26262A` |

- **Display type:** Playfair / Tiempos (titles, the directive, hook text). **Body/UI:** Inter / Söhne / Matter.
- **Motion:** all entrances/transitions use a slow eased "breathing" curve (`spring(response: 0.9, dampingFraction: 0.86)` or `easeInOut(duration: 0.6–0.9)`). **Loading is a breathing shimmer, never a spinner.**
- **Gold is rare:** the streak glyph, one focused CTA, the predicted-strength badge. Never two gold elements competing on one screen.
- Subtle paper texture on `surface.canvas`; soft, low-opacity shadows; generous whitespace.

---

## 1 · Today

> *The calm home. Exactly one directive at a time. Everything else is a quiet glyph or one tap away. Never a feed.*

### 1.1 Purpose & philosophy

Today answers one question: **"What is the one thing I should do right now?"** It is modeled on Stoic's home — date + a single titled idea, "deceptively simple," stark, calm-focused ([ScreensDesign — Stoic teardown](https://screensdesign.com/showcase/journal-mental-health-stoic)) — and on Calm's center-action / perimeter-nav pattern, where one peaceful element holds the center and everything secondary lives at the perimeter, not bolted onto the middle ([IXD@Pratt — Calm critique](https://ixd.prattsi.org/2018/01/design-critique-calm-ios-app/)). Minimal card layouts with ample whitespace and a neutral palette "promote calm and order"; notification-badge noise is turned **off** ([Appinventiv — Minimal App Design Guide](https://appinventiv.com/blog/minimal-app-design-guide/)).

### 1.2 Layout (top → bottom, single column, huge whitespace)

```
┌─────────────────────────────────────────┐
│  Tuesday, June 29              ◇ 12      │  ← date (body) · streak glyph (gold, peripheral)
│                                          │
│                                          │
│        What do you want to be            │  ← (only in directive variants that frame a prompt)
│                                          │
│   ╭───────────────────────────────────╮  │
│   │   THE ONE DIRECTIVE               │  │  ← Playfair, large, center stage
│   │   "Film this week's batch.        │  │
│   │    Your scripts are ready."       │  │
│   │                                   │  │
│   │           [  Begin  ]             │  │  ← ONE gold-accented primary action
│   ╰───────────────────────────────────╯  │
│                                          │
│                                          │
│   ── Rising: "soft launch" POVs  ↗      │  ← ONE trend line (Trend Radar)
│                                          │
│   Next: Reel · Wed 6:10 PM · Instagram  │  ← ONE quiet next-scheduled-post row
└─────────────────────────────────────────┘
```

Five elements, maximum. The directive owns the vertical center. The streak glyph and trend line are peripheral and quiet. The next-post row is a single hairline-separated line. There is **no list, no grid, no feed**.

### 1.3 Components

#### `DirectiveCard`
The single most important element on the screen.

| Property | Spec |
|---|---|
| Type | Playfair / Tiempos display, `text.primary`, 28–34pt, generous line height |
| Body line | Optional one-line context in Söhne/Inter, `text.primary` @ 70% |
| Action | Exactly one `PrimaryButton` (gold fill or gold outline), label is a verb: "Begin", "Review", "Record", "Approve" |
| Motion | Enters with breathing fade+rise (`opacity 0→1`, `offset y: 12→0`, 0.8s eased). On directive change, old card breathes out before new breathes in (no cross-dissolve jank) |
| Sizing | Card max-width ~88% of canvas; vertical centering with flexible spacers above/below |

**Directive selection** is computed server-side (a `today_directive` resolver in FastAPI; see `07-ai-system.md`) and reduces the creator's entire state into one next-best-action. Priority ladder (first match wins):

| Priority | Condition | Directive copy | Action → |
|---|---|---|---|
| 1 | No brand connected | "Let's find your voice." | Onboarding / Brand connect |
| 2 | Brand connected, no scripts generated | "Your first scripts are waiting to be written." | Studio |
| 3 | Scripts generated, not yet recorded | "Film this week's batch. Your scripts are ready." | Record (Studio→Record) |
| 4 | Footage recorded, clips rendering | "We're cutting your clips. Almost there." | Insights/status (read-only) |
| 5 | Clips ready, unscheduled | "5 clips ready. Choose when they go live." | Distribute |
| 6 | All scheduled, nothing due | "You're ahead. Rest, or pull next week forward." | Studio (optional) |
| 7 | A teardown landed | "One of your posts taught us something." | Coach card (one layer deep) |

Only **one** directive renders. The resolver never stacks them.

#### `StreakGlyph`
- A small gold mark (custom glyph, ◇/flame-as-serif-ornament per `02-design-system.md`) + count, top-right, `accent.gold`.
- **Peripheral, not interactive-heavy:** tapping it pushes the **full streak view in Profile** (`see 05-screens-produce.md`), never expands inline on Today.
- Animates a single soft pulse only when the streak *increments* (e.g., a publish completed). Otherwise static.

#### `TrendLine`
- One line: `── Rising: "<trend phrase>" ↗` in Söhne/Inter, `text.primary` @ 60%, a subtle hairline rule on the left.
- Tapping opens the dedicated **Trends screen** (Trend Radar; `see 05-screens-produce.md` / `05-screens-produce.md`). Today shows exactly **one** trend, the single highest-relevance rising format/topic for this creator's pillars.
- Source: Insights adapter + Trend Radar service; cached, refreshed on foreground.

#### `NextPostRow`
- One quiet row: `Next: <format> · <day time> · <platform>` with a tiny platform glyph.
- Backed by Supabase Realtime **Broadcast** (§0.3): when the orchestrator flips a scheduled post to `posted`/`failed`, the row updates live without a poll.
- Tapping opens that post in **Distribute**. If nothing is scheduled, the row is absent (not an empty placeholder).

### 1.4 Interactions

| Interaction | Behavior |
|---|---|
| Tap `Begin` on directive | Eased push to the directive's target screen; haptic `.soft` |
| Tap streak glyph | Push full streak view in Profile |
| Tap trend line | Push Trends screen |
| Tap next-post row | Push that post in Distribute |
| Pull-to-refresh | Re-resolves directive + refetches trend + next-post; breathing shimmer, not a spinner |
| App foreground | Silent revalidation of directive, trend, next-post; if directive changed, breathe the swap |
| Deep link / push tap | Routes straight to the directive target where applicable |

### 1.5 States

| State | Today behavior |
|---|---|
| **Loading** | Single centered breathing shimmer block where the directive will be; date renders immediately; no streak/trend/next-post until resolved. No spinner. |
| **Empty (no brand)** | Priority-1 directive: "Let's find your voice." → Brand connect. No streak, no trend, no next-post. One declarative idea only. |
| **Empty (brand, no scripts)** | Priority-2 directive → Studio. Trend line may show if available; streak shows 0-state glyph (dim, no count). |
| **Error (resolver/network)** | If the directive resolver fails, fall back to the **last cached directive** rendered read-only with a hairline note: "Showing your last plan — we'll refresh when you're back." No red, no modal. |
| **Offline** | Cached directive + cached trend shown read-only. `Begin` still routes if the target works offline (e.g., reading cached scripts); otherwise the button is disabled with subtext "Reconnect to continue." Next-post row shows last-known status with a faint "·" pulse paused. |
| **Permission-denied** | Not applicable on Today itself; permission prompts (camera/mic, social link) are deferred to the target screen. If "next scheduled post" exists but the social account got unlinked, the row shows: "Reconnect Instagram to keep this scheduled." → Settings. |

### 1.6 Acceptance criteria — Today

- [ ] Renders **at most one** directive; the resolver never displays two.
- [ ] Mutating `streakCount` does **not** redraw the `DirectiveCard` (verified via Observation property-tracking; no `body` over-reads).
- [ ] Streak glyph navigates to Profile; never expands inline.
- [ ] Trend line shows exactly one trend and routes to Trends.
- [ ] Next-post row updates live via Broadcast within 2s of an orchestrator status flip, with the channel registered-before-subscribe and torn down on disappear.
- [ ] Offline shows cached directive read-only; no spinner ever appears (breathing shimmer only).
- [ ] No notification badges anywhere on Today.

---

## 2 · Studio

> *Where the week's batch is conceived. Content pillars as circular nodes; generate the whole batch in their voice; tune the content mix; hand off to Record. The "film once → post all week" hero loop starts here.*

### 2.1 Purpose

Studio is the planning room for the **hero batch loop**. The creator's **content pillars** appear as calm circular nodes (the Brand Graph made visible). From here they generate a *batch* of scripts across pillars in one pass, adjust the **content mix**, open any script in the Reader, and when satisfied, launch the single batch **Record** session. Studio is one idea — "plan this week's batch" — not a dashboard.

### 2.2 Layout

```
┌─────────────────────────────────────────┐
│  Studio                                  │
│  This week's batch                       │  ← Playfair section title
│                                          │
│              ◯ Mindset                   │
│        ◯ Build         ◯ Story           │  ← pillar nodes (circular), gentle constellation
│              ◯ Teardown                  │     node size = mix weight
│                                          │
│   ───────────────────────────────────    │
│   Mix   ▦▦▦▦  ▦▦▦  ▦▦  ▦                 │  ← content-mix control (proportional)
│         Build Story Mind Tear            │
│   ───────────────────────────────────    │
│                                          │
│   7 scripts · ~14 clips planned          │  ← quiet summary
│                                          │
│        [  Generate this week  ]          │  ← primary (gold) when idle
│        [  Review 7 scripts  ›  ]         │  ← primary when generated
└─────────────────────────────────────────┘
```

### 2.3 Components

#### `PillarConstellation` (circular nodes)
- Each content pillar = one `PillarNode`: a soft circle, label below in Söhne/Inter, **diameter proportional to its mix weight**.
- Layout is a gentle, organic constellation (force-directed-feeling but deterministic per pillar set; persisted so it doesn't reshuffle each visit). Slow idle drift (≤2px, 6s loop) for "breathing," respects Reduce Motion.
- Tapping a node opens a one-layer-deep **Pillar sheet**: pillar description, recent winners under it, and "Generate more from this pillar."
- Long-press → reweight affordance (drives the mix control below).
- Data: `content_pillars` from the BrandGraph (`12-backend-data-security.md`). The constellation is a *view* of the Brand Graph, never an editor of identity — editing voice/pillars lives in the Brand Graph screen (`see 07-ai-system.md` / brand screens).

#### `ContentMixControl`
- A proportional bar (segmented) showing the share of the batch each pillar gets. Dragging a segment boundary reallocates; node diameters update in tandem.
- Constrained: total = 100%; minimum 1 script per active pillar; a pillar can be muted (0%) by collapsing its segment.
- Changing the mix updates the "N scripts · ~M clips planned" summary live and marks the batch **stale** if scripts were already generated (offers "Regenerate to match new mix").

#### `BatchPlanSummary`
- One quiet line: `N scripts · ~M clips planned` (M derived from chosen formats; see Format Library, `07-ai-system.md`). Updates reactively from the mix + format defaults.

#### `GenerateBatchButton` / `ReviewBatchButton`
- Idle → **"Generate this week"** (gold). Generating → progress as a breathing row (per-script ticks). Done → **"Review N scripts ›"** routes into the script list (which opens individual Readers).
- After review/approval → **"Film once ›"** hands off to Record (`see 05-screens-produce.md`).

### 2.4 Batch script generation (the Studio → Claude path)

When the creator taps **Generate this week**, `StudioModel` issues **one batch run** to the Claude adapter:

1. Assemble the **BrandGraph prefix** (voice profile, pillars, past winners, banned phrases) **once** and mark it a **cached prompt prefix** — every per-script call in the run reuses it for a large cost/latency win ([Claude Platform Docs — Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).
2. For each planned script (mix-weighted across pillars), call **Opus 4.8** with `output_config.format` against the **stable, versioned `script_v1` schema** (below).
3. Run **Haiku 4.5** voice-check / classification passes in bulk (does this read in the creator's voice? which signal types did the hooks land?) — the locked model split: Opus reasons, Haiku classifies at volume.
4. Stream results into the script list as each completes (breathing tick per script), so the creator can open the first Reader before the last finishes.
5. The run is durable: long generations are dispatched through **Trigger.dev** so a backgrounded app doesn't drop the batch (`see 09-video-pipeline.md`).

**`script_v1` JSON Schema (conceptual — full schema in `07-ai-system.md`):**

```jsonc
{
  "type": "object",
  "required": ["script_id", "pillar_id", "hook", "body", "cta",
               "format_id", "shot_plan_notes", "variants", "hooks"],
  "properties": {
    "script_id":   { "type": "string" },
    "pillar_id":   { "type": "string" },
    "format_id":   { "type": "string" },          // FK into Format Library render-recipes
    "hook":        { "type": "string" },          // the selected hook line
    "body":        { "type": "string" },          // spoken body, teleprompter-ready
    "cta":         { "type": "string" },
    "shot_plan_notes": {
      "type": "array",
      "items": { "type": "object", "required": ["beat", "note"],
                 "properties": {
                   "beat":  { "type": "string" },  // e.g. "Hook", "Proof", "Turn", "CTA"
                   "note":  { "type": "string" },  // e.g. "cut to B-roll of the build"
                   "t_hint":{ "type": "number" }   // optional seconds hint
                 } }
    },
    "variants": {                                   // 3 full-script variants
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": { "type": "object", "required": ["variant_id","hook","body","cta","steer_lineage"],
                 "properties": {
                   "variant_id":    { "type": "string" },
                   "hook":          { "type": "string" },
                   "body":          { "type": "string" },
                   "cta":           { "type": "string" },
                   "steer_lineage": { "type": "array", "items": { "type": "string" } }
                 } }
    },
    "hooks": {                                       // Hook Lab payload (see §4)
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": { "type": "object",
                 "required": ["text","signal_type","rank","rationale","strength"],
                 "properties": {
                   "text":        { "type": "string" },
                   "signal_type": { "type": "string", "enum": [
                     "curiosity_gap","contrarian","myth_bust","social_proof",
                     "fomo_urgency","quick_win","bold_claim","relatable_pov" ] },
                   "rank":        { "type": "integer" },          // 1 = top
                   "rationale":   { "type": "string" },
                   "strength":    { "type": "number" }            // virality_predictor score
                 } }
    }
  }
}
```

> Schema names/enums carry **no sensitive data** (a Structured-Outputs best practice) — only message content does ([Claude Platform Docs — Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).

### 2.5 States

| State | Studio behavior |
|---|---|
| **Loading** | Constellation nodes fade in as breathing circles (skeleton rings → filled); mix control disabled until pillars load. No spinner. |
| **Empty (no pillars / brand not analyzed)** | Single declarative CTA: "Connect your page so we can find your pillars." → Brand connect. No constellation. |
| **Empty (pillars, no scripts)** | Constellation + mix shown; primary is **"Generate this week"**; summary reads "Ready to write 7 scripts." |
| **Generating** | Per-script breathing ticks; button shows progress; partial results openable as they stream. Cancelable → returns to idle, keeps any completed scripts. |
| **Error (Claude)** | If `stop_reason` is a refusal/truncation, the affected script row shows: "We couldn't land this one — retry?" with a single retry. Other scripts unaffected. Batch-level network failure → "We saved your plan. Tap to retry the batch." |
| **Error (ClipEngine planning)** | If clip-count estimation (Format Library / `virality_predictor`) is unavailable, summary degrades to "7 scripts planned" (drops the ~M clips estimate) rather than blocking. |
| **Offline** | Constellation + mix render from cache, read-only. "Generate this week" is **queued**: tapping shows "Queued — we'll write these when you're back online," and the run fires on reconnect (Trigger.dev). |
| **Permission-denied** | N/A in Studio itself; the camera/mic prompt is owned by the **Record** handoff (`see 05-screens-produce.md`). |

### 2.6 Acceptance criteria — Studio

- [ ] Pillar node diameter is proportional to mix weight and updates live as the mix changes.
- [ ] A batch run sends **one** cached BrandGraph prefix reused across all per-script calls (verified: prefix cache hit on calls 2..N).
- [ ] Scripts stream into the list individually; the first Reader is openable before the batch completes.
- [ ] All script generation uses `output_config.format` against `script_v1`; no free-form JSON prompting exists in the codebase.
- [ ] Changing the mix after generation marks the batch stale and offers regenerate; it never silently mismatches.
- [ ] Offline "Generate" queues durably and fires on reconnect.
- [ ] `StudioModel` is `@Observable @MainActor`; constellation drift uses Reduce-Motion-aware animation.

---

## 3 · Script Reader / Editor

> *One script, read like a page. Hook · Body · CTA. Three variants. Steer it ("more contrarian", "shorter", "funnier"). Swap its format. See its shot-plan. Open the Hook Lab if the hook needs work.*

### 3.1 Purpose

The Reader is where a single script becomes *the* script. It is a calm reading surface first, an editor second — closer to reading a journal entry than filling a form. The creator reads it aloud in their head, steers it until it sounds like them, picks the format, glances at the shot plan, and approves it into the batch.

### 3.2 Layout

```
┌─────────────────────────────────────────┐
│  ‹ Batch            Mindset · Script 2/7 │  ← context, not chrome
│                                          │
│  ⌁ Format: Myth-buster        [ Swap ]   │  ← format chip + swap
│                                          │
│  HOOK                          Lab ›     │  ← section label · Hook Lab entry
│  "Stop optimizing your morning.          │  ← Playfair, the hook reads big
│   You're solving the wrong problem."     │
│                                          │
│  BODY                                    │
│  Here's what nobody tells you about…     │  ← Söhne/Inter, editable, generous leading
│  …                                       │
│                                          │
│  CTA                                     │
│  Follow for the version they don't sell  │
│                                          │
│  SHOT PLAN                               │  ← collapsed by default (progressive)
│  › Hook  · cold open, no intro            │
│  › Proof · cut to B-roll of the build     │
│  › Turn  · green-screen the claim         │
│                                          │
│  ─────────────────────────────────────    │
│  ‹ ●○○ ›  variant 1 of 3                  │  ← 3 variants
│  Steer:  Contrarian · Shorter · Funnier  │  ← steer controls
│  Steer:  Warmer · Punchier · Simpler     │
│                                          │
│        [  Approve into batch  ]          │
└─────────────────────────────────────────┘
```

### 3.3 Components

#### `ScriptCanvas` (Hook / Body / CTA)
- Three labeled sections. **HOOK** renders in Playfair display (it is the most important line); **BODY** and **CTA** in Söhne/Inter with generous leading, like prose.
- Body + CTA are **editable in place** (tap to edit; the field is hairline-underlined, never boxed — see the editorial bottom-sheet pattern in sibling design notes). Edits bind via `@Bindable var model: ScriptReaderModel`.
- Manual edits set `dirty = true` and lift the variant out of pure-generated state (tracked in `steer_lineage` as `"manual_edit"`).

#### `FormatChip` + `Swap`
- A chip showing the current **format** from the **Format Library** (formats are structured **render-recipes**, not blank talking heads: split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook). The chip uses a small format glyph + name.
- **Swap** opens a one-layer-deep format picker (sheet) of compatible recipes. Format swap is a **`strict: true` tool call** to Claude so the script's `shot_plan_notes` and on-screen beats are **re-derived to fit the new recipe** (a myth-buster's beats differ from a listicle's). The hook text can stay; the shot plan and beat structure adapt.
- Swapping recomputes the estimated clip count and may change which `virality_predictor` strength applies.

#### `VariantPager` (3 variants)
- Exactly **3 full-script variants** per script (`variants[]` in `script_v1`). A dot indicator `●○○` + left/right; swipe between them.
- Each variant is a complete hook+body+CTA. Switching variants is instant (already generated). The active variant is what gets approved.
- A variant carries `steer_lineage` so the creator can see how it was shaped ("contrarian → shorter").

#### `SteerControls`
- A compact set of **steer chips**: `Contrarian` · `Shorter` · `Funnier` · `Warmer` · `Punchier` · `Simpler` (the canonical set; "more contrarian" / "shorter" / "funnier" are the headline three from the product spec).
- Tapping a steer chip issues a **`strict: true` tool call** to **Opus 4.8** that rewrites the *current variant* under that steer, appending to `steer_lineage`. The rewrite streams in with a breathing shimmer over the affected sections.
- Steers are **composable and reversible**: an Undo restores the prior variant snapshot (kept in `@ObservationIgnored` history so it doesn't trigger redraws). Multiple steers stack ("contrarian" then "shorter").
- `stop_reason` is checked: a "Shorter" that truncates surfaces as a gentle inline note, not a broken script (§3.5 error).

#### `ShotPlan`
- Collapsed by default (progressive disclosure). Expands to the ordered **beats** from `shot_plan_notes[]` — each a `beat · note` line (e.g., "Proof · cut to B-roll of the build"), optionally with a `t_hint`.
- Shot-plan notes are seeded by Claude and **enriched** for repurpose-in (existing long video) sources. The enrichment **does not** use AssemblyAI's deprecated `auto_chapters` (it silently 500s on Universal-3 Pro — see `08-format-virality.md` §3, `09-video-pipeline.md` §6.4/§7, `17-roadmap-milestones.md` M3). Instead, AssemblyAI is used **purely for transcription + word-level timestamps + Key Phrases + sentiment**, and the **beat structure comes from a Claude moment-detection pass** over those signals — exactly the approach the pipeline mandates:
  - **AssemblyAI** (`Transcriber` adapter) returns, in one transcript call: **word-level timestamps** (`words[]`, ms), **Key Phrases** (`auto_highlights: true` → `auto_highlights_result.results[]` with `text`, `rank`, `timestamps[]`), and **Sentence-level Sentiment** (`sentiment_analysis`) — candidate caption hooks and emotional cues, but *not* chapter/beat boundaries ([AssemblyAI — Speech Understanding API](https://www.assemblyai.com/products/speech-understanding), [AssemblyAI — Key Phrases](https://www.assemblyai.com/docs/speech-understanding/key-phrases)).
  - A **Claude moment-detection pass** (Haiku 4.5 for bulk moment ranking; Opus 4.8 only when the script reasoning is harder) reads the transcript paragraphs + word-timestamps + key-phrase ranks + sentiment and **emits the timestamped beats** as `shot_plan_notes[]` (each `beat`, `note`, `t_hint`), snapping `t_hint` to word `end` timestamps. This keeps the LLM vendor consistent with the locked Claude/Anthropic stack and avoids the deprecated flag entirely. We call Claude directly (not LeMUR) to keep the Opus/Haiku split under Marque's control.
  - Visual moments (on-screen action, not speech) come from the ClipEngine `video_analysis_*` tools (`07-ai-system.md`).
- The shot plan is the bridge to Record's teleprompter + beat overlays (`see 05-screens-produce.md`).

#### `ApproveButton`
- Single gold primary: **"Approve into batch."** Commits the active variant + format + shot plan to the batch (`scripts` table, `status = approved`). Advances to the next un-approved script in the batch with a breathing transition.

### 3.4 Interactions

| Interaction | Behavior |
|---|---|
| Tap body/CTA | Inline edit; underline field; binds via `@Bindable`; sets dirty |
| Swipe / dot tap | Switch among 3 variants (instant) |
| Tap a steer chip | Streamed rewrite of current variant; lineage appended; Undo available |
| Tap **Lab ›** | Opens Hook Lab over the hook (§4) |
| Tap **Swap** | Format picker sheet; `strict` tool call re-derives shot plan |
| Tap a shot-plan beat | Expands note; (in Record) jumps the teleprompter to that beat |
| Tap **Approve** | Commit active variant; advance to next script |
| Swipe down / ‹ Batch | Return to Studio's script list; unsaved steers are kept as draft (recoverable) |

### 3.5 States

| State | Reader behavior |
|---|---|
| **Loading** | Section labels (HOOK/BODY/CTA) render immediately; text areas show breathing shimmer lines until the script resolves. No spinner. |
| **Empty** | Reached only via deep link to a not-yet-generated script → "This one isn't written yet." with **"Generate it"** (single Opus call). |
| **Error (steer/regenerate refusal or truncation)** | The affected section reverts to its prior snapshot and shows an inline hairline note: "That steer didn't land — try again or pick a different one." Driven by `stop_reason`. The rest of the script is untouched. |
| **Error (network during steer)** | Steer is **queued** offline (below) or, if transient, shows "Couldn't reach the studio — retry?" inline. The pre-steer variant remains intact. |
| **Offline** | Full script + 3 variants + shot plan are **cached and readable**. Manual edits are allowed and saved locally. Steer / Swap / Generate actions are **queued** with a faint "Queued" tag and fire on reconnect. Approve works offline (commits locally, syncs later). |
| **Permission-denied** | N/A here; surfaces at Record. If shot-plan enrichment for a repurpose-in source needs the original video and it's missing, the shot plan shows the Claude-seeded beats only with a note: "Add the source video for richer beats." |

### 3.6 Acceptance criteria — Reader

- [ ] Exactly 3 variants; the active variant is what Approve commits.
- [ ] Body/CTA edit in place via `@Bindable`; a manual edit appends `"manual_edit"` to `steer_lineage`.
- [ ] Each steer is a `strict:true` tool call to Opus 4.8, streamed, reversible via Undo (snapshots in `@ObservationIgnored` history).
- [ ] Format Swap re-derives `shot_plan_notes` to fit the new render-recipe (verified: myth-buster→listicle changes the beats).
- [ ] `stop_reason` truncation/refusal reverts the affected section and shows an inline note — never a blank script.
- [ ] Offline: script is readable; steer/swap/generate queue and fire on reconnect; manual edits persist locally.
- [ ] Shot plan is collapsed by default and expands to ordered beats.

---

## 4 · Hook Lab (nested in the Reader)

> *Open the hook, see three ranked rewrites across the eight signal types, each with a reason and a strength score. Authentic/curiosity hooks rank above gimmicky ones — by design.*

### 4.1 Purpose & placement

The Hook Lab is **not** a separate destination. It is progressive disclosure nested in the Script Reader, opened from the **Lab ›** affordance on the HOOK section. It exists because the hook is the highest-leverage 3 seconds of any clip — Reels/IG must land in ≤3s, TikTok in ≤2s — so the top-ranked hook is the **fastest-to-land** one ([OpusClip — Reels Hook Formulas / 3-second holds](https://www.opus.pro/blog/instagram-reels-hook-formulas)).

### 4.2 The 8 signal types (canonical enum)

These are the `signal_type` enum values in `script_v1.hooks[]` and the chips shown in the Lab. They are derived from current short-form hook taxonomy ([Drive — 2025 Hook Trends](https://driveeditor.com/blog/trends-short-form-video-hooks), [SendShort — 80+ Viral Hooks 2025](https://sendshort.ai/guides/tiktok-instagram-hooks/)):

| # | `signal_type` | Drives | Example opener |
|---|---|---|---|
| 1 | `curiosity_gap` | Withhold the payoff | "The one thing nobody tells you about…" |
| 2 | `contrarian` | Challenge a belief (target of the "more contrarian" steer) | "Everything you know about X is wrong." |
| 3 | `myth_bust` | Negate a common practice (pairs with the myth-buster **format**) | "Stop doing X." |
| 4 | `social_proof` | Numbers / authority | "I've done this 500 times — here's the pattern." |
| 5 | `fomo_urgency` | Time-sensitive / exclusive | "This window closes Friday." |
| 6 | `quick_win` | Immediate value | "Do this in 10 seconds." |
| 7 | `bold_claim` | Pattern-interrupt / shock | "You won't believe what this changed." |
| 8 | `relatable_pov` | First-person, in-medias-res story | "I almost quit last week. Then…" |

### 4.3 Ranking philosophy (a differentiator, not just compliance)

The 2026 algorithm shift rewards **authentic, native, story-led** openers over aggressive sales hooks, text-heavy listicles, and over-produced transitions ([The Viral App — 2026 Trends](https://theviralapp.com/blog/short-form-video-trends-2026-tiktok-reels-shorts/)). Marque's calm-premium positioning is *on-trend*, so the Hook Lab **ranks authentic / curiosity / story hooks above gimmicky ones and says so in the rationale.** This aligns ranking with both the platform algorithm and the brand. The rank is not purely the raw strength score — it is strength **conditioned on authenticity fit**.

### 4.4 Components

#### `HookLabSheet`
- Opens as a calm sheet over the Reader (one layer deep), not a full push. The current hook stays visible at top for comparison.
- Lists the **3 ranked hook variants** (`hooks[]`, `minItems: 3`), ordered by `rank`.

#### `RankedHookCard` (×3)
Each card exposes exactly four things:

| Element | Source field | Render |
|---|---|---|
| Hook text | `text` | Playfair, reads big |
| Signal-type chip | `signal_type` | Small labeled chip (e.g., "Curiosity") |
| One-line rationale | `rationale` | Söhne/Inter @ 70%, why it lands / why it's ranked here |
| Strength score | `strength` | A quiet gold strength mark (e.g., a 1–100 or 5-dot scale), from **`virality_predictor`** |

- The strength score per hook comes from the ClipEngine **`virality_predictor`** tool (hook strength, retention risk, attention, engagement), called **per hook variant** behind the adapter (`07-ai-system.md`).
- Tapping a card's **"Use this hook"** sets it as the script's hook (updates `script_v1.hook`), closes the sheet, and reflects in the Reader's HOOK section with a breathing swap. The shot plan's "Hook" beat updates if the signal type implies a different cold-open.
- A subtle **rank-1 marker** ("Fastest to land") on the top card ties the ranking back to the 3-second rule.

### 4.5 Generation contract

Hooks are produced as part of `script_v1` (so they arrive with the batch) **or** regenerated on demand from the Lab via an Opus 4.8 call with `output_config.format` against the `hooks[]` sub-schema. Each regeneration:

1. Reuses the cached BrandGraph prefix (§0.2).
2. Returns exactly 3 hooks spanning **distinct** signal types where possible (no three near-duplicate curiosity gaps).
3. Scores each via `virality_predictor`, then re-ranks under the authenticity-conditioned rule (§4.3).
4. Checks `stop_reason`.

### 4.6 States

| State | Hook Lab behavior |
|---|---|
| **Loading** | Three breathing shimmer cards (the ranks are placeholders) until hooks + strength scores resolve. Strength marks fill in last. No spinner. |
| **Empty** | If a script somehow has no hooks → "Let's write three ways in." with **"Generate hooks"** (single Opus call). |
| **Error (Claude refusal/truncation)** | If regeneration fails (`stop_reason`), keep the existing 3 hooks and show an inline note: "Couldn't rewrite these — keeping your current set." Never an empty Lab. |
| **Error (`virality_predictor` unavailable)** | Cards still render with text + signal + rationale; the **strength mark is hidden** (graceful degrade) with a tiny "scoring unavailable" note — ranking falls back to Claude's editorial rank. |
| **Offline** | The 3 hooks that came with the script are **readable and selectable** (Use-this-hook works locally). Regeneration is **queued** and fires on reconnect. |
| **Permission-denied** | N/A. |

### 4.7 Acceptance criteria — Hook Lab

- [ ] Opens nested over the Reader (sheet), never as a separate tab/destination.
- [ ] Renders exactly 3 ranked hooks; each card shows text + `signal_type` chip + one-line rationale + strength mark.
- [ ] Strength comes from `virality_predictor` per hook; if unavailable, cards degrade gracefully (no strength mark, ranking falls back to editorial).
- [ ] Ranking conditions raw strength on authenticity fit; the rationale on at least the top card references why it lands fast (3-second rule).
- [ ] "Use this hook" updates `script_v1.hook` and breathes the change into the Reader's HOOK section.
- [ ] `signal_type` is constrained to the 8-value enum by the schema grammar (no off-enum values possible).
- [ ] Offline: existing hooks selectable; regeneration queues.

---

## 5 · Cross-screen data model (Reader/Studio surface)

Full schema + RLS in `12-backend-data-security.md`. The create surface reads/writes these tables:

| Table | Key columns | Used by |
|---|---|---|
| `content_pillars` | `id, brand_id, name, weight, description` | Studio constellation + mix |
| `batches` | `id, brand_id, week_of, mix_json, status` | Studio plan |
| `scripts` | `id, batch_id, pillar_id, format_id, hook, body, cta, shot_plan_json, active_variant_id, status` | Reader, Studio list |
| `script_variants` | `id, script_id, hook, body, cta, steer_lineage[]` | Variant pager, steer history |
| `script_hooks` | `id, script_id, text, signal_type, rank, rationale, strength` | Hook Lab |
| `scheduled_posts` | `id, clip_id, platform, scheduled_at, status` | Today next-post row (via Broadcast) |
| `directive_state` | `brand_id, directive_key, payload_json, computed_at` | Today directive resolver cache |

**Realtime topics:** Today subscribes to one **Broadcast** topic `brand:{id}:next_post` (orchestrator-fed). Studio subscribes to `batch:{id}:progress` during a generation run and **unsubscribes on completion / disappear** (§0.3).

---

## 6 · Instrumentation (PostHog — see `15-infra-observability-testing.md`)

| Event | Where | Properties |
|---|---|---|
| `today_directive_shown` | Today | `directive_key`, `priority` |
| `today_directive_begin` | Today | `directive_key`, `target` |
| `studio_batch_generated` | Studio | `n_scripts`, `mix_json`, `cache_hit_ratio` |
| `studio_mix_changed` | Studio | `from_mix`, `to_mix` |
| `script_steered` | Reader | `steer`, `variant_id`, `stop_reason` |
| `script_format_swapped` | Reader | `from_format`, `to_format` |
| `script_approved` | Reader | `format_id`, `n_steers`, `manual_edits` |
| `hooklab_opened` | Hook Lab | `script_id` |
| `hook_selected` | Hook Lab | `signal_type`, `rank`, `strength` |

---

## Open questions

1. **Pillar count cap.** Is there a max number of content pillars (and thus constellation nodes) before the layout stops being calm? Proposed default cap 6; needs product sign-off.
2. **Strength score scale.** Should `virality_predictor` strength surface to the creator as a number (1–100), a 5-dot scale, or a 3-tier word ("strong / solid / risky")? Affects `RankedHookCard` and the Reader strength badge.
3. **Steer chip set — final lockdown.** Headline three are "Contrarian / Shorter / Funnier." Proposed full set adds "Warmer / Punchier / Simpler." Confirm the final canonical list and whether creators can pin custom steers.
4. **Default content mix.** When a brand is first analyzed, what's the default pillar mix — even split, or weighted by which pillars had past winners? (BrandGraph can inform but the policy is a product call.)
5. **Directive "rest" state copy.** Priority-6 ("You're ahead") — does Marque ever show a truly empty/celebratory Today, or always offer a gentle next action? Tone decision.
6. **Offline Approve conflict policy.** If a script is approved offline and the same script was edited on another device, what's the merge/conflict resolution? (Likely last-write-wins with a toast; confirm.)
7. **Repurpose-in shot-plan depth.** How many of the Claude moment-detection beats (derived from AssemblyAI word-timestamps + Key Phrases + sentiment) do we surface as editable beats vs. keep internal? Affects shot-plan UI density.

## Sources

- [Apple — Migrating from the Observable Object protocol to the Observable macro](https://developer.apple.com/documentation/swiftui/migrating-from-the-observable-object-protocol-to-the-observable-macro) — canonical `@State` / `@Bindable` / `@Environment` mappings.
- [Swift Crafted — @Observable Macro Complete Guide (2026)](https://swiftcrafted.dev/article/swiftui-observable-macro-complete-guide-observation-framework) — the `body`-over-read perf pitfall; `@MainActor` rule.
- [tanaschita — Migrating to Observation](https://tanaschita.com/swiftui-observation-migrating-to-observation/) — `@ObservationIgnored` gotcha, concise migration.
- [Claude Platform Docs — Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) — JSON `output_config.format` + `strict` tools; Opus 4.8 / Haiku 4.5 supported; no sensitive data in schema names.
- [Towards Data Science — Hands-on with Anthropic's Structured Output](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/) — grammar compile (~100–300 ms), 24h cache, warm-on-deploy.
- [OpusClip — Instagram Reels Hook Formulas](https://www.opus.pro/blog/instagram-reels-hook-formulas) — 3-second-hold basis for Hook Lab ranking.
- [Drive — Trends in Short-Form Video Hooks (2025)](https://driveeditor.com/blog/trends-short-form-video-hooks) — hook-type taxonomy → the 8 signal types.
- [SendShort — 80+ Viral Hooks (2025)](https://sendshort.ai/guides/tiktok-instagram-hooks/) — hook signal examples.
- [The Viral App — Short-Form Video Trends 2026](https://theviralapp.com/blog/short-form-video-trends-2026-tiktok-reels-shorts/) — shift to authentic/story over produced/listicle (ranking philosophy).
- [ScreensDesign — Stoic journal teardown](https://screensdesign.com/showcase/journal-mental-health-stoic) — the single-directive Today reference.
- [IXD@Pratt — Calm app design critique](https://ixd.prattsi.org/2018/01/design-critique-calm-ios-app/) — center-action / perimeter-nav calm pattern.
- [Appinventiv — Minimal App Design Guide](https://appinventiv.com/blog/minimal-app-design-guide/) — minimal layouts + whitespace, turn off badge noise.
- [Supabase — Subscribing to Database Changes](https://supabase.com/docs/guides/realtime/subscribing-to-database-changes) — channel lifecycle.
- [Supabase Swift — subscribe reference](https://supabase.com/docs/reference/swift/subscribe) — register callbacks before `subscribe()`.
- [Supabase — Broadcast](https://supabase.com/docs/guides/realtime/broadcast) — `broadcast_changes()`, private-by-default, scale pattern for live status.
- [Supabase — Postgres Changes (scale guidance)](https://supabase.com/docs/guides/realtime/postgres-changes) — why not to subscribe clients to high-churn tables.
- [AssemblyAI — Speech Understanding API](https://www.assemblyai.com/products/speech-understanding) — transcription + word-level timestamps + Key Phrases + Sentiment in one call; beats are derived by a Claude moment-detection pass, **not** the deprecated `auto_chapters` (which 500s on Universal-3 Pro — see `08-format-virality.md` §3, `09-video-pipeline.md` §6.4/§7).
- [AssemblyAI — Key Phrases (`auto_highlights`)](https://www.assemblyai.com/docs/speech-understanding/key-phrases) — ranked, timestamped key phrases as candidate caption hooks / clip in-out points feeding the Claude moment pass.
