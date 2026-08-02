import React from "react";
import { AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
         spring, interpolate, Easing } from "remotion";
import { INK, CREAM, CREAM_DIM, PLATE, FONTS,
         EASE_M3, SPRING_SMOOTH, SPRING_POP, CtaTemplateProps } from "./tokens";

// ---------------------------------------------------------------------------
// TAIL CARDS — full-frame cards that play AFTER the video ends (like the
// reference ClassicCard). They own the whole frame, so an AbsoluteFill
// background is correct here (unlike the overlay families).
//
// Shared doctrine, inherited from ClassicCard / the build-56 motion research:
//  • every element's own entrance lands inside 9-15 frames; only the STAGGER
//    between elements pushes the build past that,
//  • ambient motion (idle zoom / shimmer / tilt) runs an order of magnitude
//    slower than entrance motion, so the card never sits dead,
//  • fully deterministic: every motion is a pure function of `frame`, never a
//    random or wall-clock source (Lambda renders frames independently).
// ---------------------------------------------------------------------------

const M3 = Easing.bezier(EASE_M3[0], EASE_M3[1], EASE_M3[2], EASE_M3[3]);
const OUT_CUBIC = Easing.out(Easing.cubic);
const IMG_RE = /\.(png|jpe?g|webp|gif)(\?|$)/i;

/** Only accept a logo URL that actually points at a raster image (ClassicCard's rule). */
const asLogo = (u: string | null | undefined): string | null =>
  u && IMG_RE.test(u) ? u : null;

/** 0→1 on Material 3 emphasized-decelerate, clamped at both ends. */
const track = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** 0→1 on ease-out-cubic (the opacity ramp used across the library). */
const fade = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: OUT_CUBIC, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** First meaningful character for an avatar fallback ("@yunicorn" → "Y"). */
const initialOf = (handle: string, text: string): string => {
  const src = handle.replace(/^@+/, "").trim() || text.trim();
  const ch = src.charAt(0);
  return ch ? ch.toUpperCase() : "";
};

// ---------------------------------------------------------------------------
// paper_press — cream stock, ink headline, letterpress restraint.
// bg wipes up (10f, M3) · headline 0.96→1 on the smooth spring + 8f fade ·
// hairline rule draws (12f) · handle at +6f · idle scanline grain + shimmer.
// ---------------------------------------------------------------------------
export const PaperPress: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, logoUrl, showHandle, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(30, durationInFrames);

  const headline = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const logo = asLogo(logoUrl);
  const empty = headline.length === 0 && handle.length === 0;

  const wipe = track(frame, 0, 10);
  const headSpring = spring({ frame, fps, delay: 3, config: SPRING_SMOOTH });
  const headScale = interpolate(headSpring, [0, 1], [0.96, 1]);
  const headOpacity = fade(frame, 3, 11);
  const ruleT = track(frame, 9, 21);
  const handleT = track(frame, 15, 25);

  // Ambient: a very slow diagonal sheen crossing the stock + a static-feeling
  // scanline grain that steps 1px every few frames (deterministic, not noise).
  const sheenX = Math.sin(frame * 0.016) * 34;
  const sheenY = Math.cos(frame * 0.013) * 22;
  const grainStep = frame % 3;

  if (empty) return null;

  return (
    <AbsoluteFill style={{ overflow: "hidden", clipPath: `inset(${(1 - wipe) * 100}% 0 0 0)` }}>
      <AbsoluteFill style={{ background: CREAM }} />
      <AbsoluteFill style={{
        background: `radial-gradient(ellipse 80% 50% at calc(50% + ${sheenX}px) calc(34% + ${sheenY}px), rgba(255,255,255,0.85), rgba(255,255,255,0) 70%)`,
      }} />
      <AbsoluteFill style={{
        opacity: 0.5,
        transform: `translateY(${grainStep}px)`,
        background: "repeating-linear-gradient(0deg, rgba(20,19,16,0.045) 0px, rgba(20,19,16,0.045) 1px, rgba(20,19,16,0) 1px, rgba(20,19,16,0) 3px)",
      }} />
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 30,
      }}>
        {logo && (
          <Img src={logo} style={{
            width: 132, height: 132, objectFit: "contain", borderRadius: 26,
            opacity: fade(frame, 2, 12),
          }} />
        )}
        {headline.length > 0 && (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 62, fontWeight: 800, color: INK,
            textAlign: "center", padding: "0 96px", lineHeight: 1.18,
            letterSpacing: -1,
            textShadow: "0 1px 0 rgba(255,255,255,0.75)",
            opacity: headOpacity,
            transform: `scale(${headScale})`,
          }}>{headline}</div>
        )}
        {(handle.length > 0 || showHandle) && (
          <div style={{
            width: ruleT * 200, height: 2, background: "rgba(20,19,16,0.38)",
          }} />
        )}
        {handle.length > 0 && (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 34, fontWeight: 600,
            color: "rgba(20,19,16,0.66)", letterSpacing: 1.5,
            opacity: handleT,
            transform: `translateY(${(1 - handleT) * 10}px)`,
          }}>{handle}</div>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// knockout — a giant ink plate with the headline PUNCHED OUT of it. The type
// is filled by a light gradient through background-clip:text, so it reads as
// a hole cut in the plate rather than cream lettering laid on top.
// Plate slides up (12f) · letter-spacing 0.3em→0 (10f) · 1.5% idle zoom.
// ---------------------------------------------------------------------------
export const Knockout: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, showHandle, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const total = Math.max(30, durationInFrames);

  const headline = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const empty = headline.length === 0 && handle.length === 0;

  const slide = track(frame, 0, 12);
  const plateY = (1 - slide) * 100;
  const ls = interpolate(frame, [0, 10], [0.3, 0],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const zoom = interpolate(frame, [0, total], [1, 1.015]);
  const handleT = track(frame, 14, 24);

  if (empty) return null;

  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <AbsoluteFill style={{
        background: INK,
        transform: `translateY(${plateY}%) scale(${zoom})`,
      }}>
        <AbsoluteFill style={{
          alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 34,
        }}>
          {headline.length > 0 && (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 84, fontWeight: 800,
              textAlign: "center", padding: "0 72px", lineHeight: 1.08,
              textTransform: "uppercase",
              letterSpacing: `${ls}em`,
              // The knockout: a light gradient shows THROUGH the glyphs.
              backgroundImage: `linear-gradient(158deg, ${CREAM} 0%, ${CREAM_DIM} 48%, ${CREAM} 100%)`,
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              color: "transparent",
              WebkitTextFillColor: "transparent",
              opacity: slide,
            }}>{headline}</div>
          )}
          {handle.length > 0 ? (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 32, fontWeight: 600,
              color: CREAM_DIM, letterSpacing: 2,
              opacity: handleT,
              transform: `translateY(${(1 - handleT) * 10}px)`,
            }}>{handle}</div>
          ) : showHandle ? (
            <div style={{
              width: handleT * 160, height: 3, borderRadius: 2,
              background: "rgba(246,241,231,0.5)",
            }} />
          ) : null}
        </AbsoluteFill>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// follow_mimic — a platform-native mock: avatar + a "Follow" pill that taps
// itself once, late in the card, so the viewer's thumb has somewhere to go.
// Card fade 8f · avatar SPRING_POP at f4 · button rise 10f · tap-pulse
// (1 → 0.92 → 1 over 8f) at ~60% of the card's life.
// ---------------------------------------------------------------------------
export const FollowMimic: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, logoUrl, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(30, durationInFrames);

  const headline = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const logo = asLogo(logoUrl);
  const initial = initialOf(handle, headline);
  const empty = headline.length === 0 && handle.length === 0;

  const cardOpacity = fade(frame, 0, 8);
  const avatarSpring = spring({ frame, fps, delay: 4, config: SPRING_POP });
  const avatarScale = interpolate(avatarSpring, [0, 1], [0.7, 1]);
  const btnT = track(frame, 8, 18);

  const tapAt = Math.round(total * 0.6);
  const tapScale = interpolate(frame, [tapAt, tapAt + 4, tapAt + 8], [1, 0.92, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const ambient = interpolate(frame, [0, total], [1, 1.02]);

  // A short CTA ("Follow for more") IS the button label — rendering it on the button
  // and again underneath reads as a duplicated string. Only long copy gets the caption.
  const shortCta = headline.length > 0 && headline.split(/\s+/).length <= 4;
  const btnLabel = shortCta ? headline : "Follow";
  const showHeadline = headline.length > 0 && !shortCta;

  if (empty) return null;

  return (
    <AbsoluteFill style={{ opacity: cardOpacity, overflow: "hidden" }}>
      <AbsoluteFill style={{ background: PLATE }} />
      <AbsoluteFill style={{
        transform: `scale(${ambient})`,
        background: "radial-gradient(ellipse 70% 45% at 50% 40%, rgba(255,255,255,0.07), rgba(255,255,255,0) 68%)",
      }} />
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 28,
      }}>
        <div style={{
          width: 180, height: 180, borderRadius: 90, overflow: "hidden",
          background: "rgba(246,241,231,0.10)",
          border: "3px solid rgba(246,241,231,0.28)",
          display: "flex", alignItems: "center", justifyContent: "center",
          transform: `scale(${avatarScale})`,
          opacity: fade(frame, 4, 12),
        }}>
          {logo ? (
            <Img src={logo} style={{ width: 180, height: 180, objectFit: "cover" }} />
          ) : initial ? (
            <span style={{
              fontFamily: FONTS.inter, fontSize: 84, fontWeight: 800, color: CREAM,
            }}>{initial}</span>
          ) : (
            <div style={{ width: 26, height: 26, borderRadius: 13, background: CREAM_DIM }} />
          )}
        </div>

        {handle.length > 0 && (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 36, fontWeight: 700, color: CREAM,
            letterSpacing: 0.5, opacity: fade(frame, 6, 14),
          }}>{handle}</div>
        )}

        <div style={{
          padding: "22px 76px", borderRadius: 16, background: CREAM,
          fontFamily: FONTS.inter, fontSize: 42, fontWeight: 800, color: INK,
          opacity: btnT,
          transform: `translateY(${(1 - btnT) * 22}px) scale(${tapScale})`,
          boxShadow: "0 10px 34px rgba(0,0,0,0.45)",
        }}>Follow</div>

        {headline.length > 0 && (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 34, fontWeight: 600, color: CREAM_DIM,
            textAlign: "center", padding: "0 100px", lineHeight: 1.3,
            opacity: fade(frame, 14, 24),
          }}>{headline}</div>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// card_flip — a cream card hinges in on its horizontal edge (rotateX 90°→0
// under a 1200px perspective) with a shadow that ramps as it lands. Content
// staggers 2f per element; the card then holds a ~1° idle tilt.
// ---------------------------------------------------------------------------
export const CardFlip: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, logoUrl, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(30, durationInFrames);

  const headline = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const logo = asLogo(logoUrl);
  const empty = headline.length === 0 && handle.length === 0;

  const flip = track(frame, 0, 12);
  const rotX = interpolate(flip, [0, 1], [90, 0]);
  const tilt = Math.sin(frame * 0.028) * 1;           // slow idle, ±1°
  const shadowY = 10 + flip * 22;
  const shadowBlur = 20 + flip * 50;
  const shadowAlpha = 0.12 + flip * 0.35;

  // 2-frame stagger, each element's own entrance 10f.
  const logoS = spring({ frame, fps, delay: 8, config: SPRING_SMOOTH });
  const textS = spring({ frame, fps, delay: 10, config: SPRING_SMOOTH });
  const handleS = spring({ frame, fps, delay: 12, config: SPRING_SMOOTH });
  const logoOp = fade(frame, 8, 18);
  const textOp = fade(frame, 10, 20);
  const handleOp = fade(frame, 12, 22);

  const bgOpacity = fade(frame, 0, 8);
  const ambient = interpolate(frame, [0, total], [1, 1.02]);

  if (empty) return null;

  return (
    <AbsoluteFill style={{ overflow: "hidden", opacity: bgOpacity }}>
      <AbsoluteFill style={{ background: PLATE }} />
      <AbsoluteFill style={{
        transform: `scale(${ambient})`,
        background: "radial-gradient(ellipse 75% 48% at 50% 44%, rgba(255,255,255,0.08), rgba(255,255,255,0) 70%)",
      }} />
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center", perspective: 1200,
      }}>
        <div style={{
          width: 840, borderRadius: 40, background: CREAM,
          padding: "72px 64px",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 26,
          transform: `rotateX(${rotX}deg) rotateZ(${tilt * 0.35}deg)`,
          transformOrigin: "center center",
          boxShadow: `0 ${shadowY}px ${shadowBlur}px rgba(0,0,0,${shadowAlpha})`,
          backfaceVisibility: "hidden",
        }}>
          {logo && (
            <Img src={logo} style={{
              width: 128, height: 128, objectFit: "contain", borderRadius: 26,
              opacity: logoOp,
              transform: `translateY(${interpolate(logoS, [0, 1], [16, 0])}px)`,
            }} />
          )}
          {headline.length > 0 && (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 58, fontWeight: 800, color: INK,
              textAlign: "center", lineHeight: 1.18, letterSpacing: -1,
              opacity: textOp,
              transform: `translateY(${interpolate(textS, [0, 1], [16, 0])}px)`,
            }}>{headline}</div>
          )}
          {handle.length > 0 && (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 32, fontWeight: 600,
              color: "rgba(20,19,16,0.62)", letterSpacing: 1.2,
              opacity: handleOp,
              transform: `translateY(${interpolate(handleS, [0, 1], [16, 0])}px)`,
            }}>{handle}</div>
          )}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// credits — black screen, small centred cream type, and nothing else. The
// deliberate opposite of the energetic cluster: no ambient, no accent.
// bg fade 8f · text fade + 12px rise on the smooth spring (10f) · handle +8f.
// ---------------------------------------------------------------------------
export const Credits: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, showHandle, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const headline = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const empty = headline.length === 0 && handle.length === 0;

  const bgOpacity = fade(frame, 0, 8);
  const textS = spring({ frame, fps, delay: 2, config: SPRING_SMOOTH });
  const textOp = fade(frame, 2, 12);
  const handleS = spring({ frame, fps, delay: 10, config: SPRING_SMOOTH });
  const handleOp = fade(frame, 10, 20);

  if (empty) return null;

  return (
    <AbsoluteFill style={{ background: "#000000", opacity: bgOpacity }}>
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 22,
      }}>
        {headline.length > 0 && (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 38, fontWeight: 600, color: CREAM,
            textAlign: "center", padding: "0 140px", lineHeight: 1.45,
            letterSpacing: 2.5,
            opacity: textOp,
            transform: `translateY(${interpolate(textS, [0, 1], [12, 0])}px)`,
          }}>{headline}</div>
        )}
        {handle.length > 0 ? (
          <div style={{
            fontFamily: FONTS.inter, fontSize: 28, fontWeight: 500,
            color: CREAM_DIM, letterSpacing: 3,
            opacity: handleOp,
            transform: `translateY(${interpolate(handleS, [0, 1], [12, 0])}px)`,
          }}>{handle}</div>
        ) : showHandle ? (
          <div style={{
            width: handleOp * 120, height: 1, background: "rgba(246,241,231,0.4)",
          }} />
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
