# 02 — Design System (Stoic-grounded)

> **Status:** Canonical. This document is the single source of truth for every visual, motion, haptic, and accessibility decision in **Marque**. If a screen contradicts a token here, the token wins.
>
> **Audience:** iOS engineers (Swift + SwiftUI, iOS 17+) and product designers.
>
> **Cross-references:** screen-level layouts live in `01-information-architecture.md` and `04-screens-create.md`; the camera/teleprompter behavior is specified in `05-screens-produce.md`; the score gauge data contract comes from `07-ai-system.md` (Virality Engine) and `08-format-virality.md` (render-recipes); subscription/paywall surfaces follow `11-monetization.md`. This document defines *how those things look and feel*, never *what they do*.

---

## 0. Design philosophy (the non-negotiables)

Marque is modeled on the calm, editorial restraint of a Stoic journaling app. The design system exists to enforce one feeling: **quiet competence.** A creator opens Marque overwhelmed; every screen should lower their heart rate, not raise it.

Five doctrines bind every token and component below:

1. **One idea per screen.** The Today home screen shows **exactly one directive at a time** + a small gold streak glyph + one trend line. Nothing else. Every other feature is one layer deep, surfaced contextually (bottom sheet, nested reader), or lives in its own calm screen. **Never bolt features onto Today.** See `01-information-architecture.md` for the layer map.
2. **Never pure white, never pure black.** Light surfaces are warm cream `#F4F1EA`; dark surfaces are near-black `#0E0E10`. Pure `#FFFFFF` / `#000000` are *banned* as surface or text values.
3. **Gold is a whisper, not a shout.** The single warm gold accent `#C9A227` is used **sparingly** — glyphs, large display flourishes, active-state hairlines, the streak mark. It is **never** used for body text or small interactive labels (it fails AA contrast for text — see §6.2).
4. **Slow, eased "breathing" motion.** No bounce, no snap, no spring overshoot that reads as playful. Transitions are 250–500ms, eased. The only looping motion is a 2.4s gold-glyph breath, and it is disabled under Reduce Motion.
5. **Semantic everything.** View code never references `cream`, `gold`, or a hex literal. It references roles: `theme.colors.textPrimary`, `theme.colors.surfaceRaised`, `theme.colors.accent`. This is what makes light/dark a token swap and a future rebrand a one-file change — the same "adapters hide every vendor" discipline used across Marque's stack ([semantic color naming](https://www.magnuskahr.dk/posts/2025/06/swiftui-design-system-considerations-semantic-colors/), [production-scale design tokens](https://dev.to/sebastienlato/swiftui-design-tokens-theming-system-production-scale-b16)).

---

## 1. Token architecture (SwiftUI implementation spine)

### 1.1 The `Theme` and the Environment

Marque uses the **Environment-driven token approach** — the locked 2025 SwiftUI consensus — *not* singletons and *not* initializer injection. A single `Theme` value is published into the SwiftUI Environment; any view reads it with `@Environment(\.theme)`, and SwiftUI auto-re-renders the subtree when the theme changes (e.g. light↔dark) with zero manual observation ([app-wide theming in SwiftUI](https://www.sagarunagar.com/blog/app-wide-theming-swiftui)).

```swift
// Theme.swift — the root token container.
struct Theme: Sendable {
    let colors: ColorTokens
    let typography: TypographyTokens
    let spacing: SpacingTokens
    let radii: RadiusTokens
    let elevation: ElevationTokens
    let motion: MotionTokens
    // Haptics are stateless (see §5) and live in a HapticTokens namespace.

    /// Resolve the correct token set for the active color scheme.
    static func resolve(for scheme: ColorScheme) -> Theme {
        Theme(
            colors: scheme == .dark ? .dark : .light,
            typography: .standard,
            spacing: .standard,
            radii: .standard,
            elevation: scheme == .dark ? .dark : .light,
            motion: .standard
        )
    }
}

// EnvironmentKey via the iOS 17 @Entry macro shorthand.
extension EnvironmentValues {
    @Entry var theme: Theme = .resolve(for: .light)
}
```

```swift
// MarqueApp.swift — inject once at the root, re-resolve on scheme change.
@main
struct MarqueApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                .modifier(ThemeProvider())
        }
    }
}

struct ThemeProvider: ViewModifier {
    @Environment(\.colorScheme) private var scheme   // honors the OS system setting
    func body(content: Content) -> some View {
        content.environment(\.theme, .resolve(for: scheme))
    }
}
```

**Rules:**

- **Do not set `.preferredColorScheme`.** Marque follows the device system setting; `#0E0E10` dark and `#F4F1EA` light are two resolved token sets, never a manual toggle. (A user-facing appearance override, if ever added, is recorded as an Open Question.)
- **Carry colors as Swift tokens *and* mirror them in the Asset Catalog.** The system resolves Asset-Catalog color sets for free, but the Swift mirror keeps SwiftUI previews and snapshot tests deterministic.
- **Modifiers only bundle *multiple* tokens.** For a single property, use native APIs (`.foregroundStyle(theme.colors.textPrimary)`), never a `.dsForeground()` wrapper. Reserve a custom `ViewModifier` for grouping color + type + padding + radius + haptic into one role — e.g. `.marqueButton(.primary)` (§7) ([when to use modifiers vs native ShapeStyle](https://dev.to/sebastienlato/swiftui-design-tokens-theming-system-production-scale-b16), [building a design system with SwiftUI](https://www.freecodecamp.org/news/how-to-build-design-system-with-swiftui/)).

### 1.2 Token namespaces at a glance

| Namespace | Accessor | Defines | Section |
|---|---|---|---|
| `colors` | `theme.colors.*` | Semantic color roles, light + dark | §2 |
| `typography` | `theme.typography.*` | Roles → `Font` with Dynamic Type scaling | §3 |
| `spacing` | `theme.spacing.*` | 4pt-based spacing scale | §4.1 |
| `radii` | `theme.radii.*` | Corner radii | §4.2 |
| `elevation` | `theme.elevation.*` | Shadow recipes | §4.3 |
| `motion` | `theme.motion.*` | `Animation` curve + duration tokens | §5 |
| `haptics` | `Haptics.*` (constants) | `SensoryFeedback` mappings | §5.4 |

---

## 2. Color tokens

### 2.1 Primitive palette (never used directly in views)

These are the raw values. **View code must never reference these names** — they exist only to compose semantic tokens (§2.2).

| Primitive | Hex (light ref) | Notes |
|---|---|---|
| `cream` | `#F4F1EA` | Warm paper base, light surface |
| `creamRaised` | `#FBF9F4` | Slightly lifted card surface, light |
| `creamSunken` | `#ECE7DC` | Wells / grouped backgrounds, light |
| `ink` | `#171512` | Warm near-black text on cream |
| `inkSoft` | `#5A554C` | Secondary text on cream |
| `inkFaint` | `#8C867A` | Tertiary / placeholder on cream |
| `night` | `#0E0E10` | Near-black base, dark surface |
| `nightRaised` | `#18181B` | Lifted card surface, dark |
| `nightSunken` | `#0A0A0C` | Wells, dark |
| `bone` | `#EDEBE5` | Warm off-white text on night |
| `boneSoft` | `#A8A39A` | Secondary text on night |
| `boneFaint` | `#6E6A62` | Tertiary text on night |
| `gold` | `#C9A227` | The single warm accent |
| `goldDim` | `#9C7E1E` | Gold for hairlines / pressed |
| `hairlineLight` | `#00000014` | ~8% ink separator on cream |
| `hairlineDark` | `#FFFFFF1A` | ~10% bone separator on night |
| `success` | `#3F7A52` | Calm forest, never neon |
| `warning` | `#B07A22` | Amber, distinct from gold |
| `danger` | `#A33A2E` | Muted brick red |

### 2.2 Semantic color roles (the only colors views may use)

Both columns are resolved automatically by `Theme.resolve(for:)`. Reference these and nothing else.

| Role token | Light value | Dark value | Usage |
|---|---|---|---|
| `colors.surface` | `cream` | `night` | App background, Today canvas |
| `colors.surfaceRaised` | `creamRaised` | `nightRaised` | Cards, sheets, format cards |
| `colors.surfaceSunken` | `creamSunken` | `nightSunken` | Grouped wells, text-field troughs |
| `colors.textPrimary` | `ink` | `bone` | Titles, body, primary labels |
| `colors.textSecondary` | `inkSoft` | `boneSoft` | Subtitles, metadata, captions |
| `colors.textTertiary` | `inkFaint` | `boneFaint` | Placeholders, disabled labels |
| `colors.accent` | `gold` | `gold` | Glyphs, active hairlines, streak mark — **non-text** |
| `colors.accentPressed` | `goldDim` | `goldDim` | Pressed/active state of accent elements |
| `colors.onAccent` | `ink` | `ink` | Label on a gold fill (verified ≥4.5:1, §6.2) |
| `colors.separator` | `hairlineLight` | `hairlineDark` | 0.5–1pt hairlines |
| `colors.success` | `success` | `#5C9E72` | Publish confirmed, streak kept |
| `colors.warning` | `warning` | `#D29A3C` | Schedule conflict, soft alert |
| `colors.danger` | `danger` | `#C75A4C` | Publish failed, destructive |
| `colors.scrim` | `#00000059` | `#000000A6` | Sheet/teleprompter dimming |
| `colors.teleprompterText` | `#FFFFFF` | `#FFFFFF` | **Exception:** pure-white over camera scrim for legibility (§7.10) |
| `colors.teleprompterScrim` | `#000000A6` | `#000000A6` | Reading band behind teleprompter |

> **Teleprompter exception is deliberate.** The teleprompter renders over an arbitrary live-camera background, so it carries its *own* readable token set (white text on a dark scrim) independent of brand cream. This is the one place pure white is allowed, and only over a guaranteed dark scrim. See §7.10.

### 2.3 `ColorTokens` shape

```swift
struct ColorTokens: Sendable {
    let surface, surfaceRaised, surfaceSunken: Color
    let textPrimary, textSecondary, textTertiary: Color
    let accent, accentPressed, onAccent: Color
    let separator: Color
    let success, warning, danger: Color
    let scrim: Color
    let teleprompterText, teleprompterScrim: Color

    static let light = ColorTokens(/* cream-based */ …)
    static let dark  = ColorTokens(/* night-based */ …)
}
```

Color tokens conform to `ShapeStyle` usage directly (`Color` already is a `ShapeStyle`), so views write `.foregroundStyle(theme.colors.textPrimary)` and `.background(theme.colors.surfaceRaised)` — no wrapper modifiers.

---

## 3. Typography

### 3.1 Families

| Use | Family | Weights shipped | License |
|---|---|---|---|
| **Display / titles** | **Playfair Display** (high-contrast serif) | Regular, Medium, SemiBold | SIL OFL 1.1 — free to bundle & ship commercially |
| **Body / UI** | **Inter** (clean grotesque) | Regular, Medium, SemiBold | SIL OFL 1.1 — free to bundle & ship commercially |

**Licensing is cleared.** Both faces are SIL OFL 1.1, so they may be bundled, embedded, and shipped commercially with no fee. The two obligations: (1) include the copyright + license notice + the OFL text in the app (an acknowledgements/licenses screen — see §3.6), and (2) do **not** rename an embedded font to an OFL *Reserved Font Name*. Register all faces via `UIAppFonts` in `Info.plist` ([Playfair Display OFL on Google Fonts](https://fonts.google.com/specimen/Playfair+Display/license), [how to use OFL fonts](https://openfontlicense.org/how-to-use-ofl-fonts/), [Apple font-license forum thread](https://developer.apple.com/forums/thread/720890)).

> Tiempos / Söhne (the commercial faces the brief references as alternates) are Klim Type Foundry licenses and would require a paid commercial license. They are **not** shipped unless purchased — recorded as an Open Question.

### 3.2 The non-scaling pitfall (must read)

A hard-coded `Font.custom("PlayfairDisplay-SemiBold", size: 34)` **does not scale with Dynamic Type** and will fail accessibility review. Every custom font in Marque is registered with a `relativeTo:` text style so it scales relative to the system style ([scaling custom fonts with Dynamic Type — Sarunw](https://sarunw.com/posts/swiftui-scale-custom-font-dynamic-type/), [Use Your Loaf — scaling custom fonts](https://useyourloaf.com/blog/scaling-custom-swiftui-fonts-with-dynamic-type/), [Hacking with Swift — Dynamic Type custom font](https://www.hackingwithswift.com/quick-start/swiftui/how-to-use-dynamic-type-with-a-custom-font)).

Likewise, any non-text dimension that must grow with type (gauge stroke, teleprompter line spacing, icon size, certain paddings) uses **`@ScaledMetric(relativeTo:)`** so it scales in lockstep ([@ScaledMetric for Dynamic Type — avanderlee](https://www.avanderlee.com/swiftui/scaledmetric-dynamic-type-support/)).

### 3.3 Type scale

All sizes are base @ default Dynamic Type. **Serif = Playfair Display. Grotesque = Inter.** Line-height ≈ 1.25 for display, ≈ 1.45 for body.

| Role token | Family / weight | Size | `relativeTo:` | Line height | Tracking | Typical use |
|---|---|---|---|---|---|---|
| `display` | Playfair SemiBold | 40 | `.largeTitle` | 48 (1.20) | −0.5 | The single Today directive, onboarding prompts |
| `titleL` | Playfair SemiBold | 34 | `.largeTitle` | 42 (1.24) | −0.4 | Screen titles |
| `titleM` | Playfair Medium | 28 | `.title` | 36 (1.29) | −0.2 | Section headers |
| `titleS` | Playfair Medium | 22 | `.title2` | 30 (1.36) | 0 | Card titles, sheet titles |
| `body` | Inter Regular | 17 | `.body` | 25 (1.47) | 0 | Primary reading text, scripts |
| `bodyEmph` | Inter SemiBold | 17 | `.body` | 25 (1.47) | 0 | Emphasis within body |
| `callout` | Inter Regular | 16 | `.callout` | 23 (1.44) | 0 | Secondary actions, list rows |
| `subhead` | Inter Medium | 15 | `.subheadline` | 21 (1.40) | 0 | Metadata labels, chip text |
| `footnote` | Inter Regular | 13 | `.footnote` | 19 (1.46) | 0.1 | Timestamps, helper text |
| `caption` | Inter Medium | 12 | `.caption` | 16 (1.33) | 0.3 | Badges, gauge ticks, overlines |

> **One exception to upscaling.** The single huge `display` directive on Today may cap at `DynamicTypeSize.accessibility3` *only if* layout visibly breaks at larger sizes — and even then it must still grow up to that cap. Every other role honors the full Dynamic Type range with no cap. See §6.1.

### 3.4 `TypographyTokens` shape

Views never call `Font.custom` directly. They ask the token namespace for a role:

```swift
struct TypographyTokens: Sendable {
    func font(_ role: TypeRole) -> Font {
        switch role {
        case .display:  return .custom("PlayfairDisplay-SemiBold", size: 40, relativeTo: .largeTitle)
        case .titleL:   return .custom("PlayfairDisplay-SemiBold", size: 34, relativeTo: .largeTitle)
        case .titleM:   return .custom("PlayfairDisplay-Medium",   size: 28, relativeTo: .title)
        case .titleS:   return .custom("PlayfairDisplay-Medium",   size: 22, relativeTo: .title2)
        case .body:     return .custom("Inter-Regular",            size: 17, relativeTo: .body)
        case .bodyEmph: return .custom("Inter-SemiBold",           size: 17, relativeTo: .body)
        case .callout:  return .custom("Inter-Regular",            size: 16, relativeTo: .callout)
        case .subhead:  return .custom("Inter-Medium",             size: 15, relativeTo: .subheadline)
        case .footnote: return .custom("Inter-Regular",            size: 13, relativeTo: .footnote)
        case .caption:  return .custom("Inter-Medium",             size: 12, relativeTo: .caption)
        }
    }
    static let standard = TypographyTokens()
}

enum TypeRole { case display, titleL, titleM, titleS, body, bodyEmph, callout, subhead, footnote, caption }
```

Usage: `Text("What do you want to be known for?").font(theme.typography.font(.display))`.
A tiny convenience modifier may pair font + tracking + line-spacing in one call: `.marqueType(.body)` (it bundles three properties, so it qualifies as a legitimate grouping modifier per §1.1).

### 3.5 Copy voice (typographic, not just verbal)

Marque's copy is **quiet, declarative, slightly philosophical** ("What do you want to be known for?"). Typographically this means: sentence case (not Title Case) for directives; never ALL CAPS except `caption`-role overlines with generous tracking; no exclamation marks in system copy; numerals in the streak/score are tabular-figure aligned (`.monospacedDigit()`) so they don't jitter as they change.

### 3.6 Font registration & licenses screen

- `Info.plist` → `UIAppFonts` lists all six `.otf`/`.ttf` files.
- A **Licenses** screen (reachable from Settings → About) renders the OFL notice + full license text for Playfair Display and Inter, satisfying the OFL bundling obligation. The referral row and other Settings rows live alongside it per `04-screens-create.md`.

---

## 4. Spacing, radii, elevation

### 4.1 Spacing scale (4pt base)

Generous whitespace is a feature, not waste. Marque biases toward the larger end of this scale.

| Token | Value | Use |
|---|---|---|
| `spacing.xxs` | 4 | Icon-to-label gaps |
| `spacing.xs` | 8 | Tight intra-component |
| `spacing.s` | 12 | Chip padding, list row gaps |
| `spacing.m` | 16 | Default control padding |
| `spacing.l` | 20 | Card internal padding (min) |
| `spacing.xl` | 28 | Section spacing |
| `spacing.xxl` | 40 | Screen edge breathing room |
| `spacing.xxxl` | 64 | Today directive vertical isolation |

Default screen horizontal inset = `spacing.xxl` (40) on Today and reader screens to maximize whitespace; `spacing.l` (20) on dense list screens (Insights, Calendar).

### 4.2 Radii

| Token | Value | Use |
|---|---|---|
| `radii.sm` | 8 | Chips, badges, small controls |
| `radii.md` | 14 | Buttons, text fields |
| `radii.lg` | 20 | Cards, format cards |
| `radii.xl` | 28 | Bottom sheets, large panels |
| `radii.pill` | 999 | Pills, streak glyph container |

All corners use `.continuous` (iOS squircle) corner curvature, never circular, to match native iOS softness:

```swift
.clipShape(RoundedRectangle(cornerRadius: theme.radii.lg, style: .continuous))
```

### 4.3 Elevation / shadow

Shadows are **soft, single-direction (downward), low-opacity** — paper lifting off paper, never floating glass. Dark mode reduces shadow and leans on `surfaceRaised` lightness for separation.

| Token | Color | Radius | Y-offset | Opacity (light / dark) | Use |
|---|---|---|---|---|---|
| `elevation.none` | — | 0 | 0 | 0 / 0 | Flat-on-surface |
| `elevation.raised` | ink/black | 12 | 4 | 0.06 / 0.18 | Cards, format cards |
| `elevation.floating` | ink/black | 28 | 10 | 0.10 / 0.28 | Bottom sheets, popovers |
| `elevation.pressed` | ink/black | 6 | 2 | 0.04 / 0.12 | Pressed card (inset feel) |

```swift
struct ElevationToken { let color: Color; let radius: CGFloat; let y: CGFloat; let opacity: Double }
extension View {
    func elevation(_ t: ElevationToken) -> some View {
        shadow(color: t.color.opacity(t.opacity), radius: t.radius, x: 0, y: t.y)
    }
}
```

> Under **Reduce Transparency** (`@Environment(\.accessibilityReduceTransparency)`), any material/blur surface (e.g. a frosted sheet) falls back to an **opaque** `surfaceRaised`. Shadows are unaffected.

---

## 5. Motion & haptics

### 5.1 Motion tokens

Curves match "slow eased breathing." No bounce that reads as playful.

| Token | Definition | Use |
|---|---|---|
| `motion.calm` | `.easeInOut(duration: 0.45)` | Default transitions, fades, reveals |
| `motion.enter` | `.spring(response: 0.5, dampingFraction: 0.9)` | View/sheet entrances (critically damped — no overshoot) |
| `motion.quick` | `.easeOut(duration: 0.25)` | Selection, chip toggles, small state |
| `motion.breath` | `.easeInOut(duration: 2.4).repeatForever(autoreverses: true)` | The gold streak-glyph pulse only; **disabled under Reduce Motion** |

Durations stay in **250–500ms** for transitions. Nothing snappier or bouncier than `motion.enter`.

### 5.2 Reduce Motion is mandatory

`withAnimation()` does **not** auto-respect Reduce Motion. All animations route through a helper that reads `@Environment(\.accessibilityReduceMotion)` and degrades to instant/crossfade ([Hacking with Swift — reduce animations when requested](https://www.hackingwithswift.com/quick-start/swiftui/how-to-reduce-animations-when-requested), [createwithswift — supporting reduced motion](https://www.createwithswift.com/ensure-visual-accessibility-supporting-reduced-motion-preferences-in-swiftui/), [tanaschita — reduced motion](https://tanaschita.com/ios-accessibility-reduced-motion/)).

```swift
struct MotionEnvironment {
    let reduceMotion: Bool
    func run<R>(_ animation: Animation?, _ body: () throws -> R) rethrows -> R {
        reduceMotion ? try body() : try withAnimation(animation, body)
    }
}
// Usage:
@Environment(\.accessibilityReduceMotion) var reduceMotion
motion.run(theme.motion.calm) { isExpanded.toggle() }
```

**Degradation rules under Reduce Motion:** swap eased transitions for crossfades or instant state; stop `motion.breath` (the glyph holds its lit state); and **never** rely on motion alone to signal a change — pair every motion signal with a redundant color/icon change (e.g. the score gauge shifts ink→gold *as well as* animating, §7.7).

### 5.3 Haptics API

Marque is iOS 17+, so haptics use the SwiftUI **`.sensoryFeedback`** modifier exclusively — no UIKit `UIFeedbackGenerator` ([Apple — SensoryFeedback](https://developer.apple.com/documentation/swiftui/sensoryfeedback), [Swift with Majid — sensory feedback](https://swiftwithmajid.com/2023/10/10/sensory-feedback-in-swiftui/), [Use Your Loaf — SwiftUI sensory feedback](https://useyourloaf.com/blog/swiftui-sensory-feedback/)). The feedback fires when a trigger value changes:

```swift
.sensoryFeedback(.success, trigger: directiveCompleted)
.sensoryFeedback(.selection, trigger: selectedFormatID)
.sensoryFeedback(trigger: publishState) { _, new in new == .failed ? .error : nil }
```

**iPad has no Taptic Engine — haptics degrade silently.** No code branch needed; the modifier is simply a no-op there.

### 5.4 Haptics map

Keep haptics **sparse** — one deliberate tap per meaningful event, **never on scroll**. This restraint *is* the calm doctrine.

| Event | Feedback | Notes |
|---|---|---|
| Today directive completed / "film once" batch finished | `.success` | The hero-loop payoff moment |
| Streak increment | `.impact(weight: .medium)` | Once per increment |
| Format-card / chip selection | `.selection` | Light, frequent-but-intentional |
| Teleprompter start | `.start` | Paired with scroll begin |
| Teleprompter stop / record end | `.stop` | |
| Publish failure | `.error` | Paired with `danger` color + retry affordance |
| Score gauge crosses a threshold (e.g. 60→ "strong") | `.levelChange` | Redundant with color shift |
| Schedule conflict surfaced | `.warning` | |
| Pull-to-refresh commit | `.impact(weight: .light)` | Only on the commit, not the drag |

`HapticTokens` is a thin set of constants so events are named, not inlined:

```swift
enum Haptics {
    static let directiveDone: SensoryFeedback = .success
    static let streakUp:      SensoryFeedback = .impact(weight: .medium)
    static let select:        SensoryFeedback = .selection
    static let promptStart:   SensoryFeedback = .start
    static let promptStop:    SensoryFeedback = .stop
    static let publishFailed: SensoryFeedback = .error
    static let scoreThreshold:SensoryFeedback = .levelChange
}
```

---

## 6. Accessibility (review-gating, non-optional)

### 6.1 Dynamic Type

- Every text role scales via `relativeTo:` (§3.3); every type-coupled dimension scales via `@ScaledMetric` (§3.2).
- Test the full range **up to the accessibility sizes** (`AX1`–`AX5`). Only the Today `display` directive may cap, and only at `accessibility3`, and only if layout breaks (§3.3).
- Symbols/images coupled to text scale with `.imageScale(.medium)` and/or `@ScaledMetric` sizing ([adapting images & symbols to Dynamic Type — nilcoalescing](https://nilcoalescing.com/blog/AdaptingImagesAndSymbolsToDynamicTypeSizesInSwiftUI/)).

### 6.2 Contrast (WCAG AA, per Apple HIG)

Targets: **4.5:1** for normal text, **3:1** for large text (≥ 18pt regular / ≥ 14pt bold) ([Apple HIG — Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility/), [iOS accessibility best practices 2025](https://medium.com/@david-auerbach/ios-accessibility-guidelines-best-practices-for-2025-6ed0d256200e)).

| Pair | Ratio | Verdict |
|---|---|---|
| `ink #171512` on `cream #F4F1EA` | ~14.8:1 | PASS (body) |
| `inkSoft #5A554C` on `cream` | ~6.4:1 | PASS (body) |
| `inkFaint #8C867A` on `cream` | ~3.4:1 | Large/placeholder only |
| `bone #EDEBE5` on `night #0E0E10` | ~15.2:1 | PASS (body) |
| `ink` on `gold #C9A227` (gold fill, ink label) | ~7.0:1 | PASS — gold *fill* with ink label is fine |
| **`gold #C9A227` text on `cream`** | **~2.9:1** | **FAIL for text — banned for body/small labels** |

> **The gold rule, formalized.** `#C9A227` as a *text or small-interactive-label color* fails AA. Therefore gold is restricted to: glyphs/icons, large display flourishes, active-state hairlines/underlines, the streak mark, and **fills** that carry an ink (`onAccent`) label. Never gold text on cream. This rule is enforced in code review and by the contrast lint below.

A debug-only contrast assertion runs in CI snapshot tests: every semantic text-on-surface pairing is checked against its AA threshold; gold is asserted to appear only via `accent`/fill roles, never `textPrimary/Secondary/Tertiary`.

### 6.3 VoiceOver

- Every interactive control supplies `.accessibilityLabel` + `.accessibilityValue` (where stateful) + `.accessibilityHint`.
- The **score gauge** announces its value: label "Virality score", value "78 out of 100" (§7.7).
- **Decorative elements are hidden:** paper texture, the breathing glyph's animation, ornamental rules → `.accessibilityHidden(true)`.
- Compound cards (format card, teardown card) group with `.accessibilityElement(children: .combine)` and expose a single coherent label + a tap action.
- The streak glyph reads "7-day streak" (not "fire emoji").

### 6.4 Tap targets

Minimum **44 × 44 pt** for every interactive element (Apple HIG) ([Apple HIG — Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility/), [accessible touch target sizes — LogRocket](https://blog.logrocket.com/ux-design/all-accessible-touch-target-sizes/)). Visually small affordances (calendar cell, chip, clip-cell menu) **pad the hit area, not the glyph**:

```swift
.contentShape(Rectangle())
.frame(minWidth: 44, minHeight: 44)   // hit area; visual glyph stays small inside
```

### 6.5 Reduce Motion & Reduce Transparency

Reduce Motion handled in §5.2. Reduce Transparency falls any blur/material surface back to opaque `surfaceRaised` (§4.3). Both are read from the Environment and threaded through the `Theme`-adjacent helpers, so components don't each re-implement the check.

---

## 7. Component inventory

Every component is a **token-consuming view**. For any component that can hold data, the spec enumerates the five **States: loading / empty / error / offline / permission-denied**. Components that are purely presentational (e.g. a static divider) declare "N/A — stateless."

### 7.0 Component → token contract (summary)

| Component | Surface | Text | Radius | Elevation | Haptic | Min target |
|---|---|---|---|---|---|---|
| Primary button | `accent` fill | `onAccent` | `md` | `raised` | `.success`/none | 44 |
| Secondary button | `surface` + `ink` outline | `textPrimary` | `md` | none | `.selection` | 44 |
| Text/ghost button | transparent | `textPrimary` | `md` | none | `.selection` | 44 |
| Card | `surfaceRaised` | per content | `lg` | `raised` | — | — |
| Format card | `surfaceRaised` | `textPrimary` | `lg` | `raised` | `.selection` | 44 |
| Chip | `surfaceSunken` | `subhead` | `sm`/`pill` | none | `.selection` | 44 (padded) |
| Bottom sheet | `surfaceRaised` | `titleS`+ | `xl` (top) | `floating` | — | — |
| Tab bar | `surface` | ink glyph / `accent` active | — | hairline top | `.selection` | 44 |
| Text field | `surfaceSunken` trough + hairline | `body` | — (underline) | none | — | 44 |
| Score gauge | — | `caption` ticks | — | — | `.levelChange` | — |
| Clip cell | `surfaceRaised` | `footnote` badge | `md` | `raised` | — | 44 |
| Calendar cell | `surface` | `subhead` | `sm` | none | `.selection` | 44 |
| Teleprompter overlay | `teleprompterScrim` | `teleprompterText` | — | — | `.start`/`.stop` | 44 |

### 7.1 Buttons

Three variants, all bundled into a single `.marqueButton(_:)` modifier that groups color + type + padding + radius + the press haptic (legitimate multi-token grouping per §1.1).

| Variant | Fill | Label | Border | When |
|---|---|---|---|---|
| `.primary` | `accent` (gold) | `onAccent` (ink) | none | The one primary action on a screen |
| `.secondary` | `surface` | `textPrimary` | 1pt `textPrimary` | Alternate action |
| `.ghost` | transparent | `textPrimary` | none | Tertiary / inline |

- Min height **44**; horizontal padding `spacing.l`; radius `radii.md`; type `bodyEmph`.
- **Pressed:** scale to 0.98 via `motion.quick`; fill → `accentPressed` (primary). Under Reduce Motion, drop the scale, keep the color change.
- **Disabled:** `textTertiary` label, no shadow, `surfaceSunken` fill; `.accessibilityHint` explains why if non-obvious.
- **Loading state:** label swaps for an indeterminate progress ring (or static dots under Reduce Motion); button stays at full size to avoid layout shift; tap disabled; `.accessibilityValue("Loading")`.

```swift
PrimaryButton("Start your week") { startBatch() }   // wraps .marqueButton(.primary) + .sensoryFeedback
```

### 7.2 Cards

- Surface `surfaceRaised`, radius `radii.lg`, elevation `raised`, internal padding **≥ `spacing.l` (20)**.
- **One idea per card.** A card never stacks two unrelated CTAs.
- **States:** loading → shimmer placeholder block (static skeleton under Reduce Motion); empty → quiet centered line in `textSecondary` ("Nothing here yet."); error → `danger` hairline + retry ghost button; offline → muted card + "Saved — will sync" footnote; permission-denied → inline explainer + a single ghost button routing to Settings.

### 7.3 Format card (render-recipe preview)

Format cards present the structured **render-recipes** from `08-format-virality.md` (split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook). A format card is **not** a blank talking head — it previews structure.

- **Anatomy:** recipe thumbnail (top), tiny structural diagram glyph (e.g. a split bar, a 3-cell grid), format name (`titleS`), one-line descriptor (`footnote`/`textSecondary`).
- **Selectable.** Selected state = a **gold hairline border** (`accent`, 1.5pt) + lit diagram glyph — **never a gold fill**. Fires `.selection` haptic.
- VoiceOver: combined element, label "Split-screen format", value "Selected" / "Not selected", hint "Double-tap to choose this format for your clips."
- **States:** loading → thumbnail shimmer + name skeleton; empty → N/A (the library is bundled, never empty); error (thumbnail failed to load) → diagram glyph stands in, name still shown; offline → bundled formats render fully (recipes are local); permission-denied → N/A.

### 7.4 Chips

- Trend Radar tags, Hook Lab tags, filter chips.
- Idle: `surfaceSunken`, `subhead` in `textSecondary`, radius `radii.sm` (or `pill` for filter chips).
- Selected: gold **underline or 1pt border** (`accent`), text → `textPrimary`. `.selection` haptic.
- 44pt hit area via padding even when visually small (§6.4).

### 7.5 Bottom sheets

The primary progressive-disclosure mechanism — **this is how Hook Lab nests inside the script reader and how contextual detail surfaces without cluttering Today.**

- Native `.presentationDetents([.medium, .large])` + `.presentationDragIndicator(.visible)` + `.presentationCornerRadius(theme.radii.xl)`.
- Surface `surfaceRaised`; big serif `titleS`/`titleM` header; content scrolls; primary action pinned bottom.
- Background dim uses `colors.scrim`. Under Reduce Transparency, any frosted variant becomes opaque `surfaceRaised`.
- **States:** loading → centered progress + skeleton rows; empty → quiet line; error → inline `danger` banner + retry; offline → cached content + "offline" footnote; permission-denied → explainer + Settings route.

### 7.6 Navigation (tab bar + nav bar)

- **Tab bar:** minimal, ink SF Symbol glyphs; **gold only on the active tab** (glyph tint `accent`); 0.5pt `separator` hairline on top edge; `surface` background. `.selection` haptic on tab change. Tabs per `01-information-architecture.md` (Today, Record, Trends, Insights/Coach, Profile — final set owned by the IA doc).
- **Nav bar:** large-title uses the serif `titleL`; back chevron is an ink SF Symbol; **Today carries no feature chrome** — no toolbar buttons crowding the directive.

### 7.7 Score gauge (Virality score)

Consumes the 0–100 virality score from `07-ai-system.md`.

- Circular arc, 0–100. Stroke width via `@ScaledMetric(relativeTo: .body)` so it scales with type.
- **Color is the redundant signal:** the arc tint interpolates `textSecondary` → `accent` (ink→gold) as the score rises, so the meaning survives Reduce Motion even when the fill animation is suppressed.
- Center label: the number in `titleM` with `.monospacedDigit()`; a `caption` overline reads the band ("Strong").
- Crossing a band threshold fires `.levelChange` (§5.4).
- **VoiceOver:** `.accessibilityLabel("Virality score")`, `.accessibilityValue("78 out of 100, strong")`. The arc graphic is `.accessibilityHidden(true)` (value is on the container).
- **States:** loading → arc at 0 with a slow indeterminate sweep (static ring under Reduce Motion) + "Scoring…"; empty (not yet scored) → hollow ring + "Not scored yet"; error → hollow ring + `warning` dot + "Couldn't score — retry"; offline → last cached score + "offline" footnote; permission-denied → N/A.

### 7.8 Clip cell

Renders an edited clip from the ClipEngine pipeline (`05-screens-produce.md` / `08-format-virality.md`).

- Anatomy: thumbnail (radius `radii.md`), duration badge + format badge (`footnote` on a `scrim` chip, bottom-left), optional gold "scheduled" dot.
- Min 44pt tappable; long-press opens an action bottom sheet (publish, reschedule, delete).
- **States:** loading → shimmer thumbnail + spinner badge "Rendering…" (static under Reduce Motion); empty → N/A at cell level; error → muted thumbnail + `danger` badge "Render failed" + tap-to-retry; offline → thumbnail from cache, publish actions disabled with "offline" note; permission-denied → N/A.

### 7.9 Calendar cell

Schedule grid (publish calendar; see `10-social-publishing.md`).

- Day number in `subhead`; a small **gold dot** per scheduled post (max 3 dots, then "+N" in `caption`).
- Today's cell = subtle `surfaceSunken` fill; selected = 1pt `accent` ring.
- 44pt hit area via padding; `.selection` on tap.
- **States:** loading → skeleton dots; empty → bare day number; error → `warning` dot if a scheduled post failed to load; offline → cached schedule + "offline" footnote on the screen; permission-denied → N/A.

### 7.10 Teleprompter overlay (AVFoundation)

Renders scrolling script text over the live camera during a batch record session (`05-screens-produce.md`).

- **Own readable token set:** `teleprompterText` (white) on `teleprompterScrim` (≈65% black reading band) — independent of brand cream, because the background is arbitrary live video. This is the sanctioned pure-white exception (§2.2).
- Text size and line spacing are **user-scalable** and scale with `@ScaledMetric`; scroll speed is user-adjustable.
- Start/stop fire `.start` / `.stop` haptics (§5.4).
- Controls (speed, size, mirror, record) are ≥ 44pt, placed outside the reading band, high-contrast.
- **States:** loading → "Loading script…" centered; empty → "No script yet — generate one first" + route to the script reader; error → "Couldn't load script" + retry; offline → cached script renders fully (text is local once generated); **permission-denied → camera permission is the gating state**: a calm full-screen explainer ("Marque needs your camera to film") + one primary button to Settings. The teleprompter never shows over a black void without explaining why.

### 7.11 Text fields

- **Hairline underline style, not boxed** — editorial calm. A `separator` hairline under the field; focus raises it to `accent`; error turns it `danger`.
- Trough (if any) is `surfaceSunken`; text `body`; placeholder `textTertiary`.
- 44pt min height; clear focus ring (the underline color change *is* the focus signal, plus `.accessibilityFocused`).
- **States:** error → `danger` hairline + a `footnote` message in `danger` below; disabled → `textTertiary` + no underline animation; offline/permission-denied → N/A (handled by the submitting action, not the field).

---

## 8. Iconography, imagery, paper texture

### 8.1 Iconography

- **SF Symbols** are the icon base — native, free, Dynamic-Type-aware via `.imageScale` and weight-matched to the adjacent font weight ([Apple — Fonts / SF Symbols](https://developer.apple.com/fonts/)). Marque uses thin/regular weights to match the editorial tone; never the heavy/black weights.
- Symbol color follows the same semantic roles: ink glyphs by default, `accent` only for active/affordance states.
- Bespoke marks (the streak glyph, format diagrams) are custom vectors but follow the same weight/contrast discipline and ship as template (tintable) assets.

### 8.2 Imagery

- Creator thumbnails/clips render with `radii.md`–`lg` and `.continuous` corners; no drop shadow on media (the card carries the shadow).
- No stock illustration. Empty states are typographic (a quiet serif line), not cartoon art — consistent with the Stoic restraint.

### 8.3 Paper texture

- A subtle, low-opacity tiled overlay on `surface` to evoke paper — **opacity ≤ ~6%** so it never harms text contrast.
- Always `.accessibilityHidden(true)`.
- **Dropped or replaced under Reduce Transparency** (`@Environment(\.accessibilityReduceTransparency)`) — falls back to flat `surface`.
- Implemented as a single `Image(.paperTexture).resizable(resizingMode: .tile).blendMode(.multiply).opacity(0.05)` behind content, never over text layers.

---

## 9. SwiftUI implementation playbook

### 9.1 Reading the theme

```swift
struct TodayDirective: View {
    @Environment(\.theme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let directive: String

    var body: some View {
        Text(directive)
            .font(theme.typography.font(.display))
            .foregroundStyle(theme.colors.textPrimary)
            .padding(.vertical, theme.spacing.xxxl)
            .padding(.horizontal, theme.spacing.xxl)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}
```

### 9.2 The legitimate grouping modifiers (the only custom modifiers we ship)

| Modifier | Bundles | Rationale |
|---|---|---|
| `.marqueType(_ role:)` | font + tracking + line-spacing | Three coupled type properties |
| `.marqueButton(_ variant:)` | fill + label color + padding + radius + press scale + haptic | A full role |
| `.marqueCard(elevation:)` | surface + radius + padding + shadow | A full role |
| `.marqueHitTarget()` | `contentShape` + 44pt min frame | Accessibility guarantee |
| `.elevation(_ token:)` | the shadow recipe | One concept, many params |

Anything that sets a *single* property must use the native API directly (no `.dsForeground()` style wrappers) ([when to use modifiers vs native ShapeStyle](https://dev.to/sebastienlato/swiftui-design-tokens-theming-system-production-scale-b16)).

### 9.3 Reduce Motion helper (shared)

```swift
extension EnvironmentValues {
    var motionRunner: MotionEnvironment { MotionEnvironment(reduceMotion: accessibilityReduceMotion) }
}
// theme.motion provides the curve; motionRunner decides whether to animate.
```

### 9.4 Previews & tests

- Every component preview renders **light + dark × default + AX5 Dynamic Type × Reduce Motion on/off** (a `#Preview` matrix). This is the cheapest way to catch contrast and layout breaks before review.
- Snapshot tests assert the contrast lint (§6.2) and that no view references a primitive color name (a simple source-grep test in CI).

---

## 10. Acceptance criteria (Definition of Done for the design system)

A build satisfies this section when **all** of the following hold:

1. **No primitive leakage.** A source grep finds zero references to `cream`, `gold`, `ink`, `night`, or hex literals outside `ColorTokens`/Asset Catalog. View code uses only semantic roles.
2. **Light/dark is a token swap.** Toggling system appearance re-resolves the `Theme` and updates every surface/text/accent with no manual per-view branching.
3. **Dynamic Type passes to AX5.** Every screen is legible and non-clipping up to `AX5`; only the Today `display` directive may cap (at `accessibility3`). All custom fonts use `relativeTo:`; all type-coupled dimensions use `@ScaledMetric`.
4. **Contrast lint is green.** Every text-on-surface pairing meets AA (4.5:1 / 3:1). Gold never appears as a text role — only `accent`/fill. The gauge/streak gold passes as large/glyph.
5. **Haptics are mapped and sparse.** Each event in §5.4 fires its mapped `.sensoryFeedback`, none fire on scroll, and iPad degrades silently.
6. **Reduce Motion respected.** No animation runs without routing through the motion helper; `motion.breath` stops; every motion-only signal has a redundant color/icon change.
7. **Reduce Transparency respected.** Paper texture and any material fall back to opaque `surfaceRaised`.
8. **44pt targets.** Every interactive element (including calendar/chip/clip affordances) has a ≥ 44×44pt hit area, verified with the Accessibility Inspector.
9. **VoiceOver complete.** All controls have labels; the gauge announces its value; decorative texture/glyph animations are hidden; compound cards are combined elements.
10. **Today stays calm.** The Today screen renders exactly one directive + the gold streak glyph + one trend line, with no feature chrome — verified against `01-information-architecture.md`.
11. **Licenses screen ships.** The OFL notices for Playfair Display and Inter are present and reachable from Settings → About.

---

## Open questions

1. **Commercial faces vs OFL.** The brief lists Tiempos/Söhne as alternates to Playfair/Inter. Those are paid Klim licenses. **Default decision (recorded, reversible):** ship the free SIL OFL Playfair Display + Inter. Confirm whether to budget for Tiempos/Söhne instead — if so, this is a one-file `TypographyTokens` swap plus a license purchase.
2. **Gold-text ruling (confirm the doctrine).** `#C9A227` text fails AA at ~2.9:1 on cream. This doc *locks* gold to glyph / large-display / hairline / fill-with-ink-label only. Confirm design accepts this hard rule (it shapes the whole accent strategy).
3. **iPad support.** iPad has no haptics (the map degrades silently) and the teleprompter/record layout differs materially. **Default decision:** iPhone-first; iPad runs as a scaled iPhone app at launch. Confirm whether a true iPad layout is in scope for v1 — it changes Record and the calendar grid specs.
4. **User-facing appearance override.** Marque currently follows the system color scheme (no in-app light/dark toggle). Confirm whether an explicit override belongs in Settings; if yes, it becomes a stored preference fed into `ThemeProvider`.

## Sources

- [Sagar Unagar — App-wide theming in SwiftUI](https://www.sagarunagar.com/blog/app-wide-theming-swiftui) — Environment-driven `Theme` pattern (the chosen architecture).
- [Sebastien Lato — SwiftUI design tokens & theming (production-scale)](https://dev.to/sebastienlato/swiftui-design-tokens-theming-system-production-scale-b16) — semantic tokens; when to use a modifier vs native `ShapeStyle`.
- [Magnus Kahr — Semantic colors in a SwiftUI design system](https://www.magnuskahr.dk/posts/2025/06/swiftui-design-system-considerations-semantic-colors/) — role-based color naming for light/dark.
- [Kristoffer Knape — Building a SwiftUI design system, part 1: color](https://www.designsystemscollective.com/building-a-swiftui-design-system-part-1-color-2ea75035e691) — color token composition.
- [freeCodeCamp — How to build a design system with SwiftUI](https://www.freecodecamp.org/news/how-to-build-design-system-with-swiftui/) — modifier vs native API guidance.
- [Sarunw — Scale custom fonts with Dynamic Type](https://sarunw.com/posts/swiftui-scale-custom-font-dynamic-type/) — `Font.custom(_:size:relativeTo:)` to make Playfair scale.
- [Use Your Loaf — Scaling custom SwiftUI fonts with Dynamic Type](https://useyourloaf.com/blog/scaling-custom-swiftui-fonts-with-dynamic-type/) — same pitfall, second source.
- [Hacking with Swift — Dynamic Type with a custom font](https://www.hackingwithswift.com/quick-start/swiftui/how-to-use-dynamic-type-with-a-custom-font) — registration + scaling.
- [avanderlee — @ScaledMetric for Dynamic Type support](https://www.avanderlee.com/swiftui/scaledmetric-dynamic-type-support/) — scaling non-text dimensions (gauge stroke, paddings).
- [nilcoalescing — Adapting images & symbols to Dynamic Type](https://nilcoalescing.com/blog/AdaptingImagesAndSymbolsToDynamicTypeSizesInSwiftUI/) — SF Symbol/image scaling.
- [Apple — Human Interface Guidelines: Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility/) — 44pt targets, 4.5:1 / 3:1 contrast, Reduce Motion duty (primary).
- [Apple — SensoryFeedback](https://developer.apple.com/documentation/swiftui/sensoryfeedback) — the iOS 17 `.sensoryFeedback` modifier and feedback types (primary).
- [Swift with Majid — Sensory feedback in SwiftUI](https://swiftwithmajid.com/2023/10/10/sensory-feedback-in-swiftui/) — trigger/condition patterns.
- [Use Your Loaf — SwiftUI sensory feedback](https://useyourloaf.com/blog/swiftui-sensory-feedback/) — feedback type catalog + iPad no-op behavior.
- [Hacking with Swift — Reduce animations when requested](https://www.hackingwithswift.com/quick-start/swiftui/how-to-reduce-animations-when-requested) — `withAnimation` ignores Reduce Motion; the helper pattern.
- [createwithswift — Supporting reduced motion preferences in SwiftUI](https://www.createwithswift.com/ensure-visual-accessibility-supporting-reduced-motion-preferences-in-swiftui/) — degradation strategy.
- [tanaschita — iOS accessibility: reduced motion](https://tanaschita.com/ios-accessibility-reduced-motion/) — Environment read + redundant-signal rule.
- [Google Fonts — Playfair Display license (SIL OFL 1.1)](https://fonts.google.com/specimen/Playfair+Display/license) — free commercial embedding.
- [SIL — How to use OFL fonts](https://openfontlicense.org/how-to-use-ofl-fonts/) — bundling obligations (notice + license text, reserved-name rule).
- [Apple Developer Forums — Bundling licensed fonts in an app](https://developer.apple.com/forums/thread/720890) — `UIAppFonts` registration + license practice.
- [LogRocket — Accessible touch target sizes](https://blog.logrocket.com/ux-design/all-accessible-touch-target-sizes/) — 44pt rationale, padding-the-hit-area technique.
- [iOS accessibility guidelines — best practices for 2025](https://medium.com/@david-auerbach/ios-accessibility-guidelines-best-practices-for-2025-6ed0d256200e) — contemporary AA/VoiceOver checklist.
- [Apple — Fonts / SF Symbols](https://developer.apple.com/fonts/) — SF Symbols as the Dynamic-Type-aware icon base.
