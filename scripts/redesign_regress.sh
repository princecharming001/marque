#!/usr/bin/env bash
# Run a list of existing Maestro flows on one simulator; one PASS/FAIL line each.
# usage: scripts/redesign_regress.sh <sim-udid> <out-dir> flow [flow ...]
SIM="$1"; OUT="$2"; shift 2
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT"; xcrun simctl boot "$SIM" 2>/dev/null; xcrun simctl bootstatus "$SIM" -b >/dev/null 2>&1
xcrun simctl ui "$SIM" appearance light
export MAESTRO_DRIVER_STARTUP_TIMEOUT=${MAESTRO_DRIVER_STARTUP_TIMEOUT:-180000}
for f in "$@"; do
  /Users/home/.maestro/bin/maestro --device "$SIM" test "$ROOT/.maestro/$f.yaml" >"$OUT/$f.log" 2>&1
  rc=$?
  step=$(grep -E "FAILED" "$OUT/$f.log" | head -1 | sed 's/^ *//' | cut -c1-110)
  printf "%-18s %s %s\n" "$f" "$([ $rc -eq 0 ] && echo PASS || echo FAIL)" "$step" | tee -a "$OUT/_summary.txt"
done
exit 0
