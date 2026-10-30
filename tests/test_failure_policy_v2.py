"""Strict v2 ordinary failure evidence preserves legacy v1 semantics."""
from types import SimpleNamespace

import pytest

from atomic_flow.agents.failure_policy import CONTINUE_REPAIR, STOP, classify, validate_failure_evidence
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.task_loop import TaskRunner


def evidence(version=2, **changes):
    value = {"version": version, "check": "ordinary_test" if version == 2 else "pytest",
             "exit_code": 1, "step_index": 0, "started_at": 100.0, "source_revision": "a" * 64,
             "checked_source_revision": "a" * 64, "outcome": "failed"}
    if version == 2:
        value.update(runner="sympy-native", check_completed=True, check_failed_completed=True,
                     check_obligation_digest="b" * 64, check_spec_digest="c" * 64, check_receipt_digest="d" * 64)
    return value | changes


@pytest.mark.parametrize("runner", ["pytest", "sympy-native"])
def test_completed_native_or_pytest_failure_has_explicit_ordinary_kind(runner):
    value = evidence(runner=runner)
    validate_failure_evidence(value)
    decision = classify({"kind": "deterministic_test_failure", "evidence": value, "remaining_budget_usd": 4})
    assert decision["action"] == CONTINUE_REPAIR and decision["repair_budget_usd"] == 1
    assert classify({"kind": "deterministic_check_failure", "evidence": value,
                     "remaining_budget_usd": 4})["action"] == STOP


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 3}, {"check": "pytest"}, {"check": "syntax"}, {"exit_code": True},
    {"exit_code": 0}, {"step_index": True}, {"step_index": -1}, {"started_at": True},
    {"started_at": float("nan")}, {"started_at": -1}, {"outcome": "passed"},
    {"source_revision": "f" * 64}, {"checked_source_revision": "f" * 64},
    {"runner": "shell"}, {"runner": ["pytest"]}, {"check_completed": 1},
    {"check_failed_completed": 1}, {"check_completed": False}, {"check_failed_completed": False},
    {"check_obligation_digest": "b" * 63}, {"check_spec_digest": "C" * 64},
    {"check_receipt_digest": True}, {"extra": "field"},
])
def test_v2_invalid_shapes_types_digests_and_replayed_revisions_stop(changes):
    value = evidence(**changes)
    with pytest.raises(ValueError):
        validate_failure_evidence(value)
    assert classify({"kind": "deterministic_test_failure", "evidence": value,
                     "remaining_budget_usd": 4})["action"] == STOP


@pytest.mark.parametrize("missing", list(evidence()))
def test_every_v2_field_is_required(missing):
    value = evidence()
    del value[missing]
    with pytest.raises(ValueError):
        validate_failure_evidence(value)


@pytest.mark.parametrize("check", ["pytest", "syntax", "ruff", "mypy"])
def test_v1_whitelist_and_kind_classification_unchanged(check):
    value = evidence(version=1, check=check, checked_source_revision="f" * 64)
    validate_failure_evidence(value)
    kind = "deterministic_test_failure" if check == "pytest" else "deterministic_check_failure"
    assert classify({"kind": kind, "evidence": value, "remaining_budget_usd": 4})["action"] == CONTINUE_REPAIR
    with pytest.raises(ValueError):
        validate_failure_evidence(value | {"runner": "pytest"})


def test_v1_does_not_accept_new_ordinary_label():
    with pytest.raises(ValueError):
        validate_failure_evidence(evidence(version=1, check="ordinary_test"))


@pytest.mark.parametrize("alias", ["check", "pytest"])
@pytest.mark.parametrize("runner", ["pytest", "sympy-native"])
def test_host_guard_emits_v2_only_after_complete_current_observation(monkeypatch, alias, runner):
    manifest = {"module.py": "f" * 40}
    revision = manifest_digest(manifest)
    observation = {"check_runner": runner, "check_obligation_digest": "b" * 64,
                   "check_spec_digest": "c" * 64, "check_receipt_digest": "d" * 64}
    host = SimpleNamespace(check_obligation=object(),
                           changeset=SimpleNamespace(validate_workspace=lambda: manifest))
    step = SimpleNamespace(index=0, started_at=100.0)
    seen = []
    def matched(obligation, actual, current):
        seen.append((actual, current))
        return actual is observation and current == revision
    monkeypatch.setattr("atomic_flow.check_obligation.failed_observation_matches", matched)
    failure = TaskRunner._check_failure(host, step, "native assertion failed", alias, 1,
                                       checked_revision=revision, check_observation=observation)
    assert seen == [(observation, revision)]
    assert failure.kind == "deterministic_test_failure"
    assert failure.evidence == evidence(runner=runner, source_revision=revision, checked_source_revision=revision)
    validate_failure_evidence(failure.evidence)
    stale = TaskRunner._check_failure(host, step, "stale", alias, 1,
                                     checked_revision="e" * 64, check_observation=observation)
    assert stale.kind == "check_unavailable" and stale.evidence == {}


def test_unbound_and_nonordinary_checks_keep_v1():
    manifest = {"module.py": "f" * 40}
    revision = manifest_digest(manifest)
    step = SimpleNamespace(index=0, started_at=100.0)
    for obligation, check in ((None, "pytest"), (object(), "syntax"), (object(), "ruff")):
        host = SimpleNamespace(check_obligation=obligation,
                               changeset=SimpleNamespace(validate_workspace=lambda: manifest))
        failure = TaskRunner._check_failure(host, step, "failed", check, 1, checked_revision=revision)
        assert failure.evidence["version"] == 1 and failure.evidence["check"] == check
        validate_failure_evidence(failure.evidence)


def test_completed_native_failure_roundtrips_durable_task_state(tmp_path):
    from atomic_flow.agents.task import Step, Task, load, save
    step = Step(0, "Native ordinary check", started_at=100.0, status="failed")
    step.failure = {"kind": "deterministic_test_failure", "evidence": evidence(),
                    "rollback_revision": "a" * 64}
    task = Task.new("Inspect native check", tmp_path, [step])
    save(task)
    restored = load(task.id)
    assert restored.steps[0].failure == step.failure
    assert classify({**restored.steps[0].failure, "remaining_budget_usd": 4})["action"] == CONTINUE_REPAIR


@pytest.mark.parametrize("mismatch", ["missing", "digest", "runner", "revision"])
def test_replan_rejects_previous_contract_evidence_before_any_model_call(tmp_path, monkeypatch, mismatch):
    from atomic_flow.agents.task import Step, Task
    from atomic_flow.config import AtomicConfig
    manifest = {"module.py": "f" * 40}
    revision = manifest_digest(manifest)
    proof = evidence(source_revision=revision, checked_source_revision=revision)
    bound = SimpleNamespace(digest=proof["check_obligation_digest"], template=SimpleNamespace(runner="sympy-native"))
    if mismatch == "missing":
        bound = None
    elif mismatch == "digest":
        bound.digest = "f" * 64
    elif mismatch == "runner":
        bound.template.runner = "pytest"
    else:
        proof["source_revision"] = proof["checked_source_revision"] = "e" * 64
    step = Step(0, "Failed native test", status="failed", started_at=100.0)
    step.failure = {"kind": "deterministic_test_failure", "evidence": proof, "rollback_revision": revision}
    task = Task.new("Inspect check", tmp_path, [step])
    host = SimpleNamespace(config=AtomicConfig(), check_obligation=bound,
                           changeset=SimpleNamespace(validate_workspace=lambda: manifest))
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("stale check invoked model"))
    result = TaskRunner._replan_failed_step(host, task, step)
    assert result.status == "paused" and "contract changed" in result.error
