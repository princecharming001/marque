/** Tiny colour helpers (hex / rgb[a] / a few names) for interpolation and contrast decisions. */

type RGBA = {r: number; g: number; b: number; a: number};

const NAMED: Record<string, string> = {
  white: '#ffffff',
  black: '#000000',
  yellow: '#ffff00',
  red: '#ff0000',
  transparent: '#00000000',
};

export const parseColour = (input: string): RGBA | null => {
  const s = (NAMED[input.trim().toLowerCase()] ?? input).trim();
  const hex = s.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    let h = hex[1];
    if (h.length === 3 || h.length === 4) h = h.split('').map((c) => c + c).join('');
    if (h.length !== 6 && h.length !== 8) return null;
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return {r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) / 255 : 1};
  }
  const rgb = s.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+%?))?\s*\)$/i);
  if (rgb) {
    const a = rgb[4] === undefined ? 1 : rgb[4].endsWith('%') ? parseFloat(rgb[4]) / 100 : parseFloat(rgb[4]);
    return {r: +rgb[1], g: +rgb[2], b: +rgb[3], a};
  }
  return null;
};

const toCss = ({r, g, b, a}: RGBA): string =>
  `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${Math.round(a * 1000) / 1000})`;

/** Mix two colours (t = 0 -> a, 1 -> b); falls back to a hard switch for unparseable colours. */
export const mix = (a: string, b: string, t: number): string => {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const ca = parseColour(a);
  const cb = parseColour(b);
  if (!ca || !cb) return t < 0.5 ? a : b;
  return toCss({
    r: ca.r + (cb.r - ca.r) * t,
    g: ca.g + (cb.g - ca.g) * t,
    b: ca.b + (cb.b - ca.b) * t,
    a: ca.a + (cb.a - ca.a) * t,
  });
};

/** WCAG relative luminance (0..1); null when unparseable. */
export const luminance = (c: string): number | null => {
  const p = parseColour(c);
  if (!p) return null;
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(p.r) + 0.7152 * lin(p.g) + 0.0722 * lin(p.b);
};

/** Near-black or white text, whichever contrasts better with the background. */
export const inkOn = (background: string): string => {
  const l = luminance(background);
  if (l === null) return '#FFFFFF';
  // contrast against white vs against #111
  const cWhite = 1.05 / (l + 0.05);
  const cDark = (l + 0.05) / (0.0056 + 0.05);
  return cDark >= cWhite ? '#111111' : '#FFFFFF';
};

export const withAlpha = (c: string, alpha: number): string => {
  const p = parseColour(c);
  if (!p) return c;
  return toCss({...p, a: p.a * alpha});
};
