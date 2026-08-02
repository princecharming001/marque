#!/bin/zsh
# Contract checks for the build-61 UX workstream. Both compile the APP'S OWN source
# (SubmitConfig / DeckDecoding + the models) with swiftc — they are not re-implementations,
# so a key that disappears from the app disappears from the check.
#
#   ./run_checks.sh                 # submit-config keys only
#   ./run_checks.sh --with-deck     # also decode captured /v1/cta-styles + /v1/style-deck
#                                   # payloads (expects /tmp/cta.json and /tmp/deck.json;
#                                   # capture them from a running backend first:
#                                   #   curl -s $API/v1/cta-styles > /tmp/cta.json
#                                   #   curl -s $API/v1/style-deck > /tmp/deck.json)
set -e
DIR=${0:a:h}
S="$DIR/../Marque"
OUT=$(mktemp -d)

# swiftc requires top-level code to live in a file literally named main.swift.
cp "$DIR/submit_config_check.swift" "$OUT/main.swift"
swiftc -o "$OUT/submitcheck" "$S/Models/Models.swift" "$S/Models/StyleProfileMapper.swift" \
    "$S/Features/SubmitConfig.swift" "$OUT/main.swift"
"$OUT/submitcheck"

if [[ "$1" == "--with-deck" ]]; then
    echo
    cp "$DIR/deck_decode_check.swift" "$OUT/main.swift"
    swiftc -o "$OUT/deckcheck" "$S/Models/Models.swift" "$S/Models/StyleProfileMapper.swift" \
        "$S/Adapters/DeckDecoding.swift" "$OUT/main.swift"
    "$OUT/deckcheck"
fi
