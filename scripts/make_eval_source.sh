#!/usr/bin/env bash
# Generates the synthetic source video LOOP F renders golden plans against —
# no real footage needed since the render pipeline only cares about frame
# timing/dimensions, never content. Deterministic (same command -> same file),
# gitignored (render/fixtures/source.mp4), regenerated on demand.
#
# testsrc2 gives a moving, non-uniform pattern (so a "non-black frame" check is
# meaningful) at exactly the composition's own dimensions/fps; a 440Hz sine
# tone is the known reference frequency the pitch-preservation check (LOOP F's
# home for formatting-risk #8) verifies survives a speed change untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/render/fixtures/source.mp4"
DURATION_S="${1:-90}"   # long enough to cover every corpus fixture's total_frames

mkdir -p "$(dirname "$OUT")"

if [ -f "$OUT" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "[make_eval_source] $OUT already exists (set FORCE=1 to regenerate)"
  exit 0
fi

ffmpeg -y -f lavfi -i "testsrc2=size=1080x1920:rate=30:duration=${DURATION_S}" \
       -f lavfi -i "sine=frequency=440:duration=${DURATION_S}" \
       -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$OUT"

echo "[make_eval_source] wrote $OUT (${DURATION_S}s @ 1080x1920/30fps, 440Hz tone)"
