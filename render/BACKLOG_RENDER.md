# Render fidelity backlog (Ralph loop G worklist)

One item per iteration. Gate: keyless `python -m pytest -q` (backend) AND
`npx tsc -p tsconfig.bridge.json --noEmit` AND `npx tsc -p tsconfig.json --noEmit`
(render), both green.

- [x] G0 no-repro (audit false positive, CONFIRMED by reading Remotion's own
      source): trimBefore/trimAfter are absolute source-frame positions
      (validate-start-from-props.js requires trimAfter > trimBefore, identical
      contract to the deprecated startFrom/endAt). CutVideo.tsx's
      `trimBefore={c.src_in} trimAfter={c.src_out}` is correct as written.
      Pinned via an expanded code comment citing the exact source file (no TS
      test framework exists in render/ yet — adding one just for this single
      fact would be disproportionate; revisit if/when one gets added for other
      reasons).
- [ ] G1 Golden plan-contract fixtures: build_render_plan output validated
      field-by-field against types.ts's expected shape for all 7 compositions.
- [ ] G2 Captions safe-area: bottom-180px collides with TikTok/IG UI chrome —
      move to a configurable safe band.
- [ ] G3 Ducking honors duck_voice:false (AudioMix.tsx ducks off caption
      presence regardless today); duck from word timings when captions are off.
- [ ] G4 lufs_target is currently a dead contract field (never applied anywhere)
      — either implement an approximation or explicitly document+test it as
      intentionally unimplemented (no silently-ignored contract fields).
- [ ] G5 B-roll aspect handling: prefer portrait video_files at Pexels-resolve
      time; verify landscape fallback center-crops sanely.
- [ ] G6 Caption font embedded (staticFile/loadFont) so Lambda renders match
      design instead of relying on a system-ui fallback that may differ.
- [ ] G7 Render concurrency cap in _render_all_clips (no Lambda stampede on a
      many-clip job).
- [ ] G8 Cold-start resilience: one retry on a render submit timeout.
- [ ] G9 Preview render path: preview=true through the bridge → cheap low-res
      proof render; new contract param, doesn't overwrite render_url.
- [ ] G10 FastCuts flash boundary + volumeAt ±1-frame boundary: pin exact
      behavior with tests; fix only if a pin actually fails.

Completion promise (only when EVERY box is checked and both gates are green):
RENDER FIDELITY GREEN
