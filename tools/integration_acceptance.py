"""Real pinned LSP/MCP processes in reviewed staged TaskRunner journeys.

Only host argv select executables. Run after explicitly installing acceptance
dependencies; this script never downloads tools or calls an LLM service.
Only trusted synthetic projects execute the local regression checker; this does
not establish operating-system isolation for untrusted source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path

from pydantic import BaseModel

from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.control import request_cancel
from atomic_flow.agents.planner import Plan, PlanStep
from atomic_flow.agents.task import save
from atomic_flow.agents.task_loop import TaskCapabilities, TaskRunner
from atomic_flow.agents.tools import default_registry
from atomic_flow.backends.base import ChatResult
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.integrations.mcp import MCPClient, schema_hash
from atomic_flow.lsp import LSPClient
from atomic_flow.tree.builder import build_tree, save_tree

BEFORE = "def answer():\n    return 1\n\nvalue = answer()\n"
AFTER = BEFORE.replace("return 1", "return 2")
EXTERNAL = BEFORE.replace("return 1", "return 9")
NOTEBOOK_OUTPUT = "ACCEPTANCE_SAVED_OUTPUT_MUST_STAY_PRIVATE"
NOTEBOOK_OPERATION = {"action": "set_source", "cell_id": "cell-one", "source": "value = 2\n"}


def notebook_bytes():
    value = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"signature": "acceptance-trust-marker"},
             "cells": [{"id": "cell-one", "cell_type": "code", "source": "value = 1\n",
                        "metadata": {"trusted": True}, "execution_count": 3,
                        "outputs": [{"output_type": "stream", "name": "stdout", "text": NOTEBOOK_OUTPUT}]}]}
    return (json.dumps(value, indent=1) + "\n").encode("utf-8")


def notebook_apply_reply(messages):
    """Use the actual delivered preview, without minting read/approval authority."""
    text = "\n".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
    match = re.search(r'"preview_token"\s*:\s*"([a-f0-9]{64})"', text)
    assert match, "notebook preview token was not delivered to the scripted model"
    return {"tool": "notebook_edit", "args": {"path": "book.ipynb",
            "expected_hash": hashlib.sha1(notebook_bytes()).hexdigest(),
            "operation": NOTEBOOK_OPERATION, "preview_token": match.group(1)}}


class ScriptedBackend:
    name = "acceptance-scripted-no-network"

    def __init__(self):
        self.config = BackendConfig(model="scripted", max_output_tokens=2048, price_in_per_mtok=1, price_out_per_mtok=1)
        self.calls = []
        self.plan = Plan(steps=[PlanStep(description="Change sample return value to two and inspect both source revisions",
                                        tools_needed=["patch_file"], verify_with="py_compile"),
                                PlanStep(description="Change notebook cell source to two and run the host regression checker",
                                         tools_needed=["notebook_edit"], verify_with="pytest")],
                         rationale="Use reviewed source-only edits and a host regression checker.")
        self.replies = iter([
            {"tool": "external_probe", "args": {"phase": "before"}},
            {"tool": "read_file", "args": {"path": "sample.py"}},
            {"tool": "patch_file", "args": {"path": "sample.py", "expected_hash": hashlib.sha1(BEFORE.encode()).hexdigest(),
                                                "old_text": "return 1", "new_text": "return 2", "reason": "acceptance change"}},
            {"tool": "external_probe", "args": {"phase": "after"}},
            {"text": "Completed the reviewed Python source change."},
            {"tool": "notebook_read", "args": {"path": "book.ipynb"}},
            {"tool": "notebook_edit", "args": {"path": "book.ipynb",
                "expected_hash": hashlib.sha1(notebook_bytes()).hexdigest(), "operation": NOTEBOOK_OPERATION}},
            notebook_apply_reply,
            {"text": "Completed the reviewed source change."},
        ])

    def chat(self, messages, **kwargs):
        text = "\n".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
        assert NOTEBOOK_OUTPUT not in text, "saved notebook output leaked into model context"
        match = re.search(r"<role>(\w+)</role>", messages[1]["content"])
        role = match.group(1) if match else "planner"
        self.calls.append(role)
        if role == "planner":
            assert "PLANNER" in messages[1]["content"], "unexpected untyped model request"
            value = self.plan.model_dump()
        elif role == "parser":
            value = {}
        elif role == "critic":
            value = {"verdict": "ok", "citation_coverage": 1}
        elif role == "answerer":
            value = next(self.replies)
            if callable(value):
                value = value(messages)
        else:
            raise AssertionError(f"unexpected scripted role {role}")
        return ChatResult(json.dumps(value), 100, 50, 0.001, 1)


def run_single(args, mode="publish", evidence=None):
    if evidence is None:
        evidence = {}
    evidence.update(schema_version=2, name=mode, status="running", external_services="real local processes",
                    model="scripted; no model quality claim", paid_calls=0, observations=[])
    print(f"acceptance scenario={mode} start", flush=True)
    with tempfile.TemporaryDirectory(prefix="atomic-external-acceptance-") as tmp, ExitStack() as clients:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        (project / "sample.py").write_bytes(BEFORE.encode("utf-8"))
        (project / "book.ipynb").write_bytes(notebook_bytes())
        test_bytes = b"from sample import answer\n\ndef test_answer():\n    assert answer() == 2\n"
        (project / "test_sample.py").write_bytes(test_bytes)
        os.environ["ATOMIC_FLOW_HOME"] = str(root / "host-state")
        os.environ["ATOMIC_FLOW_EXECUTION_MODE"] = "local"
        save_tree(project, build_tree(project))
        backend = ScriptedBackend()

        class Probe(Tool):
            name, description = "external_probe", "Read source through real host-approved LSP and MCP clients"
            contract = ToolContract(effects=("read",), timeout_seconds=90)

            class Args(BaseModel):
                phase: str

            args_schema = Args

            def run(self, params, ctx):
                assert params.phase in {"before", "after"}
                expected = BEFORE if params.phase == "before" else AFTER
                assert ctx.project_root != project
                assert (project / "sample.py").read_text(encoding="utf-8") == BEFORE
                doc = lsp.read_document("sample.py")
                definitions = lsp.definition("sample.py", 3, 10)
                assert doc["text"] == expected, "LSP document bytes differ from expected revision"
                assert definitions and definitions[0]["path"] == "sample.py", f"unexpected definitions: {definitions}"
                result = mcp.call_tool("read_text_file", {"path": str(ctx.project_root / "sample.py")})
                text = "\n".join(block.get("text", "") for block in result["remote_result"]["content"])
                assert expected.strip() in text, "MCP source response differs from staged revision"
                evidence["observations"].append({"phase": params.phase, "workspace": "staged", "version": doc["version"],
                                                   "hash": doc["content_hash"], "definitions": len(definitions)})
                if params.phase == "after" and mode == "cancel":
                    request_cancel(task.id)
                    evidence["host_fault"] = "cancel after staged edit and completed read-only external probes"
                elif params.phase == "after" and mode == "source_conflict":
                    (project / "sample.py").write_bytes(EXTERNAL.encode())
                    evidence["host_fault"] = "external source editor changed original after staged edit"
                return {"phase": params.phase, "data_only": True, "definition_count": len(definitions)}

        def factory(context):
            nonlocal lsp, mcp
            assert context.workspace != context.project
            evidence["factory_bindings"] = evidence.get("factory_bindings", 0) + 1
            lsp_command = [str(args.pylsp)] + (["-m", "pylsp"] if args.pylsp_python else [])
            lsp = clients.enter_context(LSPClient(context.workspace, lsp_command, approved=True, timeout=45, private_workspace=True))
            mcp = clients.enter_context(MCPClient(context.workspace, [str(args.node), str(args.mcp_server), str(context.workspace)],
                                                  approved=True, timeout=45, approval=lambda method, params: method == "tools/call"))
            tools = mcp.list_tools()
            descriptor = next(t for t in tools if t["name"] == "read_text_file")
            mcp.allowed_tools["read_text_file"] = schema_hash(descriptor)
            registry = default_registry()
            registry.register(Probe())
            return TaskCapabilities(lsp_client=lsp, tool_registry=registry,
                                    role_tools={"answerer": ["external_probe", "read_file", "patch_file",
                                                              "notebook_read", "notebook_edit"]})

        lsp = mcp = None
        config = AtomicConfig()
        config.task.max_replans = 0
        config.task.max_model_calls = 16
        config.task.final_check = "pytest"
        runner = TaskRunner(config, project, backend, approval_callback=lambda *a: True, capability_factory=factory)
        task = runner.prepare("Change sample and notebook source to two and verify through external source tools",
                              candidates=["sample.py::answer"], n_files=3)
        assert task.status == "paused" and task.plan_ready and len(backend.calls) == 1
        assert not task.plan_approval and not task.changeset_id
        task = runner.resume(task)
        assert task.status == "paused" and "plan approval required" in task.error
        assert not task.changeset_id and len(backend.calls) == 1
        review = runner.review(task, allowed_paths=["sample.py", "book.ipynb"])
        task = runner.approve(task, review["digest"], allowed_paths=["sample.py", "book.ipynb"])
        assert task.plan_approval["digest"] == review["digest"]
        assert not task.changeset_id and len(backend.calls) == 1
        evidence["review"] = {"unapproved_resume_blocked": True, "allowed_paths": review["allowed_paths"],
                              "approval_digest": review["digest"], "planning_calls": 1}
        task = runner.resume(task)
        print(f"acceptance scenario={mode} task_status={task.status}", flush=True)
        assert len(evidence["observations"]) == 2, f"missing completed external probes: {task.error}"
        assert evidence["observations"][1]["version"] > evidence["observations"][0]["version"]
        assert abs(SpendLedger(10).today_total() - task.budget_spent) < 1e-8
        assert all(event["status"] == "completed" for event in task.budget_events)
        assert len(task.budget_events) == len(backend.calls)
        evidence.update(task_status=task.status, task_error=task.error)
        if mode == "publish":
            assert task.status == "done", f"{task.status}: {task.error} {task.steps[0].result_summary}"
            assert len(backend.calls) == 14 and evidence["factory_bindings"] == 2
            assert (project / "sample.py").read_bytes() == AFTER.encode()
            assert (project / "test_sample.py").read_bytes() == test_bytes
            assert task.completion_report["ready_to_publish"] and task.completion_report["tests"] == "passed"
            book = json.loads((project / "book.ipynb").read_text(encoding="utf-8"))
            cell = book["cells"][0]
            assert cell["id"] == "cell-one" and "".join(cell["source"]) == "value = 2\n"
            assert cell["outputs"] == [] and cell["execution_count"] is None
            assert "trusted" not in cell["metadata"] and "signature" not in book["metadata"]
            assert any(c["name"] == "notebook_structure" and c["status"] == "passed" and c["execution"] == "not_run"
                       for c in task.completion_report["checks"])
            evidence["notebook"] = {"source_changed": True, "stable_ids": True, "outputs_cleared": True,
                                    "trust_cleared": True, "notebook_execution": "not_run"}
            # Exact crash boundary: publication journal is saved but task's final
            # completion save was lost. This is durable-state injection, not an
            # actual OS process-kill test, and never patches a private method.
            task.status, task.finished_at, task.error = "paused", None, "fault: completion save interrupted after publication"
            save(task)
            calls_before_recovery = len(backend.calls)

            def no_rebind(context):
                raise AssertionError("published recovery must not recreate execution capabilities")

            recovered = TaskRunner(config, project, backend, capability_factory=no_rebind).resume(task)
            assert recovered.status == "done" and not recovered.error
            assert len(backend.calls) == calls_before_recovery
            assert (project / "sample.py").read_bytes() == AFTER.encode()
            evidence["recovery"] = {"published_journal_unsaved_completion": "verified", "new_model_calls": 0,
                                    "recreated_capabilities": 0, "source_bytes_verified": True,
                                    "fault_method": "host durable-state injection; not process-kill coverage"}
        elif mode == "cancel":
            assert task.status == "failed" and task.error == "cancelled by user", task.error
            assert (project / "sample.py").read_bytes() == BEFORE.encode()
            assert (project / "book.ipynb").read_bytes() == notebook_bytes()
            assert ChangeSet.open(project, task.changeset_id).state["status"] == "aborted"
            assert (ChangeSet.open(project, task.changeset_id).workspace / "sample.py").read_bytes() == BEFORE.encode()
            evidence["source_preserved"] = True
        else:
            assert task.status == "failed" and "plan approval is stale; project source changed" in task.error, task.error
            assert (project / "sample.py").read_bytes() == EXTERNAL.encode()
            assert (project / "book.ipynb").read_bytes() == notebook_bytes()
            assert ChangeSet.open(project, task.changeset_id).state["status"] == "aborted"
            assert (ChangeSet.open(project, task.changeset_id).workspace / "sample.py").read_bytes() == BEFORE.encode()
            evidence["external_source_preserved"] = True
        evidence["changeset_status"] = ChangeSet.open(project, task.changeset_id).state["status"]
        evidence.update(status="passed", scripted_calls=len(backend.calls), budget_events=len(task.budget_events),
                        accounting_fixture_spend_usd=task.budget_spent,
                        published_sha256=hashlib.sha256(AFTER.encode()).hexdigest() if mode == "publish" else None,
                        completion=task.completion_report)
    return evidence


def run(args):
    result = {"schema_version": 2, "status": "running", "paid_calls": 0, "scenarios": [],
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    names = ("ATOMIC_FLOW_HOME", "ATOMIC_FLOW_EXECUTION_MODE")
    old_env = {name: os.environ.get(name) for name in names}
    try:
        modes = ("publish", "cancel", "source_conflict") if getattr(args, "scenario", "all") == "all" else (args.scenario,)
        for mode in modes:
            row = {"name": mode, "status": "running"}
            result["scenarios"].append(row)
            try:
                run_single(args, mode, row)
            except Exception as exc:
                row.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:2000])
                raise
        result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:2000])
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pylsp", type=Path, required=True)
    parser.add_argument("--pylsp-python", action="store_true", help="the supplied pylsp path is its Python interpreter")
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--mcp-server", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=("all", "publish", "cancel", "source_conflict"), default="all")
    args = parser.parse_args()
    for path in (args.pylsp, args.node, args.mcp_server):
        assert path.is_absolute() and path.is_file(), path
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
