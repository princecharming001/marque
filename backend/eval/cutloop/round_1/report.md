# Cut loop — round 1
P0=9 P1=4 | gate NOT MET | streak=0/2

## By class
- overcut_partial: 5
- cut_judge_seam: 2
- overcut_content: 2
- job_failed: 2
- inconsistent_cuts: 1
- orphan_fragment: 1

## Findings
- [P0] owner-fusion overcut_partial @8.97s: cut mid-sentence: lost "most fusion fails for the same" from "most fusion fails for the same reason."
- [P0] owner-prev job_failed @Nones: source_unreachable
- [P0] owner-prev job_failed @Nones: source_unreachable
- [P0] take-40s overcut_partial @6.93s: cut mid-sentence: lost "do the 2 cuisines—" from "1, do the 2 cuisines—"
- [P0] take-41s overcut_content @6.73s: cut sentence with unique content: "The first 3 ingredients on the label are the ones that—"
- [P0] take-41s overcut_partial @11.87s: cut mid-sentence: lost "The first 3 ingredients on the label are" from "The first 3 ingredients on the label are the ones there's the most of."
- [P0] take-41s overcut_partial @23.17s: cut mid-sentence: lost "extra for for the packaging and" from "You're paying $74 extra for for the packaging and the vibe, not the fo"
- [P0] take-47s overcut_content @3.97s: cut sentence with unique content: "Here's the one that doesn't."
- [P0] take-47s overcut_partial @6.93s: cut mid-sentence: lost "So, so every recipe, so every recipe behind me says heat" from "So, so every recipe, so every recipe behind me says heat the pan, then"
- [P1] cook-a cut_judge_seam @35.5s: [s18] Marked as a partial keep; the sentence 'So I match the axes first' seems to have had material trimmed, leaving an abrupt reference to 'the a — "So I match the axes first."
- [P1] owner-fusion orphan_fragment @8.97s: orphan kept fragment "reason." of "most fusion fails for the same reason."
- [P1] take-41s inconsistent_cuts @Nones: kept-set similarity 0.86 across identical runs
- [P1] take-42s cut_judge_seam @0.17s: [s0] The partial keep drops the word 'ready' (or similar) after 'before I was', leaving a grammatically broken clause ('before I was and that's.. — "I launched before I was and that's the only reason anyone showed up."