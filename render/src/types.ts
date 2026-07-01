export interface Segment { src_in: number; src_out: number; }
export interface Drop { src_in: number; src_out: number; reason: string; }
export interface CaptionWord { word: string; frame: number; }
export interface Overlay { type: string; src_in: number; src_out: number; scale: number; text: string; }
export interface BRoll { src_in: number; src_out: number; cue_text: string; asset_id?: string; broll_query?: string; }
export interface Layout { style: string; panels: number; panel_boundaries: number[]; }
export interface EDL {
  style: string; format_id: string;
  segments: Segment[]; drops: Drop[]; captions: CaptionWord[];
  overlays: Overlay[]; broll: BRoll[]; layout: Layout;
  audio: { lufs_target: number };
}
export interface CompositionProps { sourceUrl: string; edl: EDL | null; formatId: string; }
