import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Punchy cut track with a 2-frame white flash at every real cut boundary. The boundaries
// are the OUTPUT-timeline starts of each clip (cumulative clip durations), so the flash
// lands exactly where the footage jumps.
//
// G10 (verified, not a bug): this cumulative-sum formula (Math.max(1, src_out-src_in)
// per clip, running total) is IDENTICAL to CutVideo.tsx's outCursor/outStart
// computation — traced by hand against a degenerate zero-length clip
// ({src_in:100,src_out:100}) and confirmed byte-identical boundaries, including the
// "skip pushing acc==0" guard correctly excluding only the true frame-0 start (not a
// cut) while still counting a degenerate clip's own forced 1-frame width toward
// subsequent boundaries. If you change ONE of these two formulas, change the other
// identically or the flash will visibly drift from the actual cut.
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
      <CutVideo sourceUrl={sourceUrl} clips={clips} volumeRanges={edl?.audio?.volume_ranges} />
      {flashing && (
        <div style={{ position: "absolute", inset: 0, background: "white", opacity: 0.18 }} />
      )}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
