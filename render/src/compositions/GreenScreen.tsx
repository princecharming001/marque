import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { CutVideo } from "../components/CutVideo";
import { AudioMix } from "../components/AudioMix";
import { TextStickers } from "../components/TextStickers";
import { BrollLayer } from "../components/BrollLayer";
import { Grade } from "../components/Grade";
import { Captions, FONTS } from "../components/Captions";
import { CompositionProps } from "../types";
import { LAYOUT, cardFit } from "../layout";

// Reaction / green-screen layout: a reference text card in the top of the backdrop, with
// the cut speaker track in a rounded card filling the bottom ~55% of the frame.
//
// P0.5: the old layout keyed the speaker over the right side with mixBlendMode:"multiply"
// — a fake chroma-key that just darkened the speaker into the backdrop (muddy, no real
// cutout). Replaced with a clean picture-in-picture card: no blend modes, speaker
// object-fit covered into a rounded, shadowed card. (True segmentation keying is a later
// option; this reads far better in the meantime.) The text card shows only during its
// overlay window (output coords); with no text_card overlay it shows throughout.
export const GreenScreen: React.FC<CompositionProps> = ({ sourceUrl, edl }) => {
  const frame = useCurrentFrame();
  const textCards = (edl?.overlays ?? []).filter((o) => o.type === "text_card");
  // Only a REAL text card renders. The old fallback burned a literal
  // "Reference post" placeholder into the delivered video whenever the EDL
  // carried no text_card overlay — a clean backdrop beats fake copy.
  const activeCard = textCards.find((o) => frame >= o.frame_in && frame < o.frame_out);
  // Formatting fix #9: shrink-to-fit + a hard line-clamp stop so a long reference
  // caption can't overflow the 45% band into the speaker card below it.
  const cardUsablePx = LAYOUT.FRAME_W * 0.84; // ~92% width minus internal padding
  const cardFontFit = activeCard
    ? cardFit(activeCard.text, 40, cardUsablePx, LAYOUT.CARD_MAX_LINES, LAYOUT.CARD_MIN_FONT)
    : null;

  return (
    <AbsoluteFill style={{ background: "#0f3460" }}>
      {/* Reference backdrop: text card centered in the top ~45% (clear of the speaker card). */}
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "45%",
        display: "flex", alignItems: "center", justifyContent: "center", padding: "0 8%" }}>
        {activeCard && cardFontFit && (
          <div style={{
            background: "white", borderRadius: 20, padding: "28px 40px",
            maxWidth: "92%", fontSize: cardFontFit.fontSize, color: "#111", fontFamily: FONTS.inter,
            fontWeight: 700, textAlign: "center", lineHeight: 1.25,
            boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
            display: "-webkit-box", WebkitLineClamp: LAYOUT.CARD_MAX_LINES,
            WebkitBoxOrient: "vertical", overflow: "hidden",
          }}>{activeCard.text}</div>
        )}
      </div>
      {/* Speaker in a rounded, shadowed card filling the bottom 55% — no blend modes. */}
      <div style={{ position: "absolute", left: "4%", right: "4%", bottom: "3%", height: "54%",
        borderRadius: 28, overflow: "hidden", boxShadow: "0 16px 50px rgba(0,0,0,0.5)",
        border: "3px solid rgba(255,255,255,0.9)" }}>
        <CutVideo sourceUrl={sourceUrl} clips={edl?.clips ?? []} volumeRanges={edl?.audio?.volume_ranges} look={edl?.look} gain={edl?.audio?.gain} />
      </div>
      {edl && <BrollLayer broll={edl.broll} />}
      {edl && <Captions captions={edl.captions} style={edl.caption_style} options={edl.caption_options} />}
      {edl && <TextStickers overlays={edl.overlays} />}
      {edl && <Grade look={edl.look} transitions={edl.transitions} />}
      <AudioMix audio={edl?.audio} />
    </AbsoluteFill>
  );
};
