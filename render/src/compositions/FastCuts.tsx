import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Punchy cut track with a 2-frame white flash at every real cut boundary. The boundaries
// are the OUTPUT-timeline starts of each clip (cumulative clip durations), so the flash
// lands exactly where the footage jumps.
export const FastCuts: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const clips = edl?.clips ?? [];

  let acc = 0;
  const cutStarts: number[] = [];
  for (const c of clips) {
    if (acc > 0) cutStarts.push(acc);   // skip the very first (frame 0)
    acc += Math.max(1, c.src_out - c.src_in);
  }
  const flashing = cutStarts.some((s) => frame >= s && frame < s + 2);

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <CutVideo sourceUrl={sourceUrl} clips={clips} />
      {flashing && (
        <div style={{ position: "absolute", inset: 0, background: "white", opacity: 0.18 }} />
      )}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} />}
    </AbsoluteFill>
  );
};
