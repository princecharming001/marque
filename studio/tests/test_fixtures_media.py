"""Checks that the shared synthetic-media fixtures in conftest.py are what other builders rely on."""

from __future__ import annotations

import subprocess
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import pytest

from studio.timebase import normalize_fps


def _v(streams):
    return next(s for s in streams if s["codec_type"] == "video")


def _a(streams):
    return [s for s in streams if s["codec_type"] == "audio"]


def test_synth_video(synth_video: Path, probe_streams):
    st = probe_streams(synth_video)
    v = _v(st)
    assert (v["width"], v["height"]) == (1080, 1920)
    assert normalize_fps(v["r_frame_rate"]) == 30 and v["pix_fmt"] == "yuv420p"
    assert v.get("color_transfer") == "bt709"
    a = _a(st)
    assert len(a) == 1 and a[0]["codec_name"] == "aac" and a[0]["sample_rate"] == "48000" and a[0]["channels"] == 1
    assert float(v["duration"]) == pytest.approx(6.0, abs=0.05)


def test_synth_rotated(synth_rotated: Path, probe_streams):
    v = _v(probe_streams(synth_rotated))
    assert (v["width"], v["height"]) == (1920, 1080)  # coded landscape
    rot = [sd.get("rotation") for sd in v.get("side_data_list", []) if "rotation" in sd]
    assert rot and abs(int(rot[0])) == 90  # portrait on display


def test_synth_hlg(synth_hlg: Path, probe_streams):
    v = _v(probe_streams(synth_hlg))
    assert v["codec_name"] == "hevc" and v["pix_fmt"] == "yuv420p10le"
    assert v["color_transfer"] == "arib-std-b67" and v["color_primaries"] == "bt2020"
    assert v["color_space"] == "bt2020nc"


def test_synth_vfr(synth_vfr: Path, probe_streams):
    v = _v(probe_streams(synth_vfr))
    assert Fraction(v["avg_frame_rate"]) != Fraction(v["r_frame_rate"])
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time",
                          "-of", "csv=p=0", str(synth_vfr)], capture_output=True, text=True, check=True).stdout
    pts = sorted(float(x) for x in out.split())
    deltas = {round(b - a, 4) for a, b in pairwise(pts)}
    assert len(deltas) >= 2  # irregular frame durations


def test_synth_noaudio(synth_noaudio: Path, probe_streams):
    st = probe_streams(synth_noaudio)
    assert _a(st) == [] and _v(st)["height"] == 1920


def test_synth_media_bundle(synth_media: dict[str, Path]):
    assert set(synth_media) == {"video", "rotated", "hlg", "vfr", "noaudio"}
    assert all(p.exists() and p.stat().st_size > 1000 for p in synth_media.values())
