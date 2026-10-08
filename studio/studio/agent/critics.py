"""Critics (ARCHITECTURE §9, ``skills/editing/critique.md``): the critique packet, the frame judge, the
watcher, the confirmation rule and position-swapped pairwise comparison.

**The packet** (:func:`build_packet`) is built from the *encoded deliverable*, never the plan or the proxy:

* the metrics packet and the ten invariants (:func:`studio.qa.invariants.evaluate_render`, reused when the
  render was already checked), including the ASR round-trip transcript of the render when an ASR key exists;
  a failed invariant becomes a P0 note that names the layer able to fix it (:func:`invariant_layer`: an engine
  artefact says so, so nobody trades the creator's words for a gate);
* caption geometry (:func:`studio.compile.captions.caption_geometry`): where every page sits against the face
  after framing (under the chin and by how much, above the head, on the hair, over the face), how big it renders
  (font px, cap height as % of the frame), size spread and reach into the platform bands;
* contact sheets cut from the final encode: the hook (0–3 s every 0.25 s), before/after pairs at every seam
  with the measured face shift, one frame per caption page under the platform's UI mask (red = UI, amber = UI
  only when the post description runs long), a phone-scale sheet (540 px tiles) of the hook page, the smallest
  page and the pages furthest from the position prior, the middle frame of every insert, an overview every ~2 s;
* the render's transcript in output order with word IDs, seams and pauses; a plain copy (words and IDs only) for
  the naive pass; an ID map (segments, pages, inserts, texts, SFX) instead of the Director's plan.

**Critique** (:func:`critique`):

1. *Naive pass* — a fresh context with no brief, no document and no cut markers: three gut reactions, where it
   wanted to swipe, and the one sentence it remembers (checked against the brief's idea).
2. *Rubric pass* — the frame judge (``Settings.critic_model``; a non-Anthropic family when its key exists) answers
   the binary rubric (:func:`rubric_questions`: critique.md's critic questions, the critic questions of every topic
   file whose layer is in the render — captions-and-text whenever captions are on — and the brief's items) and
   writes localized notes by ID with severity and claim type. **If this pass fails (after retries with backoff on
   transient errors), :class:`CritiqueUnavailable` is raised: a failed review is never "nothing to change", and
   nothing is cached as notes.**
3. *Watcher* — Gemini watching the review copy (burned word IDs and frame numbers) with audio, when a Google
   key exists; otherwise the packet records that the critique ran on frames + metrics only.
4. *Confirmation* (:func:`confirm_notes`) — a P0/P1 stands only when a metric or another critic confirms it:
   metric evidence that measures the note's claim (:data:`_CLAIMS_FOR_KIND`) on the same IDs (whole-video
   measurements only confirm their own claim), then agreement between critics, then a fresh-context second
   critic answering yes/no per note (marked ``same_family`` when it shares the Director's family). An unconfirmed
   P0/P1 is kept as P2 with ``unconfirmed_severity``.

**Pairwise** (:func:`pairwise`): two judges each compare the two renders in **both orders** (labels A/B, never
which one is new) with the same evidence for both (including caption sheets and geometry), plus a neutral code
diff (:func:`version_diff`) and side-by-side frames of the differences; per area (story, hook, pacing, seams,
framing, captions, text, broll, audio, color, ending) then overall. A judge saying "same" in both orders abstains.
Taste changes need every judge to prefer the challenger in both orders with no metric regression; a revision that
answers confirmed P0/P1 notes wins when nothing regresses, no judge prefers the champion, and a judge prefers it or
code measures a confirmed note as resolved (:func:`resolved_notes`). Anything else is a tie; a tie keeps the
champion.

Every artefact is saved under ``critique/`` (notes and verdicts as JSON the report renders; an unfinished review as
``unreviewed.json``; images and raw packets under ``critique/<render>/frames/``).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from studio.agent.providers import (
    ROLE_EFFORT,
    ModelSpec,
    capabilities_for,
    house_spec,
    map_provider_error,
    trace_event,
)
from studio.config import get_settings

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # the CLI prints its own progress

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai.models import Model

    from studio.compile.models import Timeline
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "overview_times",
    "CRITIC_QUESTIONS", "AREAS", "RubricAnswer", "CriticNote", "NaiveResult", "JudgeResult", "ConfirmItem",
    "ConfirmResult", "AreaVerdict", "PairwiseVerdict", "WatchResult", "AttentionResult", "Judge", "JudgePanel",
    "default_panel", "CritiquePacket", "build_packet", "render_transcript", "critique", "pairwise",
    "attention_check", "format_notes", "metric_regressions", "make_review_copy", "SECOND_JUDGE_MODEL",
    "CritiqueUnavailable", "CRITIC_BACKOFF_S", "rubric_questions", "critic_questions", "version_diff",
    "resolved_notes", "confirm_notes", "invariant_layer", "plain_transcript", "id_map", "render_layers",
    "note_claims", "PAIRWISE_AREAS", "TOPIC_QUESTIONS", "rubric_failed",
]

SECOND_JUDGE_MODEL = "claude-sonnet-5"

#: critique.md "Critic questions" (read from the doctrine at run time; this is the fallback copy).
CRITIC_QUESTIONS: tuple[str, ...] = (
    "On the first uninterrupted 1x watch, did attention ever leave the speaker for something that was not the point "
    "being made?",
    "By about 3 s, with sound on and again muted, does the viewer know the topic and have a reason to keep watching?",
    "Is any word or word edge clipped, or is there a click, drop-out, or level or noise jump at any seam?",
    "Is there a visible jump inside a thought that is neither covered nor clearly intentional?",
    "Is there a stretch where you wanted to swipe: dead air, repetition, or a static run with nothing new?",
    "Does every insert show what the voice names, arrive on the word and leave before it outstays its job?",
    "Muted, can every caption page be read in time, with no typo, bad break, or text over the face or platform UI?",
    "On a phone speaker, is every word understandable, and is the music never noticed during speech?",
    "Do skin, exposure and white balance look natural and match across takes and inserts, with no grey, washed-out "
    "or blown HDR conversion?",
    "Is there any flash frame, black frame, frozen frame or lip-sync drift?",
    "Does the video end within about 0.5 s of the last word (or on a held frame that shows something new), with the "
    "CTA intact and a clean replay cut to frame 0?",
    "Would the creator recognize this as themselves, with their energy, laughs and catchphrases intact, rather than "
    "a template?",
    "Take away the newest element (insert, zoom, SFX, card or music): would a viewer miss it? If not, the best note "
    "is to remove it.",
)

#: Murch's order (emotion, story, rhythm, eye-trace, planarity, space) mapped onto note areas.
AREAS: tuple[str, ...] = ("story", "hook", "ending", "pacing", "seams", "framing", "broll", "text", "captions",
                          "audio", "music", "sfx", "color", "other")
_SEV_RANK = {"P0": 0, "P1": 1, "P2": 2}
_ID_RE = re.compile(r"\b(?:w\d{4,}|g\d{4,}|s\d{3,}|c\d{2,}|seg\d{3,}|i\d{3,}|t\d{3,}|p\d{3,}|fx\d{3,})\b")
_RANGE_RE = re.compile(r"^(w\d{4,})\s*(?:-|–|\.\.|to)\s*(w\d{4,})$")


# ============================================================================================ schemas
class _Out(BaseModel):
    pass


class RubricAnswer(_Out):
    question: str = Field(description="The question (or its number)")
    answer: Literal["yes", "no", "cant_tell"]
    passed: bool | None = Field(default=None, description=(
        "True when this answer is the good outcome for the viewer (questions differ in polarity: 'Is any word "
        "clipped?' answered no passes; 'Can every page be read in time?' answered yes passes); None for cant_tell"))
    refs: list[str] = Field(default_factory=list, description="IDs the answer is about")
    evidence: str = ""


def rubric_failed(answer: Mapping[str, Any]) -> bool:
    """A rubric answer that is a failed check (older records without ``passed``: a plain "no")."""
    if answer.get("passed") is not None:
        return answer.get("passed") is False
    return answer.get("answer") == "no"


class CriticNote(_Out):
    severity: Literal["P0", "P1", "P2"]
    area: str = Field(description="story|hook|ending|pacing|seams|framing|broll|text|captions|audio|music|sfx|"
                                  "color|other")
    refs: list[str] = Field(default_factory=list, description="Word/gap/segment/insert/text/page/sfx IDs")
    text: str = Field(description="What the viewer experiences (quote the spoken words)")
    cause: str = ""
    suggested_ops: str = Field(default="", description="The ops you would try, by ID (the Director decides)")
    evidence: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"
    taste: bool = Field(default=False, description="True when a reasonable editor might choose otherwise")
    claim: str = Field(default="other", description=(
        "The kind of problem claimed: clipped_word | click | level_jump | lost_or_masked_word | av_sync | loudness "
        "| black_or_flash | freeze | text_position | text_size | reading_speed | typo | jump_cut | dead_air | "
        "late_hook | lingering_end | story | other"))


#: Metric evidence kind → the note claims it can confirm (a caption reading-speed hit never confirms a claim about
#: placement; a lingering ending never confirms a story note).
_CLAIMS_FOR_KIND: dict[str, frozenset[str]] = {
    "click": frozenset({"click", "level_jump"}),
    "asr_lost": frozenset({"clipped_word", "lost_or_masked_word"}),
    "masked": frozenset({"lost_or_masked_word"}),
    "text_placement": frozenset({"text_position"}),
    "caption_position": frozenset({"text_position"}),
    "caption_ui": frozenset({"text_position"}),
    "caption_size": frozenset({"text_size"}),
    "caption_fast": frozenset({"reading_speed"}),
    "cut_inside_word": frozenset({"clipped_word", "jump_cut"}),
    "cut_inside_clause": frozenset({"jump_cut"}),
    "face_jump": frozenset({"jump_cut"}),
    "clipped_word": frozenset({"clipped_word"}),
    "loudness": frozenset({"loudness", "level_jump"}),
    "av_sync": frozenset({"av_sync"}),
    "silence": frozenset({"clipped_word", "lost_or_masked_word", "level_jump"}),
    "video_black": frozenset({"black_or_flash"}),
    "video_flash": frozenset({"black_or_flash"}),
    "video_freeze": frozenset({"freeze"}),
    "lingering_end": frozenset({"lingering_end"}),
    "late_start": frozenset({"late_hook"}),
    "long_pause": frozenset({"dead_air"}),
}
#: When a note does not name its claim, the words it uses decide (same mapping, by vocabulary).
_CLAIM_WORDS: dict[str, str] = {
    "clipped_word": r"clip|cut off|cut-off|chopp|truncat|swallow|lost (?:the )?(?:start|end)|"
                    r"missing (?:syllable|sound)",
    "click": r"click|pop\b|tick|glitch",
    "level_jump": r"level|jump in (?:volume|noise)|noise (?:jump|floor)|louder|quieter|room tone",
    "lost_or_masked_word": r"lost|mask|buried|drown|inaudib|unintelligib|can't hear|cannot hear",
    "av_sync": r"sync|lip",
    "loudness": r"loud|quiet|lufs|peak|volume",
    "black_or_flash": r"black|flash",
    "freeze": r"freez|frozen|stuck|still frame",
    "text_position": r"position|placement|placed|sits|over the|covers|cover|collid|overlap|\bui\b|forehead|hair|chin|"
                     r"eyes|mouth|above the head|below the",
    "text_size": r"small|tiny|size|legib|thin|hard to read|too big|large",
    "reading_speed": r"fast|cps|reading speed|flash(?:es)? by|too quick|brief|short page",
    "jump_cut": r"jump|snap|shift|mid-thought|mid thought|mid-sentence",
    "dead_air": r"dead|drag|stall|pause|slow|linger",
    "late_hook": r"late|slow start|first (?:\d|three|two) ?s|hook",
    "lingering_end": r"end|linger|tail|last",
}


def note_claims(note: Mapping[str, Any]) -> set[str]:
    """The claims a note makes: its declared ``claim``, else what its words say."""
    c = str(note.get("claim") or "other").strip().lower()
    if c and c != "other":
        return {c}
    text = f"{note.get('text', '')} {note.get('cause', '')}".lower()
    return {k for k, rx in _CLAIM_WORDS.items() if re.search(rx, text)}


class NaiveResult(_Out):
    reactions: list[str] = Field(default_factory=list, description="At most three gut reactions")
    swipe_moments: list[str] = Field(default_factory=list, description="Where you wanted to swipe (quote words/IDs)")
    remembered_sentence: str = ""


class JudgeResult(_Out):
    rubric: list[RubricAnswer] = Field(default_factory=list)
    notes: list[CriticNote] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list, description="One or two moments that work and must survive")
    answers: list[str] = Field(default_factory=list, description="Answers to the Director's questions, in order")
    verdict: Literal["ship_it", "revise"] = "revise"


class ConfirmItem(_Out):
    note: int = Field(description="Index of the note being checked")
    confirmed: Literal["yes", "no", "cant_tell"]
    reason: str = ""


class ConfirmResult(_Out):
    items: list[ConfirmItem] = Field(default_factory=list)


class AreaVerdict(_Out):
    area: str
    verdict: Literal["A", "B", "same"]
    reason: str = ""


class PairwiseVerdict(_Out):
    areas: list[AreaVerdict] = Field(default_factory=list)
    overall: Literal["A", "B", "same"]
    reason: str = ""


class WatchResult(_Out):
    notes: list[CriticNote] = Field(default_factory=list)
    attention_left_speaker: list[str] = Field(default_factory=list)
    swipe_moments: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)


class AttentionResult(_Out):
    pulls_attention: Literal["yes", "no", "cant_tell"]
    moments: list[CriticNote] = Field(default_factory=list)
    verdict: str = ""


# ============================================================================================ judges
@dataclass
class Judge:
    """One critic model. ``model`` (a pydantic-ai model) overrides ``spec`` (tests, custom endpoints)."""

    role: str  # frame_judge | second_judge | watcher | confirmer
    spec: ModelSpec | None = None
    model: Model | None = None
    same_family: bool = False

    @property
    def label(self) -> str:
        if self.spec is not None:
            return self.spec.label
        return f"test:{getattr(self.model, 'model_name', 'model')}"

    @property
    def provider(self) -> str:
        return self.spec.provider if self.spec is not None else "anthropic"

    def capabilities(self) -> Any:
        return self.spec.capabilities if self.spec is not None else capabilities_for("anthropic", "claude-opus-5-5")


@dataclass
class JudgePanel:
    frame_judge: Judge
    second_judge: Judge
    watcher: Judge | None = None
    director_provider: str = "anthropic"
    notes: list[str] = field(default_factory=list)

    @property
    def confirmer(self) -> Judge:
        return self.second_judge

    def pairwise_judges(self) -> list[Judge]:
        return [self.frame_judge, self.second_judge]


def default_panel(settings: Settings | None = None, *, director: ModelSpec | None = None,
                  avoid_models: Iterable[str] = ()) -> JudgePanel:
    """House judges: the frame judge from another family when its key exists (Google, then OpenAI), else the house
    critic model (``Settings.critic_model``) — or, when that is a model that wrote this edit (``avoid_models``: the
    Director's active model, e.g. after a fallback), the house Director model instead, so no model judges its own
    edit; the second judge is another house model; the watcher is Gemini when a Google key exists."""
    s = settings or get_settings()
    dprov = director.provider if director is not None else s.director_provider
    avoid = {m for m in avoid_models if m}
    notes: list[str] = []
    frame: Judge
    if dprov != "google" and s.has_key("google"):
        m = os.environ.get("STUDIO_CRITIC_GOOGLE_MODEL") or "gemini-3.1-pro"
        frame = Judge("frame_judge", ModelSpec(provider="google", model=m, api_key=s.key("google"), effort="high"))
    elif dprov != "openai" and s.has_key("openai"):
        m = os.environ.get("STUDIO_CRITIC_OPENAI_MODEL") or "gpt-6-astra"
        frame = Judge("frame_judge", ModelSpec(provider="openai", model=m, api_key=s.key("openai"), effort="high"))
    else:
        spec = house_spec("critic", settings=s)
        if spec.model in avoid and s.director_model not in avoid:
            spec = spec.model_copy(update={"model": s.director_model})
            notes.append(f"the critic model wrote this edit (Director fallback): the frame judge is {spec.model}")
        frame = Judge("frame_judge", spec)
        notes.append("no other model family available: the frame judge is a fresh-context house model (same_family)")
    frame.same_family = frame.provider == dprov
    second_model = os.environ.get("STUDIO_SECOND_JUDGE_MODEL") or SECOND_JUDGE_MODEL
    second = Judge("second_judge", ModelSpec(provider="anthropic", model=second_model, api_key=s.key("anthropic"),
                                             effort=ROLE_EFFORT["critic"]))
    second.same_family = second.provider == dprov
    watcher = None
    if s.has_key("google"):
        watcher = Judge("watcher", house_spec("watcher", settings=s))
        watcher.same_family = dprov == "google"
    else:
        notes.append("no Google key: the watcher did not run; critique uses frames + metrics")
    return JudgePanel(frame_judge=frame, second_judge=second, watcher=watcher, director_provider=dprov, notes=notes)


def _agent(judge: Judge, output_cls: type[BaseModel], instructions: str, job: Job | None, stage: str) -> Any:
    from pydantic_ai import Agent, NativeOutput, PromptedOutput

    from studio.agent.providers import TracedModel, build_model

    if judge.model is not None:
        model = TracedModel(judge.model, job=job, role=judge.role, spec=None, stage=stage) if job is not None \
            else judge.model
        output: Any = output_cls
    else:
        assert judge.spec is not None
        model = build_model(judge.spec, role="critic" if judge.role != "watcher" else "watcher", job=job, stage=stage)
        output = PromptedOutput(output_cls) if judge.spec.provider == "openai_compat" else NativeOutput(output_cls)
    return Agent(model, output_type=output, instructions=instructions, retries=3, name=judge.role)


#: Waits between attempts when a critic call fails transiently (network, timeout, overload, rate limit, 5xx). A
#: review is never skipped for a blip: the Director gets 20 s / 60 s; critics get one more, longer wait.
CRITIC_BACKOFF_S: tuple[float, ...] = (20.0, 60.0, 120.0)
_TRANSIENT_KINDS = ("overloaded", "server", "timeout", "connection", "rate_limit")
_sleep: Callable[[float], None] = time.sleep


class CritiqueUnavailable(RuntimeError):
    """The critics could not review a render: the frame judge's rubric pass failed after retries. This is never
    "nothing to change" — the loop retries the round or ships the champion marked NOT REVIEWED."""

    def __init__(self, message: str, *, record: dict[str, Any] | None = None,
                 metric_notes: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.record = record or {}
        self.metric_notes = list(metric_notes or [])


def _transient(e: BaseException) -> bool:
    err = map_provider_error(e)
    return bool(err.retryable) or err.kind in _TRANSIENT_KINDS


def _ask(judge: Judge, output_cls: type[BaseModel], instructions: str, content: list[Any], *, job: Job | None,
         stage: str, backoff: Sequence[float] | None = None) -> BaseModel:
    """One structured critic call, retried with backoff on transient provider errors (the SDK's own quick retries
    come first); anything else, or the last transient failure, raises."""
    delays = tuple(CRITIC_BACKOFF_S if backoff is None else backoff)
    attempt = 0
    while True:
        try:
            agent = _agent(judge, output_cls, instructions, job, stage)
            return agent.run_sync(content).output
        except Exception as e:
            if attempt >= len(delays) or not _transient(e):
                raise
            wait = delays[attempt]
            attempt += 1
            if job is not None:
                trace_event(job, "critic_retry", stage=stage, judge=judge.label, attempt=attempt, wait_s=wait,
                            error=map_provider_error(e).message[:300])
            _sleep(wait)


def _image_parts(judge: Judge, labelled: Sequence[tuple[str, Path]], *, budget: int | None = None) -> list[Any]:
    from pydantic_ai import BinaryImage

    from studio.agent.tools import _prepare_image

    caps = judge.capabilities()
    if not caps.vision:
        return []
    out: list[Any] = []
    edge = caps.image_edge_limit()
    limit = min(caps.max_images, budget or caps.max_images)
    n = 0
    for label, p in labelled:
        if n >= limit:
            break
        p = Path(p)
        if not p.exists():
            continue
        data, mt, w, h = _prepare_image(p, edge, caps.max_image_bytes, caps.max_image_tokens)
        out.append(f"[image] {label} ({w}x{h})")
        out.append(BinaryImage(data=data, media_type=mt, identifier=p.stem))
        n += 1
    return out


# ============================================================================================ packet
@dataclass
class CritiquePacket:
    render_dir: Path
    final: Path
    doc_version: int
    platform: str
    duration_s: float
    passed: bool
    invariants: list[dict[str, Any]]
    metrics: dict[str, Any]
    advice: list[str]
    transcript: str
    asr_text: str | None = None
    asr_note: str = ""
    sheets: dict[str, list[Path]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metric_notes: list[dict[str, Any]] = field(default_factory=list)
    page_words: dict[str, list[str]] = field(default_factory=dict)
    #: where and how big the caption pages render, against the face and the platform bands (advisory)
    caption_geometry: dict[str, Any] = field(default_factory=dict)
    #: the words as heard, with IDs only (no segment headers, cut markers or pauses): the naive viewer's copy
    plain_transcript: str = ""
    #: the IDs a critic may localize by (segments as word ranges, pages, inserts, texts, SFX): no plan, no reasons
    id_map: str = ""
    #: topic-file layers present in this render (captions, framing, broll, text, music, sfx)
    layers: list[str] = field(default_factory=list)

    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.invariants if not r.get("passed")]

    def to_json(self) -> dict[str, Any]:
        return {"render_dir": str(self.render_dir), "final": str(self.final), "doc_version": self.doc_version,
                "platform": self.platform, "duration_s": self.duration_s, "passed": self.passed,
                "invariants": self.invariants, "metrics": self.metrics, "advice": self.advice,
                "transcript": self.transcript, "asr_text": self.asr_text, "asr_note": self.asr_note,
                "sheets": {k: [str(p) for p in v] for k, v in self.sheets.items()}, "evidence": self.evidence,
                "metric_notes": self.metric_notes, "page_words": self.page_words,
                "caption_geometry": self.caption_geometry, "layers": self.layers}

    def metrics_text(self) -> str:
        return json.dumps(self.metrics, default=str)[:6000]

    def caption_text(self) -> str:
        if not self.caption_geometry:
            return ""
        from studio.compile.captions import caption_geometry_text

        return caption_geometry_text(self.caption_geometry)


def _frames_dir(job: Job, rd: Path) -> Path:
    d = job.critique_dir / rd.name / "frames"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_transcript(timeline: Timeline, index: TakeIndex, *, max_words: int = 1500) -> str:
    """The render as heard, in output order: segment headers with output time, word IDs + text, pauses
    ≥ 0.3 s as ``[0.45s]`` and true cuts as ``‖``."""
    from studio.compile.timeline import piece_is_continuous

    lines: list[str] = []
    n = 0
    prev = None
    prev_end_word: str | None = None
    for k, seg in enumerate(timeline.segments):
        cut = prev is not None and not piece_is_continuous(prev, seg)
        head = f"{'‖ ' if cut else ''}{seg.seg_id} @ {float(seg.out_start):.2f}s"
        toks: list[str] = []
        for wid in seg.word_ids:
            if not index.has_word(wid):
                continue
            sp = timeline.word_map.get(wid)
            if prev_end_word is not None and sp is not None:
                pe = timeline.word_map.get(prev_end_word)
                if pe is not None and float(sp.out_start - pe.out_end) >= 0.3:
                    toks.append(f"[{float(sp.out_start - pe.out_end):.2f}s]")
            toks.append(f"{wid} {index.word(wid).display()}")
            prev_end_word = wid
            n += 1
            if n >= max_words:
                break
        lines.append(head + ": " + " ".join(toks))
        prev = seg
        if n >= max_words:
            lines.append("… (truncated)")
            break
        del k
    return "\n".join(lines)


def plain_transcript(timeline: Timeline, index: TakeIndex, *, max_words: int = 1500) -> str:
    """The render's words as heard, in output order, each with its ID; no segment headers, cut markers or pauses
    (the naive viewer must not be shown where the cuts are)."""
    toks: list[str] = []
    for seg in timeline.segments:
        for wid in seg.word_ids:
            if index.has_word(wid) and timeline.word_map.get(wid) is not None:
                toks.append(f"{wid} {index.word(wid).display()}")
                if len(toks) >= max_words:
                    return " ".join(toks) + " …"
    return " ".join(toks)


def id_map(doc: CutDocument, timeline: Timeline, index: TakeIndex) -> str:
    """IDs a critic can localize by, with no plan text: segments as word ranges, caption pages, inserts, texts and
    SFX with their anchors (what the Director decided and why stays out: judge the render, not the plan)."""
    lines = ["Segments (word ranges, output order): " + ", ".join(f"{s.id} {s.from_word}-{s.to_word}"
                                                                 for s in doc.segments[:60])]
    if timeline.captions:
        lines.append("Caption pages: " + ", ".join(
            f"{p.page_id} {p.words[0].word_id}-{p.words[-1].word_id}" for p in timeline.captions[:120] if p.words))
    if doc.inserts:
        lines.append("Inserts: " + ", ".join(f"{i.id} {i.mode} {i.anchor_from_word}-{i.anchor_to_word}"
                                             for i in doc.inserts))
    if doc.texts:
        lines.append("Texts: " + ", ".join(f"{t.id} {t.kind} {t.anchor_from_word}-{t.anchor_to_word}"
                                           for t in doc.texts))
    if doc.audio.sfx:
        lines.append("SFX: " + ", ".join(f"{x.id} at {x.anchor_word}" for x in doc.audio.sfx))
    del index
    return "\n".join(lines)


def render_layers(doc: CutDocument, timeline: Timeline) -> list[str]:
    """Topic-file layers present in a render (their critic questions join the rubric)."""
    out: list[str] = []
    if timeline.captions:
        out.append("captions")
    if any(s.framing is not None and s.framing.scale > 1.0 + 1e-6 for s in doc.segments) or \
            any(s.seam_in.kind == "punch" for s in doc.segments):
        out.append("framing")
    from studio.doc.model import CardSpec

    if doc.texts or any(isinstance(i.asset, CardSpec) for i in doc.inserts):
        out.append("text")
    if any(not isinstance(i.asset, CardSpec) for i in doc.inserts):
        out.append("broll")
    if timeline.music is not None:
        out.append("music")
    if timeline.sfx:
        out.append("sfx")
    return out


def _seam_labels(timeline: Timeline, index: TakeIndex) -> tuple[list[Fraction], list[str]]:
    from studio.compile.timeline import piece_is_continuous

    times: list[Fraction] = []
    labels: list[str] = []
    for k in range(1, len(timeline.segments)):
        a, b = timeline.segments[k - 1], timeline.segments[k]
        if piece_is_continuous(a, b):
            continue
        lw = a.word_ids[-1] if a.word_ids else ""
        rw = b.word_ids[0] if b.word_ids else ""
        times.append(Fraction(b.out_start))
        labels.append(f"s{len(times)} {lw}|{rw}")
    return times, labels


def _draw_ui_mask(sheet: Path, platform: str, width: int, height: int) -> None:
    """Shade the platform's UI bands (outside the text safe zone) on every tile of a contact sheet."""
    from PIL import Image, ImageDraw

    from studio.compile.captions import safe_zone_for
    from studio.perception.frames import read_sheet_meta

    meta = read_sheet_meta(sheet)
    zone = safe_zone_for(platform, width=width, height=height)
    img = Image.open(sheet).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)
    for t in meta.get("tiles", []):
        x, y, w, h = t.get("rect") or (0, 0, 0, 0)
        if not w or not h:
            continue
        sx, sy = w / width, h / height
        floor = zone.relaxed_bottom
        # red: platform UI (top bar, side rail, and below the relaxed caption floor at the bottom)
        bands = [(x, y, x + w, y + zone.top * sy), (x, y + h - floor * sy, x + w, y + h),
                 (x, y, x + zone.left * sx, y + h), (x + w - zone.right * sx, y, x + w, y + h)]
        for b in bands:
            dr.rectangle(b, fill=(255, 30, 30, 70))
        # amber: between the strict band and the relaxed caption floor, UI only when a post description runs long
        if zone.bottom > floor + 0.5:
            dr.rectangle((x, y + h - zone.bottom * sy, x + w, y + h - floor * sy), fill=(255, 170, 0, 45))
        # the strict text band itself, outlined
        dr.rectangle((x + zone.left * sx, y + zone.top * sy, x + w - zone.right * sx, y + h - zone.bottom * sy),
                     outline=(255, 40, 40, 230), width=2)
    out = Image.alpha_composite(img, over).convert("RGB")
    info = None
    with contextlib.suppress(Exception):
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("studio", json.dumps({**meta, "ui_mask": platform}))
    out.save(sheet, format="PNG", pnginfo=info)


def overview_times(timeline: Timeline, every_s: float = 2.0, *, guard_frames: int = 3) -> list[Fraction]:
    """Sample instants about every ``every_s`` for the overview sheet, each moved off caption page changes
    (the 2-frame gap between pages and a page's animated first frames read as "no caption" on a still) to the
    middle of the page it falls in or next to (within 0.5 s)."""
    fps = Fraction(timeline.fps)
    dur = Fraction(timeline.duration)
    last = max(Fraction(0), dur - 1 / fps)
    guard = Fraction(guard_frames) / fps
    pages = [(Fraction(p.out_start), Fraction(p.out_end)) for p in timeline.captions]
    out: list[Fraction] = []
    k = 0
    step = Fraction(every_s).limit_denominator(1000)
    while k * step <= last:
        t = k * step
        k += 1
        near = [(a, b) for a, b in pages if a - guard <= t <= b + guard]
        inside = any(a + guard <= t < b - guard for a, b in pages)
        if pages and near and not inside:
            a, b = min(near, key=lambda ab: abs((ab[0] + ab[1]) / 2 - t))
            mid = (a + b) / 2
            if abs(mid - t) <= Fraction(1, 2):
                t = Fraction(int(mid * fps)) / fps
        out.append(min(max(t, Fraction(0)), last))
    return sorted(set(out))


def _make_sheets(job: Job, rd: Path, final: Path, timeline: Timeline, index: TakeIndex, doc: CutDocument,
                 platform: str, geometry: dict[str, Any] | None = None) -> dict[str, list[Path]]:
    from studio.perception import frames as fr
    from studio.qa.report import _label_at

    d = _frames_dir(job, rd)
    out: dict[str, list[Path]] = {}

    def attempt(name: str, fn: Callable[[], list[Path]]) -> None:
        try:
            out[name] = [p for p in fn() if p is not None]
        except Exception as e:
            trace_event(job, "critic_note", render=rd.name, note=f"{name} sheet failed: {type(e).__name__}: {e}")

    dur = float(timeline.duration)
    fps = Fraction(timeline.fps)

    def overview() -> list[Path]:
        p = d / "overview.png"
        if not p.exists():
            ts = overview_times(timeline)
            items = [fr.FrameRequest(t_us=round(float(t) * 1_000_000) + 1, label=_label_at(timeline, index, t))
                     for t in ts]
            fr.contact_sheet(job, items, source=str(final), timeline=timeline, columns=6, thumb_w=216, out_path=p,
                             title="Overview: a frame about every 2 s (moved off caption page changes)")
        return [p]

    def hook() -> list[Path]:
        p = d / "hook.png"
        if not p.exists():
            ts = [Fraction(k, 4) for k in range(0, 13) if Fraction(k, 4) < Fraction(dur).limit_denominator(1000)]
            items = [fr.FrameRequest(t_us=round(float(t) * 1_000_000) + 1, label=_label_at(timeline, index, t))
                     for t in ts]
            fr.contact_sheet(job, items, source=str(final), timeline=timeline, columns=5, thumb_w=216, out_path=p,
                             title="Hook: the first 3 s every 0.25 s (sound off, phone size)")
        return [p]

    def seams() -> list[Path]:
        times, labels = _seam_labels(timeline, index)
        paths = []
        for k in range(0, len(times), 8):
            p = d / f"seams_{k // 8 + 1}.png"
            if not p.exists():
                fr.seam_pairs(final, times[k:k + 8], timeline=timeline, out_path=p, labels=labels[k:k + 8], columns=2,
                              job=job)
            paths.append(p)
        return paths

    def captions() -> list[Path]:
        pages = timeline.captions
        paths = []
        per = max(1, -(-len(pages) // -(-len(pages) // 20)))  # balanced sheets of at most 20 pages
        for k in range(0, len(pages), per):
            p = d / f"captions_{k // per + 1}.png"
            if not p.exists():
                chunk = pages[k:k + per]
                items = []
                for pg in chunk:
                    mid = (Fraction(pg.out_start) + Fraction(pg.out_end)) / 2
                    frame = int(mid * fps)
                    t_us = round(float(Fraction(frame) / fps) * 1_000_000) + 1
                    text = pg.text or " ".join(w.text for w in pg.words)
                    items.append(fr.FrameRequest(t_us=t_us, label=f"{pg.page_id} \"{text}\""))
                fr.contact_sheet(job, items, source=str(final), timeline=timeline, columns=5, thumb_w=216, out_path=p,
                                 title=f"Caption pages (muted pass) under the {platform} UI mask (red = platform UI; "
                                       "amber = UI only when the post description runs long)")
                with contextlib.suppress(Exception):
                    _draw_ui_mask(p, platform, timeline.width, timeline.height)
            paths.append(p)
        return paths

    def captions_phone() -> list[Path]:
        """Phone-scale frames (540 px wide, about a phone's physical width): the hook page, the smallest page and
        the pages furthest from the doctrine's position prior, so size and placement are judged as seen."""
        p = d / "captions_phone.png"
        if p.exists():
            return [p]
        pages = timeline.captions
        if not pages:
            return []
        geo = {r["page"]: r for r in (geometry or {}).get("pages", [])}
        chosen: list[Any] = [pages[0]]

        def add(pg: Any) -> None:
            if pg is not None and all(pg.page_id != c.page_id for c in chosen) and len(chosen) < 4:
                chosen.append(pg)

        if geo:
            add(min(pages, key=lambda pg: geo.get(pg.page_id, {}).get("size_px", 1e9)))
            odd = [pg for pg in pages if geo.get(pg.page_id, {}).get("relation") not in ("below_chin", "no_face", None)]
            if odd:
                add(odd[len(odd) // 2])
            ui = [pg for pg in pages if geo.get(pg.page_id, {}).get("into_ui_px", 0) > 0.5]
            if ui:
                add(max(ui, key=lambda pg: geo[pg.page_id]["into_ui_px"]))
        add(pages[len(pages) // 2])
        items = []
        for pg in chosen:
            a, b = Fraction(pg.out_start), Fraction(pg.out_end)
            t = a + min((b - a) / 2, Fraction(8) / fps)  # the settled page (after the entry animation)
            t_us = round(float(Fraction(int(t * fps)) / fps) * 1_000_000) + 1
            text = pg.text or " ".join(w.text for w in pg.words)
            items.append(fr.FrameRequest(t_us=t_us, label=f"{pg.page_id} \"{text}\""))
        fr.contact_sheet(job, items, source=str(final), timeline=timeline, columns=len(items), thumb_w=540,
                         out_path=p, title=f"Caption pages at phone scale (540 px wide) under the {platform} UI "
                                           "mask: the hook page, the smallest page and pages off the position prior")
        with contextlib.suppress(Exception):
            _draw_ui_mask(p, platform, timeline.width, timeline.height)
        return [p]

    def inserts() -> list[Path]:
        if not timeline.inserts:
            return []
        p = d / "inserts.png"
        if not p.exists():
            by_id = {i.id: i for i in doc.inserts}
            items = []
            for ins in timeline.inserts:
                mid = (Fraction(ins.out_start) + Fraction(ins.out_end)) / 2
                t_us = round(float(Fraction(int(mid * fps)) / fps) * 1_000_000) + 1
                job_txt = by_id[ins.insert_id].job if ins.insert_id in by_id else ""
                items.append(fr.FrameRequest(t_us=t_us, label=f"{ins.insert_id} {ins.mode}: {job_txt}"))
            fr.contact_sheet(job, items, source=str(final), timeline=timeline, columns=4, thumb_w=240, out_path=p,
                             title="Middle frame of every insert")
        return [p]

    attempt("overview", overview)
    attempt("hook", hook)
    attempt("seams", seams)
    if timeline.captions:
        attempt("captions", captions)
        attempt("captions_phone", captions_phone)
    attempt("inserts", inserts)
    return out


def _expand_refs(refs: Iterable[str], doc: CutDocument | None, index: TakeIndex,
                 page_words: Mapping[str, list[str]] | None = None) -> set[str]:
    """Word IDs a set of refs points at (segments, inserts, texts, pages, gaps and ranges expanded)."""
    out: set[str] = set()
    pw = page_words or {}
    for r in refs:
        r = str(r).strip()
        m = _RANGE_RE.match(r)
        try:
            if m:
                out.update(index.word_ids(m.group(1), m.group(2)))
            elif r.startswith("w") and index.has_word(r):
                out.add(r)
            elif r.startswith("seg") and doc is not None:
                sg = doc.segment(r)
                out.update(index.word_ids(sg.from_word, sg.to_word))
            elif r.startswith("g"):
                g = index.gap(r)
                out.update(x for x in (g.after_word_id, g.before_word_id) if x)
            elif r.startswith("i") and doc is not None:
                ins = doc.insert(r)
                out.update(index.word_ids(ins.anchor_from_word, ins.anchor_to_word))
            elif r.startswith("t") and doc is not None:
                t = doc.text(r)
                out.update(index.word_ids(t.anchor_from_word, t.anchor_to_word))
            elif r.startswith("p"):
                out.update(pw.get(r, []))
            elif r.startswith("fx") and doc is not None:
                out.add(doc.sfx(r).anchor_word)
            elif r.startswith("s"):
                out.update(index.sentence(r).word_ids)
        except (KeyError, ValueError):
            continue
    return out


def _known_ref(r: str, doc: CutDocument | None, index: TakeIndex, page_ids: set[str]) -> bool:
    r = r.strip()
    if _RANGE_RE.match(r):
        m = _RANGE_RE.match(r)
        return bool(m and index.has_word(m.group(1)) and index.has_word(m.group(2)))
    try:
        if re.fullmatch(r"w\d{4,}", r):
            return index.has_word(r)
        if re.fullmatch(r"g\d{4,}", r):
            index.gap(r)
            return True
        if re.fullmatch(r"s\d{3,}", r):
            index.sentence(r)
            return True
        if re.fullmatch(r"c\d{2,}", r):
            index.cluster(r)
            return True
        if doc is not None:
            if re.fullmatch(r"seg\d{3,}", r):
                doc.segment(r)
                return True
            if re.fullmatch(r"i\d{3,}", r):
                doc.insert(r)
                return True
            if re.fullmatch(r"t\d{3,}", r):
                doc.text(r)
                return True
            if re.fullmatch(r"fx\d{3,}", r):
                doc.sfx(r)
                return True
        if re.fullmatch(r"p\d{3,}", r):
            return r in page_ids
    except (KeyError, ValueError):
        return False
    return False


def _caption_evidence(geo: Mapping[str, Any], page_words: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    """Caption geometry facts as evidence: pages off the doctrine's position prior (not just under the chin, or
    under it outside the gap window), pages reaching into the strict platform band, and pages shrunk below the
    style size (the page visibly changes size)."""
    ev: list[dict[str, Any]] = []
    pages = list(geo.get("pages") or [])
    if not pages:
        return ev
    lo, hi = (geo.get("priors") or {}).get("chin_gap_px", [40, 120])[:2]
    style = ((geo.get("summary") or {}).get("size_px") or {}).get("style") or 0
    for r in pages:
        pid = r["page"]
        ws = list(page_words.get(pid, []))
        rel = r.get("relation")
        if rel in ("above_head", "on_hair", "over_face", "over_lower_face"):
            ev.append({"kind": "caption_position", "area": "captions", "words": ws, "ids": [pid],
                       "text": f"{pid} sits {rel.replace('_', ' ')} (top y {r['top_px']:.0f}, chin y "
                               f"{r.get('chin_px', 0):.0f})"})
        elif rel == "below_chin" and not (lo - 0.5 <= r.get("chin_gap_px", lo) <= hi + 0.5):
            ev.append({"kind": "caption_position", "area": "captions", "words": ws, "ids": [pid],
                       "text": f"{pid} sits {r['chin_gap_px']:.0f} px under the chin (prior {lo:.0f}-{hi:.0f})"})
        if r.get("into_ui_px", 0) > 0.5:
            ev.append({"kind": "caption_ui", "area": "captions", "words": ws, "ids": [pid],
                       "text": f"{pid} reaches {r['into_ui_px']:.0f} px into the strict {geo.get('platform')} band"
                               + (f" and {r['past_floor_px']:.0f} px past the caption floor"
                                  if r.get("past_floor_px", 0) > 0.5 else "")})
        if style and r.get("size_px", style) < style * 0.9 - 0.5:
            ev.append({"kind": "caption_size", "area": "captions", "words": ws, "ids": [pid],
                       "text": f"{pid} renders at {r['size_px']:.0f} px (style {style:.0f} px; shrunk to fit)"})
    return ev


def _evidence(pk: Any, timeline: Timeline, doc: CutDocument, index: TakeIndex,
              page_words: Mapping[str, list[str]], geometry: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Metric evidence a critic note can be confirmed by: ``{kind, words: [...], area, text}``."""
    ev: list[dict[str, Any]] = []
    if pk is None:
        return _caption_evidence(geometry or {}, page_words)
    for c in pk.clicks:
        if c.click:
            ev.append({"kind": "click", "area": "audio", "words": [w for w in (c.left_word, c.right_word) if w],
                       "text": f"click at seam {c.seam} (margin {c.margin_db} dB)"})
    if pk.asr is not None and pk.asr.ran:
        lost = set(pk.asr.seam_damage) | {d.word_id for d in pk.asr.diffs if d.op == "deleted"}
        if lost:
            ev.append({"kind": "asr_lost", "area": "audio", "words": sorted(lost),
                       "text": f"words the render's ASR lost: {', '.join(sorted(lost)[:12])}"})
        if pk.asr.masked:
            ev.append({"kind": "masked", "area": "music", "words": list(pk.asr.masked),
                       "text": f"words masked by music/SFX: {', '.join(pk.asr.masked[:12])}"})
    for t in pk.text:
        if t.issues:
            ws = page_words.get(t.id, [])
            if not ws and t.id.startswith("t"):
                with contextlib.suppress(KeyError):
                    tx = doc.text(t.id)
                    ws = index.word_ids(tx.anchor_from_word, tx.anchor_to_word)
            ev.append({"kind": "text_placement", "area": "captions" if t.kind == "caption" else "text",
                       "words": ws, "ids": [t.id], "text": f"{t.id}: {', '.join(t.issues)}"})
    for c in pk.cuts:
        if c.inside_word or (c.inside_clause and c.kind != "pause_trim"):
            ev.append({"kind": "cut_inside_word" if c.inside_word else "cut_inside_clause", "area": "seams",
                       "words": [w for w in (c.left_word, c.right_word) if w],
                       "text": f"seam {c.seam} {'inside a word' if c.inside_word else 'inside a clause'}"
                               + (f", face shift {c.face_shift_px:.0f}px" if c.face_shift_px else "")})
        elif c.face_shift_px is not None and c.face_scale_ratio is not None and 8 <= c.face_shift_px <= 120 and \
                0.9 <= c.face_scale_ratio <= 1.12:
            ev.append({"kind": "face_jump", "area": "seams", "words": [w for w in (c.left_word, c.right_word) if w],
                       "text": f"seam {c.seam}: face shifts {c.face_shift_px:.0f}px at the same scale"})
    for item in pk.integrity.clipped:
        w = item.get("word_id") or item.get("id")
        if w:
            ev.append({"kind": "clipped_word", "area": "audio", "words": [w], "text": f"{w} not fully in its window"})
    L = pk.loudness
    if L is not None and (L.within_target is False or L.true_peak_ok is False):
        ev.append({"kind": "loudness", "area": "audio", "words": [], "global": True,
                   "text": f"loudness {L.integrated_lufs} LUFS, true peak {L.true_peak_dbtp} dBTP"})
    if pk.av is not None and pk.av.ok is False:
        ev.append({"kind": "av_sync", "area": "audio", "words": [], "global": True,
                   "text": "; ".join(pk.av.problems)[:300]})
    if pk.silence_under_speech:
        ws = sorted({w for s in pk.silence_under_speech for w in s.word_ids})
        ev.append({"kind": "silence", "area": "audio", "words": ws, "global": not ws,
                   "text": f"{len(pk.silence_under_speech)} digital-silence runs under speech"})
    for e in pk.unexpected_video_events:
        ev.append({"kind": f"video_{e.kind}", "area": "seams", "words": [], "global": True,
                   "text": f"{e.kind} frames {e.start_s:.2f}-{e.end_s:.2f} s"})
    for pg in timeline.captions:
        text = pg.text or " ".join(w.text for w in pg.words)
        dd = float(pg.out_end - pg.out_start)
        if dd > 0 and len(text) / dd > 20.0:
            ev.append({"kind": "caption_fast", "area": "captions", "words": [w.word_id for w in pg.words],
                       "ids": [pg.page_id], "text": f"{pg.page_id} {len(text) / dd:.0f} CPS ({dd:.2f} s)"})
    ev.extend(_caption_evidence(geometry or {}, page_words))
    if pk.pacing is not None:
        if pk.pacing.final_word_to_end_s is not None and pk.pacing.final_word_to_end_s > 0.8:
            ev.append({"kind": "lingering_end", "area": "ending", "words": [], "global": True,
                       "text": f"{pk.pacing.final_word_to_end_s:.2f} s from the last word to the end"})
        if pk.pacing.time_to_first_speech_s is not None and pk.pacing.time_to_first_speech_s > 0.6:
            ev.append({"kind": "late_start", "area": "hook", "words": [], "global": True,
                       "text": f"first speech at {pk.pacing.time_to_first_speech_s:.2f} s"})
        for lp in pk.pacing.long_pauses[:20]:
            ws = [w for w in (lp.get("after"), lp.get("before")) if isinstance(w, str) and w]
            ev.append({"kind": "long_pause", "area": "pacing", "words": ws,
                       "text": f"{lp.get('ms')} ms pause after {lp.get('after')} at {lp.get('at_s')} s"})
    return ev


def build_packet(job: Job, render_dir: str | os.PathLike[str], *, doc: CutDocument | None = None,
                 index: TakeIndex | None = None, settings: Settings | None = None, asr: bool | None = None,
                 sheets: bool = True, run_qa: bool = True) -> CritiquePacket:
    """Measure (or reuse the measurements of) one render and build everything the critics look at."""
    from studio.compile.models import Timeline
    from studio.qa.invariants import InvariantResult, evaluate_render, primary_final
    from studio.qa.metrics import MetricsPacket

    s = settings or get_settings()
    rd = Path(render_dir)
    index = index or job.load_index()
    timeline = Timeline.load(rd / "timeline.json")
    if doc is None or (timeline.doc_version is not None and doc.version != timeline.doc_version):
        doc = job.load_doc(timeline.doc_version)
    final = primary_final(rd, doc)
    if final is None:
        raise FileNotFoundError(f"{rd} has no final_*.mp4 to critique")
    inv_path = rd / "qa" / "invariants.json"
    results: list[dict[str, Any]] = []
    pk: MetricsPacket | None = None
    if inv_path.exists():
        with contextlib.suppress(Exception):
            data = json.loads(inv_path.read_text(encoding="utf-8"))
            if data.get("doc_version") in (None, doc.version):
                results = [InvariantResult.model_validate(r).to_dict() for r in data.get("results", [])]
    mp = rd / "qa" / f"metrics_{final.stem}.json"
    if mp.exists():
        with contextlib.suppress(Exception):
            pk = MetricsPacket.load(mp)
    if (not results or pk is None) and run_qa:
        run = evaluate_render(job, rd, settings=s, asr=asr)
        results = [r.to_dict() for r in run.results]
        pk = run.metrics or pk
    platform = final.stem.removeprefix("final_") if final.stem.startswith("final_") else "tiktok"
    page_words = {pg.page_id or f"p{k + 1:03d}": [w.word_id for w in pg.words]
                  for k, pg in enumerate(timeline.captions)}
    asr_text = None
    asr_note = "ASR round trip did not run (no ASR key): clipped-word checks rest on the click detector and timeline"
    if pk is not None and pk.asr is not None:
        if pk.asr.ran:
            asr_text = pk.asr.hypothesis_text
            asr_note = (f"ASR round trip of the render: WER {pk.asr.wer}, lost near seams: "
                        f"{', '.join(pk.asr.seam_damage) or 'none'}; masked: {', '.join(pk.asr.masked) or 'none'}")
        elif pk.asr.skipped_reason:
            asr_note = f"ASR round trip skipped: {pk.asr.skipped_reason}"
    metric_notes: list[dict[str, Any]] = []
    for r in results:
        if not r.get("passed"):
            layer = invariant_layer(r)
            metric_notes.append({"by": "metrics", "severity": "P0", "area": _inv_area(int(r.get("number", 0))),
                                 "refs": list(r.get("refs") or []),
                                 "text": f"Invariant {r.get('number')} failed ({r.get('name')}): {r.get('detail')}",
                                 "cause": layer, "confirmed_by": ["metrics"],
                                 "evidence": "hard gate (ARCHITECTURE §7)", "confidence": "high", "taste": False,
                                 "fix_layer": layer})
    geometry: dict[str, Any] = {}
    if timeline.captions:
        try:
            from studio.compile.captions import caption_geometry

            geometry = caption_geometry(timeline, index, platform=platform, settings=s,
                                        style_px=doc.captions.style.size_px if doc.captions is not None else None)
        except Exception as e:  # advisory: the critique still runs on frames
            trace_event(job, "critic_note", render=rd.name, note=f"caption geometry failed: {type(e).__name__}: {e}")
    packet = CritiquePacket(
        render_dir=rd, final=final, doc_version=doc.version, platform=platform,
        duration_s=round(float(timeline.duration), 3), passed=bool(results) and all(r.get("passed") for r in results),
        invariants=results, metrics=pk.summary() if pk is not None else {}, advice=list(pk.advice) if pk else [],
        transcript=render_transcript(timeline, index), asr_text=asr_text, asr_note=asr_note,
        evidence=_evidence(pk, timeline, doc, index, page_words, geometry), metric_notes=metric_notes,
        page_words=page_words, caption_geometry=geometry, plain_transcript=plain_transcript(timeline, index),
        id_map=id_map(doc, timeline, index), layers=render_layers(doc, timeline),
    )
    if sheets:
        packet.sheets = _make_sheets(job, rd, final, timeline, index, doc, platform, geometry)
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(_frames_dir(job, rd) / "packet.json", packet.to_json())
    return packet


def _inv_area(n: int) -> str:
    return {1: "audio", 2: "audio", 3: "other", 4: "story", 5: "broll", 6: "color", 7: "captions", 8: "audio",
            9: "audio", 10: "other"}.get(n, "other")


def invariant_layer(result: Mapping[str, Any]) -> str:
    """Which layer can fix a failed invariant, so no one trades creator content for a gate: the engine's own
    artefacts are reported as such ("no document op fixes this; do not cut words for it")."""
    n = int(result.get("number") or 0)
    detail = str(result.get("detail") or "")
    engine = ("engine/render layer: no document op fixes this and cutting or moving words does not help; report it "
              "and keep the creator's words")
    if n == 9:
        head = re.search(r"at 0\.0\d s", detail)
        if head:
            return ("engine/render layer (the file head: the encoder's priming zeros meet the first word; the compiler "
                    "lead-in and the ingest pre-roll own it): " + engine.split(": ", 1)[1])
        return ("audio layer: a seam or pad lands on a recording dropout; move that seam to a clean clause edge or "
                "keep room tone under it (never delete words to hide it)")
    if n in (2, 3, 6, 10):
        return engine
    if n == 1:
        return "cut layer: move the seam off the word edge, or restore the clipped word's sound (J/L, pads)"
    if n == 4:
        return "story layer: restore the pinned payoff/CTA words"
    if n == 5:
        return "inserts/audio layer: replace or remove the asset without a licence record"
    if n == 7:
        return "captions/text layer: re-page, resize or reposition the text (or reframe)"
    if n == 8:
        return "audio layer (loudness target, music level); if the document is at target, the engine's mastering"
    return "unknown layer"


# ============================================================================================ review copy
def make_review_copy(job: Job, render_dir: str | os.PathLike[str], *, max_bytes: int = 18_000_000) -> Path:
    """540x960 H.264 copy of the primary final with the spoken word ID and the frame number burned in (for the
    watcher: frame numbers make a video model's answers addressable)."""
    from studio.compile.models import Timeline
    from studio.qa.invariants import primary_final

    rd = Path(render_dir)
    out = _frames_dir(job, rd) / "review_ids.mp4"
    if out.exists():
        return out
    timeline = Timeline.load(rd / "timeline.json")
    final = primary_final(rd, None)
    if final is None:
        raise FileNotFoundError(f"{rd} has no final to copy")
    index = job.load_index()
    ass = _frames_dir(job, rd) / "review_ids.ass"
    ev = []

    def ts(t: float) -> str:
        cs = max(0, round(t * 100))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s_, c = divmod(rem, 100)
        return f"{h}:{m:02d}:{s_:02d}.{c:02d}"

    for wid, sp in timeline.word_map.items():
        if sp is None or not index.has_word(wid):
            continue
        txt = f"{wid} {index.word(wid).text}".replace("{", "(").replace("}", ")")
        ev.append(f"Dialogue: 0,{ts(float(sp.out_start))},{ts(max(float(sp.out_end), float(sp.out_start) + 0.05))},"
                  f"ID,,0,0,0,,{txt}")
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 540\nPlayResY: 960\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        "Style: ID,Menlo,22,&H0000FFFF,&H00000000,&H80000000,1,0,3,2,0,1,10,10,12\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(ev) + "\n",
        encoding="utf-8")
    fontfile = next((f for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                                 "/System/Library/Fonts/Menlo.ttc",
                                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if os.path.exists(f)), None)
    vf = "scale=540:960:flags=lanczos"
    esc = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf += f",subtitles=filename='{esc}'"
    if fontfile:
        ff = fontfile.replace(":", "\\:")
        vf += (f",drawtext=fontfile='{ff}':text='f%{{frame_num}}':x=10:y=10:fontsize=22:fontcolor=yellow:"
               "box=1:boxcolor=black@0.6")
    for crf in (28, 33, 38):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(final), "-vf", vf, "-c:v", "libx264",
               "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
               "-ac", "1", "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # drawtext/subtitles unavailable: fall back to a plain small copy
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(final), "-vf",
                   "scale=540:960:flags=lanczos", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-ac", "1", str(out)]
            subprocess.run(cmd, capture_output=True, check=True)
        if out.stat().st_size <= max_bytes:
            break
    return out


# ============================================================================================ prompts
_CRITIC_ROLE = """\
You are a critic for Yunicorn Studio, reviewing the rendered edit of a creator's talking-head video. You did not \
make this edit. You see frames cut from the final encode (with burned word IDs and timecodes), the render's \
transcript in output order with word IDs, the ASR transcript of the render when it ran, and the measurements \
from code (including where every caption page sits against the face and the platform bands, and how big it \
renders). You cannot hear audio: audio questions rest on the measurements.

Rules:
- Judge the render, not the plan. Defects before taste. "Ship it" is a valid verdict, and remove beats add when \
in doubt; never ask for an addition without naming its job.
- Every note is localized by IDs printed on the sheets or in the transcript (words w0001, gaps g0001, segments \
seg001, inserts i001, texts t001, caption pages p001, sfx fx001) and quotes the spoken words. Never give a time as \
the location. Name the claim type of every note (claim: clipped_word, click, level_jump, lost_or_masked_word, \
av_sync, loudness, black_or_flash, freeze, text_position, text_size, reading_speed, typo, jump_cut, dead_air, \
late_hook, lingering_end, story or other): measurements confirm a note only when they measure that claim.
- Severity: P0 = a defect any viewer would call broken (clipped/missing word, click, drop-out, flash/black frame, \
wrong b-roll subject, misspelled name or wrong number on screen, text over eyes/mouth/platform UI, lost payoff or \
CTA, words masked by music). P1 = a quality loss most viewers would feel (late or buried hook, dead stretch, \
visible jump mid-thought, insert on an emotional line, unreadable or hard-to-read caption page, captions placed \
where the eye must leave the face to find them, lingering ending). P2 = polish or taste (mark taste=true when a \
reasonable editor might choose otherwise).
- Captions are judged at phone size against the doctrine's priors, which the measurements quote: one line, the \
block's top edge just under the chin (40-120 px after framing), phrase pages 64-96 px font at 1080 wide, inside \
the platform band. Use the phone-scale sheet to judge size and readability. A position the doctrine does not list \
(on the hair or forehead, over the face) or text that shrinks page to page is a finding, not a style.
- At most about five notes, consolidated, ordered from the top of Murch's list down (emotion, story, rhythm, \
eye-trace, planarity, space). If more than five P1s remain, say once that the problem is upstream (the story cut).
- Distrust any preference for "more": busier is not better.
- Stills are samples: captions change page with a 2-frame gap and animate in, so one sampled frame without a
caption is not evidence of missing captions. Judge caption coverage on the caption sheet (one frame per page)
and the transcript."""

_NAIVE_ROLE = """\
You are watching a short vertical video for the first time, as a viewer scrolling a feed. You see frames of the \
final encode and read what is said (with word IDs). You know nothing about what the creator intended. Give at most \
three gut reactions (where attention left the speaker, where you wanted to swipe), and the one sentence you \
remember. Quote the words (with their IDs) when you point at a moment."""

#: Pairwise areas (critique.md "Compare pairwise", plus framing and on-screen text).
PAIRWISE_AREAS: tuple[str, ...] = ("story", "hook", "pacing", "seams", "framing", "captions", "text", "broll",
                                   "audio", "color", "ending")

_PAIRWISE_ROLE = """\
You compare two renders of the same creator's talking-head video, labelled A and B. Nothing tells you which one is \
newer and it does not matter. You see frames cut from each final encode (with burned word IDs), each version's \
caption pages under the platform UI mask and at phone size, side-by-side frames of A and B where code found them \
different, each render's transcript in output order with IDs, and measurements from code (including caption \
geometry); you cannot hear, so judge audio from the measurements only. Judge each area (story, hook, pacing, seams, \
framing, captions, text, broll, audio, color, ending): A, B or same. Then an overall verdict. Rules: differences \
below perception (a 40 ms pause, a 2% scale change) are "same"; "more edits" is not better; a version with a defect \
(clipped word, click, text over the face or UI, wrong b-roll, a caption that is hard to read at phone size or sits \
off the face's reading path) loses that area; prefer the version where attention stays on the speaker unless an \
addition clearly pays for itself. Look at the listed differences before deciding an area is "same". Call it \
"same" when you cannot tell."""


def _doctrine(name: str, settings: Settings | None, section: str | None = None) -> str:
    try:
        from studio.agent import skills as sk

        return sk.load_skill(name, section=section, settings=settings)
    except Exception:
        return ""


def _questions_of(name: str, settings: Settings | None) -> list[str]:
    txt = _doctrine(name, settings, "Critic questions")
    return [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in txt.splitlines() if re.match(r"^\d+\.\s", ln.strip())]


def critic_questions(settings: Settings | None = None) -> list[str]:
    qs = _questions_of("critique", settings)
    return qs or list(CRITIC_QUESTIONS)


#: Topic files whose critic questions join the rubric when their layer is in the render (critique.md step 5:
#: "the critic questions of every topic file used in this edit"): (layer, doctrine file, prefix).
TOPIC_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("captions", "captions-and-text", "K"), ("framing", "framing-and-zooms", "F"), ("broll", "broll", "R"),
    ("text", "transitions-and-graphics", "T"), ("music", "music", "M"), ("sfx", "sfx", "X"),
)


def rubric_questions(layers: Sequence[str], brief_rubric: Sequence[str] = (),
                     settings: Settings | None = None) -> list[str]:
    """The binary rubric: critique.md's questions (C), each present layer's topic-file questions (K captions,
    F framing, R b-roll, T graphics/text, M music, X sfx) and the brief's items (B)."""
    out = [f"C{k}. {q}" for k, q in enumerate(critic_questions(settings), start=1)]
    for layer, name, prefix in TOPIC_QUESTIONS:
        if layer in layers:
            out += [f"{prefix}{k}. {q}" for k, q in enumerate(_questions_of(name, settings), start=1)]
    out += [f"B{k}. {q}" for k, q in enumerate(brief_rubric, start=1)]
    return out


def _brief_text(doc: CutDocument) -> str:
    """The creator's intent (idea, hook promise, audience, CTA, vibe, style). The Director's own finishing plan
    (visual and sound plans) stays out: critics judge what the viewer gets, not conformance to the plan."""
    b = doc.brief
    if b is None:
        return "No brief recorded."
    parts = [f"Idea: {b.goal}" if b.goal else "", f"Hook promise: {b.hook}" if b.hook else "",
             f"Audience: {b.audience}" if b.audience else "", f"CTA: {b.cta}" if b.cta else "",
             f"Vibe: {b.vibe}" if b.vibe else ""]
    style = doc.style
    parts.append(f"Style: {style.primary or 'unspecified'}" + (f" + {', '.join(style.blend)}" if style.blend else ""))
    return "\n".join(p for p in parts if p)


def _packet_text(packet: CritiquePacket, *, with_metrics: bool = True, naive: bool = False) -> str:
    if naive:  # a first-time viewer: the words as heard, no cut markers, no version, no measurements
        return (f"A {packet.duration_s:.0f}-second vertical video. What is said, in order (word IDs for pointing "
                f"only):\n{packet.plain_transcript or packet.transcript}")
    lines = [f"Render {packet.render_dir.name} (document v{packet.doc_version}), {packet.duration_s:.1f} s, "
             f"platform {packet.platform}.", "Transcript of the render (output order; ‖ = cut; [0.45s] = pause):",
             packet.transcript]
    if packet.asr_text:
        lines += ["ASR transcript of the rendered audio (what a listener's ear-proxy heard):", packet.asr_text[:6000]]
    lines.append(packet.asr_note)
    if with_metrics:
        fails = packet.failures()
        lines.append("Invariants: " + ("all ten pass" if packet.passed else
                                       "; ".join(f"#{r.get('number')} {r.get('name')} FAILED: {r.get('detail')}"
                                                 for r in fails)[:3000]))
        if packet.caption_geometry:
            lines.append(packet.caption_text())
        lines.append("Measurements (code): " + packet.metrics_text())
        if packet.advice:
            lines.append("Metric advice (priors, not gates):\n" + "\n".join(f"- {a}" for a in packet.advice[:30]))
    return "\n".join(lines)


def _sheet_list(packet: CritiquePacket, names: Sequence[str]) -> list[tuple[str, Path]]:
    labels = {"overview": "Overview: a frame every ~2 s of the final", "hook": "Hook: 0-3 s every 0.25 s",
              "seams": "Seams: last OUT / first IN frame at every cut, with face shift", "captions":
              "Caption pages under the platform UI mask", "captions_phone":
              "Caption pages at phone scale (540 px wide) under the platform UI mask",
              "inserts": "Middle frame of every insert"}
    out = []
    for n in names:
        for k, p in enumerate(packet.sheets.get(n, []), start=1):
            out.append((f"{labels.get(n, n)} ({k})", p))
    return out


# ============================================================================================ critique
def _norm_note(n: CriticNote | Mapping[str, Any], by: str, *, doc: CutDocument | None, index: TakeIndex,
               page_ids: set[str], same_family: bool) -> dict[str, Any]:
    d = n.model_dump() if isinstance(n, BaseModel) else dict(n)
    refs = [str(r).strip() for r in d.get("refs") or [] if str(r).strip()]
    # IDs quoted in the text count as refs too (models often write them inline)
    refs += [m for m in _ID_RE.findall(str(d.get("text", ""))) if m not in refs]
    good = [r for r in refs if _known_ref(r, doc, index, page_ids)]
    bad = [r for r in refs if r not in good]
    area = str(d.get("area") or "other").lower().strip()
    if area not in AREAS:
        area = "other"
    out = {"by": by, "severity": d.get("severity", "P2"), "area": area, "refs": list(dict.fromkeys(good)),
           "text": str(d.get("text", "")).strip(), "cause": str(d.get("cause", "") or ""),
           "suggested_ops": str(d.get("suggested_ops", "") or ""), "evidence": str(d.get("evidence", "") or ""),
           "confidence": d.get("confidence", "medium"), "taste": bool(d.get("taste", False)), "confirmed_by": [],
           "same_family": same_family, "claim": str(d.get("claim") or "other").strip().lower()}
    if bad:
        out["evidence"] = (out["evidence"] + f" (unknown refs dropped: {', '.join(bad[:6])})").strip()
    if not good:
        out["unlocalized"] = True
    return out


#: metric evidence of one area can confirm notes of these areas (a click cannot confirm a pacing note)
_AREA_GROUPS: dict[str, frozenset[str]] = {
    "audio": frozenset({"audio", "music", "sfx", "seams"}),
    "music": frozenset({"music", "audio"}),
    "captions": frozenset({"captions", "text"}),
    "text": frozenset({"text", "captions"}),
    "seams": frozenset({"seams", "framing", "pacing"}),
    "pacing": frozenset({"pacing", "story"}),
    "ending": frozenset({"ending", "pacing"}),
    "hook": frozenset({"hook", "story"}),
}


def _metric_confirm(note: dict[str, Any], evidence: Sequence[dict[str, Any]], doc: CutDocument | None,
                    index: TakeIndex, page_words: Mapping[str, list[str]]) -> list[str]:
    """Metric kinds that confirm a note: the evidence measures the note's claim (:data:`_CLAIMS_FOR_KIND`; a note
    without a declared claim is read by its words), sits in the same area family, and — unless it is a whole-video
    measurement (late start, lingering end, loudness, A/V, black/frozen frames) — touches the same words or IDs (a
    whole-segment ref only counts through its edge words, so a note about "seg001" is not confirmed by any defect
    somewhere inside it)."""
    claims = note_claims(note)
    refs = list(note["refs"])
    edge_refs: list[str] = []
    for r in refs:
        if r.startswith("seg") and doc is not None:
            with contextlib.suppress(KeyError):
                sg = doc.segment(r)
                edge_refs += [sg.from_word, sg.to_word]
    words = _expand_refs([r for r in refs if not r.startswith("seg")] + edge_refs, doc, index, page_words)
    hits = []
    for e in evidence:
        allowed = _AREA_GROUPS.get(str(e.get("area")), frozenset({str(e.get("area"))}))
        if note["area"] not in allowed:
            continue
        if not claims & _CLAIMS_FOR_KIND.get(str(e.get("kind")), frozenset()):
            continue
        if e.get("global"):
            hits.append(f"metrics:{e['kind']}")
            continue
        ew = set(e.get("words") or [])
        if words & ew or set(e.get("ids") or []) & set(refs):
            hits.append(f"metrics:{e['kind']}")
    return list(dict.fromkeys(hits))


def _cross_confirm(notes: list[dict[str, Any]], doc: CutDocument | None, index: TakeIndex,
                   page_words: Mapping[str, list[str]]) -> None:
    """Notes of different critics that point at the same words confirm each other."""
    exp = [_expand_refs(n["refs"], doc, index, page_words) for n in notes]
    for i, a in enumerate(notes):
        for j, b in enumerate(notes):
            if i == j or a["by"].split(":")[0] == b["by"].split(":")[0]:
                continue
            if exp[i] & exp[j] and _SEV_RANK.get(b["severity"], 2) <= 1:
                tag = f"critic:{b['by']}"
                if tag not in a["confirmed_by"]:
                    a["confirmed_by"].append(tag)
                    if b.get("same_family"):
                        a["confirmed_same_family"] = True


def format_notes(notes: Sequence[Mapping[str, Any]]) -> str:
    """Notes as the Director reads them (one line each)."""
    lines = []
    for n in notes:
        refs = ",".join(n.get("refs") or [])
        conf = n.get("confirmed_by") or []
        conf_txt = f" [confirmed: {', '.join(conf)}]" if conf else ""
        if n.get("same_family") and conf:
            conf_txt += " (same model family as the Director: weigh lower)"
        unconf = f" (was {n['unconfirmed_severity']}, unconfirmed)" if n.get("unconfirmed_severity") else ""
        taste = " taste" if n.get("taste") else ""
        body = n.get("text", "")
        if n.get("cause"):
            body += f" → cause: {n['cause']}"
        if n.get("suggested_ops"):
            body += f" → try: {n['suggested_ops']}"
        if n.get("evidence"):
            body += f" → evidence: {n['evidence']}"
        lines.append(f"[{n.get('severity', 'P2')}{taste}][{n.get('area', 'other')}][{refs or 'unlocalized'}] "
                     f"({n.get('by', 'critic')}) {body}{unconf}{conf_txt}")
    return "\n".join(lines)


def _order(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    area_rank = {a: i for i, a in enumerate(AREAS)}
    return sorted(notes, key=lambda n: (_SEV_RANK.get(n.get("severity", "P2"), 2),
                                        area_rank.get(n.get("area", "other"), 99)))


def critique(job: Job, doc: CutDocument | None, index: TakeIndex | None, render_dir: str | os.PathLike[str], *,
             questions: Sequence[str] | None = None, settings: Settings | None = None,
             packet: CritiquePacket | None = None, panel: JudgePanel | None = None, label: str | None = None,
             naive: bool = True, confirm: bool = True, watch: bool = True, max_notes: int = 8) -> list[dict[str, Any]]:
    """Critic notes for one render: ``[{by, severity, area, refs, text, …, confirmed_by, same_family}]`` (see
    module docstring). Also saved to ``critique/<label or render>/notes.json``."""
    s = settings or get_settings()
    rd = Path(render_dir)
    index = index or job.load_index()
    packet = packet or build_packet(job, rd, doc=doc, index=index, settings=s)
    if doc is None or doc.version != packet.doc_version:
        doc = job.load_doc(packet.doc_version)
    panel = panel or default_panel(s)
    label = label or rd.name
    page_ids = set(packet.page_words)
    record: dict[str, Any] = {"round": label, "render": rd.name, "doc_version": packet.doc_version,
                              "panel": {"frame_judge": panel.frame_judge.label,
                                        "second_judge": panel.second_judge.label,
                                        "watcher": panel.watcher.label if panel.watcher else None,
                                        "notes": panel.notes}}
    notes: list[dict[str, Any]] = list(packet.metric_notes)
    errors: list[str] = []

    # 1 — naive pass: the words as heard (no segment IDs, cut markers or pauses) and the frames, nothing else
    naive_res: NaiveResult | None = None
    if naive:
        content = [_packet_text(packet, with_metrics=False, naive=True), *_image_parts(panel.frame_judge, _sheet_list(
            packet, ("overview", "hook")))]
        try:
            naive_res = _ask(panel.frame_judge, NaiveResult, _NAIVE_ROLE, content, job=job,
                             stage=f"critique:{label}:naive")  # type: ignore[assignment]
        except Exception as e:
            errors.append(f"naive pass failed: {map_provider_error(e).message}")
    record["naive"] = naive_res.model_dump() if naive_res is not None else None

    # 2 — rubric pass (frame judge): critique.md + the topic files of the layers present + the brief's items
    brief_rubric = list(doc.brief.rubric) if doc.brief is not None else []
    qs = rubric_questions(packet.layers, brief_rubric, s)
    text = [
        "BRIEF (the creator's intent):", _brief_text(doc),
        "IDS YOU CAN LOCALIZE BY (no plan: judge the render):", packet.id_map,
        _packet_text(packet),
    ]
    if naive_res is not None:
        text.append("A naive first-time viewer (fresh context, no brief) reported: reactions "
                    f"{naive_res.reactions}; swipe moments {naive_res.swipe_moments}; remembered sentence: "
                    f"\"{naive_res.remembered_sentence}\". A remembered sentence that differs from the brief's idea is "
                    "a story problem.")
    text.append("RUBRIC — answer each yes / no / cant_tell (cant_tell goes to a metric; do not guess), and set passed "
                "= whether that answer is the good outcome for the viewer (the questions differ in polarity):\n"
                + "\n".join(qs))
    if questions:
        text.append("THE DIRECTOR ASKS (answer each in `answers`, in order):\n"
                    + "\n".join(f"- {q}" for q in questions))
    text.append("Then write the notes (severity, area, claim, refs by ID, what the viewer experiences, likely cause, "
                "suggested ops, evidence, confidence, taste) and one or two moments that must survive (keep). "
                "verdict = ship_it when nothing above P2 remains.")
    record["rubric_questions"] = qs
    judge_res: JudgeResult | None = None
    content = ["\n\n".join(text), *_image_parts(panel.frame_judge, _sheet_list(
        packet, ("overview", "hook", "seams", "captions_phone", "captions", "inserts")))]
    instructions = _CRITIC_ROLE + "\n\nThe critique doctrine:\n" + _doctrine("critique", s)
    try:
        judge_res = _ask(panel.frame_judge, JudgeResult, instructions, content, job=job,
                         stage=f"critique:{label}:rubric")  # type: ignore[assignment]
    except Exception as e:
        errors.append(f"frame judge failed: {map_provider_error(e).message}")
    if judge_res is None:
        # no review happened: never "nothing to change". Nothing is cached as notes; the attempt is kept for the report
        record.update({"complete": False, "errors": errors, "notes": None, "metric_notes": list(packet.metric_notes)})
        with contextlib.suppress(Exception):
            from studio.jobs import write_json_atomic

            write_json_atomic(job.critique_dir / label / "unreviewed.json", record)
        trace_event(job, "critique", render=rd.name, label=label, complete=False, errors=errors)
        raise CritiqueUnavailable("the critics could not review this render: " + "; ".join(errors),
                                  record=record, metric_notes=list(packet.metric_notes))
    by_fj = f"frame_judge:{panel.frame_judge.label}"
    record["rubric"] = [r.model_dump() for r in judge_res.rubric]
    record["keep"] = judge_res.keep
    record["answers"] = judge_res.answers
    record["verdict"] = judge_res.verdict
    for n in judge_res.notes:
        notes.append(_norm_note(n, by_fj, doc=doc, index=index, page_ids=page_ids,
                                same_family=panel.frame_judge.same_family))

    # 3 — watcher (video + audio) when a Google key exists
    if watch and panel.watcher is not None:
        try:
            wres = _watch(job, packet, panel.watcher, questions=questions, doc=doc, settings=s, label=label)
            record["watcher"] = {"ran": True, "model": panel.watcher.label,
                                 "attention_left_speaker": wres.attention_left_speaker,
                                 "swipe_moments": wres.swipe_moments, "answers": wres.answers}
            for n in wres.notes:
                notes.append(_norm_note(n, f"watcher:{panel.watcher.label}", doc=doc, index=index, page_ids=page_ids,
                                        same_family=panel.watcher.same_family))
        except Exception as e:
            record["watcher"] = {"ran": False, "reason": f"watcher failed: {map_provider_error(e).message}"}
    else:
        record["watcher"] = {"ran": False, "reason": "no Google key: critique used frames + metrics only"}

    # 4 — confirmation rule
    critic_notes = [n for n in notes if n["by"] != "metrics"]
    confirm_notes(job, packet, panel, critic_notes, doc=doc, index=index, label=label, confirm=confirm,
                  errors=errors)
    notes = _order(notes)
    metric_part = [n for n in notes if n["by"] == "metrics"]
    rest = [n for n in notes if n["by"] != "metrics"][:max_notes]
    notes = _order(metric_part + rest)
    record["notes"] = notes
    record["errors"] = errors
    record["complete"] = True
    record["advice"] = packet.advice
    record["same_family_only"] = bool(panel.frame_judge.same_family and panel.second_judge.same_family
                                      and (panel.watcher is None or panel.watcher.same_family))
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / label / "notes.json", record)
        (job.critique_dir / label / "unreviewed.json").unlink(missing_ok=True)
    trace_event(job, "critique", render=rd.name, label=label, notes=len(notes),
                p0=sum(1 for n in notes if n["severity"] == "P0"), p1=sum(1 for n in notes if n["severity"] == "P1"),
                errors=errors or None)
    return notes


def confirm_notes(job: Job, packet: CritiquePacket, panel: JudgePanel, notes: list[dict[str, Any]], *,
                  doc: CutDocument | None, index: TakeIndex, label: str, confirm: bool = True,
                  errors: list[str] | None = None) -> None:
    """The confirmation rule (critique.md step 6) on critic notes, in place: metric evidence of the same claim, then
    agreement between critics, then a fresh-context second critic; an unconfirmed P0/P1 becomes P2 with
    ``unconfirmed_severity``."""
    for n in notes:
        n["confirmed_by"].extend(_metric_confirm(n, packet.evidence, doc, index, packet.page_words))
    _cross_confirm(notes, doc, index, packet.page_words)
    pending = [n for n in notes if _SEV_RANK.get(n["severity"], 2) <= 1 and not n["confirmed_by"]]
    if pending and confirm:
        try:
            _second_opinion(job, packet, panel.confirmer, pending, label=label)
        except Exception as e:
            if errors is not None:
                errors.append(f"confirmer failed: {map_provider_error(e).message}")
    for n in notes:
        if _SEV_RANK.get(n["severity"], 2) <= 1 and not n["confirmed_by"]:
            n["unconfirmed_severity"] = n["severity"]
            n["severity"] = "P2"


def _second_opinion(job: Job, packet: CritiquePacket, judge: Judge, pending: list[dict[str, Any]], *,
                    label: str) -> None:
    listing = "\n".join(f"{k}. [{n['severity']}][{n['area']}][{','.join(n['refs'])}] {n['text']}"
                        + (f" (evidence: {n['evidence']})" if n.get("evidence") else "")
                        for k, n in enumerate(pending))
    content = [_packet_text(packet) + "\n\nAnother critic raised these notes about this render. For each, answer "
               "confirmed = yes only if the frames, transcript or measurements show the problem; no if they show it "
               "is not there; cant_tell otherwise. Be strict: a note you cannot verify is not confirmed.\n" + listing,
               *_image_parts(judge, _sheet_list(packet, ("overview", "hook", "seams", "captions_phone", "captions",
                                                         "inserts")))]
    res = _ask(judge, ConfirmResult, _CRITIC_ROLE, content, job=job, stage=f"critique:{label}:confirm")
    for item in res.items:  # type: ignore[attr-defined]
        if 0 <= item.note < len(pending) and item.confirmed == "yes":
            n = pending[item.note]
            n["confirmed_by"].append(f"critic:{judge.label}")
            if judge.same_family:
                n["confirmed_same_family"] = True
            if item.reason:
                n["evidence"] = (n.get("evidence", "") + f" (confirmer: {item.reason})").strip()


def _watch(job: Job, packet: CritiquePacket, watcher: Judge, *, questions: Sequence[str] | None,
           doc: CutDocument, settings: Settings, label: str) -> WatchResult:
    """Gemini (video + audio) watches the review copy once at 1x with sound on."""
    from pydantic_ai import BinaryContent

    video = make_review_copy(job, packet.render_dir)
    prompt = (_packet_text(packet) + "\n\nBRIEF:\n" + _brief_text(doc) + "\n\nWatch the attached video once, straight "
              "through, at 1x with sound on. Every frame shows the spoken word ID bottom-left and the frame number "
              "top-left: localize everything by those word IDs. Report where attention left the speaker, where you "
              "wanted to swipe, any word lost or masked, any join that sounds abrupt, whether the music is noticed "
              "during speech, and any visual defect. Notes by ID with severity (P0/P1/P2).")
    if questions:
        prompt += "\nAnswer the Director's questions in `answers`:\n" + "\n".join(f"- {q}" for q in questions)
    content = [prompt, BinaryContent(data=video.read_bytes(), media_type="video/mp4")]
    return _ask(watcher, WatchResult, _CRITIC_ROLE, content, job=job, stage=f"critique:{label}:watch")  # type: ignore[return-value]


# ============================================================================================ pairwise
def metric_regressions(a: CritiquePacket, b: CritiquePacket) -> dict[str, list[str]]:
    """Measured regressions of each version against the other: ``{"a": [...], "b": [...]}`` (b's list names what
    got worse in b)."""

    def facts(p: CritiquePacket) -> dict[str, float]:
        m = p.metrics or {}
        asr = m.get("asr") or {}
        L = m.get("loudness") or {}
        return {
            "invariants_failed": float(len(p.failures())),
            "clicks": float(len(m.get("clicks") or [])),
            "asr_seam_damage": float(len(asr.get("seam_damage") or [])),
            "asr_missing": float(len(asr.get("missing") or [])),
            "text_issues": float(len(m.get("text_issues") or [])),
            "cuts_inside_words": float(len(m.get("cuts_inside_words") or [])),
            "clipped_words": float(len((m.get("integrity") or {}).get("clipped") or [])),
            "silence_under_speech": float(len(m.get("digital_silence_under_speech") or [])),
            "video_events": float(len(m.get("video_events") or [])),
            "loudness_off": float(abs((L.get("integrated_lufs") or -14.0) + 14.0) > 1.0) if L else 0.0,
        }

    fa, fb = facts(a), facts(b)
    out: dict[str, list[str]] = {"a": [], "b": []}
    for k in fa:
        if fb[k] > fa[k]:
            out["b"].append(f"{k} {fa[k]:g} → {fb[k]:g}")
        elif fa[k] > fb[k]:
            out["a"].append(f"{k} {fb[k]:g} → {fa[k]:g}")
    return out


def _version_block(p: CritiquePacket, tag: str, judge: Judge) -> list[Any]:
    inv_txt = "all pass" if p.passed else "FAILED: " + "; ".join(str(r.get("name")) for r in p.failures())
    head = (f"=== VERSION {tag} === {p.duration_s:.1f} s. Transcript (output order; ‖ = cut):\n{p.transcript}\n"
            f"{p.asr_note}\nInvariants: {inv_txt}\n"
            + (p.caption_text().replace("Caption geometry", f"Caption geometry of {tag}") + "\n"
               if p.caption_geometry else "")
            + f"Measurements: {p.metrics_text()[:3000]}")
    imgs = _image_parts(judge, [(f"{tag} — {lab}", path) for lab, path in _sheet_list(
        p, ("overview", "hook", "captions_phone", "captions", "seams", "inserts"))], budget=12)
    return [head, *imgs]


def _kept_ranges(ids: Sequence[str], index: TakeIndex) -> list[tuple[str, str]]:
    """Contiguous (source-order) runs of word IDs as (first, last)."""
    pos = sorted((index.word_pos(w), w) for w in ids if index.has_word(w))
    out: list[tuple[str, str]] = []
    for k, (p, w) in enumerate(pos):
        if out and k > 0 and p == pos[k - 1][0] + 1:
            out[-1] = (out[-1][0], w)
        else:
            out.append((w, w))
    return out


def version_diff(pa: CritiquePacket, pb: CritiquePacket, job: Job, index: TakeIndex) -> dict[str, Any]:
    """What differs between two renders, computed by code and phrased symmetrically (it never says which one is
    newer). Items are ``{kind, ids, a, b}`` (what each version has; ``None`` = absent) or ``{kind, only, ids,
    text}``: words heard in only one version, caption pages that differ (text, height, face relation, size),
    framing, inserts, texts, music, SFX and length. ``moments`` are word IDs present in both, near each difference
    (for side-by-side frames)."""
    from studio.compile.models import Timeline

    tla = Timeline.load(pa.render_dir / "timeline.json")
    tlb = Timeline.load(pb.render_dir / "timeline.json")
    da, db = job.load_doc(pa.doc_version), job.load_doc(pb.doc_version)
    ka = [w for s in tla.segments for w in s.word_ids if tla.word_map.get(w) is not None]
    kb = [w for s in tlb.segments for w in s.word_ids if tlb.word_map.get(w) is not None]
    sa, sb = set(ka), set(kb)
    items: list[dict[str, Any]] = []
    moments: list[str] = []
    both = [w for w in ka if w in sb]

    def pos(w: str) -> int:
        return index.word_pos(w) if index.has_word(w) else 0

    def near(w: str) -> None:
        if not both or not index.has_word(w):
            return
        m = min(both, key=lambda x: abs(pos(x) - pos(w)))
        if m not in moments:
            moments.append(m)

    for side, only in (("a", [w for w in ka if w not in sb]), ("b", [w for w in kb if w not in sa])):
        for a, b in _kept_ranges(only, index):
            txt = " ".join(index.word(w).display() for w in index.word_ids(a, b))[:90]
            items.append({"kind": "words", "only": side, "ids": f"{a}-{b}" if a != b else a, "text": f"\"{txt}\""})
            near(a)
    if [w for w in ka if w in sb] != [w for w in kb if w in sa]:
        items.append({"kind": "order", "ids": "", "a": "one order of the shared words", "b": "another order"})
    ga = {r["page"]: r for r in (pa.caption_geometry or {}).get("pages", [])}
    gb = {r["page"]: r for r in (pb.caption_geometry or {}).get("pages", [])}

    def pages_by_first(tl: Any, g: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        out = {}
        for pg in tl.captions:
            if pg.words:
                r = dict(g.get(pg.page_id) or {})
                r.setdefault("text", pg.text or " ".join(w.text for w in pg.words))
                r["page"] = pg.page_id
                r["last"] = pg.words[-1].word_id
                out[pg.words[0].word_id] = r
        return out

    def desc(r: Mapping[str, Any]) -> str:
        bits = [f"{r.get('page')} \"{str(r.get('text'))[:40]}\""]
        if "top_px" in r:
            bits.append(f"top y{float(r['top_px']):.0f}")
        if r.get("relation"):
            bits.append(str(r["relation"]) + (f" (gap {float(r['chin_gap_px']):.0f} px)" if "chin_gap_px" in r else ""))
        if "size_px" in r:
            bits.append(f"{float(r['size_px']):.0f} px")
        return ", ".join(bits)

    pa_first, pb_first = pages_by_first(tla, ga), pages_by_first(tlb, gb)
    cap_diffs = 0
    for w in sorted(set(pa_first) | set(pb_first), key=pos):
        ra, rb = pa_first.get(w), pb_first.get(w)
        if ra is not None and rb is not None:
            same = (ra.get("text") == rb.get("text") and ra.get("last") == rb.get("last")
                    and abs(float(ra.get("top_px", 0)) - float(rb.get("top_px", 0))) <= 12
                    and abs(float(ra.get("size_px", 0)) - float(rb.get("size_px", 0))) <= 2)
            if same:
                continue
        elif not (w in sa and w in sb):
            continue
        cap_diffs += 1
        if cap_diffs <= 10:
            items.append({"kind": "caption page", "ids": w, "a": desc(ra) if ra else "no page starts here",
                          "b": desc(rb) if rb else "no page starts here"})
            near(w)
    if cap_diffs > 10:
        items.append({"kind": "caption page", "ids": "", "a": f"{cap_diffs - 10} more pages differ", "b": "(same)"})
    fa = {s.from_word: (s.framing.scale if s.framing else 1.0) for s in da.segments}
    fb = {s.from_word: (s.framing.scale if s.framing else 1.0) for s in db.segments}
    for w in sorted(set(fa) & set(fb), key=pos):
        if abs(fa[w] - fb[w]) > 0.02:
            items.append({"kind": "framing", "ids": w, "a": f"x{fa[w]:.2f}", "b": f"x{fb[w]:.2f}"})
            near(w)
    for kind, xa, xb in (("insert", {(i.anchor_from_word, i.mode) for i in da.inserts},
                          {(i.anchor_from_word, i.mode) for i in db.inserts}),
                         ("text", {(t.anchor_from_word, t.kind, t.text) for t in da.texts},
                          {(t.anchor_from_word, t.kind, t.text) for t in db.texts})):
        for side, only in (("a", xa - xb), ("b", xb - xa)):
            for x in sorted(only):
                items.append({"kind": kind, "only": side, "ids": x[0], "text": ", ".join(str(v) for v in x[1:])})
                near(x[0])
    if (tla.music is None) != (tlb.music is None):
        items.append({"kind": "music", "only": "a" if tla.music is not None else "b", "ids": "",
                      "text": "a music bed"})
    elif tla.music is not None and tlb.music is not None and \
            abs(tla.music.level_lu_under_speech - tlb.music.level_lu_under_speech) > 0.5:
        items.append({"kind": "music", "ids": "", "a": f"{tla.music.level_lu_under_speech:+.0f} LU under speech",
                      "b": f"{tlb.music.level_lu_under_speech:+.0f} LU under speech"})
    if len(tla.sfx) != len(tlb.sfx):
        items.append({"kind": "sfx", "ids": "", "a": f"{len(tla.sfx)} cues", "b": f"{len(tlb.sfx)} cues"})
    if abs(pa.duration_s - pb.duration_s) >= 0.1:
        items.append({"kind": "length", "ids": "", "a": f"{pa.duration_s:.1f} s", "b": f"{pb.duration_s:.1f} s"})
    return {"items": items, "moments": moments[:4]}


def _diff_text(diff: Mapping[str, Any], swap: bool) -> str:
    """The diff for one presentation order (``swap``: the second render is shown as A)."""
    if not diff.get("items"):
        return "CODE FOUND NO DIFFERENCE BETWEEN A AND B beyond encoding noise."
    lab = {"a": "B" if swap else "A", "b": "A" if swap else "B"}
    lines = ["WHERE A AND B DIFFER (found by code; the frames after it show A and B side by side at these moments):"]
    for it in diff["items"]:
        ids = f" {it['ids']}" if it.get("ids") else ""
        if it.get("only"):
            lines.append(f"- [{it['kind']}]{ids} only in {lab[it['only']]}: {it.get('text', '')}")
        else:
            first, second = ("b", "a") if swap else ("a", "b")
            lines.append(f"- [{it['kind']}]{ids}: A {it.get(first)}; B {it.get(second)}")
    return "\n".join(lines)


def _diff_sheet(job: Job, first: CritiquePacket, second: CritiquePacket, moments: Sequence[str], out: Path
                ) -> Path | None:
    """A over B at each moment (a word present in both), phone-ish scale, under the platform UI mask."""
    if not moments:
        return None
    from PIL import Image, ImageDraw

    from studio.compile.models import Timeline
    from studio.perception import frames as fr

    rows = []
    for tag, pk in (("A", first), ("B", second)):
        tl = Timeline.load(pk.render_dir / "timeline.json")
        fps = Fraction(tl.fps)
        items = []
        for w in moments:
            sp = tl.word_map.get(w)
            if sp is None:
                continue
            t = Fraction(sp.out_start) + Fraction(4) / fps
            items.append(fr.FrameRequest(t_us=round(float(Fraction(int(t * fps)) / fps) * 1_000_000) + 1,
                                         label=f"{tag} at {w}"))
        if not items:
            return None
        p = out.with_name(out.stem + f"_{tag}.png")
        fr.contact_sheet(job, items, source=str(pk.final), timeline=tl, columns=len(items), thumb_w=300,
                         out_path=p, title=f"Version {tag}")
        with contextlib.suppress(Exception):
            _draw_ui_mask(p, pk.platform, tl.width, tl.height)
        rows.append(Image.open(p).convert("RGB"))
    w = max(r.width for r in rows)
    img = Image.new("RGB", (w, sum(r.height for r in rows) + 8), (20, 20, 20))
    y = 0
    for r in rows:
        img.paste(r, (0, y))
        y += r.height + 8
    ImageDraw.Draw(img)
    img.save(out, format="PNG")
    return out


def _judge_verdict(results: Sequence[str]) -> str:
    """One judge over both orders: ``a``/``b`` (won both), ``lean_a``/``lean_b`` (won one, "same" in the other),
    ``same`` (same in both: the judge abstains), ``split`` (flipped with the order: position bias, no information),
    ``error``."""
    if len(results) < 2 or "error" in results:
        return "error"
    r0, r1 = results[0], results[1]
    if r0 == r1:
        return r0 if r0 in ("a", "b") else "same"
    if {r0, r1} == {"a", "b"}:
        return "split"
    return "lean_" + (r0 if r0 in ("a", "b") else r1)


def resolved_notes(pa: CritiquePacket, pb: CritiquePacket, notes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Confirmed P0/P1 notes of ``a`` that are measurably gone in ``b``: a failed invariant that passes in ``b``, or
    metric evidence that confirmed the note (same kind, same IDs or words) and no longer appears in ``b``."""
    out = []
    b_fail = {int(r.get("number") or 0) for r in pb.failures()}
    for n in notes:
        if n.get("severity") not in ("P0", "P1"):
            continue
        if n.get("by") == "metrics":
            m = re.match(r"Invariant (\d+) failed", str(n.get("text", "")))
            if m and int(m.group(1)) not in b_fail:
                out.append(n.get("text", "")[:120])
            continue
        kinds = [c.split(":", 1)[1] for c in n.get("confirmed_by") or [] if str(c).startswith("metrics:")]
        if not kinds:
            continue
        refs = set(n.get("refs") or [])
        gone = True
        for k in kinds:
            for e in pb.evidence:
                if e.get("kind") != k:
                    continue
                if e.get("global") or refs & set(e.get("ids") or []) or refs & set(e.get("words") or []):
                    gone = False
        if gone:
            out.append(n.get("text", "")[:120])
    return out


def pairwise(job: Job, render_a: str | os.PathLike[str], render_b: str | os.PathLike[str], *,
             settings: Settings | None = None, panel: JudgePanel | None = None,
             packets: tuple[CritiquePacket, CritiquePacket] | None = None, label: str | None = None,
             fix_notes: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Position-swapped comparison by two judges, each shown the same evidence for both versions (frames, caption
    pages under the UI mask and at phone scale, caption geometry, measurements) plus a neutral code diff with
    side-by-side frames of the differences.

    Verdicts per judge (:func:`_judge_verdict`): a judge that says "same" in both orders abstains. ``winner`` is
    ``"b"`` when every judge prefers b in both orders and no metric regresses in b (the rule for taste changes). When
    the revision answers confirmed P0/P1 notes (``fix_notes``, the champion's), b also wins when no metric regresses,
    no judge prefers a (in either order) and either a judge prefers b or code measures a confirmed note as gone
    (``resolved``). ``"a"`` symmetrically (unanimous only); anything else is ``"tie"`` (the champion stays).
    ``incomplete`` is True when a judge's call failed after retries."""
    s = settings or get_settings()
    ra, rb = Path(render_a), Path(render_b)
    pa, pb = packets if packets is not None else (build_packet(job, ra, settings=s), build_packet(job, rb, settings=s))
    panel = panel or default_panel(s)
    index = job.load_index()
    diff: dict[str, Any] = {"items": [], "moments": []}
    try:
        diff = version_diff(pa, pb, job, index)
    except Exception as e:
        trace_event(job, "critic_note", note=f"version diff failed: {type(e).__name__}: {e}")
    tag = label or f"{ra.name}_vs_{rb.name}"
    sheets: dict[bool, Path | None] = {}
    for swap in (False, True):
        try:
            first, second = (pb, pa) if swap else (pa, pb)
            sheets[swap] = _diff_sheet(job, first, second, diff.get("moments") or [],
                                       job.critique_dir / "pairwise" / f"{tag}_{'ba' if swap else 'ab'}.png")
        except Exception as e:
            sheets[swap] = None
            trace_event(job, "critic_note", note=f"diff sheet failed: {type(e).__name__}: {e}")
    votes: list[dict[str, Any]] = []
    per_judge: dict[str, str] = {}
    verdicts: dict[str, str] = {}
    errors: list[str] = []
    for judge in panel.pairwise_judges():
        results: list[str] = []
        for order in (("a", "b"), ("b", "a")):
            swap = order == ("b", "a")
            first, second = (pb, pa) if swap else (pa, pb)
            extra: list[Any] = [_diff_text(diff, swap)]
            if sheets.get(swap) is not None:
                extra += _image_parts(judge, [("A (top row) and B (bottom row) at the moments that differ",
                                               sheets[swap])])  # type: ignore[list-item]
            content = [*_version_block(first, "A", judge), *_version_block(second, "B", judge), *extra,
                       "Compare A and B per area (" + ", ".join(PAIRWISE_AREAS) + "), then overall (A, B or same)."]
            try:
                res: PairwiseVerdict = _ask(judge, PairwiseVerdict, _PAIRWISE_ROLE, content, job=job,  # type: ignore[assignment]
                                            stage=f"pairwise:{ra.name}-{rb.name}")
            except Exception as e:
                errors.append(f"{judge.label} ({order[0]} first): {map_provider_error(e).message}")
                results.append("error")
                continue
            mapped = {"A": order[0], "B": order[1], "same": "tie"}[res.overall]
            results.append(mapped)
            votes.append({"judge": judge.role, "model": judge.label, "order": f"{order[0]} first",
                          "winner": mapped, "areas": [{"area": v.area, "winner": {"A": order[0], "B": order[1],
                                                                                  "same": "same"}[v.verdict],
                                                       "reason": v.reason} for v in res.areas],
                          "reason": res.reason, "same_family": judge.same_family})
        v = _judge_verdict(results)
        verdicts[judge.label] = v
        per_judge[judge.label] = v if v in ("a", "b") else "tie"
    regress = metric_regressions(pa, pb)
    incomplete = any(v == "error" for v in verdicts.values())
    resolved = resolved_notes(pa, pb, fix_notes or []) if fix_notes else []
    winner, rule = "tie", ""
    vals = list(verdicts.values())
    if vals and not incomplete:
        if all(v == "b" for v in vals) and not regress["b"]:
            winner, rule = "b", "unanimous"
        elif all(v == "a" for v in vals) and not regress["a"]:
            winner, rule = "a", "unanimous"
        elif fix_notes and not regress["b"] and not any(v in ("a", "lean_a") for v in vals) \
                and (any(v in ("b", "lean_b") for v in vals) or resolved):
            winner, rule = "b", "fix"
    out = {"winner": winner, "rule": rule, "a": ra.name, "b": rb.name, "a_doc_version": pa.doc_version,
           "b_doc_version": pb.doc_version, "per_judge": per_judge, "verdicts": verdicts, "votes": votes,
           "regressions": regress, "resolved": resolved, "diff": diff.get("items", []), "errors": errors,
           "incomplete": incomplete,
           "reason": f"{ra.name} vs {rb.name}: " + ", ".join(f"{k}: {v}" for k, v in verdicts.items())
                     + (f" ({rule} rule)" if rule else "")}
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / f"pairwise_{label or ra.name + '_vs_' + rb.name}.json", out)
    trace_event(job, "pairwise", a=ra.name, b=rb.name, winner=winner, rule=rule or None, per_judge=verdicts,
                errors=errors or None)
    return out


# ============================================================================================ final watch
def attention_check(job: Job, render_dir: str | os.PathLike[str], *, settings: Settings | None = None,
                    panel: JudgePanel | None = None, packet: CritiquePacket | None = None,
                    confirm: bool = True) -> dict[str, Any]:
    """The closing watch: one continuous pass over the champion asking only the attention question. Its P0/P1 notes
    go through the confirmation rule (metric evidence of the same claim, then a fresh-context second critic):
    ``confirmed`` lists the ones that stand (the loop opens one more round for them), ``refuted`` the rest (kept as
    P2 with ``unconfirmed_severity``)."""
    s = settings or get_settings()
    rd = Path(render_dir)
    packet = packet or build_packet(job, rd, settings=s)
    panel = panel or default_panel(s)
    doc = job.load_doc(packet.doc_version)
    index = job.load_index()
    content = [_packet_text(packet) + "\n\nOne continuous watch at 1x, sound on: does anything pull the eye or ear off "
               "the speaker without paying for it? Name the moments by ID (P0/P1 only for clear problems), with "
               "their claim type.",
               *_image_parts(panel.frame_judge, _sheet_list(packet, ("overview", "hook", "captions_phone",
                                                                    "inserts")))]
    out: dict[str, Any] = {"render": rd.name, "doc_version": packet.doc_version, "judge": panel.frame_judge.label}
    try:
        res: AttentionResult = _ask(panel.frame_judge, AttentionResult, _CRITIC_ROLE, content, job=job,  # type: ignore[assignment]
                                    stage="final_watch")
    except Exception as e:
        out.update({"ran": False, "pulls_attention": "cant_tell",
                    "verdict": f"final watch failed: {map_provider_error(e).message}", "notes": [], "confirmed": [],
                    "refuted": []})
    else:
        notes = [_norm_note(n, f"final_watch:{panel.frame_judge.label}", doc=doc, index=index,
                            page_ids=set(packet.page_words), same_family=panel.frame_judge.same_family)
                 for n in res.moments]
        serious = [n for n in notes if _SEV_RANK.get(n["severity"], 2) <= 1]
        errors: list[str] = []
        if serious:
            confirm_notes(job, packet, panel, serious, doc=doc, index=index, label="final_watch", confirm=confirm,
                          errors=errors)
        out.update({"ran": True, "pulls_attention": res.pulls_attention, "verdict": res.verdict, "notes": notes,
                    "confirmed": [n for n in serious if n["severity"] in ("P0", "P1")],
                    "refuted": [n for n in serious if n.get("unconfirmed_severity")], "errors": errors})
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / "final_watch.json", out)
    return out
