#!/usr/bin/env bash
# Marque dev harness: generate project, build, install, optionally run a Maestro flow.
# Usage:
#   scripts/dev.sh build           # xcodegen + build + install on the Marque sim
#   scripts/dev.sh test            # build + install + run the full-loop Maestro flow
#   scripts/dev.sh test <flow.yaml>
set -euo pipefail

SIM="${MARQUE_SIM:-2858A468-767A-4F05-B43E-7E84FA8B86B6}"   # Marque-iPhone17Pro
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/ios/build/Build/Products/Debug-iphonesimulator/Marque.app"
CMD="${1:-test}"
FLOW="${2:-$ROOT/.maestro/flow-full.yaml}"

build() {
  cd "$ROOT/ios"
  /opt/homebrew/bin/xcodegen generate >/dev/null
  xcodebuild -project Marque.xcodeproj -scheme Marque -configuration Debug \
    -destination "platform=iOS Simulator,id=$SIM" -derivedDataPath build build \
    2>&1 | grep -E "error:|BUILD SUCCEEDED|BUILD FAILED" || true
  xcrun simctl boot "$SIM" 2>/dev/null || true
  xcrun simctl install "$SIM" "$APP"
  echo "installed -> $SIM"
}

case "$CMD" in
  build) build ;;
  test)
    build
    export MAESTRO_DRIVER_STARTUP_TIMEOUT=120000
    /Users/home/.maestro/bin/maestro --device "$SIM" test "$FLOW"
    ;;
  *) echo "unknown: $CMD"; exit 1 ;;
esac
