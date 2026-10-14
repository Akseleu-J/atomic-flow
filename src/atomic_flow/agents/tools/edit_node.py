"""edit_node: replace a function or class with explicit edit approval."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.verify import verify_edit
from atomic_flow.tree.node import TreeNode


class EditNodeArgs(BaseModel):
    node_id: str = Field(description="Short node id like 'n42', or full 'path::symbol'.")
    new_source: str = Field(description="Complete replacement definition, starting with def/class at column 0.")
    reason: str = Field(default="", max_length=500)


def _split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")


class EditNodeTool(Tool):
    name = "edit_node"
    description = (
        "Use this tool whenever the user asks to CHANGE code (rename, add, remove, refactor, fix). "
        "Provide the complete new function/class body; do not paste the new code as text in your answer. "
        "After the edit the file is verified with py_compile and matching tests; if verification fails, "
        "the change is rolled back automatically."
    )
    args_schema = EditNodeArgs

    def run(self, args: EditNodeArgs, ctx: ToolContext) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled", "hint": "run with --approve-edits (CLI) or ATOMIC_FLOW_ALLOW_EDITS=1"}
        if ctx.undo_stack is None:
            return {"error": "internal: undo stack not attached"}

        real_id = ctx.alias_to_id.get(args.node_id, args.node_id)
        node = ctx.nodes_by_id.get(real_id)
        if node is None or node.kind not in {"function", "class"}:
            return {"error": f"cannot edit node {args.node_id}: not a function/class"}
        name = node.symbol.rsplit(".", 1)[-1].split("#")[0]
        try:
            parsed = ast.parse(args.new_source)
        except SyntaxError as exc:
            return {"error": f"new_source has syntax error at line {exc.lineno}: {exc.msg}"}
        definitions = [item for item in parsed.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if not definitions or definitions[0].name != name:
            return {"error": "new_source must define the same top-level name as the original"}

        path = ctx.project_root / node.path
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return {"error": f"cannot read {node.path}: {exc}"}
        lines = _split_lines(text)
        old_source = "\n".join(lines[node.start_line - 1:node.end_line])
        new_body = args.new_source.rstrip().splitlines()
        if node.start_line > 0 and lines[node.start_line - 1].strip():
            indent = lines[node.start_line - 1][:len(lines[node.start_line - 1]) - len(lines[node.start_line - 1].lstrip())]
            new_body = [indent + line if line else line for line in new_body]
        record = ctx.undo_stack.push(node.path, node.node_id, old_source, args.new_source, project_root=ctx.project_root)
        new_lines = lines[:node.start_line - 1] + new_body + lines[node.end_line:]
        temp = path.with_suffix(path.suffix + ".edit.tmp")
        try:
            temp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            temp.replace(path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            return {"error": f"cannot write {node.path}: {exc}"}

        verdict = verify_edit(ctx.project_root, node.path)
        if not verdict.ok:
            ctx.undo_stack.undo(ctx.project_root, record.edit_id)
            return {"status": "rolled_back", "reason": verdict.message,
                    "checks": verdict.checks, "test_output": verdict.test_output[:2000]}
        ctx.sources.pop(node.path, None)
        return {"status": "applied", "edit_id": record.edit_id, "path": node.path,
                "symbol": node.symbol, "old_lines": f"{node.start_line}-{node.end_line}",
                "new_lines_count": len(new_body), "checks": verdict.checks,
                "message": verdict.message, "undo_hint": f"atomic undo {record.edit_id}"}
