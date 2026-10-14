"""find_callers: list nodes that call or reference a given node."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext


class FindCallersArgs(BaseModel):
    node_id: str = Field(description="Short node id like 'n42', or full 'path::symbol'.")
    depth: int = Field(default=1, ge=1, le=3, description="Reserved; currently returns direct callers only.")
    include_references: bool = Field(default=True,
                                     description="Include non-call references (defvjp/scan/pallas_call wiring).")


class FindCallersTool(Tool):
    name = "find_callers"
    description = (
        "Return the nodes that call or reference this node. "
        "Callers come from the call-graph; references come from wiring-style edges "
        "(e.g. `defvjp(fwd, bwd)`, `scan(step, ...)`, `pallas_call(kernel, ...)`)."
    )
    args_schema = FindCallersArgs

    def run(self, args: FindCallersArgs, ctx: ToolContext) -> dict[str, Any]:
        real_id = ctx.alias_to_id.get(args.node_id, args.node_id)
        if real_id not in ctx.nodes_by_id:
            return {"error": f"unknown node_id: {args.node_id}",
                    "hint": "use search_tree to find valid ids"}

        callers: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []

        for other in ctx.nodes_by_id.values():
            if other.node_id == real_id:
                continue
            if real_id in (other.callees or []):
                callers.append(self._entry(other, ctx))
            elif args.include_references and real_id in (getattr(other, "reference_ids", []) or []):
                refs.append(self._entry(other, ctx))

        result: dict[str, Any] = {
            "node_id": ctx.aliases.get(real_id, real_id),
            "callers": callers[:30],
            "references": refs[:20],
            "counts": {"callers": len(callers), "references": len(refs)},
        }
        if not callers and not refs:
            result["note"] = "no callers or references found in the project graph"
        return result

    @staticmethod
    def _entry(node, ctx: ToolContext) -> dict[str, Any]:
        return {
            "node_id": ctx.aliases.get(node.node_id, node.node_id),
            "path": node.path,
            "symbol": node.symbol,
            "kind": node.kind,
            "lines": f"{node.start_line}-{node.end_line}",
        }
