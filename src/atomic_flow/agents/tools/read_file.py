"""Read admitted UTF-8 source using character offsets in the redacted view."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools.file_paths import admitted_path


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500, description="Indexed project-relative POSIX source path.")
    offset: int = Field(default=0, ge=0, description="Character offset in the redacted source; follow next_offset.")
    limit: int = Field(default=6000, ge=256, le=12000)


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a source file, including imports/configuration, in bounded redacted pages. Follow next_offset."
    args_schema = ReadFileArgs
    contract = ToolContract(effects=("read",))

    def run(self, args: ReadFileArgs, ctx: ToolContext) -> dict[str, Any]:
        try:
            ctx.cancel_check()
            admitted_path(ctx, args.path, existing=True)
            text = ctx.read_source(args.path).decode("utf-8-sig")
            if "\x00" in text:
                raise OSError("binary source is not supported")
        except (OSError, UnicodeError) as exc:
            return {"error": f"cannot read {args.path}: {exc}"}
        rendered = redact(text)
        offset = min(args.offset, len(rendered))
        source = rendered[offset:offset + args.limit]
        end = offset + len(source)
        content_hash = ctx.file_hashes[args.path]
        ctx.cancel_check()
        coverage = ctx.record_read(args.path, content_hash, "file", offset, end, len(rendered), source_complete=True)
        return {"path": args.path, "source": source, "content_hash": content_hash,
                "offset": offset, "next_offset": end if end < len(rendered) else None,
                "truncated": end < len(rendered), "total_chars": len(rendered),
                "source_complete": True, "read_complete": coverage["read_complete"], "representation": "redacted"}
