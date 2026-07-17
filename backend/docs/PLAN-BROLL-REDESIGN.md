# B-Roll Redesign — From Scratch (v7 plan, 2026-07-17)

Grounded in a frame-by-frame audit of a real failed render (job `41a4579c` successor,
2026-07-17T16:08 — corn puffs accepted for "gochujang jar / bold red paste closeup",
abstract 3D render for "fat acid heat", washed-out white stock in tiny insets, a
floor-synthesized "seasons" → "four seasons montage" cue, zero memes at meme level 2)
plus a 32-agent research sweep with adversarial verification of every load-bearing
claim. Full evidence ledger at the bottom.

## The three root causes (all proven, none of them "Pexels is bad")

1. **The vision judge is architecturally broken.** It is a listwise
   "pick-the-best-thumbnail (or −1)" prompt — a *forced-choice* design. Research
   verdict: REFUTED as a workable pattern. Instruction-tuned VLMs pick from garbage
   (documented 8% rejection rate vs 96.5% for base models, arXiv 2409.00113) and
   yes-bias toward plausible co-occurrences (POPE) — an orange-ish snack pile for a
   food query is exactly the documented failure. No source swap fixes this.
2. **Cues are emitted for nouns, not narrative jobs.** The density floor turns bare
   words into cues ("seasons"); concepts get sent to a *footage library* ("fat acid
   heat infographic"); memes key off nouns so humor beats never produce them —
   GIPHY provably HAS gochujang/spice content; the cue side never asked.
3. **No nativeness pipeline.** Raw bright-white stock is dropped onto a warm
   handheld a-roll with no grade match, no grain unification, no foley — the #1,
   #2 and #6 most-cited "reads as stock" tells.

## Design principles (each traced to verified evidence)

- **B-roll is not the default filler.** Top editors' visual layer is kinetic
  captions + punch-ins + designed graphics; footage cutaways are sparse and
  motivated. A refusal (punch-in instead) is a first-class outcome.
- **Route by beat class, not noun**: `truth_noun` (physical named thing) →
  accuracy-gated footage/still · `concept` → designed animated card, never stock ·
  `punchline` → meme/reaction clip · `mood` → grade-matched stock allowed.
- **Wrong subject is worse than no asset** (viewer-churn evidence; pre-verbal
  detection in food content). Verification must be reject-biased.
- **Two acceptable looks only**: (a) grade/motion-matched "cinematic", (b)
  recognizable memetic. The generic middle is squeezed out (2026 polarization).
- **Tight compositions sized for the surface** — subject ≥ ⅓ of frame at inset
  size; food shot grammar (macro texture / overhead prep / 45° stovetop).
- **Stills + motion beat generated video**: the whole market (OpusClip, VEED,
  Captions, Submagic) generates IMAGES + Ken Burns, not video. $0.05–0.08 per
  usable insert vs $0.30–1.50+ and 30s–6min latency for video-gen (which garbles
  Hangul labels and melts food — the gochujang worst case).

## Architecture: cue → asset ladder

**Stage A — planner.** Typed cues `{beat_class, anchor_word, narrative_job,
register}`; kill bare-noun floor synthesis (floor may only propose from load-bearing
script content w/ a justification field); separate meme pass classifying
punchline/hot-take/absurd beats at meme_intensity-scaled cadence.

**Stage B — tiers** (first pass wins):
1. Creator's own media (embedding match)
2. GIPHY + KLIPY for `punchline` (+ memey truth_nouns) — keys armed; coverage proven
3. **Generated concept card** (Remotion: ≤3 words + simple diagram, brand type,
   beat-animated) — `concept` always lands here
4. **Flux 1.1 Pro still + Ken Burns/parallax** — the default generated tier for
   truth_nouns ($0.04/still, gated by the existing vision check before use)
5. Stock: **Shutterstock** (661 gochujang videos, API) > Pexels (demoted) >
   **Wikimedia Commons stills** (83 gochujang closeups, free, CC-BY/PD only).
   Query = subject noun + cinematic descriptors + register; 30–80 candidates.
   Pixabay only with a query-echo guard (it silently substituted "Gochang" county).
6. Image-to-video (Runway Gen-4 Turbo / Kling, from an approved Tier-4 still) —
   max 1–2 flagged *hero* cues per video
7. **Refusal** → punch-in / caption emphasis. Always acceptable.

**Stage C — verification** (stock + stills): ① local CLIP/SigLIP embedding
pre-filter over all thumbnails (kills corn-puffs-grade misses for ~free) ②
**pointwise calibrated scoring** — one independent Haiku call per candidate, 0–100
against a full compositional description PLUS must-attribute booleans including
**expected-false discriminators** ("are these puffed dry snacks?" must be NO) ③
legibility gate (subject ≥ ⅓ frame, closeup bias) ④ nativeness gate
(lighting/warmth class vs a-roll reference frames). Accept iff score ≥ threshold
AND all MUSTs — never argmax below threshold.

**Stage D — conform pass** (render): color-match toward the a-roll grade + unified
grain + subject-class foley (sizzle/pour/whoosh, low under voice) on EVERY external
insert; durations varied (1.5–3s footage, 1–2s memes, 2–4s cards); full-frame
takeover available for hero moments (insets aren't the only treatment); never
occlude the caption band.

## Kill list (current behaviors the evidence refutes)

listwise vision pick · affirmative-only rerank · keyword-API-with-no-embedding ·
bare-noun floor cues · concept-as-footage-search · Pexels-as-primary · noun-keyed
memes · ungraded/silent inserts · wide scenics at inset size · uniform hold
durations · Higgsfield for routine inserts (reroll economics) · any
YouTube-CC/TikTok scraping (legal minefield).

## Build order

- **P0 (fixes the observed disasters, zero new vendors):** pointwise scorer +
  expected-false discriminators; CLIP pre-filter; kill bare-noun floor cues (typed
  cues + justification); concept cues → text-card path (already exists — route to
  it); meme pass keyed to humor beats; legibility gate.
- **P1 (nativeness):** conform pass — grade match + grain + foley layer; duration
  variance; full-frame hero treatment.
- **P2 (sources):** Flux still + Ken Burns tier (fal.ai API); Wikimedia Commons
  stills client; Shutterstock Media API eval + Storyblocks free test key probe;
  KLIPY clips smoke test with the armed key.
- **P3 (hero tier):** image-to-video from approved stills, 1–2 per video, flagged
  in the EDL for the editor to keep/kill.

## Shutterstock eval — owner runbook (decision: run in parallel with free tier)

1. Create a developer account at developers.shutterstock.com → apps → new app
   (choose "Media API"); sandbox key is instant, production licensing requires the
   sales form ("API — video licensing, programmatic, ~N clips/mo").
2. Paste the sandbox key as `SHUTTERSTOCK_KEY` in Render env — the client wires
   the same as Pexels (`GET /v2/videos/search?query=...&per_page=20`,
   `Authorization: Bearer`); resolution stays gated by the pointwise scorer.
3. Eval criteria before signing anything: niche hit-rate on 20 real cue queries
   from prod jobs (target ≥60% usable vs Pexels' observed ~20%), per-clip license
   cost at footage tier, portrait/vertical share of results.

## Owner decisions needed

1. **Shutterstock Media API** is paid (sales-contact pricing) — worth an eval call,
   or stay free-tier (Commons + Flux stills) first? (Plan works either way; P2
   starts free.)
2. Flux/fal.ai key for the still-generation tier (~$0.04/insert).
3. Default insert treatment for truth moments: keep smart-inset or adopt full-frame
   hero takeovers (research says pros mostly go full-frame; you now can drag/resize
   either way in the editor).

*(Evidence ledger E1–E16 + source audit table + generation economics: see the
research synthesis appended to the workflow run `wf_da9314e8-313`; key items
inlined above.)*
