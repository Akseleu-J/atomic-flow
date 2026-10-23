import pytest
from pydantic import BaseModel, ConfigDict

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.workflows import Workflow, run_workflow


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class Read(Tool):
    name = "workflow_read"
    description = "Read workflow data"
    args_schema = Args
    contract = ToolContract()

    def __init__(self, calls):
        self.calls = calls

    def run(self, args, ctx):
        self.calls.append(args.value)
        return {"path": args.value}


def definition(steps=None):
    return Workflow.model_validate({"name": "check", "steps": steps or [
        {"id": "first", "tool": Read.name, "args": {"value": "one"}},
        {"id": "second", "tool": Read.name, "args": {"value": "two"}, "depends_on": ["first"]}]})


def test_workflow_executes_same_context_gates_and_retains_audit(tmp_path):
    workflow, registry, calls = definition(), ToolRegistry(), []
    registry.register(Read(calls))
    ctx = ToolContext(tmp_path, {}, policy=PolicyEngine(host_rules=[Rule(Read.name, "auto")]))
    output = run_workflow(workflow, registry, ctx, allowed_tools=[Read.name], role="reader",
                          expected_digest=workflow.digest(), approved=True)
    assert calls == ["one", "two"] and output["status"] == "completed"
    assert output["steps"][0]["audit"]["role"] == "reader"
    assert output["steps"][0]["result"]["provenance"]["trust"] == "data_only"
    assert output["replay_performed"] is False


def test_whole_workflow_prevalidation_before_any_tool(tmp_path):
    registry, calls = ToolRegistry(), []
    registry.register(Read(calls))
    workflow = definition([{"id": "first", "tool": Read.name, "args": {"value": "one"}},
                           {"id": "bad", "tool": Read.name, "args": {"oops": True}}])
    with pytest.raises(ValueError):
        run_workflow(workflow, registry, ToolContext(tmp_path, {}), allowed_tools=[Read.name],
                     expected_digest=workflow.digest(), approved=True)
    assert not calls
    workflow = definition()
    with pytest.raises(PermissionError):
        run_workflow(workflow, registry, ToolContext(tmp_path, {}), allowed_tools=[],
                     expected_digest=workflow.digest(), approved=True)
    with pytest.raises(PermissionError):
        run_workflow(workflow, registry, ToolContext(tmp_path, {}), allowed_tools=[Read.name],
                     expected_digest=workflow.digest())
    assert not calls


def test_workflow_hash_staleness_and_dependencies(tmp_path):
    workflow = definition()
    digest = workflow.digest()
    workflow.steps[0].args["value"] = "changed"
    with pytest.raises(ValueError, match="approved digest"):
        run_workflow(workflow, ToolRegistry(), ToolContext(tmp_path, {}), allowed_tools=[Read.name],
                     expected_digest=digest, approved=True)
    with pytest.raises(ValueError, match="earlier steps"):
        definition([{"id": "first", "tool": Read.name, "args": {"value": "one"}, "depends_on": ["later"]}])
    with pytest.raises(ValueError):
        Workflow.model_validate({"name": "x", "version": 2, "steps": [{"id": "s", "tool": "x"}]})


def test_workflow_stops_after_mutating_failure_no_replay(tmp_path):
    calls = []

    class Write(Read):
        name = "workflow_write"
        contract = ToolContract(effects=("write",), idempotent=False)

        def run(self, args, ctx):
            calls.append(args.value)
            (tmp_path / "changed.txt").write_text(args.value)
            raise OSError("failed after writing")

    registry = ToolRegistry()
    registry.register(Write(calls))
    workflow = definition([{"id": "first", "tool": Write.name, "args": {"value": "one"}},
                           {"id": "second", "tool": Write.name, "args": {"value": "two"}}])
    ctx = ToolContext(tmp_path, {}, edits_enabled=True, trusted=True)
    # This fixture's explicit host policy approves the write without granting a path scope.
    ctx.policy = PolicyEngine(host_rules=[Rule(Write.name, "auto")])
    result = run_workflow(workflow, registry, ctx, allowed_tools=[Write.name], expected_digest=workflow.digest(), approved=True)
    assert result["status"] == "stopped" and result["side_effects_may_have_occurred"] is True
    assert calls == ["one"] and (tmp_path / "changed.txt").read_text() == "one"


def test_workflow_cancellation_preserves_progress_and_pending_journal(tmp_path):
    from atomic_flow.agents.execution import TaskCancelled
    events, calls = [], []

    class Cancel(Read):
        def run(self, args, ctx):
            if args.value == "two":
                raise TaskCancelled("stop")
            return super().run(args, ctx)

    registry, workflow = ToolRegistry(), definition()
    registry.register(Cancel(calls))
    ctx = ToolContext(tmp_path, {}, policy=PolicyEngine(host_rules=[Rule(Read.name, "auto")]),
                      journal_event=lambda event, payload, **options: events.append((event, payload, options)))
    with pytest.raises(TaskCancelled) as exc:
        run_workflow(workflow, registry, ctx, allowed_tools=[Read.name], expected_digest=workflow.digest(), approved=True)
    assert calls == ["one"]
    assert [event[0] for event in events] == ["tool_request", "tool_result", "tool_request"]
    assert exc.value.workflow_report["pending_step"] == "second"
    assert len(exc.value.workflow_report["steps"]) == 1
