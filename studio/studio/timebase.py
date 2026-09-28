"""One rational clock for the whole engine (ARCHITECTURE §2).

Conventions
-----------
* **Media times** in the Take Index are integer microseconds (``int``, names end in ``_us``).
* **Compiler/output times** are :class:`fractions.Fraction` seconds. Each edit is snapped to the output
  frame grid exactly once; audio sample positions are derived from those same rational instants, so
  video frames and audio samples never drift apart (the old ``pyRound`` drift class).
* Rounding is deterministic **round-half-up** (``floor(x + 1/2)``) unless a function takes a ``mode``
  (``"round" | "floor" | "ceil"``).
* Frame rates are exact rationals: 29.97 → ``30000/1001``, 59.94 → ``60000/1001``, 23.976 →
  ``24000/1001``. :func:`normalize_fps` cleans up float/str/ffprobe inputs; :func:`nearest_standard_fps`
  picks the CFR a VFR source is conformed to.

Pydantic
--------
:data:`Rational` is an annotated ``Fraction`` type for models: it accepts ``Fraction``, ``int``,
decimal ``float``/``str`` (``29.97`` → ``2997/100``; use :func:`normalize_fps` for rates), ``"num/den"``
strings, ``(num, den)`` pairs and ``{"num":…, "den":…}``; it always serializes to a ``"num/den"`` string
(``"30/1"`` for integers).
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Annotated, Any, Literal

from pydantic import PlainSerializer, PlainValidator, WithJsonSchema

__all__ = [
    "US_PER_S",
    "SAMPLE_RATE",
    "RoundMode",
    "STANDARD_FPS",
    "Rational",
    "to_fraction",
    "fraction_to_str",
    "fraction_from_str",
    "round_fraction",
    "us_to_seconds",
    "seconds_to_us",
    "normalize_fps",
    "nearest_standard_fps",
    "is_ntsc",
    "frame_duration",
    "frame_index",
    "frame_time",
    "snap_to_frame",
    "snap_us_to_frame",
    "us_to_frame",
    "frame_to_us",
    "frame_count",
    "sample_index",
    "sample_time",
    "us_to_sample",
    "sample_to_us",
    "samples_per_frame",
    "frame_to_sample",
    "format_timecode",
    "format_us",
]

US_PER_S = 1_000_000
SAMPLE_RATE = 48_000

RoundMode = Literal["round", "floor", "ceil"]

_HALF = Fraction(1, 2)


# ---------------------------------------------------------------------------------------------- basics
def to_fraction(x: Any) -> Fraction:
    """Exact conversion to ``Fraction``.

    Floats go through their shortest decimal repr (``0.1`` → ``1/10``, not the binary expansion).
    Strings may be ``"num/den"``, integers or decimals. Raises ``TypeError``/``ValueError`` otherwise.
    """
    if isinstance(x, bool):
        raise TypeError("bool is not a time/rational value")
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"non-finite rational: {x!r}")
        return Fraction(repr(x))
    if isinstance(x, str):
        return fraction_from_str(x)
    if isinstance(x, (tuple, list)) and len(x) == 2:
        num, den = x
        if isinstance(num, bool) or isinstance(den, bool):
            raise TypeError("bool is not a rational component")
        if not isinstance(num, int) or not isinstance(den, int):
            raise TypeError(f"rational pair must be ints, got {x!r}")
        if den == 0:
            raise ZeroDivisionError("rational with zero denominator")
        return Fraction(num, den)
    if isinstance(x, dict) and set(x) == {"num", "den"}:
        return to_fraction((x["num"], x["den"]))
    # numpy scalars and Decimal
    try:
        import numbers

        if isinstance(x, numbers.Integral):
            return Fraction(int(x))
        if isinstance(x, numbers.Real):
            return to_fraction(float(x))
    except Exception:  # pragma: no cover - defensive
        pass
    raise TypeError(f"cannot convert {type(x).__name__} to Fraction")


def fraction_from_str(s: str) -> Fraction:
    """Parse ``"30000/1001"``, ``"30"``, ``"29.97"`` or ``" 1 / 3 "``. ``"0/0"`` is rejected."""
    t = s.strip()
    if not t:
        raise ValueError("empty rational string")
    if "/" in t:
        num_s, den_s = t.split("/", 1)
        num, den = Fraction(num_s.strip()), Fraction(den_s.strip())
        if den == 0:
            raise ZeroDivisionError(f"rational with zero denominator: {s!r}")
        return num / den
    return Fraction(t)


def fraction_to_str(f: Fraction | int) -> str:
    """Serialize as ``"num/den"`` (always with a denominator, e.g. ``"30/1"``)."""
    f = to_fraction(f)
    return f"{f.numerator}/{f.denominator}"


def round_fraction(x: Fraction | int | float, mode: RoundMode = "round") -> int:
    """Round a rational to an int. ``"round"`` is half-up (``floor(x + 1/2)``), deterministic."""
    fx = to_fraction(x)
    if mode == "round":
        return math.floor(fx + _HALF)
    if mode == "floor":
        return math.floor(fx)
    if mode == "ceil":
        return math.ceil(fx)
    raise ValueError(f"unknown rounding mode {mode!r}")


def _validate_rational(v: Any) -> Fraction:
    try:
        return to_fraction(v)
    except (TypeError, ValueError, ZeroDivisionError) as e:
        raise ValueError(f"invalid rational {v!r}: {e}") from e


Rational = Annotated[
    Fraction,
    PlainValidator(_validate_rational),
    PlainSerializer(fraction_to_str, return_type=str),
    WithJsonSchema({"type": "string", "description": "Exact rational as 'num/den', e.g. '30000/1001'"}),
]
"""Pydantic field type for exact rationals, serialized as ``"num/den"`` strings."""


# ---------------------------------------------------------------------------------------------- µs <-> s
def us_to_seconds(us: int) -> Fraction:
    """Integer microseconds → exact Fraction seconds."""
    if isinstance(us, bool) or not isinstance(us, int):
        raise TypeError(f"microseconds must be int, got {type(us).__name__}")
    return Fraction(us, US_PER_S)


def seconds_to_us(s: Fraction | int | float | str, mode: RoundMode = "round") -> int:
    """Seconds (any rational-ish) → integer microseconds (half-up by default)."""
    return round_fraction(to_fraction(s) * US_PER_S, mode)


# ---------------------------------------------------------------------------------------------- fps
#: Standard rates a VFR source may be conformed to.
STANDARD_FPS: tuple[Fraction, ...] = (
    Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
    Fraction(48000, 1001), Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60),
    Fraction(90), Fraction(100), Fraction(120000, 1001), Fraction(120), Fraction(240),
)

_FPS_REL_TOL = Fraction(5, 10_000)  # 0.05 %: 23.98→23.976, 29.99→30, but 29.97 stays NTSC


def is_ntsc(fps: Fraction | int | float | str) -> bool:
    """True for exact ``N*1000/1001`` rates."""
    f = to_fraction(fps)
    return f.denominator == 1001 and f.numerator % 1000 == 0


def normalize_fps(fps: Fraction | int | float | str | tuple[int, int]) -> Fraction:
    """Clean up a frame rate into an exact rational.

    * exact ``N*1000/1001`` and integer Fractions are returned unchanged;
    * anything within 0.05 % of an NTSC rate ``N*1000/1001`` (N integer) becomes that rate
      (``29.97``, ``"29.97"``, ``29.970029…`` → ``30000/1001``; ``59.94`` → ``60000/1001``;
      ``23.976``/``23.98`` → ``24000/1001``);
    * anything within 0.05 % of an integer becomes the integer (``30.0083`` → ``30``);
    * otherwise the value is approximated with denominator ≤ 1001.

    Raises ``ValueError`` for non-positive rates.
    """
    f = to_fraction(fps)
    if f <= 0:
        raise ValueError(f"frame rate must be positive, got {fps!r}")
    if f.denominator == 1 or is_ntsc(f):
        return f
    # NTSC family first so 29.97 is not pulled to 30
    n = round_fraction(f * Fraction(1001, 1000))
    if n > 0:
        ntsc = Fraction(n * 1000, 1001)
        if abs(f - ntsc) / ntsc <= _FPS_REL_TOL:
            return ntsc
    i = round_fraction(f)
    if i > 0 and abs(f - i) / i <= _FPS_REL_TOL:
        return Fraction(i)
    return f.limit_denominator(1001)


def nearest_standard_fps(fps: Fraction | int | float | str) -> Fraction:
    """Nearest rate in :data:`STANDARD_FPS` (by relative distance); used to conform VFR to CFR.

    For VFR phone footage pass the *nominal* rate (ffprobe ``r_frame_rate`` / the capture setting),
    not a low average caused by dropped frames: an average of 27 fps maps to 25, not 29.97.
    """
    f = normalize_fps(fps)
    return min(STANDARD_FPS, key=lambda s: (abs(f - s) / s, -s))


# ---------------------------------------------------------------------------------------------- frames
def frame_duration(fps: Fraction | int | float | str) -> Fraction:
    """Duration of one frame in seconds."""
    return 1 / normalize_fps(fps)


def frame_index(t: Fraction | int | float | str, fps: Fraction | int | float | str,
                mode: RoundMode = "round") -> int:
    """Frame number containing/nearest to time ``t`` (seconds) on the grid ``n / fps``."""
    return round_fraction(to_fraction(t) * normalize_fps(fps), mode)


def frame_time(n: int, fps: Fraction | int | float | str) -> Fraction:
    """Exact start time (seconds) of frame ``n``."""
    return Fraction(n) / normalize_fps(fps)


def snap_to_frame(t: Fraction | int | float | str, fps: Fraction | int | float | str,
                  mode: RoundMode = "round") -> Fraction:
    """Snap a time (seconds) onto the frame grid. Idempotent."""
    return frame_time(frame_index(t, fps, mode), fps)


def us_to_frame(us: int, fps: Fraction | int | float | str, mode: RoundMode = "round") -> int:
    """Microseconds → frame index."""
    return frame_index(us_to_seconds(us), fps, mode)


def frame_to_us(n: int, fps: Fraction | int | float | str, mode: RoundMode = "round") -> int:
    """Frame index → microseconds (rounded; exact instants live in Fraction seconds)."""
    return seconds_to_us(frame_time(n, fps), mode)


def snap_us_to_frame(us: int, fps: Fraction | int | float | str, mode: RoundMode = "round") -> int:
    """Snap a microsecond time onto the frame grid and return microseconds."""
    return frame_to_us(us_to_frame(us, fps, mode), fps)


def frame_count(duration: Fraction | int | float | str, fps: Fraction | int | float | str) -> int:
    """Number of whole frames in ``duration`` seconds (rounded half-up)."""
    return frame_index(duration, fps, "round")


# ---------------------------------------------------------------------------------------------- samples
def sample_index(t: Fraction | int | float | str, sr: int = SAMPLE_RATE, mode: RoundMode = "round") -> int:
    """Audio sample index for time ``t`` seconds."""
    return round_fraction(to_fraction(t) * sr, mode)


def sample_time(n: int, sr: int = SAMPLE_RATE) -> Fraction:
    """Exact time (seconds) of sample ``n``."""
    return Fraction(n, sr)


def us_to_sample(us: int, sr: int = SAMPLE_RATE, mode: RoundMode = "round") -> int:
    """Microseconds → sample index (exact at 48 kHz: 1 sample = 20.8333 µs)."""
    return round_fraction(Fraction(us * sr, US_PER_S), mode)


def sample_to_us(n: int, sr: int = SAMPLE_RATE, mode: RoundMode = "round") -> int:
    """Sample index → microseconds."""
    return round_fraction(Fraction(n * US_PER_S, sr), mode)


def samples_per_frame(fps: Fraction | int | float | str, sr: int = SAMPLE_RATE) -> Fraction:
    """Exact samples per frame (1600 at 30 fps; 1601.6 = 8008/5 at 29.97)."""
    return Fraction(sr) / normalize_fps(fps)


def frame_to_sample(n: int, fps: Fraction | int | float | str, sr: int = SAMPLE_RATE,
                    mode: RoundMode = "round") -> int:
    """Sample index at the start of frame ``n`` (same instant as :func:`frame_time`)."""
    return sample_index(frame_time(n, fps), sr, mode)


# ---------------------------------------------------------------------------------------------- display
def format_timecode(t: Fraction | int | float | str, fps: Fraction | int | float | str) -> str:
    """``HH:MM:SS:FF`` (non-drop-frame, counting real frames at the nominal integer rate)."""
    f = normalize_fps(fps)
    n = frame_index(t, f, "floor")
    nominal = round_fraction(f)
    ff = n % nominal
    total_s = n // nominal
    return f"{total_s // 3600:02d}:{(total_s // 60) % 60:02d}:{total_s % 60:02d}:{ff:02d}"


def format_us(us: int, decimals: int = 2) -> str:
    """Human time ``M:SS.ss`` for microseconds (e.g. ``0:12.34``)."""
    sign = "-" if us < 0 else ""
    us = abs(us)
    scale = 10**decimals
    total = round_fraction(Fraction(us * scale, US_PER_S))
    whole, frac = divmod(total, scale)
    m, s = divmod(whole, 60)
    if decimals <= 0:
        return f"{sign}{m}:{s:02d}"
    return f"{sign}{m}:{s:02d}.{frac:0{decimals}d}"
