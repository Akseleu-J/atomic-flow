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

from atomic_flow.graph.adapter_ast import adapt_file


def clean_build(files: dict[str, bytes]) -> dict[str, Any]:
    nodes: list[dict] = []
    edges: list[dict] = []
    reverse: dict[str, dict[str, list[str]]] = {"imports": {}, "calls": {}}
    for path in sorted(files):
        payload = adapt_file(path, files[path])
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
    return {"nodes": nodes, "edges": edges, "reverse": reverse, "exports": exports,
            "parsed_files": len(files)}


def project_files(root: Path, *, suffix: str = ".py") -> dict[str, bytes]:
    root = Path(root)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob(f"*{suffix}")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts[:-1]):
            continue
        files[rel] = path.read_bytes()
    return files


def affected_by_name(reverse: dict[str, dict[str, list[str]]], names: Iterable[str]) -> list[str]:
    """Advisory name-level blast radius; never treats a name match as a resolved edge."""
    mentioned: set[str] = set()
    for name in names:
        for kind in ("imports", "calls"):
            mentioned.update(reverse[kind].get(name, ()))
    return sorted(mentioned)
