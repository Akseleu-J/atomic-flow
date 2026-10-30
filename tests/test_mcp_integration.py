import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from atomic_flow.agents.base import ToolRegistry
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.integrations.mcp import MCPClient, MCPError, SourceService, register_mcp_tool, schema_hash, serve_mcp

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"


def client(root, **kwargs):
    return MCPClient(root, [sys.executable, str(FIXTURE.resolve()), str(root)], approved=True, **kwargs)


def test_actual_stdio_server_tools_resources_and_clean_environment(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "demo.py").write_bytes(b"answer = 42\n")
    capture = tmp_path / "child-environment.json"
    monkeypatch.setenv("MCP_TEST_SECRET", "never-forward-me")
    monkeypatch.setenv("GITHUB_TOKEN", "never-forward-token")
    calls = []
    with MCPClient(project, [sys.executable, str(FIXTURE.resolve()), str(project), str(capture)], approved=True,
                   allowed_resources=("atomic://source/demo.py",), approval=lambda method, params: calls.append((method, params)) or True) as remote:
        tools = remote.list_tools()
        assert {tool["name"] for tool in tools} == {
            "source_tree", "source_read", "graph_inspect", "graph_search",
            "graph_explain", "graph_chat", "graph_impact", "graph_tour",
        }
        remote.allowed_tools["source_read"] = schema_hash(next(tool for tool in tools if tool["name"] == "source_read"))
        result = remote.call_tool("source_read", {"path": "demo.py"})
        assert result["data_only"] and not result["remote_verified"] and result["verification"] == {}
        data = json.loads(result["remote_result"]["content"][0]["text"])
        assert data["content"] == "answer = 42\n"
        assert remote.read_resource("atomic://source/demo.py")["data_only"]
        assert [call[0] for call in calls] == ["tools/call", "resources/read"]
    assert set(json.loads(capture.read_text()).values()) == {None}


def test_missing_command_approval_and_relative_executable_never_start(tmp_path):
    with pytest.raises(MCPError, match="approval"):
        MCPClient(tmp_path, [sys.executable])
    with pytest.raises(MCPError, match="absolute"):
        MCPClient(tmp_path, ["python"], approved=True)


def test_remote_schema_drift_and_operation_approval_fail_closed(tmp_path):
    with client(tmp_path, allowed_tools={"source_tree": "0" * 64}, approval=lambda *_: True) as remote:
        with pytest.raises(MCPError, match="schema changed"):
            remote.call_tool("source_tree", {})
        tool = remote.list_tools()[0]
        remote.allowed_tools[tool["name"]] = schema_hash(tool)
        remote.approval = lambda *_: False
        with pytest.raises(MCPError, match="approval"):
            remote.call_tool(tool["name"], {})
        with pytest.raises(MCPError, match="grant"):
            remote.call_tool("run_shell", {})
        with pytest.raises(MCPError, match="grant"):
            remote.read_resource("file:///etc/passwd")


def test_source_service_rejects_private_traversal_outputs_and_symlinks(tmp_path):
    (tmp_path / "safe.py").write_text("ok = True\n")
    (tmp_path / ".env").write_text("SECRET=value")
    (tmp_path / "notebook.ipynb").write_text('{"outputs":["private"]}')
    source = SourceService(tmp_path)
    assert source.tree()["paths"] == ["safe.py"]
    for path in ("../safe.py", ".env", "notebook.ipynb", "safe.py:other", "credentials.py"):
        with pytest.raises(OSError):
            source.read(path)
    first = source.read("safe.py", limit=2)
    assert first["content"] == "ok" and first["next_offset"] == 2 and not first["read_complete"]
    with pytest.raises(OSError):
        source.read("safe.py", offset=100)


@pytest.mark.parametrize("mode", ["hang", "oversize"])
def test_bad_or_nonresponding_stdio_server_is_bounded(tmp_path, mode):
    remote = MCPClient(tmp_path, [sys.executable, str(FIXTURE.resolve()), str(tmp_path), mode], approved=True, timeout=2)
    with pytest.raises(MCPError):
        with remote:
            remote.list_tools()
    assert remote._proc.poll() is not None


@pytest.mark.parametrize("mode", ["hang", "hang-init"])
def test_cancellation_closes_owned_server_without_replay(tmp_path, mode):
    remote = MCPClient(tmp_path, [sys.executable, str(FIXTURE.resolve()), str(tmp_path), mode], approved=True, timeout=5)
    if mode == "hang":
        remote.start()
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()

    def cancel():
        if stop.is_set():
            raise TaskCancelled("cancelled")

    try:
        with pytest.raises(TaskCancelled):
            remote.list_tools(cancel_check=cancel)
        assert remote._proc.poll() is not None
    finally:
        timer.cancel()
        remote.close()


def test_lazy_registration_does_not_start_server_and_preserves_host_contract(tmp_path):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    remote = client(tmp_path, allowed_tools={"source_tree": "0" * 64})
    registry = ToolRegistry()
    register_mcp_tool(registry, remote, name="reviewed_source", remote_name="source_tree", args_schema=Args, description="Host reviewed operation")
    assert remote._proc is None
    assert registry.schemas(["reviewed_source"]) == []
    assert registry.specification("reviewed_source").contract.effects == ("execute",)
    tool = registry.get("reviewed_source")
    result = tool.run(Args(), SimpleNamespace(project_root=tmp_path / "other", cancel_check=lambda: None))
    assert "binding mismatch" in result["error"] and remote._proc is None


def test_server_requires_handshake_and_never_exposes_commands(tmp_path):
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_command", "arguments": {"command": "whoami"}}}]
    output = io.BytesIO()
    serve_mcp(tmp_path, io.BytesIO(b"".join(json.dumps(value).encode() + b"\n" for value in requests)), output)
    assert "error" in json.loads(output.getvalue())


def test_server_redaction_cannot_hide_schema_drift_and_errors_stay_errors(tmp_path, monkeypatch):
    secret = "ghp_" + "A" * 36
    descriptor = {"name": "inspect", "description": secret, "inputSchema": {"type": "object", "properties": {"key": {"type": "string", "default": secret}}}}
    remote = client(tmp_path, approval=lambda *_: True)
    monkeypatch.setattr(remote, "start", lambda **_: remote)

    def response(method, *args, **kwargs):
        if method == "tools/list":
            return {"tools": [descriptor]}
        return {"content": [{"type": "text", "text": secret}], "isError": True, "token": "arbitrary-private-value"}

    monkeypatch.setattr(remote, "_request", response)
    reviewed = remote.list_tools()[0]
    assert secret not in json.dumps(reviewed)
    remote.allowed_tools["inspect"] = schema_hash(reviewed)
    result = remote.call_tool("inspect", {})
    assert result["error"] and secret not in json.dumps(result) and "arbitrary-private-value" not in json.dumps(result)
    descriptor["inputSchema"]["properties"]["key"]["default"] = "ghp_" + "B" * 36
    with pytest.raises(MCPError, match="schema changed"):
        remote.call_tool("inspect", {})


def test_final_receive_boundary_observes_cancellation(tmp_path, monkeypatch):
    remote = client(tmp_path)
    checks = []

    def send(frame, cancel_check=None):
        remote._frames.put({"jsonrpc": "2.0", "id": frame["id"], "result": {}})

    def cancel():
        checks.append(True)
        if len(checks) == 3:
            raise TaskCancelled("cancelled at receive boundary")

    monkeypatch.setattr(remote, "_send", send)
    with pytest.raises(TaskCancelled):
        remote._request("test", cancel_check=cancel)
