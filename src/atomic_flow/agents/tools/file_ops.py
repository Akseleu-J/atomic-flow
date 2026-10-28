"""Small hash-bound patches and explicit source-file lifecycle operations."""
from __future__ import annotations

import stat
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atomic_flow.agents import file_transaction
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.execution import TaskCancelled, checked_workspace, run_python
from atomic_flow.agents.source import SourceConflict, SourcePreconditionFailed, digest, read_checked
from atomic_flow.agents.tools.file_paths import admitted_path, text_bytes
from atomic_flow.agents.verify import _matching_tests, verify_edit
from atomic_flow.source_lock import mutation_guard


class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=500)


class ExistingFileArgs(FileArgs):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{40}$", description="Current SHA-1 from a source read/search result.")


class PatchFileArgs(ExistingFileArgs):
    old_text: str = Field(min_length=1, max_length=1_000_000, description="Exact unique UTF-8 fragment to replace.")
    new_text: str = Field(max_length=1_000_000)
    start_line: int | None = Field(default=None, ge=1, strict=True, description="Optional inclusive line range, bound to expected_hash.")
    end_line: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def valid_range(self):
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("start_line and end_line must be supplied together")
        if self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        return self


class PatchFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patches: list[PatchFileArgs] = Field(min_length=1, max_length=16,
                                       description="Up to16 unique existing files; every current revision must be read before editing.")
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def unique_paths(self):
        if len({patch.path for patch in self.patches}) != len(self.patches):
            raise ValueError("each file may appear only once in a patch transaction")
        return self


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


@mutation_guard("ctx")
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
    verdict = None
    bound_observation = {}
    bound_result = None
    try:
        file_transaction.apply(storage, ctx.project_root, edit_id, state, after, ctx.cancel_check, after_modes)
        verification["filesystem"] = "passed"
        # Validate content at surviving paths. verify_edit delegates matching test
        # execution to the configured runner, including Docker and cancellation.
        tested = set()
        for path, data in after.items():
            if data is None:
                continue
            verdict = verify_edit(ctx.project_root, path, execution=ctx.execution, cancel_check=ctx.cancel_check,
                                  private_workspace=ctx.private_workspace, check_obligation=ctx.check_obligation)
            diagnostics.extend(verdict.diagnostics)
            bound_observation = verdict.check_observation
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
        if ctx.check_obligation is not None and any(data is None for data in after.values()):
            from atomic_flow.check_obligation import check_observation, run_obligation_check
            with checked_workspace(ctx.project_root, private_workspace=ctx.private_workspace, cancel_check=ctx.cancel_check) as check_root:
                spec, receipt = run_obligation_check(ctx.check_obligation, check_root, cancel_check=ctx.cancel_check)
            bound_observation = check_observation(ctx.check_obligation, spec, receipt)
            bound_result = receipt.result
            checks.append("pytest")
            verification["pytest"] = "passed" if receipt.check_passed else "failed"
            if not receipt.check_passed:
                raise SourceConflict("complete host ordinary-test obligation failed after file removal/move")
        elif removed_tests:
            with checked_workspace(ctx.project_root, private_workspace=ctx.private_workspace, cancel_check=ctx.cancel_check) as check_root:
                result = run_python(["-m", "pytest", "-q", "--no-header", "-x", *removed_tests], check_root,
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
        ctx.refresh(changed_paths=list(state["changes"]))
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
                ctx.refresh(changed_paths=list(state["changes"]))
            except Exception as refresh_exc:
                refresh_error = str(refresh_exc)
        if isinstance(exc, TaskCancelled):
            raise
        return {"status": "rolled_back" if restored else "rollback_failed", "reason": str(exc),
                "edit_id": edit_id, "checks": checks, "verification": verification,
                "check_timed_out": bound_result.timed_out if bound_result is not None else bool(verdict and verdict.timed_out),
                "check_exit_code": bound_result.returncode if bound_result is not None else verdict.exit_code if verdict else None,
                "check_source_changed": bool(verdict and verdict.source_changed),
                "checked_source_revision": verdict.checked_source_revision if verdict else "",
                    **bound_observation,
                "index_refresh_error": refresh_error, "diagnostics": diagnostics}
    changes = [{"path": path, "before_hash": hashes["before"], "after_hash": hashes["after"]}
               for path, hashes in state["changes"].items()]
    return {"status": "applied", "edit_id": edit_id, "path": next(iter(after)), "paths": list(after),
            "changes": changes, "content_hash": changes[0]["after_hash"], "checks": checks,
            "verification": verification, "index_revision": ctx.revision, "diagnostics": diagnostics,
            **bound_observation,
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
            if args.start_line is not None:
                lines = text.splitlines(keepends=True)
                if args.end_line > len(lines) or "".join(lines[args.start_line - 1:args.end_line]) != args.old_text:
                    return {"error": "range/old_text precondition failed; reread current source"}
                updated = text_bytes("".join(lines[:args.start_line - 1]) + args.new_text + "".join(lines[args.end_line:]))
            else:
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
            raise SourcePreconditionFailed(str(exc)) from None
        return _mutate(ctx, {args.path: original}, {args.path: updated})


class PatchFilesTool(Tool):
    name = "patch_files"
    description = "Apply patches=[{path, expected_hash, old_text, new_text, optional start_line/end_line}] for up to16 unique existing files as one journaled edit. Read every file first; verify combined source and roll back the whole set on failure."
    args_schema = PatchFilesArgs
    contract = ToolContract(effects=("read", "write", "execute"), timeout_seconds=300, idempotent=False)

    @mutation_guard("ctx")
    def run(self, args: PatchFilesArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        before, after, resolved = {}, {}, set()
        try:
            for patch in args.patches:
                ctx.cancel_check()
                target = admitted_path(ctx, patch.path, existing=True).resolve()
                if target in resolved:
                    raise SourceConflict("patch transaction aliases the same source file")
                resolved.add(target)
                if ctx.mutation_scope is not None and patch.path not in ctx.mutation_scope:
                    raise SourceConflict("mutation path is outside the approved plan scope")
                original = _read(ctx, patch.path, patch.expected_hash)
                text = original.decode("utf-8-sig")
                if patch.start_line is not None:
                    lines = text.splitlines(keepends=True)
                    if patch.end_line > len(lines) or "".join(lines[patch.start_line - 1:patch.end_line]) != patch.old_text:
                        raise SourceConflict("range/old_text precondition failed; reread current source")
                    updated = text_bytes("".join(lines[:patch.start_line - 1]) + patch.new_text + "".join(lines[patch.end_line:]))
                else:
                    if text.count(patch.old_text) != 1:
                        raise SourceConflict("old_text must match exactly one fragment; no source changed")
                    updated = text_bytes(text.replace(patch.old_text, patch.new_text, 1))
                if original.startswith(b"\xef\xbb\xbf"):
                    updated = b"\xef\xbb\xbf" + updated
                if updated == original:
                    raise SourceConflict("patch does not change source")
                before[patch.path], after[patch.path] = original, updated
        except (OSError, UnicodeError) as exc:
            # Every path, proof and replacement has to pass before the existing
            # journal can perform the first write or start a check.
            raise SourcePreconditionFailed(str(exc)) from None
        return _mutate(ctx, before, after)


class CreateFileTool(Tool):
    name = "create_file"
    description = "Create an absent supported text source file; never overwrite; verify before accepting."
    args_schema = CreateFileArgs
    contract = ToolContract(effects=("write", "execute"), timeout_seconds=300, idempotent=False)

    def run(self, args: CreateFileArgs, ctx) -> dict[str, Any]:
        if not ctx.edits_enabled:
            return {"error": "edits disabled"}
        try:
            target = admitted_path(ctx, args.path)
            if target.exists():
                return {"error": "destination already exists"}
            data = text_bytes(args.content)
        except (OSError, UnicodeError) as exc:
            raise SourcePreconditionFailed(str(exc)) from None
        return _mutate(ctx, {args.path: None}, {args.path: data})


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
            raise SourcePreconditionFailed(str(exc)) from None
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
            raise SourcePreconditionFailed(str(exc)) from None
        return _mutate(ctx, {args.destination: None, args.path: original}, {args.destination: original, args.path: None},
                       after_modes={args.destination: mode})
