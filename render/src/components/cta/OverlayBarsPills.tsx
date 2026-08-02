import React from "react";
import { AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
         spring, interpolate, Easing } from "remotion";
import { INK, CREAM, CREAM_DIM, PLATE, NEON, FONTS,
         EASE_M3, SPRING_POP, EXIT_FRAMES,
         clampOverlayY, OVERLAY_Y, CtaTemplateProps } from "./tokens";

// ---------------------------------------------------------------------------
// OVERLAY FAMILY — bars, pills and chips that sit ON TOP of live video in the
// final seconds. Contract, enforced by every component below:
//  • NO full-frame background fill — the speaker must stay visible. The
//    AbsoluteFill is a transparent positioning shell only.
//  • The graphic lives in the clean strip (below the caption band, above the
//    platform chrome) via clampOverlayY(OVERLAY_Y, height). corner_tag is the
//    documented exception: it rides the TOP-RIGHT corner, which is also the
//    corner that cannot collide with the watermark (bottom-LEFT).
//  • When live video follows the CTA, each template plays a VISIBLE EXIT
//    (~EXIT_FRAMES) instead of just ending. progress_follow is the one exception —
//    it is a countdown, so it lands on 100% with the video. But build_render_plan
//    mounts overlay CTAs FLUSH to the end of the reel, so in the shipping path
//    nothing follows them: `runsToEnd` suppresses the exit and the CTA holds. An
//    exit there would spend the last frames sliding a half-faded plate across the
//    speaker — including the frame the platform freezes on when the reel loops.
//  • Entrances land in 9-15 frames. Deterministic sinusoids only.
// ---------------------------------------------------------------------------

const M3 = Easing.bezier(EASE_M3[0], EASE_M3[1], EASE_M3[2], EASE_M3[3]);
const OUT_CUBIC = Easing.out(Easing.cubic);
const IMG_RE = /\.(png|jpe?g|webp|gif)(\?|$)/i;
const TAU = Math.PI * 2;

const asLogo = (u: string | null | undefined): string | null =>
  u && IMG_RE.test(u) ? u : null;

const track = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

const fade = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: OUT_CUBIC, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** 1 → 0 over the last EXIT_FRAMES of the window (visible exit over live video). */
const exitRamp = (frame: number, total: number, runsToEnd?: boolean,
                  frames: number = EXIT_FRAMES): number => {
  if (runsToEnd) return 1;          // hold — nothing follows this CTA
  const at = Math.max(1, total - frames);
  return interpolate(frame, [at, total], [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
};

/** Legibility over arbitrary footage, matching the TextStickers non-box treatment. */
const OVER_VIDEO_SHADOW = "0 3px 14px rgba(0,0,0,0.85)";

/** Positioning shell for a graphic of known height inside the clean strip. */
const stripStyle = (height: number): React.CSSProperties => ({
  position: "absolute",
  top: clampOverlayY(OVERLAY_Y, height),
  left: 0,
  width: "100%",
  display: "flex",
  justifyContent: "center",
});

// ---------------------------------------------------------------------------
// pill — the safe one. A visual clone of the TextStickers "box" sticker
// (solid dark fill, borderRadius 18, cream Inter, comfortable padding),
// bottom-centre in the clean strip.
// scale-pop 0.6→1 (9f) · 1.5% idle breathe · exit fade + scale 0.92 (8f).
// ---------------------------------------------------------------------------
export const Pill: React.FC<CtaTemplateProps> = ({ text, durationInFrames, runsToEnd }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const pop = spring({ frame, fps, config: SPRING_POP, durationInFrames: 9 });
  const popScale = interpolate(pop, [0, 1], [0.6, 1]);
  const breathe = 1 + Math.sin(frame * 0.09) * 0.0075;      // ±0.75% ⇒ 1.5% range
  const out = exitRamp(frame, total, runsToEnd);
  const scale = popScale * breathe * interpolate(out, [0, 1], [0.92, 1]);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(84)}>
        <span style={{
          fontFamily: FONTS.inter, fontSize: 40, fontWeight: 800,
          color: CREAM, lineHeight: 1.2,
          background: PLATE, borderRadius: 18, padding: "16px 40px",
          maxWidth: 880, textAlign: "center",
          opacity: fade(frame, 0, 6) * out,
          transform: `scale(${scale})`,
        }}>{label}</span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// bar_sweep — broadcast lower third. An ink bar sweeps in from the left
// (translateX -100%→0, 12f, M3), cream text fades in at +4f over a thin cream
// accent underline. Exit sweeps out to the RIGHT over 10f.
// ---------------------------------------------------------------------------
export const BarSweep: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, durationInFrames, runsToEnd,
}) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();
  const handle = (rawHandle || "").trim();
  const empty = label.length === 0 && handle.length === 0;

  const sweepIn = track(frame, 0, 12);
  const outAt = Math.max(1, total - 10);
  const sweepOut = runsToEnd ? 0 : interpolate(frame, [outAt, total], [0, 1],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const x = interpolate(sweepIn, [0, 1], [-110, 0]) + sweepOut * 120;
  const textOpacity = fade(frame, 4, 14) * (1 - sweepOut);
  const ruleW = track(frame, 6, 18);

  if (empty) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden" }}>
      <div style={{ ...stripStyle(140), justifyContent: "flex-start" }}>
        <div style={{
          background: INK,
          borderRadius: "0 20px 20px 0",
          padding: "24px 56px 26px 64px",
          maxWidth: 860,
          transform: `translateX(${x}%)`,
          boxShadow: "0 8px 30px rgba(0,0,0,0.45)",
        }}>
          {label.length > 0 && (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 44, fontWeight: 800, color: CREAM,
              lineHeight: 1.15, letterSpacing: -0.5,
              opacity: textOpacity,
            }}>{label}</div>
          )}
          <div style={{
            width: ruleW * 120, height: 4, borderRadius: 2,
            background: CREAM, marginTop: 12, opacity: 1 - sweepOut,
          }} />
          {handle.length > 0 && (
            <div style={{
              fontFamily: FONTS.inter, fontSize: 28, fontWeight: 600,
              color: CREAM_DIM, letterSpacing: 1.2, marginTop: 10,
              opacity: fade(frame, 8, 18) * (1 - sweepOut),
            }}>{handle}</div>
          )}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// serif_line — editorial. An italic line between two hairlines that draw
// OUTWARD from centre over 12f; the text fades in over 10f; plain fade exit.
// NOTE: no serif family is loaded (FONTS = inter/archivo/baloo/montserrat/
// anton), so this uses Inter in italic — the loaded-fonts rule wins.
// ---------------------------------------------------------------------------
export const SerifLine: React.FC<CtaTemplateProps> = ({ text, durationInFrames, runsToEnd }) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const ruleT = track(frame, 0, 12);
  const textT = fade(frame, 3, 13);
  const out = exitRamp(frame, total, runsToEnd);

  if (!label) return null;

  const rule = (opacity: number): React.CSSProperties => ({
    width: ruleT * 520, height: 1,
    background: "rgba(246,241,231,0.85)",
    boxShadow: "0 1px 6px rgba(0,0,0,0.7)",
    opacity,
  });

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(170)}>
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", gap: 22,
          opacity: out,
        }}>
          <div style={rule(1)} />
          <div style={{
            fontFamily: FONTS.inter, fontStyle: "italic", fontSize: 46, fontWeight: 600,
            color: CREAM, textAlign: "center", maxWidth: 820, lineHeight: 1.3,
            letterSpacing: 0.5,
            textShadow: OVER_VIDEO_SHADOW,
            opacity: textT,
          }}>{label}</div>
          <div style={rule(1)} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// corner_tag — a broadcast bug: small rounded chip in the TOP-RIGHT corner
// (deliberately the opposite corner from the bottom-LEFT watermark, and out of
// the clean strip by design). Slides in from the right over 10f, slides back
// out at the end.
// ---------------------------------------------------------------------------
export const CornerTag: React.FC<CtaTemplateProps> = ({
  handle: rawHandle, text, logoUrl, durationInFrames, runsToEnd,
}) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const handle = (rawHandle || "").trim() || (text || "").trim();
  const logo = asLogo(logoUrl);

  const inT = track(frame, 0, 10);
  const outAt = Math.max(1, total - EXIT_FRAMES);
  const outT = runsToEnd ? 0 : interpolate(frame, [outAt, total], [0, 1],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const x = interpolate(inT, [0, 1], [340, 0]) + outT * 340;

  if (!handle) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none", overflow: "hidden" }}>
      <div style={{
        position: "absolute", top: 150, right: 44,
        display: "flex", alignItems: "center", gap: 16,
        background: PLATE, borderRadius: 44, padding: "14px 28px 14px 16px",
        transform: `translateX(${x}px)`,
        opacity: inT * (1 - outT),
        boxShadow: "0 6px 24px rgba(0,0,0,0.45)",
      }}>
        {logo ? (
          <Img src={logo} style={{
            width: 56, height: 56, borderRadius: 28, objectFit: "cover",
          }} />
        ) : (
          <div style={{ width: 18, height: 18, borderRadius: 9, background: CREAM, marginLeft: 14 }} />
        )}
        <span style={{
          fontFamily: FONTS.inter, fontSize: 30, fontWeight: 700,
          color: CREAM, letterSpacing: 0.4, whiteSpace: "nowrap",
        }}>{handle}</span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// progress_follow — a thin bar in the clean strip that fills 0→100% LINEARLY
// across the whole window, with a short label above it. The label fades in
// over 8f. No exit by design: the bar hitting 100% IS the ending.
// ---------------------------------------------------------------------------
export const ProgressFollow: React.FC<CtaTemplateProps> = ({ text, durationInFrames, runsToEnd }) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const labelT = fade(frame, 0, 8);
  const fill = interpolate(frame, [0, Math.max(1, total - 1)], [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(110)}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}>
          <div style={{
            fontFamily: FONTS.inter, fontSize: 38, fontWeight: 700, color: CREAM,
            textAlign: "center", maxWidth: 820, lineHeight: 1.25,
            textShadow: OVER_VIDEO_SHADOW,
            opacity: labelT,
            transform: `translateY(${(1 - labelT) * 8}px)`,
          }}>{label}</div>
          <div style={{
            width: 720, height: 8, borderRadius: 4,
            background: "rgba(20,19,16,0.55)",
            overflow: "hidden",
            opacity: labelT,
            boxShadow: "0 2px 10px rgba(0,0,0,0.5)",
          }}>
            <div style={{
              width: `${fill * 100}%`, height: "100%", borderRadius: 4, background: CREAM,
            }} />
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// part_two — the follow-for-part-2 pill, with a chevron that nudges right on a
// 20-frame loop so the eye keeps returning to it. Pops in over 9f, fades out.
// ---------------------------------------------------------------------------
export const PartTwo: React.FC<CtaTemplateProps> = ({
  text, handle: rawHandle, durationInFrames, runsToEnd,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();
  const handle = (rawHandle || "").trim();

  const pop = spring({ frame, fps, config: SPRING_POP, durationInFrames: 9 });
  const popScale = interpolate(pop, [0, 1], [0.68, 1]);
  const nudge = Math.sin((frame / 20) * TAU) * 7;
  const out = exitRamp(frame, total, runsToEnd);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(96)}>
        <div style={{
          display: "flex", alignItems: "center", gap: 18,
          background: PLATE, borderRadius: 18, padding: "16px 34px 16px 40px",
          maxWidth: 900,
          opacity: fade(frame, 0, 6) * out,
          transform: `scale(${popScale})`,
          boxShadow: "0 8px 28px rgba(0,0,0,0.42)",
        }}>
          <span style={{
            fontFamily: FONTS.inter, fontSize: 40, fontWeight: 800,
            color: CREAM, lineHeight: 1.2,
          }}>{label}</span>
          {handle.length > 0 && (
            <span style={{
              fontFamily: FONTS.inter, fontSize: 28, fontWeight: 600,
              color: CREAM_DIM, lineHeight: 1.2,
            }}>{handle}</span>
          )}
          <svg width={30} height={30} viewBox="0 0 24 24"
               style={{ transform: `translateX(${nudge}px)`, flexShrink: 0 }}>
            <path d="M9 5l7 7-7 7" fill="none" stroke={CREAM} strokeWidth={2.6}
                  strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// neon_pulse — energetic cluster (the only cluster allowed NEON). A pill with
// a neon outline whose glow breathes on a 24-frame cycle (box-shadow blur
// 8↔16px). Pops in over 9f, fades out.
// ---------------------------------------------------------------------------
export const NeonPulse: React.FC<CtaTemplateProps> = ({ text, durationInFrames, runsToEnd }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const pop = spring({ frame, fps, config: SPRING_POP, durationInFrames: 9 });
  const popScale = interpolate(pop, [0, 1], [0.68, 1]);
  // 0→1→0 on a 24f cycle ⇒ blur 8↔16px, inner and outer in step.
  const pulse = 0.5 + 0.5 * Math.sin((frame / 24) * TAU);
  const blur = 8 + pulse * 8;
  const out = exitRamp(frame, total, runsToEnd);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(96)}>
        <span style={{
          fontFamily: FONTS.inter, fontSize: 40, fontWeight: 800,
          color: CREAM, lineHeight: 1.2, textAlign: "center", maxWidth: 880,
          background: "rgba(8,8,12,0.72)",
          border: `3px solid ${NEON}`,
          borderRadius: 44, padding: "16px 44px",
          boxShadow: `0 0 ${blur}px rgba(207,245,106,0.9), inset 0 0 ${blur}px rgba(207,245,106,0.35)`,
          opacity: fade(frame, 0, 6) * out,
          transform: `scale(${popScale})`,
        }}>{label}</span>
      </div>
    </AbsoluteFill>
  );
};
