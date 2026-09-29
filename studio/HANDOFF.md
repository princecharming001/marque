# Yunicorn Studio: handoff

You're picking up **Yunicorn Studio**, a from-scratch, quality-first, agentic video editor for creators' talking-head videos. Read this file first, then `README.md` (usage), `ARCHITECTURE.md` (the binding interface contract) and `skills/editing/SKILL.md` (the editing doctrine the AI follows).

## 1. What the owner asked for (non-negotiable)

- Rebuild the video editing engine **from scratch** as an open-ended, iterative, agentic editor, not a fixed rule set.
- **Claude-first**, but it must also accept a creator's own key for Anthropic, OpenAI, Google Gemini or any OpenAI-compatible endpoint (BYOK). The default runs on our own house key.
- **The only goal is the quality of the final video.** Ignore cost and latency.
- Editing is guided by deep research into how to edit talking-head videos (cuts, pacing, b-roll, captions, music, SFX, speed, zooms, colour, audio). That research lives in `skills/editing/` as guidance, not hard rules. "None" (no music, no b-roll, no zoom) is always a valid choice, and over-editing is the main failure to avoid.
- Use proven open-source and free tools where possible; paid tools only when clearly better. Don't build what already exists.
- **Not wired into the app yet.** The owner will send 5 videos and ask you to edit them in chat to verify quality. Only after that does it get integrated into the iOS app and backend.
- **Keep the old engine intact** so the product can revert. It is frozen at git tag `static-engine-v1` (commit `1c07c18`).

## 2. Where things are

- **Repo:** `github.com/princecharming001/marque`, branch **`studio-engine`**. Everything lives under `studio/`, and nothing else in the repo was changed.
- **Commits, oldest first:**
  1. `20929fb`: doctrine and contract.
  2. `b360d6a`: engine modules.
  3. `c7bd2aa`: Director, critics, loop, pipeline and CLI.
  4. `e6e0db0`: caption fixes from the first real runs.
  5. The commit that adds this handoff file.
- **Docs in the repo:**

  | Path | What it is |
  |---|---|
  | `studio/docs/plan.md` | The approved engineering plan |
  | `studio/docs/explainer.md` | The owner-facing plain-language explainer |
  | `studio/docs/research/` | Verified research by topic (`research_*.md`), the architecture, doctrine and toolchain designs, three design critiques, and the earlier research report |

- **Not in git (you must recreate):** API keys, the Python venv, model weights (downloaded on first use), the test videos, and the work directory.

## 3. How it works (60 seconds)

1. **Ingest** (`studio/media`): turns the upload into an upright SDR BT.709 ProRes mezzanine (HDR tone-mapped exactly once), a 48 kHz float dialogue WAV, and a proxy.
2. **Perception, the "Take Index"** (`studio/perception`):
   - ElevenLabs Scribe v2 verbatim transcript, which keeps fillers and false starts (AssemblyAI is the fallback);
   - word IDs `w0001…`, sentences, and retake clusters;
   - Silero VAD + energy gaps with snap points and breath spans;
   - Parselmouth prosody and emphasis;
   - audio metrics;
   - a MediaPipe face track.
3. **The Director** (`studio/agent/director.py`), Claude by default (`claude-fable-5-1`, with `claude-opus-5-5` as fallback), edits a typed **CutDocument** only through validated, **ID-addressed ops**. A model never supplies a timestamp; it works in stages:
   1. brief;
   2. story cut, which must pass a "radio test";
   3. fine cut;
   4. finishing, in order: reframe/punch-ins → b-roll and cards → captions → sound (voice chain, music, SFX) → colour.
4. **Compiler** (`studio/compile`): rational-time timeline → ffmpeg A-roll → Remotion overlay layer with alpha (`studio/overlay/`) → sample-exact audio mix (crossfades, room tone, Rubber Band time-stretch, ducked music, true-peak-limited −14 LUFS) → x264 masters.
5. **Critics and champion loop** (`studio/agent/critics.py`, `loop.py`):
   - Render at full quality.
   - Check the 10 invariants (`studio/qa`).
   - A frame judge from a different model answers the doctrine's critique rubric, with notes keyed to word IDs.
   - The Director revises.
   - Position-swapped pairwise judging picks a winner.
   - The loop stops after 2 winless rounds; the champion ships.
6. **Deliverables:** `final_<platform>.mp4`, `final_nomusic.mp4`, `cover.jpg`, `captions.srt`, the doc JSON and `report.md`, all in the job directory.
7. **`studio chat <job> "<instruction>"`:** re-enters the Director for creator tweaks, re-renders and re-checks.

## 4. Setup on a new machine (macOS, Apple Silicon was used; Linux should work)

1. **System tools:**
   - **ffmpeg 8.x** with `zscale` (libzimg), `rubberband`, `libass` and `loudnorm`. `brew install ffmpeg` has these. libplacebo is not required (it's absent here), so Dolby Vision RPU isn't applied and DV footage is tone-mapped from its HLG base.
   - **The `rubberband` CLI:** `brew install rubberband`.
   - **Node 24 and npm.**
   - **Python 3.12.**
2. **Python:**
   ```bash
   git clone git@github.com:princecharming001/marque.git && cd marque && git checkout studio-engine
   cd studio
   python3.12 -m venv .venv
   .venv/bin/pip install --upgrade pip wheel
   .venv/bin/pip install -e ".[dev]"   # if the extras fail, install the deps listed in pyproject.toml, then: pip install --no-deps -e .
   ```
   Notes:
   - mediapipe is pinned to **0.10.35**, because 1.0.1 aborts on macOS when building the face graph.
   - `deepfilternet` is not used; it needs Rust to build.
   - About 2 GB of models download into `studio/models/` on first use (the face landmarker, open_clip weights and Playwright Chromium), and the folder is gitignored.
3. **Remotion overlay project:**
   ```bash
   cd studio/overlay && npm ci && npx remotion browser ensure
   ```
   The fonts (OFL) are committed in `overlay/public/fonts/`.
4. **Keys:** put them in a dotenv file **outside the repo**, for example `~/studio.env`. **Never commit it, print it or log it.** The code reads the file itself when it's named by `STUDIO_ENV_FILE`:

   | Key | Status |
   |---|---|
   | `ANTHROPIC_API_KEY` | **Required.** The Director and critics |
   | `ELEVENLABS_API_KEY` | **Required for best quality.** Scribe v2 ASR, voice isolation, Music and SFX |
   | `PEXELS_KEY` | Stock b-roll |
   | `ASSEMBLYAI_KEY` | Fallback ASR |
   | `HIGGSFIELD_KEY` | AI stills. The account currently has **no credits** |
   | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Optional; enables the Gemini "watcher" critic, which watches and hears the video |
   | `OPENAI_API_KEY` | Optional |

   See `.env.example` for the exact names.

## 5. Running

From `studio/`:

```bash
export STUDIO_ENV_FILE=~/studio.env STUDIO_REAL=1 STUDIO_WORK_DIR=~/studio-work
.venv/bin/python -m studio.cli edit /path/to/take.mov --platform tiktok --out ~/studio-out/take1
.venv/bin/python -m studio.cli chat ~/studio-work/<job_id> "make the captions a bit bigger"
.venv/bin/python -m studio.cli qa ~/studio-work/<job_id>        # re-run the invariants
.venv/bin/python -m studio.cli report ~/studio-work/<job_id>    # regenerate report.md + contact sheet
```

- Passing a job directory to `edit` **resumes** a crashed run.
- BYOK: `--director-provider openai|google|openai_compat|anthropic --director-model <id> --director-key-env <ENV_VAR_NAME>`.
- Full-quality renders are slow: x264 `veryslow` and Remotion ProRes 4444. Expect **tens of minutes per 30–60 s take** with the champion loop. Run at most 2 edits at once.
- **Disk:** ProRes intermediates are about 26 MB/s at 1080p30 and much more at 4K60. Keep **≥10 GB free**; the pipeline refuses to render below 1.5 GB. Delete old job directories in the work dir freely.

**Tests** (keyless and offline; 1,211 pass):

```bash
.venv/bin/python -m pytest tests -q -m "not real and not slow"
.venv/bin/python -m pytest tests -q -m slow        # renders synthetic media, slower
STUDIO_ENV_FILE=~/studio.env STUDIO_REAL=1 .venv/bin/python -m pytest tests -q -m real   # small real API calls
```

**Test videos** (public URLs; download with `curl -L -o <name> <url>` into e.g. `~/studio-testdata/`):

| File | What it is | URL |
|---|---|---|
| `qa-editor-d030-h264.mov` | 30 s hot take | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/c6b40acc-d791-45ef-a2bc-da3db612478c/qa-editor-d030-h264.mov |
| `qa-editor-var-multitake.mov` | 51 s; verbatim retake + false start | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/3735be9b-0f54-49bc-862e-5b3cbc5094e7/qa-editor-var-multitake.mov |
| `qa-editor-var-silences.mov` | 105 s with long pauses | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/53a55b1b-682f-434c-bbad-caf941498c4d/qa-editor-var-silences.mov |
| `qa-editor-real-take40.mov` | 40 s real take, rotation metadata | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/4c08691b-1795-435e-bb02-f463d5abbd5f/qa-editor-real-take40.mov |
| `qa-editor-var-landscape.mp4` | Landscape 16:9 | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/62aae798-5805-4613-b2ac-654b285866e2/qa-editor-var-landscape.mp4 |
| `qa-editor-var-music.mov` | Music playing in the room | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/96d7d563-4033-4c88-a9e7-9faecaa84a37/qa-editor-var-music.mov |
| `qa-editor-var-4k60_hevc.mov` | 15 s 4K60 HEVC | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/20b16fa4-4da2-489b-8c7f-270ccf9cf83f/qa-editor-var-4k60_hevc.mov |
| `qa-editor-var-vfr.mov` | Variable frame rate | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/40aeb7da-5623-4994-bd3e-f4790655a332/qa-editor-var-vfr.mov |
| `qa-editor-var-noaudio.mp4` | No audio track | https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/uploads/5461f943-e0a6-4ae8-9102-47645503d4ed/qa-editor-var-noaudio.mp4 |

`qa-editor-real-take40` shows the owner's face: fine for internal testing, **never** for marketing.

## 6. Status (as of 2026-09-29)

**Verified:**
- 1,211 keyless tests pass.
- Real end-to-end edits of `d030` (30 s) and `var-multitake` (51 s) pass **all 10 invariants**. The multitake keeps the last complete take, removes the false start, and leaves no blank or click at the join.
- An independent craft review of 16 real edits found the Director restrained and doctrine-aligned: it never added music, b-roll or SFX without a stated job, pairwise judging runs in both orders, and no key appears in any trace.
- Captions were fixed after the first run:
  - Size: 88 px, weight 900, never below 64 px.
  - Placement: below the chin when the platform band allows it, otherwise above the head, never on the eyes or mouth, and inside the safe band.

**Not yet verified (the next job):** the other end-to-end cases were blocked by low disk space on the old machine and need re-running. A partial review found:
- **`var-silences` (105 s):** at two "dropout" joins the Director kept words the source cuts off mid-word ("meaningfu-|-nough"), which is a click the engine didn't catch. Cut-off words (`Word.kind == "cutoff"`) must never survive a join. Fix this in the compiler/validator, and give the Director feedback on it.
- **`var-landscape`:** the face-tracked 9:16 reframe is clean (static, exact, no jitter), but captions need another placement pass.
- **`real-take40`, `var-music`, `4k60`:** never rendered, so re-run them. Music-in-room voice isolation was judged correct in the plan stage.

**Known limitations:**
- No Gemini key, so the "watcher" critic falls back to frames plus audio metrics.
- Critics are same-family (Claude judging Claude) until another provider's key is added; this is flagged `same_family` in critique records.
- Higgsfield has no credits, so there are no AI-generated stills. B-roll relies on Pexels stock, creator media, screenshots and designed cards.
- Anthropic rejects `strict: true` for this many op-family tools ("compiled grammar too large"), so tools are non-strict with server-side Pydantic validation.
- No real iPhone HDR (HLG/Dolby Vision) clip has been validated yet.
- There is no scoped preview render; previews re-render the whole document.
- Deferred ideas are logged in the build notes: J/L picture cuts on blinks, a `repetition` word kind, and partial pickup splices.

## 7. Your next steps, in order

1. **Set up** (§4) and run the keyless tests.
2. **Re-run the blocked end-to-end cases** one or two at a time:
   ```bash
   .venv/bin/python -m studio.cli edit <take> --platform reels --out ~/studio-out/<name>
   ```
   Run it on `var-silences`, `real-take40`, `var-landscape`, `var-music` and `4k60_hevc`. Review each like a demanding short-form editor: look at the contact sheets and at frames around every seam, caption page and insert; diff the render's transcript against the source transcript; check loudness, true peak and clicks; and check the Director's brief and notes against `skills/editing/`.
3. **Fix what you find.** Fix engine bugs in code and add a regression test for each. Improve Director judgment through tools, tool feedback or stage instructions; don't hard-code taste as rules. Start with the cut-off-word join bug above.
4. **Independent reviews:**
   - timing and audio correctness;
   - picture, colour and overlays;
   - agent craft against the doctrine.

   Then run the final real edits of `d030`, `multitake` and `real-take40` on TikTok, plus one chat edit ("make the captions a bit bigger and add subtle background music").
5. **Edit the owner's 5 videos in chat** when they send them, and show the finals and the contact sheets.
6. Only after the owner approves quality: plan the app integration. The old plan §12 describes routing per job with a kill switch, and reverting via the `static-engine-v1` tag.

## 8. Rules the owner cares about

- **Never push to `main`, deploy, run App Store or TestFlight builds, or change production environment variables** without the owner's explicit, per-time "push it now". Local commits and pushes to this feature branch are fine when asked.
- Don't touch `backend/` (the live production backend) or the iOS app from this work until integration is approved.
- Never commit, print or log API keys, `.env` files or `.p8` keys.
- Test creator IDs or objects created in production must use the `qa-editor-` prefix. Never write to production Supabase directly, and never delete production data.
- Never use the owner's face or videos in marketing materials.
- No Apple emojis in app UI.
- Keep the doctrine as guidance; keep only the 10 invariants in `ARCHITECTURE.md` §7 as hard gates.
