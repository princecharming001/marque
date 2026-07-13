import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { usePunchScale } from "../components/PunchZoom";
import { CompositionProps } from "../types";

// Single-speaker cut with optional punch-in zoom. The cut track (CutVideo) trims the
// source per the plan's clips; the punch-in scale is applied to a wrapper at the
// composition level (global output frame) rather than inside the Series, since overlay
// coords are output-timeline coords.
export const TalkingHead: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const scale = usePunchScale(edl?.overlays);

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
