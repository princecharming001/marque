#!/usr/bin/env node
/**
 * Validate an overlay props JSON file against the real zod schema (src/schema.ts), exactly as the
 * composition will see it. Runs on Node 24's built-in TypeScript type stripping (no build step).
 *
 * Usage: node scripts/validate-props.mts <props.json>
 * Exit 0 and prints {"ok": true} when valid; exit 1 with {"ok": false, "issues": [...]} otherwise.
 */
import {readFileSync} from 'node:fs';
import {overlayPropsSchema} from '../src/schema.ts';

const file = process.argv[2];
if (!file) {
  console.error('usage: validate-props.mts <props.json>');
  process.exit(2);
}
const data = JSON.parse(readFileSync(file, 'utf8'));
const res = overlayPropsSchema.safeParse(data);
if (res.success) {
  const extra: string[] = [];
  const d = res.data;
  for (const group of ['captions', 'texts', 'cards', 'graphics'] as const) {
    for (const it of d[group] as {id: string; start: number; end: number}[]) {
      if (it.end <= it.start) extra.push(`${group} ${it.id}: end <= start`);
      if (it.end > d.durationInFrames) extra.push(`${group} ${it.id}: ends after durationInFrames`);
    }
  }
  for (const g of d.graphics) {
    if (g.kind === 'arrow' && (!g.from || !g.to)) extra.push(`graphics ${g.id}: arrow needs from/to`);
  }
  if (extra.length) {
    console.log(JSON.stringify({ok: false, issues: extra}));
    process.exit(1);
  }
  console.log(JSON.stringify({ok: true}));
} else {
  console.log(JSON.stringify({ok: false, issues: res.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`)}));
  process.exit(1);
}
