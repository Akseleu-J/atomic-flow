"""A status/reconcile lease must not kill a newly spawned queued worker."""
import threading
import time
from contextlib import contextmanager

import pytest

from atomic_flow import job_worker
from atomic_flow.agents.execution import CommandResult
from atomic_flow.config import ExecutionConfig
from atomic_flow.jobs import JobStore, _atomic
from atomic_flow.locking import exclusive_file


@pytest.mark.parametrize("interrupted_before_claim", [False, True])
def test_worker_waits_for_reconcile_lease_and_rechecks_terminal_state(tmp_path, monkeypatch, interrupted_before_claim):
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.py").write_text("print('fixture')\n", encoding="utf-8")
    store = JobStore(project)
    with monkeypatch.context() as patch:
        patch.setattr("atomic_flow.jobs.subprocess.Popen", lambda *a, **kw: None)
        job = store.start(["source.py"], config=ExecutionConfig(mode="local"))
    directory = store._directory(job["id"])
    attempted, finished = threading.Event(), threading.Event()
    errors, executions = [], []

    @contextmanager
    def observed(path, **kwargs):
        if path == directory / "worker.lock":
            attempted.set()
        with exclusive_file(path, **kwargs):
            yield

    def execute(*args, **kwargs):
        executions.append(1)
        return CommandResult(0, "", "")

    def claim():
        try:
            job_worker.run_worker(store, job["id"])
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(job_worker, "exclusive_file", observed)
    monkeypatch.setattr(job_worker, "run_python", execute)
    actor = threading.Thread(target=claim, daemon=True)
    with exclusive_file(directory / "worker.lock"):
        actor.start()
        assert attempted.wait(5)
        premature = finished.wait(0.2)
        if interrupted_before_claim:
            # A reconciler can make an aged queued request terminal while it
            # owns the lease. The delayed worker must observe that decision.
            with store._lease(job["id"]):
                state = store._read(job["id"])
                state.update(status="interrupted", finished_at=time.time(), updated_at=time.time(),
                             error="worker lease lost; execution was not replayed")
                _atomic(directory / "state.json", state)
    actor.join(5)
    assert not actor.is_alive()
    assert not premature, f"queued worker died on a transient observer lease: {errors}"
    assert not errors
    assert executions == ([] if interrupted_before_claim else [1])
    assert store.status(job["id"])["status"] == ("interrupted" if interrupted_before_claim else "succeeded")
