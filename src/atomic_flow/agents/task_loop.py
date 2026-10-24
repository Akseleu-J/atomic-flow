"""TaskRunner: execute a plan step-by-step, verifying each step."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.control import check_cancel, task_lease
from atomic_flow.agents.effects import UnresolvedEffects
from atomic_flow.agents.execution import TaskCancelled, run_python
from atomic_flow.agents.plan_review import require_approval, require_scope, review_payload
from atomic_flow.agents.planner import Plan, plan_task, replan_task
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend, check_accounting
from atomic_flow.config import AtomicConfig
from atomic_flow.errors import BudgetExceeded, TreeError
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.project_resources import ProjectLimits
from atomic_flow.source_lock import source_mutation_lease
from atomic_flow.tree.builder import build_tree, load_tree, save_tree

MUTATING_TOOLS = {"edit_node", "patch_file", "create_file", "delete_file", "move_file", "notebook_edit"}


@dataclass(frozen=True)
class TaskCapabilityContext:
    """What a host capability factory may bind to for one step.

    ``workspace`` is the staged task workspace when a changeset exists and the
    original root otherwise; capabilities that operate on source must bind HERE,
    never to the original root while staging is active.
    """

    task_id: str
    project: Path
    workspace: Path
    phase: str


@dataclass
class TaskCapabilities:
    """Host-injected step capabilities passed through to the step Orchestrator.

    The factory is trusted host code re-invoked per step against the current
    staged root; persisted task state never stores or restores these objects.
    """

    workspace_session: object | None = None
    lsp_client: object | None = None
    documentation_client: object | None = None
    hooks: object | None = None
    tool_registry: object | None = None
    role_tools: dict | None = None


@dataclass
class StepFailure(Exception):
    step_index: int
    message: str


class TaskRunner:
    def __init__(self, config: AtomicConfig | None, project: Path, backend, approval_callback=None, question_callback=None,
                 capability_factory=None, resource_limits: ProjectLimits | None = None):
        if resource_limits is not None and not isinstance(resource_limits, ProjectLimits):
            raise TypeError("resource_limits must be host-owned ProjectLimits")
        self.config = config if config is not None else AtomicConfig()
        self.project = project
        self.backend = backend
        self.approval_callback = approval_callback
        self.question_callback = question_callback
        self.capability_factory = capability_factory
        self.resource_limits = resource_limits
        self.changeset: ChangeSet | None = None
        self.cancel_check = lambda: None
        self.task_backend = backend

    @staticmethod
    def _steps(plan: Plan, offset: int = 0) -> list[Step]:
        return [Step(index=offset + i, description=p.description, tools_needed=list(p.tools_needed),
                     expected_outcome=p.expected_outcome, verify_with=p.verify_with,
                     depends_on=[offset + d for d in (p.depends_on if p.depends_on is not None else ([i - 1] if i else []))])
                for i, p in enumerate(plan.steps)]

    def review(self, task: Task, *, allowed_paths: list[str] | None = None) -> dict:
        with task_lease(task.id):
            task = load(task.id)
            return review_payload(task, self.project, allowed_paths)

    def approve(self, task: Task, expected_digest: str, *, allowed_paths: list[str] | None = None) -> Task:
        with task_lease(task.id):
            task = load(task.id)
            self._editable_plan(task)
            review = review_payload(task, self.project, allowed_paths)
            if review["digest"] != expected_digest:
                raise ChangeSetConflict("review digest changed; inspect the current plan, scope and source before approving")
            task.plan_approval = review
            task.error = "plan approved; execution not started"
            save(task)
            return task

    def revise(self, task: Task, plan: Plan) -> Task:
        with task_lease(task.id):
            task = load(task.id)
            self._editable_plan(task)
            completed = [s for s in task.steps if s.status == "done"]
            if completed or task.changeset_id:
                for step in task.steps:
                    if step.status != "done":
                        step.status = "superseded"
                task.steps.extend(self._steps(plan, max((s.index for s in task.steps), default=-1) + 1))
            else:
                task.steps = self._steps(plan)
            task.rationale, task.plan_ready, task.plan_approval = plan.rationale, True, {}
            task.current_index = next((i for i, s in enumerate(task.steps) if s.status == "pending"), len(task.steps))
            task.status, task.error, task.completion_report = "paused", "revised plan requires review and approval", {}
            save(task)
            return task

    @staticmethod
    def _editable_plan(task):
        if task.status in {"done", "failed"} or any(s.status == "running" for s in task.steps):
            raise ChangeSetConflict("active/interrupted side effects or a closed task require recovery, not plan reset")

    @staticmethod
    def _check_effects(task):
        from atomic_flow.sessions import SessionJournal
        journal = SessionJournal(task.id)
        if journal.path.exists():
            snapshot = journal.read()
            if snapshot.incomplete_tail or snapshot.unresolved_call_ids:
                raise UnresolvedEffects("task has unresolved calls or effects; inspect external state before resuming")

    def _hold_effects(self, task):
        from uuid import uuid4

        from atomic_flow.sessions import SessionJournal
        journal = SessionJournal(task.id)
        if not journal.path.exists():
            journal = SessionJournal.create(self.project, task.id, metadata={"kind": "task"})
            journal.append("user_input", {"text": task.query})
        journal.append("tool_effect_unknown", {"reason": "step interrupted with unresolved effects",
                       "step": task.current_index}, call_id="effect_" + uuid4().hex, outcome="unknown")

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
                task.error = "plan ready; review and approval required before execution"
            except BudgetExceeded as exc:
                task.status, task.error = "paused", str(exc)
            except TaskCancelled as exc:
                task.status, task.error = "failed", str(exc)
            except Exception as exc:
                task.status, task.error = "failed", f"planning failed: {type(exc).__name__}"
            save(task)
        return task

    def run_plan(self, query: str, plan: Plan, *, allowed_paths: list[str] | None = None) -> Task:
        steps = self._steps(plan)
        task = Task.new(query=query, project=self.project, steps=steps, rationale=plan.rationale)
        save(task)
        review = self.review(task, allowed_paths=allowed_paths)
        task = self.approve(task, review["digest"], allowed_paths=allowed_paths)
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
            try:
                check_accounting(task)  # Preserve precise charge diagnostics before effect-journal checks.
                self._check_effects(task)
                self.task_backend = TaskBudgetBackend(self.backend, task, self.config, self.cancel_check)
                self.cancel_check()
                if not task.plan_ready:
                    raise BudgetExceeded("planning incomplete; review costs and start a new task")
                # Published checkpoints may only be verified, never replayed; they
                # do not need a new execution authorization for legacy migration.
                recovery_only = (task.changeset_id and ChangeSet.open(self.project, task.changeset_id).state["status"]
                                 in {"published", "publishing", "recovery_conflict"})
                if not recovery_only:
                    try:
                        require_approval(task, self.project)
                    except ChangeSetConflict as exc:
                        task.status, task.error = "paused", str(exc)
                        save(task)
                        return task
                return self._resume_locked(task)
            except (BudgetExceeded, UnresolvedEffects) as exc:
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
                self.changeset = ChangeSet.create(self.project, backend=self.config.task.workspace_backend,
                                                  limits=self.resource_limits, cancel_check=self.cancel_check)
                task.changeset_id = self.changeset.id
            self._refresh_index(self.changeset.workspace, seed_root=self.project)
        except (OSError, ValueError, TreeError) as exc:
            task.status = "failed"
            task.error = str(exc)
            save(task)
            return task
        result = self._resume_steps(task)
        if result.status == "running" and result.current_index == len(result.steps):
            try:
                self._check_effects(result)
                from atomic_flow.agents.completion import assess_completion
                result.completion_report = assess_completion(result, self.changeset, self.config, self.cancel_check)
                save(result)
                if not result.completion_report["ready_to_publish"]:
                    result.status = "paused"
                    result.error = "Final host checks did not pass; workspace retained, nothing published. Inspect completion_report."
                    save(result)
                    return result
                require_scope(result, self.changeset)
                self.changeset.publish(cancel_check=self.cancel_check,
                                       finalize=lambda: self._refresh_index(self.project))
                result.status = "done"
                result.finished_at = time.time()
                result.error = ""
            except UnresolvedEffects as exc:
                result.status, result.error = "paused", str(exc)
            except TaskCancelled:
                raise
            except (OSError, ValueError, TreeError) as exc:
                result.status = "failed"
                result.error = f"publication failed: {exc} (journal: {self.changeset.id})"
        elif result.status == "failed":
            self.changeset.abort()
        save(result)
        return result

    def _refresh_index(self, root: Path, *, seed_root: Path | None = None, recovering=False):
        """Reuse syntax only after hashing current bytes; never reuse read authority.

        The task lease serializes this runner's staged source. A new workspace
        may seed its parse cache from the source index, but build_tree still
        traverses and hashes every admitted file, rebuilds links, and save_tree
        compares the destination's generation before committing.
        """
        with source_mutation_lease(root):
            return self._refresh_index_locked(root, seed_root=seed_root, recovering=recovering)

    def _refresh_index_locked(self, root, *, seed_root, recovering):
        current = None
        try:
            current = load_tree(root)
        except TreeError:
            pass  # Missing/old/corrupt advisory cache is rebuilt from source.
        previous = current
        if previous is None and seed_root is not None:
            try:
                previous = load_tree(seed_root)
            except TreeError:
                pass
        tree = build_tree(root, self.config.tree.ignore, previous=previous,
                          respect_gitignore=self.config.tree.respect_gitignore,
                          limits=self.changeset.limits if self.changeset else self.resource_limits,
                          cancel_check=None if recovering else self.cancel_check)
        if (current is None or tree["fingerprint"] != current.get("fingerprint")
                or tree["reference_format"] != current.get("reference_format")
                or tree["nodes"] != current.get("nodes")):
            save_tree(root, tree)
        return tree

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
            by_id = {s.index: s for s in task.steps}
            runnable = []
            for candidate in task.steps:
                if candidate.status not in {"pending", "blocked"}:
                    continue
                blocked = [d for d in candidate.depends_on if by_id[d].status != "done"]
                candidate.blocked_reason = ("prerequisites not done: " + ", ".join(map(str, blocked))) if blocked else ""
                candidate.status = "blocked" if blocked else "pending"
                if not blocked:
                    runnable.append(candidate)
            if not runnable:
                failed = next((s for s in task.steps if s.status == "failed"), None)
                if failed is not None:
                    return self._replan_failed_step(task, failed)
                unresolved = [s for s in task.steps if s.status not in {"done", "superseded"}]
                if unresolved:
                    task.status, task.error = "paused", "blocked work remains; review prerequisites or revise the plan"
                else:
                    task.current_index = len(task.steps)
                save(task)
                return task
            step = runnable[0]
            task.current_index = task.steps.index(step)
            step.status = "running"
            step.started_at = time.time()
            save(task)
            checkpoint = None
            try:
                checkpoint = self.changeset.checkpoint()
                self._execute_step(task, step)
                require_scope(task, self.changeset)
                self.cancel_check()
                step.status = "done"
                step.result_summary = step.result_summary or "ok"
                step.finished_at = time.time()
                task.current_index = 0
                save(task)
                continue
            except Exception as exc:
                step.status = "failed"
                step.result_summary = f"{type(exc).__name__}: {exc}"[:500]
                step.finished_at = time.time()
                if isinstance(exc, UnresolvedEffects):
                    self._hold_effects(task)  # Durable even when optional artifacts are disabled.
                try:
                    if checkpoint is None:
                        raise ChangeSetConflict(f"checkpoint failed: {exc}")
                    self.changeset.restore_checkpoint(checkpoint)
                    self._refresh_index(self.changeset.workspace, recovering=True)
                except Exception as restore_error:
                    task.status = "failed"
                    task.error = f"workspace rollback failed: {restore_error}"
                    save(task)
                    return task
                save(task)
                if isinstance(exc, UnresolvedEffects):
                    task.status, task.error = "paused", str(exc)
                    save(task)
                    return task
                if isinstance(exc, ChangeSetConflict):
                    task.status, task.error = "failed", step.result_summary
                    save(task)
                    return task
                if isinstance(exc, BudgetExceeded):
                    step.status = "pending"
                    step.started_at = step.finished_at = None
                    task.status, task.error = "paused", str(exc)
                    save(task)
                    return task
                if isinstance(exc, TaskCancelled):
                    raise

            # Finish other independently authorized work before replanning a
            # failed dependency. Blocked descendants remain explicit in state.
            if any(s.status in {"pending", "blocked"} and all(
                    next(d for d in task.steps if d.index == dep).status == "done" for dep in s.depends_on)
                   for s in task.steps):
                task.current_index = 0
                continue

            return self._replan_failed_step(task, step)

        # Completion is recorded only after publishing the workspace successfully.
        save(task)
        return task

    def _replan_failed_step(self, task: Task, step: Step) -> Task:
        """Replan only after all independently runnable work has drained."""
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
                remaining=[f"{s.index + 1}. {s.description}" for s in task.steps
                               if s.index != step.index and s.status not in {"done", "superseded"}],
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

        base_index = max(s.index for s in task.steps) + 1
        for previous in task.steps:
            if previous.status != "done":
                previous.status = "superseded"
        task.steps.extend(self._steps(new_plan, base_index))
        task.rationale = new_plan.rationale
        task.replan_count += 1
        task.current_index = len(task.steps) - len(new_plan.steps)
        task.plan_approval = {}
        task.status, task.error = "paused", "replanned work requires fresh plan review and approval"
        save(task)
        return task


    def _execute_step(self, task: Task, step: Step) -> None:
        subquery = f"[Step {step.index + 1}/{len(task.steps)}] {step.description}"
        workspace = self.changeset.workspace if self.changeset else self.project
        if isinstance(self.task_backend, TaskBudgetBackend):
            self.task_backend.phase = "step"
        capabilities = TaskCapabilities()
        if self.capability_factory is not None:
            context = TaskCapabilityContext(task_id=task.id, project=self.project, workspace=workspace, phase="step")
            capabilities = self.capability_factory(context)
            if not isinstance(capabilities, TaskCapabilities):
                raise StepFailure(step.index, "capability factory must return TaskCapabilities")
        orch = Orchestrator(self.config, workspace, self.task_backend, approval_callback=self.approval_callback,
                            settings_project=self.project, cancel_check=self.cancel_check,
                            question_callback=self.question_callback, private_workspace=self.changeset is not None,
                            mutation_scope=require_approval(task, self.project),
                            workspace_session=capabilities.workspace_session, lsp_client=capabilities.lsp_client,
                            documentation_client=capabilities.documentation_client, hooks=capabilities.hooks,
                            tool_registry=capabilities.tool_registry, role_tools=capabilities.role_tools)
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
                if path not in require_approval(task, self.project):
                    raise ChangeSetConflict("mutation path exceeds reviewed plan scope")
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
                if call.get("tool") == "notebook_edit" and summary.get("status") == "preview":
                    continue
                if summary.get("status") != "applied" or not summary.get("checks"):
                    raise StepFailure(step.index, "edit lacks applied status and syntax verification")
                if call.get("tool") == "edit_node" and "syntax" not in summary.get("checks", []):
                    raise StepFailure(step.index, "edit lacks syntax verification")
                if call.get("tool") == "notebook_edit" and summary.get("verification", {}).get("notebook_schema") != "passed":
                    raise StepFailure(step.index, "notebook edit lacks host structural verification")
                edits.append(summary)
                last_edit = i
            if call.get("tool") == "run_command" and (
                type(summary.get("exit_code")) is not int or summary["exit_code"] != 0
            ):
                raise StepFailure(step.index, f"command failed or missing exit_code: {summary.get('exit_code')}")

        if getattr(result, "verdict", "unverified") in {"wrong_answer", "needs_retry", "needs_more_context"}:
            raise StepFailure(step.index, f"Unresolved step critique: {result.verdict}")
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
