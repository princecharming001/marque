import React from "react";
import { Audio } from "remotion";
import { AudioPlan, CaptionWord } from "../types";

// Background-music layer. Ducking v1 uses CAPTION ACTIVITY as the speech proxy —
// deterministic and analysis-free: within ±15 frames of any caption word the music
// drops to 35% of its set volume. No captions → constant volume (no ducking data).
const DUCK_WINDOW = 15;
const DUCK_FACTOR = 0.35;

export const AudioMix: React.FC<{
  audio?: AudioPlan | null;
  captions?: CaptionWord[];
}> = ({ audio, captions }) => {
  const music = audio?.music;
  if (!music || !music.url) return null;

  const frames = (captions ?? []).map((c) => c.frame).sort((a, b) => a - b);
  const speechActive = (f: number): boolean => {
    // Binary-search-free scan is fine: captions are ≤ a few hundred entries.
    for (const cf of frames) {
      if (cf > f + DUCK_WINDOW) break;
      if (Math.abs(cf - f) <= DUCK_WINDOW) return true;
    }
    return false;
  };

  const duck = music.duck_voice && frames.length > 0;
  return (
    <Audio
      src={music.url}
      loop
      volume={(f) => (duck && speechActive(f) ? music.volume * DUCK_FACTOR : music.volume)}
    />
  );
};
