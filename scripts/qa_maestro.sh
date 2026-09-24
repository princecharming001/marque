#!/usr/bin/env bash
# Run one Maestro flow on a PINNED simulator and clean up after it.
#
# Why: every Maestro run leaves an XCTest screen recording in the sim's testmanagerd
# container (tens to hundreds of MB each; one dev sim had accumulated 35GB). This wrapper
# snapshots the attachment list before the run and deletes ONLY the recordings the run
# created, so a long QA sweep can't fill the disk. It never touches older attachments.
#
# Usage: scripts/qa_maestro.sh <sim-udid> <flow.yaml> [maestro test args...]
# Env:   QA_KEEP_ATTACHMENTS=1 keeps the new recordings (debugging a flaky flow).
set -uo pipefail
SIM="$1"; FLOW="$2"; shift 2
MAESTRO=/Users/home/.maestro/bin/maestro
DEV=/Users/home/Library/Developer/CoreSimulator/Devices/$SIM/data/Containers/Data/InternalDaemon
ATT=""
for c in "$DEV"/*; do
  id=$(plutil -extract MCMMetadataIdentifier raw "$c/.com.apple.mobile_container_manager.metadata.plist" 2>/dev/null)
  [ "$id" = "com.apple.testmanagerd" ] && ATT="$c/Attachments"
done
BEFORE=$(mktemp)
[ -n "$ATT" ] && ls "$ATT" 2>/dev/null | sort > "$BEFORE"
export MAESTRO_DRIVER_STARTUP_TIMEOUT=${MAESTRO_DRIVER_STARTUP_TIMEOUT:-120000}
"$MAESTRO" --device "$SIM" test "$FLOW" "$@"
RC=$?
if [ -n "$ATT" ] && [ -z "${QA_KEEP_ATTACHMENTS:-}" ]; then
  ls "$ATT" 2>/dev/null | sort | comm -13 "$BEFORE" - | while read -r f; do rm -f "$ATT/$f"; done
fi
rm -f "$BEFORE"
exit $RC
