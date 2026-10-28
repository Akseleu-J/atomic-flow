import hashlib
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from atomic_flow.agents.control import check_cancel, request_cancel, task_lease
from atomic_flow.agents.execution import ExecutionUnavailable, TaskCancelled, container_identity, docker_command, run_python
from atomic_flow.agents.task import Task, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig, ExecutionConfig, load_config


def private_syntax_fixture(root):
    from atomic_flow.agents.verify import check_source_revision
    from atomic_flow.check_private_runtime import PrivateSyntaxRequest
    from atomic_flow.check_runtime import RuntimeIdentity
    from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckSpec, SymPyRunnerIdentity

    runtime = RuntimeIdentity(base_image="swebench/task@sha256:" + "a" * 64,
        base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
        interpreter="/opt/p07-python36/bin/python3.6", python_version=(3, 6, 15), runner="sympy-native",
        profile_sha256="d" * 64, helper_sha256="e" * 64, inventory_sha256="f" * 64,
        provisioning_receipt_sha256="1" * 64, private_prefix="/opt/p07-python36",
        launcher="/opt/p07-runtime/python36", launcher_sha256="2" * 64)
    for path in (*SYMPY_RUNNER_PATHS, "sympy/core/tests/test_value.py", "sympy/value.py"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"VALUE = 1\n")
    native = SymPyRunnerIdentity((3, 6, 15), "1.4", tuple((path, hashlib.sha256((root / path).read_bytes()).hexdigest())
                                                    for path in SYMPY_RUNNER_PATHS))
    spec = CheckSpec("sympy__sympy-example", "a" * 40, "b" * 40, check_source_revision(root),
                     runtime.image_id, runtime.interpreter, "sympy-native", ("sympy/core/tests/test_value.py",),
                     sympy_identity=native, runtime_identity=runtime)
    request = PrivateSyntaxRequest(runtime, spec.digest, spec.source_revision, "sympy/value.py",
                                   hashlib.sha256((root / "sympy/value.py").read_bytes()).hexdigest(), "3" * 32)
    return spec, request


def test_private_syntax_docker_command_uses_only_bound_launcher_and_fixed_bootstrap(tmp_path, monkeypatch):
    from atomic_flow.check_private_runtime import private_syntax_args
    spec, request = private_syntax_fixture(tmp_path)
    def ordinary_forbidden(*args):
        pytest.fail("syntax must not invoke ordinary test bootstrap")
    monkeypatch.setattr("atomic_flow.check_execution.checker_args", ordinary_forbidden)
    command = docker_command("docker", spec.image, tmp_path, "test", list(spec.argv[1:]),
                             host_spec=spec, host_spec_nonce=request.nonce, host_syntax_request=request)
    index = command.index("--")
    assert command[index + 1:] == [spec.image, *private_syntax_args(request)]
    assert "--entrypoint=" + spec.runtime_identity.launcher in command[:index]
    for option in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit=128", "--memory=1073741824", "--cpus=2", "--pull=never"):
        assert option in command
    assert command.count("--mount") == 1
    assert "--workdir=/workspace" in command


def test_private_ordinary_check_uses_same_exact_launcher(tmp_path, monkeypatch):
    spec, request = private_syntax_fixture(tmp_path)
    calls = []
    def checker(value, nonce):
        calls.append((value, nonce))
        return ["-c", "reviewed-native-bootstrap"]
    monkeypatch.setattr("atomic_flow.check_execution.checker_args", checker)
    command = docker_command("docker", spec.image, tmp_path, "test", list(spec.argv[1:]),
                             host_spec=spec, host_spec_nonce=request.nonce)
    index = command.index("--")
    assert command[index + 1:] == [spec.image, "-c", "reviewed-native-bootstrap"]
    assert "--entrypoint=" + spec.runtime_identity.launcher in command[:index]
    assert calls == [(spec, request.nonce)]


@pytest.mark.parametrize("changes", [{"check_spec_digest": "0" * 64}, {"source_revision": "0" * 64},
                                    {"target_root": "/testbed"}, {"nonce": "0" * 32}])
def test_private_syntax_request_binding_mismatch_refuses_before_process(tmp_path, monkeypatch, changes):
    spec, request = private_syntax_fixture(tmp_path)
    changed = replace(request, **changes)
    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.Popen", lambda *args, **kwargs: pytest.fail("must refuse before spawn"))
    with pytest.raises(ExecutionUnavailable, match="differs"):
        run_python(list(spec.argv[1:]), tmp_path, config=ExecutionConfig(mode="docker", image=spec.image),
                   host_spec=spec, host_spec_nonce=request.nonce, host_syntax_request=changed)
    with pytest.raises(ExecutionUnavailable, match="differs"):
        docker_command("docker", spec.image, tmp_path, "test", list(spec.argv[1:]),
                       host_spec=spec, host_spec_nonce=request.nonce, host_syntax_request=changed)


def test_private_syntax_runtime_substitution_refuses(tmp_path):
    from atomic_flow.agents.execution import validate_host_syntax_request
    spec, request = private_syntax_fixture(tmp_path)
    changed = replace(request, runtime=replace(request.runtime, profile_sha256="9" * 64))
    with pytest.raises(ExecutionUnavailable, match="differs"):
        validate_host_syntax_request(spec, request.nonce, changed)


def test_private_syntax_successful_admission_skips_ordinary_checker_before_engine_probe(tmp_path, monkeypatch):
    spec, request = private_syntax_fixture(tmp_path)
    monkeypatch.setattr("atomic_flow.check_execution.checker_args", lambda *args: pytest.fail("ordinary checker is not syntax"))
    def probe(config, requirements, *, probe):
        assert probe is True
        raise ExecutionUnavailable("test stopped at engine probe")
    monkeypatch.setattr("atomic_flow.agents.execution.execution_manifest", probe)
    with pytest.raises(ExecutionUnavailable, match="test stopped at engine probe"):
        run_python(list(spec.argv[1:]), tmp_path, config=ExecutionConfig(mode="docker", image=spec.image),
                   host_spec=spec, host_spec_nonce=request.nonce, host_syntax_request=request)


@pytest.mark.parametrize("with_spec", [True, False])
def test_private_syntax_requires_typed_request_and_host_spec(tmp_path, monkeypatch, with_spec):
    spec, request = private_syntax_fixture(tmp_path)
    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.Popen", lambda *args, **kwargs: pytest.fail("must refuse before spawn"))
    arguments = {"host_spec": spec, "host_spec_nonce": request.nonce} if with_spec else {}
    with pytest.raises(ExecutionUnavailable):
        run_python(list(spec.argv[1:]), tmp_path, config=ExecutionConfig(mode="docker", image=spec.image),
                   host_syntax_request=request.as_dict() if with_spec else request, **arguments)
    with pytest.raises(ExecutionUnavailable):
        docker_command("docker", spec.image, tmp_path, "test", list(spec.argv[1:]),
                       host_syntax_request=request.as_dict() if with_spec else request, **arguments)


def test_private_syntax_still_requires_admitted_source_config_and_exact_descriptive_args(tmp_path, monkeypatch):
    spec, request = private_syntax_fixture(tmp_path)
    monkeypatch.setattr("atomic_flow.agents.execution.subprocess.Popen", lambda *args, **kwargs: pytest.fail("must refuse before spawn"))
    monkeypatch.setattr("atomic_flow.check_execution.checker_args", lambda *args: pytest.fail("ordinary checker is not syntax"))
    for config, args in ((ExecutionConfig(mode="local", image=spec.image), list(spec.argv[1:])),
                         (ExecutionConfig(mode="docker", image=spec.image), ["-c", "arbitrary"]),
                         (ExecutionConfig(mode="docker", image="wrong:latest"), list(spec.argv[1:]))):
        with pytest.raises(ExecutionUnavailable):
            run_python(args, tmp_path, config=config, host_spec=spec, host_spec_nonce=request.nonce,
                       host_syntax_request=request)
    (tmp_path / "sympy/value.py").write_bytes(b"VALUE = 2\n")
    with pytest.raises(ExecutionUnavailable, match="stale"):
        run_python(list(spec.argv[1:]), tmp_path, config=ExecutionConfig(mode="docker", image=spec.image),
                   host_spec=spec, host_spec_nonce=request.nonce, host_syntax_request=request)


def test_environment_and_output_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_SECRET_TEST", "must-not-inherit")
    result = run_python(["-c", "import os; print(os.getenv('PROVIDER_SECRET_TEST')); print('x'*100000)"],
                        tmp_path, config=ExecutionConfig(mode="local", output_bytes=1024))
    assert result.returncode == 0
    assert len(result.stdout.encode()) <= 1024
    probe = run_python(["-c", "import os,json; print(json.dumps(dict(os.environ)))"], tmp_path,
                       config=ExecutionConfig(mode="local"))
    env = json.loads(probe.stdout)
    assert "PROVIDER_SECRET_TEST" not in env
    assert "ATOMIC_FLOW_HOME" not in env
    assert "PYTHONPATH" not in env
    assert env["HOME"] != str(Path.home())


def test_timeout_and_cancellation_stop_command(tmp_path):
    cfg = ExecutionConfig(mode="local")
    start = time.monotonic()
    result = run_python(["-c", "import time; time.sleep(30)"], tmp_path, config=cfg, timeout=0.2)
    assert result.timed_out and time.monotonic() - start < 10
    start = time.monotonic()

    def cancel():
        if time.monotonic() - start > 0.2:
            raise TaskCancelled("test")

    with pytest.raises(TaskCancelled):
        run_python(["-c", "import time; time.sleep(30)"], tmp_path, config=cfg, cancel_check=cancel)
    assert time.monotonic() - start < 10


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group guarantee; Windows taskkill is best effort")
def test_successful_parent_cannot_leave_background_writer(tmp_path):
    code = "import time,pathlib; time.sleep(1); pathlib.Path('escaped').write_text('bad')"
    result = run_python(["-c", f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{code!r}])"],
                        tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode == 0
    time.sleep(1.2)
    assert not (tmp_path / "escaped").exists()


def test_docker_missing_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("atomic_flow.agents.execution.shutil.which", lambda name: None)
    with pytest.raises(ExecutionUnavailable, match="no local fallback"):
        run_python(["-c", "raise Exception('must not execute')"], tmp_path, config=ExecutionConfig(mode="docker"))


def test_docker_command_restricts_host_access(tmp_path):
    cmd = docker_command("docker", "atomic-flow-checks:local", tmp_path, "test", ["-m", "pytest"])
    for option in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--pids-limit=128", "--memory=1g", f"--user={container_identity()}", "--pull=never"):
        assert option in cmd
    assert sum(item == "--mount" for item in cmd) == 1
    assert "docker.sock" not in " ".join(cmd)


def test_root_host_never_selects_root_container(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 0, raising=False)
    assert container_identity() == "65534:65534"


def test_private_workspace_uses_nonroot_caller_identity(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1001, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 1002, raising=False)
    assert container_identity() == "1001:1002"


def test_untrusted_project_cannot_disable_container(tmp_path, monkeypatch):
    monkeypatch.delenv("ATOMIC_FLOW_EXECUTION_MODE")
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow/config.yaml").write_text("execution:\n  mode: local\n")
    assert load_config(tmp_path).execution.mode == "docker"


def test_cancel_signal_survives_task_save_and_prevents_execution(tmp_path):
    task = Task.new("test", tmp_path, [])
    save(task)
    request_cancel(task.id)
    save(task)
    with pytest.raises(TaskCancelled):
        check_cancel(task.id)
    task = TaskRunner(AtomicConfig(), tmp_path, None).resume(task)
    assert task.status == "cancelled" and task.error == "cancelled by user"
    assert not task.changeset_id


def test_only_one_runner_can_hold_task_lease(tmp_path):
    task = Task.new("test", tmp_path, [])
    with task_lease(task.id):
        with pytest.raises(ValueError, match="already"):
            with task_lease(task.id):
                pytest.fail("second lease acquired")
    with task_lease(task.id):
        pass


def test_cancel_during_publication_recovers_original(tmp_path):
    from atomic_flow.agents.changeset import ChangeSet

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("before")
    (project / "b.py").write_text("before")
    change = ChangeSet.create(project)
    for name in ("a.py", "b.py"):
        (change.workspace / name).write_text("after")
        change.track(name)
    calls = []

    def cancel():
        calls.append(1)
        if len(calls) == 3:
            raise TaskCancelled("test")

    with pytest.raises(TaskCancelled):
        change.publish(cancel_check=cancel)
    assert (project / "a.py").read_text() == "before"
    assert (project / "b.py").read_text() == "before"


def test_cancellation_after_model_response_does_not_execute_next_tool(tmp_path):
    from conftest import ScriptedBackend

    from atomic_flow.agents.planner import Plan, PlanStep
    from atomic_flow.agents.task import list_tasks
    from atomic_flow.tree.builder import build_tree, save_tree

    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    save_tree(tmp_path, build_tree(tmp_path))

    def stop_after_response(_):
        request_cancel(list_tasks()[0].id)
        return {"task_type": "modify", "focus_terms": ["a"]}

    backend = ScriptedBackend({"parser": stop_after_response})
    task = TaskRunner(AtomicConfig(), tmp_path, backend).run_plan("update a", Plan(steps=[PlanStep(description="update a")]))
    assert task.status == "cancelled" and task.error == "cancelled by user"
    assert backend.roles() == ["parser"]
    assert (tmp_path / "a.py").read_text() == "def a():\n    return 1\n"


@pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1", reason="requires explicit real Docker integration run")
def test_real_container_boundaries(tmp_path):
    # Host-only file lives outside the only mounted directory.
    project = tmp_path / "workspace"
    project.mkdir()
    secret = tmp_path / "host-only.txt"
    secret.write_text("not mounted")
    code = f"""
import os, pathlib, socket
assert os.getuid() == {container_identity().split(':')[0]} != 0
assert not pathlib.Path({str(secret)!r}).exists()
try:
    pathlib.Path('/etc/atomic-write-probe').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('root filesystem writable')
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(('1.1.1.1', 443)) != 0
assert 'ATOMIC_FLOW_HOME' not in os.environ
print('CONTAINER_BOUNDARIES_PASS')
"""
    result = run_python(["-c", code], project, config=ExecutionConfig(mode="docker"), timeout=30)
    assert not result.timed_out
    assert result.returncode == 0, result.stderr
    assert "CONTAINER_BOUNDARIES_PASS" in result.stdout
