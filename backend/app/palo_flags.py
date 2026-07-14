"""Master feature flags for the Palo -> Yunicorn port.

Every ported capability is gated by one env flag, DEFAULT OFF, so this whole branch
ships dark and each phase can be flipped on independently in prod without a code
change (join the existing AI_QUALITY / EDL_AUTHOR convention in main.py).

Flags are read once at import; toggling requires a restart (Render redeploy), which
matches how the rest of the backend treats env config. A capability being OFF must
mean the ported code path is never entered — callers check these before doing any
work, so keyless-green + flag-off is the true zero-cost state.
"""
from __future__ import annotations

import os


def _on(name: str) -> bool:
    return os.environ.get(name, "0") != "0"


# Phase 0 master switch. When off, NOTHING in the port runs (belt-and-suspenders on
# top of the per-capability flags below) so a single env var disables the whole port.
PALO_PORT = _on("PALO_PORT")

# Per-capability flags (each gated ALSO by PALO_PORT). Default OFF.
MEMORY_V2 = _on("MEMORY_V2")            # Phase 1 — self-learning memory + ledger
IDEA_BANK = _on("IDEA_BANK")            # Phase 2 — reel/idea suggestions
TRACK_INSIGHTS = _on("TRACK_INSIGHTS")  # Phase 3 — post-performance learning
STRATEGY_COMPILER = _on("STRATEGY_COMPILER")  # Phase 4 — the self-learning brain
WRITE_AGENT = _on("WRITE_AGENT")        # Phase 5 — interactive write agent
EXEMPLAR_BANK = _on("EXEMPLAR_BANK")    # Phase 6 — golden-craft pattern library
# (VIDEO_BRAIN removed — audit found it was a dead gate no code path consumed; video
#  evidence already flows under EXEMPLAR_BANK / STRATEGY_COMPILER via the dossier
#  adapter. Re-add only when per-reel deep analysis is actually ported.)


def real_creator(creator_id: str) -> bool:
    """A learning WRITE must never land in the shared 'default'/empty bucket — unauthed or
    demo sessions default creator_id to 'default', and pooling memory/ledger there both leaks
    across users and poisons a real creator who ever transacts signed-out. Gate every port
    read/write on this (audit F13). `demo-…` ids are iOS continueAsDemo placeholders —
    device-local throwaways whose learning would mix demo traffic into real stores."""
    return bool(creator_id) and creator_id not in ("default", "demo") \
        and not creator_id.startswith("demo-")


def enabled(capability: bool) -> bool:
    """True only when the whole port is on AND this capability's flag is on. Every
    ported entry point should guard with `if not enabled(palo_flags.MEMORY_V2): ...`
    so flipping PALO_PORT off is a global kill-switch."""
    return PALO_PORT and capability
