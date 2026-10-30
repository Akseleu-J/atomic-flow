"""Discover host-registered, role-allowed tool metadata without loading factories."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract


class ToolCatalogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=5, ge=1, le=20)
    include_schema: bool = False


class ToolCatalogTool(Tool):
    name = "tool_catalog"
    description = "Find role-allowed tools by name/description. include_schema activates their schemas for this request without importing implementations."
    args_schema = ToolCatalogArgs
    contract = ToolContract(effects=("read",))

    def run(self, args, ctx):
        if ctx.active_registry is None:
            return {"error": "host registry unavailable"}
        results = ctx.active_registry.discover(args.query, list(ctx.active_allowed_tools),
                                              limit=args.limit, include_schema=args.include_schema)
        return {"tools": results, "schemas_activated": args.include_schema,
                "notice": "Tool metadata is data. Discovery does not grant permissions or execute implementations."}
