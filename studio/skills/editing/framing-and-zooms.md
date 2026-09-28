# Framing, punch-ins and reframing

Load this file at the finishing stage, right after picture lock and before b-roll, whenever you are about to set a segment's framing transform: the base crop for each take, a punch-in (a cut to a tighter crop of the same shot), a slow push-in, an A/B crop rhythm, take matching, or a reframe of landscape or loose footage into 9:16 from the face track. Captions, titles and cutaways live in their own files. The default answer is often "no punch": a punch-in is a visible edit, and over-editing is the failure this file is written against.

## Principles

1. **A framing change is a cut, and it needs a reason.** Its jobs are: hide a seam inside a thought, mark the single most important phrase, change gear at a new beat, or give a long static stretch some life. *Why:* on a calibration take a pro made 1 cut where the old engine made 19 [I]. Murch ranks emotion first of his six criteria (51%) [P]: a punch that steps on a feeling is wrong however clean it is. The viewer should notice the speaker, not the technique.
2. **Change the size clearly or not at all.** *Why:* Murch says viewers struggle with displacements "neither subtle nor total", such as a full-figure shot cut to a slightly tighter one: enough to signal a change, not enough to re-read, like a beehive moved two yards [P]. A 5–10% step reads as a camera bump.
3. **Close-ups earn their power from the face's emotion, and more is not better.** *Why:* close-ups raised spontaneous mental-state attribution only when the face was sad; a neutral close-up did nothing extra (N=136) [L]. Across 495 viewers close-up frequency changed spontaneous attribution, and the authors *suggest*, without establishing, that it peaks and then falls [L]. Both used narrative film, not digital punches: take the direction, not the dosage.
4. **Keep the viewer's point of fixation still across the punch.** Scale around a point on the face midline between eye level and the nose tip, not the frame centre. *Why:* eye-trace is one of Murch's six criteria [P]. Viewers of talking faces have no fixed preference for the eyes: gaze goes to the eyes during eye contact, the mouth during speech, and the nose as an anchor when the head moves fast (Võ et al. 2012) [L]. A direct-to-camera speaker triggers all three, so anchor between them.
5. **Resolution sets the ceiling. Taste sets the size.** *Why:* every step beyond the source's pixels upsamples the face, and a soft face reads as cheap. Never AI-upscale the speaker to get more range [I].
6. **A talking-head crop should be still.** Lock the crop per segment. Move it only when the subject actually relocates. *Why:* AutoFlip picks a stationary viewport whenever the important content fits one position for most of the scene [A]; Adobe Auto Reframe offers a "Slower Motion" preset for low-motion footage [V]. A crop that follows every head bob "hunts".
7. **Match the framing to the creator's energy.** *Why:* in 12,842 Douyin marketing videos, engagement was higher when visual variation was congruent with vocal arousal [L, observational]. Visual variation alone also correlated positively, so the lever is *match*, not *minimise*. Fast pace plus arousing content overloads viewers (Lang 1999) [L].
8. **Frame for the phone, inside the platform chrome.** *Why:* Meta asks for roughly the top 14%, bottom 35% and 6% of each side to be kept clear of key creative elements [A]. A face under the caption band or like rail is a broken frame.
9. **Use few sizes and reuse them.** *Why:* the measured Hormozi Shorts toggle between roughly two crop levels [V, n=3]. A base A, one tighter B and at most one C read as camera coverage; a new size on every punch reads as a zoom effect.

## Defaults and ranges

These are starting priors. State a one-line reason whenever you leave a range. "Scale" is relative to the adjacent shot of the same take.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Punch that reads as intentional (emphasis, no time jump) | 1.15–1.25x reads as a lean-in within the shot; about 1.4–1.6x as a new, closer shot; below about 1.1x as an accident | [V] + [P] | [AutoClip](https://autoclip.dev/blog/punch-in-zoom-crop-zoom-speaker-zoom-guide) (15–25%; "Smaller reads as an accident"); measured creators' 1.5x (below); [Murch](https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf) |
| Punch that hides a seam inside a thought (pose also jumps) | at least 1.25x, 1.3–1.5x preferred | [X] from [P] | Murch; the "20 mm/30°" rule of thumb ([Wikipedia](https://en.wikipedia.org/wiki/30-degree_rule)) works out to about 1.24–1.57x on 85–35 mm lenses; "15–20%" is lore with no primary source (research_cuts_pacing.md) |
| Signature A/B crop | A = base, B = about 1.5x, toggled at semantic seams, 3.5–5.6 s between cuts | [V, n=3] | [PandaStudio, Hormozi](https://www.writepanda.ai/blog/hormozi-style-shorts-editing) |
| Restrained reference | one 1.5x punch in four measured Shorts (three had none), at 75% of runtime, marking the turn to the payoff as overlays ended | [V, n=4] | [PandaStudio, Abdaal](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/) |
| Emphasis punches per 60 s | 0–2 typical; about 4 is a ceiling, never a quota. By style: hot take 0–2 (usually 1); explainer 0–2 ("rare" in the doctrine's style table; Abdaal used one in four Shorts); sales 2–4; founder 0–2; listicle one per item at one scale; storytime a push instead; podcast clip active-speaker crops. AutoClip calls 3–4 per 40 s "a lot" | [I] from [V] | design_doctrine.md §17, research_style_trends.md; AutoClip; the style files |
| Timing | picture change on the onset of the stressed word or phrase, 0–70 ms early; 200 ms late "reads as a mistake" | [X] / [V] | AutoClip (the 200 ms figure only) |
| Timing source | forced-aligned onsets (MFA about 20 ms error), never raw ASR times: WhisperX drifts 66–110 ms, about 200 ms on disfluent speech, enough to make a punch late | [L] | research_perception_models.md |
| Punch motion | an instant scale change (a cut) by default. For high-energy styles an animated snap under about 150 ms reads "as a beat rather than a move". No elastic easing | [V] | AutoClip |
| Hold | to the end of the clause, at least about 1.5 s; return to base at the next seam or sentence boundary, never mid-sentence | [X] | — |
| Lossless ceiling, 4K vertical (2160×3840) to 1080×1920 | 2.0x (to 1440×2560: 1.5x) | [X] arithmetic | Meta recommends 1440×2560 for Reels ads ([Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels)) [A] |
| Upsampling tolerated on a face (1080p punches, or excess past the lossless ceiling) | about 1.1x by default; 1.2x on clean, sharp, well-lit footage after a 1:1 eye check; 1.25x is the hard limit | [P] + [X] | [Creative COW](https://creativecow.net/forums/thread/how-much-can-you-zoom-in-on-4k-footage-in-a-1080p/): one editor calls 110% "safe", 120% on "super clean" footage; others say quality drops past 100%. Phones hide some softness ([VMAF phone model](https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v0.md) [A]; [AT&T](https://developer.att.com/video-optimizer/docs/best-practices/resolutionandperception) [V]); platform re-encoding adds its own |
| Landscape to 9:16 crop | 4K landscape gives a 1215×2160 crop: downscaled, but only 1.125x lossless headroom left for punches. 1080p landscape gives 608×1080, which needs 1.78x upsampling: use a fit layout | [X] arithmetic | — |
| Eye line | about one third down the frame (y ≈ 600–700 of 1920). In tight framing, lose the crown before the chin | [P] | [Headroom](https://en.wikipedia.org/wiki/Headroom_(photographic_framing)) ("the closer the subject, the less headroom") |
| Interface bands to keep the face clear of | Reels: top 14%, bottom 35%, sides 6%. Shorts (Google's vertical-ads overlay): top 288, bottom 672, left 48, right 192 px. TikTok (third-party maps; varies with caption length): top ~140–200, bottom ~250–480, right ~120–180 px. House band and sources: `platforms.md` | [A] / [V] | [Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels); `platforms.md`; [Jon Loomer](https://www.jonloomer.com/tiktok-instagram-reels-safe-zones-templates/) templates |
| Face size (crown to chin) | base 25–35% of frame height; tight 38–50%. Measured Hormozi framing: chest-up, face about 40% of frame width | [X] / [V] | [PandaStudio](https://www.writepanda.ai/blog/hormozi-style-shorts-editing) |
| Slow push-in | +3–8% over 4–12 s (about 0.5–1.5% per second), eased in and out | [X] from [V] | [Morphic](https://morphic.com/ai-glossary/slow-zoom): 10–15% over 20–30 s is "almost subliminal", over 5–8 s "more noticeable". [Envato Tuts+](https://photography.tutsplus.com/tutorials/documentary-in-motion-interview-movement--cms-28295): no numbers; PBS Frontline pushes in on every interview [P] |
| Take-to-take framing match | face size within about 5% and eye line within about 2% of frame height, or a deliberate change of at least ~1.25x (the seam-hiding threshold above; critique_feasibility.md's "15–20% or more" is the lore figure) | [X] + [I] | critique_feasibility.md |
| Reframe dead zone | re-centre only when the face leaves about ±10% of crop width for more than about 0.7 s; moves eased over at least 0.4 s | [X] | Path shape from [Grundmann et al. 2011](https://research.google/pubs/auto-directed-video-stabilization-with-robust-l1-optimal-camera-paths/) [L]; smoothing via the [1€ filter](https://gery.casiez.net/publications/CHI2012-casiez.pdf) [L] |

No study links punch-in rate or size to retention on Shorts. Treat every number above as a prior to test [X].

## How to decide

1. **Read the source facts for each take.** Get orientation, resolution, fps and noise level, and from the face track the face-box height, centre, eye line and head motion. Compute the lossless ceiling (source height ÷ output height ÷ any base scale) and the **face-safe maximum** = lossless ceiling × 1.1 by default, × 1.2 on clean, sharp footage after a 1:1 eye check, never above × 1.25 (the upsampling row above). Low light and soft focus lower it. Never assume 4K.
2. **Set base framing A for each take:** a static segment transform (scale, offset) anchored on the face: eyes at about one third, chin and mouth above the caption band. Centre a direct-to-camera face; if the speaker addresses someone off-camera, leave look room on that side ([lead room](https://en.wikipedia.org/wiki/Lead_room)) [P]. If the creator framed well, A is 1.0x. For joined takes, match face size within about 5%, usually by scaling up the looser take (you cannot go below the full frame without padding).
3. **Pick the framing mode from style and measured energy.**
   - *None:* calm or sincere delivery, anything 20 s or shorter, raw-style creators, tutorials where hands or the screen matter.
   - *Emphasis punches:* the usual choice for hot takes and sales; used sparingly (0–2) in education.
   - *Slow push:* storytime and emotional climaxes.
   - *A/B rhythm:* high-energy delivery on a 4K source, or when the creator's memory asks for it.

   Write the mode and the budget into the Edit Brief.
4. **List the candidates from the locked cut.**
   - (a) Seams inside a continuing thought, where the pose jumps and the cut needs hiding.
   - (b) The one or two phrases that carry the claim, punchline or turn: highest prosody z-score *and* highest importance.
   - (c) Beat changes, especially the turn to the payoff.
   - (d) Static stretches over about 8 s.
5. **Treat seams inside a thought first.** If the take's face-safe maximum allows 1.25x or more, cut to B at the seam's first word ID and hold to the end of that clause. A punch hides a small pose shift, not a head turn, a big lean or a jumping hand: measure the landmark delta across the seam and use a cutaway when it is large, or when the headroom is missing (typically 1080p). Other options: a J/L cut, a cut on a motion onset, or an honest jump cut. Seams *between* beats usually need nothing: viewers already segment events where the action changes, so a cut there costs little (Magliano & Zacks) [X from L].
6. **Spend the emphasis budget.** Rank the candidates in (b) and take the top one or two; more only if energy and profile support it. Land each punch on the refined onset of the stressed word or phrase and hold it to the clause end. If a seam falls on the same beat, punch there so one edit does two jobs. Keep about 5 s between punches unless running an A/B rhythm. Avoid punching in the first 1–2 s, while the face and hook title are still being read, unless the hook's second clause is the claim.
7. **Add slow pushes only for long emotional or climactic passages** that you do not want to cut. Use one per segment, eased, never combined with a re-centring move or a punch, and never cut from a push-in straight into a pull-out [P]. A digital push has no parallax, so it reads as a zoom, and viewers rated an approaching camera more involving than a zoom (Heimann et al. 2014, hand-action clips) [L].
8. **Recheck collisions after the transforms.** Use per-frame landmarks from the compiled transform, not source boxes. Fix collisions with the hook title, captions and interface bands by changing the offset first, then the scale, and only then by dropping the punch.
9. **Reframe landscape or loose footage.** Compute the crop's upsample factor. At 1.2x or less, use a full-bleed stationary crop from the face track. Above that, use a fit layout: designed or blurred padding (AutoFlip's fallback [A]), a stacked frame, or a split for two speakers ([OpusClip](https://help.opus.pro/docs/article/layout-and-reframing) [V]). Pan only when the subject relocates; if meaningful gestures or a second person leave the crop, widen or switch layout.
10. **Render and watch at phone size,** plus a 1:1 crop of the eyes at the tightest framing. Answer the critic questions and cut any punch you cannot justify in one line.

## When to break it

- **Comedy:** a snap "crash" punch of 1.5–2x on the button (4K only) is a joke device and may be loud. Never punch on the setup.
- **Signature A/B rhythm:** alternate at every semantic seam for a high-energy creator who wants that look (4K only).
- **Listicles:** a punch can mark each item. Use the same scale every time so it reads as a system.
- **Podcast clips:** active-speaker crops replace punch-ins.
- **Bad base framing:** reframing a whole take (tilt, too much headroom) is repair, not a punch, and costs no budget.
- **Long single takes with no seams:** a very slow push can stand in for a cut; documentary-style creators may push on every segment, as Frontline does.
- **Returning from a cutaway:** coming back at a different size is free variation. Don't add a punch right after it.
- **Creator memory overrides these defaults.** For example: "dislikes zooms on sincere lines."
- **Zero punches is a complete answer** when the delivery carries itself.

## Worked example

A 38 s hot take about money, delivered at moderate energy (vocal arousal 0.45, about 135 wpm). It was shot on an iPhone front camera, 2160×3840, HLG, 60 fps. The creator recorded three hook takes and one body take. Output is 1080×1920, so the lossless ceiling is 2.0x.

Locked cut:
- **Hook (take 3, w0–w7):** "Most people fail at budgeting for one reason."
- **Beat 2 (take 1, w8–w29):** "It's not that they spend too much. [false start "It's that they, uh" w15–w18 removed] It's that they budget for the month they wish they had."
- **Beat 3 (w30–w51):** "So here's what I do instead. I build my budget off my worst month from last year. Not the average. The worst."
- **Beat 4 (w52–w71):** "Because that's the month that actually breaks you. And every good month after that? The extra goes straight to savings."
- **CTA (w72–w83):** "Try it for ninety days. Follow and I'll send you the sheet."

Decisions:

1. **Take matching.** In take 3 the creator leaned in: face height 29% of the frame against 27% for take 1, a 7% difference in the "two yards" zone. Set base A as take 1 at 1.07x and take 3 at 1.00x, eyes at y≈635 in both. The hook-to-body seam (w7|w8) is now an honest jump cut at a beat boundary and needs no punch.
2. **Seam inside a thought (w14|w19).** Removing the false start leaves a small pose jump in the middle of a "not X, it's Y" pair. Cut to B at 1.35x on w19 "It's", scaled around the bridge of the nose, and hold to w29 "had". Return to A on w30 "So", where the new beat starts. *Why 1.35x:* at least 1.25x is needed to read as a camera change. 1.5x felt aggressive for this speaker.
3. **Emphasis (w50–w59).** "The worst" carries the video's highest intensity peak (+2.1 z). There is no seam there, and the 420 ms pause after "average" is kept verbatim. Cut to the same B (1.35x) about 30 ms before the MFA onset of "The" (w50), and hold through "breaks you" (w59), the end of the idea. Return to A on w60 "And", the next sentence boundary. *Why not on "worst" alone:* that would split a 300 ms phrase.
4. **Rejected:** a punch on "one reason" (competes with the hook title); a slow push across the savings line (not emotional); A/B on every seam (energy mismatch); any 1.1x step (reads as a bump, and 4K allows more); a third size for the CTA (breaks the ladder).
5. **CTA at A.** Direct address in the same framing the viewer met at the start.
6. **Checks.** At B the crown sits at y≈260 (only hair under the top band) and the chin at y≈1010, clear of the caption band at y 1080–1240. The tightest source crop is take 1 at 1.07 × 1.35 ≈ 1.44x, i.e. 1495×2658, still downscaled, so a 1:1 eye crop shows no softness. Totals: 2 punches in 38 s at one size, each with a named job.

Shot in 1080p, the same video has no punch left: take 1's 1.07x match already upsamples, so B tops out near 1.12x over A (1.2x total, and only if the footage is clean), a bump below the seam threshold. The w14|w19 seam gets a cutaway with an L-cut or stays an honest jump cut, and "the worst" gets caption emphasis. Zero punches.

## Anti-patterns

- **Zooms on a timer.** One Descript user reported zoom cuts "every 20 seconds" [V, anecdote] ([Reddit](https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/)).
- Punching every sentence, or pulsing the zoom on every caption word.
- Micro-steps of 1.03–1.12x that look like the camera slipped; a new scale on every punch.
- Punching a 1080p face past 1.2x, or "fixing" the softness with AI upscaling or heavy sharpening.
- Punching mid-word or a beat late (often from raw ASR timestamps); snapping back to wide mid-sentence.
- Scaling from the frame centre, or checking collisions on pre-transform face boxes, so the eyes jump or the face slides under the captions.
- Elastic or bouncy animated zooms with a whoosh on every punch.
- Face-tracking that jitters, hunts, or re-centres on every nod.
- Joining takes whose face sizes differ by more than ~5% but less than a clear ~1.25×.
- Stacking a punch, an SFX, a caption pop and a b-roll entry on one word.
- A hard snap punch on a vulnerable line that wanted stillness or a slow push.
- Cropping out the gesturing hand when the gesture is the point.

## Critic questions

1. Does every punch-in land on a seam or on the onset of a stressed phrase that matters, and never late or mid-word?
2. Is every size change within a take either clearly visible (about 1.15x or more) or absent?
3. Do the eyes stay at about the same screen position across every punch, and are the mouth and chin always in frame?
4. In every framing, is the face clear of the interface bands, right rail, captions and hook title?
5. At the tightest framing, is the face as sharp as the base shot on a phone, with no smearing or halo?
6. When the speaker is still, is the crop still, with no drift, jitter or hunting?
7. Can each punch be justified in one line, is the count within the brief's budget, and does the video use no more than two or three sizes?
8. Where takes are joined, is the framing either matched or changed deliberately?
9. Does every return to base framing happen at a seam or sentence boundary rather than mid-sentence?
10. Is each slow push slow enough to be felt rather than noticed, and does it sit on an emotional or climactic passage?
11. In reframed landscape footage, do the face and meaningful gestures stay inside the crop?
12. Would the video lose anything important if the weakest punch were removed? If not, remove it.

## Sources

- Murch, *In the Blink of an Eye*, pp. 5–26: https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf
- 30-degree rule / 20 mm rule; Headroom; Lead room (Wikipedia): https://en.wikipedia.org/wiki/30-degree_rule, https://en.wikipedia.org/wiki/Headroom_(photographic_framing), https://en.wikipedia.org/wiki/Lead_room
- Võ, Smith, Mital & Henderson 2012, "Do the eyes really have it?", Journal of Vision: https://jov.arvojournals.org/article.aspx?articleid=2121284
- Rooney & Bálint 2018, "Watching more closely", PLoS ONE: https://pmc.ncbi.nlm.nih.gov/articles/PMC5776141/
- Bálint, Blessing & Rooney 2020, "Shot scale matters", Poetics, doi:10.1016/j.poetic.2020.101480: https://research.vu.nl/en/publications/shot-scale-matters-the-effect-of-close-up-frequency-on-mental-sta/
- Heimann et al. 2014, "Moving mirrors", J. Cogn. Neurosci., doi:10.1162/jocn_a_00602: https://direct.mit.edu/jocn/article-abstract/26/9/2087/28258/Moving-Mirrors-A-High-density-EEG-Study
- Magliano & Zacks 2011, Cognitive Science: https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf
- Lang 1999: https://www.tandfonline.com/doi/abs/10.1080/08838159909364504
- Yang et al. 2025, JTAER: https://doi.org/10.3390/jtaer20020069
- Grundmann, Kwatra & Essa 2011: https://research.google/pubs/auto-directed-video-stabilization-with-robust-l1-optimal-camera-paths/
- Casiez, Roussel & Vogel 2012, "1€ filter": https://gery.casiez.net/publications/CHI2012-casiez.pdf
- Reframers: AutoFlip https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/; Adobe Auto Reframe (403 when last checked) https://helpx.adobe.com/premiere/desktop/add-video-effects/commonly-used-effects/add-auto-reframe-effect-to-a-sequence.html; OpusClip https://help.opus.pro/docs/article/layout-and-reframing
- Safe zones: Meta Reels ads guide (also 1440×2560) https://www.facebook.com/business/ads-guide/update/image/instagram-reels; Jon Loomer templates https://www.jonloomer.com/tiktok-instagram-reels-safe-zones-templates/
- Phone viewing: Netflix VMAF phone model https://github.com/Netflix/vmaf/blob/master/resource/doc/models_v0.md; AT&T https://developer.att.com/video-optimizer/docs/best-practices/resolutionandperception
- Creative COW, zooming 4K in a 1080p timeline: https://creativecow.net/forums/thread/how-much-can-you-zoom-in-on-4k-footage-in-a-1080p/
- AutoClip, punch-in zoom guide (vendor): https://autoclip.dev/blog/punch-in-zoom-crop-zoom-speaker-zoom-guide
- PandaStudio (vendor): https://www.writepanda.ai/blog/hormozi-style-shorts-editing, https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/
- Slow push: Envato Tuts+ https://photography.tutsplus.com/tutorials/documentary-in-motion-interview-movement--cms-28295; Morphic (vendor) https://morphic.com/ai-glossary/slow-zoom
- Internal: research_cuts_pacing.md, research_visual_polish.md, research_perception_models.md, research_style_trends.md, design_doctrine.md, critique_feasibility.md
