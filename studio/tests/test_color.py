"""The colour measurement as a free function (``studio.compile.color``), keyless.

The frames come from solid-colour clips made with ffmpeg's ``color`` source, so every statistic has a known
answer: a warm mid-tone gives a positive a*/b* face, white is 100 % clipped, black 100 % crushed, grey Y' 128.
The fixture Take Index carries a face track, so the face box is measured on every frame.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from studio.compile import color as C
from studio.doc.model import CutDocument
from studio.jobs import Job
from studio.perception.index import TakeIndex

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(scope="module")
def solid_clip(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    """A 17 s 360x640 clip of one colour (``white``, ``black``, ``0x8a6f5a`` ...), made once per colour."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not available")
    root = tmp_path_factory.mktemp("color_media")
    made: dict[str, Path] = {}

    def make(colour: str) -> Path:
        if colour not in made:
            out = root / f"{colour.replace('#', '')}.mp4"
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c={colour}:size=360x640:rate=30:duration=17", "-c:v", "libx264", "-preset",
                            "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", str(out)], check=True)
            made[colour] = out
        return made[colour]

    return make


@pytest.fixture
def mezz_job(job: Job, solid_clip: Callable[[str], Path]) -> Callable[[str], Job]:
    def with_colour(colour: str) -> Job:
        shutil.copy(solid_clip(colour), job.mezz_path)
        return job

    return with_colour


# ---------------------------------------------------------------------------------------------- measurement
def test_measures_frame_and_face_on_a_warm_mezzanine(mezz_job: Callable[[str], Job], take_index: TakeIndex,
                                                     cut_doc: CutDocument) -> None:
    job = mezz_job("0x8a6f5a")  # a warm mid-tone, decodes to RGB (140, 112, 89)
    m = C.measure_color(job, take_index, cut_doc, ["w0001", "w0030:payoff", " w0041 "])
    assert m.source == job.mezz_path
    assert [f.word_id for f in m.frames] == ["w0001", "w0030", "w0041"]  # suffix and whitespace stripped
    w = take_index.word("w0030")
    assert m.frames[1].t_us == (w.start_us + w.end_us) // 2
    for f in m.frames:
        assert f.y_mean == pytest.approx(116.3, abs=1.5)
        assert f.clipped_pct == 0.0 and f.crushed_pct == 0.0
        assert f.face is not None and f.face.y_mean == pytest.approx(f.y_mean, abs=1.0)
        assert f.face.a > 5 and f.face.b > 12  # warm: + a* and clearly + b*


def test_white_is_clipped_and_black_is_crushed(mezz_job: Callable[[str], Job], take_index: TakeIndex,
                                               cut_doc: CutDocument) -> None:
    [white] = C.measure_color(mezz_job("white"), take_index, cut_doc, ["w0010"]).frames
    assert white.y_mean == pytest.approx(255.0, abs=0.5) and white.clipped_pct == 100.0 and white.crushed_pct == 0.0
    assert white.face is not None and white.face.y_mean == pytest.approx(255.0, abs=0.5)
    [black] = C.measure_color(mezz_job("black"), take_index, cut_doc, ["w0010"]).frames
    assert black.y_mean == pytest.approx(0.0, abs=1.5) and black.crushed_pct == 100.0 and black.clipped_pct == 0.0


def test_grey_reads_neutral(mezz_job: Callable[[str], Job], take_index: TakeIndex) -> None:
    [f] = C.measure_color(mezz_job("0x808080"), take_index, None, ["w0020"]).frames
    assert f.y_mean == pytest.approx(128.0, abs=0.5)
    assert f.face is not None and abs(f.face.a) < 1.0 and abs(f.face.b) < 1.0


def test_no_face_when_the_index_has_none(mezz_job: Callable[[str], Job], take_index: TakeIndex) -> None:
    ix = take_index.model_copy(update={"visual": take_index.visual.model_copy(update={"face_track": None,
                                                                                       "samples": []})})
    [f] = C.measure_color(mezz_job("0x808080"), ix, None, ["w0020"]).frames
    assert f.face is None
    assert "w0020: frame Y' mean 128; no face; clipped 0.0%, crushed 0.0%" in C.format_color_measurement(
        C.ColorMeasurement(source=Path("x"), frames=[f]))


def test_items_are_capped_at_max_items(mezz_job: Callable[[str], Job], take_index: TakeIndex) -> None:
    ids = [w.id for w in take_index.words][: C.MAX_ITEMS + 5]
    m = C.measure_color(mezz_job("0x808080"), take_index, None, ids)
    assert [f.word_id for f in m.frames] == ids[: C.MAX_ITEMS]


# ---------------------------------------------------------------------------------------------- defaults
def test_default_items_spread_over_the_kept_story(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    kept = cut_doc.kept_word_ids(take_index)
    got = C.default_items(cut_doc, take_index)
    assert len(got) == C.DEFAULT_SAMPLES and got[0] == kept[0] and got[-1] == kept[-1]
    assert [kept.index(w) for w in got] == sorted(kept.index(w) for w in got)  # output order, no repeats
    assert all(take_index.word(w).is_spoken for w in got)
    # fewer than asked: everything, in order
    assert C.default_items(cut_doc, take_index, n=100) == kept
    # one: the middle of the story; none: nothing
    assert C.default_items(cut_doc, take_index, n=1) == [kept[len(kept) // 2]]
    assert C.default_items(cut_doc, take_index, n=0) == []


def test_default_items_fall_back_to_the_whole_take(take_index: TakeIndex) -> None:
    for doc in (None, CutDocument()):
        got = C.default_items(doc, take_index, n=4)
        assert got[0] == take_index.words[0].id and got[-1] == take_index.words[-1].id and len(got) == 4


def test_measure_color_without_items_uses_the_default_sample(mezz_job: Callable[[str], Job],
                                                             take_index: TakeIndex, cut_doc: CutDocument) -> None:
    m = C.measure_color(mezz_job("0x808080"), take_index, cut_doc)
    assert [f.word_id for f in m.frames] == C.default_items(cut_doc, take_index)
    assert C.measure_color(mezz_job("0x808080"), take_index, cut_doc, []).frames == m.frames


# ---------------------------------------------------------------------------------------------- errors
def test_missing_mezzanine_and_unknown_words_raise(job: Job, mezz_job: Callable[[str], Job],
                                                   take_index: TakeIndex) -> None:
    with pytest.raises(FileNotFoundError):
        C.measure_color(job, take_index, None, ["w0001"])
    with pytest.raises(KeyError):
        C.measure_color(mezz_job("0x808080"), take_index, None, ["w9999"])


def test_a_take_without_words_has_nothing_to_measure(mezz_job: Callable[[str], Job], take_index: TakeIndex) -> None:
    empty = take_index.model_copy(update={"words": [], "sentences": [], "clusters": [], "gaps": [],
                                          "transcript_text": ""})
    with pytest.raises(ValueError, match="no words"):
        C.measure_color(mezz_job("0x808080"), empty, None)


# ---------------------------------------------------------------------------------------------- pure helpers
def test_rgb_to_lab_reference_points() -> None:
    lab = C.rgb_to_lab(np.array([[1.0, 1.0, 1.0], [0.5, 0.5, 0.5], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]))
    assert lab[0] == pytest.approx([100.0, 0.0, 0.0], abs=0.5)      # white: L* 100, neutral
    assert lab[1, 0] == pytest.approx(53.4, abs=0.5) and abs(lab[1, 1]) < 0.5  # 50 % grey
    assert lab[2, 1] > 50 and lab[2, 2] > 50                            # red: + a*, + b*
    assert lab[3, 2] < -80                                              # blue: strongly - b*


def _m(*frames: C.FrameColor) -> C.ColorMeasurement:
    return C.ColorMeasurement(source=Path("mezz.mov"), frames=list(frames))


def test_format_matches_the_director_tool_text() -> None:
    m = _m(C.FrameColor("w0001", 500_000, 118.4, 0.04, 0.0, C.FaceColor(161.6, 8.06, 12.94)),
           C.FrameColor("w0030", 9_000_000, 131.0, 0.0, 2.25, None))
    assert C.format_color_measurement(m).splitlines() == [
        "Measured on the mezzanine (BT.709 Y' 0-255; a*/b* CIELAB of the face: + b* warmer/yellow, + a* magenta; "
        "clipped = Y' ≥ 250, crushed = Y' ≤ 6):",
        "  w0001: frame Y' mean 118; face Y' 162, a* +8.1, b* +12.9; clipped 0.0%, crushed 0.0%",
        "  w0030: frame Y' mean 131; no face; clipped 0.0%, crushed 2.2%",
        "Skin usually reads natural with face Y' ≈ 150-190 (SDR) and a small positive a*/b*; judge differences "
        "between takes, not absolute numbers.",
    ]


def test_summary_line_gives_ranges() -> None:
    m = _m(C.FrameColor("w0001", 0, 118.4, 0.04, 0.0, C.FaceColor(161.6, 8.06, 12.94)),
           C.FrameColor("w0030", 0, 131.0, 0.0, 2.25, C.FaceColor(170.2, 9.0, 14.1)),
           C.FrameColor("w0041", 0, 125.0, 0.0, 0.3, None))
    assert m.summary() == ("Colour (3 frames w0001..w0041): frame Y' 118..131; face Y' 162..170 a* +8.1..+9.0 "
                           "b* +12.9..+14.1 (2/3 frames); clipped ≤0.0%, crushed ≤2.2%")
    one = _m(C.FrameColor("w0007", 0, 128.0, 0.0, 0.0, None))
    assert one.summary() == "Colour (1 frame w0007): frame Y' 128; no face; clipped ≤0.0%, crushed ≤0.0%"
    assert _m().summary() == "Colour: nothing measured"
