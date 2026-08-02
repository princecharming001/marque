// Shared design tokens for the CTA template library.
//
// Guardrails these encode (2026 short-form motion research + the winners study):
//  • Entrances land in 9-15 frames (0.3-0.5s). Slower reads sluggish, faster reads glitchy.
//  • Everything must read with sound OFF — no template depends on audio.
//  • Overlay templates sit ABOVE the platform chrome (bottom 320px is TikTok's dead zone)
//    and clear of the caption band, which is still live during the CTA window.
//  • The palette is the app's ink/cream. The `energetic` cluster may use NEON, nothing else.
import { FONTS } from "../Captions";

export const INK = "#141310";
export const CREAM = "#F6F1E7";
export const CREAM_DIM = "rgba(246,241,231,0.72)";
export const PLATE = "rgba(8,8,12,0.94)";      // the classic card's fill — keep byte-identical
export const NEON = "#CFF56A";

export { FONTS };

// Material 3 "emphasized decelerate" — the token for content entering the frame.
export const EASE_M3 = [0.05, 0.7, 0.1, 1.0] as const;

export const SPRING_SMOOTH = { damping: 200 };                          // no bounce
export const SPRING_POP = { damping: 14, stiffness: 120, mass: 1 };      // ~3% overshoot

export const ENTER_FRAMES = 12;    // canonical entrance length (0.4s @30fps)
export const EXIT_FRAMES = 8;

// Overlay geometry. 1080x1920 canvas; SAFE_BOTTOM_PX=320 is platform chrome, and the
// caption band centers around y=0.62 — overlays live BETWEEN the two, or high above.
export const CANVAS_W = 1080;
export const CANVAS_H = 1920;
export const SAFE_BOTTOM_PX = 320;
export const CAPTION_BAND_TOP = 0.555 * CANVAS_H;   // ~1066px — do not cross downward
export const CAPTION_BAND_BOTTOM = 0.70 * CANVAS_H; // ~1344px

// The clean strip for a lower-third CTA: below the caption band, above platform chrome.
export const OVERLAY_Y = CAPTION_BAND_BOTTOM + 40;              // ~1384px
export const OVERLAY_MAX_Y = CANVAS_H - SAFE_BOTTOM_PX - 40;    // ~1560px

/** Clamp an overlay's top edge into the clean strip (never over captions/chrome). */
export const clampOverlayY = (y: number, height: number): number =>
  Math.max(CAPTION_BAND_TOP + 24, Math.min(y, OVERLAY_MAX_Y - height));

export interface CtaTemplateProps {
  text: string;
  handle: string;
  logoUrl: string | null;
  showHandle: boolean;
  /** Frames in THIS template's window (Sequence-local: frame 0 = template start). */
  durationInFrames: number;
  /** True when this CTA's window ends WITH the video. build_render_plan mounts every
   *  overlay CTA flush to the end (start = total - frames), so there is no live video
   *  left to exit into — the exit would burn the final frames of the reel (and the
   *  frame the platform shows when it loops). Templates hold instead. */
  runsToEnd?: boolean;
}
