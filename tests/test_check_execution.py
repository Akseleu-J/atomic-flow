"""CheckSpec execution refuses incomplete or stale checks without host execution."""
import json
from dataclasses import replace

import pytest

from atomic_flow import check_execution as checks
from atomic_flow.agents import execution
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_spec import CheckResources, CheckSpec
from atomic_flow.config import ExecutionConfig


@pytest.fixture
def admitted(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("def test_ok():\n    assert True\n")
    return CheckSpec("example", "a" * 40, "b" * 40, check_source_revision(tmp_path),
                     "example/image@sha256:" + "c" * 64, "/opt/miniconda3/envs/testbed/bin/python",
                     "pytest", ("tests/test_example.py",)), tmp_path


def config(spec, **changes):
    return ExecutionConfig(mode="docker", image=spec.image, profile="checks",
                           output_bytes=spec.resources.output_bytes_per_stream).model_copy(update=changes)


def runner(spec, expected_nonce, **changes):
    receipt = {"nonce": expected_nonce, "root": spec.target_root, "interpreter": spec.interpreter,
               "pytest_module": "/opt/miniconda3/envs/testbed/lib/python3.9/site-packages/pytest/__init__.py",
               "pytest_version": "7.4.4", "collected": 1, "files": sorted(spec.test_paths),
               "finished": True, "skipped": 0, "failed": 0, "exit_code": 0}
    receipt.update(changes)
    return execution.CommandResult(0, checks._MARKER + json.dumps(receipt), "")


def test_bounded_docker_argv_selects_exact_interpreter_root_and_resources(admitted):
    spec, root = admitted
    spec = replace(spec, target_root="/testbed", resources=CheckResources(memory_bytes=512 * 1024**2,
                   cpus=1, pids=64, tmpfs_bytes=64 * 1024**2))
    command = execution.docker_command("docker", spec.image, root, "check", list(spec.argv[1:]),
                                      host_spec=spec, host_spec_nonce="a" * 32)
    assert command[command.index(spec.image) + 1] == spec.interpreter
    assert "--workdir=/testbed" in command
    assert any(value.endswith("dst=/testbed") for value in command)
    assert "--memory=536870912" in command and "--pids-limit=64" in command
    assert "--cpus=1" in command
    assert "--network=none" in command and "--read-only" in command and "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command and "--pull=never" in command
    assert command[-1] == "tests/test_example.py"


@pytest.mark.parametrize("changes", [
    {"mode": "local"}, {"profile": "read-only"}, {"image": "different/image"},
    {"output_bytes": 16385},
])
def test_executor_configuration_mismatch_refuses_before_subprocess(admitted, changes):
    spec, root = admitted
    with pytest.raises(execution.ExecutionUnavailable):
        checks.admit_check_spec(spec, root, config(spec, **changes), 120, list(spec.argv[1:]))


@pytest.mark.parametrize("timeout", [0, -1, 181, float("inf"), float("nan"), True])
def test_deadline_never_expands_host_spec(admitted, timeout):
    spec, root = admitted
    with pytest.raises(execution.ExecutionUnavailable):
        checks.admit_check_spec(spec, root, config(spec), timeout, list(spec.argv[1:]))


def test_stale_source_missing_test_and_native_runner_refuse(admitted):
    spec, root = admitted
    assert checks.admit_check_spec(spec, root, config(spec), 120, list(spec.argv[1:])) == spec.source_revision
    (root / "tests/test_example.py").write_text("changed\n")
    with pytest.raises(execution.ExecutionUnavailable, match="stale"):
        checks.admit_check_spec(spec, root, config(spec), 120, list(spec.argv[1:]))
    (root / "tests/test_example.py").unlink()
    with pytest.raises(execution.ExecutionUnavailable, match="missing"):
        checks.admit_check_spec(spec, root, config(spec), 120, list(spec.argv[1:]))
    native = replace(spec, runner="sympy-native", test_paths=("sympy/test_example.py",))
    with pytest.raises(execution.ExecutionUnavailable, match="not implemented"):
        checks.admit_check_spec(native, root, config(native), 120, list(native.argv[1:]))


def test_arbitrary_args_and_nonce_refuse(admitted):
    spec, root = admitted
    with pytest.raises(execution.ExecutionUnavailable):
        checks.admit_check_spec(spec, root, config(spec), 120, ["-c", "arbitrary()"])
    with pytest.raises(execution.ExecutionUnavailable):
        checks.checker_args(spec, "bad")
    with pytest.raises(execution.ExecutionUnavailable):
        checks.checker_args(spec, None)
    with pytest.raises(execution.ExecutionUnavailable):
        execution.docker_command("docker", spec.image, root, "check", list(spec.argv[1:]),
                                 host_spec_nonce="a" * 32)


def test_historical_pytest_self_runner_is_explicitly_unsupported(admitted):
    spec, root = admitted
    spec = replace(spec, task_id="pytest-dev__pytest-6116")
    with pytest.raises(execution.ExecutionUnavailable, match="self-checks"):
        checks.admit_check_spec(spec, root, config(spec), 120, list(spec.argv[1:]))


@pytest.mark.parametrize("changes", [
    {"collected": 0}, {"collected": True}, {"files": []}, {"files": ["unreviewed.py"]},
    {"skipped": 1}, {"failed": 1}, {"finished": False}, {"exit_code": 1},
    {"root": "/wrong"}, {"interpreter": "/usr/bin/python3"},
    {"pytest_module": "/workspace/pytest/__init__.py"}, {"nonce": "different"},
])
def test_zero_skipped_partial_or_wrong_identity_never_pass(admitted, changes):
    spec, _ = admitted
    assert checks._runner_receipt(runner(spec, "a" * 32, **changes), spec, "a" * 32) is None


@pytest.mark.parametrize("field", ["timed_out", "stdout_truncated", "stderr_truncated"])
def test_timeout_or_truncation_never_passes(admitted, field):
    spec, _ = admitted
    result = runner(spec, "a" * 32)
    setattr(result, field, True)
    assert checks._runner_receipt(result, spec, "a" * 32) is None


def test_missing_duplicate_and_malformed_receipts_fail_closed(admitted):
    spec, _ = admitted
    result = runner(spec, "a" * 32)
    for body in ("", result.stdout + "\n" + result.stdout, checks._MARKER + "not-json"):
        assert checks._runner_receipt(execution.CommandResult(0, body, ""), spec, "a" * 32) is None


def test_real_boundary_uses_existing_lifecycle_and_never_independent_success(admitted, monkeypatch):
    spec, root = admitted
    calls = []

    def execute(args, project, **options):
        calls.append((args, project, options))
        return runner(spec, options["host_spec_nonce"])

    monkeypatch.setattr(execution, "run_python", execute)
    receipt = checks.run_check_spec(spec, root)
    assert receipt.check_passed and not receipt.verified_success
    assert receipt.spec_digest == spec.digest
    assert receipt.source_before == receipt.source_after == spec.source_revision
    assert calls[0][0] == list(spec.argv[1:]) and calls[0][1] == root
    assert calls[0][2]["host_spec"] is spec
    assert calls[0][2]["config"].mode == "docker"


def test_source_write_during_tests_cannot_pass(admitted, monkeypatch):
    spec, root = admitted

    def execute(args, project, **options):
        (root / "tests/test_example.py").write_text("changed\n")
        return runner(spec, options["host_spec_nonce"])

    monkeypatch.setattr(execution, "run_python", execute)
    receipt = checks.run_check_spec(spec, root)
    assert not receipt.check_passed and not receipt.verified_success
    assert receipt.source_after != receipt.source_before


def test_uncertain_container_cleanup_propagates_without_success_receipt(admitted, monkeypatch):
    spec, root = admitted

    def execute(*args, **kwargs):
        raise execution.ExecutionUnavailable("Container cleanup could not be confirmed")

    monkeypatch.setattr(execution, "run_python", execute)
    with pytest.raises(execution.ExecutionUnavailable, match="cleanup"):
        checks.run_check_spec(spec, root)


def test_cancellation_precedes_source_inventory_and_process(admitted):
    spec, root = admitted

    def cancelled():
        raise execution.TaskCancelled("cancelled")

    with pytest.raises(execution.TaskCancelled):
        checks.admit_check_spec(spec, root, config(spec), 120, list(spec.argv[1:]), cancel_check=cancelled)


def test_local_fallback_is_refused_at_the_shared_boundary(admitted, monkeypatch):
    spec, root = admitted
    monkeypatch.setattr(execution, "execution_manifest", lambda *args, **kwargs: pytest.fail("reached executor"))
    with pytest.raises(execution.ExecutionUnavailable):
        execution.run_python(list(spec.argv[1:]), root, config=config(spec, mode="local"),
                             host_spec=spec, host_spec_nonce="a" * 32)
