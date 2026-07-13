// Scans the actual composition source files for prop-wiring drift on the two
// components formatting fix #5 (collision avoidance) depends on. layout.test.ts
// already exhaustively covers the PURE MATH (resolveStickerNudge/captionBandRect
// resolve intersections, are idempotent, always favor captions) against direct
// function calls — what it can't catch is a composition file that computes the
// right values but forgets to actually pass them down, silently falling back to
// TextStickers'/Captions' own defaults at the JSX call site. A full pixel-rendered
// debug-box probe (render each composition with solid-color boxes standing in for
// real content, then scan the output frames for box overlap) would also catch
// this, but at the cost of test-only render branches inside production
// components across all 7 compositions — for a class of bug a plain source scan
// already catches for a fraction of the complexity. Deferred; revisit only if a
// wiring bug of this shape actually slips through in practice.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const COMPOSITIONS_DIR = path.join(__dirname, "..", "..", "src", "compositions");
const COMPOSITION_FILES = fs.readdirSync(COMPOSITIONS_DIR).filter((f) => f.endsWith(".tsx"));

function findTags(content: string, tagName: string): string[] {
  return content.match(new RegExp(`<${tagName}\\b[\\s\\S]*?/>`, "g")) ?? [];
}

test("every <TextStickers> call site wires captionStyle + captionOptions", () => {
  const offenders: string[] = [];
  for (const file of COMPOSITION_FILES) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    for (const tag of findTags(content, "TextStickers")) {
      if (!/captionStyle=/.test(tag) || !/captionOptions=/.test(tag)) offenders.push(`${file}: ${tag}`);
    }
  }
  assert.deepEqual(offenders, [], `TextStickers call missing captionStyle/captionOptions: ${offenders.join(" | ")}`);
});

test("every <Captions> call site wires style + options", () => {
  const offenders: string[] = [];
  for (const file of COMPOSITION_FILES) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    for (const tag of findTags(content, "Captions")) {
      if (!/style=/.test(tag) || !/options=/.test(tag)) offenders.push(`${file}: ${tag}`);
    }
  }
  assert.deepEqual(offenders, [], `Captions call missing style/options: ${offenders.join(" | ")}`);
});

test("every composition rendering TextStickers also renders Captions in the same file", () => {
  // TextStickers' collision band collapses to "no captions" (hasCaptions=false)
  // whenever the composition doesn't also render real Captions data alongside it
  // — silently disabling the keep-clear band rather than erroring.
  const offenders: string[] = [];
  for (const file of COMPOSITION_FILES) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    const hasStickers = findTags(content, "TextStickers").length > 0;
    const hasCaptions = findTags(content, "Captions").length > 0;
    if (hasStickers && !hasCaptions) offenders.push(file);
  }
  assert.deepEqual(offenders, [], `TextStickers without Captions: ${offenders.join(", ")}`);
});

test("GreenScreen and DuetSplit text_card rendering goes through cardFit (no unclamped overflow path)", () => {
  const offenders: string[] = [];
  for (const file of ["GreenScreen.tsx", "DuetSplit.tsx"]) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    if (!/cardFit\(/.test(content)) offenders.push(file);
  }
  assert.deepEqual(offenders, [], `text_card composition not using cardFit: ${offenders.join(", ")}`);
});
