# Cutting and pacing

Load this file at the story cut and fine cut: whenever you decide which words, takes, pauses and fillers survive, where seams go and how each is treated. Reload it when a finishing change moves a seam, or when a critic calls the edit choppy, rushed, draggy or jumpy. Speed lives in `speed.md`, punch-in cover in `framing-and-zooms.md`, seam audio in `voice-and-loudness.md`. The main failure this file guards against is over-cutting. On a 50 s calibration take with three false-started hook attempts and one mid-sentence restart, a professional trimmed the head, made one mid-video content splice at a sentence boundary, removed a 0.27 s stumble and tightened the two longest stalls; the old engine made nineteen dead-air micro-splices.

## Principles

1. **Cut where a thought ends; the unit is the beat, not the silence.** *Why:* viewers segment events at breaks in action and meaning and blink at those breakpoints [L], so a seam there is nearly free and a seam mid-thought reads as a stumble.
2. **Protect emotion first, then story, then rhythm.** *Why:* Murch's order; viewers remember how the speaker made them feel, not a few pixels of head shift [P].
3. **Remove what wastes time; keep what makes the speaker human.** *Why:* preamble, false starts and dead air cost attention, while natural pauses, the odd filler and laughs carry meaning, and fillers even aid recall [L].
4. **Pauses are punctuation: keep the speaker's rhythm, trim only outliers.** *Why:* in audio tests shortened sentence pauses were not penalized, so shaving one costs mainly a visual seam; listeners do mark down uniformly tiny or very long pauses, and silences past ~0.6 s start to mean something [L].
5. **Every seam has a price.** *Why:* the old engine's 19 dead-air micro-splices sounded choppy, and the pro's four small mid-video edits did not [I]. Justify each seam by what it removes, never by a quota.
6. **Embrace a jump cut between beats; hide one inside a thought.** *Why:* at a beat change a jump reads as punctuation; mid-sentence it reads as an error.
7. **Continuous sound hides picture cuts.** *Why:* edit blindness mostly vanished when the soundtrack was removed [L]; a click or room-noise change exposes every seam.
8. **Tempo is structure, not a metronome.** *Why:* good editing groups similar shot lengths into packets and shifts between them for tension and release [L][P].
9. **Match cut density and pause length to measured energy and content.** *Why:* fast cutting on a calm speaker looks manic, and fast pacing plus dense or emotional content overloads viewers [L].
10. **A false start needs a later re-delivery; a spoken payoff or CTA is never a false start.** *Why:* the old engine deleted a real CTA by guessing [I].
11. **Leaving a take alone is an edit.** *Why:* over-editing is the documented failure; zero seams is valid.

## Defaults and ranges

Starting priors only. No study links cut rate, pause length or filler rate to organic retention of talking-head Shorts. State a one-line reason when you leave a range. Creator memory overrides priors, never validators.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Gap inside a phrase under ~200 ms | Not a picture seam: likely articulation (a stop closure alone can last ~180 ms). Research pause floors run 100–250 ms; agent editors call <150 ms unsafe | [L] [P] | [Llisterri](https://joaquimllisterri.cat/phonetics/LBASS_21/LBASS_21_5_pauses.html), [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md) |
| Gap left by a mid-phrase cut | Close it to the speaker's own articulation gap (often under ~100 ms). In a tight phrase, a gap was judged disfluent on half of trials at ~126 ms | [L] one phrase | [Warner 2022](https://doi.org/10.1016/j.jfludis.2022.105896) |
| Sentence-boundary pause 250–550 ms | Keep as spoken. The median silent pause in read English is 493 ms (all pauses, 44 min of read text) | [L] | [Campione & Véronis](http://sprosig.org/sp2002/pdf/campione-veronis.pdf) |
| What our pro did | Kept ~17 of 22 pauses ≥300 ms to the millisecond (silence 32% → 30%); tightened only the 577 and 690 ms stalls | [I] n=1 | `backend/eval/pro_cut_reference.py` |
| How short is too short | Read textbook passages sounded most natural at ~0.6 s within and 0.6–1.2 s between sentences; 0.075–0.15 s and ≥2.4 s least natural. In recorded read and phone speech, raters did not prefer original sentence pauses over 5 ms ones (51–53%), but did over long ones (55–75%) | [L] audio | [Liu 2022](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.778018/full), [Owoicho 2024](https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf) |
| Mid-thought hesitation 0.6–1.5 s | Tighten to 250–400 ms or the speaker's median. Listeners adjusting a pause between two words put the minimal hesitation pause at ~505 ms on average (optimal fluent pause 186 ms) | [X] [L] one small 1973 study | [Ruder 1973](https://journals.sagepub.com/doi/10.2466/pms.1973.36.1.47) |
| Deliberate beat | 0.5–1.2 s after a key claim or before a reveal (storytime to ~1.5 s). Words heard after a mid-utterance silent pause were recognised better later, so a beat before the reveal word earns its place | [X] [L] audio | [MacGregor 2010](https://www.research.ed.ac.uk/en/publications/listening-to-the-sound-of-silence-disfluent-silent-pauses-in-spee/) |
| Dead air >~1.5 s | Cut, unless the silence is the content | [X] | — |
| Turn gap, two speakers | 0–250 ms. In all 10 languages studied, the modal gap was 0–200 ms (mean 208 ms). Answers to requests sounded less willing from a ~600 ms gap | [L] | [Stivers 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC2705608/), [Roberts & Francis 2013](https://doi.org/10.1121/1.4802900) |
| Filler budget | A natural rate is ~6 disfluencies of all kinds per 100 words (~9/min at 150 wpm). In crowdsourced ratings of a speech, 5 fillers/min "may be acceptable" and 12/min hurt perceived effectiveness. Prior: none in the first sentence, ≤3–5/min elsewhere | [L] → [X] | [Bortfeld 2001](https://journals.sagepub.com/doi/10.1177/00238309010440020101), [Laske & DiGennaro Reed 2024](https://onlinelibrary.wiley.com/doi/abs/10.1002/jaba.1093) |
| Fillers and comprehension | Fillers improved story recall where same-length coughs hurt it; "uh" sped recognition of the next word | [L] audio | [Fraundorf & Watson](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/), [Fox Tree 2001](https://link.springer.com/article/10.3758/BF03194926) |
| Cut padding | 30–200 ms; ASR timestamps drift 50–100 ms (raw Whisper averages ~120 ms and drifts more on disfluent speech, so snap to forced-aligned times; `captions-and-text.md`) | [P]; Whisper figure [V] | [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md) |
| Audio crossfade | 10–30 ms equal-power at a seam in room tone (speech across a seam is not phase-coherent; video-use fades 30 ms). A couple of ms suffices in true silence or on a stop consonant; 30–100 ms across a breath; never overlap two words | [P] + [X] | [SOS comping](https://www.soundonsound.com/techniques/vocal-comping-editing), [SOS fades](https://www.soundonsound.com/techniques/using-fades-crossfades), [video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md) |
| Edit point inside speech | Silence first; else a stop closure (p, t, k) or a noisy-consonant onset (s, sh, ch), which hides an edit as well as a breath. Never inside a vowel | [P] | [SOS comping](https://www.soundonsound.com/techniques/vocal-comping-editing) |
| Room noise at a seam | Room tone from the take's own pauses, never digital silence. Background-level jumps above ~4–5 dB are audible | [P] music editing | [Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise), [ebrary, *Classical Recording*](https://ebrary.net/300232/education/crossfades) |
| A/V sync at a split edit | Detectable at +45 ms (audio early) / −125 ms (audio late) | [L] ITU subjective tests | [ITU-R BT.1359](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) |
| Unnoticed continuity cuts | ~25% of cuts between two views of a scene went unnoticed, ~33% on a motion onset; removing post-cut motion or the soundtrack exposed cuts | [L] Hollywood film, not jump cuts | [Smith & Henderson](https://bop.unibe.ch/JEMR/article/view/2264), [Smith 2017](https://eprints.bbk.ac.uk/id/eprint/14904/) |
| Seams per 60 s, single take after false starts go | 1–4 as a prior for a clean take; a critic signal, never a gate. The calibration take was not clean: after the head trim the pro made 4 small mid-video edits in ~42 s of output (1 content splice, a 0.27 s stumble, 2 stall tightenings: ~6/min all-in, ~1.4/min counting content splices), each removing a named problem. Head and tail trims do not count | [I] n=1 → [X] | `backend/eval/pro_cut_reference.py` |
| Visual change interval | 3–8 s, median ~5 s (13 Shorts, 4 creators); one DOAC clip tightened to ~2.5–3 s before its payoff. Static ≥8 s: see step 8 | [V] n=13; n=1 | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws), [DOAC](https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips) |
| Lab "fast" | ~0.5–0.67 cuts/s (a 1.5–2 s average shot), so a ~5 s median is slow to medium. Fast pacing plus arousing content lowered recognition and recall | [L] TV messages; rates unverified at source | [Collier 2010 (dissertation)](https://exa.ai/library/publication/s49x2k62711), [Lang 1999](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504) |
| Vendor cadence claim | "Pattern interrupt" every 4 s: 58% vs 41% retention; method undisclosed, do not adopt | [V] | [OpusClip](https://www.opus.pro/blog/tiktok-length-format-retention-data) |
| Platform position | TikTok gives no cut-frequency number; seamless transitions +60% recall, "editing shouldn't be frenetic" | [A] ads | [TikTok/Lumen](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf) |

**Kept boundary pause by energy** [X]:

| Energy / style | Boundary pause | Deliberate beats |
|---|---|---|
| High: hot take, sales, UGC | 200–300 ms, or the speaker's own if shorter | rare, ≤0.6 s |
| Conversational: founder, explainer | 250–450 ms | 0.5–0.9 s |
| Calm: educational, storytime | 350–700 ms | 0.8–1.5 s |
| Comedy | performer's own timing; cut dead air only | none added ([Attardo & Pickering](https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf), weak) |
| Podcast clip | 200–300 ms in a turn; 0–250 ms between turns | only on a reveal |

A relative rule usually beats the table: keep boundary pauses within ~0.7–1.3× the speaker's own median and tighten gaps above ~2× it [X].

## How to decide

1. **Use measured signals, never model timestamps:** words (IDs, verbatim with fillers), gaps (duration, energy-minimum snap point, breath flag, room tone), prosody (pitch fall, intensity), visual events (blink, look-away, stillness, face box), retake clusters.
2. **Story pass as a radio test.** Pin the hook, thesis, payoff and CTA ranges. Mark preamble, false starts that have a later re-delivery, and anything after the last payoff or CTA word. The audio alone must tell the story.
3. **Choose takes.** Prior: the last complete take, but delivery decides: our pro kept the earlier of two complete hook deliveries [I]. Override on conviction and energy (intensity, pitch-range z-scores), eyes on lens, wording, completeness, fluency. A flawed, alive take beats a clean, flat one. Comp phrase by phrase only at a thought boundary where face scale, position and light match. The joined result must play as what [Pearlman](https://blog.routledge.com/humanities-and-media-arts/cutting-rhythms-creative-film-editing/) calls "a single flow of energy and intention" [P]: energy, pitch and pace carry across the seam instead of resetting. A repeat is *deliberate* (keep) when it adds emphasis: "Two weeks. Two weeks.", a triplet, a callback. It is a *retake* when the earlier attempt is incomplete, corrected or weaker and the same clause restarts.
4. **Classify every gap, then set its length.** Articulatory (<~200 ms): leave. Boundary: keep near the speaker's median. Hesitation (mid-clause, often after a filler, no final pitch fall): tighten. Deliberate beat (sentence-final fall, eyes held, after a key claim or before a reveal): keep. Dead air: cut. Shorten a gap by removing its middle so padding and the next inhale stay.
5. **Fillers and stutters.** Count per minute; the hook's first sentence gets none. Cut fillers that are sentence-initial or bounded by ≥150 ms gaps, since those seams fall between thoughts. A lone mid-clause filler stays unless it is in a cluster ("like, you know, um") or on the thesis; if it must go, plan a cover first. Coarticulated fillers ("and-um-so") may be cut at a ≥20 ms stop closure or a noisy-consonant onset with a 5–10 ms crossfade, closing the gap to the speaker's articulation gap, and only if the click detector and an ASR round-trip over the seam pass [X]. Stutters follow the same rule: "it's, it's" with a 90 ms gap stays.
6. **Treat each seam.** *Invisible:* same framing, still head, seam in silence (the default for pause trims). *Embraced jump:* between beats. *Scale change:* to hide a pose jump, at least ~1.25× (1.3–1.5× preferred), bounded by resolution; 1.15–1.25× reads as a lean-in, under ~1.1× as an accident. The common "15–20%" figure is lore ([30-degree rule](https://en.wikipedia.org/wiki/30-degree_rule) [P]; sizes in `framing-and-zooms.md`). *Cutaway:* an insert spanning the seam with continuous voice. *Crossfade:* audio only; never dissolve a talking head into itself.
7. **Place it J/L style.** Audio seam at the acoustic minimum. The picture cut may sit anywhere in the shared silent gap where the mouth is closed on both sides: pick a blink, the frame after a completed gesture, or the incoming side's motion onset, since post-cut motion hides cuts [L]. For inserts, leave the face after the keyword's onset and return on or just before the next sentence starts, so the viewer sees the mouth begin the thought [X]. With two speakers, let the next voice lead the picture: switch 0–300 ms after the new speaker's first word, because viewers' eyes reach a new speaker about 0.3 s after speech starts [L] (`styles/podcast-sales-founder.md`), or L-cut to a reaction. Stay inside ITU sync tolerance.
8. **Check tempo.** Plot seam and visual-change intervals per section: roughly consistent within a section, shifting on purpose between them (tighter into the payoff, slower on emotional or dense lines). A static stretch over ~8 s goes to finishing as a question; "nothing" is a valid answer.
9. **Leave it alone when** the take passes the radio test, has no retakes, no mid-thought stall over ~1 s, and fillers within budget with none in the hook. Then do head and tail trims plus 0–2 seams.
10. **Render, then watch and listen.** Click detector and ASR round-trip at every seam. Then watch once straight through at 1× on a phone-sized render, sound on, before reading any metric, and note where attention drifted or a jump registered [X]. Answer the critic questions, and restore any edit you cannot justify in one line.

**Murch's rule of six, adapted.** Sacrifice from the bottom up: "don't ever give up emotion before story" ([Murch](https://blogs.ischool.berkeley.edu/i290-viznarr-s12/the-rule-of-six-walter-murch/), [P]).

| Murch (weight) | Talking-head question | Levers |
|---|---|---|
| Emotion (51%) | Keeps the feeling: the convinced take, the laugh, the breath before a hard admission? | take choice, restore IDs |
| Story (23%) | Every kept line advances the argument; setup intact for the payoff? | cut/restore ranges |
| Rhythm (10%) | Seam after the thought completes, pause fitting the section tempo? | gaps, seam position |
| Eye-trace (7%) | Eyes where the viewer was already looking? | picture cut frame, punch offset |
| Planarity (5%) | Does the face slide sideways or the eyeline flip across the seam in the same framing? Match or change clearly | punch, offset, in-point |
| 3D space (4%) | Light, wardrobe, background match across comped takes? | take choice, color match, cover |

## When to break it

- **Listicles:** a metronomic hard cut per item is the device.
- **Comedy:** no dead air, but never trim the performer's timing or the laugh.
- **Confessions:** hold 1.5–3 s of silence when eyes stay on lens and the face is thinking; the silence is the content.
- **Signature jump-cut style** on request: many visible jumps, still on word and phrase boundaries.
- **Reading notes:** a look-away justifies a mid-thought seam; cover it.
- **Multi-take rambles** need many seams; the 1–4 per minute prior does not apply.

## Worked example

Raw: 33.4 s, 4K60, medium-energy fitness creator, median boundary pause 420 ms.

> w0–w2 "Um, okay, so—" · gap 0.80 s · w3–w13 "Most people who quit the gym don't quit because they're lazy." · gap 0.44 · w14–w21 "They quit because, uh, they… they program for the—" (eyes drop to notes) · gap 1.30 · w22–w38 "They quit because they program for the person they want to be, not the person they are." · gap 0.90 (pitch falls, eyes on lens) · w39–w41 "Like, you know," · gap 0.19 · w42–w61 "if you've never, um, trained before and you start at six days a week, that lasts two weeks. Two weeks." · laugh 0.70 · w62–w73 "So here's what I'd do. Three days. Full body. Forty-five minutes. That's it." (gaps 250–320 ms) · gap 2.10 (reaches for phone) · w74–w86 "And if you want my, uh, beginner plan, it's, it's free, link in bio."

| Where | Decision | Why |
|---|---|---|
| w0–w2 + 0.80 s | Cut; in-point ~120 ms before w3 | Throat-clearing; the kept inhale stops "Most" sounding chopped |
| w14–w21 + 1.30 s | Cut, leaving ~0.42 s | w22–w38 re-delivers the clause completely: confirmed false start |
| Seam "lazy." → "They" | In-point after the look-away clears; head sits lower, so a clear 1.3× punch on "They" (4K allows it), not a micro-jump; 20 ms equal-power crossfade in room tone | An honest jump would also pass between beats; the punch earns its place by marking the turn from myth to reason |
| 0.90 s after w38 | Keep ~0.8 s | The thesis needs a beat |
| w39–w41 | Cut; seam inside that beat | Sentence-initial, clean gaps; kept, the cut runs ~9 fillers/min |
| w45 "um" | Keep | Coarticulated, mid-clause; cutting costs a mid-thought jump |
| "Two weeks. Two weeks." | Keep both | Second is lower and slower: emphasis, not a retake |
| Laugh | Keep | Emotion outranks rhythm |
| w62–w73 | Keep verbatim | Its own short gaps already tighten tempo into the payoff |
| 2.10 s gap | Tighten to ~0.4 s; in-point after the hand settles | Dead air at a beat boundary |
| w74–w86 | Keep all; end 0.3 s after "bio." | CTA is payoff; the 90 ms stutter is uncuttable and cutting "uh" would jump mid-CTA |

Result: ~24 s, three seams (~7.5/min), above the 1–4 prior and still correct: each seam removes a named problem (a false start, a filler cluster, a 2 s stall), and none exists to add energy. Two fillers remain (~5/min), neither in the hook or thesis. The ~11 s static stretch (w42–w73) goes to finishing as a question, not a forced cut.

## Anti-patterns

- Silence-threshold spraying: cutting every gap over N ms (19 dead-air micro-splices where the pro tightened two stalls).
- Squeezing every pause to 100–150 ms until the speaker sounds breathless.
- Cutting a mid-clause filler with no cover: trading an unnoticed "um" for a noticed jump.
- Chasing zero fillers, and deleting laughs and personality flubs with them.
- Calling a line a false start with no later re-delivery; cutting the CTA as a "sign-off".
- Deleting deliberate repetition or callbacks as duplicates.
- Always picking the cleanest take, or the first or last by rule.
- Seams or zooms every N seconds, or random intervals faking rhythm.
- Moving a speech seam to hit a music beat.
- Micro-jumps "fixed" with a 5–10% zoom.
- Bad seam audio: no crossfade, digital silence, noise jumps, clipped inhales, an edit inside a vowel, a new 150 ms+ gap left inside a phrase.
- Comping a clean line from another take that resets the speaker's energy mid-argument.
- Speeding up speech before cutting dead air.
- Frenetic cutting through dense or emotional lines.

## Critic questions

1. Does every visible jump fall at a sentence or clause end, with none mid-thought unless covered?
2. Is there a click, pop, drop to silence, or noise or level change at any seam?
3. Is the first or last sound of any word clipped at a seam?
4. Does any sentence start without its natural inhale, sounding chopped in?
5. Is there a mid-sentence stall over about half a second that sounds like lost place?
6. Do sentence boundaries ever butt together with no audible pause?
7. Is there a silence over about 1.5 s that is not clearly a deliberate beat?
8. Does any line appear twice with the first attempt incomplete or weaker?
9. Are the spoken payoff and CTA complete, ending within about 0.5 s of the last word?
10. Is there a filler in the first sentence, or do fillers become noticeable anywhere?
11. Is there a jump where framing stays the same but the head visibly shifts?
12. Is the cutting evenly spaced like a metronome instead of tightening into the payoff and slowing on emotional or dense lines?
13. Does every seam remove a named problem? Would the video lose anything if the weakest one were restored? If not, restore it.

## Sources

- [Murch, rule of six excerpt](https://blogs.ischool.berkeley.edu/i290-viznarr-s12/the-rule-of-six-walter-murch/)
- [Pearlman, Cutting Rhythms](https://blog.routledge.com/humanities-and-media-arts/cutting-rhythms-creative-film-editing/)
- [Nakano 2009, eyeblink synchronization](https://pmc.ncbi.nlm.nih.gov/articles/PMC2817301/)
- [Magliano & Zacks 2011, event segmentation](https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/952/files/2017/09/maglianoandzacks2011-22vhbrv.pdf)
- [Smith & Henderson 2008, edit blindness](https://bop.unibe.ch/JEMR/article/view/2264)
- [Smith & Martin-Portugues Santacreu 2017, match-action](https://eprints.bbk.ac.uk/id/eprint/14904/)
- [Cutting 2010, shot-length structure](https://jordandelong.com/pubs/2010/AttentionEvolution.pdf)
- [Lang 1999, pacing and arousal](https://www.tandfonline.com/doi/abs/10.1080/08838159909364504)
- [Collier 2010, fear and production pacing (dissertation)](https://exa.ai/library/publication/s49x2k62711)
- [Campione & Véronis 2002, pause durations](http://sprosig.org/sp2002/pdf/campione-veronis.pdf)
- [Liu 2022, pause duration and impressions](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.778018/full)
- [Owoicho 2024, pause perception](https://www.isca-archive.org/speechprosody_2024/owoicho24_speechprosody.pdf)
- [Ruder 1973, silent-interval duration as a pause cue](https://journals.sagepub.com/doi/10.2466/pms.1973.36.1.47)
- [Llisterri, silent pause thresholds](https://joaquimllisterri.cat/phonetics/LBASS_21/LBASS_21_5_pauses.html)
- [MacGregor 2010, silent pauses and memory](https://www.research.ed.ac.uk/en/publications/listening-to-the-sound-of-silence-disfluent-silent-pauses-in-spee/)
- [Stivers 2009, turn-taking](https://pmc.ncbi.nlm.nih.gov/articles/PMC2705608/)
- [Roberts & Francis 2013, tolerance for silent gaps](https://doi.org/10.1121/1.4802900)
- [Hirvenkari 2013, gaze follows the speaker](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0071569)
- [Warner 2022, gap duration and perceived disfluency](https://doi.org/10.1016/j.jfludis.2022.105896)
- [Bortfeld 2001, disfluency rates](https://journals.sagepub.com/doi/10.1177/00238309010440020101)
- [Laske & DiGennaro Reed 2024, filler rates](https://onlinelibrary.wiley.com/doi/abs/10.1002/jaba.1093)
- [Fraundorf & Watson 2011, fillers and recall](https://pmc.ncbi.nlm.nih.gov/articles/PMC3134332/)
- [Fox Tree 2001, um and uh](https://link.springer.com/article/10.3758/BF03194926)
- [Attardo & Pickering, joke timing](https://faculty.tamuc.edu/lpickering/Pdfs/Publish_11.pdf)
- [ITU-R BT.1359, A/V timing](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf)
- [Sound On Sound, vocal comping](https://www.soundonsound.com/techniques/vocal-comping-editing)
- [Sound On Sound, fades](https://www.soundonsound.com/techniques/using-fades-crossfades)
- [Idyll Sounds, room tone](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise)
- [ebrary, *Classical Recording* (Decca), crossfades](https://ebrary.net/300232/education/crossfades)
- [video-use SKILL.md](https://github.com/browser-use/video-use/blob/main/SKILL.md)
- [30-degree rule](https://en.wikipedia.org/wiki/30-degree_rule)
- [TikTok/Lumen creator ads](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf)
- [PandaStudio, 7 laws (vendor)](https://www.writepanda.ai/blog/retention-editing-7-laws)
- [PandaStudio, DOAC (vendor)](https://www.writepanda.ai/blog/how-diary-of-a-ceo-edits-podcast-clips)
- [OpusClip retention data (vendor)](https://www.opus.pro/blog/tiktok-length-format-retention-data)
- Yunicorn pro-cut calibration — `backend/eval/pro_cut_reference.py`