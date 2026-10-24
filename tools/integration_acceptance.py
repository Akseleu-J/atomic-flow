"""Real pinned LSP/MCP processes in a staged TaskRunner; model replies are scripted.

Only host argv select executables. Run after explicitly installing acceptance
dependencies; this script never downloads tools or calls an LLM service.
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
from atomic_flow.agents.planner import Plan, PlanStep
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


class ScriptedBackend:
    name = "acceptance-scripted-no-network"

    def __init__(self):
        self.config = BackendConfig(model="scripted", price_in_per_mtok=1, price_out_per_mtok=1)
        self.calls = []
        self.replies = iter([
            {"tool": "external_probe", "args": {"phase": "before"}},
            {"tool": "read_file", "args": {"path": "sample.py"}},
            {"tool": "patch_file", "args": {"path": "sample.py", "expected_hash": hashlib.sha1(BEFORE.encode()).hexdigest(),
                                                "old_text": "return 1", "new_text": "return 2", "reason": "acceptance change"}},
            {"tool": "external_probe", "args": {"phase": "after"}},
            {"text": "Completed the reviewed source change."},
        ])

    def chat(self, messages, **kwargs):
        match = re.search(r"<role>(\w+)</role>", messages[1]["content"])
        role = match.group(1) if match else "unknown"
        self.calls.append(role)
        if role == "parser":
            value = {}
        elif role == "critic":
            value = {"verdict": "ok", "citation_coverage": 1}
        elif role == "answerer":
            value = next(self.replies)
        else:
            raise AssertionError(f"unexpected scripted role {role}")
        return ChatResult(json.dumps(value), 100, 50, 0.001, 1)


def run(args):
    evidence = {"schema_version": 1, "status": "running", "external_services": "real local processes",
                "model": "scripted; no model quality claim", "paid_calls": 0, "observations": []}
    with tempfile.TemporaryDirectory(prefix="atomic-external-acceptance-") as tmp, ExitStack() as clients:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        (project / "sample.py").write_bytes(BEFORE.encode("utf-8"))
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
                return {"phase": params.phase, "data_only": True, "definition_count": len(definitions)}

        def factory(context):
            nonlocal lsp, mcp
            assert context.workspace != context.project
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
                                    role_tools={"answerer": ["external_probe", "read_file", "patch_file"]})

        lsp = mcp = None
        config = AtomicConfig()
        config.task.max_replans = 0
        runner = TaskRunner(config, project, backend, approval_callback=lambda *a: True, capability_factory=factory)
        task = runner.run_plan("Change sample return to two and verify through external source tools",
                               Plan(steps=[PlanStep(description="Change sample return value through reviewed patch", tools_needed=["patch_file"],
                                                         verify_with="py_compile")]), allowed_paths=["sample.py"])
        assert task.status == "done", f"{task.status}: {task.error} {task.steps[0].result_summary}"
        assert (project / "sample.py").read_text(encoding="utf-8") == AFTER
        assert len(evidence["observations"]) == 2
        assert evidence["observations"][1]["version"] > evidence["observations"][0]["version"]
        assert abs(SpendLedger(10).today_total() - task.budget_spent) < 1e-8
        assert task.completion_report["ready_to_publish"]
        evidence.update(status="passed", scripted_calls=len(backend.calls), budget_events=len(task.budget_events),
                        accounting_fixture_spend_usd=task.budget_spent, published_sha256=hashlib.sha256(AFTER.encode()).hexdigest(),
                        completion=task.completion_report)
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pylsp", type=Path, required=True)
    parser.add_argument("--pylsp-python", action="store_true", help="the supplied pylsp path is its Python interpreter")
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--mcp-server", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.pylsp, args.node, args.mcp_server):
        assert path.is_absolute() and path.is_file(), path
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
