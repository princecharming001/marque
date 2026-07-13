import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { Grade } from "../components/Grade";
import { BrollLayer } from "../components/BrollLayer";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Faceless / voiceover: NO face on screen — full-frame b-roll under the creator's
// voiceover + kinetic captions. The creator's cut track still renders (opacity 0) purely
// to carry the voiceover AUDIO; the b-roll layer covers it visually. The ground under
// uncovered moments is a soft dark gradient, NOT flat black: with zero resolved b-roll
// (Pexels down / no matches) the whole video is this ground + captions, and flat #000
// read as a broken render rather than an intentional text-forward look.
export const Faceless: React.FC<CompositionProps> = ({ sourceUrl, edl }) => (
  <AbsoluteFill style={{ background: "linear-gradient(160deg, #14141c 0%, #0e0e14 55%, #191922 100%)" }}>
    <AbsoluteFill style={{ opacity: 0 }}>
      <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
    </AbsoluteFill>
    {edl && <BrollLayer broll={edl.broll} />}
    {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
    {edl && <TextStickers overlays={edl.overlays} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      <AudioMix audio={edl?.audio} />
  </AbsoluteFill>
);
