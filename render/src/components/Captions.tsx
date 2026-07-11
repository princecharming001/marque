import React from "react";
import { useCurrentFrame } from "remotion";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadArchivo } from "@remotion/google-fonts/ArchivoBlack";
import { loadFont as loadBaloo } from "@remotion/google-fonts/Baloo2";
import { CaptionWord, CaptionStyle, CaptionOptions } from "../types";

interface Props { captions: CaptionWord[]; style?: CaptionStyle; options?: CaptionOptions | null; }

const ACCENT = "#FFD60A";
// G6: "system-ui, -apple-system" resolves to San Francisco when previewing in
// Remotion Studio on a Mac, but Lambda's headless Linux container has no Apple
// fonts and falls back to whatever generic sans-serif ships in that container
// image — so a caption style tuned/reviewed locally could render with a
// DIFFERENT typeface in the actual delivered video, with no error anywhere.
// @remotion/google-fonts embeds the fonts (Inter is visually close to San
// Francisco, so local review stays representative) and blocks the render via
// Remotion's own delayRender/continueRender until each font is ready —
// identical in Studio preview and on Lambda. All three caption fonts load up
// front: conditional loading would make the first render of a rarely-used
// font a timing wildcard.
const inter = loadFont("inter");
const archivo = loadFont("archivo");
const baloo = loadFont("baloo");

function loadFont(which: "inter" | "archivo" | "baloo"): string {
  if (which === "archivo") return loadArchivo("normal", { weights: ["400"] }).fontFamily;
  if (which === "baloo") return loadBaloo("normal", { weights: ["600", "700", "800"] }).fontFamily;
  return loadInter("normal", { weights: ["600", "700", "800"] }).fontFamily;
}

export const FONTS: Record<CaptionOptions["font"], string> = {
  inter, archivo, baloo,
};

// G2: bottom safe-area. At 1080x1920, TikTok/IG Reels/YT Shorts all reserve the
// bottom ~300-350px of the frame for their OWN chrome (username, caption/
// description line, sound title, the like/comment/share icon column's lower
// edge, the tab bar) — a caption anchored at bottom:180 sits UNDER that chrome
// and gets visually collided with or fully obscured on-platform (this is only
// visible once posted to an actual app, never in Remotion Studio/preview,
// which is exactly why it went unnoticed). 320px clears all three platforms'
// published safe-zone guidance with margin to spare.
const CAPTION_SAFE_BOTTOM = 320;
// Top safe-area: platform top chrome (search bar / "Following | For You" pills /
// the status bar) occupies roughly the top 250px at 1080x1920.
const CAPTION_SAFE_TOP = 280;

const DEFAULTS: CaptionOptions = {
  position: "bottom", size: "medium", pos_y: null, scale: null,
  accent: null, uppercase: false, font: "inter",
  grouping: "line",
};

// Effective font-size multiplier: continuous pinch `scale` wins over the S/M/L word.
const sizeMult = (opts: CaptionOptions): number =>
  opts.scale ?? SIZE_MULT[opts.size];

// Continuous drag position (fraction of frame height, clamped into the platform
// safe zones) — wins over the discrete top/middle/bottom anchor when set.
const posYPct = (opts: CaptionOptions): number | null =>
  opts.pos_y == null ? null : Math.min(0.84, Math.max(0.16, opts.pos_y)) * 100;

const PHRASE_LEN = 3;

// The visible index window for the active word under a grouping mode.
// line = the legacy sliding window; phrase = fixed ~3-word chunks (the OpusClip
// 13.5M-clip sweet spot); word = just the active word.
function groupBounds(
  grouping: CaptionOptions["grouping"], activeIdx: number, count: number,
  lineBack: number, lineAhead: number,
): { start: number; end: number } {
  if (grouping === "word") return { start: activeIdx, end: activeIdx };
  if (grouping === "phrase") {
    const start = Math.floor(activeIdx / PHRASE_LEN) * PHRASE_LEN;
    return { start, end: Math.min(count - 1, start + PHRASE_LEN - 1) };
  }
  return { start: Math.max(0, activeIdx - lineBack), end: Math.min(count - 1, activeIdx + lineAhead) };
}

const SIZE_MULT: Record<CaptionOptions["size"], number> = {
  small: 0.78, medium: 1, large: 1.24,
};

// Archivo Black ships a single 400 weight — asking for 700/800 would trigger
// the browser's faux-bold and distort the letterforms.
const weightFor = (font: CaptionOptions["font"], w: number): number =>
  font === "archivo" ? 400 : w;

// Captions carry OUTPUT-frame coords (remapped by the backend after cutting), so they
// render straight against the composition's global useCurrentFrame(). Three looks driven
// by the creator's caption_style, tuned by caption_options (position/size/accent/case/font):
//   clean     — a quiet running line, only the active word brightened
//   bold-word — one giant word at a time, karaoke-punch style (Submagic-like)
//   karaoke   — a line where spoken words fill with accent, upcoming stay white
export const Captions: React.FC<Props> = ({ captions, style = "clean", options }) => {
  const frame = useCurrentFrame();
  if (captions.length === 0) return null;

  const opts: CaptionOptions = { ...DEFAULTS, ...(options ?? {}) };

  // Active word = last caption whose start frame has passed (manual scan; avoids the
  // ES2023 findLastIndex lib requirement in the render bundle).
  let activeIdx = -1;
  for (let i = 0; i < captions.length; i++) {
    if (captions[i].frame <= frame) activeIdx = i;
    else break;
  }
  if (activeIdx < 0) return null;

  if (style === "bold-word") return <BoldWord word={captions[activeIdx].word} opts={opts} />;
  if (style === "karaoke") return <Karaoke captions={captions} activeIdx={activeIdx} opts={opts} />;
  return <Clean captions={captions} activeIdx={activeIdx} opts={opts} />;
};

// Line-layout anchor per position. Middle biases slightly above true center so a
// two-line wrap doesn't collide with a centered watermark/face. A continuous
// dragged pos_y (TikTok model) wins over the discrete word.
const lineWrap = (opts: CaptionOptions): React.CSSProperties => {
  const dragged = posYPct(opts);
  return {
    position: "absolute", left: 0, right: 0,
    ...(dragged != null ? { top: `${dragged}%`, transform: "translateY(-50%)" }
      : opts.position === "top" ? { top: CAPTION_SAFE_TOP }
      : opts.position === "middle" ? { top: "44%" }
      : { bottom: CAPTION_SAFE_BOTTOM }),
    display: "flex", flexWrap: "wrap", justifyContent: "center",
    padding: "0 40px", gap: 8,
  };
};

const casing = (word: string, opts: CaptionOptions): string =>
  opts.uppercase ? word.toUpperCase() : word;

const Clean: React.FC<{ captions: CaptionWord[]; activeIdx: number; opts: CaptionOptions }> =
  ({ captions, activeIdx, opts }) => {
  const { start, end } = groupBounds(opts.grouping, activeIdx, captions.length, 2, 4);
  return (
    <div style={lineWrap(opts)}>
      {captions.slice(start, end + 1).map((c, i) => {
        const isActive = start + i === activeIdx;
        return (
          <span key={start + i} style={{
            fontFamily: FONTS[opts.font], fontSize: 50 * sizeMult(opts),
            fontWeight: weightFor(opts.font, 600),
            // The accent (when chosen) colors the HOT word; default stays white.
            color: isActive && opts.accent ? opts.accent : "white",
            opacity: isActive ? 1 : 0.55,
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }}>{casing(c.word, opts)}</span>
        );
      })}
    </div>
  );
};

const BoldWord: React.FC<{ word: string; opts: CaptionOptions }> = ({ word, opts }) => (
  <div style={{
    position: "absolute", left: 0, right: 0,
    ...(posYPct(opts) != null ? { top: `${posYPct(opts)}%`, transform: "translateY(-50%)" }
      : opts.position === "top" ? { top: CAPTION_SAFE_TOP + 80 }
      : opts.position === "bottom" ? { bottom: CAPTION_SAFE_BOTTOM + 80 }
      : { top: 0, bottom: 0 }),
    display: "flex",
    alignItems: posYPct(opts) == null && opts.position === "middle" ? "center" : "flex-start",
    justifyContent: "center", padding: "0 60px",
  }}>
    <span style={{
      fontFamily: FONTS[opts.font], fontSize: 128 * sizeMult(opts),
      fontWeight: weightFor(opts.font, 800), lineHeight: 1.05,
      color: opts.accent ?? "white", textAlign: "center", textTransform: "uppercase",
      letterSpacing: opts.font === "archivo" ? 0 : -2,
      textShadow: "0 4px 20px rgba(0,0,0,0.9)",
      WebkitTextStroke: "3px rgba(0,0,0,0.55)",
    }}>{word}</span>
  </div>
);

const Karaoke: React.FC<{ captions: CaptionWord[]; activeIdx: number; opts: CaptionOptions }> =
  ({ captions, activeIdx, opts }) => {
  const { start, end } = groupBounds(opts.grouping, activeIdx, captions.length, 3, 3);
  const fill = opts.accent ?? ACCENT;
  return (
    <div style={lineWrap(opts)}>
      {captions.slice(start, end + 1).map((c, i) => {
        const idx = start + i;
        const spoken = idx <= activeIdx;
        return (
          <span key={idx} style={{
            fontFamily: FONTS[opts.font], fontSize: 54 * sizeMult(opts),
            fontWeight: weightFor(opts.font, 700),
            color: spoken ? fill : "white",
            textShadow: "0 2px 8px rgba(0,0,0,0.85)",
            transform: idx === activeIdx ? "scale(1.08)" : "scale(1)",
            transition: "transform 0.05s, color 0.05s",
          }}>{casing(c.word, opts)}</span>
        );
      })}
    </div>
  );
};
