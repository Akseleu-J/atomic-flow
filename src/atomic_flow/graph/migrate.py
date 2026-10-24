"""Migrate the legacy tree.json into the P01 graph store (roadmap P01).

Preserves supported facts (files, symbols, contains edges, resolved callees as
`calls` edges); legacy content hashes keep their algorithm label and are NOT
rebranded as sha256. Unsupported legacy facts are listed in the report, never
dropped silently. The legacy tree stays authoritative until P02 parity.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from atomic_flow.graph.schema import edge_id, file_id, symbol_id
from atomic_flow.graph.store import GraphStore

SUPPORTED = {"node_id", "path", "symbol", "kind", "start_line", "end_line", "parent_id",
             "callees", "content_hash", "signature"}
UNSUPPORTED = {"imports", "calls", "reference_ids", "reference_names", "fan_in", "fan_out",
               "size", "mtime_ns", "summary"}


def migrate(tree: dict[str, Any], store_path: Path) -> dict[str, Any]:
    if not isinstance(tree, dict) or not isinstance(tree.get("nodes"), list):
        raise ValueError("legacy tree payload missing nodes list")
    nodes: list[dict] = []
    edges: list[dict] = []
    legacy_ids: dict[str, str] = {}
    unsupported_seen: set[str] = set()
    for legacy in tree["nodes"]:
        kind = "file" if not legacy.get("symbol") else (
            "class" if legacy.get("kind") == "class" else "function")
        if kind == "file":
            new_id = file_id(legacy["path"])
            source = {"digest": legacy.get("content_hash") or "", "algorithm": "legacy"}
            node = {"id": new_id, "kind": "file", "path": legacy["path"], "source": source,
                    "facts": {"legacy_signature": legacy.get("signature", "")},
                    "fact_status": {"legacy_signature": "stale"}, "facts_class": "extracted"}
        else:
            new_id = symbol_id(legacy["path"], legacy["symbol"])
            node = {"id": new_id, "kind": kind, "path": legacy["path"], "symbol": legacy["symbol"],
                    "source": {"digest": "", "algorithm": "legacy-file"},
                    "start_line": legacy.get("start_line", 1), "end_line": legacy.get("end_line", 1),
                    "facts": {"legacy_signature": legacy.get("signature", "")},
                    "fact_status": {"legacy_signature": "stale"}, "facts_class": "extracted"}
        legacy_ids[legacy["node_id"]] = new_id
        nodes.append(node)
        unsupported_seen |= (set(legacy) & UNSUPPORTED)
    for legacy in tree["nodes"]:
        parent = legacy.get("parent_id")
        if parent and parent in legacy_ids and legacy["node_id"] in legacy_ids:
            edges.append({"id": edge_id(legacy_ids[parent], "contains", legacy_ids[legacy["node_id"]]),
                          "kind": "contains", "src": legacy_ids[parent], "dst": legacy_ids[legacy["node_id"]],
                          "status": "active", "facts": {"extractor": "legacy-tree/4"},
                          "fact_status": {"extractor": "fresh"}, "facts_class": "extracted"})
        for callee in legacy.get("callees") or []:
            if callee in legacy_ids and callee != legacy["node_id"]:
                edges.append({"id": edge_id(legacy_ids[legacy["node_id"]], "calls", legacy_ids[callee]),
                              "kind": "calls", "src": legacy_ids[legacy["node_id"]], "dst": legacy_ids[callee],
                              "status": "active", "facts": {"extractor": "legacy-tree/4"},
                              "fact_status": {"extractor": "fresh"}, "facts_class": "extracted"})
    store = GraphStore(store_path)
    store.commit(base_generation=store.generation, put_nodes=nodes, put_edges=edges)
    return {"migrated_nodes": len(nodes), "migrated_edges": len(edges),
            "unsupported_facts_retained_in_legacy": sorted(unsupported_seen),
            "note": "symbol digests are file-level legacy; P02 re-extracts per-file sha256"}
