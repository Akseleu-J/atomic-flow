"""Oversized source remains durable graph identity with explicit unknown facts."""
import hashlib
from copy import deepcopy

import pytest

from atomic_flow.graph.adapter_ast import adapt_file
from atomic_flow.graph.adapter_resource import adapt_source
from atomic_flow.graph.delta import apply_changes, apply_delta, fallback_rebuild, file_delta
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.projection import (
    ADMITTED_BYTE_LIMIT,
    EXTRACTION_BYTE_LIMIT,
    PLACEHOLDER_EXTRACTOR,
    PROJECTION_POLICY,
    coverage_from_file_nodes,
    project_file_projection,
    valid_file_extractor,
)
from atomic_flow.graph.resolve import exports_by_file
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore
from atomic_flow.graph.sync import sync_project_graph

SMALL = b"def target():\n    return 42\n"
LARGE = b"#" + b" " * EXTRACTION_BYTE_LIMIT


def wire(document):
    return ({node["id"]: node for node in document["nodes"]},
            {edge["id"]: edge for edge in document["edges"]})


def seed(tmp_path, files):
    store = GraphStore(tmp_path / "graph.json")
    graph = clean_build(files)
    store.commit(base_generation=0, put_nodes=graph["nodes"], put_edges=graph["edges"])
    return store


def test_small_projection_is_exactly_unchanged_direct_adapter():
    for path, raw in (("a.py", SMALL), ("README.md", b"# Example\n"), ("config.json", b"{}")):
        assert project_file_projection(path, raw) == adapt_source(path, raw)


@pytest.mark.parametrize("path", ["a.py", "README.md", "config.json", ".github/workflows/check.yml"])
def test_placeholder_retains_complete_identity_and_unknown_extraction(path):
    with pytest.raises(GraphContractError):
        adapt_source(path, LARGE)
    if path.endswith(".py"):
        with pytest.raises(GraphContractError):
            adapt_file(path, LARGE)
    projected = project_file_projection(path, LARGE)
    assert projected["edges"] == [] and len(projected["nodes"]) == 1
    node = projected["nodes"][0]
    assert node["source"] == {"algorithm": "sha256", "digest": hashlib.sha256(LARGE).hexdigest()}
    facts = node["facts"]
    assert facts["size_bytes"] == len(LARGE) and facts["parse_ok"] is None
    assert facts["extractor"] == PLACEHOLDER_EXTRACTOR and facts["projection_policy"] == PROJECTION_POLICY
    assert facts["imports"] == facts["raw_calls"] == []
    assert all(facts[name] == "unknown" for name in ("imports_status", "calls_status", "exports_status"))
    assert valid_file_extractor(node, "unused-small-extractor")
    coverage = coverage_from_file_nodes(projected["nodes"])
    assert not coverage["coverage_complete"] and coverage["coverage_omissions"][0]["path"] == path


@pytest.mark.parametrize("path,raw", [("x.bin", LARGE), ("../x.py", LARGE), ("x.py", "not bytes"),
                                      ("x.py", b"x" * (ADMITTED_BYTE_LIMIT + 1))],
                         ids=["unsupported", "escaping", "nonbytes", "over-admission"])
def test_projection_refuses_unsupported_unadmitted_source(path, raw):
    with pytest.raises(GraphContractError):
        project_file_projection(path, raw)


def test_resolver_preserves_module_existence_and_unknown_reexports():
    graph = clean_build({"large.py": LARGE, "bridge.py": b"from large import target\n",
                         "consumer.py": b"import large\nfrom bridge import target\nlarge.target()\ntarget()\n"})
    exports = exports_by_file(graph["nodes"])
    assert {"large.py", "bridge.py", "consumer.py"} <= exports.unknown
    assert not any(edge["kind"] == "calls" for edge in graph["edges"])
    assert any(edge["kind"] == "imports" and edge["dst"] == "file:large.py" for edge in graph["edges"])
    assert any("large.py: coverage omitted_oversized" in item for item in graph["unresolved"])
    assert any("bridge.py: import large: export coverage unknown" in item for item in graph["unresolved"])
    assert not graph["coverage_complete"]


def test_unknown_star_cannot_resolve_a_potentially_shadowed_local_function():
    graph = clean_build({"large.py": LARGE,
                         "consumer.py": b"def target(): return 1\nfrom large import *\ntarget()\n"})
    assert any(node.get("symbol") == "target" for node in graph["nodes"])
    assert not any(edge["kind"] == "calls" for edge in graph["edges"])
    assert any("consumer.py: import large: export coverage unknown" in row for row in graph["unresolved"])


def test_unknown_module_alias_cannot_fall_back_to_an_unrelated_same_named_module():
    graph = clean_build({"large.py": LARGE, "alias.py": SMALL,
                         "consumer.py": b"import large as alias\nalias.target()\n"})
    assert not any(edge["kind"] == "calls" for edge in graph["edges"])
    assert any(edge["kind"] == "imports" and edge["dst"] == "file:large.py" for edge in graph["edges"])


def test_unknown_star_cannot_resolve_a_potentially_shadowed_module_alias():
    graph = clean_build({"large.py": LARGE, "known.py": SMALL,
                         "consumer.py": b"import known\nfrom large import *\nknown.target()\n"})
    assert not any(edge["kind"] == "calls" for edge in graph["edges"])
    assert any(edge["kind"] == "imports" and edge["dst"] == "file:known.py" for edge in graph["edges"])


@pytest.mark.parametrize("consumer", [b"from large import *\ntarget()\n",
                                      b"import large as alias\nalias.target()\n",
                                      b"import known\nfrom large import *\nknown.target()\n"])
def test_known_to_opaque_star_and_alias_transitions_remove_call_edges_with_clean_parity(tmp_path, consumer):
    files = {"large.py": SMALL, "alias.py": SMALL, "known.py": SMALL, "consumer.py": consumer}
    store = seed(tmp_path, files)
    assert any(edge["kind"] == "calls" for edge in store.edges())
    for raw in (LARGE, SMALL):
        files["large.py"] = raw
        apply_changes(store, {"large.py": raw})
        assert wire(store.document()) == wire(clean_build(files))
        assert any(edge["kind"] == "calls" for edge in store.edges()) is (raw == SMALL)


def test_small_large_small_replaces_symbols_and_inferred_edges_with_clean_parity(tmp_path):
    files = {"a.py": SMALL, "caller.py": b"from a import target\ntarget()\n"}
    store = seed(tmp_path, files)
    for raw in (LARGE, SMALL):
        files["a.py"] = raw
        report = apply_changes(store, {"a.py": raw})
        assert wire(store.document()) == wire(clean_build(files))
        assert report["coverage_complete"] is (raw == SMALL)
        if raw == LARGE:
            assert not any(node["kind"] != "file" and node["path"] == "a.py" for node in store.nodes())
            assert not any(edge["kind"] == "calls" for edge in store.edges())


def test_large_add_edit_rename_delete_clean_parity_and_reload_coverage(tmp_path):
    files = {"caller.py": b"import big\n"}
    store = seed(tmp_path, files)
    operations = ({"big.py": LARGE}, {"big.py": LARGE + b" "},
                  {"big.py": None, "renamed.py": LARGE}, {"renamed.py": None})
    for changed in operations:
        for path, raw in changed.items():
            if raw is None:
                files.pop(path, None)
            else:
                files[path] = raw
        report = apply_changes(store, changed)
        assert wire(store.document()) == wire(clean_build(files))
        reloaded = GraphStore(tmp_path / "graph.json")
        assert coverage_from_file_nodes(reloaded.nodes()) == coverage_from_file_nodes(clean_build(files)["nodes"])
        assert report["coverage_complete"] == (not any(len(raw) > EXTRACTION_BYTE_LIMIT for raw in files.values()))


def test_placeholder_versions_and_cas_are_explicit(tmp_path):
    store = seed(tmp_path, {"a.py": LARGE})
    node = store.nodes()[0]
    for key, value in (("projection_policy", "unknown/2"), ("extractor", "graph-placeholder/2"),
                       ("imports_status", "known"), ("size_bytes", True)):
        invalid = deepcopy(node)
        invalid["facts"][key] = value
        assert not valid_file_extractor(invalid, "python-ast/2")
    previous = store.document()
    with pytest.raises(GraphContractError):
        apply_delta(store, file_delta("a.py", SMALL, expected_old_digest="f" * 64))
    with pytest.raises(GraphContractError):
        apply_delta(store, file_delta("a.py", SMALL, expected_old_digest=node["source"]["digest"], base_generation=0))
    assert store.document() == previous


def test_cancel_and_source_drift_keep_visible_snapshot(tmp_path):
    store = seed(tmp_path, {"a.py": SMALL})
    previous = store.document()
    def refuse():
        raise GraphContractError("source drift/cancel")
    with pytest.raises(GraphContractError):
        apply_changes(store, {"a.py": LARGE}, source_check=refuse)
    assert store.document() == previous
    with pytest.raises(GraphContractError):
        apply_changes(store, {"a.py": LARGE}, cancel_check=refuse)
    assert store.document() == previous


def test_fallback_and_sync_reconstruct_durable_omissions(tmp_path):
    files = {"big.py": LARGE, "small.py": SMALL}
    store = seed(tmp_path, {"small.py": SMALL})
    rebuilt = fallback_rebuild(store, files, "explicit test fallback")
    assert not rebuilt["coverage_complete"] and rebuilt["coverage_omissions"][0]["path"] == "big.py"
    project = tmp_path / "project"
    project.mkdir()
    for path, raw in files.items():
        (project / path).write_bytes(raw)
    initial = sync_project_graph(project, files)
    assert initial["created"] and not initial.get("failed") and not initial["coverage_complete"]
    again = sync_project_graph(project, {})
    assert not again.get("fallback") and not again.get("failed")
    assert again["coverage_omissions"] == initial["coverage_omissions"]
    (project / "big.py").write_bytes(SMALL)
    final = sync_project_graph(project, {"big.py": SMALL})
    assert final["coverage_complete"] and not final.get("failed")


def test_failed_multi_file_commit_keeps_previous_generation(tmp_path, monkeypatch):
    store = seed(tmp_path, {"a.py": SMALL})
    previous = store.document()
    def crash(**kwargs):
        raise OSError("injected publication crash")
    monkeypatch.setattr(store, "commit", crash)
    with pytest.raises(OSError):
        apply_changes(store, {"a.py": LARGE, "b.py": LARGE})
    assert GraphStore(tmp_path / "graph.json").document() == previous



def test_host_graph_admits_oversized_without_extending_model_reads(tmp_path):
    from types import SimpleNamespace

    from atomic_flow.agents.source import SourceConflict
    from atomic_flow.agents.tools.file_paths import admitted_path
    from atomic_flow.graph.project import project_files, project_paths
    from atomic_flow.project_resources import ProjectLimits, ResourceLimitError

    (tmp_path / "big.py").write_bytes(LARGE)
    ctx = SimpleNamespace(project_root=tmp_path, tree_ignore=None, respect_gitignore=True, private_workspace=False)
    with pytest.raises(SourceConflict, match="size limit"):
        admitted_path(ctx, "big.py", existing=True)
    assert project_files(tmp_path) == {"big.py": LARGE}
    assert project_paths(tmp_path, ["big.py"]) == {"big.py": LARGE}
    with pytest.raises((SourceConflict, ResourceLimitError)):
        project_files(tmp_path, limits=ProjectLimits(max_file_bytes=EXTRACTION_BYTE_LIMIT))
    with pytest.raises(SourceConflict):
        admitted_path(ctx, "big.py", existing=True, source_byte_limit=True)
    with pytest.raises(SourceConflict):
        admitted_path(ctx, "big.py", existing=True, source_byte_limit=ADMITTED_BYTE_LIMIT + 1)


def test_host_graph_large_file_admission_keeps_hidden_ignore_and_link_guards(tmp_path):
    from types import SimpleNamespace

    from atomic_flow.agents.source import SourceConflict
    from atomic_flow.agents.tools.file_paths import admitted_path
    from atomic_flow.graph.project import project_files, project_paths

    (tmp_path / "visible.py").write_bytes(SMALL)
    (tmp_path / ".hidden.py").write_bytes(LARGE)
    (tmp_path / "ignored.py").write_bytes(LARGE)
    (tmp_path / ".gitignore").write_bytes(b"ignored.py\n")
    ctx = SimpleNamespace(project_root=tmp_path, tree_ignore=None, respect_gitignore=True, private_workspace=False)
    files = project_files(tmp_path)
    assert files == {"visible.py": SMALL}
    for path in (".hidden.py", "ignored.py"):
        with pytest.raises(SourceConflict):
            project_paths(tmp_path, [path])
        with pytest.raises(SourceConflict):
            admitted_path(ctx, path, existing=True, source_byte_limit=ADMITTED_BYTE_LIMIT)
    link = tmp_path / "alias.py"
    try:
        link.symlink_to(tmp_path / "visible.py")
    except OSError:
        return  # Link creation needs a Windows privilege; Linux CI exercises it.
    with pytest.raises(SourceConflict):
        project_paths(tmp_path, ["alias.py"])
    with pytest.raises(SourceConflict):
        admitted_path(ctx, "alias.py", existing=True, source_byte_limit=ADMITTED_BYTE_LIMIT)
