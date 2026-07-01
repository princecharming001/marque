import React from "react";
import { AbsoluteFill, Video, useVideoConfig, useCurrentFrame } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const TalkingHead: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const punchIn = edl?.overlays.find(
    (o) => o.type === "punch_in" && frame >= o.src_in && frame < o.src_out
  );
  const scale = punchIn ? punchIn.scale : 1.0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {sourceUrl ? (
        <Video src={sourceUrl} style={{ width: "100%", height: "100%", objectFit: "cover",
          transform: `scale(${scale})`, transition: "transform 0.1s" }} />
      ) : (
        <div style={{ flex: 1, background: "#111", display: "flex", alignItems: "center",
          justifyContent: "center", color: "#888", fontSize: 40 }}>Preview</div>
      )}
      {edl && <Captions captions={edl.captions} totalFrames={720} />}
    </AbsoluteFill>
  );
};
