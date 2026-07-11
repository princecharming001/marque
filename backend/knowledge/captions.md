# Captions — grouping, sizing, safe zones, emphasis

## Grouping

- Default **phrase grouping**: 3-word stable chunks (not a per-frame sliding window — that
  jitters). Chunks are precomputed on stable breaks and held until the phrase completes.
- One caption block visible at a time; hide the block after the last word's `end_frame` + 12
  frames, and during any speech gap > 30 frames (silence shows no stale caption).

## Coverage

- **Every kept spoken word is captioned** (caption coverage == kept words). Captions are
  derived from the cleaned word list — never authored freehand by the model.
- Dropped/filler words get no caption.

## Sizing & safe zones

- **55–75pt** on a 1080×1920 canvas (large, thumb-stopping).
- Keep captions inside the safe zone: clear of the top ~12% (status/handle) and bottom ~18%
  (nav/CTA chrome). Center band is safest.
- **High contrast**: white text with a dark stroke/shadow, or a solid accent box. Never rely on
  color alone against unknown b-roll.

## Emphasis

- Highlight the load-bearing word per phrase (keyword highlight) — a color/scale accent on the
  single most important word draws the eye and lifts comprehension.
- Emphasis words come from the brief's emphasis spans, not random.
