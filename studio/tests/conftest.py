"""Shared pytest fixtures for Yunicorn Studio (keyless, offline).

Media (session-scoped, synthesized with ffmpeg on first use; skip if ffmpeg is missing):

* ``synth_video``    6 s 1080x1920 30 fps H.264 yuv420p + AAC 48 kHz mono (220 Hz sine + pink noise)
* ``synth_rotated``  3 s coded 1920x1080 with a 90° display matrix (portrait on display), .mov
* ``synth_hlg``      3 s 1080x1920 HEVC 10-bit yuv420p10le tagged BT.2020 / HLG (arib-std-b67), .mov
* ``synth_vfr``      3 s variable frame rate (irregular 1/60–2/60 s frame durations), .mov
* ``synth_noaudio``  3 s 1080x1920 30 fps H.264, no audio stream
* ``synth_media``    dict of all of the above by name

Documents (function-scoped, fresh objects each test):

* ``media_info``  a :class:`MediaInfo` matching the hand-built index (1080x1920, 30 fps, ~17 s)
* ``take_index``  41 words / 5 sentences: s001 hook; s002 false start ending in a cut-off ``restr-``
  (c01 take 1, incomplete); s003 retake (c01 take 2, recommended); s004 opens with the filler ``Um``;
  s005 CTA. 13 gaps incl. leading/trailing, a 900 ms silence and a breath. Visual samples at 10 fps,
  a smoothed face track and blink/look-away events.
* ``cut_doc``     version-1 CutDocument over ``take_index``: 4 segments (false start and filler removed),
  payoff/CTA pins, caption pages, a hook title, a card insert and one SFX cue.
* ``work_dir`` / ``job``  an isolated work dir and a created Job (with media_info + index saved).
* ``settings``    Settings loaded from an empty env with ``work_dir`` = the temp work dir.

Helper builders are also exposed as fixtures (``make_take_index``, ``make_cut_doc``) so other tests can
build variants without importing this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from studio.config import KEY_ENV_NAMES, Settings, reset_settings
from studio.doc.model import (
    AudioPlan,
    CaptionPage,
    CaptionPlan,
    CardSpec,
    CutDocument,
    Deliverable,
    Insert,
    Pins,
    RemovedRange,
    Segment,
    SfxCue,
    TextOverlay,
)
from studio.jobs import Job
from studio.media.models import AudioInfo, ColorInfo, MediaInfo
from studio.perception.index import (
    AsrInfo,
    AudioMetrics,
    Cluster,
    Energy,
    FaceBox,
    FaceTrack,
    FaceTrackPoint,
    Gap,
    Prosody,
    Sentence,
    TakeIndex,
    Visual,
    VisualEvent,
    VisualSample,
    Word,
    gap_id,
    word_id,
)

FFMPEG = shutil.which("ffmpeg")


# ============================================================================================ isolation
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``@pytest.mark.real`` tests only run with ``STUDIO_REAL=1`` (and ``-m real``)."""
    if os.environ.get("STUDIO_REAL") == "1":
        return
    skip = pytest.mark.skip(reason="real API test: set STUDIO_REAL=1 and STUDIO_ENV_FILE")
    for item in items:
        if item.get_closest_marker("real"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_settings(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                      tmp_path_factory: pytest.TempPathFactory):
    """Never let keyless tests read the developer's env file or write to the real work dir. Tests marked
    ``real`` keep ``STUDIO_ENV_FILE`` so :func:`studio.config.get_settings` can load keys."""
    if request.node.get_closest_marker("real") is None:
        monkeypatch.delenv("STUDIO_ENV_FILE", raising=False)
        for names in KEY_ENV_NAMES.values():
            for n in names:
                monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("STUDIO_WORK_DIR", str(tmp_path_factory.mktemp("work")))
    # machine-wide edit/render slots of tests never contend with (or wait on) a real edit running on this machine
    monkeypatch.setenv("STUDIO_SLOTS_DIR", str(tmp_path_factory.mktemp("slots")))
    # the Director's caption_preview draws an approximation instead of launching the overlay renderer per still
    monkeypatch.setenv("STUDIO_PREVIEW_STILLS", "approx")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "studio-work"
    d.mkdir()
    return d


@pytest.fixture
def settings(work_dir: Path) -> Settings:
    return Settings.load(env={"STUDIO_WORK_DIR": str(work_dir)})


# ============================================================================================ media
def _run_ffmpeg(args: list[str]) -> None:
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _audio_inputs(duration: float, freq: int) -> list[str]:
    return ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration}",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:sample_rate=48000:duration={duration}"]


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("synth_media")


@pytest.fixture(scope="session")
def synth_video(media_dir: Path) -> Path:
    out = media_dir / "portrait_6s_30fps.mp4"
    if not out.exists():
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=6", *_audio_inputs(6, 220),
                     "-filter_complex",
                     "[0:v]setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv[v];"
                     "[1:a][2:a]amix=inputs=2:normalize=0[a]",
                     "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "1", "-shortest", str(out)])
    return out


@pytest.fixture(scope="session")
def synth_rotated(media_dir: Path) -> Path:
    out = media_dir / "rotated_3s.mov"
    if not out.exists():
        land = media_dir / "_landscape_3s.mp4"
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3", *_audio_inputs(3, 330),
                     "-filter_complex", "[1:a][2:a]amix=inputs=2:normalize=0[a]", "-map", "0:v", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-ac", "1", "-shortest", str(land)])
        _run_ffmpeg(["-display_rotation:v:0", "90", "-i", str(land), "-c", "copy", str(out)])
    return out


@pytest.fixture(scope="session")
def synth_hlg(media_dir: Path) -> Path:
    out = media_dir / "hlg_10bit_3s.mov"
    if not out.exists():
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3", *_audio_inputs(3, 440),
                     "-filter_complex", "[0:v]format=yuv420p10le[v];[1:a][2:a]amix=inputs=2:normalize=0[a]",
                     "-map", "[v]", "-map", "[a]", "-c:v", "libx265", "-preset", "ultrafast",
                     "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
                                     ":range=limited",
                     "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
                     "-color_range", "tv", "-tag:v", "hvc1", "-c:a", "aac", "-ac", "1", "-shortest", str(out)])
    return out


@pytest.fixture(scope="session")
def synth_vfr(media_dir: Path) -> Path:
    out = media_dir / "vfr_3s.mov"
    if not out.exists():
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=60:duration=3", *_audio_inputs(3, 550),
                     "-filter_complex",
                     "[0:v]select='not(mod(n\\,2))+eq(mod(n\\,7)\\,3)'[v];[1:a][2:a]amix=inputs=2:normalize=0[a]",
                     "-map", "[v]", "-map", "[a]", "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-shortest", str(out)])
    return out


@pytest.fixture(scope="session")
def synth_noaudio(media_dir: Path) -> Path:
    out = media_dir / "noaudio_3s.mp4"
    if not out.exists():
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3", "-an",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", str(out)])
    return out


@pytest.fixture(scope="session")
def synth_media(synth_video: Path, synth_rotated: Path, synth_hlg: Path, synth_vfr: Path,
                synth_noaudio: Path) -> dict[str, Path]:
    return {"video": synth_video, "rotated": synth_rotated, "hlg": synth_hlg, "vfr": synth_vfr,
            "noaudio": synth_noaudio}


# ============================================================================================ take index
# (text, kind, gap_after_ms, gap_kind) — gap_after_ms 0 means words abut (40 ms, no gap object)
_SCRIPT: list[list[tuple[str, str, int, str]]] = [
    # s001 — hook
    [("Most", "word", 0, ""), ("people", "word", 0, ""), ("cut", "word", 0, ""), ("their", "word", 0, ""),
     ("videos", "word", 180, "pause"), ("way", "word", 0, ""), ("too", "word", 0, ""),
     ("much.", "word", 700, "pause")],
    # s002 — false start (c01 take 1, incomplete), ends in a cut-off
    [("The", "word", 0, ""), ("real", "word", 0, ""), ("secret", "word", 0, ""), ("is", "word", 0, ""),
     ("restr-", "cutoff", 900, "silence")],
    # s003 — the retake (c01 take 2, recommended; payoff "restraint.")
    [("The", "word", 0, ""), ("real", "word", 0, ""), ("secret", "word", 0, ""), ("is", "word", 120, "pause"),
     ("restraint.", "word", 800, "breath")],
    # s004 — opens with a filler
    [("Um,", "filler", 250, "pause"), ("keep", "word", 0, ""), ("the", "word", 0, ""),
     ("pauses", "word", 150, "pause"), ("that", "word", 0, ""), ("matter", "word", 0, ""), ("and", "word", 0, ""),
     ("cut", "word", 0, ""), ("the", "word", 0, ""), ("rest.", "word", 600, "pause")],
    # s005 — CTA ("follow me" pinned)
    [("If", "word", 0, ""), ("this", "word", 0, ""), ("helped,", "word", 300, "pause"), ("follow", "word", 0, ""),
     ("me", "word", 0, ""), ("for", "word", 0, ""), ("more", "word", 0, ""), ("simple", "word", 0, ""),
     ("editing", "word", 0, ""), ("tips", "word", 0, ""), ("every", "word", 0, ""), ("single", "word", 0, ""),
     ("week.", "word", 500, "pause")],
]
_EMPHASIS = {"much.": 0.6, "restraint.": 0.95, "matter": 0.7, "follow": 0.55, "Most": 0.4}
LEADING_GAP_MS = 300


def build_take_index() -> TakeIndex:
    """Deterministic hand-built Take Index (see module docstring)."""
    words: list[Word] = []
    gaps: list[Gap] = []
    sentences: list[Sentence] = []
    t = LEADING_GAP_MS * 1000
    n = 0
    pending_gap: tuple[int, str, int] | None = None  # (after word index, kind, ms)
    for si, sent in enumerate(_SCRIPT, start=1):
        sid = f"s{si:03d}"
        cid = "c01" if si in (2, 3) else None
        ids = []
        for text, kind, gap_ms, gap_kind in sent:
            n += 1
            dur = (160 + 22 * len(text.strip(".,-"))) * 1000
            if kind == "filler":
                dur = 320_000
            wid = word_id(n)
            f0 = 1.4 if text in _EMPHASIS else (0.1 * ((n % 5) - 2))
            words.append(Word(
                id=wid, text=text, start_us=t, end_us=t + dur, kind=kind,  # type: ignore[arg-type]
                confidence=0.97 if kind == "word" else 0.8, speaker="S1", sentence_id=sid, cluster_id=cid,
                prosody=Prosody(f0_z=round(f0, 2), int_z=round(f0 * 0.8, 2), dur_z=0.3 if text in _EMPHASIS else 0.0),
                emphasis=_EMPHASIS.get(text, 0.1),
            ))
            ids.append(wid)
            t += dur
            if gap_ms:
                pending_gap = (n, gap_kind, gap_ms)
                t_gap_start = t
                t += gap_ms * 1000
                gaps.append(Gap(
                    id="g0000", after_word_id=wid, before_word_id=None, start_us=t_gap_start, end_us=t,
                    kind=gap_kind, snap_us=t_gap_start + (gap_ms * 1000) * 3 // 5,  # type: ignore[arg-type]
                    energy_db=-62.0 if gap_kind == "silence" else -55.0, has_breath=gap_kind == "breath",
                ))
            else:
                t += 40_000
        sent_words = words[-len(sent):]
        sentences.append(Sentence(
            id=sid, word_ids=ids, text=" ".join(w.text for w in sent_words), start_us=sent_words[0].start_us,
            end_us=sent_words[-1].end_us, complete=si != 2, cluster_id=cid, speaker="S1",
        ))
    del pending_gap
    # fill before_word_id; the last gap is trailing (before None); add the leading gap
    by_id = {w.id: i for i, w in enumerate(words)}
    fixed: list[Gap] = [Gap(id="g0000", after_word_id=None, before_word_id=words[0].id, start_us=0,
                            end_us=LEADING_GAP_MS * 1000, kind="silence", snap_us=100_000, energy_db=-63.0)]
    for g in gaps:
        i = by_id[g.after_word_id]  # type: ignore[index]
        nxt = words[i + 1].id if i + 1 < len(words) else None
        fixed.append(g.model_copy(update={"before_word_id": nxt}))
    fixed = [g.model_copy(update={"id": gap_id(k)}) for k, g in enumerate(fixed, start=1)]
    duration_us = fixed[-1].end_us

    fps = Fraction(30)
    media = MediaInfo(
        path="/fixtures/take.mov", container="mov,mp4,m4a,3gp,3g2,mj2", width=1080, height=1920,
        coded_width=1920, coded_height=1080, rotation=90, display_aspect=Fraction(9, 16), fps=fps,
        r_fps=fps, avg_fps=fps, vfr=False, duration_us=duration_us, nb_frames=None, video_codec="hevc",
        color=ColorInfo(primaries="bt709", transfer="bt709", matrix="bt709", range="tv", pix_fmt="yuv420p",
                        bit_depth=8),
        audio=AudioInfo(stream_index=1, codec="aac", sample_rate=48000, channels=1, channel_layout="mono",
                        duration_us=duration_us),
    )

    samples = []
    track = []
    for k in range(0, duration_us // 100_000 + 1):
        tt = k * 100_000
        cx = 0.5 + 0.01 * ((k % 20) - 10) / 10
        samples.append(VisualSample(t_us=tt, face_box=FaceBox.from_center(cx, 0.32, 0.34, 0.2), face_conf=0.98,
                                    eyes_open=0.9, gaze_off=0.05, mouth_open=0.3, head_yaw=2.0, head_pitch=-3.0,
                                    blur=0.1, luma=0.52))
        if k % 5 == 0:
            track.append(FaceTrackPoint(t_us=tt, cx=round(cx, 4), cy=0.32, w=0.34, h=0.2, conf=0.98))
    s2 = sentences[1]
    visual = Visual(
        sample_fps=10.0, samples=samples,
        events=[VisualEvent(kind="blink", start_us=2_000_000, end_us=2_150_000),
                VisualEvent(kind="look_away", start_us=s2.start_us, end_us=s2.end_us, confidence=0.8,
                            note="glances at notes during the false start")],
        face_track=FaceTrack(points=track, method="one_euro", params={"min_cutoff": 1.0, "beta": 0.01}),
    )
    clusters = [Cluster(id="c01", sentence_ids=["s002", "s003"], similarity=0.92, recommended_sentence_id="s003",
                        notes="false start then clean retake")]
    audio = AudioMetrics(noise_floor_db=-62.0, snr_db=31.0, clipping_ratio=0.0, integrated_lufs=-21.5,
                         true_peak_dbtp=-4.2, lra_lu=5.1, rt60_est=0.35, music_in_room=False,
                         room_tone_ranges_us=[(g.start_us, g.end_us) for g in fixed if g.duration_us >= 500_000])
    return TakeIndex(
        media=media, words=words, sentences=sentences, clusters=clusters, gaps=fixed, visual=visual, audio=audio,
        energy=Energy(wpm=158.0, f0_var=0.42, loudness_var=0.31, overall=0.55),
        transcript_text=" ".join(s.text for s in sentences),
        asr=AsrInfo(provider="fixture", model="hand-built", language="en"),
    )


def _chunk_pages(word_ids: list[str], ix: TakeIndex, size: int = 3) -> list[list[str]]:
    pages: list[list[str]] = []
    cur: list[str] = []
    for w in word_ids:
        cur.append(w)
        if len(cur) >= size or ix.word(w).text.endswith((".", ",")):
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    return pages


def build_cut_doc(ix: TakeIndex) -> CutDocument:
    """Version-1 document: hook, chosen retake, s004 without the filler, CTA."""
    segs = [
        Segment(id="seg001", from_word="w0001", to_word="w0008"),
        Segment(id="seg002", from_word="w0014", to_word="w0018"),
        Segment(id="seg003", from_word="w0020", to_word="w0028"),
        Segment(id="seg004", from_word="w0029", to_word="w0041"),
    ]
    kept: list[str] = []
    for s in segs:
        kept.extend(ix.word_ids(s.from_word, s.to_word))
    pages = []
    for k, ws in enumerate(_chunk_pages(kept, ix), start=1):
        pages.append(CaptionPage(id=f"p{k:03d}", word_ids=ws,
                                 emphasis_word_ids=[w for w in ws if w in ("w0018", "w0024", "w0032")]))
    return CutDocument(
        version=1, parent_version=0, job_id="fixture-job", created_by="fixture",
        pins=Pins(payoff_word_ids=["w0018"], cta_word_ids=["w0032", "w0033"]),
        segments=segs,
        removed=[RemovedRange(from_word="w0009", to_word="w0013", reason="false start (c01 retake chosen)"),
                 RemovedRange(from_word="w0019", to_word="w0019", reason="filler")],
        inserts=[Insert(id="i001", anchor_from_word="w0020", anchor_to_word="w0024", mode="card",
                        asset=CardSpec(template="quote", title="Keep the pauses that matter"),
                        job="make the rule visible as it is said")],
        captions=CaptionPlan(pages=pages),
        texts=[TextOverlay(id="t001", kind="hook_title", text="Stop over-editing",
                           anchor_from_word="w0001", anchor_to_word="w0008", job="hook title")],
        audio=AudioPlan(sfx=[SfxCue(id="fx001", kind="pop", anchor_word="w0018", job="land the payoff")]),
        deliverables=[Deliverable(platform="tiktok")],
        counters={"seg": 4, "i": 1, "t": 1, "fx": 1, "p": len(pages)},
    )


@pytest.fixture
def make_take_index() -> Callable[[], TakeIndex]:
    return build_take_index


@pytest.fixture
def make_cut_doc() -> Callable[[TakeIndex], CutDocument]:
    return build_cut_doc


@pytest.fixture
def take_index() -> TakeIndex:
    return build_take_index()


@pytest.fixture
def media_info(take_index: TakeIndex) -> MediaInfo:
    return take_index.media.model_copy(deep=True)


@pytest.fixture
def cut_doc(take_index: TakeIndex) -> CutDocument:
    return build_cut_doc(take_index)


@pytest.fixture
def job(work_dir: Path, take_index: TakeIndex) -> Job:
    j = Job.create("fixture-job", work_dir=work_dir)
    j.save_media_info(take_index.media)
    j.save_index(take_index)
    return j


def ffprobe_streams(path: Path) -> list[dict[str, Any]]:
    """Small helper for media assertions."""
    import json

    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture
def probe_streams() -> Callable[[Path], list[dict[str, Any]]]:
    return ffprobe_streams
