// Single source of truth for layout constants + pure layout math shared by every
// composition. React-free and Remotion-free on purpose: this file is imported both
// by the render components AND by a plain `node --test` suite with no DOM, no
// Remotion runtime, no rendering — so overflow/collision/safe-area math is
// unit-testable without ever producing a pixel. Mirrored (constants only, via
// backend/test_layout_parity.py) into backend/app/layout_constants.py and
// ios/Marque/Features/Editor/LayoutConstants.swift — the mirrors must stay equal.
import layoutJson from "./layout.json";

export const LAYOUT = layoutJson;

export type CaptionPosition = "top" | "middle" | "bottom";
export type FontKey = "inter" | "archivo" | "baloo";

export interface Rect {
  /** Fraction of FRAME_H (0-1). */
  top: number;
  bottom: number;
}

// ---------------------------------------------------------------------------
// Text width estimation — a fast, deterministic PRE-FILTER, not a pixel-exact
// measurement (no DOM/canvas is available outside a real render). Calibrated once
// against a rendered sample frame per font+weight, then frozen. This estimate is
// belt-and-suspenders: the render components ALSO apply hard CSS overflow/line-clamp
// stops, and the render-formatting Ralph loop (LOOP F) catches any residual drift
// with real rendered pixels via debug-box probes.
// ---------------------------------------------------------------------------

const CHAR_W: Record<string, number> = {
  "inter:600": 0.50, "inter:700": 0.53, "inter:800": 0.56,
  "archivo:400": 0.62,
  "baloo:600": 0.52, "baloo:700": 0.54, "baloo:800": 0.57,
};

export function estTextWidth(
  text: string, font: FontKey, weight: number, fontSize: number, uppercase = false,
): number {
  const perChar = CHAR_W[`${font}:${weight}`] ?? 0.55;
  const bump = uppercase ? 1.06 : 1.0; // uppercase glyphs run slightly wider on average
  return text.length * perChar * fontSize * bump;
}

const WORD_GAP_PX = 8; // matches Captions.tsx Clean/Karaoke `gap: 8`

/**
 * Shrink-to-fit: the largest font size (down to `baseFs * minShrink`) at which
 * `words` (rendered as a wrapped, space/gap-separated run) fit within `maxLines`
 * at `usableWidthPx`. `lines` is an ESTIMATE (total estimated width / usable
 * width), not real DOM wrapping — good enough to bound overflow, not to lay out
 * pixel-exact line breaks.
 */
export function fitTextBlock(
  words: string[], baseFs: number, usableWidthPx: number, maxLines: number,
  minShrink: number, font: FontKey = "inter", weight = 600, uppercase = false,
): { fontSize: number; lines: number } {
  const totalWidthAt = (fs: number): number =>
    words.reduce((sum, w) => sum + estTextWidth(w, font, weight, fs, uppercase), 0)
    + WORD_GAP_PX * Math.max(0, words.length - 1);

  const floor = baseFs * minShrink;
  let fs = baseFs;
  while (fs > floor) {
    const lines = Math.max(1, Math.ceil(totalWidthAt(fs) / usableWidthPx));
    if (lines <= maxLines) return { fontSize: fs, lines };
    fs -= baseFs * 0.02; // 2%-of-base steps
  }
  return { fontSize: floor, lines: Math.max(1, Math.ceil(totalWidthAt(floor) / usableWidthPx)) };
}

/** BoldWord is always exactly one word, one line, uppercase, weight 800. */
export function boldWordFontSize(word: string, sizeMult: number, font: FontKey = "inter"): number {
  const base = 128 * sizeMult;
  const usable = LAYOUT.FRAME_W - 120; // padding "0 60px" both sides
  return fitTextBlock([word], base, usable, 1, LAYOUT.CAPTION_MIN_SHRINK, font, 800, true).fontSize;
}

// ---------------------------------------------------------------------------
// Caption vertical placement — anchor (discrete position or dragged pos_y), then
// clamp the CENTER so the estimated block stays inside the platform safe band.
// This "anchor, then clamp-by-estimated-block-height" rule is the parity contract
// the iOS editor preview mirrors (ProEditorView captionSimOverlay).
// ---------------------------------------------------------------------------

export function clampPosY(posY: number): number {
  return Math.min(LAYOUT.CAPTION_POS_Y_MAX, Math.max(LAYOUT.CAPTION_POS_Y_MIN, posY));
}

export function captionCenterY(
  position: CaptionPosition, draggedPosY: number | null, estBlockHeightPx: number,
): number {
  const anchor = draggedPosY != null ? clampPosY(draggedPosY) : LAYOUT.CAPTION_ANCHOR_Y[position];
  const halfH = estBlockHeightPx / 2 / LAYOUT.FRAME_H;
  const minCenter = LAYOUT.SAFE_TOP_PX / LAYOUT.FRAME_H + halfH;
  const maxCenter = 1 - LAYOUT.SAFE_BOTTOM_PX / LAYOUT.FRAME_H - halfH;
  if (minCenter > maxCenter) return anchor; // block taller than the safe band itself — nothing more to clamp
  return Math.min(maxCenter, Math.max(minCenter, anchor));
}

export function captionBandRect(
  position: CaptionPosition, draggedPosY: number | null, estBlockHeightPx: number,
): Rect {
  const center = captionCenterY(position, draggedPosY, estBlockHeightPx);
  const halfH = estBlockHeightPx / 2 / LAYOUT.FRAME_H;
  return { top: center - halfH, bottom: center + halfH };
}

// ---------------------------------------------------------------------------
// Sticker safe-area clamp + caption-band collision avoidance. Priority order
// (highest to lowest): captions > text_card / credit chip > sticker — captions
// never move; a colliding sticker nudges to the nearest free side of the caption
// band, or shrinks if there's no room on either side.
// ---------------------------------------------------------------------------

export function clampSticker(posX: number, posY: number): { x: number; y: number } {
  return {
    x: Math.min(LAYOUT.STICKER_POS_X_MAX, Math.max(LAYOUT.STICKER_POS_X_MIN, posX)),
    y: Math.min(LAYOUT.STICKER_POS_Y_MAX, Math.max(LAYOUT.STICKER_POS_Y_MIN, posY)),
  };
}

function rectsIntersect(a: Rect, b: Rect): boolean {
  return a.top < b.bottom && b.top < a.bottom;
}

/**
 * Resolve a sticker's vertical position against the caption band. Returns the
 * (possibly nudged/shrunk) position; `shrink` multiplies the sticker's own scale.
 * Idempotent: re-resolving an already-clear or already-resolved sticker is a no-op.
 */
export function resolveStickerNudge(
  stickerY: number, stickerHalfHeightFrac: number, band: Rect,
): { y: number; shrink: number } {
  const stickerRect: Rect = { top: stickerY - stickerHalfHeightFrac, bottom: stickerY + stickerHalfHeightFrac };
  if (!rectsIntersect(stickerRect, band)) return { y: stickerY, shrink: 1 };

  const h2 = stickerHalfHeightFrac;
  const spaceAbove = band.top - LAYOUT.STICKER_POS_Y_MIN;
  const spaceBelow = LAYOUT.STICKER_POS_Y_MAX - band.bottom;

  if (spaceAbove >= h2 * 2 && spaceAbove >= spaceBelow) {
    return { y: Math.max(LAYOUT.STICKER_POS_Y_MIN + h2, band.top - h2 - 0.01), shrink: 1 };
  }
  if (spaceBelow >= h2 * 2) {
    return { y: Math.min(LAYOUT.STICKER_POS_Y_MAX - h2, band.bottom + h2 + 0.01), shrink: 1 };
  }
  // No room on either side — shrink toward the sticker's own original position.
  const shrink = 0.85;
  const newH2 = h2 * shrink;
  return {
    y: Math.min(LAYOUT.STICKER_POS_Y_MAX - newH2, Math.max(LAYOUT.STICKER_POS_Y_MIN + newH2, stickerY)),
    shrink,
  };
}

// ---------------------------------------------------------------------------
// Card / quote text fit (GreenScreen reference card, DuetSplit pull-quote) — same
// shrink-to-fit mechanics as captions, word-wrapped rather than single-word.
// ---------------------------------------------------------------------------

export function cardFit(
  text: string, baseFs: number, usableWidthPx: number, maxLines: number, minFs: number,
  font: FontKey = "inter", weight = 700,
): { fontSize: number; lines: number } {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const minShrink = minFs / baseFs;
  return fitTextBlock(words.length ? words : [""], baseFs, usableWidthPx, maxLines, minShrink, font, weight, false);
}

// ---------------------------------------------------------------------------
// Karaoke active-word emphasis pop. CSS `transition` is a no-op in Remotion's
// frame-by-frame render (every frame is a fresh paint, nothing to transition
// FROM) — the pop must be computed per-frame from the word's start frame.
// Ramps 1.0 -> 1.08 over 4 frames, then holds at 1.08 (the caller only invokes
// this for the currently-active word; a newly-active word restarts the ramp).
// ---------------------------------------------------------------------------

export function karaokePop(frame: number, wordStartFrame: number): number {
  const t = Math.min(1, Math.max(0, (frame - wordStartFrame) / 4));
  return 1 + 0.08 * t;
}

// ---------------------------------------------------------------------------
// Speed-change pitch correction. Remotion's Lambda audio pipeline applies
// `playbackRate` via FFmpeg `atempo` (pitch-preserving time-stretch) — this
// helper exists for `OffthreadVideo`'s `toneFrequency` prop, which is NOT needed
// for pitch preservation (atempo already does that) but is kept as an available,
// tested primitive in case a future encode path needs an explicit correction.
// ---------------------------------------------------------------------------

export function pitchCorrection(speed: number): number {
  return 1 / (speed || 1);
}
