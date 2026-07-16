import React from "react";
import { useCurrentFrame, interpolate } from "remotion";
import { LAYOUT, cardFit } from "../layout";
import { FONTS } from "./Captions";

// Renders a `text_card` overlay on the FACE styles (TalkingHead / BrollCutaway). Previously text
// cards rendered only in GreenScreen/DuetSplit, so the literal-need fallback ("a text card beats a
// wrong clip") was invisible on exactly the styles that use b-roll — those cues silently vanished.
// The card sits in the upper region (clear of the platform chrome up top and the face + caption
// band below), a white rounded slab with a short scale-pop entrance. Shows only during its window.
export const TextCardOverlay: React.FC<{ overlays?: any[] }> = ({ overlays }) => {
  const frame = useCurrentFrame();
  const cards = (overlays ?? []).filter((o) => o.type === "text_card");
  const active = cards.find((o) => frame >= o.frame_in && frame < o.frame_out);
  if (!active || !active.text) return null;

  const usablePx = LAYOUT.FRAME_W - 80;                    // 40px inset each side
  const fit = cardFit(String(active.text), 44, usablePx, LAYOUT.CARD_MAX_LINES, LAYOUT.CARD_MIN_FONT);
  const pop = interpolate(frame, [active.frame_in, active.frame_in + 3], [0.7, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <div style={{
      position: "absolute", left: 40, right: 40, top: 160, zIndex: 3,
      display: "flex", justifyContent: "center",
      transform: `scale(${pop})`, transformOrigin: "top center",
    }}>
      <div style={{
        background: "white", borderRadius: 22, padding: "26px 40px", maxWidth: "100%",
        fontSize: fit.fontSize, color: "#111", fontFamily: FONTS.inter, fontWeight: 800,
        textAlign: "center", lineHeight: 1.22, boxShadow: "0 16px 46px rgba(0,0,0,0.4)",
        display: "-webkit-box", WebkitLineClamp: LAYOUT.CARD_MAX_LINES,
        WebkitBoxOrient: "vertical", overflow: "hidden",
      }}>{active.text}</div>
    </div>
  );
};
