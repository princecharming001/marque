/**
 * Text measurement and layout. Everything is measured with the real loaded faces
 * (@remotion/layout-utils measureText, validateFontIsLoaded) so line breaks and fitted sizes are exact,
 * then clamped into the platform safe zone. Only call these after useFontsReady() is true.
 */
import {measureText} from '@remotion/layout-utils';
import {effectiveWeight, fontStack} from './fonts';
import type {SafeZone} from './schema';

export type FontSpec = {
  font: string;
  weight: number;
  letterSpacingEm?: number;
};

export const measure = (text: string, spec: FontSpec, sizePx: number): number => {
  if (text.length === 0) return 0;
  return measureText({
    text,
    fontFamily: fontStack(spec.font),
    fontWeight: effectiveWeight(spec.font, spec.weight),
    fontSize: sizePx,
    letterSpacing: spec.letterSpacingEm ? `${spec.letterSpacingEm}em` : undefined,
    validateFontIsLoaded: true,
  }).width;
};

export type Fitted = {
  /** Word indices per line. */
  lines: number[][];
  sizePx: number;
  /** Measured width per line at sizePx (without stroke). */
  widths: number[];
};

const lineText = (words: string[], idx: number[]): string => idx.map((i) => words[i]).join(' ');

/**
 * Best split of `words` into exactly `k` lines minimizing the widest line (balanced lines read as one
 * shape; greedy filling leaves orphans). Brute force DP over split points; pages are short.
 */
const balancedSplit = (words: string[], k: number, width: (a: number, b: number) => number): number[][] => {
  const n = words.length;
  if (k <= 1 || n <= 1) return [words.map((_, i) => i)];
  const kk = Math.min(k, n);
  // best[j][i] = minimal max width splitting words[0..i) into j lines
  const best: number[][] = Array.from({length: kk + 1}, () => new Array(n + 1).fill(Infinity));
  const prev: number[][] = Array.from({length: kk + 1}, () => new Array(n + 1).fill(-1));
  best[0][0] = 0;
  for (let j = 1; j <= kk; j++) {
    for (let i = 1; i <= n; i++) {
      for (let s = j - 1; s < i; s++) {
        if (best[j - 1][s] === Infinity) continue;
        const cand = Math.max(best[j - 1][s], width(s, i));
        if (cand < best[j][i]) {
          best[j][i] = cand;
          prev[j][i] = s;
        }
      }
    }
  }
  const lines: number[][] = [];
  let i = n;
  for (let j = kk; j >= 1; j--) {
    const s = prev[j][i];
    lines.unshift(Array.from({length: i - s}, (_, t) => s + t));
    i = s;
  }
  return lines;
};

/**
 * Fit words into at most `maxLines` lines no wider than `maxWidth` (plus `extraPx`, e.g. the outer
 * stroke on both sides). Preference order: one line at full size, one line shrunk down to `minScale`,
 * more lines at full size (balanced), more lines shrunk, then up to `overflowLines` lines rather than
 * shrinking under `minScale` (captions: the legibility floor), and finally shrink whatever is needed so
 * text never leaves the safe zone. Mirrored in Python by studio.compile.captions.caption_fit.
 */
export const fitWords = ({
  words,
  spec,
  sizePx,
  maxWidth,
  maxLines,
  minScale,
  extraPx = 0,
  preferLines = 1,
  wrapFirst = false,
  overflowLines = 0,
  wrapBeforeShrink = 0,
  breaks = null,
}: {
  words: string[];
  spec: FontSpec;
  sizePx: number;
  maxWidth: number;
  maxLines: number;
  minScale: number;
  extraPx?: number;
  preferLines?: number;
  /** Cards/quotes: use more lines at full size before shrinking (captions shrink before wrapping). */
  wrapFirst?: boolean;
  /** Lines allowed beyond `maxLines` when even `minScale` would not fit (0 = none). */
  overflowLines?: number;
  /** Captions: while fewer than `maxLines` lines are used, shrink no further than this before wrapping
   * (0 = shrink down to minScale first). Mirrors WRAP_BEFORE_SHRINK in studio.compile.captions. */
  wrapBeforeShrink?: number;
  /** Planned line breaks (word index starting each line after the first): the lines are fixed and only the
   * size is fitted (Python chose them with its syntax-aware splitter, and placed that block). */
  breaks?: number[] | null;
}): Fitted => {
  const n = words.length;
  const avail = Math.max(1, maxWidth - extraPx);
  if (breaks && breaks.length > 0 && breaks.every((b, i) => b > 0 && b < n && (i === 0 || b > breaks[i - 1]))) {
    const bounds = [0, ...breaks, n];
    const lines = bounds.slice(0, -1).map((a, i) => Array.from({length: bounds[i + 1] - a}, (_, t) => a + t));
    const widths = lines.map((l) => measure(lineText(words, l), spec, sizePx));
    const scale = Math.min(1, avail / Math.max(...widths));
    return {lines, sizePx: sizePx * scale, widths: widths.map((w) => w * scale)};
  }
  const width = (a: number, b: number) => measure(words.slice(a, b).join(' '), spec, sizePx);
  const layout = (k: number) => {
    const lines = balancedSplit(words, k, width);
    const widths = lines.map((l) => measure(lineText(words, l), spec, sizePx));
    return {lines, widths, widest: Math.max(...widths)};
  };
  const scaled = (l: {lines: number[][]; widths: number[]}, scale: number): Fitted => ({
    lines: l.lines,
    sizePx: sizePx * scale,
    widths: l.widths.map((w) => w * scale),
  });

  const maxK = Math.max(1, Math.min(Math.max(maxLines, overflowLines), n));
  const startK = Math.max(1, Math.min(preferLines, maxK));
  // Fewest lines (from the preferred count) whose fit needs no more shrinking than `stepScale`:
  // captions accept down to minScale before wrapping (one line reads in one glance); cards and
  // quotes wrap first and only shrink when even the last allowed line count is too wide.
  const stepScale = wrapFirst ? 0.97 : minScale;
  const last = layout(maxK);
  for (let k = startK; k <= maxK; k++) {
    const l = k === maxK ? last : layout(k);
    if (l.widest <= avail) return scaled(l, 1);
    const need = avail / l.widest;
    const step = !wrapFirst && k < maxLines ? Math.max(stepScale, wrapBeforeShrink) : stepScale;
    if (need >= step) return scaled(l, need);
  }
  // every allowed line count is too wide: shrink the most-lines layout as much as needed
  return scaled(last, Math.min(1, avail / last.widest));
};

/** Keep a w x h box centred near (cx, cy) fully inside the safe zone (centre of the zone if too big). */
export const clampCenter = (
  cx: number,
  cy: number,
  w: number,
  h: number,
  safe: SafeZone,
  width: number,
  height: number,
): {cx: number; cy: number} => {
  const clampAxis = (c: number, size: number, lo: number, hi: number) => {
    const min = lo + size / 2;
    const max = hi - size / 2;
    if (min > max) return (lo + hi) / 2;
    return Math.min(max, Math.max(min, c));
  };
  return {
    cx: clampAxis(cx, w, safe.left, width - safe.right),
    cy: clampAxis(cy, h, safe.top, height - safe.bottom),
  };
};

/** Parse a displayed number like "$1,200.5k" into prefix / value / decimals / suffix for counters. */
export const parseCounter = (
  raw: string,
): {prefix: string; value: number; decimals: number; suffix: string; grouping: boolean} | null => {
  const m = raw.trim().match(/^([^\d\-+.]*)([-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+)(.*)$/);
  if (!m) return null;
  const numStr = m[2];
  const grouping = numStr.includes(',');
  const clean = numStr.replace(/,/g, '');
  const value = Number(clean);
  if (!Number.isFinite(value)) return null;
  const dot = clean.indexOf('.');
  const decimals = dot >= 0 ? clean.length - dot - 1 : 0;
  return {prefix: m[1], value, decimals, suffix: m[3], grouping};
};

export const formatCounter = (
  c: {prefix: string; decimals: number; suffix: string; grouping: boolean},
  value: number,
): string => {
  const fixed = value.toFixed(c.decimals);
  const body = c.grouping
    ? Number(fixed).toLocaleString('en-US', {minimumFractionDigits: c.decimals, maximumFractionDigits: c.decimals})
    : fixed;
  return `${c.prefix}${body}${c.suffix}`;
};
