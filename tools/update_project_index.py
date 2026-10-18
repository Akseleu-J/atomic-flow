"""Offline repository inventory; parses source without importing application/fixture code."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    tracked = set(filter(None, paths))
    new_python = sorted(path.relative_to(ROOT).as_posix()
                        for base in (ROOT / "src", ROOT / "tests") for path in base.rglob("*.py")
                        if path.relative_to(ROOT).as_posix() not in tracked)
    paths = list(tracked) + new_python
    entries = []
    for rel in sorted(filter(None, paths)):
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        entry = {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        try:
            source = data.decode("utf-8-sig")
        except UnicodeError:
            entries.append(entry)
            continue
        entry["lines"] = len(source.splitlines())
        if path.suffix == ".py":
            tree = ast.parse(source, filename=rel)
            entry["summary"] = (ast.get_docstring(tree) or "").split("\n")[0]
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = "." * node.level + (node.module or "")
                    imports.append(module)
            entry["imports"] = sorted(set(imports))
            symbols = []

            def visit(node, scope=""):
                nested_scope = scope
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{scope}.{node.name}" if scope else node.name
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    signature = f"class {node.name}" if kind == "class" else f"def {node.name}({ast.unparse(node.args)})"
                    symbols.append({"name": name, "kind": kind, "line": node.lineno,
                                    "end_line": node.end_lineno, "signature": signature,
                                    "summary": (ast.get_docstring(node) or "").split("\n")[0]})
                    nested_scope = name
                for child in ast.iter_child_nodes(node):
                    visit(child, nested_scope)

            visit(tree)
            entry["symbols"] = symbols
        entries.append(entry)
    modules = {}
    for entry in entries:
        rel = entry["path"]
        if rel.startswith("src/atomic_flow/") and "/benchmark_data/" not in rel and rel.endswith(".py"):
            module = rel[4:-3].replace("/", ".").removesuffix(".__init__")
            modules[module] = rel
    missing = sorted({module for entry in entries for module in entry.get("imports", [])
                      if module.startswith("atomic_flow.") and module not in modules})
    result = {"source_commit": revision, "scope": "tracked working-tree files plus new src/tests Python files; static AST, no imports",
              "untracked_python_files": new_python,
              "tracked_changes": subprocess.check_output(
                  ["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).splitlines(),
              "file_count": len(entries), "python_file_count": sum("symbols" in e for e in entries),
              "symbol_count": sum(len(e.get("symbols", [])) for e in entries),
              "missing_absolute_internal_imports": missing, "files": entries}
    target = ROOT / "docs" / "project-index.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
