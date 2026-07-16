# B-roll — when & where to place cutaways (code-enforced in assemble_edl)

Rules `assemble_edl()` enforces mechanically and `edit_plan_prompt` asks the model to honor.
Numbers reconciled 2026-07-15 against a sourced placement study (see "Sources" + reliability
tiers at the bottom — the *mechanics* below are well-corroborated; retention percentages are
marketing, not measurement, and are deliberately NOT encoded).

## Rule 0 — RELEVANCE is the switch (not quantity)

A **relevant, concrete** visual matching the words AIDS memory (dual coding); a **decorative /
tangential** one HARMS it — the *seductive-details* effect. Quantity was never the lever; relevance
is. Fire a visual only when a relevant concrete asset exists. A beat wanting a visual change but no
relevant asset → a **punch-in** (cheaper, face-keeping) or nothing, **never a decorative clip**.
Cadence (~1 change / 4–5s) is a loose PRIOR, not a target — prefer the **cheapest sufficient
change** over b-roll for a generic slot; don't add b-roll to hit a quota.

**Format-conditioned:** educational / explainer / how-to → protect comprehension: HIGH relevance
bar, SPARSE b-roll, literal illustrations only, no mood/decorative clips. entertainment / story /
hot-take → attention-first: bar relaxes, evocative b-roll allowed where it fits the beat.

Classify each line: **Illustrable** (names a concrete object/place/person, number/stat, process,
evidence) → candidate · **Face-protected** → never full-frame · **Neither** → stay on the face
(empty b-roll is a legitimate, high-performing choice). A cutaway also usefully hides a jump cut.

## Timing grammar (enforced in code — assemble_edl constants)

- **Audio leads the picture (J/L-cut):** voice continuous, b-roll *video* cuts in under it (~0.4s
  lead) — never cut audio+video together. **Align to the word it depicts.**
- Hold **1.5–2.5s** full-frame (Part-5: shorter — a face-hiding cutaway past ~4s is the biggest
  retention penalty; panel/card breathe to 3–5s). **Vary by beat**: a named thing/number → short
  (1.5–2s), a process/metaphor → longer (2–3s). **Never outlast the phrase.** Spacing **≥3s** (**≥2s**
  high-energy). The assembler enforces these per-need (`_BROLL_HOLD_POLICY`); keep ranges tight.

## Protect the face (never full-frame b-roll here)

- The **hook** (first ~90 output frames / 3s), any **punchline or reveal**, the **payoff** (the
  beat the whole take exists to deliver), the **CTA/closing line**, and first-person **emotional /
  eye-contact** beats. The viewer stays for the face on these; direct-to-lens gaze reads as more
  trustworthy and converts the CTA — generic b-roll there wastes the moment.
- `faceless` is the exception: b-roll IS the visual channel, so the hook can be b-roll.

## Budget the face (≤40% b-roll)

- **B-roll supports, does not replace, the face.** Cap total *face-hiding* (full-frame) cutaway
  time at **~40% of runtime** (assembler drops overflow) — burying the face past that strips the
  parasocial trust. Panel/card keep the face on screen, so they don't count — prefer them when the
  face should stay up. Alternate moving b-roll with static; a wall of pans/zooms disorients.

## Full-frame vs partial (panel / card / split)

- **Full-frame** when the visual *is* the point (demo, object, location, proof seen unobstructed).
  **Panel / card / split** (face stays) when you need the speaker AND the thing at once (screen
  demos, reactions, before/after); on emotional/CTA beats keep the face at least PiP-visible.

## Relevance bar (real > generic)

- B-roll must **literally depict** what's said; a mismatch "erodes credibility faster than almost
  any other editing mistake." **entity/data/evidence must show the ACTUAL thing** (own media) or
  fall back to a clean **text card** — generic stock on a proof moment signals *fake*. **No relevant
  asset → stay on the face.** Reserve stock for non-proof (establishing, metaphor) with a precise query.

## Memes / reaction inserts (`meme` need — entertainment only)

A **reaction/meme GIF** punctuates a punchline/hot-take (NEVER an info beat — seductive-details).
Entertainment takes only; **panel** (face stays), **short** (~1.5–2s), **≤2/video**. None → face.

## Audio + channel

- **Mute b-roll under the voice by default** — the continuous A-roll voice under the picture (L-cut)
  is why the cut never feels jarring. Match palette + energy to the a-roll; prefer own-media. When
  captions are dense, lighten competing on-screen text (redundancy principle).

Sources + reliability tiers: `docs/BROLL-PLACEMENT-SOURCES.md` (incl. the Part 4D audit: relevance
> cadence, seductive-details, dual coding). Mechanics are Tier-1; cadence is a correlational prior;
retention percentages are Tier-3 marketing, deliberately not encoded.
