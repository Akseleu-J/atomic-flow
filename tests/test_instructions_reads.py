import json

import pytest

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.tools.edit_node import EditNodeArgs, EditNodeTool
from atomic_flow.agents.tools.read_file import ReadFileArgs, ReadFileTool
from atomic_flow.agents.tools.read_node import ReadNodeArgs, ReadNodeTool
from atomic_flow.llm.instructions import load_instructions
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode


def context(root, **kwargs):
    tree = build_tree(root, respect_gitignore=False)
    nodes = {n["node_id"]: TreeNode.model_validate(n) for n in tree["nodes"]}
    return ToolContext(root, nodes, respect_gitignore=False, **kwargs)


def test_file_node_full_zoom_no_longer_stops_at_sixty_lines(tmp_path):
    (tmp_path / "sample.py").write_text("\n".join(f"item_{i} = {i}" for i in range(130)), encoding="utf-8")
    ctx = context(tmp_path, enforce_read_before_edit=True)
    result = ReadNodeTool().run(ReadNodeArgs(node_id="sample.py", zoom=2), ctx)
    assert "item_129" in result["source"]
    assert result["source_complete"] and result["read_complete"]
    ctx.require_read("sample.py", result["content_hash"])


def test_signature_is_not_full_definition_and_edit_requires_read(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    ctx = context(tmp_path, enforce_read_before_edit=True, edits_enabled=True, undo_stack=object())
    result = ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::sample", zoom=0), ctx)
    assert result["rendered_complete"] and not result["read_complete"]
    edit = EditNodeTool().run(EditNodeArgs(node_id="sample.py::sample", new_source="def sample():\n    return 2"), ctx)
    assert "read the complete current source" in edit["error"]
    ReadNodeTool().run(ReadNodeArgs(node_id="sample.py::sample", zoom=2), ctx)
    ctx.require_read("sample.py", result["content_hash"], "sample.py::sample")
    with pytest.raises(OSError, match="read the complete"):
        ctx.require_read("sample.py", result["content_hash"])


def test_paginated_file_tracks_gaps_redaction_and_current_hash(tmp_path):
    source = "#" * 500 + '\npassword = "supersecret123"\n' + "#" * 500
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    ctx = context(tmp_path, enforce_read_before_edit=True)
    tool = ReadFileTool()
    last = tool.run(ReadFileArgs(path="sample.py", offset=768, limit=256), ctx)
    assert not last["read_complete"]
    result = tool.run(ReadFileArgs(path="sample.py", limit=256), ctx)
    pages = [result["source"]]
    while result["next_offset"] is not None:
        result = tool.run(ReadFileArgs(path="sample.py", offset=result["next_offset"], limit=256), ctx)
        pages.append(result["source"])
    assert result["read_complete"] and "supersecret123" not in "".join(pages)
    assert "[REDACTED]" in "".join(pages)
    ctx.require_read("sample.py", result["content_hash"])
    with pytest.raises(OSError):
        ctx.require_read("sample.py", "0" * 40)
    (tmp_path / "sample.py").write_text("changed = 2\n", encoding="utf-8")
    assert "stale source" in tool.run(ReadFileArgs(path="sample.py"), ctx)["error"]
    ctx.refresh()
    assert not ctx.reads


def test_read_file_denies_private_and_escape_paths(tmp_path):
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_text('{"token":"private"}', encoding="utf-8")
    ctx = context(tmp_path)
    for path in ("../sample.py", "secrets.json", ".atomic_flow/state.json", "C:/private.py"):
        assert "error" in ReadFileTool().run(ReadFileArgs(path=path), ctx)
    assert "error" in ReadNodeTool().run(ReadNodeArgs(node_id="secrets.json"), ctx)


def test_node_pages_need_complete_contiguous_view_and_unchanged_refresh_keeps_it(tmp_path):
    source = "def sample():\n" + "".join(f"    item_{i} = {i}\n" for i in range(75)) + "    return item_74\n"
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    ctx = context(tmp_path, enforce_read_before_edit=True)
    tool = ReadNodeTool()
    page = tool.run(ReadNodeArgs(node_id="sample.py::sample", zoom=2, limit=256), ctx)
    assert page["truncated"] and page["source_complete"] and not page["read_complete"]
    with pytest.raises(OSError):
        ctx.require_read("sample.py", page["content_hash"], "sample.py::sample")
    while page["next_offset"] is not None:
        page = tool.run(ReadNodeArgs(node_id="sample.py::sample", zoom=2, offset=page["next_offset"], limit=256), ctx)
    assert page["read_complete"]
    ctx.refresh()
    ctx.require_read("sample.py", page["content_hash"], "sample.py::sample")


def test_instruction_trust_scope_order_dedup_and_redaction(tmp_path):
    project = tmp_path / "project"
    personal = tmp_path / "personal"
    for folder in (project / "pkg", project / "other", personal):
        folder.mkdir(parents=True)
    (personal / "AGENTS.md").write_text("user rule", encoding="utf-8")
    (project / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (project / "pkg" / "AGENTS.md").write_text('package rule\npassword = "supersecret123"', encoding="utf-8")
    (project / "other" / "AGENTS.md").write_text("unrelated rule", encoding="utf-8")
    untrusted = load_instructions(project, ["pkg/code.py"], user_root=personal)
    assert [s.origin for s in untrusted.sources] == ["user"]
    bundle = load_instructions(project, ["pkg/code.py", "pkg/next.py", "../escape.py"], True, user_root=personal)
    assert [(s.origin, s.scope) for s in bundle.sources] == [("user", "all"), ("project", "."), ("project", "pkg")]
    assert "unrelated rule" not in bundle.text and "supersecret123" not in bundle.text
    records = [json.loads(line) for line in bundle.text.splitlines()[1:] if line]
    assert records[-1]["scope"] == "pkg" and "[REDACTED]" in records[-1]["instructions"]
    assert all(len(s.content_hash) == 40 for s in bundle.sources)


def test_instructions_bounded_whole_records_and_private_scope(tmp_path):
    project = tmp_path / "project"
    personal = tmp_path / "personal"
    (project / "secrets_store").mkdir(parents=True)
    personal.mkdir()
    (project / "AGENTS.md").write_text("x" * 10000, encoding="utf-8")
    (project / "secrets_store" / "AGENTS.md").write_text("private instructions", encoding="utf-8")
    bundle = load_instructions(project, ["secrets_store/key.py"], True, user_root=personal, max_bytes=600)
    assert not bundle.sources and not bundle.text
    (project / "AGENTS.md").write_text("x" * 100, encoding="utf-8")
    bundle = load_instructions(project, [], True, user_root=personal, max_bytes=600)
    assert len(bundle.text.encode("utf-8")) <= 600
    assert len(bundle.sources) == 1


def test_instruction_symlink_is_never_followed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        (project / "AGENTS.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert not load_instructions(project, [], True, user_root=tmp_path / "user").sources
