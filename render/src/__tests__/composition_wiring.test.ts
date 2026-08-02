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

test("TalkingHead and BrollCutaway render text cards via TextCardOverlay (literal-need fallback visible)", () => {
  // Realism pass: the literal-need text_card fallback was invisible on the face styles that use
  // b-roll (TextStickers renders only text_sticker). These now mount TextCardOverlay so a
  // "text card beats a wrong clip" cue actually appears.
  const offenders: string[] = [];
  for (const file of ["TalkingHead.tsx", "BrollCutaway.tsx"]) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    if (!/<TextCardOverlay\b/.test(content)) offenders.push(file);
  }
  assert.deepEqual(offenders, [], `face style missing TextCardOverlay: ${offenders.join(", ")}`);
  // and the overlay itself routes through cardFit (no unclamped overflow path)
  const overlay = fs.readFileSync(path.join(COMPOSITIONS_DIR, "..", "components", "TextCardOverlay.tsx"), "utf8");
  assert.ok(/cardFit\(/.test(overlay), "TextCardOverlay must size text via cardFit");
});

test("viral-v2: BrollLayer carries KLIPY attribution + pop-in/pop-out ramps", () => {
  const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, "..", "components", "BrollLayer.tsx"), "utf8");
  assert.ok(/KlipyBadge/.test(content), "BrollLayer must define/render KlipyBadge (KLIPY ToS attribution)");
  assert.ok(/Powered by KLIPY/.test(content), "KLIPY badge label required");
  // 2026-07-29 (owner: inserts "shouldn't be rounded... blips in and out with no
  // animation"): inserts are square rectangles that SLIDE in from their own edge and
  // settle. Guard the new contract so it can't regress to the fade-blip.
  assert.ok(/insertMotion/.test(content), "inserts need the shared slide+settle motion helper");
  assert.ok(/spring\(\{\s*frame/.test(content), "insert entrance must be spring-driven");
  assert.ok(/PANEL\s*=\s*\{[^}]*radius:\s*0/.test(content), "panel inserts must be square");
  assert.ok(!/Math\.min\(fadeIn,\s*fadeOut\)/.test(content), "symmetric fade must be gone");
  assert.ok(/flashPunch/.test(content), "full-mode flash inserts (<30f) need a punch entrance");
});

test("viral-v2: TextStickers has stacked-hook frame-0 render, exit anim, anton weight fix", () => {
  const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, "..", "components", "TextStickers.tsx"), "utf8");
  assert.ok(/HOOK_STACKED_MAX_FRAME/.test(content), "hook sticker must render fully formed from frame 0");
  assert.ok(/STICKER_EXIT_FRAMES/.test(content), "stickers need an exit animation");
  assert.ok(/fontKey === "archivo" \|\| fontKey === "anton"/.test(content),
    "anton ships only weight 400 — must not faux-bold");
});

test("viral-v2: AudioMix applies music dropouts", () => {
  const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, "..", "components", "AudioMix.tsx"), "utf8");
  assert.ok(/dropoutAt/.test(content) && /DROPOUT_RAMP/.test(content), "music dropout gate missing");
  assert.ok(/dropoutAt\(f\)/.test(content), "dropoutAt must be multiplied into the volume callback");
});

test("viral-v2: every composition mounts Grade look BELOW captions and Grade transitions ON TOP", () => {
  // The vignette/grain must not darken caption text; transition dips must cover everything.
  const offenders: string[] = [];
  for (const file of COMPOSITION_FILES) {
    const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, file), "utf8");
    // For every Captions mount there must be a look-only Grade mount before it,
    // and every transitions-only mount must come after the last TextStickers.
    const lookIdx = content.indexOf("<Grade look={edl.look} />");
    const capIdx = content.indexOf("<Captions");
    if (capIdx >= 0 && (lookIdx < 0 || lookIdx > capIdx)) offenders.push(`${file}: look-Grade not before Captions`);
    const trIdx = content.lastIndexOf("<Grade transitions=");
    const stIdx = content.lastIndexOf("<TextStickers");
    if (stIdx >= 0 && (trIdx < 0 || trIdx < stIdx)) offenders.push(`${file}: transitions-Grade not after TextStickers`);
    if (/<Grade look=\{edl\.look\} transitions=/.test(content)) offenders.push(`${file}: combined Grade mount remains`);
  }
  assert.deepEqual(offenders, [], offenders.join(" | "));
});

test("build 56: the classic card is still an animated staggered build", () => {
  // Owner contract: the CTA card must never regress to the "basic" single-property
  // fade. v8 moved that build out of EndCard.tsx (now a template dispatcher) into
  // cta/ClassicCard.tsx — the guard follows it. Three pillars: spring physics,
  // per-word stagger, and the ambient (never-static) layer.
  const content = fs.readFileSync(
    path.join(COMPOSITIONS_DIR, "..", "components", "cta", "ClassicCard.tsx"), "utf8");
  assert.ok(/spring\(\{/.test(content), "ClassicCard must use spring() physics");
  assert.ok(/words\.map/.test(content), "ClassicCard must stagger per word");
  assert.ok(/damping:\s*200/.test(content), "word settle must use the smooth damping-200 spring");
  assert.ok(/ambientScale|drift/.test(content), "ClassicCard must keep an ambient motion layer");
});

test("v8: EndCard dispatches to the CTA template registry", () => {
  const content = fs.readFileSync(path.join(COMPOSITIONS_DIR, "..", "components", "EndCard.tsx"), "utf8");
  assert.ok(/resolveCta/.test(content), "EndCard must resolve the template from the registry");
  assert.ok(/style_id/.test(content), "EndCard must read end_card.style_id");
  assert.ok(/<Sequence/.test(content), "EndCard must still mount inside its Sequence window");
});
