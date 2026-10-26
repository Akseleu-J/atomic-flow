"""Verified move observations survive content edits and publication."""
from conftest import ScriptedBackend

from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.source import digest
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig
from atomic_flow.graph.relocation import owned_move_hints
from atomic_flow.graph.sync import sync_project_graph
from atomic_flow.tree.builder import build_tree, save_tree


def move_call(left, right, content_hash, status="applied"):
    return {"tool": "move_file", "result_summary": {"status": status, "changes": [
        {"path": left, "before_hash": content_hash, "after_hash": None},
        {"path": right, "before_hash": None, "after_hash": content_hash}]}}


def test_only_verified_moves_compose_across_steps():
    h = "a" * 40
    first = owned_move_hints({}, [move_call("a.py", "b.py", h)], {"a.py": h}, {"b.py": h})
    assert first == {"a.py": "b.py"}
    assert owned_move_hints(first, [move_call("b.py", "c.py", h)], {"b.py": h}, {"c.py": h}) == {"a.py": "c.py"}
    assert owned_move_hints({}, [move_call("a.py", "b.py", h, "rolled_back")], {"a.py": h}, {"b.py": h}) == {}
    assert owned_move_hints({}, [move_call("a.py", "b.py", h)], {"a.py": "b" * 40}, {"b.py": h}) == {}


def test_public_move_then_edit_preserves_graph_identity_mapping(tmp_path, isolated_home):
    original = "def value():\n    return 1\n"
    (tmp_path / "old.py").write_bytes(original.encode())
    save_tree(tmp_path, build_tree(tmp_path))
    sync_project_graph(tmp_path, {})
    responses = iter([
        {"tool": "read_file", "args": {"path": "old.py"}},
        {"tool": "move_file", "args": {"path": "old.py", "destination": "new.py", "expected_hash": digest(original.encode())}},
        {"text": "Moved.", "confidence": "high"},
        {"tool": "read_file", "args": {"path": "new.py"}},
        {"tool": "patch_file", "args": {"path": "new.py", "expected_hash": digest(original.encode()),
                                         "old_text": "return 1", "new_text": "return 2"}},
        {"text": "Updated.", "confidence": "high"},
    ])
    backend = ScriptedBackend({"parser": {"task_type": "modify", "focus_terms": ["value"]},
                               "answerer": lambda _: next(responses), "critic": {"verdict": "ok"}})
    config = AtomicConfig()
    config.task.max_replans = 0
    task = TaskRunner(config, tmp_path, backend, approval_callback=lambda *a, **kw: True).run_plan(
        "Move and update value", Plan(steps=[
            PlanStep(description="Move old.py to new.py", verify_with="py_compile"),
            PlanStep(description="Update new.py value", depends_on=[0], verify_with="py_compile")]),
        allowed_paths=["old.py", "new.py"])
    assert task.status == "done", task.error
    assert not (tmp_path / "old.py").exists()
    assert "return 2" in (tmp_path / "new.py").read_text()
    assert not task.graph_sync["fallback"] and not task.graph_sync["failed"]
    relocation = task.graph_sync["relocation"]
    assert relocation["identity_mapping"]["sym:old.py::value"] == "sym:new.py::value"
    assert relocation["path_moves"][0]["method"] == "provided_hint"
