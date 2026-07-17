import React from "react";
import { Sequence, OffthreadVideo, Img, useCurrentFrame, interpolate } from "remotion";
import { BRoll } from "../types";

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
      const mode = b.mode === "panel" || b.mode === "card" ? b.mode : "full";
      return (
        <Sequence key={i} from={b.frame_in} durationInFrames={dur} layout="none">
          <BrollClip url={b.resolved_url as string} durationInFrames={dur} mode={mode}
                     source={b.source} />
        </Sequence>
      );
    })}
  </>
);

// Frame geometry (1080×1920). Panel: upper half, clear of the top platform UI; the face
// (framed lower-half by selfie convention) and the caption band (~62%) stay visible.
const PANEL = { left: 40, right: 40, top: 130, height: 0.46 * 1920, radius: 20 };
// Card: ≤35% of frame area, upper half, over one shoulder (right side — no gaze detection
// yet, and the speaker is horizontally centered so either shoulder is safe).
const CARD = { width: 0.44 * 1080, height: 0.35 * 1920 * 0.72, top: 220, right: 48, radius: 18 };

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

const BrollClip: React.FC<{ url: string; durationInFrames: number; mode: string; source?: string }> = ({
  url, durationInFrames, mode, source,
}) => {
  const frame = useCurrentFrame(); // local to the Sequence (0 at clip start)
  const isImage = /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url);
  const isGiphy = source === "giphy";
  const isKlipy = source === "klipy";
  const badge = isGiphy ? <GiphyBadge /> : isKlipy ? <KlipyBadge /> : null;
  // Stills (own_media photos) need a STRONGER Ken-Burns push than video so they don't read as a
  // frozen slide; motion footage moves on its own, so it keeps the gentle push.
  const kenBurns = interpolate(frame, [0, durationInFrames], isImage ? [1.05, 1.18] : [1.06, 1.12], {
    extrapolateRight: "clamp",
  });
  const media = (style: React.CSSProperties) =>
    isImage ? <Img src={url} style={style} /> : <OffthreadVideo src={url} muted style={style} />;

  if (mode === "panel") {
    // Pop-in/pop-out so short inserts read as deliberate flashes, not render glitches.
    // Ramps clamp to a third of the window so a 15f meme still gets in AND out.
    const inLen = Math.min(5, Math.max(1, Math.floor(durationInFrames / 3)));
    const outLen = Math.min(4, Math.max(1, Math.floor(durationInFrames / 3)));
    const popIn = interpolate(frame, [0, inLen], [0.92, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const popOut = interpolate(frame, [durationInFrames - outLen, durationInFrames], [1, 0.95], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const fadeIn = interpolate(frame, [0, inLen], [0, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const fadeOut = interpolate(frame, [durationInFrames - outLen, durationInFrames], [1, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    return (
      <div style={{
        position: "absolute", left: PANEL.left, right: PANEL.right, top: PANEL.top,
        height: PANEL.height, borderRadius: PANEL.radius, overflow: "hidden",
        border: "3px solid rgba(255,255,255,0.14)",
        boxShadow: "0 18px 50px rgba(0,0,0,0.45)",
        transform: `scale(${popIn * popOut})`, transformOrigin: "center",
        opacity: Math.min(fadeIn, fadeOut),
      }}>
        {media({ position: "absolute", inset: 0, width: "100%", height: "100%",
                 objectFit: "cover", transform: `scale(${kenBurns})` })}
        {badge}
      </div>
    );
  }

  if (mode === "card") {
    // Scale-pop entrance over ~3 frames (spec §6.6) + symmetric pop-out.
    const outLen = Math.min(3, Math.max(1, Math.floor(durationInFrames / 3)));
    const pop = interpolate(frame, [0, 3], [0.6, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const popOut = interpolate(frame, [durationInFrames - outLen, durationInFrames], [1, 0.7], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    const fadeOut = interpolate(frame, [durationInFrames - outLen, durationInFrames], [1, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    });
    return (
      <div style={{
        position: "absolute", top: CARD.top, right: CARD.right,
        width: CARD.width, height: CARD.height,
        borderRadius: CARD.radius, overflow: "hidden",
        border: "3px solid rgba(255,255,255,0.16)",
        boxShadow: "0 14px 40px rgba(0,0,0,0.5)",
        transform: `scale(${pop * popOut})`, transformOrigin: "top right",
        opacity: fadeOut,
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
