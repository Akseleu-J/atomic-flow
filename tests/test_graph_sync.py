"""P06-4: publication hot path syncs the graph store by per-file deltas."""
import json

from atomic_flow.graph.project import clean_build, project_files
from atomic_flow.graph.sync import store_path, sync_project_graph

FILES = {
    "app.py": b"from lib import helper\n\n\ndef run():\n    return helper()\n",
    "lib.py": b"def helper():\n    return 1\n",
}


def _project(tmp_path, files=FILES):
    for rel, payload in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return tmp_path


def edge_triples(store_document):
    return sorted((e["kind"], e["src"], e["dst"]) for e in store_document["edges"])


def node_keys(store_document):
    return sorted((n["id"], sorted(n.get("facts", {}).items(), key=str)) for n in store_document["nodes"])


def test_first_sync_creates_store_and_subsequent_delta_keeps_parity(tmp_path):
    project = _project(tmp_path)
    first = sync_project_graph(project, {})
    assert first["created"] is True and first["parsed_files"] == 2

    (project / "lib.py").write_bytes(b"def assist():\n    return 1\n")
    second = sync_project_graph(project, {"lib.py": b"def assist():\n    return 1\n"})
    assert second["created"] is False and second["deltas"] == 1 and second["parsed_files"] == 1

    document = json.loads(store_path(project).read_text(encoding="utf-8"))
    expected = clean_build(project_files(project))
    assert node_keys(document) == node_keys({"nodes": expected["nodes"]})
    assert edge_triples(document) == edge_triples({"edges": expected["edges"]})
    assert second["relinked_files"] >= 1  # importer relink happened without reparse


def test_sync_never_raises_and_logs_fallback(tmp_path, monkeypatch):
    project = _project(tmp_path)
    sync_project_graph(project, {})  # create
    import atomic_flow.graph.sync as sync_module
    monkeypatch.setattr(sync_module, "apply_delta",
                        lambda *a, **kw: (_ for _ in ()).throw(ValueError("boom")))
    (project / "lib.py").write_bytes(b"x = 1\n")  # publication already wrote the file
    result = sync_project_graph(project, {"lib.py": b"x = 1\n"})
    assert result["fallback"] is True and "delta sync failed" in result["reason"]
    log = project / ".atomic_flow" / "graph-sync.jsonl"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["status"] == "fallback"
    # the fallback rebuild restored parity with the published disk state
    document = json.loads(store_path(project).read_text(encoding="utf-8"))
    expected = clean_build(project_files(project))
    assert edge_triples(document) == edge_triples({"edges": expected["edges"]})


def test_delete_delta_removes_file_from_graph(tmp_path):
    project = _project(tmp_path)
    sync_project_graph(project, {})
    (project / "app.py").unlink()
    result = sync_project_graph(project, {"app.py": None})
    assert result["deltas"] == 1
    document = json.loads(store_path(project).read_text(encoding="utf-8"))
    assert all(n["path"] != "app.py" for n in document["nodes"])
    assert all("app.py" not in e["src"] for e in document["edges"])
