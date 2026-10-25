"""Review regressions: persisted CAS and unresolved-consumer delta parity."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from atomic_flow.graph.delta import apply_delta, file_delta
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.store import GraphGenerationConflict, GraphStore
from atomic_flow.locking import exclusive_file


def _seed(tmp_path, files):
    store = GraphStore(tmp_path / "graph.json")
    clean = clean_build(files)
    store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
    return store


def _assert_parity(store, files):
    clean = clean_build(files)
    assert store.nodes() == sorted(clean["nodes"], key=lambda node: node["id"])
    assert store.edges() == sorted(clean["edges"], key=lambda edge: edge["id"])


def test_stale_separate_store_handle_cannot_overwrite_persisted_commit(tmp_path):
    files = {"a.py": b"def helper(): return 1\n"}
    first = _seed(tmp_path, files)
    stale = GraphStore(first.path)
    apply_delta(first, file_delta("b.py", b"def other(): return 2\n", expected_old_digest=None))
    before = first.path.read_bytes()
    with pytest.raises(GraphGenerationConflict, match="persisted store generation"):
        stale.commit(base_generation=1)
    assert first.path.read_bytes() == before
    assert stale.generation == 1
    _assert_parity(GraphStore(first.path), {**files, "b.py": b"def other(): return 2\n"})


def test_cooperating_processes_cannot_both_commit_same_generation(tmp_path):
    store = _seed(tmp_path, {"a.py": b"VALUE = 1\n"})
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from atomic_flow.graph.store import GraphStore, GraphGenerationConflict
store = GraphStore(sys.argv[2])
print('ready', flush=True)
sys.stdin.readline()
try:
    store.commit(base_generation=store.generation)
except GraphGenerationConflict:
    print('conflict', flush=True)
else:
    print('committed', flush=True)
"""
    source = Path(__file__).resolve().parents[1] / "src"
    children = [subprocess.Popen([sys.executable, "-c", code, str(source), str(store.path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        assert [child.stdout.readline().strip() for child in children] == ["ready", "ready"]
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        outputs = [child.communicate(timeout=15) for child in children]
        assert all(child.returncode == 0 for child in children), outputs
        assert sorted(stdout.strip() for stdout, _ in outputs) == ["committed", "conflict"]
        assert GraphStore(store.path).generation == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_held_writer_lease_refuses_without_replacing_store(tmp_path):
    store = _seed(tmp_path, {"a.py": b"VALUE = 1\n"})
    before = store.path.read_bytes()
    with exclusive_file(store.path.with_name(store.path.name + ".lock")):
        with pytest.raises(GraphGenerationConflict, match="lease"):
            store.commit(base_generation=1)
    assert store.path.read_bytes() == before


def test_planned_delta_generation_cannot_follow_unrelated_commit(tmp_path):
    store = _seed(tmp_path, {"a.py": b"VALUE = 1\n"})
    delta = file_delta("b.py", b"VALUE = 2\n", expected_old_digest=None,
                       base_generation=store.generation)
    store.commit(base_generation=store.generation)
    with pytest.raises(GraphContractError, match="generation moved since planning"):
        apply_delta(store, delta)
    assert [node["path"] for node in store.nodes()] == ["a.py"]


def test_delta_captures_generation_before_relink_work(tmp_path, monkeypatch):
    from atomic_flow.graph import delta as module

    store = _seed(tmp_path, {"a.py": b"VALUE = 1\n"})
    original = module._relink

    def raced(*args):
        result = original(*args)
        store.commit(base_generation=store.generation)
        return result

    monkeypatch.setattr(module, "_relink", raced)
    with pytest.raises(GraphContractError, match="delta superseded"):
        apply_delta(store, file_delta("b.py", b"VALUE = 2\n", expected_old_digest=None))
    assert store.generation == 2
    assert [node["path"] for node in store.nodes()] == ["a.py"]


@pytest.mark.parametrize("payload", [b"VALUE = 2\n", None])
def test_absent_digest_is_never_unconditional_overwrite_or_delete(tmp_path, payload):
    store = _seed(tmp_path, {"a.py": b"VALUE = 1\n"})
    before = store.path.read_bytes()
    with pytest.raises(GraphContractError, match="source changed"):
        apply_delta(store, file_delta("a.py", payload, expected_old_digest=None))
    assert store.path.read_bytes() == before


def test_added_module_resolves_old_unresolved_consumer_without_reparse(tmp_path, monkeypatch):
    from atomic_flow.graph import delta as module

    files = {"app.py": b"from lib import helper\ndef run(): return helper()\n",
             "unrelated.py": b"VALUE = 2\n"}
    store = _seed(tmp_path, files)
    parsed = []
    real = module.adapt_file

    def record(path, payload):
        parsed.append(path)
        return real(path, payload)

    monkeypatch.setattr(module, "adapt_file", record)
    added = b"def helper(): return 1\n"
    report = apply_delta(store, file_delta("lib.py", added, expected_old_digest=None))
    _assert_parity(store, {**files, "lib.py": added})
    assert parsed == ["lib.py"]
    assert report["relinked_files"] == 2
    assert any(edge["kind"] == "calls" for edge in store.edges())


def test_added_package_changes_module_precedence_without_stale_import_edges(tmp_path):
    files = {"app.py": b"from lib import helper\ndef run(): return helper()\n",
             "lib.py": b"def helper(): return 1\n"}
    store = _seed(tmp_path, files)
    package = b"def helper(): return 2\n"
    apply_delta(store, file_delta("lib/__init__.py", package, expected_old_digest=None))
    _assert_parity(store, {**files, "lib/__init__.py": package})


def test_removing_package_restores_fallback_module_bindings(tmp_path):
    package = b"def helper(): return 2\n"
    files = {"app.py": b"from lib import helper\ndef run(): return helper()\n",
             "lib.py": b"def helper(): return 1\n", "lib/__init__.py": package}
    store = _seed(tmp_path, files)
    apply_delta(store, file_delta("lib/__init__.py", None,
                                  expected_old_digest=hashlib.sha256(package).hexdigest()))
    _assert_parity(store, {path: payload for path, payload in files.items() if path != "lib/__init__.py"})
