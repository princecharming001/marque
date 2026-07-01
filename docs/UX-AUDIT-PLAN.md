Good, the audit's line references are grounded. I have everything I need to write the plan.

# Marque — UX Remediation Plan (Head of Product)

Repo: `/Users/home/Marque` · iOS source root: `/Users/home/Marque/ios/Marque`

## 1. Executive summary

Marque's flows work, but four themes make it read as a prototype rather than a billion-dollar consumer product. **(1) Dev/mock state leaks to users** — "AI · Mock", "Reset app to first run", and a "No camera in the Simulator" string all ship to real devices. **(2) Fabricated metrics are presented as real** — predicted scores, "projected views," "+N follows," sparklines, and "beat X% of your posts" render at the same visual weight as measured data, which a savvy creator instantly falsifies against IG/TikTok analytics. **(3) Hard App Store compliance gaps** — no Delete Account, no working Restore Purchases, no wired paywall, no Privacy/Terms links; the paywall shows a trial it can't fulfill. **(4) Broken core goals + missing item actions** — the record flow has no take-review, generated clips can't be exported/edited/deleted, the learn-from-performance loop is unreachable after onboarding (no connect entry point, dead MetricsEntrySheet), and the calendar is a fixed 7-day strip with a silent no-op schedule path. Onboarding friction (low-signal tone sliders, ~6 screens before the aha, no back button) and app-wide tab/title mismatches round out the polish gaps.

---

## 2. P0 — do now (ordered by value / effort)

Compliance blockers and dishonesty/broken-goal fixes. Auto-implement tags: **SAFE** = pure UI/copy, no product ambiguity; **DECISION** = needs a product/eng call first.

1. **Kill the "AI engine / AI · Mock" leak everywhere.** `Adapters/BackendClient.swift` (11, 37), `Features/SettingsView.swift` (14-20), `Features/StudioView.swift` (18-20). How: keep `lastMode` internal, delete both user-facing rows/badges (or `#if DEBUG`-gate); drop the `Claude`/`Mock` mapping from any rendered string. **SAFE.**

2. **Add in-app Legal links (Privacy Policy + Terms).** `Features/SettingsView.swift` (Legal group at bottom), `Features/PaywallView.swift` (terms/privacy line near price). How: two `Link`s to hosted URLs + a paywall footnote. **DECISION** (needs the two hosted URLs; the wiring is SAFE).

3. **Remove "Reset app to first run" from visible Settings.** `Features/SettingsView.swift` (45-51). How: delete the GhostButton; if QA needs it, move `store.resetAll()` behind a `#if DEBUG` gesture on the version footer. Must land with #7 so the "Account" section isn't left empty. **SAFE.**

4. **Home/Today + Plan/Calendar tab-vs-title mismatches.** `App/MarqueTabBar.swift` (10, 11), `Features/TodayView.swift` (30), `Features/CalendarView.swift` (17). How: one word each — tab+title "Today", and tab+title "Calendar". **SAFE.**

5. **Relabel every ScoreBadge as an explicit forecast (one shared component).** `DesignSystem/Components.swift` (208-219); affects `StudioView` (140), `ScriptReaderView` (43), `LibraryView` (79, 101), `InsightsView` (30), `CalendarView` (174). How: add a visible "Predicted" tag/qualitative band, styled distinctly from measured metrics; fix the component once. Do NOT hard-delete instances. **SAFE.**

6. **Fix the "Simulator" string + honest permission-denied state in Record.** `Features/RecordView.swift` (81), `Features/CameraModel.swift` (28-32). How: replace the ternary's "No camera in the Simulator…" with a consumer string; split denied-camera / denied-mic → an explicit state with a deep link to `UIApplication.openSettingsURLString`. **SAFE** (copy) **+ DECISION** (mic-vs-cam distinction is a small logic call).

7. **Add Delete Account flow.** `Features/SettingsView.swift` (new red row → confirm sheet). How: confirm sheet → wipe local state + any backend brand data; show completion. Apple 5.1.1(v) is an auto-reject gap. **DECISION** (define what "account" means at launch given no live auth).

8. **Wire real purchase + entitlement gating and a working Restore.** `Features/PaywallView.swift` (37 trial CTA, 41 empty Restore closure, 48), `State/AppStore.swift` (45 uses MockBilling), `Adapters/Billing.swift` (13 isPro defaults true). How: swap MockBilling→StoreKitBilling, wire `purchase()` + trial product, implement real `restore`, add Restore + Manage-Subscription rows in Settings. Until wired, replace the "Start 7-day free trial" CTA with honest neutral copy — never a fake trial. **DECISION** (trial product config + entitlement model).

9. **Gate the Today momentum card on real metrics; hold the honest empty state.** `Features/TodayView.swift` (64, 71-101), `State/AppStore.swift` (399-422). How: change the gate from `activeClipCount>0` to `schedule.compactMap{$0.metrics?.views}.reduce(0,+) > 0`; otherwise keep the existing honest empty/teaching state. This is the umbrella fix that removes fabricated views/follows/sparkline. **SAFE.**

10. **Add a take-review / re-record step before "Make my clips."** `Features/RecordView.swift` (47, 151-179). How: new phase between `.recorded` and `makeClips()` that plays back `footagePath` with "Use this take" / "Re-record" (restart via `restartToken++`); route the upload path through it too. **DECISION** (new phase in the record state machine).

11. **Reframe Coach teardown + kill the fabricated "beat X% of your posts."** `Features/CoachView.swift` (19-32, 51-57), `Adapters/Adapters.swift` (162-165). How: gate the trigger on `.posted` clips with logged metrics; reframe copy to forward-looking ("Likely to outperform your recent posts") until real percentiles exist. **SAFE** (copy + trigger gate).

12. **Surface real logged metrics in Insights + a discoverable "Log results" entry point.** `Features/InsightsView.swift` (never reads `.metrics`), `Features/MetricsEntrySheet.swift` (orphaned — zero instantiations), `Features/CalendarView.swift` (present it from a posted/scheduled row). How: read `ScheduledPost.metrics` / `engagementRate` into Insights; present `MetricsEntrySheet` from a one-tap affordance. **DECISION** (where the log-metrics button lives). Pairs with #9/#11.

13. **Fix the silent no-op on the free schedule path.** `Features/CalendarView.swift` (SchedulePickerSheet, 174, 196), `State/AppStore.swift` (301 `guard canPublish`). How: surface the paywall at the clip-tap moment ("Upgrade to schedule") instead of dismissing into nothing. Spec-aligned (`docs/11-monetization.md` gates schedule+publish). Latent behind `Billing.isPro` default-true today. **DECISION** (ties to #8).

14. **Give generated clips an exit + editable caption.** `Features/LibraryView.swift` (ClipCell 79, ClipDetailSheet 101-123). How: per-clip overflow (•••) with Share/Export (share sheet / save to Photos), Copy caption, Delete-with-confirm; make `clip.caption` editable before scheduling. No export path exists app-wide today — core "post-ready clip of you" goal is broken. **DECISION** (export target: share sheet vs Photos).

15. **Give Studio scripts per-item actions + non-destructive Steer.** `Features/StudioView.swift` (ScriptCard), `Features/ScriptReaderView.swift` (100-103), `State/AppStore.swift` (172-176 `steer` overwrites). How: context menu with Delete-confirm + Copy + Share; make Steer create a revertable version instead of overwriting hook/body/CTA/score in place. **DECISION** (version-vs-revert model).

16. **Add a persistent Connect Instagram/TikTok entry point outside onboarding.** `Features/ConnectAccountsView.swift` (only reachable from `OnboardingView` 116), surface in `Features/SettingsView.swift` (Accounts section) and/or the Today/schedule moment. How: reuse the existing view from a Settings row + in-context prompt. Without it the learn-loop is unreachable post-onboarding. **DECISION** (placement).

---

## 3. P1 — strong improvements

1. **Promote "Analyze my page" as the hero onboarding action; make the typed VoiceOnboardingSheet the fallback.** `OnboardingView.swift`, `AppStore.swift` (100-113 already prefers `brandScan`). Real posts > sliders.
2. **Fix VoiceOnboardingSheet fallback copy honesty.** `VoiceOnboardingSheet.swift` (106, 118-132): on `derivePillars()` fallback, say "Starter pillars for <niche> — refine anytime," not "derived from what you told us."
3. **Add skip/default + back navigation to onboarding.** `OnboardingView.swift` (87 knownFor no guard/skip; 166-169 advance-only). Skip/derive-default on `knownForStep`; back chevron in the progress header. **SAFE.**
4. **Honest progress copy on connect step.** `OnboardingView.swift` (118): "Reading your page…" / "Writing your scripts…" not "Setting up…". **SAFE.**
5. **Make Today "Next up" row actionable + distinct forecast treatment.** `TodayView.swift` (146-158 read-only; 82-85 hero numeral). Tap → open post in Calendar; style any forecast (muted/italic "Predicted") distinctly. Separate "This week posted: N" from forecast (`AppStore.swift` 407-414). **SAFE.**
6. **Add mic-status warning + makeClips error/progress surface + default to only the picked format.** `RecordView.swift` (29 auto-seeds `broll-hook`+`faceless`; 173-179 swallows failure), `CameraModel.swift` (44-47 silent no-audio), `AppStore.swift` (243 non-throwing). Default `_selectedFormats` to `[script.formatId]`; surface "Microphone off"; add real progress + retry. **DECISION** (defaults).
7. **Inline-edit Hook + CTA in ScriptReader; rename "Steer"→"Refine".** `ScriptReaderView.swift` (82 CTA display-only; 96-99 chips). Body already proves the pattern (60-81). **SAFE.**
8. **Library: delete for Clips/Footage, empty-state CTA to Create, pre-fill "Schedule this clip", de-jargon copy.** `LibraryView.swift` (35-36, 143-144 empty states; 120-123 schedule hand-off drops unattached; 167 "Imported take" dup; 294 "corpus"; 353/375 "AI"/"Auto" labels). Empty-state button → `router.showCreate`. **SAFE** except the pre-fill hand-off (needs a `pendingClip` state — **DECISION**).
9. **Calendar: Week/Month toggle, per-post overflow (Edit/Change time/Duplicate/Delete-confirm), inline best-time suggestion, confirm on destructive delete.** `CalendarView.swift` (8-12 fixed today+6; 142 hardcoded 18:00; 256-257 no confirm). Duplicate is load-bearing for the clip-reuse thesis. Frame best-time as a plain-language suggestion, not a score. **DECISION.**
10. **Coach trend cards: "Write a script for this" CTA carrying title+formatId into Studio.** `CoachView.swift` (40-47 inert), `Adapters.swift` (274/276 de-jargon "over-indexing"). **DECISION** (confirm Studio accepts a seed).
11. **Insights: honest "not enough data" first-run state; decision metrics with timeframe/comparison.** `InsightsView.swift` (20-24 vanity tiles, 26-32 predicted "Top clip"→"Highest predicted", 47-50 sole empty guard). **SAFE** (relabel/empty state); decision metrics depend on #12 landing.
12. **Settings: Notifications section (reminders toggle + Settings deep link) + Manage Subscription row.** `SettingsView.swift`. Core "consistency" promise needs reminders; no `UNUserNotification` exists today. **DECISION.**

---

## 4. P2 — later / nice-to-have

- Onboarding: defer "What you do"/"Who you serve" off the first screen (keep them — heavily load-bearing in `Adapters.swift` 173-176); collapse the ~6-screen survey to 2-3 branching questions; rebalance connect as visibly optional.
- Record: live recording timer + `targetSeconds` hint; 3-2-1 countdown; gate teleprompter pencil/tap-edit to `.ready` only; distinct start/stop `accessibilityLabel`s; format-chip blurbs + visually-marked pre-selected defaults; "Make my clips" confirm-count.
- Today: "Morning, @handle" greeting; Create FAB `accessibilityLabel` "Studio"→"Create"/"Record" + tap haptic (`MarqueTabBar.swift` 60).
- Calendar: drag-to-reschedule (L effort); device-timezone label + "publishes in ~2h"; status pills; branded IG/TikTok platform glyphs (127); reconcile intro copy ("reschedule") with the added quick action.
- Library: MediaEditSheet outcome-language labels; demote helper paragraphs; date/index-derived import titles.
- Studio: drop "8 signal types" in Hook Lab (`ScriptReaderView.swift` 147).
- Settings/Brand: demote the three voice sliders to an optional secondary nudge (keep them — also in `BrandProfileView` 25-27); sample-based voice capture on Brand (L); account-identity block (connected handle) + Sign Out; version/build footer; resolve `BrandProfileView` "Brand" vs "What Marque knows about you" title dup (14, 83); paywall title "Marque Pro" vs "Upgrade to Pro" drift.
- Coach: `accessibilityLabel("Insights")` on the icon-only entry (13-16).
- Global: `.refreshable` on genuinely server-backed lists; design-review (don't auto-strip) the tab-bar frost stack (`MarqueTabBar.swift` 83-95).

---

## 5. Explicitly cut / keep (guard against over-removal)

**Keep — do NOT delete:**
- **`whatYouDo` / `audience` model + capture** — heavily load-bearing in the mock generator (`Adapters.swift` 173-176). Only move off the first screen; never drop.
- **`knownFor`** — the generator leans on it (79, 175). Require/derive/skip, don't silently allow empty.
- **The format picker & its defaults, and Footage vs Media as separate tabs** — `makeClips` consumes `selectedFormats`; Footage→`FootageDetailSheet`→`makeClips` and MediaAsset→reference-corpus are genuinely distinct models/flows. Re-default the pre-seed (P1), don't delete the picker or merge the tabs.
- **`ScoreBadge` type, `predictedScore`, `store.streak`, "Your format mix" card, `preferredStyles`, `resetAll()`** — all still referenced elsewhere. Relabel/gate/relocate, never hard-remove.
- **VideoStyle (pre-gen) vs format (post-gen)** — a real functional distinction; do not collapse to one vocabulary.

**Cut from the audit (rejected in verify — do not action):**
- **Streak grace/recovery** — no date-based decay exists (`AppStore.swift` 269 is a plain `+=1`); nothing to protect against.
- **Front/back camera toggle** — marginal for a teleprompter product; the honest resolution is to *remove* the dead `camera.rotate` icon (`RecordView.swift` 73), not build a toggle.
- **Let free users schedule and gate at publish** — contradicts `docs/11-monetization.md` (schedule+publish is one hard gate); the correct fix is the paywall-at-clip-tap (P0 #13).
- **Retry-on-failed-clip, Duplicate-clip, Regenerate-captions in Library** — `.failed` is unreachable in mock, duplicate already works via the Calendar picker, and caption-regenerate has no backend endpoint. Delete-with-confirm + editable caption cover the real gaps.
- **goalStep "serif vs sans" contradiction** — false per code (`AppFont.question` → Inter, a real sans).
- **"API KEYS" Settings section** — phantom; exists only in a stale screenshot (`.shots/settings.png`), rendered by no code.
- **InsightsView source-comment removal, "predicted score" coaching leak, "Studio" screen-title rename, predicted-vs-actual styling task, refresh-trends, Library search/filter, paywall annual tier, Support section** — all redundant, speculative, or premature; revisit post-P0.

**Files touched most:** `Features/SettingsView.swift`, `Features/PaywallView.swift`, `Features/RecordView.swift`, `Features/CameraModel.swift`, `Features/LibraryView.swift`, `Features/CalendarView.swift`, `Features/TodayView.swift`, `Features/InsightsView.swift`, `Features/CoachView.swift`, `Features/OnboardingView.swift`, `Features/StudioView.swift`, `Features/ScriptReaderView.swift`, `State/AppStore.swift`, `Adapters/BackendClient.swift`, `Adapters/Billing.swift`, `Adapters/Adapters.swift`, `DesignSystem/Components.swift`, `App/MarqueTabBar.swift`, `Features/MetricsEntrySheet.swift`, `Features/ConnectAccountsView.swift`.