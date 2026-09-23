#!/usr/bin/env bash
# Step 6 capture: run the redesign QA flows on one simulator in one appearance.
# usage: scripts/redesign_shots.sh <sim-udid> <light|dark> <out-dir> [flow ...]
set -uo pipefail
SIM="$1"; MODE="$2"; OUT="$3"; shift 3
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLOWS=("${@:-rd-gates rd-home rd-tabs rd-editor rd-onboarding}")
[ $# -eq 0 ] && FLOWS=(rd-gates rd-home rd-tabs rd-editor rd-onboarding)
mkdir -p "$OUT"
xcrun simctl boot "$SIM" 2>/dev/null || true
xcrun simctl bootstatus "$SIM" -b >/dev/null 2>&1
xcrun simctl ui "$SIM" appearance "$MODE"
export MAESTRO_DRIVER_STARTUP_TIMEOUT=180000
for f in "${FLOWS[@]}"; do
  log="$OUT/_$f.log"
  /Users/home/.maestro/bin/maestro --device "$SIM" test -e OUT="$OUT" "$ROOT/.maestro/redesign/$f.yaml" >"$log" 2>&1
  rc=$?
  printf "%-14s %s  (%s shots)\n" "$f" "$([ $rc -eq 0 ] && echo PASS || echo "FAIL rc=$rc")" \
    "$(ls "$OUT" | grep -c '\.png$')"
  [ $rc -ne 0 ] && grep -E "FAILED|Element not found|Assertion" "$log" | head -3 | sed 's/^/    /'
done

exit 0
