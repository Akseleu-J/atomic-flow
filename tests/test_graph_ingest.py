"""P02 external ingestion: git status -> file deltas -> parity-guarded store."""
import subprocess

import pytest

from atomic_flow.graph.ingest import changed_paths, ingest_git_changes
from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore
from atomic_flow.graph.sync import sync_project_graph


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(root), "PATH": __import__("os").environ["PATH"]})


def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q")
    (root / "app.py").write_text("from lib import helper\n\n\ndef run():\n    return helper()\n", encoding="utf-8")
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return root


def parity(store, root):
    expected = clean_build(project_files(root))
    def key(nodes):
        return sorted((n["id"], sorted(n.get("facts", {}).items(), key=str)) for n in nodes)
    def edges(edge_list):
        return sorted((e["kind"], e["src"], e["dst"]) for e in edge_list)
    live = store.document()
    assert key(live["nodes"]) == key(expected["nodes"])
    assert edges(live["edges"]) == edges(expected["edges"])


def test_modified_and_untracked_files_ingest_to_parity(tmp_path):
    root = repo(tmp_path)
    sync_project_graph(root, {})  # create the store from the committed state
    (root / "lib.py").write_text("def assist():\n    return 1\n", encoding="utf-8")          # modified
    (root / "extra.py").write_text("VALUE = 2\n", encoding="utf-8")                          # untracked
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    report = ingest_git_changes(store, root)
    assert report["deltas"] == 2 and not report["fallback"]
    parity(store, root)


def test_deleted_file_ingests_and_drops_edges(tmp_path):
    root = repo(tmp_path)
    sync_project_graph(root, {})
    (root / "app.py").unlink()
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    report = ingest_git_changes(store, root)
    assert report["deltas"] == 1 and not report["fallback"]
    assert all(n["path"] != "app.py" for n in store.nodes())
    parity(store, root)


def test_clean_tree_is_noop_and_non_git_root_refused(tmp_path):
    root = repo(tmp_path)
    sync_project_graph(root, {})
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    assert ingest_git_changes(store, root) == {"deltas": 0, "unchanged": True}
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GraphContractError, match="Git root"):
        ingest_git_changes(store, plain)


def test_nested_untracked_rename_and_committed_drift(tmp_path):
    root = repo(tmp_path)
    sync_project_graph(root, {})
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    (root / "nested").mkdir()
    (root / "nested" / "new.py").write_text("VALUE = 4\n", encoding="utf-8")
    git(root, "mv", "lib.py", "renamed.py")
    hints = changed_paths(root)
    assert "nested/new.py" in hints and hints["lib.py"] is None and "renamed.py" in hints
    report = ingest_git_changes(store, root)
    assert report["deltas"] == 3 and not report["fallback"]
    parity(store, root)
    (root / "renamed.py").write_text("def helper():\n    return 9\n", encoding="utf-8")
    git(root, "add", "renamed.py", "nested/new.py")
    git(root, "commit", "-q", "-m", "outside commit")
    assert ingest_git_changes(store, root)["deltas"] == 1
    parity(store, root)


def test_cancel_mid_batch_leaves_persisted_graph_unchanged(tmp_path, monkeypatch):
    import atomic_flow.graph.ingest as module
    from atomic_flow.agents.execution import TaskCancelled
    root = repo(tmp_path)
    sync_project_graph(root, {})
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    before = store.path.read_bytes()
    (root / "app.py").write_text("A = 2\n", encoding="utf-8")
    (root / "lib.py").write_text("B = 3\n", encoding="utf-8")
    original = module.apply_delta
    def cancel_after_first(staged, delta):
        original(staged, delta)
        raise TaskCancelled("stop")
    monkeypatch.setattr(module, "apply_delta", cancel_after_first)
    with pytest.raises(TaskCancelled):
        ingest_git_changes(store, root)
    assert store.path.read_bytes() == before


def test_ignored_and_private_files_are_never_ingested(tmp_path):
    root = repo(tmp_path)
    sync_project_graph(root, {})
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "ignored.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / ".hidden.py").write_text("SECRET = 2\n", encoding="utf-8")
    assert not ({"ignored.py", ".hidden.py"} & changed_paths(root).keys())
    store = GraphStore(root / ".atomic_flow" / "graph.json")
    assert ingest_git_changes(store, root)["unchanged"]
