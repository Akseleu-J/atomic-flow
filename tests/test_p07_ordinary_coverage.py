"""The evidence inventory must not promote calibration or disclose solutions."""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import p07_ordinary_coverage as coverage  # noqa: E402


@pytest.fixture
def inputs(tmp_path):
    corpus, _ = coverage.read_json(coverage.ROOT, coverage.CORPUS)
    inventory, _ = coverage.read_json(coverage.ROOT, coverage.CALIBRATION)
    for name in (coverage.CORPUS, coverage.CALIBRATION,
                 *(row["accepted_evidence"] for row in inventory["rows"]),
                 *(row[0] for row in coverage.ACCEPTED.values())):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(coverage.ROOT / name, destination)
    corpus["tasks"][0]["patch"] = "PRIVATE_SENTINEL_PATCH"
    corpus["tasks"][0]["problem_statement"] = "PRIVATE_SENTINEL_PROBLEM"
    (tmp_path / coverage.CORPUS).write_text(json.dumps(corpus), encoding="utf8")
    return tmp_path, inventory


def test_only_proven_ordinary_rows_and_public_metadata_are_projected(inputs):
    root, _ = inputs
    result = coverage.build(root)
    assert result["counts"] == {"tasks": 30, "retained_calibrations": 30,
                                "accepted_scripted_ordinary": 6, "ordinary_not_established": 24}
    assert "PRIVATE_SENTINEL" not in json.dumps(result)
    assert all(row["full_public_ordinary"]["status"] == "not_established_in_this_inventory"
               for row in result["tasks"] if row["repo"] != "psf/requests")


def test_mismatched_calibration_cannot_be_promoted(inputs):
    root, inventory = inputs
    report = root / inventory["rows"][0]["accepted_evidence"]
    data = json.loads(report.read_text(encoding="utf8"))
    data["calibrated"] = False
    report.write_text(json.dumps(data), encoding="utf8")
    with pytest.raises(ValueError, match="calibration report"):
        coverage.build(root)
