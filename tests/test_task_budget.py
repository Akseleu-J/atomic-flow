"""Accounting acceptance uses scripted responses only; no model/API access."""
import json
import subprocess
import sys

import pytest
from conftest import ScriptedBackend

from atomic_flow.agents.control import request_cancel, task_lease
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.base import ChatResult
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.tree.builder import build_tree, save_tree

PLAN = json.dumps({"steps": [{"description": "Report sample", "verify_with": "none"}]})


class Responses:
    name = "scripted"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.config = BackendConfig(model="test", price_in_per_mtok=1, price_out_per_mtok=1)

    def chat(self, messages, **kwargs):
        self.calls.append((messages, self.config.max_output_tokens, self.config.max_retries))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def reply(text=PLAN, cost=0.001):
    return ChatResult(text, tokens_in=100, tokens_out=50, cost_usd=cost)


def sample_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n")
    save_tree(project, build_tree(project))
    return project


def test_planning_repairs_are_persisted_and_charged_once(tmp_path):
    backend = Responses(reply("not JSON"), reply())
    task = TaskRunner(AtomicConfig(), tmp_path, backend).prepare("report sample", candidates=[], n_files=0)
    assert task.plan_ready and task.status == "paused"
    assert len(task.budget_events) == 2
    assert {event["phase"] for event in task.budget_events} == {"plan"}
    assert task.budget_spent == pytest.approx(0.002)
    assert load(task.id).budget_spent == pytest.approx(0.002)
    assert SpendLedger(10).today_total() == pytest.approx(0.002)
    assert all(max_output == 4096 and retries == 0 for _, max_output, retries in backend.calls)
    assert backend.config.max_output_tokens is None and backend.config.max_retries == 2


@pytest.mark.parametrize("kind", ["missing_prices", "task", "daily"])
def test_preflight_budget_refusal_sends_no_request(tmp_path, kind):
    cfg = AtomicConfig()
    backend = Responses(reply())
    if kind == "missing_prices":
        backend.config.price_in_per_mtok = None
    elif kind == "task":
        cfg.task.max_budget_usd = 0.0001
    else:
        cfg.budget.daily_limit_usd = 0.0001
    task = TaskRunner(cfg, tmp_path, backend).prepare("report sample", candidates=[], n_files=0)
    assert not task.plan_ready and task.status == "paused"
    assert not backend.calls and task.budget_spent == 0
    assert all(event.get("not_sent") for event in task.budget_events)


def test_call_limit_survives_resume_and_counts_planner(tmp_path):
    project = sample_project(tmp_path)
    backend = Responses(reply())
    cfg = AtomicConfig()
    cfg.task.max_model_calls = 1
    runner = TaskRunner(cfg, project, backend)
    task = runner.prepare("report sample", candidates=[], n_files=1)
    assert task.plan_ready
    task = runner.approve(task, runner.review(task)["digest"])
    task = runner.resume(task)
    assert task.status == "paused" and "call limit" in task.error
    assert len(backend.calls) == 1
    assert task.steps[0].status == "pending", task.steps[0].result_summary
    task = TaskRunner(cfg, project, backend).resume(task)
    assert task.status == "paused" and len(backend.calls) == 1


@pytest.mark.parametrize("response", [reply(cost=None), reply(cost=float("nan")), RuntimeError("network lost")])
def test_uncertain_charge_is_held_and_not_replayed(tmp_path, response):
    backend = Responses(response)
    runner = TaskRunner(AtomicConfig(), tmp_path, backend)
    task = runner.prepare("report sample", candidates=[], n_files=0)
    assert task.status == "paused" and task.budget_events[0]["status"] == "unknown"
    assert SpendLedger(10).today_total() > 0
    task = runner.resume(task)
    assert "unresolved model call" in task.error and len(backend.calls) == 1


def test_process_interruption_preserves_call_reservation(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)
    backend = Responses(KeyboardInterrupt())
    wrapped = TaskBudgetBackend(backend, task, AtomicConfig(), lambda: None)
    with pytest.raises(KeyboardInterrupt):
        wrapped.chat([{"role": "user", "content": "report"}])
    assert load(task.id).budget_events[0]["status"] == "in_flight"
    assert SpendLedger(10).today_total() > 0
    task = TaskRunner(AtomicConfig(), tmp_path, backend).resume(task)
    assert task.status == "paused" and len(backend.calls) == 1


def test_cancel_after_response_keeps_known_cost(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)

    class Cancelling(Responses):
        def chat(self, messages, **kwargs):
            result = super().chat(messages, **kwargs)
            request_cancel(task.id)
            return result

    from atomic_flow.agents.control import check_cancel

    wrapped = TaskBudgetBackend(Cancelling(reply()), task, AtomicConfig(), lambda: check_cancel(task.id))
    with pytest.raises(TaskCancelled):
        wrapped.chat([{"role": "user", "content": "report"}])
    assert load(task.id).budget_spent == 0.001
    assert SpendLedger(10).today_total() == 0.001


def test_explicit_zero_price_allows_free_backend_without_reported_cost(tmp_path):
    backend = Responses(reply(cost=None))
    backend.config.price_in_per_mtok = backend.config.price_out_per_mtok = 0
    task = TaskRunner(AtomicConfig(), tmp_path, backend).prepare("report sample", candidates=[], n_files=0)
    assert task.plan_ready and task.budget_spent == 0
    assert task.budget_events[0]["cost_usd"] == 0


def test_failed_orchestrator_response_does_not_lose_cost(tmp_path):
    project = sample_project(tmp_path)
    backend = ScriptedBackend({"parser": {}, "answerer": "invalid JSON"})
    cfg = AtomicConfig()
    cfg.task.max_replans = 0
    task = TaskRunner(cfg, project, backend).run_plan("report", Plan(steps=[PlanStep(description="Report sample", verify_with="none")]))
    assert task.status == "failed"
    assert task.budget_spent == pytest.approx(len(backend.calls) * 0.001)
    assert SpendLedger(10).today_total() == pytest.approx(task.budget_spent)


def test_replanner_calls_share_task_spend_and_daily_ledger(tmp_path):
    project = sample_project(tmp_path)

    class Replanning(ScriptedBackend):
        def chat(self, messages, **kwargs):
            if "<role>" not in messages[1]["content"]:
                self.calls.append(("planner", messages))
                return reply()
            return super().chat(messages, **kwargs)

    backend = Replanning({
        "parser": [{"task_type": "modify", "focus_terms": ["sample"]}, {}],
        "answerer": [
            {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample(:"}},
            {"text": "failed edit"}, {"text": "Report sample"},
        ],
        "critic": {"verdict": "ok"},
    })
    runner = TaskRunner(AtomicConfig(), project, backend, approval_callback=lambda *a: True)
    task = runner.run_plan(
        "update sample", Plan(steps=[PlanStep(description="Update sample", verify_with="py_compile")]))
    assert task.status == "paused" and not task.plan_approval
    task = runner.approve(task, runner.review(task)["digest"])
    task = runner.resume(task)
    assert task.status == "done", task.error
    assert task.replan_count == 1
    assert any(event["phase"] == "replan" for event in task.budget_events)
    assert task.budget_spent == pytest.approx(len(backend.calls) * 0.001)
    assert SpendLedger(10).today_total() == pytest.approx(task.budget_spent)


def test_recovery_cli_cannot_write_while_runner_holds_lease(tmp_path, capsys):
    import atomic_flow.cli as cli

    task = Task.new("report", tmp_path, [])
    save(task)
    with task_lease(task.id):
        assert cli.main(["task", "--recover", task.id]) == 2
    assert "already" in capsys.readouterr().err
    assert load(task.id).status == "running"


def test_daily_reservation_excludes_competing_processes(tmp_path):
    directory = tmp_path / "shared-ledger"
    code = """
import sys
from pathlib import Path
from atomic_flow.budget import SpendLedger
from atomic_flow.errors import BudgetExceeded
try:
    SpendLedger(0.01,Path(sys.argv[1])).reserve(call_id=sys.argv[2],reserved_usd=0.007)
except BudgetExceeded:
    print('denied')
else:
    print('admitted')
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(directory), str(i)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
    results = [p.communicate(timeout=30) for p in processes]
    assert all(p.returncode == 0 for p in processes), results
    assert sorted(out.strip() for out, _ in results) == ["admitted", "denied"]
    assert SpendLedger(0.01, directory).today_total() == 0.007


def test_budget_pause_restores_step_and_resumes_with_known_spend(tmp_path):
    project = sample_project(tmp_path)
    edit = {"tool": "edit_node", "args": {"node_id": "sample.py::sample", "new_source": "def sample():\n    return 2"}}
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [edit, {"text": "Done"}, edit, {"text": "Done"}],
        "critic": {"verdict": "ok"},
    })
    cfg = AtomicConfig()
    cfg.task.max_model_calls = 2
    task = TaskRunner(cfg, project, backend, approval_callback=lambda *a: True).run_plan(
        "update sample", Plan(steps=[PlanStep(description="Update sample", verify_with="py_compile")]))
    assert task.status == "paused" and task.steps[0].status == "pending"
    assert task.budget_spent == 0.002
    assert (project / "sample.py").read_text().endswith("return 1\n")
    from atomic_flow.agents.changeset import ChangeSet

    change = ChangeSet.open(project, task.changeset_id)
    assert (change.workspace / "sample.py").read_text().endswith("return 1\n")
    cfg.task.max_model_calls = 20
    task = TaskRunner(cfg, project, backend, approval_callback=lambda *a: True).resume(task)
    assert task.status == "done", task.error
    assert "return 2" in (project / "sample.py").read_text()
    assert task.budget_spent == pytest.approx(len(backend.calls) * 0.001)
    assert SpendLedger(10).today_total() == pytest.approx(task.budget_spent)


def test_missing_usage_does_not_fabricate_zero_cost(monkeypatch):
    import atomic_flow.backends.anthropic as anthropic
    import atomic_flow.backends.openai_compatible as openai

    cfg = BackendConfig(model="test", price_in_per_mtok=1, price_out_per_mtok=1)
    monkeypatch.setattr(openai, "post_json", lambda *a, **kw: {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(anthropic, "post_json", lambda *a, **kw: {"content": [{"type": "text", "text": "ok"}]})
    for backend in (openai.OpenAICompatibleBackend(cfg), anthropic.AnthropicBackend(cfg)):
        assert backend.chat([{"role": "user", "content": "test"}]).cost_usd is None


def test_dry_run_keeps_planning_task_and_cost(tmp_path, monkeypatch, capsys):
    import atomic_flow.cli as cli
    from atomic_flow.agents.task import list_tasks

    project = sample_project(tmp_path)
    backend = Responses(reply())
    monkeypatch.setattr(cli, "make_backend", lambda *a, **kw: backend)
    assert cli.main(["task", "report sample", "--dry-run", "--project", str(project)]) == 0
    tasks = list_tasks()
    assert len(tasks) == 1
    assert tasks[0].status == "paused" and tasks[0].plan_ready
    assert tasks[0].budget_spent == 0.001 and len(backend.calls) == 1
    assert "plan saved" in capsys.readouterr().out


def test_corrupt_daily_ledger_blocks_call_before_submission(tmp_path):
    ledger = SpendLedger(10)
    ledger.dir.mkdir(parents=True)
    ledger.path.write_text('{"partial":')
    backend = Responses(reply())
    task = TaskRunner(AtomicConfig(), tmp_path, backend).prepare("report sample", candidates=[], n_files=0)
    assert task.status == "paused" and not backend.calls
    assert "invalid spend ledger" in task.error


def test_cancel_after_reservation_sends_no_call_and_releases_hold(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)
    backend = Responses(reply())
    checks = []

    def cancel():
        checks.append(1)
        if len(checks) == 2:
            raise TaskCancelled("cancel before send")

    wrapped = TaskBudgetBackend(backend, task, AtomicConfig(), cancel)
    with pytest.raises(TaskCancelled):
        wrapped.chat([{"role": "user", "content": "report"}])
    assert not backend.calls
    assert load(task.id).budget_events[0]["not_sent"]
    assert SpendLedger(10).today_total() == 0


def test_saved_cost_cannot_disappear_on_resume(tmp_path):
    task = Task.new("report", tmp_path, [])
    task.budget_events = [{"status": "completed", "cost_usd": 1.0}]
    save(task)
    backend = Responses(reply())
    task = TaskRunner(AtomicConfig(), tmp_path, backend).resume(task)
    assert task.status == "paused" and "inconsistent" in task.error
    assert not backend.calls


def test_reported_overspend_is_accounted_and_blocks_publication(tmp_path):
    backend = Responses(reply(cost=3.0))
    task = TaskRunner(AtomicConfig(), tmp_path, backend).prepare("report sample", candidates=[], n_files=0)
    assert task.status == "paused" and "exceeded" in task.error
    assert task.budget_spent == 3.0 and not task.plan_ready
    assert SpendLedger(10).today_total() == 3.0


def test_kaggle_router_preserves_explicit_budget_prices(monkeypatch):
    import atomic_flow.backends.router as router

    class FakeKaggle:
        def __init__(self, **kwargs):
            self.config = BackendConfig(model="test")

    monkeypatch.setattr(router, "KaggleBackend", FakeKaggle)
    cfg = AtomicConfig()
    cfg.backends["kaggle"].price_in_per_mtok = cfg.backends["kaggle"].price_out_per_mtok = 0
    backend = router.make_backend(cfg, "kaggle")
    assert backend.config.price_in_per_mtok == backend.config.price_out_per_mtok == 0
