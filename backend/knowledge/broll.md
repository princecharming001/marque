# B-roll — grammar for cutaways (code-enforced in assemble_edl)

These are the rules `assemble_edl()` enforces mechanically (Phase 3) and `edl_eval` checks.

## When to cut to b-roll

- Cut on **concrete nouns and actions** — the word names something showable (a place, object,
  action, number). Abstract/filler words get no cutaway.
- One cutaway per concept; don't stack b-roll on every word.

## Timing grammar (frames @30fps)

- **J-cut lead ≈ 12 frames**: the b-roll starts ~0.4s BEFORE the cue word's caption frame, so
  the visual anticipates the word.
- **Hold 2–3s (60–90 frames)**; never < 1.5s (45f) — a sub-1.5s cutaway reads as a glitch — and
  never > 3.5s (105f) — a longer hold goes stale (see pacing.md motion floor).
- **Spacing ≥ 3s (90 frames)** between consecutive cutaways; back-to-back b-roll disorients.

## Protect face time

- **Never over the hook** (first ~90 output frames) or the **CTA** (last ~60 output frames) for
  face-led styles (talking_head, green_screen) — those beats must be the creator on camera.
- faceless is the exception: b-roll IS the visual channel there, so the hook can be b-roll.

## Match the a-roll

- Match palette and energy to the surrounding a-roll (the dossier gives the a-roll's look /
  energy); a bright hyper-cut b-roll under a calm story beat breaks immersion.
- Prefer own-media over stock when the creator supplied relevant footage.
