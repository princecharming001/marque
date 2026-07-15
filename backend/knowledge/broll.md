# B-roll — when & where to place cutaways (code-enforced in assemble_edl)

Rules `assemble_edl()` enforces mechanically and `edit_plan_prompt` asks the model to honor.
Numbers reconciled 2026-07-15 against a sourced placement study (see "Sources" + reliability
tiers at the bottom — the *mechanics* below are well-corroborated; retention percentages are
marketing, not measurement, and are deliberately NOT encoded).

## Rule 0 — classify before placing

Every line is one of three things; decide which BEFORE adding any b-roll:
- **Illustrable** → b-roll candidate: names a concrete object/place/person, a number/stat, a
  process/how-to step, or shows evidence/proof. "Show, don't tell."
- **Face-protected** → never full-frame b-roll (see below).
- **Neither** → stay on the face. Empty b-roll is a legitimate, high-performing choice.

## When to cut to b-roll

- Cut on **concrete nouns, numbers, processes, evidence** — the word names something showable.
  Abstract/filler words get no cutaway. One cutaway per concept; never stack on every word.
- A cutaway also usefully **hides a jump cut** (a deleted pause/filler word).
- **Don't cut to b-roll to hit a quota** — "if you underline everything, nothing matters." A
  filler cutaway devalues the meaningful ones and the audience feels the padding.

## Timing grammar (frames @30fps)

- **Audio leads the picture (J/L-cut).** The creator's voice runs continuously; the b-roll
  *video* cuts in under it — never cut audio+video together. Lead ≈ **12 frames (~0.4s)** at the
  cut; a scene-level b-roll block can overlap up to ~2s (4s "creates confusion").
- **Align to the word it depicts** — start the insert on/just before the keyword; on the beat
  when music is present, else on visible subject motion.
- **Hold 1.5–3s (45–90 frames) for full-frame**; floor 45f (< that reads as a glitch). **5s
  (150f) is a hard ceiling** — no cited source holds footage longer. Panel/card (face visible)
  may run to the 5s ceiling; **never let a b-roll outlast the phrase it illustrates** (detaches
  the audio from its anchor and loses the viewer).
- **Spacing ≥ 3s (90 frames)** between cutaways; return to the face between them.

## Protect the face (never full-frame b-roll here)

- The **hook** (first ~90 output frames / 3s), any **punchline or reveal**, the **payoff** (the
  beat the whole take exists to deliver), the **CTA/closing line**, and first-person **emotional /
  eye-contact** beats. The viewer stays for the face on these; direct-to-lens gaze reads as more
  trustworthy and converts the CTA — generic b-roll there wastes the moment.
- `faceless` is the exception: b-roll IS the visual channel, so the hook can be b-roll.

## Budget the face (≤40% b-roll)

- **B-roll supports, does not replace, the face.** Cap total *face-hiding* (full-frame) cutaway
  time at **~40% of runtime** — the assembler drops trailing overflow. Burying the face past that
  strips the parasocial connection that makes viewers trust and follow. Panel/card keep the face
  on screen, so they don't count against this budget — prefer them when the face should stay up.
- Alternate moving b-roll with static shots; a wall of pans/zooms is disorienting.

## Full-frame vs partial (panel / card / split)

- **Full-frame** when the visual *is* the point and the words are illustrative (a demo, an
  object, a location, proof the viewer must see unobstructed).
- **Panel / card / split (face stays visible)** when you need the speaker AND the thing at once:
  software/screen demos, reactions, before/after, credibility moments. Keep the face at least
  partially visible on emotional/CTA beats — PiP, don't fully cover.

## Relevance bar (real > generic)

- B-roll must **literally depict** what's said at that moment; a mismatch "erodes credibility
  faster than almost any other editing mistake."
- **entity/data/evidence must show the ACTUAL thing** (own media) or fall back to a clean **text
  card** — generic/recognizable stock on a proof/claim moment signals *fake*. **No relevant asset
  → stay on the face, never decorative filler.** Reserve stock for non-proof (establishing,
  metaphor, transition) with a precise query.

## Audio

- **Mute b-roll under the voice by default** (edit A-roll audio first for flow, then lay muted
  b-roll on the cut points — the L-cut is why the voice never breaks). Optional: a *ducked*
  nat-sound layer (≈ −6 to −10 dB under speech, short fades) for shots where real sound adds
  presence, rising only in a speech gap.

## Match the a-roll

- Match palette + energy to the surrounding a-roll (the dossier gives look/energy); a bright
  hyper-cut b-roll under a calm story beat breaks immersion. Prefer own-media over stock.

Sources + reliability tiers: `docs/BROLL-PLACEMENT-SOURCES.md`. Mechanics are Tier-1; retention
percentages are Tier-3 marketing, deliberately not encoded.
