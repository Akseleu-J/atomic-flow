"""Host contracts and policy layers must gate execution before side effects."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule


class Args(BaseModel):
    path: str = "src/example.py"


class Output(BaseModel):
    count: int


class ContractTool(Tool):
    name = "contract_probe"
    description = "A host contract test operation."
    args_schema = Args
    contract = ToolContract(effects=("write",), idempotent=False, max_output_bytes=512)

    def __init__(self, result=None):
        self.result = result if result is not None else {"status": "applied", "path": "src/example.py", "checks": ["syntax"]}
        self.calls = 0

    def run(self, args, ctx):
        ctx.cancel_check()
        self.calls += 1
        return self.result


def invoke(tmp_path, tool, *, enabled=True, policy=None, cancel=None):
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tmp_path, {}, edits_enabled=enabled,
                      policy=policy or PolicyEngine([Rule(tool.name, "auto")]))
    if cancel:
        ctx.cancel_check = cancel
    return execute_tool({"tool": tool.name, "args": {}}, registry, ctx, allowed_tools=[tool.name])


def test_all_declared_writers_require_edit_opt_in(tmp_path):
    tool = ContractTool()
    result, record = invoke(tmp_path, tool, enabled=False)
    assert tool.calls == 0
    assert result["outcome"] == "denied"
    assert record["contract"]["effects"] == ("write",)


def test_output_limit_preserves_completed_mutation_proof(tmp_path):
    result, record = invoke(tmp_path, ContractTool({"status": "applied", "path": "src/example.py",
                           "content_hash": "a" * 64, "checks": ["syntax"], "source": "x" * 2000}))
    assert result["outcome"] == "error"
    assert result["status"] == "applied"
    assert result["verification"]["syntax"] == "passed"
    assert result["side_effects_may_have_occurred"] is True
    assert result["content_hash"] == "a" * 64
    assert len(result["output_sha256"]) == 64
    assert "source" not in result
    assert record["output_bytes"] > 512


def test_optional_output_schema_is_enforced_without_erasing_proof(tmp_path):
    tool = ContractTool()
    tool.result_schema = Output
    result, _ = invoke(tmp_path, tool)
    assert result["outcome"] == "error"
    assert "schema" in result["error"]
    assert result["status"] == "applied"


def test_non_json_tool_response_is_an_audited_error(tmp_path):
    result, record = invoke(tmp_path, ContractTool({"bad": object()}))
    assert result["outcome"] == record["result_summary"]["outcome"] == "error"
    assert "non-JSON" in result["error"]


def test_cooperative_deadline_checked_during_run(tmp_path, monkeypatch):
    clock = iter([0.0, 0.0, 121.0])
    monkeypatch.setattr("atomic_flow.agents.loop.time.monotonic", lambda: next(clock))
    tool = ContractTool()
    result, _ = invoke(tmp_path, tool)
    assert result["outcome"] == "error"
    assert "deadline" in result["error"]
    assert tool.calls == 0


def test_cancel_propagates_and_does_not_execute(tmp_path):
    tool = ContractTool()

    def cancelled():
        raise TaskCancelled("stop")

    with pytest.raises(TaskCancelled):
        invoke(tmp_path, tool, cancel=cancelled)
    assert tool.calls == 0


def test_contract_exposes_effects_and_rejects_invalid_retry():
    registry = ToolRegistry()
    registry.register(ContractTool())
    assert registry.schemas(["contract_probe"])[0]["host_contract"]["effects"] == ("write",)
    with pytest.raises(ValueError, match="idempotence"):
        ToolContract(idempotent=False, retryable=True)


def test_deny_wins_even_after_allow_and_across_layers():
    engine = PolicyEngine(host_rules=[Rule("create_file", "auto")],
                          user_rules=[Rule("create_file", "auto"), Rule("create_file", "forbid")],
                          project_rules=[Rule("create_file", "auto")], session_rules=[Rule("create_file", "auto")])
    decision = engine.decide("create_file", {}, project_trusted=True)
    assert decision.action == "forbid"
    assert decision.source == "user"


def test_project_and_session_cannot_weaken_host_or_user():
    engine = PolicyEngine(host_rules=[Rule("patch_file", "ask")], project_rules=[Rule("patch_file", "auto")])
    assert engine.decide("patch_file", {}, project_trusted=True).source == "host"
    engine = PolicyEngine(user_rules=[Rule("patch_file", "forbid")], session_rules=[Rule("patch_file", "auto")])
    assert engine.decide("patch_file", {}, project_trusted=True).action == "forbid"


def test_project_allow_cannot_override_builtin_host_dangerous_command_denial(tmp_path):
    directory = tmp_path / ".atomic_flow"
    directory.mkdir()
    (directory / "policy.yaml").write_text("rules:\n  - match: {tool: run_command}\n    action: auto\n", encoding="utf8")
    decision = PolicyEngine.load(tmp_path).decide("run_command", {"command": "pytest; sudo rm -rf /"}, project_trusted=True)
    assert decision.action == "forbid"
    assert decision.source == "host"


def test_scoped_allow_requires_both_move_endpoints_and_normalized_paths():
    engine = PolicyEngine(project_rules=[Rule("move_file", "auto", path_matches="src/*")])
    assert engine.decide("move_file", {"source": "src/a.py", "destination": "src/b.py"}, project_trusted=True).action == "auto"
    for destination in ("secrets/b.py", "src/../secrets.py", "C:/src/b.py"):
        assert engine.decide("move_file", {"source": "src/a.py", "destination": destination}, project_trusted=True).action == "ask"
    denial = PolicyEngine(project_rules=[Rule("move_file", "forbid", path_matches="secrets/*")])
    assert denial.decide("move_file", {"source": "src/a.py", "destination": "secrets/a.py"}, project_trusted=True).action == "forbid"


def test_broken_policy_fails_closed_and_explains_layer(tmp_path):
    directory = tmp_path / ".atomic_flow"
    directory.mkdir()
    (directory / "policy.yaml").write_text("rules: [broken]", encoding="utf8")
    decision = PolicyEngine.load(tmp_path).decide("read_node", {}, project_trusted=True)
    assert decision.action == "forbid"
    assert decision.source == "project"
    assert "project:" in decision.reason


def test_dispatcher_audits_source_and_rule_reason(tmp_path):
    tool = ContractTool()
    result, record = invoke(tmp_path, tool, policy=PolicyEngine(user_rules=[Rule(tool.name, "forbid")]))
    assert result["outcome"] == "denied"
    assert tool.calls == 0
    assert record["policy_source"] == "user"
    assert "user:" in record["policy_reason"]


def test_untrusted_project_policy_is_ignored_without_discarding_user_policy(tmp_path):
    directory = tmp_path / ".atomic_flow"
    directory.mkdir()
    (directory / "policy.yaml").write_text("rules:\n  - match: {tool: read_file}\n    action: forbid\n", encoding="utf8")
    user_policy = tmp_path / "user-policy.yaml"
    user_policy.write_text("rules:\n  - match: {tool: patch_file}\n    action: forbid\n", encoding="utf8")
    engine = PolicyEngine.load(tmp_path, user_path=user_policy, project_trusted=False)
    assert engine.decide("read_file", {}, project_trusted=False).action == "auto"
    assert engine.decide("patch_file", {}, project_trusted=False).source == "user"
