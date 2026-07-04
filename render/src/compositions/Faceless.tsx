import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Faceless / voiceover cut — the trimmed source fills the frame under big captions.
export const Faceless: React.FC<CompositionProps> = ({ sourceUrl, edl }) => (
  <AbsoluteFill style={{ background: "#000" }}>
    <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} />
    {edl && <Captions captions={edl.captions} style={edl.caption_style} />}
  </AbsoluteFill>
);
