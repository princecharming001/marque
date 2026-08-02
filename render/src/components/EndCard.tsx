import React from "react";
import { Sequence } from "remotion";
import { EndCardPlan } from "../types";
import { resolveCta } from "./cta/registry";

// v8: the CTA layer. This used to BE the end card (one hardcoded staggered-spring build);
// that layout now lives in cta/ClassicCard.tsx as template id "classic", and this file is
// the dispatcher over the template registry.
//
// Two mount modes, both driven by the same `end_card` carrier on the plan:
//   tail    — the card plays AFTER the last clip (build_render_plan extended total_frames
//             by `frames`, so start_frame sits at the old end of the video).
//   overlay — the CTA rides OVER the final seconds of live video; the plan set
//             start_frame = total_frames - frames and did NOT extend the timeline.
// The component signature and every composition's `<EndCard endCard={edl.end_card} />`
// mount are unchanged, so no composition needed editing for any of this.
export const EndCard: React.FC<{ endCard: EndCardPlan | null | undefined }> = ({ endCard }) => {
  if (!endCard) return null;
  const text = (endCard.text || "").trim();
  const handle = (endCard.handle || "").trim();
  // Nothing to say and no handle to show => render nothing rather than an empty plate.
  if (!text && !handle) return null;

  const { Comp } = resolveCta(endCard.style_id, endCard.mount);
  return (
    <Sequence from={endCard.start_frame} durationInFrames={endCard.frames} layout="none">
      <Comp
        text={text}
        handle={handle}
        logoUrl={endCard.logo_url ?? null}
        showHandle={endCard.show_handle}
        durationInFrames={endCard.frames}
      />
    </Sequence>
  );
};
