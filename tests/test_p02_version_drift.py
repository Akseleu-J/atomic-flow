"""A versioned cache may not silently reuse stale extractor/resolver facts."""
import pytest

from atomic_flow.graph.project import clean_build
from atomic_flow.graph.store import GraphStore
from atomic_flow.graph.sync import store_path, sync_project_graph


@pytest.mark.parametrize("drift", ["parser", "resolver"])
def test_changed_extractor_or_resolver_forces_reasoned_rebuild(tmp_path, drift):
    files = {"a.py": b"def a(): return 1\n", "b.py": b"from a import a\na()\n"}
    for path, raw in files.items():
        (tmp_path / path).write_bytes(raw)
    sync_project_graph(tmp_path, {})
    store = GraphStore(store_path(tmp_path))
    if drift == "parser":
        node = next(n for n in store.nodes() if n["kind"] == "file")
        node["facts"]["extractor"] = "python-ast/old"
        store.commit(base_generation=store.generation, put_nodes=[node])
    else:
        edge = next(e for e in store.edges() if e["facts_class"] == "inferred")
        edge["facts"]["resolver"] = "name-resolver/old"
        store.commit(base_generation=store.generation, put_edges=[edge])
    result = sync_project_graph(tmp_path, {})
    assert result["fallback"] and drift in result["reason"]
    expected = clean_build(files)
    actual = GraphStore(store_path(tmp_path)).document()
    assert sorted(actual["nodes"], key=lambda n: n["id"]) == sorted(expected["nodes"], key=lambda n: n["id"])
    assert sorted(actual["edges"], key=lambda e: e["id"]) == sorted(expected["edges"], key=lambda e: e["id"])
