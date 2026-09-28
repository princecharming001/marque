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

```bash
.venv/bin/studio edit clip.mov --brief "teach one editing tip, CTA follow" --platform tiktok
.venv/bin/studio index clip.mov
.venv/bin/studio render /Users/home/studio-work/<job_id> --preview
.venv/bin/studio chat /Users/home/studio-work/<job_id> "tighten the pauses in the middle"
.venv/bin/studio qa /Users/home/studio-work/<job_id>
```

Status: `index`, `render`, `qa` and `report` are wired end to end (`tests/test_integration_smoke.py` runs
them through the CLI on a synthetic take). `edit` and `chat` exit with code 3 until the Director, critics and
champion loop (`studio/agent/director.py`, `critics.py`, `loop.py`) are built; until then a document can be
edited with ops through `studio.agent.tools.EditSession(job).apply_ops([...])` and rendered with `studio render`.

Jobs live in `$STUDIO_WORK_DIR` (default `/Users/home/studio-work`). Keys come from the environment or
from a dotenv file named by `STUDIO_ENV_FILE` (read into memory, never exported or logged); see
`.env.example` for the names.

## Tests

```bash
.venv/bin/python -m pytest tests -q                  # keyless, offline
STUDIO_REAL=1 STUDIO_ENV_FILE=/path/to/.env .venv/bin/python -m pytest tests -q -m real
.venv/bin/ruff check studio tests                     # lint
```
