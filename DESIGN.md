# DESIGN.md — Yunicorn visual system (Stoic-derived, black & white)

Extracted 2026-09-22 from **stoic.** for iOS via Mobbin (~90 screens across onboarding, paywall,
home, editor, library, journey, settings, empty states, sheets, light and dark). Measurements come
from 3x captures (1180px = 393pt, so px ÷ 3 = pt) and are rounded to the nearest sensible token;
colors marked *sampled* were read from the capture pixels, the rest are by eye.

**Scope rule:** this document describes *structure and style*. Stoic's logo, bird mascot,
illustrations, custom icons, and copy are not ours and are not reproduced. Yunicorn keeps its clay
unicorn, writes its own copy, and uses SF Symbols / its own line glyphs.

**Owner mandate:** everything is black and white. Stoic itself uses one accent (a purple "AI"
gradient on paywall and AI features). We drop it entirely — the only "colors" are pure black, pure
white, and a grayscale ramp. Emphasis is carried by inversion (black card on light page, white
button on black card), weight, size, and tracking. Never by hue.

---

## 1. Colors

Two palettes, mirror images of each other. Every token has a light and dark value; never hardcode.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `canvas` | `#F1F1EF` | `#000000` | page background. Stoic samples `#F2F2F4`; we use the brand off-white because the mascot videos (MP4, no alpha) are rendered on it. Never pure white: cards need contrast against it |
| `surface` | `#FFFFFF` | `#111111` | cards, grouped list rows, sheets' content cards |
| `surfaceSunken` | `#EDEDEF` | `#1C1C1C` | search fields, unselected plan tiles, stat tiles, ghost pills, disabled fills |
| `ink` | `#000000` | `#141414` | flat inverted surfaces: promo strips, selected plan tiles, tab center circle (sampled pure black) |
| `heroGradientStart` | `#1F1C1F` | `#1E1E1E` | hero card TOP-LEFT (sampled): a warm near-black, lighter than `ink` |
| `heroGradientEnd` | `#121012` | `#141414` | hero card BOTTOM-RIGHT (sampled): the card darkens toward the bottom-right, not the reverse |
| `textPrimary` | `#1A1A1A` | `#FFFFFF` | titles, body |
| `textSecondary` | `#8A8A8A` | `#8E8E8E` | subtitles, captions, eyebrow labels, unselected tab labels |
| `textTertiary` | `#B8B8B8` | `#5C5C5C` | placeholders, oversized muted display text, disabled |
| `onInk` | `#FFFFFF` | `#FFFFFF` | text on `ink` surfaces |
| `onInkSecondary` | `#9A9A9A` | `#9A9A9A` | eyebrow/subtitle on `ink` surfaces |
| `hairline` | `#DCDCDC` | `#262626` | row separators (sampled), outline pills, selected-day cell border, outline cards |
| `scrim` | `rgba(0,0,0,0.45)` | `rgba(0,0,0,0.65)` | behind sheets |
| `buttonPrimary` | `#0A0A0A` | `#E8E8E8` | primary capsule fill |
| `onButtonPrimary` | `#FFFFFF` | `#000000` | primary capsule label |
| `toggleOn` | `#0A0A0A` | `#FFFFFF` | switch track when on (knob is the opposite) |

Rules:
- **No accent color exists.** Selection = inversion (a selected pill is `ink` with `onInk` text; an
  unselected pill is `surface` with `textPrimary`). Progress = filled vs unfilled black dashes.
- Dark mode is *true black* (`#000000`) with near-black cards, not iOS gray. Outline (empty-state)
  cards in dark mode use a 1pt `hairline` border instead of a fill (see the "All set for the
  morning" card, [home dark](https://mobbin.com/screens/2bc954b2-f338-46a0-94cf-2c7bb4bd6da0)).
- Semantic states stay monochrome: success = black check glyph, warning = black exclamation glyph,
  destructive = black text + trash glyph and a confirm dialog. System-red is reserved for native
  iOS alerts only.
- Photos/video thumbnails are the only full-color elements on screen.

Contrast (WCAG): `textPrimary` on `canvas` = 15.4:1, `textSecondary` on `surface` = 3.5:1 (large
text only; body text must use `textPrimary`), `onInkSecondary` on `ink` = 5.1:1, dark
`textSecondary` on `#121212` = 6.2:1. Never set body copy in `textTertiary`.

---

## 2. Typography

**Family.** Stoic sets everything in one geometric grotesk (single-story `g`, horizontal terminals,
generous x-height) with no serif anywhere. Yunicorn uses its already-bundled **Matter** as the
equivalent — it has the same skeleton. Fraunces (serif) is retired from every screen.

**Signature.** Section and page titles are **lowercase and end with a period**: "today.", "your
profile.", "breathe.", "weekly themes.". Sentence case elsewhere. Never Title Case.

| Token | Size / weight / leading | Tracking | Use |
|---|---|---|---|
| `display` | 34 / bold / 40 | -0.5 | page titles on pushed screens ("breathe.", "trends.") |
| `title1` | 26 / bold / 32 | -0.3 | sheet titles ("alternate."), editor prompts, onboarding questions |
| `title2` | 22 / bold / 28 | -0.2 | home greeting, card titles ("on glowing reviews."), promo titles |
| `title3` | 20 / semibold / 26 | 0 | library tile titles, plan tile names |
| `headline` | 17 / semibold / 22 | 0 | button labels, selected row values ("Yearly"), tab labels when selected use `caption` bold |
| `body` | 17 / regular / 24 | 0 | list rows, card questions, paragraph text, inputs |
| `bodyLarge` | 20 / regular / 28 | 0 | journal/editor text, quotes |
| `callout` | 15 / regular / 20 | 0 | secondary copy inside cards, footer summaries |
| `caption` | 13 / regular / 18 | 0 | "Day 6 of 7", timestamps, week-strip weekday, tab labels |
| `eyebrow` | 12 / semibold / 16 | **+2.4** (0.2em) uppercase | section labels ("GET INSPIRED", "ACCOUNT", "BREATHING", "BEST VALUE") |
| `displayMuted` | 30 / bold / 36 | -0.3 | oversized `textTertiary` taglines on onboarding/interstitials |
| `stat` | 28 / bold / 32 | -0.3 | numbers in stat tiles ("7", "206") |

Rules:
- Eyebrow above a title is the standard card/sheet header: `eyebrow` (secondary) + 4pt + `title1`.
- Titles are left-aligned on pushed pages, **centered** on sheets, hero cards, and onboarding.
- Dynamic Type: scale with `relativeTo:` the nearest text style; clamp `display` at 40.

---

## 3. Spacing and margins

Base unit 4. Scale: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`.

| Token | pt | Where |
|---|---|---|
| `screenH` | 16 | horizontal page margin, everywhere (cards are 361pt wide on a 393pt phone) |
| `cardPad` | 20 | inside cards (24 on hero cards) |
| `rowPad` | 16 | horizontal inside grouped list rows |
| `stack` | 12 | between cards in a vertical list |
| `groupGap` | 4 | between adjacent grouped-list cards in the same section |
| `sectionGap` | 32 | between sections; 40 before a section eyebrow |
| `headerToContent` | 24 | title block → first card |
| `ctaBottom` | 24 | bottom CTA to safe-area bottom (on top of the home indicator inset) |
| `tabBarHeight` | 56 | + safe area; center button rises 8pt above the icon row |

Small phones (SE, 375×667): keep `screenH` 16; hero card height scales to 62% of width; the
tagline `displayMuted` drops to 26. Large phones: content max-width stays full-bleed (Stoic does
not center a column); hero card height caps at 300.

---

## 4. Corner radius, shadows, borders

| Token | pt | Applies to |
|---|---|---|
| `radiusHero` | 32 | hero/day cards, tall library tiles (2:3 aspect) |
| `radiusCard` | 24 | standard content cards, paywall container, quote cards, sheet top corners |
| `radiusTile` | 20 | plan tiles, photo thumbnails, stat tiles |
| `radiusGroup` | 16 | grouped list cards, promo strips, mood/insight cards |
| `radiusCell` | 8 | week-strip selected day, inline text-field cells |
| `radiusPill` | 999 | every button, chip, search field, toggle, streak counter |

Shadows: almost none. Cards separate from the page by tone (`surface` on `canvas`). One optional
whisper shadow for elevated white cards on light: `0 2 12 rgba(0,0,0,0.04)`. Dark mode: zero
shadows; use `hairline` borders for outline states. Sheets: no shadow, just the scrim.

Borders: 1pt `hairline` on outline pills ("Reflect", "More"), the selected week-day cell, empty-
state outline cards, and circular floating controls. Row separators: 1pt `hairline`, **inset 16pt
from the leading edge** (aligned with row text), full-bleed to the trailing edge.

---

## 5. Components

### Buttons
- **Primary capsule** — `buttonPrimary` fill, `onButtonPrimary` label in `headline` (20/bold on
  paywall CTA), height **56**, horizontal padding 40, min width 150, **content-sized and centered**,
  not full-width. Reference: "Next", "Try 7 days free", "Begin Breathing"
  ([paywall](https://mobbin.com/screens/ae3055d0-47c2-49c3-ace3-e0803a633f5d)).
- **Card capsule** — the same shape inside a card at height 44–48: `surface` fill on `ink` cards
  ("Begin"), `ink` fill on `surface` cards ("Reflect" filled variant).
- **Outline capsule** — `surface` fill, 1pt `hairline`, `textPrimary` label; secondary action next
  to a primary ("More" / "Begin Breathing" row, widths 1:2.6).
- **Ghost pill** — `surfaceSunken` fill, `textSecondary` label + glyph, height 40 ("Personalize").
- **Text link** — `body` in `textPrimary`, centered, no underline ("Restore Purchase", "Terms").
- **Circular control** — 48pt circle, 1pt `hairline` on `surface` (editor "+", "Aa") or `ink`
  filled with `onInk` glyph (editor "next" chevron, tab-bar center "+" at 52pt).
- **Icon button** — bare 24pt glyph, 44pt hit area (close ✕, back ‹, share, star).
- Pressed state: scale 0.97 + opacity 0.9, `Motion.quick`. Disabled: `surfaceSunken` fill,
  `textTertiary` label.

### Cards
- **Hero card** — diagonal gradient `heroGradientStart` (top-left) → `heroGradientEnd` (bottom-right), `radiusHero`, height ~290,
  content centered: `eyebrow`-style two-line label in `onInkSecondary` → `title2` in `onInk` →
  card capsule CTA. One per screen, always first.
  ([home](https://mobbin.com/screens/839dd8c6-fd52-486a-a599-39f5242de9ba))
- **Content card** — `surface`, `radiusCard`, `cardPad`, centered text stack: `title2` → `caption`
  → `body` → outline capsule. Cards in a horizontal carousel are 361pt wide with the next card
  peeking 16pt.
- **Outline / empty card** — `hairline` border, no fill, centered `textTertiary` title and
  optional pill chips ("All set for the morning").
- **Promo strip** — `ink`, `radiusGroup`, left-aligned `title2` + `body` in `onInk`, decorative
  glyph at 30% opacity bottom-right.
- **Library tile** — 2-column grid, gap 16, aspect 2:3, `radiusHero`, alternating `surface` /
  `ink`; centered `title3` + `callout`, chevron bottom-right.
  ([library](https://mobbin.com/screens/d752a93e-8e88-4a86-82e2-88f7db2e8881))
- **Stat tile** — 2×2 grid, `surfaceSunken`, `radiusTile`, `stat` number + `caption` label.
- **Plan tile** — 2×2 grid inside a `surface` container (`radiusCard`, pad 8); tile `radiusTile`;
  unselected `surfaceSunken`, selected `ink` with a check glyph; "BEST VALUE" eyebrow.

### List rows
- Grouped in a `surface` card, `radiusGroup`, rows 52pt, `rowPad` 16, `body` label leading,
  trailing value in `headline` or chevron in `textPrimary`; 1pt inset separator. Section
  `eyebrow` sits 8pt above the card at x = 32 (aligned with row text).
  ([settings](https://mobbin.com/screens/47526be7-0680-47fe-8c36-6221a9478ffe))
- **Toggle row** — same row with a switch; switch is `toggleOn` track / opposite-tone knob.
- **Checklist row** — label + circular check (outline when off, `ink` fill when on); used for
  filters and "preparing" loaders.
- **Timeline row** — eyebrow + `headline` title, timestamp trailing in `caption`, glyph leading;
  body preview `body` in `textSecondary` (Journey).

### Tab bar
- 5 slots, `canvas` background, no top border, no blur. Outline glyph 24pt + `caption` label;
  selected = filled glyph + bold label in `textPrimary`, unselected `textSecondary`. Center slot is
  a 52pt `ink` circle with a 24pt "+" in `onInk`, raised 8pt, no label.

### Headers
- **Home** — leading utility pill (streak) / centered `title2` lowercase greeting with period /
  trailing avatar circle 32pt. Below it a 7-day strip: `caption` weekday + `body` number, selected
  day in a `radiusCell` outlined cell with bold number.
- **Pushed page** — bare back chevron at (16, top), then `display` title left-aligned, then
  `body` subtitle in `textSecondary`.
- **Sheet** — 36×5 drag indicator, centered `title1` lowercase-with-period, ✕ trailing at 24pt;
  optional eyebrow above the title; optional back chevron leading for nested sheets.
- **Flow / editor** — back chevron leading, **progress dashes** centered (n segments, 20×2, gap 8,
  `ink` filled / `hairline` unfilled), ✕ trailing; then `title1` prompt left-aligned.

### Inputs
- **Search** — capsule, `surfaceSunken`, height 52, magnifier glyph + `body` placeholder.
- **Text field (form)** — `surface` row 52pt, `radiusGroup`, `body`; label as eyebrow above.
- **Editor** — borderless `bodyLarge` on `canvas`, prompt as `title1`, helper questions in
  `textSecondary` bullets; floating circular controls pinned bottom (leading: add, text style;
  trailing: primary next).
- **Chips** — capsule 36pt, `surface` + `hairline`, glyph + `callout`; selected = `ink`.
- **Option buttons (onboarding)** — full-width capsules 56pt, `surface` fill, `body` centered;
  selected = `ink`/`onInk`. Stacked with 12pt gap.
- **Time / wheel pickers** — native, presented in a `surface` sheet card with a full-width "done"
  capsule.

### Sheets and modals
- Bottom sheet: `canvas` background, `radiusCard` top corners, drag indicator, content in
  `surface` cards, CTA row pinned at bottom (outline + primary capsules).
- Full-height sheet (settings/profile): same header, scrollable grouped lists.
- Action menu: `surface` card, `radiusGroup`, rows 44pt with trailing glyph, anchored above the
  triggering control ([editor menu](https://mobbin.com/screens/e41cc828-5696-4f0c-b7b5-52ef02756358)).
- Confirm dialog: native alert (the one place system tint may appear).

### Empty states
- Centered glyph 24pt `textSecondary` → `headline` title → `body` explanation in `textSecondary`
  → ghost pill action. Or an outline card with `textTertiary` title. Never an illustration we
  don't own. ([no results](https://mobbin.com/screens/9bc61201-33de-48c2-b8cd-a3c94612b499))

---

## 6. Layout patterns (top → bottom)

**Home / Today**
1. Header: utility pill · greeting `title2` · avatar
2. 7-day strip
3. Hero card (`ink`) — the one thing to do now
4. Secondary content cards (carousel, peeking)
5. `eyebrow` section label ("GET INSPIRED") + content cards
6. Ghost pill ("Personalize")
7. Tab bar

**Pushed list page** (library sub-pages, trends): back · `display` title · subtitle · optional
segmented control ("Weeks ▾" pill trailing) · grouped cards or 2-col grid.

**Sheet** (profile, filters, exercise detail): drag indicator · title row · eyebrow sections ·
grouped lists · pinned CTA row.

**Flow / editor**: back · progress dashes · ✕ · `title1` prompt · content · floating circular
controls. Each step is one question; "next" is the black circle bottom-right.

**Paywall**: laurel/badge line · `surface` container with segmented header + 2×2 plan tiles ·
centered text links · hairline · sticky footer: 3-line summary (`body`, plan + price + "Cancel
anytime") + primary capsule. Alternate "how your free trial works" page: vertical 4-step
timeline with circle glyphs, then the same footer.
([timeline](https://mobbin.com/screens/bdde362c-43a6-42f2-8ede-4e6500ae90fd))

**Onboarding**: ✕ or Skip top-right · centered illustration (ours) or nothing · `title1` question
centered · `body` subtitle `textSecondary` · stacked capsule options · primary capsule "Next"
centered at bottom. Interstitials: brand word + `displayMuted` tagline, left-aligned.
([onboarding flow](https://mobbin.com/flows/948a7285-9f2f-46d1-8d82-32da2d4ce828))

**Journey / history**: filter pills row (outline "Filter", "Days ▾") · `ink` insights card with
stat row · date header `title2` with chevron · timeline rows in `surface` cards.
([journey](https://mobbin.com/screens/ef8f5c26-2e4b-4baa-876e-20d1ab4c3f55))

---

## 7. Motion

Stoic is quiet: no bounces, no parallax, no color transitions (there is no color to transition).

| Token | Curve | Use |
|---|---|---|
| `Motion.quick` | spring(response 0.25, damping 0.9) | press scale, toggle, chip select, tab switch |
| `Motion.standard` | spring(response 0.4, damping 0.85) | card appear, sheet content, list insert |
| `Motion.page` | iOS default push / sheet | navigation; never custom transitions |

- Selection inverts tone in place (`Motion.quick`), no slide.
- Progress dashes fill left→right on step change (`Motion.standard`).
- Hero card CTA press: scale 0.97. Circular controls: scale 0.92.
- Loading: the checklist-row "preparing" pattern (rows check off) instead of spinners; inline
  content uses a `surfaceSunken` shimmer-free placeholder card.
- Haptics: `.impact(.light)` on primary CTA and tab center button, `.selection` on chips/options,
  `.success` when a step completes. Nothing on scroll.

---

## 8. Yunicorn-specific mappings (what stays ours)

- **Voice orb** — the Ink Drop stays exactly as is (it is already black ink on canvas; in dark
  mode it inverts to a white drop). It sits in the hero card slot on Home.
- **Mascot** — the clay unicorn replaces Stoic's bird wherever an illustration anchors a screen
  (onboarding interstitials, empty states, celebration).
- **Icons** — SF Symbols in `.regular` weight (outline) unselected, `.fill` selected; custom
  Settings glyphs already in `SettingsIcons.swift` are kept but stroked at 1.5pt in `textPrimary`.
- **Video thumbnails and reels** — full color, `radiusTile`, always inside a `surface` card so
  the color reads as content, not chrome.
- **Copy** — lowercase-period titles are adopted ("today.", "your library.", "settings."), but all
  wording is Yunicorn's own.


---

## 9. Implementation (code tokens and components)

Tokens live in `ios/Marque/DesignSystem/Theme.swift`; components in `DSComponents.swift` (new)
and `Components.swift` (legacy names re-skinned, same signatures). Deviations from §1 made
during implementation, with reasons:

| Decision | Why |
|---|---|
| `textSecondary` is `#6B6B6B` light (not Stoic's `#8A8A8A`) | the app uses it for body-size copy; `#6B6B6B` clears 4.5:1 on both `canvas` (4.8) and `surface` (5.3). Dark `#8E8E8E` clears 5.2+ on every dark surface |
| `ink` **inverts** in dark mode (`#0A0A0A` → `#ECECEC`), `onInk` with it | matches Stoic dark (light "Reflect" button, light center tab); 40 existing `background(ink)+foreground(onInk)` sites stay correct in both schemes |
| new `night` / `onNight` / `onNightSecondary` / `heroStart` / `heroEnd` | surfaces that stay dark in BOTH schemes (hero card, promo strip, camera chrome) |
| status colors (`positive`, `warning`, `critical`) = `textPrimary`; `scheduled` = `textSecondary` | black-and-white mandate; meaning moves to glyphs + wording |
| `Typeface.display` now returns Matter Bold | retires Fraunces at every legacy call site in one place |
| app root no longer forces `.light` | dark mode follows the system |

| DESIGN.md name | Code |
|---|---|
| display / title1 / title2 / title3 | `AppFont.pageTitle` / `.title1` / `.title2` / `.title3` |
| headline / body / bodyLarge / callout / caption | `AppFont.headline` / `.bodyText` / `.bodyLarge` / `.supporting` / `.caption` |
| eyebrow / displayMuted / stat | `AppFont.eyebrow` + `Track.eyebrow` (or `DSEyebrow`) / `.displayMuted` / `.stat` |
| radii | `Radius.hero` 32 / `.card` 24 / `.tile` 20 / `.group` 16 / `.cell` 8 |
| spacing | `Space.screenH` 16 / `.cardPad` 20 / `.rowPad` 16 / `.stack` 12 / `.groupGap` 4 / `.sectionGap` 32 |
| primary / outline / ghost / inverse capsule | `.buttonStyle(.dsPrimary / .dsOutline / .dsGhost / .dsInverse)` or `.ds(kind, height:, fullWidth:)`; `PrimaryButton` / `GhostButton` |
| text link | `.buttonStyle(.dsLink)` |
| circular / icon controls | `DSCircleButton(kind: .filled/.outline/.onNight)`, `DSIconButton` |
| cards | `.dsCard(.surface/.sunken/.outline)`, `DSHeroCard { }`, `DSPromoStrip`, `.marqueCard()` |
| grouped lists | `DSSection(eyebrow:) { DSRow / DSToggleRow / DSCheckRow ; DSRowDivider() }`, `DSGroup` |
| switch | `.toggleStyle(.ds)` |
| headers | `DSSheetHeader`, `DSFlowHeader`, `DSPageTitle`, `DSDragIndicator`, `DSProgressDashes` |
| data | `DSStatTile`, `DSWeekStrip`, `DSChecklistRow`, `DSTimelineRow` |
| inputs | `DSChip`, `DSOptionButton`, `.marqueField()` |
| feedback | `DSEmptyState`, `DSToast`, `DSStreakPill` |
| orb | `VoiceOrb(mode:level:size:onDark:)` — ink on light, white on dark or on a hero card |
