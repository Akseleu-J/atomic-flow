"""Cross-layer routing/accounting regressions; every model transport is scripted."""
import time

import pytest

from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.backends.base import ChatResult, ResponseShapeError
from atomic_flow.backends.http import HTTPStatusError, TransportError
from atomic_flow.backends.routing import QuotaStore, RoutingBackend
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, BackendConfig, ConversationConfig, RoutingConfig
from atomic_flow.errors import BudgetExceeded
from atomic_flow.llm.orchestrator import _Run
from atomic_flow.llm.prompts import PromptRegistry

MESSAGES = [{"role": "user", "content": "synthetic request"}]


class Backend:
    def __init__(self, name, *outcomes, free=False):
        self.name = name
        self.config = BackendConfig(model=name, base_url=f"https://{name}.invalid/v1",
                                    price_in_per_mtok=0 if free else 1, price_out_per_mtok=0 if free else 1,
                                    free_until=time.time() + 3600 if free else None)
        self.outcomes, self.calls = list(outcomes), []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def setup_task(tmp_path, routes, *, mode="capped_paid", budget=2):
    cfg = AtomicConfig()
    cfg.artifacts.save = False
    cfg.task.max_budget_usd = budget
    store = QuotaStore(tmp_path / "quotas")
    router = RoutingBackend(routes, RoutingConfig(mode=mode), ConversationConfig(), store)
    task = Task.new("synthetic", tmp_path, [])
    save(task)
    return TaskBudgetBackend(router, task, cfg, lambda: None), task, store


def test_each_physical_attempt_has_own_persisted_budget_and_one_ledger_charge(tmp_path):
    a = Backend("a", HTTPStatusError(429, "fixture refusal"))
    b = Backend("b", ChatResult("ok", 12, 3, 0.001))
    owner, task, store = setup_task(tmp_path, [a, b])
    response = owner.chat(MESSAGES)
    stored = load(task.id)
    assert response.resolved_backend == "b"
    assert len(stored.budget_events) == 2
    first, second = stored.budget_events
    assert first["request_outcome"] == "refused" and first["cost_usd"] == 0
    assert second["backend"] == "b" and second["model"] == "b"
    assert first["logical_call_id"] == second["logical_call_id"]
    assert first["route_attempt_id"] != second["route_attempt_id"]
    assert stored.budget_spent == pytest.approx(0.001)
    assert SpendLedger(10).today_total(strict=True) == pytest.approx(0.001)
    with store.state("a.invalid") as state:
        assert not state["pending"] and state["cooldown_until"] > time.time()


def test_task_budget_preflight_does_not_poison_provider_pending_slot(tmp_path):
    backend = Backend("a", ChatResult("unused", cost_usd=0.001))
    owner, _, store = setup_task(tmp_path, [backend], budget=0.000001)
    with pytest.raises(BudgetExceeded) as caught:
        owner.chat(MESSAGES)
    assert caught.value.request_outcome == "not_sent"
    assert not backend.calls
    with store.state("a.invalid") as state:
        assert not state["pending"]


def test_received_over_budget_cost_is_retained_and_releases_only_execution_slot(tmp_path):
    backend = Backend("free", ChatResult("blocked", cost_usd=0.01), free=True)
    owner, task, store = setup_task(tmp_path, [backend], mode="free_only")
    with pytest.raises(BudgetExceeded) as caught:
        owner.chat(MESSAGES)
    assert caught.value.request_outcome == "completed"
    assert caught.value.usage_result.cost_usd == 0.01
    assert load(task.id).budget_spent == pytest.approx(0.01)
    with store.state("free.invalid") as state:
        assert not state["pending"]


def test_unknown_submission_keeps_budget_hold_and_never_calls_fallback(tmp_path):
    a = Backend("a", TransportError("lost response"))
    b = Backend("b", ChatResult("unused", cost_usd=0.001))
    owner, task, store = setup_task(tmp_path, [a, b])
    with pytest.raises(BudgetExceeded):
        owner.chat(MESSAGES)
    assert len(a.calls) == 1 and not b.calls
    assert load(task.id).budget_events[0]["status"] == "unknown"
    with store.state("a.invalid") as state:
        assert len(state["pending"]) == 1


def test_malformed_received_response_settles_usage_and_preserves_failure(tmp_path):
    error = ResponseShapeError("invalid content", ChatResult("", 20, 5, 0.001))
    owner, task, store = setup_task(tmp_path, [Backend("a", error)])
    with pytest.raises(ResponseShapeError):
        owner.chat(MESSAGES)
    assert load(task.id).budget_events[0]["status"] == "completed"
    assert SpendLedger(10).today_total(strict=True) == pytest.approx(0.001)
    with store.state("a.invalid") as state:
        assert not state["pending"]


def test_outer_orchestrator_does_not_lose_cost_from_accounted_backend_failure(tmp_path):
    backend = Backend("free", ChatResult("blocked", 10, 1, 0.01), free=True)
    owner, task, _ = setup_task(tmp_path, [backend], mode="free_only")
    run = _Run(owner, PromptRegistry(tmp_path), SpendLedger(10), "synthetic", None)
    with pytest.raises(BudgetExceeded):
        run.chat(MESSAGES, "answerer")
    assert run.usage_calls[0]["cost_usd"] == pytest.approx(0.01)
    assert run.cost == pytest.approx(0.01)
    assert run.cost_missing is False
    assert load(task.id).budget_spent == SpendLedger(10).today_total(strict=True) == pytest.approx(0.01)


def test_ordinary_ask_budget_preflight_sends_nothing_and_releases_quota(tmp_path):
    backend = Backend("a", ChatResult("unused", cost_usd=0.001))
    store = QuotaStore(tmp_path / "quotas")
    router = RoutingBackend([backend], RoutingConfig(mode="capped_paid"), ConversationConfig(), store)
    run = _Run(router, PromptRegistry(tmp_path), SpendLedger(0.000001), "synthetic", None)
    with pytest.raises(BudgetExceeded) as caught:
        run.chat(MESSAGES, "answerer")
    assert caught.value.request_outcome == "not_sent" and not backend.calls
    with store.state("a.invalid") as state:
        assert not state["pending"]
