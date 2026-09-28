/**
 * Pointing graphics: arrow, circle highlight, box highlight, underline.
 *
 * They draw on once in drawFrames (6-10 frames at 30 fps, eased) and then stay put; exit is a quick
 * fade. Strokes are round-capped with a soft shadow so they read on any footage. Circles are drawn as a
 * slightly-overshooting hand-drawn loop (starts before 12 o'clock and overlaps itself) because a perfect
 * ellipse reads as UI chrome rather than an editor's mark.
 */
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {EASE_IN_OUT, exitProgress, progress, secToFrames} from '../motion';
import type {GraphicProps} from '../schema';

type Pt = {x: number; y: number};

const quadPoint = (a: Pt, c: Pt, b: Pt, t: number): Pt => ({
  x: (1 - t) * (1 - t) * a.x + 2 * (1 - t) * t * c.x + t * t * b.x,
  y: (1 - t) * (1 - t) * a.y + 2 * (1 - t) * t * c.y + t * t * b.y,
});

const polyLength = (pts: Pt[]): number => {
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return len;
};

const toPath = (pts: Pt[]): string => pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');

export const Graphic: React.FC<{g: GraphicProps; debug: boolean}> = ({g}) => {
  const frame = useCurrentFrame(); // relative to g.start
  const {width, height, fps} = useVideoConfig();
  const dur = g.end - g.start;
  const draw = g.start === 0 ? 1 : progress(frame, 0, g.drawFrames, EASE_IN_OUT);
  const out = exitProgress(frame, dur, Math.min(secToFrames(0.12, fps, 2), Math.max(1, Math.floor(dur / 4))));
  const sw = g.strokePx;

  let pts: Pt[] = [];
  let head: React.ReactNode = null;
  if (g.kind === 'arrow') {
    const a = {x: (g.from?.x ?? g.x) * width, y: (g.from?.y ?? g.y) * height};
    const b = {x: (g.to?.x ?? g.x + g.w) * width, y: (g.to?.y ?? g.y + g.h) * height};
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const nx = -(b.y - a.y) / len;
    const ny = (b.x - a.x) / len;
    const c = {x: (a.x + b.x) / 2 + nx * g.curvature * len * 0.5, y: (a.y + b.y) / 2 + ny * g.curvature * len * 0.5};
    pts = Array.from({length: 49}, (_, i) => quadPoint(a, c, b, i / 48));
    // arrowhead follows the tangent at the tip and appears once the shaft is nearly drawn
    const tip = pts[pts.length - 1];
    const prev = pts[pts.length - 4];
    const ang = Math.atan2(tip.y - prev.y, tip.x - prev.x);
    const hl = Math.max(sw * 3.2, len * 0.12);
    const hp = progress(draw, 0.82, 0.18);
    const wing = (sign: number) => ({
      x: tip.x - Math.cos(ang + sign * 0.5) * hl * hp,
      y: tip.y - Math.sin(ang + sign * 0.5) * hl * hp,
    });
    if (hp > 0) {
      const w1 = wing(1);
      const w2 = wing(-1);
      head = (
        <path
          d={`M${w1.x} ${w1.y} L${tip.x} ${tip.y} L${w2.x} ${w2.y}`}
          fill="none"
          stroke={g.color}
          strokeWidth={sw}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      );
    }
  } else if (g.kind === 'circle') {
    const cx = (g.x + g.w / 2) * width;
    const cy = (g.y + g.h / 2) * height;
    const rx = (g.w * width) / 2 * 1.14 + sw;
    const ry = (g.h * height) / 2 * 1.2 + sw;
    const wobble = 0.04 + Math.abs(g.curvature) * 0.06;
    const start = -Math.PI * 0.62;
    const sweep = Math.PI * 2 * 1.07;
    pts = Array.from({length: 97}, (_, i) => {
      const t = i / 96;
      const th = start + sweep * t;
      const r = 1 + wobble * Math.sin(th * 2 + 0.7) * (0.4 + t * 0.6) - t * 0.03;
      return {x: cx + Math.cos(th) * rx * r, y: cy + Math.sin(th) * ry * r};
    });
  } else if (g.kind === 'box') {
    const x0 = g.x * width - sw * 1.5;
    const y0 = g.y * height - sw * 1.5;
    const x1 = (g.x + g.w) * width + sw * 1.5;
    const y1 = (g.y + g.h) * height + sw * 1.5;
    pts = [
      {x: x0, y: y0},
      {x: x1, y: y0},
      {x: x1, y: y1},
      {x: x0, y: y1},
      {x: x0, y: y0 - sw * 0.2},
    ];
  } else {
    // underline: a gentle stroke just under the target box
    const y = (g.y + g.h) * height + sw * 1.2;
    const a = {x: g.x * width, y: y + sw * 0.2};
    const b = {x: (g.x + g.w) * width, y: y - sw * 0.1};
    const c = {x: (a.x + b.x) / 2, y: y + sw * (0.6 + g.curvature)};
    pts = Array.from({length: 25}, (_, i) => quadPoint(a, c, b, i / 24));
  }

  const L = polyLength(pts);
  return (
    <AbsoluteFill style={{opacity: out}}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{position: 'absolute', inset: 0, overflow: 'visible', filter: 'drop-shadow(0px 3px 8px rgba(0,0,0,0.45))'}}
      >
        <path
          d={toPath(pts)}
          fill="none"
          stroke={g.color}
          strokeWidth={sw}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={`${L} ${L}`}
          strokeDashoffset={L * (1 - draw)}
        />
        {head}
      </svg>
    </AbsoluteFill>
  );
};
