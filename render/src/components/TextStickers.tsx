import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import { CaptionOptions, CaptionStyle, CaptionWord, Overlay } from "../types";
import { FONTS } from "./Captions";
import { LAYOUT, captionBandRect, clampSticker, resolveStickerNudge } from "../layout";

// Rough conservative block-height estimate per caption style, used ONLY to size
// the caption "keep-clear" band for sticker collision avoidance (formatting fix
// #5) — not a pixel-exact layout (Captions.tsx owns that). Slightly generous is
// the safe direction here: a sticker nudged a bit further than strictly
// necessary beats one that clips under real caption text.
const CAPTION_BAND_HEIGHT_PX: Record<CaptionStyle, number> = {
  "clean": 90, "bold-word": 190, "karaoke": 95,
};

// Free-position text stickers (the TikTok text tool): style-agnostic — every
// composition renders them, unlike the style-gated text_card slab. Position is a
// fraction of frame size (center anchor), sized by `scale` against a 64px base,
// rotated, colored, with an optional dark label plate. Pop-in over ~5 frames.
export const TextStickers: React.FC<{
  overlays: Overlay[];
  captions?: CaptionWord[];
  captionStyle?: CaptionStyle;
  captionOptions?: CaptionOptions | null;
}> = ({ overlays, captions, captionStyle, captionOptions }) => {
  const frame = useCurrentFrame();
  const active = overlays.filter(
    (o) => o.type === "text_sticker" && frame >= o.frame_in && frame < o.frame_out && o.text
  );
  if (active.length === 0) return null;

  // Formatting fix #5: captions always win priority — never moved. A sticker
  // landing in the caption's band nudges to the nearest clear side, or shrinks
  // if there's no room on either side. Conservative: if the plan has ANY
  // captions authored, the band is treated as occupied for the whole clip
  // (not frame-accurate to Captions.tsx's own hide/silence-gap logic) — a
  // sticker avoiding a band that happens to be empty right now is a much safer
  // failure mode than one that clips under caption text that IS showing.
  const hasCaptions = !!captions && captions.length > 0;
  const band = hasCaptions
    ? captionBandRect(captionOptions?.position ?? "bottom", captionOptions?.pos_y ?? null,
                      CAPTION_BAND_HEIGHT_PX[captionStyle ?? "clean"])
    : null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {active.map((o, i) => {
        const pop = interpolate(frame, [o.frame_in, o.frame_in + 5], [0.6, 1],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const fontKey = (o.font in FONTS ? o.font : "inter") as keyof typeof FONTS;
        const clamped = clampSticker(o.pos_x ?? 0.5, o.pos_y ?? 0.5);
        // ~half the sticker's own height as a fraction of frame height, for the
        // nudge math — scale-aware so a bigger sticker keeps more clearance.
        const stickerHalfH = (64 * (o.scale || 1) * 1.3) / 2 / LAYOUT.FRAME_H;
        const nudge = band ? resolveStickerNudge(clamped.y, stickerHalfH, band) : { y: clamped.y, shrink: 1 };
        return (
          <div key={i} style={{
            position: "absolute",
            left: `${clamped.x * 100}%`,
            top: `${nudge.y * 100}%`,
            transform: `translate(-50%, -50%) rotate(${o.rotation ?? 0}deg) scale(${pop * nudge.shrink})`,
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
