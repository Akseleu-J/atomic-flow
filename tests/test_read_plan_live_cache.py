"""Step-focused planning, fresh caches and source admission in the real runner."""
import os
from pathlib import Path

import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.task import Step, Task, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.graph.project import project_files
from atomic_flow.project_resources import ProjectLimits, ResourceLimitError


def authorized(root, names):
    task = Task.new("Repair the requested source", root, [Step(0, "Edit z_target.py", verify_with="none")])
    task.status = "paused"
    save(task)
    runner = TaskRunner(None, root, None)
    review = runner.review(task, allowed_paths=names)
    return runner.approve(task, review["digest"], allowed_paths=names), runner


def test_explicit_step_target_is_not_displaced_by_first_twelve_scope_files(tmp_path):
    names = [f"a{i:02}.py" for i in range(16)] + ["z_target.py"]
    for name in names:
        (tmp_path / name).write_text("def helper(): return 1\n", encoding="utf-8")
    task, runner = authorized(tmp_path, names)
    text = runner._read_plan(task, tmp_path, task.steps[0])
    assert "- z_target.py (" in text
    assert runner.read_plan_report["goals"] == ["z_target.py"]
    assert text.splitlines()[1].startswith("- z_target.py (")
    assert "Partial advisory plan" in text  # Related same-name definitions do not displace the goal.


def test_live_read_plan_reuses_ast_and_refreshes_same_stat_edit_and_rollback(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "z_target.py").write_bytes(b"def helper(): return 1\n")
    task, runner = authorized(project, ["z_target.py"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file = workspace / "z_target.py"
    original = b"def helper(): return 1\n"
    file.write_bytes(original)
    runner._read_plan(task, workspace, task.steps[0])
    first = runner.read_plan_report["revision"]
    assert runner.read_plan_report["stats"]["parsed_files"] == 1
    runner._read_plan(task, workspace, task.steps[0])
    assert runner.read_plan_report["stats"]["parsed_files"] == 0
    before = file.stat()
    file.write_bytes(b"def helper(): return 2\n")
    os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
    runner._read_plan(task, workspace, task.steps[0])
    assert runner.read_plan_report["stats"]["parsed_files"] == 1
    assert runner.read_plan_report["revision"] != first
    file.write_bytes(original)
    runner._read_plan(task, workspace, task.steps[0])
    assert runner.read_plan_report["revision"] == first
    assert runner.read_plan_report["stats"]["parsed_files"] == 1


def test_no_match_is_explicit_and_does_not_fall_back_to_scope_order(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    task, runner = authorized(tmp_path, ["a.py"])
    text = runner._read_plan(task, tmp_path, Step(0, "zzzzUnmatchedRequest", verify_with="none"))
    assert "no step-specific" in text
    assert runner.read_plan_report["goals"] == []
    assert "- a.py" not in text


def test_cache_is_rebound_to_task_and_exact_approved_scope(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "z_target.py").write_text("Z = 1\n")
    task, runner = authorized(tmp_path, ["a.py", "z_target.py"])
    runner._read_plan(task, tmp_path, task.steps[0])
    old_index = runner._read_plan_index
    narrowed = runner.approve(task, runner.review(task, allowed_paths=["a.py"])["digest"], allowed_paths=["a.py"])
    text = runner._read_plan(narrowed, tmp_path, task.steps[0])
    assert runner._read_plan_index is not old_index
    assert runner.read_plan_report["goals"] == [] and "no step-specific" in text
    second = Task.new("Read a.py", tmp_path, [Step(0, "Read a.py", verify_with="none")])
    second.status = "paused"
    save(second)
    second = runner.approve(second, runner.review(second, allowed_paths=["a.py"])["digest"], allowed_paths=["a.py"])
    old_index = runner._read_plan_index
    runner._read_plan(second, tmp_path, second.steps[0])
    assert runner._read_plan_index is not old_index
    assert runner.read_plan_report["goals"] == ["a.py"]


def test_cancelled_snapshot_never_constructs_orchestrator(tmp_path, monkeypatch):
    (tmp_path / "z_target.py").write_text("A = 1\n")
    task, runner = authorized(tmp_path, ["z_target.py"])

    def cancel(*args, **kwargs):
        raise TaskCancelled("cancel read-plan scan")

    monkeypatch.setattr("atomic_flow.graph.project.project_files", cancel)
    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", lambda *a, **kw: pytest.fail("model started after cancel"))
    with pytest.raises(TaskCancelled):
        runner._execute_step(task, task.steps[0])
    assert runner._read_plan_index is None and runner.read_plan_report["reason"] == "cancelled"


def test_snapshot_excludes_ignored_hidden_and_credential_paths_before_open(tmp_path, monkeypatch):
    for name in ("ok.py", "ignored.py", "secrets.py", ".hidden.py"):
        (tmp_path / name).write_text("A = 1\n")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    github = tmp_path / ".github"
    github.mkdir()
    (github / "check.py").write_text("CHECK = 1\n")
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name not in {"ignored.py", "secrets.py", ".hidden.py"}
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert set(project_files(tmp_path)) == {"ok.py", ".github/check.py"}


def test_read_plan_resource_refusal_drops_old_cache(tmp_path):
    (tmp_path / "z_target.py").write_text("A = 1\n")
    task, runner = authorized(tmp_path, ["z_target.py"])
    runner._read_plan(task, tmp_path, task.steps[0])
    assert runner._read_plan_index is not None
    runner.resource_limits = ProjectLimits(max_total_bytes=2)
    text = runner._read_plan(task, tmp_path, task.steps[0])
    assert "ResourceLimitError" in text and runner._read_plan_index is None


def test_snapshot_total_limit_refuses_complete_operation(tmp_path):
    (tmp_path / "a.py").write_bytes(b"A = 1\n")
    (tmp_path / "b.py").write_bytes(b"B = 1\n")
    with pytest.raises(ResourceLimitError):
        project_files(tmp_path, limits=ProjectLimits(max_total_bytes=7))


def test_snapshot_never_reads_an_outside_symlink(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("PRIVATE = 1\n")
    try:
        (root / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name not in {"outside.py", "link.py"}
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(OSError, match="symlink|escapes"):
        project_files(root)


def test_ignore_policy_drift_during_scan_refuses_snapshot(tmp_path, monkeypatch):
    from atomic_flow import project_resources
    from atomic_flow.agents.source import SourceConflict

    (tmp_path / "a.py").write_text("A = 1\n")
    policy = tmp_path / ".gitignore"
    policy.write_text("other.py\n")
    original = project_resources.stream_source

    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        policy.write_text("a.py\n")
        return result

    monkeypatch.setattr(project_resources, "stream_source", drift)
    with pytest.raises(SourceConflict, match="ignore policy changed"):
        project_files(tmp_path)
