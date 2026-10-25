"""Context pack contract (roadmap P04 slice): a read plan with reasons.

Given goal files, produce an ordered, byte-capped read plan: goal files first,
then name-level importers/callers from the reverse index, then files exporting
the names the goals mention. Every entry carries the reason it was included —
the agent can show why a file is read. Name matches are advisory, never
resolved-dependency claims. Deterministic, model-free.
"""
from __future__ import annotations

from typing import Any

from atomic_flow.graph.project import affected_by_name, clean_build

PACK_EXTRACTOR = "context-pack/1"
DEFAULT_BYTE_CAP = 96 * 1024


def context_pack(files: dict[str, bytes], goals: list[str], *, byte_cap: int = DEFAULT_BYTE_CAP) -> dict[str, Any]:
    if not goals or any(g not in files for g in goals):
        unknown = [g for g in goals if g not in files]
        raise ValueError(f"unknown goal files: {unknown}")
    build = clean_build(files)
    order: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str, reason: str) -> None:
        if path not in seen:
            seen.add(path)
            order.append({"file": path, "reason": reason, "bytes": len(files[path])})

    for goal in sorted(goals):
        add(goal, "goal file")
    goal_names: set[str] = set()
    for goal in goals:
        module = goal_module(goal)
        goal_names.add(module)
        for node in build["nodes"]:
            if node["kind"] in {"class", "function"} and node["path"] == goal:
                leaf = node["symbol"].rsplit(".", 1)[-1]
                goal_names.add(leaf)
                goal_names.add(f"{module}.{leaf}")
        for imported, paths in build["reverse"]["imports"].items():
            if imported == module or imported.startswith(module + "."):
                goal_names.update(paths and [imported])
    for name in sorted(goal_names):
        for path in affected_by_name(build["reverse"], [name]):
            add(path, f"name match: {name}")
    for name in sorted(goal_names & set(build["exports"])):
        for node_id in build["exports"][name]:
            add(node_id[len("sym:"):].split("::")[0], f"defines: {name}")
    capped, total = [], 0
    for entry in order:
        if total + entry["bytes"] > byte_cap:
            entry = {**entry, "truncated": True, "reason": entry["reason"] + "; over byte cap"}
            capped.append(entry)
            continue
        total += entry["bytes"]
        capped.append(entry)
    return {"extractor": PACK_EXTRACTOR, "goals": sorted(goals), "entries": capped,
            "included_bytes": total, "byte_cap": byte_cap,
            "method": "name-level advisory; not a resolution claim"}


def goal_module(goal: str) -> str:
    return goal[:-len(".py")].replace("/", ".") if goal.endswith(".py") else goal.replace("/", ".")
