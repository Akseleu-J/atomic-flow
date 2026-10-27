"""Strict Requests3362 oracle contracts; no target code executes on this host."""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_requests3362_calibrate as adapter  # noqa: E402


def task_control():
    task = next(item for item in json.loads((ROOT / "benchmarks/stage4/corpus-p07-v2.json").read_bytes())["tasks"]
                if item["instance_id"] == adapter.TASK)
    control = json.loads((ROOT / "benchmarks/stage4/p07-controls-v6.json").read_bytes())["tasks"][adapter.TASK]
    return task, control


def test_exact_task_and_prospective_control_keep_all76_obligations():
    task, control = task_control()
    wrong = adapter.admit_task(task, control)
    assert len(task["FAIL_TO_PASS"]) == 1 and len(task["PASS_TO_PASS"]) == 75
    assert adapter.oracle.sha(wrong.encode()) == control["wrong_sha256"]


@pytest.mark.parametrize("field,value", [("base_commit", "0" * 40), ("instance_id", "psf__requests-2674"),
    ("version", "2.9"), ("patch", ""), ("test_patch", ""), ("PASS_TO_PASS", []), ("FAIL_TO_PASS", [])])
def test_other_task_or_narrowed_obligation_refused(field, value):
    task, control = task_control()
    with pytest.raises(ValueError):
        adapter.admit_task({**task, field: value}, control)


def test_old_ascii_negative_refused():
    task, control = task_control()
    with pytest.raises(ValueError, match="v6 negative"):
        adapter.admit_task(task, {**control, "new": '+        encoding = "ascii"'})


def receipt(task, failed=False):
    ids = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    statuses = {test: "PASSED" for test in ids}
    if failed:
        statuses[ids[0]] = "FAILED"
    result = {"version_info": [3, 6, 15], "prefix": adapter.transplant.PREFIX,
        "executable": adapter.transplant.PREFIX + "/bin/python3.6", "pytest_version": "2.8.7",
        "pytest_module": adapter.health.PREFIX + "pytest.py", "requests_module": "/testbed/requests/__init__.py",
        "argv": [*adapter.health.PLUGIN_ARGS, "-rA"], "finished": True, "exit": int(failed),
        "collection": ids, "plugins": [["fixture", "pytest_httpbin.plugin"], ["mock", "pytest_mock"]],
        "reports": [{"id": test, "when": when, "outcome": statuses[test].lower() if when == "call" else "passed"}
                    for test in ids for when in ("setup", "call", "teardown")]}
    return result, statuses


def transcript(result, exit_code):
    return adapter.START + "\n" + adapter.RESULT + json.dumps(result) + "\nP07_TEST_EXIT=" + str(exit_code) + "\n" + adapter.END


def test_complete_native_gold_and_real_requirement_negative(monkeypatch):
    task, _ = task_control()
    for failed in (False, True):
        result, statuses = receipt(task, failed)
        monkeypatch.setattr(adapter.oracle, "parse_observations", lambda *args, statuses=statuses: (statuses, {}))
        row = adapter.classify_target(task, transcript(result, int(failed)), int(failed), None, None)
        assert row["verified_success"] is not failed
        assert row["regressions_passed"] is True
        assert len(row["tests"]) == 76


@pytest.mark.parametrize("mutation", ["missing", "skip", "setup", "unfinished", "duplicate", "plugin", "summary"])
def test_missing_skipped_setup_error_or_disagreeing_native_receipt_refused(monkeypatch, mutation):
    task, _ = task_control()
    result, statuses = receipt(task)
    if mutation == "missing":
        result["collection"].pop()
    elif mutation == "skip":
        result["reports"][1]["outcome"] = "skipped"
    elif mutation == "setup":
        result["reports"][0]["outcome"] = "failed"
    elif mutation == "unfinished":
        result["finished"] = False
    elif mutation == "duplicate":
        result["collection"].append(result["collection"][0])
    elif mutation == "plugin":
        result["plugins"].append(["duplicate", "pytest_httpbin.plugin"])
    else:
        statuses[result["collection"][0]] = "FAILED"
    monkeypatch.setattr(adapter.oracle, "parse_observations", lambda *args: (statuses, {}))
    with pytest.raises(ValueError):
        adapter.classify_target(task, transcript(result, 0), 0, None, None)


@pytest.mark.parametrize("text,exit_code", [("", 0), (adapter.START + adapter.END, 0),
    (adapter.START + adapter.START + adapter.END, 0), (adapter.END + adapter.START, 0), ("native crash", 3)])
def test_incomplete_or_crashed_output_is_never_a_negative(text, exit_code):
    task, _ = task_control()
    with pytest.raises(ValueError):
        adapter.classify_target(task, text, exit_code, None, None)


def test_bootstrap_is_syntax_valid_and_preserves_native_assertions_and_source_import_boundary():
    source = adapter.bootstrap_source({"pytest.py": "a" * 64})
    ast.parse(source)
    assert source.index("import pytest") < source.index("sys.path.insert(0,'/testbed')") < source.index("import requests")
    assert "--assert=plain" not in source and "--noconftest" not in source
    assert "['-rA']" in source and "entries=list(pkg_resources.iter_entry_points('pytest11'))" in source
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in source and "os.environ.pop(key,None)" in source
    assert "pytest.main(argv,plugins=[recorder])" in source


def policy():
    return {"Image": "sha256:" + "a" * 64, "Mounts": [], "HostConfig": {"Binds": None, "Privileged": False,
        "NetworkMode": "none", "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges"],
        "Memory": 6 * 1024**3, "NanoCpus": 2_000_000_000, "PidsLimit": 256}}


@pytest.mark.parametrize("key,value", [("NetworkMode", "host"), ("CapAdd", ["NET_ADMIN"]), ("Privileged", True),
    ("Binds", ["/:/host"]), ("Memory", 8 * 1024**3), ("PidsLimit", 512)])
def test_target_worker_cannot_widen_isolation(key, value):
    config = policy()
    adapter.inspect_policy(config, config["Image"])
    config["HostConfig"][key] = value
    with pytest.raises(ValueError):
        adapter.inspect_policy(config, config["Image"])


def health_fixture(tmp_path, monkeypatch):
    cert_dir = tmp_path / "cert"
    health_dir = tmp_path / "health"
    cert_dir.mkdir()
    health_dir.mkdir()
    (cert_dir / "wrongca.pem").write_text("public CA")
    admitted = {"image_id": "sha256:" + "a" * 64, "installed_files": {}, "after": {"head": "source", "diff": "", "status": ""}}
    observed = {"health": "verified"}
    stdout, stderr = b"health proof", b""
    report = {"status": "synthetic_legacy_health_qualified", "cleanup_confirmed": True, "cleanup_exit": 0,
        "certificate_receipt_sha256": adapter.oracle.sha(b"certificate receipt"), "image_id": admitted["image_id"],
        "process_exit": 0, "stdout_sha256": adapter.oracle.sha(stdout), "stderr_sha256": adapter.oracle.sha(stderr),
        "bootstrap_sha256": adapter.oracle.sha(b"host health bootstrap"), "observed": observed,
        "before": admitted["after"], "after": admitted["after"], "target_setup_or_tests_executed": False, "model_calls": 0}
    monkeypatch.setattr(adapter.certificate, "admit_certificate_receipt", lambda directory: (b"certificate receipt", admitted))
    monkeypatch.setattr(adapter.health, "bootstrap_source", lambda *args: "host health bootstrap")
    monkeypatch.setattr(adapter.health, "validate_health", lambda text: observed)
    (health_dir / "health.stdout").write_bytes(stdout)
    (health_dir / "health.stderr").write_bytes(stderr)
    return cert_dir, health_dir, report


def test_qualified_health_bound_to_same_cert_image_bootstrap_and_raw_evidence(tmp_path, monkeypatch):
    cert_dir, health_dir, report = health_fixture(tmp_path, monkeypatch)
    (health_dir / "health.json").write_text(json.dumps(report))
    admitted, receipts = adapter.admit_health(cert_dir, health_dir)
    assert admitted["image_id"] == report["image_id"]
    assert receipts["certificate_receipt_sha256"] == report["certificate_receipt_sha256"]


@pytest.mark.parametrize("field,value", [("status", "health_unavailable"), ("cleanup_confirmed", False),
    ("image_id", "other"), ("certificate_receipt_sha256", "0" * 64), ("stdout_sha256", "0" * 64),
    ("bootstrap_sha256", "0" * 64), ("after", {"head": "changed"}), ("model_calls", 1)])
def test_unknown_health_or_changed_evidence_refused(tmp_path, monkeypatch, field, value):
    cert_dir, health_dir, report = health_fixture(tmp_path, monkeypatch)
    (health_dir / "health.json").write_text(json.dumps({**report, field: value}))
    with pytest.raises(ValueError):
        adapter.admit_health(cert_dir, health_dir)


def test_existing_output_or_nonroot_never_starts_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calibrate", "--certificate-receipt", "cert", "--health-receipt", "health", "--output", str(tmp_path)])
    monkeypatch.setattr(adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(adapter.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="fresh output"):
        adapter.main()


def test_same_cardinality_substituted_regression_ids_refused():
    task, control = task_control()
    task["PASS_TO_PASS"] = [*task["PASS_TO_PASS"][:-1], "different::test"]
    with pytest.raises(ValueError):
        adapter.admit_task(task, control)
