"""Migrate the legacy tree.json into the P01 graph store (roadmap P01).

Preserves supported facts (files, symbols, contains edges, resolved callees as
`calls` edges); legacy content hashes keep their algorithm label and are NOT
rebranded as sha256. Unsupported legacy facts are listed in the report, never
dropped silently. The legacy tree stays authoritative until P02 parity.
"""
from __future__ import annotations

from copy import deepcopy
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
    omissions: list[dict] = []
    identifiers: set[str] = set()
    counts: dict[tuple[str, str], int] = {}
    for legacy in tree["nodes"]:
        if not isinstance(legacy, dict) or not isinstance(legacy.get("node_id"), str):
            raise ValueError("legacy node requires a unique node_id")
        if legacy["node_id"] in identifiers:
            raise ValueError("duplicate legacy node ID")
        identifiers.add(legacy["node_id"])
        if legacy.get("kind") not in {"file", "module", "class", "function", "method"}:
            omissions.append({"node_id": legacy["node_id"], "reason": "unsupported_legacy_kind", "kind": legacy.get("kind")})
            continue
        raw_symbol = legacy.get("symbol", "")
        if not isinstance(raw_symbol, str):
            raise ValueError("legacy symbol must be text")
        # Legacy duplicate suffix belongs to identity, not qualified symbol text.
        symbol, marker, suffix = raw_symbol.rpartition("#")
        if not marker or not suffix.isdigit():
            symbol = raw_symbol
        key = (legacy["path"], symbol)
        counts[key] = counts.get(key, 0) + 1
        kind = "file" if not legacy.get("symbol") else (
            legacy["kind"] if legacy.get("kind") in {"class", "module"} else "function")
        if kind == "file":
            new_id = file_id(legacy["path"])
            source = {"digest": legacy.get("content_hash") or "", "algorithm": "legacy"}
            node = {"id": new_id, "kind": "file", "path": legacy["path"], "source": source,
                    "facts": {"legacy_signature": legacy.get("signature", "")},
                    "fact_status": {"legacy_signature": "stale"}, "facts_class": "extracted"}
        else:
            new_id = symbol_id(legacy["path"], symbol, counts[key])
            node = {"id": new_id, "kind": kind, "path": legacy["path"], "symbol": symbol,
                    "source": {"digest": "", "algorithm": "legacy-file"},
                    "start_line": legacy.get("start_line", 1), "end_line": legacy.get("end_line", 1),
                    "facts": {"legacy_signature": legacy.get("signature", "")},
                    "fact_status": {"legacy_signature": "stale"}, "facts_class": "extracted"}
        node["facts"]["legacy_record"] = deepcopy(legacy)
        node["fact_status"]["legacy_record"] = "stale"
        legacy_ids[legacy["node_id"]] = new_id
        nodes.append(node)
        unsupported_seen |= set(legacy) - SUPPORTED
    for legacy in tree["nodes"]:
        if legacy["node_id"] not in legacy_ids:
            continue
        parent = legacy.get("parent_id")
        if parent and parent not in legacy_ids:
            omissions.append({"node_id": legacy["node_id"], "reason": "dangling_parent", "target": parent})
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
            elif callee != legacy["node_id"]:
                omissions.append({"node_id": legacy["node_id"], "reason": "unresolved_callee", "target": callee})
    store = GraphStore(store_path)
    if store.generation or store.nodes() or store.edges():
        raise ValueError("migration requires an empty new graph; never merge a legacy cache silently")
    unique_edges = {}
    for edge in edges:
        if edge["id"] in unique_edges:
            omissions.append({"edge_id": edge["id"], "reason": "duplicate_legacy_relation"})
        unique_edges[edge["id"]] = edge
    edges = list(unique_edges.values())
    store.commit(base_generation=store.generation, put_nodes=nodes, put_edges=edges)
    return {"migrated_nodes": len(nodes), "migrated_edges": len(edges),
            "unsupported_facts_retained_in_legacy": sorted(unsupported_seen),
            "omissions": omissions, "identity_mapping": legacy_ids,
            "legacy_records_retained": True,
            "note": "symbol digests are file-level legacy; P02 re-extracts per-file sha256"}
