// Scans the actual render/src SOURCE tree (not the compiled dist-test output) for
// "system-ui" — a font family that resolves to San Francisco locally on a Mac but
// falls back to a generic Linux sans-serif on Lambda (the G6 class of bug: a caption
// style tuned/reviewed locally renders with a DIFFERENT typeface in the delivered
// video, with no error anywhere). Every composition must use the embedded
// @remotion/google-fonts family (see Captions.tsx FONTS) instead.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC_ROOT = path.join(__dirname, "..", "..", "src");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "__tests__") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.(tsx?|jsx?)$/.test(entry.name)) out.push(full);
  }
  return out;
}

// Matches an actual CSS font-family VALUE ("system-ui" in quotes, as fontFamily would
// carry it) — not prose that merely mentions the term (e.g. a comment explaining why
// a past bug used it).
const SYSTEM_UI_VALUE = /["']system-ui["']/;

test("no system-ui font-family value anywhere in render/src", () => {
  const offenders: string[] = [];
  for (const file of walk(SRC_ROOT)) {
    const content = fs.readFileSync(file, "utf8");
    if (SYSTEM_UI_VALUE.test(content)) offenders.push(path.relative(SRC_ROOT, file));
  }
  assert.deepEqual(offenders, [], `system-ui font-family value found in: ${offenders.join(", ")}`);
});
