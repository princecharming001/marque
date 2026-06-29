# Marque buildout backlog

Work top-to-bottom. Check off `[x]` when an item is DONE (built, `scripts/dev.sh build` green,
Maestro green if UI-affecting, committed). Verifiable items first; key-gated integrations after.

## A. Verifiable app-side (build + Maestro can confirm)
- [x] A1. Brand Profile screen — editable Brand Graph. Done: BrandProfileView (niche/what-you-do/audience/known-for, voice sliders, non-negotiable chips, pillars), person icon on Today, saves to store; flow-extras.yaml green.
- [ ] A2. Insights screen — performance overview (mock stats from clips/schedule: posts this week, best clip by predicted score, format breakdown). Reached from Coach. Calm cards.
- [ ] A3. Calendar scheduling completeness — schedule a ready clip onto a day, mark it `.scheduled`, surface it as "Next up" on Today. Cover scheduling in `flow-extras.yaml`.
- [ ] A4. Studio pillar regenerate — verify tapping a pillar generates new scripts; add Maestro step (tap a pillar id, assert script count grows).
- [ ] A5. Hook Lab + steer — verify in Maestro: open a script, open Hook Lab, pick a hook; tap a steer chip; assert the reader updates.
- [ ] A6. Repurpose-in — add a "Upload existing" source on Record using PhotosPicker (second entry to the same makeClips pipeline). Sim: selecting is fine; flow stays green.
- [ ] A7. Streak + a minimal celebration — increment streak per recorded session; show a quiet one-time celebration sheet; full streak view in Brand Profile.
- [ ] A8. States pass — ensure every screen has loading / empty / error / offline handling per the design system; add an offline banner component.

## B. Key-ready integrations (compile-clean, key-gated, mock fallback — NO new SPM deps; use URLSession / StoreKit)
- [ ] B1. AyrsharePublisher — real `URLSession` POST to Ayrshare behind `Publishing`; AppStore uses it when `AppConfig.ayrshareKey` is set, else `MockPublisher`. Untestable without a key (that's fine).
- [ ] B2. SupabaseStore — REST (`URLSession`) persistence adapter behind a `Persistence` protocol; used when supabase url+anonKey set, else the local UserDefaults store. Mirror scripts/clips/schedule.
- [ ] B3. Paywall (StoreKit 2, no RevenueCat SDK) — a `Billing` protocol + StoreKit2 implementation + a calm paywall screen gated at "publish"; mock entitlement when no products. Add a StoreKit config file for sim testing.
- [ ] B4. LiveClipEngine scaffold — structural AssemblyAI + Shotstack client behind `ClipEngineProtocol`, key-gated; documents that real rendering routes through the backend (B5). Keep `MockClipEngine` as the default/fallback.

## C. Backend skeleton (separate `backend/` dir; python build-verifiable)
- [ ] C1. FastAPI app — `/healthz`, `/v1/scripts` (proxies Anthropic server-side), adapter pattern with mock providers; `requirements.txt`; a `make run` and a tiny pytest that imports the app. No secrets committed.

When EVERY box above is checked, output `<promise>MARQUE BUILDOUT COMPLETE</promise>`.
