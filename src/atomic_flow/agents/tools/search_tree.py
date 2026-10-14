"""search_tree: lexical/semantic search over the project tree."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext


class SearchTreeArgs(BaseModel):
    query: str = Field(description="Free-text query; identifiers and docstrings are matched.")
    limit: int = Field(default=10, ge=1, le=40, description="Max number of results.")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0,
                             description="Drop results below this normalized score.")


class SearchTreeTool(Tool):
    name = "search_tree"
    description = (
        "Search the project tree by free text. Returns node ids with their scores. "
        "Use this when the provided context does not contain the symbol you need."
    )
    args_schema = SearchTreeArgs

    def run(self, args: SearchTreeArgs, ctx: ToolContext) -> dict[str, Any]:
        if ctx.index is None:
            return {"error": "search index unavailable in this request"}

        result = ctx.index.search(args.query, limit=args.limit)
        ranked = [
            {
                "node_id": ctx.aliases.get(node.node_id, node.node_id),
                "path": node.path,
                "symbol": node.symbol,
                "kind": node.kind,
                "score": round(score, 3),
            }
            for node, score in result.ranked
            if score >= args.min_score
        ]
        return {
            "query": args.query,
            "results": ranked,
            "weak": bool(getattr(result, "weak", False)),
        }
