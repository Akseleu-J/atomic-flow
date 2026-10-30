"""P02-2 resolver regressions: inferred edges, closure relink, unresolved honesty."""
import ast
import hashlib

import pytest

from atomic_flow.graph.delta import apply_delta, file_delta
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.python_bindings import extract_bindings
from atomic_flow.graph.store import GraphStore

FILES = {
    "app.py": b"from lib import helper\n\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
    "other.py": b"def caller():\n    return ghost()\n",  # call with no exporter anywhere
}


def digest_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def seed(tmp_path):
    store = GraphStore(tmp_path / "graph.json")
    clean = clean_build(FILES)
    store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
    return store, clean


def edge_kinds(store):
    return sorted((e["kind"], e["src"], e["dst"]) for e in store.edges())


def test_clean_build_infers_imports_and_unique_calls_with_unresolved_report():
    _, clean = seed(None) if False else (None, clean_build(FILES))
    kinds = sorted((e["kind"], e["src"], e["dst"]) for e in clean["edges"])
    assert ("imports", "file:app.py", "file:lib.py") in kinds
    assert ("calls", "file:app.py", "sym:lib.py::helper") in kinds
    assert all(edge["facts_class"] == "inferred" for edge in clean["edges"]
               if edge["kind"] in {"imports", "calls"})
    assert any("no imported exporter" in item for item in clean["unresolved"])


def test_rename_in_lib_relinks_importer_edges_to_parity(tmp_path):
    store, _ = seed(tmp_path)
    renamed = b"def assist():\n    return 1\n"
    report = apply_delta(store, file_delta("lib.py", renamed, expected_old_digest=digest_of(FILES["lib.py"])))
    assert report["relinked_files"] >= 1 and report["parsed_files"] == 1
    live = edge_kinds(store)
    assert ("calls", "file:app.py", "sym:lib.py::helper") not in live
    expected = clean_build({**FILES, "lib.py": renamed})
    assert live == sorted((e["kind"], e["src"], e["dst"]) for e in expected["edges"])


def test_deleted_file_drops_its_inferred_edges(tmp_path):
    store, _ = seed(tmp_path)
    apply_delta(store, file_delta("app.py", None, expected_old_digest=digest_of(FILES["app.py"])))
    live = edge_kinds(store)
    assert not any(src == "file:app.py" for _, src, _ in live)
    expected = clean_build({k: v for k, v in FILES.items() if k != "app.py"})
    assert live == sorted((e["kind"], e["src"], e["dst"]) for e in expected["edges"])


@pytest.mark.parametrize("local", [
    "import other as alias", "import alias.child", "from other import alias",
    "from other import function as alias", "def alias(): pass", "async def alias(): pass",
    "class alias: pass",
])
def test_local_bindings_do_not_resolve_calls_to_module_alias(local):
    consumer = ("import known as alias\n\ndef run():\n    " + local + "\n    return alias.target()\n").encode()
    facts = extract_bindings(ast.parse(consumer))
    assert "alias" in facts["shadowed_call_names"]
    graph = clean_build({"known.py": b"def target(): return 1\n", "consumer.py": consumer})
    assert any(edge["kind"] == "imports" and edge["src"] == "file:consumer.py"
               and edge["dst"] == "file:known.py" for edge in graph["edges"])
    assert not any(edge["kind"] == "calls" and edge["src"] == "file:consumer.py" for edge in graph["edges"])
    assert any("consumer.py: call alias.target" in item and "shadowed" in item for item in graph["unresolved"])


@pytest.mark.parametrize("header", ["def run():", "async def run():", "class Scope:"])
def test_import_binding_inside_scope_keeps_exported_function_call_unknown(header):
    consumer = ("from known import target\n\n" + header + "\n    from other import target\n    target()\n").encode()
    graph = clean_build({"known.py": b"def target(): return 1\n", "consumer.py": consumer})
    assert not any(edge["kind"] == "calls" and edge["src"] == "file:consumer.py" for edge in graph["edges"])
    assert any("consumer.py: call target" in item for item in graph["unresolved"])


def test_local_binding_uncertainty_is_conservative_across_scopes():
    source = b"import known as alias\ndef isolated():\n    import other as alias\ndef global_user():\n    return alias.target()\n"
    graph = clean_build({"known.py": b"def target(): return 1\n", "consumer.py": source})
    # The file-level call inventory cannot distinguish global_user from isolated.
    assert not any(edge["kind"] == "calls" for edge in graph["edges"])
    assert any("call alias.target" in item for item in graph["unresolved"])


def test_top_level_definitions_do_not_shadow_their_own_module_binding():
    facts = extract_bindings(ast.parse("def target(): return 1\ntarget()\n"))
    assert "target" not in facts["shadowed_call_names"]
    graph = clean_build({"consumer.py": b"def target(): return 1\ntarget()\n"})
    assert any(edge["kind"] == "calls" and edge["dst"] == "sym:consumer.py::target" for edge in graph["edges"])


@pytest.mark.parametrize("local", ["import other as alias", "from other import target as alias", "class alias: pass"])
def test_local_rebinding_add_remove_has_exact_clean_delta_parity(tmp_path, local):
    original = b"import known as alias\ndef run():\n    return alias.target()\n"
    changed = ("import known as alias\ndef run():\n    " + local + "\n    return alias.target()\n").encode()
    files = {"known.py": b"def target(): return 1\n", "consumer.py": original}
    store = GraphStore(tmp_path / "graph.json")
    initial = clean_build(files)
    store.commit(base_generation=0, put_nodes=initial["nodes"], put_edges=initial["edges"])
    assert any(edge["kind"] == "calls" for edge in store.edges())
    previous = original
    for raw in (changed, original):
        apply_delta(store, file_delta("consumer.py", raw, expected_old_digest=digest_of(previous)))
        files["consumer.py"] = raw
        expected = clean_build(files)
        assert {node["id"]: node for node in store.nodes()} == {node["id"]: node for node in expected["nodes"]}
        assert {edge["id"]: edge for edge in store.edges()} == {edge["id"]: edge for edge in expected["edges"]}
        assert any(edge["kind"] == "calls" for edge in store.edges()) is (raw == original)
        previous = raw
