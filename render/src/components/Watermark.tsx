import React from "react";
import { AbsoluteFill } from "remotion";
import { FONTS } from "./Captions";

// Build 54: the free-tier "powered by Yunicorn" badge. Mounted as the LAST visual
// sibling in every composition (after EndCard) so it rides above the whole video,
// including the outro takeover. Deliberately self-contained: the mark is inline SVG
// (a minimal unicorn head — crescent skull + horn), no remote asset to fetch or
// break on Lambda. Bottom-left, above the progress bar's band, subtle but legible.
const Mark: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
    {/* horn */}
    <path d="M19.5 3 L23.5 12.5 L16.8 10.8 Z" fill="white" opacity={0.95} />
    {/* head: crescent skull + muzzle */}
    <path
      d="M9 27 C7.5 22 8 16.5 11.5 13 C14.5 10 19 9.5 22.5 11.5 C20 12.5 18 14 17 16.5
         C19.5 15.5 22.5 15.8 24.5 17.5 C23 18.2 21.8 19.2 21 20.8 C19.8 23.4 17.5 25.5 14.5 26.4
         C12.7 27 10.8 27.2 9 27 Z"
      fill="white" opacity={0.95}
    />
    {/* eye */}
    <circle cx="14.2" cy="17.6" r="1.1" fill="rgba(8,8,12,0.9)" />
  </svg>
);

export const Watermark: React.FC = () => (
  <AbsoluteFill style={{ pointerEvents: "none" }}>
    <div style={{
      // Build 55 audit: bottom 96 sat deep inside the platform dead zone (layout.json
      // SAFE_BOTTOM_PX = 320 — TikTok's caption/sound chrome) and would render mostly
      // covered. 336 = just above the safe boundary + the 4px progress bar.
      position: "absolute", left: 40, bottom: 336,
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 18px 10px 12px", borderRadius: 999,
      background: "rgba(8,8,12,0.38)",
      backdropFilter: "blur(6px)",
    }}>
      <Mark size={30} />
      <span style={{
        fontFamily: FONTS.inter, fontSize: 24, fontWeight: 600,
        color: "rgba(255,255,255,0.92)", letterSpacing: 0.3,
        textShadow: "0 1px 6px rgba(0,0,0,0.5)",
      }}>
        powered by Yunicorn
      </span>
    </div>
  </AbsoluteFill>
);
