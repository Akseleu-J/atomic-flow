"""Shared project projection keeps advisory omissions visible and caches fresh."""
import hashlib

import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.graph import impact, read_plan
from atomic_flow.graph.adapter_ast import adapt_file as strict_adapt_file
from atomic_flow.graph.enrichment import SemanticBudget, enrich
from atomic_flow.graph.projection import EXTRACTION_BYTE_LIMIT, PLACEHOLDER_EXTRACTOR, PROJECTION_POLICY
from atomic_flow.graph.schema import GraphContractError
from atomic_flow.graph.semantic import entry_points, file_dependencies, layers, tour

LARGE = b"#" + b"x" * EXTRACTION_BYTE_LIMIT + b"\n"
SMALL = b"def helper():\n    return 1\n"


def canonical(pack):
    return {key: value for key, value in pack.items() if key != "stats"}


def test_oversized_goal_retains_identity_and_explicit_unknown_coverage():
    with pytest.raises(GraphContractError):
        strict_adapt_file("large.py", LARGE)
    index = read_plan.ContextPackIndex()
    pack = impact.context_pack({"large.py": LARGE}, ["large.py"], index=index, byte_cap=len(LARGE))
    node = index.files["large.py"]["nodes"][0]
    assert node["facts"]["extractor"] == PLACEHOLDER_EXTRACTOR
    assert node["facts"]["parse_ok"] is None and len(index.files["large.py"]["nodes"]) == 1
    assert node["source"]["digest"] == hashlib.sha256(LARGE).hexdigest()
    assert pack["entries"][0]["file"] == "large.py"
    assert pack["coverage_complete"] is False and pack["read_proof"] is False
    assert pack["projection_policy"] == PROJECTION_POLICY
    assert pack["coverage_omissions"][0]["path"] == "large.py"
    assert pack["coverage_omissions"][0]["size_bytes"] == len(LARGE)
    assert not pack["parse_failures"]  # Omission is not a syntax failure.


def test_coverage_omission_is_visible_even_when_large_file_is_not_a_pack_entry():
    pack = impact.context_pack({"goal.py": b"VALUE = 1\n", "large.py": LARGE}, ["goal.py"])
    assert [row["file"] for row in pack["entries"]] == ["goal.py"]
    assert pack["coverage_complete"] is False
    assert any(row["path"] == "large.py" for row in pack["omissions"])


def test_small_large_small_transitions_remove_stale_symbols_and_match_clean_pack():
    index = read_plan.ContextPackIndex()
    before = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    assert "helper" in index.exports
    large = impact.context_pack({"goal.py": LARGE}, ["goal.py"], index=index)
    assert "helper" not in index.exports and not index.reverse["imports"]
    assert canonical(large) == canonical(impact.context_pack({"goal.py": LARGE}, ["goal.py"]))
    unchanged = impact.context_pack({"goal.py": LARGE}, ["goal.py"], index=index)
    assert unchanged["stats"]["parsed_files"] == 0
    changed = impact.context_pack({"goal.py": LARGE.replace(b"x", b"y", 1)}, ["goal.py"], index=index)
    assert changed["revision"] != large["revision"]
    restored = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    assert canonical(restored) == canonical(before)
    assert "helper" in index.exports and restored["coverage_complete"] is True


def test_projection_policy_change_invalidates_warm_records_and_revision(monkeypatch):
    index = read_plan.ContextPackIndex()
    first = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    calls = []
    original = impact.adapt_file
    def counted(path, raw):
        calls.append(path)
        return original(path, raw)
    monkeypatch.setattr(impact, "adapt_file", counted)
    monkeypatch.setattr(read_plan, "PROJECTION_POLICY", "project-file-projection/test-upgrade")
    second = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    assert calls == ["goal.py"] and second["revision"] != first["revision"]


def test_cancelled_placeholder_update_keeps_previous_index_snapshot():
    index = read_plan.ContextPackIndex()
    original = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    def refuse(path, raw):
        raise TaskCancelled("synthetic projection cancellation")
    with pytest.raises(TaskCancelled):
        index.update({"goal.py": LARGE}, adapter=refuse)
    restored = impact.context_pack({"goal.py": SMALL}, ["goal.py"], index=index)
    assert canonical(restored) == canonical(original)


def test_oversized_impacts_keep_full_gate_unknown_and_all_source_hashes():
    files = {"goal.py": SMALL, "tests/test_large.py": LARGE, "asset.bin": b"opaque"}
    plan = impact.verification_plan(files, ["goal.py"])
    assert plan["impact_status"] == "unknown" and plan["coverage_complete"] is False
    assert plan["omitted_oversized"] == ["tests/test_large.py"]
    assert plan["omitted_unsupported"] == ["asset.bin", "tests/test_large.py"]
    assert plan["required_final_check"] == plan["loop_check"] == "full_pytest"
    assert plan["source_digests"] == {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()}


def test_structural_semantic_reports_partial_dependency_coverage_without_return_type_change():
    from atomic_flow.graph.project import clean_build
    files = {"goal.py": SMALL, "large.py": LARGE}
    dependencies = file_dependencies(clean_build(files))
    assert isinstance(dependencies, dict) and set(dependencies) == set(files)
    for report in (layers(files), entry_points(files), tour(files)):
        assert report["coverage_complete"] is False and report["dependency_coverage"] == "partial"
        assert report["coverage_omissions"][0]["path"] == "large.py"


def test_semantic_analyzer_admission_is_not_weakened_for_placeholders():
    def forbidden(request):
        pytest.fail("oversized source reached semantic analyzer")
    with pytest.raises(GraphContractError, match="byte/file cap"):
        enrich({"large.py": LARGE}, project_scope="fixture",
               identity=dict(extractor="ast", prompt="fixed", model="scripted", config="fixed", language="en"),
               analyzer=forbidden, budget=SemanticBudget(1), cache={})
