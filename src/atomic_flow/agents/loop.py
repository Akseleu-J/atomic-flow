"""Tool-use loop for text-only model backends.

Kaggle Model Proxy does not support native tool-use, so tools are invoked via a
text protocol: the model returns ``{"tool": "read_node", "args": {...}}`` as
its JSON output; the loop runs the tool, appends the result to the conversation,
and lets the model continue.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from atomic_flow.agents.base import ToolRegistry
from atomic_flow.agents.context import ToolContext

TOOL_LOOP_MAX_TURNS = 6


def render_tools_block(registry: ToolRegistry, names: list[str]) -> str:
    """Render the allowed tool schemas as a compact system-prompt block."""
    schemas = registry.schemas(names)
    if not schemas:
        return ""
    lines = [
        "TOOLS",
        "You may call a tool when the current context is insufficient.",
        'Return ONLY this JSON to call a tool: {"tool": "<name>", "args": {<arguments>}}.',
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


def execute_tool(parsed: dict[str, Any], registry: ToolRegistry, ctx: ToolContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and execute a tool call, returning its result and audit record."""
    name = parsed["tool"]
    tool = registry.get(name)
    record = {"tool": name, "args": parsed.get("args", {})}
    if tool is None:
        available = sorted(registry._tools)
        return {"error": f"unknown tool: {name}", "available": available}, record
    if getattr(ctx, "policy", None) is not None:
        decision = ctx.policy.decide(name, parsed.get("args", {}), project_trusted=getattr(ctx, "trusted", False))
        record["policy_action"] = decision.action
        if decision.action == "forbid":
            return {"error": f"tool '{name}' is forbidden by policy", "reason": decision.reason}, record
        if decision.action == "ask":
            callback = getattr(ctx, "approval_callback", None)
            if callback is None:
                return {"error": "tool requires approval but no approval channel is available",
                        "reason": decision.reason,
                        "hint": "run with an interactive CLI or pre-authorize via policy.yaml"}, record
            if not callback(name, parsed.get("args", {}), decision.reason):
                return {"error": f"tool '{name}' was not approved by the user", "reason": decision.reason}, record
        if name == "edit_node":
            ctx.edits_enabled = True
    try:
        args = tool.args_schema.model_validate(parsed["args"])
    except ValidationError as exc:
        return {"error": "invalid tool arguments", "details": exc.errors(include_url=False)[:3]}, record
    try:
        result = tool.run(args, ctx)
    except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the run
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return result, record
