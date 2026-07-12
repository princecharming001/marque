# Audio — loudness, music-under-voice, ducking, seams

## Loudness

- Target **−14 LUFS integrated** (the platform delivery standard). `probe_loudness()` measures
  the take; `gain_db = clamp(−14 − integrated_lufs, ±12)` is applied to the source audio.
- Never boost more than +12dB (noise floor) or cut more than −12dB (clamp).

## Music under voice

- Music sits **under** the voice: music bed ≈ −18 to −22 LUFS when voice is present, so the
  voice stays ~8–12 LU above the bed.
- No music over the hook's first spoken words if it competes — voice clarity wins the hook.

## Ducking

- Duck the music down while the creator speaks, back up in gaps. **Smooth the duck over ±8
  frames** (no per-word pumping — a stepped duck sounds like a broken gate).
- Duck depth ≈ −8 to −12dB under speech.

## Seams & fades

- Composition **fade in / fade out** at the head and tail (~10–15 frames) so nothing starts or
  ends abruptly.
- Music-loop seams should not click; a hard `<Audio loop>` is acceptable when the track has no
  reliable loop period (equal-power crossfade needs the loop length, which CORS-blocked catalog
  tracks don't expose — see AudioMix.tsx). Prefer tracks authored to loop.

## SFX budget (v2)

- Talking-head: 3–5 SFX per 30s on load-bearing beats only; montage may ride cuts
  <0.8s with whooshes. Full rules in sound_design.md — SFX match the visual action,
  never cover a spoken load-bearing word.
