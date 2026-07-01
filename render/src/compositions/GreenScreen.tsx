import React from "react";
import { AbsoluteFill, Video } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const GreenScreen: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const textCard = edl?.overlays.find((o) => o.type === "text_card");
  return (
    <AbsoluteFill style={{ background: "#1a1a2e" }}>
      <div style={{ position: "absolute", inset: 0, background: "#0f3460",
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        {textCard && (
          <div style={{ background: "white", borderRadius: 16, padding: "20px 32px",
            maxWidth: "80%", fontSize: 32, color: "#111", fontFamily: "system-ui",
            fontWeight: 600, textAlign: "center" }}>{textCard.text || "Reference post"}</div>
        )}
      </div>
      {sourceUrl && (
        <Video src={sourceUrl} style={{ width: "60%", height: "100%", objectFit: "cover",
          position: "absolute", right: 0, mixBlendMode: "multiply" }} />
      )}
      {edl && <Captions captions={edl.captions} totalFrames={720} />}
    </AbsoluteFill>
  );
};
