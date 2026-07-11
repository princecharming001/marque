import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { Overlay } from "../types";
import { FONTS } from "./Captions";

// Free-position text stickers (the TikTok text tool): style-agnostic — every
// composition renders them, unlike the style-gated text_card slab. Position is a
// fraction of frame size (center anchor), sized by `scale` against a 64px base,
// rotated, colored, with an optional dark label plate. Pop-in over ~5 frames.
export const TextStickers: React.FC<{ overlays: Overlay[] }> = ({ overlays }) => {
  const frame = useCurrentFrame();
  const active = overlays.filter(
    (o) => o.type === "text_sticker" && frame >= o.frame_in && frame < o.frame_out && o.text
  );
  if (active.length === 0) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {active.map((o, i) => {
        const pop = interpolate(frame, [o.frame_in, o.frame_in + 5], [0.6, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const fontKey = (o.font in FONTS ? o.font : "inter") as keyof typeof FONTS;
        return (
          <div key={i} style={{
            position: "absolute",
            left: `${(o.pos_x ?? 0.5) * 100}%`,
            top: `${(o.pos_y ?? 0.5) * 100}%`,
            transform: `translate(-50%, -50%) rotate(${o.rotation ?? 0}deg) scale(${pop})`,
            maxWidth: "86%",
          }}>
            <span style={{
              fontFamily: FONTS[fontKey],
              fontSize: 64 * (o.scale || 1),
              fontWeight: fontKey === "archivo" ? 400 : 800,
              lineHeight: 1.15,
              color: o.color ?? "white",
              textAlign: "center",
              display: "inline-block",
              padding: o.bg === "box" ? "10px 26px" : 0,
              background: o.bg === "box" ? "rgba(0,0,0,0.65)" : "transparent",
              borderRadius: o.bg === "box" ? 18 : 0,
              textShadow: o.bg === "box" ? "none" : "0 3px 14px rgba(0,0,0,0.85)",
              WebkitTextStroke: o.bg === "box" ? undefined : "2px rgba(0,0,0,0.35)",
            }}>{o.text}</span>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
