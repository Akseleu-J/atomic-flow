"""Bounded glob discovery over admitted indexed project files."""
from __future__ import annotations

import fnmatch
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.tools.file_paths import admitted_path


class FindFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(default="**/*", min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=100, ge=1, le=300)

    @field_validator("pattern")
    @classmethod
    def relative_pattern(cls, value):
        if value.startswith("/") or any(c in value for c in ("\\", ":", "\x00")) or any(
            part in {"", ".", ".."} for part in value.split("/")
        ):
            raise ValueError("use a relative POSIX glob without traversal")
        return value


def glob_matches(path: str, pattern: str) -> bool:
    # Whole-path matching: * never crosses a directory; ** spans zero or more.
    segments = pattern.split("/")
    parts = path.split("/")
    memo = {}

    def match(i, j):
        if (i, j) in memo:
            return memo[i, j]
        if j == len(segments):
            result = i == len(parts)
        elif segments[j] == "**":
            result = match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        else:
            result = i < len(parts) and bool(re.fullmatch(fnmatch.translate(segments[j]), parts[i])) and match(i + 1, j + 1)
        memo[i, j] = result
        return result

    return match(0, 0)


class FindFilesTool(Tool):
    name = "find_files"
    description = "Find indexed source paths by POSIX glob (*, ?, [], **), with stable pages and indexed hashes. No file bodies."
    args_schema = FindFilesArgs
    contract = ToolContract(effects=("read",))

    def run(self, args, ctx):
        matches = []
        skipped = 0
        for path in sorted(ctx.file_hashes):
            ctx.cancel_check()
            if not glob_matches(path, args.pattern):
                continue
            try:
                admitted_path(ctx, path, existing=True)
            except OSError:
                skipped += 1
                continue
            matches.append({"path": path, "indexed_content_hash": ctx.file_hashes[path]})
        end = min(args.offset + args.limit, len(matches))
        return {"files": matches[args.offset:end], "total": len(matches), "offset": args.offset,
                "next_offset": end if end < len(matches) else None, "truncated": end < len(matches),
                "skipped_files": skipped, "index_revision": ctx.revision, "file_bodies_read": False}
