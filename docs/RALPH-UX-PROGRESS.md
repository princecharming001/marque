# RALPH-UX-PROGRESS — UX overhaul: 7 product fixes

Progress tracker for the Ralph loop executing `docs/PLAN-UX-FIXES.md`.
**Legend:** `[ ]` todo · `[~]` in progress · `[x]` done (+ one line of evidence) · `[!]` blocked (+ reason).

**Environment note:** running on the dev Mac (not Linux CI): backend verify = `.venv/bin/pytest`
(system python3 is 3.9 and fails collection), Swift CAN be compiled here via `cd ios && ./scripts/dev.sh build`
— stronger than mirror-review. Keyless remains the CI contract for the backend suite.

**Baseline (recorded at orientation):** _pending_

---

## Slice 1 — iOS quick wins

- [ ] UX-C1 Library playback gating: `Clip.renderLocalPath` field + extension (`isServerRendered` = jobId != nil && source != "imported" && remoteURL non-empty; `playbackLocalPath`; `playbackRemoteURL`); gate ClipDetailSheet player, shareURL, ClipGridCell thumbnail. Drafts/imported stay local-first.
- [ ] UX-C2 Render caching: `AppStore.cacheRender(clipId:)` background-downloads the render on `.ready` (hook pollJob / pollClipStatuses / applyTweakResult), sets renderLocalPath, regenerates thumbnailPath poster; invalidate both whenever remoteURL changes; single in-flight guard; skip >200MB; fail-soft to streaming.
- [ ] UX-F1 FeedStore hoist: MarqueApp owns `@State private var feed = FeedStore()` + `.environment(feed)`; HomeView switches to `@Environment(FeedStore.self)`.
- [ ] UX-F2 Feed disk snapshot: Codable FeedSnapshot (scripts/reels/trend/cursors/savedAt) → Documents/marque.feed.v1.json, debounced writes after ingest/refresh; init loads it → instant paint, background revalidate with NO skeletons when cache exists; scenePhase .active + >15min stale → silent revalidate.
- [ ] UX-S1.REVIEW full-slice diff review + suite + journey traces (Library plays render; tab-switch/relaunch instant paint). Grade vs spec 0-100; <85 → fix before proceeding.

## Slice 2 — one-tap submit (backend then iOS)

- [ ] UX-B1a Backend: `ClipJobRequest` += `auto_confirm: bool=False`, `toggles: dict|None`, `creator_id: str="default"`. Extract `_apply_confirm_to_job(job, toggles, custom_instructions)` from confirm_clip_job (confirm endpoint behavior byte-identical — regression tests must stay green). New `_run_auto_pipeline(job_id)`: transcribe → brief exactly as `_run_analysis` does on main (factor shared middle `_analyze_to_brief` so dossier+loudness gather is reused) → toggles default from EDIT_FORMATS[fmt]["toggles"] when client omitted → `_apply_confirm_to_job` → `_run_edit`. Brief failure ⇒ proceed briefless; transcription failure ⇒ fail as today. `create_clip_job`: auto_confirm live → status "processing", spawn pipeline, response INCLUDES the clips array; keyless → immediate mock_ready + clips. 426 guard + analyze-first path untouched.
- [ ] UX-B1b iOS: `.recorded` screen becomes the single context screen — keep format grid + vibe picker, ADD the toggle rows (new `EditFormat.defaultToggles` mirroring EDIT_FORMATS toggles, reseeded on format change) and MOVE the customInstructions TextField from the `.brief` screen. `makeClips()` calls `startAnalyzeJob(autoConfirm: true, toggles:)`; response with clips → new `AppStore.trackSubmittedClips` (factor from confirmClips tail: insert rendering clips, pollJob, streak/celebration) → dismiss to Library. Response without clips (old backend) → existing .analyzing/.brief fallback stays fully working. LiveClipEngine.createAnalyzeJob += autoConfirm/toggles/creator_id.
- [ ] UX-S2.REVIEW review + suite + trace: keyless submit → mock_ready + tracked clips; monkeypatched live pipeline → ready; confirm endpoint regression-identical.

## Slice 3 — APNs push

- [ ] UX-B2a Backend: new `backend/app/push.py` — ES256 JWT signer cached ~40min (kid=APNS_KEY_ID, iss=APNS_TEAM_ID, key=APNS_P8 PEM), httpx AsyncClient(http2=True), sandbox/prod routing per token, apns-topic=APNS_TOPIC; `PUSH_CONFIGURED` gate, keyless no-op. `send_clips_ready(creator_id, clip_id, count)`: alert "Your clip is ready", thread-id/category clips_ready, deeplink `marque://library/clip/{clip_id}`; one send per job (push_sent flag); 410/400 → soft-disable token. `device_tokens` table in migrations.sql (creator_id text, token, environment check sandbox|prod, platform, app_version, timezone, permission, last_seen_at, disabled_at, unique(token,environment)) + in-memory fallback when Supabase unconfigured. New `POST /v1/devices` upsert endpoint. Send hook where `_render_all_clips` lands the job "ready" (creator_id present); tweak re-renders never push. Unit tests: JWT claims with a generated throwaway ES256 key, upsert idempotency, 410 soft-delete via mocked transport, keyless no-op.
- [ ] UX-B2b iOS: ios/project.yml → Marque target entitlements `aps-environment: development` (+ note to run xcodegen). New `Adapters/PushManager.swift`: UIApplicationDelegate via @UIApplicationDelegateAdaptor — register for remote notifications on every launch when authorized, hex token → new `BackendClient.registerDevice(token:environment:)` (DEBUG→sandbox else prod, include timezone/app_version/permission), didReceive → router. `AppRouter.handle(url:)` parses marque://library/clip/{id} → Library tab + pendingOpenClipId; MarqueApp .onOpenURL routes non-OAuth marque:// URLs there; LibraryView presents ClipDetailSheet for pendingOpenClipId. `PushPrimerSheet` shown once at first clips-ready moment (UserDefaults cooldown, max 3); replace the cold requestAuthorization inside notifyClipsReady with the primer path. Local notifications stay as fallback, deduped by job id in userInfo.
- [ ] UX-S3.REVIEW review + suite; push unit tests green; note real-device E2E as deferred manual step.

## Slice 4 — inspiration reels + example honesty

- [ ] UX-A1 Backend classification: new `_classify_edit_format(post) -> (edit_format, style)` heuristic (caption/hashtags/duration_s/transcript/views: short-or-no transcript + short duration + music-ish caption → recap_music/fast_cuts; spoken transcript + visual-noun-dense caption → talking_head_broll/broll_cutaway; existing faceless heuristic → recap_voiceover/faceless; default talking_head) called from `_reel_from_post`; cached reels gain additive `edit_format`/`fmt_source:"heuristic"`/`why_match`. Tier 2: in `_refresh_niche_reels`/`_refresh_watched_creator` after engagement sort, run the dossier adapter (backend/app/dossier.py) on top-K (env `REEL_CLASSIFY_TOP_K`=8) reels lacking `fmt_source=="dossier"` to classify by watching; fail-soft to tier 1; persist via the `_merge_prev_reel_work` carry-forward keys so it's paid once per reel.
- [ ] UX-A2 Rewrite `/v1/reels/examples`: match `edit_format` (dossier>heuristic) then legacy style; rank engagement+recency+transcribed+rehosted-URL bonus; top slots require non-empty video_url; live mode NEVER pads with fabricated cards (fewer real > fake); fabricated fallback cards get `sample:true`; every card gets `selection_reason`. Tests: keyless returns sample:true; seeded recap_music corpus entry selected without padding; ranking prefers rehosted.
- [ ] UX-A3 iOS: extract FailableVideoPlayer from ReelDetailSheet (private → DesignSystem/FailableVideoPlayer.swift, add muted/showsControls params; ReelDetailSheet keeps working). mimicCard: autoplay muted looping player when videoURL non-empty, onFailure → AsyncImage fallback, play only visible cards (onAppear/onDisappear), why line from whyMatch ?? whyTrending, SAMPLE chip when sample. ReelItem/ReelDTO += editFormat/whyMatch/sample (optional-with-default).
- [ ] UX-S4.REVIEW review + suite + trace all 4 formats return format-true playable cards (or honestly fewer).

## Slice 5 — chat on clip preview

- [ ] UX-D1 iOS: prominent "Tell the editor what to change…" input-shaped affordance under the ClipDetailSheet player (ready + server-rendered clips) opening TweakChatSheet at .medium/.large detents, composer focused; keep the menu entry.
- [ ] UX-D2 Preview-first flow: new `LiveClipEngine.tweakClipPreview(jobId:clipId:instruction:)` → POST tweak?preview=1; stash returned `applied` ops as pendingOps; when preview_requested, poll GET /v1/clips/{jobId} (3s) for my clip's preview_status/preview_url; ready → new transient `Clip.previewURL` (optional-with-default; cleared on apply/discard/dismiss/remoteURL change) → detail player prefers previewURL with a PREVIEW badge (player .id includes it); Apply → tweakClipOps(ops: pendingOps) deterministic commit + existing render poll; Discard → clear (server committed nothing). preview_requested==false (keyless/undo) → today's direct flow unchanged. 409 → existing polite copy.
- [ ] UX-S5.REVIEW review + suite + trace: discard leaves EDL identical; apply commits; races safe.

## Slice 6 — reasoned feed

- [ ] UX-G1 Backend: factor `_top_arms(creator_id, niche)` from get_recommendations (Thompson arms w/ human reason; _cold_recommendations fallback). `_feed_sreq` consumes arms for pillar/topic+style (template fallback when exhausted); `_compose_feed_items` stamps `why_picked` on every script (arm reason or "From your '{pillar}' pillar" + trend link); `/v1/suggestions/next-idea` routes through _top_arms. Tests: stubbed arms drive selection; why_picked present in fast + full paths; cold start reasons.
- [ ] UX-G2 iOS: Script.whyPicked (+DTO); pick cards + ScriptReaderView show the why line (micro type, tertiary color).
- [ ] UX-S6.REVIEW review + suite.

## Slice 7 — knowledge base v2 (LAST, eval-gated)

- [ ] UX-E1 Research: if web tools are available, deep-research professional short-form editing (retention-curve editing, first-2s visual grammar, J/L cuts + cut-on-action, transition taxonomy AND restraint, sound design/beat-cutting/SFX/risers, kinetic caption practice, per-format playbooks); else distill from the seed corpus in the spec + existing KB. Operational rules with numbers only — no vibes.
- [ ] UX-E2 Corpus: deepen the 7 existing backend/knowledge files; add transitions.md, sound_design.md, hook_visual.md, format_playbooks.md (one section per EDIT_FORMATS key); MANIFEST → kb-2026.08. Routing in knowledge.py digest(): EDL stage → pacing+transitions+matching format playbook; brief → hooks+retention+hook_visual; review → rubric+sound_design; token budgets hold (assert in tests).
- [ ] UX-E3 HARD GATE: `python -m eval.edl_eval` after — no dimension regresses vs the baseline recorded at orientation. If it regresses, iterate the KB until it doesn't; that is part of this unit.
- [ ] UX-S7.REVIEW review + suite + eval.

## FINAL gauntlet

- [ ] UX-F.1 One clean full run: backend suite keyless + eval + render build:bridge, all green.
- [ ] UX-F.2 End-to-end mock walkthrough: auto_confirm submit (keyless) → mock_ready clips tracked; examples endpoint per format; feed with why_picked; devices upsert; tweak preview path (mocked).
- [ ] UX-F.3 Write docs/RALPH-UX-REPORT.md: what shipped per fix, evidence index, deferred manual steps (real-device APNs E2E with the user's .p8 key; TestFlight push primer flow; on-device playback checks), known gaps.
- [ ] FINAL — print `ALL_UX_FIXES_COMPLETE`.

---

## Run log

- (bootstrap) Plan + progress files created off main f6e1bf9 (post-overhaul, post-deploy). Next: baseline.
