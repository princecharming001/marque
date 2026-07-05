# Editor hardening + manual editor backlog (Ralph loop worklist)

One item per iteration. Gate for every item: `cd /Users/home/Marque/backend &&
python -m pytest -q` fully green (keyless — no env keys), plus the item's own
verification. iOS items additionally gate on a green
`xcodebuild -project /Users/home/Marque/ios/Marque.xcodeproj -scheme Marque
-configuration Debug -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build`.

- [x] E1 PipelineError + ERROR_CODES + env knobs (SOURCE_PROBE_TIMEOUT_S etc.)
- [x] E2 _validate_source_url HEAD/Range probe, fails source_unreachable in seconds
- [x] E3 _run_render_bridge subprocess timeout + stderr propagated in-band (_error)
- [x] E4 _poll_remotion_render fail-fast: backoff, 240s budget, stall detection, raises
- [x] E5 _poll_transcription loud-fail (error/timeout/empty-words raise structured)
- [x] E6 _render_all_clips extraction + job ready only if ≥1 clip ready
- [x] E7 _rerender_clip stranding fix (broll inside try; no-prev-url → failed not fake-ready)
- [x] E8 _sweep_stuck_renders watchdog on GET poll
- [x] E9 POST /v1/clips/{job_id}/retry (EDL-stage fast path or full pipeline)
- [x] E10 GET include_words=1 + error_detail; LLM-down EDL stage falls back to safe default
- [x] E11 edl.py: segment_order permutation field + Audio models (MusicTrack, VolumeRange)
      with validators; EDL round-trips (EDL(**data) survives with new fields) — 4 tests
- [x] E12 apply_edl_ops: reorder_segments / set_music / set_segment_volume / mute_range
      (TWEAK_OP_TYPES 13→17); trim ops remap segment_order when they pop a segment — 7 tests
- [x] E13 build_render_plan: per-segment kept intervals iterated in segment_order
      (captions/overlays travel with segments); map_range longest-merged-span; identity
      order produces byte-identical plans (regression-asserted); audio block in plan
      output with volume_ranges remapped as split pieces — 6 tests, all 184 legacy green
- [x] E14 tweak direct-ops tests live in test_editor_hardening.py (done in A) — extended
      with reorder + audio op coverage through the endpoint (incl. undo across new ops)
- [x] E15 render bridge: types.ts AudioPlan/VolumeRange, AudioMix.tsx (music +
      caption-activity ducking), CutVideo volumeRanges prop, all 7 compositions wired,
      tsc clean (bridge + full project), Remotion site redeploy kicked
- [x] E16 iOS: Clip.lastError + friendlyRenderError map + failed-clip error card +
      "Try again" (retry endpoint) in ClipDetailSheet; AppStore.retryClipJob
- [x] E17 iOS: EditorView.swift manual editor (segment cut/reorder/mute rows, trim
      steppers, captions, overlays delete, music+duck; op-diffing; direct-ops apply;
      render-wait via pollJob + friendlyRenderError) — BUILD SUCCEEDED
- [ ] E18 Maestro: editor step in flow-extras.yaml (open editor on a mock clip, toggle
      a segment, apply, assert saved)

Completion promise (only when EVERY box is checked and the full pytest suite +
iOS build gates are green): EDITOR PIPELINE HARDENED
