"""Versioned read port, omission review and source-only legacy export."""
from __future__ import annotations

import hashlib
import json

from atomic_flow.graph.identity import GraphIdentity, GraphReadContext
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore

READ_API_VERSION = 1


class GraphReader:
    """Read a validated snapshot; it never issues read proof or execution grants.

    Identity/context are both required for externally scoped access. Omitting
    both retains the trusted in-process single-root compatibility interface.
    A host must bind identity before exposing a reader to a remote principal.
    """

    def __init__(self, store: GraphStore, *, identity: GraphIdentity | None = None,
                 context: GraphReadContext | None = None):
        if (identity is None) != (context is None):
            raise GraphContractError("scoped graph reader needs identity and host context")
        if context is not None:
            context.require(identity)
            if store.identity != identity:
                raise GraphContractError("reader cannot rebind another or unscoped graph store")
        self.store, self.identity, self.context = store, identity, context

    def _authorize(self):
        if self.context is not None:
            self.context.require(self.identity)
            if self.store.identity != self.identity:
                raise GraphContractError("graph store namespace changed")

    def document(self):
        self._authorize()
        return self.store.document()

    def nodes(self, *, kind=None, path=None):
        return [node for node in self.document()["nodes"]
                if (kind is None or node["kind"] == kind) and (path is None or node["path"] == path)]

    def get(self, node_id):
        return next((node for node in self.nodes() if node["id"] == node_id), None)

    def edges(self, *, kind=None, node_id=None):
        return [edge for edge in self.document()["edges"]
                if (kind is None or edge["kind"] == kind) and
                (node_id is None or node_id in {edge["src"], edge["dst"]})]

    def search(self, query, *, limit=30):
        if not isinstance(query, str) or len(query) > 4096 or type(limit) is not int or not 1 <= limit <= 1000:
            raise GraphContractError("invalid bounded graph search")
        terms = query.casefold().split()
        return [node for node in self.nodes() if all(t in (node["path"] + " " + node.get("symbol", "")).casefold()
                                                    for t in terms)][:limit]

    def review(self):
        from atomic_flow.graph.resolve import exports_by_file, infer_file_edges, module_map

        document = self.document()
        self.store.validate()
        omissions, unresolved = [], []
        modules = module_map(sorted({n["path"] for n in document["nodes"] if n["kind"] == "file"}))
        exports = exports_by_file(document["nodes"])
        for node in document["nodes"]:
            facts = node["facts"]
            if facts.get("parse_ok") is False:
                omissions.append({"node_id": node["id"], "path": node["path"], "reason": "parser_failure"})
            if any(status == "stale" for status in node["fact_status"].values()):
                omissions.append({"node_id": node["id"], "path": node["path"], "reason": "stale_facts"})
            unresolved.extend({"node_id": node["id"], "reference": str(item)} for item in facts.get("unresolved", []))
            if node["kind"] == "file":
                inferred = infer_file_edges(node, modules=modules, exports=exports)
                unresolved.extend({"node_id": node["id"], "reference": str(item)} for item in inferred["unresolved"])
        unresolved = [dict(row) for row in sorted({tuple(sorted(row.items())) for row in unresolved})]
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return {"api_version": READ_API_VERSION, "schema_version": document["schema_version"],
                "generation": document["generation"], "source_revision": hashlib.sha256(canonical).hexdigest(),
                "namespace": self.identity.namespace() if self.identity else None,
                "counts": {"nodes": len(document["nodes"]), "edges": len(document["edges"])},
                "omissions": omissions, "unresolved": unresolved,
                "status": "partial" if omissions or unresolved else "valid_supported_structure",
                "semantic_requirements": "not_assessed"}

    def export_legacy(self):
        nodes, edges = self.nodes(), self.edges()
        ids = {n["id"]: n["path"] if n["kind"] == "file" else n["id"].removeprefix("sym:") for n in nodes}
        parents = {e["dst"]: ids[e["src"]] for e in edges if e["kind"] == "contains" and e["status"] == "active"}
        rows, omitted = [], []
        for node in nodes:
            if "legacy_record" in node["facts"]:
                rows.append(node["facts"]["legacy_record"])
                continue
            if node["kind"] not in {"file", "function", "class"}:
                omitted.append({"node_id": node["id"], "reason": "unsupported_legacy_kind", "kind": node["kind"]})
                continue
            rows.append({"node_id": ids[node["id"]], "path": node["path"], "kind": node["kind"],
                         "symbol": node.get("symbol", ""), "parent_id": parents.get(node["id"]),
                         "start_line": node.get("start_line", 1), "end_line": node.get("end_line", 1),
                         "signature": node["facts"].get("signature", ""),
                         "content_hash": node["source"]["digest"],
                         "callees": sorted(ids[e["dst"]] for e in edges if e["src"] == node["id"] and e["kind"] == "calls"
                                           and e["status"] == "active")})
        return {"version": 4, "nodes": rows, "compatibility": {"api_version": READ_API_VERSION,
                "omissions": omitted, "hash_algorithms_retained": True,
                "authority": "none; export is advisory source metadata"}}

    def tree_nodes(self):
        """Adapter for existing tree/search tools; never rebrand a sha256 as sha1.

        ToolContext independently hashes current source when content_hash is
        absent. Exported metadata is not delivered-source read proof.
        """
        from atomic_flow.tree.node import TreeNode

        allowed = set(TreeNode.model_fields)
        records = self.export_legacy()["nodes"]
        result = []
        for record in records:
            if record["kind"] not in {"file", "function", "class"}:
                continue
            value = {key: item for key, item in record.items() if key in allowed}
            value["content_hash"] = ""
            result.append(TreeNode.model_validate(value))
        return result
