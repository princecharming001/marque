"""Yunicorn Studio: a quality-first agentic talking-head video editor (standalone engine).

Read ``ARCHITECTURE.md`` at the project root for the binding module contract. Top-level modules:

* :mod:`studio.config`   settings, house model ids, key handling, :func:`~studio.config.redact`
* :mod:`studio.timebase` the one rational clock (µs ints, Fraction seconds, frame/sample grids)
* :mod:`studio.jobs`     job directories, JSON helpers, trace, document versions
* :mod:`studio.media`    probe + ingest → mezzanine, audio.wav, proxy
* :mod:`studio.perception` the Take Index and its ID-based query API
* :mod:`studio.doc`      the CutDocument, typed ops (the only mutator) and pre-render validation
* :mod:`studio.compile`  CutDocument → Timeline → renders (A-roll, overlays, audio, master)
* :mod:`studio.audio`    voice chain, music, SFX
* :mod:`studio.broll`    b-roll sourcing, ranking, conform
* :mod:`studio.agent`    providers, skills, tools, Director, critics, champion loop
* :mod:`studio.qa`       metrics packet, invariants (hard gates), reports
* :mod:`studio.pipeline` end-to-end orchestration used by :mod:`studio.cli`
"""

__version__ = "0.1.0"
