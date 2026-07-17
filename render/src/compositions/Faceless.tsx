import React from "react";
import { AbsoluteFill } from "remotion";
import { CutVideo, lookFilterCSS } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { Grade } from "../components/Grade";
import { BrollLayer } from "../components/BrollLayer";
import { Captions } from "../components/Captions";
import { ProgressBar } from "../components/ProgressBar";
import { EndCard } from "../components/EndCard";
import { CompositionProps } from "../types";

// Faceless / voiceover: NO face on screen — full-frame b-roll under the creator's
// voiceover + kinetic captions. The creator's cut track still renders (opacity 0) purely
// to carry the voiceover AUDIO; the b-roll layer covers it visually. The ground under
// uncovered moments is a soft dark gradient, NOT flat black: with zero resolved b-roll
// (Pexels down / no matches) the whole video is this ground + captions, and flat #000
// read as a broken render rather than an intentional text-forward look.
export const Faceless: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  // Formatting fix #7: a chosen color grade used to be a visual no-op here — CutVideo
  // (the only thing that ever read `look`) is opacity:0 in this style, so its filter
  // never painted anything, and the VISIBLE layer (b-roll) never received the look at
  // all. Apply it directly to the b-roll wrapper instead. `url(#id)` filter references
  // (the temperature grade) still resolve: CutVideo's SVG <defs> stay present in the
  // DOM at opacity:0 — opacity hides painting, it doesn't remove the element — so
  // referencing the same def from this unrelated element works.
  const brollFilter = edl ? lookFilterCSS(edl.look) : "";
  return (
    <AbsoluteFill style={{ background: "linear-gradient(160deg, #14141c 0%, #0e0e14 55%, #191922 100%)" }}>
      <AbsoluteFill style={{ opacity: 0 }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      </AbsoluteFill>
      {edl && (
        <AbsoluteFill style={brollFilter ? { filter: brollFilter } : undefined}>
          <BrollLayer broll={edl.broll} />
        </AbsoluteFill>
      )}
      {edl?.progress_bar && <ProgressBar totalFrames={edl.total_frames} />}
      {edl && <Grade look={edl.look} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} captions={edl.captions} captionStyle={edl.caption_style} captionOptions={edl.caption_options} />}
      {edl && <Grade transitions={edl.transitions} />}
      {edl && <EndCard endCard={edl.end_card} />}
      <AudioMix audio={edl?.audio} sourceUrl={sourceUrl} />
    </AbsoluteFill>
  );
};
