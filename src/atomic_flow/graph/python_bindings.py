"""Static Python binding facts; never import or execute repository code."""
from __future__ import annotations

import ast


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def extract_bindings(tree: ast.Module) -> dict:
    imports, definitions, assigned = [], [], set()
    explicit_all, dynamic_all = None, False
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.append(child.name)
        elif isinstance(child, ast.Import):
            imports.extend({"module": a.name, "name": "", "local": a.asname or a.name.split(".")[0],
                            "level": 0, "star": False, "qualified": not bool(a.asname)} for a in child.names)
        elif isinstance(child, ast.ImportFrom):
            imports.extend({"module": child.module or "", "name": a.name,
                            "local": a.asname or a.name, "level": child.level,
                            "star": a.name == "*", "qualified": False} for a in child.names)
        elif isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            names = {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}
            assigned.update(names)
            if "__all__" in names:
                try:
                    value = ast.literal_eval(child.value)
                    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                        raise ValueError
                    if isinstance(child, ast.AugAssign):
                        if explicit_all is None:
                            raise ValueError
                        value = [*explicit_all, *value]
                    explicit_all = sorted(set(value))
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    dynamic_all = True
    calls = sorted({_dotted(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)} - {""})
    # Function-local rebinding is not resolved by a module-level name index.
    # Mark those names ambiguous rather than inventing a definite call target.
    shadowed = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            shadowed.update(arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg))
            for part in ast.walk(node):
                if isinstance(part, ast.Name) and isinstance(part.ctx, (ast.Store, ast.Del)):
                    shadowed.add(part.id)
    return {"import_bindings": imports, "call_targets": calls, "top_level_names": sorted(set(definitions)),
            "assigned_names": sorted(assigned), "shadowed_call_names": sorted(shadowed),
            "module_exports": explicit_all, "dynamic_exports": dynamic_all}
