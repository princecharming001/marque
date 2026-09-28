#!/usr/bin/env node
/**
 * Vendor the overlay fonts locally (run once after `npm ci`; the Python side runs it on demand).
 *
 * Source of truth is @remotion/google-fonts (its getInfo() lists the exact Google Fonts woff2 URLs per
 * family / style / weight / subset, pinned to the package version). We download those files into
 * public/fonts/ and write src/fonts-manifest.json, and the composition loads them with the FontFace API
 * from staticFile(). Why not call loadFont() at render time: that fetches from fonts.gstatic.com inside
 * every headless tab, so a slow or offline network would either stall the render (delayRender timeout)
 * or silently fall back to a system font and change the typography. Local files make every render
 * deterministic and offline. All families are SIL Open Font License 1.1.
 *
 * Usage: node scripts/fetch-fonts.mjs [--force]
 */
import {createHash} from 'node:crypto';
import {mkdir, readFile, writeFile, access} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = path.join(ROOT, 'public', 'fonts');
const MANIFEST = path.join(ROOT, 'src', 'fonts-manifest.json');
const FORCE = process.argv.includes('--force');

// family module -> weights to vendor. Heavy weights for captions/titles, 500-600 for card body text.
const FAMILIES = {
  Montserrat: ['500', '600', '700', '800', '900'],
  Inter: ['500', '600', '700', '800', '900'],
  Anton: ['400'],
  TikTokSans: ['500', '600', '700', '800', '900'],
  Archivo: ['500', '600', '700', '800', '900'],
  NotoColorEmoji: ['400'],
};
const TEXT_SUBSETS = ['latin', 'latin-ext'];

const exists = async (p) => {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
};

const download = async (url, dest) => {
  if (!FORCE && (await exists(dest))) return;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = Buffer.from(await res.arrayBuffer());
      if (buf.length < 1000) throw new Error(`suspiciously small font (${buf.length} bytes)`);
      await writeFile(dest, buf);
      return;
    } catch (err) {
      if (attempt === 4) throw new Error(`download failed for ${url}: ${err.message}`);
      await new Promise((r) => setTimeout(r, 500 * attempt));
    }
  }
};

const main = async () => {
  await mkdir(OUT_DIR, {recursive: true});
  await mkdir(path.dirname(MANIFEST), {recursive: true});
  const entries = [];
  const byUrl = new Map();
  for (const [mod, weights] of Object.entries(FAMILIES)) {
    const info = (await import(`@remotion/google-fonts/${mod}`)).getInfo();
    const normal = info.fonts.normal;
    if (!normal) throw new Error(`${mod}: no normal style`);
    for (const weight of weights) {
      const subsets = normal[weight];
      if (!subsets) throw new Error(`${mod}: weight ${weight} not available`);
      const wanted = mod === 'NotoColorEmoji' ? Object.keys(subsets) : TEXT_SUBSETS.filter((s) => subsets[s]);
      for (const subset of wanted) {
        const url = subsets[subset];
        let file = byUrl.get(url);
        if (!file) {
          const h = createHash('sha1').update(url).digest('hex').slice(0, 10);
          const safeSubset = subset.replace(/[^a-z0-9-]/gi, '');
          file = `fonts/${mod}-${safeSubset}-${h}.woff2`;
          byUrl.set(url, file);
          await download(url, path.join(ROOT, 'public', file));
        }
        entries.push({
          family: info.fontFamily,
          module: mod,
          weight,
          style: 'normal',
          subset,
          file,
          unicodeRange: info.unicodeRanges?.[subset] ?? null,
        });
      }
    }
  }
  const manifest = {
    source: '@remotion/google-fonts',
    version: JSON.parse(await readFile(path.join(ROOT, 'node_modules', '@remotion', 'google-fonts', 'package.json'), 'utf8')).version,
    licence: 'SIL Open Font License 1.1 (all families)',
    fonts: entries,
  };
  await writeFile(MANIFEST, JSON.stringify(manifest, null, 2) + '\n');
  console.log(`fonts: ${entries.length} faces, ${byUrl.size} files -> ${path.relative(ROOT, OUT_DIR)}`);
};

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
