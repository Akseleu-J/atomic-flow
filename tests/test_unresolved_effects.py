"""Uncertain effects stop host action, independently of model prompts/history."""
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.effects import UnresolvedEffects
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.task import load
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.llm.orchestrator import Orchestrator, _Run
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.llm.schemas import Answer
from atomic_flow.sessions import SessionJournal


class Args(BaseModel):
    value: str = "first"


def make_tools(project, *, cancel=False):
    invoked, loaded = [], []
    class Effect(Tool):
        name = "effect"
        description = "Simulated external non-idempotent operation"
        args_schema = Args
        contract = ToolContract(effects=("execute",), idempotent=False)
        def run(self, args, ctx):
            invoked.append(args.value)
            if cancel:
                raise TaskCancelled("unknown after send")
            return {"error": "lost response", "side_effects_may_have_occurred": True}
    class Other(Effect):
        name = "other"
    class Read(Effect):
        name = "inspect"
        contract = ToolContract(effects=("read",))
        def run(self, args, ctx):
            return {"available": True}
    registry = ToolRegistry()
    registry.register(Effect())
    registry.register(Read())
    def factory():
        loaded.append(True)
        return Other()
    registry.register_lazy(Other.name, Other.description, Args, Other.contract, factory)
    ctx = ToolContext(project, {}, policy=PolicyEngine(host_rules=[Rule("*", "auto")]))
    return registry, ctx, invoked, loaded


def test_latch_precedes_factory_and_policy_survives_role_and_argument_changes(tmp_path):
    registry, ctx, invoked, loaded = make_tools(tmp_path)
    result, _ = execute_tool({"tool": "effect", "args": {}}, registry, ctx, allowed_tools=["effect"])
    assert result["side_effects_may_have_occurred"] and ctx.effects_state.blocked
    approvals = []
    ctx.policy = PolicyEngine(host_rules=[Rule("*", "ask")])
    ctx.approval_callback = lambda *args: approvals.append(args) or True
    for name in ("effect", "other"):
        with pytest.raises(UnresolvedEffects):
            execute_tool({"tool": name, "args": {"value": "different"}}, registry, ctx,
                         allowed_tools=[name], role="different_role")
    assert invoked == ["first"] and not loaded and not approvals
    ctx.policy = PolicyEngine(host_rules=[Rule("inspect", "auto")])
    assert execute_tool({"tool": "inspect", "args": {}}, registry, ctx, allowed_tools=["inspect"])[0]["available"]


@pytest.mark.parametrize("native", [False, True])
def test_real_role_loop_never_requests_another_response_after_uncertainty(tmp_path, native):
    registry, ctx, invoked, _ = make_tools(tmp_path)
    backend = SimpleNamespace(config=BackendConfig(type="openai-compatible", tool_protocol="native" if native else "text_json",
                                                  max_output_tokens=4096), chat_tools=lambda *a, **k: None)
    run = _Run(backend, PromptRegistry(tmp_path), SimpleNamespace(check=lambda: None), "perform operation", None,
               tool_registry=registry, tool_ctx=ctx, native_tools=native)
    calls = []
    def chat(*_):
        from atomic_flow.backends.base import ChatResult, NativeToolCall
        calls.append(True)
        return ChatResult(json.dumps({"tool": "effect", "args": {}}),
                          tool_calls=[NativeToolCall("call_1", "effect", "{}")] if native else [])
    run.chat = chat
    with pytest.raises(UnresolvedEffects):
        run._invoke_with_tools("answerer", Answer, {}, None, ["effect"])
    with pytest.raises(UnresolvedEffects):
        run.invoke("critic", Answer, {})
    assert len(calls) == 1 and invoked == ["first"]
    assert run.journal is not None and run.journal.read().unresolved_call_ids


def test_cancelled_effect_is_latched_and_durably_observed(tmp_path):
    registry, ctx, invoked, _ = make_tools(tmp_path, cancel=True)
    journal = SessionJournal.create(tmp_path)
    ctx.journal_event = journal.append
    with pytest.raises(TaskCancelled):
        execute_tool({"tool": "effect", "args": {}}, registry, ctx, allowed_tools=["effect"])
    assert ctx.effects_state.blocked and invoked == ["first"]
    assert journal.read().unresolved_call_ids


def test_journal_failure_after_effect_never_becomes_retryable_tool_error(tmp_path):
    registry, ctx, invoked, _ = make_tools(tmp_path)
    ctx.journal_event = lambda *a, **kw: (_ for _ in ()).throw(OSError("journal unavailable"))
    with pytest.raises(UnresolvedEffects, match="journal unavailable"):
        execute_tool({"tool": "effect", "args": {}}, registry, ctx, allowed_tools=["effect"])
    assert ctx.effects_state.blocked and invoked == ["first"]


def test_preflight_read_refusal_does_not_latch_but_can_be_repaired_by_actual_read(tmp_path):
    from atomic_flow.agents.source import digest
    from atomic_flow.agents.tools import default_registry
    from atomic_flow.agents.undo import UndoStack
    path = tmp_path / "a.py"
    path.write_text("A = 1\n")
    expected = digest(path.read_bytes())
    ctx = ToolContext(tmp_path, {}, file_hashes={"a.py": expected}, edits_enabled=True, enforce_read_before_edit=True,
                      undo_stack=UndoStack(tmp_path / "undo"), policy=PolicyEngine(host_rules=[Rule("*", "auto")]))
    registry = default_registry()
    call = {"tool": "patch_file", "args": {"path": "a.py", "expected_hash": expected, "old_text": "A = 1", "new_text": "A = 2"}}
    result, _ = execute_tool(call, registry, ctx, allowed_tools=["patch_file"])
    assert "read the complete" in result["error"] and not ctx.effects_state.blocked
    assert path.read_text() == "A = 1\n"
    execute_tool({"tool": "read_file", "args": {"path": "a.py"}}, registry, ctx, allowed_tools=["read_file"])
    assert execute_tool(call, registry, ctx, allowed_tools=["patch_file"])[0]["status"] == "applied"


def test_expected_effect_error_is_concise_in_cli_and_api(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from atomic_flow import cli
    from atomic_flow.api.server import create_app
    def interrupted(*a, **kw):
        raise UnresolvedEffects("unresolved tool effects; inspect external state")
    monkeypatch.setitem(cli.HANDLERS, "ask", interrupted)
    assert cli.main(["ask", "operate"]) == 2
    assert "Traceback" not in capsys.readouterr().err
    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("atomic_flow.api.server.make_backend", lambda *a: None)
    monkeypatch.setattr("atomic_flow.api.server.Orchestrator.answer", interrupted)
    with TestClient(create_app()) as client:
        response = client.post("/v1/answer", json={"query": "operate", "project": str(tmp_path), "mode": "simple"})
    assert response.status_code == 409 and response.json()["detail"]["error"] == "unresolvedeffects"


@pytest.mark.parametrize("artifacts", [True, False])
def test_task_uncertainty_blocks_independent_work_replan_resume_and_new_approval(tmp_path, monkeypatch, artifacts):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("A = 1\n")
    config = AtomicConfig()
    config.artifacts.save = artifacts
    runner = TaskRunner(config, project, None)
    calls = []
    def execute(task, step):
        calls.append(step.index)
        raise UnresolvedEffects("external operation needs review")
    monkeypatch.setattr(runner, "_execute_step", execute)
    monkeypatch.setattr("atomic_flow.agents.task_loop.replan_task", lambda *a, **kw: pytest.fail("uncertain effect replanned"))
    plan = Plan(steps=[PlanStep(description="Execute first operation", verify_with="none"),
                       PlanStep(description="Execute independent operation", verify_with="none", depends_on=[])])
    result = runner.run_plan("perform task", plan)
    assert result.status == "paused" and calls == [0]
    assert SessionJournal(result.id).read().unresolved_call_ids
    assert result.steps[1].status == "pending" and result.replan_count == 0
    runner.config.artifacts.save = not artifacts
    revised = runner.revise(result, Plan(steps=[PlanStep(description="Try a different operation", verify_with="none")]))
    revised = runner.approve(revised, runner.review(revised)["digest"])
    assert runner.resume(revised).status == "paused" and calls == [0]
    assert "unresolved" in load(result.id).error


def test_same_request_cannot_restart_unresolved_effect_when_artifacts_disabled(tmp_path, monkeypatch):
    from conftest import ScriptedBackend
    config = AtomicConfig()
    config.artifacts.save = False
    def answer(self, run, *args):
        run.effects_state.mark("external")
        run.check_effects()
    monkeypatch.setattr(Orchestrator, "_answer", answer)
    orch = Orchestrator(config, tmp_path, ScriptedBackend({}))
    with pytest.raises(UnresolvedEffects):
        orch.answer("operate", request_id="same_request")
    monkeypatch.setattr(Orchestrator, "_answer", lambda *args: pytest.fail("restarted unresolved request"))
    with pytest.raises(UnresolvedEffects):
        orch.answer("operate", request_id="same_request")


def test_dispatch_result_artifacts_and_provider_history_redact_canary(tmp_path):
    from atomic_flow.backends.base import ChatResult
    token = "sk-" + "FAKE_CANARY_" * 4
    class Output(Tool):
        name = "inspect"
        description = "Synthetic read output"
        args_schema = Args
        def run(self, args, ctx):
            return {"stdout": token, "diagnostics": [{"message": token}], "token": "sensitive-unrecognized"}
    registry = ToolRegistry()
    registry.register(Output())
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    ctx = ToolContext(tmp_path, {}, policy=PolicyEngine(host_rules=[Rule("inspect", "auto")]))
    run = _Run(SimpleNamespace(config=BackendConfig(max_output_tokens=4096)), PromptRegistry(tmp_path),
               SimpleNamespace(check=lambda: None), "inspect", artifact, tool_registry=registry, tool_ctx=ctx)
    requests = []
    def chat(messages, *_):
        requests.append(json.dumps(messages))
        return ChatResult(json.dumps({"tool": "inspect", "args": {}}) if len(requests) == 1 else '{"text":"done"}')
    run.chat = chat
    assert run._invoke_with_tools("answerer", Answer, {}, None, ["inspect"]).text == "done"
    assert token not in "".join(requests)
    assert all(token not in path.read_text(encoding="utf-8") and "sensitive-unrecognized" not in path.read_text(encoding="utf-8")
               for path in artifact.iterdir())
