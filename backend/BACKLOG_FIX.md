# Backend editor correctness backlog (Ralph loop F worklist)

One item per iteration. Gate: `cd /Users/home/Marque/backend && python -m pytest -q`
fully green, keyless (no env keys).

- [x] F0 Committed (966e34b). Deploy is a standing checkpoint — NOT deployed;
      surfaced to user, awaiting explicit "deploy it" authorization.
- [x] F1 Fixed: trim_start/trim_end now walk PLAY order (segment_order) instead of
      array order. One pre-existing test encoded the bug as its expectation
      ("index 0 removed" despite segment_order=[2,0,1] meaning segment 2 plays
      first) — corrected with 3 new regression tests (partial spillover, trim_end,
      identity-order no-op preserved).
- [x] F2 no-repro: map_point's half-open interval math is internally consistent
      (segments/drops/captions all derive from the same ms_to_frame). Verified with
      a direct repro at every boundary frame around a drop; pinned as a permanent
      regression test (test_caption_frame_exactly_at_drop_boundary_maps_correctly).
- [ ] F3 Overlays/captions spanning reordered+cut segments emit MULTIPLE mapped
      pieces instead of only the longest (map_range, edl.py) — no silent loss.
- [ ] F4 Overlapping mute/volume_ranges merge on apply; kept-frames math stays
      correct with overlapping drops.
- [ ] F5 Out-of-bounds ops REJECTED with a reason (start>=source-end, end<=start,
      negative frames) — never silently clamped into "cut everything".
- [ ] F6 Unresolved b-roll fail-soft: strip unresolved entries from the render plan
      (never a black/blank layer); warnings[] records what was skipped.
- [ ] F7 Tweak/re-render race: per-clip render generation counter so a stale Lambda
      result can never overwrite a newer tweak's render_url.
- [ ] F8 Undo restores segment_order/audio/captions fully (test the triple); depth
      10->25; response exposes undo_available.
- [ ] F9 Swept/expired jobs return structured 410 job_expired (not bare 404).
- [ ] F10 Transcript hygiene: drop malformed word entries, dedupe identical caption
      frames, guard zero/duplicate timestamps (_normalize_words).
- [ ] F11 _merge_drops unions overlapping LLM+filler windows instead of silently
      skipping the LLM's cut.
- [ ] F12 Tweaked EDL wins over stale edit_prefs on re-render (manual
      set_captions_enabled/style must not be reverted by _apply_edit_prefs).
- [ ] F13 Silent-except sweep across the edit path: every swallow becomes a
      structured warnings[] entry or log line.
- [ ] F14 Duet clip_from/react_windows remapped under segment_order (backend side).
- [ ] F15 Durable edit sessions: persist {job_id: words, edl, clip meta} to Supabase
      (clone upsert_arm_stat pattern) with lazy restore on job-miss; keyless
      fallback stays in-memory.
- [ ] F16 Fuzz gate: seeded randomized op sequences over randomized EDLs assert
      invariants (segments monotonic, order a valid permutation, kept-frames>0 or
      rejected, plan always builds, captions within output bounds).

Completion promise (only when EVERY box is checked and the full pytest suite is
green): EDITOR CORRECTNESS GREEN
