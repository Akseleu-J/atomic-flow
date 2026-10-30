"""Explicit revision-to-revision relocation reports; never rename authority."""
from __future__ import annotations

from collections import defaultdict

from atomic_flow.graph.schema import GraphContractError, file_id


def owned_move_hints(previous, calls, before, after):
    """Compose observed, successful moves; never infer them from model arguments."""
    hints = dict(previous)
    for call in calls:
        summary = call.get("result_summary") or {}
        if call.get("tool") != "move_file" or summary.get("status") != "applied":
            continue
        changes = summary.get("changes", [])
        removed = [row for row in changes if row.get("before_hash") and row.get("after_hash") is None]
        added = [row for row in changes if row.get("before_hash") is None and row.get("after_hash")]
        if len(changes) != 2 or len(removed) != 1 or len(added) != 1:
            continue
        left, right = removed[0]["path"], added[0]["path"]
        file_id(left)
        file_id(right)
        if (before.get(left) != removed[0]["before_hash"] or left in after
                or right not in after or right in before
                or removed[0]["before_hash"] != added[0]["after_hash"]):
            continue
        origins = [origin for origin, destination in hints.items() if destination == left]
        if origins:
            for origin in origins:
                hints[origin] = right
        else:
            hints[left] = right
    if len(hints) > 4096:
        raise GraphContractError("owned move hints exceed observation cap")
    return hints


def relocation_report(before, after, moves=None):
    old = {n["path"]: n for n in before["nodes"] if n["kind"] == "file"}
    new = {n["path"]: n for n in after["nodes"] if n["kind"] == "file"}
    removed, added = set(old) - set(new), set(new) - set(old)
    if moves is not None and (not isinstance(moves, dict) or any(
            not isinstance(a, str) or not isinstance(b, str) for a, b in moves.items())):
        raise GraphContractError("move mapping requires exact old/new path pairs")
    pairs, used_old, used_new = [], set(), set()
    for left, right in sorted((moves or {}).items()):
        file_id(left)
        file_id(right)
        if left not in removed or right not in added or right in used_new:
            raise GraphContractError("move mapping does not match removed/added source identities")
        pairs.append({"old_path": left, "new_path": right, "method": "provided_hint"})
        used_old.add(left)
        used_new.add(right)
    groups_old, groups_new = defaultdict(list), defaultdict(list)
    for path in sorted(removed - used_old):
        groups_old[(old[path]["source"]["algorithm"], old[path]["source"]["digest"])].append(path)
    for path in sorted(added - used_new):
        groups_new[(new[path]["source"]["algorithm"], new[path]["source"]["digest"])].append(path)
    ambiguous = []
    for digest in sorted(groups_old.keys() & groups_new.keys()):
        lefts, rights = groups_old[digest], groups_new[digest]
        if digest[0] == "sha256" and len(lefts) == len(rights) == 1:
            left, right = lefts[0], rights[0]
            pairs.append({"old_path": left, "new_path": right, "method": "unique_content_relocation"})
            used_old.add(left)
            used_new.add(right)
        else:
            ambiguous.append({"old_paths": lefts, "new_paths": rights, "reason": "ambiguous_content_identity"})
    mapping = {}
    for pair in pairs:
        left, right = pair["old_path"], pair["new_path"]
        mapping[file_id(left)] = file_id(right)
        old_symbols, new_symbols = defaultdict(list), defaultdict(list)
        for node in before["nodes"]:
            if node["path"] == left and node["kind"] != "file":
                old_symbols[(node["kind"], node.get("symbol"))].append(node["id"])
        for node in after["nodes"]:
            if node["path"] == right and node["kind"] != "file":
                new_symbols[(node["kind"], node.get("symbol"))].append(node["id"])
        for symbol in old_symbols.keys() & new_symbols.keys():
            if len(old_symbols[symbol]) == len(new_symbols[symbol]) == 1:
                mapping[old_symbols[symbol][0]] = new_symbols[symbol][0]
            else:
                ambiguous.append({"old_ids": old_symbols[symbol], "new_ids": new_symbols[symbol],
                                  "reason": "ambiguous_symbol_identity"})
    return {"path_moves": sorted(pairs, key=lambda p: (p["old_path"], p["new_path"])),
            "identity_mapping": mapping, "ambiguous_moves": ambiguous,
            "unmapped_removed": sorted(removed - used_old), "unmapped_added": sorted(added - used_new),
            "authority": "none; mappings are revision observations, not read or write grants"}
