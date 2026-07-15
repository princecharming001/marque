import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Look, TransitionPlan } from "../types";

// Post layers that sit ABOVE the video (and b-roll) but BELOW captions:
// - vignette from the manual Adjust knobs (the only adjust a CSS filter can't do)
// - boundary transition dips (fade_black / fade_white / flash / whip / zoom_punch):
//   an effect ramped 0→peak→0 centered on the cut frame — no video overlap needed,
//   which is what makes these renderable with sequential <Series> clips.
// - film grain (A8): a cheap per-frame-bucket noise overlay, NOT a real per-pixel
//   filter on the video (see below for why).
//
// A8 DESIGN NOTE: whip/zoom_punch are implemented as OVERLAY effects (backdrop-
// filter blur / a radial "punch" pulse) rather than literally transforming the
// underlying video. Grade renders as a SIBLING of CutVideo in every composition
// (7 files, several with divergent layouts — DuetSplit has two CutVideo
// instances), so a real geometric zoom would require threading a time-varying
// transform into CutVideo itself and touching every composition — high risk to
// the core render path for a cosmetic transition effect. backdrop-filter blur
// (whip) and a radial brightness pulse (zoom_punch) are well-supported, cheap
// Chromium primitives that read as camera-motion/impact without that risk.
// feConvolveMatrix (a literal unsharp-mask sharpen) is deliberately NOT used
// anywhere here for the same reason doctrine bans long dissolves: it's one of
// the most expensive SVG filter primitives, applied per-pixel per-frame across
// a whole Lambda render — a real cost/reliability risk for a "nice to have".
const WHIP_MAX_BLUR_PX = 14;
const ZOOM_PUNCH_MAX_OPACITY = 0.22;

// Deterministic per-frame-bucket noise swatch (data-URI SVG feTurbulence),
// re-seeded every 3 frames (~10fps) so grain animates like real film grain
// without recomputing every single frame — feTurbulence is procedural (no
// image sampling), so this is cheap relative to a convolution.
const grainDataUri = (bucket: number): string => {
  const seed = (bucket % 97) + 1;   // feTurbulence seed must be a positive int
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'>` +
    `<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' seed='${seed}' stitchTiles='stitch'/>` +
    `<feColorMatrix type='matrix' values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.5 0'/></filter>` +
    `<rect width='100%' height='100%' filter='url(%23n)'/></svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
};

export const Grade: React.FC<{
  look?: Look | null;
  transitions?: TransitionPlan[] | null;
}> = ({ look, transitions }) => {
  const frame = useCurrentFrame();
  const vignette = look?.adjust?.vignette ?? 0;
  const grain = Math.min(1, Math.max(0, look?.grain ?? 0));

  let dip: { color: string; opacity: number } | null = null;
  let whipBlurPx = 0;
  let zoomPunchOpacity = 0;
  for (const t of transitions ?? []) {
    const half = Math.max(2, t.frames / 2);
    const d = Math.abs(frame - t.at_frame);
    if (d <= half) {
      const ramp = 1 - d / half;                    // 0 at edges → 1 on the cut
      if (t.style === "flash") {
        dip = { color: "#fff", opacity: ramp * 0.9 };
      } else if (t.style === "whip") {
        whipBlurPx = ramp * WHIP_MAX_BLUR_PX;
      } else if (t.style === "zoom_punch") {
        zoomPunchOpacity = ramp * ZOOM_PUNCH_MAX_OPACITY;
      } else {
        dip = { color: t.style === "fade_white" ? "#fff" : "#000", opacity: ramp };
      }
      break;
    }
  }

  if (!vignette && !dip && !whipBlurPx && !zoomPunchOpacity && !grain) return null;
  return (
    <>
      {vignette > 0 && (
        <AbsoluteFill style={{
          pointerEvents: "none",
          background: `radial-gradient(ellipse at center, transparent ${62 - vignette * 20}%, rgba(0,0,0,${0.55 * vignette}) 100%)`,
        }} />
      )}
      {whipBlurPx > 0 && (
        <AbsoluteFill style={{ pointerEvents: "none", backdropFilter: `blur(${whipBlurPx.toFixed(1)}px)` }} />
      )}
      {zoomPunchOpacity > 0 && (
        <AbsoluteFill style={{
          pointerEvents: "none",
          background: `radial-gradient(ellipse at center, rgba(255,255,255,${zoomPunchOpacity}) 0%, transparent 70%)`,
        }} />
      )}
      {grain > 0 && (
        <AbsoluteFill style={{
          pointerEvents: "none", opacity: grain * 0.5, mixBlendMode: "overlay",
          backgroundImage: grainDataUri(Math.floor(frame / 3)),
          backgroundSize: "200px 200px",
        }} />
      )}
      {dip && (
        <AbsoluteFill style={{ pointerEvents: "none", background: dip.color, opacity: dip.opacity }} />
      )}
    </>
  );
};
