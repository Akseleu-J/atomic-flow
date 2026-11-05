"""Whole-source snapshots enforce host resource envelopes before task execution."""
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict, _files, _git
from atomic_flow.config import user_dir
from atomic_flow.project_resources import ProjectLimits, ResourceLimitError, stream_source


def project(tmp_path, contents=(b"1234", b"5678")):
    root = tmp_path / "source"
    root.mkdir()
    for index, data in enumerate(contents):
        (root / f"file{index}.py").write_bytes(data)
    return root


@pytest.mark.parametrize("overrides,message", [
    ({"max_files": 1}, "file count"),
    ({"max_file_bytes": 3}, "per-file"),
    ({"max_total_bytes": 7}, "total byte"),
    ({"max_entries": 1}, "entry limit"),
])
def test_snapshot_refuses_complete_operation_without_allocating_workspace(tmp_path, overrides, message):
    root = project(tmp_path)
    with pytest.raises(ResourceLimitError, match=message):
        ChangeSet.create(root, limits=replace(ProjectLimits(), **overrides))
    assert not (user_dir() / "changesets").exists()
    assert sorted(path.read_bytes() for path in root.iterdir()) == [b"1234", b"5678"]


def test_depth_admission_and_secret_exclusion(tmp_path):
    root = project(tmp_path)
    nested = root / "one" / "two"
    nested.mkdir(parents=True)
    (nested / "source.py").write_bytes(b"ok")
    (root / ".env").write_bytes(b"synthetic secret" * 20)
    with pytest.raises(ResourceLimitError, match="depth limit"):
        _files(root, limits=replace(ProjectLimits(), max_depth=1))
    files = _files(root, limits=replace(ProjectLimits(), max_file_bytes=4))
    assert ".env" not in files and "one/two/source.py" in files


def test_disk_floor_refuses_before_workspace_allocation(tmp_path, monkeypatch):
    import atomic_flow.project_resources as resources

    root = project(tmp_path)
    monkeypatch.setattr(resources.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 1})())
    with pytest.raises(ResourceLimitError, match="disk reserve"):
        ChangeSet.create(root)
    assert not (user_dir() / "changesets").exists()


def test_streaming_hash_and_copy_do_not_read_whole_source(tmp_path, monkeypatch):
    root = project(tmp_path, (b"abc" * 100_000,))
    real = Path.read_bytes

    def no_source_whole_reads(path):
        if path.name == "file0.py":
            raise AssertionError("source must stream")
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", no_source_whole_reads)
    limits = replace(ProjectLimits(), chunk_bytes=1024)
    checks = []
    change = ChangeSet.create(root, limits=limits, cancel_check=lambda: checks.append(1))
    assert len(checks) > 300
    assert stream_source(change.workspace / "file0.py", limits, lambda: None) == (
        change.state["baseline"]["file0.py"], 300_000)
    reopened = ChangeSet.open(root, change.id)
    assert reopened.limits == limits
    reopened.check_original()


def test_copy_cancellation_retains_creation_failed_journal_and_no_partial_file(tmp_path, monkeypatch):
    import atomic_flow.agents.changeset as module

    root = project(tmp_path)
    real = module.stream_source

    class Cancelled(Exception):
        pass

    def cancel_copy(path, limits, check, **kwargs):
        if kwargs.get("sink") is not None:
            kwargs["sink"].write(b"partial")
            raise Cancelled("cancelled in bounded copy")
        return real(path, limits, check, **kwargs)

    monkeypatch.setattr(module, "stream_source", cancel_copy)
    with pytest.raises(Cancelled):
        ChangeSet.create(root)
    journals = list((user_dir() / "changesets").glob("*/journal.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text())["status"] == "creation_failed"
    assert not (journals[0].parent / "workspace" / "file0.py").exists()


def test_copy_changed_source_refuses_and_original_stays_external_version(tmp_path, monkeypatch):
    import atomic_flow.agents.changeset as module

    root = project(tmp_path)
    real = module._copy_source

    def changed(source, target, *args, **kwargs):
        source.write_bytes(b"zzzz")
        return real(source, target, *args, **kwargs)

    monkeypatch.setattr(module, "_copy_source", changed)
    with pytest.raises(ChangeSetConflict, match="source changed"):
        ChangeSet.create(root)
    assert (root / "file0.py").read_bytes() == b"zzzz"


def test_workspace_rechecks_use_persisted_limits_and_checkpoint_streaming(tmp_path):
    root = project(tmp_path)
    limits = replace(ProjectLimits(), max_files=2, max_file_bytes=4, max_total_bytes=8, chunk_bytes=2)
    change = ChangeSet.create(root, limits=limits)
    checkpoint = change.checkpoint()
    assert checkpoint["files"] == change.state["baseline"]
    (change.workspace / "file2.py").write_bytes(b"x")
    with pytest.raises(ResourceLimitError, match="file count"):
        change.validate_workspace()
    with pytest.raises(ResourceLimitError, match="file count"):
        ChangeSet.open(root, change.id).validate_workspace()


def test_git_inventory_output_cap_refuses_without_unbounded_capture(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git unavailable")
    assert _git(tmp_path, "--version", output_limit=1024).startswith(b"git version")
    with pytest.raises(ResourceLimitError, match="inventory output limit"):
        _git(tmp_path, "--version", output_limit=1)


def test_checkpoint_recovery_finishes_after_task_cancel_and_keeps_source(tmp_path):
    root = project(tmp_path, (b"x=1\n",))
    cancelled = False

    def check():
        if cancelled:
            raise RuntimeError("cancelled task")

    change = ChangeSet.create(root, cancel_check=check)
    checkpoint = change.checkpoint()
    (change.workspace / "file0.py").write_bytes(b"x=2\n")
    change.track("file0.py")
    cancelled = True
    change.restore_checkpoint(checkpoint)
    assert (change.workspace / "file0.py").read_bytes() == b"x=1\n"
    assert (root / "file0.py").read_bytes() == b"x=1\n"
    with pytest.raises(RuntimeError, match="cancelled task"):
        change.validate_workspace()


def test_private_worktree_creation_honors_inventory_output_envelope(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git unavailable")
    root = project(tmp_path, (b"x=1\n",))
    _git(root, "init", "--template=")
    _git(root, "add", "file0.py")
    limits = replace(ProjectLimits(), max_total_bytes=8)
    with pytest.raises(ResourceLimitError, match="inventory output limit"):
        ChangeSet.create(root, backend="git-worktree", limits=limits)
    journal = next((user_dir() / "changesets").glob("*/journal.json"))
    assert json.loads(journal.read_text())["status"] == "creation_failed"
    assert (root / "file0.py").read_bytes() == b"x=1\n"
