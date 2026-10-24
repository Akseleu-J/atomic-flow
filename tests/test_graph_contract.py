"""P01 graph contract regressions: schema, adapter identity, CAS store, migration."""
import json

import pytest

from atomic_flow.graph import (
    EXTRACTOR,
    GraphContractError,
    GraphGenerationConflict,
    GraphStore,
    adapt_file,
    edge_id,
    file_id,
    migrate,
    symbol_id,
)

FIXTURE = b"""import os\nfrom pathlib import Path\n\n\nclass Alpha:\n    def one(self):\n        return os.getcwd()\n\n    def one(self):  # duplicate name gets #2, never a merge\n        return 2\n\n\ndef beta(alpha):\n    return alpha.one()\n"""


def test_adapter_extracts_symbols_imports_and_calls_with_stable_identity():
    first = adapt_file("pkg/mod.py", FIXTURE)
    second = adapt_file("pkg/mod.py", FIXTURE)
    assert first == second, "same bytes must produce identical facts"
    ids = {node["id"] for node in first["nodes"]}
    assert file_id("pkg/mod.py") in ids
    assert symbol_id("pkg/mod.py", "Alpha") in ids
    assert symbol_id("pkg/mod.py", "Alpha.one", 1) in ids
    assert symbol_id("pkg/mod.py", "Alpha.one", 2) in ids, "duplicate symbol disambiguated"
    assert symbol_id("pkg/mod.py", "beta") in ids
    file_node = next(n for n in first["nodes"] if n["kind"] == "file")
    assert set(file_node["facts"]["imports"]) == {"os", "pathlib.Path"}
    assert "getcwd" in file_node["facts"]["raw_calls"] and "one" in file_node["facts"]["raw_calls"]
    contains = [e for e in first["edges"] if e["kind"] == "contains"]
    assert len(contains) == 4  # file->Alpha, Alpha->one#1, Alpha->one#2, file->beta
    assert all(e["facts"]["extractor"] == EXTRACTOR for e in contains)


def test_adapter_reports_parser_failure_without_raising():
    result = adapt_file("broken.py", b"def broken(:\n")
    file_node = result["nodes"][0]
    assert file_node["facts"]["parse_ok"] is False and result["edges"] == []


def test_store_commit_is_generation_cas_and_export_is_canonical(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    payload = adapt_file("a.py", b"def f():\n    return 1\n")
    generation = store.commit(base_generation=0, put_nodes=payload["nodes"], put_edges=payload["edges"])
    assert generation == 1
    with pytest.raises(GraphGenerationConflict):
        store.commit(base_generation=0, put_nodes=payload["nodes"])
    reread = GraphStore(tmp_path / "graph.json")
    assert reread.export_canonical() == store.export_canonical()
    assert reread.export_canonical() == reread.export_canonical()
    # Reopening the serialized store yields byte-identical canonical export.
    assert json.loads(reread.export_canonical())["generation"] == 1


def test_store_refuses_dangling_edges_and_forged_identity(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    orphan_edge = {"id": edge_id("file:a.py", "contains", "sym:a.py::ghost"),
                   "kind": "contains", "src": "file:a.py", "dst": "sym:a.py::ghost",
                   "status": "active", "facts": {}, "fact_status": {}, "facts_class": "extracted"}
    with pytest.raises(GraphContractError):
        store.commit(base_generation=0, put_edges=[orphan_edge])
    twin_a = {"id": symbol_id("a.py", "f"), "kind": "function", "path": "a.py", "symbol": "f",
              "source": {"digest": "d", "algorithm": "sha256"}, "start_line": 1, "end_line": 1,
              "facts": {}, "fact_status": {}, "facts_class": "extracted"}
    twin_b = dict(twin_a, id=symbol_id("a.py", "f", 2))
    store.commit(base_generation=0, put_nodes=[twin_a, twin_b])  # disambiguator separates them
    forged = dict(twin_b, symbol="g")  # id says f#2, symbol says g
    with pytest.raises(GraphContractError):
        store.commit(base_generation=1, put_nodes=[forged])


def test_migration_preserves_supported_facts_and_reports_unsupported(tmp_path):
    legacy = {"version": 4, "nodes": [
        {"node_id": "pkg/a.py", "path": "pkg/a.py", "symbol": "", "kind": "file",
         "parent_id": None, "callees": [], "content_hash": "abc", "signature": "",
         "imports": ["os"], "calls": ["f"], "reference_ids": [], "reference_names": [],
         "fan_in": 0, "fan_out": 1, "size": 10, "mtime_ns": 1, "summary": "old"},
        {"node_id": "pkg/a.py::f", "path": "pkg/a.py", "symbol": "f", "kind": "function",
         "start_line": 1, "end_line": 2, "parent_id": "pkg/a.py", "callees": ["pkg/a.py::g"],
         "content_hash": "", "signature": "def f()", "imports": [], "calls": [], "reference_ids": [],
         "reference_names": [], "fan_in": 1, "fan_out": 0, "size": 0, "mtime_ns": 1, "summary": ""},
        {"node_id": "pkg/a.py::g", "path": "pkg/a.py", "symbol": "g", "kind": "function",
         "start_line": 3, "end_line": 4, "parent_id": "pkg/a.py", "callees": [],
         "content_hash": "", "signature": "def g()", "imports": [], "calls": [], "reference_ids": [],
         "reference_names": [], "fan_in": 0, "fan_out": 0, "size": 0, "mtime_ns": 1, "summary": ""}],
        "edges": []}
    report = migrate(legacy, tmp_path / "graph.json")
    store = GraphStore(tmp_path / "graph.json")
    assert report["migrated_nodes"] == 3 and report["migrated_edges"] == 3  # 2 contains + 1 calls
    assert "imports" in report["unsupported_facts_retained_in_legacy"]
    assert "fan_in" in report["unsupported_facts_retained_in_legacy"]
    kinds = [edge["kind"] for edge in store.edges()]
    assert kinds.count("contains") == 2 and kinds.count("calls") == 1
    file_node = next(n for n in store.nodes() if n["kind"] == "file")
    assert file_node["source"] == {"digest": "abc", "algorithm": "legacy"}
    assert file_node["fact_status"]["legacy_signature"] == "stale"
