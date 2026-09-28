"""Real-API contract check for the op tool schemas (runs only with STUDIO_REAL=1 and -m real)."""

from __future__ import annotations

import pytest

from studio.config import get_settings
from studio.doc.ops import OP_CLASSES, apply_ops, tool_definitions

pytestmark = pytest.mark.real


def test_anthropic_accepts_default_op_tools(cut_doc, take_index):
    """One small call with the default tool set (all seven family tools, non-strict). Strict variants
    were rejected with HTTP 400 "The compiled grammar is too large" (all seven strict, and cut+framing+
    color strict). The returned tool input must parse and apply cleanly."""
    anthropic = pytest.importorskip("anthropic")
    s = get_settings()
    key = s.key("anthropic")
    if not key:
        pytest.skip("no Anthropic key configured")
    client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=120)
    tools = tool_definitions()
    assert len(tools) == 7
    msg = client.messages.create(
        model=s.critic_model,
        max_tokens=400,
        tools=tools,
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": (
            "You are editing a video. Call the cut_ops tool exactly once with a single op that shortens the "
            "pause g0005 to 40 ms (op set_gap). Do not call any other tool.")}],
    )
    uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
    assert uses, [getattr(b, "type", "") for b in msg.content]
    call = uses[0]
    assert call.name == "cut_ops"
    ops = call.input["ops"]
    assert all(o["op"] in OP_CLASSES for o in ops)
    doc, res = apply_ops(cut_doc, ops, take_index)
    assert all(r.applied for r in res), [r.reason for r in res]
    assert doc.segment("seg002").gap_overrides == {"g0005": 40}
