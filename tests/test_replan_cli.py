"""CLI opt-in is reviewed, digest-bound and never implied by --yes."""
import json

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.cli import main


def test_cli_review_and_approve_bind_internal_replan_option(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    task = Task.new("Inspect source", tmp_path, [Step(0, "Read source", tools_needed=["read_file"], verify_with="none")])
    task.status = "paused"
    save(task)
    backends = []

    def factory(*args):
        backend = ScriptedBackend({})
        backends.append(backend)
        return backend

    monkeypatch.setattr("atomic_flow.cli.make_backend", factory)
    scope = ["--scope-path", "a.py"]
    assert main(["task", "--review", task.id, *scope, "--yes"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert legacy["schema_version"] == 1 and not backends
    assert main(["task", "--review", task.id, *scope, "--allow-internal-replan"]) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["schema_version"] == 2 and review["digest"] != legacy["digest"]
    assert main(["task", "--approve", task.id, *scope, "--plan-digest", review["digest"]]) == 2
    capsys.readouterr()
    assert not load(task.id).plan_approval
    assert main(["task", "--approve", task.id, *scope, "--plan-digest", review["digest"],
                 "--allow-internal-replan"]) == 0
    assert load(task.id).plan_approval["digest"] == review["digest"]
    assert all(not backend.calls for backend in backends)


@pytest.mark.parametrize("operation", [
    ["--invalidate-step", "0", "--task-id", "task_aaaaaaaaaaaa", "--show", "task_aaaaaaaaaaaa"],
    ["--reconcile", "call_id", "--show", "task_aaaaaaaaaaaa"],
    ["--resume", "task_aaaaaaaaaaaa", "--allow-internal-replan"],
])
def test_cli_refuses_ambiguous_operations_before_loading_or_execution(operation, monkeypatch):
    monkeypatch.setattr("atomic_flow.agents.task.load", lambda *a: pytest.fail("invalid CLI request loaded a task"))
    assert main(["task", *operation]) == 2
