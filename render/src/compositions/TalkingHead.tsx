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
  const scale = punchIn
    ? interpolate(frame, [punchIn.frame_in, punchIn.frame_in + 8], [1, punchIn.scale],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" })
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
