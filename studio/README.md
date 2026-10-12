# Yunicorn Studio

A standalone editor for talking-head videos. Code perceives, measures and enforces; models judge and decide.

Every edit is a typed **CutDocument**: word-ID based, never model timestamps, and changed only through validated ops.
A compiler turns it into an exact rational-time **Timeline**, and ffmpeg/Remotion render it. Every render must pass the
same ten automatic checks (the invariants) before it ships.

Studio has two tiers:
- **standard** (the default, cents per video): one model call plans the edit and code does the rest.
- **director** (dollars per video): a Director model edits in stages, then critics and a champion loop review the
  rendered video until no revision wins.

The binding module contract is [`ARCHITECTURE.md`](ARCHITECTURE.md). The standard tier's design and interfaces are in
`docs/standard-tier-plan.md` and `docs/standard-tier-interfaces.md`. Editing doctrine lives in `skills/editing/`.
Studio is not wired into the app or the old backend.

## Tiers

| Tier | What happens | Model + speech-to-text cost per video | Who checks the result |
|---|---|---|---|
| **`standard`** (default) | Code reads the take and writes a short digest of it. One planner call (Claude Sonnet 5.5 at effort `low`) makes the story decisions by ID: which sentences to drop and why, which retake to keep, the payoff and CTA, caption accents. Code does everything else (cuts, pauses, captions, voice chain, loudness). Then one render at 1080p30, the ten checks, and code-only repair | **About $0.04**: about $0.03 for the planner and under $0.01 for speech-to-text. About $0.01 with Haiku 5.5 as the planner. All-in with compute, about $0.08-0.15 for a 30-60 s output | Code only. The video ships marked **NOT REVIEWED: standard tier** |
| **`director`** | The Director model edits in stages (brief, story, fine cut, framing, b-roll, captions, sound, colour). The champion loop then renders at full quality, critics review the render, the Director revises, and pairwise judges pick the better version | Measured at list prices: $5.77 (Opus 5.5 Director alone) to $36.20 (Fable 5.1). `--max-usd` caps it | Critics review the final render |

The standard tier's figures are **estimates**. No standard-tier edit has run against a real model yet, because the
credits are exhausted. The 3-video probe in `docs/standard-tier-plan.md` §4 is what will measure them.

"Pro" in the product plan means the director tier, sold as the Director's cut. A cheaper one-round "Pro review" on top
of a standard cut is planned but not built. Today, the nearest thing is upgrading a job with `--tier director --max-usd N`
(see below).

**Choosing the tier and the planner**

| Setting | Meaning |
|---|---|
| `--tier standard\|director` (on `studio edit`) | Overrides the tier for this edit. Otherwise a resumed job keeps the tier it was edited with, and a new job uses `STUDIO_TIER` |
| `STUDIO_TIER` | The default tier for new jobs (default `standard`). A plain `studio edit` can never run up a Director bill by accident |
| `--planner-model M` (on `edit` and `chat`) / `STUDIO_PLANNER_MODEL` | The standard tier's planner model (default `claude-sonnet-5-5`). `claude-haiku-5-5` is the cheaper candidate; the probe decides the default on quality, not price |
| `STUDIO_PLANNER_EFFORT` | The planner's thinking effort (default `low`) |
| `STUDIO_PLANNER_FALLBACK` | Unset (default): when the planner refuses, errors, or gives an unusable answer twice, the edit uses the **pure-code plan** and the report says why. It never switches model silently. Set it to a model name to try that model once more first (same provider and key); a model priced above the planner is refused unless `STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER=1` |
| `STUDIO_REMOTION_CONCURRENCY` | How many browser tabs render the caption/overlay layer at once (default: the CPU count). Lower it on a shared machine |
| `--max-usd USD` / `STUDIO_MAX_MODEL_USD` | Director tier: stop opening review rounds once the job's model spend at list prices reaches this; the current champion ships. Standard tier (`edit` and `chat`): caps one plan's or one chat's model spend (default $0.15, or `STUDIO_MAX_MODEL_USD` when lower): a retry or fallback that could pass it is skipped |

The standard tier refuses a source over 10 minutes (before ingest) and a plan over 90 s (before rendering), pointing
to `--tier director`. `studio edit` and `studio chat` exit with code 4 when nothing was delivered (the render still
fails the invariants after repair).

The planner runs on the house Anthropic key. `job.json` records the tier and the planner's model and effort, so a
resume or a chat keeps them when the environment changes. Flags given on the command line win.

Where each setting is read from:
- `STUDIO_TIER`, `STUDIO_PLANNER_MODEL` and `STUDIO_PLANNER_EFFORT`: the environment or the `STUDIO_ENV_FILE`.
- `STUDIO_PLANNER_FALLBACK`, `STUDIO_REMOTION_CONCURRENCY` and `STUDIO_MAX_MODEL_USD`: the process environment
  only. Export them; a line in the env file is ignored.

**What "NOT REVIEWED: standard tier" means.** No person and no model watched the finished video before it shipped.
Code did check all ten invariants on the encoded file, the same hard gates as the director tier:
- no click or cut-off word at a join;
- picture and sound in sync;
- the payoff and CTA kept;
- captions inside the platform's safe area and off the eyes and mouth;
- loudness at -14 LUFS;
- the delivery format.

What can still ship is a matter of taste: a slow middle, a choppy rhythm, a punch-in that lands late, a weaker retake,
an unneeded opening line such as "Part one." Watch the video before posting, fix it with `studio chat`, or upgrade the
job to the Director.

This label is different from the director tier's **NOT REVIEWED**, which means the critics were unreachable after
retries. In the standard tier, nobody was ever asked to review.

**Upgrading a job to the Director**

```bash
.venv/bin/studio edit <job_dir> --tier director --max-usd 10
```

The job keeps its media and Take Index, so ingest and speech-to-text are not paid twice. The Director's champion loop
then starts from the standard cut: critics review it, the Director revises, and pairwise judges pick the winner. The
report then says reviewed instead of NOT REVIEWED.

The upgraded job keeps the standard tier's 1080p30 media. For a full-resolution Director edit of a 4K source, start a
new job from the video with `--tier director`.

*Status:* this command is being wired with the standard pipeline (phase C) and has not been run with real credits.
Check the result's `tier` and the report's review line after the first real upgrade.

## Layout

```
studio/            python package (import name `studio`)
  config.py        settings, house model ids, key handling, redact()
  timebase.py      µs ints, Fraction seconds, frame/sample grids, fps normalisation
  jobs.py          job directories, JSON helpers, trace, document versions
  media/ perception/ doc/ compile/ audio/ broll/ agent/ qa/  pipeline.py  cli.py
  plan/            the standard tier: digest, decision, planner, prompt, code_plan, repair
tests/             keyless, offline pytest suite (+ `@pytest.mark.real` tests)
scripts/           setup-linux.sh, plan_eval.py (offline planner/code-plan eval against Director edits)
docs/              plan, explainer, research, standard-tier plan and interfaces
models/            local model weights (gitignored)
```

## Setup

```bash
cd studio
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

## Run

Real runs read keys from a dotenv file named by `STUDIO_ENV_FILE` (read into memory, never exported, logged or
written); `STUDIO_REAL=1` enables the real providers. From `studio/`:

```bash
export STUDIO_ENV_FILE=/Users/home/Marque/backend/.env STUDIO_REAL=1
.venv/bin/python -m studio.cli edit clip.mov --platform tiktok --out /Users/home/studio-work/final-clip   # standard tier
.venv/bin/python -m studio.cli edit clip.mov --tier director --max-usd 10 --platform tiktok             # the Director
.venv/bin/python -m studio.cli chat /Users/home/studio-work/final-clip "make the captions a bit bigger"
```

(`.venv/bin/studio …` is the same entry point.)

### `studio edit <video | job_dir>`

Edits one take end to end.

- **Standard tier.** Ingest, capping the working copy at 1080p and 30 fps (a 4K60 upload becomes 1080p30) → Take
  Index → digest → one planner call → the code plan, with the radio test as a gate → one render (x264 `medium`) → the
  ten invariants → code-only repair (at most one re-render) → delivery. If the planner fails, the pure-code plan is
  used. *Being wired (phase C); keyless-tested only.*
- **Director tier.** Ingest at full resolution → Take Index → the Director's staged edit (brief, story + radio test,
  fine cut, then reframe → b-roll/cards → captions → sound → colour, finalize) → the champion loop (full-quality
  render → invariants → critics → Director revision → position-swapped pairwise) → hook-alternate variants → closing
  watch → delivery.

| Flag | Meaning |
|---|---|
| `--platform tiktok\|reels\|shorts` | Destination (default `tiktok`): safe zones, encode limits, the `final_<platform>.mp4` name |
| `--tier standard\|director` | Which tier edits this job (see Tiers) |
| `--planner-model M` | Standard tier: the planner model (default `STUDIO_PLANNER_MODEL`) |
| `--brief TEXT` | Creator intent (goal, CTA, audience, vibe); otherwise inferred from the footage |
| `--style NAME` | Style hint (educational, storytime, comedy, …); otherwise classified from the transcript |
| `--media FILE` | Director tier: creator b-roll the Director may use (repeatable). The standard tier never adds b-roll |
| `--out DIR` | Also copy the deliverables into `DIR` |
| `--rounds N` | Director tier: champion-loop round guard (default 12; the loop normally stops after 2 winless rounds). Only to bound a debugging run |
| `--max-usd USD` | Director tier: stop opening review rounds once the job's model spend at list prices reaches this; the champion ships (env `STUDIO_MAX_MODEL_USD`). Standard tier: the planner call's spend cap (default $0.15) |
| `--asr-provider elevenlabs\|assemblyai` | Transcription provider (default ElevenLabs Scribe) |
| `--director-provider P --director-model M --director-key-env VAR` | Bring your own key for the Director (see BYOK) |
| `--house` | Switch a BYOK job back to the house Director explicitly |
| `--json` (before the command) | Print the result as JSON |

Passing a job directory instead of a video **resumes** a crashed or interrupted edit: finished steps are skipped
(ingest and index by their files, Director stages by `logs/director_state.json`, the loop by
`logs/loop_state.json`). It stays in the job's tier unless `--tier` says otherwise. The command prints:
- the job directory and the tier;
- the finals and the stop reason;
- the estimated cost;
- whether every invariant passed;
- whether the shipped version was reviewed.

`reviewed: False` means it ships **NOT REVIEWED**. In the standard tier this is always the case
(`review_note` "standard tier: code QA only"). In the director tier it means the critics were unreachable after
retries; see `review_note`.

### `studio chat <job_dir> "<instruction>"`

A chat edit runs in the tier the job was edited with. There is no `--tier` on chat; upgrade the job with `studio edit`
first.

**Standard job** (*being wired in phase C; keyless-tested only*). One planner-model call turns the instruction into
ops, given the current cut, the transcript and the caption layout, and the ops are applied, with one retry for
rejected ops. If the cut is unchanged, only the overlay layer and the master are re-rendered; otherwise there is a
full standard render. Then the ten invariants, code repair and delivery. Music, b-roll and paid voice isolation are
not offered in the standard tier, and the reply says what they would cost instead. `--planner-model M` overrides
the job's planner for this edit.

**Director job.** The instruction is applied to the delivered champion, and is the brief for the change (no
pairwise veto). The steps:
- The Director re-enters at the earliest stage the change touches and runs that stage's checks: the radio test
  after a story change, compile/seam checks after a cut change, a caption preview after a caption or framing change.
- The result is rendered at full quality and must pass the ten invariants. The Director gets two attempts to fix a
  failure; otherwise the previous champion stays and the summary says NOT DELIVERED.
- A critic sanity note is recorded, and the new render replaces the deliverables.

`--out DIR` copies them. The job's own Director (house or BYOK) is reused.

### Bring your own key (BYOK)

`--director-provider anthropic|openai|google|openai_compat --director-model M --director-key-env VAR` runs the
Director on the creator's key, read from the environment variable **named** `VAR` (or the `STUDIO_ENV_FILE`
values). Only the provider, model and the variable's *name* are stored in `job.json`; the key never is.
A BYOK Director never falls back to a house model or enables server-side model fallbacks, so billing and the model
never switch silently; a resume or `chat` reuses the stored flags and refuses to run if the key is missing (pass
the flags again, set the variable, or `--house` to switch explicitly). Frames uploaded for the Director (the key's
Files API workspace) are deleted once the edit is delivered. Critics stay house models.

### Outputs: where finals land

Everything lives in the job directory `$STUDIO_WORK_DIR/<job_id>/` (default `/Users/home/studio-work`):

| Path | What |
|---|---|
| `deliver/final_<platform>.mp4` | **The final video** (H.264 High, BT.709, AAC 48 kHz, faststart, no edit list; -14 LUFS / -1 dBTP) |
| `deliver/final_nomusic.mp4` | The same cut without music. Always delivered in the director tier. The standard tier never adds music, so it skips this byte-identical copy |
| `deliver/cover.jpg`, `deliver/captions.srt` | Cover frame (no running captions) and the caption sidecar |
| `deliver/edit_v<n>.json`, `deliver/timeline.json` | The shipped CutDocument and its compiled timeline |
| `deliver/alternates/<label>/final_<platform>.mp4` | A hook-alternate variant the champion beat (or the version a winning variant replaced) |
| `report.md` | Brief, story, captions, audio, QA table, review status, op log, model calls and estimated cost (model and speech-to-text). The review status is reviewed / NOT REVIEWED, same-family only. The director tier adds the critique history. The standard tier adds a Plan section (the planner's decision, or why the pure-code plan was used), the seams table, any repair, and the "NOT REVIEWED: standard tier" line |
| `renders/r<n>/` | Every render (finals, QA, timeline); intermediates are pruned once QA (and, in the standard tier, repair) has run |
| `critique/` | Director tier: critic notes (`<render>/notes.json`, or `unreviewed.json` when a review failed), pairwise verdicts, final watch, caption previews |
| `logs/`, `trace.jsonl` | Director and loop state (resume), the standard tier's `logs/plan.json` (the planner's decision, attempts, token usage), every model call, speech-to-text call and tool call (never a key) |

With `--out DIR`, the deliverables (finals, cover, SRT, document, report and `alternates/`) are hard-linked or
copied into `DIR` too, with a `.studio-job` pointer to the job: `studio chat DIR "…"`, `studio edit DIR` (resume) and
`studio report DIR` work on the delivery folder as on the job directory, and `studio chat DIR` re-delivers into `DIR`
as well as `deliver/`.

### Other commands

```bash
.venv/bin/studio index clip.mov                        # ingest + Take Index only
.venv/bin/studio render <job_dir> [--version N] [--preview]
.venv/bin/studio report <job_dir>                      # rewrite report.md
.venv/bin/studio qa <job_dir> [--render N]             # the ten invariants on a render
```

### How the review loop decides (director tier)

- **Critics** judge the final encode: a naive first-time viewer (the words as heard, no cut markers), a rubric pass
  (critique.md's critic questions, the critic questions of every topic file whose layer is in the render —
  captions-and-text whenever captions are on — and the brief's viewer-level checks), caption geometry measured by
  code (where every page sits against the face after framing, its rendered size, its reach into the platform
  bands) and phone-scale caption sheets under the platform UI mask. A P0/P1 stands only when a metric that measures
  that claim, or another critic, confirms it. A review that fails (network, overload) is retried with backoff; if it
  keeps failing the champion ships marked NOT REVIEWED, never as "nothing to change".
- **Revisions** see the champion render (`view_frames(source='render')`), the critics' sheets, the verdict and the
  rubric items answered "no". Failed invariants name the layer that can fix them (an engine artefact is never
  paid for with the creator's words).
- **Pairwise**: both judges get the same evidence for both versions plus a neutral code diff with side-by-side
  frames; a judge saying "same" in both orders abstains; taste changes need every judge in both orders; a fix of
  confirmed P0/P1 notes wins when nothing regresses and no judge prefers the champion.
- **Variants**: hook alternates the Director records are built from the champion, rendered and judged pairwise;
  the winner ships and the other is delivered under `deliver/alternates/`.
- **Captions**: the Director is shown the placement options on the take with their measured costs (under the chin
  in the strict band or on TikTok's relaxed floor at y 1600, smaller text, a base reframe that lifts the chin,
  above the head) and must look at the rendered pages with `caption_preview` before the captions stage ends.

Status:
- **Director tier.** Every command is wired end to end and has been run on real takes.
- **Standard tier.** Its modules are built and keyless-tested: tiers, digest, planner, code plan, repair, render
  profile, 1080p30 ingest, speech-to-text accounting and the offline eval. The `edit`/`chat` branch is being wired
  (phase C). Nothing in it has been run with real credits; see HANDOFF.md §6c for what the first real run must
  check.

Operational notes: at most 2 edits run machine-wide (`STUDIO_MAX_CONCURRENT_EDITS`), and only one heavy write at a
time (full/preview renders and mezzanine encodes share `STUDIO_MAX_CONCURRENT_RENDERS`, default 1). Disk is budgeted
(`studio.storage`): before the Director starts, the edit's renders must fit (lean intermediates, after reclaiming);
every render needs its *estimated* size plus `STUDIO_MIN_FREE_GB` headroom (default 1 GB), reclaims first (this job's
stale intermediates and overlay cache, then idle jobs' render intermediates and mezzanines — regenerable, rebuilt from
`media/original.*` on demand), then falls back to the lean A-roll intermediate, and only then refuses. Every render's
intermediates are pruned as soon as its QA has run; a delivered job keeps its finals and drops its mezzanine
(`STUDIO_KEEP_MEZZ=1` keeps it). `STUDIO_DIRECTOR_EFFORT` (default `max`: quality is the only goal) sets the
Director's effort.

The standard tier never writes ProRes. Its working copy and its A-roll intermediate are the lean 10-bit 4:2:2
encode, and its master is x264 `medium` CRF 16 instead of `veryslow` CRF 15, with the same colour tags, GOP and
bitrate caps. Its working copy is capped at 1080p (portrait 1080x1920) and 30 fps: a 60 fps source drops every
other frame, so the output is 30 fps. That is what keeps a 4K60 upload cheap. The director tier keeps the full
resolution and the source frame rate.

Cost: `report.md` and the `edit`/`chat` summaries carry the estimated spend at list prices (`studio.agent.cost`, by
role and stage). That covers every model call (the Director, critics, the planner) and every speech-to-text call:
the Take Index, short fill-ins and the QA round trip, at Scribe's $0.22 per audio hour, $0 when cached. The cheapest
lever is the standard tier itself. Inside the director tier, the levers, cheapest first:
1. render-review rounds and chat edits start in a fresh context (`DirectorOptions.fresh_rounds`, on by default);
2. critics run at effort `high`;
3. `STUDIO_DIRECTOR_MODEL` / `STUDIO_DIRECTOR_EFFORT` (e.g. `claude-opus-5-5` / `high`) trade Director quality for
   price; compare pairwise first;
4. `--max-usd` caps the review loop.

Keys come from the environment or from the dotenv file named by `STUDIO_ENV_FILE`; see `.env.example` for the names.

## Tests

```bash
.venv/bin/python -m pytest tests -q -m "not real and not slow"   # keyless, offline
.venv/bin/python -m pytest tests -q -m slow                      # renders synthetic media (needs ffmpeg, Node, Remotion)
STUDIO_REAL=1 STUDIO_ENV_FILE=/path/to/.env .venv/bin/python -m pytest tests -q -m real
.venv/bin/ruff check studio tests scripts                        # lint
```

The suite runs in the director tier by default (`tests/conftest.py` sets `STUDIO_TIER=director`); standard-tier tests
switch the tier per test. Model calls in tests are scripted (pydantic-ai `FunctionModel`); none reach a provider.
Offline planner evaluation against jobs that already have a Director edit: `.venv/bin/python scripts/plan_eval.py
[JOB_DIR ...]` (see `docs/standard-tier-interfaces.md`, "Offline eval").
