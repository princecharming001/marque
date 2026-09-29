"""Tests for :mod:`studio.qa` (metrics packet, invariants, report).

Keyless tests run on synthetic renders built by ``qa_fixtures`` from the shared hand-built Take Index
and CutDocument: faults are injected into otherwise clean deliverables (a click at a seam, digital
silence under a word, a gain error, an audio offset, black/frozen frames, text over the face or in the
platform UI, a non-faststart file with an edit list …) and each must be caught by the right metric and
invariant while the clean render passes all ten. The ASR round-trip is exercised with a scripted
transcriber offline and, marked ``real``, with ElevenLabs Scribe on a render cut from a real take.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from conftest import build_cut_doc, build_take_index
from qa_fixtures import (
    SR,
    _pink,
    assemble_from_timeline,
    encode_final,
    make_render,
    master,
    synth_source_audio,
    to_stereo,
)

from studio.compile.models import FramingKey, Timeline
from studio.compile.timeline import audio_seams
from studio.compile.timeline import compile as compile_timeline
from studio.doc.model import AssetRef, Insert, Licence, Pins
from studio.jobs import Job
from studio.perception.index import AsrInfo, AsrResult, TakeIndex, Word
from studio.qa import invariants as inv
from studio.qa import metrics as qm
from studio.qa import report as qr
from studio.timebase import sample_index

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")

#: skip the slow whole-file extras (CAMBI, ESTOI, black/freeze decode) where a test does not need them
FAST = {"asr": False, "banding": False, "estoi": False, "video_events": False}


# ============================================================================================ environment
@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    """One job with the fixture index + document, its compiled timeline and a synthetic source dialogue
    track saved as ``media/audio.wav`` (the sync probes and the source-differential click check read it)."""
    root = tmp_path_factory.mktemp("qa")
    ix = build_take_index()
    doc = build_cut_doc(ix)
    job = Job.create("qa-job", work_dir=root)
    job.save_media_info(ix.media)
    job.save_index(ix)
    job.save_doc(doc)
    tl = compile_timeline(doc, ix, job=job)
    src = synth_source_audio(ix)
    sf.write(str(job.audio_path), src.astype(np.float32), SR, subtype="FLOAT")
    yield SimpleNamespace(job=job, index=ix, doc=doc, timeline=tl, source=src, cache={})
    shutil.rmtree(root, ignore_errors=True)  # renders are ~10 MB each: do not leave them in pytest's basetemp


def _render(env: SimpleNamespace, name: str, **kw: Any) -> dict[str, Any]:
    """Build (once per module) a render dir with the given faults."""
    if name not in env.cache:
        rd = env.job.renders_dir / f"r_{name}"
        paths = make_render(rd, env.timeline, env.index, source=env.source, **kw)
        env.cache[name] = {**paths, "dir": rd}
    return env.cache[name]


def _measure(env: SimpleNamespace, r: dict[str, Any], **kw: Any) -> qm.MetricsPacket:
    args = {**FAST, **kw}
    return qm.measure(env.job, env.timeline, r["final"], doc=env.doc, index=env.index, render_dir=r["dir"], **args)


def _by_number(results: list[inv.InvariantResult]) -> dict[int, inv.InvariantResult]:
    return {r.number: r for r in results}


def _failed(results: list[inv.InvariantResult]) -> set[int]:
    return {r.number for r in results if not r.passed}


# ============================================================================================ clean render
@pytest.fixture(scope="module")
def clean(env: SimpleNamespace) -> SimpleNamespace:
    r = _render(env, "clean")
    pk = qm.measure(env.job, env.timeline, r["final"], doc=env.doc, index=env.index, render_dir=r["dir"], asr=False)
    results = inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk, asr=False)
    return SimpleNamespace(r=r, pk=pk, results=results)


def test_clean_render_passes_all_ten_invariants(clean: SimpleNamespace) -> None:
    assert [r.number for r in clean.results] == list(range(1, 11))
    assert inv.all_passed(clean.results), inv.summarize(clean.results)
    assert {r.id for r in clean.results} == {v[0] for v in inv.INVARIANTS.values()}
    assert (clean.r["dir"] / "qa" / "invariants.json").exists()
    saved = json.loads((clean.r["dir"] / "qa" / "invariants.json").read_text())
    assert saved["passed"] is True and len(saved["results"]) == 10


def test_clean_render_metrics(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    pk = clean.pk
    assert pk.errors == []
    # loudness on the encoded AAC, two meters
    L = pk.loudness
    assert L is not None and L.within_target and L.true_peak_ok
    assert abs(L.integrated_lufs - (-14.0)) < 0.5
    assert L.integrated_ffmpeg_lufs is not None and abs(L.integrated_ffmpeg_lufs - L.integrated_lufs) < 0.3
    assert L.true_peak_dbtp <= -1.0 and L.true_peak_ffmpeg_dbtp is not None
    # every audio seam analysed, none clicks; the 10 ms equal-power crossfades leave a clear margin
    assert len(pk.clicks) == len(audio_seams(env.timeline)) == 2
    assert not pk.clicks_found
    assert all(c.margin_db is not None and c.margin_db < -2.0 for c in pk.clicks)
    assert [(c.left_word, c.right_word) for c in pk.clicks] == [("w0008", "w0014"), ("w0018", "w0020")]
    assert pk.silence_under_speech == []
    assert pk.unexpected_video_events == []
    # A/V: exact frames, audio within one AAC frame of padding, measured sample-exact sync
    av = pk.av
    assert av is not None and av.ok, av.problems
    assert av.frame_diff == 0 and av.video_frames == env.timeline.frame_count
    assert 0 <= av.sample_diff <= av.codec_tail_allowance == 1024
    assert av.mix_exact is True
    assert av.sync and all(s.reliable for s in av.sync)
    assert av.sync_max_abs_ms is not None and av.sync_max_abs_ms < 0.5
    # timeline-level structure
    assert [c.kind for c in pk.cuts] == ["retake", "filler"]
    assert pk.cuts[0].removed_word_ids == [f"w{n:04d}" for n in range(9, 14)]
    assert pk.cuts[1].removed_word_ids == ["w0019"]
    assert not any(c.inside_word or c.inside_clause for c in pk.cuts)
    assert pk.integrity.clipped == [] and pk.integrity.leaked == []
    p = pk.pacing
    assert p is not None and p.seams == 2 and p.words_kept == 35 and p.words_removed == 6
    assert p.time_to_first_speech_s is not None and 0.0 < p.time_to_first_speech_s < 0.5
    assert p.final_word_to_end_s is not None and 0.0 < p.final_word_to_end_s < 1.0
    assert p.seams_per_min == pytest.approx(2 / (float(env.timeline.duration) / 60), abs=0.01)
    assert p.pause_median_ms is not None and p.source_pause_median_ms is not None
    # the extras ran on the primary deliverable
    assert pk.intelligibility is not None and pk.intelligibility.estoi is not None and pk.intelligibility.estoi > 0.95
    assert pk.banding is not None and pk.banding.frames > 0 and pk.banding.cambi_max is not None
    assert pk.asr is not None and not pk.asr.ran and pk.asr.skipped_reason
    # saved + JSON round trip
    saved = clean.r["dir"] / "qa" / f"metrics_{clean.r['final'].stem}.json"
    assert saved.exists()
    again = qm.MetricsPacket.load(saved)
    assert again.loudness == pk.loudness and len(again.clicks) == 2
    summ = pk.summary()
    json.dumps(summ)
    assert summ["file"] == "final_tiktok.mp4" and summ["clicks"] == []


# ============================================================================================ injected faults
def test_click_at_seam_is_caught(env: SimpleNamespace) -> None:
    """A single-sample spike on the first seam: the seam detector flags it, invariant 1 fails with the
    word IDs either side, and the other seam stays clean."""
    r = _render(env, "click_spike", click_at_seam=0, click_kind="spike")
    pk = _measure(env, r)
    found = pk.clicks_found
    assert [c.seam for c in found] == [0]
    assert found[0].margin_db > 0 and found[0].rule in ("discontinuity", "impulse")
    assert (found[0].left_word, found[0].right_word) == ("w0008", "w0014")
    assert found[0].out_t == pytest.approx(float(audio_seams(env.timeline)[0]), abs=1e-3)
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[1].passed
    assert "click at seam 0" in res[1].detail and {"w0008", "w0014"} <= set(res[1].refs)
    assert res[1].data["clicks"] and res[1].data["clicks"][0]["seam"] == 0
    assert any("Click at seam 0" in a for a in pk.advice)


def test_step_discontinuity_at_seam_is_caught(env: SimpleNamespace) -> None:
    """A waveform step (hard splice with a DC offset) on the second seam."""
    r = _render(env, "click_step", click_at_seam=1, click_kind="step")
    pk = _measure(env, r)
    assert [c.seam for c in pk.clicks_found] == [1]
    assert pk.clicks_found[0].rule == "discontinuity"


def test_digital_silence_under_speech_is_caught(env: SimpleNamespace) -> None:
    r = _render(env, "silence", silence_word="w0024", silence_ms=80.0)
    pk = _measure(env, r)
    bad = pk.silence_under_speech
    assert len(bad) == 1
    run = bad[0]
    assert run.under_speech and run.in_program and "w0024" in run.word_ids
    assert 60.0 <= run.duration_ms <= 85.0  # AAC smears the edges of the hole a little
    span = env.timeline.word_map["w0024"]
    assert float(span.out_start) <= run.start_s <= run.end_s <= float(span.out_end)
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert _failed(list(res.values())) == {9}
    assert "w0024" in res[9].refs and "digital silence" in res[9].detail


def test_short_dropout_in_the_mix_is_caught(env: SimpleNamespace) -> None:
    """A 12 ms hole under a word: AAC's MDCT smears it above −90 dBFS in the encoded file, but the PCM mix
    still has it (and it is audible), so invariant 9 fails from the mix scan."""
    r = _render(env, "dropout12", silence_word="w0027", silence_ms=12.0)
    pk = _measure(env, r)
    bad = pk.silence_under_speech
    assert len(bad) == 1 and "w0027" in bad[0].word_ids, bad
    assert bad[0].source == "mix" and bad[0].duration_ms == pytest.approx(12.0, abs=0.05)
    assert not [s for s in pk.digital_silence if s.source == "final" and s.under_speech]  # AAC hid it
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[9].passed and "w0027" in res[9].refs
    # the unit: a mix-only run is kept, one the encoded file already has is not repeated
    mix = np.full((2, SR), 1e-3, dtype=np.float32)
    mix[:, 1000:1600] = 0.0
    mix[:, 9000:9600] = 0.0
    assert qm.mix_only_silence([(8990, 9610)], mix, SR) == [(1000, 1600)]


def test_loudness_error_fails_invariant_8(env: SimpleNamespace) -> None:
    r = _render(env, "quiet", gain_db=-3.0)
    pk = _measure(env, r)
    assert pk.loudness.within_target is False
    assert pk.loudness.integrated_lufs == pytest.approx(-17.0, abs=0.3)
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[8].passed and "LUFS" in res[8].detail
    assert res[1].passed and res[9].passed


def test_true_peak_over_ceiling_fails_invariant_8(env: SimpleNamespace) -> None:
    r = _render(env, "hot", gain_db=+3.5)  # pushes the limited master (≈ −3.8 dBTP here) over −1 dBTP
    pk = _measure(env, r)
    assert pk.loudness.true_peak_ok is False
    assert pk.loudness.true_peak_dbtp > -1.0
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[8].passed and "true peak" in res[8].detail


def test_audio_offset_is_measured_and_gated(env: SimpleNamespace) -> None:
    """The per-segment sync probe measures a real audio delay; > 1 frame fails invariant 2 even though
    every stream count is right."""
    r = _render(env, "late45", offset_ms=45.0)
    pk = _measure(env, r)
    lags = [s.lag_ms for s in pk.av.sync if s.reliable]
    assert lags and all(abs(lag - 45.0) < 0.5 for lag in lags)
    assert pk.av.frame_diff == 0
    assert pk.av.ok is False and any("measured audio offset" in p for p in pk.av.problems)
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[2].passed and "measured audio offset" in res[2].detail


def test_small_audio_offset_is_advice_not_failure(env: SimpleNamespace) -> None:
    r = _render(env, "late10", offset_ms=10.0)
    pk = _measure(env, r)
    assert pk.av.ok is True
    assert pk.av.sync_max_abs_ms == pytest.approx(10.0, abs=0.5)
    assert any("Measured audio offset" in a for a in pk.advice)


def test_black_and_frozen_picture_are_reported(env: SimpleNamespace) -> None:
    r = _render(env, "blackfreeze", black=(1.0, 1.6), freeze=(8.0, 9.5))
    pk = _measure(env, r, video_events=True)
    kinds = {(e.kind, e.expected) for e in pk.video_events}
    assert ("black", False) in kinds and ("freeze", False) in kinds
    black = next(e for e in pk.video_events if e.kind == "black")
    assert black.start_s == pytest.approx(1.0, abs=0.05) and black.duration_s == pytest.approx(0.63, abs=0.08)
    assert black.refs == ["seg001"]
    freeze = next(e for e in pk.video_events if e.kind == "freeze" and not e.expected)
    assert freeze.start_s == pytest.approx(8.0, abs=0.05) and freeze.refs == ["seg004"]
    assert any(a.startswith("Black") for a in pk.advice) and any(a.startswith("Freeze") for a in pk.advice)


def test_freeze_under_a_designed_card_is_expected(env: SimpleNamespace) -> None:
    card = next(i for i in env.timeline.inserts if i.insert_id == "i001")
    a, b = float(card.out_start) + 0.1, float(card.out_end) - 0.1
    ev = qm._classify_video_events([qm.VideoEvent(kind="freeze", start_s=a, end_s=b, duration_s=b - a)], env.timeline)
    assert ev[0].expected and ev[0].refs == ["i001"]


def test_delivery_format_problems_fail_invariant_10(env: SimpleNamespace) -> None:
    rd = env.job.renders_dir / "r_badformat"
    r = make_render(rd, env.timeline, env.index, source=env.source,
                    encode_kw={"faststart": False, "editlist": True, "tag": False})
    pk = _measure(env, {**r, "dir": rd})
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, rd, metrics=pk, asr=False,
                                          save=False))
    d = res[10].detail
    assert not res[10].passed
    assert "moov atom not before mdat" in d and "edit list present" in d and "color_primaries" in d
    boxes = inv.mp4_boxes(r["final"])
    assert boxes["has_edit_list"] and boxes["top"].index("mdat") < boxes["top"].index("moov")
    good = inv.mp4_boxes(_render(env, "clean")["final"])
    assert not good["has_edit_list"] and good["top"].index("moov") < good["top"].index("mdat")


def test_missing_deliverable_and_wrong_size(env: SimpleNamespace) -> None:
    reels = env.doc.deliverables[0].model_copy(update={"platform": "reels"})
    doc = env.doc.model_copy(update={"deliverables": [*env.doc.deliverables, reels]})
    r = _render(env, "clean")
    res = _by_number(inv.check_invariants(env.job, doc, env.index, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert not res[10].passed and "final_reels.mp4" in res[10].detail
    probe = qm.probe_streams(r["final"], count_packets=True)
    probs, facts = inv.delivery_problems(r["final"], env.timeline, probe, platform="tiktok", size=(720, 1280))
    assert any("size 1080x1920" in p for p in probs) and facts["frames"] == env.timeline.frame_count


# ============================================================================================ text placement
def _write_overlay(path: Path, timeline: Timeline, boxes: list[tuple[int, int, int, int, float, float]]) -> Path:
    """ProRes 4444 overlay layer with straight alpha: opaque white rectangles ``(x, y, w, h, t0, t1)``."""
    fps = Fraction(timeline.fps)
    chain = [f"color=c=black@0.0:s={timeline.width}x{timeline.height}:r={fps.numerator}/{fps.denominator}",
             "format=yuva444p10le"]
    for x, y, w, h, t0, t1 in boxes:
        chain.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=white@1.0:t=fill:replace=1:"
                     f"enable='between(t,{t0},{t1})'")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", ",".join(chain),
                    "-frames:v", str(timeline.frame_count), "-c:v", "prores_ks", "-profile:v", "4444",
                    "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", str(path)], check=True)
    return path


def _render_with_overlay(env: SimpleNamespace, name: str, boxes: list[tuple[int, int, int, int, float, float]]
                         ) -> dict[str, Any]:
    r = _render(env, name)
    ovl = r["dir"] / "overlays.mov"
    if not ovl.exists():
        _write_overlay(ovl, env.timeline, boxes)
        man = json.loads((r["dir"] / "render.json").read_text())
        man["overlays"] = "overlays.mov"
        (r["dir"] / "render.json").write_text(json.dumps(man))
    return r


def test_face_protected_rect_matches_face_track(env: SimpleNamespace) -> None:
    rect = qm.face_protected_rect(env.timeline, env.index, Fraction(1))
    assert rect is not None
    x0, y0, x1, y1 = rect
    # face box ≈ centre (0.5, 0.32), 0.34 x 0.2 of a 1080x1920 frame with no punch-in
    assert 380 < x0 < 420 and 660 < x1 < 700 and 490 < y0 < 520 and 760 < y1 < 790
    # under the full-frame card the face is not on screen
    card = next(i for i in env.timeline.inserts if i.insert_id == "i001")
    assert qm.face_protected_rect(env.timeline, env.index, Fraction(card.out_start) + Fraction(1, 10)) is None


def test_face_protected_rect_follows_framing_transform(env: SimpleNamespace) -> None:
    base = qm.face_protected_rect(env.timeline, env.index, Fraction(1))
    segs = list(env.timeline.segments)
    segs[0] = segs[0].model_copy(update={"framing": [FramingKey(out_t=Fraction(0), scale=1.5, cx=0.5, cy=0.32)]})
    tl = env.timeline.model_copy(update={"segments": segs})
    punched = qm.face_protected_rect(tl, env.index, Fraction(1))
    assert base is not None and punched is not None
    wb, wp = base[2] - base[0], punched[2] - punched[0]
    assert wp == pytest.approx(1.5 * wb, rel=0.03)
    # centred on the face: the centre stays put while the box grows around it
    assert (punched[0] + punched[2]) / 2 == pytest.approx((base[0] + base[2]) / 2, abs=6)


def test_planned_boxes_flag_face_and_safe_zone(env: SimpleNamespace) -> None:
    clean = qm.planned_text_boxes(env.timeline, env.index, "tiktok")
    assert len(clean) == len(env.timeline.captions) + len(env.timeline.texts)
    assert all(not b.issues for b in clean), [(b.id, b.issues) for b in clean if b.issues]
    caps = list(env.timeline.captions)
    caps[1] = caps[1].model_copy(update={"y_norm": 0.33})  # over the eyes/mouth
    caps[2] = caps[2].model_copy(update={"y_norm": 0.95})  # in the caption/UI strip at the bottom
    texts = [env.timeline.texts[0].model_copy(update={"y_norm": 0.03})]  # under the status/top UI
    tl = env.timeline.model_copy(update={"captions": caps, "texts": texts})
    boxes = {b.id: b for b in qm.planned_text_boxes(tl, env.index, "tiktok")}
    assert "covers_face" in boxes[caps[1].page_id].issues
    assert boxes[caps[1].page_id].face_overlap_px > 0
    assert "outside_safe_zone" in boxes[caps[2].page_id].issues
    assert boxes["t001"].issues == [] or "outside_safe_zone" not in boxes["t001"].issues  # clamped like the renderer
    assert set(boxes[caps[1].page_id].refs) == {w.word_id for w in caps[1].words}


def test_rendered_overlay_clean_passes(env: SimpleNamespace) -> None:
    # white blocks where the caption module placed the pages (centre y 930 px, clear of the chin)
    r = _render_with_overlay(env, "ovl_clean", [(300, 890, 480, 80, 0.0, 12.0)])
    pk = _measure(env, r)
    assert pk.text_source == "rendered"
    assert pk.text and not pk.text_issues, [(t.id, t.issues, t.at_s) for t in pk.text_issues]
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert res[7].passed and "rendered" in res[7].detail


def test_rendered_overlay_over_face_and_in_ui_fails(env: SimpleNamespace) -> None:
    boxes = [(300, 890, 480, 80, 0.0, 12.0),  # fine
             (450, 560, 180, 120, 1.0, 1.9),  # over the eyes/mouth
             (200, 1600, 600, 90, 8.9, 9.6)]  # below the caption floor (TikTok UI)
    r = _render_with_overlay(env, "ovl_bad", boxes)
    pk = _measure(env, r)
    assert pk.text_source == "rendered"
    kinds = {i for t in pk.text_issues for i in t.issues}
    assert kinds == {"covers_face", "outside_safe_zone"}
    face = next(t for t in pk.text_issues if "covers_face" in t.issues)
    assert 1.0 <= face.at_s <= 1.95 and face.face_overlap_px > 1000
    ui = next(t for t in pk.text_issues if "outside_safe_zone" in t.issues)
    assert 8.85 <= ui.at_s <= 9.65 and ui.box[1] >= 1590
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[7].passed
    assert "covers_face" in res[7].detail and "outside_safe_zone" in res[7].detail


def test_rendered_overlay_under_card_is_ignored(env: SimpleNamespace) -> None:
    card = next(i for i in env.timeline.inserts if i.insert_id == "i001")
    a, b = float(card.out_start) + 0.1, float(card.out_end) - 0.1
    r = _render_with_overlay(env, "ovl_card", [(0, 0, 1080, 1920, a, b)])  # the card fills the frame
    boxes = qm.rendered_text_boxes(env.timeline, env.index, r["dir"] / "overlays.mov", "tiktok")
    assert not [t for t in boxes if t.issues]


# ============================================================================================ edit structure
def test_word_integrity_detects_clipped_and_leaked_words(env: SimpleNamespace, take_index: TakeIndex) -> None:
    segs = list(env.timeline.segments)
    w14 = take_index.word("w0014")
    w13 = take_index.word("w0013")
    # seg002 audio starts 60 ms into its first word; seg001's audio runs on into the removed false start
    segs[1] = segs[1].model_copy(update={"audio_src_in_us": w14.start_us + 60_000})
    segs[0] = segs[0].model_copy(update={"audio_src_out_us": take_index.word("w0009").end_us})
    tl = env.timeline.model_copy(update={"segments": segs})
    res = qm.word_integrity(tl, take_index)
    assert [d["word_id"] for d in res.clipped] == ["w0014"]
    assert res.clipped[0]["side"] == "start" and res.clipped[0]["missing_ms"] == pytest.approx(60.0, abs=0.1)
    assert "w0009" in [d["word_id"] for d in res.leaked] and w13.id not in [d["word_id"] for d in res.leaked]


def test_cut_inside_clause_and_word(env: SimpleNamespace, take_index: TakeIndex, cut_doc: Any) -> None:
    # remove "follow me for more" from the CTA: a content seam between "helped," and "simple"
    doc = cut_doc.model_copy(deep=True)
    seg4 = doc.segments[3]
    doc.segments[3:] = [seg4.model_copy(update={"to_word": "w0031"}),
                        seg4.model_copy(update={"id": "seg005", "from_word": "w0036"})]
    tl = compile_timeline(doc, take_index)
    last = qm.cut_checks(tl, take_index)[-1]
    assert (last.left_word, last.right_word) == ("w0031", "w0036") and last.kind == "content"
    assert last.removed_word_ids == ["w0032", "w0033", "w0034", "w0035"]
    assert last.inside_clause is False  # "helped," closes a clause
    assert last.inside_word is False
    # remove "every single" instead: "tips | week." joins inside the clause
    doc2 = cut_doc.model_copy(deep=True)
    doc2.segments[3:] = [seg4.model_copy(update={"to_word": "w0038"}),
                         seg4.model_copy(update={"id": "seg005", "from_word": "w0041"})]
    tl2 = compile_timeline(doc2, take_index)
    last2 = qm.cut_checks(tl2, take_index)[-1]
    assert (last2.left_word, last2.right_word) == ("w0038", "w0041") and last2.inside_clause is True
    pk = qm.MetricsPacket(cuts=[last2])
    assert any("inside a clause" in a for a in qm._advice(pk, {}, None))
    # a picture seam that lands inside a kept word's output span is flagged
    span = tl2.word_map["w0041"]
    seam_t = Fraction(tl2.segments[-1].out_start)
    moved = span.model_copy(update={"out_start": seam_t - Fraction(1, 10), "out_end": Fraction(span.out_end)})
    fake = tl2.model_copy(update={"word_map": {**tl2.word_map, "w0041": moved}})
    assert qm.cut_checks(fake, take_index)[-1].inside_word is True


def test_jl_cuts_are_not_cuts_inside_words(env: SimpleNamespace, take_index: TakeIndex, cut_doc: Any) -> None:
    """A J cut puts the picture seam inside the incoming word on purpose (its voice already runs): only
    the audio edit counts for invariant 1, and the click detector looks at the shifted audio seam."""
    from studio.doc.model import SeamTreatment

    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="jcut", lead_ms=400)})
    segs[2] = segs[2].model_copy(update={"seam_in": SeamTreatment(kind="lcut", lead_ms=400)})
    tl = compile_timeline(cut_doc.model_copy(update={"segments": segs}), take_index)
    cuts = qm.cut_checks(tl, take_index)
    j = cuts[0]
    assert j.audio_out_t < j.out_t  # the voice leads the picture
    heard = [w for w, sp in tl.word_map.items() if sp is not None
             and float(sp.out_start) + 0.01 < j.out_t < float(sp.out_end) - 0.01]
    assert heard  # the picture cut lands mid-word, by design
    assert not any(c.inside_word for c in cuts)
    assert qm.word_integrity(tl, take_index).clipped == []
    assert [float(t) for t in audio_seams(tl)] == pytest.approx([c.audio_out_t for c in cuts], abs=1e-3)


def test_face_seam_advice_follows_framing_doctrine() -> None:
    """Take matching (face within 5 %, eye line within 2 % of the frame) or a ≥ ×1.25 change; a same-size
    jump is fine between beats and worth hiding inside a thought."""
    def adv(ratio: float, shift: float, beat: bool) -> list[str]:
        c = qm.CutCheck(seam=3, out_t=4.0, audio_out_t=4.0, kind="content", left_word="w0010", right_word="w0011",
                        face_shift_px=shift, face_scale_ratio=ratio, beat_boundary=beat)
        return qm._face_seam_advice(c, {}, 1920)

    assert adv(1.0, 20.0, False) == []  # 1 % of the frame: matched
    jump = adv(1.01, 120.0, False)
    assert len(jump) == 1 and "inside a thought" in jump[0] and "120 px" in jump[0] and "×1.25" in jump[0]
    assert adv(1.01, 120.0, True) == []  # an honest jump between beats
    assert "accident" in adv(1.07, 0.0, True)[0]
    assert "a bump" in adv(1 / 1.12, 0.0, False)[0]  # a zoom-out counts the same
    assert "lean-in" in adv(1.2, 60.0, False)[0]
    assert adv(1.3, 200.0, False) == []  # a deliberate punch hides the seam
    assert adv(1.0, 0.0, False) == [] and qm._face_seam_advice(
        qm.CutCheck(seam=0, out_t=0, audio_out_t=0, kind="content"), {}, 1920) == []
    # fixture seams are between sentences
    ix = build_take_index()
    cuts = qm.cut_checks(compile_timeline(build_cut_doc(ix), ix), ix)
    assert [c.beat_boundary for c in cuts] == [True, True]


def test_pacing_metrics_values(env: SimpleNamespace, take_index: TakeIndex) -> None:
    p = qm.pacing_metrics(env.timeline, take_index, cuts=qm.cut_checks(env.timeline, take_index), doc=env.doc)
    first = min(float(s.out_start) for s in env.timeline.word_map.values() if s is not None)
    last = max(float(s.out_end) for s in env.timeline.word_map.values() if s is not None)
    assert p.time_to_first_speech_s == pytest.approx(first, abs=1e-3)
    assert p.final_word_to_end_s == pytest.approx(float(env.timeline.duration) - last, abs=1e-3)
    assert p.content_seams == 2 and p.cuts_inside_clauses == 0
    assert p.payoff_position_frac == pytest.approx(float(env.timeline.word_map["w0018"].out_start)
                                                   / float(env.timeline.duration), abs=1e-3)
    assert p.fillers_kept == 0 and p.words_removed == 6
    assert p.caption_cps_max is not None and p.caption_cps_median is not None
    assert p.longest_static_s > 0


# ============================================================================================ low-level detectors
def _speechy(seed: int = 1, seconds: float = 1.0) -> np.ndarray:
    """Voiced-speech-like tone at −20 dBFS RMS: 39 harmonics with a −12 dB/oct source tilt, a formant near
    600 Hz, vibrato, syllabic modulation and a −65 dBFS noise floor."""
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * SR)) / SR
    f0 = 130 + 20 * np.sin(2 * np.pi * 1.5 * t + seed)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    x = sum(np.sin(h * ph + rng.uniform(0, 2 * np.pi)) / h ** 2 * (1 + 2 * np.exp(-((h * 130 - 600) / 200) ** 2))
            for h in range(1, 40))
    x = x / np.sqrt(np.mean(x * x)) * 0.1
    return x * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t) ** 2) + 10 ** (-65 / 20) * rng.standard_normal(t.size)


def test_click_detector_units() -> None:
    a, b = _speechy(1), _speechy(7)
    n = SR // 2
    # a clean equal-power crossfaded splice at 0.5 s
    X = round(0.010 * SR)
    g = np.sin((np.arange(X) + 0.5) / X * np.pi / 2)
    clean = np.concatenate([a[: n - X // 2], a[n - X // 2: n + X // 2] * g[::-1] + b[n - X // 2: n + X // 2] * g,
                            b[n + X // 2:]])
    spike = clean.copy()
    spike[n] += 0.01  # a single-sample spike at −40 dBFS, 20 dB below the speech RMS
    step = clean.copy()
    step[n:n + 1440] += 0.08 * np.exp(-np.arange(1440) / 190.0)  # a decaying DC step
    hard = np.concatenate([a[:n], b[n:]])  # an unfaded splice between two different "words"
    per = [qm.click_features(x, SR, n) for x in (clean, spike, step, hard)]
    assert per[0]["click"] is False and per[0]["margin_db"] < -3
    assert per[1]["click"] is True and per[1]["margin_db"] > 0
    assert per[1]["lpc_spike_db"] > per[0]["lpc_spike_db"] + 6  # the impulse is unpredictable
    assert per[2]["click"] is True and per[2]["rule"] == "discontinuity"
    assert per[3]["click"] is True
    res = qm.detect_seam_clicks(np.vstack([clean, spike]), SR, [Fraction(1, 2)], labels=[("w0001", "w0002")])
    assert len(res) == 1 and res[0].click and res[0].channel == 1  # the worst channel is kept
    assert (res[0].left_word, res[0].right_word) == ("w0001", "w0002") and res[0].sample == n
    # an identical transient already in the source at the join points is inherited, not introduced
    res2 = qm.detect_seam_clicks(spike, SR, [Fraction(1, 2)], source=spike, source_samples=[(n, n)])
    assert res2[0].click is False and res2[0].rule == "inherited"
    # inaudible (below −60 dBFS) spikes are ignored
    quiet = clean * 1e-4
    quiet[n] += 1e-5
    assert qm.click_features(quiet, SR, n)["click"] is False
    # too close to the file edge: no verdict rather than a guess
    assert qm.click_features(clean, SR, 100) is None


REAL_TAKE = Path("/Users/home/studio-testdata/qa-editor-d030-h264.mov")


@pytest.mark.skipif(not REAL_TAKE.exists(), reason="QA test take not available")
def test_click_detector_on_real_speech(tmp_path: Path) -> None:
    """Calibration check on a real talking-head take (offline): natural speech positions and 10 ms
    equal-power splices never click; single-sample spikes 20 dB below the speech RMS almost always do."""
    wav = tmp_path / "take.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(REAL_TAKE), "-map", "0:a:0",
                    "-ac", "1", "-ar", str(SR), "-c:a", "pcm_f32le", str(wav)], check=True)
    x, _ = sf.read(str(wav), dtype="float64")
    x = x / (np.sqrt(np.mean(x * x)) + 1e-12) * 0.1  # −20 dBFS RMS
    rng = np.random.default_rng(11)
    H = round(0.08 * SR)
    X = round(0.010 * SR)
    g = np.sin((np.arange(X) + 0.5) / X * np.pi / 2)

    def speech_at(n: int) -> bool:
        return 20 * np.log10(np.sqrt(np.mean(x[n - 1440:n + 1440] ** 2)) + 1e-12) > -40

    pos = [int(n) for n in rng.integers(SR, x.size - SR, 4000) if speech_at(int(n))][:120]
    assert len(pos) >= 100
    natural = [qm.click_features(x[n - H:n + H], SR, H) for n in pos[:60]]
    splices = []
    for n1, n2 in zip(pos[:60], pos[60:120], strict=True):
        fade = x[n1 - X // 2:n1 + X // 2] * g[::-1] + x[n2 - X // 2:n2 + X // 2] * g
        y = np.concatenate([x[n1 - H:n1 - X // 2], fade, x[n2 + X // 2:n2 + H]])
        splices.append(qm.click_features(y, SR, H))
    spikes = []
    for n in pos[:60]:
        y = x[n - H:n + H].copy()
        y[H] += 0.01 * rng.choice([-1.0, 1.0])
        spikes.append(qm.click_features(y, SR, H))
    assert not any(f["click"] for f in natural if f)
    assert not any(f["click"] for f in splices if f)
    caught = np.mean([f["click"] for f in spikes if f])
    assert caught >= 0.85, caught


def test_digital_silence_units() -> None:
    x = np.full((2, SR), 1e-3)
    x[:, 1000:1000 + 480] = 0.0  # exactly 10 ms
    x[:, 30000:30000 + 200] = 0.0  # 4 ms: too short
    x[0, 40000:41000] = 0.0  # only one channel silent: not digital silence
    runs = qm.digital_silence_runs(x, SR)
    assert runs == [(1000, 1480)]
    words = [("w0001", 0.01, 0.2), ("w0002", 0.5, 0.7)]
    cls = qm.classify_silence([*runs, (round(0.3 * SR), round(0.4 * SR))], SR, words)
    assert cls[0].under_speech and cls[0].word_ids == ["w0001"]
    assert not cls[1].under_speech and cls[1].in_program  # between words: still a hole in the programme


def test_check_av_tolerances() -> None:
    """±1 frame, plus at most one AAC frame of encoder padding at the tail (at 60 fps one frame is only
    800 samples, the AAC tail up to 1023); a short audio stream gets no allowance."""
    tl = Timeline(fps=60, duration=Fraction(600, 60))
    N = tl.sample_count

    def probe(codec: str = "aac") -> dict[str, Any]:
        return {"streams": [{"codec_type": "video", "r_frame_rate": "60/1", "avg_frame_rate": "60/1",
                             "nb_read_packets": str(tl.frame_count), "start_time": "0.000000"},
                            {"codec_type": "audio", "codec_name": codec, "profile": "LC", "sample_rate": "48000",
                             "channels": 2, "start_time": "0.000000"}]}

    assert qm.check_av(tl, probe(), N + 1000).ok is True  # AAC padding past the picture
    assert qm.check_av(tl, probe("pcm_s16le"), N + 1000).ok is False
    assert qm.check_av(tl, probe(), N - 900).ok is False  # audio ends > 1 frame early
    assert qm.check_av(tl, probe(), N + 1900).ok is False
    bad = probe()
    bad["streams"][1]["start_time"] = "0.050000"
    assert any("starts +50.0 ms" in p for p in qm.check_av(tl, bad, N).problems)
    frames = probe()
    frames["streams"][0]["nb_read_packets"] = str(tl.frame_count - 1)
    assert qm.check_av(tl, frames, N).frame_diff == -1


def test_align_tokens_and_normalisation() -> None:
    assert qm.normalize_asr_token("Follow-up,") == ["follow", "up"]
    assert qm.normalize_asr_token("restr-") == ["restr"]
    assert qm.normalize_asr_token("—") == []
    ops = qm.align_tokens(["keep", "the", "pauses", "that", "matter"], ["keep", "pauses", "that", "mother"])
    assert ("delete", 1, None) in ops
    assert ("substitute", 4, 3) in ops
    assert [o for o in ops if o[0] == "equal"] == [("equal", 0, 0), ("equal", 2, 1), ("equal", 3, 2)]
    # one word dropped and its neighbour misheard: the deletion is charged to the missing word
    ops2 = qm.align_tokens(["keep", "the", "pauses"], ["keeps", "pauses"])
    assert ops2 == [("substitute", 0, 0), ("delete", 1, None), ("equal", 2, 1)]
    ops3 = qm.align_tokens(["the", "keep", "pauses"], ["keeps", "pauses"])
    assert ops3 == [("delete", 0, None), ("substitute", 1, 0), ("equal", 2, 1)]
    assert qm.align_tokens([], ["a"]) == [("insert", None, 0)] and qm.align_tokens(["a"], []) == [("delete", 0, None)]


def _scripted_transcriber(timeline: Timeline, index: TakeIndex, *, drop: set[str] = frozenset(),
                          swap: dict[str, str] | None = None) -> Callable[[Path], AsrResult]:
    """An offline ASR that 'hears' the kept words at their output times, minus ``drop``, with ``swap``."""
    swap = swap or {}

    def run(wav: Path) -> AsrResult:
        assert wav.exists() and sf.info(str(wav)).samplerate == SR
        words = []
        for wid, s, e in qm.spoken_word_spans(timeline, index):
            if wid in drop:
                continue
            words.append(Word(id=f"w{len(words) + 1:04d}", text=swap.get(wid, index.word(wid).text),
                              start_us=round(s * 1e6) + 30_000, end_us=round(e * 1e6), kind="word"))
        return AsrResult(words=words, asr=AsrInfo(provider="scripted", model="test"))

    return run


def test_asr_round_trip_offline(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    ix, tl = env.index, env.timeline
    audio = qm.decode_final_audio(clean.r["final"])
    cuts = qm.cut_checks(tl, ix)
    # a perfect hearing: WER 0
    ok = qm.asr_round_trip(tl, ix, audio, transcriber=_scripted_transcriber(tl, ix), cuts=cuts,
                           work_dir=clean.r["dir"] / "qa" / "asr_test")
    assert ok.ran and ok.wer == 0.0 and ok.diffs == [] and ok.seam_damage == []
    assert ok.expected_words == ok.hypothesis_words == 35
    assert ok.timing_offset_ms_median == pytest.approx(30.0, abs=1.0)
    # the word right after seam 1 ("keep") is lost, a mid-segment word is misheard, a pinned CTA word
    # is lost: only the seam word is seam damage; the pinned loss is reported as such
    bad = qm.asr_round_trip(tl, ix, audio, transcriber=_scripted_transcriber(
        tl, ix, drop={"w0020", "w0032"}, swap={"w0003": "cat"}), cuts=cuts, pinned=["w0032", "w0033"],
        work_dir=clean.r["dir"] / "qa" / "asr_test")
    assert bad.seam_damage == ["w0020"]
    assert {d.word_id: d.op for d in bad.diffs} == {"w0020": "deleted", "w0032": "deleted", "w0003": "substituted"}
    assert bad.pinned_missing == ["w0032"]
    assert bad.wer == pytest.approx(3 / 35, abs=1e-3)
    assert bad.deletions == 2 and bad.substitutions == 1


def test_asr_seam_damage_fails_invariant_1(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    pk = qm.measure(env.job, env.timeline, clean.r["final"], doc=env.doc, index=env.index,
                    render_dir=clean.r["dir"], banding=False, estoi=False, save=False,
                    transcriber=_scripted_transcriber(env.timeline, env.index, drop={"w0014"}))
    assert pk.asr.ran and pk.asr.seam_damage == ["w0014"]
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, clean.r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[1].passed and "ASR round-trip lost seam word w0014" in res[1].detail
    assert "w0014" in res[1].refs
    assert res[4].passed  # w0014 is not pinned


def test_a_cutoff_word_at_a_join_fails_invariant_1(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    """A fragment ("restr-") at a seam is a broken word and a jump: invariant 1 fails even with no click."""
    frag = {"word_id": "w0008", "text": "much-", "side": "end", "seg": "seg001"}
    pk = clean.pk.model_copy(update={"integrity": clean.pk.integrity.model_copy(update={"cutoff_at_join": [frag]})})
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, clean.r["dir"], metrics=pk,
                                          asr=False, save=False))
    assert not res[1].passed and "cut-off word at a join" in res[1].detail and "w0008" in res[1].refs
    assert res[1].data["cutoff_at_join"] == [frag]
    assert any("Cut-off word w0008" in a for a in qm._advice(pk, {}, env.doc))


def test_asr_masking_is_not_seam_damage(env: SimpleNamespace, clean: SimpleNamespace, tmp_path: Path) -> None:
    """When the mix carries music/SFX, a seam word lost in the final is re-checked on the dialogue stem:
    heard there → masked (advice, invariant 1 passes); lost there too → seam damage (fails)."""
    rd = tmp_path / "r_music"
    shutil.copytree(clean.r["dir"], rd)
    sf.write(str(rd / "stems" / "music.wav"), np.zeros((env.timeline.sample_count, 2), np.float32), SR)
    calls: list[str] = []

    def transcriber(drop_in_dialogue: bool) -> Callable[[Path], AsrResult]:
        def run(wav: Path) -> AsrResult:
            dialogue = "dialogue" in wav.parts
            calls.append("dialogue" if dialogue else "final")
            drop = {"w0014"} if (drop_in_dialogue or not dialogue) else set()
            return _scripted_transcriber(env.timeline, env.index, drop=drop)(wav)
        return run

    final = rd / "final_tiktok.mp4"
    pk = qm.measure(env.job, env.timeline, final, doc=env.doc, index=env.index, render_dir=rd, banding=False,
                    estoi=False, save=False, transcriber=transcriber(False))
    assert calls == ["final", "dialogue"]
    assert pk.asr.confirm_ran and pk.asr.seam_damage == [] and pk.asr.masked == ["w0014"]
    assert any("masks them" in a for a in pk.advice)
    res = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, rd, metrics=pk, asr=False,
                                          save=False))
    assert res[1].passed and "masked by music/SFX" in res[1].detail
    calls.clear()
    pk2 = qm.measure(env.job, env.timeline, final, doc=env.doc, index=env.index, render_dir=rd, banding=False,
                     estoi=False, save=False, transcriber=transcriber(True))
    assert pk2.asr.seam_damage == ["w0014"] and pk2.asr.masked == []
    res2 = _by_number(inv.check_invariants(env.job, env.doc, env.index, env.timeline, rd, metrics=pk2, asr=False,
                                           save=False))
    assert not res2[1].passed and "also lost in the dialogue alone" in res2[1].detail
    # no seam-damage candidate → the dialogue is not transcribed at all
    calls.clear()
    qm.measure(env.job, env.timeline, final, doc=env.doc, index=env.index, render_dir=rd, banding=False, estoi=False,
               save=False, transcriber=lambda wav: (calls.append("x"), _scripted_transcriber(env.timeline,
                                                                                             env.index)(wav))[1])
    assert calls == ["x"]


# ============================================================================================ invariants 3–6
def test_invariant_3_rejects_model_timestamps(env: SimpleNamespace, tmp_path: Path) -> None:
    job = Job.create("inv3", work_dir=tmp_path)
    job.save_index(env.index)
    job.append_oplog([
        {"by": "director", "version": 2, "applied": True,
         "op": {"op": "cut_words", "from_word": "w0009", "to_word": "w0013", "reason": "false start"}},
        {"by": "director", "version": 3, "applied": True,
         "op": {"op": "cut_words", "from_word": "w0019", "to_word": "w0019", "reason": "filler", "start_us": 812000}},
        {"by": "director", "version": 3, "applied": False, "op": {"op": "set_gap", "at_s": 1.5}},  # rejected: fine
    ])
    problems, n = inv._check_ops(job)
    assert n == 2 and len(problems) == 2 and all("oplog #1" in p for p in problems)
    assert inv.timestamp_keys({"op": "x", "start_us": 5, "items": [{"t": 1.0}], "at": "start", "ms": 300,
                               "counters": {"t_s": 1}}) == ["start_us", "items[0].t"]
    r = _render(env, "clean")
    res = _by_number(inv.check_invariants(job, env.doc, env.index, env.timeline, r["dir"], asr=False, save=False,
                                          **{"banding": False, "estoi": False}))
    assert not res[3].passed and "time-valued fields start_us" in res[3].detail


def test_invariant_3_anchor_check(env: SimpleNamespace) -> None:
    assert inv._anchor_problems(env.timeline, env.index) == []
    segs = list(env.timeline.segments)
    segs[1] = segs[1].model_copy(update={"audio_src_in_us": segs[1].audio_src_in_us - 1_500_000})
    tl = env.timeline.model_copy(update={"segments": segs})
    probs = inv._anchor_problems(tl, env.index)
    assert len(probs) == 1 and probs[0].startswith("seg002: audio in")


def test_invariant_4_pins(env: SimpleNamespace) -> None:
    r = _render(env, "clean")
    doc = env.doc.model_copy(update={"pins": Pins(payoff_word_ids=["w0018"], cta_word_ids=["w0032"],
                                                  must_keep_word_ids=["w0011"])})
    res = _by_number(inv.check_invariants(env.job, doc, env.index, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert not res[4].passed and res[4].refs == ["w0011"]
    assert "must_keep word w0011 is not in the story" in res[4].detail
    assert res[4].data["pinned"] == ["w0018", "w0032", "w0011"]


def test_invariant_5_licences(env: SimpleNamespace, tmp_path: Path) -> None:
    job = Job.create("inv5", work_dir=tmp_path)
    r = _render(env, "clean")
    no_lic = Insert(id="i002", anchor_from_word="w0029", anchor_to_word="w0031", mode="full",
                    asset=AssetRef(kind="video", source="pexels", source_id="123"), job="show it")
    doc = env.doc.model_copy(update={"inserts": [*env.doc.inserts, no_lic]})
    res = _by_number(inv.check_invariants(job, doc, env.index, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert not res[5].passed and res[5].refs == ["i002"] and "no licence record" in res[5].detail
    # registered with a commercial licence whose record file exists → pass
    rec = job.assets_dir / "broll" / "px_123.licence.json"
    rec.write_text("{}")
    asset = job.register_asset(AssetRef(kind="video", source="pexels", source_id="123", licence=Licence(
        name="Pexels License", source="pexels", record_path="assets/broll/px_123.licence.json")))
    ok_ins = no_lic.model_copy(update={"asset": asset})
    doc2 = env.doc.model_copy(update={"inserts": [*env.doc.inserts, ok_ins]})
    res2 = _by_number(inv.check_invariants(job, doc2, env.index, env.timeline, r["dir"], asr=False, save=False,
                                           banding=False, estoi=False))
    assert res2[5].passed, res2[5].detail
    # a non-commercial licence fails
    nc = ok_ins.model_copy(update={"asset": asset.model_copy(update={"id": None, "licence": Licence(
        name="CC BY-NC", commercial_use=False)})})
    doc3 = env.doc.model_copy(update={"inserts": [*env.doc.inserts, nc]})
    res3 = _by_number(inv.check_invariants(job, doc3, env.index, env.timeline, r["dir"], asr=False, save=False,
                                           banding=False, estoi=False))
    assert not res3[5].passed and "does not allow commercial use" in res3[5].detail


def test_invariant_6_hdr_once(env: SimpleNamespace) -> None:
    r = _render(env, "clean")
    m = env.index.media
    hdr_media = m.model_copy(update={"notes": [], "color": m.color.model_copy(
        update={"hdr": True, "hdr_format": "hlg", "transfer": "arib-std-b67", "primaries": "bt2020"})})
    ix = env.index.model_copy(update={"media": hdr_media})
    res = _by_number(inv.check_invariants(None, env.doc, ix, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert not res[6].passed and "0 tone-map records" in res[6].detail
    once = ix.model_copy(update={"media": hdr_media.model_copy(update={"notes": ["flag:hdr_tonemapped zscale"]})})
    res = _by_number(inv.check_invariants(None, env.doc, once, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert res[6].passed, res[6].detail
    twice = ix.model_copy(update={"media": hdr_media.model_copy(update={"notes": ["flag:hdr_tonemapped a",
                                                                                   "flag:hdr_tonemapped b"]})})
    res = _by_number(inv.check_invariants(None, env.doc, twice, env.timeline, r["dir"], asr=False, save=False,
                                          banding=False, estoi=False))
    assert not res[6].passed


def test_no_render_fails_file_gates(env: SimpleNamespace, tmp_path: Path) -> None:
    empty = tmp_path / "r_empty"
    empty.mkdir()
    res = _by_number(inv.check_invariants(None, env.doc, env.index, env.timeline, empty, asr=False, save=False))
    assert _failed(list(res.values())) == {1, 2, 7, 8, 9, 10}
    assert res[1].detail == "no final_*.mp4 in the render dir"


def test_evaluate_render_from_job_files(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    run = inv.evaluate_render(env.job, clean.r["dir"], asr=False, banding=False, estoi=False)
    assert run.passed and run.doc_version == env.doc.version
    assert run.primary == clean.r["final"] and run.metrics is not None
    assert set(run.packets) == {"final_tiktok.mp4"}


def test_secondary_deliverables_are_measured_light(env: SimpleNamespace, clean: SimpleNamespace,
                                                    tmp_path: Path) -> None:
    rd = tmp_path / "r_multi"
    shutil.copytree(clean.r["dir"], rd)
    shutil.copy(rd / "final_tiktok.mp4", rd / "final_reels.mp4")
    reels = env.doc.deliverables[0].model_copy(update={"platform": "reels"})
    doc = env.doc.model_copy(update={"deliverables": [*env.doc.deliverables, reels]})
    packets: dict[str, qm.MetricsPacket] = {}
    res = inv.check_invariants(env.job, doc, env.index, env.timeline, rd, asr=False, save=False, packets_out=packets,
                               banding=False, estoi=False)
    assert inv.all_passed(res), inv.summarize(res)
    assert set(packets) == {"final_tiktok.mp4", "final_reels.mp4"}
    assert packets["final_reels.mp4"].light and packets["final_reels.mp4"].platform == "reels"
    assert packets["final_reels.mp4"].loudness is not None and packets["final_reels.mp4"].clicks


# ============================================================================================ report
def test_report_contents_and_format(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    rd = clean.r["dir"]
    (env.job.critique_dir / "round1").mkdir(parents=True, exist_ok=True)
    (env.job.critique_dir / "round1" / "notes.json").write_text(json.dumps({"round": 1, "notes": [
        {"severity": "P1", "by": "frame_judge", "refs": ["w0014"], "text": "Seam at the retake reads as a jump."}]}))
    (env.job.critique_dir / "round1" / "pairwise.json").write_text(json.dumps(
        {"winner": "B", "votes": [{"judge": "a", "winner": "B"}, {"judge": "b", "winner": "B"}], "reason": "tighter"}))
    env.job.trace("model_call", role="director", provider="anthropic", model="m", input_tokens=10, output_tokens=5,
                  latency_ms=1200)
    path = qr.write_report(env.job, render_dir=rd, doc=env.doc, index=env.index, timeline=env.timeline,
                           metrics=clean.pk, invariants=clean.results)
    assert path == env.job.report_path and path.exists()
    text = path.read_text(encoding="utf-8")
    for h in ("# Edit report", "## Contact sheet", "## Brief", "## Story", "### The cut, in output order",
              "### Source transcript", "## Inserts", "## Captions and on-screen text", "## Music, SFX and voice",
              "## Colour", "## QA", "### Invariants", "## Critique history and decisions", "### Model calls"):
        assert h in text, h
    assert "**all ten invariants pass**" in text
    # story: kept/removed lines with reasons, struck-through removed words, retake cluster named
    assert "**CUT** `s002`" in text and "~~restr-~~" in text and "false start (c01 retake chosen)" in text
    assert "**PART** `s004`" in text and "~~{Um}~~" in text and "_filler_" in text
    assert "c01 take 2/2 (recommended)" in text
    # inserts / captions / sfx
    assert "| i001 | card |" in text and "card/quote: Keep the pauses that matter" in text
    assert "| p001 |" in text and "| t001 | hook_title |" in text and "| fx001 | pop |" in text
    # critique history
    assert "Seam at the retake reads as a jump." in text and "winner **B**" in text
    # contact sheet embedded by a relative path that exists
    img = f"renders/{rd.name}/contact_sheet.jpg"
    assert f"]({img})" in text and (env.job.root / img).exists()
    assert not any(line.startswith("![") and "](/" in line for line in text.splitlines())
    # every markdown table is rectangular
    rows: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            rows.append(line)
            continue
        if rows:
            widths = {len([c for c in _split_row(r)]) for r in rows}
            assert len(widths) == 1, rows[:3]
            assert set(rows[1].replace("|", "")) <= {"-"}
            rows = []
    # the invariants table lists the ten
    inv_rows = [ln for ln in text.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()]
    assert [int(ln.split("|")[1]) for ln in inv_rows] == list(range(1, 11))


def _split_row(row: str) -> list[str]:
    out, cur, esc = [], "", False
    for ch in row.strip()[1:-1]:
        if ch == "\\" and not esc:
            esc = True
            cur += ch
            continue
        if ch == "|" and not esc:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        esc = False
    out.append(cur)
    return out


def test_contact_sheet_every_two_seconds(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    from PIL import Image

    out = qr.make_contact_sheet(env.job, clean.r["final"], clean.r["dir"] / "sheet_test.jpg", timeline=env.timeline,
                                index=env.index, every_s=2.0, columns=4, thumb_w=120)
    im = Image.open(out)
    assert im.format == "JPEG"
    n = int(float(env.timeline.duration) // 2) + 1  # 0, 2, 4 … 12 s → 7 frames
    rows = -(-n // 4)
    assert im.width >= 4 * 120 and im.height > rows * 120 * 16 / 9  # tiles are portrait
    label = qr._label_at(env.timeline, env.index, Fraction(1))
    assert "seg001" in label and ("w000" in label or "pause" in label)


def test_report_loads_everything_from_the_job(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    """With only the job (latest render, saved metrics/invariants JSON), the report still assembles."""
    text = qr.render_report(env.job, render_dir=clean.r["dir"], contact_sheet=False, measure_missing=False)
    assert "## QA" in text and "Integrated loudness" in text and "| 10 |" in text
    assert "_No brief recorded._" in text
    assert "planned by the Director" in text


def test_report_auto_captions_and_registry_licences(env: SimpleNamespace, clean: SimpleNamespace) -> None:
    """Captions are on by default (``doc.captions is None``): the report lists the compiler's pages, not
    "off"; an SFX resolved at render time shows the licence of its registered asset."""
    rd = clean.r["dir"]
    doc = env.doc.model_copy(update={"captions": None})
    rec = env.job.assets_dir / "sfx" / "pop1.licence.json"
    rec.write_text("{}")
    env.job.register_asset(AssetRef(kind="audio", source="procedural", source_id="pop1", licence=Licence(
        name="Self-generated", source="procedural", record_path="assets/sfx/pop1.licence.json")), asset_id="pop1",
        overwrite=True)
    (rd / "audio_report.json").write_text(json.dumps({"sfx": [{"sfx_id": "fx001", "asset_id": "pop1", "gain_db": -9}]}))
    try:
        text = qr.render_report(env.job, render_dir=rd, doc=doc, index=env.index, timeline=env.timeline,
                                metrics=clean.pk, invariants=clean.results, contact_sheet=False)
    finally:
        (rd / "audio_report.json").unlink()
    assert "_Captions off._" not in text and "paged automatically" in text
    assert f"{len(env.timeline.captions)} on screen" in text
    assert "| fx001 | pop |" in text and "Self-generated" in text




def test_story_lines(env: SimpleNamespace) -> None:
    lines = qr.story_lines(env.doc, env.index)
    assert [ln["status"] for ln in lines] == ["kept", "removed", "kept", "partial", "kept"]
    assert lines[1]["reasons"] == ["false start (c01 retake chosen)"] and "incomplete" in lines[1]["tag"]


# ============================================================================================ real ASR round trip
D030 = Path("/Users/home/studio-testdata/qa-editor-d030-h264.mov")
SCRIBE_D030 = Path(__file__).parent / "transcribe_scribe_v2_d030.json"


@pytest.mark.real
@pytest.mark.skipif(not D030.exists() or not SCRIBE_D030.exists(), reason="d030 test take not available")
def test_real_asr_round_trip_scribe(tmp_path: Path) -> None:
    """Cut a real take (Scribe words from the cached response, measured gaps/sentences), render it with
    10 ms crossfades, blank the audio of one seam word, and transcribe the render with Scribe v2: the
    intact story comes back at a low WER and the blanked word is reported as seam damage."""
    from studio.config import get_settings
    from studio.doc.model import CutDocument, Deliverable, Segment
    from studio.media.probe import probe
    from studio.perception.gaps import analyze_gaps
    from studio.perception.takes import analyze_takes
    from studio.perception.transcribe import parse_elevenlabs

    settings = get_settings()
    if not settings.has_key("elevenlabs"):
        pytest.skip("no ElevenLabs key configured")
    job = Job.create("real-asr", work_dir=tmp_path)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(D030), "-map", "0:a:0", "-ac", "1",
                    "-ar", str(SR), "-c:a", "pcm_f32le", str(job.audio_path)], check=True)
    media = probe(D030)
    words, _meta = parse_elevenlabs(json.loads(SCRIBE_D030.read_text()), duration_us=media.duration_us)
    ga = analyze_gaps(job.audio_path, words)
    words2, sentences, clusters = analyze_takes(ga.words)
    ix = TakeIndex(media=media, words=words2, sentences=sentences, clusters=clusters, gaps=ga.gaps,
                   transcript_text=" ".join(s.text for s in sentences))
    job.save_index(ix)
    lex = [s for s in ix.sentences if len(s.word_ids) >= 4 and s.complete]
    assert len(lex) >= 5
    keep = [lex[0], lex[1], lex[3], lex[4]]  # lex[2] (and whatever lay between the kept lines) is removed
    segs = [Segment(id=f"seg{k:03d}", from_word=s.word_ids[0], to_word=s.word_ids[-1]) for k, s in
            enumerate(keep, start=1)]
    doc = CutDocument(version=1, job_id=job.id, segments=segs, deliverables=[Deliverable(platform="tiktok")])
    job.save_doc(doc)
    tl = compile_timeline(doc, ix, job=job)
    seams = qm.seam_pairs(tl)
    assert seams
    src, _ = sf.read(str(job.audio_path), dtype="float64")
    dia = assemble_from_timeline(tl, src)
    # blank the first word after the last seam (a distinctive clipped phoneme: the whole word is gone)
    _k, _left, right = seams[-1]
    victim = right.word_ids[0]
    span = tl.word_map[victim]
    a, b = sample_index(Fraction(span.out_start), SR), sample_index(Fraction(span.out_end), SR)
    tone = _pink(b - a, np.random.default_rng(5)) * 10 ** (-60 / 20)
    dia[a:b] = tone
    rd = job.new_render_dir()
    mixed = master(to_stereo(dia))
    tl.save(rd / "timeline.json")
    final = encode_final(rd / "final_tiktok.mp4", mixed, tl)
    pk = qm.measure(job, tl, final, doc=doc, index=ix, render_dir=rd, asr=True, banding=False, estoi=False,
                    settings=settings)
    a_ = pk.asr
    assert a_ is not None and a_.ran, pk.errors
    assert a_.provider in ("elevenlabs", "assemblyai")
    missing = {d.word_id for d in a_.diffs if d.op == "deleted"}
    assert victim in missing
    if ix.word(victim).kind == "word" and ix.word(victim).confidence >= 0.6:
        assert victim in a_.seam_damage
    assert a_.wer is not None and a_.wer < 0.12, (a_.wer, a_.diffs)
    assert set(a_.seam_damage) <= {victim} | {w for c in pk.cuts for w in (c.left_word, c.right_word) if w}
    assert a_.timing_offset_ms_p90_abs is not None and a_.timing_offset_ms_p90_abs < 250


def test_env_isolation_has_no_keys() -> None:
    """Keyless tests never see the developer's env file."""
    assert os.environ.get("STUDIO_ENV_FILE") is None
