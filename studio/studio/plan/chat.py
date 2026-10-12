"""The standard tier's chat edit (``docs/standard-tier-interfaces.md`` "Standard chat"; plan §3 step 8).

A creator's instruction on a delivered standard-tier cut costs one planner-model call, not a Director session:

* :func:`edit_document` asks the job's planner model (role ``planner``, stage ``chat``; the 5m cache TTL and effort
  ``low`` come with the role) for a :class:`ChatOps` answer, ``{ops, summary}``, over a context code builds: the
  instruction, :func:`studio.agent.tools.summarize_doc` and the transcript's cut view (:meth:`EditSession.q_transcript`
  ``"cut"``), the words that are out of the cut (so ``restore_words`` has IDs), the pauses in the cut, and the caption
  geometry (the page list and the Director's own layout lines from the compiled timeline). The cached prefix
  (:func:`build_prefix`) is the chat rules plus the compact JSON schema of every op in :data:`CHAT_OPS`: cuts and
  restores, pauses, caption style and page edits, on-screen text, framing, voice chain and loudness. Ops are IDs only,
  never times; they are applied with :meth:`EditSession.apply_ops` as one document version (oplog author
  :data:`CHAT_BY`).
* **One retry.** Every op is screened (outside :data:`CHAT_OPS`, or paid voice isolation) and dry-run with
  :func:`studio.doc.ops.apply_ops` (``persist=False``); when any is rejected, the rejection lines go back to the model
  once, on top of its own answer, and it returns the corrected complete list. What the last answer gets accepted is
  applied; what is still rejected is named in the summary. The retry is skipped when the first answer stopped at
  ``max_tokens`` or when its worst case would pass the chat's spend cap (:func:`studio.plan.planner.spend_cap`:
  ``--max-usd``, default $0.15).
* **Priced refusals without a call.** Music, sound effects, b-roll and paid voice isolation are not on the standard
  tier: a request that asks only to ADD those (:func:`refused_topics`, :func:`has_other_request`: an additive verb
  names the topic, and no removal verb is in the clause) gets a summary with the price of the way to get them (the
  capped Director-tier command, which resumes from this cut; isolation's per-minute price) and no model call; a
  request that mixes them with something the tier can do is planned with that part flagged as unavailable. A topic
  named as content ("cut the sentence about my song") is an ordinary edit and goes to the planner.
* :func:`render_change` re-renders the cheapest way that carries the change: when the cut is unchanged (segments,
  pauses, speed, seams, framing, inserts, audio and colour equal; only captions or text changed) the overlay and the
  master are rebuilt on the champion render's own ``aroll.mov`` and mixes (the repair module's overlay pass), else a
  full ``standard`` render; then the ten invariants and, on a failure, one code-only :func:`studio.plan.repair.repair`.

Why the answer is prompted JSON (``PromptedOutput``) and not a native structured output: ``ops`` are free-form op
objects (their real schemas are in the prefix and :func:`~studio.doc.ops.apply_ops` validates each one), and a
``dict`` field under Anthropic's strict structured output is forced to ``{}`` (pydantic-ai warns about exactly
this); a union of the 14 op models as a native schema would be ~13k characters of grammar, far over what the planner's
2.8k-character schema was sized for. The answer is still parsed and validated by pydantic (:class:`ChatOps`); an
unparseable answer spends the one retry like a rejected op.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from studio.plan.decision import _compact

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai.messages import ModelMessage

    from studio.agent.providers import ModelSpec
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex
    from studio.plan.repair import RepairResult

__all__ = [
    "CHAT_BY", "CHAT_LOG", "CHAT_OPS", "ISOLATION_USD_PER_MIN", "MAX_ATTEMPTS", "MAX_TOKENS", "REVIEW_ROUND_USD",
    "UPGRADE_MAX_USD",
    "ChatEdit", "ChatOps", "ChatRender", "build_prefix", "chat_context", "cut_unchanged", "edit_document",
    "has_other_request", "refusal_line", "refused_topics", "render_change",
]

#: The op subset a standard-tier chat may use: the cut (words, pauses), captions and on-screen text, framing and the
#: audio levels; ``note`` records why. Never music, SFX, inserts (b-roll), colour or the story's take choices.
CHAT_OPS: tuple[str, ...] = (
    "cut_words", "restore_words", "set_gap", "set_caption_style", "edit_caption_page", "set_captions", "add_text",
    "update_text", "remove_text", "set_framing", "clear_framing", "set_voice_chain", "set_loudness", "note",
)
#: Output budget per call (thinking plus a short op list), as for the planner (8k: one Sonnet 5.5 call stays under
#: $0.09, and an answer cut off there is not retried).
MAX_TOKENS = 8_000
#: One call and one retry carrying the rejected ops.
MAX_ATTEMPTS = 2
#: Oplog author of a chat's ops.
CHAT_BY = "chat:planner"
#: One record per chat edit (instruction, ops, rejections, usage, version).
CHAT_LOG = "logs/chat.jsonl"
#: Paid voice isolation's price (standard-tier plan §2: $0.12 per source minute).
ISOLATION_USD_PER_MIN = 0.12
#: Model spend of one Director review round at list prices (standard-tier plan §5: ~$1.50-2.50 on Opus 5.5).
REVIEW_ROUND_USD = (1.50, 2.50)
#: The spend cap the refusal's suggested upgrade command carries: one review round at its top price, rounded up. The
#: Director's loop has no cap of its own unless ``STUDIO_MAX_MODEL_USD`` is set (full loops measured $5.77-36.20), so
#: advice that quotes a round's price must not hand the creator an uncapped command.
UPGRADE_MAX_USD = 3

#: Requests the standard tier refuses with a price instead of a model call: (topic, pattern over the lower-cased
#: instruction naming the thing). Background-noise removal is not here: the local light denoise is a voice-chain
#: setting. A topic word alone is not a request: creators point at content with the same words ("cut the sentence
#: about my song", "remove the footage of me coughing"), so a topic counts only when an additive verb asks for it
#: (:data:`_ADD_VERB`), and never in a clause with a removal verb (:data:`_REMOVE_VERB`). When unsure, the planner is
#: called: one cheap call that returns no ops for music, SFX or b-roll beats refusing a legitimate edit.
_TOPICS: tuple[tuple[str, str], ...] = (
    ("music", r"music|songs?|soundtrack|background track|instrumental|lo-?fi(?: beat)?|beat drop|beats?"),
    ("sound effects", r"sound effects?|sfx|whoosh(?:es)?|swoosh(?:es)?|ding(?: sound)?|pop sound|transition sounds?"),
    ("b-roll", r"b-?roll|stock (?:footage|video|clip|image|photo)s?|cutaways?|footage of|(?:video )?clips?|videos?"
               r"|images?|photos?|pictures?|screenshots?|gifs?|memes?|overlays? of"),
    ("paid voice isolation", r"voice isolation|isolation|studio sound|studio[- ]quality (?:voice|audio|sound)"),
)
#: Verbs that ask to add something; at most a few words (no removal verb, no "about"/"saying") may sit between the
#: verb and the topic ("add some upbeat background music", "put b-roll of a laptop", "play a song under it").
_ADD_VERB = (r"add|adding|put|insert|include|play|lay|layer|overlay|throw in|bring in|mix in|apply|enable|turn on"
             r"|give me|give it|show")
#: (A determiner before "cut" makes it a noun: "a whoosh on every cut" asks to add.)
_REMOVE_VERB = re.compile(r"(?<!every )(?<!each )(?<!the )(?<!a )(?<!on )(?<!per )\b(cut|cuts|cutting|remove|removing"
                          r"|trim|delete|drop|shorten|lose|get rid of|take out|takes out|mute|lower|quieter|reduce)\b")
_GAP_WORD = r"(?:\s+(?!(?:cut|remove|trim|delete|drop|about|saying|say|says|said|where|when|that|which)\b)[\w'-]+)"
#: Requests that name the verb themselves ("isolate my voice"); "isolate the word budget" is a caption request.
_INHERENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("paid voice isolation", re.compile(r"\bisolat\w* (?:my |the |our |his |her )?(?:voice|audio|vocals?|dialogue"
                                        r"|speech|sound)\b")),
    ("paid voice isolation", re.compile(r"\buse (?:the |paid )*(?:voice isolation|isolation|studio sound)\b")),
    ("music", re.compile(r"\buse (?:(?:some|a|an|more|background|royalty[- ]free|lo-?fi|upbeat|chill|trending)\s+)"
                         r"{0,2}(?:music|song|soundtrack|track)\b")),
)
_REFUSED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (topic, re.compile(rf"\b(?:{_ADD_VERB})\b{_GAP_WORD}{{0,4}}?\s+(?:{words})\b")) for topic, words in _TOPICS)
_BARE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (topic, re.compile(rf"\b(?:{words})\b")) for topic, words in _TOPICS)
_SENTENCE_SPLIT = re.compile(r"[.;!?\n]+")
_CLAUSE_SPLIT = re.compile(r"[.;!?\n,]+|\b(?:and|also|then|plus)\b")
_ANY_VERB = re.compile(rf"\b(?:{_ADD_VERB}|make|change|move|fix|keep|set|tighten|zoom|turn|use)\b")


def _clause_topics(clause: str) -> list[str]:
    """The refused topics one clause asks to add (never in a clause with a removal verb)."""
    if _REMOVE_VERB.search(clause):
        return []
    found = {topic for topic, rx in _REFUSED if rx.search(" " + clause)}
    found |= {topic for topic, rx in _INHERENT if rx.search(clause)}
    return [topic for topic, _w in _TOPICS if topic in found]


def _refused_clauses(instruction: str) -> list[tuple[str, list[str]]]:
    """``(clause, topics)`` for every clause. A clause with no verb of its own after a refused one inherits the
    request ("add music and sound effects", "put b-roll over the middle, and a whoosh")."""
    out: list[tuple[str, list[str]]] = []
    for sentence in _SENTENCE_SPLIT.split(instruction.lower()):
        carry = False
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause or ""
            topics = _clause_topics(clause)
            if not topics and carry and not _REMOVE_VERB.search(clause) and not _ANY_VERB.search(clause):
                topics = [topic for topic, rx in _BARE if rx.search(clause)]
            if topics:
                carry = True
            elif _REMOVE_VERB.search(clause) or _ANY_VERB.search(clause):
                carry = False
            out.append((clause, topics))
    return out


class ChatOps(BaseModel):
    """The chat model's answer: the ops to apply (IDs only) and one or two sentences for the creator."""

    model_config = ConfigDict(extra="forbid")

    ops: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


# ============================================================================================ prompt
_RULES = """\
You make one change to a finished edit of a creator's talking-head video, on their instruction. Answer once with \
a JSON object {"ops": [...], "summary": "..."}: ops are applied by code to the document shown below, summary is one \
or two plain sentences for the creator saying what changed (or why nothing did).

Rules:
- Do exactly what was asked and nothing else. Restraint: if the edit already does it, return no ops and say so.
- Address everything by the IDs shown: words wNNNN, pauses gNNNN, segments segNNN, caption pages pNNN, texts tNNN. \
Never write times or invent IDs.
- Removing words: cut_words over a kept range. Bringing words back: restore_words over a range from the list of \
words not in the cut. Never remove the creator's spoken call to action unless they ask; never invent words they did \
not say (a caption's display text may fix a mis-heard word, never add a claim).
- Pauses: set_gap with the pause ID and the new length in ms (shorter is tighter; the speaker's breath stays whole).
- Captions: set_caption_style changes every page's look (size_px, font, case, colours, animation, lines); \
edit_caption_page changes one page (its words, emphasis words, display text or position); set_captions replaces \
the whole caption plan (enabled false turns captions off). Bigger text means fewer words per line; keep pages short.
- On-screen text: add_text, update_text, remove_text (short labels, never a CTA the creator did not say).
- Zoom / punch-in: set_framing on a segment or word range (scale 1.0-1.3, centred on the face); clear_framing \
removes it.
- Voice and loudness: set_voice_chain (compression, EQ, de-ess, the local light denoise) and set_loudness \
(integrated LUFS). Paid voice isolation is not available.
- Music, sound effects, b-roll, colour looks and new takes are not available in this tier: return no ops for them \
and say so in the summary.
- Use note to record why when a choice is not obvious.

Allowed ops (JSON schemas; "op" names the op):"""


def _op_schemas() -> str:
    from studio.doc.ops import OP_CLASSES

    return "\n".join(json.dumps(_compact(OP_CLASSES[n].model_json_schema()), separators=(",", ":"))
                     for n in CHAT_OPS)


def build_prefix() -> str:
    """The cached prefix (the agent's instructions): the chat rules and the compact schema of every allowed op. It is
    the same bytes for every job, so the retry seconds later reads it at the cache price."""
    return _RULES + "\n" + _op_schemas()


def refused_topics(instruction: str) -> list[str]:
    """The standard-tier refusals (music, sound effects, b-roll, paid voice isolation) the instruction asks to add. A
    topic named as content ("cut the sentence about my song") is not a request for it."""
    found = {topic for _c, topics in _refused_clauses(instruction) for topic in topics}
    return [topic for topic, _w in _TOPICS if topic in found]


def has_other_request(instruction: str) -> bool:
    """True when some clause of the instruction asks for something other than a refused topic (then the planner is
    called for that part); a clause of one or two words ("please", "thanks") is not a request."""
    for clause, topics in _refused_clauses(instruction):
        words = re.findall(r"[a-z0-9']+", clause)
        if len(words) < 3:
            continue
        if not topics:
            return True
    return False


def refusal_line(topics: list[str], index: TakeIndex) -> str:
    """What the creator is told about a refused request, with the price of the way to get it."""
    from studio.agent.cost import format_usd

    if not topics:
        return ""
    what = topics[0] if len(topics) == 1 else ", ".join(topics[:-1]) + " and " + topics[-1]
    lo, hi = REVIEW_ROUND_USD
    line = (f"{what[0].upper() + what[1:]} {'is' if len(topics) == 1 else 'are'} not part of the standard tier (one "
            "planner call, code QA; no music, sound effects, b-roll or paid voice isolation). To get "
            f"{'it' if len(topics) == 1 else 'them'}, switch this job to the Director tier with `studio edit <job> "
            f"--tier director --max-usd {UPGRADE_MAX_USD}` (it resumes from this cut, so the first pass is not paid "
            f"again; about ${lo:.2f}-{hi:.2f} in model spend per review round at list prices, and --max-usd stops "
            "opening rounds once the job's spend reaches it), then ask again with `studio chat`")
    if "paid voice isolation" in topics:
        src_s = index.media.duration_us / 1e6
        line += (f"; paid voice isolation costs ${ISOLATION_USD_PER_MIN:.2f} per source minute (about "
                 f"{format_usd(src_s / 60.0 * ISOLATION_USD_PER_MIN)} for this {src_s:.0f} s take)")
    return line + "."


def _caption_lines(job: Job, index: TakeIndex, doc: CutDocument, max_pages: int = 80) -> list[str]:
    """The caption pages as they will render (IDs, words, text, height, placement, emphasis) and the layout lines
    the Director reads (size on screen, wraps, placement options)."""
    if doc.captions is None or not doc.captions.enabled:
        return ["Captions are off (set_captions with enabled true turns them on)."]
    try:
        from studio.agent.director import _caption_layout_lines
        from studio.compile.timeline import compile as compile_timeline

        tl = compile_timeline(doc, index, job=job)
    except Exception as e:  # geometry is context, never a reason to refuse the chat
        return [f"Caption geometry unavailable ({type(e).__name__}); {len(doc.captions.pages)} pages."]
    if not tl.captions:
        return ["No caption pages in the compiled cut."]
    st = doc.captions.style
    out = [f"{len(tl.captions)} pages, style {st.font} {st.size_px}px {st.case} {st.animation}, {st.lines} line(s):"]
    for pg in tl.captions[:max_pages]:
        text = pg.text or " ".join(w.text for w in pg.words)
        emph = [w.word_id for w in pg.words if w.emphasis]
        out.append(f"  {pg.page_id or '-'} {pg.words[0].word_id}-{pg.words[-1].word_id} \"{text}\" "
                   f"y{pg.y_norm:.2f} {pg.placement or '?'}" + (f" emphasis {','.join(emph)}" if emph else ""))
    if len(tl.captions) > max_pages:
        out.append(f"  … {len(tl.captions) - max_pages} more pages")
    with contextlib.suppress(Exception):
        # the Director's lines name its own tools (auto_captions, caption_preview, meta_ops): keep the measurements
        for ln in _caption_layout_lines(tl, doc, index):
            if "caption_preview" not in ln:
                out.append(re.sub(r"\s*\[auto_captions[^\]]*\]", "", ln))
    return out


def chat_context(job: Job, index: TakeIndex, doc: CutDocument, instruction: str, *,
                 refused: list[str] | None = None) -> str:
    """The user turn: the instruction, the current edit (summary and cut transcript), the words out of the cut, the
    pauses in the cut, the captions and the on-screen text."""
    from studio.agent.tools import EditSession

    session = EditSession(job, index, doc=doc, by=CHAT_BY)
    parts = [f"CREATOR REQUEST: \"{' '.join(instruction.split())}\""]
    if refused:
        parts.append(f"(The request also mentions {', '.join(refused)}: not available in this tier and answered "
                     "separately; ignore that part.)")
    parts += ["", "CURRENT EDIT (the delivered cut; only the IDs below are valid)", session.q_transcript("cut"), "",
              "WORDS NOT IN THE CUT (restore_words brings a range back)", session.q_transcript("removed"), "",
              "PAUSES IN THE CUT (set_gap gap_id ms)", session.q_gaps(min_ms=150.0, only_in_cut=True), "",
              "CAPTIONS", *_caption_lines(job, index, doc)]
    if doc.texts:
        parts += ["", "ON-SCREEN TEXT"]
        parts += [f"  {t.id} {t.kind} \"{t.text}\" on {t.anchor_from_word}-{t.anchor_to_word}" for t in doc.texts]
    parts += ["", "Return the JSON answer for this request."]
    return "\n".join(parts)


def _retry_prompt(rejections: list[str]) -> str:
    lines = "\n".join(f"- {r}" for r in rejections)
    return ("Some of your answer could not be applied:\n" + lines + "\n\nReturn the corrected complete answer: every "
            "op you want applied (including the ones that were fine), using only the allowed ops and the IDs shown, "
            "or leave out what cannot be done and say so in the summary.")


# ============================================================================================ the call
@dataclass
class ChatEdit:
    """What :func:`edit_document` did. ``doc`` is the new document (the base when nothing applied); ``changed`` is
    True when something that renders differs from the base."""

    doc: CutDocument
    changed: bool
    summary: str
    called: bool = False
    attempts: int = 0
    applied: int = 0
    rejected: list[str] = field(default_factory=list)  # still rejected after the retry
    refused: list[str] = field(default_factory=list)  # refused topics (music, b-roll, ...)
    usage: dict[str, Any] = field(default_factory=dict)
    note: str = ""


_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _usage_of(messages: list[Any]) -> dict[str, int]:
    from pydantic_ai.messages import ModelResponse

    out = dict.fromkeys(_USAGE_KEYS, 0)
    for m in messages:
        if isinstance(m, ModelResponse):
            for k in _USAGE_KEYS:
                out[k] += int(getattr(m.usage, k, 0) or 0)
    return out


def _agent(spec: ModelSpec, job: Job, instructions: str) -> Any:
    from pydantic_ai import Agent, PromptedOutput

    from studio.agent.providers import build_model

    model = build_model(spec, role="planner", job=job, stage="chat")
    return Agent(model, output_type=PromptedOutput(ChatOps), instructions=instructions, retries=0, name="chat",
                 model_settings={"max_tokens": MAX_TOKENS})


def _call(agent: Any, spec: ModelSpec, prompt: str, history: list[ModelMessage]
          ) -> tuple[ChatOps | None, list[ModelMessage], dict[str, int], str | None, str | None]:
    """One call: ``(answer, messages, usage, parse_problem, error)``. A parse problem spends the retry (unless the
    answer stopped at ``max_tokens``: the problem then says so and the caller does not retry); an error (refusal,
    provider failure) ends the chat."""
    from pydantic_ai import capture_run_messages
    from pydantic_ai.exceptions import ContentFilterError, UnexpectedModelBehavior

    from studio.agent.providers import map_provider_error

    with capture_run_messages() as captured:
        try:
            result = agent.run_sync(prompt, message_history=list(history))
        except ContentFilterError as e:
            msgs = list(captured)
            return None, msgs, _usage_of(msgs[len(history):]), None, map_provider_error(e, spec).message
        except UnexpectedModelBehavior as e:
            from studio.plan.planner import _truncated

            msgs = list(captured)
            cut = " (it stopped at max_tokens)" if _truncated(msgs[len(history):]) else ""
            return (None, msgs, _usage_of(msgs[len(history):]),
                    f"the answer was not a valid JSON object {{\"ops\": [...], \"summary\": \"...\"}} ({e}){cut}",
                    None)
        except Exception as e:  # provider/network error (the SDK already retried transient ones)
            msgs = list(captured)
            return None, msgs, _usage_of(msgs[len(history):]), None, map_provider_error(e, spec).message
    return result.output, list(result.all_messages()), _usage_of(list(result.new_messages())), None, None


def _screen(ops: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Ops outside :data:`CHAT_OPS` (or buying paid isolation) are rejected before any dry run."""
    keep: list[dict[str, Any]] = []
    rejected: list[str] = []
    for i, op in enumerate(ops):
        name = op.get("op") if isinstance(op, dict) else None
        if not isinstance(op, dict) or not name:
            rejected.append(f"[{i}] not an op object (each op is a JSON object with an \"op\" name)")
        elif name not in CHAT_OPS:
            rejected.append(f"[{i}] {name}: not available in a standard-tier chat (allowed: {', '.join(CHAT_OPS)})")
        elif name == "set_voice_chain" and str((op.get("spec") or {}).get("denoise") or "") == "isolate":
            rejected.append(f"[{i}] set_voice_chain: paid voice isolation (denoise 'isolate') is not on the standard "
                            "tier; use 'light' for the local denoise")
        else:
            keep.append(op)
    return keep, rejected


def _dry_run(base: CutDocument, ops: list[dict[str, Any]], index: TakeIndex, job: Job
             ) -> tuple[list[dict[str, Any]], list[str]]:
    """``(accepted ops, rejection lines)`` from applying ``ops`` in order without saving anything."""
    from studio.doc.ops import apply_ops

    _doc, results = apply_ops(base, ops, index, job=job, by=CHAT_BY, persist=False)
    accepted, rejected = [], []
    for r in results:
        if r.applied:
            accepted.append(ops[r.index])
        elif r.reason and r.reason != "no change":
            rejected.append(f"{r.op}: {r.reason}")
    return accepted, rejected


def edit_document(job: Job, index: TakeIndex, base: CutDocument, instruction: str, *, spec: ModelSpec,
                  settings: Settings | None = None, max_usd: float | None = None) -> ChatEdit:
    """Plan and apply one chat instruction on ``base`` (see the module docstring). ``max_usd`` caps this chat's
    model spend (:func:`studio.plan.planner.spend_cap`). Never raises for a model failure: the result then carries
    the base unchanged and a summary saying why."""
    from studio.agent.director import render_relevant
    from studio.agent.providers import map_provider_error, trace_event
    from studio.agent.tools import EditSession
    from studio.plan.digest import estimate_tokens
    from studio.plan.planner import spend_cap

    refused = refused_topics(instruction)
    refusal = refusal_line(refused, index)
    if refused and not has_other_request(instruction):
        ed = ChatEdit(doc=base, changed=False, summary=refusal, refused=refused, note="refused without a model call")
        _log(job, instruction, ed, ops=[])
        trace_event(job, "chat_plan", stage="chat", called=False, refused=refused, changed=False)
        return ed
    context = chat_context(job, index, base, instruction, refused=refused)
    cap = spend_cap(job, max_usd)
    blocked = cap.first_call()
    if blocked:
        ed = ChatEdit(doc=base, changed=False, refused=refused, note=blocked,
                      summary=f"The chat edit was not run ({blocked}); nothing changed." + (f" {refusal}" if refusal
                                                                                           else ""))
        _log(job, instruction, ed, ops=[])
        return ed
    try:
        agent = _agent(spec, job, build_prefix())
    except Exception as e:  # a missing key or an unusable spec: nothing was called
        err = map_provider_error(e, spec)
        ed = ChatEdit(doc=base, changed=False, refused=refused, note=f"planner {err.kind}: {err.message}",
                      summary=f"The chat edit could not run ({err.message}); nothing changed."
                      + (f" {refusal}" if refusal else ""))
        _log(job, instruction, ed, ops=[])
        return ed
    history: list[ModelMessage] = []
    prompt = context
    usage = dict.fromkeys(_USAGE_KEYS, 0)
    attempts = 0
    answer: ChatOps | None = None
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    error: str | None = None
    skipped: str | None = None
    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        out, messages, use, problem, error = _call(agent, spec, prompt, history)
        for k in _USAGE_KEYS:
            usage[k] += use.get(k, 0)
        if error is not None:
            break
        if out is None:
            rejected = [problem or "the answer could not be read"]
        else:
            answer = out
            screened, rejected = _screen(out.ops)
            accepted, dry = _dry_run(base, screened, index, job)
            rejected += dry
        if not rejected:
            break
        if attempts < MAX_ATTEMPTS:
            retry = _retry_prompt(rejected)
            why = (f"retry skipped: the answer stopped at max_tokens ({MAX_TOKENS})" if problem
                   and "stopped at max_tokens" in problem else None)
            if why is None:
                more = cap.another_call(spec, estimate_tokens(build_prefix()) + estimate_tokens(context)
                                        + estimate_tokens(retry) + int(use.get("output_tokens", 0)),
                                        max_tokens=MAX_TOKENS)
                why = f"retry {more}" if more else None
            if why:
                trace_event(job, "planner_retry_skipped", stage="chat", model=spec.model, reason=why)
                skipped = why
                break
            trace_event(job, "planner_retry", stage="chat", model=spec.model, rejections=rejected[:12])
            history, prompt = messages, retry
    doc = base
    applied = 0
    if accepted:
        res = EditSession(job, index, doc=base, by=CHAT_BY).apply_ops(accepted, by=CHAT_BY)
        doc = res.doc
        applied = res.applied
        rejected += [f"{r.op}: {r.reason}" for r in res.results if not r.applied and r.reason != "no change"]
    changed = render_relevant(doc) != render_relevant(base)
    if error is not None and answer is None:
        summary = f"The chat edit could not run ({error}); nothing changed."
    else:
        summary = (answer.summary.strip() if answer is not None else "") or \
            ("Done." if changed else "No render-relevant change was needed.")
        if rejected:
            summary += (f" Not done (rejected; {skipped}): " if skipped else " Not done (rejected after one retry): ") \
                + "; ".join(rejected[:6]) + "."
    if refusal:
        summary += " " + refusal
    ed = ChatEdit(doc=doc, changed=changed, summary=summary, called=True, attempts=attempts, applied=applied,
                  rejected=rejected, refused=refused, usage=usage, note=error or skipped or "")
    _log(job, instruction, ed, ops=list(answer.ops) if answer is not None else [])
    trace_event(job, "chat_plan", stage="chat", model=spec.model, called=True, attempts=attempts, applied=applied,
                rejected=rejected[:12], refused=refused, changed=changed, doc_version=doc.version, error=error,
                **usage)
    return ed


def _log(job: Job, instruction: str, ed: ChatEdit, *, ops: list[Any]) -> None:
    with contextlib.suppress(OSError, TypeError, ValueError):
        job.append_jsonl(CHAT_LOG, [{"instruction": instruction[:1000], "called": ed.called, "attempts": ed.attempts,
                                     "ops": ops, "applied": ed.applied, "rejected": ed.rejected,
                                     "refused": ed.refused, "changed": ed.changed, "doc_version": ed.doc.version,
                                     "summary": ed.summary, "usage": ed.usage, "note": ed.note}])


# ============================================================================================ render
@dataclass
class ChatRender:
    """The render that carries a chat change: ``mode`` is ``overlay`` (overlay + master on the champion's A-roll and
    mixes) or ``full``; after a failed QA, ``repair`` is what the code-only repair did and ``render_dir``/``doc`` are
    its result."""

    render_dir: Path
    doc: CutDocument
    passed: bool
    mode: str
    failures: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    repair: RepairResult | None = None


def _cut_sig(doc: CutDocument) -> Any:
    segs = [(s.from_word, s.to_word, s.speed, s.seam_in.model_dump(mode="json"), sorted(s.gap_overrides.items()),
             s.framing.model_dump(mode="json") if s.framing is not None else None) for s in doc.segments]
    return (segs, [i.model_dump(mode="json") for i in doc.inserts], doc.audio.model_dump(mode="json"),
            doc.color.model_dump(mode="json") if doc.color is not None else None)


def cut_unchanged(before: CutDocument, after: CutDocument) -> bool:
    """Nothing the A-roll or the mix depends on changed (segments, pauses, speed, seams, framing, inserts, audio,
    colour): only captions or on-screen text did, so the overlay and the master are all that need rebuilding."""
    return _cut_sig(before) == _cut_sig(after)


def render_change(job: Job, index: TakeIndex, before: CutDocument, doc: CutDocument, champion: Path | None, *,
                  settings: Settings | None = None) -> ChatRender:
    """Render ``doc`` (the chat's new version of ``before``) the cheapest way, gate it on the invariants and, when it
    fails, run one code-only repair. The failed render's intermediates are pruned when a repair render replaced it;
    the caller prunes or keeps the returned render."""
    from studio.agent.loop import prune_render, render_full
    from studio.config import get_settings
    from studio.plan import repair as repair_mod
    from studio.qa.invariants import evaluate_render

    s = settings or get_settings()
    notes: list[str] = []
    rd: Path | None = None
    mode = "full"
    if champion is not None and cut_unchanged(before, doc):
        try:
            rd = repair_mod._overlay_render(job, doc, index, champion, profile="standard", corrections={},
                                            notes=notes)
            mode = "overlay"
        except repair_mod._NeedFull as e:
            notes.append(f"overlay pass not possible ({e}): full standard render instead")
    if rd is None:
        rd = render_full(job, doc, index, profile="standard")
    run = evaluate_render(job, rd, settings=s)
    passed = run.passed
    failures = [r.to_dict() for r in run.results if not r.passed]
    rep: RepairResult | None = None
    if not passed:
        rep = repair_mod.repair(job, doc, index, rd, failures, profile="standard", settings=s)
        notes += rep.notes
        if rep.rerendered and rep.render_dir != rd:
            prune_render(rd, keep_finals=False, job=job, reason="replaced by repair")
        rd, doc, passed, failures = rep.render_dir, rep.doc, rep.passed, list(rep.failures)
    return ChatRender(render_dir=rd, doc=doc, passed=passed, mode=mode, failures=failures, notes=notes, repair=rep)
