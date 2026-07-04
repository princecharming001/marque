import React from "react";
import { Composition } from "remotion";
import { TalkingHead } from "./compositions/TalkingHead";
import { Faceless } from "./compositions/Faceless";
import { SplitThree } from "./compositions/SplitThree";
import { FastCuts } from "./compositions/FastCuts";
import { GreenScreen } from "./compositions/GreenScreen";
import { BrollCutaway } from "./compositions/BrollCutaway";
import { DuetSplit } from "./compositions/DuetSplit";
import { CompositionProps, planDuration } from "./types";

// durationInFrames is resolved per-render from the plan's total_frames (the post-cut
// output length) via calculateMetadata — a fixed 720 would either pad short edits with a
// frozen last frame or truncate long ones. IDs use hyphens, not underscores: Remotion
// Lambda rejects underscores in composition IDs at render time.
const common = { fps: 30, width: 1080, height: 1920, durationInFrames: 720 } as const;
const meta = ({ props }: { props: CompositionProps }) => ({ durationInFrames: planDuration(props) });

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Marque-TalkingHead" component={TalkingHead} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "myth-buster" }} />
    <Composition id="Marque-Faceless" component={Faceless} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "faceless" }} />
    <Composition id="Marque-SplitThree" component={SplitThree} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "listicle" }} />
    <Composition id="Marque-FastCuts" component={FastCuts} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "listicle" }} />
    <Composition id="Marque-GreenScreen" component={GreenScreen} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "green-screen" }} />
    <Composition id="Marque-BrollCutaway" component={BrollCutaway} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "myth-buster" }} />
    <Composition id="Marque-DuetSplit" component={DuetSplit} calculateMetadata={meta} {...common}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "green-screen" }} />
  </>
);
