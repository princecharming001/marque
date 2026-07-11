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
export interface CaptionWord { word: string; frame: number; }
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
export interface Look { filter: string | null; intensity: number; adjust: Adjust; }
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
  font: "inter" | "archivo" | "baloo";
  // word = one word at a time; phrase = ~3-word chunks; line = sliding window (legacy look)
  grouping: "word" | "phrase" | "line";
  // Normalized lowercase words rendered in the accent color (CapCut keyword highlight).
  highlight_words?: string[];
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
export interface AudioPlan { lufs_target: number; gain?: number; music?: MusicTrack | null; volume_ranges: VolumeRange[]; speech_frames: number[]; }

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
  total_frames: number;
}

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
// (e.g. the Remotion Studio default-props preview).
export const planDuration = (props: CompositionProps): number =>
  Math.max(1, props.edl?.total_frames ?? 720);
