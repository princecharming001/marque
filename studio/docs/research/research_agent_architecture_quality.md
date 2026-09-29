# Agent architecture for maximum edit quality (talking-head shorts, Claude-led, provider-agnostic, cost/latency ignored)

## Summary
The evidence points to one lead editor agent, running on Claude, that owns every change to the edit list. It works through stages the way a post house does: a written edit brief first, then a story cut, a fine cut, finishing passes and QC. Specialist sub-agents (b-roll, sound, captions, color) should only propose changes. Critics start with a fresh context, come from a different model family, and review the actual render: Gemini watches it with audio, Claude looks at frames, and code measures it. Because cost and latency don't matter, the biggest quality gains come from test-time compute. That means 3–4 story-cut variants chosen by a position-swapped pairwise tournament, and up to about 5 revision rounds in which a revision is kept only if it beats the current best. Craft knowledge belongs in Agent Skills files loaded progressively (an open standard that works across providers), with hard rules kept in validators. Two more pieces: a creator memory learned from the creator's own edits, and a style card measured from reference videos. Quality is measured by blind human pairwise preference, supported by judges built from binary rubric items that are calibrated against human labels and run across model families. Much of the evidence comes from neighboring tasks, not talking-head editing, so every design choice needs A/B testing in Yunicorn's own eval.

## Verified findings
## 0. What changes when only quality matters
The earlier report ([Agentic video editing engine.md](/Users/home/URAP - Lead - Levine/reports/Agentic video editing engine.md)) sized everything to cost and latency. Its core still holds: word-ID ops, a measuring compiler, tiered validators and a fallback ladder. With cost removed, extra compute should go to four places: (a) planning, (b) several diverse candidates that are ranked and then fused, (c) critics with a fresh context, drawn from several model families, that inspect the render, and (d) more revision rounds, each accepted only if it beats the current best. All writes still go through one agent. Most of the evidence comes from neighboring tasks (generation, mashups, math and QA), so every choice below must be A/B-tested on Yunicorn's own clips.

## 1. One writer, advisory specialists
- **Multi-agent helps, within limits.** Anthropic's lead-plus-subagents system beat single Opus 4 by **90.2%** on research tasks. Anthropic says the pattern fits poorly where agents "share the same context or involve many dependencies", and token usage alone explains 80% of BrowseComp variance ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- **Cognition, April 2026:** multi-agent works "when writes stay single-threaded and the additional agents contribute intelligence rather than actions". Its reviewers worked best when they shared no context with the coder ([Cognition](https://cognition.com/blog/multi-agents-working)).
- **GLANCE** (music mashups): parallel sub-timeline editors produced cross-segment conflicts. Removing its negotiation step lowered quality from 3.45 to 3.24 ([GLANCE](https://arxiv.org/html/2604.05076)).
- **EditDuet** (two Llama-3.1-8B agents; the critic reads a *text* timeline, not the render):
  - Adding the critic moved failures 23.8%→19.5% and coverage 68.5%→82.7%.
  - Self-generated demonstrations brought failures to **8.2%**.
  - Humans preferred the full system over the editor alone 85.7% vs 14.3%, and over editor+critic 64.9% vs 35.1% ([EditDuet](https://arxiv.org/html/2509.10761v1)).
- **Descript's Underlord** is "a mix of models from different providers" ([Descript](https://www.descript.com/blog/article/underlord-got-a-model-picker-and-claude-sonnet-45)).
- **Inference.** In a 15–90 s clip every cut shifts captions, b-roll, music hits and SFX. One lead agent should own every EDL write. Specialists (b-roll, sound, captions, color) should return structured proposals only, and critics should run with fresh context.
- **Lead model.** Cost is ignored, so A/B test **Claude Fable 5.1** (top tier, "demanding reasoning and long-horizon agentic work") against Opus 5.5 at xhigh and max ([models](https://platform.claude.com/docs/en/about-claude/models/overview)). No Claude model accepts audio or video.

## 2. Plan first
- **VISTA** (CVPR'26, a video-*generation* agent): removing the planner lowered iteration-5 win rate from 45.9 to 35.1 (single-scene) and from 46.3 to 38.8 (multi-scene) ([VISTA](https://arxiv.org/html/2510.15831)).
- **AgenticVBench:**
  - Stripping per-slot descriptions cost **−27 pp** averaged over 18 Assembly tasks.
  - The often-cited **+23 pp** from editor notes comes from **a single task** (comedy_knead).
  - Claude's Plan and TodoWrite state tracking kept Opus 4.7 competitive ([AgenticVBench](https://arxiv.org/html/2605.27705)).
- **Reasoning and structure.** Strict format constraints degrade reasoning ([Let Me Speak Freely?](https://aclanthology.org/2024.emnlp-industry.91/)). The Prompt-Driven paper's version of this claim is an unsupported assertion.
  - On Opus 5.5, thinking is always on, so reasoning belongs in thinking blocks.
  - Asking the model to reproduce its reasoning in the reply can trigger a `reasoning_extraction` refusal ([Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
  - So: write the free-text brief, then compile it to a schema.
- **Practitioners** say to fix the story in the rough cut before polishing, and to finish in the order graphics → sound → grade ([LoopDesk](https://loopdesk.ai/blog/video-editing-process-explained); [Vortex Xcel](https://vortexxcel.com/youtube-video-editing-workflow/)). These are norms, not studies.
- **Implication.** The *Edit Brief* is an explicit artifact: goal, hook, beat sheet, target length, pacing, visual and sound plan, and don'ts from creator memory and the style card. A binary rubric is derived from the brief (§4).

## 3. Gated stages
The stages mirror a post house: ingest → brief → story cut (takes, structure, hook) → fine cut (seams, pauses) → **picture-lock review** → finishing (reframes/punch-ins → b-roll/graphics → captions → sound: music, SFX, ducking, loudness → color: correct, then grade) → QC.
- Each stage loads its own skill and has its own pass criteria.
- A later stage may send work back to an earlier one.
- EditDuet's best demonstrations averaged about 17 editor steps and 4 critic steps ([EditDuet](https://arxiv.org/html/2509.10761v1)).
- The story cut must pass a "radio test": it has to work as audio alone.

## 4. Render-and-watch critique
**Who can perceive the render:**
- **Claude:** no audio or video input. It reads frames only. Opus 5.5 reads dense visuals better when given crop, zoom and measure tools such as PIL/OpenCV or a crop tool ([Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
- **Gemini:** native video with audio ([docs](https://ai.google.dev/gemini-api/docs/video-understanding); [media resolution](https://ai.google.dev/gemini-api/docs/media-resolution)).
  - Sampling is 1 fps by default, with custom fps available, and the docs warn that fast action loses detail.
  - Gemini 3 costs about 70 tokens/frame by default and 280 at high resolution, which is needed for small text such as captions. Audio is 32 tokens/s.
  - `start_offset`/`end_offset` clip the video, so a seam can be re-inspected at high fps.
  - **New:** `processing: "agentic"` lets the model navigate the timeline and adapt fps and resolution ("up to 88% fewer tokens", about 7% higher quality). It runs on **Gemini 3.5–3.8 Flash**, not on 3.1 Pro, which is still preview; 3.8 Flash is the newest stable model ([models](https://ai.google.dev/gemini-api/docs/models)).
- **OpenAI:** no documented video input. It accepts up to 1,500 images or 512 MB per request ([vision](https://developers.openai.com/api/docs/guides/images-vision)). The SDK feature request was closed "not planned" on 2026-08-12 ([#1778](https://github.com/openai/openai-node/issues/1778)).
- **Open-weight:** Qwen3-Omni (Apache-2.0) takes audio and video, and its Captioner model writes detailed audio captions ([repo](https://github.com/QwenLM/Qwen3-Omni)).

**VLMs perceive craft and time poorly:**
- On VEBench's 5-option technique recognition (20% chance), Gemini-2.5-Pro scored **34.65%** and GPT-4o **24.68%**, and "audio cues prove essential".
- On temporal localization nearly every model scored **tIoU ≈ 0**; the best was 0.11 ([VEBench](https://arxiv.org/pdf/2605.03276)).
- Frame-number overlays improve temporal grounding ([NumPro, CVPR'25](https://arxiv.org/abs/2411.10332)).
- **Rules:**
  - Burn frame numbers and word IDs into review renders.
  - Map every critic note to word IDs or measured boundaries; never apply VLM timestamps directly.
  - Ask concrete questions rather than for scores. Question-driven VLM critique gave +11.57% on T2V-CompBench ([VQQA](https://arxiv.org/abs/2603.12310)).
  - Grade against a rubric written for this edit ([VideoArgus](https://arxiv.org/abs/2608.05485), CC BY 4.0).

**Give critics measurements, not just pixels:**
- ffmpeg ebur128/pyloudnorm for loudness, silence and click detection, and an ASR round-trip diff.
- [Audiobox Aesthetics](https://github.com/facebookresearch/audiobox-aesthetics) (CC-BY-4.0) for audio production quality.
- Avoid NISQA's weights, which are CC BY-NC-SA and bar commercial use ([NISQA](https://github.com/gabrielmittag/NISQA)).

**Critics need tuning.** Anthropic's evaluator would "talk itself into deciding they weren't a big deal". The fix was to read its logs and recalibrate the prompt. Separating generator from evaluator is "a strong lever" ([Anthropic harness](https://www.anthropic.com/engineering/harness-design-long-running-apps)).

**Critic design is unsettled.** VISTA's judge-composition ablation is noisy:
- A normal-only judge fell from 35.0 to 17.2 in single-scene but only from 35.3 to 33.3 in multi-scene.
- An adversarial-only judge collapsed in multi-scene (to 26.7).
- Treat "normal + adversarial + meta-judge" as a hypothesis to test, not a proven design.
- No study shows that a render-watching critic beats a text-timeline critic on talking-head footage.

## 5. Iterations
- **Self-Refine:** about 20% absolute average gain, mostly within 3 rounds ([Self-Refine](https://arxiv.org/abs/2303.17651)).
- **Without external signal, self-correction hurts** ([Huang et al.](https://arxiv.org/abs/2310.01798)).
- **Anthropic:** 5–15 rounds improved scores "before plateauing" ([harness](https://www.anthropic.com/engineering/harness-design-long-running-apps)).
- **VISTA:** 35.5→45.9 over 5 rounds. Without its pairwise champion selection, 33.3 ([VISTA](https://arxiv.org/html/2510.15831)).
- **Agents:** reflecting selectively on a score threshold beat reflecting every step ([TTC for agents](https://arxiv.org/html/2506.12928)).
- **Rule.** Revise only while a P0 or P1 issue exists, allow up to about 5 rounds, and accept a revision only if it wins a position-swapped comparison against the current best and passes every deterministic gate. Stop after 2 rounds with no win.

## 6. Candidates, selection, fusion
- **Pairwise knockout:** 40–60% relative gain, but only on the hardest half of MATH-500 ([PairJudge](https://arxiv.org/html/2501.13007v2)). A separate knockout study added +0.07 Pearson on exam scoring and machine translation ([Knockout](https://arxiv.org/abs/2506.03785)).
- **List-wise verification** beat scoring and voting (63.0 vs 59.4 vs 56.8 on GAIA). Rollouts from **mixed model families** raised Pass@4 to 74.55 ([TTC for agents](https://arxiv.org/html/2506.12928)).
- **FusioN** beats Best-of-N: +6% at N=2, plateauing around N≈7, on text tasks with a 111B fusor ([FusioN](https://ar5iv.labs.arxiv.org/html/2510.00931)).
- **Inference.** Generate 3–4 story cuts from divergent briefs (tight, breathing, hook reordered), optionally with directors from different model families. Rank them list-wise, confirm the top pair with position-swapped pairwise judging, then have the lead merge in the best parts of the other candidates (for example, B's hook into A's body).

## 7. Craft knowledge as skills
- **Format:**
  - Metadata is about 100 tokens and always loaded.
  - SKILL.md stays under 5k tokens and 500 lines, with references one level deep ([Anthropic](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview); [spec](https://agentskills.io/specification)).
  - It is an open standard (Apache-2.0, 25.7k stars), and OpenAI Codex skills "build on" it ([OpenAI](https://learn.chatgpt.com/docs/build-skills)).
- **Runtime.** API skills run only in the code-execution container, with no network and no package installs, and no Responses-API support is documented. Load skills through the harness's own `load_skill` tool.
- **Evidence:**
  - Expert-written composition skills scored best (0.620), above judge-evolved skills (0.582) and base skills (0.552). Removing composition skills cut planning to 0.465 ([VideoWeaver](https://arxiv.org/html/2606.08091v1)).
  - EditDuet's demonstrations are another data point.
  - The +23 pp from notes is a single-task result.
- **Guidance.**
  - Write principles, worked before/after examples, measurable targets and "when to break this".
  - Keep non-negotiables (no mid-word cut, CTA kept) in validators.
  - Name specific patterns; Opus 5.5 "responds well to instructions that name specific patterns".
  - Ablate one skill at a time, because "every component in a harness encodes an assumption".
- **Precedents:** [video-use](https://github.com/browser-use/video-use) (MIT), [kamgasimo](https://github.com/kamgasimo/ai-video-editor) (MIT, 0 stars and new), [Remotion skills](https://github.com/remotion-dev/skills) (no license, so don't copy).

## 8. Reference-video style cards (weak evidence)
- Build a card per reference video: code measures shot-length distribution (PySceneDetect, BSD-3), cuts per minute, caption density and position, punch-in rate, b-roll ratio, speech rate and music LUFS; Gemini adds a qualitative description.
- Use it as brief constraints, as rubric items, and as a "closer to R?" pairwise question.
- Precedents: [FableCut remake-reel](https://github.com/ronak-create/fablecut/blob/main/skills/remake-reel/SKILL.md) (MIT) and [Google 2021](https://research.google/pubs/automatic-style-transfer-for-non-linear-video-editing/). There is no quality evidence.

## 9. Creator memory
- **CIPHER** infers natural-language preferences from user edits and retrieves them from the k=5 nearest past contexts. It cut edit distance **31%** (summaries) and **73%** (email) against no learning, with simulated GPT-4 users only ([CIPHER](https://arxiv.org/html/2404.15269)).
- **Claude's memory tool** is client-side; you must build path-traversal validation yourself ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)).
- **Inference.** After each export, diff the agent's EDL against the creator's final EDL, induce preference statements tagged by context, show them to the creator to edit, and inject the relevant ones into the brief.

## 10. Ambiguity
- Temporal references are hard: ExpressEdit reached 0.68 recall ([ExpressEdit](https://arxiv.org/pdf/2403.17693)).
- Resolve from memory and profile defaults first. If still ambiguous, produce divergent variants rather than block. Ask at most one decisive question up front (target length, which take). In chat, restate your interpretation and offer one-tap undo.

## 11. Evaluation
- **Holistic pairwise judges agree with humans 64–81% of the time:**
  - EditDuet (GPT-4o on keyframes): 80.6% vs 78.7% between humans.
  - AVENUE: Qwen3-Omni 67.0%, Gemini-3.1-Pro 66.2% (best τb 0.440), Gemini-3.6-Flash 64.0%, vs 71.1% between humans ([AVENUE](https://arxiv.org/pdf/2609.04253)).
  - GLANCE: Spearman 0.67.
- **Binary rubric items:** AgenticVBench's grader agreed with held-out experts 96.4–98.2%, but only *after* low-agreement items were revised, split or removed, and the grader model is unnamed. It is still the right direction, but calibrate on held-out clips.
- **Juries of mixed model families beat one big judge:** κ 0.763 vs 0.627 for GPT-4, with less intra-model bias ([PoLL](https://arxiv.org/html/2404.18796)).
- **Known biases:**
  - Judges favor their own generations ([NeurIPS'24](https://papers.nips.cc/paper_files/paper/2024/file/7f1f0218e45f5414c79c0679633e47bc-Paper-Conference.pdf)).
  - Structured multi-dimensional evaluation cut self-preference 31.5% ([SPB](https://arxiv.org/abs/2604.22891)).
  - Always swap positions.
- **Descript:** comparing models "is extremely hard". Opus 4.6 showed 13–23% higher intent adherence over leading frontier models (vendor case study) ([Descript/Anthropic](https://claude.com/customers/descript)). Descript evaluates new models in 1–2 hours via OpenRouter ([OpenRouter](https://openrouter.ai/blog/insights/descript-model-evaluation-queue/)).
- **Human sample sizes** (independent pairs, ties excluded):
  - 100 pairs gives a ±9.8 pp 95% interval.
  - About 194 pairs detects 60 vs 50 at 80% power.
  - About 782 pairs detects 55 vs 50.
  - Pairs that share a clip or rater are correlated: inflate n by the design effect, or fit Bradley–Terry or mixed effects with clip and rater as random effects.
- **External sanity check:** [AgenticVBench](https://github.com/PhiloLabs/agentic-vbench) (Apache-2.0). Its best Assembly score is about 0.38, the best Repurpose score is 0.30 against an expert 0.95, and agents trail experts by 43–65 pp.

## 12. Architecture
```
PERCEPTION (cached): words/disfluency+IDs, acoustics (LUFS, silences, clicks, Audiobox PQ), face track, HDR,
  Gemini watch notes → mapped to word IDs; creator memory; style cards
DIRECTOR (Fable 5.1 vs Opus 5.5 xhigh, A/B): Edit Brief + per-edit binary rubric + 3–4 divergent directions
STORY CUT ×N (word-ID ops → compiler → tier-1 checks) → review renders w/ burned frame#/word IDs
SELECT: list-wise rank by cross-family jury → position-swapped pairwise confirm → lead fuses best parts
FINE CUT → seams/ASR round-trip/clicks → PICTURE-LOCK
FINISHING: lead is sole writer; read-only specialists (b-roll, sound, captions, color) propose
RENDER-AND-WATCH (≤5, only while P0/P1 exist): Gemini whole-clip (≥2–5 fps + audio; test agentic mode)
  + clipped high-fps seam checks + high media_resolution for captions; Claude on frames with crop tool
  + measurements; questions not scores → lead filters vs brief → accept only if beats best + gates pass
QC → EDL + render + trace;  LEARN: export diff → preferences; failures → eval cases
```
- **Providers.** Keep a thin in-house role interface. Call **native SDKs** for media roles, because adapters lag on video parameters: LiteLLM only added Gemini per-video fps and media_resolution in January 2026 ([#19026](https://github.com/BerriAI/litellm/issues/19026)). Use LiteLLM, Pydantic AI or OpenRouter only for text roles.
- **Anthropic-only mode:** Claude on dense frame sheets with a crop tool, plus measurements, plus Qwen3-Omni-Captioner audio captions (self-hosted). Label this mode lower-confidence and measure it against the Gemini-watcher configuration.
- **Tuning.** Tune each role-to-model mapping separately: effort names "don't correspond to the same amount of thinking across models" ([Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).

## Verified recommendations
- Keep one lead editor agent as the only writer of the EDL. Specialists (b-roll, sound, captions/graphics, color) run as read-only sub-agents with a clean context and return structured proposals, which the lead applies through typed word-ID ops. Because cost is ignored, A/B test Claude Fable 5.1 against Opus 5.5 at xhigh and max for the lead role (confidence: high on the single-writer design, medium on model choice).
- Make the Edit Brief the first artifact, and derive a per-edit binary rubric from it (VideoArgus-style). Keep reasoning in thinking blocks, then emit the brief as free text and compile it to a schema. Never prompt Claude to reproduce its reasoning in the reply, which can trigger a reasoning_extraction refusal on Opus 5.5 (confidence: medium-high).
- Run gated stages in post-house order: brief → story cut → fine cut → picture-lock → reframes, b-roll/graphics, captions, sound, color → QC. Each stage has its own skill and pass criteria, and a later stage may send work back to an earlier one (confidence: medium).
- Generate 3–4 story cuts from divergent briefs, optionally with directors from different model families. Rank them list-wise with a jury of judges from different model families, confirm the top pair with position-swapped pairwise judging, then have the lead merge the best parts of the other candidates into the winner (confidence: medium; the evidence comes from generation, math and QA tasks).
- Use Gemini as the render watcher through its native SDK: a whole-clip pass with audio at a raised fps (≥2–5 fps for 15–90 s clips), clipped high-fps re-inspection of every seam via start/end offsets, and high media_resolution for caption legibility. Benchmark Gemini 3.8 Flash in agentic processing mode against Gemini 3.1 Pro on Yunicorn's own defect set (confidence: medium).
- Burn frame numbers and word IDs into every review render. Accept critic notes only after mapping them to word IDs or measured boundaries, because VLM temporal localization is near zero on VEBench (tIoU ≤ 0.11). Ask critics concrete, localized questions rather than for scores (confidence: high).
- Back critics with deterministic measurements: loudness via ebur128 or pyloudnorm, silence and click detection, an ASR round-trip diff, and Audiobox Aesthetics production quality (CC-BY-4.0). Avoid NISQA's non-commercial weights (confidence: high).
- Revise only while a P0 or P1 issue remains, for at most about 5 rounds. Accept a revision only if it wins a position-swapped comparison against the current best and passes every deterministic gate, and stop after 2 rounds with no win. Treat the 'normal + adversarial + meta-judge' critic design as a hypothesis to ablate, since VISTA's ablations of it are noisy (confidence: medium).
- Package craft knowledge as human-written Agent Skills (agentskills.io format) loaded through the harness's own load_skill tool. Keep non-negotiable rules in validators, and ablate one skill at a time. Don't cite the +23 pp result as general, since it comes from a single task (confidence: medium).
- Build creator memory CIPHER-style from the diff between the agent's EDL and the creator's final export, plus chat feedback. Store preferences as context-tagged statements the creator can view and edit, and retrieve about k=5 into the brief. Validate with real creators, since CIPHER used only simulated users (confidence: medium).
- Make the primary metric blind human pairwise preference on a phone: about 200 independent pairs to detect 60/40 and about 800 for 55/45, inflated for clip and rater clustering, analysed with Bradley–Terry or mixed effects. Support it with a binary-rubric VLM grader calibrated on held-out clips (never pruning items using the test set), and never let a Claude-only judge score Claude's output (confidence: high).
- Provider-agnosticism: keep a thin in-house interface for each role (director, watcher, jury), with native SDKs for media roles and LiteLLM, Pydantic AI or OpenRouter only for text roles. Give the Anthropic-only mode an 'ear' through self-hosted Qwen3-Omni-Captioner audio captions and a crop tool on frames, label it lower-confidence, and measure it against the Gemini-watcher setup (confidence: medium).
- Treat reference-video style cards as an experiment: build them from measured statistics (PySceneDetect, speech rate, caption density, LUFS) and A/B test them, because no evidence yet shows they improve quality (confidence: low).

## Corrections by fact-checker
- [confirmed] Anthropic's multi-agent research system beat a single Opus agent by 90.2%, and Anthropic says multi-agent fits poorly when all agents must share context or depend on each other. → Both quotes are verbatim. The setup was an Opus 4 lead with Sonnet 4 subagents, compared with Opus 4 alone, on internal research evals. Anthropic also says token usage alone explains 80% of the variance on BrowseComp, so part of the gain is simply more compute. https://www.anthropic.com/engineering/multi-agent-research-system
- [confirmed] Cognition (2026): multi-agent works best when writes stay single-threaded, and reviewers work best with no context shared with the coder. → The quote is verbatim, from a post dated April 22, 2026. https://cognition.com/blog/multi-agents-working
- [confirmed] VISTA ablations: a normal-only judge fell from 35.0 to 17.2; the full system rose from 35.5 to 45.9; without the planner it reached 35.1 (single-scene) and 38.8 (multi-scene); without the pairwise tournament, 33.3 at iteration 5; humans preferred VISTA 66.4% of the time; published at CVPR'26. → The numbers match arXiv v1, Table 3, and the paper is in the CVPR 2026 proceedings. Four caveats. (1) The normal-only collapse happens only in single-scene; in multi-scene it fell just from 35.3 to 33.3. (2) Adversarial-only collapsed in multi-scene, from 35.3 to 26.7. (3) The curves are noisy and not monotonic. (4) VISTA is a video-generation prompt optimizer whose judges run on Gemini 2.5 Flash, not an editor. Its tournament is bidirectional, swapping positions. https://arxiv.org/html/2510.15831
- [confirmed] EditDuet: adding the critic moved failures from 23.8% to 19.5% and coverage from 68.5% to 82.7%; exploration brought failures to 8.2%; humans preferred the full system over the editor alone 85.7% vs 14.3%. → The numbers are right, and the 85.7/14.3 figure is from the Human Preference column. Missing context: both agents are Llama-3.1-8B, and the critic reads a text timeline, not the rendered video. Humans preferred full EditDuet over editor+critic only 64.9% vs 35.1%. https://arxiv.org/html/2509.10761v1
- [confirmed] GLANCE: removing bottom-up negotiation lowered quality from 3.45 to 3.24; its judge reached ρ=0.67. → Its domain is music-synced mashup editing, not talking heads. The judge figure is Spearman 0.67, with Kendall τ 0.51. https://arxiv.org/html/2604.05076
- [corrected] Gemini: 1 fps by default with custom fps; about 100/300 tokens/s; audio 32 tokens/s; the top Pro model is Gemini 3.1 Pro (preview). → The numbers are right but the picture is incomplete. The researcher missed four things. (1) A new `processing: "agentic"` mode lets the model adapt frame rate and resolution as it navigates the timeline: 'up to 88% fewer tokens' and about 7% higher quality, supported on Gemini 3.5–3.8 Flash but not 3.1 Pro. (2) Gemini 3 per-part media_resolution is about 70 tokens/frame by default and 280 at high, and high is needed for small text such as captions. (3) start_offset and end_offset clip a video, which allows zoomed re-inspection of one section. (4) Gemini 3.8 Flash is the newest stable model, while 3.1 Pro is still preview. https://ai.google.dev/gemini-api/docs/video-understanding
- [corrected] OpenAI has no native video input; openai-node#1778 was closed in March 2026 with 'extract frames' advice; up to 1,500 images per request. → The issue was opened on 2026-03-18 and closed on 2026-08-12 as 'not planned'. It is an SDK repo: the maintainer said video needs API and model support, so this is not an API roadmap statement. The vision docs confirm up to 1,500 images and 512 MB per request, and they never mention video. https://github.com/openai/openai-node/issues/1778
- [corrected] VEBench: Gemini-2.5-Pro scored 34.65% and GPT-4o 24.68% on 5-option technique recognition; audio is essential; on-screen tags beat separators, 44.44% vs 40.23%. → The 5-option format (20% chance) and the recognition scores are right. The 44.44% vs 40.23% comparison comes from the footage-selection subtask (OpSim-FS), not from technique recognition. The researcher omitted the most important finding: on temporal localization nearly every model scored tIoU ≈ 0, and the best, Gemini-2.5-Pro, scored 0.11. VLM timestamps must never be used directly as edit coordinates. https://arxiv.org/pdf/2605.03276
- [corrected] AgenticVBench: the calibrated VLM grader agreed with experts 96.4–98.2% on binary items; editor notes added +23 pp on Repurpose; stripping descriptions cost −27 pp on Assembly. → The +23 pp comes from a single task, comedy_knead, not the Repurpose family. The −27 pp is a mean across 18 Assembly tasks. The 96–98% agreement is between the grader and held-out expert labels, measured after 'items with low agreement are revised, split … or removed'. The paper does not name the grader model. That figure is therefore not comparable with the 67–81% for holistic pairwise judges. https://arxiv.org/html/2605.27705
- [unverifiable] AgenticVBench's best system averages 38.4% (GPT-5.6), against experts at 81–95% (per Crayotter). → The primary AgenticVBench paper reports the best score as about 0.38 on Assembly (GPT-5.5 on Codex), the best Repurpose score as 0.30 against an expert 0.95, and agent–expert gaps of 43–65 pp. Crayotter's table reports a 38.4 average for a GPT-5.6-based configuration, but the setup is ambiguous. Crayotter-9B itself averages 15.7. https://arxiv.org/html/2608.02694
- [confirmed] Agent Skills: metadata ~100 tokens, SKILL.md <5k tokens and under 500 lines, references one level deep; skills on the Claude API have no network and no package installs; Codex reads the same format. → All confirmed. Custom skills on the API run only inside the code-execution container; you can upload them via /v1/skills, but they don't sync across surfaces. OpenAI's docs say its skills build on the open standard. No Responses-API skill support is documented, so a server engine should load skills through its own tool, as proposed. https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- [confirmed] FusioN: +6% at N=2, gains plateau around N≈7; PairJudge knockout gives 40–60% relative gains; debiased knockout adds +0.07 Pearson. → The scope is narrower than implied. FusioN was tested on text tasks (mArenaHard, WMT24++, MGSM) with a 111B fusor. PairJudge's 40–60% applies only to the hardest 50% of MATH-500. The knockout paper's +0.07 covers exam scoring and machine translation, and 'debiased' is not in its abstract. https://ar5iv.labs.arxiv.org/html/2510.00931
- [corrected] Per-dimension forced choice cut self-preference by 31.5% (SPB paper), and cross-family judging is a mitigation. → The paper's mechanism is 'a structured multi-dimensional evaluation strategy grounded in cognitive load decomposition'. It does not test cross-family judging. The strongest evidence for cross-family judging is PoLL: a panel of three models from different families beat a single GPT-4 judge (κ 0.763 vs 0.627 on Natural Questions) with less intra-model bias. https://arxiv.org/abs/2604.22891
- [corrected] 'When LLMs are asked to both reason and produce structured output simultaneously, performance often degrades' (Prompt-Driven Agentic). → That paper states this with no ablation or citation. The better evidence is 'Let Me Speak Freely?' (EMNLP 2024 Industry), which found reasoning drops under strict format constraints. On Opus 5.5, thinking is always on and prompts that push the model to reproduce its reasoning in the reply can be refused (`reasoning_extraction`). So take the reasoning from thinking blocks, and use the text reply only for the brief itself. https://aclanthology.org/2024.emnlp-industry.91/
- [corrected] The lead editor should be Claude Opus 5.5 (test high/xhigh). → Because cost is ignored, Claude Fable 5.1 ($10/$50, default effort high) is the top-tier candidate. Anthropic's docs direct you to it for 'demanding reasoning and long-horizon agentic work, or when your evals on Claude Opus 5.5 at higher effort still fall short'. A/B test Fable 5.1 against Opus 5.5 at xhigh and max. No Claude model accepts audio or video. https://platform.claude.com/docs/en/about-claude/models/overview
- [confirmed] AVENUE: Gemini-3.1-Pro agreed with human pairwise judgments 66.2% of the time, and Qwen3-Omni 67.0%, against 71.1% between humans. → Table 4 also shows Gemini-3.1-Pro with the best rank correlation (Kendall τb 0.440 vs 0.353 for Qwen3-Omni) and leave-one-out agreement of 73.6% (vs 72.5%). Gemini-3.6-Flash scored 64.0%. The task is generative audio-video editing. https://arxiv.org/pdf/2609.04253
- [confirmed] Descript's Underlord is a mix of models from different providers, and Opus 4.6 was 13–23% better at 'doing what you asked'. → Both claims hold. The 13–23% figure is 'intent adherence over leading frontier models' across 100+ test cases, from an Anthropic customer case study, so it is vendor-published. The Underlord v2 LinkedIn quote could not be verified. https://claude.com/customers/descript
- [corrected] Sample sizes: 100 pairs gives ±10 pp; about 195 pairs detects 60/50; about 780 detects 55/50. → The arithmetic is right: the 95% CI half-width at p=0.5 is 9.8 pp, n≈194 detects 60/50, and n≈782 detects 55/50, all at 80% power and two-sided α=0.05. But it assumes independent pairs. Pairs that share a clip or a rater are correlated, so inflate n by the design effect, or fit a Bradley–Terry or mixed-effects model with clip and rater as random effects. https://arxiv.org/html/2404.18796
- [confirmed] Repo facts: remotion-dev/skills 4,743 stars, no license; video-use MIT, 27.4k stars; LiteLLM NOASSERTION; Qwen3-Omni Apache-2.0; PySceneDetect BSD-3; agentskills Apache-2.0, 25.7k stars; AgenticVBench Apache-2.0; kamgasimo 0 stars. → The GitHub API confirms all of these. Qwen3-Omni was last pushed 2026-04-23, and kamgasimo/ai-video-editor was pushed today and has no track record. Qwen3-Omni also ships a 30B-A3B-Captioner model for detailed, low-hallucination audio captions. https://github.com/QwenLM/Qwen3-Omni

## Missed items added
- Gemini 'agentic' video processing (`processing: "agentic"`, Gemini 3.5–3.8 Flash): the model navigates the timeline itself, adapting frame rate and resolution. Google reports up to 88% fewer tokens and about 7% higher quality. Combine it with per-part media_resolution (70 or 280 tokens/frame) and start_offset/end_offset to zoom in on seams at high fps. [Gemini video docs](https://ai.google.dev/gemini-api/docs/video-understanding); [media resolution](https://ai.google.dev/gemini-api/docs/media-resolution)
- Gemini 3.8 Flash is now the newest stable Gemini model, while 3.1 Pro is still preview. In AVENUE, Gemini-3.6-Flash agreed with humans 64.0% of the time vs 66.2% for 3.1-Pro, so the watcher model is an empirical choice. [Gemini models](https://ai.google.dev/gemini-api/docs/models); [AVENUE](https://arxiv.org/pdf/2609.04253)
- Claude Fable 5.1 is the top-tier lead-editor candidate when cost is ignored. [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- Opus 5.5 guidance: giving Claude crop, zoom and measure tools (a container with PIL and OpenCV, or a crop tool) improves accuracy on dense visuals, which is directly relevant to Claude as a frame-sheet critic. Asking Claude to reproduce its reasoning in the reply can trigger a `reasoning_extraction` refusal. [Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)
- PoLL juries: a panel of three judges from different model families beat a single GPT-4 judge (κ 0.763 vs 0.627; Pearson 0.917 vs 0.817 on Chatbot Arena) and showed less intra-model bias. [Replacing Judges with Juries](https://arxiv.org/html/2404.18796)
- Scaling test-time compute for agents: list-wise verification (63.0) beat scoring (59.4) and voting (56.8) on GAIA. Reflecting selectively on a score threshold beat reflecting every step. Mixing model families across parallel rollouts raised Pass@4 to 74.55. [arXiv 2506.12928](https://arxiv.org/html/2506.12928)
- NumPro: overlaying frame numbers on video frames improves temporal grounding in video LLMs (CVPR 2025, MIT code). This is primary evidence for burning in frame and word IDs. [NumPro](https://arxiv.org/abs/2411.10332)
- VEBench temporal localization: models score tIoU ≈ 0, and the best (Gemini-2.5-Pro) reaches 0.11. Map critic notes to word IDs or measured boundaries; never apply VLM timestamps directly. [VEBench](https://arxiv.org/pdf/2605.03276)
- VideoArgus: rubrics written for each sample (criteria, failure modes, evidence plans), graded by VLM tools, correlate better with humans than benchmark-specific evaluators. Code and data are CC BY 4.0. It supports writing a binary rubric per Edit Brief. [VideoArgus](https://arxiv.org/abs/2608.05485)
- VQQA: critique generated as dynamic questions to a VLM, used as 'semantic gradients', gave +11.57% on T2V-CompBench within a few rounds. This supports asking critics concrete questions rather than for scores. [VQQA](https://arxiv.org/abs/2603.12310)
- VideoWeaver: expert-written composition skills scored best (0.620 output), above judge-feedback-evolved skills (0.582) and base skills (0.552). Removing composition skills hurt planning badly (0.465). Human-authored skills should come first and auto-evolution second. [VideoWeaver](https://arxiv.org/html/2606.08091v1)
- Audio quality measurements for critics: Meta Audiobox Aesthetics scores production quality, enjoyment, complexity and usefulness, and its repo is CC-BY-4.0. NISQA's weights are CC BY-NC-SA, which bars commercial use. pyloudnorm (MIT) measures loudness. [audiobox-aesthetics](https://github.com/facebookresearch/audiobox-aesthetics); [NISQA license](https://github.com/gabrielmittag/NISQA)
- Qwen3-Omni-30B-A3B-Captioner (Apache-2.0) turns audio into detailed text captions. It is the practical 'ear' for an Anthropic-only configuration where Claude cannot hear. [Qwen3-Omni](https://github.com/QwenLM/Qwen3-Omni)
- Provider-agnostic adapters lag behind provider-specific media features. LiteLLM only added per-video fps and media_resolution in January 2026 (#19026), and Gemini's agentic mode and stateful previous_interaction_id belong to its newer Interactions API. Use native SDKs for the watcher role. [LiteLLM #19026](https://github.com/BerriAI/litellm/issues/19026)
- 'Let Me Speak Freely?' (EMNLP 2024 Industry) is the real evidence that strict format constraints degrade reasoning. [ACL Anthology](https://aclanthology.org/2024.emnlp-industry.91/)

## Tools
- Agent Skills specification (agentskills.io) [Knowledge packaging / progressive disclosure; Apache-2.0] https://github.com/agentskills/agentskills — 25.7k stars, last push 2026-08-09. An open standard that Anthropic originated and OpenAI Codex also reads. Metadata costs ~100 tokens; SKILL.md should stay under 5k tokens / 500 lines. On the Claude API, skills need a code-execution container with no network, so a server engine should load them through its own tool.
- Gemini API video understanding (Gemini 3.1 Pro preview; 3.x Flash) [Video+audio critic / watch notes; Proprietary API] https://ai.google.dev/gemini-api/docs/video-understanding — Takes native video with audio; 1 fps by default with custom fps; ~100–300 tokens/s. Gemini-3.1-Pro agreed with human pairwise judgments 66.2% of the time vs 71.1% between humans (AVENUE). Gemini-2.5-Pro led VEBench technique recognition, but only at 34.65%. Render with burned-in timecode and word IDs, and ask concrete questions.
- Claude memory tool [Creator memory; Anthropic API feature] https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool — Official. Client-side /memories store on all Claude 4+ models, with SDK helpers. Storage lives in your own database; path-traversal validation is required.
- PRELUDE / CIPHER [Preference learning from user edits; MIT] https://github.com/gao-g/prelude — NeurIPS 2024 paper. Cut edit distance 31% (summaries) and 73% (email) with simulated users. The repo has 46 stars and was last pushed in 2024. Use the method, not the code. It has not been validated with real humans.
- remotion-dev/skills [Reference agent skills (render/captions); None declared on GitHub] https://github.com/remotion-dev/skills — 4,743 stars, pushed 2026-09-25. Includes 12 skills such as remotion-captions and remotion-render. Check the license before copying; Remotion itself uses a custom license.
- browser-use/video-use [Reference editing skill + critic sub-agent; MIT] https://github.com/browser-use/video-use — 27.4k stars (possibly inflated), pushed 2026-09-24. Critic sub-agent is briefed to roast, with a cap of 3 passes. Borrow prompts and patterns; known audio-click bug.
- kamgasimo/ai-video-editor [Reference skill with progressive-disclosure reference files and verify gate; MIT] https://github.com/kamgasimo/ai-video-editor — SKILL.md plus reference/cutting.md and review.md. Strongest mechanical verification (round-trip ASR diff). Brand-new repo with 0 stars.
- FableCut remake-reel skill [Reference-video style measurement; MIT] https://github.com/ronak-create/fablecut/blob/main/skills/remake-reel/SKILL.md — 694 stars, pushed 2026-09-25. Deterministically extracts cuts, beats, BPM and energy curve. Built for music montages; adapt it for talking-head style cards.
- PySceneDetect [Shot/cut measurement for style cards; BSD-3-Clause] https://github.com/Breakthrough/PySceneDetect — 5.2k stars, pushed 2026-09-21. Measures shot lengths and cut rate in reference videos.
- Qwen3-Omni [Open-weight video+audio critic (fallback/second opinion); Apache-2.0] https://github.com/QwenLM/Qwen3-Omni — 4.0k stars. As a judge in AVENUE it agreed with humans 67.0% of the time vs 71.1% between humans. Self-hostable, giving a judge from a third model family.
- LiteLLM [Provider-agnostic model adapter; MIT core (GitHub reports NOASSERTION because of an enterprise directory)] https://github.com/BerriAI/litellm — 59.7k stars, pushed 2026-09-28. Alternatives: Pydantic AI (MIT, 20.2k stars) or OpenRouter (commercial; used by Descript).
- Pydantic AI [Provider-agnostic agent loop; MIT] https://github.com/pydantic/pydantic-ai — 20.2k stars, pushed 2026-09-28. Fits Yunicorn's existing Pydantic EDL.
- AgenticVBench [External agent-editing benchmark + rubric grader; Apache-2.0] https://github.com/PhiloLabs/agentic-vbench — 100 expert tasks. VLM grader agreed with experts 96–98% on binary items. Best system averages 38.4% vs experts at 81–95%. Long-form tasks; use it as a sanity check, not as the main eval.
- VISTA (method) [Iterative multi-critic + pairwise tournament pattern; Paper (CVPR 2026)] https://arxiv.org/abs/2510.15831 — Humans preferred its outputs 66.4% of the time. Ablations show the planner, the tournament and the adversarial judges each matter. Built for video generation; transfer the loop design.
