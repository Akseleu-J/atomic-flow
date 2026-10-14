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
