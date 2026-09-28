"""Critics (ARCHITECTURE §9, ``skills/editing/critique.md``): the critique packet, the frame judge, the
watcher, the confirmation rule and position-swapped pairwise comparison.

**The packet** (:func:`build_packet`) is built from the *encoded deliverable*, never the plan or the proxy:

* the metrics packet and the ten invariants (:func:`studio.qa.invariants.evaluate_render`, reused when the
  render was already checked), including the ASR round-trip transcript of the render when an ASR key exists;
* contact sheets cut from the final encode: the hook (0–3 s every 0.25 s), before/after pairs at every seam
  with the measured face shift, one frame per caption page drawn under the platform's UI mask (the muted
  pass), the middle frame of every insert, and an overview every ~2 s;
* the render's transcript in output order with word IDs, seams and pauses.

**Critique** (:func:`critique`):

1. *Naive pass* — a fresh context with no brief and no document: three gut reactions, where it wanted to
   swipe, and the one sentence it remembers (checked against the brief's idea).
2. *Rubric pass* — the frame judge (``Settings.critic_model``, ``claude-opus-5-5`` by default: a different
   model from the Director; a non-Anthropic family is used when its key exists) answers the binary rubric
   (critique.md's critic questions + the brief's rubric) and writes localized notes by ID with severity.
3. *Watcher* — Gemini watching the review copy (burned word IDs and frame numbers) with audio, when a Google
   key exists; otherwise the packet records that the critique ran on frames + metrics only.
4. *Confirmation* — a P0/P1 stands only when a metric or another critic confirms it: metric evidence on the
   same IDs first, then agreement between the frame judge and the watcher, then a fresh-context second
   critic answering yes/no per note (marked ``same_family`` when it shares the Director's family). An
   unconfirmed P0/P1 is kept as P2 with ``unconfirmed_severity``. Invariant failures become P0 notes
   confirmed by metrics.

**Pairwise** (:func:`pairwise`): two judges (the frame judge and a second house model, ``claude-sonnet-5``
by default) each compare the two renders in **both orders** (labels A/B, never which one is new), per area
then overall. A version wins only when it wins both orders for both judges and no metric regresses; any
disagreement is a tie, and a tie keeps the champion.

Every artefact is saved under ``critique/`` (notes and verdicts as JSON the report renders; images and raw
packets under ``critique/<render>/frames/``).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from studio.agent.providers import ModelSpec, capabilities_for, house_spec, map_provider_error, trace_event
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
    refs: list[str] = Field(default_factory=list, description="IDs the answer is about")
    evidence: str = ""


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


def default_panel(settings: Settings | None = None, *, director: ModelSpec | None = None) -> JudgePanel:
    """House judges: the frame judge from another family when its key exists (Google, then OpenAI), else the house
    critic model (``Settings.critic_model``); the second judge is another house model; the watcher is Gemini when
    a Google key exists."""
    s = settings or get_settings()
    dprov = director.provider if director is not None else s.director_provider
    notes: list[str] = []
    frame: Judge
    if dprov != "google" and s.has_key("google"):
        m = os.environ.get("STUDIO_CRITIC_GOOGLE_MODEL") or "gemini-3.1-pro"
        frame = Judge("frame_judge", ModelSpec(provider="google", model=m, api_key=s.key("google"), effort="high"))
    elif dprov != "openai" and s.has_key("openai"):
        m = os.environ.get("STUDIO_CRITIC_OPENAI_MODEL") or "gpt-6-astra"
        frame = Judge("frame_judge", ModelSpec(provider="openai", model=m, api_key=s.key("openai"), effort="high"))
    else:
        frame = Judge("frame_judge", house_spec("critic", settings=s))
        notes.append("no other model family available: the frame judge is a fresh-context house model (same_family)")
    frame.same_family = frame.provider == dprov
    second_model = os.environ.get("STUDIO_SECOND_JUDGE_MODEL") or SECOND_JUDGE_MODEL
    second = Judge("second_judge", ModelSpec(provider="anthropic", model=second_model, api_key=s.key("anthropic"),
                                             effort="max"))
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


def _ask(judge: Judge, output_cls: type[BaseModel], instructions: str, content: list[Any], *, job: Job | None,
         stage: str) -> BaseModel:
    agent = _agent(judge, output_cls, instructions, job, stage)
    res = agent.run_sync(content)
    return res.output


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

    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.invariants if not r.get("passed")]

    def to_json(self) -> dict[str, Any]:
        return {"render_dir": str(self.render_dir), "final": str(self.final), "doc_version": self.doc_version,
                "platform": self.platform, "duration_s": self.duration_s, "passed": self.passed,
                "invariants": self.invariants, "metrics": self.metrics, "advice": self.advice,
                "transcript": self.transcript, "asr_text": self.asr_text, "asr_note": self.asr_note,
                "sheets": {k: [str(p) for p in v] for k, v in self.sheets.items()}, "evidence": self.evidence,
                "metric_notes": self.metric_notes, "page_words": self.page_words}

    def metrics_text(self) -> str:
        return json.dumps(self.metrics, default=str)[:6000]


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
        bands = [(x, y, x + w, y + zone.top * sy), (x, y + h - zone.bottom * sy, x + w, y + h),
                 (x, y, x + zone.left * sx, y + h), (x + w - zone.right * sx, y, x + w, y + h)]
        for b in bands:
            dr.rectangle(b, fill=(255, 30, 30, 95))
        # the text-safe band itself, outlined
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
                 platform: str) -> dict[str, list[Path]]:
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
                                 title=f"Caption pages (muted pass) under the {platform} UI mask (red = platform UI)")
                with contextlib.suppress(Exception):
                    _draw_ui_mask(p, platform, timeline.width, timeline.height)
            paths.append(p)
        return paths

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


def _evidence(pk: Any, timeline: Timeline, doc: CutDocument, index: TakeIndex,
              page_words: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    """Metric evidence a critic note can be confirmed by: ``{kind, words: [...], area, text}``."""
    ev: list[dict[str, Any]] = []
    if pk is None:
        return ev
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
            metric_notes.append({"by": "metrics", "severity": "P0", "area": _inv_area(int(r.get("number", 0))),
                                 "refs": list(r.get("refs") or []),
                                 "text": f"Invariant {r.get('number')} failed ({r.get('name')}): {r.get('detail')}",
                                 "confirmed_by": ["metrics"], "evidence": "hard gate (ARCHITECTURE §7)",
                                 "confidence": "high", "taste": False})
    packet = CritiquePacket(
        render_dir=rd, final=final, doc_version=doc.version, platform=platform,
        duration_s=round(float(timeline.duration), 3), passed=bool(results) and all(r.get("passed") for r in results),
        invariants=results, metrics=pk.summary() if pk is not None else {}, advice=list(pk.advice) if pk else [],
        transcript=render_transcript(timeline, index), asr_text=asr_text, asr_note=asr_note,
        evidence=_evidence(pk, timeline, doc, index, page_words), metric_notes=metric_notes, page_words=page_words,
    )
    if sheets:
        packet.sheets = _make_sheets(job, rd, final, timeline, index, doc, platform)
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(_frames_dir(job, rd) / "packet.json", packet.to_json())
    return packet


def _inv_area(n: int) -> str:
    return {1: "audio", 2: "audio", 3: "other", 4: "story", 5: "broll", 6: "color", 7: "captions", 8: "audio",
            9: "audio", 10: "other"}.get(n, "other")


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
from code. You cannot hear audio: audio questions rest on the measurements.

Rules:
- Judge the render, not the plan. Defects before taste. "Ship it" is a valid verdict, and remove beats add when \
in doubt; never ask for an addition without naming its job.
- Every note is localized by IDs printed on the sheets or in the transcript (words w0001, gaps g0001, segments \
seg001, inserts i001, texts t001, caption pages p001, sfx fx001) and quotes the spoken words. Never give a time as \
the location.
- Severity: P0 = a defect any viewer would call broken (clipped/missing word, click, drop-out, flash/black frame, \
wrong b-roll subject, misspelled name or wrong number on screen, text over eyes/mouth/platform UI, lost payoff or \
CTA, words masked by music). P1 = a quality loss most viewers would feel (late or buried hook, dead stretch, \
visible jump mid-thought, insert on an emotional line, unreadable caption page, lingering ending). P2 = polish or \
taste (mark taste=true when a reasonable editor might choose otherwise).
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

_PAIRWISE_ROLE = """\
You compare two renders of the same creator's talking-head video, labelled A and B. Nothing tells you which one is \
newer and it does not matter. You see frames cut from each final encode (with burned word IDs), each render's \
transcript in output order with IDs, and measurements from code; you cannot hear, so judge audio from the \
measurements only. Judge each area (story, hook, pacing, seams, captions, broll, audio, color, ending): A, B or \
same. Then an overall verdict. Rules: differences below perception (a 40 ms pause, a 2% scale change) are "same"; \
"more edits" is not better; a version with a defect (clipped word, click, text over the face or UI, wrong b-roll) \
loses that area; prefer the version where attention stays on the speaker unless an addition clearly pays for \
itself. Call it "same" when you cannot tell."""


def _doctrine(name: str, settings: Settings | None, section: str | None = None) -> str:
    try:
        from studio.agent import skills as sk

        return sk.load_skill(name, section=section, settings=settings)
    except Exception:
        return ""


def critic_questions(settings: Settings | None = None) -> list[str]:
    txt = _doctrine("critique", settings, "Critic questions")
    qs = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in txt.splitlines() if re.match(r"^\d+\.\s", ln.strip())]
    return qs or list(CRITIC_QUESTIONS)


def _brief_text(doc: CutDocument) -> str:
    b = doc.brief
    if b is None:
        return "No brief recorded."
    parts = [f"Idea: {b.goal}" if b.goal else "", f"Hook promise: {b.hook}" if b.hook else "",
             f"Audience: {b.audience}" if b.audience else "", f"CTA: {b.cta}" if b.cta else "",
             f"Vibe: {b.vibe}" if b.vibe else "", f"Visual plan: {b.visual_plan}" if b.visual_plan else "",
             f"Sound plan: {b.sound_plan}" if b.sound_plan else "",
             ("Don'ts: " + "; ".join(b.donts)) if b.donts else "", b.text]
    style = doc.style
    parts.append(f"Style: {style.primary or 'unspecified'}" + (f" + {', '.join(style.blend)}" if style.blend else ""))
    return "\n".join(p for p in parts if p)


def _packet_text(packet: CritiquePacket, *, with_metrics: bool = True) -> str:
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
        lines.append("Measurements (code): " + packet.metrics_text())
        if packet.advice:
            lines.append("Metric advice (priors, not gates):\n" + "\n".join(f"- {a}" for a in packet.advice[:30]))
    return "\n".join(lines)


def _sheet_list(packet: CritiquePacket, names: Sequence[str]) -> list[tuple[str, Path]]:
    labels = {"overview": "Overview: a frame every ~2 s of the final", "hook": "Hook: 0-3 s every 0.25 s",
              "seams": "Seams: last OUT / first IN frame at every cut, with face shift", "captions":
              "Caption pages under the platform UI mask", "inserts": "Middle frame of every insert"}
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
           "same_family": same_family}
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
    """Metric kinds that confirm a note: same area family and the same words (a whole-segment ref only counts
    through its edge words, so a note about "seg001" is not confirmed by any defect somewhere inside it)."""
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

    # 1 — naive pass
    naive_res: NaiveResult | None = None
    if naive:
        content = [_packet_text(packet, with_metrics=False), *_image_parts(panel.frame_judge, _sheet_list(
            packet, ("overview", "hook")))]
        try:
            naive_res = _ask(panel.frame_judge, NaiveResult, _NAIVE_ROLE, content, job=job,
                             stage=f"critique:{label}:naive")  # type: ignore[assignment]
        except Exception as e:
            errors.append(f"naive pass failed: {map_provider_error(e).message}")
    record["naive"] = naive_res.model_dump() if naive_res is not None else None

    # 2 — rubric pass (frame judge)
    rubric = critic_questions(s)
    brief_rubric = list(doc.brief.rubric) if doc.brief is not None else []
    qs = [f"C{k}. {q}" for k, q in enumerate(rubric, start=1)] + [f"B{k}. {q}" for k, q in
                                                                  enumerate(brief_rubric, start=1)]
    from studio.agent.tools import summarize_doc

    text = [
        "BRIEF (the creator's intent as the Director wrote it):", _brief_text(doc),
        "DOCUMENT SUMMARY:", summarize_doc(doc, index, job=job, validation=False),
        _packet_text(packet),
    ]
    if naive_res is not None:
        text.append("A naive first-time viewer (fresh context, no brief) reported: reactions "
                    f"{naive_res.reactions}; swipe moments {naive_res.swipe_moments}; remembered sentence: "
                    f"\"{naive_res.remembered_sentence}\". A remembered sentence that differs from the brief's idea is "
                    "a story problem.")
    text.append("RUBRIC — answer each yes / no / cant_tell (cant_tell goes to a metric; do not guess):\n"
                + "\n".join(qs))
    if questions:
        text.append("THE DIRECTOR ASKS (answer each in `answers`, in order):\n"
                    + "\n".join(f"- {q}" for q in questions))
    text.append("Then write the notes (severity, area, refs by ID, what the viewer experiences, likely cause, "
                "suggested ops, evidence, confidence, taste) and one or two moments that must survive (keep). "
                "verdict = ship_it when nothing above P2 remains.")
    judge_res: JudgeResult | None = None
    content = ["\n\n".join(text), *_image_parts(panel.frame_judge, _sheet_list(
        packet, ("overview", "hook", "seams", "captions", "inserts")))]
    instructions = _CRITIC_ROLE + "\n\nThe critique doctrine:\n" + _doctrine("critique", s)
    try:
        judge_res = _ask(panel.frame_judge, JudgeResult, instructions, content, job=job,
                         stage=f"critique:{label}:rubric")  # type: ignore[assignment]
    except Exception as e:
        errors.append(f"frame judge failed: {map_provider_error(e).message}")
    by_fj = f"frame_judge:{panel.frame_judge.label}"
    if judge_res is not None:
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
    for n in critic_notes:
        n["confirmed_by"].extend(_metric_confirm(n, packet.evidence, doc, index, packet.page_words))
    _cross_confirm(critic_notes, doc, index, packet.page_words)
    pending = [n for n in critic_notes if _SEV_RANK.get(n["severity"], 2) <= 1 and not n["confirmed_by"]]
    if pending and confirm:
        try:
            _second_opinion(job, packet, panel.confirmer, pending, label=label)
        except Exception as e:
            errors.append(f"confirmer failed: {map_provider_error(e).message}")
    for n in critic_notes:
        if _SEV_RANK.get(n["severity"], 2) <= 1 and not n["confirmed_by"]:
            n["unconfirmed_severity"] = n["severity"]
            n["severity"] = "P2"
    notes = _order(notes)
    metric_part = [n for n in notes if n["by"] == "metrics"]
    rest = [n for n in notes if n["by"] != "metrics"][:max_notes]
    notes = _order(metric_part + rest)
    record["notes"] = notes
    record["errors"] = errors
    record["advice"] = packet.advice
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / label / "notes.json", record)
    trace_event(job, "critique", render=rd.name, label=label, notes=len(notes),
                p0=sum(1 for n in notes if n["severity"] == "P0"), p1=sum(1 for n in notes if n["severity"] == "P1"),
                errors=errors or None)
    return notes


def _second_opinion(job: Job, packet: CritiquePacket, judge: Judge, pending: list[dict[str, Any]], *,
                    label: str) -> None:
    listing = "\n".join(f"{k}. [{n['severity']}][{n['area']}][{','.join(n['refs'])}] {n['text']}"
                        + (f" (evidence: {n['evidence']})" if n.get("evidence") else "")
                        for k, n in enumerate(pending))
    content = [_packet_text(packet) + "\n\nAnother critic raised these notes about this render. For each, answer "
               "confirmed = yes only if the frames, transcript or measurements show the problem; no if they show it "
               "is not there; cant_tell otherwise. Be strict: a note you cannot verify is not confirmed.\n" + listing,
               *_image_parts(judge, _sheet_list(packet, ("overview", "hook", "seams", "captions", "inserts")))]
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
            f"{p.asr_note}\nInvariants: {inv_txt}\nMeasurements: {p.metrics_text()[:3000]}")
    imgs = _image_parts(judge, [(f"{tag} — {lab}", path) for lab, path in _sheet_list(p, ("overview", "hook",
                                                                                            "seams", "inserts"))],
                        budget=8)
    return [head, *imgs]


def pairwise(job: Job, render_a: str | os.PathLike[str], render_b: str | os.PathLike[str], *,
             settings: Settings | None = None, panel: JudgePanel | None = None,
             packets: tuple[CritiquePacket, CritiquePacket] | None = None, label: str | None = None) -> dict[str, Any]:
    """Position-swapped comparison by two judges. ``winner`` is ``"b"`` only if both judges prefer b in both orders
    and no metric regresses in b (``"a"`` symmetrically); anything else is ``"tie"`` (the champion stays)."""
    s = settings or get_settings()
    ra, rb = Path(render_a), Path(render_b)
    pa, pb = packets if packets is not None else (build_packet(job, ra, settings=s), build_packet(job, rb, settings=s))
    panel = panel or default_panel(s)
    votes: list[dict[str, Any]] = []
    per_judge: dict[str, str] = {}
    errors: list[str] = []
    for judge in panel.pairwise_judges():
        results: list[str] = []
        for order in (("a", "b"), ("b", "a")):
            first, second = (pa, pb) if order == ("a", "b") else (pb, pa)
            content = [*_version_block(first, "A", judge), *_version_block(second, "B", judge),
                       "Compare A and B per area, then overall (A, B or same)."]
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
        per_judge[judge.label] = results[0] if len(results) == 2 and results[0] == results[1] and \
            results[0] in ("a", "b") else "tie"
    regress = metric_regressions(pa, pb)
    winner = "tie"
    if per_judge and all(v == "b" for v in per_judge.values()) and not regress["b"]:
        winner = "b"
    elif per_judge and all(v == "a" for v in per_judge.values()) and not regress["a"]:
        winner = "a"
    out = {"winner": winner, "a": ra.name, "b": rb.name, "a_doc_version": pa.doc_version,
           "b_doc_version": pb.doc_version, "per_judge": per_judge, "votes": votes, "regressions": regress,
           "errors": errors, "reason": f"{ra.name} vs {rb.name}: " + ", ".join(f"{k}: {v}" for k, v in
                                                                          per_judge.items())}
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / f"pairwise_{label or ra.name + '_vs_' + rb.name}.json", out)
    trace_event(job, "pairwise", a=ra.name, b=rb.name, winner=winner, per_judge=per_judge, errors=errors or None)
    return out


# ============================================================================================ final watch
def attention_check(job: Job, render_dir: str | os.PathLike[str], *, settings: Settings | None = None,
                    panel: JudgePanel | None = None, packet: CritiquePacket | None = None) -> dict[str, Any]:
    """The closing watch: one continuous pass over the champion asking only the attention question."""
    s = settings or get_settings()
    rd = Path(render_dir)
    packet = packet or build_packet(job, rd, settings=s)
    panel = panel or default_panel(s)
    doc = job.load_doc(packet.doc_version)
    index = job.load_index()
    content = [_packet_text(packet) + "\n\nOne continuous watch at 1x, sound on: does anything pull the eye or ear off "
               "the speaker without paying for it? Name the moments by ID (P0/P1 only for clear problems).",
               *_image_parts(panel.frame_judge, _sheet_list(packet, ("overview", "hook", "inserts")))]
    out: dict[str, Any] = {"render": rd.name, "doc_version": packet.doc_version, "judge": panel.frame_judge.label}
    try:
        res: AttentionResult = _ask(panel.frame_judge, AttentionResult, _CRITIC_ROLE, content, job=job,  # type: ignore[assignment]
                                    stage="final_watch")
        out.update({"pulls_attention": res.pulls_attention, "verdict": res.verdict,
                    "notes": [_norm_note(n, f"final_watch:{panel.frame_judge.label}", doc=doc, index=index,
                                         page_ids=set(packet.page_words), same_family=panel.frame_judge.same_family)
                              for n in res.moments]})
    except Exception as e:
        out.update({"pulls_attention": "cant_tell", "verdict": f"final watch failed: {map_provider_error(e).message}",
                    "notes": []})
    with contextlib.suppress(Exception):
        from studio.jobs import write_json_atomic

        write_json_atomic(job.critique_dir / "final_watch.json", out)
    return out
