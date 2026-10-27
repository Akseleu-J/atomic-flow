from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import p07_requests_function_health as health

FILES = {"httpbin/core.py": "a" * 64, "httpbin/__init__.py": "b" * 64}
CERTS = {path: "c" * 64 for path in health.CERTIFICATE_PATHS}
WRONG_CA = "-----BEGIN CERTIFICATE-----\npublic unrelated test fixture\n-----END CERTIFICATE-----"


def complete(task):
    checks = {scheme: {"status": 200, "args": {"health": "p07"}} for scheme in ("http", "https")}
    checks.update(basic_auth={"status": 200, "authenticated": True, "user": "p07"},
                  wrong_ca="certificate_verification_failed", port1="connection_refused")
    if "www.google.co.uk" in health.TASK_HOSTS[task]:
        checks.update(mapped_http_alias={"host": "www.google.co.uk", "status": 200},
                      redirect_location="http://www.google.co.uk/get?health=p07")
    return {"task_id": task, "profile_sha256": health.PROFILE_SHA256,
            "input_sha256": health.canonical_sha(health.bound_inputs(task, FILES, CERTS)),
            "version_info": [3, 6, 15], "prefix": "/opt/p07-python36", "executable": "/opt/p07-python36/bin/python3.6",
            "isolated": 1, "httpbin_version": "0.5.0", "httpbin_module": health.PREFIX + "httpbin/__init__.py",
            "host_mappings": {host: [address] for host, address in health.TASK_HOSTS[task].items()},
            "urls": health.URLS, "ports": [80, 443], "server_host": "127.0.0.1", "checks": checks,
            "servers_stopped": True, "target_imports": [], "requests_imported": False,
            "target_setup_or_tests_executed": False, "calibrated": False, "verified_success": False}


def receipt(value):
    return "service logs\n" + health.RESULT_PREFIX + json.dumps(value) + "\n"


@pytest.mark.parametrize("task", list(health.TASK_HOSTS))
def test_fixed_cohort_health_preserves_url_and_mapping_contract(task):
    value = complete(task)
    assert health.validate_health(receipt(value), task, FILES, CERTS) == value
    assert value["calibrated"] is False


@pytest.mark.parametrize("field,value", [
    ("input_sha256", "0" * 64), ("version_info", [3, 9, 20]), ("prefix", "/usr/local"),
    ("executable", "/usr/bin/python3"), ("isolated", 0), ("httpbin_version", "0.10.2"),
    ("httpbin_module", "/testbed/httpbin.py"), ("ports", [8080, 8443]), ("server_host", "0.0.0.0"),
    ("servers_stopped", False), ("target_imports", ["requests"]), ("requests_imported", True),
    ("target_setup_or_tests_executed", True), ("calibrated", True), ("verified_success", True),
])
def test_mismatched_or_incomplete_health_cannot_be_accepted(field, value):
    task = "psf__requests-2148"
    observed = complete(task)
    observed[field] = value
    with pytest.raises(ValueError):
        health.validate_health(receipt(observed), task, FILES, CERTS)


def test_wrong_ca_negative_and_real_redirect_are_mandatory():
    task = "psf__requests-2148"
    for mutation in (lambda value: value["checks"].pop("wrong_ca"),
                     lambda value: value["checks"].update(wrong_ca="accepted"),
                     lambda value: value["checks"].update(redirect_location="http://httpbin.org/get"),
                     lambda value: value["host_mappings"].update({"www.google.co.uk": ["8.8.8.8"]})):
        value = complete(task)
        mutation(value)
        with pytest.raises(ValueError):
            health.validate_health(receipt(value), task, FILES, CERTS)


def test_inventory_certificate_and_task_changes_invalidate_result():
    task = "psf__requests-1963"
    text = receipt(complete(task))
    with pytest.raises(ValueError):
        health.validate_health(text, task, {**FILES, "other.py": "e" * 64}, CERTS)
    changed = copy.deepcopy(CERTS)
    changed[health.CERTIFICATE_PATHS[0]] = "f" * 64
    with pytest.raises(ValueError):
        health.validate_health(text, task, FILES, changed)
    with pytest.raises(ValueError):
        health.validate_health(text, "psf__requests-863", FILES, CERTS)


def test_missing_or_duplicate_receipt_fails():
    task = "psf__requests-863"
    for text in ("HTTP200", receipt(complete(task)) * 2):
        with pytest.raises(ValueError):
            health.validate_health(text, task, FILES, CERTS)


@pytest.mark.parametrize("task", list(health.TASK_HOSTS))
def test_bootstrap_is_fixed_real_service_and_secure_tls_without_target_import(task):
    source = health.bootstrap_source(task, FILES, CERTS, WRONG_CA)
    compile(source, "<fixed-health>", "exec")
    assert "make_server('127.0.0.1',80,httpbin.app)" in source
    assert "make_server('127.0.0.1',443,httpbin.app)" in source
    assert "urllib.request.ProxyHandler({})" in source
    assert "context.check_hostname" in source and "ssl.CERT_REQUIRED" in source
    assert "decoded.get('subjectAltName')!=(('DNS','httpbin.org'),)" in source
    assert "socket.create_connection(('127.0.0.1',1),timeout=1)" in source
    assert "http://www.google.co.uk/get?health=p07" in source
    assert "CERT_NONE" not in source and "verify=False" not in source
    assert "import requests" not in source
    assert "server.shutdown()" in source and "server.server_close()" in source
    assert "thread.join(3)" in source
    assert "os.environ['HTTPBIN_URL']='http://httpbin.org/'" in source


@pytest.mark.parametrize("path", ["/absolute.py", "../escape.py", "a//b.py", "a\\b.py", "C:foo"])
def test_unsafe_inventory_cannot_enter_source(path):
    with pytest.raises(ValueError):
        health.bootstrap_source("psf__requests-863", {path: "a" * 64}, CERTS, WRONG_CA)


def test_missing_fixed_certificate_or_unreviewed_task_refused():
    with pytest.raises(ValueError):
        health.bootstrap_source("psf__requests-2674", FILES, CERTS, WRONG_CA)
    with pytest.raises(ValueError):
        health.bootstrap_source("psf__requests-863", FILES, {}, WRONG_CA)
    with pytest.raises(ValueError):
        health.bootstrap_source("psf__requests-863", FILES, CERTS, "")


def test_profile_hash_and_service_mappings_match_exact_frozen_profile():
    profile_path = Path(__file__).resolve().parents[1] / "benchmarks/stage4/p07-requests-function-service-profile-v2.json"
    raw = profile_path.read_bytes().replace(b"\r\n", b"\n")
    assert health.sha(raw) == health.PROFILE_SHA256
    profile = json.loads(raw)
    assert {task["instance_id"]: task["service"]["host_mappings"] for task in profile["tasks"]} == health.TASK_HOSTS
    for task in profile["tasks"]:
        assert task["service"]["HTTPBIN_URL"] == health.URLS["http"]
        assert task["service"]["http_port"] == 80 and task["service"]["https_port"] == 443
