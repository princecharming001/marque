/**
 * Font loading from the vendored Google Fonts files (see scripts/fetch-fonts.mjs for why they are local).
 *
 * Every face in fonts-manifest.json is registered with the FontFace API before the first frame is
 * captured; rendering is held with delayRender() until all of them are loaded, so neither measureText()
 * nor a screenshot can ever see a fallback font.
 */
import {useEffect, useState} from 'react';
import {cancelRender, continueRender, delayRender, staticFile} from 'remotion';
import manifest from './fonts-manifest.json';

type ManifestFont = {
  family: string;
  weight: string;
  style: string;
  file: string;
  unicodeRange: string | null;
};

const FONTS = (manifest as {fonts: ManifestFont[]}).fonts;

/** Families with vendored files; anything else falls back to Montserrat. */
export const KNOWN_FAMILIES: ReadonlySet<string> = new Set(FONTS.map((f) => f.family));

const ALIASES: Record<string, string> = {
  tiktoksans: 'TikTok Sans',
  'tiktok sans': 'TikTok Sans',
  montserrat: 'Montserrat',
  inter: 'Inter',
  anton: 'Anton',
  archivo: 'Archivo',
};

export const resolveFamily = (name: string): string => {
  if (KNOWN_FAMILIES.has(name)) return name;
  const alias = ALIASES[name.trim().toLowerCase()];
  return alias && KNOWN_FAMILIES.has(alias) ? alias : 'Montserrat';
};

/**
 * CSS font stack: the chosen face, the house face, then Noto Color Emoji (never the system's Apple
 * emoji glyphs), then a generic fallback. The emoji face also covers digits/#/* as keycap bases, so it
 * must come after the text faces.
 */
export const fontStack = (name: string): string => {
  const fam = resolveFamily(name);
  const parts = [fam, 'Montserrat', 'Noto Color Emoji'];
  return [...new Set(parts)].map((p) => `"${p}"`).join(', ') + ', sans-serif';
};

/** Anton only ships one weight; ask for what exists so the browser never synthesizes bold. */
export const effectiveWeight = (name: string, weight: number): number => {
  const fam = resolveFamily(name);
  const weights = FONTS.filter((f) => f.family === fam).map((f) => Number(f.weight));
  if (weights.length === 0) return weight;
  return weights.reduce((best, w) => (Math.abs(w - weight) < Math.abs(best - weight) ? w : best), weights[0]);
};

let loading: Promise<void> | null = null;
let loaded = false;

export const loadAllFonts = (): Promise<void> => {
  if (loading) return loading;
  loading = Promise.all(
    FONTS.map(async (f) => {
      const face = new FontFace(f.family, `url(${staticFile(f.file)}) format('woff2')`, {
        weight: f.weight,
        style: f.style,
        display: 'block',
        ...(f.unicodeRange ? {unicodeRange: f.unicodeRange} : {}),
      });
      await face.load();
      document.fonts.add(face);
    }),
  ).then(() => {
    loaded = true;
  });
  return loading;
};

/** True once every vendored face is loaded; holds the frame capture until then. */
export const useFontsReady = (): boolean => {
  const [ready, setReady] = useState(loaded);
  const [handle] = useState(() => (loaded ? null : delayRender('Loading overlay fonts', {timeoutInMilliseconds: 60000})));
  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    loadAllFonts()
      .then(() => {
        if (handle !== null) continueRender(handle);
        if (!cancelled) setReady(true);
      })
      .catch((err) => cancelRender(err));
    return () => {
      cancelled = true;
    };
  }, [ready, handle]);
  return ready;
};
