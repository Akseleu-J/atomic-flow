"""A node projection must not inherit an unbounded repository diagnostic list."""
import json

import pytest

from atomic_flow.graph import operations
from atomic_flow.graph.schema import GraphContractError


def test_scoped_inspect_keeps_full_revision_without_global_diagnostic_overflow(tmp_path, monkeypatch):
    (tmp_path / "selected.py").write_text("def selected():\n    return missing()\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def other():\n    return 1\n", encoding="utf-8")
    original = operations.clean_build

    def dense_diagnostics(files):
        build = original(files)
        # The real SymPy preflight exceeded 1 MiB in metadata despite a node
        # selection. Reproduce that condition without a heavyweight repository.
        build["unresolved"] += ["other.py: " + str(i) + " " + "unresolved_dynamic_reference_" * 6 for i in range(7000)]
        return build

    monkeypatch.setattr(operations, "clean_build", dense_diagnostics)
    result = operations.graph_operation(tmp_path, "inspect", node_id="file:selected.py", byte_cap=8192)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 8192
    assert result["output_complete"] and result["read_proof"] is False
    assert result["semantic_correctness"] == "not_assessed"
    assert [node["id"] for node in result["nodes"]] == ["file:selected.py"]
    assert result["unresolved"] and all(item.startswith("selected.py: ") for item in result["unresolved"])
    scope = result["projection_scope"]
    assert scope["source_revision_scope"] == "all_admitted_files"
    assert scope["global_counts"]["unresolved"] > 7000
    assert any(item["reason"] == "outside_requested_scope" and item["counts"]["unresolved"] >= 7000
               for item in result["omissions"])
    revision = result["source_revision"]
    (tmp_path / "other.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    updated = operations.graph_operation(tmp_path, "inspect", node_id="file:selected.py", byte_cap=8192)
    assert updated["source_revision"] != revision
    assert updated["nodes"] == result["nodes"]
    # The unscoped operation still refuses this oversized global metadata.
    with pytest.raises(GraphContractError, match="metadata exceeds"):
        operations.graph_operation(tmp_path, "inspect", byte_cap=8192)


def test_scoped_entry_membership_is_computed_over_the_full_graph(tmp_path):
    (tmp_path / "a.py").write_text("from b import target\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    full = operations.graph_operation(tmp_path, "inspect")
    scoped = operations.graph_operation(tmp_path, "inspect", node_id="file:b.py")
    assert full["source_revision"] == scoped["source_revision"]
    assert "b.py" not in scoped["entry_points"]["entry_files"]
    assert set(scoped["entry_points"]["source_digests"]) == {"b.py"}
    assert set(scoped["layers"]["source_digests"]) == {"b.py"}
    assert all(paths == ["b.py"] for paths in scoped["layers"]["groups"].values())
    assert scoped["output_complete"] and scoped["read_proof"] is False


def test_scoped_overflow_drops_whole_records_with_exact_field_counts(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    original = operations.clean_build

    def many_edges(files):
        build = original(files)
        build["edges"] += [{"src": "file:a.py", "dst": "sym:a.py::target", "kind": "references",
                            "diagnostic": str(index) + "x" * 200} for index in range(1000)]
        return build

    monkeypatch.setattr(operations, "clean_build", many_edges)
    result = operations.graph_operation(tmp_path, "inspect", node_id="file:a.py", byte_cap=8192)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 8192
    assert result["output_complete"] is False
    assert result["nodes"] and result["nodes"][0]["id"] == "file:a.py"
    omission = next(item for item in result["omissions"] if item["reason"] == "output_byte_cap" and item["field"] == "edges")
    assert omission["items"] == result["projection_scope"]["global_counts"]["edges"] - len(result["edges"])
