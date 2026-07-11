import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Look, TransitionPlan } from "../types";

// Post layers that sit ABOVE the video (and b-roll) but BELOW captions:
// - vignette from the manual Adjust knobs (the only adjust a CSS filter can't do)
// - boundary transition dips (fade_black / fade_white / flash): an opacity ramp
//   0→1→0 centered on the cut frame — no video overlap needed, which is what makes
//   these renderable with sequential <Series> clips.
export const Grade: React.FC<{
  look?: Look | null;
  transitions?: TransitionPlan[] | null;
}> = ({ look, transitions }) => {
  const frame = useCurrentFrame();
  const vignette = look?.adjust?.vignette ?? 0;

  let dip: { color: string; opacity: number } | null = null;
  for (const t of transitions ?? []) {
    const half = Math.max(2, t.frames / 2);
    const d = Math.abs(frame - t.at_frame);
    if (d <= half) {
      const ramp = 1 - d / half;                    // 0 at edges → 1 on the cut
      if (t.style === "flash") {
        dip = { color: "#fff", opacity: ramp * 0.9 };
      } else {
        dip = { color: t.style === "fade_white" ? "#fff" : "#000", opacity: ramp };
      }
      break;
    }
  }

  if (!vignette && !dip) return null;
  return (
    <>
      {vignette > 0 && (
        <AbsoluteFill style={{
          pointerEvents: "none",
          background: `radial-gradient(ellipse at center, transparent ${62 - vignette * 20}%, rgba(0,0,0,${0.55 * vignette}) 100%)`,
        }} />
      )}
      {dip && (
        <AbsoluteFill style={{ pointerEvents: "none", background: dip.color, opacity: dip.opacity }} />
      )}
    </>
  );
};
