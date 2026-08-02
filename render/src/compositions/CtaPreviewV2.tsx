import React from "react";
import { AbsoluteFill, Audio, OffthreadVideo, Sequence, interpolate,
         useCurrentFrame } from "remotion";
import { resolveCta } from "../components/cta/registry";

// v2 preview stage for the ONBOARDING CTA SWIPER (owner directive, build 63): each card
// is a generated cinematic base video (Higgsfield; no text, no faces) with the REAL CTA
// template composited on top and a distinct licensed-bed music track — so a swipe judges
// a finished-feeling ending, not a bare animation on a gradient. The 20-template picker
// keeps the v1 silhouette stage (scripts/gen_cta_previews.py); this composition only
// produces the 5 curated swiper cards (scripts/gen_cta_deck_v2.py).
//
// Timeline (150f = 5s @30fps): base video runs the whole card; the CTA enters at f30 and
// HOLDS to the end (runsToEnd — nothing follows a card, same contract as a real reel).
// Music fades in over the first 12f and out over the last 9f so a loop restart doesn't
// pop.
const CTA_AT = 30;

export const CtaPreviewV2: React.FC<{
  styleId: string; text: string; handle: string; logoUrl: string | null;
  videoSrc: string; audioSrc: string;
}> = ({ styleId, text, handle, logoUrl, videoSrc, audioSrc }) => {
  const frame = useCurrentFrame();
  const total = 150;
  const { Comp } = resolveCta(styleId);

  const musicVolume = interpolate(frame, [0, 12, total - 9, total], [0, 0.9, 0.9, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "#100E0C" }}>
      {videoSrc ? (
        <OffthreadVideo src={videoSrc} muted
          style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : null}
      {/* Legibility scrim — generated footage can be bright where the CTA lands. */}
      <AbsoluteFill style={{
        background: "linear-gradient(180deg, rgba(0,0,0,0.10) 0%, rgba(0,0,0,0) 35%, " +
                    "rgba(0,0,0,0.28) 100%)",
      }} />
      <Sequence from={CTA_AT} durationInFrames={total - CTA_AT} layout="none">
        <Comp text={text} handle={handle} logoUrl={logoUrl} showHandle={handle.length > 0}
              durationInFrames={total - CTA_AT} runsToEnd />
      </Sequence>
      {audioSrc ? <Audio src={audioSrc} volume={musicVolume} /> : null}
    </AbsoluteFill>
  );
};
