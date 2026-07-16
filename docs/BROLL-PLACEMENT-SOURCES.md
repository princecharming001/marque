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

---

## Part 5 — 2026-07-15 retune research (shorter holds, cut more often, meme sources)

Owner ask: "b-roll should be shorter some places, cut more often" + "more culturally relevant
b-roll." Research-first mandate. Two research streams below (web + empirical), then the calibrated
constants. Discipline unchanged: only Tier-1/2 (measured / corroborated-mechanic) claims move
numbers; retention %s stay Tier-3 (recorded, not encoded); every constant stays inside a sanity band.

### 5.1 Measured shot-length / cadence distributions (the calibration base)

Four independent measured datasets converge on the same direction — modern high-retention short-form
is **faster-cut than our current 2–3s hold implies**, and the biggest retention penalty is any single
visual block that **outlasts ~4s**:

| Source (tier) | N | Finding |
|---|---|---|
| DEV "200 viral TikToks" (T2 — measured, blog) | 200 @≥1M views | ASL <1.5s → 4.2M median views; 1.5–2.5s → 2.8M; 2.5–4s → 1.4M; **>4s → 800K** (5× cliff). Per-niche ASL: education **2.8s** (longest), cooking **1.6s** (shortest), comedy **1.2–5.1s** (most variable), fitness **1.9–2.2s** (tightest). Explicit caveat: "means visual *change* every 1.5–2.5s (cut/zoom/text/prop), not literally cut b-roll faster." |
| Creedom "50 viral" (T2 — measured, blog) | 50 @≥10M views | Avg scene length **2.2s**; scenes **>4s → 15% retention drop** at 30s vs **2–3s → 7% drop**; pattern interrupt every 8–12s halves per-interval drop-off (22%→8%). |
| OpusClip "Anatomy of a viral TikTok 2026" (T2/T3 — vendor, 13.5M) | 13.5M clips | Viral-tier length median **41s** (18% shorter than overall); burned-in animated captions on ~every viral clip; hook/product in first 3s. (Length not hold, but corroborates "tighter wins.") |
| Xue et al. 2026 MSV (T1 — peer-reviewed, correlational) | — | Inverted-U on shot *count* — more cuts help up to a point, then hurt. Already cited (Part 4D); anchors the "don't cut for a quota" ceiling. |

**Practitioner-mechanic consensus (T2, ≥2 independent sources each):** Hormozi jump-cut every **1–3s**
on emphasis words, b-roll illustrative (1–3s). Cutaway-type holds: **reaction 1–2s, detail/close-up
1.5–3s, insert/cutaway 1–3s, establishing 2–4s** (choppity/riverside/sendshort/quso, shootsta,
storyblocks). Ling et al. 2022 (BU, 400 videos, T1): close-up/medium-shot scale correlates with
virality, large/wide scale anti-correlates — reinforces "cutaways short + tight, return to the face."

**Empirical spot-check (this session):** `eval/broll_cadence_probe.py` (new, rerunnable) —
ffmpeg scene-detect + optional Haiku face/broll vision classify. Machinery validated end-to-end on a
real short (2 cuts/1s, ASL 0.32s — an extreme fast-cut exemplar) and a synthetic self-test. A full
N≥15 corpus run was **not** completed: the prod `/v1/reels` niche caches were cold (populated lazily
via paid Apify scrapes), so no durable talking-head-with-b-roll corpus was available in-session. Per
the decision rule (N<15 → "measured, not encoded"), the encoded numbers below are calibrated to the
**published measured distributions above (N≈250 across DEV+Creedom + practitioner-type holds)**, kept
conservative within band. The probe is committed so a future audit can run it once caches are warm
(`python3 -m eval.broll_cadence_probe --from-api <base> --niche <n>`).

### 5.2 Meme / reaction-insert grammar (the "culturally relevant" source)

- **Source of footage = licensed API only** (GIPHY/Tenor), never scraped reels. Scraped IG/TikTok
  reels are copyright — usable only as a *query/trend signal*, never republished as footage.
- **GIPHY API terms (T1 — primary):** free API, but **every surface displaying GIPHY content must show
  "Powered By GIPHY" attribution**; commercial deployment needs a **production key** (app review +
  custom pricing). Tenor (Google) similarly requires "Via Tenor". → Owner action item; ships keyless
  fail-soft until a key is provisioned. (support.giphy.com/hc/en-us/articles/360028134111,
  developers.giphy.com/docs/api, .../360035158592 production-key conditions.)
- **Placement grammar (T2 practitioner + Malodia 2022 T1 meme-marketing):** a reaction/meme GIF is
  *commentary on the line* — it lands **on the punchline / hot-take / absurd stat**, stays **short**
  (a joke held too long dies — reaction 1–2s), and is **entertainment-class only**. On informational/
  educational beats it is the seductive-details effect made literal (Part 4D) → **banned there**.
  Composition: keep the face visible (the joke is the juxtaposition) → **panel**, not full-frame.
  Frequency ceiling: **≤2 per video** before it reads as try-hard.
- **Render note (T1 — our own infra):** GIPHY/Tenor serve **MP4 renditions** (`images.original.mp4`);
  use those. A raw animated `.gif` routes to Remotion `<Img>`, which does **not** time-sync animated
  GIFs on Lambda (parallel-chunk render → frozen/seamed frames). MP4 rides the existing OffthreadVideo
  path → zero render changes, no `PLAN_SCHEMA_VERSION` bump (`BRoll.source/mode` are plain strings,
  `need` never reaches the renderer).

### 5.3 Calibrated constants (encoded in `assemble_edl` + `knowledge/broll.md` + prompt, this commit)

Direction is T2-strong (4 measured datasets agree); exact frames stay conservative in-band. @30fps.

| Constant | Was | Now | Why (source) |
|---|---|---|---|
| `_BROLL_MAX_HOLD` (full mode) | 90f/3s | **75f/2.5s** | Caps a face-hiding cutaway below the >4s retention cliff (Creedom 2× drop) and at the top of the "<2.5s ASL" band (DEV). Band 60–90 ✓ |
| `_BROLL_MIN_HOLD` (full mode) | 60f/2s | **45f/1.5s** | Enables flash/detail/reaction inserts (reaction 1–2s, detail 1.5s); 1.5s is the legible floor for a J-cut cutaway. Band 36–60 ✓ |
| **per-need hold table** `_BROLL_HOLD_POLICY` | flat | see below | Institutionalizes hold *variety* (comedy ASL variance 1.2–5.1s; different beats want different holds). Named things/numbers read fast → short; process/metaphor needs to breathe → longer. |
| spacing "tight" row | — | **60f/2s** when `energy=high` OR entertainment video_type | Cooking 1.6s ASL / Hormozi 1–3s / entertainment tolerates denser cutting; educational stays 90f/3s (coherence). Band 45–90 ✓ |
| floor step divisor (coverage=full) | 360 (1/12s) | **270 (1/9s)** | Denser when the creator explicitly opts into b-roll ("cut more often"); floor also alternates min/mid holds for variety. Band 240–360 ✓ |
| J-cut lead | 12f | **12f (kept)** | Leads stay fixed (FireCut 1–10f); the `s_out−s_in ≥ min_hold` guard protects legibility on 45f flashes. |

`_BROLL_HOLD_POLICY` `(min_hold, full_max, partial_max)` frames: entity `(45,60,90)`, data `(45,60,90)`,
evidence `(45,75,120)`, action `(60,90,150)`, concept `(60,90,150)`, meme `(45,60,75)`. `full_max`
drives face-hiding cutaways (retention-critical → tighter); `partial_max` panel/card (face stays →
may breathe). Meme is capped at 75f even in panel (jokes die held).

**Still NOT encoded (unchanged discipline):** every retention % / view-multiplier above (Tier-3); the
"exact right cadence" (left to the creator's own A/B loop). Direction encoded, magnitudes stay
conservative. Research-gap register updated: *shorter-hold dose-response*, *meme-on-info-beat lift*,
*panel-meme vs full-meme* now have live flags (`BROLL_MEMES`) to measure per-creator.
