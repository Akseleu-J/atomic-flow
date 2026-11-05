"""Offline independent regressions for imported routing policy and deadlines."""
import hashlib
import json
import time

import pytest
from test_routing import MESSAGE, Route, routing

from atomic_flow.backends.base import ChatResult
from atomic_flow.backends.http import TransportError
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.backends.routing import QuotaStore, QuotaUnavailable, RoutePolicyError
from atomic_flow.errors import ConfigurationError
from atomic_flow.locking import exclusive_file


@pytest.mark.parametrize("cost", [0.01, None, True, float("nan")])
def test_free_only_rejects_actual_nonzero_or_unknown_response_after_accounting(tmp_path, cost):
    a = Route("a", prices=(0, 0), free_until=time.time() + 3600,
              outcome_queue=[ChatResult("answer", 1, 1, cost, 1)])
    b = Route("b", prices=(0, 0), free_until=time.time() + 3600)
    accounted = []

    def invoke(selected):
        result = selected.chat(MESSAGE)
        accounted.append(result.cost_usd)
        return result

    store = QuotaStore(tmp_path)
    with pytest.raises(RoutePolicyError) as error:
        routing([a, b], mode="free_only", store=store).run_attempts(invoke, MESSAGE)
    assert error.value.request_outcome == "completed"
    assert len(accounted) == 1 and a.calls == 1 and b.calls == 0
    with store.state("a.example") as state:
        assert state["pending"] == {}


def test_explicit_no_send_failure_refunds_local_capacity(tmp_path):
    store = QuotaStore(tmp_path)
    a = Route("a", requests_per_minute=1, requests_per_day=1)

    def refuse_before_transport(selected):
        raise TransportError("budget refused before transport", outcome="not_sent")

    with pytest.raises(TransportError):
        routing([a], store=store).run_attempts(refuse_before_transport, MESSAGE)
    with store.state("a.example") as state:
        assert state["pending"] == {}
        assert state["minute_requests"] == state["day_requests"] == state["minute_tokens"] == 0
    routing([a], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 1


def test_expired_deadline_after_reservation_never_invokes(tmp_path, monkeypatch):
    store = QuotaStore(tmp_path)
    original = store.reserve

    def delayed(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr("atomic_flow.backends.routing.time.monotonic", lambda: 1000.0)

    ticks = iter([0.0, 0.0])
    monkeypatch.setattr("atomic_flow.backends.routing.time.monotonic", lambda: next(ticks, 0.0))
    monkeypatch.setattr(store, "reserve", delayed)
    a = Route("a")
    with pytest.raises(QuotaUnavailable):
        routing([a], store=store, timeout_seconds=0.5).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 0
    with store.state("a.example") as state:
        assert state["pending"] == {} and state["minute_requests"] == 0


def test_quota_lock_wait_respects_route_deadline(tmp_path):
    store = QuotaStore(tmp_path)
    scope = "a.example"
    lock = tmp_path / (hashlib.sha256(scope.encode()).hexdigest() + ".lock")
    a = Route("a")
    with exclusive_file(lock):
        started = time.monotonic()
        with pytest.raises(QuotaUnavailable):
            routing([a], store=store, timeout_seconds=0.05).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
        assert time.monotonic() - started < 0.5
    assert a.calls == 0


@pytest.mark.parametrize("mutation", [[], {"minute": "corrupt"}, {"minute_requests": True}, {"pending": {"x": {"created_at": "bad"}}}])
def test_corrupt_quota_state_fails_closed_before_send(tmp_path, mutation):
    store = QuotaStore(tmp_path)
    with store.state("a.example"):
        pass
    target = tmp_path / (hashlib.sha256(b"a.example").hexdigest() + ".json")
    data = json.loads(target.read_text())
    if isinstance(mutation, dict):
        data.update(mutation)
    else:
        data = mutation
    target.write_text(json.dumps(data))
    a = Route("a")
    with pytest.raises(ConfigurationError):
        routing([a], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert a.calls == 0


def test_parameter_negotiation_retains_one_deadline(monkeypatch):
    from atomic_flow.backends.http import HTTPStatusError
    from atomic_flow.config import BackendConfig

    clock = [0.0]
    attempts = []

    def reject(*args, **kwargs):
        attempts.append(kwargs["timeout"])
        clock[0] += 2.0
        raise HTTPStatusError(400, "response_format is not supported")

    monkeypatch.setattr("atomic_flow.backends.openai_compatible.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", reject)
    backend = OpenAICompatibleBackend(BackendConfig(model="m", base_url="https://unit.example", timeout_seconds=1))
    with pytest.raises(TransportError, match="negotiation deadline") as error:
        backend.chat(MESSAGE, json_mode=True)
    assert error.value.request_outcome == "not_sent" and attempts == [1.0]


def test_route_deadline_is_rechecked_after_accounting_preflight(monkeypatch):
    from atomic_flow.config import BackendConfig

    calls = []
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: calls.append(kw))
    backend = OpenAICompatibleBackend(BackendConfig(model="m", base_url="https://unit.example", timeout_seconds=90))
    backend.route_deadline = 9.0
    with pytest.raises(TransportError) as error:
        backend.chat(MESSAGE)
    assert error.value.request_outcome == "not_sent" and calls == []


def test_routed_optional_parameter_refusal_has_no_hidden_send(monkeypatch):
    from atomic_flow.backends.http import HTTPStatusError
    from atomic_flow.config import BackendConfig

    calls = []

    def reject(*args, **kwargs):
        calls.append(1)
        raise HTTPStatusError(400, "response_format is not supported")

    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", reject)
    backend = OpenAICompatibleBackend(BackendConfig(model="m", base_url="https://unit.example"))
    backend.route_attempt_id = "route_test"
    with pytest.raises(HTTPStatusError):
        backend.chat(MESSAGE, json_mode=True)
    assert calls == [1]


@pytest.mark.parametrize("usage", [False, 0, [], "", None])
def test_falsy_malformed_usage_cannot_become_known_zero(usage):
    from atomic_flow.backends.usage import openai_usage
    from atomic_flow.config import BackendConfig

    config = BackendConfig(price_in_per_mtok=0, price_out_per_mtok=0)
    _, _, cost, source, _ = openai_usage({"usage": usage}, config)
    assert cost is None and source == "invalid_provider_usage"


def test_quota_cleanup_error_retains_received_usage_without_failover(tmp_path, monkeypatch):
    store = QuotaStore(tmp_path)
    a, b = Route("a"), Route("b")

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(store, "finish", fail)
    with pytest.raises(RoutePolicyError) as error:
        routing([a, b], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert error.value.usage_result.cost_usd == 0.001
    assert error.value.request_outcome == "completed"
    assert a.calls == 1 and b.calls == 0
    with store.state("a.example") as state:
        assert len(state["pending"]) == 1


def test_quota_cleanup_error_never_relabels_unknown_submission(tmp_path, monkeypatch):
    store = QuotaStore(tmp_path)
    a = Route("a", outcome_queue=[TransportError("unknown transport")])
    b = Route("b")

    def fail(*args, **kwargs):
        raise QuotaUnavailable("lock busy")

    monkeypatch.setattr(store, "finish", fail)
    with pytest.raises(TransportError, match="unknown transport") as error:
        routing([a, b], store=store).run_attempts(lambda selected: selected.chat(MESSAGE), MESSAGE)
    assert error.value.request_outcome == "unknown" and b.calls == 0


def test_anthropic_deadline_is_rechecked_after_accounting_preflight(monkeypatch):
    from atomic_flow.backends.anthropic import AnthropicBackend
    from atomic_flow.config import BackendConfig

    calls = []
    monkeypatch.setattr("atomic_flow.backends.anthropic.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("atomic_flow.backends.anthropic.post_json", lambda *a, **kw: calls.append(kw))
    backend = AnthropicBackend(BackendConfig(model="m", base_url="https://unit.example", timeout_seconds=90))
    backend.route_deadline = 9.0
    with pytest.raises(TransportError) as error:
        backend.chat(MESSAGE)
    assert error.value.request_outcome == "not_sent" and calls == []
