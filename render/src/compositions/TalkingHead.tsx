import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Single-speaker cut with optional punch-in zoom. The cut track (CutVideo) trims the
// source per the plan's clips; the punch-in scale is applied to a wrapper at the
// composition level (global output frame) rather than inside the Series, since overlay
// coords are output-timeline coords.
export const TalkingHead: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const punchIn = edl?.overlays.find(
    (o) => o.type === "punch_in" && frame >= o.frame_in && frame < o.frame_out
  );
  const scale = punchIn ? punchIn.scale : 1.0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ transform: `scale(${scale})`, transition: "transform 0.1s" }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} />
      </AbsoluteFill>
      {edl && <Captions captions={edl.captions} style={edl.caption_style} />}
    </AbsoluteFill>
  );
};
