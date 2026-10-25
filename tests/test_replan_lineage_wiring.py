"""P06 lineage wiring: obligations no longer force a pause when formally kept."""
from conftest import ScriptedBackend  # noqa: F401  (fixture family consistency)

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig


def staged_with_goal(root):
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    plan = Plan(steps=[PlanStep(description="Read source for the report", tools_needed=["read_file"],
                                verify_with="none")])
    task = Task.new("Report the constant", root, TaskRunner._steps(plan))
    task.steps[0].acceptance_criteria = ["The constant value is reported"]
    task.steps[0].scope_paths = []
    task.status = "paused"
    save(task)
    return task, TaskRunner(AtomicConfig(), root, None)


def authorize(runner, task):
    review = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    return runner.approve(task, review["digest"], allowed_paths=["a.py"], allow_internal_replan=True)


def lineage_kept_plan(*args, **kwargs):
    replacement = PlanStep(description="Read source via the alternate reader", tools_needed=["read_file"],
                           verify_with="none")
    replacement.acceptance_criteria = ["The constant value is reported"]
    return Plan(steps=[replacement])


def lineage_dropped_plan(*args, **kwargs):
    return Plan(steps=[PlanStep(description="Do something unrelated", tools_needed=["read_file"],
                                verify_with="none")])


def test_inherit_preserving_replan_with_obligations_continues(tmp_path, monkeypatch):
    task, runner = staged_with_goal(tmp_path)
    seen = []

    def execute(task_, step):
        seen.append(step.index)
        if len(seen) == 1:
            raise runner._check_failure(step, "first approach failed", "pytest", 1)
        step.result_summary = "independent read completed"

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lineage_kept_plan)
    task = authorize(runner, task)
    result = runner.resume(task)
    assert result.status == "done", result.error
    assert result.replan_count == 1 and len(seen) == 2
    persisted = load(result.id)
    assert persisted.steps[-1].acceptance_criteria == ["The constant value is reported"]


def test_replan_dropping_criterion_pauses_with_lineage_reason(tmp_path, monkeypatch):
    task, runner = staged_with_goal(tmp_path)
    seen = []

    def execute(task_, step):
        seen.append(step.index)
        raise runner._check_failure(step, "first approach failed", "pytest", 1)

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lineage_dropped_plan)
    task = authorize(runner, task)
    result = runner.resume(task)
    assert result.status == "paused"
    assert "criterion lost by replan" in result.error
    persisted = load(result.id)
    assert persisted.plan_approval == {} and persisted.steps[-1].status == "pending"
