"""Single-writer structural graph store with generation CAS (roadmap P01).

The first storage backend is an atomically replaced canonical JSON file with
fsync, mirroring the project's existing durability pattern. The SQLite backend
named by the roadmap is a later, separately accepted change; the transaction
API below is storage-independent.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from atomic_flow.durability import sync_directory
from atomic_flow.graph.identity import GraphIdentity
from atomic_flow.graph.schema import GRAPH_SCHEMA_VERSION, GraphContractError, validate_edge, validate_node
from atomic_flow.locking import exclusive_file


class GraphGenerationConflict(GraphContractError):
    pass


class GraphStore:
    """In-memory snapshot with compare-and-swap commits to a JSON file."""

    def __init__(self, path: Path, *, schema_version: int = GRAPH_SCHEMA_VERSION, identity: GraphIdentity | None = None):
        self.path = Path(path)
        if identity is not None and not isinstance(identity, GraphIdentity):
            raise GraphContractError("graph identity must be host typed context")
        self.identity = identity
        if schema_version != GRAPH_SCHEMA_VERSION:
            raise GraphContractError("unsupported graph schema version")
        self._safe_path()
        self._source_digest = None
        if self.path.exists():
            raw = self._read_bytes()
            document = self._decode(raw)
            persisted_identity = document.get("identity")
            if persisted_identity is not None:
                bound = GraphIdentity(**persisted_identity)
                if identity is not None and bound != identity:
                    raise GraphContractError("persisted graph root identity mismatch")
                self.identity = bound
            elif identity is not None:
                raise GraphContractError("unbound legacy graph requires explicit scoped rebuild")
            if document.get("schema_version") != schema_version:
                raise GraphContractError("graph store schema mismatch; migrate explicitly")
            self._nodes: dict[str, dict] = {n["id"]: n for n in document["nodes"]}
            self._edges: dict[str, dict] = {e["id"]: e for e in document["edges"]}
            self._generation: int = document["generation"]
            self._source_digest = hashlib.sha256(raw).hexdigest()
        else:
            self._nodes, self._edges, self._generation = {}, {}, 0
        self.validate()

    # -- reads ---------------------------------------------------------------
    @property
    def generation(self) -> int:
        return self._generation

    def nodes(self) -> list[dict]:
        return copy.deepcopy(sorted(self._nodes.values(), key=lambda n: n["id"]))

    def edges(self) -> list[dict]:
        return copy.deepcopy(sorted(self._edges.values(), key=lambda e: e["id"]))

    def _safe_path(self):
        for path in (self.path, *self.path.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise GraphContractError("graph storage paths may not contain links or junctions")

    def _read_bytes(self):
        self._safe_path()
        with self.path.open("rb") as stream:
            raw = stream.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            raise GraphContractError("graph store exceeds byte bound")
        return raw

    @staticmethod
    def _decode(raw):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise GraphContractError("duplicate graph JSON key")
                result[key] = value
            return result
        try:
            document = json.loads(raw, object_pairs_hook=pairs,
                                  parse_constant=lambda _x: (_ for _ in ()).throw(GraphContractError("nonfinite graph JSON")))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GraphContractError("malformed graph store") from exc
        if (not isinstance(document, dict) or set(document) - {"schema_version", "generation", "nodes", "edges", "identity"}
                or not {"schema_version", "generation", "nodes", "edges"} <= set(document)
                or type(document["schema_version"]) is not int or document["schema_version"] != GRAPH_SCHEMA_VERSION
                or type(document["generation"]) is not int or document["generation"] < 0
                or not isinstance(document["nodes"], list) or not isinstance(document["edges"], list)):
            raise GraphContractError("invalid graph document fields/version/generation")
        if "identity" in document:
            if not isinstance(document["identity"], dict) or set(document["identity"]) != {
                    "principal_id", "workspace_id", "project_id", "root_id", "policy_version"}:
                raise GraphContractError("invalid persisted graph namespace")
            GraphIdentity(**document["identity"])
        for name, validator in (("nodes", validate_node), ("edges", validate_edge)):
            seen = set()
            for item in document[name]:
                validator(item)
                if item["id"] in seen:
                    raise GraphContractError("duplicate graph record ID")
                seen.add(item["id"])
        return document

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
        """CAS one persisted snapshot under a cooperating-writer OS lease.

        Atomic replacement alone is not CAS: separately opened stale handles
        must compare the persisted generation while holding the same lease.
        The marker is persistent and is never unlinked to change lock identity.
        """
        self._safe_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        for path in (self.path, lock_path, *self.path.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise GraphContractError("graph storage paths may not contain links or junctions")
        manager = exclusive_file(lock_path, busy_message="graph writer lease is held")
        try:
            manager.__enter__()
        except ValueError as exc:
            raise GraphGenerationConflict(str(exc)) from exc
        try:
            return self._commit_locked(base_generation=base_generation, put_nodes=put_nodes,
                                       put_edges=put_edges, drop_node_ids=drop_node_ids,
                                       drop_edge_ids=drop_edge_ids)
        finally:
            manager.__exit__(None, None, None)

    def _commit_locked(self, *, base_generation: int, put_nodes: list[dict] | None,
                       put_edges: list[dict] | None, drop_node_ids: list[str] | None,
                       drop_edge_ids: list[str] | None) -> int:
        if type(base_generation) is not int or base_generation < 0:
            raise GraphContractError("base generation must be a non-negative integer")
        try:
            raw = self._read_bytes()
            persisted = self._decode(raw)
            persisted_digest = hashlib.sha256(raw).hexdigest()
        except FileNotFoundError:
            if self._source_digest is not None:
                raise GraphGenerationConflict("graph snapshot disappeared; reopen before commit") from None
            persisted_generation = 0
            persisted_digest = None
        else:
            if not isinstance(persisted, dict) or persisted.get("schema_version") != GRAPH_SCHEMA_VERSION:
                raise GraphContractError("graph store schema mismatch; migrate explicitly")
            persisted_generation = persisted.get("generation")
            if type(persisted_generation) is not int or persisted_generation < 0:
                raise GraphContractError("persisted graph generation is malformed")
        if persisted_generation != base_generation:
            raise GraphGenerationConflict(
                f"persisted store generation moved to {persisted_generation}; re-read and retry")
        if self._source_digest != persisted_digest:
            raise GraphGenerationConflict("graph bytes changed since snapshot; reopen before commit")
        if base_generation != self._generation:
            raise GraphGenerationConflict(
                f"store generation moved to {self._generation}; re-read and retry")
        for records, validator in ((put_nodes or [], validate_node), (put_edges or [], validate_edge)):
            seen = set()
            for record in records:
                validator(record)
                if record["id"] in seen:
                    raise GraphContractError("duplicate graph transaction record ID")
                seen.add(record["id"])
        nodes, edges = copy.deepcopy(self._nodes), copy.deepcopy(self._edges)
        for node_id in drop_node_ids or []:
            nodes.pop(node_id, None)
            edges = {eid: edge for eid, edge in edges.items()
                     if edge["src"] != node_id and edge["dst"] != node_id}
        for edge_id in drop_edge_ids or []:
            edges.pop(edge_id, None)
        for node in put_nodes or []:
            validate_node(node)
            nodes[node["id"]] = copy.deepcopy(node)
        for edge in put_edges or []:
            validate_edge(edge)
            edges[edge["id"]] = copy.deepcopy(edge)
        candidate = GraphStore.__new__(GraphStore)
        candidate.path, candidate._nodes, candidate._edges = self.path, nodes, edges
        candidate.identity = self.identity
        candidate._generation = base_generation + 1
        candidate.validate()
        payload = self._serialize(candidate._nodes, candidate._edges, candidate._generation, candidate.identity)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="graph-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            sync_directory(self.path.parent)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        self._nodes, self._edges, self._generation = nodes, edges, candidate._generation
        self._source_digest = hashlib.sha256(payload).hexdigest()
        return self._generation

    @staticmethod
    def _serialize(nodes: dict, edges: dict, generation: int, identity=None) -> bytes:
        document = {"schema_version": GRAPH_SCHEMA_VERSION, "generation": generation,
                    "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
                    "edges": sorted(edges.values(), key=lambda e: e["id"])}
        if identity is not None:
            document["identity"] = dict(identity.__dict__)
        return (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1, allow_nan=False) + "\n").encode("utf-8")

    def export_canonical(self) -> bytes:
        """Deterministic canonical bytes for parity checks and inspection."""
        return self._serialize(self._nodes, self._edges, self._generation, self.identity)

    def document(self) -> dict[str, Any]:
        return json.loads(self.export_canonical())
