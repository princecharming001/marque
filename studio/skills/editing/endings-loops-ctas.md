# Endings, loops and calls to action

Load this file at the story cut, when you pin the payoff and CTA word IDs and choose the ending, and at QC for the last three seconds and the restart seam. Reload it when a chat edit touches the final beat, the CTA or the first line; in a looping player those are neighbours. Related: hooks in `story-and-hook.md`, seams in `cutting-and-pacing.md`, the music button in `music.md`, CTA text in `captions-and-text.md`, platform wording in `platforms.md`.

## Principles

1. **Stop when the meaning stops, not when the reveal happens.** The final payoff is the last line that adds something: verdict, proof, how to apply it, punchline. *Why:* viewers judge film clips and ads mostly by the peak and the final moment, not the length [L] ([Fredrickson & Kahneman 1993](https://doi.org/10.1037/0022-3514.65.1.45); [Baumgartner et al. 1997](https://doi.org/10.1177/002224379703400203)). Completion feeds ranking on TikTok and Instagram [A], so every empty second at the end is another second to swipe.
2. **A CTA the creator said is part of the payoff. Never invent one.** *Why:* the old engine deleted a real closing CTA as a "false start" [I]. The creator knows what the video is for; a line they never said puts words in their mouth.
3. **No appended outros, end cards, logo stings or fades to black.** *Why:* measured top Shorts cut right after the final payoff word, with "no fade, no 'follow for more'" [V]. Platforms replay automatically, so an outro is dead air before the replay, and a fade tells the viewer it is over [X].
4. **The last frame is a seam, every time.** *Why:* TikTok, Reels and Shorts loop, so every viewer who stays sees the last frame cut to frame 0 [X]. Keep it clean even without a designed loop.
5. **Build a loop only when the creator's words already make one.** *Why:* YouTube leaves loops out of engaged views, which YPP earnings use, and TikTok Creator Rewards counts one view per account [A]. A loop built from mangled words inflates vanity numbers and earns nothing; the first-time viewer comes first.
6. **An open ending must still keep the video's own promise.** *Why:* the Zeigarnik "unfinished things stick" claim failed a 2025 meta-analysis, though people do tend to *resume* interrupted tasks (Ovsiankina) [L]. A tease can pull viewers to a part two that exists; withholding this video's payoff breaks the hook's promise. Curiosity is a knowledge gap [L]: close the one you opened.
7. **One ask, specific and tied to the content.** *Why:* questions raised comment counts on brand posts [L]. Facebook demotes engagement bait but exempts genuine requests for advice [A]. Each extra ask adds time and dilutes the first.
8. **Match the ask to the moment.** *Why:* people who spot a persuasion tactic re-evaluate its user (persuasion knowledge model) [L]. A sales tag right after a sincere admission costs trust.
9. **"No CTA" is a valid ending.** *Why:* comedy ends on the button, and an emotional line can end on silence.
10. **The usual ending edit is one trim.** *Why:* over-editing is the main failure; our pro left the closing CTA untouched [I]. If the creator stops cleanly, set the out-point and add nothing: no loop surgery, text prompt, SFX or music hit, unless the video is worse without it.

## Defaults and ranges

Starting priors; give a one-line reason whenever you leave a range. Creator memory overrides them but never a validator: pinned CTA and payoff IDs survive.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Last kept word → last frame (face, nothing new on screen) | 0.15–0.5 s, target ~0.3 s; end before the head drops, the eyes leave the lens or a reach starts | [I] [X] | design_doctrine.md §16; `critique.md` |
| What measured top Shorts do | The cut lands right after the final payoff word; the last 1.5–2 s show a completed state, such as the answer on screen (n=13, hand-annotated). OpusClip's "hold the final frame for 1 to 2 seconds" (method not shown) applies only when that frame shows something new | [V] | [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws), [OpusClip](https://www.opus.pro/blog/tiktok-length-format-retention-data) |
| Held visual ending (result, card, finished dish) | Result on screen 1.5–2 s in total, entering under the last words; past the last word, hold only what reading needs (0.3 s per new word + 1 s) | [X] on [P] [V] | [BBC](https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/), [PandaStudio](https://www.writepanda.ai/blog/retention-editing-7-laws) |
| Material after the final payoff | None, except the spoken CTA, a comedy button or tag, or a memory-listed sign-off (≤ ~1.5 s) | [I] [X] | `backend/eval/pro_cut_reference.py` |
| Spoken CTA words retained | 100%, unless the creator overrides in the brief. The pro kept "Rate that version 7 out of 10. Follow for the next collision test." whole | [I] n=1 | `backend/eval/pro_cut_reference.py` |
| Asks per ending | One; two only when linked ("follow for part two") | [X] | — |
| Loop candidate length | Up to ~35 s; beyond that a rewatch costs too much | [X] | research_style_trends.md |
| Loop seam (tail + head silence) | 100–250 ms when the last line runs grammatically into the first. When it starts a new sentence, use this speaker's kept boundary pause (energy table in `cutting-and-pacing.md`: ~200–300 ms high energy, ~250–450 ms conversational). The median silent pause in read English is 493 ms | [X] on [L] | [Campione & Véronis](https://www.isca-archive.org/speechprosody_2002/campione02_speechprosody.pdf) |
| Loop seam picture | Match (face size within ~5%, eye line within ~2% of frame height), or change clearly, ≥1.25×, since a pose jump rides across the seam. Under ~1.1× reads as an accident; 1.15–1.25× as a lean-in that will not hide the jump. "15–20%" is lore | [X] from [P] [V] | `framing-and-zooms.md` |
| Loop seam audio | Room tone matched from nearby in the same take; level or noise jump under ~4–5 dB; no audible fade, only 5–10 ms de-click ramps. Audio and video streams end on the same frame, or the join stutters | [P] [X] | [Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise), [Decca guide via ebrary](https://ebrary.net/300232/education/crossfades) |
| Music at the end | Final button or downbeat on the last word; tail ≤0.5 s. No button on a designed loop | [I] [P] | `music.md`, [PremiumBeat](https://www.premiumbeat.com/blog/timing-music-for-video-editing/) |
| Completion as a ranking input | TikTok: finishing a video is a strong indicator of interest and gets "greater weight" (2020). Instagram: whether you will "watch a reel all the way through" is among its most important predictions (2023) | [A] | [TikTok](https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you), [Instagram](https://about.instagram.com/blog/announcements/instagram-ranking-explained) |
| Replays | Shorts count a view on every start or replay (since 31 Mar 2025); all YouTube formats count from playback start (since 24 Aug 2026). Engaged views exclude loops; YPP earnings use them, eligibility uses "qualified" views. `audienceWatchRatio` can exceed 1 on rewatched parts; Instagram's total view time includes replays. No platform publishes a ranking weight for replays | [A] | [PPC Land](https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/), [YouTube](https://support.google.com/youtube/answer/12220281), [Google](https://developers.google.com/youtube/analytics/metrics), [Meta](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights) |
| TikTok Creator Rewards | Videos longer than 1 min; a qualified view is ≥5 s and counts once per account | [A] | [TikTok terms](https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en) |
| Completion by length | 62% at 21–34 s vs 48% over 60 s (~500 videos, method not shown). Short videos complete more by definition: no licence to cut substance | [V] | [OpusClip](https://www.opus.pro/blog/tiktok-length-format-retention-data) |
| Questions and comments | Posing a question raised comment counts across 355 brand Facebook posts (correlational) | [L] | [de Vries et al. 2012](https://doi.org/10.1016/j.intmar.2012.01.003) |
| Written CTA in ads | "Strong written CTAs": +205% purchase intent in creator ads. "Written" means the ad unit's CTA copy above the button, not text burned into the video. Platform-commissioned (TikTok Marketing Science 2022, run by Lumen). Ads only | [A] | [CreatorIQ × TikTok](https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf) |
| Engagement bait | Facebook demotes posts that request votes, shares, comments, tags or reactions "for purposes other than a specific call to action" (since 2017), including bait in a video's *audio* (2019). Requests for help or advice are exempt. TikTok keeps "tricking others into increasing engagement" ("like-for-like", false incentives for following) off the For You feed | [A] | [Meta 2017](https://about.fb.com/news/2017/12/news-feed-fyi-fighting-engagement-bait-on-facebook/), [Meta guidelines](https://transparency.meta.com/features/approach-to-ranking/content-distribution-guidelines/engagement-bait/), [TikTok](https://www.tiktok.com/community-guidelines/en/integrity-authenticity) |

## How to decide

1. **Pin the ending material in the brief.** Record the payoff range, the protected CTA range, any comedy button, and any memory-listed sign-off. Then tag every sentence after the last payoff word:
   - *PROOF/APPLY* adds meaning. Keep it.
   - *CTA* and *BUTTON*: keep.
   - *RESTATE*: usually cut. If the restatement is the stronger, cleaner line, end on it and cut the weaker one, provided nothing the viewer needs is lost; otherwise note it for the creator.
   - *SIGN-OFF* ("so yeah", "that's it", "okay bye"): cut.
   - *DEAD AIR / REACH*: cut.
   - *Several deliveries of the last line or CTA* (they are the most retaken lines): choose the one that lands, with a falling, finished pitch, eyes on the lens and no trailing "…or whatever". Prior: the last complete take; delivery decides (`cutting-and-pacing.md`).
2. **Choose the ending type.** Default: a hard stop on the payoff, plus the CTA if one was spoken. Alternatives: comedy button, held visual, designed loop, part-two tease. Write the choice and a one-line reason into the plan.
3. **Set the out-point.**
   - Take the last word's end from forced alignment, not model timestamps, and keep its release (fricative tail, plosive burst, nasal decay). An ASR round-trip over the last second must read the word intact.
   - Add a 0.15–0.5 s tail. The last frame has the mouth closed and eyes on the lens, before the head drops or the hand reaches. A warm nod can take the tail to 0.5 s; past that it reads as winding down.
4. **Clean the restart seam, always.**
   - The tail plus frame 0's lead-in (0.1–0.5 s, per `story-and-hook.md`) should sum to a gap this speaker would plausibly leave between sentences.
   - Keep the same framing at both ends, or change it clearly.
   - Keep room tone continuous, clear the last caption page at the last frame, and never fade.
5. **Decide on a loop.** Build one only if every answer is yes:
   - (a) The video runs about 35 s or less.
   - (b) The creator's last words run into the first kept line as grammar ("…just remember:" into the opening claim) or as thought (the ending returns to the opening image or question).
   - (c) No spoken CTA follows the lead-in, or the CTA itself leads in.
   - (d) The first line still works cold for someone who has not seen the end.

   If so, cut everything after the lead-in word ID, set the seam to the loop prior, and match framing across it. Use no music, or a bed cut to loop at a bar line with no button. Render it twice back to back and listen across the join. If any test fails, end on the payoff.
6. **Handle the spoken CTA.**
   - Keep every word; tighten only dead air inside it (`cutting-and-pacing.md`).
   - Keep the face on screen: no full-screen insert over the ask (`broll.md`).
   - If the ask names something to read (a handle, a code), echo it as text while spoken, inside the safe band, without extending the runtime.
   - An early ask ("follow before you scroll") is protected too but costs the opening: keep it and offer a hook alternate without it.
   - A platform-wrong CTA ("subscribe" on TikTok) gets the creator's recorded alternate if one exists (`platforms.md`); otherwise flag it, never cut it silently.
7. **Comment prompts.**
   - A spoken prompt stays.
   - Add a text prompt only when the brief asks: one specific question the video equips viewers to answer, over the last spoken beat.
   - Never add "comment YES", "tag someone who" or "share with three friends": the engagement-bait patterns platforms demote.
   - Spoken bait stays (protected) but is flagged once: Facebook demotes bait in audio, and "follow for part two" with no part two resembles TikTok's FYF-ineligible "false incentives for following" [A]. Offer a cut for approval.
   - An ask the creator wants but never said goes in the handoff notes as post-caption or pinned-comment copy, not into the video.
8. **Finish the tail.** Follow `music.md`: land the button on the last word so its ring-out (≤0.5 s) ends by the out-point, not chopped mid-decay. No SFX "ding" under an ask. An optional comedy button SFX goes after the punchline, never under it. "No music" and "no SFX" are complete answers here.
9. **Critique.** Watch the last 3 s and first 2 s as one clip, twice, sound on and muted. List the ending pass's ops; any beyond the out-point and sign-off cuts must justify itself or be reverted.

## When to break it

- **Visual payoffs** (a tutorial result, a before/after, a plated dish): let the result fill the last 1.5–2 s, entering under the final words; hold past the last word only as long as reading it takes.
- **Emotional endings.** When the face is still working after the last word (a breath, wet eyes), hold 0.8–1.5 s; at 0.3 s it feels like a hang-up.
- **Signature sign-offs.** A catchphrase listed in creator memory, ≤ ~1.5 s, is identity, not dead air. Keep it.
- **Comedy.** End on the button; a reaction laugh can be the button. A CTA after the laugh stays (protected); note that it steps on the joke and offer a no-CTA alternate.
- **Podcast clips and trailers.** A cliffhanger pointing to the full episode is legitimate (DOAC trailers end on one) [P], provided the episode exists and the clip has paid off its own hook.
- **Series.** "Follow for part two" is fine when part two exists or is committed and part one still delivers; people do tend to resume what was interrupted [L].
- **Sales and UGC ads.** The CTA is the job: "Close With a Clear Call to Action" (CreatorIQ × TikTok) and "a strong CTA" (TikTok creative guidance) [A]. The +205% measured the ad unit's CTA copy, set outside the video, so burned-in CTA text is optional: only while the ask is spoken, clear of the ad UI.
- **Length pressure.** A Creator Rewards creator who needs 60 s gets real substance restored (`platforms.md`), never a padded ending.
- **Explicit end-card requests.** Honor them: ≤ ~1.5 s, overlapping the last line where possible; state the cost once.

## Worked example

A tech explainer at conversational energy, in one take. Frame 0 opens on the first line: "Your phone isn't dying because of your apps." After the story cut, the tail reads:

> w71–w84 (31.2–35.0 s) "It's background app refresh. Turn it off for everything except maps and messages." · gap 0.55 · w85–w93 (35.6–37.9 s) "My phone went from dead at three to forty percent at bedtime." · gap 0.42 · w94–w104 (38.3–41.1 s) "Try it for a week and comment your battery at five. I read all of them." · small nod, gap 0.90 · w105–w108 "So yeah, that's it." · gap 0.70 · w109–w110 "Okay, bye!" · 3.8 s of reaching for the phone.

| Where | Decision | Why |
|---|---|---|
| w71–w84 | Keep. Payoff | The answer the hook promised |
| w85–w93 | Keep | It comes after the reveal but adds proof. The meaning has not stopped yet |
| w94–w104 | Keep all, including "I read all of them." | One specific, answerable ask tied to the content; the promise is the creator's voice |
| w105–w110 and the reach | Cut | Sign-offs that signal the end and add nothing; 6.3 s of the old tail |
| Out-point | 41.45 s, 0.35 s after "them" ends | Keeps the nasal decay and the nod peak (41.4 s), before the head drops (41.6 s) |
| Loop? | No | "…I read all of them" does not run into "Your phone isn't dying…". A loop would mean cutting the CTA |
| Restart seam | 0.35 s tail + 0.2 s lead-in = 0.55 s, his own gap after the payoff. Both ends at 1.0× framing; room tone matched | The replay starts like a new sentence, with no jump in picture or noise |
| Text and music | No "comment below" sticker; the last caption page holds to the end; the bed's button lands on "them" | Spoken ask plus captions already reach muted viewers |

**A loop case.** A 19 s hot take at high energy opens: "Nobody who's actually rich talks about passive income." It ends: "…so next time a guy in a rented Lambo says 'passive income', just remember:" (w41–w49), followed by "yeah" and a laugh.

- All four loop tests pass. Cut "yeah" and the laugh: it reacts to no punchline and would break the join.
- Tail 0.12 s plus lead-in 0.10 s gives a 0.22 s seam, close to the creator's own gap before a continuation.
- The fine cut had punched the last segment to 1.2×; frame 0 is 1.0×. At the join 1.2× is a lean-in: too small to read as a new shot, big enough to show the pose jump. Remove the punch so both ends sit at 1.0×; this loop's job is to be invisible. (A ≥1.3× punch earned elsewhere could stay as a clear cut.)
- No music, SFX or text prompt. Played twice, it sounds like one rant, and the first line still works cold. Two ops in total: one cut, one punch removed.

## Anti-patterns

- Deleting the creator's CTA as a "sign-off".
- Inventing an ask: "Follow for more" text, subscribe animations, or a generated voice line.
- End cards, logo stings, "thanks for watching" slates, fades to black.
- Leaving 1–4 s of "okay bye", looking away or reaching for the phone; clipping the last consonant.
- Fake loops (a last sentence chopped mid-word, a payoff cut so it "loops" into the hook) and cliffhangers for a nonexistent part two.
- Stacked asks: follow, like, comment, share, link.
- Bait prompts: "comment YES", "tag someone who…".
- A music tail running past the last word, a ding under the CTA, or CTA text under the platform UI or after the voice has finished.
- "Finishing strong" by decoration: a last-second punch-in, whoosh, flash or emoji on a line that already lands.

## Critic questions

1. Does the video end within about 0.5 s of the last spoken word, or after a held visual that shows something new?
2. Is the final word complete, with no clipped consonant, click or cut breath?
3. Is every CTA the creator spoke present and complete (unless the brief records an override)?
4. After the final payoff, is there anything besides the spoken CTA, a comedy button or a memory-listed sign-off?
5. Is there an end card, logo sting, "follow for more" graphic or fade to black the creator did not ask for?
6. Does the video deliver the payoff its hook promised before it ends?
7. Is there more than one ask in the ending, where the asks are not linked?
8. Is any added comment prompt a specific question about the content, rather than "comment YES" or "tag a friend"?
9. Played twice back to back, is the last-frame-to-frame-0 cut free of a jump in framing, noise or music level?
10. If built as a loop, does the join sound like continuous speech, and does the first line still work cold?
11. Does any music resolve on the last word, never running past it or fading under it?
12. Is any CTA text inside the safe band and on screen only while spoken?
13. Did the ending pass add anything (text, SFX, a music hit, a punch, loop surgery) the video is just as good without?

## Sources

- PandaStudio, retention editing laws (vendor, n=13): https://www.writepanda.ai/blog/retention-editing-7-laws
- OpusClip, TikTok length and retention data (vendor): https://www.opus.pro/blog/tiktok-length-format-retention-data
- TikTok, How TikTok recommends videos (2020): https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you
- Instagram, Ranking explained (2023): https://about.instagram.com/blog/announcements/instagram-ranking-explained
- PPC Land, Shorts view counting from 31 Mar 2025: https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/
- YouTube Help, view counting, engaged views and YPP: https://support.google.com/youtube/answer/12220281
- YouTube Analytics API metrics: https://developers.google.com/youtube/analytics/metrics
- Meta, Instagram media insights: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
- TikTok, Creator Rewards terms: https://www.tiktok.com/legal/page/global/tiktok-creator-rewards-program-eea/en
- TikTok, Creative best practices (ads): https://ads.tiktok.com/help/article/creative-best-practices
- CreatorIQ × TikTok, 5 Keys to Successful TikTok Creator Ads: https://s3.amazonaws.com/media.mediapost.com/uploads/5_Keys_to_Successful_TikTok_Creator_Ads.pdf
- Meta, Fighting engagement bait (2017; 2019 audio update): https://about.fb.com/news/2017/12/news-feed-fyi-fighting-engagement-bait-on-facebook/
- Meta Transparency Center, Engagement bait guideline: https://transparency.meta.com/features/approach-to-ranking/content-distribution-guidelines/engagement-bait/
- TikTok Community Guidelines, fake engagement: https://www.tiktok.com/community-guidelines/en/integrity-authenticity
- Baumgartner, Sujan & Padgett 1997, affective reactions to ads: https://doi.org/10.1177/002224379703400203
- Fredrickson & Kahneman 1993, duration neglect: https://doi.org/10.1037/0022-3514.65.1.45
- Loewenstein 1994, curiosity: https://doi.org/10.1037/0033-2909.116.1.75
- Ghibellini & Meier 2025, Zeigarnik and Ovsiankina meta-analysis: https://doi.org/10.1057/s41599-025-05000-w
- de Vries, Gensler & Leeflang 2012, brand post popularity: https://doi.org/10.1016/j.intmar.2012.01.003
- Friestad & Wright 1994, persuasion knowledge model: https://doi.org/10.1086/209380
- Campione & Véronis 2002, pause durations: https://www.isca-archive.org/speechprosody_2002/campione02_speechprosody.pdf
- Idyll Sounds, room tone: https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise
- Decca editing guide via ebrary, level jumps: https://ebrary.net/300232/education/crossfades
- PremiumBeat, timing music: https://www.premiumbeat.com/blog/timing-music-for-video-editing/
- BBC subtitle guidelines: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/
- Yunicorn pro-cut calibration: `backend/eval/pro_cut_reference.py`
