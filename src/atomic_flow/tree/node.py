"""Serializable project tree records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal["file", "class", "function"]


class TreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str                      # "<path>::<qualified symbol>" or just "<path>" for files
    path: str
    symbol: str = ""
    kind: NodeKind
    start_line: int = 1
    end_line: int = 1
    parent_id: str | None = None
    signature: str = ""
    summary: str = ""
    imports: list[str] = Field(default_factory=list)   # file nodes: "pkg.mod" and "pkg.mod.name"
    calls: list[str] = Field(default_factory=list)     # raw callee names seen in the body
    callees: list[str] = Field(default_factory=list)   # RESOLVED callee node_ids (real graph edges)
    reference_ids: list[str] = Field(default_factory=list)  # RESOLVED names passed as callbacks or wiring
    fan_in: int = 0
    fan_out: int = 0
    # File nodes only: used for incremental refresh and stale-index detection.
    content_hash: str = ""
    size: int = 0
    mtime_ns: int = 0

    @property
    def label(self) -> str:
        return f"{self.path}::{self.symbol}" if self.symbol else self.path
