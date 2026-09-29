# Yunicorn Editing Doctrine: Skills and Knowledge Pack (plan)

This is the head editor's craft brief for the agentic editor. It describes how to edit a talking-head short well. It does not describe how the engine runs. It is written as principles, starting ranges, judgment calls and "when to break this" notes. None of it is a rule list the agent executes blindly.

---

## 1. How the doctrine is meant to work

**Three layers, and each owns different things:**

1. **Validators (code; non-negotiable; the agent never spends attention on them)**
   - No cut inside a word, and no cut in an inter-word gap under 150 ms.
   - Cut edges snap to a measured acoustic minimum, with 30–200 ms padding. This follows [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md), which notes ASR timestamps drift by 50–100 ms.
   - 10–30 ms equal-power crossfades, room-tone fill, and never any digital silence.
   - Every spoken CTA and payoff word is retained unless the creator asks otherwise.
   - Text stays inside the platform safe band and off the eyes and mouth.
   - Loudness is −14 LUFS integrated with true peak ≤ −1 dBTP, measured after encoding.
   - HDR is tone-mapped exactly once. No clip is reused. Every asset passes a licence check.
   - **No timestamp produced by a model (Claude, Gemini, Qwen) is ever used as an edit coordinate.** All times come from aligned words or measured signals.
2. **Doctrine (this pack):** taste, ranges, trade-offs and worked examples.
3. **Creator memory:** per-creator preferences learned from their corrections. These override doctrine defaults (see §19).

**Evidence tiers.** Every range in the pack carries a tag:
- **[L]** Lab or causal study. Strong, but never measured on Shorts.
- **[A]** Platform data, almost all about ads rather than organic retention.
- **[V]** Vendor or practitioner, often n≈3–13.
- **[I]** Internal Yunicorn evidence.
- **[X]** Our own inference.

**No study links cut rate, zoom rate, speed-up or caption style to organic retention for talking heads.** Every number here is a prior to test, not a law.

**What the agent must do on every edit:**
- Name a style profile, plus an optional signature, in the Edit Brief.
- Give a one-line reason for any departure from a range.
- Give every added element a named job.

**The master test:** *the edit fails if the viewer notices the technique instead of the speaker.*
- Over-editing is the documented failure mode. On a calibration take, a pro editor made **1 splice where our old heuristic made 19** [I].
- Fast pacing combined with arousing content overloads viewers and lowers recall ([Lang 1999](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504)) [L].

---

## 2. Pack layout (progressive disclosure)

The pack follows the [Agent Skills format](https://agentskills.io/specification):
- Metadata of about 100 tokens is always loaded.
- `SKILL.md` stays under 5k tokens.
- Topic files sit one level deep and load through the harness's `load_skill` tool when a stage needs them.
- Human-written skills beat judge-evolved ones: 0.620 vs 0.582, and removing composition skills cut planning to 0.465 ([VideoWeaver](https://arxiv.org/html/2606.08091v1)). So people author the doctrine, and automation only proposes changes.

| File | Loaded when | Contains |
|---|---|---|
| `SKILL.md` | Always | Prime directives, evidence legend, stage order, profile picker, routing table, list of what validators already enforce |
| `story-and-hook.md` | Brief, story cut | Hook windows, first-line types, cold opens, open loops, beat sheets, length targets, alternate openings |
| `cutting-and-pacing.md` | Story and fine cut | Pause policy, take selection, false starts, fillers, seam placement, hidden vs visible jump cuts, tempo structure |
| `speed.md` | Fine cut | When to speed up and by how much, protected lines, ramps, slow motion |
| `framing-and-zooms.md` | Finishing: reframe | Punch-in size vs source resolution, placement on stress, frequency, push-ins, reframing paths |
| `broll.md` | Finishing: b-roll | When to cut away, jobs, durations, layouts, entry and exit timing, anti-cheese taste |
| `broll-sourcing.md` | When b-roll is chosen | Asset-class routing, licence traps, AI-generation policy, the retrieval rubric, the conform pass |
| `captions-and-text.md` | Finishing: captions | Paging, typography, emphasis, placement, timing, animation, emoji, hook titles, callouts |
| `music.md` | Finishing: sound | Whether to use music, selection, level and ducking, structure and beat sync, sources, no-music export |
| `sfx.md` | Finishing: sound | Sparse event-locked SFX, placement by frame, levels, bans |
| `voice-and-loudness.md` | Ingest and sound | Measure-then-treat voice chain, breaths, seams, loudness targets |
| `color-and-look.md` | Finishing: color | Do-no-harm order, take matching, skin targets, look strength, what never to do |
| `transitions-and-graphics.md` | Finishing | Transition budget and bans, cards, lists, progress bars, text-behind-subject |
| `endings-loops-ctas.md` | Story cut and QC | Payoff-to-end gap, CTA retention, when a loop is honest, comedy buttons |
| `styles/*.md` (9 files) | After the profile is picked | One treatment per style (§17) |
| `signatures/*.md` | Opt-in only | Hormozi, Abdaal, DOAC measured priors (§18) |
| `platforms.md` | Brief and QC | Length and monetisation rules, originality, AI labels, safe zones |
| `critique.md` | Every review round | Metrics, critic questions, rubric derivation, how to write a note |
| `examples/*.md` | With their topic | Before/after transcript and EDL pairs, including over-edited failures |
| `evidence.md`, `CHANGELOG.md` | Maintainers only | Source registry with tier, n, domain and expiry; version history |
| `creator/<id>.md` | Brief | Generated creator memory; not authored by hand |

**Every topic file has the same shape:**
1. Principles, with the reason behind each.
2. A range table with tiers.
3. Judgment calls.
4. When to break a principle.
5. One worked example.
6. What validators already enforce.
7. What the critic should measure or ask.

---

## 3. `SKILL.md`: prime directives

1. **Story before polish.** The story cut must pass a *radio test*: it has to work as audio alone. Finishing never rescues a weak structure.
2. **Every addition has a job.** "No insert", "no music" and "no zoom" are always valid choices.
   - Interesting but irrelevant visuals reduce recall and transfer (g = −0.33; [Sundararajan & Adesope 2020](https://link.springer.com/article/10.1007/s10648-020-09522-4)) [L].
3. **The beat is the unit of editing, not the silence.** Cut where a thought ends.
4. **Voice before visuals.** Degraded audio lowers ratings of both the content and the speaker ([Newman & Schwarz 2018](https://journals.sagepub.com/doi/abs/10.1177/1075547018759345)) [L]. It is the strongest causal lever we have.
5. **Match the edit to measured energy; never fake energy.**
   - Speaking rate is a proxy for enthusiasm. Speeding up a flat speaker does not create enthusiasm ([Guo, Kim & Rubin 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf)) [L/observational].
6. **Lean raw and creator-specific by default.**
   - TikTok asks for "DIY or not overly polished" ([TikTok](https://ads.tiktok.com/help/article/creative-best-practices)) [A].
   - Mosseri: "imperfection becomes a signal" (Dec 2025).
   - Never ship one template across creators. YouTube's inauthentic-content policy and Instagram's originality ranking both penalise it.
7. **Protect what only this creator could say.** Keep laughs, catchphrases, personality flubs, the payoff and the CTA.
8. **Resolve ambiguity with variants, not blocking.** Ask the creator at most one question up front.

**Stage order.** This mirrors a post house, and any stage may send work back to an earlier one:

brief → story cut → fine cut → picture lock → reframes and punch-ins → b-roll and graphics → captions → sound → color → QC

---

## 4. `story-and-hook.md`

**Principles**
- **The opening is the thumbnail.**
  - Every one of 13 measured top Shorts reached speech or an on-screen payoff within **2.5 s** ([PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws)) [V, n=13, vendor].
  - TikTok says to state the proposition in the first **3 s** and hook within **6 s**. 90% of ad-recall impact falls in the first 6 s [A].
  - Galloway reports that a viewed-vs-swiped ratio of 70–90% goes with top performers, and under 60% rarely performs [V].
- **The first kept line is a verdict, claim, question, promise or stake.** It is never a greeting, logo or preamble.
- **Cut throat-clearing, but keep direct address.** In TikTok creator ads ([Lumen 2022](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf)) [A, ads]:
  - a person on screen in the first 2 s gave +50% hooking power;
  - saying "you" in the first 5 s gave +128% purchase intent;
  - a greeting gave +112% brand recall.
  - So remove "Hey guys, so today I want to…" and keep "You're budgeting wrong."
- **Open a knowledge gap and pay it off.** Curiosity comes from a salient gap ([Loewenstein 1994](https://doi.org/10.1037/0033-2909.116.1.75)) [L]. Measured payoffs sit at **55–85% of runtime** [V].
- **Design the story in text first.** The DOAC trailer editor works from transcript to a written story, then to the edit, using four beats: hook, lesson, emotional rollercoaster, cliffhanger ([source](https://callummcdonnell.substack.com/p/meet-the-viral-editor-behind-steven)) [V].

**Length targets** [V, correlational, brand-heavy]
- **Default 30–60 s.** Use 45–90 s for stories and explainers, and 60–180 s only for a real arc.
- Reels median views peak at 45–60 s ([Socialinsider](https://www.socialinsider.io/blog/instagram-reels-length/)). Shorts at 45–59 s get the most average views (vidIQ, 331M Shorts).
- TikTok Creator Rewards pays only for videos over 1 min, so offer a ≥60 s cut when that matters.
- Reels over 3 min are not recommended to non-followers.
- **Never pad.**

**Judgment calls**
- **Cold-open pull-forward:** use it only when the line stands on its own without setup.
- **Alternate openings:** generate 2–3 and have the critic rank them. A creator or Trial Reel picks when stakes are high.
- **Never speed up the hook.**

**When to break a principle**
- Storytime may open mid-scene on the stakes rather than a claim.
- Tutorials open by showing the finished result.
- A signature greeting that *is* the creator's brand can stay if it lasts about 1 s or less.

**Example**

*Raw (4.1 s before content):* "Um, hey guys, so today I wanted to talk about, uh, why most people fail at budgeting."
*Edit:* "Most people fail at budgeting for one reason." The first word lands at 0.3 s, a hook title restates the claim, and the face is visible.

---

## 5. `cutting-and-pacing.md`

**Where to cut**

A seam belongs at the end of a thought or of a completed gesture:
- Viewers perceive event boundaries at breaks in *action*. Jumps in space or time barely register ([Magliano & Zacks 2011](https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf)) [L].
- Viewers' blinks synchronise at implicit low-attention breakpoints ([Nakano 2009](https://europepmc.org/articles/PMC2817301)) [L]. This is Murch's "cut on the blink" with evidence behind it.
- The best cut points are where the speaker is "relatively quiet and still" ([Berthouzoz 2012](https://www.floraine.org/research/video-transitions/)).
- Murch's priority order is emotion > story > rhythm.

**Pause policy** [L + I]

The median pause in read English is **493 ms** ([Campione & Véronis](http://sprosig.org/sp2002/pdf/campione-veronis.pdf)). Our pro kept 300–550 ms pauses verbatim [I]. Listeners do not hear shortened pauses but do penalise very long ones ([Owoicho 2024](https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf)). So the real cost of shaving a pause is the *visual* seam, not the sound.

| Pause | Default |
|---|---|
| <200 ms inside a phrase | Never cut |
| 250–550 ms at a sentence boundary | Keep verbatim |
| 0.6–1.5 s hesitation | Tighten to 250–400 ms with room tone, or cut at a clean seam |
| Deliberate dramatic beat | Keep 0.5–1.2 s |
| >1.5 s dead air | Cut |

**How to tell a dramatic pause from a hesitation:** use prosody features. A sentence-final fall in pitch and intensity followed by a pause means keep it. A mid-phrase pause after a filler means tighten it.

**Takes, false starts and fillers**
- **Take choice:** the last complete take is the prior. Override it on delivery: energy, wording and completeness.
- **False starts:** label a line a false start only when a *later* re-delivery exists. A CTA was once deleted because this rule was missing [I].
- **Deliberate repetition** is emphasis; keep it.
- **Stutters:** cut only across a gap of 150 ms or more. Otherwise cover the cut or keep the stutter.
- **Fillers help listeners:** they improved story recall, while duration-matched coughs impaired it ([Fraundorf & Watson](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)) [L]. Hormozi's measured Shorts keep fillers [V, n=3].
- **Filler dial by context:**
  - Remove aggressively in the hook, in hot takes and in sales pieces.
  - In storytime, remove only clusters of fillers.
  - Keep any filler that carries personality.

**Jump cuts: embrace or hide**
- **Embrace** them between beats, for compression in lists and hot takes, and as comic punctuation.
- **Hide** them inside a single thought, and in emotional or story passages. The tools for hiding are:
  - a cutaway with a J- or L-cut;
  - a cut on the onset of motion (edits went unnoticed a quarter to a third of the time in [Smith & Henderson](https://bop.unibe.ch/JEMR/article/view/2264)) [L];
  - a clearly different framing.
- **Avoid scale changes under about 10%.** They read as a mistake. Murch warns against changes that are "neither subtle nor total".
- Seamless transitions went with **+60% recall** in TikTok ads, which also note that "editing shouldn't be frenetic" [A].

**Rhythm: tempo structure, not randomness**

Cutting's 150-film study found that adjacent shots have *correlated* lengths, grouped into "packets", and that the pattern is unrelated to how much viewers liked the film ([Cutting 2010](https://jordandelong.com/pubs/2010/AttentionEvolution.pdf)) [L]. For a Short, that means:
- Keep a locally consistent tempo inside a section, and change tempo between sections.
- **Starting prior: a visual change about every 4–6 s (median about 5).** Flag any static stretch of 8 s or more [V, n≈13].
  - Tighten to about 2.5–3 s before the payoff; DOAC does this at 4.1–6.3 s tightening to 2.5–3 s [V, n=3].
  - Slow down on emotional or information-dense beats. Emotion change is the most taxing kind of new information per cut ([Lang 2013](https://exa.ai/library/publication/q9vn7f63xlj)) [L].
- **Speed context:** the lab defines "fast" as 0.5–0.67 cuts/s and "slow" as 0.16 cuts/s. A 5 s median is slow to medium, which leaves headroom for calm content but not for dense content.
- **Seams per 60 s: pro calibration 1–4** [I].
- **Reject** the vendor claim that cutting every 0.6–0.9 s works best. No method has been published.
- **Energy matching:** calm speakers get fewer seams. High-arousal speakers can take more.

**When to break a principle**
- Comedy runs tight with no dead air.
- A list can use metronomic hard cuts per item as a deliberate device.

**What the critic asks**
- Does any cut land inside a clause?
- Where is the longest static stretch?
- Is any section frenetic while dense information is being delivered?

---

## 6. `speed.md`

**Evidence**
- Time-compressed TV ads at about 1.25× went unnoticed and raised recall by 36%/40% ([MacLachlan & Siegel 1980](https://exa.ai/library/publication/3vh4g3cfs4p)).
- But compressed ads captured less attention, evoked fewer cognitive responses and made persuasion lean on source credibility ([Moore 1986](https://exa.ai/library/publication/m0zfrfpygsv)).
- Older adults recalled less from compressed ads ([Stephens 1982](https://exa.ai/library/publication/5ggfldbymjd)).
- Lecture comprehension held up to 2× at speeds the experimenters assigned ([Murphy 2022](https://castel.psych.ucla.edu/wp-content/uploads/sites/111/2021/11/ACP-Lecture-Speed-Murphy-2021-in-press.pdf)) [L].
- **The gain is not free:** faster speech trades depth of processing for credibility cues.

**Defaults**
- **Cut dead air first.** Speed is the last resort.
- **Up to about 1.1× (hard cap about 1.2×)**, pitch-preserved and applied per segment, only for slow speakers.
- **Always 1.0×** for the hook, emotional lines, punchlines and instructions.
- **2–3× only on footage with no speech:** process shots and b-roll. Ease in and out of ramps.
- **Never slow speech down.** Slow motion is for b-roll only, from 60 fps sources or RIFE-interpolated footage.
- **Watch caption load:** above about 1.15× on dense speech, captions exceed about 20 CPS. Re-page the captions or reduce the speed; never trim words.

---

## 7. `framing-and-zooms.md`

**Punch-in size is bounded by source resolution** [V]

| Source → output | Useful ceiling | Typical punch |
|---|---|---|
| 1080p → 1080p | 1.1–1.3× | 1.1–1.2× |
| 4K → 1080p | 1.8–2× | up to about 1.5× (Hormozi's A/B crop) |

**Placement**
- Land the punch-in on the onset of the stressed word, chosen from prosody z-scores, not from meaning alone.
- Keep the eyes near the upper third.
- Keep the face clear of the platform interface zones.

**Frequency**
- **Default 2–4 per 60 s.** The vendor ceiling is about 3–4 per 40 s.
- Abdaal uses at most one 1.5× punch-in, at about 75% of runtime [V, n=4].
- Mechanical zooms on a timer are a known failure. One Descript user reported zoom cuts "every 20 seconds" (an anecdote).

**Movement and reframing**
- Slow push-ins suit storytime climaxes. Hard punches suit claims and punchlines.
- Reframing follows the subject with a dead zone and a smoothed path. The camera must never "hunt".

**When to break a principle**
- A creator's signature A/B-crop rhythm, on 4K sources.
- Podcast clips, where active-speaker crops replace punch-ins.
- **Never upscale the speaker's face** to get more zoom range.

---

## 8. `broll.md`

**Evidence base.** Most b-roll rules come from vendor blogs with no data ([AutoClip](https://autoclip.dev/blog/b-roll-advanced-techniques-for-clippers), [Pireel](https://pireel.com/en/blog/b-roll-and-graphics-for-talking-head-video)) [V]. The one causal anchor is the seductive-details result [L]: wrong or decorative b-roll is worse than none. The base rate is low: 6% of 13.5M OpusClip clips used b-roll [V].

**Every insert names its job:**
- illustrate a real thing;
- show evidence;
- render a concept or number card;
- land a punchline (licensed media only);
- cover a seam;
- rescue a sagging middle.

**Try a punch-in first.** It "keeps the viewer in the room".

**Stay on the face for:**
- emotion, sincerity and personal experience;
- punchlines and reveals;
- the CTA;
- the first 1.5–3 s, where cutting away "reads as an ad".

The DOAC team's Facebook tests found that opening on the guest in the chair "always came out on top" over opening on b-roll [V].

**Timing** [V, with ranges]

| Insert | Hold |
|---|---|
| Cutaway | 1–3 s |
| Maximum before focus drifts | 3–5 s |
| Meme or reaction | 0.5–1.5 s |
| Text-bearing insert | ≥0.3 s per word + 1 s |

- **Budget 15–25% of runtime**, weighted toward the middle. Beyond about a third, the video becomes a montage.
- **Enter on the keyword's word boundary.** Source the insert from within ±1.5 s of the keyword, and exit by the end of the phrase.
- Short flashes of 0.5–1 s are safe when the voice names the thing shown. People detect named targets at very short exposures ([Potter 2014](https://link.springer.com/article/10.3758/s13414-013-0605-z)) [L, threshold contested].
- **The voice never changes.** Mute stock audio and lay a little foley or room tone underneath.
- **Never reuse a clip.**

**Layouts for 9:16**
- **Full-screen hard cut:** the default for literal inserts.
- **Split screen or PiP with the face kept:** for demos, comparisons and anything longer than about 3 s. The subject should fill at least a third of the inset.
- **Creator matted over a screenshot:** for commentary, news and product talk.
- **Designed cards:** for numbers and lists. Numbers are never stock footage.
- **Ken Burns or 2.5D parallax:** for stills.

**Taste (anti-cheese)**
- Ban generic stand-ins such as typing hands, city traffic and pouring coffee.
- Prefer specific, observational footage in one "camera world".
- Never pair a recognisable stock face with negative narration. It violates Pexels' terms and is also tasteless.
- Reject anything with another platform's watermark. Instagram demotes watermarked content.

**When to break a principle**
- A hook built on a striking *visual* payoff can open on it.
- Tutorials may be majority screen or hands.
- Educational content gets no decorative inserts at all.

---

## 9. `broll-sourcing.md`

**Route each beat by asset class, not by keyword:**

| Beat | Source |
|---|---|
| Real entity or claim | Creator's own media, then Playwright screenshots, then real photos |
| Numbers or lists | Designed cards |
| Mood | Conformed premium stock (Storyblocks, Shutterstock, Adobe Stock; Pexels and Pixabay as fallback) |
| Punchline | Memes only once licensed |
| Anything else | Stylized AI still with parallax |

**Ask the creator for pickups.** When the best asset would be their own product, workspace or hands, request a 2–3 item shot list. This carries no licence, AI-label or originality risk.

**Licence traps**
- **GIPHY and KLIPY:** their terms do not cover server-rendered, monetised exports.
- **Tenor:** shut down on 30 June 2026.
- **Brandfetch free Logo API:** forbids server-side use.
- **Qwen-Image-2.1, FLUX.2 [dev] and klein 9B:** non-commercial.
- **Kling:** needs written permission for commercial use.

**AI video and images**
- Photoreal AI video is limited to 1–2 flagged hero shots.
- An AI disclosure label costs about **7–8% of likes** ([JCR 2026](https://academic.oup.com/jcr/advance-article/doi/10.1093/jcr/ucag013/8672493)) [L + field data].
- SynthID survives re-rendering, so assume any photoreal AI clip will be detected.
- Prefer stylized or illustrative generation, and tell the creator when a clip needs disclosure.

**Judge each candidate on its own against a threshold, never by forcing a pick from a list.** The rubric:
- correct subject, including checks against likely look-alikes;
- subject fills at least a third of the frame;
- no watermark or platform logo;
- no recognisable person in a negative context;
- fits the style.

If nothing clears the threshold, use no insert.

**Conform before judging taste** (validator-backed):
1. Convert everything to one transfer function (HLG to BT.709).
2. Match frame rate.
3. Grade-match to A-roll reference frames.
4. Upscale low-resolution sources.

Reject anything that still looks like a different camera world.

---

## 10. `captions-and-text.md`

**Evidence base.** No peer-reviewed study compares karaoke, keyword-highlight and sentence captions on retention. On Douyin brand videos, subtitles even correlated *negatively* with comments and shares. Style is an A/B question.

**Default page**
- **One line of 2–4 words, about 20 characters or fewer, split at phrase boundaries.**
  - Two-line subtitles drew more fixation away from the picture in vertical video ([Li 2026](https://onlinelibrary.wiley.com/doi/10.1002/acp.70262), n=211) [L].
- A subtle current-word state.
- Single-word punch pages only for the hook, numbers and punchlines.
- For calm or teaching content, a mixed-case sentence style.

**Line breaks** follow Netflix and DCMP rules:
- Never split an article from its noun, a first name from a surname, or an auxiliary from its verb.
- Breaks that ignore syntax raise cognitive load ([Gerber-Morón 2018](https://bop.unibe.ch/JEMR/article/view/4267)).

**Typography**
- A heavy sans at weight 800–900 (Montserrat, Inter, Anton).
- White text with a paint-order stroke and a soft shadow, plus **one** accent colour.
- All caps only on short, large pages. Capitals are read faster at the acuity limit, and the advantage vanishes at large sizes ([Arditi & Cho 2007](https://pubmed.ncbi.nlm.nih.gov/17675131/)).

**Emphasis**
- At most one accent word per page, chosen from meaning *plus* measured prosody: loudness, pitch and duration z-scores computed in code, because Claude cannot hear.
- Express emphasis through weight, size or colour, not motion ([Caption Royale, CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642258)) [L].
- Emphasis helps only when sparse: signalling meta-analysis g = 0.53 for retention ([Schneider 2018](https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581)).

**Placement**
- Just below the chin, inside the cross-platform band **x 65–888, y 288–1248** at 1080×1920.
  - The Meta official safe zone keeps 14% top, 35% bottom and 6% on each side clear.
  - TikTok and Shorts figures come from third parties.
- Captions that follow the speaker kept eyes on relevant regions ([Kurzhals CHI 2017](https://dl.acm.org/doi/10.1145/3025453.3025772), n=40) [L].
- Keep the position consistent and move it only to avoid a collision. Deaf and hard-of-hearing viewers disliked captions that are erratic *or* far from the action ([McDonnell CHI 2024](https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_CaptionItInAnAccessibleWayThatIsAlsoEnjoyableCharacterizingUserDrivenCaptioningPracticesOnTiktok_CHI2024.pdf)).

**Timing**
- Page duration of at least 0.5 s (punch pages 0.25 s). Merge shorter pages.
- 2-frame gaps between pages.
- Hold 0.5 s only at the end of a speech run.
- Highlights lead speech by 0–1 frame and **never lag**. By analogy with lip-sync research, audio ahead of picture is noticed at about 45 ms ([ITU-R BT.1359](https://www.itu.int/rec/R-REC-BT.1359)).
- Monitor CPS, but never delete words to meet it.
  - Viewers who understand the soundtrack prefer unreduced subtitles ([Szarkowska 2018](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0199331)).
  - **Captions stay verbatim.** A filler may be dropped from the text only where it was also cut from the audio.

**Animation and emoji**
- One entry pop per page, 150–200 ms. The current word scales to 1.1× at most.
- No looping bounce, shake or strobe. At most 3 flashes per second (WCAG 2.3.1).
- At most about one emoji per beat, placed beside the words and never replacing them. Render Noto or Twemoji, never Apple glyphs.

**Other on-screen text**
- **Hook title:** 7 words or fewer, from 0 to about 3 s, in the upper band (y 288–600). It must pass a mute test.
- **Static text dwell:** at least 0.3 s per word plus 1 s (BBC and Netflix norms).
  - TikTok's "5–10 words/s" figure applies to brief ad overlays, *not* captions.
- **Callouts** fire on transcript triggers, one focal element at a time.
- **Keyword pop-ups** appear at most once every 5–10 s.
- **Lower third** appears once, on self-introduction.

**When to break a principle**
- The full "Hormozi package" (colour-per-keyword, a whoosh per word, emoji, shake) only on an explicit style request. It is widely reported as fading.

---

## 11. `music.md`

**Whether to use music at all**
- **Default: no music, or an unfamiliar, low-complexity instrumental.**
  - Lyrics add informational masking ([Scharenborg & Larson 2018](https://www.isca-archive.org/interspeech_2018/scharenborg18_interspeech.pdf)).
  - Familiar songs hurt speech recognition more than unfamiliar ones ([Brown & Bidelman 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9599198/); that study ran at 0 dB SNR, so it shows direction only).
- **Educational and tutorial:** a very low bed or none. Irrelevant music hurts learning ([Moreno & Mayer](https://psycnet.apa.org/doi/10.1037/0022-0663.92.1.117)) [L].
- **Sales and UGC:** music went with +61% recall in TikTok ads [A].
- Original vs licensed audio showed no engagement difference (8.1% vs 8.1%, FYPNow) [V].

**Level**
- **Start 18–20 LU below speech** while the person is talking.
- Swell in gaps longer than 1 s, under b-roll, and at the intro and outro.
- Duck fast and release slowly. Carve 2–4 kHz out of the music.
- The final level is set by an intelligibility gate on the real mix (ESTOI of speech stem vs mix, plus ASR WER), not by a fixed number. WCAG's 20 dB figure covers audio-only content and is only a proxy.

**Structure and beat sync**
- **Cut the speech first, then fit the music.**
- Put the energy rise on the payoff, and backtime the track so a downbeat lands on the last word.
- Use exact-duration track versions or loop points; never let the bed end on a hard fade.
- Align visual changes to downbeats **only where they already coincide with speech beats. Never move a speech cut to hit a beat.**

**Selection**
- Match mood to the speaker's *measured* arousal and to the content.
- Always render a voice + SFX export with no music, so creators can attach trending sounds natively.
- On YouTube, Audio Library music is the claim-safe default.

---

## 12. `sfx.md`

**Evidence:** practitioner only [V].

**Placement**
- Impacts land on the frame.
- Whooshes straddle the cut, or lead it by 2–4 frames.
- Risers stop dead on the reveal.

**Density**
- At most one sound per 4–5 s.
- **Only on real visual events.** None on serious, emotional or educational beats.

**Level**
- Sources disagree (−6 to −24 dB), so set it relative to measured speech loudness and tune it by A/B.

**Bans**
- A sound per caption word.
- Comedic stingers on sincere content.
- Unlicensed or non-commercial libraries.

**When to break a principle**
- Comedy buttons and listicle item markers can carry a consistent signature sound.

---

## 13. `voice-and-loudness.md`

**Measure, then treat**
- Denoise or dereverb only when measured SNR or C50 is poor, with an attenuation limit of about 12–24 dB. A little residual room sounds more natural than artifacts.
- Choose between voice variants (none, discriminative, hybrid generative→discriminative, commercial isolators) with metric gates:
  - ASR WER rises by at most 1 point;
  - speaker similarity is preserved;
  - no regression on DNSMOS or UTMOS.
- **Never choose a variant because it "sounds cleaner" to a model hearing 16 kHz audio.**

**Chain priors**
1. High-pass at 40–100 Hz.
2. Cut boxiness only where measured.
3. De-ess just enough.
4. Compress 3–6 dB.
5. Level so takes match within about ±2 LU.

**Breaths**
- Attenuate loud breaths and leave quiet ones.
- iZotope describes this Target approach as "more natural", and warns that reducing every breath "can result in unnatural sounding results" ([RX docs](https://s3.amazonaws.com/izotopedownloads/docs/rx8/en/breath-control/index.html)).

**Seams**
- 10–30 ms equal-power crossfades.
- Match level and noise floor across a join; jumps above about 4–5 dB are audible.
- Fill gaps with room tone looped from the take's own pauses.

**Loudness**
- **−14 LUFS integrated, true peak ≤ −1 dBTP** (−1.5 to −2 for dense mixes), measured after the AAC encode.
- YouTube turns loud content down and never turns quiet content up.
- AES TD1008 recommends −18 LUFS for speech, so −14 is a platform-matching choice. A/B −14 against −16.

---

## 14. `color-and-look.md`

**Priority order: do no harm, then consistency, then a restrained look.**

**Why polish is hygiene.** Technical quality scores correlate only 0.07–0.31 with watch time ([Li 2024](https://arxiv.org/html/2410.00289v1)). The same paper's model using aesthetic and content features reached SRCC 0.696. So composition and look still matter; technical perfection alone does not.

**Order of work**
- Match takes to each other first, then apply the look.
- Keep settings static per take. Keyframe only when the lighting really changes.

**Skin checks** (measured, not eyeballed)
- Face luma against BT.2408 HLG ranges: light 55–65%, medium 45–60%, dark 25–45% ([ITU](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf)). Data for Fitzpatrick types 1, 5 and 6 is limited.
- Saturation about 30–40% for lighter skin and 15–20% for darker skin. These are "guidelines, not absolutes" (Van Hurkman).

**Claude's colour judgment is weak.** ColorBench found colour understanding "largely neglected" across 32 VLMs ([ColorBench](https://arxiv.org/abs/2504.10514)). Always read the measured numbers alongside before/after stills, and judge noise and sharpness on 1:1 crops.

**Never by default**
- Added grain (encoders smear it).
- Skin smoothing.
- Generative face restoration (it flickers).
- AI upscaling of the speaker.
- Heavy looks.

A look LUT is applied at modest strength, and varies per creator.

---

## 15. `transitions-and-graphics.md`

**Transitions**
- **Default:** hard cuts plus J- and L-cuts.
- Stylised flash, whip or glitch transitions only at section boundaries, about 2 per video at most. Whips last under 0.4 s.
- No dissolves inside talking head.
- **Banned:** star wipes, spins, 3D cubes.

**Graphics**
- Numbers and lists become designed cards, with one focal element at a time.
- Cards fire on transcript triggers and obey the text-dwell rule.
- **"Text behind subject"** hook titles are a practitioner trend; A/B them.
- **Progress bars** are opt-in only. The only peer-reviewed evidence concerns chapter bars and purchase intent, not retention.

---

## 16. `endings-loops-ctas.md`

**Endings**
- **End within about 0.5 s of the final payoff or CTA word. No outros.** Measured Shorts end within 1.5–2 s of the payoff [V].

**CTAs**
- **A spoken CTA is part of the payoff.** It is never a disposable sign-off [I].

**Loops**
- Build one only when the transcript supports it, meaning the last line flows into the first. This usually applies under about 35 s.
- Since 31 Mar 2025 every start and replay counts as a Shorts view. YPP still uses engaged views, so a dishonest loop gains nothing.

**Comedy**
- End on the button or tag.

---

## 17. `styles/`: per-style treatments (priors the agent adjusts)

| Style | Length | Open | Cadence | Punch-ins | Speed | B-roll / text | Music / SFX |
|---|---|---|---|---|---|---|---|
| **Educational** | 30–60 s | Counterintuitive answer ≤2 s | 4–8 s; beat after key claims | Rare | 1.0× | Proof and cards only; highlight numbers | None or very low; no SFX |
| **Storytime** | 45–120 s | Drop in at the stakes | 5–8 s; keep dramatic pauses | Slow push-in at the climax | 1.0× | Plain captions; few inserts | None or ambient |
| **Comedy** | 15–45 s | The premise | Tight, no dead air | On the button | Never on the punchline | Keep the timing whole | Button SFX optional |
| **Hot take** | 15–45 s | The take itself | 3–5 s | On the claim | ≤1.1× | Hook text = the take | None |
| **Listicle** | 30–75 s | Count + promise | Hard cut per item | Section markers | ≤1.1× | Counter on screen | Light bed |
| **Tutorial** | 45–120 s | Show the result first | Keep steps whole | Rare | 1.0× on steps; 2–3× on silent process | Screen or hands dominate; step labels | Low bed |
| **Podcast clip** | 30–60 s | Pull-forward peak + context card | 4–6 s, 2.5–3 s near payoff; pauses 0.2–0.3 s | Active-speaker crops | 1.0× | 3–5 emphasised words | Usually none |
| **Sales / UGC** | ~21–34 s | Face + "you" ≤2 s | 3–5 s: hook, problem, demo, proof, CTA | Moderate | ≤1.1× | Lo-fi product in use; CTA overlay | Music aids recall |
| **Founder / brand** | 30–60 s | A specific stake | Natural; keep flubs | Few | 1.0× | Proof inserts | Subtle |

**Style-specific notes**
- **Educational:** a face raises satisfaction and choice but can lower learning ([Sondermann & Merkt](https://exa.ai/library/publication/28zts39hc3s)). So keep the face, and make every insert relevant.
- **Comedy:** 20 joke performances showed no pre-punchline pause and no rate change ([Attardo & Pickering](https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf)). Do not insert artificial beats before punchlines (weak evidence).

**What each style file adds:**
- a worked example;
- the style's "don'ts";
- how its energy-matching dials shift.

---

## 18. `signatures/` (opt-in; tiny samples)

- **Hormozi** (n=3):
  - hard cuts every 3.5–5.6 s;
  - 1.5× A/B crop (needs a 4K source);
  - zero b-roll;
  - fillers kept;
  - 1–4 caption words every 0.8–1.0 s.
- **Abdaal** (n=4):
  - about 95% face time;
  - at most one 1.5× punch-in, at about 75% of runtime;
  - content within 2.5 s.
- **DOAC** (n=3): the cadence in §5, pull-forward cold opens and four-beat structure.

Signatures are loaded only when requested. Each one records its sample size, so the agent treats it as flavour, not law.

---

## 19. `critique.md`: what reviewers check

**Metrics computed on every render** (critic inputs and eval features):
- Seams per minute (pro: 1–4 per 60 s).
- Median kept boundary pause (250–550 ms).
- Share of cuts landing inside a clause.
- Tempo consistency within sections, and tempo shifts between them.
- Longest static stretch (flag over 8 s).
- Time to first speech or payoff (≤2.5 s) and to the proposition (≤3 s).
- Punch-in count and scale vs source resolution.
- Share of speech sped up, and the maximum factor.
- Gap from final word to end (≤0.5 s).
- CTA and payoff words retained.
- A round-trip ASR diff, catching clipped words and leftover fillers.
- B-roll share and reuse.
- Caption CPS and safe-zone collisions.
- LUFS and true peak.
- ESTOI of speech against the mix.

**How critics work**
- Ask concrete, localised questions, not for scores. Question-driven critique gave +11.57% on T2V-CompBench ([VQQA](https://arxiv.org/abs/2603.12310)).
  - Examples: "Does the insert at word 212 show the named product?" "Is the seam at frame 431 visible?"
- Grade against a binary rubric derived from *this* edit's brief ([VideoArgus](https://arxiv.org/abs/2608.05485)).
- Review renders carry burned-in frame numbers and word IDs ([NumPro](https://arxiv.org/html/2411.10332v1)).
- Map every note back to word IDs before acting on it. VLM temporal localisation is near zero (tIoU ≤ 0.11; [VEBench](https://arxiv.org/pdf/2605.03276)).
- A Claude-only judge never scores Claude's own edit. Use cross-family juries ([PoLL](https://arxiv.org/html/2404.18796)).

**Revision rounds**
- Revise only while a P0 or P1 issue exists, for at most about 5 rounds.
- Accept a revision only if it wins a position-swapped comparison against the current best.

---

## 20. How the doctrine gets updated

### A. From creator corrections (fast, per creator)

1. **Capture every correction.** After each export, diff the agent's EDL against the creator's final EDL. Also log chat tweaks, undos, rejected variants and alternate-opening picks.
2. **Classify each diff against a doctrine section.** Examples:
   - "restored a 700 ms pause" → `cutting-and-pacing` / pause policy;
   - "deleted b-roll at claim" → `broll` / stay-on-face;
   - "shrank zoom" → `framing-and-zooms`.
3. **Induce preference statements, CIPHER-style.** CIPHER cut edit distance by 31–73% vs no learning, with simulated users only ([CIPHER](https://arxiv.org/html/2404.15269)).
   - Statements are tagged with context (style, beat type, platform).
   - Example: "Keeps dramatic pauses up to ~1.2 s in storytime; dislikes zooms on sincere lines."
4. **Show the preferences to the creator.** They can view and edit them in the app. The about 5 most relevant are retrieved into each Edit Brief.
5. **Creator memory overrides doctrine defaults but never validators.** A creator cannot ask the engine to cut mid-word.

### B. From aggregate signal (slow, house doctrine)

- **Override-rate dashboard.**
  - Track, per principle and style profile, how often creators reverse it.
  - A principle reversed by many creators in one profile is flagged for review. A working threshold: at least 10 distinct creators, or at least 5% of that profile's edits.
- **Promotion rule.** A recurring correction becomes doctrine only when all three hold:
  1. A human editor rewrites it as a principle with a "when to break this" note.
  2. It wins a blind pairwise evaluation on phones: about 200 independent pairs to detect 60/40, and about 800 for 55/45, inflated for clip and rater clustering and analysed with Bradley–Terry.
  3. The golden regression set does not get worse.
- **Platform outcomes.** With creator consent:
  - Instagram: `reels_skip_rate`, average watch time and sends; Trial Reels for real organic A/B tests of edit variants.
  - YouTube: `engagedViews` and `audienceWatchRatio`.
  - TikTok's public API has no retention data.
  - Always compare each video against the creator's own median.
- **Ablate one file at a time.** Every harness component encodes an assumption, so a doctrine change ships as a versioned diff and is tested alone.

### C. From new research and tools

- **`evidence.md` source registry.** Every claim records its tier, sample size, domain (ads, lab, organic), date and an **expiry or re-check date**. Platform facts go stale fast: YouTube changed its Content ID rules for 1–3 min Shorts on 24 Sep 2026.
- **Intake:**
  - A monthly scan of platform policy pages and help centres.
  - A quarterly literature pass covering captions, pacing, audio and platform research.
  - Vendor claims enter as [V] priors, never as defaults.
  - Anything superseded is struck through in place, with a pointer to what replaced it, not silently deleted.
- **Automation proposes; people decide.** Automated agents may draft doctrine PRs (summaries, new ranges, candidate examples). A human editor approves each one, because human-authored skills outperformed judge-evolved ones in VideoWeaver.

### D. Reproducibility

- Each job pins the doctrine version it ran with, and the trace records it, so any edit can be explained and replayed.
- Every failed edit becomes an eval case. Every creator-approved edit that differed from the agent's is a candidate `examples/` entry after review.