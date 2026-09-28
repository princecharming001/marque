# Evidence ledger

Every number the doctrine uses, with its meaning, tier, source and the files that use it. Tiers follow `SKILL.md`: [L] lab or peer-reviewed, [A] platform or large-scale data, [V] vendor, [P] practitioner or standards convention, [I] Yunicorn internal, [X] our inference or arithmetic; "→" marks a finding applied by inference. Every row is due for re-checking in **2027-03**; platform facts, provider terms and API limits should be re-checked first. Worked-example measurements (a creator's wpm, a take's SNR) are illustrations, not doctrine, and are not ledgered.

When a number changes, change it here, in the files listed under "Used in", and in `constants.yaml`.

**File keys.** SK `SKILL.md` · SH `story-and-hook.md` · CP `cutting-and-pacing.md` · SP `speed.md` · FZ `framing-and-zooms.md` · BR `broll.md` · BS `broll-sourcing.md` · CT `captions-and-text.md` · TG `transitions-and-graphics.md` · MU `music.md` · FX `sfx.md` · VL `voice-and-loudness.md` · CL `color-and-look.md` · EN `endings-loops-ctas.md` · PL `platforms.md` · CR `critique.md` · ED `styles/educational-tutorial-listicle.md` · PS `styles/podcast-sales-founder.md` · SC `styles/story-comedy-hottake.md` · EX `examples.md` · K `constants.yaml`

Internal documents (design_doctrine.md, research_*.md, critique_*.md, design_*.md) live in `reports/Yunicorn Studio design inputs/`; ARCHITECTURE.md is `studio/ARCHITECTURE.md`.

---

## 1. Calibration and internal measurements

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 50 s, 140 words | Calibration take: raw length and word count, with three false-started hook attempts and one mid-sentence restart | [I] | `backend/eval/pro_cut_reference.py` | SK, CP, EX, K | 2027-03 |
| 1 vs 19 | The pro made one mid-video content splice (at a sentence boundary) where the old engine made 19 dead-air micro-splices; shorthand "1 cut vs 19" | [I] n=1 | `backend/eval/pro_cut_reference.py`; design_doctrine.md §1 | SK, SH, CP, FZ, BR, CT, TG, FX, CR, SC, EX, K | 2027-03 |
| 6.9 s | Content the pro removed: the false starts and the restart only | [I] | `backend/eval/pro_cut_reference.py` | SK, EX, K | 2027-03 |
| 0.27 s | The pro's mid-sentence stumble removal | [I] | `backend/eval/pro_cut_reference.py` (PRO_CUT_SPANS_MS) | SK, CP, EX, K | 2027-03 |
| 577 ms, 690 ms | The two stalls the pro tightened; the only pause edits | [I] | `backend/eval/pro_cut_reference.py` | SK, CP, EX, K | 2027-03 |
| ~17 of 22 | Pauses ≥300 ms the pro kept to the millisecond (385→385, 481→481, 368→369 ms) | [I] | `backend/eval/pro_cut_reference.py` | SK, CP, SP, EX, K | 2027-03 |
| 32% → 30% | Silence ratio before and after the pro's cut | [I] | `backend/eval/pro_cut_reference.py` | CP, EX, K | 2027-03 |
| ~42 s; 4 edits (~6/min; ~1.4/min content splices) | Pro output length and mid-video edits after the head trim | [I] | `backend/eval/pro_cut_reference.py` | CP, EX, K | 2027-03 |
| >350 ms → 200 ms | The old engine's pause policy that produced the 19 splices | [I] | `backend/eval/pro_cut_reference.py` | EX | 2027-03 |
| 100% | Spoken CTA words the pro kept ("Rate that version 7 out of 10. Follow for the next collision test.") | [I] n=1 | `backend/eval/pro_cut_reference.py` | EN, EX, K | 2027-03 |
| Earlier of two | The pro kept the earlier of two complete hook deliveries | [I] | `backend/eval/pro_cut_reference.py` | CP, EX | 2027-03 |
| 1–4 per 60 s | Seam prior for a clean single take after false starts go; a critic signal, never a gate | [I] n=1 → [X] | `backend/eval/pro_cut_reference.py` | SH, CP, CR, ED, K | 2027-03 |
| 3× | The old engine sped silent gaps up to 3× (anti-pattern) | [I] | research_codebase.md | SP | 2027-03 |
| 1.30× | The old engine's per-sentence rate normalisation ceiling (anti-pattern) | [I] | research_codebase.md | SP | 2027-03 |
| y ≈ 1600 | Old caption anchor, under the platform UI (anti-pattern) | [I] | research_captions_text.md | CT, PL, K | 2027-03 |

## 2. Story, hook and length

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 0.1–0.5 s | Target for the first spoken word after frame 0; also the restart lead-in | [X] | — | SH, EN, SC, K | 2027-03 |
| 2.5 s | All 13 measured top Shorts reached speech or an on-screen payoff by 2.5 s | [V] n=13 | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws) | SH, CR, SC, K | 2027-03 |
| ≤3 s | Topic (proposition) clear | [A] ads | [TikTok creative best practices](https://ads.tiktok.com/help/article/creative-best-practices) | SH, CR, SC, PS, K | 2027-03 |
| ≤6 s; 3.0 s median; 1.34% vs 0.69% | Hook fully set; median hook resolves at 3.0 s; 4–6 s hooks had higher save rates than 2–3 s (descriptive) | [A] ads; [V] | [TikTok](https://ads.tiktok.com/help/article/creative-best-practices), [FYPNow](https://fypnow.com/research/tiktok-video-anatomy) | SH, PS, K | 2027-03 |
| 3 s | Instagram's skip rate counts skips in the first 3 s | [A] | [Meta insights](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights) | SH | 2027-03 |
| 5 s; 1.5M | Skips cluster in the first 5 s (Kuaishou and a second app, 1.5M interactions) | [A] | [arXiv 2504.03107](https://arxiv.org/html/2504.03107v1) | SH | 2027-03 |
| +50%; 2 s | A person on screen in the first 2 s raised "hooking power" (MetrixLab 2023, ads) | [A] ads | [CreatorIQ × TikTok](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf) | SH, PS | 2027-03 |
| +128%; 5 s | "You" in the first 5 s raised purchase intent (Lumen 2022, ads) | [A] ads | CreatorIQ × TikTok (above) | SH, PS | 2027-03 |
| 1.5× | Ads with speech addressed to the viewer were 1.5× as likely to hook | [A] ads | CreatorIQ × TikTok (above) | SH | 2027-03 |
| 100 ms | A first impression of a face forms in 100 ms | [L] | [Willis & Todorov 2006](https://pubmed.ncbi.nlm.nih.gov/16866745/) | SH | 2027-03 |
| 55–85% | Payoff position in runtime | [V] n=13 | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws) | SH, PS, K | 2027-03 |
| 30–60 s; 60–180 s | Default length; longer only for a real arc | [A] + [V] correlational | [Socialinsider Reels](https://www.socialinsider.io/blog/instagram-reels-length/), [vidIQ](https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_) | SH, K | 2027-03 |
| 45–60 s; 6M | Reels median views peak at 45–60 s (10,374 vs 4,700 under 30 s; 4,428 above 180 s), brand accounts, H1 2026 | [A] vendor-run, correlational | [Socialinsider Reels](https://www.socialinsider.io/blog/instagram-reels-length/) | SH, PL, ED, SC | 2027-03 |
| 45–59 s; 331M | Shorts length with the most average views; 15–29 s, the most common, gets the fewest | [V] | [vidIQ](https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_) | SH, PL, ED | 2027-03 |
| 70–90%; <60%; 5,400 | Viewed-vs-swiped for top Shorts; under 60% rarely performs (2023, before the 2025 view-count change) | [V] | [Galloway](https://threadreaderapp.com/thread/1646898356419981315.html) | SH | 2027-03 |
| ≈2.3×; 4,452 vs 1,973; 6,037 on 40 clips; 34,635 | Hook categories (7-day views, AI-labelled): best vs worst among categories with 500+ clips; top category overall (product showcase) on a tiny sample | [V] correlational | [OpusClip hooks](https://www.opus.pro/research/best-video-hooks-tiktok) | SH | 2027-03 |
| 3,994; 3,708; 3,602; 7,722 | Process-explainer, question-opener and story-teaser average views; shock/surprise is the most used (7,722 clips) | [V] | OpusClip hooks (above) | ED, SC | 2027-03 |
| 2–3 | Hook alternates, differing in type | [X] | — | SH, K | 2027-03 |
| 3–5 | Creatives per ad group (sales hook alternates) | [A] ads | [TikTok creative best practices](https://ads.tiktok.com/help/article/creative-best-practices) | PS, EX, K | 2027-03 |
| ≤7 words | Hook title length | [P] + [X] | [BBC via Clevercast](https://www.clevercast.com/bbc-subtitling-guidelines/) | SH, CT, SC, PS, K | 2027-03 |
| 1.0× | Hook speed; one continuous take | [X] | [Vaughan-Johnston](https://pmc.ncbi.nlm.nih.gov/articles/PMC12681368/) (falling pitch, 3 experiments) [L] | SH, SP, K | 2027-03 |
| ≤25 words | The brief's one-idea sentence | [X] | — | SH, SK, K | 2027-03 |
| ≥150 ms | Clean gaps at both ends of a pulled-forward cold open | [X] | — | SH, K | 2027-03 |
| ≤~1 s | A signature greeting may stay | [X] | — | SH, PS, K | 2027-03 |
| 4–6 s | Setup a sophisticated audience may need | [X] | — | SH | 2027-03 |
| ~40 s | Videos longer than this should keep a question open at the midpoint (critic) | [X] | — | SH | 2027-03 |
| 18 of 19; d = 0.86 | Coherence principle: removing extraneous material helped (median effect) | [L] | [Mayer 2023](https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf) (from Mayer 2021) | SH, BR, BS, FX, ED | 2027-03 |
| 51% / 23% / 10% / 7% / 5% / 4% | Murch's rule of six: emotion, story, rhythm, eye-trace, planarity, 3D space | [P] | [Murch](https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf) | SH, CP, FZ, BR, MU, TG, CR | 2027-03 |
| 69%; 25%; n = 5,616 | US adults who say they watch video with sound off in public (private: 25%), 2019 self-report | [A] | [Verizon/Publicis via Forbes](https://www.forbes.com/sites/tjmccue/2019/07/31/verizon-media-says-69-percent-of-consumers-watching-video-with-sound-off/) | SH, CT, FX, CR | 2027-03 |
| 88% | TikTok users who call sound vital (Nielsen for TikTok, 2020) | [A] | [TikTok, Evolution of Sound](https://ads.tiktok.com/business/en-US/blog/evolution-of-sound-volume-1) | SH, CR | 2027-03 |
| ~2,000 | Viewers in the eye-tracked TV-ad zapping study | [L] | [Teixeira, Wedel & Pieters 2010](https://pubsonline.informs.org/doi/10.1287/mksc.1100.0567) | SH | 2027-03 |
| n = 3 | DOAC trailers that mute key words to hold the gap | [V] | [PandaStudio DOAC](https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips) | SH | 2027-03 |

## 3. Cutting, pauses and seams

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| <~200 ms; ~180 ms; 100–250 ms; <150 ms | A gap inside a phrase is articulation, not a seam; a stop closure alone can last ~180 ms; research pause floors; agent editors call <150 ms unsafe | [L] + [P] | [Llisterri](https://joaquimllisterri.cat/phonetics/LBASS_21/LBASS_21_5_pauses.html), [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md) | CP, K | 2027-03 |
| ~126 ms; <~100 ms | In a tight phrase a gap was judged disfluent on half of trials at ~126 ms; close mid-phrase cut gaps to the speaker's articulation gap | [L] one phrase → [X] | [Warner 2022](https://doi.org/10.1016/j.jfludis.2022.105896) | CP, K | 2027-03 |
| 250–550 ms; 493 ms | Keep sentence-boundary pauses as spoken; median silent pause in 44 min of read English | [L] | [Campione & Véronis 2002](http://sprosig.org/sp2002/pdf/campione-veronis.pdf) | CP, SP, EN, EX, K | 2027-03 |
| ~0.6 s; 0.6–1.2 s; 0.075–0.15 s; ≥2.4 s | Most natural pauses within and between sentences in read text; least natural | [L] audio | [Liu 2022](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.778018/full) | CP | 2027-03 |
| 51–53%; 55–75% | Raters did not prefer original sentence pauses over 5 ms ones, but did over long ones | [L] audio | [Owoicho 2024](https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf) | CP | 2027-03 |
| ~0.6 s | Silences past this start to carry meaning | [L] | Liu 2022; Owoicho 2024 (above) | CP | 2027-03 |
| 0.6–1.5 s → 250–400 ms; ~505 ms; 186 ms | Tighten mid-thought hesitations; minimal hesitation pause and optimal fluent pause in one small 1973 study | [X] + [L] | [Ruder 1973](https://journals.sagepub.com/doi/10.2466/pms.1973.36.1.47) | CP, SP, EX, K | 2027-03 |
| 0.5–1.2 s (to ~1.5 s storytime) | Deliberate beat after a key claim or before a reveal | [X] + [L] audio | [MacGregor 2010](https://www.research.ed.ac.uk/en/publications/listening-to-the-sound-of-silence-disfluent-silent-pauses-in-spee/) | CP, ED, SC, K | 2027-03 |
| >~1.5 s | Dead air: cut unless the silence is the content | [X] | — | CP, CR, K | 2027-03 |
| 1.5–3 s | Confession silence to hold when the face is working | [X] | — | CP, K | 2027-03 |
| 0–250 ms; 0–200 ms; 208 ms; ~600 ms | Turn gaps between speakers; modal and mean gap across 10 languages; a ~600 ms gap sounded less willing | [L] | [Stivers 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC2705608/), [Roberts & Francis 2013](https://doi.org/10.1121/1.4802900) | CP, PS, K | 2027-03 |
| +7 to +469 ms | Range of language means for answer gaps | [L] | Stivers 2009 (above) | PS | 2027-03 |
| ≥700 ms | Gap at which dispreferred answers (refusals) clearly outnumber preferred ones; never create or erase one | [L] | [Kendrick & Torreira 2015](https://doi.org/10.1080/0163853X.2014.955997) | PS, K | 2027-03 |
| 200–300 / 250–450 / 350–700 ms | Kept boundary pauses by energy: high (hot take, sales, UGC), conversational (founder, explainer), calm (educational, storytime); podcast 200–300 in a turn | [X] | — | SK, CP, EN, SC, PS, ED, EX, K | 2027-03 |
| ≤0.6 / 0.5–0.9 / 0.8–1.5 s | Deliberate beats by energy (high, conversational, calm) | [X] | — | SK, CP, SC, K | 2027-03 |
| 0.7–1.3×; >2× | Keep boundary pauses within this multiple of the speaker's median; tighten gaps above twice it | [X] | — | SK, CP, SC, K | 2027-03 |
| 350–500 ms | Pauses for a calm hot take | [X] | — | SC | 2027-03 |
| ~6 per 100 words; ~9/min | Natural disfluency rate (all kinds) | [L] | [Bortfeld 2001](https://journals.sagepub.com/doi/10.1177/00238309010440020101) | CP, K | 2027-03 |
| 5/min; 12/min | Filler rates judged "may be acceptable" and harmful to perceived effectiveness | [L] | [Laske & DiGennaro Reed 2024](https://onlinelibrary.wiley.com/doi/abs/10.1002/jaba.1093) | CP, K | 2027-03 |
| 0 first sentence; ≤3–5/min | Filler prior | [L] → [X] | Bortfeld; Laske (above) | CP, SH, K | 2027-03 |
| ≥150 ms | Gaps around a filler that make it safe to cut | [X] | — | CP, K | 2027-03 |
| ≥20 ms; 5–10 ms | Stop closure needed to cut a coarticulated filler; its crossfade | [X] | — | CP, K | 2027-03 |
| 30–200 ms | Cut padding | [P] | [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md) | CP, K | 2027-03 |
| 50–100 ms | ASR timestamp drift | [P] | video-use (above) | CP, K | 2027-03 |
| ~120 ms (123 ms); 21 ms; 84.9% | Raw Whisper mean word-boundary error; MFA mean error; MFA boundaries within 50 ms on conversational speech | [V] + [L] | [FA-Bench ASR](https://github.com/olewave/fa-bench/blob/main/records/202609/en/asr/word/buckeye/README.md), [FA-Bench aligners](https://github.com/olewave/fa-bench/blob/main/records/202609/en/gold/word/buckeye/README.md), [Rousso 2024](https://arxiv.org/html/2406.19363) | CT, CP, FZ, K | 2027-03 |
| 66–110 ms; ~200 ms | WhisperX drift; on disfluent speech | [L] | research_perception_models.md | FZ | 2027-03 |
| 10–30 ms; ~2 ms; 30–100 ms; 30 ms | Seam crossfade in room tone (equal-power); in true silence or on a stop; across a breath; video-use's default | [P] + [X] | [SOS comping](https://www.soundonsound.com/techniques/vocal-comping-editing), [SOS fades](https://www.soundonsound.com/techniques/using-fades-crossfades), video-use | CP, VL, K | 2027-03 |
| >~4–5 dB; 0.3–0.5 dB | Background-level jumps become audible at a seam; small changes can already make an edit feel different | [P] classical editing | [ebrary (Decca)](https://ebrary.net/300232/education/crossfades), [Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise) | CP, VL, EN, CR, K | 2027-03 |
| +45 / −125 ms; +90 / −185 ms | A/V offset detectable (audio early / late); acceptable limits | [L] ITU subjective tests | [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) | CP, SP, BR, CT, MU, FX, VL, CR, K | 2027-03 |
| ~25%; ~33% | Continuity cuts that went unnoticed; on a motion onset | [L] Hollywood film | [Smith & Henderson 2008](https://bop.unibe.ch/JEMR/article/view/2264) | CP, TG | 2027-03 |
| 3–8 s; ~5 s; 2.5–3 s | Visual-change interval in 13 Shorts (4 creators) and median; one DOAC clip before its payoff | [V] n=13; n=1 | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws), [DOAC](https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips) | CP, ED, SC, PS, K | 2027-03 |
| ≥~8 s | A static stretch becomes a finishing question ("nothing" is a valid answer) | [X] | — | CP, FZ, BR, K | 2027-03 |
| 8–20 s | Static stretch that is fine in storytime while the delivery holds | [X] | — | SC | 2027-03 |
| 0.5–0.67 cuts/s | "Fast" pacing in the lab literature (1.5–2 s shots) | [L] | [Lang 1999](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504), [Collier 2010](https://exa.ai/library/publication/s49x2k62711) | CP, PS, K | 2027-03 |
| 4 s; 58% vs 41% | Vendor "pattern interrupt" claim; method undisclosed, not adopted | [V] | [OpusClip](https://www.opus.pro/blog/tiktok-length-format-retention-data) | CP | 2027-03 |
| +60% | Seamless transitions and recall in creator ads ("editing shouldn't be frenetic") | [A] ads | [TikTok/Lumen](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf) | CP, TG, PS | 2027-03 |
| 0–300 ms; ~0.3 s; 74%; n = 19 | Switch picture 0–300 ms after a new speaker's first word; eyes arrive ~0.3 s after speech starts; viewers watch the current speaker 74% of the time | [L] | [Hirvenkari 2013](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0071569) | CP, PS, K | 2027-03 |
| ≤200 ms | Motivated early switch (inhale, head turn) | [X] | — | PS, K | 2027-03 |

## 4. Speed

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 1.0× | Default speech speed; the pro changed no speed | [I] | `backend/eval/pro_cut_reference.py` | SK, SP, K | 2027-03 |
| ~5% | Just-noticeable tempo difference with a reference | [L] | [Quené 2007](https://doi.org/10.1016/j.wocn.2006.09.001) | SK, SP, K | 2027-03 |
| 1.03–1.08×; 1.05×; 1.10×; 1.10–1.20×; >1.20× | Gentle speed-up; starting factor; soft ceiling; needs a written reason; visible effect only | [X] on [L] | Quené; [MacLachlan & Siegel 1980](https://journals.sagepub.com/doi/abs/10.1177/002224378001700106) (1.25× unnoticed without reference); [Yueh 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12675162/) | SK, SP, ED, SC, PS, EX, K | 2027-03 |
| ≤0.05 | Speed difference between adjacent segments inside one thought | [X] | — | SP, K | 2027-03 |
| <~150 wpm; 156 wpm (48–254) | Output rate at which a segment becomes a speed candidate; edX video average | [X]; [A] | [Guo, Kim & Rubin 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf) | SP, K | 2027-03 |
| 184 / 172 wpm; n = 312 / 121; 16–18 CPS | Creator pace on Reels and TikTok, pauses included | [V] | [Voqusa](https://www.voqusa.com/en/blog/video-transcription-statistics-2026) | SP, CT | 2027-03 |
| 170 / 190 wpm; 170–180 wpm | Best recognition rate for dense and light news; output ceiling on dense content | [L] → [X] | [Rodero 2016](https://doi.org/10.1080/15213269.2014.1002942) | SP, K | 2027-03 |
| 164 wpm; 196 wpm (111–291) | Conversational rate per speaker; both talkers | [L] | [Yuan et al. 2006](https://www.isca-archive.org/interspeech_2006/yuan06_interspeech.html) | SP | 2027-03 |
| ~275 wpm; ~2 points to 1.5×; 17 points at 2.5×; 24 studies | Comprehension knee; lecture playback losses | [L] | [Foulke & Sticht 1969](https://pubmed.ncbi.nlm.nih.gov/4897155/), [Murphy 2022](https://onlinelibrary.wiley.com/doi/abs/10.1002/acp.3899), [Tharumalingam 2025](https://doi.org/10.1007/s10648-025-10003-9) via [Pearce](https://theconversation.com/what-happens-to-your-brain-when-you-watch-videos-online-at-faster-speeds-than-normal-259930) | SP | 2027-03 |
| 4.17→3.82; 1.94→2.59; 3.94→3.69; n = 326, 313, 246 | Satisfaction and perceived distortion at 1.25× (cooking tutorial); replication on an explainer | [L] | [Yueh et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12675162/) | SP | 2027-03 |
| 180→220 wpm | Faster speech helped persuasion only under moderate involvement | [L] | [Smith & Shaffer 1995](https://journals.sagepub.com/doi/10.1177/01461672952110006) | SP | 2027-03 |
| +1.65 / +3.2 semitones; +20% | Pitch rise at 1.10× / 1.20× without correction; the pitch rise that made speakers seem less truthful | [X] arithmetic; [L] | [Apple et al. 1979](https://doi.org/10.1037/0022-3514.37.5.715) | SP | 2027-03 |
| 2.5–4.2× | Range at which nonuniform compression beat uniform | [L] | [Covell et al. 1998](https://www.mangolassi.org/covell/1997-061/index.html) | SP | 2027-03 |
| 0.5–100; 2.0 | FFmpeg `atempo` range; it skips samples above 2.0 | [V] | [FFmpeg atempo](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/atempo.html) | SP | 2027-03 |
| ~5 frames/s | Frames skipped at 60 fps and 1.08× (nearest-frame) | [X] arithmetic | — | SP | 2027-03 |
| ≥ output fps ÷ source fps; 0.5×; 0.25× | Slow-motion floor; 120 fps Slo-mo into 60 fps output; 240 fps | [A] + [P] | [Apple](https://support.apple.com/guide/iphone/change-video-recording-settings-iphc1827d32f/ios), [Frame.io](https://blog.frame.io/2019/10/17/mixed-frame-rates-part-3/) | SP, BR, K | 2027-03 |
| 2–3×; ~8× | Fast motion on silent process footage; timelapse-like steps | [P] + [X] | — | SP, BR, ED, K | 2027-03 |
| 0; ≤2; 0.5–1.5 s | Speed ramps by default, per short, and each | [V] | [OpusClip](https://www.opus.pro/blog/best-speed-ramp-pace-control-tools-short-form) (2–3 max), [Kapwing](https://www.kapwing.com/resources/how-to-use-the-speed-ramp-effect/) | SP, K | 2027-03 |
| 2× | Viewer-side fast-forward on TikTok, Reels (Mar 2025) and Shorts (Jun 2026) | [A] | [PetaPixel](https://petapixel.com/2025/03/28/instagram-rolls-out-fast-forward-feature-for-reels/), [TechCrunch](https://techcrunch.com/2026/06/25/youtube-shorts-are-getting-even-shorter-with-an-update-that-lets-you-double-the-playback-speed/) | SP, K | 2027-03 |
| <~130 wpm; 1.12–1.15× | A very slow deliberate speaker on low-stakes explanation | [X] | — | SP, K | 2027-03 |
| 1.02–1.04× | Uniform whole-video factor for a hard duration cap | [X] | — | SP, K | 2027-03 |
| 1.5–3×; ≤3 s | Fast-forward gag, once, signposted | [X] | — | SP, SC, K | 2027-03 |
| ≥~300 ms | Speed changes only at a seam or a pause this long | [X] | — | SP, K | 2027-03 |
| ~1 s | Revert a speed change that saves less than this across the video | [X] | — | SP, K | 2027-03 |
| ~0.6 s | No sped segment should still contain a pause this long (critic) | [X] | — | SP | 2027-03 |
| 0.5× | Instant-replay gag speed with pitched-down audio | [X] | — | SP | 2027-03 |

## 5. Framing and punch-ins

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 1.15–1.25×; 1.4–1.6×; <~1.1× | Lean-in within the shot; a new, closer shot; an accident | [V] + [P] | [AutoClip](https://autoclip.dev/blog/punch-in-zoom-crop-zoom-speaker-zoom-guide) (15–25%), [Murch](https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf) | FZ, CP, EN, K | 2027-03 |
| ≥1.25× (1.3–1.5× preferred); 1.24–1.57× | Scale change that hides a seam inside a thought; the 20 mm/30° rule on 85–35 mm lenses; "15–20%" is lore | [X] from [P] | [30-degree rule](https://en.wikipedia.org/wiki/30-degree_rule); research_cuts_pacing.md | CP, FZ, CL, EN, EX, K | 2027-03 |
| 1.5×; 3.5–5.6 s; n = 3 | Hormozi A/B crop: B size and interval between cuts | [V] | [PandaStudio Hormozi](https://www.writepanda.ai/blog/hormozi-style-shorts-editing) | FZ, PS, SC, K | 2027-03 |
| One 1.5× punch; 75%; n = 4 | Abdaal: one punch in four Shorts (three had none), at 75% of runtime | [V] | [PandaStudio Abdaal](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal/) | FZ, ED, K | 2027-03 |
| 0–2 typical; ~4 ceiling | Emphasis punches per 60 s (hot take 0–2, explainer 0–2, sales 2–4, founder 0–2) | [I] from [V] | design_doctrine.md §17; AutoClip ("3–4 per 40 s is a lot"); the style files | SK, FZ, ED, PS, SC, K | 2027-03 |
| 0–70 ms; 200 ms | Punch lands on the stressed onset, slightly early; 200 ms late "reads as a mistake" | [X]; [V] | AutoClip | FZ, K | 2027-03 |
| <~150 ms | Animated snap that reads "as a beat rather than a move" | [V] | AutoClip | FZ, K | 2027-03 |
| ≥~1.5 s | Punch hold, to the clause end | [X] | — | FZ, K | 2027-03 |
| ~5 s | Spacing between punches outside an A/B rhythm | [X] | — | FZ, K | 2027-03 |
| 1–2 s | No punch while the face and hook title are being read | [X] | — | FZ, K | 2027-03 |
| 2.0× (1.5× to 1440×2560) | Lossless ceiling, 4K vertical to 1080×1920 | [X] arithmetic | — | FZ, K | 2027-03 |
| 1440×2560 | Meta's recommended Reels ad resolution | [A] | [Meta ads guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels) | FZ, PL | 2027-03 |
| ~1.1×; 1.2×; 1.25× | Upsampling tolerated on a face by default; on clean footage after a 1:1 eye check; hard limit | [P] + [X] | [Creative COW](https://creativecow.net/forums/thread/how-much-can-you-zoom-in-on-4k-footage-in-a-1080p/) (110% "safe", 120% "super clean") | FZ, PS, K | 2027-03 |
| 1215×2160, 1.125×; 608×1080, 1.78×; 0.89×; 31.6% | 9:16 crop from 4K and 1080p landscape; stacked halves; share of 16:9 width kept | [X] arithmetic | — | FZ, BS, PS, K | 2027-03 |
| y ≈ 600–700 | Eye line about one third down the frame | [P] | [Headroom](https://en.wikipedia.org/wiki/Headroom_(photographic_framing)) | FZ, K | 2027-03 |
| 25–35%; 38–50%; ~40% | Face height (crown to chin) at base and tight framing; Hormozi's face ~40% of frame width | [X]; [V] | PandaStudio Hormozi | FZ, K | 2027-03 |
| +3–8% over 4–12 s; 10–15% over 20–30 s | Slow push-in; Morphic's "almost subliminal" push | [X] from [V] | [Morphic](https://morphic.com/ai-glossary/slow-zoom) | FZ, SC, K | 2027-03 |
| ≤5%; ≤2%; or ≥~1.25× | Take-to-take match: face size, eye line (of frame height), or a clear change | [X] + [I] | critique_feasibility.md | FZ, EN, EX, K | 2027-03 |
| ±10%; 0.7 s; ≥0.4 s | Reframe dead zone (of crop width), hold before re-centring, easing | [X] | [Grundmann 2011](https://research.google/pubs/auto-directed-video-stabilization-with-robust-l1-optimal-camera-paths/), [1€ filter](https://gery.casiez.net/publications/CHI2012-casiez.pdf) | FZ, K | 2027-03 |
| 1.5–2× | Comedy crash punch on the button (4K only) | [X] | — | FZ, SC, K | 2027-03 |
| N = 136; 495 | Close-up studies: sad faces raised mental-state attribution; close-up frequency | [L] | [Rooney & Bálint 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC5776141/), [Bálint 2020](https://research.vu.nl/en/publications/shot-scale-matters-the-effect-of-close-up-frequency-on-mental-sta/) | FZ | 2027-03 |
| 12,842 | Douyin marketing videos: engagement higher when visual variation matched vocal arousal | [L] observational | [Yang et al. 2025](https://doi.org/10.3390/jtaer20020069) | FZ, BR, FX | 2027-03 |
| 20 s | "Zoom cuts every 20 seconds" (a timer anti-pattern, one user's report) | [V] anecdote | [Reddit](https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/) | FZ | 2027-03 |
| <~1.2× | Podcast punch-ins from 1080p | [X] | — | PS | 2027-03 |

## 6. B-roll

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 6.0% (733,762 of 13.5M) | Short clips using any b-roll, Jan–Mar 2026 (prevalence, not effect) | [V] | [OpusClip b-roll](https://www.opus.pro/research/broll-visual-effects-short-form) | BR | 2027-03 |
| 2.4% (297,434); 12.2M | Clips using any transition; the 12.2M visual-effects subset | [V] | OpusClip b-roll (above) | FX, TG | 2027-03 |
| 1–3 s | Full-screen cutaway hold | [V] guidance | [AutoClip](https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers) | BR, K | 2027-03 |
| 3–5 s | Ceiling before focus drifts (recommended, not measured); critic flags >5 s outside a demo | [V] | OpusClip b-roll | BR, CR, K | 2027-03 |
| 0.5–1.5 s | Meme, reaction flash or visual gag | [I] | research_broll.md | BR, SC, K | 2027-03 |
| ~0.5 s; 13–80 ms | Floor for a named literal object; named-picture detection (contested) | [L] → [X] | [Potter 2014](https://link.springer.com/article/10.3758/s13414-013-0605-z) | BR, CR, K | 2027-03 |
| ≤2 frames early; ≤~45 ms late | Insert entry around the keyword onset | [X] from [L] | ITU-R BT.1359 | BR, K | 2027-03 |
| 15–25%; ~⅓ | Runtime share when a style uses b-roll; beyond this it stops feeling like a conversation | [V] + [X] | AutoClip | BR, K | 2027-03 |
| ~1.5–3 s (vendor 3 s) | No full-screen cutaway in the hook | [V] | AutoClip | BR, SH, K | 2027-03 |
| ~2 s | The face returns this soon after a visual hook | [X] | — | BR, K | 2027-03 |
| ≥0.25 s | Insert on each side of a seam it covers | [X] | — | BR, K | 2027-03 |
| ≥~1.5 s | Face between full-screen inserts, unless a montage | [X] | Guo 2014 ("jarring" switching) | BR, K | 2027-03 |
| >~3 s | Use a face-kept layout when an insert runs longer or must be read | [L] + [V] | [Kizilcec 2014](https://rene.kizilcec.com/wp-content/uploads/2014/01/final_version2.pdf) | BR, ED, K | 2027-03 |
| ~40–45%; 11 in 77 s; n = 4 | Abdaal's persistent top panel and its changes | [V] | PandaStudio Abdaal | BR, K | 2027-03 |
| 40–50% | Split-screen content on top; above ~50% the eyes fall into the bottom UI band | [V] + [X] | PandaStudio Abdaal; `platforms.md` band | BR, EX, K | 2027-03 |
| ≥⅓ | Subject size in the frame or inset | [P] | research_broll.md | BR, BS, K | 2027-03 |
| ≤0.5 | Grade-transfer strength (exposure, WB, contrast only) | [I] | critique_feasibility.md; [color-matcher](https://github.com/hahnec/color-matcher) | BR, BS, CL, K | 2027-03 |
| 5–10% | Push over a still's hold | [X] | — | BR, K | 2027-03 |
| 0.5–0.8 s | Inserts in a deliberate montage | [X] | — | BR, K | 2027-03 |
| 2–3 | Pickup shots to request from the creator | [X] | — | BR, BS, PS, K | 2027-03 |
| g = −0.33; 68 effects, 58 papers, n = 7,521; g = −0.48 | Seductive details lower learning; photos as seductive details | [L] | [Sundararajan & Adesope 2020](https://link.springer.com/article/10.1007/s10648-020-09522-4) | SK, BR, BS, TG, CR, ED | 2027-03 |
| d = 1.35; 13 tests | Multimedia principle: relevant pictures with words | [L] | Mayer 2023 | BR | 2027-03 |
| 16 of 17, d = 0.58; d = 0.19, 3 of 7 negative | Embodiment principle; image principle | [L] | Mayer 2023 | BR | 2027-03 |
| 8 of 8, d = 1.31 | Temporal contiguity | [L] | Mayer 2023 | BR, ED | 2027-03 |
| 9 of 9, d = 0.82 | Spatial contiguity | [L] | Mayer 2023 | ED | 2027-03 |
| 11 of 12, d = 0.71 | Visual signalling | [L] | Mayer 2023 | BR | 2027-03 |
| n = 22; ~41%; 3.7 s | Face-plus-slides: time on the face; switch interval | [L] | Kizilcec 2014 | BR, ED | 2027-03 |
| 6.9M | edX sessions: face-plus-slides beat slides alone | [A] | [Guo 2014](https://dl.acm.org/doi/10.1145/2556325.2566239) | BR | 2027-03 |
| 2,511 | TikTok product videos: shot count showed an inverted U | [L] observational | [Xiao, Li & Mou 2026](https://www.emerald.com/intr/article/36/1/154/1255369/Exploring-user-engagement-behavior-with-short-form) | BR | 2027-03 |
| +65%; +25% | Product visible: brand affinity and recall (TikTok/Lumen 2021, ads) | [A] ads | [TikTok for Business](https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads) | BR | 2027-03 |
| 0–4 / 0–2 / 0–2 / 0–2 / 0–1 per item / 2–5 / 1–3 | Inserts per 60 s: educational, storytime, comedy, hot take, listicle, sales, founder | [X] | design_doctrine.md §17 | BR, ED, SC, PS, K | 2027-03 |
| 0–15% / 0–10% / 0–5% / 0–10% / 10–30% / 30–70% / 20–40% / 5–15% | Full-screen (or screen) share by the same styles, tutorial as split or PiP | [X] | design_doctrine.md §17 | BR, ED, SC, PS, K | 2027-03 |
| 0 (n = 3); ~95% (n = 4) | Hormozi's b-roll; Abdaal's face time | [V] | PandaStudio | BR, ED, PS, SC | 2027-03 |
| 7–8%; 1,135,817 posts; 8 experiments | Likes lost to an AI label | [L] | [Carney, Riveros & Tully, JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493) | BR, BS, PL, PS, K | 2027-03 |

## 7. B-roll sourcing and licensing

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 5–10 | Concrete queries per beat | [I]; [L] expansion | research_broll.md; [Jagerman 2023](https://arxiv.org/abs/2305.03653) | BS, K | 2027-03 |
| 50–200 → ~20 → ≤8 | Candidate funnel: candidates, embedding top-k, Claude-judged | [I] | design_toolchain.md, critique_simplicity.md | BS, K | 2027-03 |
| 3–5; 0.25–0.5 s | Frames embedded per candidate; gate sampling interval | [I] | critique_feasibility.md | BS, K | 2027-03 |
| 4–6 | Contact-sheet frames per candidate, spread across the used range | [I] | house procedure (broll-sourcing.md) | BS, K | 2027-03 |
| ≥4/5 | Accept bar on every rubric item | [I] | design_doctrine.md | BS, K | 2027-03 |
| <5% | Wrong-subject rate target | [I] | design_architecture.md | BS, K | 2027-03 |
| ≤3 | Rounds (requery, reroute, generate) before refusing | [I] | design_architecture.md | BS, K | 2027-03 |
| 66 of 80 | Reordering alone let a weaker model beat a stronger one (LLM judge position bias) | [L] | [Wang 2023](https://arxiv.org/abs/2305.17926) | BS, CR | 2027-03 |
| ≤1.25×; ~1.5× | Upscaling without super-resolution; the creator's own footage | [X] / [I] | critique_feasibility.md | BS, K | 2027-03 |
| 1080×960 | A split half HD fits | [X] arithmetic | — | BS | 2027-03 |
| ≥30 fps | Stock frame rate for motion in a 60 fps timeline (24/25 judders) | [X] | — | BS, K | 2027-03 |
| 203 cd/m² | SDR inserts' diffuse white in an HDR master | [P] | [ITU-R BT.2408](https://www.itu.int/pub/R-REP-BT.2408) | BS, CL, PL, K | 2027-03 |
| 238 wpm (~0.25 s/word) | Silent reading rate | [L] | [Brysbaert 2019](https://doi.org/10.1016/j.jml.2019.104047) | BS, CT, K | 2027-03 |
| 0; ≤1–2 | Photoreal AI inserts by default; flagged hero shots at most | [I] | design_doctrine.md | BS, K | 2027-03 |
| 500,000 | Shutterstock Standard video audience cap | [A] | [Shutterstock](https://www.shutterstock.com/help/en/articles/10617046-what-usage-is-permitted-with-the-shutterstock-video-license) | BS | 2027-03 |
| 200/h, 20k/month; 100 per 60 s, ≤24 h | Pexels and Pixabay API limits and cache | [A] | [Pexels API](https://www.pexels.com/api/documentation/), [Pixabay API](https://pixabay.com/api/docs/) | BS | 2027-03 |
| 2 Aug 2026 | EU AI Act deepfake disclosure applies | [A] | [Art. 50](https://artificialintelligenceact.eu/article/50/) | BS | 2027-03 |
| 24 Sep 2026 | Sora API removed | [A] | [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations) | BS | 2027-03 |
| 30 Jun 2026 | Tenor API shut down | [A] | [9to5Google](https://9to5google.com/2026/06/30/google-tenor-api-gif-updates/) | BS | 2027-03 |
| §4.6 | Kling clause requiring written permission | [A] | [Kling policy](https://kling.ai/docs/user-policy) | BS | 2027-03 |

## 8. Captions and on-screen text

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 80.2%; 78.6%; 13.5M | OpusClip clips captioned; animated (tool adoption, not performance) | [V] | [OpusClip captions](https://www.opus.pro/research/best-caption-strategy-short-form) | CT | 2027-03 |
| 6,606 | Douyin brand videos: subtitles correlated negatively with comments and shares; vocal music positively with engagement | [L] observational | [Int J Advertising 2026](https://www.tandfonline.com/doi/full/10.1080/02650487.2026.2670858) | CT, MU | 2027-03 |
| 2–4 words | Caption page (one-word pages for hook, numbers, punchlines) | [X] + [V] | [Submagic](https://www.submagic.co/blog/how-to-make-alex-hormozi-captions), [Remotion](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/index.tsx) | CT, K | 2027-03 |
| ≤15 chars, 2 lines, 4–6 words; 1.2 s | Submagic's Hormozi recipe; Remotion template paging interval | [V] | Submagic; Remotion | CT | 2027-03 |
| 1 line; n = 211; up to 3 | One line (two only in the sentence style); vertical-video subtitle study; BBC's 9:16 allowance | [L]; [P] | [Li 2026](https://onlinelibrary.wiley.com/doi/10.1002/acp.70262), [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) | CT, K | 2027-03 |
| ≤~20 chars; 25 chars = 90% of width (972 px); line height ≤4.5% (≈86 px); ≈690 px ≈ 18 chars | Line length and BBC's 9:16 geometry | [P] | BBC | CT, K | 2027-03 |
| 12, 16, 20 CPS | Rates at which comprehension was equal | [L] | [Szarkowska 2018](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331) | CT | 2027-03 |
| 20 CPS | Adult caption rate cap (monitored; re-page or reduce speed, never delete words) | [P] | [Netflix](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide) | CT, SP, CR, K | 2027-03 |
| 160–180 wpm; 0.3 s/word | BBC subtitle rate; minimum on-screen time per word | [P] | BBC ([summary](https://www.clevercast.com/bbc-subtitling-guidelines/)) | CT, SH, TG, ED, K | 2027-03 |
| ~145 wpm; ~170 wpm; n = 578 | Caption rate judged comfortable; where trouble began | [L] | [Jensema 1998](https://pubmed.ncbi.nlm.nih.gov/9842059/) | CT | 2027-03 |
| ≥0.5 s; 0.25 s; 20 frames (0.83 s) | Page minimum; punch-page minimum; Netflix's standalone floor (too long for synced pages) | [X]; [P] | [Netflix timing](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines) | CT, SC, K | 2027-03 |
| 2 frames; <0.5 s | Gap between pages; close shorter gaps | [P] | Netflix timing | CT, K | 2027-03 |
| 70–100 ms; <125 ms; 1–2 frames | Page and highlight lead before the measured onset; lead ceiling; Netflix's on-onset tolerance | [X] on [L]; [P] | ITU-R BT.1359; Netflix timing | CT, K | 2027-03 |
| ±80 ms | Snap window to an acoustic onset | [X] | — | CT | 2027-03 |
| ~4 frames | A page change this close after a seam moves onto the seam | [X] | — | CT, K | 2027-03 |
| 0.3–0.5 s; ≤0.3 s; ≥0.5 s | End-of-run hold; clear on a dramatic pause; Netflix's preference | [X] on [P] | Netflix timing; BBC | CT, K | 2027-03 |
| 700–900; 600–700 | Caption font weight; the sentence style | [P] | [DCMP](https://dcmp.org/captioningkey/print) | CT, K | 2027-03 |
| 64–96 px; 110–140 px; 120 px | Phrase-page and punch-page sizes; Remotion template | [X] | [Remotion Page.tsx](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/Page.tsx) | CT, K | 2027-03 |
| 8–12% | Stroke width as a share of font size | [P] | [Remotion docs](https://www.remotion.dev/docs/captions/displaying) | CT, K | 2027-03 |
| ≤1 per page; ≤~25% of pages; z ≥ ~+1.5; 39 | Accent words; prosody gate; DHH participants in Caption Royale | [X] + [P] + [L] | BBC; [Caption Royale](https://dl.acm.org/doi/10.1145/3613904.3642258) | CT, ED, K | 2027-03 |
| g = 0.53; 103 studies | Signalling helps retention | [L] | [Schneider 2018](https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581) | CT, TG, ED | 2027-03 |
| 26 of 28, d = 0.70 | Signalling (Mayer's count; verbal and visual cues) | [L] | Mayer 2023 | FX | 2027-03 |
| 8 of 12, d = 0.10 | Redundancy: small cost of on-screen text that repeats narration | [L] | Mayer 2023 | ED | 2027-03 |
| 100–200 ms; ≥0.9; ≤1.1×; 0.8→1, 50 px | Page entry animation, starting scale, current-word scale; Remotion template's heavier default | [X] on [P] | [Remotion SubtitlePage](https://github.com/remotion-dev/template-tiktok/blob/main/src/CaptionedVideo/SubtitlePage.tsx) | CT, K | 2027-03 |
| ≤3 flashes/s | Flash limit | [P] standard | [WCAG 2.3.1](https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html), [ITU-R BT.1702](https://www.itu.int/rec/R-REC-BT.1702) | CT, TG, FX, CR, K | 2027-03 |
| 0; ≤~1 per 10 s; 2.5× | Emoji by default; in playful styles; ads with emoji likelier top-20% purchase intent (no method) | [L]; [A] | [McDonnell CHI 2024](https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf), [Meta/Toluna](https://www.facebook.com/business/news/reels-creative-strategies) | CT, K | 2027-03 |
| 40–120 px; n = 40 | Caption top edge below the chin (after transforms); speaker-following caption study | [X]; [L] | [Kurzhals 2017](https://dl.acm.org/doi/10.1145/3025453.3025772) | CT, K | 2027-03 |
| ~300 ms | Page break at gaps this long | [X] | — | CT | 2027-03 |
| >200 wpm | Very fast talkers get larger two-line pages | [X] | — | CT | 2027-03 |
| 0.3 s/word + 1 s | Static text dwell for titles, cards, labels, text-bearing inserts (the +1 s is our margin) | [P] rate + [X] margin | BBC | CT, SH, BR, BS, TG, ED, EN, PS, K | 2027-03 |
| ~2–3.5 s | Hook-title dwell | [X] | — | CT | 2027-03 |
| 1–3 words; ≤1 per 5–10 s; d = 0.47–0.70 | Callouts; labels beside a diagram improved retention | [X]; [L] | [Mayer & Johnson 2008](https://doi.org/10.1037/0022-0663.100.2.380) | CT, TG, K | 2027-03 |
| 3–5 s; 4–6 words; 2.2–2.8 s | Lower third once; name plus role; its dwell minimum | [X] | BBC | CT, K | 2027-03 |
| 5–10 words/s (300–600 wpm) | TikTok ad-guide text rate; ignored for captions | [A] | TikTok creative best practices | CT | 2027-03 |
| n = 9; n = 49 | TikTok caption interviews; language-learner highlight study | [L] | McDonnell 2024; [Draxler 2023](https://arxiv.org/abs/2307.05870) | CT, TG | 2027-03 |
| 1–3 words; 3–5 per clip | Podcast caption pages; emphasis words per clip | [V] | PandaStudio DOAC | PS, K | 2027-03 |
| white, yellow, cyan, green | Speaker colour order | [P] | BBC | PS | 2027-03 |
| ≥~45 px (~50 px); ≥~30 px; 17 pt; 11 pt; 375–430 pt | Legible screenshot text at 1080×1920; floor; iOS default and minimum; phone widths | [X] from [P] | [Apple HIG](https://developer.apple.com/design/human-interface-guidelines/typography) | ED, EX, K | 2027-03 |

## 9. Transitions and graphics

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| ≥90% | Seams that are hard or J/L cuts (100% is normal) | [V] | [Superdirector](https://superdirector.app/learn/transitions-cuts) | TG, K | 2027-03 |
| 0; ~2 | Stylized transitions by default; at most, at section boundaries | [P] / [V] | design_doctrine.md; Superdirector | TG, K | 2027-03 |
| 6–12 frames (0.2–0.4 s) | Synthetic whip pan | [P] + [X] | design_doctrine.md | TG, K | 2027-03 |
| 2–4 frames | Flash or dip to white | [X] | — | TG, K | 2027-03 |
| 4–8 frames | Zoom transition | [X] | — | TG, K | 2027-03 |
| 8–15 frames; 13% | Dissolve (never inside a talking head); a 2 s dissolve in a 15 s clip | [P] + [X]; [V] | [Adobe](https://www.adobe.com/creativecloud/video/post-production/transitions.html), Superdirector | TG, K | 2027-03 |
| 1–3 frames; ~0.5 s | J-cut audio lead; at a section change | [V] + [X] | Superdirector | TG, K | 2027-03 |
| 100–500 ms; 200–300 ms; 150–240 / 400 ms | UI animation durations; substantial changes; Carbon's moderate and large | [P] | [NN/g](https://www.nngroup.com/articles/animation-duration/), [Carbon](https://github.com/carbon-design-system/carbon/blob/main/packages/motion/src/dtcg/motion.json) | TG, K | 2027-03 |
| 5–9 frames (170–300 ms); 0–2 frames; 3–6 frames | Graphic entrance and lead before the trigger word; exit | [X] on [P] | NN/g | TG, K | 2027-03 |
| (0, 0, 0.3, 1); (0.05, 0.7, 0.1, 1); (0.4, 0.14, 1, 1); (0.3, 0, 0.8, 0.15) | Entrance and exit easing curves | [P] / [V] | Carbon; [Material](https://github.com/material-components/material-web/blob/main/tokens/versions/v0_192/_md-sys-motion.scss) | TG | 2027-03 |
| 0–5%; damping 10; damping 200, ~23 frames | Scale overshoot; Remotion's bouncy default spring; the settling spring at 30 fps | [P] / [V] | [Remotion](https://www.remotion.dev/docs/transitions/timings) | TG, K | 2027-03 |
| 2–4; 6–10; 6; ~6 frames | Stagger within a graphic; arrow draw-on; gap between elements; land on a seam within | [X] | — | TG, K | 2027-03 |
| 1 | Changing elements at once, apart from caption paging | [X] + [L] | [Pieters 2010](https://doi.org/10.1509/jmkg.74.5.048) (249 print ads) | TG, CT, K | 2027-03 |
| 16.6×; 11.1× | Looks drawn by faces and by text versus matched regions | [L] | [Cerf 2009](https://doi.org/10.1167/9.12.10) | TG, CR | 2027-03 |
| 26 studies | Animation beat static pictures by a medium margin | [L] | [Höffler & Leutner 2007](https://doi.org/10.1016/j.learninstruc.2007.09.013) | TG | 2027-03 |
| 32 experiments | Progress indicators: constant bars did not cut drop-off; slow-to-fast raised it | [L] other domain | [Villar 2013](https://doi.org/10.1177/0894439313497468) | TG, ED | 2027-03 |
| 1 display face, 1 accent | Brand kit (plus the caption face, white/black, one motion preset per element) | [X] on [P] | [NN/g consistency](https://www.nngroup.com/articles/consistency-and-standards/) | TG | 2027-03 |

## 10. Music

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 16–20 LU; 12–25 LU; 18–25 dB; ~6–15 | Music under speech: starting level, working range; vendor figures that disagree (Zella; Pixflow) | [V] + [I] | [Zella](https://zellahq.com/blog/music-ducking-explained/), [Pixflow](https://pixflow.net/blog/audio-mixing-premiere-pro/), research_audio_music_sfx.md | MU, CR, ED, SC, PS, EX, K | 2027-03 |
| ≥10 LU; ≥15 LU; 4 LU; n = 22 | Separation floor for commentary over music; over ambience; non-experts wanted 4 LU more (TV) | [L] | [Torcoli 2019](https://doi.org/10.17743/jaes.2019.0052) | MU, FX, ED, CR, K | 2027-03 |
| ~5.7 LU; n = 20 | Interquartile spread of preferred speech-to-background difference | [L] | [Resti 2023](https://arxiv.org/abs/2305.19100) | MU, K | 2027-03 |
| 12–15 LU | A sparse bed that vanishes on phones may sit this close | [X] | — | MU, K | 2027-03 |
| 3 s; 0.4 s | BS.1770 short-term and momentary windows | [P] | [EBU Tech 3341](https://tech.ebu.ch/docs/tech/tech3341.pdf) | MU, VL | 2027-03 |
| 50–300 ms; 100–250 ms | Duck attack; pre-duck before the first word | [V] + [X] | Zella | MU, K | 2027-03 |
| <~1 s; 0.5–1.0 s | Hold the duck through short gaps; release | [P] + [X] | [Sound On Sound](https://www.soundonsound.com/techniques/live-side-chain-compression) | MU, K | 2027-03 |
| +4–8 dB; ≥1.2 s | Swell, only in gaps this long, under voiceless b-roll and the outro | [X] | research_audio_music_sfx.md | MU, K | 2027-03 |
| 2–4 dB at 1–4 kHz | Dynamic dip on the music under speech | [V] band + [X] depth | Pixflow | MU, K | 2027-03 |
| ±1 frame; ~20 ms; ~45 ms early | Hit-point tolerance; temporal-order threshold; visual-event hits | [X] on [L] | [Hirsh 1959](https://doi.org/10.1121/1.1907782), ITU-R BT.1359 | MU, K | 2027-03 |
| 1–3; 0.5–2 s; 0.3–1.0 s | Hits, stingers and dropouts per short; stinger and dropout lengths | [X] | — | MU, CR, EX, K | 2027-03 |
| ≤0.5 s | Music tail after the last word | [I] + [P] | design_doctrine.md §16; [PremiumBeat](https://www.premiumbeat.com/blog/timing-music-for-video-editing/) | MU, EN, K | 2027-03 |
| 60–90 / 85–105 / 100–125 BPM | Tempo priors: calm, conversational, high-energy | [X]; [L] tempo drives arousal | [Husain 2002](https://online.ucpress.edu/mp/article-abstract/20/2/151/62120/) | MU, K | 2027-03 |
| 15 dB; 0–5 dB | Sung lyrics hurt spoken-word recognition even 15 dB under speech; busier arrangements hurt only at 0–5 dB | [L] | [Scharenborg & Larson 2018](https://www.isca-archive.org/interspeech_2018/scharenborg18_interspeech.pdf) | MU | 2027-03 |
| d ≈ −0.3; d ≈ −0.2 | Lyrics' cost to memory and reading | [L] | [Souza & Barbosa 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10162369/) | MU | 2027-03 |
| −5 dB SNR | Condition under which familiar songs hurt recognition more (direction only) | [L] | [Brown & Bidelman 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9562996/) | MU | 2027-03 |
| ~2.5; ~5 | Simultaneous sounds of one kind audiences track; total across the spectrum | [P] | [Murch, Dense Clarity](https://transom.org/2005/walter-murch/) | MU, FX | 2027-03 |
| >~185 wpm | Lean to no music above this rate | [X] | — | MU, K | 2027-03 |
| a few percent (~3%) | Maximum music stretch | [X] | — | MU, K | 2027-03 |
| 1 s–5 min | Epidemic Version lengths | [V] | [Epidemic](https://developers.epidemicsite.com/docs/soundtracking-with-llm/) | MU | 2027-03 |
| 3 s–10 min; 30 chunks; 3–120 s | ElevenLabs Music lengths; plan chunks and chunk length | [V] | [ElevenLabs](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans) | MU | 2027-03 |
| 4 or 8 bars | Music edit points at phrase boundaries | [P] | PremiumBeat | MU | 2027-03 |
| 1–2 s; 3 | Tail on a generated bed; candidates or seeds | [X] | — | MU, K | 2027-03 |
| +61%; +177% | Creator ads with music: recall and purchase intent (ads) | [A] ads | [CreatorIQ × TikTok](https://www.creatoriq.com/press/releases/tiktok-creatoriq-release-special-report-with-data-backed-keys-to-success-for-advertisers?hs_amp=true) | MU, PS | 2027-03 |
| 90 s (some 30–60 s); 24 Sep 2026 | Shorts music usable via YouTube's tools; claimed 1–3 min Shorts monetise only once resolved | [A] | [YouTube Help](https://support.google.com/youtube/answer/15424877) | MU, PL, K | 2027-03 |
| 0–100 | Instagram Audio API volume scale, no documented dB mapping | [A] | [Instagram Audio API](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/) | MU, PL | 2027-03 |
| ~1M | TikTok Commercial Music Library tracks | [A] | [TikTok CML](https://ads.tiktok.com/help/article/commercial-music-library) | PS | 2027-03 |

## 11. Sound effects

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| ±1 frame (±33 ms at 30 fps) | Transient sync; late before early | [L] + [P] + [V] → [X] | ITU-R BT.1359; [Dixon & Spitz 1980](https://doi.org/10.1068/p090719); [Descript](https://www.descript.com/blog/article/how-to-add-sound-effects-to-a-video-2) | FX, K | 2027-03 |
| ≤2 frames; 2–4 frames | Whoosh peak before the cut; onset lead for a short swish | [P] weak | research_audio_music_sfx.md §7 | FX, K | 2027-03 |
| 0.8–2.0 s | Riser, ending on a real reveal | [P] + [X] | research_audio_music_sfx.md §7 | FX, K | 2027-03 |
| ≥4–5 s | Spacing between sounds (rhythmic sets excepted) | [P] weak | research_audio_music_sfx.md §7 | FX, K | 2027-03 |
| ≤2 | Non-voice layers at once (bed plus one SFX) | [X] from [P] | Murch | FX, K | 2027-03 |
| −10 to −20 dB; −9 to −18 dB; −6 to −24 dB | Vendor reference SFX and dialogue levels; spread of published advice | [V] | [Epidemic Sound](https://www.epidemicsound.com/blog/audio-mixing-for-video/) | FX | 2027-03 |
| 4–10 dB; ≥10 LU; ~15 LU; ~6 LU | Transients under the local speech peak; textures under speech, continuous ones; in a clean gap | [X]; floors [L] | Torcoli 2019 | FX, K | 2027-03 |
| +5 LU; −18 vs −23 LUFS | Short-term ceiling above target; EBU R 128 s1 for adverts and promos | [P] | [EBU R 128 s1](https://tech.ebu.ch/docs/r/r128s1.pdf) | FX, VL, K | 2027-03 |
| 20 dB; 1–2 s | Background under speech for audio-only content (AAA); occasional sounds exempt | [P] | [WCAG 1.4.7](https://www.w3.org/WAI/WCAG21/Understanding/low-or-no-background-audio.html) | FX | 2027-03 |
| 2–4 dB; ±150 ms | Extra duck on the bed under an SFX; drop the SFX near a music hit | [X] | — | FX, K | 2027-03 |
| ≤1 semitone; 1–2 dB | Variation for repeated action sounds | [P] + [X] | [Unity](https://docs.unity3d.com/6000.0/Documentation/Manual/AudioRandomContainer-fundamentals.html) | FX, K | 2027-03 |
| 8 of 9 | Auditory features that drew orienting responses (radio) | [L] | [Potter, Lang & Bolls 2008](https://doi.org/10.1027/1864-1105.20.4.168) | FX | 2027-03 |
| 0–2 / 0–1 / 0–3 / 0–1 / 0 or 1 per item / 0–4 / 0 / 0–4 / 0–2 / 0–2 | SFX per 60 s: educational, storytime, comedy, hot take, listicle, tutorial, podcast, sales, founder, premium | [X] | design_doctrine.md §17 | FX, ED, SC, PS, K | 2027-03 |
| 1–2 (3 max) | Sound families per video | [X] | — | FX, K | 2027-03 |
| 2–3 frames | Optional flash with a shutter on a real screenshot | [X] | — | FX | 2027-03 |
| ≥15 LU; 2–4 frames | Foley under the voice; its J-cut lead | [P]; [X] | [StringLabs](https://stringlabscreative.com/avoid-these-common-stock-footage-mistakes-that-make-videos-feel-cheap/) | FX, K | 2027-03 |
| ≥150 ms | Gaps that make a transient safe to place | [X] | — | FX, K | 2027-03 |
| ~150 ms | Comedy button after the punch word | [X] | — | FX, SC, K | 2027-03 |
| >~1 dB | Limiter pull that marks an SFX as too hot | [X] | — | FX, K | 2027-03 |
| ~200 Hz | Phone-speaker simulation high-pass | [X] | — | FX, K | 2027-03 |
| 4–8 | Generated SFX candidates | [X] | — | FX, K | 2027-03 |
| 250k+ | Epidemic Sound SFX library | [V] | [Epidemic Partner API](https://developers.epidemicsound.com/) | FX | 2027-03 |
| 0.6B; 806k; 473k; USD 1M | Stable Audio 3 Small-SFX size, training sets, Community License revenue cap | [V]; [A] | [Model card](https://huggingface.co/stabilityai/stable-audio-3-small-sfx), [licence](https://stability.ai/license) | FX | 2027-03 |
| 0.1–30 s; 48 kHz | ElevenLabs SFX length; WAV only for non-looping | [V] | [ElevenLabs SFX](https://elevenlabs.io/docs/overview/capabilities/sound-effects) | FX | 2027-03 |

## 12. Voice processing and loudness

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 16 kbps; ~1 kbps; 1 fps | Gemini audio-file input; a video's soundtrack; video frame sampling | [V] | [Gemini audio](https://ai.google.dev/gemini-api/docs/audio), [video](https://ai.google.dev/gemini-api/docs/video-understanding) | VL, FX, CR, K | 2027-03 |
| ≥25 / 20–25 / 10–20 / <10 dB; ±4 dB | Denoise bands by median speech SNR (none; light 6–12 dB; 12–18 dB; 18–24 dB); fuzzy edges | [X]; calibrate [I] | [Brouhaha](https://arxiv.org/abs/2210.13248) | VL, K | 2027-03 |
| 0–30 dB; ~4 dB; 1.1 dB; 5 rooms | Brouhaha's training SNR range; frame SNR error; C50 error | [L] | Brouhaha | VL | 2027-03 |
| ≥20 / 10–20 / <10 dB | Dereverb bands by C50 | [X] | Brouhaha | VL, K | 2027-03 |
| 6–24 dB (≤24); 12 dB | Denoise attenuation limit; ffmpeg afftdn default | [I] + [V] | [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py), [FFmpeg](https://ffmpeg.org/ffmpeg-filters.html#afftdn) | VL, K | 2027-03 |
| +1 point; ~10%; ≤1 ms | Restoration gates: WER rise, spectral-rolloff change, lag | [I]; [X] | design_doctrine.md §13; [DNSMOS](https://arxiv.org/abs/2110.01763) | VL, K | 2027-03 |
| 3 samples ≥ −0.1 dBFS | Clipping flag inside a kept word | [X] | — | VL, K | 2027-03 |
| 80–100 Hz, 12 dB/oct; 85; 110–120 Hz; ~90–155 / ~165–255 Hz | High-pass start; for thin and boomy voices; adult male and female F0 | [P] | [NPR](https://www.npr.org/sections/npr-training/2025/05/31/g-s1-67902/the-producers-handbook-to-mixing-audio-stories), [Sound Radix](https://www.soundradix.com/articles/mixing-dialogue-in-audio-storytelling/), [Voice frequency](https://en.wikipedia.org/wiki/Voice_frequency) | VL, K | 2027-03 |
| 200–400 Hz (to 500), 2–4 dB | Mud cut, only on a measured bump | [V] + [X] | [Orphiq](https://orphiq.com/resources/vocal-eq-cheat-sheet) | VL, K | 2027-03 |
| 2–5 kHz, 0 to +3 dB | Presence, only if dulled | [V] + [X] | Orphiq (3–5 kHz) | VL, K | 2027-03 |
| ~3–6 / ~5–8 kHz; 2–6 dB | De-ess start bands (male, female); depth | [V] + [X] | [Apple Logic Pro](https://support.apple.com/en-us/102006) | VL, K | 2027-03 |
| 1.5:1–2:1; 2–3 dB (5–6 on emphasis); ~10 ms; 100–150 ms; 3:1–4:1, 4–6 dB | Compression ratio, gain reduction, attack, release; dense delivery over music | [P] | NPR (11/110 ms), [NPR FAQ](https://npr.org/g-s1-65503) (1.5:1, 3–5 dB), [Transom](https://transom.org/2015/podcasting-basics-part-3-audio-levels-and-processing/) | VL, K | 2027-03 |
| ±2–3 dB; ±1.5 LU | Leveler range; adjacent segments (and podcast speakers) short-term | [P] + [X] | Sound Radix | VL, PS, K | 2027-03 |
| ~15–20 dB | Loud breaths pulled below neighbouring speech | [V] + [X] | [iZotope Breath Control](https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html) | VL, K | 2027-03 |
| 600 ms vs 300 ms | Inhalations that did (and did not) aid transcription and recall (synthetic speech) | [L] | [Whalen 1995](https://pubmed.ncbi.nlm.nih.gov/7759655/), [Elmers 2021](https://www.isca-archive.org/interspeech_2021/elmers21_interspeech.html) | VL, K | 2027-03 |
| ~1–2 dB | Room-tone bed level against the processed floor | [P] + [X] | [Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise) | VL, K | 2027-03 |
| 5 ms / 15 ms; ≤1 ms | EBU R37 per-stage lip-sync allowance (early / late); our limit per neural stage | [P]; [I] | [EBU R37](https://tech.ebu.ch/docs/r/r037.pdf) | VL, K | 2027-03 |
| −14 LUFS; ±0.5 LU aim; ±1 LU gate | Integrated loudness after the AAC encode (a platform-matching choice, not a standard) | [I]; gate is a validator | design_doctrine.md §13; ARCHITECTURE.md §7 | VL, PL, MU, CR, SK, K | 2027-03 |
| ≤ −1 dBTP; −1.5 dBTP; −1.5 to −2 | True peak after encode; limiter ceiling; for dense mixes | [P] + [A] | [AES TD1008](https://aes.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf), [Spotify](https://support.spotify.com/us/artists/article/loudness-normalization/) (below −2 when louder than −14) | VL, PL, MU, CR, K | 2027-03 |
| ≤ integrated +5 LU (−9 LUFS at −14) | Maximum short-term loudness | [P] → [X] | EBU R 128 s1 | VL, FX, K | 2027-03 |
| ~1 min | Do not target Loudness Range below this (or on short-form) | [P] | [EBU R 128](https://tech.ebu.ch/docs/r/r128.pdf) | VL | 2027-03 |
| ≤~2–3 dB | Limiter work on peaks | [X] | — | VL, K | 2027-03 |
| −18 LUFS (+1 LU); −20 to −16; −16 ±1 | AES TD1008 speech streams; EBU R 128 s2 interim; Apple Podcasts | [P] + [A] | TD1008; [R 128 s2](https://tech.ebu.ch/docs/r/r128s2.pdf); [Apple](https://podcasters.apple.com/support/893-audio-requirements) | VL, PL, MU, CR, K | 2027-03 |
| −14 LUFS (since 2019) | YouTube turns louder content down, never quieter content up | [P] measurement | [Production Advice](https://productionadvice.co.uk/youtube-loudness/) | VL, PL | 2027-03 |
| −16 LUFS | APU's "conservative production convention" for TikTok and Reels | [V] | [APU](https://apu.software/tiktok-instagram-reels-loudness/) | VL | 2027-03 |
| −16; −12 to −11 | A/B alternative for dense or dynamic material; defensible if a platform proves not to normalise | [X] | — | VL, PL, K | 2027-03 |
| −8, −9, −23 LUFS | Mastering targets named as anti-patterns (too loud; too quiet) | [X] | — | VL, PL | 2027-03 |
| 3 dB | Dual-mono measurement difference to remember | [P] | [FFmpeg loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) | VL | 2027-03 |
| ≤~1 dB | Voice level change when folded to mono | [X] | — | VL, K | 2027-03 |
| <200 Hz; ~100 Hz | Phone speakers struggle below 200 Hz and are silent by about 100 Hz | [V] | [LANDR](https://blog.landr.com/make-bass-audible-phone-speakers/) | VL | 2027-03 |

## 13. Colour and look

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 203 cd/m²; 75% HLG; 58% PQ | HDR reference and graphics white | [P] standard | [ITU-R BT.2408-8](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf) | CL, PL, BS, K | 2027-03 |
| ~90% SDR; 95% ceiling | Where HDR reference white lands in the SDR output | [P] / [X] | BT.2408 §7.7, Annex 8 | CL, K | 2027-03 |
| 40% HLG → ~50% SDR | Mid-tone check for the tone map | [P] | BT.2408 §2.2 | CL | 2027-03 |
| 55–65 / 45–60 / 25–45% HLG | Face level before the tone map (light, medium, dark skin): a sanity band | [P] / [X] | BT.2408 Table 2 | CL | 2027-03 |
| 45–70 / 30–60 / 15–35% | Face-mask mean luma after the tone map, by skin lightness | [V] | Van Hurkman via [Larry Jordan](https://larryjordan.com/articles/the-secret-to-setting-skin-colors-accurately/) | CL, K | 2027-03 |
| 74.6% (SD ~6; 713 faces); ~80% | Japanese studio news lit skin; soft ceiling | [P] / [X] | BT.2408 Annex 4 | CL, K | 2027-03 |
| ~123°; ±2°; ±5° | Skin-tone line from +Cb; tolerance; investigate beyond | [V] / [X] | Van Hurkman; [YIQ](https://en.wikipedia.org/wiki/YIQ) | CL, K | 2027-03 |
| 30–40% / 15–20%; >10% | Vectorscope skin saturation (lighter / darker skin); warning above the camera's own | [V] / [X] | Van Hurkman | CL | 2027-03 |
| 0.9–1.1× (±10%); ±15% | Global saturation change; beyond ±15% should be rare | [X] | — | CL, K | 2027-03 |
| ±0.5 stop; ~1 stop | Exposure change per take; beyond, fix partially | [X] | — | CL, EX, K | 2027-03 |
| >5%; ≤2%; code 16 = 0%, 235 = 100% | Shadow detail; pixels at or near code 16; limited-range mapping | [X]; [P] | [FFmpeg signalstats](https://ffmpeg.org/ffmpeg-filters.html#signalstats), BT.2408 §2.4 | CL, K | 2027-03 |
| ΔE2000 ≤ 2; 3 points; ≈2.3 | Take-to-take skin and neutral match; face-luma match; ΔE*ab JND | [L] / [X] | [Color difference](https://en.wikipedia.org/wiki/Color_difference) (Sharma) | CL, EX, K | 2027-03 |
| 0–40% (typ. 20–30%); ±5° | Look LUT strength; skin hue after the look | [I] / [X] | design_doctrine.md §14 | CL, K | 2027-03 |
| >ΔE 5 | Reject an insert whose neutrals stay this far from the A-roll | [I] / [X] | critique_feasibility.md | CL, K | 2027-03 |
| 90–95% | Lowered white for glaring full-screen UI | [X] from [P] | BT.2408 §7.7 | CL, K | 2027-03 |
| CAMBI ≤3; ~5; 24 | Banding target; slightly annoying; unwatchable | [L] | [Netflix CAMBI](https://github.com/Netflix/vmaf/blob/master/resource/doc/cambi.md) | CL, K | 2027-03 |
| 90k; r ≈ 0.07–0.31; SRCC 0.689 → 0.696 | Snap videos: quality scores tracked watch time weakly; aesthetics added little to an engagement model | [A] | [SnapUGC](https://arxiv.org/html/2410.00289v1) | CL | 2027-03 |
| ~64× | A wrong reference-white constant crushed Jellyfin's output | [P] | [Jellyfin #775](https://github.com/jellyfin/jellyfin-ffmpeg/issues/775) | CL | 2027-03 |
| 32 | Vision-language models whose colour perception ColorBench found weak | [L] | [ColorBench](https://arxiv.org/abs/2504.10514) | CL | 2027-03 |
| 20 frames | libplacebo's peak-detection smoothing | [P] | [libplacebo](https://libplacebo.org/options/) | CL | 2027-03 |
| Nov 2025; 8.4 | Instagram iOS preserves Dolby Vision (profile 8.4) | [A] | [Meta 2025](https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/) | CL, PL | 2027-03 |

## 14. Endings, loops and CTAs

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 0.15–0.5 s (target ~0.3 s) | Last kept word to last frame on a face | [I] + [X] | design_doctrine.md §16 | EN, CR, CP, SH, PS, ED, SC, EX, K | 2027-03 |
| 1.5–2 s; n = 13; 1–2 s | Measured top Shorts show a completed state in the last 1.5–2 s; OpusClip's "hold the final frame" | [V] | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws), [OpusClip](https://www.opus.pro/blog/tiktok-length-format-retention-data) | EN, CR, ED, K | 2027-03 |
| 0.3 s/new word + 1 s | Hold past the last word for a held visual | [X] on [P] | BBC | EN | 2027-03 |
| ≤~1.5 s | Memory-listed sign-off; end card on explicit request | [I] + [X] | `backend/eval/pro_cut_reference.py` | EN, K | 2027-03 |
| 0.8–1.5 s | Hold after the last word on an emotional ending | [X] | — | EN, K | 2027-03 |
| ≤1 s; 0.3–1.0 s; 1–2 s | Comedy: end after the button; face hold with no laugh; silent comedy's reaction hold | [I] + [X] | BBC §15 | SC, K | 2027-03 |
| 1.5–2 s | Optional podcast title card (never on emotional clips) | [V] | PandaStudio DOAC | PS, K | 2027-03 |
| 1 (2 if linked) | Asks per ending | [X] | — | EN, K | 2027-03 |
| ≤~35 s | Loop candidate length | [X] | research_style_trends.md | EN, K | 2027-03 |
| 100–250 ms | Loop seam when the last line runs into the first | [X] on [L] | Campione & Véronis | EN, K | 2027-03 |
| 5–10 ms | De-click ramps at a loop seam (no audible fade) | [P] + [X] | Idyll Sounds; ebrary | EN, K | 2027-03 |
| 2020; 2023 | TikTok gives completion "greater weight"; Instagram predicts full watches | [A] | [TikTok](https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you), [Instagram](https://about.instagram.com/blog/announcements/instagram-ranking-explained) | EN | 2027-03 |
| 31 Mar 2025; 24 Aug 2026 | Shorts count a view on every start or replay; all YouTube formats count from playback start | [A] | [PPC Land](https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/), [YouTube](https://support.google.com/youtube/answer/12220281) | EN | 2027-03 |
| >1 min; ≥5 s | TikTok Creator Rewards pays only for videos longer than 1 min; a qualified view is ≥5 s, once per account | [A] | [TikTok newsroom](https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program), [terms](https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en) | EN, PL, ED, SC, K | 2027-03 |
| 62% vs 48%; ~500 | Completion at 21–34 s vs over 60 s (method not shown) | [V] | OpusClip | EN | 2027-03 |
| 355 | Brand Facebook posts: questions raised comments (correlational) | [L] | [de Vries 2012](https://doi.org/10.1016/j.intmar.2012.01.003) | EN | 2027-03 |
| +205% | "Strong written CTAs" (ad-unit copy, not burned-in text) and purchase intent (TikTok 2022, Lumen) | [A] ads | CreatorIQ × TikTok | EN, PS | 2027-03 |
| 2017; 2019 | Facebook demotes engagement bait; bait in a video's audio | [A] | [Meta 2017](https://about.fb.com/news/2017/12/news-feed-fyi-fighting-engagement-bait-on-facebook/), [guidelines](https://transparency.meta.com/features/approach-to-ranking/content-distribution-guidelines/engagement-bait/) | EN | 2027-03 |

## 15. Platforms and delivery (facts checked 27 Sep 2026)

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 1080×1920; ≥540×960 | Delivery frame; TikTok ads minimum | [X] + [A] | [Meta ads guide](https://www.facebook.com/business/ads-guide/update/video/instagram-reels), [TikTok](https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads) | PL, K | 2027-03 |
| 1080p; 1920 px; 360–4096 px | Shorts upload maximum; IG maximum width; TikTok accepted range | [A] | [YouTube](https://support.google.com/youtube/answer/10059070), [IG API](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media), [TikTok API](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) | PL | 2027-03 |
| 23–60 fps | Constant frame rate at the source cadence (mixed 30/60 → 30) | [A] + [X] | IG API, TikTok API, [YouTube encoding](https://support.google.com/youtube/answer/1722171) | PL, K | 2027-03 |
| keyint = fps/2; 2 B-frames | H.264 High, 8-bit 4:2:0, BT.709, TV range, closed GOP, +faststart | [A] | YouTube encoding, IG API | PL, K | 2027-03 |
| CRF 14–16 | Encode quality, capped by maxrate | [I] | research_render_timeline.md | PL, K | 2027-03 |
| min(25 Mbps, 0.9 × 300 MB ÷ duration); ≈25 / ≈12 / ≈3.6 Mbps | IG bitrate cap; at ~85 s, 3 min, 10 min | [A] + [X] | IG API | PL, K | 2027-03 |
| 3 s–15 min | Reels via the API | [A] | IG API | PL, K | 2027-03 |
| 8 / 12 Mbps; 10 / 15 Mbps | YouTube 1080p SDR reference at 24–30 / 48–60 fps; HDR | [A] | YouTube encoding | PL | 2027-03 |
| 48 kHz; 128 / 384 / 256–320 kbps | AAC-LC stereo; IG, YouTube, TikTok (unspecified, our choice) | [A] + [X] | IG API, YouTube encoding | PL, K | 2027-03 |
| 10 min | TikTok maximum via the API | [A] | TikTok API | PL, K | 2027-03 |
| 6.0% / 4.2% / 5.5% (5.5–5.9% past 120 s); 2,200 / 11,136 | TikTok engagement rate by length (15–30, 30–60, 120–180 s); median views at 30–60 and 120–180 s; 6M brand videos, H1 2026 | [A] vendor-run, correlational | [Socialinsider TikTok](https://www.socialinsider.io/blog/how-long-are-tiktok-videos/) | PL, SC, PS | 2027-03 |
| >3 min | Reels outside the Reels tab reach only followers (unconfirmed by Meta) | [V] | — | PL, K | 2027-03 |
| 3 min (15 Oct 2024) | Shorts maximum length | [A] | [YouTube Help](https://support.google.com/youtube/answer/15424877) | PL, K | 2027-03 |
| 15–45 / 30–60 / 30–75 / 30–90 / 30–60 / 45–120 / 15–40 s | Length by style: comedy and hot take, educational, listicle, podcast clip, founder, storytime and tutorial, sales (aim 20–35) | [I] + style files | design_doctrine.md §17; styles/*.md | PL, ED, SC, PS, SK, K | 2027-03 |
| 269 / 672 / 65 / 65 px | Reels safe zone at 1080×1920 (14% / 35% / 6%) | [A] ads spec | [Meta](https://www.facebook.com/business/ads-guide/update/image/instagram-reels) | PL, FZ, CT, TG, K | 2027-03 |
| 288 / 672 / 48 / 192 px (x 48–887, y 288–1247) | Shorts safe zone from Google's vertical-ads overlay; supersedes third-party maps | [A] ads; [X] organic | [Google overlay](https://services.google.com/fh/files/misc/youtubesafezoneoverlay_vertical_final.png) | PL, FZ, CT, TG, K | 2027-03 |
| 380 / 380 / 60 / 120 px | poster.ly's third-party Shorts map (superseded) | [V] | [poster.ly](https://www.poster.ly/tools/youtube-shorts-safe-zone-checker) | CT, TG | 2027-03 |
| ~140–200 / ~250–480 / ~60 / ~120–180 px | TikTok safe zone (varies with format and caption length) | [V] | [Zeely](https://zeely.ai/blog/tiktok-safe-zones/), [CreaMate](https://creamate.ai/en/blog/tiktok-safe-zone-guide), [Jon Loomer](https://www.jonloomer.com/tiktok-instagram-reels-safe-zones-templates/) | PL, FZ, CT, K | 2027-03 |
| x 65–888, y 288–1248 (823×960); titles y 288–600 | Cross-platform house band for text | [I] from [A] | design_doctrine.md | PL, CT, TG, SK, EX, K | 2027-03 |
| x ≈ 476; ~696 px | The band's centre; a caption centred on 540 wider than this runs under the right rail | [X] arithmetic | — | PL, K | 2027-03 |
| ~690 px | Maximum centred caption line | [P] + [X] | BBC | CT, K | 2027-03 |
| y ≈ 1436 | Relaxed organic caption floor, only after an overlay mock shows it clear | [X] | — | CT, PL, K | 2027-03 |
| x 1015; 127 px | IG-only text limit; extra width over the shared band | [X] | — | PL, K | 2027-03 |
| y 240–1680 | Instagram's 3:4 profile-grid crop | [V] | [PetaPixel](https://petapixel.com/2025/01/23/instagram-swaps-square-profile-grids-for-rectangles/) | PL, K | 2027-03 |
| 2160×3840; ≥640 px; ≤50 MB | Custom Shorts cover (Studio desktop, verified accounts) | [A] | [YouTube thumbnails](https://support.google.com/youtube/answer/72431) | PL | 2027-03 |
| 100 / 100; 10–20 (e.g. 12) | IG Audio API defaults for audio_volume / video_volume; our audio_volume for a trending sound | [A]; [X] | Instagram Audio API | PL, K | 2027-03 |
| Jul 2025 | YouTube's inauthentic-content monetisation update | [A] | [Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/) | PL, BS | 2027-03 |
| Nov 2025 | TikTok tests a viewer control to see less AI content | [A] | [TikTok](https://newsroom.tiktok.com/more-ways-to-spot-shape-and-understand-ai-content?lang=en) | PL | 2027-03 |
| 2024 | Instagram gives costlier encodes to videos with more views (Mosseri) | [A] | [TechCrunch](https://techcrunch.com/2024/10/27/instagram-is-lowering-video-quality-for-unpopular-videos) | PL | 2027-03 |

## 16. Critique and AI judges

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| 12.5 → 31.3 mIoU | Frame-number overlays raised a video model's grounding (Qwen2-VL-7B) | [L] | [NumPro](https://arxiv.org/abs/2411.10332) | CR | 2027-03 |
| tIoU ≤ 0.11 | Model timestamps are nearly useless as edit coordinates | [L] | [VEBench](https://arxiv.org/pdf/2605.03276) | CR | 2027-03 |
| ±1 s | Seam slices sent at native fps | [V] → [X] | Gemini video docs | CR, K | 2027-03 |
| 13 ms | A stray frame can register (detection above chance, contested) | [L] | Potter 2014 | CR | 2027-03 |
| 64–67% vs 71.1% | Model judges' agreement with human pairwise votes vs human–human | [L] preprint | [AVENUE](https://arxiv.org/pdf/2609.04253) | CR | 2027-03 |
| 32 points | Audio-LLM judges trail humans on paralinguistic pairs | [L] preprint | [ParaPair](https://arxiv.org/abs/2606.24648) | CR | 2027-03 |
| 65.0%; 23.8% | Swap consistency of GPT-4 and Claude-v1 (2023 models) | [L] | [Zheng 2023](https://arxiv.org/abs/2306.05685) | CR | 2027-03 |
| 91.3%; 8.7%; 0.94 → 0.98 | Verbosity-padded answers won with Claude-v1/GPT-3.5 vs GPT-4 judging; length control's gain in Arena correlation | [L] | Zheng 2023; [Dubois 2024](https://arxiv.org/abs/2404.04475) | CR | 2027-03 |
| +10 pp; +25 pp | Self-preference of GPT-4 and Claude-v1 | [L] | Zheng 2023; [Panickssery 2024](https://arxiv.org/abs/2404.13076) | CR | 2027-03 |
| 46.4% → 52.2%; κ 0.763 vs 0.627 | Checklists vs direct scoring; a mixed-family jury vs a single judge | [L] | [TICK](https://arxiv.org/abs/2410.03608), [PoLL](https://arxiv.org/abs/2404.18796) | CR | 2027-03 |
| 31.5% | Structured judging cut self-preference | [L] | [SPB](https://arxiv.org/abs/2604.22891) | CR | 2027-03 |
| ~200; ~800 | Blind human pairs to detect a 60/40 and a 55/45 preference (α 0.05, 80% power) | [X] binomial power | — | CR, K | 2027-03 |
| ~5 | Consolidated notes per round; more P1s than this means an upstream problem | [P] + [X] | [Frame.io](https://blog.frame.io/2022/04/18/10-steps-for-client-approval/) | CR, K | 2027-03 |
| 2 rounds; ≥2 families | Stop after two winless rounds (ties keep the champion); at least two judges, from different model families | [I]; [L] | ARCHITECTURE.md §9 (guard 12); [PoLL](https://arxiv.org/abs/2404.18796) | SK, CR, K | 2027-03 |

## 17. Style-specific evidence

| Value | Meaning | Tier | Source | Used in | Re-check |
|---|---|---|---|---|---|
| N = 112 | Adding a talking head to narrated slides lowered factual learning, raised satisfaction | [L] | [Sondermann & Merkt 2022](https://doi.org/10.1016/j.compedu.2022.104675) | ED | 2027-03 |
| d = 0.32; 0.36; 0.42 | Segmenting: retention, transfer, system-set breaks | [L] | [Rey 2019](https://link.springer.com/article/10.1007/s10648-018-9456-4) | ED | 2027-03 |
| 2–3 min; 1.5–2× | edX tutorial watch time at any length; tablet-drawing tutorials held viewers longer | [A] | Guo 2014 | ED | 2027-03 |
| ~4 chunks; first 3–4 and last | Working-memory capacity; serial-position recall | [L] | [Cowan 2001](https://doi.org/10.1017/S0140525X01003922), [Murdock 1962](https://doi.org/10.1037/h0045106) | ED | 2027-03 |
| 3–4 s; ~7 s; 8–15 s | Time per list item: rapid-fire, tier list, standard | [V] n=4 and n=1; [X] | PandaStudio Abdaal, Hormozi | ED | 2027-03 |
| 4–8 s | Visual change in an explainer (critic signal) | [V] n=13 | PandaStudio | ED | 2027-03 |
| 0.5–1.2 s; 0.4–0.8 s | Beat after the answer; step-boundary beat | [X] from [L] | MacGregor 2010; Rey 2019 | ED, K | 2027-03 |
| 3–5 items; 10–12 | Listicle items that each need a reason; rapid-fire formats | [L] → [X]; [V] | Cowan 2001; PandaStudio | ED, K | 2027-03 |
| "1/3" for ≤5 items | Counter format | [L] → [X] | Villar 2013 | ED | 2027-03 |
| 0.3–0.6 s; ≥~1 s; ≤5 words | Eased zoom inside a screen recording; hold on each completed step's result; step-label length | [X] | — | ED | 2027-03 |
| ~8 s; ~3 s (~3.2 s per item); 1–5 words | Abdaal overlay change in frameworks and lists; caption page size | [V] n=4 | PandaStudio Abdaal | ED | 2027-03 |
| ~3.5 s | Hormozi rapid-fire listicle cut interval | [V] n=1 | PandaStudio Hormozi | ED | 2027-03 |
| 5–25%; 0%, ~13 cuts/min, 35% memes | Cleo Abram face time; Fireship long-form | [V] n≈3; n=3 | PandaStudio | ED | 2027-03 |
| 84% | Self-shot mobile creative's likelihood of beating studio creative (2019 Stories ads) | [A] ads | [Meta](https://en-gb.facebook.com/business/news/insights/perfection-fatigued-millennials-gen-z-want-human-video) | PS | 2027-03 |
| 300–700 ms | Eye movement to a new speaker after speech starts, with no anticipation | [L] | Hirvenkari 2013 | PS | 2027-03 |
| 5.3×; 4.8×; 2.7×; 2.1×; 1.7× | Odds of top-20% purchase intent: USPs, brand ≤25% of runtime, product shown twice+, speech plus music; brand ads with brand and message in 5 s | [A] survey, no method | [Meta/Toluna Dec 2025](https://www.facebook.com/business/news/reels-creative-strategies) | PS | 2027-03 |
| 90%; +112%; +89%; +47% | Ad-recall impact in the first 6 s; greeting and recall; product shown throughout: recall, purchase intent | [A] ads | CreatorIQ × TikTok | PS | 2027-03 |
| +12%; 47% | Captioned ads' view time; campaign value in the first 3 s (c. 2016) | [A] ads | [Meta](https://www.facebook.com/business/news/updated-features-for-video-ads) | PS | 2027-03 |
| ≥~15% | Talk share each diarized speaker holds to count as a podcast clip | [X] | — | PS | 2027-03 |
| ≤7 words; 10 s | Podcast context banner; nothing unresolved in the first 10 s | [X] | — | PS | 2027-03 |
| 66–139 s; 4 in ~3 s; 4.1–6.3 s; 1–1.5 s; ~5.5 s; 6.5% | DOAC clips (n=3): length, fastest switching, cut interval, reaction cutaways, banner exit, only b-roll share | [V] | PandaStudio DOAC | PS, K | 2027-03 |
| >~12 s; 5–6 s | A long podcast hold; crop change interval instead | [V] | PandaStudio DOAC | PS, K | 2027-03 |
| 0.0 s; ~14% | "Ad" label from the first frame, below Meta's top UI zone | [A] + [X] | [FTC](https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers), [ASA](https://www.asa.org.uk/resource/influencers-guide.html) | PS, EX | 2027-03 |
| top 2% | Redken's result-first ad's retention rank among TikTok ads | [A] | CreatorIQ × TikTok | PS | 2027-03 |
| ρ = .57; .29; 132; .20; .29; .27 | Transportation: emotional response; attention; effect sizes; identifiable characters; imaginable plot; lifelikeness | [L] | [van Laer 2014](https://openaccess.city.ac.uk/id/eprint/6755/1/SSRN-id2033192.pdf) | SC | 2027-03 |
| 20 jokes; 0.51 vs 0.42 s | No special punchline pause or rate; setup vs pre-punchline pauses (not significant) | [L] small | [Attardo & Pickering 2011](https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf) | SC, CP, SP | 2027-03 |
| >99% of 1,200 | Natural laughs at phrase or sentence ends | [L] | [Provine 1993](https://doi.org/10.1111/j.1439-0310.1993.tb00478.x) | SC | 2027-03 |
| 4,000+; 30,000+ | Movies and articles: emotional volatility raised ratings and reading | [L] | [Berger, Kim & Meyer 2021](https://doi.org/10.1093/jcr/ucab010) | SC | 2027-03 |
| +2.3%; 370M | Click-through per negative headline word | [L] | [Robertson 2023](https://doi.org/10.1038/s41562-023-01538-4) | SC | 2027-03 |
| +67%; 2.7M | Share odds per out-group term (observational) | [L] | [Rathje 2021](https://doi.org/10.1073/pnas.2024292118) | SC | 2027-03 |
| 3,602 (790); 4,452 (782); 1,973 (7,722) | Story-teaser, absurd-statement and shock hooks: average views (clips) | [V] | OpusClip hooks | SC | 2027-03 |
| 50 parts, ~10 min, 500M | "Who TF Did I Marry?!" series, a reference example of an unedited teller | [P] reference | [Wikipedia](https://en.wikipedia.org/wiki/Who_TF_Did_I_Marry%3F) | SC | 2027-03 |
| 0.0 s; 3.5–5.6 s | Hormozi advice clips start speech at frame 0 and hard-cut at this interval (n=3) | [V] | PandaStudio Hormozi | SC | 2027-03 |
| 18–20 LU | Ambient storytime bed (out before the reveal) | [X] | `music.md` | SC | 2027-03 |
| 0–2; ≤10% / ≤5% | Inserts per 60 s and full-screen share for storytime and hot take / comedy | [X] | `broll.md` | SC, K | 2027-03 |
