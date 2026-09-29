# A multi-provider agent loop where users bring their own API key (Claude first; OpenAI, Gemini and OpenRouter/open models also accepted)

## Summary
Run the editing agent loop in Yunicorn's own backend, and use Pydantic AI (Python, MIT; the Vercel AI SDK if the engine is TypeScript) as a thin layer with native Anthropic, OpenAI and Google adapters. Don't use OpenAI-format translators such as LiteLLM, and don't use the Claude Agent SDK, in the critical path. Two reasons. First, all three providers now return hidden reasoning state that must be sent back exactly as received, or the request fails or quietly loses the reasoning. Second, the Claude Agent SDK is effectively Claude-only.

Assign models by role. Claude Opus 5.5 (or Fable 5.1) is the Director and the fresh-context Critic. Gemini is an optional "Watcher" tool because it is the only frontier model with native video and audio input. GPT-6 Astra is an alternative Director. Open models get helper roles only.

When a model lacks a capability, fall back: video becomes frames plus precomputed audio features, strict schemas become server-side validation, and images in tool results become a tagged user message. Only models that pass the regression eval become Director or Critic.

BYOK should stay optional, with a house Claude key as the quality default. Keep the key in the iOS Keychain, send it per job over TLS, hold it only in memory on the server, check it with the provider's list-models call, map errors to clear messages, and show Apple's third-party-AI consent screen. Apple has rejected at least one app for BYOK under 3.1.1, but many BYOK apps are live. Keep every feature purchasable in-app.

## Verified findings
## Bottom line (after fact-check)
The researcher's architecture holds up: Yunicorn owns the loop, uses Pydantic AI's native per-provider models, keeps one provider-native append-only history per role, and puts a certification gate on roles. Six corrections change the details:
1. LiteLLM's Anthropic passthrough is not safe either.
2. Opus 5.5 → Fable 5.1 escalation preserves thinking.
3. OpenAI's encrypted reasoning is returned by default.
4. Gemini's agentic video mode is Flash-only.
5. Under a quality-only criterion, Fable 5.1 must be evaluated as the default Director, not only as an escalation.
6. BYOK should never downgrade the Watcher or Critic. The house should hold keys for every provider and route each role to its best certified model.

## 1. Harness choice
- **Claude Agent SDK is not usable as the multi-provider layer.** It runs the Claude Code binary. Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway" ([LLM gateways](https://code.claude.com/docs/en/llm-gateway)). Third parties may not offer claude.ai login "unless previously approved" ([Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)).
- **Pydantic AI** (MIT, 20.2k★, v2.51.0 on 2026-09-25) is the right model layer for a Python backend.
  - It documents that Opus 5.5 and Fable 5.1 reject forced tool choice and bind thinking to the conversation prefix ([docs](https://pydantic.dev/docs/ai/models/anthropic/)).
  - Caveat: when the prefix changes, it auto-retries with `prefix_mismatch_behavior='drop_block'`. That silently discards reasoning, so treat every such retry as a harness bug and alert on it.
  - Images returned by tools stay inside the tool result for Anthropic, OpenAI Responses and Gemini 3. Audio or video returned by a tool raises `NotImplementedError` on Anthropic and OpenAI Responses ([tools-advanced](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/)).
  - Video input works on Google models only. Pydantic AI does pass Gemini `video_metadata` (fps, offsets) and per-part `media_resolution` through `vendor_metadata` ([google.py](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/google.py)).
  - Durable execution with Temporal, DBOS, Prefect or Restate makes each model and tool call resumable ([durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)). Never pass BYOK keys as step inputs, because those are persisted.
- **Vercel AI SDK** (Apache-2.0, `ai@7.0.118` on 2026-09-27) has equivalent Anthropic coverage: adaptive thinking, cacheControl, compaction, programmatic tool calling, images via `toModelOutput` ([provider docs](https://ai-sdk.dev/providers/ai-sdk-providers/anthropic)). Use it only if the engine is TypeScript.
- **LiteLLM** (59.7k★): keep it out entirely, not just out of the critical path.
  - #24985 and #15601 are now closed. The open bug [#42550](https://github.com/BerriAI/litellm/issues/42550) (2026-09-22) shows the native `/v1/messages` passthrough strips signed thinking blocks with empty text. That is Opus 5.5's default `display: omitted` output, so replayed reasoning is lost silently.
  - Supply-chain incident: 1.82.7 and 1.82.8 were live on PyPI for about 40 minutes on 2026-03-24. 1.82.8 shipped a `.pth` file that runs at interpreter start and stole env vars and cloud keys. The root cause was a compromised Trivy dependency in CI ([advisory](https://docs.litellm.ai/blog/security-update-march-2026)).
- **OpenAI Agents SDK:** LiteLLM and any-llm adapters are "best-effort, beta", and traces upload to OpenAI by default ([models](https://openai.github.io/openai-agents-python/models/)). Not recommended.
- **LangGraph, Mastra, ADK, aisuite, instructor:** stars and licenses as reported (checked via GitHub API). None is needed for a single-agent loop.

## 2. Reasoning-state parity (correctness, therefore quality)
- **Claude Opus 5.5 / Fable 5.1:**
  - Thinking blocks go back "complete and unmodified", including blocks with empty text. Edited, reordered or dropped blocks get a 400 ([migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)).
  - For accounts created on or after 2026-08-31, or with `prefix_mismatch_behavior: "error"`, editing earlier history returns a 400 ([errors](https://platform.claude.com/docs/en/api/errors)).
  - **Correction:** Fable 5.1 and Mythos 5.1 *can* read Opus 5.5 thinking; other models drop it (no 400). So Opus → Fable escalation can continue in the same conversation.
  - Opus 5.5 still rejects forced `tool_choice`, non-default temperature/top_p/top_k, prefill and `thinking: disabled`.
  - Append-only history can still adapt: Opus 5.5 accepts mid-conversation `role: "system"` messages, and per-message effort and inline tool changes are in beta.
- **Gemini 3:**
  - Missing `thought_signature` returns a 400, and only the first parallel call carries one ([thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)).
  - Dummy signatures are allowed for injected or foreign function calls.
  - This is a real BYOK failure mode in the wild ([genoffice #769](https://github.com/genspark-ai/genoffice/issues/769)).
- **OpenAI:** with `store=false` or ZDR, reasoning items carry `encrypted_content` by default; `include` is legacy. You must echo all reasoning, function-call and output items since the last user turn ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)).
- **OpenRouter:** `reasoning_details` must be passed back unmodified and in order ([reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)).
- **Refusals (missed):** Opus 5.5 and Fable 5.1 have safety classifiers that return `stop_reason: "refusal"` as a normal response. Categories include `general_harms`, which "benign work can also trigger" ([refusals](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)). The loop must handle this. The beta server-side `fallbacks` option retries on another Claude model within the call and names the model that answered. Log it, because it changes the Director mid-job.
- **Rule:** fix provider and model per role per job. The only mid-conversation switches allowed are Claude→Claude escalation or refusal fallback.

## 3. Schema and API shape differences
- Claude strict mode strips `minimum`, `maximum`, `multipleOf`, `minLength` and `maxLength`. The SDKs validate them locally ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)). Keep Pydantic validation plus readable tool-result errors.
- `GET /v1/models` returns `capabilities` fields: batch, citations, code_execution, context_management strategies, effort levels, image_input, pdf_input, structured_outputs, thinking types ([Models API](https://platform.claude.com/docs/en/api/models/list)).
  - It does *not* flag forced-tool or sampling-parameter rejection, so the live probe is still needed.
- **Anthropic errors** ([errors](https://platform.claude.com/docs/en/api/errors)):

  | Error | What to do |
  |---|---|
  | 400 on a spend limit | Surface to the user |
  | 401 (invalid or expired key) | Surface to the user |
  | 402 `billing_error` | Surface to the user |
  | 403 | Surface to the user |
  | 413 over 32 MB | Watch base64 frames |
  | 429 without `retry-after` (tier cap) | Not retryable |
  | 429 / 500 / 504 / 529 | Retry with backoff |

## 4. Media and vision (quality-critical)
- **Native video and audio:** among the big three, only Gemini. GPT-6 Astra takes text and images; all current Claude models take text, images and PDF ([GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Claude models](https://platform.claude.com/docs/en/about-claude/models/overview)).
- **Gemini video details** ([video docs](https://ai.google.dev/gemini-api/docs/video-understanding)):
  - Defaults: 1 fps; low resolution at 66 tokens/frame (about 100 tokens/s); high resolution at 258 tokens/frame. Audio is 32 tokens/s.
  - Length limits: about 3 h at low resolution, 1 h at high.
  - Uploads: under 100 MB inline; Files API up to 20 GB paid.
  - `start_offset`/`end_offset` clipping and custom fps are supported. Fps is reportedly capped at 24 ([Google Cloud](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding); weak, snippet only).
  - Timestamps are **MM:SS only**, so code must anchor anything sub-second.
  - **Correction:** "agentic video understanding" (loading transcript, frames or audio on demand, "up to 88% more token-efficient") is listed only for 3.8, 3.7 and 3.6 Flash and 3.5 Flash-Lite, not for 3.1 Pro.
- **Gemini model status** ([models](https://ai.google.dev/gemini-api/docs/models)): Gemini 3.1 Pro is still `-preview`, and no 3.5 Pro exists. 3.8 Flash has been stable since 2026-09-02, at $0.75/$3.75 until 2026-12-31, then $1.50/$7.50 ([Google](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/)).
  - For the Watcher, evaluate 3.8 Flash (stable, agentic mode) against 3.1 Pro (preview) on dense clip checks. No public benchmark settles which is better.
- **Claude vision tiers (missed)** ([vision](https://platform.claude.com/docs/en/build-with-claude/vision), [coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)):
  - Claude 4.7 and later use the high-resolution tier: 2576 px long edge and 4784 visual tokens. A 1080×1920 frame goes through unresized at about 2,691 tokens.
  - Above 20 images per request, every image is capped at 2000 px.
  - Use the Files API for frames in long loops, and `oversized_image: "error"` to stop silent downscaling.
  - Frame-strip resolution and count must be a per-provider setting, not one constant.
- **Other native audio-video models (missed):**
  - Qwen3.8-Omni-Flash (2026-09-18, API-only, 1M context, video up to 2 h). Qwen claims its audio quality is above Gemini 3.8 Flash; that is vendor-only evidence ([MarkTechPost](https://www.marktechpost.com/2026/09/18/alibaba-qwen-releases-qwen3-8-omni-flash/)).
  - Qwen3-Omni is Apache-2.0 with open weights ([GitHub](https://github.com/QwenLM/Qwen3-Omni)).
  - Both are Watcher candidates only if they pass the eval.
- **Harness sensitivity:** in AgenticVBench, GPT-5.5 scored 0.38/0.37/0.18 on Assembly under Codex/OpenCode/OpenClaw. Qwen3-VL scored 0.009 vs 0.073 depending on harness. The best stack "barely crosses 30%" against experts at 0.81–0.95 ([AgenticVBench](https://arxiv.org/html/2605.27705)). Prompts and tools must be tuned and certified per provider.

## 5. Models for editing roles (quality-only criterion)
| Model | Verified facts | Role |
|---|---|---|
| **Claude Fable 5.1** | Released 2026-09-01. $10/$50, 1M context / 128K output, default effort high. Anthropic: "demanding reasoning and long-horizon agentic work, or when your evals on Claude Opus 5.5 at higher effort still fall short" ([overview](https://platform.claude.com/docs/en/models/fable-5-1/overview)) | **Co-default Director candidate.** Cost and latency are ignored, so it should not be reserved for hard cases |
| **Claude Opus 5.5** | Released 2026-09-22. $4/$20, 1M/128K, default effort medium (xhigh and max available). Anthropic says "start with" it; Chartography 89.0%, GDPval-AA 1846 vs 1542 for GPT-6 Astra (max effort). Anthropic also says the Fable 5.1 gap is "narrower than these scores suggest" ([announcement](https://www.anthropic.com/claude-opus-5-5)) | Director at xhigh/max; fresh-context Critic |
| **GPT-6 Astra** | Released 2026-09-03/04. $10/$50, 1.05M/128K, text+image, effort low to max, Responses and Chat Completions ([OpenAI](https://developers.openai.com/api/docs/models/gpt-6-astra)) | Alternative Director; cross-family Critic |
| **Gemini 3.8 Flash / 3.1 Pro (preview)** | See §4 | Watcher; native audio-video Critic of the *rendered* clip |
| **Open models** | Helper roles only; mostly coding benchmarks (secondary evidence) | Helpers |

**Weak evidence:** no benchmark compares the current generation on video-editing agent tasks. Choose the Director by Yunicorn's own blind pairwise eval: Fable 5.1 at high/xhigh vs Opus 5.5 at xhigh/max vs GPT-6 Astra at high/xhigh.

## 6. Role slots and fallbacks
- **Director:** tool use, image input, 200K+ context.
- **Watcher:** native video and audio, exposed as a tool. It answers targeted questions on clipped ranges at high fps; code converts its MM:SS answers to frames.
- **Critic:** fresh context and a different family from the Director, because LLM judges favor their own outputs ([Panickssery et al.](https://arxiv.org/abs/2404.13076)). Split it in two:
  - Gemini watches and hears the rendered MP4 (music ducking, caption sync, audible seams).
  - Claude judges high-resolution frames, the transcript and deterministic metrics.
  - The quality gain from this split is unmeasured, so it needs an ablation.
- **Helper:** caption cleanup and search queries.
- **Fallbacks by missing capability:**

  | Missing capability | Fallback |
  |---|---|
  | Video or audio input | Dense frame strips plus deterministic features |
  | Images inside tool results | Tagged user message |
  | Strict schemas | Pydantic validation with retry |
  | Parallel tool calls | Sequential calls |
  | Small context | Compaction and context editing |
  | Weaker model | Macro tools plus more guards |

- **Certification** uses an existing harness, not a custom one: Pydantic Evals (same repo), [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) (MIT), or promptfoo (MIT, but [OpenAI-owned since March 2026](https://openai.com/index/openai-to-acquire-promptfoo/), a neutrality concern).
  - Example gate: at least 45% blind pairwise preference against the house default.
  - Anything uncertified is labelled "experimental".

## 7. BYOK design
- **Quality framing (new):** with cost ignored, BYOK can only match or lower quality compared with house keys. The house should hold Anthropic and Google keys, and ideally OpenAI too.
  - A user's key should fill only the Director slot, and only with a certified model.
  - The Watcher and Critic stay on house keys whatever key the user brings.
  - This replaces the researcher's "features-only fallback" rows for Anthropic and OpenAI BYOK, which were a quality downgrade.
  - The consent screen must then name every provider that receives data.
- **iOS:**
  - Store the key in the Keychain with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, never UserDefaults.
  - Send it per request over TLS in a header. For Anthropic that is `Authorization: Bearer` (`x-api-key` is legacy); for Gemini, `x-goog-api-key` ([auth](https://platform.claude.com/docs/en/manage-claude/authentication)).
- **Server:**
  - Hold the key in memory for the job's lifetime only, never in queue or durable-workflow payloads.
  - If it must be stored, use KMS envelope encryption.
  - Scrub auth headers from logs, Sentry and OpenTelemetry, and disable Agents SDK tracing.
  - Pin dependencies by hash.
  - House key: use Anthropic Workload Identity Federation instead of a static secret.
- **Guide users:**
  - Anthropic warns that uploading a key to a third-party tool gives its developer "access to your Claude Console account" ([support](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)).
  - Recommend an identity-backed personal key scoped to one workspace, with expiry (presets of 3 h, 1 d, 7 d or 30 d, or custom) and a workspace spend limit.
- **Connection UX (missed):**
  - OpenRouter's [OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth) returns a user-controlled key with no copy-paste.
  - There is no sanctioned OAuth that bills a user's Claude or ChatGPT subscription. Anthropic blocks consumer OAuth tokens outside its own apps ([secondary](https://www.mindstudio.ai/blog/anthropic-openclaw-ban-oauth-authentication)), and OpenAI has not shipped third-party "Sign in with ChatGPT" billing ([forum](https://community.openai.com/t/login-with-chatgpt-allow-users-to-use-their-own-plus-subscription-in-3rd-party-apps/1378506), weak).
- **OpenRouter BYOK terms:** 5% fee after a free allowance of $25k/month at list price on pay-as-you-go. Shared-capacity fallback is on by default; disable it ([BYOK](https://openrouter.ai/docs/guides/overview/auth/byok)).
- **App Attest** is the alternative to BYOK: 1-hour, workspace-billed, Messages-only tokens for attested iOS apps.

## 8. Precedents and platform terms
- **Cursor:** the key is sent to Cursor's backend with every request and not stored; ZDR doesn't apply to BYOK requests ([Cursor](https://cursor.com/help/models-and-usage/api-keys)).
- **Raycast iOS:** BYOK since 2026-05-29, including use "without a Pro subscription" ([changelog](https://www.raycast.com/changelog/ios/1-2-13)).
- **Vyra:** an OAuth MCP server with 50+ tools. Its docs say claude.ai use requires Pro or Max ([Vyra](https://www.usevyra.com/docs/mcp)).
- **Apple 3.1.1:** one BYOK app was rejected in September 2024 because it "uses API keys to unlock or enable functionality" ([forum](https://developer.apple.com/forums/thread/763884)). The thread has no resolution, and no newer primary case was found.
- **Apple 5.1.2(i):** apps must "clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission" ([guidelines](https://developer.apple.com/app-store/review/guidelines/)).
- **Gemini API terms** ([terms](https://ai.google.dev/gemini-api/terms)):
  - Users must be 18+.
  - On unpaid tiers, content is used to improve Google products and may be human-reviewed.
  - Apps offered in the EEA, UK or Switzerland must use paid services only.
  - The API is "not for consumer use". A consumer creator entering their own Gemini key remains a legal grey area.
- No explicit BYOK prohibition was found from Anthropic, OpenAI or Google.

## Verified recommendations
- Own the loop in the Python backend and use Pydantic AI (MIT, v2.51) as the model layer. Keep escape hatches to the raw anthropic, openai and google-genai SDKs. Its Google model already passes video_metadata (fps, offsets) and per-part media_resolution. Alert on any Anthropic drop_block auto-retry, because it silently discards reasoning.
- Keep LiteLLM out entirely, including its /v1/messages passthrough: open bug #42550 strips Opus 5.5's signed empty thinking blocks, and PyPI was compromised in March 2026. Also keep the Claude Agent SDK, the OpenAI Agents SDK and LangGraph out of the critical path. If the long tail of open models is wanted, reach it through OpenRouter via Pydantic AI, restricted to helper roles.
- Keep one append-only, provider-native history per role per job. Allowed mid-conversation changes: Claude to Claude escalation (Fable 5.1 reads Opus 5.5 thinking), mid-conversation system messages, and beta per-message effort. Echo Gemini thought signatures and OpenAI reasoning items exactly. Handle Claude stop_reason 'refusal' explicitly and log any server-side fallback model.
- Choose the Director by blind pairwise eval with cost and latency ignored: Fable 5.1 at high/xhigh vs Opus 5.5 at xhigh/max vs GPT-6 Astra at high/xhigh. Do not assume Opus 5.5 at its medium default, because Anthropic positions Fable 5.1 for demanding long-horizon agentic work.
- Use house keys for every provider so each role gets its best certified model. BYOK fills only the Director slot, and only with a certified model. The Gemini Watcher and the cross-family Critic stay on house keys regardless of which key the user brings. Name every provider in the 5.1.2(i) consent screen.
- Split the Critic. Gemini watches and hears the rendered MP4 on clipped ranges at high custom fps; code converts its MM:SS answers to frames. Claude judges high-resolution frames (tier limits 2576 px / 4784 tokens; at most 20 images per request, or 2000 px each above that), the transcript and deterministic metrics. Ablate against a frames-plus-features-only baseline, since no evidence yet shows the gain.
- Evaluate Gemini 3.8 Flash (stable; agentic video mode) against Gemini 3.1 Pro (still preview) for the Watcher and Critic before committing. Treat Qwen3.8-Omni-Flash (API-only) and Qwen3-Omni (Apache-2.0) as eval-gated alternatives, since their claims are vendor-only.
- Certify per role with an existing harness (Pydantic Evals or Inspect AI). Tune prompts and tools per provider, because AgenticVBench shows harness swings as large as model swings. Label uncertified models 'experimental'.
- Run jobs with Pydantic AI durable execution (Temporal, DBOS, Prefect or Restate) so out-of-memory restarts resume. Never place BYOK keys in step inputs or queue payloads; hold them in worker memory, or use KMS envelope encryption if they must be stored. Use Anthropic Workload Identity Federation for the house key.
- BYOK UX and security: store the key in the iOS Keychain (AfterFirstUnlockThisDeviceOnly) and send it in a header over TLS. For Anthropic that header is Authorization: Bearer. Recommend a personal key scoped to one workspace, with an expiry and a spend limit. Offer OpenRouter via OAuth PKCE. Validate with list-models plus a live probe, because the Models API doesn't flag forced-tool or sampling rejections. Map 400 spend-limit, 401, 402, 403, 413 and cap-429 errors to clear messages, and never fall back silently to a different billing key.
- App Store posture: keep every feature purchasable via in-app purchase, frame BYOK as 'use your own AI account', and explain it in the review notes. For Gemini, warn users that free keys allow training and human review, and that the EEA, UK and Switzerland need paid keys. Treat Gemini's 'not for consumer use' and 18+ terms as an open legal question before offering Google BYOK to consumers.

## Corrections by fact-checker
- [confirmed] The Claude Agent SDK only reaches Claude models, Anthropic doesn't support routing Claude Code to non-Claude models through gateways, and third parties may not offer claude.ai login. → Confirmed verbatim, with one qualifier: the login ban applies "unless previously approved". Separately (secondary source), consumer-plan OAuth tokens have been blocked server-side outside Claude Code and claude.ai since early 2026. https://code.claude.com/docs/en/agent-sdk/overview ; https://code.claude.com/docs/en/llm-gateway ; https://www.mindstudio.ai/blog/anthropic-openclaw-ban-oauth-authentication
- [confirmed] Pydantic AI: MIT, 20.2k stars, v2.51 released 2026-09-25, and its docs cover the Opus 5.5 / Fable 5.1 forced-tool and thinking-binding quirks. → Confirmed: MIT, 20,217 stars, v2.51.0 on 2026-09-25. One addition: on accounts where binding is enforced, Pydantic AI automatically retries with prefix_mismatch_behavior='drop_block'. That keeps requests from failing but silently drops earlier reasoning, so drop_block retries should be logged as a harness bug. https://pydantic.dev/docs/ai/models/anthropic/ ; gh api repos/pydantic/pydantic-ai/releases
- [corrected] Open question: does Pydantic AI pass Gemini videoMetadata (fps, offsets, media_resolution) through? → Yes. In models/google.py, vendor_metadata on a video part maps to video_metadata (fps, start/end offsets), and media_resolution can be set per part (including ULTRA_HIGH). The Watcher tool does not need a raw google-genai escape hatch just for these settings. https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/google.py
- [corrected] LiteLLM has open thinking-block round-trip bugs #24985, #42550 and #15601, and its /v1/messages passthrough avoids translation. → #24985 was closed on 2026-08-24 and #15601 on 2026-05-20. Only #42550 (opened 2026-09-22) is open. That bug is in the /v1/messages passthrough itself: it strips signed thinking blocks whose text is empty, which is Opus 5.5's default display=omitted output, so replayed reasoning is lost silently. The passthrough does not avoid the problem. https://github.com/BerriAI/litellm/issues/42550 ; https://github.com/BerriAI/litellm/issues/24985 ; https://github.com/BerriAI/litellm/issues/15601
- [confirmed] On 2026-03-24, LiteLLM PyPI 1.82.7 and 1.82.8 shipped a credential stealer that ran at import time. → Confirmed, with more detail. The versions were live for about 40 minutes from 10:39 UTC before quarantine. 1.82.8 added litellm_init.pth, and .pth files execute when the Python interpreter starts, not only when litellm is imported. The stealer took env vars, SSH and cloud keys and Kubernetes tokens. The root cause was a compromised Trivy dependency in LiteLLM's CI. https://docs.litellm.ai/blog/security-update-march-2026 ; https://github.com/BerriAI/litellm/issues/24518
- [corrected] Other Claude models can't read Opus 5.5's thinking at all. → Claude Fable 5.1 and Mythos 5.1 do read Opus 5.5 thinking blocks; no other model does. A block the target model can't read is dropped rather than rejected. Escalating Opus 5.5 to Fable 5.1 mid-conversation therefore keeps the reasoning, and the step does not have to restart. https://platform.claude.com/docs/en/models/opus-5-5/migration-guide ; https://platform.claude.com/docs/en/api/errors
- [confirmed] Opus 5.5 returns 400 on forced tool_choice, non-default temperature/top_p, prefill and disabled thinking. Thinking blocks must be returned unmodified, and accounts created after 2026-08-31 get a 400 when they edit earlier history. → Confirmed verbatim. Binding enforcement applies to accounts created on or after 2026-08-31 00:00 UTC, or to any request that sets block_binding.prefix_mismatch_behavior='error' (beta header thinking-binding-controls-2026-08-01). https://platform.claude.com/docs/en/models/opus-5-5/migration-guide ; https://platform.claude.com/docs/en/api/errors
- [confirmed] Gemini 3 returns 400 when function calls lack thought_signature, and only the first call in a parallel batch carries one. → Confirmed. The same doc also allows dummy signatures ("skip_thought_signature_validator") for function calls injected from another model's history, so moving history into Gemini is possible (reasoning is not carried over). https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
- [corrected] A stateless OpenAI tool loop needs include: ["reasoning.encrypted_content"]. → With store=false or zero data retention, reasoning items now include encrypted_content by default. The include value is accepted only for compatibility and is not required. What is still required is passing back every reasoning item, function call and function output since the last user message. https://developers.openai.com/api/docs/guides/reasoning
- [corrected] Gemini video: 1 fps default, 66 (low) or 258 tokens per frame, 32 tokens/s of audio, about 3 h of video. An agentic mode on 3.5 Flash and later uses up to 88% fewer tokens. → The frame and audio numbers are right; low resolution (66 tokens per frame, about 100 tokens/s) is the default, and 1 h is the limit at high resolution. "Agentic video understanding" is listed only for Gemini 3.8, 3.7 and 3.6 Flash and 3.5 Flash-Lite, not for 3.1 Pro. The doc's wording is "up to 88% more token-efficient", and the model loads transcript, frames or audio on demand. Custom fps can be set; Google Cloud docs reportedly cap it at 24 (weak: search snippet only). Timestamps are MM:SS only. https://ai.google.dev/gemini-api/docs/video-understanding ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding
- [confirmed] GPT-6 Astra: released 2026-09-03/04, $10/$50, 1.05M context, text+image input, effort low to max. → Confirmed. Also: 128K max output, cached input $1, available on both Responses and Chat Completions. https://developers.openai.com/api/docs/models/gpt-6-astra ; https://en.wikipedia.org/wiki/GPT-6_Astra
- [corrected] Quality-first default: Opus 5.5 as Director, Fable 5.1 only for hard cases. → Anthropic says "start with" Opus 5.5 for most workloads. It also says to use Fable 5.1 "for demanding reasoning and long-horizon agentic work, or when your evals on Claude Opus 5.5 at higher effort still fall short." Default effort is medium on Opus 5.5 and high on Fable 5.1. The brief ignores cost and latency, so the default should be decided by a blind head-to-head: Fable 5.1 at high/xhigh vs Opus 5.5 at xhigh/max. Anthropic itself says the Opus 5.5 to Fable 5.1 gap is "narrower than these scores suggest". https://platform.claude.com/docs/en/about-claude/models/overview ; https://www.anthropic.com/claude-opus-5-5
- [confirmed] Anthropic error mapping: 401 authentication, 402 billing, 403 permission, 400 on a spend limit, 429 without retry-after on a tier cap, and 429/529 transient. → Confirmed. The list is missing 413 request_too_large (32 MB Messages limit; relevant for base64 frames), 500 api_error and 504 timeout_error. Expired keys return 401. https://platform.claude.com/docs/en/api/errors ; https://platform.claude.com/docs/en/manage-claude/authentication
- [corrected] OpenRouter BYOK charges a 5% fee and falls back to shared capacity by default. → The fee is 5% of the normal OpenRouter cost, but only after a plan-dependent free allowance measured at list price: $25,000/month on pay-as-you-go and $200,000 on Enterprise. Shared-capacity fallback is the default and can be disabled per model or per provider. https://openrouter.ai/docs/guides/overview/auth/byok
- [confirmed] Apple rejected a BYOK app under 3.1.1 in September 2024, and 5.1.2(i) requires explicit consent before sharing personal data with third-party AI. → Both confirmed verbatim. The forum thread shows no resolution. No newer primary evidence of BYOK-specific rejections was found. https://developer.apple.com/forums/thread/763884 ; https://developer.apple.com/app-store/review/guidelines/
- [confirmed] Raycast iOS has had BYOK since 2026-05-29, usable without a Pro subscription. → Confirmed (v1.2.13; Anthropic, Google, OpenAI, OpenRouter). https://www.raycast.com/changelog/ios/1-2-13
- [confirmed] Gemini API terms: 18+, unpaid services used to improve products and human-reviewed, EEA/UK/CH paid-only, 'not for consumer use'. → All four confirmed verbatim. Paid services do not use prompts or responses to improve products. https://ai.google.dev/gemini-api/terms
- [confirmed] App Attest issues Claude API tokens to attested iOS apps that bill the developer's workspace and last one hour. → Confirmed. The tokens also authorize only Messages API calls. https://platform.claude.com/docs/en/manage-claude/authentication
- [confirmed] AgenticVBench: the same model scored 0.38 vs 0.18 by harness, GPT-5.5 won every family, and the best stack scored 31%. → The model was GPT-5.5 on Assembly: 0.38 on Codex, 0.37 on OpenCode, 0.18 on OpenClaw. The paper says the best stack "barely crosses 30%" (0.38 vs expert 0.81 on Assembly; 0.30 vs 0.95 on Repurpose). https://arxiv.org/html/2605.27705
- [confirmed] Claude strict mode drops numeric constraints. → minimum, maximum, multipleOf, minLength and maxLength are stripped from the wire schema. The SDKs append them to the field description and validate them locally. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- [confirmed] Gemini 3.1 Pro is preview-only, and Gemini 3.8 Flash has been stable since 2026-09-02 at $0.75/$3.75 introductory. → Confirmed; no 3.5 Pro exists. The introductory price runs through 2026-12-31, then $1.50/$7.50. Google calls 3.8 Flash "our most intelligent Flash model". https://ai.google.dev/gemini-api/docs/models ; https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/

## Missed items added
- Fable 5.1 reads Opus 5.5 thinking blocks, so a job can escalate from Opus to Fable within the same conversation without losing reasoning. A block from a model the target can't read is dropped, not rejected. (https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)
- Opus 5.5 and Fable 5.1 have safety classifiers that can return stop_reason "refusal" as a normal response, not an error. The categories include general_harms, which benign content can trigger. A beta server-side `fallbacks` option retries on another Claude model inside the same call. The loop must handle this stop reason explicitly and record which model answered. (https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)
- Claude vision tiers. Claude 4.7 and later use a high-resolution tier: 2576 px long edge and 4784 visual tokens, so a 1080x1920 frame is sent unresized at about 2,691 tokens. Requests with more than 20 images drop to a 2000 px per-image cap. Limits are 600 images and 32 MB per request. The Files API keeps payloads small, and `oversized_image: "error"` turns silent downscaling into an error. Frame-strip sizing should be set per provider. (https://platform.claude.com/docs/en/build-with-claude/vision ; https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)
- Append-only history can still adapt mid-job. Opus 5.5 accepts mid-conversation role:system messages. Per-message effort (beta) and inline tool changes (inline-tools-2026-09-15) avoid breaking the cache or the thinking binding. (https://platform.claude.com/docs/en/models/opus-5-5/migration-guide ; https://platform.claude.com/docs/en/models/fable-5-1/overview)
- Pydantic AI durable execution, co-maintained with Temporal, DBOS, Prefect and Restate, makes each model and tool call a durable step. That fits Yunicorn's history of out-of-memory restarts. Caveat: workflow history persists step inputs, so BYOK keys must never be step arguments. (https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- Gemini accepts start/end offsets and custom fps. Custom fps is reportedly capped at 24 per Google Cloud docs (weak evidence). Clip-level dense checks are therefore possible on the rendered output, e.g. 10 s at 24 fps is 240 frames. Timestamps are MM:SS only, so code must anchor the results. (https://ai.google.dev/gemini-api/docs/video-understanding ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/video-understanding)
- Qwen3.8-Omni-Flash (2026-09-18) is API-only. It takes audio and video, has a 1M context, and handles video up to 2 h. Qwen claims audio quality above Gemini 3.8 Flash; that is vendor-only evidence. Qwen3-Omni (Apache-2.0, open weights) is a self-hostable model that both sees and hears, but it is older. These are the only non-Google models found that can act as a native audio-video Watcher. (https://www.marktechpost.com/2026/09/18/alibaba-qwen-releases-qwen3-8-omni-flash/ ; https://github.com/QwenLM/Qwen3-Omni)
- OpenRouter's OAuth PKCE flow gives the app a user-controlled key without the user copy-pasting it. That is the only sanctioned connect-your-account flow found. Anthropic blocks consumer OAuth outside its own apps, and OpenAI has no 'Sign in with ChatGPT' billing for third parties. (https://openrouter.ai/docs/guides/overview/auth/oauth ; https://community.openai.com/t/login-with-chatgpt-allow-users-to-use-their-own-plus-subscription-in-3rd-party-apps/1378506)
- Anthropic Workload Identity Federation removes the static house key. Anthropic now recommends Authorization: Bearer (x-api-key is legacy). Identity-backed personal or service-account keys, scoped to one workspace, are recommended over legacy workspace keys; guide BYOK users to that setup. (https://platform.claude.com/docs/en/manage-claude/authentication)
- Cross-provider certification can use existing eval harnesses: Pydantic Evals (in the pydantic-ai monorepo), Inspect AI (MIT, 2.9k stars, active) and promptfoo (MIT, 25.5k stars). promptfoo has been OpenAI-owned since March 2026, which matters when using it to rank OpenAI against Anthropic models. (https://github.com/UKGovernmentBEIS/inspect_ai ; https://openai.com/index/openai-to-acquire-promptfoo/)
- Anthropic's advisor tool (an executor consulting Opus or Fable mid-generation) is mainly a cost lever. With cost ignored, running Fable 5.1 or Opus 5.5 solo is simpler and at least as good, so it is not needed. (https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)

## Tools
- Pydantic AI [Provider-agnostic agent / model layer (Python); MIT] https://github.com/pydantic/pydantic-ai — 20.2k stars; v2.51 released 2026-09-25, pushed 2026-09-27. Native Anthropic features (caching, effort, compaction, task budgets); docs cover Opus 5.5/Fable 5.1 forced-tool and thinking-binding quirks. Images returned by tools stay inside the tool result for Anthropic, OpenAI Responses and Gemini 3. Recommended model layer. Audio/video returned by a tool raises NotImplementedError on Anthropic/OpenAI. Supports OpenRouter, Bedrock, Vertex and Foundry.
- Vercel AI SDK [Provider-agnostic agent / model layer (TypeScript); Apache-2.0] https://github.com/vercel/ai — 27k stars; ai@7.0.118 released 2026-09-27. Anthropic provider supports cacheControl, adaptive thinking/effort including claude-opus-5-5, tool-result images via toModelOutput, compaction and programmatic tool calling. Choose instead of Pydantic AI only if the engine is TypeScript. Mastra builds on it.
- LiteLLM [Unified OpenAI-format client / proxy gateway; MIT (enterprise/ directory separately licensed)] https://github.com/BerriAI/litellm — 59.7k stars and very active. Open thinking-block round-trip bugs (#24985, #42550, #15601). March 24, 2026 PyPI compromise (1.82.7/1.82.8 credential stealer). Keep out of the critical path; if used, only for the open-model long tail, pinned by hash. Its /v1/messages passthrough avoids translation.
- OpenRouter [Hosted multi-provider router (OpenAI-compatible); Proprietary service] https://openrouter.ai/docs — Passes cache_control through for Anthropic/Gemini; reasoning_details must be echoed back. Video: AI Studio only YouTube links, Vertex only base64. Its BYOK charges a 5% fee and falls back to shared capacity by default. Good way to accept one 'other provider' key that covers open models. Disable shared-capacity fallback if users expect only their own key to be billed.
- Claude Agent SDK [Claude-only agent harness; MIT (use governed by Anthropic Commercial Terms)] https://github.com/anthropics/claude-agent-sdk-python — 8.2k stars, pushed 2026-09-25. Providers: Anthropic API, Bedrock, Claude Platform on AWS, Google Cloud Agent Platform, Microsoft Foundry. Anthropic doesn't support non-Claude models via gateways. Not suitable for multi-provider BYOK. Runs a CLI subprocess per session.
- OpenAI Agents SDK [Agent framework (OpenAI-first); MIT] https://github.com/openai/openai-agents-python — 29.7k stars. Non-OpenAI models via LiteLLM/any-llm are 'best-effort, beta'; traces go to OpenAI by default. Not recommended; with BYOK, disable tracing.
- LangGraph [Graph orchestration; MIT] https://github.com/langchain-ai/langgraph — 42.4k stars, active; the langchain-litellm wrapper loses reasoning state (#222). Unneeded for a single-agent tool loop.
- Mastra [TypeScript agent framework; Apache-2.0 core; ee/ directories under a separate license] https://github.com/mastra-ai/mastra — 28.4k stars, active; built on the AI SDK. Only if TypeScript and workflow/memory primitives are wanted.
- aisuite [Unified chat API plus light agents; MIT] https://github.com/andrewyng/aisuite — 16.3k stars; last push 2026-09-18; OpenAI-shaped interface across providers, Agents API, MCP. Lightweight but hides provider-native reasoning and caching features (inference).
- instructor [Structured-output extraction; MIT] https://github.com/567-labs/instructor — 13.9k stars, active. Not an agent loop; Pydantic AI already covers it.
- Google ADK [Agent framework (Gemini-first); Apache-2.0] https://github.com/google/adk-python — 21.7k stars, active; non-Gemini models via LiteLLM. Not needed; call Gemini through its native SDK or Pydantic AI.
- any-llm (Mozilla.ai) [Unified provider client; Apache-2.0] https://github.com/mozilla-ai/any-llm — 2.2k stars; used as an OpenAI Agents SDK adapter (beta). Alternative to LiteLLM; same translation caveats.
- Anthropic Python SDK [Official provider SDK; MIT] https://github.com/anthropics/anthropic-sdk-python — Official; pushed 2026-09-22; full feature coverage (effort, compaction, Files API, strict tools). Escape hatch for Claude-specific features.
- google-genai (Python) [Official provider SDK; Apache-2.0] https://github.com/googleapis/python-genai — Official; pushed 2026-09-27; video metadata (fps, media_resolution), Files API up to 20 GB paid, agentic video mode. Use for the Gemini Watcher tool.
- openai-python [Official provider SDK; Apache-2.0] https://github.com/openai/openai-python — Official, 31.7k stars; Responses API with encrypted reasoning items. Use the Responses API, not Chat Completions, for tool loops with images.
