"""Actual Git worktrees must retain managed snapshot and publication authority boundaries."""
import shutil
import subprocess

import pytest

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict


def git(root, *args):
    result = subprocess.run([shutil.which("git"), "-c", "core.hooksPath=", *args], cwd=root,
                            capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repository(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git is required for actual worktree tests")
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "b.py").write_text("b = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "baseline")
    return root


def test_worktree_snapshot_dirty_untracked_and_reopen_without_source_git_changes(repository):
    (repository / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repository / "new.py").write_text("untracked = True\n", encoding="utf-8")
    before = git(repository, "status", "--porcelain=v1", "-z")
    head = git(repository, "rev-parse", "HEAD")
    change = ChangeSet.create(repository, backend="git-worktree")
    assert (change.workspace / "a.py").read_bytes() == (repository / "a.py").read_bytes()
    assert (change.workspace / "new.py").read_bytes() == (repository / "new.py").read_bytes()
    marker = (change.workspace / ".git").read_text()
    assert "git-private" in marker and str(repository / ".git") not in marker
    assert git(change.workspace, "rev-parse", "HEAD").decode().strip() == change.state["workspace_commit"]
    assert git(change.workspace, "status", "--porcelain=v1", "-z") == b""
    reopened = ChangeSet.open(repository, change.id)
    assert reopened.validate_workspace() == change.state["baseline"]
    assert git(repository, "status", "--porcelain=v1", "-z") == before
    assert git(repository, "rev-parse", "HEAD") == head


def test_worktree_publication_keeps_hash_approval_and_original_conflict_gates(repository):
    change = ChangeSet.create(repository, backend="git-worktree")
    (change.workspace / "a.py").write_text("a = 3\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="unapproved"):
        change.publish()
    change.track("a.py")
    (repository / "nested" / "b.py").write_text("user = 9\n", encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="original project changed"):
        change.publish()
    assert (repository / "a.py").read_text() == "a = 1\n"
    assert (repository / "nested" / "b.py").read_text() == "user = 9\n"


def test_worktree_success_publish_and_explicit_terminal_cleanup(repository):
    change = ChangeSet.create(repository, backend="git-worktree")
    with pytest.raises(ChangeSetConflict, match="must be retained"):
        change.cleanup()
    (change.workspace / "a.py").write_text("a = 3\n", encoding="utf-8")
    change.track("a.py")
    change.publish()
    change.cleanup()
    assert not change.workspace.exists()
    assert (change.directory / "journal.json").is_file()
    reopened = ChangeSet.open(repository, change.id)
    reopened.verify_published()
    assert (repository / "a.py").read_text() == "a = 3\n"


def test_worktree_interrupted_publication_recovers_after_reopen(repository, monkeypatch):
    change = ChangeSet.create(repository, backend="git-worktree")
    for name in ("a.py", "nested/b.py"):
        (change.workspace / name).write_text("changed = True\n", encoding="utf-8")
        change.track(name)
    write = change._write_target

    def crash(name, data):
        if name != "a.py":
            raise KeyboardInterrupt
        write(name, data)

    monkeypatch.setattr(change, "_write_target", crash)
    with pytest.raises(KeyboardInterrupt):
        change.publish()
    reopened = ChangeSet.open(repository, change.id)
    reopened.recover()
    assert reopened.state["status"] == "rolled_back"
    assert (repository / "a.py").read_text() == "a = 1\n"
    assert reopened.workspace.exists()


def test_worktree_abort_removes_only_owned_workspace_and_retains_evidence(repository):
    change = ChangeSet.create(repository, backend="git-worktree")
    (change.workspace / "new.py").write_text("failed = True\n", encoding="utf-8")
    change.abort()
    assert not change.workspace.exists()
    assert ChangeSet.open(repository, change.id).state["status"] == "aborted"
    assert (change.directory / "git-private").is_dir()
    assert (repository / "a.py").read_text() == "a = 1\n"


def test_worktree_metadata_substitution_blocks_reopen_and_cleanup(repository, tmp_path):
    change = ChangeSet.create(repository, backend="git-worktree")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("keep")
    (change.workspace / ".git").unlink()  # Git marks this hidden on Windows; replace as an attacker could.
    (change.workspace / ".git").write_text("gitdir: " + str(foreign), encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="marker changed"):
        ChangeSet.open(repository, change.id)
    with pytest.raises(ChangeSetConflict, match="marker changed"):
        change.abort()
    assert (foreign / "keep.txt").read_text() == "keep"
    assert change.workspace.exists()


def test_worktree_registration_substitution_is_not_removed(repository, tmp_path):
    change = ChangeSet.create(repository, backend="git-worktree")
    registration = change.directory / "git-private" / "worktrees" / "workspace" / "gitdir"
    registration.write_text(str(repository / ".git"), encoding="utf-8")
    with pytest.raises(ChangeSetConflict, match="registration changed"):
        change.abort()
    assert (repository / ".git").is_dir()


def test_worktree_private_git_config_changes_fail_before_execution_cleanup(repository):
    change = ChangeSet.create(repository, backend="git-worktree")
    with (change.directory / "git-private" / "config").open("a", encoding="utf-8") as handle:
        handle.write("\n[core]\n hooksPath = /arbitrary\n")
    with pytest.raises(ChangeSetConflict, match="config changed"):
        change.validate_workspace()
    with pytest.raises(ChangeSetConflict, match="config changed"):
        change.abort()
    assert change.workspace.exists()


def test_worktree_rejects_submodules_without_checkout(repository):
    oid = git(repository, "rev-parse", "HEAD").decode().strip()
    git(repository, "update-index", "--add", "--cacheinfo", f"160000,{oid},module")
    with pytest.raises(ChangeSetConflict, match="submodules"):
        ChangeSet.create(repository, backend="git-worktree")
    assert not (repository / "module").exists()


def test_worktree_creation_does_not_inherit_git_template_hooks_or_config(repository, tmp_path, monkeypatch):
    sentinel = tmp_path / "hook-ran"
    template = tmp_path / "template"
    (template / "hooks").mkdir(parents=True)
    (template / "hooks" / "post-checkout").write_text("#!/bin/sh\ntouch '" + sentinel.as_posix() + "'\nexit 91\n")
    (template / "hooks" / "post-checkout").chmod(0o755)
    git(repository, "config", "core.hooksPath", str(template / "hooks"))
    git(repository, "config", "filter.host.clean", "exit 92")
    (repository / ".gitattributes").write_text("*.py filter=host\n", encoding="utf-8")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(template / "hooks"))
    change = ChangeSet.create(repository, backend="git-worktree")
    assert not sentinel.exists()
    assert not (change.directory / "git-private" / "hooks" / "post-checkout").exists()


def test_worktree_rejects_managed_source_symlink(repository, tmp_path):
    outside = tmp_path / "private.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    try:
        (repository / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(OSError, match="(symlink|escapes)"):
        ChangeSet.create(repository, backend="git-worktree")
    assert outside.read_text() == "secret = True\n"


def test_worktree_rejects_non_repository_and_unknown_backend(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    with pytest.raises(ChangeSetConflict, match="repository root"):
        ChangeSet.create(root, backend="git-worktree")
    with pytest.raises(ChangeSetConflict, match="unknown workspace"):
        ChangeSet.create(root, backend="anything")
