**Simplicity and reuse review: the smallest design that still gets maximum quality**

Items are ordered by how much each one affects quality or engineering risk.

**P0: fix before building**

1. **Too many selection layers stacked on top of each other.**
   - The plan chains four story cuts, list-wise jury ranking, a pairwise confirmation, fusion, three rounds of fine-cut metrics, per-specialist rounds, and six render-and-watch rounds.
   - Each layer depends on judges that agree with humans only 64–81% of the time. Stacking them compounds that noise and can drift the cut toward whatever the judges prefer.
   - Fusion ("B's hook into A's body") also creates continuity seams the plan never checks.
   - **Fix:**
     - Baseline is one Director, one cut, and the champion render-and-watch loop.
     - Generate divergent candidates only for the opening: 2–3 hooks, which is also a deliverable.
     - Drop fusion. The Director can already `choose_take` and `reorder_beats`.
     - Add N story cuts back only if the §8 ablation shows a positive win.

2. **Metrics are used as gates, which contradicts "rules must not drive edits".**
   - The picture-lock gate includes "fine-cut metrics": seams per minute of 1–4, pause medians and similar.
   - A multi-take ramble needs more seams. Gating on 1–4 pushes the Director to keep flubs, which is the 19-splice failure in reverse.
   - **Fix:** only invariants gate. Every §8 metric goes to the critic as advice.

3. **The payoff/CTA validator can't be computed by code.**
   - "No removal of a CTA or payoff the creator actually said" needs a model to decide what the payoff is.
   - **Fix:** the brief pins CTA and payoff word IDs, and the validator checks that pinned words are still present. That makes the check deterministic.

4. **The numbers contradict each other.**
   - Speed cap: validator ≤1.25×, operation ≤1.2×, doctrine about 1.1× with a hard cap of 1.2×.
   - Revision rounds: 6 in the architecture, about 5 in the doctrine.
   - Sync tolerance: ±10 ms for lip sync, +5/−15 ms in QA.
   - −14 LUFS is written as a validator but also listed as an A/B.
   - The toolchain says every top aligner is within one frame, yet MFA is still required for captions.
   - **Fix:** keep one `constants.yaml` that both the validators and the skill pack read. The architecture and toolchain docs point to it and don't restate the numbers.

5. **One structured-output schema for all operations will be rejected.**
   - This has already happened in this codebase. `backend/app/edl.py` (next to `CHAT_TWEAK_OP_TYPES`) documents that the full op union was rejected as "schema too complex". Every chat tweak then silently fell back to canned text.
   - The plan's `apply_ops` with `strict:true` over roughly 20 operation types will hit the same limit, on every provider.
   - **Fix:** one tool per operation family: cut, overlay, caption, audio, grade. Each gets a small schema.

**P1: cut or merge (duplicated or not needed)**

6. **The perception stack is too big for one person talking to a phone.**
   - Keep:
     - Scribe v2;
     - the Silero plus RMS acoustic snapper;
     - Parselmouth f0 and intensity z-scores;
     - MediaPipe Face Landmarker (blinks, look-away, face and chin boxes);
     - FFmpeg `signalstats`/`blurdetect`.
   - Drop from the default path:
     - WhiStress (39 stars, English only; Parselmouth z-scores cover emphasis);
     - emotion2vec;
     - Pose/Hand landmarkers;
     - TalkNet;
     - Brouhaha plus Audiobox at ingest;
     - SyncNet on every take.
   - SyncNet only needs to run when ffprobe finds variable frame rate or an edit list. An iPhone take has no constant offset otherwise.
   - L6 "recommended way to hide the cut" is a Director judgment, not a perception layer.

7. **Word timing is over-built.**
   - The plan has two ASR voters, a three-way timing vote, re-decoding of disputed slices, MFA, Qwen3-FA and CrisperWhisper.
   - Cut edges are set by the acoustic snapper anyway, and the plan's own note says all top aligners are within a frame.
   - **Fix:**
     - Scribe character times, then the snapper, then an ASR round-trip diff on the render.
     - Add a second voter only if the bake-off shows Scribe misses fillers or cut-offs on real Yunicorn takes.
     - Caption timing uses Scribe times. MFA is a fallback, not a pass.

8. **Voice best-of-N is a pile of metrics.**
   - Nine candidates judged by five MOS predictors that disagree with each other can pick artifacts.
   - **Fix:**
     - Measure first. When SNR and C50 are fine, skip enhancement and apply only the pedalboard chain.
     - Otherwise run two candidates: MossFormer2_48K and one paid tool picked by a human listening bake-off.
     - Gate on WER Δ≤1, ECAPA speaker similarity, and one MOS.
     - Drop nara_wpe, the Sidon/Resemble hybrid and BS-RoFormer unless the bake-off earns them back.

9. **Three critic channels overlap.**
   - Gemini appears as the L5 Watcher, as a jury member and as the S9 watcher.
   - Claude also judges frames, crops and caption stills.
   - **Fix:** one `watch(clip_ids, question)` tool used for both perception and review. The jury is Gemini watch plus one frame judge from a family other than the Director's.

10. **Duplicate sourcing.**
    - Music: Epidemic, ElevenLabs Music, Lyria 3.5 and self-hosted Stable Audio 3. **Fix:** Epidemic only (Safelisting, downbeats, Versions and stems cover every need). Generation is a fallback added later only if an eval shows a gap.
    - Stock: Shutterstock plus contracted Storyblocks, Adobe and Getty, plus Pexels and Pixabay. **Fix:** Shutterstock plus Pexels.
    - Retrieval: a Qwen3-VL embedder and reranker on top of a Claude contact-sheet judge is two ranking layers for 50–200 candidates. **Fix:** embedder top-20, then the Claude threshold judge. Add the reranker only if the hit-rate bake-off favours it.

11. **Harness infrastructure.**
    - Temporal is heavy infrastructure for a job the Cut Document already makes resumable. The operation log is event-sourced, and perception artifacts can be cached by content hash.
    - **Fix:** use DBOS on the existing Postgres (Supabase), or plain checkpointing from the last document version plus cached artifacts.
    - Drop FCPXML: OTIO adapters emit it, and creators don't need it in v1.

12. **Preview in the iOS editor.**
    - The plan uses an AVFoundation composition, a WKWebView running the React overlays, Lottie and Rive, plus parity CI. That is four rendering paths and one permanent drift problem.
    - **Fix:**
      - The phone composites only A-roll cuts, speed and audio natively.
      - The server renders captions and graphics for the changed region as an alpha overlay track (ProRes 4444 or HEVC-alpha), using the same Remotion code.
      - Parity then holds by construction, and WKWebView, Rive and the parity CI all go away.
    - Latency doesn't matter to the owner, so this trade is worth making.

13. **Agent-authored `custom_graphic`** (JIT-compiled React plus Zod) is the largest source of failures for the least evidence.
    - **Fix:** v1 uses templates plus Lottie only.

14. **Defer to a later phase:**
    - the HDR experiment;
    - reference-video style cards;
    - the Hormozi/Abdaal/DOAC signatures (n=3–4);
    - LMM-EVQA and AgenticVBench;
    - SeedVR2/FlashVSR;
    - photoreal Veo/Omni hero shots;
    - BiRefNet text-behind-subject;
    - Rubber Band vs Signalsmith and 1080 vs 1440 bake-offs;
    - Gemini and OpenAI-compatible BYOK. v1 BYOK is Anthropic plus OpenAI in the Director slot only.
    - Seven content-type profiles plus nine style files is duplication. Merge them into the nine `styles/` files.

**P1: reuse from the existing codebase instead of rewriting**

15. **Checks are already written.** These are all in `/Users/home/Marque-wt/editor-audit/backend/`:
    - `app/edit_lint.py` covers static windows, metronomic cutting, same framing on adjacent shots, anchor drift, caption coverage, reading rate, a static open, breathing after a peak, and a complete ending.
    - `eval/invariants.py`, `eval/cut_qc.py`, `eval/audio_qc.py` and `eval/layout_qc.py` hold invariant and QC checks.
    - `eval/pro_cut_reference.py` holds the 1-vs-19 calibration, and `eval/golden.py` plus `corpus.json` hold the golden set.
    - **Fix:** port these to word-ID addressing to become the §8 metrics packet and validators. Don't rewrite them.

16. **Operation vocabulary and mutator.**
    - `TWEAK_OP_TYPES` in `app/edl.py` already covers about 80% of the new operations: `cut_range`/`restore_range`, `set_segment_speed`, `add_punch_in`, `add_broll`/`set_broll_rect`, `set_split_fraction`, `edit_caption`, `set_music`, `mute_range`, `reorder_segments`, `undo`.
    - Its single-mutator pattern ("the only thing that mutates the EDL") is exactly the "one writer" rule.
    - **Fix:** keep the names and semantics, change addressing from milliseconds to word IDs, and keep the 150 ms / padding invariants inside the mutator.

17. **Renderer components.**
    - Port Captions, BrollLayer, PunchZoom, TextCardOverlay and Watermark.
    - Do not port as defaults:
      - EndCard, which conflicts with the doctrine's "no outros, end within 0.5 s of the payoff";
      - Grade, replaced by the FFmpeg grade;
      - AudioMix, since audio now lives outside Remotion.
    - Nine compositions (TalkingHead, BrollCutaway, SplitThree and others) collapse into one composition driven by layout operations.
    - `app/audio.py` already has `seam_declick_filter` and loudness helpers. Reuse them inside the new FFmpeg audio graph.

**P2: what a top human editor does that the plan misses**

18. **Ask for a re-recorded line.** A professional will ask for one line again rather than restore it. When the payoff is missing, the audio is unrecoverable, or a key line is flubbed with no later re-delivery, the Director should request a spoken pickup of that line. This beats any restoration model. The plan asks for pickups only for b-roll.

19. **Morph-cut option at seams.** When head position barely changes across a mid-thought seam, a 3–6 frame optical-flow blend can hide the jump. RIFE is already in the stack. Offer it as one seam treatment next to punch-in and cutaway. It can look uncanny, so gate it by A/B.

20. **Phrase-level comping across takes.** Choose the best delivery per sentence, not per take, and check continuity at each join: lighting, wardrobe and position deltas from the existing L4 face boxes.

21. **Cover frame and first frame.** Choose the Reels/TikTok cover frame and make frame 0 a strong still: face visible, eyes open, hook title on screen. This is a professional deliverable and costs almost nothing.

22. **Phone-speaker mix check.** Run the ESTOI/WER intelligibility gate a second time on the mix through a phone-speaker simulation (a high-pass around 300 Hz). A bed that sits right on AirPods can mask speech on a phone speaker or vanish entirely.

23. **One continuous final watch.** After the last round, do one whole-clip watch at 1× on the final encode, with no ±1 s seam slices. Ask only "anything that pulls attention from the speaker?", the doctrine's master test.

**Minimal design that keeps maximum quality**

- **Ingest:** a single tone map into the mezzanine. **Perception:** Scribe, the snapper, Parselmouth and MediaPipe. **Voice:** a measure-gated chain of two candidates at most.
- **Director (Claude):** the brief, 2–3 hook alternates, then one cut using per-family operation tools under the ported `edl.py` mutator. Invariants and pinned words gate; the ported `edit_lint` metrics advise.
- **Finishing:** the specialists stay as skills loaded into the Director, not separate agents. Add separate agents only if an ablation shows fresh context wins.
- **Review:** the champion render-and-watch loop with Gemini watch plus one cross-family frame judge.
- **Rendering and output:** Remotion (the ported components, one composition), the FFmpeg audio graph, our own mux, per-platform encodes, and the ported QC.
- **Editor:** native A-roll preview plus server-rendered alpha overlays.
- **Infrastructure and eval:** checkpointing on Postgres; human pairwise evaluation as the only arbiter of whether any removed piece comes back.