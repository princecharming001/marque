import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

// Reaction / green-screen layout: a reference text card on the backdrop with the cut
// speaker track keyed over the right side. The text card shows only during its overlay
// window (output coords); if the plan has no text_card overlay it shows throughout.
export const GreenScreen: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const textCards = (edl?.overlays ?? []).filter((o) => o.type === "text_card");
  const activeCard = textCards.length
    ? textCards.find((o) => frame >= o.frame_in && frame < o.frame_out)
    : { text: "Reference post" } as { text: string };

  return (
    <AbsoluteFill style={{ background: "#1a1a2e" }}>
      <div style={{ position: "absolute", inset: 0, background: "#0f3460",
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        {activeCard && (
          <div style={{ background: "white", borderRadius: 16, padding: "20px 32px",
            maxWidth: "80%", fontSize: 32, color: "#111", fontFamily: "system-ui",
            fontWeight: 600, textAlign: "center" }}>{activeCard.text || "Reference post"}</div>
        )}
      </div>
      <div style={{ position: "absolute", right: 0, top: 0, width: "60%", height: "100%",
        mixBlendMode: "multiply", overflow: "hidden" }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} />
      </div>
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
