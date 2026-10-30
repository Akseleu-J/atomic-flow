"""Flat TaskRunner integration uses scripted inference and mocked checks only."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from atomic_flow import check_execution
from atomic_flow.agents.envelope import runtime_digest
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Step, Task, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.tools.run_command import RunCommandTool
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.backends.base import ChatResult
from atomic_flow.check_execution import CheckReceipt, _runner_receipt
from atomic_flow.check_obligation import CheckObligation, obligation_binding
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, BackendConfig, is_trusted, trust_project
from atomic_flow.sessions import SessionJournal
from atomic_flow.tree.builder import build_tree, save_tree
from tools.p07_flat_task_runner import FlatTaskRunner

ORIGINALS = {"a.py": "from b import original\n\ndef call():\n    return original()\n",
             "b.py": "def original():\n    return 42\n"}


class Scripted:
    name = "flat-scripted"

    def __init__(self, actions):
        self.actions, self.calls = list(actions), []
        self.config = BackendConfig(model="scripted", max_input_tokens=200_000, max_output_tokens=4096,
                                    max_retries=0, price_in_per_mtok=0, price_out_per_mtok=0)

    def chat(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        if not self.actions:
            raise AssertionError("unexpected parser/critic/planner/followup")
        answer = self.actions.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ChatResult(json.dumps(answer), 10, 5, 0, 0, cost_source="scripted_zero_provider_calls")


def call(tool, **args):
    return {"tool": tool, "args": args}


def repair_actions():
    patches = [{"path": relative, "expected_hash": hashlib.sha1(text.encode()).hexdigest(),
                "old_text": text, "new_text": text.replace("original", "renamed")}
               for relative, text in ORIGINALS.items()]
    return [call("run_command", command="check"), call("read_file", path="a.py"), call("read_file", path="b.py"),
            call("patch_files", patches=patches), call("run_command", command="check"),
            {"done": True, "summary": "Request final host verification."}]


@pytest.fixture
def host(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for relative, text in ORIGINALS.items():
        (project / relative).write_bytes(text.encode())
    (project / "tests").mkdir()
    (project / "tests/test_sample.py").write_bytes(b"def test_sample():\n    assert True\n")
    save_tree(project, build_tree(project))
    trust_project(project)
    spec = CheckSpec("public_flat_integration", "a" * 40, "b" * 40, check_source_revision(project),
                     "example/checks@sha256:" + "c" * 64, "/usr/local/bin/python", "pytest",
                     ("tests/test_sample.py",))
    bound = CheckObligation.seal(spec, project, environment_digest="d" * 64, deadline_at=time.time() + 600)
    config = AtomicConfig()
    config.task.execution_strategy = "flat-baseline-v1"
    config.task.max_replans = 1
    config.task.max_model_calls = 20
    config.task.final_check = "ordinary_test"
    config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", spec.image
    checks = []

    def checker(current, workspace, *, cancel_check=None):
        passed = all("original" not in (workspace / relative).read_text(encoding="utf-8") for relative in ORIGINALS)
        checks.append((current, passed))
        code = int(not passed)
        data = {"nonce": "e" * 32, "root": current.target_root, "interpreter": current.interpreter,
                "interpreter_resolved": "/usr/local/bin/python3.11", "pytest_module": "/opt/checker/pytest.py",
                "pytest_version": "8.3.0", "collected": 1, "files": sorted(current.test_paths),
                "skipped": 0, "failed": code, "finished": True, "call_failed": code,
                "setup_failed": 0, "teardown_failed": 0, "exit_code": code}
        from atomic_flow.agents.execution import CommandResult
        result = CommandResult(code, "ATOMIC_CHECK_SPEC_RECEIPT:" + json.dumps(data), "")
        receipt = _runner_receipt(result, current, "e" * 32)
        assert receipt is not None
        return CheckReceipt(current.digest, current.source_revision, current.source_revision, passed, receipt, result)

    monkeypatch.setattr(check_execution, "run_check_spec", checker)
    return SimpleNamespace(project=project, config=config, bound=bound, checks=checks)


def runner(host, backend):
    return FlatTaskRunner(host.config, host.project, backend, approval_callback=lambda *args: True,
                          check_obligation=host.bound)


def test_full_current_failure_deterministic_retry_multifile_final_gate_and_publication(host, monkeypatch):
    monkeypatch.setattr(TaskRunner, "_read_plan", lambda *args: pytest.fail("Atomic graph hints entered baseline"))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task",
                        lambda *args, **kwargs: pytest.fail("model planner entered baseline"))
    backend = Scripted(repair_actions())
    finished = runner(host, backend).run_flat("Rename original to renamed consistently in the public source",
                                            allowed_paths=["a.py", "b.py"])
    assert finished.status == "done", finished.error
    assert finished.replan_count == 1
    assert finished.completion_report["ready_to_publish"] is True
    assert finished.completion_report["check_obligation_digest"] == host.bound.digest
    assert len(backend.calls) == 6 and len(finished.budget_events) == 6
    assert all(event["status"] == "completed" for event in finished.budget_events)
    assert finished.budget_spent == 0 and finished.repair_budget
    assert finished.steps[0].failure["evidence"]["check_failed_completed"] is True
    assert finished.steps[0].status == "superseded"
    assert finished.steps[-1].evidence["verification"]["outcome"] == "passed"
    assert all((host.project / relative).read_text(encoding="utf-8") == text.replace("original", "renamed")
               for relative, text in ORIGINALS.items())
    assert finished.graph_sync.get("failed") is False
    assert all("<role>" not in json.dumps(messages) and "Host read plan" not in json.dumps(messages)
               for messages in backend.calls)
    assert "Historical host observations" in backend.calls[1][1]["content"]
    events = SessionJournal(finished.id).read()
    assert not events.unresolved_call_ids
    tool_events = [event for event in events.events if event["event_type"] in {"tool_request", "tool_result"}]
    for position in range(0, len(tool_events), 2):
        assert tool_events[position]["event_type"] == "tool_request"
        assert tool_events[position + 1]["event_type"] == "tool_result"
        assert tool_events[position]["call_id"] == tool_events[position + 1]["call_id"]


def test_strategy_binding_is_explicit_and_runner_types_cannot_swap(host):
    backend = Scripted([])
    with pytest.raises(ValueError, match="strategy"):
        TaskRunner(host.config, host.project, backend, check_obligation=host.bound)
    ordinary = host.config.model_copy(deep=True)
    ordinary.task.execution_strategy = None
    with pytest.raises(ValueError, match="strategy"):
        FlatTaskRunner(ordinary, host.project, backend, check_obligation=host.bound)
    flat = runner(host, backend)
    host.config.task.max_model_calls = 99
    assert flat.config.task.max_model_calls == 20
    with pytest.raises(ValueError, match="without model planning"):
        flat.prepare("public query", candidates=[], n_files=3)


def test_none_strategy_preserves_historical_runtime_digest_bytes(host):
    config = host.config.model_copy(deep=True)
    config.task.execution_strategy = None
    settings = config.model_dump(mode="json")
    settings["task"].pop("execution_strategy")
    for entry in settings["backends"].values():
        entry.pop("api_key", None)
    legacy = {"config": settings, "backend": None, "trusted": is_trusted(host.project), "policies": [None, None],
              "check_obligation": obligation_binding(host.bound)}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, allow_nan=False).encode()).hexdigest()
    assert runtime_digest(config, host.project, check_obligation=host.bound) == expected
    assert runtime_digest(host.config, host.project, check_obligation=host.bound) != expected


def test_schema1_or_missing_replan_envelope_cannot_enter_flat_execution(host):
    backend = Scripted([])
    flat = runner(host, backend)
    task = Task.new("Public issue", host.project, [Step(0, "Resolve public issue", verify_with="ordinary_test")])
    save(task)
    with pytest.raises(ValueError, match="schema-2"):
        flat.review(task, allowed_paths=["a.py", "b.py"], allow_internal_replan=False)
    from atomic_flow.agents.plan_review import review_payload
    task.plan_approval = review_payload(task, host.project, ["a.py", "b.py"], check_obligation=host.bound)
    save(task)
    result = flat.resume(task)
    assert result.status == "paused" and "schema-2" in result.error
    assert backend.calls == []


def test_call_cap_without_done_never_becomes_success_or_publication(host):
    host.config.task.max_model_calls = 1
    backend = Scripted([call("read_file", path="a.py"), {"done": True}])
    result = runner(host, backend).run_flat("Resolve public issue", allowed_paths=["a.py", "b.py"])
    assert result.status == "paused" and len(backend.calls) == 1
    assert not result.completion_report.get("ready_to_publish")
    assert all((host.project / relative).read_text(encoding="utf-8") == text for relative, text in ORIGINALS.items())


def test_unknown_model_charge_and_unknown_tool_effect_halt_without_retry(host, monkeypatch):
    backend = Scripted([RuntimeError("unknown physical response"), {"done": True}])
    result = runner(host, backend).run_flat("Resolve public issue", allowed_paths=["a.py", "b.py"])
    assert result.status == "paused" and len(backend.calls) == 1
    assert result.budget_events[0]["status"] == "unknown"
    assert SessionJournal(result.id).read().unresolved_call_ids

    def broken(*args):
        raise RuntimeError("unknown checker execution effect")
    monkeypatch.setattr(RunCommandTool, "run", broken)
    backend = Scripted([call("run_command", command="check"), {"done": True}])
    result = runner(host, backend).run_flat("Resolve public issue", allowed_paths=["a.py", "b.py"])
    assert result.status == "paused" and len(backend.calls) == 1
    assert SessionJournal(result.id).read().unresolved_call_ids
    assert not result.completion_report.get("ready_to_publish")


def test_default_replacement_hook_calls_same_model_planner_arguments(host, monkeypatch):
    ordinary = host.config.model_copy(deep=True)
    ordinary.task.execution_strategy = None
    base = TaskRunner(ordinary, host.project, Scripted([]), check_obligation=host.bound)
    base.changeset = SimpleNamespace(workspace=host.project)
    base.task_backend = object()
    step = Step(0, "failed step", verify_with="ordinary_test", result_summary="host failure", status="failed")
    task = Task.new("public query", host.project, [step])
    expected = Plan(steps=[PlanStep(description="Reviewed remaining work")])
    seen = {}
    def plan(query, **kwargs):
        seen.update(query=query, **kwargs)
        return expected
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", plan)
    assert base._choose_replacement_plan(task, step, "requirements", "planning") is expected
    assert seen == {"query": task.query, "completed": [], "failed_description": step.description,
                    "verify_with": step.verify_with, "error": step.result_summary, "remaining": [],
                    "project_root": host.project, "backend": base.task_backend, "settings_project": host.project,
                    "requirements_context": "requirements", "planning_context": "planning"}


def test_flat_replacement_keeps_scope_criteria_and_ancestor_goal_bounds(host):
    from atomic_flow.agents.goals import Goal
    flat = runner(host, Scripted([]))
    step = Step(0, "Public issue", tools_needed=["patch_files", "run_command"], verify_with="ordinary_test",
                status="failed", parent_goal="p0:fix", acceptance_criteria=["Preserve behavior"],
                scope_paths=[], linked_code_nodes=["a.py::call"])
    task = Task.new("Public issue", host.project, [step])
    task.goals = [Goal("p0:fix", "Fix public behavior", acceptance_criteria=["Public issue resolved"],
                       scope_paths=["a.py", "b.py"], max_model_calls=10, max_budget_usd=1.0)]
    replacement = flat._choose_replacement_plan(task, step, "", "")
    assert replacement.steps[0].scope_paths == []
    assert replacement.steps[0].acceptance_criteria == step.acceptance_criteria
    assert replacement.steps[0].linked_code_nodes == step.linked_code_nodes
    assert replacement.steps[0].parent_goal == "fix"
    assert replacement.goals[0].max_model_calls == 10 and replacement.goals[0].max_budget_usd == 1.0
    assert replacement.goals[0].scope_paths == ["a.py", "b.py"]


def test_stale_candidate_failure_after_rollback_still_refuses_for_both_runners(host):
    from atomic_flow.agents.step_evidence import manifest_digest
    from atomic_flow.agents.verify import check_source_snapshot
    current = manifest_digest(check_source_snapshot(host.project))
    task = Task.new("Public issue", host.project, [])
    proof = {"version": 2, "check": "ordinary_test", "exit_code": 1, "step_index": 0, "started_at": 1.0,
             "source_revision": "f" * 64, "checked_source_revision": "f" * 64, "outcome": "failed",
             "runner": "pytest", "check_completed": True, "check_failed_completed": True,
             "check_obligation_digest": host.bound.digest, "check_spec_digest": "a" * 64,
             "check_receipt_digest": "b" * 64}
    step = Step(0, "Failed candidate", status="failed", started_at=1.0)
    step.failure = {"kind": "deterministic_test_failure", "evidence": proof, "rollback_revision": current}
    task.steps = [step]
    for constructor in (TaskRunner, FlatTaskRunner):
        config = host.config.model_copy(deep=True)
        config.task.execution_strategy = None if constructor is TaskRunner else "flat-baseline-v1"
        obj = constructor(config, host.project, Scripted([]), check_obligation=host.bound)
        obj.changeset = SimpleNamespace(validate_workspace=lambda: check_source_snapshot(host.project))
        result = obj._replan_failed_step(task, step)
        assert result.status == "paused" and "contract changed" in result.error
