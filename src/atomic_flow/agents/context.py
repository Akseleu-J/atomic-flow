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
    enforce_read_before_edit: bool = False
    reads: list[dict[str, Any]] = field(default_factory=list)
    question_callback: Callable[[dict[str, Any]], str | None] | None = None
    journal_event: Callable[..., Any] | None = None
    action_boundary: Callable[[], bool] | None = None
    mutation_scope: frozenset[str] | None = None
    result_store: Any = None
    private_workspace: bool = False
    active_allowed_tools: tuple[str, ...] = ()
    active_role: str = ""
    active_registry: Any = None
    workspace_session: Any = None
    lsp_client: Any = None
    documentation_client: Any = None
    hooks: Any = None
    source_watcher: Any = None
    invalidated_paths: set[str] = field(default_factory=set)

    def record_read(self, path: str, content_hash: str, view: str, offset: int, end: int,
                    total: int, node_id: str | None = None, source_complete: bool = False) -> dict[str, Any]:
        """Record only the redacted characters actually returned by a source tool.

        A complete rendering and a complete read are different: a signature can
        be fully returned without revealing the definition's body.
        """
        if not 0 <= offset <= end <= total:
            raise ValueError("invalid rendered read range")
        key = (path, content_hash, view, node_id)
        item = next((r for r in self.reads if (r["path"], r["content_hash"], r["view"], r["node_id"]) == key), None)
        if item is None:
            item = {"path": path, "content_hash": content_hash, "view": view, "node_id": node_id,
                    "total": total, "ranges": [], "source_complete": source_complete,
                    "representation": "redacted"}
            self.reads.append(item)
        if item["total"] != total or item["source_complete"] != source_complete:
            raise ValueError("rendered source changed within one indexed revision")
        ranges = sorted([*item["ranges"], [offset, end]])
        merged: list[list[int]] = []
        for start, stop in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], stop)
            else:
                merged.append([start, stop])
        item["ranges"] = merged
        item["read_complete"] = total == 0 or merged == [[0, total]]
        return {**item, "ranges": [r[:] for r in merged]}

    def read_coverage(self, path: str, content_hash: str, node_id: str | None = None) -> dict[str, Any]:
        matching = [r for r in self.reads if r["path"] == path and r["content_hash"] == content_hash
                    and (r["node_id"] is None or r["node_id"] == node_id)]
        return {"path": path, "content_hash": content_hash, "node_id": node_id,
                "read_complete": any(r["read_complete"] and r["source_complete"] for r in matching),
                "views": [{**r, "ranges": [span[:] for span in r["ranges"]]} for r in matching]}

    def require_read(self, path: str, content_hash: str, node_id: str | None = None) -> None:
        if self.enforce_read_before_edit and not self.read_coverage(path, content_hash, node_id)["read_complete"]:
            raise OSError("read the complete current source before editing; follow read_file/read_node next_offset")

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

    def invalidate_sources(self, paths) -> None:
        affected = set(paths)
        if not affected:
            return
        if ".gitignore" in affected:
            affected |= set(self.file_hashes)
        self.invalidated_paths.update(affected)
        for path in affected:
            self.file_hashes.pop(path, None)
        self.reads = [r for r in self.reads if r["path"] not in affected]
        self.evidence = {nid: text for nid, text in self.evidence.items()
                         if nid in self.nodes_by_id and self.nodes_by_id[nid].path not in affected}
        self.sources.clear()
        self.revision += 1

    def sync_external_changes(self) -> bool:
        if self.source_watcher is None:
            return True
        event = self.source_watcher.poll()
        if event is None:
            return True
        self.invalidate_sources(set(event.paths) - self.invalidated_paths)
        if not event.ready:
            return False
        self.refresh()
        try:
            self.source_watcher.acknowledge(event)
        except OSError:
            self.invalidate_sources(event.paths)
            raise
        self.invalidated_paths.clear()
        return True

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
        # Dedicated notebook reads track source-only views. Never index raw JSON
        # outputs as generic source, but preserve only still-current notebook proof.
        from atomic_flow.agents.source import digest
        from atomic_flow.agents.tools.file_paths import admitted_path
        for path in old_hashes:
            if path.lower().endswith(".ipynb"):
                try:
                    with admitted_path(self, path, existing=True, notebook=True).open("rb") as handle:
                        data = handle.read(1_000_001)
                    if len(data) > 1_000_000:
                        continue
                    self.file_hashes[path] = digest(data)
                except OSError:
                    pass
        self.reads = [r for r in self.reads if self.file_hashes.get(r["path"]) == r["content_hash"]
                      and (r["node_id"] is None or r["node_id"] in fresh)]
        self.evidence = {nid: text for nid, text in self.evidence.items() if nid in fresh
                         and old_hashes.get(fresh[nid].path) == self.file_hashes.get(fresh[nid].path)}
        self.nodes_by_id.clear()
        self.nodes_by_id.update(fresh)
        self.sources.clear()
        self.index = ActivationIndex(list(fresh.values()))
        save_tree(self.project_root, tree)
        self.revision += 1
