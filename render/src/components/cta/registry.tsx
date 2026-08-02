import React from "react";
import { CtaTemplateProps } from "./tokens";
import { CtaLayout, layoutFor, DEFAULT_OVERLAY_STYLE, DEFAULT_TAIL_STYLE } from "./catalog";
import { ClassicCard } from "./ClassicCard";
import { PaperPress, Knockout, FollowMimic, CardFlip, Credits } from "./TailCards";
import { Pill, BarSweep, SerifLine, CornerTag, ProgressFollow, PartTwo, NeonPulse }
  from "./OverlayBarsPills";
import { HandleReveal, Typewriter, BlurIn, ScalePop, UnderlineSweep, ArrowNudge, Glitch }
  from "./OverlayText";

export interface CtaEntry {
  layout: CtaLayout;
  Comp: React.FC<CtaTemplateProps>;
}

// The single dispatch table. Every id here MUST exist in cta_styles.json and vice-versa
// (asserted by src/__tests__/cta_registry.test.ts) — that JSON is what the backend and
// the iOS picker read, so a drift between them would ship a style users can pick but
// nothing can render.
export const CTA_REGISTRY: Record<string, CtaEntry> = {
  // tail cards — play AFTER the last clip; build_render_plan extends total_frames
  classic:      { layout: "tail_card", Comp: ClassicCard },
  paper_press:  { layout: "tail_card", Comp: PaperPress },
  knockout:     { layout: "tail_card", Comp: Knockout },
  follow_mimic: { layout: "tail_card", Comp: FollowMimic },
  card_flip:    { layout: "tail_card", Comp: CardFlip },
  credits:      { layout: "tail_card", Comp: Credits },
  // overlays — ride OVER the final seconds of live video; no tail extension
  pill:             { layout: "overlay", Comp: Pill },
  handle_reveal:    { layout: "overlay", Comp: HandleReveal },
  bar_sweep:        { layout: "overlay", Comp: BarSweep },
  serif_line:       { layout: "overlay", Comp: SerifLine },
  typewriter:       { layout: "overlay", Comp: Typewriter },
  blur_in:          { layout: "overlay", Comp: BlurIn },
  scale_pop:        { layout: "overlay", Comp: ScalePop },
  underline_sweep:  { layout: "overlay", Comp: UnderlineSweep },
  corner_tag:       { layout: "overlay", Comp: CornerTag },
  progress_follow:  { layout: "overlay", Comp: ProgressFollow },
  part_two:         { layout: "overlay", Comp: PartTwo },
  arrow_nudge:      { layout: "overlay", Comp: ArrowNudge },
  neon_pulse:       { layout: "overlay", Comp: NeonPulse },
  glitch:           { layout: "overlay", Comp: Glitch },
};

/**
 * Resolve a plan's style to a renderable entry. An unknown id (a backend newer than this
 * bundle) degrades to the safe template for its mount rather than crashing the render:
 * an overlay falls back to `pill`, anything else to `classic`.
 */
export const resolveCta = (styleId?: string, mount?: string): CtaEntry => {
  const hit = styleId ? CTA_REGISTRY[styleId] : undefined;
  if (hit) return hit;
  return mount === "overlay" ? CTA_REGISTRY.pill : CTA_REGISTRY.classic;
};
