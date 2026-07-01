I have everything I need — ground truth verified against the actual files and Maestro flows. Here is the implementation plan.

---

# Marque → Max Redesign: SwiftUI Implementation Plan

**Verified against source.** Read before starting: `/Users/home/Marque/ios/Marque/DesignSystem/Theme.swift`, `/Users/home/Marque/ios/Marque/DesignSystem/Components.swift`, and `/Users/home/Marque/.maestro/*.yaml`.

**Two ground-truth corrections that reshape the whole job** (the brief was partly built on stale assumptions):
1. **The palette and sans font are already ported.** `Palette.textPrimary`/`ink` are already `#1C1A17` (warm), `textSecondary` `#555049`, `textTertiary` `#97928A`, `shadowWarm` `#2E2A20`, and `gold`/`goldDeep` are aliased to `accent` (blue `#2C6BED`). `Typeface.sans` already maps to `Matter-*`. **Do not re-derive any of this.** Inter survives only as dead Info.plist entries.
2. **The serif is a live bug, not a fallback.** `Typeface.display` calls `.custom("Fraunces-9ptBlack", size:)`. That PostScript name **resolves** — to the Black optical cut. Every serif title in Marque renders Black-weight *right now*. Verified: both the Marque-bundled and maxapp-source `Fraunces-Regular.ttf` are `Fraunces-9ptBlack` (typo subfamily "Black"), and maxapp's `Fraunces-SemiBold.ttf` shares the **identical** PS name `Fraunces-9ptBlack` — so it cannot even be addressed separately. **There is no clean 400 Fraunces on disk anywhere.** `PlayfairDisplay.ttf` *is* a correct variable 400 face (PS `PlayfairDisplay-Regular`, wght default 400) and is already bundled + in Info.plist.

---

## 1. Design-system foundation (do first)

### 1a. Fix the serif — the single load-bearing change

**Decision: revert the serif to Playfair now; treat "real Fraunces" as a separate, gated task.** Shipping Playfair immediately kills the live Black-weight bug with one line and zero risk; it already reads as an editorial serif and every `AppFont` serif role points through `Typeface.display`. Do **not** ship `Fraunces-9ptBlack` as the editorial face — Black at 40pt is not the Max DNA.

In `Theme.swift`, `Typeface.display`:
```swift
static func display(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
    .custom("PlayfairDisplay-Regular", size: size)   // was "Fraunces-9ptBlack" (Black cut — bug)
}
```
- Change the default weight arg from `.semibold` → `.regular`. Playfair is variable-weighted, but the editorial DNA wants ~400; the `.custom(name,size:)` call ignores `.weight()` on a static-named face anyway, so PS `PlayfairDisplay-Regular` gives you true 400. Leave `.weight()` off.
- Update the stale header comment (lines 3–4, 55–63) — it currently claims Inter + describes the Fraunces-Black rationale as intentional.
- **Optional real-Fraunces path (separate PR, do NOT block on it):** generate a genuine static Fraunces Regular@400 + SemiBold from the variable source (`fonttools varLib.instancer`), give each a **distinct** PS name (`Fraunces-Regular`, `Fraunces-SemiBold`), add both to the target + Info.plist UIAppFonts, verify names with `fc-scan`, then add a `Typeface.serif(_:_:)` helper and repoint only the serif `AppFont` roles. Until that lands, Playfair is the serif.

**Do not** add the maxapp `Fraunces-SemiBold.ttf` — it has the same PS name as the Regular and will not resolve distinctly. **Optional cleanup:** prune the 4 dead `Inter-*.ttf` entries from Info.plist UIAppFonts.

### 1b. Retune the serif type ramp (safe — role names stay stable)

Keep every `AppFont` enum name so call sites and ids are untouched. Only swap sizes/tracking. Apply `.tracking()` at the role or call site per Max's scale (SwiftUI `.tracking` = RN `letterSpacing`, 1:1 point-based):

| Role | Family | Size | Tracking |
|---|---|---|---|
| `displayXL` | Playfair | 40 | −1.0 |
| `serifL` | Playfair | 28 | −0.4 |
| `serifM` | Playfair | 22 | −0.2 |
| `displayM` | Playfair | 28 | −0.4 |
| `question` | **keep sans** Matter-Bold | 30→ leave, or 28 | −0.2 |
| `heroNumeral` | **keep sans** Matter-Bold 44 | — | (Track.hero −1.5) |

- `heroNumeral` is currently `Typeface.sans(44,.bold)`. **Leave it sans** unless/until a real Fraunces lands — a Playfair-Black-style numeral is not wanted, and a Playfair 400 numeral at 48 is thin. The brief's "Fraunces 48/−2.0" numeral is gated on 1a's optional path.
- `micro` stays `Matter-SemiBold 11`; keep applying `.tracking(Track.label /* 1.4 */)` + `.textCase(.uppercase)` at call sites (never pre-uppercase strings — keeps `accessibilityLabel` readable).

### 1c. Palette — add at most 2 tokens, change 1 value

Almost everything is already correct. Net edits in `Palette`:
- **`textSoft`** (new): `Color(hex: 0x5C574E)` — warm body/why paragraphs inside cards. (It's near-identical to existing `textSecondary #555049`; acceptable to just reuse `textSecondary` and skip this to avoid token sprawl — implementer's call, but prefer reuse.)
- **`accentDeep`** (new, optional): `Color(hex: 0x1F4FB0)` — only add if a tinted-pill text use actually lands (provenance pill). Skip otherwise.
- **Canvas:** leave `canvas = #F1F1EF` as-is OR bump to `#FCFBF9` — either is defensible; `#F1F1EF` is the current Stoic ground. If you change it, change the one token and verify `surfaceRaised #FFFFFF` cards still pop. **Do not** touch `textPrimary`/`ink`/`gold`/`shadowWarm` — already on target.
- **`hairline`** stays `#000000@0.08` (used app-wide on fields/cards). For warm **zone dividers** use `Palette.textPrimary.opacity(0.12)` inline — do not repoint the shared `hairline` token.

### 1d. Primitive tweaks in `Components.swift` / `Theme.swift`

- **`marqueCard` shadow** (Theme.swift line 151): `0.06 / radius 16 / y 6` → `0.07 / radius 18 / y 8`. Keep `shadowWarm`, keep `style: .continuous`. Signature already exposes `radius:` — pass the prominence ladder at call sites (quiet 18–20, rows 22, hero 24, close-out 26). No signature change.
- **`marqueHairline`** — add one inline helper for zone breaks (do **not** over-abstract; inline `Rectangle` is fine where used once):
  ```swift
  struct MarqueHairline: View {
      var body: some View { Rectangle().fill(Palette.textPrimary.opacity(0.12)).frame(height: 0.5) }
  }
  ```
  Apply `.padding(.top,28).padding(.bottom,18)` at zone-break call sites (not inside the view). No `Divider()` between timeline rows — separate rows with ~9pt padding only.
- **`SectionLabel` bar** (line 174): `frame(width:3, height:11)` → `height:14`, `cornerRadius: 1.5`, `HStack(spacing: 8)` (was 6). **Keep `accent` OPTIONAL / default `nil`.** SectionLabel is used ~22 places (Studio, Library, etc.); defaulting the bar ON would add a blue tick to every existing call site. Opt in per call site. Do **not** bake `.padding(.bottom,16)` into the component — apply the label→body gap at call sites.
- **`Chip`** (line 236): add an `onDark: Bool = false` param AND a `tint: Color? = nil` tiny/provenance variant — both **additive, defaulted**, so existing `Chip(text:)` / `Chip(text:selected:)` call sites keep compiling.
  - `onDark` unselected (over camera): fill `white.opacity(0.12)`, label `white.opacity(0.6)`, no paper shadow. Selected keeps ink-on-white.
  - tiny/provenance: `Capsule().fill(Palette.accent.opacity(0.14))`, `Matter-Medium 10` label in `accentDeep`/`#1F4FB0`, pad H7/V2. Only build/use it if a real pill survives (see Library/Today notes).
  - Add `.accessibilityAddTraits(.isSelected)` when `selected`.
- **`SectionTitle`** — keep it; still used by Insights/Coach/ScriptReader. Do not delete.
- **`ScreenTitle`** — do **not** change its shared defaults (size 30, lowercase). Used by Coach/Calendar/Studio/Library; raising size or case globally causes Maestro `Library`/`Settings` ambiguity. Build editorial headers **inline per screen** instead.

---

## 2. Per-screen redesign

Ordered by impact. **SAFE** = pure visual. **CARE** = layout/logic/id-adjacent.

### Today (`TodayView`)
1. **SAFE** — Header: inline VStack — date kicker (`micro` + `Track.label` uppercase `textTertiary`, bottom 6) over serif title via `ScreenTitle(size:40)` or inline Playfair 40 `.tracking(-1)`. **Keep `.navigationTitle("Today")` inline** — 4 flows `assertVisible: "Today"`. Preserve `today.profile` / `today.settings` on the top bar.
2. **SAFE** — Momentum stat card: one serif beat only (numeral stays sans until real Fraunces). Demote empty-state "Post your first clip" `serifL`→`headline` Matter. Keep Sparkline, SectionLabel eyebrow, +N chip.
3. **CARE** — "Your move" command card = dominant NEXT-UP hero: `marqueCard(radius:24, padding:20)`, keep `ProgressRing` + "Your move" SectionLabel + `PrimaryButton(today.cta)`. Title `displayM`→`serifL`, add a why-line subtitle. **Do NOT rename the "Your move" eyebrow to "Next up"** — collides with the schedule row below.
4. **CARE (highest id risk)** — Next-up / Learning / Trend → hairline-separated quiet-row stack. **The literal substring "Next up" MUST stay visible in the `today.nextUp` row** — `flow-extras` does `scrollUntilVisible` on text `"Next up"` (Maestro matches text, not id). Keep a "Next up" label/title in that row. Demote LearningMeter/trend to bare rows. Provenance pill only if >1 platform (reuse tiny Chip, don't fork).
5. **SAFE** — Spacing/restraint pass: 22 horizontal, ~110 bottom scroll pad, zone ladder 12<16<22–26, accent strictly functional.

### Studio + ScriptReader (`StudioView`, `ScriptReaderView`)
1. **SAFE** — Studio header: inline kicker + Playfair wordmark, **keep literal "Studio"** (`tapOn: "Studio"` in 5 flows). Preserve `studio.close`.
2. **CARE** — NEXT-UP hero above pillar carousel: new `MarqueHero` driven by top script; route via existing `.navigationDestination(for: Script.self)`. **No-op when `store.scripts.isEmpty`.** Give it a NEW unique id or none — must not collide with `studio.scriptRow`/`studio.openScript`.
3. **CARE** — Pillar carousel: 3pt leading provenance rail clipped to 20pt `.continuous` corner, sentence-case title (drop `.textCase(.lowercase)`). **Keep "Your pillars" SectionLabel text** (`assertVisible` in flow-full + flow-studio-exit) and **`studio.pillar.<name>`** id (flow taps `studio.pillar.Myth-busting`).
4. **CARE** — "Ready to record" zone: keep literal **"Ready to record"** (asserted), keep `studio.scriptRow` + `studio.openScript`. ScriptCard title serif+lowercase → `headline` Matter warm ink. Why-line "From {pillar}" — **only add "· aimed at {audience}" if `Script` has an audience field; verify first, else drop the clause.**
5. **SAFE** — Empty/generating: `EmptyStateView` copy free to change (no flow asserts it); keep inline `ProgressView().tint(Palette.accent)` — no full-screen spinner. Success haptic on batch complete.
6. **SAFE** — ScriptReader hook = single serif hero (`serifL`/`displayM`); SectionTitle "Hook · tap to explore" → `SectionLabel("Hook", accent:)` (no flow asserts that string). Keep `script.hookButton`. `goldDeep`→`accent` recolor is a visual no-op (already aliased).
7. **SAFE** — Body/format/shot-plan: warm body type + `.lineSpacing(7)`; replace `Divider().background(Palette.hairline)` with `MarqueHairline`; shot-plan dot `Palette.gold`→`textTertiary`.
8. **CARE** — Refine + Record: keep literal **"Refine"** (asserted), keep `script.steer` + `script.record`. Bottom bar already `.safeAreaInset` + `.ultraThinMaterial`; add a 0.5pt `white@0.85` top rim. Record via shared `PrimaryButton`.
9. **CARE** — Sheets: keep `pick.talking_head` id, `hooklab.pickHook` id, and literal **"Pick your hook"** + **"Pick a clip"** (asserted). Restyle to warm paper + Playfair titles; don't reorder the style list (AHA flow depends on `pick.talking_head` resolving).

### Calendar (`CalendarView`, `DayRow`, `PostRow`, `MonthGrid`, sheets)
1. **SAFE** — `.background(Palette.surface)` → `Palette.canvas` in CalendarView, `SchedulePickerSheet`, `PostEditorSheet`. Use existing `canvas` token.
2. **SAFE** — Header: inline context kicker + `SectionLabel("View", accent:)` above the Picker; **keep `ScreenTitle(text:"Calendar")` unchanged** (lowercase 30). Keep `calendar.modeToggle`. Helper copy `textTertiary`→`textSecondary` + `.lineSpacing(4)`.
3. **SAFE** — DayRow: 3pt leading accent rail when `!posts.isEmpty`; day name → `serifM`. Keep TODAY Capsule `.accessibilityHidden(true)`. Leave `marqueCard` at `Radius.xl` (22).
4. **CARE** — Empty-day: top-aligned icon + title + why-line. **Title MUST stay literally `hasReady ? "Schedule a clip" : "Nothing scheduled"`** (`visible: "Schedule a clip"` asserted). Keep `calendar.addClip` + `.disabled(!hasReady)`.
5. **CARE** — PostRow → timeline idiom: 52pt time column + thumbnail + title + meta + status pill using `Palette.positive`/`accent` opacity(0.14) (no hardcoded hex). Keep outer Button `calendar.post` + contextMenu.
6. **SAFE** — "Add another" stays bare accent HStack (`calendar.addClip`, shared id fine). Tighten week VStack spacing to 12.
7. **SAFE** — MonthGrid: weekday initials `.tracking(Track.label)`; today cell `surfaceRaised`→`accentMuted`. Keep `onPickDay`.

### Library (`LibraryView`, `ClipCell`, `FootageCell`, `UnderlineTabBar`)
1. **SAFE (app-wide theme)** — inherits the 1c/1d foundation warmth automatically. Verify Library reads on the ground.
2. **CARE** — Inline Library header (do **not** touch `ScreenTitle` globally — would create two "Library" strings and break `tapOn: "Library"`). Build inline: micro count kicker + inline Playfair "Library" 40. Static kicker or `"\(store.clips.count) CLIPS"` both safe.
3. **SAFE** — Keep `UnderlineTabBar` (`tab.Clips/Footage/Media` ids preserved; no flow taps them by text). Tighten spacing under the header.
4. **CARE** — `ClipCell`: thumbnail row + why-line (`FormatTag · {seconds}s` + caption glyph) + status-tinted 3pt leading rail. **Drop the inline status pill** — redundant with the status-grouped section eyebrow the clip already sits under. Keep `library.clip` + contextMenu.
5. **SAFE** — Add `ClipStatus.tint` (local computed var) → pass to `SectionLabel(accent:)` on each status section + the rail. `SectionLabel` accent param already exists.
6. **SAFE** — Footage/Media intro copy: `.lineSpacing(7)`; FootageCell why-line parity with ClipCell. CTA weights already correct (Media ink-fill, Footage hairline) — no change. Keep `library.importMedia`, `library.createFirst`.
7. **SAFE** — Skip the tiny/accent Chip variant here (its only consumer, the inline pill, is dropped). Keep `ClipStatus.tint`.

### Coach + Insights (`CoachView`, `InsightsView`)
1. **SAFE** — Inherit foundation. `heroNumeral` stays sans (see 1b).
2. **SAFE** — Coach header: inline kicker + large serif "Coach". Keep trailing `coach.insights` Button + `.accessibilityLabel("Insights")` (asserted).
3. **SAFE** — Each Trend → why-lined row-card with 3pt accent rail. Keep `coach.writeScript` Button + `store.generateFromTrend`. Fields `t.title/t.why/t.formatId` verified.
4. **SAFE** — `SectionTitle`→`SectionLabel(accent:)` in both screens (bar now 3×14). Apply the 16pt label→body gap at call sites, not in the component. Keep **"Trending in your niche"** (`visible:` asserted) and **"Insights"** (asserted).
5. **SAFE** — "What worked" cards: three-tier ramp — `t.headline` `serifM`, `t.detail` `textSoft` + `.lineSpacing(5)`, caption `textTertiary`.
6. **SAFE** — One `MarqueHairline` zone divider between teardown and trend zones (`.top,28`/`.bottom,18`); drop the uniform gap there so spacing doesn't double.
7. **SAFE** — Insights headline metric (`totalViews`) as a hero-numeral moment on canvas (sans until real Fraunces; degrades safely).
8. **SAFE** — "Your format mix" bare list → quiet why-lined rows in one `marqueCard(radius:24)`.
9. **SAFE** — Coach trends spinner → 3-bar skeleton at `textPrimary.opacity(0.07)`. Insights info line → bare HStack (info.circle + `textSoft` callout). Do **not** globally restyle shared `EmptyStateView` — restyle at call sites.

### Record (`RecordView`)
1. **CARE** — topBar: replace the clear-xmark spacer hack (line 76) with a 3-column HStack. Close = 38pt `LiquidGlassFill(radius:19, corners:false)` + white xmark (keep same `Button{dismiss()}`, no id). Center kicker "TELEPROMPTER" `micro` white@0.7. Trailing format = accent-tint capsule (accent.opacity(0.14) + light-accent label) for on-dark legibility. Keep the ZStack + 0.45 camera scrim — **no paper canvas over live camera.**
2. **CARE** — speedControl: add the `onDark` Chip variant (from 1d) in **both** `.ready` and `.recording`; precede with `SectionLabel("PACE", accent:)`. Keep 0.6/1.0/1.5 values + Slow/Normal/Fast.
3. **CARE** — `.ready`: split instruction into serif "Read it once." (Playfair 28 `.regular` `.tracking(-0.4)` white@0.94) + Matter caption. **Keep `record.openSettings` + `record.upload`** on their same controls, keep labels textually intact.
4. **CARE (highest payoff)** — `.recorded` review: wrap in `LiquidGlass(radius:24)` (NOT `marqueCard` — rejected over media). SectionLabel "YOUR TAKE" + serif "Nice take." + why-line. Format picker → `onDark` Chip preserving selectedFormats set logic. Replace hand-rolled button with `PrimaryButton(...)` but **re-attach `record.makeClips` + `.disabled(selectedFormats.isEmpty)` + the 0.5 dim** to it. Demote Re-record keeping **`record.reRecord`**.
5. **CARE** — `.recording`: glass timer pill (`LiquidGlassFill(corners:false)`), one why-line, 52pt glass pause disc keeping **`record.pausePrompt`** + toggle. Keep **`record.capture`** on the record disc. Mic warning → `Palette.critical` caption row.
6. **SAFE** — `.making`: replace `ProgressView` with serif "Cutting your clips." + Matter caption + 2–3 shimmer skeleton bars at white@0.10. No behavior change.
7. **SAFE** — Teleprompter: hook `Typeface.display(28,.semibold)` → Playfair 28 `.regular` `.tracking(-0.4)`; body keep Matter 23, bump `.lineSpacing` 6→7; CTA keep accent. Preserve all EditField bindings + scroll math.

### Settings + Paywall + BrandProfile (`SettingsView`, `PaywallView`, `BrandProfileView`)
1. **CARE** — Settings: replace black "Upgrade to Pro" rectangle with a serif upgrade hero — `marqueCard(radius:24)`, SectionLabel "MARQUE PRO", Playfair `serifL` title, one body line, RadialGradient `accent.opacity(0.10–0.12)` bloom behind title (no plate). **Keep outer `Button{showPaywall=true}` + `settings.upgrade`** (tapped by final-shots/verify-shots). Whole card tappable.
2. **CARE** — Settings rows: rewrite `row(_:_:tint:)` — 42×42 tinted iconTile (radius 14 `.continuous`, fill `tint.opacity(0.05)`, stroke `tint.opacity(0.09)`, 20pt symbol) + title `headline` + optional `sub` (`caption`/`textSecondary`) + chevron. Add `sub` param defaulted nil. Group per-section in `marqueCard(radius:20)`, ~9pt row padding, no drawn dividers. **Keep `settings.deleteAccount`** with `tint: Palette.critical`; keep Restore=Button, Manage=Link wrappers.
3. **SAFE** — Notifications: keep Toggle + `settings.reminders` + `.tint(Palette.accent)`, seat in `marqueCard(radius:20)`. Inline hairlines between zones. Keep `.navigationTitle("Settings")` (asserted).
4. **CARE** — Paywall: SectionLabel "MARQUE PRO" → **keep exact "Film once.\nPost all week."** re-fonted to `displayXL` Playfair → subline `bodyL`. RadialGradient bloom. `proFeatures` → 4 icon rows in `marqueCard(radius:24)`. Keep `.navigationTitle("Marque Pro")` (asserted) + `paywall.subscribe`.
5. **SAFE** — Paywall CTA: keep `PrimaryButton("Go Pro", shine:true)` + `paywall.subscribe`. Recolor price/restore/links to warm neutrals, accent only on tappable links. bg → canvas.
6. **SAFE** — BrandProfile header: **keep exact "What Marque knows about you"** (asserted) in `displayM` Playfair + SectionLabel "BRAND GRAPH". bg → canvas.
7. **CARE** — BrandProfile fields: `field()` SectionTitle → bar-less `SectionLabel` + underline TextField. **Keep `profile.niche/whatYouDo/audience/knownFor`** (flow taps `profile.knownFor`). Voice Slider `.tint(accent)`. **"Never say" removable pills:** the shared `Chip` is text-only (no trailing xmark) — either add an optional trailing-glyph to Chip OR keep the current ad-hoc capsule and restyle. Pick one explicitly; do **not** claim `Chip` works as-is. Pillars → inline tinted icon rows.

---

## 3. Guardrails

### accessibilityIdentifiers — must survive verbatim (restyle in place, never fork the tappable control)
```
today.profile · today.settings · today.cta · today.nextUp
studio.close · studio.scriptRow · studio.openScript · studio.pillar.Myth-busting (and studio.pillar.<name>)
script.hookButton · script.steer · script.record · hooklab.pickHook · pick.talking_head
calendar.addClip · calendar.post · calendar.modeToggle · schedule.pickClip
library.clip · library.importMedia · library.createFirst · tab.Clips/Footage/Media
coach.insights · coach.writeScript
record.capture · record.makeClips · record.upload · record.reRecord · record.pausePrompt · record.openSettings
settings.upgrade · settings.deleteAccount · settings.reminders
paywall.subscribe
onboard.start · onboard.back · onboard.niche · onboard.whatYouDo · onboard.audience · onboard.knownFor · onboard.voiceInstead · onboard.finish
profile.niche · profile.whatYouDo · profile.audience · profile.knownFor
connect.tiktok · connect.handle · connect.link · connect.linked
celebration.dismiss
```

### Maestro-tapped/asserted STRINGS — Maestro matches on text, so the id alone won't save these; the literal must stay visible/tappable
- **assertVisible:** `Today` · `Studio` (also tapped) · `Your pillars` · `Ready to record` · `Refine` · `Shot plan` · `Pick your hook` · `Pick a clip` · `Marque` · `Marque Pro` · `Settings` · `Insights` · `Delete account` · `API keys` · `Caption` · `What Marque knows about you` · `Tell me about you` · `What are you here to do?` · `What do you want to be known for?` · `What kind of videos?` · `Connect your accounts`
- **visible / waitUntil:** `Schedule a clip` · `Trending in your niche` · `Library` · `Enter Marque`
- **tapOn:** `Today` · `Calendar` · `Coach` · `Library` · `Studio` · `Continue` · `Done` · `Skip for now` · `Grow my audience` · `Talking-head`
- **Two live traps:** (a) Today's `today.nextUp` row must keep the substring **"Next up"** (`scrollUntilVisible` matches text). (b) Onboarding goal card must keep `g.rawValue` ("Grow my audience") as the visible tappable label; onboarding top-region must stay **non-interactive** (flows dismiss keyboard by tapping `50%,12–15%`).

### Deliberately DO NOT (over-design)
- Do **not** ship `Fraunces-9ptBlack` as the editorial serif, and do **not** port maxapp's `Fraunces-SemiBold.ttf` (duplicate PS name). Playfair now; real static Fraunces only as a separate verified PR.
- Do **not** re-derive the palette/sans — already ported. No churning `ink`/`textPrimary`/`gold`/`shadowWarm`.
- Do **not** change `ScreenTitle` or `SectionTitle` shared defaults, or default `SectionLabel.accent` ON — all cause app-wide regressions / Maestro ambiguity. Opt in per call site; build editorial headers inline.
- Do **not** invent redundant tokens (`inkHairline`, `cardBorder`, a second `textSoft` if reusing `textSecondary`).
- Do **not** put `marqueCard`/paper over live camera (Record) — `LiquidGlass` only over media.
- Do **not** add gradients on content (only the RadialGradient hero blooms + tab/glass sheen), no nested cards, no heavy rules — hairlines + whitespace only. One accent color (blue), functional-only. One dominant action per screen; loading = skeletons, never body spinners.

**Files touched:** `DesignSystem/Theme.swift` (1a–1d), `DesignSystem/Components.swift` (1d + Chip/SectionLabel/MarqueHairline/MarqueHero), then the per-screen views named in §2. Font decision is the gate — land 1a first, screenshot every serif screen, then proceed.