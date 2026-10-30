"""run_command: execute a fixed set of project quality tools without a shell."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.base import Tool
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.diagnostics import quality_diagnostics
from atomic_flow.agents.execution import checked_workspace, run_python
from atomic_flow.agents.verify import check_source_changed, check_source_snapshot
from atomic_flow.output import sanitize_output

ALLOWED = {
    "pytest": ["pytest"],
    "ruff": ["ruff", "check"],
    "mypy": ["mypy", "src"],
}


class RunCommandArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal["check", "pytest", "ruff", "mypy"]
    target: str = Field(default="", description="Optional path/pattern inside the project.")


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run the full host ordinary-test obligation with check, or quality tools pytest, ruff, or mypy. "
        "Commands come from a fixed list; arbitrary shell is unavailable."
    )
    args_schema = RunCommandArgs

    def run(self, args: RunCommandArgs, ctx: ToolContext) -> dict[str, Any]:
        if args.command == "check" and ctx.check_obligation is None:
            return {"command": args.command, "error": "check requires a host ordinary-test obligation"}
        if ctx.check_obligation is not None and args.command in {"check", "pytest"}:
            from atomic_flow.check_obligation import check_observation, run_obligation_check
            if args.target:
                return {"command": args.command, "error": "host ordinary-test obligation cannot be narrowed"}
            if args.command == "pytest" and ctx.check_obligation.template.runner != "pytest":
                return {"command": args.command, "error": "native host obligation requires command check"}
            ctx.cancel_check()
            with checked_workspace(ctx.project_root, private_workspace=ctx.private_workspace, cancel_check=ctx.cancel_check) as check_root:
                spec, receipt = run_obligation_check(ctx.check_obligation, check_root, cancel_check=ctx.cancel_check)
            result = receipt.result
            observation = check_observation(ctx.check_obligation, spec, receipt)
            return sanitize_output({"command": args.command, "exit_code": result.returncode,
                                    **observation, "timed_out": result.timed_out,
                                    "error": "" if receipt.check_passed else "complete host test obligation failed or incomplete",
                                    "stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:], "index_revision": ctx.revision})
        command_parts = ALLOWED.get(args.command)
        if command_parts is None:
            return {"error": f"command not allowed: {args.command}"}
        cmd = ["-m", *command_parts]
        if args.target:
            project_root = ctx.project_root.resolve()
            target_path = (ctx.project_root / args.target).resolve()
            if not target_path.is_relative_to(project_root):
                return {"error": "target escapes project root"}
            cmd.extend(["--", target_path.relative_to(project_root).as_posix()])
        # Model-invoked checks never mount the original checkout (which can
        # contain credentials excluded by source admission). TaskRunner already
        # supplies a private snapshot; ordinary ask gets an ephemeral changeset.
        ctx.cancel_check()
        with checked_workspace(ctx.project_root, private_workspace=ctx.private_workspace, cancel_check=ctx.cancel_check) as check_root:
            from atomic_flow.agents.step_evidence import manifest_digest
            checked_snapshot = check_source_snapshot(check_root, ctx.cancel_check)
            checked_revision = manifest_digest(checked_snapshot)
            result = run_python(cmd, check_root, config=ctx.execution, timeout=120, cancel_check=ctx.cancel_check)
            changed = check_source_changed(checked_snapshot, check_source_snapshot(check_root, ctx.cancel_check))
        if changed:
            return {"command": args.command, "error": "source changed during check", "check_source_changed": True,
                    "checked_source_revision": checked_revision}
        if result.timed_out:
            return {"command": args.command, "error": "command timed out after 120 seconds", "timed_out": True,
                    "checked_source_revision": checked_revision}
        return sanitize_output({"command": args.command, "exit_code": result.returncode,
                "checked_source_revision": checked_revision,
                "stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:], "index_revision": ctx.revision,
                **quality_diagnostics(args.command, result.stdout + "\n" + result.stderr, check_root, dict(ctx.file_hashes))})
