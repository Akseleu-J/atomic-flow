from pathlib import Path

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools.edit_node import EditNodeArgs, EditNodeTool
from atomic_flow.agents.undo import UndoStack
from atomic_flow.tree.node import TreeNode


def make_context(tmp_path: Path, *, enabled: bool, stack: UndoStack | None = None) -> tuple[ToolContext, TreeNode]:
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    node = TreeNode(node_id="sample.py::sample", path="sample.py", symbol="sample",
                    kind="function", start_line=1, end_line=2)
    return ToolContext(tmp_path, {node.node_id: node}, edits_enabled=enabled, undo_stack=stack), node


def test_edit_node_requires_explicit_approval(tmp_path):
    ctx, node = make_context(tmp_path, enabled=False)
    result = EditNodeTool().run(EditNodeArgs(node_id=node.node_id, new_source="def sample():\n    return 2"), ctx)
    assert result["error"] == "edits disabled"
    assert "return 1" in (tmp_path / "sample.py").read_text()


def test_noop_edit_is_not_reported_as_applied(tmp_path):
    stack = UndoStack(tmp_path / "edits")
    ctx, node = make_context(tmp_path, enabled=True, stack=stack)
    result = EditNodeTool().run(EditNodeArgs(node_id=node.node_id, new_source="def sample():\n    return 1"), ctx)
    assert result["error"] == "replacement does not change source"
    assert stack.list() == []


def test_edit_node_verifies_and_records_backup(tmp_path):
    stack = UndoStack(tmp_path / "edits")
    ctx, node = make_context(tmp_path, enabled=True, stack=stack)
    result = EditNodeTool().run(EditNodeArgs(node_id=node.node_id, new_source="def sample():\n    return 2"), ctx)
    assert result["status"] == "applied"
    assert result["checks"] == ["syntax"]
    assert "return 2" in (tmp_path / "sample.py").read_text()
    assert (tmp_path / "edits" / result["edit_id"] / "original.py").exists()


def test_undo_last_works_from_a_new_stack(tmp_path):
    stack = UndoStack(tmp_path / "edits")
    ctx, node = make_context(tmp_path, enabled=True, stack=stack)
    result = EditNodeTool().run(EditNodeArgs(node_id=node.node_id, new_source="def sample():\n    return 2"), ctx)
    restored = UndoStack(tmp_path / "edits").undo(tmp_path, "last")
    assert restored and restored.edit_id == result["edit_id"]
    assert "return 1" in (tmp_path / "sample.py").read_text()
