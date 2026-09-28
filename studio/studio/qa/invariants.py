"""Invariants — the only hard gates (ARCHITECTURE §7).

``check_invariants(job, doc, index, timeline, render_dir) -> list[InvariantResult]`` evaluates all ten
against the files actually rendered in ``render_dir`` (every ``final_*.mp4``: each deliverable carries
its own mix and platform), using the evidence gathered by :func:`studio.qa.metrics.measure`. Each result
is ``{id, number, name, passed, detail, refs, data}``; ``refs`` are word/segment/insert IDs so the
Director can act on a failure without reading times.

1. **No click or clipped phoneme at any seam** — the seam click detector (±5 ms, both the
   discontinuity and the impulse detector, source-differential) on every deliverable; a kept word not
   fully inside its audio window or a seam inside a kept word (timeline evidence); and, when an ASR key
   exists, the round-trip transcript: a confidently recognised source word next to a seam that the
   render's ASR loses is the clipped-phoneme signature. When the mix also carries music/SFX, a candidate
   must be lost in the render's dialogue stem too (else it is masking — reported, not gated here).
2. **Audio matches the compiled timeline (±1 frame)** — frame count, sample count, stream start offset
   and durations of every deliverable; ``mix.wav``/``mix_nomusic.wav`` sample-exact; the measured
   per-segment audio offset (source dialogue cross-correlated at the timeline's predicted position).
3. **No model timestamp as an edit coordinate** — every applied op in ``doc/oplog.jsonl`` re-validates
   against the strict op schemas and carries no absolute-time field; the document holds no time-valued
   edit field; and every compiled audio edit point is anchored to measured word boundaries of the
   segment's own words (recompiling the document reproduces the render's edit points, reported as
   evidence).
4. **Pinned payoff/CTA (and must-keep) words present** — kept in the document and mapped to an output
   span inside the video. An ASR loss of a pinned word is reported (it is gated by 1 when at a seam).
5. **Licence for every asset** — every b-roll insert (document and compiled timeline), the music bed and
   every SFX (explicit cue assets and the assets the audio stage resolved, from ``audio_report.json``)
   has a licence record allowing commercial use; registered assets must exist in the job registry and
   a licence ``record_path`` must exist on disk.
6. **HDR tone-mapped exactly once** — an HLG/PQ source carries exactly one tone-map note from ingest and
   an SDR BT.709 mezzanine; an SDR source carries none; the A-roll reads the mezzanine; drawn b-roll,
   the overlay layer and every deliverable are SDR BT.709 (no HDR transfer anywhere downstream).
7. **Text inside the platform safe zone, off eyes and mouth** — per deliverable platform, on the
   rendered overlay pixels (planned boxes when no overlay layer exists).
8. **Loudness** — integrated loudness at the document target ±1 LU and true peak ≤ the ceiling
   (−1 dBTP), measured on each encoded file (the higher of two true-peak meters).
9. **No digital silence under speech** — on each deliverable's decoded audio and on its PCM mix (a
   dropout too short to survive AAC is still audible).
10. **Delivery format** — H.264 High, yuv420p, BT.709 primaries/transfer/matrix, TV range, the timeline's
    size (9:16) and exact frame rate (CFR) and frame count, duration, AAC-LC 48 kHz stereo ≥ 256 kbps,
    ``moov`` before ``mdat`` (faststart), no edit list, and the platform's duration/size limits.

Results are saved to ``<render_dir>/qa/invariants.json``. :func:`evaluate_render` is the one-call entry
point for a job's render directory (it loads the timeline, the document version and the index itself).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from studio.timebase import to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline
    from studio.config import Settings
    from studio.doc.model import CutDocument, Licence
    from studio.jobs import Job
    from studio.perception.index import TakeIndex
    from studio.qa.metrics import MetricsPacket

__all__ = [
    "INVARIANTS", "InvariantResult", "check_invariants", "all_passed", "failures", "summarize", "finals_in",
    "primary_final", "mp4_boxes", "delivery_problems", "PLATFORM_LIMITS", "timestamp_keys", "QaRun",
    "evaluate_render",
]

#: number -> (id, name)
INVARIANTS: dict[int, tuple[str, str]] = {
    1: ("no_seam_click", "No audible click or clipped phoneme at any seam"),
    2: ("av_alignment", "Rendered audio matches the compiled timeline (±1 frame A/V sync)"),
    3: ("no_model_timestamps", "No model-provided timestamp used as an edit coordinate"),
    4: ("pins_present", "Pinned payoff/CTA words present"),
    5: ("licences", "Licence record for every inserted asset, music and SFX"),
    6: ("hdr_once", "HDR tone-mapped exactly once (mezzanine only)"),
    7: ("text_safe", "Captions/text inside the platform safe zone and off eyes/mouth"),
    8: ("loudness", "Loudness at target ±1 LU; true peak ≤ ceiling on the encoded file"),
    9: ("no_digital_silence", "No digital silence under speech"),
    10: ("delivery_format", "Delivery format checks"),
}

#: platform delivery limits (skills/editing/platforms.md, constants.yaml ``delivery``)
PLATFORM_LIMITS: dict[str, dict[str, float | None]] = {
    "tiktok": {"min_s": 1.0, "max_s": 600.0, "max_bytes": None},
    "reels": {"min_s": 3.0, "max_s": 900.0, "max_bytes": 300_000_000},
    "shorts": {"min_s": 1.0, "max_s": 180.0, "max_bytes": None},
}

_HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}
_TIME_KEY = re.compile(r"(_us|_usec|_sec|_secs|_seconds|_s)$|^(t|ts|time|timestamp|start|end|start_time|end_time|"
                       r"pts|at|at_time|out_t|in_t|seconds|us)$")
_TIME_KEY_ALLOW = {"target_length_s"}  # a desired length, not an edit point


class InvariantResult(BaseModel):
    id: str
    number: int = Field(ge=1, le=10)
    name: str
    passed: bool
    detail: str = ""
    refs: list[str] = Field(default_factory=list)  # word/seg/insert/asset IDs involved
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def details(self) -> str:  # scaffold name
        return self.detail

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _result(n: int, passed: bool, detail: str, refs: Iterable[str] = (), **data: Any) -> InvariantResult:
    iid, name = INVARIANTS[n]
    return InvariantResult(id=iid, number=n, name=name, passed=passed, detail=detail,
                           refs=list(dict.fromkeys(r for r in refs if r)), data=data)


def all_passed(results: Iterable[InvariantResult]) -> bool:
    return all(r.passed for r in results)


def failures(results: Iterable[InvariantResult]) -> list[InvariantResult]:
    return [r for r in results if not r.passed]


def summarize(results: Sequence[InvariantResult]) -> str:
    """One line per invariant: ``✓ 8 loudness — …``."""
    return "\n".join(f"{'PASS' if r.passed else 'FAIL'} {r.number:>2} {r.id}: {r.detail}" for r in results)


# ============================================================================================ files
def finals_in(render_dir: str | os.PathLike[str]) -> list[Path]:
    """``final_*.mp4`` in a render dir (platform finals first, ``final_nomusic`` last)."""
    rd = Path(render_dir)
    fs = sorted(rd.glob("final_*.mp4"))
    return sorted(fs, key=lambda p: (p.stem == "final_nomusic", p.name))


def primary_final(render_dir: str | os.PathLike[str], doc: CutDocument | None = None) -> Path | None:
    rd = Path(render_dir)
    if doc is not None and doc.deliverables:
        p = rd / f"final_{doc.deliverables[0].platform}.mp4"
        if p.exists():
            return p
    fs = [f for f in finals_in(rd) if f.stem != "final_nomusic"]
    return fs[0] if fs else (finals_in(rd)[0] if finals_in(rd) else None)


_CONTAINERS = {b"moov", b"trak", b"edts", b"mdia", b"minf", b"stbl", b"dinf", b"udta", b"mvex", b"moof", b"traf"}


def mp4_boxes(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Top-level box order and whether any ``elst`` (edit list) box exists (MP4/MOV box walk)."""
    top: list[str] = []
    elst: list[dict[str, Any]] = []
    p = Path(path)
    size_total = p.stat().st_size
    with open(p, "rb") as fh:
        pos = 0
        while pos + 8 <= size_total:
            fh.seek(pos)
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            size, typ = struct.unpack(">I4s", hdr)
            hlen = 8
            if size == 1:
                size = struct.unpack(">Q", fh.read(8))[0]
                hlen = 16
            elif size == 0:
                size = size_total - pos
            if size < hlen:
                break
            top.append(typ.decode("latin-1"))
            if typ == b"moov" and size <= 256 * 1024 * 1024:
                fh.seek(pos + hlen)
                payload = fh.read(size - hlen)
                _walk(payload, elst)
            pos += size
    return {"top": top, "edit_lists": elst, "has_edit_list": bool(elst)}


def _walk(buf: bytes, elst: list[dict[str, Any]]) -> None:
    i = 0
    n = len(buf)
    while i + 8 <= n:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hlen = 8
        if size == 1 and i + 16 <= n:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hlen = 16
        elif size == 0:
            size = n - i
        if size < hlen or i + size > n:
            break
        if typ == b"elst":
            body = buf[i + hlen: i + size]
            entries = struct.unpack(">I", body[4:8])[0] if len(body) >= 8 else None
            elst.append({"entries": entries})
        elif typ in _CONTAINERS:
            _walk(buf[i + hlen: i + size], elst)
        i += size


def delivery_problems(final: Path, timeline: Timeline, probe: Mapping[str, Any], *, platform: str | None,
                      size: tuple[int, int], min_audio_kbps: float = 250.0) -> tuple[list[str], dict[str, Any]]:
    """Format problems of one deliverable (empty list = pass) plus the facts checked."""
    problems: list[str] = []
    streams = probe.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    facts: dict[str, Any] = {"file": final.name}
    fps = to_fraction(timeline.fps)
    if v is None:
        problems.append("no video stream")
    else:
        want = {"codec_name": "h264", "pix_fmt": "yuv420p", "color_primaries": "bt709", "color_transfer": "bt709",
                "color_space": "bt709", "color_range": "tv"}
        for k, w in want.items():
            facts[k] = v.get(k)
            if v.get(k) != w:
                problems.append(f"{k}={v.get(k)} (want {w})")
        facts["profile"] = v.get("profile")
        if (v.get("profile") or "").lower() not in ("high", "constrained high", "progressive high"):
            problems.append(f"H.264 profile {v.get('profile')} (want High)")
        wh = (int(v.get("width", 0)), int(v.get("height", 0)))
        facts["size"] = list(wh)
        if wh != tuple(size):
            problems.append(f"size {wh[0]}x{wh[1]} (want {size[0]}x{size[1]})")
        elif wh[0] * 16 != wh[1] * 9:
            problems.append(f"size {wh[0]}x{wh[1]} is not 9:16")
        try:
            r = Fraction(str(v.get("r_frame_rate")))
            avg = Fraction(str(v.get("avg_frame_rate")))
        except (ValueError, ZeroDivisionError):
            r = avg = Fraction(0)
        facts["r_frame_rate"], facts["avg_frame_rate"] = v.get("r_frame_rate"), v.get("avg_frame_rate")
        if r != fps:
            problems.append(f"frame rate {v.get('r_frame_rate')} (want {fps.numerator}/{fps.denominator})")
        if avg > 0 and abs(float(avg - r)) > 0.001 * float(r or 1):
            problems.append(f"variable frame rate (avg {v.get('avg_frame_rate')} vs {v.get('r_frame_rate')})")
        nb = v.get("nb_read_packets") or v.get("nb_frames")
        with contextlib.suppress(TypeError, ValueError):
            facts["frames"] = int(nb)
            if int(nb) != timeline.frame_count:
                problems.append(f"{nb} frames (want {timeline.frame_count})")
    if a is None:
        problems.append("no audio stream")
    else:
        facts["audio"] = {"codec": a.get("codec_name"), "profile": a.get("profile"),
                          "sample_rate": a.get("sample_rate"),
                          "channels": a.get("channels"), "bit_rate": a.get("bit_rate")}
        if a.get("codec_name") != "aac":
            problems.append(f"audio codec {a.get('codec_name')} (want aac)")
        elif (a.get("profile") or "LC") != "LC":
            problems.append(f"AAC profile {a.get('profile')} (want LC)")
        if str(a.get("sample_rate")) != "48000":
            problems.append(f"audio {a.get('sample_rate')} Hz (want 48000)")
        if int(a.get("channels") or 0) != 2:
            problems.append(f"{a.get('channels')} audio channels (want 2)")
        br = a.get("bit_rate")
        with contextlib.suppress(TypeError, ValueError):
            if br is not None and float(br) < min_audio_kbps * 1000:
                problems.append(f"audio {float(br) / 1000:.0f} kbps (want ≥ {min_audio_kbps:.0f})")
    fmt = probe.get("format", {})
    try:
        dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        dur = None
    facts["duration_s"] = dur
    tl_dur = float(timeline.duration)
    if dur is not None and abs(dur - tl_dur) > 1.0 / float(fps) + 0.025:  # one frame + one AAC frame
        problems.append(f"duration {dur:.3f} s (timeline {tl_dur:.3f} s)")
    try:
        boxes = mp4_boxes(final)
        facts["top_boxes"] = boxes["top"]
        facts["edit_list"] = boxes["has_edit_list"]
        top = boxes["top"]
        if "moov" not in top or ("mdat" in top and top.index("mdat") < top.index("moov")):
            problems.append("moov atom not before mdat (no +faststart)")
        if boxes["has_edit_list"]:
            problems.append("edit list present (Instagram requires none)")
    except (OSError, struct.error) as e:
        problems.append(f"container unreadable: {e}")
    lim = PLATFORM_LIMITS.get(platform or "")
    if lim is not None and dur is not None:
        if lim["max_s"] is not None and dur > float(lim["max_s"]):
            problems.append(f"{dur:.1f} s exceeds {platform}'s {lim['max_s']:.0f} s limit")
        if lim["min_s"] is not None and dur < float(lim["min_s"]):
            problems.append(f"{dur:.1f} s is below {platform}'s {lim['min_s']:.0f} s minimum")
    if lim is not None and lim.get("max_bytes"):
        sz = final.stat().st_size
        facts["bytes"] = sz
        if sz > float(lim["max_bytes"]):  # type: ignore[arg-type]
            problems.append(f"{sz / 1e6:.0f} MB exceeds {platform}'s {float(lim['max_bytes']) / 1e6:.0f} MB")  # type: ignore[arg-type]
    return problems, facts


# ============================================================================================ invariant 3 helpers
def timestamp_keys(obj: Any, path: str = "") -> list[str]:
    """Dotted paths of numeric fields whose key looks like an absolute time (``*_us``, ``*_s``, ``start``,
    ``t`` …). Strings (``at: "start"``) and ID counters are not times."""
    out: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            ks = str(k)
            p = f"{path}.{ks}" if path else ks
            if ks == "counters":
                continue
            numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
            if numeric and ks not in _TIME_KEY_ALLOW and _TIME_KEY.search(ks):
                out.append(p)
            out += timestamp_keys(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += timestamp_keys(v, f"{path}[{i}]")
    return out


def _check_ops(job: Job | None) -> tuple[list[str], int]:
    """(problems, applied op count) for the oplog."""
    if job is None:
        return [], 0
    from studio.doc.ops import parse_op

    problems: list[str] = []
    n = 0
    for k, e in enumerate(job.read_oplog()):
        if not e.get("applied"):
            continue
        n += 1
        op = e.get("op")
        if not isinstance(op, Mapping):
            problems.append(f"oplog #{k}: op is not an object")
            continue
        try:
            parse_op(dict(op))
        except Exception as ex:  # pydantic ValidationError or unknown op
            problems.append(f"oplog #{k} ({op.get('op')}): fails the op schema ({str(ex)[:120]})")
        bad = timestamp_keys(op)
        if bad:
            problems.append(f"oplog #{k} ({op.get('op')}): time-valued fields {', '.join(bad[:4])}")
    return problems, n


def _anchor_problems(timeline: Timeline, index: TakeIndex, *, max_pad_us: int = 1_000_000) -> list[str]:
    """Every audio edit point must be anchored to the measured edges of the segment's own words."""
    out: list[str] = []
    prev = None
    for s in timeline.segments:
        if not s.word_ids:
            out.append(f"{s.seg_id}: no words")
            prev = s
            continue
        unknown = [w for w in s.word_ids if not index.has_word(w)]
        if unknown:
            out.append(f"{s.seg_id}: unknown words {unknown[:3]}")
            prev = s
            continue
        first, last = index.word(s.word_ids[0]), index.word(s.word_ids[-1])
        cont_in = prev is not None and prev.audio_src_out_us == s.audio_src_in_us
        if not cont_in and not (first.start_us - max_pad_us <= s.audio_src_in_us <= first.start_us + 2_000):
            out.append(f"{s.seg_id}: audio in {s.audio_src_in_us} µs is not anchored to {first.id} "
                       f"({first.start_us} µs)")
        prev = s
    segs = timeline.segments
    for i, s in enumerate(segs):
        if not s.word_ids or not all(index.has_word(w) for w in s.word_ids):
            continue
        cont_out = i + 1 < len(segs) and segs[i + 1].audio_src_in_us == s.audio_src_out_us
        last = index.word(s.word_ids[-1])
        if not cont_out and not (last.end_us - 2_000 <= s.audio_src_out_us <= last.end_us + max_pad_us):
            out.append(f"{s.seg_id}: audio out {s.audio_src_out_us} µs is not anchored to {last.id} "
                       f"({last.end_us} µs)")
    return out


def _recompile_matches(doc: CutDocument, index: TakeIndex, timeline: Timeline, job: Job | None) -> bool | None:
    try:
        from studio.compile.timeline import compile as compile_timeline

        tl2 = compile_timeline(doc, index, job=job, width=timeline.width, height=timeline.height)
    except Exception:
        return None
    a = [(s.seg_id, s.src_in_us, s.src_out_us, s.audio_src_in_us, s.audio_src_out_us, str(s.out_start), str(s.out_end))
         for s in timeline.segments]
    b = [(s.seg_id, s.src_in_us, s.src_out_us, s.audio_src_in_us, s.audio_src_out_us, str(s.out_start), str(s.out_end))
         for s in tl2.segments]
    return a == b


# ============================================================================================ invariant 5 helpers
def _licence_problem(lic: Licence | None, job: Job | None) -> str | None:
    if lic is None:
        return "no licence record"
    if not (lic.name or "").strip():
        return "licence has no name"
    if not lic.commercial_use:
        return f"licence '{lic.name}' does not allow commercial use"
    if lic.record_path and job is not None:
        rp = Path(lic.record_path)
        p = rp if rp.is_absolute() else job.root / rp
        if not p.exists():
            return f"licence record file {lic.record_path} is missing"
    return None


def _registered(job: Job | None, asset_id: str | None) -> Any:
    if job is None or not asset_id:
        return None
    try:
        return job.load_asset(asset_id)
    except Exception:
        return None


# ============================================================================================ main
def check_invariants(job: Job | None, doc: CutDocument, index: TakeIndex, timeline: Timeline,
                     render_dir: str | os.PathLike[str], *,
                     metrics: MetricsPacket | Mapping[str, MetricsPacket] | None = None,
                     settings: Settings | None = None, asr: bool | None = None, save: bool = True,
                     packets_out: dict[str, MetricsPacket] | None = None,
                     **measure_kw: Any) -> list[InvariantResult]:
    """Evaluate all ten invariants against the rendered files in ``render_dir``.

    ``metrics``: a packet for the primary final, or ``{final file name: packet}``; missing ones are
    measured here (primary: full; other deliverables: loudness/clicks/silence/A-V/text only).
    ``packets_out`` (optional dict) receives every packet used, keyed by final file name.
    """
    from studio.qa.metrics import MetricsPacket, measure, platform_of, probe_streams

    rd = Path(render_dir)
    finals = finals_in(rd)
    primary = primary_final(rd, doc)
    packets: dict[str, MetricsPacket] = {}
    if isinstance(metrics, MetricsPacket):
        if primary is not None:
            packets[primary.name] = metrics
    elif isinstance(metrics, Mapping):
        packets.update(metrics)
    for f in finals:
        if f.name in packets:
            continue
        is_primary = primary is not None and f == primary
        kw = dict(measure_kw)
        if not is_primary:
            kw.setdefault("light", True)
            kw.setdefault("rendered_text", True)
        packets[f.name] = measure(job, timeline, f, doc=doc, index=index, render_dir=rd, settings=settings,
                                  asr=asr if is_primary else False, **kw)
    manifest: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        manifest = json.loads((rd / "render.json").read_text(encoding="utf-8"))
    ordered = [packets[f.name] for f in finals if f.name in packets]
    prim = packets.get(primary.name) if primary is not None else None
    no_file = "no final_*.mp4 in the render dir"

    results: list[InvariantResult] = []

    # ------------------------------------------------------------------ 1 clicks / clipped phonemes
    refs: list[str] = []
    msgs: list[str] = []
    click_data = []
    for pk in ordered:
        for c in pk.clicks_found:
            msgs.append(f"click at seam {c.seam} ({c.out_t:.2f} s, {c.left_word}→{c.right_word}) in "
                        f"{Path(pk.final_path).name} [{c.rule}, +{c.margin_db:.1f} dB]")
            refs += [c.left_word or "", c.right_word or ""]
            click_data.append({"file": Path(pk.final_path).name, **c.model_dump()})
    ref_pk = prim or (ordered[0] if ordered else None)
    clipped = ref_pk.integrity.clipped if ref_pk is not None else []
    for d in clipped:
        msgs.append(f"{d['word_id']} “{d['text']}” clipped by {d['missing_ms']} ms at its {d['side']}")
        refs.append(d["word_id"])
    inside = [c for c in (ref_pk.cuts if ref_pk is not None else []) if c.inside_word]
    for c in inside:
        msgs.append(f"seam {c.seam} at {c.out_t:.2f} s falls inside a kept word")
        refs += [c.left_word or "", c.right_word or ""]
    asr_note = ""
    asr_data: dict[str, Any] = {}
    if prim is not None and prim.asr is not None:
        a = prim.asr
        asr_data = {"ran": a.ran, "wer": a.wer, "seam_damage": a.seam_damage, "masked": a.masked,
                    "confirmed_on_dialogue": a.confirm_ran, "skipped_reason": a.skipped_reason}
        if a.ran:
            for w in a.seam_damage:
                d = next((x for x in a.diffs if x.word_id == w), None)
                heard = f" (heard “{d.heard}”)" if d is not None and d.heard else ""
                also = " (also lost in the dialogue alone)" if a.confirm_ran else ""
                msgs.append(f"ASR round-trip lost seam word {w} “{index.word(w).text}”{heard}{also}")
                refs.append(w)
            asr_note = f"; ASR round-trip WER {a.wer:.1%}" if a.wer is not None else ""
            if a.masked:
                asr_note += (f"; seam words {', '.join(a.masked)} are masked by music/SFX in the mix but intact in "
                             f"the dialogue")
        else:
            asr_note = f"; ASR round-trip not run ({a.skipped_reason})"
    if not finals:
        results.append(_result(1, False, no_file))
    else:
        n_seams = len(ref_pk.clicks) if ref_pk is not None else 0
        results.append(_result(1, not msgs, "; ".join(msgs) if msgs else
                               f"{n_seams} seams clean in {len(ordered)} file(s), no clipped words{asr_note}",
                               refs, clicks=click_data, clipped=clipped, inside_word=[c.seam for c in inside],
                               asr=asr_data, seams=n_seams))

    # ------------------------------------------------------------------ 2 A/V alignment
    msgs, av_data = [], {}
    for pk in ordered:
        name = Path(pk.final_path).name
        if pk.av is None:
            msgs.append(f"{name}: A/V not measured")
            continue
        av_data[name] = pk.av.model_dump(exclude={"sync"})
        av_data[name]["sync"] = [s.model_dump() for s in pk.av.sync]
        msgs += [f"{name}: {p}" for p in pk.av.problems]
    mixes_checked = {}
    for mix in ("mix.wav", "mix_nomusic.wav"):
        mp = rd / mix
        if mp.exists():
            try:
                import soundfile as sf

                info = sf.info(str(mp))
                mixes_checked[mix] = info.frames
                if info.frames != timeline.sample_count and not any(mix in m for m in msgs):
                    msgs.append(f"{mix} has {info.frames} samples, timeline {timeline.sample_count} (not sample-exact)")
            except RuntimeError:
                msgs.append(f"{mix} unreadable")
    if not finals:
        results.append(_result(2, False, no_file))
    else:
        worst = max((pk.av.sync_max_abs_ms for pk in ordered if pk.av and pk.av.sync_max_abs_ms is not None),
                    default=None)
        ok_detail = (f"frames/samples match the timeline in {len(ordered)} file(s)"
                     + (f"; measured audio offset ≤ {worst:.2f} ms" if worst is not None else "; sync probes n/a"))
        results.append(_result(2, not msgs, "; ".join(msgs) if msgs else ok_detail, files=av_data,
                               mixes=mixes_checked))

    # ------------------------------------------------------------------ 3 no model timestamps
    msgs = []
    op_problems, n_ops = _check_ops(job)
    msgs += op_problems
    doc_json = doc.model_dump(mode="json", exclude={"meta", "notes", "brief"})
    bad_doc = timestamp_keys(doc_json)
    if bad_doc:
        msgs.append("document has time-valued fields: " + ", ".join(bad_doc[:5]))
    anchor = _anchor_problems(timeline, index)
    msgs += anchor
    same = _recompile_matches(doc, index, timeline, job)
    detail3 = (f"{n_ops} applied ops re-validate against the ID-only op schemas; every edit point is anchored "
               f"to measured word edges" + ("; recompiling reproduces the render's edit points" if same else
                                             "; recompile differs (non-default compile options?)" if same is False
                                             else ""))
    results.append(_result(3, not msgs, "; ".join(msgs) if msgs else detail3, ops=n_ops, recompile_matches=same,
                           anchor_problems=anchor))

    # ------------------------------------------------------------------ 4 pins
    kept = set(doc.kept_word_ids(index))
    dur = to_fraction(timeline.duration)
    msgs, refs = [], []
    pinned = [(w, k) for k in ("payoff", "cta", "must_keep") for w in doc.pins.of_kind(k)]  # type: ignore[arg-type]
    for w, k in pinned:
        span = timeline.word_map.get(w)
        if w not in kept:
            msgs.append(f"{k} word {w} is not in the story")
            refs.append(w)
        elif span is None:
            msgs.append(f"{k} word {w} has no output span")
            refs.append(w)
        elif not (to_fraction(span.out_start) >= 0 and to_fraction(span.out_end) <= dur):
            msgs.append(f"{k} word {w} falls outside the video")
            refs.append(w)
    lost = list(prim.asr.pinned_missing) if prim is not None and prim.asr is not None and prim.asr.ran else []
    note4 = f"; ASR did not hear pinned {', '.join(lost)} (check audibility)" if lost else ""
    results.append(_result(4, not msgs, ("; ".join(msgs) if msgs else
                                         f"{len(pinned)} pinned words present" if pinned else "no pinned words")
                           + note4, refs, pinned=[w for w, _ in pinned], asr_not_heard=lost))

    # ------------------------------------------------------------------ 5 licences
    msgs, refs = [], []
    checked: list[dict[str, Any]] = []
    from studio.doc.model import AssetRef

    for ins in doc.inserts:
        if not isinstance(ins.asset, AssetRef):
            continue
        prob = _licence_problem(ins.effective_licence, job)
        reg = _registered(job, ins.asset.id)
        if ins.asset.id and job is not None and reg is None:
            prob = prob or f"asset {ins.asset.id} is not registered in the job"
        elif reg is not None and prob is None:
            prob = _licence_problem(reg.licence, job)
        checked.append({"ref": ins.id, "asset": ins.asset.id, "problem": prob})
        if prob:
            msgs.append(f"insert {ins.id} ({ins.asset.source} {ins.asset.id or ''}): {prob}")
            refs.append(ins.id)
    for ti in timeline.inserts:
        seen = any(c["ref"] == ti.insert_id for c in checked)
        if isinstance(ti.asset, AssetRef) and ti.asset_path is not None and not seen:
            prob = _licence_problem(ti.asset.licence, job)
            checked.append({"ref": ti.insert_id, "asset": ti.asset.id, "problem": prob})
            if prob:
                msgs.append(f"insert {ti.insert_id}: {prob}")
                refs.append(ti.insert_id)
    m = doc.audio.music
    if m is not None and m.source != "none" and (m.asset is not None or m.asset_id):
        asset = m.asset or _registered(job, m.asset_id)
        prob = "music asset not resolvable" if asset is None else _licence_problem(asset.licence, job)
        checked.append({"ref": "music", "asset": m.asset_id or (asset.id if asset else None), "problem": prob})
        if prob:
            msgs.append(f"music ({m.source} {m.asset_id or ''}): {prob}")
            refs.append(m.asset_id or "music")
    for cue in doc.audio.sfx:
        if cue.asset is not None:
            prob = _licence_problem(cue.asset.licence, job)
            checked.append({"ref": cue.id, "asset": cue.asset.id, "problem": prob})
            if prob:
                msgs.append(f"sfx {cue.id}: {prob}")
                refs.append(cue.id)
    report_sfx: list[Mapping[str, Any]] = []
    with contextlib.suppress(OSError, ValueError):
        report_sfx = json.loads((rd / "audio_report.json").read_text(encoding="utf-8")).get("sfx") or []
    for pl in report_sfx:
        aid = pl.get("asset_id") if isinstance(pl, Mapping) else None
        if not aid or any(c.get("asset") == aid and c["ref"] == pl.get("sfx_id") for c in checked):
            continue
        reg = _registered(job, aid)
        prob = "resolved SFX asset not registered" if reg is None else _licence_problem(reg.licence, job)
        checked.append({"ref": pl.get("sfx_id"), "asset": aid, "problem": prob})
        if prob:
            msgs.append(f"sfx {pl.get('sfx_id')} → {aid}: {prob}")
            refs.append(str(pl.get("sfx_id") or aid))
    n_assets = len(checked)
    results.append(_result(5, not msgs, "; ".join(msgs) if msgs else
                           (f"{n_assets} assets carry commercial licence records" if n_assets else
                            "no licensed assets used (cards/procedural only)"), refs, checked=checked))

    # ------------------------------------------------------------------ 6 HDR once
    msgs = []
    media = index.media
    from studio.media.probe import FLAG_HDR_TONEMAPPED

    tonemap_notes = [n for n in media.notes if n.startswith(FLAG_HDR_TONEMAPPED)]
    facts6: dict[str, Any] = {"source_hdr": media.hdr, "tonemap_notes": len(tonemap_notes)}
    if media.hdr and len(tonemap_notes) != 1:
        msgs.append(f"HDR source has {len(tonemap_notes)} tone-map records (want exactly 1)")
    if not media.hdr and tonemap_notes:
        msgs.append("SDR source was tone-mapped")

    def transfer_of(path: Path) -> dict[str, Any] | None:
        try:
            pr = probe_streams(path)
        except Exception:
            return None
        v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"), None)
        if v is None:
            return None
        return {k: v.get(k) for k in ("color_transfer", "color_primaries", "color_space")}

    if job is not None and job.mezz_path.exists():
        t = transfer_of(job.mezz_path)
        facts6["mezz"] = t
        if t is not None and (t.get("color_transfer") in _HDR_TRANSFERS or
                              any(t.get(k) not in (None, "bt709") for k in ("color_transfer", "color_primaries"))):
            msgs.append(f"mezzanine is not SDR BT.709 ({t})")
    elif job is not None and media.hdr:
        msgs.append("HDR source but no mezzanine")
    if timeline.source_path and job is not None:
        facts6["aroll_source"] = Path(timeline.source_path).name
        if Path(timeline.source_path).resolve() != job.mezz_path.resolve():
            msgs.append(f"A-roll reads {Path(timeline.source_path).name}, not the mezzanine")
    for ti in timeline.inserts:
        if ti.asset_path and Path(ti.asset_path).exists() and Path(ti.asset_path).suffix.lower() in (
                ".mp4", ".mov", ".m4v", ".mkv", ".webm"):
            t = transfer_of(Path(ti.asset_path))
            if t is not None and t.get("color_transfer") in _HDR_TRANSFERS:
                msgs.append(f"insert {ti.insert_id} b-roll is still HDR ({t['color_transfer']})")
    for layer in ("overlays.mov", "aroll.mov"):
        lp = rd / layer
        if lp.exists():
            t = transfer_of(lp)
            facts6[layer] = t
            if t is not None and t.get("color_transfer") in _HDR_TRANSFERS:
                msgs.append(f"{layer} carries an HDR transfer")
    for f in finals:
        t = transfer_of(f)
        if t is not None and t.get("color_transfer") in _HDR_TRANSFERS:
            msgs.append(f"{f.name} carries an HDR transfer")
    detail6 = ("HLG/PQ source tone-mapped once at ingest; everything downstream is SDR BT.709" if media.hdr else
               "SDR source, no tone mapping; everything downstream is SDR BT.709")
    results.append(_result(6, not msgs, "; ".join(msgs) if msgs else detail6, **facts6))

    # ------------------------------------------------------------------ 7 text safe zone / face
    msgs, refs = [], []
    text_data: dict[str, Any] = {}
    sources = set()
    for pk in ordered:
        if pk.text_source == "none" or Path(pk.final_path).stem == "final_nomusic":
            continue
        sources.add(pk.text_source)
        text_data[pk.platform] = {"source": pk.text_source, "boxes": len(pk.text),
                                  "issues": [t.model_dump() for t in pk.text_issues]}
        for t in pk.text_issues:
            where = f" at {t.at_s:.2f} s" if t.at_s is not None else ""
            msgs.append(f"{pk.platform}: {t.kind} {t.id} {'/'.join(t.issues)}{where}")
            refs += [t.id, *(t.refs[:2])]
    if not finals:
        results.append(_result(7, False, no_file))
    else:
        n_boxes = sum(v["boxes"] for v in text_data.values())
        ok7 = (f"{n_boxes} text elements checked on the {'/'.join(sorted(sources)) or 'n/a'} layer against "
               f"{', '.join(sorted(text_data)) or 'no'} safe zones and the face" if text_data
               else "no captions or text drawn")
        results.append(_result(7, not msgs, "; ".join(msgs) if msgs else ok7, refs, platforms=text_data))

    # ------------------------------------------------------------------ 8 loudness
    msgs, loud = [], {}
    for pk in ordered:
        name = Path(pk.final_path).name
        L = pk.loudness
        if L is None:
            msgs.append(f"{name}: loudness not measured")
            continue
        loud[name] = {"integrated_lufs": L.integrated_lufs, "true_peak_dbtp": L.true_peak_dbtp,
                      "ffmpeg_lufs": L.integrated_ffmpeg_lufs, "ffmpeg_tp": L.true_peak_ffmpeg_dbtp}
        if not L.within_target:
            msgs.append(f"{name}: {L.integrated_lufs} LUFS (target {L.target_lufs:g} ±{L.tolerance_lu:g} LU)")
        if not L.true_peak_ok:
            msgs.append(f"{name}: true peak {L.true_peak_dbtp} dBTP (ceiling {L.ceiling_dbtp:g})")
    if not finals:
        results.append(_result(8, False, no_file))
    else:
        ok8 = "; ".join(f"{n} {v['integrated_lufs']} LUFS / {v['true_peak_dbtp']} dBTP" for n, v in loud.items())
        results.append(_result(8, not msgs, "; ".join(msgs) if msgs else ok8, files=loud,
                               target_lufs=doc.audio.loudness_target_lufs, ceiling_dbtp=doc.audio.true_peak_dbtp))

    # ------------------------------------------------------------------ 9 digital silence
    msgs, refs = [], []
    runs: dict[str, Any] = {}
    for pk in ordered:
        name = Path(pk.final_path).name
        bad = pk.silence_under_speech
        runs[name] = [s.model_dump() for s in pk.digital_silence]
        for s in bad:
            where = f"under {', '.join(s.word_ids[:3])}" if s.word_ids else "between words"
            src = " (PCM mix)" if s.source == "mix" else ""
            msgs.append(f"{name}: {s.duration_ms:.0f} ms of digital silence{src} at {s.start_s:.2f} s {where}")
            refs += s.word_ids
    if not finals:
        results.append(_result(9, False, no_file))
    else:
        results.append(_result(9, not msgs, "; ".join(msgs) if msgs else
                               f"no digital silence under speech in {len(ordered)} file(s)", refs, runs=runs))

    # ------------------------------------------------------------------ 10 delivery format
    msgs = []
    facts10: dict[str, Any] = {}
    preview = bool(manifest.get("preview"))
    for f in finals:
        pk = packets.get(f.name)
        try:
            probe = probe_streams(f, count_packets=True)
        except Exception as e:
            msgs.append(f"{f.name}: unreadable ({e})")
            continue
        v = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
        size = (timeline.width, timeline.height)
        if preview and v is not None:
            size = (int(v.get("width", 0)), int(v.get("height", 0)))
        plat = pk.platform if pk is not None else platform_of(f, doc)
        probs, facts = delivery_problems(f, timeline, probe, platform=None if f.stem == "final_nomusic" else plat,
                                         size=size)
        facts10[f.name] = facts
        msgs += [f"{f.name}: {p}" for p in probs]
    want_plats = [d.platform for d in doc.deliverables]
    missing = [p for p in dict.fromkeys(want_plats) if not (rd / f"final_{p}.mp4").exists()]
    if missing and finals:
        msgs.append("missing deliverables: " + ", ".join(f"final_{p}.mp4" for p in missing))
    if not finals:
        results.append(_result(10, False, no_file))
    else:
        results.append(_result(10, not msgs, "; ".join(msgs) if msgs else
                               f"{len(finals)} file(s): H.264 High yuv420p BT.709 TV, "
                               f"{timeline.width}x{timeline.height} @ "
                               f"{timeline.fps.numerator}/{timeline.fps.denominator} "
                               f"CFR, AAC 48 kHz stereo, faststart, no edit list", files=facts10, preview=preview))

    results.sort(key=lambda r: r.number)
    if packets_out is not None:
        packets_out.update(packets)
    if save:
        with contextlib.suppress(OSError):
            from studio.jobs import write_json_atomic

            write_json_atomic(rd / "qa" / "invariants.json", {
                "render_dir": str(rd), "doc_version": doc.version, "passed": all_passed(results),
                "results": [r.to_dict() for r in results]})
    return results


# ============================================================================================ one call
@dataclass
class QaRun:
    """What :func:`evaluate_render` found for one render directory."""

    render_dir: Path
    doc_version: int | None
    results: list[InvariantResult] = field(default_factory=list)
    packets: dict[str, MetricsPacket] = field(default_factory=dict)  # final file name -> packet
    primary: Path | None = None

    @property
    def passed(self) -> bool:
        return bool(self.results) and all_passed(self.results)

    @property
    def metrics(self) -> MetricsPacket | None:
        """The primary deliverable's packet."""
        return self.packets.get(self.primary.name) if self.primary is not None else None


def evaluate_render(job: Job, render_dir: str | os.PathLike[str] | None = None, *, settings: Settings | None = None,
                    asr: bool | None = None, save: bool = True, **measure_kw: Any) -> QaRun:
    """Measure and gate a job's render (latest when ``render_dir`` is None) from the files alone: the
    render's ``timeline.json`` (recompiled from the document when absent), the document version named in
    ``render.json`` and the job's Take Index."""
    from studio.compile.models import Timeline
    from studio.jobs import JobError

    rd = Path(render_dir) if render_dir is not None else job.latest_render_dir()
    if rd is None or not rd.is_dir():
        raise JobError(f"job {job.id} has no render to check")
    manifest: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        manifest = json.loads((rd / "render.json").read_text(encoding="utf-8"))
    index = job.load_index()
    tl_path = rd / "timeline.json"
    timeline = Timeline.load(tl_path) if tl_path.exists() else None
    v = manifest.get("doc_version")
    if timeline is not None and timeline.doc_version is not None:
        v = timeline.doc_version
    doc = job.load_doc(v if isinstance(v, int) else None)
    if timeline is None:
        from studio.compile.timeline import compile as compile_timeline

        timeline = compile_timeline(doc, index, job=job)
    packets: dict[str, MetricsPacket] = {}
    results = check_invariants(job, doc, index, timeline, rd, settings=settings, asr=asr, save=save,
                               packets_out=packets, **measure_kw)
    return QaRun(render_dir=rd, doc_version=doc.version, results=results, packets=packets,
                 primary=primary_final(rd, doc))
