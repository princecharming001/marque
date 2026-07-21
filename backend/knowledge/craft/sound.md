# Craft — Sound Layering (dialogue-first, by the numbers)

The stack is Dialogue / Music / Effects over an ambience bed (the D/M/E stem
model — Netflix delivery requires the split). Dialogue is the governing law
(GoE #34): the bed always drops under speech; intelligibility binds every
other layer.

Published levels: music bed sits -18 to -20dB below dialogue during speech
(practitioner band; WCAG G56 makes >=20dB the accessibility-strict floor;
separation under 15dB risks masking on phone speakers). Ducking: 4-10dB
reduction, attack <=30ms, release 50-200ms — deeper pumps, slower swallows
word onsets. Short-form masters: -14 LUFS integrated / true peak <= -1dBTP
(platform normalization target; AES TD1008 streams at ~-16 with speech 2-3 LU
under music for parity — we serve platform loudness, so -14).

Room tone, never digital silence (Purcell; Frame.io): true silence jars the
viewer out; every speech gap carries matched tone (our room-tone bed at 0.55
is this rule). Ambience is continuous across cuts WITHIN a scene; hard sonic
contrast only at scene/topic boundaries (GoE element 6).

Music endings are structural (GoE #29): back-time the track so its final bar
lands with the final shot — never fade out mid-phrase. Track can lead picture
at the open (GoE #28). Cuts on transients score higher (GoE #37 — blink
masking); that is beat_snap's doctrine.

```yaml
rules:
  - id: snd.dialogue_first
    principle: "Bed ducks under all speech; -18..-20dB below dialogue (floor 15dB separation)"
    source: "GoE #34; WCAG G56; practitioner consensus"
    enforce: lint
    params: {max_music_volume_undacked: 0.25}
  - id: snd.master_loudness
    principle: "-14 LUFS integrated, true peak <= -1.0 dBTP"
    source: "Platform normalization; AES TD1008"
    enforce: knob
  - id: snd.no_digital_silence
    principle: "Speech gaps carry room tone, never true silence"
    source: "Purcell, Dialogue Editing; Frame.io"
    enforce: knob
  - id: snd.structural_ending
    principle: "Music ends on a structural bar with the final shot, never a mid-phrase fade"
    source: "GoE #29"
    enforce: critic
```
