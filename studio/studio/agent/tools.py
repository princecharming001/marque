"""Director tools (ARCHITECTURE §9): an :class:`EditSession` plus pydantic-ai tools bound to it.

The Director perceives the take only through these tools and changes the edit only through the op
tools, which call :func:`studio.doc.ops.apply_ops` (the one writer). Every tool addresses media by ID
(words ``w0001``, gaps ``g0001``, sentences ``s001``, clusters ``c01``, segments ``seg001``); no tool
accepts a time as an edit coordinate.

Tools (all return compact text unless noted; bad input returns an ``ERROR:`` line the model can act on
instead of raising, so one typo never ends a run):

=================== ================================================================================
get_overview        media, measured delivery energy, audio health, face coverage, document state
get_transcript      ``full`` (every word ID + gaps), ``compact``, ``cut`` (current story + checks), ``removed``
get_words           per-word detail over an ID range (times, confidence, emphasis, prosody, cut status)
get_sentences       sentences with ranges, completeness, retake cluster and cut status
get_clusters        retake clusters with every take's text, duration, fillers, gaze events, cut status
get_gaps            pauses/breaths/silences ≥ ``min_ms`` with snap energy and where they sit in the cut
get_prosody         z-scored f0 / intensity / duration and emphasis for word IDs or ``wA-wB`` ranges
get_visual_events   blinks, look-aways, reading, face loss, mapped onto the words they cover
view_frames         **images**: contact sheets (items) and filmstrips with waveform (ranges), burned IDs
list_assets         registered b-roll / music / SFX assets (IDs usable in ops) with licences
load_skill          a doctrine topic file (optionally one section)
<family>_ops        one tool per op family (cut, framing, inserts, captions, audio, color, meta) whose
                    input schema is that family's op union; returns per-op results + a doc summary
apply_ops           the same for a mixed batch (atomic "one change → one version" across families)
render_preview      callback slot: render the current document (preview quality) and report
request_critique    callback slot: ask the critics localized questions about the latest render
ask_creator         callback slot: ask the creator (returns "no answer" in batch mode)
=================== ================================================================================

Design notes
------------
* **Stable tool list, runtime stage gating.** Claude Fable 5.1 / Opus 5.5 bind thinking blocks to
  the conversation prefix, so adding or removing tools between Director stages would invalidate
  earlier reasoning. The session therefore exposes every op family for the whole conversation and
  rejects ops outside the current stage's families (``STAGE_FAMILIES``) with a readable reason.
* **Op tools are non-strict** (the provider's strict grammar budget is too small for the op unions,
  see :data:`studio.doc.ops.DEFAULT_STRICT_FAMILIES`); :func:`~studio.doc.ops.apply_ops` validates every
  op and its rejection reasons let the model retry. Op tools are ``sequential`` (never run in parallel
  with another tool call) and the session serializes writes with a lock.
* **Frames the model can actually read.** Contact sheets are laid out so that no image exceeds the
  model's safe edge (2000 px for Claude once a request holds more than 20 images; Claude 4.7+ sees up to
  2576 px natively) or its visual-token budget (4784 28-px patches on Claude 4.7+, 1568 before), so the
  API never resamples the burned IDs; anything still too large is downscaled here with Lanczos. Images
  are returned inside the tool result, PNG when small, otherwise 4:4:4 JPEG q≈92. With an
  ``image_uploader`` (:func:`studio.agent.providers.make_image_uploader`, Anthropic Files API) each sheet
  is uploaded once and referenced by ``file_id``, so a long conversation is not capped by the 32 MB
  request size; without one (or if an upload fails) images go inline under a byte budget. Each sheet is
  followed by the measured face/eye/gaze values at those instants; models without vision get only the
  numbers.
* **One tool list per session.** :meth:`EditSession.tools` builds the tools once per argument set and
  reuses the same objects, so the tool definitions (part of the cached, thinking-bound prompt prefix)
  stay byte-identical for the whole conversation even while the doctrine files change on disk.
* **Everything is traced.** Each tool call appends a redacted ``tool_call`` record (name, stage, args
  summary, ok/error, latency, output size, images) to ``trace.jsonl``; model calls are traced by
  :class:`studio.agent.providers.TracedModel`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import math
import re
import threading
import time
from bisect import bisect_left
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from studio.agent.providers import Capabilities, capabilities_for, redact_secrets, trace_event, visual_tokens
from studio.config import get_settings
from studio.timebase import format_us

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai import Tool

    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.doc.ops import OpResult
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "OpToolMode", "PreviewResult", "ApplyOutcome", "EditSession", "ToolList", "build_tools", "summarize_doc",
    "TOOL_NAMES", "QUERY_TOOL_NAMES", "CALLBACK_TOOL_NAMES", "STAGES",
]

OpToolMode = Literal["families", "apply_ops", "both"]

QUERY_TOOL_NAMES: tuple[str, ...] = (
    "get_overview", "get_transcript", "get_words", "get_sentences", "get_clusters", "get_gaps", "get_prosody",
    "get_visual_events", "view_frames", "list_assets", "load_skill",
)
CALLBACK_TOOL_NAMES: tuple[str, ...] = ("render_preview", "request_critique", "ask_creator")
_FAMILY_ORDER = ("cut", "framing", "inserts", "captions", "audio", "color", "meta")
TOOL_NAMES: tuple[str, ...] = (*QUERY_TOOL_NAMES, *(f"{f}_ops" for f in _FAMILY_ORDER), "apply_ops",
                               *CALLBACK_TOOL_NAMES)

#: Director stages (ARCHITECTURE §9). ``None``/``chat``/``finalize`` allow every op family.
STAGES: tuple[str, ...] = ("brief", "story", "fine_cut", "finishing", "finalize", "chat")

_ITEM_RE = re.compile(r"^(w\d{4,}|g\d{4,}|s\d{3,})(:(start|mid|end))?$")
_RANGE_RE = re.compile(r"^\s*([wsg]\d{3,}|seg\d{3,})\s*(?:-|\.\.|–|—|to)\s*([wsg]\d{3,}|seg\d{3,})\s*$")
_MAX_ITEMS = 48
_MAX_RANGES = 8
_MAX_WORDS_PER_CALL = 400
_TOOL_RETRIES = 5
_PNG_MAX_BYTES = 1_500_000
_DEFAULT_IMAGE_BUDGET = 24_000_000  # base64-inflated bytes kept under the 32 MB Anthropic request cap
_GUTTER = 6  # studio.perception.frames grid gutter (px)


# ============================================================================================ callbacks
@dataclass
class PreviewResult:
    """What a ``render_preview`` callback returns (a plain ``str`` summary is also accepted)."""

    summary: str = ""
    video_path: Path | None = None  # the render; ``timeline.json`` next to it maps word IDs to output time
    timeline_path: Path | None = None
    images: list[Path] = field(default_factory=list)  # optional sheets to show the Director
    metrics: dict[str, Any] = field(default_factory=dict)


RenderPreviewFn = Callable[["EditSession", str], Any]
CritiqueFn = Callable[["EditSession", list[str]], Any]
AskCreatorFn = Callable[["EditSession", str, "list[str] | None"], Any]


@dataclass
class ApplyOutcome:
    doc: CutDocument
    results: list[OpResult]
    base_version: int
    new_version: int
    text: str

    @property
    def applied(self) -> int:
        return sum(1 for r in self.results if r.applied)


class ToolList(list):  # type: ignore[type-arg]
    """``list[Tool]`` that also carries the :class:`EditSession` the tools are bound to."""

    session: EditSession


# ============================================================================================ helpers
def _t(us: int | None) -> str:
    return "-" if us is None else format_us(int(us))


def _secs(us: int) -> str:
    return f"{us / 1e6:.2f}s"


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_z(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:+.2f}"


def _jsonish(obj: Any, n: int = 180) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:  # pragma: no cover
        s = str(obj)
    return _clip(s, n)


def _words_text(index: TakeIndex, ids: Sequence[str], n: int = 90) -> str:
    return _clip(" ".join(index.word(w).display() for w in ids), n)


def summarize_doc(doc: CutDocument, index: TakeIndex, *, job: Job | None = None, validation: bool = True,
                  max_segments: int = 40) -> str:
    """Compact, model-readable summary of a document (story, finishing layers, pins, checks)."""
    lines: list[str] = []
    src = index.duration_us
    est = doc.estimated_duration_us(index) if doc.segments else 0
    head = f"Document v{doc.version}" + (f" (from v{doc.parent_version})" if doc.parent_version is not None else "")
    lines.append(f"{head}: {len(doc.segments)} segments, est. {_secs(est)} of {_secs(src)} source")
    if doc.brief is not None:
        b = doc.brief
        bits = [x for x in (b.goal and f"goal: {b.goal}", b.hook and f"hook: {b.hook}",
                            b.target_length_s and f"target {b.target_length_s:.0f}s") if x]
        if bits:
            lines.append("Brief: " + _clip("; ".join(str(x) for x in bits), 300))
    if doc.style.primary or doc.style.blend:
        d = doc.style.dials
        lines.append(f"Style: {doc.style.primary or '-'}" + (f" + {', '.join(doc.style.blend)}" if doc.style.blend
                                                               else "")
                     + f" · dials energy {d.energy:.2f} pace {d.pace:.2f} polish {d.polish:.2f} humor {d.humor:.2f}")
    for s in doc.segments[:max_segments]:
        ids = index.word_ids(s.from_word, s.to_word)
        attrs = []
        if s.speed != 1.0:
            attrs.append(f"speed {s.speed:.2f}")
        if s.seam_in.kind != "cut":
            attrs.append(f"seam {s.seam_in.kind}" + (f" {s.seam_in.lead_ms}ms" if s.seam_in.lead_ms else ""))
        if s.framing is not None:
            attrs.append(f"framing x{s.framing.scale:.2f} {s.framing.ease}")
        if s.gap_overrides:
            attrs.append("gaps " + ",".join(f"{g}={ms}ms" for g, ms in sorted(s.gap_overrides.items())))
        extra = f" [{'; '.join(attrs)}]" if attrs else ""
        lines.append(f"  {s.id} {s.from_word}-{s.to_word} ({len(ids)}w){extra}: {_words_text(index, ids, 80)}")
    if len(doc.segments) > max_segments:
        lines.append(f"  … {len(doc.segments) - max_segments} more segments (get_transcript view='cut')")
    if doc.removed:
        rr = "; ".join(f"{r.from_word}-{r.to_word} ({_clip(r.reason, 40) or 'no reason'})" for r in doc.removed[:8])
        more = f" (+{len(doc.removed) - 8} more)" if len(doc.removed) > 8 else ""
        lines.append(f"Removed {len(doc.removed)} ranges: {rr}{more}")
    fin: list[str] = []
    if doc.inserts:
        fin.append("inserts " + ", ".join(f"{i.id} {i.mode} {i.anchor_from_word}-{i.anchor_to_word}"
                                          for i in doc.inserts[:8]))
    if doc.texts:
        fin.append("texts " + ", ".join(f"{t.id} {t.kind} '{_clip(t.text, 24)}'" for t in doc.texts[:6]))
    if doc.captions is not None:
        c = doc.captions
        fin.append(f"captions {'on' if c.enabled else 'off'} {len(c.pages)} pages "
                   f"({c.style.font} {c.style.size_px}px {c.style.animation})")
    au = doc.audio
    if au.music is not None:
        fin.append(f"music {au.music.asset_id or au.music.source} {au.music.level_lu_under_speech:+.0f} LU")
    if au.sfx:
        fin.append(f"sfx {len(au.sfx)} cues")
    v = au.voice
    fin.append(f"voice chain denoise {v.denoise}, comp {v.compression_db:.0f} dB; loudness "
               f"{au.loudness_target_lufs:.0f} LUFS / {au.true_peak_dbtp:.1f} dBTP")
    if doc.color is not None:
        fin.append(f"color exp {doc.color.exposure:+.2f} sat {doc.color.saturation:.2f}"
                   + (f" look {doc.color.look}" if doc.color.look else ""))
    lines.append("Finishing: " + "; ".join(fin))
    pins = doc.pins
    pin_bits = [f"{k} {','.join(v)}" for k, v in (("must_keep", pins.must_keep_word_ids),
                                                    ("payoff", pins.payoff_word_ids),
                                                    ("cta", pins.cta_word_ids)) if v]
    lines.append("Pins: " + ("; ".join(pin_bits) if pin_bits else "none"))
    if validation:
        try:
            from studio.doc.validate import validate_document

            fs = validate_document(doc, index, job)
            errs = [f for f in fs if f.level == "error"]
            warns = [f for f in fs if f.level == "warning"]
            chk = f"Checks: {len(errs)} errors, {len(warns)} warnings"
            shown = [*errs, *warns][:8]
            if shown:
                chk += ": " + " | ".join(f"[{f.level}] {f.code}: {_clip(f.message, 140)}" for f in shown)
            lines.append(chk)
        except Exception as e:  # pragma: no cover - validation must never break a summary
            lines.append(f"Checks: unavailable ({type(e).__name__})")
    return "\n".join(lines)


# ============================================================================================ session
class EditSession:
    """State shared by the Director's tools for one job.

    ``doc`` is the current document (latest saved version, or a new v0 which is saved so ``undo`` can
    reach it). :meth:`apply_ops` is the only mutation path; every applied batch becomes a saved version
    (``doc/v{n}.json``) with an oplog entry. Callback slots (``render_preview``, ``critique``,
    ``ask_creator``) are filled by the Director / champion loop; they may be sync or async.
    """

    def __init__(
        self,
        job: Job,
        index: TakeIndex | None = None,
        doc: CutDocument | None = None,
        *,
        settings: Settings | None = None,
        batch: bool = True,
        by: str = "director",
        stage: str | None = None,
        capabilities: Capabilities | None = None,
        platforms: Iterable[str] = ("tiktok",),
        render_preview: RenderPreviewFn | None = None,
        critique: CritiqueFn | None = None,
        ask_creator: AskCreatorFn | None = None,
        image_budget_bytes: int | None = None,
        image_uploader: Any | None = None,
    ):
        """``image_uploader``: an object with ``provider_name`` and ``upload(data, media_type, filename) ->
        file_id`` (see :func:`studio.agent.providers.make_image_uploader`); ``None`` sends images inline."""
        self.job = job
        self.index = index if index is not None else job.load_index()
        self.settings = settings if settings is not None else get_settings()
        self.batch = batch
        self.by = by
        self.capabilities = capabilities or capabilities_for("anthropic", self.settings.director_model)
        self.render_preview_cb = render_preview
        self.critique_cb = critique
        self.ask_creator_cb = ask_creator
        self._lock = threading.RLock()
        self.stage: str | None = None
        self.set_stage(stage)
        self.doc = doc if doc is not None else self._initial_doc(tuple(platforms))
        self._ensure_saved(self.doc)
        self.versions: list[int] = [self.doc.version]
        self.last_render: Path | None = None
        self.last_timeline: Path | None = None
        self.last_preview: PreviewResult | None = None
        self.critique_notes: list[Any] = []
        self.questions: list[dict[str, Any]] = []
        self.images_sent = 0
        self.image_bytes_sent = 0
        self.images_uploaded = 0
        self.image_budget_bytes = image_budget_bytes or min(_DEFAULT_IMAGE_BUDGET,
                                                            int(self.capabilities.max_request_bytes * 0.75))
        self.image_uploader = image_uploader
        self.upload_disabled: str | None = None  # reason uploads stopped (then images go inline)
        self.tool_calls = 0
        self._tool_cache: dict[tuple[Any, ...], ToolList] = {}

    def new_conversation(self) -> None:
        """Reset the per-conversation image accounting; call when a fresh agent conversation starts on
        this session (the document, versions and callbacks carry over)."""
        with self._lock:
            self.images_sent = 0
            self.image_bytes_sent = 0

    # ------------------------------------------------------------------ doc + stage
    def _initial_doc(self, platforms: tuple[str, ...]) -> CutDocument:
        from studio.doc.model import new_document

        if self.job.latest_doc_version() is not None:
            return self.job.load_doc()
        return new_document(self.job.id, created_by=self.by, platforms=platforms)  # type: ignore[arg-type]

    def _ensure_saved(self, doc: CutDocument) -> None:
        from studio.jobs import JobError

        if doc.version not in self.job.doc_versions():
            with contextlib.suppress(JobError):  # raced with another writer
                self.job.save_doc(doc)

    def set_stage(self, stage: str | None) -> None:
        if stage is not None and stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; use one of {', '.join(STAGES)}")
        self.stage = stage

    def allowed_families(self) -> tuple[str, ...]:
        from studio.doc.ops import OP_FAMILIES, STAGE_FAMILIES

        if self.stage in (None, "chat", "finalize"):
            return tuple(OP_FAMILIES)
        return tuple(STAGE_FAMILIES.get(self.stage, tuple(OP_FAMILIES)))

    def reload_doc(self, version: int | None = None) -> CutDocument:
        with self._lock:
            self.doc = self.job.load_doc(version)
            return self.doc

    # ------------------------------------------------------------------ ops
    def apply_ops(self, ops: Any, *, by: str | None = None) -> ApplyOutcome:
        """Apply ops (list of dicts/op models, a single op, or a JSON string of either) to the current
        document; enforce stage gating; save the new version and oplog; return results + summary text."""
        from studio.doc.ops import OP_CLASSES, OpResult, apply_ops, oplog_entries

        raw = _coerce_ops(ops)
        allowed = set(self.allowed_families())
        with self._lock:
            base = self.doc
            keep: list[Any] = []
            keep_idx: list[int] = []
            gated: list[OpResult] = []
            for i, op in enumerate(raw):
                name = op.get("op") if isinstance(op, dict) else getattr(op, "op", None)
                cls = OP_CLASSES.get(str(name)) if name is not None else None
                if cls is not None and cls.family not in allowed:
                    gated.append(OpResult(index=i, op=str(name), applied=False,
                                          reason=f"'{name}' ({cls.family} family) is not available in the "
                                                 f"{self.stage} stage; allowed families: {', '.join(sorted(allowed))}"))
                    continue
                keep.append(op)
                keep_idx.append(i)
            results: list[OpResult] = []
            new_doc = base
            if keep:
                new_doc, res = apply_ops(base, keep, self.index, job=self.job, by=by or self.by)
                for r in res:
                    results.append(r.model_copy(update={"index": keep_idx[r.index]}))
            if gated:
                self.job.append_oplog(oplog_entries(gated, raw, base_version=base.version,
                                                    new_version=base.version, by=by or self.by))
            results.extend(gated)
            results.sort(key=lambda r: r.index)
            self.doc = new_doc
            if new_doc.version != base.version:
                self.versions.append(new_doc.version)
            text = self._format_apply(raw, results, base.version, new_doc)
        return ApplyOutcome(doc=new_doc, results=results, base_version=base.version, new_version=new_doc.version,
                            text=text)

    def _format_apply(self, raw: list[Any], results: list[OpResult], base_v: int, doc: CutDocument) -> str:
        n_ok = sum(1 for r in results if r.applied)
        if n_ok:
            head = f"Applied {n_ok} of {len(results)} ops: v{base_v} -> v{doc.version} (saved doc/v{doc.version}.json)."
        else:
            head = f"Applied 0 of {len(results)} ops: document unchanged at v{base_v}."
        lines = [head]
        for r in results:
            op = raw[r.index] if r.index < len(raw) else {}
            args = {k: v for k, v in (op.items() if isinstance(op, dict) else []) if k != "op"}
            state = "applied" if r.applied else "REJECTED"
            line = f"[{r.index}] {r.op} {_jsonish(args, 140)}: {state}"
            if r.ids:
                line += f"; ids {', '.join(r.ids)}"
            if r.reason:
                line += f"; {r.reason}"
            if r.warnings:
                line += "; warnings: " + " | ".join(_clip(w, 160) for w in r.warnings[:6])
            lines.append(line)
        if self.stage:
            lines.append(f"Stage: {self.stage} (families: {', '.join(self.allowed_families())})")
        lines.append(summarize_doc(doc, self.index, job=self.job))
        return "\n".join(lines)

    # ------------------------------------------------------------------ status helpers
    def _kept_map(self) -> dict[str, str]:
        """``{word_id: segment_id}`` for kept words in the current document."""
        out: dict[str, str] = {}
        for s in self.doc.segments:
            for w in self.index.word_ids(s.from_word, s.to_word):
                out[w] = s.id
        return out

    def _word_status(self, wid: str, kept: dict[str, str]) -> str:
        return f"kept {kept[wid]}" if wid in kept else "cut"

    # ------------------------------------------------------------------ queries
    def q_overview(self) -> str:
        ix = self.index
        m = ix.media
        lines = []
        fps = float(m.fps)
        orient = "portrait" if m.height > m.width else "landscape" if m.width > m.height else "square"
        col = m.color
        hdr = f"HDR {col.hdr_format or 'yes'} (tone-mapped once in the mezzanine)" if col.hdr else "SDR"
        au = m.audio
        aud = (f"audio {au.codec} {au.sample_rate} Hz {au.channels}ch" if au is not None else "no audio stream")
        lines.append(f"Source: {m.width}x{m.height} {orient}, {fps:.3f} fps{' (VFR, conformed)' if m.vfr else ''}, "
                     f"{_secs(m.duration_us)}, {hdr} ({col.transfer or 'untagged'}), {aud}")
        kinds: dict[str, int] = {}
        for w in ix.words:
            kinds[w.kind] = kinds.get(w.kind, 0) + 1
        long_gaps = len(ix.get_gaps(250))
        lines.append(f"Words {len(ix.words)} (fillers {kinds.get('filler', 0)}, cut-offs {kinds.get('cutoff', 0)}, "
                     f"events {kinds.get('event', 0)}) · sentences {len(ix.sentences)} "
                     f"(incomplete {sum(1 for s in ix.sentences if not s.complete)}) · retake clusters "
                     f"{len(ix.clusters)} · gaps {len(ix.gaps)} (>=250 ms: {long_gaps})")
        e = ix.energy
        lines.append(f"Delivery energy: {e.wpm:.0f} wpm, f0 variability {e.f0_var:.2f}, loudness variability "
                     f"{e.loudness_var:.2f}, overall {e.overall:.2f} (0 calm .. 1 high)")
        a = ix.audio
        if a is not None:
            def f(v: float | None, fmt: str) -> str:
                return "n/a" if v is None else format(v, fmt)
            lines.append(f"Audio: noise floor {f(a.noise_floor_db, '.0f')} dB, SNR {f(a.snr_db, '.0f')} dB, clipping "
                         f"{a.clipping_ratio * 100:.2f}%, {f(a.integrated_lufs, '.1f')} LUFS, true peak "
                         f"{f(a.true_peak_dbtp, '.1f')} dBTP, LRA {f(a.lra_lu, '.1f')} LU, "
                         f"RT60 {f(a.rt60_est, '.2f')} s, "
                         f"music in room: {'yes' if a.music_in_room else 'no'}, room-tone ranges "
                         f"{len(a.room_tone_ranges_us)}"
                         + (f", extras {_jsonish(a.extras, 160)}" if a.extras else ""))
        vs = ix.visual.samples
        if vs:
            faced = [s for s in vs if s.face_box is not None]
            cover = len(faced) / len(vs)
            mean_w = sum(s.face_box.w for s in faced) / len(faced) if faced else 0.0  # type: ignore[union-attr]
            mean_cx = sum(s.face_box.cx for s in faced) / len(faced) if faced else 0.0  # type: ignore[union-attr]
            ev: dict[str, int] = {}
            for x in ix.visual.events:
                ev[x.kind] = ev.get(x.kind, 0) + 1
            lines.append(f"Visual: face in {cover * 100:.0f}% of {len(vs)} samples, mean face width {mean_w:.2f}, "
                         f"mean centre x {mean_cx:.2f}; events "
                         + (", ".join(f"{k} {n}" for k, n in sorted(ev.items())) or "none"))
        else:
            lines.append("Visual: no samples")
        lines.append(f"ASR: {ix.asr.provider}/{ix.asr.model}" + (f" ({ix.asr.language})" if ix.asr.language else ""))
        try:
            n_assets = len(self.job.list_assets())
        except Exception:  # pragma: no cover
            n_assets = 0
        lines.append(f"Registered assets: {n_assets} (list_assets)")
        lines.append(f"Stage: {self.stage or 'any'}; op families allowed now: {', '.join(self.allowed_families())}")
        lines.append(summarize_doc(self.doc, self.index, job=self.job))
        return "\n".join(lines)

    def q_transcript(self, view: str = "full", min_gap_ms: float | None = None) -> str:
        ix = self.index
        if view in ("full", "compact"):
            return ix.render_transcript(view, min_gap_ms=min_gap_ms)  # type: ignore[arg-type]
        if view == "cut":
            if not self.doc.segments:
                return (f"The story is empty (document v{self.doc.version}). Build it with cut_ops "
                        "(set_story) from the source transcript (get_transcript view='full').")
            return summarize_doc(self.doc, ix, job=self.job) + "\n\nStory in output order:\n" + \
                self.doc.render_story(ix, view="full")
        if view == "removed":
            if not self.doc.segments:
                return "The story is empty: every word is currently out (build it with cut_ops set_story)."
            kept = self._kept_map()
            removed_ids = [w.id for w in ix.words if w.id not in kept]
            if not removed_ids:
                return "Nothing is removed: every source word is in the story."
            lines = []
            covered: set[str] = set()
            for r in self.doc.removed:
                try:
                    ids = ix.word_ids(r.from_word, r.to_word)
                except (KeyError, ValueError):
                    continue
                covered.update(ids)
                span_us = ix.word(r.to_word).end_us - ix.word(r.from_word).start_us
                lines.append(f"{r.from_word}-{r.to_word} ({_secs(span_us)}) "
                             f"reason: {r.reason or '-'}: {_words_text(ix, ids, 160)}")
            rest = [w for w in removed_ids if w not in covered]
            if rest:
                runs: list[list[str]] = []
                for w in rest:
                    if runs and ix.word_pos(w) == ix.word_pos(runs[-1][-1]) + 1:
                        runs[-1].append(w)
                    else:
                        runs.append([w])
                for run in runs:
                    lines.append(f"{run[0]}-{run[-1]} (no recorded reason): {_words_text(ix, run, 160)}")
            return f"{len(removed_ids)} of {len(ix.words)} words are out of the story:\n" + "\n".join(lines)
        return "ERROR: view must be one of full, compact, cut, removed"

    def q_words(self, from_id: str, to_id: str | None = None) -> str:
        ix = self.index
        to_id = to_id or from_id
        ws = ix.get_words(from_id, to_id)
        note = ""
        if len(ws) > _MAX_WORDS_PER_CALL:
            ws = ws[:_MAX_WORDS_PER_CALL]
            note = f"\n… truncated at {_MAX_WORDS_PER_CALL} words; call again from {ix.next_word(ws[-1].id).id}"  # type: ignore[union-attr]
        kept = self._kept_map()
        lines = []
        for w in ws:
            p = w.prosody
            pros = "" if p is None else f" f0 {_fmt_z(p.f0_z)} int {_fmt_z(p.int_z)} dur {_fmt_z(p.dur_z)}"
            g = ix.gap_after(w.id)
            gtxt = f" · then {g.id} {g.duration_ms:.0f}ms {g.kind}" if g is not None else ""
            lines.append(f"{w.id} {json.dumps(w.text, ensure_ascii=False)} {w.kind} {_t(w.start_us)} "
                         f"+{w.duration_us / 1000:.0f}ms conf {w.confidence:.2f} emph {w.emphasis:.2f}{pros} · "
                         f"{w.sentence_id or '-'}{' ' + w.cluster_id if w.cluster_id else ''} · "
                         f"{self._word_status(w.id, kept)}{gtxt}")
        return "\n".join(lines) + note

    def q_sentences(self) -> str:
        ix = self.index
        kept = self._kept_map()
        lines = []
        for s in ix.sentences:
            n_kept = sum(1 for w in s.word_ids if w in kept)
            tag = []
            if s.cluster_id:
                c = ix.cluster_map.get(s.cluster_id)
                if c is not None and s.id in c.sentence_ids:
                    k = c.sentence_ids.index(s.id) + 1
                    tag.append(f"{c.id} take {k}/{len(c.sentence_ids)}"
                               + (" (recommended)" if c.recommended_sentence_id == s.id else ""))
            if not s.complete:
                tag.append("incomplete")
            lines.append(f"{s.id} {s.first_word}-{s.last_word} {_t(s.start_us)} {_secs(s.end_us - s.start_us)} "
                         f"{' '.join(tag) + ' ' if tag else ''}kept {n_kept}/{len(s.word_ids)}: "
                         f"{_clip(s.text, 200)}")
        return "\n".join(lines) if lines else "No sentences in the index."

    def q_clusters(self) -> str:
        ix = self.index
        if not ix.clusters:
            return "No retake clusters: every line was delivered once."
        kept = self._kept_map()
        lines = []
        for c in ix.clusters:
            lines.append(f"{c.id}: {len(c.sentence_ids)} takes, similarity {c.similarity:.2f}, recommended "
                         f"{c.recommended_sentence_id or '-'}" + (f" — {c.notes}" if c.notes else ""))
            for k, sid in enumerate(c.sentence_ids, start=1):
                s = ix.sentence(sid)
                ws = [ix.word(w) for w in s.word_ids]
                fill = sum(1 for w in ws if w.kind == "filler")
                cut = sum(1 for w in ws if w.kind == "cutoff")
                conf = sum(w.confidence for w in ws) / len(ws)
                emph = max((w.emphasis for w in ws), default=0.0)
                evs = ix.get_visual_events(from_word=s.first_word, to_word=s.last_word)
                span = max(1, s.end_us - s.start_us)
                ev_txt = ", ".join(
                    f"{e.kind} {min(1.0, (min(e.end_us, s.end_us) - max(e.start_us, s.start_us)) / span) * 100:.0f}%"
                    for e in evs) or "none"
                n_kept = sum(1 for w in s.word_ids if w in kept)
                lines.append(f"  take {k} {sid} {s.first_word}-{s.last_word} {_secs(s.end_us - s.start_us)} "
                             f"{'complete' if s.complete else 'INCOMPLETE'}, fillers {fill}, cut-offs {cut}, "
                             f"mean conf {conf:.2f}, peak emphasis {emph:.2f}, visual: {ev_txt}; in cut "
                             f"{n_kept}/{len(s.word_ids)}: {_clip(s.text, 160)}")
        return "\n".join(lines)

    def q_gaps(self, min_ms: float = 250.0, only_in_cut: bool = False) -> str:
        ix = self.index
        kept = self._kept_map()
        seg_bounds = {(s.from_word, s.to_word) for s in self.doc.segments}
        firsts = {a for a, _b in seg_bounds}
        lasts = {b for _a, b in seg_bounds}
        overrides: dict[str, int] = {}
        for s in self.doc.segments:
            overrides.update(s.gap_overrides)
        lines = []
        for g in ix.get_gaps(min_ms):
            a, b = g.after_word_id, g.before_word_id
            if a is None or b is None:
                where = "leading silence" if a is None else "trailing silence"
                in_cut = False
            elif a in kept and b in kept and kept[a] == kept[b]:
                where = f"inside {kept[a]}" + (f" (override {overrides[g.id]}ms)" if g.id in overrides else "")
                in_cut = True
            elif a in kept and b in kept:
                where = f"seam {kept[a]}->{kept[b]}"
                in_cut = False
            elif (a in lasts) or (b in firsts):
                where = "at a segment edge (not played)"
                in_cut = False
            else:
                where = "cut"
                in_cut = False
            if only_in_cut and not in_cut:
                continue
            aw = f"{a} {json.dumps(ix.word(a).text, ensure_ascii=False)}" if a else "start"
            bw = f"{b} {json.dumps(ix.word(b).text, ensure_ascii=False)}" if b else "end"
            en = "" if g.energy_db is None else f", {g.energy_db:.0f} dB"
            breath = " +breath" if g.has_breath and g.kind != "breath" else ""
            lines.append(f"{g.id} {g.duration_ms:.0f}ms {g.kind}{breath}"
                         f" after {aw} before {bw}{en} · {where}")
        if not lines:
            return f"No gaps >= {min_ms:.0f} ms" + (" inside the current cut." if only_in_cut else ".")
        return "\n".join(lines)

    def q_prosody(self, word_ids: Sequence[str]) -> str:
        ids = self._expand_word_refs(word_ids)
        rows = self.index.get_prosody(ids)
        out = []
        for r in rows:
            out.append(f"{r['id']} {json.dumps(r['text'], ensure_ascii=False)} {r['kind']}: f0 {_fmt_z(r['f0_z'])} "
                       f"int {_fmt_z(r['int_z'])} dur {_fmt_z(r['dur_z'])} emphasis {r['emphasis']:.2f}")
        return "z-scores vs this speaker's own baseline (+ = higher/louder/longer)\n" + "\n".join(out)

    def q_visual_events(self, kind: str | None = None) -> str:
        ix = self.index
        valid = ("blink", "look_away", "face_lost", "reading")
        if kind is not None and kind not in valid:
            return f"ERROR: kind must be one of {', '.join(valid)} (or omitted for all)"
        evs = ix.get_visual_events(kind)
        if not evs:
            return "No visual events" + (f" of kind {kind}." if kind else ".")
        kept = self._kept_map()
        lines = []
        for e in evs:
            ws = ix.words_between_us(e.start_us, max(e.end_us, e.start_us + 1))
            span = f"{ws[0].id}-{ws[-1].id}" if ws else "(in a gap)"
            n_kept = sum(1 for w in ws if w.id in kept)
            txt = _words_text(ix, [w.id for w in ws], 80) if ws else ""
            lines.append(f"{e.kind} {_t(e.start_us)} {_secs(e.end_us - e.start_us)} conf {e.confidence:.2f} over {span}"
                         + (f" (kept {n_kept}/{len(ws)})" if ws else "") + (f": {txt}" if txt else "")
                         + (f" — {e.note}" if e.note else ""))
        return "\n".join(lines)

    def q_assets(self, kind: str | None = None) -> str:
        try:
            assets = self.job.list_assets()
        except Exception as e:
            return f"ERROR: cannot read the asset registry ({type(e).__name__})"
        if kind:
            assets = [a for a in assets if a.kind == kind]
        if not assets:
            return "No registered assets" + (f" of kind {kind}" if kind else "") + \
                ". Designed cards need no asset (use a card spec); b-roll/music/SFX must be registered first."
        lines = []
        for a in assets:
            dims = f" {a.width}x{a.height}" if a.width and a.height else ""
            dur = f" {a.duration_ms / 1000:.1f}s" if a.duration_ms else ""
            lic = a.licence.name if a.licence else "NO LICENCE (cannot be used)"
            lines.append(f"{a.id} {a.kind}{dims}{dur} from {a.source}: {_clip(a.description or a.query or '', 120)} "
                         f"· licence {lic}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ id helpers
    def _expand_word_refs(self, refs: Sequence[str] | str) -> list[str]:
        """Word IDs from ``w0012``, ``w0010-w0020``, ``s003`` (its words) or ``seg002`` (its words)."""
        ix = self.index
        if isinstance(refs, str):
            refs = [refs]
        out: list[str] = []
        for ref in refs:
            r = str(ref).strip()
            m = _RANGE_RE.match(r)
            if m:
                a, b = self._range_bounds(m.group(1), m.group(2))
                out.extend(ix.word_ids(a, b))
            elif re.match(r"^w\d{4,}$", r):
                ix.word(r)
                out.append(r)
            elif re.match(r"^s\d{3,}$", r):
                out.extend(ix.sentence(r).word_ids)
            elif re.match(r"^seg\d{3,}$", r):
                seg = self.doc.segment(r)
                out.extend(ix.word_ids(seg.from_word, seg.to_word))
            else:
                raise KeyError(f"not a word reference: {r!r} (use w0012, w0010-w0020, s003 or seg002)")
        seen: set[str] = set()
        return [w for w in out if not (w in seen or seen.add(w))]  # type: ignore[func-returns-value]

    def _range_bounds(self, a: str, b: str) -> tuple[str, str]:
        ix = self.index

        def first(x: str) -> str:
            if x.startswith("seg"):
                return self.doc.segment(x).from_word
            if x.startswith("s"):
                return ix.sentence(x).first_word
            if x.startswith("g"):
                g = ix.gap(x)
                return g.before_word_id or g.after_word_id  # type: ignore[return-value]
            ix.word(x)
            return x

        def last(x: str) -> str:
            if x.startswith("seg"):
                return self.doc.segment(x).to_word
            if x.startswith("s"):
                return ix.sentence(x).last_word
            if x.startswith("g"):
                g = ix.gap(x)
                return g.after_word_id or g.before_word_id  # type: ignore[return-value]
            ix.word(x)
            return x

        fa, lb = first(a), last(b)
        if ix.word_pos(fa) > ix.word_pos(lb):
            raise ValueError(f"range reversed: {a} comes after {b}")
        return fa, lb

    # ------------------------------------------------------------------ frames
    def view_frames(self, items: Sequence[str] | None = None, ranges: Sequence[str] | None = None, *,
                    source: str = "source", frames_per_range: int = 6, hires: bool = False) -> list[Any]:
        """Contact sheets for ``items`` and filmstrips for ``ranges`` as ``[text, BinaryImage, …]``."""
        from studio.perception import frames as fr

        items = [str(x).strip() for x in (items or []) if str(x).strip()]
        ranges = [str(x).strip() for x in (ranges or []) if str(x).strip()]
        if not items and not ranges:
            return ["ERROR: give items (e.g. ['w0012', 'g0004', 's003:mid']) and/or ranges (e.g. ['w0010-w0020'])"]
        if len(items) > _MAX_ITEMS or len(ranges) > _MAX_RANGES:
            return [f"ERROR: at most {_MAX_ITEMS} items and {_MAX_RANGES} ranges per call"]
        ix = self.index
        for it in items:
            m = _ITEM_RE.match(it)
            if not m:
                return [f"ERROR: bad item {it!r}; use a word/gap/sentence ID, optionally with :start/:mid/:end"]
            base = m.group(1)
            if base.startswith("w"):
                ix.word(base)
            elif base.startswith("g"):
                ix.gap(base)
            else:
                ix.sentence(base)
        spans: list[tuple[str, Any]] = []
        for r in ranges:
            m = _RANGE_RE.match(r)
            if m:
                a, b = self._range_bounds(m.group(1), m.group(2))
                spans.append((r, (a, b)))
            elif re.match(r"^seg\d{3,}$", r):
                seg = self.doc.segment(r)
                spans.append((r, (seg.from_word, seg.to_word)))
            elif _ITEM_RE.match(r) and ":" not in r:
                self._expand_word_refs([r]) if not r.startswith("g") else ix.gap(r)
                spans.append((r, r))
            else:
                return [f"ERROR: bad range {r!r}; use 'w0010-w0020', 's003', 's002-s004', 'g0004' or 'seg002'"]
        frames_per_range = max(2, min(16, int(frames_per_range)))

        timeline: Path | None = None
        if source == "render":
            if self.last_render is None or not Path(self.last_render).exists():
                return ["ERROR: no preview render yet; call render_preview first (or use source='source')"]
            src: str | Path = Path(self.last_render)
            timeline = self.last_timeline
        elif source == "source":
            src = "proxy"
            if hires:
                try:
                    fr.resolve_source(self.job, "mezz")
                    src = "mezz"
                except FileNotFoundError:
                    src = "proxy"  # no mezzanine yet: larger tiles from the proxy are still sharper
        else:
            return ["ERROR: source must be 'source' or 'render'"]

        caps = self.capabilities
        edge = caps.image_edge_limit()
        path = fr.resolve_source(self.job, src)
        vp = fr.probe_video(path)
        portrait = vp.height >= vp.width
        tw, cols, per_sheet = _sheet_plan(caps, vp.width, vp.height, hires=hires, n_items=max(1, len(items)))

        pngs: list[tuple[str, Path]] = []
        chunks = [items[i:i + per_sheet] for i in range(0, len(items), per_sheet)]
        for k, chunk in enumerate(chunks, start=1):
            title = f"frames {k}/{len(chunks)}: {', '.join(chunk[:6])}{' …' if len(chunk) > 6 else ''}"
            p = fr.contact_sheet(self.job, chunk, index=ix, source=src, columns=min(cols, len(chunk)), thumb_w=tw,
                                 title=title, timeline=timeline)
            pngs.append((f"Contact sheet {k}/{len(chunks)} ({'render' if source == 'render' else 'source'}): "
                         f"{', '.join(chunk)}", p))
        for label, span in spans:
            fs_tw = None
            if hires:
                fs_tw = min(480 if portrait else 640, max(96, (min(edge, 1600) - 6 * (frames_per_range + 1))
                                                         // frames_per_range))
            p = fr.filmstrip(self.job, span, n_frames=frames_per_range, source=src, index=ix, timeline=timeline,
                             thumb_w=fs_tw, title=f"{label}")
            pngs.append((f"Filmstrip {label} ({'render' if source == 'render' else 'source'}): {frames_per_range} "
                         "evenly spaced frames; waveform below with word boundaries, gaps shaded", p))

        measure = self._frame_measurements(items) if (items and source == "source") else ""
        if not caps.vision:
            return [("This model cannot view images; measured values at the requested instants follow.\n"
                     + (measure or "(no per-frame measurements for ranges)"))]
        prepared = [(label, p, *_prepare_image(p, edge, caps.max_image_bytes, caps.max_image_tokens))
                    for label, p in pngs]
        total_b64 = sum(math.ceil(len(d) / 3) * 4 for _l, _p, d, _mt, _w, _h in prepared)
        with self._lock:
            if self.images_sent + len(prepared) > caps.max_images:
                return [f"ERROR: this conversation already holds {self.images_sent} images; the model accepts "
                        f"{caps.max_images} per request. Ask for fewer frames."]
            if not self._uploads_on() and self.image_bytes_sent + total_b64 > self.image_budget_bytes:
                return [f"ERROR: image budget for this conversation is nearly spent "
                        f"({self.image_bytes_sent / 1e6:.1f} of {self.image_budget_bytes / 1e6:.0f} MB); request "
                        "fewer items or rely on get_visual_events / get_words measurements."]
            self.images_sent += len(prepared)

        out: list[Any] = [f"{len(prepared)} image(s). Tiles are labelled '<ID>  <M:SS.ss>  f<frame>' in yellow; "
                          "quote those IDs back. Times are for orientation only."]
        for label, p, data, mt, w, h in prepared:
            out.append(f"{label} [{w}x{h}]")
            out.append(self._image_part(data, mt, p.stem))
        if measure:
            out.append(measure)
        return out

    # ------------------------------------------------------------------ images
    def _uploads_on(self) -> bool:
        return self.image_uploader is not None and self.upload_disabled is None

    def _image_part(self, data: bytes, media_type: str, stem: str) -> Any:
        """``UploadedFile`` when an uploader is attached (and working), else an inline ``BinaryImage``."""
        from pydantic_ai import BinaryImage, UploadedFile

        if self._uploads_on():
            ext = ".jpg" if media_type == "image/jpeg" else ".png"
            try:
                fid = self.image_uploader.upload(data, media_type, f"{stem}{ext}")  # type: ignore[union-attr]
            except Exception as e:  # fall back to inline for the rest of the session
                self.upload_disabled = f"{type(e).__name__}: {_clip(str(e), 200)}"
                trace_event(self.job, "image_upload_disabled", reason=self.upload_disabled)
            else:
                with self._lock:
                    self.images_uploaded += 1
                return UploadedFile(fid, provider_name=self.image_uploader.provider_name,  # type: ignore[union-attr]
                                    media_type=media_type, identifier=stem)
        with self._lock:
            self.image_bytes_sent += math.ceil(len(data) / 3) * 4
        return BinaryImage(data=data, media_type=media_type, identifier=stem)

    def _frame_measurements(self, items: Sequence[str]) -> str:
        from studio.perception.frames import resolve_items

        ix = self.index
        samples = ix.visual.samples
        if not samples:
            return ""
        times = [s.t_us for s in samples]
        lines = ["Measured at each tile (nearest 10 fps visual sample): face conf, box centre/width, eyes open, "
                 "gaze off-lens, mouth open, head yaw/pitch, blur, luma"]
        for r in resolve_items(list(items), index=ix):
            if r.t_us is None:
                continue
            i = bisect_left(times, r.t_us)
            cands = [j for j in (i - 1, i) if 0 <= j < len(samples)]
            s = samples[min(cands, key=lambda j: abs(times[j] - r.t_us))]  # type: ignore[operator]

            def f(v: float | None, fmt: str = ".2f") -> str:
                return "n/a" if v is None else format(v, fmt)
            box = (f"({s.face_box.cx:.2f},{s.face_box.cy:.2f}) w {s.face_box.w:.2f}" if s.face_box is not None
                   else "no face")
            lines.append(f"{r.ref} {_t(r.t_us)}: face {s.face_conf:.2f} {box}, eyes {f(s.eyes_open)}, gaze_off "
                         f"{f(s.gaze_off)}, mouth {f(s.mouth_open)}, yaw {f(s.head_yaw, '.0f')} pitch "
                         f"{f(s.head_pitch, '.0f')}, blur {f(s.blur)}, luma {f(s.luma)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ callbacks
    async def call_render_preview(self, scope: str) -> list[Any]:
        if self.render_preview_cb is None:
            return ["render_preview is not available in this session (no renderer attached). Continue from the "
                    "Take Index, view_frames on the source and get_transcript(view='cut')."]
        res = await _call_cb(self.render_preview_cb, self, scope)
        pr = _as_preview(res)
        self.last_preview = pr
        if pr.video_path is not None:
            self.last_render = Path(pr.video_path)
            tl = pr.timeline_path or (Path(pr.video_path).parent / "timeline.json")
            self.last_timeline = Path(tl) if Path(tl).exists() else None
        text = pr.summary or "Preview rendered."
        if pr.video_path is not None:
            text += (f"\nRender: {Path(pr.video_path).name} (document v{self.doc.version}). Use view_frames(..., "
                     "source='render') to inspect it by word ID.")
        if pr.metrics:
            text += "\nMetrics: " + _jsonish(pr.metrics, 1500)
        out: list[Any] = [text]
        if pr.images and self.capabilities.vision:
            caps = self.capabilities
            edge = caps.image_edge_limit()
            room = max(0, caps.max_images - self.images_sent)
            for p in pr.images[:min(8, room)]:
                data, mt, w, h = _prepare_image(Path(p), edge, caps.max_image_bytes, caps.max_image_tokens)
                with self._lock:
                    self.images_sent += 1
                out.append(f"{Path(p).name} [{w}x{h}]")
                out.append(self._image_part(data, mt, Path(p).stem))
        return out

    async def call_critique(self, questions: list[str]) -> str:
        if self.critique_cb is None:
            return ("request_critique is not available in this session (no critics attached). Self-review with "
                    "view_frames and the doctrine's critique checklist (load_skill('critique')).")
        res = await _call_cb(self.critique_cb, self, list(questions))
        if isinstance(res, str):
            self.critique_notes.append(res)
            return res
        notes = list(res or [])
        self.critique_notes.extend(notes)
        if not notes:
            return "The critics raised no notes."
        lines = []
        for n in notes:
            if isinstance(n, dict):
                refs = ",".join(n.get("refs") or [])
                conf = n.get("confirmed_by")
                conf_txt = f" (confirmed by {', '.join(conf) if isinstance(conf, list) else conf})" if conf else ""
                lines.append(f"[{n.get('severity', 'P2')}] {n.get('by', 'critic')}"
                             + (f" @ {refs}" if refs else "") + f": {n.get('text', '')}{conf_txt}")
            else:
                lines.append(str(n))
        return "\n".join(lines)

    async def call_ask_creator(self, question: str, options: list[str] | None) -> str:
        rec: dict[str, Any] = {"question": question, "options": options or [], "answer": None}
        self.questions.append(rec)
        no_answer = ("NO ANSWER: the creator is not available (batch mode). Decide yourself from the footage and "
                     "the doctrine, prefer the more restrained reading, and record the assumption with a meta_ops "
                     "note.")
        if self.batch or self.ask_creator_cb is None:
            return no_answer
        ans = await _call_cb(self.ask_creator_cb, self, question, options)
        if ans is None or (isinstance(ans, str) and not ans.strip()):
            return no_answer.replace("(batch mode)", "(no reply)")
        rec["answer"] = str(ans)
        return f"Creator answered: {ans}"

    # ------------------------------------------------------------------ tracing
    def trace_tool(self, name: str, args: dict[str, Any], *, ok: bool, latency_ms: int, result: Any = None,
                   error: str | None = None) -> None:
        chars = 0
        images = 0
        if isinstance(result, str):
            chars = len(result)
        elif isinstance(result, list):
            for x in result:
                if isinstance(x, str):
                    chars += len(x)
                else:
                    images += 1
        trace_event(self.job, "tool_call", tool=name, stage=self.stage, args=_summarize_args(args), ok=ok,
                    latency_ms=latency_ms, result_chars=chars, images=images, doc_version=self.doc.version,
                    error=error)

    # ------------------------------------------------------------------ pydantic-ai tools
    def tools(self, *, op_tools: OpToolMode = "both", include: Iterable[str] | None = None,
              exclude: Iterable[str] = ()) -> ToolList:
        """The Director's tool list bound to this session (stable across stages; see module docs).

        Built once per argument set: later calls return a new list holding the same :class:`Tool`
        objects, so definitions never drift within a conversation."""
        inc = None if include is None else tuple(sorted(set(include)))
        key = (op_tools, inc, tuple(sorted(set(exclude))))
        with self._lock:
            cached = self._tool_cache.get(key)
            if cached is None:
                cached = _make_tools(self, op_tools=op_tools, include=inc, exclude=exclude)
                self._tool_cache[key] = cached
        out = ToolList(cached)
        out.session = self
        return out


# ============================================================================================ tool plumbing
def _coerce_ops(ops: Any) -> list[Any]:
    from pydantic import BaseModel

    if isinstance(ops, str):
        try:
            ops = json.loads(ops)
        except json.JSONDecodeError as e:
            raise ValueError(f"ops is not valid JSON: {e}") from None
    if isinstance(ops, dict) and "ops" in ops and isinstance(ops["ops"], list):
        ops = ops["ops"]
    if isinstance(ops, (dict, BaseModel)):
        ops = [ops]
    if not isinstance(ops, list):
        raise ValueError("ops must be a list of op objects like {'op': 'cut_words', ...}")
    return [o.model_dump(mode="json") if isinstance(o, BaseModel) else o for o in ops]


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k == "ops":
            try:
                lst = _coerce_ops(v)
                out["ops"] = [str(o.get("op", "?")) if isinstance(o, dict) else "?" for o in lst][:50]
                out["n_ops"] = len(lst)
            except Exception:
                out["ops"] = "<unparseable>"
            continue
        if isinstance(v, list) and len(v) > 20:
            out[k] = [*v[:20], f"… +{len(v) - 20}"]
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "…"
        else:
            out[k] = v
    return redact_secrets(out)


async def _call_cb(cb: Callable[..., Any], *args: Any) -> Any:
    if inspect.iscoroutinefunction(cb):
        return await cb(*args)
    res = await asyncio.to_thread(cb, *args)
    if inspect.isawaitable(res):
        return await res
    return res


def _as_preview(res: Any) -> PreviewResult:
    if isinstance(res, PreviewResult):
        return res
    if isinstance(res, str):
        return PreviewResult(summary=res)
    if isinstance(res, Path):
        return PreviewResult(summary="Preview rendered.", video_path=res)
    if isinstance(res, dict):
        return PreviewResult(summary=str(res.get("summary", "")),
                             video_path=Path(res["video_path"]) if res.get("video_path") else None,
                             timeline_path=Path(res["timeline_path"]) if res.get("timeline_path") else None,
                             images=[Path(p) for p in res.get("images", [])], metrics=dict(res.get("metrics", {})))
    return PreviewResult(summary=str(res))


def _fit_dims(w: int, h: int, edge: int, max_tokens: int | None) -> tuple[int, int]:
    """Largest size ≤ (w, h) with long edge ≤ ``edge`` and at most ``max_tokens`` 28-px patches."""
    s = min(1.0, edge / max(w, h))
    tw, th = max(1, round(w * s)), max(1, round(h * s))
    if max_tokens and visual_tokens(tw, th) > max_tokens:
        s *= math.sqrt(max_tokens / visual_tokens(tw, th))
        tw, th = max(1, round(w * s)), max(1, round(h * s))
        while visual_tokens(tw, th) > max_tokens and max(tw, th) > 28:
            s *= 0.985
            tw, th = max(1, round(w * s)), max(1, round(h * s))
    return tw, th


def _sheet_plan(caps: Capabilities, src_w: int, src_h: int, *, hires: bool, n_items: int) -> tuple[int, int, int]:
    """``(thumb_w, columns, tiles_per_sheet)`` for contact sheets that the model sees unresampled: the sheet
    (tiles, two label lines per tile, title) fits ``caps.image_edge_limit()`` and ``caps.max_image_tokens``.
    Tiles never exceed the source width (frames are never upscaled)."""
    portrait = src_h >= src_w
    if hires:
        tw, cols = (480, 3) if portrait else (760, 2)
    else:
        tw, cols = (270, 5) if portrait else (400, 3)
    edge = caps.image_edge_limit()
    tw = max(96, min(tw, src_w, (edge - _GUTTER * (cols + 1)) // cols))
    tw -= tw % 2
    th = max(1, round(tw * src_h / src_w))
    fsize = max(11, min(22, tw // 15))
    line = round(fsize * 1.25) + 2  # conservative vs. the renderer's font metrics
    head = round((fsize + 3) * 1.25) + 2 + line + 10

    def size(rows: int, c: int) -> tuple[int, int]:
        return c * tw + (c + 1) * _GUTTER, head + rows * (th + 2 * line + 6) + (rows + 1) * _GUTTER

    cols = max(1, min(cols, n_items))
    rows = max(1, math.ceil(n_items / cols))
    tok = caps.max_image_tokens

    def fits(r: int, c: int) -> bool:
        w, h = size(r, c)
        return max(w, h) <= edge and (tok is None or visual_tokens(w, h) <= tok)

    while rows > 1 and not fits(rows, cols):
        rows -= 1
    while cols > 1 and not fits(rows, cols):
        cols -= 1
    return tw, cols, cols * rows


def _prepare_image(path: Path, edge: int, max_bytes: int,
                   max_tokens: int | None = None) -> tuple[bytes, str, int, int]:
    """Image bytes the model can take: long edge ≤ ``edge`` and ≤ ``max_tokens`` visual tokens (Lanczos,
    so the provider never resamples), PNG when small, otherwise 4:4:4 JPEG (quality 92 → 80) so burned
    text stays sharp while respecting byte limits."""
    from PIL import Image

    png_cap = min(_PNG_MAX_BYTES, max_bytes)
    with Image.open(path) as im0:
        w, h = im0.size
        fmt = im0.format
        tw, th = _fit_dims(w, h, edge, max_tokens)
        if fmt == "PNG" and (tw, th) == (w, h) and path.stat().st_size <= png_cap:
            return path.read_bytes(), "image/png", w, h  # already fine: send the file as written
        im = im0.convert("RGB")
    if (tw, th) != (w, h):
        im = im.resize((tw, th), Image.Resampling.LANCZOS)
        w, h = im.size
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=6)
    data = buf.getvalue()
    if len(data) <= png_cap:
        return data, "image/png", w, h
    for q in (92, 88, 84, 80):
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=q, subsampling=0)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            break
    return data, "image/jpeg", w, h


def _err(e: BaseException) -> str:
    msg = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
    return f"ERROR: {msg}" if msg else f"ERROR: {type(e).__name__}"


def _make_tools(session: EditSession, *, op_tools: OpToolMode, include: Iterable[str] | None,
                exclude: Iterable[str]) -> ToolList:
    from pydantic_ai import Tool

    from studio.agent import skills as sk
    from studio.doc.ops import FAMILY_DESCRIPTIONS, OP_FAMILIES, family_tool_schema

    s = session

    async def run(name: str, args: dict[str, Any], fn: Callable[[], Any], *, is_async: bool = False) -> Any:
        t0 = time.perf_counter()
        s.tool_calls += 1
        try:
            res = await fn() if is_async else await asyncio.to_thread(fn)
        except (KeyError, ValueError, LookupError, FileNotFoundError) as e:
            res = _err(e)
            s.trace_tool(name, args, ok=False, latency_ms=round((time.perf_counter() - t0) * 1000), result=res,
                         error=res)
            return res
        except Exception as e:  # never kill the run on a tool bug; report it to the model and the trace
            res = f"ERROR: {name} failed internally ({type(e).__name__}: {_clip(str(e), 300)})"
            s.trace_tool(name, args, ok=False, latency_ms=round((time.perf_counter() - t0) * 1000), result=res,
                         error=res)
            return res
        ok = not (isinstance(res, str) and res.startswith("ERROR:")) and not (
            isinstance(res, list) and res and isinstance(res[0], str) and res[0].startswith("ERROR:"))
        s.trace_tool(name, args, ok=ok, latency_ms=round((time.perf_counter() - t0) * 1000), result=res,
                     error=None if ok else (res if isinstance(res, str) else res[0]))
        if isinstance(res, list) and len(res) == 1 and isinstance(res[0], str):
            return res[0]
        return res

    # ---------------------------------------------------------------- query tools
    async def get_overview() -> str:
        """Overview of the source and the edit so far: resolution, fps, duration, HDR, audio health (noise
        floor, SNR, loudness, music in the room), measured delivery energy (words per minute, pitch and
        loudness variability), face coverage, visual event counts, registered assets, the current stage and
        a summary of the current document with its validation checks. Call this first."""
        return await run("get_overview", {}, s.q_overview)

    async def get_transcript(view: Literal["full", "compact", "cut", "removed"] = "full",
                             min_gap_ms: float | None = None) -> str:
        """Read the transcript with word, gap, sentence and retake IDs (the IDs every op uses). 'full' is the
        working view for cutting; 'cut' shows the current story; 'removed' shows what is out and why.

        Args:
            view: 'full' = one header per sentence (ID, times, retake cluster/take, incomplete flag) then every
                word as '<word ID> <text>' with pauses inline as '[g0012 0.62s]' (kind shown unless it is a
                plain pause), fillers as '{um}', audio events as '(laughter)', cut-off words ending in '-'.
                'compact' = one line per sentence with word-ID ranges only. 'cut' = the current story in output
                order with each segment's treatment, plus the document summary and checks. 'removed' = what is
                out of the story and why.
            min_gap_ms: Hide pauses shorter than this (default 0 for 'full', 250 for 'compact').
        """
        return await run("get_transcript", {"view": view, "min_gap_ms": min_gap_ms},
                         lambda: s.q_transcript(view, min_gap_ms))

    async def get_words(from_id: str, to_id: str | None = None) -> str:
        """Per-word detail for an inclusive source range: text, kind (word/filler/event/cutoff), start time
        and duration, ASR confidence, emphasis 0..1, prosody z-scores (f0/intensity/duration), sentence and
        cluster, whether it is kept (and in which segment) or cut, and the gap after it. Max 400 words per
        call.

        Args:
            from_id: First word ID, e.g. 'w0010'.
            to_id: Last word ID (inclusive); defaults to from_id.
        """
        return await run("get_words", {"from_id": from_id, "to_id": to_id}, lambda: s.q_words(from_id, to_id))

    async def get_sentences() -> str:
        """Every sentence: ID, word range, start time, duration, retake cluster/take (and whether it is the
        recommended take), completeness, how many of its words are in the current cut, and its text."""
        return await run("get_sentences", {}, s.q_sentences)

    async def get_clusters() -> str:
        """Retake clusters (the same line delivered more than once, in time order) with every take's text,
        duration, completeness, filler and cut-off counts, mean ASR confidence, peak emphasis, overlapping
        visual events (look-aways/reading as % of the take) and whether it is in the cut. The default
        recommendation is the last complete take; choose by delivery (use get_prosody and view_frames)."""
        return await run("get_clusters", {}, s.q_clusters)

    async def get_gaps(min_ms: float = 250.0, only_in_cut: bool = False) -> str:
        """Silences between words: gap ID, measured length, kind (pause/breath/silence/noise), the words on
        each side, energy at the quietest point, and where it sits in the current cut (inside a segment with
        any override, at a seam, or cut). Pauses are punctuation: shorten outliers with cut_ops set_gap by
        gap ID; never invent times.

        Args:
            min_ms: Only gaps at least this long (ms).
            only_in_cut: Only gaps that play inside the current cut.
        """
        return await run("get_gaps", {"min_ms": min_ms, "only_in_cut": only_in_cut},
                         lambda: s.q_gaps(min_ms, only_in_cut))

    async def get_prosody(word_ids: list[str]) -> str:
        """Prosody for words, z-scored against this speaker's own baseline: f0 (pitch), intensity and
        duration, plus the combined emphasis 0..1. Use it to find stressed words (caption emphasis, punch-in
        anchors, the strongest take).

        Args:
            word_ids: Word IDs or ranges, e.g. ['w0012', 'w0020-w0030', 's003', 'seg002'].
        """
        return await run("get_prosody", {"word_ids": word_ids}, lambda: s.q_prosody(word_ids))

    async def get_visual_events(kind: Literal["blink", "look_away", "face_lost", "reading"] | None = None) -> str:
        """Measured visual events (blinks, look-aways, reading/gaze-down, face lost) with start time, length,
        confidence and the word IDs they overlap (and how many of those words are kept).

        Args:
            kind: Only this kind; omit for all.
        """
        return await run("get_visual_events", {"kind": kind}, lambda: s.q_visual_events(kind))

    async def view_frames(items: list[str] | None = None, ranges: list[str] | None = None,
                          source: Literal["source", "render"] = "source", frames_per_range: int = 6,
                          hires: bool = False) -> Any:
        """Look at the picture. Returns images you can see: contact sheets for single instants and filmstrips
        (evenly spaced frames with the waveform, word boundaries and shaded gaps underneath) for ranges. Every
        tile has its ID, timecode and frame number burned in; answer with those IDs. Measured face/eye/gaze
        values for each instant follow the images.

        Args:
            items: Instants: word IDs (frame at the word onset; 'w0012:mid' or 'w0012:end' for other points), gap
                IDs (the quietest point of the pause) or sentence IDs (first word). Up to 48.
            ranges: Spans for filmstrips: 'w0010-w0020', 's003', 's002-s004', 'g0004' or a segment 'seg002'. Up to 8.
            source: 'source' = the ingested take; 'render' = the latest render_preview output (word IDs map to
                output time; cut words show as 'not in this render').
            frames_per_range: Frames per filmstrip (2-16).
            hires: Larger tiles from the full-resolution mezzanine (for focus, skin, small detail).
        """
        return await run("view_frames", {"items": items, "ranges": ranges, "source": source,
                                         "frames_per_range": frames_per_range, "hires": hires},
                         lambda: s.view_frames(items, ranges, source=source, frames_per_range=frames_per_range,
                                               hires=hires))

    async def list_assets(kind: Literal["video", "image", "audio", "gif", "screenshot", "generated_image",
                                        "generated_video"] | None = None) -> str:
        """Registered assets (b-roll clips, stills, screenshots, music, SFX) with their IDs, size, duration,
        source, description and licence. Only registered assets with a licence can be referenced by ops
        (asset_id). Designed cards need no asset.

        Args:
            kind: Only this kind; omit for all.
        """
        return await run("list_assets", {"kind": kind}, lambda: s.q_assets(kind))

    topics = sk.list_skills(settings=s.settings)
    topic_list = ", ".join(topics) if topics else "(none yet)"

    async def load_skill(name: str, section: str | None = None) -> str:
        """Load a doctrine topic file (the editing craft for one area). Load the file a stage needs before
        deciding that stage.

        Args:
            name: Topic name, e.g. 'cutting-and-pacing', 'broll', 'styles/story-comedy-hottake'; 'constants'
                returns the default priors (constants.yaml).
            section: Optional heading to return only that section, e.g. 'Principles'.
        """

        def _load() -> str:
            try:
                return sk.load_skill(name, section=section, settings=s.settings)
            except sk.SkillNotFound as e:
                return f"ERROR: {e}"

        return await run("load_skill", {"name": name, "section": section}, _load)


    # ---------------------------------------------------------------- op tools
    def op_names(fam: str) -> str:
        return ", ".join(cls.model_fields["op"].default for cls in OP_FAMILIES[fam])

    def make_family_tool(fam: str) -> Tool:
        async def family_ops(ops: Any = None, **extra: Any) -> str:
            if ops is None and extra:
                ops = [extra]
            return await run(f"{fam}_ops", {"ops": ops}, lambda: s.apply_ops(ops).text)

        desc = (f"{FAMILY_DESCRIPTIONS[fam]} Ops: {op_names(fam)}. Send one or more ops in 'ops' (applied in order, "
                "each validated; one rejected op does not block the others). Returns each op's result (applied or "
                "the rejection reason to fix), the new document version and a summary with validation checks. "
                "Ops outside the current stage's families are rejected.")
        tool = Tool.from_schema(family_ops, name=f"{fam}_ops", description=desc,
                                json_schema=family_tool_schema(fam, strict=False), takes_ctx=False, sequential=True)
        tool.max_retries = _TOOL_RETRIES
        return tool

    async def apply_ops(ops: list[dict[str, Any]]) -> str:
        """Apply a batch of CutDocument ops from any families in one call (one version for the whole change,
        e.g. cut words and fix the caption pages together). Each op is an object with an 'op' field and that
        op's fields exactly as in the per-family tools (cut_ops, framing_ops, inserts_ops, captions_ops,
        audio_ops, color_ops, meta_ops); address words/gaps/segments/inserts by ID, never by time. Returns
        per-op results with rejection reasons, the new version and a document summary with checks.

        Args:
            ops: Ops applied in order, e.g. [{'op': 'cut_words', 'from_word': 'w0019', 'to_word': 'w0019',
                'reason': 'filler'}, {'op': 'set_gap', 'gap_id': 'g0004', 'ms': 350}].
        """
        return await run("apply_ops", {"ops": ops}, lambda: s.apply_ops(ops).text)

    # ---------------------------------------------------------------- callback tools
    async def render_preview(scope: str = "full") -> Any:
        """Render the current document as a preview (proxy quality) so you can check the result, then inspect
        it with view_frames(..., source='render'). Returns the renderer's report (duration, seams, metrics).

        Args:
            scope: 'full', 'hook' (first seconds), a segment ID like 'seg003', or a word range like 'w0010-w0040'.
        """
        return await run("render_preview", {"scope": scope}, lambda: s.call_render_preview(scope), is_async=True)

    async def request_critique(questions: list[str]) -> str:
        """Ask the critics (a fresh-context frame judge, the watcher and the metrics packet) localized questions
        about the latest render, e.g. 'At w0143->w0151, does the head jump?'. Returns notes with severity
        (P0/P1/P2), the IDs they refer to and whether a metric or second critic confirmed them.

        Args:
            questions: Specific, ID-anchored questions.
        """
        return await run("request_critique", {"questions": questions}, lambda: s.call_critique(questions),
                         is_async=True)

    async def ask_creator(question: str, options: list[str] | None = None) -> str:
        """Ask the creator a question only they can answer (e.g. which of two CTAs to keep, a name's
        spelling). In batch mode there is no answer: decide yourself and note the assumption.

        Args:
            question: One short question.
            options: Optional choices to offer.
        """
        return await run("ask_creator", {"question": question, "options": options},
                         lambda: s.call_ask_creator(question, options), is_async=True)

    funcs: list[Callable[..., Any]] = [get_overview, get_transcript, get_words, get_sentences, get_clusters, get_gaps,
                                       get_prosody, get_visual_events, view_frames, list_assets, load_skill]
    tools: list[Tool] = [Tool(f, takes_ctx=False, max_retries=_TOOL_RETRIES, docstring_format="google")
                         for f in funcs]
    for t in tools:
        if t.name == "load_skill":
            t.description = (f"{t.description} Available topics: {topic_list}; 'SKILL' re-reads the index and "
                             "'constants' returns the default priors.")
    if op_tools in ("families", "both"):
        tools.extend(make_family_tool(f) for f in _FAMILY_ORDER if f in OP_FAMILIES)
    if op_tools in ("apply_ops", "both"):
        tools.append(Tool(apply_ops, takes_ctx=False, max_retries=_TOOL_RETRIES, docstring_format="google",
                          sequential=True))
    for f in (render_preview, request_critique, ask_creator):
        tools.append(Tool(f, takes_ctx=False, max_retries=_TOOL_RETRIES, docstring_format="google",
                          sequential=f is render_preview))
    inc = set(include) if include is not None else None
    exc = set(exclude)
    out = ToolList(t for t in tools if (inc is None or t.name in inc) and t.name not in exc)
    out.session = session
    return out


def build_tools(job: Job | EditSession, index: TakeIndex | None = None, *, batch: bool = True,
                stage: str | None = None, op_tools: OpToolMode = "both", include: Iterable[str] | None = None,
                exclude: Iterable[str] = (), **session_kwargs: Any) -> ToolList:
    """Tool objects for the Director agent bound to ``job``/``index`` (or to an existing
    :class:`EditSession`). The returned list's ``.session`` holds the live document, callbacks and
    counters. ``batch=True`` makes ``ask_creator`` answer "no answer"."""
    if isinstance(job, EditSession):
        session = job
        if stage is not None:
            session.set_stage(stage)
    else:
        session = EditSession(job, index, batch=batch, stage=stage, **session_kwargs)
    return session.tools(op_tools=op_tools, include=include, exclude=exclude)
