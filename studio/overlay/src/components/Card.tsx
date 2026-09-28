/**
 * Full-frame (or split/pip-region) designed cards: title, number, stat, list, steps, quote, comparison.
 *
 * Design rules (transitions-and-graphics.md): one display face + the caption face, one accent plus
 * white/near-black, one motion preset per element type. All card text stays inside the safe zone and
 * above the caption band (reserveBottomPx) so captions can keep running over the card. Card-level
 * transition in/out comes from the insert (cut/fade/dissolve/slide/zoom/whip); inner parts then enter
 * decelerating with a 2-4 frame stagger and hold still. Counters land on their final value within
 * ~0.7 s (they start on the spoken number). Sizes are measured with the loaded faces, never guessed.
 */
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {inkOn, withAlpha} from '../colour';
import {effectiveWeight, fontStack} from '../fonts';
import {fitWords, formatCounter, measure, parseCounter} from '../layout';
import {EASE_EMPHASIZED, EASE_EXIT, EASE_IN_OUT, lerp, progress, secToFrames} from '../motion';
import type {CardProps, SafeZone} from '../schema';

type Props = {card: CardProps; safe: SafeZone; debug: boolean};

const BASE_W = 823; // design width of the house safe band (x 65-888)

type Lines = {lines: string[]; size: number; widths: number[]};

const fitLines = (text: string, font: string, weight: number, size: number, maxW: number, maxLines: number, minScale = 0.55): Lines => {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return {lines: [], size, widths: []};
  const f = fitWords({words, spec: {font, weight}, sizePx: size, maxWidth: maxW, maxLines, minScale, preferLines: 1, wrapFirst: true});
  return {lines: f.lines.map((l) => l.map((i) => words[i]).join(' ')), size: f.sizePx, widths: f.widths};
};

const TextBlock: React.FC<{
  l: Lines;
  font: string;
  weight: number;
  color: string;
  lineHeight?: number;
  align?: 'left' | 'center';
  style?: React.CSSProperties;
  upper?: boolean;
}> = ({l, font, weight, color, lineHeight = 1.08, align = 'center', style, upper}) => (
  <div style={{textAlign: align, ...style}}>
    {l.lines.map((ln, i) => (
      <div
        key={i}
        style={{
          fontFamily: fontStack(font),
          fontWeight: effectiveWeight(font, weight),
          fontSize: l.size,
          lineHeight,
          color,
          whiteSpace: 'pre',
          textTransform: upper ? 'uppercase' : undefined,
        }}
      >
        {ln}
      </div>
    ))}
  </div>
);

export const Card: React.FC<Props> = ({card, safe, debug}) => {
  const frame = useCurrentFrame(); // relative to card.start
  const {width, height, fps} = useVideoConfig();
  const dur = card.end - card.start;

  // ------------------------------------------------------------------ container geometry
  const full = card.rect === null;
  const rx = full ? 0 : card.rect![0] * width;
  const ry = full ? 0 : card.rect![1] * height;
  const rw = full ? width : card.rect![2] * width;
  const rh = full ? height : card.rect![3] * height;
  const cLeft = full ? safe.left : rw * 0.07;
  const cRight = full ? width - safe.right : rw * 0.93;
  const cTop = full ? safe.top : rh * 0.08;
  const cBottom = full ? height - safe.bottom - card.reserveBottomPx : rh * 0.92;
  const cW = Math.max(100, cRight - cLeft);
  const cH = Math.max(100, cBottom - cTop);
  const u = Math.min(cW / BASE_W, (cH / 900) * 1.15, 1.6);

  // ------------------------------------------------------------------ container transition
  const tin = card.transitionIn;
  const tout = card.transitionOut;
  const inF = card.start > 0 ? tin.frames : 0;
  const outF = Math.min(tout.frames, Math.max(0, Math.floor(dur / 3)));
  const pIn = tin.kind === 'cut' || inF === 0 ? 1 : progress(frame, 0, inF, EASE_EMPHASIZED);
  const pOut = tout.kind === 'cut' || outF === 0 ? 0 : progress(frame, dur - outF, outF, EASE_EXIT);
  let opacity = 1;
  let tx = 0;
  let ty = 0;
  let scale = 1;
  let blur = 0;
  const applyIn = (kind: string, p: number) => {
    const q = 1 - p;
    if (kind === 'fade' || kind === 'dissolve') opacity *= p;
    if (kind === 'slide') ty += q * rh;
    if (kind === 'zoom') {
      scale *= lerp(1.08, 1, p);
      opacity *= p;
    }
    if (kind === 'whip') {
      tx += q * rw;
      blur += q * 36;
    }
  };
  const applyOut = (kind: string, p: number) => {
    if (kind === 'fade' || kind === 'dissolve') opacity *= 1 - p;
    if (kind === 'slide') ty -= p * rh;
    if (kind === 'zoom') {
      scale *= lerp(1, 0.96, p);
      opacity *= 1 - p;
    }
    if (kind === 'whip') {
      tx -= p * rw;
      blur += p * 36;
    }
  };
  applyIn(tin.kind, pIn);
  applyOut(tout.kind, pOut);

  // content enters as the container settles (overlap by half the container move)
  const content0 = Math.round(inF * 0.5);
  const stagger = secToFrames(0.1, fps, 2);
  const partF = secToFrames(0.26, fps, 3);
  const part = (k: number): React.CSSProperties => {
    const p = card.start === 0 && content0 === 0 && k === 0 ? 1 : progress(frame, content0 + k * stagger, partF, EASE_EMPHASIZED);
    return {opacity: p, transform: `translateY(${lerp(28 * u, 0, p)}px)`};
  };

  const accent = card.accent;
  const transparentBg = card.background.trim().toLowerCase() === 'transparent';
  const ink = transparentBg ? '#FFFFFF' : inkOn(card.background.startsWith('#') || card.background.startsWith('rgb') ? card.background : '#111111');
  const soft = withAlpha(ink, 0.74);
  const shadow = transparentBg ? '0 4px 24px rgba(0,0,0,0.55)' : undefined;
  const body = card.font;
  const display = card.displayFont;
  const displayUpper = display === 'Anton';

  // ------------------------------------------------------------------ templates
  let content: React.ReactNode = null;
  const col: React.CSSProperties = {display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 28 * u};

  if (card.template === 'title') {
    const title = fitLines(card.title || card.body, display, 900, 132 * u, cW, 3);
    const sub = fitLines(card.subtitle, body, 600, 46 * u, cW * 0.92, 2);
    const barP = progress(frame, content0 + stagger, secToFrames(0.35, fps), EASE_IN_OUT);
    content = (
      <div style={col}>
        <div style={part(0)}>
          <TextBlock l={title} font={display} weight={900} color={ink} upper={displayUpper} style={{textShadow: shadow}} />
        </div>
        <div style={{width: 150 * u * barP, height: 12 * u, borderRadius: 6 * u, background: accent}} />
        {sub.lines.length ? (
          <div style={part(2)}>
            <TextBlock l={sub} font={body} weight={600} color={soft} lineHeight={1.2} style={{textShadow: shadow}} />
          </div>
        ) : null}
      </div>
    );
  } else if (card.template === 'number' || card.template === 'stat') {
    const raw = card.number ?? card.title;
    const counter = parseCounter(raw);
    const countP = progress(frame, content0, secToFrames(0.7, fps), EASE_IN_OUT);
    const shown = counter ? formatCounter(counter, counter.value * countP) : raw;
    const unit = card.unit ?? '';
    const inlineUnit = unit.length > 0 && unit.length <= 3;
    const numMaxW = cW * (inlineUnit ? 0.8 : 1);
    const numSize0 = 280 * u;
    const numW = measure(raw, {font: display, weight: 900}, numSize0);
    const numSize = Math.min(numSize0, (numSize0 * numMaxW) / Math.max(1, numW));
    const unitSize = numSize * 0.42;
    const label = fitLines(card.number ? card.title : card.subtitle, body, 800, 60 * u, cW, 2);
    const sub = fitLines(card.number ? card.subtitle || card.body : card.body, body, 600, 40 * u, cW * 0.92, 2);
    const pct = counter && (counter.suffix.trim() === '%' || unit.trim() === '%') ? Math.max(0, Math.min(100, counter.value)) : null;
    content = (
      <div style={col}>
        <div style={{...part(0), display: 'flex', alignItems: 'baseline', justifyContent: 'center'}}>
          <div
            style={{
              fontFamily: fontStack(display),
              fontWeight: effectiveWeight(display, 900),
              fontSize: numSize,
              lineHeight: 1,
              color: accent,
              fontVariantNumeric: 'tabular-nums',
              minWidth: numW * (numSize / numSize0),
              textAlign: 'center',
              whiteSpace: 'pre',
              textShadow: shadow,
            }}
          >
            {shown}
          </div>
          {inlineUnit ? (
            <div
              style={{
                fontFamily: fontStack(display),
                fontWeight: effectiveWeight(display, 900),
                fontSize: unitSize,
                color: accent,
                marginLeft: 8 * u,
                textShadow: shadow,
              }}
            >
              {unit}
            </div>
          ) : null}
        </div>
        {!inlineUnit && unit ? (
          <div style={part(1)}>
            <TextBlock l={fitLines(unit, body, 800, 54 * u, cW, 1)} font={body} weight={800} color={ink} />
          </div>
        ) : null}
        {pct !== null ? (
          <div style={{...part(1), width: cW * 0.82, height: 20 * u, borderRadius: 10 * u, background: withAlpha(ink, 0.16)}}>
            <div style={{width: `${pct * countP}%`, height: '100%', borderRadius: 10 * u, background: accent}} />
          </div>
        ) : null}
        {label.lines.length ? (
          <div style={part(2)}>
            <TextBlock l={label} font={body} weight={800} color={ink} style={{textShadow: shadow}} />
          </div>
        ) : null}
        {sub.lines.length ? (
          <div style={part(3)}>
            <TextBlock l={sub} font={body} weight={600} color={soft} lineHeight={1.2} style={{textShadow: shadow}} />
          </div>
        ) : null}
      </div>
    );
  } else if (card.template === 'list' || card.template === 'steps') {
    const title = fitLines(card.title, display, 900, 88 * u, cW, 2);
    const markerD = 64 * u;
    const gap = 26 * u;
    const itemW = cW - markerD - gap;
    const itemFits = card.items.map((it) => fitLines(it, body, 700, 50 * u, itemW, 2, 0.7));
    const itemSize = Math.min(...itemFits.map((f) => f.size), 50 * u);
    const items = card.items.map((it) => fitLines(it, body, 700, itemSize, itemW, 2, 1));
    const steps = card.template === 'steps';
    const rowGap = (steps ? 34 : 26) * u;
    const lineP = progress(frame, content0 + stagger, stagger * Math.max(1, items.length) + partF, EASE_IN_OUT);
    content = (
      <div style={{display: 'flex', flexDirection: 'column', gap: 44 * u, width: cW}}>
        {title.lines.length ? (
          <div style={part(0)}>
            <TextBlock l={title} font={display} weight={900} color={ink} align="left" upper={displayUpper} style={{textShadow: shadow}} />
          </div>
        ) : null}
        <div style={{position: 'relative', display: 'flex', flexDirection: 'column', gap: rowGap}}>
          {steps && items.length > 1 ? (
            <div
              style={{
                position: 'absolute',
                left: markerD / 2 - 3 * u,
                top: markerD / 2,
                width: 6 * u,
                height: `calc(${lineP * 100}% - ${markerD}px)`,
                background: withAlpha(accent, 0.55),
                borderRadius: 3 * u,
              }}
            />
          ) : null}
          {items.map((l, k) => (
            <div key={k} style={{...part(k + 1), display: 'flex', alignItems: 'center', gap}}>
              <div
                style={{
                  width: markerD,
                  height: markerD,
                  flex: 'none',
                  borderRadius: steps ? markerD / 2 : 14 * u,
                  background: accent,
                  color: inkOn(accent),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontFamily: fontStack(display),
                  fontWeight: effectiveWeight(display, 900),
                  fontSize: markerD * 0.56,
                }}
              >
                {k + 1}
              </div>
              <TextBlock l={l} font={body} weight={700} color={ink} align="left" lineHeight={1.15} style={{textShadow: shadow}} />
            </div>
          ))}
        </div>
        {card.body ? (
          <div style={part(items.length + 1)}>
            <TextBlock l={fitLines(card.body, body, 600, 38 * u, cW, 2)} font={body} weight={600} color={soft} align="left" />
          </div>
        ) : null}
      </div>
    );
  } else if (card.template === 'quote') {
    const q = fitLines(card.title || card.body, body, 800, 72 * u, cW, 6, 0.6);
    const attribution = card.subtitle ? `— ${card.subtitle}` : '';
    content = (
      <div style={{...col, alignItems: 'flex-start', width: cW}}>
        <div
          style={{
            ...part(0),
            fontFamily: fontStack(body),
            fontWeight: effectiveWeight(body, 900),
            fontSize: 230 * u,
            lineHeight: 0.8,
            height: 130 * u,
            color: accent,
          }}
        >
          {'“'}
        </div>
        <div style={part(1)}>
          <TextBlock l={q} font={body} weight={800} color={ink} align="left" lineHeight={1.16} style={{textShadow: shadow}} />
        </div>
        {attribution ? (
          <div style={part(2)}>
            <TextBlock l={fitLines(attribution, body, 600, 42 * u, cW, 1)} font={body} weight={600} color={soft} align="left" />
          </div>
        ) : null}
      </div>
    );
  } else if (card.template === 'comparison') {
    let [left, right, leftBody, rightBody] = card.items;
    let heading = card.title;
    if ((!left || !right) && /\s+vs\.?\s+/i.test(card.title)) {
      [left, right] = card.title.split(/\s+vs\.?\s+/i);
      heading = '';
    }
    const colW = (cW - 36 * u) / 2;
    const head = fitLines(heading, display, 900, 80 * u, cW, 2);
    const colHead = [fitLines(left ?? '', display, 900, 70 * u, colW - 40 * u, 2), fitLines(right ?? '', display, 900, 70 * u, colW - 40 * u, 2)];
    const hs = Math.min(colHead[0].size, colHead[1].size);
    const heads = [fitLines(left ?? '', display, 900, hs, colW - 40 * u, 2, 1), fitLines(right ?? '', display, 900, hs, colW - 40 * u, 2, 1)];
    const bodies = [fitLines(leftBody ?? '', body, 600, 38 * u, colW - 40 * u, 4), fitLines(rightBody ?? '', body, 600, 38 * u, colW - 40 * u, 4)];
    const vsD = 92 * u;
    content = (
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 48 * u, width: cW}}>
        {head.lines.length ? (
          <div style={part(0)}>
            <TextBlock l={head} font={display} weight={900} color={ink} upper={displayUpper} />
          </div>
        ) : null}
        <div style={{position: 'relative', display: 'flex', gap: 36 * u, width: cW}}>
          {[0, 1].map((k) => (
            <div
              key={k}
              style={{
                ...part(k + 1),
                width: colW,
                minHeight: 320 * u,
                borderRadius: 28 * u,
                padding: 20 * u,
                boxSizing: 'border-box',
                background: k === 0 ? withAlpha(ink, 0.08) : withAlpha(accent, 0.14),
                border: `${4 * u}px solid ${k === 0 ? withAlpha(ink, 0.18) : accent}`,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 18 * u,
              }}
            >
              <TextBlock l={heads[k]} font={display} weight={900} color={k === 0 ? soft : ink} upper={displayUpper} />
              {bodies[k].lines.length ? <TextBlock l={bodies[k]} font={body} weight={600} color={soft} lineHeight={1.2} /> : null}
            </div>
          ))}
          <div
            style={{
              ...part(3),
              position: 'absolute',
              left: cW / 2 - vsD / 2,
              top: `calc(50% - ${vsD / 2}px)`,
              width: vsD,
              height: vsD,
              borderRadius: vsD / 2,
              background: accent,
              color: inkOn(accent),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: fontStack(display),
              fontWeight: effectiveWeight(display, 900),
              fontSize: vsD * 0.4,
            }}
          >
            VS
          </div>
        </div>
      </div>
    );
  }

  return (
    <AbsoluteFill style={{opacity}}>
      <div
        style={{
          position: 'absolute',
          left: rx,
          top: ry,
          width: rw,
          height: rh,
          overflow: 'hidden',
          borderRadius: full ? 0 : 36,
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          filter: blur > 0.5 ? `blur(${blur}px)` : undefined,
          background: transparentBg ? undefined : card.background,
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: cLeft,
            top: cTop,
            width: cW,
            height: cH,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            outline: debug ? '2px dashed #00FF88' : undefined,
          }}
        >
          {content}
        </div>
      </div>
    </AbsoluteFill>
  );
};
