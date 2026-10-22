"""Opt-in OS/container acceptance checks; never use a live model or API credentials."""
import os
import subprocess
import time

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.execution import TaskCancelled, container_identity, run_python
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.verify import verify_edit
from atomic_flow.config import AtomicConfig, ExecutionConfig
from atomic_flow.tree.builder import build_tree, save_tree

pytestmark = pytest.mark.skipif(os.getenv("ATOMIC_FLOW_TEST_DOCKER") != "1", reason="explicit Docker integration required")


def test_project_pytest_runs_inside_container(tmp_path):
    (tmp_path / "sample.py").write_text("def sample():\n    return 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        f"import os\nfrom sample import sample\ndef test_sample():\n    assert os.getuid() == {container_identity().split(':')[0]} != 0\n    assert sample() == 2\n")
    verdict = verify_edit(tmp_path, "sample.py", execution=ExecutionConfig(mode="docker"))
    assert verdict.ok, verdict.message + verdict.test_output
    assert verdict.checks == ["syntax", "pytest"]


@pytest.mark.parametrize("expected", [1, 2])
def test_task_container_verification_controls_publication(tmp_path, expected):
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n")
    (project / "tests").mkdir()
    (project / "tests/test_sample.py").write_text(
        f"import os\nfrom sample import sample\ndef test_sample():\n    assert os.getuid() == {container_identity().split(':')[0]} != 0\n    assert sample() == {expected}\n")
    save_tree(project, build_tree(project))
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [
            {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample():\n    return 2"}},
            {"text": "Changed sample."},
        ],
        "critic": {"verdict": "ok"},
    })
    cfg = AtomicConfig(execution=ExecutionConfig(mode="docker"))
    cfg.task.max_replans = 0
    task = TaskRunner(cfg, project, backend, approval_callback=lambda *args: True).run_plan(
        "update sample", Plan(steps=[PlanStep(description="Update sample", verify_with="pytest", tools_needed=["edit_node"])]))
    assert task.status == ("done" if expected == 2 else "failed"), task.error
    assert f"return {expected}" in (project / "sample.py").read_text()
    if expected == 2:
        assert "pytest auto-run" in task.steps[0].result_summary
        assert task.completion_report["ready_to_publish"] and task.completion_report["tests"] == "passed"
    else:
        assert "rolled back" in task.error


@pytest.mark.parametrize("cancelled", [False, True])
def test_timeout_and_cancel_remove_real_container(tmp_path, monkeypatch, cancelled):
    import atomic_flow.agents.execution as execution

    names = []
    original = execution.docker_command

    def record(engine, image, workspace, name, args):
        names.append(name)
        return original(engine, image, workspace, name, args)

    monkeypatch.setattr(execution, "docker_command", record)
    start = time.monotonic()

    def check():
        if cancelled and time.monotonic() - start > 2:
            raise TaskCancelled("test")

    args = ["-c", "import time; print('started',flush=True); time.sleep(60)"]
    if cancelled:
        with pytest.raises(TaskCancelled):
            run_python(args, tmp_path, config=ExecutionConfig(mode="docker"), timeout=20, cancel_check=check)
    else:
        result = run_python(args, tmp_path, config=ExecutionConfig(mode="docker"), timeout=2)
        assert result.timed_out
    assert time.monotonic() - start < 30
    inspected = subprocess.run(["docker", "inspect", names[0]], capture_output=True, text=True, timeout=10)
    assert inspected.returncode != 0, "managed container survived cleanup"
