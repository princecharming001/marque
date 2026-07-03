---
name: design-review
description: Visual design review for Marque — screenshot screens in the simulator, grade them against the app's design language rubric, and fix what fails. Use when asked to review/critique/polish UI, apply or check the theme, hunt visual inconsistencies, or after any significant UI change.
---

# Marque design review

You are acting as a design lead reviewing this app, not a developer checking it compiles. Judge every screen by LOOKING at it, never by reading the code alone. Code review tells you what a screen is; only a screenshot tells you what it looks like.

## The loop (never skip step 3)

1. Build: `./scripts/dev.sh build` (device `2858A468-767A-4F05-B43E-7E84FA8B86B6`).
2. Navigate to the screen — Maestro flow or the DEBUG `dev.jump` menu (Onboarding/Home), then taps.
3. Screenshot: `xcrun simctl io 2858A468-767A-4F05-B43E-7E84FA8B86B6 screenshot out.png`, downscale with `sips -Z 700`, and **Read the image**.
4. Grade against the rubric below. Name each failure specifically ("the streak pill's 16pt padding reads heavier than the 12pt used on Home chips"), not vaguely ("could be more polished").
5. Fix → rebuild → re-screenshot → re-grade. A fix is not done until the new screenshot passes.

For a full-app sweep: capture EVERY screen first, then review them together in one pass — inconsistency ("which screen is the odd one out?") is only visible side-by-side.

## Marque's design language (the rubric)

Aesthetic: **Stoic editorial warmth** — serif display type over a calm warm canvas, generous whitespace, one accent used sparingly. Reference points the user has blessed: craft.do, the maxapp ink+gold system, BitePal onboarding patterns.

Grade each screen on:

1. **Token discipline** — every color from `Palette`, every font from `AppFont`/`Typeface`, every gap from `Space`, every corner from `Radius` (all in `ios/Marque/DesignSystem/Theme.swift`). Raw `Color(hex:)`, `.font(.system(size:))`, or magic padding numbers in feature files are defects unless the component is deliberately one-off (e.g. VoiceOrb's gradient art).
2. **Hierarchy** — one clear focal point per screen; eyes should land in ≤1s. If two elements compete, demote one.
3. **Spacing rhythm** — gaps step consistently (Space.xs→xl). Uneven optical gaps between sibling sections are defects even when the code "looks right."
4. **Type pairing** — serif display (`Typeface.display`) for moments, sans (`AppFont`) for utility. Serif in body copy or sans in a hero headline = defect.
5. **Restraint** — at most ONE bold move per screen (the orb, a dark hero, an oversized numeral). Everything else stays quiet. Two competing statements = remove one.
6. **Consistency across screens** — same control style everywhere (capsule buttons, hairline dividers, card radius). A rounded-rect button on one screen and a capsule on another is a defect.
7. **States** — check empty, loading, error, and long-content states, not just the happy path. Truncated text, orphaned single-line wraps, and clipped descenders are defects.
8. **Hard rules** — NEVER Apple emojis in UI (SF Symbols or custom art only). Tap targets ≥ 44pt. Text contrast ≥ 4.5:1 against its actual background (check the screenshot, not the token name).

## AI-slop tells (flag on sight)

- Every section the same weight — no rhythm, no focal point.
- Decoration that encodes nothing (gradient blobs, numbered 01/02/03 markers on non-sequential content).
- Scattered small animations instead of one orchestrated moment.
- Default-feeling combos: plain white cards + gray hairlines + blue accent with no point of view.
- Copy that describes the app to itself ("Your personalized dashboard") instead of speaking to the user.

## Token audit (run during any theme work)

```bash
# Raw colors outside the design system
grep -rn "Color(hex:" ios/Marque --include="*.swift" | grep -v DesignSystem
# System fonts bypassing AppFont/Typeface (icon .font(.system(size:)) on Image is acceptable; on Text it's a defect)
grep -rn "\.font(\.system(" ios/Marque --include="*.swift" | grep -v DesignSystem
```

Rule for theme changes: change tokens in `Theme.swift` first, migrate stragglers to tokens second, and only then look at per-screen exceptions. Never restyle screen-by-screen — that's how themes drift.

## Output format

For reviews, return a table: Screen · Finding · Rubric item violated · Severity (breaks-the-look / inconsistent / nitpick) · Fix. Then apply fixes for everything at "inconsistent" or worse unless told otherwise, re-screenshot, and show before/after.
