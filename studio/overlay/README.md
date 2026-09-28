# Yunicorn Studio overlay renderer (Remotion)

One composition, `Overlay` (size, fps and duration come from the props via `calculateMetadata`), draws
everything that sits on top of the A-roll on a transparent canvas: caption pages, text overlays (hook
title, callout, numbered list, lower third, label, CTA), designed cards (title, number, stat, list, steps,
quote, comparison) and pointing graphics (arrow, circle, box, underline). The props schema is
`src/schema.ts` and mirrors `studio/compile/overlays.py` (`OverlayProps`, camelCase on the wire).

Python drives it (`studio.compile.overlays.render_overlays`): it bundles this project once per content
hash into `.cache/bundle-<hash>` and renders ProRes 4444 with alpha:

    remotion render <bundle> Overlay overlays.mov --props=props.json --codec=prores --prores-profile=4444 \
      --pixel-format=yuva444p10le --image-format=png --color-space=bt709 --muted

Setup (once, needs the network): `npm ci && node scripts/fetch-fonts.mjs && npx remotion browser ensure`
(`studio.compile.overlays.ensure_overlay_project()` does the same). Renders are offline afterwards.

Useful commands: `npm run studio` (preview with the demo props), `npm run typecheck`,
`node scripts/validate-props.mts <props.json>` (validate props against the zod schema).

Licensing: Remotion is source-available; company use needs a Remotion company licence (see
https://www.remotion.dev/license). Fonts: SIL OFL 1.1 (`public/fonts/LICENSES.md`).
