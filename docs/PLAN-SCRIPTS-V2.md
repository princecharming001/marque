# Yunicorn scripts v2: plan

2026-10-19. Revision 2. Built from: a read of `main` (131bb90), the September realism runs and the 09-23 prod smoke, three codebase maps, three research passes (competitor teardown, hooks/trends/costs, human-sounding text + voice cloning), then an engineering review and a product/craft review whose changes are folded into §5–9. Nothing in this document is implemented.

## 0. Status (2026-10-19, branch `scripts-v2`, nothing pushed or deployed)

| Phase | State | Commits |
|---|---|---|
| 0. Meter, strip temperature, Haiku 5.5 | done | bbf81bc, c2dc121 |
| 1. Two-day slice (lint, payoff-first slots, own-length, opener dedup) | done | f856f3d, 014b54b |
| 2. Writer v2 behind `SCRIPTS_V2` | done | 1f05322, ff98dc8, (cold-start fixes) |
| 3. Voice profiles + `/v1/voice` | done (backend); app screen written, uncompiled | 1f05322, 73acb53 |
| 4. Niche angles, Hook Lab v2, mimic beats | done | 1f05322 |
| 5. App build | Swift written, logic tests 366/366; **not compiled for iOS** (no Xcode on this Mac) | 73acb53 |
| 6. opener_frame bandit dim, mocks, docs | done, dimension gated by `BANDIT_OPENER_FRAME` until the migration is applied | 1f05322 |
| 6. Retire v1 | **not done on purpose**: v1 is the kill switch until v2 has run live | |

Realism eval, 8 personas, independent Opus judge (spoken / substance / specificity / honesty; hard tells per script from the v2 lint):

| Run | hard tells | spoken | substance | specificity | honesty | would film | p50 | metered $ / page |
|---|---|---|---|---|---|---|---|---|
| old main, first paint (Haiku 4.5) | 0.88 | 7.5 | 7.7 | 7.2 | 8.5 | 21/24 | 11.6 s | ~$0.02 (est.) |
| slice, first paint (Haiku 5.5) | 0.22 | 8.0 | 7.7 | 7.4 | 9.1 | 22/23 | 7.6 s | $0.002 |
| slice, full (Opus 4.8) | 0.29 | 8.1 | 8.0 | 7.6 | 9.2 | 24/24 | 68 s | ~$0.30 |
| v2, first paint (Haiku 5.5), r2 | **0.00** | 7.7 | 6.9 | 6.3 | **9.4** | 20/24 | 10.7 s | ~$0.002 |
| v2, full (Sonnet 5.5), r2 | **0.00** | 7.8 | 7.1 | 6.5 | 9.2 | 22/24 | 20 s | ~$0.03 |
| v2, full (Opus 5.5 writer A/B) | **0.00** | 7.8 | 7.4 | 6.8 | 9.2 | 23/24 | 29 s | ~$0.05 |

What it says: the eval personas have **no reels**, so it measures the cold-start case, not v2's main lever (the creator's own speech as few-shot + material). There, v2 removes every hard tell and invents nothing, and the judge scores it less specific than the slice, partly because v1 invents specifics. The live smoke with a creator who has spoken reels carried their phrasing ("you guys", "honestly", "okay so", "do the boring thing") with zero hard tells on the full path. `SCRIPTS_V2_MIN_SAMPLES=N` keeps creators with fewer than N spoken reels on the slice if you prefer that trade.

Rollout, each step needs a "push it now":
1. Merge `scripts-v2` → `main`, deploy marque-api with `SCRIPTS_V2` unset (= off). Phases 0-1 go live for everyone (metering, Haiku 5.5, slice); v2 stays dark.
2. `SCRIPTS_V2=allowlist`, `SCRIPTS_V2_ALLOWLIST=<owner's creator id>`, redeploy; owner runs "Rebuild from my reels" (or the digest) and compares.
3. Apply the `post_registry.opener_frame` migration, then `BANDIT_OPENER_FRAME=1`. Optional: `NICHE_TRANSCRIPT_PASS=1` (~$0.75/niche/week).
4. App build with the phase-5 Swift (first real compile), TestFlight.
5. After a week live on the allowlist: `SCRIPTS_V2=on`, then retire the v1 prompt blocks.

## 1. Why

The owner: "the scripts seem very weirdly written a lot of the time." Goal: scripts that (1) use hooks grounded in what actually goes viral, (2) stay current with trends, (3) fit this creator and niche, (4) sound like this creator, learned from their past reels, (5) sound like something a human would say out loud. Cost per user must stay small.

## 2. What is wrong today (measured, not guessed)

Source: `backend/prompts.py` on `main` (131bb90), the September realism runs in `backend/eval/out/script_realism_*.json`, and the 09-23 prod smoke (`prod_smoke_2026-09-23.json`, 39 scripts from the live API).

### 2a. Every script has the same shape

Four consecutive prod scripts for a "fitness for desk workers" creator:

- "You're stretching before lifting and sabotaging your workout."
- "Sitting up straight at your desk isn't fixing your back. It's making it worse."
- "The reason you can't build muscle isn't cardio. It's your calorie math."
- "Your back hurts because of where you sit, not how strong you are."

All four are the same hook: negate a belief, reframe it. All four bodies run belief → "that's backwards" → mechanism → one tip → tidy closing one-liner ("Your lower back will thank you, not your desk setup."). That is one writer with one trick. A viewer who sees three of these in a week reads it as a template. The 09-23 realism judge said the same thing in different words ("pure filler", "'Fashion' as a literal noun sounds robotic in the mouth").

Across the 79 scripts saved in `eval/out/script_realism_*.json`: 46% of hooks are `contrarian`, "here's what" appears in 43%, 17 of 79 CTAs start with "Save this", median length 148 words (over the 140 cap). The mock fallback template is literally "Most {niche} advice is backwards. Here's what the top 1% actually do."

### 2a'. There are two writers and users mostly see the cheap one

`_generate_scripts` (Opus hooks ×6 at temp 1.0 → Haiku hook judge → one Opus write per slot → Haiku judge → Opus revise → speakability repair → honesty repair) is the "full" path. The feed paints page 0 with `_fast_feed_scripts`: the same Haiku slot planner, then one **Haiku** call per slot with the rule "hook is ONE sentence; the body is exactly THREE short paragraphs of two or three sentences each; the CTA is ONE sentence", 13 s timeout, no hooks pass, no judge. Chat scripts (`_chain_scripts`) are the fast path too. The Opus upgrade of page 0 runs in the background. So most first impressions are a Haiku draft under a fixed 1-3-1 shape, and idea-bank scripts get hook/CTA guessed by sentence-splitting with `hookSignal` hard-coded to `curiosity`.

Model constants in `prompts.py:18-20` are `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`. Temperature is API default (1.0) for all writing.

### 2b. The prompt is a rule pile

`scripts_prompt()` builds a ~4,100-token system prompt from twelve doctrine blocks (VOICE_DOCTRINE, TITLE_DOCTRINE, TALKING_HEAD_MANDATE, SHOTPLAN_RULE, CRAFT_RULES_BLOCK, VIRALITY_BLOCK, GROUNDING_BLOCK, style rubric, BODY_FORMAT_RULE, CRAFT_EXAMPLES_BLOCK, a JSON exemplar, LENGTH_BUDGET) plus a self-check. 34 lines contain never / must / always / hard. Most rules are individually sensible. Together they over-constrain: the model satisfies the checklist and the result is correct, safe, and flat. The one "RIGHT" example (the freelancer-client script) and the one JSON exemplar (the 2-minute inbox rule) are both the same contrarian reframe, so the model learns that shape as the house style.

### 2c. "Voice" is four caption openers

What we collect: at onboarding `_run_digest` scrapes 10 posts from the creator's handle and transcribes the top 4 by views with AssemblyAI (transcript capped at 1,500 chars). Stored in `creator_posts` (max 10 rows, timestamps dropped). `creator_media` (the creator's own uploaded takes, with transcripts) is never used for voice.

What reaches the writer: `_voice_exemplars()` sends the top-4 posts as `opener (120 chars) + cadence (100 chars)`, and it reads `caption or transcript`, so whenever a caption exists the spoken words are ignored. `hooks_prompt` is called with no posts at all, yet its winners are forced verbatim as each script's opener. `/v1/scripts` only sees posts the client sends, and the client never holds posts. Real production profiles are mostly niche + audience + goal, so most scripts are written with no voice data.

### 2d. Hooks are a vocabulary, not research

Eight hand-named signals (`SIGNAL_LIST`), 30 hand-written `NICHE_PRIORS`, three example hooks inside `hooks_prompt`, and a judge. `backend/knowledge/hooks.md` has a different 7-type taxonomy and only feeds the edit brief. Meanwhile `reels_cache` already holds transcribed top reels per niche (≥10k views, refreshed on an 18 h TTL) and nothing mines their openers. The bandit (`arm_stats`) learns per signal label, never per hook.

### 2e. Trends do not reach the writer

`_refresh_niche_trends` clusters the reels corpus by format on a 24 h TTL and caches it in memory only (the `trends` table in doc 06 was never built). `/v1/trends` and the feed's trend card are display-only; `converse_user` can take trends but the call site omits them; `AppStore.generateFromTrend` exists and no UI calls it. The only trend→script path is user-initiated `/v1/mimic`.

### 2e'. Spend on the main path is unmetered

`main.anthropic()` records nothing to `ai_usage`; only background modules log hard-coded token estimates. There are no per-user caps. We cannot currently say what a script page costs.

### 2f. Length and structure are fixed by mandate

LENGTH_BUDGET pins every script to 90–140 words (35–55 s) with 2–4 paragraphs and a hook/body/CTA JSON schema. Real creators in most niches vary between 15 s and 90 s and often have no CTA. A fixed envelope is one more reason the output reads as one template.

### 2g. Quality loop judges the same blind spots

The pipeline judge (`script_judge_prompt`) scores hook_strength / specificity / format_fit / voice_match / slop / fabricated and triggers `script_revise_prompt`. It shares the generator's rubric, so it can't catch "this sounds like the last ten." The September realism judge (`eval/script_realism_eval.py`) is independent and good; it is an offline tool, not a gate.

## 3. What other products do

Checked: Captions/Mirage, Submagic, Syllaby, Predis, Opus Clip, Vizard, quso, Munch, Taplio, Hypefury, Jasper, Copy.ai, Writesonic, HubSpot, Canva, Creatify, Arcads, Castmagic, Descript, TokScript, Subscribr/OverseerOS, Virlo/vidanalyze hook tools, TikTok Creative Center's script generator.

- **Voice.** No video tool learns voice from a creator's own reels; they offer tone dropdowns, which reviewers call generic. Voice learning lives in text tools and has converged on one shape: 3–20 samples → an **editable generated profile** (Jasper ~4.5k chars; HubSpot extracts "sentence structure, pronoun usage, tone, formatting") **plus verbatim excerpts kept as few-shot**, with a side-by-side with/without-voice preview. Taplio's own guidance: "Give it your posts, not a description of your tone… 10 to 20 of your own posts changes the output more than any prompt wording." The closest analogs (OverseerOS "Creator DNA", Subscribr "channel memory") analyze selected channel transcripts and let the user review the extracted patterns before saving.
- **Hooks.** Converged UX: 5–10 hooks, each labelled by mechanism (question / curiosity gap / POV / contrarian / numbered / stakes), creator picks; sometimes a per-hook score with a lever label. Opus and Vizard publish their score as plain-English rubric questions (Hook / Flow / Value / Trend), and reviewers trust the breakdown more than the number. Practitioner evidence (Blackman for Ali Abdaal, Jenny Hoyos): hook ≤ 3 s, built from target + transformation + stakes, "promise not process", write the hook and last line first, one concept per video, ~34 s, but/therefore transitions never and-then, don't linger after a payoff.
- **Trends.** Mostly marketing. Opus/quso have an opaque "trend" factor; Syllaby/Munch serve keyword lists. The only approaches reviewers call useful ground in actual winning content: TokScript (paste a viral transcript → reuse its structure), Creatify (library of real ads decomposed into hook/script/beats).
- **Cost.** Syllaby charges 1 credit per 1-minute script (~$0.03–0.09); Subscribr ~$4–5/script at list; TokScript $10/mo unlimited. A Sonnet pipeline at ~5k in / 800 out is ~$0.02 per script before caching; Haiku ~$0.001.
- **Patterns to avoid (documented):** tone pickers as voice; "Are you tired of…" and credential openers; overpromising hooks; the AI tells (not-X-but-Y, rule of three, em-dash cadence, closer that restates, "rehearsed vulnerability resolving into a moral"); invented stats; opaque single-number virality scores; repetitive hook pools; hashtag lists sold as personalization.

**Our opening:** we already scrape and transcribe the creator's reels and the niche's top reels. Nobody in the video category uses that for voice or hooks. The plan below is mostly plumbing what we have into the writer, plus one new data product (a per-niche hook/trend corpus).

## 4. What the research says

### 4a. Hooks

- **No public hook→retention dataset exists.** The 5.6B-row TikTok metadata dump on HuggingFace is CC-NC and has no transcripts; TikTok's Research API is academic-only. Open-source "script generators" (MoneyPrinterTurbo, ShortGPT) are pipelines with generic prompts and zero hook science. So the hook corpus has to be ours, mined from scraped top reels. We already scrape and transcribe them (`reels_cache`).
- **Best quantitative study available:** OpusClip, 34,635 TikToks, Jan–Mar 2026. Top-performing hook types, in order: outcome/result-first, expert-explainer setup (credential → gap → promise), contrarian, specific number, imperative. Documented losers: "Let me tell you about…", greetings, "Okay so…", "Have you ever wondered…". Vendor data, treat as directional.
- **Platform guidance:** Mosseri (2025): hook in 3 s, on-screen text (muted viewers), say what the video is about early, cliffhangers not required; ranking signals are watch time, likes/reach, sends/reach. YouTube Shorts' metric is "viewed vs swiped away". TikTok's 2026 trend report themes: unpolished realism, curiosity detours, emotional payoff.
- **Numbers to design with:** ~2.5 words per second spoken → a 3-second hook is ≤ 8 words; the 1.5-second swipe decision is ~4 words. Under-60 s videos have the highest completion rates (Wistia 2025, Vidyard).
- **Text features that predict attention/sharing (peer-reviewed):** high-arousal emotion (Berger & Milkman 2012); simple words plus anxious/exciting/hopeful uncertainty sustain attention, sadness loses it (Berger, Moe & Schweidel 2023); curiosity peaks at mid-confidence, so hook what the niche viewer half-knows, not total unknowns (Loewenstein 1994; Kang 2009); concrete > abstract (Packard & Berger 2021); second person raises involvement (Cruz et al. 2017). Zero-shot LLM judges predict video popularity about as well as trained models (Kayal et al., ACL 2025), which justifies a cheap LLM judge. Follower count still dominates virality (Ling et al. 2022): text is a real but modest lever; don't oversell "predictedScore".
- **Implication:** replace the 8 hand-named signals with a corpus-backed library: real spoken openers from top reels in the creator's niche, each labeled by type and freshness, retrieved per request as exemplars. Rank hook *templates* per niche with a cheap pairwise judge (Haiku, position-swapped, ~$0.001/pair) and later with first-party outcomes.

### 4b. Trends (sources, cost, freshness)

| Source | Access | Cost | Notes |
|---|---|---|---|
| TikTok search/hashtag videos | Apify `clockworks/tiktok-scraper` (we already use it) | ~$1.70 / 1k results | real-time; caption ≠ speech |
| TikTok spoken hooks | Apify `clockworks/tiktok-transcript-extractor` | $1.70 / 1k + $0.027/min when no subtitles | first 5 s of VTT = the real hook line |
| TikTok Creative Center (hashtags, songs, top ads) | no official API; Apify `datapeak/tiktok-creative-center` | $1 / 1k rows | daily, by country + industry |
| Instagram hashtag reels | Apify (we already use it) | ~$2.30 / 1k | |
| YouTube Data API | official, free 10k units/day | $0 | `search.list` short + viewCount + publishedAfter = 100 units; Shorts need a duration/HEAD check |
| Exa / Tavily | search APIs | $7 / 1k req; ~$0.005 / credit | good for "what's new in niche" topics, not hook lines |
| Google Trends, Reddit, X | pytrends dead, Reddit commercial-ambiguous, X $5/1k posts | skip for v1 | |
| Trendpop / Exolyt / Pentos / Glimpse | SaaS $99–950/mo, no per-call API | skip | |

What this plan does with it: v1 uses only what we already scrape (`reels_cache`, Option A's sources) and adds the weekly transcript pass (Option B, ~$0.75/niche/week) in phase 6 once a niche has active users. YouTube Data API and Creative Center are cheap but are left out of v1 to keep the surface small; Option C (Exa/Tavily topics) only if a news-heavy niche asks for it.

The 3-second retention signal is only observable through first-party OAuth: IG Graph (`ig_reels_avg_watch_time`, shares, saves), YouTube Analytics (`averageViewPercentage`), TikTok Display API. That is the long-term reward signal; scraped views are the interim proxy.

### 4c. Voice and human-sounding text

- **Few-shot with the creator's own transcripts is the strongest lever.** EMNLP Findings 2025 ("Catch Me If You Can? Not Yet"): few-shot vs zero-shot roughly doubled authorship attribution; 2→10 examples added almost nothing. So: 3–5 exemplars in the prompt, selected by **hook/CTA type, not topic** (topic-matched selection made retelling worse). Anthropic's own guidance: 3–5 examples steer tone and structure more reliably than rules.
- **TICL (arXiv 2502.08972):** profile = exemplars + model-generated *negative* samples + one-line explanations of what was off. 76.6% win rate vs fine-tuning with <10 samples; the explanations alone carry most of the gain; it specifically suppresses "additionally / therefore" in favor of "so / honestly". One-time cost per creator, zero at inference.
- **Draft → critic → rewrite** helps (+8–13% personalization, PerFine) when the critic is style-only and the rewrite *cuts* rather than adds; gains plateau after 2 rounds. Single-item rating judges are the worst method (everything gets the same score); **pairwise** judging is reliable.
- **Style embeddings** (Wegmann `Style-Embedding`, HF) picked the real human continuation with 100% accuracy on spoken transcripts where a GPT-4o judge scored 33–50%. A cosine-to-creator-centroid check costs nothing per call.
- **Paraphrase "humanizers" don't work** (Pangram: more fluent humanizer output is *more* detectable). Don't build one.
- **Platform facts (verified against the Claude API reference):** on Sonnet 5.5 / Haiku 5.5 non-default `temperature`/`top_p` return 400, and assistant prefill is removed. Variety must come from best-of-N + a judge and from different inputs per slot, not from sampling. Prices: Sonnet 5.5 $2/$10 per MTok (cache read $0.20), Haiku 5.5 $0.10/$0.50, Opus 5.5 $4/$20. The repo's current constants (`opus-4-8` $5/$25, `sonnet-4-6` $3/$15, `haiku-4-5` $1/$5) are a generation behind: Haiku alone is 10× cheaper on 5.5.
- **Spoken-register facts:** 150–170 wpm conversational; broadcast rule is one idea per sentence, 8–20 words; real speech has fragments, restarts and one-sentence asides; a 45-second reel is ~110–125 words. Transcription of a creator's back catalog via Apify `firsthand/instagram-reel-transcripts` is ~$0.006/reel (≈ $0.60 for 100 reels).
- A 48-item spoken-script AI-tell checklist (openers like "Here's the thing", tricolons, "not X, it's Y", "read that again", formal connectives, missing contractions, summarizing or moralizing endings, template CTAs, wrong address form…) and permissively-licensed lexicons (WSC, slop-guard-rs, humanize-ai-writing, nabokov; MIT) exist to seed a Python lint. Research deliverable with the full list is in the agent report; the lint spec below uses it.

## 5. Target design (revision 2, after an engineering review and a product/craft review)

### Principles

1. **Material before words.** Humans write from particulars: a price, a client, the thing they got wrong last week, a number they actually know. The current writer gets `{topic, angle}` and invents the rest, which is where "your calorie math" comes from. Every slot now starts with two or three real particulars, gathered before any writing.
2. **Payoff first.** Pros design the ending, then write the hook as a promise of it. Each slot carries a `payoff` (what the viewer walks away with); the writer drafts the last line before the first.
3. **Data over doctrine.** Register comes from the creator's own transcripts (and, for cold start, the niche's), not from 2,600 words of rules. The doctrine shrinks to a short, nearly content-free, byte-stable block; it must not become a new house voice.
4. **One dumb writer call.** The script call writes the script and nothing else (no `why`, no alt hooks, no scores). Metadata in the same call pulls the model back into assistant mode mid-sentence.
5. **Variety where viewers feel it.** Viewers don't perceive "mechanism"; they perceive sameness of content, rhythm and emotional temperature. Pages vary on particular, temperature (rant / confession / how-to / reaction / story), length and format, and keep the creator's signature constant. The only hook rule: no two openers on a page share a syntactic frame.
6. **Hooks stay personal; trends supply angles.** The niche corpus tells the planner what the niche is arguing about this week and what viewers are being promised. The creator's own openers are the primary hook exemplars; niche openers are cold-start only and never a shape to copy.
7. **Cheap, independent checks.** A Python lint for the mechanical tells; one Haiku question per script ("could this first line sit on a different video?"). Pairwise voice judging and style embeddings are offline eval tools, not gates.
8. **Everything metered**, from the first commit.

### Components

**A. Writer v2** — new package `backend/app/scripts_v2/`.

- `llm.py`: a new call helper (neither `main.anthropic()` nor `palo_llm.anthropic_cached_json` supports multi-turn messages, `thinking`, `output_config.effort`, or returns `usage`). Supports: multi-turn messages (exemplars as prior assistant turns), `cache_control` on the stable prefix, effort, thinking config per model (Sonnet 5.5: `thinking: {type: "between_tools"}` at effort ≤ high when we want it off; Haiku 5.5: `disabled` only at effort ≤ high; `max_tokens` sized for thinking + output so structured output never truncates), returns `(parsed, usage)`, records to `ai_usage`, never sets `temperature`, keyless → deterministic mock.
- `doctrine.md` (≤ 500 tokens, plain spoken prose, no bullets, no "NEVER"): talk to one person; one idea; open on the thing itself; use the particulars you were given and only those; let sentences be uneven, one aside is fine; stop when it's said. Version-hashed (`doctrine_version` on every script), byte-stability test across two requests for the same creator, headroom test like `test_knowledge.py`.
- `material.py` (Haiku 5.5, one call per page, ~$0.001): given the creator's transcripts, profile and the niche's current angles, return 2–3 **particulars** per slot (a number they said, a thing they do, a mistake they named, a promise the niche is making this week) each tagged with its source (`own_transcript:<id>` | `niche_angle:<id>` | `ask_user`). Writers may only use tagged particulars; the lint rejects untagged numbers/claims. When nothing is available, the slot carries one `ask_user` question and the script uses the existing `[your result]` bracket, as today.
- `planner.py` (Haiku 5.5): N slots `{topic, angle, payoff, temperature, format, target_seconds, particulars[], opener_frame}`. Hard rules: distinct temperatures, distinct formats, lengths spread across the creator's own observed range (fall back to niche range), ≥ 1 slot on a live niche angle when one exists, `opener_frame` distinct per slot and not equal to any of the creator's last 10 openers or the page's others. Respects a creator signature (if 80% of their reels open with a story, most slots do too).
- `writer.py` (Sonnet 5.5, effort `low`, structured output): one call per slot, output `{last_line, script}` where `script` is one continuous spoken text that ends with `last_line`. Prompt order for caching: doctrine → voice profile prose → 3 own-transcript exemplars as prior assistant turns (talking-head, solo, spoken, lightly cleaned; selected by format/CTA habit, not topic) → the slot (payoff, particulars, temperature, length, opener frame to avoid). `hook/body/cta` for `ScriptDTO` are derived with the existing `_split_spoken_script` (`main.py:14615`); `cta` is always non-empty (the last sentence), because `ScriptReaderView.swift:226` renders it as an editable tap target.
- `lint.py` (Python, no LLM): AI-tell lexicon and phrase patterns (seeded from WSC / slop-guard / humanize-ai-writing, MIT, plus the spoken-specific items from §4c), opener blacklist, tricolon and "not X, it's Y" detectors, list markers, wrap-up / moral endings, template CTAs, contraction rate < 0.8, sentence-length std/mean < 0.45, address-form mismatch vs profile, length outside target ± 20%, **untagged particular** (a number, %, "studies show", a named client not in the slot's particulars), > 6-word overlap with any exemplar, duplicate opener frame vs the page and the creator's last 10. Returns named failures; replaces `_ensure_speakable`, `_ensure_honest`, the dash scrubber and `flag_stage_direction` for v2 output.
- `judge.py` (Haiku 5.5, one call per script): "could this first line sit on a different video in this niche? yes/no + the one word that makes it specific." Fail → one Sonnet rewrite with the lint failures and that line, instructed to cut, not add. (Best-of-2 is **not** in v1; if added later it must vary the input — different particulars or exemplar set — because sampling is fixed on 5.5 models and identical inputs give near-twins.)
- `alt_hooks` for Hook Lab come from a separate Haiku call that sees the finished script and the creator's real openers, never from the writer call.
- `pipeline.py`: **first paint stays Haiku 5.5** (plan + material + 3 writes + lint, inside today's budgets: planner 6 s, slot 13 s), replacing `_fast_feed_scripts`' 1-3-1 suffix; `_chain_scripts` (inline in `/v1/converse`) uses the same. The **background upgrade** (`_refresh_feed_page`, `/v1/scripts`) runs the Sonnet writer + judge + rewrite. Sonnet-first-paint is an owner A/B, not a default.
- Steer (`/v1/steer`, `/v1/write/turn`): same voice context and lint; chips map to operations ("shorter" = cut the weakest beat, keep the opener and last line).
- Reference-video mode: `/v1/mimic` already turns one reel into a script. Upgrade it the TokScript way: extract the reel's beat skeleton (hook / turn / payoff / exit) and write *those beats* with the creator's particulars and voice. This is the cheapest honest "trend" feature and reuses the "Steal these" UI.

**B. Voice profile** — stored as a JSONB key on the existing `creator_posts` row (no new table until it earns one); module `scripts_v2/voice.py`.

- The primary object is **one editable prose paragraph** the creator reads ("You talk to 'you guys'. You open mid-story about half the time. You swear a little. You say 'non-negotiable' and 'do the boring thing'. You end by trailing off or with 'try it and tell me', never 'follow for more'…"), plus a short list the UI can render: 5 phrases they really say, 3 real openers, how they end, address form, swearing, typical length, "never say" list, and a confidence badge ("built from 4 reels, add more").
- Build: ingest up to 30 reels (Apify `firsthand/instagram-reel-transcripts` ~$0.006/reel, or the TikTok transcript actor; `creator_media` transcripts first since they're free and already spoken); lightly clean transcripts (punctuation restore, drop ASR fragments) before use; Python stats (median/p90 sentence words, contraction rate, wpm, address counts, opener/closer n-grams, swearing, typical length); one Sonnet call writes the paragraph and picks 5–8 exemplars (talking-head, solo, spoken). ~$0.10 + transcription. Refresh stats on each new reel.
- **Correction capture** (the richest voice signal we own): a "sounds like me / doesn't" thumb on every script (`/v1/feed/feedback` gains `voice_verdict`), and the teleprompter/reader edit diff (`versionHistory` already exists in `AppStore.swift:884`) is sent as `edit_diff` and summarized into `user_corrections` ("she changed 'here's why' to 'and like'"). Corrections are injected verbatim; they are the highest-leverage field.
- TICL negatives: deferred to a later phase, and when added: style-only explanations that must cite a phrase from the real transcript, cap 5, de-duped, shown to the creator with a delete toggle.
- Cold start (no handle or < 5 reels): paragraph derived from the niche's typical rhythm + the three sliders, `data_confidence: low`; most traffic hits this branch today (`_creator_posts` returns `[]` for demo ids and anyone whose onboarding never scraped), so the cold-start path gets its own eval persona set.

**C. Niche angles and openers** — extend what exists rather than new infrastructure in v1.

- `_refresh_niche_trends` (`main.py:13355`) already clusters `reels_cache` by format on a 24 h TTL. Extend it to emit, per cluster: a one-line **promise** ("what the viewer is told they'll get"), `velocity` vs the previous refresh, and up to 3 transcribed openers (filter `transcribed == True`; today that is ~5–12 reels per warmed niche, zero for cold niches; < 3 → omit the block). Persist alongside `reels_cache` (same JSONB pattern) so `/v1/trends`, the feed trend card and the planner read the same durable object; `AppStore.generateFromTrend` gets wired in the app phase.
- Corpus deepening (phase 3): weekly transcript pass on the top 30 TikToks per niche (~$0.75/niche/week) once the niche has active users; mechanism labels and views/follower via Haiku. YouTube Data API and Creative Center are **out of v1**.
- Scraped text is data: wrapped in tags, instruction-like lines stripped, never verbatim in a script (lint overlap guard).

**D. Hook Lab v2** — 5 hooks, each a different opener frame, each with a label, a one-line why, and either "modeled on your reel from <date>" or "modeled on a niche opener". Three plain checks (specific to this video? promises the payoff? in your voice?) instead of a number. Built with the creator's real openers in front of the model (today it gets none).

**E. Metering and caps** — both wrappers (`main.anthropic()` at 1324 and `palo_llm.anthropic_cached_json`) return/record real `usage` (a contextvar side-channel so call sites don't change); the seven raw `client.post(ANTHROPIC_URL…)` sites (`_score_edl_vision`, `_matte_qc`, `analyze_media` ×2, two more in main, `dossier.py:295`) are either routed through a wrapper or record usage inline. `ai_usage` rows carry `route`, `model`, `creator_id`, `doctrine_version`, cached/uncached/output/thinking tokens. Per-creator soft daily budget (`SCRIPTS_DAILY_BUDGET_USD`, default $0.50): past it, only the Haiku first paint runs. `scripts/cost_report.py` prints $/page and $/creator/day.

**F. Learning** — `hook_mechanism` → call it `opener_frame`; adding it as a bandit dimension touches five places (`arm_stats.arm_key` is free text so the table is fine; `post_registry` needs a column, `_POST_COLS` in `supabase_persistence.py:28`, the register whitelist at `main.py:11518-11539`, the `DIMENSIONS` loops at 11668 and 14889). Also a legacy map `opener_frame → SIGNAL_LIST` so `hookSignal` keeps feeding the existing arms until the new dimension lands. Thumbs (`voice_verdict`) and likes per `doctrine_version` are the first-week success metric. First-party OAuth metrics remain out of scope.

### What gets retired

`scripts_prompt`, `hooks_prompt` + mandated hooks, `script_judge_prompt`/`script_revise_prompt`, the fast path's 1-3-1 rule, `_ensure_speakable`/`_ensure_honest`/`scrub_em_dashes` for v2 output, `LENGTH_BUDGET`, the mock script templates (rewritten per route with the new shape, deterministic, keyless). `NICHE_PRIORS` stays as the cold-start fallback. The Palo write agent stays and gets the v2 voice context and lint.

### App contract

v1 is backend-only: `ScriptDTO` keys unchanged, `cta` never empty, `hookSignal` mapped to the legacy enum (unknown values already coerce safely, `BackendClient.swift:142-145`). Additive fields: `opener_frame`, `particular_sources`, `voice_confidence`, `doctrine_version`. The app phase adds: "How you talk" (paragraph + list + toggles + "that's not how I talk" box + with/without preview), voice thumb on scripts, edit-diff upload, Hook Lab labels/checks, trend card date + "make this mine", "not this topic" on the feed, seconds-to-read on the reader.

## 6. Cost model (recomputed)

Prices: Sonnet 5.5 $2 in / $10 out, cache read $0.20, cache write $2.50 per MTok; Haiku 5.5 $0.10 / $0.50. Thinking tokens bill as output (assume 200–400 per Sonnet write at `low`).

| Page type | Steps | Cost |
|---|---|---|
| First paint (Haiku 5.5) | plan + material + 3 writes + lint | **≈ $0.003** |
| Background upgrade (Sonnet 5.5, 1 write/slot) | 3 writes (first sequenced to warm the cache, two read it) ≈ $0.013 + $0.007 + $0.007; thinking +$0.01; 3 Haiku judges $0.001; 30% rewrite $0.004 | **≈ $0.045** |
| Full page if best-of-2 is ever enabled | 6 writes, mostly cache writes under `gather` | ≈ $0.09 (A/B only) |
| Hook Lab (5 hooks, Haiku) | 1 call | ≈ $0.001 |

Eval gate: ≤ $0.06 per upgraded page, ≤ $0.005 per first paint. Today: ≈ $0.25–0.40 per full page, and the first paint on Haiku 4.5 costs ~10× its 5.5 equivalent. Per creator per month: light ≈ $0.30, regular (1 page/day) ≈ $1.50, heavy (3 pages/day) ≈ $4.50 before the daily cap. Shared: niche openers from what we already scrape ($0 extra in v1); phase-3 transcript pass ≈ $0.75/niche/week; voice build ≈ $0.30 one-time per creator ($0.18 transcription for 30 reels + $0.10 build).

## 7. Build order and rollout

Flag `SCRIPTS_V2=off|allowlist|on` as a module constant read at import (copy `studio_engine.py:31-32`), creator allowlist, default off; every phase a branch off `main`, tests green, "push it now" per deploy.

| Phase | Scope | Size |
|---|---|---|
| **0. Meter, strip temperature, flip Haiku** | Usage side-channel in both wrappers + the seven raw posts; `cost_report.py`. Remove every explicit `temperature=` (main.py 2373, 2878, 6615, 6972, 10879, 10912, 12739; ideas.py:340; sketch_ideas.py:562; channel_identity.py:554) or have the wrappers drop it for 5.5 models. Flip only `HAIKU` to `claude-haiku-5-5` (10× cheaper first paint; writers already go through `palo_llm`). Add `thinking` config to the wrappers. Gate: full backend suite + `run_eval` + realism eval before/after. | 1–2 days |
| **1. The two-day slice (no flag, no tables, owner feels it)** | (a) `_voice_exemplars`: prefer `transcript` over `caption`, send the first two full sentences not 120 chars; (b) `_plan_slots`/`_page_formats`: distinct temperature + opener frame per slot, named in the slot prompt, plus a `payoff` field; (c) replace the 1-3-1 fast-path suffix with a length target from the creator's own transcript lengths; (d) opener dedup from `reels_cache` (transcribed only) + the creator's last 10; (e) the `lint.py` tells check wired as a rewrite trigger on the full path. Measured with `script_realism_eval.py` + `cross_page_dupes` + tells/script. | 2 days |
| **2. Writer v2 behind the flag** | `scripts_v2/` llm, doctrine, material, planner, writer, lint, judge, pipeline, mocks; wired into feed first paint (Haiku), background upgrade + `/v1/scripts` (Sonnet), `_chain_scripts`, `/v1/steer`. Owner allowlisted. Eval harness v2. Prefix byte-stability test. | 3–4 days |
| **3. Voice** | 30-reel ingest (Apify + `creator_media`), cleaning, stats, paragraph + exemplar pick, `GET /v1/voice`, `voice_verdict` + `edit_diff` on feedback, corrections injection, cold-start persona evals. | 2–3 days |
| **4. Angles + Hook Lab + mimic** | `_refresh_niche_trends` emits promise/velocity/openers, persisted; planner reads it; Hook Lab v2; `/v1/mimic` beat-skeleton mode; `opener_frame` logging + legacy signal map. | 2–3 days |
| **5. App build** | "How you talk" editor + with/without preview at onboarding, voice thumb, edit-diff upload, Hook Lab labels/checks, trend card → generate, "not this topic", seconds-to-read, new enum values. TestFlight. | app sprint |
| **6. Deepen + retire** | Weekly niche transcript pass, `opener_frame` bandit dimension (five edits listed in F), optional TICL negatives, Sonnet-first-paint and best-of-2 A/Bs, `SCRIPTS_V2=on`, delete v1 prompt blocks and repair passes, update CLAUDE.md. | 2–3 days |

Kill switch: `SCRIPTS_V2=off` + redeploy. Phases 0–1 have no flag and are reverted by commit.

Scheduling note: the in-process sweep runs only when `PALO_PORT` is set and restarts lose progress; the phase-4 refresh rides the existing TTL-on-request path (no sweep needed). Any future cron waits on the Render Blueprint auto-sync question (it would create the $180/mo Studio worker on the next sync).

## 8. Verification

- **CI (keyless):** lint fixtures (each tell has a failing and a passing example); planner invariants (distinct temperature/format/opener frame per page; length spread; signature respected); `ScriptDTO` contract (hook/body/cta non-empty strings, exact keys); doctrine budget + headroom; prefix byte-stability across two requests; untagged-particular rejection; exemplar overlap guard; every v2 branch returns deterministic mock output with no keys; `temperature` never sent to a 5.5 model (wrapper test).
- **Eval harness v2** (`eval/scripts_v2_eval.py`, extends `script_realism_eval.py`; 8 personas incl. 3 cold-start × 3 pages, old vs new): tells/script < 0.3 (today ≈ 2–3); cross-page opener-frame duplicates 0; particulars per script ≥ 1, all tagged; length within target ± 20%; independent realism judge ≥ 7/10 on spoken/substance/specificity; voice pairwise (offline, for creators with ≥ 10 transcripts): judge picks the draft as "real" ≥ 40%; style-embedding margin reported, not gated; invented-claim rate 0; `ai_usage` ≤ $0.06 per upgraded page and ≤ $0.005 per first paint; first paint p50 < 8 s, `/v1/converse` unchanged ± 1 s.
- **Owner blind test** after phase 3: 10 unlabeled old/new pairs on the owner's account; target ≥ 8/10.
- **Live, one week on allowlist before `on`:** like rate and `voice_verdict` per `doctrine_version`; daily spend per creator; `quality_sentry` sample scored with the v2 lint.
- **Definition of done the owner can test by hand:**
  1. Read a page of three aloud: none makes you wince; no two share an opener frame.
  2. Each script has one particular that could not be swapped into another creator's script, and you can see where it came from.
  3. The last line is a payoff or a hard stop, never a summary or a moral.
  4. A friend who follows you can't pick the AI script from your real transcript better than 6 of 10.
  5. "How you talk" shows five phrases you actually say; delete one and the next page stops using it.
  6. Add five reels; the paragraph visibly changes and the preview shows before/after.
  7. The trend card names a live angle with a date; one tap gives a script on it in your voice.
  8. Two creators in the same niche get different openers on the same day.
  9. Edit a line in the teleprompter; the correction shows up in your profile.
  10. Cost report shows ≤ $0.06 per upgraded page and the first paint lands under 8 s.
- Standing rule: every bug found in rollout gets a failing-then-passing test.

## 9. Decisions needed from the owner

1. **Writer model:** Sonnet 5.5 at `low` for the background upgrade (cost pick); Opus 5.5 at `low` is ~2× per page. Proposed: ship Sonnet, A/B Opus on the owner's account in phase 2.
2. **First paint:** stays Haiku 5.5 (sub-8 s, ~$0.003/page) with the Sonnet pass in the background, as today's architecture. Sonnet-first-paint is an A/B.
3. **Transcription for back catalogs:** Apify reel-transcript actor (~$0.006/reel, IG) + TikTok transcript actor, vs. our own AssemblyAI. Proposed: Apify.
4. **Voice-profile storage:** JSONB on `creator_posts` now, own table later. OK?
5. **Blueprint auto-sync** must be turned off before any Render cron is added (would create the $180/mo worker).
6. **Phase 5 app build** is where users *see* the voice work (preview, editor, thumb). It can start in parallel once `GET /v1/voice` exists (end of phase 3).
