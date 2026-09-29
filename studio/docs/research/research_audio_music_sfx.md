# Voice processing, music and sound design for agentic talking-head short-form editing (Yunicorn engine rebuild)

## Summary
The best-sounding short-form talking-head audio is clean, natural and mostly left alone. The voice should be intelligible and consistent, with no audible edits. Music should stay well below the voice and be left out when it doesn't earn its place. Sound effects should be sparse and felt more than heard.

Claude cannot hear audio. Gemini can, but it downsamples to 16 kbps mono. So the engine has to turn audio into measurements the agent can read: SNR and reverb, noise and music tags, quality scores, loudness curves, and beat and section maps. The agent then runs several processing variants and keeps the one that passes checks. Those checks cover ASR word error, speaker similarity, quality scores, loudness and true peak, clicks at seams, and lip-sync.

Recommended stack:
- Denoising: gated discriminative models first (ClearerVoice MossFormer2 48k, DeepFilterNet3 with an attenuation limit). Add BS-RoFormer only when music is playing in the room. Use generative restorers (Smule Renaissance, Sidon, Resemble Enhance) only behind hallucination guards.
- Loudness: a custom dynamics chain, then linear gain to -14 LUFS with a true peak of -1 dBTP or lower.
- Edits: 10–30 ms crossfades with room-tone fill.
- Music: a licensed API (Epidemic Sound Partner API) or ElevenLabs Music, fitted with beat_this or allin1.
- Exports: always also produce a version without music, because trending sounds can only be added inside the platform apps.

Several licenses rule out popular models: MusicGen, MMAudio, madmom and Essentia models, Meta Denoiser, and Sonniss for an in-app library.

## Verified findings
## 0. Bottom line (post-verification)

The researcher's core design holds up:
- Talking-head audio should be clean and natural, and mostly left alone.
- Music sits well under speech, or is left out.
- Sound effects are sparse.

The key constraint also holds, and is stronger than stated: **no LLM in the stack hears full-band audio.**
- Claude's Messages API still takes no audio input ([SDK issue #1198](https://github.com/anthropics/anthropic-sdk-python/issues/1198)).
- Gemini audio is "Downsampled to 16 Kbps" and multi-channel is "combined to single channel" ([Gemini audio docs](https://ai.google.dev/gemini-api/docs/audio)).
- Qwen3-Omni resamples its input to 16 kHz mel features ([tech report review](https://www.themoonlight.io/en/review/qwen3-omni-technical-report)).

So DSP and ML metrics must judge hiss, sibilance, codec artifacts and anything above 8 kHz. Audio LLMs only flag gross problems.

**Main corrections**
1. The newest challenge evidence (ICASSP 2026 URGENT) favours **hybrid generative+discriminative** enhancement over pure discriminative models.
2. Smule Renaissance is the open-source leader on *singing*; on speech it is only "competitive".
3. **NISQA weights are CC BY-NC-SA**, so NISQA cannot gate production.
4. **Instagram now has an official Audio API** (May 2026) for attaching authorized or trending audio at Reel creation.
5. The Qwen3-Omni *Captioner* takes no text prompt.
6. DeepFilterNet already defaults to DFN3 and compensates for delay by default.

**Main additions**
- Epidemic Sound's **Safelisting API and MCP server**.
- Capture-side levers in iOS 26.
- Commercial isolation APIs as best-of-N candidates.
- Music models trained on licensed data: **Lyria 3.5** and **Stable Audio 3**.
- Evaluation metrics whose licences allow commercial use.

## 1. Capture and ingest (the biggest quality lever)

**Better capture beats any denoiser.**
- iOS 26 adds AirPods "high-quality recording", which Apple likens to a "LAV microphone" ([WWDC25 251](https://developer.apple.com/videos/play/wwdc2025/251/)).
- `AVInputPickerInteraction` gives an in-app input menu with metering and mic-mode selection (same session).
- Apps cannot set Voice Isolation themselves. They can open the system mic-mode UI and read `preferredMicrophoneMode` ([Apple](https://developer.apple.com/documentation/avfoundation/avcapturedevice/preferredmicrophonemode)).
- Add a pre-record noise check that nudges users toward AirPods, a lav mic or a quieter room.

**Apple Audio Mix (verified).**
- `CNAssetSpatialAudioInfo` exposes Cinematic, Studio and In-Frame plus six more modes, with `effectIntensity` from 0 to 1.
- `AUAudioMix` outputs "4 channels of ambience, in FOA, plus one channel of dialog".
- It needs iOS/macOS 26 and a Spatial Audio recording.
- No benchmark exists, so treat the speech stem as one best-of-N candidate.

**Spatial Audio files (verified).**
- iPhone 16 Pro files carry AAC plus an APAC track that ffmpeg cannot decode. Selection by bitrate picks APAC and fails ([immich #30901](https://github.com/immich-app/immich/pull/30901); [ComfyUI #14746](https://github.com/Comfy-Org/ComfyUI/pull/14746)).
- Map the AAC stream explicitly.

**Lip-sync.**
- Keep 48 kHz float throughout.
- ITU-R BT.1359 puts detection at about +45 ms (audio early) and −125 ms (audio late). EBU R37 asks for +40/−60 ms ([summary](https://en.wikipedia.org/wiki/Audio-to-video_synchronization)).
- DeepFilterNet pads for STFT delay **by default**; `--no-delay-compensation` disables it ([enhance.py](https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py)).
- Cross-correlate every neural stage against the original to verify sync.

## 2. Perception layer (text the agent reads)

**Per segment the agent gets:**
- WhisperX word timings (BSD-2).
- **Brouhaha** voice activity, SNR and C50 (MIT, active 2026) ([repo](https://github.com/marianne-m/brouhaha-vad)).
- PANNs AudioSet tags (MIT).
- Quality scores.
- A short-term LUFS curve.
- Sibilance energy, breath flags and clip flags.

These decide *whether* to process at all.

**Quality metrics whose licences allow commercial use.**
- Usable: DNSMOS (CC-BY-4.0), UTMOSv2 (MIT), Audiobox Aesthetics (CC-BY-4.0).
- **Drop NISQA**: its weights are CC BY-NC-SA 4.0 ([NISQA](https://github.com/gabrielmittag/NISQA)).
- Add **VERSA** (Apache-2.0, 63+ metrics) as the evaluation harness ([repo](https://github.com/wavlab-speech/versa)).
- Add SCOREQ (MIT code; weight licence unstated) ([repo](https://github.com/alessandroragano/scoreq)).
- Use SpeechBrain ECAPA (Apache-2.0) for speaker similarity.

**Measure intelligibility on the actual mix.** The engine owns the clean speech stem, so compute **ESTOI(speech stem, final mix)** with pystoi (MIT). This measures music masking directly.

## 3. Voice chain

**Denoiser evidence (updated)**
- **URGENT 2025** (32 submissions) ([paper](https://arxiv.org/abs/2505.23212)):
  - A discriminative model won.
  - Some generative or hybrid systems were preferred in subjective tests.
  - Purely generative models showed language dependency.
- **ICASSP 2026 URGENT** (29 entries) ([paper](https://arxiv.org/html/2601.13531)):
  - "Leading systems dominantly followed a hybrid generative and discriminative paradigm."
  - They outperformed standalone generative or discriminative baselines on both kinds of metric, and the top 6 were confirmed by P.808 listening tests.
  - So best-of-N should include a **hybrid chain** (generative restore, then discriminative refine, or a blend).
- **Smule Renaissance Small** (MIT weights, 48 kHz, 10.5× real time on an iPhone 12 CPU):
  - It "surpasses all open-source baselines on singing" but is only "competitive on speech" ([SRS](https://arxiv.org/abs/2510.21659)).
  - On DNS5, OVRL is 3.18 versus 3.22 for Resemble Enhance.
- **Sidon**:
  - Code is MIT ([repo](https://github.com/sarulab-speech/Sidon)); the weight licence is unverified.
  - Self-reported NISQA is 4.79 versus Miipher's 4.69.
- **ClearerVoice MossFormer2_SE_48K** (Apache-2.0, last push Aug 2025): the primary discriminative candidate.
- **DeepFilterNet3** (MIT/Apache):
  - Unmaintained: v0.5.6 on 31 Aug 2023, last push Oct 2024.
  - The default model *is* DFN3; the CLI help text that says DFN2 is stale.
  - `atten_lim_db` mixes residual noise back in for a natural sound.
- **Music playing in the room**: use BS-RoFormer through python-audio-separator (MIT code; checkpoint `sdr_12.9755`). Check each checkpoint's licence.
- **Commercial candidates for best-of-N** (cost is ignored):
  - **ElevenLabs Voice Isolator API** ([docs](https://elevenlabs.io/docs/overview/capabilities/voice-isolator)):
    - Accepts video files, up to 500 MB or 1 h.
    - "Not specifically optimized" for separating voice from music.
  - **ai-coustics** Lark 2 / Finch 2 ([blog](https://ai-coustics.com/blog/developer-platform-api-playground-sdk)).
  - **Auphonic** ([algorithms](https://auphonic.com/help/algorithms/singletrack.html); [video cutting](https://auphonic.com/blog/2026/04/15/automatic-video-cutting/)):
    - Separate noise, reverb and breath controls, plus mouth-noise removal.
    - A 4× oversampled true-peak limiter.
    - Since April 2026, automatic cutting of silence, fillers and coughs from video.
  - Adobe Enhance Speech: no public API found (weak evidence).
  - No independent blind test ranks these tools, so the gates decide.
- **Excluded**:
  - Meta Denoiser: archived.
  - facebookresearch/demucs: archived; use the adefossez fork (MIT).
  - RNNoise and VoiceFixer: baselines only.

**Chain defaults.** These are craft starting points, not rules.
1. Denoise and dereverb only when SNR or C50 is poor. Limit attenuation to about 12–24 dB. For reverb, use WPE (nara_wpe, MIT).
2. High-pass at 40–100 Hz. Cut boxiness only where measured.
3. De-ess "just enough".
4. Compress by 3–6 dB, then apply a slow leveler so takes match within about ±2 LU (proposal).
5. Attenuate breaths rather than delete them (weak evidence).
6. For speed changes, use pitch-preserving stretch: Rubber Band (GPL-2+ or commercial) or Signalsmith (MIT).

Implement with pedalboard (GPL-3) server-side, or with ffmpeg.

## 4. Seams, room tone, padding

- Use 10–30 ms equal-power crossfades, longer when the edit falls mid-sound.
- Match level and noise floor before joining. Jumps above about 4–5 dB are audible ([ebrary](https://ebrary.net/300232/education/crossfades)).
- Never leave digital silence. Build loopable room tone from the take's own pauses ([Idyll Sounds](https://idyllsounds.com/blog/dialog-editing-how-to-fill-a-scene-with-noise)).
- Padding of 100–300 ms is weakly evidenced. The agent should choose it per style.

## 5. Loudness

- **loudnorm (verified)** ([ffmpeg](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)):
  - Defaults are I −24, LRA 7, TP −2.
  - Linear mode "will revert to dynamic" if its conditions fail.
  - Dynamic mode upsamples to 192 kHz, so set `-ar`.
- **YouTube** "doesn't turn up quieter videos" ([Production Advice](https://productionadvice.co.uk/stats-for-nerds/)).
- **TikTok and IG** targets remain undocumented; only blog posts exist.
- **AES TD1008** ([PDF](https://aes2.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf)):
  - True peak must stay at or below −1 dBTP at the lossy codec input.
  - It also recommends **−18 LUFS for speech** and −16 LUFS for music in audio streaming.
  - So −14 LUFS is a choice made to match platforms, not a standard.
- **Target:**
  - −14 LUFS integrated, true peak ≤ −1 dBTP (−1.5 to −2 for dense mixes).
  - Verify after the AAC encode.
  - Do the dynamics yourself, then apply linear gain (pyloudnorm) and an oversampled true-peak limiter.

## 6. Music

**When to use it.** Default to no music, or to an unfamiliar, low-complexity instrumental.
- Lyrics add informational masking ([Scharenborg & Larson](https://www.isca-archive.org/interspeech_2018/scharenborg18_interspeech.pdf)).
- *Familiar* songs hurt speech recognition more than unfamiliar ones ([Brown & Bidelman 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9599198/)). That study ran at 0 dB SNR, so it shows the direction only.
- TikTok's Kantar "88% say sound is vital" is marketing evidence.

**Level.**
- WCAG 1.4.7's 20 dB rule is AAA and covers **prerecorded audio-only** content, not video ([W3C](https://www.w3.org/WAI/WCAG21/Understanding/low-or-no-background-audio.html)). Treat it only as a proxy.
- Start about 18–20 LU below speech while the person is talking.
- Let the music swell in gaps longer than 1 s, under b-roll, and at the intro and outro.
- Duck fast and release slowly. Carve 2–4 kHz out of the music.
- Let an ESTOI or WER gate on the real mix decide the final level.

**Structure.**
- Cut on speech first, then fit the music: put the energy rise on the payoff, and backtime so a downbeat lands on the last word.
- **beat_this**: MIT code and weights, but "some of the training files are fully copyrighted" ([repo](https://github.com/CPJKU/beat_this)).
- **allin1**: MIT; last push May 2024.
- **madmom**: model files are CC BY-NC-SA ([LICENSE](https://github.com/CPJKU/madmom/blob/main/LICENSE)).
- **PyMusicLooper** (MIT) finds seamless loop points for extending a bed ([repo](https://github.com/arkrow/PyMusicLooper)).

**Sources (verified).**
- **Epidemic Sound Partner API** ([developers](https://developers.epidemicsound.com/)):
  - Catalogue and tools: 55k+ tracks, 250k+ SFX, Soundmatch (analyzes video frames), semantic search, Highlights (5–60 s).
  - Tiers: Free allows 50 downloads with no commercial licence. Scale includes "Sub-licensing for your end-users".
  - **Beats endpoint**: millisecond timestamps with bar position; 1 marks the downbeat ([docs](https://developers.epidemicsite.com/docs/Endpoints/get-track-beats)).
  - **Versions**: adapts a track to a target duration from 1 s to 5 min ([LLM guide](https://developers.epidemicsite.com/docs/soundtracking-with-llm/)).
  - **Safelisting API** ([docs](https://developers.epidemicsite.com/docs/safelisting/)):
    - Protects YouTube channels and individual videos, and Instagram, TikTok and Facebook channels.
    - Requires partnership enablement.
    - Needed because Epidemic registers its tracks in Content ID ([help](https://www.epidemicsoundhelp.com/hc/en-us/articles/26253712691730-Why-was-a-Content-ID-claim-received-from-Epidemic-Sound)).
  - **MCP server (beta)** built for AI agents: search, SFX, `EditRecording` and stems ([MCP](https://developers.epidemicsound.com/docs/mcp/)).
  - Downloads are MP3 at 128 or 320 kbps.
- **Unsuitable for an in-app library**: YouTube Audio Library, Uppbeat (Content ID-registered), Incompetech (CC BY), FMA.
- **Pixabay** forbids "Standalone" redistribution ([license](https://pixabay.com/service/license-summary/)). Baking tracks into renders on the server is fine.

**AI music.**
- **ElevenLabs Music** ([page](https://elevenlabs.io/eleven-music-api); [composition plans](https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans)):
  - "Broad commercial use"; film and TV need Enterprise.
  - Lengths from 3 s to 10 min.
  - **`composition_plan`** sets section durations and styles, so sections can be aligned with the hook and payoff. `force_instrumental` is available.
  - PCM output up to 48 kHz.
- **Lyria 3.5** in the Gemini API (Sep 2026) ([docs](https://ai.google.dev/gemini-api/docs/music-generation)):
  - Instrumental on request.
  - 44.1 kHz WAV.
  - SynthID watermark.
  - Terms need legal review.
- **Stable Audio 3**, open weights (around May 2026) ([card](https://huggingface.co/stabilityai/stable-audio-3-medium)):
  - Trained on 806k AudioSparx-licensed plus 473k Freesound CC recordings.
  - Stability Community License: free under $1M revenue.
  - **Clearer training-data provenance than ACE-Step.**
  - Stable Audio 2.5 is the hosted enterprise option ([Stability](https://stability.ai/news-updates/stability-ai-introduces-stable-audio-25-the-first-audio-model-built-for-enterprise-sound-production-at-scale)).
- **Suno** ([MBW](https://www.musicbusinessworldwide.com/warner-music-group-settles-with-suno-strikes-first-of-its-kind-deal-with-ai-song-generator/)):
  - Settled with WMG on 25 Nov 2025.
  - Free tier cannot download; paid downloads are capped.
- **Udio**: downloads are disabled ([help](https://help.udio.com/en/articles/12683565-changes-associated-with-the-universal-music-group-umg-partnership)).
- **ACE-Step 1.5** ([repo](https://github.com/ace-step/ACE-Step-1.5)):
  - MIT, 12.9k★.
  - Self-claimed quality "between Suno v4.5 and Suno v5", with no benchmark and no training-data disclosure.
  - Needs from 6 GB or less (with offload) to 24 GB or more of VRAM.
- **Excluded**:
  - MusicGen: weights CC-BY-NC.
  - MMAudio: checkpoints CC-BY-NC ([repo](https://github.com/hkchengrex/MMAudio)).
  - HunyuanVideo-Foley ([LICENSE](https://github.com/Tencent-Hunyuan/HunyuanVideo-Foley/blob/main/LICENSE)):
    - Excludes the EU, UK and South Korea.
    - Needs a licence above 100M monthly active users.
    - Bans using its outputs to improve other models.

**Platform-native sounds (corrected).**
- **Instagram**: the **Instagram Audio API** attaches authorized music or trending audio at Reel creation ([Meta docs](https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/)):
  - Business or Creator accounts only.
  - Works only with Facebook Login.
  - The catalogue differs from the app, and there is no preview.
- **TikTok**:
  - The Content Posting API still has no sound picker ([TokPortal](https://www.tokportal.com/learn/tiktok-sounds-api)).
  - Business accounts may attach Commercial Music Library clips through aggregators ([bundle.social](https://bundle.social/tiktok-music-api); weak evidence).
- **Always render a no-music (voice + SFX) export.**

## 7. Sound effects

**Timing and sparsity** come from practitioner blogs only (weak evidence):
- Impacts land on the frame.
- Whooshes straddle the cut, or lead it by 2–4 frames.
- Risers stop dead on the reveal.
- At most one sound per 4–5 s.
- None on serious content.

**Level:** the sources disagree (−6 to −24 dB), so gate on measured loudness relative to speech.

**Licences (verified):**
- Freesound API is "only for non-commercial purposes" without permission ([terms](https://freesound.org/docs/api/terms_of_use.html)).
- Sonniss GDC forbids library or SDK redistribution and AI training ([license](https://sonniss.com/gdc-bundle-license/)).
- ElevenLabs SFX ([docs](https://elevenlabs.io/docs/overview/capabilities/sound-effects)):
  - Maximum 30 s.
  - 48 kHz WAV only for non-looping effects; looping effects are MP3.

**Preferred sources:** Epidemic SFX through the API or MCP; Stable Audio 3 Small-SFX for open-weight generation.

## 8. Agentic audio loop (revised)

1. **Perceive** (Section 2).
2. **Plan.** Claude writes the audio brief.
3. **Best-of-N voice variants:**
   - No-op.
   - DFN3 with an attenuation limit.
   - MossFormer2-48k.
   - The Apple speech stem.
   - BS-RoFormer (when music is present).
   - SRS or Sidon.
   - **A hybrid generative→discriminative chain.**
   - ElevenLabs Isolator, ai-coustics, Auphonic.
4. **Gates.** These are proposals to calibrate:
   - ASR word error rate rises by ≤1 point.
   - Speaker similarity passes a threshold calibrated per embedding model.
   - DNSMOS, UTMOSv2 and SCOREQ do not regress.
   - Loudness is −14±0.5 LUFS and true peak ≤ −1 dBTP after encoding.
   - ESTOI of the speech stem against the mix stays above a floor.
   - No seam shows a spectral-flux spike.
   - No digital silence.
   - Lip-sync is within 0±10 ms.
5. **Listening critic.** Use **Qwen3-Omni-30B-A3B-Instruct** (Apache-2.0) or Gemini for targeted questions. The *Captioner* "does not accept any text prompts" and is recommended for clips of 30 s or less ([card](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Captioner)), so it can only give blind captions.
6. **Expose ops** for music level, duck depth, SFX and the platform-audio choice.

## Verified recommendations
- Invest in capture before denoising. In the iOS 26 app, offer AirPods high-quality recording (bluetoothHighQualityRecording) and AVInputPickerInteraction. Run a pre-record noise check, and open the system mic-mode UI to suggest Voice Isolation, since apps cannot set it themselves. On Spatial Audio recordings, also export Apple's AUAudioMix dialog stem as a candidate.
- Keep the voice chain gated and best-of-N. Measure first (Brouhaha SNR/C50, PANNs tags, DNSMOS/UTMOSv2). Then run a no-op, ClearerVoice MossFormer2_SE_48K, DeepFilterNet3 with atten_lim 12-24 dB, BS-RoFormer when music is detected, and at least one hybrid chain (generative restore, then discriminative refine), as ICASSP 2026 URGENT supports. Add ElevenLabs Voice Isolator, ai-coustics and Auphonic as commercial candidates. Select only on metric gates.
- Use generative restorers (Smule Renaissance, Sidon, Resemble Enhance) only on badly degraded takes, behind ASR-WER and speaker-similarity guards. Do not treat SRS as the speech leader: its open-source lead is on singing.
- Build the evaluation gates from tools whose licences allow commercial use: VERSA, DNSMOS, UTMOSv2, SCOREQ, Audiobox Aesthetics, and ECAPA speaker similarity. Remove NISQA from production gates (CC BY-NC-SA weights). Add ESTOI(clean speech stem, final mix) as an intelligibility gate for music masking. Calibrate every threshold on a labelled Yunicorn set.
- Handle loudness yourself. Apply dynamics, then linear gain to -14 LUFS integrated with pyloudnorm, then an oversampled true-peak limiter at -1 dBTP or lower, and re-measure after the AAC encode. Never rely on single-pass or dynamic loudnorm. Treat -14 as a platform-matching default, not a standard: AES TD1008 says -18 for speech in audio streaming. Measure TikTok and IG behaviour empirically.
- Seams: use 10-30 ms equal-power crossfades, match level and noise floor across takes, fill gaps with room tone looped from the take's own pauses, never leave digital silence, and cross-correlate against the original after every neural stage for lip-sync. Note that DeepFilterNet compensates for delay by default.
- Default to no music or an unfamiliar instrumental bed, starting about 18-20 LU under speech. Let an ESTOI/WER gate on the actual mix decide the final level, not a fixed number. WCAG 1.4.7 covers audio-only content and is only a proxy.
- Integrate Epidemic Sound through its MCP server and Partner API on the Scale tier. Use search, Soundmatch, Beats (downbeat positions), Versions (exact duration), Highlights and stems. Negotiate Safelisting in the partnership so end users' YouTube, Instagram and TikTok channels are protected from Content ID claims.
- For generated beds: use ElevenLabs Music with composition_plan to align sections with the edit's hook and payoff, and force_instrumental. Use Lyria 3.5 when a Google key is supplied. Use Stable Audio 3 (licensed training data, open weights) for self-hosting. Prefer these over ACE-Step 1.5 (undisclosed training data), and exclude MusicGen, MMAudio, HunyuanVideo-Foley and Udio.
- Always render a no-music (voice + SFX) export. For Instagram, integrate the Instagram Audio API (Facebook Login, Business/Creator accounts) so the agent can attach authorized or trending audio at publish time. For TikTok, keep the inbox-draft flow for trending sounds, plus Commercial Music Library attachment for business accounts if it is verified.
- Treat SFX as sparse, event-locked ops, with level measured relative to speech loudness and tuned by A/B tests. Source them from Epidemic SFX or Stable Audio 3 Small-SFX. Do not call the Freesound API commercially or ship Sonniss sounds as an in-app library.
- For an audio 'listening critic', use Qwen3-Omni-30B-A3B-Instruct (Apache-2.0) or Gemini for gross judgments only: masking, mood, abrupt joins. Use the Captioner only for blind captions of clips of 30 s or less. All of these models hear at 16 kHz or below, so fidelity judgments stay with DSP/ML metrics.

## Corrections by fact-checker
- [confirmed] Gemini downsamples audio to 16 Kbps and combines channels to mono, so no LLM can judge fidelity (hiss, sibilance, >8 kHz artifacts). → Confirmed verbatim. It is stronger than stated: the proposed open 'ears' model, Qwen3-Omni, also resamples input to 16 kHz with 128-bin mel features, so it cannot hear above 8 kHz either. As of the latest docs found, Claude's Messages API still has no audio input (open feature request). https://ai.google.dev/gemini-api/docs/audio ; https://www.themoonlight.io/en/review/qwen3-omni-technical-report ; https://github.com/anthropics/anthropic-sdk-python/issues/1198
- [corrected] Qwen3-Omni-30B-A3B-Captioner (Apache-2.0) can serve as the listening critic for gross judgments such as music too loud, mood mismatch or abrupt joins. → The Apache-2.0 licence and the 'low-hallucination' self-claim are confirmed. However, the Captioner 'does not accept any text prompts'. It takes one audio input per call, and clips of 30 s or less are recommended. It can only produce blind descriptive captions and cannot answer targeted questions. For critic questions, use Qwen3-Omni-30B-A3B-Instruct (Apache-2.0) or Gemini. https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Captioner
- [corrected] URGENT 2025 shows discriminative models are the safe/robust default; generative ones are language-dependent. → This is an accurate reading of URGENT 2025: 32 submissions, a discriminative winner, some generative or hybrid systems preferred subjectively, and pure generative systems language-dependent. The newer ICASSP 2026 URGENT challenge (29 valid entries) supersedes it. There, 'leading systems dominantly followed a hybrid generative and discriminative paradigm' and 'consistently outperformed standalone generative or discriminative baselines' on both intrusive and non-intrusive metrics, and the top 6 went through P.808 listening tests. Best-of-N should therefore include hybrid chains, not only single discriminative models. https://arxiv.org/abs/2505.23212 ; https://arxiv.org/html/2601.13531
- [corrected] Smule Renaissance Small was 'highest among open-source' on an extreme-degradation benchmark. → The abstract says SRS 'surpasses all open-source baselines on singing and matches commercial systems, while remaining competitive on speech despite no speech-specific training.' It is not the open-source leader for speech, which is Yunicorn's use case. MIT weights, 48 kHz, 10.4M parameters and 10.5x real time on an iPhone 12 CPU are confirmed. https://arxiv.org/abs/2510.21659 ; https://huggingface.co/smulelabs/Smule-Renaissance-Small
- [corrected] DNSMOS / UTMOSv2 / NISQA licences are CC-BY-4.0 / MIT / MIT. → DNSMOS (DNS-Challenge repo, CC-BY-4.0) and UTMOSv2 (MIT) are correct. NISQA's code is MIT, but its model weights (nisqa.tar, nisqa_mos_only.tar, nisqa_tts.tar) are CC BY-NC-SA 4.0. NISQA therefore should not gate a commercial production pipeline. The same applies to ClearerVoice's bundled NISQA scoring. https://github.com/gabrielmittag/NISQA
- [corrected] DeepFilterNet: the Python default loads DFN2, so DFN3 must be specified; it has a --compensate-delay flag; it is effectively unmaintained (v0.5.6, Aug 2023). → In both v0.5.6 and main, DEFAULT_MODEL = "DeepFilterNet3"; the CLI help text mentioning DFN2 is stale. Delay compensation is ON by default: enhance() has pad=True, and the CLI flag is --no-delay-compensation, which disables it. The unmaintained status is confirmed: last release v0.5.6 on 2023-08-31 and last push 2024-10-17. The MIT/Apache dual licence and the atten_lim_db behaviour are confirmed. https://github.com/Rikorose/DeepFilterNet/blob/main/DeepFilterNet/df/enhance.py
- [refuted] IG API publishing cannot attach Instagram library music; audio_name only labels the embedded audio. → Meta's Instagram Audio API (opened around May 2026) lets apps 'retrieve and search for audio — both original sounds from Instagram Reels and music — and attach them to Reels at creation time' through /ig_audio and audio_configuration. It returns trending audio when no query is given. Limits: Business or Creator accounts only, Instagram API with Facebook Login only (not Instagram Login), a catalog limited to audio 'authorized for third party use' that differs from the app, and no preview. https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/ ; https://www.barchart.com/story/news/3444506/enji-launches-instagram-audio-for-scheduled-reels-a-capability-most-social-media-schedulers-still-dont-offer
- [confirmed] TikTok's API has no sound picker; the workaround is an inbox draft finished in-app. → This is confirmed for the Content Posting API. One nuance: business accounts can attach Commercial Music Library clips (song_clip_id) at creation via TikTok's CML API, as wrapped by aggregators. That evidence is third-party and weak, and CML does not include trending consumer sounds. https://www.tokportal.com/learn/tiktok-sounds-api ; https://bundle.social/tiktok-music-api
- [confirmed] Epidemic Sound Partner API: 55k tracks, 250k SFX, stems, Soundmatch, semantic search, highlights, beat/segment data, versions to target duration; sublicensing to end users on Scale; free tier no commercial licence. → All confirmed. Details: the free tier allows 50 downloads with no commercial licence; Scale includes 'Sub-licensing for your end-users'; the Beats endpoint gives per-beat millisecond timestamps with bar position (1 = downbeat); Versions adapt a track to 1 s–5 min; Highlights cover 5–60 s; track downloads are MP3 128/320 kbps. The researcher missed the Safelisting API and the MCP server (see missed items). https://developers.epidemicsound.com/ ; https://developers.epidemicsite.com/docs/Endpoints/get-track-beats ; https://developers.epidemicsite.com/docs/soundtracking-with-llm/ ; https://developers.epidemicsound.com/docs/api-reference/
- [confirmed] ElevenLabs Music is cleared for broad commercial use on paid plans; film/TV needs Enterprise; trained on licensed data. → Confirmed: 'Film, TV, and large studio game rights require an Enterprise plan'; lengths 3 s–10 min; PCM up to 48 kHz. The researcher left out useful controls: composition_plan (per-section style, duration and negative styles, so sections can be aligned to the edit) and force_instrumental. https://elevenlabs.io/eleven-music-api ; https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans
- [confirmed] Udio downloads disabled since UMG settlement; Suno settled with Warner Nov 2025 with gated downloads. → Udio: 'downloading of audio, video, and stems has been disabled' (article dated 17 Feb 2026). Suno–WMG settled on 25 Nov 2025. Free-tier songs are not downloadable, paid tiers have monthly download caps, and current models will be deprecated for licensed ones. https://help.udio.com/en/articles/12683565-changes-associated-with-the-universal-music-group-umg-partnership ; https://www.musicbusinessworldwide.com/warner-music-group-settles-with-suno-strikes-first-of-its-kind-deal-with-ai-song-generator/
- [corrected] ACE-Step 1.5: MIT, 12.9k stars, self-reported 'between Suno v4.5 and Suno v5', no benchmark, no training-data provenance, under 4 GB VRAM. → The licence, star count, quality self-claim, absence of a quantitative benchmark and absence of training-data disclosure are all confirmed. The README gives VRAM tiers from 6 GB or less (turbo with CPU offload) up to 24 GB or more (optimal), not 'under 4 GB'. An XL 4B DiT was added in April 2026. Stable Audio 3 (licensed data, open weights) is now a lower-risk open alternative. https://github.com/ace-step/ACE-Step-1.5 ; https://huggingface.co/stabilityai/stable-audio-3-medium
- [confirmed] beat_this is MIT for code and weights; madmom models CC BY-NC-SA; MusicGen and MMAudio weights CC-BY-NC; HunyuanVideo-Foley excludes EU/UK/South Korea with a 100M-MAU clause. → All confirmed from the LICENSE files and READMEs. beat_this adds that 'some of the training files are fully copyrighted', leaving the risk assessment to the user. The HunyuanVideo-Foley licence also bars using outputs to improve other AI models. https://github.com/CPJKU/beat_this ; https://github.com/CPJKU/madmom/blob/main/LICENSE ; https://github.com/facebookresearch/audiocraft ; https://github.com/hkchengrex/MMAudio ; https://github.com/Tencent-Hunyuan/HunyuanVideo-Foley/blob/main/LICENSE
- [confirmed] ffmpeg loudnorm defaults I=-24, LRA=7, TP=-2; linear reverts to dynamic if target LRA < source LRA or TP would be breached; dynamic upsamples to 192 kHz. → Confirmed verbatim from the ffmpeg-filters documentation. https://ffmpeg.org/ffmpeg-filters.html#loudnorm
- [corrected] AES TD1008: true peak must not exceed -1 dBTP at lossy codec input; default target -14 LUFS. → The -1 dBTP limit is confirmed. However, TD1008 itself recommends -18 LUFS for speech and -16 LUFS for music in audio streaming. -14 LUFS is a platform-matching choice (YouTube turns loud content down and never turns quiet content up), not a TD1008 recommendation. TikTok and IG targets are still undocumented. https://aes2.org/wp-content/uploads/2024/01/20210924_TD1008_v3.13.pdf ; https://productionadvice.co.uk/stats-for-nerds/
- [corrected] WCAG AAA requires background audio >=20 dB below speech. → The success criterion (1.4.7, AAA) applies to prerecorded audio-only content that is mainly speech, and exempts occasional sounds of 1–2 s. It is not a video requirement, so use it only as a proxy starting level. https://www.w3.org/WAI/WCAG21/Understanding/low-or-no-background-audio.html
- [confirmed] Apple Cinematic framework (CNAssetSpatialAudioInfo/AUAudioMix, WWDC25 session 251) exposes Audio Mix styles with effectIntensity 0-1 and can output a mono speech stem plus FOA ambience. → Confirmed. The session is titled 'Enhance your app's audio recording capabilities'. There are 3 Photos styles plus 6 additional modes; AUAudioMix outputs '4 channels of ambience, in FOA, plus one channel of dialog'; it requires iOS/macOS 26. The same session adds AirPods high-quality recording and AVInputPickerInteraction, which the researcher missed. https://developer.apple.com/videos/play/wwdc2025/251/
- [confirmed] iPhone Spatial Audio files carry an APAC track ffmpeg cannot decode; map the AAC stream explicitly. → Confirmed by the immich PR (iPhone 16 Pro): selection by bitrate picks the APAC track and fails with 'no decoder found'. ComfyUI hit the same failure in 2026. Note that ffmpeg's own 'apac' decoder is an unrelated codec. https://github.com/immich-app/immich/pull/30901 ; https://github.com/Comfy-Org/ComfyUI/pull/14746
- [confirmed] Freesound API commercial use needs permission; Sonniss GDC forbids supplying sounds in software/asset packs and AI training; Pixabay forbids standalone redistribution. → All three are confirmed verbatim from the licence and terms pages. https://freesound.org/docs/api/terms_of_use.html ; https://sonniss.com/gdc-bundle-license/ ; https://pixabay.com/service/license-summary/
- [corrected] ElevenLabs SFX: 30 s maximum, 48 kHz WAV, loopable. → The 30 s maximum and looping are confirmed. WAV at 48 kHz is available only for non-looping effects; looping effects are MP3. https://elevenlabs.io/docs/overview/capabilities/sound-effects

## Missed items added
- ICASSP 2026 URGENT challenge: hybrid generative and discriminative pipelines beat both pure approaches on objective and P.808 subjective scores. This updates the 'discriminative default' conclusion (https://arxiv.org/html/2601.13531).
- Epidemic Sound Safelisting API: clears end users' YouTube channels and videos and their Instagram, TikTok and Facebook channels against copyright claims. It needs partnership enablement. This matters because Epidemic tracks are registered in Content ID (https://developers.epidemicsite.com/docs/safelisting/ ; https://www.epidemicsoundhelp.com/hc/en-us/articles/26253712691730-Why-was-a-Content-ID-claim-received-from-Epidemic-Sound).
- Epidemic Sound MCP server (beta), built for AI agents: search, similar, SFX, EditRecording to a target duration, and stem downloads. It plugs directly into a Claude tool loop (https://developers.epidemicsound.com/docs/mcp/).
- Instagram Audio API (around May 2026): search Meta-authorized and trending audio and attach it at Reel creation (Business/Creator accounts with Facebook Login) (https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/).
- Capture-side iOS 26 levers: AirPods high-quality recording (Apple calls it 'LAV microphone'-like) and AVInputPickerInteraction for in-app mic choice with metering. Voice Isolation can only be offered through the system mic-mode UI, not set by the app (https://developer.apple.com/videos/play/wwdc2025/251/ ; https://developer.apple.com/documentation/avfoundation/avcapturedevice/preferredmicrophonemode).
- Commercial isolation and enhancement APIs to add as best-of-N candidates: ElevenLabs Voice Isolator (/v1/audio-isolation, accepts video, up to 500 MB / 1 h) (https://elevenlabs.io/docs/overview/capabilities/voice-isolator), ai-coustics Lark 2 / Finch 2 API and SDK (https://ai-coustics.com/blog/developer-platform-api-playground-sdk), and Auphonic's April 2026 automatic video cutting of silence, fillers, coughs and music (https://auphonic.com/blog/2026/04/15/automatic-video-cutting/).
- Google Lyria 3.5 in the Gemini API (Sep 2026): instrumental on request, 44.1 kHz stereo, WAV output, SynthID watermark. It fits the 'other provider key' requirement (https://ai.google.dev/gemini-api/docs/music-generation).
- Stable Audio 3 (open weights, around May 2026): trained on 806k AudioSparx-licensed plus 473k Freesound CC recordings, Stability Community License, and a Small-SFX variant. Stable Audio 2.5 is the hosted enterprise API on licensed data, with inpainting and lengths up to 3 min (https://huggingface.co/stabilityai/stable-audio-3-medium ; https://stability.ai/news-updates/stability-ai-introduces-stable-audio-25-the-first-audio-model-built-for-enterprise-sound-production-at-scale).
- Licence-clean evaluation tooling: VERSA (Apache-2.0, 63+ speech, audio and music metrics) (https://github.com/wavlab-speech/versa), SCOREQ (MIT code) (https://github.com/alessandroragano/scoreq), and SpeechBrain ECAPA (Apache-2.0) for speaker similarity (https://github.com/speechbrain/speechbrain).
- Intrusive intelligibility on the final mix: the engine owns the clean speech stem, so it can compute ESTOI(speech stem, final mix) with pystoi (MIT). That measures music masking directly instead of relying on a fixed LU offset (https://github.com/mpariente/pystoi).
- ElevenLabs Music composition_plan: per-section durations and styles let a generated bed's sections line up with the edit's hook and payoff timestamps; also force_instrumental (https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans).
- PyMusicLooper (MIT) finds seamless loop points to extend a licensed bed to the edit's length when a vendor 'versions' endpoint is unavailable (https://github.com/arkrow/PyMusicLooper).

## Tools
- ClearerVoice-Studio (MossFormer2_SE_48K, speech super-resolution, speechscore) [speech enhancement / denoise; Apache-2.0] https://github.com/modelscope/ClearerVoice-Studio — 4.5k stars, last push Aug 2025. The project reports its FRCRN denoiser used more than 3M times on ModelScope. Bundles DNSMOS/NISQA scoring. Primary discriminative 48 kHz candidate.
- DeepFilterNet3 [speech enhancement / denoise; MIT or Apache-2.0 (dual)] https://github.com/Rikorose/DeepFilterNet — 4.8k stars. Paper: 'Perceptually Motivated Real-Time Speech Enhancement'. Last release v0.5.6 (Aug 2023), last push Oct 2024, so effectively unmaintained. 48 kHz WAV only. Use atten_lim_db for natural residual noise and --compensate-delay for lip-sync. The Python default loads DFN2, so specify DFN3.
- python-audio-separator (BS-RoFormer / Mel-Band RoFormer, UVR models) [vocal isolation / dereverb; MIT (code); model weights vary] https://github.com/nomadkaraoke/python-audio-separator — Model table lists BS-RoFormer vocals SDR about 12.9. Active (push Aug 2026). 1.4k stars; UVR GUI has 26k stars. Use when background music is present, to avoid Content ID. Check each weight's license.
- Smule Renaissance Small [generative speech restoration; MIT (weights)] https://arxiv.org/html/2510.21659 — DNS5 DNSMOS OVRL 3.18, vs Resemble Enhance 3.22 and VoiceFixer 3.04. Best among open-source systems on an extreme-degradation benchmark. 10.5x real time on iPhone 12 CPU. 48 kHz; could run on-device.
- Sidon [generative speech restoration; Open code and model (weight license to verify)] https://arxiv.org/abs/2509.17052 — NISQA 4.79 vs Miipher 4.69, DNSMOS 3.30 vs 3.13, speaker similarity 0.979, 48 kHz. Built for dataset cleansing; re-synthesizes via a vocoder, so guard for timbre drift.
- Resemble Enhance [denoise + generative enhancement; MIT] https://github.com/resemble-ai/resemble-enhance — 2.4k stars. DNS5 DNSMOS OVRL 3.22 (in the SRS paper table). Last push Dec 2024. 44.1 kHz; has a --denoise_only mode.
- VoiceFixer [speech restoration (clipping, reverb, bandwidth); MIT] https://github.com/haoheliu/voicefixer — DNS5 OVRL 3.04 (SRS paper). 1.4k stars. 2021-era. Fallback only.
- RNNoise / ffmpeg arnndn [denoise baseline; BSD-3-Clause] https://github.com/xiph/rnnoise — 5.9k stars. Low-complexity baseline. Not a quality leader.
- Auphonic API [commercial full voice chain; Commercial] https://auphonic.com/help/algorithms/singletrack.html — Documented pipeline: denoise, dereverb and breath/mouth-noise removal, AutoEQ, de-esser, adaptive leveler, 4x oversampled true-peak limiter. Has an API. Best available option for mouth clicks and breaths; also a reference for best-of-N.
- Apple Cinematic framework (CNAssetSpatialAudioInfo / AUAudioMix) [on-device voice isolation (spatial audio); Apple SDK] https://developer.apple.com/videos/play/wwdc2025/251/ — WWDC25: can output an extracted mono speech stem plus FOA ambience from iPhone Spatial Audio recordings. No public benchmark. iOS 26+, recordings made with Spatial Audio only.
- nara_wpe [dereverberation (DSP); MIT] https://github.com/fgnt/nara_wpe — Classic WPE implementation. 574 stars. Use only when C50 indicates a reverberant room.
- Brouhaha [perception: VAD + SNR + C50; MIT] https://github.com/marianne-m/brouhaha-vad — Published multi-task model (arXiv 2210.13248). Active 2026. Gates denoise and dereverb decisions.
- PANNs audioset_tagging_cnn [perception: sound event tagging; MIT] https://github.com/qiuqiangkong/audioset_tagging_cnn — 1.8k stars. Widely used AudioSet tagger; Stable Audio Open used it for data curation. Detects background music, wind, keyboard.
- WhisperX [word timestamps / ASR round-trip; BSD-2-Clause] https://github.com/m-bain/whisperX — 24k stars, active. WER-delta gate after processing.
- DNSMOS / UTMOSv2 / NISQA [non-intrusive speech quality metrics; CC-BY-4.0 / MIT / MIT] https://github.com/microsoft/DNS-Challenge — Standard in URGENT and DNS challenges and in the SRS and Sidon papers. UTMOSv2: github.com/sarulab-speech/UTMOSv2; NISQA: github.com/gabrielmittag/NISQA.
- Audiobox Aesthetics [quality metric for speech, music and SFX; CC-BY-4.0] https://github.com/facebookresearch/audiobox-aesthetics — Meta model predicting production quality (PQ), production complexity (PC), content enjoyment (CE) and content usefulness (CU). Scores music candidates and the final mix.
- Spotify pedalboard [DSP chain (EQ, compressor, limiter, VST3 hosting); GPL-3.0] https://github.com/spotify/pedalboard — 6.3k stars, active Sep 2026. JUCE-based. Server-side use only.
- FFmpeg filters (loudnorm, ebur128, deesser, acrossfade, sidechaincompress) [DSP / loudness / mixing; LGPL/GPL] https://ffmpeg.org/ffmpeg-filters.html#loudnorm — Documented behavior: loudnorm linear mode reverts to dynamic, and dynamic mode upsamples to 192 kHz. Set -ar 48000; avoid digital silence.
- pyloudnorm / ffmpeg-normalize [loudness measurement and normalization; MIT / MIT] https://github.com/csteinmetz1/pyloudnorm — BS.1770 implementation, 783 stars (pushed 2026). ffmpeg-normalize 1.5k stars (pushed Sep 2026). Linear gain to target, then a TP limiter.
- Rubber Band / Signalsmith Stretch [pitch-preserving time-stretch for speed changes; GPL-2.0 or commercial / MIT] https://github.com/Signalsmith-Audio/signalsmith-stretch — Established libraries; Signalsmith active Sep 2026. No head-to-head benchmark found. A/B against ffmpeg atempo.
- beat_this [beat/downbeat tracking; MIT (code and weights)] https://github.com/CPJKU/beat_this — ISMIR 2024 'Accurate Beat Tracking Without DBN Postprocessing'. Active May 2026. The README notes some training data is copyrighted.
- All-In-One (allin1) [music structure: BPM, beats, downbeats, sections; MIT] https://github.com/mir-aidj/all-in-one — arXiv 2307.16425. Harmonix 8-fold ensemble. 852 stars; last push 2024. Needs NATTEN; uses Demucs internally.
- madmom [beat tracking (legacy); BSD code; CC BY-NC-SA models] https://github.com/CPJKU/madmom — 1.7k stars, classic. Models are non-commercial; avoid.
- librosa [audio analysis (onsets, tempo, features); ISC] https://github.com/librosa/librosa — 8.6k stars, active. Utility; weaker beat tracker.
- LAION-CLAP [text-to-music/SFX retrieval embeddings; CC0-1.0 (repo)] https://github.com/LAION-AI/CLAP — 2.3k stars; standard audio-text embedding. For searching your own music and SFX library. MuQ-MuLan weights are CC-BY-NC and Essentia models CC BY-NC-SA, so avoid those.
- Qwen3-Omni-30B-A3B-Captioner [audio LLM listening critic / music captioning; Apache-2.0] https://github.com/QwenLM/Qwen3-Omni — README self-reports detailed, low-hallucination audio captions; includes a music-analysis cookbook. Gives the agent 'ears' without a Google key.
- Gemini API audio understanding [audio LLM critic (other-provider key); Commercial API] https://ai.google.dev/gemini-api/docs/audio — Documented: 32 tokens per second of audio, downsampled to 16 kbps mono. Gross judgments only, not fidelity.
- Epidemic Sound Partner API [licensed music + SFX catalog with AI tools; Commercial (sublicensing on Scale tier)] https://developers.epidemicsound.com/ — 55k tracks and 250k SFX with stems. Soundmatch, semantic search, highlights, beat/segment data, track versions. Partners include VEED and Picsart. Free tier: 50 downloads, no commercial license.
- ElevenLabs Music API [AI music generation; Commercial; cleared for broad commercial use on paid plans] https://elevenlabs.io/eleven-music-api — Trained on licensed data; deals with Merlin and Kobalt. Film/TV needs Enterprise. Bespoke instrumental beds of exact length.
- ElevenLabs Sound Effects [AI SFX generation; Commercial API (check plan terms)] https://elevenlabs.io/docs/overview/capabilities/sound-effects — 30 s maximum, 48 kHz WAV, looping, prompt-influence control. Custom one-shots.
- ACE-Step 1.5 [open AI music generation; MIT (code); no training-data provenance disclosed] https://github.com/ace-step/ACE-Step-1.5 — 12.9k stars, active Sep 2026. Self-reported 'between Suno v4.5 and Suno v5' with no benchmark in the README. BPM, key and duration control; under 4 GB VRAM. Legal review needed before commercial use.
- Stable Audio Open 1.0 [open text-to-audio (SFX, textures, short music); Stability AI Community License (free commercial use under $1M revenue)] https://huggingface.co/stabilityai/stable-audio-open-1.0 — Trained on 486k CC0, CC-BY and CC Sampling+ recordings, filtered with Audible Magic. Stereo 44.1 kHz, up to 47 s. Better suited to SFX than to music beds.
- AudioCraft / MusicGen [open AI music generation; MIT code; CC-BY-NC weights] https://github.com/facebookresearch/audiocraft — 23.6k stars. Non-commercial weights; exclude.
- MMAudio / HunyuanVideo-Foley [video-to-audio Foley; CC-BY-NC weights / Tencent Hunyuan Community (excludes EU, UK, South Korea)] https://github.com/Tencent-Hunyuan/HunyuanVideo-Foley — Hunyuan self-reports MovieGen-Audio-Bench MOS 4.14 vs MMAudio 3.58. Not needed for talking heads; licensing blockers.
- Freesound (CC0 subset) [SFX source; Per-sound CC0/CC-BY; API non-commercial without permission] https://freesound.org/docs/api/terms_of_use.html — Largest CC SFX corpus; source of Stable Audio Open's training data. Curate a CC0 pack offline; don't call the API commercially.
- Pixabay Music & SFX [free music/SFX source; Pixabay Content License] https://pixabay.com/service/license-summary/ — Free commercial use, no attribution; no standalone redistribution. Server-side bake-in only. The API covers images and video only.
- Sonniss GDC bundles [SFX source; Royalty-free; no redistribution in software or asset packs; no AI training] https://sonniss.com/gdc-bundle-license/ — Pro-grade recordings. Cannot be an in-app library.
- Incompetech / Uppbeat / YouTube Audio Library [free music sources; CC BY 4.0 / freemium with credit / YouTube-centric terms] https://incompetech.com/music/royalty-free/faq.html — Widely used by creators. Uppbeat tracks are registered in Content ID. Not suited to a cross-platform in-app library without partnerships.
