#!/bin/zsh
# Pure-logic regression tests for the upload / processing / store layer. There is no iOS
# unit-test target, so this compiles the APP'S OWN Foundation-only sources (the exact files
# the app ships — not re-implementations) together with the assertion suites in this
# directory into one macOS binary under /tmp and runs it. Nothing here is wired into the
# Xcode project.
#
#   ios/LogicTests/run.sh            # exit 0 = every assertion passed, 1 = a failure
#
# Adding a suite: put `func run<Name>Tests()` in a new *Tests.swift file here, call it from
# main.swift, and list any new pure app source in SOURCES below (Foundation-only: no UIKit,
# AVFoundation or SwiftUI, or it won't compile for macOS).
set -euo pipefail
DIR=${0:a:h}
S="$DIR/../Marque"
OUT=$(mktemp -d /tmp/marque-logictests.XXXXXX)
trap 'rm -rf "$OUT"' EXIT

SOURCES=(
  "$S/Models/Models.swift"
  "$S/Models/StyleProfileMapper.swift"
  "$S/Adapters/UploadRetryPolicy.swift"
  "$S/Adapters/UploadJournal.swift"
  "$S/Adapters/CompressionPlan.swift"
  "$S/Adapters/ServerClipAdoption.swift"
  "$S/Adapters/JobPolicies.swift"
  "$S/Adapters/CappedDownload.swift"
  "$S/Features/TeleprompterLayout.swift"
  "$S/Features/Home/DaySummary.swift"
)

swiftc -O -o "$OUT/logictests" "${SOURCES[@]}" "$DIR"/*Tests.swift "$DIR/Support.swift" "$DIR/main.swift"
"$OUT/logictests"
