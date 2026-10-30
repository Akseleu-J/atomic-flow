"""Trusted detached job worker entry point; never loaded from a project cwd."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

from atomic_flow.agents.execution import ExecutionUnavailable, TaskCancelled, execution_manifest, run_python
from atomic_flow.config import ExecutionConfig
from atomic_flow.jobs import TERMINAL, JobError, JobStore, _atomic, _snapshot_hashes
from atomic_flow.locking import exclusive_file


def run_worker(store: JobStore, job_id: str):
    directory = store._directory(job_id)
    # Status reconciliation briefly takes the same lease even while queued.
    # Wait for that observer; then re-read state under the metadata lease so a
    # late worker cannot replay an already terminal/reconciled request.
    with exclusive_file(directory / "worker.lock", blocking=True, busy_message="worker active"):
        with store._lease(job_id):
            state = store._read(job_id)
            if state["status"] in TERMINAL:
                return
            if state["status"] != "queued":
                raise JobError("job already claimed; execution cannot be replayed")
            state.update(status="running", updated_at=time.time())
            _atomic(directory / "state.json", state)
        lock = threading.Lock()
        retained, discarded = 0, 0
        cap_hit = threading.Event()
        handles = {}
        status, error, returncode = "failed", None, None
        launch_attempted = False
        effects_unknown = False
        last_heartbeat = 0

        def output(stream, chunk):
            nonlocal retained, discarded
            with lock:
                keep = min(len(chunk), max(0, state["output_bytes"] - retained))
                if keep:
                    handles[stream].write(chunk[:keep])
                    handles[stream].flush()
                    retained += keep
                discarded += len(chunk) - keep
                if len(chunk) > keep:
                    cap_hit.set()

        def check():
            nonlocal last_heartbeat
            if cap_hit.is_set():
                raise TaskCancelled("output storage limit reached")
            if (directory / "cancel").exists():
                raise TaskCancelled("cancelled by user")
            if time.monotonic() - last_heartbeat >= 1:
                with store._lease(job_id):
                    current = store._read(job_id)
                    current["updated_at"] = time.time()
                    _atomic(directory / "state.json", current)
                last_heartbeat = time.monotonic()

        try:
            workspace = directory / "workspace"
            hashes = _snapshot_hashes(workspace)
            for path in workspace.rglob("*"):
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise JobError("source snapshot contains a link")
            physical_files = {path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if not path.is_dir()}
            if hashes != state["source_hashes"] or physical_files != set(hashes):
                raise JobError("source snapshot integrity mismatch")
            for stream in ("stdout", "stderr"):
                handles[stream] = (directory / f"{stream}.bin").open("xb")
            if state["execution"]["mode"] == "docker" and shutil.which("docker") is None:
                raise JobError("Docker unavailable before execution")
            if state["execution"]["mode"] == "local" and state["execution"].get("profile", "checks") != "checks":
                raise JobError("selected execution profile requires Docker")
            resolved = execution_manifest(ExecutionConfig.model_validate(state["execution"]), probe=True)
            if state.get("resolved_execution", resolved) != resolved:
                raise JobError("execution runtime identity changed after job preparation")
            launch_attempted = True
            result = run_python(state["args"], workspace, config=ExecutionConfig.model_validate(state["execution"]),
                                timeout=state["timeout"], cancel_check=check, output_callback=output,
                                container_name=state["container_name"], container_id_file=directory / "container.id")
            returncode = result.returncode
            status = "output_limit" if cap_hit.is_set() else "timed_out" if result.timed_out else "succeeded" if returncode == 0 else "failed"
        except TaskCancelled:
            status = "output_limit" if cap_hit.is_set() else "cancelled"
        except (ExecutionUnavailable, JobError):
            error = "execution unavailable or source/output integrity check failed"
            effects_unknown = launch_attempted
        except Exception:
            error = "worker failed; see execution and source integrity gates"
            effects_unknown = launch_attempted
        finally:
            for handle in handles.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    status, error, effects_unknown = "failed", "output durability could not be confirmed", launch_attempted
                finally:
                    try:
                        handle.close()
                    except OSError:
                        status, error, effects_unknown = "failed", "output durability could not be confirmed", launch_attempted
            with store._lease(job_id):
                current = store._read(job_id)
                current.update(status=status, returncode=returncode, error=error,
                               updated_at=time.time(), finished_at=time.time(), discarded_bytes=discarded,
                               effects_unknown=effects_unknown,
                               cleanup_confirmed=not effects_unknown,
                               output_complete=status in {"succeeded", "failed", "timed_out", "cancelled"} and error is None)
                _atomic(directory / "state.json", current)


def main():
    if len(sys.argv) != 6:
        raise SystemExit("expected project, root, job ID, host home and workspace flag")
    project, root, job_id, home, private = sys.argv[1:]
    if private not in {"0", "1"}:
        raise SystemExit("invalid workspace flag")
    # The root is an explicit parent-host argument, never inherited from credentials.
    os.environ["ATOMIC_FLOW_HOME"] = home
    run_worker(JobStore(Path(project), Path(root), private_workspace=private == "1"), job_id)


if __name__ == "__main__":
    main()
