# Style treatments: educational explainer, tutorial/how-to, listicle

Load this file once the brief names the style as an explainer (why or what something is), a tutorial (how to do something), a listicle (a counted set of items), or a blend such as "3 mistakes" or "5 steps". It shifts the defaults in `cutting-and-pacing.md`, `broll.md`, `captions-and-text.md`, `music.md`, `speed.md` and `framing-and-zooms.md` without repeating their mechanics. These styles exist so the viewer can understand, do or remember something, so clarity outranks stimulation. No music, no SFX and no b-roll are normal outcomes here.

## Principles

1. **Clarity is the retention strategy.** *Why:* removing extraneous material improved learning in 18 of 19 lab tests (median d = 0.86), and inserting interesting but irrelevant video clips into a narrated lesson lowered transfer [L]. Across 58 papers, seductive details lowered learning (g = −0.33), clearest for recall and static pictures; transfer-only and animated details showed no significant effect [L].
2. **Every visual must teach, prove or orient.** *Why:* a talking head alternating with relevant slides out-engaged slides alone [A]; a stock lightbulb teaches nothing.
3. **Face for the claim, the thing for the evidence.** *Why:* in a small eye-tracking study (n = 22) learners strongly preferred seeing the instructor, looked at the face about 41% of the time, and recalled no less [L]; in a larger one (N = 112), adding a talking head to narrated slides lowered factual learning while raising satisfaction and choice [L].
4. **Signal structure, sparingly.** *Why:* signaling improves retention (g = 0.53, 103 studies) and helps most when sparse [L]. The creator's spoken "first, second" and vocal stress already signal [L], so on-screen cues must serve the muted or re-watching viewer.
5. **Segment, then mark the segments.** *Why:* segmenting improves retention (d = 0.32) and transfer (d = 0.36), most when the system sets the breaks (d = 0.42) [L]. In those studies a boundary was a pause; in a short, the markers are a held beat, step labels, item counters and hard cuts [X].
6. **Tutorials are re-watched; explainers are watched once.** *Why:* edX learners re-watched tutorials more and watched about 2–3 min of each whatever the length [A] (long-form MOOC logs, not Shorts). Tutorials need findable signposts.
7. **Show hands-on actions from the doer's point of view.** *Why:* first-person demonstration beat third-person on assembly accuracy in two experiments, more so for complex tasks [L].
8. **Lists: about four items, ends remembered best.** *Why:* working memory holds about 4 chunks [L], and recall favours the first 3–4 and the last items [L].
9. **Music costs attention here.** *Why:* background music disturbs reading and slightly hurts memory [L], and irrelevant music hurt learning from narrated lessons [L]. Captions, labels and screenshots are reading.
10. **Keep instructions, the answer and dense reasoning at 1.0×.** *Why:* a viewer copying a step works in real time [X], and Guo's authors caution that speeding up an unenthusiastic speaker may not raise engagement [A].

## Defaults and ranges

These are starting priors. Give a one-line reason whenever you leave a range.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Length, explainer | 30–60 s; 45–90 s if the mechanism needs two steps. Views peak at 45–60 s (Reels, brand accounts) and 45–59 s (Shorts) | [A] correlational | [Socialinsider](https://www.socialinsider.io/blog/instagram-reels-length/), [vidIQ](https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_) |
| Length, tutorial | 45–120 s, one task per video (edX: 2–3 min watched per tutorial at any length) | [A] → [X] | [Guo 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf) |
| Length, listicle | 30–75 s with 3–5 items | [L] (about 4 chunks) → [X] | [Cowan 2001](https://doi.org/10.1017/S0140525X01003922) |
| TikTok Creator Rewards | Pays only over 1 min; "search value" is one of four factors. Offer a 60 s+ cut when it matters | [A] | [TikTok](https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program) |
| Time per list item | Rapid-fire 3–4 s; tier list about 7 s per verdict; standard 8–15 s | [V] n=4 and n=1; standard [X] | [PandaStudio Abdaal](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal), [Hormozi](https://www.writepanda.ai/blog/hormozi-style-shorts-editing) |
| Visual change, explainer | Every 4–8 s (top Shorts measured 3–8 s, median ~5); an overlay change counts. A critic signal, not a quota | [V] n=13 | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws) |
| Seams per 60 s, talking parts | 1–4, a critic signal, never a gate; listicles add one boundary per item | [I] n=1 | `backend/eval/pro_cut_reference.py` |
| Beat around the answer | Keep the speaker's own pause *before* the answer word; 0.5–1.2 s *after* it before moving on | [X] from [L] (words heard after a pause were recognised better) | [MacGregor 2010](https://www.research.ed.ac.uk/en/publications/listening-to-the-sound-of-silence-disfluent-silent-pauses-in-spee/) |
| Step boundary | 0.4–0.8 s beat with the new label up | [X] from segmenting [L] | [Rey 2019](https://link.springer.com/article/10.1007/s10648-018-9456-4) |
| Speed | 1.0× on hook, instructions, answer; listicle talk ≤1.10×; silent process 2–3×, timelapse up to ~8× | [P] + [X] | `speed.md` |
| Inserts | Explainer 0–4 per 60 s, 0–15% full-screen; tutorial screen or hands 30–70% as split or PiP; listicle 0–1 per item, 10–30% | [X] | `broll.md` |
| Face-kept layout | Split or PiP when an insert must be read or runs past ~3 s; viewers switched face↔slide every 3.7 s | [L] n=22 + [A] | [Kizilcec 2014](https://rene.kizilcec.com/wp-content/uploads/2014/01/final_version2.pdf), [Guo 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf) |
| Legible screenshot text | ≥ ~45 px at 1080×1920 for anything read (~50 px covers 375 pt phones); never under ~30 px (iOS 17 pt default, 11 pt minimum, on a 375–430 pt phone) | [X] from [P] | [Apple HIG](https://developer.apple.com/design/human-interface-guidelines/typography) |
| Label and card dwell | At least 0.3 s per word, plus 1 s for labels; more for long figures or a busy picture | [P] rate + [X] 1 s margin | [BBC §4.1, §4.3](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/) |
| Callout placement | Arrow, box or label next to the thing it names (not in the caption band), entering on the word that names it | [L] spatial contiguity 9 of 9 tests, d = 0.82; temporal 8 of 8, d = 1.31 | [Mayer 2023](https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf) |
| Emphasis | ≤1 accent per caption page: numbers, terms, the thing to tap | [L] → [X] | [Schneider 2018](https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581) |
| Counter format | "1/3" for ≤5 items; "#1" without a total for longer lists | [L] (32 web-survey experiments: slow-starting progress indicators raised drop-off; constant ones did not lower it) → [X] | [Villar 2013](https://doi.org/10.1177/0894439313497468) |
| Hook type | Process-explainer hooks 3,994 avg views, question openers 3,708, shock hooks 1,973 (34,635 TikTok clips, correlational) | [V] | [OpusClip](https://www.opus.pro/research/best-video-hooks-tiktok) |
| Music | Explainer none; tutorial and listicle none or a light instrumental bed, starting 16–20 LU under speech, never within 10 LU | [L] for "none" and the 10 LU floor (TV, n=22); [V] + [I] for 16–20 | [Moreno & Mayer 2000](https://doi.org/10.1037/0022-0663.92.1.117), [Kämpfe 2011](https://doi.org/10.1177/0305735610376261), [Torcoli 2019](https://doi.org/10.17743/jaes.2019.0052), `music.md` |
| SFX | Explainer none; tutorial only the action's real sound; listicle optionally one identical quiet marker per item | [P] + [X] | `sfx.md` |
| Ending | ≤0.5 s after the answer, last item or spoken CTA; a result shot may hold 1.5–2 s | [I]; measured Shorts end on the payoff, no outro [V] n=4 | `design_doctrine.md` §16, [PandaStudio Abdaal](https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal) |

**Measured reference creators** (tiny samples; flavour, not law):
- **Ali Abdaal Shorts, explainer and list formats [V] n=4:** about 95% face time, almost no camera cuts. Top-zone overlays change about every 8 s in framework videos and every 3 s in lists (about 3.2 s per rapid-fire item). Captions of 1–5 words; at most one 1.5× punch-in, near 75% of runtime.
- **Alex Hormozi rapid-fire listicle [V] n=1:** a hard cut about every 3.5 s, numbered chapter pills, a faint bed, no b-roll or screenshots, ends on the last line.
- **Cleo Abram [V] n≈3:** scripted explainers with 5–25% face time in an "annotated, zooming world" of graphics; a voiceover mode, not our talking-head default.
- **Fireship, long-form [V] n=3:** 0% face, about 13 cuts/min, 35% memes, numbered cards; the no-face extreme, not a template.
- **Tutorials:** no short-form sample exists. In Guo's edX data, tablet-drawing tutorials held viewers 1.5–2× longer than slides or code screencasts; where slides or code must be shown, the authors advise adding emphasis by sketching over them [A]. For us: a drawn circle or underline on the screen recording [X].

## How to decide

1. **Classify, and allow blends.** Count enumerators ("first"), imperatives with pointing words ("tap this") and causal markers ("that's why"). Pick the primary style by what the viewer does afterwards: understand, do, or choose from a set. "5 steps" is a tutorial with listicle signposts; "3 mistakes" is a listicle with explainer proof. Record the blend in the brief.
2. **Map the structure on word IDs.**
   - **Explainer:** pin the QUESTION or CLAIM, the ANSWER (payoff), the MECHANISM and one EXAMPLE. Keep a second example only if it answers an obvious objection.
   - **Tutorial:** a step table: step n → instruction IDs → action footage (screen, hands, pickup) → result state. Every step must be said and seen. If footage is missing, ask for a pickup; if none comes, a designed step card or a screenshot carries the step and the brief records the gap. Stock footage cannot show the creator's steps. Pin the result shot. Keep prerequisites as a fast first step. Cut detours unless the mistake is the lesson.
   - **Listicle:** an item table: ordinal IDs → name → one reason → at most one example. Match tempo across items, not duration.
3. **Settle the order.** Keep the creator's order when ordinals are spoken or it is a countdown. Reorder only when items carry no ordinals, or the ordinals sit in clean gaps (≥150 ms) and can be cut; then put a strong item first and the strongest last. Drop a weak item only when no count is spoken or shown; otherwise tighten it, because a "3" that delivers two breaks the promise. Never reorder tutorial steps; the only move is result-first, a deliberate callback copy of the result shot at the head.
4. **Pick the hook** (mechanics in `story-and-hook.md`). Explainer: the counterintuitive answer or a specific "you" question within 2 s, holding back the mechanism. Tutorial: the finished result on screen plus the outcome in words ("Clean grout in two minutes"). Listicle: count, promise and audience in the first sentence.
5. **Cut to the style.**
   - **Explainer:** pauses per `cutting-and-pacing.md` (calm teaching 350–700 ms, conversational 250–450 ms); keep the pause before the answer and a 0.5–1.2 s beat after it.
   - **Tutorial:** never cut inside an action the viewer must copy. Compress waiting with 2–8× speed or a hard cut plus a "2 min later" label. Hold each completed step's result for about 1 s or more [X].
   - **Listicle:** a picture cut at every item boundary, picture-only if the speech flows. Trim fillers from ordinals and names.
6. **Frame.** Explainer: 0–2 punch-ins, on the answer or key number; none for calm speakers. Tutorial: never crop hands or screen; zoom inside the recording to the action area, eased over 0.3–0.6 s [X]. Listicle: one consistent item marker (a counter pill, alternating 1.0×/1.25× framing on 4K, or both), identical every time.
7. **Insert.**
   - **Explainer:** proof and structure only (the chart, the study headline, a number card). Full-screen only for reads under 3 s that the voice names; otherwise split or PiP. Annotate with one arrow, box or underline placed on the detail, appearing on the word that names it.
   - **Tutorial:** during steps the screen or hands are the main track (split, or full-screen POV with face PiP); the face returns for the why, warnings and the result. Capture screens at phone viewport, crop so key text reaches about 45 px, blur private data. Hard-cut or speed through loading spinners and aimless cursor travel, but keep each tap or click and the state it produces on screen together [X].
   - **Listicle:** at most one visual per item, entering on its name, with identical layout and timing across items.
8. **Text.** A hook title only if it passes the mute test; for tutorials include the searchable task words [X]. Step labels and item pills ("Step 2 · Add the formula", "2/3 · Raycast"): top band, enter on the ordinal, stay through the segment, 5 words or fewer. Captions stay verbatim and move to the clear band during screen inserts; when a card shows a number, the caption drops its accent. If a card carries the spoken sentence, hide that caption page rather than show it twice; short labels can coexist, as the redundancy cost is small (d = 0.10) and fades for short text [L].
9. **Sound: choose none first.** Explainer: no music unless creator memory asks. Tutorial: keep the real action sound; a light bed may carry silent process stretches and ducks under instructions. Listicle: a light bed is allowed; never move item cuts to beats.
10. **Review.** Run the critic questions, then delete any insert, label or sound the video is not worse without.

## When to break it

- **Rapid-fire lists from high-energy creators:** 3–4 s items, often 10–12 of them, 200–300 ms pauses (or the speaker's own if shorter), a bed and one marker sound (the Hormozi and Abdaal formats). The 3–5 item prior is for items that each need a reason.
- **A clean single take from a clear speaker:** trim the head and tail, caption it, stop. Zero inserts, zooms, music and SFX is a finished edit when the words already carry the idea.
- **Visual explanations** (a physical demo, the shape of a graph): the visual may dominate, as in a tutorial or Cleo Abram's graphics-led mode, when the creator supplies the material.
- **Sensitive education** (health, grief, debt shame): no counters, pills, music or SFX, and plain captions.
- **Countdowns that promise "number one is the best":** the countdown is the structure, so never reorder.
- **Body-as-screen tutorials** (makeup, exercise form): the creator's body is the demonstration. Keep the full movement in frame and skip punch-ins.
- **Creator memory** beats every prior here.

## Worked example

"Priya", productivity creator, medium energy (172 wpm), 68 s raw including an 11 s tail, 4K30 HDR. She supplied screen recordings for Raycast and Tally, none for Obsidian.

| IDs (raw s) | Line | Beat |
|---|---|---|
| w0–14 (0–4.1) | "Okay, so, um, people keep asking what I use, so here are my, uh, my—" | PREAMBLE |
| w15–27 (4.1–9.0) | "Three free apps that replaced forty dollars a month of subscriptions for me." | HOOK |
| w28–45 (9.0–15.8) | "First: Obsidian. It's a notes app, it's free, and everything lives on your laptop, so it's, like, instant." | ITEM 1 |
| w46–60 (15.8–21.9) | "It also has this graph view, which looks cool, but honestly I never use it." | TANGENT |
| w61–68 (21.9–25.4) | "Second— wait, no, let me do that again." | FALSE START |
| w69–92 (25.4–36.0) | "Second: Raycast. You hit command-space, type 'clip', and your whole clipboard history is right there. That replaced a four-dollar app and a six-dollar app." | ITEM 2 |
| w93–117 (36.0–50.2) | "And third, the one that saved me the most: Tally. Unlimited forms, unlimited responses, free. I was paying Typeform thirty dollars a month for that." | ITEM 3 |
| w118–129 (50.2–56.8) | "So yeah, those are my three. Comment which one you're trying first." | RECAP + CTA |

**Decisions**
- **Head:** cut w0–14; "Three" lands at 0.2 s, hook whole at 1.0×. Hook title "3 free apps that replaced $40/month", 0–3.0 s (6 words, ≥2.8 s dwell).
- **Order:** ordinals are spoken, so no reorder. The strongest item is already last; item 1, the weakest, is trimmed hardest.
- **Body:** cut w46–68. The tangent undercuts the item and the false start has a later re-delivery, so the single seam falls on the item 1 → 2 boundary. Keep "it's, like, instant": a lone mid-clause filler that carries her voice. Cut the recap (w118–123) and the tail; keep the protected CTA and end 0.4 s after "first".
- **Item marker:** item 1 opens at 1.25× on "First", item 2 at 1.0× across the seam, item 3 at 1.25× on "And third" (a picture-only cut; the speech flows). Pills "1/3 Obsidian", "2/3 Raycast", "3/3 Tally" enter on each ordinal. No other zooms.
- **Inserts:** item 1 gets none; a stock "notes on laptop" clip was rejected as decoration. Item 2: split screen of her recording from "command-space" (w73) to "right there" (w83), about 3.8 s, zoomed 1.8× so list text renders about 50 px, one highlight box on "whole clipboard history". Item 3: split of Tally from "Unlimited" through "free", then her face for the payoff with an over-the-shoulder card "$30/mo → $0" entering on "thirty" (w112), held 2.0 s.
- **Captions:** phrase style, mixed case, accents on app names and dollar amounts; clear band during both splits.
- **Sound:** none. Her past list videos carry no music (creator memory), and both splits ask the viewer to read. No SFX: the pill and the framing change already mark each item.
- **Result:** about 39 s, 2 internal seams. Rejected: Tally first, a whoosh per item, zooms on dollar figures, a bed under the two reading-heavy splits.

**Tutorial variant.** "This is how you get a stripped screw out in ten seconds." Result first (the screw backing out, 0–1.5 s) under the spoken hook. Her POV hands footage is the main track, with labels "1 Rubber band on the head" and "2 Press hard, turn slowly". Instructions at 1.0×; 20 s of failed turning becomes a hard cut. The screw's real squeak stays, no music. End on the freed screw, held 1 s.

## Anti-patterns

- Decorative stock or AI visuals (a lightbulb for "idea", typing hands for "productivity") instead of proof.
- Desktop screenshots squeezed to 1080 px wide; captions covering the UI being demonstrated.
- Cutting mid-action, skipping a step the voice mentions, or never showing the finished result.
- Speeding up instructions, or a flat explainer to fake energy.
- Highlighting every other word; an accent, a card and an arrow competing at once.
- A whoosh, zoom pulse and emoji on every list item, or a different item device per item.
- "1/10" on screen at item one; reordering a list with spoken ordinals; padding weak items.
- Lyrics or a busy bed under explanation; memes in serious teaching; a recap after the payoff.

## Critic questions

Answer each yes or no from the rendered video.

1. After one watch, could a viewer state the answer, repeat the steps, or name the items?
2. Does every insert, label and card show what the voice is saying at that moment?
3. Is every text element the viewer must read up long enough (0.3 s per word + 1 s) and large enough to read on a phone?
4. In a tutorial, is every step both said and shown, in order, with no cut inside an action?
5. Does the video end within about 0.5 s of the answer or last item (or after a result hold of 2 s at most)?
6. Is at most one element highlighted at any moment?
7. Do the hook, instructions and answer play at natural speed?
8. In a listicle, is the count stated in the first 3 s, and does each item start with the same marker?
9. Is the face on screen for the hook, the key claim and the CTA?
10. If music is present, is it instrumental and clearly below speech throughout?
11. Could a viewer skipping back find each step or item from its label?
12. Is every insert, label, zoom and sound one the video would be worse without?

## Sources

- Guo, Kim & Rubin 2014, edX engagement: https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf
- Kizilcec et al. 2014, showing face: https://rene.kizilcec.com/wp-content/uploads/2014/01/final_version2.pdf
- Sondermann & Merkt 2022, talking heads (N = 112): https://doi.org/10.1016/j.compedu.2022.104675
- Rey et al. 2019, segmenting meta-analysis: https://link.springer.com/article/10.1007/s10648-018-9456-4
- Schneider et al. 2018, signaling meta-analysis: https://www.sciencedirect.com/science/article/abs/pii/S1747938X17300581
- Mayer 2023, research-based principles (coherence 18 of 19, d = 0.86; signaling; redundancy; spatial and temporal contiguity; from Mayer 2021): https://www.unh.edu/teaching-learning-resource-hub/sites/default/files/media/2023-06/itow-research-based-principles-for-designing-multimedia-instruction-mayer.pdf
- Mayer, Heiser & Lonn 2001, irrelevant video clips lower transfer: https://doi.org/10.1037/0022-0663.93.1.187
- Sundararajan & Adesope 2020, seductive details: https://link.springer.com/article/10.1007/s10648-020-09522-4
- Fiorella et al. 2017, first-person video modeling: https://doi.org/10.1037/edu0000161
- Cowan 2001, the magical number 4: https://doi.org/10.1017/S0140525X01003922
- Murdock 1962, serial position effect: https://doi.org/10.1037/h0045106
- Villar et al. 2013, progress indicators: https://doi.org/10.1177/0894439313497468
- Moreno & Mayer 2000, sounds and music: https://doi.org/10.1037/0022-0663.92.1.117
- Kämpfe et al. 2011, background music: https://doi.org/10.1177/0305735610376261
- Torcoli et al. 2019, ducking levels: https://doi.org/10.17743/jaes.2019.0052
- MacGregor et al. 2010, silent pauses: https://www.research.ed.ac.uk/en/publications/listening-to-the-sound-of-silence-disfluent-silent-pauses-in-spee/
- Apple HIG, typography: https://developer.apple.com/design/human-interface-guidelines/typography
- BBC Subtitle Guidelines, timing §4.1 and §4.3: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
- PandaStudio, retention laws (13 Shorts): https://www.writepanda.ai/blog/retention-editing-7-laws
- PandaStudio, Ali Abdaal Shorts: https://www.writepanda.ai/blog/how-to-edit-shorts-like-ali-abdaal
- PandaStudio, Hormozi Shorts: https://www.writepanda.ai/blog/hormozi-style-shorts-editing
- PandaStudio, Fireship: https://www.writepanda.ai/blog/fireship-editing-style-measured
- OpusClip, TikTok hook types: https://www.opus.pro/research/best-video-hooks-tiktok
- TikTok, Creator Rewards Program: https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program
- Socialinsider, Reels length: https://www.socialinsider.io/blog/instagram-reels-length/
- vidIQ, 331M Shorts: https://www.linkedin.com/posts/vidiq_we-analyzed-331-million-youtube-shorts-published-activity-7478837949355388930-moa_
