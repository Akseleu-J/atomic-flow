"""Dispatcher boundaries: audit every outcome and enforce host-side permissions."""
from pathlib import Path

import pytest
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.tools import default_registry


class EmptyArgs(BaseModel):
    pass


class ProbeTool(Tool):
    name = "probe"
    description = "Test dispatcher outcomes without a subprocess."
    args_schema = EmptyArgs

    def __init__(self, result=None, raises=False):
        self.calls = 0
        self.result = result if result is not None else {"source": "large source body"}
        self.raises = raises

    def run(self, args, ctx):
        self.calls += 1
        if self.raises:
            raise RuntimeError("broken tool")
        return self.result


def invoke(tool, tmp_path, *, allowed=("probe",), policy=None):
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tmp_path, {}, policy=policy or PolicyEngine([Rule("probe", "auto")]))
    return execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=allowed, role="refiner")


def test_role_denial_is_enforced_and_recorded(tmp_path):
    tool = ProbeTool()
    result, record = invoke(tool, tmp_path, allowed=("read_node",))
    assert tool.calls == 0
    assert result["outcome"] == record["result_summary"]["outcome"] == "denied"
    assert record["role"] == "refiner"


@pytest.mark.parametrize("result,raises,outcome", [
    ({"error": "timed out"}, False, "error"),
    ({"status": "rolled_back", "reason": "syntax failed", "checks": ["syntax"]}, False, "error"),
    ({"command": "pytest", "exit_code": 1}, False, "error"),
    ({"command": "pytest"}, False, "error"),
    ({"command": "pytest", "exit_code": 0}, False, "success"),
    (None, True, "error"),
    (["invalid tool response"], False, "error"),
])
def test_every_execution_outcome_is_audited(tmp_path, result, raises, outcome):
    payload, record = invoke(ProbeTool(result, raises), tmp_path)
    assert payload["outcome"] == record["result_summary"]["outcome"] == outcome
    if outcome == "error":
        assert payload["error"]


def test_policy_denial_has_a_result_summary(tmp_path):
    tool = ProbeTool()
    result, record = invoke(tool, tmp_path, policy=PolicyEngine([Rule("probe", "forbid")]))
    assert tool.calls == 0
    assert result["outcome"] == record["result_summary"]["outcome"] == "denied"


def test_edit_capability_cannot_be_granted_by_policy(tmp_path):
    ctx = ToolContext(tmp_path, {}, trusted=True, policy=PolicyEngine(), edits_enabled=False)
    result, record = execute_tool(
        {"tool": "edit_node", "args": {"node_id": "x.py::f", "new_source": "def f(): pass"}},
        default_registry(), ctx, allowed_tools=["edit_node"], role="answerer",
    )
    assert not ctx.edits_enabled
    assert result["outcome"] == record["result_summary"]["outcome"] == "denied"


def test_invalid_arguments_do_not_prompt_for_approval(tmp_path):
    prompts = []
    ctx = ToolContext(tmp_path, {}, edits_enabled=True, policy=PolicyEngine(),
                      approval_callback=lambda *args: prompts.append(args) or True)
    result, record = execute_tool({"tool": "edit_node", "args": {}}, default_registry(), ctx,
                                  allowed_tools=["edit_node"], role="answerer")
    assert not prompts
    assert result["outcome"] == record["result_summary"]["outcome"] == "error"


def test_unknown_tool_is_recorded(tmp_path):
    result, record = execute_tool({"tool": "unknown", "args": {}}, default_registry(),
                                  ToolContext(Path(tmp_path), {}), allowed_tools=["unknown"])
    assert result["outcome"] == record["result_summary"]["outcome"] == "error"


def test_summary_drops_source_but_keeps_verification(tmp_path):
    result, record = invoke(ProbeTool({"status": "applied", "checks": ["syntax"], "source": "large"}), tmp_path)
    assert result["verification"] == {"syntax": "passed", "pytest": "not_run"}
    assert record["result_summary"]["verification"] == result["verification"]
    assert "source" not in record["result_summary"]


def test_failed_tests_preserve_successful_syntax_check(tmp_path):
    result, _ = invoke(ProbeTool({"status": "rolled_back", "checks": ["syntax", "pytest"], "reason": "tests failed"}), tmp_path)
    assert result["verification"] == {"syntax": "passed", "pytest": "failed"}


def test_approval_exception_is_an_audited_error(tmp_path):
    def broken_callback(*args):
        raise RuntimeError("approval disconnected")

    tool = ProbeTool()
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tmp_path, {}, policy=PolicyEngine([Rule("probe", "ask")]), approval_callback=broken_callback)
    result, record = execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=["probe"])
    assert tool.calls == 0
    assert result["outcome"] == record["result_summary"]["outcome"] == "error"


def test_real_orchestrator_blocks_a_critics_edit(project, config):
    from conftest import ScriptedBackend

    from atomic_flow.llm.orchestrator import Orchestrator

    original = (project / "invoice_service.py").read_bytes()
    approvals = []
    backend = ScriptedBackend({
        "parser": {},
        "answerer": {"text": "Refunds need paid status."},
        "critic": [
            {"tool": "edit_node", "args": {"node_id": "invoice_service.py::is_refundable",
                                             "new_source": "def is_refundable(*args):\n    return False"}},
            {"verdict": "ok"},
        ],
    })
    result = Orchestrator(config, project, backend, approval_callback=lambda *a: approvals.append(a) or True).answer(
        "explain refundable rule", mode="medium", approve_edits=True,
    )
    assert result.tool_calls[0]["role"] == "critic"
    assert result.tool_calls[0]["result_summary"]["outcome"] == "denied"
    assert result.verdict == "needs_retry"
    assert any("failed or were denied" in warning for warning in result.warnings)
    assert not approvals
    assert (project / "invoice_service.py").read_bytes() == original
