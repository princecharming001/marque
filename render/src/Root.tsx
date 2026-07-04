import React from "react";
import { Composition } from "remotion";
import { TalkingHead } from "./compositions/TalkingHead";
import { Faceless } from "./compositions/Faceless";
import { SplitThree } from "./compositions/SplitThree";
import { FastCuts } from "./compositions/FastCuts";
import { GreenScreen } from "./compositions/GreenScreen";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Marque-TalkingHead" component={TalkingHead}
      durationInFrames={720} fps={30} width={1080} height={1920}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "myth-buster" }} />
    <Composition id="Marque-Faceless" component={Faceless}
      durationInFrames={720} fps={30} width={1080} height={1920}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "faceless" }} />
    <Composition id="Marque-SplitThree" component={SplitThree}
      durationInFrames={720} fps={30} width={1080} height={1920}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "listicle" }} />
    <Composition id="Marque-FastCuts" component={FastCuts}
      durationInFrames={720} fps={30} width={1080} height={1920}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "listicle" }} />
    <Composition id="Marque-GreenScreen" component={GreenScreen}
      durationInFrames={720} fps={30} width={1080} height={1920}
      defaultProps={{ sourceUrl: "", edl: null, formatId: "green-screen" }} />
  </>
);
