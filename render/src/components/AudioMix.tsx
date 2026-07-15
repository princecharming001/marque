import React from "react";
import { Audio, Sequence, useVideoConfig } from "remotion";
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
const FADE = 20;          // composition-level fade out (and intro ramp after the lead)
const MUSIC_LEAD = 15;    // spec §7: the bed starts ~0.5s (15f@30) AFTER the first spoken
                          // word, so the hook lands over clean voice, never under music.

// P4: SFX one-shots — build_render_plan has already resolved each cue to an
// output frame + hosted URL (an unresolved kind, or one whose anchor frame
// got cut, never reaches this list — see synthesize_sfx/build_render_plan).
// Each cue is its own Sequence so it plays exactly once starting at its frame,
// independent of the looping music track above.
const SfxLayer: React.FC<{ sfx: AudioPlan["sfx"] }> = ({ sfx }) => (
  <>
    {sfx.map((cue, i) => (
      <Sequence key={i} from={cue.frame} layout="none">
        <Audio src={cue.url} volume={cue.gain} />
      </Sequence>
    ))}
  </>
);

export const AudioMix: React.FC<{ audio?: AudioPlan | null }> = ({ audio }) => {
  const music = audio?.music;
  const { durationInFrames } = useVideoConfig();
  const sfx = audio?.sfx ?? [];

  // P4: music and SFX are independent layers — a clip with SFX cues but no
  // (or unset) music must still hear them, so this can no longer early-return
  // just because there's no music track.
  if ((!music || !music.url) && sfx.length === 0) return null;

  // A5a (schema v3): optional per-plan duck-curve override, each field read
  // independently with the module constant as its fallback — an absent
  // `audio.duck` (every pre-v3 plan) behaves byte-identically to today.
  const duck = audio?.duck;
  const duckWindow = duck?.window_f ?? DUCK_WINDOW;
  const duckRamp = duck?.ramp_f ?? DUCK_RAMP;
  const duckFactor = duck?.factor ?? DUCK_FACTOR;

  const frames = (audio?.speech_frames ?? []).slice().sort((a, b) => a - b);
  // Smoothed duck: full duck within duckWindow of speech, easing back to full across
  // duckRamp frames (instead of the old hard 35%/100% step that pumped on every word).
  const duckAt = (f: number): number => {
    if (!music || !music.duck_voice || frames.length === 0) return 1;
    let nearest = Infinity;
    for (const cf of frames) {
      const d = Math.abs(cf - f);
      if (d < nearest) nearest = d;
      if (cf > f + duckWindow + duckRamp) break;
    }
    if (nearest <= duckWindow) return duckFactor;
    if (nearest >= duckWindow + duckRamp) return 1;
    const t = (nearest - duckWindow) / duckRamp;   // 0 → ducked, 1 → full
    return duckFactor + (1 - duckFactor) * t;
  };

  // Fade OUT at the end so the loop doesn't cut off abruptly. The INTRO ramp is handled
  // by startGate below (after the music lead), not here.
  const envAt = (f: number): number => {
    let e = 1;
    if (f > durationInFrames - FADE) e *= Math.max(0, (durationInFrames - f) / FADE);
    return e;
  };

  // Music start gate: silent until MUSIC_LEAD frames past the first spoken word, then ramps
  // in over FADE frames. The hook plays over clean voice (spec §7). Falls back to a tiny
  // lead when there's no speech-frame data so music still doesn't slam in at frame 0.
  const firstSpeech = frames.length ? frames[0] : 0;
  const musicStart = firstSpeech + MUSIC_LEAD;
  const startGate = (f: number): number => {
    if (f < musicStart) return 0;
    if (f < musicStart + FADE) return (f - musicStart) / FADE;
    return 1;
  };

  return (
    <>
      {music && music.url && (
        <Audio src={music.url} loop
               volume={(f) => music.volume * duckAt(f) * envAt(f) * startGate(f)} />
      )}
      {sfx.length > 0 && <SfxLayer sfx={sfx} />}
    </>
  );
};
