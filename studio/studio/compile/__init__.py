"""Compiler and renderer (ARCHITECTURE §6): CutDocument + TakeIndex → Timeline → files.

* :mod:`studio.compile.models`   — Timeline models (implemented; Fractions serialize as "num/den").
* :mod:`studio.compile.timeline` — :func:`compile` word IDs → output time, frame-grid snapping.
* :mod:`studio.compile.video`    — A-roll ffmpeg graph from the mezzanine.
* :mod:`studio.compile.overlays` — Remotion overlay props + render (ProRes 4444 alpha).
* :mod:`studio.compile.captions` — caption paging/placement helpers and SRT.
* :mod:`studio.compile.audio`    — dialogue edit, speed, voice chain, music, SFX, loudness.
* :mod:`studio.compile.master`   — composite + encode deliverables.
"""
