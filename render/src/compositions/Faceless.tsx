import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { BrollLayer } from "../components/BrollLayer";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Faceless / voiceover: NO face on screen — full-frame b-roll under the creator's
// voiceover + kinetic captions. The creator's cut track still renders (opacity 0) purely
// to carry the voiceover AUDIO; the b-roll layer covers it visually. A dark ground shows
// under any moment the b-roll doesn't cover (rather than exposing the face).
export const Faceless: React.FC<CompositionProps> = ({ sourceUrl, edl }) => (
  <AbsoluteFill style={{ background: "#000" }}>
    <AbsoluteFill style={{ opacity: 0 }}>
      <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} />
    </AbsoluteFill>
    {edl && <BrollLayer broll={edl.broll} />}
    {edl && <Captions captions={edl.captions} style={edl.caption_style} />}
    <AudioMix audio={edl?.audio} />
  </AbsoluteFill>
);
