**Requirements-fidelity critique of the Studio v2 plan, most severe first**

**P0: drifts from the owner's stated requirements**

1. **Fixed caps and a stop-when-no-defects loop trade quality for cost and latency.**
   - S9 runs "only while a P0 or P1 issue remains", so it fixes defects and then stops. It never tries to make an acceptable edit better.
   - The plan also sets hard counts everywhere: S5 has 4 candidates and 3 rounds, S7 has 3, b-roll gets 3 rounds and then "punch-in or nothing", music gets 3 beds, and S9 gets 6 rounds (the doctrine says about 5).
   - Chat edits get "at most 2 render-and-watch rounds scoped to the changed region". Moving a cut changes pacing, music backtiming and caption paging across the whole video.
   - Other cheapness choices:
     - Perception (L4) samples at 15 fps. A blink lasts 100–150 ms, so the plan cannot "cut on the blink".
     - Review renders are 540×960.
     - B-roll is judged from stills on a contact sheet, which cannot show motion, AI morphing or jitter.
     - Director escalates from Opus to Fable, which is a pattern for saving money.
     - A "Flash" Watcher is chosen by default.
   - **Fix:**
     - Stop on convergence: two rounds with no pairwise win, confirmed by a second jury. Keep polish (P2) rounds.
     - Treat the caps only as runaway guards set far above normal use, with N chosen by ablation.
     - After any chat edit, the jury judges the whole video.
     - Perception runs at native fps.
     - The jury watches the actual platform encode at full resolution.
     - Gemini watches the shortlisted b-roll composited into the edit.
     - The Director starts on the bake-off winner at maximum effort, with no cost-driven escalation.

2. **"Invariants" that are really taste rules make the engine rule-bound.** Examples from the validator lists:
   - Speed ≤1.25× and never on the hook. This blocks a deliberate fast-forward gag, and 1.25 conflicts with the ≤1.2 in the op schema and the doctrine.
   - No cut in a gap under 150 ms. This conflicts with the doctrine's "cover the cut" for stutters.
   - No reused clip. This blocks callbacks, loop reprises and a tutorial that shows the result first and again later.
   - CTA retention with no creator override. The doctrine allows one, but the architecture does not, and "memory never overrides validators".
   - −14 LUFS as an invariant while it is also A/B'd against −16.
   - Fixed 30–200 ms padding and 10–30 ms crossfades. These rule out intentional J/L-cuts and tight comedy.

   Worse, **metrics from one calibration take became gates**. "Pro: 1–4 seams per 60 s" is [I], n=1. It is used both in the S7 picture-lock gate and as a P2 exit criterion ("seams per minute and pause medians in the professional range"). A listicle cut per item fails it by design.
   - **Fix:** Define invariants as measured outcomes only:
     - no audible click or discontinuity;
     - no clipped phoneme, checked by the ASR round-trip;
     - A/V sync;
     - licence;
     - tone map applied once;
     - safe zone for the *chosen* platform;
     - the loudness target that was *selected*.
   - Every other rule moves into the doctrine. Metrics feed the critics and are never gates. Phase exit criteria are human preference and invariant compliance only.

3. **BYOK does not match the owner's answer.** The owner asked that creators can paste an "Anthropic, OpenAI **or Google** key … to run their edits on their own model/account". The plan falls short in three ways:
   - Gemini BYOK "waits on legal review" with no path forward.
   - OpenAI-compatible endpoints are "experimental label only", but BYOK is allowed "only with a model certified for that role". OpenRouter and open models therefore never become usable.
   - The Watcher, jury and perception always run on the house key, so "their account" only partly applies. That is a defensible choice for quality, but the owner was never told about it.
   - **Fix:**
     - Offer Google BYOK now through Vertex AI or paid-tier keys, blocking free-tier and EEA free keys at validation.
     - Certify popular OpenRouter models one by one, and add an "uncertified, gated by the house jury" mode.
     - Tell the owner explicitly which roles run on the creator's key and which stay on the house key.
     - Optionally offer an all-LLM BYOK mode in which the Watcher also runs on a creator's Google key.

4. **Keeping the static engine intact is not actually guaranteed.**
   - `backend/studio/` lives in the same service as the old engine. FFmpeg 8, libplacebo, torch and the GPU models would all change the runtime the static engine uses, for example the ffmpeg filter behaviour it calls.
   - Shared Supabase tables and buckets are not fenced off.
   - The tag covers only a commit. `1c07c18` is origin/main HEAD (build 91), but a revert also needs:
     - the Render deploy SHA and image;
     - the `marque-render` bundle;
     - the iOS build that contains ProEditorView.
   - Native Swift cannot be OTA'd, so ProEditorView and the old flows must stay compiled in every future app build, and the kill switch must be server-side. The plan implies both but never states either.
   - **Fix:**
     - Run Studio as a separate service and image.
     - Pin the static engine's image by digest.
     - Give Studio its own tables and buckets and freeze the static schema.
     - Tag all three artifacts (backend, render bundle, iOS build).
     - State that the old iOS editor is never deleted.
     - Test the revert end to end in P0.

5. **The plan is still anchored to the old system.**
   - It ports EndCard, the CTA registry, Watermark, Captions and BrollLayer "as starting points". EndCard contradicts the doctrine's "no outros; end ≤0.5 s after payoff".
   - It seeds the doctrine from kb-2026.11.
   - It keeps `AnalyzeJobResponse` and the old status strings for Studio jobs.
   - It reuses Lambda because the old engine uses Lambda.
   - **Fix:**
     - Build fresh on `@remotion/captions`, and port an old component only if it wins a bake-off.
     - Give Studio its own v2 API, and keep the legacy polling contract only for static jobs.
     - Choose the render host on quality: a self-hosted GPU/Chrome fleet avoids Lambda's limits for 10-min 4K ProRes.

6. **The requested deliverable is missing.** The owner asked for "a PLAN plus a plain-language doc with visuals". The plan only lists six visuals to include, and its text is jargon throughout (NumPro, PoLL, ESTOI, VQQA, CIPHER).
   - **Fix:** Produce the explainer.
     - Use no jargon, or gloss it.
     - Add visuals for:
       - the revert switch;
       - the BYOK data flow (which provider sees what);
       - the caption safe band;
       - the pause policy;
       - one real take going through S0–S11.

**P1: fidelity and "above and beyond" gaps**

7. **Claude-first is underused.**
   - The Claude path hand-rolls `load_skill` instead of using Claude's native Agent Skills, code execution (to measure clips in a sandbox) and context editing.
   - Helpers ("any certified model") spell names and brands for captions, which is visible on screen, so they should use the best model.
   - **Fix:** Use Anthropic-native features on the Claude path and keep the portable layer for the other providers.

8. **Quality levers are banned without being evaluated.**
   - The plan bans eye-contact correction, even though L4 detects "reading notes" and the only remedy offered is a cut. It also bans subtle skin smoothing, speaker upscaling and grain.
   - These contradict "every default is a starting bet".
   - **Fix:** Keep the licence bans. Make the taste bans "off by default, eval-gated, creator opt-in".

9. **Research gaps, measured against "go above and beyond":**
   - Cover frame and thumbnail selection.
   - SRT sidecar files and searchable on-screen keywords.
   - Per-platform *edit* variants: length and pacing, not just encodes.
   - Consented patching of flubbed words in the creator's own voice, with lip-sync, as an eval-gated option.
   - Non-English quality: WhiStress is English-only, the MFA model is English, and caption paging is English-centric.
   - Multi-speaker and podcast doctrine beyond TalkNet.
   - An optional creator intent field (goal, CTA, audience, vibe), instead of "at most one question".
   - Missing paid voice candidate: Adobe Enhance Speech, if API access exists.

10. **HDR delivery is pushed to a P5 experiment.** iPhone HDR is the dominant input, and always tone-mapping to SDR may be the biggest pixel-quality loss in the plan.
    - **Fix:** Move an HDR-versus-SDR phone-viewed bake-off to P3 for platforms that preserve HDR.

11. **Inconsistencies between the documents will harden into rules:**
    - speed cap 1.2 vs 1.25;
    - S9 rounds 6 vs about 5;
    - 7 profiles vs 9 styles;
    - the CTA override present in one validator list and missing from the other;
    - a forced "name a profile" with no allowance for blends or "none".
    - **Fix:** Keep one source of truth for both invariants and profiles, and allow blends.

12. **"Anti-template: vary treatment across creators, checked in QA" adds variance for policy reasons, not quality.**
    - **Fix:** Variation should come from each creator's profile. Drop the variance QA check.

13. **Taste defaults rest on non-comparable evidence.** "No music by default" cites masking studies run at 0 dB SNR, not a bed 18–20 LU under speech. "Lean raw" leans on ads data.
    - **Fix:** State both as open bake-off questions, not defaults.

**P2: building what isn't needed**

14. **Components that should wait until they prove they help quality:**
    - FCPXML and OTIO exports;
    - the reference-video style cards;
    - CIPHER memory before the core cut wins;
    - four music sources, three SFX sources, about ten voice candidates and three AI-video generators all integrated up front;
    - Temporal vs DBOS still undecided.
    - **Fix:** Integrate a component only after a bake-off shows a marginal win. Start with 1–2 per category and defer the exports until a creator asks.

15. **The quality bar sits below the goal.**
    - The P3 exit accepts ≥45% against pro cuts, which means below parity. For "the best possible edit", make pro parity (≥50%) the tracked target.
    - Automatic fallback to the static engine silently ships a lower-quality edit.
    - **Fix:** Tell the creator on fallback, and offer the Studio champion alongside it.