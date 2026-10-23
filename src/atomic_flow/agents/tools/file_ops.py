"""Small hash-bound patches and explicit source-file lifecycle operations."""
from __future__ import annotations

import stat
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents import file_transaction
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.execution import TaskCancelled, run_python
from atomic_flow.agents.source import SourceConflict, digest, read_checked
from atomic_flow.agents.tools.file_paths import admitted_path, text_bytes
from atomic_flow.agents.verify import _matching_tests, verify_edit


class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=500)


class ExistingFileArgs(FileArgs):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{40}$", description="Current SHA-1 from a source read/search result.")


class PatchFileArgs(ExistingFileArgs):
    old_text: str = Field(min_length=1, max_length=1_000_000, description="Exact unique UTF-8 fragment to replace.")
    new_text: str = Field(max_length=1_000_000)


class CreateFileArgs(FileArgs):
    content: str = Field(max_length=1_000_000)


class DeleteFileArgs(ExistingFileArgs):
    pass


class MoveFileArgs(ExistingFileArgs):
    destination: str = Field(min_length=1, max_length=500, description="New absent project-relative path.")


def _read(ctx, path: str, expected_hash: str) -> bytes:
    admitted_path(ctx, path, existing=True)
    # Only operate on an indexed source revision; an arbitrary guessed hash must
    # not bypass the project's ignored/private source admission rules.
    if ctx.file_hashes.get(path) != expected_hash:
        raise SourceConflict("expected_hash is not the current indexed revision; refresh/read first")
    data = read_checked(ctx.project_root, path, expected_hash)
    ctx.require_read(path, expected_hash)
    if b"\x00" in data:
        raise SourceConflict("binary files are not supported")
    data.decode("utf-8-sig")
    return data


def _mutate(ctx, before: dict[str, bytes | None], after: dict[str, bytes | None], *, after_modes=None) -> dict[str, Any]:
    if not ctx.edits_enabled:
        return {"error": "edits disabled"}
    if ctx.undo_stack is None:
        return {"error": "internal: undo stack not attached"}
    storage = ctx.undo_stack.storage_dir
    edit_id, state = file_transaction.begin(storage, ctx.project_root, before, after)
    checks = ["filesystem"]
    verification = {"filesystem": "not_run", "syntax": "not_run", "pytest": "not_run"}
    diagnostics = []
    try:
        file_transaction.apply(storage, ctx.project_root, edit_id, state, after, ctx.cancel_check, after_modes)
        verification["filesystem"] = "passed"
        # Validate content at surviving paths. verify_edit delegates matching test
        # execution to the configured runner, including Docker and cancellation.
        tested = set()
        for path, data in after.items():
            if data is None:
                continue
            verdict = verify_edit(ctx.project_root, path, execution=ctx.execution, cancel_check=ctx.cancel_check)
            diagnostics.extend(verdict.diagnostics)
            for check in verdict.checks:
                if check != "skip":
                    if check not in checks:
                        checks.append(check)
                    verification[check] = "passed" if verdict.ok or check != verdict.checks[-1] else "failed"
            if not verdict.ok:
                raise SourceConflict(verdict.message)
            tested.update(_matching_tests(ctx.project_root, path))
        # A removal/move can break the old module's dependants even though its
        # former path cannot be compiled. Run its matching tests when present.
        removed_tests = sorted({test for path, data in after.items() if data is None
                                for test in _matching_tests(ctx.project_root, path)} - tested)
        if removed_tests:
            result = run_python(["-m", "pytest", "-q", "--no-header", "-x", *removed_tests], ctx.project_root,
                                config=ctx.execution, timeout=120, cancel_check=ctx.cancel_check)
            checks.append("pytest")
            verification["pytest"] = "passed" if result.returncode == 0 and not result.timed_out else "failed"
            if verification["pytest"] != "passed":
                raise SourceConflict("matching tests failed after file removal/move")
        ctx.cancel_check()
        for path, hashes in state["changes"].items():
            target = admitted_path(ctx, path)
            current = digest(target.read_bytes()) if target.exists() else None
            if current != hashes["after"]:
                raise SourceConflict(f"source changed during verification: {path}")
        ctx.refresh()
        for path, hashes in state["changes"].items():
            target = admitted_path(ctx, path)
            current = digest(target.read_bytes()) if target.exists() else None
            if current != hashes["after"]:
                raise SourceConflict(f"source changed during index refresh: {path}")
        file_transaction.finish(storage, edit_id, state)
    except Exception as exc:
        restored = file_transaction.undo(storage, ctx.project_root, edit_id, interrupted=True)
        refresh_error = ""
        if restored:
            try:
                ctx.refresh()
            except Exception as refresh_exc:
                refresh_error = str(refresh_exc)
        if isinstance(exc, TaskCancelled):
            raise
        return {"status": "rolled_back" if restored else "rollback_failed", "reason": str(exc),
                "edit_id": edit_id, "checks": checks, "verification": verification,
                "index_refresh_error": refresh_error, "diagnostics": diagnostics}
    changes = [{"path": path, "before_hash": hashes["before"], "after_hash": hashes["after"]}
               for path, hashes in state["changes"].items()]
    return {"status": "applied", "edit_id": edit_id, "path": next(iter(after)), "paths": list(after),
            "changes": changes, "content_hash": changes[0]["after_hash"], "checks": checks,
            "verification": verification, "index_revision": ctx.revision, "diagnostics": diagnostics,
            "undo_hint": f"atomic undo {edit_id}"}


class PatchFileTool(Tool):
    name = "patch_file"
    description = "Replace one exact unique fragment in a hash-checked text source; verify and roll back failures."
    args_schema = PatchFileArgs
    contract = ToolContract(effects=("read", "write", "execute"), timeout_seconds=300, idempotent=False)

    def run(self, args: PatchFileArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        try:
            original = _read(ctx, args.path, args.expected_hash)
            text = original.decode("utf-8-sig")
            if text.count(args.old_text) != 1:
                return {"error": "old_text must match exactly one fragment; no source changed"}
            updated = text_bytes(text.replace(args.old_text, args.new_text, 1))
            if original.startswith(b"\xef\xbb\xbf"):
                updated = b"\xef\xbb\xbf" + updated
            if updated == original:
                return {"error": "patch does not change source"}
        except (OSError, UnicodeError) as exc:
            # Only this preflight is known to precede every write/execute.
            # Do not classify exceptions after _mutate as safe refusals.
            return {"error": str(exc)}
        return _mutate(ctx, {args.path: original}, {args.path: updated})


class CreateFileTool(Tool):
    name = "create_file"
    description = "Create an absent supported text source file; never overwrite; verify before accepting."
    args_schema = CreateFileArgs
    contract = ToolContract(effects=("write", "execute"), timeout_seconds=300, idempotent=False)

    def run(self, args: CreateFileArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        target = admitted_path(ctx, args.path)
        if target.exists():
            return {"error": "destination already exists"}
        return _mutate(ctx, {args.path: None}, {args.path: text_bytes(args.content)})


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete one regular indexed text file at its expected hash; retain a recoverable backup."
    args_schema = DeleteFileArgs
    contract = ToolContract(effects=("read", "write", "execute"), timeout_seconds=300, idempotent=False)

    def run(self, args: DeleteFileArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        try:
            original = _read(ctx, args.path, args.expected_hash)
        except (OSError, UnicodeError) as exc:
            return {"error": str(exc)}
        return _mutate(ctx, {args.path: original}, {args.path: None})


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move a hash-checked indexed text file to an absent supported source path; verify and preserve undo."
    args_schema = MoveFileArgs
    contract = ToolContract(effects=("read", "write", "execute"), timeout_seconds=300, idempotent=False)

    def run(self, args: MoveFileArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        try:
            original = _read(ctx, args.path, args.expected_hash)
            destination = admitted_path(ctx, args.destination)
            if destination.exists() or destination.resolve() == admitted_path(ctx, args.path).resolve():
                return {"error": "destination must be absent and different from source"}
            mode = stat.S_IMODE(admitted_path(ctx, args.path, existing=True).stat().st_mode)
        except (OSError, UnicodeError) as exc:
            return {"error": str(exc)}
        return _mutate(ctx, {args.destination: None, args.path: original}, {args.destination: original, args.path: None},
                       after_modes={args.destination: mode})
