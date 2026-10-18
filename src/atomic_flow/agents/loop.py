"""Tool-use loop for text-only model backends.

Kaggle Model Proxy does not support native tool-use, so tools are invoked via a
text protocol: the model returns ``{"tool": "read_node", "args": {...}}`` as
its JSON output; the loop runs the tool, appends the result to the conversation,
and lets the model continue.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from atomic_flow.agents.base import ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.policy import PolicyEngine
from atomic_flow.agents.result import Outcome, ToolResult

TOOL_LOOP_MAX_TURNS = 6


def render_tools_block(registry: ToolRegistry, names: list[str]) -> str:
    """Render the allowed tool schemas as a compact system-prompt block."""
    schemas = registry.schemas(names)
    if not schemas:
        return ""
    lines = [
        "TOOLS",
        "You may call a tool when the current context is insufficient or when the task requires an action.",
        'Return ONLY this JSON to call a tool: {"tool": "<name>", "args": {<arguments>}}.',
        "**If the user's request asks to modify code (rename, add, remove, refactor, fix, update), "
        "this is an ACTION request. Your next response must be a tool call, not a final answer.**",
        "**For action requests you MUST call `edit_node` with the full new body instead of describing the change in your answer.**",
        "Before calling `edit_node`, you MUST call `read_node` with zoom=2 to obtain the current full body. "
        "If read_node returns next_offset, read the remaining pages with that offset before editing. "
        "The context may contain only a signature. Do not call `read_node` repeatedly once the target body is visible.",
        "After the body is fetched, call `edit_node` immediately and stop exploring; avoid extra read/search calls.",
        "After an edit you may call `run_command` to verify.",
        "Use at most a few calls total, then return the normal role output.",
        "Available tools:",
    ]
    for schema in schemas:
        props = schema["input_schema"].get("properties", {})
        args = ", ".join(
            f"{key}: {value.get('type', 'any')}"
            + ("" if value.get("default") is None else f"={value['default']}")
            for key, value in props.items()
        )
        lines.append(f"- {schema['name']}({args}) — {schema['description']}")
    return "\n".join(lines)


def looks_like_tool_call(parsed: Any) -> bool:
    """Return true only for an object with a non-empty tool name and dict args."""
    return (
        isinstance(parsed, dict)
        and isinstance(parsed.get("tool"), str)
        and bool(parsed["tool"])
        and isinstance(parsed.get("args", {}), dict)
    )


def execute_tool(parsed: dict[str, Any], registry: ToolRegistry, ctx: ToolContext, *,
                 allowed_tools: list[str] | tuple[str, ...], role: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate, authorize and audit every outcome, including early rejections."""
    ctx.cancel_check()
    name = parsed["tool"]
    tool = registry.get(name)
    record = {"tool": name, "args": parsed.get("args", {}), "role": role}

    def finish(payload: Any, outcome: Outcome | None = None):
        result = ToolResult.from_payload(payload, outcome=outcome)
        record["result_summary"] = result.summary()
        return result.model_payload(), record

    if name not in allowed_tools:
        return finish({"error": f"tool '{name}' is not allowed for role '{role}'"}, "denied")
    if tool is None:
        return finish({"error": f"unknown tool: {name}"})
    try:
        args = tool.args_schema.model_validate(parsed.get("args", {}))
    except ValidationError as exc:
        return finish({"error": "invalid tool arguments", "details": exc.errors(include_url=False, include_context=False)[:3]})
    if name == "edit_node" and not ctx.edits_enabled:
        return finish({"error": "edits disabled", "hint": "enable edits explicitly for this request"}, "denied")
    try:
        decision = (ctx.policy or PolicyEngine()).decide(name, args.model_dump(), project_trusted=ctx.trusted)
        record["policy_action"] = decision.action
        if decision.action == "forbid":
            return finish({"error": f"tool '{name}' is forbidden by policy", "reason": decision.reason}, "denied")
        if decision.action == "ask":
            callback = ctx.approval_callback
            if callback is None:
                return finish({"error": "tool requires approval but no approval channel is available",
                               "reason": decision.reason}, "denied")
            if not callback(name, args.model_dump(), decision.reason):
                return finish({"error": f"tool '{name}' was not approved by the user", "reason": decision.reason}, "denied")
        elif decision.action != "auto":
            return finish({"error": "unknown policy decision"}, "denied")
        result = tool.run(args, ctx)
    except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
        from atomic_flow.agents.execution import TaskCancelled
        if isinstance(exc, TaskCancelled):
            raise
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return finish(result)
