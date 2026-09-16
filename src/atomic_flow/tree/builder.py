"""TreeBuilder — parse Python sources into a hierarchical tree.

Uses the stdlib ``ast`` module. No external parsers required for v0.0.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import PathSpec

from atomic_flow.config import Settings
from atomic_flow.tree.node import Node, NodeKind

logger = logging.getLogger(__name__)


@dataclass
class Tree:
    """Result of building a tree from a repository."""

    root: Path
    nodes: list[Node] = field(default_factory=list)
    by_id: dict[str, Node] = field(default_factory=dict)

    def add(self, node: Node) -> None:
        self.nodes.append(node)
        self.by_id[node.id] = node

    def children_of(self, parent_id: str | None) -> list[Node]:
        return [n for n in self.nodes if n.parent_id == parent_id]

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for n in self.nodes:
            by_kind[n.kind.value] = by_kind.get(n.kind.value, 0) + 1
        return {
            "total": len(self.nodes),
            "by_kind": by_kind,
            "with_embedding": sum(1 for n in self.nodes if n.embedding is not None),
        }


class TreeBuilder:
    """Builds a Tree from a directory."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ignore = PathSpec.from_lines(
            "gitwildmatch", settings.tree.ignore_patterns
        )

    def build(self, root: Path) -> Tree:
        tree = Tree(root=root)
        py_files = self._collect_python_files(root)
        logger.info("Found %d Python files under %s", len(py_files), root)

        for file in py_files:
            self._parse_file(file, root, tree)

        return tree

    # ---- internals ----

    def _collect_python_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if self._ignore.match_file(rel):
                continue
            files.append(path)
        return sorted(files)

    def _parse_file(self, path: Path, root: Path, tree: Tree) -> None:
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            module = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            logger.warning("Skipping %s (syntax error): %s", rel, e)
            return

        file_node = Node(
            id=Node.make_id(rel, ""),
            kind=NodeKind.FILE,
            name=path.name,
            file=rel,
            start_line=1,
            end_line=len(source.splitlines()) or 1,
        )
        tree.add(file_node)
        self._walk(module, rel, tree, parent_id=file_node.id, path_prefix="")

    def _walk(
        self,
        node: ast.AST,
        file: str,
        tree: Tree,
        parent_id: str,
        path_prefix: str,
    ) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self._handle_class(child, file, tree, parent_id, path_prefix)

            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions only — methods handled inside classes.
                if path_prefix == "":
                    self._handle_function(
                        child, file, tree, parent_id, path_prefix, NodeKind.FUNCTION
                    )

    def _handle_class(
        self,
        node: ast.ClassDef,
        file: str,
        tree: Tree,
        parent_id: str,
        path_prefix: str,
    ) -> None:
        path = f"{path_prefix}.{node.name}" if path_prefix else node.name
        node_id = Node.make_id(file, path)

        cls = Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=node.name,
            file=file,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_id=parent_id,
        )
        tree.add(cls)

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_function(
                    child, file, tree, node_id, path, NodeKind.METHOD
                )
            elif isinstance(child, ast.ClassDef):
                self._handle_class(child, file, tree, node_id, path)

    def _handle_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file: str,
        tree: Tree,
        parent_id: str,
        path_prefix: str,
        kind: NodeKind,
    ) -> None:
        path = f"{path_prefix}.{node.name}" if path_prefix else node.name
        node_id = Node.make_id(file, path)

        fn = Node(
            id=node_id,
            kind=kind,
            name=node.name,
            file=file,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_id=parent_id,
            signature=self._signature(node),
        )
        tree.add(fn)

    @staticmethod
    def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [a.arg for a in node.args.args]
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)})"


def build_tree(root: Path, settings: Settings | None = None) -> Tree:
    """Convenience wrapper."""
    settings = settings or Settings.load()
    return TreeBuilder(settings).build(root)
