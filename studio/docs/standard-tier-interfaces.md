# Standard tier: module interfaces (binding for the build)

Companion to `standard-tier-plan.md`. These signatures are fixed so the modules can be built in parallel; change one
only by editing this file first. Everything here is Python 3.12, pydantic v2, keyless-testable (scripted models as in
`tests/director_script.py`; no real API calls). The Director/critic path is untouched and stays selectable.

**Status (2026-10-12).** Phase A built tiers, digest, decision, planner, the render profile, the ingest conform and
ASR accounting (commit `6bb7358`). Phase B built the code plan, repair and the offline eval harness. Phase C wired
the pipeline branch, the standard chat and the upgrade; a review pass then fixed what it found (the "Review fixes"
section at the end). "As built" notes below record where the build refined a signature. **No real model or ASR call has been made through any of it** (no credits): structured-output acceptance,
token usage, refusal rates and real-take quality are open until the probe in `standard-tier-plan.md` §4.

## Tiers and settings (`studio/config.py`, `studio/cli.py`, `studio/pipeline.py`)

- `Settings.tier: Literal["standard", "director"]` from `STUDIO_TIER` (default **`standard`**). **Built in phase A**
  (`pipeline._resolve_tier`: flag > job meta > legacy director jobs > settings; `studio chat --planner-model`
  exists, chat has no `--tier`).
- `Settings.planner_model: str` from `STUDIO_PLANNER_MODEL` (default `claude-sonnet-5-5`);
  `Settings.planner_effort: str` from `STUDIO_PLANNER_EFFORT` (default `low`).
- `providers.ROLE_EFFORT["planner"] = "low"`; `providers.house_spec("planner")` → the planner model with the house
  Anthropic key; BYOK via `spec_from_cli` works for the planner too. The planner role uses a **5m** cache TTL
  (`model_settings(spec, role="planner")`), the other roles keep `CACHE_TTL`.
- CLI: `studio edit --tier standard|director`, `--planner-model M`; `studio chat` reuses the job's tier and takes
  `--max-usd` (the standard chat's spend cap); `studio index --tier` picks the ingest (standard: the 1080p30 caps).
  Exit code **4** = nothing delivered (a standard edit or a chat whose render still fails the invariants after
  repair); 0 ok, 1 error, 2 usage, 3 not implemented.
  `job.json` meta records `tier` and `planner` (provider, model, effort) next to `director`; resume and chat reuse
  them; flags given now win.
- `pipeline.edit(..., tier: str | None = None, planner_model: str | None = None)`; `tier or settings.tier`.
  `EditResult` gains `tier: str` and `reviewed` semantics: the standard tier ships `reviewed=False`,
  `review_note="standard tier: code QA only"`.

## Digest (`studio/plan/digest.py`)

```python
@dataclass
class Digest:
    text: str                 # what the planner reads (≤ max_tokens; sentence blocks dropped from the middle first)
    sentence_ids: list[str]   # every sentence id in the text, in order
    cluster_recs: dict[str, str]   # cluster id -> recommended sentence id
    stats: dict[str, Any]     # wpm, energy, pause median ms, snr_db, duration_s, words, fillers, cutoffs, music_in_room

def build_digest(index: TakeIndex, *, platform: str, brief: str | None = None, style_hint: str | None = None,
                 max_tokens: int = 12_000) -> Digest
```
Lines: one per sentence `s012 [c01 rec] (3.1-6.4s) Everyone says you need {um} an authentic brand story. [g0012 0.62s]`
with fillers in `{}`, cut-offs as `restr-`, pauses ≥ 250 ms marked inline, incomplete sentences tagged
`[incomplete]`, dropout-chopped words tagged `[chopped]`; then a short measured block: energy/wpm/pauses, SNR and
music-in-room, top-12 emphasis word IDs with their words, caption placement options (lifted from the Director's
`placement_options` lines), the voice-chain plan note, the colour measurement line, and the platform length window.
Tokens are estimated as `len(text) / 3.5`. **As built:** every word carries its ID inline (the index's `full`
transcript grammar) because the planner names word IDs with no lookup tool; a `Grammar:` line explains the marks.
`Digest.stats` carries extra keys (chopped, sentences, clusters, chars, tokens_est, omitted_sentence_ids). The
colour line is appended by the caller (`planner.py`/`code_plan.py`) from `studio.compile.color.measure_color`
when the job's mezzanine exists; `build_digest` itself never needs the job.

## Decision (`studio/plan/decision.py`)

```python
Reason = Literal["preamble", "tangent", "after_payoff", "unfinished", "repeat", "repeats_hook"]

class WordRange(BaseModel): from_word: str; to_word: str
class SentenceDrop(BaseModel): sentence_id: str; reason: Reason
TrimReason = Literal["label", "preamble", "tangent", "after_payoff"]
class WordTrim(BaseModel): from_word: str; to_word: str; reason: TrimReason   # one edge of a kept sentence
class TakeChoice(BaseModel): cluster_id: str; sentence_id: str
class PunchIn(BaseModel): anchor_word: str; reason: str
class TextFix(BaseModel): word_id: str; text: str          # caption display text for one word (never the audio)
class StructureMarker(BaseModel): anchor_from_word: str; anchor_to_word: str; label: str   # "1/3", "Step 2"

class PlanDecision(BaseModel):
    style_primary: str; style_secondary: str | None = None
    goal: str                     # ≤ 25 words
    hook_promise: str
    drop_sentences: list[SentenceDrop] = []
    trim_words: list[WordTrim] = []             # "Part one:" before the claim in the same sentence
    choose_take: list[TakeChoice] = []
    payoff: WordRange | None = None
    cta: WordRange | None = None
    must_keep: list[WordRange] = []
    deliberate_beat_gap_ids: list[str] = []     # pauses to keep at their measured length
    cut_filler_word_ids: list[str] = []
    punch_in: PunchIn | None = None             # at most one; applied only when the source's clean scale allows ≥ 1.15x
    caption_emphasis_word_ids: list[str] = []   # ≤ 8, at most one per page
    caption_text_fixes: list[TextFix] = []
    structure_markers: list[StructureMarker] = []
    notes: str = ""

def validate_decision(d: PlanDecision, index: TakeIndex) -> list[str]
```
`validate_decision` returns the rejections (empty = valid). It rejects:
- an ID that does not exist;
- drops that remove more than 50% of the spoken words;
- a `repeat`/`repeats_hook` drop with no later sentence in the same cluster and no stated hook;
- a payoff or CTA range that is not inside the kept words;
- dropping a CTA-lexicon sentence (follow/comment/link/part two…) for any reason except `repeat`;
- more than one punch-in, or more than 8 emphasis words;
- a non-filler word in `cut_filler_word_ids`;
- a reversed range;
- a take choice outside its cluster, or a chosen take that is also dropped.

The 25-word goal is only an instruction in the prompt; it is never a rejection. Helpers: `kept_word_ids(d, index)`,
`is_cta_sentence(text)`, `CTA_LEXICON`, `MAX_DROP_SHARE` (0.5), `MAX_EMPHASIS_WORDS` (8).
`PLAN_SCHEMA_JSON` is the compact JSON schema string sent to the model.

**As built:** the schema is 2,295 chars. The models carry no `Field` constraints, because Anthropic's grammar compiler
rejects or ignores them. Every limit lives in `validate_decision`, and its rejection lines are plain text the one
retry carries back to the model. A decision can only drop **whole sentences**; it cannot cut a range inside a kept
sentence (see the eval finding under "Offline eval").

## Code plan (`studio/plan/code_plan.py`)

```python
def code_plan(index: TakeIndex, *, platform: str, decision: PlanDecision | None, settings: Settings | None = None,
              brief: str | None = None, style: str | None = None, job: Job | None = None) -> list[dict[str, Any]]
```
Returns the ordered op list (the existing ID-addressed op schemas, applied with `studio.doc.ops.apply_ops`) for a
complete document: `set_brief`/`set_style` (from the decision or from measured energy), `set_story` (recommended
takes, false-start/abandoned drops, the decision's drops and take choices, cut-off and dropout-chopped words removed
back to the nearest clause edge inside the sentence else the sentence dropped), pins, `set_gap`/`cut_words` for
fillers and pauses from `constants.yaml` priors (never below the speaker's median; `deliberate_beat_gap_ids` kept),
seams `cut`, the optional punch-in via `set_framing` only when `timeline` reports a clean scale ≥ 1.15x, captions
via `auto_caption_plan` + the placer's `auto` ladder with a uniform-size rule (largest size in 64-88 px at which
every page is one line; re-page rather than shrink) and the decision's emphasis/text fixes, `add_text` for
structure markers, `set_voice_chain` from measurements (paid isolation only when `music_in_room` and the source
≤ 60 s), `set_color` only on a measured fault, music/SFX/b-roll none, deliverables for the platform.
`radio_blockers(doc, index) -> list[str]` moves from `Director._radio_blockers` to `studio/doc/validate.py` and
`measure_color(job, index, doc, items=None)` is a free function in `studio/compile/color.py` (`items` None samples
the kept story; raises `FileNotFoundError` without a mezzanine; `ColorMeasurement.summary()` is the one-line form),
both reused by the Director unchanged in behaviour. **Built in phase A.**

**As built (phase B):**

*The `job` argument (optional).* With a job, the planning compiles use the job's media, the voice chain is measured
on `audio.wav` and colour on `mezz.mov`. Each measurement is skipped when its file is missing. Without a job, colour
is skipped and the voice chain comes from the index's own measurements.

*Op order:* `set_brief`, `set_style`, `set_deliverables`, `set_story`, `cut_words` (fillers), `set_gap`, `pin`,
`set_framing` (at most one), `add_text` labels, `set_captions`, `edit_caption_page` (accents, then text fixes),
`set_voice_chain`, `set_color` (only on a fault), `note` (summary, at most 1,800 chars). The labels come before the
captions so the caption placer sees them.

*Decisions the ops make:*
- **Story.** A restarted fragment ("we need to b- build") is cut alone when there is a closure on both sides of it.
  Otherwise a cut-off or dropout-chopped word takes its clause with it, back to the nearest clause edge, else the
  sentence goes. The sentence start counts as an edge only when no lexical word comes before the cut-off.
- **Fillers.** The decision's fillers, every filler in the first kept sentence, sentence-initial fillers and fillers
  bounded by pauses. Each cut needs a closure on both sides.
- **Pauses.** Boundary pauses come down to the deliberate-beat window. Mid-clause hesitations are tightened from
  0.6 s, to max(400 ms, the speaker's median). A pause after a comma or colon counts as a boundary. Trims under
  100 ms are skipped. A pause is never trimmed below the speaker's median, and dropouts and
  `deliberate_beat_gap_ids` are never trimmed.
- **Punch-in.** Held to the end of the anchor's sentence (at least 1.5 s). It returns to base there or at the next
  seam.
- **Colour.** A fault means face luma under 15% or over 80%. There is no colour step without a mezzanine.
- **Brief.** `Brief.cta` holds the spoken CTA text, never an invented one. `Brief.deviations` is left empty.

*Caption size* is the largest size in `CAPTION_SIZE_LADDER` (88 down to 64 px in steps of 2) that meets all three:
- every page renders on one line at full size, and the placer's `auto` ladder shrinks nothing;
- the paging has no more one-word pages than at 64 px;
- it breaks no more phrases mid-unit than at 64 px.

At each size the pager re-pages with its floor at that size. A page the renderer would still shrink or wrap is split
where the no-shrink fitter breaks the line. The decision's emphasis words **replace** the automatic accents. Text
fixes that would overflow the line are skipped, with a note.

*Other public names:*

```python
def plan_document(job: Job, index: TakeIndex, platform: str, decision: PlanDecision | None, *,
                  settings: Settings | None = None, brief: str | None = None, style: str | None = None,
                  by: str = "code_plan", check_radio: bool = True) -> CutDocument
def radio_check(doc: CutDocument, index: TakeIndex, *, raise_on_block: bool = False) -> list[str]
```
- `plan_document` applies the op list through `EditSession` to a **fresh** document. Its parent is the job's latest
  saved version, so undo returns there, and an earlier version's pins cannot block `set_story`. It writes
  `doc/v{n}.json` and the oplog, and traces `code_plan` (platform, decision, ops, applied, rejected, version). It
  raises `CodePlanError` when the take has no words or nothing is kept, and `RadioBlocked` (with `.blockers`) when the
  radio test fails.
- `radio_check` wraps `validate.radio_blockers`.
- Constants: `CAPTION_SIZE_LADDER`, `PAID_ISOLATION_MAX_S` (60.0), `ISOLATION_BUDGET_USD` (0.17: the job's traced
  API spend + the isolation + a 3-render QA ASR reserve must stay under it), `UNMETERED_ISOLATION_MAX_S` (30.0:
  without a job to meter), `PLAN_BY` (`"code_plan"`), `ENERGY_BANDS`. `energy_band(overall)` returns `calm`,
  `conversational` or `high`. `story_speech_s(index, *, decision=None, settings=None) -> float` is the kept speech
  of the code plan's story (no pauses), the floor the pipeline checks before paying the planner.

*Timing.* Planning takes 0.3-2.3 s without a job. With a job it takes about 1-11 s, mostly `plan_voice_chain` on
`audio.wav` plus about 4 s for `measure_color` to decode a ProRes mezzanine.

*Real indexes.* Checked locally on the five real Take Indexes with no decision. Validation found 0 errors and the
radio test 0 blockers. Every Director payoff and CTA pin was kept. The kept-word Jaccard against the Director's
document was 0.835-1.000. Captions came out at 64 px on all five; they are fast talkers at 186-212 wpm. Details are
in HANDOFF §6c.

## Planner (`studio/plan/planner.py`)

```python
@dataclass
class PlanResult:
    decision: PlanDecision | None   # None: the pure-code plan is used (refusal, invalid output after one retry, no key)
    attempts: int
    rejected: list[str]             # validate_decision rejections of the first attempt, if any
    usage: dict[str, Any]           # input/output/cache tokens of every attempt
    note: str

def plan(job: Job, index: TakeIndex, digest: Digest, *, spec: ModelSpec | None = None,
         settings: Settings | None = None, brief: str | None = None, style: str | None = None) -> PlanResult
```
One `pydantic_ai.Agent(output_type=NativeOutput(PlanDecision))` call through `providers.build_model(spec,
role="planner")` with a cached prefix of ≤ 1k tokens of doctrine checklist (SKILL.md core directives + style picker,
condensed) + `PLAN_SCHEMA_JSON`, then the digest; `max_tokens` 8k (review fix; was 16k); one retry carrying the
rejection text; traced as
`model_call` with `role="planner"`, `stage="plan"`; the decision saved to `logs/plan.json`. Never escalates to
another model unless `STUDIO_PLANNER_FALLBACK` names one.

**As built (phase A):**

*The call.*
- One `Agent(output_type=NativeOutput(PlanDecision), retries=0, max_tokens=8_000)`. `openai_compat` endpoints get
  `PromptedOutput` instead. Server-side refusal fallbacks are off for role `planner` (house and BYOK).
- The retry is run by `plan()` itself. A first answer that `validate_decision` rejects, or that is not valid
  PlanDecision JSON, gets one more call on top of the first exchange, carrying every rejection line
  (`prompt.retry_prompt`).
- A refusal, a provider error or a missing key is never retried. The procedure stops there. Neither is an answer
  cut off at `max_tokens`, nor a retry whose worst case would pass the spend cap.
- `plan(..., max_usd=None)`: `spend_cap(job, max_usd)` → `SpendCap` (`max_usd`, else `DEFAULT_MAX_USD` = 0.15 or a
  lower `STUDIO_MAX_MODEL_USD`). The first call runs while the cap is not reached; the retry and the fallback only
  when spent + their worst case (prompt uncached, `MAX_TOKENS` out) stays under it.
- `MAX_ATTEMPTS` is 2 per model.

*The prefix* is the agent's instructions, cached with `anthropic_cache_instructions`:
- `prompt.DOCTRINE_CHECKLIST`, held under 1k estimated tokens by a test;
- the schema;
- the brief and style hints.

That is about 5.7k chars, about 1.6k estimated tokens. The digest is the user turn.

*`STUDIO_PLANNER_FALLBACK=<model>`* runs the same procedure (one call, one retry) on that model, with the primary's
provider and key, after the primary fails. It traces `planner_fallback`. It never runs after a missing-key failure,
and a fallback priced above the primary (or unpriced against a priced primary) is refused (`planner_fallback_refused`)
unless `STUDIO_PLANNER_FALLBACK_ALLOW_PRICIER=1`.

*Records.*
- `logs/plan.json` holds: decision (or null), attempts, rejected, `rejections_by_attempt`, errors, usage (totals plus
  one row per attempt), note, model, fallback_model, brief, style, and the prefix and digest sizes.
- The trace gets one `model_call` per attempt, `planner_retry` when the retry runs, and one `plan` summary event.
- `plan()` never raises for a model failure. `decision=None` means the caller ships the pure-code plan, and `note`
  says why.

*Verified only with scripted `FunctionModel`s.* Not yet checked against a real model: that Sonnet 5.5 and Haiku 5.5
accept the native structured output with this schema, the real token usage and effort-`low` thinking, and the
refusal and retry rates (probe item 1).

## Render profile (`studio/compile/master.py`, `studio/agent/loop.py`, `studio/media/ingest.py`)

- `encode_settings(platform, duration_s, fps, *, profile: Literal["final", "standard", "preview"] = "final")`:
  `standard` = x264 `medium`, CRF 16, the same VBV/GOP/BT.709 tags (invariant 10 unchanged).
- `render_full(job, doc, index, *, profile="final")` threads the profile to the master encode; `render.json`
  records `profile`.
- `STUDIO_REMOTION_CONCURRENCY` (default: cpu count) for the overlay render. `master(..., profile=...)` and
  `render_document(..., profile=...)` take the profile too (repair and the standard chat pass it).
- Ingest: `ingest(src, job, *, max_height: int | None = None, max_fps: Fraction | None = None)`: the standard tier
  passes `max_height=1920` (portrait) / `1080` (landscape) and `max_fps=30` so a 4K60 source becomes a 1080p30
  mezzanine (lean encoder); the pro tier passes neither. `MediaInfo` records the conform.
- **Built in phase A.**
  - `master.RENDER_PROFILES` maps each profile to its preset and CRF: `final` veryslow 15, `standard` medium 16,
    `preview` faster 20.
  - `render_full(profile="standard")` always writes the lean A-roll intermediate, never ProRes HQ. Unless
    `STUDIO_AROLL_CODEC` forces a codec, lean is the tier's budget, not a disk fallback.
  - The frame rate is divided by the smallest integer that brings it under the cap: 60 → 30, 59.94 → 29.97,
    50 → 25. Frames are dropped in a regular pattern, never resampled.
  - A reclaimed mezzanine is rebuilt at the conformed size (`storage.ensure_mezz`).
  - Not yet measured on the production host: speed, the bitrate cap, and Remotion memory at concurrency 4 (probe
    item 4).

## Repair (`studio/plan/repair.py`)

```python
@dataclass
class RepairResult:
    doc: CutDocument; render_dir: Path; passed: bool; fixed: list[str]; remaining: list[str]; rerendered: bool

def repair(job: Job, doc: CutDocument, index: TakeIndex, render_dir: Path, failures: list[dict[str, Any]], *,
           profile: str = "standard", settings: Settings | None = None) -> RepairResult
```
Code-only fixes by invariant number: 7 → re-place captions (placer ladder, then size step down) and re-render
overlay + master only on the existing `aroll.mov` + `mix.wav`; 1 → cut the fragment `describe_cutoff_at_join`
names and re-render; 8 → re-master audio + mux; 4 → restore pins. At most one re-render; a second failure returns
`passed=False` with `remaining`. Intermediates are pruned only after repair.

**As built (phase B):**

*The result.* The signature is unchanged. `RepairResult` gains three fields with defaults:
- `mode`: `none`, `full`, `overlay` or `audio`;
- `notes`: one line per decision, for the report;
- `failures`: the invariants still failing, in `InvariantResult.to_dict()` form.

`fixed` and `remaining` hold labels such as `"7 text_safe"` (`invariant_label(n)`). `REPAIRABLE` maps 1, 4, 7 and 8
to a one-line description of each fix. `failures` is taken exactly as `evaluate_render` produces it, and entries with
`passed=True` are ignored.

*The fixes:*
- **1.** `cut_words` on each cut-off fragment at a join and on each kept dropout-chopped word. The fragments come from
  `validate.cutoffs_at_joins`, `Word.truncated` and invariant 1's `cutoff_at_join` / `truncated` data. This repeats
  up to 3 rounds while a cut exposes a new fragment. A pinned fragment is never cut. Then one full render.
- **4.** `restore_words` for each pinned word missing from the story, one op per contiguous run. Then one full render.
- **7.** Each caption page named in the failure is re-placed in this order:
  1. the placer's own spot, with the page's pin cleared;
  2. the nearest clean height, trying other pages' heights first;
  3. smaller text: ×0.9, then ×0.8, then 64 px.

  Every candidate is compiled and checked with QA's own geometry, with the box widened by how far the render drew past
  the plan. Then an **overlay + master pass**: a new `renders/r{n}` hard-links the failed render's `aroll.mov`,
  mixes, stems and `audio_report.json`, recompiles the timeline, and renders only the overlays and the master. It
  falls back to one full render if those files are gone or the cut changed.
- **8.** No document change. The mix is re-mastered against the loudness offset measured on the encoded final, then
  re-muxed onto the stream-copied video, so the picture is never re-encoded. Combined with 7, it runs inside the
  overlay pass.

*What renders nothing.* These come back in `remaining` with a note, and no render is spent on them:
- invariants 2, 3, 5, 6 and 10;
- invariant 1 with nothing to cut;
- invariant 7 on a text overlay rather than a caption;
- invariant 9, unless its refs are all words being cut.

*Records.* Each call makes exactly one re-render and one `evaluate_render`. Document changes go through
`EditSession(by="repair").apply_ops` after a dry run, so a rejected fix saves no version. Each call writes a `repair`
trace event; the overlay and audio passes also write `render_done` with `repair=<mode>`.

*Tested on synthetic media (slow tests).* A caption pushed into the UI strip was fixed by a 38.5 s overlay pass; the
full first render took about 80 s. A mix 3 dB under target came back to -14.0 ±0.3 LUFS with a byte-identical video
stream.

*Required call order (pipeline).* Do not prune between the render and repair:
1. `rd = render_full(..., profile="standard")`
2. `run = evaluate_render(job, rd)`
3. If it failed: `res = repair(job, doc, index, rd, [r.to_dict() for r in run.results if not r.passed], ...)`
4. If `res.rerendered`: `prune_render(rd, keep_finals=False)`. The hard links keep the repaired render's files alive.
5. Deliver `res.render_dir` / `res.doc` as the champion pair, and put `res.notes`, `res.fixed` and `res.remaining` in
   the report.
6. If `res.passed` is False: the second attempt (the pure-code plan), else FAILED.

## ASR accounting (`studio/qa/metrics.py`, `studio/perception/fill.py`, `studio/perception/transcribe.py`, `studio/agent/cost.py`)

**Built in phase A:** `transcribe_file(..., purpose="index"|"fill"|"qa", on_event=...)`, `fill.MIN_EXCERPT_S`,
`metrics.asr_round_trip(..., job=None)`, `cost.ASR_PRICES_PER_HOUR`, `cost.asr_cost`, `CostSummary.asr_calls /
asr_cached_calls / asr_seconds`, `to_dict()["asr_usd"]`. Float WAVs that are hashed for the ASR cache are written
with scipy (libsndfile's PEAK chunk carries wall-clock time and broke the cache). Every ElevenLabs/AssemblyAI call
is traced as `{"event": "asr", "provider", "model", "audio_seconds", "purpose": "index"|"fill"|"qa",
"cached": bool}`; `cost.job_cost` prices them (Scribe $0.22/h; AssemblyAI $0.15/h; cached = $0) under role `asr`,
and the report's cost section shows the ASR line. Fill excerpts shorter than 1 s are skipped.
The prices are computed per audio second. ElevenLabs' real rounding (it says it bills "per audio minute") is
unverified until probe item 5.

## Pipeline branch (`studio/pipeline.py`)

`edit()` on tier `standard`: ingest (downscaled) → Take Index → `build_digest` → `plan` → `code_plan` → `apply_ops`
→ `validate_document` + `radio_blockers` → compile → `render_full(profile="standard")` → `evaluate_render` →
`repair` → `LoopState(champion_render, champion_doc, reviewed=False, review_note="standard tier: code QA only")`
→ report (Plan section with the decision and the digest stats, seams table, "NOT REVIEWED: standard tier") →
deliver one final (`final_nomusic.mp4` omitted when music is None). `studio edit <job>` on a standard job with
`--tier director` resumes into the Director loop from the standard champion (the first pass is never re-paid).

**Built in phase C** (`_edit_standard`, `_chat_standard`, `_resume_from_standard`; the review fixes are at the end
of this file). For repair, follow the call order in the Repair section.

Phase C added helpers to `studio/agent/loop.py`:
- `record_standard_champion(job, render_dir, doc_version, *, passed)` writes the standard champion as a finished
  `LoopState`: `reviewed=False`, `review_note=STANDARD_REVIEW_NOTE` ("standard tier: code QA only"),
  `stop_reason=STANDARD_STOP_REASON`.
- `is_standard_state(st)` recognises that state.
- `reopen_for_review(job)` is the upgrade. It keeps a complete standard champion and clears done, stop, rounds and
  review, so `ChampionLoop.run()` critiques that render instead of rendering a first pass.

For "never re-paid", the upgrade must also not run the Director's staged first pass. A resumed `director` job runs
`Director.run()` for every stage not marked done in `logs/director_state.json`, and a standard job has none marked.
The upgraded job keeps its media and Take Index, so a 4K source stays at the 1080p30 mezzanine.

## Standard chat (`studio/pipeline.py`, `studio/plan/chat.py`)

`chat()` on a standard job: one planner-model call with the instruction, `summarize_doc`, the transcript (cut view),
caption geometry text and a cached prefix of the allowed op subset (cut, captions, text, framing, audio levels;
no music/b-roll/isolation: those return a priced refusal line) → `{ops, summary}` → `apply_ops` → one retry on
rejected ops → overlay + master-only re-render when the cut is unchanged, a full standard render otherwise →
invariants → repair → deliver. Traced as `model_call` role `planner`, stage `chat`.

## Offline eval (`scripts/plan_eval.py`)

**Built in phase B** (keyless half of plan step 11). It compares the standard tier's plan with what the Director
shipped on jobs that already exist, without a model call:

```
.venv/bin/python scripts/plan_eval.py [JOB_DIR ...] [--work-dir DIR] [--json] [--no-write] [--with-media]
```

*Inputs and outputs.*
- With no jobs named, it scans `--work-dir`: `settings.work_dir` when that directory exists, else
  `/root/studio-work`. A job is picked up when it has `index/take_index.json` and a reference document.
- The reference is the highest `deliver/edit_v*.json`, sorted as numbers. A job that never delivered falls back to
  `logs/loop_state.json` → `champion_doc`, marked kind `champion`.
- It writes `<job>/logs/plan_eval.json` unless `--no-write`. `--json` prints the records instead of the table.
- `--with-media` passes `Job(root)` (never `Job.open`, so no directories are created in real jobs) to `code_plan`.
- Exit code 1 only for an unexpected exception or a record that could not be written. Disagreement is never a
  failure. A `code_plan` module that is not installed (`ModuleNotFoundError` for the module itself) gives status
  `unavailable`, and `CodePlanError` gives `failed`; neither is a crash. Any other import failure of the module (a
  SyntaxError, a broken import inside it) is a regression: it is recorded under `errors` and the run exits 1.

*Importable functions* (load the script with `importlib`):
- `discover_jobs(work_dir)`
- `find_reference(root) -> Reference` (doc, path, kind, version)
- `view_doc(doc, ix) -> DocView`
- `compare_views(ref, cand, ix)`: the comparison. It reports:
  - Jaccard over spoken and over lexical words, and the word spans only one side keeps;
  - which payoff and CTA pins are kept, and whether the in- and out-points match;
  - dropped and trimmed sentences, each with its reason;
  - estimated and compiled durations;
  - caption size and page count, emphasis overlap, and radio blockers.
- `run_code_plan(...)`
- `evaluate_decision(d, ix, ref_view)`: validity, rejections and unknown-ID rejections; story Jaccard; which drops
  agree; whether the payoff and CTA match; emphasis, punch-in, structure-marker and take-choice agreement.
- `evaluate_job(root, *, with_media=False)`
- `format_table(records)`
- `main(argv)`

The planner column reads `absent`, `no decision`, `unreadable` or `evaluated`. When `evaluated`, it compares
`logs/plan.json`'s decision and a code plan built from it.

*Not covered by `ruff check studio tests`.* The script was linted on its own and is clean.

**First run (2026-10-12, five real jobs, no decision, about 5.5 s in all):**

| Job | Reference | Jaccard | Pins kept | Same out-point | Drops ref / plan / both | Output s ref / plan | Caption px x pages ref / plan |
|---|---|---|---|---|---|---|---|
| d030 Opus | v39 delivered | 0.945 | 11/11 | no | 3 / 1 / 1 | 25.9 / 27.9 | 80x36 / 64x34 |
| d030 Fable | v15 delivered | 0.978 | 35/35 | yes | 2 / 1 / 1 | 26.9 / 27.9 | 70x34 / 64x34 |
| real-take40 | v13 champion | 1.000 | 18/18 | yes | 1 / 1 / 1 | 37.0 / 37.0 | 80x52 / 64x50 |
| var-silences | v22 champion | 0.835 | 16/16 | yes | 2 / 1 / 1 | 32.4 / 38.0 | 68x42 / 64x49 |
| 4k60 | v15 delivered | 1.000 | 11/11 | yes | 0 / 0 / 0 | 15.2 / 15.2 | 72x18 / 64x18 |

Radio blockers were 0 on both sides everywhere. The digests were 780-1,333 estimated tokens. Every disagreement is a
planner-level call that the pure-code plan does not make on its own:
- the spoken "Part one." series label (3 of 5 jobs);
- var-silences s003, which restates the hook;
- d030 Opus ending on the payoff "proof on." rather than "So flip it.".

With decisions that copy the Directors' choices, d030 Opus and take40 reach Jaccard 1.000. var-silences reaches
0.981; the gap is "Part one:" inside s001, because a decision could only drop whole sentences (fixed since: see
"Word trims and the hook guard" below). No job has a real `logs/plan.json` yet, so the planner column read `absent`
everywhere.

## Word trims and the hook guard (2026-10-12, as built)

The first real planner run on d030 dropped s001 ("Part one: Everyone says you need an authentic brand story.") as
preamble, because the ASR merged the label into the hook's sentence and the checklist said to drop a series label.
The video opened on "They're wrong." with its setup gone. Two changes:

- `trim_words` cuts words at one edge of a sentence that stays. `trim_problem` accepts a trim only inside one
  sentence, touching its first or last spoken word, with clause punctuation or a pause of at least 200 ms at the seam,
  leaving at least two spoken words, and leaving a spoken CTA intact. Trims count toward the 50% drop share and
  `kept_word_ids` leaves them out, so a payoff, CTA or must-keep range inside a trim is rejected. The code plan applies
  only trims `trim_problem` accepts and notes the rest.
- `validate_decision` rejects dropping the sentence that best matches `hook_promise` (at least half its content words,
  and two or more) unless a kept sentence also carries the hook. The rejection names the label trim to use instead.

On the same d030 index the real planner then answered `trim_words w0001-w0002 (label)` on attempt 1, and the cut opens
"Everyone says you need an authentic brand story. They're wrong."

## Review fixes (2026-10-12, as built)

A review of phases A-C found these; each has a keyless regression test.

- **Upgrade resume.** `edit(tier="director")` detects the upgrade from the job's recorded tier *or* the loop state
  (`is_standard_state(LoopState.load(job))`), so an upgrade that stopped before the hand-over (no disk, a failed
  mezzanine rebuild, Ctrl-C) is still handed to the review rounds on the retry; `reopen_for_review` clears the
  review note, so a completed hand-over is never seeded twice.
- **`--tier standard` after an upgrade** no longer re-delivers the Director's champion labelled as standard: the
  re-deliver shortcut runs only while the loop state is the standard tier's; otherwise a fresh standard plan runs
  (the recorded planner answer is reused) and its render is recorded as the standard champion.
- **Crash-resume reuse** (`_paid_answer`): only a decision the model gave, or an answer validation rejected after
  its retry (paid), is reused from `logs/plan.json`; a call that never answered (5xx, refusal, missing key) is made
  again.
- **Hard limits** (plan §2): a source over `digest.STANDARD_MAX_SOURCE_S` (600 s) is refused before ingest; a take
  whose kept speech, after the most the planner may drop, is still over `STANDARD_MAX_OUTPUT_S` (90 s) is refused
  before the planner call; a plan over 90 s is refused before rendering. Each refusal points to `--tier director`.
- **Second attempt** (the pure-code plan) runs only when a plan-dependent invariant still fails
  (`repair.PLAN_DEPENDENT` = {1, 4, 7, 8, 9}) and the pure-code document renders differently
  (`director.render_relevant`); `logs/standard.json` `second_attempt.skipped` says why it did not run.
- **Spend cap.** `edit(max_usd=)` and `chat(max_usd=)` (CLI `--max-usd` on both) reach the planner and the chat call
  (`planner.spend_cap`); default $0.15 per plan or chat.
- **Paid isolation** is priced: `cost.ISOLATION_PRICES_PER_MIN` ($0.12/min), `audio_isolation` events under role
  `isolation` in `job_cost`, `format_cost` and the report; `CostSummary.model_usd` leaves ASR and isolation out of
  every model-spend cap (the Director's loop included). `logs/standard.json` records `cost` (the summary's dict).
  The code plan buys isolation only within `ISOLATION_BUDGET_USD`.
- **Standard chat refusals** (`chat.refused_topics`) need an additive verb naming the topic and no removal verb in
  the clause ("cut the sentence about my song" is an ordinary edit for the planner); the suggested upgrade command
  carries `--max-usd 3` (`chat.UPGRADE_MAX_USD`). `chat.MAX_TOKENS` is 8k. A failed report in a chat is traced.
- **Code plan.** A speaker's restarted fragment with no closure to cut in stays in place (it is inside continuous
  speech, never at a join); the spoken CTA and the decision's payoff/CTA/must-keep sentences are never dropped by
  the cut-off rule (only the chopped word goes); a decision pin range that lost words is named in the note. A
  recording dropout between kept words is dead air (`set_gap` to its class target). A stall at a boundary comes
  down to the speaker's boundary pause (`pauses.boundary_by_energy_ms`, 0.7-1.3x the median), not the beat-window
  top. Pages a size would wrap are re-paged with the pager's dynamic program and accepted only when no part flickers
  (`min_page_s` / `punch_page_s`); `_paging_faults` also counts flicker pages. Decision accents keep
  `accent_page_share` and skip the punch-in anchor and labels. The brief's hook skips a spoken series label; its CTA
  comes from the decision's range when there is one. `decision.CTA_LEXICON` matches imperative forms only.
- **Captions.** "one" earns the number bonus only before a content word; sentence-initial list markers are never
  accented (all alike).
- **Render and ingest.** `studio render` uses the job's tier's profile; `studio index --tier standard` (or
  `STUDIO_TIER=standard`) ingests with the standard caps; a standard edit of an uncapped mezzanine says so.
- **QA ASR**: the overlay repair pass links the failed render's `qa/asr_*/asr_raw_*.json` so identical audio is
  re-parsed, not billed again.
- Not changed: Sonnet 5.5's cache-read price ($0.10 in `cost.PRICES` vs $0.20 in the bundled API reference; under
  $0.001 a video) waits for the probe's invoice.
