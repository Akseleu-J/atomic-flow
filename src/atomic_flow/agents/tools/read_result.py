"""Read a page of an existing result without rerunning its producing tool."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.result_store import ResultStoreError


class ReadResultArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4096, ge=1, le=16384)


class ReadResultTool(Tool):
    name = "read_result"
    description = "Read bounded JSON text pages from a result ID in this request; no action is replayed."
    args_schema = ReadResultArgs
    contract = ToolContract(effects=("read",), max_output_bytes=131072)

    def run(self, args: ReadResultArgs, ctx: ToolContext) -> dict[str, Any]:
        ctx.cancel_check()
        store = getattr(ctx, "result_store", None)
        if store is None:
            return {"error": "result storage is unavailable in this request"}
        try:
            result = store.read(args.result_id, args.offset, args.limit)
        except ResultStoreError as exc:
            return {"error": str(exc), "result_id": args.result_id}
        ctx.cancel_check()
        return result
