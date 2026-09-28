/**
 * Motion vocabulary shared by every element (doctrine: transitions-and-graphics.md, captions-and-text.md).
 *
 * - Entrances decelerate, exits accelerate and are shorter ("slow in, fast out").
 * - Enter once, then hold still: no loops, no shake, no bounce on text.
 * - Durations are specified in seconds and converted with the composition fps, so 60 fps sources get
 *   the same timing in twice the frames.
 */
import {Easing, interpolate} from 'remotion';

/** cubic-bezier(0, 0, 0.3, 1): standard decelerate for entrances. */
export const EASE_ENTER = Easing.bezier(0, 0, 0.3, 1);
/** Material "emphasized decelerate" (0.05, 0.7, 0.1, 1): larger elements, cards. */
export const EASE_EMPHASIZED = Easing.bezier(0.05, 0.7, 0.1, 1);
/** Accelerating exit. */
export const EASE_EXIT = Easing.bezier(0.4, 0.14, 1, 1);
/** Symmetric move (draw-ons, counters). */
export const EASE_IN_OUT = Easing.bezier(0.45, 0, 0.2, 1);

export const secToFrames = (sec: number, fps: number, min = 1): number => Math.max(min, Math.round(sec * fps));

const clampOpts = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** 0 -> 1 progress over `frames` starting at `from` with an easing. */
export const progress = (frame: number, from: number, frames: number, easing = EASE_ENTER): number => {
  if (frames <= 0) return frame >= from ? 1 : 0;
  return interpolate(frame, [from, from + frames], [0, 1], {...clampOpts, easing});
};

/** 1 -> 0 over the last `frames` frames before `end` (exclusive), accelerating. */
export const exitProgress = (frame: number, end: number, frames: number): number => {
  if (frames <= 0) return 1;
  return interpolate(frame, [end - frames, end], [1, 0], {...clampOpts, easing: EASE_EXIT});
};

export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
