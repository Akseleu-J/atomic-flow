"""Real orchestration must stage all steps and publish only a completed task."""
import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig
from atomic_flow.tree.builder import build_tree, save_tree


@pytest.mark.parametrize("second_ok", [True, False])
def test_multistep_task_publishes_all_or_none(tmp_path, second_ok):
    root = tmp_path / "project"
    root.mkdir()
    for name in ("a", "b"):
        (root / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    save_tree(root, build_tree(root))
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["return"]},
        "answerer": [
            {"tool": "read_node", "args": {"node_id": "a.py::a", "zoom": 2}},
            {"tool": "edit_node", "args": {"node_id": "a.py::a", "new_source": "def a():\n    return 2"}},
            {"text": "First change ready."},
            {"tool": "read_node", "args": {"node_id": "b.py::b", "zoom": 2}},
            {"tool": "edit_node", "args": {"node_id": "b.py::b", "new_source": "def b():\n    return 2" if second_ok else "def b(:"}},
            {"text": "Second change ready."},
        ],
        "critic": {"verdict": "ok"},
    })
    approvals = []

    def approve(*args):
        # Even the second step must see an unchanged original checkout.
        assert "return 1" in (root / "a.py").read_text()
        assert "return 1" in (root / "b.py").read_text()
        approvals.append(args)
        return True

    config = AtomicConfig()
    config.task.max_replans = 0
    task = TaskRunner(config, root, backend, approval_callback=approve).run_plan(
        "update both functions", Plan(steps=[PlanStep(description=f"Update {name} function", tools_needed=["edit_node"])
                                            for name in ("a", "b")]),
    )
    assert task.status == ("done" if second_ok else "failed")
    expected = "return 2" if second_ok else "return 1"
    assert all(expected in (root / f"{name}.py").read_text() for name in ("a", "b"))
    assert task.changeset_id
    assert approvals


def test_resume_conflict_sends_no_model_request(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    change = ChangeSet.create(root)
    task = Task.new("resume", root, [Step(0, "update a")])
    task.changeset_id = change.id
    (root / "a.py").write_text("user\n", encoding="utf-8")
    backend = ScriptedBackend({})
    result = TaskRunner(AtomicConfig(), root, backend).resume(task)
    assert result.status == "failed"
    assert "changed" in result.error
    assert not backend.calls
    assert (root / "a.py").read_text() == "user\n"


def test_legacy_checkpoint_is_not_silently_replayed(tmp_path):
    task = Task.new("resume", tmp_path, [Step(0, "first", status="done"), Step(1, "second")])
    task.current_index = 1
    backend = ScriptedBackend({})
    result = TaskRunner(AtomicConfig(), tmp_path, backend).resume(task)
    assert result.status == "failed" and "legacy" in result.error
    assert not backend.calls


def test_interrupted_step_requires_review(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    change = ChangeSet.create(root)
    task = Task.new("resume", root, [Step(0, "first", status="running")])
    task.changeset_id = change.id
    backend = ScriptedBackend({})
    result = TaskRunner(AtomicConfig(), root, backend).resume(task)
    assert result.status == "failed" and "interrupted" in result.error
    assert not backend.calls


def test_offline_cli_recovery_never_constructs_backend(tmp_path, monkeypatch, capsys):
    import atomic_flow.cli as cli
    from atomic_flow.agents.task import save

    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "b.py").write_text("b = 1\n", encoding="utf-8")
    change = ChangeSet.create(root)
    for rel in ("a.py", "b.py"):
        (change.workspace / rel).write_text("changed\n", encoding="utf-8")
        change.track(rel)
    original = change._write_target

    def interrupted(rel, data):
        if rel == "b.py":
            raise KeyboardInterrupt()
        original(rel, data)

    monkeypatch.setattr(change, "_write_target", interrupted)
    with pytest.raises(KeyboardInterrupt):
        change.publish()
    task = Task.new("recover", root, [Step(0, "first", status="done")])
    task.changeset_id = change.id
    save(task)
    monkeypatch.setattr(cli, "make_backend", lambda *a: pytest.fail("recovery must not create a model backend"))
    assert cli.main(["task", "--recover", task.id]) == 0
    assert "rolled back" in capsys.readouterr().out
    assert (root / "a.py").read_text() == "a = 1\n"


def test_completed_publish_is_not_replayed_after_crash(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    change = ChangeSet.create(root)
    change.publish()
    task = Task.new("resume", root, [Step(0, "first", status="done")])
    task.current_index = 1
    task.changeset_id = change.id
    backend = ScriptedBackend({})
    result = TaskRunner(AtomicConfig(), root, backend).resume(task)
    assert result.status == "done"
    assert not backend.calls


@pytest.mark.parametrize("task_id", ["../outside", "x/y", "task_not-an-id"])
def test_task_ids_cannot_escape_storage(task_id):
    from atomic_flow.agents.task import load

    with pytest.raises(ValueError, match="invalid task id"):
        load(task_id)
