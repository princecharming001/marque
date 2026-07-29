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
