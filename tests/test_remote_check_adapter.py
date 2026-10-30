"""Standalone check bridge: fixed authority, immutable source, no replay/false oracle PASS."""
import base64
import copy
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.remote import RemoteExecutor, RemoteOutcomeUnknown, SourceBundle, canonical, sha256
from atomic_flow.remote_check_adapter import PYTEST_ARGS, RemoteCheckAdapter, RemoteCheckRequest

IMAGE = "sha256:" + "a" * 64


class Worker:
    """Protocol-faithful transport; no target code executes in these tests."""

    def __init__(self):
        self.calls = []
        self.bundle = None
        self.receipt = None
        self.lose_ack = False
        self.state = "succeeded"
        self.code = 0
        self.effects = False
        self.cleanup = True
        self.output_complete = True
        self.tamper = None

    def call(self, action, payload):
        self.calls.append(action)
        if action == "submit":
            self.bundle = SourceBundle.model_validate(payload["bundle"])
            self.bundle.checked_files()
            self.receipt = {"version": 1, "request_id": self.bundle.request_id,
                "bundle_hash": self.bundle.digest, "source_hash": self.bundle.source_hash,
                "image": self.bundle.image, "job_id": "job_" + "b" * 24}
            if self.lose_ack:
                raise RemoteOutcomeUnknown("sent but ack lost")
            return copy.deepcopy(self.receipt)
        if action == "lookup":
            return copy.deepcopy(self.receipt)
        assert action == "status" and payload["receipt"] == self.receipt
        status = {"id": self.receipt["job_id"], "source_hashes": {f.path: f.sha256 for f in self.bundle.files},
            "status": self.state, "returncode": self.code, "effects_unknown": self.effects,
            "output_complete": self.output_complete, "cleanup_confirmed": self.cleanup,
            "output_expired": False, "discarded_bytes": 0}
        if self.tamper == "source":
            status["source_hashes"] = {}
        result = {"receipt": copy.deepcopy(self.receipt), "status": status, "result_hash": sha256(canonical(status))}
        if self.tamper == "hash":
            result["result_hash"] = "0" * 64
        return result


def prepare(tmp_path):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "module.py").write_bytes(b"def answer():\n    return 42\n")
    (source / "tests.py").write_bytes(b"def test_answer():\n    assert True\n")
    return RemoteCheckRequest.prepare(source, ["module.py", "tests.py"], image=IMAGE)


def test_fixed_request_is_immutable_and_rejects_command_or_source_substitution(tmp_path):
    request = prepare(tmp_path)
    assert tuple(request.bundle().args) == PYTEST_ARGS
    assert request.source_is_current()
    with pytest.raises(FrozenInstanceError):
        request.check = "shell"
    for args in [["-c", "import os"], ["-m", "pytest", "; curl evil"], [*PYTEST_ARGS, "--override-ini=x"]]:
        data = request.bundle().model_dump()
        data["args"] = args
        with pytest.raises(ValueError, match="command/profile"):
            RemoteCheckRequest(request.source_root, canonical(data))
    data = request.bundle().model_dump()
    data["files"][0]["content"] = base64.b64encode(b"different bytes").decode()
    with pytest.raises(ValueError, match="hash mismatch"):
        RemoteCheckRequest(request.source_root, canonical(data))


def test_source_change_before_submit_never_calls_worker(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    (tmp_path / "source/module.py").write_bytes(b"changed = True\n")
    with pytest.raises(ValueError, match="source changed"):
        adapter.submit(request)
    assert worker.calls == []


def test_result_binds_original_source_image_and_command_but_does_not_claim_oracle(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    ticket = adapter.submit(request)
    result = adapter.observe(request, ticket)
    assert result.status == "process_passed" and result.returncode == 0
    assert result.request_digest == request.binding_digest
    assert result.source_hash == request.bundle().source_hash and result.image == IMAGE
    assert result.source_current and result.verified_success is False
    assert result.test_case_completeness == result.semantic_correctness == "not_assessed"
    with pytest.raises(ValueError, match="fabricate"):
        replace(result, verified_success=True)
    assert adapter.submit(request) == ticket  # Known submitted record does not resend.
    assert worker.calls.count("submit") == 1


def test_lost_submit_is_durable_global_hold_and_reconciliation_never_reexecutes(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    worker.lose_ack = True
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    with pytest.raises(RemoteOutcomeUnknown):
        adapter.submit(request)
    fresh = RemoteCheckRequest.prepare(tmp_path / "source", ["module.py", "tests.py"], image=IMAGE)
    with pytest.raises(RemoteOutcomeUnknown, match="another check"):
        adapter.submit(fresh)
    reopened = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    with pytest.raises(RemoteOutcomeUnknown, match="reconcile"):
        reopened.submit(request)
    ticket = reopened.reconcile(request)
    with pytest.raises(RemoteOutcomeUnknown, match="another check"):
        reopened.submit(fresh)  # Lookup ack alone cannot clear effect uncertainty.
    assert reopened.observe(request, ticket).status == "process_passed"
    assert worker.calls.count("submit") == 1 and worker.calls.count("lookup") == 1


@pytest.mark.parametrize("state,code,cleanup,complete,expected", [
    ("running", None, False, False, "pending"),
    ("failed", 1, True, True, "process_failed"),
    ("succeeded", 0, True, False, "infrastructure_error"),
    ("succeeded", True, True, True, "infrastructure_error"),
    ("succeeded", 1, True, True, "infrastructure_error"),
    ("timed_out", 1, True, True, "infrastructure_error"),
])
def test_terminal_process_and_cleanup_are_strict_not_test_completeness(tmp_path, state, code, cleanup, complete, expected):
    request = prepare(tmp_path)
    worker = Worker()
    worker.state, worker.code, worker.cleanup, worker.output_complete = state, code, cleanup, complete
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    result = adapter(request)
    assert result.status == expected and result.verified_success is False


@pytest.mark.parametrize("tamper", ["source", "hash"])
def test_tampered_worker_receipt_cannot_be_accepted_and_holds_next_admission(tmp_path, tamper):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    ticket = adapter.submit(request)
    worker.tamper = tamper
    with pytest.raises(ValueError):
        adapter.observe(request, ticket)
    fresh = RemoteCheckRequest.prepare(tmp_path / "source", ["module.py", "tests.py"], image=IMAGE)
    with pytest.raises(RemoteOutcomeUnknown, match="another check"):
        adapter.submit(fresh)
    assert worker.calls.count("submit") == 1


def test_unknown_effects_are_not_cleared_by_read_only_receipt_lookup(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    ticket = adapter.submit(request)
    worker.effects = True
    with pytest.raises(RemoteOutcomeUnknown, match="effects"):
        adapter.observe(request, ticket)
    ticket = adapter.reconcile(request)
    with pytest.raises(RemoteOutcomeUnknown, match="effects"):
        adapter.observe(request, ticket)
    assert worker.calls.count("submit") == 1


def test_terminal_unconfirmed_cleanup_blocks_more_admission(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    ticket = adapter.submit(request)
    worker.cleanup = False
    with pytest.raises(RemoteOutcomeUnknown, match="cleanup"):
        adapter.observe(request, ticket)
    fresh = RemoteCheckRequest.prepare(tmp_path / "source", ["module.py", "tests.py"], image=IMAGE)
    with pytest.raises(RemoteOutcomeUnknown, match="another check"):
        adapter.submit(fresh)


def test_source_changed_after_execution_never_matches_current_revision(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    ticket = adapter.submit(request)
    (tmp_path / "source/module.py").write_bytes(b"changed = True\n")
    result = adapter.observe(request, ticket)
    assert not result.source_current and result.status == "infrastructure_error"
    assert result.verified_success is False


def test_65_file_request_is_refused_before_any_network(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    paths = []
    for index in range(65):
        path = "file" + str(index) + ".py"
        (source / path).write_bytes(b"x = 1\n")
        paths.append(path)
    with pytest.raises(ValueError, match="1 and 64"):
        RemoteCheckRequest.prepare(source, paths, image=IMAGE)


def test_journal_binding_tamper_is_rejected_without_resend(tmp_path):
    request = prepare(tmp_path)
    worker = Worker()
    adapter = RemoteCheckAdapter(RemoteExecutor(worker), tmp_path / "journal")
    adapter.submit(request)
    path = next((tmp_path / "journal").glob("*.json"))
    data = json.loads(path.read_bytes())
    data["bundle_hash"] = "0" * 64
    path.write_bytes(canonical(data))
    with pytest.raises(ValueError, match="binding"):
        adapter.submit(request)
    assert worker.calls.count("submit") == 1
