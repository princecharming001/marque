"""The CutDocument (ARCHITECTURE §5): the single, versioned edit decision document.

* :mod:`studio.doc.model`    — Pydantic models for the document.
* :mod:`studio.doc.ops`      — typed ops and :func:`~studio.doc.ops.apply_ops` (the only mutator).
* :mod:`studio.doc.validate` — pre-render document checks returning findings.
"""
