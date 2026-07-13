# Palo Port — API contract for iOS (branch `palo-port`)

The backend port (Phases 0–6) is complete and **flag-gated OFF**. This is the typed
request/response contract the SwiftUI surfaces (P7.x) integrate against. Every endpoint
degrades gracefully when its flag is off or keys are absent (`mode: "off"|"mock"|"live"`),
so iOS can build + ship against mocks before prod flips the flags.

**Global:** all responses carry a `mode` field. `off` = capability flag off, `mock` =
no key/deterministic, `live` = real. Treat `off`/`mock` as valid, renderable data.

## Flags (env, all default OFF)
`PALO_PORT` master + `MEMORY_V2` · `IDEA_BANK` · `TRACK_INSIGHTS` · `STRATEGY_COMPILER`
· `WRITE_AGENT` · `EXEMPLAR_BANK` · `VIDEO_BRAIN`. A capability needs `PALO_PORT=1` AND
its own flag. Tiers (`creators.tier`): `starter` / `growth` / `studio`.

---

## Endpoints

### `POST /v1/ideas` — the idea bank (P7.2 feed, P7.6 pick-and-write)
Req: `{ "creator_id": str, "limit": int=12 }`
Res: `{ "mode", "briefs": [ Brief ] }`
`Brief = { id, creator_id, source: "spitfire|onboarding|chat|insight", title, summary,
beginning, middle, ending, score, status, created_at }`

### `GET|POST /v1/feed` — reasoned feed, now with briefs prepended (P7.2)
Unchanged shape `{ mode, items, next_cursor }`. When `IDEA_BANK` on, the **first page**
prepends idea items: `Item = { id, kind: "idea", source: "idea_bank", title, summary,
score, brief_id }` ahead of the existing script items. Deduped, capped at 3, never
re-injected on `cursor > 0`. Render idea items with a "Make this mine" affordance →
`POST /v1/write/from-brief`.

### `POST /v1/write/from-brief` — brief → full script (P7.2 → editor, P7.6)
Req: `{ "creator_id": str, "brief": Brief, "brand": {…} }`
Res: `{ "mode", "title", "body" }` — a ready-to-edit script (assembled from beats if off/keyless).

### `POST /v1/write/turn` — co-writing edit-chat (P7.5, ScriptReaderView)
Req: `{ "creator_id": str, "script": { title, body }, "instruction": str }`
Res: `{ "mode", "actions": [ Action ], "preview": { title, body }, "invariants": [str], "answer": str }`
`Action = { op: "edit|add|fill|answer", applied: bool, reason?, …fields }`. Actions are
**exact-substring** — render each as an accept/reject tweak-op (reuse `TweakChatSheet`);
`preview.body` is the doc with all applied. `answer` is chat-only (no doc change).
`invariants` non-empty ⇒ the model violated the contract (surface as a soft warning).

### `POST /v1/converse` — unchanged shape; now brain-aware (all surfaces)
When `MEMORY_V2`/`STRATEGY_COMPILER` on, the assistant silently uses the creator's memory,
never-re-pitch ledger, and compiled strategy. No client change; replies get sharper.
Deep-link entry from an insight push carries `?insight=<id>` — open ChatView pre-seeded.

### Insights (P7.3, PerformanceView inbox + push)
Cards live in `insight_feed` (delivered via APNs by the daily cron). Push payload:
`{ aps{alert{title,body}}, deeplink: "marque://chat?insight=<id>", insight_id, seed }`.
`InsightCard = { id, type, category: "blue|yellow|green|orange", title, description,
content, chips, conversation_seed, delivered, created_at }`. Tapping the card/push opens
ChatView seeded from `conversation_seed`. (Add a `GET` list route when the inbox lands;
the store method `load_insights` already exists.)

### Strategy (P7.4, "Your Strategy" / PlanBuildingView)
`channel_strategies.strategy_markdown` — render the `## Insights / ## Plan / ## Buckets /
## Brand Bets / ## Not-Doing` sections; `strategy_revision` + `strategy_updated_at` power
a "what changed this week" view. (Add a `GET /v1/strategy` read route when P7.4 lands;
`load_strategy` exists.)

### Internal crons (Render cron → not client-facing)
`POST /internal/cron/{ideate,insights,compile}` — body `{ "token": INTERNAL_CRON_TOKEN }`,
token+flag guarded. Idea bank / insight sweep / weekly strategy compile.

---

## iOS surface map (P7.x — follow-on SwiftUI, not in this backend loop)
- **P7.2 feed v2** — `HomeView`: render `kind:"idea"` items + "Make this mine"; `ReelDetailSheet` brief→script.
- **P7.3 insights inbox** — `PerformanceView` insight cards (type-colored, chips); push deep-link → seeded `ChatView`; `MetricsEntrySheet` demoted to fallback.
- **P7.4 Your Strategy** — `PlanBuildingView` → rendered strategy markdown + revision history.
- **P7.5 write edit-chat** — `ScriptReaderView` + `TweakChatSheet` applying `/v1/write/turn` actions with accept/reject.
- **P7.6 reel review** — `Library`/`ReelDetailSheet` "Get Palo's review" (Creative Review port, future).

## Rollout
Merge `palo-port` → flip `PALO_PORT=1` + one capability flag at a time in Render env.
Apply `migrations.sql` PALO PORT block (enable pgvector) first. Provision keys per
`../../HANDOFF_PALO_PORT_PLAN.md` §2. Owner deploys manually (standing rule).
