# Captions and on-screen text

Load this file in finishing, once picture is locked and the kept word IDs, segment timing, framing transforms and inserts are known, and again whenever a chat edit changes wording, timing or framing. It covers burned-in captions (paging, typography, emphasis, placement, timing, animation, emoji, verbatim policy) and all other on-screen text: hook titles, callouts, numbered lists, lower thirds and how long each stays up. Captions exist so the video works with the sound off and for Deaf and hard-of-hearing (DHH) viewers, not as decoration. The best caption track is read without being noticed, while the eyes stay on the speaker.

## Principles

1. **Text serves the face.** *Why:* the face carries the message; text that is long, far away, moving or colourful pulls the eye off it. Murch counts eye-trace among his six cut criteria, and captions near the face shorten the eye's trip.
2. **One line, one phrase, one glance.** *Why:* in vertical video, two-line subtitles drew more fixation time and more revisits than one-line subtitles, and the fixation-time gap survived controlling for character count (Li 2026).
3. **Stay put.** *Why:* DHH viewers want captions "static, right there, simple, clean", and captions that jump make them "look all around" (McDonnell, CHI 2024). Move only to avoid a collision.
4. **Timing comes from measured audio, never from the language model.** *Why:* the Director cannot hear; raw Whisper word times are off by about 120 ms on average, and by analogy with lip-sync thresholds (ITU-R BT.1359), text that lags the voice is noticed sooner than text that leads it.
5. **Emphasis works only when rare.** *Why:* signalling helps retention (g = 0.53), but language learners found synced keyword highlights "too distracting" for everyday viewing, and every styled caption in Caption Royale scored below plain captions on legibility. A colour on every page signals nothing.
6. **Motion is a tax.** *Why:* each new page is already an abrupt onset, which captures attention on its own (Yantis & Jonides 1984). Bounce, shake and per-word pops add more.
7. **Verbatim words, clean presentation.** *Why:* viewers who hear the audio prefer unreduced text (Szarkowska 2018) and DHH advocates push for verbatim, but hesitation sounds carry no meaning and clutter a page.
8. **Every other text element needs a job and a trigger word.** *Why:* over-editing is the main failure (a pro made 1 cut where the old engine made 19 [I]); text load follows the same pattern.
9. **"None" is valid** for hook titles, callouts, lists, lower thirds, emoji and accent colour. Captions are on by default.

## Defaults and ranges

These are starting priors, not laws. No peer-reviewed study compares caption styles on organic short-form retention. On 6,606 Douyin brand videos, subtitles even correlated *negatively* with comments and shares. Style is something to A/B test.

| Parameter | Starting prior | Tier | Source |
|---|---|---|---|
| Captions on | Default on. 69% watch with sound off in public (self-report survey) | [A] | [Verizon/Publicis 2019](https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/) |
| | 80.2% of 13.5M clips made in OpusClip's own tool are captioned, 78.6% animated. This is tool output and adoption, not performance | [V] | [OpusClip 2026](https://www.opus.pro/research/best-caption-strategy-short-form) |
| Page size | 2–4 words; 1-word pages only for hook, numbers, punchlines. Submagic's Hormozi recipe: caps, at most 15 characters a line, 2 lines, 4–6 words. Remotion's template pages every 1.2 s (3–4 words at 180 wpm) | [X], [V] | [Submagic](https://www.submagic.co/blog/how-to-make-alex-hormozi-captions), [Remotion template](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/index.tsx) |
| Lines | 1 (2 only in the calm sentence style). BBC allows up to 3 in 9:16, but it has no platform UI to dodge | [L], [P] | [Li 2026, n=211](https://onlinelibrary.wiley.com/doi/10.1002/acp.70262), [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Line length | About 20 characters or fewer; measured width is the real limit. BBC: 25 characters fill 90% of 9:16 width (972 px) at its line height of at most 4.5% (≈86 px), so a ≈690 px centred line holds about 18 | [P] | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Reading speed | Comprehension equal at 12, 16 and 20 CPS; slow subtitles frustrate | [L] | [Szarkowska 2018](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331) |
| | 20 CPS adult cap (Netflix); 160–180 wpm, about 0.3 s per word (BBC). "Comfortable" at about 145 wpm, little trouble until about 170 wpm (n=578) | [P], [L] | [Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide), [Jensema 1998](https://pubmed.ncbi.nlm.nih.gov/9842059/) |
| Creator speech rate | 184 wpm Reels (n=312), 172 wpm TikTok (n=121), word count over transcribed duration; about 16–18 CPS | [V] | [Voqusa](https://www.voqusa.com/en/blog/video-transcription-statistics-2026) |
| Page duration | At least 0.5 s (punch pages 0.25 s); merge shorter pages. Netflix's floor for standalone subtitles is 20 frames (0.83 s at 24 fps), too long for speech-synced pages | [X], [P] | [Netflix timing](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines) |
| Gap between pages | 2 frames; close gaps under 0.5 s | [P] | [Netflix timing](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines) |
| Sync | Page-in and word highlight lead the measured onset by about 70–100 ms (2–3 frames at 30 fps, 4–6 at 60); never lag. Keep the lead under 125 ms: text ahead of the voice is like sound behind picture, detectable at −125 ms, while text behind the voice is detectable at +45 ms. Broadcast norms put subtitles on the onset (Netflix: within 1–2 frames); the small lead only absorbs aligner error | [X] on [L], [P] | [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf), [Netflix timing](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines) |
| Word timing | Forced aligner (MFA): 84.9% of boundaries within 50 ms on conversational speech; 21 ms mean error vs 123 ms for raw Whisper (vendor-run benchmark) | [L], [V] | [Rousso 2024](https://arxiv.org/html/2406.19363), [FA-Bench aligners](https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md), [FA-Bench ASR](https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md) |
| End-of-run hold | 0.3–0.5 s after the last word; clear within 0.3 s on a deliberate dramatic pause. Netflix prefers at least 0.5 s; BBC clears roughly with the speech, because text left up gets re-read | [X] on [P] | [Netflix timing](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines), [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Typeface | Heavy sans, weight 700–900 (Montserrat, Inter, TikTok Sans, Anton; OFL). DCMP asks for white, sans serif, *medium* weight, drop or rim shadow; short-form runs heavier to survive small screens and busy backgrounds, so drop to 600–700 for the sentence style | [P] | [DCMP](https://dcmp.org/captioningkey/print) |
| Size at 1080×1920 | 64–96 px phrase pages, 110–140 px punch pages (Remotion template 120 px; BBC 9:16 line height 4.5%, about 86 px) | [X] | [Remotion template](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/Page.tsx) |
| Outline | Paint-order stroke about 8–12% of font size plus soft shadow; translucent box on busy b-roll | [P] | [Remotion docs](https://www.remotion.dev/docs/captions/displaying) |
| Case | Mixed case for 3+ word pages and calm styles; caps fine on short, large pages. Legibility does not decide this: caps read as fast as mixed case at large sizes, and faster near acuity. Tone does: caps read as shouting | [L], [P] | [Arditi & Cho 2007](https://pubmed.ncbi.nlm.nih.gov/17675131/), [DCMP](https://dcmp.org/captioningkey/print) |
| Accent | At most 1 word per page, on about 25% of pages or fewer (Yunicorn prior). BBC marks stress sparingly: "Do not overuse this device". For conveying emotion, colour plus weight or colour plus size won with 39 DHH participants; weight disrupted reading less than size | [X], [P], [L] | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/), [Caption Royale, CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642258) |
| Entry animation | At most 1 per page, 100–200 ms, scale from 0.9 or more (gentler than the Remotion template's 0.8→1 plus a 50 px rise); current word at most 1.1×; no loops or shake; at most 3 flashes/s | [X] on [P] | [Remotion SubtitlePage](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/SubtitlePage.tsx), [WCAG 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html) |
| Emoji | 0 by default; in playful styles at most about 1 per 10 s, as a tone marker. Ads: emoji 2.5× likelier top-20% purchase intent (survey, no method) | [L], [A] | [McDonnell 2024](https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf), [Meta/Toluna](https://www.facebook.com/business/news/reels-creative-strategies) |
| Position | Caption top edge 40–120 px below the chin landmark, after transforms (Yunicorn prior). Speaker-following captions raise fixations on relevant regions (n=40); BBC places 9:16 subtitles "a little higher up" because faces sit high | [X], [L], [P] | [Kurzhals 2017](https://dl.acm.org/doi/10.1145/3025453.3025772), [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Safe band | Reels (official, ads spec): keep about 14% top, 35% bottom, 6% sides clear; the bottom figure includes the ad CTA, so organic is looser. TikTok: zone varies with ad-caption length, templates only | [A] | [Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels), [TikTok](https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads?lang=en) |
| | Shorts: Google's official vertical-ads overlay gives 288 top / 672 bottom / 48 left / 192 right, and it supersedes third-party maps (poster.ly lists 380 / 380 / 60 / 120). TikTok, third-party maps only: bottom ~250–480, right ~120–180. Master band (`platforms.md`): x 65–888, y 288–1248 (relaxed organic floor about 1436), centred lines at most about 690 px wide. Confirm on screenshots of the live apps | [A] Shorts; [V] TikTok; [X] band | [Google overlay](https://services.google.com/fh/files/misc/youtubesafezoneoverlay_vertical_final.png), [poster.ly](https://www.poster.ly/tools/youtube-shorts-safe-zone-checker), [Zeely](https://zeely.ai/blog/tiktok-safe-zones/) |
| Static text dwell | At least 0.3 s per word (BBC's subtitle minimum) plus a 1 s margin for labels (our margin); silent reading averages 238 wpm | [P] rate + [X] margin; [L] reading | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/), [Brysbaert 2019](https://www.researchgate.net/publication/332380784_How_many_words_do_we_read_per_minute_A_review_and_meta-analysis_of_reading_rate) |
| Hook title | 7 words or fewer, on at frame 0, held for the static dwell (about 2–3.5 s). TikTok's ad guidance: proposition within the first 3 s. Frame 0 is the default cover on TikTok and Reels, so it must read as a still | [X], [A] | [TikTok creative](https://ads.tiktok.com/help/article/creative-best-practices), `platforms.md` |
| Callouts | 1–3 words beside the referent, at most 1 per 5–10 s (Yunicorn prior). 2–3 printed words beside a diagram improved retention (d = 0.47–0.70) | [X], [L] | [Mayer & Johnson 2008](https://psycnet.apa.org/record/2008-05694-008) |
| Lower third | Once, on self-introduction, 3–5 s (name plus role is 4–6 words, so the dwell rule gives 2.2–2.8 s minimum) | [X] | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |

TikTok's ad guide recommends "5–10 words per second when using text". That is 300–600 wpm, two to three times any speech rate and above silent reading speed, and it comes with no evidence. Ignore it for captions.

## How to decide

1. **Read the brief:** style profile, measured energy, wpm, creator memory (creator presets override these defaults). Pick a caption style:
   - **Phrase** (default): 2–4 words, one line, optional subtle current-word state.
   - **Sentence** (calm teaching, storytime, emotional): mixed case, up to 2 lines, bottom-heavy, no karaoke.
   - **Beat** (high-energy hot takes): 1–3 words in caps, more one-word pages. Not the default for anyone: every page change is an onset.
2. **Write the text from the kept word IDs**, after picture lock.
   - Fix names and brands. Display numbers as digits ("$60"); the aligner keeps the spoken form ("sixty dollars").
   - Clean verbatim: drop non-lexical hesitations ("um", "uh") even if they stay in the audio, unless the hesitation *is* the moment. Keep every lexical word, "like" and "you know" included. Never paraphrase. Netflix includes "um" only when it matters to mood or character and lets it go for reading speed, while keeping text "as close to the audio as possible"; DCMP's own editing example drops "uh" and "um". BBC and Deaf advocates lean fully verbatim, so a creator preset may choose strict verbatim.
   - Mask profanity only on the creator's request (often to dodge moderation). DCMP says caption it verbatim; DHH interviewees saw masking as poorer access but accepted it as platform culture (McDonnell 2024).
   - Non-speech sounds: none by default. When a sound carries the beat (a ding the creator reacts to, a laugh the joke needs), a short tag such as `[phone buzzes]` is valid. Standard practice captions only the sounds needed to understand or enjoy the video, which matched DHH interviewees' preference; TikTok creators rarely caption sounds at all (DCMP; McDonnell 2024).
3. **Page the word IDs.**
   - Break at punctuation, kept seams, and gaps of about 300 ms or more.
   - Avoid splitting article+noun, adjective+noun, first+last name, pronoun+verb or auxiliary+verb (Netflix, DCMP); syntax-blind breaks raise cognitive load without hurting comprehension ([Gerber-Morón 2018](https://bop.unibe.ch/JEMR/article/view/4267)), so a forced split is a cost, not a failure.
   - Keep setup and punchline on separate pages. Text shown before its word is spoken spoils the beat (BBC: "do not pre-empt an effect"; Netflix: "avoid revealing punchlines").
4. **Choose accents.** Candidates come from meaning (numbers, contrast words, negations, the payoff noun). Keep only those that code's prosody features support (loudness, pitch or duration z-score about +1.5 or more), since you cannot hear the audio. If a punch-in, SFX or card already lands on that word, skip the accent: one device per beat. One accent colour per creator, meaning only "emphasis"; any current-word state uses weight or brightness, never that colour. Zero accents in a video is a fine result.
5. **Time the pages** on the output timeline, after speed changes.
   - In-time = first word's aligned onset (snapped to an acoustic onset within ±80 ms) minus the 70–100 ms lead. Out-time = next page's in-time minus 2 frames.
   - Seams: page changes that coincide with cuts are less tiring (BBC), and Netflix snaps timing to shot changes. If a page change falls within about 4 frames *after* a visible seam (jump cut, punch, insert in or out), move it onto the seam; two changes a few frames apart read as a flicker. Never pull a page earlier than the lead limit to reach a seam.
   - Hold 0.3–0.5 s at the end of a speech run; on a deliberate dramatic pause, clear the text so the silence reads as silence.
   - If a sped-up section exceeds about 20 CPS, re-page larger or reduce the speed. Never delete words to fit.
6. **Place** using per-frame face landmarks *after* punch-ins and reframes.
   - Anchor once per framing section, with hysteresis. Move only on a collision with eyes or mouth, the UI mask, OCR-detected text in b-roll, or another overlay.
   - When nothing fits: smaller text, then the lower band, then text behind the subject (matte), then fold the title into the captions.
   - When an insert's own text collides, re-frame the insert rather than move the captions.
7. **Add other text only with a job and a trigger word ID.** Hook title: only if the opening needs a label a muted scroller grasps in 2 s. Callout: a number or term to remember, next to what it names. Numbered list: when the speaker enumerates. Lower third: when the creator introduces themselves and the name matters. At most one *changing* text element at a time; a static title beside changing captions is fine.
8. **Critique the render:** watch muted, check a still at every page change with the TikTok, Reels and Shorts UI overlaid, and check timing against measured onsets in code, not by eye. Diff the caption text against a second ASR pass: caption users skipped videos whose captions were badly wrong (McDonnell 2024).

## When to break it

- **Comedy.** Put the punchline word on its own page, exactly on the word, even if that makes a 0.25 s page.
- **Very fast talkers (above 200 wpm).** Use larger two-line pages rather than faster flicker.
- **Explicit style requests** such as the "Hormozi package" (caps, coloured and enlarged keywords, two short lines). Honour them, but keep the safe band, a clear mouth and the no-lag rule.
- **Multiple speakers.** Colour may identify speakers (BBC practice). Then emphasis moves to weight, because colour cannot carry two meanings.
- **Screen recordings or text-heavy b-roll.** Move the captions to the clear band for the whole insert, not word by word.
- **Captions off.** When on-screen text *is* the content, or when the creator ships a caption-free brand look. In that case export a sidecar SRT where the platform accepts one.
- **Platform auto-captions.** Whenever captions are burned in, tell the creator to leave the platform's auto-captions off for that post, or the viewer gets double text.

## Worked example

Creator "Maya", personal-finance talking head: 38 s, calm-to-medium energy, 176 wpm, chin at y 940 in the locked framing. Profile: educational. Style: Phrase, mixed case, Inter Black 84 px, white, 9 px stroke plus soft shadow, yellow accent. Caption top edge at y 1020 (80 px below the chin).

| Spoken (kept audio) | Pages | Decisions and why |
|---|---|---|
| "Most people fail at budgeting for one reason." | `Most people fail` / `at budgeting` / `for one reason.` | "one" has z-scores of +2.1 loudness and +1.8 duration and carries the hook's promise, so it gets the accent. A hook title was considered, but the head spans y 250–700 and a title in the upper band would sit on the forehead. Folded into the captions; the first page is up at frame 0. |
| "They track what they spent, not what they're about to spend." | `They track` / `what they spent,` / `not what they're` / `about to spend.` | A contrast pair. Accenting both halves was considered; only "spend" (the new idea) is accented, because two accents within 2 s dilute each other. |
| "…Dinner with Sam, sixty dollars. Gas, forty." | `Dinner with Sam,` / `$60.` / `Gas, $40.` | Digits for display, words for alignment. `$60.` becomes a one-word number beat. No callout card: the captions already carry the numbers. A calendar-screenshot insert has burned-in text at y 1000–1180; OCR flags the collision, so the insert is re-cropped and the captions stay put. |
| "By Friday I've spent, um, basically exactly what I planned." | `By Friday` / `I've spent` / `basically exactly` / `what I planned.` | The audio keeps "um" because the beat sells the payoff; the caption drops it (clean verbatim). The page break at the hesitation lands `basically exactly` on the word, "exactly" accented. |
| "Try it for one week." | `Try it` / `for one week.` | Held 0.4 s after the last word; the video ends within 0.5 s. |

A 1.15× punch-in at 0:14 moves the chin down to y 975. The captions stay at y 1020: the 45 px gap is still inside the 40–120 px prior, so hysteresis holds them. Totals: 5 accents across 29 pages, no emoji, no lower third (no self-introduction), no list, peak 18.4 CPS. The muted watch passes.

## Anti-patterns

- Captions at y≈1600, the old Yunicorn bottom anchor, where they sit under the TikTok and Reels caption and buttons [I].
- Word-by-word pop with bounce on every word, a whoosh per word, or shake.
- Colour on every other word, or one colour doing both karaoke highlight and emphasis.
- Captions over the mouth or eyes, or jumping to wherever the background has contrast.
- Three-line paragraphs, and breaks like "the / budget" or "Maya / Chen".
- Captions that lag the voice (raw Whisper times), or that hang over a pause or a scene change. A page change a few frames after a jump cut.
- Paraphrased or "tidied" captions that disagree with the lips. Misspelled names and brands.
- A hook title, captions, a callout and emoji all changing at once.
- Titles flashed for 1 s. Lower thirds shown repeatedly.
- Emoji that replace words, or Apple emoji glyphs (render Noto or Twemoji).
- The same template on every creator, whatever their energy.

## Critic questions

Every "yes" is a pass.

1. Muted, can you follow the whole video from the text alone?
2. Can every page be read in one glance (one line, or two in the sentence style) before it disappears?
3. With the TikTok, Reels and Shorts UI overlaid, is all text fully visible?
4. Does all text stay clear of the eyes and mouth throughout?
5. Do the captions stay in one place except where something forced a move?
6. Does every page appear with, or a hair before, its first word, and never after it?
7. Does the caption text match the words heard, with names and brands spelled correctly?
8. Is every punchline or payoff word kept off screen until it is spoken?
9. Are accents rare enough (at most 1 per page, most pages plain) that each one means something?
10. Is there at most one changing text element on screen at any moment?
11. Does every hook title, card, list item and lower third stay up long enough to read at a relaxed pace?
12. Did your eyes stay on the speaker rather than on animation, colour or emoji?
13. Was "none" weighed for every title, card, emoji and accent, and does each survivor have a named job and trigger word?

## Sources

- Li 2026, one-line vs two-line subtitles in vertical video: https://onlinelibrary.wiley.com/doi/10.1002/acp.70262
- McDonnell et al., CHI 2024, captioning practices on TikTok: https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf
- de Lacerda Pataca et al., Caption Royale, CHI 2024: https://dl.acm.org/doi/10.1145/3613904.3642258
- Draxler et al. 2023, keyword highlights "useful but distracting" (language learners, n=49): https://arxiv.org/abs/2307.05870
- Schneider et al. 2018, signalling meta-analysis: https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581
- Mayer & Johnson 2008, revising the redundancy principle: https://psycnet.apa.org/record/2008-05694-008
- Szarkowska & Gerber-Morón 2018, fast subtitles: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331
- Gerber-Morón et al. 2018, line breaks and cognitive load: https://bop.unibe.ch/JEMR/article/view/4267
- Jensema 1998, caption speed: https://pubmed.ncbi.nlm.nih.gov/9842059/
- Brysbaert 2019, reading-rate meta-analysis: https://www.researchgate.net/publication/332380784_How_many_words_do_we_read_per_minute_A_review_and_meta-analysis_of_reading_rate
- Kurzhals et al., CHI 2017, speaker-following subtitles: https://dl.acm.org/doi/10.1145/3025453.3025772
- Arditi & Cho 2007, letter case and text legibility: https://pubmed.ncbi.nlm.nih.gov/17675131/
- Yantis & Jonides 1984, abrupt onsets capture attention: https://pubmed.ncbi.nlm.nih.gov/6238122/
- ITU-R BT.1359, audio/video sync: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- Rousso et al., Interspeech 2024, forced aligners: https://arxiv.org/html/2406.19363
- FA-Bench, Buckeye word boundaries (vendor-run): https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md
- FA-Bench, raw ASR timestamps incl. Whisper large-v3 (vendor-run): https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md
- BBC Subtitle Guidelines: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
- Netflix English (USA) Timed Text Style Guide: https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide
- Netflix subtitle timing guidelines: https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines
- DCMP Captioning Key: https://dcmp.org/captioningkey/print
- WCAG 2.2, three flashes: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- Meta Reels ads guide, safe zones: https://www.facebook.com/business/ads-guide/update/image/instagram-reels
- Meta, Reels creative strategies: https://www.facebook.com/business/news/reels-creative-strategies
- TikTok in-feed ads specs, safe zone: https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads?lang=en
- TikTok creative best practices: https://ads.tiktok.com/help/article/creative-best-practices
- YouTube Shorts safe-zone reading (third party): https://www.poster.ly/tools/youtube-shorts-safe-zone-checker
- TikTok safe zones (third party): https://zeely.ai/blog/tiktok-safe-zones/
- Verizon/Publicis 2019 sound-off survey: https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/
- OpusClip caption adoption research: https://www.opus.pro/research/best-caption-strategy-short-form
- Douyin subtitles and engagement, Int J Advertising 2026: https://www.tandfonline.com/doi/full/10.1080/02650487.2026.2670858
- Voqusa creator speech-rate data: https://www.voqusa.com/en/blog/video-transcription-statistics-2026
- Submagic, Hormozi caption style: https://www.submagic.co/blog/how-to-make-alex-hormozi-captions
- Remotion TikTok template (index.tsx, 1.2 s paging): https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/index.tsx
- Remotion TikTok template (Page.tsx): https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/Page.tsx
- Remotion TikTok template (SubtitlePage.tsx): https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/SubtitlePage.tsx
- Remotion, displaying captions: https://www.remotion.dev/docs/captions/displaying
- Walter Murch, rule of six: https://en.wikipedia.org/wiki/Walter_Murch
