import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo, clipOutFrames } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { CompositionProps } from "../types";

// Punchy cut track with a 2-frame white flash at every real cut boundary. The boundaries
// are the OUTPUT-timeline starts of each clip (cumulative clip durations), so the flash
// lands exactly where the footage jumps.
//
// G10 (verified, not a bug): this cumulative-sum formula (Math.max(1, src_out-src_in)
// per clip, running total) is IDENTICAL to CutVideo.tsx's outCursor/outStart
// computation — traced by hand against a degenerate zero-length clip
// ({src_in:100,src_out:100}) and confirmed byte-identical boundaries, including the
// "skip pushing acc==0" guard correctly excluding only the true frame-0 start (not a
// cut) while still counting a degenerate clip's own forced 1-frame width toward
// subsequent boundaries. If you change ONE of these two formulas, change the other
// identically or the flash will visibly drift from the actual cut.
export const FastCuts: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const clips = edl?.clips ?? [];

  let acc = 0;
  const cutStarts: number[] = [];
  for (const c of clips) {
    if (acc > 0) cutStarts.push(acc);   // skip the very first (frame 0)
    acc += clipOutFrames(c);            // G10: MUST match CutVideo's outCursor formula
  }

  // P0.4: the old flash strobed — 0.18 opacity for 2 hard frames at EVERY boundary,
  // so a burst of filler micro-cuts (multiple cuts within a few frames) flickered.
  // Now (1) rate-limit: a flash only fires at a cut ≥45 output frames (1.5s) since the
  // last flashed cut — filler micro-cuts get none — and (2) soften: peak 0.10 opacity
  // on the boundary frame, linearly fading to 0 across 3 frames (an accent, not a strobe).
  const FLASH_MIN_GAP = 45;
  const FLASH_PEAK = 0.10;
  const FLASH_FRAMES = 3;
  const flashStarts: number[] = [];
  let lastFlash = -Infinity;
  for (const s of cutStarts) {
    if (s - lastFlash >= FLASH_MIN_GAP) { flashStarts.push(s); lastFlash = s; }
  }
  let flashOpacity = 0;
  for (const s of flashStarts) {
    if (frame >= s && frame < s + FLASH_FRAMES) {
      flashOpacity = Math.max(flashOpacity, FLASH_PEAK * (1 - (frame - s) / FLASH_FRAMES));
    }
  }

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <CutVideo sourceUrl={sourceUrl} clips={clips} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      {flashOpacity > 0 && (
        <div style={{ position: "absolute", inset: 0, background: "white", opacity: flashOpacity }} />
      )}
      {edl && <BrollLayer broll={edl.broll} />}
      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
