import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig,
         spring, interpolate, Easing } from "remotion";
import { CREAM, FONTS,
         EASE_M3, SPRING_SMOOTH, SPRING_POP, EXIT_FRAMES,
         clampOverlayY, OVERLAY_Y, CtaTemplateProps } from "./tokens";

// ---------------------------------------------------------------------------
// OVERLAY FAMILY — bare type over live video (no plate, no bar). Same contract
// as OverlayBarsPills:
//  • the AbsoluteFill is a transparent positioning shell — NEVER a background
//    fill, the speaker stays visible underneath;
//  • the type lives in the clean strip via clampOverlayY(OVERLAY_Y, height);
//  • every template has a visible exit, because the video keeps playing;
//  • entrances land in 9-15 frames, deterministic sinusoids only.
// Legibility over unknown footage comes from a heavy drop shadow (the same
// treatment TextStickers uses for its no-box stickers), not from a plate.
// ---------------------------------------------------------------------------

const M3 = Easing.bezier(EASE_M3[0], EASE_M3[1], EASE_M3[2], EASE_M3[3]);
const OUT_CUBIC = Easing.out(Easing.cubic);
const TAU = Math.PI * 2;

const OVER_VIDEO_SHADOW = "0 3px 16px rgba(0,0,0,0.88)";

const track = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: M3, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

const fade = (frame: number, from: number, to: number): number =>
  interpolate(frame, [from, Math.max(from + 1, to)], [0, 1],
    { easing: OUT_CUBIC, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

const exitRamp = (frame: number, total: number, frames: number = EXIT_FRAMES): number => {
  const at = Math.max(1, total - frames);
  return interpolate(frame, [at, total], [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
};

const stripStyle = (height: number): React.CSSProperties => ({
  position: "absolute",
  top: clampOverlayY(OVERLAY_Y, height),
  left: 0,
  width: "100%",
  display: "flex",
  justifyContent: "center",
});

// ---------------------------------------------------------------------------
// handle_reveal — just the handle in clean type, with an underline that draws
// itself 0→100% of the handle's own width over 12f (M3). Handle fades and
// rises 10px over 9f; 6f fade exit.
// ---------------------------------------------------------------------------
export const HandleReveal: React.FC<CtaTemplateProps> = ({
  handle: rawHandle, text, durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  // params: ["handle"] — but fall back to the CTA line rather than render nothing.
  const label = (rawHandle || "").trim() || (text || "").trim();

  const enter = track(frame, 0, 9);
  const rule = track(frame, 3, 15);
  const out = exitRamp(frame, total, 6);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(110)}>
        <div style={{
          display: "inline-flex", flexDirection: "column", alignItems: "flex-start",
          opacity: enter * out,
          transform: `translateY(${(1 - enter) * 10}px)`,
        }}>
          <span style={{
            fontFamily: FONTS.inter, fontSize: 52, fontWeight: 700, color: CREAM,
            letterSpacing: 0.5, lineHeight: 1.2, whiteSpace: "nowrap",
            textShadow: OVER_VIDEO_SHADOW,
          }}>{label}</span>
          <div style={{
            width: `${rule * 100}%`, height: 4, borderRadius: 2, marginTop: 12,
            background: CREAM, boxShadow: "0 2px 10px rgba(0,0,0,0.6)",
          }} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// typewriter — the line types itself on with a blinking caret (15f blink
// cycle), then holds. 8f fade exit.
// The reveal is STRING SLICING, never per-character opacity: a hundred
// animated spans is both a render cost and a subpixel-jitter source.
// ADAPTED: the canonical rate is 2 frames/char, but it falls back to 1 when a
// long line would otherwise still be typing at the end of the window — typing
// always completes by ~60% of the window.
// ---------------------------------------------------------------------------
export const Typewriter: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();
  const n = label.length;
  const budget = Math.max(1, Math.floor(total * 0.6));
  const step = n > 0 && n * 2 > budget ? 1 : 2;

  const shown = Math.min(n, Math.floor(frame / step));
  const typing = shown < n;
  // 15f cycle: caret lit for the first 8 frames of each cycle, and always lit
  // while characters are still landing.
  const caretOn = typing || frame % 15 < 8;
  const out = exitRamp(frame, total);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(120)}>
        <span style={{
          fontFamily: FONTS.inter, fontSize: 46, fontWeight: 700, color: CREAM,
          textAlign: "center", maxWidth: 880, lineHeight: 1.28,
          textShadow: OVER_VIDEO_SHADOW,
          opacity: out,
          whiteSpace: "pre-wrap",
        }}>
          {label.slice(0, shown)}
          <span style={{ opacity: caretOn ? 1 : 0 }}>|</span>
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// blur_in — the line resolves out of a 12px blur while its letter-spacing
// closes from 0.12em to 0 over 12f on the smooth spring. Exits by blurring
// back out over 8f.
// ---------------------------------------------------------------------------
export const BlurIn: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const s = spring({ frame, fps, config: SPRING_SMOOTH, durationInFrames: 12 });
  const inBlur = interpolate(s, [0, 1], [12, 0]);
  const ls = interpolate(s, [0, 1], [0.12, 0]);
  const out = exitRamp(frame, total);
  // NB: `out` runs 1→0, so the input range still has to be written ascending —
  // Remotion's interpolate() throws on a non-increasing inputRange.
  const outBlur = interpolate(out, [0, 1], [10, 0]);
  const blurPx = inBlur + outBlur;

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(120)}>
        <span style={{
          fontFamily: FONTS.inter, fontSize: 50, fontWeight: 800, color: CREAM,
          textAlign: "center", maxWidth: 880, lineHeight: 1.24,
          letterSpacing: `${ls}em`,
          textShadow: OVER_VIDEO_SHADOW,
          opacity: fade(frame, 0, 8) * out,
          filter: blurPx > 0.15 ? `blur(${blurPx}px)` : undefined,
        }}>{label}</span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// scale_pop — one big line on the pop spring (0.7→1, ~3% overshoot).
// Exits by scaling back down and fading over 8f.
// ---------------------------------------------------------------------------
export const ScalePop: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const pop = spring({ frame, fps, config: SPRING_POP, durationInFrames: 12 });
  const out = exitRamp(frame, total);
  const scale = interpolate(pop, [0, 1], [0.7, 1]) * interpolate(out, [0, 1], [0.86, 1]);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(130)}>
        <span style={{
          fontFamily: FONTS.inter, fontSize: 62, fontWeight: 800, color: CREAM,
          textAlign: "center", maxWidth: 900, lineHeight: 1.18, letterSpacing: -1,
          textShadow: OVER_VIDEO_SHADOW,
          opacity: fade(frame, 0, 6) * out,
          transform: `scale(${scale})`,
        }}>{label}</span>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// underline_sweep — the headline rises 16px over 10f on the smooth spring, and
// an underline sweeps in beneath it (12f, M3) starting at +6f. Fade exit.
// ---------------------------------------------------------------------------
export const UnderlineSweep: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const s = spring({ frame, fps, config: SPRING_SMOOTH, durationInFrames: 10 });
  const rise = interpolate(s, [0, 1], [16, 0]);
  const sweep = track(frame, 6, 18);
  const out = exitRamp(frame, total);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(130)}>
        <div style={{
          display: "inline-flex", flexDirection: "column", alignItems: "flex-start",
          maxWidth: 880,
          opacity: fade(frame, 0, 8) * out,
          transform: `translateY(${rise}px)`,
        }}>
          <span style={{
            fontFamily: FONTS.inter, fontSize: 54, fontWeight: 800, color: CREAM,
            lineHeight: 1.2, letterSpacing: -0.5, textAlign: "left",
            textShadow: OVER_VIDEO_SHADOW,
          }}>{label}</span>
          <div style={{
            width: "100%", height: 6, borderRadius: 3, marginTop: 14,
            background: CREAM,
            boxShadow: "0 2px 10px rgba(0,0,0,0.6)",
            transform: `scaleX(${sweep})`,
            transformOrigin: "left center",
          }} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// arrow_nudge — the line plus an SVG arrow that bounces 10px on a sine over an
// 18-frame cycle, pointing at where the follow button lives. Text pops in over
// 9f; fade exit. (SVG, never an emoji glyph.)
// ---------------------------------------------------------------------------
export const ArrowNudge: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const pop = spring({ frame, fps, config: SPRING_POP, durationInFrames: 9 });
  const scale = interpolate(pop, [0, 1], [0.78, 1]);
  const bounce = Math.sin((frame / 18) * TAU) * 10;
  const out = exitRamp(frame, total);

  if (!label) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(120)}>
        <div style={{
          display: "flex", alignItems: "center", gap: 22, maxWidth: 900,
          opacity: fade(frame, 0, 6) * out,
          transform: `scale(${scale})`,
        }}>
          <span style={{
            fontFamily: FONTS.inter, fontSize: 48, fontWeight: 800, color: CREAM,
            lineHeight: 1.2, letterSpacing: -0.5,
            textShadow: OVER_VIDEO_SHADOW,
          }}>{label}</span>
          <svg width={56} height={56} viewBox="0 0 24 24"
               style={{ transform: `translateX(${bounce}px)`, flexShrink: 0,
                        filter: "drop-shadow(0 3px 10px rgba(0,0,0,0.8))" }}>
            <path d="M4 12h13M12.5 6l6.5 6-6.5 6" fill="none" stroke={CREAM}
                  strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// glitch — a 6-frame RGB-split burst on entrance (the line is drawn three
// times: a red copy and a cyan copy offset ±3px behind the cream original,
// with the split flipping twice across the burst), then DEAD STILL. One 3f
// re-glitch at ~70% of the window, then a hard-cut exit — no fade, the graphic
// simply stops with the window, which is the point of the style.
// All offsets come from a fixed table indexed by frame — no randomness.
// ---------------------------------------------------------------------------
const GLITCH_JITTER = [0, 4, -3, 2, -5, 1];

export const Glitch: React.FC<CtaTemplateProps> = ({ text, durationInFrames }) => {
  const frame = useCurrentFrame();
  const total = Math.max(24, durationInFrames);

  const label = (text || "").trim();

  const reAt = Math.round(total * 0.7);
  const inBurst = frame < 6;
  const inRe = frame >= reAt && frame < reAt + 3;
  const active = inBurst || inRe;
  // Two repeats across the 6f burst: the split sign flips every 3 frames.
  const phase = inBurst ? frame : frame - reAt;
  const sign = phase % 6 < 3 ? 1 : -1;
  const split = active ? 3 * sign : 0;
  const jitter = active ? GLITCH_JITTER[phase % GLITCH_JITTER.length] : 0;

  if (!label) return null;

  const base: React.CSSProperties = {
    fontFamily: FONTS.inter, fontSize: 56, fontWeight: 800,
    textAlign: "center", maxWidth: 880, lineHeight: 1.2, letterSpacing: -0.5,
    textTransform: "uppercase",
  };

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={stripStyle(130)}>
        <div style={{ position: "relative", display: "inline-block",
                      transform: `translateX(${jitter}px)` }}>
          {active && (
            <span style={{
              ...base, position: "absolute", inset: 0, color: "#FF2D2D",
              transform: `translateX(${-split}px)`, opacity: 0.85,
            }}>{label}</span>
          )}
          {active && (
            <span style={{
              ...base, position: "absolute", inset: 0, color: "#2DF0FF",
              transform: `translateX(${split}px)`, opacity: 0.85,
            }}>{label}</span>
          )}
          <span style={{
            ...base, position: "relative", color: CREAM, display: "inline-block",
            textShadow: OVER_VIDEO_SHADOW,
          }}>{label}</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};
