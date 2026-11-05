from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_flow.config import ExecutionConfig
from atomic_flow.evaluation import calibrate, load_suite

PILOT = Path(__file__).resolve().parents[1] / "benchmarks" / "pilot"
ACTIVE = PILOT / "tasks-calibration-20261105.json"


def test_active_calibration_preserves_all_historical_tasks_and_grading():
    historical = load_suite(PILOT / "tasks.json")
    active = copy.deepcopy(load_suite(ACTIVE))
    provenance = active.pop("revision_provenance")
    for label, record in provenance.items():
        assert record["historical_commit"] == historical[f"{label}_commit"]
        assert record["export_commit"] == active[f"{label}_commit"]
        active[f"{label}_commit"] = record["historical_commit"]
    assert active == historical


@pytest.mark.parametrize("mutation", ["null", "missing_label", "bad_tree", "wrong_export", "extra_field"])
def test_revision_provenance_rejects_invalid_records(tmp_path, mutation):
    suite = json.loads(ACTIVE.read_text(encoding="utf-8"))
    records = suite["revision_provenance"]
    if mutation == "null":
        suite["revision_provenance"] = None
    elif mutation == "missing_label":
        del records["gold"]
    elif mutation == "bad_tree":
        records["base"]["tree"] = "HEAD"
    elif mutation == "wrong_export":
        records["base"]["export_commit"] = "a" * 40
    else:
        records["base"]["fallback"] = "a" * 40
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps(suite), encoding="utf-8")
    with pytest.raises(ValueError, match="[Pp]rovenance"):
        load_suite(manifest)


@pytest.mark.parametrize("bad_label", [None, "base", "gold"])
def test_tree_validation_precedes_any_export_and_preserves_provenance(tmp_path, monkeypatch, bad_label):
    suite = load_suite(ACTIVE)
    records = suite["revision_provenance"]
    resolved = []
    exported = []

    def resolve(args, **kwargs):
        label = next(label for label, record in records.items()
                     if args[-1] == record["export_commit"] + "^{tree}")
        resolved.append(label)
        return SimpleNamespace(stdout=("0" * 40 if label == bad_label else records[label]["tree"]) + "\n")

    def export(repository, commit, destination):
        exported.append(commit)
        destination.mkdir()

    monkeypatch.setattr("atomic_flow.evaluation.subprocess.run", resolve)
    monkeypatch.setattr("atomic_flow.evaluation.export_snapshot", export)
    monkeypatch.setattr("atomic_flow.evaluation.check_snapshot",
                        lambda workspace, *args: {"status": "fail" if workspace.name == "base" else "pass"})
    if bad_label:
        with pytest.raises(ValueError, match="pinned historical tree"):
            calibrate(tmp_path, ACTIVE, execution=ExecutionConfig(mode="local"))
        assert exported == []
    else:
        report = calibrate(tmp_path, ACTIVE, execution=ExecutionConfig(mode="local"))
        assert resolved == ["base", "gold"]
        assert exported == [suite["base_commit"], suite["gold_commit"]]
        assert report["revision_provenance"] == records
        assert report["calibrated"] is True
        assert report["model_calls"] == 0
