"""Anthropic token breakdown and content failures retain conservative accounting."""
import pytest

from atomic_flow.backends.anthropic import AnthropicBackend
from atomic_flow.backends.base import ResponseShapeError
from atomic_flow.backends.usage import AnthropicCachePrices, anthropic_usage
from atomic_flow.config import BackendConfig


def config(**kwargs):
    return BackendConfig(model="fixed", price_in_per_mtok=2, price_out_per_mtok=3, **kwargs)


def usage(**kwargs):
    return {"usage": {"input_tokens": 10, "output_tokens": 5, **kwargs}}


def test_no_cache_uses_only_explicit_base_tariff():
    result = anthropic_usage(usage(), config())
    assert result[:4] == (10, 5, 35 / 1e6, "configured_estimate")


def test_cached_tokens_have_separate_explicit_prices_and_ttl_counters():
    record = usage(cache_read_input_tokens=100, cache_creation_input_tokens=30,
                   cache_creation={"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 10})
    result = anthropic_usage(record, config(), AnthropicCachePrices(.2, 2.5, 4))
    assert result[0:2] == (140, 5)
    assert result[2] == pytest.approx((20 + 15 + 20 + 50 + 40) / 1e6)
    assert result[4:] == (100, 30)


@pytest.mark.parametrize("record", [usage(cache_read_input_tokens=100), usage(cache_creation_input_tokens=20)])
def test_cached_usage_without_explicit_tariff_or_ttl_never_uses_base_input_rate(record):
    result = anthropic_usage(record, config())
    assert result[2] is None and result[3] == "unpriced_cache_usage"


@pytest.mark.parametrize("bad", [True, "10", -1, 1.2, None])
def test_invalid_token_count_never_converts_to_number(bad):
    result = anthropic_usage(usage(input_tokens=bad), config())
    assert result[2] is None and result[3] == "invalid_provider_usage"


@pytest.mark.parametrize("bad", [None, [], False, "bad"])
def test_malformed_usage_stays_unknown_even_with_zero_tariffs(bad):
    cfg = BackendConfig(model="m", price_in_per_mtok=0, price_out_per_mtok=0)
    assert anthropic_usage({"usage": bad}, cfg)[2:4] == (None, "invalid_provider_usage")


def test_wrong_cache_breakdown_and_server_tool_usage_are_not_known_cost():
    record = usage(cache_creation_input_tokens=20,
                   cache_creation={"ephemeral_5m_input_tokens": 19, "ephemeral_1h_input_tokens": 0})
    assert anthropic_usage(record, config(), AnthropicCachePrices(.2, 2.5, 4))[2:4] == (None, "invalid_provider_usage")
    assert anthropic_usage(usage(server_tool_use={"web_search_requests": 1}), config())[2:4] == (None, "unpriced_server_tool_usage")
    assert anthropic_usage(usage(), config(), model="different")[2:4] == (None, "model_price_mismatch")
    assert anthropic_usage(usage(cache_creation={"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}), config())[2:4] == (None, "invalid_provider_usage")


@pytest.mark.parametrize("content", [None, {}, [], [{"type": "text", "text": 2}], [{"type": "tool_use", "id": "x"}]])
def test_malformed_content_keeps_received_token_cost(monkeypatch, content):
    monkeypatch.setattr("atomic_flow.backends.anthropic.post_json", lambda *a, **kw: {**usage(), "content": content})
    with pytest.raises(ResponseShapeError) as caught:
        AnthropicBackend(config()).chat([{"role": "user", "content": "hi"}])
    assert caught.value.request_outcome == "completed"
    assert caught.value.usage_result.cost_usd == 35 / 1e6


def test_thinking_blocks_are_not_answer_text(monkeypatch):
    payload = {**usage(), "content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": "answer"}]}
    monkeypatch.setattr("atomic_flow.backends.anthropic.post_json", lambda *a, **kw: payload)
    result = AnthropicBackend(config()).chat([{"role": "user", "content": "hi"}])
    assert result.content == "answer" and result.resolved_model == "fixed"


@pytest.mark.parametrize("bad", [True, -1, float("nan"), "0"])
def test_cache_tariff_validates_at_host_construction(bad):
    with pytest.raises(ValueError):
        AnthropicCachePrices(read_per_mtok=bad)
