"""Scripted pydantic-ai models for the Director / critics / loop / pipeline tests (keyless, offline).

* :class:`ScriptedDirector` — a ``FunctionModel`` script keyed by the Director's current stage: ``plan[stage]`` is a
  list of steps, one per model response since the stage's prompt; a step is a list of ``(tool, args)`` calls,
  a callable returning one, or a string (the final text). When the steps run out it answers ``"<stage> done."``.
* :func:`structured` — a ``FunctionModel`` that answers a structured-output request (the ``final_result``
  output tool) with ``fn(messages, info) -> dict``.
* :data:`STORY` / :func:`full_plan` — a complete, valid edit of the hand-built fixture take.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

STORY = [{"from_word": "w0001", "to_word": "w0008"}, {"from_word": "w0014", "to_word": "w0018"},
         {"from_word": "w0020", "to_word": "w0041"}]

BRIEF_OPS = [
    {"op": "set_brief", "brief": {"goal": "Keep the pauses that matter and cut the rest.", "hook": "Most people cut "
                                  "their videos way too much", "cta": "follow me for more", "target_length_s": 14,
                                  "rubric": ["Does the hook land by 3 s?", "Is the CTA w0032-w0033 intact?",
                                             "Does every pause feel natural?"]}},
    {"op": "set_style", "style": {"primary": "educational", "dials": {"energy": 0.5, "pace": 0.5}}},
]
PIN_OPS = [{"op": "pin", "word_ids": ["w0018"], "kind": "payoff"},
           {"op": "pin", "word_ids": ["w0032", "w0033"], "kind": "cta"}]
CARD = {"op": "add_insert", "anchor_from_word": "w0020", "anchor_to_word": "w0024", "mode": "card",
        "card": {"template": "quote", "title": "Keep the pauses that matter"}, "job": "make the rule visible"}


def responses_since_prompt(messages: list[ModelMessage]) -> int:
    k = 0
    for m in reversed(messages):
        if isinstance(m, ModelResponse):
            k += 1
        elif isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts):
            break
    return k


def last_returns(messages: list[ModelMessage]) -> dict[str, Any]:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return {}
    return {p.tool_name: p.content for p in messages[-1].parts if isinstance(p, ToolReturnPart)}


def last_prompt(messages: list[ModelMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    return p.content if isinstance(p.content, str) else str(p.content)
    return ""


_STAGE_MARKERS = (
    ("STAGE 1/9", "brief"), ("STAGE 2/9", "story"), ("STAGE 3/9", "fine_cut"), ("FINISHING PASS 1/5", "reframe"),
    ("FINISHING PASS 2/5", "broll"), ("FINISHING PASS 3/5", "captions"), ("FINISHING PASS 4/5", "sound"),
    ("FINISHING PASS 5/5", "color"), ("STAGE 9/9", "finalize"), ("RENDER REVIEW", "revise"),
    ("CREATOR REQUEST", "chat"), ("FAILS hard invariants", "chat"),
)


def stage_from_prompt(text: str) -> str | None:
    """The stage a Director prompt opens (for scripts that do not hold the Director object)."""
    for marker, stage in _STAGE_MARKERS:
        if marker in text:
            return stage
    return None


class ScriptedDirector:
    def __init__(self, plan: dict[str, list[Any]], *, name: str = "scripted-director"):
        self.plan = plan
        self.name = name
        self.director: Any = None
        self.returns: list[tuple[str, str, Any]] = []  # (stage, tool, content)
        self.prompts: list[tuple[str, str]] = []
        self.calls = 0

    def model(self) -> FunctionModel:
        return FunctionModel(self, model_name=self.name)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        stage = self.director.session.stage if self.director is not None else stage_from_prompt(last_prompt(messages))
        for tool, content in last_returns(messages).items():
            self.returns.append((stage, tool, content))
        k = responses_since_prompt(messages)
        if k == 0:
            self.prompts.append((stage, last_prompt(messages)))
        steps = self.plan.get(stage or "", [])
        if k < len(steps):
            step = steps[k]
            if callable(step):
                step = step(self, messages)
            if isinstance(step, str):
                return ModelResponse(parts=[TextPart(step)])
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in step])
        return ModelResponse(parts=[TextPart(f"{stage} done.")])

    def results_for(self, tool: str, stage: str | None = None) -> list[Any]:
        return [c for s, t, c in self.returns if t == tool and (stage is None or s == stage)]


def full_plan() -> dict[str, list[Any]]:
    return {
        "brief": [[("get_overview", {}), ("get_transcript", {"view": "full"})],
                  [("meta_ops", {"ops": BRIEF_OPS})],
                  [("finish_stage", {"summary": "Brief: restraint idea, hook s001, payoff w0018, CTA w0032-w0033."})],
                  "Brief finished."],
        "story": [[("apply_ops", {"ops": [{"op": "set_story", "segments": STORY,
                                           "reason": "false start s002 and filler w0019 out"}, *PIN_OPS]})],
                  [("finish_stage", {"summary": "trying to finish before the radio test"})],
                  [("radio_test", {})],
                  [("finish_stage", {"summary": "Radio test passes: hook, retake s003, payoff, CTA."})],
                  "Story finished."],
        "fine_cut": [[("cut_ops", {"ops": [{"op": "set_gap", "gap_id": "g0009", "ms": 350}]})],
                     [("compile_check", {}), ("check_seams", {})],
                     [("finish_stage", {"summary": "g0009 trimmed to 350 ms; seams sit between thoughts."})],
                     "Fine cut finished."],
        "reframe": [[("finish_stage", {"summary": "none: calm delivery, punch-ins would add nothing"})], "ok"],
        "broll": [[("inserts_ops", {"ops": [CARD]})],
                  [("finish_stage", {"summary": "one quote card i001 on the rule; no stock b-roll"})], "ok"],
        "captions": [[("auto_captions", {})], [("finish_stage", {"summary": "auto-paged captions, default style"})],
                     "ok"],
        "sound": [[("voice_plan", {}), ("music_options", {}), ("sfx_guidance", {"kind": "pop"})],
                  [("audio_ops", {"ops": [{"op": "set_music", "spec": None}]})],
                  [("finish_stage", {"summary": "default voice chain; no music (calm explainer); no SFX"})], "ok"],
        "color": [[("finish_stage", {"summary": "none: exposure and skin look natural"})], "ok"],
        "finalize": [[("compile_check", {})], [("finish_stage", {"summary": "ready for render"})], "ok"],
    }


def structured(fn: Callable[[list[ModelMessage], AgentInfo], dict[str, Any]], *,
               name: str = "scripted-judge") -> FunctionModel:
    """A model answering structured-output requests through the output tool."""

    def call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool, fn(messages, info))])

    return FunctionModel(call, model_name=name)


def prompt_text(messages: list[ModelMessage]) -> str:
    """All user-prompt text of the conversation (images skipped)."""
    out = []
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    if isinstance(p.content, str):
                        out.append(p.content)
                    else:
                        out.extend(x for x in p.content if isinstance(x, str))
    return "\n".join(out)
