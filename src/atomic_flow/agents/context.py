"""Shared state available to tools during one role turn."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atomic_flow.tree.node import TreeNode


@dataclass
class ToolContext:
    """Everything a tool needs to run for one request."""

    project_root: Path
    nodes_by_id: dict[str, TreeNode]
    aliases: dict[str, str] = field(default_factory=dict)
    alias_to_id: dict[str, str] = field(default_factory=dict)
    sources: dict[str, list[str] | None] = field(default_factory=dict)
    index: Any = None
    budget_spent: float = 0.0
