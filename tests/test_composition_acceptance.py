"""Exercise the host journey fixtures; real protocol coverage is a separate CI job."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "composition_acceptance_tool", Path(__file__).resolve().parents[1] / "tools" / "integration_acceptance.py")
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


@pytest.mark.parametrize("mode", ["publish", "cancel", "source_conflict"])
def test_public_review_edit_check_and_fault_journeys(tmp_path, monkeypatch, mode):
    # Only public dependency constructors are replaced. TaskRunner, notebook
    # operations, dispatcher, ledger, checks and publication remain real. These
    # fakes cannot establish external LSP/MCP interoperability.
    clients = []

    class LSP:
        def __init__(self, root, command, **kwargs):
            assert kwargs["approved"] and kwargs["private_workspace"]
            self.root, self.revisions, self.closed = root, {}, False
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def read_document(self, path):
            text = (self.root / path).read_text(encoding="utf-8")
            old, version = self.revisions.get(path, (None, 0))
            version += old != text
            self.revisions[path] = text, version
            return {"text": text, "version": version, "content_hash": hashlib.sha1(text.encode()).hexdigest()}

        def definition(self, path, line, column):
            return [{"path": path}]

    class MCP:
        def __init__(self, root, command, **kwargs):
            assert kwargs["approved"]
            self.allowed_tools, self.closed = {}, False
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def list_tools(self):
            return [{"name": "read_text_file", "inputSchema": {"type": "object"}}]

        def call_tool(self, name, params):
            assert name in self.allowed_tools
            return {"remote_result": {"content": [{"text": Path(params["path"]).read_text(encoding="utf-8")}]}}

    monkeypatch.setattr(acceptance, "LSPClient", LSP)
    monkeypatch.setattr(acceptance, "MCPClient", MCP)
    args = SimpleNamespace(pylsp=tmp_path / "host-pylsp", pylsp_python=False,
                           node=tmp_path / "host-node", mcp_server=tmp_path / "host-mcp.js")
    result = acceptance.run_single(args, mode)
    assert result["status"] == "passed"
    assert len(result["observations"]) == 2
    assert clients and all(client.closed for client in clients)
    if mode == "publish":
        assert result["completion"]["tests"] == "passed"
        assert result["recovery"]["new_model_calls"] == 0
    else:
        assert result["changeset_status"] == "aborted"
