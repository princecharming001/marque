# B-roll — when & where to place cutaways (code-enforced in assemble_edl)

Rules `assemble_edl()` enforces mechanically and `edit_plan_prompt` asks the model to honor.
Numbers reconciled 2026-07-15 against a sourced placement study (see "Sources" + reliability
tiers at the bottom — the *mechanics* below are well-corroborated; retention percentages are
marketing, not measurement, and are deliberately NOT encoded).

## Rule 0 — RELEVANCE is the switch (not quantity)

A **relevant, concrete** visual matching the words AIDS memory (dual coding); a **decorative /
tangential** one HARMS it — it competes with the point (the *seductive-details* effect). Quantity
was never the lever; relevance is. Fire a visual only when a relevant concrete asset exists for the
beat. A beat that wants a visual change but has no relevant asset → a **punch-in** (cheaper,
face-keeping) or nothing, **never a decorative clip**. Cadence (~1 change / 4–5s) is a loose PRIOR,
not a target — prefer the **cheapest sufficient change** (punch-in / caption pop) over b-roll for a
generic slot; don't add b-roll to hit a quota.

**Format-conditioned:** educational / explainer / how-to → protect comprehension: HIGH relevance
bar, SPARSE b-roll, literal illustrations only, no mood/decorative clips. entertainment / story /
hot-take → attention-first: bar relaxes, evocative b-roll allowed where it fits the beat.

Classify each line: **Illustrable** (names a concrete object/place/person, number/stat, process,
evidence) → candidate · **Face-protected** → never full-frame · **Neither** → stay on the face
(empty b-roll is a legitimate, high-performing choice). A cutaway also usefully hides a jump cut.

## Timing grammar (enforced in code — assemble_edl constants)

- **Audio leads the picture (J/L-cut):** voice continuous, b-roll *video* cuts in under it (~0.4s
  lead) — never cut audio+video together. **Align to the word it depicts.**
- Hold **1.5–3s** full-frame (5s hard ceiling; **never outlast the phrase**), spacing **≥3s**,
  return to the face between. The assembler enforces all these numbers; the model just keeps ranges
  tight to the phrase.

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

- **Full-frame** when the visual *is* the point and the words are illustrative (demo, object,
  location, proof to see unobstructed). **Panel / card / split** (face stays visible) when you need
  the speaker AND the thing at once (screen demos, reactions, before/after) — and on emotional/CTA
  beats, keep the face at least PiP-visible, don't fully cover.

## Relevance bar (real > generic)

- B-roll must **literally depict** what's said at that moment; a mismatch "erodes credibility
  faster than almost any other editing mistake."
- **entity/data/evidence must show the ACTUAL thing** (own media) or fall back to a clean **text
  card** — generic/recognizable stock on a proof/claim moment signals *fake*. **No relevant asset
  → stay on the face, never decorative filler.** Reserve stock for non-proof (establishing,
  metaphor, transition) with a precise query.

## Audio

- **Mute b-roll under the voice by default** — the continuous A-roll voice under the picture (L-cut)
  is why the cut never feels jarring. (Optional ducked nat-sound layer: see docs.)

## Match the a-roll + avoid channel overload

- Match palette + energy to the surrounding a-roll; prefer own-media over stock. When captions are
  dense, lighten competing on-screen text on the b-roll (redundancy principle) — two heavy text
  layers at once overload the visual channel.

Sources + reliability tiers: `docs/BROLL-PLACEMENT-SOURCES.md` (incl. the Part 4D audit: relevance
> cadence, seductive-details, dual coding). Mechanics are Tier-1; cadence is a correlational prior;
retention percentages are Tier-3 marketing, deliberately not encoded.
