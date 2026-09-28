# Platform specs and delivery

Load this file at the brief, once destinations are known, because platform rules set length windows, music routes, text placement and AI-label needs before any word is cut. Load it again at delivery for encodes, covers, loudness, caption files and disclosure flags. Validators enforce most of it; your job is the judgment calls: variants, music route, cover frame, AI labels and text placement. The usual correct delivery is one master, a frame-0 cover, no added sound and no label, and each departure needs a named reason. Facts were checked on 27 Sep 2026. Re-verify any row past its re-check date in `evidence.md`.

## Principles

1. **One master, packaged per platform.** *Why:* the story is the same everywhere, and every extra cut is another review and another chance for a defect. Packaging changes the encode, cover, flags and sound route; it adds no edits. Cut a separate version only when a hard limit or a real length window calls for it.
2. **Upload the file that survives re-encoding best.** *Why:* every platform transcodes again, and Instagram keeps its costlier, higher-quality encodes for creators who draw more views (Mosseri, 2024) [A]. Faces and caption edges must survive a bad transcode.
3. **Keep text in the safe band. Pictures may run to the edges.** *Why:* tabs, the right rail, the handle and the description cover the edges. A caption under the like button is a defect; a b-roll sky there is not.
4. **The first frame is the cover.** *Why:* feeds autoplay, and TikTok and Instagram API covers default to the first frame. A fade from black or a blink on frame 0 ships a bad cover.
5. **Bake in only music cleared for every destination, and always ship a no-music master.** *Why:* platform music licences cover sounds added inside their own apps, not third-party renders. Trending sounds can only be attached natively.
6. **Set loudness once and measure it after encoding.** *Why:* YouTube turns loud files down and never turns quiet ones up. TikTok and Instagram publish no target.
7. **Tone-map HDR exactly once and deliver SDR by default.** *Why:* the compositor is SDR-only, a second tone map turns faces grey, and only YouTube documents how it treats third-party HDR files.
8. **Disclose realistic synthetic media, and prefer edits that don't need it.** *Why:* all three platforms require labels and auto-label from provenance signals. Labels cost likes, and TikTok is testing a control that lets viewers see less AI content.
9. **Never pad to meet a length rule.** *Why:* what padding loses in retention outweighs what a monetisation threshold pays [X].
10. **Nothing in the file should signal that it came from somewhere else.** *Why:* Instagram shows fewer low-resolution, watermarked, muted, bordered, mostly-text or already-posted Reels [A], and YouTube's inauthentic-content policy (Jul 2025) demonetises mass-produced, near-identical videos. So designed cards stay short inserts, never the bulk of the runtime.

## Defaults and ranges

Starting priors; give a one-line reason for leaving a range.

**Delivery encode**

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Frame | 1080×1920, 9:16, accepted everywhere. Meta's ad spec recommends 1440×2560; TikTok ads ask for ≥540×960 | [X] + [A] | [Meta ads guide](https://www.facebook.com/business/ads-guide/update/video/instagram-reels), [TikTok](https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads) |
| Resolution ceilings | Shorts upload max 1080p. IG max 1920 px wide. TikTok accepts 360–4096 px | [A] | [YouTube](https://support.google.com/youtube/answer/10059070), [IG API](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media), [TikTok API](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) |
| Frame rate | Constant frame rate at the source cadence, within 23–60 fps. If takes mix 30 and 60 fps, conform to 30 by dropping frames. Keep 60 only when every take is 60 | [A] + [X] | IG API, TikTok API, [YouTube encoding](https://support.google.com/youtube/answer/1722171) |
| Codec | H.264 High, 8-bit 4:2:0, BT.709 tags, TV range, closed GOP with keyint = fps/2 and 2 B-frames, +faststart, no edit lists | [A] | YouTube encoding, IG API |
| Quality | CRF 14–16, capped by maxrate | [I] | research_render_timeline.md |
| IG bitrate cap | min(25 Mbps, 0.9 × 300 MB ÷ duration). That is ≈25 Mbps up to ~85 s, ≈12 Mbps at 3 min and ≈3.6 Mbps at 10 min | [A] + [X] | IG API (25 Mbps VBR max, 300 MB, 3 s–15 min) |
| YouTube reference bitrates | 1080p SDR: 8 Mbps at 24–30 fps, 12 Mbps at 48–60 fps. HDR: 10 / 15 Mbps | [A] | YouTube encoding |
| Audio | AAC-LC, 48 kHz, stereo. IG 128 kbps; YouTube 384 kbps; TikTok unspecified, so use 256–320 kbps | [A] + [X] | IG API, YouTube encoding |
| Loudness | −14 LUFS integrated, aiming for ±0.5 LU (validator gate ±1 LU); true peak ≤ −1 dBTP (−1.5 to −2 for dense mixes), measured after the AAC encode. No platform publishes a target; AES TD1008 gives −18 for speech streams, so −14 is a platform-matching choice to A/B against −16 (`voice-and-loudness.md`) | [I] target; [P] peak and YouTube behaviour | [Production Advice](https://productionadvice.co.uk/stats-for-nerds/) (YouTube turns loud audio down, never up), [AES TD1008](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf) |
| Dynamic range | SDR BT.709. Tone-map once at ingest, with HLG reference white = 75% = 203 cd/m² | [P] (ITU standard) | [BT.2408](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf) |

**HDR by platform.** Instagram's iOS app keeps iPhone Dolby Vision 8.4 through to AV1 and makes its own SDR renditions ([Meta 2025](https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/)) [A]; whether API uploads keep HDR is unverified. YouTube accepts HLG or PQ with Rec.2020 tags and auto-generates SDR ([YouTube](https://support.google.com/youtube/answer/7126552)) [A]. TikTok publishes no creator-facing HDR spec.

**Length**

| Platform | Hard limits | Data priors |
|---|---|---|
| TikTok | Up to 10 min via the API; an account's own maximum may be lower [A]. Creator Rewards (personal accounts only) pays only for videos over 1 min [A] ([TikTok, Mar 2024](https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program)); a qualified view needs ≥5 s watched and counts once per account [A] ([terms](https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en)) | Engagement rate is U-shaped (6.0% at 15–30 s, 4.2% at 30–60 s, 5.5% at 120–180 s), while median views climb with length (2,200 at 30–60 s, 11,136 at 120–180 s). 6M brand videos, H1 2026, confounded by the 1-min rule [A] (vendor-run, correlational; [Socialinsider](https://www.socialinsider.io/blog/how-long-are-tiktok-videos/)) |
| Reels | 3 s–15 min via the API [A]. Reels over 3 min stay out of the Reels tab and reach only followers [V] (Meta has not confirmed) | Median views peak at 45–60 s (10,374, against 4,700 under 30 s) and fall back to 4,428 above 180 s. 6M brand Reels, H1 2026 [A] (vendor-run, correlational; [Socialinsider](https://www.socialinsider.io/blog/instagram-reels-length/)) |
| Shorts | Square or vertical, up to 3 min (since 15 Oct 2024). Since 24 Sep 2026, a new 1–3 min Short with a Content ID claim is no longer auto-blocked, but it cannot be monetised until the claim is resolved. Most songs are usable for 90 s, some only 60 or 30 s [A] ([YouTube](https://support.google.com/youtube/answer/15424877)) | 45–59 s gets the most average views; 15–29 s, the most common length, gets the fewest (vidIQ, 331M Shorts) [V] |
| By style | Comedy and hot take 15–45 s; educational 30–60 s; listicle 30–75 s; podcast clip 30–90 s (to ~140 s if the exchange keeps building); founder 30–60 s; storytime and tutorial 45–120 s; sales 15–40 s, aiming for 20–35 s. Style files own these | [I] design_doctrine §17, updated by the style files ([V] DOAC for podcast) |

**Safe zones at 1080×1920** (pixels kept free of text, measured from each edge)

| Zone | Top | Bottom | Left | Right | Tier |
|---|---|---|---|---|---|
| Instagram Reels (Meta ads spec, official) | 269 (14%) | 672 (35%) | 65 (6%) | 65 (6%) | [A] |
| YouTube Shorts (Google's official vertical-ads overlay, measured) | 288 | 672 | 48 | 192 | [A] for ads; organic Shorts [X] |
| TikTok (varies with format and caption length, per TikTok; third-party maps) | ~140–200 | ~250–480 | ~60 | ~120–180 | [V] |
| **Cross-platform default** | Text inside x 65–888, y 288–1248 (823×960). Hook titles in y 288–600 | | | | [I] from [A] |

Both official figures are ad specs with room for ad buttons; organic UI is lighter, but a long description grows over the bottom. Treat the pixels as priors; overlay mocks built from current app screenshots outrank any table. Google's overlay supersedes third-party Shorts maps (poster.ly).

**Covers, caption files, sound and AI labels**

| | TikTok | Reels | Shorts |
|---|---|---|---|
| Cover | Set with `video_cover_timestamp_ms`; if unset or invalid, the first frame [A] | `cover_url` image (overrides `thumb_offset`), or `thumb_offset` in ms (default 0, the first frame) [A]. The profile grid shows a ~3:4 centre crop [V] | Picked from a frame in the app. A custom 9:16 image (2160×3840 recommended, ≥640 px tall, ≤50 MB) can be added only in Studio on desktop by verified accounts [A] |
| Caption file | The posting API has no subtitle field (`title` is the post text), so burn captions in [A] | `caption` is the post text; there is no subtitle-track field, so burn captions in [A] | Accepts SRT, SBV, VTT and TTML. SRT and SBV styling is ignored; TTML keeps styling and position [A] |
| Native sound | No sound picker in the API; trending sounds are added in the app [A] | Audio API (Business or Creator account with Facebook Login): trending sounds or authorised music, `audio_volume` and `video_volume` 0–100, **both default to 100**, no preview [A] | YouTube's tools allow most songs for ≤90 s; the Audio Library draws no claims [A] |
| AI label | Label realistic AI images, audio or video; `is_aigc` in the API. Auto-labels via C2PA and an invisible watermark; testing a viewer control to see less AI content (Nov 2025) [A] | Disclose photoreal video or realistic-sounding audio that was created or altered; penalties possible. Reads C2PA and IPTC; in 2024 Meta could not yet detect AI video or audio at scale [A] | Disclose realistic altered or synthetic content. Exempt: repair, upscaling, captions, and cloning one's own voice for voice-overs or dubs. Auto-labels from C2PA. Disclosure "won't limit a video's audience" [A] |

A label costs about **7–8% of likes** (1,135,817 TikTok posts plus 8 preregistered experiments; [JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493)) [L]. The loss comes from weaker parasocial connection, not quality judgments, and disclosures that signal creator effort shrink it, so the post text can say what was made by hand [X].

**Validators already enforce** encode tags, file limits, loudness, text safe-zone collisions, the no-music master and licence checks.

## How to decide

1. **Read the destination sheet in the brief:** platforms, account types (TikTok business accounts get only the Commercial Music Library and cannot join Creator Rewards; the IG Audio API needs Business or Creator), Creator Rewards or YPP enrolment, YouTube verification (for custom Shorts covers), and posting path (API or in-app draft).
2. **Set length windows before the story cut** by intersecting the style prior with each platform's limits. Length never justifies keeping filler, dead air or a weak aside.
3. **Plan variants, defaulting to none.** One master gets per-platform encodes, covers and flags. Add a cut variant only for a named reason:
   - a hard limit (a Short over 3 min), or a Short over 1 min whose claimed music would stop it earning;
   - a TikTok cut over 60 s for a Creator Rewards creator, when real content supports it (restore cut word IDs that carry substance, never gaps or fillers);
   - a Trial Reel testing hook A against hook B;
   - platform-specific CTA wording ("follow" or "subscribe") when the creator speaks one [P].
4. **Lay out text against the protected rectangles.** Captions, hook titles, callouts, list counters and the text inside screenshots and cards must sit in the band.
   - The band's centre is x ≈ 476, not 540. A caption centred on 540 and wider than about 696 px runs under the right rail.
   - Full-screen b-roll may run under the UI, but readable detail must be cropped into the band.
5. **Pick the cover.**
   - Make frame 0 a usable still: face visible, eyes open, hook title on screen, no fade from black.
   - Choose a cover frame on a word ID with eyes open, no motion blur and the title fully drawn. Set `thumb_offset` / `video_cover_timestamp_ms`.
   - The 3:4 grid crop spans y 240–1680, so a cover inside the band survives it.
6. **Route the sound for each destination.**
   - `music.md` decides whether there is music; "none" is often right for a voice-led video. This step only routes it.
   - Bake music in only if it is cleared for every destination (Epidemic safelisted, or generated with rights). Otherwise deliver the no-music master with a note for native sound.
   - Native trending sound is the creator's option, not a default. Through the IG Audio API, set `audio_volume` around 10–20 and `video_volume` 100 explicitly, because both default to 100 and would bury the voice. Check it in the app; the API has no preview [X].
7. **Screen the insert list for disclosure.** Classify each generated asset.
   - **No label:** stylised, illustrative or clearly unreal assets; colour, denoise, upscaling or repair; generated captions or titles.
   - **Label:** photoreal scenes, a real person made to say or do something, or synthetic voice.
   - **Split:** the creator's own cloned voice patching a word is exempt on YouTube but is realistic synthetic audio for Meta and TikTok. Prefer another take or a pickup.
   - Swap in a designed card or real footage when the job allows. Otherwise set the manifest flags and tell the creator.
   - Never try to strip provenance. SynthID survives re-rendering.
8. **Encode, then watch like a viewer.**
   - Review renders carry each platform's UI overlay mock and burned-in word IDs. Watch them at phone size.
   - Simulate a low-bitrate re-encode to check that captions and faces survive.
   - For YouTube, add an SRT from the final aligned words for search and translation. Viewers with CC on see it over burned-in captions, so it stays optional.
9. **Write the hand-off note** when the creator posts in-app [X]:
   - Move the file at original quality (AirDrop or Files, not a messaging app that recompresses).
   - Switch on the app's high-quality upload setting and add no in-app filters, trims or text, each of which re-encodes.
   - If captions are burned in, leave the app's auto captions off so viewers never see two sets.

## When to break it

- **Single destination.** Use that platform's own zone. An IG-only video gets text out to x 1015, which is 127 px more width than the shared band.
- **Organic posts with short descriptions.** Captions may drop toward the relaxed floor of about y 1436 (`captions-and-text.md`) once the overlay mock shows them clear. Titles and card text stay in the band.
- **The creator uses platform-native captions.** Don't burn captions in; deliver a caption-free master and keep the caption band clear of other text.
- **Cleared music the creator wants everywhere.** Bake it in. Still ship the no-music master.
- **HDR delivery.** Only when the posting path is verified to keep HDR (such as IG in-app on iOS) and the pipeline composites HDR with graphics white at 203 cd/m². Meta found SDR white text over HDR video looked grey ([Meta 2023](https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/)).
- **Longer than 3 min.** Only if the creator accepts followers-only reach on Reels or wants long-form on YouTube.
- **1440×2560 for Instagram.** Try it as an A/B. Shorts stay at 1080p.
- **Loudness.** Move to −16 only if a logged A/B supports it for quiet, intimate delivery.

## Worked example

A money creator films 4K 30 fps HDR on an iPhone, in three takes. The creator is enrolled in Creator Rewards and posts to TikTok, Reels (Business account) and Shorts. The story cut runs 58 s. It opens: "Paying on time is only a third of your credit score. The part nobody checks is utilisation."

- **Master.** Tone-mapped once to 1080×1920 SDR, 30 fps, H.264 CRF 15 capped at 25 Mbps, keyint 15, −14 LUFS, −1 dBTP.
- **Variants.** Reels and Shorts get the 58 s master. For TikTok, restoring w301–w322 ("…and this is why closing your oldest card hurts…") adds 9 s of real substance, making 67 s. Without one, TikTok gets 58 s and the brief says why.
- **Layout.**
  - Captions sit just below the chin at y ≈ 1100, capped at 680 px wide around x 476.
  - The hook title "Your score's hidden 30%" sits at y 320–520.
  - The "30% utilisation" stat card keeps all of its text inside x 65–888.
- **Cover.** Frame 0 shows the face with the title already on screen. The cover is set at 1,240 ms (on the word "third", eyes open). The YouTube account is unverified, so the creator picks the Shorts frame in the app.
- **Sound.** No music (educational, 172 wpm; the voice carries it), so every platform gets the no-music master. The hand-off offers a trending sound as the creator's option: in the TikTok app, or on Reels through the Audio API at `audio_volume` 12 with `video_volume` 100. YouTube gets an SRT built from the final words.
- **What was left alone.** No per-platform end cards, no "part 2" text, no second hook. Only TikTok's length differs, for a named reason.
- **AI.** A proposed photoreal shot of "a person frowning at a bank statement" became a designed statement card, so no label is needed anywhere.

## Anti-patterns

- **Captions anchored at y ≈ 1600,** where TikTok's description and every platform's handle cover them.
- **Full-width captions centred on x 540,** ending under the like button.
- **A fade from black or a logo sting at the start,** which makes the default cover black.
- **Padding to 61 s** with restored pauses, fillers or a recap to reach Creator Rewards.
- **Shipping only the music mix,** or baking in a copyrighted or ripped trending song.
- **Any watermark** (the app's own, stock or another platform's) or letterbox bars on landscape b-roll. Instagram demotes both.
- **Mastering to −9 LUFS** (YouTube turns it down, squashed) or to −23 (it stays quiet).
- **Uploading 4K to Shorts or the IG API,** or a fixed 25 Mbps on a 4-minute take that breaks the 300 MB cap.
- **Platform-flavoured additions nobody asked for:** a trending sound under a teaching voice, per-platform end cards, "follow for part 2" text.

## Critic questions

1. Is every caption, title and card text inside the destination's safe zone (by default x 65–888, y 288–1248), with nothing under the right rail?
2. Are frame 0 and the chosen cover frame both usable, with face visible, eyes open, no black or blurred frame, and face and title inside the centre 3:4 crop?
3. Does the delivered file measure within 0.5 LU of −14 LUFS integrated (hard gate ±1 LU) with true peak ≤ −1 dBTP?
4. Does a no-music master exist, with voice and SFX intact and no silent dropouts?
5. Is every file within its destination's limits for duration, width, file size, frame rate and codec?
6. Is the video free of watermarks, platform logos and letterbox or pillarbox bars?
7. On a phone at typical brightness, do skin and white text look natural rather than grey, washed out or clipped?
8. If a photoreal generated visual or synthetic voice appears, is it flagged for disclosure on every platform?
9. Does each platform variant have a named reason, while keeping the same story and hook?
10. If a TikTok variant runs over 60 s, is every added second real content rather than gaps, fillers or recap?
11. Is any baked-in music cleared for every platform it ships to?
12. Did packaging change anything beyond encode, cover, flags, sound route and a named variant?

## Sources

- YouTube Help, Shorts upload: https://support.google.com/youtube/answer/10059070
- YouTube Help, three-minute Shorts and Content ID: https://support.google.com/youtube/answer/15424877
- YouTube Help, upload encoding settings: https://support.google.com/youtube/answer/1722171
- YouTube Help, HDR uploads: https://support.google.com/youtube/answer/7126552
- YouTube Help, subtitle files: https://support.google.com/youtube/answer/2734698
- YouTube Help, altered or synthetic content: https://support.google.com/youtube/answer/14328491
- YouTube Help, thumbnails (Shorts): https://support.google.com/youtube/answer/72431
- Social Media Today, YouTube inauthentic-content update (Jul 2025): https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/
- Google Ads Help, video ad specs and safe zones: https://support.google.com/google-ads/answer/13547298
- Google, YouTube vertical-ads safe-zone overlay (safe area x 48–887, y 288–1247): https://services.google.com/fh/files/misc/youtubesafezoneoverlay_vertical_final.png
- Instagram Graph API, IG User Media: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- Instagram Audio API: https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/
- Meta Ads Guide, Instagram Reels video: https://www.facebook.com/business/ads-guide/update/video/instagram-reels
- Instagram, recommendations and originality: https://creators.instagram.com/blog/recommendations-and-originality
- Instagram, ranking explained: https://about.instagram.com/blog/announcements/instagram-ranking-explained
- Meta, labeling AI-generated content (Feb 2024): https://about.fb.com/news/2024/02/labeling-ai-generated-images-on-facebook-instagram-and-threads/
- Meta Engineering, Dolby Vision on Instagram iOS (2025): https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/
- Meta Engineering, HDR on Reels (2023): https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/
- TechCrunch, Instagram video quality by views: https://techcrunch.com/2024/10/27/instagram-is-lowering-video-quality-for-unpopular-videos
- PetaPixel, Instagram rectangular profile grid: https://petapixel.com/2025/01/23/instagram-swaps-square-profile-grids-for-rectangles/
- TikTok Content Posting API, media transfer: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- TikTok Content Posting API, direct post: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- TikTok Ads, in-feed ad specs: https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads
- TikTok Newsroom, Creator Rewards Program: https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program
- TikTok, Creator Rewards terms (qualified views): https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en
- TikTok Newsroom, C2PA, watermarks and AIGC control (Nov 2025): https://newsroom.tiktok.com/more-ways-to-spot-shape-and-understand-ai-content?lang=en
- Socialinsider, Reels length: https://www.socialinsider.io/blog/instagram-reels-length/
- Socialinsider, TikTok length: https://www.socialinsider.io/blog/how-long-are-tiktok-videos/
- vidIQ, 331M Shorts analysis: https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_
- poster.ly, third-party Shorts map: https://www.poster.ly/tools/youtube-shorts-safe-zone-checker
- Zeely, TikTok safe zones: https://zeely.ai/blog/tiktok-safe-zones/
- CreaMate, TikTok safe zones: https://creamate.ai/en/blog/tiktok-safe-zone-guide
- Production Advice, YouTube loudness: https://productionadvice.co.uk/stats-for-nerds/
- AES TD1008, streaming loudness: https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf
- ITU-R BT.2408, HDR reference levels: https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf
- Carney, Riveros & Tully, AI disclosure labels (JCR 2026): https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493
