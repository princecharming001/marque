# Pacing — cut cadence, energy matching, dead-air budgets

Operational rules. Numbers here are the source of truth; do not duplicate them in prompts.

## Cadence by video_type

The loader selects the row matching the brief's `video_type`. Frames @30fps.

| video_type | cut cadence | frames | note |
|---|---|---|---|
| entertainment | 1–2s | 30–60 | fastest; punch every beat, never linger |
| education | 2–4s | 60–120 | let a point land, but cut on completion |
| story | 2–5s | 60–150 | pace to the emotional arc, slower on payoff |
| promo | 1.5–3s | 45–90 | product beats punchy, CTA gets a hold |
| vlog | 3–5s | 90–150 | conversational; cut on scene/topic change |
| default | 2–4s | 60–120 | when video_type is unknown |

## By style

Per-composition pacing character (loader appends the row matching `style`).

- talking_head: cut only on filler / dead-air / a genuine flub; the take is the spine. Never chop mid-sentence.
- faceless: every b-roll beat is a cut; no shot held static > 3s; caption-driven rhythm.
- fast_cuts: hard cut on every enumerated line; inter-line silence ≤ 80ms (≤ 3 frames).
- split_three: three panels, most screen time to the strongest/last solution.
- green_screen: speech drives cuts; keep the speaker on-screen throughout; no mid-sentence cuts.
- broll_cutaway: talking head is the spine; each [broll:] cue is one cutaway with a ~12-frame J-cut lead.

## Energy-matched cadence

Cut cadence should track the dossier `delivery_curve`, not a fixed metronome:
- high-energy stretch (energy ≥ 0.7): cut toward the fast end of the video_type band.
- low-energy stretch (energy ≤ 0.3): either cut it (dead weight) or hold and let b-roll carry it.
- beat-timed cuts land on stresses/downbeats — measured ≈ +23% completion vs off-beat cuts.

## Dead-air budget

- Trim any inter-word gap > 350ms (talking_head/vlog) or > 80ms (fast_cuts/faceless beat seams).
- No single static frame held > 3s without motion (see retention.md — motion floor).
- Loop-friendly ending: trim trailing dead-air to ≤ 10 frames so the last frame cuts clean to the first.
