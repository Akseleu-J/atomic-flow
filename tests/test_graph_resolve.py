"""P02-2 resolver regressions: inferred edges, closure relink, unresolved honesty."""
import hashlib

from atomic_flow.graph.delta import apply_delta, file_delta
from atomic_flow.graph.project import clean_build
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
