# Format rubric — LOOP F vision tier (optional, paid, ask-first)

Judges RENDER FORMATTING only — never editorial/content quality (that's
`review_rubric.md`'s job). Score a fixture's sampled frames 0–100; each failed
dimension reports a frame-anchored issue. This tier exists to catch what the
deterministic checks (duration/non-black/saturation/pitch) and the pure-math
layout tests (`render/src/__tests__/layout.test.ts`,
`composition_wiring.test.ts`) structurally can't: real font-metric overflow,
actual visual collisions, and typography/legibility judgment calls.

## Dimensions

1. **Safe areas**: no caption/sticker/card/chip pixels sit inside the platform
   UI chrome bands (roughly the top ~15% and bottom ~17% of frame). Fail →
   `unsafe_area`.
2. **Overflow / clipping**: no text is cut off, wrapped mid-character, or
   spilling past its container's visible edge. Fail → `overflow`.
3. **Collisions**: no two of {caption, sticker, text_card, credit chip}
   visually overlap each other. Fail → `collision`.
4. **Typeface fallback**: every visible text element renders in one of the
   three embedded families (Inter / Archivo Black / Baloo 2) — never a system
   sans-serif fallback glyph (tofu boxes, obviously wrong metrics). Fail →
   `font_fallback`.
5. **Contrast**: text remains legible against its background at every
   sampled frame (no white-on-white, no low-contrast pairing). Fail →
   `contrast`.
6. **Placeholder copy**: no obviously-fake or leftover placeholder text
   ("Reference post", "Solution 1/2/3", lorem-ipsum-style filler) is visible.
   Fail → `placeholder_copy`.
7. **Layout matches style**: the composition's framing (talking-head crop,
   green-screen card, duet split panels, split-three thirds, etc.) reads as
   its intended layout, not a broken/degenerate one (e.g. a panel collapsed to
   zero height, a card rendering off-canvas). Fail → `layout_mismatch`.

## Scoring

- Start at 100; subtract per failed dimension (safe-areas/collisions/overflow
  are heaviest — ~20 each; the rest ~10–15).
- Judge each dimension independently; do not let one bad frame anchor the
  whole score if the rest of the sampled frames are clean — report the worst
  offending frame per failed dimension instead.

## Output shape

`{score_0_100, issues: [{code, frame, description}]}` — `description` is a
short human-readable note (no `fix_op`: these are render-pipeline bugs to
patch in `render/src/**`, not per-clip creator-facing tweaks).
