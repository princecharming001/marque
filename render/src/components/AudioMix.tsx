import React from "react";
import { Audio } from "remotion";
import { AudioPlan } from "../types";

// Background-music layer. Ducking v1 uses SPEECH-FRAME activity as the speech
// proxy — deterministic and analysis-free: within ±15 frames of any spoken word
// the music drops to 35% of its set volume. G3: speech_frames (not the visual
// captions list) is the source here specifically so ducking keeps working when
// a creator turns captions OFF but still wants music ducked under their voice —
// captions and duck_voice are independent creative choices.
const DUCK_WINDOW = 15;
const DUCK_FACTOR = 0.35;

export const AudioMix: React.FC<{
  audio?: AudioPlan | null;
}> = ({ audio }) => {
  const music = audio?.music;
  if (!music || !music.url) return null;

  const frames = (audio?.speech_frames ?? []).slice().sort((a, b) => a - b);
  const speechActive = (f: number): boolean => {
    // Binary-search-free scan is fine: a transcript is ≤ a few hundred words.
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
