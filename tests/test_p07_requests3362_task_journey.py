"""Offline acceptance attacks against retained Requests TaskRunner evidence."""
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from test_check_requests3362 import NONCE, receipt, result, spec

from atomic_flow.check_execution import CheckReceipt


@pytest.fixture
def journey(monkeypatch):
    # Import only the acceptance function, without optional provisioner stacks.
    tools = Path(__file__).resolve().parents[1] / "tools"
    synthetic = ModuleType("p07_requests3362_check_smoke")
    for name, value in {"MODULE_PATH": "requests/p07_private_probe.py", "ORDINARY_SOURCE": "# synthetic\n",
                        "ROOT": tools.parent, "TEST_PATH": "test_p07_private_fixture.py",
                        "WRONG_CA": "p07_private_wrongca.pem", "save": None, "smoke": None}.items():
        setattr(synthetic, name, value)
    observations = ModuleType("p07_sympy_task_journey")
    observations._observations = None
    monkeypatch.setitem(__import__("sys").modules, synthetic.__name__, synthetic)
    monkeypatch.setitem(__import__("sys").modules, observations.__name__, observations)
    loaded = importlib.util.spec_from_file_location("requests_task_acceptance", tools / "p07_requests3362_task_journey.py")
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    return module


def admitted(journey):
    before = spec()
    after = replace(before, source_revision="b" * 64)
    obligation = SimpleNamespace(digest="d" * 64)
    checks = []
    for index, current in enumerate((before, after)):
        value = receipt(current, failed=index == 0)
        raw = CheckReceipt(current.digest, current.source_revision, current.source_revision, index == 1, value, result(value))
        topology = SimpleNamespace(container_id=str(index) * 64, host_netns_inode=100, netns_inode=200 + index,
                                   nonce=NONCE, image_id=current.image, fixture_digest=current.fixture_identity.digest)
        checks.append((current, raw, topology))
    failure = {"kind": "deterministic_test_failure", "evidence": {
        "version": 2, "check": "ordinary_test", "runner": "pytest", "exit_code": 1,
        "step_index": 0, "started_at": 1.0, "outcome": "failed", "source_revision": before.source_revision,
        "checked_source_revision": before.source_revision, "check_completed": True, "check_failed_completed": True,
        "check_obligation_digest": obligation.digest, "check_spec_digest": before.digest, "check_receipt_digest": "e" * 64}}
    task = SimpleNamespace(status="done", replan_count=1,
        completion_report={"ready_to_publish": True, "check_passed": True, "check_completed": True,
                           "check_result_exit_code": 0, "check_runner": "pytest", "checked_source_revision": after.source_revision,
                           "check_obligation_digest": obligation.digest},
        graph_sync={"failed": False, "coverage_complete": True, "coverage_omissions": []})
    return task, dict(before_spec=before, obligation=obligation, final_revision=after.source_revision,
                      changed=[journey.MODULE_PATH], failures=[failure], raw_checks=checks,
                      final_module=b"value = 42\n", final_test=journey.JOURNEY_TEST_SOURCE.encode())


def test_exact_raw_receipts_and_scoped_publication_qualify(journey):
    task, values = admitted(journey)
    assert journey.validate_journey(task, **values)


def test_unknown_inventory_mode_refuses_before_source_or_executor_access(journey, tmp_path):
    with pytest.raises(ValueError, match="ordinary inventory mode"):
        journey.journey(tmp_path, tmp_path, tmp_path / "unused", "selected-hidden-tests")
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("attack", ["same_container", "wrong_nonce", "wrong_fixture", "source_changed",
                                   "servers_running", "missing_negative", "hidden_test_edit", "stale_failure",
                                   "incomplete_graph", "not_published", "missing_receipt"])
def test_partial_or_substituted_evidence_cannot_qualify(journey, attack):
    task, values = admitted(journey)
    if attack == "same_container":
        values["raw_checks"][1][2].container_id = values["raw_checks"][0][2].container_id
    elif attack == "wrong_nonce":
        values["raw_checks"][1][2].nonce = "f" * 32
    elif attack == "wrong_fixture":
        values["raw_checks"][1][2].fixture_digest = "f" * 64
    elif attack == "source_changed":
        current, raw, topology = values["raw_checks"][1]
        values["raw_checks"][1] = (current, replace(raw, source_after="f" * 64), topology)
    elif attack == "servers_running":
        values["raw_checks"][1][1].runner_receipt["requests3362_fixture"]["servers_stopped"] = False
    elif attack == "missing_negative":
        values["raw_checks"].pop(0)
    elif attack == "hidden_test_edit":
        values["changed"].append(journey.TEST_PATH)
    elif attack == "stale_failure":
        values["failures"][0]["evidence"]["source_revision"] = "f" * 64
    elif attack == "incomplete_graph":
        task.graph_sync["coverage_complete"] = False
    elif attack == "not_published":
        task.completion_report["ready_to_publish"] = False
    elif attack == "missing_receipt":
        current, raw, topology = values["raw_checks"][1]
        values["raw_checks"][1] = (current, replace(raw, runner_receipt=None), topology)
    assert not journey.validate_journey(task, **values)
