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
    {/* horn: slender taper from the forehead, tip up-right */}
    <path d="M18.2 8.6 L24.6 1.4 L20.9 10.2 Z" fill="white" opacity={0.95} />
    {/* horn spiral notches */}
    <path d="M20.6 5.9 L22.6 6.8 M19.6 7.6 L21.4 8.5" stroke="rgba(8,8,12,0.35)"
      strokeWidth={0.9} strokeLinecap="round" />
    {/* head + neck: poll, upright ear, forehead stop, squared muzzle, jaw, throat,
        then a mane of three scallops cut into the back edge of the neck */}
    <path
      d="M13.2 9.8
         L15.2 5.6 L16.9 9.2
         C18.6 9.6 20.6 10.8 21.9 12.6
         C23.2 14.3 23.9 16.2 24.1 18.0
         L21.4 18.4 C21.0 19.4 20.2 20.1 19.1 20.4
         C17.9 20.7 16.7 20.5 15.8 19.8
         C15.9 21.6 15.4 23.4 14.3 24.9
         C13.1 26.5 11.3 27.7 9.2 28.3
         L8.0 24.9 L10.2 23.9 L8.6 21.3 L11.0 20.5 L9.8 17.8
         C10.2 14.6 11.3 11.7 13.2 9.8 Z"
      fill="white" opacity={0.95}
    />
    {/* eye: above the jaw, forward on the head */}
    <circle cx="17.9" cy="13.6" r="1.15" fill="rgba(8,8,12,0.9)" />
    {/* nostril on the squared muzzle */}
    <circle cx="22.6" cy="16.6" r="0.6" fill="rgba(8,8,12,0.55)" />
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
