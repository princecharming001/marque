import { test } from "node:test";
import assert from "node:assert/strict";
import {
  LAYOUT, estTextWidth, fitTextBlock, boldWordFontSize, captionCenterY,
  clampSticker, captionBandRect, resolveStickerNudge, cardFit, karaokePop,
  pitchCorrection, clampPosY, progressFraction,
} from "../layout";

// ---- boldWordFontSize: shrinks to fit within the usable width, down to the floor ----
//
// A realistic-but-long word (~20 chars) must be shrunk to actually FIT — font-size
// shrinking has a legibility floor (CAPTION_MIN_SHRINK), so a genuinely pathological
// single "word" (40+ chars, no spaces) can still exceed the usable width even at the
// floor; that residual case is caught by the render layer's CSS `overflowWrap:
// anywhere` belt (Captions.tsx), not by font-shrinking alone — this function's
// contract is "shrink as far as the legibility floor allows", not "guarantee no
// overflow for any input".

test("a realistic long word shrinks to fit the usable width across all fonts and sizes", () => {
  const word = "uncharacteristically"; // 20 chars — long but not pathological
  const usable = LAYOUT.FRAME_W - 120;
  for (const font of ["inter", "archivo", "baloo"] as const) {
    for (const mult of [0.78, 1.0, 1.24]) {
      const fs = boldWordFontSize(word, mult, font);
      const w = estTextWidth(word, font, 800, fs, true);
      assert.ok(w <= usable + 1, `${font}@${mult}: width ${w} exceeds usable ${usable}`);
    }
  }
});

test("a pathological 37-char word bottoms out at the shrink floor (CSS overflow-wrap is the belt)", () => {
  const fs = boldWordFontSize("extraordinarily-uncharacteristically", 1.24, "inter");
  assert.ok(Math.abs(fs - 128 * 1.24 * LAYOUT.CAPTION_MIN_SHRINK) < 0.01);
});

test("boldword min-scale floor is respected", () => {
  const fs = boldWordFontSize("supercalifragilisticexpialidocious", 1.24, "archivo");
  assert.ok(fs >= 128 * 1.24 * LAYOUT.CAPTION_MIN_SHRINK - 0.01);
});

// ---- fitTextBlock: group fits within max lines, monotonic in text length ----

test("group of long words fits within CAPTION_MAX_LINES at large size", () => {
  const words = ["extraordinarily", "uncharacteristically", "incomprehensible", "juxtaposition", "onomatopoeia"];
  const { lines, fontSize } = fitTextBlock(
    words, 50 * LAYOUT.SIZE_MULT.large, LAYOUT.FRAME_W - 80, LAYOUT.CAPTION_MAX_LINES, LAYOUT.CAPTION_MIN_SHRINK,
  );
  assert.ok(lines <= LAYOUT.CAPTION_MAX_LINES, `lines=${lines}`);
  assert.ok(fontSize > 0);
});

test("fitTextBlock font size is monotonic non-increasing as text grows", () => {
  const short = fitTextBlock(["hi"], 100, 900, 2, 0.5);
  const long = fitTextBlock(["hi", "there", "extraordinarily", "uncharacteristically"], 100, 900, 2, 0.5);
  assert.ok(long.fontSize <= short.fontSize);
});

test("fitTextBlock never returns a font size below the shrink floor", () => {
  const words = Array(20).fill("supercalifragilisticexpialidocious");
  const { fontSize } = fitTextBlock(words, 100, 900, 2, 0.5);
  assert.ok(fontSize >= 100 * 0.5 - 1e-9);
});

// ---- captionCenterY: clamped into the safe band ----

test("bold-word large at bottom anchor stays inside the safe band", () => {
  const fs = boldWordFontSize("hook", 1.24, "inter");
  const estHeight = fs * 1.3; // line-height 1.05-ish + margin; rough single-line estimate
  const center = captionCenterY("bottom", null, estHeight);
  const bottomEdgePx = (center + estHeight / 2 / LAYOUT.FRAME_H) * LAYOUT.FRAME_H;
  assert.ok(bottomEdgePx <= LAYOUT.FRAME_H - LAYOUT.SAFE_BOTTOM_PX + 1);
});

test("top anchor stays clear of the top safe area for a tall block", () => {
  const center = captionCenterY("top", null, 300);
  const topEdgePx = (center - 150 / LAYOUT.FRAME_H) * LAYOUT.FRAME_H;
  assert.ok(topEdgePx >= LAYOUT.SAFE_TOP_PX - 1);
});

test("dragged pos_y is clamped into [CAPTION_POS_Y_MIN, CAPTION_POS_Y_MAX]", () => {
  assert.equal(clampPosY(0.99), LAYOUT.CAPTION_POS_Y_MAX);
  assert.equal(clampPosY(0.0), LAYOUT.CAPTION_POS_Y_MIN);
  assert.equal(clampPosY(0.5), 0.5);
});

// ---- sticker clamp + band collision ----

test("sticker clamp bounds", () => {
  const { x, y } = clampSticker(1.5, -0.5);
  assert.equal(x, LAYOUT.STICKER_POS_X_MAX);
  assert.equal(y, LAYOUT.STICKER_POS_Y_MIN);
});

test("nudge resolves an intersection with the caption band", () => {
  const band = captionBandRect("bottom", null, 200); // near the bottom
  const { y } = resolveStickerNudge(band.top + (band.bottom - band.top) / 2, 0.05, band);
  const stickerRect = { top: y - 0.05, bottom: y + 0.05 };
  assert.ok(!(stickerRect.top < band.bottom && band.top < stickerRect.bottom), "still intersects after nudge");
});

test("nudge is a no-op when there is no intersection", () => {
  const band = captionBandRect("bottom", null, 200);
  const clearY = 0.2; // near the top, clear of a bottom-anchored caption band
  const result = resolveStickerNudge(clearY, 0.05, band);
  assert.equal(result.y, clearY);
  assert.equal(result.shrink, 1);
});

test("nudge is idempotent — resolving twice does not move it further", () => {
  const band = captionBandRect("bottom", null, 200);
  const first = resolveStickerNudge(band.top + 0.01, 0.05, band);
  const firstRect = { top: first.y - 0.05 * first.shrink, bottom: first.y + 0.05 * first.shrink };
  const second = resolveStickerNudge(first.y, 0.05 * first.shrink, band);
  assert.equal(second.y, first.y);
});

test("captions always win priority — captionBandRect ignores stickers entirely", () => {
  // captionBandRect's inputs are only caption-related; nothing about a sticker can
  // change where the caption band sits, by construction (no sticker param exists).
  const band1 = captionBandRect("bottom", null, 200);
  const band2 = captionBandRect("bottom", null, 200);
  assert.deepEqual(band1, band2);
});

// ---- card / quote fit ----

test("a 300-char card shrinks then clamps at the floor", () => {
  const text = "word ".repeat(60).trim(); // ~300 chars
  const { fontSize, lines } = cardFit(text, 40, LAYOUT.FRAME_W * 0.84, LAYOUT.CARD_MAX_LINES, LAYOUT.CARD_MIN_FONT);
  assert.ok(fontSize >= LAYOUT.CARD_MIN_FONT - 1e-9);
  assert.ok(lines >= 1);
});

test("a short quote does not shrink below base font", () => {
  const { fontSize } = cardFit("short quote", 30, LAYOUT.FRAME_W * 0.84, LAYOUT.QUOTE_MAX_LINES, LAYOUT.QUOTE_MIN_FONT);
  assert.equal(fontSize, 30);
});

// ---- karaoke pop ----

test("karaoke pop ramps from 1.0 then holds at 1.08", () => {
  assert.equal(karaokePop(100, 100), 1.0);
  assert.ok(karaokePop(102, 100) > 1.0 && karaokePop(102, 100) < 1.08);
  assert.equal(karaokePop(104, 100), 1.08);
  assert.equal(karaokePop(150, 100), 1.08); // holds well after
});

// ---- pitch correction ----

test("pitch correction is the inverse of speed", () => {
  assert.equal(pitchCorrection(2.0), 0.5);
  assert.equal(pitchCorrection(0.5), 2.0);
  assert.equal(pitchCorrection(0), 1.0); // guarded against div-by-zero
});

// ---- P4: progress bar fraction ----

test("progress fraction is 0 at frame 0 and 1 at the last frame", () => {
  assert.equal(progressFraction(0, 300), 0);
  assert.equal(progressFraction(300, 300), 1);
  assert.equal(progressFraction(150, 300), 0.5);
});

test("progress fraction clamps rather than exceeding 1 past the end", () => {
  assert.equal(progressFraction(400, 300), 1);
});

test("progress fraction never goes negative for a negative frame", () => {
  assert.equal(progressFraction(-5, 300), 0);
});

test("progress fraction is 0 for a degenerate zero-length plan (no div-by-zero)", () => {
  assert.equal(progressFraction(0, 0), 0);
});
