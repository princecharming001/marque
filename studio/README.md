# Yunicorn Studio

A standalone, quality-first agentic editor for talking-head videos. Code perceives, measures and
enforces; models judge and decide. A Director model edits a typed **CutDocument** (word-ID based, never
model timestamps) through validated ops; a compiler turns it into an exact rational-time **Timeline**;
ffmpeg/Remotion render it; critics and a champion loop iterate until no revision wins.

The binding module contract is [`ARCHITECTURE.md`](ARCHITECTURE.md). Editing doctrine lives in
`skills/editing/`. Studio is not wired into the app or the old backend.

## Layout

```
studio/            python package (import name `studio`)
  config.py        settings, house model ids, key handling, redact()
  timebase.py      µs ints, Fraction seconds, frame/sample grids, fps normalisation
  jobs.py          job directories, JSON helpers, trace, document versions
  media/ perception/ doc/ compile/ audio/ broll/ agent/ qa/  pipeline.py  cli.py
tests/             keyless, offline pytest suite (+ `@pytest.mark.real` tests)
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
.venv/bin/python -m studio.cli edit clip.mov --platform tiktok --out /Users/home/studio-work/final-clip
.venv/bin/python -m studio.cli chat /Users/home/studio-work/final-clip "make the captions a bit bigger"
```

(`.venv/bin/studio …` is the same entry point.)

### `studio edit <video | job_dir>`

Edits one take end to end: ingest → Take Index → the Director's staged edit (brief, story + radio test, fine cut,
then reframe → b-roll/cards → captions → sound → colour, finalize) → the champion loop (full-quality render →
invariants → critics → Director revision → position-swapped pairwise) → hook-alternate variants → closing watch →
delivery.

| Flag | Meaning |
|---|---|
| `--platform tiktok\|reels\|shorts` | Destination (default `tiktok`): safe zones, encode limits, the `final_<platform>.mp4` name |
| `--brief TEXT` | Creator intent (goal, CTA, audience, vibe); otherwise the Director infers it from the footage |
| `--style NAME` | Style hint (educational, storytime, comedy, …); otherwise classified from the transcript |
| `--media FILE` | Creator b-roll the Director may use (repeatable) |
| `--out DIR` | Also copy the deliverables into `DIR` |
| `--rounds N` | Champion-loop round guard (default 12; the loop normally stops after 2 winless rounds). Only to bound a debugging run |
| `--asr-provider elevenlabs\|assemblyai` | Transcription provider (default ElevenLabs Scribe) |
| `--director-provider P --director-model M --director-key-env VAR` | Bring your own key for the Director (see BYOK) |
| `--house` | Switch a BYOK job back to the house Director explicitly |
| `--json` (before the command) | Print the result as JSON |

Passing a job directory instead of a video **resumes** a crashed or interrupted edit: finished steps are skipped
(ingest and index by their files, Director stages by `logs/director_state.json`, the loop by
`logs/loop_state.json`). The command prints the job directory, the finals, the stop reason, whether every
invariant passed, and whether the critics reviewed the shipped version (`reviewed: False` means it ships **NOT
REVIEWED**: the critics were unreachable after retries; see `review_note`).

### `studio chat <job_dir> "<instruction>"`

Applies a creator instruction to the delivered champion: the instruction is the brief for the change (no pairwise
veto), the Director re-enters at the earliest stage it touches and runs that stage's checks (radio test after a
story change, compile/seam checks after a cut change, a caption preview after a caption or framing change), the
result is rendered at full quality and must pass the ten invariants (the Director gets two attempts to fix a
failure; otherwise the previous champion stays and the summary says NOT DELIVERED), a critic sanity note is
recorded, and the new render replaces the deliverables. `--out DIR` copies them; the job's own Director (house or
BYOK) is reused.

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
| `deliver/final_nomusic.mp4` | The same cut without music (always delivered) |
| `deliver/cover.jpg`, `deliver/captions.srt` | Cover frame (no running captions) and the caption sidecar |
| `deliver/edit_v<n>.json`, `deliver/timeline.json` | The shipped CutDocument and its compiled timeline |
| `deliver/alternates/<label>/final_<platform>.mp4` | A hook-alternate variant the champion beat (or the version a winning variant replaced) |
| `report.md` | Brief, story, captions, audio, QA table, review status (reviewed / NOT REVIEWED, same-family only), critique history, op log, model calls |
| `renders/r<n>/` | Every full-quality render (finals, QA, timeline); intermediates are pruned once QA has run |
| `critique/` | Critic notes (`<render>/notes.json`, or `unreviewed.json` when a review failed), pairwise verdicts, final watch, caption previews |
| `logs/`, `trace.jsonl` | Director and loop state (resume), every model call and tool call (never a key) |

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

### How the review loop decides

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

Status: every command is wired end to end.

Operational notes: at most 2 edits run machine-wide (`STUDIO_MAX_CONCURRENT_EDITS`), and only one heavy write at a
time (full/preview renders and mezzanine encodes share `STUDIO_MAX_CONCURRENT_RENDERS`, default 1). Disk is budgeted
(`studio.storage`): before the Director starts, the edit's renders must fit (lean intermediates, after reclaiming);
every render needs its *estimated* size plus `STUDIO_MIN_FREE_GB` headroom (default 1 GB), reclaims first (this job's
stale intermediates and overlay cache, then idle jobs' render intermediates and mezzanines — regenerable, rebuilt from
`media/original.*` on demand), then falls back to the lean A-roll intermediate, and only then refuses. Every render's
intermediates are pruned as soon as its QA has run; a delivered job keeps its finals and drops its mezzanine
(`STUDIO_KEEP_MEZZ=1` keeps it). `STUDIO_DIRECTOR_EFFORT` (default `max`: quality is the only goal) sets the
Director's effort.

Keys come from the environment or from the dotenv file named by `STUDIO_ENV_FILE`; see `.env.example` for the names.

## Tests

```bash
.venv/bin/python -m pytest tests -q                  # keyless, offline
STUDIO_REAL=1 STUDIO_ENV_FILE=/path/to/.env .venv/bin/python -m pytest tests -q -m real
.venv/bin/ruff check studio tests                     # lint
```
