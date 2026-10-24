"""Regressions for index commit ownership and project admission (audit Q02/Q04).

The pre-W1 defect: every save_tree caller wrote one shared temp path tree.json.tmp and
renamed it, so two writers produced a lost writer and a FileNotFoundError. The fix
commits under .atomic_flow/tree.lock with unique temp files and a generation CAS.
"""
import json
import os
import threading

import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.errors import TreeError
from atomic_flow.project_resources import ProjectLimits, ResourceLimitError
from atomic_flow.tree import builder
from atomic_flow.tree.builder import build_tree, load_tree, save_tree


def write_project(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def tree_json(root):
    return json.loads((root / ".atomic_flow" / "tree.json").read_text(encoding="utf-8"))


def test_concurrent_save_tree_writers_never_collide(tmp_path):
    root = write_project(tmp_path / "proj", {"a.py": "def a():\n    return 1\n"})
    barrier = threading.Barrier(2)
    outcomes = []

    def writer(marker):
        tree = {"writer": marker, "_base_generation": None}
        barrier.wait()
        try:
            outcomes.append((marker, "saved", save_tree(root, tree)))
        except TreeError as exc:
            outcomes.append((marker, "retryable", str(exc)))

    threads = [threading.Thread(target=writer, args=(marker,)) for marker in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    saved = [outcome for outcome in outcomes if outcome[1] == "saved"]
    retryable = [outcome for outcome in outcomes if outcome[1] == "retryable"]
    # Exactly one writer commits; the other observes a retryable busy/stale error.
    assert len(saved) == 1 and len(retryable) == 1
    final = tree_json(root)
    assert final["writer"] == saved[0][0]
    assert not list((root / ".atomic_flow").glob("tree-*.tmp"))


def test_stale_generation_save_is_rejected_then_rebuild_succeeds(tmp_path):
    root = write_project(tmp_path / "proj", {"a.py": "def a():\n    return 1\n"})
    save_tree(root, build_tree(root))
    stale_view = load_tree(root)

    # Another writer commits a different generation between our load and our save.
    (root / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    save_tree(root, build_tree(root))

    with pytest.raises(TreeError, match="stale index generation"):
        save_tree(root, stale_view)
    fresh = build_tree(root)                      # rebuild observes the current generation
    save_tree(root, fresh)
    assert load_tree(root)["fingerprint"] == fresh["fingerprint"]


def test_interrupted_commit_preserves_previous_generation(tmp_path, monkeypatch):
    root = write_project(tmp_path / "proj", {"a.py": "def a():\n    return 1\n"})
    save_tree(root, build_tree(root))
    before = (root / ".atomic_flow" / "tree.json").read_bytes()
    real_replace = os.replace

    def failing_replace(src, dst):
        if str(dst).endswith("tree.json"):
            raise OSError("simulated crash before rename")
        return real_replace(src, dst)

    monkeypatch.setattr(builder.os, "replace", failing_replace)
    current = load_tree(root)                     # carries the on-disk generation past the CAS gate
    current["writer"] = "interrupted"
    with pytest.raises(TreeError, match="index commit unavailable"):
        save_tree(root, current)
    monkeypatch.undo()
    assert (root / ".atomic_flow" / "tree.json").read_bytes() == before
    assert not list((root / ".atomic_flow").glob("tree-*.tmp"))
    save_tree(root, build_tree(root))
    assert load_tree(root)["files"] >= 1


def test_load_tree_generation_and_corruption_guards(tmp_path):
    import hashlib

    root = write_project(tmp_path / "proj", {"a.py": "def a():\n    return 1\n"})
    save_tree(root, build_tree(root))
    raw = (root / ".atomic_flow" / "tree.json").read_bytes()
    tree = load_tree(root)
    assert tree["_base_generation"] == hashlib.sha256(raw).hexdigest()

    (root / ".atomic_flow" / "tree.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TreeError, match="Cannot read project tree"):
        load_tree(root)
    (root / ".atomic_flow" / "tree.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(TreeError, match="must be an object"):
        load_tree(root)


def test_project_limits_refuse_oversized_work(tmp_path):
    root = write_project(tmp_path / "proj", {"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 2\n"})
    with pytest.raises(ResourceLimitError, match="source file limit"):
        build_tree(root, limits=ProjectLimits(max_files=1))
    with pytest.raises(ResourceLimitError, match="node limit"):
        build_tree(root, limits=ProjectLimits(max_nodes=1))
    wide = write_project(tmp_path / "entries", {"a.py": "x = 1\n", "b.py": "x = 2\n", "c.txt": "not source\n"})
    with pytest.raises(ResourceLimitError, match="entry limit"):
        build_tree(wide, limits=ProjectLimits(max_entries=2))
    deep = write_project(tmp_path / "deep", {"a/b/c/d.py": "x = 1\n"})
    with pytest.raises(ResourceLimitError, match="depth limit"):
        build_tree(deep, limits=ProjectLimits(max_depth=1))


def test_cancellation_is_honoured_during_the_walk(tmp_path):
    root = write_project(tmp_path / "proj", {"a.py": "x = 1\n", "b.py": "x = 2\n"})
    with pytest.raises(TaskCancelled):
        build_tree(root, cancel_check=lambda: (_ for _ in ()).throw(TaskCancelled("stop")))

    seen = []

    def cancel_later():
        seen.append(1)
        if len(seen) >= 3:
            raise TaskCancelled("stop mid-walk")

    with pytest.raises(TaskCancelled):
        build_tree(root, cancel_check=cancel_later)


def test_stats_report_skipped_files_and_private_state_stays_out(tmp_path):
    root = write_project(tmp_path / "proj", {"a.py": "x = 1\n", "notes.txt": "not indexed source\n"})
    (root / ".atomic_flow").mkdir()
    (root / ".atomic_flow" / "secret.py").write_text("token = 'x'\n", encoding="utf-8")
    tree = build_tree(root)
    paths = {node["path"] for node in tree["nodes"] if node["kind"] == "file"}
    assert paths == {"a.py"}
    assert tree["stats"]["skipped"] >= 1 and tree["stats"]["read_bytes"] > 0
