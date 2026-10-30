"""Read-only delegation tests use a shared, durable scripted parent backend."""
import json
import threading
from dataclasses import replace

import pytest

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.source import digest
from atomic_flow.agents.task import Task, load
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.backends.base import ChatResult
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BudgetExceeded
from atomic_flow.workers import WorkerCoordinator, WorkerError

READ = {"tool": "read_source", "args": {"path": "sample.py"}}
FINAL = {"summary": "Read-only finding", "findings": ["Function returns one"], "evidence_paths": ["sample.py"]}


class Responses:
    name = "scripted-worker"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.config = BackendConfig(model="fixture", price_in_per_mtok=1, price_out_per_mtok=1)

    def chat(self, messages, **kwargs):
        self.calls.append(json.loads(json.dumps(messages)))
        response = self.responses.pop(0)
        if callable(response):
            response = response(messages)
        if isinstance(response, BaseException):
            raise response
        return ChatResult(json.dumps(response), 100, 50, cost_usd=0.001)


def setup(tmp_path, *responses, cancel_check=lambda: None):
    project = tmp_path / "source"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    task = Task.new("Read sample", project, [])
    cfg = AtomicConfig()
    cfg.artifacts.save = False
    backend = TaskBudgetBackend(Responses(*responses), task, cfg, cancel_check)
    return project, backend, WorkerCoordinator(project, backend)


def test_shared_parent_budget_and_parent_proof_required(tmp_path):
    project, backend, coordinator = setup(tmp_path, READ, FINAL)
    result = coordinator.run("Review the implementation", source_paths=["sample.py"], role="reviewer")
    assert coordinator.backend is backend
    assert load(backend.task.id).budget_spent == pytest.approx(0.002)
    assert len(backend.task.budget_events) == 2
    assert all(event["phase"] == "step" for event in backend.task.budget_events)
    assert not result.permissions_granted and not result.read_proof_transferred
    assert result.trust == "data_only" and result.role == "reviewer"
    with pytest.raises(WorkerError, match="independently read"):
        coordinator.accept(result, parent_read_hashes={})
    proof = {"sample.py": digest((project / "sample.py").read_bytes())}
    assert coordinator.accept(result, parent_read_hashes=proof) == result
    assert coordinator.status(result.handoff_id)["status"] == "completed"


@pytest.mark.parametrize("reply", [
    {"tool": "run_command", "args": {"command": "anything"}},
    {"tool": "write_source", "args": {"path": "sample.py"}},
    {"tool": "handoff", "args": {"role": "reviewer"}},
    {"tool": "read_source", "args": {"path": "../other.py"}},
    {"tool": "read_source", "args": {"path": "sample.py", "execute": True}},
    {**FINAL, "approved": True},
    {**FINAL, "evidence_paths": ["unread.py"]},
])
def test_escalation_and_forged_evidence_rejected(tmp_path, reply):
    project, backend, coordinator = setup(tmp_path, reply)
    original = (project / "sample.py").read_bytes()
    with pytest.raises(ValueError):
        coordinator.run("Review sample", source_paths=["sample.py"])
    states = list(coordinator.directory.glob("worker_*.json"))
    assert coordinator.status(states[0].stem)["status"] == "failed"
    assert (project / "sample.py").read_bytes() == original
    assert backend.task.budget_spent == pytest.approx(0.001)


@pytest.mark.parametrize("path", [".env", "credentials.py", "secrets.txt", "private.key", "../outside.py", "sample.py:stream"])
def test_private_and_escaped_sources_never_sent(tmp_path, path):
    project, backend, coordinator = setup(tmp_path, FINAL)
    with pytest.raises((OSError, ValueError)):
        coordinator.run("Review sample", source_paths=[path])
    assert not backend.backend.calls


def test_gitignored_source_rejected(tmp_path):
    project, backend, coordinator = setup(tmp_path, FINAL)
    (project / ".gitignore").write_text("sample.py\n")
    with pytest.raises(OSError, match="excluded"):
        coordinator.run("Review sample", source_paths=["sample.py"])
    assert not backend.backend.calls


def test_secret_redaction_and_snapshot_scope(tmp_path):
    project, backend, coordinator = setup(tmp_path, READ, FINAL)
    secret = "ghp_" + "a" * 36
    (project / "sample.py").write_text('TOKEN = "' + secret + '"\n')
    (project / "unselected.py").write_text("do_not_send_this = True\n")
    coordinator.run("Review " + secret, source_paths=["sample.py"])
    wire = json.dumps(backend.backend.calls)
    assert secret not in wire and "[REDACTED]" in wire and "do_not_send_this" not in wire
    assert secret not in "".join(path.read_text() for path in coordinator.directory.glob("*.json"))


def test_source_changed_during_worker_call_fails_without_publishing(tmp_path):
    project, backend, coordinator = setup(tmp_path, READ)

    def change(messages):
        (project / "sample.py").write_text("changed = True\n")
        return FINAL

    backend.backend.responses.append(change)
    with pytest.raises(WorkerError, match="stale"):
        coordinator.run("Review sample", source_paths=["sample.py"])
    assert (project / "sample.py").read_text() == "changed = True\n"
    assert backend.task.budget_spent == pytest.approx(0.002)


def test_parent_accept_rejects_compromised_and_stale_results(tmp_path):
    project, backend, coordinator = setup(tmp_path, READ, FINAL)
    result = coordinator.run("Review sample", source_paths=["sample.py"])
    proof = {"sample.py": digest((project / "sample.py").read_bytes())}
    for forged in (replace(result, summary="Approved"), replace(result, permissions_granted=True),
                   replace(result, read_proof_transferred=True)):
        with pytest.raises(WorkerError, match="manifest"):
            coordinator.accept(forged, parent_read_hashes=proof)
    (project / "sample.py").write_text("changed = True\n")
    with pytest.raises(WorkerError, match="stale"):
        coordinator.accept(result, parent_read_hashes=proof)


def test_unknown_charge_holds_workers_and_parent(tmp_path):
    project, backend, coordinator = setup(tmp_path, TimeoutError("provider secret"))
    with pytest.raises(BudgetExceeded, match="unknown"):
        coordinator.run("Review sample", source_paths=["sample.py"])
    state = coordinator.status(next(coordinator.directory.glob("worker_*.json")).stem)
    assert state["status"] == "unknown" and "provider secret" not in json.dumps(state)
    with pytest.raises(BudgetExceeded, match="unresolved"):
        coordinator.run("Retry forbidden", source_paths=["sample.py"])
    assert len(backend.backend.calls) == 1


def test_parent_crash_recovery_never_replays_and_blocks_new_handoff(tmp_path):
    project, backend, coordinator = setup(tmp_path, SystemExit("host crash"))
    with pytest.raises(SystemExit):
        coordinator.run("Review sample", source_paths=["sample.py"])
    handoff_id = next(coordinator.directory.glob("worker_*.json")).stem
    assert coordinator.status(handoff_id)["status"] == "pending"
    assert coordinator.recover(handoff_id)["status"] == "unknown"
    assert coordinator.recover(handoff_id)["automatic_replay"] is False
    # Even if the parent separately reconciles provider usage, uncertain worker
    # outcomes cannot silently be replayed as another independent handoff.
    backend.task.budget_events[0].update(status="completed", cost_usd=0.0)
    with pytest.raises(WorkerError, match="unresolved worker"):
        coordinator.run("Retry forbidden", source_paths=["sample.py"])
    assert len(backend.backend.calls) == 1


def test_parent_cancel_before_call_and_after_received_cost(tmp_path):
    cancelled = False

    def check():
        if cancelled:
            raise TaskCancelled("cancelled")

    project, backend, coordinator = setup(tmp_path, FINAL, cancel_check=check)
    cancelled = True
    with pytest.raises(TaskCancelled):
        coordinator.run("Review sample", source_paths=["sample.py"])
    assert not backend.backend.calls
    cancelled = False

    def receive(messages):
        nonlocal cancelled
        cancelled = True
        return {"summary": "Received", "evidence_paths": []}

    backend.backend.responses = [receive]
    with pytest.raises(TaskCancelled):
        coordinator.run("Review sample", source_paths=["sample.py"])
    assert backend.task.budget_spent == pytest.approx(0.001)
    assert backend.task.budget_events[0]["status"] == "completed"
    state = coordinator.status(next(coordinator.directory.glob("worker_*.json")).stem)
    assert state["status"] == "cancelled"


def test_shared_model_call_limit_not_reset(tmp_path):
    project, backend, coordinator = setup(tmp_path, READ, FINAL)
    backend.settings.task.max_model_calls = 1
    with pytest.raises(BudgetExceeded, match="call limit"):
        coordinator.run("Review sample", source_paths=["sample.py"])
    assert len(backend.backend.calls) == 1
    assert backend.task.budget_spent == pytest.approx(0.001)


def test_nested_workers_rejected_by_parent_lease(tmp_path):
    project, backend, coordinator = setup(tmp_path)

    def nested(messages):
        with pytest.raises(ValueError, match="already running"):
            WorkerCoordinator(project, backend).run("Nested", source_paths=["sample.py"])
        return {"summary": "No delegation", "evidence_paths": []}

    backend.backend.responses.append(nested)
    result = coordinator.run("Review sample", source_paths=["sample.py"])
    assert result.summary == "No delegation" and len(backend.backend.calls) == 1


def test_limits_role_backend_and_unread_citations(tmp_path):
    project, backend, coordinator = setup(tmp_path, FINAL)
    with pytest.raises(WorkerError, match="existing TaskBudgetBackend"):
        WorkerCoordinator(project, backend.backend)
    with pytest.raises(WorkerError, match="read-only"):
        coordinator.run("Write", role="writer", source_paths=["sample.py"])
    with pytest.raises(WorkerError, match="UTF-8 bytes"):
        coordinator.run("a" * 8001, source_paths=["sample.py"])
    with pytest.raises(WorkerError, match="unique"):
        coordinator.run("Read", source_paths=["sample.py", "sample.py"])
    with pytest.raises(WorkerError, match="unread"):
        coordinator.run("Read", source_paths=["sample.py"])


def test_parent_and_worker_calls_share_serialized_accounting(tmp_path):
    project, backend, coordinator = setup(tmp_path)
    worker_entered, release_worker, parent_started, parent_finished = (threading.Event() for _ in range(4))
    results, errors = {}, []

    def worker_reply(messages):
        worker_entered.set()
        assert release_worker.wait(5)
        return {"summary": "Worker complete", "evidence_paths": []}

    backend.backend.responses = [worker_reply, {"summary": "Parent complete"}]

    def worker():
        try:
            results["worker"] = coordinator.run("Review", source_paths=["sample.py"])
        except BaseException as exc:
            errors.append(exc)

    def parent():
        parent_started.set()
        try:
            results["parent"] = backend.chat([{"role": "user", "content": "Parent request"}])
        except BaseException as exc:
            errors.append(exc)
        finally:
            parent_finished.set()

    worker_thread, parent_thread = threading.Thread(target=worker), threading.Thread(target=parent)
    worker_thread.start()
    try:
        assert worker_entered.wait(5)
        parent_thread.start()
        assert parent_started.wait(5)
        assert not parent_finished.wait(0.1)
        assert len(backend.backend.calls) == 1
    finally:
        release_worker.set()
        worker_thread.join(5)
        if parent_thread.ident is not None:
            parent_thread.join(5)
    assert not errors and not worker_thread.is_alive() and not parent_thread.is_alive()
    assert results["worker"].summary == "Worker complete"
    assert "Parent complete" in results["parent"].content
    assert backend.task.budget_spent == pytest.approx(0.002)
    assert len(backend.task.budget_events) == 2
    assert all(event["status"] == "completed" for event in backend.task.budget_events)


def test_waiting_parent_call_can_cancel_without_send(tmp_path):
    cancelled = threading.Event()

    def check():
        if threading.current_thread().name == "waiting-parent" and cancelled.is_set():
            raise TaskCancelled("cancelled while waiting for shared accounting")

    project, backend, coordinator = setup(tmp_path, cancel_check=check)
    failures = []

    def wait_for_call():
        try:
            backend.chat([{"role": "user", "content": "Not sent"}])
        except BaseException as exc:
            failures.append(exc)

    with backend.call_scope():
        waiting = threading.Thread(target=wait_for_call, name="waiting-parent")
        waiting.start()
        cancelled.set()
        waiting.join(3)
        assert not waiting.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], TaskCancelled)
    assert not backend.backend.calls and not backend.task.budget_events
