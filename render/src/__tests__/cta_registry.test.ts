import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { CTA_STYLES, CTA_LAYOUTS, layoutFor, DEFAULT_OVERLAY_STYLE,
         DEFAULT_TAIL_STYLE } from "../components/cta/catalog";

const CTA_DIR = join(__dirname, "..", "..", "src", "components", "cta");
const catalog = JSON.parse(readFileSync(join(CTA_DIR, "cta_styles.json"), "utf8"));
const catalogIds: string[] = catalog.styles.map((s: any) => s.id);
const src = (f: string) => readFileSync(join(CTA_DIR, f), "utf8");

// The catalog JSON is what the backend serves and the iOS picker renders. A style in
// one but not the other would ship a template a creator can pick and nothing can draw.
test("registry source declares exactly the catalog ids", () => {
  // registry.tsx is JSX (not importable here), so assert on its source: every catalog
  // id must appear as a registry key, and the registry must not invent extra ones.
  const reg = readFileSync(join(CTA_DIR, "registry.tsx"), "utf8");
  const body = reg.slice(reg.indexOf("CTA_REGISTRY"), reg.indexOf("resolveCta"));
  const declared = [...body.matchAll(/^\s{2}([a-z_]+):\s*\{/gm)].map((m) => m[1]);
  assert.deepEqual(declared.sort(), [...catalogIds].sort(),
    "every cta_styles.json id needs a CTA_REGISTRY entry and vice-versa");
});

test("catalog has 20 styles split 6 tail / 14 overlay", () => {
  assert.equal(catalogIds.length, 20);
  const tail = catalog.styles.filter((s: any) => s.layout_class === "tail_card");
  const overlay = catalog.styles.filter((s: any) => s.layout_class === "overlay");
  assert.equal(tail.length, 6);
  assert.equal(overlay.length, 14);
});

test("catalog layout lookup matches each style's layout_class", () => {
  for (const s of catalog.styles) {
    assert.equal(layoutFor(s.id), s.layout_class, `${s.id}: layout lookup must match`);
    assert.equal(CTA_LAYOUTS[s.id], s.layout_class);
  }
  assert.equal(CTA_STYLES.length, catalogIds.length);
});

// Version skew: a backend newer than this bundle can send an id we've never heard of.
// It must degrade, never crash a paid render.
test("unknown ids degrade by mount instead of throwing", () => {
  assert.equal(layoutFor("does_not_exist"), "tail_card", "unknown id falls back to a tail card");
  assert.equal(layoutFor(undefined), "tail_card");
  const reg = readFileSync(join(CTA_DIR, "registry.tsx"), "utf8");
  assert.ok(/mount === "overlay" \? CTA_REGISTRY\[DEFAULT_OVERLAY_STYLE\]|mount === "overlay" \? CTA_REGISTRY\.pill/.test(reg),
    "resolveCta must fall back to the pill template for unknown OVERLAY ids");
  assert.ok(new RegExp(`CTA_REGISTRY\\[DEFAULT_TAIL_STYLE\\]|CTA_REGISTRY\\.classic`).test(reg),
    "resolveCta must fall back to classic otherwise");
  assert.equal(DEFAULT_OVERLAY_STYLE, "pill");
  assert.equal(DEFAULT_TAIL_STYLE, "classic");
});

test("classic is the default and is a tail card", () => {
  assert.equal(CTA_LAYOUTS.classic, "tail_card");
  assert.equal(CTA_LAYOUTS.pill, "overlay");
});

// Overlay templates draw over LIVE video whose captions are still running. They have to
// use the shared band-clearing geometry rather than hardcoding a y that lands on text.
test("overlay template files use the shared band-safe geometry", () => {
  for (const f of ["OverlayBarsPills.tsx", "OverlayText.tsx"]) {
    const s = src(f);
    assert.ok(/clampOverlayY|OVERLAY_Y/.test(s),
      `${f} must position via clampOverlayY/OVERLAY_Y so it clears the caption band`);
  }
});

// Guardrail from the 2026 motion research: entrances land in 0.3-0.5s. A slower entrance
// eats the CTA's dwell time; these files must not introduce long ramps.
test("templates keep entrance windows short", () => {
  for (const f of ["TailCards.tsx", "OverlayBarsPills.tsx", "OverlayText.tsx", "ClassicCard.tsx"]) {
    const s = src(f);
    // Only the INPUT range of an entrance interpolate counts — `interpolate(frame,
    // [0, N], ...)`. The third argument is the OUTPUT range (px, opacity, width) and
    // has nothing to do with timing, so it must not be matched.
    const entrances = [...s.matchAll(/interpolate\(\s*frame\s*,\s*\[\s*0\s*,\s*(\d+)/g)];
    for (const m of entrances) {
      const n = Number(m[1]);
      assert.ok(n <= 30, `${f}: entrance ramp [0, ${n}] exceeds the 30-frame guardrail`);
    }
    // An entrance is either a frame interpolate or a spring() — both are timed by
    // frame; what matters is that the file animates at all.
    assert.ok(entrances.length > 0 || /spring\(\s*\{/.test(s),
      `${f}: expected a frame-driven or spring entrance`);
  }
});

// Determinism: Lambda renders each frame independently, so any wall-clock or RNG in a
// template produces a strobing, non-reproducible video.
test("templates are deterministic", () => {
  for (const f of ["TailCards.tsx", "OverlayBarsPills.tsx", "OverlayText.tsx", "ClassicCard.tsx"]) {
    const s = src(f);
    assert.ok(!/Math\.random|Date\.now|new Date\(/.test(s),
      `${f} must not use Math.random/Date — frames must be reproducible`);
  }
});

// The CTA is the LAST thing in the reel: build_render_plan mounts overlay CTAs flush
// to the end (start_frame = total_frames - frames). A template that animates itself
// off over its final frames therefore spends the end of the video — and the frame the
// platform freezes on when the reel loops — sliding a half-faded plate across the
// speaker. Caught in a real 34s Lambda render, not by the still gate.
test("every overlay exit is suppressed when the CTA runs to the end of the video", () => {
  for (const f of ["OverlayBarsPills.tsx", "OverlayText.tsx"]) {
    const s = src(f);
    // The shared helper must short-circuit on the flag...
    assert.match(s, /if \(runsToEnd\) return 1;/,
      `${f}: exitRamp must hold (return 1) when nothing follows the CTA`);
    // ...and every exit that is keyed off FRAME (rather than derived from an
    // already-guarded ramp, e.g. `interpolate(out, ...)`) must be guarded too.
    const rawExits = [...s.matchAll(/const \w*[Oo]ut\w*\s*=\s*interpolate\(\s*frame\s*,/g)];
    for (const m of rawExits) {
      const line = s.slice(m.index, s.indexOf("\n", m.index!));
      assert.fail(`${f}: unguarded frame-keyed exit — ${line.trim()}`);
    }
    // Each template must actually forward the flag it was given.
    const uses = [...s.matchAll(/exitRamp\(frame, total(?!, runsToEnd)/g)];
    assert.equal(uses.length, 0, `${f}: an exitRamp call does not pass runsToEnd`);
  }
});

test("EndCard derives runsToEnd from the real composition duration", () => {
  const s = src("../EndCard.tsx");
  assert.match(s, /useVideoConfig\(\)/,
    "EndCard must read the composition duration, not assume it");
  assert.match(s, /runsToEnd\s*=\s*endCard\.start_frame \+ endCard\.frames >= durationInFrames/,
    "runsToEnd must compare the CTA window against the video's end");
  assert.match(s, /runsToEnd=\{runsToEnd\}/, "EndCard must pass runsToEnd to the template");
});
