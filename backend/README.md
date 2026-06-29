# Marque backend

FastAPI orchestration service. Holds every vendor key so the iOS app ships none
(see `../DECISIONS.md`). This skeleton runs with zero keys (mock fallback).

## Run
```
make setup     # creates .venv + installs deps
make test      # pytest
make run       # uvicorn on :8000
```

## Endpoints
- `GET /healthz` — liveness
- `GET /readyz` — readiness + AI mode (live/mock)
- `POST /v1/scripts` — generate scripts. Proxies Anthropic when `ANTHROPIC_API_KEY` is set,
  mocks otherwise. Body: `{niche, audience, known_for, pillar, count}`.

## Keys (env, never committed)
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, plus (as the pipeline lands) `ASSEMBLYAI_KEY`,
`SHOTSTACK_KEY`, `AYRSHARE_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLOUDFLARE_*`.

The iOS app points each adapter's base URL here so the key stays server-side.
