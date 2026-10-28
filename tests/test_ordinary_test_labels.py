"""Generic ordinary-test labels require immutable host execution authority."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_check_obligation_integration import config, fake_executor
from test_check_obligation_integration import workspace as workspace
from test_completion_diagnostics import completed_task
from test_envelope import APPROVAL

from atomic_flow.agents.changeset import ChangeSet, ChangeSetConflict
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.envelope import envelope_preserved
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.replan_lineage import lineage_preserved, verification_lineage_kind
from atomic_flow.agents.result import ToolResult
from atomic_flow.agents.step_evidence import build_receipt, receipt_matches
from atomic_flow.agents.task import Step, Task, load, save
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.agents.verify import verify_edit
from atomic_flow.config import AtomicConfig


def test_schema_defaults_and_persisted_labels_are_not_rewritten(tmp_path):
    assert AtomicConfig().task.final_check == "syntax"
    plan = Plan(steps=[PlanStep(description="Check ordinary obligation", verify_with="ordinary_test")])
    assert plan.steps[0].verify_with == "ordinary_test"
    for method in ("pytest", "ordinary_test"):
        step = Step(0, "Check existing obligation", verify_with=method, request_id="req_check")
        task = Task.new("Inspect", tmp_path, [step])
        save(task)
        assert load(task.id).steps[0].verify_with == method
        receipt = build_receipt(task.id, step, before_manifest={}, after_manifest={}, approved_scope=[])
        assert receipt["verification"]["method"] == method
        assert receipt_matches(receipt, step, {}, task_id=task.id)[0]
        other = deepcopy(step)
        other.verify_with = "pytest" if method == "ordinary_test" else "ordinary_test"
        assert not receipt_matches(receipt, other, {}, task_id=task.id)[0]


def test_unbound_plan_review_and_step_refuse_without_model_or_checker(tmp_path, monkeypatch):
    runner = TaskRunner(AtomicConfig(), tmp_path, None)
    step = Step(0, "Run ordinary check", verify_with="ordinary_test")
    task = Task.new("Inspect", tmp_path, [step])
    monkeypatch.setattr("atomic_flow.agents.task_loop.run_python", lambda *a, **kw: pytest.fail("unbound fallback"))
    with pytest.raises(ChangeSetConflict, match="host CheckObligation"):
        runner._review(task, [], False)
    with pytest.raises(StepFailure) as failure:
        runner._verify_step(step, SimpleNamespace(tool_calls=[], verdict="unverified"))
    assert failure.value.kind == "check_unavailable"
    cfg = AtomicConfig()
    cfg.task.final_check = "ordinary_test"
    with pytest.raises(ValueError, match="host CheckObligation"):
        TaskRunner(cfg, tmp_path, None)


@pytest.mark.parametrize("where", ["step", "config"])
def test_unbound_final_ordinary_check_never_uses_pytest_fallback(tmp_path, monkeypatch, where):
    cfg = AtomicConfig()
    if where == "config":
        cfg.task.final_check = "ordinary_test"
    task = completed_task(tmp_path, verifier="ordinary_test" if where == "step" else "none")
    monkeypatch.setattr("atomic_flow.agents.completion.run_python", lambda *a, **kw: pytest.fail("unbound final fallback"))
    result = assess_completion(task, ChangeSet.create(tmp_path), cfg)
    assert result["tests"] == "unavailable" and result["ready_to_publish"] is False


def test_bound_step_and_postedit_use_generic_verification_key(workspace, monkeypatch):
    root, bound = workspace
    calls = fake_executor(monkeypatch)
    runner = TaskRunner(config(bound), root, None, check_obligation=bound)
    step = Step(0, "Check ordinary obligation", verify_with="ordinary_test")
    runner._verify_step(step, SimpleNamespace(tool_calls=[], verdict="unverified"))
    verdict = verify_edit(root, "value.py", private_workspace=True, check_obligation=bound)
    assert verdict.ok and verdict.checks == ["syntax", "ordinary_test"] and len(calls) == 2
    result = ToolResult.from_payload({"status": "rolled_back", "checks": verdict.checks})
    assert result.summary()["verification"]["ordinary_test"] == "failed"


@pytest.mark.parametrize("method", ["ordinary_test", "pytest"])
def test_envelope_preserves_test_floor_without_canonicalizing_legacy(method):
    approval = deepcopy(APPROVAL)
    approval["internal_replan"]["required_verify"] = method
    original = deepcopy(approval)
    other = "pytest" if method == "ordinary_test" else "ordinary_test"
    assert envelope_preserved(approval, [Step(0, "Check", tools_needed=["edit_node", "run_command"],
                                            verify_with=other)], failure="", budget_spent=0)[0]
    assert not envelope_preserved(approval, [Step(0, "Check", tools_needed=["edit_node", "run_command"],
                                                verify_with="py_compile")], failure="", budget_spent=0)[0]
    assert approval == original


@pytest.mark.parametrize("original,replacement", [("pytest", "ordinary_test"), ("ordinary_test", "pytest")])
def test_mixed_labels_preserve_lineage_only_for_bound_ordinary_tests(original, replacement):
    def decision(bound):
        return lineage_preserved(
            {"required_checks": {verification_lineage_kind(original, host_ordinary_test=bound)}},
            [{"checks": [verification_lineage_kind(replacement, host_ordinary_test=bound)]}])[0]
    assert decision(True) and not decision(False)
    assert verification_lineage_kind("py_compile", host_ordinary_test=True) == "py_compile"
