"""Staged edits, conflict detection and recovery without clobbering user changes."""
import pytest

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict


def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "b.py").write_text("b = 1\n", encoding="utf-8")
    return root


def test_changes_are_staged_until_publish(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")
    assert (root / "a.py").read_text() == "a = 1\n"
    change.publish()
    assert (root / "a.py").read_text() == "a = 2\n"
    assert change.state["status"] == "published"


def test_external_change_aborts_publish_without_overwrite(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")
    (root / "b.py").write_text("b = 99\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict):
        change.publish()
    assert (root / "a.py").read_text() == "a = 1\n"
    assert (root / "b.py").read_text() == "b = 99\n"


def test_unapproved_workspace_modification_is_not_published(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "b.py").write_text("b = 7\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="unapproved"):
        change.publish()
    assert (root / "b.py").read_text() == "b = 1\n"


def test_approved_path_cannot_publish_later_unverified_bytes(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")
    (change.workspace / "a.py").write_text("not verified\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="after verification"):
        change.publish()
    assert (root / "a.py").read_text() == "a = 1\n"


def test_partial_publish_failure_rolls_back_all_files(tmp_path, monkeypatch):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    for rel in ("a.py", "b.py"):
        (change.workspace / rel).write_text("changed\n", encoding="utf-8")
        change.track(rel)
    original = change._write_target

    def fail_second(rel, data):
        if rel == "b.py":
            raise OSError("disk failure")
        original(rel, data)

    monkeypatch.setattr(change, "_write_target", fail_second)
    with pytest.raises(OSError):
        change.publish()
    assert (root / "a.py").read_text() == "a = 1\n"
    assert (root / "b.py").read_text() == "b = 1\n"


def test_recovery_after_interruption_restores_only_own_writes(tmp_path, monkeypatch):
    root = project(tmp_path)
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
    reopened = ChangeSet.open(root, change.id)
    reopened.recover()
    assert (root / "a.py").read_text() == "a = 1\n"
    assert reopened.state["status"] == "rolled_back"


def test_recovery_preserves_user_edit_after_interruption(tmp_path, monkeypatch):
    root = project(tmp_path)
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
    (root / "a.py").write_text("user change\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict):
        ChangeSet.open(root, change.id).recover()
    assert (root / "a.py").read_text() == "user change\n"


def test_finalization_failure_restores_source(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")

    def failed_index_write():
        raise OSError("index disk failure")

    with pytest.raises(OSError):
        change.publish(finalize=failed_index_write)
    assert (root / "a.py").read_text() == "a = 1\n"


def test_concurrent_write_during_publish_is_preserved(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")
    with pytest.raises(ChangeSetConflict):
        change.publish(finalize=lambda: (root / "b.py").write_text("user\n", encoding="utf-8"))
    assert (root / "a.py").read_text() == "a = 1\n"
    assert (root / "b.py").read_text() == "user\n"


def test_step_checkpoint_removes_failed_step_changes_only(tmp_path):
    root = project(tmp_path)
    change = ChangeSet.create(root)
    (change.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    change.track("a.py")
    checkpoint = change.checkpoint()
    (change.workspace / "b.py").write_text("bad\n", encoding="utf-8")
    change.track("b.py")
    change.restore_checkpoint(checkpoint)
    assert (change.workspace / "a.py").read_text() == "a = 2\n"
    assert (change.workspace / "b.py").read_text() == "b = 1\n"
    assert change.state["approved"] == ["a.py"]
