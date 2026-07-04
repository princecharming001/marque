import React from "react";
import { Sequence, OffthreadVideo, Img, useCurrentFrame, interpolate } from "remotion";
import { BRoll } from "../types";

// Renders b-roll clips as full-frame cover overlays on the OUTPUT timeline. Each entry
// is mounted only during its [frame_in, frame_out) window (a Sequence), muted (audio
// stays on the base track underneath), with a slow Ken-Burns push so still-ish stock
// doesn't feel frozen. Non-9:16 stock is center-cropped to fill (objectFit cover), never
// letterboxed. Shared by broll_cutaway (over the face) and faceless (as the whole frame).
export const BrollLayer: React.FC<{ broll: BRoll[] }> = ({ broll }) => (
  <>
    {broll.filter((b) => b.resolved_url).map((b, i) => {
      const dur = Math.max(1, b.frame_out - b.frame_in);
      return (
        <Sequence key={i} from={b.frame_in} durationInFrames={dur} layout="none">
          <BrollClip url={b.resolved_url as string} durationInFrames={dur} />
        </Sequence>
      );
    })}
  </>
);

const BrollClip: React.FC<{ url: string; durationInFrames: number }> = ({ url, durationInFrames }) => {
  const frame = useCurrentFrame(); // local to the Sequence (0 at clip start)
  const scale = interpolate(frame, [0, durationInFrames], [1.06, 1.12], {
    extrapolateRight: "clamp",
  });
  const isImage = /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url);
  const style: React.CSSProperties = {
    position: "absolute", inset: 0, width: "100%", height: "100%",
    objectFit: "cover", transform: `scale(${scale})`,
  };
  return isImage ? (
    <Img src={url} style={style} />
  ) : (
    <OffthreadVideo src={url} muted style={style} />
  );
};
