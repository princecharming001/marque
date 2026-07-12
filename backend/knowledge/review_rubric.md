# Review rubric — self-review scoring (Phase 5b) and eval judge

Score an edit 0–100. Each dimension is pass/fail with a frame-anchored issue when it fails;
the self-review loop (SELF_REVIEW) and the eval scorecard judge both use this rubric.

## Dimensions

1. **Hook lands 0–3s** (≤ 90 output frames): the payoff/claim/curiosity is present in the open,
   not buried behind throat-clearing. Fail → `hook_late`, fix by dropping/reordering the intro.
2. **Caption sync ±3 frames**: captions match the spoken word within 3 frames, cover every kept
   word, and hide in silences. Fail → `caption_sync` / `caption_gap`.
3. **No jarring jump-cuts**: cuts land on word/beat boundaries (±3 frames), no mid-word chops, no
   sub-0.4s slivers. Fail → `sliver` / `mid_word_cut`.
4. **B-roll relevance & grammar**: cutaways match the concept, respect J-cut lead, 2–3s holds,
   ≥3s spacing, and never cover the hook/CTA face. Fail → `broll_*`.
5. **Audio levels**: ≈ −14 LUFS, music under voice, smooth duck, clean fades. Fail → `audio_levels`.
6. **No black / flash frames**: no strobe, no dropped-to-black frames, no unintended blanks.
   Fail → `flash` / `black_frame`.

## Scoring

- Start at 100; subtract per failed dimension weighted by severity (hook/caption sync are
  heaviest — ~25 each; b-roll/audio/flash ~10–15).
- Self-review triggers a single revision ONLY if score < `SELF_REVIEW_THRESHOLD` (70), and never
  on re-renders or manual tweaks.

## Output shape

`{score_0_100, issues: [{code, frame, fix_op}]}` where `fix_op` is drawn from the tweak-envelope
op set so it can be applied via `apply_edl_ops` before one final render.

## v2 additions

7. **Transition restraint**: ≤2–3 styled transitions per 30s, ≥90f apart, none mid-thought.
   Fail → `transition_spam`.
8. **Sound discipline**: SFX within budget (3–5/30s talking-head), first sound ≤1.5s,
   no SFX over load-bearing words, beat-timed cuts when music present. Fail → `sound_design`.
