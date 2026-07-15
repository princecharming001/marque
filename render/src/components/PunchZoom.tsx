import { useCurrentFrame, interpolate } from "remotion";
import { Overlay } from "../types";

// The punch-in zoom scale at the current frame, given a plan's overlays —
// factored out of TalkingHead (its original, only home) so GreenScreen,
// BrollCutaway, and SplitThree can share the exact same ramp math instead of
// each composition re-implementing (and inevitably drifting from) it.
//
// Ramp over ~8 frames with interpolate — a CSS `transition` is a no-op in a
// frame-by-frame render (nothing to transition FROM), so it would snap the zoom
// instead of easing it. Eases the EXIT too (a hard snap back to 1.0 at
// frame_out reads as a glitch); `r` clamps the ramp so short windows still get
// a symmetric ease without the in/out keyframes colliding.
export function usePunchScale(overlays: Overlay[] | undefined | null): number {
  const frame = useCurrentFrame();
  const punchIn = (overlays ?? []).find(
    (o) => o.type === "punch_in" && frame >= o.frame_in && frame < o.frame_out
  );
  if (!punchIn) return 1.0;
  // Belt on top of the backend's combined-scale clamp: a punch overlay never zooms past
  // 1.20 on its own (spec §6.1 ceiling), so even a malformed EDL scale can't spike.
  const peak = Math.min(1.2, Math.max(1.0, punchIn.scale));
  const w = punchIn.frame_out - punchIn.frame_in;
  const r = Math.min(8, w / 2);
  // A window <=16 frames makes r land exactly on w/2, so frame_in+r === frame_out-r
  // (an algebraic identity, not an edge case) — interpolate() requires a strictly
  // increasing inputRange and throws on the duplicate keyframe. Below that width
  // there's no room for a plateau anyway; ease straight to a single peak instead.
  if (r * 2 >= w) {
    const mid = (punchIn.frame_in + punchIn.frame_out) / 2;
    return interpolate(
      frame,
      [punchIn.frame_in, mid, punchIn.frame_out],
      [1, peak, 1],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );
  }
  return interpolate(
    frame,
    [punchIn.frame_in, punchIn.frame_in + r, punchIn.frame_out - r, punchIn.frame_out],
    [1, peak, peak, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
}
