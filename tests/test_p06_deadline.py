"""Reviewed absolute task deadline survives replanning, expiry and restart."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_internal_replan import authorize, repaired_plan, staged
from test_repair_budget import MESSAGES, Responses

from atomic_flow.agents import envelope
from atomic_flow.agents.task import load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.errors import BudgetExceeded


def approved(tmp_path, monkeypatch):
    task, runner = staged(tmp_path)
    backend = Responses()
    runner.backend = backend
    task = authorize(runner, task)
    grant = task.plan_approval["internal_replan"]
    clock = SimpleNamespace(now=grant["deadline_at"] - 1)
    monkeypatch.setattr(envelope, "time", SimpleNamespace(time=lambda: clock.now))
    return task, runner, backend, clock


def test_deadline_is_bound_to_review_digest_and_task_creation(tmp_path):
    task, runner = staged(tmp_path)
    first = runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)
    assert first["internal_replan"]["deadline_at"] == task.created_at + runner.config.task.max_duration_seconds
    assert runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)["digest"] == first["digest"]
    runner.config.task.max_duration_seconds += 1
    assert runner.review(task, allowed_paths=["a.py"], allow_internal_replan=True)["digest"] != first["digest"]


def test_expired_resume_does_not_call_backend_or_execute_step(tmp_path, monkeypatch):
    task, runner, backend, clock = approved(tmp_path, monkeypatch)
    clock.now += 2
    monkeypatch.setattr(runner, "_execute_step", lambda *args: pytest.fail("expired task acted"))
    result = runner.resume(load(task.id))
    assert result.status == "paused" and "deadline" in result.error
    assert not backend.calls and not result.completion_report.get("ready_to_publish")
    again = TaskRunner(runner.config, tmp_path, backend).resume(load(task.id))
    assert again.status == "paused" and not backend.calls


def test_expiry_after_reservation_releases_unsent_hold(tmp_path, monkeypatch):
    task, runner, backend, clock = approved(tmp_path, monkeypatch)
    wrapped = TaskBudgetBackend(backend, task, runner.config, lambda: None)
    reserve = wrapped.ledger.reserve

    def expire(**kwargs):
        reserve(**kwargs)
        clock.now += 2

    monkeypatch.setattr(wrapped.ledger, "reserve", expire)
    with pytest.raises(BudgetExceeded, match="deadline"):
        wrapped.chat(MESSAGES)
    saved = load(task.id)
    assert not backend.calls and saved.budget_spent == 0
    assert saved.budget_events[-1]["not_sent"] and saved.budget_events[-1]["cost_usd"] == 0


def test_expiry_after_known_response_keeps_cost_and_blocks_next_call(tmp_path, monkeypatch):
    task, runner, backend, clock = approved(tmp_path, monkeypatch)
    original = backend.chat

    def reply(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.now += 2
        return result

    backend.chat = reply
    wrapped = TaskBudgetBackend(backend, task, runner.config, lambda: None)
    with pytest.raises(BudgetExceeded, match="deadline"):
        wrapped.chat(MESSAGES)
    saved = load(task.id)
    assert saved.budget_spent == 0.0005 and saved.budget_events[-1]["status"] == "completed"
    with pytest.raises(BudgetExceeded, match="deadline"):
        TaskBudgetBackend(backend, saved, runner.config, lambda: None).chat(MESSAGES)
    assert len(backend.calls) == 1


def test_replan_preserves_original_absolute_deadline(tmp_path, monkeypatch):
    task, runner, _backend, clock = approved(tmp_path, monkeypatch)
    deadline = task.plan_approval["internal_replan"]["deadline_at"]

    def execute(task_, step):
        if step.index == 0:
            raise runner._check_failure(step, "fixture failed check", "pytest", 1)

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", repaired_plan)
    result = runner.resume(task)
    assert result.status == "done", result.error
    assert result.replan_count == 1 and result.plan_approval["internal_replan"]["deadline_at"] == deadline
    assert clock.now < deadline


def test_legacy_envelope_requires_review_without_minting_deadline(tmp_path, monkeypatch):
    from atomic_flow.agents.plan_review import fingerprint

    task, runner, backend, _clock = approved(tmp_path, monkeypatch)
    legacy = deepcopy(task.plan_approval)
    legacy["internal_replan"]["version"] = 1
    legacy["internal_replan"].pop("deadline_at")
    legacy["digest"] = fingerprint({key: value for key, value in legacy.items() if key != "digest"})
    task.plan_approval = legacy
    save(task)
    result = runner.resume(task)
    assert result.status == "paused" and "fresh review" in result.error
    assert not backend.calls and "deadline_at" not in load(task.id).plan_approval["internal_replan"]


def test_expiry_after_final_check_blocks_publication(tmp_path, monkeypatch):
    from atomic_flow.agents.completion import assess_completion
    from atomic_flow.agents.source import digest

    task, runner, backend, clock = approved(tmp_path, monkeypatch)

    def execute(task_, step):
        path = runner.changeset.workspace / "a.py"
        path.write_text("A = 2\n", encoding="utf-8")
        runner.changeset.track("a.py", digest(path.read_bytes()))

    def final_check(*args, **kwargs):
        report = assess_completion(*args, **kwargs)
        assert report["ready_to_publish"]
        clock.now += 2
        return report

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.completion.assess_completion", final_check)
    result = runner.resume(task)
    assert result.status == "paused" and "deadline" in result.error
    assert (tmp_path / "a.py").read_text() == "A = 1\n"
    assert runner.changeset.state["status"] == "active" and not backend.calls


def test_expiry_during_replan_never_renews_automatic_authority(tmp_path, monkeypatch):
    task, runner, backend, clock = approved(tmp_path, monkeypatch)
    seen = []

    def execute(task_, step):
        seen.append(step.index)
        raise runner._check_failure(step, "fixture failure", "pytest", 1)

    def replan(*args, **kwargs):
        clock.now += 2
        return repaired_plan()

    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", replan)
    result = runner.resume(task)
    assert result.status == "paused" and "deadline" in result.error
    assert seen == [0] and not result.plan_approval and not backend.calls
