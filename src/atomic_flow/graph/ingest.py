"""External Git hints plus admitted content audit; one generation-CAS publish.

This external reconciliation boundary scans bytes and audits clean-build parity.
Owned edits use the separate delta fast path. Events never grant read authority.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from atomic_flow.graph.adapter_resource import resource_kind
from atomic_flow.graph.delta import apply_delta, file_delta
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore


def _git(root: Path, *args: str) -> bytes:
    engine = shutil.which("git")
    if engine is None:
        raise GraphContractError("Git is unavailable for external-change ingestion")
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run([engine, "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
                            cwd=root, env=env, capture_output=True, timeout=120, check=False)
    if result.returncode:
        raise GraphContractError("Git root/status unavailable")
    return result.stdout


def changed_paths(root: Path, *, suffix: str = ".py", files=None) -> dict[str, bytes | None]:
    """Both ends of renames (delete/add) and nested untracked files; admitted bytes only."""
    raw = _git(root, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    files = project_files(root, suffix=suffix) if files is None else files
    paths = [os.fsdecode(entry[3:]) for entry in raw.split(b"\0") if entry]
    return {p: files.get(p) for p in paths if p.endswith(suffix)
            and (p in files or not (root / p).exists())}


def _structure(document):
    return (sorted(document["nodes"], key=lambda n: n["id"]),
            sorted(document["edges"], key=lambda e: e["id"]))


def _admitted_graph_files(root, cancel_check):
    files = project_files(root, suffix=None, cancel_check=cancel_check)
    return {path: raw for path, raw in files.items()
            if path.endswith((".py", ".pyi")) or resource_kind(path)}


def _git_move_hints(root):
    entries = _git(root, "status", "--porcelain=v1", "-z", "--find-renames", "--untracked-files=all").split(b"\0")
    moves, index = {}, 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if b"R" in entry[:2]:
            if index >= len(entries) or not entries[index]:
                raise GraphContractError("malformed Git rename hint")
            moves[os.fsdecode(entries[index])] = os.fsdecode(entry[3:])
            index += 1
    return moves


def ingest_git_changes(store: GraphStore, root: Path, *, cancel_check=lambda: None) -> dict:
    root = Path(root).absolute()
    if not (root / ".git").exists():
        raise GraphContractError("ingestion requires a Git root; non-Git projects use explicit rebuilds")
    return ingest_source_changes(store, root, use_git=True, cancel_check=cancel_check)


def ingest_source_changes(store: GraphStore, root: Path, *, use_git=False, event=None,
                          reason="external source reconciliation", cancel_check=lambda: None, moves=None) -> dict:
    """Reconcile arbitrary external writers; watcher and Git paths remain hints.

    A full admitted content audit is intentional at this trust boundary. It
    catches dropped events, same-size edits and clean Git branch switches.
    """
    root = Path(root).absolute()
    if not isinstance(reason, str) or not reason.strip():
        raise GraphContractError("external reconciliation requires a reason")
    cancel_check()
    if use_git:
        _git(root, "rev-parse", "--is-inside-work-tree")
    files = _admitted_graph_files(root, cancel_check)
    hints = changed_paths(root, files=files) if use_git else {p: None for p in getattr(event, "paths", ())}
    old = {n["path"]: n["source"]["digest"] for n in store.nodes() if n["kind"] == "file"}
    before_document = store.document()
    if moves is None and use_git:
        moves = {a: b for a, b in _git_move_hints(root).items() if a in old and a not in files and b in files and b not in old}
    changed = {p: files.get(p) for p in old.keys() | files.keys()
               if old.get(p) != (hashlib.sha256(files[p]).hexdigest() if p in files else None)}
    if not changed:
        return {"deltas": 0, "unchanged": True, "reason": reason,
                "scanned_files": len(files), "parsed_files": 0, "audit_parsed_files": 0}
    reconciliation_reason = reason
    base, reason, reports = store.generation, "", []
    with tempfile.TemporaryDirectory(prefix="atomic-graph-ingest-") as temp:
        staged = GraphStore(Path(temp) / "graph.json")
        staged.commit(base_generation=0, put_nodes=store.nodes(), put_edges=store.edges())
        try:
            for path in sorted(changed):
                cancel_check()
                reports.append(apply_delta(staged, file_delta(path, changed[path], expected_old_digest=old.get(path))))
        except GraphContractError:
            reason = "external ingestion delta contract failure"
        cancel_check()
        expected, candidate = clean_build(files), staged.document()
        if reason or _structure(candidate) != _structure(expected):
            reason = reason or "external ingestion parity mismatch"
            candidate = expected
        from atomic_flow.graph.relocation import relocation_report
        relocation = relocation_report(before_document, candidate, moves)
        if _admitted_graph_files(root, cancel_check) != files:
            raise GraphContractError("source changed during external reconciliation")
        cancel_check()
        store.commit(base_generation=base, drop_node_ids=[n["id"] for n in store.nodes()],
                     put_nodes=candidate["nodes"], put_edges=candidate["edges"])
    return {"deltas": len(changed), "fallback": bool(reason), "reason": reason,
            "relocation": relocation,
            "reconciliation_reason": reconciliation_reason,
            "git_hints": len(hints), "scanned_files": len(files),
            "parsed_files": sum(r["parsed_files"] for r in reports),
            "audit_parsed_files": expected["parsed_files"]}
