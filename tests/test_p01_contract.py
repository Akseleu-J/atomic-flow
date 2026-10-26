"""Full P01 wire/root/read/migration compatibility contract regressions."""
import json
from copy import deepcopy

import pytest

from atomic_flow.graph import GraphContractError, GraphGenerationConflict, GraphStore, adapt_file, migrate
from atomic_flow.graph.adapter_resource import adapt_source
from atomic_flow.graph.compat import GraphReader
from atomic_flow.graph.identity import GraphIdentity, GraphReadContext
from atomic_flow.graph.schema import validate_node


def populated(tmp_path, identity=None):
    store = GraphStore(tmp_path / "graph.json", identity=identity)
    payload = adapt_file("pkg/a.py", b"class A:\n    def f(self):\n        return 1\n")
    store.commit(base_generation=0, put_nodes=payload["nodes"], put_edges=payload["edges"])
    return store


@pytest.mark.parametrize("path", ["../a.py", "/a.py", "C:/a.py", "a\\b.py", "a//b.py", "./a.py", "a/../b.py"])
def test_unsafe_graph_paths_are_refused_before_adapter_or_storage(path):
    with pytest.raises(GraphContractError):
        adapt_file(path, b"X = 1\n")


@pytest.mark.parametrize("mutation", ["bad_hash", "nonfinite", "bad_span", "missing_status", "unknown_field"])
def test_graph_source_provenance_and_freshness_are_strict(mutation):
    node = adapt_file("a.py", b"def f():\n    return 1\n")["nodes"][1]
    if mutation == "bad_hash":
        node["source"]["digest"] = "short"
    elif mutation == "nonfinite":
        node["facts"]["score"] = float("nan")
        node["fact_status"]["score"] = "fresh"
    elif mutation == "bad_span":
        node["start_line"] = True
    elif mutation == "missing_status":
        node["fact_status"].pop("signature")
    else:
        node["authority"] = "admin"
    with pytest.raises(GraphContractError):
        validate_node(node)


def test_store_snapshots_do_not_grant_mutable_handles_and_detect_same_generation_tamper(tmp_path):
    store = populated(tmp_path)
    original = store.export_canonical()
    store.nodes()[0]["facts"]["parse_ok"] = "forged"
    assert store.export_canonical() == original
    tampered = json.loads(original)
    tampered["nodes"][0]["facts"]["parse_ok"] = False
    store.path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(GraphGenerationConflict):
        store.commit(base_generation=1)


def test_duplicate_transaction_ids_do_not_silently_replace_records(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    node = adapt_file("a.py", b"X = 1\n")["nodes"][0]
    with pytest.raises(GraphContractError, match="duplicate"):
        store.commit(base_generation=0, put_nodes=[node, deepcopy(node)])
    assert store.generation == 0 and not store.path.exists()


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_key", "unknown_field", "bad_generation"])
def test_store_strict_document_decode_never_silently_collapses_records(tmp_path, mutation):
    store = populated(tmp_path)
    document = store.document()
    if mutation == "duplicate_id":
        document["nodes"].append(deepcopy(document["nodes"][0]))
    elif mutation == "unknown_field":
        document["authority"] = "admin"
    elif mutation == "bad_generation":
        document["generation"] = True
    raw = json.dumps(document)
    if mutation == "duplicate_key":
        raw = raw.replace('"generation": 1', '"generation": 1, "generation": 2')
    store.path.write_text(raw, encoding="utf-8")
    with pytest.raises(GraphContractError):
        GraphStore(store.path)


def test_root_principal_policy_namespaces_reject_cross_project_reads(tmp_path):
    identity = GraphIdentity("alice", "workspace", "project-a", "root-a", "policy-1")
    store = populated(tmp_path, identity)
    context = GraphReadContext(identity, frozenset({("workspace", "project-a", "root-a")}))
    reader = GraphReader(store, identity=identity, context=context)
    assert reader.review()["namespace"] == identity.namespace()
    assert reader.get("file:pkg/a.py") and reader.search("A f")
    assert reader.edges(node_id="file:pkg/a.py", kind="contains")
    for changed in (GraphIdentity("bob", "workspace", "project-a", "root-a", "policy-1"),
                    GraphIdentity("alice", "workspace", "project-b", "root-a", "policy-1"),
                    GraphIdentity("alice", "workspace", "project-a", "root-b", "policy-1"),
                    GraphIdentity("alice", "workspace", "project-a", "root-a", "policy-2")):
        with pytest.raises(GraphContractError):
            GraphReader(store, identity=changed, context=context)
    with pytest.raises(GraphContractError):
        GraphReader(store, identity=identity, context=GraphReadContext(identity, frozenset()))
    foreign = GraphIdentity("alice", "workspace", "project-b", "root-a", "policy-1")
    with pytest.raises(GraphContractError):
        GraphStore(store.path, identity=foreign)
    with pytest.raises(GraphContractError):
        GraphReader(store, identity=foreign, context=GraphReadContext(foreign, frozenset({("workspace", "project-b", "root-a")})))
    reopened = GraphStore(store.path)
    assert reopened.identity == identity


def test_legacy_roundtrip_preserves_supported_records_duplicate_symbols_and_omissions(tmp_path):
    legacy = {"version": 4, "nodes": [
        {"node_id": "a.py", "kind": "file", "path": "a.py", "symbol": "", "content_hash": "abc", "summary": "old"},
        {"node_id": "a.py::f", "kind": "function", "path": "a.py", "symbol": "f", "parent_id": "a.py",
         "start_line": 1, "end_line": 2, "callees": ["missing"], "signature": "def f()"},
        {"node_id": "a.py::f#2", "kind": "function", "path": "a.py", "symbol": "f#2", "parent_id": "missing",
         "start_line": 3, "end_line": 4, "callees": []},
        {"node_id": "unknown", "kind": "alien", "path": "a.py", "symbol": "alien"}]}
    before = deepcopy(legacy)
    report = migrate(legacy, tmp_path / "graph.json")
    reader = GraphReader(GraphStore(tmp_path / "graph.json"))
    exported = reader.export_legacy()
    assert legacy == before
    assert exported["nodes"] == legacy["nodes"][:3]
    assert reader.get("sym:a.py::f#2")
    assert {o["reason"] for o in report["omissions"]} == {"unsupported_legacy_kind", "dangling_parent", "unresolved_callee"}
    assert report["legacy_records_retained"] and reader.review()["status"] == "partial"
    with pytest.raises(ValueError, match="empty"):
        migrate(legacy, tmp_path / "graph.json")


def test_resource_kinds_parse_failures_and_legacy_omissions_are_explicit(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    for path, source in [("config.json", b"invalid json"), ("README.md", b"# Notes"),
                         (".github/workflows/test.yml", b"name: test"), ("bad.py", b"def bad(:")]:
        payload = adapt_source(path, source)
        store.commit(base_generation=store.generation, put_nodes=payload["nodes"], put_edges=payload["edges"])
    reader = GraphReader(store)
    assert {n["kind"] for n in reader.nodes()} >= {"config", "document", "pipeline", "file"}
    assert {o["path"] for o in reader.review()["omissions"]} == {"config.json", "bad.py"}
    assert len(reader.export_legacy()["compatibility"]["omissions"]) == 3


def test_graph_read_port_feeds_existing_tree_search_without_read_authority(tmp_path):
    from atomic_flow.activation.activate import ActivationIndex
    from atomic_flow.agents.context import ToolContext
    from atomic_flow.agents.tools.search_tree import SearchTreeArgs, SearchTreeTool

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("class A:\n    def f(self):\n        return 1\n", encoding="utf-8")
    reader = GraphReader(populated(tmp_path))
    nodes = reader.tree_nodes()
    context = ToolContext(tmp_path, {node.node_id: node for node in nodes}, index=ActivationIndex(nodes),
                          enforce_read_before_edit=True)
    result = SearchTreeTool().run(SearchTreeArgs(query="pkg"), context)
    assert result["results"] and any(row["symbol"] == "A.f" for row in result["results"])
    assert not context.reads
    with pytest.raises(OSError, match="complete current source"):
        context.require_read("pkg/a.py", context.file_hashes["pkg/a.py"])


def test_graph_review_reports_unresolved_dependencies_instead_of_complete_claim(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    payload = adapt_file("a.py", b"import missing_package\ndef feature():\n    return missing_package.run()\n")
    store.commit(base_generation=0, put_nodes=payload["nodes"], put_edges=payload["edges"])
    report = GraphReader(store).review()
    assert report["status"] == "partial" and report["unresolved"]
    assert report["semantic_requirements"] == "not_assessed"
