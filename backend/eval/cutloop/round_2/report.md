# Cut loop — round 2
P0=4 P1=9 | gate NOT MET | streak=0/2

## By class
- overcut_partial: 3
- undercut_dupe: 3
- cut_judge_seam: 2
- undercut_stumble: 1
- inconsistent_cuts: 1
- overcut_content: 1
- cut_judge_undercut: 1
- orphan_fragment: 1

## Findings
- [P0] take-40s overcut_partial @6.93s: cut mid-sentence: lost "do the 2 cuisines—" from "1, do the 2 cuisines—"
- [P0] take-41s overcut_partial @6.73s: cut mid-sentence: lost "the ones that—" from "The first 3 ingredients on the label are the ones that—"
- [P0] take-41s overcut_partial @23.17s: cut mid-sentence: lost "extra for for the packaging and" from "You're paying $74 extra for for the packaging and the vibe, not the fo"
- [P0] take-47s overcut_content @3.97s: cut sentence with unique content: "Here's the one that doesn't."
- [P1] owner-fusion undercut_dupe @8.97s: near-duplicate takes both kept: "most fusion fails for the same reason." ~ "most fusion fails for the same reason."
- [P1] owner-fusion orphan_fragment @35.57s: orphan kept fragment "the axes first." of "So I match the axes first."
- [P1] procut cut_judge_seam @35.5s: [s18] Marked as a partial keep ('So I match the axes first.'), indicating mid-sentence material was cut; as it stands it reads as an abrupt transi — "So I match the axes first."
- [P1] take-40s cut_judge_undercut @25.57s: [s7] This partial keep ('3, The one you skip.') is a stumble/false start that merely restates the phrase 'the one you're skipping' from sentence  — "3, The one you skip."
- [P1] take-41s undercut_stumble @6.73s: kept stumble "The first 3 ingredients on the label are the ones that—" restarts as "The first 3 ingredients on the label are the ones there's th"
- [P1] take-41s undercut_dupe @17.2s: near-duplicate takes both kept: "Flip the fancy one over." ~ "Now flip the drugstore one over."
- [P1] take-41s undercut_dupe @11.87s: near-duplicate takes both kept: "The first 3 ingredients on the label are the ones that—" ~ "The first 3 ingredients on the label are the ones there"
- [P1] take-41s inconsistent_cuts @Nones: kept-set similarity 0.82 across identical runs
- [P1] take-42s cut_judge_seam @0.17s: [s0] The partial keep truncates 'before I was [ready]' — the word 'ready' appears to have been cut, leaving 'before I was and that's the only rea — "I launched before I was and that's the only reason anyone showed up."