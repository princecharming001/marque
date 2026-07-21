# Craft — Cut Decisions (the canon, condensed for the edit planner)

Priority when criteria conflict — Murch's Rule of Six (In the Blink of an Eye,
published weights): Emotion 51% > Story 23% > Rhythm 10% > Eye-trace 7% >
2D plane 5% > 3D space 4%. Sacrifice UP from the bottom: never give up emotion
for story, story for rhythm, rhythm for eye-trace. A continuity flaw under the
right emotion is invisible; a perfect match under the wrong emotion is not.

Per-cut checklist — Thompson/Bowen's six elements (Grammar of the Edit): every
cut should deliver (1) NEW INFORMATION, (2) a MOTIVATION (a movement, an eye
shift, an off-screen sound, an elapsed beat), (3) a visibly DIFFERENT
COMPOSITION, (4) a different ANGLE (the 30° rule — closer than that reads as a
jump), (5) CONTINUITY of action, (6) SOUND continuity within a scene (ambience
carries across the cut; hard sonic contrast only at scene boundaries).
Thompson's list is the validator; Murch's order is the conflict policy.

Dmytryk's rules that bind here (On Film Editing): never cut without a positive
reason; when unsure of the exact frame, cut LONG not short; cut in movement
when movement exists; enter late, exit early — no dead heads or tails; cut for
performance values over continuity matches; substance first, then form.

Creator-mode jump cuts (mode-dependent — this pipeline is CREATOR mode):
exposed jump cuts are the accepted vernacular of single-camera talking-head
content — correct for removing pauses, fillers, flubs. Each resulting seam is
(a) covered by b-roll, (b) bridged by a punch-in scale change (a digital "new
angle" satisfying the 30° similarity test), or (c) left exposed at a CONSISTENT
cadence. In narrative/drama mode the same moves are violations (never strip a
performer's pauses — GoE #5); documentary cuts ums/ahs (GoE #33).

Placement finesse: prefer cut points at clause/thought boundaries (Murch's
blink — the audience blinks when they "get" the idea) and on loud transients
(GoE #37 — a cut on a loud sound hides inside the blink). At action edits,
advance the second shot a few frames (GoE #42) — never repeat frames.
Reactions/inserts start MID-clause, not after the period (GoE #6). After a run
of close shots, re-establish wide (GoE #23). Never leave an empty frame after
a subject exits (GoE #46).

```yaml
rules:
  - id: cut.murch_priority
    principle: "Conflicts resolve by Emotion>Story>Rhythm>Eye-trace>2D>3D (51/23/10/7/5/4)"
    source: "Murch, In the Blink of an Eye (2nd ed.)"
    enforce: prompt
  - id: cut.six_elements
    principle: "Every cut: new info, motivation, changed composition, changed angle, action + sound continuity"
    source: "Thompson/Bowen, Grammar of the Edit"
    enforce: prompt
  - id: cut.long_not_short
    principle: "Ambiguous out-points resolve LATE (keep the frame) not early"
    source: "Dmytryk rule 2"
    enforce: prompt
  - id: cut.jump_seam_policy
    principle: "Every filler/pause seam is covered (b-roll), bridged (punch-in), or exposed at consistent cadence"
    source: "Wisecut/Adobe interview workflow; GoE #5/#33 mode split"
    enforce: critic
  - id: cut.mid_clause_inserts
    principle: "Inserts/reactions enter mid-clause, not on sentence ends"
    source: "GoE working practice #6"
    enforce: prompt
```
