# Perception stack for an agentic talking-head editor: transcription, alignment, disfluency, prosody, visual and video-native understanding, and the multimodal take index

## Summary
Build the agent's perception around a word-level, verbatim transcript. Every other signal (pauses, emphasis, face, gesture, visual descriptions) hangs off word IDs and millisecond times. Measured quality points to three choices. First, ElevenLabs Scribe v2 is the best readily available verbatim ASR: 90.3% English disfluency F1 and 2.2% content WER. CrisperWhisper 2.0 Pro scores a little higher (93.2%) and has the best word timing (~30 ms), but it needs a commercial license and every one of its numbers comes from its own authors. AssemblyAI, the previous plan's choice, catches "um/uh" well but misses most false starts and all vocal sounds. Deepgram, Whisper-based pipelines (WhisperX, faster-whisper) and gpt-4o-transcribe are ruled out, for dropping fillers or for having no word timestamps. Second, re-time words with Montreal Forced Aligner 3.0 (~20 ms) and snap cut edges to measured silence (Silero VAD v6 plus energy). Third, no LLM (Gemini, Qwen, Claude) should ever provide a timestamp: grounding errors are measured in seconds and Gemini's times drift. Put video/audio LLMs to work on judgments about clips the code has already cut out. Build prosody and face signals with DSP, WhiStress and MediaPipe, because audio LLMs are near chance on paralinguistics. Skip shot detection for single phone takes.

## Verified findings
## 0. Scope and what changed
This builds on the prior report (word-ID transcript spine, kamgasimo acoustic tables, round-trip ASR verifier; AssemblyAI named as ASR). Fact-check verdict: the researcher's core architecture holds. The main corrections:
- The only verbatim benchmark uses acted, single-speaker audio.
- MFA should be a timing voter, not the authority, in disfluent spans.
- MAI-Transcribe-2 is a stronger second voter than stated.
- Several secondary numbers and attributions were fixed.

Claude takes no audio or video input, so everything below exists to turn media into text and images for the agent ([Anthropic Vision docs, via prior notes](https://platform.claude.com/docs/en/build-with-claude/vision)).

## 1. ASR: verbatim capture beats WER, but the evidence is thin
**Headline WER cannot choose the ASR.** Artificial Analysis normalizes with Whisper's normalizer plus rules that "remove bracketed text and filler words ('uh', 'um')" ([AA methodology](https://artificialanalysis.ai/speech-to-text/methodology)). AA-WER:

| Model | AA-WER |
|---|---|
| MAI-Transcribe-2 | 2.0 |
| **Scribe v2** | **2.2** |
| Gemini 3.5 Transcribe | 2.6 |
| Universal-3 Pro | 3.1 |
| GPT Transcribe | 3.3 |
| GPT-4o Transcribe | 4.0 |
| Whisper L-v3 | 4.1 |
| Nova-3 | 5.2 |

Source: [AA leaderboard](https://artificialanalysis.ai/speech-to-text/non-streaming).

**Verbatim benchmark (Nyra, MIT, vendor-run, ranks itself first)** ([repo](https://github.com/nyrahealth/nyra_verbatim_speech_benchmark)):

| System (EN, n=4,957) | Disfl F1 | Filler | Vocal | Cutoff | Rep | vWER |
|---|---|---|---|---|---|---|
| CrisperWhisper 2.0 Pro | 93.2 | 95.7 | 94.8 | 90.7 | 88.3 | 3.0 |
| CrisperWhisper 2.0 | 90.7 | 94.3 | 83.5 | 89.3 | 87.8 | 3.6 |
| **Scribe v2** | **90.3** | 95.5 | 83.4 | 80.1 | 87.9 | 3.2 |
| Inworld STT | 84.4 | 95.2 | 10.7 | 84.8 | 86.8 | 4.0 |
| MAI-Transcribe-1.5 | 84.0 | 95.8 | 0.0 | 83.3 | 86.8 | 3.6 |
| xAI Grok STT | 73.9 | 84.4 | 0.0 | 60.3 | 83.4 | 4.7 |
| AssemblyAI U3 Pro (prompt 2) | 67.9 | 90.8 | 2.8 | 27.9 | 82.7 | 4.5 |
| Deepgram Nova-3 | 57.3 | 45.7 | 0.0 | 72.2 | 83.2 | 6.3 |
| Canary 1B v2 | 26.6 | 26.7 | 0.0 | 48.9 | 30.5 | 9.0 |
| Whisper L-v3 | 9.7 | 9.4 | 0.0 | 31.4 | 6.4 | 10.2 |

**Critical caveat (missed by the researcher).** The English set is DisfluencySpeech: **one speaker re-enacting ~5,000 Switchboard utterances in a studio (9.49 h)** ([DisfluencySpeech](https://arxiv.org/abs/2406.08820)). The disfluencies are acted, it is one voice, and the audio is clean. Treat the ranking as directional, not proof, for creator phone audio. German has only 202 utterances. On it, AssemblyAI filler F1 falls to 3.2–7.8 and Deepgram to 1.0.

What still holds:
- **Whisper-text pipelines** (WhisperX, faster-whisper) are out as the verbatim source ([video-use SKILL](https://github.com/browser-use/video-use/blob/main/SKILL.md)).
- **AssemblyAI U3 Pro** catches fillers but misses cut-offs (27.9) and vocal sounds (2.8). Nyra's paper gives it 85.8% event F1 on a different scoring, against 86.3% for Reverb and 90.7% for Nyra's model ([Nyra paper](https://arxiv.org/html/2607.18934v1)).
- **Deepgram** `filler_words` covers seven fixed tokens and is off by default. When off, "uh"/"um" are stripped ([Deepgram](https://developers.deepgram.com/docs/filler-words)).
- **OpenAI**: "timestamp_granularities[] … is only supported for whisper-1", so gpt-4o-transcribe and gpt-transcribe cannot place cuts ([OpenAI](https://developers.openai.com/api/docs/guides/speech-to-text)).

**Scribe v2 settings** ([API ref](https://elevenlabs.io/docs/api-reference/speech-to-text/convert); [upgrade, 2026-04-02](https://elevenlabs.io/blog/scribe-v2-just-got-an-upgrade)):
- `timestamps_granularity="character"`: per-character times, missed by the researcher.
- Per-word `logprob`: use it for confidence flags.
- `tag_audio_events`.
- `diarize` (up to 32 speakers).
- `keyterms`: up to 1,000 terms of 50 characters each; seed them from the creator's name, brand and products.
- `seed` and `temperature`.
- `no_verbatim` must stay **off**: it removes "filler words, false starts and non-speech sounds".

**Second voter options, in order:**
1. **MAI-Transcribe-2** (public preview, Sept 2026). Word timestamps, a `transcribeStyle` that defaults to `verbatim` ("including filler words and false starts"), phrase biasing, 60 languages ([MS Learn](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe)). The researcher wrongly listed it as timestamp-unverified. Its predecessor 1.5 scored 84.0 disfluency F1 but 0 on vocal sounds; 2.0 has no verbatim score yet. It is marked "not recommended for production" while in preview.
2. **CrisperWhisper 2.0 Pro**: best measured, but all numbers are self-reported, and Nyra's benchmark data could overlap its training distribution. Weights are non-commercial except Pro, which is "commercial license only" ([repo](https://github.com/nyrahealth/CrisperWhisper)).
3. **AssemblyAI Universal-3.5 Pro** (2026-07-07; verbatim unbenchmarked). Use the documented prompt: "Mandatory: Preserve linguistic speech patterns including disfluencies, filler words, hesitations, repetitions, stutters, false starts, and colloquialisms." Audio tags are "experimental" ([AAI prompt guide](https://www.assemblyai.com/blog/universal-3-pro-prompt-engineering); [U3.5](https://www.assemblyai.com/blog/universal-3-5-pro-async)).

**Also verified:**
- **Inworld STT** (84.8 cutoff F1) is an unconsidered candidate.
- **Rev Reverb**: the code is Apache-2.0, but the weights are gated under license "other", so treat them as not cleared for commercial use ([HF](https://huggingface.co/Revai/reverb-asr)).
- **Parakeet-TDT-0.6B-v3**: CC-BY-4.0, word and character timestamps, 6.34% average Open ASR WER, no verbatim data ([card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)).
- **Voxtral Mini Transcribe V2**: API only, word timestamps, 100 bias terms. Voxtral Realtime is Apache-2.0 ([Mistral](https://mistral.ai/news/voxtral-transcribe-2/)).
- **Avoid whisper-timestamped's `detect_disfluencies`**: it is AGPL-3.0 ([repo](https://github.com/linto-ai/whisper-timestamped)).

**Benchmark rights.** AssemblyAI's terms bar use to "engage in competitive analysis or benchmarking" ([AAI ToS](https://www.assemblyai.com/legal/terms-of-service)). A third-party audit flags the same for xAI Grok STT, and finds no such clause for ElevenLabs, OpenAI, Deepgram or Speechmatics ([koedesk](https://github.com/guide-inc-org/koedesk-stt-bench/blob/main/PREREGISTRATION.md)). Get written consent before an in-house bake-off that includes those two.

**Gold labels.** Rev AI's human transcriber with `verbatim=true` covers English only, returns in 12–24 h and costs +$0.50/min for verbatim ([Rev AI](https://docs.rev.ai/api/asynchronous/transcribers/)). It fits labelling a bake-off set; production use would mean next-day turnaround.

## 2. Word timing: voters plus acoustic snapping, not one aligner
Mean word-boundary error in ms:

| Method | TIMIT | Buckeye | FluencyBank (disfluent) | Source |
|---|---|---|---|---|
| MFA 3.0 (ARPA) | 19.9 | 21.8 | 142 | [MFA 2026](https://arxiv.org/html/2606.18466), [Nyra paper](https://arxiv.org/html/2607.18934v1) |
| CrisperWhisper 2.0 | 29.6 (paper variant 36) | 40.6 | 102 | [Nyra site](https://www.nyra-labs.com/crisperwhisper), Nyra paper |
| xAI Grok STT | 37.1 | 47.1 | – | Nyra site |
| Scribe v2 | 51.3 | 59.6 | – | Nyra site |
| Deepgram Nova-3 | 63.3 | 88.4 | – | Nyra site |
| NeMo Forced Aligner | 78.2 | 88.6 | – | MFA 2026 |
| WhisperX | 66–110 | 110.9 | 200 | MFA 2026 / Nyra paper |

(The researcher's "Canary-1B 78 ms" is actually the NeMo Forced Aligner figure.)

**Correction to the MFA recommendation.** MFA leads on *fluent* speech, and those numbers come from its own authors. On disfluent speech it is **worse than CrisperWhisper (142 vs 102 ms)**. Retake and stumble cuts happen exactly there, and cut-offs are partial words that need G2P pronunciations. So:
- Use MFA as one timing voter alongside Scribe character times and, if licensed, CrisperWhisper.
- Take the final edge from measured signal: search the inter-word gap for the Silero non-speech region and the RMS minimum.
- Pad 30–200 ms. video-use pads this much because "Scribe timestamps drift 50–100ms" ([video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md)).
- kamgasimo places "every cut… on the measured sound" and re-decodes isolated slices to adjudicate fillers ([cutting.md](https://github.com/kamgasimo/ai-video-editor/blob/main/reference/cutting.md)).
- TimeBolt markets ±10 ms waveform snapping, a vendor claim ([TimeBolt](https://timebolt.io/)).

The researcher's "last word's end is 1–2 s late" trap could not be sourced.

**Qwen3-ForcedAligner** (Apache-2.0, ≤5 min, 11 languages): 42.9 ms against MFA labels, and **24.8–41.8 ms on a human-labeled set** ([HF](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)). This contradicts the researcher's claim that it was only measured against MFA. It is a reasonable fallback for non-English.

**Order matters.** An aligner only fixes times. A dropped "um" gets absorbed into neighbouring words, so the verbatim transcript must come first.

## 3. VAD and diarization
**Silero VAD v6** (MIT):
- Multi-domain ROC-AUC 0.97, against v5 0.96, TEN 0.93 and WebRTC 0.73.
- On noise-only ESC-50, **clip-level accuracy** (not ROC-AUC) is 0.87 against v5 0.61, TEN 0.42 and WebRTC 0 ([wiki, 2026-03](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)).
- Pair it with RMS or silencedetect for gap maps.
- Also gate any Whisper-derived model with VAD, to avoid decoding silence or music.

**Diarization** is only needed to flag non-creator speech. Options:
- Scribe diarize.
- pyannote community-1 (CC-BY-4.0). AMI-IHM DER is 17.0, against 18.8 for v3.1 and 12.9 for the paid Precision-2. Its exclusive diarization output reconciles speaker turns with word times ([HF](https://huggingface.co/pyannote-community/speaker-diarization-community-1)).

**Missed: diarization cannot tell whether the visible face is talking.** Where a second face or an off-camera voice appears, add active speaker detection, e.g. TalkNet-ASD (MIT; AVA test mAP 90.8, Columbia F1 96.3) ([repo](https://github.com/TaoRuijie/TalkNet-ASD)).

## 4. Prosody and emphasis: measure it, don't ask an audio LLM
Audio LLMs are weak here:
- **SonicBench:** "most models perform near random guessing" on physical attributes, even though their frozen encoders capture the cues ([SonicBench](https://arxiv.org/abs/2601.11039)).
- **ParaPairAudioBench:** audio-LLM judges trail humans by **32 percentage points on average across five dimensions** (style, rate, emphasis, age, gender), not specifically on emphasis ([ParaPair](https://arxiv.org/abs/2606.24648)).
- **StressTest detection F1:** Gemini-2.5-Pro 48.5, GPT-4o-audio 46.1, against WhiStress 88.3 and StresSLM 86.9.
- **StressTest reasoning:** WhiStress→GPT-4o 83.4%, Gemini 77.5%, StresSLM 86.2%. Humans average 92.6%, and a majority vote reaches 96.0% ([StressTest](https://arxiv.org/html/2505.22765)).
- **Gemini's input audio** is processed at 1 Kbps ([Gemini docs](https://ai.google.dev/gemini-api/docs/video-understanding)).

Recommended per-word features:
- **Parselmouth/Praat** (GPL-3.0+; fine server-side without distribution): f0, intensity and duration, z-scored against the creator's baseline ([repo](https://github.com/YannickJadoul/Parselmouth)).
- **WhiStress** stress probabilities (MIT, English, whisper-small.en, 39 stars) ([repo](https://github.com/slp-rl/WhiStress)).
- **emotion2vec+** is optional. The code is MIT, it outputs 9 classes, it was last updated Oct 2024, and it has no editing evidence ([repo](https://github.com/ddlBoJack/emotion2vec)).

**Missed: per-take audio quality.** Meta Audiobox Aesthetics (CC-BY-4.0) predicts Production Quality, Content Enjoyment and related axes. It can inform take choice and polish decisions but has not been validated for this use ([repo](https://github.com/facebookresearch/audiobox-aesthetics)).

## 5. Video-native models: judgments yes, timestamps never
**Temporal grounding is coarse.**
- Charades-TL mIoU: TimeLens2-8B 58.6 (CC BY 4.0), Gemini 2.5 Pro 52.8, Qwen3-VL-235B 47.8, GPT-5 40.5 ([TimeLens2](https://arxiv.org/html/2607.17423)).
- VUE-TR-V2 IoU: Gemini 3 Pro Preview 37.6, Vidi2.5 49.6 (proprietary), GPT-5 17.2 ([Vidi2.5](https://arxiv.org/html/2511.19529)).
- The researcher's "Gemini 3 Pro 39.7" could not be traced.

**Gemini timestamps drift** ([forum, 2026-03-07](https://discuss.ai.google.dev/t/bug-gemini-3-flash-and-3-1-pro-progressive-timestamp-drift-in-audio-transcription/129501)):
- Gemini 3.1 Pro drifts −16 s on an 11:49 clip, with times rounded to whole seconds.
- Gemini 3 Flash runs about 22% fast, reaching −157 s.
- Users still reported it unresolved in Aug 2026.

**Gemini input handling** ([docs](https://ai.google.dev/gemini-api/docs/video-understanding)): default sampling is 1 fps, with custom `fps` and `start_offset`/`end_offset`. The docs say fast action "might lose detail", and prompt references use MM:SS.

**Where they help: holistic judgments.** VideoMME with audio: Gemini-3.1 Pro 89.0, Qwen3.5-Omni-Plus 83.7. DailyOmni: Qwen 84.6, Gemini 82.7. Qwen3.5-Omni is API-only ([report](https://arxiv.org/html/2604.15804v1)). The pattern:
1. Code cuts each take or segment into its own clip with an ID.
2. The model answers judgment questions, e.g. which take reads more confident, whether the speaker is reading notes, what they are holding.
3. Any time the model outputs is discarded.

**Burn frame numbers or timestamps into frames shown to Claude.** NumPro raised Qwen2-VL-7B mIoU from 12.5 to 31.3 on ActivityNet and from 7.9 to 38.5 on Charades-STA, training-free ([NumPro](https://arxiv.org/html/2411.10332v1)).

**Other video models:**
- **Qwen3-Omni-30B-A3B** (Apache-2.0) is the open fallback judge. It needs 88.52 GB for 30 s of video in BF16, and WorldSense is 54.0 ([HF](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct)).
- **Pegasus 1.5** (2026-09-25) is vendor-reported at 0.428 vs 0.337 "overall segmentation" against Gemini 3.1 Pro, with a 2 s minimum segment. It suits b-roll indexing, not cut perception ([TwelveLabs](https://www.twelvelabs.io/blog/introducing-pegasus-1-5)).

## 6. Shots, face, gesture, sync
**Shot detection** is not needed for single phone takes. For multi-shot uploads and b-roll, use TransNetV2 (MIT) ([repo](https://github.com/soCzech/TransNetV2)). Range F1 on OmniShotCutBench: TransNetV2 0.814, PySceneDetect 0.755, OmniShotCut 0.881. On BBC: 0.967 against 0.889. OmniShotCut's code is not confirmed released ([OmniShotCut](https://arxiv.org/html/2604.24762v2)).

**MediaPipe Face Landmarker** outputs 478 landmarks, 52 blendshapes and a transformation matrix, with a VIDEO mode ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)). The researcher said no accuracy figures exist. In fact the **Blendshape V2 card reports MAD ≈0.199 on the 0–1 scale and lists limitations**: faces looking away more than 80°, less than 50% visible, or too small; plus "jittering" under noise, lighting or motion ([model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf)). Two consequences:
- Smooth blendshapes over time before thresholding blinks and look-aways.
- Treat face-lost frames as a look-away signal in their own right.

Derived signals:
- Blinks.
- Head yaw/pitch and eye-look (reading notes).
- Mouth-open onset: keep the pre-speech breath inside the cut.
- Stillness windows.
- Face box for punch-ins.

Add the Pose/Hand landmarkers for gesture motion energy.

**Seam score.** Cut where the speaker is "relatively quiet and still" ([Berthouzoz et al., SIGGRAPH 2012](https://www.floraine.org/research/video-transitions/)). Compute the landmark and pose delta between the out-frame and the in-frame. A large delta predicts a visible jump that needs a punch-in or cover. There is no modern A/B evidence for this; build an in-house labeled seam set.

**Missed: audio-video offset.** SyncNet (MIT) estimates the offset in frames, with a confidence score ([repo](https://github.com/joonson/syncnet_python)). A constant offset from wireless mics makes word-accurate cuts look wrong on the lips. iOS 26 lets AirPods act as a video mic ([TechRadar](https://www.techradar.com/audio/earbuds-airpods/how-to-use-airpods-as-a-video-mic-in-ios-26)). How often this affects creator footage is unmeasured, so run the check and fix the offset before any cutting.

**Pre-processing.** Tone-map HLG/PQ to BT.709 and resolve VFR presentation timestamps onto one media clock before frame analysis.

## 7. The take index
Deep Video Discovery pattern: multi-granular DB plus Global Browse / Clip Search / Frame Inspect tools. LVBench 74.2%, rising to **76.0% with transcripts** (NeurIPS 2025) ([DVD](https://arxiv.org/abs/2505.18079)). For 15–90 s takes the whole index fits in context; add search for 10-minute takes. All layers share one clock (ms plus native frame index):
- **L0 media:** fps/VFR, HDR transfer, rotation, R128 loudness, clipping, noise floor, **AV offset**, and audio-quality scores.
- **L1 words:** id and verbatim text. Per-voter times, Scribe character times and logprob, the MFA time, and a consensus time. Type: word, filler, repetition, cut-off, or event. Sentence id and retake-cluster id.
- **L2 gaps:** duration, VAD probability, energy-minimum snap point, breath flag, room-tone RMS.
- **L3 prosody:** z-scored f0 and range, intensity, relative duration, rate, WhiStress probability.
- **L4 visual events** (10–15 fps, smoothed): blinks, look-aways, face-lost, gesture, stillness, face box, blur/exposure; ASD speaking-face flag when needed.
- **L5 semantics:** Claude's reading of the text plus contact sheets; optional per-clip Gemini or Qwen-Omni verdicts keyed by ID.
- **L6 seams** (on demand): visual delta, room-tone match, snap points.
- **Disagreement flags:** raised where ASR voters, logprob or aligners disagree. Resolve by re-decoding the slice in isolation or with acoustics.

**Tools on the index:** `get_words`, `get_gaps`, `get_prosody`, `get_visual_events`, `view_frames(range)` (timestamp-burned sheet), `judge_clip(ids, q)` (only when a key exists), `redecode(range)`.

## Evidence strength
**Strong:**
- Whisper-family filler loss.
- LLM and Gemini timestamp unreliability.
- The aligner hierarchy on *fluent* speech.

**Moderate / weak:**
- Scribe versus CrisperWhisper: vendor-run benchmark on acted, single-speaker studio audio.
- MFA's value on disfluent spans: its advantage reverses there.
- MediaPipe seam cues: a 2012 paper, no modern A/B.
- AV-offset prevalence: anecdotal.

**Missing:**
- Any verbatim or cut-edge benchmark on real creator phone monologue.
- Verbatim scores for MAI-Transcribe-2, Universal-3.5 Pro and Parakeet.

## Verified recommendations
- Primary ASR: ElevenLabs Scribe v2 in verbatim mode. Settings: no_verbatim off, tag_audio_events on, diarize on, timestamps_granularity='character', keyterms (≤1,000) from the creator profile, per-word logprob kept for flags. Confidence medium: it ranks 3rd on the only verbatim benchmark (90.3 disfluency F1), but that benchmark is one speaker's acted studio speech.
- Add a second verbatim voter that has word times and reconcile word by word, flagging disagreements. Try MAI-Transcribe-2 (verbatim default, word timestamps, public preview) first. Use CrisperWhisper 2.0 Pro if Nyra licenses it, and AssemblyAI U3.5 Pro with the documented 'Mandatory: Preserve…' prompt as the fallback.
- Make timing a vote, not a single aligner. Keep Scribe character times, MFA 3.0 and, if licensed, CrisperWhisper times as voters. Always place the final cut edge by acoustic snapping inside the gap (Silero v6 non-speech plus RMS minimum, 30–200 ms padding). Measure in the bake-off whether MFA helps at all in disfluent spans, where it scores 142 ms against CrisperWhisper's 102 ms.
- Never accept a timestamp from Gemini, Qwen-Omni, Twelve Labs or Claude. Cut clips in code, send each by ID, ask only for judgments, and burn frame numbers or timestamps into every contact sheet shown to Claude (NumPro: 12.5→31.3 mIoU).
- Build prosody with DSP (Parselmouth f0, intensity, duration, rate, z-scored per creator) plus WhiStress stress probabilities. Do not rely on audio-LLM paralinguistic judgments: they sit near chance on SonicBench, trail humans by 32 points on ParaPair, and Gemini scores 48.5 stress-detection F1.
- Run MediaPipe Face (plus Pose/Hand) at 10–15 fps on a tone-mapped, VFR-resolved proxy with temporal smoothing. Treat face-lost frames (look-away >80°) as an event and compute the seam visual delta on demand. Add TalkNet-ASD only when a second face or an off-camera voice is detected.
- Run a SyncNet audio-video offset check per upload and correct any constant offset before cutting. Score per-take audio quality with Audiobox Aesthetics as an input to take choice. Both are cheap, but the evidence for each is weak or untested.
- Structure perception as the layered take index keyed by word IDs and one media clock, exposed through a few query tools, with the whole index in context for takes under about 2 minutes.
- Leave shot detection and diarization out of the default path. Use TransNetV2 only for multi-shot uploads or b-roll, and Scribe diarize or pyannote community-1 only to flag non-creator speech.
- Before locking the stack, label 30–50 real creator takes, using Rev AI human verbatim (English, 12–24 h) plus in-house cut-point annotation. Bake off Scribe v2, MAI-Transcribe-2, CrisperWhisper 2.0, Inworld STT and U3.5 Pro on verbatim F1 and cut-edge error. Get written consent from AssemblyAI and xAI first, since their terms bar benchmarking.
- Keep perception in the verification loop: re-decode isolated slices to settle ambiguous fillers, and re-transcribe the rendered output to diff against the expected kept words.

## Corrections by fact-checker
- [corrected] Nyra verbatim benchmark (English): CrisperWhisper 2.0 Pro 93.2 disfluency F1, CW 2.0 90.7, Scribe v2 90.3 (filler 95.5, vocal 83.4, cutoff 80.1), AssemblyAI U3 Pro 67.9 (cutoff 27.9, vocal 2.8), Deepgram Nova-3 57.3, Whisper L-v3 9.7; n=4,957; MIT; built by Nyra → All numbers match the repo. The researcher missed a key caveat: the English set is DisfluencySpeech, in which one speaker re-enacts about 5,000 Switchboard utterances in a studio (9.49 h). The disfluencies are acted, it is one voice, and the audio is studio quality, so the ranking may not transfer to creator phone audio. The AssemblyAI Prompt 1 row uses only a 249-utterance subset, and the German set has only 202 utterances. Inworld STT (84.4 disfluency F1, 84.8 cutoff F1) ranks 4th and was omitted. https://github.com/nyrahealth/nyra_verbatim_speech_benchmark ; https://arxiv.org/abs/2406.08820
- [confirmed] Artificial Analysis normalizes away fillers, so AA-WER measures caption-word accuracy only; MAI-Transcribe-2 2.0%, Scribe v2 2.2%, Gemini 3.5 Transcribe 2.6%, Universal-3 Pro 3.1%, GPT-4o Transcribe 4.0%, Whisper L-v3 4.1%, Nova-3 5.2% → The methodology says it removes bracketed text and filler words ('uh', 'um'). One small fix: the tools table ranks Scribe '4th of ~20', but among the models checked it is second, behind only MAI-Transcribe-2. https://artificialanalysis.ai/speech-to-text/methodology ; https://artificialanalysis.ai/speech-to-text/non-streaming
- [refuted] MAI-Transcribe: word-timestamp support not verified; text-only voter candidate → MAI-Transcribe-2 (public preview, not recommended for production) returns word-level timestamps via modelOptions.timestamps='word'. Its transcribeStyle defaults to 'verbatim', which keeps fillers and false starts. It also offers phrase-list biasing, diarization and 60 languages. That makes it a full timing-capable second voter, although its own verbatim F1 is unmeasured (only 1.5 was benchmarked). https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe
- [corrected] Re-time with MFA 3.0 (~20 ms) as the consensus clock (confidence high) → 19.93/21.75 ms on TIMIT/Buckeye is confirmed, but it comes from MFA's own authors and applies to fluent speech. On disfluent FluencyBank, MFA scores 142 ms, worse than CrisperWhisper's 102 ms (and CW+s 122 ms). Cuts happen exactly in disfluent spans, and cut-offs are partial words that need G2P pronunciations. MFA should be one timing voter, not the authority, and its benefit over snapping should be measured in the bake-off. Downgrade to medium. https://arxiv.org/html/2606.18466 ; https://arxiv.org/html/2607.18934v1
- [corrected] CrisperWhisper 2.0 word timing 29.6 ms TIMIT / 40.6 ms Buckeye / 102 ms FluencyBank → 29.6 and 40.6 appear on Nyra's marketing page (vendor-reported), which has no FluencyBank or MFA rows. The 102 ms FluencyBank figure comes from the arXiv paper's '(ah)+s' variant, which reports 36 ms (not 29.6) on TIMIT. These are different variants from the same authors. https://www.nyra-labs.com/crisperwhisper ; https://arxiv.org/html/2607.18934v1
- [confirmed] CrisperWhisper: code MIT, weights non-commercial, Pro commercial-only → Weights for 1.0, 2.0 and Standard are under the Nyra Health Non-Commercial Research License. Pro is 'available under commercial license only'. The repo has 1.4k stars. https://github.com/nyrahealth/CrisperWhisper
- [corrected] Rev Reverb: repo license covers the code only → The code is Apache-2.0. The weights on Hugging Face are gated with license 'other', and nothing clears them for commercial use, so do not treat Reverb weights as a free commercial option. Separately, Rev AI's paid API offers HUMAN verbatim transcription (English only, 12–24 h turnaround, +$0.50/min verbatim add-on), which the researcher missed. https://github.com/revdotcom/reverb ; https://huggingface.co/Revai/reverb-asr ; https://docs.rev.ai/api/asynchronous/transcribers/
- [confirmed] Scribe v2: word times, audio_event tags, diarization up to 32, keyterms up to 1,000, keep no_verbatim off → Also confirmed: keyterms are capped at 50 characters each and were raised from 100 to 1,000 on 2026-04-02. The researcher missed that timestamps_granularity='character' returns per-character times, that each word carries a logprob, and that the API exposes seed and temperature. no_verbatim also drops non-speech sounds. https://elevenlabs.io/docs/api-reference/speech-to-text/convert ; https://elevenlabs.io/blog/scribe-v2-just-got-an-upgrade
- [confirmed] Only whisper-1 supports timestamp_granularities in OpenAI's STT API → The docs quote: 'The timestamp_granularities[] parameter is only supported for whisper-1.' This also applies to gpt-transcribe and gpt-4o-transcribe-diarize. https://developers.openai.com/api/docs/guides/speech-to-text
- [confirmed] Deepgram filler_words recognizes only seven fixed tokens → The seven are uh, um, mhmm, mm-mm, uh-uh, uh-huh, nuh-uh. The feature is off by default, and with it off 'uh' and 'um' are stripped from the transcript. https://developers.deepgram.com/docs/filler-words
- [corrected] Silero VAD v6 ROC-AUC 0.97 vs v5 0.96, TEN 0.93, WebRTC 0.73; ESC-50 0.87 vs 0.61 → The ROC-AUC figures are confirmed (multi-domain validation set, wiki edited 2026-03-26). The ESC-50 figures are clip-level accuracy on noise-only audio, not ROC-AUC: a clip fails if any ≥100 ms speech run is predicted. On that measure v6 scores 0.87, v5 0.61, TEN 0.42 and WebRTC 0. https://github.com/snakers4/silero-vad/wiki/Quality-Metrics
- [confirmed] StressTest: Gemini-2.5-Pro stress detection F1 48.5, GPT-4o-audio 46.1, StresSLM 86.9; WhiStress→GPT-4o reasoning 83.4 vs Gemini 77.5; humans 92.6 → Also reported: WhiStress alone scores 88.3 detection F1 and StresSLM scores 86.2% on reasoning. Individual humans average 92.6%, and a majority vote of annotators reaches 96.0%. https://arxiv.org/html/2505.22765
- [corrected] ParaPairAudioBench: audio-LLM judges trail humans by ~32 points on style, rate and emphasis → The 32 percentage-point gap is an average across five dimensions (Style, Rate, Emphasis, Age, Gender), and the abstract gives no per-dimension breakdown. SonicBench's 'near random guessing' finding is confirmed, and it adds that frozen audio encoders do capture the cues (≥60% accuracy). https://arxiv.org/abs/2606.24648 ; https://arxiv.org/abs/2601.11039
- [confirmed] Gemini 3.x timestamp drift: 3.1 Pro ~16–17 s on an 11:49 clip, rounded to whole seconds; 3 Flash ~22% fast, −157 s → Reported 2026-03-07. No Google staff reply, and user comments through 2026-08-13 say it is unresolved. Gemini docs add that audio is processed at 1 Kbps (32 tokens/s), frames are sampled at 1 fps by default, and prompts reference MM:SS, so resolution is about one second by design. https://discuss.ai.google.dev/t/bug-gemini-3-flash-and-3-1-pro-progressive-timestamp-drift-in-audio-transcription/129501 ; https://ai.google.dev/gemini-api/docs/video-understanding
- [corrected] TimeLens2: Charades-TL mIoU Gemini 2.5 Pro 52.8, GPT-5 40.5, Qwen3-VL-235B 47.8, TimeLens2-8B 58.6; Gemini 3 Pro 39.7 on VUE-TR-V2 → The Charades-TL numbers are confirmed. The Gemini 3 Pro 39.7 VUE-TR-V2 figure could not be found in TimeLens2. The traceable figure is Vidi2.5's 37.58% IoU for Gemini 3 Pro Preview on VUE-TR-V2, against Vidi2.5 at 49.62% and GPT-5 at 17.15%. TimeLens2-8B scores 47.7 on VUE-TR-V2. https://arxiv.org/html/2607.17423 ; https://arxiv.org/html/2511.19529
- [corrected] Qwen3-ForcedAligner 42.9 ms, but test labels were MFA-generated so it only measures agreement with MFA → 42.9 ms is the average on the MFA-labeled raw set. The model card also reports a Human-Labeled set at 24.8–41.8 ms (Raw 27.8, Raw-Noisy 41.8, Concat-300s 24.8); its language mix is not stated. The model is Apache-2.0, handles ≤5 min and covers 11 languages. https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- [corrected] MediaPipe Face Landmarker: no accuracy figures published → The Blendshape V2 model card reports a mean absolute deviation of about 0.199 on the 0–1 scale (a fairness evaluation on 511 lab subjects). It lists limitations: faces looking away more than 80°, less than 50% visible, or too small, plus 'jittering' under noise, lighting or motion. Blendshape signals need temporal smoothing, and extreme look-aways will drop out rather than register. https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf ; https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
- [unverifiable] Known trap: full-context ASR marks the last word's end 1–2 s late → This is not in the cited kamgasimo cutting.md or the video-use SKILL. video-use says 'Scribe timestamps drift 50–100ms' and uses 30–200 ms padding. kamgasimo says word times 'move by a few tenths of a second, and by more around pauses'. https://github.com/browser-use/video-use/blob/main/SKILL.md ; https://github.com/kamgasimo/ai-video-editor/blob/main/reference/cutting.md
- [confirmed] TimeBolt snaps to ±10 ms of waveform boundaries → This is vendor marketing copy on the TimeBolt homepage, not the features page, and it has no independent measurement. https://timebolt.io/
- [refuted] Canary-1B timing 78 ms TIMIT (tools table) → 78.24 ms TIMIT is the NeMo Forced Aligner figure in the MFA 2026 paper, not Canary-1B, so the two appear to have been conflated. https://arxiv.org/html/2606.18466
- [corrected] Qwen3-Omni-30B-A3B needs 88.5 GB for 30 s video; WorldSense 54.1 → 88.52 GB for 30 s is confirmed; 15 s needs 78.85 GB and 120 s needs 144.81 GB. WorldSense is 54.0 for the Instruct model (54.1 is the Flash variant). License is Apache-2.0. https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct
- [confirmed] AssemblyAI ToS §2.4(f) forbids competitive analysis or benchmarking → AssemblyAI's Terms, Playground Terms and EULA bar use to 'engage in competitive analysis or benchmarking'. The koedesk audit also flags xAI Grok STT's enterprise terms ('benchmark any Services'), and finds no such clause for ElevenLabs, OpenAI, Deepgram or Speechmatics. https://www.assemblyai.com/legal/terms-of-service ; https://github.com/guide-inc-org/koedesk-stt-bench/blob/main/PREREGISTRATION.md
- [corrected] Nyra paper gives AssemblyAI 85.8% event F1 and 92.5% filler F1 → 85.8% event F1 is in the paper, alongside Reverb 86.3% and the Nyra model 90.7%. The 92.5% filler F1 comes from the benchmark's Prompt 1 row on a 249-utterance subset, not the paper. https://arxiv.org/html/2607.18934v1 ; https://github.com/nyrahealth/nyra_verbatim_speech_benchmark
- [confirmed] Deep Video Discovery LVBench 74.2% → 76.0% with transcripts; NumPro Qwen2-VL-7B 12.5 → 31.3 mIoU → DVD was published at NeurIPS 2025. NumPro's 12.5→31.3 figure is ActivityNet, training-free; on Charades-STA it goes 7.9→38.5. https://arxiv.org/abs/2505.18079 ; https://arxiv.org/html/2411.10332v1
- [confirmed] Twelve Labs Pegasus 1.5 (2026-09): 0.43 segmentation F1 vs 0.34 Gemini 3.1 Pro, ≥2 s segments → Released 2026-09-25. The vendor reports 0.4279 vs 0.3370 'overall segmentation performance', and the example request uses min_segment_duration=2. All figures are vendor-reported. https://www.twelvelabs.io/blog/introducing-pegasus-1-5

## Missed items added
- The benchmark behind the ASR choice is one speaker re-enacting ~5,000 Switchboard utterances in a studio (9.49 h). No verbatim benchmark covers real, multi-speaker, phone-recorded monologue, which strengthens the case for an in-house bake-off ([DisfluencySpeech](https://arxiv.org/abs/2406.08820))
- MAI-Transcribe-2 (public preview, Sept 2026) is a timing-capable second voter: word timestamps, a default 'verbatim' style that keeps fillers and false starts, phrase biasing and 60 languages. It is also the best on AA-WER at 2.0% ([MS Learn](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe))
- Scribe v2 offers character-level timestamps (timestamps_granularity='character'), a per-word logprob and seeded decoding. Character times give finer cut edges, and logprob drives disagreement flags without a second vendor ([ElevenLabs API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert))
- Inworld STT ranks 4th on the Nyra verbatim benchmark: 84.4 disfluency F1 and 84.8 cutoff F1, the latter better than Scribe's 80.1. It is weak on vocal sounds (10.7) ([Nyra benchmark](https://github.com/nyrahealth/nyra_verbatim_speech_benchmark))
- Rev AI human verbatim transcription (English only, 12–24 h, +$0.50/min verbatim add-on) is a practical source of gold labels for the bake-off, and an option under a quality-only mandate ([Rev AI docs](https://docs.rev.ai/api/asynchronous/transcribers/))
- Active speaker detection, e.g. TalkNet-ASD (MIT; AVA test mAP 90.8, Columbia F1 96.3), checks whether the visible face is the one speaking. Diarization cannot do this. It is useful when a second face is in frame or a voice comes from off camera ([TalkNet-ASD](https://github.com/TaoRuijie/TalkNet-ASD))
- An audio-video offset check with SyncNet (MIT; outputs offset in frames plus confidence) guards against a constant lip-sync offset from wireless mics. iOS 26 lets AirPods act as a video mic in camera apps. Evidence that this is common in creator footage is anecdotal ([syncnet_python](https://github.com/joonson/syncnet_python); [TechRadar](https://www.techradar.com/audio/earbuds-airpods/how-to-use-airpods-as-a-video-mic-in-ios-26))
- Per-take audio quality scoring with Meta Audiobox Aesthetics (CC-BY-4.0; Production Quality, Content Enjoyment and related axes) informs take choice and audio-polish decisions. It has not been validated for talking-head take selection ([audiobox-aesthetics](https://github.com/facebookresearch/audiobox-aesthetics))
- Gemini audio input is processed at 1 Kbps (32 tokens/s) with 1 fps video and MM:SS references, a second reason not to route fine audio or timing judgments through it ([Gemini docs](https://ai.google.dev/gemini-api/docs/video-understanding))
- The Claude API takes no audio or video input (text, images and PDFs only), so every perception signal Claude uses must arrive as text or images ([prior notes: claude_capabilities.md, citing Anthropic Vision/Models docs](https://platform.claude.com/docs/en/build-with-claude/vision))
- License trap: whisper-timestamped's detect_disfluencies option ships under AGPL-3.0, whose network clause matters for a server; avoid it ([whisper-timestamped](https://github.com/linto-ai/whisper-timestamped))
- Speechmatics tags disfluencies (a remove_disfluencies option exists; tagging is the default) across 17 languages, but it has no public verbatim F1, so it is a candidate for the bake-off only ([Speechmatics docs](https://docs.speechmatics.com/features/word-tagging))

## Tools
- ElevenLabs Scribe v2 [ASR (verbatim, word timestamps, audio events, diarization); Commercial API] https://elevenlabs.io/docs/overview/capabilities/speech-to-text — English DisfluencySpeech: disfluency F1 90.3%, filler F1 95.5%, cutoff 80.1%, vocal sound 83.4%, vWER 3.2% (Nyra benchmark). AA-WER 2.2% (4th of ~20). Word timing 51.3 ms TIMIT / 59.6 ms Buckeye (Nyra). Primary recommendation. Supports keyterms (up to 1,000); keep no_verbatim off; video-use reports 50–100 ms drift, so snap edges.
- CrisperWhisper 2.0 / 2.0 Pro [ASR (verbatim + intended modes, precise timestamps); Code MIT; weights non-commercial (standard) or commercial license (Pro) from Nyra Labs] https://github.com/nyrahealth/CrisperWhisper — Disfluency F1 93.2% (Pro) / 90.7%; timing 29.6 ms TIMIT, 40.6 ms Buckeye, 102 ms FluencyBank — all author-reported (Interspeech 2024 + arXiv 2607.18934). Best measured, but every number comes from its authors; requires commercial licensing.
- AssemblyAI Universal-3 Pro / 3.5 Pro [ASR (promptable); Commercial API (ToS reportedly bars benchmarking)] https://www.assemblyai.com/blog/universal-3-5-pro-async — U3 Pro prompted: filler F1 90.8% but cutoff 27.9%, vocal sound 2.8% (Nyra English); event F1 85.8% on DisfluencySpeech (arXiv 2607.18934); AA-WER 3.1%. U3.5 Pro (Jul 2026) not yet verbatim-benchmarked. Good second voter with an explicit verbatim prompt; audio tags experimental.
- Microsoft MAI-Transcribe-1.5 / 2 [ASR; Commercial API] https://artificialanalysis.ai/speech-to-text/non-streaming — MAI-Transcribe-1.5: filler F1 95.8%, disfluency F1 84.0%, vocal sound 0%. MAI-Transcribe-2: AA-WER 2.0% (top 3). Word-timestamp support not verified; candidate text voter.
- Deepgram Nova-3 [ASR; Commercial API] https://developers.deepgram.com/docs/filler-words — Filler F1 45.7%, disfluency F1 57.3% (Nyra); AA-WER 5.2%; timing 63.3/88.4 ms. Not recommended for verbatim editing.
- OpenAI gpt-4o-transcribe / gpt-transcribe [ASR; Commercial API] https://developers.openai.com/api/docs/guides/speech-to-text — AA-WER 4.0% (gpt-4o) / 3.3% (GPT Transcribe); docs say timestamp_granularities is supported only by whisper-1. No word timestamps, so it cannot place cuts.
- Whisper large-v3 / faster-whisper / WhisperX [ASR + wav2vec2 forced alignment; MIT (Whisper, faster-whisper); BSD-2 (WhisperX)] https://github.com/m-bain/whisperX — Whisper L-v3 filler F1 9.4%, disfluency F1 9.7%; WhisperX word timing 66–110 ms read, 111 ms conversational, 200 ms disfluent. Drops fillers; not suitable as the verbatim source.
- NVIDIA Parakeet-TDT-0.6B-v3 / Canary-1B-v2 [Open ASR; CC-BY-4.0] https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 — Parakeet v3: 6.34% average Open ASR WER, word/char timestamps. Canary 1B v2: filler F1 26.7%; Canary-1B timing 78 ms TIMIT. Parakeet's verbatim behaviour is unmeasured; Canary drops disfluencies.
- Montreal Forced Aligner 3.0 [Forced alignment; MIT] https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner — Word boundary 19.9 ms TIMIT, 21.8 ms Buckeye; phones below 15 ms; beats WhisperX (110 ms) and NFA (78–89 ms) (arXiv 2606.18466). 142 ms on FluencyBank. Needs a verbatim transcript in; re-time words after ASR reconciliation.
- Qwen3-ForcedAligner-0.6B [Forced alignment; Apache-2.0] https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B — 42.9 ms average shift against MFA-labeled references, vs WhisperX 133 ms and NFA 130 ms. Up to 5 min of audio, 11 languages; fallback aligner for non-MFA languages.
- Silero VAD v6 [VAD; MIT] https://github.com/snakers4/silero-vad/wiki/Quality-Metrics — ROC-AUC 0.97 (v5 0.96, TEN 0.93, WebRTC 0.73); ESC-50 noise rejection 0.87 vs 0.61 for v5 (wiki, Mar 2026). About 10k stars. Use with energy/RMS for gap maps and snap points.
- pyannote speaker-diarization-community-1 [Diarization; CC-BY-4.0 (gated)] https://huggingface.co/pyannote-community/speaker-diarization-community-1 — DER on AMI 17.0 vs 18.8 (v3.1) and 12.9 (paid Precision-2); 'exclusive diarization' for reconciling with ASR word times. Only to flag non-creator speech.
- WhiStress [Word stress/emphasis detection; MIT] https://github.com/slp-rl/WhiStress — Interspeech 2025. WhiStress→GPT-4o cascade reached 83.4% stress reasoning on StressTest vs Gemini-2.5-Pro 77.5% and GPT-4o-audio 68.8%. English only (whisper-small.en); 39 stars.
- Parselmouth (Praat) [Prosody DSP (f0, intensity, formants, jitter); GPL-3.0+] https://github.com/YannickJadoul/Parselmouth — Calls Praat's C/C++ code directly, the phonetics reference implementation; 1.3k stars. GPL is fine for server-side use without distribution.
- emotion2vec+ large [Speech emotion recognition; MIT (repo)] https://github.com/ddlBoJack/emotion2vec — 9 categorical classes, 50 Hz frame features, trained on 42.5k hours; last updated 2024; no editing-relevant evaluation. Optional; low evidence of value for cut decisions.
- Google Gemini 3.x Pro (video+audio input) [Video-native multimodal LLM; Commercial API] https://ai.google.dev/gemini-api/docs/video-understanding — VideoMME with audio 89.0 (3.1 Pro); grounding 39.7 mIoU on VUE-TR-V2 (3 Pro); documented timestamp drift of 16–157 s on 12-min audio (developer forum, 2026-03). Use for per-clip judgments only; 1 fps default with configurable fps and clip offsets.
- Qwen3-Omni-30B-A3B [Open audio-visual LLM; Apache-2.0] https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct — WorldSense 54.1; LibriSpeech-clean WER 1.22%; needs 88.5 GB for 30 s of video (BF16). Open fallback judge when no Gemini key is available. Qwen3.5-Omni-Plus (DailyOmni 84.6) is API-only as far as found.
- TimeLens2-8B [Open video temporal grounding; CC BY 4.0] https://arxiv.org/html/2607.17423 — Charades-TL 58.6 mIoU vs Gemini 2.5 Pro 52.8, Qwen3-VL-235B 47.8, GPT-5 40.5 (Jul 2026). Best open grounder, but still second-level error; not needed when the transcript is the spine.
- Twelve Labs Pegasus 1.5 / Marengo 3.0 [Video understanding API / embeddings; Commercial API] https://www.twelvelabs.io/blog/introducing-pegasus-1-5 — Vendor-reported segmentation F1 0.43 vs Gemini 3.1 Pro 0.34; configurable minimum segment (~2 s). Better suited to b-roll library search than take perception.
- MediaPipe Face / Pose / Hand Landmarker [Face, expression, gesture tracking; Apache-2.0 (code)] https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker — 478 3D landmarks plus 52 blendshapes plus head-pose matrix; VIDEO mode; no published accuracy figures. Source of blink, look-away, stillness and gesture events and seam visual delta.
- TransNetV2 [Shot boundary detection; MIT] https://github.com/soCzech/TransNetV2 — F1 96.2 BBC, 93.9 RAI, 77.9 ClipShots; OmniShotCut benchmark range F1 0.814 vs PySceneDetect 0.755. Only for multi-shot uploads or b-roll.
- PySceneDetect [Shot boundary detection (heuristic); BSD-3-Clause] https://github.com/Breakthrough/PySceneDetect — BBC range F1 0.889; weak on gradual transitions (transition IoU 0.18). Adequate for hard cuts only.
- Deep Video Discovery (pattern) [Agentic video index/search architecture; Code released by Microsoft (license not checked)] https://arxiv.org/abs/2505.18079 — LVBench 74.2%, 76.0% with transcripts (NeurIPS 2025). Template for the multi-granular index and its query tools.
- NumPro (pattern) [Timestamp overlay for VLM grounding; Research code] https://arxiv.org/html/2411.10332v1 — Qwen2-VL-7B ActivityNet mIoU 12.5 → 31.3 with overlaid frame numbers (CVPR 2025). Burn timestamps into every contact sheet shown to Claude.
