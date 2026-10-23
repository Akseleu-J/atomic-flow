"""Build a persistent, source-free, incrementally refreshable tree from a project directory."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from atomic_flow.activation.redact import redact
from atomic_flow.errors import TreeError
from atomic_flow.textutil import split_lines
from atomic_flow.tree.defaults import DEFAULT_IGNORE, MAX_FILE_BYTES, PYTHON_SUFFIXES, SOURCE_SUFFIXES
from atomic_flow.tree.node import TreeNode
from atomic_flow.tree.references import collect_references

TREE_VERSION = 4


def node_id(path: str, symbol: str = "") -> str:
    """Readable and stable while path + qualified symbol stay the same."""
    return f"{path}::{symbol}" if symbol else path


# --------------------------------------------------------------------------- ignore rules

class _Ignore:
    """Hidden dirs, configured glob patterns and (optionally) the project's .gitignore."""

    def __init__(self, root: Path, patterns: list[str], respect_gitignore: bool):
        self.patterns = patterns
        self.spec = None
        gitignore = root / ".gitignore"
        if respect_gitignore and gitignore.is_file():
            try:
                from pathspec import GitIgnoreSpec
                self.spec = GitIgnoreSpec.from_lines(gitignore.read_text(encoding="utf-8").splitlines())
            except (ImportError, OSError, UnicodeError):
                self.spec = None

    def __call__(self, rel: str, is_dir: bool) -> bool:
        parts = PurePosixPath(rel).parts
        if any(part.startswith(".") and part != ".github" for part in parts):
            return True
        if any(fnmatch.fnmatch(part, pat) for part in parts for pat in self.patterns):
            return True
        if any(fnmatch.fnmatch(rel, pat) for pat in self.patterns):
            return True
        return bool(self.spec and self.spec.match_file(rel + "/" if is_dir else rel))


# --------------------------------------------------------------------------- python indexing

def _signature(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Full, single-pass signature built from the AST (works for multi-line parameter lists)."""
    decorators = "".join(f"@{ast.unparse(d)} " for d in node.decorator_list)
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [f"{k.arg or '**'}={ast.unparse(k.value)}" for k in node.keywords]
        head = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    else:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        head = f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"
    return (decorators + head)[:500]


def _first_line(text: str | None, limit: int = 240) -> str:
    return text.strip().splitlines()[0][:limit] if text and text.strip() else ""


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, rel: str, file_id: str):
        self.rel, self.file_id = rel, file_id
        self.nodes: list[TreeNode] = []
        self.scope: list[str] = []
        self.stack: list[TreeNode] = []
        self.call_stack: list[set[str]] = []
        self.imports: set[str] = set()
        self.seen: Counter[str] = Counter()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = "." * node.level + (node.module or "")
        if node.module:
            self.imports.add(base)
        for alias in node.names:
            if alias.name != "*":
                self.imports.add(f"{base}.{alias.name}" if node.module else f"{base}{alias.name}")

    def visit_Call(self, node: ast.Call) -> None:
        if self.call_stack:
            fn = node.func
            if isinstance(fn, ast.Name):
                self.call_stack[-1].add(fn.id)
            elif isinstance(fn, ast.Attribute):
                self.call_stack[-1].add(fn.attr)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node, "function")

    def _definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        symbol = ".".join([*self.scope, node.name])
        # @property getter/setter pairs, @overload and conditional redefinitions share a name:
        # without a suffix they collide on node_id and silently overwrite each other.
        self.seen[symbol] += 1
        if self.seen[symbol] > 1:
            symbol = f"{symbol}#{self.seen[symbol]}"
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        item = TreeNode(
            node_id=node_id(self.rel, symbol), path=self.rel, symbol=symbol, kind=kind,  # type: ignore[arg-type]
            start_line=first, end_line=getattr(node, "end_lineno", None) or node.lineno,
            parent_id=self.stack[-1].node_id if self.stack else self.file_id,
            signature=redact(_signature(node)), summary=redact(_first_line(ast.get_docstring(node))),
        )
        self.nodes.append(item)
        self.scope.append(symbol.rsplit(".", 1)[-1])
        self.stack.append(item)
        self.call_stack.append(set())
        for child in node.body:
            self.visit(child)
        item.calls = sorted(self.call_stack.pop())
        self.stack.pop()
        self.scope.pop()


def _index_file(rel: str, source: str, digest: str, size: int, mtime_ns: int, is_python: bool) -> list[TreeNode]:
    lines = split_lines(source)
    file_node = TreeNode(node_id=node_id(rel), path=rel, kind="file", end_line=max(1, len(lines)),
                         content_hash=digest, size=size, mtime_ns=mtime_ns)
    if not is_python:
        first = next((line.strip().lstrip("#").strip() for line in lines if line.strip()), "")
        file_node.summary = redact(first[:160])
        return [file_node]
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError):
        file_node.summary = "(unparseable Python file)"
        return [file_node]
    file_node.summary = redact(_first_line(ast.get_docstring(tree)))
    visitor = _PythonVisitor(rel, file_node.node_id)
    visitor.visit(tree)
    file_node.imports = sorted(visitor.imports)
    try:
        references, aliases = collect_references(source)
    except (SyntaxError, ValueError, RecursionError):
        references, aliases = {}, {}
    file_node.reference_ids = sorted({aliases.get(name, name) for name in references.get("", set())})
    for item in visitor.nodes:
        scope = item.symbol.rsplit(".", 1)[-1].split("#")[0]
        item.reference_ids = sorted({aliases.get(name, name) for name in references.get(scope, set())})
    return [file_node, *visitor.nodes]


# --------------------------------------------------------------------------- graph linking

def _module_keys(path: str) -> list[str]:
    """'src/a/b.py' -> ['src.a.b', 'a.b', 'b'];  'pkg/__init__.py' -> ['pkg']."""
    p = PurePosixPath(path)
    if p.suffix not in PYTHON_SUFFIXES:
        return []
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts))]


def _link(nodes: list[TreeNode]) -> None:
    """Resolve raw call *names* into call-graph *edges* between nodes.

    A bare-name counter (what v0.1 did) makes ``get``/``run``/``append`` look like the most
    important functions in the project. Resolution order here: same file -> files this file
    imports -> a project-wide unique definition. Ambiguous names produce no edge.
    """
    modules: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.kind == "file":
            for key in _module_keys(n.path):
                modules[key].append(n.path)
    imported: dict[str, set[str]] = {}
    for n in nodes:
        if n.kind == "file":
            targets: set[str] = set()
            for entry in n.imports:
                key = entry.lstrip(".")
                if key:
                    targets.update(modules.get(key, []))
            targets.discard(n.path)
            imported[n.path] = targets
    defs: dict[str, list[TreeNode]] = defaultdict(list)
    for n in nodes:
        if n.kind in {"function", "class"}:
            defs[n.symbol.rsplit(".", 1)[-1].split("#")[0]].append(n)
    fan_in: Counter[str] = Counter()
    for n in nodes:
        n.callees, n.reference_ids, n.fan_in, n.fan_out = [], n.reference_ids, 0, 0
    for n in nodes:
        if n.kind not in {"function", "class"}:
            continue
        resolved: list[str] = []
        for name in n.calls:
            candidates = defs.get(name, [])
            picked = [c for c in candidates if c.path == n.path]
            if not picked:
                allowed = imported.get(n.path, set())
                picked = [c for c in candidates if c.path in allowed]
            if not picked and len(candidates) == 1:
                picked = candidates
            for c in picked[:3]:
                if c.node_id != n.node_id and c.node_id not in resolved:
                    resolved.append(c.node_id)
        n.callees = resolved
        n.fan_out = len(resolved)
        fan_in.update(resolved)
    for n in nodes:
        resolved: list[str] = []
        for name in n.reference_ids:
            candidates = defs.get(name, [])
            picked = [c for c in candidates if c.path == n.path]
            if not picked:
                allowed = imported.get(n.path, set())
                picked = [c for c in candidates if c.path in allowed]
            if not picked and len(candidates) == 1:
                picked = candidates
            for c in picked[:3]:
                if c.node_id != n.node_id and c.node_id not in resolved:
                    resolved.append(c.node_id)
        n.reference_ids = resolved
        fan_in.update(resolved)
    for n in nodes:
        n.fan_in = fan_in[n.node_id]


# --------------------------------------------------------------------------- public API

def _join(rel_dir: str, name: str) -> str:
    return name if rel_dir in {"", "."} else f"{rel_dir}/{name}"


def _group_by_path(previous: dict | None) -> dict[str, list[TreeNode]]:
    if not previous or previous.get("version") != TREE_VERSION:
        return {}
    grouped: dict[str, list[TreeNode]] = defaultdict(list)
    for raw in previous.get("nodes", []):
        try:
            grouped[raw["path"]].append(TreeNode.model_validate(raw))
        except Exception:  # noqa: BLE001 - a corrupt record only costs a re-parse of that file
            continue
    return {path: items for path, items in grouped.items() if items and items[0].kind == "file"}


def build_tree(root: str | Path, ignore: list[str] | None = None, *, previous: dict | None = None,
               respect_gitignore: bool = True, max_file_bytes: int = MAX_FILE_BYTES) -> dict:
    """Index ``root``. With ``previous`` only files whose size/mtime/content changed are re-parsed."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise TreeError(f"Project directory does not exist: {root}")
    from atomic_flow.config import user_dir

    private_root = user_dir().resolve()
    if root == private_root:
        raise TreeError("Refusing to index atomic_flow private state as project source.")
    skip = _Ignore(root, list(DEFAULT_IGNORE if ignore is None else ignore), respect_gitignore)
    old = _group_by_path(previous)
    per_file: dict[str, list[TreeNode]] = {}
    parsed = reused = 0
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if not skip(_join(rel_dir, d), True)
                         and (current_path / d).resolve() != private_root)
        for name in sorted(files):
            rel = _join(rel_dir, name)
            path = current_path / name
            suffix = path.suffix.lower()
            if suffix not in SOURCE_SUFFIXES or skip(rel, False):
                continue
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > max_file_bytes:
                continue
            cached = old.get(rel)
            try:
                with path.open("rb") as handle:
                    data = handle.read(max_file_bytes + 1)
                if len(data) > max_file_bytes:
                    continue
                source = data.decode("utf-8-sig")
            except (OSError, UnicodeError):
                continue
            digest = hashlib.sha1(data).hexdigest()
            if cached and cached[0].content_hash == digest:      # touched, not edited
                cached[0] = cached[0].model_copy(update={"mtime_ns": stat.st_mtime_ns})
                per_file[rel] = cached
                reused += 1
                continue
            per_file[rel] = _index_file(rel, source, digest, stat.st_size, stat.st_mtime_ns, suffix in PYTHON_SUFFIXES)
            parsed += 1
    nodes = [n.model_copy(deep=True) for items in per_file.values() for n in items]
    _link(nodes)
    fingerprint = hashlib.sha1("\n".join(f"{n.path}:{n.content_hash}" for n in nodes if n.kind == "file").encode()).hexdigest()
    return {"version": TREE_VERSION, "root": root.name, "files": len(per_file), "fingerprint": fingerprint,
            "stats": {"parsed": parsed, "reused": reused}, "nodes": [n.model_dump() for n in nodes]}


def _tree_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / ".atomic_flow" / "tree.json"


def save_tree(root: str | Path, tree: dict) -> Path:
    target = _tree_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tree, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(target)                                            # atomic: no half-written tree.json
    return target


def load_tree(root: str | Path) -> dict:
    target = _tree_path(root)
    if not target.exists():
        raise TreeError(f"No project tree found at {target}. Run `atomic tree build` first.")
    try:
        tree = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TreeError(f"Cannot read project tree {target}: {exc}") from exc
    if tree.get("version") != TREE_VERSION:
        raise TreeError(f"Project tree format v{tree.get('version')} is outdated (need v{TREE_VERSION}). Run `atomic tree build`.")
    return tree


def refresh_tree(root: str | Path, ignore: list[str] | None = None, *, respect_gitignore: bool = True,
                 save: bool = True) -> tuple[dict, bool]:
    """Bring an existing tree up to date. Returns ``(tree, changed)``."""
    previous = load_tree(root)
    tree = build_tree(root, ignore, previous=previous, respect_gitignore=respect_gitignore)
    changed = tree["fingerprint"] != previous.get("fingerprint")
    if changed and save:
        save_tree(root, tree)
    return tree, changed


def diff_trees(old: dict, new: dict) -> dict[str, list[str]]:
    """File-level diff. Content hashes catch body edits that a node-id diff cannot see."""
    def files(tree: dict) -> dict[str, str]:
        return {n["path"]: n.get("content_hash", "") for n in tree["nodes"] if n["kind"] == "file"}
    a, b = files(old), files(new)
    return {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
            "modified": sorted(p for p in a.keys() & b.keys() if a[p] != b[p])}
