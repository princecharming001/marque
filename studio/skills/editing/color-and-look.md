# Color and look

Load this file at the color step of finishing, after sound and before QC. Also load it when you review a take's ingest report (tone map, tags, mixed formats), when you conform b-roll, and whenever a critic flags a grey or washed-out picture, orange or grey skin, a color jump at a seam, flicker, or banding. Its job is to make iPhone footage look like a well-shot version of itself: one correct HDR-to-SDR conversion, faces exposed and colored like skin, takes and inserts that match, and at most a light look. "No correction" and "no look" are valid outcomes, and on a well-lit take they are the usual ones.

## Principles

1. **Do no harm first, then consistency, then a restrained look.** *Why:* across 90k Snap videos, off-the-shelf quality scores tracked average watch time only weakly (r ≈ 0.07–0.31 by duration bucket), and adding aesthetic features to the authors' trained engagement model moved it only from SRCC 0.689 to 0.696 [A]. A bad grade does visible damage; a good look adds little.
2. **Tone-map exactly once, anchored to 203-nit reference white.** *Why:* each extra conversion compounds. Raw iPhone HDR on Reels looked overexposed until Meta tone-mapped it [A]. A wrong reference-white constant once crushed Jellyfin's output about 64x [P]. Remotion's `<OffthreadVideo>` tone-maps HDR input by default [V].
3. **The face is the reference, and it is measured, not eyeballed.** *Why:* viewers judge the whole picture by the skin. Across 32 vision-language models, ColorBench found color understanding weak and sometimes misleading [L]. Claude's eye has to be paired with numbers.
4. **Correct toward how the room looked, not toward a style.** *Why:* TikTok's ad creative guidance recommends a "DIY or not overly polished style" that fits in with user content [A]. Instagram's head says imperfection is becoming a signal of authenticity [A].
5. **Match takes before you apply a look.** *Why:* a look amplifies whatever differences it sits on, and a color jump at a seam reads as a mistake, not a choice [V]. Viewers forgive a background shift at a seam more than a skin shift, so match skin first [X].
6. **Keep settings static within a take.** *Why:* per-frame auto-correction and dynamic peak detection make exposure "breathe". libplacebo smooths its own peak detection over 20 frames because frame-to-frame changes flicker [P].
7. **Protect both ends of the range.** *Why:* the delivery is 8-bit and gets re-encoded by the platform. Crushed shadows and clipped highlights cannot be recovered, and smooth gradients band, most visibly on bright screens in dim rooms [L]. BT.2408 warns that clipping makes later compression harder [P].
8. **The face belongs to the creator.** *Why:* per-frame generative face restoration causes identity flicker [L]. TikTok restricts its own beautifying effects for minors, which shows the norm [A]. Retouching someone's face without asking breaks trust.
9. **A look is seasoning. It is per creator and consistent across their videos.** *Why:* one LUT on every creator makes every output look like one template and erases the creator's own visual identity [X]. Heavy looks push skin off the skin-tone line [V].

## Defaults and ranges

Starting priors, not rules. Leave a range only with a one-line reason in the Edit Brief. SDR percentages are of the limited range (8-bit code 16 = 0%, 235 = 100%).

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| HDR reference white / graphics white | 203 cd/m², which is 75% HLG and 58% PQ | [P] standards body | [ITU-R BT.2408-8, Table 1](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf) |
| Where HDR reference white lands in the SDR output | about 90% SDR, highlights compressed above it. China Media Group maps 75% HLG to 90% SDR; live down-mappers reach about 95% only by parking highlights in super-whites above 100%, which web players may clip, so 95% is a ceiling | [P] / [X] | BT.2408 §7.7 and Annex 8 |
| Mid-tone anchor for checking the tone map | 40% HLG maps to about 50% SDR after display-light down-mapping (the broadcast grass reference) | [P] | BT.2408 §2.2 |
| Face level before the tone map (HLG) | light skin 55–65%, medium 45–60%, dark 25–45% HLG. Data for types 1, 5 and 6 is thin. They assume exposure to the 75% HLG reference, which iPhone auto-exposure does not target: a sanity band, not a goal | [P] / [X] | BT.2408 Table 2 |
| Face-mask mean luma after the tone map (SDR) | lighter skin 45–70%, medium 30–60%, deep 15–35%. The source table is keyed by ethnicity and gender; we key it by skin lightness | [V] | Van Hurkman via [Larry Jordan](https://larryjordan.com/articles/the-secret-to-setting-skin-colors-accurately/): "guidelines, not absolutes" |
| Brightest non-specular skin (lit cheek or forehead) | Japanese studio news SDR averages 74.6% (SD about 6; 713 faces). European and American broadcast skin runs far darker. Soft ceiling about 80% | [P] / [X] | BT.2408 Annex 4 and Annex 1 |
| Skin hue | on the skin-tone line (the I axis, about 123° counter-clockwise from +Cb on a Cb/Cr plot), within about ±2°. Investigate anything past ±5° | [V] / [X] | Van Hurkman via Larry Jordan; [YIQ](https://en.wikipedia.org/wiki/YIQ) |
| Skin saturation (vectorscope) | about 30–40% for lighter skin and 15–20% for darker; scope scales differ, so also compare with the camera's own: more than about 10% above it is a warning | [V] / [X] | Van Hurkman via Larry Jordan |
| Global saturation change | 0.9–1.1x; leaving it needs a one-line reason, and beyond ±15% should be rare (iPhone footage arrives already processed) | [X] | — |
| Exposure change per take | usually within ±0.5 stop. Beyond about 1 stop, noise and compression show, so fix partially and accept the rest | [X] | — |
| Shadows | dark hair and clothing keep detail above about 5%. At most about 2% of pixels at or near code 16 (count from a luma histogram; signalstats YLOW is only the 10th percentile) | [X] | [FFmpeg signalstats](https://ffmpeg.org/ffmpeg-filters.html#signalstats); BT.2408 §2.4 |
| Highlights | face never clipped. Nothing important above 100% (platforms may clip super-whites). Windows and specular highlights may clip | [P] | BT.2408 §7.6.4 |
| Take-to-take match | skin and neutral patches within ΔE2000 ≤ 2; face luma within 3 points | [L] / [X] | ΔE formulas were designed so 1.0 ≈ one just-noticeable difference; ΔE*ab's JND was later revised to ≈ 2.3 ([Sharma, via Wikipedia](https://en.wikipedia.org/wiki/Color_difference)) |
| Look LUT strength | 0% (none) to 40%, typically 20–30%. After the look, skin hue stays within ±5° | [I] / [X] | design_doctrine.md §14 ("modest strength") |
| B-roll grade match | exposure, white balance and contrast only; MKL transfer at ≤0.5 strength. Reject an insert whose neutrals stay above about ΔE 5 from the A-roll | [I] / [X] | critique_feasibility.md; [color-matcher](https://github.com/hahnec/color-matcher) |
| White UI in screenshots and cards | true by default. If a full-screen white UI glares after the A-roll, lower its white to about 90–95% (the A-roll's diffuse white) or frame it on a card; never tint it. White captions stay at 100% | [X] from [P] | BT.2408 §7.7 |
| Banding | CAMBI ≤ 3 per frame on the master (no-reference; 0 = no banding). About 5 is where banding becomes slightly annoying; 24 is unwatchable. It is worst on bright screens in dim rooms | [L] | [Netflix libvmaf CAMBI doc](https://github.com/Netflix/vmaf/blob/master/resource/doc/cambi.md) |
| Bit depth | grade on the 10-bit mezzanine. Convert to 8-bit once, with dithering (libplacebo defaults to blue noise and says dithering "is always required" when lowering bit depth) | [P] | [libplacebo options](https://libplacebo.org/options/) |
| Tone-map operator | pinned per engine version, chosen by blind A/B against Apple's AVFoundation H.264 export (Meta calls objective tone-map metrics "an open research problem"). The zscale+tonemap path offers hable (Meta's production base, tuned), mobius and reinhard. The installed FFmpeg lacks libplacebo; if added, its spline (default) and bt.2390 join the A/B, and it labels hable, mobius and gamma legacy. zscale ignores the Dolby Vision RPU and maps the HLG base layer | [I] / [A] / [P] | [Meta 2023](https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/); [WWDC20](https://developer.apple.com/videos/play/wwdc2020/10010/); [libplacebo](https://libplacebo.org/options/) |
| Tone-map settings | operator set explicitly (FFmpeg `tonemap` defaults to `none`, desaturate only; the libplacebo filter to `auto`). zscale linearizes at npl=203. No per-frame peak detection (libplacebo's is on by default). Output tags explicitly bt709/tv: libplacebo's `auto` copies the input tags, or outputs BT.2020+PQ when it applies Dolby Vision | [P] / [I] | [FFmpeg tonemap](https://ffmpeg.org/ffmpeg-filters.html#tonemap-1); [FFmpeg libplacebo](https://ffmpeg.org/ffmpeg-filters.html#libplacebo); ARCHITECTURE.md §3 |
| Delivery transfer | SDR BT.709 master by default. YouTube accepts HLG/PQ and down-converts for SDR screens, optionally guided by a LUT. Instagram iOS has preserved Dolby Vision since Nov 2025. We found no TikTok HDR guidance | [A] / [I] | [YouTube HDR](https://support.google.com/youtube/answer/7126552); [Meta 2025](https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/) |
| Grain, skin smoothing, face restoration | none by default. Grain changes every frame, so encoders spend bits on it and then smear or clump it | [I] / [V] | [Nilo](https://josephnilo.com/blog/why-youtube-compression-ruins-video-quality/); [DVFace](https://arxiv.org/html/2604.14560) |

## How to decide

1. **Check the ingest before you grade.** For each take, read the source transfer (HLG with Dolby Vision 8.4, SDR, or Apple Log), the pinned operator, and the asserted mezzanine tags (bt709/bt709/bt709, TV range; FFmpeg `colordetect` confirms the pixels really are limited range). Compare a proxy frame with Apple's own SDR export of the same frame. If it looks grey, washed out, crushed or very different, that is an ingest bug to send back, not a grading job. Every take is tone-mapped on its own before any matching. Apple Log takes go through Apple's official LUT first.
2. **Measure only what the cut uses.** Code reports, per take and per kept segment (the word-ID ranges that survive picture lock):
   - face-mask mean luma and lit-side luma;
   - skin hue angle against the skin line, and face-mask saturation;
   - a*/b* of any neutral surface (white wall, grey shirt);
   - signalstats YLOW/YHIGH and BRNG (out-of-range) share;
   - clipped face pixels;
   - CAMBI per frame (it hunts contours in smooth areas);
   - face luma over time, to catch lighting drift.

   Read the numbers first, then the before/after stills, then 1:1 crops for noise and banding.
3. **Decide whether each take needs anything.** If the face is in range, hue is within ±2–5°, neutrals read neutral and nothing important clips, write "no correction" with the reason and move on.
4. **Apply a primary correction, static per take, in this order:** exposure (land the face in range), white balance (skin onto the line, whites reading white, practical lamps left warm), black point and contrast (a gentle S only if the image is flat, never crushed), then saturation (usually within ±10%). Keyframe only when the light really changes within a take, such as a cloud passing or an auto-exposure jump. Prefer cutting around that moment if the story cut allows it. A face lost against a bright window has no global fix (there are no power windows): lift exposure until the face is in range, let the window clip, and consider a punch-in that crops it out.

   *Engine note:* if `set_color` offers only one document-wide ColorSpec, grade for the hero take and resolve other takes through take choice or seam cover (step 5), and log the need for per-take specs. `lut_strength` defaults to 1.0, so always set it explicitly when you set a look.
5. **Match the takes.** The hero take is the one carrying the hook or most kept words, usually the best lit. Match the others to it using face-mask and neutral regions only, never full-frame statistics. Check each take-change seam with a still pair: last frame before, first frame after. If the residual is still visible (daylight against lamp light, say), you have three options:
   - choose a different take;
   - cover the seam with a punch-in of at least 1.25x, if the take's face-safe maximum allows it, so it reads as a new shot and crops away much of the mismatched background (see framing-and-zooms.md). A punch hides a background shift, not a skin shift;
   - cover it with a cutaway.

   Never use a crossfade, which shows both colors at once.
6. **Choose the look, if any,** from the creator profile and content style:
   - *none*, for raw or documentary creators and product-accuracy content;
   - *clean*, a slight S-curve with a touch of warmth, at 20–30%;
   - the creator's saved signature look.

   Apply one look to the A-roll and to conformed footage b-roll. Never apply it to screenshots, designed cards, logos or product color, which must stay true. Captions and overlays are composited after the grade and are never graded.
7. **Conform the b-roll:** tone-map once; match exposure, white balance and contrast to A-roll reference frames (MKL ≤0.5); then apply the same look. Reject the insert if it still looks like a different camera world.
8. **Render and check:** skin numbers per segment, seam pairs, a luma-variance scrub for flicker, CAMBI on the master and on a re-encode at a platform-like bitrate (platform transcodes deepen banding and block up dark areas [V]), full-resolution still pairs, and a phone-brightness, dim-room view. Record every per-take setting, the look and its strength, and each reason in the Edit Brief.

## When to break it

- **Signature looks.** A creator with an established warm film look or black and white keeps it. For black and white, check that faces still separate from the background in luma.
- **Intentional mood.** A late-night storytime can sit below the face range. Keep the eyes readable and do not "fix" the mood.
- **Colored light that is part of the set.** Neon, RGB panels or a sunset are content. Correct skin only if it goes sickly, and keep the color in the background.
- **Product accuracy.** For makeup, clothing, food or paint demos, accuracy beats the look: no look, and white balance set from a neutral reference. A pleasing shift here misrepresents the product.
- **HDR delivery.** Break the SDR default only on an approved HDR experiment where the posting path preserves Dolby Vision (Instagram iOS today). Graphics then sit at 203 nits, and the SDR fallback still comes from a single tone map.
- **Opt-in retouching.** If the creator asks, allow subtle blemish or shine reduction behind an eval gate. It never reshapes the face or runs as per-frame generative restoration.
- **Very noisy low light.** Temporal denoise is a repair, not a beauty filter; check 1:1 crops for wax.

## Worked example

A 44 s money-tips video, iPhone 15 Pro, HLG with Dolby Vision 8.4, 60 fps, 4K. The creator has medium-brown skin and energetic delivery. Take A was filmed by a window at 4 pm; Take B, a retake of the ending, about an hour later with a tungsten lamp on. The locked cut uses A for w1–w111 and B for w112–w131.

- Hook, Take A, w1–w6: *"Most people budget backwards."*
- Ending, Take B, w112–w131: *"Try it for one month and tell me what changed."*

**Ingest.** Both takes were tone-mapped separately with the pinned operator and are tagged bt709. The proxy is within ΔE 1.5 of Apple's H.264 export on skin. No action.

**Measure.**

| | Take A | Take B |
|---|---|---|
| Face level before tone map | 56% HLG, inside 45–60 | 47% HLG |
| Face-mask luma after tone map | 52% SDR | 43% SDR |
| Skin hue | +1° from the line | +7°, toward orange |
| Wall | b* +2 | b* +12, a tungsten cast |
| Skin saturation | baseline | 16% above A |
| Other | window behind the right shoulder clips | — |

**Take A:** no correction. The clipped window is not the subject, and pulling it down would grey the whole frame.

**Take B:**
- Exposure +0.5 EV, which brings the face to about 50% (+0.4 would land near 48%).
- White balance cooler until the wall reads b* +4. The lamp stays visibly warm, but skin returns to +2°.
- Saturation −12%.
- Skin ΔE2000 against A is now 1.7, and face luma is 2 points apart.

**Seam at w112.** The side-by-side still shows a warmer background in B, even with matched skin. No crossfade. The ending is the call to action and the face should carry it, so w112 gets a 1.3x punch-in, which reads as a new shot and crops most of the warm wall. Logged as a joint decision with the framing pass.

**Look.** The profile says "clean, bright". The house clean look goes on at 25%. After the look, skin is at +2° and face luma is 51–53% across the cut.

**Inserts.**
- A stock clip of a calendar (BT.709, cool daylight) is matched on exposure and white balance with MKL 0.4, then given the same look.
- A banking-app screenshot gets no look and stays true; its white does not glare next to the window-lit body.
- One rejected option: "cinematic" teal shadows. They would turn the creator's navy shirt cyan and the beige wall green, for no gain in meaning.

**Check.** CAMBI peaks at 2.1 (on the wall) and 2.6 after a platform-like re-encode, there is no luma flicker within any segment, and the phone-brightness review passes.

## Anti-patterns

- **Grey or blown HDR.** HDR passed through untouched, or tone-mapped twice (for example by an ingest step and then Remotion's automatic conversion).
- **Tone map on defaults.** The operator left on `none` or `auto`, output tags left to copy the input, or a mistyped reference white.
- **Breathing exposure.** Per-frame auto-exposure or auto white balance, or peak detection left on.
- **Retouching by default.** Skin smoothing, eye or face reshaping, or generative face restoration.
- **Crushed blacks** sold as "cinematic", or milky lifted blacks that look grey on a phone.
- **Orange-teal by reflex.** Orange skin from a saturation or vibrance push. Todd Miro's critique: it makes every film look alike.
- **One LUT on everything.** A-roll, stock, screenshots and brand cards all tinted alike, and every creator with the same look.
- **Full-frame color transfer** from unlike content, such as beach stock onto a beige room, which leaves casts.
- **Grading from downscaled contact sheets**, or trusting the model's eye without measured skin numbers.
- **Judging stills extracted with the wrong matrix or range** (BT.601, or limited read as full). Skin and blacks shift, and the Director "fixes" a problem that is not in the video. Extract with an explicit bt709, TV-range conversion [X].
- **Heavy vignettes, gradients and added grain:** banding sources, and encoders smear grain.
- **Different settings on segments of one take**, so a jump cut also flickers.
- **"Fixing" deliberate color**, such as neutralizing a creator's neon set or the warmth of a lamp.

## Critic questions

1. Is the speaker's face naturally exposed in every segment, neither grey and washed out nor blown out?
2. Does skin look like skin (not orange, pink, green or grey) in every take?
3. At every change of take, do adjacent segments look like the same room at the same time of day?
4. Do dark hair, dark clothing and shadows keep visible detail, with no crushed black blobs?
5. Is the face free of clipped highlights on the forehead, nose and cheeks?
6. Does brightness and color stay steady within each shot, with no breathing or flicker?
7. Are walls, skies and other smooth areas free of visible banding or blocky steps on a phone at full brightness?
8. Do all b-roll inserts look like they belong to the A-roll's camera world?
9. Are screenshots, product shots and brand colors shown true, untinted by the look?
10. Is the face un-retouched (no smoothing, reshaping or plastic skin), unless the creator opted in?
11. Would a viewer call the video "clean" rather than "filtered"?
12. Do white captions and graphics read as white, not grey, next to the picture?
13. Could any color setting be removed without the video looking worse? If so, remove it.

## Sources

- ITU-R Report BT.2408-8, HDR production practice: https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf
- Meta Engineering, HDR video on Reels (2023): https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/
- Meta Engineering, Instagram iOS Dolby Vision (2025): https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/
- Apple WWDC20, Export HDR media with AVFoundation: https://developer.apple.com/videos/play/wwdc2020/10010/
- libplacebo options: https://libplacebo.org/options/
- FFmpeg filters (tonemap, libplacebo, signalstats, colordetect): https://ffmpeg.org/ffmpeg-filters.html
- Jellyfin-ffmpeg #775, HLG reference-white bug: https://github.com/jellyfin/jellyfin-ffmpeg/issues/775
- Remotion, HDR handling: https://www.remotion.dev/docs/hdr
- YouTube Help, HDR uploads: https://support.google.com/youtube/answer/7126552
- Larry Jordan, skin colors (Van Hurkman values): https://larryjordan.com/articles/the-secret-to-setting-skin-colors-accurately/
- Netflix libvmaf, CAMBI documentation: https://github.com/Netflix/vmaf/blob/master/resource/doc/cambi.md
- Wikipedia, Color difference (citing Sharma 2003): https://en.wikipedia.org/wiki/Color_difference
- Wikipedia, YIQ (I axis): https://en.wikipedia.org/wiki/YIQ
- Li et al. 2024, SnapUGC engagement study: https://arxiv.org/html/2410.00289v1
- ColorBench (VLM color perception): https://arxiv.org/abs/2504.10514
- DVFace (identity flicker): https://arxiv.org/html/2604.14560
- color-matcher: https://github.com/hahnec/color-matcher
- TikTok Ads Help, Creative best practices: https://ads.tiktok.com/help/article/creative-best-practices
- Adam Mosseri, raw aesthetics: https://www.threads.com/@mosseri/post/DS76UiklIDf
- 9to5Mac, TikTok under-18 beauty filters: https://9to5mac.com/2024/11/27/tiktok-will-ban-beauty-filters-by-under-18s-over-mental-health-impact/
- Todd Miro, Teal and Orange: https://theabyssgazes.blogspot.com/2010/03/teal-and-orange-hollywood-please-stop.html
- Joseph Nilo, YouTube compression (practitioner): https://josephnilo.com/blog/why-youtube-compression-ruins-video-quality/
- Yunicorn internal: studio ARCHITECTURE.md §3 and §5; design_doctrine.md §14, critique_feasibility.md items 10 and 16, design_toolchain.md rows 1 and 12 (reports/Yunicorn Studio design inputs/)
