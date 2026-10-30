"""Changed integration boundaries; no external services or paid model calls."""
import json
import subprocess
import sys

import pytest
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.cli import main
from atomic_flow.hooks import HookManager
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.sdk import start_answer


class Args(BaseModel):
    pass


def registered_probe(tmp_path, hooks, *, write=False):
    created = []
    class Probe(Tool):
        name = "probe"
        description = "Integration probe"
        args_schema = Args
        contract = ToolContract(effects=("write",) if write else ("read",))
        def run(self, args, ctx):
            if write:
                (tmp_path / "effect.txt").write_text("applied")
                return {"status": "applied", "path": "effect.txt"}
            ctx.record_read("a.py", "a" * 40, "file", 0, 1, 1, source_complete=True)
            return {"source": "x"}
    registry = ToolRegistry()
    def factory():
        created.append(True)
        return Probe()
    registry.register_lazy("probe", Probe.description, Args, Probe.contract, factory)
    context = ToolContext(tmp_path, {}, hooks=hooks, edits_enabled=True,
                          policy=PolicyEngine([Rule("probe", "auto")]))
    return registry, context, created


def test_hook_veto_and_policy_denial_precede_lazy_factory(tmp_path):
    hooks, observed = HookManager(), []
    hooks.register("before_tool", lambda value: observed.append(value) or False, approved=True)
    registry, ctx, created = registered_probe(tmp_path, hooks)
    denied, _ = execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=[])
    assert denied["outcome"] == "denied" and not observed
    rejected, _ = execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=["probe"])
    assert rejected["outcome"] == "error" and not created and not ctx.reads


def test_post_hook_failure_preserves_write_effect_without_retry(tmp_path):
    hooks = HookManager()
    def broken(_):
        raise ValueError("secret hook implementation detail")
    hooks.register("after_tool", broken, approved=True)
    registry, ctx, created = registered_probe(tmp_path, hooks, write=True)
    result, record = execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=["probe"])
    assert result["outcome"] == "error" and result["side_effects_may_have_occurred"]
    assert record["result_summary"]["status"] == "applied"
    assert (tmp_path / "effect.txt").read_text() == "applied" and len(created) == 1
    assert "implementation detail" not in json.dumps(result)


def test_post_hook_cancellation_propagates_and_revokes_read_proof(tmp_path):
    hooks = HookManager()
    cancelled = False
    def late_cancel(_):
        nonlocal cancelled
        cancelled = True
    def check():
        if cancelled:
            raise TaskCancelled("late cancel")
    hooks.register("after_tool", late_cancel, approved=True)
    registry, ctx, _ = registered_probe(tmp_path, hooks)
    ctx.cancel_check = check
    with pytest.raises(TaskCancelled):
        execute_tool({"tool": "probe", "args": {}}, registry, ctx, allowed_tools=["probe"])
    assert not ctx.reads


@pytest.mark.parametrize("name", ["ask_user", "external_read"])
def test_after_hook_failure_cannot_restore_success_or_erase_existing_uncertainty(tmp_path, name):
    hooks = HookManager()
    def broken(_):
        raise RuntimeError("failed")
    hooks.register("after_tool", broken, approved=True)
    class Remote(Tool):
        description = "Uncertain remote result"
        args_schema = Args
        contract = ToolContract(effects=("read",))
        def run(self, args, ctx):
            return {"outcome": "success", "side_effects_may_have_occurred": True}
    tool = Remote()
    tool.name = name
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tmp_path, {}, hooks=hooks, policy=PolicyEngine([Rule(name, "auto")]))
    result, _ = execute_tool({"tool": name, "args": {}}, registry, ctx, allowed_tools=[name])
    assert result["outcome"] == "error" and result["side_effects_may_have_occurred"]


def test_sdk_lifecycle_failure_is_terminal_before_any_model_call(tmp_path, config):
    from conftest import ScriptedBackend
    hooks, events = HookManager(), []
    hooks.register("run_start", lambda value: events.append(value["event"]) or False, approved=True)
    hooks.register("run_error", lambda value: events.append(value["event"]), approved=True)
    backend = ScriptedBackend({})
    handle = start_answer(config, tmp_path, backend, "read project", hooks=hooks)
    with pytest.raises(Exception, match="hook may return"):
        handle.result(5)
    assert events == ["run_start", "run_error"] and not backend.calls
    assert list(handle.events())[-1].kind == "run_failed"


def test_successful_orchestrator_lifecycle_is_bound_to_request(tmp_path, config, monkeypatch):
    from types import SimpleNamespace

    from conftest import ScriptedBackend
    hooks, seen = HookManager(), []
    for event in ("run_start", "run_end", "run_error"):
        hooks.register(event, lambda value: seen.append((value["event"], value["request_id"])), approved=True)
    # Exercise lifecycle orchestration without a model or unrelated retrieval pipeline.
    result = SimpleNamespace(verdict="PASS", model_dump=lambda: {})
    monkeypatch.setattr(Orchestrator, "_answer", lambda *args: result)
    monkeypatch.setattr("atomic_flow.llm.context_state.checkpoint_run", lambda *args: None)
    assert Orchestrator(config, tmp_path, ScriptedBackend({}), hooks=hooks).answer("read", request_id="hook_test") is result
    assert seen == [("run_start", "hook_test"), ("run_end", "hook_test")]


def test_component_catalog_is_deferred_in_fresh_interpreter():
    code = """import json, sys
from atomic_flow.sdk import components
c = components()
before = [m for m in ('fastapi','torch','transformers','kaggle','atomic_flow.integrations.mcp','atomic_flow.lsp') if m in sys.modules]
symbol = c.load('lsp')
print(json.dumps({'before':before, 'symbol':symbol.__name__, 'lsp_loaded':'atomic_flow.lsp' in sys.modules}))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20, check=True)
    output = json.loads(proc.stdout)
    assert output == {"before": [], "symbol": "LSPClient", "lsp_loaded": True}


def test_invalid_mcp_host_command_reports_cli_error_without_traceback(tmp_path, capsys):
    assert main(["mcp", "tools", "--project", str(tmp_path), "--command-json", "[]", "--yes"]) == 2
    output = capsys.readouterr()
    assert "Traceback" not in output.err + output.out
