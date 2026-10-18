"""read_node: fetch source of a tree node at a given zoom level."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext
from atomic_flow.tree.node import TreeNode


class ReadNodeArgs(BaseModel):
    node_id: str = Field(description="Short node id like 'n42', or full 'path::symbol'.")
    zoom: int = Field(default=2, ge=0, le=3,
                      description="0=signature, 1=+docstring, 2=full body, 3=+related nodes.")
    offset: int = Field(default=0, ge=0, description="Character offset into rendered source; follow next_offset for long nodes.")
    limit: int = Field(default=6000, ge=256, le=12000, description="Maximum source characters per page.")


def _split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")


class ReadNodeTool(Tool):
    name = "read_node"
    description = (
        "Read the source of a project node at a given zoom. "
        "zoom=0 signature only, zoom=1 adds docstring, zoom=2 body, "
        "zoom=3 additionally lists callers/callees/references. Follow next_offset to read remaining pages."
    )
    args_schema = ReadNodeArgs

    def run(self, args: ReadNodeArgs, ctx: ToolContext) -> dict[str, Any]:
        real_id = ctx.alias_to_id.get(args.node_id, args.node_id)
        node = ctx.nodes_by_id.get(real_id)
        if node is None:
            return {"error": f"unknown node_id: {args.node_id}",
                    "hint": "use search_tree to find valid ids"}

        try:
            text = ctx.read_source(node.path).decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            return {"error": f"cannot read {node.path}: {exc}"}
        lines = _split_lines(redact(text))
        source = self._render(node, lines, args.zoom, ctx)
        page = source[args.offset:args.offset + args.limit]
        next_offset = args.offset + len(page) if args.offset + len(page) < len(source) else None
        ctx.evidence[node.node_id] = (ctx.evidence.get(node.node_id, "") + "\n" + page).strip()
        return {
            "node_id": ctx.aliases.get(node.node_id, node.node_id),
            "path": node.path,
            "symbol": node.symbol,
            "kind": node.kind,
            "lines": f"{node.start_line}-{node.end_line}",
            "zoom": args.zoom,
            "source": page,
            "offset": args.offset, "next_offset": next_offset, "truncated": next_offset is not None,
            "content_hash": ctx.file_hashes[node.path],
        }

    @staticmethod
    def _render(node: TreeNode, lines: list[str], zoom: int, ctx: ToolContext) -> str:
        if node.kind == "file":
            limit = {0: 5, 1: 20, 2: 60, 3: 200}.get(zoom, 60)
            body = lines[:limit]
            head = f"# {node.path} (showing {len(body)} of {len(lines)} lines)\n"
            return head + "\n".join(body)

        if zoom == 0:
            return node.signature or node.symbol

        start, end = node.start_line, node.end_line
        if zoom == 1:
            end = min(end, start + 14)

        body_lines = lines[start - 1:end]
        numbered = "\n".join(f"{start + i:>4}| {line}" for i, line in enumerate(body_lines))

        if zoom == 3:
            related: list[str] = []
            for callee_id in (node.callees or [])[:8]:
                callee = ctx.nodes_by_id.get(callee_id)
                if callee:
                    alias = ctx.aliases.get(callee_id, callee_id)
                    related.append(f"  calls -> {alias}  {callee.path}::{callee.symbol}")
            for ref_id in (getattr(node, "reference_ids", []) or [])[:8]:
                if ref_id in (node.callees or []):
                    continue
                ref = ctx.nodes_by_id.get(ref_id)
                if ref:
                    alias = ctx.aliases.get(ref_id, ref_id)
                    related.append(f"  refs  -> {alias}  {ref.path}::{ref.symbol}")
            if related:
                numbered += "\n\n# related:\n" + "\n".join(related)

        if end < node.end_line:
            numbered += f"\n      ... ({node.end_line - end} more lines)"
        return numbered
