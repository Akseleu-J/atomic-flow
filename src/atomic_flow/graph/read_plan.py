"""Ephemeral incremental structural projection for advisory read planning.

This is a query index, not another durable graph store or a source permission.
Only changed file bytes reach the AST adapter. Reverse-name buckets are patched
by file ownership; cancellation or admission failure publishes no partial state.
"""
from __future__ import annotations

import hashlib
import json

from atomic_flow.graph.adapter_ast import EXTRACTOR
from atomic_flow.graph.projection import PROJECTION_POLICY, coverage_from_file_nodes, project_file_projection
from atomic_flow.graph.schema import GraphContractError

# Preserve the existing instrumentation patch point, with the shared project
# projection applying its unchanged strict parser or explicit file placeholder.
adapt_file = project_file_projection


class ContextPackIndex:
    def __init__(self):
        self.files: dict[str, dict] = {}
        self.reverse: dict[str, dict[str, frozenset]] = {"imports": {}, "calls": {}}
        self.exports: dict[str, frozenset] = {}
        self.revision = ""
        self.node_count = 0
        self.stats: dict[str, int] = {}
        self.projection_policy = ""
        self.coverage = {"coverage_complete": True, "coverage_omissions": []}

    def update(self, files: dict[str, bytes], *, cancel_check=lambda: None, max_nodes=100_000, adapter=None):
        if type(max_nodes) is not int or max_nodes < 1:
            raise ValueError("read-plan node limit must be positive")
        cancel_check()
        changed, pending, deleted = {}, {}, set(self.files) - set(files)
        for path in sorted(files):
            cancel_check()
            data = files[path]
            if not isinstance(data, bytes):
                raise GraphContractError("read-plan source must be bytes")
            digest = hashlib.sha256(data).hexdigest()
            old = self.files.get(path)
            if old is not None and old["digest"] == digest and self.projection_policy == PROJECTION_POLICY:
                continue
            pending[path] = (data, digest)
        node_count = self.node_count - sum(len(self.files[p]["nodes"]) for p in deleted | pending.keys() if p in self.files)
        for path, (data, digest) in pending.items():
            cancel_check()
            payload = (adapter or adapt_file)(path, data)
            cancel_check()
            node_count += len(payload["nodes"])
            if node_count > max_nodes:
                raise GraphContractError("read-plan node limit exceeded")
            changed[path] = {"digest": digest, "bytes": len(data), "nodes": payload["nodes"]}
        if node_count > max_nodes:
            raise GraphContractError("read-plan node limit exceeded")
        records = dict(self.files)
        reverse = {kind: dict(buckets) for kind, buckets in self.reverse.items()}
        exports = dict(self.exports)

        def patch(mapping, key, value, add):
            values = set(mapping.get(key, ()))
            if add:
                values.add(value)
            else:
                values.discard(value)
            if values:
                mapping[key] = frozenset(values)
            else:
                mapping.pop(key, None)

        def update_record(path, record, add):
            facts = record["nodes"][0]["facts"]
            for kind, field in (("imports", "imports"), ("calls", "raw_calls")):
                for name in facts[field]:
                    patch(reverse[kind], name, path, add)
            for node in record["nodes"][1:]:
                patch(exports, node["symbol"].rsplit(".", 1)[-1], node["id"], add)

        for path in sorted(deleted | changed.keys()):
            cancel_check()
            if path in records:
                update_record(path, records.pop(path), False)
            if path in changed:
                records[path] = changed[path]
                update_record(path, changed[path], True)
        coverage = coverage_from_file_nodes(node for record in records.values() for node in record["nodes"])
        revision = hashlib.sha256(json.dumps({"extractor": EXTRACTOR, "projection_policy": PROJECTION_POLICY,
            "files": {p: records[p]["digest"] for p in sorted(records)}}, sort_keys=True).encode()).hexdigest()
        cancel_check()
        self.files, self.reverse, self.exports = records, reverse, exports
        self.revision, self.node_count = revision, node_count
        self.projection_policy, self.coverage = PROJECTION_POLICY, coverage
        self.stats = {"parsed_files": len(changed), "reused_files": len(files) - len(changed),
                      "deleted_files": len(deleted), "scanned_files": len(files)}
        return dict(self.stats)
