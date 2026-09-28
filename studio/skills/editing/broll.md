# B-roll: when and how to cut it in

Load this file at the finishing stage, after picture lock and after reframes and punch-ins, whenever you consider putting anything other than the creator's face on screen: a full-screen cutaway, split screen, picture-in-picture, over-the-shoulder card, creator over a screenshot, stock clip or AI still. It covers whether an insert should exist, its job, its mode, and where it enters and leaves in word IDs. Asset sourcing (licensing, retrieval, AI policy, the conform pipeline) is in `broll-sourcing.md`. The right answer is often "no insert": a talking-head short works because of a person, and b-roll takes that person away.

## Principles

1. **Every insert needs a job you can name in one line.** The jobs are: illustrate what is being said, prove a claim, show the product, make an abstract idea concrete, mark emphasis, cover a seam, or reset attention in a sagging middle. *Why:* interesting but irrelevant material lowers learning (g = −0.33, 68 effects from 58 papers, n = 7,521; photos as seductive details g = −0.48) [L]. Cutting extraneous material helped in 18 of 19 of Mayer's tests (median d = 0.86), most when the lesson is system-paced, as a video is [L]. Web eyetracking shows people skip generic stock photos of people and study informative ones (NN/g) [V].
2. **Show the sentence, not the noun.** "Connecting your bank takes ten seconds" wants the connection happening on screen, not a bank building. *Why:* relevant pictures alongside words raise understanding a lot (median d = 1.35, 13 tests) [L]. On topic is not relevant: real lightning-strike clips inserted into a lesson on how lightning forms lowered transfer, because they showed the topic, not the explanation (Mayer, Heiser & Lonn 2001) [L]. A keyword match lands on the cost side.
3. **The face is the default shot, and its value is its expression.** *Why:* a speaker who gestures, holds eye contact and emotes aids learning (embodiment, 16 of 17 tests, d = 0.58); a static speaker image barely does (image principle, d = 0.19, 3 of 7 tests negative) [L]. A face beside slides did not change recall, yet viewers strongly preferred it (n = 22) [L]. A documentary editor notes that eyes often "tell more of a story" than words [P]. In DOAC's Facebook tests of trailer openings, the guest in the chair "always came out on top" over b-roll [V]. Protect the face when it is doing something; a flat, static stretch is where an insert costs least.
4. **Proof must be real, and illustration must not pose as proof.** *Why:* in four experiments, related but non-probative photos made claims seem truer, especially unfamiliar ones (Newman et al. 2012) [L]. Stock "lab" footage under a health claim manufactures credibility. For proof, use the creator's screen, result, receipt or source.
5. **The picture lands with the word.** *Why:* narration and matching pictures shown together beat the same material shown one after the other (temporal contiguity, 8 of 8 tests, d = 1.31) [L]. Those tests compared whole segments, so frame-level timing is our inference [X]. A vendor guide warns that an overlay landing "slightly before or after a natural break in speech" feels amateur [V].
6. **The voice never changes.** *Why:* the unbroken voice is what makes a cutaway read as "still them, showing me something". Stock audio is muted.
7. **Leave when the point is over.** *Why:* if an insert holds past its phrase, the next idea is heard over an irrelevant picture, and the seductive-details cost returns.
8. **If the viewer must study it, keep the face in frame.** *Why:* with both visible, viewers split time (about 41% on the face) and switch every 3.7 s (n = 22) [L]. Split screen or PiP allows this; full-screen forces a choice. Across 6.9M edX sessions, face-plus-slides beat slides alone; producers proposed PiP to avoid "the jarring effect of switching repeatedly" (Guo et al. 2014) [A].
9. **Keep one camera world.** *Why:* grey HDR mismatches, cadence judder, staged stock acting and one LUT on everything make an insert read as pasted in [P].
10. **Match insert energy to the speaker's energy.** *Why:* in 12,842 Douyin influencer videos, visual variation congruent with vocal arousal went with higher engagement. In 2,511 TikTok product-promotion videos, shot count showed an inverted U [L, observational; marketing videos].
11. **Try the cheaper tool first.** In order: nothing, then a punch-in, then caption emphasis, then a designed card, then b-roll. *Why:* "Punch-ins keep the viewer in the room; B-roll takes them out of it" [V]. Over-editing is our documented failure: a pro made 1 cut where the old engine made 19 [I].
12. **Emotion and story outrank polish.** *Why:* Murch weights a cut's criteria as emotion 51%, story 23%, rhythm 10% and eye-trace only 7% [P]. An insert that is well timed and graded but steps on a line's feeling is still the wrong cut.

## Defaults and ranges

These are starting priors. Give a one-line reason whenever you leave a range.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Short clips using any b-roll | 6.0% (733,762 of 13.5M, Jan–Mar 2026): prevalence, not effect; method unpublished | [V] vendor data | [OpusClip](https://www.opus.pro/research/broll-visual-effects-short-form) |
| Full-screen cutaway hold | 1–3 s | [V] guidance, no data | [AutoClip](https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers) |
| Ceiling before focus drifts | 3–5 s (recommended, not measured) | [V] | [OpusClip](https://www.opus.pro/research/broll-visual-effects-short-form) |
| Meme or reaction flash | 0.5–1.5 s | [I] | research_broll.md |
| Floor for a named literal object | about 0.5 s; named targets are detected at 13–80 ms (contested) | [L] → [X] | [Potter 2014](https://link.springer.com/article/10.3758/s13414-013-0605-z) |
| Text-bearing insert | at least 0.3 s per word + 1 s, the house dwell rule shared with cards, titles and labels | [P] rate + [X] 1 s margin | [BBC subtitle guidelines](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Entry timing | at the keyword onset or the phrase break just before it, up to 2 frames early; not more than about 45 ms late | [X], from lip-sync detectability (+45 / −125 ms) | [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) |
| Runtime share, if the style uses b-roll | 15–25% "as overlay", weighted to the middle; beyond about a third it stops feeling like "a moment from a conversation". Face-kept layouts cost less | [V] + [X] | [AutoClip](https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers) |
| Hook protection | no full-screen cutaway in the first ~1.5–3 s (the vendor says 3 s: cutting away "reads as an ad") | [V] | [AutoClip](https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers) |
| Seam hidden under an insert | at least 0.25 s of insert on each side | [X] | — |
| Face between full-screen inserts | about 1.5 s or more, unless a montage is intended | [X] | Guo et al. 2014 ("jarring" switching) |
| Face-kept layout | use when the insert runs over ~3 s or must be read | [L] + [V] | [Kizilcec 2014](https://rene.kizilcec.com/wp-content/uploads/2014/01/final_version2.pdf) |
| Persistent top panel, face below | top ~40–45%, updating on the beat (11 changes in 77 s) instead of cutaways | [V, n=4] | [PandaStudio, Abdaal](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/) |
| Subject in an inset | at least ⅓ of the inset frame | [P] | research_broll.md |
| Insert grade match | exposure, white balance and contrast only, at ≤0.5 transfer strength; HDR tone-mapped once | [I] | critique_feasibility.md |
| Photoreal AI insert | an AI label cost posts about 7–8% of likes (1.1M TikTok posts, 8 experiments) by weakening the parasocial bond | [L] + [A] | [JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493) |
| Product visible on screen | +65% brand affinity, +25% recall (ads only; TikTok/Lumen 2021 SMB study) | [A] | [TikTok](https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads) |

**Frequency by style.** Priors per 60 s, all [X], from the doctrine's style table and the sources above. Zero is valid in every row; as one guide puts it, "There is no fixed ratio" [V].

| Style | Inserts / 60 s | Full-screen share | Typical jobs and modes |
|---|---|---|---|
| Educational | 0–4 | 0–15% | Proof, cards, screenshots; no decoration |
| Storytime | 0–2 | 0–10% | A real photo of the person or place |
| Comedy | 0–2 | 0–5% | Only when the joke is visual |
| Hot take | 0–2 | 0–10% | The headline or post being answered |
| Listicle | 0, or 1 per item | 10–30% | Item visual or card |
| Tutorial / demo | Screen may dominate | 30–70% as split or PiP | Screen recording, hands, result first |
| Sales / UGC | 2–5 | 20–40% | Product in real use, lo-fi |
| Founder / brand | 1–3 | 5–15% | Proof: product, team, numbers |
| Commentary / news | Background most of the runtime | Creator over screenshot | The article or post |

Measured signatures: Hormozi uses zero b-roll, memes or screenshots (n=3), and Abdaal is on camera about 95% of the time, getting his visual change from a top panel rather than cutaways (n=4) [V].

## How to decide

1. **Start from the locked cut.** B-roll never repairs story. If the cut fails the radio test, go back to the story cut.
2. **Mark protected spans by word ID:** the hook, punchlines and reveals, sincere or emotional lines, the CTA and payoff, and moments where the creator gestures at or demonstrates something on camera. They get no full-screen insert by default.
3. **Find candidates.** Walk the unprotected beats and ask: what would the viewer want to *see* here that the face cannot show? Triggers include a specific object, place, screen or result; a number; a claim a skeptic would doubt; a list; a mid-thought seam left by the fine cut; or a static stretch of 8 s or more where the face itself has gone flat.
4. **Name the job.** No job, no insert.
5. **Try cheaper tools.** Would a punch-in, a caption emphasis or nothing do the same job? A seam in the middle of a thought can take a clearly different framing instead (see `framing-and-zooms.md`). Use a cutaway when there is also something worth showing.
6. **Pick the asset class** (see `broll-sourcing.md`). In order of preference:
   - the creator's own footage, screens and results;
   - real screenshots and screen recordings;
   - designed cards for numbers and lists (numbers are never stock footage);
   - specific, licensed stock for mood;
   - a stylized AI still with slow parallax.

   Photoreal AI video is rare and flagged. If nothing clears the bar, use nothing. When the creator's own product or hands are the right picture, ask them for 2–3 pickup shots.
7. **Pick the mode.**
   - *Full-screen cutaway:* a fast, literal read (1–3 s) that the voice names, or a seam cover.
   - *Split screen, top/bottom:* evidence or demos over ~3 s, comparisons, or anything to be studied while the face still matters. Content takes about 40–50% on top (Abdaal's panel ~40–45% [V]); the face sits below, eyes clear of the bottom UI band. Readable screens want more room, but above ~50% the face panel can no longer keep the eyes above y ≈ 1248, so crop the screen to the element instead [X].
   - *Picture-in-picture:* the creator stays large and the object is secondary.
   - *Over-the-shoulder card:* a number, quote or headline in the upper band beside or above the head, used when the line is a payoff and the face must stay. For a list or framework, one persistent top panel that updates per point can replace a run of cutaways.
   - *Creator over screenshot:* commentary, news or reactions. This is TikTok's native green-screen grammar.
8. **Set in and out points on word IDs.**
   - *In:* at the onset of the word that names the thing, or up to 2 frames early. Prefer a phrase onset or breath within about 0.3 s before it, and always when the image needs a moment to read [X]. If the boundary is uncertain, err early: early reads as anticipation, late as lag.
   - *Out:* at the phrase end, ideally in the breath gap, before the next idea.
   - *Return frame:* come back on a live face (eyes open, not mid-blink, expression fitting the next line). If that frame is weak, move the out point a word [X].
   - Place the insert's subject where the viewer's eye already was, near the speaker's eyes (Murch's eye-trace) [P].
   - Let content set the lengths, not a timer.
   - When covering a seam, keep at least 0.25 s of insert on each side. The audio seam still needs its own crossfade: an insert hides picture, not sound.
9. **Set motion inside the insert.**
   - Silent footage may run 2–3×.
   - Slow motion only from high-frame-rate sources: the factor must be at least output fps ÷ source fps, so a 120 fps Slo-mo clip gives 0.5× in a 60 fps output and a 60 fps clip cannot slow at all there (`speed.md`).
   - Give stills a 5–10% push over the hold [X].
   - Start clips with the action under way; trim settles, fades and dead frames [X].
   - Crop screenshots and recordings to the element the voice names, and mark it with one box, arrow or underline (visual signaling: 11 of 12 tests, d = 0.71, best used sparingly) [L].
   - A calm speaker gets slow, observational shots; a high-energy speaker gets more motion and shorter holds.
10. **Conform, then budget.** Conform: one transfer function, native cadence, matched exposure, white balance and contrast, and stock audio muted. Budget:
    - share against the style prior;
    - no reused clip unless it is a deliberate callback;
    - varied durations;
    - no back-to-back full-screen inserts unless a montage is intended; if you would cut away three times in ~10 s, hold one split or PiP instead;
    - one visual system per video: one split ratio, one PiP frame, one card template;
    - text clear of captions and platform UI.
11. **Watch in motion, composited, at full resolution.** A contact sheet misses mid-clip logos, text and faces, subjects drifting out of the 9:16 crop, AI morphing and interpolation warps.
12. **Run the subtraction test.** For each insert, ask: is the video worse without it? If not, delete it.

## When to break it

- **Visual hook.** If the most arresting thing is visual (a tutorial's finished result, a before/after, a surprising product moment), open on it. The voice starts at once, and the face returns by about 2 s.
- **Deliberate montage.** A spoken list of quick examples can take a rhythmic run of 0.5–0.8 s inserts.
- **Demos.** Hold the screen as long as the step takes, in split screen or PiP, well past 5 s.
- **The image is the emotion.** A real photo of the grandmother being described can deepen the line. Use a slow push, then an L-cut back to the face.
- **The cutaway is the joke.** When cutting to the photo *is* the punchline, the cut is comic timing. Land it on the beat.
- **Broken A-roll.** If focus hunts or exposure blows out for a stretch, relevant inserts may run longer to cover it.
- **Creator memory.** "Never b-roll" or "memes welcome" overrides these priors.

## Worked example

A founder with calm-to-medium energy, about 32 s. The creator supplied screen recordings of the old and new onboarding, plus an analytics screenshot.

> w0–w9 "We doubled signups by deleting eleven screens from our onboarding."
> w10–w29 "Here's what the old flow looked like: fourteen screens, and you had to verify your email before you saw anything."
> w30–w33 "Most people just… [0.8 s pause] left."
> w34–w50 "So now you connect your bank, [false start w40–w42 cut] and you instantly see where your money went."
> w51–w61 "Signups went from nine percent to eighteen percent in three weeks."
> w62–w73 "If your onboarding asks for something before it gives something, cut it."

- **w0–w9, face.** The hook. A rising-graph stock clip was rejected: it breaks hook protection and is a cliché.
- **w13–w29, split screen, about 5.5 s.** The old-flow recording sits on top, cut into fourteen quick holds so that the last one lands on "anything" (w29). A highlight box marks the "Verify your email" screen at w23 (visual signaling, d = 0.71 [L]). Job: proof. Split, not full-screen, because it runs over 3 s and the creator's quiet disbelief carries the tone.
- **w30–w33, face.** The beat lives in the pause. A stock "frustrated woman closes laptop" was rejected: generic, a stock face under negative narration, and it kills the beat.
- **w37–w50, full-screen cutaway, about 2.4 s.** The new-flow capture enters on "connect" (w37), about 0.6 s before the seam between w39 and w43, and exits at the end of "went" (w50), about 1.8 s after the seam. Two jobs: show the product and hide a mid-thought seam; the audio seam gets its own crossfade.
- **w51–w61, over-the-shoulder card.** The card reads "9% → 18% signups, 3 weeks", with the real dashboard crop beneath. It enters on "nine" (w54), resolves "18%" on "eighteen" (w57), and holds until 0.5 s after w61. The face stays because this is the payoff claim. Without the real screenshot, the card shows the figure plainly rather than faking a dashboard.
- **w62–w73, face.** This is the payoff and CTA. The video ends about 0.4 s after w73.

Result: three inserts, about 8% full-screen. Counting the split and card, inserts cover about a third of the runtime, above the 15–25% prior; the stated reason is that two keep the face and all three are the creator's own proof. Without those recordings, the right edit is one card or nothing.

## Anti-patterns

- Noun matching: "bank" becomes a bank building, "growth" becomes a sprouting plant.
- Cutting away in the hook, on the punchline or over the CTA.
- Generic stock: typing hands, handshakes, traffic, pouring coffee, staged office acting.
- B-roll on a timer, every insert the same length.
- An insert that arrives after its word or outstays its phrase.
- Stock audio leaking under the voice.
- A grey HDR/SDR mismatch, 24 fps judder inside 60 fps A-roll, or one LUT on everything.
- Stock charts or "labs" posing as evidence.
- Stock faces under negative narration.
- Watermarks, other platforms' logos, or undisclosed photoreal AI.
- The wrong lookalike product, such as a competitor's app when the creator names theirs.
- Insert text fighting the captions, or captions unreadable over busy footage.
- Horizontal clips or whole desktop screenshots crushed into 9:16, subject cropped off or nothing marked.
- Seam covers that start or end right at the seam, or return to the face mid-blink.
- Ping-pong: face, insert, face, insert every second or two, when one held split or PiP would do.

## Critic questions

1. Does every insert show what the voice is saying at that moment, not just a keyword?
2. Is the face on screen for the opening ~2 s, every punchline or reveal, the emotional lines and the CTA (unless the brief states an exception)?
3. Does every insert appear on or just before its word, never noticeably after?
4. Does every full-screen insert leave by the end of its phrase, with none over 5 s outside a face-kept demo?
5. Is the voice identical in tone and level through every insert, with no stock audio audible?
6. Do all inserts look shot in the same world as the creator (exposure, white balance, contrast, smooth cadence)?
7. Is every insert specific enough that it could not be pasted into another creator's video on another topic?
8. Is every insert used as proof real evidence (the creator's own screen, result or source)?
9. Where an insert covers a seam, is the jump invisible on return and the audio seam inaudible?
10. Is every text-bearing insert readable in the time given, and clear of captions and platform UI?
11. Is every insert free of watermarks, other platforms' logos and undisclosed photoreal AI?
12. Does every return to the face land on a live frame, with no face-insert ping-pong?
13. Would removing any single insert make the video worse? If not, cut it.

## Sources

- AutoClip (vendor): https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers
- Pireel (vendor): https://pireel.com/en/blog/b-roll-and-graphics-for-talking-head-video
- OpusClip Research 2026 (vendor): https://www.opus.pro/research/broll-visual-effects-short-form
- Sundararajan & Adesope 2020, seductive-details meta-analysis: https://link.springer.com/article/10.1007/s10648-020-09522-4
- Mayer 2023, multimedia design principles: https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf
- Mayer, Heiser & Lonn 2001: https://doi.org/10.1037/0022-0663.93.1.187
- Kizilcec et al. 2014: https://rene.kizilcec.com/wp-content/uploads/2014/01/final_version2.pdf
- Guo, Kim & Rubin 2014: https://dl.acm.org/doi/10.1145/2556325.2566239
- Newman et al. 2012, truthiness: https://link.springer.com/article/10.3758/s13423-012-0292-0
- NN/g, Photos as web content: https://www.nngroup.com/articles/photos-as-web-content/
- Potter et al. 2014: https://link.springer.com/article/10.3758/s13414-013-0605-z
- ITU-R BT.1359: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- BBC Subtitle Guidelines: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
- Yang et al. 2025, JTAER: https://doi.org/10.3390/jtaer20020069
- Xiao, Li & Mou 2026, Internet Research: https://www.emerald.com/intr/article/36/1/154/1255369/Exploring-user-engagement-behavior-with-short-form
- Carney, Riveros & Tully 2026, JCR: https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493
- TikTok for Business, Creative best practices: https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads
- TikTok Newsroom, Green screen effect: https://newsroom.tiktok.com/en-gb/green-screen-effect-on-tik-tok/
- McDonnell, DOAC editor interview: https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven
- Hullfish, Art of the Cut (Meiklejohn): https://www.provideocoalition.com/art-of-the-cut-with-documentary-editor-neil-meiklejohn/
- Murch 2001, In the Blink of an Eye: https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf
- PandaStudio breakdowns, Abdaal (n=4) and Hormozi (n=3): https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/ ; https://www.writepanda.ai/blog/hormozi-style-shorts-editing
- String Labs, Stock footage mistakes: https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/
- YouTube Help, Altered or synthetic content: https://support.google.com/youtube/answer/14328491
- Internal: Yunicorn Studio design inputs (research_broll.md, research_cuts_pacing.md, design_doctrine.md, critique_feasibility.md)
