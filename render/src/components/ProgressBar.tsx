import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { LAYOUT, PROGRESS_BAR_HEIGHT_PX, progressFraction } from "../layout";

// P4: a thin watch-progress cue (CapCut/TikTok "how much is left" bar), opt-in via
// edl.progress_bar. Anchored just ABOVE the bottom safe area (its own bottom edge
// sits exactly at the SAFE_BOTTOM_PX boundary) so it never lands in platform UI
// chrome. Mounted BEFORE Captions in every composition (plain DOM order, no
// z-index anywhere else in this codebase) so captions always paint on top of it.
export const ProgressBar: React.FC<{ totalFrames: number }> = ({ totalFrames }) => {
  const frame = useCurrentFrame();
  const fraction = progressFraction(frame, totalFrames);
  const bottomPx = LAYOUT.FRAME_H - LAYOUT.SAFE_BOTTOM_PX;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{
        position: "absolute", left: 0, top: bottomPx - PROGRESS_BAR_HEIGHT_PX,
        width: "100%", height: PROGRESS_BAR_HEIGHT_PX, background: "rgba(255,255,255,0.18)",
      }}>
        <div style={{
          width: `${fraction * 100}%`, height: "100%", background: "rgba(255,255,255,0.9)",
        }} />
      </div>
    </AbsoluteFill>
  );
};
