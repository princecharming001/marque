import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { usePunchScale } from "../components/PunchZoom";
import { CompositionProps } from "../types";

// Three stacked panels of the same cut track; the active third lights up in sequence.
// Panel timing is derived from the OUTPUT duration (total_frames / 3) rather than the
// editorial layout.panel_boundaries, which were authored in pre-cut source coords.
// Formatting fix #6: this used to burn hardcoded English "Solution 1/2/3 ✓" labels
// into every render regardless of content — wrong for non-3-item, non-English, or
// non-listicle takes. A clean panel beats fake copy (same rationale as GreenScreen's
// removed "Reference post" placeholder). If real per-panel labels are wanted later,
// the backend should author them as genuine text_sticker overlays (no schema change
// needed) rather than the composition inventing them.
export const SplitThree: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const punchScale = usePunchScale(edl?.overlays);
  const total = edl?.total_frames ?? 720;
  const third = Math.max(1, Math.floor(total / 3));
  const active = Math.min(2, Math.floor(frame / third));

  return (
    // Whole-canvas punch (v1): zooms all three panels together rather than each
    // panel's video independently — simplest correct behavior for a style whose
    // "frame" is the 3-panel composite, not any single panel.
    <AbsoluteFill style={{ transform: `scale(${punchScale})` }}>
      <AbsoluteFill style={{ background: "#000", flexDirection: "column" }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{
            flex: 1, position: "relative", borderBottom: i < 2 ? "2px solid #333" : "none",
            opacity: active === i ? 1 : 0.4, overflow: "hidden",
          }}>
            <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
          </div>
        ))}
        {edl && <BrollLayer broll={edl.broll} />}
        {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
        {edl && <TextStickers overlays={edl.overlays} />}
        {edl && <Grade look={edl.look} transitions={edl.transitions} />}
        <AudioMix audio={edl?.audio} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
