"""W6 capability composition: host factories reach the durable TaskRunner path.

Covers the RT-04/Q03 composition gap: a host-owned capability factory is invoked
per step against the STAGED workspace, its TaskCapabilities reach the step
Orchestrator unchanged, and an injected read-only tool executes under the SAME
task budget backend. Real LSP/MCP acceptance stays open (docs STEP_21).
"""
import json
from pathlib import Path

import pytest
from conftest import ScriptedBackend
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task_loop import TaskCapabilities, TaskCapabilityContext, TaskRunner
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig
from atomic_flow.tree.builder import build_tree, save_tree

PLAN = json.dumps({"steps": [{"description": "Probe the environment", "verify_with": "none"}]})


def make_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    save_tree(project, build_tree(project))
    return project


class Probe(Tool):
    name, description = "probe", "Read-only acceptance probe"

    class Args(BaseModel):
        question: str = "state"

    args_schema = Args
    contract = ToolContract(effects=("read",))

    def __init__(self):
        self.observations = []

    def run(self, args, ctx):
        self.observations.append(str(ctx.project_root))
        return {"seen": "staged"}


def scripted_probe_backend():
    return ScriptedBackend({"parser": {}, "answerer": [{"tool": "probe", "args": {}}, {"text": "done"}],
                            "critic": {"verdict": "ok", "citation_coverage": 1}})


def run_probe_task(tmp_path, factory=None, backend=None, *, max_replans=2):
    project = make_project(tmp_path)
    config = AtomicConfig()
    config.task.max_replans = max_replans
    runner = TaskRunner(config, project, backend or scripted_probe_backend(),
                        approval_callback=lambda *a: True, capability_factory=factory)
    task = runner.run_plan("probe", Plan(steps=[PlanStep(description="Probe", verify_with="none")]))
    return project, task


def test_capability_factory_reaches_step_orchestrator(tmp_path, monkeypatch):
    from atomic_flow.agents import task_loop
    from atomic_flow.llm.orchestrator import Orchestrator as Real

    recorded = []

    class Recording(Real):
        def __init__(self, *args, **kwargs):
            recorded.append({"positional": args, **kwargs})
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(task_loop, "Orchestrator", Recording)

    seen_contexts = []
    registry = ToolRegistry()
    registry.register(Probe())

    def factory(context: TaskCapabilityContext) -> TaskCapabilities:
        seen_contexts.append(context)
        return TaskCapabilities(tool_registry=registry, role_tools={"answerer": ["probe"]})

    _, task = run_probe_task(tmp_path, factory=factory)
    assert task.status == "done", task.error

    assert len(seen_contexts) == 1
    context = seen_contexts[0]
    assert context.task_id == task.id and context.phase == "step"
    assert context.project == Path(task.project).resolve()
    # The step ran staged: the factory observed the task-owned changeset workspace,
    # never the original root.
    assert context.workspace != context.project
    assert "changesets" in context.workspace.parts and context.workspace.name == "workspace"
    # The step orchestrator received the injected capabilities unchanged.
    assert recorded[0]["tool_registry"] is registry
    assert recorded[0]["role_tools"] == {"answerer": ["probe"]}


def test_injected_tool_executes_under_shared_task_budget(tmp_path):
    probe = Probe()
    registry = ToolRegistry()
    registry.register(probe)
    _, task = run_probe_task(tmp_path, factory=lambda ctx: TaskCapabilities(
        tool_registry=registry, role_tools={"answerer": ["probe"]}))
    assert task.status == "done", task.error
    assert len(probe.observations) == 1      # the injected tool actually executed in the step
    assert task.budget_events
    assert all(event.get("status") == "completed" for event in task.budget_events)
    # One accounting owner: the ledger total equals the task's recorded spend.
    assert SpendLedger(10).today_total() == pytest.approx(task.budget_spent)


def test_capability_factory_must_return_task_capabilities(tmp_path, monkeypatch):
    from atomic_flow.llm.orchestrator import Orchestrator as Real

    constructed = []

    class Recording(Real):
        def __init__(self, *args, **kwargs):
            constructed.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", Recording)
    backend = ScriptedBackend({"parser": {}, "answerer": [{"text": PLAN}]})
    _, task = run_probe_task(tmp_path, factory=lambda ctx: {"tool_registry": "not-a-capabilities"},
                             backend=backend, max_replans=0)
    assert task.status == "failed" and "TaskCapabilities" in task.error
    assert not constructed            # refused before any orchestrator/model work
