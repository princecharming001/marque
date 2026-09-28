# Transitions and motion graphics

Load this file at finishing, after picture lock, punch-ins and b-roll placement. Use it whenever a seam is about to become anything other than a plain cut (whip, flash, zoom, match cut, dissolve), or a designed graphic is about to go on screen (stat callout, list marker, comparison card, arrow, circle, progress bar, lower third). It also sets timing, easing and brand rules for animation; captions, punch-ins and inserts have their own files. The default is "hard cut, no graphic", and a video with zero stylized transitions and zero graphics is a complete edit, often the right one. Over-editing is the failure this file guards against.

## Principles

1. **The cut is the transition.** Almost every seam is a hard cut or a J/L cut. *Why:* Murch weights a cut's worth as emotion 51%, story 23%, rhythm 10%, eye-trace 7%, screen plane 5%, 3D space 4% [P]. An effect that only adds novelty serves none of the top three; what viewers remember "is not the editing" but how they felt (Murch) [P]. Only 2.4% of 12.2M organic clips processed by OpusClip's engine (Jan–Mar 2026) used any transition. That is prevalence under one tool's defaults, not an effect [V].
2. **A stylized transition is punctuation for a change of section, place or time. It does not belong inside a thought.** *Why:* cuts to an unrelated scene (always a change of content) slowed reactions and hurt memory for what followed; cuts linked by picture or sound cost less (Lang et al. 1993) [L]. A section change already pays that cost, so a transition there announces a real break; mid-thought it announces a break that is not there [X]. A dissolve tells viewers "time has passed" (Adobe) [P].
3. **Hide seams with content, not effects.** *Why:* a quarter of same-scene edits went unnoticed, and a third when the cut landed on a sudden motion onset (Smith & Henderson 2008) [L]. The speaker's own movement hides a seam better than a whoosh.
4. **Motion needs a job: point, structure, or show change.** *Why:* animation beat static pictures by a medium margin across 26 studies (Höffler & Leutner 2007) [L], but where it seemed to win it usually carried more information (Tversky et al. 2002) [L]. Interesting but irrelevant additions lower learning, g = −0.33 (Sundararajan & Adesope 2020) [L].
5. **Enter once, then hold still.** *Why:* motion onset captures attention; ongoing motion does not help (Abrams & Christ 2003) [L]. Caption-reliant TikTok users wanted captions "static, right there, simple, clean" and called heavy on-screen motion "jarring" (McDonnell et al. 2024, interviews, n = 9) [L]. A loop keeps pulling the eye off the face.
6. **The face is the main graphic, and text competes with it.** *Why:* in free viewing, faces drew 16.6 times and text 11.1 times more looks than size-matched regions, and people found it hard not to look even when looking cost them (Cerf et al. 2009) [L].
7. **One focal element at a time, beyond the captions.** *Why:* signalling helps retention (g = 0.53, 103 studies; Schneider et al. 2018) [L]; that it works only while sparse is our reading [X]. In 249 eye-tracked print ads, dense feature clutter lowered attention to the brand and liking of the ad, while deliberate design raised both (Pieters et al. 2010) [L].
8. **Entrances decelerate; exits accelerate and are quicker.** *Why:* Disney's "slow in and slow out" [P]; NN/g recommends ease-out for entrances and slightly longer entrances than exits [P]. Linear motion looks mechanical.
9. **Consistency is the brand.** Each element type keeps one font, colour, placement logic and motion, within and across videos. *Why:* familiar patterns let attention go to the content instead of the interface (NN/g) [P]. A marker that looks the same every time reads as structure.
10. **Respect the phone's chrome and viewers' safety.** *Why:* Meta's Reels ad spec asks for 14% top, 35% bottom and 6% per side to be kept clear [A]. More than 3 flashes a second is a seizure risk (WCAG 2.3.1, ITU-R BT.1702) [P].
11. **Match intensity to the creator's energy.** *Why:* fast pacing plus arousing content overloads viewers and lowers recall (Lang 1999) [L]. TikTok asks for "DIY or not overly polished" [A]. On a calibration take a pro made 1 cut where our old engine made 19 [I].

## Defaults and ranges

Frames are at 30 fps output; double them at 60 fps. These are priors: give a one-line reason whenever you leave a range. No study links transitions or graphics to organic Shorts retention [X].

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Seams that are hard or J/L cuts | 90% or more; 100% is normal | [V] | [Superdirector](https://superdirector.app/learn/transitions-cuts) |
| Stylized transitions per video | 0 by default; about 2 at most, at section boundaries only. A whip "earns its place once per clip, maximum" | [P] / [V] | design_doctrine.md; [Superdirector](https://superdirector.app/learn/transitions-cuts) |
| Whip pan (synthetic) | 6–12 frames (0.2–0.4 s), directional blur, visual cut at peak blur | [P] length; [X] frames | design_doctrine.md ("under 0.4 s") |
| Flash / dip to white | 2–4 frames, one flash per transition. A flash is any pair of opposing luminance swings, so fast bright-dark cutting counts. At most 3 in any 1 s; no saturated-red flashes | [X]; [P] limit (standards) | [WCAG 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html); [ITU-R BT.1702](https://www.itu.int/rec/R-REC-BT.1702) |
| Zoom transition (scale ramp through the cut) | 4–8 frames, accelerating in, decelerating out | [X] | — |
| Dissolve | None inside talking head; 8–15 frames only for a real time jump or b-roll montage. A 2 s dissolve in a 15 s clip "burns 13 percent of the runtime" | [P] use; [X] frames; [V] quote | [Adobe](https://www.adobe.com/creativecloud/video/post-production/transitions.html); [Superdirector](https://superdirector.app/learn/transitions-cuts) |
| J-cut audio lead | 1–3 frames; up to about 0.5 s at a section change | [V] / [X] | [Superdirector](https://superdirector.app/learn/transitions-cuts) |
| UI animation durations | 100–500 ms; 200–300 ms for substantial changes; at 500 ms they "feel like a real drag". The more often an animation repeats, the shorter and subtler it should be | [P] | [NN/g](https://www.nngroup.com/articles/animation-duration/); [Carbon](https://github.com/carbon-design-system/carbon/blob/main/packages/motion/src/dtcg/motion.json) (150–240 ms moderate, 400 ms large) |
| Graphic entrance / exit | Enter 5–9 frames (170–300 ms), 0–2 frames before the trigger word; exit 3–6 frames at a clause end or seam | [X] on [P] | NN/g |
| Easing | Enter cubic-bezier(0, 0, 0.3, 1) or Material emphasized-decelerate (0.05, 0.7, 0.1, 1); exit (0.4, 0.14, 1, 1) or (0.3, 0, 0.8, 0.15). Linear only for opacity and progress fills. Overshoot 0–5% of scale; Remotion's default spring (damping 10) bounces, damping 200 settles in about 23 frames at 30 fps | [P] / [V] | [Carbon](https://github.com/carbon-design-system/carbon/blob/main/packages/motion/src/dtcg/motion.json); [Material](https://github.com/material-components/material-web/blob/main/tokens/versions/v0_192/_md-sys-motion.scss); [Remotion](https://www.remotion.dev/docs/transitions/timings) |
| Card or callout dwell | at least 0.3 s per word (BBC reading rate, 160–180 wpm) + 1 s, and never gone before the spoken referent ends | [P] rate; [X] +1 s | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Callouts | at most 1 per 5–10 s; 2–3 words beside the referent (labels next to a diagram: retention d = 0.47, 0.70; no transfer gain) | [X] / [L] | [Mayer & Johnson 2008](https://doi.org/10.1037/0022-0663.100.2.380) |
| Safe band (1080×1920) | House band (`platforms.md`): x 65–888, y 288–1248; titles y 288–600. Built from Meta's 14/35/6% ad spec and Google's official Shorts vertical-ads overlay (288 px top), which supersedes third-party maps such as poster.ly's 380 px; TikTok from third-party maps | [I] on [A] / [V] | [Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels); [Google overlay](https://services.google.com/fh/files/misc/youtubesafezoneoverlay_vertical_final.png) |
| Brand kit | 1 display face plus the caption face, 1 accent plus white/black, 1 motion preset per element type | [X] on [P] | [NN/g](https://www.nngroup.com/articles/consistency-and-standards/) |
| Progress bar | Off unless opted in. Web surveys (32 experiments): constant bars did not cut drop-off; slow-to-fast bars raised it. Influencer videos: chapter bars lowered purchase intention via impatience. Neither measured Shorts retention | [L], other domains | [Villar et al. 2013](https://doi.org/10.1177/0894439313497468); [Yang et al. 2025](https://ideas.repec.org/a/inm/orserv/v17y2025i4p168-189.html) |

## How to decide

1. **Read the brief.** Note the style profile, measured energy (vocal arousal, words per minute), platform and creator memory (brand kit, allowed transitions, notes such as "hates whooshes"). Write a transition budget and a graphics budget into the Edit Brief. Zero is valid.
2. **Classify every seam in the locked cut by word ID.**
   - *Inside a thought:* hide it with a punch-in, a cutaway, or a cut on motion onset, not a stylized transition. A crossfade does not hide a same-framing jump; it morphs the face. Truly hidden cuts need frame interpolation (Berthouzoz et al. 2012) [P], which the engine lacks.
   - *Between beats in a section:* hard cut. Cuts within one scene raised memory without adding load (Lang et al. 2000) [L].
   - *Section boundary* (list item, act change, place or time jump): the normal home for a stylized transition.
   - *Insert entry or exit:* hard cut at the word onset (`broll.md`).
3. **At each section boundary, try hard cut, then J/L cut, then a motivated transition.** Use a stylized transition only when the style or energy supports it, the footage supplies a motivation (the creator's hand over the lens, a head turn, a location change, a real time jump), and budget remains. Motion in the footage calls for a match cut or cut on action. A location change in energetic content calls for a whip. A reflective time jump calls for a dip or dissolve. A list item calls for a hard cut plus a marker, so the graphic does the punctuating.
4. **Place it on the audio, not over it.** Centre the transition in the gap between the sections' boundary words. No flash peak, blur peak or dissolve midpoint may cover a word onset. Where the gap is short, let the next section's audio lead as a J-cut; an audio link makes a cut "related" in Lang's terms [X]. Keep the key number or claim out of the first second after any section change, because memory is worse just after unrelated cuts (Lang et al. 1993) [X on L]. Sound is part of the transition: a whip's whoosh peaks on the cut frame (`sfx.md`), a performed transition brings its own sound, and a hard cut needs none. With music, favour boundaries on a downbeat (`music.md`).
5. **List graphic candidates from the transcript** and give each a job:
   - *point:* arrow, circle or highlight box on an inserted screenshot;
   - *structure:* list marker or counter;
   - *evidence:* stat callout or comparison card;
   - *identify:* lower third, for a guest or a credential that changes how a claim lands. The creator's own name on their own channel is usually noise [X].

   Cues that point help viewers find things and sometimes improve learning; cues that show structure or relations are less proven (de Koning et al. 2009) [L]. Try the cheapest tool first: caption emphasis, then a callout, then a card, then an animated diagram. If the captions already carry the number clearly, skip the card.
6. **Lay it out from the face track, after punch-ins.** Put each graphic beside what it names and on the word that names it (contiguity, Ginns 2006) [L], usually in the upper band or over the shoulder. Keep it off the eye-to-mouth box and above y 1248. Anchor graphics to the screen, not the face, and re-check face clearance in every punched framing. Apart from caption paging, only one element changes at a time. If a trigger word falls within about 6 frames of a seam or punch-in, land the graphic on the seam frame so the viewer sees one change, not two [X]. When a card carries a number, drop the caption accent on that page.
7. **Specify the motion.**
   - Enter on the trigger word, decelerating, then hold still.
   - Exit accelerating, at a clause end or seam.
   - Stagger parts of one graphic by 2–4 frames.
   - Counters land on their final value by the end of the spoken number.
   - Arrows and circles draw on in 6–10 frames and stay put.
   - Leave at least 6 frames between one element's exit and the next one's entrance.
8. **Brand pass.** Take fonts and colours from the brand kit; if there is none, use the house caption face and one accent. Use the same preset for every instance of an element type. Use templates and Lottie only; per-video animation code renders inconsistently, and "the results tend to look amateur" (critique_feasibility.md) [I].
9. **Render and watch** at phone size, with sound and muted; frame-step every transition. Code checks flash count and the safe band. Delete anything you cannot justify in one line.

## When to break it

- **The transition is the content.** In outfit changes, glow-ups and travel reveals, the transition is the payoff. Build it from the creator's performed motion.
- **Creator-performed transitions** (a hand over the lens, a jump, a clap) are performance. Keep them and cut on the darkest or peak-motion frame. They count toward the budget, but always beat an added effect.
- **A list as a system.** A high-energy listicle may repeat one identical short whip or zoom at every item, even beyond 2, because it marks structure.
- **Comedy.** A smash cut or a deliberately jarring transition can be the joke, but never on the setup.
- **Storytime time jumps.** A dip to black or a 10–15 frame dissolve for "six months later" is honest grammar.
- **Sales and UGC ads.** TikTok's ad guidance recommends "transitions, stickers, and graphics" [A], and seamless transitions went with +60% recall in creator ads, under the line "editing shouldn't be frenetic" (Lumen 2022) [A]. Test them, and prefer seamless to flashy.
- **Tutorials.** Arrows, highlight boxes and zooms on screen recordings are core grammar and can run denser, one at a time.
- **Text behind the subject.** A hook title partly behind the creator's head is an untested practitioner trend [X]. Use it on request or when the style profile lists it, on a still subject with the key word fully visible; frame-step hair and fingers for matte flicker, and A/B it (`captions-and-text.md`).
- **Creator memory and explicit requests** override defaults, never the flash limit or safe band.

## Worked example

A calm-to-medium educator (arousal 0.40, about 150 wpm), 44 s, 1080×1920 at 30 fps. Brand kit: Inter Tight Black, amber accent, 85% black plates.

- **Hook (w0–w8):** "I fixed my sleep by quitting three things."
- **Item 1 (w9–w32):** "One: coffee after noon. Caffeine has a half-life of about five hours, so a 3 p.m. latte is still half in you at 8." ("five" = w19.)
- **Item 2 (w33–w47):** "Two: my phone in bed. It wasn't the blue light. It was the scrolling." The take ends with the creator's palm covering the lens.
- **Item 3 (w48–w81):** The next take opens with the palm pulling away. "Three: sleeping in on weekends. Two extra hours on Saturday is basically flying to Denver and back by Monday."
- **CTA (w82–w90):** "Pick one tonight. Tell me which in the comments."

Decisions:

1. **w8|w9, w32|w33, w81|w82: hard cuts.** These are section boundaries, but a whip at w32|w33 would be the only energetic move in a calm video, an energy mismatch.
2. **w47|w48: the palm transition.** Cut while the lens is fully covered in both takes, keeping about 3 dark frames from each (about 0.2 s of dark), and add no effect. "Three" (w48) leads the uncovering by 3 frames. Performed, on a section boundary and hiding the take change, it is the video's one stylized transition. One dark dip is 1 flash. The key claim arrives 1.4 s later.
3. **List marker.** One chip at y 390–470, left of the head, inside the title zone (y 288–600):
   - it enters on w9 over 6 frames (scale 0.9 to 1.0, fade, decelerating);
   - it flips to "2" on w33 and "3" on w48 with a shorter 4-frame move, since it repeats;
   - it exits after w81.

   *Job:* structure.
4. **Stat callout: "Caffeine half-life ≈ 5 h".** It sits at x 610–880, y 560–760, clear of the face box.
   - It enters 1 frame before w19 over 8 frames, with no overshoot.
   - It holds about 5.5 s against a 2.2 s dwell minimum.
   - It exits in 5 frames inside the 400 ms section pause, 7 frames before the marker flips.
   - The "five hours" caption page gets no accent colour.

   *Job:* evidence.
5. **Rejected:**
   - a "blue light" cross-out, because the contrast is comic timing and the face carries it;
   - a plane animation for "Denver" (a seductive detail);
   - a progress bar (not requested);
   - a hook flash;
   - whooshes on the markers.
6. **Totals:** 1 performed transition, 1 marker, 1 card. Never two animating elements at once, and all inside y 390–1248. A calmer creator could ship the marker alone.

## Anti-patterns

- Preset-pack transitions: star, heart and iris wipes, spins, 3D cubes, page curls. Even film guides warn an iris wipe "may break the audience's attention" (StudioBinder) [P].
- Glitch, RGB split, light leaks or zoom blur on every cut. If you cannot defend a transition "in one sentence connected to the emotion", make it a hard cut (Superdirector) [V].
- Dissolves between takes of the same framing, which morph the face and falsely signal elapsed time. Use a punch-in, cutaway or cut on motion (`framing-and-zooms.md`).
- A fade from black at the start, or a fade to black at the end.
- Graphics that bounce, pulse or loop for their whole life; emoji rain; shaking text.
- Two elements animating at once, or a card stacked on the captions and a hook title.
- Graphics over the eyes or mouth, or in the bottom 35%.
- Four fonts, a new accent per card, or different motion for each instance.
- Linear slides, 1 s entrances, springy overshoot on sincere lines.
- A card whose number differs from the spoken one, or that arrives early or leaves unread.
- A default progress bar, or one that crawls early and races late [L].

## Critic questions

1. Is every seam inside a spoken thought free of stylized transitions?
2. Is the stylized-transition count within budget, and is each on a section, place or time change with a visible motivation in the footage?
3. Do all transition peaks (flash, blur, dissolve midpoint) avoid word onsets?
4. Does each graphic appear within 2 frames of its trigger word (or on an adjacent seam) and leave at a clause end or seam?
5. After its entrance, is every graphic still?
6. Apart from caption paging, is at most one element animating at every moment?
7. Does every graphic stay inside the safe band and off the eyes and mouth in every frame, after punch-ins?
8. Do all graphics of one type share font, colour and motion?
9. Does every number and word on a graphic match the speech, and stay up long enough to read?
10. Is the video free of anything flashing more than 3 times in one second, including fast bright-dark cutting, and of saturated-red flashes?
11. Watching muted at phone size, would removing each transition or graphic make the video worse? (Remove any that fail.)

## Sources

- Murch, *In the Blink of an Eye* pp. 5–26: https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf
- Smith & Henderson 2008, Edit blindness: https://bop.unibe.ch/JEMR/article/view/2264
- Berthouzoz, Li & Agrawala 2012, Cuts and transitions in interview video: https://www.floraine.org/research/video-transitions/
- Lang et al. 1993, Related and unrelated cuts: https://doi.org/10.1177/009365093020001001
- Lang et al. 2000, Effects of edits on arousal, attention and memory: https://doi.org/10.1207/s15506878jobem4401_7
- Lang et al. 1999, Pacing and arousing content: https://www.tandfonline.com/doi/abs/10.1080/08838159909364504
- Abrams & Christ 2003, Motion onset captures attention: https://doi.org/10.1111/1467-9280.01458
- Cerf, Frady & Koch 2009, Faces and text attract gaze: https://doi.org/10.1167/9.12.10
- Pieters, Wedel & Batra 2010, Stopping power of advertising: https://doi.org/10.1509/jmkg.74.5.048
- Höffler & Leutner 2007, Animation vs static pictures: https://doi.org/10.1016/j.learninstruc.2007.09.013
- Tversky, Morrison & Bétrancourt 2002, Animation: can it facilitate?: https://doi.org/10.1006/ijhc.2002.1017
- de Koning et al. 2009, Attention cueing in animations: https://doi.org/10.1007/s10648-009-9098-7
- Schneider et al. 2018, Signalling meta-analysis: https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581
- Mayer & Johnson 2008, Redundancy principle revised: https://doi.org/10.1037/0022-0663.100.2.380
- Ginns 2006, Contiguity meta-analysis: https://doi.org/10.1016/j.learninstruc.2006.10.001
- Sundararajan & Adesope 2020, Seductive details: https://link.springer.com/article/10.1007/s10648-020-09522-4
- Villar, Callegaro & Yang 2013, Progress indicators: https://doi.org/10.1177/0894439313497468
- Yang et al. 2025, Chapter progress bars: https://ideas.repec.org/a/inm/orserv/v17y2025i4p168-189.html
- McDonnell et al. CHI 2024, TikTok captioning: https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf
- WCAG 2.3.1, Three flashes: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- ITU-R BT.1702-3, Photosensitive seizures: https://www.itu.int/rec/R-REC-BT.1702
- NN/g, Animation duration: https://www.nngroup.com/articles/animation-duration/
- NN/g, Consistency and standards: https://www.nngroup.com/articles/consistency-and-standards/
- Material 3 motion tokens: https://github.com/material-components/material-web/blob/main/tokens/versions/v0_192/_md-sys-motion.scss
- IBM Carbon motion tokens: https://github.com/carbon-design-system/carbon/blob/main/packages/motion/src/dtcg/motion.json
- Twelve basic principles of animation: https://en.wikipedia.org/wiki/Twelve_basic_principles_of_animation
- Remotion spring and transition timings: https://www.remotion.dev/docs/spring ; https://www.remotion.dev/docs/transitions/timings
- Adobe, Video transitions: https://www.adobe.com/creativecloud/video/post-production/transitions.html
- StudioBinder, Editing transitions: https://www.studiobinder.com/blog/types-of-editing-transitions-in-film/
- Superdirector, Transitions and cuts: https://superdirector.app/learn/transitions-cuts
- OpusClip, Visual effects in short-form: https://www.opus.pro/research/broll-visual-effects-short-form
- TikTok, Creative best practices: https://ads.tiktok.com/help/article/creative-best-practices
- TikTok/Lumen 2022, Creator ads: https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf
- Meta, Reels safe zones: https://www.facebook.com/business/ads-guide/update/image/instagram-reels
- poster.ly, Shorts safe zone: https://www.poster.ly/tools/youtube-shorts-safe-zone-checker
- BBC, Subtitle guidelines: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
- Internal: design_doctrine.md, research_visual_polish.md, research_cuts_pacing.md, research_captions_text.md, critique_feasibility.md, platforms.md
