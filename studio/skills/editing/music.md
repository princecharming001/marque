# Music: whether, what, how loud, and where it lands

Load this file at the brief, to decide whether music has a job and which destinations can legally carry it, and again at the sound stage of finishing, after picture lock, b-roll and captions. Reload it whenever a chat edit moves a cut: the ending backtime and hit points hang off word IDs. Sound effects are in `sfx.md`; the voice chain and loudness targets are in `voice-and-loudness.md`. "No music" is always a valid answer, and for many talking heads it is the right one.

## Principles

1. **Music needs a job you can name in one line.** Possible jobs:
   - carry momentum through a list or a run of jump cuts;
   - add an emotional colour the words lack;
   - mark structure (the turn, the reveal, the end);
   - fill stretches without speech;
   - carry a creator's recurring sonic identity.

   *Why:* background music slightly hurts reading and memory while helping emotional response [L], and irrelevant music lowers learning [L]. Without a job, you pay that cost for nothing.
2. **The voice is the anchor, and music is set relative to it.** *Why:* listening effort rises as the background gets louder, even while every word stays intelligible [L]. Degraded speech lowers ratings of both the content and the speaker [L].
3. **Under speech, use sparse, instrumental, unfamiliar music.** *Why:* sung lyrics lowered spoken-word recognition even with speech 15 dB above the music; busier arrangements hurt only at 0–5 dB [L]. Lyrics also hurt memory (d ≈ −0.3) and reading (d ≈ −0.2), while lo-fi instrumental did not credibly differ from silence [L]. Familiar songs hurt sentence recognition more [L; at −5 dB SNR, and an EEG study disagrees, so direction only].
4. **Match the speaker's measured energy; don't add energy they lack.** *Why:* tempo moves arousal and mode moves mood [L], and music biases how viewers read a person's motives, mood-congruently [L]. A hype track under a flat delivery exposes the gap rather than hiding it.
5. **Cut the speech first, then fit the music.** Don't move a speech cut, shorten a pause or change speed to hit a beat. *Why:* Murch weights a cut's emotion at 51%, story at 23% and rhythm at only 10% [P]. The track can be slipped, re-versioned or regenerated; the performance cannot.
6. **Shape the music to the story.** Hold back through the setup, lift on the payoff, and end on the last word with a real musical ending. *Why:* a cadence tells the viewer the thought is complete. A fade over the last line, or a chop mid-bar, reads as unfinished [P].
7. **Sync to the beat only where it costs nothing.** *Why:* cuts aligned to beats raised perceptual pleasure even when viewers could not detect the alignment [L]. It never outweighs the speech timing in principle 5.
8. **Use fewer layers and fewer hits.** *Why:* audiences track about 2.5 simultaneous sounds of one kind, and at most about five in total [P]. Hitting every keyword is Mickey-Mousing, which fell out of favour in serious film scoring through overuse [P].
9. **Bake in only cleared music, and always ship a no-music master.** *Why:* platform music licences cover songs added inside the platform's own tools, not third-party renders [A]. Creators also need a clean version they can put native or trending sounds on.
10. **Judge the mix on a phone, the way a non-editor hears it.** *Why:* non-experts wanted music 4 LU quieter than experts did [L]. Phone speakers also drop the bass that keeps a quiet bed audible.

## Defaults and ranges

These are starting priors. Give a one-line reason whenever you leave a range. Compare speech and music using BS.1770 short-term loudness (a 3 s window; momentary is 0.4 s, per [EBU Tech 3341](https://tech.ebu.ch/docs/tech/tech3341.pdf)) over speech runs, not with peak meters.

| Parameter | Prior | Tier | Source |
|---|---|---|---|
| Music level during speech | start 16–20 LU below speech; working range 12–25 LU | [V] + [I]; vendors disagree (Zella: 18–25 dB under; Pixflow's meter targets imply ~6–15) | [Zella](https://zellahq.com/blog/music-ducking-explained/), [Pixflow](https://pixflow.net/blog/audio-mixing-premiere-pro/), research_audio_music_sfx.md |
| Separation floor and bias | at least 10 LU for commentary over music (15 over ambience); non-experts wanted 4 LU *more* separation than experts, so err quieter | [L] (TV, n=22) | [Torcoli et al. 2019](https://doi.org/10.17743/jaes.2019.0052) |
| Listener spread | individual preferences span an interquartile range of ~5.7 LU; no single level suits everyone | [L] (n=20) | [Resti et al. 2023](https://arxiv.org/abs/2305.19100) |
| Final master (validator) | −14 LUFS integrated (a platform-matching choice; TD1008 itself suggests −18 for speech streams); true peak ≤ −1 dBTP after encoding | [I] + [P] (true peak) | [AES TD1008](https://aes2.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf), [Production Advice](https://productionadvice.co.uk/stats-for-nerds/) |
| Duck attack | ramp down over 50–300 ms, starting 100–250 ms *before* the first word (look-ahead from word times) | [V] (attack under ~300 ms) + [X] | [Zella](https://zellahq.com/blog/music-ducking-explained/) |
| Duck hold and release | hold through gaps under ~1 s; release over 0.5–1.0 s | [P] (never pop up in breaths) + [X] | [Sound On Sound](https://www.soundonsound.com/techniques/live-side-chain-compression) |
| Swell | +4–8 dB, only in gaps of 1.2 s or more, under voiceless b-roll, and in the outro | [X] | research_audio_music_sfx.md |
| Spectral carve | ~2–4 dB dynamic dip at 1–4 kHz on the music, only while speech plays | [V] (band) + [X] (depth) | [Pixflow](https://pixflow.net/blog/audio-mixing-premiere-pro/) |
| Hit-point tolerance | within ±1 frame of the stressed-syllable onset; a hit on a visual event may run up to ~45 ms early | [X]: listeners need ~20 ms between two sounds to tell which came first [L]; AV offsets are detected at +45/−125 ms [L] | [Hirsh 1959](https://doi.org/10.1121/1.1907782), [ITU-R BT.1359](https://www.itu.int/rec/R-REC-BT.1359/en) |
| Hits, stingers, dropouts | 1–3 in total per short; stinger 0.5–2 s; dropout 0.3–1.0 s | [X] | — |
| Ending | final downbeat or button on the last word; tail ≤0.5 s; video ends within ~0.5 s of the last word | [I] + [P] | design_doctrine.md §16, [PremiumBeat](https://www.premiumbeat.com/blog/timing-music-for-video-editing/) |
| Tempo priors | calm or sincere: 60–90 BPM, or no music; conversational: 85–105; high-energy list or sales: 100–125 (half-time feel is fine) | [X]; tempo drives arousal [L] | [Husain et al. 2002](https://online.ucpress.edu/mp/article-abstract/20/2/151/62120/) |
| Style defaults | educational: none or very low; storytime: none or ambient; hot take and podcast: usually none; listicle and tutorial: light bed; sales/UGC: bed; founder: subtle | [I] | design_doctrine.md §17 |
| YouTube Shorts | most songs usable for up to 90 s via YouTube's tools (some 30–60 s); since 24 Sep 2026, claimed 1–3 min Shorts stay up but monetise only once the claim is resolved | [A] | [YouTube Help](https://support.google.com/youtube/answer/15424877) |

**Validators already enforce** loudness and true peak, no digital silence, a licence check on every asset, and time coordinates taken from aligned words and measured beats, never from a model's guess. Spend your attention on taste.

## How to decide

1. **Read the brief.** Note:
   - the style profile;
   - measured energy (arousal, words per minute, pitch variance);
   - creator memory (some creators never want music);
   - each destination's safelist status.
2. **Name the job or choose none.** Does the locked cut pass the radio test? What would a bed add? Lean to none for grief, health scares, apologies, news, dense teaching, speakers above ~185 wpm [X], and deadpan comedy, unless creator memory or a clear job says otherwise. If music is already audible in the recording, separate it first or choose none, since two tracks in different keys and tempos clash. The no-music master gets rendered anyway, so "none" costs nothing.
3. **Build the music map from the locked timeline.** List:
   - speech runs (first and last word IDs);
   - gaps of 1.2 s or more;
   - inserts with and without voice;
   - hard seams;
   - anchor words: hook end, turn, payoff (its stressed syllable, from phone alignment), CTA, and last word.
4. **Choose a source for each destination.**
   - Safelisted destination: catalogue music (Epidemic, `vocalType=NONE`).
   - Not safelisted, or status unknown: a generated bed (ElevenLabs Music or Lyria).
   - Neither is cleared: ship the no-music master only.
5. **Select three candidates** that differ in mood or instrumentation. Describe the track in plain words taken from the content, e.g. "warm minimal lo-fi, soft drums, sparse plucks, no lead melody". Reject any track with:
   - vocals or vocal chops under speech;
   - a busy lead line at 1–4 kHz;
   - large tempo or style swings;
   - a recognisable sound.

   Prefer tracks that ship with stems.
6. **Fit the structure.**
   - Backtime first, so the final downbeat or button lands on the last word.
   - Then put the lift on the payoff.
   - Choose an entry: a bed from frame one, or a voice-only hook with music entering on the downbeat after the hook's last word.
   - Fit the length with an Epidemic Version (1 s–5 min), bar-line cuts, or generation to exact section lengths (ElevenLabs: 3 s–10 min, up to 30 chunks). Avoid stretching the music by more than a few percent [X].
   - Make your own music edits on a downbeat at a phrase boundary (4 or 8 bars), with a short crossfade, and hide them under a word, a hard cut or the hit where you can. Listen for a skipped or doubled beat [P].
7. **Sync where it is free.** Once the ending and the payoff hit are fixed, use whatever freedom remains (a bar-line edit, another Version, another entry bar) to land downbeats on existing hard seams. Get downbeats from Epidemic's beats endpoint or a tracker such as beat_this. Seams that stay off-beat stay where they are.
8. **Mix.**
   - Set the bed against speech short-term loudness.
   - Draw the duck envelope from word times: pre-duck before speech, hold through short gaps, release only into real gaps.
   - When stems exist, mute the melody stem under speech and keep drums and bass.
   - Apply the 1–4 kHz dip.
   - Swell under voiceless b-roll and the outro.
9. **Place hit points on anchor words.** A hit can be a downbeat, a drum entry, a section change or a stop.
10. **Use a stinger or dropout only if the reveal still needs marking.** Never put one on the same word as an SFX.
11. **Render and judge.**
    - Run the ESTOI and WER gates on the real mix.
    - Play it through a phone-speaker simulation (band-limited, with the bass rolled off).
    - Answer the critic questions.
    - Watch it against the no-music master. **If the music version does not clearly win, ship none.**
12. **Deliver.**
    - The cleared master.
    - A no-music master (voice plus SFX, same timing, same loudness).
    - A one-line note for any in-app sound.

    For the Instagram Audio API, set `audio_volume` well below `video_volume` and check by ear, because the 0–100 scale has no documented dB mapping [A].

**Licensing**
- **Never bake in a commercial song.** A render needs a sync licence Yunicorn lacks. TikTok bars businesses from its general library [A]; Meta says unauthorised music "may be blocked, muted or removed" [A]; a claimed Short over a minute can't monetise until the claim is resolved [A]. Creators add hit songs in-app, over the no-music master.
- **Catalogue music gets claimed unless the channel is safelisted.** Epidemic tracks are registered in Content ID. The partner Safelisting API clears YouTube, Instagram, TikTok and Facebook channels, and individual videos on YouTube only. It must be enabled in the partnership [V].
- **Generated music.** ElevenLabs Music is cleared for broad commercial use on paid plans; film and TV need Enterprise [V]. Lyria output carries SynthID, and its terms need legal review [V]. Exclude models with non-commercial weights (MusicGen) and services whose terms bar commercial use or gate downloads (Udio, Suno's free tier).
- **YouTube Audio Library** tracks are claim-free, but only on YouTube [A].

**Prompting a generated bed**
- Generate after picture lock, at the exact length plus a 1–2 s tail.
- **Prompt mode:** set `force_instrumental: true`.
- **Composition-plan mode:** `force_instrumental` does not apply here. Leave every chunk's lyric lines empty and put "vocals, vocal chops, choir, humming" in its negative styles [V]. Section durations are enforced strictly only on `music_v1`, so measure the boundaries you get [V].
- **State the style yourself:** genre, mood, instrumentation, BPM, era and key. The model fills anything left open with the most average choice [V].
- **Add the use:** "bed under a spoken voice, sparse midrange, no lead melody". Describe a sound, not an artist [X].
- **Use one plan chunk per section of the music map** (each 3–120 s) [V]. Write the ending explicitly: "ends on a single hit, no fade out".
- **Generate three seeds.** Verify tempo, downbeats, the ending and the absence of voice with the beat tracker and a voice detector, not by trusting the prompt.

## When to break it

- **No speech.** Over a voiceless montage, a silent process sped up 2–3×, or an outro card, music comes forward and cutting on the beat is right [L].
- **Sales and UGC.** A bed is the default: TikTok creator ads with music showed +61% brand recall and +177% purchase intent [A; ads, not organic reach].
- **Comedy.** An ironic cue, such as mock-epic strings or a sitcom sting, can be the button. Place it after the punchline, never under it.
- **Creator signature.** A recurring bed or sting is brand identity, and consistency beats novelty. Check creator memory.
- **Trend formats.** When a trending sound is the concept, deliver the no-music master timed so the creator can line up the sound's drop in the app.
- **Vocals.** Vocals can work where nobody speaks [X]. Across 6,606 Douyin brand videos, vocal background music went with more likes, comments and shares [L, observational; speech not controlled].
- **Loops.** A looping video needs looping music: no button, and the last bar flows into the first.
- **Beds that vanish on phones.** A sparse bed can sit 12–15 LU under speech. The 10 LU floor still holds.

## Worked example

*Fitness creator, 176 wpm, arousal 0.62, fast and smiling. Locked cut: 36.0 s. TikTok and Shorts are safelisted; Reels is not.*

> "I tried every viral morning routine for thirty days. Only one actually mattered." (hook; w11 "mattered" ends at 3.10 s)
> "Cold showers? Made me hate mornings." / "Journaling? Lasted four days." / "Five a.m. alarms? I just got tired earlier." (hard seams at 6.2, 9.9 and 14.8 s)
> "The one that worked…" [kept 0.6 s pause] "…was ten minutes of sunlight before my phone." (payoff; "SUN" in w71 at 27.42 s)
> "That's it. Try it for a week and tell me I'm wrong." (w84 "wrong" ends at 35.62 s)

**Job.** Carry momentum through three hard-cut items, and mark the reveal.

**Selection.** The Epidemic search was "bright minimal lo-fi pop, soft kick and claps, warm bass, sparse plucks, no lead melody, instrumental, ~98 BPM". A (piano lead in the voice band) and B (dense strumming masked consonants) were rejected; C, with stems, was chosen.

**Entry.** The hook is voice-only. Music enters on a downbeat 60 ms after "mattered", so the entry itself announces the list. A bed under the hook would compete with the most important line.

**Level.** Speech sits near −14 LUFS short-term; drums and bass run at −31 LUFS (17 LU under), with the melody stem muted. The 0.3–0.5 s gaps between items are held, not swelled, because a swell there would pump.

**Reveal.** The bed drops out on the last beat before the kept pause, and room tone continues. Drums and bass return on a downbeat within 10 ms of "SUN", joined by the pad stem for the lift. The melody stays muted because she is still talking. The dropout, the lift and the ending button make three marks, which spends the budget, so there is no stinger and no whoosh.

**Ending and sync.** The Version is backtimed so a single-hit button lands on the onset of "wrong", with a 0.4 s ring-out. Only the 9.9 s seam falls within a frame of a downbeat; the 6.2 s and 14.8 s seams stay put. **No speech cut moved.**

**Verify.**
- On the phone simulation, the kick vanished under item 2. The bed was raised 2 dB, to 15 LU under.
- ESTOI held, and the critic still didn't notice the music under speech.
- Against the no-music master, the jury preferred the music version for list momentum.

**Delivery.**
- TikTok and Shorts get the Epidemic master.
- Reels gets an ElevenLabs bed built from two plan chunks, each with empty lyric lines and "vocals" in its negative styles:
  - 24.26 s (3.16 s entry to "SUN"), "sparse, drums and bass only";
  - 8.58 s, "lift with warm pad, no lead melody, ends on a single hit, no fade".
  - The measured boundary landed 20 ms late; the bed was slipped 20 ms earlier.
- All three destinations also get the no-music master.

**Contrast.** Her follow-up, "I had my first panic attack at the gym," gets no music. A bed would have no job her voice isn't already doing.

## Anti-patterns

- Putting music on everything "because shorts have music".
- Setting levels on studio monitors late in a session, when adapted ears let beds creep louder than audiences want.
- Baking in a commercial song, which gets the video muted, claimed or demonetised.
- A fade-out over the last line, a mid-bar chop, or a loop point that skips a beat.
- Trusting a vendor BPM tag without checking for half-time or double-time errors.
- Mickey-Mousing: a hit on every keyword, a stinger per sentence, or a whoosh, riser and sting stacked on one moment.
- Mismatched mood: a trap beat under a vulnerable story, sad piano under a joke, or trailer music under a mundane tip.
- Using music to hide noise (fix it in the voice chain), or to fake energy the speaker doesn't have. A bed may soften a small room-tone step between takes, but that is a bonus, never the job.
- A music-only intro before the first word.
- The same stock bed for every creator.

## Critic questions

1. Is every word intelligible on a phone speaker at normal volume, with the music in?
2. While the person talks, does the music ever pull attention (a melody, sung words or vocal chops, a sudden change)? (Pass = no.)
3. Does the music's mood fit the speaker's tone on every beat, including the sincere lines?
4. Does the energy lift land on the payoff, and not before it?
5. Does the music end with a musical ending at the last word, with no fade over speech and no mid-phrase chop?
6. Is the level free of audible pumping within sentences?
7. Is every music edit (loop, section jump, Version seam) inaudible?
8. Does every speech cut still fall where the thought ends, rather than where a beat falls?
9. Are there three or fewer hits, stingers and dropouts in total?
10. Would the video be worse without the music, judged against the no-music master?
11. Is there a no-music master that matches the main master in timing and loudness?

## Sources

- Torcoli et al. (2019), Preferred levels for background ducking, JAES 67(12): https://doi.org/10.17743/jaes.2019.0052
- Resti et al. (2023), preferred loudness difference; Torcoli et al. (2022), listening effort: https://arxiv.org/abs/2305.19100 ; https://arxiv.org/abs/2207.14240
- Souza & Barbosa (2023), Music with lyrics interferes with cognitive tasks: https://pmc.ncbi.nlm.nih.gov/articles/PMC10162369/
- Brown & Bidelman (2022), familiarity and speech recognition in musical noise: https://pmc.ncbi.nlm.nih.gov/articles/PMC9562996/ (EEG counterpoint: https://pmc.ncbi.nlm.nih.gov/articles/PMC9599198/)
- Scharenborg & Larson (2018), Lyrics and music complexity vs spoken-word recognition: https://www.isca-archive.org/interspeech_2018/scharenborg18_interspeech.pdf
- Kämpfe et al. (2011) meta-analysis; Moreno & Mayer (2000), irrelevant sounds: https://journals.sagepub.com/doi/abs/10.1177/0305735610376261 ; https://psycnet.apa.org/doi/10.1037/0022-0663.92.1.117
- Husain et al. (2002), tempo, mode, arousal and mood: https://online.ucpress.edu/mp/article-abstract/20/2/151/62120/
- Boltz (2001), soundtracks bias interpretation of film: https://doi.org/10.1525/mp.2001.18.4.427
- Hirsh (1959), Auditory perception of temporal order: https://doi.org/10.1121/1.1907782
- Lin, Yeh & Shams (2022), Subliminal cut-beat congruency enhances pleasure: https://pubmed.ncbi.nlm.nih.gov/35398533/
- Newman & Schwarz (2018), Good sound, good research: https://journals.sagepub.com/doi/abs/10.1177/1075547018759345
- Dong et al. (2026), Douyin brand videos, Int J Advertising: https://www.tandfonline.com/doi/full/10.1080/02650487.2026.2670858
- Murch, In the Blink of an Eye (Rule of Six, p. 18) and Dense Clarity, Clear Density: https://sciencepolicy.colorado.edu/students/fysm1000-01/murch_2001_pp5-26.pdf ; https://transom.org/2005/walter-murch/
- Practice: Mickey Mousing; Sound On Sound on ducking; PremiumBeat on timing music edits: https://en.wikipedia.org/wiki/Mickey_Mousing ; https://www.soundonsound.com/techniques/live-side-chain-compression ; https://www.premiumbeat.com/blog/timing-music-for-video-editing/
- Pixflow and Zella (vendor blogs) on levels and ducking: https://pixflow.net/blog/audio-mixing-premiere-pro/ ; https://zellahq.com/blog/music-ducking-explained/
- EBU Tech 3341, AES TD1008 and Production Advice on loudness: https://tech.ebu.ch/docs/tech/tech3341.pdf ; https://aes2.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf ; https://productionadvice.co.uk/stats-for-nerds/
- ITU-R BT.1359, Relative timing of sound and vision: https://www.itu.int/rec/R-REC-BT.1359/en
- TikTok Commercial Music Library; CreatorIQ × TikTok creator-ad report: https://ads.tiktok.com/help/article/commercial-music-library ; https://www.creatoriq.com/press/releases/tiktok-creatoriq-release-special-report-with-data-backed-keys-to-success-for-advertisers?hs_amp=true
- Meta, Music Guidelines and Instagram Audio API: https://www.facebook.com/legal/music_guidelines ; https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/
- YouTube Help, Three-minute Shorts and claims: https://support.google.com/youtube/answer/15424877
- Epidemic Sound, Safelisting, Content ID claims, Versions and beats: https://developers.epidemicsite.com/docs/safelisting/ ; https://www.epidemicsoundhelp.com/hc/en-us/articles/26253712691730-Why-was-a-Content-ID-claim-received-from-Epidemic-Sound ; https://developers.epidemicsite.com/docs/soundtracking-with-llm/ ; https://developers.epidemicsite.com/docs/Endpoints/get-track-beats
- ElevenLabs Music docs and terms: https://elevenlabs.io/docs/api-reference/music/compose ; https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans ; https://elevenlabs.io/docs/overview/capabilities/music/best-practices ; https://elevenlabs.io/eleven-music-api
- Google Lyria (Gemini API): https://ai.google.dev/gemini-api/docs/music-generation
