# Viral editing doctrine — the numbers that make short-form go viral

Distilled from cross-platform data studies + practitioner consensus (2026; sources in
`docs/research/VIRAL_EDITING_SOURCES.md`). Numbers here are editorial intent for the
EDL/plan author; the deterministic math lives in `app/edl.py` + `app/retention.py`.
The single-largest data point: after captions, **B-roll is the #1 retention lever**
(present in 6.0% of 13.5M analyzed clips — the most-used visual enhancement); **visual
hooks appear in only 0.04%** of clips, so a strong opening visual is a rare, cheap
differentiator.

## 1 · The first 3 seconds decide everything
- **Get to the hook immediately.** Intros appear in only 1.0% of viral clips and cause
  drop-off — keep any intro under 2 seconds, or skip it. The buried-hook pull (plan
  author already surfaces the strongest line to frame 0) is doctrine, not option.
- **Lead with a face or a close-up.** Faces earn ~28% more early retention than product/
  wide shots; close-ups outperform wide shots in the first 3s. Never open on b-roll.
- **Silent-viewer insurance:** on-screen text in the first 2s even when narration is
  clear — most feed views start muted.
- Protect the hook window: no b-roll cutaway, no speed-ramp, no cut inside the first
  ~1.2s of spoken hook (already enforced; keep it).

## 2 · A-roll pacing — cut to compress, never to mangle
- Target delivered pace ~150 words/min; the edit compresses dead air + filler so the
  *felt* pace lands nearer 165–175 wpm without pitch change (speed-ramp, not re-record).
- **Only speed up when it earns it:** low-information stretches and silence, never the
  hook or the payoff line. Spoken speed cap 1.35×; silence fast-forward up to ~3×;
  total compression ≤ 25% of runtime. (These are the `plan_pacing` numbers — keep them.)
- **Never cut mid-word or mid-clause.** Every cut boundary snaps to a transcript word
  edge; a gap that still carries speech energy is a missed transcript word, not dead air
  — protect it. A cut that changes meaning is worse than a slow moment.
- **Retakes:** when the creator flubs and re-delivers the same line, keep the better
  (usually later) take and drop the earlier one as one clean cut. A freestyle edit may
  OMIT weak tangents entirely for cohesion — completeness is not the goal, a tight
  watchable cut is.

## 3 · B-roll — the default visual layer (talking-head included)
- **Illustrate claims, stats, and story beats.** Pull the cutaway from the ~1.5s around
  the keyword it illustrates; 3–5s max per clip (longer pulls focus off the narrator).
- **Mix three kinds per ~minute:** literal (matches the word), metaphorical (evokes the
  feeling), atmospheric (sets the scene). Vertical-native footage; matched resolution to
  the A-roll (mismatch is a top retention killer).
- **J-cut / L-cut every insertion** (audio leads or trails the picture) so the cutaway
  never feels bolted on; cut on motion; ≥3s spacing between cutaways.
- **Never** let b-roll outlast the phrase it illustrates, and **never** cover a punchline
  or reveal — the viewer wants the face there. No stock clichés (handshake, keyboard,
  lightbulb), no watermarked/low-res footage.
- Prefer the creator's OWN imported media when it matches the cue; fall back to stock.

## 4 · Captions — always on, built for muted mobile
- Burn captions on every video; ~80% of feed watch time is muted.
- Readable at a glance: high contrast, one short line at a time (phrase grouping ~3
  words, not a wall), lower-third with negative space so it never covers the face or
  key b-roll.
- **Active-word emphasis drives retention** — the spoken word brightens/scales (clean),
  one-word-at-a-time punch (bold-word), or fill-as-spoken (karaoke). Match the style to
  energy: bold-word/karaoke for high-energy montage, clean for founder talking-head.
- Highlight the 1–2 payoff words per line (the number, the verb, the twist).

## 5 · Music & sound design
- **Founder talking-head:** subtle bed, ducked hard under speech (bed ≈ −18 to −20 LUFS
  under the voice), never competing. Upbeat-but-restrained; no lyrics over speech.
- **Recap/montage (fast_cuts):** music is the spine — **cut on the beat** (every hard cut
  within a few frames of a beat lifts completion), bed louder (no VO to protect).
- **Voiceover recap:** low music bed UNDER the VO (not silent) + a real visual channel
  (b-roll/text) — a faceless recap with no visuals is a dead frame.
- Match track energy/tempo to the content's tone and the creator's brand voice, not a
  random pick. Every seam gets a micro-fade so a cut never pops/clicks.

## 6 · Structure & retention devices
- **Open loop early, pay it off late.** Tease the payoff in the hook; deliver at the end.
- **Pattern interrupts** on a cadence (punch-in zoom, text sticker, b-roll, SFX) so the
  frame never sits static too long — but spaced, never every second.
- **End card / CTA:** action-oriented and short (outros appear in 4.6% of clips and drive
  follows/clicks); one ask, not a long sign-off.
- Text overlays (4.5% of clips) for the key stat/number/step — distinct from captions,
  positioned not to fight them.

## 7 · Format archetypes (which lever set to pull)
- **talking_head** — the take is the spine; light b-roll on claims, clean captions,
  restrained bed, tight filler/dead-air removal. Default founder format.
- **talking_head_broll** — same, heavier b-roll cadence; cutaways carry the visual noun.
- **recap_music (fast_cuts)** — montage, beat-cut, loud bed, bold captions, no face
  needed; every enumerated beat is a hard cut.
- **recap_voiceover (faceless)** — VO over b-roll/text, low bed, no face; guarantee a
  visual on every beat.
Pick the format to the content; when unknown, default talking_head with light b-roll.
