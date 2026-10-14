"""TaskRunner: execute a plan step-by-step, verifying each step."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from atomic_flow.agents.planner import Plan, replan_task
from atomic_flow.agents.task import Step, Task, save
from atomic_flow.config import AtomicConfig
from atomic_flow.llm.orchestrator import Orchestrator


@dataclass
class StepFailure(Exception):
    step_index: int
    message: str


class TaskRunner:
    def __init__(self, config: AtomicConfig | None, project: Path, backend, approval_callback=None):
        self.config = config
        self.project = project
        self.backend = backend
        self.approval_callback = approval_callback

    def run_plan(self, query: str, plan: Plan) -> Task:
        steps = [
            Step(
                index=i,
                description=p.description,
                tools_needed=list(p.tools_needed),
                expected_outcome=p.expected_outcome,
                verify_with=p.verify_with,
            )
            for i, p in enumerate(plan.steps)
        ]
        task = Task.new(query=query, project=self.project, steps=steps, rationale=plan.rationale)
        save(task)
        return self.resume(task)

    def resume(self, task: Task) -> Task:
        task.status = "running"
        save(task)

        while task.current_index < len(task.steps):
            step = task.steps[task.current_index]
            step.status = "running"
            step.started_at = time.time()
            save(task)
            try:
                self._execute_step(task, step)
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
                save(task)

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
                new_plan = replan_task(
                    task.query,
                    completed=[f"{s.index + 1}. {s.description}" for s in task.steps if s.status == "done"],
                    failed_description=step.description,
                    verify_with=step.verify_with,
                    error=step.result_summary,
                    remaining=[f"{s.index + 1}. {s.description}" for s in task.steps[task.current_index + 1:]],
                    project_root=self.project,
                    backend=self.backend,
                )
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

        task.status = "done"
        task.finished_at = time.time()
        save(task)
        return task

    def _execute_step(self, task: Task, step: Step) -> None:
        subquery = f"[Step {step.index + 1}/{len(task.steps)}] {step.description}"
        orch = Orchestrator(self.config, self.project, self.backend)
        orch._approval_callback = self.approval_callback
        result = orch.answer(subquery, mode="medium", approve_edits=True)
        step.request_id = result.ledger_id
        task.budget_spent += result.cost_usd or 0.0
        self._verify_step(step, result)

    def _verify_step(self, step: Step, result) -> None:
        if step.verify_with == "none":
            return
        calls = result.tool_calls or []

        if step.verify_with == "py_compile":
            for call in calls:
                if call.get("tool") != "edit_node":
                    continue
                status = (call.get("result_summary") or {}).get("status")
                if status == "rolled_back":
                    reason = (call.get("result_summary") or {}).get("reason", "edit rolled back")
                    raise StepFailure(step.index, f"edit rolled back: {reason}")
                if status and status not in {"applied"}:
                    raise StepFailure(step.index, f"edit ended with status={status}")
            return

        if step.verify_with == "pytest":
            pytest_calls = [
                call for call in calls
                if call.get("tool") == "run_command"
                and call.get("args", {}).get("command") == "pytest"
            ]
            for call in pytest_calls:
                summary = call.get("result_summary") or {}
                code = summary.get("exit_code")
                if code not in (0, None):
                    raise StepFailure(step.index, f"pytest exit_code={code}")
            if pytest_calls:
                return

            import subprocess
            import sys
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "--no-header", "-x"],
                    cwd=self.project, capture_output=True, text=True, timeout=180,
                )
            except subprocess.TimeoutExpired:
                raise StepFailure(step.index, "pytest timed out after 180s")
            if proc.returncode != 0:
                out = (proc.stdout + proc.stderr)[-1500:]
                raise StepFailure(step.index, f"pytest exit_code={proc.returncode}\n{out}")
            step.result_summary = "ok (pytest auto-run by TaskRunner)"
            return
