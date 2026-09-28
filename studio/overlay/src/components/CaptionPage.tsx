/**
 * One caption page (a 1-4 word phrase chunk).
 *
 * Rendering choices (quality):
 * - Words are drawn as SVG <text> with an outer stroke (paint-order: stroke) and ROUND line joins. CSS
 *   -webkit-text-stroke uses miter joins, which spike on heavy faces (M, N, V, W) at thick strokes.
 * - Every word is measured with the loaded face and placed explicitly, so the accent word can scale
 *   around its own centre without reflowing the line.
 * - Lines are fitted to the caption width: shrink first (down to minScale), then balanced wrapping when
 *   the style allows more than one line, so a page never leaves the safe band.
 * - Soft drop shadow via a CSS filter on the whole block (covers fill + stroke, no per-glyph halos).
 * - Motion: at most one entrance per page (100-200 ms, scale from 0.9 or opacity), then hold still.
 *   The accent word turns to the highlight colour on its measured onset (minus highlightLeadFrames) with
 *   a <= 1.06x settle; "karaoke" dims upcoming words (brightness, never the accent colour).
 */
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {mix} from '../colour';
import {effectiveWeight, fontStack} from '../fonts';
import {fitWords, measure} from '../layout';
import {EASE_ENTER, lerp, progress, secToFrames} from '../motion';
import type {CaptionLayout, CaptionPageProps, SafeZone} from '../schema';

type Props = {
  page: CaptionPageProps;
  layout: CaptionLayout;
  safe: SafeZone;
  debug: boolean;
};

export const CaptionPage: React.FC<Props> = ({page, layout, safe, debug}) => {
  const frame = useCurrentFrame(); // relative to page.start
  const {fps, width, height} = useVideoConfig();
  const {style} = page;
  const spec = {font: style.font, weight: style.weight, letterSpacingEm: style.letterSpacingEm};
  const words = page.words.map((w) => w.text);
  const hasBox = style.background !== null;
  const stroke = hasBox ? 0 : style.strokePx;
  const padX = hasBox ? style.sizePx * 0.32 : 0;
  const padY = hasBox ? style.sizePx * 0.14 : 0;

  const left = Math.max(layout.leftPx, safe.left);
  const right = Math.min(layout.rightPx, width - safe.right);
  const maxWidth = Math.min(layout.maxWidthPx, right - left);
  const fitted = fitWords({
    words,
    spec,
    sizePx: style.sizePx,
    maxWidth,
    maxLines: style.maxLines,
    minScale: layout.minScale,
    extraPx: 2 * stroke + 2 * padX,
  });
  const size = fitted.sizePx;
  const lineH = size * style.lineHeight;
  const space = measure(' ', spec, size);
  const blockW = Math.max(...fitted.widths) + 2 * stroke + 2 * padX;
  const blockH = fitted.lines.length * lineH + 2 * padY + stroke;

  // horizontal: preferred centre, shifted to stay inside [left, right]
  let cx = layout.xCenterPx;
  cx = Math.min(cx, right - blockW / 2);
  cx = Math.max(cx, left + blockW / 2);
  // vertical: Python placed the block (face-aware, safe band); only keep it inside the frame
  let cy = page.yNorm * height;
  cy = Math.min(Math.max(cy, blockH / 2), height - blockH / 2);

  // ------------------------------------------------------------------ page entrance
  const entering = page.start > 0 && style.animation !== 'none';
  const enterFrames = secToFrames(0.15, fps, 2);
  const p = entering ? progress(frame, 0, enterFrames, EASE_ENTER) : 1;
  const fadeP = entering ? progress(frame, 0, Math.max(1, secToFrames(0.07, fps)), EASE_ENTER) : 1;
  let pageScale = 1;
  let pageOpacity = 1;
  let pageDy = 0;
  switch (style.animation) {
    case 'pop':
      pageScale = lerp(0.9, 1, p);
      pageOpacity = fadeP;
      break;
    case 'fade':
    case 'karaoke':
      pageOpacity = entering ? progress(frame, 0, secToFrames(0.1, fps, 2), EASE_ENTER) : 1;
      break;
    case 'slide':
      pageDy = lerp(size * 0.3, 0, p);
      pageOpacity = fadeP;
      break;
    default:
      break;
  }

  // ------------------------------------------------------------------ word placement
  const top0 = cy - blockH / 2 + padY + stroke / 2;
  const placed: {i: number; x: number; y: number; w: number}[] = [];
  fitted.lines.forEach((line, li) => {
    const widths = line.map((i) => measure(words[i], spec, size));
    const lineW = widths.reduce((a, b) => a + b, 0) + space * (line.length - 1);
    let x = cx - lineW / 2;
    const y = top0 + li * lineH + lineH / 2;
    line.forEach((i, k) => {
      placed.push({i, x: x + widths[k] / 2, y, w: widths[k]});
      x += widths[k] + space;
    });
  });

  const weight = effectiveWeight(style.font, style.weight);
  const family = fontStack(style.font);
  const shadow = style.shadow ? 'drop-shadow(0px 4px 14px rgba(0,0,0,0.42)) drop-shadow(0px 1px 2px rgba(0,0,0,0.35))' : 'none';
  const lead = style.highlightLeadFrames;
  const accentFrames = secToFrames(0.1, fps, 2);

  return (
    <AbsoluteFill>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{
          position: 'absolute',
          inset: 0,
          overflow: 'visible',
          filter: shadow,
          opacity: pageOpacity,
          transform: `translateY(${pageDy}px) scale(${pageScale})`,
          transformOrigin: `${cx}px ${cy}px`,
        }}
      >
        {hasBox ? (
          <rect
            x={cx - blockW / 2}
            y={cy - blockH / 2}
            width={blockW}
            height={blockH}
            rx={size * 0.22}
            fill={style.background ?? 'transparent'}
          />
        ) : null}
        {placed.map(({i, x, y}) => {
          const w = page.words[i];
          const rel = w.start - page.start; // word onset, relative frames
          let fill = style.color;
          let scale = 1;
          let opacity = 1;
          if (w.emphasis) {
            const t = progress(frame, rel - lead, accentFrames, EASE_ENTER);
            fill = mix(style.color, style.highlightColor, t);
            scale = lerp(1, 1.06, t);
          }
          if (style.animation === 'karaoke' && frame < rel - lead) {
            opacity = 0.5;
          }
          return (
            <g key={i} transform={`translate(${x} ${y}) scale(${scale}) translate(${-x} ${-y})`} opacity={opacity}>
              <text
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="central"
                fontFamily={family}
                fontWeight={weight}
                fontSize={size}
                letterSpacing={style.letterSpacingEm ? `${style.letterSpacingEm}em` : undefined}
                fill={fill}
                stroke={stroke > 0 ? style.strokeColor : 'none'}
                strokeWidth={stroke > 0 ? stroke * 2 : 0}
                strokeLinejoin="round"
                strokeLinecap="round"
                paintOrder="stroke fill"
                style={{fontKerning: 'normal', textRendering: 'geometricPrecision'}}
              >
                {words[i]}
              </text>
            </g>
          );
        })}
        {debug ? (
          <rect
            x={cx - blockW / 2}
            y={cy - blockH / 2}
            width={blockW}
            height={blockH}
            fill="none"
            stroke="#00E5FF"
            strokeWidth={2}
          />
        ) : null}
      </svg>
    </AbsoluteFill>
  );
};
