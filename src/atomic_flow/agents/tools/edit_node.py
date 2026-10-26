"""edit_node: replace a function or class with explicit edit approval."""
from __future__ import annotations

import ast
from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.source import digest, read_checked, replace_checked
from atomic_flow.agents.verify import verify_edit
from atomic_flow.source_lock import mutation_guard
from atomic_flow.textutil import split_lines


class EditNodeArgs(BaseModel):
    node_id: str = Field(description="Short node id like 'n42', or full 'path::symbol'.")
    new_source: str = Field(description="Complete replacement definition, starting with def/class at column 0.")
    reason: str = Field(default="", max_length=500)


class EditNodeTool(Tool):
    name = "edit_node"
    description = (
        "Use this tool whenever the user asks to CHANGE code (rename, add, remove, refactor, fix). "
        "Provide the complete new function/class body; do not paste the new code as text in your answer. "
        "After the edit the file is verified with py_compile and matching tests; if verification fails, "
        "the change is rolled back automatically."
    )
    args_schema = EditNodeArgs

    @mutation_guard("ctx")
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

        try:
            original = ctx.read_source(node.path)
            ctx.require_read(node.path, ctx.file_hashes[node.path], node.node_id)
            text = original.decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            return {"error": f"cannot read {node.path}: {exc}"}
        lines = split_lines(text)
        old_source = "\n".join(lines[node.start_line - 1:node.end_line])
        new_body = split_lines(args.new_source.rstrip())
        if node.start_line > 0 and lines[node.start_line - 1].strip():
            indent = lines[node.start_line - 1][:len(lines[node.start_line - 1]) - len(lines[node.start_line - 1].lstrip())]
            new_body = [indent + line if line else line for line in new_body]
        new_lines = lines[:node.start_line - 1] + new_body + lines[node.end_line:]
        if new_lines == lines:
            return {"error": "replacement does not change source"}
        newline = "\r\n" if "\r\n" in text else "\n"
        suffix = newline if text.endswith(("\r", "\n")) else ""
        updated = (newline.join(new_lines) + suffix).encode("utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8")
        try:
            record = ctx.undo_stack.push(node.path, node.node_id, old_source, args.new_source,
                                         project_root=ctx.project_root, before=original, after=updated)
            replace_checked(ctx.project_root, node.path, updated, ctx.file_hashes[node.path])
        except OSError as exc:
            return {"error": f"cannot write {node.path}: {exc}"}

        verdict = None
        try:
            verdict = verify_edit(ctx.project_root, node.path, execution=ctx.execution, cancel_check=ctx.cancel_check,
                                  private_workspace=ctx.private_workspace)
            if verdict.ok:
                read_checked(ctx.project_root, node.path, digest(updated))
                ctx.refresh(changed_paths=[node.path])
                read_checked(ctx.project_root, node.path, digest(updated))
            else:
                raise ValueError(verdict.message)
        except Exception as exc:  # a crashed verifier must also restore the original
            restored = ctx.undo_stack.undo(ctx.project_root, record.edit_id)
            refresh_error = ""
            if restored:
                try:
                    ctx.refresh(changed_paths=[node.path])
                except Exception as refresh_exc:
                    refresh_error = str(refresh_exc)
            from atomic_flow.agents.execution import TaskCancelled
            if isinstance(exc, TaskCancelled):
                raise
            verification = {check: ("passed" if verdict and check in verdict.checks else "not_run")
                            for check in ("syntax", "pytest")}
            if verdict and not verdict.ok and verdict.checks:
                verification[verdict.checks[-1]] = "failed"
            return {"status": "rolled_back" if restored else "rollback_failed", "reason": str(exc),
                    "checks": verdict.checks if verdict else [], "index_refresh_error": refresh_error,
                    "verification": verification,
                    "check_timed_out": bool(verdict and verdict.timed_out),
                    "check_exit_code": verdict.exit_code if verdict else None,
                    "check_source_changed": bool(verdict and verdict.source_changed),
                    "checked_source_revision": verdict.checked_source_revision if verdict else "",
                    "test_output": verdict.test_output[:2000] if verdict else "",
                    "diagnostics": verdict.diagnostics if verdict else [],
                    "diagnostic_source_hash": verdict.source_hash if verdict else ""}
        return {"status": "applied", "edit_id": record.edit_id, "path": node.path,
                "symbol": node.symbol, "old_lines": f"{node.start_line}-{node.end_line}",
                "new_lines_count": len(new_body), "checks": verdict.checks,
                "message": verdict.message, "index_revision": ctx.revision, "diagnostics": verdict.diagnostics,
                "content_hash": ctx.file_hashes[node.path], "undo_hint": f"atomic undo {record.edit_id}"}
