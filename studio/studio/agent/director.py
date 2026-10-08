"""The Director — the lead editor and the only writer of the CutDocument (ARCHITECTURE §9).

The Director is one pydantic-ai agent conversation over an :class:`~studio.agent.tools.EditSession`, driven
through the doctrine's stages **in code**:

``brief → story → fine_cut → reframe → broll → captions → sound → color → finalize``

then ``revise`` rounds from the champion loop (:mod:`studio.agent.loop`) and ``chat`` for creator
instructions. Each editing stage is a separate ``agent.run`` on the same, append-only message history (thinking
blocks stay bound to their prefix, so the tool list never changes between stages); a render-review round or a
chat edit starts in a fresh context from the stage summaries and the document (``DirectorOptions.fresh_rounds``:
the editing conversation is ~300k tokens by then, re-read on every call and re-written after the render gap);
the stage is enforced at runtime:

* **Op gating.** :class:`DirectorSession` only accepts the op families of the current stage
  (:data:`STAGE_OPS`): the story stage can only cut, finishing passes only add their own layer (plus the
  earlier finishing layers, which they may adjust), the picture is locked after the fine cut. Revision and
  chat rounds open every family.
* **Exit tests.** A stage ends only when the Director calls ``finish_stage(summary)`` and the stage's exit
  test passes in code (brief written; story passes the ``radio_test``; fine cut compiled with every pause
  target reported as asked → kept and its seams looked at; no validation errors). An output validator
  refuses to end a run whose stage is unfinished.
* **Restraint.** Every finishing pass accepts "none". The b-roll bar is enforced in code
  (:func:`studio.broll.rank.parse_judgment`: every rubric item ≥ 4 or no insert).

Director-only tools (on top of the session's query/op tools): ``finish_stage``, ``radio_test``,
``compile_check`` (compiles the document without rendering: pause targets asked → kept, effective punch-in
scale after the face-safe clamp, seams, inserts, text dwell, caption pages over 20 CPS or under 0.5 s),
``check_seams`` (source frames either side of every cut with the measured face shift, eyes and mouth),
``broll_search`` / ``broll_use`` / ``broll_screenshot`` / ``broll_generate`` (Pexels stock, creator
media, captures, generated stills when the account allows), ``auto_captions``, ``voice_plan``,
``music_options`` (ElevenLabs beds fitted to this cut), ``sfx_guidance`` and ``measure_color``.

Models: the house Director is ``Settings.director_model`` (``claude-fable-5-1``) at ``Settings.director_effort``
(``max``: quality is the only goal) unless ``STUDIO_DIRECTOR_EFFORT`` says otherwise; unavailable models
(404/403/retention) fall back inside the request (:func:`studio.agent.providers.build_model`), and a refusal or a
persistent overload switches the rest of the edit to ``Settings.director_fallback_model`` in a fresh context (a
model's thinking cannot move to another model). A BYOK spec (``ModelSpec(byok=True)``) never falls back to a house
model and never enables server-side model fallbacks: billing and the model never switch silently.

State for resuming after a crash lives in ``logs/director_state.json`` (finished stages with summaries
and document versions) and ``logs/director_messages.json`` (the conversation).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from studio.agent.providers import (
    ModelSpec,
    capabilities_for,
    director_fallback_spec,
    house_spec,
    map_provider_error,
    trace_event,
)
from studio.agent.tools import EditSession, _clip, _err, _prepare_image, summarize_doc
from studio.config import get_settings

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # the CLI prints its own progress

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai import Tool
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model

    from studio.compile.models import Timeline
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "STAGE_ORDER", "FINISHING_PASSES", "DIRECTOR_STAGES", "STAGE_OPS", "DIRECTOR_TOOL_NAMES", "DirectorError",
    "DirectorOptions", "DirectorSession", "StageRecord", "DirectorState", "RevisionResult", "Director",
    "director_spec", "render_relevant", "run_director", "chat_edit",
]

#: Stages the Director runs for a new edit, in doctrine order (SKILL.md "Stage order").
FINISHING_PASSES: tuple[str, ...] = ("reframe", "broll", "captions", "sound", "color")
STAGE_ORDER: tuple[str, ...] = ("brief", "story", "fine_cut", *FINISHING_PASSES, "finalize")
#: Every stage name a session accepts.
DIRECTOR_STAGES: tuple[str, ...] = (*STAGE_ORDER, "revise", "chat")

_ALL_FAMILIES = ("cut", "framing", "inserts", "captions", "audio", "color", "meta")
#: Op families open in each stage. Finishing passes may adjust the layers finished before them (never the cut).
STAGE_OPS: dict[str, tuple[str, ...]] = {
    "brief": ("meta",),
    "story": ("cut", "meta"),
    "fine_cut": ("cut", "framing", "meta"),
    "reframe": ("framing", "meta"),
    "broll": ("framing", "inserts", "meta"),
    "captions": ("framing", "inserts", "captions", "meta"),
    "sound": ("framing", "inserts", "captions", "audio", "meta"),
    "color": ("framing", "inserts", "captions", "audio", "color", "meta"),
    "finalize": _ALL_FAMILIES,
    "revise": _ALL_FAMILIES,
    "chat": _ALL_FAMILIES,
}

#: Model requests allowed per stage run (a runaway guard, far above normal use).
REQUEST_LIMITS: dict[str, int] = {
    "brief": 40, "story": 80, "fine_cut": 90, "reframe": 50, "broll": 90, "captions": 60, "sound": 60,
    "color": 40, "finalize": 40, "revise": 90, "chat": 90,
}

DIRECTOR_TOOL_NAMES: tuple[str, ...] = (
    "finish_stage", "radio_test", "compile_check", "check_seams", "broll_search", "broll_use", "broll_screenshot",
    "broll_generate", "auto_captions", "caption_preview", "voice_plan", "music_options", "sfx_guidance",
    "measure_color",
)

_TRANSIENT = ("overloaded", "server", "timeout", "connection", "rate_limit")
_FALLBACK_KINDS = ("refusal", "overloaded", "server", "not_found", "permission")
_RUBRIC_ITEMS = ("subject", "readable", "clean", "look", "sharp")
_STATE_FILE = "director_state.json"
_MESSAGES_FILE = "director_messages.json"
_MAX_SAVED_HISTORY_BYTES = 60_000_000


class DirectorError(RuntimeError):
    """The Director could not run (model unavailable, key missing, unrecoverable provider error)."""


@dataclass
class DirectorOptions:
    """Knobs for a Director run (the defaults are the quality path)."""

    effort: str | None = None  # None: Settings.director_effort (STUDIO_DIRECTOR_EFFORT, default "max")
    request_limits: dict[str, int] = field(default_factory=lambda: dict(REQUEST_LIMITS))
    transient_retries: int = 2
    backoff_s: tuple[float, ...] = (20.0, 60.0)
    allow_fallback: bool = True
    max_creator_questions: int = 1
    save_history: bool = True
    #: Start every revision round and chat edit in a fresh context (the stage summaries, the current document and the
    #: critics' notes) instead of the whole editing conversation. By the first render review that conversation runs to
    #: ~300k tokens with every stale contact sheet in it, is re-read on every call, and after the render gap is
    #: re-written at the cache-write price; the loop's brief already carries what earlier rounds decided.
    fresh_rounds: bool = True


def director_spec(settings: Settings | None = None, *, effort: str | None = None) -> ModelSpec:
    """The house Director spec (``Settings.director_*``) at ``effort`` (default: ``Settings.director_effort``, which
    ``STUDIO_DIRECTOR_EFFORT`` sets; ``max`` when unset)."""
    s = settings or get_settings()
    spec = house_spec("director", settings=s)
    eff = effort or os.environ.get("STUDIO_DIRECTOR_EFFORT") or s.director_effort or "max"
    return spec.model_copy(update={"effort": eff})


def render_relevant(doc: CutDocument) -> dict[str, Any]:
    """The parts of a document that change the rendered video (versions, notes and counters excluded)."""
    return doc.model_dump(mode="json", exclude={"version", "parent_version", "created_by", "notes", "counters",
                                                "meta", "brief", "job_id", "hook_alternates"})


# ============================================================================================ session
class DirectorSession(EditSession):
    """:class:`EditSession` with the Director's finer stages (each finishing pass gates its own families)
    and bookkeeping for the stage exit tests."""

    def __init__(self, *args: Any, max_creator_questions: int = 1, **kwargs: Any):
        self.max_creator_questions = max_creator_questions
        self.stage_done = False
        self.stage_summary: str | None = None
        self.finished_version: int | None = None
        self.radio_sig: Any = None
        self.compile_sig: Any = None
        self.seams_sig: Any = None
        self.seams_unavailable = False
        self.caption_preview_sig: Any = None
        self.caption_preview_unavailable = False
        #: questions for the critics from the last finish_stage (answered when the next render is critiqued)
        self.critic_questions: list[str] = []
        #: the document a revise/chat round started from (its exit test compares against it)
        self.base_doc: Any = None
        super().__init__(*args, **kwargs)

    def set_stage(self, stage: str | None) -> None:
        if stage is not None and stage not in DIRECTOR_STAGES:
            raise ValueError(f"unknown stage {stage!r}; use one of {', '.join(DIRECTOR_STAGES)}")
        self.stage = stage
        self.stage_done = False
        self.stage_summary = None
        self.finished_version = None
        self.critic_questions = []

    def allowed_families(self) -> tuple[str, ...]:
        if self.stage is None:
            return _ALL_FAMILIES
        return STAGE_OPS.get(self.stage, _ALL_FAMILIES)

    async def call_ask_creator(self, question: str, options: list[str] | None) -> str:
        if self.stage not in ("brief", "chat", None):
            return ("NOT ASKED: questions to the creator belong to the brief stage. Decide from the footage and the "
                    "doctrine and record the assumption with a meta_ops note.")
        if len(self.questions) >= self.max_creator_questions:
            return ("NOT ASKED: the one creator question for this edit has been used. Decide yourself, prefer the "
                    "more restrained reading and record the assumption with a meta_ops note.")
        return await super().call_ask_creator(question, options)


def _story_sig(doc: CutDocument) -> tuple[Any, ...]:
    return tuple((s.from_word, s.to_word) for s in doc.segments)


def _cut_sig(doc: CutDocument) -> tuple[Any, ...]:
    return tuple((s.from_word, s.to_word, s.speed, tuple(sorted(s.gap_overrides.items())), s.seam_in.kind,
                  s.seam_in.lead_ms) for s in doc.segments)


def _seam_set(doc: CutDocument) -> set[tuple[str, str]]:
    return {(a.to_word, b.from_word) for a, b in zip(doc.segments, doc.segments[1:], strict=False)}


def _caption_sig(doc: CutDocument) -> str:
    """Everything that decides where and how the captions render: the plan and style, the story, framing and
    punch-ins, inserts and texts."""
    return json.dumps({
        "c": doc.captions.model_dump(mode="json") if doc.captions is not None else None,
        "s": [(s.from_word, s.to_word, s.speed, s.seam_in.kind,
               s.framing.model_dump(mode="json") if s.framing is not None else None) for s in doc.segments],
        "i": [(i.id, i.mode, i.anchor_from_word, i.anchor_to_word) for i in doc.inserts],
        "t": [(t.id, t.kind, t.anchor_from_word, t.anchor_to_word, str(t.position)) for t in doc.texts],
        "d": [d.platform for d in doc.deliverables]}, sort_keys=True, default=str)


# ============================================================================================ state
@dataclass
class StageRecord:
    stage: str
    summary: str = ""
    doc_version: int | None = None
    forced: bool = False
    model: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    requests: int = 0
    note: str = ""
    questions: list[str] = field(default_factory=list)  # for the critics (finish_stage questions_for_critics)


@dataclass
class DirectorState:
    """What survives a crash: finished stages (with document versions) and the model in use."""

    completed: list[StageRecord] = field(default_factory=list)
    model: str = ""
    fallback_active: bool = False

    @classmethod
    def load(cls, job: Job) -> DirectorState:
        p = job.logs_dir / _STATE_FILE
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        recs = [StageRecord(**{k: v for k, v in r.items() if k in StageRecord.__dataclass_fields__})
                for r in data.get("completed", []) if isinstance(r, dict)]
        return cls(completed=recs, model=str(data.get("model", "")),
                   fallback_active=bool(data.get("fallback_active", False)))

    def save(self, job: Job) -> None:
        from studio.jobs import write_json_atomic

        write_json_atomic(job.logs_dir / _STATE_FILE, {"completed": [asdict(r) for r in self.completed],
                                                        "model": self.model, "fallback_active": self.fallback_active})

    def done(self, stage: str) -> bool:
        return any(r.stage == stage for r in self.completed)

    def summaries(self) -> str:
        return "\n".join(f"- {r.stage} (v{r.doc_version}): {r.summary}" for r in self.completed if r.summary)


@dataclass
class RevisionResult:
    doc: CutDocument
    changed: bool
    summary: str = ""
    base_version: int = 0


# ============================================================================================ prompts
_RULES = """\
You are the Director of Yunicorn Studio: the lead editor, and the only one who changes this edit. A creator \
filmed themselves talking to the camera; your only goal is the best possible final video. Cost and time do \
not matter.

How this editing session works
- The edit runs in the doctrine's stage order: brief → story → fine_cut → finishing passes (reframe, broll, \
captions, sound, color) → finalize, then render-and-critique rounds in which you revise from the critics' \
notes. Each stage arrives as a message. Finish every stage by calling finish_stage(summary) once its exit \
test passes; code checks the exit test and tells you exactly what is missing. After finish_stage succeeds, \
reply with one short line and stop: the next stage follows.
- Ops of families outside the current stage are rejected with a reason. Do not polish what a later stage \
will cut; after the fine cut the picture is locked (revision rounds may reopen it).
- You perceive the take only through tools: transcript with IDs, gaps, prosody, visual events, frames with \
burned IDs, and measurements. You cannot hear, so sound decisions rest on measurements.
- Address everything by ID: words w0001, gaps g0001, sentences s001, retake clusters c01, segments \
seg001, inserts i001, texts t001, caption pages p001, SFX fx001, registered assets. Never pass a time as an \
edit coordinate; times printed by tools are for orientation only.
- Every op is validated and a rejected op comes back with its reason: fix it and resend. Batch the ops of \
one decision into one call (one document version per change). Record the reason for non-obvious decisions \
with a meta_ops note that names the ID.
- Restraint: "none" is a complete answer for every finishing layer (no punch-in, no b-roll, no card, no \
text, no music, no SFX, no colour change). Every addition needs a job you can say in one line and must \
survive the subtraction test. Over-editing is the failure this doctrine designs against.
- Protect what only this creator could say: their spoken CTA, catchphrases, laughs, qualifiers. Never \
invent a CTA or splice a claim they did not make.
- Batch mode: the creator is not available. ask_creator returns no answer; ask at most one question in the \
whole edit, only in the brief stage, and only if the answer would change the edit.

Useful tools beyond the query and op tools: radio_test (the kept story as a listener hears it), \
compile_check (compiles without rendering: pause targets asked → kept, effective punch-in scale, seams, \
insert and text timing, caption reading speed), check_seams (frames either side of every cut), \
broll_search → broll_use (sourced b-roll judged against a written bar; "none" is valid), auto_captions, \
voice_plan, music_options, sfx_guidance, measure_color, and load_skill for the topic files. The doctrine \
follows."""


def _stage_prompt(stage: str, ctx: dict[str, Any]) -> str:
    brief = ctx.get("brief") or "none given (infer the goal from the footage)"
    style = ctx.get("style") or "not specified (classify it from the transcript; blends and 'none' are allowed)"
    platforms = ", ".join(ctx.get("platforms") or ["tiktok"])
    caps = ctx.get("capabilities") or ""
    if stage == "brief":
        return (
            "STAGE 1/9 — BRIEF.\n"
            f"Creator's brief: {brief}\nRequested style: {style}\nDestinations: {platforms}\n\n"
            "Start with get_overview, get_transcript(view='full') and get_clusters; note the measured delivery energy. "
            "Load story-and-hook, platforms and the style file that fits. Then write the brief with meta_ops: "
            "set_brief (goal = the one idea in 25 words or fewer; audience; hook = the promise of the first 3 s; "
            "beats; target_length_s; cta = only a CTA the creator actually says; vibe; visual_plan and sound_plan, "
            "writing 'none' where that is the call; donts; deviations from doctrine priors with one-line reasons; "
            "rubric = 6-10 binary yes/no questions about what a viewer experiences, each checkable on the rendered "
            "video by someone who never saw your plan, e.g. 'Muted, does the first caption page state the topic by "
            "2 s?', 'Is every caption readable at phone size without covering the face?', 'Does the payoff land "
            "before any swipe-worthy stretch?'. Never restate your own add/skip decisions as a check (not 'Is there "
            "no music?', not 'Is there at most one punch-in?'): critics grade the viewer's experience, not conformance "
            "to your plan) and set_style (primary, at most "
            "one blend, dials from the measured energy). Name the payoff and spoken-CTA word IDs in the brief (beats / "
            "cta): pins can only hold words that are in the story, so you pin them right after building the story.\n"
            f"{caps}\n"
            "Exit test: you can state the idea and the payoff. Then call finish_stage(summary)."
        )
    if stage == "story":
        return (
            "STAGE 2/9 — STORY CUT. Words and order only.\n"
            "Load cutting-and-pacing (and endings-loops-ctas when the ending needs a decision). Choose each retake by "
            "delivery (get_clusters, get_prosody, view_frames); remove preamble, superseded takes (a line is a false "
            "start only when a later re-delivery exists), tangents, repetition, and anything after the payoff except "
            "the spoken CTA; reorder only the open, and only if the hook needs it. Build the story with cut_ops "
            "(set_story for the whole story; choose_take, cut_words, restore_words to refine), and in the same batch "
            "(apply_ops) pin the payoff and the spoken CTA (meta pin kind payoff / cta; must_keep for catchphrases or "
            "laughs worth protecting): pinned words cannot be removed by any later op.\n"
            "Then call radio_test and read it as a listener who never heard the cut words: it must make sense beat to "
            "beat, the first 3 s must tell the topic, and the payoff must deliver the hook's promise. Fix and re-run "
            "until it passes. Exit: finish_stage with your radio-test verdict."
        )
    if stage == "fine_cut":
        return (
            "STAGE 3/9 — FINE CUT.\n"
            "Load cutting-and-pacing (and speed only if you are tempted to change speed). Classify the pauses inside "
            "the cut (get_gaps with only_in_cut=true) and shorten only outliers against this speaker's own rhythm "
            "(set_gap by gap ID; pauses are punctuation); remove fillers and stutters that hurt (cut_words) and keep "
            "the human ones; give every seam a treatment (set_seam: cut, jcut/lcut with lead_ms, punch) — each seam "
            "must remove a named problem. Speed stays 1.0 unless doctrine says otherwise.\n"
            "Then call compile_check (every pause you set comes back as asked → kept: a kept breath can leave a pause "
            "longer than asked) and check_seams (frames either side of every cut: face jump, blink, mouth; and what "
            "the ear gets: the pause the compile keeps, clause edge or MID-CLAUSE, words a recording dropout CHOPS, "
            "untranscribed sound in a pad, the click detector), and fix visible jumps mid-thought (move the seam to a "
            "thought boundary, J/L it into a shared silence, or punch) and every audible problem it names.\n"
            "Exit: no seam without a named problem; nothing clipped; rhythm fits the measured energy. The picture "
            "locks when you call finish_stage."
        )
    if stage == "reframe":
        return (
            "FINISHING PASS 1/5 — REFRAME AND PUNCH-INS (stage 4/9).\n"
            "Load framing-and-zooms. Decide base framing and any punch-ins (framing_ops set_framing on a segment or a "
            "word range, anchored on stressed words). A 1080p source cannot punch past the face-safe ceiling: "
            "compile_check reports asked → rendered scale and how much of the video it softens (upsampling above "
            "x1.2); when you need more than it allows, prefer a cutaway or nothing, and return to the base framing "
            "after the beat. Zero punch-ins is a valid answer, especially for a calm speaker. Then finish_stage."
        )
    if stage == "broll":
        return (
            "FINISHING PASS 2/5 — B-ROLL, DESIGNED CARDS AND GRAPHICS (stage 5/9).\n"
            "Load broll and broll-sourcing (transitions-and-graphics for cards). Try the cheapest tool first: nothing, "
            "a punch-in, caption emphasis, a designed card, then b-roll. For each beat that truly needs a picture "
            "(prove, show, orient, mark structure, hide a seam): write the need first (subject + action + setting, and "
            "the look-alikes that would be wrong), call broll_search, judge its contact sheet against the bar "
            "(score every rubric item 1-5; each must be >= 4), then broll_use(candidate_id, ..., scores) or decide "
            "none. Designed cards (numbers, lists, quotes, steps) go through inserts_ops add_insert with a card spec. "
            "Never cover the hook's face, an emotional line or the payoff face.\n"
            f"{caps}\n"
            "Then finish_stage ('none' is a complete answer)."
        )
    if stage == "captions":
        return (
            "FINISHING PASS 3/5 — CAPTIONS AND ON-SCREEN TEXT (stage 6/9).\n"
            "Load captions-and-text. Captions are on by default: call auto_captions to page the kept words (it applies "
            "a plan and reports each page's time on screen, reading speed, size and where it lands, plus the "
            "placement options on this take with their measured costs: under the chin in the strict band or the "
            "relaxed floor, smaller text, a base reframe that lifts the chin, above the head). None of them is "
            "imposed: choose position (auto_captions position=...), size and framing for this creator, and record "
            "why with a meta_ops note. Fix bad breaks, accents or display text with captions_ops edit_caption_page / "
            "set_caption_style; never delete words to fix reading speed. Add a hook title, callout or list only when "
            "it has a job (add_text). Then call caption_preview and look at the rendered pages on the framed picture "
            "under the platform UI mask at phone scale: size, place, and whether the eye stays on the face. The "
            "stage cannot finish until you have looked at the captions after your last caption or framing change. "
            "Then finish_stage."
        )
    if stage == "sound":
        return (
            "FINISHING PASS 4/5 — SOUND (stage 7/9).\n"
            "Load voice-and-loudness, music and sfx. Call voice_plan and apply the measured chain (audio_ops "
            "set_voice_chain) unless the default is right for this take. Music: decide none or a bed (a no-music "
            "master is always delivered). If a bed has a job, call music_options (beds generated and fitted to this "
            "cut) and pick one with audio_ops set_music {asset_id, level_lu_under_speech, ...}; otherwise "
            "set_music null. SFX only with a job and never on a key word (add_sfx; sfx_guidance for levels). "
            "Loudness stays -14 LUFS / -1 dBTP unless you have a reason. Then finish_stage."
        )
    if stage == "color":
        return (
            "FINISHING PASS 5/5 — COLOUR AND LOOK (stage 8/9).\n"
            "Load color-and-look. Look before you touch: view_frames (hires) and measure_color (face luma, cast, "
            "clipping). Use color_ops set_color only to fix a measured problem (exposure, white balance, a take "
            "mismatch) or for a look with a job; otherwise leave colour alone. Then finish_stage."
        )
    if stage == "finalize":
        return (
            "STAGE 9/9 — FINALIZE.\n"
            "Read get_transcript(view='cut') and run compile_check once more. Resolve validation warnings that matter "
            "and leave a closing note. Add nothing new. Hook alternates (meta_ops set_hook_alternates) are real "
            "variants: after the champion settles you build each one from it, it is rendered at full quality and "
            "judged pairwise against the champion with positions swapped; the winner ships and the other is "
            "delivered as an alternate. Record one only when two openings are both defensible and the footage "
            "supports both (at most two). Then finish_stage(summary, questions_for_critics=[...]): the document goes "
            "to render and critique, and the critics answer your questions (ID-anchored, about what you are unsure "
            "of) in the first review."
        )
    raise ValueError(f"no default prompt for stage {stage!r}")


# ============================================================================================ small helpers
def _caption_layout_lines(tl: Any, doc: Any, index: Any = None) -> list[str]:
    """How the caption pages will render: size on screen, wraps, and where the placer put them (and why)."""
    from studio.compile import captions as cap

    W, H = tl.width, tl.height
    ws = W / 1080.0
    style = doc.captions.style if doc.captions is not None else None
    req = style.size_px if style is not None else tl.captions[0].style.size_px
    base_lines = style.lines if style is not None else 1
    sizes: list[float] = []
    small: list[str] = []
    wrapped: list[str] = []
    two_line: list[str] = []
    kinds: dict[str, int] = {}
    tops: set[int] = set()
    one_line = 0
    for p in tl.captions:
        text = p.text or " ".join(w.text for w in p.words)
        fit = cap.caption_fit(p.style, text, W)
        px = fit.size_px / ws
        sizes.append(px)
        if px < req * 0.9 - 0.5:
            small.append(f"{p.page_id} \"{_clip(text, 28)}\" {px:.0f}px")
        if fit.lines > base_lines:
            wrapped.append(f"{p.page_id} \"{fit.display}\"")
        elif fit.lines > 1:
            two_line.append(f"{p.page_id} \"{fit.display}\"")
        else:
            one_line += 1
        kinds[p.placement or "?"] = kinds.get(p.placement or "?", 0) + 1
        tops.add(round((p.y_norm * H - fit.height / 2) / H * 1920))
    # how much one line holds at the chosen size (the doctrine's "≈20 chars per 690 px" depends on the font)
    st0 = style or tl.captions[0].style
    probe = "the quick brown fox jumps over lazy dogs"
    per_char = cap.estimate_text_width(probe, st0.size_px, st0.font, weight=st0.weight) / len(probe)
    cap_chars = int(690 / max(per_char, 1e-6))
    out = [f"  on screen: {min(sizes):.0f}-{max(sizes):.0f} px (style {req} px; floor 64 px), "
           f"{len(tops)} caption height(s), placement " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
           f"  one line holds ~{cap_chars} characters at {st0.size_px} px {st0.font} {st0.weight}: {one_line}/"
           f"{len(tl.captions)} pages are one line"]
    first = tl.captions[0]
    f0 = cap.caption_fit(first.style, first.text or " ".join(w.text for w in first.words), W)
    if f0.size_px / ws < req * 0.97 - 0.5 or f0.lines > 1 or first.placement in ("least_bad", "on_hair"):
        out.append(f"  HOOK PAGE {first.page_id} \"{f0.display}\" renders at {f0.size_px / ws:.0f} px, "
                   f"{f0.lines} line(s), placement {first.placement}: frame 0 is the cover and the first read, give it "
                   "a short one-line page at full size (edit_caption_page / re-page)")
    out.extend(_placement_option_lines(tl, doc, index, st0, kinds))
    if kinds.get("on_hair"):
        out.append("  on_hair pages: the block sits over the hair or forehead (between the head top and the eyes)")
    if kinds.get("least_bad"):
        out.append("  least_bad pages: no clean spot (they may graze the hair or chin, never eyes or mouth)")
    if small:
        out.append("  shrunk to fit one line: " + "; ".join(small[:10]) + " - split the page, or use fewer words")
    if wrapped:
        out.append("  wrapped past the style's lines (too wide even at the floor): " + "; ".join(wrapped[:10])
                   + " - split the page")
    if two_line:
        out.append("  two-line pages (line break shown as /): " + "; ".join(two_line[:12]))
    return out


def _placement_option_lines(tl: Any, doc: Any, index: Any, style: Any, kinds: dict[str, int]) -> list[str]:
    """The caption positions this take allows, with their measured costs (median face, base framing); the Director
    chooses. Nothing here is an instruction."""
    from studio.compile import captions as cap

    if index is None:
        return []
    W, H = tl.width, tl.height
    try:
        zone = cap.safe_zone_for([d.platform for d in doc.deliverables] or ["tiktok"], width=W, height=H)
        o = cap.placement_options(index, style=style, zone=zone, width=W, height=H)
    except Exception:
        return []
    if o is None:
        return []
    now = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
    bc, rf, ab = o["below_chin"], o["reframe"], o["above_head"]
    lines = [f"  placement options on this take (median face, base framing: chin y{o['chin_px']:.0f}, eye line "
             f"y{o['eye_line_px']:.0f}, head top y{o['head_top_px']:.0f}; strict text band ends "
             f"y{o['strict_band_bottom_px']:.0f}, relaxed caption floor y{o['relaxed_floor_px']:.0f}; a one-line "
             f"block at {o['size_px']} px is {o['block_h_px']:.0f} px tall). Now: {now}. The doctrine's prior is the "
             "top edge 40-120 px under the chin; when nothing fits it lists smaller text, then the lower band, then a "
             "matte, then folding a title into the captions. Pages follow the head, so a bobbing chin pushes some "
             "pages lower than these medians:"]
    if bc["fits_strict"]:
        cost = "fits inside the strict band"
    elif bc["fits_relaxed"]:
        cost = (f"{bc['into_ui_px']:.0f} px into the strict band (platform UI only when a post description runs "
                "long), inside the relaxed floor")
    else:
        cost = "does not fit above the relaxed floor at this size"
    lines.append(f"   (a) just under the chin: top y{bc['top_px']:.0f}, bottom y{bc['bottom_px']:.0f}: {cost} "
                 "[auto_captions position='below_chin']")
    rel_small = o.get("smaller_text_relaxed_px")
    lines.append("   (b) smaller text under the chin: inside the strict band "
                 + (f"at {o['smaller_text_px']} px" if o["smaller_text_px"] else
                    "nothing fits down to the 64 px legibility floor")
                 + (f"; above the relaxed floor at {rel_small} px" if rel_small else ""))
    rs, ss, ok = rf.get("relaxed_scale"), rf.get("strict_scale"), rf.get("clean_scale") or 1.0

    def scale_txt(v: Any) -> str:
        if v is None:
            return "n/a"
        if v == float("inf"):
            return "impossible (the face sits at the bottom of the source)"
        return f"x{v:.2f}" + (" (soft: past this source's clean upsampling)" if v > ok + 1e-6 else "")

    how = (f" (set_framing scale {rs:.2f}, center {{x: 0.5, y: {rf['centre_y']:.2f}}})"
           if rs not in (None, float("inf")) and rf.get("centre_y") is not None and rs > 1.0 + 1e-6 else "")
    lines.append(f"   (c) a base reframe that lifts the chin (framing_ops set_framing on every segment, crop low): "
                 f"{scale_txt(rs)} fits the relaxed floor{how}, {scale_txt(ss)} the strict band; this source stays "
                 f"clean to x{ok:.2f}; it also crops the top of the frame and enlarges the face")
    lines.append(f"   (d) above the head: top y{ab['top_px']:.0f}, bottom y{ab['bottom_px']:.0f}, "
                 + ("clear of the hair" if ab["clear_of_hair"] else "on the hair") + "; not in the doctrine's fallback "
                 "list: the eye leaves the face to find the text [auto_captions position='auto' falls back to it]")
    lines.append("  Choose for this creator and footage, record why (meta_ops note), then look with caption_preview.")
    return lines


def _fmt_s(t: float | Fraction | None) -> str:
    return "-" if t is None else f"{float(t):.2f}s"


def _words_text(index: TakeIndex, ids: Sequence[str], n: int = 120) -> str:
    return _clip(" ".join(index.word(w).display() for w in ids if index.has_word(w)), n)


def _free_bytes(path: Path) -> int:
    import shutil

    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


# ============================================================================================ director
class Director:
    """One Director for one job: a session, its tools and the staged conversation."""

    def __init__(
        self,
        job: Job,
        index: TakeIndex | None = None,
        *,
        spec: ModelSpec | None = None,
        settings: Settings | None = None,
        model: Model | None = None,
        fallback_model: Model | None = None,
        brief: str | None = None,
        style: str | None = None,
        platforms: Iterable[str] = ("tiktok",),
        batch: bool = True,
        render_preview: Callable[..., Any] | None = None,
        critique: Callable[..., Any] | None = None,
        ask_creator: Callable[..., Any] | None = None,
        options: DirectorOptions | None = None,
        creator_media: Sequence[str | os.PathLike[str]] = (),
        resume: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ):
        from studio.agent.providers import make_image_uploader

        self.job = job
        self.settings = settings or get_settings()
        self.options = options or DirectorOptions()
        self.spec = spec or director_spec(self.settings, effort=self.options.effort)
        self._model_override = model
        self._fallback_override = fallback_model
        self._sleep = sleep
        self.brief_text = brief
        self.style_name = style
        self.platforms = tuple(platforms) or ("tiktok",)
        self.state = DirectorState.load(job) if resume else DirectorState()
        self.using_fallback = bool(self.state.fallback_active)
        uploader = None
        if model is None and not self.using_fallback:
            with contextlib.suppress(Exception):
                uploader = make_image_uploader(self.spec, job=job)
        caps = self.spec.capabilities if model is None else capabilities_for("anthropic", self.settings.director_model)
        self.session = DirectorSession(
            job, index, settings=self.settings, batch=batch, by=f"director:{self.model_label}", capabilities=caps,
            platforms=self.platforms, render_preview=render_preview, critique=critique, ask_creator=ask_creator,
            image_uploader=uploader, max_creator_questions=self.options.max_creator_questions,
        )
        self.index = self.session.index
        self.broll_pool: dict[str, dict[str, Any]] = {}
        self._broll_sheets = 0
        self._creator_candidates: list[Any] = []
        self._load_broll_pool()
        if creator_media:
            self.add_creator_media(creator_media)
        self.tools: list[Tool] = [*self.session.tools(), *self._director_tools()]
        self.history: list[ModelMessage] = self._load_history() if resume else []
        self._handoff_reason: str | None = None
        self._traced: Any = None
        self.agent = self._make_agent()

    # ------------------------------------------------------------------ models
    @property
    def model_label(self) -> str:
        if self._model_override is not None and not self.using_fallback:
            return getattr(self._model_override, "model_name", "test-model")
        if self.using_fallback:
            return self._fallback_spec().model if self._fallback_spec() else self.spec.model
        return self.spec.model

    def _fallback_spec(self) -> ModelSpec | None:
        if self.spec.byok or not self.options.allow_fallback:
            return None
        if self.spec.provider != "anthropic":
            return None
        fb = director_fallback_spec(settings=self.settings)
        fb = fb.model_copy(update={"effort": self.spec.effort})
        return None if fb.model == self.spec.model else fb

    def _build_model(self) -> Model:
        from studio.agent.providers import TracedModel, build_model

        if self.using_fallback:
            if self._fallback_override is not None:
                return TracedModel(self._fallback_override, job=self.job, role="director", spec=None)
            fb = self._fallback_spec()
            if fb is None:
                raise DirectorError("no fallback Director model is available for this run")
            return build_model(fb, role="director", job=self.job)
        if self._model_override is not None:
            return TracedModel(self._model_override, job=self.job, role="director", spec=None)
        fb = self._fallback_spec()
        try:
            return build_model(self.spec, role="director", job=self.job, fallback=fb)
        except Exception as e:  # missing key etc.
            raise DirectorError(map_provider_error(e, self.spec).message) from None

    def _instructions(self) -> str:
        from studio.agent import skills as sk

        return _RULES + "\n\n" + sk.system_prompt_block(settings=self.settings)

    def _make_agent(self) -> Any:
        from pydantic_ai import Agent, ModelRetry

        self._traced = self._build_model()
        agent = Agent(self._traced, instructions=self._instructions(), tools=self.tools, retries=5,
                      output_type=str, name="director")

        @agent.output_validator
        def _stage_finished(output: str) -> str:
            s = self.session
            if not s.stage_done:
                raise ModelRetry(
                    f"The {s.stage} stage is not finished yet. Continue the stage and call "
                    "finish_stage(summary) once its exit test passes (it tells you what is missing).")
            if s.finished_version is not None and s.doc.version != s.finished_version:
                s.stage_done = False
                raise ModelRetry(
                    f"You changed the document (v{s.finished_version} → v{s.doc.version}) after finish_stage: call "
                    "finish_stage again so the exit test checks the final state.")
            return output

        return agent

    # ------------------------------------------------------------------ persistence
    def _load_history(self) -> list[ModelMessage]:
        p = self.job.logs_dir / _MESSAGES_FILE
        if not p.exists():
            return []
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            return list(ModelMessagesTypeAdapter.validate_json(p.read_bytes()))
        except Exception as e:
            trace_event(self.job, "director_note", note=f"conversation not restored ({type(e).__name__}); "
                                                        "continuing in a fresh context with stage summaries")
            return []

    def _save_history(self) -> None:
        if not self.options.save_history:
            return
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            data = ModelMessagesTypeAdapter.dump_json(self.history)
        except Exception:
            return
        p = self.job.logs_dir / _MESSAGES_FILE
        if len(data) > _MAX_SAVED_HISTORY_BYTES:
            p.unlink(missing_ok=True)
            return
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    # ------------------------------------------------------------------ stage runner
    def context(self) -> dict[str, Any]:
        return {"brief": self.brief_text, "style": self.style_name, "platforms": list(self.platforms),
                "capabilities": self.capabilities_text()}

    def capabilities_text(self) -> str:
        s = self.settings
        bits = []
        bits.append("Stock b-roll (Pexels): " + ("available" if s.has_key("pexels") else "unavailable (no key)"))
        n_creator = len(self._creator_candidates)
        bits.append(f"Creator media supplied: {n_creator} file(s)" if n_creator else "Creator media: none supplied")
        bits.append("Generated stills (Higgsfield): " + ("try broll_generate; the account may lack credits"
                                                           if s.has_key("higgsfield") else "unavailable"))
        bits.append("Music generation (ElevenLabs): " + ("available" if s.has_key("elevenlabs") else "unavailable"))
        return "Sources this run: " + "; ".join(bits) + "."

    def _fresh_context_prompt(self, stage: str, prompt: str) -> str:
        done = self.state.summaries() or "- (no stage finished yet)"
        why = self._handoff_reason or "the previous model's conversation could not continue"
        return (f"You are taking over this edit mid-way in a fresh context ({why}). Finished stages:\n" + done
                + "\n\nCurrent document:\n" + summarize_doc(self.session.doc, self.index, job=self.job)
                + "\n\nRe-read what you need with the query tools, then continue.\n\n" + prompt)

    def _start_fresh(self, reason: str) -> None:
        """Drop the carried conversation before a round: the next stage starts from the handoff prompt."""
        if not self.history:
            return
        trace_event(self.job, "director_note", note=f"fresh context: {reason} ({len(self.history)} messages dropped)")
        self.history = []
        self.session.new_conversation()
        self._handoff_reason = reason

    def run_stage(self, stage: str, prompt: str | None = None, *, record: bool = True,
                  attachments: Sequence[tuple[str, Path]] = ()) -> StageRecord:
        """Run one stage to its exit (or the request guard) and return its record. ``attachments`` (label, image)
        are shown with the stage's prompt."""
        from pydantic_ai import UsageLimits, capture_run_messages
        from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
        from pydantic_ai.messages import ModelRequest

        prompt = prompt or _stage_prompt(stage, self.context())
        self.session.set_stage(stage)
        if self._traced is not None and hasattr(self._traced, "stage"):
            self._traced.stage = stage
        rec = StageRecord(stage=stage, model=self.model_label, started_at=time.time())
        trace_event(self.job, "director_stage", stage=stage, status="start", doc_version=self.session.doc.version,
                    model=self.model_label)
        limit = self.options.request_limits.get(stage, 60)
        history: list[ModelMessage] = list(self.history)

        def with_images(text: str) -> Any:
            if not attachments:
                return text
            imgs = self._images([(label, Path(p)) for label, p in attachments if Path(p).exists()])
            return [text, *imgs] if imgs else text

        user_prompt: Any = with_images(prompt) if history or not self.state.completed else \
            with_images(self._fresh_context_prompt(stage, prompt))
        transient = 0
        fresh_retry = False
        while True:
            captured: list[ModelMessage] = []
            try:
                with capture_run_messages() as captured:
                    result = self.agent.run_sync(user_prompt, message_history=history or None,
                                                 usage_limits=UsageLimits(request_limit=limit))
                self.history = list(result.all_messages())
                rec.requests = _usage_requests(result)
                break
            except (UsageLimitExceeded, UnexpectedModelBehavior) as e:
                if isinstance(e, UnexpectedModelBehavior) and not isinstance(e, UsageLimitExceeded) and \
                        _is_provider_error(e):
                    kind = map_provider_error(e, self.spec).kind
                    if self._switch_to_fallback(kind, e):
                        history, user_prompt = [], with_images(self._fresh_context_prompt(stage, prompt))
                        continue
                    raise DirectorError(map_provider_error(e, self.spec).message) from None
                # the guard or a model that will not finish: keep what was done, end the stage explicitly
                self.history = _complete_prefix(captured) if captured else history
                rec.forced = True
                rec.note = f"stage ended by the harness ({type(e).__name__}: {_clip(str(e), 200)})"
                break
            except Exception as e:
                err = map_provider_error(e, self.spec)
                trace_event(self.job, "director_error", stage=stage, kind=err.kind, error=err.message)
                if err.kind in _TRANSIENT and transient < self.options.transient_retries:
                    delay = self.options.backoff_s[min(transient, len(self.options.backoff_s) - 1)] \
                        if self.options.backoff_s else 0.0
                    transient += 1
                    self._sleep(delay)
                    if captured and isinstance(captured[-1], ModelRequest):
                        history, user_prompt = list(captured), None  # resume the exact conversation
                    continue
                if self._switch_to_fallback(err.kind, e):
                    history, user_prompt = [], with_images(self._fresh_context_prompt(stage, prompt))
                    continue
                if err.kind == "bad_request" and history and not fresh_retry:
                    # an old conversation the API no longer accepts (expired image files, thinking blocks bound to
                    # another model): continue once in a fresh context with the stage summaries
                    fresh_retry = True
                    self.history = []
                    self.session.new_conversation()
                    trace_event(self.job, "director_note", note="conversation rejected; continuing in a fresh context")
                    history, user_prompt = [], with_images(self._fresh_context_prompt(stage, prompt))
                    continue
                raise DirectorError(err.message) from None
        self._handoff_reason = None
        rec.ended_at = time.time()
        rec.doc_version = self.session.doc.version
        if self.session.stage_done:
            rec.summary = self.session.stage_summary or ""
            rec.questions = list(self.session.critic_questions)
        else:
            rec.forced = True
            rec.summary = rec.summary or "(stage ended without finish_stage)"
        trace_event(self.job, "director_stage", stage=stage, status="forced" if rec.forced else "done",
                    doc_version=rec.doc_version, requests=rec.requests, summary=_clip(rec.summary, 400),
                    note=rec.note or None)
        if record:
            self.state.completed = [r for r in self.state.completed if r.stage != stage] + [rec]
            self.state.model = self.model_label
            self.state.save(self.job)
        self._save_history()
        return rec

    def _switch_to_fallback(self, kind: str, exc: BaseException) -> bool:
        if self.using_fallback or kind not in _FALLBACK_KINDS:
            return False
        if self._fallback_spec() is None and self._fallback_override is None:
            return False
        if self._fallback_override is None and self._model_override is not None:
            return False  # tests with a scripted model and no scripted fallback
        self.using_fallback = True
        self.state.fallback_active = True
        self.state.save(self.job)
        self.session.image_uploader = None
        if self._fallback_override is None:
            from studio.agent.providers import make_image_uploader

            fb = self._fallback_spec()
            with contextlib.suppress(Exception):
                self.session.image_uploader = make_image_uploader(fb, job=self.job) if fb is not None else None
            self.session.upload_disabled = None
        self.session.new_conversation()
        self.history = []
        trace_event(self.job, "director_fallback", reason=kind, error=_clip(str(exc), 300),
                    to_model=self.model_label)
        self.agent = self._make_agent()
        return True

    # ------------------------------------------------------------------ public flows
    def run(self, stages: Sequence[str] = STAGE_ORDER) -> CutDocument:
        """Run every unfinished stage in order and return the document."""
        for stage in stages:
            if self.state.done(stage):
                continue
            self.run_stage(stage)
        return self.session.doc

    def revise(self, notes_text: str, *, base_version: int, round_no: int = 1, champion_render: Path | None = None,
               attachments: Sequence[tuple[str, Path]] = (), raw_prompt: bool = False) -> RevisionResult:
        """One champion-loop revision: branch from ``base_version`` (the champion), let the Director act on the
        critics' notes, and report whether anything that renders changed. ``champion_render`` (the render the critics
        judged) becomes ``view_frames(source='render')``'s source; ``attachments`` are the critics' sheets, shown with
        the notes. ``raw_prompt`` sends ``notes_text`` as the whole instruction (variant rounds)."""
        base = self.session.reload_doc(base_version)
        self._point_at_render(champion_render)
        self.session.base_doc = base
        self.session.critic_questions = []
        if self.options.fresh_rounds:
            self._start_fresh(f"render review round {round_no}: the edit is finished and rendered, and each round "
                              "starts from the summaries below")
        if raw_prompt:
            prompt = notes_text
        else:
            prompt = (
                f"RENDER REVIEW — round {round_no}. The champion render (document v{base_version}) was encoded, "
                "measured and critiqued. The document is back at the champion version"
                + (" and view_frames(..., source='render') shows that render (by word ID)" if champion_render
                   is not None else "")
                + (". The critics' own sheets are attached below" if attachments else "") + ".\n\n" + notes_text
                + "\n\nLoad critique. Decide every note: fix, or decline with a one-line reason. Defects (confirmed "
                "P0/P1) should be fixed; taste notes are options, never requirements. Prefer removing over adding, and "
                "fix at the right layer (a sagging middle is a trim, not an insert; an engine artefact is reported, "
                "never paid for with the creator's words). Every op family is open, including the story. The exit "
                "test follows what you touch: radio_test after a story change, compile_check after a cut change, "
                "check_seams when new seams appear, caption_preview after a caption or framing change. Then "
                "finish_stage with one line per note (fixed / declined and why), and questions_for_critics for "
                "anything you want the critics to check on the new render. If nothing is worth changing, finish_stage "
                "without ops: that is a valid verdict."
            )
        rec = self.run_stage("revise", prompt, record=False, attachments=attachments)
        doc = self.session.doc
        changed = render_relevant(doc) != render_relevant(base)
        return RevisionResult(doc=doc, changed=changed, summary=rec.summary, base_version=base_version)

    def _point_at_render(self, render_dir: Path | None) -> None:
        """Make ``render_dir`` (a full-quality render: its primary final and timeline survive pruning) the render
        view_frames(source='render') inspects."""
        if render_dir is None:
            return
        rd = Path(render_dir)
        finals = sorted(rd.glob("final_*.mp4"))
        primary = next((f for f in finals if f.stem != "final_nomusic"), finals[0] if finals else None)
        if primary is not None:
            self.session.last_render = primary
            tl = rd / "timeline.json"
            self.session.last_timeline = tl if tl.exists() else None

    def chat(self, instruction: str, *, base_version: int | None = None,
             champion_render: Path | None = None) -> dict[str, Any]:
        """Apply a creator instruction (the instruction is the brief for this change)."""
        base = self.session.reload_doc(base_version) if base_version is not None else self.session.doc
        before = base.version
        self._point_at_render(champion_render)
        self.session.base_doc = base
        if self.options.fresh_rounds:
            self._start_fresh("a creator's chat edit on the delivered cut; the edit starts from the summaries below")
        prompt = (
            f"CREATOR REQUEST (chat): \"{instruction}\"\n\n"
            "The creator's instruction is the brief for this change: carry it out cleanly and completely. Re-enter at "
            "the earliest stage it touches and repair anything that hangs off changed word IDs (captions, inserts, "
            "texts, SFX, music anchors). State a cost once in your summary if there is one; push back only when it "
            "would break a hard invariant. Every op family is open"
            + (" and view_frames(..., source='render') shows the current delivered render" if champion_render
               is not None else "")
            + ". The exit test follows what you touch: radio_test after a story change, compile_check after a cut "
            "change, check_seams when new seams appear, caption_preview after a caption or framing change. Then "
            "finish_stage summarizing exactly what changed."
        )
        rec = self.run_stage("chat", prompt, record=False)
        doc = self.session.doc
        results = [e for e in self.job.read_oplog() if int(e.get("version") or -1) > before]
        return {"doc": doc, "summary": rec.summary, "results": results,
                "changed": render_relevant(doc) != render_relevant(base), "base_version": before}

    # ------------------------------------------------------------------ creator media
    def add_creator_media(self, paths: Sequence[str | os.PathLike[str]]) -> list[str]:
        from studio.broll import sources

        out = []
        for p in paths:
            try:
                cands = sources.creator_media([p], job=self.job, description="")
            except Exception as e:
                trace_event(self.job, "director_note", note=f"creator media {Path(p).name} skipped: {e}")
                continue
            for c in cands:
                self._creator_candidates.append(c)
                out.append(c.id)
        return out

    # ------------------------------------------------------------------ b-roll pool persistence
    def _pool_path(self) -> Path:
        return self.job.assets_dir / "broll" / "pool.json"

    def _load_broll_pool(self) -> None:
        p = self._pool_path()
        if not p.exists():
            return
        with contextlib.suppress(Exception):
            from studio.broll.sources import BrollCandidate

            data = json.loads(p.read_text(encoding="utf-8"))
            for cid, rec in data.items():
                rec["candidate"] = BrollCandidate.from_dict(rec["candidate"], job=self.job)
                self.broll_pool[cid] = rec

    def _save_broll_pool(self) -> None:
        with contextlib.suppress(Exception):
            from studio.jobs import to_jsonable, write_json_atomic

            data = {cid: {**{k: v for k, v in rec.items() if k != "candidate"},
                          "candidate": rec["candidate"].to_dict()} for cid, rec in self.broll_pool.items()}
            write_json_atomic(self._pool_path(), to_jsonable(data))

    # ------------------------------------------------------------------ tool plumbing
    def _run_tool(self, name: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        s = self.session
        t0 = time.perf_counter()
        s.tool_calls += 1
        try:
            res = fn()
        except (KeyError, ValueError, LookupError, FileNotFoundError) as e:
            res = _err(e)
        except Exception as e:  # never kill the run on a tool bug
            res = f"ERROR: {name} failed internally ({type(e).__name__}: {_clip(str(e), 300)})"
        ok = not (isinstance(res, str) and res.startswith("ERROR:")) and not (
            isinstance(res, list) and res and isinstance(res[0], str) and res[0].startswith("ERROR:"))
        s.trace_tool(name, args, ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000), result=res,
                     error=None if ok else (res if isinstance(res, str) else res[0]))
        if isinstance(res, list) and len(res) == 1 and isinstance(res[0], str):
            return res[0]
        return res

    async def _arun(self, name: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(self._run_tool, name, args, fn)

    def _images(self, labelled: Sequence[tuple[str, Path]]) -> list[Any]:
        """``[label, image, …]`` sized for the Director's model (within the conversation's image budget)."""
        s = self.session
        caps = s.capabilities
        if not caps.vision:
            return []
        out: list[Any] = []
        edge = caps.image_edge_limit()
        for label, p in labelled:
            if s.images_sent + 1 > caps.max_images:
                out.append(f"(image budget reached; {label} not shown)")
                break
            data, mt, w, h = _prepare_image(Path(p), edge, caps.max_image_bytes, caps.max_image_tokens)
            with s._lock:
                s.images_sent += 1
            out.append(f"{label} [{w}x{h}]")
            out.append(s._image_part(data, mt, Path(p).stem))
        return out

    def _compile(self, doc: CutDocument | None = None) -> Timeline:
        from studio.compile.timeline import compile as compile_timeline

        return compile_timeline(doc or self.session.doc, self.index, job=self.job)

    # ------------------------------------------------------------------ tool implementations
    def t_finish_stage(self, summary: str, questions_for_critics: Sequence[str] | None = None) -> str:
        s = self.session
        stage = s.stage or "?"
        summary = " ".join(str(summary or "").split())
        if len(summary) < 8:
            return "NOT FINISHED: give a real summary (what you decided and why, by ID)."
        problems = self._exit_problems(stage)
        if problems:
            return "NOT FINISHED — the exit test is not met yet:\n" + "\n".join(f"- {p}" for p in problems)
        s.stage_done = True
        s.stage_summary = summary
        s.finished_version = s.doc.version
        s.critic_questions = [" ".join(str(q).split()) for q in (questions_for_critics or []) if str(q).strip()][:6]
        return (f"Stage {stage} finished at document v{s.doc.version}. Reply with one short line and stop; the next "
                "step follows.")

    def _exit_problems(self, stage: str) -> list[str]:
        from studio.doc.validate import validate_document

        s = self.session
        doc = s.doc
        out: list[str] = []
        if stage == "brief":
            b = doc.brief
            if b is None or not (b.goal.strip() or b.text.strip()):
                out.append("the brief is not written: meta_ops set_brief with at least the goal (the one idea), "
                           "the hook's promise and the rubric")
            elif not b.rubric:
                out.append("the brief has no rubric: add 6-10 binary questions the critics will check on the render")
            return out
        if stage != "brief" and not doc.segments:
            out.append("the story is empty: build it with cut_ops set_story")
            return out
        if stage == "story":
            if s.radio_sig != _story_sig(doc):
                out.append("run radio_test on the current story (it changed since the last radio test) and judge it")
            else:
                out.extend(self._radio_blockers())
        if stage == "fine_cut":
            if s.compile_sig != _cut_sig(doc):
                out.append("run compile_check on the current cut (pause targets asked → kept, seams)")
            if not s.seams_unavailable and s.seams_sig != _cut_sig(doc) and len(doc.segments) > 1:
                out.append("run check_seams on the current cut and look at the frames either side of every seam")
        if stage == "captions":
            out.extend(self._caption_look_needed(doc))
        if stage in ("revise", "chat") and s.base_doc is not None:
            base = s.base_doc
            if _story_sig(doc) != _story_sig(base) and s.radio_sig != _story_sig(doc):
                out.append("the story changed: run radio_test on it and judge it as a listener")
            if _cut_sig(doc) != _cut_sig(base) and s.compile_sig != _cut_sig(doc):
                out.append("the cut changed: run compile_check on it (pause targets asked → kept, seams)")
            if (_seam_set(doc) - _seam_set(base)) and not s.seams_unavailable and s.seams_sig != _cut_sig(doc):
                out.append("new seams: run check_seams and look at the frames either side of them")
            if _caption_sig(doc) != _caption_sig(base):
                out.extend(self._caption_look_needed(doc))
        try:
            errs = [f for f in validate_document(doc, self.index, self.job) if f.level == "error"]
        except Exception:
            errs = []
        if stage == "story":  # the radio blockers above already name each cut-off word at a join
            errs = [f for f in errs if f.code != "cutoff_at_join"]
        for f in errs[:8]:
            out.append(f"validation error {f.code}: {f.message}")
        return out

    def _caption_look_needed(self, doc: CutDocument) -> list[str]:
        s = self.session
        if s.caption_preview_unavailable or (doc.captions is not None and not doc.captions.enabled):
            return []
        if s.caption_preview_sig == _caption_sig(doc):
            return []
        return ["look at the rendered captions: call caption_preview (after your last caption or framing change) and "
                "judge size, place and the eye's path at phone scale"]

    # ---------------------------------------------------------------- radio test
    def _radio_blockers(self) -> list[str]:
        doc, ix = self.session.doc, self.index
        kept = doc.kept_word_ids(ix)
        out = []
        missing = [w for w in (*doc.pins.payoff_word_ids, *doc.pins.cta_word_ids) if w not in set(kept)]
        if missing:
            out.append(f"pinned payoff/CTA words are not in the story: {', '.join(missing)}")
        from studio.doc.validate import cutoffs_at_joins, describe_cutoff_at_join

        at_joins = cutoffs_at_joins(doc, ix)
        out.extend(describe_cutoff_at_join(ix, c) for c in at_joins[:8])
        joined = {c["word_id"] for c in at_joins}
        chopped = [w for w in kept if ix.word(w).truncated and w not in joined]
        if chopped:
            out.append("words the recording itself chops (a digital-silence dropout) are kept: "
                       + ", ".join(f"{w} {ix.word(w).display()!r}" for w in chopped[:8])
                       + " — the listener hears a chopped word (invariant 1 fails): cut them, and move the seam to a "
                         "clean clause edge nearby")
        return out

    def t_radio_test(self) -> str:
        s = self.session
        doc, ix = s.doc, self.index
        if not doc.segments:
            return "ERROR: the story is empty; build it with cut_ops set_story first."
        try:
            tl = self._compile()
            spans = {k: v for k, v in tl.word_map.items() if v is not None}
            total = float(tl.duration)
        except Exception as e:  # compile problems are reported, the radio test still runs on estimates
            tl, spans, total = None, {}, doc.estimated_duration_us(ix) / 1e6
            trace_event(self.job, "director_note", note=f"radio_test compile failed: {e}")
        kept = doc.kept_word_ids(ix)
        kept_set = set(kept)
        lines = [f"RADIO TEST — document v{doc.version}: the story with the picture off, in output order. Read it as a "
                 "listener who never heard the cut words.",
                 f"Length ≈ {total:.1f} s · {len(kept)} words · {len(doc.segments)} segments"]
        hook = [w for w in kept if w in spans and float(spans[w].out_start) < 3.0]
        if hook:
            lines.append(f"First 3 s (the hook, sound on or muted): \"{_words_text(ix, hook, 240)}\"")
        for k, seg in enumerate(doc.segments):
            ids = ix.word_ids(seg.from_word, seg.to_word)
            if tl is not None and ids[0] in spans and ids[-1] in spans:
                a, b = float(spans[ids[0]].out_start), float(spans[ids[-1]].out_end)
                when = f"{a:.1f}–{b:.1f} s ({b - a:.1f} s)"
            else:
                span = ix.word(ids[-1]).end_us - ix.word(ids[0]).start_us
                when = f"≈{span / seg.speed / 1e6:.1f} s"
            lines.append(f"{seg.id} {seg.from_word}-{seg.to_word} {when}: {_words_text(ix, ids, 700)}")
            if k + 1 < len(doc.segments):
                nxt = doc.segments[k + 1]
                a_w, b_w = ids[-1], nxt.from_word
                pa, pb = ix.word_pos(a_w), ix.word_pos(b_w)
                if pb == pa + 1:
                    continue
                gone = ix.word_ids(ix.words[pa + 1].id, ix.words[pb - 1].id) if pb > pa + 1 else []
                sa, sb = ix.word(a_w).sentence_id, ix.word(b_w).sentence_id
                ends = re.search(r"[.!?]$", ix.word(a_w).text)
                mid = "mid-sentence" if (sa == sb or not ends) else "between sentences"
                if pb < pa:
                    lines.append(f"   ‖ seam {seg.id}→{nxt.id}: REORDERED (jumps back in the take), {mid}")
                else:
                    gone_s = (ix.word(gone[-1]).end_us - ix.word(gone[0]).start_us) / 1e6 if gone else 0.0
                    lines.append(f"   ‖ seam {seg.id}→{nxt.id}: {mid}; removed {len(gone)} words ({gone_s:.1f} s): "
                                 f"\"{_words_text(ix, gone, 140)}\"")
        checks: list[str] = []
        for b in self._radio_blockers():
            checks.append(f"[blocker] {b}")
        for sent in ix.sentences:
            n = sum(1 for w in sent.word_ids if w in kept_set)
            if 0 < n < len(sent.word_ids):
                checks.append(f"[check] {sent.id} is only partly kept ({n}/{len(sent.word_ids)} words): does it still "
                              "read as a sentence?")
            if n and not sent.complete:
                checks.append(f"[check] {sent.id} is marked incomplete in the take (a false start?) and is kept")
        for c in ix.clusters:
            kept_takes = [sid for sid in c.sentence_ids
                          if sum(1 for w in ix.sentence(sid).word_ids if w in kept_set)
                          >= 0.5 * len(ix.sentence(sid).word_ids)]
            if len(kept_takes) > 1:
                checks.append(f"[check] retake cluster {c.id}: {len(kept_takes)} takes kept ({', '.join(kept_takes)}); "
                              "the listener hears the line twice")
        if not doc.pins.payoff_word_ids:
            checks.append("[check] no payoff word is pinned (meta pin kind payoff) — pins protect it from later ops")
        from studio.doc.validate import cutoffs_at_joins

        joined = {c["word_id"] for c in cutoffs_at_joins(doc, ix)}
        cut_offs = [w for w in kept if ix.word(w).kind == "cutoff" and w not in joined]
        if cut_offs:
            checks.append(f"[check] cut-off words kept inside continuous speech (the speaker's own stumble; keep or "
                          f"cut by taste): {', '.join(cut_offs[:10])}")
        fillers = [w for w in kept if ix.word(w).kind == "filler"]
        if fillers:
            checks.append(f"[info] {len(fillers)} fillers kept (decide them in the fine cut): "
                          f"{', '.join(fillers[:12])}")
        last = [w for w in kept if ix.word(w).kind != "event"]
        if last:
            lw = ix.word(last[-1])
            pinned = "CTA" if last[-1] in doc.pins.cta_word_ids else "payoff" if last[-1] in doc.pins.payoff_word_ids \
                else "not pinned"
            lines.append(f"Ends on {lw.id} \"{lw.text}\" ({pinned})")
        if doc.brief is not None and (doc.brief.goal or doc.brief.hook):
            lines.append(f"Brief: idea \"{_clip(doc.brief.goal, 200)}\"; hook promise \"{_clip(doc.brief.hook, 200)}\"")
        if checks:
            lines.append("Checks:\n" + "\n".join("  " + c for c in checks))
        lines.append("Pass when: it makes sense beat to beat, the first 3 s tell the topic, and the payoff delivers "
                     "the hook's promise. If it passes, finish_stage with that verdict; if not, fix the story and "
                     "re-run.")
        s.radio_sig = _story_sig(doc)
        return "\n".join(lines)

    # ---------------------------------------------------------------- compile check
    def t_compile_check(self) -> str:
        from studio.compile.timeline import piece_is_continuous

        s = self.session
        doc, ix = s.doc, self.index
        if not doc.segments:
            return "ERROR: the story is empty; nothing to compile."
        n_trace = len(self.job.read_trace())
        tl = self._compile()
        notes = [e.get("note") for e in self.job.read_trace()[n_trace:] if e.get("event") == "compile_note"]
        wm = tl.word_map
        kept = doc.kept_word_ids(ix)
        spoken = [w for w in kept if wm.get(w) is not None]
        lines = [f"COMPILED document v{doc.version}: {float(tl.duration):.2f} s, {tl.frame_count} frames @ "
                 f"{float(tl.fps):.3f} fps, {len(tl.segments)} pieces from {len(doc.segments)} segments."]
        if spoken:
            first, last = wm[spoken[0]], wm[spoken[-1]]
            lines.append(f"First word at {float(first.out_start):.2f} s; last word ends {float(last.out_end):.2f} s → "  # type: ignore[union-attr]
                         f"{float(tl.duration - last.out_end):.2f} s to the last frame.")  # type: ignore[union-attr]
        # pause targets asked → kept
        asked: list[str] = []
        for seg in doc.segments:
            for gid, ms in sorted(seg.gap_overrides.items()):
                try:
                    g = ix.gap(gid)
                except KeyError:
                    continue
                a = wm.get(g.after_word_id or "")
                b = wm.get(g.before_word_id or "")
                if a is None or b is None:
                    asked.append(f"{gid}: asked {ms} ms → not played (a side is cut)")
                    continue
                kept_ms = float(b.out_start - a.out_end) * 1000.0
                why = ""
                if kept_ms > ms + 40:
                    why = " (kept longer: " + ("a breath is kept whole — remove the breath with a shorter target or "
                                               "accept the pause" if (g.has_breath or g.kind == "breath")
                                               else "pads/frame grid or the pause cannot shrink further") + ")"
                asked.append(f"{gid} ({g.duration_ms:.0f} ms measured{', breath' if g.has_breath else ''}): asked {ms} "
                             f"ms → kept {kept_ms:.0f} ms{why}")
        if asked:
            lines.append("Pauses you set (asked → kept, output time):\n" + "\n".join("  " + x for x in asked))
        long_kept = []
        seg_of = s._kept_map()
        for g in ix.gaps:
            a_id, b_id = g.after_word_id, g.before_word_id
            if a_id is None or b_id is None or seg_of.get(a_id) is None or seg_of.get(a_id) != seg_of.get(b_id):
                continue
            a, b = wm.get(a_id), wm.get(b_id)
            if a is None or b is None:
                continue
            gap_out = float(b.out_start - a.out_end)
            if gap_out >= 0.7:
                long_kept.append(f"{g.id} {gap_out * 1000:.0f} ms")
        if long_kept:
            lines.append("Long pauses still playing (≥ 700 ms): " + ", ".join(long_kept[:20]))
        # seams
        seams = []
        for k in range(1, len(tl.segments)):
            A, B = tl.segments[k - 1], tl.segments[k]
            if piece_is_continuous(A, B):
                if A.seg_id != B.seg_id:
                    seams.append(f"  {A.seg_id}→{B.seg_id} at {float(B.out_start):.2f} s: continuous "
                                 "(picture change only)")
                continue
            lw = A.word_ids[-1] if A.word_ids else "?"
            rw = B.word_ids[0] if B.word_ids else "?"
            kind = B.seam_in.kind + (f" {B.seam_in.lead_ms} ms" if B.seam_in.lead_ms else "")
            same = A.seg_id == B.seg_id
            what = "pause trim" if same else "cut"
            lt = ix.word(lw).text if ix.has_word(lw) else ""
            rt = ix.word(rw).text if ix.has_word(rw) else ""
            seams.append(f"  {A.seg_id}→{B.seg_id} at {float(B.out_start):.2f} s ({what}, {kind}): …{lw} \"{lt}\" | "
                         f"{rw} \"{rt}\"…")
        lines.append(f"Seams ({len(seams)}):" + ("\n" + "\n".join(seams) if seams else " none"))
        # framing asked → rendered, with the picture's upsampling (output px per source px) and how long it holds
        from studio.compile.timeline import base_window

        fr = []
        src_w, src_h = ix.media.width, ix.media.height
        _bw, bh = base_window(src_w, src_h, tl.width / tl.height)
        base_up = tl.height / max(1.0, bh * src_h)  # output px per source px at scale 1
        total = float(tl.duration) or 1.0
        for seg in doc.segments:
            want = seg.framing.scale if seg.framing is not None else (1.3 if seg.seam_in.kind == "punch" else None)
            pieces_ = [p for p in tl.segments if p.seg_id == seg.id]
            got = max((key.scale for p in pieces_ for key in p.framing), default=1.0)
            up = base_up * got
            held = sum(float(p.out_end - p.out_start) for p in pieces_)
            if want is None and up <= 1.2 + 1e-6:
                continue
            flag = ""
            if want is not None and got + 0.01 < want:
                flag = (" — CLAMPED by the face-safe ceiling (more would look soft); a punch below 1.25x may not hide "
                        "a pose jump: consider a cutaway, a J/L into a pause, or no punch")
            if up > 1.2 + 1e-6:
                flag += (f" — SOFT: the picture is upsampled x{up:.2f} (clean up to x1.2) for {held:.1f} s "
                         f"({held / total * 100:.0f}% of the video)" + ("; punch for the beat, then return to 1.0"
                                                                       if want is not None and got > 1.0 else ""))
            fr.append(f"  {seg.id}: " + (f"asked x{want:.2f} → rendered x{got:.2f}" if want is not None else
                                         f"base framing x{got:.2f}") + flag)
        if fr:
            lines.append(f"Framing (asked → rendered; source {src_w}x{src_h}, base upsampling x{base_up:.2f}):\n"
                         + "\n".join(fr))
        if tl.inserts:
            lines.append("Inserts:\n" + "\n".join(
                f"  {i.insert_id} {i.mode} {float(i.out_start):.2f}–{float(i.out_end):.2f} s "
                f"({float(i.out_end - i.out_start):.2f} s)" for i in tl.inserts))
        if tl.texts:
            tx = []
            for t in tl.texts:
                d = float(t.out_end - t.out_start)
                need = 0.3 * len(t.text.split()) + (1.0 if t.kind == "hook_title" else 0.5)
                tx.append(f"  {t.text_id} {t.kind} {float(t.out_start):.2f}–{float(t.out_end):.2f} s ({d:.2f} s; "
                          f"reading needs ≈{need:.1f} s{' — SHORT' if d + 0.05 < need else ''}): "
                          f"\"{_clip(t.text, 60)}\"")
            lines.append("Texts:\n" + "\n".join(tx))
        if tl.captions:
            fast, short = [], []
            cps_all = []
            for p in tl.captions:
                text = p.text or " ".join(w.text for w in p.words)
                d = float(p.out_end - p.out_start)
                cps = len(text) / d if d > 0 else 99.0
                cps_all.append(cps)
                if cps > 20.0:
                    fast.append(f"{p.page_id} \"{_clip(text, 30)}\" {cps:.0f} CPS ({d:.2f} s)")
                if d < 0.5 and len(p.words) != 1:
                    short.append(f"{p.page_id} \"{_clip(text, 30)}\" {d:.2f} s")
            lines.append(f"Captions: {len(tl.captions)} pages" + ("" if doc.captions is not None and doc.captions.pages
                                                                  else " (automatic paging)")
                         + f", reading speed max {max(cps_all):.0f} CPS, "
                           f"median {sorted(cps_all)[len(cps_all) // 2]:.0f} CPS")
            lines.extend(_caption_layout_lines(tl, doc, ix))
            if fast:
                lines.append("  over 20 CPS: " + "; ".join(fast[:12]) + " — re-page (merge with a neighbour), never "
                             "delete words")
            if short:
                lines.append("  under 0.5 s on screen: " + "; ".join(short[:12]))
        elif doc.captions is None or doc.captions.enabled:
            lines.append("Captions: none compiled")
        else:
            lines.append("Captions: off")
        if tl.sfx:
            lines.append("SFX: " + ", ".join(f"{x.sfx_id} {x.kind} at {float(x.out_t):.2f} s" for x in tl.sfx))
        if tl.music is not None:
            lines.append(f"Music: {float(tl.music.out_start):.2f}–{float(tl.music.out_end):.2f} s at "
                         f"{tl.music.level_lu_under_speech:+.0f} LU under speech")
        if notes:
            lines.append("Compiler notes:\n" + "\n".join("  " + _clip(n, 300) for n in notes[:12] if n))
        try:
            from studio.doc.validate import validate_document

            fs = [f for f in validate_document(doc, ix, self.job) if f.level in ("error", "warning")]
            if fs:
                lines.append("Checks:\n" + "\n".join(f"  [{f.level}] {f.code}: {_clip(f.message, 200)}"
                                                       for f in fs[:12]))
        except Exception:
            pass
        s.compile_sig = _cut_sig(doc)
        return "\n".join(lines)

    # ---------------------------------------------------------------- seams
    def t_check_seams(self, max_seams: int = 16) -> Any:
        from studio.compile.timeline import piece_is_continuous
        from studio.perception import frames as fr

        s = self.session
        doc, ix = s.doc, self.index
        if len(doc.segments) < 2 and not any(seg.gap_overrides for seg in doc.segments):
            s.seams_sig = _cut_sig(doc)
            return "No seams: the story is one continuous segment."
        tl = self._compile()
        pairs = []
        for k in range(1, len(tl.segments)):
            A, B = tl.segments[k - 1], tl.segments[k]
            if piece_is_continuous(A, B):
                continue
            pairs.append((A, B))
        if not pairs:
            s.seams_sig = _cut_sig(doc)
            return "No true seams (only continuous picture changes)."
        pairs = pairs[: max(1, min(int(max_seams), 24))]
        try:
            src = "proxy"
            fr.resolve_source(self.job, src)
        except (FileNotFoundError, ValueError):
            s.seams_unavailable = True
            s.seams_sig = _cut_sig(doc)
            return ("Seam frames unavailable (no video for this job); judge the seams from get_visual_events and "
                    "get_words instead.")
        fus = round(1e6 / float(tl.fps))
        items = []
        meas = ["Measured at each seam (nearest 10 fps sample): face centre/width OUT→IN, eyes open, mouth open, "
                "blink:"]
        samples = ix.visual.samples if ix.visual is not None else []
        from bisect import bisect_left

        times = [x.t_us for x in samples]

        def sample(t: int) -> Any:
            if not samples:
                return None
            i = bisect_left(times, t)
            cands = [j for j in (i - 1, i) if 0 <= j < len(samples)]
            return samples[min(cands, key=lambda j: abs(times[j] - t))]

        events = ix.visual.events if ix.visual is not None else []
        blinks = [(e.start_us, e.end_us) for e in events if e.kind == "blink"]
        audio_facts, audio_head = self._seam_audio_facts(tl, pairs)
        for k, (A, B) in enumerate(pairs, start=1):
            t_out = max(0, A.src_out_us - fus)
            t_in = B.src_in_us
            lw = A.word_ids[-1] if A.word_ids else ""
            rw = B.word_ids[0] if B.word_ids else ""
            items.append(fr.FrameRequest(t_us=t_out, label=f"seam {k} OUT …{lw} {ix.word(lw).text if lw else ''}"))
            items.append(fr.FrameRequest(t_us=t_in, label=f"seam {k} IN {rw} {ix.word(rw).text if rw else ''}…"))
            so, si = sample(t_out), sample(t_in)
            desc = f"  seam {k} {A.seg_id}→{B.seg_id} ({B.seam_in.kind}) …{lw}|{rw}…: "
            if so is not None and si is not None and so.face_box is not None and si.face_box is not None:
                shift = math.hypot(si.face_box.cx - so.face_box.cx, si.face_box.cy - so.face_box.cy) * 100
                ratio = si.face_box.w / so.face_box.w if so.face_box.w else 1.0
                risk = ""
                if 1.0 < shift < 8.0 and 0.9 < ratio < 1.12 and B.seam_in.kind in ("cut", "jcut", "lcut"):
                    risk = " → small same-size shift: a visible jump cut unless it sits between thoughts"
                fo, fi = so.face_box, si.face_box
                desc += (f"face ({fo.cx:.2f},{fo.cy:.2f}) w{fo.w:.2f} → ({fi.cx:.2f},{fi.cy:.2f}) w{fi.w:.2f}, "
                         f"shift {shift:.1f}% scale ×{ratio:.2f}{risk}")

                def na(v: Any) -> str:
                    return "n/a" if v is None else f"{v:.2f}"

                desc += f"; eyes {na(so.eyes_open)}→{na(si.eyes_open)}; mouth {na(so.mouth_open)}→{na(si.mouth_open)}"
            else:
                desc += "face not measured on both sides"
            if any(a <= t_in < b or a <= t_out < b for a, b in blinks):
                desc += "; a blink touches the cut (hides it)"
            if audio_facts.get(k - 1):
                desc += "\n      audio: " + audio_facts[k - 1]
            meas.append(desc)
        # two seams per row (OUT, IN, OUT, IN)
        sheet = fr.contact_sheet(self.job, items, index=ix, source="proxy", columns=4,
                                 title=f"Seams of document v{doc.version}: last OUT frame and first IN frame of each "
                                       "cut (source frames, before framing)")
        s.seams_sig = _cut_sig(doc)
        out: list[Any] = [f"{len(pairs)} seam(s). Look for a visible jump inside a thought, eyes shut, a mouth caught "
                          "mid-word, or a head snap; fix with a seam move, J/L, punch or cutaway (or accept it between "
                          "thoughts)."]
        out.extend(self._images([("Seam sheet", sheet)]))
        out.append("\n".join(meas + ([audio_head] if audio_head else [])))
        return out

    def _seam_audio_facts(self, tl: Any, pairs: list[tuple[Any, Any]]) -> tuple[dict[int, str], str]:
        """Per seam (index into ``pairs``): what the ear gets — the pause the compile actually keeps, a clause or
        beat edge (from punctuation), words the recording itself chops, untranscribed sound in the pads, and the
        click detector on a dialogue-only assembly (no video render: works when previews are blocked). Plus a
        line for hard stops at recording dropouts elsewhere in kept audio."""
        from studio.compile.audio import assemble_dialogue, soften_dropout_edges
        from studio.qa import metrics as M

        ix = self.index
        facts: dict[int, str] = {}
        try:
            import soundfile as sf

            x, sr = sf.read(str(self.job.audio_path), dtype="float32", always_2d=True)
            x = x.mean(axis=1)
            x = soften_dropout_edges(x, sr, ix)
            if sr != tl.sample_rate:
                raise ValueError("sample rate mismatch")
            dia = assemble_dialogue(tl, x, sr, index=ix).audio
        except Exception as e:  # audio facts are advisory: frames still come back
            return {}, f"(seam audio audition unavailable: {type(e).__name__})"
        times = [M._audio_seam_t(B) for _A, B in pairs]
        labels = [(A.word_ids[-1] if A.word_ids else None, B.word_ids[0] if B.word_ids else None) for A, B in pairs]
        src = [(round(A.audio_src_out_us * sr / 1e6), round(B.audio_src_in_us * sr / 1e6)) for A, B in pairs]
        try:
            clicks = M.detect_seam_clicks(dia, sr, times, labels=labels, source=x, source_samples=src)
        except Exception:
            clicks = []
        integ = M.word_integrity(tl, ix)
        wm = tl.word_map
        for k, (_A, _B) in enumerate(pairs):
            lw, rw = labels[k]
            bits: list[str] = []
            if lw and rw and wm.get(lw) is not None and wm.get(rw) is not None:
                pause = float(wm[rw].out_start - wm[lw].out_end) * 1000
                bits.append(f"compiled pause {pause:.0f} ms between the words")
            if lw and rw and ix.has_word(lw) and ix.has_word(rw):
                l_end = M._clause_end(ix.word(lw).text)
                r_start = M._punct_start(ix, rw)
                bits.append("between sentences" if M._SENTENCE_END.search(ix.word(lw).text.strip()) else
                            "at a clause edge" if (l_end or r_start) else "MID-CLAUSE (no punctuation either side)")
            for w in (lw, rw):
                if w and ix.has_word(w) and ix.word(w).truncated:
                    bits.append(f"{w} {ix.word(w).display()!r} is CHOPPED by a recording dropout")
                elif w and ix.has_word(w) and ix.word(w).kind == "cutoff":
                    bits.append(f"{w} {ix.word(w).display()!r} is a CUT-OFF fragment at the join (invariant 1 fails: "
                                "move the seam off it)")
            leaks = [d for d in integ.sound_leaks if d.get("before_word") == rw or d.get("after_word") == lw]
            for d in leaks:
                bits.append(f"{d['overlap_ms']:.0f} ms of untranscribed sound ({d['gap_id']}) "
                            + ("sits in the pad (muted under room tone: a fragment of a lost word — the seam is on "
                               "damaged material, consider moving it)" if d.get("muted")
                               else "plays between the words"))
            c = clicks[k] if k < len(clicks) else None
            if c is not None and c.margin_db is not None:
                bits.append(("CLICK" if c.click else "no click") + f" (detector margin {c.margin_db:+.1f} dB"
                            + (", inherited from the source" if c.rule == "inherited" else "") + ")")
            facts[k] = "; ".join(bits)
        head = ""
        edges = M.dropout_edges_in_output(tl, ix)
        if edges:
            try:
                ec = M.detect_seam_clicks(dia, sr, [t for t, _l, _r in edges], labels=[(a, b) for _t, a, b in edges])
            except Exception:
                ec = []
            head = ("Hard stops/starts at recording dropouts inside the kept audio (the source goes to digital "
                    "silence mid-sound; not a seam, but the ear hears it): " + "; ".join(
                        f"{float(t):.2f} s after {a or '…'} before {b or '…'}"
                        + (" CLICK" if k < len(ec) and ec[k].click else "") for k, (t, a, b) in enumerate(edges)))
        return facts, head

    # ---------------------------------------------------------------- b-roll
    def _anchor_span_s(self, a: str, b: str) -> tuple[float | None, float | None, float | None]:
        """Output (start, end) of an anchor word range from a compile, plus a source time for context."""
        try:
            tl = self._compile()
        except Exception:
            return None, None, None
        sa, sb = tl.word_map.get(a), tl.word_map.get(b)
        if sa is None or sb is None:
            return None, None, None
        return float(sa.out_start), float(sb.out_end), float(tl.fps)

    def t_broll_search(self, need: str, queries: list[str], anchor_from_word: str, anchor_to_word: str,
                       mode: str = "full", expected_false: list[str] | None = None, kinds: list[str] | None = None,
                       allow_faces: bool = True) -> Any:
        from studio.broll import rank, sources

        s = self.session
        ix = self.index
        kept = set(s.doc.kept_word_ids(ix))
        for w in (anchor_from_word, anchor_to_word):
            ix.word(w)
            if w not in kept:
                return f"ERROR: {w} is not in the story; anchor inserts on kept words."
        if mode not in ("full", "split_top", "split_bottom", "pip"):
            return "ERROR: mode must be full, split_top, split_bottom or pip (cards go through inserts_ops)"
        a_s, b_s, fps = self._anchor_span_s(anchor_from_word, anchor_to_word)
        dur = (b_s - a_s) if a_s is not None and b_s is not None else None
        queries = [q.strip() for q in (queries or []) if q and q.strip()][:10] or [need]
        cands: list[Any] = []
        notes: list[str] = []
        if self.settings.has_key("pexels"):
            try:
                cands.extend(sources.search_many(queries, kinds=tuple(kinds or ("video", "image")), per_query=12,
                                                 max_total=150, settings=self.settings))
            except Exception as e:
                notes.append(f"Pexels search failed: {_clip(str(e), 160)}")
        else:
            notes.append("Pexels unavailable (no key)")
        cands.extend(self._creator_candidates)
        if not cands:
            return ("No b-roll candidates (" + ("; ".join(notes) or "no sources") + "). Decide none, a punch-in or a "
                    "designed card (inserts_ops add_insert with card).")
        nd = rank.Need(text=need, queries=tuple(queries), expected_false=tuple(expected_false or ()), mode=mode,
                       duration_s=dur, allow_faces=allow_faces, output_fps=fps)
        ref = self.job if self.job.mezz_path.exists() else None
        ranked = rank.rank_candidates(cands, need=nd, job=self.job, top_k=20, include_rejected=True,
                                      a_roll_reference=ref)
        ok = [r for r in ranked if not r[2].get("rejected_by")]
        rejected = [r for r in ranked if r[2].get("rejected_by")]
        if not ok:
            why: dict[str, int] = {}
            for _c, _sc, d in rejected:
                for g in d.get("rejected_by") or ["?"]:
                    why[str(g)] = why.get(str(g), 0) + 1
            return (f"Nothing cleared the gates ({len(cands)} candidates; rejected by: "
                    + ", ".join(f"{k} ×{n}" for k, n in sorted(why.items(), key=lambda kv: -kv[1])) + "). "
                    "'none' is the answer unless a designed card does the job.")
        top = ok[:6]
        self._broll_sheets += 1
        out_path = self.job.critique_dir / "broll" / f"sheet_{self._broll_sheets:02d}.png"
        ctx = None
        if a_s is not None:
            w0, w1 = ix.word(anchor_from_word), ix.word(anchor_to_word)
            src = str(self.job.mezz_path if self.job.mezz_path.exists() else self.job.proxy_path)
            if Path(src).exists():
                ctx = [(src, max(0.0, w0.start_us / 1e6 - 0.3)), (src, w1.end_us / 1e6 + 0.2)]
        sheet = rank.contact_sheet_for_judgment(top, need=nd, out_path=out_path, job=self.job, context=need,
                                                a_roll_reference=ref, a_roll_context=ctx)
        for c, score, diag in top:
            self.broll_pool[c.id] = {"candidate": c, "score": float(score), "need": need, "mode": mode,
                                     "anchor_from_word": anchor_from_word, "anchor_to_word": anchor_to_word,
                                     "diag": {k: v for k, v in diag.items() if k in ("rejected_by", "notes", "score",
                                                                                     "use_ms", "warnings")}}
        self._save_broll_pool()
        lines = [f"{len(cands)} candidates → {len(ok)} cleared the gates; the best {len(top)} are on the sheet"
                 + (f" ({'; '.join(notes)})" if notes else "") + "."]
        for c, score, diag in top:
            lic = c.licence.get("name", "?") if isinstance(c.licence, dict) else "?"
            warn = diag.get("warnings") or diag.get("notes") or ""
            lines.append(f"  {c.id}: {c.summary()} · score {score:.2f} · licence {lic}"
                         + (f" · {_clip(json.dumps(warn, default=str), 160)}" if warn else ""))
        lines.append(sheet.prompt)
        lines.append("To use one: broll_use(candidate_id, anchor_from_word, anchor_to_word, job, scores={subject, "
                     "readable, clean, look, sharp}); every score must be >= 4. Otherwise decide none.")
        out: list[Any] = ["\n".join(lines)]
        out.extend(self._images([(f"B-roll judgment sheet {i + 1}/{len(sheet.paths)}", p)
                                 for i, p in enumerate(sheet.paths)]))
        return out

    def t_broll_use(self, candidate_id: str, anchor_from_word: str, anchor_to_word: str, job: str,
                    scores: dict[str, int], mode: str | None = None, audio: str = "voice_only",
                    transition_in: str = "cut", transition_out: str = "cut") -> str:
        from studio.broll import rank, sources

        rec = self.broll_pool.get(candidate_id)
        if rec is None:
            known = ", ".join(sorted(self.broll_pool)[:20]) or "none yet (call broll_search)"
            return f"ERROR: unknown candidate {candidate_id!r}; candidates on your sheets: {known}"
        j = rank.parse_judgment({"scores": {candidate_id: scores or {}}, "pick": candidate_id}, [candidate_id])
        if j.pick is None:
            return ("NOT USED: " + "; ".join(j.errors or ["below the bar"]) + ". The bar is every rubric item "
                    f"({', '.join(_RUBRIC_ITEMS)}) >= 4. Decide none, or judge another candidate.")
        c = rec["candidate"]
        asset = sources.download(c, self.job, settings=self.settings)
        tr_in = {"kind": transition_in, "ms": 0 if transition_in == "cut" else 200}
        tr_out = {"kind": transition_out, "ms": 0 if transition_out == "cut" else 200}
        op = {"op": "add_insert", "anchor_from_word": anchor_from_word, "anchor_to_word": anchor_to_word,
              "mode": mode or rec.get("mode") or "full", "asset_id": asset.id, "job": job, "audio": audio,
              "transition_in": tr_in, "transition_out": tr_out,
              "note": f"b-roll bar scores {json.dumps(scores)}; need: {_clip(rec.get('need', ''), 120)}"}
        res = self.session.apply_ops([op])
        lic = asset.licence.name if asset.licence else "?"
        return f"Registered {asset.id} (licence: {lic}).\n" + res.text

    def t_broll_screenshot(self, url: str, description: str, selector: str | None = None) -> Any:
        from studio.broll import sources

        try:
            c = sources.screenshot(url, job=self.job, selector=selector, description=description,
                                   settings=self.settings)
        except Exception as e:
            return f"Capture unavailable ({type(e).__name__}: {_clip(str(e), 200)}). Use stock, a card, or none."
        self.broll_pool[c.id] = {"candidate": c, "score": 0.0, "need": description, "mode": "split_top",
                                 "anchor_from_word": "", "anchor_to_word": "", "diag": {}}
        self._save_broll_pool()
        out: list[Any] = [f"Captured {c.id}: {c.summary()}. Judge it (all five rubric items >= 4) before broll_use."]
        if c.local_path:
            out.extend(self._images([(f"Capture {c.id}", Path(c.local_path))]))
        return out

    def t_broll_generate(self, prompt: str, style: str = "stylized") -> Any:
        from studio.broll import sources

        if not self.settings.has_key("higgsfield"):
            return "Generated stills are unavailable (no Higgsfield key). Use stock, creator media, a card, or none."
        try:
            c = sources.generate_still(prompt, job=self.job, style=style, settings=self.settings)
        except Exception as e:
            return (f"Generation unavailable ({type(e).__name__}: {_clip(str(e), 200)}). Use stock, creator media, a "
                    "designed card, or none.")
        self.broll_pool[c.id] = {"candidate": c, "score": 0.0, "need": prompt, "mode": "full",
                                 "anchor_from_word": "", "anchor_to_word": "", "diag": {"ai_generated": True}}
        self._save_broll_pool()
        out: list[Any] = [f"Generated {c.id} (AI imagery: disclosure may be required). Judge it before broll_use."]
        if c.local_path:
            out.extend(self._images([(f"Generated {c.id}", Path(c.local_path))]))
        return out

    # ---------------------------------------------------------------- captions
    def t_auto_captions(self, apply: bool = True, font: str | None = None, size_px: int | None = None,
                        animation: str | None = None, case: str | None = None, highlight_color: str | None = None,
                        max_words_per_page: int | None = None, position: str | None = None) -> str:
        from studio.compile import captions as cap
        from studio.doc.model import CaptionStyle

        s = self.session
        doc = s.doc
        if not doc.segments:
            return "ERROR: the story is empty."
        base = doc.captions.style if doc.captions is not None else CaptionStyle()
        upd = {k: v for k, v in {"font": font, "size_px": size_px, "animation": animation, "case": case,
                                 "highlight_color": highlight_color,
                                 "max_words_per_page": max_words_per_page}.items() if v is not None}
        style = CaptionStyle.model_validate({**base.model_dump(), **upd})
        tl = self._compile()
        plan = cap.auto_caption_plan(doc, self.index, style=style, timeline=tl)
        if position:
            plan = plan.model_copy(update={"position": position})
        text = ""
        if apply:
            res = s.apply_ops([{"op": "set_captions", "plan": plan.model_dump(mode="json")}])
            text = res.text.splitlines()[0] + "\n"
            if not res.applied:
                return text + res.text
            tl = self._compile()
        rows = []
        for p in tl.captions:
            t = p.text or " ".join(w.text for w in p.words)
            d = float(p.out_end - p.out_start)
            cps = len(t) / d if d > 0 else 99.0
            emph = [w.word_id for w in p.words if w.emphasis]
            flag = " FAST" if cps > 20 else ""
            flag += " SHORT" if d < 0.5 and len(p.words) > 1 else ""
            fit = cap.caption_fit(p.style, t, tl.width)
            px = fit.size_px * 1080.0 / tl.width
            where = f" {px:.0f}px{f' x{fit.lines} lines' if fit.lines > 1 else ''} {p.placement or ''} y{p.y_norm:.2f}"
            rows.append(f"  {p.page_id} {float(p.out_start):.2f}+{d:.2f}s {cps:4.0f} CPS{flag}{where}: "
                        f"\"{fit.display if fit.lines > 1 else t}\""
                        + (f" accent {','.join(emph)}" if emph else ""))
        head = (f"{len(tl.captions)} caption pages ({style.font} {style.weight} {style.size_px}px {style.animation}, "
                f"{'applied' if apply else 'proposed, not applied'}):")
        summary = _caption_layout_lines(tl, s.doc, self.index) if apply and tl.captions else []
        return text + head + "\n" + "\n".join(rows[:120]) + ("\n" + "\n".join(summary) if summary else "")

    def t_caption_preview(self, pages: Sequence[str] | None = None) -> Any:
        from studio.compile import captions as cap

        s = self.session
        doc = s.doc
        if not doc.segments:
            return "ERROR: the story is empty."
        tl = self._compile()
        if not tl.captions:
            s.caption_preview_sig = _caption_sig(doc)
            return "No caption pages compiled (captions are off or empty): nothing to look at."
        platforms = [d.platform for d in doc.deliverables] or list(self.platforms)
        plat: Any = platforms[0] if len(platforms) == 1 else platforms
        geo = cap.caption_geometry(tl, self.index, platform=plat, settings=self.settings,
                                   style_px=doc.captions.style.size_px if doc.captions is not None else None)
        rows = {r["page"]: r for r in geo.get("pages", [])}
        by_id = {p.page_id: p for p in tl.captions}
        want = [str(x).strip() for x in (pages or []) if str(x).strip()]
        unknown = [x for x in want if x not in by_id]
        chosen: list[Any] = []

        def add(pg: Any) -> None:
            if pg is not None and pg.page_id not in {c.page_id for c in chosen} and len(chosen) < 6:
                chosen.append(pg)

        for x in want:
            add(by_id.get(x))
        add(tl.captions[0])
        add(min(tl.captions, key=lambda p: rows.get(p.page_id, {}).get("size_px", 1e9)))
        for pg in tl.captions:
            if rows.get(pg.page_id, {}).get("relation") not in ("below_chin", "no_face", None) or \
                    rows.get(pg.page_id, {}).get("into_ui_px", 0) > 0.5:
                add(pg)
                break
        add(tl.captions[len(tl.captions) // 2])
        add(tl.captions[-1])
        fps = Fraction(tl.fps)
        frames = {}
        for pg in chosen:
            a = frame_index_floor(pg.out_start, fps)
            b = max(a + 1, frame_index_floor(pg.out_end, fps))
            frames[pg.page_id] = min(b - 1, a + min(8, max(0, (b - a) // 2)))
        try:
            sheet = _caption_preview_sheet(self.job, self.index, tl, chosen, frames, platforms, rows,
                                           settings=self.settings, version=doc.version)
        except Exception as e:  # no picture to show (no video yet): the exit test does not wait on a broken tool
            s.caption_preview_unavailable = True
            s.caption_preview_sig = _caption_sig(doc)
            return (f"Caption preview unavailable ({type(e).__name__}: {_clip(str(e), 200)}). Judge from the "
                    "measurements:\n" + cap.caption_geometry_text(geo))
        s.caption_preview_sig = _caption_sig(doc)
        lines = [f"CAPTION PREVIEW of document v{doc.version} ({sheet.stem.split('_')[-1]} overlay stills): "
                 f"{len(chosen)} pages at their settled frame on the framed picture, under the "
                 f"{'/'.join(platforms)} UI mask, each tile 540 px wide (about a phone's width).",
                 cap.caption_geometry_text(geo)]
        for pg in chosen:
            r = rows.get(pg.page_id, {})
            lines.append(f"  {pg.page_id} \"{_clip(r.get('text', ''), 40)}\": {r.get('size_px', '?')} px font "
                         f"(cap {r.get('cap_height_px', '?')} px), top y{r.get('top_px', 0):.0f}, "
                         f"{r.get('relation', '?')}" + (f" gap {r['chin_gap_px']:.0f} px" if "chin_gap_px" in r else "")
                         + (f", {r['into_ui_px']:.0f} px into the strict band" if r.get("into_ui_px", 0) > 0.5
                            else ""))
        if unknown:
            lines.append("Unknown page IDs ignored: " + ", ".join(unknown[:8]))
        out: list[Any] = ["\n".join(lines)]
        out.extend(self._images([("Caption preview (phone scale, UI mask)", sheet)]))
        return out

    # ---------------------------------------------------------------- sound
    def t_voice_plan(self, apply: bool = False) -> str:
        from studio.audio import voice

        if not self.job.audio_path.exists():
            return "ERROR: no dialogue audio for this job (media/audio.wav); keep the default chain."
        spec = voice.plan_voice_chain(self.job, self.index)
        text = "Measured voice chain:\n" + json.dumps(spec.model_dump(mode="json"), indent=1)
        if apply:
            res = self.session.apply_ops([{"op": "set_voice_chain", "spec": spec.model_dump(mode="json")}])
            text += "\n" + res.text
        else:
            text += ("\nApply it with audio_ops set_voice_chain (edit any value you disagree with) or "
                     "voice_plan(apply=true).")
        return text

    def t_music_options(self, n: int = 2, mood: str | None = None, prompt: str | None = None) -> str:
        from studio.audio import music
        from studio.doc.model import MusicSpec

        if not self.settings.has_key("elevenlabs"):
            return ("Music generation is unavailable (no ElevenLabs key). The answer is no music (audio_ops set_music "
                    "null); the no-music master ships anyway.")
        doc = self.session.doc
        tl = self._compile()
        kept = [w for w in doc.kept_word_ids(self.index) if tl.word_map.get(w) is not None]
        if not kept:
            return "ERROR: nothing compiled."
        dur = float(tl.duration)
        end_anchor = float(tl.word_map[kept[-1]].out_start)  # type: ignore[union-attr]
        hits = [float(tl.word_map[w].out_start) for w in doc.pins.payoff_word_ids  # type: ignore[union-attr]
                if tl.word_map.get(w) is not None][:1]
        tmp = doc.model_copy(deep=True)
        tmp.audio.music = MusicSpec(source="elevenlabs", mood=mood, prompt=prompt)
        specs = music.find_music(self.job, tmp, duration_s=dur, settings=self.settings, n=max(1, min(int(n), 3)),
                                 index=self.index, end_anchor_s=end_anchor, hits_s=hits)
        if not specs:
            return "No bed could be generated (see the trace). No music is a valid answer: set_music null."
        lines = [f"{len(specs)} bed(s) generated for a {dur:.1f} s cut (ending lands on the last word; lift on the "
                 "payoff):"]
        for sp in specs:
            desc = sp.asset.description if sp.asset is not None else ""
            lic = sp.asset.licence.name if sp.asset is not None and sp.asset.licence else "?"
            lines.append(f"  asset_id {sp.asset_id}: {_clip(desc, 400)} · licence {lic}")
        lines.append("Pick one with audio_ops set_music {asset_id, source: 'elevenlabs', level_lu_under_speech: -18 "
                     "(-16..-20), duck: true} — or set_music null. You cannot hear it: the analysis numbers and the "
                     "brief decide.")
        return "\n".join(lines)

    def t_sfx_guidance(self, kind: str | None = None) -> str:
        from studio.audio import sfx

        return sfx.level_guidance(kind)

    # ---------------------------------------------------------------- colour
    def t_measure_color(self, items: list[str]) -> str:
        import numpy as np

        from studio.perception import frames as fr

        ix = self.index
        refs = [str(x).strip() for x in (items or []) if str(x).strip()][:24]
        if not refs:
            return "ERROR: give word IDs, e.g. ['w0001', 'w0120']"
        try:
            src = fr.resolve_source(self.job, "mezz")
        except (FileNotFoundError, ValueError):
            return "ERROR: no mezzanine video for this job."
        lines = ["Measured on the mezzanine (BT.709 Y' 0-255; a*/b* CIELAB of the face: + b* warmer/yellow, + a* "
                 "magenta; clipped = Y' ≥ 250, crushed = Y' ≤ 6):"]
        for r in refs:
            w = ix.word(r.split(":")[0])
            t = (w.start_us + w.end_us) // 2
            img = fr.grab_frame(self.job, t, source=str(src), width=540).astype(np.float32) / 255.0
            y = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
            box = ix.face_at(t)
            face = ""
            if box is not None:
                h, wd = y.shape
                x0, y0 = int(max(0, (box.cx - box.w / 2) * wd)), int(max(0, (box.cy - box.h / 2) * h))
                x1, y1 = int(min(wd, (box.cx + box.w / 2) * wd)), int(min(h, (box.cy + box.h / 2) * h))
                if x1 > x0 + 2 and y1 > y0 + 2:
                    crop = img[y0:y1, x0:x1]
                    lab = _rgb_to_lab(crop.reshape(-1, 3))
                    face = (f"face Y' {float(y[y0:y1, x0:x1].mean()) * 255:.0f}, a* {float(lab[:, 1].mean()):+.1f}, "
                            f"b* {float(lab[:, 2].mean()):+.1f}")
            clipped = float((y >= 250 / 255).mean()) * 100
            crushed = float((y <= 6 / 255).mean()) * 100
            lines.append(f"  {w.id}: frame Y' mean {float(y.mean()) * 255:.0f}; {face or 'no face'}; clipped "
                         f"{clipped:.1f}%, crushed {crushed:.1f}%")
        lines.append("Skin usually reads natural with face Y' ≈ 150-190 (SDR) and a small positive a*/b*; judge "
                     "differences between takes, not absolute numbers.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ tool objects
    def _director_tools(self) -> list[Tool]:
        from pydantic_ai import Tool

        d = self

        async def finish_stage(summary: str, questions_for_critics: list[str] | None = None) -> str:
            """End the current stage. Code checks the stage's exit test (brief written; story passes the radio test;
            fine cut compiled and its seams checked; captions looked at with caption_preview; in revision and chat,
            the checks of whatever you touched; no validation errors) and says what is missing if it fails.

            Args:
                summary: What you decided in this stage and why, by ID (for finishing passes, 'none' with the reason
                    is a complete answer). For the story stage include your radio-test verdict.
                questions_for_critics: At finalize and in revisions: up to 6 ID-anchored questions about what you
                    are unsure of (e.g. 'At w0088->w0091, does the tail feel clipped?'); the critics answer them when
                    the render is reviewed.
            """
            return await d._arun("finish_stage", {"summary": summary, "questions_for_critics": questions_for_critics},
                                 lambda: d.t_finish_stage(summary, questions_for_critics))

        async def radio_test() -> str:
            """The kept story as a listener hears it with the picture off, in output order: each segment's words and
            duration, what each seam removes (and whether it falls mid-sentence), the first 3 s, the ending, pins,
            and checks (partly kept sentences, both takes of a retake kept, cut-off words, fillers). The story stage
            cannot finish until the current story has been radio-tested."""
            return await d._arun("radio_test", {}, d.t_radio_test)

        async def compile_check() -> str:
            """Compile the current document without rendering and report what code will actually do: output length,
            first/last word timing, every pause target you set as asked → kept (a kept breath can make a pause longer
            than asked), long pauses still playing, seams with the words on each side, framing asked → rendered scale
            (1080p sources are clamped at the face-safe ceiling), insert and text timing, caption pages over 20 CPS or
            under 0.5 s, SFX, music, compiler notes and validation checks."""
            return await d._arun("compile_check", {}, d.t_compile_check)

        async def check_seams(max_seams: int = 16) -> Any:
            """Images: the last OUT frame and first IN frame of every true cut in the current story (source frames,
            labelled with the words), with the measured face shift/scale, eyes and mouth at each side. Use it to find
            visible jump cuts inside a thought, blinks and mouths caught mid-word.

            Args:
                max_seams: Show at most this many seams (1-24).
            """
            return await d._arun("check_seams", {"max_seams": max_seams}, lambda: d.t_check_seams(max_seams))

        async def broll_search(need: str, queries: list[str], anchor_from_word: str, anchor_to_word: str,
                               mode: Literal["full", "split_top", "split_bottom", "pip"] = "full",
                               expected_false: list[str] | None = None,
                               kinds: list[Literal["video", "image"]] | None = None,
                               allow_faces: bool = True) -> Any:
            """Source b-roll for one beat: searches stock (Pexels) and the creator's media, ranks candidates by
            meaning, rejects any that fail the code gates (resolution, sharpness, text/watermarks, faces, shot
            changes, crop, duration, grade), and returns a contact sheet of the best (at the final crop, graded,
            between the A-roll frames either side) with candidate IDs and the judging rubric. 'none' stays valid.

            Args:
                need: One concrete phrase: subject + action + setting (e.g. 'hands typing on a laptop keyboard').
                queries: 3-8 concrete search variants.
                anchor_from_word: First kept word the insert covers.
                anchor_to_word: Last kept word it covers.
                mode: full, split_top, split_bottom or pip.
                expected_false: Look-alikes that would be wrong (written before looking).
                kinds: 'video' and/or 'image' (default both).
                allow_faces: False rejects recognisable strangers (negative or medical narration).
            """
            args = {"need": need, "queries": queries, "anchor_from_word": anchor_from_word,
                    "anchor_to_word": anchor_to_word, "mode": mode, "expected_false": expected_false, "kinds": kinds,
                    "allow_faces": allow_faces}
            return await d._arun("broll_search", args, lambda: d.t_broll_search(
                need, queries, anchor_from_word, anchor_to_word, mode, expected_false, kinds, allow_faces))

        async def broll_use(candidate_id: str, anchor_from_word: str, anchor_to_word: str, job: str,
                            scores: dict[str, int],
                            mode: Literal["full", "split_top", "split_bottom", "pip"] | None = None,
                            audio: Literal["voice_only", "duck", "with_sfx"] = "voice_only",
                            transition_in: Literal["cut", "fade", "dissolve"] = "cut",
                            transition_out: Literal["cut", "fade", "dissolve"] = "cut") -> str:
            """Use a judged b-roll candidate: code enforces the bar (every rubric score >= 4), downloads it into the
            job with its licence record, and adds the insert (add_insert with the registered asset ID).

            Args:
                candidate_id: ID from a broll_search sheet.
                anchor_from_word: First kept word the insert covers.
                anchor_to_word: Last kept word it covers.
                job: Why the insert exists, in one line (prove / show / orient / mark structure / hide a seam).
                scores: Your 1-5 scores: {"subject": n, "readable": n, "clean": n, "look": n, "sharp": n}.
                mode: full, split_top, split_bottom or pip (default: the mode searched for).
                audio: voice_only (default), duck or with_sfx.
                transition_in: cut (default), fade or dissolve.
                transition_out: cut (default), fade or dissolve.
            """
            args = {"candidate_id": candidate_id, "anchor_from_word": anchor_from_word,
                    "anchor_to_word": anchor_to_word, "job": job, "scores": scores, "mode": mode}
            return await d._arun("broll_use", args, lambda: d.t_broll_use(
                candidate_id, anchor_from_word, anchor_to_word, job, scores, mode, audio, transition_in,
                transition_out))

        async def broll_screenshot(url: str, description: str, selector: str | None = None) -> Any:
            """Capture a public web page at phone size as a b-roll candidate (for 'show the thing named': a site, a
            post, a headline). Returns the capture to judge; then broll_use.

            Args:
                url: http(s) URL.
                description: What the capture shows.
                selector: Optional CSS selector to capture one element.
            """
            return await d._arun("broll_screenshot", {"url": url, "description": description, "selector": selector},
                                 lambda: d.t_broll_screenshot(url, description, selector))

        async def broll_generate(prompt: str, style: Literal["stylized", "photo", "soul"] = "stylized") -> Any:
            """Generate a still (Higgsfield) as a b-roll candidate when stock cannot show the idea. Often unavailable
            (no credits): then use stock, a designed card, or none.

            Args:
                prompt: What the image shows (no text in the image).
                style: stylized (default), photo or soul.
            """
            return await d._arun("broll_generate", {"prompt": prompt, "style": style},
                                 lambda: d.t_broll_generate(prompt, style))

        async def auto_captions(apply: bool = True, font: str | None = None, size_px: int | None = None,
                                animation: Literal["none", "pop", "karaoke", "fade", "slide"] | None = None,
                                case: Literal["as_is", "upper", "lower", "title"] | None = None,
                                highlight_color: str | None = None, max_words_per_page: int | None = None,
                                position: Literal["auto", "below_chin", "lower_third", "center",
                                                  "upper_third"] | None = None) -> str:
            """Page the kept words into captions with code (phrase-aware breaks, reading speed, prosody-backed
            accents, timing on the frame grid) and apply the plan with set_captions; reports every page's time on
            screen, reading speed (CPS) and accent. Edit pages afterwards with captions_ops.

            Args:
                apply: Apply the plan (default) or only propose it.
                font: Caption font (default Montserrat).
                size_px: Font size at 1080 px width (house default 88; the doctrine's phrase range is 64-96; a
                    page never renders under 64, one too wide for a line at 64 wraps instead).
                animation: none, pop, karaoke, fade or slide.
                case: as_is, upper, lower or title.
                highlight_color: Accent colour, e.g. '#FFD400'.
                max_words_per_page: Page size cap (1-10).
                position: Placement policy. 'auto' (default) follows the doctrine's order: under the chin in the
                    strict band, smaller text, the relaxed caption floor, then above the head. 'below_chin' only
                    places under the chin (strict band, then the relaxed floor; never above the head). 'lower_third',
                    'center', 'upper_third' fix a height (kept off eyes and mouth). The report after applying lists
                    the options on this take with their measured costs.
            """
            args = {"apply": apply, "font": font, "size_px": size_px, "animation": animation, "case": case,
                    "highlight_color": highlight_color, "max_words_per_page": max_words_per_page, "position": position}
            return await d._arun("auto_captions", args, lambda: d.t_auto_captions(
                apply, font, size_px, animation, case, highlight_color, max_words_per_page, position))

        async def voice_plan(apply: bool = False) -> str:
            """A measured starting voice chain (high-pass, EQ, de-ess, compression, leveler, denoise/isolation only
            when measurements call for it; each stage names the measurement that justified it).

            Args:
                apply: Apply it with set_voice_chain now (default: only report it).
            """
            return await d._arun("voice_plan", {"apply": apply}, lambda: d.t_voice_plan(apply))

        async def music_options(n: int = 2, mood: str | None = None, prompt: str | None = None) -> str:
            """Generate candidate music beds (ElevenLabs, instrumental, licensed) sized to the compiled cut: the ending
            lands on the last word and a lift on the payoff. Returns registered asset IDs with measured analysis
            (tempo, key, ending, voice detection). Call only when a bed has a job; no music is always valid.

            Args:
                n: Number of candidates (1-3).
                mood: Mood words, e.g. 'warm, optimistic'.
                prompt: Extra direction for the composer.
            """
            return await d._arun("music_options", {"n": n, "mood": mood, "prompt": prompt},
                                 lambda: d.t_music_options(n, mood, prompt))

        async def sfx_guidance(kind: str | None = None) -> str:
            """Level and placement guidance for sound effects (relative to the voice), optionally for one kind
            (whoosh, pop, hit, riser, click, ding, …).

            Args:
                kind: One SFX kind, or omit for all.
            """
            return await d._arun("sfx_guidance", {"kind": kind}, lambda: d.t_sfx_guidance(kind))

        async def measure_color(items: list[str]) -> str:
            """Measure the picture at word IDs on the mezzanine: frame luma, the face's luma and CIELAB a*/b* cast, and
            clipped/crushed pixel share. Read numbers, not impressions, before any colour change.

            Args:
                items: Word IDs, e.g. ['w0001', 'w0042', 'w0120'] (one per take or lighting change).
            """
            return await d._arun("measure_color", {"items": items}, lambda: d.t_measure_color(items))

        async def caption_preview(pages: list[str] | None = None) -> Any:
            """Look at the captions as the viewer will: the overlay renderer draws the caption pages (and any text on
            screen) on the framed picture at the page's settled frame, under the platform UI mask (red = UI, amber =
            UI only with a long post description), at phone scale. Shows the hook page, the smallest page, pages off
            the position prior and any pages you name, with measured size, height and relation to the face.

            Args:
                pages: Caption page IDs to include (up to 6 in all), e.g. ['p004', 'p017'].
            """
            return await d._arun("caption_preview", {"pages": pages}, lambda: d.t_caption_preview(pages))

        funcs = [finish_stage, radio_test, compile_check, check_seams, broll_search, broll_use, broll_screenshot,
                 broll_generate, auto_captions, caption_preview, voice_plan, music_options, sfx_guidance,
                 measure_color]
        sequential = {"finish_stage", "broll_use", "auto_captions", "voice_plan", "music_options", "caption_preview"}
        return [Tool(f, takes_ctx=False, max_retries=5, docstring_format="google", sequential=f.__name__ in sequential)
                for f in funcs]


# ============================================================================================ caption preview
def frame_index_floor(t: Any, fps: Fraction) -> int:
    return math.floor(Fraction(t) * fps + Fraction(1, 1_000_000))


def _framed_frame(job: Job, index: TakeIndex, tl: Any, t: Fraction) -> Any:
    """The A-roll picture at output time ``t`` as the renderer frames it (source frame, framing crop, split region),
    as a PIL image at the output size; a full-frame insert shows as a labelled grey tile."""
    from PIL import Image, ImageDraw

    from studio.compile.timeline import crop_window, framing_state, region_for_time
    from studio.perception import frames as fr

    W, H = tl.width, tl.height
    canvas = Image.new("RGB", (W, H), (60, 60, 60))
    covering = [x for x in tl.inserts if Fraction(x.out_start) <= t < Fraction(x.out_end)
                and x.mode in ("full", "card")]
    if covering:
        ImageDraw.Draw(canvas).text((40, H // 2), f"insert {covering[0].insert_id} ({covering[0].mode})",
                                    fill=(230, 230, 230))
        return canvas
    seg = tl.segment_at(t)
    if seg is None:
        return canvas
    src = "mezz" if job.mezz_path.exists() else "proxy"
    src_us = min(max(seg.out_to_src_us(t), seg.src_in_us), max(seg.src_in_us, seg.src_out_us - 1))
    img = Image.fromarray(fr.grab_frame(job, src_us, source=src))
    sw, sh = index.media.width, index.media.height
    fx, fy = img.width / sw, img.height / sh
    rx, ry, rw, rh = region_for_time(tl, t)
    sc, cx, cy = framing_state(seg.framing, t)
    x0, y0, w, h = crop_window(sc, cx, cy, sw, sh, rw * W, rh * H)
    crop = img.crop((round(x0 * fx), round(y0 * fy), round((x0 + w) * fx), round((y0 + h) * fy)))
    canvas.paste(crop.resize((max(1, round(rw * W)), max(1, round(rh * H))), Image.LANCZOS),
                 (round(rx * W), round(ry * H)))
    return canvas


def _overlay_stills(tl: Any, index: TakeIndex, frames: Sequence[int], platforms: Sequence[str], out_dir: Path,
                    settings: Settings | None) -> dict[int, Path]:
    """The overlay layer at ``frames`` rendered by the real renderer (Remotion stills of the full overlay props).
    ``STUDIO_PREVIEW_STILLS=approx`` (keyless tests) or a missing renderer returns {} (the caller approximates)."""
    if (os.environ.get("STUDIO_PREVIEW_STILLS") or "").strip().lower() == "approx":
        return {}
    import concurrent.futures

    from studio.compile import overlays as ov

    props = ov.build_overlay_props(tl, index=index, platforms=list(platforms), settings=settings)
    bundle = ov.bundle_overlay(settings)
    root = ov._overlay_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    pfile = out_dir / "props.json"
    pfile.write_text(json.dumps(props.to_json_dict()), encoding="utf-8")

    def one(f: int) -> tuple[int, Path | None]:
        out = out_dir / f"still_{f:06d}.png"
        cmd = [*ov._remotion_bin(root), "still", str(bundle), ov.COMPOSITION_ID, str(out), f"--props={pfile}",
               f"--frame={int(f)}", "--image-format=png", "--overwrite", "--log=error"]
        exe = ov._browser_executable(settings)
        if exe is not None:
            cmd.append(f"--browser-executable={exe}")
        proc = ov._run(cmd, root, timeout=180)
        return f, out if proc.returncode == 0 and out.exists() else None

    res: dict[int, Path] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        for f, p in ex.map(one, sorted(set(int(x) for x in frames))):
            if p is not None:
                res[f] = p
    return res


def _approx_caption(canvas: Any, pg: Any, row: Mapping[str, Any], tl: Any) -> None:
    """Draw a caption block where the placer put it (the renderer was unavailable): white text, black outline."""
    from PIL import ImageDraw, ImageFont

    W = tl.width
    size = max(12, round(float(row.get("size_px", pg.style.size_px)) * W / 1080))
    font = None
    for f in ("/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(f):
            font = ImageFont.truetype(f, size)
            break
    dr = ImageDraw.Draw(canvas)
    text = pg.text or " ".join(w.text for w in pg.words)
    top = float(row.get("top_px", pg.y_norm * tl.height))
    bbox = dr.textbbox((0, 0), text, font=font)
    x = (W - (bbox[2] - bbox[0])) / 2
    dr.text((x, top), text, font=font, fill=(255, 255, 255), stroke_width=max(2, size // 10), stroke_fill=(0, 0, 0))


def _mask_tile(img: Any, zone: Any) -> Any:
    """The platform UI mask on one full-size frame: red = UI, amber = UI only with a long description."""
    from PIL import Image, ImageDraw

    W, H = img.size
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)
    floor = zone.relaxed_bottom
    for b in ((0, 0, W, zone.top), (0, H - floor, W, H), (0, 0, zone.left, H), (W - zone.right, 0, W, H)):
        dr.rectangle(b, fill=(255, 30, 30, 70))
    if zone.bottom > floor + 0.5:
        dr.rectangle((0, H - zone.bottom, W, H - floor), fill=(255, 170, 0, 45))
    dr.rectangle((zone.left, zone.top, W - zone.right, H - zone.bottom), outline=(255, 40, 40, 230), width=4)
    return Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")


def _caption_preview_sheet(job: Job, index: TakeIndex, tl: Any, pages: Sequence[Any], frames: Mapping[str, int],
                           platforms: Sequence[str], rows: Mapping[str, Mapping[str, Any]], *,
                           settings: Settings | None, version: int) -> Path:
    """Composite the chosen caption pages on the framed picture under the UI mask; 540-px tiles in one sheet."""
    from PIL import Image, ImageDraw

    from studio.compile.captions import safe_zone_for

    out_dir = job.critique_dir / "caption_preview" / f"v{version}"
    stills = _overlay_stills(tl, index, list(frames.values()), platforms, out_dir, settings)
    zone = safe_zone_for(list(platforms), width=tl.width, height=tl.height)
    fps = Fraction(tl.fps)
    tiles = []
    for pg in pages:
        f = frames[pg.page_id]
        t = Fraction(f) / fps
        base = _framed_frame(job, index, tl, t)
        if f in stills:
            ovl = Image.open(stills[f]).convert("RGBA").resize(base.size, Image.LANCZOS)
            base = Image.alpha_composite(base.convert("RGBA"), ovl).convert("RGB")
        else:
            _approx_caption(base, pg, rows.get(pg.page_id, {}), tl)
        base = _mask_tile(base, zone)
        tw = 540
        th = round(base.height * tw / base.width)
        tile = Image.new("RGB", (tw, th + 44), (18, 18, 18))
        tile.paste(base.resize((tw, th), Image.LANCZOS), (0, 0))
        r = rows.get(pg.page_id, {})
        ImageDraw.Draw(tile).text((8, th + 6), f"{pg.page_id} f{f}  {r.get('size_px', '?')}px  "
                                               f"{r.get('relation', '?')}  top y{r.get('top_px', 0):.0f}",
                                  fill=(255, 220, 60))
        ImageDraw.Draw(tile).text((8, th + 24), _clip(str(r.get("text", "")), 60), fill=(230, 230, 230))
        tiles.append(tile)
    cols = min(3, len(tiles))
    rows_n = -(-len(tiles) // cols)
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * 6, rows_n * th + (rows_n + 1) * 6), (8, 8, 8))
    for k, tile in enumerate(tiles):
        sheet.paste(tile, (6 + (k % cols) * (tw + 6), 6 + (k // cols) * (th + 6)))
    kind = "rendered" if stills else "approx"
    path = out_dir / f"caption_preview_{kind}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG")
    return path


# ============================================================================================ helpers
def _rgb_to_lab(rgb: Any) -> Any:
    """sRGB (0..1, BT.709 primaries) → CIELAB (D65)."""
    import numpy as np

    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1)


def _is_provider_error(e: BaseException) -> bool:
    from pydantic_ai.exceptions import ContentFilterError

    return isinstance(e, ContentFilterError)


def _complete_prefix(messages: list[ModelMessage]) -> list[ModelMessage]:
    """The longest prefix of ``messages`` that ends on a model response without pending tool calls, so the next
    stage can append a user message (a prefix keeps every thinking block bound to what preceded it)."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    out = list(messages)
    while out:
        last = out[-1]
        if isinstance(last, ModelResponse) and not any(isinstance(p, ToolCallPart) for p in last.parts):
            break
        out.pop()
    return out


def _usage_requests(result: Any) -> int:
    u = getattr(result, "usage", None)
    if callable(u):
        u = u()
    return int(getattr(u, "requests", 0) or 0)


# ============================================================================================ convenience
def run_director(job: Job, index: TakeIndex | None = None, *, brief: str | None = None, style: str | None = None,
                 platform: str = "tiktok", spec: ModelSpec | None = None, settings: Settings | None = None,
                 **kwargs: Any) -> CutDocument:
    """Run every Director stage for ``job`` (resuming finished stages) and return the document."""
    d = Director(job, index, spec=spec, settings=settings, brief=brief, style=style, platforms=(platform,), **kwargs)
    return d.run()


def chat_edit(job: Job, instruction: str, *, spec: ModelSpec | None = None, settings: Settings | None = None,
              **kwargs: Any) -> dict[str, Any]:
    """Apply a creator instruction as ops; returns ``{"doc", "summary", "results", "changed", "base_version"}``."""
    d = Director(job, spec=spec, settings=settings, **kwargs)
    return d.chat(instruction)
