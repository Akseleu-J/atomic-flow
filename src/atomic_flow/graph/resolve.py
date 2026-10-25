"""Resolver layer (roadmap P02): inferred cross-file edges from stored facts.

Inputs are the file nodes' extracted facts (imports/raw_calls) plus the
project's export index — no re-parsing. `imports` edges bind a file to the
file owning the imported module; `calls` edges bind a caller file to a symbol
node when the imported module exports that exact leaf uniquely. Ambiguous or
unmatched names go to the unresolved report instead of a guessed edge.
Edges produced here carry facts_class "inferred" and status freshness, so
they are distinguishable from extracted contains-edges.
"""
from __future__ import annotations

from typing import Any

from atomic_flow.graph.schema import edge_id

RESOLVER = "name-resolver/1"


def module_map(paths: list[str]) -> dict[str, str]:
    """Dotted module name -> file path (packages map to __init__.py when present)."""
    mapping: dict[str, str] = {}
    inits = {path[:-len("/__init__.py")].replace("/", "."): path
             for path in paths if path.endswith("__init__.py")}
    for path in paths:
        if not path.endswith(".py"):
            continue
        dotted = path[:-len(".py")].replace("/", ".")
        if path.endswith("__init__.py"):
            mapping.setdefault(dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted, path)
            continue
        mapping[dotted] = path
    mapping.update(inits)
    return mapping


def exports_by_file(nodes: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """path -> {leaf symbol name -> [symbol node ids]}."""
    result: dict[str, dict[str, list[str]]] = {}
    for node in nodes:
        if node["kind"] in {"class", "function"}:
            leaf = node["symbol"].rsplit(".", 1)[-1]
            result.setdefault(node["path"], {}).setdefault(leaf, []).append(node["id"])
    return result


def infer_file_edges(file_node: dict[str, Any], *, modules: dict[str, str],
                     exports: dict[str, dict[str, list[str]]]) -> dict[str, Any]:
    """Inferred outgoing edges of one file from its stored facts."""
    path = file_node["path"]
    facts = file_node["facts"]
    edges: list[dict] = []
    unresolved: list[str] = []
    imported_files: set[str] = set()
    for name in facts.get("imports", []):
        parts = name.split(".")
        target = None
        for size in range(len(parts), 0, -1):
            candidate = modules.get(".".join(parts[:size]))
            if candidate and candidate != path:
                target = candidate
                break
        if target is None:
            unresolved.append(f"import {name}")
            continue
        imported_files.add(target)
        edges.append({"id": edge_id(file_node["id"], "imports", f"file:{target}"),
                      "kind": "imports", "src": file_node["id"], "dst": f"file:{target}",
                      "status": "active", "facts": {"resolver": RESOLVER},
                      "fact_status": {"resolver": "fresh"}, "facts_class": "inferred"})
    imported_leaves = {name.rsplit(".", 1)[-1] for name in facts.get("imports", [])}
    for call in facts.get("raw_calls", []):
        targets: list[str] = []
        for target_file in imported_files:
            found = exports.get(target_file, {}).get(call, [])
            targets.extend(found)
        if len(targets) == 1 and call in imported_leaves:
            edges.append({"id": edge_id(file_node["id"], "calls", targets[0]),
                          "kind": "calls", "src": file_node["id"], "dst": targets[0],
                          "status": "active", "facts": {"resolver": RESOLVER},
                          "fact_status": {"resolver": "fresh"}, "facts_class": "inferred"})
        elif len(targets) > 1:
            unresolved.append(f"call {call} (ambiguous: {len(targets)} candidates)")
        elif not targets:
            unresolved.append(f"call {call} (no imported exporter)")
    return {"edges": edges, "unresolved": sorted(set(unresolved))}
