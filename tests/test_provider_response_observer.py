"""Diagnostics preserve paid wire bodies without changing provider admission."""
import copy

import pytest

from atomic_flow.backends.base import ResponseShapeError
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import BackendConfig


def config():
    return BackendConfig(type="openai-compatible", model="frozen", max_retries=0,
                         price_in_per_mtok=1, price_out_per_mtok=1)


def payload(message):
    return {"choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def test_observer_records_undeclared_call_while_transport_still_refuses_it(monkeypatch):
    body = payload({"tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: copy.deepcopy(body))
    seen = []
    backend = OpenAICompatibleBackend(config(), response_observer=seen.append)
    with pytest.raises(ResponseShapeError) as error:
        backend.chat([{"role": "user", "content": "public source"}])
    assert seen == [body] and error.value.usage_result.cost_usd == pytest.approx(.000012)
    assert "headers" not in seen[0] and "endpoint" not in seen[0]


def test_observer_mutation_cannot_change_decoded_response(monkeypatch):
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: payload({"content": "OK"}))
    backend = OpenAICompatibleBackend(config(), response_observer=lambda body: body.clear())
    assert backend.chat([{"role": "user", "content": "x"}]).content == "OK"


def test_failed_diagnostics_keep_known_usage_and_never_send_again(monkeypatch):
    calls = []

    def send(*args, **kwargs):
        calls.append(True)
        return payload({"content": "OK"})

    def fail(body):
        raise OSError("evidence unavailable")

    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", send)
    backend = OpenAICompatibleBackend(config(), response_observer=fail)
    with pytest.raises(OSError) as error:
        backend.chat([{"role": "user", "content": "x"}])
    assert error.value.usage_result.cost_usd == pytest.approx(.000012) and len(calls) == 1
