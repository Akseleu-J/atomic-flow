"""P05 rolling frontier: same host scheduler, no replay or new authority."""
import json
from copy import deepcopy

import pytest

from atomic_flow.agents.changeset import ChangeSetConflict
from atomic_flow.agents.frontier import frontier_context, task_frontier
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.state import StateError


def task(tmp_path, steps):
    return Task(id="task_f00500000001", query="fix", project=str(tmp_path), steps=steps)


def test_frontier_drains_independent_work_and_links_existing_ledgers(tmp_path):
    job = task(tmp_path, [Step(7, "read", status="done", request_id="req_read", verify_with="none"),
                          Step(9, "failed", status="failed"),
                          Step(12, "blocked descendant", depends_on=[9]),
                          Step(14, "next", depends_on=[7]), Step(16, "independent")])
    before = deepcopy(job)
    view = task_frontier(job)
    assert view["ready"] == [14, 16]
    assert view["blocked"] == [{"index": 12, "waiting_for": [9]}]
    assert view["decision"] == "execute_ready"
    assert view["next_steps"][0]["prerequisites"] == [
        {"index": 7, "request_id": "req_read", "verification": "none"}]
    assert job == before
    job.steps[3].status = job.steps[4].status = "done"
    assert task_frontier(job)["decision"] == "replan_failed"


def test_invalidation_recomputes_frontier_without_replaying_done_steps(tmp_path, isolated_home):
    job = task(tmp_path, [Step(0, "a", status="done"),
                          Step(1, "b", status="done", depends_on=[0]),
                          Step(2, "independent", status="done", request_id="req_keep")])
    job.status = "failed"
    save(job)
    runner = TaskRunner(None, tmp_path, None)
    runner.invalidate_step(job.id, 0, "premise changed")
    view = runner.explain_next(job.id)
    assert view["task_status"] == "paused"
    assert view["ready"] == [0]
    assert view["blocked"] == [{"index": 1, "waiting_for": [0]}]
    assert load(job.id).steps[2].request_id == "req_keep"


def test_resume_explanation_is_read_only_and_project_bound(tmp_path, isolated_home):
    job = task(tmp_path, [Step(0, "legacy done", status="done"), Step(1, "next", depends_on=[0])])
    path = save(job)
    before = path.read_bytes()
    view = TaskRunner(None, tmp_path, None).explain_next(job.id)
    assert view["next_steps"][0]["prerequisites"][0]["request_id"] == ""
    assert path.read_bytes() == before
    with pytest.raises(ChangeSetConflict, match="another project"):
        TaskRunner(None, tmp_path / "other", None).explain_next(job.id)


@pytest.mark.parametrize("horizon", [0, 4, True, 1.5])
def test_horizon_rejects_invalid_bounds(tmp_path, horizon):
    with pytest.raises(ValueError, match="horizon"):
        task_frontier(task(tmp_path, []), horizon=horizon)


def test_horizon_and_context_stay_bounded_with_valid_json(tmp_path):
    job = task(tmp_path, [Step(i, "x" * 1000, status="done", request_id="r" * 1000) for i in range(25)] +
               [Step(i, "y" * 1000, depends_on=list(range(25))) for i in range(25, 35)])
    view = task_frontier(job)
    assert len(view["next_steps"]) == 3 and view["omitted_ready"] == 7
    assert view["next_steps"][0]["omitted_prerequisites"] == 5
    assert view["next_steps"][0]["prerequisites"][0]["request_id_truncated"]
    assert len(view["next_steps"][0]["prerequisites"][0]["request_id"]) == 128
    text = frontier_context(job)
    payload = json.loads(text.split("\n", 1)[1])
    assert payload["details_omitted"]
    assert len(text) < 5300


@pytest.mark.parametrize("steps,message", [
    ([Step(0, "a", depends_on=[1]), Step(1, "b", depends_on=[0])], "cyclic"),
    ([Step(0, "a", depends_on=[8])], "missing"),
])
def test_invalid_dag_never_produces_a_frontier(tmp_path, steps, message):
    with pytest.raises(StateError, match=message):
        task_frontier(task(tmp_path, steps))


def test_running_requires_reconciliation_superseded_does_not_satisfy_dependency(tmp_path):
    job = task(tmp_path, [Step(0, "old", status="superseded"),
                          Step(1, "running", status="running"), Step(2, "wait", depends_on=[0])])
    assert task_frontier(job)["decision"] == "reconcile_running"
    assert task_frontier(job)["ready"] == []
    assert "current_step_running" in frontier_context(job)
    job.steps[1].status = "done"
    assert task_frontier(job)["decision"] == "review_blocked"


def test_scheduler_does_not_drain_ready_work_while_running_step_is_unresolved(tmp_path, isolated_home, monkeypatch):
    job = task(tmp_path, [Step(0, "unfinished", status="running"), Step(1, "independent")])
    runner = TaskRunner(None, tmp_path, None)
    monkeypatch.setattr(runner, "_execute_step", lambda *a: pytest.fail("unresolved work must stop execution"))
    result = runner._resume_steps(job)
    assert result.status == "paused" and "reconciliation" in result.error
    assert [s.status for s in load(job.id).steps] == ["running", "pending"]
