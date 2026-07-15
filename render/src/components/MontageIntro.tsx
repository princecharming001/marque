import React from "react";
import { Sequence, OffthreadVideo, Img } from "remotion";
import { Montage } from "../types";

// Addendum mode H — the listicle hook flash: right after the hook line, every item the
// take promises flashes full-frame in rapid sequence (12f ≈ 0.4s each, hard cuts, ≤5
// items = ≤2s, inside the spec's 2.5s cap). The backend only emits `montage` for a
// listicle take with ≥4 distinct REAL assets, so this component never runs on filler.
// Muted; the speaker's voice carries straight through the flash.
export const MontageIntro: React.FC<{ montage?: Montage | null }> = ({ montage }) => {
  if (!montage || !(montage.items?.length >= 2)) return null;
  const per = Math.max(9, Math.min(15, montage.frames_per || 12));   // 0.3–0.5s per item
  const items = montage.items.slice(0, 5);
  return (
    <>
      {items.map((url, i) => {
        const isImage = /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url);
        const style: React.CSSProperties = {
          position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
        };
        return (
          <Sequence key={i} from={montage.frame_in + i * per} durationInFrames={per} layout="none">
            {isImage ? <Img src={url} style={style} /> : <OffthreadVideo src={url} muted style={style} />}
            {/* item counter chip — N/total (spec §6.6) */}
            <div style={{
              position: "absolute", top: 300, right: 60,
              padding: "10px 20px", borderRadius: 999, background: "rgba(0,0,0,0.6)",
              color: "#fff", fontFamily: "Inter, sans-serif", fontSize: 34, fontWeight: 800,
            }}>{i + 1}/{items.length}</div>
          </Sequence>
        );
      })}
    </>
  );
};
