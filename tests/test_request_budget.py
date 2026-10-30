import json

import pytest

from atomic_flow.config import BackendConfig, ConversationConfig, load_config
from atomic_flow.errors import ContextLimitError
from atomic_flow.llm.history import RequestHistory
from atomic_flow.llm.request_budget import admit_request, estimate_request


def test_serialized_cyrillic_json_schemas_and_framing_are_charged():
    messages = [{"role": "system", "content": 'Правило: "сохрани"\n'}, {"role": "user", "content": "ёж"}]
    schemas = [{"name": "read", "parameters": {"type": "object"}}]
    cfg = BackendConfig(model="deployment-v1", request_framing_tokens=13, message_framing_tokens=7, max_output_tokens=16)
    measured = admit_request(messages, cfg, tool_schemas=schemas)
    expected = len(json.dumps({"messages": messages, "tools": schemas}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert measured.serialized_request_bytes == expected
    assert measured.estimated_input_tokens == expected + 27
    assert measured.model == "deployment-v1"
    assert not measured.provider_token_count_verified
    assert measured.count_method == "utf8_bytes_estimate_with_configured_framing"


def test_reply_reserve_and_input_limit_have_independent_boundaries():
    messages = [{"role": "user", "content": "small"}]
    cfg = BackendConfig(max_output_tokens=100, context_window_tokens=200)
    count = estimate_request(messages, cfg).estimated_input_tokens
    cfg.context_window_tokens = count + 100
    assert admit_request(messages, cfg).output_reserve_tokens == 100
    cfg.context_window_tokens -= 1
    with pytest.raises(ContextLimitError, match="reply reserve"):
        admit_request(messages, cfg, output_reserve_tokens=16)  # Cannot under-reserve configured output.
    cfg.context_window_tokens = 10000
    cfg.max_input_tokens = count - 1
    with pytest.raises(ContextLimitError, match="max_input_tokens"):
        admit_request(messages, cfg)


def test_task_reserve_and_unknown_model_limits_are_explicit():
    messages = [{"role": "user", "content": "task"}]
    measured = admit_request(messages, BackendConfig(), output_reserve_tokens=2048)
    assert measured.output_reserve_tokens == 2048
    assert measured.context_window_tokens is None and measured.max_input_tokens is None
    assert not measured.provider_token_count_verified
    assert admit_request(messages, BackendConfig()).output_reserve_tokens == 4096


def test_injected_tokenizer_receives_complete_json_without_provider_exactness_claim():
    seen = []

    def counter(text):
        seen.append(json.loads(text))
        return 20

    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "query"}]
    cfg = BackendConfig(context_window_tokens=200, max_output_tokens=16)
    measured = admit_request(messages, cfg, tool_schemas=[{"name": "tool"}], token_counter=counter)
    assert seen == [{"messages": messages, "tools": [{"name": "tool"}]}]
    assert measured.estimated_input_tokens == 20 + 32 + 16
    assert not measured.provider_token_count_verified
    with pytest.raises(ValueError, match="nonnegative integer"):
        admit_request(messages, cfg, token_counter=lambda text: -1)


def test_token_pressure_compacts_old_results_even_below_byte_cap():
    cfg = BackendConfig(context_window_tokens=3000, max_output_tokens=200)
    history = RequestHistory(ConversationConfig(recent_tool_results=1), cfg)
    messages = [{"role": "system", "content": "permissions"}]
    history.append_result(messages, {"source": "x" * 2000, "content_hash": "original", "outcome": "success"})
    history.append_result(messages, {"source": "y" * 1000, "outcome": "success"})
    snapshot = history.prepare(messages)
    assert "history_body_omitted" in snapshot[1]["content"]
    assert "original" in snapshot[1]["content"]
    assert "y" * 1000 in snapshot[2]["content"]
    stats = history.stats()["request_admission"]
    assert stats["estimated_input_tokens"] + stats["output_reserve_tokens"] <= 3000


def test_oversized_pinned_instructions_block_before_backend_is_called():
    called = []
    messages = [{"role": "system", "content": "rule" * 600}]
    history = RequestHistory(ConversationConfig(), BackendConfig(context_window_tokens=2048, max_output_tokens=128))

    def send():
        request = history.prepare(messages)
        called.append(request)

    with pytest.raises(ContextLimitError):
        send()
    assert called == []
    assert messages[0]["content"] == "rule" * 600


def test_untrusted_project_cannot_expand_model_context_or_framing(tmp_path):
    project = tmp_path / ".atomic_flow"
    project.mkdir()
    (project / "config.yaml").write_text("backends:\n  openai-compatible:\n    context_window_tokens: 999999\n    request_framing_tokens: 0\n")
    cfg = load_config(tmp_path)
    assert cfg.backends["openai-compatible"].context_window_tokens is None
    assert cfg.backends["openai-compatible"].request_framing_tokens == 32


def test_tool_schema_bytes_alone_can_block_admission():
    messages = [{"role": "user", "content": "hi"}]
    with pytest.raises(ContextLimitError, match="max_request_bytes"):
        admit_request(messages, BackendConfig(), ConversationConfig(max_request_bytes=4096),
                      tool_schemas=[{"description": "x" * 5000}])
