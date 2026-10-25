"""P05-1: explicit invalidation requeues done steps and their dependents only."""
import pytest

from atomic_flow.agents.task import Step, Task, save
from atomic_flow.agents.task_loop import TaskRunner


def _task(tmp_path, statuses=(("a", "done"), ("b", "done"), ("c", "done"))):
    steps = [Step(index=i, description=d, status=s, depends_on=[i - 1] if i else [])
             for i, (d, s) in enumerate(statuses)]
    task = Task(id="task_aa1000000001", query="q", project=str(tmp_path), steps=steps, current_index=len(steps))
    save(task)
    return task


def test_invalidation_cascades_to_dependents_and_records_reason(tmp_path, isolated_home):
    _task(tmp_path)
    # step 0 <- 1 <- 2 chain: invalidate 0 requeues all three
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.invalidate_step("task_aa1000000001", 0, "source premise changed: lib.py")
    assert [s.status for s in task.steps] == ["pending"] * 3
    assert all("source premise changed: lib.py" in s.invalidated_reason for s in task.steps)
    assert task.current_index == 0


def test_invalidation_spares_independent_done_steps(tmp_path, isolated_home):
    steps = [Step(index=0, description="a", status="done"),
             Step(index=1, description="b", status="done"),
             Step(index=2, description="c", status="done", depends_on=[1])]
    task = Task(id="task_aa2000000002", query="q", project=str(tmp_path), steps=steps, current_index=3)
    save(task)
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.invalidate_step("task_aa2000000002", 1, "test oracle updated")
    assert [s.status for s in task.steps] == ["done", "pending", "pending"]
    assert task.steps[0].invalidated_reason == ""


def test_invalidation_refusals(tmp_path, isolated_home):
    _task(tmp_path)
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    with pytest.raises(Exception, match="explicit reason"):
        runner.invalidate_step("task_aa1000000001", 0, "  ")
    with pytest.raises(Exception, match="unknown step index"):
        runner.invalidate_step("task_aa1000000001", 9, "r")
    # non-done target
    steps = [Step(index=0, description="a", status="failed")]
    save(Task(id="task_aa3000000003", query="q", project=str(tmp_path), steps=steps, current_index=0,
              status="failed"))
    with pytest.raises(Exception, match="only done steps"):
        runner.invalidate_step("task_aa3000000003", 0, "retry premise")
    # completed tasks are immutable
    _task(tmp_path)
    from atomic_flow.agents.task import load
    done = load("task_aa1000000001")
    done.status = "done"
    save(done)
    with pytest.raises(Exception, match="immutable"):
        runner.invalidate_step("task_aa1000000001", 0, "late reason")


def test_invalidated_done_step_is_rerunnable_state(tmp_path, isolated_home):
    steps = [Step(index=0, description="edit helper", status="done", verify_with="py_compile",
                  result_summary="ok", request_id="req_old")]
    task = Task(id="task_aa4000000004", query="fix helper", project=str(tmp_path), steps=steps,
                current_index=1, status="failed", error="old failure")
    save(task)
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.invalidate_step("task_aa4000000004", 0, "source premise changed")
    step = task.steps[0]
    assert step.status == "pending" and step.request_id == "" and step.result_summary == ""
    assert "source premise changed" in step.invalidated_reason
    assert task.current_index == 0 and task.status == "paused" and task.error == ""
    # the runner's own scheduler picks pending steps from current_index onward
    from atomic_flow.agents.task import load
    reloaded = load("task_aa4000000004")
    assert reloaded.steps[0].status == "pending" and reloaded.steps[0].invalidated_reason


def test_invalidation_cannot_requeue_a_different_project(tmp_path):
    from atomic_flow.agents.changeset import ChangeSetConflict
    from atomic_flow.config import user_dir

    task = _task(tmp_path)
    path = user_dir() / "tasks" / f"{task.id}.json"
    before = path.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ChangeSetConflict, match="another project"):
        TaskRunner(None, other, None).invalidate_step(task.id, 0, "wrong root")
    assert path.read_bytes() == before


def test_invalidation_reason_in_persisted_state_must_be_text(tmp_path):
    from atomic_flow.state import StateError

    task = _task(tmp_path)
    task.steps[0].invalidated_reason = {"not": "text"}
    with pytest.raises(StateError, match="invalidated_reason"):
        save(task)
