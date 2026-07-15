// The render plan (built by backend build_render_plan). Distinct from the editorial
// EDL: `clips` are SOURCE-video frame ranges to keep (fed to OffthreadVideo), while
// `captions`/`overlays`/`broll` are already remapped to OUTPUT-timeline coords, so a
// composition just renders them at the current output frame. `total_frames` is the
// output duration (the composition's durationInFrames, set via calculateMetadata).
export interface Clip {
  src_in: number; src_out: number; speed: number;
  // Canvas transform (pinch-zoom / drag-reposition of the clip itself)
  tx_scale: number; tx_x: number; tx_y: number;
}
export interface CaptionWord { word: string; frame: number; end_frame?: number; }
export interface Overlay {
  type: string; frame_in: number; frame_out: number; scale: number; text: string;
  // text_sticker placement + look (fractions of frame size; ignored by other types)
  pos_x: number; pos_y: number; rotation: number;
  color: string | null; bg: string; font: string;
}

// A boundary dip where one clip hands off to the next (output coords, centered).
export interface TransitionPlan { at_frame: number; style: string; frames: number; }

// Whole-video color treatment: named preset blended by intensity + manual knobs.
export interface Adjust {
  brightness: number; contrast: number; saturation: number;
  temperature: number; vignette: number;
}
// A8 (schema v4): grain is additive/defaulted (0) — a stale v3 plan renders identically.
export interface Look { filter: string | null; intensity: number; adjust: Adjust; grain?: number; }
// resolved_url is filled by the backend Pexels-resolve step; frame_in/out are output coords.
export interface BRoll { frame_in: number; frame_out: number; cue_text: string; asset_id?: string; broll_query?: string; source?: string; resolved_url?: string; }
export interface Layout { style: string; panels: number; panel_boundaries: number[]; split_fraction?: number; }

// duet_split — the reacted-to clip and its top-panel play/freeze/duck schedule (output coords).
export interface ReactSource { resolved_url?: string; kind?: string; credit_label?: string; }
export interface ReactWindow { state: string; frame_in: number; frame_out: number; clip_from: number; audio_gain: number; }

export type CaptionStyle = "clean" | "bold-word" | "karaoke";

// Caption tuning knobs, composable under the style preset (backend CaptionOptions).
// `accent: null` = the style's own default color. Always fully populated by
// build_render_plan — no undefined keys reach the composition.
export interface CaptionOptions {
  position: "top" | "middle" | "bottom";
  size: "small" | "medium" | "large";
  // Continuous overrides from canvas drag/pinch (TikTok model); null = use the words.
  pos_y: number | null;
  scale: number | null;
  accent: string | null;
  uppercase: boolean;
  font: "inter" | "archivo" | "baloo" | "montserrat" | "anton";
  // word = one word at a time; phrase = ~3-word chunks; line = sliding window (legacy look)
  grouping: "word" | "phrase" | "line";
  // Normalized lowercase words rendered in the accent color (CapCut keyword highlight).
  highlight_words?: string[];
  // A2 (superintelligence epic, schema v3): all additive/defaulted (0/undefined),
  // so a plan from a stale backend renders byte-identical.
  stroke_px?: number;                  // outlined-caption look (Hormozi/Submagic); dual-span, see Captions.tsx
  sync_lead_frames?: number;           // words appear this many frames BEFORE their spoken start (doctrine: 100-200ms early)
  highlight_persist_frames?: number;   // karaoke: extend a word's "filled" state this many frames past its end
}

// Audio plan (output coords for volume_ranges; music plays across the whole output).
export interface MusicTrack { url?: string | null; query?: string | null; volume: number; duck_voice: boolean; }
export interface VolumeRange { frame_in: number; frame_out: number; volume: number; }
// speech_frames: word-start output frames for the ducking heuristic, independent
// of whether captions are visually enabled (G3) — always present when the
// transcript has words, even with captions toggled off.
//
// lufs_target (G4, deliberately deferred): carried through the contract for
// future loudness-normalization work, but nothing in the compositions reads it
// today — real LUFS normalization needs an ffmpeg loudnorm pass or equivalent,
// which doesn't exist in this render bridge yet. Not a bug; documented.
// P4: one deterministically-authored SFX one-shot, already resolved to an
// output frame + hosted URL by build_render_plan (a cue whose kind had no
// configured asset, or whose anchor frame got cut, never reaches this list).
export interface SfxCue { frame: number; kind: string; gain: number; url: string; }
// A5a (schema v3): optional duck-curve override, read with AudioMix.tsx's own
// constants as the fallback for any missing field — an absent `duck` (every
// pre-v3 plan) behaves byte-identically to today.
export interface DuckParams { factor?: number; window_f?: number; ramp_f?: number; }
export interface AudioPlan { lufs_target: number; gain?: number; music?: MusicTrack | null; volume_ranges: VolumeRange[]; speech_frames: number[]; sfx: SfxCue[]; duck?: DuckParams | null; }

// P4: a tail-of-video CTA card. Tail-anchored (not a source-coord remap) —
// start_frame is where the last kept clip ends, and total_frames on the plan
// already includes its `frames` (build_render_plan extends it there).
export interface EndCardPlan { text: string; start_frame: number; frames: number; show_handle: boolean; }

export interface RenderPlan {
  style: string;
  format_id: string;
  clips: Clip[];
  captions: CaptionWord[];
  overlays: Overlay[];
  broll: BRoll[];
  react_source?: ReactSource | null;
  react_schedule?: ReactWindow[];
  layout: Layout;
  caption_style: CaptionStyle;
  caption_options?: CaptionOptions | null;
  transitions?: TransitionPlan[];
  look?: Look | null;
  audio?: AudioPlan | null;
  end_card?: EndCardPlan | null;   // P4
  progress_bar?: boolean;          // P4
  total_frames: number;
  // #19: backend build_render_plan's contract version — compared against
  // PLAN_SCHEMA_VERSION below to detect backend/site deploy skew.
  schema_version?: number;
}

// #19: keep in lockstep with backend edl.py PLAN_SCHEMA_VERSION. Bump BOTH when the
// render-plan shape changes so a half-deploy (backend updated, Remotion site stale)
// surfaces as a logged warning instead of a silently-wrong render.
// v2 (P4): added end_card, progress_bar, audio.sfx.
// v3 (A2/A5a, superintelligence epic): added caption_options.stroke_px/
// sync_lead_frames/highlight_persist_frames, audio.duck, montserrat/anton fonts.
// v4 (A8, superintelligence epic): added look.grain, whip/zoom_punch transitions,
// the "finishing" filter preset.
export const PLAN_SCHEMA_VERSION = 4;

let _schemaWarned = false;
// Warn ONCE in the Lambda logs on a plan/bundle version mismatch. Never throws — a
// skew degrades to an observable warning, not a broken user render.
export const checkPlanSchema = (plan: RenderPlan | null | undefined): void => {
  if (!plan || _schemaWarned) return;
  const got = plan.schema_version;
  if (got !== undefined && got !== PLAN_SCHEMA_VERSION) {
    _schemaWarned = true;
    // eslint-disable-next-line no-console
    console.warn(
      `[marque] render plan schema_version ${got} != bundle ${PLAN_SCHEMA_VERSION} — ` +
        `backend and Remotion site are out of sync; redeploy the site ` +
        `(npx remotion lambda sites create).`,
    );
  }
};

// Python's round(): banker's rounding, half-to-EVEN. build_render_plan computes
// every out_cursor / caption / overlay output coordinate with it, and the
// compositions recompute clip durations locally — the two sides MUST round
// identically or clip boundaries drift one frame at exact halves (e.g. a
// speed-2.0 clip with an odd kept length: Python round(12.5)=12, JS
// Math.round(12.5)=13), desyncing captions/overlays and truncating the tail.
export const pyRound = (x: number): number => {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
};

// Source-audio volume at an output frame (default 1.0 outside every range).
export const volumeAt = (frame: number, ranges: VolumeRange[] | undefined | null): number => {
  if (!ranges) return 1.0;
  for (const r of ranges) {
    if (frame >= r.frame_in && frame < r.frame_out) return r.volume;
  }
  return 1.0;
};

// A `type` alias (not `interface`) so it satisfies Remotion's Record<string, unknown>
// props constraint — interfaces lack the implicit index signature and are rejected.
export type CompositionProps = {
  sourceUrl: string;
  edl: RenderPlan | null;
  formatId: string;
};

// Duration resolver for calculateMetadata — falls back to 720 when no plan is passed
// (e.g. the Remotion Studio default-props preview). Also the once-per-render chokepoint
// where the plan/bundle schema version is checked (#19).
export const planDuration = (props: CompositionProps): number => {
  checkPlanSchema(props.edl);
  return Math.max(1, props.edl?.total_frames ?? 720);
};
