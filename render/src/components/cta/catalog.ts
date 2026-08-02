// Pure-data view of the CTA catalog: no JSX, no component imports, so tests and any
// non-React consumer can import it (the test tsconfig doesn't enable --jsx).
// registry.tsx maps these same ids to components and is asserted against this list.
import catalogJson from "./cta_styles.json";

export type CtaLayout = "tail_card" | "overlay";

export interface CtaStyleMeta {
  id: string;
  label: string;
  blurb: string;
  layout_class: CtaLayout;
  ui_class: string;
  cluster: "minimal" | "energetic";
  params: string[];
  default_frames: number;
}

export const CTA_STYLES: CtaStyleMeta[] = (catalogJson as any).styles;

export const CTA_LAYOUTS: Record<string, CtaLayout> = Object.fromEntries(
  CTA_STYLES.map((s) => [s.id, s.layout_class]),
);

export const DEFAULT_TAIL_STYLE = "classic";
export const DEFAULT_OVERLAY_STYLE = "pill";

export const layoutFor = (styleId?: string): CtaLayout =>
  (styleId && CTA_LAYOUTS[styleId]) || "tail_card";
