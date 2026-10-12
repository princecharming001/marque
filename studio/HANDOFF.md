# Yunicorn Studio: handoff

You're picking up **Yunicorn Studio**, a from-scratch, quality-first, agentic video editor for creators' talking-head videos. Read this file first, then `README.md` (usage and the two tiers), `ARCHITECTURE.md` (the binding interface contract; §11 is the standard tier) and `skills/editing/SKILL.md` (the editing doctrine the AI follows). The latest status is §6c.

## 1. What the owner asked for (non-negotiable)

- Rebuild the video editing engine **from scratch** as an open-ended, iterative, agentic editor, not a fixed rule set.
- **Claude-first**, but it must also accept a creator's own key for Anthropic, OpenAI, Google Gemini or any OpenAI-compatible endpoint (BYOK). The default runs on our own house key.
- **The only goal is the quality of the final video.** Ignore cost and latency. *(Changed on 2026-10-09 for the default path: a standard edit must cost under $0.20 a video. That tier is now the default. The quality-first Director edit stays as the paid tier, unchanged; see §6b and §6c.)*
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
  6. Later commits (`git log`): Linux portability, cut-off joins, cost accounting, the standard-tier plan (`359ffd6`), and standard-tier phase A (`6bb7358`). Phase B and C are not committed yet as this is written.
- **Docs in the repo:**

  | Path | What it is |
  |---|---|
  | `studio/docs/plan.md` | The approved engineering plan |
  | `studio/docs/explainer.md` | The owner-facing plain-language explainer |
  | `studio/docs/research/` | Verified research by topic (`research_*.md`), the architecture, doctrine and toolchain designs, three design critiques, and the earlier research report |
  | `studio/docs/standard-tier-plan.md` | Why the Director edit costs dollars and how the standard tier gets under $0.20: design, cost tables, build plan, the real-credit probe (§4) |
  | `studio/docs/standard-tier-interfaces.md` | The standard tier's binding module signatures, with "As built" notes |

- **Not in git (you must recreate):** API keys, the Python venv, model weights (downloaded on first use), the test videos, and the work directory.

## 3. How it works (60 seconds)

This section describes the **director** tier, the reviewed edit. The default **standard** tier runs the same steps 1, 2, 4 and 6, but replaces 3 and 5 with one planner call, code, one render and code-only repair. See ARCHITECTURE §11 and §6c below.

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

- **Tiers:** `studio edit` defaults to the **standard** tier (`STUDIO_TIER`). Pass `--tier director` (or set `STUDIO_TIER=director`) for the Director and champion loop described above. A resume and `studio chat` stay in the job's tier. A standard edit is one planner call and one render at x264 `medium` (no champion loop); its compute is estimated at 33-54 vCPU-min for a 60 s take, not yet measured on the production host.

**Tests** (keyless and offline; 1,342 passed at phase A, plus 57 from phase B; see §6c):

```bash
.venv/bin/python -m pytest tests -q -m "not real and not slow"
.venv/bin/python -m pytest tests -q -m slow        # renders synthetic media, slower (needs Node 24 on PATH and the Remotion browser, or STUDIO_REMOTION_BROWSER)
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

## 6b. Session 2 update (2026-09-29, Linux cloud container)

**Done:**
- **Cut-off words never survive a join.** A plain ASR fragment (`kind == "cutoff"`, e.g. `restr-`, no dropout) at a
  seam or at the story's first/last word was not gated anywhere; dropout-chopped words (`truncated`) already were.
  Now `studio.doc.validate.cutoffs_at_joins` defines it once, and:
  - validator error `cutoff_at_join` blocks every Director stage exit, with the ID-level fix ("cut_words w0123 w0123
    so the join lands on w0122");
  - the radio test lists it as a blocker (replacing the narrower "story ends on a cut-off word");
  - `check_seams` audio facts name a fragment at the join;
  - invariant 1 fails on it in the render (`Integrity.cutoff_at_join`, measured on the timeline's continuous audio
    runs), so it can never ship. A fragment inside continuous kept speech stays a judgment call.
  - Regression tests: `tests/test_cutoff_joins.py`, `tests/test_qa.py::test_a_cutoff_word_at_a_join_fails_invariant_1`.
- **Linux portability** (production will most likely run on Linux):
  - `scripts/setup-linux.sh`: idempotent setup (FFmpeg 8.1.2 from Ubuntu's source tarball with zimg, rubberband,
    libass, x264, x265, vidstab; rubberband CLI; Node 24; venv; Remotion).
  - `STUDIO_REMOTION_BROWSER`: point Remotion at an existing headless Chrome when remotion.media is unreachable.
  - OCR off macOS: RapidOCR (Apache-2.0, ONNX, weights in the wheel) backs the b-roll text/watermark gates where
    Apple Vision is missing; the MSER fallback alone missed a plain "shutterstock" watermark.
  - Native FFmpeg AAC: PNS and intensity stereo off (low-bitrate tools that synthesize noise at 320 kbps; a dual-mono
    mix decoded 0.03 apart between channels), mid/side forced for a mono mix (channels now bit-identical).

- **AAC on Linux: FDK-AAC** (owner's decision). FFmpeg's native AAC is quality-limited (asked for 320k it stops at
  ~220-245 kbps on simple content, below invariant 10's 250 kbps floor, and no setting raises it). The master now
  prefers AudioToolbox, then `libfdk_aac` (CBR 320k, 20 kHz band, priming 2048 measured), then native `aac`.
  `setup-linux.sh` builds FFmpeg with `--enable-nonfree`: fine on our own servers, never redistribute that binary.

**Cost (owner asked for cheaper edits, 2026-10-08).** Measured on the first three real Linux edits at list prices
(4K60 15 s: $9.70; var-silences 105 s: ~$33; real-take40 40 s: ~$21), the spend was: the Director's conversation
growing to ~300k tokens and being re-read on every call (and re-written at the 1h cache-write price, 2x input, after
a render gap: one call cost ~$6), critics thinking ~100k tokens per rubric answer at effort `max`, and Fable's
per-token price. Changes, in order of no quality trade-off first:
- `DirectorOptions.fresh_rounds` (default on): every render-review round and chat edit starts in a fresh context
  from the stage summaries, the document and the critics' notes; the loop's brief already carries earlier rounds.
- Critics at effort `high` (`ROLE_EFFORT["critic"]`, the second judge too).
- `studio.agent.cost`: every report and CLI summary now carries the estimated model spend (list prices, cache
  writes at `CACHE_TTL`), by role and stage, so cost is measured, not guessed.
- The Director model and effort stay the quality defaults (Fable 5.1, `max`); `STUDIO_DIRECTOR_MODEL=claude-opus-5-5`
  and `STUDIO_DIRECTOR_EFFORT=high` are the cheaper settings to compare pairwise before switching the default.

**Engine bugs found by the first revision rounds (fixed, with regression tests):**
- Invariant 7 rejected revisions whose captions sat on the relaxed floor: (a) the gate allowed the floor only inside
  each page's *estimated* box while the renderer draws lines wider (`rendered_text_boxes`: the allowance is now the
  planned rows across the band); (b) the placer let a block sit flush on the floor and the renderer's stroke/shadow
  bled 6 px into the UI (`RENDER_BLEED_PX`: the placer keeps 8 px clear of the floor, the gate tolerates 8 px of
  fringe over any band edge). The preview tool, which measures the real render, had passed those pages; the gate
  and the tool now agree. Three d030 runs lost all their revision rounds to this before the fix.
- A house-Director job did not record its model: a resume on a host whose `STUDIO_DIRECTOR_MODEL` differs switched
  model silently (an Opus job finished on Fable). `job.json` now records the house model and effort; resume and chat
  reuse them; flags given now still win.

**Under $0.20 a video (owner requirement, 2026-10-09).** The reviewed agentic edit costs dollars by construction
(50-150 Director calls over a 36-tool, image-bearing context, then a render-critique-revise loop that was 90% of
the bill and won 1 of 10 decided rounds); mass-market editors run one ASR pass, code for everything measurable, 1-3
small text-only model calls, one render, no review. `docs/standard-tier-plan.md` is the investigated design and
build plan (13-agent workflow: trace profile, competitor pipelines and pricing, verified price sheets, codebase
levers, three designs, two judges, three refutations): a **standard tier** = the deterministic engine + one
structured-output planner call (Sonnet 5.5 low ~$0.03, Haiku 5.5 ~$0.002) + one render + the 10 invariants + code
repair, shipped NOT REVIEWED: **$0.04-0.05 model+ASR per video, $0.08-0.15 all-in with compute** at 1080p30
(4K60 sources must be downscaled at ingest); the Director/critic loop stays as a paid tier. ~18 days of work.
No-regret steps already done: `cost.py` prices corrected (Sonnet 5.5 cache read $0.10; Haiku 5.5 >100k tier);
Haiku 5.5 / Sonnet 5.5 capability overrides (pydantic-ai's profile would send Haiku 5.5 `budget_tokens`, a 400);
`--max-usd` / `STUDIO_MAX_MODEL_USD` stops the review loop at a dollar cap (`LoopConfig.max_model_usd`).

**Environment limits here:** no libvmaf (CAMBI banding metric reports unavailable; advisory, not an invariant).

## 6c. Session 3: the standard tier (2026-10-12)

**In plain terms.**
- `studio edit` now defaults to the **standard tier**. The estimate is about **$0.04** of model and speech-to-text per
  video, instead of $6-36, with the same ten hard checks.
- Each video ships marked **"NOT REVIEWED: standard tier"**: code checked it, but nobody watched it.
- The Director/critic edit is unchanged. Run it with `--tier director`, or upgrade a standard job the same way.
- **Nothing in the standard tier has run against a real model or real speech-to-text yet**, because the API credits
  are exhausted. Treat its cost and quality as unproven until the probe below runs.

**Built, by phase.** The design is `docs/standard-tier-plan.md`; the signatures and "As built" notes are in
`docs/standard-tier-interfaces.md`.

*Phase A (commit `6bb7358`):*
- **Tiers.** `STUDIO_TIER` (default `standard`), `--tier`, `--planner-model`, `STUDIO_PLANNER_MODEL` /
  `STUDIO_PLANNER_EFFORT`. `job.json` records the tier and planner, so a resume or a chat stays in its tier. Jobs from
  before tiers stay Director jobs.
- **Planner role.** Effort `low`, a 5-minute cache, and capability fixes so Haiku 5.5 / Sonnet 5.5 get effort, not a
  thinking budget.
- **The digest** (`studio/plan/digest.py`): the take as text with word IDs, about 0.8-1.3k tokens per real take.
- **The decision** (`studio/plan/decision.py`): `PlanDecision`, which names IDs only, and the guards that reject an
  unusable answer.
- **The planner** (`planner.py`, `prompt.py`):
  - one structured-output call and one retry carrying the rejections;
  - on any failure, the pure-code plan;
  - `STUDIO_PLANNER_FALLBACK` is the only escalation;
  - everything recorded in `logs/plan.json`.
- **Rendering.** Render profile `standard` (x264 `medium` CRF 16, same tags), `STUDIO_REMOTION_CONCURRENCY`, and an
  ingest cap that turns a 4K60 upload into a 1080p30 lean working copy.
- **ASR accounting.** Every Scribe/AssemblyAI call is traced and priced in the report. This also fixed an ASR cache
  miss that re-paid speech-to-text on every re-run: libsndfile writes a timestamp into the WAV.
- **Shared with the Director.** `validate.radio_blockers` and `compile.color.measure_color`.

*Phase B (not committed yet as this is written):*
- **`studio/plan/code_plan.py`.** The decision (or none) becomes a complete document: story, clause-edge cut-off
  removal, fillers, pauses, pins, an optional punch-in, labels, one uniform caption size, voice chain, colour only on
  a fault. `plan_document` writes it as a fresh version, and the radio test gates it. Eleven bugs in the earlier draft
  were fixed, among them:
  - captions never cleared the size ladder on real takes;
  - a re-plan was applied on top of the old document, whose pins broke it;
  - filler and pause rules were looser than the doctrine;
  - the colour check could brighten deep skin;
  - the punch-in could hold to the end of the video;
  - a cut-off at a sentence start dropped the whole sentence.
- **`studio/plan/repair.py`.** Code-only fixes for invariants 1, 4, 7 and 8, with at most one re-render. A caption
  fix re-renders only the overlay layer and the master, on the failed render's hard-linked A-roll and mix. A loudness
  fix re-masters the mix and re-muxes onto the copied video stream. Failures with no code fix render nothing and are
  reported.
- **`scripts/plan_eval.py`.** An offline harness comparing the code plan, and any `logs/plan.json` decision, with the
  Director's delivered document on existing jobs. No model calls, about 1 s a job.

*Phase C:*
- `_edit_standard`: ingest caps → index → digest → planner → code plan → one render → QA → repair → deliver one final
  with the NOT REVIEWED report;
- `_chat_standard`;
- the upgrade from a standard job to the Director;
- these docs: README tiers, ARCHITECTURE §3 and §11, the "As built" notes.

*Review fixes (after phase C; the list is in `docs/standard-tier-interfaces.md`, "Review fixes"):*
- **Pipeline.** An interrupted upgrade resumes as an upgrade; `--tier standard` after an upgrade re-plans instead of
  relabelling the Director's champion; a planner call that never answered is made again on a crash-resume; the
  tier's hard limits (source ≤ 10 min, output ≤ 90 s) refuse before anything is paid; the pure-code second attempt
  runs only for plan-dependent failures; `studio render` and `studio index` follow the job's tier.
- **Cost.** Paid voice isolation is priced ($0.12/min) and kept within a $0.17 running API budget; the planner and
  the chat have an 8k output budget, a $0.15 spend cap (`--max-usd`), no server-side refusal fallback, and a
  fallback model priced above the primary is refused.
- **CLI.** Exit code 4 when nothing was delivered.
- **Chat.** Only requests that ask to add music, SFX, b-roll or isolation are refused without a call ("remove the
  footage of me coughing" is planned), and the suggested upgrade command carries `--max-usd 3`.
- **Code plan.** A speaker's stumble with no gap to cut in stays (it no longer costs its clause or the CTA); the
  CTA/payoff sentences are never dropped by the cut-off rule; dropout silence inside kept speech is cut; stalls come
  down to the speaker's boundary pause; captions never split a page into a flicker (a size steps down instead);
  decision accents keep the 25 % page share; the CTA lexicon matches imperatives only; the brief's hook skips a
  spoken "Part one." label; "one" and list markers no longer draw accents.

**Tests.**
- Keyless suite: **1,342 passed, 0 failed** at phase A (`6bb7358`).
- Phase B adds **57 keyless tests** (code plan 26, repair 21, eval 10) and **2 slow tests**: repair's real overlay
  pass and audio pass, 134 s. All pass on their own; the five plan test files together pass 82.
- After phase C and the review fixes the full keyless suite is **1,476 passed, 4 skipped, 0 failed** (20.5 min). The
  slow tests were not re-run after the review fixes.
- `ruff check studio tests scripts` is clean.

**Keyless checks on the five real jobs.** Run locally with `scripts/plan_eval.py`; nothing was copied into the repo.
The code plan here is without a planner decision.

| Job | Kept-word agreement with the Director | Payoff/CTA pins kept | Output s (Director / plan) | Caption px (Director / plan) |
|---|---|---|---|---|
| d030, Opus Director | 0.945 | 11/11 | 25.9 / 27.9 | 80 / 64 |
| d030, Fable Director | 0.978 | 35/35 | 26.9 / 27.9 | 70 / 64 |
| real-take40 | 1.000 | 18/18 | 37.0 / 37.0 | 80 / 64 |
| var-silences | 0.835 | 16/16 | 32.4 / 38.0 | 68 / 64 |
| 4k60 | 1.000 | 11/11 | 15.2 / 15.2 | 72 / 64 |

- Every plan validated with 0 errors and 0 radio-test blockers. Every word the plan removes, the Director removed
  too.
- On var-silences, the cuts at the recording dropouts match the Director word for word ("life," and "buys,").
- The gaps are story calls that belong to the planner: the spoken "Part one." label (3 of 5 jobs), var-silences s003
  (restates the hook), and where d030 ends.
- With decisions that copy the Directors' choices, d030 and take40 reach 1.000 and var-silences 0.981.
- Synthetic-media repair: a caption pushed into the platform UI was fixed by a 38.5 s overlay pass, against about 80 s
  for the first render. A mix 3 dB too quiet came back to -14.0 LUFS without re-encoding the picture.

**Product calls for the owner.**
1. **Caption size.** The standard tier picks the biggest size that pages as cleanly as the smallest. On all five real
   takes (fast talkers, 186-212 words a minute) that is the 64 px floor; the Directors chose 68-80 px and accepted
   more one-word or shrunk pages. Allowing one extra paging fault would give take40 68 px and var-silences 66 px
   (`code_plan._caption_ops`).
2. **"Part one."** The pure-code plan keeps a spoken series label that every Director cut (the brief's hook now skips
   it, and the planner's checklist names it as preamble). Leave the cut to the planner's `preamble` drop, or add a
   code rule for the fallback path.
3. **Cuts inside a sentence.** The planner can drop only whole sentences, so "Part one:" at the start of a longer
   sentence cannot go. A `cut_ranges` field with the same guards would fix it.

**What needs a real-credit probe** (`docs/standard-tier-plan.md` §4; about $3-5 and 1.5 hours on one 4-vCPU box).

*The runs:*
- **Three videos:**
  - d030 (30 s): tests the "Part one." drop, the payoff pin, and the out-point "proof on.";
  - real-take40 (40 s): tests the `1/3 2/3 3/3` labels and the "Fusion isn't" text fix;
  - var-silences (105 s): tests the dropout joins at "life,"/"buys," and the "restates the hook" drop.
- **Two planners:** each video through the standard tier on Haiku 5.5 and on Sonnet 5.5 at effort `low`, 6 edits.
- **Chat:** one chat edit on each of the 6 results.
- **4K60:** one 4K60 ingest through the 1080p30 path, for compute only.
- **Judging:** each of the 6 outputs against its existing Director champion, with one Opus 5.5 judge in both orders.

*Pass criteria:*
1. **Planner.** Input at most 6k tokens and output at most 3k at effort `low`. Structured output accepted on both
   models with this schema. At most 1 retry across the 3 videos, and zero refusals. Replace the chars/3.5 estimates
   with `count_tokens`.
2. **Agreement with the Director.**
   - kept-word agreement at least 0.9;
   - the same payoff pin, and the same out-point on d030;
   - "Part one." dropped, the take40 labels emitted, and the var-silences joins.

   Choose Haiku or Sonnet on this, not on price.
3. **Judge.** Zero P0 (a cut-off end, a caption over the eyes or mouth, a clipped word) across the 6. Record the P1s.
   The Director winning most comparisons is expected: that is the difference between the tiers, not a failure.
4. **Compute** on the production host:
   - the standard render at most 10 vCPU-min per 30 s of output;
   - Remotion at concurrency 4 fits in 8 GB;
   - the CRF 16 bitrate at most 16 Mbps, and invariant 10 passes;
   - the 4K60 ingest at most 3x the source duration.
5. **ElevenLabs billing.** Compare the dashboard with the audio seconds in `trace.jsonl`; the per-minute rounding is
   unstated.
6. **Chat.** The time and cost of an overlay-only re-render, against a full one.

*How to run it, once phase C has landed and keys are set* (`STUDIO_ENV_FILE`, `STUDIO_REAL=1`):

```bash
for m in claude-haiku-5-5 claude-sonnet-5-5; do
  .venv/bin/studio edit ~/studio-testdata/qa-editor-d030-h264.mov     --tier standard --planner-model $m --platform tiktok
  .venv/bin/studio edit ~/studio-testdata/qa-editor-real-take40.mov   --tier standard --planner-model $m --platform reels
  .venv/bin/studio edit ~/studio-testdata/qa-editor-var-silences.mov  --tier standard --planner-model $m --platform reels
done
```

`scripts/plan_eval.py` compares a `logs/plan.json` with the Director document **in the same job directory**. For
criterion 2, run the planner once over each existing Director job's own index (one planner call, no render), then
run the harness on that job:

```bash
JOB=/root/studio-work/20261008-225344-08453c   # d030; real-take40 is 20261008-205115-fc126f, var-silences 20261008-200717-0b7de7
STUDIO_PLANNER_MODEL=claude-haiku-5-5 .venv/bin/python -c '
import sys; from pathlib import Path
from studio.jobs import Job; from studio.plan.digest import build_digest; from studio.plan.planner import plan
job = Job(Path(sys.argv[1])); ix = job.load_index()
print(plan(job, ix, build_digest(ix, platform=sys.argv[2])).note)' "$JOB" tiktok
.venv/bin/python scripts/plan_eval.py "$JOB"
```

Each planner run overwrites that job's `logs/plan.json`, so evaluate after each model. Afterwards, update the cost
figures in README, ARCHITECTURE §11 and the plan from the measured `report.md` cost lines, and pick the default
planner.

## 7. Your next steps, in order

1. **Set up** (§4) and run the keyless tests.
   - **First, once credits exist:** land phase C, run the full keyless suite, then the standard-tier probe in
     §6c. It decides the default planner and replaces the estimated costs with measured ones.
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
