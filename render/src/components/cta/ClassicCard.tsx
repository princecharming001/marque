import React from "react";
import { AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
         spring, interpolate, Easing } from "remotion";
import { FONTS, CtaTemplateProps } from "./tokens";

// The original end card (build 56), moved here VERBATIM as template id "classic" so old
// plans and the auto path render byte-identically. Research notes from the original:
//  • Backdrop: fast eased fade + a SLOW ambient layer (radial accent drifting, ~3.5% zoom
//    across the card's life) so the card never sits static (Ken-Burns doctrine).
//  • Logo: spring pop with a calm ~3% overshoot (damping 14 over the bouncier default).
//  • Headline: per-WORD rise (20px) + fade + blur-to-sharp on a 2-frame stagger (~67ms,
//    inside the 50-200ms convention), each word on the canonical smooth spring (damping 200).
//  • Accent underline: width draw-in on Material 3's emphasized-decelerate bezier.
//  • Handle: late rise+fade, completing the top-to-bottom offset-and-delay hierarchy.
export const ClassicCard: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, logoUrl, showHandle, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(30, durationInFrames);
  const k = Math.min(1, total / 75);

  const logo = (logoUrl || "").match(/\.(png|jpe?g|webp|gif)(\?|$)/i) ? logoUrl : null;
  const handle = (rawHandle || "").trim();
  const words = text.split(/\s+/).filter(Boolean);

  const bgOpacity = interpolate(frame, [0, 12 * k], [0, 1],
    { easing: Easing.out(Easing.cubic), extrapolateRight: "clamp" });
  const ambientScale = interpolate(frame, [0, total], [1, 1.035]);
  const driftX = Math.sin(frame * 0.021) * 26;
  const driftY = Math.cos(frame * 0.017) * 18;

  const logoAt = Math.round(3 * k);
  const wordsAt = Math.round((logo ? 10 : 5) * k);
  const wordStep = Math.max(1, Math.round(2 * k));
  const wordsDone = wordsAt + words.length * wordStep + 9;
  const ruleAt = Math.min(wordsDone, Math.round(total * 0.45));
  const handleAt = ruleAt + Math.round(4 * k);

  const logoSpring = spring({ frame, fps, delay: logoAt,
    config: { damping: 14, stiffness: 120, mass: 1 } });
  const logoScale = interpolate(logoSpring, [0, 1], [0.82, 1]);
  const logoOpacity = interpolate(frame, [logoAt, logoAt + 8 * k], [0, 1],
    { easing: Easing.out(Easing.cubic), extrapolateRight: "clamp" });

  const ruleW = interpolate(frame, [ruleAt, ruleAt + 13 * k], [0, 64],
    { easing: Easing.bezier(0.05, 0.7, 0.1, 1.0),
      extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const handleT = interpolate(frame, [handleAt, handleAt + 10 * k], [0, 1],
    { easing: Easing.bezier(0.05, 0.7, 0.1, 1.0),
      extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ opacity: bgOpacity, overflow: "hidden" }}>
      <AbsoluteFill style={{ background: "rgba(8,8,12,0.94)" }} />
      <AbsoluteFill style={{
        transform: `scale(${ambientScale})`,
        background: `radial-gradient(ellipse 90% 55% at calc(50% + ${driftX}px) calc(38% + ${driftY}px), rgba(255,255,255,0.075), rgba(255,255,255,0) 68%)`,
      }} />
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 28,
      }}>
        {logo && (
          <Img src={logo!} style={{
            width: 168, height: 168, objectFit: "contain", borderRadius: 32,
            opacity: logoOpacity,
            transform: `scale(${logoScale})`,
            filter: "drop-shadow(0 6px 24px rgba(0,0,0,0.5))",
          }} />
        )}
        <div style={{
          fontFamily: FONTS.inter, fontSize: 56, fontWeight: 800, color: "white",
          textAlign: "center", padding: "0 80px", lineHeight: 1.2,
          textShadow: "0 4px 20px rgba(0,0,0,0.6)",
          display: "flex", flexWrap: "wrap", justifyContent: "center", columnGap: 14,
        }}>
          {words.map((w, i) => {
            const at = wordsAt + i * wordStep;
            const s = spring({ frame, fps, delay: at, config: { damping: 200 } });
            const wordOpacity = interpolate(frame, [at, at + 8 * k], [0, 1],
              { easing: Easing.out(Easing.cubic), extrapolateRight: "clamp" });
            const blurPx = interpolate(s, [0, 1], [7, 0]);
            return (
              <span key={i} style={{
                display: "inline-block",
                opacity: wordOpacity,
                transform: `translateY(${interpolate(s, [0, 1], [20, 0])}px)`,
                filter: blurPx > 0.15 ? `blur(${blurPx}px)` : undefined,
              }}>{w}</span>
            );
          })}
        </div>
        {handle ? (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 34, fontWeight: 600,
            color: "rgba(255,255,255,0.75)", letterSpacing: 0.5,
            opacity: handleT,
            transform: `translateY(${(1 - handleT) * 14}px)`,
          }}>{handle}</div>
        ) : showHandle && (
          <div style={{ width: ruleW, height: 4, borderRadius: 2, background: "rgba(255,255,255,0.55)" }} />
        )}
        {handle.length > 0 && (
          <div style={{ width: ruleW, height: 4, borderRadius: 2, background: "rgba(255,255,255,0.4)" }} />
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
