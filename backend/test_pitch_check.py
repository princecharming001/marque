"""Tests for the pitch-preservation regression guard (eval/pitch_check.py).

Fully keyless: the Goertzel detector itself is pure math, tested against
synthetic sine waves generated in Python (no ffmpeg needed). Only
`check_pitch_preserved`'s ffmpeg-decode boundary is monkeypatched.
"""
from __future__ import annotations

import math
import subprocess

from eval import pitch_check


def _sine(freq: float, sample_rate: int, n: int) -> list[float]:
    return [math.sin(2 * math.pi * freq * i / sample_rate) for i in range(n)]


def test_goertzel_detects_dominant_frequency():
    samples = _sine(440.0, pitch_check.SAMPLE_RATE, 4000)
    e440 = pitch_check.goertzel_energy(samples, pitch_check.SAMPLE_RATE, 440.0)
    e880 = pitch_check.goertzel_energy(samples, pitch_check.SAMPLE_RATE, 880.0)
    assert e440 > 1000 * e880


def test_goertzel_empty_samples_returns_zero():
    assert pitch_check.goertzel_energy([], pitch_check.SAMPLE_RATE, 440.0) == 0.0


def test_check_pitch_preserved_pass_on_reference_tone(monkeypatch):
    samples = _sine(440.0, pitch_check.SAMPLE_RATE, 4000)
    monkeypatch.setattr(pitch_check, "_decode_pcm", lambda path: samples)
    result = pitch_check.check_pitch_preserved("fake.mp4")
    assert result["ok"] is True
    assert result["ratio"] >= result["min_ratio"]


def test_check_pitch_preserved_fails_on_chipmunked_tone(monkeypatch):
    samples = _sine(880.0, pitch_check.SAMPLE_RATE, 4000)
    monkeypatch.setattr(pitch_check, "_decode_pcm", lambda path: samples)
    result = pitch_check.check_pitch_preserved("fake.mp4")
    assert result["ok"] is False


def test_check_pitch_preserved_handles_decode_failure(monkeypatch):
    def _boom(path):
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(pitch_check, "_decode_pcm", _boom)
    result = pitch_check.check_pitch_preserved("fake.mp4")
    assert result["ok"] is False
    assert "reason" in result


def test_check_pitch_preserved_handles_silent_track(monkeypatch):
    monkeypatch.setattr(pitch_check, "_decode_pcm", lambda path: [])
    result = pitch_check.check_pitch_preserved("fake.mp4")
    assert result["ok"] is False
    assert "reason" in result
