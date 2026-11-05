import dataclasses
import os
import subprocess

import pytest
from pydantic import ValidationError

from atomic_flow.agents.execution import (
    EXECUTION_PROFILES,
    ExecutionUnavailable,
    docker_command,
    execution_profile,
    run_python,
)
from atomic_flow.config import ExecutionConfig, TaskConfig, load_config


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill helper")
def test_failed_tree_helper_still_kills_owned_parent_and_preserves_uncertainty(monkeypatch):
    from atomic_flow.agents.execution import _kill_tree

    class Process:
        pid = 123
        killed = False

        def poll(self):
            return None if not self.killed else -1

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("proc", timeout)
            return -1

    proc = Process()

    def helper(command, **kwargs):
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(command, 10)

    # Parent exit cannot prove that descendants died after the helper failed.
    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.run", helper)
    with pytest.raises(ExecutionUnavailable, match="effects remain unknown"):
        _kill_tree(proc)
    assert proc.killed

    # A process that survives even the forceful kill keeps the honest unknown.
    class Stubborn(Process):
        def kill(self):
            pass  # TerminateProcess refused; the tree is still running

    stubborn = Stubborn()
    with pytest.raises(ExecutionUnavailable, match="effects remain unknown"):
        _kill_tree(stubborn)


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill helper")
@pytest.mark.parametrize("helper_status", [1, 128])
def test_exited_parent_does_not_prove_descendant_cleanup(monkeypatch, helper_status):
    from types import SimpleNamespace

    from atomic_flow.agents.execution import _kill_tree

    class ExitedParent:
        pid = 123

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pytest.fail("already exited parent must not need killing")

    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=helper_status))
    with pytest.raises(ExecutionUnavailable, match="effects remain unknown"):
        _kill_tree(ExitedParent())


def test_owned_windows_job_waits_for_every_process(monkeypatch):
    from types import SimpleNamespace

    from atomic_flow.windows_job import WindowsJob

    active_counts = [2, 1, 0]
    observed = []

    def query(handle, information_class, info, size, returned):
        info._obj.active = active_counts.pop(0)
        observed.append(info._obj.active)
        return 1

    job = WindowsJob.__new__(WindowsJob)
    job.handle = 123
    job.api = SimpleNamespace(TerminateJobObject=lambda *args: 1, QueryInformationJobObject=query)
    monkeypatch.setattr("atomic_flow.windows_job.time.sleep", lambda seconds: None)
    job.terminate()
    assert observed == [2, 1, 0]


@pytest.mark.parametrize("query_success", [True, False])
def test_owned_windows_job_unobserved_or_active_tree_remains_unknown(monkeypatch, query_success):
    from types import SimpleNamespace

    from atomic_flow.windows_job import WindowsJob

    def query(handle, information_class, info, size, returned):
        info._obj.active = 1
        return query_success

    job = WindowsJob.__new__(WindowsJob)
    job.handle = 123
    job.api = SimpleNamespace(TerminateJobObject=lambda *args: 1, QueryInformationJobObject=query)
    times = iter([0, 31])
    monkeypatch.setattr("atomic_flow.windows_job.time.monotonic", lambda: next(times, 31))
    with pytest.raises(OSError, match="processes remain active|could not be observed"):
        job.terminate()


@pytest.mark.parametrize("kind", ["mcp", "lsp"])
def test_host_clients_retain_owned_job_cleanup_uncertainty(tmp_path, kind):
    import sys

    from atomic_flow.integrations.mcp import MCPClient, MCPError
    from atomic_flow.lsp import LSPClient, LSPError

    class UnobservedJob:
        closed = False

        def terminate(self):
            raise OSError("query refused")

        def close(self):
            self.closed = True

    cls, error = (MCPClient, MCPError) if kind == "mcp" else (LSPClient, LSPError)
    peer = cls(tmp_path, [sys.executable], approved=True)
    job = UnobservedJob()
    peer._owned_job = job
    for _ in range(2):
        with pytest.raises(error, match="effects remain unknown"):
            peer.close()
    assert job.closed


def test_fixed_profiles_and_mount_options(tmp_path):
    read_only = docker_command("docker", "image", tmp_path, "job", ["-c", "pass"], profile="read-only")
    mount = read_only[read_only.index("--mount") + 1]
    assert mount.endswith("dst=/workspace,readonly")
    assert "--memory=512m" in read_only and "--pids-limit=64" in read_only
    assert "--network=none" in read_only and "--read-only" in read_only
    assert read_only.count("--mount") == 1
    assert "--privileged" not in read_only
    with pytest.raises(TypeError):
        EXECUTION_PROFILES["escape"] = execution_profile("checks")
    with pytest.raises(dataclasses.FrozenInstanceError):
        execution_profile("checks").workspace_read_only = True


def test_profile_selection_cannot_supply_bypass(tmp_path, monkeypatch):
    for invalid in ("host", "--privileged", "checks,network=host"):
        with pytest.raises(ValidationError):
            ExecutionConfig(profile=invalid)
        with pytest.raises(ExecutionUnavailable, match="Unknown"):
            docker_command("docker", "image", tmp_path, "job", [], profile=invalid)
    with pytest.raises(ValidationError):
        ExecutionConfig(network="host")
    monkeypatch.delenv("ATOMIC_FLOW_EXECUTION_MODE")
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow/config.yaml").write_text("execution:\n  profile: read-only\ntask:\n  workspace_backend: git-worktree\n")
    config = load_config(tmp_path)
    assert config.execution.profile == "checks" and config.task.workspace_backend == "copy"
    assert TaskConfig(workspace_backend="git-worktree").workspace_backend == "git-worktree"


def test_bind_mount_option_injection_and_local_profile_fail_closed(tmp_path):
    with pytest.raises(ExecutionUnavailable, match="bind mount"):
        docker_command("docker", "image", tmp_path / "bad,src=/", "job", [])
    with pytest.raises(ExecutionUnavailable, match="requires Docker"):
        run_python(["-c", "raise Exception('must not run')"], tmp_path,
                   config=ExecutionConfig(mode="local", profile="read-only"))
    with pytest.raises(ExecutionUnavailable, match="container name"):
        run_python([], tmp_path, config=ExecutionConfig(mode="local"), container_name="foreign-container")


def test_streaming_has_explicit_tail_truncation_and_full_callback(tmp_path):
    chunks = {"stdout": [], "stderr": []}
    result = run_python(["-c", "import sys; print('X'*9000); print('error', file=sys.stderr)"], tmp_path,
                        config=ExecutionConfig(mode="local", output_bytes=1024),
                        output_callback=lambda stream, chunk: chunks[stream].append(chunk))
    assert result.returncode == 0 and result.stdout_truncated and not result.stderr_truncated
    assert len(result.stdout.encode()) == 1024
    assert b"".join(chunks["stdout"]).count(b"X") == 9000
    assert b"error" in b"".join(chunks["stderr"])


def test_collection_failure_stops_process_and_hides_exception(tmp_path):
    def failed_output(stream, chunk):
        raise ValueError("secret-in-callback-error")

    with pytest.raises(ExecutionUnavailable, match="Output collection failed") as error:
        run_python(["-u", "-c", "import time; print('ready'); time.sleep(30)"], tmp_path,
                   config=ExecutionConfig(mode="local"), output_callback=failed_output, timeout=4)
    assert "secret-in-callback-error" not in str(error.value)


@pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1", reason="requires real Docker integration run")
def test_real_read_only_workspace_profile(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("original")
    script = """
import pathlib
p = pathlib.Path('/workspace/source.txt')
assert p.read_text() == 'original'
try:
    p.write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('read-only profile allowed workspace mutation')
print('READ_ONLY_PROFILE_PASS')
"""
    result = run_python(["-c", script], tmp_path, timeout=30,
                        config=ExecutionConfig(mode="docker", profile="read-only"))
    assert result.returncode == 0, result.stderr
    assert "READ_ONLY_PROFILE_PASS" in result.stdout
    assert source.read_text() == "original"
