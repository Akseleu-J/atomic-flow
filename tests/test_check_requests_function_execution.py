"""Host resource separation attacks with fake worker; no target or Docker runs."""
from types import SimpleNamespace

import pytest
import test_check_requests_execution_overlay as core_fixture
import test_check_requests_function as api

from atomic_flow import check_requests_function_execution as host
from atomic_flow.agents.execution import ExecutionUnavailable
from atomic_flow.agents.source import SourceConflict
from atomic_flow.agents.verify import check_source_snapshot
from atomic_flow.check_execution import CheckReceipt, completed_check_receipt, run_check_spec


@pytest.fixture
def setup(tmp_path, monkeypatch):
    candidate, _, original = core_fixture.bound.__wrapped__(tmp_path)
    spec = api.seal(original)
    (candidate / "requests/cacert.pem").unlink()  # A normal staged source excludes PEM.
    monkeypatch.setattr(host, "platform", SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(host, "os", SimpleNamespace(geteuid=lambda: 0, fsync=host.os.fsync))
    state = SimpleNamespace(spec=spec, candidate=candidate, executions=[], attack=None)
    monkeypatch.setattr(host, "_public_resources", lambda *args: (api.ORIGINAL_CA, api.PUBLIC_CA,
                        {"container_id": "a" * 64, "cleanup_confirmed": True}))

    def run(spec, execution, *, cancel_check, _function_overlay):
        assert not (candidate / "requests/cacert.pem").exists()
        assert (execution / "requests/cacert.pem").read_bytes() == api.ORIGINAL_CA + b"\n" + api.PUBLIC_CA
        assert not (execution / ".env").exists() and not (execution / "requests/private-key.pem").exists()
        assert check_source_snapshot(execution) == check_source_snapshot(candidate)
        state.executions.append(execution)
        if state.attack == "candidate":
            (candidate / "requests/api.py").write_bytes(b"changed during execution\n")
        elif state.attack == "insert_ca":
            (candidate / "requests/cacert.pem").write_bytes(api.ORIGINAL_CA)
        elif state.attack == "execution_ca":
            (execution / "requests/cacert.pem").write_bytes(api.ORIGINAL_CA)
        elif state.attack == "extra_resource":
            (execution / "unadmitted.pem").write_bytes(b"must refuse")
        value = api.raw_observation(spec, _function_overlay)
        return CheckReceipt(spec.digest, spec.source_revision, spec.source_revision, True, value, api.packet(value)), SimpleNamespace()

    monkeypatch.setattr(host.worker, "_run_requests_check", run)
    return state


def test_candidate_stays_without_ca_while_execution_has_exact_append_and_core(setup):
    before = check_source_snapshot(setup.candidate)
    receipt, _, proof = host.run_function36_check(setup.spec, setup.candidate)
    assert receipt.check_passed
    assert proof["candidate_ca_present"] is False
    assert proof["candidate_and_execution_specs_equal"] is True
    assert proof["overlay"]["candidate_core_revision"] == setup.spec.source_revision
    assert proof["overlay"]["execution_core_revision"] == setup.spec.source_revision
    assert proof["overlay"]["candidate_revision"] != proof["overlay"]["execution_revision"]
    assert check_source_snapshot(setup.candidate) == before
    assert not (setup.candidate / "requests/cacert.pem").exists()


@pytest.mark.parametrize("attack", ["candidate", "insert_ca", "execution_ca", "extra_resource"])
def test_candidate_or_execution_resource_mutation_refuses_completion(setup, attack):
    setup.attack = attack
    with pytest.raises((ExecutionUnavailable, SourceConflict)):
        host.run_function36_check(setup.spec, setup.candidate)
    assert len(setup.executions) == 1


def test_inserted_ca_refuses_even_when_core_source_hash_does_not_change(setup):
    (setup.candidate / "requests/cacert.pem").write_bytes(api.ORIGINAL_CA)
    with pytest.raises(ExecutionUnavailable, match="must not contain"):
        host.run_function36_check(setup.spec, setup.candidate)
    assert not setup.executions


def test_preexisting_cancellation_starts_no_resource_or_target_execution(setup):
    def cancelled():
        raise RuntimeError("synthetic cancellation")

    with pytest.raises(RuntimeError, match="synthetic cancellation"):
        host.run_function36_check(setup.spec, setup.candidate, cancel_check=cancelled)
    assert not setup.executions


def test_generic_dispatch_keeps_exact_resource_receipt_for_revalidation(setup):
    checked = run_check_spec(setup.spec, setup.candidate)
    assert checked.check_passed and completed_check_receipt(setup.spec, checked)
    checked.runner_receipt["requests_function_fixture"]["servers_stopped"] = False
    assert not completed_check_receipt(setup.spec, checked)
