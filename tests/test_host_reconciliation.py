"""Host reconciliation, job payload GC and executor manifests (audit RT-02/W4 groundwork).

Covers: the host-only `host_reconciliation` session event (validated, scoped, clears
exactly one of charge/effect), journal tamper detection on the incremental append path,
JobStore.collect() tombstones, and execution_manifest capability refusal.
"""
import time

import pytest

from atomic_flow.agents.execution import ExecutionConfig, ExecutionRequirements, ExecutionUnavailable, execution_manifest
from atomic_flow.job_worker import run_worker
from atomic_flow.jobs import TERMINAL, JobStore, _atomic
from atomic_flow.sessions import SessionJournal
from atomic_flow.state import StateError

EVIDENCE = "e" * 64


def reconciliation(scope):
    return {"scope": scope, "reconciliation_id": "reconcile_" + "a" * 64,
            "evidence_sha256": EVIDENCE, "actor": "host-owner", "reason": "provider dashboard confirmed no charge"}


# --- host reconciliation observations ------------------------------------------


def test_reconciliation_event_is_validated(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("model_request", {"role": "answerer"}, call_id="model_1")
    bad_payloads = [
        {"scope": "charges"},                                        # unknown scope
        {"scope": "charge", "reconciliation_id": "not-an-id", "evidence_sha256": EVIDENCE, "actor": "h", "reason": "r"},
        {"scope": "charge", "reconciliation_id": "reconcile_" + "a" * 64, "evidence_sha256": "xyz", "actor": "h", "reason": "r"},
        {"scope": "charge", "reconciliation_id": "reconcile_" + "a" * 64, "evidence_sha256": EVIDENCE},  # no actor/reason
    ]
    for payload in bad_payloads:
        with pytest.raises(StateError, match="invalid host reconciliation"):
            journal.append("host_reconciliation", payload, call_id="model_1")
    journal.append("host_reconciliation", reconciliation("charge"), call_id="model_1")
    snapshot = journal.read()
    assert "model_1" not in snapshot.unresolved_call_ids
    assert "model_1" not in snapshot.pending_call_ids


def test_charge_and_effect_reconciliation_are_independent(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("model_request", {"role": "answerer"}, call_id="model_1")
    journal.append("tool_request", {"tool": "write"}, call_id="write.1")
    journal.append("tool_effect_unknown", {"reason": "timeout"}, call_id="write.1")
    snapshot = journal.read()
    assert set(snapshot.unresolved_call_ids) == {"model_1", "write.1"}

    journal.append("host_reconciliation", reconciliation("charge"), call_id="model_1")
    assert journal.read().unresolved_call_ids == ["write.1"]  # settling cost never clears an effect hold

    journal.append("host_reconciliation", reconciliation("effect"), call_id="write.1")
    assert journal.read().unresolved_call_ids == []


def test_reconciliation_never_clears_an_unrelated_call(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("model_request", {"role": "answerer"}, call_id="model_1")
    journal.append("model_failed", {"error_type": "TimeoutError"}, call_id="model_2", outcome="unknown")
    journal.append("host_reconciliation", reconciliation("charge"), call_id="model_1")
    assert journal.read().unresolved_call_ids == ["model_2"]


# --- journal integrity on the incremental append path --------------------------


def test_same_size_prefix_edit_is_detected_before_append(tmp_path):
    journal = SessionJournal.create(tmp_path)
    for index in range(3):
        journal.append("question", {"text": f"question {index}"}, call_id=f"q{index}")
    raw = journal.path.read_bytes()
    journal.path.write_bytes(raw.replace(b"question 0", b"question X"))  # same length tamper
    with pytest.raises(StateError, match="validated session prefix changed"):
        journal.append("completed", {})
    journal.path.write_bytes(raw)
    journal.append("completed", {})  # restored bytes append normally


def test_incremental_path_still_rejects_interrupted_tail(tmp_path):
    journal = SessionJournal.create(tmp_path)
    journal.append("question", {"text": "q"}, call_id="q1")
    with journal.path.open("ab") as handle:
        handle.write(b'{"event_type":"completed"}')  # syntactically complete, not committed
    with pytest.raises(StateError, match="interrupted tail"):
        journal.append("completed", {})
    journal.recover_tail()
    journal.append("completed", {})
    assert not journal.read().incomplete_tail


# --- JobStore.collect tombstones ------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.py").write_text("print('source')", encoding="utf-8")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "state"))
    return JobStore(project)


def wait(store, identifier, timeout=60):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = store.status(identifier)
        if state["status"] in TERMINAL:
            return state
        time.sleep(0.05)
    pytest.fail(f"worker did not finish: {state}")


def finished_job(store, *, retention_seconds=1):
    job = store.start(["-c", "pass"], config=ExecutionConfig(mode="local"), retention_seconds=retention_seconds)
    wait(store, job["id"])
    return job


def test_collect_expired_terminal_jobs_leaves_readable_tombstones(store):
    job = finished_job(store)
    directory = store._directory(job["id"])
    assert (directory / "workspace").exists()
    time.sleep(1.1)
    assert store.collect() == [job["id"]]
    state = store.status(job["id"])
    assert state["status"] == "succeeded"            # identity and outcome survive collection
    assert state["payload_collected"] and state["output_expired"] and not state["output_complete"]
    assert not (directory / "workspace").exists() and not (directory / "stdout.bin").exists()
    assert store.collect() == []                     # idempotent


def test_collect_skips_unexpired_pending_and_uncertain_jobs(store, monkeypatch):
    fresh = finished_job(store, retention_seconds=3600)
    pending = store.start(["-c", "pass"], config=ExecutionConfig(mode="local"))
    uncertain = store.start(["-c", "pass"], config=ExecutionConfig(mode="local"), retention_seconds=1)

    def failed_execution(*args, **kwargs):
        raise OSError("simulated child cleanup timeout")

    monkeypatch.setattr("atomic_flow.job_worker.run_python", failed_execution)
    run_worker(store, uncertain["id"])
    wait(store, uncertain["id"])
    time.sleep(1.1)
    assert store.collect() == []                     # nothing eligible: fresh/pending/uncertain
    assert store.status(fresh["id"])["status"] == "succeeded"
    assert (store._directory(pending["id"]) / "workspace").exists()


def test_collect_never_touches_a_job_with_an_active_worker(store):
    from atomic_flow.locking import exclusive_file

    job = finished_job(store, retention_seconds=1)
    time.sleep(1.1)
    with exclusive_file(store._directory(job["id"]) / "worker.lock", blocking=True):
        assert store.collect() == []
        assert (store._directory(job["id"]) / "workspace").exists()
    assert store.collect() == [job["id"]]


# --- execution manifests (W4 groundwork) ----------------------------------------


def test_execution_manifest_describes_and_refuses():
    local = ExecutionConfig(mode="local")
    manifest = execution_manifest(local)
    assert manifest["executor"] == "trusted-local" and manifest["profile"] == "checks"
    assert manifest["capabilities"]["filesystem_isolation"] is False
    assert manifest["digest"] == execution_manifest(local)["digest"]

    with pytest.raises(ExecutionUnavailable, match="requires Docker"):
        execution_manifest(ExecutionConfig(mode="local", profile="read-only"))
    with pytest.raises(ExecutionUnavailable, match="Executor lacks"):
        execution_manifest(local, ExecutionRequirements(filesystem_isolation=True))
    with pytest.raises(ExecutionUnavailable, match="host-defined"):
        execution_manifest(local, "network_isolation")  # type: ignore[arg-type]

    docker = ExecutionConfig(mode="docker", image="repo/img@sha256:" + "0" * 64)
    described = execution_manifest(docker)
    assert described["executor"] == "docker" and described["capabilities"]["filesystem_isolation"] is True
    assert described["runtime_immutable"] is True


def test_worker_refuses_changed_execution_identity(store):
    job = store.start(["-c", "print('never-executed-marker')"], config=ExecutionConfig(mode="local"))
    path = store._directory(job["id"]) / "state.json"
    state = store._read(job["id"])
    state["resolved_execution"] = dict(state["resolved_execution"], digest="tampered")
    _atomic(path, state)
    run_worker(store, job["id"])
    finished = wait(store, job["id"])
    # The persisted error is deliberately generic; the refusal point is proven by the
    # marker never executing and no uncertain effects being claimed.
    assert finished["status"] == "failed" and finished["error"]
    assert finished["returncode"] is None and not finished["effects_unknown"]
    assert (store._directory(job["id"]) / "stdout.bin").read_bytes() == b""
