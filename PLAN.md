# Marque — Redesign + AI Buildout (execution spec)

Audited 2026-06-29. This is the spec the build executes against. maxapp facts cite the
forensic audit of `/Users/home/maxapp/mobile`.

## 0. Audit verdict — what's actually broken
- **Pillars are hard-coded, not AI.** `AppStore.derivePillars()` assigns the same 4 generic
  strings to everyone; "Analyze my page" is a 1.1s `Task.sleep` that sets static `topThemes`.
  Pillars carry only name+color — no description/angle/topics. (Scripts *are* real live-Claude.)
- **Scripts have no title/summary** → Studio cards show a 2-line-truncated hook; no progressive
  disclosure (card → summary → full → record).
- **Recorded footage is discarded** — `Clip` has no media field; the captured `.mov` is dropped.
  Library shows placeholder rectangles. No playback.
- **No personal-media corpus** for the AI to reference.
- **No real analytics** — every number is a local count or a pre-publish predicted score. Today
  surfaces none of it.
- **Calendar** — no thumbnails, no edit/reschedule/delete, fixed 18:00, caption-only mock publish
  (no media URL), no auto-captions.
- **Aesthetic drift** — serif is Fraunces (maxapp uses Playfair Display); type scale isn't
  systematized (no tracking discipline, `displayL` is sans by accident); LiquidGlass primitive
  absent; floating pill tab bar (maxapp is docked frosted + center FAB); gold→blue alias drift
  makes the offline banner accidentally blue.

## 1. Aesthetic — match maxapp exactly
**Tokens (keep — already correct):** canvas `#FFFFFF`, onboarding/home canvas `#F1F1EF`, ink
`#111113`, accent blue `#2C6BED`, hairline `rgba(0,0,0,0.08)`, divider `#E5E5E5`, muted text
`#9A9A9A`/`#555555`. Radii sm10/md14/lg18/xl22. Continuous (squircle) corners everywhere.
**Fonts:** serif → **Playfair Display** (copy TTFs from maxapp, replace Fraunces). Sans → Inter
(Matter substitute; Matter is proprietary). 
**Type discipline (the premium tell):**
- Headlines/large numerals: negative tracking (-0.2 → -2). Hero numeral 44px ls -1.5.
- UPPERCASE micro-labels: wide tracking (1.2–1.6), Inter-SemiBold 11px, muted, optional 3px accent bar.
- Body/buttons: neutral tracking (0.1–0.3).
- **lowercase Playfair** on screen titles + card titles = maxapp's editorial signature.
- Onboarding step questions are **sans-bold** (Inter-Bold ~30 ls -0.6); serif reserved for hero +
  reveal/aha + screen titles + card titles + big numerals.
**LiquidGlass primitive** (SwiftUI): `.ultraThinMaterial` + corner speculars + top sheen + 3
luminous rims + cool float shadow (`#3A3358` @ 0.22, r26, y14). Used ONLY on: docked tab bar,
center FAB, floating controls over media, media-hero overlays. NOT on flat cards (rejected on light bg).
**Shadows:** warm/cool-tinted, low opacity (0.04–0.09), generous radius, downward offset.
**Tab bar:** docked edge-to-edge frosted (BlurView + milky white 0.62 + top sheen + hairline top
rim), icons 22, active `#111113`/inactive `#9A9A9A`, no active indicator, labels 10px; **center
FAB** = Record/Create (maxapp's center-Scan pattern).
**ShineSweep:** 86px white streak rotated 18°, ~1.4s sweep + long pause (~4.2s loop). Premium CTAs only.

## 2. Data model (`Models.swift`)
- `Script` += `title` (≤6 words), `summary` (1 line).
- `Clip` += `title`, `captionLines[]`, `localVideoPath?`, `remoteURL?`, `thumbnailPath?`, `captioned`.
- `Pillar` += `summary`, `angle` (the creator's unique take), `exampleTopics[]`.
- New `MediaKind`, `MediaAsset` (personal corpus), `Footage` (filmed-but-undecided takes),
  `PostMetrics` (views/likes/comments/shares/follows).
- `ScheduledPost` += `autoCaptions`, `metrics?`.

## 3. AI layer — comprehensive (`Adapters` protocol, Mock + Anthropic routers, backend)
- **NEW `generatePillars(brand:)`** — 4–6 niche-specific pillars, each with summary + the
  creator's angle + 3 example topics + rationale. Live Claude prompt + a niche-aware Mock (not static).
- **Real `analyzePage(handle:)`** — infers themes/pillars from handle+brand via the LLM (mock =
  derived from niche, never the static four).
- **Enrich `generateScripts`** — returns `title`+`summary`; prompt injects `pillar.angle`,
  voice sliders, banned words, the **media-corpus** summary, and current trends.
- **NEW `captions(for:)`** — timed burned-in caption lines for a clip.
- **NEW `interpretInsights(metrics:)`** — plain-English coaching from performance.
- **Backend** (`backend/main.py`): add `/v1/pillars`; parse `/v1/scripts` into structured items;
  scaffold `/v1/clips` (transcribe→caption→render→R2) and `/v1/publish` (with media); keep mock
  fallback + tests green.

## 4. Today — command center + insights
Giant numeral hero (streak or week index) → **growth card** (this-week reach/follows delta +
sparkline + best post + momentum line from `interpretInsights`) → directive + single CTA →
Next-up scheduled post → trend teaser.

## 5. Studio — progressive-disclosure script cards
Pillar cards show name + summary + example topics (tap → generate 3 scripts on that angle).
Script card = short **lowercase-serif title** + format/score + chevron → taps to **expand inline**
(summary + hook + meta) → "Open" → full `ScriptReaderView` → "Record" → teleprompter.

## 6. Calendar — rebuild
Week + month toggle, **clip thumbnails** on day cells. Tap a scheduled post → **editor sheet**
(time picker, platform chips, editable caption, **auto-captions toggle**, clip preview) → save /
reschedule / delete. "Post now" + scheduled publish via Ayrshare **with media URL** + caption;
auto-captions toggle wires to the caption-burn step.

## 7. Library — rebuild (3 tabs)
**Clips** (rendered, real thumbnails + AVPlayer playback) · **Footage** (filmed-but-undecided
takes; "make clips from this") · **Media** (bulk `PhotosPicker` import → personal corpus grid the
AI references). Persist recorded takes into Footage.

## 8. Recorder
Proportional teleprompter (`ScrollViewReader` + timer-driven), speed control + pause; persist the
take into Footage; trigger caption generation.

## 9. Tab bar + polish
Docked frosted bar + center Record FAB; fix gold-alias drift; tuned ShineSweep; verify build +
Maestro green; commit per phase.

## Execution order
0 Foundation (models, fonts→Playfair, Theme tokens/tracking, Components incl. LiquidGlass) →
1 AI layer → 2 Studio cards → 3 Today → 4 Library → 5 Calendar → 6 Recorder → 7 Tab bar/polish →
verify + commit each. Codable note: new non-optional fields reset the v1 snapshot once on upgrade
(app has `-reset` + 20s onboarding; API keys live outside the snapshot, so they survive).
