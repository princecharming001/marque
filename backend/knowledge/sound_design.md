# Sound design — beat-cutting, SFX, risers (rules with numbers)

## Density budget
- Talking-head: **3–5 SFX per 30s**, on the most load-bearing beats only. Constant SFX
  reads as noise; purposeful SFX reads as production value.
- Edit-heavy montage (recap_music): denser is allowed — a whoosh may ride cuts faster
  than ~0.8s, a bass hit lands the hook visual, one riser leads into the payoff.

## Placement
- The first sound must land inside the **first 1–1.5s** — it is part of the hook's
  five layers and inside the retention-prediction window.
- SFX must MATCH the visual action (whoosh = movement/transition; hit = reveal/claim;
  riser = build INTO a payoff, cut the riser dead at the payoff frame).
- Pop against dialogue or silence: short transient whooshes + sub-bass hits over speech;
  never layer SFX over a spoken load-bearing word.

## Beat-cutting
- When music is present, cuts land ON the beat (beat-timed cuts ≈ +23% completion);
  half-beat cuts acceptable in double-time sections.
- Duck music −8 to −12dB under speech, smoothed ±8 frames (no per-word pumping).
- Music bed −18 to −22 LUFS under voice; voice stays ~8–12 LU above the bed (−14 LUFS
  integrated delivery).

## Endings
- No SFX in the last 15 frames except a soft close hit; trailing dead-air ≤10 frames
  (loop-friendly endings flow back into the opening beat).

## Authoring (enforced in code — you don't place SFX yourself)

SFX are synthesized DETERMINISTICALLY (app/retention.py `synthesize_sfx`), not authored
per-clip: a whoosh lands at every transition/punch-in, budget-capped at ~5 per 30s of kept
footage, ≥15 frames apart, none in the last 15 frames — matching the density budget above
without needing an editorial decision from you. There is no `sfx` field in the plan schema;
this section exists so you understand what's already happening to the sound, not so you
configure it.
