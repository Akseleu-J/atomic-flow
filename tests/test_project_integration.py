import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.source import digest
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.task_loop import StepFailure, TaskRunner
from atomic_flow.agents.tools import default_registry
from atomic_flow.agents.undo import UndoStack
from atomic_flow.cli import main
from atomic_flow.config import AtomicConfig
from atomic_flow.dialogue import prepare_continuation, record_dialogue_state
from atomic_flow.sdk import open_lsp, workspace
from atomic_flow.sessions import SessionJournal


def notebook_file(root, *, legacy=False):
    value = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [{
        "cell_type": "code", "metadata": {"trusted": True}, "source": "value = 1\n",
        "outputs": [{"output_type": "stream", "name": "stdout", "text": "PRIVATE_SAVED_OUTPUT"}],
        "execution_count": 1, **({} if legacy else {"id": "cell_a"})}]}
    path = root / "book.ipynb"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_notebook_cli_preview_apply_and_raw_source_denial(tmp_path, capsys):
    notebook_file(tmp_path)
    assert main(["notebook", "read", "book.ipynb", "--project", str(tmp_path)]) == 0
    read = json.loads(capsys.readouterr().out)
    assert "PRIVATE_SAVED_OUTPUT" not in json.dumps(read)
    operation = json.dumps({"action": "set_source", "cell_id": "cell_a", "source": "value = 2\n"})
    assert main(["notebook", "preview", "book.ipynb", "--project", str(tmp_path), "--operation-json", operation]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main(["notebook", "apply", "book.ipynb", "--project", str(tmp_path), "--operation-json", operation,
                 "--preview-token", preview["preview_token"], "--expected-hash", preview["content_hash"], "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert json.loads((tmp_path / "book.ipynb").read_text())["cells"][0]["outputs"] == []
    result, _ = execute_tool({"tool": "read_file", "args": {"path": "book.ipynb"}}, default_registry(), ToolContext(tmp_path, {}), allowed_tools=["read_file"])
    assert "error" in result and "PRIVATE_SAVED_OUTPUT" not in json.dumps(result)


def test_notebook_cli_legacy_normalization_is_reviewable_across_invocations(tmp_path, capsys):
    notebook_file(tmp_path, legacy=True)
    args = ["notebook", "normalize-ids", "book.ipynb", "--project", str(tmp_path)]
    assert main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main([*args, "--preview-token", preview["preview_token"], "--expected-hash", preview["content_hash"], "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert json.loads((tmp_path / "book.ipynb").read_text())["cells"][0]["id"].startswith("af_")


def test_notebook_dispatch_scope_gate_precedes_approval_and_success_refresh(tmp_path):
    notebook_file(tmp_path)
    approvals = []
    ctx = ToolContext(tmp_path, {}, edits_enabled=True, undo_stack=UndoStack(tmp_path / "edits"),
        mutation_scope=frozenset({"other.py"}), approval_callback=lambda *args: approvals.append(args) or True)
    registry = default_registry()
    read, _ = execute_tool({"tool": "notebook_read", "args": {"path": "book.ipynb"}}, registry, ctx, allowed_tools=["notebook_read"])
    call = {"tool": "notebook_edit", "args": {"path": "book.ipynb", "expected_hash": read["content_hash"],
        "operation": {"action": "set_source", "cell_id": "cell_a", "source": "value = 2\n"}}}
    result, _ = execute_tool(call, registry, ctx, allowed_tools=["notebook_edit"])
    assert result["outcome"] == "denied" and not approvals
    ctx.mutation_scope = frozenset({"book.ipynb"})
    preview, _ = execute_tool(call, registry, ctx, allowed_tools=["notebook_edit"])
    call["args"]["preview_token"] = preview["preview_token"]
    applied, _ = execute_tool(call, registry, ctx, allowed_tools=["notebook_edit"])
    assert applied["status"] == "applied"
    assert ctx.file_hashes["book.ipynb"] == digest((tmp_path / "book.ipynb").read_bytes())
    assert not ctx.read_coverage("book.ipynb", ctx.file_hashes["book.ipynb"])["read_complete"]


def test_workspace_cli_explicit_grant_tool_namespace_and_revocation(tmp_path, capsys):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n")
    grant = ["workspace", "grant", "app", str(root), "--workspace", "demo"]
    assert main(grant) == 2
    capsys.readouterr()
    assert main([*grant, "--yes"]) == 0
    capsys.readouterr()
    ctx = ToolContext(tmp_path, {}, workspace_session=workspace("demo").session())
    call = {"tool": "workspace_read", "args": {"root_id": "app", "path": "main.py"}}
    result, record = execute_tool(call, default_registry(), ctx, allowed_tools=["workspace_read"])
    assert result["root_id"] == "app" and result["workspace_id"] == "demo"
    assert record["result_summary"]["root_id"] == "app" and not ctx.reads
    assert not ctx.workspace_session._reads
    assert main(["workspace", "revoke", "app", "--workspace", "demo", "--yes"]) == 0
    capsys.readouterr()
    result, _ = execute_tool(call, default_registry(), ctx, allowed_tools=["workspace_read"])
    assert "error" in result


def test_lsp_cli_and_read_tool_use_only_host_approved_server(tmp_path, capsys):
    (tmp_path / "a.py").write_text("a = 1\n")
    server = Path(__file__).parent / "fixtures/lsp_server.py"
    command = [sys.executable, str(server.resolve()), "normal"]
    args = ["lsp", "definition", "a.py", "--project", str(tmp_path), "--command-json", json.dumps(command)]
    assert main(args) == 2
    capsys.readouterr()
    assert main([*args, "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["path"] == "a.py"
    call = {"tool": "lsp_definition", "args": {"path": "a.py", "line": 0, "character": 0}}
    registry = default_registry()
    ctx = ToolContext(tmp_path, {})
    result, _ = execute_tool(call, registry, ctx, allowed_tools=["lsp_definition"])
    assert "error" in result
    with open_lsp(tmp_path, command, approved=True) as client:
        ctx.lsp_client = client
        result, _ = execute_tool(call, registry, ctx, allowed_tools=["lsp_definition"])
        assert result["results"][0]["path"] == "a.py" and not result["read_complete"]
        assert not ctx.reads


def test_read_authority_is_reverted_when_dispatcher_cancels_after_tool_return(tmp_path):
    cancelled = False
    class Args(BaseModel):
        pass
    class ReadProbe(Tool):
        name = "read_probe"
        description = "Test delivered read proof"
        args_schema = Args
        contract = ToolContract(effects=("read",))
        def run(self, args, ctx):
            nonlocal cancelled
            ctx.record_read("a.py", "a" * 40, "file", 0, 1, 1, source_complete=True)
            cancelled = True
            return {"source": "x"}
    def check():
        if cancelled:
            raise TaskCancelled("cancel after read")
    ctx = ToolContext(tmp_path, {}, cancel_check=check, policy=PolicyEngine([Rule("read_probe", "auto")]))
    registry = ToolRegistry()
    registry.register(ReadProbe())
    with pytest.raises(TaskCancelled):
        execute_tool({"tool": "read_probe", "args": {}}, registry, ctx, allowed_tools=["read_probe"])
    assert ctx.reads == []


def test_notebook_checkpoint_checks_whole_file_without_carrying_read_authority(tmp_path):
    path = notebook_file(tmp_path)
    journal = SessionJournal.create(tmp_path, session_id="session_nb14")
    journal.append("completed", {})
    record_dialogue_state(journal.session_id, tmp_path, goal="Notebook review", source_paths=["book.ipynb"])
    continuation = prepare_continuation(journal.session_id, tmp_path, "continue")
    assert not continuation.context["read_evidence"]
    value = json.loads(path.read_text())
    value["cells"][0]["outputs"] = []
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError), match="stale|changed"):
        prepare_continuation(journal.session_id, tmp_path, "continue")


def test_completion_validates_notebook_structure_without_executing_cells(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    notebook_file(project)
    changeset = ChangeSet.create(project)
    path = changeset.workspace / "book.ipynb"
    value = json.loads(path.read_text())
    value["cells"][0]["source"] = "raise RuntimeError('must not execute')"
    path.write_text(json.dumps(value))
    changeset.track("book.ipynb", digest(path.read_bytes()))
    task = Task.new("update notebook", project, [Step(0, "update cell", status="done")])
    task.current_index = 1
    report = assess_completion(task, changeset, AtomicConfig())
    assert report["ready_to_publish"]
    assert any(item["name"] == "notebook_structure" and item["execution"] == "not_run" for item in report["checks"])
    value["cells"][0].pop("id")
    path.write_text(json.dumps(value))
    changeset.track("book.ipynb", digest(path.read_bytes()))
    assert not assess_completion(task, changeset, AtomicConfig())["ready_to_publish"]


def test_optional_project_tools_have_deferred_schemas():
    registry = default_registry()
    names = ["notebook_read", "notebook_edit", "workspace_read", "lsp_definition"]
    assert registry.schemas(names) == []
    assert len(registry.schemas(names, include_deferred=True)) == 4


def test_notebook_preview_is_not_a_completed_task_edit(tmp_path):
    runner = TaskRunner(AtomicConfig(), tmp_path, None)
    step = Step(0, "edit notebook", tools_needed=["notebook_edit"], verify_with="none")
    preview = {"tool": "notebook_edit", "result_summary": {"status": "preview", "outcome": "success"}}
    with pytest.raises(StepFailure, match="no edit was applied"):
        runner._verify_step(step, SimpleNamespace(tool_calls=[preview], verdict="PASS"))
    applied = {"tool": "notebook_edit", "result_summary": {"status": "applied", "outcome": "success",
        "checks": ["notebook_schema"], "verification": {"notebook_schema": "passed", "notebook_execution": "not_run"}}}
    runner._verify_step(step, SimpleNamespace(tool_calls=[preview, applied], verdict="PASS"))
    applied["result_summary"]["verification"] = {"notebook_schema": "not_run"}
    with pytest.raises(StepFailure, match="structural verification"):
        runner._verify_step(step, SimpleNamespace(tool_calls=[applied], verdict="PASS"))


def test_taskrunner_publishes_verified_notebook_hash_without_cell_execution(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    notebook_file(project)
    (project / "main.py").write_text("value = 1\n")
    class ScriptedOrchestrator:
        def __init__(self, config, root, backend, **options):
            self.ctx = ToolContext(root, {}, edits_enabled=True, private_workspace=True,
                enforce_read_before_edit=True, mutation_scope=frozenset(options["mutation_scope"]),
                approval_callback=lambda *a: True, undo_stack=UndoStack(tmp_path / "undo"))
        def answer(self, *args, **kwargs):
            registry, records = default_registry(), []
            def call(name, args):
                result, record = execute_tool({"tool": name, "args": args}, registry, self.ctx,
                                              allowed_tools=["notebook_read", "notebook_edit"])
                records.append(record)
                assert result["outcome"] == "success", result
                return result
            read = call("notebook_read", {"path": "book.ipynb"})
            operation = {"path": "book.ipynb", "expected_hash": read["content_hash"],
                "operation": {"action": "set_source", "cell_id": "cell_a", "source": "raise RuntimeError('not executed')"}}
            preview = call("notebook_edit", operation)
            call("notebook_edit", {**operation, "preview_token": preview["preview_token"]})
            return SimpleNamespace(tool_calls=records, ledger_id="scripted_nb", verdict="PASS")
    monkeypatch.setattr("atomic_flow.agents.task_loop.Orchestrator", ScriptedOrchestrator)
    runner = TaskRunner(AtomicConfig(), project, None, approval_callback=lambda *a: True)
    task = runner.run_plan("Edit notebook source", Plan(steps=[PlanStep(description="Update cell source", tools_needed=["notebook_edit"],
                                                                      verify_with="none")]), allowed_paths=["book.ipynb"])
    assert task.status == "done", task.error
    value = json.loads((project / "book.ipynb").read_text())
    assert value["cells"][0]["source"] == "raise RuntimeError('not executed')" and not value["cells"][0]["outputs"]
