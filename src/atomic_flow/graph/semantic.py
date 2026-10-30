"""Deterministic understand layer (roadmap P03, structural part only).

Layers, entry points and a guided tour order are computed by code from
name-level import facts. There is no LLM here: summaries/explanations are the
separate admitted-model stage, and this module never fabricates them. Every
artifact is bound to the source digest and extractor version so stale
semantic facts are detectable (P03 invalidation contract).
"""
from __future__ import annotations

import ast
import hashlib
import json
from typing import Any

from atomic_flow.graph.project import clean_build
from atomic_flow.graph.projection import PROJECTION_POLICY, coverage_from_file_nodes

SEMANTIC_EXTRACTOR = "structural-semantic/1"


def semantic_hashes(source: bytes) -> dict[str, str]:
    """Separate executable, API/import and documentation invalidation reasons."""
    def hashed(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    result = {"bytes": hashlib.sha256(source).hexdigest()}
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeError):
        return {**result, "body": result["bytes"], "api": result["bytes"], "docs": result["bytes"],
                "references": result["bytes"], "parse_status": "unavailable"}
    docs, api, references = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docs.append(ast.get_docstring(node, clean=False))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            api.append((node.name, ast.dump(node.args), ast.dump(node.returns) if node.returns else None,
                        [ast.dump(d) for d in node.decorator_list]))
        elif isinstance(node, ast.ClassDef):
            api.append((node.name, [ast.dump(base) for base in node.bases]))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            api.append(ast.dump(node))
        if isinstance(node, (ast.Call, ast.Import, ast.ImportFrom)):
            references.append(ast.dump(node))
    # Strip docstrings from the executable representation, not comments from bytes.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:]
    return {**result, "body": hashed(ast.dump(tree)), "api": hashed(api), "docs": hashed(docs),
            "references": hashed(references), "parse_status": "parsed"}


def file_dependencies(build: dict) -> dict[str, list[str]]:
    """Resolved edges only; build unresolved/coverage omissions bound completeness.

    An empty dependency list for a placeholder is not known independence.
    Consumers needing safe invalidation must consult the build coverage report.
    """
    paths = {n["id"]: n["path"] for n in build["nodes"]}
    direct = {n["path"]: set() for n in build["nodes"] if n["kind"] == "file"}
    for edge in build["edges"]:
        if edge["kind"] not in {"imports", "calls", "references"}:
            continue
        left, right = paths.get(edge["src"]), paths.get(edge["dst"])
        if left in direct and right in direct and left != right:
            direct[left].add(right)
    closure = {}
    for path in sorted(direct):
        seen, pending = set(), list(direct[path])
        while pending:
            candidate = pending.pop()
            if candidate in seen or candidate == path:
                continue
            seen.add(candidate)
            pending.extend(direct[candidate] - seen)
        closure[path] = sorted(seen)
    return closure


def assemble(files, build, artifacts) -> dict:
    """Source-bound narrative fragments and flows, without semantic truth claims."""
    digests = _digests(build)
    entry = entry_points(files)
    structural_tour = tour(files)
    steps = []
    for item in structural_tour["steps"]:
        path = item["file"]
        artifact = artifacts[path]
        steps.append({**item, "source_digest": digests[path], "node_id": "file:" + path,
                      "summary": artifact["result"]["summary"], "evidence": artifact["result"]["evidence"],
                      "cache_key": artifact["cache_key"], "status": artifact["status"]})
    node_paths = {n["id"]: n["path"] for n in build["nodes"]}
    flows = [{"src": e["src"], "dst": e["dst"], "kind": e["kind"],
              "source_digests": {p: digests[p] for p in sorted({node_paths[e["src"]], node_paths[e["dst"]]})},
              "facts_class": "inferred", "semantic_correctness": "not_assessed"}
             for e in build["edges"] if e["kind"] in {"calls", "imports"}]
    domains = {}
    for path, artifact in sorted(artifacts.items()):
        for tag in artifact["result"]["tags"]:
            domains.setdefault(tag, []).append({"node_id": "file:" + path, "source_digest": digests[path],
                                               "cache_key": artifact["cache_key"]})
    architecture = {"method": "structural packages plus inferred source-evidenced domain tags",
                    "source_digests": digests, "domains": domains,
                    "fragments": [{"path": path, "source_digest": digests[path],
                                   "summary": artifact["result"]["summary"], "evidence": artifact["result"]["evidence"],
                                   "cache_key": artifact["cache_key"]} for path, artifact in sorted(artifacts.items())],
                    "semantic_correctness": "not_assessed"}
    return {"layers": layers(files), "entry_points": entry,
            "tour": {**structural_tour, "steps": steps, "note": "Evidence-bound inferred narrative; semantic truth not assessed"},
            "flows": flows, "architecture": architecture, "unresolved": build["unresolved"],
            **_coverage(build)}


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
            "groups": {name: sorted(paths) for name, paths in sorted(groups.items())}, **_coverage(build)}


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
            "entry_files": entries, "method": "name-level; not a resolution claim", **_coverage(build)}


def tour(files: dict[str, bytes]) -> dict[str, Any]:
    """Deterministic walk order: entry files first, then the rest sorted."""
    build = clean_build(files)
    entries = set(entry_points(files)["entry_files"])
    order = sorted(entries) + sorted(path for path in files if path not in entries)
    return {"extractor": SEMANTIC_EXTRACTOR, "source_digests": _digests(build),
            "steps": [{"position": index + 1, "file": path,
                       "rationale": "entry candidate" if path in entries else "project file"}
                      for index, path in enumerate(order)],
            "note": "structural order only; narrative tours require the LLM stage", **_coverage(build)}


def _coverage(build: dict) -> dict:
    coverage = coverage_from_file_nodes(build["nodes"])
    return {**coverage, "projection_policy": PROJECTION_POLICY,
            "dependency_coverage": "partial" if build["unresolved"] or not coverage["coverage_complete"] else "static_inferred"}


def _digests(build: dict[str, Any]) -> dict[str, str]:
    return {node["path"]: node["source"]["digest"] for node in build["nodes"] if node["kind"] == "file"}


def invalidated(current_digests: dict[str, str], artifact: dict[str, Any]) -> list[str]:
    """Files whose digest moved since the artifact was produced."""
    return sorted(path for path, digest in artifact.get("source_digests", {}).items()
                  if current_digests.get(path) != digest)
