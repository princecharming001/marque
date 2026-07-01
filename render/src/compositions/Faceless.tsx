import React from "react";
import { AbsoluteFill, Video } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const Faceless: React.FC<CompositionProps> = ({ sourceUrl, edl }) => (
  <AbsoluteFill style={{ background: "#000" }}>
    {sourceUrl ? (
      <Video src={sourceUrl} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    ) : (
      <div style={{ flex: 1, background: "#222", display: "flex", alignItems: "center",
        justifyContent: "center", color: "#888", fontSize: 40 }}>B-roll</div>
    )}
    {edl && <Captions captions={edl.captions} totalFrames={720} />}
  </AbsoluteFill>
);
