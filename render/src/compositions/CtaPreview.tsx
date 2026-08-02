import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate, Sequence } from "remotion";
import { resolveCta } from "../components/cta/registry";
import { FONTS, CREAM_DIM } from "../components/cta/tokens";

// The stage every CTA template is previewed on for the in-app picker.
//
// Deliberately NOT a real talking-head take: a real face makes people judge the person,
// and a licensed clip would have to ship in the bundle. Instead a neutral branded stage —
// a warm ink gradient, a softly swaying silhouette that reads as "someone talking", and a
// live caption line in the real caption band — so the ONLY thing that differs between the
// 20 preview clips is the CTA treatment itself.
//
// Timeline (150f = 5s @30fps): 0-30 establishes the stage; the CTA plays 30-150, the same
// 4s window it would get on a real reel.
const STAGE_FRAMES = 30;

export const CtaPreview: React.FC<{
  styleId: string; text: string; handle: string; logoUrl: string | null;
}> = ({ styleId, text, handle, logoUrl }) => {
  const frame = useCurrentFrame();
  const { Comp, layout } = resolveCta(styleId);
  const total = 150;
  const ctaFrames = total - STAGE_FRAMES;

  // Deterministic sway so the stage never sits frozen (same doctrine as the ambient
  // layer on the classic card).
  const sway = Math.sin(frame * 0.035) * 10;
  const breathe = 1 + Math.sin(frame * 0.028) * 0.012;

  return (
    <AbsoluteFill style={{ backgroundColor: "#141310" }}>
      {/* stage: warm gradient + vignette */}
      <AbsoluteFill style={{
        background:
          "radial-gradient(ellipse 80% 60% at 50% 32%, #2A2622 0%, #1A1714 55%, #100E0C 100%)",
      }} />
      {/* silhouette — head + shoulders, soft-edged so it reads as a person out of focus */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div style={{
          width: 620, height: 980,
          transform: `translateX(${sway}px) scale(${breathe})`,
          transformOrigin: "bottom center",
          filter: "blur(2px)",
        }}>
          <svg viewBox="0 0 620 980" width="620" height="980">
            <defs>
              <linearGradient id="sil" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#4A423A" />
                <stop offset="100%" stopColor="#2A2521" />
              </linearGradient>
            </defs>
            {/* head */}
            <ellipse cx="310" cy="300" rx="150" ry="180" fill="url(#sil)" />
            {/* shoulders / torso */}
            <path d="M310 470 C 470 470 560 590 580 760 L 580 980 L 40 980 L 40 760 C 60 590 150 470 310 470 Z"
                  fill="url(#sil)" />
          </svg>
        </div>
      </AbsoluteFill>

      {/* a live caption line in the real band, so overlay CTAs visibly clear it */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start" }}>
        <div style={{
          position: "absolute", top: 0.60 * 1920, width: "100%", textAlign: "center",
          fontFamily: FONTS.inter, fontSize: 52, fontWeight: 600, color: "white",
          textShadow: "0 3px 14px rgba(0,0,0,0.7)",
          opacity: interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" }),
        }}>
          and that's the whole trick
        </div>
      </AbsoluteFill>

      {/* the template under test */}
      <Sequence from={STAGE_FRAMES} durationInFrames={ctaFrames} layout="none">
        <Comp text={text} handle={handle} logoUrl={logoUrl}
              showHandle={true} durationInFrames={ctaFrames} />
      </Sequence>

      {/* tiny label so a screenshot of the bank is self-describing (preview only) */}
      <div style={{
        position: "absolute", left: 0, right: 0, bottom: 48, textAlign: "center",
        fontFamily: FONTS.inter, fontSize: 26, fontWeight: 600, letterSpacing: 1.5,
        color: CREAM_DIM, textTransform: "uppercase",
        opacity: layout === "overlay" ? 0.45 : 0,   // hidden once a tail card takes over
      }}>
        {styleId.replace(/_/g, " ")}
      </div>
    </AbsoluteFill>
  );
};
