import React from "react";
import { AbsoluteFill, OffthreadVideo, useVideoConfig, useCurrentFrame } from "remotion";
import { Captions } from "../components/Captions";
import { CompositionProps } from "../types";

export const TalkingHead: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const punchIn = edl?.overlays.find(
    (o) => o.type === "punch_in" && frame >= o.src_in && frame < o.src_out
  );
  const scale = punchIn ? punchIn.scale : 1.0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {sourceUrl ? (
        // OffthreadVideo extracts frames server-side via FFmpeg instead of relying on
        // the browser's <video> element to decode — the browser decoder rejects
        // several otherwise-valid MP4s during Lambda rendering (confirmed live: two
        // different public test sources both failed with MEDIA_ELEMENT_ERROR under
        // <Video>, rendered fine under OffthreadVideo). This is Remotion's own
        // recommended component for server-side/Lambda rendering.
        <OffthreadVideo src={sourceUrl} style={{ width: "100%", height: "100%", objectFit: "cover",
          transform: `scale(${scale})`, transition: "transform 0.1s" }} />
      ) : (
        <div style={{ flex: 1, background: "#111", display: "flex", alignItems: "center",
          justifyContent: "center", color: "#888", fontSize: 40 }}>Preview</div>
      )}
      {edl && <Captions captions={edl.captions} totalFrames={720} />}
    </AbsoluteFill>
  );
};
