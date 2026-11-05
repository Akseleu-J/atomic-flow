"""Edits and reads must use the exact indexed revision and keep stable aliases."""
import pytest

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools.edit_node import EditNodeArgs, EditNodeTool
from atomic_flow.agents.tools.read_node import ReadNodeArgs, ReadNodeTool
from atomic_flow.agents.undo import UndoStack
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode


def context(root):
    nodes = [TreeNode.model_validate(n) for n in build_tree(root)["nodes"]]
    aliases = {n.node_id: f"n{i}" for i, n in enumerate(nodes, 1)}
    return ToolContext(root, {n.node_id: n for n in nodes}, aliases=aliases,
                       alias_to_id={a: n for n, a in aliases.items()}, edits_enabled=True,
                       undo_stack=UndoStack(root.parent / "edits"))


def test_external_edit_is_never_overwritten(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("def first():\n    return 1\n", encoding="utf-8")
    ctx = context(tmp_path)
    path.write_text("# user edit\ndef first():\n    return 3\n", encoding="utf-8")
    result = EditNodeTool().run(EditNodeArgs(node_id="sample.py::first", new_source="def first():\n    return 2"), ctx)
    assert "stale" in result["error"]
    assert "return 3" in path.read_text()
    assert ctx.undo_stack.list() == []


def test_second_edit_uses_refreshed_ranges_and_stable_alias(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    ctx = context(tmp_path)
    second_alias = ctx.aliases["sample.py::second"]
    first = EditNodeTool().run(EditNodeArgs(node_id="sample.py::first",
        new_source="def first():\n    extra = 10\n    more = 20\n    return extra + more"), ctx)
    assert first["status"] == "applied"
    assert ctx.nodes_by_id["sample.py::second"].start_line == 6
    assert ctx.aliases["sample.py::second"] == second_alias
    second = EditNodeTool().run(EditNodeArgs(node_id=second_alias, new_source="def second():\n    return 42"), ctx)
    assert second["status"] == "applied"
    assert "return extra + more" in path.read_text() and "return 42" in path.read_text()
    assert ctx.index.search("second").ranked


def test_cached_read_checks_hash_and_redacts_secrets(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text('def first():\n    token = "sensitive-value-123"\n    return token\n', encoding="utf-8")
    ctx = context(tmp_path)
    initial = ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::first"), ctx)
    assert "sensitive-value-123" not in initial["source"]
    path.write_text("def first():\n    return 9\n", encoding="utf-8")
    assert "stale" in ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::first"), ctx)["error"]


def test_undo_is_bound_to_project_and_current_content(tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    path = root / "sample.py"
    path.write_text("def first():\n    return 1\n", encoding="utf-8")
    ctx = context(root)
    result = EditNodeTool().run(EditNodeArgs(node_id="sample.py::first", new_source="def first():\n    return 2"), ctx)
    other = tmp_path / "b"
    other.mkdir()
    (other / "sample.py").write_text("user-owned\n", encoding="utf-8")
    assert ctx.undo_stack.undo(other, result["edit_id"]) is None
    assert (other / "sample.py").read_text() == "user-owned\n"
    path.write_text("user edit\n", encoding="utf-8")
    assert ctx.undo_stack.undo(root, result["edit_id"]) is None
    assert path.read_text() == "user edit\n"


def test_verifier_exception_rolls_back(tmp_path, monkeypatch):
    import atomic_flow.agents.tools.edit_node as module

    path = tmp_path / "sample.py"
    path.write_text("def first():\n    return 1\n", encoding="utf-8")
    ctx = context(tmp_path)
    monkeypatch.setattr(module, "verify_edit", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("verifier failed")))
    result = EditNodeTool().run(EditNodeArgs(node_id="sample.py::first", new_source="def first():\n    return 2"), ctx)
    assert result["status"] == "rolled_back"
    assert path.read_text() == "def first():\n    return 1\n"


def test_path_escape_is_rejected(tmp_path):
    node = TreeNode(node_id="evil", path="../outside.py", symbol="f", kind="function")
    ctx = ToolContext(tmp_path, {"evil": node}, edits_enabled=True, undo_stack=UndoStack(tmp_path / "edits"))
    assert "error" in ReadNodeTool().run(ReadNodeArgs(node_id="evil"), ctx)
    assert "error" in EditNodeTool().run(EditNodeArgs(node_id="evil", new_source="def f(): pass"), ctx)


def test_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / "external.py"
    outside.write_text("def f(): return 1\n", encoding="utf-8")
    try:
        (tmp_path / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted")
    node = TreeNode(node_id="link.py::f", path="link.py", symbol="f", kind="function")
    ctx = ToolContext(tmp_path, {node.node_id: node})
    assert "error" in ReadNodeTool().run(ReadNodeArgs(node_id=node.node_id), ctx)


def test_long_body_pagination_records_only_visible_evidence(tmp_path):
    source = "def first():\n" + "".join(f"    value_{i} = {i}\n" for i in range(150)) + "    return final_marker\n"
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    ctx = context(tmp_path)
    first = ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::first", limit=256), ctx)
    assert first["truncated"] and "final_marker" not in ctx.evidence["sample.py::first"]
    offset = first["next_offset"]
    while offset is not None:
        page = ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::first", limit=256, offset=offset), ctx)
        offset = page["next_offset"]
    assert "final_marker" in ctx.evidence["sample.py::first"]


def test_crlf_and_bom_are_preserved(tmp_path):
    path = tmp_path / "sample.py"
    original = b"\xef\xbb\xbfdef first():\r\n    return 1\r\n"
    path.write_bytes(original)
    ctx = context(tmp_path)
    result = EditNodeTool().run(EditNodeArgs(node_id="sample.py::first", new_source="def first():\n    return 2"), ctx)
    assert result["status"] == "applied"
    assert path.read_bytes() == original.replace(b"return 1", b"return 2")


def test_private_state_is_not_indexed_even_inside_project(tmp_path, monkeypatch):
    private = tmp_path / "state"
    private.mkdir()
    (private / "original.py").write_text("def private_backup(): pass\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def sample(): pass\n", encoding="utf-8")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(private))
    assert {node["path"] for node in build_tree(tmp_path)["nodes"]} == {"sample.py"}
