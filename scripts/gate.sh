#!/usr/bin/env bash
# Single entry point for every gate in the retention-editor-upgrade initiative — used
# by the "loop" work (LOOP E / LOOP F / LOOP U) and by hand between phases. Stages
# short-circuit on first failure; each prints "[gate] <stage> OK|FAIL" so a scrollback
# always shows exactly how far it got.
#
# Usage:
#   scripts/gate.sh              # --fast (default): keyless, ~1 min
#   scripts/gate.sh --fast
#   scripts/gate.sh --full       # + iOS build + local render-smoke
#   scripts/gate.sh --paid       # + full corpus vision scoring + live LLM judges
#                                #   (needs ANTHROPIC_API_KEY; exits 2 if unset)
#
# Exit codes: 0 = pass, 1 = a gate stage failed, 2 = missing tool/env for the
# requested tier (never silently downgrades a tier and reports green).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIER="${1:---fast}"

pass() { echo "[gate] $1 OK"; }
fail() { echo "[gate] $1 FAIL"; exit 1; }
need_env() { if [ -z "${!1:-}" ]; then echo "[gate] $2 SKIPPED — \$$1 not set"; exit 2; fi; }

run() {
  local label="$1"; shift
  if "$@"; then pass "$label"; else fail "$label"; fi
}

# ---------------------------------------------------------------------------
# --fast tier — keyless, every iteration of every loop runs this.
# ---------------------------------------------------------------------------

# LOOP C — fail the gate if any Palo landmine secret got copied into the port files
# (Moonshot Kimi key, sk-… keys, RapidAPI keys, postgres creds). Scans ONLY the port's
# own modules so it never trips on unrelated existing code; skips modules not yet built.
secret_scan() {
  local files
  files=$(ls "$ROOT"/backend/app/palo_*.py "$ROOT"/backend/app/tiers.py \
             "$ROOT"/backend/app/doctrine.py "$ROOT"/backend/app/prompt_store.py \
             "$ROOT"/backend/app/prompt_assembly.py "$ROOT"/backend/app/ai_usage.py \
             "$ROOT"/backend/app/ideas.py "$ROOT"/backend/app/memory_v2.py \
             "$ROOT"/backend/app/recall_ledger.py "$ROOT"/backend/app/track_insights.py \
             "$ROOT"/backend/app/metrics_pollers.py "$ROOT"/backend/app/strategy_compiler.py \
             "$ROOT"/backend/app/dossier_adapter.py "$ROOT"/backend/app/write_agent.py \
             "$ROOT"/backend/app/exemplar.py "$ROOT"/backend/eval/port_eval.py 2>/dev/null)
  [ -z "$files" ] && return 0
  if echo "$files" | xargs grep -InE 'Kimik2APIKey|sk-[A-Za-z0-9]{24,}|rapidapi[._-]?key[[:space:]]*[:=]|postgres(ql)?://[^[:space:]/]+:[^[:space:]@]+@'; then
    echo "[gate] secrets:scan — Palo landmine secret found in port files ^"
    return 1
  fi
  return 0
}

fast_tier() {
  run "backend:pytest"       bash -c "cd '$ROOT/backend' && .venv/bin/python -m pytest -q"
  run "backend:edl_eval"     bash -c "cd '$ROOT/backend' && .venv/bin/python -m eval.edl_eval"
  run "backend:run_eval"     bash -c "cd '$ROOT/backend' && .venv/bin/python -m eval.run_eval"
  run "backend:port_eval"    bash -c "cd '$ROOT/backend' && .venv/bin/python -m eval.port_eval"
  run "secrets:scan"         secret_scan
  run "render:typecheck"     bash -c "cd '$ROOT/render' && npx tsc --noEmit"
  run "render:build_bridge"  bash -c "cd '$ROOT/render' && npm run build:bridge"
  run "render:node_test"     bash -c "cd '$ROOT/render' && npm test"
}

# ---------------------------------------------------------------------------
# --full tier — fast + iOS build + a local (Lambda-free) render smoke test.
# ---------------------------------------------------------------------------

full_tier() {
  fast_tier

  if [ "${SKIP_IOS:-0}" != "1" ]; then
    run "ios:build" bash -c "cd '$ROOT' && ./scripts/dev.sh build 2>&1 | tee /tmp/marque_ios_build.log | grep -q 'BUILD SUCCEEDED'"
  else
    echo "[gate] ios:build SKIPPED (SKIP_IOS=1)"
  fi

  if [ -f "$ROOT/backend/eval/format_eval.py" ]; then
    run "render:smoke" bash -c "cd '$ROOT/backend' && .venv/bin/python -m eval.format_eval --render"
  else
    echo "[gate] render:smoke SKIPPED — backend/eval/format_eval.py not built yet (LOOP F)"
  fi
}

# ---------------------------------------------------------------------------
# --paid tier — full + vision/LLM-judge scoring. Requires ANTHROPIC_API_KEY.
# ---------------------------------------------------------------------------

paid_tier() {
  full_tier
  need_env ANTHROPIC_API_KEY "paid tier"

  run "backend:edl_eval_live" bash -c "cd '$ROOT/backend' && ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY' .venv/bin/python -m eval.edl_eval --live"

  if [ -f "$ROOT/backend/eval/format_eval.py" ]; then
    run "render:format_score" bash -c "cd '$ROOT/backend' && ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY' .venv/bin/python -m eval.format_eval --render --score"
  else
    echo "[gate] render:format_score SKIPPED — backend/eval/format_eval.py not built yet (LOOP F)"
  fi

  if [ -f "$ROOT/backend/eval/ui_eval.py" ]; then
    run "ui:vision_score" bash -c "cd '$ROOT/backend' && ANTHROPIC_API_KEY='$ANTHROPIC_API_KEY' .venv/bin/python -m eval.ui_eval"
  else
    echo "[gate] ui:vision_score SKIPPED — backend/eval/ui_eval.py not built yet (LOOP U)"
  fi
}

case "$TIER" in
  --fast) fast_tier ;;
  --full) full_tier ;;
  --paid) paid_tier ;;
  *) echo "usage: $0 [--fast|--full|--paid]"; exit 2 ;;
esac

echo "[gate] $TIER — ALL STAGES PASSED"
