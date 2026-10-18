"""Task checks must use real tool results and actual changes, not model prose."""
import json
from types import SimpleNamespace

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Step
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.config import AtomicConfig
from atomic_flow.tree.builder import build_tree, load_tree, save_tree


def result(calls):
    return SimpleNamespace(tool_calls=calls)


@pytest.mark.parametrize("calls,method", [
    ([], "py_compile"),
    ([{"tool": "edit_node"}], "py_compile"),
    ([{"tool": "edit_node", "result_summary": {"error": "denied"}}], "py_compile"),
    ([{"tool": "edit_node", "result_summary": {"status": "applied"}}], "py_compile"),
    ([{"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied"}}], "py_compile"),
    ([{"tool": "run_command", "args": {"command": "pytest"}, "result_summary": {}}], "pytest"),
    ([{"tool": "run_command", "args": {"command": "pytest"}, "result_summary": {"outcome": "success"}}], "pytest"),
    ([{"tool": "run_command", "args": {"command": "pytest"},
       "result_summary": {"error": "timeout"}}], "pytest"),
    ([{"tool": "edit_node", "result_summary": {"error": "denied"}}], "none"),
    ([], "unknown-method"),
])
def test_incomplete_or_failed_evidence_cannot_pass(tmp_path, calls, method):
    runner = TaskRunner(AtomicConfig(), tmp_path, None)
    with pytest.raises(StepFailure):
        runner._verify_step(Step(0, "change sample", verify_with=method), result(calls))


def test_declared_edit_requires_an_edit_even_with_pytest(tmp_path):
    step = Step(0, "fix sample", tools_needed=["edit_node"], verify_with="pytest")
    calls = [{"tool": "run_command", "args": {"command": "pytest"}, "result_summary": {"outcome": "success", "exit_code": 0}}]
    with pytest.raises(StepFailure, match="edit"):
        TaskRunner(AtomicConfig(), tmp_path, None)._verify_step(step, result(calls))


def test_declared_command_cannot_be_replaced_with_prose(tmp_path):
    step = Step(0, "run quality check", tools_needed=["run_command"], verify_with="none")
    with pytest.raises(StepFailure, match="run_command"):
        TaskRunner(AtomicConfig(), tmp_path, None)._verify_step(step, result([]))


def test_pytest_before_last_edit_is_rerun(tmp_path, monkeypatch):
    import atomic_flow.agents.task_loop as task_loop

    invoked = []
    monkeypatch.setattr(task_loop, "run_python", lambda *a, **kw: invoked.append(a) or
                        SimpleNamespace(returncode=0, stdout="passed", stderr="", timed_out=False))
    calls = [
        {"tool": "run_command", "args": {"command": "pytest"}, "result_summary": {"outcome": "success", "exit_code": 0}},
        {"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"]}},
    ]
    TaskRunner(AtomicConfig(), tmp_path, None)._verify_step(Step(0, "fix sample", verify_with="pytest"), result(calls))
    assert len(invoked) == 1


@pytest.mark.parametrize("target,rerun", [("", False), ("tests/test_sample.py", True)])
def test_only_full_pytest_after_edits_satisfies_step(tmp_path, monkeypatch, target, rerun):
    import atomic_flow.agents.task_loop as task_loop

    invoked = []
    monkeypatch.setattr(task_loop, "run_python", lambda *a, **kw: invoked.append(a) or
                        SimpleNamespace(returncode=0, stdout="passed", stderr="", timed_out=False))
    calls = [
        {"tool": "edit_node", "result_summary": {"outcome": "success", "status": "applied", "checks": ["syntax"]}},
        {"tool": "run_command", "args": {"command": "pytest", "target": target},
         "result_summary": {"outcome": "success", "exit_code": 0}},
    ]
    TaskRunner(AtomicConfig(), tmp_path, None)._verify_step(Step(0, "fix sample", verify_with="pytest"), result(calls))
    assert bool(invoked) == rerun


@pytest.mark.parametrize("approved", [True, False])
def test_task_uses_real_orchestrator_and_approval_channel(tmp_path, approved):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "sample.py"
    source.write_text('def sample():\n    """Return sample value."""\n    return 1\n', encoding="utf-8")
    save_tree(project, build_tree(project))
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [
            {"tool": "read_node", "args": {"node_id": "sample.py::sample", "zoom": 2}},
            {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample():\n    return 2"}},
            {"text": "Changed sample.", "confidence": "low"},
        ],
        "critic": {"verdict": "ok", "citation_coverage": 1},
    })
    config = AtomicConfig()
    config.task.max_replans = 0
    approvals = []
    runner = TaskRunner(config, project, backend,
                        approval_callback=lambda *args: approvals.append(args) or approved)
    task = runner.run_plan("change sample", Plan(steps=[PlanStep(
        description="Change sample return value", tools_needed=["edit_node"], verify_with="py_compile",
    )]))
    assert approvals and approvals[0][0] == "edit_node"
    assert task.status == ("done" if approved else "failed")
    assert ("return 2" in source.read_text()) == approved
    from atomic_flow.config import user_dir
    meta = json.loads((user_dir() / "runs" / task.steps[0].request_id / "meta.json").read_text())
    edit = next(c for c in meta["tool_calls"] if c["tool"] == "edit_node")
    assert edit["result_summary"]["outcome"] == ("success" if approved else "denied")


def test_cli_task_bootstraps_persisted_index(tmp_path, monkeypatch, capsys):
    import atomic_flow.cli as cli
    from atomic_flow.backends.base import ChatResult

    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")

    class Backend(ScriptedBackend):
        def chat(self, messages, **kwargs):
            if "<role>" not in messages[1]["content"]:
                return ChatResult(json.dumps({"steps": [{"description": "Report sample", "verify_with": "none"}]}),
                                  tokens_in=100, tokens_out=50, cost_usd=0.001)
            return super().chat(messages, **kwargs)

    backend = Backend({"parser": {}, "answerer": {"text": "Sample returns one."}, "critic": {"verdict": "ok"}})
    monkeypatch.setattr(cli, "make_backend", lambda *a: backend)
    assert cli.main(["task", "explain sample", "--project", str(project), "--yes"]) == 0
    assert load_tree(project)["files"] == 1
    assert "status: done" in capsys.readouterr().out


def test_task_failed_matching_tests_roll_back_real_edit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_sample.py").write_text(
        "from sample import sample\ndef test_sample():\n    assert sample() == 1\n", encoding="utf-8",
    )
    save_tree(project, build_tree(project))
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [
            {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample():\n    return 2"}},
            {"text": "Done.", "confidence": "low"},
        ],
        "critic": {"verdict": "ok"},
    })
    config = AtomicConfig()
    config.task.max_replans = 0
    task = TaskRunner(config, project, backend, approval_callback=lambda *args: True).run_plan(
        "change sample", Plan(steps=[PlanStep(description="Change sample value", verify_with="py_compile")]),
    )
    assert task.status == "failed"
    assert "rolled back" in task.error
    assert (project / "sample.py").read_text() == "def sample():\n    return 1\n"
