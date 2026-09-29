# Captions and on-screen text for talking-head short-form (Yunicorn agentic editor rebuild)

## Summary
The research gives firm numbers for reading speed, caption timing and alignment accuracy. It gives almost nothing peer-reviewed on which caption style keeps people watching. Deaf and hard-of-hearing viewers prefer captions that stay in one place and stay static. They dislike captions hidden under platform buttons and erratic animation. Emoji help when used sparingly. Keyword highlighting helps people learn, but viewers found word-by-word highlights distracting for everyday watching. So the default should be 2–4 word phrase chunks, a heavy sans-serif font with a thick outline, at most one accent-coloured keyword per chunk, a short entry pop, and a fixed position just below the chin. The one-word "Hormozi" beat should be used only for emphasis. Caption timing should come from a forced aligner, not raw Whisper. Montreal Forced Aligner is the most accurate aligner in both a peer-reviewed study and a 2026 benchmark, at about 21 ms error on conversational speech. Qwen3-ForcedAligner (Apache-2.0) is the multilingual fallback. Remotion should stay the renderer. One concrete bug: Yunicorn's current bottom caption position (y=1600, 320 px bottom margin) sits under the TikTok, Reels and Shorts on-screen controls.

## Verified findings
## 0. Prior research and the current system
The earlier engine report settled two things. Captions are regenerated in code from the kept words and snapped to word start frames, and Remotion stays the renderer. It left caption design uncovered. Yunicorn's current `layout.json` places captions inside every platform's bottom interface zone ([layout.json](file:///Users/home/Marque/render/src/layout.json)):
- `SAFE_BOTTOM_PX: 320`
- a "bottom" anchor at 0.8333 (y≈1600)
- `CAPTION_POS_Y_MAX: 0.85` (y≈1632)
- a watermark 336 px from the bottom

It also renders at 30 fps, so one frame is 33 ms.

## 1. What captions are proven to do (the evidence is thin)
- **Meta.** "Captioned video ads increase video view time by an average of 12%" comes from undated internal tests with no published method ([Meta](https://www.facebook.com/business/news/updated-features-for-video-ads)).
- **Meta/Toluna Reels.** The outcome measured was the chance of ranking in the top 20% for *purchase intent* or *brand interest* in ad surveys. It was not watch time. Correlational, with no method published ([Meta](https://www.facebook.com/business/news/reels-creative-strategies)):
  - main message in the first 5 s: 1.7×
  - audio and visual together: 1.8×
  - emoji: 2.5×
- **Vendor claims.** Figures such as "+12–40% watch time" are unsourced. **No peer-reviewed study compares karaoke, keyword-highlight and sentence captions on short-form retention.** Yunicorn must A/B test this itself.

## 2. Styles and words per screen
**New and most relevant: one line beats two in vertical video.** Li (2026) had 211 viewers watch a TikTok with webcam eye tracking. Two-line subtitles drew longer total fixation and more revisits, and were skipped less, than one-line subtitles, even with character count controlled ([Li 2026](https://onlinelibrary.wiley.com/doi/10.1002/acp.70262)). More text on screen pulls the eye away from the face.

**CHI 2024 (McDonnell et al.)** ([PDF](https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf)):
- Sample: 300 TikToks collected in February 2023, half of them Deafness or disability content, plus 9 interviewees, all DHH but one. It predates the peak of Hormozi-style captions.
- Timing: 83.3% used movie-like line timing, 5% one or a few words at a time, 3.3% built lines up word by word.
- Motion: 10% animated their captions and 34.3% moved them around the screen.
- Colour: 87% used black and white.
- Interviewees wanted captions "static, right there, simple, clean". They disliked erratic motion, interface overlap and captions "far from the action". They liked sparing emoji and called heavy use "a little bit cringey".

**Keyword highlights.** Second-language learners (n=49) preferred time-synced keyword highlights for learning, but found them "too distracting to replace standard captions in everyday viewing" ([arXiv 2307.05870](https://arxiv.org/abs/2307.05870)). Signalling cues help learning: a meta-analysis of 103 studies (n=12,201) found g=0.53 for retention and 0.33 for transfer ([Schneider 2018](https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581)). Emphasis works, but only when sparse.

**One word at a time.** Spritz-style one-word display (RSVP at 250 wpm) impaired literal comprehension and raised visual fatigue compared with normal reading ([Benedetto 2015](https://www.sciencedirect.com/science/article/abs/pii/S0747563214007663)). This is an analogy, because captions duplicate the audio.

**Style references:**
- **Hormozi/Submagic:** TheBoldFont, Anton or Montserrat Black; all caps; 1–3 words; a coloured keyword ([Submagic](https://www.submagic.co/blog/how-to-make-alex-hormozi-captions)).
- **Remotion template:** 1200 ms pages with a green current-word karaoke highlight (#39E508) ([Page.tsx](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/Page.tsx)).
- **Netflix-style:** at most 42 characters per line, 2 lines, bottom-heavy ([Netflix EN-US](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide)).

**Default:**
- One line of 2–4 words, about 20 characters or fewer, split at phrase boundaries, with a subtle current-word state.
- Single-word punch pages only for the hook, numbers and punchlines.
- A mixed-case minimal sentence style for calm or teaching content.

## 3. Reading speed and timing
- **Standards.** The Netflix EN-US guide sets up to 20 CPS for adults and 17 for children. General requirements set 5/6 s minimum and 7 s maximum per event ([Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements)).
- **Research.** Viewers comprehended 12, 16 and 20 CPS equally well. When they understood the soundtrack, they **preferred faster, unreduced subtitles**, and slow ones caused re-reading and frustration ([Szarkowska 2018](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331)). Auto-subtitles shown too fast cut comprehension by about 10% ([2024](https://www.tandfonline.com/doi/full/10.1080/1475939X.2024.2433259); the exact CPS was not verifiable behind the paywall).
- **Creator pace (vendor data).** Reels run 184 wpm (n=312) and TikTok 172 wpm (n=121), pauses included ([Voqusa](https://www.voqusa.com/en/blog/video-transcription-statistics-2026)). Verbatim, that is about 16–18 CPS. A 1.15× speed-up passes 20 CPS.
- **What this means for synced captions.** The page rate follows the speech rate, so "≤20 CPS" and "≥0.3 s per word" **cannot be enforced as hard limits**: 184 wpm already averages 0.33 s per word. Enforce things you can control instead:
  - a minimum page duration of about 0.5 s (punch pages about 0.25 s), merging short pages;
  - character caps per page;
  - 2-frame gaps.

  Treat CPS as a monitored metric that triggers re-paging into bigger pages or a limit on speed-ups. It should not trigger deleting text. TikTok's "5–10 words per second" ([TikTok](https://ads.tiktok.com/help/article/creative-best-practices)) contradicts all of this.
- **Netflix timing rules to adapt** ([timing guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines)):
  - Captions start within 1–2 frames of the audio.
  - Out-times ideally land at least 0.5 s after the audio ends, but only at the end of a speech run.
  - Leave 2-frame gaps, and chain captions when the gap is under 0.5 s.
  - End a caption 2 frames before a shot change.
- **Highlight sync.** In lip-sync research, viewers notice sound ahead of the picture at about 45 ms and behind it only at about 125 ms ([ITU-R BT.1359](https://www.itu.int/rec/R-REC-BT.1359)). By analogy, let highlights lead speech by 0–1 frame and never lag.

## 4. Typography, colour and emphasis
- **Font.** Use a heavy sans at weight 800–900: Montserrat, Anton or Inter (all OFL). TheBoldFont is "100% free" for any use, but the free version is uppercase-only with few glyphs; the Pro version adds lowercase ([license](https://github.com/remotion-dev/template-tiktok/blob/main/public/theboldfont-license.rtf)).
- **Outline.** Use `WebkitTextStroke` with `paintOrder: stroke` plus a soft shadow ([Remotion](https://www.remotion.dev/docs/captions/displaying)). This beats Yunicorn's second outline-only copy of the text.
- **Case.** Capitals are read faster near the acuity limit, and the advantage disappears at large sizes ([Arditi & Cho 2007](https://pubmed.ncbi.nlm.nih.gov/17675131/)). All caps is fine for short, large pages. Use mixed case for sentences. DCMP reserves caps for screaming ([DCMP](https://dcmp.org/learn/225-captioning-tip-sheet)).
- **Emphasis (added).** Caption Royale tested typographic changes (weight, size, colour) with 39 DHH participants, who chose styles on readability and minimal distraction ([CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642258)). An earlier study mapped loudness to font weight ([CHI 2023](https://dl.acm.org/doi/10.1145/3544548.3581511)). Let Claude pick at most one accent keyword per page from meaning *plus* measured prosody. Code computes per-word loudness, pitch and duration z-scores from the aligned words, because Claude cannot hear the audio. Use white text with one accent colour.

## 5. Position, safe zones and faces (1080×1920)
| Platform | Top | Bottom | Right | Left | Source quality |
|---|---|---|---|---|---|
| Meta Reels/Stories | 14% (269 px) | 35% (672 px) | 6% (65 px) | 6% | official ([Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels)) |
| TikTok | ~108–150 | ~270–484 | ~120–180 | ~44–60 | third parties only; TikTok says the zone varies with post-caption length ([Zeely](https://zeely.ai/blog/tiktok-safe-zones/)) |
| YouTube Shorts | 288 | 672 | 192 | 48 | third-party reading of Google's template ([poster.ly](https://www.poster.ly/tools/youtube-shorts-safe-zone-checker)) |

- **Common safe band:** x 65–888, y 288–1248. A relaxed organic floor is about y 1436.
  - Yunicorn's current bottom anchor (y≈1600), its y-max (1632) and the Remotion template box (y 1420–1570) all violate it.
- **Place captions near the face (added evidence).** Speaker-following subtitles raised fixations on relevant image regions and shortened saccades, n=40 ([Kurzhals CHI 2017](https://dl.acm.org/doi/10.1145/3025453.3025772)). CHI 2024 users complained about captions "far from the action" but also wanted a consistent position.
- **Rule.** Anchor captions just below the chin inside the band, and never over the eyes or mouth. Move them only to avoid a collision.
- **Face tools.** Get face and chin boxes from MediaPipe Face Landmarker (Apache-2.0). Do not use InsightFace's pretrained models, which are non-commercial ([InsightFace](https://github.com/deepinsight/insightface)).
- **Verify.** Measure screenshots of the live organic apps before trusting third-party pixel values.

## 6. Line breaking
- **Where to break.** Follow Netflix and DCMP: break after punctuation or before a conjunction or preposition. Never split an article or adjective from its noun, a first name from a last name, a pronoun from its verb, or an auxiliary from its verb. Prefer a bottom-heavy shape.
- **Evidence.** Breaks that ignored syntax raised cognitive load without hurting comprehension ([Gerber-Morón 2018](https://bop.unibe.ch/JEMR/article/view/4267)).
- **Division of work.** Claude proposes the breaks. Code validates the fit with `@remotion/layout-utils` `measureText`/`fillTextBox`, which are browser-only ([docs](https://www.remotion.dev/docs/layout-utils/fill-text-box)).
- **iOS parity (engineering inference).** CoreText and Chrome measure text differently. The server should emit explicit page text, line breaks and font size, and the iOS editor should reproduce them, not re-wrap.

## 7. Animation and emoji
- **Animation.** The template uses a 5-frame spring: scale 0.8→1 and a 50 px rise ([SubtitlePage.tsx](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/SubtitlePage.tsx)). CHI 2024 users found strobing and shaking "jarring".
  - Allow one entry pop per page, 150–200 ms.
  - Current-word scale at most 1.1×.
  - No looping bounce or shake.
  - At most 3 flashes per second ([WCAG 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html)).
- **Emoji.** Use about one per beat at most, next to the words, never replacing them. Render with Noto Color Emoji (OFL) or Twemoji (CC-BY 4.0), never Apple glyphs.

## 8. Other on-screen text (evidence mostly from practitioners)
- **Hook title.** From 0 to about 3 s, 7 words or fewer, in the upper band (y 288–600), restating the spoken promise. Hide or shrink captions meanwhile.
- **Depth effect (added).** "Text behind subject" puts the title between the person and the background; it has been a trend since about 2025 ([CapCut guide](https://www.capcut.com/resource/how-to-put-text-behind-a-person-in-capcut)). Build it with a person matte from SAM 2 (Apache-2.0), BiRefNet (MIT) or MediaPipe (Apache-2.0), composited as a layer above the text in Remotion. Avoid RobustVideoMatting (GPL-3.0) and MatAnyone (non-commercial S-Lab licence). The evidence is from practitioners only, so A/B test it.
- **Callouts and lower thirds.** Numbered lists and stat callouts fire when the speech triggers them, with one focal text element at a time. A lower third appears once, on self-introduction. Keyword pop-ups at most once every 5–10 s.

## 9. Word-timing accuracy (the aligner decides timestamps, never the LLM)
Buckeye conversational speech, TEST split, Track 1 (aligner given the reference transcript) unless noted ([FA-Bench gold](https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md); [FA-Bench ASR](https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md)):

| System | MAE clean | F1@20 ms | Clean / noise failures (of 4,513) | License |
|---|---|---|---|---|
| MFA 3.4 | 21.3 ms | 0.68 | 8 / 354 | code MIT; English model CC-BY-4.0 |
| Olign 1.0 (the benchmark owner's API) | 22.5 | 0.74 | 0 / 1 | commercial |
| Charsiu | 28.1 | 0.61 | 0 / 112 | MIT, stale since 2022 |
| Qwen3-FA | 33.8 | 0.43 | – | Apache-2.0 |
| MMS-FA | 37.1 | 0.23 | – | weights CC-BY-NC |
| WhisperX | 41.7 | 0.17 | 0 / 0 | BSD-2 |
| Google Chirp 2 (own timestamps, Track 2) | 26.9 | 0.44 | 0 / 3 | commercial |
| Scribe v2 (own timestamps, Track 2) | 34.8 | 0.11 | 0 / 34 | commercial |
| Whisper large-v3 raw (Track 2) | 122.7 | 0.14 | 0 / 0 | MIT |

- **Peer-reviewed support for MFA.** On Buckeye within 50 ms: MFA 84.9%, MMS 75.0%, WhisperX 67.4%. On TIMIT: 89.4%, 75.7%, 82.4% ([Rousso, Interspeech 2024](https://arxiv.org/html/2406.19363)).
- **MFA's 2026 SOTA claim** of below 15 ms comes from MFA's own authors ([arXiv 2606.18466](https://arxiv.org/abs/2606.18466)).
- **Caveats:**
  - FA-Bench is vendor-run, has 14 stars, and its MAE leaves out failed utterances.
  - At 30 fps every top aligner is within one frame. **Robustness and outliers matter more than a 10 ms MAE gap.**
  - WhisperX cannot time tokens like "2014." or "£13.60" ([README](https://github.com/m-bain/whisperX#limitations)). Spell numbers out for alignment and display them as digits.
  - stable-ts is **archived**.
  - Traps with non-commercial licences: the MMS weights, ctc-forced-aligner's default model and the CrisperWhisper weights ([HF](https://huggingface.co/MahmoudAshraf/mms-300m-1130-forced-aligner)).
  - Qwen3-FA accepts at most 5 minutes per call and covers 11 languages ([HF](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)).

## 10. Rendering
- **Remotion `@remotion/captions`.** `createTikTokStyleCaptions` takes `combineTokensWithinMilliseconds` and, from v4.0.514, `breakOnSilenceAfterMilliseconds` ([docs](https://www.remotion.dev/docs/captions/create-tiktok-style-captions)).
  - Pages break only at tokens that begin with a space, so Chinese and Japanese need custom paging.
  - Remotion has 60.7k stars and is active.
  - **License for a SaaS:** "Automators", $0.01 per render with a $100/month minimum ([remotion.pro](https://www.remotion.pro/license)).
- **libass (ISC).** Handles karaoke `\k` and `\t` animation, but has **no colour emoji** ([#381](https://github.com/libass/libass/issues/381), still open). Use it for previews only.
- **pycaps (MIT, 217 stars, alpha).** A design reference only. It is not on PyPI, and `pip install pycaps` installs an unrelated package.
- **HDR (corrected).** Remotion renders sRGB only. It converts HLG to SDR automatically and lossily, and its bt2020 flag mislabels SDR output as HDR ([Remotion HDR](https://www.remotion.dev/docs/hdr)). Tone-map iPhone HLG to SDR deliberately before rendering (for example ffmpeg zscale/tonemap or libplacebo) and check skin tones. BT.2408's 203 cd/m² graphics white only matters if Yunicorn ever composites HDR outside Remotion.

## 11. Split between Claude and code
**Claude writes a structured caption plan:**
- corrected wording (names, brands);
- page breaks;
- style;
- the emphasis word (informed by prosody features);
- emoji;
- hook title, callouts and lower third.

Claude does not paraphrase the verbatim text. Deleting text for pace should be a flagged, rare exception, since viewers prefer unreduced text and Deaf advocates press for verbatim captions.

**Code:**
1. Aligns the final text with MFA. It detects failures and implausible durations and falls back to Qwen3-FA, then to the ASR's own times.
2. Snaps timings to frames.
3. Enforces page duration, gaps, characters per page, the safe band and face collisions.
4. Renders in Remotion.

**QA:**
- second-pass ASR word error rate on the caption text;
- checks against interface masks and face boxes;
- Claude viewing a still at each page change with the platform interface overlaid;
- about 200 hand-labelled word boundaries from real Yunicorn takes to confirm aligner accuracy.

## Verified recommendations
- Treat all on-screen text as one structured caption plan from Claude: page breaks, one emphasis word, emoji, hook title, callouts, lower third, style. Code derives all timing and position from aligned words. Replace the unenforceable hard limits (CPS ≤20, ≥0.3 s per word) with ones code can hold: page duration of about 0.5 s or more (punch pages about 0.25 s), merging short pages, 20 characters or fewer per line, 2-frame gaps, a 0.5 s hold only at the end of a speech run, and safe-band and face-collision checks. Monitor CPS and respond by re-paging or limiting speed-ups, not by deleting words.
- Re-time the Claude-corrected transcript with MFA 3.x (code MIT, English model CC-BY-4.0). Detect failures and implausible word durations, then fall back to Qwen3-ForcedAligner (Apache-2.0; 10 languages beyond English; chunk audio into pieces of 5 minutes or less), then to ASR timestamps. All top aligners are within one frame at 30 fps, so optimise for robustness, not the 10 ms MAE gap. Never use raw Whisper timestamps, archived stable-ts, or any MMS, ctc-forced-aligner-default or CrisperWhisper weights (non-commercial). Spell out numbers for alignment and display them as digits.
- Fix placement now. In layout.json, replace the bottom anchor 0.8333, CAPTION_POS_Y_MAX 0.85 and WATERMARK_BOTTOM_PX 336 with a cross-platform band of y 288–1248 and x 65–888 (relaxed organic floor about y 1436). Anchor captions just below the chin, near the face (Kurzhals 2017), at a consistent spot, moving them only on collision. Get face boxes from MediaPipe (Apache-2.0), not InsightFace (non-commercial models). Confirm the band against screenshots of the live TikTok, Reels and Shorts apps, since the TikTok and Shorts pixel values are third-party readings.
- Default style: one line of 2–4 words (one-line subtitles pull less attention from the picture in vertical video; Li 2026, n=211), split at phrase boundaries using Netflix/DCMP rules. Heavy sans at weight 800–900 (Montserrat, Inter, Anton; OFL), white with a paint-order stroke plus shadow, all caps only for short pages. At most one accent keyword per page, current-word scale 1.1× or less, and single-word punch pages only for hook, number or punchline beats. Offer a mixed-case sentence style, and A/B test styles, because no retention study exists.
- Pick emphasis words using meaning plus measured prosody. Code computes per-word loudness, pitch and duration z-scores from the aligned audio and passes them to Claude as text, since Claude cannot hear. Express emphasis through weight, size or colour, not motion (Caption Royale CHI 2024; CHI 2023).
- Cap animation at one entry pop per page (150–200 ms), with no looping bounce, shake or strobe and flashes at 3 per second or fewer. Highlights may lead speech by 0–1 frame and never lag (by analogy with ITU-R BT.1359).
- Keep caption text verbatim by default. On sped-up sections, re-page into larger pages or limit speed-up to about 1.15× on dense speech, instead of paraphrasing. Viewers who understand the audio prefer unreduced text (Szarkowska 2018), and Deaf advocates press for verbatim captions. Allow filler removal only where the audio was also cut.
- Add a hook title card for the first ~3 s (7 words or fewer, upper band y 288–600), and trial a 'text-behind-subject' version using SAM 2, BiRefNet or MediaPipe mattes composited in Remotion. Avoid GPL or non-commercial matting models (RVM, MatAnyone). Callouts and lower thirds fire only on transcript triggers, with one text focal point at a time. This rests on practitioner evidence and needs A/B testing.
- Keep Remotion (@remotion/captions plus layout-utils) as the final renderer and budget for the Automators license ($0.01 per render, $100/month minimum). Use libass only for previews. Build custom paging for languages written without spaces. The server should emit explicit page text, line breaks and font size so the iOS editor reproduces captions exactly instead of re-wrapping them.
- Tone-map iPhone HLG to SDR explicitly before Remotion, with a controlled operator and a skin-tone check. Do not rely on Remotion's automatic lossy conversion or its bt2020 flag, which mislabels SDR as HDR. Remotion cannot output real HDR.
- Build a caption QA gate: second-pass ASR word error rate on caption text, per-page duration and character checks, text boxes checked against platform interface masks and face boxes, and Claude viewing stills at each page change with the interface overlaid. Hand-label about 200 word boundaries from real Yunicorn takes to confirm aligner choice, because FA-Bench is run by a competing aligner vendor and MFA's 2026 SOTA claim is self-reported.

## Corrections by fact-checker
- [confirmed] FA-Bench Buckeye word-boundary error: MFA 3.4 21.3 ms (F1@20ms 0.68), Qwen3-FA 33.8 ms, MMS-FA 37.1, WhisperX 41.7, CrisperWhisper 43.1, stable-ts 71.2, Whisper large-v3 raw 122.7, Parakeet→MFA 20.3, Scribe v2 34.8. → All numbers match the Buckeye TEST split. Missing context: (a) MAE leaves out utterances a system returned nothing for, which flatters MFA a little. (b) The benchmark owner's own API aligner, Olign 1.0, is at or near the top (19.4 ms dev / 22.5 ms test, F1 0.744), so this is a vendor-run board with a conflict of interest. (c) The researcher's table omits Charsiu (28.1 ms, MIT, but last commit 2022) and Google Chirp 2 (26.9 ms, the best raw API timestamps). (d) At Yunicorn's 30 fps, 21 ms versus 34 ms is under half a frame. https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md ; https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md
- [confirmed] MFA returned nothing for 354 of 4,513 utterances under added noise. → That count is for the additive 'noise' condition only. On the test split MFA 3.4 failed 8 clean, 11 reverb, 170 music and 30 babble utterances out of 4,513. Clean audio fails only about 0.2% of the time, but a fallback is still required. https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md
- [confirmed] Rousso et al., Interspeech 2024: MFA best; on Buckeye within 50 ms, MFA 84.9%, MMS 75.0%, WhisperX 67.4%; on TIMIT, MFA 89.4%, WhisperX 82.4%, MMS 75.7%. →  https://arxiv.org/html/2406.19363
- [corrected] A 2026 paper keeps MFA at state of the art with mean boundary errors below 15 ms. → The paper was written by MFA's own developers (McAuliffe, Gunter, Wagner, Sonderegger; Interspeech 2026). It is self-reported, covers English, Japanese and Korean sets, and does not clearly measure word-level boundaries. Treat it as a claim, not independent confirmation. https://arxiv.org/abs/2606.18466
- [confirmed] Qwen3-ForcedAligner-0.6B: Apache-2.0, 11 languages, at most 5 minutes of audio per call, 32.4 ms self-reported error, about 398k downloads. →  https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- [corrected] stable-ts development is 'indefinitely paused'. → The GitHub repository is now archived (read-only, last push 2026-05-30). It is not merely paused. https://github.com/jianfch/stable-ts
- [corrected] Remotion license: free for up to 3 employees, otherwise a company license. → That is true but incomplete. A SaaS that renders for its users on a server falls under 'Remotion for Automators', which costs $0.01 per render with a $100/month minimum (Enterprise starts at $500/month). Budget for this rather than the seat license. https://www.remotion.pro/license ; https://github.com/remotion-dev/remotion/blob/main/LICENSE.md
- [corrected] For HDR output, keep caption white at 203 cd/m² (75% of the HLG signal, BT.2408). → The BT.2408 figure is correct but does not apply to a Remotion pipeline. Remotion renders in headless Chrome and always produces sRGB/SDR frames. `<OffthreadVideo>` converts HDR input to SDR automatically and lossily, and Remotion's docs call its bt2020 colour-space flag 'a mistake' because it tags SDR content as HDR. Tone-map iPhone HLG to SDR explicitly before Remotion. https://www.remotion.dev/docs/hdr
- [corrected] Meta/Toluna Reels: main message in the first 5 s is 1.7× more likely to rank highly; audio plus visual 1.8×; emoji in DR ads 2.5×. → The outcome is 'likelihood to rank in the top 20% for purchase intent' (1.7× and emoji 2.5×) or 'for brand interest' (1.8×). These are ad-effectiveness survey metrics, not watch time or delivery ranking. The source is 'explorative research' by Toluna with no methodology, sample size or date published. https://www.facebook.com/business/news/reels-creative-strategies
- [confirmed] Meta safe zone: 14% top, 35% bottom, 6% sides. YouTube Shorts: 288/672/48/192 px (third-party reading of Google's template). → Meta's figures are official. The YouTube numbers are third-party readings of Google's PNG template: an 840×960 safe area starting 288 px from the top and 48 px from the left. Third-party TikTok figures vary more widely than stated, with the bottom margin anywhere from about 270 to 484 px, and TikTok itself says the zone changes with post-caption length. https://www.facebook.com/business/ads-guide/update/image/instagram-reels ; https://www.poster.ly/tools/youtube-shorts-safe-zone-checker
- [confirmed] TikTok recommends showing '5–10 words per second'. → TikTok's page says exactly that. It contradicts the reading research, as the researcher noted. https://ads.tiktok.com/help/article/creative-best-practices
- [confirmed] Netflix: 20 CPS for adults, 17 for children, 42 characters per line; minimum 5/6 s and maximum 7 s per event; out-time at least 0.5 s after the audio ends; 2-frame gaps; chain captions when the gap is under 0.5 s. → CPS and the 42-character limit come from the English (USA) guide, not General Requirements. These rules were written for broadcast subtitles that are read. For captions paged in sync with speech, '≤20 CPS' and '≥0.3 s per word' cannot be enforced as hard limits. The researcher's own 184 wpm figure is about 0.33 s per word on average, so any fast talker breaks the rule. The '≥0.5 s after speech' hold only applies at the end of a run of speech. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide ; https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines
- [confirmed] CHI 2024 (McDonnell et al.): 83.3% line-style, 5% one or a few words, 3.3% built word by word; 87% black and white; users want static captions, dislike erratic motion and interface overlap; emoji in moderation, heavy use 'cringey'. → Missing context: the videos were collected in February 2023, before Hormozi-style captions became dominant. Half the 300 videos were Deafness or disability content. The 9 interviewees were all DHH but one. '83.3%' describes movie-like timing, not a preference. Also, 10% of videos animated captions and 34.3% moved them around the screen, and one participant (P7) argued for top placement. https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf
- [confirmed] arXiv 2307.05870: synced keyword highlights preferred for learning but 'too distracting to replace standard captions in everyday viewing' (n=49). → The participants were second-language learners using captions to learn a language. That limits how far the result applies to native-language creator content. https://arxiv.org/abs/2307.05870
- [confirmed] Szarkowska 2018: comprehension was the same at 12, 16 and 20 CPS; slow subtitles were 'unnecessarily long'. → The study also found that viewers who understood the soundtrack (English clips) preferred faster, unreduced subtitles, and slow subtitles caused more re-reading and frustration. This argues against the researcher's recommendation to trim caption text on sped-up sections. https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331
- [corrected] English Reels average 184 wpm (Voqusa, n=5,832). → The 184 wpm figure for Reels rests on 312 samples; TikTok is 172 wpm from 121 samples. The rate divides word count by the transcribed duration, pauses included, so the rate during actual speech is higher. It is vendor telemetry. https://www.voqusa.com/en/blog/video-transcription-statistics-2026
- [confirmed] Remotion TikTok template: 1200 ms pages, 120 px font, 20 px WebkitTextStroke with paintOrder stroke, 5-frame spring (scale 0.8→1, 50 px rise), bottom:350; comments suggest 200 ms for one word and 1500 ms for many words. → Also note that the template's default is a karaoke current-word highlight (#39E508 green) in TheBoldFont. Its box sits at y 1420–1570. https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/Page.tsx
- [confirmed] libass has no colour emoji support (issue #381). → The issue is still open; it was filed in 2020 and last updated 2026-01. https://github.com/libass/libass/issues/381
- [confirmed] ITU-R BT.1359: sound running ahead of the picture is detectable at about 45 ms, sound lagging at about 125 ms. → This is a lip-sync standard (acceptability limits +90/−185 ms). Applying it to text highlights is an analogy, not measured evidence. https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- [confirmed] MMS weights are CC-BY-NC; CrisperWhisper weights are non-commercial. → Also, ctc-forced-aligner's default model (MahmoudAshraf/mms-300m-1130-forced-aligner, about 2.5M downloads) is CC-BY-NC-4.0, so it is easy to adopt by accident. MFA's code is MIT and its English acoustic model is CC-BY-4.0, which allows commercial use with attribution. https://github.com/facebookresearch/fairseq/blob/main/examples/mms/README.md ; https://huggingface.co/MahmoudAshraf/mms-300m-1130-forced-aligner ; https://mfa-models.readthedocs.io/en/latest/acoustic/English/English%20MFA%20acoustic%20model%20v3_1_0.html
- [unverifiable] Auto-generated subtitles near 30 CPS lowered comprehension by 10% (Matthew 2024). → Search summaries confirm the 10% comprehension drop, attributed to how fast consecutive subtitles were shown. The article is paywalled (403), so the '≈30 CPS' figure could not be checked. https://www.tandfonline.com/doi/full/10.1080/1475939X.2024.2433259
- [confirmed] pycaps is MIT, alpha, and not on PyPI. → Watch out: `pip install pycaps` installs an unrelated 'Linux capabilities' package. https://github.com/francozanardi/pycaps ; https://pypi.org/project/pycaps/
- [confirmed] Signalling meta-analysis g=0.53 retention / 0.33 transfer; Arditi & Cho uppercase finding; Benedetto Spritz finding. → The meta-analysis covered 103 studies (n=12,201). Arditi & Cho found capitals read faster at twice the acuity size, with the advantage gone at ten times. Benedetto compared RSVP at 250 wpm with normal reading and found impaired literal comprehension and more visual fatigue. https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581 ; https://pubmed.ncbi.nlm.nih.gov/17675131/ ; https://www.sciencedirect.com/science/article/abs/pii/S0747563214007663

## Missed items added
- Li (2026, Applied Cognitive Psychology) is the first eye-tracking study of subtitles in vertical video. It used webcam eye tracking with n=211 on a TikTok video. Two-line subtitles drew more total fixation time and more revisits, and were skipped less, than one-line subtitles, even after controlling for character count. This is direct evidence for a one-line default that keeps eyes on the face. https://onlinelibrary.wiley.com/doi/10.1002/acp.70262
- Kurzhals et al. (CHI 2017) ran a lab eye-tracking study with n=40. Subtitles that follow the speaker increased fixations on relevant image regions and shortened saccades compared with centre-bottom subtitles. This is peer-reviewed support for placing captions near the face. https://dl.acm.org/doi/10.1145/3025453.3025772
- Caption Royale (CHI 2024, 39 DHH participants) and de Lacerda Pataca (CHI 2023) study typographic cues for emotion and prosody, for example loudness shown as font weight. Readability and minimal distraction decided which styles won. This supports choosing emphasis words from measured vocal emphasis, not only from meaning. https://dl.acm.org/doi/10.1145/3613904.3642258 ; https://dl.acm.org/doi/10.1145/3544548.3581511
- Remotion cannot output HDR, and it converts HLG to SDR automatically and lossily inside OffthreadVideo, so tone mapping has to be controlled upstream. https://www.remotion.dev/docs/hdr
- Remotion's SaaS license is the 'Automators' tier: $0.01 per render with a $100/month minimum. https://www.remotion.pro/license
- createTikTokStyleCaptions starts a new page only at a token that begins with a space, so Chinese and Japanese text needs custom pagination. breakOnSilenceAfterMilliseconds needs v4.0.514 or later. @remotion/layout-utils fillTextBox and measureText (browser-only) allow line-fit checks inside the renderer. https://www.remotion.dev/docs/captions/create-tiktok-style-captions ; https://www.remotion.dev/docs/layout-utils/fill-text-box
- 'Text behind subject' is a depth effect for hook titles, a practitioner trend from 2025 on. It can be built with Apache or MIT segmentation models (MediaPipe, SAM 2, BiRefNet). Avoid RobustVideoMatting (GPL-3.0) and MatAnyone (S-Lab non-commercial). https://www.capcut.com/resource/how-to-put-text-behind-a-person-in-capcut ; https://github.com/facebookresearch/sam2 ; https://github.com/ZhengPeng7/BiRefNet ; https://github.com/pq-yang/MatAnyone/blob/main/LICENSE
- For face and chin detection, MediaPipe Face Landmarker is Apache-2.0. InsightFace's pretrained models are 'non-commercial research purposes only', so do not use them. https://github.com/google-ai-edge/mediapipe ; https://github.com/deepinsight/insightface
- FA-Bench also shows that Google Chirp 2 has the best raw API word timestamps (26.9 ms, F1 0.44), and that the benchmark owner's own aligner (Olign) tops the chart, which is a conflict of interest. https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md
- Yunicorn's layout.json has more unsafe values than the bottom anchor. CAPTION_POS_Y_MAX is 0.85 (y≈1632) and WATERMARK_BOTTOM_PX is 336, both inside every platform's bottom interface zone. /Users/home/Marque/render/src/layout.json
- Deaf advocates press for verbatim captions. CHI 2024 found 18.3% of sampled videos deliberately non-verbatim, and caption users' views on that were mixed. This bears on any plan to have Claude trim or paraphrase caption text. https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf

## Tools
- Montreal Forced Aligner 3.x [forced alignment (primary); MIT] https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner — Buckeye word error 21.3 ms, F1@20ms 0.68 (FA-Bench 2026-09). TIMIT 89.4% of boundaries within 50 ms, best in the peer-reviewed Rousso et al. Interspeech 2024. A 2026 paper reports mean boundary errors under 15 ms on its benchmark sets. 1.9k stars, pushed 2026-08. Needs a pronunciation dictionary plus G2P for out-of-vocabulary words (names, slang). Returned nothing for about 8% of utterances under added noise, so it needs a fallback.
- Qwen3-ForcedAligner-0.6B [forced alignment (multilingual fallback); Apache-2.0] https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B — Self-reported 32.4 ms average error on human-labelled sets. Independent FA-Bench Buckeye result 33.8 ms. 11 languages. About 398k downloads. At most 5 minutes of audio per call, so chunk 10-minute takes.
- NVIDIA Parakeet-TDT-0.6B-v3 [ASR front end for the MFA pipeline; CC-BY-4.0] https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 — Parakeet → MFA 3.4 gives 20.3 ms error on Buckeye, the best open ASR + aligner pipeline in FA-Bench. About 561k downloads. Its own timestamps are weaker (80.7 ms), so always re-align. Word accuracy and verbatim fillers are covered by another track.
- ElevenLabs Scribe v2 [commercial ASR with timestamps; Commercial API] https://elevenlabs.io/speech-to-text — 34.8 ms error on Buckeye but F1@20ms only 0.11 (FA-Bench). Disfluency F1 79.2, the best closed system on Nyra's vendor benchmark. Acceptable fallback timing. Better used for words and fillers, then re-aligned with MFA.
- WhisperX [ASR + CTC alignment; BSD-2-Clause] https://github.com/m-bain/whisperX — 41.7 ms error on Buckeye. TIMIT 82.4% within 50 ms (Rousso 2024). 24k stars, active. Cannot align tokens like '2014.' or '£13.60'. Weaker than MFA; keep only as a fallback.
- torchaudio forced_align / ctc-forced-aligner [CTC alignment utility; BSD-2-Clause (code); MMS weights CC-BY-NC-4.0] https://github.com/MahmoudAshraf97/ctc-forced-aligner — MMS-FA 37.1 ms on Buckeye; covers 1,100+ languages. MMS weights are non-commercial, so use it only with commercially licensed CTC models. torchaudio is in maintenance mode.
- stable-ts [Whisper timestamp stabilization; MIT] https://github.com/jianfch/stable-ts — 71.2 ms on Buckeye (FA-Bench). The repo says development is 'indefinitely paused'. Do not adopt. Its regrouping heuristics are a useful reference only.
- CrisperWhisper 2.0 [verbatim ASR with timestamps; Code MIT; weights non-commercial (commercial license available)] https://github.com/nyrahealth/CrisperWhisper — Self-reported 29.6 ms TIMIT boundary error and disfluency F1 87.8. FA-Bench Buckeye 43.1 ms (TIMIT overlap caveat). Consider only if Yunicorn licenses it.
- Remotion + @remotion/captions + template-tiktok [caption rendering (primary); Remotion License (free for 3 or fewer employees; company license otherwise)] https://www.remotion.dev/docs/captions/create-tiktok-style-captions — 60.7k stars and daily commits. createTikTokStyleCaptions pagination; fitText; paint-order stroke. Already Yunicorn's renderer. The template's default bottom:350 position violates platform safe zones; do not copy it.
- libass (via ffmpeg ass/subtitles filter) [caption rendering (preview/fallback); ISC] https://github.com/libass/libass — Industry-standard ASS renderer. Karaoke highlighting (\k/\kf) and \t animations. No colour emoji (issue #381 open since years, updated 2026-01). Weaker layout control than CSS.
- pycaps / tscaps [CSS caption templating reference; MIT (pycaps; tscaps engine MIT, app AGPL-3.0)] https://github.com/francozanardi/pycaps — 217 stars, alpha, not on PyPI. Renders CSS through Playwright; supports word tagging and emoji/sound-effect insertion. Borrow ideas only: CSS states like .word-being-narrated and tag-based styling.
- Noto Color Emoji / Twemoji [emoji assets; OFL-1.1 (Noto fonts); Twemoji graphics CC-BY 4.0, code MIT] https://github.com/googlefonts/noto-emoji — Noto: 5k stars, active 2026-09. Twemoji fork (jdecked): 1.9k stars. Never use Apple Color Emoji glyphs in renders.
- Montserrat / Inter / Anton (Google Fonts); TheBoldFont [caption fonts; OFL-1.1; TheBoldFont free for any use] https://github.com/remotion-dev/template-tiktok/blob/main/public/theboldfont-license.rtf — Montserrat Black and TheBoldFont/Anton are the fonts practitioners cite for Hormozi/Submagic styles. TheBoldFont ships in Remotion's template. The free TheBoldFont is uppercase-only with limited glyphs, so accented or non-Latin text shows missing-glyph boxes.
- FA-Bench [alignment benchmark harness; PolyForm Noncommercial 1.0.0] https://github.com/olewave/fa-bench — 30 systems on human-labelled TIMIT and Buckeye, clean and noisy. Reproducible, but run by a vendor (Olewave) and only 14 stars. Use its methodology to build an internal hand-labelled check; commercial use of the code needs a license.
