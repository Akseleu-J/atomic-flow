import base64
import os
import time

import pytest

from atomic_flow.config import ExecutionConfig
from atomic_flow.job_worker import run_worker
from atomic_flow.jobs import TERMINAL, JobError, JobStore, _atomic
from atomic_flow.locking import exclusive_file


@pytest.fixture
def store(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.py").write_text("print('source')", encoding="utf-8")
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "state"))
    return JobStore(project)


def wait(store, identifier, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = store.status(identifier)
        if state["status"] in TERMINAL:
            return state
        time.sleep(0.05)
    pytest.fail(f"worker did not finish: {state}")


def test_detached_job_snapshot_pages_and_clean_environment(store, monkeypatch):
    monkeypatch.setenv("JOB_TEST_SECRET", "do-not-inherit-credentials")
    (store.project / "atomic_flow.py").write_text("raise RuntimeError('import hijack')")
    (store.project / ".env").write_text("PRIVATE=never-snapshot")
    (store.project / "credentials.json").write_text('{"key":"never-snapshot"}')
    job = store.start(["-c", "import os;print(os.getenv('JOB_TEST_SECRET'));print('abcdefghij');print('err',file=__import__('sys').stderr)"],
                      config=ExecutionConfig(mode="local"))
    (store.project / "source.py").write_text("source changed")
    done = wait(store, job["id"])
    assert done["status"] == "succeeded", done
    assert done["output_complete"] and done["discarded_bytes"] == 0
    assert ".env" not in done["source_hashes"]
    assert "credentials.json" not in done["source_hashes"]
    assert "cursor_key" not in done and "args" not in done
    # The command used the copied source, never the subsequently modified original.
    assert (store._directory(job["id"]) / "workspace/source.py").read_text() == "print('source')"
    chunks, cursor = [], None
    for _ in range(20):
        page = store.output(job["id"], cursor=cursor, limit=3)
        chunks.append(base64.b64decode(page["data_base64"]))
        cursor = page["next_cursor"]
        if page["at_end"]:
            break
    assert b"".join(chunks) == b"None\nabcdefghij\n" or b"".join(chunks) == b"None\r\nabcdefghij\r\n"
    assert "err" in store.output(job["id"], stream="stderr")["text"]
    assert len(store.list()) == 1
    with pytest.raises(JobError, match="cursor"):
        store.output(job["id"], stream="stderr", cursor=cursor)
    with pytest.raises(JobError, match="cursor"):
        store.output(job["id"], cursor=cursor + "bad")


def test_live_output_then_cancel_is_durable(store):
    job = store.start(["-u", "-c", "import time;print('started',flush=True);time.sleep(120)"],
                      config=ExecutionConfig(mode="local"))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and "started" not in store.output(job["id"])["text"]:
        time.sleep(0.05)
    live = store.output(job["id"])
    if "started" not in live["text"]:
        store.cancel(job["id"])
    assert "started" in live["text"] and not live["terminal"] and not live["output_complete"]
    # A separate store instance can control and read the detached worker.
    second = JobStore(store.project)
    second.cancel(job["id"])
    done = wait(second, job["id"])
    assert done["status"] == "cancelled"
    assert "started" in second.output(job["id"])["text"]
    assert second.reconcile(job["id"]) == []


def test_timeout_and_output_cap_are_explicit(store):
    timed = store.start(["-c", "import time;time.sleep(30)"], config=ExecutionConfig(mode="local"), timeout=0.2)
    assert wait(store, timed["id"])["status"] == "timed_out"
    capped = store.start(["-c", "print('a'*100000)"], config=ExecutionConfig(mode="local"), output_bytes=1024)
    done = wait(store, capped["id"])
    assert done["status"] == "output_limit" and not done["output_complete"]
    assert done["discarded_bytes"] > 0
    assert store.output(capped["id"])["retained_bytes"] <= 1024
    assert sum((store._directory(capped["id"]) / (x + ".bin")).stat().st_size for x in ("stdout", "stderr")) <= 1024


def test_project_cursor_isolation_and_retention(store, tmp_path):
    first = store.start(["-c", "print('one')"], config=ExecutionConfig(mode="local"), retention_seconds=60)
    second = store.start(["-c", "print('two')"], config=ExecutionConfig(mode="local"))
    wait(store, first["id"])
    wait(store, second["id"])
    cursor = store.output(first["id"])["next_cursor"]
    with pytest.raises(JobError, match="cursor"):
        store.output(second["id"], cursor=cursor)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(JobError):
        JobStore(other).status(first["id"])
    state = store._read(first["id"])
    state["finished_at"] = time.time() - 65
    _atomic(store._directory(first["id"]) / "state.json", state)
    assert store.status(first["id"])["output_expired"]
    with pytest.raises(JobError, match="expired"):
        store.output(first["id"], cursor=cursor)


def queued(store, monkeypatch):
    monkeypatch.setattr("atomic_flow.jobs.subprocess.Popen", lambda *a, **kw: None)
    return store.start(["-c", "print('not replayed')"], config=ExecutionConfig(mode="local"))


def test_snapshot_tamper_fails_closed_and_terminal_never_replays(store, monkeypatch):
    job = queued(store, monkeypatch)
    directory = store._directory(job["id"])
    (directory / "workspace/source.py").write_text("tampered")
    run_worker(store, job["id"])
    done = store.status(job["id"])
    assert done["status"] == "failed" and not done["output_complete"]
    assert not (directory / "stdout.bin").exists()
    run_worker(store, job["id"])
    assert store.status(job["id"]) == done


def test_lost_lease_reconcile_does_not_kill_reused_pid_or_replay(store, monkeypatch):
    job = queued(store, monkeypatch)
    directory = store._directory(job["id"])
    state = store._read(job["id"])
    state.update(status="running", worker_pid=os.getpid())
    _atomic(directory / "state.json", state)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("PID kill is forbidden"))
    with exclusive_file(directory / "worker.lock"):
        assert store.reconcile(job["id"]) == []
    recovered = store.reconcile(job["id"])
    assert recovered[0]["status"] == "interrupted" and recovered[0]["effects_unknown"]
    assert not recovered[0]["cleanup_confirmed"]
    run_worker(store, job["id"])
    assert not (directory / "stdout.bin").exists()


def test_queued_startup_grace_and_metadata_tampering(store, monkeypatch):
    job = queued(store, monkeypatch)
    assert store.reconcile(job["id"]) == []
    path = store._directory(job["id"]) / "state.json"
    state = store._read(job["id"])
    state["created_at"] -= 16
    _atomic(path, state)
    assert store.reconcile(job["id"])[0]["status"] == "interrupted"
    state["container_name"] = "unrelated-container"
    _atomic(path, state)
    with pytest.raises(JobError, match="identity"):
        store.status(job["id"])


def test_storage_and_identifier_guards(store):
    with pytest.raises(JobError):
        store.start(["-c", "pass"], timeout=float("nan"))
    with pytest.raises(JobError):
        store.start(["-c", "pass"], output_bytes=1)
    with pytest.raises(JobError):
        store.status("../outside")
    with pytest.raises(JobError):
        JobStore(store.project, root=store.project / "jobs")


def test_docker_is_default_and_missing_engine_never_falls_back(store, monkeypatch):
    job = queued(store, monkeypatch)
    path = store._directory(job["id"]) / "state.json"
    state = store._read(job["id"])
    state["execution"] = ExecutionConfig(mode="docker").model_dump()
    _atomic(path, state)
    monkeypatch.setattr("atomic_flow.agents.execution.shutil.which", lambda *args: None)
    run_worker(store, job["id"])
    assert store.status(job["id"])["status"] == "failed"


def test_output_exact_bytes_survive_split_utf8(store):
    job = store.start(["-c", "print('🦀')"], config=ExecutionConfig(mode="local"))
    wait(store, job["id"])
    chunks, cursor = [], None
    while True:
        page = store.output(job["id"], cursor=cursor, limit=1)
        chunks.append(base64.b64decode(page["data_base64"]))
        cursor = page["next_cursor"]
        if page["at_end"]:
            break
    assert b"".join(chunks).decode().strip() == "🦀"


def test_global_active_admission_covers_projects_and_worker_roots(store, tmp_path, monkeypatch):
    queued(store, monkeypatch)
    for index in range(3):
        project = tmp_path / ("project" + str(index))
        project.mkdir()
        (project / "a.py").write_text("pass")
        other = JobStore(project, root=store.root / ("worker_" + str(index)))
        queued(other, monkeypatch)
    with pytest.raises(JobError, match="active job limit"):
        queued(store, monkeypatch)


def test_snapshot_preparation_failure_has_terminal_journal(store, monkeypatch):
    monkeypatch.setattr("atomic_flow.jobs._snapshot_hashes", lambda *a, **kw: (_ for _ in ()).throw(JobError("source changed")))
    job = store.start(["-c", "pass"], config=ExecutionConfig(mode="local"))
    assert job["status"] == "failed" and not job["effects_unknown"]
    assert store.list()[0]["id"] == job["id"]


def test_cleanup_failure_after_execution_attempt_keeps_uncertainty(store, monkeypatch):
    job = queued(store, monkeypatch)

    def failed_execution(*args, **kwargs):
        raise OSError("simulated child cleanup timeout")

    monkeypatch.setattr("atomic_flow.job_worker.run_python", failed_execution)
    run_worker(store, job["id"])
    state = store.status(job["id"])
    assert state["status"] == "failed" and state["effects_unknown"] and not state["output_complete"]
    assert store._active_count() == 1


def test_reconciliation_uses_immutable_container_id_never_reused_name(store, monkeypatch):
    job = queued(store, monkeypatch)
    path = store._directory(job["id"])
    state = store._read(job["id"])
    state.update(status="running", execution=ExecutionConfig(mode="docker").model_dump())
    _atomic(path / "state.json", state)
    monkeypatch.setattr("atomic_flow.jobs.shutil.which", lambda _: "docker")
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr("atomic_flow.jobs.subprocess.run", lambda command, **kw: (calls.append(command), Result())[1])
    # A missing ownership file cannot authorize deleting a newly reused name.
    store.reconcile(job["id"])
    assert not calls and not store.status(job["id"])["cleanup_confirmed"]
    state.update(status="running")
    _atomic(path / "state.json", state)
    (path / "container.id").write_text("a" * 64)
    store.reconcile(job["id"])
    assert calls == [["docker", "rm", "-f", "a" * 64]]
    assert store.status(job["id"])["cleanup_confirmed"]


def test_private_application_source_needs_explicit_host_flag(store):
    project = store.root.parent / "remote-worker/source"
    project.mkdir(parents=True)
    (project / "a.py").write_text("pass")
    with pytest.raises(JobError, match="private application"):
        JobStore(project)
    admitted = JobStore(project, private_workspace=True)
    assert admitted.private_workspace


def test_docker_cleanup_failure_is_not_proof_of_absence(store, monkeypatch):
    from types import SimpleNamespace

    from atomic_flow.agents.execution import _remove_container

    responses = iter([SimpleNamespace(returncode=1), SimpleNamespace(returncode=1, stdout="")])
    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.run", lambda *a, **kw: next(responses))
    assert not _remove_container("docker", "a" * 64, store.project)
    responses = iter([SimpleNamespace(returncode=1), SimpleNamespace(returncode=0, stdout="")])
    assert _remove_container("docker", "a" * 64, store.project)
    responses = iter([SimpleNamespace(returncode=1), SimpleNamespace(returncode=0, stdout="a" * 64)])
    assert not _remove_container("docker", "a" * 64, store.project)


@pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1" or os.name == "nt", reason="requires explicit real Linux Docker run")
def test_real_docker_jobs_output_cancel_and_crash_reconciliation(store, monkeypatch):
    import subprocess

    original = subprocess.Popen
    workers = []

    def launch(command, *args, **kwargs):
        proc = original(command, *args, **kwargs)
        if "-I" in command and "-c" in command:
            workers.append(proc)
        return proc

    monkeypatch.setattr("atomic_flow.jobs.subprocess.Popen", launch)
    normal = store.start(["-u", "-c", "print('docker-job')"], config=ExecutionConfig(mode="docker"))
    assert wait(store, normal["id"])["status"] == "succeeded"
    assert "docker-job" in store.output(normal["id"])["text"]
    for crash in (False, True):
        job = store.start(["-u", "-c", "import time;print('running',flush=True);time.sleep(120)"],
                          config=ExecutionConfig(mode="docker"))
        owned_worker = workers[-1]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and "running" not in store.output(job["id"])["text"]:
            time.sleep(0.05)
        assert "running" in store.output(job["id"])["text"]
        cid = (store._directory(job["id"]) / "container.id").read_text().strip()
        if crash:
            # Ownership is the live Popen returned by this test, never a stored PID.
            owned_worker.kill()
            owned_worker.wait(timeout=15)
            reconciled = store.reconcile(job["id"])
            assert reconciled[0]["status"] == "interrupted" and reconciled[0]["cleanup_confirmed"]
            assert reconciled[0]["effects_unknown"]
            run_worker(store, job["id"])
            assert store.status(job["id"])["status"] == "interrupted"
        else:
            store.cancel(job["id"])
            assert wait(store, job["id"])["status"] == "cancelled"
        inspected = subprocess.run(["docker", "inspect", cid], capture_output=True, timeout=15)
        assert inspected.returncode != 0
