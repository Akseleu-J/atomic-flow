"""Clean structural build over project files plus name-level reverse indexes.

The reverse indexes are name matches (imports/raw calls), deliberately NOT
resolution: they answer "which files mention this name" so a delta can report
potentially affected files. True call/import resolution is the P02 resolver
milestone and must not be faked here.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from atomic_flow.graph.projection import coverage_from_file_nodes, project_file_projection
from atomic_flow.graph.resolve import exports_by_file, infer_file_edges, module_map


def clean_build(files: dict[str, bytes]) -> dict[str, Any]:
    nodes: list[dict] = []
    edges: list[dict] = []
    reverse: dict[str, dict[str, list[str]]] = {"imports": {}, "calls": {}}
    for path in sorted(files):
        payload = project_file_projection(path, files[path])
        nodes.extend(payload["nodes"])
        edges.extend(payload["edges"])
        file_node = payload["nodes"][0]
        for name in file_node["facts"]["imports"]:
            reverse["imports"].setdefault(name, []).append(path)
        for name in file_node["facts"]["raw_calls"]:
            reverse["calls"].setdefault(name, []).append(path)
    exports: dict[str, list[str]] = {}
    for node in nodes:
        if node["kind"] in {"class", "function"}:
            leaf = node["symbol"].rsplit(".", 1)[-1]
            exports.setdefault(leaf, []).append(node["id"])
    modules = module_map(sorted(files))
    by_file_exports = exports_by_file(nodes)
    unresolved: list[str] = []
    for node in nodes:
        if node["kind"] != "file":
            continue
        inferred = infer_file_edges(node, modules=modules, exports=by_file_exports)
        edges.extend(inferred["edges"])
        unresolved.extend(f"{node['path']}: {item}" for item in inferred["unresolved"])
    return {"nodes": nodes, "edges": edges, "reverse": reverse, "exports": exports,
            "parsed_files": len(files), "unresolved": sorted(unresolved), **coverage_from_file_nodes(nodes)}


def project_files(root: Path, *, suffix: str | None = ".py", tree_ignore=None, respect_gitignore=True,
                  private_workspace=False, limits=None, cancel_check=lambda: None) -> dict[str, bytes]:
    """Read a bounded, admitted snapshot; the same bytes are hashed and returned.

    A scan/event is not read authority. Tool reads and edits still need their own
    current source checks. Any failed read refuses this entire advisory snapshot.
    """
    from io import BytesIO
    from types import SimpleNamespace

    from atomic_flow.agents.source import SourceConflict, confined_path
    from atomic_flow.agents.tools.file_paths import admitted_path
    from atomic_flow.config import user_dir
    from atomic_flow.execution_inputs import private_credential_path
    from atomic_flow.project_resources import ProjectLimits, ResourceLimitError, source_files, stream_source
    from atomic_flow.tree.builder import _Ignore
    from atomic_flow.tree.defaults import DEFAULT_IGNORE, MAX_FILE_BYTES

    root = Path(root).absolute()
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (root, *root.parents)):
        raise SourceConflict("read-plan root may not contain links")
    limits = limits or ProjectLimits()
    cancel_check()
    def ignore_bytes():
        ignore_path = confined_path(root, ".gitignore")
        if not respect_gitignore or not ignore_path.exists():
            return None
        with ignore_path.open("rb") as handle:
            raw = handle.read(min(limits.max_file_bytes, MAX_FILE_BYTES) + 1)
        if len(raw) > min(limits.max_file_bytes, MAX_FILE_BYTES):
            raise ResourceLimitError("read-plan ignore policy exceeds byte limit")
        raw.decode("utf-8-sig")  # Invalid policy must not silently admit ignored source.
        return raw

    policy = ignore_bytes()
    ignore = _Ignore(root, list(DEFAULT_IGNORE) + list(tree_ignore or []), respect_gitignore)

    def skip(rel, is_dir):
        if ignore(rel, is_dir) or private_credential_path(rel):
            return True
        confined_path(root, rel)
        return False

    ctx = SimpleNamespace(project_root=root, tree_ignore=tree_ignore,
                          respect_gitignore=respect_gitignore, private_workspace=private_workspace)
    files, total = {}, 0
    for rel, _ in source_files(root, skip, limits, cancel_check, private_root=user_dir().resolve()):
        if suffix is not None and not rel.endswith(suffix):
            continue
        if len(files) >= limits.max_files:
            raise ResourceLimitError("read-plan source file limit exceeded")
        path = admitted_path(ctx, rel, existing=True, source_byte_limit=limits.max_file_bytes)
        sink = BytesIO()
        _, size = stream_source(path, limits, cancel_check, sink=sink,
                                remaining_bytes=limits.max_total_bytes - total)
        total += size
        files[rel] = sink.getvalue()
    cancel_check()
    if ignore_bytes() != policy:
        raise SourceConflict("ignore policy changed during read-plan scan")
    return files


def affected_by_name(reverse: dict[str, dict[str, list[str]]], names: Iterable[str]) -> list[str]:
    """Advisory name-level blast radius; never treats a name match as a resolved edge."""
    mentioned: set[str] = set()
    for name in names:
        for kind in ("imports", "calls"):
            mentioned.update(reverse[kind].get(name, ()))
    return sorted(mentioned)


def project_paths(root: Path, paths, *, tree_ignore=None, respect_gitignore=True,
                  private_workspace=False, limits=None, cancel_check=lambda: None) -> dict[str, bytes | None]:
    """Admit exact host-owned changed paths without enumerating other source files."""
    from types import SimpleNamespace

    from atomic_flow.agents.source import confined_path
    from atomic_flow.agents.tools.file_paths import admitted_path
    from atomic_flow.graph.schema import file_id
    from atomic_flow.project_resources import ProjectLimits, ResourceLimitError

    limits = limits or ProjectLimits()
    if len(paths) > limits.max_files:
        raise ResourceLimitError("delta path count exceeds project limit")
    ctx = SimpleNamespace(project_root=Path(root).absolute(), tree_ignore=tree_ignore,
                          respect_gitignore=respect_gitignore, private_workspace=private_workspace)
    result, total = {}, 0
    for path in sorted(set(paths)):
        cancel_check()
        file_id(path)
        target = confined_path(ctx.project_root, path)
        if not target.exists():
            result[path] = None
            continue
        target = admitted_path(ctx, path, existing=True, source_byte_limit=limits.max_file_bytes)
        with target.open("rb") as handle:
            data = handle.read(limits.max_file_bytes + 1)
        total += len(data)
        if len(data) > limits.max_file_bytes or total > limits.max_total_bytes:
            raise ResourceLimitError("delta source byte limit exceeded")
        result[path] = data
    cancel_check()
    return result
