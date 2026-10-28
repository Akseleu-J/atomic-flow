"""Context pack contract (roadmap P04 slice): a read plan with reasons.

Given goal files, produce an ordered, byte-capped read plan: goal files first,
then name-level importers/callers from the reverse index, then files exporting
the names the goals mention. Every entry carries the reason it was included —
the agent can show why a file is read. Name matches are advisory, never
resolved-dependency claims. Deterministic, model-free.
"""
from __future__ import annotations

import re
from typing import Any

from atomic_flow.activation.text import raw_terms
from atomic_flow.graph import read_plan
from atomic_flow.graph.project import affected_by_name
from atomic_flow.graph.resolve import module_map

ContextPackIndex = read_plan.ContextPackIndex
# Preserve the public patch point for instrumentation without changing adapters.
adapt_file = read_plan.adapt_file

PACK_EXTRACTOR = "context-pack/2"
DEFAULT_BYTE_CAP = 96 * 1024


def context_pack(files: dict[str, bytes], goals: list[str], *, byte_cap: int = DEFAULT_BYTE_CAP,
                 index: ContextPackIndex | None = None, cancel_check=lambda: None, max_nodes=100_000) -> dict[str, Any]:
    if not goals or any(g not in files for g in goals):
        unknown = [g for g in goals if g not in files]
        raise ValueError(f"unknown goal files: {unknown}")
    if type(byte_cap) is not int or byte_cap < 0:
        raise ValueError("context-pack byte cap must be a non-negative integer")
    index = index if index is not None else ContextPackIndex()
    index.update(files, cancel_check=cancel_check, max_nodes=max_nodes, adapter=adapt_file)
    return context_pack_from_index(index, goals, byte_cap=byte_cap, cancel_check=cancel_check)


def context_pack_from_index(index: ContextPackIndex, goals: list[str], *, byte_cap=DEFAULT_BYTE_CAP,
                             cancel_check=lambda: None) -> dict[str, Any]:
    """Project a reconciled advisory snapshot; this does not validate source bytes."""
    files = index.files
    if not goals or any(goal not in files for goal in goals):
        raise ValueError("unknown goal files in reconciled index")
    if type(byte_cap) is not int or byte_cap < 0:
        raise ValueError("context-pack byte cap must be a non-negative integer")
    order: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str, reason: str) -> None:
        if path not in seen:
            seen.add(path)
            order.append({"file": path, "reason": reason, "bytes": files[path]["bytes"]})

    for goal in sorted(goals):
        cancel_check()
        add(goal, "goal file")
    goal_names: set[str] = set()
    for goal in goals:
        cancel_check()
        module = goal_module(goal)
        goal_names.add(module)
        for node in index.files[goal]["nodes"]:
            if node["kind"] in {"class", "function"} and node["path"] == goal:
                leaf = node["symbol"].rsplit(".", 1)[-1]
                goal_names.add(leaf)
                goal_names.add(f"{module}.{leaf}")
        facts = index.files[goal]["nodes"][0]["facts"]
        goal_names.update(facts["imports"])
        goal_names.update(facts["raw_calls"])
        for imported, paths in index.reverse["imports"].items():
            if imported == module or imported.startswith(module + "."):
                goal_names.update(paths and [imported])
    for name in sorted(goal_names):
        cancel_check()
        for path in affected_by_name(index.reverse, [name]):
            add(path, f"name match: {name}")
    modules = module_map(sorted(files))
    for goal in sorted(goals):
        cancel_check()
        for imported in index.files[goal]["nodes"][0]["facts"]["imports"]:
            parts = imported.split(".")
            for size in range(len(parts), 0, -1):
                candidate = modules.get(".".join(parts[:size]))
                if candidate is not None:
                    add(candidate, f"import name match: {imported}")
                    break
    for name in sorted(goal_names & set(index.exports)):
        cancel_check()
        for node_id in sorted(index.exports[name]):
            add(node_id[len("sym:"):].split("::")[0], f"defines: {name}")
    capped, total = [], 0
    for entry in order:
        cancel_check()
        if total + entry["bytes"] > byte_cap:
            entry = {**entry, "truncated": True, "reason": entry["reason"] + "; over byte cap"}
            capped.append(entry)
            continue
        total += entry["bytes"]
        capped.append(entry)
    omissions = [{"path": entry["file"], "reason": "context_byte_cap"} for entry in capped if entry.get("truncated")]
    return {"extractor": PACK_EXTRACTOR, "goals": sorted(goals), "entries": capped,
            "included_bytes": total, "byte_cap": byte_cap,
            "estimated_token_cap": byte_cap, "omissions": omissions, "coverage_complete": not omissions,
            "read_proof": False, "runtime_coverage": "not_assessed",
            "revision": index.revision, "stats": dict(index.stats),
            "parse_failures": sorted(p for p, record in index.files.items() if not record["nodes"][0]["facts"]["parse_ok"]),
            "method": "name-level advisory; not a resolution claim"}


def select_goals(index: ContextPackIndex, allowed_paths, description: str, *, limit=4):
    """Choose admitted approved targets, with explicit paths before lexical names."""
    query = description.replace("\\", "/")
    words = raw_terms(query)
    ranked = []
    for path in sorted(set(allowed_paths) & index.files.keys()):
        explicit = bool(re.search(r"(?<![\w./-])" + re.escape(path) + r"(?![\w./-])", query))
        score = 1000 if explicit else 0
        stem = path.rsplit("/", 1)[-1].removesuffix(".py").lower()
        if stem in words:
            score += 20
        for node in index.files[path]["nodes"][1:]:
            leaf = node["symbol"].rsplit(".", 1)[-1]
            if leaf.lower() in words:
                score += 50
            score += len(raw_terms(leaf) & words)
        if score:
            ranked.append((not explicit, -score, path))
    ranked.sort()
    if not ranked:
        return [], 0
    return [path for _, _, path in ranked[:limit]], max(0, len(ranked) - limit)


def goal_module(goal: str) -> str:
    return goal[:-len(".py")].replace("/", ".") if goal.endswith(".py") else goal.replace("/", ".")


def verification_plan(files: dict[str, bytes], changed_paths: list[str]) -> dict:
    """Advisory targeted checks; unresolved/config/deletion always broadens final gate.

    This does not execute tests or mark requirements verified. Every plan has a
    final full-regression obligation even when a narrow target can run in-loop.
    """
    from atomic_flow.graph.adapter_resource import resource_kind
    from atomic_flow.graph.project import clean_build
    from atomic_flow.graph.semantic import file_dependencies
    # Graph adapters admit at most 1MB per file; the source workspace has a
    # larger independent ceiling. An oversized advisory input is an explicit
    # coverage omission, never permission to omit the full final test gate.
    oversized = sorted(p for p, raw in files.items()
                       if (p.endswith((".py", ".pyi")) or resource_kind(p)) and len(raw) > 1_000_000)
    supported = {p: raw for p, raw in files.items()
                 if (p.endswith((".py", ".pyi")) or resource_kind(p)) and len(raw) <= 1_000_000}
    omitted = sorted(set(files) - set(supported))
    build = clean_build(supported)
    deps = file_dependencies(build)
    affected = set(changed_paths)
    while True:
        expanded = affected | {path for path, dependencies in deps.items() if set(dependencies) & affected}
        if expanded == affected:
            break
        affected = expanded
    targets = sorted(p for p in files if p.endswith(".py") and
                     (p in affected and (p.startswith("tests/") or p.rsplit("/", 1)[-1].startswith("test_"))))
    for path in changed_paths:
        stem = path.rsplit("/", 1)[-1].removesuffix(".py")
        targets.extend(p for p in files if p.endswith(".py") and p.rsplit("/", 1)[-1] == "test_" + stem + ".py")
    unknown = bool(omitted or build["unresolved"] or any(p not in files or not p.endswith((".py", ".pyi")) for p in changed_paths))
    return {"schema_version": 1, "changed_paths": sorted(set(changed_paths)), "affected_paths": sorted(affected),
            "targeted_tests": sorted(set(targets)), "impact_status": "unknown" if unknown else "static_inferred",
            "unresolved": build["unresolved"], "runtime_coverage": "not_assessed",
            "omitted_unsupported": omitted,
            "omitted_oversized": oversized,
            "loop_check": "full_pytest" if unknown or not targets else "targeted_pytest",
            "required_final_check": "full_pytest", "checks_executed": False,
            "source_digests": {p: __import__("hashlib").sha256(raw).hexdigest() for p, raw in sorted(files.items())}}
