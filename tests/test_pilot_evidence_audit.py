"""Offline evidence reconciliation tests; no providers or candidate execution."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_evidence_audit", ROOT / "tools/audit_live_pilot.py")
audit_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_tool)


def write_json(path, body):
    path.write_text(json.dumps(body), encoding="utf-8")


def saved_run(root, suffix, arm, costs, *, receipt=False, complete=None):
    folder = root / ("stage4-live-pilot-" + suffix)
    folder.mkdir()
    rows = [{"attempt": i + 1, "status": "response", "request_outcome": "completed",
             "cost_usd": cost, "tokens_in": 2, "tokens_out": 1,
             "request_sha256": "same-request", "response_sha256": "same-response"}
            for i, cost in enumerate(costs)]
    complete = bool(rows) and all(cost is not None for cost in costs) if complete is None else complete
    known = sum(cost for cost in costs if cost is not None)
    report = {"arm": arm, "status": "failed", "calls": len(rows), "cost_usd": known if complete else None,
              "cost_complete": complete, "known_cost_lower_bound_usd": known,
              "tokens_in": len(rows) * 2, "tokens_out": len(rows), "wall_seconds": 1.0}
    write_json(folder / "report.json", report)
    write_json(folder / "attempts.json", rows)
    if receipt:
        write_json(folder / "kaggle-quota.json", {"credential_label": "authorized-fixture", "utc": "2026-10-24T10:00:00+00:00",
                   "before": [{"period": "DAILY", "used": 1.0, "allowed": 10.0}],
                   "after": [{"period": "DAILY", "used": 1.0 + known, "allowed": 10.0}]})
    return folder, report


def test_retains_failed_unknown_zero_and_repeated_records_without_deduplication(tmp_path):
    saved_run(tmp_path, "baseline", "baseline", [.1, .2], receipt=True)
    saved_run(tmp_path, "atomic", "atomic", [.3, None])
    saved_run(tmp_path, "environment", "baseline", [])
    observed = audit_tool.audit(tmp_path)
    assert not observed["inconsistencies"]
    assert observed["summary"]["calls_reported"] == observed["summary"]["attempt_records"] == 4
    assert observed["summary"]["known_cost_lower_bound_usd"] == pytest.approx(.6)
    assert observed["summary"]["incomplete_cost_runs"] == 2
    assert observed["summary"]["zero_call_runs"] == 1
    assert observed["by_arm"]["baseline"]["status_counts"] == {"failed": 2}
    assert observed["quota_coverage"]["missing_charged_runs"] == ["stage4-live-pilot-atomic"]
    assert observed["repeated_fingerprints"][0]["records"] == 4
    atomic = next(row for row in observed["runs"] if row["arm"] == "atomic")
    assert atomic["unknown_charge_records"] == 1 and atomic["cost_reported_usd"] is None
    assert not observed["model_quality_measured"] and observed["model_calls_made_by_audit"] == 0


def test_mismatched_cost_count_quota_and_candidate_are_errors_and_cli_exits_nonzero(tmp_path, monkeypatch):
    folder, report = saved_run(tmp_path, "baseline", "baseline", [.25], receipt=True)
    report.update(calls=2, cost_usd=.5, patch_sha256="wrong")
    write_json(folder / "report.json", report)
    (folder / "candidate.patch").write_bytes(b"source-only-data")
    output = tmp_path / "audit.json"
    monkeypatch.setattr("sys.argv", ["audit_live_pilot.py", str(tmp_path), "--output", str(output)])
    assert audit_tool.main() == 1
    observed = json.loads(output.read_text(encoding="utf-8"))
    codes = {row["code"] for row in observed["inconsistencies"]}
    assert {"call_count_mismatch", "report_attempt_cost_mismatch", "quota_cost_mismatch", "candidate_hash_mismatch"} <= codes


def test_missing_receipts_are_coverage_gaps_and_known_hashes_match_without_changing_inputs(tmp_path):
    folder, report = saved_run(tmp_path, "baseline", "baseline", [.1])
    patch = b"patch bytes are never imported or executed"
    (folder / "candidate.patch").write_bytes(patch)
    report["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    write_json(folder / "report.json", report)
    original = {p.name: p.read_bytes() for p in folder.iterdir()}
    observed = audit_tool.audit(tmp_path)
    assert not observed["inconsistencies"] and observed["quota_coverage"]["receipts"] == 0
    assert not observed["quota_coverage"]["all_runs_have_receipts"]
    assert observed["quota_coverage"]["max_observed_absolute_error_usd"] is None
    for entry in observed["evidence_files"]:
        raw = (tmp_path / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest() and entry["bytes"] == len(raw)
    assert original == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_decreased_quota_counter_is_retained_as_possible_reset_not_false_cost_mismatch(tmp_path):
    folder, _ = saved_run(tmp_path, "baseline", "baseline", [.1], receipt=True)
    path = folder / "kaggle-quota.json"
    quota = json.loads(path.read_text())
    quota["after"][0]["used"] = .05
    write_json(path, quota)
    observed = audit_tool.audit(tmp_path)
    assert not observed["inconsistencies"]
    assert any(row["code"] == "quota_counter_decreased_possible_reset" for row in observed["warnings"])
    comparison = observed["runs"][0]["quota_comparisons"][0]
    assert comparison["delta_usd"] == pytest.approx(-.95) and comparison["matches_reported_cost"] is None


def test_bounded_json_rejects_oversized_attempts_and_nonfinite_report_fields(tmp_path, monkeypatch):
    folder, report = saved_run(tmp_path, "baseline", "baseline", [.1])
    report["wall_seconds"] = float("nan")
    report["arm"] = {"bad": "type"}
    report["status"] = float("nan")
    write_json(folder / "report.json", report)
    monkeypatch.setattr(audit_tool, "MAX_FILE_BYTES", 500)
    (folder / "attempts.json").write_bytes(b" " * 501)
    observed = audit_tool.audit(tmp_path)
    codes = {row["code"] for row in observed["inconsistencies"]}
    assert {"invalid_arm", "invalid_wall_seconds", "invalid_status", "unreadable_or_invalid_attempts_json"} <= codes
    json.dumps(observed, allow_nan=False)


def test_empty_root_refuses_silent_success_and_tolerance_must_be_positive(tmp_path):
    assert audit_tool.audit(tmp_path)["inconsistencies"][0]["code"] == "no_pilot_reports"
    for value in (0, -1, float("nan"), True):
        with pytest.raises(ValueError):
            audit_tool.audit(tmp_path, tolerance_usd=value)


def test_cli_refuses_to_overwrite_historical_evidence(tmp_path, monkeypatch):
    folder, _ = saved_run(tmp_path, "baseline", "baseline", [.1])
    target = folder / "report.json"
    before = target.read_bytes()
    monkeypatch.setattr("sys.argv", ["audit_live_pilot.py", str(tmp_path), "--output", str(target)])
    with pytest.raises(SystemExit) as exc:
        audit_tool.main()
    assert exc.value.code == 2 and target.read_bytes() == before
