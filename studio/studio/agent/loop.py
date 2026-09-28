"""Champion loop (placeholder; owned by the agent builder): render → critique → Director revision →
position-swapped pairwise (≥2 judges; ties keep the champion) → stop after 2 winless rounds (guard 12).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from studio.agent.providers import ModelSpec
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex


def champion_loop(job: Job, doc: CutDocument, index: TakeIndex, *, rounds: int | None = None,
                  spec: ModelSpec | None = None, settings: Settings | None = None) -> CutDocument:
    """Iterate to convergence; returns the champion document (always shippable)."""
    raise NotImplementedError("studio.agent.loop.champion_loop")
