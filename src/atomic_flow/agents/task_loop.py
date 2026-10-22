"""TaskRunner: execute a plan step-by-step, verifying each step."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.control import check_cancel, task_lease
from atomic_flow.agents.execution import TaskCancelled, run_python
from atomic_flow.agents.planner import Plan, plan_task, replan_task
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend, check_accounting
from atomic_flow.config import AtomicConfig
from atomic_flow.errors import BudgetExceeded
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree

MUTATING_TOOLS = {"edit_node", "patch_file", "create_file", "delete_file", "move_file"}


@dataclass
class StepFailure(Exception):
    step_index: int
    message: str


class TaskRunner:
    def __init__(self, config: AtomicConfig | None, project: Path, backend, approval_callback=None, question_callback=None):
        self.config = config if config is not None else AtomicConfig()
        self.project = project
        self.backend = backend
        self.approval_callback = approval_callback
        self.question_callback = question_callback
        self.changeset: ChangeSet | None = None
        self.cancel_check = lambda: None
        self.task_backend = backend

    @staticmethod
    def _steps(plan: Plan) -> list[Step]:
        return [Step(index=i, description=p.description, tools_needed=list(p.tools_needed),
                     expected_outcome=p.expected_outcome, verify_with=p.verify_with)
                for i, p in enumerate(plan.steps)]

    def prepare(self, query: str, *, candidates: list[str], n_files: int) -> Task:
        """Persist a task before planning, including rejected/repaired planner responses."""
        task = Task.new(query, self.project, [])
        task.plan_ready = False
        save(task)
        with task_lease(task.id):
            def check():
                check_cancel(task.id)
            backend = TaskBudgetBackend(self.backend, task, self.config, check, phase="plan")
            try:
                plan = plan_task(query, project_root=self.project, backend=backend,
                                 candidates=candidates, n_files=n_files)
                task.steps = self._steps(plan)
                task.rationale = plan.rationale
                task.plan_ready = True
                task.status = "paused"
                task.error = "plan ready; execution not started"
            except BudgetExceeded as exc:
                task.status, task.error = "paused", str(exc)
            except TaskCancelled as exc:
                task.status, task.error = "failed", str(exc)
            except Exception as exc:
                task.status, task.error = "failed", f"planning failed: {type(exc).__name__}"
            save(task)
        return task

    def run_plan(self, query: str, plan: Plan) -> Task:
        steps = self._steps(plan)
        task = Task.new(query=query, project=self.project, steps=steps, rationale=plan.rationale)
        save(task)
        return self.resume(task)

    def resume(self, task: Task) -> Task:
        with task_lease(task.id):
            try:
                task = load(task.id)
            except FileNotFoundError:
                save(task)
            self.changeset = None
            if task.status in {"done", "failed"}:
                return task
            self.cancel_check = lambda: check_cancel(task.id)
            self.task_backend = TaskBudgetBackend(self.backend, task, self.config, self.cancel_check)
            try:
                check_accounting(task)
                if not task.plan_ready:
                    raise BudgetExceeded("planning incomplete; review costs and start a new task")
                return self._resume_locked(task)
            except BudgetExceeded as exc:
                task.status, task.error = "paused", str(exc)
                save(task)
                return task
            except TaskCancelled as exc:
                if self.changeset and self.changeset.state["status"] == "active":
                    self.changeset.abort()
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = time.time()
                save(task)
                return task

    def _resume_locked(self, task: Task) -> Task:
        self.cancel_check()
        try:
            if Path(task.project).resolve() != self.project.resolve():
                raise ChangeSetConflict("task belongs to another project")
            if task.changeset_id:
                self.changeset = ChangeSet.open(self.project, task.changeset_id)
                if self.changeset.state["status"] == "published" and task.current_index == len(task.steps):
                    self.changeset.verify_published()
                    task.status = "done"
                    task.finished_at = time.time()
                    task.error = ""
                    save(task)
                    return task
                if self.changeset.state["status"] in {"publishing", "recovery_conflict"}:
                    self.changeset.recover()
                    raise ChangeSetConflict("interrupted publication recovered; review task before starting a new task")
                if self.changeset.state["status"] != "active":
                    raise ChangeSetConflict("changeset is closed; start a new task instead of replaying edits")
                self.changeset.check_original()
                self.changeset.validate_workspace()
                if any(step.status == "running" for step in task.steps):
                    raise ChangeSetConflict("interrupted step may have side effects; review its workspace before resuming")
            else:
                if task.current_index:
                    raise ChangeSetConflict("legacy checkpoint has no workspace; cannot safely replay/resume it")
                self.changeset = ChangeSet.create(self.project)
                task.changeset_id = self.changeset.id
            save_tree(self.changeset.workspace, build_tree(self.changeset.workspace, self.config.tree.ignore,
                      respect_gitignore=self.config.tree.respect_gitignore))
        except (OSError, ValueError) as exc:
            task.status = "failed"
            task.error = str(exc)
            save(task)
            return task
        result = self._resume_steps(task)
        if result.status == "running" and result.current_index == len(result.steps):
            try:
                self.changeset.publish(cancel_check=self.cancel_check, finalize=lambda: save_tree(
                    self.project, build_tree(self.project, self.config.tree.ignore,
                                             respect_gitignore=self.config.tree.respect_gitignore)))
                result.status = "done"
                result.finished_at = time.time()
                result.error = ""
            except TaskCancelled:
                raise
            except (OSError, ValueError) as exc:
                result.status = "failed"
                result.error = f"publication failed: {exc} (journal: {self.changeset.id})"
        elif result.status == "failed":
            self.changeset.abort()
        save(result)
        return result

    def _resume_steps(self, task: Task) -> Task:
        task.status = "running"
        task.error = ""
        save(task)

        while task.current_index < len(task.steps):
            self.cancel_check()
            if (self.config.task.fail_on_budget_exceeded and self.config.task.max_budget_usd > 0
                    and task.budget_spent >= self.config.task.max_budget_usd):
                task.status = "paused"
                task.error = "task budget exhausted before next step"
                save(task)
                return task
            step = task.steps[task.current_index]
            step.status = "running"
            step.started_at = time.time()
            save(task)
            checkpoint = None
            try:
                checkpoint = self.changeset.checkpoint()
                self._execute_step(task, step)
                self.cancel_check()
                step.status = "done"
                step.result_summary = step.result_summary or "ok"
                step.finished_at = time.time()
                task.current_index += 1
                save(task)
                continue
            except Exception as exc:
                step.status = "failed"
                step.result_summary = f"{type(exc).__name__}: {exc}"[:500]
                step.finished_at = time.time()
                try:
                    if checkpoint is None:
                        raise ChangeSetConflict(f"checkpoint failed: {exc}")
                    self.changeset.restore_checkpoint(checkpoint)
                    save_tree(self.changeset.workspace, build_tree(
                        self.changeset.workspace, self.config.tree.ignore,
                        respect_gitignore=self.config.tree.respect_gitignore))
                except Exception as restore_error:
                    task.status = "failed"
                    task.error = f"workspace rollback failed: {restore_error}"
                    save(task)
                    return task
                save(task)
                if isinstance(exc, BudgetExceeded):
                    step.status = "pending"
                    step.started_at = step.finished_at = None
                    task.status, task.error = "paused", str(exc)
                    save(task)
                    return task
                if isinstance(exc, TaskCancelled):
                    raise

            if self.config is not None and self.config.task.max_budget_usd > 0 \
                    and task.budget_spent >= self.config.task.max_budget_usd \
                    and self.config.task.fail_on_budget_exceeded:
                task.status = "paused"
                task.error = f"budget exceeded: ${task.budget_spent:.4f} >= ${self.config.task.max_budget_usd:.4f}"
                save(task)
                return task

            max_replans = self.config.task.max_replans if self.config is not None else 2
            if task.replan_count >= max_replans:
                task.status = "failed"
                task.error = step.result_summary
                save(task)
                return task

            try:
                self.cancel_check()
                self.task_backend.phase = "replan"
                new_plan = replan_task(
                    task.query,
                    completed=[f"{s.index + 1}. {s.description}" for s in task.steps if s.status == "done"],
                    failed_description=step.description,
                    verify_with=step.verify_with,
                    error=step.result_summary,
                    remaining=[f"{s.index + 1}. {s.description}" for s in task.steps[task.current_index + 1:]],
                    project_root=self.changeset.workspace,
                    backend=self.task_backend,
                    settings_project=self.project,
                )
            except BudgetExceeded as exc:
                task.status, task.error = "paused", str(exc)
                save(task)
                return task
            except TaskCancelled:
                raise
            except Exception as exc:
                task.status = "failed"
                task.error = step.result_summary if self.backend is None or not hasattr(self.backend, "chat") else f"replan failed: {exc}"
                save(task)
                return task

            if len(new_plan.steps) == 1 and new_plan.steps[0].description.startswith("STOP:"):
                task.status = "failed"
                task.error = step.result_summary or new_plan.steps[0].description
                save(task)
                return task

            base_index = len(task.steps)
            for i, p in enumerate(new_plan.steps):
                task.steps.append(
                    Step(
                        index=base_index + i,
                        description=p.description,
                        tools_needed=list(p.tools_needed),
                        expected_outcome=p.expected_outcome,
                        verify_with=p.verify_with,
                    )
                )
            task.replan_count += 1
            task.current_index = base_index
            save(task)

        # Completion is recorded only after publishing the workspace successfully.
        save(task)
        return task

    def _execute_step(self, task: Task, step: Step) -> None:
        subquery = f"[Step {step.index + 1}/{len(task.steps)}] {step.description}"
        workspace = self.changeset.workspace if self.changeset else self.project
        if isinstance(self.task_backend, TaskBudgetBackend):
            self.task_backend.phase = "step"
        orch = Orchestrator(self.config, workspace, self.task_backend, approval_callback=self.approval_callback,
                            settings_project=self.project, cancel_check=self.cancel_check,
                            question_callback=self.question_callback, private_workspace=self.changeset is not None)
        result = orch.answer(subquery, mode="medium", approve_edits=True)
        step.request_id = result.ledger_id
        if self.changeset:
            edits = {}
            for call in result.tool_calls or []:
                summary = call.get("result_summary") or {}
                if call.get("tool") in MUTATING_TOOLS and summary.get("status") == "applied":
                    if summary.get("changes"):
                        for change in summary["changes"]:
                            edits[change["path"]] = change["after_hash"]
                    else:
                        edits[summary["path"]] = summary.get("content_hash")
            for path, expected_hash in edits.items():
                self.changeset.track(path, expected_hash)
        self._verify_step(step, result)

    def _verify_step(self, step: Step, result) -> None:
        calls = result.tool_calls or []
        if step.verify_with not in {"none", "py_compile", "pytest"}:
            raise StepFailure(step.index, f"unknown verification method: {step.verify_with}")
        edits = []
        last_edit = -1
        for i, call in enumerate(calls):
            summary = call.get("result_summary")
            if not isinstance(summary, dict) or not summary:
                raise StepFailure(step.index, f"{call.get('tool')}: missing tool result")
            if summary.get("status") == "rolled_back":
                raise StepFailure(step.index, f"edit rolled back: {summary.get('reason', 'verification failed')}")
            if summary.get("outcome") in {"error", "denied"} or summary.get("error"):
                raise StepFailure(step.index, f"{call.get('tool')}: {summary.get('error') or summary['outcome']}")
            if summary.get("outcome") != "success":
                raise StepFailure(step.index, f"{call.get('tool')}: missing successful tool outcome")
            if call.get("tool") in MUTATING_TOOLS:
                if summary.get("status") != "applied" or not summary.get("checks"):
                    raise StepFailure(step.index, "edit lacks applied status and syntax verification")
                if call.get("tool") == "edit_node" and "syntax" not in summary.get("checks", []):
                    raise StepFailure(step.index, "edit lacks syntax verification")
                edits.append(summary)
                last_edit = i
            if call.get("tool") == "run_command" and (
                type(summary.get("exit_code")) is not int or summary["exit_code"] != 0
            ):
                raise StepFailure(step.index, f"command failed or missing exit_code: {summary.get('exit_code')}")

        if (step.verify_with == "py_compile" or MUTATING_TOOLS.intersection(step.tools_needed)) and not edits:
            raise StepFailure(step.index, "step requires a verified edit, but no edit was applied")
        if "run_command" in step.tools_needed and not any(call.get("tool") == "run_command" for call in calls):
            raise StepFailure(step.index, "step requires run_command, but no command was executed")
        if step.verify_with == "none":
            step.result_summary = "completed (verification not requested)"
            return
        if step.verify_with == "py_compile":
            syntax = "passed" if all("syntax" in edit.get("checks", []) for edit in edits) else "not run for all files"
            step.result_summary = f"ok (file verification passed; syntax {syntax}; tests " + (
                "passed)" if all("pytest" in edit.get("checks", []) for edit in edits) else "not run for all edits)"
            )
            return

        if step.verify_with == "pytest":
            fresh_pytest_calls = [
                call for i, call in enumerate(calls)
                if call.get("tool") == "run_command"
                and call.get("args", {}).get("command") == "pytest"
                and not call.get("args", {}).get("target")
                and i > last_edit
            ]
            if fresh_pytest_calls:
                step.result_summary = "ok (pytest passed after last edit)"
                return

            proc = run_python(["-m", "pytest", "-q", "--no-header", "-x"],
                              self.changeset.workspace if self.changeset else self.project,
                              config=self.config.execution, timeout=180, cancel_check=self.cancel_check)
            if proc.timed_out:
                raise StepFailure(step.index, "pytest timed out after 180s")
            if proc.returncode != 0:
                out = (proc.stdout + proc.stderr)[-1500:]
                raise StepFailure(step.index, f"pytest exit_code={proc.returncode}\n{out}")
            step.result_summary = "ok (pytest auto-run by TaskRunner)"
            return
