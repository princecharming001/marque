#!/usr/bin/env bash
# CP-1 gate: every CTA template must RENDER — not just typecheck — at its entrance,
# its middle and its final frame. A template that throws at frame 0 (bad interpolate
# range, undefined param) would otherwise only surface when a creator picks it.
#
#   ./scripts/cta_stills.sh [out_dir]
set -uo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-/tmp/cta_stills}"
mkdir -p "$OUT"
IDS=$(python3 -c "import json;print(' '.join(s['id'] for s in json.load(open('src/components/cta/cta_styles.json'))['styles']))")
FRAMES="0 75 149"
fail=0; n=0
for id in $IDS; do
  for f in $FRAMES; do
    n=$((n+1))
    props=$(python3 -c "import json;print(json.dumps({'styleId':'$id','text':'Follow for more','handle':'@yourname','logoUrl':None}))")
    if ! npx remotion still src/index.ts Marque-CtaPreview "$OUT/${id}_${f}.png" \
         --frame="$f" --props="$props" >/dev/null 2>"$OUT/${id}_${f}.err"; then
      echo "FAIL  $id @ frame $f"; sed -n '1,6p' "$OUT/${id}_${f}.err"; fail=$((fail+1))
    fi
  done
done
echo "---"
echo "rendered $((n-fail))/$n stills across $(echo $IDS | wc -w | tr -d ' ') templates"
[ "$fail" -eq 0 ] && echo "CTA STILLS: PASS" || { echo "CTA STILLS: $fail FAILURES"; exit 1; }
