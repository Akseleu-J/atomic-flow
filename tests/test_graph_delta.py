"""P02 delta regressions: delta/clean parity, content CAS, instrumentation."""
import pytest

from atomic_flow.graph.delta import apply_delta, fallback_rebuild, file_delta
from atomic_flow.graph.project import affected_by_name, clean_build
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphStore

FILES = {
    "app.py": b"from lib import helper\n\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
    "extra.py": b"VALUE = 2\n",
}


def seed(tmp_path, files=FILES):
    store = GraphStore(tmp_path / "graph.json")
    clean = clean_build(files)
    store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
    return store, clean


def parity(store, files):
    clean = clean_build(files)
    live = store.document()
    def key(nodes):
        return sorted((n["id"], sorted(n.get("facts", {}).items(), key=str)) for n in nodes)
    assert key(live["nodes"]) == key(clean["nodes"])
    assert sorted(e["id"] for e in live["edges"]) == sorted(e["id"] for e in clean["edges"])


def test_delta_sequence_matches_clean_build(tmp_path):
    store, _ = seed(tmp_path)
    lib_new = b"def helper():\n    return 2\n\n\ndef helper():  # same-name overload\n    return 3\n"
    delta = file_delta("lib.py", lib_new, expected_old_digest=_digest_of(FILES["lib.py"]))
    report = apply_delta(store, delta)
    assert report["parsed_files"] == 1 and report["scanned_files"] == 1
    files = dict(FILES, **{"lib.py": lib_new})
    parity(store, files)

    added = b"from lib import helper\n\n\ndef late():\n    return helper()\n"
    apply_delta(store, file_delta("newmod.py", added, expected_old_digest=""))
    parity(store, {**files, "newmod.py": added})

    apply_delta(store, file_delta("extra.py", None, expected_old_digest=_digest_of(FILES["extra.py"])))
    current = {k: v for k, v in {**files, "newmod.py": added}.items() if k != "extra.py"}
    parity(store, current)

    body_shift = b"from lib import helper\n\n\ndef run():\n    x = 1\n    return helper()\n"
    apply_delta(store, file_delta("app.py", body_shift, expected_old_digest=_digest_of(FILES["app.py"])))
    parity(store, {**current, "app.py": body_shift})


def _digest_of(payload: bytes) -> str:
    import hashlib
    return hashlib.sha256(payload).hexdigest()


def test_delta_refuses_moved_source_and_records_instrumentation(tmp_path):
    store, _ = seed(tmp_path)
    stale = file_delta("lib.py", b"def helper():\n    return 9\n",
                       expected_old_digest=_digest_of(b"def helper():\n    return 1\n"))
    # someone already changed lib.py underneath the delta builder
    apply_delta(store, file_delta("lib.py", b"def helper():\n    return 7\n",
                                  expected_old_digest=_digest_of(FILES["lib.py"])))
    with pytest.raises(GraphContractError, match="source changed"):
        apply_delta(store, stale)
    file_nodes = [n for n in store.nodes() if n["path"] == "lib.py" and n["kind"] == "file"]
    assert len(file_nodes) == 1 and file_nodes[0]["source"]["digest"] == _digest_of(b"def helper():\n    return 7\n")
    assert [n["id"] for n in store.nodes() if n["path"] == "lib.py" and n["kind"] != "file"] == ["sym:lib.py::helper"]


def test_body_edit_parses_only_the_changed_file(tmp_path, monkeypatch):
    store, _ = seed(tmp_path)
    import atomic_flow.graph.delta as delta_module
    calls = []
    real = delta_module.adapt_file

    def counting(path, payload):
        calls.append(path)
        return real(path, payload)

    monkeypatch.setattr(delta_module, "adapt_file", counting)
    apply_delta(store, file_delta("app.py", b"from lib import helper\n\n\ndef run():\n    return helper() + 1\n",
                                  expected_old_digest=_digest_of(FILES["app.py"])))
    assert calls == ["app.py"], "a private-body edit must not parse unrelated files"


def test_fallback_rebuild_requires_reason_and_restores_parity(tmp_path):
    store, _ = seed(tmp_path)
    with pytest.raises(GraphContractError):
        fallback_rebuild(store, FILES, "")
    report = fallback_rebuild(store, FILES, "branch switch")
    assert report["reason"] == "branch switch" and report["parsed_files"] == 3
    parity(store, FILES)


def test_reverse_index_reports_name_level_blast_radius():
    clean = clean_build(FILES)
    assert affected_by_name(clean["reverse"], ["helper"]) == ["app.py"]
    assert affected_by_name(clean["reverse"], ["lib.helper"]) == ["app.py"]
    assert clean["exports"]["helper"] == ["sym:lib.py::helper"]
