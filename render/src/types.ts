// The render plan (built by backend build_render_plan). Distinct from the editorial
// EDL: `clips` are SOURCE-video frame ranges to keep (fed to OffthreadVideo), while
// `captions`/`overlays`/`broll` are already remapped to OUTPUT-timeline coords, so a
// composition just renders them at the current output frame. `total_frames` is the
// output duration (the composition's durationInFrames, set via calculateMetadata).
export interface Clip { src_in: number; src_out: number; }
export interface CaptionWord { word: string; frame: number; }
export interface Overlay { type: string; frame_in: number; frame_out: number; scale: number; text: string; }
export interface BRoll { frame_in: number; frame_out: number; cue_text: string; asset_id?: string; broll_query?: string; }
export interface Layout { style: string; panels: number; panel_boundaries: number[]; }

export type CaptionStyle = "clean" | "bold-word" | "karaoke";

export interface RenderPlan {
  style: string;
  format_id: string;
  clips: Clip[];
  captions: CaptionWord[];
  overlays: Overlay[];
  broll: BRoll[];
  layout: Layout;
  caption_style: CaptionStyle;
  total_frames: number;
}

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
