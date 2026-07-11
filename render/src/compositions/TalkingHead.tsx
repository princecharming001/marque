import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
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
  // Ramp over ~8 frames with interpolate — a CSS `transition` is a no-op in a
  // frame-by-frame render, so it would snap the zoom instead of easing it.
  // P4.2: ease the EXIT too (was a hard snap back to 1.0 at frame_out). r clamps the
  // ramp so short windows still get a symmetric ease without colliding keyframes.
  const scale = punchIn
    ? (() => {
        const r = Math.min(8, (punchIn.frame_out - punchIn.frame_in) / 2);
        return interpolate(
          frame,
          [punchIn.frame_in, punchIn.frame_in + r, punchIn.frame_out - r, punchIn.frame_out],
          [1, punchIn.scale, punchIn.scale, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
        );
      })()
    : 1.0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      </AbsoluteFill>
      {edl && <BrollLayer broll={edl.broll} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
