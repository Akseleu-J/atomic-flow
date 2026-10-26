"""Static module bindings and monotone re-export closure over extracted facts.

Dynamic imports, dynamic __all__, star ambiguity and local shadowing never
invent a resolved target. Resolution uses stored facts, not source execution.
"""
from __future__ import annotations

from typing import Any

from atomic_flow.graph.schema import edge_id

RESOLVER = "name-resolver/2"


class ExportIndex(dict):
    """Transient binding index with explicit star-import visibility."""

    def __init__(self, values, files):
        super().__init__(values)
        self.star_names = {path: (set(node["facts"].get("module_exports") or [])
                                 if node["facts"].get("module_exports") is not None
                                 else {name for name in self[path] if not name.startswith("_")})
                           for path, node in files.items()}
        self.dynamic = {path for path, node in files.items() if node["facts"].get("dynamic_exports")}


def module_map(paths: list[str]) -> dict[str, str]:
    mapping = {}
    for path in sorted(paths):
        if path.endswith(".py"):
            name = path[:-3].replace("/", ".")
            if name.endswith(".__init__"):
                name = name[:-9]
            elif name == "__init__":
                name = ""
            if name not in mapping or path.endswith("/__init__.py"):
                mapping[name] = path
    return mapping


def import_module(path, binding):
    module = binding.get("module", "")
    level = binding.get("level", 0)
    if not level:
        return module
    parts = path.split("/")[:-1]
    if level > len(parts):
        return None
    prefix = parts[:len(parts) - level + 1]
    return ".".join([*prefix, *([module] if module else [])])


def _bindings(node):
    facts = node["facts"]
    if "import_bindings" in facts:
        return facts["import_bindings"]
    result = []
    for name in facts.get("imports", []):
        module, _, leaf = name.rpartition(".")
        result.append({"module": module or name, "name": leaf if module else "",
                       "local": leaf or name, "level": 0, "star": False})
    return result


def _ambiguous_bindings(node):
    facts = node["facts"]
    imported = {binding["local"] for binding in _bindings(node) if not binding.get("star")}
    return set(facts.get("assigned_names", [])) | (imported & set(facts.get("top_level_names", [])))


def exports_by_file(nodes: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    files = {n["path"]: n for n in nodes if n["kind"] == "file"}
    modules = module_map(list(files))
    exports = {p: {} for p in files}
    for node in nodes:
        if (node["kind"] in {"class", "function"} and "." not in node["symbol"]
                and node["symbol"] not in _ambiguous_bindings(files[node["path"]])):
            exports[node["path"]].setdefault(node["symbol"], set()).add(node["id"])
    # Monotone finite symbol propagation reaches a fixed point for import cycles.
    for _ in range(max(1, len(files) + 1)):
        changed = False
        for path, node in sorted(files.items()):
            facts = node["facts"]
            explicit = set(facts.get("top_level_names", [])) | set(facts.get("assigned_names", []))
            bindings = _bindings(node)
            explicit.update(b["local"] for b in bindings if not b.get("star"))
            for b in bindings:
                if not b.get("star") and b["local"] in _ambiguous_bindings(node):
                    continue
                target = modules.get(import_module(path, b))
                if not target or target == path or files[target]["facts"].get("dynamic_exports"):
                    continue
                candidates = exports[target]
                if b.get("star"):
                    allow = files[target]["facts"].get("module_exports")
                    names = allow if allow is not None else [n for n in candidates if not n.startswith("_")]
                    pairs = [(n, candidates.get(n, set())) for n in names if n not in explicit]
                elif b.get("name"):
                    pairs = [(b["local"], candidates.get(b["name"], set()))]
                else:
                    pairs = []
                for name, ids in pairs:
                    old = exports[path].setdefault(name, set())
                    before = len(old)
                    old.update(ids)
                    changed |= len(old) != before
        if not changed:
            break
    return ExportIndex({p: {name: sorted(ids) for name, ids in sorted(names.items()) if ids}
                        for p, names in exports.items()}, files)


def dependencies(file_node, modules):
    """Potential module dependencies, including currently unresolved imports."""
    names, targets = set(), set()
    for binding in _bindings(file_node):
        module = import_module(file_node["path"], binding)
        if module is None:
            continue
        names.add(module)
        if module in modules:
            targets.add(modules[module])
        if binding.get("name") and not binding.get("star"):
            submodule = (module + "." if module else "") + binding["name"]
            names.add(submodule)
            if submodule in modules:
                targets.add(modules[submodule])
    return names, targets - {file_node["path"]}


def infer_file_edges(file_node: dict[str, Any], *, modules: dict[str, str],
                     exports: dict[str, dict[str, list[str]]]) -> dict[str, Any]:
    path, facts = file_node["path"], file_node["facts"]
    edges, unresolved = {}, []

    def edge(kind, target):
        if target != file_node["id"]:
            item = {"id": edge_id(file_node["id"], kind, target), "kind": kind,
                    "src": file_node["id"], "dst": target, "status": "active",
                    "facts": {"resolver": RESOLVER}, "fact_status": {"resolver": "fresh"},
                    "facts_class": "inferred"}
            edges[item["id"]] = item

    local, module_locals, stars = {}, {}, []
    for b in _bindings(file_node):
        module = import_module(path, b)
        target = modules.get(module)
        if b.get("name") and not b.get("star"):
            submodule = (module + "." if module else "") + b["name"]
            subtarget = modules.get(submodule)
            if subtarget:
                module_locals[b["local"]] = subtarget
                edge("imports", "file:" + subtarget)
        if not target:
            unresolved.append(f"import {module or b.get('module', '')} (unresolved)")
            continue
        edge("imports", "file:" + target)
        if b.get("star"):
            stars.append(target)
        elif b.get("name"):
            local[b["local"]] = exports.get(target, {}).get(b["name"], [])
        else:
            module_locals[b["local"]] = target
    shadowed = set(facts.get("shadowed_call_names", [])) | _ambiguous_bindings(file_node)
    for call in facts.get("call_targets", facts.get("raw_calls", [])):
        head, _, tail = call.partition(".")
        ids = []
        if head not in shadowed:
            if tail and head in module_locals:
                target = module_locals[head]
                ids = exports.get(target, {}).get(tail, [])
                if not ids:
                    target = modules.get(call.rpartition(".")[0])
                    ids = exports.get(target, {}).get(call.rsplit(".", 1)[-1], [])
            elif not tail:
                ids = local.get(call, []) or exports.get(path, {}).get(call, [])
                if not ids:
                    ids = [i for target in stars
                           if target not in getattr(exports, "dynamic", set())
                           and call in getattr(exports, "star_names", {}).get(target, set(exports.get(target, {})))
                           for i in exports.get(target, {}).get(call, [])]
        ids = sorted(set(ids))
        if len(ids) == 1:
            edge("calls", ids[0])
        elif len(ids) > 1:
            unresolved.append(f"call {call} (ambiguous: {len(ids)} candidates)")
        else:
            unresolved.append(f"call {call} (no imported exporter or shadowed/dynamic binding)")
    if facts.get("dynamic_exports"):
        unresolved.append("dynamic __all__: exports not statically complete")
    return {"edges": sorted(edges.values(), key=lambda e: e["id"]), "unresolved": sorted(set(unresolved))}
