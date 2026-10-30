"""Regressions for provider usage/error contracts: numeric usage, Retry-After, trust boundaries.

Every case reproduces a defect from docs/audit-2026-10-23/provider-routing.md (B02/B03)
or pins the unchanged default behaviour the routing layer must not alter.
"""
import io
import json
import time
import urllib.error
from datetime import UTC, datetime
from email.utils import formatdate

import pytest

from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.backends import http
from atomic_flow.backends.base import ChatResult, ResponseShapeError
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.backends.usage import openai_usage
from atomic_flow.config import AtomicConfig, BackendConfig, load_config
from atomic_flow.errors import ConfigurationError

MESSAGE = [{"role": "user", "content": "report"}]


def backend_config(**overrides):
    values = {"model": "m", "base_url": "https://unit.example/v1", "api_key": "k"}
    values.update(overrides)
    return BackendConfig(**values)


# --- B02: provider usage normalization -----------------------------------------


@pytest.mark.parametrize("usage,expected_cost,expected_source", [
    ({"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 5}, 0.001, "provider"),
    ({"cost": 0.0, "prompt_tokens": 10, "completion_tokens": 5}, 0.0, "provider"),
    ({"cost_usd": 0.002, "prompt_tokens": 1, "completion_tokens": 1}, 0.002, "provider"),
    ({"total_cost_usd": 0.003, "prompt_tokens": 1, "completion_tokens": 1}, 0.003, "provider"),
    ({"input_tokens": 7, "output_tokens": 3}, None, "unknown"),
])
def test_openai_usage_number_forms(usage, expected_cost, expected_source):
    tokens_in, tokens_out, cost, source, _ = openai_usage({"usage": usage}, backend_config())
    assert (tokens_in, tokens_out) == (usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                                       usage.get("completion_tokens", usage.get("output_tokens", 0)))
    assert cost == expected_cost
    assert source == expected_source


def test_openai_usage_explicit_numeric_zero_is_known():
    _, _, cost, source, _ = openai_usage({"usage": {"cost": 0.0, "prompt_tokens": 1, "completion_tokens": 1}}, backend_config())
    assert cost == 0.0 and source == "provider"


def test_openai_usage_nanodollar_nested_wins_over_duplicates():
    usage = {"cost": {"input_tokens_cost_nanodollars": 1000, "output_tokens_cost_nanodollars": 500},
             "input_tokens_cost_nanodollars": 9999, "output_tokens_cost_nanodollars": 9999}
    _, _, cost, source, _ = openai_usage({"usage": usage}, backend_config())
    assert cost == pytest.approx(0.0000015) and source == "provider"


def test_openai_usage_nanodollar_top_level_only_and_partial_rejected():
    _, _, cost, source, _ = openai_usage(
        {"usage": {"input_tokens_cost_nanodollars": 1000, "output_tokens_cost_nanodollars": 500}}, backend_config())
    assert cost == pytest.approx(0.0000015) and source == "provider"
    _, _, cost, source, _ = openai_usage({"usage": {"cost": {"input_tokens_cost_nanodollars": 1000}}}, backend_config())
    assert cost is None and source == "invalid_provider_usage"


@pytest.mark.parametrize("bad", ["0.5", True, float("nan"), -0.1, None])
def test_openai_usage_malformed_cost_is_invalid_provider_usage(bad):
    usage = {"cost": bad, "prompt_tokens": 10, "completion_tokens": 5}
    tokens_in, tokens_out, cost, source, _ = openai_usage({"usage": usage}, backend_config())
    assert (tokens_in, tokens_out) == (10, 5)
    assert cost is None and source == "invalid_provider_usage"


def test_openai_usage_non_dict_usage_and_cache_tokens():
    _, _, cost, source, _ = openai_usage({"usage": [1, 2]}, backend_config())
    assert cost is None and source == "invalid_provider_usage"
    _, _, _, _, cached = openai_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 3}}}, backend_config())
    assert cached == 3


def test_openai_usage_configured_zero_prices_never_override_invalid_provider_cost():
    free = backend_config(price_in_per_mtok=0, price_out_per_mtok=0)
    _, _, cost, source, _ = openai_usage({"usage": {"cost": "bogus", "prompt_tokens": 1, "completion_tokens": 1}}, free)
    assert cost is None and source == "invalid_provider_usage"
    _, _, cost, source, _ = openai_usage({"usage": {"prompt_tokens": 1, "completion_tokens": 1}}, free)
    assert cost == 0.0 and source == "configured_estimate"


def test_openai_compatible_preserves_numeric_cost_and_attribution(monkeypatch):
    payload = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
               "usage": {"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: payload)
    result = OpenAICompatibleBackend(backend_config(), backend_name="openrouter").chat(MESSAGE)
    assert result.content == "hi" and result.cost_usd == 0.001 and result.cost_source == "provider"
    assert result.resolved_backend == "openrouter" and result.resolved_model == "m"


def test_openai_compatible_malformed_content_keeps_usage_via_response_shape_error(monkeypatch):
    payload = {"usage": {"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 5}}  # no choices
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: payload)
    with pytest.raises(ResponseShapeError) as excinfo:
        OpenAICompatibleBackend(backend_config()).chat(MESSAGE)
    assert excinfo.value.request_outcome == "completed"
    settled = excinfo.value.usage_result
    assert settled.cost_usd == 0.001 and settled.cost_source == "provider"
    assert (settled.tokens_in, settled.tokens_out) == (10, 5)


def test_openai_compatible_content_parts_are_joined(monkeypatch):
    payload = {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}],
               "usage": {"cost": 0.0, "prompt_tokens": 1, "completion_tokens": 1}}
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: payload)
    assert OpenAICompatibleBackend(backend_config()).chat(MESSAGE).content == "ab"


# --- accounting settles usage before a shape error propagates ------------------


class _ShapeBackend:
    name = "shape"

    def __init__(self):
        self.config = BackendConfig(model="m", price_in_per_mtok=1, price_out_per_mtok=1)

    def chat(self, messages, **kwargs):
        raise ResponseShapeError("malformed content", ChatResult("", 10, 5, 0.001, 1, cost_source="provider"))


class _BoolCostBackend(_ShapeBackend):
    def chat(self, messages, **kwargs):
        return ChatResult("x", 1, 1, True, 1)  # bool must never count as a valid charge


def test_response_shape_error_settles_usage_before_raising(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)
    wrapped = TaskBudgetBackend(_ShapeBackend(), task, AtomicConfig(), lambda: None)
    with pytest.raises(ResponseShapeError):
        wrapped.chat(MESSAGE)
    stored = load(task.id)
    assert stored.budget_events[0]["status"] == "completed"
    assert stored.budget_events[0]["cost_usd"] == pytest.approx(0.001)
    assert stored.budget_spent == pytest.approx(0.001)


def test_bool_cost_from_backend_stays_unknown(tmp_path):
    task = Task.new("report", tmp_path, [])
    save(task)
    wrapped = TaskBudgetBackend(_BoolCostBackend(), task, AtomicConfig(), lambda: None)
    with pytest.raises(Exception, match="no valid cost"):
        wrapped.chat(MESSAGE)
    stored = load(task.id)
    assert stored.budget_events[0]["status"] == "unknown"
    assert stored.budget_spent == 0


# --- B03: Retry-After is never shortened ---------------------------------------


def test_retry_after_seconds_contract():
    assert http.retry_after_seconds("120") == 120.0
    assert http.retry_after_seconds(1.5) == 1.5
    assert http.retry_after_seconds("0.5") == 0.5
    assert http.retry_after_seconds(None) is None
    assert http.retry_after_seconds("garbage") is None
    assert http.retry_after_seconds("-3") == 0.0
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC).timestamp()
    future = formatdate(now + 120, usegmt=True)
    past = formatdate(now - 60, usegmt=True)
    assert http.retry_after_seconds(future, now=now) == pytest.approx(120.0, abs=2)
    assert http.retry_after_seconds(past, now=now) == 0.0


def _post(url, body, headers, **kwargs):
    return http.post_json(url, body, headers, **kwargs)


def test_post_json_never_shortens_provider_cooldown(monkeypatch):
    calls = []

    def opener(request, **kw):
        calls.append(1)
        raise urllib.error.HTTPError("https://unit.example", 429, "Too Many Requests",
                                     {"Retry-After": "120"}, io.BytesIO(b"limit exceeded"))

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    started = time.monotonic()
    with pytest.raises(http.HTTPStatusError) as excinfo:
        _post("https://unit.example/v1/chat/completions", {"a": 1}, {}, timeout=5.0, retries=2)
    assert excinfo.value.status == 429 and excinfo.value.request_outcome == "refused"
    assert len(calls) == 1                      # no early retry that would shorten the cooldown
    assert time.monotonic() - started < 4.0     # and no 10-second clamped sleep either


def test_post_json_small_retry_after_still_retries(monkeypatch):
    calls = []

    def opener(request, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://unit.example", 429, "slow down",
                                         {"Retry-After": "0.05"}, io.BytesIO(b""))
        return io.BytesIO(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(http.urllib.request, "urlopen", opener)
    result = _post("https://unit.example/v1/chat/completions", {"a": 1}, {}, timeout=10.0, retries=1)
    assert result == {"ok": True} and len(calls) == 2


# --- routing stays a host-only decision ----------------------------------------


def test_project_config_cannot_set_routing(tmp_path):
    project = tmp_path / "proj"
    (project / ".atomic_flow").mkdir(parents=True)
    (project / ".atomic_flow" / "config.yaml").write_text(
        "routing:\n  mode: free_only\n  routes: [someone]\n", encoding="utf-8")
    assert load_config(project).routing.mode == "fixed"
    assert load_config(project).routing.routes == []


def test_routing_config_rejects_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AtomicConfig.model_validate({"routing": {"mode": "always"}})


def test_default_backend_construction_is_unchanged():
    config = AtomicConfig()
    config.backends["openai-compatible"] = backend_config()
    from atomic_flow.backends.router import make_backend
    backend = make_backend(config, "openai-compatible")
    assert backend.name == "openai-compatible"
    with pytest.raises(ConfigurationError):
        make_backend(config, "missing-backend-name")


def test_make_backend_builds_routing_only_from_named_host_routes():
    config = AtomicConfig()
    config.routing.mode = "free_only"
    config.routing.routes = ["free", "paid"]
    config.backends["free"] = backend_config(name="free", model="m1", price_in_per_mtok=0,
                                             price_out_per_mtok=0, free_until=time.time() + 3600)
    config.backends["paid"] = backend_config(name="paid", model="m2")
    from atomic_flow.backends.router import make_backend
    from atomic_flow.backends.routing import RoutingBackend
    routed = make_backend(config, "auto")
    assert isinstance(routed, RoutingBackend) and [b.name for b in routed.backends] == ["free", "paid"]
    with pytest.raises(ConfigurationError):
        make_backend(config, "auto", model="override")
    config.routing.routes.append("auto")
    with pytest.raises(ConfigurationError):
        make_backend(config, "auto")
