import React from "react";
import { useCurrentFrame } from "remotion";
import { CaptionWord } from "../types";

interface Props { captions: CaptionWord[]; totalFrames: number; }

export const Captions: React.FC<Props> = ({ captions, totalFrames }) => {
  const frame = useCurrentFrame();
  const activeIdx = captions.findLastIndex((c) => c.frame <= frame);
  const windowStart = Math.max(0, activeIdx - 2);
  const windowEnd = Math.min(captions.length - 1, activeIdx + 4);
  const visible = captions.slice(windowStart, windowEnd + 1);

  return (
    <div style={{
      position: "absolute", bottom: 180, left: 0, right: 0,
      display: "flex", flexWrap: "wrap", justifyContent: "center",
      padding: "0 40px", gap: 6,
    }}>
      {visible.map((c, i) => (
        <span key={windowStart + i} style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          fontSize: 52, fontWeight: 700,
          color: windowStart + i === activeIdx ? "#FFD60A" : "white",
          textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          transition: "color 0.05s",
        }}>{c.word} </span>
      ))}
    </div>
  );
};
