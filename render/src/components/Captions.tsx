import React from "react";
import { useCurrentFrame } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { CaptionWord, CaptionStyle } from "../types";

interface Props { captions: CaptionWord[]; style?: CaptionStyle; }

const ACCENT = "#FFD60A";
// G6: "system-ui, -apple-system" resolves to San Francisco when previewing in
// Remotion Studio on a Mac, but Lambda's headless Linux container has no Apple
// fonts and falls back to whatever generic sans-serif ships in that container
// image — so a caption style tuned/reviewed locally could render with a
// DIFFERENT typeface in the actual delivered video, with no error anywhere.
// @remotion/google-fonts embeds Inter (visually close to San Francisco's
// proportions, so local review stays representative) and blocks the render
// via Remotion's own delayRender/continueRender until the font is ready —
// identical in Studio preview and on Lambda.
const { fontFamily } = loadFont("normal", { weights: ["600", "700", "800"] });
const FONT = fontFamily;

// G2: bottom safe-area. At 1080x1920, TikTok/IG Reels/YT Shorts all reserve the
// bottom ~300-350px of the frame for their OWN chrome (username, caption/
// description line, sound title, the like/comment/share icon column's lower
// edge, the tab bar) — a caption anchored at bottom:180 sits UNDER that chrome
// and gets visually collided with or fully obscured on-platform (this is only
// visible once posted to an actual app, never in Remotion Studio/preview,
// which is exactly why it went unnoticed). 320px clears all three platforms'
// published safe-zone guidance with margin to spare.
const CAPTION_SAFE_BOTTOM = 320;

// Captions carry OUTPUT-frame coords (remapped by the backend after cutting), so they
// render straight against the composition's global useCurrentFrame(). Three looks driven
// by the creator's Settings → caption_style:
//   clean     — a quiet running line, only the active word brightened
//   bold-word — one giant word at a time, karaoke-punch style (Submagic-like)
//   karaoke   — a line where spoken words fill with accent, upcoming stay white
export const Captions: React.FC<Props> = ({ captions, style = "clean" }) => {
  const frame = useCurrentFrame();
  if (captions.length === 0) return null;

  // Active word = last caption whose start frame has passed (manual scan; avoids the
  // ES2023 findLastIndex lib requirement in the render bundle).
  let activeIdx = -1;
  for (let i = 0; i < captions.length; i++) {
    if (captions[i].frame <= frame) activeIdx = i;
    else break;
  }
  if (activeIdx < 0) return null;

  if (style === "bold-word") return <BoldWord word={captions[activeIdx].word} />;
  if (style === "karaoke") return <Karaoke captions={captions} activeIdx={activeIdx} />;
  return <Clean captions={captions} activeIdx={activeIdx} />;
};

const wrap: React.CSSProperties = {
  position: "absolute", bottom: CAPTION_SAFE_BOTTOM, left: 0, right: 0,
  display: "flex", flexWrap: "wrap", justifyContent: "center",
  padding: "0 40px", gap: 8,
};

const Clean: React.FC<{ captions: CaptionWord[]; activeIdx: number }> = ({ captions, activeIdx }) => {
  const start = Math.max(0, activeIdx - 2);
  const end = Math.min(captions.length - 1, activeIdx + 4);
  return (
    <div style={wrap}>
      {captions.slice(start, end + 1).map((c, i) => {
        const isActive = start + i === activeIdx;
        return (
          <span key={start + i} style={{
            fontFamily: FONT, fontSize: 50, fontWeight: 600,
            color: "white", opacity: isActive ? 1 : 0.55,
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }}>{c.word}</span>
        );
      })}
    </div>
  );
};

const BoldWord: React.FC<{ word: string }> = ({ word }) => (
  <div style={{
    position: "absolute", inset: 0, display: "flex",
    alignItems: "center", justifyContent: "center", padding: "0 60px",
  }}>
    <span style={{
      fontFamily: FONT, fontSize: 128, fontWeight: 800, lineHeight: 1.05,
      color: "white", textAlign: "center", textTransform: "uppercase",
      letterSpacing: -2, textShadow: "0 4px 20px rgba(0,0,0,0.9)",
      WebkitTextStroke: "3px rgba(0,0,0,0.55)",
    }}>{word}</span>
  </div>
);

const Karaoke: React.FC<{ captions: CaptionWord[]; activeIdx: number }> = ({ captions, activeIdx }) => {
  const start = Math.max(0, activeIdx - 3);
  const end = Math.min(captions.length - 1, activeIdx + 3);
  return (
    <div style={wrap}>
      {captions.slice(start, end + 1).map((c, i) => {
        const idx = start + i;
        const spoken = idx <= activeIdx;
        return (
          <span key={idx} style={{
            fontFamily: FONT, fontSize: 54, fontWeight: 700,
            color: spoken ? ACCENT : "white",
            textShadow: "0 2px 8px rgba(0,0,0,0.85)",
            transform: idx === activeIdx ? "scale(1.08)" : "scale(1)",
            transition: "transform 0.05s, color 0.05s",
          }}>{c.word}</span>
        );
      })}
    </div>
  );
};
