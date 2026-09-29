"""Second-pass ASR over speech the first pass left out (Take Index layer between ASR and gaps).

Scribe v2 sometimes drops a verbatim restart outright: on real-take40 one run returned "One, do the two
cuisines, [1.3 s stall] do the two cuisines share …" and a re-run of the same audio returned "One, do the two
[2.3 s] cuisines share …" — four spoken words gone. The gap between "two" and "cuisines" then held speech the
Director could not see, the compiler could trim into (a pause target keeps its edges: "cui-" … "-two"), and no
gate knew about (the removed-word leak check only knows transcribed words).

So after the first refinement every gap is scanned for **speech the transcript does not own**: frames that are
energy-active (the gap module's activity mask), speech-like to Silero (≥ 0.5) and not a measured breath, at least
``MIN_SPEECH_MS`` in total. Each such stretch (padded, clamped to the gap) is sent to the ASR again on its own —
an isolated excerpt has no neighbouring duplicate to collapse into — and the words it returns inside the gap are
merged into the transcript (re-numbered in time order; refinement then runs once over the whole word list).
Whatever still has no words (no key, the ASR hears nothing) is left for the gap stage to mark as untranscribed
sound (``Gap.sound_us``, kind ``noise``), which pads and pause trims never keep and the Director sees.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job
    from studio.perception.index import Gap, Word

__all__ = ["SpeechRegion", "untranscribed_speech", "fill_untranscribed", "mark_untranscribed", "MIN_SPEECH_MS"]

MIN_SPEECH_MS = 250.0  # speech-like activity inside a gap at least this long gets a second ASR pass
EDGE_GUARD_US = 30_000  # frames this close to a word edge belong to that word (decay / onset)
BRIDGE_US = 200_000  # speech runs closer than this are one stretch
PAD_US = 120_000  # excerpt padding around the stretch (clamped to the gap)
LEAD_SILENCE_S = 0.25  # digital silence around the excerpt (ASR models settle on it)
CONTEXT_US = 1_200_000  # neighbouring transcribed speech included on each side of the excerpt
MAX_BELOW_SPEECH_DB = 12.0  # quieter than this under typical speech: a mumble or noise, never sent to ASR
MIN_CONFIDENCE = 0.5  # second-pass words less certain than this are not added
#: real-take40's lead-in (a handling noise under a hum, 40 % voiced) came back from an isolated Scribe pass as a
#: confident "Basically" that AssemblyAI and two full-take passes never heard; its real dropped words were 84-91 %
MIN_VOICED = 0.6


@dataclass(frozen=True)
class SpeechRegion:
    gap_id: str
    start_us: int
    end_us: int
    speech_ms: float
    level_below_speech_db: float = 0.0  # how far under the take's typical speech level this sound sits
    voiced: float = 1.0  # share of its speech-like frames with a pitch (words are mostly voiced; noises are not)


def untranscribed_speech(source: Any, words: list[Word], gaps: list[Gap], *,
                         min_speech_ms: float = MIN_SPEECH_MS) -> list[SpeechRegion]:
    """Stretches of speech-like sound inside gaps that no word owns (see module docstring)."""
    from studio.perception import gaps as G

    if not G._audio_available(source):
        return []
    feat = G._features(source)
    if feat.n_frames == 0 or not gaps:
        return []
    thr = G._thresholds(feat, words)
    speech = thr.active & (feat.vad >= 0.5) & ~feat.breath_mask & ~feat.digital
    out: list[SpeechRegion] = []
    for g in gaps:
        a_us = g.start_us + (EDGE_GUARD_US if g.after_word_id is not None else 0)
        b_us = g.end_us - (EDGE_GUARD_US if g.before_word_id is not None else 0)
        if b_us - a_us < min_speech_ms * 1000:
            continue
        k0, k1 = feat.frame_ceil(a_us), feat.frame_floor(b_us)
        if k1 <= k0:
            continue
        seg = speech[k0:k1 + 1]
        n = int(seg.sum())
        if n * feat.hop_us < min_speech_ms * 1000:
            continue
        runs = G._runs(seg)
        # merge runs across short holes; keep the longest merged stretch group(s)
        merged: list[list[int]] = []
        for ra, rb in runs:
            if merged and (ra - merged[-1][1]) * feat.hop_us < BRIDGE_US:
                merged[-1][1] = rb
            else:
                merged.append([ra, rb])
        for ra, rb in merged:
            ms = float(seg[ra:rb].sum()) * feat.hop_us / 1000
            if ms < min_speech_ms * 0.6:
                continue
            s_us = max(g.start_us, feat.frame_time(k0 + ra) - PAD_US)
            e_us = min(g.end_us, feat.frame_time(k0 + rb - 1) + PAD_US)
            lvl = feat.vb_db[k0 + ra:k0 + rb][seg[ra:rb]]
            below = float(thr.speech_vb - np.percentile(lvl, 75)) if lvl.size else 99.0
            vo = feat.pitch.voiced[k0 + ra:k0 + rb][seg[ra:rb]]
            out.append(SpeechRegion(gap_id=g.id, start_us=int(s_us), end_us=int(e_us), speech_ms=ms,
                                    level_below_speech_db=round(below, 1),
                                    voiced=round(float(vo.mean()), 2) if vo.size else 0.0))
    return out


def _excerpt(job: Job, a_us: int, b_us: int, out: Path) -> float:
    """Write ``[a, b]`` of the dialogue track, with digital silence around it; returns the excerpt's time offset
    (seconds of source time at excerpt t=0)."""
    import soundfile as sf

    info = sf.info(str(job.audio_path))
    sr = info.samplerate
    a, b = max(0, round(a_us * sr / 1e6)), min(info.frames, round(b_us * sr / 1e6))
    x, _ = sf.read(str(job.audio_path), start=a, stop=b, dtype="float32", always_2d=True)
    pad = np.zeros((round(LEAD_SILENCE_S * sr), x.shape[1]), dtype=np.float32)
    sf.write(str(out), np.concatenate([pad, x, pad]), sr, subtype="FLOAT", format="WAV")
    return a / sr - LEAD_SILENCE_S


def fill_untranscribed(job: Job, words: list[Word], gaps: list[Gap], *, provider: str | None = None,
                       settings: Settings | None = None) -> tuple[list[Word], list[dict[str, Any]]]:
    """Transcribe untranscribed speech inside ``gaps`` on its own and return ``(new words, report)``: the words
    (provisional IDs ``w9xxx``; the caller merges and re-numbers) that fall inside their gap, not overlapping any
    existing word. The report lists every region and what came of it. Never raises: without a key or on an ASR
    error the region is reported as untranscribed."""
    from studio.perception import transcribe as T
    from studio.perception.index import word_id

    regions = untranscribed_speech(job, words, gaps)
    report: list[dict[str, Any]] = []
    found: list[Word] = []
    if not regions:
        return found, report
    fill_dir = job.index_dir / "fill"
    fill_dir.mkdir(parents=True, exist_ok=True)
    spans = sorted((w.start_us, w.end_us) for w in words)
    by_id = {w.id: w for w in words}
    for r in regions:
        rec: dict[str, Any] = {"gap_id": r.gap_id, "start_us": r.start_us, "end_us": r.end_us,
                               "speech_ms": round(r.speech_ms), "below_speech_db": r.level_below_speech_db,
                               "words": []}
        if r.level_below_speech_db > MAX_BELOW_SPEECH_DB:  # a mumble, handling noise or breathing: not words
            rec["skipped"] = "too quiet for speech"
            report.append(rec)
            continue
        if r.voiced < MIN_VOICED:  # handling noise, a sigh: an isolated excerpt of it invites a hallucinated word
            rec["skipped"] = f"not voiced like speech ({r.voiced:.0%} voiced)"
            report.append(rec)
            continue
        g0 = next((x for x in gaps if x.id == r.gap_id), None)
        # context: the neighbouring words ride along (an isolated fragment invites a guess), then are dropped
        ctx_a, ctx_b = r.start_us, r.end_us
        if g0 is not None and g0.after_word_id in by_id:
            ctx_a = max(by_id[g0.after_word_id].start_us, r.start_us - CONTEXT_US)
        if g0 is not None and g0.before_word_id in by_id:
            ctx_b = min(by_id[g0.before_word_id].end_us, r.end_us + CONTEXT_US)
        key = hashlib.sha1(f"{ctx_a}-{ctx_b}".encode()).hexdigest()[:10]
        wav = fill_dir / f"excerpt_{key}.wav"
        try:
            offset = _excerpt(job, ctx_a, ctx_b, wav)
            res = T.transcribe_file(wav, provider=provider, settings=settings, cache_dir=fill_dir / key)
        except Exception as e:  # no key, network, parse: the gap stage marks the sound instead
            rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            report.append(rec)
            continue
        finally:
            with contextlib.suppress(OSError):
                wav.unlink()
        g = next((x for x in gaps if x.id == r.gap_id), None)
        lo, hi = (g.start_us, g.end_us) if g is not None else (r.start_us, r.end_us)
        for w in res.words:
            s = round(offset * 1e6) + w.start_us
            e = round(offset * 1e6) + w.end_us
            if s < lo - 80_000 or e > hi + 80_000 or e <= s or w.kind == "event":  # (a refined edge may creep in)
                continue
            if w.confidence < MIN_CONFIDENCE:
                rec.setdefault("rejected", []).append(w.text)
                continue
            s, e = max(s, lo), min(e, hi)
            # a word the transcript already has (the context) overlaps it mostly: drop; a refined neighbour's edge
            # that reaches a little into it: clip the new word to the free stretch
            if any(min(e, b) - max(s, a) > 0.5 * (e - s) for a, b in spans):
                continue
            for a, b in spans:
                if a < s < b:
                    s = b
                if a < e < b:
                    e = a
            if e - s < 20_000:
                continue
            nw = w.model_copy(update={"id": word_id(9000 + len(found) + 1), "start_us": int(s), "end_us": int(e),
                                      "chars": None})
            found.append(nw)
            rec["words"].append(w.text)
        report.append(rec)
    return found, report


def mark_untranscribed(gaps: list[Gap], regions: list[SpeechRegion]) -> list[Gap]:
    """Record speech that still has no words on its gap: ``sound_us`` (pads and pause trims never keep it) and
    kind ``noise`` (the transcript shows it), so neither the Director nor the compiler mistakes it for a pause."""
    by_gap: dict[str, list[tuple[int, int]]] = {}
    for r in regions:
        by_gap.setdefault(r.gap_id, []).append((r.start_us, r.end_us))
    out: list[Gap] = []
    for g in gaps:
        add = by_gap.get(g.id)
        if not add:
            out.append(g)
            continue
        spans = sorted([*g.sound_us, *((max(a, g.start_us), min(b, g.end_us)) for a, b in add)])
        merged: list[tuple[int, int]] = []
        for a, b in spans:
            if b <= a:
                continue
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        snap = g.snap_us
        if any(a <= snap <= b for a, b in merged):  # the quiet point must not sit inside the speech
            free = [(g.start_us, merged[0][0])] + [(merged[k][1], merged[k + 1][0]) for k in range(len(merged) - 1)] \
                + [(merged[-1][1], g.end_us)]
            a, b = max(free, key=lambda r: r[1] - r[0])
            snap = (a + b) // 2 if b > a else snap
        out.append(g.model_copy(update={"sound_us": merged, "kind": "noise" if g.kind != "breath" else g.kind,
                                        "snap_us": int(min(max(snap, g.start_us), g.end_us))}))
    return out
