"""Perception: build the Take Index (ARCHITECTURE §4).

* :mod:`studio.perception.index` — Take Index models, ID-based query API, save/load, :func:`build_index`.
* :mod:`studio.perception.transcribe` — ASR (ElevenLabs Scribe v2 verbatim; AssemblyAI fallback).
* :mod:`studio.perception.gaps` — acoustic boundary snapping (Silero VAD + RMS minima) and gaps.
* :mod:`studio.perception.takes` — sentences, completeness, retake clusters.
* :mod:`studio.perception.prosody` — f0/intensity/duration z-scores, emphasis, energy.
* :mod:`studio.perception.audio_metrics` — noise floor, SNR, loudness, clipping, room tone.
* :mod:`studio.perception.visual` — face track, blinks, look-away, reading, blur/luma.
* :mod:`studio.perception.frames` — frame grabs and contact sheets with burned IDs.
"""
