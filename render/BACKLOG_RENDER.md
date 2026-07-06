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
- [x] G1 Added 3 golden-fixture tests (test_render_plan_matches_typescript_
      contract_exactly, ..._with_all_optionals_absent, ..._for_every_composition_
      style) asserting build_render_plan's output keys match render/src/types.ts's
      RenderPlan/Clip/CaptionWord/Overlay/BRoll/Layout/ReactSource/ReactWindow/
      MusicTrack/VolumeRange/AudioPlan EXACTLY (not superset/subset) — manual
      inspection found the contract already matched, but the fixtures caught a
      REAL gap: build_render_plan passed a hand-built/incomplete layout dict
      through as-is instead of normalizing it, so a caller supplying a partial
      layout (e.g. only {"style": ...}) produced a plan missing panels/
      panel_boundaries — REQUIRED fields in TS's Layout interface. Fixed:
      layout is now always normalized through the Layout Pydantic model.
- [x] G2 Fixed: bottom offset 180→320px (named CAPTION_SAFE_BOTTOM constant,
      documented against TikTok/IG Reels/YT Shorts published safe-zone
      guidance). Only visible once posted to an actual app (never in Remotion
      Studio/preview), which is why it went unnoticed. BoldWord already
      vertically centers (inset:0 + alignItems:center) — unaffected, already
      clears the bottom chrome by design.
- [x] G3 Fixed a real gap (duck_voice:false itself was ALREADY honored, contra
      the audit — but ducking used the visual `captions` array as its ONLY
      speech-activity signal, so turning captions off silently killed ducking
      too even with duck_voice:true). Added a new EDL field `speech_frames`
      (word-start frames, independent of the captions-enabled toggle, populated
      once from the transcript and never cleared by it) threaded through
      build_render_plan → AudioPlan (types.ts) → AudioMix.tsx. All 8 composition
      call sites updated (dropped the now-unused `captions` prop on AudioMix).
- [x] G4 Documented as deliberately deferred (not silent): real LUFS
      normalization needs an ffmpeg loudnorm two-pass or equivalent, which
      doesn't exist in this render bridge — that's DSP infrastructure work, not
      a bug fix, so out of scope for this loop. Added clear comments at all 3
      sites (Audio Pydantic model, build_render_plan, AudioPlan in types.ts) +
      a pinning test confirming the field flows through the full contract with
      its published-platform-target default (-14 LUFS) and round-trips a
      custom value, so it's ready for that work whenever it lands.
- [x] G5 Fixed: _fetch_pexels already searched with orientation=portrait, but
      that only biases which VIDEOS Pexels returns — the matched video's OWN
      video_files can still include landscape transcodes, and the old code
      just grabbed the first "hd"-quality one regardless of actual w/h. Now
      prefers an actual portrait (height>width) file, hd quality first,
      falling back to any portrait, then any hd, then the first file.
      BrollLayer.tsx's objectFit:cover already center-crops sanely (verified —
      no letterboxing either way; this only improves crop QUALITY, since a
      landscape 16:9 file cropped to fill 9:16 loses ~70% of its width).
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
