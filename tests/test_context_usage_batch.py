from __future__ import annotations

import json

import pytest

from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.tools import default_registry
from atomic_flow.backends.anthropic import AnthropicBackend
from atomic_flow.backends.base import ChatResult, backend_capabilities
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.budget import SpendLedger
from atomic_flow.config import BackendConfig, ConversationConfig
from atomic_flow.llm.history import RequestHistory
from atomic_flow.llm.orchestrator import _Run
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import TreeNode


def test_near_limit_compacts_old_results_before_hard_limit():
    history = RequestHistory(ConversationConfig(max_request_bytes=4096, recent_tool_results=1, compact_at_ratio=0.7))
    messages = [{"role": "system", "content": "instructions"}]
    history.append_result(messages, {"source": "a" * 2000, "outcome": "success"})
    history.append_result(messages, {"source": "b" * 800, "outcome": "success"})
    assert sum(len(m["content"]) for m in messages) < 4096
    history.prepare(messages)
    assert history.stats()["compacted_tool_results"] == 1
    assert "history_body_omitted" in messages[1]["content"]
    assert "b" * 800 in messages[-1]["content"]
    history.prepare(messages)
    assert history.stats()["compacted_tool_results"] == 1


def test_context_byte_partition_and_unknown_model_limits_remain_explicit():
    history = RequestHistory(ConversationConfig())
    messages = [{"role": "system", "content": "Инструкция\nTOOLS"},
                {"role": "user", "content": '<question>x</question><project_context>"code"</project_context>'}]
    history.append_result(messages, {"source": "text"})
    history.prepare(messages)
    stats = history.stats()
    parts = stats["context_breakdown"]["serialized_bytes_by_component"]
    assert sum(parts.values()) == stats["request_admission"]["serialized_request_bytes"]
    assert parts["source"] and parts["tool_results"] and parts["system_and_tools"]
    caps = backend_capabilities(BackendConfig(type="kaggle"))
    assert not caps["native_tool_calls"] and not caps["model_capabilities_verified"]
    assert caps["context_window_tokens"] is None and caps["cancellation"] == "timeout_only"


def test_openai_usage_cache_and_duplicated_proxy_cost_are_not_double_counted(monkeypatch):
    payload = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {
        "prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40},
        "cost": {"input_tokens_cost_nanodollars": 1000, "output_tokens_cost_nanodollars": 500},
        "input_tokens_cost_nanodollars": 1000, "output_tokens_cost_nanodollars": 500}}
    monkeypatch.setattr("atomic_flow.backends.openai_compatible.post_json", lambda *a, **kw: payload)
    result = OpenAICompatibleBackend(BackendConfig()).chat([])
    assert result.tokens_in == 100 and result.cache_read_tokens == 40
    assert result.cost_usd == 0.0000015 and result.cost_source == "provider"
    payload["usage"].pop("prompt_tokens_details")
    assert OpenAICompatibleBackend(BackendConfig()).chat([]).cache_read_tokens is None


def test_anthropic_input_counts_include_cache_but_cost_stays_labeled_estimate(monkeypatch):
    monkeypatch.setattr("atomic_flow.backends.anthropic.post_json", lambda *a, **kw: {
        "content": [{"type": "text", "text": "{}"}], "usage": {
            "input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20}})
    result = AnthropicBackend(BackendConfig(price_in_per_mtok=1, price_out_per_mtok=1)).chat([])
    assert result.tokens_in == 110 and result.cache_read_tokens == 80 and result.cache_write_tokens == 20
    assert result.cost_source == "configured_estimate"


def test_usage_keeps_failed_call_and_prefix_changes_without_message_bodies(tmp_path):
    class Backend:
        config = BackendConfig(model="test", max_output_tokens=256)
        name = "fake"
        calls = 0

        def chat(self, *a, **kw):
            self.calls += 1
            if self.calls == 3:
                raise ValueError("private detail")
            return ChatResult("{}", 20, 5, 0.001, cache_read_tokens=10, cost_source="provider")

    run = _Run(Backend(), PromptRegistry(tmp_path), SpendLedger(1), "query", None)
    for content in ("system-1", "system-2"):
        run.chat([{"role": "system", "content": content}], "answerer")
    with pytest.raises(ValueError):
        run.chat([{"role": "system", "content": "system-2"}], "critic")
    report = run.usage_report()
    assert report["by_role"]["answerer"]["calls"] == 2
    assert report["calls"][1]["system_prefix_changed"]
    assert report["by_role"]["critic"]["unknown_cost_calls"] == 1
    assert "system-1" not in json.dumps(report) and "private detail" not in json.dumps(report)


def ctx(root):
    return ToolContext(root, {n["node_id"]: TreeNode.model_validate(n) for n in build_tree(root)["nodes"]})


def batch(context, requests, allowed=("read_many", "read_file", "find_files")):
    return execute_tool({"tool": "read_many", "args": {"requests": requests}}, default_registry(enable_parallel_reads=True), context,
                        allowed_tools=allowed, role="critic")[0]


def test_parallel_read_merges_coverage_and_keeps_child_audit(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 2\n")
    context = ctx(tmp_path)
    result = batch(context, [{"tool": "read_file", "args": {"path": path}} for path in ("a.py", "b.py")])
    assert result["outcome"] == "success" and result["workers"] == 2
    assert len(result["calls"]) == 2 and all(r["role"] == "critic" for r in result["calls"])
    assert all(context.read_coverage(path, context.file_hashes[path])["read_complete"] for path in ("a.py", "b.py"))


@pytest.mark.parametrize("tool", ["edit_node", "run_command", "read_many", "ask_user", "read_file"])
def test_batch_preflight_rejects_disallowed_child_without_starting_any(tmp_path, tool):
    (tmp_path / "a.py").write_text("A = 1\n")
    context = ctx(tmp_path)
    result = batch(context, [{"tool": "find_files", "args": {}}, {"tool": tool, "args": {}}],
                   allowed=("read_many", "find_files"))
    assert result["outcome"] == "error" and not context.reads


def test_parallel_feature_off_by_default_and_cannot_be_enabled_by_untrusted_project(tmp_path):
    from atomic_flow.config import load_config, trust_project
    assert default_registry().get("read_many") is None
    (tmp_path / ".atomic_flow").mkdir()
    (tmp_path / ".atomic_flow/config.yaml").write_text("experimental:\n  parallel_reads: true\n")
    assert not load_config(tmp_path).experimental.parallel_reads
    trust_project(tmp_path)
    assert load_config(tmp_path).experimental.parallel_reads


def test_batch_never_prompts_or_weakens_inner_forbid(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    context = ctx(tmp_path)
    context.policy = PolicyEngine([Rule("read_file", "ask")])
    context.approval_callback = lambda *args: pytest.fail("no parallel approval prompts")
    result = batch(context, [{"tool": "read_file", "args": {"path": "a.py"}}])
    assert result["outcome"] == "error" and not context.reads


def test_oversized_batch_does_not_mark_unreturned_source_as_read(tmp_path):
    # Eight maximum Unicode pages overflow the aggregate contract.
    (tmp_path / "a.py").write_text("# " + "𐀀" * 11000 + "\n", encoding="utf-8")
    context = ctx(tmp_path)
    result = batch(context, [{"tool": "read_file", "args": {"path": "a.py", "limit": 12000}}] * 8)
    assert result["outcome"] == "error" and not context.reads
