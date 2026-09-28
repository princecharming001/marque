"""The Director (placeholder; owned by the agent builder) — the only writer of the CutDocument.
Stages: brief → story cut → fine cut → finishing → finalize.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.agent.providers import ModelSpec
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex


def run_director(job: Job, index: TakeIndex, *, brief: str | None = None, style: str | None = None,
                 platform: str = "tiktok", spec: ModelSpec | None = None,
                 settings: Settings | None = None) -> CutDocument:
    """Produce the edited document (saved versions in ``doc/``)."""
    raise NotImplementedError("studio.agent.director.run_director")


def chat_edit(job: Job, instruction: str, *, spec: ModelSpec | None = None,
              settings: Settings | None = None) -> dict[str, Any]:
    """Apply a creator instruction as ops; returns ``{"doc": CutDocument, "summary": str, "results": [...]}``."""
    raise NotImplementedError("studio.agent.director.chat_edit")
