"""Pure/scripted harness checks. Actual Docker qualification runs only in CI."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_flow.config import AtomicConfig, BackendConfig


@pytest.fixture
def module(monkeypatch):
    # Exercise the public CLI tool's own import layout, without executing it.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    return importlib.import_module("p07_flat_task_journey")


def base_report(module, arm):
    return {"status": "scripted_public_coupled_obligation_journey_qualified", "task_status": "done",
            "replan_count": 1, "final_source": dict(module.EXPECTED_SOURCES), "obligation_digest": "a" * 64,
            "completion": {"ready_to_publish": True, "check_passed": True, "check_completed": True,
                           "check_obligation_digest": "a" * 64},
            "graph_sync": {"failed": False, "coverage_complete": True},
            "execution_strategy": "flat-baseline-v1" if arm == "flat" else None,
            "approved_runtime_digest": "d" * 64,
            "backend_roles": ["flat_turn"] * 6 if arm == "flat" else ["parser", "answerer", "host_planner", "critic"],
            "budget_events": [{"status": "completed", "cost_usd": 0, "cost_source": "scripted_zero_provider_calls"}] * 6,
            "remaining_scripted_answers": 0, "budget_spent_usd": 0}


def test_scripted_flat_backend_consumes_exact_six_actions_and_no_role_scaffold(module):
    originals = {path: text.replace("renamed", "original") for path, text in module.EXPECTED_SOURCES.items()}
    backend = module.FlatScriptedBackend(originals)
    prompts = [{"role": "system", "content": "flat JSON"}, {"role": "user", "content": "public issue"}]
    actions = []
    for _ in range(6):
        result = backend.chat(prompts)
        actions.append(json.loads(result.content))
        assert result.cost_usd == 0 and result.cost_source == "scripted_zero_provider_calls"
    assert [action.get("tool", "done") for action in actions] == [
        "run_command", "read_file", "read_file", "patch_files", "run_command", "done"]
    assert actions[0]["args"] == actions[4]["args"] == {"command": "check"}
    assert {patch["path"]: patch["new_text"] for patch in actions[3]["args"]["patches"]} == module.EXPECTED_SOURCES
    assert backend.roles() == ["flat_turn"] * 6
    with pytest.raises(ValueError):
        backend.chat(prompts)
    fresh = module.FlatScriptedBackend(originals)
    with pytest.raises(ValueError):
        fresh.chat([{"role": "system", "content": "flat"}, {"role": "user", "content": "<role>parser</role>"}])
    with pytest.raises(ValueError):
        fresh.chat(prompts, model="other")
    assert not fresh.calls


@pytest.mark.parametrize("arm", ["atomic", "flat"])
def test_qualification_requires_full_publication_fresh_obligation_and_explicit_graph_coverage(module, arm):
    good = base_report(module, arm)
    assert module.qualifies(good, arm)
    mutations = [
        ("task_status", "paused"), ("replan_count", 0), ("final_source", {}),
        ("completion", {**good["completion"], "check_completed": False}),
        ("completion", {**good["completion"], "check_obligation_digest": "b" * 64}),
        ("completion", {**good["completion"], "ready_to_publish": False}),
        ("graph_sync", {"failed": False}), ("graph_sync", {"failed": True, "coverage_complete": True}),
        ("graph_sync", {"failed": False, "coverage_complete": False}),
    ]
    for key, value in mutations:
        assert not module.qualifies({**good, key: value}, arm)


def test_flat_qualification_requires_exact_script_zero_accounting_and_strategy(module):
    good = base_report(module, "flat")
    for change in [
        {"execution_strategy": None}, {"approved_runtime_digest": None}, {"remaining_scripted_answers": 1}, {"backend_roles": ["parser"] * 6},
        {"budget_spent_usd": 0.001}, {"budget_events": []},
        {"budget_events": [{"status": "unknown", "cost_usd": None, "cost_source": "unknown"}] * 6},
    ]:
        assert not module.qualifies({**good, **change}, "flat")


@pytest.mark.parametrize("arm", ["atomic", "flat"])
def test_driver_uses_same_base_journey_with_optional_trusted_runner_and_clear_scope(module, tmp_path, monkeypatch, arm):
    output = tmp_path / arm
    captured = {}

    class FakeRunner:
        def __init__(self, config, project, backend, **kwargs):
            self.config, self.backend = config, backend
            self.check_obligation = kwargs["check_obligation"]
            captured["runner_strategy"] = config.task.execution_strategy

        def run_plan(self, *args, **kwargs):
            captured["run_options"] = kwargs
            if arm == "flat":
                messages = [{"role": "system", "content": "flat"}, {"role": "user", "content": "public fixture"}]
                for _ in range(6):
                    self.backend.chat(messages)
            return SimpleNamespace(id="task_" + "a" * 12,
                                   plan_approval={"internal_replan": {"runtime_digest": "d" * 64}})

    monkeypatch.setattr(module, "FlatTaskRunner", FakeRunner)
    monkeypatch.setattr(module, "TaskRunner", FakeRunner)
    monkeypatch.setattr(module, "_export_sessions", lambda target: [])
    monkeypatch.setattr(module, "SessionJournal", lambda task_id: SimpleNamespace(path=tmp_path / "absent-session"))

    def fake_journey(target, command, *, backend_factory, runner_factory):
        captured.update(command=command, has_backend_factory=backend_factory is not None,
                        has_runner_factory=runner_factory is not None)
        target.mkdir()
        originals = {path: text.replace("renamed", "original") for path, text in module.EXPECTED_SOURCES.items()}
        backend = backend_factory(originals=originals, step={}, output=target) if backend_factory else SimpleNamespace(
            config=BackendConfig(), calls=[("parser", [{"role": "user", "content": "scripted"}])])
        config = AtomicConfig()
        config.execution.image = "example/checks@sha256:" + "a" * 64
        config.task.max_model_calls, config.task.max_budget_usd, config.task.max_replans = 40, 1, 1
        obligation = SimpleNamespace(template=SimpleNamespace(test_paths=("tests/test_sample.py",)))
        selected = runner_factory(config, tmp_path, backend, approval_callback=lambda *args: True, check_obligation=obligation)
        selected.run_plan("public fixture", object(), allowed_paths=["a.py", "b.py"], allow_internal_replan=True)
        return base_report(module, arm)

    monkeypatch.setattr(module, "journey", fake_journey)
    report = module.matched_public_journey(output, arm)
    assert report["status"] == "scripted_public_" + arm + "_task_journey_qualified"
    assert report["provider_calls"] == 0 and report["model_generation"] is False
    assert report["hidden_oracle"] == "not_run" and report["measured_model_utility"] is False
    assert report["independent_requirement_success"] is False
    assert captured["command"] == "check" and captured["has_runner_factory"]
    assert captured["has_backend_factory"] is (arm == "flat")
    assert captured["run_options"] == {"allowed_paths": ["a.py", "b.py"], "allow_internal_replan": True}
    assert report["shared_limits"]["max_model_calls"] == 40
    assert report["shared_limits"]["max_budget_usd"] == 1
    assert report["shared_limits"]["ordinary_test_paths"] == ["tests/test_sample.py"]
    assert (output / "backend-requests.json").exists()
    assert json.loads((output / "result.json").read_text(encoding="utf-8")) == report


def test_unknown_arm_refuses_without_running_base_journey(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "journey", lambda *args, **kwargs: pytest.fail("unexpected base journey"))
    with pytest.raises(ValueError):
        module.matched_public_journey(tmp_path / "new", "other")


def test_matched_public_workflow_pins_actions_and_requests_only_scripted_arms():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/p07-matched-public-task-journey.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "arm: [atomic, flat]" in text
    assert "python tools/p07_flat_task_journey.py" in text
    assert "persist-credentials: false" in text
    assert "contents: read" in text
    assert "MODEL_PROXY" not in text and "KAGGLE" not in text
    actions = [line.split("@", 1)[1].strip() for line in text.splitlines() if "uses:" in line]
    assert len(actions) == 3 and all(len(sha) == 40 and set(sha) <= set("0123456789abcdef") for sha in actions)
