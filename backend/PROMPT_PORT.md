# PROMPT_PORT — LOOP P contract (Palo → Yunicorn backend port)

You are grinding the Palo → Yunicorn AI-backend port. Work one unit at a time from
`BACKLOG_PORT.md`. Reference architecture: `../HANDOFF_PALO_PORT_PLAN.md`. Source of
truth to COPY FROM (read-only, never edit): `/Users/home/Palo_Server`.

## Loop

1. Open `BACKLOG_PORT.md`; pick the FIRST unchecked `[ ]` item.
2. Write the failing test FIRST (keyless — monkeypatch every external boundary; assert
   `mode:"mock"` / deterministic fallback with no keys).
3. Implement in `app/*.py`, behind the item's `app/palo_flags.py` flag (default OFF).
   Copy Palo prompt text VERBATIM into `prompts.py` builders (adapt IG-reel vocabulary
   second). Persist via `PaloStore` methods + `migrations.sql` idempotent blocks.
4. Run `scripts/gate.sh --fast` (keyless). It MUST stay green: full pytest + `eval/port_eval`
   + render checks + secret-scan. Add your new golden checks to `eval/port_eval.py`.
5. Check the item off with a one-line evidence note (`[x] … — <test names>, keyless green`).
6. `git add` ONLY your new/changed files (never `-A`; the tree has unrelated `.shots/`),
   commit locally with a `palo-port:` prefix. Do NOT push or deploy.
7. Repeat. When every box is checked, print exactly: **YUNICORN PORT GREEN**.

## Hard rules

- Keyless-mock everywhere; a missing key degrades, never 500s (matches `if not ANTHROPIC_KEY`).
- No LangChain. Text: `anthropic()` (request path, raises→route mocks) or
  `app.palo_llm.anthropic_cached()` (background, returns None→mock). JSON: `anthropic_json` /
  `anthropic_cached_json`. Models: `OPUS/SONNET/HAIKU` from `prompts.py`.
- Every LLM op records `ai_usage` and has a call-budget test (compile ≤2 heavy, ideate ≤4,
  judge ≤1). Strategy compile stays behind `ai_usage.compile_allowed` (allowlist default empty).
- NEVER copy a Palo secret. LOOP C (`secrets:scan` in gate.sh) fails on Moonshot keys,
  `sk-…`, RapidAPI keys, or `postgres://user:pass@` in the diff.
- Tier gating via `app.tiers` only (`has_feature`, `cadence`, `metrics_sources`, `at_least`).
- iOS: ship typed request/response models + a contract note in `docs/api/PALO_PORT.md`; do
  not hand-edit Swift in this loop unless the item is a P7.x UI unit.

## Definition of done (a unit)

Failing→passing keyless test committed · flag default OFF · `gate.sh --fast` green ·
`port_eval` golden added · `ai_usage` + budget test · backlog box checked with evidence.
