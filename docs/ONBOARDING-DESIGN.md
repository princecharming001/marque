# Marque Onboarding — Design Language Spec

The single source of truth for the onboarding redesign. Derived from a Mobbin study
of Cal AI's full 34-screen onboarding (the conversion benchmark) and Alma's
onboarding (the differentiation reference), fused with Marque's existing editorial
identity. If a screen disagrees with this doc, the screen is wrong.

## 1. Design DNA

**Cal AI skeleton** (what we mimic):
- Thin progress indicator + circled back button pinned to the top of EVERY step.
- One big headline + one small gray subtitle. Never more than two text blocks
  before the content.
- Content vertically centered; enormous whitespace; one idea per screen.
- Monochrome discipline: near-black on off-white, ONE accent used sparingly.
- Full-width pill CTA at the bottom — only on screens that need one.
- Benefit "interstitial" screens between question clusters (a stat/comparison
  that amplifies commitment, not a mascot filler).

**Marque identity** (what makes it ours, not a Cal AI clone):
- Headlines in Fraunces serif (`Typeface.display`) — Cal AI uses geometric sans;
  our serif is the brand signature.
- Warm cream canvas `Palette.canvas` (#F1F1EF), warm ink `Palette.ink` (#1C1A17).
- The matte-black clay 3D unicorn mascot (sparingly: landing, 2 interstitials,
  plan-building, celebration — never floating chips).
- Accent blue `Palette.accent` appears at most ONCE per screen (usually never).

**Alma borrowings** (the "unique clean" layer):
- Segmented-dash progress (one dash per quiz question) instead of a single bar.
- Tall rounded option cards: icon badge + title + optional gray subtitle.
- Auto-advance on single-select — no Continue button, back always available.

## 2. Universal layout skeleton (every step)

```
┌──────────────────────────────────────┐
│ (◀)  ▬▬ ▬▬ ▬▬ ▭▭ ▭▭ ▭▭ ▭▭            │  Top bar, fixed 44pt:
│                                      │  BackCircle + SegmentedProgress
│            Headline serif            │
│         one-line gray subtitle       │  Header block, centered
│                                      │
│        ┌──────────────────┐          │
│        │   content region │          │  Vertically CENTERED in the
│        │   (cards/field/  │          │  remaining space — never pushed
│        │    mascot/etc.)  │          │  to top or bottom
│        └──────────────────┘          │
│                                      │
│        [ ink pill CTA 56pt ]         │  CTA slot — ONLY multi-select /
└──────────────────────────────────────┘  freeform / interstitial steps
```

Rules:
- `OnboardingScaffold` implements this; steps only provide header strings,
  content, and an optional CTA. No step lays itself out.
- Keyboard: the scaffold does NOT ignore the keyboard safe area; the centered
  content region compresses symmetrically so a text field stays visually
  centered above the keyboard. No jump, no push-to-top.
- Progress dashes count QUIZ questions only (interstitials/freeform included,
  landing/building/ready excluded). Back button on every step except landing
  and building/ready.
- Horizontal padding: `Space.screenH` (20pt) everywhere.

## 3. Component specs

**SegmentedProgress** — capsule dashes, 4pt tall, `Space.xs` gaps; filled = ink,
rest = #E2E1DE; fill animates `.easeOut(0.38)`.

**BackCircle** — 36pt circle, white fill, hairline stroke, `chevron.left` 14pt
semibold ink. `accessibilityIdentifier("onboard.back")`.

**OptionCard** — min height 72pt, `Radius.xl` (22), white surface, hairline
border, 16pt internal padding.
- Leading icon badge 44×44: the matte-black clay `OnbIcon-*` asset (interim:
  monochrome ink SF Symbol at 20pt in a `surfaceSunken` circle).
- Title `AppFont.headline` ink; optional subtitle `AppFont.caption` textTertiary.
- Selected: `Palette.ink` 1.5pt border + `scaleEffect(1.02)` + shadow unchanged.
  NO green ring, NO tinted icon circles, NO accent fills.
- Whole card is the tap target (`Button` + `.contentShape`).

**Pill CTA** — 56pt, `Radius.pill`, ink fill, `onInk` label `AppFont.headline`.
One per screen max.

**UnicornMascot(pose:size:)** — asset-backed (`UnicornHero/Thinking/Proud/
Celebrate`), appear-bounce (`Motion.spring`, 0.7→1.0) + slow breath
(`Motion.breath`, 1.0↔1.03). No code-drawn features, no orbiting chips.

## 4. Motion rules

- Selection: `Motion.spring` on the card border/scale + light-impact haptic
  (`.sensoryFeedback(.impact(weight: .light), trigger: tick)`).
- Auto-advance: cancellable 300ms after selection; re-tap within the window
  re-arms it (last selection wins); back cancels it.
- Step transition: forward = slide-from-trailing + fade (`Motion.enter`);
  back = fade (mirrored move feels heavy in reverse).
- Entrances within a step: `staggerReveal` (existing), indices top-to-bottom.
- Flow completion ("Enter Marque"): `.success` haptic.

## 5. Icon asset naming contract (generated LAST, after mascot look approval)

Matte-black clay 3D, same lighting family as the unicorn, on transparent/cream,
rendered small-legible (readable at 44pt). One per MCQ option:

| Step | Options → asset names |
|---|---|
| goal | `OnbIcon-goal-audience` (megaphone), `-clients` (handshake), `-authority` (crown), `-monetize` (coin stack) |
| blocker | `OnbIcon-blocker-ideas` (empty lightbulb), `-time` (hourglass), `-editing` (scissors), `-confidence` (masked face) |
| frequency | `OnbIcon-freq-rarely` (turtle), `-sometimes` (walking figure), `-often` (rabbit), `-daily` (flame) |
| platform | `OnbIcon-platform-instagram` (camera), `-tiktok` (music note), `-both` (two overlapping squares) |
| comfort | `OnbIcon-comfort-natural` (video camera), `-getting` (camera half-open), `-off` (microphone) |
| pace | `OnbIcon-pace-3` (three dots), `-5` (five dots), `-7` (seven-dot week row) |
| voiceTeach | `OnbIcon-voice-connect` (link/chain), `-interview` (speech bubble) |

~25 assets total. Until they land, `OptionCard` renders the SF-Symbol fallback
listed in code — monochrome ink only.

## 6. Mascot usage map

| Pose | Where | Size |
|---|---|---|
| `UnicornHero` | Landing (top ~55% of screen, ~60% width) | large |
| `UnicornThinking` | Interstitial A (method) + PlanBuilding | 150–170pt |
| `UnicornProud` | Interstitial B (brand mirror) | 150pt |
| `UnicornCelebrate` | Plan-ready (aha) | 150pt |

Nowhere else. The blue-circle PlaceholderMascot and every floating-chip
decoration (StarField, ConstellationLines, FloatingDecor, DarkLandingBadge,
MascotScene, ThoughtCloud, PulsingWaveformBadge) are deleted.

## 7. Copy deck (drives Maestro asserts — keep in sync with flow-full.yaml)

| Step | Headline | Subtitle |
|---|---|---|
| landing | Film once.\nPost every day. | Your AI content partner for short-form video. |
| goal | What are you here to do? | This shapes every script I write for you. |
| blocker | What gets in the way most? | I'll build your plan around fixing this. |
| frequency | How often do you post right now? | No judgment — this is the before picture. |
| method (int. A) | Consistency beats virality | (personalized off frequency) |
| name | What should I call you? | The name you'd like to go by. |
| niche | What's your niche? | Fitness, finance, cooking… whatever you make content about. |
| about | Tell me about you | What you do, and who it's for. |
| knownFor | What do you want to be known for? | The heart of your brand — one sentence. |
| platform | Where does your audience live? | Where your clips will land first. |
| voiceTeach | Let me learn your voice | So every script sounds like you — not a template. |
| voiceSliders | Fine-tune your voice | Slide until the preview sounds like you. |
| cameraComfort | How do you feel on camera? | There's a format for every comfort level. |
| styles | Pick your video styles | Each gets its own kind of script. |
| pace | Pick your weekly pace | You can change this anytime. |
| mirror (int. B) | Your brand, in a sentence | (the composed brand sentence, set in serif) |
| building | Building your content plan | Feel free to close the app — I'll notify you. |
| ready | Your first 3 scripts are ready | Record when you've got a few minutes — I'll do the editing. |

## 8. Question order rationale (conversion)

Pain first (goal→blocker→frequency) hooks motivation before asking for effort;
interstitial A converts that pain into belief in the method; identity cluster
(name→niche→about→knownFor) rides the commitment; platform + voiceTeach are the
highest-effort asks and sit AFTER investment is built; voice sliders prefill from
the reel scan when available; styles/pace are easy closers; the brand mirror
(interstitial B) plays the answer back as proof-of-listening right before the
plan builds. The `stage` (follower-count) question is CUT — lowest-signal, and
asking "how small are you" early is a downer; the field stays editable in Profile.
