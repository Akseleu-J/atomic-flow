"""File-level structural delta (roadmap P02 first slice).

A delta replaces one file's owned nodes and edges wholesale (file-level
reparse is the correct delta unit; sub-file incrementality is a later
optimization). The transaction refuses to apply when the stored file digest
moved (content CAS) or the store generation moved (generation CAS); on any
refusal or crash the previous consistent snapshot stays visible. A fallback
full rebuild is an explicit operation with a recorded reason.
"""
from __future__ import annotations

from typing import Any

from atomic_flow.graph.adapter_ast import adapt_file
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError, file_id
from atomic_flow.graph.store import GraphGenerationConflict, GraphStore


def file_delta(path: str, new_bytes: bytes | None, *, expected_old_digest: str | None):
    """Ops for one file: None bytes delete the file from the graph."""
    owned = file_id(path)
    if new_bytes is None:
        return {"path": path, "put_nodes": [], "put_edges": [],
                "drop_node_ids": [owned], "parsed_files": 0, "deleted": True,
                "new_digest": None, "expected_old_digest": expected_old_digest}
    payload = adapt_file(path, new_bytes)
    return {"path": path, "put_nodes": payload["nodes"], "put_edges": payload["edges"],
            "drop_node_ids": [owned], "parsed_files": 1, "deleted": False,
            "new_digest": payload["nodes"][0]["source"]["digest"],
            "expected_old_digest": expected_old_digest}


def apply_delta(store: GraphStore, delta: dict[str, Any]) -> dict[str, Any]:
    """Validate digests, then commit; returns instrumentation for the report."""
    path, expected = delta["path"], delta.get("expected_old_digest")
    drop_ids = set(delta["drop_node_ids"])
    if expected is not None or not delta["deleted"]:
        # A file-level delta owns every node of that path; stale symbols of
        # the previous revision must not survive the replacement.
        drop_ids.update(node["id"] for node in store.nodes() if node["path"] == path)
    if expected is not None:
        node = next((n for n in store.nodes() if n["id"] == file_id(path)), None)
        stored_digest = node["source"]["digest"] if node else ""
        if stored_digest != expected:
            raise GraphContractError(
                f"source changed since delta was built: {path} (expected {expected[:12]}, "
                f"store has {stored_digest[:12] or 'no file'})")
    try:
        generation = store.commit(base_generation=store.generation,
                                  put_nodes=delta["put_nodes"], put_edges=delta["put_edges"],
                                  drop_node_ids=sorted(drop_ids))
    except GraphGenerationConflict as exc:
        raise GraphContractError(f"delta superseded: {exc}") from exc
    return {"path": path, "generation": generation, "deleted": delta["deleted"],
            "put_nodes": len(delta["put_nodes"]), "put_edges": len(delta["put_edges"]),
            "dropped": len(delta["drop_node_ids"]), "parsed_files": delta["parsed_files"],
            "scanned_files": 1, "relinked_files": 0}


def fallback_rebuild(store: GraphStore, files: dict[str, bytes], reason: str) -> dict[str, Any]:
    """Full rebuild inside one transaction; reason is recorded, never silent."""
    if not reason:
        raise GraphContractError("fallback rebuild requires an explicit reason")
    clean = clean_build(files)
    old_ids = [node["id"] for node in store.nodes()]
    generation = store.commit(base_generation=store.generation, put_nodes=clean["nodes"],
                              put_edges=clean["edges"], drop_node_ids=old_ids)
    return {"generation": generation, "reason": reason, "parsed_files": clean["parsed_files"],
            "scanned_files": len(files)}
