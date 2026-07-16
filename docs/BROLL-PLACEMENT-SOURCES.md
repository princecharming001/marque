# B-Roll Timing & Placement — sourced study (2026-07-15)

The full web-research report behind `backend/knowledge/broll.md`. The knowledge file encodes
the **mechanics** (well-corroborated); this doc records the evidence and reliability tiers so a
future editor knows *why* each number is what it is and which claims NOT to trust.

## Reliability tiers (read before hard-coding any number)

- **Tier 1 — practitioner mechanics (encoded, trusted):** J/L-cut audio-leads-video & overlap
  timing, b-roll hold durations, edit-A-roll-first workflow, mute-under-voice, relevance/literal
  match, ~60:40 A-roll:b-roll. Corroborated across independent tool docs + editor guides.
- **Tier 2 — directional vendor doctrine (use the direction, not the exact figure):** face-first
  hooks, skip-b-roll-on-punchlines/reveals, "visual change every ~2–3s" (short-form) vs ~20–40s
  (long-form), ~40% b-roll cap. Internally consistent + widely repeated, but editorial.
- **Tier 3 — marketing stats (NOT encoded):** all retention percentages / view-multipliers
  (28%, 58% vs 41%, 4.2×, "60,000× faster", "0.2–0.5s visual-before-audio"). No primary platform
  data; at least one source self-admits it. Instrument and learn our own instead.

## The 8 areas → rule + numbers + sources

1. **Timing vs speech (J/L-cut):** voice leads, picture follows — never cut audio+video together.
   Offset video **1–10 frames (~4f subtle min)** to tighten a talking-head cut; **0.5–2s (2s sweet
   spot; 4s = confusion)** for a scene-level b-roll block. Align the insert to the exact word it
   depicts (on the beat with music, else on subject motion). *edicionvideopro.com/en/editing-
   techniques/j-cuts-and-l-cuts…, soundstripe.com/blogs/a-video-editors-guide-to-j-cuts-and-l-cuts,
   helpx.adobe.com/premiere/…/perform-j-cuts-and-l-cuts, learn.firecut.ai/features/remove-silences/
   j-cuts, knowlify.com/articles/a-roll-vs-b-roll, insidetheedit.com/blog/b-roll-editing-structure*
2. **Where vs not:** DEMAND b-roll on illustrable moments (concrete noun, number/stat, process,
   evidence, and to cover a jump cut). STAY on the face for the hook, punchlines/reveals, CTA,
   emotional/eye-contact beats — direct-to-lens gaze reads as more trustworthy & converts.
   *captions.ai/blog/practical-guide-b-roll-video, opus.pro/research/broll-visual-effects-short-
   form, storyblocks.com/resources/blog/a-roll-vs-b-roll-footage, researchgate.net (eye contact &
   video-mediated communication)*
3. **Density/cadence:** short-form — a visual change (cut, punch-in, caption, OR b-roll) every
   **~2–3s**, never a >5s dead block; long-form relaxes to every **~20–40s**. Don't cut to b-roll
   for a quota. *strategia-x.com/blog/2026-07-01-vertical-video-retention-editing-playbook,
   aibrify.com/blog/short-form-video-editing-captions-b-roll-guide, air.io/en/youtube-hacks/
   advanced-retention-editing-…, storyblocks.com, socialrails.com/blog/b-roll-footage-complete-guide*
4. **Full-frame vs partial:** full-frame when the visual IS the point; PiP/split/corner-card when
   you need speaker AND thing (demo, screen-share, react, credibility) — face in a lower corner,
   not centered. *cursa.app/en/page/b-roll-and-cutaways…, vavoza.com/split-screen-short-form-
   videos…, screenstory.io/blog/how-to-screen-record-with-facecam…*
5. **Relevance:** literal/motivated match only; mismatches "erode credibility faster than almost
   any editing mistake." Proof moments (demos, stats, your product/team) need real footage;
   generic/recognizable stock signals fake. No relevant asset → stay on the face. ~70:30 / 60:40
   custom:stock. *captions.ai, cursa.app, cloudixdigital.com/custom-b-roll-vs-stock-footage…,
   522productions.com/pros-and-cons-of-using-b-roll-vs-stock-footage*
6. **Audio:** mute b-roll under the voice by default (edit A-roll first); optionally a *ducked*
   nat-sound layer (~−6 to −10 dB under speech, short fades). *captions.ai, knowlify.com,
   creativecow.net/forums/thread/approach-to-editing-b-roll-audio, store.hollyland.com/blogs/
   creator-hub/do-audio-ducking-in-davinci-resolve*
7. **Hook (0–3s):** keep the face + a bold **3–8-word** hook text overlay; viewers decide in ~2–3s.
   Face-first vs motion-first splits by platform (Shorts favor face; TikTok/Reels reward a dynamic
   interrupt) — synthesis: face + motion + bold text, hook *words* on the face; never bury the hook
   under generic/static/slow b-roll. *capcut.com/create/short-form-video-hooks-first-3-second-
   patterns, opus.pro/blog/instagram-reels-hook-formulas, go-viral.app/blog/hook-first-3-seconds*
8. **Cheat-sheet numbers:** hold 1.5–3s (5s ceiling; establishing 2–4 / detail 1.5–3 / reaction
   1–2 / cutaway 2–5s); A-roll:b-roll ~60:40 (100% A-roll is valid); Hormozi-style jump cut every
   1–3s, punch-in 10–20%, captions 2–4 words, face ≥60% of frame (choppity); shoot 4–6× the b-roll
   you use. The "lead by 2–4 frames" exact phrasing is unverified (nearest real: FireCut 1–10f).

## What we encoded vs deliberately did NOT

Encoded in `knowledge/broll.md` + `assemble_edl` constants: J-cut lead 12f (~0.4s), hold 45–90f
full (150f/5s ceiling panel-card), spacing ≥90f, hook-protect 90f, CTA-protect 60f, ≤40% face-
hiding runtime budget, relevance→text-card→face fallback, mute-under-voice. NOT encoded: any
retention %/multiplier (Tier 3), and the exact cadence number is left to our own A/B (Tier 2).

---

## Part 4D — critical audit + the missing multimedia-learning literature (2026-07-15)

An adversarial re-appraisal of the earlier b-roll research. Headline: the *direction* survived but
the *confidence* was overstated, and one whole body of contradicting evidence (instructional design)
was absent. Three conclusions that changed the doctrine:

1. **The quantitative base is almost entirely correlational or vendor-supplied.** No public causal
   experiment isolates b-roll placement's effect on short-form retention. The "cadence optimum" is a
   correlational description (one paper, generic *shot changes* not b-roll specifically), not a causal
   target. Two of the highest-cited "facts" are TikTok's own ad-marketing (conflicted). → **Cadence is
   a starting PRIOR, not a target.** Demote all vendor %s to hypotheses (already Tier-3, kept out).
2. **The missing literature partly CONTRADICTS naive b-roll doctrine.** The *seductive-details effect*
   (Sundararajan & Adesope 2020 meta-analysis, Ed. Psych. Review; Rey 2012; Instructional Science 2023):
   interesting-but-irrelevant visuals **reduce** comprehension/recall — Mayer's *coherence principle*.
   So for informational/educational content, decorative or tangential b-roll is NOT neutral; it lowers
   message retention. "Fill every 4–5s" is actively wrong there.
3. **Relevance is the switch, not quantity.** *Dual coding* (Paivio) + *picture-superiority* + the
   *concreteness effect* (Brysbaert et al. 2014 norms; Childers & Houston 1984, J. Consumer Research):
   a RELEVANT concrete visual integrated with the message aids memory via two retrieval routes;
   irrelevant/decorative visuals harm it. The lever was never *how much* b-roll — it's *how relevant*.

**Recalibrated rules now encoded** (`knowledge/broll.md` Rule 0 + `edit_plan_prompt` b-roll section):
relevance is the PRIMARY gate (cadence secondary); a beat wanting a visual change but lacking a
relevant asset gets a **punch-in** (cheapest sufficient, face-keeping) or nothing, never decorative
b-roll; **format-conditioned** density (educational/explainer = high bar + sparse + literal-only;
entertainment/story = relaxed, mood b-roll ok); redundancy caution (dense captions → lighten
competing on-screen text — Mayer redundancy principle). Kept (now better grounded): concreteness gate,
semantic anchoring (event segmentation), perceptual timing constants, signaling/arrows (Mayer & Fiorella),
the ≤40% face budget / inverted-U governor.

**Still explicitly guaranteed:** when the creator EXPLICITLY opts into "Talking Head + B-roll", b-roll
still appears (the coverage=full path) — 4D governs WHERE it lands (most-relevant beats, format-aware),
not WHETHER it appears on opt-in.

**The only path to causal truth is the creator's own A/B tests** (research-gap register): cadence
dose-response, b-roll-vs-punch-in, relevant-vs-decorative, meme-on-info-beat, face-first-vs-b-roll-hook.
The per-creator learning loop is what converts this doctrine from "plausible" to "proven for them."

**Added independent peer-reviewed sources (highest trust):** Sundararajan & Adesope 2020
(link.springer.com/article/10.1007/s10648-020-09522-4); Rey 2012 (Educational Research Review);
Instructional Science 2023 (link.springer.com/article/10.1007/s11251-023-09632-w); Mayer & Fiorella,
Cambridge Handbook of Multimedia Learning (coherence/signaling/redundancy); Paivio dual coding +
picture-superiority; Childers & Houston 1984 (J. Consumer Research); Brysbaert, Warriner & Kuperman
2014 concreteness norms; Malodia et al. 2022 meme marketing (Psychology & Marketing). **Load-bearing
correlational:** Xue et al. 2026 MSV cadence model (arXiv 2604.19995) — peer-reviewed, organic, but
correlational + generic-shot + US-only. **Conflicted/vendor (directional only):** TikTok Creative
Codes / Neuro-Insight / Lumen / MediaScience (TikTok-commissioned), VidMob.
