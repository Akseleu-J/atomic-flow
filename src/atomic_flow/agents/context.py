"""Shared state available to tools during one role turn."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atomic_flow.config import ExecutionConfig
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
    edits_enabled: bool = False
    undo_stack: Any = None
    policy: Any = None
    trusted: bool = False
    approval_callback: Callable[[str, dict[str, Any], str], bool] | None = None
    file_hashes: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    revision: int = 0
    tree_ignore: list[str] | None = None
    respect_gitignore: bool = True
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    cancel_check: Callable[[], None] = field(default=lambda: None)

    def __post_init__(self):
        from atomic_flow.agents.source import confined_path, digest

        for node in self.nodes_by_id.values():
            if node.kind == "file" and node.content_hash:
                self.file_hashes.setdefault(node.path, node.content_hash)
        for node in self.nodes_by_id.values():
            if node.path not in self.file_hashes:
                try:
                    self.file_hashes[node.path] = digest(confined_path(self.project_root, node.path).read_bytes())
                except OSError:
                    pass

    def read_source(self, rel: str) -> bytes:
        from atomic_flow.agents.source import read_checked

        return read_checked(self.project_root, rel, self.file_hashes.get(rel, ""))

    def refresh(self) -> None:
        """Reparse after a verified edit; preserve aliases, never recycle removed ids."""
        from atomic_flow.activation.activate import ActivationIndex
        from atomic_flow.tree.builder import build_tree, save_tree

        tree = build_tree(self.project_root, self.tree_ignore, respect_gitignore=self.respect_gitignore)
        fresh = {item["node_id"]: TreeNode.model_validate(item) for item in tree["nodes"]}
        next_alias = max((int(a[1:]) for a in self.alias_to_id if a.startswith("n") and a[1:].isdigit()), default=0) + 1
        for nid in fresh:
            if nid not in self.aliases:
                alias = f"n{next_alias}"
                self.aliases[nid] = alias
                self.alias_to_id[alias] = nid
                next_alias += 1
        old_hashes = self.file_hashes.copy()
        self.file_hashes = {n.path: n.content_hash for n in fresh.values() if n.kind == "file"}
        self.evidence = {nid: text for nid, text in self.evidence.items() if nid in fresh
                         and old_hashes.get(fresh[nid].path) == self.file_hashes.get(fresh[nid].path)}
        self.nodes_by_id.clear()
        self.nodes_by_id.update(fresh)
        self.sources.clear()
        self.index = ActivationIndex(list(fresh.values()))
        save_tree(self.project_root, tree)
        self.revision += 1
