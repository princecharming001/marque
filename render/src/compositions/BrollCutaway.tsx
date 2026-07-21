import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { Grade } from "../components/Grade";
import { BrollLayer } from "../components/BrollLayer";
import { TextCardOverlay } from "../components/TextCardOverlay";
import { Captions } from "../components/Captions";
import { usePunchScale } from "../components/PunchZoom";
import { MontageIntro } from "../components/MontageIntro";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { Watermark } from "../components/Watermark";
import { CompositionProps } from "../types";

// Talking-head A-roll is the spine (full-frame, its audio continuous). At each cue, a
// b-roll clip cuts in FULL-FRAME on top for its window, then hard-cuts back to the face —
// a cutaway, not a PiP or split. The b-roll is muted so the creator's voice never breaks
// (a J-cut: the picture changes, the audio doesn't). Captions stay on top of everything.
// Punch-in zoom applies to the A-roll spine (BrollLayer draws OVER it during a cutaway,
// so a punch overlapping a b-roll window is invisible anyway — never worth guarding).
export const BrollCutaway: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const punchScale = usePunchScale(edl?.overlays);
  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ transform: `scale(${punchScale})` }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      </AbsoluteFill>
      {edl && <BrollLayer broll={edl.broll} />}
      {edl && <TextCardOverlay overlays={edl.overlays} />}
      {edl && <MontageIntro montage={edl.montage} />}
      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Grade look={edl.look} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      {edl?.watermark && <Watermark />}
      <AudioMix audio={edl?.audio} sourceUrl={sourceUrl} />
    </AbsoluteFill>
  );
};
