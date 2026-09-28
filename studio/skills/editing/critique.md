# Critiquing an edit

Load this file at every review round: after each render in the champion loop, after any chat edit, when ranking story cuts or hook alternates, and whenever the Director weighs whether a note is worth acting on. It covers how to watch a render, what to check, how to write notes the engine can act on, how to separate defects from taste, how to compare versions fairly, and the biases of AI judges. The craft rules live in the topic files; this file judges the result. It guards against critique that drives over-editing: critics that always find something, and fixes that add more.

## Principles

1. **Judge the render, not the plan.** *Why:* clicks, clipped words, text collisions, music masking and rhythm exist only in the encoded file, and a plan always looks right to its author.
2. **Watch it the way the viewer will: final encode, phone size, 1.0×, sound on, one uninterrupted pass before any notes.** *Why:* seams, lip sync and pacing are real-time phenomena, and the first impression is gone once you start scrubbing.
3. **The master test is attention: does anything pull the eye or ear off the speaker without paying for it?** *Why:* faces, text and motion onsets grab attention automatically, and irrelevant detail lowers learning [L]. Good technique goes unnoticed.
4. **Defects before taste.** *Why:* a defect is something any viewer would call broken; a taste note is one reasonable option among several, and taste notes are where critics push their own preferences and pile on edits.
5. **Every note is localized, evidenced and written as engine ops.** *Why:* model timestamps are nearly useless (tIoU ≤ 0.11 [L]); a note without word, gap, insert or segment IDs cannot be applied or checked.
6. **Name the problem and its likely cause, offer a fix, and let the Director decide.** *Why:* viewers are usually right that something is wrong and often wrong about the remedy (Gaiman); Pixar's Braintrust diagnoses without authority because symptoms often come from somewhere else in the film [P].
7. **"Ship it" is a valid verdict, and "remove" beats "add" when in doubt.** *Why:* a professional made 1 cut where the old engine made 19 [I], and models that revise without outside evidence can make things worse [L].
8. **Compare, don't score, and swap positions every time.** *Why:* pairwise judgments are steadier than absolute scores, and LLM judges flip with order [L].
9. **Distrust your own family, your own work and any preference for "more".** *Why:* judges favor their own outputs and longer, busier answers [L]; agents grading their own work tend to praise it [P]; for video, "more edits" reads as "more effort".
10. **Judge against this creator's brief, energy and memory, not a house template.** *Why:* even TikTok's ad guidance asks for a "DIY or not overly polished style" so content fits in among creators [A], and creator memory overrides doctrine defaults.
11. **Watch as someone who never heard the cut words.** *Why:* people cannot set aside what they know when judging what others will understand (the curse of knowledge) [L]. The Director has heard every deleted clause and fills gaps the viewer falls into; the naive pass exists for this.

## Defaults and ranges

Starting priors. Only the invariants in ARCHITECTURE §7 are gates; every other number here is a reason to look closer.

| What | Prior | Tier | Source |
|---|---|---|---|
| Review copies | The final platform encode (never the proxy) at 1.0× on a phone-sized display, plus a copy with burned-in word IDs and frame numbers for localizing. Frame numbers raised a video model's grounding mIoU from 12.5 to 31.3 (Qwen2-VL-7B, ActivityNet, no training) | [X] [L] | [NumPro](https://arxiv.org/abs/2411.10332) |
| Model watcher sampling | Gemini samples video at 1 fps by default and processes a video's audio at about 1 kbps mono (standalone audio files get 16 kbps), so it misses a 1-frame flash, a 2-frame jump or a click. Send ±1 s seam slices at native fps | [V] | [Gemini docs](https://ai.google.dev/gemini-api/docs/video-understanding) |
| Stray frame at a seam | A defect, even for a single frame: people detected a named picture above chance at 13 ms per picture (threshold contested) | [L] | [Potter 2014](https://link.springer.com/article/10.3758/s13414-013-0605-z) |
| Attention cost of text | In free viewing, faces and text drew 16.6× and 11.1× more looks than size- and position-matched regions, and viewers told to avoid them still struggled to. Motion onset also captures attention | [L] | [Cerf 2009](https://doi.org/10.1167/9.12.10), [Abrams & Christ 2003](https://doi.org/10.1111/1467-9280.01458) |
| Decorative inserts | Interesting but irrelevant detail lowered learning (g = −0.33) | [L] | [Sundararajan & Adesope 2020](https://link.springer.com/article/10.1007/s10648-020-09522-4) |
| Hook | First speech or payoff by 2.5 s; proposition in the first 3 s | [V] n=13, [A] ads | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws), [TikTok](https://ads.tiktok.com/help/article/creative-best-practices) |
| Sound on and off | 69% of US adults say they watch video with sound off in public (self-report, 2019); 88% of TikTok users call sound "vital" (Nielsen for TikTok, 2020). Review both ways | [A] | [Verizon/Publicis](https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/), [TikTok/Nielsen](https://ads.tiktok.com/business/en-US/blog/evolution-of-sound-volume-1) |
| A/V sync | Detectable at +45 ms (audio early) and −125 ms (audio late); unacceptable beyond +90 / −185 ms | [L] ITU subjective tests | [ITU-R BT.1359](https://www.itu.int/rec/R-REC-BT.1359) |
| Seam audio | Level changes at an edit above about 4–5 dB start to be audible as the noise floor shifts (a classical-editing rule of thumb, so an upper bound). Never digital silence | [P] | [ebrary (Decca guide)](https://ebrary.net/300232/education/crossfades) |
| Loudness | Gate (ARCHITECTURE §7): −14 LUFS ±1 LU integrated, true peak ≤ −1 dBTP, measured on the encode; mastering aims tighter, at ±0.5 LU. −14 is a platform-matching choice, not a standard: AES TD1008 recommends −18 LUFS for speech streams | [I] + [P] | `voice-and-loudness.md`, [AES TD1008](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf) |
| Music under speech | Starts 16–20 LU under the voice; never within 10 LU. A word lost on the phone-speaker simulation is a defect whatever ESTOI says | [V] + [I] start; [L] floor | `music.md`, [pystoi](https://github.com/mpariente/pystoi) |
| Caption reading | ≤20 characters per second. Synced pages follow the speech rate, so a fast page is a reason to re-page or check speed, never to delete words | [P] | [Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide) |
| Flashing | At most 3 flashes in any 1 s | [P] | [WCAG 2.3.1](https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html) |
| Ending | Last kept word to last frame 0.15–0.5 s on a face; 1.5–2 s only when the held frame shows something new. All 13 measured top Shorts cut right after the final payoff word | [I] [V] | `endings-loops-ctas.md`, [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws) |
| Seams per 60 s, clean single take | 1–4: a reason to ask, never a gate | [I] n=1 | `backend/eval/pro_cut_reference.py` |
| AI judge vs human | On generative audio-video edits, model judges agreed with human pairwise votes 64–67% of the time, against 71.1% between humans | [L] preprint | [AVENUE](https://arxiv.org/pdf/2609.04253) |
| AI judge hearing | Audio-LLM judges trail humans by 32 points on average across style, rate, emphasis, age and gender, and fail worst on ties, where they should abstain. Claude takes no audio at all | [L] preprint | [ParaPair](https://arxiv.org/abs/2606.24648) |
| Position bias | Swap consistency on near-identical answers: GPT-4 65.0%, Claude-v1 23.8% (2023 models). With ChatGPT judging, reordering alone let Vicuna-13B beat ChatGPT on 66 of 80 queries. Near-identical versions are exactly the champion-loop case | [L] | [Zheng 2023](https://arxiv.org/abs/2306.05685), [Wang 2023](https://arxiv.org/abs/2305.17926) |
| Verbosity bias | Answers padded with a restated list beat the originals 91.3% of the time with Claude-v1 and GPT-3.5 judging, 8.7% with GPT-4. Length control raised Arena correlation from 0.94 to 0.98 | [L] | [Zheng 2023](https://arxiv.org/abs/2306.05685), [Dubois 2024](https://arxiv.org/abs/2404.04475) |
| Self-preference | GPT-4 gave its own outputs +10 pp win rate and Claude-v1 +25 pp (inconclusive). Self-recognition tracks self-preference linearly | [L] | [Zheng 2023](https://arxiv.org/abs/2306.05685), [Panickssery 2024](https://arxiv.org/abs/2404.13076) |
| Mitigations | Generated yes/no checklists raised exact judge–human agreement from 46.4% to 52.2% over direct scoring. A mixed-family jury reached κ 0.763 against 0.627 for a single GPT-4 judge | [L] | [TICK](https://arxiv.org/abs/2410.03608), [PoLL](https://arxiv.org/abs/2404.18796) |
| Human blind pairs | About 200 independent pairs to detect a 60/40 preference and about 800 for 55/45 (two-sided α 0.05, 80% power). Inflate for shared clips and raters | [X] binomial power | — |

## How to decide

1. **Gather inputs.** The final encode and its word-ID review copy; the brief and the binary rubric derived from it; the transcript with IDs; the metrics packet from `studio.qa` (click detector, ASR round-trip diff, LUFS and true peak, ESTOI on full-band and phone-speaker mixes, seam level jumps, face-box deltas at seams, CPS, safe-zone collisions, end gap); the current champion.
2. **Naive pass.** Use a fresh context with no brief and no EDL. Watch once, straight through, at 1×, sound on. Write at most three gut reactions: the moments attention left the speaker, where you wanted to swipe, and the one sentence you remember. Compare that sentence with the brief's one idea. A mismatch is a story problem that no finishing note can fix.
3. **Muted pass,** with the TikTok, Reels and Shorts UI overlaid. Can captions and the hook title carry the video alone? Is any text on the face or under the UI?
4. **Listening pass.** Review every seam slice (±1 s), the music under speech, and the ending. Claude cannot hear, and model listeners hear at low bitrate, so fidelity questions go to the metrics. The watcher answers only gross questions: is a word lost, is the music noticed, is a join abrupt?
5. **Rubric pass.** Answer each binary item from the brief plus the critic questions of every topic file used in this edit: yes, no, or "can't tell". Concrete yes/no questions beat scores (TICK, above), and question-driven critique gives a revision something to act on (VQQA) [L]. "Can't tell" goes to a metric or a second critic; don't guess.
6. **Grade severity.**
   - **P0 (defect, blocks shipping; most are objective QC items as in the EBU catalogue):** a clipped or missing word, click, drop-out, level jump, A/V drift, a flash or black frame, a b-roll insert showing the wrong subject, a misspelled name or an on-screen number that differs from the spoken one, text over the eyes, mouth or platform UI, a lost payoff or CTA, a flash-rate violation, or a word masked by music.
   - **P1 (a quality loss most viewers would feel):** a late or buried hook, a dead stretch, a visible jump mid-thought, an insert on an emotional line, music that competes with the voice, an unreadable caption page, a lingering ending.
   - **P2:** polish or taste.
   - A P0 or P1 stands only when a metric or a critic from another model family confirms it. With no other family available, a fresh-context house critic can confirm, marked `same_family`, and the Director weighs it lower. A creator's own note needs no confirmation: it is the brief.
7. **Write the notes.** Each note gets one issue, in this shape: `[severity][area][IDs] what the viewer experiences → likely cause → suggested ops → evidence → confidence`. Quote the spoken words. Send about five, consolidated, ordered from the top of Murch's list down (emotion, story, rhythm, eye-trace, planarity, space) [P]: a story fix can make every polish note below it moot, and when emotion and story work, viewers forgive the lower items. When critics disagree, sacrifice from the bottom of that list. If more than about five P1s remain, say once that the problem is upstream (the story cut) and stop listing symptoms. End with one or two moments that work and must survive the revision.
8. **Separate taste from defects.** Ask whether the creator, a viewer and a professional editor would all call it a mistake. If so, it is a defect. If a reasonable editor with this brief might choose otherwise, it is taste. Phrase taste as an option with its reason, never as a requirement, and never against creator memory. A taste note that adds an element must name that element's job.
9. **Compare pairwise.**
   - Render both versions at the same encode and matched loudness, labeled A and B, with no hint which one is new.
   - Ask per-area verdicts first (story, hook, pacing, seams, captions, b-roll, audio, color, ending; "same" allowed), then an overall verdict. Structured judging cut self-preference by 31.5% [L].
   - Run both orders. A win counts only when a version wins in both orders; an inconsistent result is a tie.
   - Use at least two judges from different families, never the Director's family alone. Accept a revision only if both judges prefer it and no metric regresses.
   - A tie keeps the champion. The exception is a champion that fails an invariant: it is not a champion, so the fixed version replaces it unless it loses.
   - Use stripped versions as controls: the no-music master and, when a round added inserts, zooms or SFX, the same cut without them. If the edit cannot beat its stripped version in both orders, the added layer has not earned its place [X].
10. **Close the loop.** The Director maps notes to ops and re-renders. Stop after two rounds with no pairwise win. Finish with one continuous 1× watch of the final encode that asks only the attention question. When the creator later shares retention graphs, compare the dips (skips and exits [A]) with where the naive critic wanted to swipe, and record the gap in creator memory.

## When to break it

- **Stylized formats** (a meme edit, the full Hormozi package, a listicle's per-item cuts): the technique is part of the content. Ask whether it serves the joke or the count, not whether it is noticed. Defects still block.
- **Deliberate roughness:** a kept stumble, a laugh or an intentional jump cut is not a defect. Check creator memory before writing a note.
- **Sub-perceptual differences** (a 40 ms pause, a 2% scale change) do not justify a jury round. Call it a tie.
- **Frame-stepping and slow playback** may confirm a suspected defect after the 1× pass. They are the wrong tool for finding pacing or taste problems, because no viewer watches that way.
- **When metrics and ears disagree,** as when ESTOI passes but a word is lost on a phone speaker, trust what the listener lost once an ASR run on the phone-simulated mix confirms it.
- **Creator chat edits:** the instruction is the brief. Critique checks that it was carried out cleanly, not whether the creator was right; state a cost once, and push back only on an invariant.

## Worked example

A calm founder, 34 s, for Reels. The brief's idea is "price for the customers you want."

> w0003–w0014 "Most founders raise prices too late, and it's not because they're scared." · w0015–w0031 "It's because they're pricing for the customers they have, not the ones they want." · w0032–w0060 "When we went from forty-nine to two hundred a month, we lost eleven customers, and revenue went up thirty percent." · w0061–w0072 "So raise it, watch who leaves, and build for who stays." · w0073–w0081 "Follow for part two on how we told them."

IDs come from the source transcript, so cut fillers and retakes leave gaps in the numbering. The champion is r2. The naive critic's reactions to challenger r3: "Attention left her at the cash-register clip. The +30% arrived after she said it. The ending felt cut off." Its one remembered sentence matched the brief.

| Note | Decision | Why |
|---|---|---|
| [P0][audio][w0081 "them"] The last ~60 ms is missing; the ASR round-trip reads "told the". Fix: extend seg009's out-point past the word's release into the trailing silence and hold 0.3 s | Fix | Invariant failure, confirmed by metric |
| [P1][b-roll][i002, w0040–w0046 "we lost eleven customers"] A generic cash-register clip pulls the eye during her one vulnerable line. Fix: `remove_insert(i002)` and stay on her face | Fix | Seductive detail on personal experience; the other-family critic agreed |
| [P1][text][i003] The "+30%" card enters at w0058 "percent", about 0.6 s late. Fix: anchor it at w0056 "thirty" | Fix | Measured against word onsets |
| [P2][captions][w0026–w0029] The page break "FOR THE / CUSTOMERS" splits the article from its noun. Fix: re-page | Fix | Cheap, objective, adds nothing to the screen |
| [P2 taste][sfx] "Add a pop on the card" | Decline | No job; calm creator; the card already lands on "thirty" |
| Keep: her half-smile after "went up thirty percent" | Protect | The line's emotional beat; no fix may trim it |

r3 failed an invariant, so the fixes became r4, judged against r2. The Gemini watcher and a non-Claude frame judge each preferred r4 on b-roll, text and ending in both orders and called seams and color "same". r4 became champion. Its only open note was P2, the final 1× watch found nothing, and it shipped. Removing an insert was the round's biggest win.

## Anti-patterns

- Scores or vague notes with no IDs or evidence: "engagement 7/10", "pacing feels off", "make it pop".
- Taking "at 0:12" from a model and treating it as an edit coordinate.
- Notes that always add: b-roll every 3 s, zooms "for energy", music "to fill".
- Reviewing contact sheets, the proxy, or playback at 2×, or judging color by model eye on downscaled stills (VLM color perception is weak [L]; read measured face luma).
- Treating a symptom at the wrong layer, such as covering a sagging middle with b-roll when the story cut needs a trim. Catmull's image: operate on the knee when the pain comes from fallen arches and you add to it [P].
- Critic inflation: a fresh P1 every round and never a "ship it".
- Letting a judge see which version is new, running only one order, or having Claude alone judge Claude's edit.
- Taste notes that overrule creator memory, or [I] calibration numbers treated as gates.
- Returning a rewritten edit instead of notes.

## Critic questions

1. On the first uninterrupted 1× watch, did attention ever leave the speaker for something that was not the point being made?
2. By about 3 s, with sound on and again muted, does the viewer know the topic and have a reason to keep watching?
3. Is any word or word edge clipped, or is there a click, drop-out, or level or noise jump at any seam?
4. Is there a visible jump inside a thought that is neither covered nor clearly intentional?
5. Is there a stretch where you wanted to swipe: dead air, repetition, or a static run with nothing new?
6. Does every insert show what the voice names, arrive on the word and leave before it outstays its job?
7. Muted, can every caption page be read in time, with no typo, bad break, or text over the face or platform UI?
8. On a phone speaker, is every word understandable, and is the music never noticed during speech?
9. Do skin, exposure and white balance look natural and match across takes and inserts, with no grey, washed-out or blown HDR conversion?
10. Is there any flash frame, black frame, frozen frame or lip-sync drift?
11. Does the video end within about 0.5 s of the last word (or on a held frame that shows something new), with the CTA intact and a clean replay cut to frame 0?
12. Would the creator recognize this as themselves, with their energy, laughs and catchphrases intact, rather than a template?
13. Take away the newest element (insert, zoom, SFX, card or music): would a viewer miss it? If not, the best note is to remove it.

## Sources

- [Murch, rule of six](https://blogs.ischool.berkeley.edu/i290-viznarr-s12/the-rule-of-six-walter-murch/)
- [Catmull, inside the Pixar Braintrust (Creativity, Inc. excerpt)](https://www.fastcompany.com/3027135/inside-the-pixar-braintrust)
- [Gaiman, rules of writing (rule 5)](https://www.themarginalian.org/2012/09/28/neil-gaiman-8-rules-of-writing/)
- [Frame.io, consolidating client notes](https://blog.frame.io/2022/04/18/10-steps-for-client-approval/)
- [Anthropic, separate generator and evaluator](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [Zheng 2023, MT-Bench judge biases](https://arxiv.org/abs/2306.05685)
- [Wang 2023, LLMs are not fair evaluators](https://arxiv.org/abs/2305.17926)
- [Panickssery 2024, self-preference](https://arxiv.org/abs/2404.13076)
- [Dubois 2024, length-controlled AlpacaEval](https://arxiv.org/abs/2404.04475)
- [Huang 2024, LLMs cannot self-correct yet](https://arxiv.org/abs/2310.01798)
- [TICK, checklist evaluation](https://arxiv.org/abs/2410.03608)
- [PoLL, juries of judges](https://arxiv.org/abs/2404.18796)
- [SPB, structured evaluation vs self-preference](https://arxiv.org/abs/2604.22891)
- [VISTA, pairwise champion selection](https://arxiv.org/html/2510.15831)
- [VQQA, question-driven critique](https://arxiv.org/abs/2603.12310)
- [VideoArgus, per-sample rubrics](https://arxiv.org/abs/2608.05485)
- [AVENUE, AV judge agreement](https://arxiv.org/pdf/2609.04253)
- [VEBench, temporal localization](https://arxiv.org/pdf/2605.03276)
- [NumPro, frame-number overlays](https://arxiv.org/abs/2411.10332)
- [ParaPairAudioBench](https://arxiv.org/abs/2606.24648)
- [ColorBench, VLM color perception](https://arxiv.org/abs/2504.10514)
- [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [Cerf 2009, faces and text attract gaze](https://doi.org/10.1167/9.12.10)
- [Abrams & Christ 2003, motion onset](https://doi.org/10.1111/1467-9280.01458)
- [Camerer, Loewenstein & Weber 1989, curse of knowledge](https://doi.org/10.1086/261651)
- [Potter 2014, meaning at 13 ms](https://link.springer.com/article/10.3758/s13414-013-0605-z)
- [Sundararajan & Adesope 2020, seductive details](https://link.springer.com/article/10.1007/s10648-020-09522-4)
- [ITU-R BT.1359, A/V timing](https://www.itu.int/rec/R-REC-BT.1359)
- [EBU QC test catalogue](https://qc.ebu.io)
- [AES TD1008, streaming loudness](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf)
- [pystoi (ESTOI)](https://github.com/mpariente/pystoi)
- [ebrary, crossfades and level jumps (Decca classical-recording guide)](https://ebrary.net/300232/education/crossfades)
- [WCAG 2.3.1, flashing](https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html)
- [Netflix timed text style guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide)
- [TikTok creative best practices](https://ads.tiktok.com/help/article/creative-best-practices)
- [TikTok/Nielsen, evolution of sound](https://ads.tiktok.com/business/en-US/blog/evolution-of-sound-volume-1)
- [YouTube Help, key moments for audience retention](https://support.google.com/youtube/answer/9314415)
- [Verizon/Publicis sound-off survey](https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/)
- [PandaStudio, retention laws (vendor)](https://www.writepanda.ai/blog/retention-editing-7-laws)
- Yunicorn internal: `backend/eval/pro_cut_reference.py`; `studio/ARCHITECTURE.md` §7, §9; Studio design inputs (`research_agent_architecture_quality.md`, `critique_feasibility.md`, `critique_simplicity.md`)
