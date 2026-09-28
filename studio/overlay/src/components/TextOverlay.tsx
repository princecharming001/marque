/**
 * Text overlays anchored to words: hook title, callout, numbered list, lower third, label, CTA.
 *
 * Each kind has one fixed look and one motion preset (consistency is the brand): enter decelerating in
 * 170-300 ms on the trigger frame, hold still, exit accelerating in ~120 ms. An element that starts on
 * frame 0 has no entrance, because frame 0 is the default cover and must read as a still. Every block is
 * measured with the real face and clamped fully inside the safe zone.
 */
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {inkOn, withAlpha} from '../colour';
import {effectiveWeight, fontStack} from '../fonts';
import {clampCenter, fitWords, measure} from '../layout';
import {EASE_EMPHASIZED, EASE_ENTER, exitProgress, lerp, progress, secToFrames} from '../motion';
import type {SafeZone, TextOverlayProps} from '../schema';

type Props = {
  text: TextOverlayProps;
  safe: SafeZone;
  debug: boolean;
};

type Motion = {opacity: number; scale: number; dx: number; dy: number; chars: number | null};

const useMotion = (t: TextOverlayProps, sizePx: number, enterSec: number): Motion => {
  const frame = useCurrentFrame(); // relative to t.start
  const {fps} = useVideoConfig();
  const dur = t.end - t.start;
  const entering = t.start > 0 && t.animation !== 'none';
  const enterF = secToFrames(enterSec, fps, 2);
  const exitF = Math.min(secToFrames(0.12, fps, 2), Math.max(1, Math.floor(dur / 4)));
  const pIn = entering ? progress(frame, 0, enterF, EASE_EMPHASIZED) : 1;
  const out = exitProgress(frame, dur, exitF);
  const m: Motion = {opacity: out, scale: lerp(1, 0.97, 1 - out), dx: 0, dy: 0, chars: null};
  switch (t.animation) {
    case 'pop':
      m.scale *= lerp(0.9, 1, pIn);
      m.opacity *= entering ? progress(frame, 0, Math.max(1, Math.round(enterF / 2)), EASE_ENTER) : 1;
      break;
    case 'fade':
      m.opacity *= pIn;
      break;
    case 'slide':
      m.dy = lerp(sizePx * 0.45, 0, pIn);
      m.opacity *= pIn;
      break;
    case 'typewriter': {
      const total = t.text.length;
      const typeF = Math.max(2, Math.min(secToFrames(0.8, fps), Math.round((total / 28) * fps)));
      m.chars = entering ? Math.min(total, Math.ceil((frame / typeF) * total)) : total;
      break;
    }
    default:
      break;
  }
  return m;
};

/** Stroked or boxed lines of heavy text, centred on (cx, cy) (the shared look of titles/labels). */
const TextLines: React.FC<{
  lines: string[];
  size: number;
  font: string;
  weight: number;
  color: string;
  strokePx: number;
  strokeColor: string;
  align: 'left' | 'center' | 'right';
  cx: number;
  cy: number;
  blockW: number;
  lineH: number;
}> = ({lines, size, font, weight, color, strokePx, strokeColor, align, cx, cy, blockW, lineH}) => {
  const anchor = align === 'left' ? 'start' : align === 'right' ? 'end' : 'middle';
  const x = align === 'left' ? cx - blockW / 2 + strokePx : align === 'right' ? cx + blockW / 2 - strokePx : cx;
  const top = cy - (lines.length * lineH) / 2;
  return (
    <>
      {lines.map((ln, i) => (
        <text
          key={i}
          x={x}
          y={top + i * lineH + lineH / 2}
          textAnchor={anchor}
          dominantBaseline="central"
          fontFamily={fontStack(font)}
          fontWeight={effectiveWeight(font, weight)}
          fontSize={size}
          fill={color}
          stroke={strokePx > 0 ? strokeColor : 'none'}
          strokeWidth={strokePx > 0 ? strokePx * 2 : 0}
          strokeLinejoin="round"
          paintOrder="stroke fill"
          style={{textRendering: 'geometricPrecision'}}
        >
          {ln}
        </text>
      ))}
    </>
  );
};

const SvgLayer: React.FC<{children: React.ReactNode; m: Motion; cx: number; cy: number; shadow?: boolean}> = ({
  children,
  m,
  cx,
  cy,
  shadow = true,
}) => {
  const {width, height} = useVideoConfig();
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
          opacity: m.opacity,
          transform: `translate(${m.dx}px, ${m.dy}px) scale(${m.scale})`,
          transformOrigin: `${cx}px ${cy}px`,
          filter: shadow ? 'drop-shadow(0px 6px 18px rgba(0,0,0,0.38))' : 'none',
        }}
      >
        {children}
      </svg>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------------------------- title/label/callout/cta
const BlockText: React.FC<Props> = ({text: t, safe, debug}) => {
  const {width, height} = useVideoConfig();
  const s = t.style;
  const boxed = s.background !== null;
  const isPill = t.kind === 'callout' || t.kind === 'cta';
  const padX = boxed ? s.sizePx * (isPill ? 0.55 : 0.4) : 0;
  const padY = boxed ? s.sizePx * (isPill ? 0.3 : 0.22) : 0;
  const stroke = boxed ? 0 : s.strokePx;
  const maxLines = t.kind === 'hook_title' ? 3 : t.kind === 'label' ? 2 : 2;
  const words = t.text.split(/\s+/).filter(Boolean);
  const fitted = fitWords({
    words,
    spec: {font: s.font, weight: s.weight},
    sizePx: s.sizePx,
    maxWidth: Math.min(t.maxWidthPx, width - safe.left - safe.right),
    maxLines,
    minScale: 0.82,
    extraPx: 2 * stroke + 2 * padX,
  });
  const size = fitted.sizePx;
  const lineH = size * 1.12;
  const lines = fitted.lines.map((l) => l.map((i) => words[i]).join(' '));
  const blockW = Math.max(...fitted.widths) + 2 * stroke + 2 * padX;
  const blockH = lines.length * lineH + 2 * padY + stroke;
  const {cx, cy} = clampCenter(t.xNorm * width, t.yNorm * height, blockW, blockH, safe, width, height);
  const m = useMotion(t, size, t.kind === 'hook_title' ? 0.24 : 0.18);
  let shown = lines;
  if (m.chars !== null) {
    let left = m.chars;
    shown = lines.map((ln) => {
      const part = ln.slice(0, Math.max(0, left));
      left -= ln.length + 1;
      return part;
    });
  }
  const bg = s.background ?? 'transparent';
  const ink = boxed && s.color.toLowerCase() === 'auto' ? inkOn(bg) : s.color;
  return (
    <SvgLayer m={m} cx={cx} cy={cy} shadow={!boxed || t.kind !== 'lower_third'}>
      {boxed ? (
        <rect
          x={cx - blockW / 2}
          y={cy - blockH / 2}
          width={blockW}
          height={blockH}
          rx={isPill ? blockH / 2 : size * 0.2}
          fill={bg}
        />
      ) : null}
      <TextLines
        lines={shown}
        size={size}
        font={s.font}
        weight={s.weight}
        color={ink}
        strokePx={stroke}
        strokeColor={s.strokeColor}
        align={s.align}
        cx={cx}
        cy={cy}
        blockW={blockW - 2 * padX}
        lineH={lineH}
      />
      {debug ? (
        <rect x={cx - blockW / 2} y={cy - blockH / 2} width={blockW} height={blockH} fill="none" stroke="#FF00E5" strokeWidth={2} />
      ) : null}
    </SvgLayer>
  );
};

// ---------------------------------------------------------------------------------------------- numbered list
const ListText: React.FC<Props> = ({text: t, safe, debug}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const s = t.style;
  const titleSize = s.sizePx;
  const itemBase = s.sizePx * 0.78;
  const maxW = Math.min(t.maxWidthPx, width - safe.left - safe.right);
  const pad = s.background !== null ? s.sizePx * 0.45 : 0;
  const markerD = itemBase * 1.15;
  const gap = itemBase * 0.4;
  const spec = {font: s.font, weight: s.weight};
  const itemSpec = {font: s.font, weight: Math.max(600, s.weight - 100)};
  // one common item size so the list reads as one system
  const itemAvail = maxW - 2 * pad - markerD - gap;
  let itemSize = itemBase;
  for (const it of t.items) {
    const w = measure(it, itemSpec, itemBase);
    if (w > itemAvail) itemSize = Math.min(itemSize, (itemBase * itemAvail) / w);
  }
  itemSize = Math.max(itemSize, itemBase * 0.6);
  const titleWords = t.text.split(/\s+/).filter(Boolean);
  const titleFit = titleWords.length
    ? fitWords({words: titleWords, spec, sizePx: titleSize, maxWidth: maxW - 2 * pad, maxLines: 2, minScale: 0.8})
    : null;
  const titleLines = titleFit ? titleFit.lines.map((l) => l.map((i) => titleWords[i]).join(' ')) : [];
  const tSize = titleFit ? titleFit.sizePx : 0;
  const tLineH = tSize * 1.12;
  const rowH = Math.max(markerD, itemSize * 1.2) * 1.28;
  const itemsW = Math.max(0, ...t.items.map((it) => measure(it, itemSpec, itemSize))) + markerD + gap;
  const blockW = Math.min(maxW, Math.max(titleFit ? Math.max(...titleFit.widths) : 0, itemsW) + 2 * pad);
  const titleH = titleLines.length * tLineH + (titleLines.length ? itemSize * 0.35 : 0);
  const blockH = titleH + t.items.length * rowH + 2 * pad;
  const {cx, cy} = clampCenter(t.xNorm * width, t.yNorm * height, blockW, blockH, safe, width, height);
  const x0 = cx - blockW / 2 + pad;
  const y0 = cy - blockH / 2 + pad;
  const dur = t.end - t.start;
  const entering = t.start > 0 && t.animation !== 'none';
  const out = exitProgress(frame, dur, Math.min(secToFrames(0.12, fps, 2), Math.max(1, Math.floor(dur / 4))));
  const pTitle = entering ? progress(frame, 0, secToFrames(0.2, fps, 2), EASE_EMPHASIZED) : 1;
  const stagger = secToFrames(0.1, fps, 2);
  const titleIn = entering ? secToFrames(0.2, fps) : 0;
  const itemIn = secToFrames(0.22, fps, 2);
  const stroke = s.background !== null ? 0 : Math.max(s.strokePx, itemSize * 0.08);
  return (
    <SvgLayer m={{opacity: out, dx: 0, dy: 0, scale: 1, chars: null}} cx={cx} cy={cy}>
      {s.background !== null ? (
        <rect
          x={cx - blockW / 2}
          y={cy - blockH / 2}
          width={blockW}
          height={blockH}
          rx={itemSize * 0.35}
          fill={s.background}
          opacity={pTitle}
        />
      ) : null}
      {titleLines.length ? (
        <g opacity={pTitle} transform={`translate(0 ${lerp(tSize * 0.3, 0, pTitle)})`}>
          <TextLines
            lines={titleLines}
            size={tSize}
            font={s.font}
            weight={s.weight}
            color={s.color}
            strokePx={stroke}
            strokeColor={s.strokeColor}
            align="left"
            cx={x0 + (blockW - 2 * pad) / 2}
            cy={y0 + (titleLines.length * tLineH) / 2}
            blockW={blockW - 2 * pad}
            lineH={tLineH}
          />
        </g>
      ) : null}
      {t.items.map((it, k) => {
        const abs = t.itemStarts[k];
        const at = abs !== null && abs !== undefined ? abs - t.start : titleIn + k * stagger;
        const p = at <= 0 && t.start === 0 ? 1 : progress(frame, at, itemIn, EASE_EMPHASIZED);
        if (p <= 0) return null;
        const ry = y0 + titleH + k * rowH + rowH / 2;
        const dx = lerp(-itemSize * 0.5, 0, p);
        return (
          <g key={k} opacity={p} transform={`translate(${dx} 0)`}>
            <circle cx={x0 + markerD / 2} cy={ry} r={markerD / 2} fill={t.accent} />
            <text
              x={x0 + markerD / 2}
              y={ry}
              textAnchor="middle"
              dominantBaseline="central"
              fontFamily={fontStack(s.font)}
              fontWeight={effectiveWeight(s.font, 900)}
              fontSize={markerD * 0.58}
              fill={inkOn(t.accent)}
            >
              {k + 1}
            </text>
            <text
              x={x0 + markerD + gap}
              y={ry}
              dominantBaseline="central"
              fontFamily={fontStack(s.font)}
              fontWeight={effectiveWeight(itemSpec.font, itemSpec.weight)}
              fontSize={itemSize}
              fill={s.color}
              stroke={stroke > 0 ? s.strokeColor : 'none'}
              strokeWidth={stroke * 2}
              strokeLinejoin="round"
              paintOrder="stroke fill"
            >
              {it}
            </text>
          </g>
        );
      })}
      {debug ? (
        <rect x={cx - blockW / 2} y={cy - blockH / 2} width={blockW} height={blockH} fill="none" stroke="#FF00E5" strokeWidth={2} />
      ) : null}
    </SvgLayer>
  );
};

// ---------------------------------------------------------------------------------------------- lower third
const LowerThird: React.FC<Props> = ({text: t, safe, debug}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const s = t.style;
  const nameSize = s.sizePx;
  const roleText = t.items[0] ?? '';
  const roleSize = nameSize * 0.62;
  const maxW = Math.min(t.maxWidthPx, width - safe.left - safe.right);
  const barW = Math.max(6, nameSize * 0.12);
  const padX = nameSize * 0.45;
  const padY = nameSize * 0.32;
  const avail = maxW - barW - 2 * padX;
  const nameW0 = measure(t.text, {font: s.font, weight: s.weight}, nameSize);
  const nScale = Math.min(1, avail / Math.max(1, nameW0));
  const roleW0 = roleText ? measure(roleText, {font: s.font, weight: 600}, roleSize) : 0;
  const rScale = roleText ? Math.min(1, avail / Math.max(1, roleW0)) : 1;
  const nS = nameSize * nScale;
  const rS = roleSize * rScale;
  const contentW = Math.max(nameW0 * nScale, roleW0 * rScale);
  const blockW = barW + 2 * padX + contentW;
  const blockH = 2 * padY + nS * 1.1 + (roleText ? rS * 1.3 : 0);
  const {cx, cy} = clampCenter(t.xNorm * width, t.yNorm * height, blockW, blockH, safe, width, height);
  const x0 = cx - blockW / 2;
  const y0 = cy - blockH / 2;
  const dur = t.end - t.start;
  const entering = t.start > 0 && t.animation !== 'none';
  const inF = secToFrames(0.28, fps, 2);
  const pBar = entering ? progress(frame, 0, inF, EASE_EMPHASIZED) : 1;
  const pText = entering ? progress(frame, Math.round(inF / 3), inF, EASE_EMPHASIZED) : 1;
  const out = exitProgress(frame, dur, Math.min(secToFrames(0.15, fps, 2), Math.max(1, Math.floor(dur / 4))));
  const panel = s.background ?? 'rgba(12,12,14,0.78)';
  const clipW = (blockW - barW) * pText;
  const clipId = `lt-${t.id}`;
  return (
    <SvgLayer m={{opacity: out, scale: 1, dx: lerp(-18, 0, out), dy: 0, chars: null}} cx={cx} cy={cy}>
      <defs>
        <clipPath id={clipId}>
          <rect x={x0 + barW} y={y0} width={clipW} height={blockH} />
        </clipPath>
      </defs>
      <rect x={x0} y={y0 + (blockH * (1 - pBar)) / 2} width={barW} height={blockH * pBar} fill={t.accent} />
      <g clipPath={`url(#${clipId})`}>
        <rect x={x0 + barW} y={y0} width={blockW - barW} height={blockH} fill={panel} />
        <text
          x={x0 + barW + padX}
          y={y0 + padY + (nS * 1.1) / 2}
          dominantBaseline="central"
          fontFamily={fontStack(s.font)}
          fontWeight={effectiveWeight(s.font, s.weight)}
          fontSize={nS}
          fill={s.color}
        >
          {t.text}
        </text>
        {roleText ? (
          <text
            x={x0 + barW + padX}
            y={y0 + padY + nS * 1.1 + (rS * 1.3) / 2}
            dominantBaseline="central"
            fontFamily={fontStack(s.font)}
            fontWeight={effectiveWeight(s.font, 600)}
            fontSize={rS}
            fill={withAlpha(s.color, 0.78)}
          >
            {roleText}
          </text>
        ) : null}
      </g>
      {debug ? <rect x={x0} y={y0} width={blockW} height={blockH} fill="none" stroke="#FF00E5" strokeWidth={2} /> : null}
    </SvgLayer>
  );
};

export const TextOverlay: React.FC<Props> = (props) => {
  switch (props.text.kind) {
    case 'list':
      return <ListText {...props} />;
    case 'lower_third':
      return <LowerThird {...props} />;
    default:
      return <BlockText {...props} />;
  }
};
