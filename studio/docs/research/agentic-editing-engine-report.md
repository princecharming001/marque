# Let Claude decide, let code measure

Yunicorn should rebuild its editing engine as a bounded Claude agent. The agent makes the editorial calls: which take survives, what gets cut, which pauses stay, which b-roll fits, and whether the result looks right. It makes them through the same typed edit operations the iOS timeline already uses. Deterministic code keeps everything that is really measurement: timestamps, word-snapping, caption timing, rendering and validation. The owner is right that today's engine "is static and won't get better with the Claude models". One temperature-0 Sonnet 4.6 call writes the edit list, about 20 hand-written passes then rewrite it, and a single self-review allows at most one fix. A smarter model's choices mostly get overwritten, and recent production bugs came from those passes fighting each other (Yunicorn codebase audit). The owner is also right that agentic output can stay editable: video-use itself emits a JSON edit list, and Descript and Adobe both put agent edits into ordinary, undoable projects. What the research does not support is handing the model everything. Benchmarks find models close to useless at pinning exact timestamps, harness design moves scores as much as the choice of model, and several of the newest open-source editors have moved detection back into code. The version that improves with each Claude release gives the model judgment. It feeds the model text-first perception, turns today's rewriting passes into checks the model must satisfy, and grades the output with code plus a separate evaluator. On Sonnet 5 that costs about **$0.10–0.27 in model tokens per 15–90 s video**. That is roughly what today's pipeline probably costs, though nobody measures it today. If the model's turns are kept short, it should take about as long as today's 2.5–3 minute wait. Opus 5.5 costs about twice as much and, at its default effort, adds more than a minute. The rebuild mostly rewires parts Yunicorn already has: the edit list, about 28 edit operations, the plan path, a dozen checkers, the test takes and a shadow switch. It rolls out in five phases, each with numeric gates.

## Claude should make the judgment calls; code should handle timestamps and validation

The owner's diagnosis is accurate, and the audit shows the mechanism. On the default path, one structured-output call to claude-sonnet-4-6 at temperature 0 writes the edit list once. About 20 deterministic passes then rewrite it: sentence-integrity passes A–E, retake dedupe, filler sweep, pacing, emphasis punch-ins, snapping, framing clamps and more. A single self-review scores up to 8 frames and permits at most one fix round. Silence is never measured on this path, and every model ID is pinned to an older generation (Yunicorn codebase audit). A stronger model can only improve the first draft, which the passes then override, so model upgrades barely reach the creator.

The passes also interfere with each other. A recent bug left a **1.37 s blank at a retake join** because a keep-veto and a restored cut fought (Yunicorn codebase audit). Earlier, a calibration against a professional editor's cut found the old trimming heuristic made **19 micro-splices on a take the pro cut with one** ([internal notes](/Users/home/.claude/projects/-Users-home-URAP---Lead---Levine/memory/marque_pro_cut_calibration.md)). Anthropic's harness research describes the pattern: "Every component in a harness encodes assumptions worth stress testing, as they go stale" ([Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps)).

The fix is not "let the model do everything", which is video-use's stated philosophy ("The LLM picks better than any heuristic you'll write") ([video-use SKILL.md](https://github.com/browser-use/video-use/blob/main/SKILL.md)). The research consistently finds that models reason well over transcripts and badly over time coordinates:

- **VEBench:** once a task requires temporal localization, "nearly all models fail completely, achieving tIoU scores close to zero" ([VEBench](https://arxiv.org/pdf/2605.03276)). tIoU measures how well the model's time range overlaps the correct one.
- **AgenticVBench:** agents rendered valid files but chose the wrong cut window, with a **median start error of 15 to 100+ seconds** ([AgenticVBench](https://arxiv.org/html/2605.27705)).
- **NumPro:** video models invent impossible frame ranges unless frame numbers are printed on the image ([NumPro](https://arxiv.org/html/2411.10332v1)).
- **Vidi2.5:** a specialized grounding model beat general frontier models at locating moments, **49.6% IoU against 37.6% for Gemini 3 Pro and 17.2% for GPT-5** ([Vidi2.5](https://arxiv.org/html/2511.19529)).

The same benchmark shows how much the surrounding software matters. One model's score on identical tasks fell from 0.38 to 0.18 depending only on the harness, and the authors conclude "What looks like a model gap on a given cell is often a harness gap." The best model-and-harness pair scored **31%** against human experts at 0.81–0.95 ([AgenticVBench](https://arxiv.org/html/2605.27705)). That is a caution against assuming agentic editing is solved. Yunicorn's task, trimming one 15–90 s talking-head take, is much narrower than those long-form benchmarks, though.

Descript is the closest precedent, and its record cuts both ways. Anthropic's case study says Underlord moved from rigid, predetermined workflows to an open-ended agent built on Claude ([Claude customer story](https://claude.com/customers/descript)). Model upgrades then turned into measured gains: switching to Opus 4.6 improved filler removal by **43%** and b-roll placement accuracy from **60% to 92%** ([Descript changelog](https://descript.canny.io/changelog/release-roundupfebruary-10th-2026)). New models go live in about half a day because the evals already exist ([Claude customer story](https://claude.com/customers/descript)). Yet Underlord still calls deterministic tools such as Remove Filler Words and Shorten Word Gaps ([Descript Help](https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript)). Descript ranks "Don't break things" above "Do what I asked" in its evals ([Claude customer story](https://claude.com/customers/descript)). Users still report mid-word cuts, misread instructions and, under one model, zoom cuts "every 20 seconds" ([Reddit](https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/)).

The open-source editors point the same way. Two of the newest Claude editing skills moved detection back into code and use the model to adjudicate. vincentventalon's script keeps the last take while Claude catches "a sentence abandoned and said again in other words" ([SKILL.md](https://github.com/vincentventalon/claude-code-video-editing-skill/blob/main/SKILL.md)). kamgasimo places "every cut … on the measured sound instead" because transcript word times drift ([cutting.md](https://github.com/kamgasimo/ai-video-editor/blob/main/reference/cutting.md)). video-use shipped an audible click at every segment boundary that "was caught by the user, not by the pipeline" ([issue #162](https://github.com/browser-use/video-use/issues/162)).

| Editing job | Owner | Why |
|---|---|---|
| Choosing among takes, catching rephrased restarts, keeping deliberate repetition | Claude | Rule-based retake matching deletes intentional emphasis ([Chase Jarvis](https://chasejarvis.com/blog/descript-underlord-ai/)); the model catches rephrasings that scripts miss ([vincentventalon](https://github.com/vincentventalon/claude-code-video-editing-skill/blob/main/SKILL.md)) |
| What to cut for length, hook and structure | Claude | This is where Descript's model-driven gains came from ([Claude customer story](https://claude.com/customers/descript)) |
| Which pauses are intentional | Claude decides, code measures | The pro kept 300–550 ms sentence pauses verbatim (Yunicorn codebase audit); pause lengths come from measured audio |
| Ambiguous fillers and stumbles | Claude, working from code-generated candidates | Filler removal improved 43% with a model swap ([Descript changelog](https://descript.canny.io/changelog/release-roundupfebruary-10th-2026)); an acoustic evidence table settles edge cases ([kamgasimo](https://github.com/kamgasimo/ai-video-editor/blob/main/reference/cutting.md)) |
| B-roll choice, punch-in emphasis, SFX | Claude, gated by evals | 60%→92% b-roll accuracy, but a model change also caused zoom overuse ([Descript changelog](https://descript.canny.io/changelog/release-roundupfebruary-10th-2026); [Reddit](https://www.reddit.com/r/Descript/comments/1q2euuz/my_experience_with_the_underlord_actual_prompts/)) |
| Looking at rendered overlays, framing and caption collisions | Claude vision, on specific frames only | "the transcript might seem fine, but the visuals might have been messed up" ([VideoDiff](https://arxiv.org/html/2502.10190)) |
| Converting decisions into frames, snapping to word edges and quiet points, padding, fades | Code | Timestamp failures in the four benchmarks above; commercial tools snap each cut to the waveform within ±10 ms ([TimeBolt](https://www.timebolt.io/remove-silence-from-video)) |
| Caption timing | Code, derived from the words | ASR word times drift 50–100 ms ([video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md)), and iOS matches captions to exact frames |
| Clicks, loudness, dead air in the render | Code | "You cannot listen" ([video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md)); the click bug above |
| Structural validity (bounds, overlaps, invariants) | Code | Agents hallucinate out-of-bounds indices and nonexistent functions ([EditDuet](https://arxiv.org/html/2509.10761v1)) |

The working rule is that Claude chooses and code locates and checks. On that basis the owner's goal is achievable, with one caveat. Several of Yunicorn's visible defects are engineering problems that no model upgrade will fix: mid-word cuts, dead air in the render, audio clicks and stalls from out-of-memory crashes.

## Keep the existing EDL, and let typed edit operations be the only way anyone changes it

The owner is right that an agent's output can stay editable. Every product in the research that keeps an editable timeline has its agent write into the same undoable document the human edits:

- **Descript:** cuts become restorable "ignored text" ([Descript Help](https://help.descript.com/descript-tour/sound-good-tools)).
- **Adobe Premiere:** its assistant produces "a fully editable Premiere sequence", and every action it takes "is recorded in Premiere's undo and history stacks" ([Adobe Community](https://community.adobe.com/announcements-727/meet-your-new-assistant-editor-ai-assistant-in-premiere-pro-is-now-in-public-beta-1629317)).
- **video-use:** its output is a source-referenced JSON edit decision list (EDL) with a stated reason for each kept range ([video-use SKILL.md](https://github.com/browser-use/video-use/blob/main/SKILL.md)).

Yunicorn's Pydantic EDL is already richer than video-use's. It stores segments, drops with reasons, captions, overlays, b-roll with inset rectangles, transitions, audio and an end card, all in 30 fps source-frame integers. video-use still has per-range speed, zoom and multi-track support sitting in open pull requests. The EDL should stay the canonical format. `build_render_plan` (schema v8) and the Remotion renderer should stay untouched, so the rebuild never touches rendering.

**OpenTimelineIO (OTIO)** is the wrong canonical format for this app. Its feature matrix marks audio and video effects as unsupported even in native OTIO, and spatial transforms exist only as a proposal ([OTIO feature matrix](https://github.com/AcademySoftwareFoundation/OpenTimelineIO/blob/main/docs/tutorials/feature-matrix.rst); [spatial coordinates proposal](https://github.com/AcademySoftwareFoundation/OpenTimelineIO/blob/main/docs/tutorials/spatial-coordinates.md)). Yunicorn's zooms, captions and b-roll insets would end up as opaque metadata. The Swift-to-AVFoundation bridge describes itself as "under heavy development" and has been tested mainly on macOS ([OTIO-AVFoundation](https://github.com/OpenTimelineIO/OpenTimelineIO-AVFoundation)).

**FCPXML** is the best hand-off format for a professional editor, since it has caption elements, keyframed `adjust-transform` and `adjust-volume` ([Apple FCPXML DTD](https://developer.apple.com/documentation/professional-video-applications/document-type-definition)). It is a format for editing apps to import, not something an agent should edit directly. Both formats belong later, as export by-products for creators who finish in Premiere, Resolve or Final Cut. kamgasimo's frame-quantized FCPXML of cuts shows the export is a small job ([fcpxml.mjs](https://github.com/kamgasimo/ai-video-editor/blob/main/scripts/fcpxml.mjs)). Migrating the canonical format would break the iOS contract and gain nothing the agent needs.

The existing operation vocabulary is the right interface for the agent. `apply_edl_ops` never raises and reports which ops were applied and which were skipped. That is the pattern production agent editors use: tldraw validates and sanitizes every typed agent action before applying it ([tldraw](https://tldraw.dev/starter-kits/agent)), and Twick tells models "Do not manually mutate raw JSON" ([Twick](https://github.com/ncounterspecialist/twick/blob/main/AI_Builder.md)). The evidence against positional addressing is strong: "GPT is terrible at working with source code line numbers" ([Aider](https://aider.chat/docs/unified-diffs.html)), and array indices have the same weakness. Four changes are needed:

1. **Address ops by word ID and segment ID, not frames or indices.** Examples: `cut_words(w041, w047)`, `restore_words`, `add_broll(over w088–w095, candidate)`, `add_punch_in(at w112)`. The compiler converts these to frames.
2. **Add a whole-cut op, `set_cut`, for the first pass.** It sets the full keep-list as word-ID ranges with a reason for each. Large structural changes are most reliable as whole rewrites and small changes as targeted edits. Remotion's AI template lets the model choose between the two for the same reason ([Remotion](https://www.remotion.dev/docs/ai/ai-saas-template); [Diff-XYZ](https://arxiv.org/html/2510.12487v2)).
3. **Generate everything from one op registry.** The registry should produce the Pydantic union, the chat JSON schema, the agent's tool schema and the iOS op list. That closes today's gap where the chat schema cannot express several ops. Add the missing ops for end card, SFX and room tone.
4. **Keep the tool schema a discriminated union with required fields.** Strict structured outputs reject numeric constraints and cap optional parameters per tool ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)), so bounds checks happen in Pydantic and come back to the model as readable errors.

The iOS contract can be kept item by item:

- **Segments addressed by array index.** Add a stable `id` as a new field and let the server translate IDs to indices. iOS keeps sending index-based op logs against a known EDL hash until a later app release adopts IDs.
- **Captions matched to words by exact start frame.** Captions are always regenerated in code from the kept words, using the same rounding the iOS engine uses. The agent may change caption text, style or visibility, never caption frames. A contract test asserts that every caption start frame equals some word's start frame.
- **The b-roll lane.** `add_broll` emits exactly today's window shape (window, query, source, resolved_url, mode, inset_rect).
- **The draft hash.** Either exclude the new `id` and `reason` fields from the hash, or add them only to new EDLs, so existing drafts don't appear modified.
- **The 25-entry server-side `edl_history`.** Each agent run and each chat turn should be one entry, meaning one undo, which mirrors Descript's per-response Revert button ([Descript changelog](https://descript.canny.io/changelog/release-roundupfebruary-10th-2026)). Store the op list and per-op reasons alongside each entry so the app can show "what changed and why".
- **The local iOS engine.** The first cut arrives as a full EDL and chat results arrive as an EDL plus an op summary, so the app never has to replay agent ops it doesn't know. Ops the app lacks stay server-only until an app update adds them.

## The engine: Claude decides, a compiler measures, a separate grader checks

The recommended flow reuses most of today's parts. Only the middle changes.

```
UPLOAD
  └─► PRECOMPUTE (code, parallel, cached by content hash)
        AssemblyAI words + disfluency tags · 360p proxy · silencedetect (always)
        candidates: fillers, silences, retake clusters, false starts, sign-off/CTA
        one overview contact sheet
  └─► EDITING AGENT (Claude; 4–10 turns scaled to clip length)
        reads word-ID transcript + candidates → calls tools → emits typed ops
        ▲ applied/skipped + tier-1 check failures, returned on every call
  └─► COMPILER (code): word IDs → 30 fps frames, snap to quiet point, padding,
        30 ms fades, clamps, captions derived from kept words, invariants
  └─► VERIFY  tier 2: seam preview, ASR round-trip diff, clicks, silences, LUFS
              tier 3: separate evaluator, binary rubric, ≤2 fix rounds
  └─► EDL (same schema) ─► iOS timeline + chat editor (same ops) ─► Remotion final render
  ✕ on budget cap or repeated failure ─► today's pipeline ─► captions-only original
```

**What the agent sees.** The Claude API accepts text, images and PDFs, but not video or audio ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)), so everything the agent perceives is built server-side. Up front it gets about 10K tokens:

- A cached editing doctrine and the tool schemas.
- The creator's brief, which replaces today's separate brief call. The agent's first turn writes a short plan that doubles as progress text in the app.
- The full word-level transcript with IDs, pause lengths and AssemblyAI disfluency tags.
- The candidate list, each item with evidence and a confidence score.
- One 12-frame overview contact sheet from the proxy, about 900 tokens.

A 90 s clip is only about 225 words, so the whole word-level transcript fits in a few thousand tokens. video-use's phrase-only packing exists for hour-long rushes, and it hides the word boundaries a mid-phrase cut needs ([pack_transcripts.py](https://github.com/browser-use/video-use/blob/main/helpers/pack_transcripts.py)). The view looks like this:

```
P04 [08.12–11.40] w041 So w042 the w043 {um} w044 thing w045 nobody w046 tells w047 you
    gap 0.62 s (s07)
P05 [12.02–14.88] w052 So w053 the w054 thing w055 nobody w056 tells w057 you   ← retake cluster r02 (restarts P04)
```

On demand, the agent can pull more:

- **Seam filmstrips.** Frames plus a waveform, word labels and shaded silences, the pattern video-use's `timeline_view` uses "at decision points" ([timeline_view.py](https://github.com/browser-use/video-use/blob/main/helpers/timeline_view.py)).
- **Acoustic tables.** kamgasimo's numeric energy, voicing and pitch rows, for sounds the transcript can't classify ([cutting.md](https://github.com/kamgasimo/ai-video-editor/blob/main/reference/cutting.md)).
- **B-roll contact sheets.**

Images cost ⌈w/28⌉×⌈h/28⌉ tokens, so a 360×640 frame costs 299 tokens and a 12-thumbnail sheet about 900. Keep each request to 20 or fewer image blocks, or at most 2,000 px per side, to avoid the stricter image limits, and pass images by Files API ID ([Vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)). Anthropic's current advice for visual work is "tools, not more thinking": let the model crop, zoom and re-check instead of raising effort ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).

**The tool list.** Anthropic recommends "a few thoughtful tools" that return high-signal results ([Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)). Eight cover the job:

| Tool | What the agent gets back | Source / what it replaces |
|---|---|---|
| `get_transcript(from_word?, to_word?)` | Word lines with IDs, pauses and disfluency tags (the full clip by default) | video-use's packed transcript, with word IDs added |
| `get_candidates(kind?)` | Precomputed fillers, silences, retake clusters, false starts and sign-off/CTA, each with ID, evidence and confidence | Deterministic detectors from kamgasimo and vincentventalon; replaces the filler and retake passes |
| `view_timeline(from, to, on=source\|edit)` | One PNG: 8–12 proxy frames, waveform, word labels, shaded silences (about 900 tokens) | New server tool; none exists today |
| `get_acoustics(from, to)` | A 30 ms table of energy, voicing and pitch | kamgasimo's `acoustics.mjs` |
| `find_broll(query, over_words)` | A contact sheet of stock candidates with IDs and durations | Today's Pexels/GIPHY/KLIPY search; replaces the separate vision rerank |
| `apply_ops(ops[])` | Applied/skipped per op, tier-1 check failures, a compact summary of the new EDL | Existing `apply_edl_ops`, plus word-ID ops, `set_cut` and macros |
| `preview(scope=seams\|full)` | Tier-2 measurements plus seam and overlay sheets | Today's self-review render, split into a cheap path and a Remotion path |
| `finish(summary)` | Runs the separate evaluator and final gates; the only way to end | Replaces "one self-review, one fix" |

**What happens to today's 20 passes.** The core of the rebuild is reassigning each pass. Pure geometry becomes a compiler that always runs. Expensive, rare mistakes become hard floors the agent can see. Everything else becomes either a check the agent must resolve or a macro it may call.

| Today's pass | New role |
|---|---|
| Clamp to source, framing clamps, frame rounding, snapping cut ends, re-trimming restored silence | **Compiler.** Always on, idempotent, no judgment |
| Opening-overcut guard; ending/CTA guard | **Hard floor.** The agent sees a rejection with a reason. The plan-path model once labelled a closing 5.3 s CTA a "false_start" and cut it ([internal notes](/Users/home/.claude/projects/-Users-home-URAP---Lead---Levine/memory/marque_pro_cut_calibration.md)) |
| Sentence-integrity passes A–E | **Tier-1 check** the agent must resolve. No more silent rewrites, and a better model triggers fewer of them |
| Keep-last-take, retake dedupe, filler sweep | **Candidates plus macros** (`keep_take(cluster)`, `remove_fillers(min_confidence)`) plus `cut_qc` undercut checks. Deterministic by default; the model overrides for deliberate repetition |
| Pacing | **Pro-cut checks** (keep natural 300–550 ms pauses, at most 4 seams per 60 s) plus a `tighten_pauses(max_ms)` macro |
| Emphasis punch-ins, interrupt scheduling, SFX, end card/CTA, b-roll | **Agent decisions made through ops.** Today's rules become defaults offered as macros, not overrides |

This mirrors how Descript's agent calls its own deterministic cleanup tools ([Descript Help](https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript)). It also matches the "deterministic candidates, model adjudicates, deterministic verifier" split that recent open-source editors converged on ([kamgasimo plan-cuts](https://github.com/kamgasimo/ai-video-editor/blob/main/scripts/plan-cuts.mjs)).

The compiler also absorbs cut-point craft that models do badly and code does well:

- Snap each cut edge to the quietest point inside the gap between words. Berthouzoz et al. chose cut points where the speaker is "relatively quiet and still" ([Berthouzoz](https://www.floraine.org/research/video-transitions/)).
- Pad kept words by roughly 50 ms before and 80 ms after.
- Apply 30 ms audio fades at every seam ([video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md)).

**Key design decisions and the tradeoffs accepted.**

| Decision | Recommendation | Tradeoff accepted |
|---|---|---|
| One call vs a loop | A bounded loop, with turns scaled to clip length and early exit when checks pass | Token cost and latency vary per video, where one call was predictable |
| Starting point | The agent writes the cut from evidence (transcript plus candidates) and does not revise a pre-applied heuristic draft | A turn or two more than draft-then-revise, but no anchoring to old rules; the heuristic draft stays as fallback and shadow baseline |
| Addressing | Word and segment IDs; code converts them to frames | ID plumbing through EDL, ops and chat, in exchange for removing the most-reported failure class |
| Harness | A Messages API tool loop inside the existing Python backend, with strict tools | Yunicorn owns the loop code. It avoids the Agent SDK's per-job CLI subprocess (about 1 GiB RAM each) ([Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)) and Anthropic's sandbox, which has no ffmpeg and no internet ([code execution](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)) |
| Model | Sonnet 5 as default, Opus 5.5 at low effort as challenger, Haiku 4.5 not a core dependency | Some quality possibly left on the table until an A/B proves Opus pays for itself; avoids Haiku 4.5's retirement date, which falls "not sooner than Oct 15, 2026" ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)) |
| Grading | Deterministic checks first, then a separate evaluator call | One extra call (about $0.03–0.05); self-review over-praises ([Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps)) |
| Vision | Only at seams, overlay keyframes and b-roll candidates | Blind between sampled frames, which the render measurements cover |
| Hard floors | Keep a few non-negotiable guards (CTA/ending, opening overcut, minimum segment, no mid-word cut), visible to the agent | Occasionally blocks a clever edit |

**Harness mechanics.** Use `strict: true` on every tool. Return all parallel tool results in a single message, because splitting them trains Claude to stop calling tools in parallel. Opus 5.5 returns an error on forced `tool_choice` and cannot turn thinking off, so use `auto` with strict schemas and treat effort as the only dial ([Opus 5.5 migration guide](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)).

Lay out the cache as tools, then system doctrine, then per-video context, then the growing history. The doctrine can use the 1-hour cache shared across jobs; the job itself needs only the 5-minute cache. Never change effort mid-run, because that invalidates the cache ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

The loop mostly waits on the API, so it can run on the orchestrator. Its conversation must be saved after every turn so an out-of-memory restart resumes from cache rather than starting over. All ffmpeg work (proxy, filmstrips, seam previews) should move to per-job workers with at least 2 GB. x264's default lookahead holds dozens of full frames in memory ([addpipe](https://blog.addpipe.com/reducing-ffmpeg-memory-usage/)), and today's 512 MiB / 0.5 CPU instance has been killed for running out of memory on clips over about a minute (Yunicorn codebase audit).

Transcript text is untrusted data. Spoken words can carry injected instructions ([Schneider](https://christian-schneider.net/blog/multimodal-prompt-injection/)). The agent therefore gets only inspect, edit and render tools, and the worst realistic outcome is a bad edit that the validator catches.

**Model routing.** Sonnet 5 ($2 / $10 per million input/output tokens, now permanent pricing) is the default because of its fast time-to-first-token. Opus 5.5 ($4 / $20) is what Anthropic says to "start with … for most workloads" ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)). Its vision is sharper, and at medium effort it "matches or exceeds" Opus 5 at high ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)). Run it at low effort as the A/B challenger. Anthropic notes that lowering effort cuts thinking, cost and latency "more reliably than prompt instructions do" ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)). Test the strongest model at low effort before building a cascade of models. Keep every model ID and effort level in configuration. Third parties say Sonnet 5.5 and Haiku 5.5 are coming, but Anthropic has not confirmed it ([Forkast](https://forkast.news/anthropics-claude-5-5-release-efficiency-gains-and-strategic-consolidation/)).

**The chat editor runs on the same agent,** with a smaller budget. It starts from the current EDL and the creator's message, and it has a one-turn fast path for single-op requests. Resolving phrases like "the part where I talk about pricing" is hard: ExpressEdit resolved such references with only 0.68 recall ([ExpressEdit](https://arxiv.org/pdf/2403.17693)). The agent should therefore state what it understood ("cut 0:23–0:31, where you mention pricing") and make the change undoable in one tap. Competitors set the bar: Captions applies "most changes … under 10 seconds" ([Captions](https://captions.ai/blog/editing-videos-is-now-as-easy-as-typing)).

## Three verification tiers, hard budgets and a fallback ladder

**Tier 1** runs on every `apply_ops` call. It takes milliseconds and costs nothing. It reruns the checks Yunicorn already has:

- `check_edl_invariants`
- `edit_lint` (static_window, captions_missing, same_framing_adjacent, anchor_drift)
- `cut_qc` (overcut, undercut, orphan fragments)
- `broll_timing` (mid-word cuts)
- The pro-cut reference rules

Failures return with IDs, and the agent fixes or justifies each one. The same inputs that today trigger silent rewrites become feedback. EditDuet's editor–critic pair, whose critic reads the timeline as text, failed outright on 8.2% of tasks, against 14.3–34.8% for the baselines ([EditDuet](https://arxiv.org/html/2509.10761v1)).

**Tier 2** runs once per candidate final. It takes seconds and costs fractions of a cent:

- Render a preview of the seams only.
- Re-transcribe it and diff the words against the expected kept words. This is kamgasimo's gate, and it deterministically catches clipped words and leftover fillers ([verify.mjs](https://github.com/kamgasimo/ai-video-editor/blob/main/scripts/verify.mjs)).
- Detect clicks at seams, as video-use's issue #162 proposes.
- Measure rendered silences of 2 s or more and loudness (LUFS), reusing the `long_video_prod` checks.
- Run AgenticVBench's "honesty check", which rebuilds the expected output from the EDL and compares it with the render ([AgenticVBench](https://arxiv.org/html/2605.27705)).

Cut and audio checks render on the proxy with ffmpeg. Overlay, caption and b-roll checks use the existing half-scale Remotion preview, and only when the edit has overlays. The agent then looks at seam and overlay sheets for problems only vision catches: overlay collisions, face framing inside punch-ins, b-roll fit.

**Tier 3** runs once. A separate evaluator with fresh context applies a binary pass/fail rubric, briefed, as video-use briefs its critic, "to roast, not to praise" ([video-use](https://github.com/browser-use/video-use/blob/main/SKILL.md)). Anthropic found agents "confidently praising work—even when obviously mediocre", and that a separate evaluator beats self-critique ([Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps)). Fix rounds are capped at two; video-use caps at three and kamgasimo at three to four. A run ends only through `finish` and the gates, following Anthropic's advice to "treat a text-only end of turn as a report rather than as proof the task is done" ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).

**Budgets are enforced in the harness and scale with word count.** Yunicorn has already learned that flat caps break. The legacy 8,000-token EDL cap truncated around 650 words because the schema echoes the caption array ([internal notes](/Users/home/.claude/projects/-Users-home-URAP---Lead---Levine/memory/marque_longfootage_liveness.md)). Reasonable starting caps:

| Budget | Starting cap |
|---|---|
| Agent turns | 4 for clips of 20 s or less, 8–10 for 90 s |
| Cumulative input tokens | 150K |
| Output tokens | 12K |
| Agent-phase wall clock | 120 s |
| Hard dollar stop | $0.50 per job |

The model also sees two soft signals. One is a task budget, a beta feature with a 20K-token minimum. The other is an elapsed-time signal, which Anthropic says helps Opus 5.5 "pace its work to finish inside the budget" ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)). SDK retries also count against the wall clock, because timeouts are retried too. Transcription and candidate detection stay outside the loop. In AgenticVBench, agents "burn the rollout budget on full-source whisper transcription or repeated frame thumbnailing and never reach the assembly step" ([AgenticVBench](https://arxiv.org/html/2605.27705)).

**Fallbacks rely on an "anytime" property.** Every accepted `apply_ops` leaves a valid EDL, so a timed-out run can ship its last state that passed tiers 1 and 2. Beyond that, the ladder is:

1. The agent's edit.
2. Today's pipeline. Use whichever path, plan or legacy, wins the Phase 0 baseline; the plan path at least measures silence.
3. The original clip with captions only.

Drop down the ladder when a budget cap is hit, when the validator rejects twice in a row, when the model refuses (`stop_reason: refusal`), during a storm of 429/5xx errors, or when the evaluator still fails after two rounds. Log the rung used for every job; the fallback rate is a top-line health metric. Key every tool output by job, turn and argument hash. That extends Yunicorn's existing self-healing registry, so a resume reuses frames and cache reads instead of paying for transcription or model calls again.

## Sonnet 5 runs the loop for about today's money; Opus 5.5 roughly doubles it

No vendor publishes per-video agent costs, so these figures are my calculation from list prices ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). The assumptions:

- Speech runs at about 2.5 words per second.
- The static doctrine and tool schemas are about 6K tokens; per-video context is about 4K.
- Each turn adds about 2.5K tokens: one filmstrip of about 900 tokens, tool results, and the previous turn.
- Each turn produces about 1,000 output tokens, including thinking.
- A 60 s clip takes 7 editor turns (4 for 15 s, 9 for 90 s).
- The evaluator runs 1–2 times, each with about 9K input tokens (eight 540×960 frames plus text) and 800 output tokens.
- A 5-minute cache covers the growing conversation.
- Transcription and rendering are excluded.

The "lean" case is 4 turns at 600 output tokens each. The "heavy" case is 10 turns at 2,000 each with two evaluator passes.

| Model | 15 s | 60 s typical | 90 s | 60 s lean → heavy | Per 100K 60 s videos |
|---|---|---|---|---|---|
| Haiku 4.5 | $0.05 | $0.10 | $0.14 | $0.05–0.20 | ≈ $10K |
| **Sonnet 5** | **$0.10** | **$0.19** | **$0.27** | $0.09–0.41 | **≈ $19K** |
| Opus 5.5 (Sonnet 5 as evaluator) | $0.17 | $0.32 | $0.45 | $0.16–0.72 | ≈ $32K |
| Today: Sonnet 4.6 brief + one-shot EDL + b-roll rerank + one self-review (estimated) | — | ≈ $0.15–0.30 | — | — | ≈ $15–30K |

The "today" row is an estimate, because the edit path has no token accounting (Yunicorn codebase audit). It prices the four known Sonnet 4.6 calls at $3 / $15 per million tokens, plus a fix round on some jobs.

On these assumptions, **a Sonnet 5 agent costs about what the current pipeline does**, because Sonnet 5 is a third cheaper per token than the Sonnet 4.6 it replaces. Output tokens make up roughly half the cost. After caching, the biggest cost levers are terse tool calls and a lower effort setting. Anthropic's own rule of thumb is that agents use about **4× the tokens of chat** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)), so the typical-to-heavy spread is real, and the hard caps exist for it. Opus 5.5 "tends to finish the same task with fewer tokens" ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)), which can narrow the gap. Only the Phase 1 eval will show by how much.

Model tokens will dominate the bill; compute won't. A final Remotion Lambda render of a one-minute video costs **$0.017–0.021** ([Remotion](https://www.remotion.dev/docs/lambda/cost-example)). A 20 s seam preview on a 2-core, 4 GiB worker costs about **$0.0007** at Modal's rates ([Modal](https://modal.com/pricing)). Moving previews off Remotion also frees its three-render concurrency cap for final renders. A chat edit on Sonnet 5 should cost about **1–6 cents**.

Latency needs more care. Per turn it is time-to-first-token plus output tokens divided by output speed. Artificial Analysis measures Sonnet 5 at about 1.2–1.7 s to first token and 57–63 tokens/s, Opus 5.5 at 5.5 s (low effort) or 13.4 s (medium) and about 80 tokens/s, and Haiku 4.5 without thinking at 0.65 s and 80 tokens/s ([Artificial Analysis](https://artificialanalysis.ai/providers/anthropic)). The estimates below assume a 1,200-token first turn (the cut plan) and 350-token later turns. For tools they add three 2 s filmstrip waits and a 20 s tier-2 preview.

| 60 s clip, 7 turns | Model time | + Evaluator | + Tools & preview | Agent phase |
|---|---|---|---|---|
| Haiku 4.5 (no thinking) | 46 s | 8 s | 26 s | ≈ 80 s |
| **Sonnet 5, low or medium** | 64–67 s | 11–12 s | 26 s | **≈ 100–105 s** |
| Opus 5.5, low | 81 s | 13 s | 26 s | ≈ 120 s |
| Opus 5.5, medium (its default) | 135 s | 21 s | 26 s | ≈ 180 s |

A 15 s clip on Sonnet 5 runs about 80 s and a 90 s clip about 115 s. Another Artificial Analysis page reports a slower first token for Sonnet 5 at medium effort (4.0 s against 1.65 s) ([Artificial Analysis](https://artificialanalysis.ai/models/releases/claude-sonnet-5)), which would push the 60 s case to about 120 s.

Today's end-to-end time is **155–165 s for a 40 s take and 165–186 s for a 50 s multitake** (Yunicorn codebase audit). The agent phase replaces today's brief, authoring, rerank and self-review stages, which are not timed separately. My best estimate is that a Sonnet 5 agent lands near today's totals and Opus 5.5 at medium adds about 80 s. Opus 5.5 fast mode, at $8 / $40 for up to 2.5× output speed, closes that gap at twice the price ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

Three levers matter more than the model choice:

1. **Output tokens.** At 60–80 tokens/s, each 1,000 tokens adds 12–17 s. Deriving captions in code removes the caption array the model writes today.
2. **Parallel tool calls** within a turn.
3. **Showing the edit before the MP4 exists.** If the local iOS engine can play a complete EDL (it already previews op edits), the app can show the cut as soon as the agent finishes and render the MP4 in the background. That takes the 15–19 s final render ([Remotion](https://www.remotion.dev/docs/lambda/cost-example)) out of the wait. Stream the agent's plan as progress text, as Descript's v2 does ([Descript](https://descript.canny.io/changelog/announcingunderlord-v2)).

Throughput is fine for now: Sonnet 5's entry-tier limit of 400K output tokens per minute allows about 55 edits a minute ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits)). Raise the tier before a launch spike.

## An eval suite makes each Claude release a measured upgrade within days

"Gets better with Claude" has to be measured, not assumed. Model changes move behavior in both directions: Descript's zoom overuse came with a model swap. Descript can adopt a new model in half a day only because its eval already existed ([Claude customer story](https://claude.com/customers/descript)). Anthropic's guidance is the same: teams with evals adopt new models "in days versus weeks", and 20–50 tasks drawn from real failures is enough to start ([Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). Build the eval before switching anything.

**The offline suite** starts with Yunicorn's existing assets:

- The test takes: 30 s, 40 s, 50 s multitake with a verbatim retake, 60 s, 85 s with silences, 180 s, 300 s and 600 s.
- The cut-loop raw takes and the pro editor's reference spans.
- Every past production failure, turned into a case: the 1.37 s blank at a retake join, and the CTA cut as a false start.
- 20–40 real uploads, with creators' consent, covering different styles.

The takes of 180 s and longer act as stress tests for budgets and memory.

Split the suite in two:

- **Regression set.** Must pass at about 100%: invariants, no mid-word cuts, no rendered silence of 2 s or more, CTA kept, captions drawn only from the transcript, loudness in range.
- **Capability set.** Hard judgment cases where today's models often fail: rephrased restarts, deliberate repetition for emphasis, intentional pauses, b-roll relevance on technical topics, and choosing between multitakes.

The capability score climbing with each model release is the direct measure of the owner's goal.

**Grading starts with code.**

- **Existing checks:** `check_edl_invariants`, `edit_lint`, `cut_qc` (including its run-to-run consistency check), `broll_timing`, the pro-cut rules and the `long_video_prod` checks.
- **New checks:** the round-trip transcript diff, seam click detection, the honesty check, and pacing statistics — seams per minute, and median kept pause against the pro's 300–550 ms.

Every grader must be shown to fail on a deliberately planted defect. Yunicorn's eval loops have gone "vacuously green" before, when graders received nothing and passed ([internal notes](/Users/home/.claude/projects/-Users-home-URAP---Lead---Levine/memory/marque_loop_env_trap.md)).

Model judges handle what code can't: pairwise comparisons of the agent against the current pipeline, and of a new model against the old one. Use binary verdicts, which track real quality better than 1–5 scales ([Hamel Husain](https://hamel.dev/blog/posts/llm-judge/)), and calibrate them against about 100 seams and 50 pairs labelled by the founder or a pro editor. The existing `cut_judge` can be recalibrated for this role. Published judges agree with humans **67–87%** of the time, about as often as humans agree with each other. EditDuet's judge scored 80.6% against 78.7% between humans ([EditDuet](https://arxiv.org/html/2509.10761v1); [Browser Use](https://browser-use.com/posts/ai-browser-agent-benchmark); [AVENUE](https://arxiv.org/pdf/2609.04253)). Aggregate verdicts across cases rather than trusting any single one.

**Run each case three times** and report:

- pass@1 and pass^3. pass^3 captures consistency: 75% per trial is only 42% pass^3.
- Cost per clip.
- p50 and p95 latency.
- Turns.
- The fallback rung used.

Offline runs can use the Batch API at 50% off.

**Online measurement comes next.** Shadow mode already exists but has only logged issue counts and never run in production (Yunicorn codebase audit). Extend it to log the full EDL, each check's result, tokens, cost, latency and fallback rung on 5–10% of uploads while the current pipeline keeps shipping.

The A/B test then measures:

- **Primary:** export or post rate.
- **Corrective edits,** read from the iOS op logs. A `restore_range` or undo shortly after the first cut signals overcutting; a manual `cut_range` signals undercutting.
- **Time to ready.**
- **Guardrails:** fallback rate, p95 latency and cost per video.

Read edits per export with care. Descript counted about 50% more edits per export as engagement, not failure ([Claude customer story](https://claude.com/customers/descript)). The op logs are also the data flywheel. Every creator correction becomes an eval case and, later, per-creator preference memory, much as Kapwing's agent "learns from your edits" ([Kapwing](https://www.kapwing.com/blog/giving-ai-a-canvas-to-paint-on/)).

**The upgrade protocol** is short. When a new model ships:

1. Run the suite at two or three effort levels, because effort names "don't correspond to the same amount of thinking across models" ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
2. Require zero regressions, a pairwise win rate at least as good as the current model, and a gain in capability or cost.
3. Flip the configuration and shadow for 48 hours.

Separately, and on a schedule, try deleting one scaffold (a pass, a guard, a doctrine paragraph) and keep the deletion if the eval holds. Anthropic recommends exactly this re-testing with each model ([Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).

## Five gated phases take the agent from offline to default

Every phase ships behind a flag as a third authoring mode alongside today's legacy and plan paths. The durations are rough estimates for a small team.

| Phase | What gets built | Exit criteria (all must hold) |
|---|---|---|
| **0. Instrument and de-risk** (~2 weeks) | Per-job token and cost accounting. ffmpeg moved to 2 GB+ workers. A 360p proxy made at upload. silencedetect on every path. Stable segment IDs added. One op registry generating the chat schema, with the missing ops added. Model IDs moved to config (test Sonnet 5 inside the current pipeline). A regression runner with k=3. Current pipeline baselined | Baseline numbers exist for defects, cost and latency. No out-of-memory kills on the 90 s and 180 s takes. Every op can be expressed in chat. Each grader shown to fire on a planted defect |
| **1. Offline agent** (~3–4 weeks) | Tools, compiler, checks returned as feedback, three verification tiers, budgets, fallback ladder. Suite run on Sonnet 5, Opus 5.5 at low effort, and Haiku 4.5 | 100% invariants. No regression against baseline on mid-word cuts, rendered silence, CTA retention or retake leakage. At least 60% blind pairwise preference over the current pipeline (founder plus calibrated judge). p95 model cost ≤ $0.40 and p95 agent phase ≤ 120 s for clips up to 90 s. Fallback rate below 5% |
| **2. Shadow** (~2–3 weeks) | Agent runs on 5–10% of real uploads while the current output ships; Batch API replays of older uploads | At least 500 real clips. Defect rates at or below the current pipeline. Judge win rate within 5 points of offline. Fallback below 3%. No stuck jobs. 50 randomly chosen pairs reviewed by a human |
| **3. A/B test and chat** (~3–4 weeks) | Agent output shipped to 10% of uploads, then 50%. Chat editor moved onto the same loop. iOS contract unchanged | Export/post rate no worse than control. Corrective-edit rate lower. p95 time-to-ready no more than 20% above control. Cost within budget. Then 100% |
| **4. Prune and compound** (ongoing) | Judgment passes retired one at a time. Model-upgrade gate automated. ID-addressed ops in the next iOS release. OTIO and FCPXML export. Per-creator memory built from op logs | Each retired pass shows no regression. A new Claude model adopted through the gate within about 2 days |

Two contingencies belong in the plan:

- **If Opus 5.5 at low effort clearly wins the capability set in Phase 1,** route multitakes and clips of 60 s or longer to it, and offer it as a paid tier. Consider fast mode if its latency stings.
- **If no model beats the baseline offline,** fall back to a middle path: the plan path on an upgraded model, with checks returned as feedback for one repair round. This is cheaper and keeps most of the plumbing.

Either way, retire the Haiku 4.5 dependency before its retirement window opens. It is used somewhere in today's pipeline.

## Conclusion

Whether an editor "gets better with Claude" depends on the harness and the eval, not on how many times Claude gets called. The largest single gain available is to stop overwriting the model's judgment with rules written for older models. Turning those rules into checks, candidates and macros lets each model release show up directly in the cut. It also makes over-engineered rules visible, because a pass that a new model never triggers can simply be deleted.

Yunicorn is closer to this than it looks. The EDL, the op vocabulary, the plan path, the checkers, the test takes and even the shadow switch already exist. What is missing is perception tooling (proxy, filmstrips, word-ID views), a bounded loop, and measurement. The durable advantage is the correction data. Every restore and undo that creators make in the iOS editor records where the agent's judgment differed from theirs. Over time that becomes both the eval and a per-creator memory that no model upgrade gives a competitor for free.
