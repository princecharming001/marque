# Speed changes and time-stretching

Load this file in the fine cut, after the story cut and pause policy are done, whenever you are tempted to change playback speed: speeding up slow speech, fitting a hard duration cap, slow or fast motion on b-roll, a speed ramp, or a fast-forward gag. It also covers stretching audio and video so a speed change stays inaudible and in sync. The default answer is 1.0x; most good talking-head edits change no speech speed at all.

## Principles

1. **1.0x is the default, and speed is the last time-saving tool, not the first.** Why: every other move (cutting a beat, choosing a take, tightening a pause) removes something, while speed alters every syllable of the creator's voice. Our pro calibration take changed no speed.
2. **Take time out of silence before you take it out of syllables.** Why: moderately shortened pauses mostly go unnoticed in listening tests, while compressed syllables are judged. Nonuniform compression, which squeezes pauses and easy sounds more than consonants, beat uniform compression on comprehension and preference (tested at 2.5-4.2x; the direction holds).
3. **Speed up a slow segment, never a slow creator.** Why: speaking rate correlates with enthusiasm, and speeding up an unenthusiastic speaker may not help. Perceived pace comes more from what is said and what changes on screen than from syllable rate. Calm is a legitimate style.
4. **Protected lines stay at 1.0x by default: the hook, punchlines, emotional lines, instructions and steps, the payoff and the CTA.** Why: warmth peaks at a natural rate. Joke tellers follow no fixed rate or pause formula at the punchline, so the performer's own delivery is the timing and retiming it is a guess. Instructions need processing time. And every viewer hears the hook.
5. **Consistency matters more than absolute rate.** Why: listeners hear about a 5% tempo difference when they have a reference, and the unsped parts of the same video are that reference. One gentle speed per section beats per-sentence speeds.
6. **Change speed only where the ear already expects a reset: at a seam or a real pause.** Why: a tempo change mid-phrase is heard as a lurch, while a pause resets the listener's tempo anchor.
7. **Comprehension is not the constraint; perceived quality and authenticity are.** Why: across 24 lecture studies, playback up to 1.5x cost only about 2 percentage points on tests. Even so, at 1.25x viewers rated satisfaction lower and noticed more distortion.
8. **Viewers already have a fast-forward button.** Why: TikTok, Reels and Shorts all offer viewer-side 2x, and your baked-in speed multiplies with it.
9. **Always preserve pitch on speech, and stretch only the voice stem.** Why: without pitch correction, 1.1x raises the voice by 1.65 semitones. Stretching a mix that includes music makes the music warble.
10. **Slow motion is for b-roll and needs real frames.** Why: slow motion adds weight and even perceived intent, and slow motion built from duplicated or badly interpolated frames looks broken.
11. **Speed ramps belong on picture, not on speech.** Why: a ramp warps whatever audio rides on it, and repeated ramps read as gimmick rather than emphasis.

## Defaults and ranges

Tiers: [L] lab or peer-reviewed study, [A] platform or large-scale data, [V] vendor claim, [P] practitioner consensus, [I] Yunicorn internal, [X] our inference. All of these are starting priors. No study links speech speed-ups to organic retention on Shorts.

| What | Starting prior | Tier and source |
|---|---|---|
| Speech speed | 1.0x | [I] Pro calibration take: ~17 of 22 pauses ≥300 ms kept to the millisecond (385→385, 481→481 ms), so no speed was applied (`backend/eval/pro_cut_reference.py`, Marque repo) |
| Just-noticeable tempo difference | about 5% | [L] [Quené 2007](https://doi.org/10.1016/j.wocn.2006.09.001) (figure as cited by later work) |
| Gentle speed-up when justified | 1.03-1.08x. Soft ceiling 1.10x. 1.10-1.20x needs a written reason. Above about 1.20x only as a visible effect | [X], built on Quené, [MacLachlan & Siegel 1980](https://journals.sagepub.com/doi/abs/10.1177/002224378001700106) (1.25x pitch-preserved ads went unnoticed without a reference) and [Yueh et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12675162/) (1.25x cost satisfaction) |
| Speed difference between adjacent segments inside one thought | at most 0.05 | [X] from the 5% just-noticeable difference |
| Rate at which a segment becomes a speed candidate | below about 150 wpm output rate, measured after pauses are tightened, and clearly below the creator's own median | [X]; edX videos averaged 156 wpm (range 48-254) [A] ([Guo et al. 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf)) |
| Short-form creator pace | Reels 184 wpm (n=312), TikTok 172 wpm (n=121), pauses included; user-uploaded transcripts | [V] [Voqusa](https://www.voqusa.com/en/blog/video-transcription-statistics-2026) |
| Best rate for recognition | 170 wpm for dense news, 190 wpm for light news: the denser the content, the slower it must be | [L] [Rodero 2016](https://doi.org/10.1080/15213269.2014.1002942) (radio news) |
| Conversational baseline | 164 wpm per speaker (turn-wise); 196 wpm counting both talkers over conversation time (111-291 per conversation) | [L] [Yuan et al. 2006](https://www.isca-archive.org/interspeech_2006/yuan06_interspeech.html) (Switchboard) |
| Comprehension knee | about 275 wpm. Lecture playback: about 2 points lost up to 1.5x, moderate-to-large losses from 2x (17 points at 2.5x); older adults are hurt more | [L] [Foulke & Sticht 1969](https://pubmed.ncbi.nlm.nih.gov/4897155/), [Murphy et al. 2022](https://onlinelibrary.wiley.com/doi/abs/10.1002/acp.3899), [Tharumalingam et al. 2025](https://doi.org/10.1007/s10648-025-10003-9) (24 studies; numbers via [Pearce](https://theconversation.com/what-happens-to-your-brain-when-you-watch-videos-online-at-faster-speeds-than-normal-259930)) |
| Experience cost at 1.25x | satisfaction 4.17→3.82; perceived distortion 1.94→2.59; comprehension and intent unchanged. Replicated on an explainer (3.94→3.69), not in a third sample | [L] [Yueh et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12675162/) (n=326, cooking tutorial; n=313 and 246) |
| Persuasion | 180→220 wpm helped only under moderate involvement, via credibility, and blunted strong-vs-weak argument discrimination. Compressed ads drew less attention and fewer thoughts about the claims. Faster rates raise competence; benevolence peaks at the natural rate | [L] [Smith & Shaffer 1995](https://journals.sagepub.com/doi/10.1177/01461672952110006), [Moore et al. 1986](https://doi.org/10.1086/209049), [Smith et al. 1975](https://journals.sagepub.com/doi/10.1177/002383097501800203) |
| Pitch rise without pitch correction | 1.10x = +1.65 semitones; 1.20x = +3.2 semitones, the same +20% pitch that made speakers seem less truthful and more nervous. Slowed speech read as less truthful and less persuasive | [X] arithmetic; [L] [Apple et al. 1979](https://doi.org/10.1037/0022-3514.37.5.715) |
| Time-stretch engine | Rubber Band R3 through the CLI (`rubberband -3`); `-D`/`--duration` sets an exact output length; `--crisp` affects R2 only. FFmpeg's `rubberband` filter has no engine option. `atempo` (WSOLA-style, a family designed for speech) is the A/B fallback: range 0.5-100, skips samples above 2.0 | [V] [Rubber Band usage](https://breakfastquay.com/rubberband/usage.txt), [FFmpeg rubberband](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/rubberband.html), [FFmpeg atempo](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/atempo.html); [L] [Verhelst & Roelands 1993](https://doi.org/10.1109/ICASSP.1993.319366) |
| Lip-sync tolerance | detectable at audio 45 ms early or 125 ms late; acceptable to +90/−185 ms. Per-segment rounding accumulates toward these | [L] [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) |
| Frame cadence under speed-up | 60 fps at 1.08x skips about 5 source frames per second (nearest-frame) | [X] arithmetic |
| Slow-motion floor | factor ≥ output fps ÷ source fps: 120 fps Slo-mo gives 0.5x at 60 fps output, 240 fps gives 0.25x; a 60 fps source gives 0.5x only in a 30 fps output. Use integer ratios; frame blending is visible even to untrained viewers | [A] [Apple](https://support.apple.com/guide/iphone/change-video-recording-settings-iphc1827d32f/ios); [P] [Frame.io](https://blog.frame.io/2019/10/17/mixed-frame-rates-part-3/) |
| Fast b-roll | 2-3x on silent process footage, up to about 8x for timelapse-like steps; integer factors | [P] doctrine prior; [X] |
| Speed ramps | 0 by default, at most 2 per short; 0.5-1.5 s each; stepped 1→2→4→2→1x; original audio muted or covered | [V] [OpusClip](https://www.opus.pro/blog/best-speed-ramp-pace-control-tools-short-form) (says 2-3 max), [Kapwing](https://www.kapwing.com/resources/how-to-use-the-speed-ramp-effect/) |
| Caption load after speed-up | about 20 characters per second or less, as a monitored signal: re-page or reduce speed, never delete words | [P] [Netflix English (USA)](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide); `captions-and-text.md` |
| Viewer-side speed | 2x on TikTok, Instagram Reels (Mar 2025) and YouTube Shorts (Jun 2026) | [A] [PetaPixel](https://petapixel.com/2025/03/28/instagram-rolls-out-fast-forward-feature-for-reels/), [TechCrunch](https://techcrunch.com/2026/06/25/youtube-shorts-are-getting-even-shorter-with-an-update-that-lets-you-double-the-playback-speed/) |

## How to decide

**Speech**

1. **Finish the free moves first.** Cut dead beats, `choose_take` the best take, remove clustered fillers, and `set_gap` according to `cutting-and-pacing.md` (keep 250-550 ms sentence-boundary pauses, tighten 0.6-1.5 s hesitations to 250-400 ms).
2. **Measure each segment's output rate:** words ÷ (duration including kept gaps). Also read `energy` and `get_prosody` for the segment, and compare with the creator's own median rate. If a segment is at or above about 150 wpm, or near the creator's median, it almost always stays at 1.0x.
3. **Find out why a slow segment is slow.** Long pauses mean more gap work, not speed. Over-explaining means cutting words. Speed is left only for *articulation* drag: stretched vowels ("sooo the first thing"), a deliberate but flat reading pace, or a non-native speaker's careful delivery on non-critical explanation.
4. **Mark protected ranges** and keep them at 1.0x: the hook sentence, punchlines and buttons, emotional lines and the beat after them, steps and instructions, and pinned payoff and CTA words.
5. **Group candidates into sections.** Take a run of consecutive non-protected sentences, not individual sentences. Put segment boundaries only at an existing seam or a gap of about 300 ms or more. Never split a continuous phrase just to change its speed.
6. **Pick one factor per section.** Start at 1.05x and go to 1.08x only if 1.05x still drags. Keep neighbouring segments within 0.05 of each other unless a seam separates them. Anything above 1.10x needs a note naming the reason. Style priors: educational, storytime, podcast clips, founder pieces and tutorial steps stay at 1.0x; hot takes, listicles and sales pieces may reach 1.10x.
7. **Re-check the numbers after speed.** Kept pauses must still sit in the speaker's own boundary-pause range in *output* time (250-550 ms for most, up to ~700 ms for calm speakers, per `cutting-and-pacing.md`); a 400 ms gap in a 1.08x segment plays as 370 ms unless its target is set in output time. Why: pauses restored at phrase boundaries aided recall of compressed speech more than randomly placed ones. Captions should stay at or under about 20 cps, and the output rate should not pass about 170-180 wpm on dense content. Then total the seconds saved. If the saving is under about 1 s across the video, revert: the risk buys nothing.
8. **Render and listen as an A/B.** The critic compares 1.0x and sped renders of the segment, checking for warble, doubled or swallowed consonants, pitch drift, lip sync at the segment's end *and at the end of the video*, and stutter in fast gestures. Speech runs through Rubber Band R3 (`rubberband -3`) on the dialogue stem only, before the voice chain, stretched to the exact sample length the compiler assigns (`--duration`, or a ratio computed from sample counts), never a rounded factor per segment, which drifts. Re-run click detection afterwards (`voice-and-loudness.md`). If R3 sounds phasey on a voice, A/B `atempo`. Music and SFX are placed on output time and never stretched with the voice. Avoid J/L cuts across a speed boundary; if one is needed, the lead audio plays at its own segment's factor. Video uses nearest-frame retiming; never frame-blend or optical-flow a speaking face.
9. **Write a `note`** for each speed segment: why it was sped, the factor and the seconds saved. If `set_speed` rejects a factor, accept the engine's cap.

**Picture-only speed (b-roll, inserts, process footage)**

- **Fast motion:** use it on silent process footage (cooking, assembly, screen scrolling) at 2-3x or more, with integer factors so the cadence stays even. The voice-over stays at 1.0x.
- **Slow motion:** use it only where a real moment deserves weight (a pour, a signature, a jump). Check the source frame rate: the factor must be at least output fps ÷ source fps, so a 60 fps clip in a 60 fps output cannot go below 1.0x without interpolation. Read the real stream rate: a Slo-mo clip exported from Photos may already be conformed to 30 fps with its slow section baked in. Practical-RIFE is a gated fallback for simple motion only; reject it on faces, hands or text if a warp check fails.
- **Ramps:** add one only when it lands on something (a music hit, a transition whoosh, an action peak) and only on b-roll or a transition. Mute or cover the source audio. "None" is the normal answer for calm creators.

## When to break it

- **A fast-forward gag.** A rambling tangent sped to 1.5-3x *is* the joke when it is short (3 s or less), used once, and signposted with a "2x" label, a tape-whir SFX or a deliberate chipmunk voice. It must never try to hide the speed.
- **A very slow deliberate speaker** (under about 130 wpm after gap work) doing low-stakes explanation. Going to 1.12-1.15x can be right if the A/B is clean. The audience matters: older viewers lose more from compressed speech.
- **When the only alternative is an uncovered mid-thought jump cut.** Inside one continuous thought with no b-roll, punch or natural beat to hide a seam, one 1.05-1.08x section can be less visible than a cut, because every seam has a price. Render both and let the critic pick; leaving the stretch alone is also an answer.
- **A hard duration cap with nothing left to cut.** A uniform 1.02-1.04x on the whole video stays under the just-noticeable difference and keeps everything consistent. Always try cutting a beat first.
- **Creator memory.** If a creator consistently speeds up their own videos, that is their signature; memory overrides doctrine.
- **Slowed speech as a replay gag.** An "instant replay" of a blooper at 0.5x with pitched-down audio works because it is obviously an effect. Serious speech is never slowed.

## Worked example

A calm renter-advice creator films a 72 s take: "Three things I check before signing any lease." It runs 154 words at 128 wpm, with 34% silence.

- **Hook** (w0001-w0010): "Your landlord is counting on you not reading page four." This is **1.0x.** It is the hook, and it is already the creator's crispest line.
- **Story cut:** a false start and an earlier retake of item 2 are removed, bringing the take to 63.0 s.
- **Pauses:** `set_gap` tightens 11 hesitations (average 0.95 s) to 0.35 s, saving 6.6 s: now 56.4 s at 145 wpm. Boundary pauses of 300-500 ms stay.
- **Items 1-2 explanation** (s004-s007, 14.2 s, 31 words, 131 wpm, after the fine cut removed a doubled "the"): "So the first thing is the renewal clause. Because what happens is a lot of leases just auto-renew." Most of the slowness is vowel stretch ("sooo", "juuust"), not pauses. This section gets **1.08x**, bounded by a seam before s004 and gap g0058 (0.42 s) after s007. It saves 1.05 s, and the section moves to 141 wpm.
- **Story** (s008-s011): "My first apartment, I signed it without reading page four. Lost my whole deposit. Nine hundred dollars. I cried in the parking lot." This is **1.0x**, and the 0.8 s beat after "Nine hundred dollars" stays: the line is emotional, and warmth peaks at the natural rate. Under "I signed it without reading page four" goes a 1.4 s insert of the creator's own 120 fps Slo-mo clip of a pen signing, at **0.5x** into the 60 fps output. The frames are real, and the slow motion gives the regret weight. The voice underneath stays at 1.0x.
- **Item 3** (s012-s013): "Ask for it in writing. Email, not a phone call." This is **1.0x**, because it is an instruction.
- **Payoff and CTA:** "Page four. Every time. Follow for part two, where I read a real lease line by line." This is **1.0x**, because both are pinned.

**Rejected:** a global 1.15x (saving 7.4 s). It would speed the hook, story and CTA, push item 3 from 168 to 193 wpm with captions near 20 cps, and make a calm creator sound hurried. A speed ramp into the list was also rejected: nothing in the audio or picture calls for one. **Result:** 55.4 s. Pause tightening saved about six times what the speed change did. The critic's A/B of s004-s007 at 1.0x vs 1.08x found no timbre difference, and lips stayed in sync at the section's end. This is a borderline call: 1.05 s barely clears the ~1 s bar, and a 1.0x-throughout cut at 56.4 s would have been equally defensible.

## Anti-patterns

- Speeding the whole video to 1.2-1.3x "for retention": a breathless, processed voice that stacks with the viewer's own 2x.
- Speeding up instead of cutting: keeping a rambling beat and compressing it.
- Fast-forwarding picture through silence instead of trimming it. Our old engine sped silent gaps up to 3x [I], and the face visibly zips; use `set_gap`.
- Speeding the hook "to get to the point faster."
- Per-sentence rate normalization, where each sentence gets its own factor. It produces audible tempo wobble. Our old engine did this up to 1.30x [I].
- Changing speed in the middle of a phrase, or between two sentences with no pause between them.
- Stretching a mixed track (the music warbles) or using varispeed without pitch correction (the chipmunk voice).
- Trusting objective quality metrics for stretch quality: they predict listener ratings poorly. Listen.
- Speeding speech harder under b-roll because "nobody sees the lips." The ear still hears the tempo.
- Using speed to fake energy, or on dense educational or emotional passages.
- Frame-blending or optical-flow retiming a talking face, which produces ghosted or warped mouths.
- Slow motion from 24, 30 or 60 fps footage with duplicated frames, or with interpolation that visibly warps hands and edges.
- Ramps on every b-roll clip, on the speaker's face with the voice riding them, or landing on nothing.
- Slowing another person's action "for drama": slow motion makes actions look more intentional, so a clumsy moment can read as deliberate.

## Critic questions

1. Does every protected line (the hook, punchlines, emotional lines, instructions, the payoff and the CTA) play at 1.0x?
2. Is every sped segment one where the pauses had already been tightened, so no sped segment still contains a pause over about 0.6 s?
3. Can you watch the whole video without any line sounding faster than the creator's own delivery elsewhere in it?
4. Is the voice's pitch and timbre identical in sped and unsped segments?
5. Is each sped segment free of audible artifacts (warble, metallic or phasey tone, doubled or swallowed consonants)?
6. Does every speed change fall at a seam or at a pause of about 300 ms or more?
7. Are the lips still in sync at the end of every sped segment and at the end of the video?
8. Do the speaker's gestures in sped segments move without visible stutter?
9. Are caption pages on sped segments still easy to read at normal viewing?
10. Is every slow-motion shot free of duplicated-frame stutter and interpolation warping?
11. Are there at most two speed ramps, all on b-roll or transitions, none carrying speech?
12. If the creator is calm, is the edit free of speed-ups that make them sound hurried?
13. Would this video be just as good with no speed change at all? If yes, remove it.

## Sources

- Quené 2007, On the just noticeable difference for tempo in speech: https://doi.org/10.1016/j.wocn.2006.09.001
- Foulke & Sticht 1969, Review of research on accelerated speech: https://pubmed.ncbi.nlm.nih.gov/4897155/
- Rodero 2016, Speech rate and information density on recognition: https://doi.org/10.1080/15213269.2014.1002942
- Yuan, Liberman & Cieri 2006, Speaking rate in conversation: https://www.isca-archive.org/interspeech_2006/yuan06_interspeech.html
- Guo, Kim & Rubin 2014, How video production affects student engagement: https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf
- Voqusa, Video transcription statistics 2026 (vendor): https://www.voqusa.com/en/blog/video-transcription-statistics-2026
- Smith & Shaffer 1995, Speed of speech and persuasion: https://journals.sagepub.com/doi/10.1177/01461672952110006
- Smith et al. 1975, Speech rate and personality perception: https://journals.sagepub.com/doi/10.1177/002383097501800203
- Apple, Streeter & Krauss 1979, Effects of pitch and speech rate on personal attributions: https://doi.org/10.1037/0022-3514.37.5.715
- MacLachlan & Siegel 1980, Time compression of TV commercials: https://journals.sagepub.com/doi/abs/10.1177/002224378001700106
- Moore et al. 1986, Time compression and persuasion: https://doi.org/10.1086/209049
- Wingfield et al. 1999, Time restoration and recall of time-compressed speech: https://pubmed.ncbi.nlm.nih.gov/10509694/
- Covell et al. 1998, Mach1 nonuniform time compression: https://www.mangolassi.org/covell/1997-061/index.html
- Murphy et al. 2022, Lecture video speed and comprehension: https://onlinelibrary.wiley.com/doi/abs/10.1002/acp.3899
- Tharumalingam et al. 2025, Playback speed meta-analysis: https://doi.org/10.1007/s10648-025-10003-9
- Pearce 2025, The Conversation summary of that meta-analysis: https://theconversation.com/what-happens-to-your-brain-when-you-watch-videos-online-at-faster-speeds-than-normal-259930
- Yueh et al. 2025, Cognitive and affective impacts of playback acceleration: https://pmc.ncbi.nlm.nih.gov/articles/PMC12675162/
- Attardo & Pickering, Timing in joke performance: https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf
- Owoicho et al. 2024, Inter-sentence pause preferences: https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf
- Caruso, Burns & Converse 2016, Slow motion increases perceived intent: https://www.pnas.org/doi/10.1073/pnas.1603865113
- Verhelst & Roelands 1993, WSOLA for high-quality time-scale modification of speech: https://doi.org/10.1109/ICASSP.1993.319366
- Roberts & Paliwal 2020, TSM dataset with subjective quality labels: https://arxiv.org/abs/2006.00848
- Rubber Band CLI usage: https://breakfastquay.com/rubberband/usage.txt
- FFmpeg rubberband and atempo filters: https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/rubberband.html ; https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/atempo.html
- ITU-R BT.1359, Relative timing of sound and vision: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- Frame.io, Mixed frame rates and interpolation: https://blog.frame.io/2019/10/17/mixed-frame-rates-part-3/
- Apple, iPhone video and Slo-mo settings: https://support.apple.com/guide/iphone/change-video-recording-settings-iphc1827d32f/ios
- Practical-RIFE: https://github.com/hzwer/Practical-RIFE
- OpusClip, Speed-ramp tools for short-form: https://www.opus.pro/blog/best-speed-ramp-pace-control-tools-short-form
- Kapwing, How to use the speed ramp effect: https://www.kapwing.com/resources/how-to-use-the-speed-ramp-effect/
- Netflix English (USA) Timed Text Style Guide: https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide
- Instagram Reels 2x playback (notes TikTok's equivalent): https://petapixel.com/2025/03/28/instagram-rolls-out-fast-forward-feature-for-reels/
- YouTube Shorts 2x playback: https://techcrunch.com/2026/06/25/youtube-shorts-are-getting-even-shorter-with-an-update-that-lets-you-double-the-playback-speed/
