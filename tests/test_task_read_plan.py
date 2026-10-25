"""P04-2: the step query carries a host-computed advisory read plan."""
from atomic_flow.agents import task_loop
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.source import digest


def _project(tmp_path):
    (tmp_path / "app.py").write_text("from lib import helper\n\n\ndef run():\n    return helper()\n", encoding="utf-8")
    (tmp_path / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_step_query_includes_read_plan_with_reasons(monkeypatch, isolated_home, tmp_path):
    captured = {}
    project = _project(tmp_path)
    lib_digest = digest((project / "lib.py").read_bytes())

    class R:
        tool_calls = [{"tool": "edit_node",
                       "result_summary": {"outcome": "success", "status": "applied",
                                          "checks": ["syntax"], "path": "lib.py",
                                          "content_hash": lib_digest}}]
        ledger_id = "req_x"
        cost_usd = 0.0

    class FakeOrch:
        def __init__(self, *a, **kw):
            pass

        def answer(self, query, *a, **kw):
            captured["query"] = query
            return R()

    monkeypatch.setattr(task_loop, "Orchestrator", FakeOrch)
    project = _project(tmp_path)
    plan = Plan(steps=[PlanStep(description="edit helper", verify_with="py_compile")])
    runner = task_loop.TaskRunner(config=None, project=project, backend=None)
    task = runner.run_plan("fix helper", plan)
    assert task.status == "done", task.error
    query = captured["query"]
    assert "Host read plan" in query
    assert "- lib.py (" in query and "goal file" in query
    assert "- app.py (" in query  # included with a reason (goal file or name match)
    assert "next_offset" in query  # the continuation-cursor instruction from the pilot failure


def test_read_plan_degrades_explicitly_when_graph_fails(monkeypatch, isolated_home, tmp_path):
    project = _project(tmp_path)

    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(task_loop, "require_approval", lambda task, project: ["lib.py"])
    monkeypatch.setattr("atomic_flow.graph.project.project_files", broken)
    runner = task_loop.TaskRunner(config=None, project=project, backend=None)
    text = runner._read_plan(type("T", (), {"plan_approval": {}})(), project)
    assert text.startswith("Host read plan unavailable (RuntimeError)")
