# What makes short-form talking-head videos perform (2025-2026): editing guidance an agentic AI editor can adapt per video

## Summary
This track fills a gap. The earlier notes and report covered tools and architecture but said almost nothing about audiences. The evidence agrees on four points. (1) The first 1-3 seconds matter most, and every platform now measures them directly: Instagram skip rate, YouTube "stayed to watch", and TikTok's own data. (2) Cutting wasted time (speaker pauses, repetition, preamble) and ending right on the payoff beats adding effects. Cut density, b-roll and speaking energy show inverted-U or diminishing returns. (3) In 2024-26 the head of Instagram, MrBeast, Hormozi, TikTok's ad guidance and trend reports all moved away from over-stimulated "retention editing" toward raw, personal, lightly cut talking heads. Raw does not mean unedited: the hook, one idea, captions and tight pacing still matter. (4) Edit intensity should match the speaker's own vocal energy. A 12,842-video study found that matching them raises engagement and mismatching lowers it. The best length data points to 30-60 s by default and 60-180 s when there is a story or tutorial arc. The editor should work from continuous "style dials". Defaults come from each content style and are adjusted by the creator's profile and the measured energy of each clip. Evidence on captions, zooms and b-roll is mostly correlational, adoption-only or anecdotal.

## Verified findings
## 0. Scope and fact-check verdict
The earlier notes (`research_notes/Agentic video editing engine/*`) and the report covered architecture, tools and evaluation. Their only audience-side reference was a pro-cut benchmark: natural 300-550 ms pauses and at most about 4 seams per 60 s. This track adds platform and audience evidence.

**Verdict:**
- Most platform-policy claims check out. One is out of date: YouTube changed its Content ID rules on 24 Sep 2026.
- Several dataset claims need caveats: brand-only samples, product-ad samples, and one n=2 comparison. The TikTok length curve was misread.
- One number was wrong: DOAC's editor has made 200 trailers, not 450.
- The researcher missed audio-quality evidence, caption reading speeds, official safe zones, loudness practice, AI-labelling rules, and the Trial Reels API as a route to real A/B tests.

## 1. Platform facts (verified)
**TikTok**
- **Ads best-practices page** ([TikTok](https://ads.tiktok.com/help/article/creative-best-practices)):
  - Hook in the first 6 s; state the proposition in the first 3 s.
  - On-screen text at 5-10 words/s.
  - 9:16 at 720p or higher, with sound, in a "DIY or not overly polished" style.
  - Use transitions and stickers, and keep content in the UI safe zone.
  - The page gives no length recommendation.
  - Caution on the text rate: 5-10 words/s is 300-600 wpm, 2-3x the broadcast caption norm. Treat it as guidance for brief ad overlays, not captions.
- **TikTok×CreatorIQ, all confirmed but all from branded ads** ([CreatorIQ](https://www.creatoriq.com/press/releases/tiktok-creatoriq-release-special-report-with-data-backed-keys-to-success-for-advertisers?hs_amp=true)):
  - A person on screen in the first 2 s: +50% hooking power.
  - Saying "you" in the first 5 s: +128% purchase intent. Direct address: +112% recall.
  - Text overlays: 1.4x more likely to hook.
  - Music: +61% recall and +177% purchase intent. Seamless transitions: +60% recall.
  - 90% of the recall effect lands in the first 6 s.
  - These measure ad recall and intent, not organic retention.
- **Creator Rewards:**
  - Pays only for videos over 1 min, scored on originality, play duration, search value and engagement ([newsroom](https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program)).
  - Qualified views are unique For You views of at least 5 s, excluding paid and "not interested" views ([terms](https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en)).
- **API gap:** the public API exposes only view, like, comment and share counts, duration and `is_aigc`. There is no retention data ([TikTok dev](https://developers.tiktok.com/doc/tiktok-api-v2-video-object)).

**YouTube**
- **Length and music:**
  - Shorts can run up to 3 min since 15 Oct 2024.
  - **Correction:** since 24 Sep 2026, new 1-3 min Shorts with an active Content ID claim are no longer blocked automatically and may stay playable. They cannot be monetized until the claim is resolved. Nothing changed under 1 min, and most songs can be used for up to 90 s ([YT help](https://support.google.com/youtube/answer/15424877)).
  - The music-policy page still says "blocked" ([YT](https://support.google.com/youtube/answer/13486873)). The Audio Library, which draws no claims, remains the safe default.
- **Views:**
  - Since 31 Mar 2025, views count every start and replay. The old method is now "engaged views" ([YT](https://support.google.com/youtube/answer/9082582)).
  - "Stayed to watch" is the % of viewers who stay past the initial seconds; the exact threshold is not published ([YT](https://support.google.com/youtube/answer/12220281)).
  - The Analytics API exposes `engagedViews`, `audienceWatchRatio` and `relativeRetentionPerformance` ([Google](https://developers.google.com/youtube/analytics/metrics)).
- **Ranking:** YouTube weighs satisfaction surveys (secondary source, [Buffer](https://buffer.com/resources/youtube-algorithm/)).

**Instagram**
- **Top signals:** Mosseri (Jan 2025) named watch time, likes per reach and sends per reach, with sends weighing more for reaching non-followers (secondary reporting, [SMT](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/)).
- **Skip rate:** added to Insights in Aug 2025 ([SMT](https://www.socialmediatoday.com/news/instagram-adds-retention-insights-reels/758464/)). The API metric `reels_skip_rate` is views skipped in the first 3 s divided by initial views, flagged "estimated, in development" ([Meta](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights)).
- **Shown less:** low-res, watermarked, muted, bordered, mostly-text or reposted Reels ([IG](https://about.instagram.com/blog/announcements/instagram-ranking-explained)).
- **Originality (30 Apr 2024):** only the original of identical content is recommended. Accounts that post others' content 10+ times in 30 days lose recommendations, and borders or watermarks do not count as original ([IG](https://creators.instagram.com/blog/recommendations-and-originality)).
- **Length:** Reels over 3 min are not recommended to non-followers (widely reported, [Socialinsider](https://www.socialinsider.io/blog/instagram-reels-length/)).
- **Mosseri, 31 Dec 2025:** "imperfection becomes a signal". He expects the raw aesthetic to accelerate and asks whether a creator can "make something that only you could create". The framing is AI making polish cheap ([Threads](https://www.threads.com/@mosseri/post/DS76UiklIDf)).
- **Trial Reels (missed):**
  - Shown to non-followers first. The Graph API supports `trial_params`, with `graduation_strategy` set to MANUAL or SS_PERFORMANCE, and allows 100 API posts per 24 h ([Meta docs](https://developers.facebook.com/docs/instagram-platform/content-publishing/)).
  - This is the only first-party route to automated organic A/B tests.

**AI labelling and originality (missed)**
- **TikTok** labels realistic AI-generated content and reads C2PA Content Credentials automatically ([TikTok](https://newsroom.tiktok.com/more-ways-to-spot-shape-and-understand-ai-content?lang=en)).
- **YouTube disclosure:** realistic altered or synthetic content must be disclosed. Production-assistance uses are exempt, and disclosure does not reduce reach ([YT](https://support.google.com/youtube/answer/14328491?hl=en)).
- **YouTube's July 2025 "inauthentic content" policy** targets mass-produced, template-like content ([SMT](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/)).
- **Implications:**
  - Label any realistic AI-generated b-roll or voice work.
  - Avoid giving many creators identical template edits.

## 2. Safe zones and loudness (missed; deterministic grader checks)
- **Instagram (official Meta guidance):** keep about 14% at the top, 35% at the bottom and 6% on each side free of text. At 1080×1920 that is about 270, 672 and 65 px ([Meta Ads Guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels)).
- **TikTok:** there is no single pixel spec; the safe area depends on caption length and format, and TikTok provides templates. Vendors approximate 130-150 px at the top, 440-484 px at the bottom and a 140 px right rail ([summary](https://admanage.ai/blog/tiktok-ad-specs)).
- **Shorts:** no official spec. Use the union of the two.
- **Loudness:**
  - No short-form platform publishes a target.
  - YouTube turns down content louder than about −14 LUFS and never boosts quieter content ([Production Advice](https://productionadvice.co.uk/stats-for-nerds/); reverse-engineered, medium confidence).
  - Deliver at −14 LUFS integrated and −1 dBTP.

## 3. Large-sample data (correlational)
**Length**
- **Reels** (Socialinsider; 6M Reels from brand accounts, H1 2026):
  - Median views peak at 45-60 s (10,374, vs 4,700 at 1-30 s), with engagement rate at 0.35%.
  - 120-180 s is close behind (9,000 views; 0.33%).
  - Above 180 s both collapse (4,428 views; 0.15%) ([Socialinsider](https://www.socialinsider.io/blog/instagram-reels-length/)).
- **TikTok** (Socialinsider; 6M videos, brand accounts). **Correction:** engagement rate is U-shaped.
  - It is 5.9% at 0-15 s and 6.0% at 15-30 s, lowest at 30-60 s (4.2%), 5.5% at 120-180 s and 5.9% above 180 s.
  - Median views rise from about 1,000 (under 30 s) to 11,136 (120-180 s) ([Socialinsider](https://www.socialinsider.io/blog/how-long-are-tiktok-videos/)).
  - Confounded by the 1-min Creator Rewards rule.
- **Shorts** (vidIQ; 331M Shorts over 90 days):
  - 15-29 s is 37% of uploads.
  - 45-59 s gets the most average views, and 0.91% of those videos reach 1M views vs 0.53% at 15-29 s.
  - Like rate rises from 1.05% (<15 s) to 2.79% (2-3 min), which is partly survivorship ([vidIQ](https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_)).
- **FYPNow:**
  - Median length 29 s, median hook 3.0 s, first value at 5.5 s.
  - Save rate rises from 0.63% to 1.29% with length.
  - The sample is creators who already perform well, and its size is not published ([FYPNow](https://fypnow.com/research/tiktok-video-anatomy)).
- **Jenny Hoyos:** aims for about 34 s and 90%+ retention (secondary, [Leaderonomics](https://www.leaderonomics.com/videos/box-of-chocolates/how-to-make-great-youtube-shorts-lessons-from-jenny-hoyos)).
- **Takeaway:** 30-60 s by default; go longer only for a real arc.

**Cuts and visual variation**
- **Xiao et al.:** 2,511 product-promotion videos from 60 sellers (2020-21). Number of shots has an inverted-U relation to engagement, and speech rate raises shares only ([Internet Research](https://www.emerald.com/intr/article/36/1/154/1255369/Exploring-user-engagement-behavior-with-short-form)).
- **Yang et al., JTAER 2025:** 12,842 product videos from 170 Douyin influencers.
  - Measures: vocal arousal via a SpeechBrain emotion model; visual variation as scene-transition rate.
  - Arousal is an inverted U (optimum about 0.54 normalized); variation is linear-positive; audio-visual congruence is positive.
  - The reported β=0.22 could not be verified ([JTAER](https://doi.org/10.3390/jtaer20020069)).
  - These are marketing videos; generalizing to creator talking heads is an assumption.
- **FYPNow scene counts:** U-shaped. 1-2 scenes 9.6%, 3-5 scenes 8.4%, 11+ scenes 11.4%.
- **MrBeast:**
  - Tubefilter counted 38 vs 23 cuts/min in one mid-video minute of two videos, so n=2 ([Tubefilter](https://www.tubefilter.com/2024/03/04/mrbeast-editing-style-number-of-cuts-per-video/)).
  - The slowdown is also reported by [BI](https://www.businessinsider.com/mrbeast-has-grown-up-he-thinks-his-youtube-videos-should-too-2024-3).
- No causal study of zooms or SFX exists.

**Captions**
- **Verizon/Publicis 2019** (5,616 US adults aged 18-54, all video, self-report): 69% watch with sound off in public, and 80% are more likely to finish a captioned video ([Forbes](https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/)).
- **Comprehension:** more than 100 studies find benefits ([Gernsbacher](https://pmc.ncbi.nlm.nih.gov/articles/PMC5214590/)).
- **OpusClip** (13.5M clips, Jan-Mar 2026): 80.2% captioned and 78.6% animated. This is adoption only, with no performance comparison ([OpusClip](https://www.opus.pro/research/best-caption-strategy-short-form)).
- **Counter-evidence:** on 6,606 Douyin brand videos, subtitles were negatively related to comments and shares, and vocal background music was positive ([Int J Advertising 2026](https://www.tandfonline.com/doi/full/10.1080/02650487.2026.2670858)).
- **Reading speed (missed):**
  - Norms: BBC 160-180 wpm, about 0.3 s per word ([BBC summary](https://www.clevercast.com/bbc-subtitling-guidelines/)); Netflix English 20 cps for adult and 17 cps for children's content ([Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements)).
  - Word-synced captions follow speech, so they are fine.
  - Static hook text needs at least about 0.3 s per word plus about 1 s.

**Speech, energy and audio**
- **Guo, Kim & Rubin 2014** ([paper](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf)); data are 6.9M sessions, 862 videos, 4 edX courses:
  - Within length buckets, engagement rose up to 2x with speaking rate, but not steadily: it dipped at 145-165 wpm (mean 156 wpm).
  - Videos of 0-3 min sat near the ceiling regardless of rate.
  - The authors say rate is a proxy for enthusiasm; speeding up an unenthusiastic speaker may not help.
  - edX producers edited out "umm/uhh" and pauses.
  - Head plus slides beat slides alone. The informal-vs-studio result rests on one pair of courses.
- **Speed (missed):** comprehension barely drops up to 1.5-2x playback ([Murphy 2022](https://onlinelibrary.wiley.com/doi/abs/10.1002/acp.3899)). What limits speed-ups is authenticity and artefacts, not understanding.
- **Fillers (missed):** filled pauses improved recall in a lab study ([Fraundorf & Watson 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)). Removing every filler is not free.
- **Audio quality (missed, causal):** identical talks with degraded audio got lower ratings for both the research and the researcher ([Newman & Schwarz 2018](https://journals.sagepub.com/doi/abs/10.1177/1075547018759345)). This is the strongest causal lever found in this track.

**Polish**
- **Meta:**
  - 2019 direct-response ads on Stories: self-recorded mobile creative had an 84% likelihood of beating studio creative on content views.
  - A 2022 study: lower production lifted ad recall in 4 verticals ([Meta](https://en-gb.facebook.com/business/news/insights/perfection-fatigued-millennials-gen-z-want-human-video)).
- **Hootsuite 2026:** "over-editing is out"; flubs signal authenticity; nearly a third of consumers are less likely to choose brands that use AI ads ([Hootsuite](https://blog.hootsuite.com/social-media-trends/)).
- **TikTok Next 2026:** "Reali-TEA" ([TikTok](https://ads.tiktok.com/business/en/next)).
- The HubSpot 77% figure was not verified.

**Music and b-roll**
- **Music:** original vs licensed audio showed no difference (8.1% vs 8.1% engagement, FYPNow).
- **B-roll:**
  - Only illustrative b-roll helps (OpusClip, n=1, weak).
  - DOAC's Facebook tests: an opening on the guest in the chair "always came out on top" over b-roll ([McDonnell](https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven)).

## 4. Hooks, structure and endings
- **Hoyos:**
  - A visual hook in under 3 s that passes a mute test and foreshadows the ending.
  - Write the hook and the last line first; end right after the payoff.
- **Kallaway:** hook, then rehooks, varying sentence rhythm ([Castmagic](https://www.castmagic.io/podcast/rhythm-hooks-and-a-billion-views-kane-kallaways-short-form-video-content-secrets)).
- **DOAC (corrected):**
  - 200 trailers of about 90 s each.
  - Workflow: 2 h of podcast, then 1 h of soundbites, then a transcript, then the story built in a document, then the 90 s edit.
  - Four beats: hook, lesson, emotional rollercoaster, cliffhanger.
  - This is the "design in text" pattern, which suits Claude.
- **Diagnostics:** skip rate or ECR for the hook, the retention curve for the middle. Compare against the creator's own median; there are no official benchmarks.

## 5. Trends (mostly practitioner or vendor evidence)
- **Fading:**
  - The full "Hormozi package": yellow keywords, a whoosh per word, emoji and shake.
  - Interrupts every 0.5-1 s, and constant zoom pulses.
  - Stock intros and heavy skin smoothing.
- **Rising:**
  - Raw or lightly cut single takes.
  - Speech-synced chunks of 2-4 words with no SFX or emoji.
  - Keeping flubs, and 45-90 s story arcs.
- **Strongest signals:** Mosseri, TikTok's "not overly polished" guidance, the MrBeast and Hormozi pivots, and Hootsuite.

## 6. Default dials by content style (starting points Claude adjusts)
| Style | Length | Open | Pace | Text | Layers | Music/SFX |
|---|---|---|---|---|---|---|
| Explainer | 30-60 s | Counterintuitive answer ≤2 s | Cut speaker pauses; keep the beat after key claims | Synced chunks; highlight numbers | Proof inserts; 2-4 zooms | Low bed optional |
| Storytime | 45-120 s | Drop in at the stakes | Keep dramatic pauses | Plain | One framing; push-in at the climax | None or ambient |
| Hot take | 15-45 s | The take itself | Tight | Hook text = the take | One punch-in | None |
| Listicle | 30-75 s | Count + promise | Hard cut per item | Counter | Zoom as section marker | Light bed |
| Tutorial | 45-120 s | Show the result first | Speed-ramp dull stretches | Step labels | Screen or hands dominate | Low bed |
| Podcast clip | 30-60 s | Peak + context card | Pauses 0.2-0.3 s | 3-5 emphasized words | Active-speaker reframe | Usually none |
| Founder/brand | 30-60 s | Specific stake | Natural; keep flubs | Clean | Proof inserts | Subtle |
| UGC ad | ~21-34 s | Person + "you" ≤2 s | Hook, problem, demo, proof, CTA | Overlays + CTA | Lo-fi product in use | Music aids recall |

## 7. Matching the edit to the creator
- **Energy matching:**
  - Measure vocal arousal with a speech-emotion model, as JTAER did, plus words per minute.
  - Set the scene-transition rate to match: calm speakers get few seams, no SFX and plain captions.
  - Never fake energy with speed-ups.
- **Signature:** keep laughs, catchphrases and stumbles that carry personality (Mosseri's "only you could create" test).
- **Consistency:** keep a consistent per-creator look, but avoid giving all creators the same template (YouTube inauthentic-content policy, IG originality).

## 8. How strong the evidence is
- **Causal (lab):** audio quality → credibility; playback speed → comprehension; fillers → recall.
- **Strong:** platform definitions and policies.
- **Medium:** peer-reviewed correlational studies, which are mostly marketing or product videos, and large vendor datasets, which are often brand-only.
- **Weak:** trend blogs, and n=1 or n=2 comparisons.
- **Candidate learned proxy:** SnapUGC/LMM-EVQA predicts ECR (the share of viewers watching past about 5 s) with SROCC 0.707 and PLCC 0.714. The audio-aware model beat the visual-only one. The code is Apache-2.0 but the dataset license is unclear, and the domain is Snapchat Spotlight. Use it only to rank alternative openings of the same clip ([VQualA](https://arxiv.org/abs/2509.02969), [LMM-EVQA](https://github.com/sunwei925/LMM-EVQA)).
- **Still unknown causally:** optimal cut density, zoom rate and caption style. The engine must run its own experiments.

## Verified recommendations
- Encode guidance as continuous style dials: energy, cut density, pause policy, caption style, text load, b-roll ratio, zoom and SFX budgets, music, polish and length. Set them from three things: a content-style default, a per-creator profile and features measured on each clip. Those features are vocal arousal from a speech-emotion model, words per minute and scene-transition rate. Claude reasons over the dials instead of applying fixed rules. Confidence: high for the structure. The evidence for particular values comes mostly from marketing videos.
- Treat the first 1-3 s as a hard gate. Open on the claim, payoff or stakes, with the face or subject visible and hook text that passes a mute test. Never open on a greeting or logo. Generate 2-3 alternative openings. Optionally rank them with an ECR predictor (SnapUGC/LMM-EVQA), used only for relative ranking. Let the creator, or a Trial Reel, choose. Confidence: high.
- Make removing wasted time the core pass: false starts, repetition, preamble, speaker pauses and trailing after the payoff. Keep pauses that serve the listener (about 300-550 ms; 0.2-0.3 s for podcast clips). Keep an occasional natural filler or flub that carries personality rather than scrubbing every one (Fraundorf & Watson; Hootsuite 2026). End on the payoff, or on a seamless loop when under about 35 s. Confidence: high.
- Make audio polish a first-class pass, ahead of visual effects. Apply speech enhancement and de-noise without audible artefacts, keep dialogue level consistent, duck music under speech, and deliver at -14 LUFS integrated and -1 dBTP. Confidence: high. This is the only causal evidence in the track (Newman & Schwarz 2018), and YouTube turns down anything louder.
- Match visual variation to measured vocal energy. Calm speakers get few seams, no SFX and plain captions; high-energy speakers can take more. Speed-ups of about 5-10% are acceptable for comprehension (Murphy 2022), but never use them to fake enthusiasm (Guo 2014). Confidence: medium.
- Captions on by default, as speech-synced chunks of 2-4 words with emphasis only on numbers and key terms. Avoid the full Hormozi package by default. Give static hook text and cards a dwell of at least about 0.3 s per word plus about 1 s (BBC and Netflix norms). Do not use TikTok's 5-10 words/s ad figure for captions. Place all text inside Meta's 14% top / 35% bottom / 6% side safe zone and clear of TikTok's right rail. Confidence: medium-high.
- Treat zooms, SFX and b-roll as scarce emphasis tied to story turns. Use b-roll only when it illustrates or proves what is being said, and return to the face for key claims and the CTA. Defaults per 60 s: 2-4 zooms; SFX only on real visual changes. Confidence: medium (no causal evidence exists).
- Set length by style and platform: 30-60 s by default; 45-90 s for stories and explainers; 60-180 s only when there is a real arc. Offer a version of at least 60 s for TikTok Creator Rewards creators, and keep Reels at 3 min or less. Never pad. Confidence: medium. The datasets are brand-heavy, and TikTok's engagement rate is U-shaped, with its lowest point at 30-60 s.
- Add deterministic platform-safety checks to the grader. Check for: no watermarks, borders or muted original audio; text inside safe zones; loudness in range. Use Audio Library music on YouTube, which avoids claims and keeps monetization; since 24 Sep 2026, claimed 1-3 min Shorts stay playable but cannot be monetized. Flag realistic AI-generated b-roll or voice for platform AI disclosure (TikTok AIGC/C2PA, YouTube altered-content). Confidence: high.
- Close the feedback loop where APIs allow it, with creator consent. Instagram: reels_skip_rate, ig_reels_avg_watch_time, sends; publish edit variants as Trial Reels via trial_params to run real organic A/B tests. YouTube: engagedViews, audienceWatchRatio. TikTok's public API has no retention, so rely on counts plus creator-supplied analytics. Compare each video against the creator's own median. Confidence: medium.
- Default to a raw-leaning, creator-specific look: light grading, no skin smoothing, no stock intros. Offer a kinetic 'full retention' style only when the genre or persona fits or the creator asks. Vary the treatment across creators so outputs never look like one template, which protects against YouTube's inauthentic-content policy and Instagram's originality ranking. Confidence: medium.

## Corrections by fact-checker
- [corrected] YouTube: a Short over 1 min with any Content ID claim is blocked globally, so use Audio Library music → Out of date as of 3 days ago. Since 24 Sep 2026, new Shorts of 1-3 min with an active Content ID claim are no longer blocked automatically and may stay playable. They cannot be monetized until the claim is resolved. Nothing changed for Shorts under 1 min. Most songs can be used for up to 90 s through YouTube's tools. YouTube's music-policy page (13486873) still says 'blocked', so YouTube's own docs disagree. The Audio Library remains the safe default because its tracks draw no claims and keep the Short monetizable. https://support.google.com/youtube/answer/15424877
- [corrected] TikTok (Socialinsider, 6M videos): engagement rate is highest at 15-30 s (6.0%) → Engagement rate is U-shaped, not a peak. It is 5.90% at 0-15 s, 6.00% at 15-30 s, lowest (4.20%) at 30-60 s, 5.50% at 120-180 s and 5.90% above 180 s. Median views climb from about 1,000-1,274 (under 30 s) to 11,136 (120-180 s). The sample is brand accounts only, not creators. https://www.socialinsider.io/blog/how-long-are-tiktok-videos/
- [confirmed] Reels (Socialinsider, 6M Reels H1 2026): median views and engagement rate peak at 45-60 s → Numbers are exact: 10,374 vs 4,700 views, 0.35% ER, and a collapse above 180 s (4,428 views; 0.15%). However, the sample is BRAND accounts (Jan-Jun 2026), and 120-180 s is nearly as good (0.33% ER, 9,000 views). https://www.socialinsider.io/blog/instagram-reels-length/
- [corrected] DOAC trailer editor has made 450+ trailers → The cited source says Ant Smith has made 200 trailers, each distilled to about 90 s: 2 h of podcast, then 1 h of soundbites, then a transcript, then the story built in a document. The four beats and the Facebook tests of openings (guest in the chair 'always came out on top' over b-roll) are confirmed. https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven
- [unverifiable] 12,842 Douyin videos: matching visual variation to vocal arousal raises engagement (β=0.22) → Direction confirmed: arousal is an inverted U (optimum about 0.54 on the normalized scale), visual variation (scene-transition rate) is linear-positive, and congruence is significantly positive. The β=0.22 figure could not be checked because MDPI returned 403. The sample is product-promotion videos from 170 influencers (May-Jul 2024), and arousal was measured with a SpeechBrain emotion model. It is marketing content, not general creator talking heads. https://doi.org/10.3390/jtaer20020069
- [confirmed] Guo, Kim & Rubin 2014: engagement rose up to 2x with faster speech; informal talking heads beat studio lectures → Confirmed, with nuance. The data are 6.9M sessions on 862 videos from 4 edX courses (Fall 2012). The 'up to 2x' holds only within length buckets and is non-monotonic: there is a dip at 145-165 wpm (mean 156 wpm), and 0-3 min videos sit near the ceiling whatever the rate. The informal-vs-studio finding rests on one pair of courses (instructor confound). The paper also reports that edX producers edited out 'umm/uhh' and pauses, and that head plus slides beat slides alone. https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf
- [confirmed] MrBeast mid-video cuts fell from 38/min to 23/min (Tubefilter) → The numbers are accurate, but Tubefilter counted one mid-video minute in each of just two videos (Mar 2023 vs Mar 2024). It is an illustration (n=2), not a measurement of his style. https://www.tubefilter.com/2024/03/04/mrbeast-editing-style-number-of-cuts-per-video/
- [confirmed] Instagram reels_skip_rate (% of views skipped in the first 3 s) is available via the Graph API → The definition is verbatim: views skipped in the first 3 s divided by initial views. Meta flags it as 'estimated and in development'. ig_reels_avg_watch_time, shares, saved and reposts are also available. https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
- [confirmed] TikTok ads best practices: hook in the first 6 s, proposition in the first 3 s, on-screen text at 5-10 words/s, 9:16 at 720p or higher, DIY / not over-polished → Wording confirmed, and the page gives no length recommendation. However, 5-10 words/s is 300-600 wpm, about 2-3x the broadcast caption norm (BBC 160-180 wpm). Treat it as guidance for brief ad overlays, never as a caption speed. https://ads.tiktok.com/help/article/creative-best-practices
- [confirmed] TikTok x CreatorIQ: person in the first 2 s gives +50% hooking power; 'you' in the first 5 s gives +128% purchase intent; +112% recall; text overlays 1.4x; music +61% recall; transitions +60%; 90% of recall in the first 6 s → All figures confirmed. All come from branded creator ADS and measure recall or intent, not organic retention. Music also showed +177% purchase intent. https://www.creatoriq.com/press/releases/tiktok-creatoriq-release-special-report-with-data-backed-keys-to-success-for-advertisers?hs_amp=true
- [confirmed] TikTok Creator Rewards pays only for videos of 1 min or longer, weights four factors, and needs at least 5 s watched for a qualified view → Confirmed. Qualified views are unique For You views of at least 5 s, excluding paid, promoted, fraudulent and 'not interested' views. The details of the 5 s rule come from the terms and secondary summaries. https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program
- [confirmed] vidIQ, 331M Shorts: 45-59 s has the most average views; 0.91% vs 0.53% reach 1M; like rate rises from 1.05% to 2.79% with length → Confirmed for Shorts from a 90-day window. The rise in like rate with length is partly survivorship, since longer Shorts keep only committed viewers. https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_
- [confirmed] Mosseri 2026 outlook: raw aesthetic accelerates; 'imperfection becomes a signal' → Posted 31 Dec 2025 on Threads. He frames it around AI making polish and even 'authenticity' cheap to reproduce, and poses the test 'can you make something that only you could create?'. https://www.threads.com/@mosseri/post/DS76UiklIDf
- [confirmed] Instagram shows less of low-res, watermarked, muted, bordered, mostly-text or reposted Reels; copies are replaced by originals and aggregators removed → Confirmed. The originality update is dated 30 Apr 2024. Accounts that post others' content 10+ times in 30 days lose recommendations, and adding borders or watermarks does not make content original. https://creators.instagram.com/blog/recommendations-and-originality
- [confirmed] Verizon/Publicis 2019: 69% watch with sound off in public; 80% more likely to finish a captioned video → Survey of 5,616 US adults aged 18-54 (Apr 2019). It covers video in general, not short-form, and is self-reported. https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/
- [confirmed] Subtitles correlated with fewer comments and shares on 6,606 Douyin brand videos (cited via an exa.ai library link) → Confirmed. The primary source is the International Journal of Advertising (2026, negative binomial models), which also finds vocal background music positively related to all three engagement types. https://www.tandfonline.com/doi/full/10.1080/02650487.2026.2670858
- [confirmed] Meta: self-recorded mobile creative 84% likely to beat studio creative → This is a 2019 Meta study of direct-response ADS on Stories, measuring content views. The lift in recall from lower production is a separate 2022 study across 4 verticals (tech, retail, restaurants, e-commerce). https://en-gb.facebook.com/business/news/insights/perfection-fatigued-millennials-gen-z-want-human-video
- [confirmed] TikTok Sans is OFL-licensed and suits captions → SIL OFL 1.1, and on Google Fonts. It has four variable axes: weight, width, slant and optical size. https://github.com/tiktok/TikTokSans

## Missed items added
- Audio quality causally affects credibility. Identical talks played with degraded audio were rated less favourably, and so was the speaker. This is the strongest causal evidence in the track and argues for speech enhancement and level consistency before visual effects. [Newman & Schwarz 2018, Science Communication](https://journals.sagepub.com/doi/abs/10.1177/1075547018759345)
- Filled pauses ('uh') improved recall of passages compared with fluent and cough-matched controls in a lab study. Deleting every filler is not free; occasional natural fillers before key points can stay. [Fraundorf & Watson 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)
- Playing lectures at 1.5-2x cost little comprehension. Comprehension does not limit mild speed-ups; perceived authenticity and pitch artefacts do. [Murphy et al. 2022, Applied Cognitive Psychology](https://onlinelibrary.wiley.com/doi/abs/10.1002/acp.3899)
- Caption reading-speed norms: BBC 160-180 wpm (about 0.3 s per word); Netflix English 20 cps for adult and 17 cps for children's content, with events of at least 5/6 s. Use these for static hook text and title cards. [Clevercast BBC summary](https://www.clevercast.com/bbc-subtitling-guidelines/), [Netflix TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements)
- Official Meta safe zone for Reels: keep about 14% top, 35% bottom and 6% of each side free of text and logos (about 270/672/65 px at 1080x1920). TikTok publishes no single pixel safe zone; its templates vary with caption length and format. [Meta Ads Guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels), [TikTok spec summary](https://admanage.ai/blog/tiktok-ad-specs)
- Loudness: TikTok, Instagram and Shorts publish no target. YouTube turns down content louder than about -14 LUFS and does not boost quieter content (reverse-engineered via Stats for Nerds). Deliver at -14 LUFS integrated and -1 dBTP. [Production Advice](https://productionadvice.co.uk/stats-for-nerds/)
- Instagram Trial Reels (Dec 2024) are shown to non-followers first. The Graph API supports trial_params with graduation_strategy MANUAL or SS_PERFORMANCE (limit: 100 API posts per 24 h). This is the only first-party route to automated organic A/B tests of edit variants. [Meta content publishing docs](https://developers.facebook.com/docs/instagram-platform/content-publishing/), [Meta newsroom](https://about.fb.com/news/2024/12/trial-reels-try-content-non-followers-first-see-what-perfoms-best/)
- The TikTok API v2 video object exposes only view, like, comment and share counts, duration and is_aigc. There is no watch time or retention, so the feedback loop cannot learn TikTok hook or retention data through the API. [TikTok developers](https://developers.tiktok.com/doc/tiktok-api-v2-video-object)
- AI disclosure and originality rules. TikTok labels realistic AI-generated content and auto-reads C2PA Content Credentials. YouTube requires disclosure of realistic altered or synthetic content; production-assistance uses are exempt and disclosure does not reduce reach. YouTube's 15 Jul 2025 'inauthentic content' policy targets mass-produced, template-like content. These matter for AI b-roll or voice features and for identical template edits across many creators. [TikTok newsroom](https://newsroom.tiktok.com/more-ways-to-spot-shape-and-understand-ai-content?lang=en), [YouTube help](https://support.google.com/youtube/answer/14328491?hl=en), [Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/)
- SnapUGC (120k Snapchat Spotlight videos, 5-60 s, 2,000+ views each) is labelled with ECR (engagement continuation rate), the share of viewers who watch past about 5 s, which is a direct proxy for the hook. The ICCV VQualA 2025 winner LMM-EVQA (Apache-2.0) reached SROCC 0.707 / PLCC 0.714. The audio-aware VideoLLaMA2 beat the visual-only Qwen2.5-VL. The dataset license is unclear. [VQualA 2025](https://arxiv.org/abs/2509.02969), [LMM-EVQA](https://github.com/sunwei925/LMM-EVQA), [Sun et al.](https://arxiv.org/pdf/2508.02516)
- YouTube's own Shorts limits: most songs can be used for up to 90 s in a 3-min Short via YouTube tools, and some tracks only for 60 or 30 s. [YouTube help](https://support.google.com/youtube/answer/15424877)
- Guo 2014 also found that alternating the talking head with slides beat slides alone, and that tutorials get re-watched and skimmed while lectures are watched once. This supports face-plus-proof insert patterns for explainers and chapter-like signposting for tutorials. [Guo, Kim & Rubin](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf)

## Tools
- Instagram Graph API media insights (reels_skip_rate, ig_reels_avg_watch_time, shares, reach) [performance feedback / analytics API; Proprietary (Meta Platform Terms), free] https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights — Official Meta docs define reels_skip_rate as the % of views skipped in the first 3 s. It matches the Aug 2025 Insights feature and Mosseri's named signals (watch time, likes and sends per reach). Lets the editor close the loop with each creator's real hook and retention data. Some metrics are marked 'in development'.
- YouTube Analytics API (engagedViews, audienceWatchRatio, relativeRetentionPerformance) [performance feedback / analytics API; Proprietary (Google API ToS), free] https://developers.google.com/youtube/analytics/metrics — Official Google docs. Engaged views keep the pre-March-2025 view definition, and audienceWatchRatio gives the per-moment retention curve. Use it to learn per-creator pacing and hook preferences. Raw Shorts views have been inflated by replays since 31 Mar 2025.
- TikTok Creative Center (Top Ads, trends, creative insights) [reference / trend and ad library; Proprietary, free] https://ads.tiktok.com/business/creativecenter — TikTok's official hub. Its best-practices page gives concrete specs: hook ≤6 s, proposition ≤3 s, text 5-10 words/s, 9:16 at 720p or higher. Good source of examples for UGC and sales-ad treatment. Its statistics are mostly ad recall, not organic retention.
- YouTube Audio Library / Shorts Audio Library [music source (royalty-free); Free to use on YouTube; Audio Library tracks will not receive Content ID claims] https://support.google.com/youtube/answer/13486873 — Official YouTube help: Shorts over 1 min with any Content ID claim are blocked globally, and royalty-free Audio Library music avoids this. Needed if the engine adds music to Shorts longer than 60 s.
- TikTok Sans (variable font) [caption typography; SIL Open Font License (OFL)] https://github.com/tiktok/TikTokSans — Verified in the google/fonts METADATA.pb: license OFL, designers Grilli Type / Contrast Foundry / Type Network, added to Google Fonts 2025-07-09. Practitioner sources describe it as a rising caption font. A free, platform-native caption font that fits the clean caption style. Variable weight and width support emphasis without extra colours.
