# Standard tier: module interfaces (binding for the build)

Companion to `standard-tier-plan.md`. These signatures are fixed so the modules can be built in parallel; change one
only by editing this file first. Everything here is Python 3.12, pydantic v2, keyless-testable (scripted models as in
`tests/director_script.py`; no real API calls). The Director/critic path is untouched and stays selectable.

## Tiers and settings (`studio/config.py`, `studio/cli.py`, `studio/pipeline.py`)

- `Settings.tier: Literal["standard", "director"]` from `STUDIO_TIER` (default **`standard`**). **Built in phase A**
  (`pipeline._resolve_tier`: flag > job meta > legacy director jobs > settings; `_edit_standard` / `_chat_standard`
  stubs raise until phase C; `studio chat --planner-model` exists, chat has no `--tier`).
- `Settings.planner_model: str` from `STUDIO_PLANNER_MODEL` (default `claude-sonnet-5-5`);
  `Settings.planner_effort: str` from `STUDIO_PLANNER_EFFORT` (default `low`).
- `providers.ROLE_EFFORT["planner"] = "low"`; `providers.house_spec("planner")` → the planner model with the house
  Anthropic key; BYOK via `spec_from_cli` works for the planner too. The planner role uses a **5m** cache TTL
  (`model_settings(spec, role="planner")`), the other roles keep `CACHE_TTL`.
- CLI: `studio edit --tier standard|director`, `--planner-model M`; `studio chat` reuses the job's tier.
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
class TakeChoice(BaseModel): cluster_id: str; sentence_id: str
class PunchIn(BaseModel): anchor_word: str; reason: str
class TextFix(BaseModel): word_id: str; text: str          # caption display text for one word (never the audio)
class StructureMarker(BaseModel): anchor_from_word: str; anchor_to_word: str; label: str   # "1/3", "Step 2"

class PlanDecision(BaseModel):
    style_primary: str; style_secondary: str | None = None
    goal: str                     # ≤ 25 words
    hook_promise: str
    drop_sentences: list[SentenceDrop] = []
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
`validate_decision` returns the rejections (empty = valid; also: a non-filler word in `cut_filler_word_ids`,
reversed ranges, a take choice outside its cluster, a chosen take that is also dropped; the 25-word goal is a
prompt instruction, not a rejection). Helpers: `kept_word_ids(d, index)`, `is_cta_sentence(text)`, `CTA_LEXICON`.
`PLAN_SCHEMA_JSON` is 2.3k chars; the models carry no `Field` constraints (grammar size). every ID exists; drops never remove more than 50% of the
spoken words; a `repeat`/`repeats_hook` drop needs a later sentence in the same cluster or a stated hook; the payoff
and CTA ranges lie inside kept words; a CTA-lexicon sentence (follow/comment/link/part two…) is never dropped unless
its reason is `repeat`; at most one punch-in; ≤ 8 emphasis words. `PLAN_SCHEMA_JSON` is the compact JSON schema
string sent to the model (≈ 2.8k chars).

## Code plan (`studio/plan/code_plan.py`)

```python
def code_plan(index: TakeIndex, *, platform: str, decision: PlanDecision | None, settings: Settings | None = None,
              brief: str | None = None, style: str | None = None) -> list[dict[str, Any]]
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
condensed) + `PLAN_SCHEMA_JSON`, then the digest; `max_tokens` 16k; one retry carrying the rejection text; traced as
`model_call` with `role="planner"`, `stage="plan"`; the decision saved to `logs/plan.json`. Never escalates to
another model unless `STUDIO_PLANNER_FALLBACK` names one.

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

## ASR accounting (`studio/qa/metrics.py`, `studio/perception/fill.py`, `studio/perception/transcribe.py`, `studio/agent/cost.py`)

**Built in phase A:** `transcribe_file(..., purpose="index"|"fill"|"qa", on_event=...)`, `fill.MIN_EXCERPT_S`,
`metrics.asr_round_trip(..., job=None)`, `cost.ASR_PRICES_PER_HOUR`, `cost.asr_cost`, `CostSummary.asr_calls /
asr_cached_calls / asr_seconds`, `to_dict()["asr_usd"]`. Float WAVs that are hashed for the ASR cache are written
with scipy (libsndfile's PEAK chunk carries wall-clock time and broke the cache). Every ElevenLabs/AssemblyAI call
is traced as `{"event": "asr", "provider", "model", "audio_seconds", "purpose": "index"|"fill"|"qa", "cached": bool}`; `cost.job_cost` prices them (Scribe $0.22/h; AssemblyAI $0.15/h; cached
= $0) under role `asr`, and the report's cost section shows the ASR line. Fill excerpts shorter than 1 s are skipped.

## Pipeline branch (`studio/pipeline.py`)

`edit()` on tier `standard`: ingest (downscaled) → Take Index → `build_digest` → `plan` → `code_plan` → `apply_ops`
→ `validate_document` + `radio_blockers` → compile → `render_full(profile="standard")` → `evaluate_render` →
`repair` → `LoopState(champion_render, champion_doc, reviewed=False, review_note="standard tier: code QA only")`
→ report (Plan section with the decision and the digest stats, seams table, "NOT REVIEWED: standard tier") →
deliver one final (`final_nomusic.mp4` omitted when music is None). `studio edit <job>` on a standard job with
`--tier director` resumes into the Director loop from the standard champion (the first pass is never re-paid).

## Standard chat (`studio/pipeline.py`, `studio/plan/chat.py`)

`chat()` on a standard job: one planner-model call with the instruction, `summarize_doc`, the transcript (cut view),
caption geometry text and a cached prefix of the allowed op subset (cut, captions, text, framing, audio levels;
no music/b-roll/isolation: those return a priced refusal line) → `{ops, summary}` → `apply_ops` → one retry on
rejected ops → overlay + master-only re-render when the cut is unchanged, a full standard render otherwise →
invariants → repair → deliver. Traced as `model_call` role `planner`, stage `chat`.
