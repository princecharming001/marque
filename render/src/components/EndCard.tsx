import React from "react";
import { AbsoluteFill, Img, Sequence, useCurrentFrame, interpolate } from "remotion";
import { FONTS } from "./Captions";
import { EndCardPlan } from "../types";

// P4: a tail-of-video CTA card — full-screen dark takeover with the creator's
// CTA text, mounted at the very end of every composition once edl.end_card is
// set. build_render_plan has already extended total_frames to cover its
// `frames`, so this Sequence just occupies that already-reserved tail window.
//
// `show_handle` renders a plain decorative accent, NOT a fabricated handle
// string — there's no real @handle in the render plan today, and a clean
// accent beats fake copy (same rationale as GreenScreen's removed placeholder
// reference card and SplitThree's removed hardcoded labels).
export const EndCard: React.FC<{ endCard: EndCardPlan | null | undefined }> = ({ endCard }) => {
  if (!endCard) return null;
  return (
    <Sequence from={endCard.start_frame} durationInFrames={endCard.frames} layout="none">
      <EndCardContent endCard={endCard} />
    </Sequence>
  );
};

const FADE_IN_FRAMES = 15;

const EndCardContent: React.FC<{ endCard: EndCardPlan }> = ({ endCard }) => {
  const frame = useCurrentFrame();   // local to the Sequence: 0 at start_frame
  const opacity = interpolate(frame, [0, FADE_IN_FRAMES], [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // Build 54 (outro builder): an uploaded logo renders above the CTA; a real @handle
  // renders under it. The decorative accent bar remains the no-handle fallback.
  const logo = (endCard.logo_url || "").match(/\.(png|jpe?g|webp|gif)(\?|$)/i) ? endCard.logo_url : null;
  const handle = (endCard.handle || "").trim();
  return (
    <AbsoluteFill style={{
      background: "rgba(8,8,12,0.94)", opacity,
      alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 28,
    }}>
      {logo && (
        <Img src={logo!} style={{
          width: 168, height: 168, objectFit: "contain", borderRadius: 32,
          filter: "drop-shadow(0 6px 24px rgba(0,0,0,0.5))",
        }} />
      )}
      <div style={{
        fontFamily: FONTS.inter, fontSize: 56, fontWeight: 800, color: "white",
        textAlign: "center", padding: "0 80px", lineHeight: 1.2,
        textShadow: "0 4px 20px rgba(0,0,0,0.6)",
      }}>{endCard.text}</div>
      {handle ? (
        <div style={{
          fontFamily: FONTS.inter, fontSize: 34, fontWeight: 600,
          color: "rgba(255,255,255,0.75)", letterSpacing: 0.5,
        }}>{handle}</div>
      ) : endCard.show_handle && (
        <div style={{ width: 60, height: 4, borderRadius: 2, background: "rgba(255,255,255,0.55)" }} />
      )}
    </AbsoluteFill>
  );
};
