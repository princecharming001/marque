# The new Yunicorn editor, in plain words

## What it is

Today's editor follows a fixed recipe. Every video gets roughly the same treatment.

The new editor, called Studio, works like a small team of expert human editors. It studies the video, decides what story to tell, makes a cut, watches its own work, and keeps improving it until it can't do better.

- **Claude** (Anthropic's AI) is the lead editor.
- **Other AI models** help it watch the video and judge the result.
- **Proven, ordinary software** does the precise work: timing every word, finding the quietest point between words, cleaning the voice, and making the final file.

It is built for one thing only: the best possible final video. Cost and speed don't count.

## How it edits a video

```
  1. FILM         The creator films one or more takes
       |
       v
  2. TAKE NOTES   Every word, "um", pause, breath and blink is
       |          written down with its exact timing
       v
  3. PLAN         The lead editor writes a short brief: who it's
       |          for, the hook, the story, the length, and the
       |          lines that must stay (the payoff, the ask)
       v
  4. STORY CUT    Best take of each line; false starts and dead
       |          air removed. It must work as audio alone.
       v
  5. POLISH       Zooms -> b-roll -> captions -> music and sound
       |          -> colour (the order a professional studio uses)
       v
  6. WATCH + FIX  Make the video, watch it, judge it, keep only
       |          real improvements
       v
  7. DELIVER      Files for TikTok, Reels and Shorts, a no-music
                  copy, 3 alternative openings, a cover image
```
*The seven steps from a raw take to finished clips.*

Three details matter a lot:

- **Notes first.** The AI edits from exact, measured notes. It never guesses times itself, because AI models are bad at pinpointing moments in a video.
- **Story before polish.** If the cut doesn't work as audio alone, like a radio clip, no zoom or music will save it.
- **It can ask for a retake.** If a key line is missing or garbled, it asks the creator to re-record just that line, as a real editor would.

## The editing team

```
            +------------------------------+
            |     LEAD EDITOR (Claude)     |
            |  the only one allowed to     |---- edits ---> THE EDIT
            |  change the video            |                   |
            +------------------------------+                   | render
               ^                     ^                         v
               | advice              | notes               THE VIDEO
               |                     |                         |
  +----------------------+   +----------------------------+    |
  | SPECIALISTS          |   | CRITICS (other AI firms)   |    |
  | b-roll, captions,    |   | Gemini watches + listens;  |<---+
  | music & sound, colour|   | a 2nd model checks stills  | watch
  +----------------------+   +----------------------------+
```
*One editor holds the pen; specialists advise and outside critics review.*

- **Only one editor changes the video.** When several AIs edit the same thing, they undo each other. One writer keeps the edit coherent.
- **Critics come from other AI companies.** An AI tends to like its own work. So Gemini (Google) watches and listens to the result, and a second model checks still frames. In testing, people have the final say.

## The watch-and-fix loop

```
   +----------------------------------+
   | CHAMPION = best version so far   |<---------------+
   +----------------------------------+                |
                  |                                    |
                  v                                    |
   Make the real, phone-ready video                    |
                  |                                    |
                  v                                    |
   Critics answer precise questions:                   |
   "Is the jump after 'budget' visible?"               |
                  |                                    |
                  v                                    |
   The lead editor makes a new version                 |
                  |                                    |
                  v                                    |
   Head-to-head: new vs champion.        new wins and  |
   Two judges, shown in both orders. --- breaks -------+
                  |                      nothing
                  | no win twice in a row
                  v
          SHIP THE CHAMPION
```
*A change is kept only if it clearly beats the best version so far.*

The best version so far always ships, so a bad idea never reaches a creator. We stop when the edit stops improving, not after a fixed number of tries. At the end, the critics watch the whole video once and ask one question: does anything distract from the speaker?

## Why the edits will be better than today

- **Restraint.** On a test video, a professional editor made 1 cut where today's engine made 19. The new editor learns that "no zoom", "no b-roll" and "no music" are often right.
- **Research, not a recipe.** It follows editing guidelines written by people. Each says how strong its evidence is and when to break it.
- **Clean cuts.** Cuts land where a thought ends, at the quietest point between words, and are checked for clicks and clipped words.
- **Voice first.** Poor audio makes a speaker seem less credible. We clean the voice only when measurements say so, and only if it still sounds like the creator with every word clear.
- **Better picture.** iPhone HDR is converted once, carefully. Zooms come from 4K. Colour is matched across takes.
- **B-roll earns its place.** Each clip needs a job, must show the right thing, and is checked playing inside the edit. If nothing fits, we stay on the creator's face.
- **Readable captions.** One short line below the chin, moved only to avoid the face or the app's buttons.
- **Safe music.** Licensed tracks platforms recognise, so creators avoid copyright claims.
- **It learns each creator**: their pace, catchphrases and the flubs they like. Creators can see and edit what it learned.
- **A better app editor.** Tap words in a transcript to cut, or tell the chat what to change. Chat changes get the same full review.

## Bring your own AI key

```
   CREATOR'S KEY (optional)          YUNICORN'S KEYS (always)
   Anthropic, OpenAI, Google         +--------------------------+
   or OpenRouter-style               | Listening (speech to     |
            |                        |   text, timing)          |
            v                        | Critics that judge       |
   +--------------------+            | Final quality gate       |
   | LEAD EDITOR runs   |  the edit  +--------------------------+
   | on their own model |----------> must pass our critics
   +--------------------+            before it ships
```
*Their key runs the editing decisions; our tools still listen, judge and guard quality.*

- By default, everything runs on our keys.
- A creator can paste an Anthropic, OpenAI, Google or OpenRouter-style key. Their model then makes the editing decisions.
- Listening and judging stay on our keys, so a weaker model can't slip a worse video through. With a Google key, a creator can also choose to run the watching on their account.
- Models we've tested run as standard. Others carry an "experimental" label and still face our critics.
- If their model can't reach our bar, we offer to redo the edit on our key. We never switch billing silently.
- Google keys must come from a paid account, because free Google keys let Google train on the data.
- The key lives in the iPhone's secure keychain and is sent only when an edit runs. Our servers hold it in memory for that job and never log it.
- The consent screen names every AI company that will see the video.
- We add proper user logins to our servers first. No keys are accepted before that.

## Keeping the old editor as a backup

```
                      new video
                          |
                  +---------------+
                  | ENGINE SWITCH |   one server setting
                  +---------------+
                    /           \
             OLD   /             \   NEW
                  v               v
   +--------------------+   +---------------------+
   | Today's engine     |   | Studio              |
   | frozen, untouched  |   | separate servers    |
   | + today's editor   |   | + the new editor    |
   +--------------------+   +---------------------+
```
*Flip one switch and every new video goes back to today's editor.*

- We freeze an exact copy of today's engine: code, server image, render setup and current app build.
- Studio runs on separate servers with separate storage, so building it can't break the old engine.
- The old in-app editor stays in every future app version. Older app versions always get the old engine.
- If a Studio edit fails, the old engine redoes it automatically, and we tell the creator.
- We rehearse flipping the switch before launch and in every phase.

## How we'll know it's better

- **People decide, not AI.** Raters and creators watch two versions of a video on a phone, without knowing which engine made which, and pick the better one. About 200 comparisons show whether one wins 60 to 40.
- **A pro benchmark.** We collect 200 real creator videos and pay 2–3 professional editors to cut 40 of them.
- **Real ears.** A listening panel checks sound on phone speakers and earbuds, because no AI reliably hears hiss or harsh "s" sounds.
- **Automatic checks on every video**: no clicks, no cut-off words, captions off the face, correct loudness, sound in sync.
- **After launch**: how often creators accept the edit untouched, how much they change, and, with permission, how viewers watch compared with each creator's usual video.

Targets: win 60% of blind comparisons against today's engine before adding polish, and 65% after. The goal is to match professional editors.

## Rollout timeline

```
  PHASE 0  Safety net + test set    Done when flipping the switch
     |     (freeze old engine,      sends every new video to the
     |     200 real test videos)    old engine, end to end
     v
  PHASE 1  Listening + sound        Done when sound and sync are
     |     (notes, voice, HDR)      pro-grade and listeners prefer
     |                              the cleaned voice
     v
  PHASE 2  Story cuts + the         Done when it wins 60% of blind
     |     watch-and-fix loop       tests against today's engine
     v
  PHASE 3  Polish: b-roll, music,   Done when it wins 65%, and we
     |     colour, zooms            track how close it gets to pros
     v
  PHASE 4  New app editor, chat,    Done when the app preview
     |     bring-your-own key       matches the final video and
     |                              outside models pass our tests
     v
  PHASE 5  Gradual rollout: quiet   Done when real results match
           test, invited creators,  or beat today's, with fewer
           then everyone            than 2% of edits falling back
```
*Each phase starts only when the one before has passed its test.*

## What we won't build

Speech-to-text, video rendering, music libraries and stock-footage search already exist and work well, so we use them. We build only what makes our editor ours: the notes, the shared edit plan, the watch-and-fix loop, the editing guidelines and the new app editor.
