1. **P0. Remotion should not composite the A-roll, and Lambda cannot hold the mezzanine.**
   - **Risk:** Every A-roll pixel goes through Chrome at 8 bits. That is two YUV↔RGB conversions, with the source frames extracted at 8-bit and composited in 8-bit sRGB. Punch-ins from 4K get scaled by Chrome's image scaler. So the 10-bit grade mostly gains nothing, and banding and softness get in.
   - **Risk:** OffthreadVideo downloads the whole source file, and Lambda has 10 GB of disk (verified). 4K60 ProRes 422HQ runs about 13 GB per minute, so the S1 mezzanine is infeasible on Lambda even for a one-minute take.
   - **Fix:** FFmpeg/libplacebo builds the A-roll track at 10–16 bit: tone map straight from the original, grade, punch/reframe with an EWA-Lanczos scaler, and cut in full-screen b-roll.
   - **Fix:** Remotion renders only captions, graphics and PiP, as a ProRes 4444 alpha layer at output resolution. Composite once in FFmpeg and convert to 8-bit once, with error-diffusion dither. Add a CAMBI banding gate (it ships in libvmaf).
   - **Fix:** Render on self-hosted boxes. This also removes the double tone-map risk.

2. **P0. Frame and sample quantization drift.**
   - **Risk:** At 59.94 fps a frame is 800.8 samples at 48 kHz. Speed segments make the source-to-output frame mapping non-integer. If each segment is rounded on its own, 20–30 cuts add up to audible drift. This is the same class as the old engine's pyRound drift.
   - **Fix:** The compiler keeps output time as a cumulative rational. It snaps each edit point to the output frame grid once, and takes the audio sample positions from those same instants. Keep a rational fps end to end.
   - **Fix:** Add a 10-minute test fixture: 59.94 fps, speed segments, 30 cuts, with beep/flash markers at the start, middle and end.

3. **P0. The sync gates claim more precision than the tools have.**
   - **Risk:** SyncNet measures offsets in whole video frames at 25 Hz, so ±40 ms (verified). It cannot check ±10 ms or +5/−15 ms. Gemini's audio is 16 kbps mono (verified), so it cannot judge sync either.
   - **Fix:** Get source sync right by honouring MOV edit lists and AAC priming (never `-ignore_editlist`). Keep clap-test fixtures for each iPhone model, camera, HDR mode and Spatial Audio.
   - **Fix:** Use SyncNet only to flag offsets of 40 ms or more.
   - **Fix:** Change the voice-chain "±10 ms lip sync" gate to "cross-correlation lag between processed and original audio ≤1 ms", which can actually be measured.
   - **Fix:** Check render sync by comparing the output audio against the compiled audio graph, sample-exact.

4. **P0. None of the audio critics can hear the problems that matter.**
   - **Risk:** Claude cannot hear. Gemini gets 16 kbps mono. DNSMOS, UTMOSv2, SCOREQ and Audiobox Aesthetics all score audio at 16 kHz. Nothing checks sibilance, high-frequency hiss or bandwidth loss from the enhancers, seam clicks, ducking pumping, or masking on phone speakers.
   - **Fix:** Add full-band deterministic checks:
     - a derivative/HF-burst click detector at ±5 ms around every seam;
     - a spectral-rolloff comparison against the original;
     - the 5–9 kHz sibilance ratio;
     - the gain-reduction rate of the ducking envelope;
     - inter-sample peaks;
     - a mono-fold check.
   - **Fix:** Run a human listening panel on phone speaker and earbuds for every voice and music bake-off, plus a weekly production sample.
   - **Fix:** Generative restorers (Sidon, Resemble Enhance) run only below measured SNR/C50 thresholds, and must also pass a word-level ASR diff, because they can hallucinate phonemes.
   - **Fix:** Pick one voice chain per recording session, not per take. Then match long-term average spectra across takes, so take seams don't jump in timbre.

5. **P1. The champion loop accepts noise as wins, and critics always find something.**
   - **Risk:** Pairwise judges agree with humans 64–81% of the time. Over 6 rounds, a worse revision will "win" at a real rate. LLM critics almost always raise a P1. Reopening picture lock from S8 can start a cycle through every specialist.
   - **Fix:** Accept a revision only if it wins in both orders from at least 2 families, with a tie option and no deterministic metric regressions.
   - **Fix:** A P0 or P1 counts only if a metric or a second critic confirms it.
   - **Fix:** Also compare against the picture-lock version to detect drift.
   - **Fix:** Allow at most one reopen per job, and only for a P0. Re-anchor downstream items by word ID and recompute the music backtiming in code.
   - **Fix:** Qualify the rubric grader by Cohen's kappa and recall on the fail class, not raw 90% agreement. Binary items that almost always pass make 90% trivial.

6. **P1. Caption timing asks for more precision than the aligner has.**
   - **Risk:** MFA is off by about 142 ms on disfluent speech. "Lead by 0–1 frame, never lag" at 60 fps means 16 ms, which is out of reach.
   - **Why the lead is safe:** ITU-R BT.1359 puts detectability at +45 ms with audio early and −125 ms with audio late. A highlight that appears early is the tolerant direction.
   - **Fix:** Lead by 2–4 frames. Snap each word onset to an acoustic-onset detector within ±80 ms of the aligned time.
   - **Fix:** QA caption timing in code, from the render plan against measured onsets, not by critics.
   - **Fix:** Align per VAD utterance. Pre-seed pronunciations for names and brands, because G2P fails on them.

7. **P1. Caption and title collisions after transforms.**
   - **Risk:** Collision checks on source face boxes break once punch-ins or reframes move the face. In selfie framing the head usually sits around y 250–700, so the hook-title band (y 288–600) lands on the forehead or eyes.
   - **Fix:** Check placement against per-frame landmarks after the compiled transforms.
   - **Fix:** Anchor placement per section with hysteresis, since erratic caption movement is disliked.
   - **Fix:** When no position fits, fall back in this order: smaller text → lower band → text-behind-subject via matte → fold the title into the captions.
   - **Fix:** OCR every b-roll frame to catch collisions with burned-in text.

8. **P1. The "no cut in gaps under 150 ms" invariant blocks legitimate filler removal.**
   - **Risk:** Coarticulated fillers like "and-um-so" have gaps under 150 ms, so the doctrine's "remove fillers aggressively in the hook" becomes impossible.
   - **Fix:** Also allow cuts at stop-closure or low-energy phone boundaries of 20 ms or more, with 5–10 ms crossfades. Gate them with the click detector and an ASR round-trip over the seam. Keep 150 ms as the default wherever the seam is visible.

9. **P1. The durable harness will hit hard limits.**
   - **Risk:** Temporal limits each payload to 2 MB and history to 50 MB (verified). Provider-native histories full of frames, contact sheets and the Take Index will exceed them.
   - **Risk:** Any client-side history trimming, such as a Pydantic AI history processor dropping images, breaks preserved thinking on Opus 5.5 and Fable 5.1. Accounts created on or after 2026-08-31 get a 400 on edited history.
   - **Risk:** The server-side refusal fallback to Opus 5 runs without the Opus thinking.
   - **Fix:** Use Temporal External Storage (claim-check) for all payloads. Send images by Files API ID or URL, not base64. Use child workflows per stage.
   - **Fix:** Use only server-side compaction or context editing. Add a contract test that no earlier turn is ever edited.
   - **Fix:** Set effort explicitly on every call, because Opus 5.5 defaults to medium.

10. **P1. Takes and inserts that don't match.**
    - **Risk:** Fusing takes (B's hook into A's body) changes framing, exposure and white balance at the seam. The doctrine forbids scale jumps under 10% but gives no operation to fix them.
    - **Risk:** A full-frame MKL colour transfer between unlike content (a beach stock clip onto a beige room) creates colour casts.
    - **Fix:** At take boundaries, normalise face scale and position with a reframe, or punch in deliberately by 15–20% or more.
    - **Fix:** Match takes on face-mask and neutral regions only.
    - **Fix:** For b-roll, match exposure, white balance and contrast only, with MKL at 0.5 strength or less. Reject clips whose ΔE to the look target stays too large.

11. **P1. B-roll judged from contact sheets misses failures that happen over time.**
    - **Risk:** A logo, text or face can appear mid-clip, the subject can drift out of the 9:16 crop, RIFE can warp motion, and SeedVR2 can hallucinate.
    - **Fix:** Sample every 0.25–0.5 s across only the in/out range actually used.
    - **Fix:** Make OCR, logo and face detectors hard gates.
    - **Fix:** Track the subject box to confirm it stays inside the crop throughout; if it doesn't, switch to a split or PiP layout.
    - **Fix:** Keep native cadence by repeating frames when conforming 24/30 fps to 60. Make RIFE a gated candidate with a warp check.
    - **Fix:** Use SeedVR2 only on content with no faces or text; otherwise reject low-resolution clips.

12. **P1. The speed plan as written won't do what it says.**
    - **Risk:** FFmpeg's `rubberband` filter has no option to select the R3 "finer" engine (checked against the options list).
    - **Risk:** At 1.1× on 60 fps, every 11th frame is dropped. The Watcher samples at 1–5 fps and cannot see that judder.
    - **Fix:** Run R3 through the rubberband CLI (`--fine`) or librubberband directly, compensating for its start delay and producing a sample-exact length.
    - **Fix:** Add deterministic temporal QC: dropped and duplicated frames, freezedetect, and an SSIM cadence check. Prefer segments whose edges fall on cuts.

13. **P2. Preview parity will not reach pixel level.**
    - **Risk:** A WKWebView overlay synced to AVPlayer through a JS bridge lags 1–2 frames. CoreText and Skia measure text differently. Creators will then "fix" timing that was correct.
    - **Fix:** Render captions natively with AVSynchronizedLayer from the explicit caption plan and the same font files.
    - **Fix:** Test parity on geometry and timing (±1 frame), not on pixels.
    - **Fix:** Preview `custom_graphic` items as server-rendered alpha clips.

14. **P2. Agent-written `custom_graphic` code.**
    - **Risk:** The output can vary between renders (Math.random, Date, font-load races). It is arbitrary code running in the render path. The results tend to look amateur.
    - **Fix:** Sandbox it and lint it for determinism.
    - **Fix:** In v1, limit it to a vetted library of parameterised primitives, and check the render at several frames.

15. **P2. Music licensing and level.**
    - **Risk:** Safelisting is usually tied to a connected channel. An unverified destination gets claims or muting.
    - **Fix:** Check safelist status for each destination before baking in catalogue music. Otherwise use a generated bed, or deliver the no-music master and add a native sound at publish.
    - **Risk:** A bed 18–20 LU under speech can disappear on phone speakers.
    - **Fix:** Set the bed level using a phone-speaker simulation (band-limited) as well as the ESTOI gate. At that level ESTOI is almost always near its ceiling, so it rarely binds.

16. **P2. Ingest edge cases.**
    - **Risk:** Dolby Vision 8.4 with the RPU applied is libplacebo's least-exercised path. Real uploads also include Apple Log (ProRes Log), Cinematic mode, and mixed HDR/SDR or 30/60 fps takes within one job.
    - **Fix:** Add a pure-HLG (RPU off) arm to the tone-map A/B.
    - **Fix:** Add an Apple Log path using Apple's official LUT.
    - **Fix:** Tone-map each take separately before matching, and conform everything to one timeline frame rate.

Sources checked:
- [Gemini audio docs](https://ai.google.dev/gemini-api/docs/audio)
- [FFmpeg rubberband options](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/rubberband.html)
- [Remotion Lambda disk](https://www.remotion.dev/docs/lambda/disk-size)
- [OffthreadVideo download behaviour](https://rendercomp.com/blog/remotion-video-embedding-offthreadvideo-guide/)
- [SyncNet granularity](https://arxiv.org/pdf/2303.00502)
- [Temporal limits](https://docs.temporal.io/troubleshooting/blob-size-limit-error)
- [Temporal External Storage](https://docs.temporal.io/external-storage)
- The claude-api skill's migration notes: Opus 5.5 effort default, preserved-thinking history check, 2576 px vision.