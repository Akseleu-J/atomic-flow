"""P00 contract inventory is read-only and freezes source/provenance boundaries."""
import importlib.util
import json
from pathlib import Path


def test_p00_inventory_preserves_historical_evidence_and_declares_measurement_limits():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("p00_contracts", root / "tools/p00_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = [root / "validation/p00-baseline-20261024/card.json",
             root / "validation/p00-baseline-20261024/acceptance-registry.json"]
    before = [p.read_bytes() for p in paths]
    report = module.collect(root)
    assert [p.read_bytes() for p in paths] == before
    assert report["model_calls"] == 0 and report["phase"] == "P00"
    assert report["comparison"]["quality_status"] == "not_assessed"
    assert report["comparison"]["historical_unknown_cost"] == "retained; not zero"
    assert report["legacy_claim_corrections"][0]["agent_task_quality"].startswith("not_assessed")
    assert len(report["declared_dependencies"]) == 3
    assert {"api", "encoder", "kaggle", "benchmarks", "dev"} <= report["optional_dependencies"].keys()
    assert "tests/test_p01_contract.py" in report["coverage_test_inventory"]
    assert all(v["status"] == "retained_historical_not_rerun" for v in report["historical_evidence"].values())
    assert json.loads(json.dumps(report, allow_nan=False))["schema"] == "p00-contract-evidence/1"


def test_baseline_cache_measurement_never_replaces_existing_project_tree(tmp_path, monkeypatch):
    from atomic_flow.tree.builder import build_tree, save_tree

    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("p00_baseline", repo / "tools/p00_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    marker = save_tree(project, build_tree(project))
    original = marker.read_bytes()
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "git", lambda *_args: "test-source")
    monkeypatch.setattr("sys.argv", ["p00_baseline", "--repeats", "1", "--output", str(tmp_path / "baseline.json")])
    assert module.main() == 0
    assert marker.read_bytes() == original
    card = json.loads((tmp_path / "baseline.json").read_text())
    assert card["model_calls"] == 0 and card["indicators"]["tree_load"]["rows"][0]["value"] == 2
