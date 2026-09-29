/**
 * Props schema for the "Overlay" composition. It mirrors the Python side exactly
 * (studio/compile/overlays.py: OverlayProps and friends, serialized with camelCase aliases).
 *
 * Conventions
 * - All times are integer output frames on the timeline's frame grid: `start` inclusive, `end` exclusive.
 * - Pixel values are at the composition size (normally 1080x1920). Normalized values are 0..1 of the
 *   output frame (top-left origin).
 * - `safe` is the text safe zone: margins in px kept free of text, measured from each edge.
 *
 * This file must stay plain erasable TypeScript (no enums/namespaces) because
 * scripts/validate-props.mts imports it directly with Node's type stripping.
 */
import {z} from 'zod';

const frame = z.number().int().min(0);
const colour = z.string().min(1).max(200);
const norm = z.number().min(0).max(1);

export const safeZoneSchema = z.object({
  top: z.number().min(0),
  bottom: z.number().min(0),
  left: z.number().min(0),
  right: z.number().min(0),
});

export const captionLayoutSchema = z.object({
  /** Preferred horizontal centre of caption lines (px). The block is shifted to stay inside [leftPx, rightPx]. */
  xCenterPx: z.number(),
  /** Widest a caption line may be (px); longer pages shrink (down to minScale) and/or wrap. */
  maxWidthPx: z.number().positive(),
  leftPx: z.number().min(0),
  rightPx: z.number().positive(),
  /** Smallest font scale the fitter may use before wrapping/overflowing. */
  minScale: z.number().min(0.3).max(1),
  /** Legibility floor (px at the output size): a caption never shrinks below it while wrapping can help. */
  minSizePx: z.number().min(0).default(0),
  /** A page too wide for its style's lines at the floor may wrap to this many lines instead of shrinking. */
  overflowLines: z.number().int().min(1).max(3).default(1),
  /** Vertical band a caption block must stay inside (px): the safe top and the (relaxed) caption floor. */
  topPx: z.number().min(0).default(0),
  bottomPx: z.number().min(0).default(0),
});

export const captionStyleSchema = z.object({
  font: z.string().min(1),
  weight: z.number().int().min(100).max(1000),
  sizePx: z.number().positive(),
  strokePx: z.number().min(0),
  strokeColor: colour,
  color: colour,
  highlightColor: colour,
  background: colour.nullable(),
  animation: z.enum(['none', 'pop', 'karaoke', 'fade', 'slide']),
  shadow: z.boolean(),
  highlightLeadFrames: z.number().int().min(0).max(12),
  maxLines: z.number().int().min(1).max(3),
  letterSpacingEm: z.number().min(-0.2).max(0.5),
  lineHeight: z.number().min(0.8).max(2),
});

export const captionWordSchema = z.object({
  text: z.string(),
  start: frame,
  end: frame,
  emphasis: z.boolean(),
});

export const captionPageSchema = z.object({
  id: z.string(),
  start: frame,
  end: frame,
  words: z.array(captionWordSchema).min(1),
  /** Vertical centre of the caption block, normalized. */
  yNorm: norm,
  style: captionStyleSchema,
  /** Line count the placer planned the block for (the renderer keeps it, shrinking as needed, so the
   * block it draws is the block that was placed); absent = fit freely. */
  lines: z.number().int().min(1).max(3).optional(),
  /** Planned line breaks (word index where each line after the first starts), chosen in Python with the
   * syntax-aware splitter (no "the / budget"); when present the renderer keeps them and only fits the size. */
  breaks: z.array(z.number().int().min(1)).nullable().optional(),
});

export const textStyleSchema = z.object({
  font: z.string().min(1),
  weight: z.number().int().min(100).max(1000),
  sizePx: z.number().positive(),
  color: colour,
  background: colour.nullable(),
  strokePx: z.number().min(0),
  strokeColor: colour,
  align: z.enum(['left', 'center', 'right']),
});

export const textKindSchema = z.enum(['hook_title', 'callout', 'list', 'lower_third', 'label', 'cta']);

export const textOverlaySchema = z.object({
  id: z.string(),
  kind: textKindSchema,
  text: z.string(),
  items: z.array(z.string()),
  /** Per-item reveal frame (absolute) for lists; null = staggered after the title. */
  itemStarts: z.array(frame.nullable()),
  start: frame,
  end: frame,
  /** Anchor point (centre of the block), normalized; the block is clamped into the safe zone. */
  xNorm: norm,
  yNorm: norm,
  style: textStyleSchema,
  accent: colour,
  animation: z.enum(['none', 'pop', 'fade', 'slide', 'typewriter']),
  maxWidthPx: z.number().positive(),
});

export const transitionSchema = z.object({
  kind: z.enum(['cut', 'fade', 'dissolve', 'slide', 'zoom', 'whip']),
  frames: z.number().int().min(0),
});

export const cardTemplateSchema = z.enum(['title', 'number', 'stat', 'list', 'quote', 'steps', 'comparison']);

export const cardSchema = z.object({
  id: z.string(),
  template: cardTemplateSchema,
  title: z.string(),
  subtitle: z.string(),
  body: z.string(),
  items: z.array(z.string()),
  number: z.string().nullable(),
  unit: z.string().nullable(),
  accent: colour,
  /** CSS colour/gradient; "transparent" draws only the content (e.g. over a blurred A-roll underlay). */
  background: colour,
  start: frame,
  end: frame,
  /** Normalized [x, y, w, h] for a card inside a split/pip region; null = full frame. */
  rect: z.tuple([norm, norm, norm, norm]).nullable(),
  transitionIn: transitionSchema,
  transitionOut: transitionSchema,
  font: z.string().min(1),
  displayFont: z.string().min(1),
  /** Pixels kept free at the bottom of the card's content area (caption band) while captions run. */
  reserveBottomPx: z.number().min(0),
});

export const pointSchema = z.object({x: norm, y: norm});

export const graphicSchema = z.object({
  id: z.string(),
  kind: z.enum(['arrow', 'circle', 'box', 'underline']),
  start: frame,
  end: frame,
  /** Target box (circle/box/underline), normalized. */
  x: norm,
  y: norm,
  w: norm,
  h: norm,
  /** Arrow endpoints (tail -> head), normalized; required for arrows. */
  from: pointSchema.nullable(),
  to: pointSchema.nullable(),
  color: colour,
  strokePx: z.number().positive(),
  /** Arrow bend (-1..1, fraction of length); circles use it as a hand-drawn wobble. */
  curvature: z.number().min(-1).max(1),
  drawFrames: z.number().int().min(1),
});

export const themeSchema = z.object({
  accent: colour,
  font: z.string().min(1),
  displayFont: z.string().min(1),
  cardBackground: colour,
  textColor: colour,
});

export const overlayPropsSchema = z.object({
  version: z.literal(1),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  fps: z.number().positive(),
  durationInFrames: z.number().int().positive(),
  safe: safeZoneSchema,
  captionLayout: captionLayoutSchema,
  theme: themeSchema,
  captions: z.array(captionPageSchema),
  texts: z.array(textOverlaySchema),
  cards: z.array(cardSchema),
  graphics: z.array(graphicSchema),
  /** Draws the safe zone and element boxes (review renders only). */
  debug: z.boolean(),
});

export type SafeZone = z.infer<typeof safeZoneSchema>;
export type CaptionLayout = z.infer<typeof captionLayoutSchema>;
export type CaptionStyle = z.infer<typeof captionStyleSchema>;
export type CaptionWord = z.infer<typeof captionWordSchema>;
export type CaptionPageProps = z.infer<typeof captionPageSchema>;
export type TextStyle = z.infer<typeof textStyleSchema>;
export type TextOverlayProps = z.infer<typeof textOverlaySchema>;
export type Transition = z.infer<typeof transitionSchema>;
export type CardProps = z.infer<typeof cardSchema>;
export type GraphicProps = z.infer<typeof graphicSchema>;
export type Theme = z.infer<typeof themeSchema>;
export type OverlayProps = z.infer<typeof overlayPropsSchema>;
