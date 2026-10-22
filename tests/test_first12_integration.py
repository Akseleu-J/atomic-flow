"""Cross-package acceptance: real dispatcher/orchestrator and durable publication."""
import json

import pytest
from conftest import ScriptedBackend
from pydantic import BaseModel

from atomic_flow.agents.base import Tool
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.agents.source import digest
from atomic_flow.agents.task import Task
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.agents.undo import UndoStack
from atomic_flow.backends.base import ChatResult
from atomic_flow.cli import _ask_user_question, main
from atomic_flow.config import AtomicConfig, user_dir
from atomic_flow.llm.orchestrator import Orchestrator, _Run
from atomic_flow.sessions import SessionJournal
from atomic_flow.tree.builder import build_tree, save_tree


def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.py").write_bytes(b"def sample():\n    return 1\n")
    save_tree(root, build_tree(root))
    return root


def final():
    return {"text": "Completed.", "confidence": "high", "citations": [], "missing_context": []}


def test_question_channel_and_session_order(tmp_path):
    root = project(tmp_path)
    backend = ScriptedBackend({"answerer": [
        {"tool": "ask_user", "args": {"question": "Which output?", "options": ["JSON", "Text"]}}, final()]})
    questions = []

    def answer(question):
        snapshot = SessionJournal.open("question_integration").read()
        kinds = [event["event_type"] for event in snapshot.events]
        assert kinds.index("user_input") < kinds.index("model_request") < kinds.index("question")
        questions.append(question)
        return "JSON"

    result = Orchestrator(AtomicConfig(), root, backend, question_callback=answer).answer(
        "sample", mode="simple", request_id="question_integration")
    assert questions and result.tool_calls[0]["result_summary"]["outcome"] == "success"
    snapshot = SessionJournal.open("question_integration").read()
    assert not snapshot.pending_call_ids
    assert any(event["event_type"] == "answer" and event["payload"]["answer"] == "JSON" for event in snapshot.events)


def test_noninteractive_question_never_invents_answer(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert _ask_user_question({"question": "Choose", "options": ["yes"]}) is None


def test_create_publishes_verified_changes_with_task_journal(tmp_path):
    root = project(tmp_path)
    backend = ScriptedBackend({
        "parser": {"task_type": "modify", "focus_terms": ["sample"]},
        "answerer": [{"tool": "create_file", "args": {"path": "created.py", "content": "VALUE = 7\n"}}, final()],
        "critic": {"verdict": "ok", "citation_coverage": 1, "feedback": ""},
    })
    task = TaskRunner(AtomicConfig(), root, backend, approval_callback=lambda *a: True).run_plan(
        "add sample module", Plan(steps=[PlanStep(description="Create sample module", tools_needed=["create_file"])]))
    assert task.status == "done", task.error
    assert (root / "created.py").read_text() == "VALUE = 7\n"
    journal = SessionJournal.open(task.id).read()
    assert journal.events[1]["event_type"] == "user_input" and not journal.pending_call_ids
    # Standalone tools and CLI undo operate on the same project-bound transaction format.
    step_journal = SessionJournal.open(task.steps[0].request_id).read()
    tool_event = next(event for event in step_journal.events if event["event_type"] == "tool_result")
    assert tool_event["payload"]["result"]["changes"][0]["path"] == "created.py"


def test_patch_requires_actual_read_then_can_be_undone(tmp_path):
    root = project(tmp_path)
    expected = digest((root / "sample.py").read_bytes())
    backend = ScriptedBackend({"answerer": [
        {"tool": "read_file", "args": {"path": "sample.py"}},
        {"tool": "patch_file", "args": {"path": "sample.py", "expected_hash": expected,
                                          "old_text": "return 1", "new_text": "return 2"}}, final()]})
    result = Orchestrator(AtomicConfig(), root, backend, approval_callback=lambda *a: True).answer(
        "update sample", mode="simple", approve_edits=True)
    assert result.tool_calls[1]["result_summary"]["status"] == "applied"
    assert "return 2" in (root / "sample.py").read_text()
    record = UndoStack(user_dir() / "edits").undo(root)
    assert record is not None and "return 1" in (root / "sample.py").read_text()


def test_configured_context_rejects_without_call(tmp_path):
    root = project(tmp_path)
    backend = ScriptedBackend({"answerer": final()})
    backend.config.context_window_tokens = 5000
    backend.config.max_output_tokens = 4096
    with pytest.raises(Exception, match="context_window_tokens"):
        Orchestrator(AtomicConfig(), root, backend).answer("sample", mode="simple")
    assert not backend.calls


def test_large_result_retrieval_keeps_action_proof_separate(tmp_path, monkeypatch):
    from atomic_flow.agents import tools

    root = project(tmp_path)

    class Empty(BaseModel):
        pass

    class Probe(Tool):
        name, description, args_schema = "large_probe", "Large data for acceptance", Empty

        def run(self, args, ctx):
            return {"data": "source " * 8000}

    registry = tools.default_registry()
    registry.register(Probe())
    monkeypatch.setattr(tools, "default_registry", lambda: registry)
    monkeypatch.setitem(tools.ROLE_TOOLS, "answerer", [*tools.ROLE_TOOLS["answerer"], "large_probe"])

    class Backend(ScriptedBackend):
        def chat(self, messages, **kwargs):
            self.calls.append(("answerer", messages))
            if len(self.calls) == 1:
                result = {"tool": "large_probe", "args": {}}
            elif len(self.calls) == 2:
                preview = json.loads(messages[-1]["content"].removeprefix("<tool_result>").removesuffix("</tool_result>"))
                assert "data" not in preview and preview["history_body_omitted"]
                result = {"tool": "read_result", "args": {"result_id": preview["result_id"], "limit": 256}}
            else:
                page = json.loads(messages[-1]["content"].removeprefix("<tool_result>").removesuffix("</tool_result>"))
                assert page["original_outcome"] == "success" and page["truncated"]
                assert "status" not in page and "checks" not in page
                result = final()
            return ChatResult(json.dumps(result), 10, 10, 0, 0)

    result = Orchestrator(AtomicConfig(), root, Backend({}), approval_callback=lambda *a: True).answer(
        "sample", mode="simple", request_id="large_integration")
    assert [call["tool"] for call in result.tool_calls] == ["large_probe", "read_result"]
    assert main(["session", "show", "large_integration"]) == 0


def test_unicode_source_page_is_delivered_before_read_is_counted(tmp_path):
    root = project(tmp_path)
    original = "# sample\n" + "😀" * 9000
    (root / "unicode.md").write_bytes(original.encode("utf-8"))
    save_tree(root, build_tree(root))
    backend = ScriptedBackend({"answerer": [
        {"tool": "read_file", "args": {"path": "unicode.md", "limit": 12000}}, final()]})
    Orchestrator(AtomicConfig(), root, backend).answer("sample", mode="simple")
    payload = json.loads(backend.calls[1][1][-1]["content"].removeprefix("<tool_result>").removesuffix("</tool_result>"))
    assert payload["source"] == original and payload["read_complete"]
    assert "result_id" not in payload


@pytest.mark.parametrize("path", [".GIT/new.py", ".ATOMIC_FLOW/config.yaml", "CON.py", "folder./sample.py"])
def test_create_rejects_private_and_os_aliases(tmp_path, path):
    from atomic_flow.agents.context import ToolContext
    from atomic_flow.agents.tools.file_ops import CreateFileArgs, CreateFileTool

    root = project(tmp_path)
    ctx = ToolContext(root, {}, edits_enabled=True, undo_stack=UndoStack(user_dir() / "edits"))
    with pytest.raises(OSError):
        CreateFileTool().run(CreateFileArgs(path=path, content="x=1\n"), ctx)


def test_windows_policy_cannot_be_bypassed_by_case(monkeypatch):
    import atomic_flow.agents.policy as policy
    monkeypatch.setattr(policy.os, "name", "nt")
    engine = PolicyEngine(user_rules=[Rule("create_file", "forbid", path_matches="protected/*")])
    assert engine.decide("create_file", {"path": "Protected/new.py"}, project_trusted=False).action == "forbid"


def test_successful_write_has_terminal_event_before_optional_artifact_failure(tmp_path, monkeypatch):
    root = project(tmp_path)
    backend = ScriptedBackend({"answerer": [{"tool": "create_file", "args": {"path": "created.py", "content": "x=1\n"}}]})
    original = _Run.write_json

    def write(run, name, data):
        if "_tool_" in name:
            raise OSError("injected optional artifact failure")
        return original(run, name, data)

    monkeypatch.setattr(_Run, "write_json", write)
    with pytest.raises(OSError, match="optional artifact"):
        Orchestrator(AtomicConfig(), root, backend, approval_callback=lambda *a: True).answer(
            "sample", mode="simple", request_id="artifact_failure", approve_edits=True)
    snapshot = SessionJournal.open("artifact_failure").read()
    assert not snapshot.pending_call_ids
    applied = next(event for event in snapshot.events if event["event_type"] == "tool_result")
    assert applied["payload"]["result"]["status"] == "applied"
    assert (root / "created.py").exists()


def test_interrupted_task_journal_creation_saves_input_before_first_request(tmp_path):
    root = project(tmp_path)
    task = Task.new("sample", root, [])
    SessionJournal.create(root, task.id)  # Simulate death between create and input append.
    backend = ScriptedBackend({})
    wrapped = TaskBudgetBackend(backend, task, AtomicConfig(), lambda: None)
    snapshot = wrapped.journal.read()
    assert [event["event_type"] for event in snapshot.events] == ["session_created", "user_input"]
