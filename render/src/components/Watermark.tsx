import React from "react";
import { AbsoluteFill } from "remotion";
import { FONTS } from "./Captions";
import { LAYOUT } from "../layout";

// Build 54 (v2 in build 62): the free-tier "powered by the yunicorn app" badge.
// Mounted as the LAST visual sibling in every composition (after EndCard) so it
// rides above the whole video, including the outro takeover. Deliberately
// self-contained: the mark is inline SVG, no remote asset to fetch or break on
// Lambda. Geometry lives in layout.json (WATERMARK_*) so eval/layout_qc.py's
// collision rect derives from the same numbers instead of drifting.
//
// The mark: a side-profile unicorn head. What makes it READ as a unicorn rather
// than a blob (build-62 owner feedback): an upright triangular EAR distinct from
// the horn, a slender notched horn, a squared muzzle with a jaw underside, and a
// scalloped mane cut into the back of the neck.
const Mark: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
    {/* Chunky right-facing unicorn head — bold solid silhouette so the three
        tells (horn, upright ear, stepped muzzle) survive at 30px. */}
    <path
      d="M6 30
         L8 18
         C8.4 14.6 10.2 11.2 13 9.2
         L15.1 3.6 L17.2 8.2
         L18.4 8.2
         L25.6 1.2 L21.2 9.6
         C22.4 10.4 23.4 11.4 24.2 12.8
         L28.6 15.2 L29.4 17.6 L28.4 19.8
         L24.6 20.6
         C22.8 21.8 20.4 22.4 18 22
         C17.4 24.8 15.6 27.4 12.8 29
         L12 30 Z"
      fill="white" opacity={0.96}
    />
    {/* eye */}
    <circle cx="21.6" cy="14.2" r="1.35" fill="rgba(8,8,12,0.92)" />
    {/* nostril on the muzzle */}
    <circle cx="27.2" cy="17.6" r="0.75" fill="rgba(8,8,12,0.6)" />
    {/* mane: dark notches cut into the neck so they read as texture, not blob */}
    <path d="M9.3 17.5 C10.6 16.2 11.2 14.6 11.3 13.0 M10.6 21.5 C12.2 20.4 13.1 18.9 13.4 17.2"
      stroke="rgba(8,8,12,0.35)" strokeWidth={1.1} strokeLinecap="round" />
  </svg>
);

export type WatermarkPos = "bottom_left" | "bottom_right";

export const Watermark: React.FC<{ pos?: WatermarkPos }> = ({ pos = "bottom_left" }) => {
  // Both placements sit at the platform legal floor: layout.json SAFE_BOTTOM_PX
  // = 320 is TikTok's caption/sound chrome; WATERMARK_BOTTOM_PX (336) = just
  // above that boundary + the 4px progress bar. A top placement was rejected —
  // it lands inside the hook-title clamp band. NOTE: compositions mount
  // <Watermark /> with the default; bottom_right exists for the owner's
  // screenshot comparison, and adopting it means updating WATERMARK_LEFT_PX
  // semantics + eval/layout_qc.py's rect together (the collision tripwire
  // models the DEFAULT placement only).
  const place: React.CSSProperties =
    pos === "bottom_right"
      ? { right: LAYOUT.WATERMARK_LEFT_PX, bottom: LAYOUT.WATERMARK_BOTTOM_PX }
      : { left: LAYOUT.WATERMARK_LEFT_PX, bottom: LAYOUT.WATERMARK_BOTTOM_PX };
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{
        position: "absolute", ...place,
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
          powered by the yunicorn app
        </span>
      </div>
    </AbsoluteFill>
  );
};
