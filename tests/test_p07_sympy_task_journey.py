"""Synthetic journey exports check observations without prompts or source bodies."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from p07_sympy_task_journey import _observations


def artifact(tmp_path, calls):
    state = tmp_path / "state"
    target = state / "runs/req_fixture"
    target.mkdir(parents=True)
    (target / "meta.json").write_text(json.dumps({"tool_calls": calls, "messages": ["private prompt"]}))
    output = tmp_path / "output"
    output.mkdir()
    task = SimpleNamespace(steps=[SimpleNamespace(index=0, request_id="req_fixture")])
    return state, task, output


def test_export_retains_host_failure_proof_and_omits_prompt_source_and_full_spec(tmp_path):
    summary = {"check_runner": "sympy-native", "check_failed_completed": True,
               "checked_source_revision": "a" * 64, "check_spec": {"private": "not exported"},
               "stdout": "raw output", "source": "private source"}
    state, task, output = artifact(tmp_path, [{"tool": "run_command", "result_summary": summary}])
    rows = _observations(state, task, output)
    assert rows[0]["summary"] == {key: summary[key] for key in (
        "check_runner", "check_failed_completed", "checked_source_revision")}
    text = (output / "host-tool-observations.json").read_text()
    assert "private" not in text and "raw output" not in text


@pytest.mark.parametrize("calls", [
    [{"result_summary": "invalid"}],
    [{"result_summary": {"verification": {"check": "x" * 20_000}}}],
    [{"result_summary": {}}] * 257,
])
def test_invalid_or_unbounded_observations_refuse(tmp_path, calls):
    state, task, output = artifact(tmp_path, calls)
    with pytest.raises(ValueError):
        _observations(state, task, output)
