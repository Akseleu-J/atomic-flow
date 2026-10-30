"""Task index reuse rehashes bytes, preserves edges and keeps cancellation live."""
import os
from copy import deepcopy
from dataclasses import replace

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig, user_dir
from atomic_flow.project_resources import ProjectLimits, ResourceLimitError, require_workspace_paths, workspace_path_error
from atomic_flow.tree import builder


def project(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.py").write_bytes(b"def callback():\n    return 1\n\ndef main():\n    return list(map(callback, []))\n")
    return root


def edges(tree):
    return {item["node_id"]: (item["callees"], item["reference_ids"], item["fan_in"])
            for item in tree["nodes"]}


def test_incremental_refresh_preserves_reference_edges_and_relinks_after_changes(tmp_path):
    root = project(tmp_path)
    first = builder.build_tree(root)
    assert first["nodes"][-1]["reference_ids"] == ["a.py::callback"]
    warm = builder.build_tree(root, previous=first)
    assert warm["stats"]["parsed"] == 0 and warm["stats"]["reused"] == 1
    assert edges(warm) == edges(first)
    # An added ambiguous name cannot keep a stale resolved edge. The unchanged
    # consumer must relink from its raw names against the current whole project.
    (root / "b.py").write_bytes(b"def other():\n    return map(callback, [])\n")
    unique = builder.build_tree(root, previous=warm)
    assert next(n for n in unique["nodes"] if n["node_id"] == "b.py::other")["reference_ids"] == ["a.py::callback"]
    (root / "c.py").write_bytes(b"def callback():\n    return 2\n")
    ambiguous = builder.build_tree(root, previous=unique)
    assert next(n for n in ambiguous["nodes"] if n["node_id"] == "b.py::other")["reference_ids"] == []


def test_legacy_cache_reparses_and_persists_raw_reference_upgrade(tmp_path):
    root = project(tmp_path)
    old = builder.build_tree(root)
    old.pop("reference_format")
    for raw in old["nodes"]:
        raw.pop("reference_names")
    builder.save_tree(root, old)
    fresh, changed = builder.refresh_tree(root)
    assert changed and fresh["stats"]["parsed"] == 1
    assert builder.load_tree(root)["reference_format"] == builder.REFERENCE_FORMAT
    again, changed = builder.refresh_tree(root)
    assert not changed and again["stats"]["reused"] == 1
    assert edges(again) == edges(fresh)


def test_invalid_cached_child_invalidates_entire_file_instead_of_dropping_symbol(tmp_path):
    root = project(tmp_path)
    old = builder.build_tree(root)
    damaged = deepcopy(old)
    damaged["nodes"][-1]["kind"] = "invalid_child_record"
    fresh = builder.build_tree(root, previous=damaged)
    assert fresh["stats"]["parsed"] == 1
    assert fresh["stats"]["reused"] == 0
    assert edges(fresh) == edges(old)


@pytest.mark.parametrize("via_runner", [False, True])
def test_repaired_children_are_persisted_even_if_source_fingerprint_unchanged(tmp_path, via_runner):
    root = project(tmp_path)
    old = builder.build_tree(root)
    damaged = deepcopy(old)
    damaged["nodes"][-1]["kind"] = "invalid_child_record"
    builder.save_tree(root, damaged)
    if via_runner:
        tree = TaskRunner(AtomicConfig(), root, None)._refresh_index(root)
    else:
        tree, changed = builder.refresh_tree(root)
        assert changed
    assert tree["fingerprint"] == old["fingerprint"]
    assert edges(builder.load_tree(root)) == edges(old)


def test_index_respects_host_per_file_envelope_even_with_matching_cache(tmp_path):
    root = project(tmp_path)
    old = builder.build_tree(root)
    with pytest.raises(ResourceLimitError, match="per-file byte limit"):
        builder.build_tree(root, previous=old, limits=replace(ProjectLimits(), max_file_bytes=4))
    # The older explicit source-selection argument still skips filtered files;
    # it does not override a stricter host resource envelope.
    filtered = builder.build_tree(root, max_file_bytes=4)
    assert filtered["files"] == 0 and filtered["stats"]["skipped"] == 1


def test_task_resume_reuses_original_parse_cache_without_skipping_source_hashes(tmp_path, monkeypatch):
    root = project(tmp_path)
    builder.save_tree(root, builder.build_tree(root))
    parsed = []
    original = builder._index_file

    def observe(*args, **kwargs):
        parsed.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "_index_file", observe)
    backend = ScriptedBackend({"parser": {}, "answerer": {"text": "Inspected"},
                               "critic": {"verdict": "ok", "citation_coverage": 1}})
    config = AtomicConfig()
    config.artifacts.save = False
    task = TaskRunner(config, root, backend).run_plan("Inspect", Plan(
        steps=[PlanStep(description="Inspect function", verify_with="none")]))
    assert task.status == "done", task.error
    assert backend.calls
    assert parsed == []  # staged resume, answer refresh and source publication reuse syntax


def test_same_size_same_mtime_source_edit_invalidates_reused_syntax(tmp_path):
    root = project(tmp_path)
    runner = TaskRunner(AtomicConfig(), root, None)
    first = runner._refresh_index(root)
    source = root / "a.py"
    before = source.stat()
    source.write_bytes(source.read_bytes().replace(b"return 1", b"return 2"))
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = runner._refresh_index(root)
    assert changed["stats"]["parsed"] == 1
    assert changed["fingerprint"] != first["fingerprint"]
    assert builder.load_tree(root)["fingerprint"] == changed["fingerprint"]


def test_index_graph_linking_observes_cancel_before_any_cache_commit(tmp_path, monkeypatch):
    root = project(tmp_path)
    initial = builder.build_tree(root)
    builder.save_tree(root, initial)
    original = builder._link
    graph = False

    def check():
        if graph:
            raise TaskCancelled("cancel graph build")

    def link(nodes, cancel_check):
        nonlocal graph
        graph = True
        return original(nodes, cancel_check)

    monkeypatch.setattr(builder, "_link", link)
    runner = TaskRunner(AtomicConfig(), root, None)
    runner.cancel_check = check
    with pytest.raises(TaskCancelled, match="cancel graph build"):
        runner._refresh_index(root)
    assert builder.load_tree(root)["fingerprint"] == initial["fingerprint"]
    # Recovery still refreshes its advisory index after a cancelled task.
    runner._refresh_index(root, recovering=True)


def test_host_path_envelope_refuses_before_workspace_allocation(tmp_path):
    root = project(tmp_path)
    with pytest.raises(ResourceLimitError, match="shorter ATOMIC_FLOW_HOME"):
        ChangeSet.create(root, limits=replace(ProjectLimits(), max_workspace_path_chars=80))
    assert not (user_dir() / "changesets").exists()
    assert (root / "a.py").read_bytes().startswith(b"def callback")


def test_task_runner_host_path_envelope_fails_before_model_step(tmp_path):
    root = project(tmp_path)
    backend = ScriptedBackend({})
    runner = TaskRunner(AtomicConfig(), root, backend,
                        resource_limits=replace(ProjectLimits(), max_workspace_path_chars=80))
    task = runner.run_plan("Inspect", Plan(steps=[PlanStep(description="Inspect", verify_with="none")]))
    assert task.status == "failed" and "shorter ATOMIC_FLOW_HOME" in task.error
    assert backend.calls == []
    assert not (user_dir() / "changesets").exists()


def test_path_envelope_includes_checkpoint_and_atomic_suffix(tmp_path):
    root = tmp_path / "change_fixed"
    relative = "dir/source.py"
    actual = root / "checkpoints" / ("0" * 32) / relative
    plain_length = len(str(actual).encode("utf-16-le")) // 2
    with pytest.raises(ResourceLimitError):
        require_workspace_paths(root, [relative], replace(ProjectLimits(), max_workspace_path_chars=plain_length + 1))
    require_workspace_paths(root, [relative], replace(ProjectLimits(), max_workspace_path_chars=plain_length + 100))
    require_workspace_paths(root, ["x" * 500], ProjectLimits())  # no invented default OS cap


def test_actual_windows_path_error_has_actionable_hint_only_for_path_failures():
    failure = OSError("long path")
    failure.winerror = 206
    assert "shorter ATOMIC_FLOW_HOME" in str(workspace_path_error(failure))
    assert workspace_path_error(OSError("disk failure")) is None
    with pytest.raises(ValueError):
        replace(ProjectLimits(), max_workspace_path_chars=False)


def test_graph_cancellation_checks_do_not_perform_io_for_every_node():
    nodes = [builder.TreeNode(node_id=f"a.py::f{i}", path="a.py", symbol=f"f{i}", kind="function")
             for i in range(500)]
    checks = []
    builder._link(nodes, lambda: checks.append(True))
    assert 1 < len(checks) < 100


def test_large_python_ast_visit_is_cooperatively_cancelled():
    def check():
        raise TaskCancelled("cancel AST traversal")

    source = "\n".join(f"value_{i} = function({i})" for i in range(500))
    with pytest.raises(TaskCancelled, match="cancel AST traversal"):
        builder._index_file("a.py", source, "unused", len(source), 0, True, cancel_check=check)
