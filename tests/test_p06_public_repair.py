"""Public prepare/review/approve/resume with real tools, checks and publication."""
import json

from conftest import ScriptedBackend

from atomic_flow.agents.planner import Plan, PlanGoal, PlanStep
from atomic_flow.agents.task import load
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.sync import store_path, sync_project_graph
from atomic_flow.tree.builder import build_tree, save_tree


def test_hierarchical_multifile_failure_repair_publish_without_new_plan_review(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    tests = project / "tests"
    tests.mkdir()
    for name in ("left", "right"):
        (project / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
        (tests / f"test_{name}.py").write_text(
            f"from {name} import {name}\ndef test_{name}():\n    assert {name}() == 2\n", encoding="utf-8")
    save_tree(project, build_tree(project))
    sync_project_graph(project, {})
    oracle = {p.name: p.read_bytes() for p in tests.glob("*.py")}
    criteria = ["Both public functions return two", "Existing tests pass"]
    goals = [PlanGoal(id="root", description="Update both public functions", acceptance_criteria=criteria,
                      scope_paths=["left.py", "right.py"], max_model_calls=50, max_budget_usd=1.0),
             PlanGoal(id="implementation", parent_id="root", description="Implement both functions",
                      acceptance_criteria=criteria, scope_paths=["left.py", "right.py"],
                      max_model_calls=40, max_budget_usd=0.8)]
    initial = Plan(goals=goals, steps=[PlanStep(
        description="Update left and right functions", parent_goal="implementation", acceptance_criteria=criteria,
        tools_needed=["edit_node"], verify_with="pytest", scope_paths=["left.py", "right.py"],
        linked_test_nodes=["tests/test_left.py", "tests/test_right.py"])])
    repaired = initial.model_copy(deep=True)
    repaired.steps[0].description = "Repair both function implementations against existing tests"
    answers = iter([
        {"tool": "edit_node", "args": {"node_id": "left.py::left", "new_source": "def left():\n    return 3\n"}},
        {"text": "First attempt completed.", "confidence": "high"},
        {"tool": "edit_node", "args": {"node_id": "left.py::left", "new_source": "def left():\n    return 2\n"}},
        {"tool": "edit_node", "args": {"node_id": "right.py::right", "new_source": "def right():\n    return 2\n"}},
        {"text": "Both functions updated.", "confidence": "high"},
    ])

    class Backend(ScriptedBackend):
        def chat(self, messages, **kwargs):
            user = messages[1]["content"]
            if "<role>" in user:
                return super().chat(messages, **kwargs)
            is_replan = "COMPLETED STEPS" in user
            self.calls.append(("replan" if is_replan else "plan", messages))
            return ChatResult((repaired if is_replan else initial).model_dump_json(), 100, 50, 0.001, 5)

    backend = Backend({"parser": {"task_type": "modify", "focus_terms": ["left", "right"]},
                       "answerer": lambda _user: next(answers), "critic": {"verdict": "ok"}})
    approvals = []
    runner = TaskRunner(AtomicConfig(), project, backend,
                        approval_callback=lambda *a, **kw: approvals.append(1) or True)
    task = runner.prepare("Make both functions return two", candidates=["left.py::left", "right.py::right"], n_files=4)
    assert task.status == "paused" and not task.plan_approval
    allowed = ["left.py", "right.py"]
    review = runner.review(task, allowed_paths=allowed, allow_internal_replan=True)
    task = runner.approve(task, review["digest"], allowed_paths=allowed, allow_internal_replan=True)
    result = runner.resume(task)
    assert result.status == "done", result.error
    assert result.replan_count == 1 and [s.status for s in result.steps] == ["superseded", "done"]
    assert result.steps[0].failure["kind"] == "deterministic_test_failure"
    assert result.steps[0].failure["evidence"]["exit_code"] == 1
    assert result.steps[-1].parent_goal == "p0:implementation" and len(result.goals) == 2
    assert result.steps[-1].evidence["verification"] == {"method": "pytest", "outcome": "passed"}
    assert result.plan_approval["allowed_paths"] == allowed
    assert result.plan_approval["internal_replan"]["runtime_digest"] == review["internal_replan"]["runtime_digest"]
    # Independent tool policy remains enforced. This trusted fixture callback
    # approves its three edits automatically; no second plan review is performed.
    assert len(approvals) == 3
    assert result.completion_report["ready_to_publish"]
    assert {p.name: p.read_bytes() for p in tests.glob("*.py")} == oracle
    for name in ("left", "right"):
        assert (project / f"{name}.py").read_text() == f"def {name}():\n    return 2\n"
    assert result.graph_sync and not result.graph_sync["failed"] and not result.graph_sync["fallback"]
    actual = json.loads(store_path(project).read_text())
    expected = clean_build(project_files(project))
    assert sorted(actual["nodes"], key=lambda n: n["id"]) == sorted(expected["nodes"], key=lambda n: n["id"])
    assert sorted(actual["edges"], key=lambda e: e["id"]) == sorted(expected["edges"], key=lambda e: e["id"])
    repairs = result.budget_events[result.repair_budget["start_event"]:]
    assert repairs and all(e["repair_id"] == result.repair_budget["id"] for e in repairs)
    assert all("p0:root" in e["goal_ids"] and "p0:implementation" in e["goal_ids"] for e in repairs)
    count = len(backend.calls)
    assert runner.resume(load(task.id)).status == "done" and len(backend.calls) == count
