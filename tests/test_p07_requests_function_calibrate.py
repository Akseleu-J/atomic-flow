"""Function-task contracts, without target setup, imports or test execution."""
import ast
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_requests_function_calibrate as adapter  # noqa: E402

TASKS = ("psf__requests-863", "psf__requests-1963", "psf__requests-2148", "psf__requests-2317")


def task_control(task_id=TASKS[0]):
    task = next(row for row in json.loads(adapter.MANIFEST.read_bytes())["tasks"] if row["instance_id"] == task_id)
    control = json.loads(adapter.CONTROLS.read_bytes())["tasks"][task_id]
    return task, control


@pytest.mark.parametrize("task_id,count", zip(TASKS, (64, 119, 128, 141), strict=True))
def test_all_four_exact_original_tasks_keep_every_requirement_and_own_base(task_id, count):
    task, control = task_control(task_id)
    before = copy.deepcopy(task)
    wrong = adapter.admit_task(task, control)
    record = adapter.task_record(task_id)
    assert task == before and wrong != task["patch"]
    assert len(task["FAIL_TO_PASS"]) + len(task["PASS_TO_PASS"]) == count
    assert record["base_commit"] == task["base_commit"]
    assert task_id.replace("psf__requests-", "requests-") in record["official_base_image"]


@pytest.mark.parametrize("field,value", [("repo", "other/requests"), ("base_commit", "0" * 40),
                                        ("FAIL_TO_PASS", []), ("PASS_TO_PASS", []), ("patch", ""), ("test_patch", "")])
def test_source_patch_and_requirement_narrowing_cannot_be_admitted(field, value):
    task, control = task_control()
    with pytest.raises(ValueError):
        adapter.admit_task({**task, field: value}, control)


def test_same_size_regression_substitution_is_rejected():
    task, control = task_control()
    task["PASS_TO_PASS"][-1] = "tests/other.py::test_replacement"
    with pytest.raises(ValueError):
        adapter.admit_task(task, control)


@pytest.mark.parametrize("task_id", ["psf__requests-3362", "psf__requests-2674", "other"])
def test_unadmitted_other_task_cannot_select_a_worker(task_id):
    with pytest.raises(ValueError):
        adapter.task_record(task_id)


@pytest.mark.parametrize("field,value", [("gold_sha256", "0" * 64), ("old", ""), ("new", None), ("rationale", "")])
def test_negative_control_is_bound_to_reviewed_gold(field, value):
    task, control = task_control()
    with pytest.raises(ValueError):
        adapter.admit_task(task, {**control, field: value})


def certificates():
    return {path: "b" * 64 for path in adapter.service.CERTIFICATE_PATHS}


def test_full_native_source_keeps_assertions_conftest_and_starts_real_services_before_target_import():
    code = adapter.bootstrap_source(TASKS[0], {"pytest.py": "a" * 64}, certificates())
    ast.parse(code)
    assert code.index("import pytest") < code.index("import httpbin,pkg_resources") < code.index("import requests")
    assert "make_server('127.0.0.1',80,httpbin.app)" in code
    assert "make_server('127.0.0.1',443,httpbin.app)" in code
    assert "load_cert_chain(fixture+'cert.pem',fixture+'key.pem')" in code
    assert "HTTPBIN_URL']='http://httpbin.org/'" in code
    assert "REQUESTS_CA_BUNDLE']=fixture+'cacert.pem'" in code
    assert "pytest.main(argv,plugins=[recorder])" in code
    assert "['-rA','-v']" in code
    assert "--assert=plain" not in code and "--noconftest" not in code
    assert code.index("function_server.shutdown()") < code.index("P07_TARGET_RESULT=")
    assert code.count("P07_FUNCTION_SERVERS_STOPPED=1") == 1


def test_certificate_inventory_cannot_read_an_arbitrary_host_or_worker_path():
    with pytest.raises(ValueError):
        adapter.certificate_inventory_source({"/etc/shadow": "a" * 64})


def policy(task_id=TASKS[0]):
    return {"Image": "sha256:" + "a" * 64, "Mounts": [], "HostConfig": {
        "Binds": None, "Privileged": False, "NetworkMode": "none", "CapDrop": ["ALL"], "CapAdd": None,
        "SecurityOpt": ["no-new-privileges"], "Memory": 6 * 1024**3, "NanoCpus": 2_000_000_000, "PidsLimit": 256,
        "Sysctls": {"net.ipv4.ip_unprivileged_port_start": "0"},
        "ExtraHosts": [host + ":" + address for host, address in adapter.service.TASK_HOSTS[task_id].items()]}}


@pytest.mark.parametrize("field,value", [("NetworkMode", "host"), ("CapAdd", ["NET_ADMIN"]), ("Privileged", True),
                                        ("Binds", ["/:/host"]), ("Sysctls", {}), ("ExtraHosts", ["*:127.0.0.1"])])
def test_real_service_transport_cannot_widen_container_isolation(field, value):
    config = policy()
    adapter.inspect_policy(config, config["Image"], TASKS[0])
    config["HostConfig"][field] = value
    with pytest.raises(ValueError):
        adapter.inspect_policy(config, config["Image"], TASKS[0])


def test_off_host_redirect_alias_is_only_available_to_tasks_that_require_it():
    assert "www.google.co.uk:127.0.0.1" not in adapter.transport_arguments(TASKS[0])
    assert "www.google.co.uk:127.0.0.1" in adapter.transport_arguments(TASKS[2])
    config = policy(TASKS[2])
    with pytest.raises(ValueError):
        adapter.inspect_policy(config, config["Image"], TASKS[0])


def transcript(task, failure=False):
    ids = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    statuses = {node: "PASSED" for node in ids}
    if failure:
        statuses[ids[0]] = "FAILED"
    result = {"version_info": [3, 6, 15], "prefix": adapter.transplant.PREFIX,
              "executable": adapter.transplant.PREFIX + "/bin/python3.6", "pytest_version": "2.8.7",
              "pytest_module": adapter.common.health.PREFIX + "pytest.py", "requests_module": "/testbed/requests/__init__.py",
              "argv": [*adapter.common.health.PLUGIN_ARGS, "-rA", "-v"], "finished": True, "exit": int(failure),
              "collection": ids, "plugins": [["fixture", "pytest_httpbin.plugin"], ["mock", "pytest_mock"]],
              "reports": [{"id": node, "when": phase, "outcome": statuses[node].lower() if phase == "call" else "passed",
                           "has_wasxfail": False} for node in ids for phase in ("setup", "call", "teardown")]}
    lines = [node + " " + statuses[node] for node in ids]
    lines += ["P07_FUNCTION_SERVERS_STOPPED=1", adapter.common.RESULT + json.dumps(result), "P07_TEST_EXIT=" + str(int(failure))]
    return adapter.common.START + "\n" + "\n".join(lines) + "\n" + adapter.common.END, statuses


@pytest.mark.parametrize("failure", [False, True])
def test_full_function_receipt_uses_accepted_complete_identity_parser(monkeypatch, failure):
    task, _ = task_control()
    text, statuses = transcript(task, failure)
    opaque = {"tests/p07_identity.py::case_" + format(index, "06d"): statuses[node]
              for index, node in enumerate(sorted(statuses))}
    monkeypatch.setattr(adapter.oracle, "parse_observations", lambda *args: (opaque, {}))
    row = adapter.classify_target(task, text, int(failure), None, None)
    assert row["verified_success"] is not failure
    assert row["regressions_passed"] is True
    assert len(row["tests"]) == 64


def test_successful_tests_cannot_replace_real_server_shutdown_receipt():
    task, _ = task_control()
    text, _ = transcript(task)
    with pytest.raises(ValueError, match="shutdown"):
        adapter.classify_target(task, text.replace("P07_FUNCTION_SERVERS_STOPPED=1", ""), 0, None, None)


def test_an_extra_ordinary_failure_cannot_be_hidden_outside_manifest(monkeypatch):
    task, _ = task_control()
    text, statuses = transcript(task)
    result_line = next(line for line in text.splitlines() if line.startswith(adapter.common.RESULT))
    result = json.loads(result_line[len(adapter.common.RESULT):])
    extra = "tests/test_requests.py::RequestsTestSuite::test_allow_list_of_hooks_to_register_hook"
    result["collection"].append(extra)
    result["reports"].extend({"id": extra, "when": phase, "outcome": "failed" if phase == "call" else "passed",
                              "has_wasxfail": False} for phase in ("setup", "call", "teardown"))
    result["exit"] = 1
    text = text.replace(result_line, adapter.common.RESULT + json.dumps(result)).replace("P07_TEST_EXIT=0", "P07_TEST_EXIT=1")
    text = text.replace("P07_FUNCTION_SERVERS_STOPPED=1", extra + " FAILED\nP07_FUNCTION_SERVERS_STOPPED=1")
    statuses[extra] = "FAILED"
    opaque = {"tests/p07_identity.py::case_" + format(index, "06d"): statuses[node]
              for index, node in enumerate(sorted(statuses))}
    monkeypatch.setattr(adapter.oracle, "parse_observations", lambda *args: (opaque, {}))
    row = adapter.classify_target(task, text, 1, None, None)
    assert not row["verified_success"] and row["regressions_passed"] is False


def test_other_image_or_ancestry_is_not_accepted():
    image_id = "sha256:" + "a" * 64
    base = {"Id": "sha256:" + "b" * 64, "RootFS": {"Layers": ["base"]}}
    image = {"Id": image_id, "Os": "linux", "Architecture": "amd64", "RootFS": {"Layers": ["base", "private"]},
             "Config": {"Labels": {"task": "fixed"}}}
    admitted = {"image_id": image_id, "image_inspect": copy.deepcopy(image), "base_image_inspect": copy.deepcopy(base)}
    adapter.admit_image(image, base, admitted)
    with pytest.raises(ValueError):
        adapter.admit_image({**image, "RootFS": {"Layers": ["other"]}}, base, admitted)


def test_existing_output_refuses_before_any_remote_or_harness_call(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calibrate", "--runtime-receipt", "runtime", "--output", str(tmp_path)])
    monkeypatch.setattr(adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(adapter.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="fresh output"):
        adapter.main()


def test_default_ca_overlay_is_fixed_trust_material_not_python_or_test_edit():
    source = adapter.default_ca_overlay_source("a" * 64, "b" * 64)
    ast.parse(source)
    assert "/testbed/requests/cacert.pem" in source
    assert "/opt/p07-function-fixture/cacert.pem" in source
    assert "combined.startswith(original)" in source
    assert "verification_disabled':False" in source
    assert "import requests" not in source
    assert "verify=False" not in source


@pytest.mark.parametrize("bad", ["", "a" * 63, "G" * 64, "../path"])
def test_default_ca_overlay_refuses_unbound_material(bad):
    with pytest.raises(ValueError):
        adapter.default_ca_overlay_source(bad, "a" * 64)
