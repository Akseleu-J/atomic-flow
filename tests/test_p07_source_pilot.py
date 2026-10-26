"""The development pilot cannot admit uncalibrated work or unsafe actions."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in ("evaluation_stage4", "evaluation_provenance", "evaluation_live_pilot", "evaluation_p07_isolated", "evaluation_p07_source_pilot"):
    spec = importlib.util.spec_from_file_location(name, ROOT / ("tools/" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules.setdefault(name, module)
pilot = sys.modules["evaluation_p07_source_pilot"]


@pytest.mark.parametrize("action", [
    {"tool": "run_command", "args": {"command": "pytest"}},
    {"tool": "patch_file", "args": {"path": "tests/test_kind.py"}},
    {"tool": "read_file", "args": {"path": "../private.txt"}},
    {"done": True, "extra": "authority"}, [],
])
def test_baseline_cannot_expand_tool_or_source_envelope(action):
    with pytest.raises(ValueError):
        pilot.decode_action(json.dumps(action))


def test_missing_or_failed_calibration_cannot_start_paid_generation():
    task = {"instance_id": "sympy__sympy-21614", "base_commit": "a" * 40}
    with pytest.raises(ValueError, match="calibration"):
        pilot.admit_calibration({}, task)
    with pytest.raises(ValueError, match="calibration"):
        pilot.admit_calibration({**task, "calibrated": True, "model_calls": 0, "rows": []}, task)


def test_iterative_baseline_uses_exact_read_then_verified_edit(tmp_path):
    from atomic_flow.agents.source import digest
    from atomic_flow.backends.base import ChatResult

    work, output = tmp_path / "project", tmp_path / "output"
    work.mkdir()
    output.mkdir()
    path = work / pilot.SOURCE
    path.parent.mkdir(parents=True)
    original = "class Derivative(object):\n    value = 1\n"
    path.write_bytes(original.encode())
    actions = iter([
        {"tool": "read_file", "args": {"path": pilot.SOURCE}},
        {"tool": "patch_file", "args": {"path": pilot.SOURCE, "expected_hash": digest(original.encode()),
                                         "old_text": "value = 1", "new_text": "value = 2"}},
        {"done": True},
    ])

    class Backend:
        calls = []
        limits = {"max_calls": 20}

        def chat(self, messages):
            self.calls.append(len(messages))
            return ChatResult(content=json.dumps(next(actions)))

    backend = Backend()
    patch, detail = pilot.baseline({"problem_statement": "Update value", "base_commit": "a" * 40}, work, original, backend, output)
    assert len(backend.calls) == 3 and detail["generation_kind"] == "iterative_plain_agent"
    assert detail["model_claimed_done"] and "+    value = 2" in patch
    transcript = json.loads((output / "baseline-tools.json").read_text())
    assert transcript[0]["result"]["read_complete"] and transcript[1]["result"]["status"] == "applied"
