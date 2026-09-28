# Voice processing and loudness

Load this file at ingest, after transcription, to measure each recording and decide how much restoration it needs, if any. Load it again at the sound stage of finishing to set the voice chain, handle breaths, clicks and seams, and master loudness. Reload it whenever a chat edit adds or moves a seam, since crossfades, room tone and breath decisions hang off word and gap IDs. Music level and ducking live in `music.md`, sound effects in `sfx.md`, and pause lengths in `cutting-and-pacing.md`. For a clean AirPods or lav recording, "a high-pass, gentle compression and loudness, nothing else" is a complete and often correct answer.

## Principles

1. **Measure before you touch anything.** *Why:* you cannot hear. Claude has no audio input, and Gemini gets 16 kbps mono from an audio file and about 1 kbps from a video's soundtrack, so every decision rests on numbers (SNR, C50, clipping, spectra, loudness) and on post-processing gates [I].
2. **Protect the voice before you polish it.** *Why:* degraded audio lowers ratings of both the content and the speaker [L]. A natural voice with some room noise beats a clean synthetic one.
3. **Denoise only as far as the noise demands, never to silence.** *Why:* suppression trades speech quality (SIG) for background quality (BAK). Overdone, it leaves "a metallic ring, or gurgles and watery S sounds" (Towne, Transom) [P], and generative restorers can invent phonemes [I].
4. **One chain per recording session, not per segment.** *Why:* segments processed differently jump in timbre and noise floor at every seam. That reads as an edit even when the words flow [I].
5. **Every seam must be inaudible.** *Why:* a click, a chopped breath, a cut-off reverb tail or dead silence announces an edit. Level changes above ~4–5 dB become audible through the shifting noise floor, and changes of 0.3–0.5 dB can already make an edit feel different [P, classical-music editing].
6. **Keep the breath, lose the gasp.** *Why:* breaths cue a new thought; in synthetic-speech studies a preceding breath improved transcription and recall (600 ms inhalations did, 300 ms did not) [L]. Loud breaths distract: attenuate them, leave quiet ones, remove one only whole.
7. **Compress for consistency, not for loudness.** *Why:* quiet words must survive a phone in a noisy room [X], but a flattened delivery loses the emphasis the creator performed. NPR levels mainly by riding gain and keeps compression light [P].
8. **Master to where the platforms play, and verify after encoding.** *Why:* YouTube turns loud content down and never turns quiet content up [P]. The codec creates peaks the limiter never saw [P].
9. **Judge for the phone speaker and one earbud, not the studio.** *Why:* phone speakers struggle below 200 Hz and are silent by about 100 Hz [V]. Intelligibility lives in the consonant band, and a single earbud is mono.
10. **Match processing intensity to the recording.** *Why:* the chain fixes problems measurement found; it does not show work. NPR's rule is "Do as little processing to the audio as possible": if the high-pass fixes it, stop there [P].

## Defaults and ranges

These are starting priors. Give a one-line reason whenever you leave a range. Measure loudness per ITU-R BS.1770 (gated integrated, 3 s short-term, 4x-oversampled true peak).

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Denoise bands (median speech-frame SNR) | Reads ≥ ~25 dB: none. 20–25: none, or light (limit 6–12 dB) only if the floor is audible at final gain. 10–20: limit 12–18 dB. Under 10: best-of-N incl. isolators, limit 18–24 dB; consider a pickup. Band edges are ±4 dB fuzzy | [X] bands; calibrate [I] | [Brouhaha](https://arxiv.org/abs/2210.13248) |
| SNR reading ceiling | Brouhaha was trained on read speech (LibriSpeech, 16 kHz) mixed at 0–30 dB SNR; frame SNR MAE ~4 dB on synthetic test data. Readings above ~25 dB mean "clean", not an exact number | [L] | [Brouhaha](https://arxiv.org/abs/2210.13248) |
| Dereverb bands (C50) | ≥20 dB: none. 10–20: only if tails show at seams. Under 10: candidate, light reduction | [X]; C50 MAE 1.1 dB in 5 real rooms [L] | [Brouhaha](https://arxiv.org/abs/2210.13248) |
| Attenuation limit | Never unlimited: cap suppression by band (6–24 dB, 24 at most) and mix the residual noise back in | [I] + [V] (12 dB example; ffmpeg afftdn default 12 dB) | [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py), [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#afftdn) |
| Restoration gates | ASR WER +1 point max; speaker similarity held; DNSMOS SIG not below the untreated take; spectral rolloff within ~10% of source; lag to source ≤1 ms | [I]; 10% tolerance [X] | design_doctrine.md §13, critique_feasibility.md, [DNSMOS P.835](https://arxiv.org/abs/2110.01763) |
| Clipping flag | 3+ consecutive samples at ≥ −0.1 dBFS inside a kept word | [X] | — |
| High-pass | Start at 80–100 Hz, 12 dB/oct; 85 if the voice thins, 110–120 for a boomy voice. Keep the corner below most of the speaker's F0 range (adult male ~90–155 Hz, female ~165–255 Hz). Also tames p-pops | [P] | [NPR](https://www.npr.org/sections/npr-training/2025/05/31/g-s1-67902/the-producers-handbook-to-mixing-audio-stories), [Sound Radix](https://www.soundradix.com/articles/mixing-dialogue-in-audio-storytelling/), [Wikipedia](https://en.wikipedia.org/wiki/Voice_frequency) |
| Mud or boxiness | 200–400 Hz (up to 500 in small rooms), wide cut of 2–4 dB, only where the spectrum shows a bump | [V] (sung-vocal cheat sheet) + [X] | [Orphiq](https://orphiq.com/resources/vocal-eq-cheat-sheet) |
| Presence | 2–5 kHz, 0 to +3 dB, only if rolloff shows dulling (often after denoise); home recordings are often over-bright already | [V] + [X] | [Orphiq](https://orphiq.com/resources/vocal-eq-cheat-sheet) (3–5 kHz) |
| De-ess | Start from male ~3–6 kHz, female ~5–8 kHz, then centre on the measured sibilance peak; 2–6 dB off sibilants only | [V] bands + [X] depth | [Apple Logic Pro](https://support.apple.com/en-us/102006) |
| Compression | 1.5:1–2:1; 2–3 dB gain reduction, 5–6 dB on emphasised words; attack ~10 ms, release ~100–150 ms. Up to 3:1–4:1 and 4–6 dB only for dense, fast delivery over music | [P] | [NPR](https://www.npr.org/sections/npr-training/2025/05/31/g-s1-67902/the-producers-handbook-to-mixing-audio-stories) (1.5:1–2:1, 11/110 ms, 2–3 dB), [NPR FAQ](https://npr.org/g-s1-65503) (1.5:1, 3–5 dB), [Transom](https://transom.org/2015/podcasting-basics-part-3-audio-levels-and-processing/) (3:1–4:1, 4–6 dB) |
| Leveler | Slow, ±2–3 dB range, window about a word or two; adjacent segments within ±1.5 LU short-term | [P] + [X] | [Sound Radix](https://www.soundradix.com/articles/mixing-dialogue-in-audio-storytelling/) |
| Breaths | Target mode: pull loud breaths to ~15–20 dB below neighbouring speech; leave quieter ones | [V] mode + [X] level | [iZotope RX](https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html) |
| Mouth clicks | Declick at low sensitivity (higher sensitivity damages plosives), before compression lifts the clicks | [V] + [X] order | [iZotope RX](https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/mouth-de-click/index.html) |
| Seam crossfade | 10–30 ms equal-power in room tone; a couple of ms in true silence or on a stop consonant; 30–100 ms across a breath; equal-gain only when rejoining one continuous sound | [P] + [X] (10–30 default) | [SOS comping](https://www.soundonsound.com/techniques/vocal-comping-editing), [SOS fades](https://www.soundonsound.com/techniques/using-fades-crossfades) |
| Room-tone bed | From the take's own processed pauses, nearest the seam first, looped; level within ~1–2 dB of the processed floor | [P] + [X] | [Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise) |
| Lip sync | Detectable at ~45 ms audio-early, ~125 ms audio-late; EBU R37 allows 5 ms early / 15 ms late per stage. Hold every neural stage to ≤1 ms | [L] (ITU subjective tests) + [P] (R37) + [I] | [ITU-R BT.1359](https://www.itu.int/rec/R-REC-BT.1359), [EBU R37](https://tech.ebu.ch/docs/r/r037.pdf) |
| Integrated loudness | −14 LUFS, whole mix, measured after the AAC encode. Aim for ±0.5 LU; the validator's hard gate is ±1 LU (ARCHITECTURE §7) | [I] (platform-matching choice, not a standard) | design_doctrine.md §13 |
| True peak | ≤ −1 dBTP after encode; limiter ceiling −1.5 dBTP, because overshoot grows as bitrate falls. Limiter last: filtering after it recreates overshoot | [P] (standard) + [A] | [AES TD1008](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf), [Spotify](https://support.spotify.com/us/artists/article/loudness-normalization/) (TP below −2 when louder than −14) |
| Max short-term loudness | ≤ integrated +5 LU (−9 LUFS at a −14 target). R 128 s1 caps adverts and promos at −18 LUFS, +5 LU over its −23 target | [P] ceiling; [X] transfer to −14 | [EBU R 128 s1](https://tech.ebu.ch/docs/r/r128s1.pdf) |
| Loudness Range | Do not target LRA under ~1 minute (R 128) or on short-form generally (s1); too few 3 s blocks | [P] (standard) | [EBU R 128](https://tech.ebu.ch/docs/r/r128.pdf), [s1](https://tech.ebu.ch/docs/r/r128s1.pdf) |
| Limiter work | ≤ ~2–3 dB on peaks; more means the compression is wrong | [X] | — |
| Speech standards | TD1008: −18 LUFS dialogue-gated (+1 LU tolerance) for speech streams, −1 dBTP at codec input. EBU R 128 s2: −20 to −16 LUFS interim for streams without loudness metadata. Apple Podcasts: −16 ±1 | [P] (standard) + [A] | [TD1008](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf), [R 128 s2](https://tech.ebu.ch/docs/r/r128s2.pdf), [Apple](https://podcasters.apple.com/support/893-audio-requirements) |
| YouTube | Since 2019, turns content above −14 LUFS down; never turns quieter content up. Shorts not separately measured | [P] (practitioner measurement) | [Production Advice](https://productionadvice.co.uk/youtube-loudness/), [Stats for nerds](https://productionadvice.co.uk/stats-for-nerds/) |
| TikTok, Instagram | No official spec or normalization measurements found. APU's −16 is, in its own words, a "conservative production convention" | [V] | [APU Software](https://apu.software/tiktok-instagram-reels-loudness/) |
| Mono fold-down | Voice level change ≤ ~1 dB; channel correlation near +1 | [X] | — |
| Phone speaker | Little output below 200 Hz, almost none by 100 Hz | [V] | [LANDR](https://blog.landr.com/make-bass-audible-phone-speakers/) |

**What validators already enforce:** the loudness and true-peak targets after encoding, no digital silence, crossfades at every seam, and edit times from aligned words and measured energy minima, never from a model. Spend your attention on judgment.

## How to decide

1. **Measure each take at ingest.** Record median and 10th-percentile speech SNR, C50, noise tags (hum, HVAC, traffic, wind, music in the room), clipped samples per word, the long-term spectrum, the 5–9 kHz sibilance ratio, breath flags and levels per gap, click and p-pop flags per word onset, raw integrated loudness, and AV offset. A floor near digital silence that pumps between words means the phone's Voice Isolation already ran: do not denoise again. Music in the room calls for a separation candidate.
2. **Choose the session chain.** From the bands, pick none, light, standard or heavy. Run candidates on whole takes, always including "none", so the noise estimate sees the full take and the room tone you cut later matches. Keep the lightest candidate that passes every gate. If nothing passes on a payoff or CTA line, use another take of that line (by word IDs) or request a spoken pickup.
3. **Handle clipping.** If a kept word clips, prefer an alternate take of it. Otherwise declip and re-run the ASR diff on that word. Otherwise request a pickup.
4. **Match takes.** When segments come from different takes, compare their spectra and short-term loudness with the dominant take. Correct level and broad EQ until adjacent segments sit within ±1.5 LU with no audible tonal step. Match with EQ and gain, not with a different denoiser.
5. **Build every seam.** The cut sits at the gap's energy minimum. Crossfade 10–30 ms, never across two words. Lay continuous room tone under every gap, cutaway and insert, and keep it unbroken across J and L cuts. The voice does not change under b-roll, and an insert hides picture, not sound: the seam beneath it still needs its crossfade.
6. **Decide breaths and clicks gap by gap.** Compare each flagged breath in a kept gap with the words on either side: attenuate it if loud, leave it if quiet. If `cutting-and-pacing.md` tightens a gap below the breath's length, remove the breath whole, cutting at its edges. Never cut mid-breath. Keep the pre-speech breath before a new thought or a turn, since it marks the beat. Rapid-fire list styles can lose more breaths. Declick flagged word onsets, prioritising the hook, the payoff and words beside seams.
7. **Process the voice bus in order:** hum notch if tagged, high-pass (a de-plosive only for pops it leaves), corrective EQ (only for measured problems), gain rides on words that fall away, compression, de-ess, then leveler. Every stage must name the measurement that called for it; skip the rest. Segments sped up per `speed.md` go through the same chain. Re-run the click detector afterwards, because stretching can smear transients.
8. **Master.** After music and SFX are mixed, apply linear gain to reach −14 LUFS, then an oversampled true-peak limiter with a −1.5 dBTP ceiling as the last stage. Encode AAC, then re-measure integrated loudness, true peak and maximum short-term loudness. Log the voice stem's loudness too; a busy mix can hit −14 with the voice low. Fold to mono and compare. Run a phone-speaker simulation (heavy low cut, small-speaker band) and the intelligibility gates in `music.md`. The no-music master gets the same targets.
9. **Critique.** Answer the critic questions below. Fix one cause at a time; if the limiter works hard, fix the compression, not the ceiling.

## When to break it

- **The place is the content.** For a street interview, a walk-and-talk, a live event or a reaction, denoise lightly or not at all. The ambience tells the viewer where they are.
- **Whisper, ASMR, intimate confession.** Use less compression, and keep breaths and mouth sounds. They are the performance.
- **Comedy.** A gulp, a sigh or a lip smack can be the joke, so keep it at full level.
- **Shouted hooks and big laughs.** Let short-term loudness approach the +5 LU ceiling. Compressing the shout flat removes the reason it was shouted.
- **A unique performance on a bad recording.** Accept heavier restoration only if every gate passes. Otherwise flag a pickup rather than ship artifacts on the line that matters.
- **Loudness target.** −14 is a prior. If an A/B shows −16 reads better (dense music, dynamic storytelling), move it. If measured downloads show a platform does not normalize, −12 to −11 is defensible while the limiter works ≤ 2–3 dB and true peak holds after encoding [X]. Creator memory can also fix a target.
- **The creator's signature sound.** If past posts carry a deliberately raw phone sound or a heavy broadcast chain, match it.
- **Foreign sources.** Screen recordings and clipped videos get their own chain, matched to the voice's level, not its timbre.

## Worked example

*Creator: skincare educator, iPhone front camera at arm's length, kitchen at night, 158 wpm, medium energy. The hook comes from take 2 and the body from take 1, recorded 20 minutes apart. Locked cut: 38 s. The music file chose none.*

> "Stop putting vitamin C on at night. Here's why." (take 2, w1–w9)
> "Vitamin C is an antioxidant. Its whole job is fighting damage from sun and pollution…" (take 1, w10–w48)
> "…so at night it's basically doing nothing." [gap g49: 1.1 s with a loud inhale] "Retinol is what you want at night." (w50–w55)
> "Save this so you don't forget." (CTA, w92–w97)

**Measurements.** Take 1: SNR 19 dB, 60 Hz fridge hum with harmonics, C50 16 dB. Take 2 (phone farther away): SNR 24 dB, C50 12 dB, 2.6 LU quieter, +4 dB at 250–350 Hz. No clipping. High sibilance on "vitamin C" and "save this". The g49 breath is only 9 dB below the speech around it. Clicks at the onsets of w10 and w92. Raw loudness −27.3 LUFS.

**Restoration.** SNR 19 puts take 1 in the denoise band. We notched the hum first, then ran three candidates on both whole takes: none, MossFormer2 blended to a 15 dB attenuation limit, and a commercial isolator. The isolator raised WER by 2.1 points because it swallowed "C" in "vitamin C" twice, so it was rejected. MossFormer2 passed (WER +0.3, SIG −0.02, rolloff −4%, lag 0) and runs on both takes: take 2 alone would not need it, but one chain keeps the floors matched.

**Dereverb: none.** Take 2's tail only shows at the w9→w10 seam. Light dereverb made the hook drier than the body (C50 about 22 against 16), which is a new mismatch. Instead we matched take 2 with −3 dB at 300 Hz and +2.6 dB of gain, and let "why" ring 120 ms into take 1's room tone before w10, with a 25 ms equal-power crossfade.

**Chain.** High-pass at 90 Hz, below her 5th-percentile F0 of 172 Hz. No presence boost, because rolloff stayed close to the source. Declick w10 and w92 at low sensitivity. Compression at 2:1, 10 ms attack, 120 ms release, 2–3 dB on most words and about 5 dB on "Stop" and "nothing". De-ess 3–4 dB, centred on her measured sibilance peak at 7.4 kHz. Leveler ±2 dB. Adjacent segments now sit within 1 LU.

**Breath at g49.** The pacing pass tightened g49 to 0.4 s, and the inhale is 0.35 s. "Retinol is what you want at night" is the turn, so the breath stays whole, pulled down 8 dB. The silence before it goes. Room tone, 2.4 s looped from take 1's processed pauses, runs under all 11 gaps and the 1.8 s product cutaway.

**Master.** After AAC encode: −14.0 LUFS, −1.4 dBTP, maximum short-term −10.3 LUFS (+3.7 LU, on "Stop"). Mono fold changes level by 0.2 dB. The phone simulation keeps every word.

**Not done:** dereverb, presence EQ, generative restoration, breath deletion. None had a job.

## Anti-patterns

- Denoising to zero: underwater vowels, dropped consonants, a vacuum between words that pumps on every syllable.
- Stacking denoisers, re-denoising Voice Isolation audio, or shipping generative restoration without an ASR diff.
- A different chain per segment, so every seam jumps in timbre and floor.
- Gating pauses to digital silence, or synthetic pink noise in place of real room tone.
- Deleting every breath until the delivery sounds machine-gunned, or cutting through the middle of one.
- Running the full chain because the tools exist; on a clean recording most stages have no job.
- Crushing compression that flattens emphasis and lifts the room; presence boosts on harsh phone audio; de-essing into a lisp.
- Mastering to −8 LUFS "to be loud". YouTube turns it down, and on every platform the distortion remains.
- Measuring true peak only before encoding. Using one-pass or dynamic loudnorm, which pumps. Measuring a mono stem and forgetting the 3 dB dual-mono difference ([FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)).
- Stereo wideners or reverb on voice; they collapse in mono.
- Normalizing clips to −14 before the mix, so music and SFX push past the target.
- Judging quality with a 16 kHz model or studio headphones instead of a phone.

## Critic questions

1. On a phone speaker at normal volume, is every word intelligible, including the quietest line?
2. Is the voice free of processing artifacts: watery or metallic tone, warbling, swallowed consonants?
3. Does the voice sound like the same person in the same room across every seam?
4. Is the background continuous under every cut and insert, with no dead silence and no noise pumping between words?
5. Is every seam free of clicks, pops, chopped breaths and cut-off reverb tails?
6. Are breaths natural: present at new thoughts, never louder than the words around them?
7. Are "s" and "sh" sounds free of harshness without sounding lisped?
8. Does the level stay steady, with no line jumping out or falling away except deliberate emphasis?
9. Is the loudest moment (shout, laugh, hook) free of distortion?
10. Do the lips stay in sync throughout?
11. After encoding, does the file measure within 0.5 LU of −14 LUFS integrated (hard gate ±1 LU) and −1 dBTP or lower true peak?
12. Folded to mono, does the voice keep the same level and tone?
13. Could any stage in the chain be removed with no measurable loss? If so, remove it.

## Sources

- Newman & Schwarz (2018), Good sound, good research: https://journals.sagepub.com/doi/abs/10.1177/1075547018759345
- Lavechin et al. (2022), Brouhaha (VAD, SNR, C50): https://arxiv.org/abs/2210.13248
- Reddy et al. (2022), DNSMOS P.835: https://arxiv.org/abs/2110.01763
- Whalen, Hoequist & Sheffert (1995), Breath sounds and synthetic speech perception: https://pubmed.ncbi.nlm.nih.gov/7759655/
- Elmers et al. (2021), Take a breath (Interspeech): https://www.isca-archive.org/interspeech_2021/elmers21_interspeech.html
- ITU-R BS.1770-5, Loudness and true-peak measurement: https://www.itu.int/rec/R-REC-BS.1770
- EBU R 128 (2023): https://tech.ebu.ch/docs/r/r128.pdf
- EBU R 128 s1 (2020), Short-form content: https://tech.ebu.ch/docs/r/r128s1.pdf
- EBU R 128 s2, Loudness in streaming: https://tech.ebu.ch/docs/r/r128s2.pdf
- EBU Tech 3341, Loudness metering: https://tech.ebu.ch/docs/tech/tech3341.pdf
- AES TD1008, Loudness for streaming and on-demand: https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf
- ITU-R BT.1359, Relative timing of sound and vision: https://www.itu.int/rec/R-REC-BT.1359
- EBU R37, Relative timing of sound and vision: https://tech.ebu.ch/docs/r/r037.pdf
- Production Advice, YouTube loudness: https://productionadvice.co.uk/youtube-loudness/
- Production Advice, Stats for nerds: https://productionadvice.co.uk/stats-for-nerds/
- Spotify, Loudness normalization: https://support.spotify.com/us/artists/article/loudness-normalization/
- Apple, Podcast audio requirements: https://podcasters.apple.com/support/893-audio-requirements
- APU Software, TikTok and Reels loudness: https://apu.software/tiktok-instagram-reels-loudness/
- NPR Training, Producer's handbook to mixing: https://www.npr.org/sections/npr-training/2025/05/31/g-s1-67902/the-producers-handbook-to-mixing-audio-stories
- NPR Training, Audio production FAQ: https://npr.org/g-s1-65503
- Jeff Towne, Transom, Podcasting basics part 3: https://transom.org/2015/podcasting-basics-part-3-audio-levels-and-processing/
- Rob Byers, Sound Radix, Mixing dialogue: https://www.soundradix.com/articles/mixing-dialogue-in-audio-storytelling/
- Apple, Reduce sibilance in Logic Pro: https://support.apple.com/en-us/102006
- iZotope RX Breath Control: https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html
- iZotope RX Mouth De-click: https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/mouth-de-click/index.html
- iZotope RX De-reverb: https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/de-reverb/index.html
- DeepFilterNet enhance.py (atten_lim_db): https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py
- FFmpeg filters (afftdn, loudnorm): https://ffmpeg.org/ffmpeg-filters.html
- Sound On Sound, Using fades and crossfades: https://www.soundonsound.com/techniques/using-fades-crossfades
- Sound On Sound, Vocal comping and editing: https://www.soundonsound.com/techniques/vocal-comping-editing
- Orphiq, Vocal EQ cheat sheet (sung vocals): https://orphiq.com/resources/vocal-eq-cheat-sheet
- Idyll Sounds, Filling a scene with room tone: https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise
- ebrary, Classical Recording (Decca tradition), Crossfades: https://ebrary.net/300232/education/crossfades
- LANDR, Bass on phone speakers: https://blog.landr.com/make-bass-audible-phone-speakers/
- Wikipedia, Voice frequency: https://en.wikipedia.org/wiki/Voice_frequency
- Google, Gemini audio (16 kbps mono input): https://ai.google.dev/gemini-api/docs/audio
- Google, Gemini video understanding (1 fps, 1 kbps mono soundtrack): https://ai.google.dev/gemini-api/docs/video-understanding
- Internal: design_doctrine.md §13, research_audio_music_sfx.md, critique_feasibility.md (Yunicorn Studio design inputs)
