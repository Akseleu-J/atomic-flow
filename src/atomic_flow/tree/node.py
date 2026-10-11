"""Serializable project tree records."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    path: str
    symbol: str = ""
    kind: str
    start_line: int = 1
    end_line: int = 1
    parent_id: str | None = None
    signature: str = ""
    summary: str = ""
    imports: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    fan_in: int = 0
    fan_out: int = 0

    @property
    def label(self) -> str:
        return f"{self.path}::{self.symbol}" if self.symbol else self.path
