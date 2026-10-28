"""Project graph synchronization on the publication hot path (P06/P02).

After a changeset publishes, apply one file delta per changed path to the
project-level graph store instead of a full graph rebuild. First use builds
the store from current sources. Content drift falls back to an explicit
reasoned rebuild; sync failures never roll back the already-published source
— they are recorded in a sidecar log beside the store.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from atomic_flow.graph.adapter_ast import EXTRACTOR
from atomic_flow.graph.adapter_resource import RESOURCE_EXTRACTOR, resource_kind
from atomic_flow.graph.delta import apply_changes, fallback_rebuild
from atomic_flow.graph.project import clean_build, project_files, project_paths
from atomic_flow.graph.projection import coverage_from_file_nodes, valid_file_extractor
from atomic_flow.graph.resolve import RESOLVER
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore


def store_path(project: Path) -> Path:
    return project / ".atomic_flow" / "graph.json"


def _graph_files(project, cancel_check):
    return {p: raw for p, raw in project_files(project, suffix=None, cancel_check=cancel_check).items()
            if p.endswith((".py", ".pyi")) or resource_kind(p)}


def sync_project_graph(project: Path, changed: dict[str, Any], *, cancel_check=lambda: None, moves=None) -> dict[str, Any]:
    """Apply per-file deltas for `changed` ({path: new bytes | None=deleted}).

    Never raises for graph-internal problems: a failed sync degrades to a
    reasoned fallback rebuild, and a failed fallback is logged to
    `.atomic_flow/graph-sync.jsonl`. Source publication is already done.
    """
    project = Path(project)
    target = store_path(project)
    log = target.parent / "graph-sync.jsonl"
    started = time.monotonic()
    def validate_snapshot(files):
        if _graph_files(project, cancel_check) != files:
            raise GraphContractError("published source drifted during graph synchronization")
    try:
        cancel_check()
        if not target.exists():
            files = _graph_files(project, cancel_check)
            clean = clean_build(files)
            store = GraphStore(target)
            validate_snapshot(files)
            cancel_check()
            store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
            result = {"created": True, "parsed_files": clean["parsed_files"], "deltas": 0}
        else:
            store = GraphStore(target)
            if any(not valid_file_extractor(node,
                    EXTRACTOR if node["path"].endswith((".py", ".pyi")) else RESOURCE_EXTRACTOR)
                   for node in store.nodes() if node["kind"] == "file"):
                raise GraphContractError("parser/extractor version changed")
            if any(edge.get("facts_class") == "inferred" and edge["facts"].get("resolver") != RESOLVER
                   for edge in store.edges()):
                raise GraphContractError("resolver version changed")
            def validate_source():
                if project_paths(project, changed, cancel_check=cancel_check) != changed:
                    raise GraphContractError("published source drifted during graph synchronization")
            result = {"created": False, **apply_changes(store, changed, cancel_check=cancel_check,
                                                        source_check=validate_source, moves=moves)}
        result.update(coverage_from_file_nodes(store.nodes()))
        result["seconds"] = round(time.monotonic() - started, 4)
        _append(log, {**result, "changed": sorted(changed), "status": "ok"})
        return result
    except (GraphContractError, OSError, ValueError) as exc:
        reason = (str(exc) if str(exc) in {"parser/extractor version changed", "resolver version changed"}
                  else f"delta sync failed: {type(exc).__name__}")
        try:
            cancel_check()
            files = _graph_files(project, cancel_check)
            store = GraphStore(target)
            rebuild = fallback_rebuild(store, files, reason,
                                       source_check=lambda: validate_snapshot(files), cancel_check=cancel_check)
            coverage = coverage_from_file_nodes(store.nodes())
            _append(log, {"status": "fallback", "reason": reason, **coverage,
                          "parsed_files": rebuild["parsed_files"],
                          "seconds": round(time.monotonic() - started, 4)})
            return {"created": False, "deltas": 0, "fallback": True, "reason": reason, **coverage}
        except BaseException as inner:  # never break publication
            _append(log, {"status": "failed", "reason": reason,
                          "fallback_error_type": type(inner).__name__})
            return {"created": False, "deltas": 0, "failed": True, "reason": reason}


def _append(log: Path, record: dict[str, Any]) -> None:
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # the sidecar log is best-effort diagnostics
