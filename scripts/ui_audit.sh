#!/usr/bin/env bash
# LOOP U — runs .maestro/format-audit.yaml twice against the same simulator:
# once at the default text size, once at accessibility-adjacent "extra-extra-large"
# (xcrun simctl ui <device> content_size — note underscore, not hyphen, despite
# the option's own docs prose using a hyphen) — a cheap way to catch layout that
# breaks under large Dynamic Type, per the plan's LOOP U spec. $0 cost: no
# vision/LLM calls, just Maestro's own assertVisible/assertNotVisible against
# .maestro/ui-manifest.json's expectations (screenshots are additionally
# available for the optional vision tier, backend/eval/ui_eval.py).
#
# Usage: scripts/ui_audit.sh
#
# Requires a LOCAL keyless backend already running on :8001 (no
# ANTHROPIC_API_KEY set — main.py's demo-job synthesis only fires keyless):
#   cd backend && uvicorn main:app --port 8001
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIM="${MARQUE_SIM:-2858A468-767A-4F05-B43E-7E84FA8B86B6}"
FLOW="$ROOT/.maestro/format-audit.yaml"
SHOTS="$ROOT/.shots"

# Build once — both passes reuse the same installed binary (the app doesn't
# read content-size at build time, only at runtime), avoiding a redundant
# rebuild+reinstall for the second pass.
"$ROOT/scripts/dev.sh" build
xcrun simctl boot "$SIM" 2>/dev/null || true

run_pass() {
  local suffix="$1"
  echo "[ui_audit] pass: $suffix"
  export MAESTRO_DRIVER_STARTUP_TIMEOUT=120000
  /Users/home/.maestro/bin/maestro --device "$SIM" test "$FLOW"
  if [ "$suffix" != "default" ]; then
    # Re-tag this pass's screenshots so the default pass's files (written
    # first, same names) aren't overwritten by the second run.
    for f in "$SHOTS"/format-audit-*.png; do
      case "$f" in *"-$suffix.png") continue ;; esac
      [ -f "$f" ] || continue
      mv "$f" "${f%.png}-$suffix.png"
    done
  fi
}

xcrun simctl ui "$SIM" content_size default
run_pass "default"

xcrun simctl ui "$SIM" content_size extra-extra-large
run_pass "xxl"

# Always restore default afterward — never leave the sim in an accessibility
# state that would confuse an unrelated later manual run.
xcrun simctl ui "$SIM" content_size default

echo "[ui_audit] done — screenshots in $SHOTS/format-audit-*.png (default) and *-xxl.png"
