"""Media probing and ingest (ARCHITECTURE §3).

* :mod:`studio.media.models` — :class:`MediaInfo` (implemented).
* :mod:`studio.media.probe`  — ffprobe wrapper → :class:`MediaInfo`.
* :mod:`studio.media.ingest` — original → mezzanine (upright, SDR BT.709, CFR), 48 kHz WAV, proxy.
"""
