import os

import pytest

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.tools import default_registry
from atomic_flow.agents.tools.read_file import ReadFileArgs, ReadFileTool
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode
from atomic_flow.watching import SourceWatcher, WatchConflict


class Clock:
    value = 0.0
    def __call__(self):
        return self.value


def context(project):
    return ToolContext(project, {n["node_id"]: TreeNode.model_validate(n) for n in build_tree(project)["nodes"]},
                       enforce_read_before_edit=True)


def test_same_size_same_mtime_editor_write_is_never_reused(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("value = 1\n")
    old = build_tree(tmp_path)
    stat = path.stat()
    path.write_text("value = 2\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    fresh = build_tree(tmp_path, previous=old)
    assert old["fingerprint"] != fresh["fingerprint"]
    assert fresh["stats"]["parsed"] == 1
    assert build_tree(tmp_path, previous=fresh)["stats"]["reused"] == 1


def test_debounce_coalesces_create_modify_delete_and_checks_ack(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    clock = Clock()
    watcher = SourceWatcher(tmp_path, interval=0.1, debounce=0.2, clock=clock)
    (tmp_path / "a.py").write_text("value = 2\n")
    first = watcher.poll(force=True)
    assert first.paths == ("a.py",) and not first.ready
    clock.value = 0.1
    (tmp_path / "a.py").unlink()
    (tmp_path / "b.py").write_text("pass\n")
    second = watcher.poll(force=True)
    assert second.paths == ("a.py", "b.py") and not second.ready
    clock.value = 0.31
    stable = watcher.poll(force=True)
    assert stable.ready
    (tmp_path / "b.py").write_text("changed = True\n")
    with pytest.raises(WatchConflict, match="during refresh"):
        watcher.acknowledge(stable)
    clock.value = 0.6
    current = watcher.poll(force=True)
    watcher.acknowledge(current)
    assert watcher.poll(force=True) is None


def test_watcher_invalidation_revokes_read_proof_before_refresh(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    ctx = context(tmp_path)
    read = ReadFileTool().run(ReadFileArgs(path="a.py"), ctx)
    old_hash = read["content_hash"]
    assert ctx.read_coverage("a.py", old_hash)["read_complete"]
    clock = Clock()
    ctx.source_watcher = SourceWatcher(tmp_path, interval=0.1, debounce=0.2, clock=clock)
    (tmp_path / "a.py").write_text("value = 2\n")
    clock.value = 0.15
    result, _ = execute_tool({"tool": "read_file", "args": {"path": "a.py"}}, default_registry(), ctx, allowed_tools=["read_file"])
    assert "debounce" in result["error"]
    assert not ctx.read_coverage("a.py", old_hash)["read_complete"]
    assert "a.py" not in ctx.file_hashes
    clock.value = 0.4
    result, _ = execute_tool({"tool": "read_file", "args": {"path": "a.py"}}, default_registry(), ctx, allowed_tools=["read_file"])
    assert "value = 2" in result["source"]
    assert result["content_hash"] != old_hash and result["read_complete"]


def test_ignore_policy_change_invalidates_previously_visible_file(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    ctx = context(tmp_path)
    old = ReadFileTool().run(ReadFileArgs(path="a.py"), ctx)
    clock = Clock()
    ctx.source_watcher = SourceWatcher(tmp_path, interval=0.1, debounce=0, clock=clock)
    (tmp_path / ".gitignore").write_text("a.py\n")
    clock.value = 0.2
    assert ctx.sync_external_changes()
    assert not ctx.read_coverage("a.py", old["content_hash"])["read_complete"]
    assert "a.py" not in ctx.file_hashes


def test_notebook_changes_watched_without_exposing_outputs(tmp_path):
    (tmp_path / "note.ipynb").write_text('{"cells":[]}')
    clock = Clock()
    watcher = SourceWatcher(tmp_path, debounce=0, clock=clock)
    (tmp_path / "note.ipynb").write_text('{"cells":[],"secret_output":"never show"}')
    event = watcher.poll(force=True)
    assert event.paths == ("note.ipynb",)
    assert "never show" not in str(event.observation())


def test_watch_limit_private_files_symlinks_and_cancel(tmp_path):
    (tmp_path / "a.py").write_text("pass\n")
    (tmp_path / "credentials.json").write_text("do not read")
    (tmp_path / ".env").write_text("do not read")
    watcher = SourceWatcher(tmp_path, max_files=1)
    assert set(watcher._observed) == {"a.py"}
    (tmp_path / "b.py").write_text("pass\n")
    with pytest.raises(WatchConflict, match="limit"):
        watcher.poll(force=True)
    def cancel():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        SourceWatcher(tmp_path, cancel_check=cancel)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("outside = True")
    try:
        (tmp_path / "linked.py").symlink_to(outside)
    except OSError:
        return
    watch = SourceWatcher(tmp_path)
    assert "linked.py" not in watch._observed


def test_stale_preview_arguments_do_not_get_rebased_after_external_edit(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    ctx = context(tmp_path)
    old = ReadFileTool().run(ReadFileArgs(path="a.py"), ctx)
    ctx.edits_enabled = True
    ctx.approval_callback = lambda *args: True
    clock = Clock()
    ctx.source_watcher = SourceWatcher(tmp_path, debounce=0, clock=clock)
    (tmp_path / "a.py").write_text("value = 2\n")
    clock.value = 0.3
    result, record = execute_tool({"tool": "patch_file", "args": {"path": "a.py", "expected_hash": old["content_hash"],
        "old_text": "value = 1", "new_text": "value = 3"}}, default_registry(), ctx, allowed_tools=["patch_file"])
    # It cannot execute from the old read, even if host policy is later approved.
    assert "expected_hash" in result["error"]
    assert (tmp_path / "a.py").read_text() == "value = 2\n"


def test_empty_directories_count_toward_scan_bound(tmp_path):
    for i in range(4):
        (tmp_path / str(i)).mkdir()
    with pytest.raises(WatchConflict, match="entry"):
        SourceWatcher(tmp_path, max_entries=3)
