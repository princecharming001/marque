import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { Grade } from "../components/Grade";
import { BrollLayer } from "../components/BrollLayer";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Talking-head A-roll is the spine (full-frame, its audio continuous). At each cue, a
// b-roll clip cuts in FULL-FRAME on top for its window, then hard-cuts back to the face —
// a cutaway, not a PiP or split. The b-roll is muted so the creator's voice never breaks
// (a J-cut: the picture changes, the audio doesn't). Captions stay on top of everything.
export const BrollCutaway: React.FC<CompositionProps> = ({ sourceUrl, edl }) => (
  <AbsoluteFill style={{ background: "#000" }}>
    <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} />
    {edl && <BrollLayer broll={edl.broll} />}
    {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
    {edl && <TextStickers overlays={edl.overlays} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      <AudioMix audio={edl?.audio} />
  </AbsoluteFill>
);
