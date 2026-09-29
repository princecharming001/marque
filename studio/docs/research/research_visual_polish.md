# Visual polish for phone-shot talking-head shorts: color/HDR, framing, stabilization, effects, transitions, and the best open-source stack

## Summary
For talking-head shorts, visual polish mostly means avoiding damage, and the research is strong on that point. In a Snap study of 90,000 videos, standard video-quality scores barely tracked engagement (r≈0.07–0.31). So the first job is to never make the footage worse: no flicker, identity drift, washed-out HDR, over-sharpening or cheesy transitions. The second job is to keep takes consistent with each other. After that, the gains come from good framing and restrained motion design. Most iPhone clips are HDR (10-bit HLG plus Dolby Vision 8.4). They need one correct HLG-to-Rec.709 conversion (libplacebo) that is checked against Apple's own SDR rendering. Claude should set the grade for each take using settings a person can read, then look at rendered stills and adjust. Research on photo-editing agents shows that looking at the result and adjusting is what makes these agents beat baselines. Code should run one MediaPipe face track and derive every punch-in, reframe and subject stabilization from it. BiRefNet (MIT) is the best background-removal model in an August 2026 benchmark. Skip generative face restoration, AI upscaling of the speaker, open-source eye-contact fixes and added film grain. Use RIFE only for b-roll slow motion. Hard cuts plus punch-ins should be the default transition. Keep Remotion or trial HyperFrames (Apache-2.0) for motion graphics, built from a small curated component library.

## Verified findings
**Verification summary.** Most of the researcher's tool, license and benchmark claims hold up against primary sources: HUG-VIS, the licenses, the repo statistics, BT.2408, libplacebo, Meta Engineering and Remotion pricing. Five claims needed correction:
- the libplacebo defaults;
- how the photo-agent studies bear on Claude;
- the punch-in source;
- the progress-bar paper;
- the TikTok beauty-filter framing.

The biggest gap is the final render. Remotion's defaults lose quality, and Remotion tone-maps HDR on its own. Safe-zone numbers, free measurement gates and VLM color-perception limits were also missing.

## 0. What polish is worth
- **The Snap/CUHK data:** the SnapUGC set has 90k Spotlight videos. MOS predictions correlate with average watch time as follows ([Li et al. 2024](https://arxiv.org/html/2410.00289v1)):
  - UVQ: 0.084, 0.156, 0.290, 0.289
  - DOVER: 0.073, 0.148, 0.305, 0.286
  - The four duration bins run from [19,21) s to [49,51) s.
- **These correlations are weak to modest (≤0.31), not zero.** The same paper's engagement model reached **SRCC 0.696** using captions, visual features and aesthetic features.
- **Reading:** technical polish (artifacts, flicker, blown or washed-out skin, consistency across takes) is hygiene. Composition and aesthetics still carry signal. So "do no harm" comes first, but the look is not irrelevant.

## 1. iPhone HDR to SDR
- **Source format:** iPhones record Dolby Vision **Profile 8.4** with an HLG base layer, and HDR is on by default ([WWDC20](https://developer.apple.com/videos/play/wwdc2020/10010/); [Meta 2025](https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/)).
- **Apple's own conversion:** AVAssetExportSession **H.264 presets convert HDR to SDR**, while HEVC presets preserve Dolby Vision 8.4 (WWDC20). That makes an on-device golden reference easy to build.
- **What Meta did:** it first tone-mapped on device with Apple's APIs. It found that SDR white text next to HDR video "appeared gray". Its server-side SDR renditions now use a **tuned Hable operator** ([Meta 2023](https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/)). That is evidence that operator choice is empirical.
- **Where the platforms stand:**
  - Instagram iOS has preserved Dolby Vision and amve metadata since 17 Nov 2025, carrying HEVC 8.4 through to AV1 10.4 (Meta 2025).
  - TikTok has no creator-facing HDR documentation (weak evidence).
  - **Deliver SDR Rec.709 by default.**
- **BT.2408 reference levels** ([ITU BT.2408-8](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf)):
  - Graphics White is 203 cd/m², which is 75% HLG or 58% PQ.
  - Skin: light 55–65% HLG, medium 45–60%, dark 25–45%. Data for Fitzpatrick types 1, 5 and 6 is limited.
  - These make good numeric checks on face exposure.

**libplacebo** ([options](https://libplacebo.org/options/); [FFmpeg filter](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Video/libplacebo.html))
- **Operators:** the library default is `spline`. Alternatives are `bt2390`, `bt2446a` and `st2094-40/10`. Mobius, hable and gamma are "legacy/low-quality, and should not be used".
- **Other defaults:** gamut mapping is perceptual and peak detection is on.
- **Correction:** `contrast_recovery` is **0.0** in libplacebo and 0.3 only in its high_quality preset. In FFmpeg's filter, `tonemapping` defaults to **auto** and `contrast_recovery` to **0.30**.
- **Dolby Vision:** `apply_dolbyvision` is on by default.
- **Hardware:** the filter can run on CPU through Mesa (lavapipe).

**Traps**
- **Wrong HLG reference white:** Jellyfin's `tonemap_cuda` used 203 instead of 3.17955, crushing output about 64x. The issue is now closed ([#775](https://github.com/jellyfin/jellyfin-ffmpeg/issues/775)).
- **Missing tags:** clips without transfer tags skip the tone map ([video-use #190](https://github.com/browser-use/video-use/issues/190)).
- **Remotion's own tone map (new):** Remotion renders in headless Chrome, which is sRGB only. `<OffthreadVideo>` **automatically tone-maps HDR inputs with zscale** (toneMapped=true since 4.0.117), and the docs advise against HDR output ([Remotion HDR](https://www.remotion.dev/docs/hdr); [OffthreadVideo](https://www.remotion.dev/docs/offthreadvideo)). Ingest must therefore write **correctly tagged BT.709** files. Otherwise Remotion adds a second, uncontrolled mapping.

**Rule:**
- Tone-map once at ingest and assert the tags; FFmpeg 8's `colordetect` helps here.
- Normalize to constant frame rate.
- Regression-test golden frames against Apple's H.264 export.
- A/B spline vs bt2390 vs a tuned Hable vs Apple on real takes.

## 2. Exposure, white balance, grading and skin
- **Settings:** static per take, keyframed only when the lighting really changes. Match takes to each other first, then apply the look.
- **Primitives:** FFmpeg `colortemperature`, `colorbalance`, `curves`, `eq`, `vibrance`, `lut3d` and `grayworld` (which needs linear input); OpenColorIO and colour-science (both BSD-3 and active in Sep 2026); color-matcher (GPL-3, pushed Feb 2026).
- **Skin guidelines:** hue near the skin-tone line; saturation about 30–40% for lighter skin and 15–20% for darker skin, and these are "guidelines, not absolutes" (Van Hurkman via [Larry Jordan](https://larryjordan.com/articles/the-secret-to-setting-skin-colors-accurately/)). Check face luma against the BT.2408 ranges above.

**Evidence that an agent can grade (corrected)**
- **PhotoArtAgent:** it drove Lightroom sliders. Without its reflection loop it "did not demonstrate a clear advantage" over baselines; with the loop it won ([PhotoArtAgent](https://arxiv.org/html/2505.23130)). Its "Claude" was **Claude 3.5 Sonnet** running GPT-4o-tuned prompts, so the result says little about current Claude.
- **The specialist winners are fine-tuned VLMs, and general MLLMs were the losing baselines:**
  - JarvisArt is a Qwen2.5-VL-7B model with SFT and RL, rated by 80 people ([JarvisArt](https://arxiv.org/html/2506.17612v1)).
  - PrismGPT was preferred **58.0%** vs MonetGPT (95% CI 55.4–60.6) and **65.0%** vs SepLUT, from 103 participants and 1,545 judgments per comparison ([PrismGPT](https://arxiv.org/html/2609.24768)).
- **Color perception is weak in VLMs.** ColorBench tested 32 of them and found color understanding "largely neglected", with color cues sometimes misleading them ([ColorBench](https://arxiv.org/abs/2504.10514)).
- **So:**
  - Claude sets readable settings and looks at before/after pairs.
  - Code supplies measured face luma, hue, saturation and clipping alongside every image.
  - Noise and sharpness are judged on **1:1 crops**.
- **No added grain:** it looks like constant motion to encoders and gets smeared ([Nilo](https://josephnilo.com/blog/why-youtube-compression-ruins-video-quality/)).

## 3. Face retouch
- **Generative restoration flickers.** Running single-image restoration frame by frame "inevitably introduces severe identity flickering" ([DVFace](https://arxiv.org/html/2604.14560)).
- **Open-source beauty filters are hobby-grade.**
- **Correction on TikTok:** its under-18 rule restricts TikTok's **own in-app** appearance effects (bigger eyes, plumped lips, skin smoothing). It does not affect uploaded video ([9to5Mac](https://9to5mac.com/2024/11/27/tiktok-will-ban-beauty-filters-by-under-18s-over-mental-health-impact/)).
- **Recommendation:** off by default. At most, an opt-in, mask-limited, edge-preserving soften.

## 4. Denoise and sharpen
- **Denoise only when a noise estimate calls for it:**
  - `hqdn3d` is fast but can ghost or band ([Codec Wiki](https://codecs.wiki/docs/filtering/denoise)).
  - For `nlmeans_vulkan`, a patch (PR #20689, Oct 2025) reports **0.55x → 1.42x realtime** at 1080p on an RX 9070 XT. Its merge status is unverified ([ffmpeg-devel](https://www.mail-archive.com/ffmpeg-devel@ffmpeg.org/msg185826.html)).
- **Sharpening:** at most a mild `cas`. iPhone footage is already sharpened (reasoning; the evidence is weak).

## 5. Upscaling
- **It adds no real detail** to native iPhone footage.
- **Use it only on** low-resolution b-roll, GIFs and memes, or big punch-ins from 1080p.
- **FlashVSR** (Apache-2.0, v1.1 Nov 2025): about **17 FPS at 768×1408 on an A100** ([repo](https://github.com/OpenImagingLab/FlashVSR)). Its sparse-attention speedup is verified only on A100/A800, is limited on H200, and is unknown on RTX 40/50.
- **SeedVR2** (Apache-2.0, ICLR 2026) needs **4×H100-80G** for 1080p. It warns that it "tend[s] to overly generate details" on lightly degraded input ([repo](https://github.com/ByteDance-Seed/SeedVR)).
- **So never run either on the speaker's face.**
- **Real-ESRGAN** (BSD-3) works frame by frame and has had no push since Aug 2024.

## 6. Stabilization
- **Gyroflow** (GPL-3) needs gyro logs. It "will not work if you had any internal stabilization enabled", and only logger apps are listed for iOS ([Gyroflow](https://docs.gyroflow.xyz/app/getting-started/supported-cameras/mobile-phones)). iPhone **Enhanced Stabilization is on by default** ([Apple](https://support.apple.com/guide/iphone/change-video-recording-settings-iphc1827d32f/ios)), so skip Gyroflow.
- **vid.stab** (LGPL-2.1+, pushed Aug 2026) is a two-pass handheld fallback.
- **The preferred approach is subject-anchored crop smoothing using L1-optimal paths.** These are built from constant, linear and parabolic segments and shipped in the YouTube Video Editor ([Google Research](https://research.google/blog/auto-directed-video-stabilization-with-robust-l1-optimal-camera-paths/)).

## 7. Reframing, punch-ins and safe zones
- **MediaPipe Face Detector** (Apache-2.0, about 37k stars):
  - short-range BlazeFace is for "selfie-like" images, with 128×128 input and 2.94 ms on CPU;
  - a **full-range** model now exists for rear-camera or tripod shots;
  - it has a VIDEO mode ([MediaPipe](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector)).
- **AutoFlip is deprecated.**
- **Open-source reframers** are all MediaPipe plus smoothing, and ClipsAI has had no push since Jan 2024. Build your own with a dead-zone plus an L1 path or a 1€ filter.
- **Punch-in correction:** the cited No Film School piece gives **no percentage and no beat rule** ([NFS](https://nofilmschool.com/easy-ways-to-hide-jump-cuts-your-next-video)). Creator sources suggest:
  - scale no more than about 110–120%;
  - place punch-ins on key lines or punchlines, not at random ([Katie Steckly](https://www.tiktok.com/@katiesteckly/video/7354004424777288965)).
  - This is weak evidence, so Claude decides and the evaluator enforces a budget.
- **Safe zones (new, enforced by code):**
  - Meta's official Reels guidance keeps about **14% at the top, 35% at the bottom and 6% on each side** free of text and logos ([Meta Ads Guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels)).
  - TikTok's right rail is about 120 px and its bottom block about 300 px or more at 1080×1920. That comes from vendor templates and is weak ([Jon Loomer](https://www.jonloomer.com/tiktok-instagram-reels-safe-zones-templates/)).
  - Keep the eyes near the upper third and keep captions, stickers and punch-in framing out of these zones.

## 8. Background removal and matting
**HUG-VIS benchmark** (27 Aug 2026; 1920×1080 at 30 fps; seated half-body actors on green screen; 9 systems) ([arXiv](https://arxiv.org/html/2608.26517v1)):

| Model | MAD | MSE | dtSSD | Grad | Conn |
|---|---|---|---|---|---|
| BiRefNet | **2.30** | **0.82** | **2.04** | **10.43** | **4.31** |
| MatAnyone 2 | 3.86 | 0.91 | 2.12 | 11.45 | 4.53 |
| SAM 3 | 3.92 | 2.13 | 3.40 | 28.01 | 4.88 |
| RVM | 4.80 | 1.65 | 2.38 | 16.55 | 6.64 |

- **The remaining errors are fingers and hand motion.** The benchmark has no cluttered home backgrounds and is not 9:16, so validate on Yunicorn takes.
- **Licenses and speed:**
  - BiRefNet is MIT; FP16 runs at 17 FPS at 1024² on a 4090, and HR-matting and dynamic-resolution variants exist ([repo](https://github.com/ZhengPeng7/BiRefNet)).
  - MatAnyone and MatAnyone 2 are S-Lab, non-commercial without permission (LICENSE.txt).
  - RVM is GPL-3.0 and has not been pushed since Apr 2024.
  - The SAM License permits use and derivatives, with acknowledgement and trade-control terms.
- **Flicker:** if alphas flicker, try blind deflickering ([All-In-One-Deflicker](https://github.com/ChenyangLEI/All-In-One-Deflicker), Apache-2.0) or light temporal smoothing.

## 9. Eye contact
- **Descript integrates NVIDIA Maxine Eye Contact** ([NVIDIA](https://blogs.nvidia.com/blog/maxine-3d-video-communications/)). Maxine is proprietary and now also ships as a NIM container ([docs](https://docs.nvidia.com/nim/maxine/eye-contact/latest/overview.html)).
- **No open-source equivalent is production-grade.**
- **Skip for v1** and fix it at capture with a teleprompter near the lens.

## 10. Frame interpolation
- **Practical-RIFE** (MIT): the README recommends 4.25 "by default"; 4.26, from Sep 2024, is the newest weights ([repo](https://github.com/hzwer/Practical-RIFE)).
- **Google FILM is archived.**
- **Use it only for b-roll slow motion,** and optionally for tiny-displacement "smooth cuts" that Claude inspects.
- **Prefer 60 fps source** over synthesized frames, and never slow speech.

## 11. Transitions (practitioner consensus, weak)
- **Defaults:** hard cuts and J/L cuts.
- **Stylized flash, whip or glitch** only at section boundaries, under a per-video budget.
- **Ban** star wipes, spins and 3D cubes ([Videomaker](https://www.videomaker.com/how-to/editing/editing-technique/6-video-transitions-that-arent-cheesy/)).
- **Overuse has happened in production:** Descript users complained of zoom cuts "every 20 seconds" under one model (prior report).

## 12. Motion graphics and final-render quality
- **Remotion's license:** free for up to 3 people. Automators pay **$0.01 per render with a $100/month minimum** ([Remotion](https://www.remotion.pro/license)).
- **Remotion's quality defaults (new):**
  - the default frame format is **JPEG at quality 80**;
  - h264 CRF defaults to **18**;
  - Remotion advises "Export your video in `bt709` for more accurate colors", which becomes the default only in v5 ([quality guide](https://www.remotion.dev/docs/quality); [config](https://www.remotion.dev/docs/config)).
- **For a quality-only engine,** set:
  - PNG frames, or JPEG at quality 100;
  - colorSpace bt709;
  - a lower CRF with a slower x264 preset;
  - text rendered at output scale.
- **HyperFrames:** Apache-2.0, 53,656 stars, created 10 Mar 2026, active daily. It renders deterministic MP4 from HTML with 21 agent skills ([repo](https://github.com/heygen-com/hyperframes)). It is also browser-rendered, so assume SDR output. Trial it before any switch.
- **lottie-web** (MIT) has had no push since Sep 2025.
- **Noto Animated Emoji** is **CC BY 4.0** and needs attribution ([Google](https://googlefonts.github.io/noto-emoji-animation/); [attribution](https://github.com/google/fonts/issues/7011)).
- **Progress bars (corrected):**
  - Yang et al. study **chapter progress bars in influencer-marketing videos** and measure purchase intention, not burned-in bars or retention in shorts ([RePEc](https://ideas.repec.org/a/inm/orserv/v17y2025i4p168-189.html)).
  - Vendor claims of a 15–30% lift are unsourced.
  - Make progress bars opt-in and A/B test them.

## 13. Do-no-harm gates (new)
- **FFmpeg measurements (free, license-clean):** `signalstats` (YLOW/YHIGH, SATAVG, HUEAVG, BRNG out-of-range), `blurdetect`, `blockdetect`, `colordetect`, `deflicker` and `scdet` ([FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)).
- **Comparisons against the source:**
  - **VMAF** (BSD-2-Clause-Patent) checks whether an operation damaged fidelity ([VMAF](https://github.com/Netflix/vmaf)).
  - **UVQ** (Apache-2.0) gives a no-reference score ([UVQ](https://github.com/google/uvq)).
- **Avoid DOVER in the product:** it is S-Lab non-commercial.
- **These catch regressions; they are not an objective.** The quality metrics correlate only 0.07–0.31 with watch time.

## Verified recommendations
- Adopt 'do no harm, then consistency, then restrained design' as the visual policy. Gate every visual operation with FFmpeg measurements: signalstats clipping and out-of-range, face-mask luma and hue checked against BT.2408 skin ranges, blurdetect/blockdetect, and flicker checked with deflicker/luma variance. Also run VMAF against the source and UVQ as a no-reference score. Then have Claude or a separate judge compare before/after still pairs and 1:1 crops. Do not use DOVER in the product because it is non-commercial.
- Color-manage once at ingest:
- Assert transfer, primaries and range with colordetect/ffprobe, and normalize to constant frame rate.
- Tone-map Dolby Vision 8.4/HLG to tagged BT.709 with FFmpeg 8 libplacebo; RPU application is on by default, and set tonemapping explicitly rather than relying on 'auto'.
- Keep 10-bit intermediates.
- Regression-test golden frames against Apple's AVFoundation H.264-preset SDR export.
- A/B spline, bt2390, a tuned Hable and Apple's own conversion on real Yunicorn takes.
- Deliver SDR by default.
- Make sure Remotion never tone-maps a second time or degrades the final render. Feed it only BT.709-tagged SDR sources. Set PNG frames (or jpegQuality 100), colorSpace bt709, a lower CRF with a slower x264 preset, and render text at output resolution. The defaults are JPEG q80, CRF 18 and a non-bt709 color space before v5.
- Let Claude grade each take through readable static settings: exposure, temperature/tint, curve, saturation, and a curated look LUT with a strength. Always pair its visual judgment with measured face luma, hue and saturation, because VLM color perception is weak (ColorBench), and the photo-agent evidence comes from fine-tuned specialists or render-and-reflect loops, not zero-shot general models. Match takes first, then apply the look. No per-frame auto-correction and no added grain.
- Run one MediaPipe face-track service (short-range for selfies, full-range for rear-camera shots, plus Face Landmarker). Derive punch-ins, reframes and subject stabilization from it, smoothing the crop path with L1-optimal paths or a 1€ filter with a dead-zone. Code enforces platform safe zones (Meta: 14% top, 35% bottom, 6% sides; TikTok right rail) for captions, stickers and eye placement.
- Keep punch-ins at about 110–120% scale or less on 1080p sources, placed by Claude on key lines. Keep hard cuts and J/L cuts as the default transitions, allow stylized transitions only at section boundaries, and enforce a per-video budget in the evaluator. The evidence here is practitioner-level, so validate with creator A/Bs.
- Use BiRefNet (MIT, HR-matting or dynamic variant) for background removal, text-behind-subject and green-screen formats. Add blind deflickering or light temporal alpha smoothing, and validate hands and hair on real 9:16 home footage, since HUG-VIS is 1080p landscape green screen. Use MatAnyone 2 only with a commercial license; treat RVM as legacy.
- Do not ship in v1: generative face restoration, AI upscaling of the speaker, open-source eye-contact correction, skin smoothing on by default, or added grain. Use FlashVSR or SeedVR2 only on low-resolution b-roll or assets. Check your GPU class first, because FlashVSR's speedup is verified only on A100/A800 and SeedVR2 needs 4×H100 for 1080p.
- Denoise only when a noise estimate calls for it: hqdn3d for mild noise, nlmeans or nlmeans_vulkan for heavy low-light noise, checking merge status first. At most apply a mild cas sharpen. Use Practical-RIFE 4.25 only for b-roll slow motion and gated smooth cuts.
- Keep Remotion, whose automator pricing is $0.01 per render with a $100/month minimum, with a small curated component library. Trial HyperFrames (Apache-2.0, agent-native) before any switch, and keep emoji assets on Noto Animated Emoji (CC BY 4.0, attribution required). Make progress bars opt-in and A/B test them, since the only peer-reviewed evidence concerns chapter bars and purchase intention, not retention in shorts.
- If Yunicorn captures in-app, fix the ceiling at capture: 4K for punch-in headroom, locked exposure and white balance, a deliberate choice of SDR or HLG, fixed frame rate (Auto FPS off), and a teleprompter near the lens.

## Corrections by fact-checker
- [corrected] Snap/CUHK 90k-video study: VQA quality scores barely track engagement (DOVER 0.073–0.305, UVQ 0.084–0.290), so visual polish is only a hygiene factor. → The numbers are right. Table 1 correlates MOS predictions with average watch time in four duration bins: UVQ 0.084/0.156/0.290/0.289 and DOVER 0.073/0.148/0.305/0.286, for bins [19,21) s through [49,51) s. The correlation rises with duration to about 0.3, which is weak-to-modest rather than negligible. The same paper's engagement model reached SRCC 0.696 using captions, intermediate visual features and aesthetic features. So technical MOS is weak, but aesthetic and visual content still carry signal. 'Hygiene' fits technical polish; it does not fit composition or aesthetics. https://arxiv.org/html/2410.00289v1
- [confirmed] iPhones record 10-bit HEVC HLG with Dolby Vision 8.4 metadata (cited to Wikipedia). → Primary sources confirm it. Apple's WWDC20 session uses Dolby Vision Profile 8.4 as the example iPhone source format. Meta's Nov 2025 post describes transcoding iPhone HEVC Profile 8.4 to AV1 Profile 10.4. https://developer.apple.com/videos/play/wwdc2020/10010/
- [confirmed] Meta tone-mapped on device with Apple's native APIs, and SDR white text next to HDR video looked gray. → Both points are confirmed. The researcher left out that Meta's server-side SDR renditions use a tuned Hable operator, which libplacebo calls 'legacy/low-quality'. Operator choice therefore needs an A/B test, not a blanket rule. https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/
- [confirmed] BT.2408 Graphics White = 203 cd/m², 75% HLG / 58% PQ. → Table 1 of Rep. ITU-R BT.2408-8 confirms it. Table 2 also gives skin-tone signal ranges that make useful numeric face-luma checks: light skin 55–65% HLG (45–55% PQ), medium 45–60% HLG, dark 25–45% HLG. The report notes that data for types 1, 5 and 6 is limited. https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf
- [corrected] libplacebo: spline is the default; mobius, hable and gamma are legacy; contrast_recovery is 0.3; FFmpeg's libplacebo filter applies Dolby Vision RPU by default; Vulkan is needed. → libplacebo does call mobius, hable and gamma 'legacy/low-quality, and should not be used'. The library's contrast_recovery default is 0.0 and only the high_quality preset uses 0.3. FFmpeg's filter differs: tonemapping defaults to 'auto' (heuristic) and contrast_recovery to 0.30. apply_dolbyvision is on by default, as claimed. The filter can also run on CPU through Mesa (lavapipe), so a GPU is not strictly required. https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Video/libplacebo.html
- [confirmed] Instagram preserves Dolby Vision and amve metadata since Nov 2025. → Meta Engineering confirms it on 2025-11-17. It applies to the Instagram iOS app, which carries HEVC Profile 8.4 through to AV1 Profile 10.4. It is not cross-platform. TikTok publishes no creator-facing HDR guidance (weak evidence), so SDR remains the right default. https://engineering.fb.com/2025/11/17/ios/enhancing-hdr-on-instagram-for-ios-with-dolby-vision/
- [corrected] PhotoArtAgent: without its reflection loop there was no clear advantage; Claude underperformed GPT-4o. → The reflection finding is correct: without reflection it 'did not demonstrate a clear advantage over RSFNet or TSFlow'. The 'Claude' tested was Claude 3.5 Sonnet, with prompts designed for GPT-4o, and it drove Lightroom 8.1 sliders. This says nothing reliable about current Claude models. https://arxiv.org/html/2505.23130
- [corrected] MonetGPT, JarvisArt and PrismGPT user studies show MLLM agents using procedural operations win, which supports Claude grading. → The numbers are real. PrismGPT was preferred 58.0% vs MonetGPT [55.4, 60.6] and 65.0% vs SepLUT, from 103 participants and 1,545 pairwise judgments per comparison. JarvisArt had 80 participants. But these are fine-tuned specialist VLMs (JarvisArt is Qwen2.5-VL-7B with SFT+RL), and general frontier MLLMs such as GPT-4o were the losing baselines. The evidence supports readable procedural operations plus a render-and-look loop. It does not show that a zero-shot general model is a good colorist. ColorBench (32 VLMs) found color understanding 'largely neglected', and color cues can mislead models. https://arxiv.org/html/2609.24768
- [confirmed] HUG-VIS (Aug 2026): BiRefNet best video-matting model (MAD 2.30, dtSSD 2.04, Grad 10.43), ahead of MatAnyone 2, SAM 3 and RVM. → Confirmed. BiRefNet leads all five metrics, including MSE 0.82 and Conn 4.31, across 9 systems. The protocol is 1920×1080, 30 fps, seated half-body actors on green screen with professionally refined ground truth. That is close to a talking head but has no cluttered home backgrounds and is not 9:16. The main failure mode is 'slender fingers and rapidly changing hand shapes'. https://arxiv.org/html/2608.26517v1
- [confirmed] Licenses: BiRefNet MIT; MatAnyone and MatAnyone 2 NTU S-Lab (non-commercial); RVM GPL-3.0; SAM 3 SAM License permitting use and derivatives. → All confirmed from the repo LICENSE files. The SAM License also requires acknowledgement in publications and trade-control compliance. Separately, DOVER (a VQA model the researcher cites) is also S-Lab non-commercial. https://github.com/pq-yang/MatAnyone2
- [confirmed] HyperFrames: Apache-2.0, about 53.7k stars, created Mar 2026, 'Built for agents', ships Claude Code skills. → The GitHub API shows 53,656 stars, Apache-2.0, created 2026-03-10, pushed 2026-09-28. The README describes deterministic MP4 from HTML/CSS/seekable animations and 21 agent skills. Like Remotion it renders through a browser, so assume SDR/sRGB output (inferred, not verified). https://github.com/heygen-com/hyperframes
- [confirmed] Remotion: free up to 3 people; automators pay $0.01 per render with a $100/month minimum. → Confirmed. The researcher missed that Remotion's defaults are not quality-maximal: JPEG intermediate frames at quality 80, h264 CRF 18, and a non-bt709 colorSpace before v5. OffthreadVideo also silently tone-maps HDR inputs with zscale. https://www.remotion.pro/license
- [corrected] Alternate 10–20% punch-ins placed on emphasis beats (cited to No Film School). → The cited No Film School article gives no percentage and says nothing about beat placement; it only says zoom in and out to hide jump cuts. Practitioner sources elsewhere say scale no more than about 110–120% and place punch-ins on key lines or punchlines (weak, creator-level evidence). https://nofilmschool.com/easy-ways-to-hide-jump-cuts-your-next-video
- [corrected] The only peer-reviewed evidence shows progress bars increase impatience and lower purchase intention (Yang et al. 2025). → The paper (Service Science 17(4), 2025) is about chapter progress bars, a navigation and segmentation UI, in video-based influencer marketing, and its outcome is purchase intention. It does not measure burned-in progress-bar overlays or retention in shorts. It is weak, indirect evidence; keep progress bars opt-in and A/B test them. https://ideas.repec.org/a/inm/orserv/v17y2025i4p168-189.html
- [corrected] TikTok blocks skin smoothing and feature changes for users under 18 (as platform pushback against retouching). → TikTok restricts its own in-app appearance effects, such as bigger eyes, plumped lips and skin smoothing, for under-18 users. It does not police uploaded videos edited elsewhere. It is a norm signal, not a constraint on Yunicorn's server edits. https://9to5mac.com/2024/11/27/tiktok-will-ban-beauty-filters-by-under-18s-over-mental-health-impact/
- [confirmed] FlashVSR about 17 FPS at 768×1408 on one A100; SeedVR2 1080p needs 4×H100-80G; Practical-RIFE v4.25 recommended, v4.26 latest. → All confirmed from the READMEs. Additional caveats: FlashVSR's block-sparse attention is verified only on A100/A800, is limited on H200, and is unknown on RTX 40/50. SeedVR2 warns it tends to 'overly generate details' on lightly degraded input, which supports never running it on faces. The newest RIFE weights date from Sep 2024. https://github.com/OpenImagingLab/FlashVSR
- [confirmed] nlmeans_vulkan rewrite about 1.42x realtime at 1080p on RX 9070 XT. → The patch (PR #20689, Oct 2025) reports 0.55x → 1.42x at 1080p. Its merge status is unverified. https://www.mail-archive.com/ffmpeg-devel@ffmpeg.org/msg185826.html
- [confirmed] Gyroflow impractical: needs gyro logs, fails with internal stabilization, stock Camera app not listed. → Confirmed. Apple also turns Enhanced Stabilization on by default, so camera-roll footage is already stabilized in camera. https://docs.gyroflow.xyz/app/getting-started/supported-cameras/mobile-phones
- [confirmed] L1-optimal camera paths (Grundmann 2011) are the basis of YouTube's stabilizer; generative face restoration causes 'severe identity flickering' (DVFace); Jellyfin HLG reference-white bug about 64x. → All three are confirmed. The L1 method shipped in the YouTube Video Editor. The DVFace quote is in its Related Work section. Jellyfin #775 was a tonemap_cuda typo (203 vs 3.17955) on the 7.1 branch and is now closed. https://research.google/blog/auto-directed-video-stabilization-with-robust-l1-optimal-camera-paths/

## Missed items added
- Remotion's final-render defaults degrade quality: frames go through JPEG at quality 80 by default, h264 CRF defaults to 18, and Remotion recommends exporting in bt709 'for more accurate colors', which only becomes the default in v5. For a quality-only engine, use PNG frames or jpegQuality 100, colorSpace bt709, lower CRF with a slower x264 preset, and render text at output scale. [Remotion quality guide](https://www.remotion.dev/docs/quality); [Remotion config](https://www.remotion.dev/docs/config)
- Remotion renders in headless Chrome, which is sRGB/SDR only. Since 4.0.117 OffthreadVideo automatically tone-maps HDR inputs with FFmpeg/zscale (toneMapped=true by default), and the docs advise against HDR output. A controlled libplacebo ingest must write correctly tagged BT.709 files, or Remotion applies a second, uncontrolled mapping. [Remotion HDR](https://www.remotion.dev/docs/hdr); [OffthreadVideo](https://www.remotion.dev/docs/offthreadvideo)
- Platform safe zones as hard numbers. Meta's official Reels guidance is to keep about 14% at the top, 35% at the bottom and 6% on each side free of text and logos. TikTok's right rail (about 120 px) and bottom caption block (about 300 px or more at 1080×1920) come from vendor guides, so that evidence is weak. These should be code-enforced constraints for captions, punch-in eye placement and graphics. [Meta Ads Guide](https://www.facebook.com/business/ads-guide/update/image/instagram-reels); [Jon Loomer templates](https://www.jonloomer.com/tiktok-instagram-reels-safe-zones-templates/)
- Free, license-clean 'do no harm' measurements the researcher did not list. FFmpeg has signalstats (YLOW/YHIGH/SATAVG/HUEAVG/BRNG), blurdetect, blockdetect, colordetect, deflicker and scdet. VMAF (BSD-2-Clause-Patent) can compare processed output with source, and UVQ (Apache-2.0) gives a no-reference score. DOVER, which the researcher cites, is S-Lab non-commercial. [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html); [VMAF](https://github.com/Netflix/vmaf); [UVQ](https://github.com/google/uvq)
- VLM color perception is weak. ColorBench tested 32 VLMs and found color understanding 'largely neglected' and that color cues can mislead models. Claude's before/after color judgments must therefore be paired with measured face luma, hue and saturation, and noise or sharpening checks need 1:1 crops, not downscaled stills. [ColorBench](https://arxiv.org/abs/2504.10514)
- BT.2408 Table 2 skin-tone signal ranges (light 55–65% HLG, medium 45–60%, dark 25–45%) give numeric face-exposure targets that are better grounded than vectorscope lore. [ITU BT.2408-8](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-BT.2408-8-2024-PDF-E.pdf)
- Blind video deflickering (All-In-One-Deflicker, CVPR 2023, Apache-2.0, pushed Jul 2026) can repair temporal flicker from per-frame processing such as BiRefNet alphas or per-frame color ops. It is a cleaner option than hand-rolled smoothing when flicker is detected. [repo](https://github.com/ChenyangLEI/All-In-One-Deflicker)
- Apple's own reference path: AVAssetExportSession H.264 presets convert HDR to SDR, while HEVC presets preserve Dolby Vision 8.4. This makes an on-device golden reference, or on-device SDR conversion at upload, trivial to build. [WWDC20 10010](https://developer.apple.com/videos/play/wwdc2020/10010/)
- Meta's production SDR path uses a tuned Hable operator despite libplacebo calling Hable legacy, which is real-world evidence that operator choice is empirical. [Meta Eng 2023](https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/)
- iPhone capture traps: HDR and Enhanced Stabilization are on by default, and Auto FPS can lower frame rate in low light. Normalize to constant frame rate and assert tags at ingest (weak source). [Apple Support](https://support.apple.com/guide/iphone/change-video-recording-settings-iphc1827d32f/ios)
- MediaPipe now ships a full-range BlazeFace model, as well as the short-range selfie model, for rear-camera or tripod shots where the face is further than about 2 m away. [MediaPipe](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector)
- lottie-web has had no push since Sep 2025. It is fine for frame-seeking through @remotion/lottie, but it is not actively developed. [repo](https://github.com/airbnb/lottie-web)

## Tools
- FFmpeg 8 + libplacebo filter [HDR→SDR tone mapping / color conversion; LGPL-2.1 (libplacebo); FFmpeg LGPL/GPL build-dependent] https://libplacebo.org/options/ — Implements ITU/SMPTE curves (spline default, bt.2390, bt.2446a, st2094-40) with perceptual gamut mapping, peak detection and Dolby Vision RPU application; libplacebo labels mobius and hable legacy. Needs Vulkan (GPU or lavapipe). CPU fallback: zscale (zimg) + tonemap with npl=203.
- Apple AVFoundation HDR→SDR export (on-device) [HDR→SDR reference / on-device tone mapping; Proprietary (iOS SDK)] https://engineering.fb.com/2023/07/17/video-engineering/hdr-video-reels-meta/ — Meta used Apple's native tone-mapping APIs to fix overexposed iPhone HDR uploads to Reels. Use as the golden reference for validating server tone mapping, or tone-map on device before upload.
- FFmpeg color filters (grayworld, colorcorrect, colortemperature, colorbalance, curves, eq, vibrance, lut3d, cas, hqdn3d, nlmeans_vulkan) [Grading, white balance, LUTs, denoise, sharpen; LGPL/GPL (FFmpeg)] https://ffmpeg.org/ffmpeg-filters.html — Standard primitives. nlmeans_vulkan rewrite reached about 1.42x realtime at 1080p (FFmpeg-devel PR); Codec Wiki documents hqdn3d artifacts. Apply static per-take settings chosen by Claude. grayworld requires linear-light input.
- OpenColorIO / colour-science [Color management math; BSD-3-Clause] https://github.com/colour-science/colour — ASWF / industry standard; colour ~2.7k stars, OCIO ~2.1k stars, both active Sep 2026. For exact transforms and LUT generation.
- color-matcher [Take-to-take color matching; GPL-3.0] https://github.com/hahnec/color-matcher — Implements MKL, Reinhard and histogram matching; ~668 stars, pushed Feb 2026. GPL is fine for server-side use. Match takes to each other, then apply the look.
- MediaPipe Face Detector + Face Landmarker [Face tracking for reframing, punch-ins, masks; Apache-2.0] https://developers.google.com/edge/mediapipe/solutions/vision/face_detector — BlazeFace short-range model built for selfie-like phone images; 2.94 ms CPU; video mode; repo ~37k stars, active. Add your own smoothing (L1 path or 1€ filter). AutoFlip is deprecated.
- vid.stab [Stabilization (handheld); LGPL-2.1+] https://github.com/georgmartius/vid.stab — De facto FFmpeg two-pass stabilizer, with tripod mode and optzoom; pushed Aug 2026. Use only on handheld shake. Prefer face-anchored crop smoothing for talking heads.
- Gyroflow [Gyro-based stabilization; GPL-3.0] https://docs.gyroflow.xyz/app/getting-started/supported-cameras/mobile-phones — Best-in-class when gyro logs exist (~9.5k stars), but it fails with internal stabilization on, and the stock iPhone Camera app is not among the iOS apps it lists. Not recommended for camera-roll iPhone footage.
- BiRefNet (incl. HR-matting) [Background removal / matting; MIT] https://github.com/ZhengPeng7/BiRefNet — Best on every metric in HUG-VIS (Aug 2026) video matting: MAD 2.30, dtSSD 2.04; 17 FPS at 1024² in FP16 on an RTX 4090. Per-frame model; add light temporal alpha smoothing.
- MatAnyone 2 [Video matting; NTU S-Lab License 1.0 (non-commercial without permission)] https://github.com/pq-yang/MatAnyone2 — Second on HUG-VIS (MAD 3.86, dtSSD 2.12); beats RVM on its own synthetic benchmarks (MAD 4.73 vs 6.08). Needs a commercial license for Yunicorn.
- RobustVideoMatting (RVM) [Video matting; GPL-3.0] https://github.com/PeterL1n/RobustVideoMatting — HUG-VIS MAD 4.80 / dtSSD 2.38, behind BiRefNet and MatAnyone 2; last push Apr 2024. Legacy fallback only.
- SAM 2 / SAM 3 [Promptable segmentation and tracking; Apache-2.0 (SAM 2); SAM License (SAM 3)] https://github.com/facebookresearch/sam3 — ~19.9k and ~11.8k stars. As a matting stand-in on HUG-VIS, SAM 3 scored MAD 3.92 with weaker temporal stability (dtSSD 3.40). Use for object masks and tracking (b-roll subjects), not face boxes or hair-level alpha.
- Practical-RIFE [Frame interpolation / slow-mo; MIT] https://github.com/hzwer/Practical-RIFE — Maintained RIFE line; v4.25 recommended default, v4.26 latest; pushed Aug 2026. Google FILM is archived. B-roll slow motion and gated smooth cuts only.
- FlashVSR [Temporal video super-resolution; Apache-2.0] https://github.com/OpenImagingLab/FlashVSR — One-step diffusion VSR at about 17 FPS for 768×1408 on an A100; v1.1 Nov 2025; ~1.9k stars. Only for low-resolution b-roll or assets; never on the speaker's face.
- SeedVR2 [Temporal video restoration / SR; Apache-2.0] https://github.com/ByteDance-Seed/SeedVR — ByteDance one-step diffusion restoration; 1080p needs 4×H100-80G. Heavy; an alternative to FlashVSR for assets.
- Real-ESRGAN [Image/frame upscaling; BSD-3-Clause] https://github.com/xinntao/Real-ESRGAN — ~36.9k stars but last push Aug 2024; works frame by frame with no temporal consistency. Use for still images or thumbnails, not video of faces.
- NVIDIA Maxine Eye Contact [Gaze correction; Proprietary (NVIDIA SLA; AI Enterprise or Developer Program; commercial use allowed)] https://catalog.ngc.nvidia.com/orgs/nvidia/teams/maxine/collections/nvargazeredirection — Powers Descript's Eye Contact feature (NVIDIA blog). No open-source equivalent reaches production quality. Skip for v1; optional and off by default if added.
- Remotion [Motion graphics / final renderer; Remotion License (free up to 3 people; $0.01 per render, $100/month minimum for automators)] https://www.remotion.pro/license — ~60.8k stars, active; already Yunicorn's renderer. Keep, with a curated component library.
- HyperFrames [Agent-native HTML-to-video motion graphics; Apache-2.0] https://github.com/heygen-com/hyperframes — ~53.7k stars since Mar 2026, active daily; deterministic MP4 from HTML/CSS/seekable animations; ships Claude Code skills. Trial as a license-free alternative. Young project, so check determinism and fonts.
- lottie-web [Animated stickers / elements; MIT] https://github.com/airbnb/lottie-web — ~32k stars; frame-seekable (goToAndStop) for deterministic rendering. Pair with Noto Animated Emoji (CC BY 4.0, attribution required).
- Noto Animated Emoji [Emoji/sticker assets; CC BY 4.0] https://googlefonts.github.io/noto-emoji-animation/ — Official Google set in Lottie, WebP and GIF. Attribution can sit on an About or credits page. Avoid Apple emoji.
- Motion Canvas / Revideo [Code-driven motion graphics; MIT] https://github.com/motion-canvas/motion-canvas — ~19.2k and ~4.1k stars; Revideo adds headless rendering. Only if leaving Remotion; HyperFrames is the more agent-oriented option.
