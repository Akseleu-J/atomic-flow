"""P02 parity, no-global-work and trust/recovery boundaries."""
import hashlib

import pytest

from atomic_flow.graph.delta import apply_changes, apply_delta, file_delta
from atomic_flow.graph.ingest import ingest_source_changes
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.store import GraphStore
from atomic_flow.tree.builder import build_tree, refresh_tree, save_tree
from atomic_flow.watching import SourceWatcher


def structure(value):
    return (sorted(value["nodes"], key=lambda n: n["id"]), sorted(value["edges"], key=lambda e: e["id"]))


def seeded(tmp_path, files):
    store = GraphStore(tmp_path / "graph.json")
    clean = clean_build(files)
    store.commit(base_generation=0, put_nodes=clean["nodes"], put_edges=clean["edges"])
    return store


def test_fixedpoint_reexports_alias_relative_star_and_new_target(tmp_path):
    files = {"pkg/__init__.py": b"from .bridge import public\n",
             "pkg/bridge.py": b"from .lib import helper as public\n",
             "pkg/lib.py": b"def helper(): return 1\n",
             "app.py": b"from pkg import public as action\ndef run(): return action()\n",
             "star.py": b"from pkg import *\ndef run(): return public()\n",
             "waiting.py": b"from missing import target\ndef run(): return target()\n"}
    store = seeded(tmp_path, files)
    assert any(e["kind"] == "calls" and e["src"] == "file:app.py" and e["dst"] == "sym:pkg/lib.py::helper"
               for e in store.edges())
    for path, new in [("pkg/lib.py", b"def changed(): return 2\n"),
                      ("pkg/bridge.py", b"from .lib import changed as public\n"),
                      ("missing.py", b"def target(): return 1\n"), ("pkg/lib.py", None)]:
        old = files.get(path)
        report = apply_delta(store, file_delta(path, new, expected_old_digest=hashlib.sha256(old).hexdigest() if old else None))
        if new is None:
            files.pop(path)
        else:
            files[path] = new
        assert structure(store.document()) == structure(clean_build(files))
        assert report["parsed_files"] == (0 if new is None else 1)


def test_wildcard_ambiguity_dynamic_exports_nested_names_are_not_guessed():
    files = {"a.py": b"def target(): pass\n", "b.py": b"def target(): pass\n",
             "app.py": b"from a import *\nfrom b import *\ntarget()\n",
             "nested.py": b"def outer():\n    def hidden(): pass\n    return hidden\n",
             "bad.py": b"from nested import hidden\nhidden()\n",
             "dynamic.py": b"from a import *\n__all__ = make_exports()\n",
             "dynamic_user.py": b"from dynamic import *\ntarget()\n"}
    clean = clean_build(files)
    assert not any(e["kind"] == "calls" and e["src"] in {"file:app.py", "file:bad.py", "file:dynamic_user.py"}
                   for e in clean["edges"])
    assert any("ambiguous" in u for u in clean["unresolved"])
    assert any("dynamic __all__" in u for u in clean["unresolved"])


def test_multifile_overlay_cancel_never_exposes_partial_snapshot(tmp_path):
    files = {"a.py": b"def a(): return 1\n", "b.py": b"def b(): return 1\n"}
    store = seeded(tmp_path, files)
    before = store.export_canonical()
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled between private deltas")
    changed = {p: data.replace(b"1", b"2") for p, data in files.items()}
    with pytest.raises(RuntimeError, match="cancelled"):
        apply_changes(store, changed, cancel_check=cancel)
    assert store.export_canonical() == before
    assert GraphStore(store.path).export_canonical() == before
    generation = store.generation
    apply_changes(store, changed)
    assert store.generation == generation + 1
    assert structure(store.document()) == structure(clean_build(changed))


def test_owned_private_body_edit_reads_parses_relinks_only_changed_file(tmp_path, monkeypatch):
    from atomic_flow.tree import builder
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    tree = build_tree(tmp_path)
    save_tree(tmp_path, tree)
    (tmp_path / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    parsed = []
    real = builder._index_file
    monkeypatch.setattr(builder, "_index_file", lambda path, *a, **kw: parsed.append(path) or real(path, *a, **kw))
    updated, changed = refresh_tree(tmp_path, changed_paths=["a.py"])
    assert changed and parsed == ["a.py"]
    assert updated["stats"]["read_files"] == updated["stats"]["relinked"] == 1
    assert not updated["stats"]["fallback_reason"]
    clean = build_tree(tmp_path)
    def normalize(t):
        return sorted(({k: v for k, v in n.items() if k != "mtime_ns"} for n in t["nodes"]), key=lambda n: n["node_id"])
    assert normalize(updated) == normalize(clean)


def test_non_git_watch_and_dropped_events_reconcile_to_parity(tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    (project / "a.py").write_bytes(b"def a(): return 1\n")
    store = seeded(tmp_path, {"a.py": (project / "a.py").read_bytes()})
    watcher = SourceWatcher(project, debounce=0)
    (project / "a.py").write_bytes(b"def a(): return 2\n")
    event = watcher.poll(force=True)
    assert event and event.ready
    report = watcher.reconcile_graph(store, event)
    assert report["deltas"] == 1
    assert watcher.poll(force=True) is None
    (project / "b.py").write_bytes(b"from a import a\na()\n")
    ingest_source_changes(store, project, reason="dropped event audit")
    assert structure(store.document()) == structure(clean_build({p.name: p.read_bytes() for p in project.glob("*.py")}))


def test_activation_updates_reuse_unchanged_tokens(tmp_path):
    from atomic_flow.activation.activate import ActivationIndex
    from atomic_flow.tree.node import TreeNode
    (tmp_path / "a.py").write_text("def alpha(): return 1\n")
    tree = build_tree(tmp_path)
    nodes = [TreeNode.model_validate(n) for n in tree["nodes"]]
    initial = ActivationIndex(nodes)
    fresh = initial.updated(nodes)
    assert fresh.stats["tokenized_nodes"] == 0 and fresh.stats["reused_nodes"] == len(nodes)
    assert [n.node_id for n, _ in fresh.search("alpha").ranked] == [n.node_id for n, _ in initial.search("alpha").ranked]
