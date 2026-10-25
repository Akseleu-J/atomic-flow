"""Deterministic understand layer (roadmap P03, structural part only).

Layers, entry points and a guided tour order are computed by code from
name-level import facts. There is no LLM here: summaries/explanations are the
separate admitted-model stage, and this module never fabricates them. Every
artifact is bound to the source digest and extractor version so stale
semantic facts are detectable (P03 invalidation contract).
"""
from __future__ import annotations

from typing import Any

from atomic_flow.graph.project import clean_build

SEMANTIC_EXTRACTOR = "structural-semantic/1"


def layers(files: dict[str, bytes]) -> dict[str, Any]:
    """Group files by top package and depth; deterministic, name-level only."""
    build = clean_build(files)
    groups: dict[str, list[str]] = {}
    for node in build["nodes"]:
        if node["kind"] != "file":
            continue
        parts = node["path"].split("/")
        groups.setdefault(parts[0] if len(parts) > 1 else ".", []).append(node["path"])
    return {"extractor": SEMANTIC_EXTRACTOR, "source_digests": _digests(build),
            "groups": {name: sorted(paths) for name, paths in sorted(groups.items())}}


def entry_points(files: dict[str, bytes]) -> dict[str, Any]:
    """Files no other project file mentions by module name (name-level heuristic)."""
    build = clean_build(files)
    module_of: dict[str, str] = {}
    for path in files:
        dotted = path[:-len(".py")].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
        module_of[dotted] = path
        module_of[dotted.rsplit(".", 1)[-1]] = path
    mentioned: set[str] = set()
    for name in build["reverse"]["imports"]:
        parts = name.split(".")
        for size in range(len(parts), 0, -1):
            target = module_of.get(".".join(parts[:size]))
            if target:
                mentioned.add(target)
                break
    entries = sorted(path for path in files if path not in mentioned)
    return {"extractor": SEMANTIC_EXTRACTOR, "source_digests": _digests(build),
            "entry_files": entries, "method": "name-level; not a resolution claim"}


def tour(files: dict[str, bytes]) -> dict[str, Any]:
    """Deterministic walk order: entry files first, then the rest sorted."""
    build = clean_build(files)
    entries = set(entry_points(files)["entry_files"])
    order = sorted(entries) + sorted(path for path in files if path not in entries)
    return {"extractor": SEMANTIC_EXTRACTOR, "source_digests": _digests(build),
            "steps": [{"position": index + 1, "file": path,
                       "rationale": "entry candidate" if path in entries else "project file"}
                      for index, path in enumerate(order)],
            "note": "structural order only; narrative tours require the LLM stage"}


def _digests(build: dict[str, Any]) -> dict[str, str]:
    return {node["path"]: node["source"]["digest"] for node in build["nodes"] if node["kind"] == "file"}


def invalidated(current_digests: dict[str, str], artifact: dict[str, Any]) -> list[str]:
    """Files whose digest moved since the artifact was produced."""
    return sorted(path for path, digest in artifact.get("source_digests", {}).items()
                  if current_digests.get(path) != digest)
