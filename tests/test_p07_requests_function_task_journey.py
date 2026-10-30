"""Offline attacks against the complete public function36 acceptance gate."""
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import test_check_requests_execution_overlay as core_fixture
import test_check_requests_function as api

from atomic_flow.check_execution import CheckReceipt
from atomic_flow.check_requests_execution_overlay import prepare_requests_execution_overlay


@pytest.fixture
def journey(monkeypatch):
    tools = Path(__file__).resolve().parents[1] / "tools"
    fixture = ModuleType("p07_requests_function_check_smoke")
    for name, value in {"MODULE": "requests/p07_function_probe.py", "TEST": "test_p07_function_fixture.py",
                        "ORDINARY": "# synthetic ordinary fixture\n", "ROOT": tools.parent,
                        "encoded": lambda v: json.dumps(v).encode(), "save": None, "sha": api.sha, "smoke": None}.items():
        setattr(fixture, name, value)
    observations = ModuleType("p07_sympy_task_journey")
    observations._observations = None
    monkeypatch.setitem(__import__("sys").modules, fixture.__name__, fixture)
    monkeypatch.setitem(__import__("sys").modules, observations.__name__, observations)
    loaded = importlib.util.spec_from_file_location("function_task_acceptance", tools / "p07_requests_function_task_journey.py")
    result = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(result)
    return result


def admitted(journey, tmp_path):
    candidate, destination, original = core_fixture.bound.__wrapped__(tmp_path)
    initial = api.seal(original)
    _, overlay = prepare_requests_execution_overlay(initial, candidate, destination, api.PUBLIC_CA,
                                                    original_ca_sha256=api.sha(api.ORIGINAL_CA))
    final = replace(initial, source_revision="d" * 64)
    raw = []
    for index, spec in enumerate((initial, final)):
        own_overlay = replace(overlay, candidate_core_revision=spec.source_revision,
                              execution_core_revision=spec.source_revision, check_spec_digest=spec.digest)
        value = api.raw_observation(spec, own_overlay, failed=index == 0)
        receipt = CheckReceipt(spec.digest, spec.source_revision, spec.source_revision, index == 1, value, api.packet(value))
        topology = SimpleNamespace(container_id=str(index + 1) * 64, nonce=api.NONCE, netns_inode=100 + index,
                                   host_netns_inode=200, image_id=spec.image, fixture_digest=spec.fixture_identity.digest)
        proof = {"candidate_ca_present": False, "candidate_and_execution_specs_equal": True,
                 "candidate_core_revision": spec.source_revision, "execution_core_revision": spec.source_revision,
                 "overlay": own_overlay.as_dict(), "resource_container": {
                     "container_id": str(index + 3) * 64, "image_id": spec.image, "started": False, "cleanup_confirmed": True}}
        raw.append((spec, receipt, topology, proof))
    obligation = SimpleNamespace(digest="e" * 64)
    failure = {"kind": "deterministic_test_failure", "evidence": {
        "version": 2, "check": "ordinary_test", "runner": "pytest", "outcome": "failed", "exit_code": 1,
        "step_index": 0, "started_at": 1.0, "source_revision": initial.source_revision,
        "checked_source_revision": initial.source_revision, "check_completed": True, "check_failed_completed": True,
        "check_spec_digest": initial.digest, "check_obligation_digest": obligation.digest, "check_receipt_digest": "f" * 64}}
    task = SimpleNamespace(status="done", replan_count=1, completion_report={
        "ready_to_publish": True, "check_passed": True, "check_completed": True, "check_runner": "pytest",
        "check_result_exit_code": 0, "checked_source_revision": final.source_revision, "check_obligation_digest": obligation.digest,
        "check_spec_digest": final.digest, "check_receipt_digest": api.sha(json.dumps(raw[-1][1].runner_receipt,
          sort_keys=True, separators=(",", ":"), allow_nan=False).encode())},
        graph_sync={"failed": False, "coverage_complete": True, "coverage_omissions": []})
    return task, dict(initial_spec=initial, obligation=obligation, final_revision=final.source_revision, changed=[journey.MODULE],
                      raw_checks=raw, failures=[failure], final_module=b"value = 42\n", final_test=journey.ORDINARY.encode())


def test_exact_function_resource_execution_and_publication_qualify(journey, tmp_path):
    task, values = admitted(journey, tmp_path)
    assert journey.validate(task, **values)


@pytest.mark.parametrize("attack", ["same_revision", "same_container", "resource_running", "wrong_resource_image",
                                   "mixed_overlay", "ca_in_candidate", "unapproved_change", "stale_failure", "not_published",
                                   "stale_final_receipt", "missing_source_cid"])
def test_stale_mixed_or_incomplete_resource_evidence_cannot_qualify(journey, tmp_path, attack):
    task, values = admitted(journey, tmp_path)
    if attack == "same_revision":
        values["final_revision"] = values["initial_spec"].source_revision
    elif attack == "same_container":
        values["raw_checks"][1][2].container_id = values["raw_checks"][0][2].container_id
    elif attack == "resource_running":
        values["raw_checks"][1][3]["resource_container"]["started"] = True
    elif attack == "wrong_resource_image":
        values["raw_checks"][1][3]["resource_container"]["image_id"] = "sha256:" + "f" * 64
    elif attack == "mixed_overlay":
        values["raw_checks"][1][3]["overlay"]["fixture_digest"] = "f" * 64
    elif attack == "ca_in_candidate":
        values["raw_checks"][1][3]["candidate_ca_present"] = True
    elif attack == "unapproved_change":
        values["changed"].append("requests/cacert.pem")
    elif attack == "stale_failure":
        values["failures"][0]["evidence"]["check_spec_digest"] = "f" * 64
    elif attack == "not_published":
        task.completion_report["ready_to_publish"] = False
    elif attack == "stale_final_receipt":
        task.completion_report["check_receipt_digest"] = "f" * 64
    elif attack == "missing_source_cid":
        values["raw_checks"][1][3]["resource_container"].pop("container_id")
    assert not journey.validate(task, **values)
