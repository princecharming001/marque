import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { TextCardOverlay } from "../components/TextCardOverlay";
import { Grade } from "../components/Grade";
import { Captions } from "../components/Captions";
import { usePunchScale } from "../components/PunchZoom";
import { MontageIntro } from "../components/MontageIntro";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { CompositionProps } from "../types";

// Single-speaker cut with optional punch-in zoom. The cut track (CutVideo) trims the
// source per the plan's clips; the punch-in scale is applied to a wrapper at the
// composition level (global output frame) rather than inside the Series, since overlay
// coords are output-timeline coords.
export const TalkingHead: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const scale = usePunchScale(edl?.overlays);

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      </AbsoluteFill>
      {edl && <BrollLayer broll={edl.broll} />}
      {edl && <TextCardOverlay overlays={edl.overlays} />}
      {edl && <MontageIntro montage={edl.montage} />}
      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
