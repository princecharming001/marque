"""Helpers for the caption/overlay tests (module ``captions``/``overlays``).

``simple_timeline`` is a tiny stand-in for the compiler: segments concatenated on the frame grid with
fixed pads, exact ``word_map`` spans, seams at segment joins, optional framing keys / inserts / texts.
It keeps these tests independent of :mod:`studio.compile.timeline`.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from studio.compile.models import FramingKey, Timeline, TimelineInsert, TimelineSegment, TimelineText, WordSpan
from studio.doc.model import CutDocument
from studio.perception.index import FaceTrack, FaceTrackPoint, TakeIndex
from studio.timebase import snap_to_frame, to_fraction

PAD_US = 80_000


def simple_timeline(doc: CutDocument, ix: TakeIndex, *, fps: Fraction | int = 30,
                    framing: dict[str, list[FramingKey]] | None = None,
                    inserts: Sequence[TimelineInsert] = (), texts: Sequence[TimelineText] = ()) -> Timeline:
    fps = to_fraction(fps)
    cursor = Fraction(0)
    segs: list[TimelineSegment] = []
    wm: dict[str, WordSpan | None] = {w.id: None for w in ix.words}
    seams: list[Fraction] = []
    for s in doc.segments:
        ws = ix.get_words(s.from_word, s.to_word)
        src_in = max(0, ws[0].start_us - PAD_US)
        src_out = ws[-1].end_us + PAD_US
        speed = to_fraction(s.speed)
        out_end = snap_to_frame(cursor + Fraction(src_out - src_in, 1_000_000) / speed, fps)
        keys = (framing or {}).get(s.id, [])
        segs.append(TimelineSegment(seg_id=s.id, src_in_us=src_in, src_out_us=src_out, out_start=cursor,
                                    out_end=out_end, speed=s.speed, audio_src_in_us=src_in, audio_src_out_us=src_out,
                                    word_ids=[w.id for w in ws], framing=keys))
        for w in ws:
            wm[w.id] = WordSpan(out_start=cursor + Fraction(w.start_us - src_in, 1_000_000) / speed,
                                out_end=cursor + Fraction(w.end_us - src_in, 1_000_000) / speed)
        if cursor > 0:
            seams.append(cursor)
        cursor = out_end
    return Timeline(fps=fps, duration=cursor, segments=segs, word_map=wm, seams=seams, inserts=list(inserts),
                    texts=list(texts))


def with_face(ix: TakeIndex, cx: float, cy: float, w: float = 0.34, h: float = 0.2, *,
              jitter: float = 0.0) -> TakeIndex:
    """Copy of ``ix`` with a constant (optionally jittering) face track."""
    pts = []
    for k in range(0, ix.duration_us // 500_000 + 2):
        d = jitter if k % 2 else -jitter
        pts.append(FaceTrackPoint(t_us=k * 500_000, cx=cx, cy=cy + d, w=w, h=h, conf=0.98))
    vis = ix.visual.model_copy(update={"face_track": FaceTrack(points=pts, method="test"), "samples": []})
    return ix.model_copy(update={"visual": vis})


def without_face(ix: TakeIndex) -> TakeIndex:
    vis = ix.visual.model_copy(update={"face_track": None, "samples": []})
    return ix.model_copy(update={"visual": vis})
