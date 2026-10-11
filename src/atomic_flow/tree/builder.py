"""Build a persistent, source-free tree from a project directory."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
from collections import Counter
from pathlib import Path

from atomic_flow.errors import TreeError
from atomic_flow.tree.node import TreeNode

TREE_VERSION = 1
SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".md", ".yaml", ".yml", ".toml", ".json"}


def node_id(path: str, symbol: str = "") -> str:
    value = f"{path}::{symbol}" if symbol else path
    return "sha1:" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16] + ":" + value


def _ignored(rel: str, patterns: list[str]) -> bool:
    parts = Path(rel).parts
    return any(part.startswith(".") and part not in {".github"} for part in parts) or any(
        fnmatch.fnmatch(part, pattern) or fnmatch.fnmatch(rel, pattern) for part in parts for pattern in patterns
    )


def _decorated_source(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    lines = source.splitlines()
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1 if node.decorator_list else node.lineno - 1
    return "\n".join(lines[start:node.lineno]).strip()


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, source: str, module_id: str):
        self.rel, self.source, self.module_id = rel, source, module_id
        self.nodes: list[TreeNode] = []
        self.scope: list[str] = []
        self.current: list[tuple[str, ast.AST]] = []
        self.call_stack: list[set[str]] = []
        self.imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add("." * node.level + node.module)

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        if self.call_stack and isinstance(fn, ast.Name):
            self.call_stack[-1].add(fn.id)
        elif self.call_stack and isinstance(fn, ast.Attribute):
            self.call_stack[-1].add(fn.attr)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node, "function")

    def _visit_definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        symbol = ".".join([*self.scope, node.name])
        parent_id = self.current[-1][0] if self.current else self.module_id
        if isinstance(node, ast.ClassDef):
            signature = f"class {node.name}"
        else:
            signature = _decorated_source(self.source, node)
        docstring = ast.get_docstring(node) or ""
        item = TreeNode(
            node_id=node_id(self.rel, symbol), path=self.rel, symbol=symbol, kind=kind,
            start_line=min([node.lineno, *(d.lineno for d in node.decorator_list)]) if getattr(node, "decorator_list", None) else node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            parent_id=parent_id, signature=signature[:500],
            summary=docstring.splitlines()[0][:240] if docstring else "",
        )
        self.nodes.append(item)
        self.scope.append(node.name)
        self.current.append((item.node_id, node))
        self.call_stack.append(set())
        for child in node.body:
            self.visit(child)
        item.calls = sorted(self.call_stack.pop())
        self.current.pop()
        self.scope.pop()


def build_tree(root: str | Path, ignore: list[str] | None = None) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise TreeError(f"Project directory does not exist: {root}")
    patterns = ignore or [".git", ".venv", "venv", "node_modules", "__pycache__", ".atomic_flow", "dist", "build"]
    nodes: list[TreeNode] = []
    call_counts: Counter[str] = Counter()
    file_count = 0
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if not _ignored((current_path / d).relative_to(root).as_posix(), patterns))
        for name in sorted(files):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if path.suffix.lower() not in SOURCE_SUFFIXES or _ignored(rel, patterns):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            file_count += 1
            file_node = TreeNode(node_id=node_id(rel), path=rel, kind="file", end_line=max(1, len(source.splitlines())))
            nodes.append(file_node)
            if path.suffix.lower() != ".py" and path.suffix.lower() != ".pyi":
                continue
            module = TreeNode(node_id=node_id(rel, "<module>"), path=rel, symbol="<module>", kind="module", end_line=max(1, len(source.splitlines())), parent_id=file_node.node_id)
            nodes.append(module)
            try:
                tree = ast.parse(source, filename=rel)
            except (SyntaxError, ValueError):
                continue
            module_doc = ast.get_docstring(tree) or ""
            module.summary = module_doc.splitlines()[0][:240] if module_doc else ""
            visitor = _PythonVisitor(rel, source, module.node_id)
            visitor.visit(tree)
            file_node.imports = sorted(visitor.imports)
            module.imports = sorted(visitor.imports)
            for item in visitor.nodes:
                call_counts.update(item.calls)
            nodes.extend(visitor.nodes)
    for node in nodes:
        if node.symbol and node.kind in {"function", "class"}:
            node.fan_out = len(node.calls)
            node.fan_in = call_counts[node.symbol.rsplit(".", 1)[-1]]
    return {"version": TREE_VERSION, "root": str(root), "files": file_count, "nodes": [n.model_dump() for n in nodes]}


def save_tree(root: str | Path, tree: dict) -> Path:
    import json

    target = Path(root).expanduser().resolve() / ".atomic_flow" / "tree.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_tree(root: str | Path) -> dict:
    import json

    target = Path(root).expanduser().resolve() / ".atomic_flow" / "tree.json"
    if not target.exists():
        raise TreeError(f"No project tree found at {target}. Run `atomic tree build` first.")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TreeError(f"Cannot read project tree {target}: {exc}") from exc
