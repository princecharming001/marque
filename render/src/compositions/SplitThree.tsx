import React from "react";
import { AbsoluteFill, Video, useCurrentFrame } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const SplitThree: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const boundaries = edl?.layout.panel_boundaries ?? [240, 480];
  const panel = frame < boundaries[0] ? 0 : frame < boundaries[1] ? 1 : 2;
  const labels = ["Solution 1", "Solution 2", "Solution 3 ✓"];

  return (
    <AbsoluteFill style={{ background: "#000", flexDirection: "column" }}>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{
          flex: 1, position: "relative", borderBottom: i < 2 ? "2px solid #333" : "none",
          opacity: panel === i ? 1 : 0.4, transition: "opacity 0.3s",
        }}>
          {sourceUrl && <Video src={sourceUrl}
            style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
          <div style={{ position: "absolute", top: 8, left: 12, color: "white",
            fontFamily: "system-ui", fontSize: 22, fontWeight: 700,
            textShadow: "0 1px 4px rgba(0,0,0,0.8)" }}>{labels[i]}</div>
        </div>
      ))}
      {edl && <Captions captions={edl.captions} totalFrames={720} />}
    </AbsoluteFill>
  );
};
