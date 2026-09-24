#!/bin/bash
# Pure-logic tests for the manual editor (ios/Marque/Features/Editor). The files listed
# below are Foundation-only by design, so they compile for macOS with plain swiftc — no
# simulator, no Xcode test target. Exit status is non-zero on any failed assertion.
#
#   ios/LogicTests/editor/run.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ED="$HERE/../../Marque/Features/Editor"
OUT="/tmp/marque-editor-logictests"
mkdir -p "$OUT"

xcrun swiftc -swift-version 5 -o "$OUT/editor-logic" \
  "$ED/LayoutConstants.swift" \
  "$ED/EditorModel.swift" \
  "$ED/LocalEDLEngine.swift" \
  "$ED/EditorSession.swift" \
  "$ED/EditorDraft.swift" \
  "$HERE/main.swift"

"$OUT/editor-logic"
