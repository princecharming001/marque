# Screen map — Yunicorn → Stoic patterns

Every user-visible screen in the app (89 entries from `screen-inventory.json`, grouped by area),
the closest Stoic pattern from `DESIGN.md` (§5 components, §6 layouts), what changes visually,
and what has no Stoic equivalent. **Presentation only**: every button, input, toggle, gesture,
state, and data call listed in the inventory is preserved on every row.

Legend for "closest Stoic pattern": `Today` = home layout, `Sheet` = drag indicator + centered
lowercase title + ✕, `Editor` = back · progress dashes · ✕ · prompt · floating circles, `Rows` =
grouped list card, `Tiles` = 2-col grid, `Checklist` = "preparing" rows, `Paywall`, `Onboard` =
centered question + stacked capsule options + capsule Next, `Journey` = filter pills + ink insight
card + dated timeline rows, `Stats` = 2×2 stat tiles + eyebrow sections, `Empty` = glyph + title +
body + ghost pill.

## Gate (4)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| PaymentScreen (soft wall / upgrade) | `Paywall` | Dark hero image, Ken Burns, scrim and serif go. Canvas page; badge-line slot holds the mascot; one `surface` container with a single selected `ink` plan tile (the only product); Restore / Terms as centered text links; hairline; sticky footer = 3-line summary + primary capsule "Subscribe for $19.99/month"; "Continue free" as ghost pill. 3.1.2(c) price hierarchy kept. | Stoic sells 4 plans in a 2×2 grid; we sell one. The second grid slot becomes Stoic's "how your free trial works" 4-step timeline so the page isn't empty. |
| SignInScreen (sign in / create) | none → `Onboard` + `Rows` | Dark parity palette, hero image and Fraunces italic go. Canvas; `title1` centered "welcome back." / "save your brand."; fields as `surface` rows in a `radiusGroup` card; primary capsule Continue; Google + Apple as outline capsules (Apple keeps its required glyph/label). | Stoic has no accounts at all. |
| YunicornProPaywall (unpaid) | `Sheet` + `Paywall` footer | Sheet header "yunicorn plus."; features as `Rows` with glyph; promo `ink` strip; sticky footer CTA. | — |
| YunicornProPaywall (paid) | `Sheet` + `Checklist` | "you're on plus." + checked rows + Manage / Restore text links. | — |

## Home (7)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| HomeView | `Today` | Header = streak pill · `title2` "good morning." · avatar. 7-day posting strip (driven by existing weekly target / posted days; today outlined). Hero `ink` card holds the orb + "Tap to talk". Today's picks → `surface` content cards in a peeking carousel. "TRENDING" eyebrow + ticker; "STEAL THESE" eyebrow + reel cards. Tab bar per §5. | The marquee ticker has no Stoic analog — it stays, as a single `caption` line inside a `surface` card. No "Personalize" pill (we don't have the feature; nothing invented). |
| VoiceBubble | hero card content | Becomes the hero card's centered stack: orb, `onInkSecondary` caption. | — |
| VoiceOrb | — | Shape/physics untouched. Gains a tone parameter so it renders as a white drop on `ink` and in dark mode. | — |
| VoiceSessionView | `Sheet` + `Editor` body | Drag indicator; eyebrow "MORNING SESSION" + `title1` "talk to yuni."; orb centered on canvas; assistant text as `bodyLarge`, user turns as `surfaceSunken` `radiusCard` bubbles; chips → capsule chips; composer = search capsule + `ink` circular send. | — |
| ReelFeedPager | none | Full-bleed video stays. Chrome = 48pt outline/ink circular controls, capsule chips, `onInk` text over the bottom scrim. | Stoic has no video feed. |
| ReelStatsSheet | `Stats` | 2×2 `surfaceSunken` stat tiles + eyebrow "TRANSCRIPT" + `body`. | — |
| ReelDetailSheet (unreachable) | `Sheet` + `Stats` | Token restyle only; still not presented anywhere. | — |

## Chat (14)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| ChatView (tab root) | none → `Editor` text + `Journey` rows | Header `title2` "chat." + drawer glyph; assistant replies as plain `bodyLarge` on canvas (Stoic journal voice); user turns as `surfaceSunken` bubbles; suggested chips as capsule chips; composer = search capsule + circular morph button. | Stoic has no chat. |
| ChatUserBubble | bubble | `surfaceSunken`, `radiusCard`, `body`. | — |
| ChatAssistantMessage | journal text | `bodyLarge`, typewriter reveal kept. | — |
| ChatTypingIndicator | — | Three `textSecondary` dots, no color. | — |
| ChatScriptCard | content card | `surface` card: eyebrow format tag, `title3`, `body` hook, outline "Save" + `ink` "Film" capsules. | — |
| ClipEditCard | `Checklist` | Stages as checklist rows; failed = `Empty` inline + outline "Retry". | — |
| ChatVideoAnalysisCard | content card | Eyebrow sections inside one `surface` card. | — |
| DayPlanCard | `Journey` rows | Time blocks as timeline rows. | — |
| ChatSuggestedChips | chips | Capsule chips; "Type my own" as text link. | — |
| MorphSendButton | circular control | 48pt `ink` circle; mic / arrow / stop glyphs. | — |
| ConversationsDrawer | none → `Sheet` content | Stays a left overlay; panel is `surface`, `radiusCard`; eyebrow sections CONVERSATIONS / PREFERENCES; persona + length as single-select rows with trailing check. | Stoic has no drawer. |
| ChatAttachSheet | action menu | `Rows` with trailing glyphs (Photos, Library). | — |
| ChatEditConfigSheet | `Sheet` + `Rows` | Toggle rows, option chips, text field row, footer primary capsule. | — |
| ScriptReaderView (from chat) | see Film | — | — |

## Onboarding (15)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| WelcomeLanding | Stoic interstitial | Mascot centered; brand word + `displayMuted` tagline (left-aligned); primary capsule "Get started" content-sized; text link; legal `caption`. Serif 44 → Matter. | — |
| Niche chip cloud | none → capsule chips | Cloud layout and infinite scroll stay (multi-select is functional). Chips = capsule `surface`+hairline / `ink` selected; the three category colors go — legend becomes three eyebrow labels, chips carry no dot; first pick keeps its star glyph. SegmentedProgress → progress dashes. | Stoic's options are a single stacked column; a 56-chip cloud can't be. |
| Connect account | `Onboard` + `Rows` | Platform cards → grouped rows with glyph + chevron; linked account row with avatar; "Skip" text top-right. | — |
| Scan theater | `Checklist` | Exactly Stoic's "preparing" rows. | — |
| Audience chip cloud | as Niche | Same treatment. | Same. |
| Goal cards | `Onboard` | Stacked capsule options, single-select, auto-advance kept. | — |
| Pace slider | none → stat + slider | `surface` card with `stat` number "5 / week" and a monochrome slider; capsule Next. | Stoic has no slider; nearest is its time picker card. |
| Plan building | `Checklist` | Identical pattern. | — |
| Build failed | `Empty` | Glyph + `headline` + `body` + primary capsule "Try again". | — |
| Plan ready / aha | `Checklist` (checked) | Three checked rows with script titles + primary capsule "Enter Yunicorn". | — |
| Notification primer | Stoic notification page | Bell glyph, `title1`, `body`, primary capsule, "Not now" text link. | — |
| TourOverlay | none | Scrim + spotlight stay; speech card = `surface` `radiusCard` with mascot, `body`, capsule Next/Done. | Stoic has no coach marks. |
| StyleTasteSwiper | none | Deck mechanics stay; cards `radiusHero`, attribute chips monochrome, like/pass as outline/ink circles. | Stoic has no swiper. |
| CTAPickSwiper (unreferenced) | — | Token restyle only. | Dead code. |
| VoiceInterviewView (unreferenced) | `Editor` | Token restyle only. | Dead code. |

## Film (6)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| FilmView hub | `Today` inside a full-screen cover | ✕ + `title1` "film."; hero `ink` card "Film freestyle"; queue as `surface` content cards; "Write your own" outline capsule; drafts as timeline rows; reorder as text link. | — |
| CustomScriptSheet | `Editor` | Title row `title1`, borderless `bodyLarge` editor, floating `ink` circle to save. | — |
| QueueReorderSheet | `Rows` | Native List .onMove kept; rows 52pt in a grouped card. | — |
| ScriptReaderView | `Editor` | Back/✕ header; hook as `title1`; body/CTA `bodyLarge` inline-editable; refine chips as capsules; history / bookmark as circular controls; "Record" primary capsule. | — |
| RecordView | none | Camera full-bleed stays. All 24 controls become circular ink/outline controls and capsule pills over the scrim; "Save as draft" outline capsule. | Stoic has no camera. |
| Teleprompter | none | Script `bodyLarge` `onInk` over scrim; speed pill capsule. | Same. |

## Editor (12)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| ProEditorView | none | Preview canvas untouched. Tool rail → capsule chips; panels → `surface` `radiusGroup` cards; timeline monochrome; all 40 controls kept. | The biggest mismatch: Stoic has no editor. Only chrome changes. |
| Caption list panel | `Checklist` / `Rows` | Rows with timecode trailing `caption`. | — |
| Add media panel | `Rows` | Three rows with glyphs. | — |
| Clean up panel | `Checklist` | Checkable rows + primary capsule. | — |
| Restore panel | `Checklist` | Same. | — |
| Music picker sheet | `Rows` | Trailing check on the selected bed. | — |
| Theme sheet | `Tiles` | 2-col `radiusTile` tiles, selected = `ink`. | — |
| Fullscreen preview | — | Unchanged (video). | — |
| Dialogs (MarqueDialogCard) | Stoic confirm card | `surface` `radiusCard`, `title1`/`body`, outline + primary capsules. | — |
| TweakChatSheet | `Sheet` + chat | As ChatView inside a sheet; proof-render preview in a `radiusTile` card. | — |
| PipelineTimeline | progress dashes + `Checklist` | Four dashes + eyebrow phase names + active line `body`. | — |
| CTALibrarySheet (dormant) | `Rows` | Token restyle only. | Dead code. |

## Root, settings, scripts (11)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| RootView | — | No UI. | — |
| RootTabView | Stoic tab shell | Same 4 roots + center; per-tab NavigationStacks untouched. | — |
| MarqueTabBar | Stoic tab bar | Floating glass capsule goes. `canvas` bar, outline/fill glyphs, `caption` labels (real text, Maestro-safe), 52pt `ink` center circle raised 8pt. | — |
| TourOverlay | see Onboarding | — | — |
| SettingsView | Stoic "your profile." | Eyebrow sections ACCOUNT / NOTIFICATIONS / SUBSCRIPTION / DATA / SUPPORT / APPLICATION; 52pt `Rows`; toggles; Plus upsell as `ink` promo strip; version `caption` footer. | — |
| SettingsIcons | — | Tiles become `surfaceSunken` circles with `textPrimary` glyphs; no color. | — |
| ScriptReaderView | see Film | — | — |
| HookLabSheet | `Rows` | Single-select rows with trailing check. | — |
| ScriptVersionHistorySheet | `Journey` rows | Timeline rows; current version marked. | — |
| CelebrationView | Stoic badge moment | Centered mascot/RankSeal (monochrome), `title1`, `caption`, primary capsule. | — |
| SubmitConfig | — | Not a screen. | — |

## Library, Performance, Profile (20)

| Unicorn screen | Closest Stoic pattern | What changes | Doesn't fit |
|---|---|---|---|
| LibraryView (Clips) | Stoic Library + underline tabs | `display` "your library."; UnderlineTabBar restyled as Stoic's "Badges / Stats" underline tabs; status groups as eyebrow sections; 3-col 9:16 `radiusTile` grid; bulk bar as pinned capsule row. | No search field (we have none; not invented). |
| Library Media tab | `Tiles` + `Empty` | Square grid, import primary capsule, analysis badge as eyebrow. | — |
| Clip detail sheet | `Sheet` | 9:16 player in `radiusTile` card; actions as `Rows`; destructive as text link + confirm. | — |
| Post now sheet | `Rows` + capsule | Platform toggle rows, primary capsule. | — |
| Versions sheet | `Journey` rows | Timeline rows with preview / restore text links. | — |
| Version preview | — | Video + outline/primary capsules. | — |
| Bulk schedule sheet | `Rows` + pickers | Native pickers inside `surface` cards; primary capsule. | — |
| GroupAssignSheet | `Checklist` | Tri-state rows; inline "New group" text-field row. | — |
| Media edit sheet | `Rows` + chips | Kind chips capsule; note field row; fit meter as `stat` tile. | — |
| PerformanceView | `Journey` | Filter pills row (7-day / month); `ink` insights card with strategy door + stat row; date headers `title2`; scheduled posts as timeline rows; 7/30/90 metrics as `Stats` tiles; sparklines monochrome. | Calendar month grid has no Stoic analog; it uses the 7-day strip cell style. |
| StrategyView | pushed page | `display` "your strategy."; eyebrow sections; `surface` cards; verdict as `ink` card. | — |
| Schedule picker sheet | `Rows` + pickers + `Tiles` | Pickers in cards; clip picker as 3-col tiles; primary capsule. | — |
| Edit post sheet | same | Same. | — |
| ProfileView | Stoic profile + `Stats` | Avatar 64 + `title2` name + 3 stat tiles; doors as `Rows` under eyebrows; Settings gear as icon button. | — |
| Brand identity sheet | form `Rows` | Text-field rows. | — |
| Voice & tone sheet | none → cards | Three sliders in `surface` cards with eyebrow labels; monochrome track. | Stoic has no sliders. |
| Content pillars sheet | `Rows` + capsule | Editable rows; add as outline capsule; regenerate primary capsule. | — |
| Creator profile sheet | Stoic Badges sheet | RankSeal + `ink` progress bar; summary `surface` card; trait chips. | — |
| Editing style sheet | `Rows` + chips + card | Toggle rows, option chips, sample reel in `radiusTile` card. | — |
| Connect accounts (profile) | `Rows` | Avatar rows with "Can post / Voice only" as monochrome chips. | — |

## Cross-cutting

| Element | Closest Stoic pattern | What changes |
|---|---|---|
| OfflineBanner | none | Thin `ink` capsule toast at top with `onInk` `caption`. |
| EmptyStateView (all tabs) | `Empty` | Glyph + `headline` + `body` + ghost pill. |
| MarqueToggle / segmented / wheel | Stoic toggle / underline tabs / native wheel | Monochrome track, underline tabs, native wheel in a `surface` card. |
| Video players (InkVideoPlayer etc.) | — | Unchanged; corner radius `radiusTile` via `layer.cornerRadius`. |
| Dark mode | Stoic dark | App is hard-locked light today (`.preferredColorScheme(.light)`); every token gains a dark pair; the lock is removed. |
