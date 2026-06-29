# 08 — Format Library, Hooks & Virality Engine

> **Scope.** This document specifies the three intelligence systems that make Marque more than a clip-cutter: the **Format Library** (viral formats expressed as structured *render-recipes*), the **Hook taxonomy + Hook Lab**, the **Virality Engine** (a script-level pre-write predictor *and* a clip-level post-render predictor with fixable feedback), the **Trend Radar**, and the **learning loop** that promotes each creator's best-performing formats. It is the implementation contract for the FastAPI orchestrator engineers, the Trigger.dev job authors, the iOS engineers who surface scores, and the designers who keep all of this from cluttering Today.
>
> **Sibling docs (authoritative on conflict):**
> - `00-overview.md` — product thesis, the core loop, the differentiators (Brand Graph, Virality Engine, Format Library).
> - `01-information-architecture.md` — adapter contracts (`ClipEngine`, `Publisher`, `Insights`), Trigger.dev job topology, FastAPI orchestrator surface, tab/navigation map.
> - `03-onboarding.md` — Brand Graph ingestion; `niche` and brand voice are set here and feed every system below.
> - `05-screens-produce.md` — Record, Library/Clip Editor, Calendar, Coach/Insights/Trends/Brand Profile. **This doc supplies the *engine* behind the scores and trend line those screens render; that doc owns the *pixels*.** On any UI-vs-engine boundary dispute, screen layout defers to `05`, scoring/data contracts defer to here.
> - `10-social-publishing.md` *(referenced as the publishing spec; in this repo the publishing/compliance material currently lives in the back half of `05-screens-produce.md`)* — Publisher/Insights adapter internals, IG Graph + TikTok Content Posting compliance, quota accounting, and the quartile-playback pullback that is **this engine's reward signal**.
> - `07-ai-system.md` *(referenced as the AI-system spec; Claude prompt/caching material is co-located with `01`/`05` until split out)* — Claude Opus 4.8 / Haiku 4.5 prompts, prompt caching, Brand Graph injection, the shared virality-scoring tool contract.
>
> **Locked aesthetic (see design system in `01`/`05`).** Warm cream `#F4F1EA` (light) / near-black `#0E0E10` (dark) — never pure white/black. Serif display (Playfair/Tiempos) for titles; grotesque (Inter/Söhne/Matter) for UI/body. Single warm gold accent `#C9A227`, used sparingly. Huge whitespace, one idea per screen, slow eased "breathing" motion, soft shadows, subtle paper texture. **Scores are never a red number and never a dashboard.** A weak hook is surfaced as a calm, declarative *suggestion*, not an alarm.
>
> **Anti-clutter doctrine (binding).** Today shows exactly **one directive + one gold streak glyph + one trend line** — nothing from this engine bolts onto Today except that single Trend Radar line. The Hook Lab is *nested inside the script reader* (progressive disclosure). Clip scores live on the Clip Editor card, one tap deep. "Your best formats" is surfaced *contextually* (a gentle nudge at schedule time), never as a Today dashboard.

---

## 0. Mental model — three systems, one feedback loop

```
                        ┌──────────────────────── BRAND GRAPH (03-onboarding) ────────────────────────┐
                        │  niche · voice · pillars · audience · banned topics                          │
                        └──────────────┬──────────────────────────────────────────────┬───────────────┘
                                       │ injected into every Claude call               │
                                       ▼                                               ▼
   ┌─────────────┐   pick format  ┌─────────────┐   render-recipe   ┌──────────────┐   merge fields   ┌─────────────┐
   │ FORMAT LIB  │ ─────────────▶ │  SCRIPT +   │ ───────────────▶  │  CLIP RENDER │ ───────────────▶ │  PUBLISH    │
   │ (recipes)   │                │  HOOK LAB   │                   │  (Shotstack) │                  │ (Ayrshare)  │
   └─────▲───────┘                └──────┬──────┘                   └──────┬───────┘                  └──────┬──────┘
         │                               │ pre-write score                 │ post-render score              │
         │                               ▼ (Opus rubric)                   ▼ (ClipEngine predictor)         │
         │                        ┌──────────────────────────────────────────────────┐                     │
         │                        │              VIRALITY ENGINE                       │                     │
         │                        └───────────────────────┬──────────────────────────┘                     │
         │                                                 │                                                 │
         │        promote best arms                        │ reward = 3s-retention / watch-through           │
         └──────────────── LEARNING LOOP (Thompson bandit) ◀──────── CLIP OUTCOMES ◀──── quartile playbacks ─┘
                                  ▲                                                       (Insights pullback)
                                  │
                          TREND RADAR (per-niche; external Claude+web-search at launch, first-party folds in with scale — §7) ──▶ one line on Today
```

Four nouns recur across every table below — fix their meaning now:

| Term | Definition | Owner |
| --- | --- | --- |
| **Format** | A reusable, versioned *render-recipe*: a saved Shotstack template + structured metadata (beats, caption style, B-roll slots, AI-visual needs, platform tuning). | `formats` table |
| **Hook** | The first ~0–3s opener of a clip, typed by one of **8 signal types**, with a predicted 3-second hold. | `hooks` table |
| **Predictor** | A function producing a 0–100 score. Two surfaces: *pre-write* (Opus rubric over a script) and *post-render* (ClipEngine multimodal model over a rendered clip). | `script_predictions`, `clip_predictions` |
| **Arm** | A bandit arm = a Format (v1) or a Format×hook-type pair (v2). The learning loop samples arms per creator. | `format_bandit_state` |

**North-star metric:** **3-second retention** (the share of viewers still watching at 3.0s). Everything optimizes toward it; raw views are explicitly *not* optimized (they lag and confound with follower count). See §1.

---

## 1. The 3-second cliff — the spine of the engine

The single most reproducible finding across the 2025–2026 literature: distribution decisions collapse at the **3-second mark**, and platforms score the first 3s as a **separate retention metric** from total watch-through. A 46,605-hook TikTok study found emotional hooks are rare (~3.2%) yet drive disproportionate comments/shares, that **curiosity-gap and urgency hooks outperform** direct-address and "how-to" framing, and — critically for our data model — that **optimal hook length is niche-dependent** (≈9 words for news, up to ≈90 for entertainment), with SHAP analysis ranking content category, hook length, urgency cues, and emotional richness as the most reliable predictors ([The Science of the First Three Seconds, 2025](https://doi.org/10.31235/osf.io/rj2mz_v1)).

A manual teardown of 500 one-million-view videos distilled **11 hook patterns that account for ~80% of viral openers**, catalogued along four axes we adopt verbatim as the hook data model — *verbal opening, visual opening, cognitive load, emotional axis* — and found hooks are **platform-divergent**: TikTok rewards visual-first/POV/raw; Reels rewards aesthetic/demonstration-first; Shorts rewards information density (listicles, authority-borrow, specific numbers) ([FluxNote 500-video analysis](https://fluxnote.io/blog/short-form-hook-library-500-viral-videos)).

The **bridge matters as much as the hook.** The completion-rate beat structure is **hook (0–3s) → restate the promise in plain language (3–6s) → one concrete proof point (6–12s) → resolution (12s+)**; most creators skip the bridge ([1kreach — engineering hooks, 2026](https://1kreach.com/blog/first-3-seconds-engineering-hooks-2026)). This beat structure is encoded directly into every Format's `beats[]` (§2).

### 1.1 Retention bands (remote-config, not hard-coded)

These directional bands come from creator-tooling blogs — **treat as marketing data, not ground truth**, and store them in remote config (`engine.retention_bands`) so they can be retuned per platform without a release. They drive *copy* (which feedback string a clip gets), never a gate.

| 3s-retention | Band | Engine meaning |
| --- | --- | --- |
| ≥ 85% | `viral_potential` | Surface as "stronger than 80%+ of your clips." |
| 70–85% | `optimal` | Healthy; ship as-is. |
| 60–70% | `soft` | Offer one fix (hook swap / earlier caption). |
| < 60% | `weak` | Offer the top two fixes; nudge a re-render. |
| Δ > 35% lost in first 3s | `fails_to_broaden` | Flag: opener is shedding the audience before broadening. |

Sources for the bands ([OpusClip — TikTok hook formulas](https://www.opus.pro/blog/tiktok-hook-formulas); [TTS Vibes — first-3-seconds stats](https://insights.ttsvibes.com/tiktok-first-3-seconds-hook-retention-rate/)). **Do not** present a band as a verdict in red; render it as a calm gold/ink suggestion per the aesthetic.

---

## 2. The Format Library — formats as structured RENDER-RECIPES

A **Format is a render-recipe, not a blank talking head.** Concretely, a Format is a **saved Shotstack template plus structured metadata**. Rendering creator footage in a Format = `POST /templates/render` with a `merge` array that swaps the creator's clip URL, hook text, caption text, B-roll URLs, and segment timings into the template's handlebars placeholders. This 1:1 mapping is the central engineering insight of this doc.

### 2.1 Why Shotstack templates *are* render-recipes

The Shotstack Edit API models an edit as `timeline → tracks → clips → assets` (asset types: `video`, `image`, `text`, `audio`, `html`, `luma`, plus transitions/filters/overlays) ([Shotstack API reference](https://shotstack.io/docs/api/)). A saved template (`POST /templates`) uses **double-brace handlebars** placeholders — `{{HOOK_TEXT}}`, `{{CLIP_URL}}`, `{{TRIM}}`, `{{BROLL_1}}` — and `POST /templates/render` resolves them from a `merge: [{find, replace}]` array (the `find` value omits the braces) ([Templates guide](https://shotstack.io/docs/guide/architecting-an-application/templates/); [Merging data guide](https://shotstack.io/docs/guide/architecting-an-application/merging-data/)). Merge fields can target **any property** — a clip's `src`, `start`, `length`, `trim`, position, or scale — which is exactly how a single template parametrizes B-roll swaps, caption text, and per-segment timing ([Studio designers guide](https://shotstack.io/learn/studio-designers-guide/)).

The native primitives cover the entire catalog: **split-screen** (crop + position), **3-up / picture-in-picture** (`picture-in-picture.json`, layered tracks + position/scale), **captions** (`captions.json`, time-synced text), **overlays / lower-thirds / kinetic text** (`.mov` with alpha via luma matte or overlay tracks), **transitions** (luma-matte) ([shotstack/json-examples](https://github.com/shotstack/json-examples); [Shotstack templates gallery](https://shotstack.io/templates/)).

> **Render boundary (cross-ref `01-information-architecture.md`, open question in `05-screens-produce.md`).** v1 plan: **Shotstack performs the final templated render**; the **ClipEngine MCP** supplies the assets Shotstack can't (faceless AI visuals, green-screen background removal, B-roll generation) **and** the post-render virality score. Everything stays behind the `ClipEngine` and a thin `RenderEngine` adapter so a swap is a one-file change. A Format swap in the Clip Editor is therefore a **Shotstack template re-render**, not an MCP re-clip.

### 2.2 Output controls every Format exposes

| Control | Value | Notes |
| --- | --- | --- |
| `output.aspectRatio` / `output.size` | `9:16` / `1080×1920` | Reels + TikTok. Locked for v1. |
| `output.fps` | 30 (TikTok), 30 (Reels) | Per `platform_tuning`. |
| `output.quality` | `high` | |
| `output.range` | `{start, length}` | From AssemblyAI moment boundaries (§3). |
| `poster` / `thumbnail` | frame capture | Cover-frame generation for the Library card. |
| `destinations[]` | Cloudflare **R2** custom provider | Renders land on a **publicly cURL-able R2/Stream URL** — mandatory because IG/TikTok fetch the file by URL (`10-social-publishing.md`). Not Shotstack's default disk. |

Verify merges in staging with `?data=true&merged=true`; poll render status until `done` and capture the mp4 `url`. Use the free `stage` Edit env for dev, `v1` for prod.

### 2.3 The `render_recipe` schema (the heart of a Format row)

Every Format carries a structured `render_recipe` JSON. The Format row stores it; the orchestrator compiles `render_recipe` + creator inputs into the Shotstack `merge` array at render time.

```jsonc
// formats.render_recipe  (jsonb)
{
  "layout": {
    "shotstack_template_id": "tmpl_splitscreen_v3",   // versioned, reproducible
    "template_version": 3
  },
  "beats": [                                            // §1 beat structure, in ms
    { "name": "hook",   "start_ms": 0,     "end_ms": 3000,  "merge": ["HOOK_TEXT"] },
    { "name": "bridge", "start_ms": 3000,  "end_ms": 6000,  "merge": ["BRIDGE_TEXT"] },
    { "name": "proof",  "start_ms": 6000,  "end_ms": 12000, "merge": ["BROLL_1", "PROOF_CAPTION"] },
    { "name": "payoff", "start_ms": 12000, "end_ms": null,  "merge": ["CTA_TEXT"] }
  ],
  "caption_style": {                                    // Marque tokens baked as template defaults
    "font": "Matter-SemiBold",
    "size_pt": 64,
    "fill": "#F4F1EA",            // cream
    "highlight": "#C9A227",       // gold, word-by-word active token
    "position": "lower-third",
    "animation": "word_pop",      // karaoke-style time-synced reveal
    "first_caption_by_ms": 800    // captions MUST appear inside the 3s window
  },
  "broll_slots": [
    { "id": "BROLL_1", "source": "creator_or_ai", "ai_fallback": true, "max_len_ms": 2500 }
  ],
  "ai_visual_needs": {            // routed to ClipEngine MCP, behind the adapter
    "remove_background": false,   // green-screen formats → true (remove_background tool)
    "generate_visual": false      // faceless formats → true (generate_image / generate_video)
  },
  "target_length_s": 32,
  "platform_tuning": {
    "tiktok": { "opener_cut_ms": 400,  "pace": "fast",   "fps": 30, "caption_density": "high" },
    "reels":  { "opener_cut_ms": 600,  "pace": "medium", "fps": 30, "caption_density": "medium" },
    "shorts": { "opener_cut_ms": 600,  "pace": "info",   "fps": 30, "caption_density": "high" }
  }
}
```

> **Doctrine:** **never** build per-creator bespoke imperative edits. A Format is a *versioned template record* (`shotstack_template_id` + `template_version`), so re-rendering an old clip is reproducible and a template fix is a one-row change. Versioning is mandatory: a clip stores the `template_version` it was rendered with.

### 2.4 The Format catalog (v1)

Each Format below is a render-recipe archetype. **Length, pace, and caption density are tuned per platform** per §2.3. Formats requiring AI visuals or background removal are routed to the **ClipEngine MCP** (`generate_image`/`generate_video`/`remove_background`/`reframe`), always behind the adapter.

| Format | Archetype / template | Beats emphasis | B-roll / AI-visual need | Target len (TikTok/Reels/Shorts) | Best-fit hook types (§4) | Platform lean |
| --- | --- | --- | --- | --- | --- | --- |
| **Split-screen** | crop+position, 2 panes | hook on top pane, proof on bottom | optional creator B-roll | 25 / 30 / 30s | Counterintuitive, Visual-interrupt | TikTok/Reels |
| **3-up talking heads** | PiP, 3 layered tracks | rapid intercut across 3 takes | none | 22 / 28 / 28s | POV, Stakes | TikTok |
| **Green-screen** | `remove_background` → composite over visual | subject over swapped backdrop | **AI**: `remove_background` + backdrop image | 25 / 30 / 30s | Authority-borrow, Data-claim | TikTok |
| **Faceless AI-visual** | generated B-roll + VO captions, no face | visual carries; captions dense | **AI**: `generate_image`/`generate_video` per beat | 30 / 35 / 35s | Curiosity-gap, Data-claim | Reels/Shorts |
| **Before / after** | open on result, luma-wipe to process | **visible-result-first** opener | creator footage, ordered | 22 / 26 / 26s | Visible-result-first, Stakes | Reels/TikTok |
| **Myth-buster** | full-frame + bold lower-third "MYTH/TRUTH" | counter-claim hook → correction | text overlays | 28 / 32 / 32s | Counterintuitive, Authority-borrow | Shorts/Reels |
| **Listicle** | numbered kinetic-text chapters | N segments, each a beat | numbered overlays | 30 / 35 / 40s | Data-claim, Authority-borrow | **Shorts** |
| **POV** | full-frame, on-screen scenario caption | second-person framing | optional B-roll | 20 / 24 / 24s | POV, Curiosity-gap | **TikTok** |
| **Reaction** | PiP corner reaction over source | react beat in first 0.5s | source clip + reaction track | 22 / 26 / 26s | Visual-interrupt, Counterintuitive | TikTok/Reels |
| **B-roll + caption-hook** | full-bleed B-roll, hook burned as caption | **caption is the hook**, no spoken opener | B-roll (creator or AI) | 25 / 30 / 30s | Curiosity-gap, Data-claim | Reels |

> **Catalog extensibility.** New formats are added as `formats` rows with a new `shotstack_template_id` — **no app release required**. A remote-config `formats.enabled[]` gates which appear in the Library and the bandit's arm set.

### 2.5 Where the Format Library lives in the UI

- **Script reader / Clip Editor (`05-screens-produce.md`):** when the creator picks a Format for a clip, a single calm row shows the format name + a one-line "what this does." Selecting it is one tap; the recipe is invisible.
- **"Your best formats" is contextual, never a dashboard.** At schedule time the bandit (§6) may surface *one* gentle line — "Listicle has been your strongest format lately" — and nowhere else. **Never on Today.**
- The full catalog is browsable one layer deep from the Clip Editor, as a quiet list — not a grid of loud thumbnails.

---

## 3. Moment detection — AssemblyAI feeds the `range`/`trim` merge fields

"AI edits one batch session into many clips" = a moment-selection layer that produces in/out points, which become Shotstack `output.range` and per-clip `trim` merge fields.

- **Key Phrases** (`auto_highlights: true`) returns `auto_highlights_result.results[]` with `text`, `rank` (relevancy), `count`, and **`timestamps[]` in ms** — direct candidate clip in/out points ([AssemblyAI — identifying highlights](https://www.assemblyai.com/docs/speech-understanding/identify-highlights/identifying-highlights-in-audio-or-video-files)).
- **Word-level timestamps** let us snap clip boundaries to sentence edges; **pad ±0.5s and cut on nearby silence** so words aren't clipped (AssemblyAI's own clip tooling snaps to silence — replicate it).
- Additional predictor *features*: **Sentiment Analysis, Topic Detection, Entity Detection, speaker labels** — stored on the clip and fed to both predictors.
- **Deprecation gotcha — do *not* use `auto_chapters`.** The legacy `auto_chapters` boolean is deprecated; AssemblyAI now steers chapterization through their **LLM Gateway**. Since Marque's locked LLM is **Claude/Anthropic**, do the moment-selection *reasoning* pass with **Claude directly** (Haiku 4.5 for bulk moment ranking, Opus 4.8 only for harder script/teardown reasoning) over AssemblyAI's retrieved paragraphs — keeping the LLM vendor consistent with the stack. **Use AssemblyAI purely for transcription + word-level timestamps + sentiment/key-phrases** ([AssemblyAI — summarized chapters / auto-chapters deprecation](https://www.assemblyai.com/docs/speech-understanding/create-summarized-chapters/creating-summarized-chapters-from-podcasts.mdx)).

```
batch .mov ─▶ AssemblyAI (transcript + word ts + key-phrases + sentiment)
            └▶ Claude Haiku 4.5 (rank candidate moments → clip in/out, pad ±0.5s, snap to silence)
                 └▶ per moment: pick Format (bandit-weighted) + best hook (§4)
                      └▶ Shotstack /templates/render (merge: CLIP_URL, TRIM, HOOK_TEXT, captions…)
```

This pipeline is a Trigger.dev durable job (`01-information-architecture.md`); each `clips` row records `assemblyai_segment` (start/end ms, key_phrase rank, sentiment).

---

## 4. Hook taxonomy (8 signal types) + Hook Lab

### 4.1 The 8 signal types

Each is a **render-affecting signal**, not merely copy — the chosen type changes opener pace, caption timing, and which Format fits. Every type carries a `platform_affinity` map so the Hook Lab re-ranks suggestions by destination platform (TikTok rewards visual-first/POV; Reels rewards aesthetic/demonstration-first; Shorts rewards info-density — §1).

| # | Signal type | Pattern / first-5-words feel | Cognitive load | Emotional axis | `platform_affinity` (tiktok / reels / shorts) |
| --- | --- | --- | --- | --- | --- |
| 1 | **Curiosity gap / open loop** | "the part nobody tells you about…" | question | curiosity | 0.9 / 0.7 / 0.7 |
| 2 | **Counterintuitive / pattern-interrupt** | "posting more is shrinking your account" | claim | contradiction | 0.8 / 0.8 / 0.7 |
| 3 | **Specific-number / data claim** | "I tested this for 90 days…" | claim | curiosity | 0.6 / 0.7 / **0.9** |
| 4 | **Stated stakes** | "I quit Reels for 30 days, here's what happened" | story | aspiration | 0.8 / 0.7 / 0.6 |
| 5 | **Visible-result-first / before-after** | open *on the outcome*, then rewind | demo | aspiration | 0.7 / **0.9** / 0.7 |
| 6 | **POV / second-person scenario** | "POV: you just…" | story | amusement | **0.9** / 0.5 / 0.5 |
| 7 | **Authority-borrow** | credential / named entity up front | claim | aspiration | 0.6 / 0.7 / **0.9** |
| 8 | **Visual interrupt / demonstration-first** | hard cut in first 0.5s, *no spoken line* | demo | curiosity | 0.7 / **0.9** / 0.6 |

> Affinity weights are **remote-config seeds** (`engine.hook_affinity`), not constants — the learning loop (§6) can nudge them per niche over time. The 4-axis catalog (verbal/visual/cognitive/emotional) is stored on each hook so the predictor and bandit can reason over interpretable features ([FluxNote 500-video analysis](https://fluxnote.io/blog/short-form-hook-library-500-viral-videos); [Science of the First Three Seconds](https://doi.org/10.31235/osf.io/rj2mz_v1)).

### 4.2 Hook Lab — nested in the script reader (progressive disclosure)

Per the anti-clutter doctrine, **Hook Lab is not a screen.** It is a collapsed affordance inside the script reader (`05-screens-produce.md`): the script's current hook shows inline with its pre-write 3s-hold estimate. Tapping "Try other hooks" expands **2–3 alternative hooks of *different signal types*** for the *same* script body, each with a **predicted 3s-hold delta** ("+8%"). Re-ranked live by the selected destination platform's affinity.

**Component spec — `HookLabDisclosure`**

| Aspect | Spec |
| --- | --- |
| Trigger | "Try other hooks" row beneath the active hook; calm gold chevron. |
| Content | 2–3 `HookCard`s: signal-type label (small caps grotesque), hook text (serif), `predicted_3s_hold` delta vs. current, word count. |
| Re-rank control | Segmented `Instagram / TikTok` toggle → re-sorts by `platform_affinity`. |
| Action | Tap a card → it becomes the chosen hook (`hooks.chosen = true`); script re-scores. |
| Generation | Alternatives generated by **Claude Opus 4.8** with Brand-Graph voice injected + prompt caching (`07-ai-system.md`); each tagged with its `signal_type`. |
| Constraint | Hook length validated against **niche-appropriate** band (§1; news short, entertainment long), not a fixed cap. |

**States.**

| State | Behavior |
| --- | --- |
| Loading | Hook text holds; alternatives slot in with a slow fade — no spinner over the script. |
| Empty | Fewer than 2 distinct viable types → show what exists; never pad with low-quality variants. |
| Error (LLM) | Collapse silently to the current hook + a quiet "Couldn't fetch alternatives." Script stays fully usable. |
| Offline | Disclosure disabled with a calm hint; last-fetched alternatives (if cached) remain tappable. |

---

## 5. The Virality Engine — two scoring surfaces

The engine has **two predictors**: a **pre-write (script) score** and a **post-render (clip) score**. Be honest about what each is: the script score is an LLM rubric; the clip score is a multimodal model. **Both are surfaced as a *relative* signal ("stronger than 80% of your last clips"), never as an absolute probability of going viral.**

### 5.1 Pre-write (script) predictor — Claude Opus 4.8 rubric judge

**Why a rubric works:** hook performance is predictable from *interpretable* features (content category, hook length, urgency cues, emotional richness were the SHAP-top predictors) ([Science of the First Three Seconds](https://doi.org/10.31235/osf.io/rj2mz_v1)), and rubric-based VLM scoring is an established short-form-virality method ([Rubric-based VLM framework for short-form virality, arXiv 2512.21402](https://arxiv.org/html/2512.21402v1)).

**Opus 4.8** (high-reasoning) judges the script + chosen hook against a fixed rubric, returning a **structured tool output**: a 0–100 overall plus a **per-criterion breakdown so feedback is fixable** ([structured tool outputs + prompt caching — `07-ai-system.md`]). Brand-Graph voice is injected and prompt-cached.

| Rubric criterion | What it checks | Example fixable feedback |
| --- | --- | --- |
| Hook signal-type present | One of the 8 types is clearly present | "Opener is generic — make it a curiosity gap." |
| Hook length (niche-aware) | Within the niche band (§1) | "Hook is 22 words — trim to ≤14 for your niche." |
| Curiosity-or-urgency trigger | At least one present | "No open loop — add 'the part nobody mentions…'." |
| Stated promise | A clear payoff is promised | "Promise is vague — say what the viewer will get." |
| **Bridge present (3–6s)** | The promise is restated plainly | "You jump from hook to detail — add a one-line bridge." |
| Proof specificity (6–12s) | One concrete proof point | "Add one number or example as proof." |
| Payoff / resolution | Lands the loop | "The loop never closes — resolve the hook." |

```jsonc
// script_predictions row (written by FastAPI worker, service role)
{
  "script_id": "…",
  "overall": 78,
  "criteria": [
    {"key":"hook_signal_type","score":0.9,"pass":true,"feedback":null},
    {"key":"hook_length","score":0.4,"pass":false,"feedback":"Hook is 22 words — trim to ≤14."},
    {"key":"bridge_present","score":0.2,"pass":false,"feedback":"Add a one-line bridge after the hook."}
  ],
  "model":"opus-4.8",
  "prompt_cache_key":"brandgraph:<creator>:v12"
}
```

**Surface:** inline in the script reader as a small serif number + the *single highest-leverage* fix as a tap-to-expand. Not a panel of seven warnings.

### 5.2 Post-render (clip) predictor — ClipEngine multimodal, honestly framed

After Shotstack renders, the **ClipEngine MCP `virality_predictor` tool** scores the finished mp4 — the **v1 black-box scorer behind the `ClipEngine` adapter** (`01-information-architecture.md`).

**Frame it honestly.** The strongest published academic signals are **content-only cold-start** metrics: **Engagement Continuation Rate (ECR)** = P(watch > 5s) and **NAWP** (normalized average watch %) ([SnapUGC — engagement prediction, arXiv 2410.00289](https://arxiv.org/html/2410.00289v1)). Top systems (ICCV VQualA 2025) found **audio is a critical modality** — audio-visual models beat visual-only — and frame-sampling density matters ([LMM engagement prediction, ICCV 2025](https://arxiv.org/html/2508.02516v2); [MMF-QE multi-modal fusion, ICCVW 2025](https://openaccess.thecvf.com/content/ICCV2025W/VQualA/papers/Guan_MMF-QE_Advanced_Multi-Modal_Fusion_for_Quality_Assessment_and_Engagement_Prediction_ICCVW_2025_paper.pdf)). So the post-render subscore set spans **hook hold, audio (music/energy), visual/aesthetic quality, caption density, pacing** — not just transcript.

```jsonc
// clip_predictions row
{
  "clip_id":"…","format_id":"…",
  "overall": 71,
  "subscores": {"hook_hold":0.55,"audio":0.80,"aesthetic":0.74,"caption":0.40,"pacing":0.68},
  "fix_suggestions": [
    {"key":"hook_hold","action":"swap_hook","payload":{"to_hook_id":"hk_var2"},
      "label":"Hook hold is weak — try hook variant #2."},
    {"key":"caption","action":"enable_caption_track","payload":{"by_ms":800},
      "label":"No captions in the first 3s — turn them on."},
    {"key":"pacing","action":"tighten_trim","payload":{"trim_dead_air_at_ms":2000},
      "label":"Dead air at 0:02 — tighten the cut."}
  ],
  "predictor_source":"mcp",            // "in_house" later
  "predictor_version":"clipengine-2026.06"
}
```

### 5.3 The fixable-feedback loop (closes back into §2)

Every low subscore maps to a **concrete render fix that re-merges the Shotstack template** — the loop is mechanical, not advisory:

| Low subscore | `action` | Re-render effect |
| --- | --- | --- |
| `hook_hold` | `swap_hook` | New `HOOK_TEXT` merge → re-render. |
| `caption` | `enable_caption_track` | Caption track + `first_caption_by_ms` → re-render. |
| `pacing` | `tighten_trim` | New `TRIM`/`range` merge → re-render. |
| `aesthetic` | `swap_broll` | New B-roll URL (creator or AI) → re-render. |

In the Clip Editor each fix is **one tap = one re-render**, with the new clip's score shown after it lands. Re-renders are durable Trigger.dev jobs.

### 5.4 Predictor states

| State | Pre-write | Post-render |
| --- | --- | --- |
| Loading | Inline shimmer on the number only. | Card shows "Scoring…" with the clip thumbnail; never blocks the Library. |
| Unavailable (LLM/MCP down) | Hide the score; script/clip stays fully usable; quiet "Score unavailable." | Same — publishing is **never gated** on a score. |
| Low-confidence | Show as a range or omit; never invent precision. | Label "preliminary." |
| Offline | Last cached score shown, badged "as of <time>." | Same. |

---

## 6. The learning loop — batched Thompson-sampling bandit per creator

"Promote the best-performing formats per creator" = a **batched Thompson-sampling multi-armed bandit** — the best-evidenced design choice in this spec.

### 6.1 Design

- **Arms = Formats** (v1). v2: Format×hook-type pairs. Per arm, keep **Beta(α, β)**; on each decision **sample** from every arm and pick the max — the standard content-card bandit ([Thompson-sampling content cards, arXiv 2108.01440](https://ar5iv.labs.arxiv.org/html/2108.01440)).
- **Reward = a normalized engagement outcome — 3s-retention / watch-through, NOT raw views** (avoids follower-count confounds). Source of truth = **video quartile playback counts** from the Insights pullback (§7).
- **Batched updates, not real-time.** Update `α`/`β` on a cadence — per creator, on each new analytics pull (the production pattern: evaluate over a window, update on a schedule) ([AWS / LotteON — dynamic A/B with batched Thompson sampling](https://aws.amazon.com/blogs/machine-learning/how-lotteon-built-dynamic-a-b-testing-for-their-personalized-recommendation-system/)).
- **Cold-start warm-up is mandatory.** New creators are seeded with **niche-level priors** (global best Formats for their niche) and forced through an exploration warm-up before the bandit is allowed to exploit ([same AWS source]; [contextual-bandit-recommender — warm-start](https://github.com/Sakeeb91/contextual-bandit-recommender)). Track this with `niche_prior_seeded`.
- **Defer contextual bandits.** LinUCB / neural-TS keyed on creator features are a v3 option, but the production pitfalls are real — **calibrate the sampled probabilities, tune/monitor exploration, avoid cohort data leakage** ([BanditLP, arXiv 2601.15552](https://arxiv.org/html/2601.15552)). **Launch with plain batched Beta-Bernoulli TS per creator.**

### 6.2 Reward must be settled + idempotent

Matching Marque's house learning-loop discipline: a clip's outcome is **counted exactly once**, after a fixed **measurement window (48–72h post-publish)**, keyed by `run_id`/`post_id`, **reach-normalized** so retries/backfills cannot double-count.

```
on publish        → clip_outcomes row (ayrshare_post_id, run_id, settled_at = null)
+48–72h window    → pull quartile playbacks (07/06-publishing) → compute reward (reach-normalized)
                    → set settled_at, write reward ONCE (idempotent on run_id)
batch update tick → per creator: for each settled, unapplied reward,
                    α += reward, β += (1 − reward); mark applied; bump last_updated
```

### 6.3 Where the loop touches the UI

- **Format selection** in the auto-clip pipeline (§3) is bandit-weighted per creator.
- **Insights (`05-screens-produce.md`):** "your best Formats" lives here, surfaced contextually — and as **one gentle nudge at schedule time**. **Never a Today dashboard.**
- The bandit is invisible to the creator; it just makes the defaults quietly better.

---

## 7. Trend Radar — per-niche, refreshed

No clean platform-wide "trending sounds/topics" API exists in the locked stack, so sourcing is explicit. **The single Today trend line is one of only three allowed Today elements (one directive + one gold streak glyph + one trend line), so it can never be blank at launch.** That constraint forces a *committed* v1 source with a defined cold-start behavior — not an open question.

#### 7.0 v1 decision (committed)

> **v1 Trend Radar primary source = a scheduled Claude + web-search external pass per niche.** First-party aggregation (Marque's own creator base) is the *long-term* differentiator but is structurally unavailable at launch: every niche has `< N` Marque creators on day one, so a first-party-first design would render the Today trend line empty for every creator at GA — the exact failure the anti-clutter doctrine forbids. We therefore **launch on the external pass** and **fold first-party signal in as it accrues**, flipping the blend per niche once that niche crosses a creator/clip-volume floor.

| Phase | Primary source | First-party role | Trigger |
| --- | --- | --- | --- |
| **GA (launch)** | Claude + web-search external pass per niche (`source='external'`) | none (no data yet) | ships M5 |
| **Blended** | external pass **+** first-party lift, merged by confidence | re-ranks/augments external rows once a niche has data | niche crosses **≥ 50 creators _or_ ≥ 500 settled `clip_outcomes`** in 30 days (remote-config `trends.first_party_floor`) |
| **First-party-led** | first-party lift dominates; external pass becomes the freshness top-up | primary signal | niche is mature + first-party lift validated against outcomes |

The blend is a per-niche remote-config weight (`trends.blend.<niche>`), so a niche graduates from external → blended → first-party-led **without a release**. `trends.source` records which path produced a given row.

#### 7.1 The external pass — cadence, cost, caching, compliance

- **What it is.** A FastAPI-triggered, Trigger.dev-durable job that, per active niche, runs **one Claude call with the web-search tool enabled** (`07-ai-system.md`) to synthesize *trending topics, angles, and audio/sound references* for that niche, returning a **structured tool output** matching the `trends.signal` schema below. **Haiku 4.5** does the bulk synthesis (it is cheap, non-interactive, and runs via the Message Batches API at 50% off); **Opus 4.8** is reserved for niches flagged low-confidence on a re-rank pass. Brand-Graph context is *not* needed here — the pass is per **niche**, not per creator, which is what makes it cheap and cacheable.
- **Cadence.** **Once daily per *active* niche**, on a 06:00 UTC cron — *not* per creator. A "niche" is active if ≥1 creator has it set in their Brand Graph (`03-onboarding.md`). Result is written once to `trends` and **fanned out to every creator in that niche from cache** — so cost scales with *distinct active niches* (tens), not creators (thousands).
- **Cost envelope.** One cached row per niche per day. With Haiku 4.5 + Batches and a bounded web-search budget, the daily spend is `≈ (active_niches × 1 batched call)` — low-tens of cents/day at launch scale, and **flat in creator count**. A remote-config `trends.max_web_searches_per_niche` caps tool calls per pass so a single niche can't run away. This is the explicit answer to "08 falls back to a Claude + web-search pass that no doc specifies the cost/cadence/compliance of."
- **Caching.** Every creator reads the **cached** niche row (`trends.refreshed_at`); the Today line and Trends screen never trigger a live model call. A cron miss serves the last cached row, badged "as of <date>" (states table below).
- **Compliance.** The pass **synthesizes from Claude's web-search results**; it does **not** scrape TikTok/IG, call any unlicensed third-party trend API, or store platform UGC. Trend *topics/angles/audio names* are non-proprietary signal. Any externally suggested **audio** is surfaced as a *named reference for the creator to add inside the platform's own composer* — Marque never re-hosts or attaches copyrighted audio to a render (`10-social-publishing.md`). This keeps the external pass clear of the "personal account management utility" and licensing gray-areas flagged for TikTok in §9.

#### 7.2 First-party signal (folds in with scale)

- **Insights pullback (Phyllo/Ayrshare behind the `Insights` adapter)** gives the *creator's own* analytics — **not** platform-wide trends — and aggregated across a niche it becomes the first-party lift signal: which Formats/hooks/topics are **currently overperforming across Marque's own creator base in the same niche** (`source='first_party'`). Privacy-safe (aggregated, never per-creator-identifying), defensible, and it *improves with scale*. **This is the eventual differentiator: Marque's Trend Radar learns from Marque creators, not a generic scraper** — but it is *additive to*, never a launch substitute for, the external pass.
- The bandit's per-niche priors (§6) and `clip_outcomes` (§8) supply this lift directly, so first-party trends are a read over data the engine already produces.

#### 7.3 Refresh

- **Refresh per active niche on a daily cron and cache** (`trends.refreshed_at`); fan out from cache to every creator in the niche.

```jsonc
// trends row
{
  "niche":"fitness",
  "source":"external",                    // v1 default (Claude+web-search); "first_party" | "blended" as a niche matures (§7.0)
  "signal": {
    "top_formats":[{"format_slug":"listicle","lift":0.22}],   // display projection — slug, not the uuid FK
    "top_hooks":[{"signal_type":"data_claim","lift":0.18}],
    "topics":[{"label":"zone-2 cardio","momentum":0.31}],
    "audio":[]
  },
  "refreshed_at":"2026-06-29T06:00:00Z"
}
```

**UI (binding, per anti-clutter doctrine):** **exactly one trend line on Today** → taps through to the **dedicated Trends screen** (`05-screens-produce.md`). Nothing else from this system bolts onto Today.

| State | Behavior |
| --- | --- |
| Loading | Today's trend line shows the last cached line, no spinner. |
| Empty (brand-new app / cold niche, `< N` first-party creators) | **Never blank.** The daily external (Claude+web-search) pass is the *launch primary*, so a freshly cached external row is always present; first-party lift simply isn't blended in yet. Only if the *external pass itself* has never succeeded for a niche (e.g. a niche activated <24h ago, before its first cron) show the calm "Trends are still learning your niche." line — and trigger an on-demand external pass so the next read is populated. |
| Stale (cron missed) | Show cached line badged "as of <date>." |
| Offline | Cached line remains; Trends screen shows "Reconnect to refresh." |

---

## 8. Data model (Supabase / Postgres)

Screen-facing projections below; defer to the canonical `03`-series data-model authority on conflict. **RLS: every table is creator-scoped; predictor/bandit/trend writes happen via the *service role* from FastAPI / Trigger.dev workers — never the client.**

> **Canonical key note (binding).** `formats.id` is a **`uuid` PK**; `slug` is a **display-only** human-readable handle (`'listicle'`, `'split-screen'`). **Every FK joins on the `uuid`** — `clips.format_id`, `clip_predictions.format_id`, `format_bandit_state.format_id`, and `scripts.format_id` are all `uuid references formats(id)`. `slug` is used **only** for human-readable bandit arm labels and UI copy, never as a join key. The canonical data-model authority is **`12-backend-data-security.md §3.4`** (`formats` is a global, read-only catalog with `uuid` PK + `text unique slug`); this section is a screen-facing projection of it and defers to it on any conflict. This resolves the prior `text`-vs-`uuid` divergence between this doc and `12`, which would otherwise have surfaced late through `12 §3.8`'s `generate_typescript_types`-matches-Swift acceptance check.

```sql
-- Versioned render-recipes (projection of 12-backend-data-security.md §3.4 — the canonical authority)
create table formats (
  id                  uuid primary key default gen_random_uuid(),  -- canonical key; FKs join on this
  slug                text unique not null,         -- display-only handle: 'listicle', 'split-screen'…
  name                text not null,
  archetype           text not null,
  shotstack_template_id text not null,
  template_version    int  not null default 1,
  default_aspect_ratio text not null default '9:16',
  target_length_s     int  not null,
  render_recipe       jsonb not null,              -- §2.3 (beats, caption_style, broll_slots, ai_visual_needs, platform_tuning); = 12 §3.4 `recipe`
  active              boolean not null default true -- = 12 §3.4 `enabled`
);

-- 8 enum rows (§4.1)
create table hook_signal_types (
  key               text primary key,              -- 'curiosity_gap','counterintuitive',…
  display_name      text not null,
  cognitive_load    text not null,                 -- question|claim|story|demo|list
  emotional_axis    text not null,                 -- curiosity|pain|aspiration|contradiction|amusement
  platform_affinity jsonb not null                 -- {"tiktok":0.9,"reels":0.7,"shorts":0.7}
);

create table hooks (
  id                uuid primary key default gen_random_uuid(),
  creator_id        uuid not null references profiles(id),
  script_id         uuid not null references scripts(id),
  signal_type       text not null references hook_signal_types(key),
  text              text not null,
  predicted_3s_hold numeric,                        -- 0..1
  word_count        int,
  chosen            boolean not null default false,
  created_at        timestamptz not null default now()
);

create table script_predictions (
  id                uuid primary key default gen_random_uuid(),
  creator_id        uuid not null references profiles(id),
  script_id         uuid not null references scripts(id),
  overall           int not null check (overall between 0 and 100),
  criteria          jsonb not null,                 -- per-rubric fixable feedback (§5.1)
  model             text not null,                  -- 'opus-4.8' | 'haiku-4.5'
  prompt_cache_key  text,
  created_at        timestamptz not null default now()
);

create table clip_predictions (
  id                uuid primary key default gen_random_uuid(),
  creator_id        uuid not null references profiles(id),
  clip_id           uuid not null references clips(id),
  format_id         uuid not null references formats(id),    -- joins on formats.id (uuid)
  overall           int not null check (overall between 0 and 100),
  subscores         jsonb not null,                 -- {hook_hold,audio,aesthetic,caption,pacing}
  fix_suggestions   jsonb not null,                 -- actionable re-render fixes (§5.2)
  predictor_source  text not null,                  -- 'mcp' | 'in_house'
  predictor_version text not null,
  created_at        timestamptz not null default now()
);

create table clips (
  id                 uuid primary key default gen_random_uuid(),
  creator_id         uuid not null references profiles(id),
  batch_session_id   uuid not null,
  format_id          uuid not null references formats(id),    -- joins on formats.id (uuid)
  template_version   int  not null,                 -- reproducibility (§2.3)
  hook_id            uuid references hooks(id),
  assemblyai_segment jsonb,                          -- {start_ms,end_ms,key_phrase_rank,sentiment}
  render_id          text,                           -- Shotstack render id
  r2_url             text,                           -- public, cURL-able (06-publishing)
  stream_uid         text,
  created_at         timestamptz not null default now()
);

-- Beta-Bernoulli arm per creator (§6)
create table format_bandit_state (
  creator_id        uuid not null references profiles(id),
  format_id         uuid not null references formats(id),     -- joins on formats.id (uuid); arm labels use formats.slug
  alpha             numeric not null default 1,
  beta              numeric not null default 1,
  impressions       int not null default 0,
  niche_prior_seeded boolean not null default false,
  last_updated      timestamptz not null default now(),
  primary key (creator_id, format_id)
);

-- Settled, idempotent reward (§6.2)
create table clip_outcomes (
  id                 uuid primary key default gen_random_uuid(),
  creator_id         uuid not null references profiles(id),
  clip_id            uuid not null references clips(id),
  ayrshare_post_id   text not null,
  platform           text not null,                  -- 'instagram' | 'tiktok'
  quartile_playbacks jsonb,                           -- {25,50,75,100}
  retention_3s       numeric,
  reach              int,
  reward             numeric,                          -- reach-normalized, written once
  run_id             text not null,                    -- idempotency key
  settled_at         timestamptz,
  unique (run_id)
);

create table trends (
  id           uuid primary key default gen_random_uuid(),
  niche        text not null,
  source       text not null,                          -- 'first_party' | 'external'
  signal       jsonb not null,                          -- §7
  refreshed_at timestamptz not null default now()
);
```

### 8.1 UI placement summary (anti-clutter doctrine)

| Surface | What this engine puts there |
| --- | --- |
| **Today** | **One** Trend Radar line + gold streak glyph + one directive. **Nothing else.** |
| **Script reader** | Hook Lab (nested, progressive disclosure) + inline pre-write score + single top fix. |
| **Record / Review (Clip Editor)** | Clip-level virality score + fixable feedback per clip; "film once → post all week" auto-schedule. |
| **Trends screen** | Full per-niche Trend Radar. |
| **Insights** | Archived teardown cards + outcomes; bandit-driven "your best Formats," surfaced contextually (e.g., a schedule-time nudge) — never a dashboard. |

---

## 9. Publish & analytics constraints that *bound* this engine

The engine's reward signal and trend refresh depend on the publishing/analytics surface. Publisher = **Ayrshare** adapter; real targets = **Instagram Graph API (Content Publishing)** + **TikTok Content Posting API**. Full compliance lives in `10-social-publishing.md` / the publishing half of `05-screens-produce.md`; the engine-relevant hard facts:

- **Reward signal = quartile playbacks.** Ayrshare `/analytics/post` (by Ayrshare Post ID) returns per-platform likes/impressions/views and **`playback25/50/75/100Count`** — **these quartiles are our retention proxy and the bandit's reward** (`100Count/impressions` ≈ watch-through; the 25→50 drop ≈ the 3s-cliff proxy). Backfills cumulative metrics when the network is briefly unavailable ([Ayrshare — analytics on a post](https://www.ayrshare.com/docs/apis/analytics/post.md)).
- **Film-once-post-all-week backend = Ayrshare auto-schedule** (`autoSchedule:true` + named UTC schedule, `weekdays` 0–6, `excludeDates`) — the natural way to spread a batch's clips across the week's next slots ([Ayrshare — auto-schedule](https://www.ayrshare.com/docs/apis/auto-schedule/set-schedule)). Store the returned **Ayrshare Post ID** — it's the join key to outcomes.
- **Renders must be publicly reachable URLs.** IG `video_url` and TikTok `PULL_FROM_URL` both fetch by URL → serve from **Cloudflare R2/Stream** public URLs (verify the R2/Stream domain as a TikTok pull-prefix).
- **IG limits bound trend/reward cadence:** **50 published posts / rolling 24h** (the *authoritative reference page* says 50; other Meta pages say 100 — discrepancy recorded below), **containers expire after 24h**, **≤400 containers / 24h**; check `GET /<IG_ID>/content_publishing_limit` ([IG — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/); [IG — media_publish ref](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/)).
- **TikTok is the most restrictive surface and shapes go-to-market.** Until the API client passes TikTok's **Content Posting Audit** (2–6 weeks), every post is forced **`SELF_ONLY`**, unaudited clients serve **≤5 users / 24h**, and TikTok **explicitly rejects "personal account management utilities"** — Marque must position as serving a broad creator audience. Caps: **~15 posts/day/creator** (shared across clients), **≤5 pending shares / 24h**, publish/init **6 req/min**, tokens expire ~24h. Mandatory audit UX: show creator nickname from `creator_info/query`, a **Commercial Content** disclosure, and **music-usage consent** ([TikTok — content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines); [TikTok — Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)).

---

## 10. Acceptance criteria

**Format Library**
- [ ] Every Format is a `formats` row with a valid `shotstack_template_id` + `template_version`; no imperative per-creator edits exist anywhere in the codebase.
- [ ] Rendering a clip in a Format issues `POST /templates/render` with a `merge` array compiled from `render_recipe` + creator inputs; merge verified in staging with `?data=true&merged=true`.
- [ ] All 10 v1 Formats render to a `9:16`, 1080×1920 mp4 on a **public R2 URL**.
- [ ] A Format swap in the Clip Editor re-renders via Shotstack (not an MCP re-clip) and stores the new `template_version`.
- [ ] Green-screen / faceless Formats route AI-visual needs through the `ClipEngine` adapter only.

**Hooks + Hook Lab**
- [ ] All 8 signal types exist as `hook_signal_types` rows with a `platform_affinity` map.
- [ ] Hook Lab is **nested in the script reader** — it is not a tab or full screen.
- [ ] Hook Lab offers 2–3 alternatives of *different* signal types, each with a 3s-hold delta, re-ranked by the Instagram/TikTok toggle.
- [ ] Hook length validation uses the **niche band**, not a fixed cap.

**Virality Engine**
- [ ] Pre-write score returns a 0–100 + per-criterion fixable feedback; the reader shows the number + the single highest-leverage fix.
- [ ] Post-render score returns `overall` + subscores `{hook_hold, audio, aesthetic, caption, pacing}` + ≥1 actionable `fix_suggestion`.
- [ ] Each `fix_suggestion` maps to a concrete re-merge action; "apply fix" triggers exactly one re-render and shows the new score.
- [ ] Scores are shown as **relative** ("stronger than N% of your clips"), never as an absolute virality probability.
- [ ] Publishing is **never gated** on a score; missing/failed scores degrade calmly.

**Learning loop**
- [ ] Plain batched Beta-Bernoulli TS per creator; `format_bandit_state` has one row per (creator, format).
- [ ] New creators are seeded with niche priors and `niche_prior_seeded=true`; a warm-up window precedes exploitation.
- [ ] Reward is computed from quartile playbacks, **reach-normalized**, written **once** per `run_id` after a 48–72h window; backfills cannot double-count.
- [ ] "Your best Formats" appears only in Insights / as a schedule-time nudge — **never on Today**.

**Trend Radar**
- [ ] Today shows **exactly one** trend line; the full radar is on the Trends screen.
- [ ] v1 trend signal is the **external Claude+web-search pass** per active niche, refreshed daily via cron + cached and fanned out from cache; first-party (Marque creator base) lift blends in per niche once `trends.first_party_floor` is crossed (§7.0). The Today trend line is **never blank at GA**.
- [ ] The external pass runs **once daily per active niche** (not per creator), via Trigger.dev, with Haiku 4.5 + Message Batches and a `trends.max_web_searches_per_niche` cap; cost is flat in creator count.

**Cross-cutting / States**
- [ ] Every engine table is creator-scoped via RLS; all predictor/bandit/trend writes are service-role only.
- [ ] Every surface defines loading / empty / error / offline states; **no full-screen spinners, no red error states.**

---

## Open questions

1. **IG rate-limit discrepancy (50 vs 100 / 24h).** Meta doc pages disagree; the authoritative `media_publish` reference says **50**. Pin to 50 in the `Publisher` quota accountant and re-confirm at integration. (`10-social-publishing.md`)
2. **Trend Radar data source for GA — DECIDED (§7.0).** v1 ships on the **external Claude + web-search pass** (committed: it's the only source with data on day one), with first-party lift blending in per niche as volume accrues. *Remaining* product call: whether to *additionally* license a paid external trend feed later for richer audio/sound coverage — TikTok Creative Center has no robust public API and third-party trend APIs are licensing gray-areas, so this is a post-GA investment decision, not a launch blocker. `05-screens-produce.md §6` and `17-roadmap-milestones.md` M5 are reconciled to this decision.
3. **In-house virality predictor timing.** v1 = ClipEngine MCP `virality_predictor` + Opus rubric. The in-house model needs a labeled outcome dataset Marque won't have at launch (it accrues from `clip_outcomes`). When to invest is a roadmap decision; keep `predictor_source`/`predictor_version` so a swap is a one-row change.
4. **Contextual vs. plain bandit.** Recommend plain batched Beta-Bernoulli TS at launch; contextual (LinUCB / neural-TS) deferred due to calibration / cohort-leakage risk. Confirm the v2 trigger (likely: enough per-creator volume to justify context features).
5. **TikTok audit timing gates GA on TikTok.** Forced-`SELF_ONLY` + ≤5-user cap until the 2–6 week audit passes, and TikTok rejects "personal account utilities." Needs go-to-market sequencing — **IG-first launch?** Also: does Ayrshare's managed integration absorb TikTok's audit, or must Marque undergo it directly? (`10-social-publishing.md`)
6. **Confirm the R2/Stream delivery domain** is verifiable as a TikTok `PULL_FROM_URL` prefix *and* publicly cURL-able for IG's `video_url` fetch.
7. **Arm granularity for v2.** Format-only arms (v1) vs. Format×hook-type arms (v2). The latter multiplies the arm count (≈10 formats × 8 hook types = 80) and worsens cold-start — confirm there's enough per-creator volume before enabling.
8. **Quartile→3s-retention mapping.** Quartile playbacks give 25/50/75/100% watch points, not a literal 3.0s point. Confirm the chosen proxy (the 25-quartile drop, or supplement with platform-native first-3s metrics where the Insights adapter exposes them).

---

## Sources

1. [The Science of the First Three Seconds (2025)](https://doi.org/10.31235/osf.io/rj2mz_v1) — 46,605-hook TikTok study; SHAP-ranked predictors (content category, hook length, urgency, emotional richness); niche-dependent hook length; 5 hook archetypes; curiosity/urgency > how-to. Basis for the hook taxonomy + pre-write rubric.
2. [FluxNote — 500 viral videos hook library](https://fluxnote.io/blog/short-form-hook-library-500-viral-videos) — 11 hook patterns ≈80% of viral openers; the 4-axis hook catalog (verbal/visual/cognitive/emotional); TikTok/Reels/Shorts platform divergence. Basis for the hook data model + `platform_affinity`.
3. [1kreach — Engineering hooks (2026)](https://1kreach.com/blog/first-3-seconds-engineering-hooks-2026) — hook→bridge→proof→payoff beat structure with timings. Basis for Format `beats[]`.
4. [OpusClip — TikTok hook formulas](https://www.opus.pro/blog/tiktok-hook-formulas) — directional 3s-retention bands (*marketing data*, used for remote-config copy).
5. [TTS Vibes — first-3-seconds retention stats](https://insights.ttsvibes.com/tiktok-first-3-seconds-hook-retention-rate/) — directional retention thresholds (*marketing data*).
6. [Shotstack — API reference](https://shotstack.io/docs/api/) — timeline→tracks→clips→assets edit schema; output controls.
7. [Shotstack — Architecting with templates](https://shotstack.io/docs/guide/architecting-an-application/templates/) — templates + handlebars placeholders = the render-recipe mechanism.
8. [Shotstack — Merging data](https://shotstack.io/docs/guide/architecting-an-application/merging-data/) — `merge:[{find,replace}]`; `find` omits braces; merge targets any property.
9. [Shotstack — Studio designers guide](https://shotstack.io/learn/studio-designers-guide/) — merge fields can target src/start/length/trim/position/scale.
10. [shotstack/json-examples](https://github.com/shotstack/json-examples) — concrete JSON for split-screen, picture-in-picture, captions, luma-matte overlays (Format catalog primitives).
11. [Shotstack — templates gallery](https://shotstack.io/templates/) — splitscreen / PiP / caption / overlay template archetypes.
12. [AssemblyAI — Identifying highlights (Key Phrases)](https://www.assemblyai.com/docs/speech-understanding/identify-highlights/identifying-highlights-in-audio-or-video-files) — `auto_highlights` ranked, timestamped highlights → clip in/out points.
13. [AssemblyAI — Summarized chapters (auto_chapters deprecation)](https://www.assemblyai.com/docs/speech-understanding/create-summarized-chapters/creating-summarized-chapters-from-podcasts.mdx) — `auto_chapters` deprecated → do moment-selection reasoning with Claude.
14. [SnapUGC — engagement prediction (arXiv 2410.00289)](https://arxiv.org/html/2410.00289v1) — ECR (P>5s watch) & NAWP as content-only cold-start metrics; multimodal features. Post-render predictor framing.
15. [LMM engagement prediction, ICCV 2025 (arXiv 2508.02516)](https://arxiv.org/html/2508.02516v2) — audio is a critical predictor modality; frame-sampling density matters.
16. [MMF-QE multi-modal fusion, ICCVW 2025](https://openaccess.thecvf.com/content/ICCV2025W/VQualA/papers/Guan_MMF-QE_Advanced_Multi-Modal_Fusion_for_Quality_Assessment_and_Engagement_Prediction_ICCVW_2025_paper.pdf) — multimodal fusion for quality + engagement prediction.
17. [Rubric-based VLM framework for short-form virality (arXiv 2512.21402)](https://arxiv.org/html/2512.21402v1) — rubric-based VLM scoring; basis for the Opus pre-write rubric.
18. [Thompson-sampling content cards (arXiv 2108.01440)](https://ar5iv.labs.arxiv.org/html/2108.01440) — Beta-Bernoulli MAB for content selection.
19. [AWS / LotteON — dynamic A/B with batched Thompson sampling](https://aws.amazon.com/blogs/machine-learning/how-lotteon-built-dynamic-a-b-testing-for-their-personalized-recommendation-system/) — batched update cadence + warm-up in production.
20. [contextual-bandit-recommender — warm-start](https://github.com/Sakeeb91/contextual-bandit-recommender) — cold-start / collaborative warm-start priors.
21. [BanditLP (arXiv 2601.15552)](https://arxiv.org/html/2601.15552) — production bandit pitfalls: calibrate sampled probabilities, tune exploration, avoid cohort leakage; reason to defer contextual.
22. [Instagram — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) — container→publish flow, public-URL requirement, 24h container expiry, 400-container cap, `content_publishing_limit`.
23. [Instagram — media_publish reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/) — authoritative 50-post/24h limit.
24. [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) — audit gate (SELF_ONLY until audited), ≤5-user cap, mandatory consent UX, rejects "personal account utilities."
25. [TikTok — Content Posting API: Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — creator_info/query → video/init → status flow; PULL_FROM_URL verified-prefix requirement; caps.
26. [Ayrshare — Analytics on a post](https://www.ayrshare.com/docs/apis/analytics/post.md) — `playback25/50/75/100Count` quartile metrics = the bandit reward signal; backfill behavior.
27. [Ayrshare — Auto-schedule](https://www.ayrshare.com/docs/apis/auto-schedule/set-schedule) — `autoSchedule` named UTC schedule; backend for film-once-post-all-week.
