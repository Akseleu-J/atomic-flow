"""Task persistence and TaskRunner with a fake backend."""
from __future__ import annotations

import pytest

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.source import digest
from atomic_flow.agents.task import Step, Task, list_tasks, load, save
from atomic_flow.agents.task_loop import TaskRunner


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIC_FLOW_EXECUTION_MODE", "local")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "home"))
    yield


def test_task_round_trip(isolated_home, tmp_path):
    task = Task.new(
        query="rename X",
        project=tmp_path,
        steps=[Step(index=0, description="step one", verify_with="py_compile")],
        rationale="single step",
    )
    save(task)
    back = load(task.id)
    assert back.query == "rename X"
    assert len(back.steps) == 1
    assert back.steps[0].description == "step one"
    assert back.status == "running"


def test_task_list_sorted(isolated_home, tmp_path):
    a = Task.new(query="a", project=tmp_path, steps=[Step(index=0, description="x")])
    b = Task.new(query="b", project=tmp_path, steps=[Step(index=0, description="y")])
    a.created_at = 1.0
    b.created_at = 2.0
    save(a)
    save(b)
    listing = list_tasks()
    assert [t.id for t in listing] == [b.id, a.id]


def test_task_runner_marks_done(monkeypatch, isolated_home, tmp_path):
    from atomic_flow.agents import task_loop
    # The final host gate independently reruns declared task checks.
    (tmp_path / "test_completion.py").write_text("def test_final_state():\n    assert True\n")
    (tmp_path / "x.py").write_text("X = 1\n")

    class R:
        def __init__(self, calls):
            self.tool_calls = calls
            self.ledger_id = "req_x"
            self.cost_usd = 0.01

    responses = [
        R([{"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"], "path": "x.py", "content_hash": digest((tmp_path / "x.py").read_bytes())}}]),
        R([{"tool": "run_command", "args": {"command": "pytest"},
            "result_summary": {"outcome": "success", "exit_code": 0}}]),
    ]

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            return responses.pop(0)

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)

    plan = Plan(steps=[
        PlanStep(description="edit x", verify_with="py_compile"),
        PlanStep(description="run tests", verify_with="pytest"),
    ])
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.run_plan("do things", plan)
    assert task.status == "done"
    assert task.current_index == 2
    assert all(s.status == "done" for s in task.steps)


def test_task_runner_stops_on_failed_edit(monkeypatch, isolated_home, tmp_path):
    from atomic_flow.agents import task_loop

    class R:
        tool_calls = [{"tool": "edit_node",
                       "result_summary": {"status": "rolled_back", "reason": "py_compile failed"}}]
        ledger_id = "req_x"
        cost_usd = 0.01

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            return R()

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)

    plan = Plan(steps=[PlanStep(description="bad edit", verify_with="py_compile")])
    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.run_plan("fail", plan)
    assert task.status == "paused", task.error
    assert task.last_failure_decision["action"] == "stop"
    assert task.replan_count == 0
    assert task.steps[0].status == "failed"
    assert "rolled back" in task.error


def test_task_runner_resume_continues_from_checkpoint(monkeypatch, isolated_home, tmp_path):
    from atomic_flow.agents import task_loop
    from atomic_flow.agents.changeset import ChangeSet
    (tmp_path / "x.py").write_text("X = 1\n")

    class R:
        def __init__(self, calls):
            self.tool_calls = calls
            self.ledger_id = "req_x"
            self.cost_usd = 0.0

    calls_made = []

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            calls_made.append(kw.get("mode"))
            return R([{"tool": "edit_node",
                       "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"], "path": "x.py", "content_hash": digest((tmp_path / "x.py").read_bytes())}}])

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)

    task = Task.new(
        query="two steps",
        project=tmp_path,
        steps=[
            Step(index=0, description="first", verify_with="py_compile", status="done"),
            Step(index=1, description="second", verify_with="py_compile", status="pending"),
        ],
    )
    task.current_index = 1
    task.changeset_id = ChangeSet.create(tmp_path).id
    save(task)

    runner = TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.approve(task, runner.review(task)["digest"])
    result = runner.resume(task)
    assert result.status == "done"
    assert len(calls_made) == 1
    assert result.steps[1].status == "done"


def test_replan_appends_steps_on_failure(monkeypatch, isolated_home, tmp_path):
    from atomic_flow.agents import task_loop
    (tmp_path / "x.py").write_text("X = 1\n")

    class R:
        def __init__(self, calls):
            self.tool_calls = calls
            self.ledger_id = "req_x"
            self.cost_usd = 0.0

    responses = [
        R([{"tool": "edit_node", "result_summary": {"status": "rolled_back", "reason": "syntax"}}]),
        R([{"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"], "path": "x.py", "content_hash": digest((tmp_path / "x.py").read_bytes())}}]),
    ]

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            result = responses.pop(0)
            if result.tool_calls[0]["result_summary"].get("status") == "rolled_back":
                from atomic_flow.agents.step_evidence import manifest_digest
                result.tool_calls[0]["result_summary"].update(verification={"syntax": "failed"}, checks=["syntax"],
                    checked_source_revision=manifest_digest(runner.changeset.validate_workspace()))
            return result

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)
    monkeypatch.setattr(task_loop, "replan_task", lambda *a, **kw: Plan(
        steps=[PlanStep(description="try alternative approach", verify_with="py_compile")],
    ))

    plan = Plan(steps=[PlanStep(description="initial attempt", verify_with="py_compile")])
    runner = task_loop.TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.run_plan("do", plan)
    assert task.status == "paused" and not task.plan_approval
    assert len(responses) == 1  # The new plan cannot run before host approval.
    task = runner.approve(task, runner.review(task)["digest"])
    task = runner.resume(task)
    assert task.status == "done"
    assert task.replan_count == 1
    assert len(task.steps) == 2
    assert task.steps[0].status == "superseded"
    assert task.steps[1].status == "done"


def test_verify_runs_pytest_itself_when_model_did_not(monkeypatch, isolated_home, tmp_path):
    """verify_with=pytest, but the model produced no run_command — TaskRunner runs pytest."""
    from atomic_flow.agents import task_loop

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text("def f(): return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("from pkg.m import f\ndef test_f():\n    assert f() == 1\n")

    class R:
        tool_calls = [{"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"], "path": "pkg/m.py", "content_hash": digest((tmp_path / "pkg/m.py").read_bytes())}}]
        ledger_id = "req_x"
        cost_usd = 0.0

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            return R()

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)

    plan = Plan(steps=[PlanStep(description="edit the function", verify_with="pytest")])
    runner = task_loop.TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.run_plan("do", plan)
    assert task.status == "done"
    assert "auto-run" in task.steps[0].result_summary


def test_verify_fails_task_when_pytest_fails(monkeypatch, isolated_home, tmp_path):
    from atomic_flow.agents import task_loop

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text("def f(): return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("from pkg.m import f\ndef test_f():\n    assert f() == 999\n")

    class R:
        tool_calls = [{"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"], "path": "pkg/m.py", "content_hash": digest((tmp_path / "pkg/m.py").read_bytes())}}]
        ledger_id = "req_x"
        cost_usd = 0.0

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, *a, **kw):
            return R()

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)
    monkeypatch.setattr(task_loop, "replan_task", lambda *a, **kw: Plan(
        steps=[PlanStep(description="STOP: cannot fix", verify_with="none")],
    ))

    plan = Plan(steps=[PlanStep(description="edit the function", verify_with="pytest")])
    runner = task_loop.TaskRunner(config=None, project=tmp_path, backend=None)
    task = runner.run_plan("do", plan)
    assert task.status == "failed"
    assert "pytest" in task.error.lower()
