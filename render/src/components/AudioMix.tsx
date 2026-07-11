import React from "react";
import { Audio, useVideoConfig } from "remotion";
import { AudioPlan } from "../types";

// Background-music layer.
//
// Ducking uses SPEECH-FRAME activity as the speech proxy (deterministic, analysis-free):
// within DUCK_WINDOW frames of a spoken word the music drops to DUCK_FACTOR of its set
// volume. G3: speech_frames (not the visual captions list) is the source so ducking keeps
// working when captions are OFF but the creator still wants music under their voice.
//
// P0.6: (1) the duck is no longer a hard step — it ramps over DUCK_RAMP frames on each side
// so the music doesn't "pump" on every word; (2) a composition-level fade in/out so the
// track doesn't slam in at frame 0 or cut off at the end.
//
// DEVIATION FROM PLAN (documented per the guardrail): the plan asked to replace `loop` with
// staggered self-crossfading Sequences (30-frame equal-power seam). That requires the music
// LOOP PERIOD, which is only obtainable via @remotion/media-utils useAudioData /
// getAudioDurationInSeconds — both decodeAudioData()-based and CORS-gated. The bundled music
// catalog (main.py MUSIC_TRACKS) is hosted on commondatastorage.googleapis.com WITHOUT CORS
// headers, so useAudioData throws and FAILS the render (verified: /tmp/p06 render errored
// "Does the resource support CORS?"). `<Audio>` streams server-side and is not CORS-gated,
// so it stays. Adding music.duration to the EDL is disallowed (only end_frame / audio.gain
// are additive). The seam softening is therefore delivered as a composition fade in/out plus
// reliance on loop-clean source tracks, rather than a per-seam crossfade — the audible wins
// (no pumping duck, no slam-in) land without risking the render.
const DUCK_WINDOW = 15;   // full duck within this many frames of speech
const DUCK_RAMP = 8;      // ramp between full and ducked across this many frames
const DUCK_FACTOR = 0.35;
const FADE = 20;          // composition-level fade in / out

export const AudioMix: React.FC<{ audio?: AudioPlan | null }> = ({ audio }) => {
  const music = audio?.music;
  const { durationInFrames } = useVideoConfig();
  if (!music || !music.url) return null;

  const frames = (audio?.speech_frames ?? []).slice().sort((a, b) => a - b);
  // Smoothed duck: full duck within DUCK_WINDOW of speech, easing back to full across
  // DUCK_RAMP frames (instead of the old hard 35%/100% step that pumped on every word).
  const duckAt = (f: number): number => {
    if (!music.duck_voice || frames.length === 0) return 1;
    let nearest = Infinity;
    for (const cf of frames) {
      const d = Math.abs(cf - f);
      if (d < nearest) nearest = d;
      if (cf > f + DUCK_WINDOW + DUCK_RAMP) break;
    }
    if (nearest <= DUCK_WINDOW) return DUCK_FACTOR;
    if (nearest >= DUCK_WINDOW + DUCK_RAMP) return 1;
    const t = (nearest - DUCK_WINDOW) / DUCK_RAMP;   // 0 → ducked, 1 → full
    return DUCK_FACTOR + (1 - DUCK_FACTOR) * t;
  };

  // Composition-level fade in/out so the loop doesn't slam in at 0 or cut off at the end.
  const envAt = (f: number): number => {
    let e = 1;
    if (f < FADE) e *= f / FADE;
    if (f > durationInFrames - FADE) e *= Math.max(0, (durationInFrames - f) / FADE);
    return e;
  };

  return (
    <Audio src={music.url} loop volume={(f) => music.volume * duckAt(f) * envAt(f)} />
  );
};
