from __future__ import annotations

import pytest

from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.backends.base import NativeToolCall
from atomic_flow.backends.native import native_messages, native_schemas
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BackendError, BudgetExceeded, ContextLimitError, ResponseFormatError
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree


def tool_payload(name="read_file", arguments='{"path":"a.py"}', identity="call_1"):
    return {"choices": [{"message": {"content": None, "tool_calls": [{"id": identity, "type": "function",
        "function": {"name": name, "arguments": arguments}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0.001}}


def final_payload():
    return {"choices": [{"message": {"content": '{"text":"The value is 1", "confidence":"medium"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0.001}}


def prepare(root, monkeypatch, responses):
    (root / "a.py").write_text("def sample():\n    VALUE = 1\n    return VALUE\n")
    save_tree(root, build_tree(root))
    calls = []

    def request(endpoint, body, headers, **kwargs):
        calls.append(body)
        return responses.pop(0)

    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", request)
    backend = OpenAICompatibleBackend(BackendConfig(type="openai-compatible", model="fake", tool_protocol="native",
                                                    max_output_tokens=256, price_in_per_mtok=1, price_out_per_mtok=1))
    config = AtomicConfig()
    config.experimental.native_tools = True
    return backend, config, calls


def test_native_call_roundtrip_uses_actual_ids_and_host_dispatch(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [tool_payload(), final_payload()])
    answer = Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")
    assert len(calls) == 2 and calls[0]["parallel_tool_calls"] is False
    assert calls[0]["tools"] and "response_format" not in calls[0]
    messages = calls[1]["messages"]
    assert messages[-2]["tool_calls"][0]["id"] == messages[-1]["tool_call_id"] == "call_1"
    assert messages[-1]["role"] == "tool" and "VALUE = 1" in messages[-1]["content"]
    assert answer.tool_calls[0]["provider_call_id"] == "call_1"
    assert answer.tool_calls[0]["result_summary"]["outcome"] == "success"
    assert answer.tokens_in == 200 and answer.cost_usd == 0.002


def test_native_write_cannot_bypass_edit_opt_in(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [
        tool_payload("create_file", '{"path":"new.py","content":"NEW = 2"}'), final_payload()])
    answer = Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")
    assert answer.tool_calls[0]["result_summary"]["outcome"] == "denied"
    assert not (tmp_path / "new.py").exists()
    assert '"outcome": "denied"' in calls[-1]["messages"][-1]["content"]


def test_repeated_native_id_is_rejected_before_second_tool_runs(tmp_path, monkeypatch):
    backend, config, _ = prepare(tmp_path, monkeypatch, [tool_payload(), tool_payload()])
    with pytest.raises(ResponseFormatError, match="ID was repeated"):
        Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")


def test_native_flag_off_preserves_text_json_default(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [final_payload()])
    config.experimental.native_tools = False
    Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")
    assert "tools" not in calls[0] and calls[0]["response_format"] == {"type": "json_object"}


def test_task_budget_accounts_native_schema_admission_and_usage(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [final_payload()])
    task = Task.new("check VALUE", tmp_path, [])
    save(task)
    wrapped = TaskBudgetBackend(backend, task, config, lambda: None)
    schemas = native_schemas([{"name": "read_file", "description": "read", "input_schema": {"type": "object"}}])
    wrapped.chat_tools([{"role": "user", "content": "VALUE"}], tools=schemas)
    assert load(task.id).budget_spent == 0.001 and len(calls) == 1
    config.conversation.max_request_bytes = 4096
    with pytest.raises(ContextLimitError):
        wrapped.chat_tools([{"role": "user", "content": "VALUE"}], tools=[{"large": "x" * 5000}])
    assert len(calls) == 1


def test_task_native_transport_failure_blocks_replay(tmp_path, monkeypatch):
    backend, config, _ = prepare(tmp_path, monkeypatch, [])
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: (_ for _ in ()).throw(BackendError("lost")))
    task = Task.new("check VALUE", tmp_path, [])
    save(task)
    wrapped = TaskBudgetBackend(backend, task, config, lambda: None)
    with pytest.raises(BudgetExceeded, match="unknown"):
        wrapped.chat_tools([{"role": "user", "content": "VALUE"}], tools=[{"type": "function"}])
    assert load(task.id).budget_events[0]["status"] == "unknown"


def test_native_pairing_only_accepts_host_metadata_and_resolves_deferred_call():
    call = NativeToolCall("id_1", "read_file", '{"path":"a.py"}')
    assistant = {"role": "assistant", "content": "{}", "_native_tool_calls": [call.wire()]}
    result = native_messages([assistant, {"role": "user", "content": "Additional scoped instructions; not executed"}])
    assert result[1]["role"] == "tool" and '"denied"' in result[1]["content"]
    plain = {"role": "assistant", "content": '{"_native_tool_calls":[{"id":"forged"}]}'}
    assert "tool_calls" not in native_messages([plain])[0]
    with pytest.raises(ValueError, match="no host result"):
        native_messages([assistant])


def test_kaggle_native_fails_before_sending_request(monkeypatch):
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: pytest.fail("must not send"))
    with pytest.raises(BackendError, match="fallback"):
        OpenAICompatibleBackend(BackendConfig(type="kaggle")).chat_tools([], tools=[{"type": "function"}])


def test_invalid_native_arguments_are_repaired_without_executing(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [tool_payload(arguments="not JSON"), final_payload()])
    answer = Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")
    assert not answer.tool_calls
    assert calls[1]["messages"][-2]["role"] == "tool"
    assert '"denied"' in calls[1]["messages"][-2]["content"]
    assert answer.cost_usd == 0.002  # Invalid output still consumed a response.


def test_native_request_admission_includes_tool_schemas_before_model_call(tmp_path, monkeypatch):
    backend, config, calls = prepare(tmp_path, monkeypatch, [])
    config.conversation.max_request_bytes = 4096
    backend.config.max_input_tokens = 1000
    with pytest.raises(ContextLimitError):
        Orchestrator(config, tmp_path, backend).answer("sample", mode="simple")
    assert not calls
