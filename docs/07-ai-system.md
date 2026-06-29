# 07 · AI System Architecture

> **Scope.** This document specifies Marque's intelligence layer: the LLM services, how they route between Claude models, the prompt and memory architecture, structured outputs, guardrails and moderation, the eval/quality gates, and the latency / cost / fallback machinery. It is implementation-grade — engineers should be able to build the FastAPI orchestration service and its adapters from this alone.
>
> **What lives elsewhere.** Video mechanics (AssemblyAI transcription, Shotstack rendering, MCP `personal_clipper` / `reframe`, Cloudflare R2 + Stream, Ayrshare publishing) belong to the Clip Pipeline section (`09-video-pipeline.md`) — here we cover only the *orchestration reasoning* the Clip Engine adapter performs. The Brand Graph table shape is owned by `12-backend-data-security.md` (this layer consumes it as cached context). Subscription tiers and quota enforcement are owned by `11-monetization.md`. The aesthetic doctrine — one idea per screen, cream surfaces, gold accent, quiet declarative copy — is owned by `02-design-system.md`; this document inherits it for every AI-authored string and every streaming-UX moment.

---

## 1. Design principles

Five principles govern every decision below.

1. **Every vendor sits behind an adapter.** Claude is reached only through an internal `LLMRouter`; the clip toolchain only through a `ClipEngine` adapter; publishing only through a `Publisher` adapter; analytics only through an `Insights` adapter. Swapping a model or a vendor is a one-file change. No call site names a model string directly.
2. **Cheapest tier that clears the bar.** Default to Claude Haiku 4.5 for high-volume, narrow work; promote to Claude Opus 4.8 only where quality is user-visible and *compounding* — the Brand Graph, scripts, hooks, teardowns. This is Anthropic's stated agent-design heuristic ([Anthropic — advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
3. **Context is the moat.** The Brand Graph + voice exemplars + format-recipe library are the per-creator cached prefix, written once per session and read by every downstream call. The differentiator is not the model — it is what we put in front of it, deterministically and cheaply.
4. **Generated content is published content.** Marque writes scripts that get posted to Instagram and TikTok under the creator's name. Output moderation is a first-class pipeline stage, not an afterthought. Nothing auto-publishes without explicit creator approval.
5. **Calm under the hood, calm on the surface.** Long jobs are durable and never block a screen. Interactive calls stream. Refusals and failures degrade into a "needs your edit" path, never a crash — consistent with the anti-clutter doctrine that the creator should only ever see one clear next action.

---

## 2. Service map

Marque's intelligence is six logical services, each a module in the FastAPI orchestration layer. All share the `LLMRouter`, the prompt-cache prefix builder, and the usage meter.

```
                          ┌───────────────────────────────────────────┐
                          │           FastAPI orchestration            │
                          │                                            │
  Brand/page scrape ─────▶│  ① Brand Analyzer ──▶ Brand Graph (cached) │
                          │                                            │
  Creator corpus ────────▶│  ② Voice Engine  ──▶ voice profile + checks│
                          │                                            │
  "make this week" ──────▶│  ③ Script Studio ──▶ ScriptCandidate[]     │
                          │        └ Hook Lab (nested)                 │
                          │                                            │
  candidate scripts ─────▶│  ④ Virality Engine ─▶ score + teardown     │
                          │                                            │
  approved batch ────────▶│  ⑤ Clip Engine orch ─▶ render-recipe plan ─┼──▶ Clip Pipeline (08)
                          │                                            │
  pulled-back metrics ───▶│  ⑥ Coach ──────────▶ one directive + card  │
                          └───────────────┬────────────────────────────┘
                                          │  every call
                                ┌─────────▼──────────┐
                                │     LLMRouter      │  (model choice, caching,
                                │  Opus 4.8 / Haiku  │   streaming, retries,
                                │       4.5          │   usage metering)
                                └────────────────────┘
```

### 2.1 Brand Analyzer — builds the Brand Graph

**Job.** On creator onboarding (and on demand thereafter), read the scraped existing page — captions, comments, bio, pinned posts, visual style notes — and distill a structured **Brand Graph**: niche, audience, content pillars, voice axes, do/don't list, recurring hooks, and competitor set. This is the context layer the whole product compounds on.

| Aspect | Value |
|---|---|
| Trigger | Onboarding; manual "refresh my brand"; quarterly auto-refresh |
| Model | **Claude Opus 4.8** (`claude-opus-4-8`) |
| Output | `BrandGraph` Pydantic model via structured outputs (§5) |
| Frequency | Rare (once per creator per refresh) — cost is amortized across every downstream call |
| Latency budget | Up to ~90s; runs as a durable job, surfaced via Supabase Realtime |

**Rationale.** One-time-per-creator, maximally high-leverage. Every Script Studio, Hook Lab, and Coach call reads this output, so quality here compounds. The cost of one Opus pass is trivial against the hundreds of downstream Haiku/Opus calls it improves.

### 2.2 Voice Engine — synthesizes and polices the creator's voice

Two distinct sub-jobs with **different model tiers**:

| Sub-job | Model | Why |
|---|---|---|
| **Voice profile synthesis** — distill verbatim few-shot exemplars + voice axes (formality, energy, sentence length, signature phrases, emoji policy) from the creator corpus | **Opus 4.8** | Infrequent; sets the standard the cheap checks measure against |
| **Voice checks** — score a candidate line/script against the profile; in-voice / out-of-voice + one-line reason | **Haiku 4.5** | High-volume, narrow, latency-sensitive; 5× cheaper input, runs on every candidate |

The synthesized profile (exemplars + axes) lives **inside the cached prefix** so every check and every generation reads the same source of truth.

### 2.3 Script Studio — viral scripts in the creator's voice

**Job.** Given a chosen **format recipe** (split-screen, 3-up talking heads, myth-buster, listicle, POV, before/after, etc.) and a topic/trend, generate N script candidates that (a) sound like the creator, (b) obey the recipe's structure, and (c) front-load a strong hook.

| Aspect | Value |
|---|---|
| Model | **Claude Opus 4.8** for generation |
| Pattern | Generate-N-then-judge (§6), N = 3 candidates per recipe |
| Output | `ScriptCandidate[]` via structured outputs |
| Latency | Interactive — streamed via SSE; pre-warmed cache (§7) |

**Rationale.** This is the core product-quality surface. Creativity + voice fidelity + structural adherence to a render-recipe is exactly what justifies Opus.

#### Hook Lab (nested in the script reader)

Per the Section-8 doctrine, Hook Lab is **progressive disclosure inside the script reader**, not a separate feature. When the creator taps the hook line, Marque offers variant hooks.

| Step | Model | Why |
|---|---|---|
| Generate hook variants (5) | **Opus 4.8** | The hook is the highest-leverage 3 seconds of any clip |
| Rank the variants | **Haiku 4.5** | Ranking against a rubric is cheap, narrow work |

### 2.4 Virality Engine — predict, score, explain

**Job.** Score a script/clip for predicted virality, hook strength, and retention risk, and write the *explanation* a creator can act on.

| Sub-job | Engine | Model |
|---|---|---|
| Numeric prediction (virality %, retention curve, hook score) | MCP `virality_predictor` (behind `ClipEngine`) | — (not Claude) |
| Teardown *narrative* — why it'll land, what to tighten | **Opus 4.8** | Declarative, philosophical copy in the creator's voice |
| Bulk tagging/classification (topic, format, sentiment) | Claude | **Haiku 4.5** |

**Rationale.** The MCP tool does the number; Claude does the *meaning*. Splitting the two keeps the expensive model off the high-volume classification path.

### 2.5 Clip Engine orchestrator — render-recipe planning

**Job.** This service does the *reasoning* around clip production; the mechanics live in `09-video-pipeline.md`. Given a recorded batch take + chosen recipes, it decides: which format recipe maps to which moment, what moments to extract (prompts handed to AssemblyAI moment detection), and the render-recipe parameters (split-screen layout, caption style, B-roll cue points, overlay timing) handed to Shotstack.

| Aspect | Value |
|---|---|
| Model | **Opus 4.8** (planning only) |
| Output | `RenderRecipePlan` — a structured object the Clip Pipeline executes |
| Downstream | AssemblyAI (transcription/moments), MCP `personal_clipper` / `reframe`, Shotstack (render), R2 + Stream (storage), Ayrshare (publish) — all in `08` |

**Rationale.** Orchestration reasoning — mapping creative intent onto a deterministic render graph — is high-stakes and benefits from Opus's planning. The actual media work is non-LLM.

### 2.6 Coach — one directive, one teardown card

**Job.** Read pulled-back analytics (via the `Insights` adapter — Phyllo/Ayrshare) and produce, in the creator's voice, **one** directive for the Today screen plus an archived teardown card in the Coach feed.

| Sub-job | Model | Why |
|---|---|---|
| Weekly teardown narrative + the single Today directive | **Opus 4.8** | The philosophical, declarative copy the aesthetic demands; "one idea, said well" |
| Routine metric summarization (rolling averages, deltas) | **Haiku 4.5** | Cheap, repetitive number-crunching narration |

**Rationale.** The Today screen shows exactly one directive at a time (anti-clutter doctrine). That single sentence is load-bearing — it gets Opus. The supporting metric prose does not.

### 2.7 Routing summary table (the load-bearing reference)

| Service / call | Model | Pattern | Rationale |
|---|---|---|---|
| Brand Analyzer | `claude-opus-4-8` | Structured output | One-time, compounding leverage |
| Voice profile synthesis | `claude-opus-4-8` | Structured output | Sets the bar the cheap checks measure |
| Voice check (per candidate) | `claude-haiku-4-5` | Binary + reason | High-volume, latency-sensitive, 5× cheaper |
| Script Studio (generate) | `claude-opus-4-8` | Generate-N-then-judge | Core quality surface |
| Hook Lab (generate) | `claude-opus-4-8` | Generate-N | Highest-leverage 3 seconds |
| Hook Lab (rank) | `claude-haiku-4-5` | Rubric rank | Cheap selection |
| Virality teardown narrative | `claude-opus-4-8` | Structured output | Actionable explanation copy |
| Virality bulk tagging | `claude-haiku-4-5` | Classification | High-volume |
| Clip Engine orchestrator | `claude-opus-4-8` | Structured plan | Orchestration reasoning |
| Coach weekly teardown + Today directive | `claude-opus-4-8` | Structured output | Declarative, voice-matched copy |
| Coach routine metric summary | `claude-haiku-4-5` | Summarization | Repetitive number narration |
| Publish-gate moderation (§5) | `claude-haiku-4-5` | Structured rubric | Runs on 100% of outputs; must be cheap |

> **Canonical model facts** (verified against Anthropic's `claude-api` skill, cache date 2026-06-04 — authoritative over web docs):
>
> | Model | Exact ID | Input $/1M | Output $/1M | Context | Max output |
> |---|---|---|---|---|---|
> | Claude Opus 4.8 | `claude-opus-4-8` | $5.00 | $25.00 | 1M | 128K |
> | Claude Haiku 4.5 | `claude-haiku-4-5` | $1.00 | $5.00 | 200K | 64K |
>
> **Do not append date suffixes** — the bare strings are complete. Routing is strictly two-tier; a middle tier (Sonnet 4.6) is an open question (§11), not in the current design.

---

## 3. The `LLMRouter` adapter

Every Claude call goes through one module. This is where model choice, caching, streaming, retries, refusal handling, and usage metering are centralized.

### 3.1 Responsibilities

- **Model resolution.** Maps a logical *task name* (e.g. `script.generate`, `voice.check`) to a concrete model ID via config. No call site hard-codes `claude-opus-4-8`.
- **Thinking config.** Opus 4.8 uses **adaptive thinking only** — `thinking={"type": "adaptive"}`. The legacy `{"type": "enabled", "budget_tokens": N}` returns **HTTP 400** on Opus 4.8 and must never be sent. Depth is controlled via `output_config={"effort": ...}` (`low|medium|high|xhigh|max`, default `high`). Haiku 4.5 does **not** accept the effort parameter — Haiku calls stay simple (no `thinking`, no `effort`).
- **Streaming policy.** Any call whose `max_tokens > ~16K`, or any Opus call that may produce long output, **must** stream (`.stream()` + `.get_final_message()`) to avoid SDK HTTP timeouts. Interactive services (Script Studio, Hook Lab, Coach chat) always stream to the client.
- **Prompt-cache assembly.** Builds the `tools → system → messages` prefix with `cache_control` breakpoints (§4).
- **Structured-output enforcement.** Attaches `output_config.format` / `strict: true` and validates against the Pydantic schema (§5).
- **Refusal + error handling.** Inspects `stop_reason` before reading content; routes refusals to the human-edit UX; retries 429/5xx with SDK backoff (§8).
- **Usage metering.** Records `usage.input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens` per call, keyed by creator, into Postgres (§8).

### 3.2 Recommended call shape (Python, Opus)

```python
# Interactive Opus generation — always streamed.
with client.messages.stream(
    model="claude-opus-4-8",
    max_tokens=8000,
    thinking={"type": "adaptive"},          # adaptive ONLY on Opus 4.8
    output_config={
        "effort": "high",                   # default; bump to xhigh for the hardest planning
        "format": {"type": "json_schema", "schema": SCRIPT_CANDIDATES_SCHEMA},
    },
    system=cached_prefix,                    # see §4 — Brand Graph + recipes + exemplars
    messages=[{"role": "user", "content": request_block}],
) as stream:
    msg = stream.get_final_message()

if msg.stop_reason == "refusal":            # ALWAYS check before reading content
    return needs_creator_edit(msg.stop_details)
```

```python
# High-volume Haiku check — simple, no thinking/effort, structured binary output.
resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=256,
    system=cached_prefix,                    # same prefix → cache hit
    messages=[{"role": "user", "content": voice_check_request}],
    output_config={"format": {"type": "json_schema", "schema": VOICE_SCORE_SCHEMA}},
)
```

> Structured outputs require the beta header `anthropic-beta: structured-outputs-2025-11-13` (the SDK sets this when you pass `output_config.format`; on raw HTTP, send it explicitly).

---

## 4. Prompt architecture + caching

Caching is where Marque's per-creator economics are won or lost. Because the same Brand Graph + recipe library + voice exemplars feed every call in a session, the cached prefix can carry ~90% of the cost savings.

### 4.1 The prefix-match invariant

Caching is a **prefix match**. Render order is `tools → system → messages`. Any byte change anywhere in the prefix invalidates everything after it ([Prompt caching — Claude docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). The discipline: **stable content first, volatile content last.**

| Position | Content | Stability |
|---|---|---|
| 1 (system, cached) | Frozen system prompt (role, publish-safety block, format doctrine) | Never changes |
| 2 (system, cached) | The creator's **Brand Graph** (serialized deterministically) | Per-creator, per-session |
| 3 (system, cached) | **Format-recipe library** (the structured render-recipes) | Versioned, rarely changes |
| 4 (system, cached) | **Verbatim voice exemplars** + voice axes | Per-creator |
| 5 (messages, uncached) | Retrieved blocks (post history, trend matches, prior teardowns) | Per-request |
| 6 (messages, uncached) | The specific request (topic, recipe choice, timestamps, IDs) | Per-request |

### 4.2 Caching parameters

- `cache_control={"type": "ephemeral"}` → 5-minute TTL; `{"type": "ephemeral", "ttl": "1h"}` → 1-hour TTL.
- **Max 4 breakpoints** per request. Place them at the ends of blocks 1–4 above (or a top-level auto-cache `cache_control` on `messages.create()` to cache the last cacheable block).
- **Minimum cacheable prefix — Marque-critical:** **Opus 4.8 and Haiku 4.5 both require 4096 tokens.** A 3K-token prefix silently won't cache (`cache_creation_input_tokens: 0`, no error). Design the prefix (system + Brand Graph + recipe library + exemplars) to clear 4096 tokens — for a real creator this is easily met.
- **Economics:** cache **read** ≈ 0.1× input price; cache **write** = 1.25× (5-min) or 2.0× (1-hour). Break-even is 2 requests at 5-min TTL, ≥3 at 1-hour.

### 4.3 Marque caching strategy

- During an **active batch-generation session** (bursty — many Script Studio + Hook Lab + Coach reads), use the **1-hour TTL** so the prefix survives the whole session. Otherwise 5-minute.
- **Pre-warm the cache** at the start of a generation session with a `max_tokens: 0` request, so the first real request doesn't pay cold-write latency. Place the warm breakpoint on the last *shared* block (the exemplars), not on a placeholder message.
- **Verify hits** via `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens`. If reads stay 0 across identical-prefix calls, a silent invalidator is in the prefix — audit for `datetime.now()`, UUIDs, unsorted `json.dumps()` (always `sort_keys=True`), or a varying tool set.
- **Don't change tools or model mid-session** — both invalidate the entire cache. Don't interpolate the date/creator-ID into the frozen system prompt.

### 4.4 System-prompt design

The frozen system prompt (block 1) has four parts, in this order so nothing volatile precedes the cached span:

1. **Role + product framing** — "You write short-form scripts for [creator] in their exact voice." Calm, declarative instruction tone (matches §2's copy doctrine).
2. **Format doctrine** — how render-recipes constrain structure (a myth-buster has a claim→bust→proof arc; a listicle has N beats; etc.).
3. **Publish-safety block** (§5.3) — the non-negotiable content rules every generation must obey.
4. **Output contract** — points at the structured schema; "respond directly, no preamble."

### 4.5 Operator channel — defense against prompt injection

Marque ingests **untrusted scraped captions and comments**. Those must never be able to issue instructions. Two defenses:

- Treat all scraped text strictly as **data**, never as instructions. Wrap it in a clearly delimited block and tell the model so in the system prompt.
- For mid-conversation operator instructions (mode switches, injected state), use the **`{"role": "system", ...}` message channel** appended to `messages[]` (Opus 4.8, no beta header) rather than editing the top-level system prompt. This preserves the cached prefix *and* is the prompt-injection-safe operator channel — operator authority cannot be spoofed by scraped user content the way a `<system-reminder>` buried in a user turn can.

---

## 5. Structured outputs + tool use

Marque relies on grammar-constrained structured outputs so that the orchestration layer never parses freeform text. **Pydantic models are the single schema source** for every structured artifact.

### 5.1 The two independent levers

- **`output_config={"format": {"type": "json_schema", "schema": {...}}}`** constrains the *response* to valid JSON matching the schema. This is grammar-constrained decoding — *the model literally cannot emit tokens that violate the schema*, so there are no `json.loads()` retries ([Structured outputs — Claude docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).
- **`strict: true`** on a **tool definition** (sibling of `name`/`description`/`input_schema`, **not** on `tool_choice`) guarantees `tool_use.input` validates exactly. Used when we must guarantee one specific structured call.

Both require the beta header `anthropic-beta: structured-outputs-2025-11-13`.

### 5.2 Schema rules + ergonomics

- Every object must set `additionalProperties: false` and list `required` for every field.
- **Not supported in-schema:** recursive schemas, numeric constraints (`minimum`/`maximum`), string-length constraints. The Python SDK strips these and validates them client-side — so keep them in the Pydantic model for client validation but don't rely on the model enforcing them.
- **Schema-compile cost:** the first request per schema pays a one-time compile cost, cached 24h. **Keep schemas stable** (don't regenerate the JSON Schema per request) so we reuse the compiled grammar.
- **Incompatible with citations** (400) and with prefilling.
- Use `client.messages.parse()` with the Pydantic model to get a typed object back.
- To force a specific structured call, `tool_choice={"type": "tool", "name": "..."}`.

### 5.3 The core Pydantic schemas

These are the contracts the orchestration layer is built on. (Field shapes are illustrative; exact persisted columns are owned by `12-backend-data-security.md`.)

```python
class VoiceAxis(BaseModel):
    name: str                  # e.g. "formality", "energy"
    value: float               # 0.0–1.0 (validated client-side)
    evidence: str              # one verbatim exemplar line

class BrandGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    niche: str
    audience: str
    pillars: list[str]
    voice_axes: list[VoiceAxis]
    signature_phrases: list[str]
    dos: list[str]
    donts: list[str]
    competitors: list[str]
    exemplars: list[str]       # verbatim few-shot voice samples

class FormatRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str                  # "split_screen" | "myth_buster" | "listicle" | ...
    beats: list[str]           # structural arc the script must follow
    render_hints: list[str]    # cues the Clip Engine maps to Shotstack

class ScriptCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_slug: str
    hook: str                  # the load-bearing first 3 seconds
    beats: list[str]           # one line per recipe beat
    full_script: str
    estimated_duration_s: int

class VoiceScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    in_voice: bool
    reason: str                # one line

class PublishGate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defamation: bool
    ip_infringement: bool
    unverifiable_health_claim: bool
    unverifiable_financial_claim: bool
    ftc_disclosure_needed: bool
    impersonation: bool
    reason: str

class TeardownCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directive: str             # the single Today-screen sentence
    why: str
    one_change: str

class RenderRecipePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clips: list["ClipPlan"]    # moment → recipe → render params
```

---

## 6. Retrieval + memory injection

The Brand Graph is the persistent context layer. Marque mixes **cache-augmented generation** (precompute + cache the small, hot context) with **true RAG** (vector search) for the unbounded corpora.

### 6.1 What goes where

| Corpus | Size | Strategy |
|---|---|---|
| Brand Graph + voice exemplars + recipe library | Small, hot | **Inject in full** into the cached prefix (cache-augmented generation). Precomputed once per session; read by every call. |
| Full post history | Unbounded | **RAG** — `pgvector` top-k over `vector(1024)` (§6.3), injected *after* the cached prefix |
| Trend archive | Unbounded | **RAG** — top-k matches for the chosen topic |
| Prior-performance teardowns | Grows over time | **RAG** — retrieve relevant prior lessons |

**Rationale.** Long-context wins on quality when resources are ample, but **RAG is far more cost- and latency-efficient** ([RAG vs long-context — Meilisearch](https://www.meilisearch.com/blog/rag-vs-long-context-llms)). The Brand Graph is small enough to cache in full and re-read cheaply; the unbounded corpora are retrieved top-k so we never re-send a huge corpus per call. KV-cache cost scales linearly with prompt length — another reason to keep the cached prefix tight ([Context engineering for production agents](https://medium.com/@kuldeep.paul08/context-engineering-optimizing-llm-memory-for-production-ai-agents-6a7c9165a431)).

### 6.2 Memory layer

- Durable creator memory lives in **Supabase Postgres** (the locked store) with **`pgvector`** for embeddings.
- Each injected unit (a Brand Graph entry, a retrieved exemplar, a prior teardown) is a discrete **context block**.
- The orchestration service assembles: `prefix = system + brand_graph + recipe_library + exemplars` (cached) then appends `retrieved_blocks + request` (uncached).
- Every vector write and query goes through one module — the **`Embedder` adapter** (§6.3) — so the model and dimension are pinned in exactly one place and a vendor swap is a one-file change, mirroring the `LLMRouter`/`ClipEngine`/`Publisher` discipline of §1.

### 6.3 The `Embedder` adapter — canonical embeddings decision

The locked stack ships an LLM (Claude) but **no embeddings model** — the Anthropic API has no embeddings endpoint. RAG/exemplar-recall therefore needs a third-party embedder, and the choice is **load-bearing across documents**: `06-brand-graph.md` declares `vector(N)` columns, HNSW indexes, and the `match_brand_facts` RPC, and embeddings produced by different models are not comparable, so the dimension is baked into the schema and **cannot be changed after the first migration without a full re-embed and index rebuild**. This section is the single owner of that decision; `06-brand-graph.md` consumes the dimension fixed here. Treat the two as one decision with two write sites.

**Canonical decision (owned jointly by `06` and `07`).**

| Aspect | Value |
|---|---|
| Provider | **Voyage AI** — Anthropic's recommended embeddings partner; the natural pairing for a Claude-centric stack ([Anthropic — Embeddings](https://platform.claude.com/docs/en/build-with-claude/embeddings)) |
| Model | **`voyage-3.5`** (general-purpose retrieval; strong quality-per-dollar) ([Voyage embeddings](https://docs.voyageai.com/docs/embeddings)) |
| **Dimension** | **`1024`** — the canonical value. `voyage-3.5` supports Matryoshka output dims (256/512/1024/2048); 1024 is the deliberate cost/recall midpoint, **not** a default. |
| Distance | **cosine** (`vector_cosine_ops`), normalized vectors |
| Input type | `input_type="document"` on write, `input_type="query"` on recall (asymmetric retrieval) |
| Where it runs | Server-side only, in the FastAPI orchestration layer. The Voyage key never reaches the iOS client. |

> **Schema implication — apply before the first migration is authored.** `06-brand-graph.md` must set every embedding column and index to **`vector(1024)`**: `brand_facts.embedding`, the voice-fingerprint embedding column, the `idx_bf_embedding` HNSW index, and the `match_brand_facts(query_embedding vector(1024), …)` RPC signature. The placeholder `vector(1536)` (OpenAI-shaped) in `06` is superseded by this decision; shipping the migration at 1536 against 1024-dim vectors is the exact "wrong dimension before first migration" failure `06` Open Q #1 warns about. `06` Open Q #1 is hereby **resolved by this section** and should reference it rather than restating the open choice.

**Why a single owner.** Both docs previously flagged this gap independently without converging — `06` left `vector(1536)` "TBD pending Open Q #1," and `07` deferred to an "unspecified `Embedder` adapter." That split is the bug: the migration that creates the vector columns was blocked on a decision nobody owned jointly. Pinning provider + dimension here, and having `06` read 1024 from here, unblocks it.

**Adapter contract.** Every call site names the adapter, never the vendor:

```python
class Embedder(Protocol):
    DIM: int = 1024                       # MUST equal the vector(N) in 06-brand-graph.md
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

- The adapter is the **only** code that constructs Voyage requests and the **only** place `DIM`/model strings appear. A guard at startup asserts `Embedder.DIM == <introspected vector column dimension>` so a model/dimension drift fails loudly at boot, not silently at query time.
- Recall flows through `match_brand_facts` (the `06` RPC), called via Supabase `.rpc()`; PostgREST can't invoke pgvector operators directly. The adapter produces the `query_embedding`; `06` owns the SQL.
- **Vendor swap** (e.g. to `gte-small`/384 self-hosted, or OpenAI `text-embedding-3-*`) = change the adapter's model/`DIM` **and** run a re-embed migration (rebuild every vector, rebuild the HNSW index, alter the column and RPC dimension). Because comparability breaks across models, this is always a backfill, never a hot swap — which is exactly why the choice is pinned now.

**If RAG is deferred for v1.** RAG is **not** deferred — the Coach (`05`), Script Studio voice exemplars, and trend recall all depend on `match_brand_facts`, so the vector columns ship in the first migration at `vector(1024)`. Were RAG ever cut from v1, the correct move would be to **omit the vector columns and the RPC from the first migration entirely** (add them later in a dedicated embeddings migration once the model is confirmed), rather than ship `vector(1536)` placeholder columns that would have to be dropped and rebuilt. Do not author a migration with a provisional dimension.

---

## 7. Guardrails + content moderation

**This is the highest-liability area of the product.** Marque generates content *and publishes it* to Instagram and TikTok under the creator's name — exposing defamation, IP-infringement, harmful/medical/financial-claim, and impersonation risk. There are two moderation surfaces, both required.

### 7.1 Input moderation (untrusted ingestion)

Marque scrapes pages, captions, and comments — untrusted text that could carry prompt-injection payloads. Mitigation:

- Treat all scraped text as **data, never instructions** (delimited block; system prompt says so).
- Use the `role: "system"` operator channel (§4.5) so operator authority can't be spoofed by scraped content.

### 7.2 Anthropic's built-in layer

All Claude models are trained honest/helpful/harmless and refuse AUP-violating requests regardless of prompt; the API also ships **free real-time Safety Filters / classifiers** as a backstop ([Building safeguards for Claude](https://www.anthropic.com/news/building-safeguards-for-claude); [Content moderation — Claude docs](https://platform.claude.com/docs/en/about-claude/use-case-guides/content-moderation)).

**Design for refusals as a normal path.** The AUP classifier has been reported as occasionally over-aggressive on benign creator-adjacent content (supplements, finance, security) ([The Register, 2026](https://www.theregister.com/software/2026/04/23/claude_opus_47_has_turned/)). So a refusal is not an error state — it's a UX branch.

**Refusal handling (mandatory).** A refused request returns **HTTP 200 with `stop_reason: "refusal"`** plus a `stop_details.category`. The `LLMRouter` **must check `stop_reason` before reading `content[0]`** (refusals carry empty/partial content). On refusal, route to a **"this script needs your edit"** path — the creator gets a calm prompt to adjust, not a crash. (`stop_details` is `null` for every non-refusal `stop_reason`, so guard before reading `.category`.)

### 7.3 The Marque guardrail pipeline

A four-stage pipeline runs on every script before it can be scheduled:

1. **Generation-time publish-safety block.** The system prompt's publish-safety section (§4.4) forbids: defamatory claims about named people/brands; unverifiable health/financial/legal claims; third-party IP (lyrics, trademarks, copyrighted scripts); missing FTC disclosure on sponsored/affiliate content; impersonation.
2. **Dedicated Haiku 4.5 publish-gate classifier.** Every final script is scored across categories (`defamation / ip_infringement / unverifiable_health_claim / unverifiable_financial_claim / ftc_disclosure_needed / impersonation`) using the `PublishGate` structured schema + a binary rubric. Cheap enough to run on **100% of outputs** before scheduling.
3. **Anthropic Safety Filters** — the free API backstop.
4. **Human-in-the-loop.** Nothing auto-publishes without the creator's **explicit approval** — which also satisfies Instagram Graph and TikTok Content Posting API content policies (the downstream constraint that justifies the whole gate; Ayrshare sits in front of both — see `09-video-pipeline.md`).

### 7.4 States

| State | Behavior |
|---|---|
| Clean | Script proceeds to the approval screen |
| Flagged (soft) | Surfaced to the creator with the flagged category + a one-line reason; creator can edit and re-gate, or override (pending §11.1 policy) |
| Refusal (`stop_reason: "refusal"`) | "Needs your edit" path; never a crash |
| Gate model 429/overload | Fail *closed* on the gate (don't let an un-gated script through); retry with backoff; surface "checking…" calmly |

---

## 8. Evals + quality gates

Quality is enforced with concrete, citable patterns, not vibes.

### 8.1 Generate-N-then-judge

Generate K candidates, judge, pick the winner (or majority-vote across judgments). Reduces outlier risk at K× generation cost ([LLM-as-a-judge — Agenta](https://agenta.ai/blog/llm-as-a-judge-guide-to-llm-evaluation-best-practices)).

| Surface | K | Judge model |
|---|---|---|
| Script Studio | 3 candidates per recipe | Opus 4.8 (final quality) |
| Hook Lab | 5 hook variants | Haiku 4.5 (rank) |

**Use the cheaper model as judge where viable, the stronger where it matters.** Haiku 4.5 judges bulk voice-match; Opus 4.8 judges final virality/hook quality.

### 8.2 Rubric design rules

- Prefer **binary or 3-point** scales (Excellent / Acceptable / Poor). Avoid 10/100-point scales without anchored examples — they're noisy.
- **Evaluate one criterion per call** — score voice-match, hook-strength, format-adherence, and publish-safety in **separate** calls, never one blended score ("criterion conflation").
- **Mitigate known judge biases** ([5 biases that kill LLM evals — S. Sigl](https://www.sebastiansigl.com/blog/llm-judge-biases-and-how-to-fix-them/)):
  - *Position bias* (10–15pt winrate swing) → randomize candidate order / balance-permute rubric options.
  - *Verbosity bias* → state explicitly that length ≠ quality.
  - *Self-enhancement bias* → hide which model produced which candidate from the judge.

### 8.3 Self-critique gate + fresh-context verifier

Have the generator critique its own draft against the brand voice + publish-safety rubric before surfacing it — self-reference improves judgment ([Do Before You Judge — arXiv 2509.19880](https://arxiv.org/pdf/2509.19880)). Then pair it with a **separate fresh-context judge** for the final gate; fresh-context verifiers outperform pure self-critique.

### 8.4 Voice-match scoring (product metric)

Judge each candidate against the verbatim few-shot exemplars in the Brand Graph: binary `in_voice` + one-line reason (`VoiceScore`). Track a **rolling voice-match rate per creator** as a first-class product metric (surfaced internally; informs whether a Brand Graph refresh is due).

### 8.5 Offline eval harness

Maintain a golden set per format recipe (known-good scripts + known-bad). On every prompt or schema change, re-run the harness and gate the deploy on no regression in voice-match rate and publish-gate precision/recall. Wire results into PostHog for trend tracking.

---

## 9. Latency + streaming UX

Two latency regimes, designed separately — and both serving the calm aesthetic (the creator never stares at a spinner; long work happens out of sight).

### 9.1 Interactive (Script Studio, Hook Lab, Coach chat)

- **Stream via SSE.** Claude TTFT is typically <~800ms; stream tokens so output appears immediately ([Streaming messages — Claude docs](https://platform.claude.com/docs/en/build-with-claude/streaming)).
- With tool use, begin executing tool #1 the moment its `tool_use` block completes while later blocks still stream — nearly halves perceived latency on multi-tool turns.
- For 128K Opus outputs, **streaming is required** (timeout avoidance), not optional.
- **Aesthetic mapping:** stream into a single calm reader; slow eased reveal; one script at a time. No progress-bar clutter.

### 9.2 Long-running (the HERO batch loop)

The film-once → post-all-week loop is minutes-long and multi-vendor (AssemblyAI → MCP clipper → Shotstack → R2/Stream → Ayrshare). **Do NOT hold an HTTP request open.**

- Run it as a **durable job on Trigger.dev** (the locked orchestrator), persist progress, and push status to the SwiftUI client via **Supabase Realtime** (or APNs for completion — "your clips are ready").
- Durable-execution pattern: the client reconnects at the last-acknowledged offset; no duplicate work on crash ([AI streaming → durable sessions — WebSocket.org](https://websocket.org/guides/use-cases/ai-streaming/)).
- **Mapping:** FastAPI streams interactive SSE; Trigger.dev owns the durable pipeline; Realtime/APNs deliver completion. The creator sees a calm "we're cutting your week" state, then a notification — never a blocked screen.

### 9.3 States (every AI surface)

| State | Treatment |
|---|---|
| Loading | Stream tokens (interactive) or calm progress copy + Realtime updates (batch); never a bare spinner |
| Empty | Quiet prompt copy ("What do you want to be known for?") — no skeleton clutter |
| Error (5xx/429) | SDK auto-retry with backoff; if exhausted, calm "try again" + preserve the creator's input |
| Offline | Queue the request; SwiftUI shows "saved — will run when you're back" |
| Permission-denied (analytics) | Coach degrades to last-known directive; prompts to reconnect the social account |
| Refusal | "Needs your edit" path (§7.2) |

---

## 10. Cost controls + per-user usage metering + fallbacks

### 10.1 Three cost levers (all on the cheap side)

1. **Route bulk work to Haiku 4.5** — voice checks, classification, ranking, metric summaries, the publish gate.
2. **Prompt-cache the per-creator prefix** — ≥90% savings on the cached span (mind the 4096-token Opus minimum, §4.2).
3. **Batch API for non-latency-sensitive work** — bulk classification and overnight teardown generation at **50% of standard price** (`messages.batches.*`). Results are unordered — key by `custom_id`; up to 100K requests/batch, ≤24h, results retained 29 days.

### 10.2 Token metering

- Pre-estimate with `client.messages.count_tokens` (model-specific — **never `tiktoken`**, which undercounts Claude ~15–20%).
- Record per call, keyed by creator, in Postgres: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`.
- **Total prompt = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`** — don't read `input_tokens` alone (it's only the uncached remainder).
- Enforce per-plan quotas tied to the RevenueCat subscription tier (enforcement owned by `11-monetization.md`; this layer supplies the meter).

### 10.3 Graceful fallbacks

- On `stop_reason: "refusal"` → route to the human-edit UX (§7.2). (Server-side `fallbacks` is a Fable-5 feature; for Opus 4.8, implement fallback logic at the adapter layer.)
- On 429/529 → SDK auto-retry (exponential backoff, default `max_retries=2`). 429 carries `retry-after`; the SDK honors it.
- On repeated Opus overload for a non-critical surface → optionally fall back to a **Haiku draft flagged "needs review"** rather than failing the creator outright. Never silently degrade a *publish-bound* artifact without the review flag.
- The publish gate fails **closed** (§7.4) — overload there blocks scheduling, it does not wave a script through.

---

## 11. Open questions

1. **Publish-gate hard-block vs. warn-and-override.** Should a flagged script **hard-block** scheduling, or always allow with a warning + creator override? This is a liability-vs-friction call that legal should weigh in on before launch. (Current pipeline supports both; default is soft-flag + override pending decision.)
2. **Embeddings provider — RESOLVED (§6.3).** Closed: **Voyage AI `voyage-3.5` at `vector(1024)`, cosine**, behind the `Embedder` adapter, jointly owned by this doc and `06-brand-graph.md`. `06` must set its vector columns, HNSW index, and `match_brand_facts` RPC to `vector(1024)` before authoring the first migration (its Open Q #1 is resolved by §6.3). Remaining sub-decision (non-blocking, can change without a re-embed): whether to lift the recall dimension to 2048 if eval shows 1024 under-recalls on long post histories — Matryoshka means we can re-embed to a larger dim later, but **only** as a deliberate backfill, never a silent change.
3. **Per-creator Opus spend ceiling.** What is the acceptable Opus spend per creator per generation session? Setting this concretely lets us fix the Haiku/Opus routing thresholds and the per-plan token quotas.
4. **Middle tier (Sonnet 4.6) for Script Studio.** The locked spec is strictly Opus/Haiku. Is a Sonnet 4.6 (`$3/$15`, 1M context) middle tier wanted for Script Studio to cut cost at acceptable quality, or do we stay two-tier? Affects the routing table in §2.7.

## 12. Sources

- [Prompt caching — Claude Platform docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — `cache_control` params, 4-breakpoint max, prefix-match invariant, TTLs.
- [Anthropic pricing — Claude docs](https://platform.claude.com/docs/en/about-claude/pricing) — Opus 4.8 $5/$25, Haiku 4.5 $1/$5, cache read 0.1× / write 1.25×–2×.
- [Structured outputs — Claude docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) — `output_config.format`, `strict: true`, grammar-constrained decoding, 24h schema cache.
- [Anthropic — Advanced tool use / agent design](https://www.anthropic.com/engineering/advanced-tool-use) — cheapest-tier-first heuristic, strict tool use, forced `tool_choice`.
- [Content moderation — Claude docs](https://platform.claude.com/docs/en/about-claude/use-case-guides/content-moderation) — classifier-based moderation categories incl. IP/hate.
- [Building safeguards for Claude — Anthropic](https://www.anthropic.com/news/building-safeguards-for-claude) — real-time classifiers + Safety Filters as a free API layer.
- [Handle streaming refusals — Claude docs](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/handle-streaming-refusals) — `stop_reason: "refusal"` handling.
- [Claude AUP over-aggression (The Register, 2026)](https://www.theregister.com/software/2026/04/23/claude_opus_47_has_turned/) — design for false-positive refusals on benign creator content.
- [LLM-as-a-judge best practices (Agenta)](https://agenta.ai/blog/llm-as-a-judge-guide-to-llm-evaluation-best-practices) — binary/3-point scales, criterion separation, N-best.
- [5 biases in LLM evals (Sebastian Sigl)](https://www.sebastiansigl.com/blog/llm-judge-biases-and-how-to-fix-them/) — position/verbosity/self-enhancement bias + fixes.
- [Do Before You Judge — self-reference (arXiv 2509.19880)](https://arxiv.org/pdf/2509.19880) — self-critique improves judgment quality.
- [Streaming messages — Claude docs](https://platform.claude.com/docs/en/build-with-claude/streaming) — SSE events, TTFT, mid-stream tool execution.
- [AI streaming → durable sessions (WebSocket.org)](https://websocket.org/guides/use-cases/ai-streaming/) — durable/crash-proof long-job pattern (maps to Trigger.dev).
- [RAG vs long-context (Meilisearch)](https://www.meilisearch.com/blog/rag-vs-long-context-llms) — cost/latency tradeoff; cache-augmented generation.
- [Embeddings — Claude docs](https://platform.claude.com/docs/en/build-with-claude/embeddings) — Anthropic has no embeddings endpoint; Voyage AI is the recommended partner (basis for the §6.3 provider pick).
- [Voyage AI embeddings](https://docs.voyageai.com/docs/embeddings) — `voyage-3.5`, `input_type` document/query asymmetry, Matryoshka output dimensions (256/512/1024/2048) — basis for the canonical `vector(1024)`.
- [Context engineering for production agents (K. Paul)](https://medium.com/@kuldeep.paul08/context-engineering-optimizing-llm-memory-for-production-ai-agents-6a7c9165a431) — KV-cache cost scales with prompt length.
- [Token counting — Claude docs](https://platform.claude.com/docs/en/build-with-claude/token-counting) — `count_tokens`, don't use `tiktoken`.

> Model IDs, pricing, the 4096-token Opus/Haiku cache minimum, the `structured-outputs-2025-11-13` beta header, the adaptive-thinking constraint, and Batch-API 50% pricing are verified against Anthropic's `claude-api` skill (cache date 2026-06-04), authoritative over web sources where they differ.
