# B-roll sourcing, ranking and licensing

Load this file after `broll.md` has decided that a beat gets an insert and named its job and mode, and before anything is searched, captured, generated or placed. `broll.md` owns whether and when to cut away. This file owns what goes on screen and whether Yunicorn may render it: sources, licences, queries, ranking, conform and refusal. It is editorial guidance, not legal advice. The licence validator and counsel settle edge cases, and provider terms are re-checked at each `evidence.md` expiry.

## Principles

1. **Route the beat to an asset class before searching.** *Why:* no search fixes a class error. A number is a card, a claim needs evidence, and only mood or metaphor is a stock question.
2. **Prefer the most real source that can do the job.** Usual order: the creator's own media or pickups, then real captures, then designed cards, then licensed stock, then AI. *Why:* real material carries trust, and each step down adds licence, label or originality risk.
3. **A licence is a gate, not a score.** *Why:* a perfect clip that cannot legally be rendered into a monetised export is worth nothing, and a claim or takedown costs more than any insert earns.
4. **Evidence must be real.** Capture, crop and highlight. Never fabricate, edit or restage a post, review, headline or dashboard, and never design a card that mimics a real account. *Why:* one exposed fake discredits the creator, and it is impersonation.
5. **Describe what the camera sees, not what the idea means.** *Why:* stock search rewards a concrete keyword plus shot descriptors such as aerial, timelapse or handheld ([Pond5](https://blog.pond5.com/26162-5-game-changing-stock-media-search-tips/)) [V]. Abstract queries return clichés.
6. **Judge each candidate alone, after conform, across the range you will use, against a bar written before you look.** *Why:* LLM judges are swayed by order; reordering alone let a weaker model beat a stronger one on 66 of 80 queries ([Wang 2023](https://arxiv.org/abs/2305.17926)). A forced choice always picks something, and a thumbnail misses the logo that appears at second 2.
7. **Inserts belong to the A-roll's camera world, and to each other.** *Why:* a grey HDR mismatch, judder or a colour jump reads as cheap even when the subject is right, and so does cinematic 24 fps stock beside bright gimbal clips ([String Labs](https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/)) [P]. For two or more stock clips, try one contributor's series or visual-similarity search ([Pond5](https://blog.pond5.com/26162-5-game-changing-stock-media-search-tips/)).
8. **Photoreal AI is the last resort, and it is always flagged.** *Why:* the label costs about 7–8% of likes, mostly by signalling less creator effort and weakening the viewer's bond with the creator ([JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493)) [L]. Platforms auto-label it, and EU deepfake disclosure has applied since 2 August 2026.
9. **Refusing is a valid result, and a good find is not a reason to insert.** *Why:* interesting but irrelevant details lower recall and transfer (g = −0.33, [Sundararajan & Adesope 2020](https://link.springer.com/article/10.1007/s10648-020-09522-4)). Removing extraneous material helped in 18 of 19 tests (median d = 0.86, Mayer 2021 via [Mayer 2023](https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf)). If the best candidate merely fits, the beat is usually better on the face or a punch-in.

## Defaults and ranges

Tiers are as in `SKILL.md`. Here [A] also covers a provider's own terms and API docs. Internal files live in the Yunicorn Studio design inputs.

| Parameter | Starting prior | Tier | Source |
|---|---|---|---|
| Queries per beat | 5–10 concrete variants | [I]; expansion [L] (text retrieval) | research_broll.md; [Jagerman 2023](https://arxiv.org/abs/2305.03653) |
| Funnel | 50–200 candidates → embedding top ~20 → code gates → Claude judges ≤8 | [I] | design_toolchain.md, critique_simplicity.md |
| Frames embedded / gate sampling | 3–5 per candidate / every 0.25–0.5 s of the used range | [I] | critique_feasibility.md |
| Accept bar | every gate passes; correct subject with no look-alike; every rubric item ≥4/5 | [I], calibrate | design_doctrine.md |
| Wrong-subject rate | under 5% of placed inserts | [I] (target) | design_architecture.md |
| Rounds before refusing | up to 3 (requery, reroute, generate): a runaway guard, not a quota | [I] | design_architecture.md |
| Subject size | at least ⅓ of the frame or inset | [P] | research_broll.md |
| 16:9 source in full 9:16 | keeps 31.6% of the width. 1920×1080 gives 608×1080 (a 1.78× upscale); 3840×2160 gives 1215×2160 (fine). HD fits a split half (1080×960) | [X] arithmetic | — |
| Upscaling | ≤1.25× without super-resolution; SeedVR2 only with no faces or text | [X] / [I] | critique_feasibility.md |
| Frame rate (60 fps timeline) | prefer ≥30 fps for motion. 30 doubles cleanly; 24/25 gives uneven 3:2-style cadence | [X] | — |
| Grade match | exposure, white balance and contrast only; MKL transfer at ≤0.5 strength | [I] | critique_feasibility.md |
| SDR inserts in an HDR master | diffuse white at 203 cd/m² | [P] | [BT.2408](https://www.itu.int/pub/R-REP-BT.2408) |
| Text-bearing hold | ≥0.3 s/word + 1 s. Silent reading averages 238 wpm (~0.25 s/word) | [P] rate + [X] 1 s margin; basis [L] | [Brysbaert 2019](https://doi.org/10.1016/j.jml.2019.104047) |
| Photoreal AI | 0 by default; at most 1–2 flagged hero shots | [I] | design_doctrine.md |
| AI-label cost | ~7–8% fewer likes (1.1M TikTok posts + 8 experiments) | [L] | [JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493) |
| Shutterstock Standard video | audience of up to 500,000; Enhanced removes the cap (help-centre wording, not re-verified on the licence page; confirm in the API contract) | [A] | [Shutterstock](https://www.shutterstock.com/help/en/articles/10617046-what-usage-is-permitted-with-the-shutterstock-video-license) |
| Pexels / Pixabay API | 200 req/h and 20k/month, with a visible Pexels link / 100 req per 60 s, cache ≤24 h | [A] | [Pexels](https://www.pexels.com/api/documentation/), [Pixabay](https://pixabay.com/api/docs/) |

## How to decide

**1. Route by job.**

| Job | First choice | Fallback | Never |
|---|---|---|---|
| A real thing the creator owns or did | Their own media, or a pickup | A real photo or capture | Stock or AI posing as theirs |
| Evidence (post, headline, price, review, dashboard) | A live capture or their screen recording | A split screen with the face | A recreated or edited mock-up |
| Number, list, quote, comparison | A designed card | — | Stock "money" or "graph" footage |
| Mood, metaphor, place, generic action | Licensed stock, premium first | A stylized AI still with parallax | Photoreal AI of a real place or event |
| Punchline, reaction | The creator's reaction pickup, or a card | A meme under written licence | GIPHY, KLIPY, Tenor, film or TV clips |

**2. The creator's own media.**
- Search their uploads with the same embedding stack.
- Reject files carrying another platform's watermark, such as a downloaded TikTok, and ask for the original. Instagram stops recommending reposts to non-followers ([PetaPixel](https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/)).
- Mute their audio, and blur bystanders, balances, account numbers and notifications.
- If the right picture is their product, hands or workspace and none exists, request 2–3 pickups. Put them in the Director's single up-front question, or offer them after the edit. A 5 s phone clip of the real thing beats any stock.

**3. Real captures.**
- Capture public pages with Playwright at an iPhone viewport, and log the URL and time. If a site blocks scripts, as Reddit does, ask the creator for a screenshot or screen recording.
- Crop to the line that matters and highlight it. In screen recordings, silence notifications, crop the status bar and zoom to the part discussed; a full, unreadable page is decoration [X].
- Nominative fair use lets you show a brand to identify it, using only what is needed and implying no sponsorship ([INTA](https://www.inta.org/fact-sheets/fair-use-of-trademarks-intended-for-a-non-legal-audience/)).
- A post screenshot is on its firmest ground when the post is what the creator is discussing ([Boesen v. United Sports, 2020](https://www.copyright.gov/fair-use/summaries/boesen-unitedsportspubls-edny2020.pdf)). It must never be a back door to the photo inside it.
- No DMs or private accounts.
- Blur private individuals' handles and faces.
- Take logos from the page or from press kits. Brandfetch's free API forbids server-side use ([Brandfetch](https://docs.brandfetch.com/docs/logo-link/guidelines)).

**4. Designed cards.**
- Figures and quotes appear exactly as spoken, and quotes name the real speaker.
- Add a source line only when the creator named a source. Never invent one; flag unsupported claims to the creator instead.
- Generic chat bubbles are fine; a real platform's post look with a real handle is not.
- If the ≥0.3 s/word + 1 s hold exceeds ~3 s, cut words or keep the face in a split.

**5. Stock: brief, traps, queries, filters.**
1. **Brief:** one line covering subject, action, setting, shot size, camera motion, light and mood, with motion matched to the creator's energy. Prefer natural light and people who look like the viewer's world over staged "corporate acting" ([String Labs](https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/)) [P].
2. **Expected-false list:** the look-alikes that would be wrong, written before searching. For "espresso machine": a drip brewer, a kettle, or a café with no machine in shot.
3. **Queries:** 5–10, each leading with a visible noun plus an action ("ice cream melting on countertop"). Vary one axis per query: synonym, shot size (macro, close-up, overhead), motion (timelapse, slow motion, static) or setting. Use no brand names, and no people unless people are the point.
4. **Filter at the API:**
   - **Shutterstock:** `orientation=vertical`, `license=commercial`, `resolution=4k`, `fps_from`, and `people_model_released=true` when people appear ([SDK](https://github.com/shutterstock/public-api-javascript-sdk/blob/master/docs/VideosApi.md)).
   - **Pexels:** `orientation=portrait`, `size=large`.
   - **Everywhere:** drop editorial-only assets, which exclude commercial use ([Shutterstock](https://www.shutterstock.com/help/en/articles/10617059-shutterstock-editorial-license)).

**Scope.** Shutterstock Standard caps the audience at 500,000, which a hit short passes, so exports need Enhanced or an API contract. A Storyblocks Individual licence covers only work published during the subscription ([Storyblocks](https://www.storyblocks.com/resources/blog/what-you-need-to-know-about-storyblocks-individual-license-updates)).

**Free-stock traps.**
- **Brands:** Pexels and Pixabay bar commercial use of content showing trademarks, logos or brands "in relation to goods and services" ([Pexels ToS](https://www.pexels.com/terms-of-service/), [Pixabay](https://pixabay.com/service/license-summary/)). Treat a monetised or sponsored short as covered.
- **People:** Pexels bars showing identifiable people "in a bad light" ([licence](https://www.pexels.com/license/)) or as "suffering from, or medicating for" an ailment ([ToS](https://www.pexels.com/terms-of-service/)).
- **Releases:** Pexels warrants no releases and makes the user responsible for obtaining them ([ToS](https://www.pexels.com/terms-of-service/)).

So a recognisable stranger never sits under narration about scams, debt, illness or failure.

**6. Recall, then hard gates.**
- **Recall.** Embed 3–5 frames per candidate ([Qwen3-VL-Embedding](https://github.com/qwenlm/qwen3-vl-embedding) or [PE-Core](https://github.com/facebookresearch/perception_models)). Keep the top ~20 *by rank*, because cosine scores are not comparable across queries [X]. Keep no stored index of provider content: Pexels bans automated collection.
- **Gates.** Code checks over the planned in/out:
  - resolution after the crop;
  - quality ([UVQ](https://github.com/google/uvq), FFmpeg `blurdetect`/`blockdetect`; not DOVER, which is non-commercial);
  - OCR;
  - watermarks ([LAION detector](https://github.com/LAION-AI/LAION-5B-WatermarkDetection), MIT);
  - logos and faces;
  - shot changes ([TransNetV2](https://github.com/soCzech/TransNetV2));
  - the subject staying inside the 9:16 crop. If it drifts, use a split or PiP, or reject.

A failed gate rejects the clip. It is not a penalty to weigh against its strengths.

**7. Conform, then judge.**
- **Conform:**
  - tone-map HLG once;
  - keep native cadence by repeating frames (RIFE only when gated with a warp check);
  - crop to the layout;
  - match exposure, white balance and contrast to A-roll frames.
- **Contact sheet:** 4–6 frames per candidate, spread across the used range, with timestamps burned in, at the final crop, between the A-roll frames either side of the cut.
- **Scoring:** Claude scores each sheet alone on:
  - the correct subject, with none of the expected-false items;
  - size and position (Murch's eye-trace: near where the face was, [Murch](https://www.studiobinder.com/blog/walter-murch-rule-of-six/));
  - no text, watermark, logo, or person in a bad context;
  - fit with the look;
  - sharpness at phone size.
- **Watcher:** it views the top 1–2 composited into the edit, because stills miss morphing, warps and judder.

**8. AI generation, when stock fails or the image cannot exist.**
- **Default:** stylized stills. YouTube exempts clearly unrealistic or animated content from disclosure ([YouTube](https://support.google.com/youtube/answer/14328491)). Use 2.5D parallax with Depth Anything V2 *Small*, the only size licensed for commercial use ([repo](https://github.com/DepthAnything/Depth-Anything-V2)).
- **Commercially usable APIs:**
  - Imagen, Veo and Gemini, GA versions only, indemnified by Google ([Google](https://cloud.google.com/terms/generative-ai-indemnified-services)).
  - Adobe Firefly, indemnified only on enterprise plans ([Adobe](https://business.adobe.com/products/firefly-business/firefly-ai-approach.html)).
  - GPT Image, but OpenAI's output indemnity excludes trademark claims and output "modified, transformed, or used in combination" with other products ([Service Terms](https://openai.com/policies/service-terms/)), which a parallaxed composite is.
  - Runway, which allows commercial use but offers no indemnity ([terms](https://runwayml.com/terms-of-use)).
- **Avoid:** Kling without written permission ([§4.6](https://kling.ai/docs/user-policy)); Sora, whose API was removed on 24 September 2026 ([OpenAI](https://developers.openai.com/api/docs/deprecations)); Qwen-Image-2.1, FLUX.2 [dev] and klein 9B weights (non-commercial); MiniMax H3 weights in the US, EU, UK or Korea.
- **Never generate:** a real person, a real brand, or a real place or event presented as real.
- **Disclosure:** photoreal video always sets the disclosure flag, and metadata is never stripped.
  - TikTok auto-labels from C2PA ([TikTok](https://newsroom.tiktok.com/en-us/partnering-with-our-industry-to-advance-ai-transparency-and-literacy)); YouTube from C2PA and its own detection ([YouTube](https://support.google.com/youtube/answer/14328491)).
  - EU AI Act Art. 50(4) requires disclosing deepfakes: content that "resembles existing persons, objects, places, entities or events" and would "falsely appear" authentic (Art. 3(60)). Evidently artistic or fictional work needs only an unobtrusive disclosure ([Art. 50](https://artificialintelligenceact.eu/article/50/)).
- **Checks:** hands, garbled text, morphing and physics, at every sampled frame.
- **Copyright:** purely AI output is not copyrightable in the US ([USCO](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf)), so it adds nothing the creator owns.

**9. GIFs and memes.** Never render GIPHY, KLIPY or Tenor media into an export:
- **GIPHY** bans caching or proxying its media ([docs](https://developers.giphy.com/docs/api/)), and its user licence is personal and non-commercial ([terms](https://support.giphy.com/hc/en-us/articles/360020027752-GIPHY-User-Terms-of-Service)).
- **KLIPY** bans caching or storing, and its user licence is also personal and non-commercial ([terms](https://klipy.com/support/api-terms)).
- **Tenor's** API shut down on 30 June 2026 ([9to5Google](https://9to5google.com/2026/06/30/google-tenor-api-gif-updates/)).
- **Underlying rights:** most reaction GIFs are film or TV clips the site does not own, so Content ID risk would remain anyway.

Alternatives: the creator's reaction pickup, a card, a meme under licence, or a suggestion that they add one from the platform's own sticker picker at post time [X].

**10. Record or refuse.** Each `add_broll` op carries:
- the job, reason, word-ID span and layout;
- the asset ID;
- a licence record: provider, tier, capture URL or generating model, release status and disclosure flag. It is also what disputes a Content ID claim ([YouTube](https://support.google.com/youtube/answer/2797454));
- the conform recipe;
- 2–3 alternates that cleared the bar, or "none cleared".

If nothing clears the bar, write no op. Fall back to a punch-in or the face, and log "refused: \<reason\>".

## When to break it

- **The creator's own footage**, even grainy, shaky or 30 fps, beats pristine generic stock. Relax the quality gates for it, up to about 1.5× upscale. Never relax the licence, privacy or watermark gates.
- **Static 24 fps shots** can skip interpolation: judder needs motion to show.
- **Photoreal AI** is acceptable for a "what if" beat the creator knowingly asks for. Label it, and include no real people.
- **Tutorials:** the creator's screen recording is the main track.
- **Seam covers:** the relevance bar may drop to "doesn't contradict the line", though a punch-in still comes first.
- **Reuse** a clip only for a deliberate callback.

## Worked example

A 45 s personal-finance talking head, shot on an iPhone at 60 fps in HLG, with calm energy.

> w0–w10 "Your savings account is quietly losing you money every single month."

This is the hook, so there is no insert.

> w11–w27 "Most big banks pay zero point four percent. Prices went up almost three percent last year."

A number, so a card: "Big banks: 0.4%" then "Prices: ~3%", figures as spoken. No source line, because the creator named none; the claim is flagged to them. Five words need 2.5 s, so it could run full-screen, but the line lasts about 4 s and the creator's flat disbelief sells it, so it runs in a split with the face. Rejected: stock "burning cash", a cliché and a seductive detail.

> w28–w44 "So I moved mine to a high-yield account. Took me ten minutes."

The creator did this, and there is no media of it. The Director requests a pickup: "screen-record your account scrolling to the rate, 5 s, balance hidden." The account number is blurred and the clip plays as PiP.

> w45–w58 "This thread had four thousand people saying the same thing."

This is evidence. Reddit blocks scripts, so the creator screen-records the public thread, cropped to the title and vote count, with usernames blurred and the capture time logged. The thread is what the line is about. Its 11-word title needs 4.3 s, so it runs in a split rather than full-screen.

> w59–w70 "Leaving cash in there is like leaving ice cream on the counter."

This is a metaphor, so it goes to stock.
- **Brief:** a scoop melting into a puddle on a kitchen counter, close-up, daylight, static.
- **Expected-false list:** a frozen-yogurt shop, an ice-cream truck, people eating, snow, 3D renders.
- **Queries:** "ice cream melting on kitchen counter", "melting ice cream scoop close up", "ice cream puddle countertop timelapse", "melting popsicle dripping close up", and two more. Filters: vertical, commercial, 4K, ≥30 fps.
- **Gates:** of 140 candidates, the top 20 were gated and 7 rejected: two watermarked, one branded tub, three soft, one with a hand entering at 2.1 s.
- **Pick:** after conform, a vertical timelapse of a scoop slumping in a bowl cleared the bar. It plays 1.4 s full-screen, "ice" to "counter". Alternate: a dripping popsicle.

> w71–w80 "My friend Dave laughed at me. Now he's doing it too."

Refused: a GIPHY "laughing" GIF (licence) and a stock laughing man (a stranger posing as a real friend). The face carries it, as it does the CTA, w81–w86 "Follow for part two."

Four inserts in 45 s (about 5 per minute) sits above the educational prior of 0–4 per 60 s in `broll.md`. It stands only because three keep the face and three are the creator's own proof or real captures; only the 1.4 s stock shot hides the face. If the critic finds it busy, cut the stock first: the metaphor already works in words.

## Anti-patterns

- Keyword b-roll: "money" becomes raining cash, "work" becomes typing hands.
- Taking the best of a weak top five because something had to win.
- A Pexels stranger under "scammers" or "debt"; editorial-only stock in a sponsored video.
- GIPHY, KLIPY or Tenor media, ripped film clips, or other creators' TikToks.
- Mock posts from real accounts, edited screenshots, invented sources.
- HD landscape stretched to full 9:16, 24 fps judder in pans, untreated SDR beside HLG.
- Photoreal AI "real" places or people, or stripped C2PA.
- The same top-ranked stock clips across many creators' videos: the templated sameness YouTube's mass-produced-content policy targets ([Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/); applying it to stock is [X]).

## Critic questions

1. Does every insert show the specific thing being said, not a look-alike or generic stand-in?
2. Is every frame of every insert free of watermarks, platform logos and unintended brand logos?
3. Where a number, list or quote is shown, is it a designed card whose figures match the spoken words?
4. Is every screenshot a real, unaltered capture with private individuals obscured?
5. Is the video free of recognisable stock people paired with negative, medical or embarrassing narration?
6. Is the video free of GIPHY, KLIPY and Tenor media and film or TV clips?
7. If any insert is photoreal AI, is the disclosure flag set?
8. Does every insert match the A-roll's look (no grey HDR), move without judder or morphing, and stay sharp at phone size?
9. Do the inserts look like they belong to each other?
10. Is every text-bearing insert readable in its hold time and inside the safe band?
11. Does every insert's subject fill enough of the frame to read at a glance?

## Sources

- Pexels licence: https://www.pexels.com/license/
- Pexels terms: https://www.pexels.com/terms-of-service/
- Pexels API: https://www.pexels.com/api/documentation/
- Pixabay licence: https://pixabay.com/service/license-summary/
- Pixabay API: https://pixabay.com/api/docs/
- Shutterstock video licence: https://www.shutterstock.com/help/en/articles/10617046-what-usage-is-permitted-with-the-shutterstock-video-license
- Shutterstock editorial licence: https://www.shutterstock.com/help/en/articles/10617059-shutterstock-editorial-license
- Shutterstock API: https://github.com/shutterstock/public-api-javascript-sdk/blob/master/docs/VideosApi.md
- Storyblocks Individual licence: https://www.storyblocks.com/resources/blog/what-you-need-to-know-about-storyblocks-individual-license-updates
- Pond5 search tips: https://blog.pond5.com/26162-5-game-changing-stock-media-search-tips/
- GIPHY API: https://developers.giphy.com/docs/api/
- GIPHY User Terms: https://support.giphy.com/hc/en-us/articles/360020027752-GIPHY-User-Terms-of-Service
- KLIPY API terms: https://klipy.com/support/api-terms
- 9to5Google, Tenor shutdown: https://9to5google.com/2026/06/30/google-tenor-api-gif-updates/
- INTA, fair use of trademarks: https://www.inta.org/fact-sheets/fair-use-of-trademarks-intended-for-a-non-legal-audience/
- Boesen v. United Sports (USCO summary): https://www.copyright.gov/fair-use/summaries/boesen-unitedsportspubls-edny2020.pdf
- Brandfetch guidelines: https://docs.brandfetch.com/docs/logo-link/guidelines
- USCO AI report, Part 2: https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf
- YouTube altered or synthetic content: https://support.google.com/youtube/answer/14328491
- TikTok Content Credentials: https://newsroom.tiktok.com/en-us/partnering-with-our-industry-to-advance-ai-transparency-and-literacy
- EU AI Act Article 50: https://artificialintelligenceact.eu/article/50/
- Carney, Riveros & Tully, JCR 2026: https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493
- Google indemnified AI services: https://cloud.google.com/terms/generative-ai-indemnified-services
- Adobe Firefly approach: https://business.adobe.com/products/firefly-business/firefly-ai-approach.html
- OpenAI Service Terms: https://openai.com/policies/service-terms/
- Runway terms of use: https://runwayml.com/terms-of-use
- YouTube Content ID disputes: https://support.google.com/youtube/answer/2797454
- String Labs, stock footage mistakes: https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/
- OpenAI deprecations (Sora): https://developers.openai.com/api/docs/deprecations
- Kling user policy: https://kling.ai/docs/user-policy
- Depth Anything V2: https://github.com/DepthAnything/Depth-Anything-V2
- Sundararajan & Adesope 2020: https://link.springer.com/article/10.1007/s10648-020-09522-4
- Mayer 2023, research-based principles (coherence 18 of 19, from Mayer 2021): https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf
- Brysbaert 2019, reading rate: https://doi.org/10.1016/j.jml.2019.104047
- Wang et al. 2023, LLM evaluator bias: https://arxiv.org/abs/2305.17926
- Jagerman et al. 2023, LLM query expansion: https://arxiv.org/abs/2305.03653
- Murch's rule of six (summary): https://www.studiobinder.com/blog/walter-murch-rule-of-six/
- Qwen3-VL-Embedding: https://github.com/qwenlm/qwen3-vl-embedding
- Perception Encoder: https://github.com/facebookresearch/perception_models
- UVQ: https://github.com/google/uvq
- LAION watermark detection: https://github.com/LAION-AI/LAION-5B-WatermarkDetection
- TransNetV2: https://github.com/soCzech/TransNetV2
- ITU-R BT.2408: https://www.itu.int/pub/R-REP-BT.2408
- PetaPixel, Instagram reposted content: https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/
- Social Media Today, YouTube inauthentic content: https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/
