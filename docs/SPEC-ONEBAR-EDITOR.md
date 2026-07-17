# SPEC: One-Bar Editor (CapCut/TikTok parity refactor)

Status: SPEC — not implemented. Verified against source 2026-07-17 (every file:line below spot-checked).
Scope: `ios/Marque/Features/Editor/` + `.maestro/` flows + two one-line backend-adjacent gate fixes.
Reference model: TikTok/CapCut mobile editor (screen-recording teardown, 2026-07-17).

The core change: replace the permanent 3-layer chrome stack (64pt mode-tab bar + 44pt always-visible
context strip + 52pt mode drawer) with ONE contextual toolbar that swaps wholesale on selection,
and give the reclaimed ~120pt to the starved timeline lanes.

---

## 0. Phase P0 — independent bug fix (ship first, no UI dependency)

**Style-gate mismatch** (op silently dropped while button shows):
- `LocalEDLEngine.swift:303` punch-in gate `["talking_head","duet_split"]` → `["talking_head","duet_split","green_screen","broll_cutaway","split_three"]` (= backend `_PUNCH_STYLES`, backend/app/edl.py:1182).
- `LocalEDLEngine.swift:309` text-card gate `["green_screen","duet_split"]` → `["green_screen","duet_split","talking_head","broll_cutaway"]` (= `_TEXTCARD_STYLES`, edl.py:1186).
- `ProEditorView.swift:1859-1860` — the `punchInsSupported`/`textCardsSupported` local fallback lists: same two lists.
- Optional cosmetic: backend/main.py:3675 `_mock_edl` has a stale hand-mirror of `_PUNCH_STYLES` ("mirrors" comment now false) — align or drop the comment.

**Dead code** (verified orphaned):
- `showStickerInput` @State (:37) + its marqueInput dialog (:139-140) — never set true.
- `adjustDraft` @State (:56) — zero references.
- `setStickerStyle` (+Actions:440-442), `setCaptionStyle` — no call sites.
- Stale "long-press enters reorder" header comment in EditorTimeline.swift (no such gesture exists).

---

## 1. State model

### Deleted
`mode: Mode` (+ enum, :16/:24), `modeToolbar` (:1832-1853), `modeDrawer` (:330-441 chip switch),
`contextStrip`/`segContextStrip` (:1676-1819), `openModeDrawer` (:1871, documented no-op),
`timelineExtra`/`timelineExtraBase` (:90-91) + drag capsule (:287-293; id `editorPro.timelineDivider`
has zero external references — safe), `speedPanelSeg` (:53, folded into `expansion`).

### Added
```swift
enum RootPanel { case sound, text, captions, effects, filters }
@State var rootPanel: RootPanel? = nil

@State var selectedMusic = false          // INVARIANT: true only while session.draft.music != nil
@State var selectedPhraseID: Int? = nil   // CaptionPhrase.id (= startFrame) — NEVER an array index;
                                          // phrases is recomputed per draft change, indexes go stale.
                                          // Resolve: phrases.first { $0.id == selectedPhraseID };
                                          // toolbarState falls to ROOT when resolution fails.

enum Expansion: Equatable {               // exactly ONE open; opening one closes the previous
  case speed(seg: Int)                    // replaces speedPanelSeg
  case clipVolume(seg: Int)               // NEW — clip volume slider moves out of the Sound drawer
  case musicVolume
  case captionStyle, captionCustomize     // preset row / options row as phrase-vocab expansions
  case transitionDuration(boundary: Int)
}
@State var expansion: Expansion? = nil
```
`showCaptionCustomize`/`showFilterAdvanced` stay (they gate rows inside the .captions/.filters
ROOT PANELS); the phrase-vocabulary Style/Customize use `expansion` instead.

### The choke-point setter (the invariant's enforcement — REQUIRED)
Today selection writes are scattered across ≥6 sites that each clear a *different* partial subset
(EditorTimeline.swift:120-124, 249-255, 497-502; ProEditorView.swift:962-970, 1313, 1326-1332;
+Actions:382-386, 505-510, 590-593; doUndo/doRedo :1026-1042). A music/phrase selection could
never be cleared by any of them. Therefore:

```swift
enum SelectionTarget { case seg(Int), overlay(Int), broll(Int), boundary(Int), music, phrase(Int) }
func select(_ target: SelectionTarget?)   // the ONLY writer of selection state
```
`select(_:)` semantics, in order:
1. If `typingSticker != nil`: `commitTyping(idx)` FIRST (before touching any state — the on-canvas
   TextField binds the shared `editDraft` buffer; ProEditorView.swift:163-165, 1283).
2. Pause the player.
3. Nil every selection var + `expansion`; nil `rootPanel` (selection and rootPanel are mutually
   exclusive). Then set the target.
4. Haptic tick.

Rewiring: EditorTimeline's direct `@Binding` mutations convert to callbacks — `selectedSeg`/
`selectedOverlay` become plain `let` + `onTapClip(Int)` / `onTapOverlay(Int)` / `onTapBackground()`
(matching the existing selectedBoundary/selectedBroll callback pattern, EditorTimeline.swift:28-32).
`canvasTapSelect`, sticker tap, `selectLastRoll`, `addTextSticker`, `addPunchInOnHook`, and
`doUndo`/`doRedo` all route through `select(_:)`.

**Invalidation rules** (all verified failure modes):
- Undo/redo → `select(nil)` and `rootPanel = nil` (today they miss speedPanelSeg; :1026-1042).
- Every vocabulary's Delete clears its own selection (music Delete → `selectedMusic = false`;
  today `removeMusic()` clears nothing, +Actions:213).
- Captions toggled off → `selectedPhraseID = nil`.
- Clip Delete → `select(nil)` (closes any open Speed/Volume expansion pointing at the dropped seg).

**toolbarState priority** (belt-and-suspenders for any transient double-set):
`broll > boundary > overlay > phrase > music > seg > root`.

### onChange(of: rootPanel) — replaces onChange(of: mode) side effects (:156-161)
- Entering `.filters`: `loadFilterPreview()` if `filterPreviewImage == nil` (sole load trigger today;
  without this the filter cards render the placeholder gradient forever, :487-495).
- Leaving `.captions` → `showCaptionCustomize = false`; leaving `.filters` → `showFilterAdvanced = false`.
- Caption-sim dashed drag affordance (:1434-1437, currently `mode == .text`):
  → `rootPanel == .captions || selectedPhraseID != nil`.

---

## 2. The one-bar toolbar

Geometry: ONE row, 84pt tall. Fixed 44pt-wide **chevron-down deselect tile** pinned left, OUTSIDE
the scroll (takes id `editorPro.ctx.back`); then `ScrollView(.horizontal)` of icon-over-label tiles
~72pt wide (icon 20pt, label 10pt). Background `Palette.ink`.

**Chevron behavior — topmost layer only:** open expansion → close it; else selection → `select(nil)`;
else rootPanel → nil; at plain root the tile is HIDDEN (column collapses).

**Active root tile** gets the accent tint (parity with :1846).

### Vocabularies

| State | Tiles (order) | Notes / ids |
|---|---|---|
| ROOT | Edit · Sound · Text · Captions · Clean up · Effects · Filters | NEW ids `editorPro.root.*` — deliberately NOT reusing `editorPro.mode.*` so stale flows fail loudly. `Clean up` keeps `editorPro.cleanup`. |
| CLIP | Split · Speed · Volume · Mute · Delete · Move ◀ · Move ▶ | keep ids `ctx.split`, `ctx.speed`, `ctx.delete`, `moveLeft`, `moveRight`, `muteToggle`; Volume tile = NEW `editorPro.ctx.volume`, opens `.clipVolume` expansion (slider 0.0–2.0, draft `clipVolDraft`) |
| MUSIC | Replace · Volume · Delete | NEW `editorPro.music.replace/.volume/.delete`. Replace → music sheet. Volume → `.musicVolume` expansion (0.0–0.5, draft `musicVolDraft`). Delete → `set_music enabled:false` + `selectedMusic = false`. |
| PHRASE | Edit · Edit all · Style · Customize · Remove fillers | NEW `editorPro.phrase.*`. Edit → `beginPhraseEdit(resolvedPhrase)` (existing `editingPhrase` dialog, +Actions:283). Edit all → captionListPanel. Style → `.captionStyle` expansion. Customize → `.captionCustomize`. Remove fillers → cleanupPanel. |
| TEXT STICKER (`overlay.type == "text_sticker"`) | Edit · Duplicate · Delete | Edit → `beginTypingSticker` (canvas keyboard). Duplicate → `duplicateSticker` (guard is sticker-only, +Actions:422-429). Canvas corner handles STAY (redundant by design, as reference). |
| TEXT CARD (`"text_card"`) | Edit · Delete | Edit → `beginOverlayTextEdit` (dialog; keeps `ctx.editOverlayText`). NO Duplicate (unsupported for cards). |
| PUNCH-IN (`"punch_in"`) | Subtle · Medium · Strong · Shorter · Longer · Delete (+ duration readout) | keeps `ctx.zoomSubtle/Medium/Strong` (editor-tracks.yaml:35-37 depends on zoomStrong). This vocabulary was a HOLE — the deleted contextStrip was the only host of zoom controls (:1743-1757). |
| BOUNDARY | None/Fade/White/Flash chips · Duration tile | chips keep `ctx.transition.*`. Duration → `.transitionDuration` expansion. **FIX while rehosting:** today the slider commits one op PER DRAG TICK (:1718-1723) — convert to the standard draft + `onEditingChanged` pattern, one `set_transition` on release. |
| B-ROLL | Replace · Duplicate · Shorter · Longer · Delete | ids preserved (`ctx.replace` etc. + `ctx.deleteRoll`). Unchanged behavior. |

### Root tile actions
- **Edit**: pause; `select(.seg(clipUnderPlayhead ?? lastClipInPlayOrder))`. The fallback is REQUIRED:
  `clipUnderPlayhead` is nil whenever the playhead parks at output end (half-open interval test
  :955-960 vs end-clamping EditorModel.swift:302-314) — i.e. right after watching the clip, the most
  common moment to start editing. No-op only when the document has no segments.
- **Sound**: `rootPanel = .sound`. Does NOT auto-open the music sheet — this honors the deliberate
  removal of auto-pop (the `openModeDrawer` tombstone comment, :1871). Panel content: `Add sound` /
  `Change sound` chip (id `addSound`) + `Music volume` slider when music exists.
- **Text**: `rootPanel = .text`. Chips: `Add text` (id `addSticker`, keyboard-first `startTextEntry`),
  `Text card` (id `addTextCard`, gated `textCardsSupported`).
- **Captions**: `rootPanel = .captions`. Chips/rows: captions on/off (`captionsToggle`), 10-preset row
  (`capPreset.*`), `Customize` disclosure (`capCustomize` → options row `captionOptions`),
  `Edit captions` (`editCaptions`).
- **Clean up**: opens cleanupPanel (unchanged panel).
- **Effects**: `rootPanel = .effects`. Chips: `Punch-in` (`addPunchIn`, gated `punchInsSupported`),
  `Add b-roll` (`addBroll`). Tile hidden when `!punchInsSupported && !brollSupported`, where
  **`brollSupported = caps?["broll"] ?? true`** — ONE definition, used everywhere. (Today two sites
  contradict: `?? false` at :1865 vs `?? true` at :409; backend always returns broll:true, edl.py:1194,
  and the engine is style-universal, LocalEDLEngine.swift:340-349. `?? true` is correct.)
- **Filters**: `rootPanel = .filters`. Rows: filter cards (74pt), tools row (Theme + Advanced),
  intensity row when a filter is active; Advanced → adjust knobs row. All ids preserved.

Root PANEL content rows render ABOVE the toolbar (like today's drawer rows); selection-vocabulary
EXPANSIONS replace them (below).

---

## 3. Expansion rows

An expansion row **REPLACES the toolbar row in the same 84pt slot** (does not stack above it).
This is both the CapCut pattern (the volume/adjust sheets replace the tool row) and the small-screen
fix: the worst-case stack would leave a ~96pt-wide preview on the iPhone SE (667pt, still supported —
deployment target iOS 17, device family iPhone-only). With replacement, SE preview ≈ 36-39%.

Row layout: `Reset` text-button left · content center · `✓` right (`editorPro.expansion.reset` /
`.confirm`). ✓ closes the expansion (returns to the vocabulary). Sliders keep the UX-4 draft +
commit-one-op-on-release pattern — instant-commit is retained; ✓ commits nothing.

**Reset semantics + guard** (Reset MUST no-op with only a haptic when already at default —
an unconditional op flips `isDirty`, relabels Save→"Render", and burns a ~1-min server re-render
of a byte-identical video; EditorSession.swift:26, ProEditorView.swift:252-255):

| Expansion | Reset target | Emits (only when off-default) |
|---|---|---|
| .speed | 1.0 | `set_segment_speed` |
| .clipVolume | 1.0 | `set_segment_volume` (guard: a covering volumeRange exists with volume ≠ 1.0 — note an unguarded 1.0 op APPENDS a range and permanently un-collapses the voice lane, LocalEDLEngine.swift:194-197) |
| .musicVolume | 0.15 | via existing `setMusicVolume` path — MUST carry the current `url` + `duck_voice`; a bare volume-only `set_music` is silently SKIPPED server-side (edl.py:1874-1896). No-op when music == nil. |
| .transitionDuration | 12 frames (0.4s) | `set_transition` |
| filter intensity | 1.0 | `set_filter` |
| adjust knobs row | all zeros | one `set_adjust` (guard: `!adjust.isNeutral`) |
| .captionStyle / .captionCustomize / text & effects chips / filter cards | — | **✓-only header, no Reset** (pickers with visible active state) |

---

## 4. Timeline changes (EditorTimeline.swift + EditorTracks.swift)

### API
- `selectedSeg`/`selectedOverlay`: `@Binding` → plain `let` + `onTapClip(Int)`/`onTapOverlay(Int)`/
  `onTapBackground()` callbacks (choke-point routing, §1).
- ADD `selectedMusic: Bool = false`, `selectedPhraseID: Int? = nil` (plain let, boundary/broll pattern).
- `onTapMusic` SPLIT (today one closure serves both the strip AND the dashed add-strip, :443-452):
  `onTapMusic` (MusicStrip; parent does `select(.music)`) + `onTapAddMusic` (AddLaneStrip; parent
  opens the music sheet, keeps `rootPanel = .sound`).
- `onTapPhrase(CaptionPhrase)`: parent body changes to `select(.phrase(p.id))` (no immediate dialog).
- `onTapVoice(Int)`: parent body → `select(.seg(i)); expansion = .clipVolume(seg: i)` (seeds clipVolDraft).

### Geometry (all sites enumerated; the FOUR mirrors change atomically — lane stack :98-103,
laneGutter :272-281, `timelineHeight` ProEditorView.swift:1582-1597, `showVoiceLane` :1601-1603)

| Element | Now → New | Sites |
|---|---|---|
| Filmstrip | 56 → **64** | ET 206, 210, 274, 299 (+ tile 40×64), 507, 574, 627; PEV 1583. FilmstripCache 120×214 covers 64@3x=192px — no change. |
| Caption lane/strip | 18/16 → **24/22** | ET 406, 414, 410 (y-offset stays (24-22)/2=1); EditorTracks 71 |
| Overlay lane/chip | 20/16 → **22/20** | ET 461, 468, 487, 496 |
| Rolls per-row/strip | 18/16 → **22/20** | ET 332, 342, 345, 348, 353, 361, 378, 393 |
| Voice lane | 16 (unchanged — status readout, not an edit target) | — |
| Music lane/strip | 16 → **28/26** + waveform | ET 443-452; EditorTracks 137, 155 |
| Ruler labels | fixed 3s → adaptive `[1,2,5].first { CGFloat($0) * pps >= 36 } ?? 5` (2s at default pps 18) | ET 159-166 |
| Playhead | accent → **white** 2pt | ET 109 |
| Selected clip border | accent 2.5 → **white** 2.5pt | ET 207-208 |
| Duration badge | bottomTrailing/always → **topLeading / SELECTED clip only**, keep w≥44 guard, `.padding(.leading, 14)` to clear the 11pt trim bracket (occlusion is otherwise 100% since the badge now only shows with brackets) | ET 213-222 |
| Speed badge | topLeading → **topTrailing** (all speed≠1 clips) | ET 224-233 |
| Transition diamonds | y-offset 19 → **23** (re-center for 64pt track) | ET 153 |
| Drag-handle divider | DELETED | PEV 287-293, 1596 |

New `timelineHeight` (music slack fixed to exact 28+2):
```swift
var h: CGFloat = 12 + 2 + 64 + 8
if captionsOn, !phrases.isEmpty { h += 26 }
if !(session?.draft.overlays.isEmpty ?? true) { h += 24 }
if let rolls = session?.draft.broll, !rolls.isEmpty { h += overlapping(rolls) ? 46 : 24 }
else if rootPanel == .effects { h += 24 }
if showVoiceLane { h += 18 }
if session?.draft.music != nil || rootPanel == .sound { h += 30 }
return h + 8
```

### Lane visibility (mode-free)
- Captions: `captionsOn && !phrases.isEmpty` (unchanged).
- Overlays: nonempty (unchanged).
- Rolls: strips when nonempty; dashed add-strip only when `rootPanel == .effects`
  (DROPS today's `.edit` arm — deliberate declutter; the `+` tile on the clip track remains the
  always-available add path).
- Voice: `!volumeRanges.isEmpty || rootPanel == .sound || expansion is .clipVolume`.
- Music: strip whenever music set; dashed add-strip only `rootPanel == .sound && music == nil`.

### Selected-state rendering (both NEW — neither strip has any today, EditorTracks 56-76/121-141)
- `MusicStrip(selected:)` → 2pt **white** strokeBorder (contrast on teal).
- `CaptionClipStrip(selected:)` → 2pt **Palette.accent** strokeBorder (white-on-white would be
  invisible against the 0.88-white fill).

### Music pseudo-waveform
Adapt VoiceStrip's Canvas (EditorTracks 90-106): bar height = pure fn of `(seed + i)` using the same
double-sin jitter, constant base 0.6 (no speechFrames), keep volume scaling + bar geometry (2pt bars,
1.5 gap). **Seed = FNV-1a over the music URL's UTF-8 bytes** — NOT `String.hashValue` (SipHash is
per-launch randomized; the waveform would re-roll every launch) and NOT the display name (unstable
derivation, PEV 1575-1579). Name + volume% text overlay as today.

---

## 5. Green modified-dot

4pt green circle centered under the tile label, derived from the draft (no new state):
- CLIP Speed tile: `speed != 1.0`
- CLIP Volume tile: covering volumeRange with `volume != 1.0`
- MUSIC Volume tile: `music.volume != 0.15`
- ROOT Filters tile: `filter != nil || !adjust.isNeutral || intensity != 1.0`
Same conditions double as the Reset guards (§3).

---

## 6. Copy + misc

- Coach overlay line 3 (:192): "Tap a caption strip on the timeline to fix its words" →
  **"Tap a caption strip, then Edit, to fix its words"** (behavior is now select-first). Same id.
- Behavioral changes accepted and intentional: (a) a selected clip no longer survives root-tile taps
  (today tab-switching preserves selectedSeg, :1837-1840 — replaced by Volume living in the clip
  vocabulary); (b) phrase tap selects instead of opening the dialog; (c) rolls add-strip loses the
  edit-mode arm; (d) Sound does not auto-open the sheet.
- Unaffected and preserved: transitionSimOverlay + splitThreeChrome, placeholder mode, trim system
  (trimContentShift, restorableFrames, preview-follows-trim), magnetic scrub snap, #47 onDisappear
  render survival, Render/Save honest label, marqueInput dialogs root-hosted, theme sheet,
  captionList/media/cleanup panels replacing the lower stack inline.

---

## 7. Probes / Maestro (the actual verification story)

There are NO iOS test targets — Maestro flows are the only UI verification. `scripts/ui_audit.sh`
runs `.maestro/format-audit.yaml` twice (default + XXL type) and WILL FAIL at its `mode.text` tap
the moment the tabs die; `gate.sh --fast`'s python drift guard couples `ui-manifest.json` ↔
`format-audit.yaml` string literals. So the same change that lands §2 must also update:

- **format-audit.yaml + ui-manifest.json** (drift-guard-coupled pair): `mode.text` → `root.captions`,
  `mode.edit` → root (cleanup is a root tile now), `editor-caption-drawer` manifest step retargeted.
- **10 flows referencing dead/moved ids**: editor-pro-flow, editor-open, editor-tracks,
  round6-editor, feature-pack, caption-options, capcut-editor, media-rolls, edit-format-flow,
  editor-edit-logic (+ editor-trim-check/ux2/ux3 use ctx.split which SURVIVES on the new clip tile).
- id policy: `ctx.split/.delete/.speed/.deleteOverlay/.editOverlayText/.zoom*/.transition.*/
  .replace/.duplicate/.shorter/.longer/.back`, `moveLeft/Right`, `cleanup`, all drawer/panel ids
  (`captionsToggle`, `editCaptions`, `capCustomize`, `addSticker`, `addTextCard`, `addSound`,
  `clipVolume`, `muteToggle`, `addPunchIn`, `addBroll`, filter/speed/caption row ids) carry to their
  new hosts UNCHANGED. New surfaces get NEW ids (`root.*`, `music.*`, `phrase.*`, `expansion.*`).
  `editorPro.mode.*` and `timelineDivider` die without replacement.
- Post-change gate: `gate.sh --fast` green, `ios: BUILD SUCCEEDED`, `ui_audit.sh` green at both
  text sizes, plus hand-run of the rewritten editor flows.

---

## 8. Phasing

- **P0** — §0 gate fix + dead code. Independent, zero UI risk.
- **P1** — §1 state machine + §2 one-bar + §3 expansions + §7 flow rewrites (one atomic change;
  the drift guard makes flows non-optional). The bulk of the work; ProEditorView.swift +
  ProEditorView+Actions.swift + EditorTimeline.swift API.
- **P2** — §4 timeline geometry + waveform + selected-state rendering (four mirror sites atomically).
- **P3** — §5 dots + §6 copy + Reset guards polish.

Each phase leaves the build green and the gate passing. Nothing deploys without explicit say-so.
