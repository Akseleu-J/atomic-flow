"""Read admitted UTF-8 source using character offsets in the redacted view."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools.file_paths import admitted_path


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500, description="Indexed project-relative POSIX source path.")
    offset: int = Field(default=0, ge=0, description="Character offset in the redacted source; follow next_offset.")
    limit: int = Field(default=6000, ge=256, le=12000)
    start_line: int | None = Field(default=None, ge=1, description="Optional 1-based inclusive range in the redacted view.")
    end_line: int | None = Field(default=None, ge=1, description="Optional inclusive end; defaults to end of file.")

    @model_validator(mode="after")
    def line_order(self):
        if self.end_line is not None and (self.start_line is None or self.end_line < self.start_line):
            raise ValueError("end_line requires start_line and must not precede it")
        return self


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
        lines = rendered.splitlines(keepends=True)
        start = sum(map(len, lines[:max(0, (args.start_line or 1) - 1)]))
        stop = sum(map(len, lines[:args.end_line])) if args.end_line is not None else len(rendered)
        section = rendered[start:stop]
        offset = min(args.offset, len(section))
        source = section[offset:offset + args.limit]
        end = offset + len(source)
        content_hash = ctx.file_hashes[args.path]
        ctx.cancel_check()
        coverage = ctx.record_read(args.path, content_hash, "file", start + offset, start + end,
                                   len(rendered), source_complete=True)
        return {"path": args.path, "source": source, "content_hash": content_hash,
                "offset": offset, "next_offset": end if end < len(section) else None,
                "truncated": end < len(section), "total_chars": len(rendered), "view_chars": len(section),
                "start_line": args.start_line or 1, "end_line": min(args.end_line or len(lines), len(lines)),
                "total_lines": len(lines), "file_char_offset": start + offset,
                "source_complete": True, "read_complete": coverage["read_complete"], "representation": "redacted"}
