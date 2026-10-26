"""Creation and fallback must revalidate source after parsing, before graph CAS."""
import hashlib

from atomic_flow.graph.store import GraphStore
from atomic_flow.graph.sync import store_path, sync_project_graph


def test_initial_graph_source_drift_reconciles_instead_of_accepting_stale_snapshot(tmp_path, monkeypatch):
    import atomic_flow.graph.sync as sync

    path = tmp_path / "a.py"
    path.write_bytes(b"VALUE = 1\n")
    original = sync.clean_build

    def change_after_parse(files):
        result = original(files)
        path.write_bytes(b"VALUE = 2\n")
        return result

    monkeypatch.setattr(sync, "clean_build", change_after_parse)
    result = sync_project_graph(tmp_path, {})
    assert result["fallback"] is True
    node = GraphStore(store_path(tmp_path)).nodes()[0]
    assert node["source"]["digest"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_fallback_source_drift_preserves_previous_graph_snapshot(tmp_path, monkeypatch):
    import atomic_flow.graph.delta as delta
    import atomic_flow.graph.sync as sync

    path = tmp_path / "a.py"
    path.write_bytes(b"VALUE = 1\n")
    assert sync_project_graph(tmp_path, {})["created"]
    target = store_path(tmp_path)
    previous = target.read_bytes()
    path.write_bytes(b"VALUE = 2\n")
    original = delta.clean_build

    def change_after_parse(files):
        result = original(files)
        path.write_bytes(b"VALUE = 3\n")
        return result

    def refuse_delta(*args, **kwargs):
        raise ValueError("forced delta refusal")

    monkeypatch.setattr(sync, "apply_changes", refuse_delta)
    monkeypatch.setattr(delta, "clean_build", change_after_parse)
    result = sync_project_graph(tmp_path, {"a.py": b"VALUE = 2\n"})
    assert result["failed"] is True
    assert target.read_bytes() == previous
