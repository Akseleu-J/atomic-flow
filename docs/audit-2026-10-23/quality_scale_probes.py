"""Offline audit probes. No model/network/host-state calls; temporary project only."""
import inspect
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atomic_flow.api.server import ChatRequest, create_app
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import save_tree
from fastapi.testclient import TestClient

report = {"model_calls": 0, "network_calls": 0, "probe_kind": "deterministic offline fault interleaving"}
request = ChatRequest(messages=[{"role": "user", "content": "x" * 2_000_000}])
report["chat_schema_accepted_content_chars"] = len(request.messages[0]["content"])
with patch.dict(os.environ, {"ATOMIC_FLOW_SERVER_API_KEY": "", "ATOMIC_FLOW_REQUIRE_API_KEY": "false"}):
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        report["oversized_chat_status"] = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x" * 50_001}]}).status_code
        report["oversized_answer_status"] = client.post("/v1/answer", json={"query": "x" * 50_001}).status_code
        report["malformed_content_part_status"] = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": [{"type": "text", "text": 123}]}]}).status_code
    with TestClient(create_app(), raise_server_exceptions=True) as client:
        try:
            client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x" * 50_001}]})
        except Exception as exc:
            report["oversized_chat_exception"] = type(exc).__name__
            if hasattr(exc, "errors"):
                report["oversized_chat_errors"] = [{"type": e["type"], "loc": e["loc"]} for e in exc.errors()]

def probe_tree_race():
    root = ROOT / "docs/audit-2026-10-23/quality-probe-workspace"
    first_written = threading.Event()
    both_written = threading.Event()
    original_write = Path.write_text
    first_replaced = threading.Event()
    original = Path.replace
    results = []
    def synchronized_write(path, data, *args, **kwargs):
        if path.name != "tree.json.tmp":
            return original_write(path, data, *args, **kwargs)
        if threading.current_thread().name == "writer-0":
            result = original_write(path, data, *args, **kwargs)
            first_written.set()
            both_written.wait(timeout=10)
            return result
        first_written.wait(timeout=10)
        result = original_write(path, data, *args, **kwargs)
        both_written.set()
        return result
    def synchronized_replace(path, target):
        if path.name == "tree.json.tmp":
            if threading.current_thread().name == "writer-0":
                result = original(path, target)
                first_replaced.set()
                return result
            first_replaced.wait(timeout=10)
        return original(path, target)
    def writer(value):
        try:
            save_tree(root, {"writer": value})
            results.append({"writer": value, "result": "saved"})
        except Exception as exc:
            results.append({"writer": value, "result": type(exc).__name__})
    with patch.object(Path, "write_text", synchronized_write), patch.object(Path, "replace", synchronized_replace):
        threads = [threading.Thread(target=writer, args=(i,), name=f"writer-{i}") for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
    report["concurrent_tree_saves"] = sorted(results, key=lambda row: row["writer"])
    report["saved_tree"] = json.loads((root / ".atomic_flow" / "tree.json").read_text(encoding="utf-8")) if (root / ".atomic_flow" / "tree.json").exists() else None

probe_tree_race()

report["task_runner_parameters"] = list(inspect.signature(TaskRunner).parameters)
report["orchestrator_host_capabilities_missing_from_runner"] = sorted(set(inspect.signature(Orchestrator).parameters) & {"workspace_session", "lsp_client", "documentation_client", "hooks", "tool_registry", "role_tools"} - set(inspect.signature(TaskRunner).parameters))
print(json.dumps(report, indent=2))
