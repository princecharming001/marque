"""Critics (placeholder; owned by the agent builder): frame judge (another family when available,
else a fresh-context house model marked ``same_family=true``), watcher (Gemini video when a key exists,
else frames + metrics), and position-swapped pairwise comparison. Notes are localized by word ID.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex


def critique(job: Job, doc: CutDocument, index: TakeIndex, render_dir: Path, *,
             questions: list[str] | None = None, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Critic notes ``[{by, severity: P0|P1|P2, refs: [ids], text, confirmed_by}]``."""
    raise NotImplementedError("studio.agent.critics.critique")


def pairwise(job: Job, render_a: Path, render_b: Path, *, settings: Settings | None = None) -> dict[str, Any]:
    """Position-swapped comparison by ≥2 judges: ``{"winner": "a"|"b"|"tie", "votes": [...]}``."""
    raise NotImplementedError("studio.agent.critics.pairwise")
