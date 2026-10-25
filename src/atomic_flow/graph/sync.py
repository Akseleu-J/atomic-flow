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

from atomic_flow.graph.delta import apply_delta, fallback_rebuild, file_delta
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore


def store_path(project: Path) -> Path:
    return project / ".atomic_flow" / "graph.json"


def sync_project_graph(project: Path, changed: dict[str, Any], *, cancel_check=lambda: None) -> dict[str, Any]:
    """Apply per-file deltas for `changed` ({path: new bytes | None=deleted}).

    Never raises for graph-internal problems: a failed sync degrades to a
    reasoned fallback rebuild, and a failed fallback is logged to
    `.atomic_flow/graph-sync.jsonl`. Source publication is already done.
    """
    project = Path(project)
    target = store_path(project)
    log = target.parent / "graph-sync.jsonl"
    started = time.monotonic()
    try:
        cancel_check()
        if not target.exists():
            files = project_files(project)
            clean = clean_build(files)
            store = GraphStore(target)
            store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
            result = {"created": True, "parsed_files": clean["parsed_files"], "deltas": 0}
        else:
            store = GraphStore(target)
            reports = []
            for path in sorted(changed):
                cancel_check()
                new_bytes = changed[path]
                current = next((n["source"]["digest"] for n in store.nodes()
                                if n["kind"] == "file" and n["path"] == path), "")
                reports.append(apply_delta(store, file_delta(path, new_bytes,
                                                             expected_old_digest=current)))
            result = {"created": False, "deltas": len(reports),
                      "parsed_files": sum(r["parsed_files"] for r in reports),
                      "relinked_files": max((r["relinked_files"] for r in reports), default=0)}
        result["seconds"] = round(time.monotonic() - started, 4)
        _append(log, {**result, "changed": sorted(changed), "status": "ok"})
        return result
    except (GraphContractError, OSError, ValueError) as exc:
        reason = f"delta sync failed: {type(exc).__name__}"
        try:
            cancel_check()
            files = project_files(project)
            store = GraphStore(target) if target.exists() else GraphStore(target)
            rebuild = fallback_rebuild(store, files, reason)
            _append(log, {"status": "fallback", "reason": reason,
                          "parsed_files": rebuild["parsed_files"],
                          "seconds": round(time.monotonic() - started, 4)})
            return {"created": False, "deltas": 0, "fallback": True, "reason": reason}
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
