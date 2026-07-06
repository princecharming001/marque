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
// G0 (verified against Remotion's OWN source, not just docs — a prior audit flagged
// this as backwards): trimBefore/trimAfter are ABSOLUTE source-frame positions, not
// "skip N frames" durations. remotion's validateTrimProps requires trimAfter >
// trimBefore (see node_modules/remotion/dist/cjs/validate-start-from-props.js —
// same contract as the deprecated startFrom/endAt props it aliases). That is exactly
// src_in/src_out's contract, so `trimBefore={c.src_in} trimAfter={c.src_out}` below
// is correct as written. Do not "fix" this without re-reading that file.
//
// volumeRanges (OUTPUT coords, from the plan's audio block) drive per-range source
// volume — mutes and duck-downs from the manual editor. Each Series.Sequence knows its
// own output offset (cumulative durations), so localFrame + outStart = output frame.
// volumeAt (types.ts) uses a half-open [frame_in, frame_out) check, matching every
// other interval convention in this codebase (segments/drops/kept-intervals) —
// verified, not an off-by-one (G10).
//
// G10: outStart/outCursor here (Math.max(1, src_out-src_in) per clip, running total)
// MUST stay identical to FastCuts.tsx's cutStarts computation — that file duplicates
// this formula to place its cut-flash at the same boundaries. Traced by hand against
// a degenerate zero-length clip and confirmed byte-identical; if you change this
// formula, change FastCuts.tsx's identically or the flash will visibly drift.
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
