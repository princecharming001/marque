# Marque — Build Progress

> Status as of 2026-06-29. The spec lives in `docs/` (18 sections). This file tracks what's
> actually built and verified vs. what's next.

## What runs today (verified)

A native **SwiftUI iOS app** that implements the **entire core loop end-to-end in keyless mock mode**,
built to the locked Stoic aesthetic (cream/near-black, serif display, gold accent, anti-clutter Today).

- **Project**: `ios/` — XcodeGen (`project.yml`) → `Marque.xcodeproj`, iOS 17+, iPhone-only, builds clean with Xcode 26 / Swift 6.2.
- **Design system** (`DesignSystem/`): semantic light+dark palette, serif/grotesque type scale, spacing/radii/motion tokens, components (PrimaryButton with ink-on-gold label per the contrast rule, cards, chips, pillar node, score badge, streak glyph, format tag).
- **Navigation** (`App/`, `Navigation/`): 5-tab shell (Today / Studio / Library / Calendar / Coach), per-tab `NavigationStack`, `AppRouter`.
- **State + persistence** (`State/AppStore.swift`): `@Observable` store, JSON persistence to UserDefaults, `-reset` launch arg for clean runs.
- **Adapter layer** (`Adapters/`): `LLMRouting`, `ClipEngineProtocol`, `Publishing`, `InsightsProviding` — each with a **deterministic, logic-shaped mock** (real hook templates across the 8 signal types, format selection, virality scoring from the Brand Graph). Swap to live = a new conformer + a key.
- **Flows**: onboarding (goal → niche/about → known-for → voice → connect/skip → **AI generates first 3 scripts** → enter), Studio (pillar constellation → generate scripts), Script Reader (hook + body + CTA + shot plan, **Hook Lab** sheet, **format swap**, steer chips), Record (teleprompter + capture + **"make my clips"** → renders N formats), Library (clips by status), Calendar (week + schedule), Coach (Trend Radar personalized to niche + teardown cards).

### Verified by Maestro (`.maestro/flow-full.yaml`, all steps COMPLETED)
onboarding → AI script generation → Today → Studio → Script Reader → Record → **3 clips rendered in 3 formats** → Library → Calendar → Coach. Proof screenshots in `.shots/`.

Run it:  `scripts/dev.sh test`   ·   build only: `scripts/dev.sh build`

## Known simplifications (mock-mode, by design until keys arrive)
- **AI is the deterministic mock engine**, not live Claude (no `ANTHROPIC_API_KEY` present). The `LLMRouter` adapter is ready for a live Anthropic conformer.
- **Record uses a simulated capture**, not the real AVFoundation camera/teleprompter yet.
- No backend (Supabase/FastAPI), no real publishing (Ayrshare/IG/TikTok), no StoreKit/RevenueCat, no real video render (Shotstack/R2). All are spec'd and sit behind adapters.

## Next milestones (see `docs/17-roadmap-milestones.md`)
1. **Real Record**: AVFoundation `AVCaptureSession` + offset-driven teleprompter + background-resumable upload stub.
2. **Live AI adapter**: Anthropic `LLMRouter` conformer (Opus 4.8 / Haiku 4.5) behind an env key; keep mock as fallback.
3. **Backend**: FastAPI orchestrator + Supabase schema + Trigger.dev jobs (mock providers until keys).
4. **Publishing + scheduling**: Ayrshare adapter + the TikTok/IG compliance UX; real `Insights` pullback.
5. **Monetization**: StoreKit 2 + RevenueCat paywall + entitlement gates.
6. **Polish + compliance**: states, accessibility pass, App Store gates (UGC, AI disclosure, deletion).

## Credentials needed to go live (paste into backend, never the app)
Anthropic, Supabase, Ayrshare (or chosen social provider), AssemblyAI, Shotstack, Cloudflare R2+Stream, RevenueCat, Sentry, PostHog. See `OPEN-QUESTIONS.md`.
