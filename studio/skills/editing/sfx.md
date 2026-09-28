# Sound effects

Load this file at the finishing stage when you are considering any sound that is neither the creator's voice nor the music bed: whooshes, pops, ticks, dings, risers, shutters, typing, impacts, foley under inserts, record scratches and comedy buttons. Picture lock, inserts, captions and the music plan should already exist, because SFX mark events. The file covers whether a sound should exist, its family, its sync point (word ID plus frame), its level, and where it may legally come from. Voice treatment is in `voice-and-loudness.md` and beds are in `music.md`. The usual answer is zero sounds, or a handful. Every SFX competes with the person talking.

## Principles

1. **An SFX marks something the viewer can see.** Examples are a counter appearing, a screenshot landing, a card sliding in, or a real reveal. *Why:* a sound tied to a visible change fuses with it into one event; a sound with nothing to mark only adds processing. Adding music and sounds to narrated animations lowered learning (Moreno & Mayer 2000) [L], and coherence effects are strongest when the lesson is system-paced, as video is, and the extra material is highly interesting, as meme sounds are [L].
2. **Felt more than heard.** *Why:* the edit fails when the viewer notices the technique instead of the speaker. Good effects "shouldn't be noticed at all" (Descript) [V].
3. **The voice wins every collision.** *Why:* a sound on a stressed syllable masks consonants [X], and low audio quality lowered ratings of both the talk and the speaker [L] (a degraded recording, not an SFX layer, so the transfer is [X]).
4. **Scarcity is what makes a sound work.** *Why:* auditory feature onsets trigger orienting responses that lift memory for what follows [L]. Orienting habituates with repetition, fast pacing combined with arousing content overloads viewers [L], and signaling cues help most when used sparingly [L].
5. **Match intensity to the creator and the register.** *Why:* across 12,842 Douyin videos, engagement peaked at moderate audio arousal and rose when visual variation matched it [L, observational; visuals, not SFX]. Calm, sincere or educational delivery gets none.
6. **A hard cut does not need a whoosh.** *Why:* the cut is already the event, and the speaker's own movement hides a seam better than a sound (`transitions-and-graphics.md`). Only 2.4% of 12.2M clips made with OpusClip used transitions [V].
7. **The sync point lands on the frame.** *Why:* asynchrony is detected more easily when sound leads the picture, and for a hammer blow than for speech [L]. Broadcast detectability starts at about 45 ms of sound lead (ITU subjective tests) [L], and an effect two frames late "reads as wrong" [V].
8. **Keep one small palette per video:** 1–2 sound families, 3 at most [X]. *Why:* listeners follow only about two and a half layers of the same kind of sound (Murch) [P]. One signature sound reads as design, while a grab-bag reads as a template.
9. **The video must still work on mute.** *Why:* 69% of US adults say they watch video with sound off in public, 25% in private (n = 5,616, all video, 2019) [A, self-report]. A joke that lives only in an SFX dies for them, so give it a visual twin, such as a reaction or a text beat.
10. **Every sound is licensed, clean and dry.** *Why:* meme sounds of unknown provenance, low-bitrate files and reverb tails cheapen the mix and create legal risk.

## Defaults and ranges

These are starting priors. Give a one-line reason whenever you leave a range. Frame counts assume 30 fps; at 60 fps, convert by milliseconds.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Transient sync (pop, tick, ding, click, shutter, impact) | Transient between 1 frame early and 1 frame late of the event frame (±33 ms). If in doubt, late rather than early: late sound is how the physical world behaves | [L] + [P] + [V], applied to SFX by analogy [X] | [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) (TV lip sync: detectable at 45 ms early, 125 ms late); [Dixon & Spitz 1980](https://doi.org/10.1068/p090719); [Descript](https://www.descript.com/blog/article/how-to-add-sound-effects-to-a-video-2) |
| Whoosh on a transition or punch | Peak on the cut frame or up to 2 frames before it. The onset leads by the sound's rise: 2–4 frames for a short swish, longer for a swell. The tail may straddle the cut | [P] (weak) | research_audio_music_sfx.md §7 |
| Riser | Ends on the reveal frame, with a dead stop or into one soft impact; 0.8–2.0 s long | [P] + [X] | research_audio_music_sfx.md §7 |
| Spacing | At least 4–5 s between sounds, except in a deliberate rhythmic set | [P] (weak: practitioner blogs) | research_audio_music_sfx.md §7 |
| Simultaneous non-voice layers | At most 2 (bed plus one SFX); never two SFX on one frame. Murch allows 2.5 layers of one kind, 5 across the spectrum, in film; phone speakers justify a stricter prior | [X], informed by [P] | [Murch](https://transom.org/2005/walter-murch/) |
| Level: vendor reference | SFX −10 to −20 dB, dialogue −9 to −18 dB; published advice spans −6 to −24 dB | [V] | [Epidemic Sound](https://www.epidemicsound.com/blog/audio-mixing-for-video/) |
| Level: our prior | Transients peak 4–10 dB under the local speech peak. Textures (whoosh, riser, typing, foley) sit ≥10 LU under local short-term speech where they overlap words, continuous ones nearer 15 (the `music.md` floors), and may rise to about 6 LU under in a clean gap | [X]; floors [L] | Row above; [Torcoli et al. 2019](https://doi.org/10.17743/jaes.2019.0052) |
| Loudness ceiling | No 3 s (short-term) window more than +5 LU above target loudness. R 128 s1 sets −18 LUFS against a −23 target | [P], standard | [EBU R 128 s1](https://tech.ebu.ch/docs/r/r128s1.pdf) |
| Background vs speech (proxy) | 20 dB under speech; occasional sounds of 1–2 s exempt. WCAG AAA, for audio-only content | [P], standard | [WCAG 1.4.7](https://www.w3.org/WAI/WCAG21/Understanding/low-or-no-background-audio.html) |
| Music under an SFX | Duck the bed a further 2–4 dB for the sound's duration, or drop the SFX if the bed has a hit within ±150 ms | [X] | — |
| Repeated action sounds (typing, UI clicks) | Vary the sample, or pitch by up to a semitone and level by 1–2 dB, so repeats don't sound mechanical. List markers stay identical on purpose | [P] + [X] | [Unity Audio Random Container](https://docs.unity3d.com/6000.0/Documentation/Manual/AudioRandomContainer-fundamentals.html) |
| Extraneous sound in teaching content | Removing extraneous material helped in 18 of 19 tests (median d = 0.86) | [L] | [Mayer 2023](https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf) |
| Cues that mark structure | Helped in 26 of 28 tests (median d = 0.70), most when sparse. The cues were verbal or visual (headings, stress, arrows, flashing), so a sound cue is our inference | [L] → [X] | [Mayer 2023](https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf) |
| Orienting | 8 of 9 auditory features drew orienting responses; as a group they raised recognition of what followed (radio) | [L] | [Potter, Lang & Bolls 2008](https://doi.org/10.1027/1864-1105.20.4.168) |
| Over-editing baseline | A pro made 1 cut where the old engine made 19 | [I] | design_doctrine.md §1 |

**Validators already enforce** a licence check on every asset, the loudness and true-peak targets after encoding, and sync points snapped to the output frame grid from word IDs, never from a model's guess. Spend your attention on whether the sound should exist at all.

**Budget per 60 s by style.** All values are [X], derived from design_doctrine.md §17 and research_style_trends.md §7. Zero is valid in every row.

| Style | SFX / 60 s | Typical jobs |
|---|---|---|
| Educational / explainer | 0 (up to 2 functional) | Tick on a list counter, shutter on real evidence |
| Storytime | 0–1 | Only a sound the story's own insert would make |
| Comedy / skit | 0–3 | A button after the punchline, or a sound that is the joke |
| Hot take | 0–1 | Usually nothing |
| Listicle | 0, or 1 per item | The same tick on every counter |
| Tutorial / demo | 0–4, low | UI clicks and typing tied to on-screen actions |
| Podcast clip | 0 | — |
| Sales / UGC | 0–4 | Product moments, proof screenshots |
| Founder / brand | 0–2 | Proof inserts |
| Premium / luxury | 0–2 | Real foley only (box lid, fabric, glass) |

### Sound families: where each helps and where it cheapens

- **Whoosh.** Use it for motion the viewer sees: a whip, a card sliding in, or a style punch. It cheapens every jump cut and every zoom pulse. "A whoosh per word" belongs to a fading package and is used only on request [P].
- **Pop or soft click.** Use it when one key text element, emoji or callout appears. It cheapens caption pages, emphasis words, and hook titles present from frame 0.
- **Tick or ding.** Use it as a list marker, with the same sample on every item. It cheapens anything else. A ding in a different key from the bed sounds wrong.
- **Riser.** Use it only into a real visual reveal (a before/after, a result shown). With nothing revealed it cheapens the moment; over a creator's dramatic pause it spoils the silence.
- **Camera shutter.** Use it when a real screenshot or photo lands, optionally with a 2–3 frame flash (at most 3 flashes per second, [WCAG 2.3.1](https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html)). It cheapens stock images.
- **Typing.** Use it only under on-screen typing, starting and stopping with it. Use a real typing pass or varied keys, never one click looped.
- **Foley under an insert.** Muted stock (`broll.md`) can leave a full-screen cutaway feeling dead. A little real foley or room tone (a pour, keys, a door) at least 15 LU under the voice is "the glue" that makes stock feel shot in one space ([StringLabs](https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/)) [P]. Leading the picture by 2–4 frames (a J-cut) makes the cut feel motivated [X]. It cheapens when audible over words, generic, or under screenshots and graphics that make no sound in life.
- **Impact.** Use it once, for a hard cut to a big number. Its sub-bass vanishes on phone speakers and slams on earbuds.
- **Record scratch, vine boom, "bruh", laugh track, notification sounds.** Comedy personas only, and only when licensed. Elsewhere they are dated, and their provenance is often proprietary.

### Sourcing and licences

- **Epidemic Sound (preferred).** 250k+ SFX, sub-licensable to end users on the Scale tier [V] ([Partner API](https://developers.epidemicsound.com/)). Its MCP exposes `SearchSoundEffects` (term, duration, tags) and `SearchSimilarToSoundEffect`, with WAV downloads ([MCP](https://developers.epidemicsound.com/docs/mcp/)).
- **Stable Audio 3 Small-SFX (open weights, 0.6B).** Trained on 806k AudioSparx-licensed and 473k Freesound CC0/CC-BY/CC-Sampling+ recordings [V] ([card](https://huggingface.co/stabilityai/stable-audio-3-small-sfx)). Its Community License covers commercial use only under USD 1M annual revenue; above that, an Enterprise licence ([licence](https://stability.ai/license)).
- **ElevenLabs SFX (paid).** 0.1–30 s, with `prompt_influence` and `loop`. WAV at 48 kHz is only available for non-looping effects [V] ([docs](https://elevenlabs.io/docs/overview/capabilities/sound-effects)). Rights depend on the plan.
- **Never use:**
  - the Freesound API, which is "only for non-commercial purposes" without permission ([terms](https://freesound.org/docs/api/terms_of_use.html));
  - Sonniss GDC sounds anywhere in the engine. The licence covers the licensee's own projects, and it forbids supplying or sub-licensing the sounds to anyone else and using them for AI training ([licence](https://sonniss.com/gdc-bundle-license/));
  - sounds ripped from in-app libraries;
  - meme sounds of unknown provenance (`fahh` and `sus` are already disabled) [I];
  - the legacy typing, click, shutter, sparkle and riser files, which have no licence note [I] (research_codebase.md §3).
- **Pixabay** sounds may only be baked into renders on the server, never offered as standalone files, because the licence bars standalone distribution ([licence](https://pixabay.com/service/license-summary/)).

### AI generation prompts

Build every prompt as: family + material + character + envelope + "one-shot, dry, no reverb tail, no music, no voice". Always set `duration_seconds`, and use a high `prompt_influence` for literal results. Examples:

- "Soft airy whoosh, short one-shot, swells and peaks at the very end, no low boom, dry, no music, no voice" (0.5 s).
- "Soft wooden tick, a mallet on a wood block, single hit, dry, one-shot" (0.3 s).

Generate 4–8 candidates and choose by measurement. Reject any with detected speech or tonal content that clashes with the bed. Trim the leading silence with a few milliseconds of fade so it cannot click, record the sync point, and normalize to the family's reference level.

## How to decide

1. **Set the budget first,** from style, measured vocal arousal and words per minute, and creator memory. Calm, sincere and educational delivery starts at zero. If the creator will add a trending sound in-app, keep SFX minimal, because its hits are unknown.
2. **List candidate events from the EDL:** insert in-points, cards and counters, callouts, screenshots, typing, deliberate transitions, style punches, reveals and comedy buttons. Record each as an anchor word ID plus a frame offset, so re-cuts move the sound with its event.
3. **Protect spans by word ID.** No SFX may overlap:
   - the hook's first word;
   - punchline words;
   - sincere lines;
   - the payoff and CTA;
   - the stressed syllable of any key term.

   Prefer gaps of 150 ms or more, or word onsets, where a short transient masks little.
4. **Name the job.** The jobs are: mark structure, sell a visible motion, mark evidence landing, land a comedy button, or pay off a real build. Then ask whether the visual, or silence, already does that job. A music dropout or the creator's own pause often marks a moment better than any sound (`music.md`). No job, or a job already done, means no sound.
5. **Enforce density.** When two candidates fall within 4 s of each other, keep the stronger job. Never stack sounds.
6. **Pick the family, then the asset,** inside the palette. Tonal sounds go in key with the bed, or use atonal ones.
7. **Place by sync point, not by file start.** Each asset carries sync point (transient or peak), duration, tail, peak, momentary LUFS, spectral centroid, key and licence. Snap the sync point to the event frame on the output grid, so a whoosh's onset falls before its cut.
8. **Set the level against local speech.** With music, duck the bed briefly or drop the SFX near a music hit. Check the voice + SFX export too: sounds hidden under the bed often jump out without it. If the master limiter pulls more than about 1 dB at an SFX, it is too hot [X].
9. **Measure, because you cannot hear.** Claude hears nothing, and listening models hear at 16 kHz or below (Gemini downsamples an audio file to 16 kbps mono, and a video's soundtrack to about 1 kbps) [I]. In the render, check:
   - SFX loudness against speech;
   - the +5 LU ceiling;
   - ASR on every overlapped word (no word may change), and ESTOI of the voice stem against the mix over the overlapped span (no drop beyond measurement noise);
   - clicks at SFX ends;
   - a mono fold;
   - a phone-speaker simulation (high-pass around 200 Hz), in which every impact must still read [X].
10. **Run the subtraction test.** Mute each SFX in turn; if nothing is lost, delete it.

## When to break it

- **The sound is the joke.** In comedy, a scratch or button is comic timing. Land it after the punchline word, in the beat; never delay the punchline for it.
- **Rhythmic sets.** A list or quick montage can take one identical marker per item, closer than 4 s apart.
- **Tutorials.** UI clicks, typing and a completion chime can follow real on-screen actions at a low level, because they carry information.
- **Premium foley.** Close, real texture at a higher level can be the aesthetic, if it stays real and specific.
- **Creator memory or an explicit request.** "Never SFX", "memes welcome" or the full Hormozi-style package overrides these priors; sounds must still be licensed.

## Worked example

A listicle, about 21 s at 30 fps, from a medium-high energy creator. The bed sits about 19 LU under speech, and a counter card appears on each "Number".

> w0–w8 "Three apps that replaced my twelve hundred dollar assistant."
> w9–w20 "Number one: Reclaim. It schedules my deep work around my meetings automatically."
> w21–w32 "Number two: Superhuman. I hit inbox zero in eleven minutes a day."
> w33–w41 "And number three is the one nobody talks about…" [0.7 s pause]
> w42–w51 "Granola. It writes my meeting notes while I just… talk."
> w52–w59 "Follow for part two, I've got seven more."

- **w0–w8, none.** The hook title is on screen from frame 0, so a whoosh would mark nothing and sit on "Three".
- **w9, w21, w34: one soft wooden tick each,** using the same atonal sample. The sync point is on each counter's first visible frame, at the onset of "Number", and the tick peaks about 6 dB under speech. "Number" is a signpost, not a key term, so the onset is safe. Job: list structure. The ticks fall about 4 s apart, which is fine for a rhythmic set.
- **w13, calendar screenshot: shutter rejected.** It would land 1.3 s after a tick, and the cut is already the event.
- **w29, the "11 min/day" card: pop rejected.** It would sit on the stressed "eleven", which the voice and the card already carry.
- **The pause before w42: riser rejected.** The creator's silence is the tension, and a riser would make it sound like an ad. `music.md` may drop the bed out instead.
- **w51, "talk": button rejected.** The persona is not comedic, and the delivery lands the joke.
- **w52–w59, CTA: none.**

Result: three ticks in 21 s; ASR and ESTOI unchanged on w9, w21 and w34.

A calm educator gets the same counters and zero SFX. A "memes welcome" creator might get one licensed button about 150 ms after "talk".

## Anti-patterns

- A whoosh on every jump cut or punch-in; a pop on every caption page or emphasis word.
- SFX as "energy" with no visual event; Mickey-mousing every motion, out of favour in serious film through overuse ([Mickey Mousing](https://en.wikipedia.org/wiki/Mickey_Mousing)).
- Transients more than a frame early or late. Risers that resolve into nothing.
- Stingers and meme sounds on sincere, educational or premium lines. SFX over the hook's first word, a punchline or the CTA.
- Sub-bass booms, dings out of key with the bed, or SFX landing on music hits.
- Grab-bag palettes, or one stock sample for every kind of event.
- Low-bitrate, clipped, reverb-smeared or unlicensed files.

## Critic questions

1. Is every SFX tied to something visible on screen, or to a deliberate comedy button?
2. On one normal viewing, does no SFX register as a separate event, comedy buttons aside?
3. Is every word near an SFX fully intelligible?
4. Does every transient land on its visual, neither audibly early nor late?
5. Are SFX about 4 s apart or more (rhythmic sets aside), and are the hook's first word, punchlines, sincere lines and the CTA free of them?
6. Does every riser resolve into a real on-screen reveal?
7. Does every sound fit the tone, with no cartoon or meme sounds on sincere, educational or premium content?
8. With music present, is every tonal SFX in key, and does no SFX collide with a music hit?
9. Does the no-music export sound balanced, with no SFX jumping out or making the limiter pump?
10. Is every SFX free of distortion, clipping, codec artifacts and smeared tails?
11. On mute, does every moment an SFX supports still land?
12. Would muting any single SFX make the video worse? If not, cut it.

## Sources

- Moreno & Mayer 2000, coherence effect (irrelevant sounds): https://doi.org/10.1037/0022-0663.92.1.117
- Mayer 2023, Research-based principles for multimedia instruction: https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf
- Potter, Lang & Bolls 2008, Identifying structural features of audio: https://doi.org/10.1027/1864-1105.20.4.168
- Lang et al. 1999, Pacing and arousing content: https://www.tandfonline.com/doi/abs/10.1080/08838159909364504
- Newman & Schwarz 2018, Good sound, good research: https://journals.sagepub.com/doi/abs/10.1177/1075547018759345
- Yang et al. 2025, JTAER, auditory arousal and visual variation (Douyin): https://doi.org/10.3390/jtaer20020069
- Dixon & Spitz 1980, Detection of auditory visual desynchrony: https://doi.org/10.1068/p090719
- ITU-R BT.1359-1, Relative timing of sound and vision: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- EBU R 128 s1 (2020), Loudness parameters for short-form content: https://tech.ebu.ch/docs/r/r128s1.pdf
- Torcoli et al. 2019, Preferred levels for background ducking: https://doi.org/10.17743/jaes.2019.0052
- WCAG 1.4.7 and 2.3.1: https://www.w3.org/WAI/WCAG21/Understanding/low-or-no-background-audio.html ; https://www.w3.org/WAI/WCAG21/Understanding/three-flashes-or-below-threshold.html
- Walter Murch, Dense Clarity – Clear Density: https://transom.org/2005/walter-murch/
- Mickey Mousing: https://en.wikipedia.org/wiki/Mickey_Mousing
- Epidemic Sound, Audio mixing for video: https://www.epidemicsound.com/blog/audio-mixing-for-video/
- Descript, How to add sound effects to a video: https://www.descript.com/blog/article/how-to-add-sound-effects-to-a-video-2
- StringLabs, stock footage mistakes: https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/
- Unity, Audio Random Container fundamentals: https://docs.unity3d.com/6000.0/Documentation/Manual/AudioRandomContainer-fundamentals.html
- OpusClip Research, B-roll and visual effects (its own users' clips, 2026): https://www.opus.pro/research/broll-visual-effects-short-form
- Verizon Media/Publicis sound-off survey (Forbes): https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/
- Vendor docs and licences (ElevenLabs, Epidemic API and MCP, Stable Audio 3 card and Stability licence, Freesound API, Sonniss GDC, Pixabay): https://elevenlabs.io/docs/overview/capabilities/sound-effects ; https://developers.epidemicsound.com/ ; https://developers.epidemicsound.com/docs/mcp/ ; https://huggingface.co/stabilityai/stable-audio-3-small-sfx ; https://stability.ai/license ; https://freesound.org/docs/api/terms_of_use.html ; https://sonniss.com/gdc-bundle-license/ ; https://pixabay.com/service/license-summary/
- Internal: Yunicorn Studio design inputs (research_audio_music_sfx.md §7, design_doctrine.md §§1, 12, 17, research_style_trends.md, research_codebase.md §3, critique_feasibility.md)
