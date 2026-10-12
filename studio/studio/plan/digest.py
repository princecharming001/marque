"""The planner's digest: what the standard tier's one model call reads about a take (standard-tier plan §2, step 3).

Why a digest and not the Take Index itself: the planner is a single text-only call with no tools, so it needs
every fact it may act on in one page, and nothing it may not act on (no times, no frames). The digest is the
Take Index's own transcript grammar plus the measurements the Director used to ask tools for, formatted by code:

* one line per sentence, ``s003 [c01 2/2 rec] (5.8-7.3s) w0014 The w0015 real ... w0018 restraint. [g0006 0.80s
  breath]``: fillers ``{um}``, cut-offs ``restr-``, dropout-chopped words ``[chopped]``, abandoned sentences
  ``[incomplete]``, pauses of at least :data:`INLINE_PAUSE_MIN_MS` inline with their gap ID;
* a measured block: delivery energy and pauses, SNR and music-in-room, the fillers/cut-offs by ID, the top-12
  emphasis words, the caption placement options (the same :func:`studio.compile.captions.placement_options`
  measurement the Director reads, in fewer words), the voice chain planned from the index's own measurements,
  and the platform length window.

Why every word carries its ID: half of the :class:`~studio.plan.decision.PlanDecision` fields (payoff/CTA/
must-keep ranges, filler cuts, emphasis, text fixes, markers, the punch-in anchor) are word IDs, and a model
cannot count its way from a sentence's ID range to its 11th word reliably. Inline IDs are the ``full``
transcript grammar the Director already reads, and they cost about one token per word.

What is left out on purpose: the colour measurement line waits for a free ``measure_color`` function (today it
is a Director method that needs the job's mezzanine); the voice-chain note is planned from
``TakeIndex.audio`` alone (no ``audio.wav`` needed) and is omitted when the index has no audio measurements.

Size: tokens are estimated as ``len(text) / 3.5``. Over ``max_tokens`` the digest drops whole sentence lines
from the middle outwards, never the first or the last sentence (the hook and the ending are what the planner
judges first), and marks each omitted run in place.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from studio.perception.index import Cluster, Gap, Sentence, TakeIndex, Word

__all__ = [
    "CHARS_PER_TOKEN", "EMPHASIS_TOP_N", "INLINE_PAUSE_MIN_MS", "STANDARD_MAX_OUTPUT_S",
    "Digest", "build_digest", "estimate_tokens",
]

#: Characters per token for the size estimate (English prose with IDs; measured ~3.3-3.8 on the real digests).
CHARS_PER_TOKEN = 3.5
#: Pauses at least this long appear inline as ``[g0012 0.62s]`` (the compact transcript's threshold; shorter
#: pauses are articulatory and the code plan handles them from constants.yaml priors).
INLINE_PAUSE_MIN_MS = 250.0
#: How many emphasis words the measured block lists.
EMPHASIS_TOP_N = 12
#: The standard tier ships at most this long (standard-tier-plan.md §2: "output <= 90 s, prefer 75 s").
STANDARD_MAX_OUTPUT_S = 90

# Platform facts the length line falls back on when constants.yaml is unavailable (skills/editing/platforms.md).
_PLATFORM_LENGTH: dict[str, dict[str, Any]] = {
    "tiktok": {"max_s": 600, "note": "Creator Rewards pays only for videos over 60 s; never pad to reach it"},
    "reels": {"min_s": 3, "max_s": 900, "note": "over 180 s leaves the Reels tab"},
    "shorts": {"max_s": 180, "note": "most songs are usable for 90 s at most"},
}
_DEFAULT_STYLE_WINDOW = (30.0, 60.0)


@dataclass
class Digest:
    text: str  # what the planner reads (<= max_tokens; sentence blocks dropped from the middle first)
    sentence_ids: list[str]  # every sentence id in the text, in order
    cluster_recs: dict[str, str]  # cluster id -> recommended sentence id
    stats: dict[str, Any]  # wpm, energy, pause median ms, snr_db, duration_s, words, fillers, cutoffs, music_in_room


def estimate_tokens(text: str) -> int:
    """Token estimate used for the cap: ``len(text) / 3.5``, rounded up."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# ============================================================================================ sentence lines
def _groups(ix: TakeIndex) -> list[tuple[Sentence | None, list[Word]]]:
    """Words grouped by sentence in time order; words without a sentence form their own pseudo-groups."""
    smap = ix.sentence_map
    groups: list[tuple[Sentence | None, list[Word]]] = []
    for w in ix.words:
        s = smap.get(w.sentence_id) if w.sentence_id else None
        if groups and groups[-1][0] is s and (s is not None or groups[-1][1][-1].sentence_id is None):
            groups[-1][1].append(w)
        else:
            groups.append((s, [w]))
    return groups


def _gap_token(g: Gap) -> str:
    suffix = "" if g.kind == "pause" else f" {g.kind}"
    if g.dropouts_us:
        lost = sum(b - a for a, b in g.dropouts_us) / 1e6
        suffix += f" DROPOUT {lost:.2f}s lost"
    if g.sound_us and g.kind != "noise":
        suffix += f" +{sum(b - a for a, b in g.sound_us) / 1e6:.2f}s untranscribed sound"
    return f"[{g.id} {g.duration_us / 1e6:.2f}s{suffix}]"


def _cluster_tag(ix: TakeIndex, s: Sentence) -> str:
    c = ix.cluster_map.get(s.cluster_id) if s.cluster_id else None
    if c is None:
        return ""
    k = c.sentence_ids.index(s.id) + 1 if s.id in c.sentence_ids else 0
    return f" [{c.id} {k}/{len(c.sentence_ids)}" + (" rec]" if s.id == c.recommended_sentence_id else "]")


def _sentence_line(ix: TakeIndex, s: Sentence | None, ws: list[Word]) -> str:
    head = (s.id if s is not None else "s---") + (_cluster_tag(ix, s) if s is not None else "")
    head += f" ({ws[0].start_us / 1e6:.1f}-{ws[-1].end_us / 1e6:.1f}s)"
    if s is not None and not s.complete:
        head += " [incomplete]"
    toks: list[str] = []
    for w in ws:
        tok = f"{w.id} {w.display()}"
        if w.truncated:
            tok += " [chopped]"
        toks.append(tok)
        g = ix.gap_after(w.id)
        if g is not None and g.is_inner and g.duration_ms >= INLINE_PAUSE_MIN_MS:
            toks.append(_gap_token(g))
    return f"{head} {' '.join(toks)}"


def _cluster_choice(c: Cluster, ix: TakeIndex) -> str:
    """The take the code plan keeps without a decision: the index's recommendation, else the last complete take."""
    if c.recommended_sentence_id:
        return c.recommended_sentence_id
    smap = ix.sentence_map
    complete = [sid for sid in c.sentence_ids if sid in smap and smap[sid].complete]
    return (complete or c.sentence_ids)[-1]


def _cluster_lines(ix: TakeIndex) -> list[str]:
    if not ix.clusters:
        return []
    out = ["Retake clusters (the code plan keeps the recommended take unless choose_take says otherwise):"]
    for c in ix.clusters:
        rec = _cluster_choice(c, ix)
        parts = []
        for sid in c.sentence_ids:
            s = ix.sentence_map.get(sid)
            tag = "" if s is None or s.complete else " incomplete"
            parts.append(f"{sid}{tag}")
        note = f"; {c.notes}" if c.notes else ""
        out.append(f"  {c.id}: {' -> '.join(parts)}; rec {rec}; similarity {c.similarity:.2f}{note}")
    return out


# ============================================================================================ measured block
def _spoken(ix: TakeIndex) -> list[Word]:
    return [w for w in ix.words if w.is_spoken]


def _ids_line(label: str, words: list[Word], limit: int = 24) -> str:
    if not words:
        return f"{label}: none"
    shown = ", ".join(f"{w.id} {w.display()}" for w in words[:limit])
    more = f" (+{len(words) - limit} more)" if len(words) > limit else ""
    return f"{label}: {len(words)} ({shown}{more})"


def _energy_line(ix: TakeIndex, inner_pauses: list[Gap]) -> str:
    e = ix.energy
    speech_s = sum(w.duration_us for w in _spoken(ix)) / 1e6
    if e.wpm > 0:
        energy = (f"Energy {e.overall:.2f} (wpm {e.wpm:.0f}, pitch var {e.f0_var:.2f} st, loudness var "
                  f"{e.loudness_var:.2f} dB)")
    else:
        energy = "Energy: not measured"
    if inner_pauses:
        med = statistics.median(g.duration_ms for g in inner_pauses)
        longest = max(inner_pauses, key=lambda g: g.duration_us)
        pauses = (f"pauses >= {INLINE_PAUSE_MIN_MS:.0f} ms: {len(inner_pauses)}, median {med:.0f} ms, longest "
                  f"{longest.id} {longest.duration_ms:.0f} ms {longest.kind}")
    else:
        pauses = f"pauses >= {INLINE_PAUSE_MIN_MS:.0f} ms: none"
    return f"  {energy}; {pauses}; speech {speech_s:.1f} s of {ix.duration_us / 1e6:.1f} s"


def _audio_line(ix: TakeIndex) -> str:
    a = ix.audio
    if a is None:
        return "  Audio: not measured"

    def num(v: float | None, fmt: str, unit: str) -> str:
        return "n/a" if v is None else f"{v:{fmt}}{unit}"

    return (f"  Audio: SNR {num(a.snr_db, '.1f', ' dB')}, noise floor {num(a.noise_floor_db, '.1f', ' dBFS')}, "
            f"music in room: {'yes' if a.music_in_room else 'no'}, clipping {a.clipping_ratio * 100:.2f}%, "
            f"integrated {num(a.integrated_lufs, '.1f', ' LUFS')}, true peak {num(a.true_peak_dbtp, '.1f', ' dBTP')}, "
            f"LRA {num(a.lra_lu, '.1f', ' LU')}")


def _emphasis_line(ix: TakeIndex) -> str:
    cands = [w for w in ix.words if w.kind == "word" and w.emphasis > 0.0]
    if not cands:
        return "  Emphasis: not measured"
    top = sorted(cands, key=lambda w: (-w.emphasis, w.start_us))[:EMPHASIS_TOP_N]
    return (f"  Emphasis top {len(top)} (prosodic stress 0-1; caption emphasis candidates): "
            + ", ".join(f"{w.id} {w.display()} {w.emphasis:.2f}" for w in top))


def _placement_lines(ix: TakeIndex, platform: str) -> list[str]:
    """The caption positions this take allows, measured at the median face (same source as the Director's
    option lines, condensed). Code places the pages; the planner only picks emphasis words and text fixes."""
    try:
        from studio.compile import captions as cap
        from studio.doc.model import CaptionStyle

        style = CaptionStyle()
        zone = cap.safe_zone_for(platform)
        o = cap.placement_options(ix, style=style, zone=zone)
    except Exception:  # a hand-built or partial index must never break the digest
        o = None
    if o is None:
        return ["  Caption placement: no face track; the placer uses the platform's text band"]
    bc, rf, ab = o["below_chin"], o["reframe"], o["above_head"]
    lines = [f"  Caption placement options at the base framing (median face; {o['size_px']} px one-line block "
             f"{o['block_h_px']:.0f} px tall; chin y{o['chin_px']:.0f}, eye line y{o['eye_line_px']:.0f}, head top "
             f"y{o['head_top_px']:.0f}; strict text band ends y{o['strict_band_bottom_px']:.0f}, relaxed caption floor "
             f"y{o['relaxed_floor_px']:.0f}); code places the pages with the placer's auto ladder:"]
    if bc["fits_strict"]:
        cost = "fits the strict band"
    elif bc["fits_relaxed"]:
        cost = f"{bc['into_ui_px']:.0f} px into the strict band (platform UI only with a long description)"
    else:
        cost = "does not fit above the relaxed floor at this size"
    lines.append(f"   (a) under the chin: top y{bc['top_px']:.0f}, bottom y{bc['bottom_px']:.0f}: {cost}")
    small = o.get("smaller_text_px")
    rel_small = o.get("smaller_text_relaxed_px")
    lines.append("   (b) smaller text under the chin: "
                 + (f"{small} px fits the strict band" if small else "nothing down to the 64 px floor fits")
                 + (f"; {rel_small} px above the relaxed floor" if rel_small else ""))
    rs, ss, ok = rf.get("relaxed_scale"), rf.get("strict_scale"), rf.get("clean_scale") or 1.0

    def scale_txt(v: Any) -> str:
        if v is None:
            return "n/a"
        if v == float("inf"):
            return "impossible (face at the bottom of the source)"
        return f"x{v:.2f}" + (" (past this source's clean upsampling)" if v > ok + 1e-6 else "")

    if rs is not None and rs != float("inf") and rs <= 1.0 + 1e-6:
        lines.append(f"   (c) reframe lifting the chin: not needed; this source stays clean to x{ok:.2f}")
    else:
        lines.append(f"   (c) reframe lifting the chin: {scale_txt(rs)} fits the relaxed floor, {scale_txt(ss)} the "
                     f"strict band; this source stays clean to x{ok:.2f}")
    lines.append(f"   (d) above the head: top y{ab['top_px']:.0f}, bottom y{ab['bottom_px']:.0f}, "
                 + ("clear of the hair" if ab["clear_of_hair"] else "on the hair"))
    return lines


def _voice_line(ix: TakeIndex) -> str | None:
    """The chain :func:`studio.audio.voice.plan_voice_chain` plans from the index's measurements alone (no
    ``audio.wav``): the denoise/isolation decision, HPF and dynamics. None when the index has no audio metrics."""
    a = ix.audio
    if a is None:
        return None
    try:
        from studio.audio.voice import plan_voice_chain

        m = {"snr_db_index": a.snr_db, "noise_floor_db": a.noise_floor_db, "music_in_room": a.music_in_room,
             "clipping_ratio": a.clipping_ratio}
        spec = plan_voice_chain(None, ix, measurements=m)
    except Exception:
        return None
    prov = f" ({spec.isolation_provider})" if spec.isolation_provider else ""
    return (f"  Voice chain planned from the index's measurements: denoise {spec.denoise}{prov}, HPF "
            f"{spec.hpf_hz:.0f} Hz, compression {spec.compression_db:.0f} dB at {spec.comp_ratio:.1f}:1, leveler "
            f"{'on' if spec.leveler else 'off'}, de-ess {spec.deess_db:.0f} dB, breath attenuation "
            f"{spec.breath_atten_db:.0f} dB"
            + (f"; {spec.notes}" if spec.notes else ""))


def _constants() -> dict[str, Any]:
    try:
        from studio.compile.captions import load_constants

        return load_constants() or {}
    except Exception:
        return {}


def _style_window(style_hint: str | None, consts: dict[str, Any]) -> tuple[float, float, str]:
    windows = consts.get("length_s") if isinstance(consts.get("length_s"), dict) else {}
    default = windows.get("default") if isinstance(windows, dict) else None
    lo, hi = (float(default[0]), float(default[1])) if isinstance(default, list | tuple) and len(default) == 2 \
        else _DEFAULT_STYLE_WINDOW
    if style_hint:
        key = style_hint.strip().lower().replace("-", "_").replace(" ", "_")
        for name, v in (windows or {}).items():
            if name == key and isinstance(v, list | tuple) and len(v) == 2:
                return float(v[0]), float(v[1]), name
    return lo, hi, "default"


def _length_line(ix: TakeIndex, platform: str, style_hint: str | None) -> str:
    consts = _constants()
    delivery = consts.get("delivery") if isinstance(consts.get("delivery"), dict) else {}
    facts = dict(_PLATFORM_LENGTH.get(platform, {}))
    if platform == "shorts" and isinstance(delivery.get("shorts_max_s"), int | float):
        facts["max_s"] = delivery["shorts_max_s"]
    elif platform == "tiktok" and isinstance(delivery.get("tiktok_api_max_s"), int | float):
        facts["max_s"] = delivery["tiktok_api_max_s"]
    elif platform == "reels":
        api = delivery.get("reels_api_s")
        if isinstance(api, list | tuple) and len(api) == 2:
            facts["min_s"], facts["max_s"] = api
    plat = f"platform {platform}"
    if facts.get("max_s"):
        plat += f" (up to {facts['max_s']:.0f} s"
        plat += f"; {facts['note']})" if facts.get("note") else ")"
    lo, hi, name = _style_window(style_hint, consts)
    return (f"  Length: {plat}; style window {lo:.0f}-{hi:.0f} s ({name}); standard tier ships at most "
            f"{STANDARD_MAX_OUTPUT_S} s; source {ix.duration_us / 1e6:.1f} s")


# ============================================================================================ assembly
def _head_lines(ix: TakeIndex, platform: str, brief: str | None, style_hint: str | None) -> list[str]:
    n_words = len(_spoken(ix))
    out = [f"Take digest: {ix.duration_us / 1e6:.1f} s source, {n_words} spoken words, {len(ix.sentences)} sentences, "
           f"{len(ix.clusters)} retake cluster(s); platform {platform}."]
    if brief:
        out.append(f"Brief: {' '.join(brief.split())}")
    if style_hint:
        out.append(f"Style hint: {' '.join(style_hint.split())}")
    out.append("Grammar: sNNN sentence [cNN take k/n, rec = recommended] (start-end s) [incomplete = abandoned]; "
               "wNNNN word; {um} filler; restr- cut-off; [chopped] = a recording dropout chops the word; "
               f"[gNNNN 0.62s kind] pause >= {INLINE_PAUSE_MIN_MS:.0f} ms. Decisions name these IDs, never times.")
    out.append("Transcript:")
    return out


def _omitted_marker(blocks: list[tuple[Sentence | None, list[Word], str]], run: list[int], max_tokens: int) -> str:
    first, last = blocks[run[0]], blocks[run[-1]]
    a = first[0].id if first[0] is not None else "s---"
    b = last[0].id if last[0] is not None else "s---"
    dur = (last[1][-1].end_us - first[1][0].start_us) / 1e6
    span = a if a == b else f"{a}-{b}"
    noun = "sentence" if len(run) == 1 else "sentences"
    return f"[{span} omitted: {len(run)} {noun}, {dur:.1f} s, to fit the {max_tokens}-token digest cap]"


def _assemble(head: list[str], blocks: list[tuple[Sentence | None, list[Word], str]], omitted: set[int],
              tail: list[str], max_tokens: int) -> str:
    lines = list(head)
    run: list[int] = []
    for i, (_s, _ws, line) in enumerate(blocks):
        if i in omitted:
            run.append(i)
            continue
        if run:
            lines.append(_omitted_marker(blocks, run, max_tokens))
            run = []
        lines.append(line)
    if run:
        lines.append(_omitted_marker(blocks, run, max_tokens))
    lines.extend(tail)
    return "\n".join(lines)


def _drop_order(n: int) -> list[int]:
    """Block indices in the order they are dropped: nearest the middle first, the first and last never."""
    if n <= 2:
        return []
    centre = (n - 1) / 2.0
    return sorted(range(1, n - 1), key=lambda i: (abs(i - centre), -i))


def build_digest(index: TakeIndex, *, platform: str, brief: str | None = None, style_hint: str | None = None,
                 max_tokens: int = 12_000) -> Digest:
    """Build the planner's digest of ``index`` for ``platform`` (see the module docstring for the format)."""
    ix = index
    groups = _groups(ix)
    blocks = [(s, ws, _sentence_line(ix, s, ws)) for s, ws in groups]
    # the speaker's pauses: a recording dropout is tagged inline but is not a pause they made
    inner_pauses = [g for g in ix.gaps if g.is_inner and g.duration_ms >= INLINE_PAUSE_MIN_MS and not g.is_dropout]
    fillers = [w for w in ix.words if w.kind == "filler"]
    cutoffs = [w for w in ix.words if w.kind == "cutoff" and not w.truncated]
    chopped = [w for w in ix.words if w.truncated]

    head = _head_lines(ix, platform, brief, style_hint)
    tail = [*_cluster_lines(ix), "Measured:", _energy_line(ix, inner_pauses), _audio_line(ix),
            _ids_line("  Fillers", fillers), _ids_line("  Cut-offs", cutoffs), _ids_line("  Dropout-chopped", chopped),
            _emphasis_line(ix), *_placement_lines(ix, platform)]
    voice = _voice_line(ix)
    if voice:
        tail.append(voice)
    tail.append(_length_line(ix, platform, style_hint))

    omitted: set[int] = set()
    text = _assemble(head, blocks, omitted, tail, max_tokens)
    for i in _drop_order(len(blocks)):
        if estimate_tokens(text) <= max_tokens:
            break
        omitted.add(i)
        text = _assemble(head, blocks, omitted, tail, max_tokens)

    sentence_ids = [s.id for i, (s, _ws, _l) in enumerate(blocks) if i not in omitted and s is not None]
    omitted_ids = [s.id for i, (s, _ws, _l) in enumerate(blocks) if i in omitted and s is not None]
    cluster_recs = {c.id: _cluster_choice(c, ix) for c in ix.clusters}
    a = ix.audio
    stats: dict[str, Any] = {
        "wpm": ix.energy.wpm,
        "energy": ix.energy.overall,
        "pause_median_ms": statistics.median(g.duration_ms for g in inner_pauses) if inner_pauses else None,
        "snr_db": a.snr_db if a is not None else None,
        "duration_s": ix.duration_us / 1e6,
        "words": len(_spoken(ix)),
        "fillers": len(fillers),
        "cutoffs": len(cutoffs),
        "chopped": len(chopped),
        "music_in_room": bool(a.music_in_room) if a is not None else False,
        "sentences": len(ix.sentences),
        "clusters": len(ix.clusters),
        "chars": len(text),
        "tokens_est": estimate_tokens(text),
        "omitted_sentence_ids": omitted_ids,
    }
    return Digest(text=text, sentence_ids=sentence_ids, cluster_recs=cluster_recs, stats=stats)
