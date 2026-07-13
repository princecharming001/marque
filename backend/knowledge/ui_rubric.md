# UI rubric — LOOP U vision tier (optional, paid, ask-first)

Judges APP UI FORMATTING only — the SwiftUI editor/app chrome, never the
rendered video content (that's `format_rubric.md`'s job). Score a screen's
screenshot 0–100; each failed dimension reports a short note. This tier exists
for what Maestro's assertVisible/assertNotVisible structurally can't catch:
layout crowding, truncated text, misaligned elements, and legibility at large
Dynamic Type sizes — judgment calls a presence/absence check can't make.

## Dimensions

1. **Layout integrity**: no overlapping controls, no element clipped by the
   screen edge or another view, safe-area respected (no control under the
   status bar / home indicator). Fail → `layout_broken`.
2. **Text legibility**: no truncated/ellipsized text that hides meaningful
   content, no text overflowing its container, adequate contrast against its
   background. Fail → `text_illegible`.
3. **Dynamic Type resilience** (XXL screenshots only): the layout still reads
   as intentional at large text sizes — reflow/wrap is fine, but a control
   pushed off-screen or two labels overlapping is not. Fail → `dynamic_type_broken`.
4. **Composition framing fidelity**: for the three framing-preview screens
   (green_screen/duet_split/split_three), the preview's framing genuinely
   resembles what that composition looks like when rendered — not just "some
   chrome present" but chrome that reads as the right shape. Fail → `framing_mismatch`.
5. **Visual consistency**: spacing, corner radii, and color usage match the
   surrounding screens (no screen that looks like it belongs to a different
   app). Fail → `inconsistent_style`.

## Scoring

- Start at 100; subtract per failed dimension (layout integrity and text
  legibility are heaviest — ~25 each; the rest ~15–20).
- Judge each screenshot independently against its own manifest entry
  (`.maestro/ui-manifest.json`) — don't penalize a screen for what a DIFFERENT
  screen's dimension expects (e.g. don't dock the onboarding landing screen for
  "framing mismatch," which only applies to the 3 framing-preview screens).

## Output shape

`{score_0_100, issues: [{code, screen_id, description}]}` — `screen_id` matches
the `id` field in `.maestro/ui-manifest.json`; `description` is a short
human-readable note. No `fix_op`: these are SwiftUI layout bugs to patch in
`ios/Marque/**`, not render-pipeline or per-clip creator-facing tweaks.
