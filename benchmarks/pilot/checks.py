"""Private-to-the-run behavioral checks; never copied into the agent workspace."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def full_file_pages(root):
    from atomic_flow.agents.context import ToolContext
    from atomic_flow.agents.tools.read_node import ReadNodeArgs, ReadNodeTool
    from atomic_flow.tree.builder import build_tree
    from atomic_flow.tree.node import TreeNode

    text = "\n".join(f"# line_{i}" for i in range(240)) + "\n"
    (root / "long.py").write_bytes(text.encode())
    nodes = {n["node_id"]: TreeNode.model_validate(n) for n in build_tree(root)["nodes"]}
    ctx = ToolContext(root, nodes)
    offset, pages, seen = 0, [], set()
    for _ in range(100):
        result = ReadNodeTool().run(ReadNodeArgs(node_id="long.py", zoom=2, offset=offset, limit=256), ctx)
        assert not result.get("error"), result
        pages.append(result["source"])
        nxt = result.get("next_offset")
        if nxt is None:
            break
        assert nxt > offset and nxt not in seen
        seen.add(nxt)
        offset = nxt
    else:
        raise AssertionError("pagination did not terminate")
    assert "line_239" in "".join(pages), "end of file missing"
    assert (root / "long.py").read_bytes() == text.encode(), "read modified source"


def deny_priority(root):
    from atomic_flow.agents.policy import PolicyEngine, Rule

    for rules in ([Rule("edit_node", "auto"), Rule("edit_node", "forbid")],
                  [Rule("edit_node", "forbid"), Rule("edit_node", "auto")]):
        assert PolicyEngine(rules).decide("edit_node", {}, project_trusted=True).action == "forbid"
    assert PolicyEngine([Rule("read_node", "auto")]).decide("read_node", {}, project_trusted=False).action == "auto"


def corrupt_policy(root):
    from atomic_flow.agents.policy import PolicyEngine

    folder = root / ".atomic_flow"
    folder.mkdir()
    for content in ("rules: [", "rules: wrong_type", "rules:\n  - match: {tool: read_node}\n    action: nonsense\n"):
        (folder / "policy.yaml").write_text(content)
        assert PolicyEngine.load(root).decide("read_node", {}, project_trusted=True).action == "forbid"


def task_versions(root):
    from atomic_flow.agents.task import Step, Task, load, save

    task = Task.new("compatibility", root, [Step(0, "Inspect source", verify_with="none")])
    path = save(task)
    state = json.loads(path.read_text())
    assert type(state.get("schema_version")) is int and state["schema_version"] >= 1
    legacy = dict(state)
    legacy.pop("schema_version")
    path.write_text(json.dumps(legacy))
    before = path.read_bytes()
    assert load(task.id).query == task.query and path.read_bytes() == before
    future = dict(state, schema_version=999999)
    path.write_text(json.dumps(future))
    before = path.read_bytes()
    try:
        load(task.id)
    except ValueError as exc:
        assert any(word in str(exc).lower() for word in ("version", "schema", "newer", "supported"))
    else:
        raise AssertionError("future schema accepted")
    assert path.read_bytes() == before


def unique_patch(root):
    from atomic_flow.agents.context import ToolContext
    from atomic_flow.agents.source import digest
    from atomic_flow.agents.tools.file_ops import PatchFileArgs, PatchFileTool
    from atomic_flow.agents.undo import UndoStack
    from atomic_flow.tree.builder import build_tree
    from atomic_flow.tree.node import TreeNode

    path = root / "sample.py"
    original = b"VALUE = 1\nOTHER = 1\n"
    path.write_bytes(original)
    nodes = {n["node_id"]: TreeNode.model_validate(n) for n in build_tree(root)["nodes"]}
    ctx = ToolContext(root, nodes, edits_enabled=True, undo_stack=UndoStack(root / ".undo"))
    args = dict(path="sample.py", expected_hash=digest(original))
    result = PatchFileTool().run(PatchFileArgs(**args, old_text="= 1", new_text="= 9"), ctx)
    assert result.get("error") and path.read_bytes() == original
    result = PatchFileTool().run(PatchFileArgs(**args, old_text="VALUE = 1", new_text="VALUE = 9"), ctx)
    assert result.get("status") == "applied" and path.read_bytes() == b"VALUE = 9\nOTHER = 1\n"
    try:
        result = PatchFileTool().run(PatchFileArgs(**args, old_text="VALUE = 9", new_text="VALUE = 4"), ctx)
    except OSError:
        pass
    else:
        assert result.get("error") or result.get("status") in {"rolled_back", "rollback_failed"}
    assert path.read_bytes() == b"VALUE = 9\nOTHER = 1\n"


def explicit_question(root):
    from atomic_flow.agents.context import ToolContext
    from atomic_flow.agents.tools.ask_user import AskUserArgs, AskUserTool

    args = AskUserArgs(question="Choose a format", options=["JSON", "Text"])
    ctx = ToolContext(root, {})
    result = AskUserTool().run(args, ctx)
    assert result.get("error") and "answer" not in result
    seen = []
    ctx.question_callback = lambda question: seen.append(question) or "JSON"
    result = AskUserTool().run(args, ctx)
    assert len(seen) == 1 and result.get("answer") == "JSON"
    ctx.question_callback = lambda question: None
    result = AskUserTool().run(args, ctx)
    assert result.get("error") and "answer" not in result


CHECKS = {name: value for name, value in list(globals().items()) if name in {
    "full_file_pages", "deny_priority", "corrupt_policy", "task_versions", "unique_patch", "explicit_question"}}


def verify(check_id):
    with tempfile.TemporaryDirectory(prefix="atomic-grader-") as directory:
        root = Path(directory)
        os.environ["ATOMIC_FLOW_HOME"] = str(root / ".state")
        CHECKS[check_id](root)
