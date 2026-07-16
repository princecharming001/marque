import React from "react";
import { useCurrentFrame } from "remotion";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadArchivo } from "@remotion/google-fonts/ArchivoBlack";
import { loadFont as loadBaloo } from "@remotion/google-fonts/Baloo2";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadAnton } from "@remotion/google-fonts/Anton";
import { CaptionWord, CaptionStyle, CaptionOptions } from "../types";
import { LAYOUT, karaokePop, fitTextBlock, boldWordFontSize, captionCenterY, clampPosY } from "../layout";

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
const montserrat = loadFont("montserrat");
const anton = loadFont("anton");

function loadFont(which: CaptionOptions["font"]): string {
  if (which === "archivo") return loadArchivo("normal", { weights: ["400"] }).fontFamily;
  if (which === "baloo") return loadBaloo("normal", { weights: ["600", "700", "800"] }).fontFamily;
  // A2: Montserrat needs weight 900 for the Hormozi/Submagic bold-caption look.
  if (which === "montserrat") return loadMontserrat("normal", { weights: ["700", "900"] }).fontFamily;
  // Anton ships a single 400 weight — asking for anything heavier would trigger
  // the browser's faux-bold (same reasoning as Archivo Black, see weightFor below).
  if (which === "anton") return loadAnton("normal", { weights: ["400"] }).fontFamily;
  return loadInter("normal", { weights: ["600", "700", "800"] }).fontFamily;
}

export const FONTS: Record<CaptionOptions["font"], string> = {
  inter, archivo, baloo, montserrat, anton,
};

const DEFAULTS: CaptionOptions = {
  position: "bottom", size: "medium", pos_y: null, scale: null,
  accent: null, uppercase: false, font: "inter",
  grouping: "phrase", highlight_words: [],   // P0.7: phrase default (stable 3-word chunks)
  stroke_px: 0, sync_lead_frames: 0, highlight_persist_frames: 0,   // A2 (schema v3)
  bg: "",   // schema v6
};

// schema v6: a rounded background pill hugging the caption text (CapCut "boxed" / TikTok
// solid-bg / Beast word-box look). Applied to an INNER fit-content wrapper so the box wraps
// the words, not the full-frame block. "" (default) = no box, byte-identical to pre-v6 output.
const boxStyle = (bg: string | null | undefined): React.CSSProperties =>
  bg ? { backgroundColor: bg, borderRadius: 14, padding: "6px 20px" } : {};

const wordEnd = (c: CaptionWord): number => c.end_frame ?? c.frame + LAYOUT.DEFAULT_WORD_FRAMES;

// CapCut keyword highlight: a word whose normalized form is in highlight_words
// renders in the accent color (default gold) regardless of active state.
const HIGHLIGHT_DEFAULT = "#FFD60A";
const normWord = (w: string): string => w.toLowerCase().replace(/[^a-z0-9]/g, "");
const isHighlighted = (word: string, opts: CaptionOptions): boolean =>
  (opts.highlight_words ?? []).includes(normWord(word));
const highlightColor = (opts: CaptionOptions): string => opts.accent ?? HIGHLIGHT_DEFAULT;

// Effective font-size multiplier: continuous pinch `scale` wins over the S/M/L word.
const sizeMult = (opts: CaptionOptions): number =>
  opts.scale ?? LAYOUT.SIZE_MULT[opts.size];

// Continuous drag position (fraction of frame height) — clamped via the SHARED
// layout constants (backend/app/layout_constants.py, iOS LayoutConstants.swift
// agree on this exact range; backend/test_layout_parity.py is the tripwire).
const posYFrac = (opts: CaptionOptions): number | null =>
  opts.pos_y == null ? null : clampPosY(opts.pos_y);

// The visible index window for the active word under a grouping mode.
// line = the legacy sliding window; phrase = fixed ~3-word chunks (the OpusClip
// 13.5M-clip sweet spot); word = just the active word.
function groupBounds(
  grouping: CaptionOptions["grouping"], activeIdx: number, count: number,
  lineBack: number, lineAhead: number,
): { start: number; end: number } {
  if (grouping === "word") return { start: activeIdx, end: activeIdx };
  if (grouping === "phrase") {
    const start = Math.floor(activeIdx / LAYOUT.PHRASE_LEN) * LAYOUT.PHRASE_LEN;
    return { start, end: Math.min(count - 1, start + LAYOUT.PHRASE_LEN - 1) };
  }
  // P0.7: `line` mode is now stable fixed chunks (like phrase but wider) instead of a
  // per-frame sliding window that reflowed text on every frame. lineBack/lineAhead retained
  // for signature compatibility but no longer drive a jittery window.
  void lineBack; void lineAhead;
  const start = Math.floor(activeIdx / LAYOUT.LINE_LEN) * LAYOUT.LINE_LEN;
  return { start, end: Math.min(count - 1, start + LAYOUT.LINE_LEN - 1) };
}

// Archivo Black and Anton each ship a single 400 weight — asking for 700/800
// would trigger the browser's faux-bold and distort the letterforms.
const weightFor = (font: CaptionOptions["font"], w: number): number =>
  (font === "archivo" || font === "anton") ? 400 : w;

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
  // A2: sync_lead_frames pre-empts the active word by N frames (doctrine:
  // captions should appear ~100-200ms/3-6f early) — 0 (default) is today's
  // exact behavior.
  const leadFrame = frame + (opts.sync_lead_frames ?? 0);

  // Active word = last caption whose start frame has passed (manual scan; avoids the
  // ES2023 findLastIndex lib requirement in the render bundle).
  let activeIdx = -1;
  for (let i = 0; i < captions.length; i++) {
    if (captions[i].frame <= leadFrame) activeIdx = i;
    else break;
  }
  if (activeIdx < 0) return null;

  // P0.7: hide the block after the last word ends (+HIDE_AFTER_LAST frames) so it
  // doesn't burn on screen through the outro, and during long (>SILENCE_GAP-frame)
  // silences between words so a trailing word doesn't hang there through a pause.
  const last = captions[captions.length - 1];
  if (frame > wordEnd(last) + LAYOUT.CAPTION_HIDE_AFTER_LAST) return null;
  const next = captions[activeIdx + 1];
  if (next && frame > wordEnd(captions[activeIdx]) &&
      frame < next.frame && next.frame - wordEnd(captions[activeIdx]) > LAYOUT.CAPTION_SILENCE_GAP) {
    return null;
  }

  if (style === "bold-word") return <BoldWord word={captions[activeIdx].word} opts={opts} />;
  if (style === "karaoke") return <Karaoke captions={captions} activeIdx={activeIdx} opts={opts} />;
  return <Clean captions={captions} activeIdx={activeIdx} opts={opts} />;
};

// Shared vertical placement (formatting fix #2/#12): anchor (discrete position or
// dragged pos_y), then clamp the block's CENTER so its ESTIMATED height stays
// inside the platform safe band — the single mechanism both fixed-size (BoldWord)
// and grouped (Clean/Karaoke) styles use, replacing three separate ad hoc
// top/bottom/44%-offset branches that had no idea how tall their own content was.
const blockPosition = (opts: CaptionOptions, estBlockHeightPx: number): React.CSSProperties => {
  const centerFrac = captionCenterY(opts.position, posYFrac(opts), estBlockHeightPx);
  return { position: "absolute", left: 0, right: 0, top: `${centerFrac * 100}%`, transform: "translateY(-50%)" };
};

const wrapStyle: React.CSSProperties = {
  display: "flex", flexWrap: "wrap", justifyContent: "center", padding: "0 40px", gap: 8,
};

const casing = (word: string, opts: CaptionOptions): string =>
  opts.uppercase ? word.toUpperCase() : word;

// Usable width matches Clean/Karaoke's own horizontal padding ("0 40px" in
// wrapStyle below) — BoldWord's own "0 60px" usable width is computed inside
// layout.ts's boldWordFontSize itself, so there's no separate constant here.
const GROUP_USABLE_PX = LAYOUT.FRAME_W - 80;
// Rough line-height factor for the block-height estimate fed to blockPosition —
// not pixel-exact (this is a fast pre-filter; the render-formatting Ralph loop
// catches residual drift with real pixels), just enough to keep a 2-line wrap
// from being clamped as if it were 1 line tall.
const LINE_HEIGHT_FACTOR = 1.3;

// A2 (schema v3): thick-stroke ("Hormozi"/Submagic outline) caption look. At
// 8-12px, applying WebkitTextStroke directly on a filled span eats the glyph
// interior (the stroke draws both inside and outside the glyph edge, and at
// that width basically overwrites the fill at typical caption sizes) — so a
// stroke this heavy needs a DUAL SPAN: an absolutely-positioned stroke-only
// clone (transparent fill, thick stroke) sitting exactly BEHIND a normal,
// un-stroked fill span. `strokePx` <= 0 renders a plain span (no wrapper, no
// perf/layout cost) — the default, byte-identical to pre-A2 output.
const Stroked: React.FC<{ text: string; style: React.CSSProperties; strokePx: number }> =
  ({ text, style, strokePx }) => {
  if (!strokePx || strokePx <= 0) return <span style={style}>{text}</span>;
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <span style={{ ...style, position: "absolute", left: 0, top: 0,
                     WebkitTextStroke: `${strokePx}px black`, color: "transparent",
                     textShadow: "none" }}>
        {text}
      </span>
      <span style={style}>{text}</span>
    </span>
  );
};

const Clean: React.FC<{ captions: CaptionWord[]; activeIdx: number; opts: CaptionOptions }> =
  ({ captions, activeIdx, opts }) => {
  const { start, end } = groupBounds(opts.grouping, activeIdx, captions.length, 2, 4);
  const group = captions.slice(start, end + 1);
  // Measured at a representative weight (700, between the 600 default and the 800
  // highlighted weight) — sizing for the heavier case keeps the lighter-weight
  // words comfortably inside the same fitted width.
  const fit = fitTextBlock(group.map((c) => c.word), 50 * sizeMult(opts), GROUP_USABLE_PX,
                           LAYOUT.CAPTION_MAX_LINES, LAYOUT.CAPTION_MIN_SHRINK, opts.font, 700, opts.uppercase);
  const estHeight = fit.fontSize * LINE_HEIGHT_FACTOR * fit.lines;
  return (
    <div style={{ ...blockPosition(opts, estHeight), display: "flex", justifyContent: "center", padding: "0 40px" }}>
     <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 8, ...boxStyle(opts.bg) }}>
      {group.map((c, i) => {
        const isActive = start + i === activeIdx;
        const hi = isHighlighted(c.word, opts);
        return (
          <Stroked key={start + i} strokePx={opts.stroke_px ?? 0} text={casing(c.word, opts)} style={{
            fontFamily: FONTS[opts.font], fontSize: fit.fontSize,
            fontWeight: weightFor(opts.font, hi ? 800 : 600),
            // Keyword highlight wins; else the accent colors the HOT word; default white.
            color: hi ? highlightColor(opts) : (isActive && opts.accent ? opts.accent : "white"),
            opacity: isActive || hi ? 1 : 0.55,
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }} />
        );
      })}
     </div>
    </div>
  );
};

const BoldWord: React.FC<{ word: string; opts: CaptionOptions }> = ({ word, opts }) => {
  const fontSize = boldWordFontSize(word, sizeMult(opts), opts.font);
  const estHeight = fontSize * 1.1;   // single line, tight leading (lineHeight 1.05 below)
  const strokePx = opts.stroke_px ?? 0;
  const baseStyle: React.CSSProperties = {
    fontFamily: FONTS[opts.font], fontSize,
    fontWeight: weightFor(opts.font, 800), lineHeight: 1.05,
    color: opts.accent ?? "white", textAlign: "center", textTransform: "uppercase",
    letterSpacing: opts.font === "archivo" ? 0 : -2,
    textShadow: "0 4px 20px rgba(0,0,0,0.9)",
    // A2: a heavy stroke_px (>0) uses the Stroked dual-span technique below
    // instead of this thin baked-in outline (which is fine at 3px on a single
    // span, but would eat the interior at Hormozi-scale 8-12px).
    ...(strokePx > 0 ? {} : { WebkitTextStroke: "3px rgba(0,0,0,0.55)" }),
    // Belt for a genuinely pathological single "word" the font-shrink floor
    // alone can't fit (see layout.ts fitTextBlock docs) — wraps mid-word
    // rather than overflowing the frame.
    maxWidth: "100%", overflowWrap: "anywhere",
  };
  return (
    <div style={{
      ...blockPosition(opts, estHeight),
      display: "flex", alignItems: "center", justifyContent: "center", padding: "0 60px",
    }}>
      <div style={{ display: "inline-flex", ...boxStyle(opts.bg) }}>
        <Stroked text={word} style={baseStyle} strokePx={strokePx} />
      </div>
    </div>
  );
};

const Karaoke: React.FC<{ captions: CaptionWord[]; activeIdx: number; opts: CaptionOptions }> =
  ({ captions, activeIdx, opts }) => {
  const frame = useCurrentFrame();
  const { start, end } = groupBounds(opts.grouping, activeIdx, captions.length, 3, 3);
  const group = captions.slice(start, end + 1);
  const fill = opts.accent ?? ACCENT;
  const fit = fitTextBlock(group.map((c) => c.word), 54 * sizeMult(opts), GROUP_USABLE_PX,
                           LAYOUT.CAPTION_MAX_LINES, LAYOUT.CAPTION_MIN_SHRINK, opts.font, 700, opts.uppercase);
  const estHeight = fit.fontSize * LINE_HEIGHT_FACTOR * fit.lines;
  return (
    <div style={{ ...blockPosition(opts, estHeight), display: "flex", justifyContent: "center", padding: "0 40px" }}>
     <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 8, ...boxStyle(opts.bg) }}>
      {group.map((c, i) => {
        const idx = start + i;
        const spoken = idx <= activeIdx;
        // Formatting fix #14: a CSS `transition` is a no-op in Remotion's frame-by-frame
        // render (every frame is a fresh paint, nothing to transition FROM) — the old
        // `transform: scale(1.08)` + `transition` snapped invisibly instead of popping.
        // karaokePop computes the ramp per-frame from the active word's own start frame.
        // A2 (schema v3): highlight_persist_frames keeps the PREVIOUS word popped for a
        // few extra frames after the next one has already gone active — a brief overlap
        // that reads as less abrupt than an instant handoff. 0 (default) = today's exact
        // single-active-word behavior.
        const persist = opts.highlight_persist_frames ?? 0;
        const isPopping = idx === activeIdx ||
          (persist > 0 && idx === activeIdx - 1 && frame <= wordEnd(c) + persist);
        const pop = isPopping ? karaokePop(frame, c.frame) : 1;
        return (
          <Stroked key={idx} strokePx={opts.stroke_px ?? 0} text={casing(c.word, opts)} style={{
            fontFamily: FONTS[opts.font], fontSize: fit.fontSize,
            fontWeight: weightFor(opts.font, 700),
            color: spoken ? fill : "white",
            textShadow: "0 2px 8px rgba(0,0,0,0.85)",
            display: "inline-block",
            transform: `scale(${pop})`,
          }} />
        );
      })}
     </div>
    </div>
  );
};
