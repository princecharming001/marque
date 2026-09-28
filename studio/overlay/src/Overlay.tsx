/**
 * The "Overlay" composition: a transparent 9:16 layer holding every graphic the A-roll does not.
 *
 * Z-order (bottom -> top): designed cards, pointing graphics, text overlays, captions. Only elements
 * active on the current frame are mounted, each inside a <Sequence> so its own clock starts at 0.
 * Nothing is drawn until every font face is loaded (fonts.ts), so measurements are always exact.
 * There is deliberately no background: PNG frames keep the alpha channel for ProRes 4444.
 */
import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {Card} from './components/Card';
import {CaptionPage} from './components/CaptionPage';
import {Graphic} from './components/Graphic';
import {TextOverlay} from './components/TextOverlay';
import {useFontsReady} from './fonts';
import type {OverlayProps} from './schema';

const active = <T extends {start: number; end: number}>(items: T[], frame: number): T[] =>
  items.filter((it) => frame >= it.start && frame < it.end && it.end > it.start);

const SafeZoneGuide: React.FC<{props: OverlayProps}> = ({props}) => {
  const {width, height} = useVideoConfig();
  const {safe} = props;
  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: safe.left,
          top: safe.top,
          width: width - safe.left - safe.right,
          height: height - safe.top - safe.bottom,
          outline: '3px dashed rgba(255,64,64,0.9)',
        }}
      />
    </AbsoluteFill>
  );
};

export const Overlay: React.FC<OverlayProps> = (props) => {
  const ready = useFontsReady();
  const frame = useCurrentFrame();
  if (!ready) return <AbsoluteFill />;
  return (
    <AbsoluteFill>
      {active(props.cards, frame).map((c) => (
        <Sequence key={c.id} from={c.start} durationInFrames={c.end - c.start} name={`card ${c.id}`}>
          <Card card={c} safe={props.safe} debug={props.debug} />
        </Sequence>
      ))}
      {active(props.graphics, frame).map((g) => (
        <Sequence key={g.id} from={g.start} durationInFrames={g.end - g.start} name={`graphic ${g.id}`}>
          <Graphic g={g} debug={props.debug} />
        </Sequence>
      ))}
      {active(props.texts, frame).map((t) => (
        <Sequence key={t.id} from={t.start} durationInFrames={t.end - t.start} name={`text ${t.id}`}>
          <TextOverlay text={t} safe={props.safe} debug={props.debug} />
        </Sequence>
      ))}
      {active(props.captions, frame).map((p) => (
        <Sequence key={p.id} from={p.start} durationInFrames={p.end - p.start} name={`caption ${p.id}`}>
          <CaptionPage page={p} layout={props.captionLayout} safe={props.safe} debug={props.debug} />
        </Sequence>
      ))}
      {props.debug ? <SafeZoneGuide props={props} /> : null}
    </AbsoluteFill>
  );
};
