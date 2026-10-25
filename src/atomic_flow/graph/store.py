"""Single-writer structural graph store with generation CAS (roadmap P01).

The first storage backend is an atomically replaced canonical JSON file with
fsync, mirroring the project's existing durability pattern. The SQLite backend
named by the roadmap is a later, separately accepted change; the transaction
API below is storage-independent.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from atomic_flow.graph.schema import GRAPH_SCHEMA_VERSION, GraphContractError, validate_edge, validate_node


class GraphGenerationConflict(GraphContractError):
    pass


class GraphStore:
    """In-memory snapshot with compare-and-swap commits to a JSON file."""

    def __init__(self, path: Path, *, schema_version: int = GRAPH_SCHEMA_VERSION):
        self.path = Path(path)
        if self.path.exists():
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("schema_version") != schema_version:
                raise GraphContractError("graph store schema mismatch; migrate explicitly")
            self._nodes: dict[str, dict] = {n["id"]: n for n in document["nodes"]}
            self._edges: dict[str, dict] = {e["id"]: e for e in document["edges"]}
            self._generation: int = document["generation"]
        else:
            self._nodes, self._edges, self._generation = {}, {}, 0
        self.validate()

    # -- reads ---------------------------------------------------------------
    @property
    def generation(self) -> int:
        return self._generation

    def nodes(self) -> list[dict]:
        return sorted(self._nodes.values(), key=lambda n: n["id"])

    def edges(self) -> list[dict]:
        return sorted(self._edges.values(), key=lambda e: e["id"])

    def validate(self) -> None:
        for node in self._nodes.values():
            validate_node(node)
        for edge in self._edges.values():
            validate_edge(edge)
            if edge["status"] == "active":
                for endpoint in (edge["src"], edge["dst"]):
                    if endpoint not in self._nodes:
                        raise GraphContractError(f"dangling edge endpoint {endpoint!r}")

    # -- writes --------------------------------------------------------------
    def commit(self, *, base_generation: int, put_nodes: list[dict] | None = None,
               put_edges: list[dict] | None = None, drop_node_ids: list[str] | None = None,
               drop_edge_ids: list[str] | None = None) -> int:
        """Apply one transaction; refuse when the base generation moved."""
        if base_generation != self._generation:
            raise GraphGenerationConflict(
                f"store generation moved to {self._generation}; re-read and retry")
        nodes, edges = dict(self._nodes), dict(self._edges)
        for node_id in drop_node_ids or []:
            nodes.pop(node_id, None)
            edges = {eid: edge for eid, edge in edges.items()
                     if edge["src"] != node_id and edge["dst"] != node_id}
        for edge_id in drop_edge_ids or []:
            edges.pop(edge_id, None)
        for node in put_nodes or []:
            validate_node(node)
            nodes[node["id"]] = node
        for edge in put_edges or []:
            validate_edge(edge)
            edges[edge["id"]] = edge
        candidate = GraphStore.__new__(GraphStore)
        candidate.path, candidate._nodes, candidate._edges = self.path, nodes, edges
        candidate._generation = base_generation + 1
        candidate.validate()
        payload = self._serialize(candidate._nodes, candidate._edges, candidate._generation)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="graph-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        self._nodes, self._edges, self._generation = nodes, edges, candidate._generation
        return self._generation

    @staticmethod
    def _serialize(nodes: dict, edges: dict, generation: int) -> bytes:
        document = {"schema_version": GRAPH_SCHEMA_VERSION, "generation": generation,
                    "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
                    "edges": sorted(edges.values(), key=lambda e: e["id"])}
        return (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8")

    def export_canonical(self) -> bytes:
        """Deterministic canonical bytes for parity checks and inspection."""
        return self._serialize(self._nodes, self._edges, self._generation)

    def document(self) -> dict[str, Any]:
        return json.loads(self.export_canonical())
