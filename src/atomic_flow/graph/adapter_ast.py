"""Python AST adapter: source bytes -> structural graph facts (roadmap P01).

Extracted facts only (no inference, no resolution). Raw import/call names are
retained as facts for P02 reverse indexes; edges emitted here are strictly
`contains` (file -> symbol, class -> method). Call/import edges require the
resolver and belong to the delta stage. Duplicate qualified names get a `#N`
disambiguator exactly like the legacy tree, never a silent merge.
"""
from __future__ import annotations

import ast
import hashlib
from typing import Any

from atomic_flow.graph.schema import edge_id, file_id, symbol_id

EXTRACTOR = "python-ast/1"


def _qualified(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def adapt_file(path: str, source: bytes) -> dict[str, Any]:
    """Build nodes/edges for one Python file; parser failure is reported, not raised."""
    digest = hashlib.sha256(source).hexdigest()
    file_node = {"id": file_id(path), "kind": "file", "path": path,
                 "source": {"digest": digest, "algorithm": "sha256"},
                 "facts": {"extractor": EXTRACTOR, "parse_ok": True,
                           "imports": [], "raw_calls": []},
                 "fact_status": {name: "fresh" for name in
                                 ("extractor", "parse_ok", "imports", "raw_calls")},
                 "facts_class": "extracted"}
    nodes: list[dict] = [file_node]
    edges: list[dict] = []
    seen: dict[str, int] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        file_node["facts"]["parse_ok"] = False
        file_node["facts"]["parse_error"] = f"line {exc.lineno}: {exc.msg}"
        file_node["fact_status"]["parse_error"] = "fresh"
        return {"nodes": nodes, "edges": edges}

    def walk(node: ast.AST, parent_symbol: str, parent_id: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = _qualified(parent_symbol, child.name)
                seen[qualified] = seen.get(qualified, 0) + 1
                node_identifier = symbol_id(path, qualified, seen[qualified])
                facts: dict[str, Any] = {"extractor": EXTRACTOR,
                                         "start_line": child.lineno,
                                         "end_line": getattr(child, "end_lineno", None) or child.lineno}
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    facts["signature"] = f"def {child.name}({', '.join(a.arg for a in child.args.args)})"
                else:
                    facts["signature"] = f"class {child.name}"
                symbol_node = {"id": node_identifier, "kind": "class" if isinstance(child, ast.ClassDef) else "function",
                               "path": path, "symbol": qualified,
                               "source": {"digest": digest, "algorithm": "sha256"},
                               "start_line": facts["start_line"], "end_line": facts["end_line"],
                               "facts": facts,
                               "fact_status": {name: "fresh" for name in facts},
                               "facts_class": "extracted"}
                nodes.append(symbol_node)
                edges.append({"id": edge_id(parent_id, "contains", node_identifier),
                              "kind": "contains", "src": parent_id, "dst": node_identifier,
                              "status": "active", "facts": {"extractor": EXTRACTOR},
                              "fact_status": {"extractor": "fresh"}, "facts_class": "extracted"})
                walk(child, qualified, node_identifier)
            elif isinstance(child, ast.Import):
                file_node["facts"]["imports"].extend(alias.name for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                prefix = child.module or ""
                file_node["facts"]["imports"].extend(
                    f"{prefix}.{alias.name}" if prefix else alias.name for alias in child.names)
            elif isinstance(child, ast.Call):
                target = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
                if target:
                    file_node["facts"]["raw_calls"].append(target)
                walk(child, parent_symbol, parent_id)
            else:
                walk(child, parent_symbol, parent_id)

    walk(tree, "", file_node["id"])
    file_node["facts"]["imports"] = sorted(set(file_node["facts"]["imports"]))
    file_node["facts"]["raw_calls"] = sorted(set(file_node["facts"]["raw_calls"]))
    return {"nodes": nodes, "edges": edges}
