"""Package12 glue exercises user-facing paths with no paid/network models."""
from __future__ import annotations

import json

import pytest
from conftest import ScriptedBackend
from pydantic import BaseModel

from atomic_flow import cli
from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.task import Task, load, save
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.dialogue import prepare_continuation, record_dialogue_state
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.memory import MemoryStore
from atomic_flow.sdk import approve_task, resume_answer, review_task, revise_task
from atomic_flow.sessions import SessionJournal


def parsed(*args):
    return cli._parser().parse_args(args)


def test_cli_plan_review_approve_revise_is_offline_and_digest_bound(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_backend", lambda *a, **kw: pytest.fail("offline review has no backend"))
    task = Task.new("Report the refund rule", project, TaskRunner._steps(Plan(steps=[PlanStep(description="Report the rule", verify_with="none")])))
    save(task)
    assert cli.cmd_task(parsed("task", "--review", task.id)) == 0
    review = json.loads(capsys.readouterr().out)
    assert not load(task.id).plan_approval
    assert cli.cmd_task(parsed("task", "--approve", task.id, "--plan-digest", review["digest"])) == 0
    assert json.loads(capsys.readouterr().out)["execution_started"] is False
    assert load(task.id).plan_approval["digest"] == review["digest"]
    plan_file = project.parent / "revised.json"
    plan_file.write_text(json.dumps({"steps": [{"description": "Report a revised rule", "verify_with": "none"}]}))
    assert cli.cmd_task(parsed("task", "--revise", task.id, "--plan-file", str(plan_file))) == 0
    revised = json.loads(capsys.readouterr().out)
    assert revised["digest"] != review["digest"] and not load(task.id).plan_approval
    with pytest.raises(OSError, match="digest changed"):
        cli.cmd_task(parsed("task", "--approve", task.id, "--plan-digest", review["digest"]))


def test_sdk_offline_plan_api_and_explicit_scope(project):
    task = Task.new("Report sample", project, TaskRunner._steps(Plan(steps=[PlanStep(description="Report sample", verify_with="none")])))
    save(task)
    scope = ["invoice_service.py", "future.py"]
    reviewed = review_task(task.id, allowed_paths=scope)
    assert reviewed["scope_hashes"]["future.py"] is None
    with pytest.raises(OSError, match="digest changed"):
        approve_task(task.id, reviewed["digest"])
    assert approve_task(task.id, reviewed["digest"], allowed_paths=scope).plan_approval
    revised = revise_task(task.id, {"steps": [{"description": "Report another sample", "verify_with": "none"}]})
    assert not revised.plan_approval


def test_cli_memory_requires_write_authorization_and_selected_deletion(project, capsys):
    with pytest.raises(ValueError, match="approv"):
        cli.cmd_memory(parsed("memory", "add", "Keep changes small", "--project", str(project)))
    assert cli.cmd_memory(parsed("memory", "add", "Keep changes small", "--project", str(project), "--yes")) == 0
    entry = json.loads(capsys.readouterr().out)
    assert cli.cmd_memory(parsed("memory", "select", "--id", entry["id"], "--project", str(project))) == 0
    assert json.loads(capsys.readouterr().out)[0]["content"] == "Keep changes small"
    assert cli.cmd_memory(parsed("memory", "update", entry["id"], "Use focused diffs", "--yes", "--project", str(project))) == 0
    assert json.loads(capsys.readouterr().out)["content"] == "Use focused diffs"
    assert cli.cmd_memory(parsed("memory", "delete", entry["id"], "--yes", "--project", str(project))) == 0
    assert json.loads(capsys.readouterr().out) == {"deleted": entry["id"]}
    assert MemoryStore(project).list() == []


def test_cli_session_fork_checkpoint_and_actual_resume(project, config, monkeypatch, capsys):
    model = ScriptedBackend({"answerer": {"text": "First answer", "confidence": "low"}})
    first = Orchestrator(config, project, model).answer("refund", mode="simple")
    assert cli.cmd_session(parsed("session", "checkpoint", first.ledger_id, "--project", str(project), "--goal", "Review refund",
                                  "--constraint", "No API calls", "--decision", "Keep the public signature")) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["summary"]["decisions"] == ["Keep the public signature"]
    assert cli.cmd_session(parsed("session", "fork", first.ledger_id, "--project", str(project), "--query", "Follow up")) == 0
    child = json.loads(capsys.readouterr().out)
    assert child["execution_performed"] is False
    assert prepare_continuation(child["session_id"], project, "Explain refund").context["read_evidence"] is False
    model = ScriptedBackend({"answerer": {"text": "Follow-up", "confidence": "low"}})
    monkeypatch.setattr(cli, "make_backend", lambda *a, **kw: model)
    assert cli.cmd_session(parsed("session", "resume", child["session_id"], "refund", "--project", str(project), "--json")) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["text"] == "Follow-up" and answer["ledger_id"] not in {first.ledger_id, child["session_id"]}
    assert len(model.calls) == 1


def test_sdk_resume_uses_fresh_request_and_no_implicit_edits(project, config):
    model = ScriptedBackend({"answerer": {"text": "Reply", "confidence": "low"}})
    original = Orchestrator(config, project, model).answer("refund", mode="simple")
    handle = resume_answer(config, project, model, original.ledger_id, "refund", mode="simple")
    result = handle.result(5)
    assert result.ledger_id != original.ledger_id and len(model.calls) == 2
    assert list(handle.events())[-1].kind == "run_completed"


def test_cli_rewind_preview_confirmation_and_create_delete_restore(project, capsys):
    existing = project / "invoice_service.py"
    before = existing.read_bytes()
    assert cli.cmd_rewind(parsed("rewind", "snapshot", "before", "invoice_service.py", "new_file.py", "--project", str(project))) == 0
    snapshot = json.loads(capsys.readouterr().out)
    existing.unlink()
    (project / "new_file.py").write_text("NEW = True\n")
    assert cli.cmd_rewind(parsed("rewind", "preview", snapshot["snapshot_id"], "--project", str(project))) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["diff"]
    options = ("rewind", "apply", snapshot["snapshot_id"], "--preview-token", preview["preview_token"], "--project", str(project))
    with pytest.raises(ValueError, match="--yes"):
        cli.cmd_rewind(parsed(*options))
    assert cli.cmd_rewind(parsed(*options, "--yes")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert existing.read_bytes() == before and not (project / "new_file.py").exists()


class WriteArgs(BaseModel):
    path: str
    destination: str | None = None


class ScopedWrite(Tool):
    name, description, args_schema = "move_file", "Scope test", WriteArgs
    contract = ToolContract(effects=("write",), idempotent=False)

    def run(self, args, ctx):
        pytest.fail("unapproved paths cannot load a write implementation")


def test_scope_denies_either_move_path_before_factory_or_approval(project):
    registry, imported, approved = ToolRegistry(), [], []
    registry.register_lazy("move_file", "Scope test", WriteArgs, ScopedWrite.contract, lambda: imported.append(True) or ScopedWrite())
    ctx = ToolContext(project, {}, edits_enabled=True, mutation_scope=frozenset({"invoice_service.py"}),
                      policy=PolicyEngine([Rule("move_file", "ask")]), approval_callback=lambda *a: approved.append(True) or True)
    for path, destination in (("invoice_service.py", "not_approved.py"), ("not_approved.py", "invoice_service.py")):
        result, _ = execute_tool({"tool": "move_file", "args": {"path": path, "destination": destination}}, registry, ctx,
                                 allowed_tools=["move_file"])
        assert result["outcome"] == "denied"
    assert not imported and not approved


def test_api_continuation_and_memory_are_auth_and_project_bound(project, monkeypatch):
    from fastapi.testclient import TestClient

    import atomic_flow.api.server as server
    monkeypatch.setenv("ATOMIC_FLOW_PROJECT_ROOT", str(project))
    monkeypatch.setenv("ATOMIC_FLOW_SERVER_API_KEY", "a" * 32)
    model = ScriptedBackend({"answerer": {"text": "API answer", "confidence": "low"}})
    monkeypatch.setattr(server, "make_backend", lambda *a, **kw: model)
    client = TestClient(server.create_app())
    auth = {"Authorization": "Bearer " + "a" * 32}
    item = MemoryStore(project).add("Small diffs", approved=True)
    data = {"query": "refund", "project": str(project), "mode": "simple", "memory_ids": [item["id"]]}
    assert client.post("/v1/answer", json=data).status_code == 401
    first = client.post("/v1/answer", json=data, headers=auth)
    assert first.status_code == 200 and "Small diffs" in json.dumps(model.calls[-1][1])
    data["resume_from"] = first.json()["ledger_id"]
    second = client.post("/v1/answer", json=data, headers=auth)
    assert second.status_code == 200 and second.json()["ledger_id"] != data["resume_from"]
    (project / "invoice_service.py").write_text("Changed after observed source\n")
    assert client.post("/v1/answer", json=data, headers=auth).status_code == 400


@pytest.mark.parametrize("effect", ["read", "write"])
def test_failed_effectful_tool_remains_unresolved_in_real_dispatcher(project, effect):
    class Broken(ScopedWrite):
        name = "broken_test"
        contract = ToolContract(effects=(effect,), idempotent=False)

        def run(self, args, ctx):
            if effect == "write":
                (ctx.project_root / args.path).write_text("PARTIAL = True\n")
            raise RuntimeError("tool failed after invocation")

    registry = ToolRegistry()
    registry.register(Broken())
    ctx = ToolContext(project, {}, edits_enabled=True, policy=PolicyEngine([Rule("broken_test", "auto")]))
    result, record = execute_tool({"tool": "broken_test", "args": {"path": "partial.py"}}, registry, ctx,
                                  allowed_tools=["broken_test"])
    journal = SessionJournal.create(project)
    journal.append("tool_request", {}, call_id="t1")
    # The live _Run.event normalizes dataclass contract tuples to JSON first.
    journal.append("tool_result", json.loads(json.dumps({"result": result, "call": record})), call_id="t1", outcome="error")
    journal.append("completed", {})
    if effect == "write":
        assert result["side_effects_may_have_occurred"] and record["result_summary"]["side_effects_may_have_occurred"]
        assert journal.read().unresolved_call_ids == ["t1"]
        with pytest.raises(ValueError, match="unresolved"):
            record_dialogue_state(journal.session_id, project, goal="continue")
    else:
        assert not journal.read().unresolved_call_ids
        assert record_dialogue_state(journal.session_id, project, goal="continue")["read_evidence"] is False
