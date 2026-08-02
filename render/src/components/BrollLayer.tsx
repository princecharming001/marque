import React from "react";
import { Sequence, OffthreadVideo, Img, useCurrentFrame, useVideoConfig,
         interpolate, spring } from "remotion";
import { BRoll } from "../types";

// Insert motion (owner directive 2026-07-29: "it shouldn't be rounded... it just blips in
// and out with no animation"). Inserts are RECTANGLES that SLIDE in from the side they
// live on and settle — a directional entrance reads as a deliberate editorial move, where
// a symmetric fade reads as a render glitch. Card mode keeps a small radius (it's a
// floating artifact card, the one case where a rounded plate is the convention).
const SLIDE_PX = 60;                                   // travel distance
const SLIDE_SPRING = { damping: 14, stiffness: 120, mass: 1 };   // ~0.35-0.45s settle @30fps
const ANTI_FLASH_FRAMES = 2;                           // kills the 1-frame bright-source pop
const SETTLE_OUT_FRAMES = 4;                           // exit: tiny scale settle, no fade

// Shared entrance/exit for the inset modes. `axis` is the side the insert lives on, so it
// always travels from its own edge inward. NOT a hook — a pure helper taking frame/fps, so
// it can be called from inside the mode branches without breaking the rules of hooks.
const insertMotion = (frame: number, fps: number, durationInFrames: number,
                      axis: "top" | "right") => {
  const s = spring({ frame, fps, config: SLIDE_SPRING });
  const travel = interpolate(s, [0, 1], [SLIDE_PX, 0]);
  const outAt = Math.max(1, durationInFrames - SETTLE_OUT_FRAMES);
  const settle = interpolate(frame, [outAt, durationInFrames], [1, 0.985],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // Opacity is effectively hard-on: a ≤2-frame ramp only, never a symmetric fade.
  const opacity = interpolate(frame, [0, ANTI_FLASH_FRAMES], [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const offset = axis === "top" ? `translateY(${-travel}px)` : `translateX(${travel}px)`;
  return { transform: `${offset} scale(${settle})`, opacity };
};

// Renders b-roll inserts on the OUTPUT timeline, composited per the Addendum Part 2 mode
// carried on each item (schema v5, additive — absent mode = "full" = v1 behavior):
//   full  (mode B) — covers the whole frame; the face is hidden while it plays.
//   panel (mode C) — a rounded-corner panel in the upper half; the face stays visible
//                    below. ≥40px margins, soft border/shadow so it reads as a card.
//   card  (mode D) — a small floating media card over one shoulder (upper half, right
//                    side by default), scale-pop entrance. For quick citations.
// Every entry mounts only during its [frame_in, frame_out) window (a Sequence), muted
// (audio stays on the base track), with a slow Ken-Burns push so still-ish stock doesn't
// feel frozen. Non-9:16 stock is center-cropped (objectFit cover), never letterboxed.
// Shared by broll_cutaway (over the face) and faceless (as the whole frame).
export const BrollLayer: React.FC<{ broll: BRoll[] }> = ({ broll }) => (
  <>
    {broll.filter((b) => b.resolved_url).map((b, i) => {
      const dur = Math.max(1, b.frame_out - b.frame_in);
      const mode = b.mode === "panel" || b.mode === "card" || b.mode === "smart" ? b.mode : "full";
      return (
        <Sequence key={i} from={b.frame_in} durationInFrames={dur} layout="none">
          <BrollClip url={b.resolved_url as string} durationInFrames={dur} mode={mode}
                     source={b.source} insetRect={b.inset_rect ?? null} />
        </Sequence>
      );
    })}
  </>
);

// Frame geometry (1080×1920). Panel: upper half, clear of the top platform UI; the face
// (framed lower-half by selfie convention) and the caption band (~62%) stay visible.
const PANEL = { left: 40, right: 40, top: 130, height: 0.46 * 1920, radius: 0 };
// Card: ≤35% of frame area, upper half, over one shoulder (right side — no gaze detection
// yet, and the speaker is horizontally centered so either shoulder is safe). The ONLY
// rounded insert: a floating artifact card (screenshot/receipt/tweet) reads as a plate.
const CARD = { width: 0.44 * 1080, height: 0.35 * 1920 * 0.72, top: 220, right: 48, radius: 8 };

// Provider attribution — both GIPHY's and KLIPY's API terms require a visible mark
// wherever their content displays. Small pill, bottom-right of the insert.
const ProviderBadge: React.FC<{ label: string }> = ({ label }) => (
  <div style={{
    position: "absolute", right: 8, bottom: 8, zIndex: 2,
    padding: "2px 7px", borderRadius: 5, background: "rgba(0,0,0,0.6)",
    color: "#fff", fontSize: 13, fontWeight: 700, letterSpacing: 0.2,
    fontFamily: "Inter, Helvetica, Arial, sans-serif",
  }}>
    {label}
  </div>
);
const GiphyBadge: React.FC = () => <ProviderBadge label="Powered By GIPHY" />;
const KlipyBadge: React.FC = () => <ProviderBadge label="Powered by KLIPY" />;

import { InsetRect } from "../types";

const BrollClip: React.FC<{ url: string; durationInFrames: number; mode: string;
                            source?: string; insetRect?: InsetRect | null }> = ({
  url, durationInFrames, mode, source, insetRect,
}) => {
  const frame = useCurrentFrame(); // local to the Sequence (0 at clip start)
  const { fps } = useVideoConfig();
  const isImage = /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url);
  const isGiphy = source === "giphy";
  const isKlipy = source === "klipy";
  const badge = isGiphy ? <GiphyBadge /> : isKlipy ? <KlipyBadge /> : null;
  // Stills (own_media photos) need a STRONGER Ken-Burns push than video so they don't read as a
  // frozen slide; motion footage moves on its own, so it keeps the gentle push.
  const kenBurns = interpolate(frame, [0, durationInFrames], isImage ? [1.05, 1.18] : [1.06, 1.12], {
    extrapolateRight: "clamp",
  });
  // v7 P1 conform: stock/still inserts get a gentle normalization (knock down
  // blown-white stock, nudge contrast) so they sit inside the a-roll's grade —
  // the #1 "reads as stock" tell is color mismatch. Memes keep their punch.
  const conform = isGiphy || isKlipy ? "" : "saturate(0.96) brightness(0.97) contrast(1.02)";
  const media = (style: React.CSSProperties) => {
    const merged = conform
      ? { ...style, filter: [style.filter, conform].filter(Boolean).join(" ") }
      : style;
    return isImage ? <Img src={url} style={merged} /> : <OffthreadVideo src={url} muted style={merged} />;
  };

  if (mode === "smart" && insetRect) {
    // v3 OTS face-aware inset: rect precomputed backend-side (opposite the face,
    // safe-zone + caption-band clean). Square-cornered rectangle that slides down from
    // its top edge and settles — no rounding, no fade (owner directive).
    const motion = insertMotion(frame, fps, durationInFrames, "top");
    return (
      <div style={{
        position: "absolute",
        left: insetRect.x * 1080, top: insetRect.y * 1920,
        width: insetRect.w * 1080, height: insetRect.h * 1920,
        borderRadius: 0, overflow: "hidden",
        border: "3px solid rgba(255,255,255,0.14)",
        boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
        transform: motion.transform, transformOrigin: "center",
        opacity: motion.opacity,
      }}>
        {media({ position: "absolute", inset: 0, width: "100%", height: "100%",
                 objectFit: "cover", transform: `scale(${kenBurns})` })}
        {badge}
      </div>
    );
  }

  if (mode === "panel" || mode === "smart") {
    // Upper-half panel: a square rectangle that slides down into place from the top.
    const motion = insertMotion(frame, fps, durationInFrames, "top");
    return (
      <div style={{
        position: "absolute", left: PANEL.left, right: PANEL.right, top: PANEL.top,
        height: PANEL.height, borderRadius: PANEL.radius, overflow: "hidden",
        border: "3px solid rgba(255,255,255,0.14)",
        boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
        transform: motion.transform, transformOrigin: "center",
        opacity: motion.opacity,
      }}>
        {media({ position: "absolute", inset: 0, width: "100%", height: "100%",
                 objectFit: "cover", transform: `scale(${kenBurns})` })}
        {badge}
      </div>
    );
  }

  if (mode === "card") {
    // Shoulder card: slides in from the right edge it sits on; keeps a small radius.
    const motion = insertMotion(frame, fps, durationInFrames, "right");
    return (
      <div style={{
        position: "absolute", top: CARD.top, right: CARD.right,
        width: CARD.width, height: CARD.height,
        borderRadius: CARD.radius, overflow: "hidden",
        border: "3px solid rgba(255,255,255,0.16)",
        boxShadow: "0 14px 40px rgba(0,0,0,0.5)",
        transform: motion.transform, transformOrigin: "top right",
        opacity: motion.opacity,
      }}>
        {media({ position: "absolute", inset: 0, width: "100%", height: "100%",
                 // A card is usually a screenshot/tweet/receipt — show the WHOLE artifact
                 // (contain) over a dark backing rather than cropping the evidence.
                 objectFit: isImage ? "contain" : "cover", background: "#101014",
                 transform: `scale(${kenBurns})` })}
        {badge}
      </div>
    );
  }

  // mode "full" — the v1 full-frame cover insert. Flash inserts (<1s) get a 2-frame
  // punch-in entrance so the cut reads intentional; sustained cutaways keep the hard cut.
  const flashPunch = durationInFrames < 30
    ? interpolate(frame, [0, 2], [1.06, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })
    : 1;
  return (
    <>
      {media({ position: "absolute", inset: 0, width: "100%", height: "100%",
               objectFit: "cover", transform: `scale(${kenBurns * flashPunch})` })}
      {badge}
    </>
  );
};
