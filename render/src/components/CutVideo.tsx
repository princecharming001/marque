import React from "react";
import { Series, OffthreadVideo } from "remotion";
import { Clip, VolumeRange, volumeAt, Look } from "../types";

// The actual cut: stitches the kept source intervals (`clips`, source frames) back to
// back on the output timeline via <Series>. OffthreadVideo trimBefore/trimAfter select
// the source range; the Series.Sequence duration is that range's length divided by the
// clip's playback speed, so the concatenation IS the trimmed (and retimed) edit.
// Rendered as a sibling of captions/overlays (not their parent), so those keep using the
// global output frame from useCurrentFrame().
//
// G0 (verified against Remotion's OWN source, not just docs — a prior audit flagged
// this as backwards): trimBefore/trimAfter are ABSOLUTE source-frame positions, not
// "skip N frames" durations. remotion's validateTrimProps requires trimAfter >
// trimBefore (see node_modules/remotion/dist/cjs/validate-start-from-props.js —
// same contract as the deprecated startFrom/endAt props it aliases). That is exactly
// src_in/src_out's contract, so `trimBefore={c.src_in} trimAfter={c.src_out}` below
// is correct as written. Do not "fix" this without re-reading that file.
//
// SPEED: per-clip playbackRate (CapCut Speed → Normal). Output duration of a clip is
// round(kept/speed) — the SAME formula build_render_plan uses for its out_cursor, so
// caption/overlay output coords stay aligned with what actually plays. clipOutFrames
// is the single source of that formula on the TS side.
//
// volumeRanges (OUTPUT coords, from the plan's audio block) drive per-range source
// volume — mutes and duck-downs from the manual editor. Each Series.Sequence knows its
// own output offset (cumulative durations), so localFrame + outStart = output frame.
// volumeAt (types.ts) uses a half-open [frame_in, frame_out) check, matching every
// other interval convention in this codebase (segments/drops/kept-intervals) —
// verified, not an off-by-one (G10).
//
// G10: outStart/outCursor here MUST stay identical to FastCuts.tsx's cutStarts
// computation — that file duplicates this formula (via clipOutFrames) to place its
// cut-flash at the same boundaries. If you change this formula, change that usage too.
export const clipOutFrames = (c: Clip): number =>
  Math.max(1, Math.round((c.src_out - c.src_in) / (c.speed || 1)));

// P0.8: a REAL temperature grade. The old warm=`sepia()` / cool=`hue-rotate()` are crude —
// sepia desaturates + tints brown (not a white-balance) and hue-rotate spins every hue (a
// green face goes teal). A true temperature shift scales the RED and BLUE channels in
// opposite directions (warm = +R/−B, cool = −R/+B), which is exactly a diagonal
// feColorMatrix. `temperatureFilter` returns the SVG filter def (id + 20-value matrix) for
// a look's combined preset + adjust.temperature; lookFilterCSS references it via url(#id),
// and CutVideo renders the def. Returns null when the look is temperature-neutral.
const TEMP_K = 0.3;   // channel-gain per unit temperature (temp∈[-1,1])
export const temperatureFilter = (look: Look | null | undefined): { id: string; matrix: string } | null => {
  if (!look) return null;
  const t = Math.min(1, Math.max(0, look.intensity ?? 1));
  let temp = 0;
  if (look.filter === "warm") temp += 0.5 * t;
  else if (look.filter === "cool") temp -= 0.5 * t;
  temp += look.adjust?.temperature ?? 0;      // the manual knob (±0.5)
  temp = Math.max(-1, Math.min(1, temp));
  if (Math.abs(temp) < 0.001) return null;
  const r = (1 + TEMP_K * temp).toFixed(4);
  const b = (1 - TEMP_K * temp).toFixed(4);
  const matrix = `${r} 0 0 0 0  0 1 0 0 0  0 0 ${b} 0 0  0 0 0 1 0`;
  return { id: `marque-temp-${Math.round(temp * 1000)}`, matrix };
};

// The whole-video color grade as a CSS filter chain: named preset (blended toward
// identity by intensity) composed with the manual Adjust knobs. Empty look → "".
export const lookFilterCSS = (look: Look | null | undefined): string => {
  if (!look) return "";
  const parts: string[] = [];
  const t = Math.min(1, Math.max(0, look.intensity ?? 1));
  const lerp = (from: number, to: number) => from + (to - from) * t;
  switch (look.filter) {
    case "vivid": parts.push(`saturate(${lerp(1, 1.35)}) contrast(${lerp(1, 1.08)})`); break;
    case "film": parts.push(`contrast(${lerp(1, 1.12)}) saturate(${lerp(1, 0.85)}) sepia(${lerp(0, 0.18)})`); break;
    case "mono": parts.push(`grayscale(${t}) contrast(${lerp(1, 1.05)})`); break;
    case "golden": parts.push(`sepia(${lerp(0, 0.35)}) saturate(${lerp(1, 1.2)}) brightness(${lerp(1, 1.05)})`); break;
    // warm/cool: the temperature itself is the feColorMatrix below; keep only their
    // saturation/brightness character here.
    case "warm": parts.push(`saturate(${lerp(1, 1.1)})`); break;
    case "cool": parts.push(`saturate(${lerp(1, 1.05)}) brightness(${lerp(1, 1.02)})`); break;
  }
  const a = look.adjust;
  if (a) {
    if (a.brightness) parts.push(`brightness(${1 + a.brightness})`);
    if (a.contrast) parts.push(`contrast(${1 + a.contrast})`);
    if (a.saturation) parts.push(`saturate(${1 + a.saturation})`);
    // a.temperature is folded into the feColorMatrix (temperatureFilter), not sepia/hue.
  }
  const temp = temperatureFilter(look);
  if (temp) parts.push(`url(#${temp.id})`);
  return parts.join(" ");
};

export const CutVideo: React.FC<{
  sourceUrl: string;
  clips: Clip[];
  volumeRanges?: VolumeRange[] | null;
  look?: Look | null;
  style?: React.CSSProperties;
  gain?: number;   // P0.6: loudness-normalization dB applied to source audio
}> = ({ sourceUrl, clips, volumeRanges, look, style, gain }) => {
  if (!sourceUrl || clips.length === 0) {
    return <div style={{ width: "100%", height: "100%", background: "#111" }} />;
  }
  const filter = lookFilterCSS(look);
  const tempDef = temperatureFilter(look);   // P0.8: SVG feColorMatrix def that filter refs
  // P0.6: loudness normalization — a linear multiplier on the source audio. gain 0 → 1.0
  // (untouched). Composes with per-range volume; a range's volume is scaled by the same
  // factor so mute (0) stays muted.
  const gainMult = Math.pow(10, (gain ?? 0) / 20);
  const hasRanges = !!(volumeRanges && volumeRanges.length > 0);
  let outCursor = 0;
  const withOffsets = clips.map((c) => {
    const outStart = outCursor;
    outCursor += clipOutFrames(c);
    return { clip: c, outStart };
  });
  return (
    <>
      {tempDef && (
        <svg width={0} height={0} style={{ position: "absolute" }} aria-hidden>
          <defs>
            <filter id={tempDef.id} colorInterpolationFilters="sRGB">
              <feColorMatrix type="matrix" values={tempDef.matrix} />
            </filter>
          </defs>
        </svg>
      )}
    <Series>
      {withOffsets.map(({ clip: c, outStart }, i) => (
        <Series.Sequence key={i} durationInFrames={clipOutFrames(c)}>
          <OffthreadVideo
            src={sourceUrl}
            trimBefore={c.src_in}
            trimAfter={c.src_out}
            playbackRate={c.speed || 1}
            volume={
              hasRanges
                ? (localF) => volumeAt(outStart + localF, volumeRanges!) * gainMult
                : gainMult !== 1
                  ? gainMult
                  : undefined
            }
            style={{ width: "100%", height: "100%", objectFit: "cover",
                     // Canvas transform: translate in unscaled units, then zoom
                     // (CSS transform lists apply right-to-left).
                     ...(c.tx_scale !== 1 || c.tx_x !== 0 || c.tx_y !== 0
                       ? { transform: `translate(${(c.tx_x ?? 0) * 100}%, ${(c.tx_y ?? 0) * 100}%) scale(${c.tx_scale ?? 1})` }
                       : {}),
                     ...(filter ? { filter } : {}), ...style }}
          />
        </Series.Sequence>
      ))}
    </Series>
    </>
  );
};
