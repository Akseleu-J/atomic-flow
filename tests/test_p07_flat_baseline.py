"""Flat loop uses real host tools with scripted model and a mocked ordinary checker."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from atomic_flow import check_execution
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.control import task_lease
from atomic_flow.agents.execution import CommandResult
from atomic_flow.agents.plan_review import review_payload
from atomic_flow.agents.policy import PolicyEngine
from atomic_flow.agents.task import Step, Task, save
from atomic_flow.agents.task_budget import TaskBudgetBackend
from atomic_flow.agents.tools import default_registry
from atomic_flow.agents.undo import UndoStack
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.backends.base import ChatResult
from atomic_flow.check_execution import CheckReceipt, _runner_receipt
from atomic_flow.check_obligation import (
    CheckObligation,
    check_observation,
    failed_observation_matches,
    run_obligation_check,
)
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.tree.builder import build_tree, save_tree
from atomic_flow.tree.node import TreeNode
from tools.p07_flat_baseline import FLAT_TOOLS, run_flat_attempt

ORIGINALS = {"a.py": "from b import original\n\ndef call():\n    return original()\n",
             "b.py": "def original():\n    return 42\n"}
TOOLS = tuple(sorted(FLAT_TOOLS))


class Scripted:
    name = "flat-scripted"

    def __init__(self, actions):
        self.config = BackendConfig(model="scripted", max_input_tokens=200_000, max_output_tokens=4096,
                                    max_retries=0, price_in_per_mtok=0, price_out_per_mtok=0)
        self.actions = list(actions)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        if not self.actions:
            raise AssertionError("unexpected extra model call")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return ChatResult(action if isinstance(action, str) else json.dumps(action), 10, 5, 0, 0,
                          cost_source="scripted_zero_provider_calls")


@pytest.fixture
def host(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for relative, text in ORIGINALS.items():
        (project / relative).write_bytes(text.encode())
    (project / "tests").mkdir()
    (project / "tests/test_sample.py").write_bytes(b"def test_sample():\n    assert True\n")
    tree = build_tree(project)
    save_tree(project, tree)
    spec = CheckSpec("public_flat_example", "a" * 40, "b" * 40, check_source_revision(project),
                     "example/checks@sha256:" + "c" * 64, "/usr/local/bin/python",
                     "pytest", ("tests/test_sample.py",))
    deadline = time.time() + 600
    bound = CheckObligation.seal(spec, project, environment_digest="d" * 64, deadline_at=deadline)
    checker_calls = []

    def checker(current, workspace, *, cancel_check=None):
        # Host script checks fixture bytes; no target import/execution occurs.
        passed = all("original" not in (workspace / relative).read_text(encoding="utf-8") for relative in ORIGINALS)
        checker_calls.append((current, passed))
        code = 0 if passed else 1
        data = {"nonce": "e" * 32, "root": current.target_root, "interpreter": current.interpreter,
                "interpreter_resolved": "/usr/local/bin/python3.11", "pytest_module": "/opt/checker/pytest.py",
                "pytest_version": "8.3.0", "collected": 1, "files": sorted(current.test_paths),
                "skipped": 0, "failed": code, "finished": True, "call_failed": code,
                "setup_failed": 0, "teardown_failed": 0, "exit_code": code}
        result = CommandResult(code, "ATOMIC_CHECK_SPEC_RECEIPT:" + json.dumps(data), "")
        parsed = _runner_receipt(result, current, "e" * 32)
        assert parsed is not None
        return CheckReceipt(current.digest, current.source_revision, current.source_revision, passed, parsed, result)

    monkeypatch.setattr(check_execution, "run_check_spec", checker)
    config = AtomicConfig()
    config.execution.mode, config.execution.profile, config.execution.image = "docker", "checks", spec.image
    config.task.max_model_calls = 30
    step = Step(0, "Resolve public issue", status="running", started_at=time.time(), verify_with="ordinary_test",
                scope_paths=["a.py", "b.py"])
    task = Task.new("Rename original to renamed consistently in public source", project, [step])
    changeset = ChangeSet.create(project)
    task.changeset_id = changeset.id
    workspace = changeset.workspace
    tree = build_tree(workspace)
    save_tree(workspace, tree)
    nodes = {item["node_id"]: TreeNode.model_validate(item) for item in tree["nodes"]}
    context = ToolContext(workspace, nodes, edits_enabled=True, undo_stack=UndoStack(tmp_path / "undo"),
                          policy=PolicyEngine(), trusted=True, approval_callback=lambda *args: True,
                          private_workspace=True, enforce_read_before_edit=True, mutation_scope=frozenset(ORIGINALS),
                          check_obligation=bound, execution=config.execution)
    return SimpleNamespace(root=workspace, project=project, changeset=changeset, bound=bound, config=config,
                           task=task, step=step, context=context, registry=default_registry(),
                           deadline=deadline - 1, checker_calls=checker_calls)


def run(host, scripted, **changes):
    # The test parent supplies genuine approval/persistence and holds the same
    # OS task lease as TaskRunner.resume. The executor never grants this authority.
    with task_lease(host.task.id):
        host.task.plan_approval = review_payload(host.task, host.project, ["a.py", "b.py"],
                                                check_obligation=host.bound)
        save(host.task)
        budget = TaskBudgetBackend(scripted, host.task, host.config, lambda: None)
        budget.bind_steps([host.step.index])
        arguments = dict(query=host.task.query, step=host.step, context=host.context, registry=host.registry,
                         budget=budget, allowed_tools=TOOLS, max_calls=20, deadline_at=host.deadline)
        arguments.update(changes)
        result = run_flat_attempt(**arguments)
        # Same parent tail as TaskRunner._execute_step: retain actual verified
        # writes in the changeset before any subsequent review or phase.
        for record in result.tool_calls:
            summary = record.get("result_summary") or {}
            if record["tool"] in {"patch_file", "patch_files"} and summary.get("status") == "applied":
                for change in summary.get("changes") or [{"path": summary["path"], "after_hash": summary["content_hash"]}]:
                    host.changeset.track(change["path"], change["after_hash"])
        host.changeset.validate_workspace()
        return result


def action(tool, **args):
    return {"tool": tool, "args": args}


def patches():
    return [{"path": relative, "expected_hash": hashlib.sha1(text.encode()).hexdigest(),
             "old_text": text, "new_text": text.replace("original", "renamed")}
            for relative, text in ORIGINALS.items()]


def test_actual_shared_find_search_read_multifile_patch_and_check_without_atomic_scaffold(host):
    scripted = Scripted([
        action("find_files", pattern="*.py"), action("search_text", query="original", regex=False),
        action("read_file", path="a.py"), action("read_file", path="b.py"),
        action("patch_files", patches=patches()), action("run_command", command="check"),
        {"done": True, "summary": "Coupled change prepared; host must verify."},
    ])
    audited = []
    result = run(host, scripted, record_callback=lambda observed, record: audited.append((observed, record)))
    assert result.final_requested and result.stop_reason == "final_requested"
    assert result.model_calls == 7 and len(result.tool_calls) == len(result.tool_results) == len(audited) == 6
    assert [record["tool"] for record in result.tool_calls] == [
        "find_files", "search_text", "read_file", "read_file", "patch_files", "run_command"]
    assert result.tool_calls[4]["result_summary"]["status"] == "applied"
    assert result.tool_calls[5]["result_summary"]["check_passed"] is True
    assert all((host.root / relative).read_text(encoding="utf-8") == text.replace("original", "renamed")
               for relative, text in ORIGINALS.items())
    assert result.verdict == "unverified" and not result.check_failure_observed
    assert len(host.task.budget_events) == 7
    assert all(event["status"] == "completed" for event in host.task.budget_events)
    first_prompt = json.dumps(scripted.calls[0])
    for excluded in ("Host read plan", "<role>parser", "<role>critic", "frontier_context", "gold", "FAIL_TO_PASS"):
        assert excluded not in first_prompt
    assert result.history_stats["peak_request_bytes"] <= host.config.conversation.max_request_bytes


def test_negative_ends_attempt_then_parent_fresh_failure_can_admit_separate_repair(host):
    scripted = Scripted([action("run_command", command="check"),
                         action("read_file", path="a.py"), action("read_file", path="b.py"),
                         action("patch_files", patches=patches()), action("run_command", command="check"),
                         {"done": True}])
    first = run(host, scripted)
    assert first.stop_reason == "negative_tool_result" and first.model_calls == 1
    assert first.check_failure_observed and not first.final_requested
    assert len(first.tool_calls) == 1 and first.tool_calls[0]["result_summary"]["outcome"] == "error"
    assert len(scripted.actions) == 5
    assert all((host.root / relative).read_text(encoding="utf-8") == text for relative, text in ORIGINALS.items())
    preserved = copy.deepcopy(first.tool_calls)
    # The parent performs the actual current ordinary check; the helper supplies
    # no completed-negative flag, retry decision or synthetic planner response.
    current, receipt = run_obligation_check(host.bound, host.root)
    observation = check_observation(host.bound, current, receipt)
    assert failed_observation_matches(host.bound, observation, check_source_revision(host.root))
    host.step.status = "superseded"
    host.step = Step(1, "Continue public flat repair", status="running", started_at=time.time(),
                     verify_with="ordinary_test", scope_paths=["a.py", "b.py"])
    host.task.steps.append(host.step)
    second = run(host, scripted)
    assert second.final_requested and second.model_calls == 5
    assert first.tool_calls == preserved
    assert [record["tool"] for record in first.tool_calls + second.tool_calls] == [
        "run_command", "read_file", "read_file", "patch_files", "run_command"]
    assert len(host.task.budget_events) == 6
    assert {event["step_index"] for event in host.task.budget_events} == {0, 1}


def test_denied_tool_or_unread_edit_stops_before_next_model_turn_and_preserves_record(host):
    for actions in [
        [action("search_tree", query="original"), {"done": True}],
        [action("patch_files", patches=patches()), {"done": True}],
    ]:
        scripted = Scripted(actions)
        result = run(host, scripted)
        assert result.stop_reason == "negative_tool_result"
        assert result.model_calls == 1 and len(result.tool_calls) == 1 and len(scripted.actions) == 1
        assert result.tool_calls[0]["result_summary"]["outcome"] in {"error", "denied"}
        assert all((host.root / relative).read_text(encoding="utf-8") == text for relative, text in ORIGINALS.items())


@pytest.mark.parametrize("args", [{"command": "pytest"}, {"command": "check", "target": "tests/test_sample.py"}])
def test_command_aliases_and_test_selection_are_refused_before_dispatch(host, args):
    result = run(host, Scripted([{"tool": "run_command", "args": args}, {"done": True}]))
    assert result.stop_reason == "command_protocol_refused" and not result.tool_calls
    assert host.checker_calls == []


@pytest.mark.parametrize("text", [
    '{"done":true,"done":true}', '{"done":true,"unapproved":1}', '{"tool":"read_file","args":{},"extra":1}',
    '{"done":true,"summary":NaN}', "not JSON",
])
def test_bad_protocol_stops_without_tool_or_followup(host, text):
    scripted = Scripted([text, {"done": True}])
    result = run(host, scripted)
    assert result.stop_reason == "response_protocol_refused"
    assert result.model_calls == 1 and result.tool_calls == [] and len(scripted.actions) == 1


def test_repeated_source_tool_requests_use_shared_history_guard(host):
    scripted = Scripted([action("find_files", pattern="*.py")] * 10)
    result = run(host, scripted)
    assert result.stop_reason == "tool_execution_or_cancel"
    assert result.error_type == "RepeatedToolCall"
    assert len(result.tool_calls) == host.config.conversation.max_same_tool_call


def test_call_cap_deadline_and_unknown_model_do_not_continue(host):
    limited = run(host, Scripted([action("read_file", path="a.py"), {"done": True}]), max_calls=1)
    assert limited.stop_reason == "call_limit" and limited.model_calls == 1
    expired = run(host, Scripted([{"done": True}]), now=lambda: host.deadline + 1)
    assert expired.stop_reason == "deadline" and expired.model_calls == 0
    failed = run(host, Scripted([RuntimeError("private model exception"), {"done": True}]))
    assert failed.stop_reason == "model_failed_no_retry" and failed.model_calls == 1
    assert failed.error_type == "BudgetExceeded"
    assert failed.final_text == ""


def test_audit_failure_retains_actual_record_and_stops(host):
    def broken(observed, record):
        raise RuntimeError("audit sink unavailable")
    scripted = Scripted([action("read_file", path="a.py"), {"done": True}])
    result = run(host, scripted, record_callback=broken)
    assert result.stop_reason == "audit_failed"
    assert len(result.tool_calls) == 1 and len(scripted.actions) == 1


def test_context_and_allowed_tool_authority_cannot_be_widened(host):
    scripted = Scripted([{"done": True}])
    for changes in [
        {"allowed_tools": ("read_file", "search_tree")}, {"allowed_tools": ("read_file", "read_file")},
        {"allowed_tools": ["read_file"]}, {"deadline_at": host.bound.deadline_at + 1},
    ]:
        with pytest.raises(ValueError):
            run(host, scripted, **changes)
    host.context.enforce_read_before_edit = False
    with pytest.raises(ValueError):
        run(host, scripted)
    assert not scripted.calls


def test_incomplete_or_infrastructure_check_is_only_an_observation_no_repair_authority(host, monkeypatch):
    def unavailable(spec, workspace, *, cancel_check=None):
        command = CommandResult(2, "collection infrastructure failed", "")
        return CheckReceipt(spec.digest, spec.source_revision, spec.source_revision, False, None, command)
    monkeypatch.setattr(check_execution, "run_check_spec", unavailable)
    result = run(host, Scripted([action("run_command", command="check"), {"done": True}]))
    summary = result.tool_calls[0]["result_summary"]
    assert result.stop_reason == "negative_tool_result" and result.check_failure_observed
    assert summary["check_completed"] is False and summary["check_failed_completed"] is False
    assert not failed_observation_matches(host.bound, summary, check_source_revision(host.root))
