"""TaskRunner: execute a plan step-by-step, verifying each step."""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.control import check_cancel, task_lease
from atomic_flow.agents.effects import UnresolvedEffects
from atomic_flow.agents.envelope import envelope_preserved, failure_signature, make_envelope, no_progress, runtime_digest
from atomic_flow.agents.execution import TaskCancelled, run_python
from atomic_flow.agents.failure_policy import classify
from atomic_flow.agents.goals import Goal, goal_context, replan_requirements, step_scope, validate_goals
from atomic_flow.agents.plan_review import require_approval, require_scope, review_payload
from atomic_flow.agents.planner import Plan, plan_task, replan_task
from atomic_flow.agents.repair_budget import begin_repair, check_repair_budget
from atomic_flow.agents.replan_constraints import inherit_constraints
from atomic_flow.agents.replan_lineage import lineage_preserved
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend, check_accounting
from atomic_flow.config import AtomicConfig, user_dir
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
    kind: str = "unclassified"
    evidence: dict = field(default_factory=dict)


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
        self._read_plan_index = None
        self._read_plan_key = None
        self.read_plan_report: dict = {}
        self._step_tool_calls: dict[int, list] = {}

    @staticmethod
    def _steps(plan: Plan, offset: int = 0) -> list[Step]:
        return [Step(index=offset + i, description=p.description, tools_needed=list(p.tools_needed),
                     expected_outcome=p.expected_outcome, verify_with=p.verify_with,
                     parent_goal=f"p{offset}:{p.parent_goal}" if p.parent_goal else "",
                     acceptance_criteria=list(p.acceptance_criteria),
                     scope_paths=list(p.scope_paths), linked_code_nodes=list(p.linked_code_nodes),
                     linked_test_nodes=list(p.linked_test_nodes),
                     depends_on=[offset + d for d in (p.depends_on if p.depends_on is not None else ([i - 1] if i else []))])
                for i, p in enumerate(plan.steps)]

    @staticmethod
    def _goals(plan: Plan, offset: int = 0) -> list[Goal]:
        return [Goal(f"p{offset}:{g.id}", g.description,
                     f"p{offset}:{g.parent_id}" if g.parent_id else "", list(g.acceptance_criteria),
                     deepcopy(g.scope_paths), g.max_model_calls, g.max_budget_usd)
                for g in plan.goals]

    def _review(self, task, allowed_paths, allow_internal_replan):
        if type(allow_internal_replan) is not bool:
            raise ValueError("allow_internal_replan must be an explicit boolean")
        if allow_internal_replan and self.capability_factory is not None:
            raise ValueError("internal replans do not support injected capabilities in this version")
        envelope = make_envelope(task, self.config, self.project, self.backend) if allow_internal_replan else None
        return review_payload(task, self.project, allowed_paths, internal_replan=envelope)

    def review(self, task: Task, *, allowed_paths: list[str] | None = None, allow_internal_replan: bool = False) -> dict:
        with task_lease(task.id):
            task = load(task.id)
            return self._review(task, allowed_paths, allow_internal_replan)

    def approve(self, task: Task, expected_digest: str, *, allowed_paths: list[str] | None = None,
                allow_internal_replan: bool = False) -> Task:
        with task_lease(task.id):
            task = load(task.id)
            self._editable_plan(task)
            review = self._review(task, allowed_paths, allow_internal_replan)
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
                offset = max((s.index for s in task.steps), default=-1) + 1
                new_steps, new_goals = self._steps(plan, offset), self._goals(plan, offset)
                validate_goals(task.goals + new_goals, task.steps + new_steps)
                for step in task.steps:
                    if step.status != "done":
                        step.status = "superseded"
                task.steps.extend(new_steps)
                task.goals.extend(new_goals)
            else:
                task.steps = self._steps(plan)
                task.goals = self._goals(plan)
            task.rationale, task.plan_ready, task.plan_approval = plan.rationale, True, {}
            task.current_index = next((i for i, s in enumerate(task.steps) if s.status == "pending"), len(task.steps))
            task.status, task.error, task.completion_report = "paused", "revised plan requires review and approval", {}
            save(task)
            return task

    @staticmethod
    def _editable_plan(task):
        if task.status in {"done", "failed", "cancelled"} or any(s.status == "running" for s in task.steps):
            raise ChangeSetConflict("active/interrupted side effects or a closed task require recovery, not plan reset")

    def expand_plan(self, task: Task, index: int, plan: Plan) -> Task:
        """Refine one pending step under lease; execution always needs new review."""
        from atomic_flow.agents.rolling_plan import propose_expansion

        with task_lease(task.id):
            task = load(task.id)
            if Path(task.project).resolve() != self.project.resolve():
                raise ChangeSetConflict("task belongs to another project")
            self._editable_plan(task)
            check_accounting(task)
            self._check_effects(task)
            if task.changeset_id:
                change = ChangeSet.open(self.project, task.changeset_id)
                if change.state["status"] != "active":
                    raise ChangeSetConflict("closed changeset cannot be expanded")
                change.check_original()
                change.validate_workspace()
            expanded = propose_expansion(task, index, plan)
            save(expanded)
            return expanded

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
                task.goals = self._goals(plan)
                task.rationale = plan.rationale
                task.plan_ready = True
                task.status = "paused"
                task.error = "plan ready; review and approval required before execution"
            except BudgetExceeded as exc:
                task.status, task.error = "paused", str(exc)
            except TaskCancelled as exc:
                task.status, task.error = "cancelled", str(exc)
            except Exception as exc:
                task.status, task.error = "failed", f"planning failed: {type(exc).__name__}"
            save(task)
        return task

    def run_plan(self, query: str, plan: Plan, *, allowed_paths: list[str] | None = None,
                 allow_internal_replan: bool = False) -> Task:
        steps = self._steps(plan)
        task = Task.new(query=query, project=self.project, steps=steps, rationale=plan.rationale)
        task.goals = self._goals(plan)
        save(task)
        review = self.review(task, allowed_paths=allowed_paths, allow_internal_replan=allow_internal_replan)
        task = self.approve(task, review["digest"], allowed_paths=allowed_paths, allow_internal_replan=allow_internal_replan)
        return self.resume(task)

    def _sync_graph(self, task: Task, changed_paths: list[str]) -> None:
        """P06/P02 hot path: delta-sync the structural graph after publish.

        The published source is already final; a graph problem must never
        flip a completed publication into a failure — sync_project_graph
        degrades to a reasoned fallback and records diagnostics itself.
        """
        try:
            from atomic_flow.graph.sync import sync_project_graph

            changed = {path: (self.project / path).read_bytes()
                       if (self.project / path).is_file() else None
                       for path in changed_paths}
            report = sync_project_graph(self.project, changed, cancel_check=self.cancel_check)
            task.graph_sync = {"deltas": report.get("deltas", 0),
                               "created": report.get("created", False),
                               "fallback": bool(report.get("fallback")),
                               "failed": bool(report.get("failed"))}
        except Exception as exc:  # publication already succeeded; record only
            task.graph_sync = {"failed": True, "error_type": type(exc).__name__}

    def invalidate_step(self, task_id: str, index: int, reason: str) -> Task:
        """Host-explicit requeue of a done step (roadmap P05).

        Re-running succeeded work requires an explicit, recorded reason. The
        invalidation cascades to done steps whose premises (transitive
        depends_on) include the target. Published/completed tasks are refused:
        their external effects must never be replayed; start a new task.
        """
        if not reason or not reason.strip():
            raise ChangeSetConflict("step invalidation requires an explicit reason")
        with task_lease(task_id):
            task = load(task_id)
            if Path(task.project).resolve() != self.project.resolve():
                raise ChangeSetConflict("task belongs to another project")
            if task.status in {"done", "cancelled"}:
                raise ChangeSetConflict("completed task is immutable; create a new task instead of replay")
            if task.changeset_id:
                state = ChangeSet.open(self.project, task.changeset_id).state["status"]
                if state in {"published", "publishing", "recovery_conflict"}:
                    raise ChangeSetConflict("published effects cannot be replayed; create a new task")
            by_index = {step.index: step for step in task.steps}
            if index not in by_index:
                raise ChangeSetConflict(f"unknown step index: {index}")
            if by_index[index].status != "done":
                raise ChangeSetConflict(f"step {index} is {by_index[index].status}; only done steps are invalidated")

            dependents: set[int] = set()
            frontier = [index]
            while frontier:
                current = frontier.pop()
                for step in task.steps:
                    if step.index in dependents or step.status != "done":
                        continue
                    if current in step.depends_on:
                        dependents.add(step.index)
                        frontier.append(step.index)
            targets = sorted({index, *dependents})
            for target in targets:
                step = by_index[target]
                step.status = "pending"
                step.blocked_reason = ""
                step.invalidated_reason = f"step {index}: {reason.strip()}"
                step.result_summary = ""
                step.request_id = ""
                step.evidence = {}
                step.started_at = step.finished_at = None
            task.current_index = min(task.current_index, targets[0])
            if task.status in {"failed", "paused"}:
                task.status, task.error = "paused", ""
            save(task)
            return task

    def resume(self, task: Task) -> Task:
        with task_lease(task.id):
            try:
                task = load(task.id)
            except FileNotFoundError:
                save(task)
            self.changeset = None
            if task.status in {"done", "failed", "cancelled"}:
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
                        self._require_replan_runtime(task)
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
                task.status = "cancelled"
                for step in task.steps:
                    if step.status in {"pending", "blocked", "running", "failed"}:
                        step.status = "cancelled"
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
            indexed = self._refresh_index(self.changeset.workspace, seed_root=self.project)
            task.tree_fingerprint = indexed["fingerprint"]
        except (OSError, ValueError, TreeError) as exc:
            task.status = "failed"
            task.error = str(exc)
            save(task)
            return task
        result = self._resume_steps(task)
        if result.status == "running" and result.current_index == len(result.steps):
            try:
                check_repair_budget(result)
                self._check_effects(result)
                self._require_replan_runtime(result)
                from atomic_flow.agents.completion import assess_completion
                result.completion_report = assess_completion(result, self.changeset, self.config, self.cancel_check)
                save(result)
                if not result.completion_report["ready_to_publish"]:
                    result.status = "paused"
                    result.error = "Final host checks did not pass; workspace retained, nothing published. Inspect completion_report."
                    save(result)
                    return result
                require_scope(result, self.changeset)
                changed_paths = sorted(self.changeset.validate_workspace())
                self.changeset.publish(cancel_check=self.cancel_check,
                                       finalize=lambda: self._refresh_index(self.project))
                self._sync_graph(result, changed_paths)
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
            check_repair_budget(task)
            try:
                self._require_replan_runtime(task)
            except ChangeSetConflict as exc:
                task.status, task.error = "paused", str(exc)
                save(task)
                return task
            if (self.config.task.fail_on_budget_exceeded and self.config.task.max_budget_usd > 0
                    and task.budget_spent >= self.config.task.max_budget_usd):
                task.status = "paused"
                task.error = "task budget exhausted before next step"
                save(task)
                return task
            from atomic_flow.agents.frontier import task_frontier

            frontier = task_frontier(task)
            if frontier["running"]:
                task.status = "paused"
                task.error = "unfinished running step requires reconciliation before scheduling"
                save(task)
                return task
            ready = set(frontier["ready"])
            waiting = {item["index"]: item["waiting_for"] for item in frontier["blocked"]}
            runnable = []
            for candidate in task.steps:
                if candidate.status not in {"pending", "blocked"}:
                    continue
                blocked = waiting.get(candidate.index, [])
                candidate.blocked_reason = ("prerequisites not done: " + ", ".join(map(str, blocked))) if blocked else ""
                candidate.status = "blocked" if blocked else "pending"
                if candidate.index in ready:
                    runnable.append(candidate)
            if not runnable:
                failed = next((s for s in task.steps if s.status == "failed"), None)
                if failed is not None:
                    result = self._replan_failed_step(task, failed)
                    if result.status == "running":
                        continue
                    return result
                unresolved = [s for s in task.steps if s.status not in {"done", "superseded"}]
                if unresolved:
                    task.status, task.error = "paused", "blocked work remains; review prerequisites or revise the plan"
                else:
                    task.current_index = len(task.steps)
                save(task)
                return task
            step = runnable[0]
            # A receipt is provenance, never authority. Changed prerequisites
            # pause for explicit invalidation/review instead of action replay.
            from atomic_flow.agents.step_evidence import receipt_matches
            manifest = self.changeset.validate_workspace()
            for dependency in step.depends_on:
                premise = next(s for s in task.steps if s.index == dependency)
                if premise.evidence:
                    matches, reasons = receipt_matches(premise.evidence, premise, manifest, task_id=task.id,
                                                       approved_scope=step_scope(task, premise, require_approval(task, self.project)))
                    if not matches or premise.evidence["verification"]["outcome"] in {"failed", "unavailable"}:
                        task.status, task.error = "paused", f"step {dependency} evidence needs explicit invalidation/review: {reasons}"
                        save(task)
                        return task
            task.current_index = task.steps.index(step)
            step.status = "running"
            step.evidence = {}
            step.failure = {}
            step.started_at = time.time()
            save(task)
            checkpoint = None
            try:
                checkpoint = self.changeset.checkpoint()
                self.task_backend.bind_steps([step.index])
                effective_scope = step_scope(task, step, require_approval(task, self.project))
                self._execute_step(task, step)
                require_scope(task, self.changeset)
                after_manifest = self.changeset.validate_workspace()
                step_delta = {path for path in checkpoint["files"].keys() | after_manifest.keys()
                              if checkpoint["files"].get(path) != after_manifest.get(path)}
                if step_delta - effective_scope:
                    raise ChangeSetConflict("step mutations exceed reviewed goal scope")
                self.cancel_check()
                indexed = self._refresh_index(self.changeset.workspace)
                task.tree_fingerprint = indexed["fingerprint"]
                if self.changeset.validate_workspace() != after_manifest:
                    raise ChangeSetConflict("workspace changed while binding step evidence")
                self._record_step_evidence(task, step, checkpoint["files"], after_manifest, effective_scope)
                step.status = "done"
                step.result_summary = step.result_summary or "ok"
                step.finished_at = time.time()
                task.current_index = 0
                save(task)
                continue
            except Exception as exc:
                step.status = "failed"
                message = exc.message if isinstance(exc, StepFailure) else str(exc)
                step.result_summary = f"{type(exc).__name__}: {message}"[:500]
                step.finished_at = time.time()
                failure = ({"kind": exc.kind, "evidence": deepcopy(exc.evidence)} if isinstance(exc, StepFailure)
                           else {"kind": "unclassified", "evidence": {}})
                if failure.get("evidence"):
                    from atomic_flow.agents.step_evidence import manifest_digest
                    proof = failure["evidence"]
                    if (proof.get("step_index") != step.index or proof.get("started_at") != step.started_at
                            or proof.get("source_revision") != manifest_digest(self.changeset.validate_workspace())):
                        failure = {"kind": "source_conflict", "evidence": {}}
                if isinstance(exc, UnresolvedEffects):
                    self._hold_effects(task)  # Durable even when optional artifacts are disabled.
                try:
                    if checkpoint is None:
                        raise ChangeSetConflict(f"checkpoint failed: {exc}")
                    self.changeset.restore_checkpoint(checkpoint)
                    self._refresh_index(self.changeset.workspace, recovering=True)
                except Exception as restore_error:
                    task.status = "failed"
                    task.error = f"workspace rollback failed: {type(restore_error).__name__}: {restore_error}"
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
                from atomic_flow.agents.step_evidence import manifest_digest
                failure["rollback_revision"] = manifest_digest(self.changeset.validate_workspace())
                step.failure = failure
                remaining = self.config.task.max_budget_usd - task.budget_spent if self.config.task.max_budget_usd > 0 else None
                decision = classify({**failure, "remaining_budget_usd": remaining})
                task.last_failure_decision = decision
                save(task)
                if decision["action"] != "continue_bounded_repair":
                    task.status, task.error = "paused", (
                        f"failure policy: {decision.get('reason') or decision['action']}; "
                        f"step {step.index} outcome: {step.result_summary[:200]}")
                    save(task)
                    return task

            # Finish other independently authorized work before replanning a
            # failed dependency. Blocked descendants remain explicit in state.
            if task_frontier(task)["ready"]:
                task.current_index = 0
                continue

            result = self._replan_failed_step(task, step)
            if result.status == "running":
                continue
            return result

        # Completion is recorded only after publishing the workspace successfully.
        save(task)
        return task

    def _require_replan_runtime(self, task):
        envelope = task.plan_approval.get("internal_replan")
        if envelope is None:
            return
        try:
            if self.capability_factory is not None or runtime_digest(self.config, self.project, self.backend) != envelope["runtime_digest"]:
                raise ValueError("runtime, policy, trust or capabilities changed")
        except (OSError, ValueError, KeyError) as exc:
            raise ChangeSetConflict(f"internal replan runtime binding changed: {exc}; fresh review required") from exc

    def _replan_failed_step(self, task: Task, step: Step) -> Task:
        """Replan only after all independently runnable work has drained."""
        from atomic_flow.agents.step_evidence import manifest_digest
        remaining = self.config.task.max_budget_usd - task.budget_spent if self.config.task.max_budget_usd > 0 else None
        decision = classify({**step.failure, "remaining_budget_usd": remaining})
        if (decision["action"] != "continue_bounded_repair"
                or step.failure.get("rollback_revision") != manifest_digest(self.changeset.validate_workspace())):
            task.status, task.error = "paused", "repair requires fresh host failing-check evidence and unchanged rollback source"
            save(task)
            return task
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
            self._check_effects(task)
            check_accounting(task)
            require_scope(task, self.changeset)
            self._require_replan_runtime(task)
        except (ChangeSetConflict, BudgetExceeded, UnresolvedEffects) as exc:
            task.status, task.error = "paused", str(exc)
            save(task)
            return task
        previous_approval = deepcopy(task.plan_approval)
        autonomous = previous_approval.get("schema_version") == 2
        if autonomous:
            envelope = previous_approval["internal_replan"]
            signature = failure_signature(step.result_summary)
            if signature and envelope["replan_failures"].count(signature) < 2:
                envelope["replan_failures"].append(signature)
            if no_progress(previous_approval, step.result_summary):
                task.plan_approval = review_payload(task, self.project, previous_approval["allowed_paths"],
                                                    internal_replan=envelope)
                task.status, task.error = "paused", "no-progress guard: recurring failure requires fresh review"
                save(task)
                return task
        try:
            requirements_context = replan_requirements(task.goals, task.steps)
        except ValueError as exc:
            task.status, task.error = "paused", str(exc)
            save(task)
            return task
        try:
            self.task_backend.phase = "replan"
            begin_repair(task, decision["repair_budget_usd"], step.index)
            task.last_failure_decision = {**decision, "effective_repair_cap_usd": task.repair_budget["cap_usd"],
                                          "repair_id": task.repair_budget["id"]}
            save(task)
            self.task_backend.bind_steps([s.index for s in task.steps if s.status not in {"done", "superseded"}])
            import json

            from atomic_flow.agents.step_evidence import manifest_digest
            planning_context = json.dumps({
                "workspace_revision": manifest_digest(self.changeset.validate_workspace()),
                "graph_revision": task.tree_fingerprint, "context_revision": self.read_plan_report.get("revision", ""),
                "remaining_model_calls": max(0, self.config.task.max_model_calls - sum(
                    not e.get("not_sent") for e in task.budget_events)),
                "budget_spent_usd": task.budget_spent,
                "task_budget_cap_usd": self.config.task.max_budget_usd,
                "observations": [{"index": s.index, "status": s.status, "receipt": s.evidence.get("digest", "")}
                                 for s in task.steps][-16:]}, ensure_ascii=False)
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
                requirements_context=requirements_context,
                planning_context=planning_context,
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
        new_steps = self._steps(new_plan, base_index)
        if len(task.steps) + len(new_steps) > 128:
            task.status, task.error = "paused", "task history exceeds 128 steps; start a reviewed continuation"
            save(task)
            return task
        new_goals = self._goals(new_plan, base_index)
        constraint_reasons = []
        if autonomous:
            new_goals, constraint_reasons = inherit_constraints(task, new_goals, new_steps)
        try:
            validate_goals(task.goals + new_goals, task.steps + new_steps)
        except ValueError as exc:
            task.status, task.error = "paused", f"replan goal bounds require fresh plan: {exc}"
            save(task)
            return task
        accepted, reasons = envelope_preserved(previous_approval, new_steps, failure=step.result_summary,
                                               budget_spent=task.budget_spent)
        if constraint_reasons:
            accepted = False
            reasons.extend(constraint_reasons)
        if task.goals or new_goals or any(s.acceptance_criteria or s.scope_paths or s.linked_code_nodes or s.linked_test_nodes
                                        for s in task.steps + new_steps):
            # Formal lineage gate (ADR-0001): obligations may be carried by a
            # replacement plan only when the pure predicate proves nothing is
            # lost; anything else still needs fresh review.
            envelope = previous_approval.get("internal_replan") or {}
            obligations = {
                "criteria": sorted({criterion for holder in [*task.goals, *new_goals, *task.steps, *new_steps]
                                    for criterion in getattr(holder, "acceptance_criteria", ())}),
                "scope_paths": previous_approval.get("allowed_paths"),
                "max_budget_usd": envelope.get("max_budget_usd"),
                "required_checks": {envelope["required_verify"]} if envelope.get("required_verify") else set(),
            }
            lineage_ok, lineage_reasons = lineage_preserved(obligations, [
                {"criteria": list(s.acceptance_criteria), "scope_paths": s.scope_paths or None,
                 "checks": [s.verify_with], "budget_usd": None} for s in new_steps])
            if not lineage_ok:
                accepted = False
                reasons.extend(lineage_reasons)
        for previous in task.steps:
            if previous.status != "done":
                previous.status = "superseded"
        task.steps.extend(new_steps)
        task.goals.extend(new_goals)
        task.rationale = new_plan.rationale
        task.replan_count += 1
        task.current_index = len(task.steps) - len(new_plan.steps)
        task.plan_approval = {}
        task.status, task.error = "paused", "replanned work requires fresh plan review and approval"
        if autonomous and accepted:
            try:
                self.cancel_check()
                check_accounting(task)
                self._check_effects(task)
                with source_mutation_lease(self.project):
                    review = review_payload(task, self.project, previous_approval["allowed_paths"],
                                            internal_replan=previous_approval["internal_replan"])
                    if any(review[key] != previous_approval[key] for key in ("source_manifest", "scope_hashes", "allowed_paths")):
                        raise ChangeSetConflict("source or path scope changed during replan")
                    task.plan_approval = review
                    self._require_replan_runtime(task)
                    require_scope(task, self.changeset)
                task.status, task.error = "running", ""
            except (OSError, ValueError, BudgetExceeded, UnresolvedEffects) as exc:
                task.plan_approval = {}
                task.error = f"internal replan requires fresh review: {exc}"
        elif autonomous:
            task.error = "internal replan requires fresh review: " + "; ".join(reasons)
        save(task)
        return task


    def _read_plan(self, task: Task, workspace, step: Step | None = None) -> str:
        """Host-computed advisory read order for the approved paths (P04).

        Bounded, deterministic, model-free; graph problems degrade to an
        explicit notice instead of silently skipping the plan.
        """
        try:
            from atomic_flow.graph.adapter_ast import EXTRACTOR
            from atomic_flow.graph.impact import ContextPackIndex, context_pack_from_index, select_goals
            from atomic_flow.graph.project import project_files

            paths = require_approval(task, self.project)
            limits = self.changeset.limits if self.changeset else self.resource_limits or ProjectLimits()
            key = (task.id, str(workspace.resolve()), tuple(sorted(paths)), EXTRACTOR,
                   tuple(self.config.tree.ignore), self.config.tree.respect_gitignore, limits, self.changeset is not None)
            if key != self._read_plan_key:
                self._read_plan_index, self._read_plan_key = ContextPackIndex(), key
            files = project_files(workspace, tree_ignore=self.config.tree.ignore,
                                  respect_gitignore=self.config.tree.respect_gitignore,
                                  private_workspace=self.changeset is not None, limits=limits,
                                  cancel_check=self.cancel_check)
            self._read_plan_index.update(files, cancel_check=self.cancel_check, max_nodes=limits.max_nodes)
            description = (step.description + " " + step.expected_outcome) if step is not None else getattr(task, "query", "")
            goals, omitted_goals = select_goals(self._read_plan_index, paths, description)
            # The index was already reconciled above, so pack construction must
            # preserve those counters instead of claiming a second warm update.
            stats = dict(self._read_plan_index.stats)
            if not goals:
                self.read_plan_report = {"revision": self._read_plan_index.revision, "stats": stats, "goals": [],
                                         "reason": "no step-specific approved Python target matched"}
                return "Host read plan: no step-specific approved Python target matched; search admitted source before editing."
            pack = context_pack_from_index(self._read_plan_index, goals, cancel_check=self.cancel_check)
            self.read_plan_report = {"revision": pack["revision"], "stats": stats, "goals": goals,
                                     "omitted_goal_candidates": omitted_goals, "parse_failures": pack["parse_failures"]}
            lines = ["Host read plan (advisory, name-level; read goal files completely, "
                     "following read_file/read_node next_offset, before any edit):"]
            for entry in pack["entries"][:12]:
                note = " [over byte cap: read selectively]" if entry.get("truncated") else ""
                lines.append(f"- {entry['file']} ({entry['bytes']} bytes) - {entry['reason']}{note}")
            omitted = max(0, len(pack["entries"]) - 12)
            if omitted or omitted_goals or pack["parse_failures"]:
                lines.append(f"Partial advisory plan: {omitted} entries / {omitted_goals} goal candidates omitted; "
                             f"{len(pack['parse_failures'])} parse failures. Use source tools; this is not read proof.")
            text = "\n".join(lines)
            return text if len(text) <= 2000 else text[:1997] + "\n..."
        except TaskCancelled:
            self._read_plan_index = self._read_plan_key = None
            self.read_plan_report = {"reason": "cancelled"}
            raise
        except Exception as exc:  # advisory only; no partial cache may survive
            self._read_plan_index = self._read_plan_key = None
            self.read_plan_report = {"reason": type(exc).__name__}
            return f"Host read plan unavailable ({type(exc).__name__}); read approved files completely before editing."

    def explain_next(self, task_id: str, *, horizon: int = 3) -> dict:
        """Read-only structural readiness, never an execution authorization."""
        from atomic_flow.agents.frontier import task_frontier

        task = load(task_id)
        if Path(task.project).resolve() != self.project.resolve():
            raise ChangeSetConflict("task belongs to another project")
        view = task_frontier(task, horizon=horizon)
        view["graph_revision"] = task.tree_fingerprint
        from atomic_flow.agents.changeset import _files
        from atomic_flow.agents.step_evidence import receipt_matches
        manifest = (ChangeSet.open(self.project, task.changeset_id).validate_workspace()
                    if task.changeset_id and task.status not in {"done", "failed", "cancelled"}
                    else _files(self.project, user_dir().resolve()))
        view["evidence"] = [{"index": s.index, "current": receipt_matches(s.evidence, s, manifest, task_id=task.id,
                             approved_scope=step_scope(task, s, task.plan_approval.get("allowed_paths", []))
                             if task.plan_approval else None)[0],
                             "receipt": s.evidence.get("digest", ""), "legacy_missing": not bool(s.evidence)}
                            for s in task.steps if s.status == "done"]
        return view

    def _record_step_evidence(self, task, step, before_manifest, after_manifest, effective_scope):
        from atomic_flow.agents.step_evidence import build_receipt
        calls = self._step_tool_calls.pop(step.index, [])
        # Retrieval skeletons, role context and host auto-pytest can observe
        # files absent from tool transcripts. Until coverage is host-proven,
        # bind the whole snapshot; declared node links cannot narrow it.
        step.evidence = build_receipt(task.id, step, before_manifest=before_manifest, after_manifest=after_manifest,
                                      approved_scope=effective_scope, tool_calls=calls,
                                      verification_outcome="not_run" if step.verify_with == "none" else "passed",
                                      graph_revision=task.tree_fingerprint,
                                      context_revision=self.read_plan_report.get("revision", ""),
                                      premise_paths=None)

    def _execute_step(self, task: Task, step: Step) -> None:
        from atomic_flow.agents.frontier import frontier_context

        base_query = f"[Step {step.index + 1}/{len(task.steps)}] {step.description}"
        workspace = self.changeset.workspace if self.changeset else self.project
        subquery = base_query
        subquery += "\n" + frontier_context(task)
        if step.parent_goal or step.acceptance_criteria:
            import json
            requirements = {"ancestor_goals": goal_context(task.goals, step),
                            "step_acceptance_criteria": step.acceptance_criteria}
            subquery += "\nReviewed requirements (criteria are not proof of completion):\n" + json.dumps(requirements, ensure_ascii=False)
        plan_text = self._read_plan(task, workspace, step)
        if plan_text:
            # The plan rides in the model-visible query only; retrieval keeps
            # ranking on the pristine step description so context selection
            # does not shift because of the advisory text.
            subquery += "\n" + plan_text
        if isinstance(self.task_backend, TaskBudgetBackend):
            self.task_backend.phase = "step"
        capabilities = TaskCapabilities()
        if self.capability_factory is not None:
            context = TaskCapabilityContext(task_id=task.id, project=self.project, workspace=workspace, phase="step")
            capabilities = self.capability_factory(context)
            if not isinstance(capabilities, TaskCapabilities):
                raise StepFailure(step.index, "capability factory must return TaskCapabilities", "verification_contract_failure")
        envelope = task.plan_approval.get("internal_replan")
        if envelope is not None:
            from atomic_flow.agents.tools import ROLE_TOOLS
            allowed = set(envelope["allowed_tools"])
            capabilities.role_tools = {role: [tool for tool in tools if tool in allowed]
                                       for role, tools in ROLE_TOOLS.items()}
        effective_scope = step_scope(task, step, require_approval(task, self.project))
        orch = Orchestrator(self.config, workspace, self.task_backend, approval_callback=self.approval_callback,
                            settings_project=self.project, cancel_check=self.cancel_check,
                            question_callback=self.question_callback, private_workspace=self.changeset is not None,
                            mutation_scope=effective_scope,
                            workspace_session=capabilities.workspace_session, lsp_client=capabilities.lsp_client,
                            documentation_client=capabilities.documentation_client, hooks=capabilities.hooks,
                            tool_registry=capabilities.tool_registry, role_tools=capabilities.role_tools)
        result = orch.answer(subquery, mode="medium", approve_edits=True, retrieval_query=base_query)
        self._step_tool_calls[step.index] = result.tool_calls or []
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
                if path not in effective_scope:
                    raise ChangeSetConflict("mutation path exceeds reviewed plan scope")
                self.changeset.track(path, expected_hash)
        self._verify_step(step, result)

    def _check_failure(self, step: Step, message: str, check: str, exit_code: int, *, checked_revision=None) -> StepFailure:
        """Trusted host check result for this attempt; never inferred from text."""
        from atomic_flow.agents.step_evidence import manifest_digest
        if type(exit_code) is not int or exit_code != 1 or check not in {"syntax", "pytest", "ruff", "mypy"}:
            return StepFailure(step.index, message, "check_unavailable")
        if self.changeset is None or step.started_at is None:
            return StepFailure(step.index, message, "verification_contract_failure")
        evidence = {"version": 1, "check": check, "exit_code": exit_code, "step_index": step.index,
                    "started_at": step.started_at, "outcome": "failed",
                    "source_revision": manifest_digest(self.changeset.validate_workspace())}
        evidence["checked_source_revision"] = checked_revision or evidence["source_revision"]
        return StepFailure(step.index, message,
                           "deterministic_test_failure" if check == "pytest" else "deterministic_check_failure", evidence)

    def _verify_step(self, step: Step, result) -> None:
        calls = result.tool_calls or []
        if step.verify_with not in {"none", "py_compile", "pytest"}:
            raise StepFailure(step.index, f"unknown verification method: {step.verify_with}", "verification_contract_failure")
        edits = []
        last_edit = -1
        last_mutation = max((i for i, call in enumerate(calls) if call.get("tool") in MUTATING_TOOLS), default=-1)
        for i, call in enumerate(calls):
            summary = call.get("result_summary")
            if not isinstance(summary, dict) or not summary:
                raise StepFailure(step.index, f"{call.get('tool')}: missing tool result")
            if summary.get("check_source_changed"):
                raise StepFailure(step.index, "source changed during host check", "source_conflict")
            if summary.get("check_timed_out") or summary.get("timed_out"):
                raise StepFailure(step.index, "host check timed out", "check_timeout")
            if summary.get("status") == "rolled_back":
                failed = [check for check in ("syntax", "pytest") if summary.get("verification", {}).get(check) == "failed"]
                if failed and i >= last_mutation:
                    code = 1 if failed[-1] == "syntax" else summary.get("check_exit_code")
                    if code == 1 and not summary.get("checked_source_revision"):
                        raise StepFailure(step.index, "host check lacks source revision", "verification_contract_failure")
                    raise self._check_failure(step, "host check failed; edit rolled back", failed[-1], code,
                                              checked_revision=summary.get("checked_source_revision"))
                raise StepFailure(step.index, f"edit rolled back: {summary.get('reason', 'verification failed')}")
            if (call.get("tool") == "run_command" and type(summary.get("exit_code")) is int
                    and summary["exit_code"] != 0 and summary.get("outcome") != "denied"):
                if i >= last_mutation:
                    from atomic_flow.agents.step_evidence import manifest_digest
                    if (not self.changeset or summary.get("checked_source_revision") !=
                            manifest_digest(self.changeset.validate_workspace())):
                        raise StepFailure(step.index, "host check source revision is missing or stale", "source_conflict")
                    raise self._check_failure(step, "host command check failed", call.get("args", {}).get("command"), summary["exit_code"],
                                              checked_revision=summary["checked_source_revision"])
                raise StepFailure(step.index, "failing command predates a later mutation", "verification_contract_failure")
            if summary.get("outcome") in {"error", "denied"} or summary.get("error"):
                kind = "scope_violation_detected" if summary.get("outcome") == "denied" else "tool_precondition_failure"
                raise StepFailure(step.index, f"{call.get('tool')}: {summary.get('error') or summary['outcome']}", kind)
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
                if i >= last_mutation:
                    raise self._check_failure(step, "host command check failed", call.get("args", {}).get("command"), summary.get("exit_code"))
                raise StepFailure(step.index, "failing command predates a later mutation", "verification_contract_failure")

        if getattr(result, "verdict", "unverified") in {"wrong_answer", "needs_retry", "needs_more_context"}:
            raise StepFailure(step.index, f"Unresolved step critique: {result.verdict}", "model_critique")
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

            before_check = self.changeset.validate_workspace() if self.changeset else None
            proc = run_python(["-m", "pytest", "-q", "--no-header", "-x"],
                              self.changeset.workspace if self.changeset else self.project,
                              config=self.config.execution, timeout=180, cancel_check=self.cancel_check)
            if self.changeset and self.changeset.validate_workspace() != before_check:
                raise StepFailure(step.index, "source changed during pytest", "source_conflict")
            if proc.timed_out:
                raise StepFailure(step.index, "pytest timed out after 180s", "check_timeout")
            if proc.returncode != 0:
                out = (proc.stdout + proc.stderr)[-1500:]
                raise self._check_failure(step, f"pytest exit_code={proc.returncode}\n{out}", "pytest", proc.returncode)
            step.result_summary = "ok (pytest auto-run by TaskRunner)"
            return
