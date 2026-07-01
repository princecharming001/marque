import React from "react";
import { AbsoluteFill, Video, useCurrentFrame } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const FastCuts: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const activeSegment = edl?.segments.findIndex(
    (s) => frame >= s.src_in && frame < s.src_out
  ) ?? 0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {sourceUrl && (
        <Video key={activeSegment} src={sourceUrl}
          style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      )}
      {edl?.segments.some((s) => s.src_in === frame) && (
        <div style={{ position: "absolute", inset: 0, background: "white", opacity: 0.15 }} />
      )}
      {edl && <Captions captions={edl.captions} totalFrames={720} />}
    </AbsoluteFill>
  );
};
