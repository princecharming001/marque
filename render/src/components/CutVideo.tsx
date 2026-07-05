import React from "react";
import { Series, OffthreadVideo } from "remotion";
import { Clip, VolumeRange, volumeAt } from "../types";

// The actual cut: stitches the kept source intervals (`clips`, source frames) back to
// back on the output timeline via <Series>. OffthreadVideo trimBefore/trimAfter select
// the source range; the Series.Sequence duration is that range's length (no speed
// change), so the concatenation IS the trimmed edit. This is what makes the "AI editor"
// actually remove filler/dead-air instead of just overlaying effects on the raw take.
// Rendered as a sibling of captions/overlays (not their parent), so those keep using the
// global output frame from useCurrentFrame().
//
// volumeRanges (OUTPUT coords, from the plan's audio block) drive per-range source
// volume — mutes and duck-downs from the manual editor. Each Series.Sequence knows its
// own output offset (cumulative durations), so localFrame + outStart = output frame.
export const CutVideo: React.FC<{
  sourceUrl: string;
  clips: Clip[];
  volumeRanges?: VolumeRange[] | null;
  style?: React.CSSProperties;
}> = ({ sourceUrl, clips, volumeRanges, style }) => {
  if (!sourceUrl || clips.length === 0) {
    return <div style={{ width: "100%", height: "100%", background: "#111" }} />;
  }
  let outCursor = 0;
  const withOffsets = clips.map((c) => {
    const outStart = outCursor;
    outCursor += Math.max(1, c.src_out - c.src_in);
    return { clip: c, outStart };
  });
  return (
    <Series>
      {withOffsets.map(({ clip: c, outStart }, i) => (
        <Series.Sequence key={i} durationInFrames={Math.max(1, c.src_out - c.src_in)}>
          <OffthreadVideo
            src={sourceUrl}
            trimBefore={c.src_in}
            trimAfter={c.src_out}
            volume={
              volumeRanges && volumeRanges.length > 0
                ? (localF) => volumeAt(outStart + localF, volumeRanges)
                : undefined
            }
            style={{ width: "100%", height: "100%", objectFit: "cover", ...style }}
          />
        </Series.Sequence>
      ))}
    </Series>
  );
};
