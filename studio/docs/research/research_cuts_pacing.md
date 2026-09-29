# Cutting and pacing craft for talking-head short-form video (principles with ranges for an agentic Claude editor)

## Summary
Top editors of talking-head shorts cut to follow the speaker's thoughts. Rhythm comes after that. The best recent measurements of top Shorts (small samples) show less cutting than the "retention editing" myth suggests. Speech starts at 0.0 s. Something visibly changes every 3–8 s (median about 5 s). Punch-ins are about 1.5x and used sparingly or as A/B crops. Fillers and natural pauses are mostly kept. Trims land between beats, never inside a sentence. Yunicorn's own pro-edited take tells the same story: every 300–550 ms sentence pause was kept, only stalls longer than about 575 ms were tightened, and there was one splice in total. Research supports cutting with varied intervals rather than a metronomic beat. It warns that fast pacing combined with extra decoration overloads viewers, especially on educational content. Speeding up speech is weakly supported: cut dead air first, and if you speed up at all, go gently (about 1.05–1.15x) and never on the hook, emotional lines or jokes. Hooks should deliver the point within about 1–3 s. Endings should stop on the payoff or a CTA the creator actually said, with no outro. Almost none of these ranges comes from a controlled study, so give them to Claude as starting priors with reasons and test them with evals. Do not hard-code them as rules.

## Verified findings
## 0. How strong is the evidence? (corrected)

**Controlled lab evidence (strong, but none of it on Shorts):**
- Attention and shot structure ([Cutting 2010](https://jordandelong.com/pubs/2010/AttentionEvolution.pdf)).
- Pacing × arousal overload ([Lang 1999](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504)).
- Seductive details ([Rey 2012](https://pmc.ncbi.nlm.nih.gov/articles/PMC8442593/)).
- Edit blindness ([Smith & Henderson](https://bop.unibe.ch/JEMR/article/view/2264)).
- Where viewers blink ([Nakano 2009](https://europepmc.org/articles/PMC2817301)).
- What viewers treat as an event boundary ([Magliano & Zacks 2011](https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf)).
- Pause types ([Campione & Véronis](http://sprosig.org/sp2002/pdf/campione-veronis.pdf)).
- Speech rate and persuasion ([Smith & Shaffer](https://cir.nii.ac.jp/crid/1363670321183928320)).
- Sped-up ("time-compressed") ads ([MacLachlan & Siegel 1980](https://exa.ai/library/publication/3vh4g3cfs4p); [Moore et al. 1986](https://exa.ai/library/publication/m0zfrfpygsv)).
- Fillers ([Fraundorf & Watson](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)).

**Platform statistics (moderate, and all about *ads*):**
- TikTok's 6 s, 2 s, 1.5x and 1.4x figures, and Meta's 3 s figure from about 2016. None of these measure organic retention.

**Weak:**
- PandaStudio's "measured" breakdowns:
  - PandaStudio is a **vendor**: it sells an AI editing agent.
  - It measured 13 Shorts in total: Hormozi n=3, DOAC n=3, Abdaal n=4, plus Cleo Abram.
  - Its own description: "not a controlled experiment" ([7 laws](https://www.writepanda.ai/blog/retention-editing-7-laws)).
- Galloway's 5,400-Short study (April 2023) is correlational and predates the 2025 change in how Shorts views are counted ([thread](https://threadreaderapp.com/thread/1646898356419981315.html)).

**Internal:** one pro-edited take (`marque_pro_cut_calibration.md`). Verified: 300–550 ms pauses were kept verbatim, only the 577 and 690 ms stalls were tightened, there was one splice, the full CTA was kept, and the old heuristic had made 19 micro-splices.

**Gap:** no study was found that links cut rate, zoom rate, speed-up or jump cuts to organic retention for talking heads. Every number below is a *prior* to test in evals.

## 1. Cut where a thought ends
- **Murch's order of priorities:** emotion (51%) > story (23%) > rhythm (10%). A lower priority is sacrificed for a higher one (Murch, *In the Blink of an Eye*; [StudioBinder](https://www.studiobinder.com/blog/walter-murch-rule-of-six/)).
- **Blinks mark natural breakpoints:**
  - Viewers' blinks synchronize at *implicit* low-attention points: the end of an action, the main character absent, a long shot.
  - They do not fall at scene breaks as such, and do not synchronize for audio-only stories ([Nakano 2009](https://europepmc.org/articles/PMC2817301)).
  - So seams belong at the end of a thought or a completed gesture.
- **What reads as a boundary:** breaks in *action* drive where viewers perceive event boundaries. Jumps in space or time have minor effects ([Magliano & Zacks](https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf)).
  - A jump cut inside a continuing thought is therefore cheap.
  - A cut that breaks the thought reads as a new beat, so use that deliberately.
- **Cut-point quality:** the best points are where the speaker is "relatively quiet and still" ([Berthouzoz](https://www.floraine.org/research/video-transitions/)).
- **Default unit of editing:** the beat (a sentence or clause), not the silence.

## 2. Pauses and breaths
- **Pause types:** brief (<200 ms), medium (200–1000 ms) and long (>1000 ms). Long pauses appeared only in spontaneous speech, but that data was 1 h of French. Read English had a median pause of **493 ms** ([Campione & Véronis](http://sprosig.org/sp2002/pdf/campione-veronis.pdf)). This matches the pro's kept 300–550 ms pauses.
- **Audio listening tests** ([Owoicho 2024](https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf)):
  - Setup: a TTS evaluation of inter-sentence pauses only.
  - Raters did **not** prefer the original pauses over pauses cut to 5 ms.
  - They **did** dislike very long pauses: 54.5–74.7% preferred the originals.
  - Implication: shortening is inaudible, so its only cost is the visual seam; long stalls really are penalized.
- **Practitioner defaults:**
  - auto-editor keeps a 0.2 s margin by default ([README](https://github.com/WyattBlue/auto-editor)).
  - video-use pads cuts by 30–200 ms, treats gaps of 400 ms or more as the cleanest cut points, calls gaps under 150 ms unsafe, and notes that ASR timestamps drift 50–100 ms ([SKILL.md](https://github.com/browser-use/video-use/blob/main/SKILL.md)).

**Pause policy (inference):**

| Pause | Default |
|---|---|
| <200 ms inside a phrase | Never cut |
| 250–550 ms at a sentence boundary | Keep verbatim |
| 0.6–1.5 s hesitation | Tighten to 250–400 ms (with room tone), or cut at a clean seam |
| Deliberate beat | Keep 0.5–1.2 s |
| >1.5 s dead air | Cut |

- **Breaths:** attenuate them rather than delete them.
  - iZotope's Target mode is "more natural": it heavily reduces loud breaths and leaves quiet ones.
  - Gain mode, which reduces every breath, "can result in unnatural sounding results" ([RX docs](https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html)).
  - The widely repeated word "clinical" does *not* appear in that doc.
  - Never leave digital silence; fill with room tone.

## 3. Retakes, false starts and fillers
- **Choosing a take:** keep the last complete take as the prior (TimeBolt, Descript). Override it on delivery: energy, wording, completeness.
- **False starts:** require a *later* re-delivery before labeling anything a false start. The internal pro-cut incident deleted a CTA that was mislabeled this way.
- **Deliberate repetition** is emphasis; keep it.
- **Stutters:** cut only where there is a gap of 150 ms or more; otherwise hide the cut or keep the stutter.
- **Fillers:**
  - Evidence: fillers *helped* story recall, while duration-matched coughs *impaired* it ([Fraundorf & Watson](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)). That was audio stories and memory, not watch-time.
  - In the three measured Hormozi Shorts, fillers were kept and trims "land between beats, never inside sentences" ([PandaStudio](https://www.writepanda.ai/blog/hormozi-style-shorts-editing), n=3).
  - Policy: remove fillers aggressively in the hook, hot takes and sales pieces. Remove only clustered fillers in storytime.
- **ASR matters:**
  - Whisper-family transcripts normalize fillers (video-use).
  - CrisperWhisper 2.0 reports disfluency F1 of 87.8 (Pro: 93.5), against 79.2 for ElevenLabs Scribe v2 and 30.5 for AssemblyAI Universal-3 Pro. These are the vendor's own benchmark figures ([repo](https://github.com/nyrahealth/CrisperWhisper)).
  - Run Yunicorn's own labeled test before choosing a filler substrate.

## 4. Jump cuts: when to hide them and when to embrace them
- **Embrace them** between beats, for time compression in lists and hot takes, and as comic punctuation.
- **Hide them** inside one thought and in emotional or storytime passages.
- **Evidence on noticing cuts:** a quarter of edits between two viewpoints of the same scene went unnoticed, rising to a third when the cut coincided with sudden motion onset ([Smith & Henderson](https://bop.unibe.ch/JEMR/article/view/2264)). That was film footage, not talking heads.
- **TikTok ad data:** ads with seamless shot transitions got **+60% recall**, and "editing shouldn't be frenetic" ([TikTok/CreatorIQ](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf); ads, correlational).
- **Ways to hide a cut:**
  - **Clear scale change.** The classical rule is a camera move of at least 30° or a lens change of about 20 mm ([30-degree rule](https://en.wikipedia.org/wiki/30-degree_rule)). The "≥20% scale" figure is practitioner lore. Murch warns against changes "neither subtle nor total".
  - **Cut on motion onset.**
  - **Cutaway** with a J- or L-cut.
  - **Morph Cut.** Adobe's guidance could not be fetched (403) in this pass.
- **Avoid micro-jumps:** a scale change under about 10% with the same framing reads as an error.
- **No controlled jump-cut study found:** only a pre-registered 2025 study (n=130), with results not located ([OSF](https://exa.ai/library/publication/y7nlg8717tv)).

## 5. Punch-ins and zooms
- **Size:**
  - Practitioner range is 15–25% (vendor [Autoclip](https://autoclip.dev/blog/punch-in-zoom-crop-zoom-speaker-zoom-guide)).
  - Hormozi alternates the full frame with a **1.5x** crop (n=3).
  - Abdaal uses at most one 1.5x punch-in, at about 75% of runtime (n=4, [PandaStudio](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/)).
- **Resolution ceiling:**
  - 1080p footage into a 1080p output: about 1.1–1.3x.
  - 4K footage into a 1080p output: about 1.8–2x (r/editors practitioner threads).
- **Placement:** on the onset of the stressed word; keep the eyes near the upper third.
- **Frequency:** about 3–4 emphasis punch-ins per 40 s at most (vendor).
  - Over-zooming by AI editors is documented only anecdotally: one Descript user reported zoom cuts "every 20 seconds" ([Reddit](https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/)).

## 6. Rhythm and cadence (corrected)
- **What Cutting actually found** ([paper](https://jordandelong.com/pubs/2010/AttentionEvolution.pdf)):
  - Across 150 films, *adjacent shot lengths became increasingly correlated*. Films are organized in "packets" of similar-length shots, and the 1/f structure appears at whole-film scale.
  - The pattern has "no relationship" to how much people liked the films ([Cornell](https://news.cornell.edu/stories/2010/03/study-pattern-movies-mimics-found-our-brain)).
  - For a 30–60 s Short, the translation is: **keep a locally consistent tempo inside a section, and change tempo between sections.** It is not "randomize the intervals".
- **Pace and overload:**
  - Fast pace plus arousing content overloads viewers and lowers recognition and recall ([Lang 1999](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504)).
  - In this literature "fast" means about 0.5–0.67 cuts/s, "medium" about 0.33 and "slow" about 0.16 ([Collier 2010](https://exa.ai/library/publication/s49x2k62711)). A median of about 5 s between changes is slow to medium.
- **Load per cut:** what matters is also how much new information each cut *introduces*. Emotion change is the most taxing kind ([Lang 2013](https://exa.ai/library/publication/q9vn7f63xlj)). Slow the cutting where the speaker delivers dense new information.
- **Measured cadence (weak, n≈13):**
  - A visual state change every 3–8 s, median about 5 s. A static stretch of 8 s or more is flagged ([7 laws](https://www.writepanda.ai/blog/retention-editing-7-laws)).
  - Hormozi: 3.5–5.6 s between hard cuts.
  - DOAC: 4.1–6.3 s, tightening to 2.5–3 s before the payoff and about 5.1 s on emotional material ([DOAC](https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips), n=3).
- **Counter-claim:** Gling says "cuts every 0.6–0.9 s" work best. This is vendor marketing with no published method.

## 7. Speed changes
- **Evidence:**
  - At 220 vs 180 wpm, persuasion rose only under moderate involvement, and only through perceived credibility. Fast speech also blunted listeners' ability to tell strong arguments from weak ones ([Smith & Shaffer](https://cir.nii.ac.jp/crid/1363670321183928320)).
  - Time-compressed TV ads at about 1.25x went **unnoticed** and raised recall by 36%/40% ([MacLachlan & Siegel 1980](https://exa.ai/library/publication/3vh4g3cfs4p)).
  - But compressed ads captured less attention, evoked fewer cognitive responses and made viewers lean more on source credibility ([Moore et al. 1986](https://exa.ai/library/publication/m0zfrfpygsv)).
  - Older adults recalled less from compressed ads ([Stephens 1982](https://exa.ai/library/publication/5ggfldbymjd)).
  - Lecture comprehension showed minimal cost up to 2x and declined beyond it, at speeds the experimenters assigned ([Murphy 2022](https://castel.psych.ucla.edu/wp-content/uploads/sites/111/2021/11/ACP-Lecture-Speed-Murphy-2021-in-press.pdf)).
- **Guidance:**
  - Cut dead air first.
  - Up to about 1.1x, and at most about 1.2x, pitch-preserved and per segment, only for slow speakers. The 1970s evidence tolerates about 1.25x, but it trades depth of processing for credibility cues.
  - Keep the hook, emotional lines, punchlines and instructions at 1.0x.
  - Use 2–3x only on process footage or b-roll with no speech.

## 8. Hooks
- **TikTok:** "Introduce your content proposition in the first 3 seconds"; put the hook in the first 6 s ([TikTok](https://ads.tiktok.com/help/article/creative-best-practices)). 90% of ad-recall impact and 80% of awareness impact fall in the first 6 s (internal meta-analysis).
- **TikTok creator ads** ([PDF](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf)):
  - A person on screen in the first 2 s gives +50% "hooking power" (defined as being in the top third of ads by 6 s view rate).
  - Direct address makes an ad 1.5x more likely to hook; text overlay, 1.4x.
  - A greeting gave **+112% brand recall**, and "you" in the first 5 s gave +128% purchase intent.
- **Greetings:** cut throat-clearing ("hey guys, so today…"), but keep direct address.
- **Meta:** up to 47% of campaign value comes in the first 3 s (Nielsen, about 2016) ([Meta](https://www.facebook.com/business/news/updated-features-for-video-ads)).
- **Galloway:** a viewed-vs-swiped ratio of 70–90% goes with the best performers, and under 60% rarely performs. "Treat your intro like a thumbnail."
- **Measured Shorts:** all 13 reach speech *or an on-screen payoff* within **2.5 s**; "0.0 s" is not the measured figure. The first line kept is a verdict, claim, question or promise.
- **Cold opens:** the DOAC "redacted pull-forward" works only when the line stands alone.
- **Open loops:** curiosity comes from a salient knowledge gap ([Loewenstein 1994](https://doi.org/10.1037/0033-2909.116.1.75)). Measured payoffs sit at 55–85% of runtime.

## 9. Endings and loops
- **Measured endings:** no outros; the video ends within 1.5–2 s of the payoff (PandaStudio).
- **CTA:** a CTA the creator said is part of the payoff, not a disposable sign-off (internal).
- **Loops:** since 31 March 2025 every start and replay counts as a Shorts view, but YPP and monetization still use engaged views ([PPC Land](https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/)). Build a loop only when the transcript supports it.
- **Comedy:** end on the button or tag.

## 10. J/L cuts and audio seams
- Use J- and L-cuts at talking-head/b-roll boundaries.
- Put fades of about 30 ms at every seam (video-use), or better, short overlapping crossfades so the audio doesn't dip (inference).
- Cut on speaker changes in podcast clips.

## 11. Profiles by content type (priors)
| Type | Cadence | Punch-ins | Speed | Notes |
|---|---|---|---|---|
| Educational | 4–8 s; slow where information is dense | Rare | 1.0x | Seductive details: recall d=0.30, transfer d=0.48, heterogeneous ([Rey](https://pmc.ncbi.nlm.nih.gov/articles/PMC8442593/)). Irrelevant sounds and music hurt ([Moreno & Mayer](https://psycnet.apa.org/doi/10.1037/0022-0663.92.1.117)). A face raises satisfaction but can lower learning ([Sondermann & Merkt](https://exa.ai/library/publication/28zts39hc3s)) |
| Storytime | 5–8 s; breathe on emotion | Slow push-ins | 1.0x | Keep dramatic pauses |
| Comedy | Tight, no dead air | On the button | Never on the punchline | 20 joke performances showed no pre-punchline pause or rate change ([Attardo & Pickering](https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf)); don't insert artificial beats (weak) |
| Hot take | 3–5 s | On the claim | ≤1.1x | Remove fillers |
| Tutorial | Keep steps whole | Rare | 1.0x on steps; 2–3x on process | — |
| Sales/UGC | 3–5 s | Moderate | ≤1.1x | Face in the first 2 s; direct address; "not overly polished" |
| Podcast clip | 4–6 s, 2.5–3 s near the payoff | Crops | 1.0x | Pull-forward hook |

## 12. Signature styles (opt-in priors; tiny samples)
- **Hormozi (n=3):** hard cuts every 3.5–5.6 s, a 1.5x A/B crop, zero b-roll, fillers kept, 1–4 caption words per screen every 0.8–1.0 s.
- **Abdaal (n=4):** about 95% face time, at most one 1.5x punch-in, content starts within 2.5 s.
- **DOAC (n=3):** as in §6.
- **Gadzhi and Dan Koe:** practitioner or vendor sources only.

## 13. Over-editing
- **Evidence it happens:**
  - The pro used 1 splice where the heuristic made 19.
  - Fast pace plus arousal overloads viewers (Lang).
  - The TikTok ad data favors seamless over frenetic transitions.
  - The "raw videos get 81% more views" claim is an anecdote.
- **Test:** the edit fails if the viewer notices the technique instead of the speaker.

## 14. Metrics for the critic and evals (revised)
- Seams per minute (pro: 1–4 per 60 s).
- Median kept boundary pause (250–550 ms).
- Share of cuts landing inside a clause.
- **Tempo structure:** within-section interval consistency plus between-section tempo shifts (tighter before the payoff). This replaces the invented CV<0.3 rule.
- Longest static stretch (flag over 8 s).
- Time to first speech or on-screen payoff (≤2.5 s) and to the proposition (≤3 s).
- Punch-in count and scale per 30 s against source resolution.
- Share of speech sped up and maximum factor.
- Gap from final word to end (≤0.5 s).
- CTA and payoff words retained.
- Round-trip ASR diff on the render, to catch clipped words and leftover fillers.

## 15. Tools (verified 2026-09-27)
- **Shot detection:**
  - PySceneDetect: BSD-3, 5.2k stars, pushed 2026-09-21. Threshold-based.
  - TransNetV2: MIT, F1 96.2 BBC, 93.9 RAI, 77.9 ClipShots; last push Dec 2023 ([repo](https://github.com/soCzech/TransNetV2)). Better for fingerprinting reference edits.
  - Neither catches slow push-ins or caption swaps.
- **ASR and timing:**
  - WhisperX: BSD-2, 24.3k stars. Vendor-benchmarked boundary error 64.8 ms; normalizes fillers.
  - CrisperWhisper 2.0: code MIT; models non-commercial, with a commercial licence for Pro. Claimed 29.6 ms boundary error. Test it yourself.
  - Silero VAD (MIT) for speech/silence segmentation.
  - Montreal Forced Aligner (MIT) for boundary refinement; untested here.
- **Faces:** MediaPipe (Apache-2.0) for face-centered crops and motion onsets.
- **Prosody:** Parselmouth (GPL-3.0) for F0 and intensity, to tell sentence-final pauses from hesitations (inference).
- **Time-stretch:** Rubber Band, GPL-2.0-or-later with a commercial licence available; GPL obligations don't trigger for server-only use. Use the R3 engine, and A/B it against ffmpeg atempo at 1.05–1.2x.
- **Baselines:** auto-editor (Unlicense) and video-use (MIT, 27.4k stars) for defaults and candidate cut points, not for final decisions.

## Verified recommendations
- Give Claude a craft brief of principles, with ranges and the reasons behind them, plus selectable content-type and signature-style profiles (§1-13). Label every range with its evidence tier: lab, ad-platform, vendor n≈3-13, or internal. The agent names its profile and justifies any deviation. Do not hard-code these as deterministic passes. The best-cited cadence numbers come from about 13 hand-picked Shorts measured by a vendor, and rule-based trimming already produced 19 splices where the pro made 1.
- Place seams on thought and action boundaries: the ends of sentences or clauses, completed gestures, low-attention moments. Hide cuts inside a continuing thought (a clear ≥1.3x scale change, a cutaway with a J/L cut, or a cut on motion onset). Keep visible jump cuts for beat changes. This is supported by Magliano & Zacks (action breaks define event boundaries), Nakano (blinks at implicit breakpoints), Smith & Henderson (motion onset masks cuts) and TikTok's +60% recall for seamless transitions.
- Pause policy: never cut under 200 ms; keep 250-550 ms sentence-boundary pauses verbatim; tighten 0.6-1.5 s hesitations to 250-400 ms with room tone; keep deliberate 0.5-1.2 s beats; cut dead air over 1.5 s. Attenuate breaths using target-level reduction rather than deleting them. Rationale: the read-English median pause is about 490 ms, and listeners don't hear shortened pauses but do penalize long ones, so the real cost of shaving is the visual seam.
- Replace the 'randomize intervals / CV<0.3' idea with a tempo-structure rule: a consistent interval within a section, deliberate tempo shifts between sections (tighter toward the payoff, slower on emotional or information-dense beats), and no static stretch over about 8 s. A median visual change of about 4-6 s is a starting prior. That is 'slow-medium' by the lab definitions, so there is headroom, but Lang's overload result argues against going much faster when the content is dense or emotional.
- Hooks: the first speech or on-screen payoff within 2.5 s, and the proposition within 3 s. Strip throat-clearing ('hey guys, so today…') but keep direct address to the viewer ('you'), which TikTok's ad data associates with large recall and hook gains. Never speed up the hook. Use a pull-forward cold open only when the line stands on its own.
- Speed: cut dead air first. Allow at most about 1.1x (hard cap about 1.2x), pitch-preserved per segment with Rubber Band R3, and only for slow speakers. Never apply it to the hook, emotional lines, punchlines or instructions. The 1970s-80s ad research shows about 1.25x goes unnoticed and helps recall but reduces elaboration and hurts older viewers, so the gain is not free. A/B it in evals.
- Filler and pause substrate: benchmark verbatim ASR candidates (CrisperWhisper 2.0 Pro under a commercial licence, ElevenLabs Scribe v2, AssemblyAI with disfluencies) on a Yunicorn-labeled set of creator takes for disfluency F1 and word-boundary error before committing. Don't rely on plain Whisper or WhisperX, which normalize fillers and have about 65 ms boundary error.
- Educational and tutorial profiles: exclude decorative b-roll, music beds and sound effects unrelated to the content; keep beats after key claims; allow keyword signaling. Rey (d=0.30 recall, 0.48 transfer, heterogeneous) and Moreno & Mayer support this. Note the trade-off: a visible face raises satisfaction and choice but can lower learning.
- Endings: end at most about 0.5 s after the final payoff or CTA word, with no outro. Never cut a CTA the creator actually said. Build loops only when the transcript supports them; replays now count as Shorts views, but YPP uses engaged views.
- Compute the §14 metrics on every render as critic feedback and eval features, including a round-trip ASR diff. Calibrate against more pro-cut pairs and against creators' corrections in the iOS editor. Also fingerprint a larger reference corpus of top creator Shorts yourself (TransNetV2 plus a face-box scale tracker) rather than relying on vendor measurements with n≈3 per creator.

## Corrections by fact-checker
- [corrected] Top talking-head Shorts start speech at 0.0 s; PandaStudio's measured Shorts are neutral 'measured' evidence. → The source says every one of the 13 Shorts reaches speech OR an on-screen payoff within 2.5 s. It does not say 0.0 s. PandaStudio is also a vendor: it builds an AI editing agent. The per-creator samples are tiny: Hormozi n=3, DOAC n=3, Abdaal n=4, plus Cleo Abram. The post is dated 4 July 2026. Payoff at 55-85% of runtime, no outros, and ending within 1.5-2 s of the payoff are accurately reported. https://www.writepanda.ai/blog/retention-editing-7-laws ; https://www.writepanda.ai/blog/hormozi-style-shorts-editing ; https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/ ; https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips
- [corrected] Owoicho et al. 2024 show listeners don't notice pause shaving, so shaving 400 ms to 250 ms buys nothing and costs seams. → This is an audio-only TTS-evaluation study of inter-sentence pauses in 3-5-sentence clips (LibriTTS, CALLHOME, news). The authors explicitly make no psycholinguistic claims. Raters showed no preference for the original pauses over pauses cut to 5 ms, so shortening is inaudible. They did significantly dislike very long pauses (54.5-74.7% preferred the originals). The implication is different: shaving is free for the ear, so its only cost is the visual jump-cut seam, and long pauses really are penalized. https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf
- [corrected] Cutting 2010: film shot lengths evolved toward 1/f, so vary intervals; flag coefficient of variation < 0.3 as metronomic. → Cutting found that adjacent shot lengths became increasingly correlated. Shots cluster in 'packets' of similar length, and the 1/f spectra show up at whole-film timescales (minutes to hours) across 150 films. That is not random variation. A 30-60 s Short with 8-15 shots cannot express 1/f. Cutting also says the pattern has no relationship to how much people liked the films. The CV<0.3 threshold is the researcher's own invention. Better translation: keep a locally consistent tempo within a section, then shift tempo between sections (for example, tighten toward the payoff). https://jordandelong.com/pubs/2010/AttentionEvolution.pdf ; https://news.cornell.edu/stories/2010/03/study-pattern-movies-mimics-found-our-brain
- [corrected] Murphy 2022: lecture comprehension held up to 2x when viewers chose the speed. → Speeds (1x, 1.5x, 2x, 2.5x) were assigned by the experimenters, not chosen by viewers. The study found minimal comprehension cost up to 2x and a decline beyond 2x, on lecture videos. It measured comprehension, not persuasion or retention/watch-time. https://castel.psych.ucla.edu/wp-content/uploads/sites/111/2021/11/ACP-Lecture-Speed-Murphy-2021-in-press.pdf
- [corrected] iZotope's Breath Control docs say fully de-breathed dialogue sounds 'clinical'. → The RX 8 doc contains no 'clinical' quote. It says Gain mode (reducing every breath) 'can result in unnatural sounding results'. Target mode is 'more natural' because it cuts loud breaths heavily and leaves quiet ones alone. The attenuate-rather-than-delete principle stands, but the quote is misattributed. https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html
- [corrected] Successive shots should differ by at least ~20% (30-degree rule citation). → Wikipedia's 30-degree rule article says to move the camera at least 30 degrees or change focal length by about 20 mm (the '20 mm/30 degree rule'). It does not give a 20% scale figure. The 20% scale-change number is practitioner lore (StoryEnvelope), not the cited primary source. Murch's warning against changes that are 'neither subtle nor total' supports the idea, not the number. https://en.wikipedia.org/wiki/30-degree_rule
- [confirmed] Campione & Véronis: pauses are brief (<200 ms), medium (200-1000 ms) and long (>1000 ms); long pauses occur only in spontaneous speech. → Confirmed, with scope caveats. The data are about 6,000 pauses in 5.5 h. The spontaneous part is about 1 h of French interviews only; the read part covers five languages. Read English had a median silent pause of 493 ms (mean 487 ms), which supports keeping pauses of about 300-550 ms. http://sprosig.org/sp2002/pdf/campione-veronis.pdf
- [confirmed] Smith & Henderson edit blindness: about a quarter of cuts unnoticed, about a third when coinciding with sudden motion onset. → Confirmed for edits that join two viewpoints of the same scene in film clips. It is not a talking-head jump-cut study. https://bop.unibe.ch/JEMR/article/view/2264
- [confirmed] Rey 2012 seductive details: recall d=0.30, transfer d=0.48. → Retention d=0.30 (34 studies, 3,535 participants) and transfer d=0.48 (21 studies). Heterogeneity is high: only 11 of 39 effects clearly supported the effect and 15 did not. https://pmc.ncbi.nlm.nih.gov/articles/PMC8442593/ ; https://eric.ed.gov/?id=EJ986386
- [confirmed] Lang 1999: fast pace plus arousing content overloads viewers and lowers recall. → Confirmed: the combination lowered recognition and cued recall. Nuance the researcher missed: in this literature 'fast' is roughly 0.5-0.67 cuts/s (a 1.5-2 s average shot length), so a Shorts median of about 5 s is 'slow' to 'medium'. https://www.tandfonline.com/doi/abs/10.1080/08838159909364504 ; https://exa.ai/library/publication/s49x2k62711
- [confirmed] Smith & Shaffer 1995: 220 vs 180 wpm raised persuasion only under moderate involvement and blunted argument-quality discrimination. → Confirmed. The persuasion gain came through perceived source credibility. https://cir.nii.ac.jp/crid/1363670321183928320
- [confirmed] Fraundorf & Watson: fillers helped story recall; duration-matched coughs hurt it. → Confirmed: 'coughs matched in duration to the fillers impaired recall'. These were audio stories, and the finding is about memory, not watch-time. https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/
- [confirmed] TikTok: 90% of ad recall impact within the first 6 s; person in first 2 s gives +50% hooking power; direct address 1.5x; text overlay 1.4x. → All confirmed, but all are about ADS. 'Hooking' is defined as being in the top third of ads by 6-second view rate (Metrixlab 2023). The 90% figure (plus 80% of awareness impact) comes from TikTok's internal 'Value of a View' meta-analysis. The same PDF has uncited nuance: greeting the audience gave +112% brand recall, and seamless transitions gave +60% recall ('editing shouldn't be frenetic'). https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf ; https://ads.tiktok.com/business/en-US/blog/creative-best-practices-top-performing-ads
- [confirmed] Meta: up to 47% of campaign value in the first 3 s. → Confirmed. It comes from Facebook/Nielsen research of about 2016. 'Value' means ad recall, awareness and purchase intent. It is old and ad-specific. https://www.facebook.com/business/news/updated-features-for-video-ads
- [confirmed] Galloway: 70-90% viewed-vs-swiped predicts success; <60% rarely performs. → Confirmed: 5,400 Shorts, 33 channels, 3.3B views, April 2023. The data are correlational and predate the March 2025 view-count change. Galloway notes high VVSA does not guarantee success. https://threadreaderapp.com/thread/1646898356419981315.html
- [confirmed] Since 31 March 2025 Shorts views count every start and replay. → Confirmed. Engaged views (the old metric) still drive YPP eligibility and monetization. https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/
- [corrected] Attardo & Pickering: comedians don't pause before punchlines. → The study covers 20 joke performances, prepared and spontaneous, not specifically by professional comedians. It found no evidence that punch lines are preceded by pauses or delivered at a different speech rate. The authors call timing theory 'in serious need of further research'. So it supports 'don't insert artificial pre-punchline beats' only weakly. https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf
- [corrected] Rubber Band is GPL-2.0; check GPL obligations for server use. → It is GPL-2.0-or-later, with a paid commercial licence. GPL (not AGPL) obligations trigger on distribution, so purely server-side rendering does not require releasing source. Latest tag is v4.0.0; the R3 'Finer' engine is the higher-quality one. https://breakfastquay.com/rubberband/ ; https://github.com/breakfastquay/rubberband
- [confirmed] auto-editor default margin 0.2 s; video-use pads 30-200 ms, treats >=400 ms as cleanest cut, <150 ms unsafe, uses 30 ms fades. → Both confirmed from the repositories. video-use also notes that Scribe timestamps drift 50-100 ms, which padding absorbs. It warns that Whisper normalizes fillers. https://github.com/WyattBlue/auto-editor ; https://github.com/browser-use/video-use/blob/main/SKILL.md
- [corrected] Descript users complained of mechanical zoom cuts every 20 s after a model change. → This is a single Reddit user report (Jan 2026), attributed to the Opus 4.1 selection. It is an anecdote, not a pattern across users. https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/
- [unverifiable] Adobe Morph Cut works best with a single fixed subject, static background and handles on both sides. → The Adobe help page returned 403 on every fetch attempt, so this could not be checked against the primary source in this pass. The claim is consistent with widely reported practice. https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-transitions/morph-cut-overview.html
- [corrected] WhisperX is a free alternative for pause measurement. → The license (BSD-2) and activity are confirmed. However, Whisper-family output normalizes or omits fillers (video-use warns about this). On a vendor benchmark WhisperX's mean word-boundary error is 64.8 ms on TIMIT, against 29.6 ms for CrisperWhisper 2.0 and 51.3 ms for ElevenLabs Scribe v2. That makes it a weak substrate for filler and short-pause decisions without verbatim priming or re-alignment. https://github.com/nyrahealth/CrisperWhisper

## Missed items added
- Blinks synchronize across viewers at implicit low-attention breakpoints: the end of an action, the main character absent, long shots. They do not fall at scene breaks as such, and do not synchronize for audio-only or background video. This is primary evidence for Murch's 'cut on the blink' and for placing seams at the ends of thoughts or gestures (Nakano et al. 2009, Proc R Soc B) - https://europepmc.org/articles/PMC2817301
- Action discontinuities drive perceived event boundaries, while space/time discontinuities have only minor effects. So jump cuts that keep the thought continuous are cognitively cheap, and cuts that break the thought read as boundaries, so reserve them for beat changes (Magliano & Zacks 2011) - https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf
- LC4MP 'information introduced' per camera change: cognitive load depends on how much new information each cut adds, and emotion change is the most taxing dimension. Guidance is to slow the pacing where new information is dense (Lang et al. 2013) - https://exa.ai/library/publication/q9vn7f63xlj
- Time-compressed advertising evidence the researcher missed. About 1.25x compression (30 s into 24 s) went unnoticed and raised unaided/aided recall by 36%/40% (MacLachlan & Siegel 1980, https://exa.ai/library/publication/3vh4g3cfs4p). But compressed ads capture less attention, evoke fewer cognitive responses and shift persuasion toward source credibility (Moore, Hausknecht & Thamodaran 1986, https://exa.ai/library/publication/m0zfrfpygsv), and older adults recall less from them (Stephens 1982, https://exa.ai/library/publication/5ggfldbymjd).
- TikTok creator-ad data that complicates 'cut all greetings': a creator greeting the audience gave +112% brand recall, saying 'you' in the first 5 s gave +128% purchase intent, and seamless shot transitions gave +60% recall ('editing shouldn't be frenetic'). All from Lumen 2022, all ads - https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf
- CrisperWhisper 2.0 (inference code MIT; models under a non-commercial licence; Pro models need a commercial licence). Vendor-reported disfluency F1 is 87.8 (Pro 93.5), against ElevenLabs Scribe v2 at 79.2 and AssemblyAI Universal-3 Pro at 30.5. Word-boundary error is 29.6 ms. This is directly relevant to filler and pause decisions, but it is the vendor's own benchmark, so Yunicorn should run its own test - https://github.com/nyrahealth/CrisperWhisper
- TransNetV2 (MIT; last push Dec 2023) scores shot-boundary F1 of 96.2 on BBC Planet Earth, 93.9 on RAI and 77.9 on ClipShots. It is a stronger learned detector than threshold-based PySceneDetect for fingerprinting reference edits - https://github.com/soCzech/TransNetV2
- Talking heads in educational videos gave worse factual learning but higher satisfaction, and viewers chose them more often (N=112). This is a real trade-off for the educational profile: face-time drives choice, and relevant visuals drive learning (Sondermann & Merkt 2022) - https://exa.ai/library/publication/28zts39hc3s
- Parselmouth (Praat in Python, GPL-3.0, active) provides F0 and intensity contours. It could help tell sentence-final falls (keep the pause) from mid-phrase hesitations (tighten the pause), which addresses the researcher's open question on dramatic vs hesitation pauses. This is an inference and was not benchmarked - https://github.com/YannickJadoul/Parselmouth
- No controlled study of jump cuts in talking-head video was found. A pre-registered 2025 German dissertation (n=130, jump cuts vs none in music-lesson videos) exists, but its results were not located - https://exa.ai/library/publication/y7nlg8717tv

## Tools
- PySceneDetect [Cut and shot-rhythm measurement (build style fingerprints from reference edits; critic metric for visual-change intervals); BSD-3-Clause] https://github.com/Breakthrough/PySceneDetect — 5.2k stars, last push 2026-09-21. ffmpeg-based scene detection like this is the method PandaStudio used to measure 963 cuts in its long-form corpus. Hard cuts only; punch-in crops within one camera setup need crop-change or face-bounding-box detection.
- WhisperX [Word-level timestamps for pause, filler and seam analysis; BSD-2-Clause] https://github.com/m-bain/whisperX — 24.3k stars, last push 2026-09-26. The prior report already recommends an ASR with disfluency tags (AssemblyAI) for filler candidates; WhisperX is a free alternative for pause measurement.
- Silero VAD [Speech/silence segmentation for measuring pause lengths; MIT] https://github.com/snakers4/silero-vad — 10.3k stars, last push 2026-09-23. Pair it with word timings so pauses can be classified as <200 ms, boundary, hesitation or dramatic.
- MediaPipe Face Landmarker [Face-centered punch-in cropping and detecting head-motion onsets as cut points; Apache-2.0] https://github.com/google-ai-edge/mediapipe — 37.1k stars, last push 2026-09-25. Landmarks also give eye/blink signals, which fits Murch's cut-on-blink heuristic.
- auto-editor [Silence-based cutting baseline and reference defaults; Unlicense] https://github.com/WyattBlue/auto-editor — 5.4k stars, last push 2026-09-19. Default 0.2 s margin documented in its README. Use as a baseline or candidate generator, not the final decision-maker; threshold cutting is what over-splices.
- Rubber Band Library (or ffmpeg atempo/rubberband filters) [Pitch-preserving time-stretch for gentle speed-ups; GPL-2.0 (commercial license available); ffmpeg is LGPL/GPL depending on build] https://github.com/breakfastquay/rubberband — 784 stars, last push 2025-03-03. I did not benchmark its quality against atempo in this pass. Check GPL obligations for server use and A/B it against atempo at 1.05–1.15x; this tool matters only if speed-ups are used at all.
- video-use (SKILL.md thresholds) [Agentic editing reference with practitioner cut thresholds; MIT] https://github.com/browser-use/video-use — 27.4k stars, last push 2026-09-24. Documents 30–200 ms padding, ≥400 ms gaps as clean cut targets and 30 ms fades. Already covered in prior research; listed here for its craft thresholds.
- Adobe Premiere Morph Cut [Reference technique for hiding jump cuts (commercial, not open source); Proprietary] https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-transitions/morph-cut-overview.html — Adobe documents best results for a single subject, fixed shot and static background; it fails with hand or body motion and without handles on both sides of the cut. No free equivalent of production quality was found in this pass. Adobe's jump-cut smoothing research (arXiv 2401.04718, from prior notes) is research only.
