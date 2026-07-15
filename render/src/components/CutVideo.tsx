import React from "react";
import { Series, OffthreadVideo } from "remotion";
import { Clip, VolumeRange, volumeAt, Look, pyRound } from "../types";

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
// pyRound, NOT Math.round: build_render_plan's out_cursor uses Python round()
// (half-to-even); Math.round is half-up, and the mismatch shifted every
// boundary after a x.5-length clip by one frame (see pyRound in types.ts).
export const clipOutFrames = (c: Clip): number =>
  Math.max(1, pyRound((c.src_out - c.src_in) / (c.speed || 1)));

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

// A8 (schema v4): "finishing" — an IG-proof polish pass (saturate + contrast +
// a black-lift so shadows don't crush on a phone screen). Black lift needs an
// SVG feComponentTransfer (a CSS filter alone can't remap the shadow floor);
// `finishingFilter` returns that def (CutVideo renders it, same pattern as
// temperatureFilter below) and lookFilterCSS references it via url(#id).
// Deliberately NO sharpen/feConvolveMatrix here — see Grade.tsx's design note
// on why a per-pixel convolution is out of scope (Lambda render cost/risk).
const FINISHING_BLACK_FLOOR = 12 / 255;
export const finishingFilter = (look: Look | null | undefined): { id: string; floor: string } | null => {
  if (!look || look.filter !== "finishing") return null;
  const t = Math.min(1, Math.max(0, look.intensity ?? 1));
  const floor = FINISHING_BLACK_FLOOR * t;   // intensity scales how much the floor lifts
  return { id: `marque-finishing-${Math.round(floor * 1000)}`, floor: floor.toFixed(4) };
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
    case "finishing": parts.push(`saturate(${lerp(1, 1.10)}) contrast(${lerp(1, 1.15)})`); break;
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
  const fin = finishingFilter(look);
  if (fin) parts.push(`url(#${fin.id})`);
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
  const finDef = finishingFilter(look);      // A8: SVG feComponentTransfer black-lift def
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
  // Per-cut declick: every clip boundary is a hard butt-splice of the source waveform,
  // so its instantaneous amplitude jump reads as a click/pop (worst mid-phrase, and at
  // every speed-ramp seam). An equal-power (√) micro-fade over ~2 frames at each INTERNAL
  // seam smooths the discontinuity without an audible "fade". First-clip-start and
  // last-clip-end are the true video boundaries — the composition owns those, so we skip
  // them to avoid double-fading.
  const SEAM_FADE_FRAMES = 3;
  const seamFade = (localF: number, len: number, fadeIn: boolean, fadeOut: boolean): number => {
    if (len <= 2 * SEAM_FADE_FRAMES) return 1;   // clip too short to fade cleanly
    // Symmetric ramp over SEAM_FADE_FRAMES frames on each internal edge. The +1 divisor
    // centres the ramp on frame midpoints so neither edge renders a fully-silent frame
    // (a hard 0 can itself click); the two abutting clips' equal-power (√) gains still
    // sum to ~unity across the seam. fade-in: 0.25→0.5→0.75→1; fade-out mirrors it.
    let g = 1;
    if (fadeIn && localF < SEAM_FADE_FRAMES) g = Math.min(g, (localF + 1) / (SEAM_FADE_FRAMES + 1));
    if (fadeOut && localF >= len - SEAM_FADE_FRAMES) g = Math.min(g, (len - localF) / (SEAM_FADE_FRAMES + 1));
    return Math.sqrt(Math.max(0, Math.min(1, g)));
  };
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
      {finDef && (
        <svg width={0} height={0} style={{ position: "absolute" }} aria-hidden>
          <defs>
            <filter id={finDef.id} colorInterpolationFilters="sRGB">
              <feComponentTransfer>
                <feFuncR type="linear" slope={(1 - Number(finDef.floor)).toFixed(4)} intercept={finDef.floor} />
                <feFuncG type="linear" slope={(1 - Number(finDef.floor)).toFixed(4)} intercept={finDef.floor} />
                <feFuncB type="linear" slope={(1 - Number(finDef.floor)).toFixed(4)} intercept={finDef.floor} />
              </feComponentTransfer>
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
            volume={(localF) => {
              const len = clipOutFrames(c);
              const base = hasRanges ? volumeAt(outStart + localF, volumeRanges!) : 1;
              return base * gainMult * seamFade(localF, len, i > 0, i < withOffsets.length - 1);
            }}
            style={{ width: "100%", height: "100%", objectFit: "cover",
                     // Canvas transform: translate in unscaled units, then zoom
                     // (CSS transform lists apply right-to-left). transformOrigin
                     // biases the zoom toward a face-position proxy (38% down the
                     // frame, ~selfie framing) instead of dead-center — without
                     // this a punch/framing zoom pulls the face out of frame on a
                     // tight crop. VIDEO_UNDERSTANDING is off so there's no real
                     // face box yet; this is the deliberate v1 proxy (A3).
                     ...(c.tx_scale !== 1 || c.tx_x !== 0 || c.tx_y !== 0
                       ? { transform: `translate(${(c.tx_x ?? 0) * 100}%, ${(c.tx_y ?? 0) * 100}%) scale(${c.tx_scale ?? 1})`,
                          transformOrigin: "50% 38%" }
                       : {}),
                     ...(filter ? { filter } : {}), ...style }}
          />
        </Series.Sequence>
      ))}
    </Series>
    </>
  );
};
