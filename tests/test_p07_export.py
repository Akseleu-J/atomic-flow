"""Curated Kaggle output preserves evidence and excludes full target workspaces."""
import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
export = importlib.import_module("prepare_p07_kaggle").export_campaign


def test_export_is_small_scrubbed_and_retains_all_30_task_roots(tmp_path):
    scratch, out = tmp_path / "scratch", tmp_path / "out"
    scratch.mkdir()
    packet = {"tasks": [{"instance_id": f"case-{i}"} for i in range(30)]}
    (scratch / "summary.json").write_text(json.dumps({"cost_complete": True, "rows": [], "p07": "OPEN"}))
    (scratch / "ledger.json").write_text('{"attempts":[{"status":"settled","cost_usd":0.01}]}')
    nested = scratch / "tasks/case-0/atomic"
    nested.mkdir(parents=True)
    (nested / "provider-envelope-1.json").write_text('{"content":"fixture-secret-value"}')
    workspace = nested / "workspace"
    workspace.mkdir()
    (workspace / "target.py").write_text("x = 'fixture-secret-value'\n" * 100000)
    result = export(scratch, out, packet, [b"fixture-secret-value"], {"status": "campaign_returned"})
    assert result["cost_complete"] and result["p07"] == "OPEN"
    with zipfile.ZipFile(out / "results.zip") as archive:
        assert "ledger.json" in archive.namelist()
        assert all("workspace" not in Path(name).parts for name in archive.namelist())
        assert b"fixture-secret-value" not in archive.read("tasks/case-0/atomic/provider-envelope-1.json")
        assert b"REDACTED" in archive.read("tasks/case-0/atomic/provider-envelope-1.json")
        assert len([n for n in archive.namelist() if n.endswith('/status.json')]) == 30
    assert all((out / f"tasks/case-{i}/baseline").is_dir() for i in range(30))
    receipt = json.loads((out / "archive-sha256.json").read_text())
    assert receipt["sha256"] == hashlib.sha256((out / "results.zip").read_bytes()).hexdigest()
    assert (out / "results.zip").stat().st_size < 50000


def test_timeout_export_preserves_original_summary_and_uncertain_attempt(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "summary.json").write_text('{"cost_complete":true,"p07":"OPEN","rows":[]}')
    ledger = b'{"attempts":[{"status":"in_flight","cost_usd":null}]}'
    (scratch / "ledger.json").write_bytes(ledger)
    result = export(scratch, tmp_path / "export", {"tasks": []}, [],
                    {"status": "campaign_timeout_unknown_do_not_repeat"})
    assert not result["cost_complete"] and result["attempt_outcome"] == "unknown_do_not_repeat"
    with zipfile.ZipFile(tmp_path / "export/results.zip") as archive:
        assert json.loads(archive.read("driver-summary.json"))["cost_complete"]
        assert archive.read("ledger.json") == ledger


def test_preparation_failure_still_exports_explicit_unknown_summary(tmp_path):
    result = export(tmp_path / "absent", tmp_path / "export", {"tasks": []}, [],
                    {"status": "framework_preparation_failed"})
    assert result["cost_complete"] is False and result["attempt_outcome"] == "unknown_do_not_repeat"
    assert (tmp_path / "export/results.zip").is_file()
