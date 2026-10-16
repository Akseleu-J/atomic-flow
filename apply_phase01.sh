#!/usr/bin/env bash
# fix_failed_patches.sh — re-applies A2, A16, A13
set -euo pipefail

cd "${1:-.}"

# ---------------------------------------------------------------------------
# A2: package-data fixtures/**/*.py
# ---------------------------------------------------------------------------
echo "=== A2 package-data ==="
python - <<'PY'
import pathlib, sys
p = pathlib.Path("pyproject.toml")
s = p.read_text()
old, new = "benchmark_data/fixtures/*.py", "benchmark_data/fixtures/**/*.py"
if new in s:
    print("A2: already applied"); sys.exit(0)
if old not in s:
    print("A2: pattern not found", file=sys.stderr); sys.exit(1)
p.write_text(s.replace(old, new))
print("A2: applied")
PY
pytest -q >/tmp/p.out 2>&1 && echo "  pytest: PASS ($(grep -oE '[0-9]+ passed' /tmp/p.out | head -1))" || { echo "  pytest: FAIL"; tail -20 /tmp/p.out; exit 1; }

# ---------------------------------------------------------------------------
# A16: word-boundary regex in choose_mode
# ---------------------------------------------------------------------------
echo "=== A16 choose_mode ==="
python - <<'PY'
import pathlib, sys
p = pathlib.Path("src/atomic_flow/llm/orchestrator.py")
s = p.read_text()
if r"refactor\w*" in s:
    print("A16: already applied"); sys.exit(0)
old = '''    hard_terms = ("refactor", "migration", "migrate", "trace", "review", "audit", "рефактор", "миграц",
                  "трассир", "аудит", "across files", "cross-file")
    if any(term in text for term in hard_terms):
        return "hard"'''
new = '''    if re.search(
        r"\\b(refactor\\w*|migrat\\w*|trace\\b|review|audit\\w*|"
        r"рефактор\\w*|миграц\\w*|трассир\\w*|аудит\\w*)\\b|across files|cross-file",
        text,
    ):
        return "hard"'''
if old not in s:
    print("A16: block not found — проверьте вручную:", file=sys.stderr); sys.exit(1)
p.write_text(s.replace(old, new))
print("A16: applied")
PY
pytest -q >/tmp/p.out 2>&1 && echo "  pytest: PASS ($(grep -oE '[0-9]+ passed' /tmp/p.out | head -1))" || { echo "  pytest: FAIL"; tail -20 /tmp/p.out; exit 1; }

# ---------------------------------------------------------------------------
# A13: references.py — module only (no integration)
# ---------------------------------------------------------------------------
echo "=== A13 references.py ==="
if [[ -f src/atomic_flow/tree/references.py ]]; then
    echo "A13: already exists"
else
    cat > src/atomic_flow/tree/references.py <<'PYFILE'
"""Reference edges: names that are PASSED, not called.

v0.2 only records ast.Call targets inside function bodies, so these are invisible:
  * jax.lax.scan(step, ...), pl.pallas_call(kernel, ...), threading.Thread(target=fn)
  * module-level wiring such as `_gdn2_core.defvjp(_gdn2_core_fwd, _gdn2_core_bwd)`
  * decorator arguments and aliased imports (`from .x import f as _r2c`)
"""
from __future__ import annotations

import ast


def collect_references(source: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Returns ({scope_symbol_or_'' -> loaded names}, {import alias -> original name})."""
    tree = ast.parse(source)
    refs: dict[str, set[str]] = {}
    aliases: dict[str, str] = {}

    class V(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []

        def _bucket(self) -> set[str]:
            return refs.setdefault(".".join(self.scope), set())

        def visit_ImportFrom(self, node):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self._bucket().add(node.id)

        def _def(self, node):
            for d in node.decorator_list:
                self.visit(d)
            self.scope.append(node.name)
            for child in node.body:
                self.visit(child)
            self.scope.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _def

    V().visit(tree)
    return refs, aliases
PYFILE
    echo "A13: references.py created"
fi
pytest -q >/tmp/p.out 2>&1 && echo "  pytest: PASS ($(grep -oE '[0-9]+ passed' /tmp/p.out | head -1))" || { echo "  pytest: FAIL"; tail -20 /tmp/p.out; exit 1; }

echo
echo "=== Done ==="
echo "Applied: A1, A3, A11, A2, A16, A13 (module)"
echo "Still manual: A10 (activate.py), A13 _link integration"