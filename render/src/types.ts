// The render plan (built by backend build_render_plan). Distinct from the editorial
// EDL: `clips` are SOURCE-video frame ranges to keep (fed to OffthreadVideo), while
// `captions`/`overlays`/`broll` are already remapped to OUTPUT-timeline coords, so a
// composition just renders them at the current output frame. `total_frames` is the
// output duration (the composition's durationInFrames, set via calculateMetadata).
export interface Clip { src_in: number; src_out: number; }
export interface CaptionWord { word: string; frame: number; }
export interface Overlay { type: string; frame_in: number; frame_out: number; scale: number; text: string; }
// resolved_url is filled by the backend Pexels-resolve step; frame_in/out are output coords.
export interface BRoll { frame_in: number; frame_out: number; cue_text: string; asset_id?: string; broll_query?: string; source?: string; resolved_url?: string; }
export interface Layout { style: string; panels: number; panel_boundaries: number[]; split_fraction?: number; }

// duet_split — the reacted-to clip and its top-panel play/freeze/duck schedule (output coords).
export interface ReactSource { resolved_url?: string; kind?: string; credit_label?: string; }
export interface ReactWindow { state: string; frame_in: number; frame_out: number; clip_from: number; audio_gain: number; }

export type CaptionStyle = "clean" | "bold-word" | "karaoke";

// Audio plan (output coords for volume_ranges; music plays across the whole output).
export interface MusicTrack { url?: string | null; query?: string | null; volume: number; duck_voice: boolean; }
export interface VolumeRange { frame_in: number; frame_out: number; volume: number; }
export interface AudioPlan { lufs_target: number; music?: MusicTrack | null; volume_ranges: VolumeRange[]; }

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
