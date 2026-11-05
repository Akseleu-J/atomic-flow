"""Dedicated notebook cell tools; raw JSON/output is never a model source view."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.source import SourceConflict
from atomic_flow.notebooks import NotebookDocument


class NotebookReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=6000, ge=256, le=12000)


class NotebookOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["set_source", "insert", "delete", "move"]
    cell_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    before_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    cell_type: Literal["code", "markdown", "raw"] | None = None
    source: str | None = Field(default=None, max_length=1_000_000)


class NotebookEditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    expected_hash: str = Field(pattern=r"^[a-f0-9]{40}$")
    operation: NotebookOperation
    preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _load(ctx: ToolContext, path: str) -> NotebookDocument:
    document = NotebookDocument.load(ctx.project_root, path, expected_hash=ctx.file_hashes.get(path),
                                     tree_ignore=ctx.tree_ignore, respect_gitignore=ctx.respect_gitignore,
                                     private_workspace=ctx.private_workspace)
    ctx.file_hashes.setdefault(path, document.content_hash)
    return document


class NotebookReadTool(Tool):
    name = "notebook_read"
    description = "Read notebook cell IDs and sources in redacted pages. Outputs and metadata are omitted; follow next_offset."
    args_schema = NotebookReadArgs
    contract = ToolContract(effects=("read",))

    def run(self, args: NotebookReadArgs, ctx: ToolContext) -> dict[str, Any]:
        ctx.cancel_check()
        document = _load(ctx, args.path)
        result = document.read(offset=args.offset, limit=args.limit)
        ctx.cancel_check()
        document.assert_current()
        coverage = ctx.record_read(args.path, document.content_hash, "notebook", result["offset"],
                                   result["end_offset"], result["total_chars"], source_complete=True)
        result["read_complete"] = coverage["read_complete"]
        return result


class NotebookEditTool(Tool):
    name = "notebook_edit"
    description = "Preview a stable-ID cell operation, then apply that exact preview token after complete current notebook read. Never executes code."
    args_schema = NotebookEditArgs
    contract = ToolContract(effects=("read", "write"), idempotent=False)

    def run(self, args: NotebookEditArgs, ctx: ToolContext) -> dict[str, Any]:
        document = _load(ctx, args.path)
        if args.expected_hash != document.content_hash:
            raise SourceConflict("expected_hash is not the current notebook revision")
        operation = args.operation.model_dump(exclude_none=True)
        preview = document.preview(operation)
        if args.preview_token is None:
            return preview.payload()
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        if ctx.undo_stack is None:
            return {"error": "internal: undo stack not attached"}
        if not ctx.read_coverage(args.path, document.content_hash)["read_complete"]:
            raise SourceConflict("read the complete current source before editing notebook cells")
        result = document.apply(preview, storage=ctx.undo_stack.storage_dir, approved=True,
                                preview_token=args.preview_token, cancel_check=ctx.cancel_check,
                                after_write=ctx.refresh)
        if result["status"] == "applied":
            ctx.file_hashes[args.path] = result["content_hash"]
        else:
            # Drop any read authority established against a failed refresh.
            ctx.file_hashes.pop(args.path, None)
            ctx.reads = [item for item in ctx.reads if item["path"] != args.path]
        return result
