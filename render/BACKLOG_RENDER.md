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
- [x] G6 Fixed: replaced "system-ui, -apple-system" (resolves to San Francisco
      in Studio-on-Mac preview, but Lambda's headless Linux container has no
      Apple fonts and falls back to a different generic sans, silently) with
      @remotion/google-fonts/Inter's loadFont() — embeds the font and blocks
      the render via Remotion's own delayRender/continueRender until ready,
      identical in Studio and on Lambda. New dependency, justified: this is
      the official first-party Remotion package built for exactly this
      problem; pinned to 4.0.484, matching remotion/@remotion/lambda's
      resolved version (and the deployed Lambda function verified earlier
      this session).
- [x] G7 The audit's exact framing was a false positive (clips WITHIN one job
      already render sequentially — a plain for-loop, no gather/create_task) —
      but the underlying concern was real from a different angle: separate
      JOBS each run in their own asyncio task with NO cap at all, so a burst
      of users could still stack up unbounded concurrent Lambda invocations.
      Added a process-wide asyncio.Semaphore(RENDER_CONCURRENCY_CAP=3) around
      the submit+poll critical section in both _render_all_clips and
      _rerender_clip. Verified with a test proving peak concurrency hits the
      job count (6) unprotected vs. the cap (2) protected.
- [x] G8 Fixed: _submit_remotion_render retries ONCE, with double the timeout
      budget, specifically when the bridge reports a timeout (the cold-start
      signature) — not on any other bridge error (e.g. a bad composition id,
      which would just fail identically twice). Verified both the recovery
      path and that non-timeout errors still fail fast with no wasted retry.
- [ ] G9 Preview render path: preview=true through the bridge → cheap low-res
      proof render; new contract param, doesn't overwrite render_url.
- [ ] G10 FastCuts flash boundary + volumeAt ±1-frame boundary: pin exact
      behavior with tests; fix only if a pin actually fails.

Completion promise (only when EVERY box is checked and both gates are green):
RENDER FIDELITY GREEN
