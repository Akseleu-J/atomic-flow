"""Host review authority, stale source refusal, and dependency scheduling."""
import json
from dataclasses import asdict

import pytest

from atomic_flow.agents.changeset import ChangeSetConflict
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.config import AtomicConfig


def staged(root, plan=None):
    (root / "a.py").write_text("A = 1\n")
    task = Task.new("change source", root, TaskRunner._steps(plan or Plan(steps=[PlanStep(description="Update source")])))
    task.status = "paused"
    save(task)
    return task, TaskRunner(AtomicConfig(), root, None)


def test_unapproved_legacy_task_cannot_execute(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("unapproved execution"))
    result = runner.resume(task)
    assert result.status == "paused" and "approval required" in result.error


@pytest.mark.parametrize("version", [0, 1])
def test_legacy_migration_cannot_carry_approval_and_preserves_sequential_dependencies(tmp_path, version):
    task = Task.new("legacy", tmp_path, [Step(7, "First change"), Step(12, "Next change")])
    path = save(task)
    data = asdict(task)
    data["schema_version"] = version
    data["plan_approval"] = {"digest": "legacy field cannot grant permission"}
    data["steps"][1]["depends_on"] = []
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    migrated = load(task.id)
    assert migrated.schema_version == 2 and not migrated.plan_approval
    assert migrated.steps[1].depends_on == [7] and path.read_bytes() == before


def test_review_and_approval_bind_plan_source_and_new_path_scope(tmp_path):
    task, runner = staged(tmp_path)
    review = runner.review(task, allowed_paths=["a.py", "new.py"])
    assert review["scope_hashes"]["new.py"] is None
    with pytest.raises(ChangeSetConflict, match="digest changed"):
        runner.approve(task, review["digest"])
    approved = runner.approve(task, review["digest"], allowed_paths=["a.py", "new.py"])
    assert approved.plan_approval["digest"] == review["digest"]
    (tmp_path / "a.py").write_text("A = 2\n")
    result = runner.resume(approved)
    assert result.status == "paused" and "source changed" in result.error
    with pytest.raises(ChangeSetConflict, match="digest changed"):
        runner.approve(approved, review["digest"], allowed_paths=["a.py", "new.py"])


def test_revision_invalidates_approval_without_model_call(tmp_path):
    task, runner = staged(tmp_path)
    task = runner.approve(task, runner.review(task)["digest"])
    task = runner.revise(task, Plan(steps=[PlanStep(description="Different source change")]))
    assert not task.plan_approval and task.status == "paused"
    assert runner.review(task)["plan"]["steps"][0]["description"] == "Different source change"
    assert runner.resume(task).status == "paused"


def test_review_rejects_directory_traversal_and_wildcards(tmp_path):
    task, runner = staged(tmp_path)
    for path in ("../x.py", "/x.py", "a/../b.py", ".", "a\\b.py", "*.py"):
        with pytest.raises(ValueError):
            runner.review(task, allowed_paths=[path])


@pytest.mark.parametrize("dependencies", [[[0]], [[1], [0]], [[4]]])
def test_invalid_dependency_graphs_rejected(dependencies):
    with pytest.raises(ValueError):
        Plan(steps=[PlanStep(description="Check source", depends_on=d) for d in dependencies])


def test_independent_work_runs_while_failed_descendants_remain_blocked(tmp_path, monkeypatch):
    plan = Plan(steps=[PlanStep(description="First source check", verify_with="none", depends_on=[]),
                       PlanStep(description="Dependent source check", verify_with="none", depends_on=[0]),
                       PlanStep(description="Independent source check", verify_with="none", depends_on=[])])
    task, runner = staged(tmp_path, plan)
    seen = []
    def execute(task, step):
        seen.append(step.index)
        if step.index == 0:
            raise StepFailure(step.index, "prerequisite failed")
    monkeypatch.setattr(runner, "_execute_step", execute)
    task = runner.approve(task, runner.review(task)["digest"])
    result = runner.resume(task)
    assert seen == [0, 2] and result.status == "paused"
    persisted = load(result.id)
    assert persisted.steps[1].status == "blocked" and "0" in persisted.steps[1].blocked_reason
    assert persisted.steps[2].status == "done"
    assert not persisted.completion_report.get("ready_to_publish")


def test_replanned_work_pauses_before_any_new_execution(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    calls = []
    def execute(task, step):
        calls.append(step.index)
        raise StepFailure(step.index, "failed")
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: Plan(
        steps=[PlanStep(description="Alternative source approach")]))
    task = runner.approve(task, runner.review(task)["digest"])
    result = runner.resume(task)
    assert result.status == "paused" and calls == [0] and result.replan_count == 1
    assert not result.plan_approval and result.steps[0].status == "superseded"
    assert runner.resume(result).status == "paused" and calls == [0]


def test_plan_tampering_after_approval_does_not_execute(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    task = runner.approve(task, runner.review(task)["digest"])
    task.steps[0].description = "Execute different action"
    save(task)
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("tampered execution"))
    assert "stale" in runner.resume(task).error


def test_revision_refuses_interrupted_step(tmp_path):
    task, runner = staged(tmp_path)
    task.steps[0].status = "running"
    save(task)
    with pytest.raises(ChangeSetConflict, match="side effects"):
        runner.revise(task, Plan(steps=[PlanStep(description="Unsafe reset attempt")]))


def test_final_scope_catches_out_of_scope_mutations_from_host_backend(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    (tmp_path / "b.py").write_text("B = 1\n")
    task = runner.approve(task, runner.review(task, allowed_paths=["a.py"])["digest"], allowed_paths=["a.py"])
    def execute(task, step):
        (runner.changeset.workspace / "b.py").write_text("B = 2\n")
        runner.changeset.track("b.py")
    monkeypatch.setattr(runner, "_execute_step", execute)
    runner.config.task.max_replans = 0
    result = runner.resume(task)
    assert result.status == "failed" and "scope" in result.error
    assert (tmp_path / "b.py").read_text() == "B = 1\n"


def test_explicit_host_scope_can_authorize_new_path(tmp_path, monkeypatch):
    runner = TaskRunner(AtomicConfig(), tmp_path, None)
    def execute(task, step):
        (runner.changeset.workspace / "new.py").write_text("NEW = 1\n")
        runner.changeset.track("new.py")
    monkeypatch.setattr(runner, "_execute_step", execute)
    task = runner.run_plan("create source", Plan(steps=[PlanStep(description="Create new source")]), allowed_paths=["new.py"])
    assert task.status == "done" and (tmp_path / "new.py").read_text() == "NEW = 1\n"


def test_host_integrity_failure_never_calls_replanner(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    def execute(task, step):
        raise ChangeSetConflict("source changed after authorization")
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("integrity failure replanned"))
    task = runner.approve(task, runner.review(task)["digest"])
    result = runner.resume(task)
    assert result.status == "failed" and not result.replan_count
    assert "source changed" in result.error


def test_revise_preserves_done_work_and_remaps_new_dependencies(tmp_path):
    task, runner = staged(tmp_path)
    task.steps[0].status = "done"
    save(task)
    revised = runner.revise(task, Plan(steps=[PlanStep(description="Next independent change", depends_on=[]),
                                            PlanStep(description="Next dependent change", depends_on=[0])]))
    assert [s.index for s in revised.steps] == [0, 1, 2]
    assert revised.steps[0].status == "done" and revised.steps[2].depends_on == [1]
    assert not revised.plan_approval
