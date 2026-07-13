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
  const r = Math.min(8, (punchIn.frame_out - punchIn.frame_in) / 2);
  return interpolate(
    frame,
    [punchIn.frame_in, punchIn.frame_in + r, punchIn.frame_out - r, punchIn.frame_out],
    [1, punchIn.scale, punchIn.scale, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
}
