"""Regressions for private execution inputs and checker identity/output gates."""
from __future__ import annotations

import json
import os

import pytest

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import CommandResult, docker_command, run_python
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.agents.tools.run_command import RunCommandArgs, RunCommandTool
from atomic_flow.config import AtomicConfig, ExecutionConfig
from atomic_flow.jobs import _snapshot_hashes
from atomic_flow.output import sanitize_output
from atomic_flow.remote import build_bundle

CANARY = "sk-" + "SYNTHETIC_CANARY_" * 3


def _completed_task(project):
    task = Task.new("Run tests", project, [Step(0, "Run tests", verify_with="pytest", status="done")])
    task.current_index = 1
    return task


def test_execution_snapshot_excludes_private_inputs_but_keeps_build_artifacts(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    private = ["credentials.json", "secrets.py", "secrets/nested.txt", "tls.pem", "service.env", "id_ed25519"]
    for name in private:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CANARY)
    (project / ".gitignore").write_text("credentials.json\ngenerated.dat\n")
    (project / "generated.dat").write_bytes(b"build input")
    (project / "a.py").write_text("A = 1\n")
    context = ToolContext(project, {})
    for name in private:
        with pytest.raises(SourceConflict):
            admitted_path(context, name, existing=True)
    change = ChangeSet.create(project)
    assert all(not (change.workspace / name).exists() for name in private)
    assert (change.workspace / "generated.dat").read_bytes() == b"build input"
    assert "a.py" in change.state["baseline"]
    job_manifest = _snapshot_hashes(project)
    assert not set(private).intersection(job_manifest)
    with pytest.raises(SourceConflict):
        build_bundle(project, ["credentials.json"], ["-m", "pytest"], image="atomic-flow-checks:local")


def test_command_output_and_diagnostics_redact_recognized_canaries(tmp_path, monkeypatch):
    source = tmp_path / "a.py"
    source.write_text("A = 1\n")
    context = ToolContext(tmp_path, {}, file_hashes={"a.py": digest(source.read_bytes())})
    monkeypatch.setattr("atomic_flow.agents.tools.run_command.run_python", lambda *a, **kw:
                        CommandResult(1, f"a.py:1:1: F401 {CANARY}\n", CANARY))
    result = RunCommandTool().run(RunCommandArgs(command="ruff"), context)
    assert result["diagnostics"]
    assert CANARY not in json.dumps(result)
    assert "[REDACTED]" in result["stdout"]
    raw = {"token": "unrecognized-value", "nested": [CANARY], "exit_code": 1}
    clean = sanitize_output(raw)
    assert raw["token"] == "unrecognized-value" and clean["token"] == "[REDACTED]"
    assert clean["exit_code"] == 1 and CANARY not in json.dumps(clean)


def test_run_python_redacts_stdout_and_stderr(tmp_path):
    result = run_python(["-c", f"import sys; print({CANARY!r}); print({CANARY!r}, file=sys.stderr)"],
                        tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode == 0
    assert CANARY not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stdout and "[REDACTED]" in result.stderr


def test_model_check_uses_snapshot_without_private_file_even_outside_taskrunner(tmp_path):
    (tmp_path / "credentials.json").write_text('"arbitrary-secret-without-recognized-prefix"')
    (tmp_path / "test_inputs.py").write_text(
        'from pathlib import Path\ndef test_private_absent():\n'
        '    assert not Path("credentials.json").exists()\n'
        '    Path("check_side_effect.txt").write_text("private only")\n')
    result = RunCommandTool().run(RunCommandArgs(command="pytest"),
                                 ToolContext(tmp_path, {}, execution=ExecutionConfig(mode="local")))
    assert result["exit_code"] == 0, result
    assert not (tmp_path / "check_side_effect.txt").exists()
    assert "arbitrary-secret" not in json.dumps(result)


def test_final_report_redacts_even_an_injected_executor_result(tmp_path, monkeypatch):
    change = ChangeSet.create(tmp_path)
    monkeypatch.setattr("atomic_flow.agents.completion.run_python", lambda *a, **kw: CommandResult(1, CANARY, CANARY))
    report = assess_completion(_completed_task(tmp_path), change, AtomicConfig())
    assert not report["ready_to_publish"] and CANARY not in json.dumps(report)


@pytest.mark.parametrize("checker, arguments", [("pytest", []), ("ruff", ["check"]), ("mypy", ["a.py"])])
def test_installed_checker_cannot_be_replaced_by_project_module(tmp_path, checker, arguments):
    (tmp_path / f"{checker}.py").write_text("print('SHADOW_EXECUTED')\nraise SystemExit(0)\n")
    (tmp_path / "a.py").write_text("import os\nx: int = 'wrong'\n")
    (tmp_path / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    result = run_python(["-m", checker, *arguments], tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode != 0
    assert "SHADOW_EXECUTED" not in result.stdout + result.stderr


def test_installed_pytest_preserves_project_imports(tmp_path):
    (tmp_path / "pytest.py").write_text("raise RuntimeError('shadowed')\n")
    (tmp_path / "application.py").write_text("VALUE = 42\n")
    (tmp_path / "test_application.py").write_text("import application\ndef test_value():\n    assert application.VALUE == 42\n")
    result = run_python(["-m", "pytest", "-q"], tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_trusted_checker_preserves_selected_matching_tests(tmp_path):
    (tmp_path / "test_selected.py").write_text("def test_selected():\n    assert True\n")
    (tmp_path / "test_unrelated.py").write_text("def test_unrelated():\n    assert False\n")
    result = run_python(["-m", "pytest", "-q", "test_selected.py"], tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode == 0 and "1 passed" in result.stdout
    assert "test_unrelated" not in result.stdout


def test_final_pytest_gate_rejects_shadowing_and_zero_collection(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.py").write_text("print('SHADOW_EXECUTED')\n")
    (project / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    change = ChangeSet.create(project)
    report = assess_completion(_completed_task(project), change, AtomicConfig())
    assert not report["ready_to_publish"] and report["tests"] == "failed"
    assert "SHADOW_EXECUTED" not in report["test_output_tail"]
    (project / "test_failure.py").unlink()
    # A plugin returning success despite collecting nothing must still fail.
    (project / "conftest.py").write_text("def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n")
    empty = ChangeSet.create(project)
    report = assess_completion(_completed_task(project), empty, AtomicConfig())
    assert not report["ready_to_publish"] and report["test_exit_code"] == 5


def test_docker_quality_launch_uses_isolated_installed_checker(tmp_path):
    command = docker_command("docker", "atomic-flow-checks:local", tmp_path, "test", ["-m", "pytest", "-q"])
    start = command.index("python")
    assert command[start + 1:start + 3] == ["-I", "-c"]
    assert command[-2:] == ["pytest", "-q"]


@pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1", reason="requires explicit real Docker integration run")
def test_real_docker_checker_rejects_shadow_module_and_protects_output(tmp_path):
    (tmp_path / "pytest.py").write_text("print('SHADOW_EXECUTED')\n")
    (tmp_path / "test_failure.py").write_text(f"def test_failure():\n    assert False, {CANARY!r}\n")
    result = run_python(["-m", "pytest", "-q"], tmp_path, config=ExecutionConfig(mode="docker"))
    assert result.returncode != 0 and "SHADOW_EXECUTED" not in result.stdout + result.stderr
    assert CANARY not in result.stdout + result.stderr
    assert "[REDACTED]" in result.stdout
