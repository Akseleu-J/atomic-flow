"""Delta acceptance for resource owners and export visibility changes."""

import hashlib

import pytest

from atomic_flow.graph.delta import apply_changes, apply_delta, file_delta
from atomic_flow.graph.ingest import ingest_source_changes
from atomic_flow.graph.project import clean_build
from atomic_flow.graph.store import GraphStore
from atomic_flow.graph.sync import store_path, sync_project_graph


def _structure(value):
    return (
        sorted(value["nodes"], key=lambda item: item["id"]),
        sorted(value["edges"], key=lambda item: item["id"]),
    )


def _seed(path, files):
    store = GraphStore(path)
    candidate = clean_build(files)
    store.commit(
        base_generation=0,
        put_nodes=candidate["nodes"],
        put_edges=candidate["edges"],
    )
    return store


def _assert_parity(store, files):
    assert _structure({"nodes": store.nodes(), "edges": store.edges()}) == _structure(
        clean_build(files)
    )


def _change(store, files, path, content):
    before = files.get(path)
    report = apply_delta(
        store,
        file_delta(
            path,
            content,
            expected_old_digest=hashlib.sha256(before).hexdigest() if before else None,
        ),
    )
    if content is None:
        files.pop(path)
    else:
        files[path] = content
    _assert_parity(store, files)
    return report


def test_mixed_resource_owned_batch_and_external_reconciliation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    files = {
        "a.py": b"def run():\n    return 1\n",
        "settings.toml": b"[project]\nname = 'before'\n",
        "README.md": b"# Before\n\nDescription.\n",
        ".github/workflows/check.yml": b"name: check\non: push\njobs: {}\n",
    }
    for path, content in files.items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    store = _seed(tmp_path / "graph.json", files)
    changes = {
        "settings.toml": b"[project]\nname = 'after'\n",
        "README.md": b"# After\n\nChanged description.\n",
    }
    apply_changes(store, changes)
    files.update(changes)
    for path, content in changes.items():
        (project / path).write_bytes(content)
    _assert_parity(store, files)

    # Events are only hints: no supplied event still discovers added resources,
    # deletion, and changed pipeline bytes in a non-Git project.
    (project / "README.md").unlink()
    files.pop("README.md")
    files["GUIDE.md"] = b"# Guide\n\nNew instructions.\n"
    files[".github/workflows/check.yml"] = b"name: revised\non: push\njobs: {}\n"
    for path in ("GUIDE.md", ".github/workflows/check.yml"):
        (project / path).write_bytes(files[path])
    report = ingest_source_changes(store, project, reason="mixed resource acceptance")
    assert report["deltas"] == 3
    _assert_parity(store, files)


def test_export_visibility_invalidates_transitive_consumers(tmp_path):
    files = {
        "provider.py": b"__all__ = ['foo']\ndef foo(): return 1\ndef bar(): return 2\n",
        "bridge.py": b"from provider import *\n",
        "consumer.py": b"from bridge import *\ndef use(): return foo()\n",
        "unrelated.py": b"def idle(): return 0\n",
    }
    store = _seed(tmp_path / "graph.json", files)
    for content in (
        b"__all__ = ['bar']\ndef foo(): return 1\ndef bar(): return 2\n",
        b"__all__ = dynamic_exports()\ndef foo(): return 1\ndef bar(): return 2\n",
        b"__all__ = ['foo']\ndef foo(): return 1\ndef bar(): return 2\n",
    ):
        report = _change(store, files, "provider.py", content)
        assert {"provider.py", "bridge.py", "consumer.py"} <= set(
            report["invalidated_paths"]
        )
        assert "unrelated.py" not in report["invalidated_paths"]
        assert report["parsed_files"] == 1


def test_batch_reports_resolution_closure_after_provider_move(tmp_path):
    files = {
        "provider.py": b"def foo(): return 1\n",
        "bridge.py": b"from provider import foo\n",
        "consumer.py": b"from bridge import foo\ndef use(): return foo()\n",
    }
    store = _seed(tmp_path / "graph.json", files)
    content = files.pop("provider.py")
    files["moved.py"] = content
    report = apply_changes(store, {"provider.py": None, "moved.py": content})
    _assert_parity(store, files)
    assert {"provider.py", "moved.py", "bridge.py", "consumer.py"} <= set(
        report["invalidated_paths"]
    )


def test_mixed_resources_live_sync_initial_and_owned_edit(tmp_path):
    files = {
        "a.py": b"def run(): return 1\n",
        "README.md": b"# Initial\n",
        "settings.toml": b"name = 'initial'\n",
    }
    for path, content in files.items():
        (tmp_path / path).write_bytes(content)
    sync_project_graph(tmp_path, {})
    _assert_parity(GraphStore(store_path(tmp_path)), files)
    files["README.md"] = b"# Revised\n"
    (tmp_path / "README.md").write_bytes(files["README.md"])
    sync_project_graph(tmp_path, {"README.md": files["README.md"]})
    _assert_parity(GraphStore(store_path(tmp_path)), files)


def test_public_sync_cancellation_after_private_delta_keeps_persisted_snapshot(
    tmp_path, monkeypatch
):
    import atomic_flow.graph.delta as delta_module

    files = {
        "a.py": b"def a(): return 1\n",
        "b.py": b"from a import a\ndef b(): return a()\n",
    }
    for path, content in files.items():
        (tmp_path / path).write_bytes(content)
    sync_project_graph(tmp_path, {})
    persisted = store_path(tmp_path).read_bytes()
    generation = GraphStore(store_path(tmp_path)).generation
    changes = {
        "a.py": b"def renamed(): return 2\n",
        "b.py": b"from a import renamed\ndef b(): return renamed()\n",
    }
    for path, content in changes.items():
        (tmp_path / path).write_bytes(content)
    real_delta = delta_module.apply_delta
    private_steps = []

    def cancel_after_private_delta(*args, **kwargs):
        result = real_delta(*args, **kwargs)
        private_steps.append(result)
        raise RuntimeError("acceptance cancellation after private staged delta")

    with monkeypatch.context() as scoped:
        scoped.setattr(delta_module, "apply_delta", cancel_after_private_delta)
        with pytest.raises(RuntimeError, match="acceptance cancellation"):
            sync_project_graph(tmp_path, changes)
    assert len(private_steps) == 1
    assert store_path(tmp_path).read_bytes() == persisted
    assert GraphStore(store_path(tmp_path)).generation == generation
    _assert_parity(GraphStore(store_path(tmp_path)), files)
    files.update(changes)
    sync_project_graph(tmp_path, changes)
    _assert_parity(GraphStore(store_path(tmp_path)), files)


def test_private_body_edit_retains_incoming_calls_without_consumer_relink(tmp_path):
    files = {
        "provider.py": b"def foo(): return 1\n",
        "consumer.py": b"from provider import foo\ndef use(): return foo()\n",
    }
    store = _seed(tmp_path / "graph.json", files)
    report = _change(store, files, "provider.py", b"def foo(): return 2\n")
    assert report["parsed_files"] == 1
    assert set(report["invalidated_paths"]) == {"provider.py"}
    # Clean parity verifies that incoming consumer->foo edges were retained,
    # rather than deleted together with a same-ID replacement of foo.


@pytest.mark.parametrize(
    "consumer",
    [
        b"from provider import foo\nfoo = lambda: 2\ndef use(): return foo()\n",
        b"from provider import foo\ndef foo(): return 2\ndef use(): return foo()\n",
        b"import provider\nprovider = object()\ndef use(): return provider.foo()\n",
    ],
)
def test_shadowed_imports_never_invent_provider_call_edges(consumer):
    built = clean_build(
        {"provider.py": b"def foo(): return 1\n", "consumer.py": consumer}
    )
    provider_ids = {
        node["id"] for node in built["nodes"] if node["path"] == "provider.py"
    }
    assert not any(
        edge["kind"] == "calls"
        and any(isinstance(value, str) and value in provider_ids for value in edge.values())
        for edge in built["edges"]
    )
