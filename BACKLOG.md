# Marque buildout backlog

Work top-to-bottom. Check off `[x]` when an item is DONE (built, `scripts/dev.sh build` green,
Maestro green if UI-affecting, committed). Verifiable items first; key-gated integrations after.

## A. Verifiable app-side (build + Maestro can confirm)
- [x] A1. Brand Profile screen — editable Brand Graph. Done: BrandProfileView (niche/what-you-do/audience/known-for, voice sliders, non-negotiable chips, pillars), person icon on Today, saves to store; flow-extras.yaml green.
- [x] A2. Insights screen — InsightsView (clips made / scheduled / streak stats, top clip, format mix), opened via a chart icon on Coach; flow-extras covers it.
- [x] A3. Calendar scheduling — schedule a ready clip onto a day (ids calendar.addClip / schedule.pickClip), marks it .scheduled, surfaces "Next up" on Today; flow-extras covers the full schedule path.
- [x] A4. Studio pillar regenerate — verified: tapping a pillar node (studio.pillar.<name>) triggers generation via the same path as the AHA; flow-extras taps a pillar and confirms Studio stays intact.
- [x] A5. Hook Lab + steer — Hook Lab cards are now selectable (store.setHook), ids added (script.hookButton / hooklab.pickHook / script.steer); flow-extras opens Hook Lab, picks a hook, scrolls to and taps a steer chip.
- [x] A6. Repurpose-in — PhotosPicker "Upload existing video" on Record (id record.upload) routes an imported video to the same makeClips pipeline; flow-full asserts the entry exists.
- [x] A7. Streak + celebration — makeClips increments the consistency streak and shows a calm CelebrationView sheet (id celebration.dismiss); Brand Profile shows the session count; flow-full handles the celebration.
- [x] A8. States — empty states (EmptyStateView) on Library/Studio/Insights, loading spinners on generation, calm errors; added NetworkMonitor + app-wide OfflineBanner (never red) via safeAreaInset on RootView.

## B. Key-ready integrations (compile-clean, key-gated, mock fallback — NO new SPM deps; use URLSession / StoreKit)
- [x] B1. AyrsharePublisher — URLSession POST to Ayrshare /api/post behind Publishing, key-gated (AppConfig.ayrshareKey), mock fallback on error. Compile-clean; functionally untestable without a key + public media URL.
- [x] B2. SupabaseStore — RemotePersistence (PostgREST URLSession push/pull to an app_state row), best-effort mirror on save when supabase url+anonKey set; local UserDefaults stays source of truth otherwise.
- [x] B3. Paywall (StoreKit 2) — Billing protocol + MockBilling (dev-unlocked) + StoreKitBilling, PaywallView from Settings, canPublish gate, Marque.storekit config. (was B3) — a `Billing` protocol + StoreKit2 implementation + a calm paywall screen gated at "publish"; mock entitlement when no products. Add a StoreKit config file for sim testing.
- [ ] B4. LiveClipEngine scaffold — structural AssemblyAI + Shotstack client behind `ClipEngineProtocol`, key-gated; documents that real rendering routes through the backend (B5). Keep `MockClipEngine` as the default/fallback.

## C. Backend skeleton (separate `backend/` dir; python build-verifiable)
- [ ] C1. FastAPI app — `/healthz`, `/v1/scripts` (proxies Anthropic server-side), adapter pattern with mock providers; `requirements.txt`; a `make run` and a tiny pytest that imports the app. No secrets committed.

When EVERY box above is checked, output `<promise>MARQUE BUILDOUT COMPLETE</promise>`.
