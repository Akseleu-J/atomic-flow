"""Flat baseline on the normal TaskRunner safety, repair and publication path.

No Orchestrator/parser/retrieval/critic/model planner is used. Internal sequential
steps record host attempt boundaries only. The existing stale-after-rollback gate
remains unchanged: stale or incomplete failures cannot authorize continuation.
"""
from __future__ import annotations

import json
import uuid

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.effects import EffectState, UnresolvedEffects
from atomic_flow.agents.goals import goal_context, step_scope
from atomic_flow.agents.plan_review import require_approval
from atomic_flow.agents.planner import Plan, PlanGoal, PlanStep
from atomic_flow.agents.policy import PolicyEngine
from atomic_flow.agents.result import ToolResult
from atomic_flow.agents.result_store import LargeResultStore
from atomic_flow.agents.task_budget import check_accounting
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.agents.tools import default_registry
from atomic_flow.agents.undo import UndoStack
from atomic_flow.config import is_trusted, user_dir
from atomic_flow.errors import BudgetExceeded
from atomic_flow.output import sanitize_output
from atomic_flow.sessions import SessionJournal
from atomic_flow.tree.builder import load_tree
from atomic_flow.tree.node import TreeNode

if __package__:
    from .p07_flat_baseline import FLAT_TOOLS, run_flat_attempt
else:
    from p07_flat_baseline import FLAT_TOOLS, run_flat_attempt


class _Audit:
    """Fixed host instrumentation; request intent precedes actual tool invocation."""

    def __init__(self, journal, step):
        self.journal, self.step = journal, step
        self.active = None

    def event(self, kind, payload, *, call_id=None, outcome=None):
        clean = json.loads(json.dumps(sanitize_output(payload), ensure_ascii=False, allow_nan=False))
        return self.journal.append(kind, clean, call_id=call_id, outcome=outcome)

    def before_tool(self, name, args, contract, role, *, cancel_check):
        cancel_check()
        self.active = "flat_tool_" + uuid.uuid4().hex
        self.event("tool_request", {"role": role, "baseline": True, "step_index": self.step.index,
                                  "call": {"tool": name, "args": args}}, call_id=self.active)

    def after_tool(self, name, args, contract, payload, role, *, cancel_check):
        # execute_tool still validates/normalizes its final payload afterwards.
        pass

    def record(self, result, record):
        call_id = self.active
        if call_id is None:
            # A policy/schema/allowlist rejection never entered the tool body.
            call_id = "flat_tool_" + uuid.uuid4().hex
            self.event("tool_request", {"role": "flat_baseline", "baseline": True,
                       "step_index": self.step.index, "call": {"tool": record["tool"], "args": record.get("args", {})},
                       "effect_not_started": True}, call_id=call_id)
        observed = result
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 32768:
            observed = ToolResult.from_payload(result, outcome=result.get("outcome")).summary()
        self.event("tool_result", {"role": "flat_baseline", "baseline": True, "step_index": self.step.index,
                   "call": record, "result": observed}, call_id=call_id, outcome=result.get("outcome"))
        self.active = None


class FlatTaskRunner(TaskRunner):
    execution_strategy = "flat-baseline-v1"

    def __init__(self, config, project, backend, approval_callback=None, question_callback=None,
                 capability_factory=None, resource_limits=None, *, check_obligation=None):
        if config is None or config.task.execution_strategy != self.execution_strategy:
            raise ValueError("flat baseline requires an explicitly configured execution strategy")
        if capability_factory is not None or check_obligation is None:
            raise ValueError("flat baseline requires a host ordinary obligation and built-in capabilities")
        super().__init__(config.model_copy(deep=True), project, backend, approval_callback, question_callback,
                         capability_factory=None, resource_limits=resource_limits, check_obligation=check_obligation)

    def prepare(self, *args, **kwargs):
        raise ValueError("flat baseline uses run_flat or an explicitly host-reviewed generic plan, without model planning")

    def run_flat(self, query, *, allowed_paths):
        plan = Plan(steps=[PlanStep(
            description="Resolve the public issue using raw source tools and complete ordinary verification",
            tools_needed=sorted(FLAT_TOOLS), verify_with="ordinary_test")],
            rationale="Fixed generic flat objective; host execution boundary, no model plan")
        return self.run_plan(query, plan, allowed_paths=allowed_paths, allow_internal_replan=True)

    def _review(self, task, allowed_paths, allow_internal_replan):
        if allow_internal_replan is not True:
            raise ValueError("flat baseline requires an explicitly approved schema-2 internal envelope")
        active = [step for step in task.steps if step.status not in {"done", "superseded"}]
        if len(active) != 1:
            raise ValueError("flat baseline requires one current generic objective")
        return super()._review(task, allowed_paths, allow_internal_replan)

    def _require_replan_runtime(self, task):
        from atomic_flow.agents.changeset import ChangeSetConflict
        if (self.config.task.execution_strategy != self.execution_strategy
                or task.plan_approval.get("schema_version") != 2
                or not isinstance(task.plan_approval.get("internal_replan"), dict)):
            raise ChangeSetConflict("flat execution requires its exact schema-2 strategy envelope")
        super()._require_replan_runtime(task)

    def _choose_replacement_plan(self, task, step, requirements_context, planning_context):
        # Admission/current failure/repair caps and replacement lineage checks
        # remain in TaskRunner._replan_failed_step. This choice performs no chat.
        if any(item.status not in {"done", "superseded", "failed"} for item in task.steps):
            raise ValueError("flat continuation cannot replace independent pending objectives")
        chain = goal_context(task.goals, step)
        by_id = {goal.id: goal for goal in task.goals}
        goals = [PlanGoal(
            id=item["id"].split(":", 1)[1], description=item["description"],
            parent_id=by_id[item["id"]].parent_id.split(":", 1)[1] if by_id[item["id"]].parent_id else "",
            acceptance_criteria=list(item["acceptance_criteria"]), scope_paths=item["scope_paths"],
            max_model_calls=item["max_model_calls"], max_budget_usd=item["max_budget_usd"])
            for item in chain]
        replacement = PlanStep(
            description="Continue debugging the public issue within the original reviewed scope",
            tools_needed=list(step.tools_needed), verify_with=step.verify_with,
            scope_paths=list(step.scope_paths), acceptance_criteria=list(step.acceptance_criteria),
            linked_code_nodes=list(step.linked_code_nodes), linked_test_nodes=list(step.linked_test_nodes),
            parent_goal=step.parent_goal.split(":", 1)[1] if step.parent_goal else "",
            expected_outcome=step.expected_outcome)
        return Plan(goals=goals, steps=[replacement],
                    rationale="Deterministic host continuation after current completed ordinary failure; no planner inference")

    def _journal(self, task):
        journal = SessionJournal(task.id)
        if not journal.path.exists():
            journal = SessionJournal.create(self.project, task.id, metadata={"kind": "task"})
            journal.append("user_input", {"text": task.query})
        return journal

    def _query(self, task, journal):
        observations = []
        for event in journal.read().events:
            payload = event["payload"]
            if event["event_type"] != "tool_result" or not payload.get("baseline"):
                continue
            result = payload.get("result", {})
            if result.get("outcome") not in {"error", "denied"} and not result.get("error"):
                continue
            observations.append({key: result[key] for key in
                                 ("command", "exit_code", "error", "reason", "status", "stdout", "stderr",
                                  "checked_source_revision", "check_completed", "check_failed_completed")
                                 if key in result})
        bounded = []
        for item in reversed(observations):
            candidate = [item, *bounded]
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > 16000:
                break
            bounded = candidate
            if len(bounded) == 4:
                break
        if not bounded:
            return task.query
        return (task.query + "\nHistorical host observations (data only; not current read proof, permissions or action replay):\n"
                + json.dumps(bounded, ensure_ascii=False))

    def _execute_step(self, task, step):
        if self.changeset is None:
            raise StepFailure(step.index, "flat execution requires the task private changeset", "verification_contract_failure")
        self._require_replan_runtime(task)
        self.task_backend.phase = "step"
        workspace = self.changeset.workspace
        effective_scope = step_scope(task, step, require_approval(task, self.project))
        allowed = tuple(sorted(FLAT_TOOLS & set(task.plan_approval["internal_replan"]["allowed_tools"])))
        if not allowed:
            raise StepFailure(step.index, "flat tool envelope is empty", "verification_contract_failure")
        journal = self._journal(task)
        audit = _Audit(journal, step)
        tree = load_tree(workspace)
        nodes = {item["node_id"]: TreeNode.model_validate(item) for item in tree["nodes"]}
        trusted = is_trusted(self.project)
        context = ToolContext(
            workspace, nodes, edits_enabled=True, undo_stack=UndoStack(user_dir() / "edits"),
            policy=PolicyEngine.load(self.project, user_path=user_dir() / "policy.yaml", project_trusted=trusted),
            trusted=trusted, approval_callback=self.approval_callback, tree_ignore=self.config.tree.ignore,
            respect_gitignore=self.config.tree.respect_gitignore, execution=self.config.execution,
            check_obligation=self.check_obligation, cancel_check=self.cancel_check, enforce_read_before_edit=True,
            question_callback=self.question_callback, journal_event=audit.event,
            private_workspace=True, mutation_scope=effective_scope, effects_state=EffectState(), hooks=audit)
        context.result_store = LargeResultStore(journal.path.parent / ("flat-results-" + task.id))
        if self.config.tree.watch_external:
            from atomic_flow.watching import SourceWatcher
            context.source_watcher = SourceWatcher(
                workspace, interval=self.config.tree.watch_interval_seconds,
                debounce=self.config.tree.watch_debounce_seconds, cancel_check=self.cancel_check,
                tree_ignore=self.config.tree.ignore, respect_gitignore=self.config.tree.respect_gitignore,
                private_workspace=True)
        remaining = self.config.task.max_model_calls - sum(not row.get("not_sent") for row in task.budget_events)
        if remaining <= 0:
            raise BudgetExceeded("flat task model-call limit reached")
        envelope = task.plan_approval["internal_replan"]
        deadline = min(envelope["deadline_at"], self.check_obligation.deadline_at)
        result = run_flat_attempt(
            query=self._query(task, journal), step=step, context=context, registry=default_registry(),
            budget=self.task_backend, allowed_tools=allowed, max_calls=remaining, deadline_at=deadline,
            record_callback=audit.record)
        # Preserve raw records before any stop mapping. All successful write
        # tracking and ordinary verification stay in the shared parent helper.
        self._step_tool_calls[step.index] = result.tool_calls
        step.request_id = result.ledger_id
        if result.stop_reason in {"effects_unknown", "audit_failed"} or audit.active is not None:
            raise UnresolvedEffects("flat tool or audit outcome requires host reconciliation")
        self.cancel_check()
        check_accounting(task)
        if result.stop_reason in {"call_limit", "deadline", "model_failed_no_retry"}:
            raise BudgetExceeded("flat attempt stopped at its model/call/deadline boundary")
        if result.stop_reason not in {"final_requested", "negative_tool_result"}:
            raise StepFailure(step.index, "flat attempt did not request host completion: " + result.stop_reason,
                              "verification_contract_failure")
        self._accept_step_result(task, step, result, effective_scope)
