"""Job report: ``report.md`` — what was made, why, and whether it is shippable.

``write_report(job)`` assembles everything reproducible from the job directory into one Markdown file
at the job root (``job.report_path``):

1. **Header** — job, document version and author model, render, deliverables (relative links), the QA
   verdict (all invariants pass / which fail).
2. **Contact sheet** of the primary final — a frame every ~2 s with output timecode, the word being
   spoken (with its ID) and what is on screen (segment, insert, caption page), rendered with
   :func:`studio.perception.frames.contact_sheet` from the encoded file itself and embedded as a relative
   image (``renders/r{n}/contact_sheet.jpg``).
3. **Brief** and **style**.
4. **Story** — the cut in output order (segment, words, output span, speed, seam, framing), then every
   source sentence marked kept / removed / partial with removed words struck through and the recorded
   reason (retake clusters named), then the removed ranges.
5. **Inserts**, **captions and text**, **music and SFX**, **voice and loudness**, **colour**.
6. **QA** — the metrics table (value vs doctrine prior, from ``constants.yaml``), the ten invariants,
   and the metrics packet's advice lines.
7. **Critique history** — critic notes and pairwise verdicts found under ``critique/``, the document
   notes (per-decision rationale), the op log per version, and the model-call trace summary.

Missing pieces are measured on demand (``measure`` / ``check_invariants``) or left out with a note; the
report never fails because an optional artefact is absent.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.timebase import format_us, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex
    from studio.qa.invariants import InvariantResult
    from studio.qa.metrics import MetricsPacket

__all__ = ["write_report", "render_report", "make_contact_sheet", "story_lines"]


# ============================================================================================ helpers
def _t(x: Any) -> str:
    """Output time as ``M:SS.ss``."""
    return format_us(round(float(to_fraction(x)) * 1_000_000))


def _esc(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_esc(c) for c in r) + " |")
    return out


def _rel(job: Job, p: Path | str | None) -> str | None:
    if p is None:
        return None
    pp = Path(p)
    with contextlib.suppress(ValueError):
        return pp.resolve().relative_to(job.root).as_posix()
    return str(pp)


def _num(x: Any, fmt: str = "{:.2f}", none: str = "n/a") -> str:
    if x is None:
        return none
    try:
        if isinstance(x, float) and math.isnan(x):
            return none
        return fmt.format(x)
    except (TypeError, ValueError):
        return str(x)


def _words_text(index: TakeIndex, a: str, b: str, limit: int = 14) -> str:
    try:
        ws = index.get_words(a, b)
    except (KeyError, ValueError):
        return f"{a}–{b}"
    toks = [w.display() for w in ws]
    txt = " ".join(toks[:limit]) + (" …" if len(toks) > limit else "")
    return txt


def _load_render_context(job: Job, render_dir: Path | None, doc: CutDocument | None, index: TakeIndex | None,
                         timeline: Timeline | None) -> tuple[Path | None, CutDocument | None, TakeIndex | None,
                                                          Timeline | None, dict[str, Any], list[str]]:
    notes: list[str] = []
    rd = render_dir if render_dir is not None else job.latest_render_dir()
    manifest: dict[str, Any] = {}
    if rd is not None:
        with contextlib.suppress(OSError, ValueError):
            manifest = json.loads((Path(rd) / "render.json").read_text(encoding="utf-8"))
    if index is None:
        try:
            index = job.load_index()
        except Exception as e:
            notes.append(f"take index unavailable ({type(e).__name__})")
    if doc is None:
        v = manifest.get("doc_version")
        if timeline is not None and timeline.doc_version is not None:
            v = timeline.doc_version
        try:
            doc = job.load_doc(v if isinstance(v, int) else None)
        except Exception:
            try:
                doc = job.load_doc()
            except Exception as e:
                notes.append(f"no saved document ({type(e).__name__})")
    if timeline is None and rd is not None and (Path(rd) / "timeline.json").exists():
        from studio.compile.models import Timeline

        with contextlib.suppress(Exception):
            timeline = Timeline.load(Path(rd) / "timeline.json")
    if timeline is None and doc is not None and index is not None:
        try:
            from studio.compile.timeline import compile as compile_timeline

            timeline = compile_timeline(doc, index, job=job)
            notes.append("timeline recompiled from the document (no timeline.json in the render)")
        except Exception as e:
            notes.append(f"timeline unavailable ({type(e).__name__}: {str(e)[:120]})")
    return (Path(rd) if rd is not None else None), doc, index, timeline, manifest, notes


# ============================================================================================ contact sheet
def _label_at(timeline: Timeline | None, index: TakeIndex | None, t: Fraction, budget: int = 25) -> str:
    """Compact tile label (a contact-sheet tile fits ~25 characters): the word being spoken with its ID
    (or the next word after a pause), then the insert on screen, else the segment."""
    if timeline is None:
        return ""
    cur = None
    nxt = None
    for wid, sp in timeline.word_map.items():
        if sp is None:
            continue
        s, e = to_fraction(sp.out_start), to_fraction(sp.out_end)
        if s <= t < e:
            cur = wid
            break
        if s > t and (nxt is None or s < to_fraction(timeline.word_map[nxt].out_start)):  # type: ignore[union-attr]
            nxt = wid
    where = next((f"{i.insert_id} {i.mode}" for i in timeline.inserts
                  if to_fraction(i.out_start) <= t < to_fraction(i.out_end)), None)
    if where is None:
        seg = timeline.segment_at(t)
        where = seg.seg_id if seg is not None else ""
    room = max(8, budget - len(where) - 1)
    if cur is not None:
        txt = index.word(cur).text if index is not None and index.has_word(cur) else ""
        word = f"{cur} {txt}".strip()
        word = word if len(word) <= room else word[: room - 1] + "…"
    elif nxt is not None:
        word = f"(pause→{nxt})"
    else:
        word = ""
    return " ".join(x for x in (word, where) if x)


def make_contact_sheet(job: Job | None, final_path: str | os.PathLike[str], out_path: str | os.PathLike[str], *,
                       timeline: Timeline | None = None, index: TakeIndex | None = None, every_s: float = 2.0,
                       columns: int = 6, thumb_w: int = 216, title: str | None = None) -> Path:
    """Frames of the final every ~``every_s`` (first frame included) labelled with the spoken word ID and
    what is on screen; saved as JPEG (quality 90)."""
    from PIL import Image

    from studio.compile.video import probe_video
    from studio.perception.frames import FrameRequest, contact_sheet

    final = Path(final_path)
    vp = probe_video(final)
    fps = to_fraction(vp.fps)
    if timeline is not None:
        dur = to_fraction(timeline.duration)
    else:
        dur = Fraction(vp.nb_frames, 1) / fps if vp.nb_frames else Fraction(vp.duration_s or 0).limit_denominator(1000)
    last = max(Fraction(0), dur - 1 / fps)
    n = max(1, math.floor(float(dur) / every_s) + 1)
    times = sorted({min(Fraction(k) * Fraction(every_s).limit_denominator(1000), last) for k in range(n)})
    items = [FrameRequest(t_us=round(float(t) * 1_000_000) + 1, label=_label_at(timeline, index, t)) for t in times]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_png = out.with_name(f".{out.stem}.tmp.png")
    contact_sheet(job, items, source=str(final), columns=min(columns, len(items)), thumb_w=thumb_w,
                  out_path=tmp_png, title=title or f"{final.name} — a frame every {every_s:g} s",
                  timeline=timeline)
    img = Image.open(tmp_png).convert("RGB")
    if out.suffix.lower() in (".jpg", ".jpeg"):
        img.save(out, "JPEG", quality=90, optimize=True)
        tmp_png.unlink(missing_ok=True)
    else:
        os.replace(tmp_png, out)
    return out


# ============================================================================================ story
def story_lines(doc: CutDocument, index: TakeIndex) -> list[dict[str, Any]]:
    """Every source sentence with its status (kept/removed/partial), marked-up text and reasons."""
    kept = set(doc.kept_word_ids(index))
    pos = {w.id: i for i, w in enumerate(index.words)}
    reasons: list[tuple[int, int, str]] = []
    for r in doc.removed:
        with contextlib.suppress(KeyError):
            reasons.append((pos[r.from_word], pos[r.to_word], r.reason))
    out: list[dict[str, Any]] = []
    groups: list[tuple[str | None, list[Any]]] = []
    for w in index.words:
        sid = w.sentence_id
        if groups and groups[-1][0] == sid and sid is not None:
            groups[-1][1].append(w)
        else:
            groups.append((sid, [w]))
    for sid, ws in groups:
        toks = []
        n_kept = 0
        why: list[str] = []
        for w in ws:
            if w.id in kept:
                toks.append(w.display())
                n_kept += 1
            else:
                toks.append(f"~~{w.display()}~~")
                p = pos[w.id]
                for a, b, reason in reasons:
                    if a <= p <= b and reason and reason not in why:
                        why.append(reason)
        status = "kept" if n_kept == len(ws) else "removed" if n_kept == 0 else "partial"
        s = index.sentence_map.get(sid) if sid else None
        tag = ""
        if s is not None and s.cluster_id and s.cluster_id in index.cluster_map:
            c = index.cluster_map[s.cluster_id]
            k = c.sentence_ids.index(s.id) + 1 if s.id in c.sentence_ids else 0
            tag = f"{c.id} take {k}/{len(c.sentence_ids)}" + (" (recommended)" if s.id == c.recommended_sentence_id
                                                               else "")
        if s is not None and not s.complete:
            tag = (tag + ", " if tag else "") + "incomplete"
        out.append({"sentence": sid or "—", "range": f"{ws[0].id}–{ws[-1].id}", "status": status,
                    "text": " ".join(toks), "reasons": why, "tag": tag,
                    "at": format_us(ws[0].start_us)})
    return out


# ============================================================================================ sections
def _section_header(job: Job, doc: CutDocument | None, rd: Path | None, manifest: Mapping[str, Any],
                    timeline: Timeline | None, invariants: Sequence[InvariantResult] | None) -> list[str]:
    L = ["# Edit report", ""]
    L.append(f"- **Job:** `{job.id}`")
    if doc is not None:
        L.append(f"- **Document:** v{doc.version}" + (f" (parent v{doc.parent_version})"
                                                     if doc.parent_version is not None
                                                     else "") + (f", by `{doc.created_by}`" if doc.created_by else ""))
    if rd is not None:
        L.append(f"- **Render:** `{_rel(job, rd)}`" + (" (preview)" if manifest.get("preview") else ""))
        finals = sorted(rd.glob("final_*.mp4"))
        if finals:
            L.append("- **Deliverables:** " + ", ".join(f"[{f.name}]({_rel(job, f)})" for f in finals))
        extras = [p for p in (rd / "cover.jpg", rd / "captions.srt") if p.exists()]
        if extras:
            L.append("- **Also:** " + ", ".join(f"[{p.name}]({_rel(job, p)})" for p in extras))
    if timeline is not None:
        fps = to_fraction(timeline.fps)
        L.append(f"- **Output:** {float(timeline.duration):.2f} s, {timeline.frame_count} frames @ "
                 f"{fps.numerator}/{fps.denominator} fps, {timeline.width}x{timeline.height}")
    if invariants:
        bad = [r for r in invariants if not r.passed]
        verdict = ("**all ten invariants pass**" if not bad else
                   "**FAILS " + ", ".join(f"#{r.number} {r.id}" for r in bad) + "**")
        L.append(f"- **QA verdict:** {verdict}")
    L += _review_lines(job, rd)
    L.append("")
    return L


def _review_lines(job: Job, rd: Path | None) -> list[str]:
    """Whether critics actually reviewed the shipped render (a failed review is NOT REVIEWED, never "no notes"),
    whether the review was same-family only, the closing watch, and delivered alternates."""
    p = job.logs_dir / "loop_state.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if rd is not None and st.get("champion_render") not in (None, rd.name):
        return []
    L: list[str] = []
    if st.get("reviewed") is False:
        L.append("- **Review:** **NOT REVIEWED** — the critics could not review the shipped version"
                 + (f" ({_esc(st.get('review_note'))})" if st.get("review_note") else "") + ".")
    else:
        rec: dict[str, Any] = {}
        with contextlib.suppress(OSError, ValueError):
            rec = json.loads((job.critique_dir / str(st.get("champion_render")) / "notes.json").read_text("utf-8"))
        panel = rec.get("panel") or {}
        who = ", ".join(str(v) for k, v in panel.items() if k in ("frame_judge", "second_judge", "watcher") and v)
        L.append("- **Review:** reviewed" + (f" by {who}" if who else "")
                 + (f"; frame judge's verdict **{rec.get('verdict')}**" if rec.get("verdict") else ""))
        if rec.get("same_family_only"):
            L.append("- **Same-family review only:** every judge shares the Director's model family (no other family's "
                     "key): weigh critic verdicts lower; wins rest on metric evidence where it exists.")
    fw = st.get("final_watch") or {}
    if fw:
        conf = fw.get("confirmed") or []
        L.append("- **Closing watch:** " + (f"{len(conf)} confirmed P0/P1 note(s)" if conf else
                                             "no confirmed problem" if fw.get("ran", True) else
                                             _esc(fw.get("verdict", "did not run")))
                 + (f", {len(fw.get('refuted') or [])} refuted" if fw.get("refuted") else ""))
    for v in st.get("variants") or []:
        L.append(f"- **Variant {v.get('hook')}:** {v.get('outcome')}"
                 + (f" (delivered alternate: `deliver/alternates/{v['alternate']['label']}/`)" if v.get("alternate")
                    else "") + (f" — {_esc(v.get('note'))}" if v.get("note") else ""))
    return L


def _section_brief(doc: CutDocument | None) -> list[str]:
    L = ["## Brief", ""]
    b = doc.brief if doc is not None else None
    if b is None:
        return [*L, "_No brief recorded._", ""]
    fields = [("Goal", b.goal), ("Audience", b.audience), ("Hook", b.hook), ("CTA", b.cta), ("Vibe", b.vibe),
              ("Visual plan", b.visual_plan), ("Sound plan", b.sound_plan),
              ("Target length", f"{b.target_length_s:g} s" if b.target_length_s else "")]
    for k, v in fields:
        if v:
            L.append(f"- **{k}:** {v}")
    for k, vs in (("Beats", b.beats), ("Don'ts", b.donts), ("Deviations from doctrine", b.deviations),
                  ("Rubric", b.rubric)):
        if vs:
            L.append(f"- **{k}:**")
            L += [f"  - {v}" for v in vs]
    if b.creator_question:
        L.append(f"- **Asked the creator:** {b.creator_question} — _{b.creator_answer or 'no answer'}_")
    if b.text:
        L += ["", "> " + b.text.replace("\n", "\n> ")]
    if doc is not None:
        st = doc.style
        dials = ", ".join(f"{k} {getattr(st.dials, k):.2f}" for k in ("energy", "pace", "polish", "humor"))
        extra = ", ".join(f"{d.name} {d.value:.2f}" for d in st.dials.extra)
        L += ["", f"**Style:** {st.primary or 'unspecified'}" + (f" + {', '.join(st.blend)}" if st.blend else "")
              + f" — dials: {dials}" + (f", {extra}" if extra else "")]
    L.append("")
    return L


def _section_story(doc: CutDocument | None, index: TakeIndex | None, timeline: Timeline | None) -> list[str]:
    L = ["## Story", ""]
    if doc is None or index is None:
        return [*L, "_Story unavailable (no document or index)._", ""]
    L += ["### The cut, in output order", ""]
    rows = []
    for s in doc.segments:
        span = ""
        if timeline is not None:
            pieces = [p for p in timeline.segments if p.seg_id == s.id]
            if pieces:
                span = f"{_t(pieces[0].out_start)}–{_t(pieces[-1].out_end)}"
        seam = s.seam_in.kind + (f" {s.seam_in.lead_ms} ms" if s.seam_in.lead_ms else "")
        fr = ""
        if s.framing is not None:
            ctr = s.framing.center
            c = ctr if isinstance(ctr, str) else f"({ctr.x:.2f},{ctr.y:.2f})"
            fr = f"×{s.framing.scale:.2f} {c} {s.framing.ease}"
        gaps = ", ".join(f"{g}→{ms} ms" for g, ms in sorted(s.gap_overrides.items()))
        rows.append([s.id, f"{s.from_word}–{s.to_word}", span, f"{s.speed:.2f}", seam, fr, gaps,
                     _words_text(index, s.from_word, s.to_word)])
    L += _table(["Seg", "Words", "Out", "Speed", "Seam in", "Framing", "Pauses", "Text"], rows)
    L += ["", "### Source transcript: what was kept and removed", ""]
    L.append("Removed words are ~~struck through~~; reasons come from the document's removal record.")
    L.append("")
    for ln in story_lines(doc, index):
        mark = {"kept": "KEPT", "removed": "CUT", "partial": "PART"}[ln["status"]]
        tag = f" _[{ln['tag']}]_" if ln["tag"] else ""
        why = f" — _{'; '.join(ln['reasons'])}_" if ln["reasons"] else ""
        L.append(f"- **{mark}** `{ln['sentence']}` {ln['at']} ({ln['range']}){tag}: {ln['text']}{why}")
    if doc.removed:
        L += ["", "### Removed ranges", ""]
        L += _table(["From", "To", "Text", "Reason"],
                    [[r.from_word, r.to_word, _words_text(index, r.from_word, r.to_word), r.reason or "—"]
                     for r in doc.removed])
    L.append("")
    return L


def _licence_str(lic: Any) -> str:
    if lic is None:
        return "—"
    s = lic.name
    if lic.holder:
        s += f", {lic.holder}"
    if lic.attribution_required and lic.attribution:
        s += f" (credit: {lic.attribution})"
    return s


def _section_inserts(doc: CutDocument | None, index: TakeIndex | None, timeline: Timeline | None) -> list[str]:
    from studio.doc.model import AssetRef

    L = ["## Inserts (b-roll and cards)", ""]
    if doc is None or not doc.inserts:
        return [*L, "_None: the talking head carries the video._", ""]
    spans = {i.insert_id: i for i in (timeline.inserts if timeline is not None else [])}
    rows = []
    for ins in doc.inserts:
        ti = spans.get(ins.id)
        on = f"{_t(ti.out_start)}–{_t(ti.out_end)}" if ti is not None else "not shown"
        if isinstance(ins.asset, AssetRef):
            a = ins.asset
            asset = (f"{a.kind} {a.source}:{a.id or a.source_id or ''}"
                     + (f" — {a.description}" if a.description else ""))
            if ti is not None and ti.asset_path is None:
                asset += " (file missing: not drawn)"
        else:
            c = ins.asset
            asset = f"card/{c.template}: {c.title or c.number or c.body}"
        anchor = f"{ins.anchor_from_word}–{ins.anchor_to_word}"
        if index is not None:
            anchor += f" “{_words_text(index, ins.anchor_from_word, ins.anchor_to_word, 8)}”"
        trans = f"{ins.transition_in.kind}/{ins.transition_out.kind}"
        rows.append([ins.id, ins.mode, anchor, on, asset, ins.job, trans, _licence_str(ins.effective_licence)])
    L += _table(["ID", "Mode", "Anchor", "On screen", "Asset", "Job", "In/out", "Licence"], rows)
    L.append("")
    return L


def _section_text(doc: CutDocument | None, timeline: Timeline | None, max_pages: int = 60) -> list[str]:
    L = ["## Captions and on-screen text", ""]
    cp = doc.captions if doc is not None else None
    pages = timeline.captions if timeline is not None else []
    if cp is not None and not cp.enabled:
        L.append("_Captions off._")
    elif cp is None and not pages:
        L.append("_No captions on screen._")
    else:
        # captions are on by default (``doc.captions is None``): the compiler pages them automatically
        st = cp.style if cp is not None else pages[0].style
        L.append(f"**Style:** {st.font} {st.weight}, {st.size_px}px, colour {st.color}, "
                 f"highlight {st.highlight_color}, "
                 f"stroke {st.stroke_px:g}px {st.stroke_color}" + (f", box {st.background}" if st.background else "")
                 + f", case {st.case}, animation {st.animation}, ≤{st.max_words_per_page} words/page, "
                 f"position {cp.position if cp is not None else 'auto'}.")
        planned = f"{len(cp.pages)} planned by the Director" if cp is not None and cp.pages else "paged automatically"
        L += ["", f"**Pages:** {planned}, {len(pages)} on screen."]
        if pages:
            L.append("")
            rows = []
            for pg in pages[:max_pages]:
                text = pg.text or " ".join(w.text for w in pg.words)
                emph = ", ".join(w.text for w in pg.words if w.emphasis)
                rows.append([pg.page_id or "—", f"{_t(pg.out_start)}–{_t(pg.out_end)}", text, emph,
                             f"{pg.y_norm * timeline.height:.0f}px" if timeline is not None else ""])
            L += _table(["Page", "Time", "Text", "Emphasis", "Centre y"], rows)
            if len(pages) > max_pages:
                L.append(f"_…and {len(pages) - max_pages} more pages (see captions.srt)._")
    L.append("")
    texts = doc.texts if doc is not None else []
    if texts:
        tspan = {t.text_id: t for t in (timeline.texts if timeline is not None else [])}
        rows = []
        for t in texts:
            tt = tspan.get(t.id)
            rows.append([t.id, t.kind, t.text + (f" [{'; '.join(t.items)}]" if t.items else ""),
                         f"{t.anchor_from_word}–{t.anchor_to_word}",
                         f"{_t(tt.out_start)}–{_t(tt.out_end)}" if tt is not None else "not shown",
                         t.position if isinstance(t.position, str) else f"({t.position.x:.2f},{t.position.y:.2f})",
                         t.job])
        L += ["**Text overlays:**", ""]
        L += _table(["ID", "Kind", "Text", "Anchor", "On screen", "Position", "Job"], rows)
        L.append("")
    return L


def _section_audio(job: Job, doc: CutDocument | None, timeline: Timeline | None, rd: Path | None) -> list[str]:
    L = ["## Music, SFX and voice", ""]
    if doc is None:
        return [*L, "_No document._", ""]
    a = doc.audio
    m = a.music
    if m is None or m.source == "none":
        L.append("**Music:** none (a no-music master is the default).")
    else:
        asset = m.asset
        L.append(f"**Music:** {m.source}" + (f" `{m.asset_id}`" if m.asset_id else "")
                 + (f" — {m.mood}" if m.mood else "") + (f" — prompt: “{m.prompt}”" if m.prompt else ""))
        L.append(f"  - level {m.level_lu_under_speech:g} LU under speech, ducking "
                 f"{'on' if m.duck else 'off'} ({m.duck_db:g} dB), fades {m.fade_in_ms}/{m.fade_out_ms} ms, "
                 f"{'backtimed' if m.backtime else 'not backtimed'}"
                 + (f", hits on {', '.join(m.hit_word_ids)}" if m.hit_word_ids else ""))
        if timeline is not None and timeline.music is not None:
            L.append(f"  - plays {_t(timeline.music.out_start)}–{_t(timeline.music.out_end)}")
        L.append(f"  - licence: {_licence_str(asset.licence if asset is not None else None)}")
    placed: dict[str, Mapping[str, Any]] = {}
    if rd is not None:
        with contextlib.suppress(OSError, ValueError):
            for p in json.loads((rd / "audio_report.json").read_text(encoding="utf-8")).get("sfx") or []:
                if isinstance(p, Mapping) and p.get("sfx_id"):
                    placed[str(p["sfx_id"])] = p
    if a.sfx:
        tsfx = {s.sfx_id: s for s in (timeline.sfx if timeline is not None else [])}
        rows = []
        for c in a.sfx:
            ts = tsfx.get(c.id)
            pl = placed.get(c.id, {})
            asset = (c.asset.id if c.asset is not None else None) or pl.get("asset_id") or "resolved at render"
            if c.asset is not None:
                lic = _licence_str(c.asset.licence)
            else:
                reg = None
                with contextlib.suppress(Exception):
                    reg = job.load_asset(str(pl.get("asset_id") or ""))
                lic = _licence_str(reg.licence) if reg is not None else "—"
            rows.append([c.id, c.kind, f"{c.anchor_word} ({c.at}{c.offset_ms:+d} ms)",
                         _t(ts.out_t) if ts is not None else "not placed",
                         f"{pl.get('gain_db', c.gain_db):g} dB", asset, c.job, lic])
        L += ["", "**SFX:**", ""]
        L += _table(["ID", "Kind", "Anchor", "At", "Level", "Asset", "Job", "Licence"], rows)
    else:
        L += ["", "**SFX:** none."]
    v = a.voice
    eq = ", ".join(f"{b.type} {b.freq_hz:g} Hz {b.gain_db:+g} dB" for b in v.eq) or "flat"
    breaths = f"breaths −{v.breath_atten_db:g} dB" if v.breath_atten_db else "breaths untouched"
    chain = (f"**Voice chain:** {'on' if v.enabled else 'off'} — denoise {v.denoise}"
             + (f" ({v.isolation_provider})" if v.isolation_provider else "")
             + f", HPF {v.hpf_hz:g} Hz, EQ {eq}, de-ess {v.deess_db:g} dB, compression {v.compression_db:g} dB "
             f"@ {v.comp_ratio:g}:1, leveler {'on' if v.leveler else 'off'}, {breaths}, "
             f"room tone {'on' if a.room_tone else 'off'}." + (f" _{v.notes}_" if v.notes else ""))
    L += ["", chain, "",
          f"**Loudness target:** {a.loudness_target_lufs:g} LUFS integrated, "
          f"true peak ≤ {a.true_peak_dbtp:g} dBTP.", ""]
    return L


def _section_color(doc: CutDocument | None) -> list[str]:
    L = ["## Colour", ""]
    c = doc.color if doc is not None else None
    if c is None:
        return [*L, "_No grade: the source's (tone-mapped) colour is delivered as is._", ""]
    parts = [f"exposure {c.exposure:+.2f} EV", f"contrast ×{c.contrast:.2f}", f"saturation ×{c.saturation:.2f}",
             f"temp {c.temp:+.2f}", f"tint {c.tint:+.2f}"]
    if c.white_balance_k:
        parts.append(f"white balance {c.white_balance_k} K")
    if c.look:
        parts.append(f"look {c.look} @ {c.lut_strength:.0%}")
    return [*L, ", ".join(parts) + ".", ""]


def _flag(ok: bool | None, gate: bool = False) -> str:
    if ok is None:
        return "—"
    if ok:
        return "ok"
    return "FAIL" if gate else "look"


def _qa_rows(pk: MetricsPacket, priors: Mapping[str, Any]) -> list[list[str]]:
    from studio.qa.metrics import _range, prior

    rows: list[list[str]] = []
    L = pk.loudness
    if L is not None:
        rows.append(["Integrated loudness", f"{_num(L.integrated_lufs, '{:.2f}')} LUFS (ffmpeg "
                     f"{_num(L.integrated_ffmpeg_lufs, '{:.2f}')})", f"{L.target_lufs:g} ±{L.tolerance_lu:g} LU",
                     _flag(L.within_target, True)])
        rows.append(["True peak (after AAC)", f"{_num(L.true_peak_dbtp)} dBTP (4× {_num(L.true_peak_4x_dbtp)}, "
                     f"ffmpeg {_num(L.true_peak_ffmpeg_dbtp)})", f"≤ {L.ceiling_dbtp:g} dBTP",
                     _flag(L.true_peak_ok, True)])
        st = float(prior(priors, "loudness.max_short_term_over_integrated_lu", 5))
        rows.append(["Short-term max over integrated", f"{_num(L.short_term_over_integrated_lu, '{:.1f}')} LU",
                     f"≤ {st:g} LU", _flag(None if L.short_term_over_integrated_lu is None
                                           else L.short_term_over_integrated_lu <= st)])
        rows.append(["Loudness range", f"{_num(L.lra_lu, '{:.1f}')} LU", "—", "—"])
        fold = float(prior(priors, "voice.mono_fold_voice_change_max_db", 1))
        rows.append(["Mono fold-down change", f"{_num(L.mono_fold_delta_lu, '{:+.2f}')} LU", f"±{fold:g} LU",
                     _flag(None if L.mono_fold_delta_lu is None else abs(L.mono_fold_delta_lu) <= fold)])
    worst = max((c.margin_db for c in pk.clicks if c.margin_db is not None), default=None)
    rows.append(["Seam clicks", f"{len(pk.clicks_found)} of {len(pk.clicks)} seams (worst margin "
                 f"{_num(worst, '{:+.1f}')} dB)", "0", _flag(not pk.clicks_found, True)])
    rows.append(["Digital silence under speech", f"{len(pk.silence_under_speech)} run(s)"
                 + (f"; {len(pk.digital_silence)} total" if pk.digital_silence else ""), "0",
                 _flag(not pk.silence_under_speech, True)])
    exp = [e for e in pk.video_events if e.expected]
    rows.append(["Black / frozen picture", f"{len(pk.unexpected_video_events)} unexpected"
                 + (f" ({len(exp)} expected on cards/stills)" if exp else ""), "0",
                 _flag(not pk.unexpected_video_events)])
    if pk.av is not None:
        av = pk.av
        if av.frame_diff is not None and av.sample_diff is not None:
            val = (f"frames {av.frame_diff:+d}, samples {av.sample_diff:+d}, start "
                   f"{_num(av.start_offset_ms, '{:+.1f}')} ms, measured offset ≤ "
                   f"{_num(av.sync_max_abs_ms, '{:.2f}')} ms")
        else:
            val = "; ".join(av.problems) or "n/a"
        rows.append(["A/V vs timeline", val, f"±{av.tolerance_ms:.1f} ms (1 frame)", _flag(av.ok, True)])
    rows.append(["Text placement", f"{len(pk.text)} elements ({pk.text_source}), {len(pk.text_issues)} issue(s)",
                 "safe zone, off eyes/mouth", _flag(not pk.text_issues, True)])
    p = pk.pacing
    if p is not None:
        lo, hi = _range(prior(priors, "seams.per_60s_clean_take", [1, 4]), (1.0, 4.0))
        rows.append(["Seams per minute", f"{p.seams_per_min:.1f} (content {p.content_seams_per_min:.1f}; "
                     f"{p.seams} seams)", f"{lo:g}–{hi:g} content/min (clean take)",
                     _flag(p.content_seams_per_min <= hi)])
        rows.append(["Cuts inside clauses / words", f"{p.cuts_inside_clauses} / {p.cuts_inside_words}", "0 / 0",
                     _flag(p.cuts_inside_words == 0 and p.cuts_inside_clauses == 0)])
        klo, khi = _range(prior(priors, "pauses.relative_keep_x_median", [0.7, 1.3]), (0.7, 1.3))
        rows.append(["Pause median", f"{_num(p.pause_median_ms, '{:.0f}')} ms (creator "
                     f"{_num(p.source_pause_median_ms, '{:.0f}')} ms, ×{_num(p.pause_ratio)})", f"×{klo:g}–{khi:g}",
                     _flag(None if p.pause_ratio is None else klo <= p.pause_ratio <= khi)])
        flo, fhi = _range(prior(priors, "hook.first_word_target_s", [0.1, 0.5]), (0.1, 0.5))
        rows.append(["Time to first speech", f"{_num(p.time_to_first_speech_s)} s", f"{flo:g}–{fhi:g} s",
                     _flag(None if p.time_to_first_speech_s is None else flo <= p.time_to_first_speech_s <= fhi)])
        elo, ehi = _range(prior(priors, "endings.last_word_to_end_s", [0.15, 0.5]), (0.15, 0.5))
        rows.append(["Final word to end", f"{_num(p.final_word_to_end_s)} s", f"{elo:g}–{ehi:g} s",
                     _flag(None if p.final_word_to_end_s is None else elo <= p.final_word_to_end_s <= ehi)])
        plo, phi = _range(prior(priors, "hook.payoff_position_frac", [0.55, 0.85]), (0.55, 0.85))
        rows.append(["Payoff position", _num(p.payoff_position_frac, "{:.0%}"), f"{plo:.0%}–{phi:.0%}",
                     _flag(None if p.payoff_position_frac is None else plo <= p.payoff_position_frac <= phi)])
        rows.append(["Words per minute", _num(p.wpm, "{:.0f}"), "—", "—"])
        fmax = float(prior(priors, "fillers.acceptable_per_min", 5))
        rows.append(["Fillers kept", f"{p.fillers_kept} ({p.fillers_per_min:.1f}/min)", f"≤ {fmax:g}/min",
                     _flag(p.fillers_per_min <= fmax)])
        q = float(prior(priors, "seams.static_stretch_question_s", 8))
        rows.append(["Longest static stretch", f"{p.longest_static_s:.1f} s", f"question ≥ {q:g} s",
                     _flag(p.longest_static_s < q)])
        cps = float(prior(priors, "captions.cps_max", 20))
        rows.append(["Caption reading speed", f"max {_num(p.caption_cps_max, '{:.1f}')} / median "
                     f"{_num(p.caption_cps_median, '{:.1f}')} chars/s", f"≤ {cps:g} chars/s",
                     _flag(None if p.caption_cps_max is None else p.caption_cps_max <= cps)])
        rows.append(["Removed", f"{p.words_removed} words ({p.removed_source_s:.1f} s of speech)", "—", "—"])
    a = pk.asr
    if a is not None:
        if a.ran:
            masked = f", masked by music/SFX {len(a.masked)}" if a.masked else ""
            rows.append(["ASR round-trip", f"WER {_num(a.wer, '{:.1%}')} ({a.provider} {a.model}); "
                         f"{len(a.diffs)} word diffs, seam damage {len(a.seam_damage)}{masked}",
                         "no seam damage", _flag(not a.seam_damage, True)])
        else:
            rows.append(["ASR round-trip", f"not run ({a.skipped_reason})", "—", "—"])
    it = pk.intelligibility
    if it is not None:
        rows.append(["ESTOI (dialogue vs mix)", f"{_num(it.estoi, '{:.3f}')} (phone {_num(it.estoi_phone, '{:.3f}')})",
                     "≥ 0.85", _flag(None if it.estoi is None else it.estoi >= 0.85)])
    b = pk.banding
    if b is not None:
        cm = float(prior(priors, "color.cambi_max", 3))
        rows.append(["Banding (CAMBI)", f"max {_num(b.cambi_max, '{:.2f}')}, mean {_num(b.cambi_mean, '{:.2f}')} "
                     f"({b.frames} frames)", f"≤ {cm:g}", _flag(None if b.cambi_max is None else b.cambi_max <= cm)])
    return rows


def _section_qa(pk: MetricsPacket | None, invariants: Sequence[InvariantResult] | None,
                priors: Mapping[str, Any]) -> list[str]:
    L = ["## QA", ""]
    if pk is None:
        L += ["_No measured deliverable (render not found)._", ""]
    else:
        L += [f"Measured on `{Path(pk.final_path).name}` ({pk.platform}). \"ok\"/\"look\" compare against doctrine "
              f"priors (advice); \"FAIL\" marks a hard gate.", ""]
        L += _table(["Metric", "Value", "Prior / gate", ""], _qa_rows(pk, priors))
        L.append("")
    L += ["### Invariants", ""]
    if not invariants:
        L += ["_Not evaluated._", ""]
    else:
        L += _table(["#", "Invariant", "Result", "Detail"],
                    [[str(r.number), r.name, "PASS" if r.passed else "FAIL",
                      (r.detail[:400] + ("…" if len(r.detail) > 400 else ""))
                      + (f" (refs: {', '.join(r.refs[:8])})" if r.refs and not r.passed else "")]
                     for r in invariants])
        L.append("")
    if pk is not None and (pk.advice or pk.errors):
        L += ["### Advice from the metrics", ""]
        L += [f"- {a}" for a in pk.advice]
        L += [f"- _measurement error: {e}_" for e in pk.errors]
        L.append("")
    return L


def _critique_items(job: Job) -> list[tuple[str, list[str]]]:
    """(file label, markdown lines) for every critique artefact (notes, verdicts), oldest first."""
    d = job.critique_dir
    if not d.exists():
        return []
    files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".jsonl", ".md")
             and "frames" not in p.relative_to(d).parts]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    out: list[tuple[str, list[str]]] = []
    for p in files:
        rel = p.relative_to(job.root).as_posix()
        lines: list[str] = []
        if p.suffix.lower() == ".md":
            lines.append(f"- see [{p.name}]({rel})")
        else:
            try:
                if p.suffix.lower() == ".jsonl":
                    data: Any = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
                else:
                    data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                lines.append(f"- _unreadable: {rel}_")
                out.append((rel, lines))
                continue
            lines += _render_critique(data)
        if lines:
            out.append((rel, lines))
    return out


def _render_critique(data: Any, depth: int = 0) -> list[str]:
    out: list[str] = []
    if isinstance(data, list):
        for x in data[:80]:
            out += _render_critique(x, depth)
        if len(data) > 80:
            out.append(f"- _…{len(data) - 80} more_")
        return out
    if not isinstance(data, Mapping):
        return [f"- {_esc(data)}"] if data not in (None, "") else []
    if "winner" in data:
        votes = data.get("votes")
        vs = ""
        if isinstance(votes, list):
            vs = "; ".join(f"{v.get('judge') or v.get('by') or v.get('model', '?')}: "
                           f"{v.get('winner', v.get('vote', '?'))}"
                           for v in votes if isinstance(v, Mapping))
        out.append(f"- **Pairwise:** winner **{data.get('winner')}**" + (f" — {vs}" if vs else "")
                   + (f" — {_esc(data.get('reason'))}" if data.get("reason") else ""))
        return out
    if "text" in data or "note" in data:
        sev = data.get("severity") or data.get("priority")
        by = data.get("by") or data.get("critic") or data.get("model")
        refs = data.get("refs") or data.get("ids")
        conf = data.get("confirmed_by")
        head = " ".join(x for x in (f"[{sev}]" if sev else "", f"[{by}]" if by else "",
                                   f"[{', '.join(refs) if isinstance(refs, list) else refs}]" if refs else "") if x)
        out.append(f"- {head} {_esc(data.get('text') or data.get('note'))}".replace("-  ", "- ")
                   + (f" _(confirmed by {conf})_" if conf else ""))
        return out
    for key in ("notes", "items", "critique", "results"):
        if isinstance(data.get(key), list):
            head = data.get("round") or data.get("render") or data.get("version")
            if head is not None and depth == 0:
                out.append(f"- **{key} ({head})**")
            return out + _render_critique(data[key], depth + 1)
    summary = ", ".join(f"{k}: {_esc(v)}" for k, v in list(data.items())[:6] if not isinstance(v, (dict, list)))
    return [f"- {summary}"] if summary else []


def _section_history(job: Job, doc: CutDocument | None) -> list[str]:
    L = ["## Critique history and decisions", ""]
    items = _critique_items(job)
    if items:
        for rel, lines in items:
            L += [f"**{rel}**", "", *lines, ""]
    else:
        L += ["_No critique artefacts in `critique/`._", ""]
    if doc is not None and doc.notes:
        L += ["### Decision notes", ""]
        for n in doc.notes:
            L.append("- " + (f"**{n.by}** " if n.by else "") + (f"[{n.ref}] " if n.ref else "")
                     + (f"(v{n.version}) " if n.version is not None else "") + _esc(n.text))
        L.append("")
    ops = job.read_oplog()
    if ops:
        by_v: dict[int, dict[str, Any]] = defaultdict(lambda: {"by": set(), "applied": Counter(), "rejected": 0})
        for e in ops:
            v = int(e.get("version") or 0)
            rec = by_v[v]
            rec["by"].add(str(e.get("by") or ""))
            name = (e.get("op") or {}).get("op", "?") if isinstance(e.get("op"), Mapping) else "?"
            if e.get("applied"):
                rec["applied"][name] += 1
            else:
                rec["rejected"] += 1
        L += ["### Op log", ""]
        L += _table(["Version", "By", "Applied ops", "Rejected"],
                    [[f"v{v}", ", ".join(sorted(x for x in r["by"] if x)) or "—",
                      ", ".join(f"{k}×{n}" if n > 1 else k for k, n in r["applied"].most_common()) or "—",
                      str(r["rejected"])] for v, r in sorted(by_v.items())])
        L.append("")
    trace = job.read_trace()
    calls = [e for e in trace if e.get("event") == "model_call"]
    if calls:
        agg: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for e in calls:
            k = (str(e.get("role") or "?"), f"{e.get('provider') or '?'}:{e.get('model') or '?'}")
            a = agg[k]
            a["calls"] += 1
            for f in ("input_tokens", "output_tokens", "latency_ms"):
                with contextlib.suppress(TypeError, ValueError):
                    a[f] += float(e.get(f) or 0)
        L += ["### Model calls", ""]
        L += _table(["Role", "Model", "Calls", "Input tokens", "Output tokens", "Time"],
                    [[r, m, f"{a['calls']:.0f}", f"{a['input_tokens']:.0f}", f"{a['output_tokens']:.0f}",
                      f"{a['latency_ms'] / 1000:.0f} s"] for (r, m), a in sorted(agg.items())])
        L.append("")
    return L


# ============================================================================================ main
def render_report(job: Job, *, render_dir: str | os.PathLike[str] | None = None, doc: CutDocument | None = None,
                  index: TakeIndex | None = None, timeline: Timeline | None = None,
                  metrics: MetricsPacket | None = None, invariants: Sequence[InvariantResult] | None = None,
                  contact_sheet: bool = True, measure_missing: bool = True,
                  settings: Settings | None = None) -> str:
    """The report as Markdown (see module docstring); also writes the contact sheet into the render."""
    from studio.qa.invariants import InvariantResult, check_invariants, primary_final
    from studio.qa.metrics import MetricsPacket, load_priors, measure

    rd, doc, index, timeline, manifest, notes = _load_render_context(
        job, Path(render_dir) if render_dir is not None else None, doc, index, timeline)
    priors = load_priors(settings)
    final = primary_final(rd, doc) if rd is not None else None
    if metrics is None and final is not None:
        p = rd / "qa" / f"metrics_{final.stem}.json"  # type: ignore[operator]
        if p.exists():
            with contextlib.suppress(Exception):
                metrics = MetricsPacket.load(p)
        if metrics is None and measure_missing and timeline is not None:
            try:
                metrics = measure(job, timeline, final, doc=doc, index=index, render_dir=rd, settings=settings)
            except Exception as e:
                notes.append(f"metrics failed ({type(e).__name__}: {str(e)[:120]})")
    if invariants is None and rd is not None:
        p = rd / "qa" / "invariants.json"
        if p.exists():
            with contextlib.suppress(Exception):
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("doc_version") in (None, doc.version if doc is not None else None):
                    invariants = [InvariantResult.model_validate(r) for r in data.get("results", [])]
        if invariants is None and measure_missing and final is not None and doc is not None and index is not None \
                and timeline is not None:
            try:
                invariants = check_invariants(job, doc, index, timeline, rd, metrics=metrics, settings=settings)
            except Exception as e:
                notes.append(f"invariants failed ({type(e).__name__}: {str(e)[:120]})")
    L = _section_header(job, doc, rd, manifest, timeline, invariants)
    if contact_sheet and final is not None:
        try:
            sheet = make_contact_sheet(job, final, rd / "contact_sheet.jpg", timeline=timeline, index=index)  # type: ignore[operator]
            L += ["## Contact sheet", "", f"![Contact sheet of {final.name}]({_rel(job, sheet)})", "",
                  "_A frame every ~2 s of the delivered file: output time, frame, the word being spoken (ID) and "
                  "what is on screen._", ""]
        except Exception as e:
            notes.append(f"contact sheet failed ({type(e).__name__}: {str(e)[:120]})")
    L += _section_brief(doc)
    L += _section_story(doc, index, timeline)
    L += _section_inserts(doc, index, timeline)
    L += _section_text(doc, timeline)
    L += _section_audio(job, doc, timeline, rd)
    L += _section_color(doc)
    L += _section_qa(metrics, invariants, priors)
    L += _section_history(job, doc)
    if notes:
        L += ["## Report notes", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(L).rstrip() + "\n"


def write_report(job: Job, **kw: Any) -> Path:
    """Write ``job.report_path`` (``report.md``) from the job directory and return it. Keyword arguments
    are passed to :func:`render_report` (``render_dir``, ``doc``, ``index``, ``timeline``, ``metrics``,
    ``invariants``, ``contact_sheet``, ``measure_missing``, ``settings``)."""
    text = render_report(job, **kw)
    p = job.report_path
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    job.trace("report", path=p.name, chars=len(text))
    return p
