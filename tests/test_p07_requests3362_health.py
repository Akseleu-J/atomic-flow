from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import p07_requests3362_health as health


def complete_result():
    collection = ["test_health.py::" + name for name in health.TEST_IDS]
    return {"exit_code": 0, "session_exit": 0, "finished": True, "servers_stopped": True,
            "target_imports": [], "requests_imported": False, "entrypoints": health.ENTRYPOINTS,
            "version_info": [3, 6, 15], "prefix": health.transplant.PREFIX,
            "executable": health.transplant.PREFIX + "/bin/python3.6", "assertion_mode": "plain_synthetic_only",
            "fixture_tests_sha256": health.sha(health.TEST_SOURCE.encode()), "collection": collection,
            "reports": [{"nodeid": node, "when": phase, "outcome": "passed"}
                        for node in collection for phase in ("setup", "call", "teardown")],
            "plugins": [["pytest_httpbin.plugin", "pytest_httpbin.plugin"], ["pytest_mock", "pytest_mock"],
                        ["capture", "_pytest.capture"], ["host", "host_health_recorder"]],
            "imports": {name: health.PREFIX + name.replace(".", "/") + ".py"
                        for name in ("pytest", "httpbin", "pytest_httpbin.plugin", "pytest_mock")}}


def receipt(value):
    return "captured pytest output\n" + health.RESULT_PREFIX + json.dumps(value) + "\n"


def test_complete_fixed_health_is_descriptive_not_target_grading():
    result = complete_result()
    assert health.validate_health(receipt(result)) == result
    assert "verified_success" not in result


@pytest.mark.parametrize("field,value", [
    ("exit_code", 1), ("session_exit", 3), ("finished", False), ("servers_stopped", False),
    ("target_imports", ["requests"]), ("requests_imported", True),
    ("entrypoints", {"httpbin": "other.plugin"}), ("version_info", [3, 9, 20]),
    ("prefix", "/usr/local"), ("executable", "/usr/bin/python3"),
    ("assertion_mode", "native_target_verified"), ("fixture_tests_sha256", "0" * 64),
])
def test_incomplete_or_differently_bound_health_is_refused(field, value):
    observed = complete_result()
    observed[field] = value
    with pytest.raises(ValueError):
        health.validate_health(receipt(observed))


def test_all_three_synthetic_obligations_and_nine_reports_are_required():
    original = complete_result()
    for mutation in (lambda r: r["collection"].pop(), lambda r: r["reports"].pop(),
                     lambda r: r["reports"][0].update(outcome="skipped"),
                     lambda r: r["reports"][1].update(outcome="failed"),
                     lambda r: r["reports"][1].update(when="setup")):
        value = copy.deepcopy(original)
        mutation(value)
        with pytest.raises(ValueError):
            health.validate_health(receipt(value))


def test_duplicate_or_extra_plugin_and_target_import_are_refused():
    original = complete_result()
    for mutation in (lambda r: r["plugins"].append(["duplicate", "pytest_mock"]),
                     lambda r: r["plugins"].append(["unsafe", "project_plugin"]),
                     lambda r: r["imports"].update(pytest="/testbed/src/pytest.py")):
        value = copy.deepcopy(original)
        mutation(value)
        with pytest.raises(ValueError):
            health.validate_health(receipt(value))


def test_missing_or_duplicate_finished_receipt_is_refused():
    with pytest.raises(ValueError):
        health.validate_health("3 passed")
    with pytest.raises(ValueError):
        health.validate_health(receipt(complete_result()) * 2)


def test_v2_health_version_is_explicit_and_bound_to_receipt():
    files = {f"package/file{index}.py": "a" * 64 for index in range(238)}
    source = health.bootstrap_source(files, "-----BEGIN CERTIFICATE-----", httpbin_version="0.5.0")
    compile(source, "<v2-health>", "exec")
    assert "pkg_resources.get_distribution('httpbin').version!=data['httpbin_version']" in source
    result = complete_result()
    result["httpbin_version"] = "0.5.0"
    assert health.validate_health(receipt(result), httpbin_version="0.5.0") == result
    with pytest.raises(ValueError):
        health.validate_health(receipt(result))
    with pytest.raises(ValueError):
        health.bootstrap_source(files, "-----BEGIN CERTIFICATE-----", httpbin_version="0.10.2")


def test_fixed_bootstrap_confines_imports_checks_all_bytes_and_uses_verified_tls():
    files = {f"package/file{index}.py": "a" * 64 for index in range(235)}
    source = health.bootstrap_source(files, "-----BEGIN CERTIFICATE-----\npublic test fixture\n")
    compile(source, "<host-health>", "exec")
    assert "sys.flags.isolated" in source
    assert "hashlib.sha256(path.read_bytes())" in source
    assert "pkg_resources.iter_entry_points('pytest11')" in source
    assert "ep.load(" not in source
    assert "--noconftest" in source and "--assert=plain" in source
    assert "config.pluginmanager.list_name_plugin()" in source
    assert "threading.Thread.join(server,3)" in source
    assert "server._server.server_close()" in source
    assert "ssl.CERT_REQUIRED" in health.TEST_SOURCE
    assert "context.check_hostname" in health.TEST_SOURCE
    assert "unrelated CA unexpectedly accepted" in health.TEST_SOURCE
    assert "verify=False" not in source and "CERT_NONE" not in source
    assert "requests.get" not in source


@pytest.mark.parametrize("files", [{}, {"../escape.py": "a" * 64}])
def test_narrowed_or_unsafe_installed_inventory_is_refused(files):
    with pytest.raises(ValueError):
        health.bootstrap_source(files, "-----BEGIN CERTIFICATE-----")


def config():
    return {"Image": "sha256:" + "a" * 64, "Mounts": [], "Config": {"User": "65534:65534"},
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": [],
                           "SecurityOpt": ["no-new-privileges"], "Memory": 1024**3,
                           "NanoCpus": 2_000_000_000, "PidsLimit": 128}}


@pytest.mark.parametrize("field,value", [("NetworkMode", "host"), ("ReadonlyRootfs", False),
    ("CapAdd", ["NET_ADMIN"]), ("Privileged", True), ("Binds", ["/:/host"]), ("Memory", 2 * 1024**3)])
def test_worker_isolation_cannot_expand(field, value):
    value_config = config()
    health.inspect_policy(value_config, value_config["Image"])
    value_config["HostConfig"][field] = value
    with pytest.raises(ValueError):
        health.inspect_policy(value_config, value_config["Image"])


def test_existing_output_is_refused_before_certificate_import(tmp_path, monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    with pytest.raises(ValueError, match="fresh output"):
        health.qualify(tmp_path / "missing", tmp_path)


def test_actual_certificate_image_requires_exact_parent_ancestry_and_overlay_labels():
    parent = {"Id": "sha256:" + "a" * 64, "RootFS": {"Layers": ["original", "private-runtime"]}}
    overlay = {"pytest_httpbin/certs/cert.pem": "c" * 64}
    image = {"Id": "sha256:" + "b" * 64, "Os": "linux", "Architecture": "amd64",
             "RootFS": {"Layers": [*parent["RootFS"]["Layers"], "certificate-overlay"]},
             "Config": {"Labels": {"atomic.p07.requests.cert.parent": parent["Id"],
                        "atomic.p07.requests.cert.overlay": health.sha(json.dumps(overlay, sort_keys=True).encode())}}}
    admitted = {"parent_image_id": parent["Id"], "parent_runtime_report": {"image_inspect": copy.deepcopy(parent)},
                "image_id": image["Id"], "image_inspect": copy.deepcopy(image), "overlay_files": overlay}
    health.admit_image(image, parent, admitted)
    for mutation in (lambda value: value.update(Id="sha256:" + "f" * 64),
                     lambda value: value.update(Os="windows"),
                     lambda value: value["RootFS"].update(Layers=["unrelated", "private-runtime", "certificate-overlay"]),
                     lambda value: value["Config"]["Labels"].update({"atomic.p07.requests.cert.parent": "other"}),
                     lambda value: value["Config"]["Labels"].update({"atomic.p07.requests.cert.overlay": "f" * 64})):
        value = copy.deepcopy(image)
        mutation(value)
        with pytest.raises(ValueError):
            health.admit_image(value, parent, admitted)
