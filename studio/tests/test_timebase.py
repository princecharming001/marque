from __future__ import annotations

from fractions import Fraction as F

import pytest
from pydantic import BaseModel

from studio import timebase as tb
from studio.timebase import Rational

NTSC30 = F(30000, 1001)
NTSC60 = F(60000, 1001)
NTSC24 = F(24000, 1001)


# ------------------------------------------------------------------ conversions
@pytest.mark.parametrize("value,expected", [
    (F(1, 3), F(1, 3)), (5, F(5)), (0.1, F(1, 10)), (29.97, F(2997, 100)), ("30000/1001", NTSC30),
    ("29.97", F(2997, 100)), (" 1 / 3 ", F(1, 3)), ("12", F(12)), ((30000, 1001), NTSC30),
    ({"num": 3, "den": 4}, F(3, 4)), ("1.5/3", F(1, 2)),
])
def test_to_fraction(value, expected):
    assert tb.to_fraction(value) == expected


@pytest.mark.parametrize("bad", [True, None, "abc", "", "1/0", float("nan"), float("inf"), (1, 0), (1.5, 2), [1]])
def test_to_fraction_rejects(bad):
    with pytest.raises((TypeError, ValueError, ZeroDivisionError)):
        tb.to_fraction(bad)


def test_to_fraction_numpy_scalars():
    np = pytest.importorskip("numpy")
    assert tb.to_fraction(np.int64(7)) == 7
    assert tb.to_fraction(np.float32(0.5)) == F(1, 2)


def test_fraction_str_roundtrip():
    for f in (NTSC30, F(0), F(-7, 3), F(30), F(1, 48000)):
        s = tb.fraction_to_str(f)
        assert "/" in s
        assert tb.fraction_from_str(s) == f
    assert tb.fraction_to_str(30) == "30/1"


def test_round_fraction_half_up_and_modes():
    assert tb.round_fraction(F(1, 2)) == 1
    assert tb.round_fraction(F(3, 2)) == 2  # not banker's rounding
    assert tb.round_fraction(F(5, 2)) == 3
    assert tb.round_fraction(F(-1, 2)) == 0
    assert tb.round_fraction(F(7, 3), "floor") == 2
    assert tb.round_fraction(F(7, 3), "ceil") == 3
    with pytest.raises(ValueError):
        tb.round_fraction(1, "nearest")  # type: ignore[arg-type]


def test_us_seconds():
    assert tb.us_to_seconds(1_500_000) == F(3, 2)
    assert tb.us_to_seconds(1) == F(1, 1_000_000)
    assert tb.seconds_to_us(F(1, 3)) == 333_333
    assert tb.seconds_to_us(F(2, 3)) == 666_667
    assert tb.seconds_to_us("0.0000005") == 1  # half-up
    assert tb.seconds_to_us(1.25) == 1_250_000
    with pytest.raises(TypeError):
        tb.us_to_seconds(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tb.us_to_seconds(True)  # type: ignore[arg-type]


# ------------------------------------------------------------------ fps
@pytest.mark.parametrize("value,expected", [
    (29.97, NTSC30), ("29.97", NTSC30), (29.970029, NTSC30), ("30000/1001", NTSC30), (NTSC30, NTSC30),
    (59.94, NTSC60), ("60000/1001", NTSC60), (23.976, NTSC24), (23.98, NTSC24), (119.88, F(120000, 1001)),
    (30, F(30)), (30.0, F(30)), ("30/1", F(30)), (25, F(25)), (60.0, F(60)), (29.99, F(30)),
    ("10833/361", F(30)),  # ffprobe avg rate of a VFR phone clip
    (47.952, F(48000, 1001)), (12.5, F(25, 2)), ((24000, 1001), NTSC24),
])
def test_normalize_fps(value, expected):
    assert tb.normalize_fps(value) == expected


def test_normalize_fps_keeps_ntsc_distinct_from_integer():
    assert tb.normalize_fps(29.97) != 30
    assert tb.normalize_fps(59.94) != 60
    assert tb.is_ntsc(NTSC30) and not tb.is_ntsc(30) and not tb.is_ntsc(F(2997, 100))


@pytest.mark.parametrize("bad", [0, -30, "0/1"])
def test_normalize_fps_rejects_non_positive(bad):
    with pytest.raises(ValueError):
        tb.normalize_fps(bad)


@pytest.mark.parametrize("value,expected", [
    ("6180/179", F(30)),  # 34.5 fps VFR average → nearest standard
    (31.2, F(30)), (28.0, NTSC30), (27.0, F(25)), (58.0, NTSC60), (61, F(60)), (24.3, F(24)), (100.2, F(100)),
    (NTSC30, NTSC30), (26.0, F(25)),
])
def test_nearest_standard_fps(value, expected):
    assert tb.nearest_standard_fps(value) == expected


# ------------------------------------------------------------------ frames
@pytest.mark.parametrize("fps", [F(30), NTSC30, NTSC60, F(60), NTSC24, F(25), F(24)])
def test_frame_grid_roundtrip(fps):
    for n in (0, 1, 2, 29, 30, 1799, 10_000, 35_964):
        t = tb.frame_time(n, fps)
        assert tb.frame_index(t, fps) == n
        assert tb.snap_to_frame(t, fps) == t
        # any time within ±0.49 frame snaps back to n
        d = tb.frame_duration(fps)
        assert tb.frame_index(t + d * F(49, 100), fps) == n
        assert tb.frame_index(t - d * F(49, 100), fps) == n


def test_snap_modes_and_idempotence():
    fps = NTSC30
    t = F(1, 7)
    r, fl, ce = (tb.snap_to_frame(t, fps, m) for m in ("round", "floor", "ceil"))
    assert fl <= t <= ce and fl <= r <= ce
    assert ce - fl == tb.frame_duration(fps)
    assert tb.snap_to_frame(r, fps) == r
    # exact output time on the grid is a multiple of 1001/30000
    assert (tb.snap_to_frame(F(12345, 1000), fps) * fps).denominator == 1


def test_ntsc_frame_math_is_exact():
    fps = NTSC30
    assert tb.frame_time(30000, fps) == 1001  # 30000 frames = 1001 s exactly
    assert tb.frame_duration(fps) == F(1001, 30000)
    assert tb.frame_count(F(1001), fps) == 30000
    assert tb.frame_count(F(10), 30) == 300
    # float path would drift; rationals do not
    t = sum((tb.frame_duration(fps) for _ in range(30000)), F(0))
    assert t == 1001


def test_us_frame_conversions():
    assert tb.us_to_frame(1_000_000, 30) == 30
    assert tb.us_to_frame(1_016_000, 30) == 30  # 30.48 → 30
    assert tb.us_to_frame(1_017_000, 30) == 31  # 30.51 → 31
    assert tb.us_to_frame(1_017_000, 30, "floor") == 30
    assert tb.frame_to_us(1, NTSC30) == 33_367  # 33366.67 µs
    assert tb.snap_us_to_frame(1_010_000, 30) == 1_000_000
    assert tb.frame_to_us(30, 30) == 1_000_000


# ------------------------------------------------------------------ samples
def test_sample_math_48k():
    assert tb.SAMPLE_RATE == 48_000
    assert tb.sample_index(F(1), 48_000) == 48_000
    assert tb.sample_time(24_000) == F(1, 2)
    assert tb.us_to_sample(1_000_000) == 48_000
    assert tb.us_to_sample(21) == 1  # 1.008 samples
    assert tb.us_to_sample(10) == 0  # 0.48 → 0
    assert tb.sample_to_us(1) == 21  # 20.83 µs
    assert tb.sample_to_us(48_000) == 1_000_000
    assert tb.samples_per_frame(30) == 1600
    assert tb.samples_per_frame(NTSC30) == F(8008, 5)  # 1601.6
    assert tb.samples_per_frame(NTSC60) == F(4004, 5)


def test_frame_and_sample_positions_share_instants():
    """Audio sample positions derive from the same rational frame instants (no drift)."""
    fps = NTSC30
    for n in (0, 1, 5, 299, 30000, 107892):
        t = tb.frame_time(n, fps)
        s = tb.frame_to_sample(n, fps)
        assert s == tb.sample_index(t)
        assert abs(tb.sample_time(s) - t) <= F(1, 2 * 48_000)
    # 5 NTSC frames are exactly 8008 samples; every 5-frame boundary lands on an integer sample
    assert tb.frame_to_sample(5, fps) == 8008
    assert tb.frame_to_sample(30000, fps) == 1001 * 48_000


# ------------------------------------------------------------------ display
def test_format_timecode():
    assert tb.format_timecode(F(0), 30) == "00:00:00:00"
    assert tb.format_timecode(F(61) + F(15, 30), 30) == "00:01:01:15"
    assert tb.format_timecode(F(3601), 25) == "01:00:01:00"
    assert tb.format_timecode(tb.frame_time(59, NTSC30), NTSC30) == "00:00:01:29"


def test_format_us():
    assert tb.format_us(12_340_000) == "0:12.34"
    assert tb.format_us(62_005_000) == "1:02.01"  # half-up at 0.005
    assert tb.format_us(0) == "0:00.00"
    assert tb.format_us(-1_500_000) == "-0:01.50"
    assert tb.format_us(1_999_999, decimals=0) == "0:02"
    assert tb.format_us(5_123_456, decimals=3) == "0:05.123"


# ------------------------------------------------------------------ pydantic type
class _M(BaseModel):
    t: Rational
    opt: Rational | None = None
    many: list[Rational] = []


def test_rational_field_parse_and_serialize():
    m = _M(t="30000/1001", opt=0.5, many=[1, "1/3", F(2, 7)])
    assert m.t == NTSC30 and m.opt == F(1, 2)
    assert m.many == [F(1), F(1, 3), F(2, 7)]
    js = m.model_dump_json()
    assert '"30000/1001"' in js and '"1/2"' in js and '"1/1"' in js and '"1/3"' in js
    back = _M.model_validate_json(js)
    assert back == m
    assert m.model_dump()["t"] == "30000/1001"  # python-mode dump also uses the string form
    assert _M.model_json_schema()["properties"]["t"]["type"] == "string"


@pytest.mark.parametrize("bad", ["x", "1/0", True, None, [1, 2, 3]])
def test_rational_field_rejects(bad):
    with pytest.raises(ValueError):
        _M(t=bad)
