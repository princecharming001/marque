# Deploying Marque updates ("OTA")

Marque's iOS app is **native SwiftUI** — there is no Expo/JS bundle, so app-side
code can NEVER ship over the air (Apple prohibits native apps downloading
executable code). What updates without App Review is the **backend**, and since
nearly all product behavior lives there (script/hook generation, the AI editor +
EDL pipeline, conversational tweaks, feed/reels/trends, prompts, learning loop),
a backend deploy IS Marque's OTA channel.

## What ships OTA vs what needs a build

| Change | Channel | Latency |
|---|---|---|
| Backend code (`backend/`, `render/` bridge, `Dockerfile`) | Render deploy | ~3 min |
| Backend env vars / API keys | Render dashboard or API → redeploy | ~3 min |
| Supabase data (learning stack, config rows) | direct SQL/API | instant |
| ANY Swift/UI/native change | archive → TestFlight/App Store | hours–days |

## The pipeline (verified 2026-07-04)

- **Service**: `marque-api` on Render — `srv-d94rk95ckfvc73ag4990`, region oregon,
  plan starter, Docker (repo-root `Dockerfile`), health check `/healthz`.
- **Repo**: `github.com/princecharming001/marque`, branch `main`.
- **Live URL**: `https://marque-api.onrender.com`

### ⚠️ Auto-deploy on push does NOT work

The service is configured `autoDeploy: yes` (trigger: commit), but the GitHub
repo has **no Render webhook** (`gh api repos/princecharming001/marque/hooks`
returns `[]`) — pushes never reach Render, so nothing auto-deploys. This is the
same failure mode maxapp's service has. To fix permanently: GitHub → Settings →
Applications → Render → grant the app access to the `marque` repo (or reconnect
the repo from the Render dashboard service settings), then verify a push
produces a deploy with `trigger: "commit"`.

### Deploy procedure (the reliable path)

```bash
# 1. Never push red:
cd backend && python3 -m pytest -q && python3 -m eval.run_eval

# 2. Push:
git push origin main

# 3. Trigger the deploy manually (auto-deploy is dead, see above):
curl -s -X POST "https://api.render.com/v1/services/srv-d94rk95ckfvc73ag4990/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  --data '{"clearCache":"do_not_clear"}'

# 4. Poll until live (id from step 3):
curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d94rk95ckfvc73ag4990/deploys/<dep-id>"

# 5. Verify:
curl -s https://marque-api.onrender.com/readyz    # expect ai/scrape/publish: live
```

Docs-only changes don't need a deploy. `RENDER_API_KEY` lives with the account
owner (never commit it).

### TestFlight per-build compliance/internal-tester steps are automated

Two things ASC used to require a manual website click for on *every* build:

1. **Export compliance question** — permanently eliminated. `ITSAppUsesNonExemptEncryption: false`
   is baked into `ios/project.yml` -> `Info.plist` (accurate: the app's only crypto
   use is SHA256 content hashing plus standard HTTPS/TLS, both export-exempt).
   ASC reads this from the binary at upload time and never asks again. Re-audit
   this flag if the app ever adds real encryption (E2E, at-rest crypto, etc.).
2. **Adding the build to Internal Testers** — not eliminable (Apple's API has no
   "auto-add every new build" setting), but scripted: after `altool --upload-app`
   succeeds, run:
   ```bash
   python3 scripts/testflight_add_to_internal.py <build_number>
   ```
   It answers export compliance via the API as a backstop and POSTs the build
   into the "Internal Testers" beta group (id `c811be00-cd4c-4db6-ace5-38ac0d77377a`,
   app id `6787590830`). ASC has a propagation lag between `processingState:
   VALID` and the build becoming visible to beta-group endpoints — the script
   retries for up to 5 minutes.

### Gotchas

- `_clip_jobs` (clip/tweak sessions) are **in-memory** — every deploy wipes them;
  clients see "edit session expired" on old clips. The learning stack
  (`arm_stats`/`post_registry`) is durable in Supabase and survives deploys.
- Env-var changes via `PUT /v1/services/{id}/env-vars` do not restart the
  service by themselves — follow with a deploy.
- First request after an idle period may be slow only on free plans; starter is
  always-on.

## Studio editing engine (marque-studio worker)

One-tap clip edits can run on the Studio engine (`studio/`) instead of the legacy EDL + Lambda pipeline.
The API decides per job at create time (`job["engine"]`, `backend/app/studio_engine.py`) and falls back to the
legacy pipeline for that job whenever Studio is off, busy, slow or failing. Nothing changes in the iOS app: the
job still polls the same `GET /v1/clips/{id}` fields (`status`, `clips[].render_url`, `thumbnail_url`).

- **Worker**: Render private service `marque-studio` (render.yaml), `Dockerfile.studio`, plan pro plus (4 vCPU,
  8 GB), a 20 GB disk at `/data` for 24 h of job folders. Env: `STUDIO_WORKER_TOKEN`, `ANTHROPIC_API_KEY`,
  `ELEVENLABS_API_KEY`, `ASSEMBLYAI_KEY`, `REMOTION_AWS_ACCESS_KEY_ID`, `REMOTION_AWS_SECRET_ACCESS_KEY`,
  `REMOTION_BUCKET`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (same values as marque-api).
- **API switches** (marque-api env, restart to apply): `STUDIO_ENGINE=off|on|allowlist`,
  `STUDIO_ENGINE_ALLOWLIST=<creator ids>`, `STUDIO_WORKER_URL=http://marque-studio:8080` (the private hostname
  the Render dashboard shows), `STUDIO_WORKER_TOKEN`. Optional: `STUDIO_QUEUE_MAX_S` (300), `STUDIO_MAX_S` (2400).
- **Kill switch**: set `STUDIO_ENGINE=off` and redeploy marque-api. New jobs go legacy; jobs already on Studio
  finish there or fall back.
- **What routes**: `auto_confirm` talking-head jobs from Pro creators (`config.is_pro != "0"`: free-tier renders need
  the watermark, which only legacy draws) that don't ask for b-roll or music. Split screen, green screen, react,
  analyze-first (brief review) and eval `edl_only` jobs stay legacy. Sources over `STUDIO_MAX_SOURCE_S` (180 s
  until longer takes are measured) fall back. A take with no speech fails as `transcribe_failed` (no fallback: legacy
  would fail the same way).
- **Worker safety**: it downloads sources only from `STUDIO_SOURCE_HOSTS` (default: the `SUPABASE_URL` host), never
  follows redirects, and refuses to start with half-set S3/Supabase credentials. `DELETE /jobs/{id}` stops a running
  edit at its next stage; the API sends it on every fallback. A job that shipped without the planner (key or credits
  problem) logs a warning on both sides (`planner_used: false`).
- **Known gap**: a Studio clip has no legacy EDL, so the manual Pro editor can't open it (the app shows "isn't
  ready yet"); chat tweaks re-edit it through the worker (`POST /jobs/{id}/chat`), and requests it can't do (added
  music, SFX, cutaways) get a reply up front. `GET /v1/clips/{id}` reports `engine` and `edit_capabilities` so the
  next app build can hide manual edit / restore / retheme for these clips.
- **Outputs**: finals in the Remotion S3 bucket under `studio/<job>/<clip>-r<n>.mp4`, posters in
  `marque-clips/posters/`, same places the legacy pipeline writes.
