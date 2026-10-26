"""The development pilot cannot admit uncalibrated work or unsafe actions."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for name in ("evaluation_stage4", "evaluation_provenance", "evaluation_live_pilot", "evaluation_p07_isolated", "evaluation_p07_source_pilot"):
    spec = importlib.util.spec_from_file_location(name, ROOT / ("tools/" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules.setdefault(name, module)
pilot = sys.modules["evaluation_p07_source_pilot"]


def test_raw_response_survives_consumer_json_failure(tmp_path):
    from atomic_flow.backends.base import ChatResult
    from atomic_flow.config import BackendConfig

    class Transport:
        name = "scripted"
        config = BackendConfig(type="openai-compatible", model="frozen", max_retries=0, max_output_tokens=16)

        def chat(self, messages, **kwargs):
            return ChatResult(content="not JSON", tokens_in=10, tokens_out=2, cost_usd=.0000055, cost_source="provider")

    backend = pilot.SourcePilotBackend(Transport(), tmp_path, {"max_calls": 1, "max_input_tokens": 1000,
        "max_output_tokens": 16, "max_reported_cost_usd": .01}, time.monotonic() + 30)
    result = backend.chat([{"role": "user", "content": "public source"}])
    with pytest.raises(ValueError):
        pilot.decode_action(result.content)
    assert json.loads((tmp_path / "physical-response-1.json").read_text())["content"] == "not JSON"
    assert json.loads((tmp_path / "attempts.json").read_text())[0]["cost_usd"] == .0000055
    assert json.loads((tmp_path / "physical-request-1.json").read_text())[0]["content"] == "public source"


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


def test_read_only_context_does_not_expand_write_scope():
    assert pilot.decode_action(json.dumps({"tool": "read_file", "args": {"path": "sympy/core/basic.py"}}))["tool"] == "read_file"
    with pytest.raises(ValueError):
        pilot.decode_action(json.dumps({"tool": "patch_file", "args": {"path": "sympy/core/basic.py"}}))


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
