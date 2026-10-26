"""File-level structural delta (roadmap P02 first slice).

A delta replaces one file's owned nodes and edges wholesale (file-level
reparse is the correct delta unit; sub-file incrementality is a later
optimization). The transaction refuses to apply when the stored file digest
moved (content CAS) or the store generation moved (generation CAS); on any
refusal or crash the previous consistent snapshot stays visible. A fallback
full rebuild is an explicit operation with a recorded reason.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from atomic_flow.graph.adapter_resource import adapt_source as adapt_file
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError, file_id
from atomic_flow.graph.store import GraphGenerationConflict, GraphStore


def file_delta(path: str, new_bytes: bytes | None, *, expected_old_digest: str | None,
               base_generation: int | None = None):
    """Ops for one file; optionally bind planning to a specific generation.

    None/empty old digest means the file must be absent, never an unconditional
    overwrite. Legacy callers without base_generation bind to the snapshot at
    apply entry, while callers that retain a planned delta can bind it earlier.
    """
    if base_generation is not None and (type(base_generation) is not int or base_generation < 0):
        raise GraphContractError("base generation must be a non-negative integer")
    owned = file_id(path)
    if new_bytes is None:
        return {"path": path, "put_nodes": [], "put_edges": [],
                "drop_node_ids": [owned], "parsed_files": 0, "deleted": True,
                "new_digest": None, "expected_old_digest": expected_old_digest,
                "base_generation": base_generation}
    payload = adapt_file(path, new_bytes)
    return {"path": path, "put_nodes": payload["nodes"], "put_edges": payload["edges"],
            "drop_node_ids": [owned], "parsed_files": 1, "deleted": False,
            "new_digest": payload["nodes"][0]["source"]["digest"],
            "expected_old_digest": expected_old_digest, "base_generation": base_generation}


def _relink(store: GraphStore, delta: dict[str, Any], drop_ids: set[str]):
    """Recompute inferred cross-file edges of the closure hit by this delta.

    The closure is the changed file plus the files it imports or that import
    it (pre-change topology from the store). Their inferred edges are rebuilt
    from stored file facts — no source re-parsing. Returns (edges, dropped
    edge ids, relinked file count, unresolved report).
    """
    from atomic_flow.graph.resolve import exports_by_file, infer_file_edges, module_map

    path = delta["path"]
    nodes_pre = [n for n in store.nodes()]
    post_nodes = [n for n in nodes_pre if n["id"] not in drop_ids and n["path"] != path]
    post_nodes.extend(delta["put_nodes"])
    file_nodes = {n["path"]: n for n in post_nodes if n["kind"] == "file"}
    modules = module_map(sorted(file_nodes))
    exports = exports_by_file(post_nodes)

    old_edges = store.edges()
    affected = {path}
    # Add/delete can resolve a previously unresolved import or change module
    # precedence (__init__.py vs a same-named module). The old resolved graph
    # cannot describe such consumers. Inspect stored raw imports, but only run
    # the resolver for files depending on changed module/export bindings.
    pre_files = {n["path"]: n for n in nodes_pre if n["kind"] == "file"}
    pre_modules = module_map(sorted(pre_files))
    pre_exports = exports_by_file(nodes_pre)
    changed_modules = {name for name in pre_modules.keys() | modules.keys()
                       if pre_modules.get(name) != modules.get(name)}
    changed_exports = {candidate for candidate in pre_exports.keys() | exports.keys()
                       if (pre_exports.get(candidate) != exports.get(candidate)
                           or pre_exports.star_names.get(candidate) != exports.star_names.get(candidate)
                           or (candidate in pre_exports.dynamic) != (candidate in exports.dynamic))}
    from atomic_flow.graph.resolve import dependencies
    for candidate_path, node in file_nodes.items():
        names, targets = dependencies(node, modules)
        old_node = pre_files.get(candidate_path)
        old_targets = dependencies(old_node, pre_modules)[1] if old_node else set()
        if (old_node is None or names & changed_modules
                or (targets | old_targets) & changed_exports):
            affected.add(candidate_path)

    dropped: set[str] = set()
    for edge in old_edges:
        if edge.get("facts_class") != "inferred":
            continue
        src_file = edge["src"][len("file:"):] if edge["src"].startswith("file:") else edge["src"].split("::")[0][len("sym:"):]
        if src_file in affected:
            dropped.add(edge["id"])

    new_edges: list[dict] = []
    unresolved: list[str] = []
    for affected_path in sorted(affected):
        node = file_nodes.get(affected_path)
        if node is None:
            continue
        inferred = infer_file_edges(node, modules=modules, exports=exports)
        new_edges.extend(inferred["edges"])
        unresolved.extend(f"{affected_path}: {item}" for item in inferred["unresolved"])
    return new_edges, sorted(dropped), sorted(affected), sorted(unresolved)


def apply_delta(store: GraphStore, delta: dict[str, Any]) -> dict[str, Any]:
    """Validate digests, then commit; returns instrumentation for the report."""
    path, expected = delta["path"], delta.get("expected_old_digest")
    base_generation = delta.get("base_generation")
    if base_generation is None:
        base_generation = store.generation
    if base_generation != store.generation:
        raise GraphContractError("delta superseded: store generation moved since planning")
    drop_ids = set(delta["drop_node_ids"])
    # A file-level delta owns every node of that path; stale symbols of
    # the previous revision must not survive the replacement.
    drop_ids.update(node["id"] for node in store.nodes() if node["path"] == path)
    node = next((n for n in store.nodes() if n["id"] == file_id(path)), None)
    stored_digest = node["source"]["digest"] if node else ""
    if stored_digest != (expected or ""):
        raise GraphContractError(
            f"source changed since delta was built: {path} (expected {(expected or '')[:12] or 'no file'}, "
            f"store has {stored_digest[:12] or 'no file'})")
    relink_edges, dropped_edges, affected, unresolved = _relink(store, delta, drop_ids)
    relinked = len(affected)
    put_edges = list(delta["put_edges"])
    put_edge_ids = {edge["id"] for edge in put_edges}
    put_edges.extend(edge for edge in relink_edges if edge["id"] not in put_edge_ids)
    # Retain incoming links to identities that survive a file replacement.
    # Dropping every old node first erased unchanged callers/importers even
    # when export bindings stayed identical and no relink was required.
    new_ids = {item["id"] for item in delta["put_nodes"]}
    stale_ids = drop_ids - new_ids
    dropped_edges = sorted(set(dropped_edges) | {edge["id"] for edge in store.edges()
                                                if edge["src"] in drop_ids})
    try:
        generation = store.commit(base_generation=base_generation,
                                  put_nodes=delta["put_nodes"], put_edges=put_edges,
                                  drop_node_ids=sorted(stale_ids), drop_edge_ids=dropped_edges)
    except GraphGenerationConflict as exc:
        raise GraphContractError(f"delta superseded: {exc}") from exc
    return {"path": path, "generation": generation, "deleted": delta["deleted"],
            "put_nodes": len(delta["put_nodes"]), "put_edges": len(put_edges),
            "dropped": len(drop_ids), "parsed_files": delta["parsed_files"],
            "scanned_files": 1, "read_files": 1 if not delta["deleted"] else 0,
            "relinked_files": relinked, "reused_files": max(0, sum(n["kind"] == "file" for n in store.nodes()) - relinked),
            "invalidated_paths": affected,
            "global_algorithms_invalidated": ["pagerank"], "unresolved": unresolved}


def fallback_rebuild(store: GraphStore, files: dict[str, bytes], reason: str, *,
                     source_check=lambda: None, cancel_check=lambda: None) -> dict[str, Any]:
    """Full rebuild inside one transaction; reason is recorded, never silent."""
    if not reason:
        raise GraphContractError("fallback rebuild requires an explicit reason")
    clean = clean_build(files)
    old_ids = [node["id"] for node in store.nodes()]
    cancel_check()
    source_check()
    cancel_check()
    generation = store.commit(base_generation=store.generation, put_nodes=clean["nodes"],
                              put_edges=clean["edges"], drop_node_ids=old_ids)
    return {"generation": generation, "reason": reason, "parsed_files": clean["parsed_files"],
            "scanned_files": len(files)}


def apply_changes(store: GraphStore, changed: dict[str, bytes | None], *, cancel_check=lambda: None,
                  source_check=lambda: None, moves=None) -> dict:
    """Private staged graph overlay followed by one visible generation CAS.

    A cancelled/failed multi-file operation never exposes its intermediate graph.
    source_check is a host validation callback immediately before publication.
    """
    base = store.generation
    previous = store.document()
    reports = []
    from atomic_flow.graph.relocation import relocation_report
    relocation = relocation_report(previous, previous, moves) if not changed else None
    with tempfile.TemporaryDirectory(prefix="atomic-graph-overlay-") as directory:
        staged = GraphStore(Path(directory) / "graph.json")
        staged.commit(base_generation=0, put_nodes=previous["nodes"], put_edges=previous["edges"])
        for path in sorted(changed):
            cancel_check()
            old = next((n["source"]["digest"] for n in staged.nodes()
                        if n["kind"] == "file" and n["path"] == path), None)
            reports.append(apply_delta(staged, file_delta(path, changed[path], expected_old_digest=old)))
        cancel_check()
        source_check()
        cancel_check()
        if changed:
            candidate = staged.document()
            relocation = relocation_report(previous, candidate, moves)
            store.commit(base_generation=base, drop_node_ids=[n["id"] for n in previous["nodes"]],
                         put_nodes=candidate["nodes"], put_edges=candidate["edges"])
    return {"deltas": len(reports), "generation": store.generation, "reports": reports,
            "relocation": relocation,
            "parsed_files": sum(r["parsed_files"] for r in reports),
            "scanned_files": len(changed), "read_files": sum(v is not None for v in changed.values()),
            "relinked_files": sum(r["relinked_files"] for r in reports),
            "invalidated_paths": sorted({path for report in reports for path in report["invalidated_paths"]}),
            "global_algorithms_invalidated": ["pagerank"] if changed else []}
