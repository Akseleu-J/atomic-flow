"""Source search/lifecycle operations and journal publication acceptance tests."""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from atomic_flow.agents import file_transaction
from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.tools.file_ops import (
    CreateFileArgs,
    CreateFileTool,
    DeleteFileArgs,
    DeleteFileTool,
    MoveFileArgs,
    MoveFileTool,
    PatchFileArgs,
    PatchFileTool,
)
from atomic_flow.agents.tools.search_text import SearchTextArgs, SearchTextTool
from atomic_flow.agents.undo import UndoStack
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode


def context(tmp_path, *, edits=True):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.py").write_text("value = 1\nother = 1\n", encoding="utf-8")
    (root / "README.md").write_text("A small project.\n", encoding="utf-8")
    nodes = {data["node_id"]: TreeNode.model_validate(data) for data in build_tree(root)["nodes"]}
    return ToolContext(root, nodes, edits_enabled=edits, undo_stack=UndoStack(tmp_path / "undo"))


def test_literal_search_reports_current_line_hash_and_redacts(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / "a.py").write_text('api_key = "long-secret-value"\nvalue = 2\nvalue = 3\n', encoding="utf-8")
    (ctx.project_root / "credentials.json").write_text('{"value": "private"}', encoding="utf-8")
    (ctx.project_root / "ignored.py").write_text("value = 9\n", encoding="utf-8")
    (ctx.project_root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    ctx.refresh()
    tool = SearchTextTool()
    result = tool.run(SearchTextArgs(query="value", max_results=1), ctx)
    assert result["results"][0]["line"] == 2  # the credential value was masked before matching
    assert result["results"][0]["path"] == "a.py"
    assert result["results"][0]["content_hash"] == ctx.file_hashes["a.py"]
    assert result["truncated"] and result["next_offset"] == 1
    second = tool.run(SearchTextArgs(query="value", offset=1, max_results=1), ctx)
    assert second["results"][0]["line"] == 3
    assert "long-secret-value" not in str(tool.run(SearchTextArgs(query="api_key"), ctx))
    assert not tool.run(SearchTextArgs(query="private"), ctx)["results"]
    assert not tool.run(SearchTextArgs(query="value", path_glob="ignored.py"), ctx)["results"]


def test_regex_search_works_and_rejects_bad_pattern(tmp_path):
    ctx = context(tmp_path)
    tool = SearchTextTool()
    result = tool.run(SearchTextArgs(query=r"^value\s*=\s*\d+$", regex=True), ctx)
    assert result["results"][0]["line"] == 1
    assert "invalid regex" in tool.run(SearchTextArgs(query="[", regex=True), ctx)["error"]


def test_regex_startup_budget_does_not_consume_evaluation_deadline(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    ticks = iter([0, 6, 6, 7])
    polls = iter([False, True, False, True])
    responses = iter([{"ready": True}, ([{"path": "a.py", "line": 1}], False)])
    receiver = SimpleNamespace(poll=lambda timeout: next(polls), recv=lambda: next(responses), close=lambda: None)
    process = SimpleNamespace(start=lambda: None, is_alive=lambda: True, terminate=lambda: None, join=lambda **kw: None)
    mp = SimpleNamespace(Pipe=lambda **kw: (receiver, SimpleNamespace(close=lambda: None)), Process=lambda **kw: process)
    monkeypatch.setattr("atomic_flow.agents.tools.search_text.multiprocessing.get_context", lambda name: mp)
    monkeypatch.setattr("atomic_flow.agents.tools.search_text.time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert SearchTextTool().run(SearchTextArgs(query="value", regex=True), ctx)["results"][0]["line"] == 1


def test_pathological_regex_is_terminated(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / "a.py").write_text("# " + "a" * 100_000 + "!\n", encoding="utf-8")
    ctx.refresh()
    result = SearchTextTool().run(SearchTextArgs(query="(a+)+$", regex=True, path_glob="a.py"), ctx)
    assert "deadline" in result["error"]


def test_search_skips_stale_source_and_honors_cancellation(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / "a.py").write_text("changed = 9\n", encoding="utf-8")
    result = SearchTextTool().run(SearchTextArgs(query="changed"), ctx)
    assert not result["results"] and result["skipped_files"] == 1
    def cancelled():
        raise TaskCancelled("cancelled")
    ctx.cancel_check = cancelled
    with pytest.raises(TaskCancelled):
        SearchTextTool().run(SearchTextArgs(query="value"), ctx)


def test_patch_is_unique_hash_bound_verified_and_undoable(tmp_path):
    ctx = context(tmp_path)
    tool = PatchFileTool()
    old_hash = ctx.file_hashes["a.py"]
    ambiguous = tool.run(PatchFileArgs(path="a.py", expected_hash=old_hash, old_text="1", new_text="2"), ctx)
    assert "exactly one" in ambiguous["error"]
    result = tool.run(PatchFileArgs(path="a.py", expected_hash=old_hash, old_text="value = 1", new_text="value = 2"), ctx)
    assert result["status"] == "applied" and result["verification"]["syntax"] == "passed"
    assert result["changes"] == [{"path": "a.py", "before_hash": old_hash, "after_hash": ctx.file_hashes["a.py"]}]
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, result["edit_id"])
    assert (ctx.project_root / "a.py").read_text() == "value = 1\nother = 1\n"


def test_stale_patch_and_disabled_mutations_leave_bytes_alone(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / "a.py").write_text("user = 5\n", encoding="utf-8")
    with pytest.raises(SourceConflict):
        PatchFileTool().run(PatchFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"], old_text="value", new_text="new"), ctx)
    assert (ctx.project_root / "a.py").read_text() == "user = 5\n"
    ctx.edits_enabled = False
    assert CreateFileTool().run(CreateFileArgs(path="new.py", content="x=1\n"), ctx)["error"] == "edits disabled"
    assert not (ctx.project_root / "new.py").exists()


def test_syntax_failure_rolls_back_patch_and_creation(tmp_path):
    ctx = context(tmp_path)
    old = (ctx.project_root / "a.py").read_bytes()
    result = PatchFileTool().run(PatchFileArgs(path="a.py", expected_hash=digest(old), old_text="value = 1", new_text="def broken("), ctx)
    assert result["status"] == "rolled_back" and result["verification"]["syntax"] == "failed"
    assert (ctx.project_root / "a.py").read_bytes() == old
    created = CreateFileTool().run(CreateFileArgs(path="bad.py", content="def broken("), ctx)
    assert created["status"] == "rolled_back" and not (ctx.project_root / "bad.py").exists()


def test_create_delete_move_and_undo_preserve_actual_file_states(tmp_path):
    ctx = context(tmp_path)
    created = CreateFileTool().run(CreateFileArgs(path="pkg/new.py", content="x = 3\n"), ctx)
    assert created["status"] == "applied" and created["changes"][0]["before_hash"] is None
    conflict = CreateFileTool().run(CreateFileArgs(path="pkg/new.py", content="x = 9\n"), ctx)
    assert "exists" in conflict["error"]
    deleted = DeleteFileTool().run(DeleteFileArgs(path="pkg/new.py", expected_hash=ctx.file_hashes["pkg/new.py"]), ctx)
    assert deleted["status"] == "applied" and deleted["content_hash"] is None
    assert not (ctx.project_root / "pkg/new.py").exists()
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, deleted["edit_id"])
    ctx.refresh()
    moved = MoveFileTool().run(MoveFileArgs(path="pkg/new.py", expected_hash=ctx.file_hashes["pkg/new.py"], destination="pkg/renamed.py"), ctx)
    assert moved["status"] == "applied" and len(moved["changes"]) == 2
    assert not (ctx.project_root / "pkg/new.py").exists()
    assert (ctx.project_root / "pkg/renamed.py").read_text() == "x = 3\n"
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, moved["edit_id"])
    assert (ctx.project_root / "pkg/new.py").exists() and not (ctx.project_root / "pkg/renamed.py").exists()
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, created["edit_id"])
    assert not (ctx.project_root / "pkg/new.py").exists()


@pytest.mark.parametrize("path", ["../escape.py", "C:/escape.py", "folder\\escape.py", ".env.json", "credentials.json", "secrets/key.py", "a.py:payload", ".atomic_flow/file.py", "image.png"])
def test_private_ignored_or_non_source_paths_are_rejected(tmp_path, path):
    ctx = context(tmp_path)
    with pytest.raises(SourceConflict):
        CreateFileTool().run(CreateFileArgs(path=path, content="x = 1\n"), ctx)


def test_gitignore_and_symlink_paths_cannot_be_created(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    with pytest.raises(SourceConflict):
        CreateFileTool().run(CreateFileArgs(path="ignored/new.py", content="x=1"), ctx)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (ctx.project_root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SourceConflict):
        CreateFileTool().run(CreateFileArgs(path="linked/new.py", content="x=1"), ctx)
    assert not (outside / "new.py").exists()


def test_verifier_concurrent_change_is_preserved(tmp_path, monkeypatch):
    from atomic_flow.agents.verify import VerificationResult
    ctx = context(tmp_path)
    def change_during_verify(root, path, **kwargs):
        (root / path).write_text("user = 99\n", encoding="utf-8")
        return VerificationResult(ok=True, checks=["syntax"])
    monkeypatch.setattr("atomic_flow.agents.tools.file_ops.verify_edit", change_during_verify)
    result = PatchFileTool().run(PatchFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"], old_text="value = 1", new_text="value = 2"), ctx)
    assert result["status"] == "rollback_failed"
    assert (ctx.project_root / "a.py").read_text() == "user = 99\n"


def test_old_module_matching_tests_fail_and_roll_back_move(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from atomic_flow.agents.verify import VerificationResult

    ctx = context(tmp_path)
    (ctx.project_root / "tests").mkdir()
    (ctx.project_root / "tests/test_a.py").write_text("# matching test\n", encoding="utf-8")
    invoked = []
    monkeypatch.setattr("atomic_flow.agents.tools.file_ops.verify_edit",
                        lambda *args, **kwargs: VerificationResult(ok=True, checks=["syntax"]))
    def failed_tests(arguments, root, **kwargs):
        invoked.extend(arguments)
        return SimpleNamespace(returncode=1, timed_out=False)
    monkeypatch.setattr("atomic_flow.agents.tools.file_ops.run_python", failed_tests)
    result = MoveFileTool().run(MoveFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"], destination="new.py"), ctx)
    assert result["status"] == "rolled_back" and result["verification"]["pytest"] == "failed"
    assert "tests/test_a.py" in invoked
    assert (ctx.project_root / "a.py").exists() and not (ctx.project_root / "new.py").exists()


def test_cancel_during_multi_path_move_restores_source(tmp_path):
    ctx = context(tmp_path)
    calls = 0
    def cancel_second_write():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TaskCancelled("cancelled between writes")
    ctx.cancel_check = cancel_second_write
    with pytest.raises(TaskCancelled):
        MoveFileTool().run(MoveFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"], destination="new.py"), ctx)
    assert (ctx.project_root / "a.py").exists() and not (ctx.project_root / "new.py").exists()


def test_move_recovery_preserves_modes_and_conflicting_destination(tmp_path):
    ctx = context(tmp_path)
    source = ctx.project_root / "a.py"
    source.chmod(0o755)
    mode = stat.S_IMODE(source.stat().st_mode)
    result = MoveFileTool().run(MoveFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"], destination="b.py"), ctx)
    assert result["status"] == "applied"
    assert stat.S_IMODE((ctx.project_root / "b.py").stat().st_mode) == mode
    (ctx.project_root / "b.py").write_text("user = 8\n", encoding="utf-8")
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, result["edit_id"]) is None
    assert not source.exists()
    assert (ctx.project_root / "b.py").read_text() == "user = 8\n"


def test_changeset_publishes_create_delete_move_and_checkpoint_restores(tmp_path):
    ctx = context(tmp_path)
    change = ChangeSet.create(ctx.project_root)
    checkpoint = change.checkpoint()
    (change.workspace / "a.py").unlink()
    (change.workspace / "new.py").write_bytes(b"new = 7\n")
    change.track("a.py", None)
    change.track("new.py", digest(b"new = 7\n"))
    changed_checkpoint = change.checkpoint()
    change.restore_checkpoint(checkpoint)
    assert (change.workspace / "a.py").exists() and not (change.workspace / "new.py").exists()
    change.restore_checkpoint(changed_checkpoint)
    change.publish()
    assert not (ctx.project_root / "a.py").exists()
    assert (ctx.project_root / "new.py").read_text() == "new = 7\n"


def test_changeset_explicit_deletion_proof_and_interrupted_create_recovery(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    change = ChangeSet.create(ctx.project_root)
    with pytest.raises(ChangeSetConflict):
        change.track("a.py", None)
    (change.workspace / "a.py").unlink()
    (change.workspace / "new.py").write_bytes(b"new = 7\n")
    change.track("a.py", None)
    change.track("new.py", digest(b"new = 7\n"))
    original = change._write_target
    def interrupted(rel, data):
        original(rel, data)
        if rel == "new.py":
            raise KeyboardInterrupt()
    monkeypatch.setattr(change, "_write_target", interrupted)
    with pytest.raises(KeyboardInterrupt):
        change.publish()
    reopened = ChangeSet.open(ctx.project_root, change.id)
    reopened.recover()
    assert (ctx.project_root / "a.py").exists() and not (ctx.project_root / "new.py").exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix executable mode semantics")
def test_deleted_executable_undo_preserves_mode(tmp_path):
    ctx = context(tmp_path)
    path = ctx.project_root / "a.py"
    path.chmod(0o751)
    result = DeleteFileTool().run(DeleteFileArgs(path="a.py", expected_hash=ctx.file_hashes["a.py"]), ctx)
    assert file_transaction.undo(ctx.undo_stack.storage_dir, ctx.project_root, result["edit_id"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o751


@pytest.mark.skipif(os.name == "nt", reason="Unix executable mode semantics")
def test_changeset_move_publish_and_deleted_recovery_keep_modes(tmp_path):
    ctx = context(tmp_path)
    (ctx.project_root / "a.py").chmod(0o751)
    change = ChangeSet.create(ctx.project_root)
    original = (change.workspace / "a.py").read_bytes()
    (change.workspace / "a.py").unlink()
    (change.workspace / "moved.py").write_bytes(original)
    (change.workspace / "moved.py").chmod(0o751)
    change.track("a.py", None)
    change.track("moved.py", digest(original))
    checkpoint = change.checkpoint()
    (change.workspace / "moved.py").unlink()
    change.track("moved.py", None)
    change.restore_checkpoint(checkpoint)
    assert stat.S_IMODE((change.workspace / "moved.py").stat().st_mode) == 0o751
    def failed_finalize():
        assert stat.S_IMODE((ctx.project_root / "moved.py").stat().st_mode) == 0o751
        raise OSError("finalization failed")
    with pytest.raises(OSError):
        change.publish(finalize=failed_finalize)
    assert stat.S_IMODE((ctx.project_root / "a.py").stat().st_mode) == 0o751
    assert not (ctx.project_root / "moved.py").exists()
