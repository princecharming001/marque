// LV-28 (editor audit 2026-09-24): the music bed is an <Audio loop> whose volume is a
// frame-keyed CURVE (duck to speech_frames, start gate after the first word, punchline
// dropouts, end fade — all in composition/output frames). Remotion's default
// loopVolumeCurveBehavior="repeat" hands that callback the LOOP-LOCAL frame, so once the
// track looped the start gate re-silenced the bed, ducking followed the wrong words and
// the end fade never fired. Same source-scan style as composition_wiring.test.ts: a
// behavioral render test would need a Lambda/Chromium render per assertion, while this
// class of bug is one missing prop at the call site.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = path.join(__dirname, "..", "..", "src");

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== "__tests__") out.push(...tsxFiles(p));
    else if (entry.isFile() && entry.name.endsWith(".tsx")) out.push(p);
  }
  return out;
}

function audioTags(content: string): string[] {
  return content.match(/<Audio\b[\s\S]*?\/>/g) ?? [];
}

const isLooped = (tag: string) => /\sloop(?=[\s/>]|=\{true\})/.test(tag);
const hasVolumeCurve = (tag: string) => /\svolume=\{\s*\(/.test(tag);   // volume={(f) => ...}
const extendsCurve = (tag: string) => /\sloopVolumeCurveBehavior="extend"/.test(tag);

test("AudioMix: the looped music bed hands its volume curve the composition frame", () => {
  const content = fs.readFileSync(path.join(SRC, "components", "AudioMix.tsx"), "utf8");
  const music = audioTags(content).filter((t) => /src=\{music\.url\}/.test(t));
  assert.equal(music.length, 1, "expected exactly one music <Audio>");
  assert.ok(isLooped(music[0]), "the music bed must loop under long takes");
  assert.ok(hasVolumeCurve(music[0]), "the music volume must be the frame-keyed curve");
  assert.ok(extendsCurve(music[0]),
    'music <Audio loop> needs loopVolumeCurveBehavior="extend" (default "repeat" = loop-local frames)');
});

test('every looped <Audio> with a volume curve uses loopVolumeCurveBehavior="extend"', () => {
  const offenders: string[] = [];
  let curves = 0;
  for (const file of tsxFiles(SRC)) {
    for (const tag of audioTags(fs.readFileSync(file, "utf8"))) {
      if (!(isLooped(tag) && hasVolumeCurve(tag))) continue;
      curves += 1;
      if (!extendsCurve(tag)) offenders.push(`${path.relative(SRC, file)}: ${tag.replace(/\s+/g, " ")}`);
    }
  }
  assert.ok(curves >= 1, "scan found no looped volume curve at all — the guard would be vacuous");
  assert.deepEqual(offenders, [], `looped <Audio> curve without "extend": ${offenders.join(" | ")}`);
});

test("pinned remotion: 'repeat' = loop-local frame, 'extend' = + loop offset", () => {
  // The fix relies on this library behavior (remotion 4.0.484, lockfile-pinned to match
  // the deployed Lambda function). If an upgrade changes it, re-verify AudioMix.
  const root = path.dirname(require.resolve("remotion/package.json"));
  const js = fs.readFileSync(path.join(root, "dist", "cjs", "audio", "use-audio-frame.js"), "utf8");
  assert.ok(/behavior === 'repeat' \|\| loop === null\)\s*\{\s*return frame \+ startsAt;/.test(js),
    "'repeat' should return the loop-local frame");
  assert.ok(/return frame \+ startsAt \+ loop\.durationInFrames \* loop\.iteration;/.test(js),
    "'extend' should add the elapsed loops");
  const dts = fs.readFileSync(path.join(root, "dist", "cjs", "audio", "use-audio-frame.d.ts"), "utf8");
  assert.ok(/LoopVolumeCurveBehavior = 'repeat' \| 'extend'/.test(dts));
});
