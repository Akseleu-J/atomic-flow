"""Targeted/syntax-only loop success never replaces available final regressions."""
import pytest
from test_completion_diagnostics import completed_task

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.execution import CommandResult
from atomic_flow.agents.source import digest
from atomic_flow.config import AtomicConfig


@pytest.mark.parametrize("exit_code,status", [(0, "passed"), (1, "failed"), (5, "unavailable")])
@pytest.mark.parametrize("test_path", ["tests/test_a.py", "arithmetic_test.py"])
def test_existing_tests_force_full_final_gate_after_syntax_only_edit(tmp_path, monkeypatch, exit_code, status, test_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / test_path).write_text("from a import A\ndef test_a(): assert A == 2\n", encoding="utf-8")
    change = ChangeSet.create(tmp_path)
    path = change.workspace / "a.py"
    path.write_text("A = 2\n", encoding="utf-8")
    change.track("a.py", digest(path.read_bytes()))
    calls = []

    def check(args, root, **kwargs):
        calls.append((args, root))
        return CommandResult(exit_code, "", "")

    monkeypatch.setattr("atomic_flow.agents.completion.run_python", check)
    report = assess_completion(completed_task(tmp_path), change, AtomicConfig())
    assert report["tests"] == status and report["ready_to_publish"] == (exit_code == 0)
    assert calls == [(["-m", "pytest", "-q", "--no-header", "-x"], change.workspace)]
    assert report["impact_check_plan"]["required_final_check"] == "full_pytest"
    assert report["impact_check_plan"]["final_gate_applicable"] is True
    assert (tmp_path / "a.py").read_text() == "A = 1\n"
