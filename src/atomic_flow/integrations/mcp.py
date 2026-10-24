"""Bounded MCP 2025-06-18 stdio integration, independently implemented.

Subprocess servers are trusted host executables, not a sandbox. Repository/model
input cannot start them. Remote descriptions, annotations and content are data;
host grants, operation approval and ordinary dispatcher gates remain authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.execution import TaskCancelled, _kill_tree, clean_environment
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.tools.file_paths import admitted_path

PROTOCOL_VERSION = "2025-06-18"
MAX_FRAME_BYTES = 1024 * 1024
MAX_SOURCE_CHARS = 65536


class MCPError(RuntimeError):
    """Protocol failure without echoing untrusted server strings or credentials."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MCPError("duplicate JSON object key")
        result[key] = value
    return result


def _decode(raw):
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(MCPError("invalid number")))


def schema_hash(descriptor: dict) -> str:
    """Pin the remote operation identity and input schema, not its readOnly hint."""
    if re.fullmatch(r"[a-f0-9]{64}", descriptor.get("host_schema_hash", "")):
        return descriptor["host_schema_hash"]
    return hashlib.sha256(_json({"name": descriptor.get("name"), "inputSchema": descriptor.get("inputSchema")})).hexdigest()


def _remote_data(value):
    """Redact server strings without elevating any returned metadata to authority."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_remote_data(item) for item in value]
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.fullmatch(r"(?i)(api[_-]?key|secret|token|password|passwd)", key)
                      and isinstance(item, str) else _remote_data(item)) for key, item in value.items()}
    return value


class MCPClient:
    def __init__(self, project: Path, command: list[str], *, approved: bool = False,
                 allowed_tools: dict[str, str] | None = None, allowed_resources=(),
                 approval=None, timeout: float = 10):
        if approved is not True or not isinstance(command, list) or not command or not all(isinstance(v, str) and "\0" not in v for v in command):
            raise MCPError("explicit trusted host command approval required")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file() or not 0 < timeout <= 120:
            raise MCPError("absolute host executable and bounded timeout required")
        if len(_json(command)) > 32768:
            raise MCPError("command exceeds limit")
        self.project = Path(project).resolve()
        self.command = list(command)
        self.allowed_tools = dict(allowed_tools or {})
        self.allowed_resources = frozenset(allowed_resources)
        if len(self.allowed_tools) > 128 or len(self.allowed_resources) > 128:
            raise MCPError("grant count exceeds limit")
        if any(not isinstance(k, str) or not isinstance(v, str) or len(v) != 64 for k, v in self.allowed_tools.items()):
            raise MCPError("invalid host tool schema grant")
        self.approval = approval
        self.timeout = timeout
        self._lock = threading.RLock()
        self._proc = None
        self._owned_job = None
        self._cleanup_unknown = False
        self._temp = None
        self._frames = queue.Queue(maxsize=16)
        self._next_id = 0
        self._initialized = False

    def start(self, *, cancel_check=None):
        with self._lock:
            if self._initialized:
                return self
            if self._proc is not None:
                raise MCPError("closed or failed client cannot restart")
            self._temp = tempfile.TemporaryDirectory(prefix="atomic-mcp-")
            home = Path(self._temp.name)
            try:
                spawn = subprocess.Popen
                if os.name == "nt":
                    from atomic_flow.windows_job import WindowsJob

                    self._owned_job = WindowsJob()
                    spawn = self._owned_job.spawn
                self._proc = spawn(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, cwd=home, env=clean_environment(home),
                                              start_new_session=os.name != "nt")
                threading.Thread(target=self._reader, daemon=True).start()
                result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                       "clientInfo": {"name": "atomic-flow", "version": "1"}}, cancel_check)
                if result.get("protocolVersion") != PROTOCOL_VERSION or not isinstance(result.get("capabilities"), dict):
                    raise MCPError("unsupported protocol negotiation")
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                self._initialized = True
            except BaseException:
                self.close()
                raise
            return self

    def _reader(self):
        try:
            while self._proc is not None:
                raw = self._proc.stdout.readline(MAX_FRAME_BYTES + 1)
                if not raw or len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
                    raise MCPError("server closed or exceeded frame limit")
                frame = _decode(raw)
                if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                    raise MCPError("invalid JSON-RPC frame")
                self._frames.put(frame, timeout=0.2)
        except BaseException:
            try:
                self._frames.put(MCPError("server transport failed"), timeout=0.2)
            except queue.Full:
                if self._proc is not None:
                    self._proc.kill()

    def _send(self, frame, cancel_check=None):
        raw = _json(frame) + b"\n"
        if len(raw) > MAX_FRAME_BYTES:
            raise MCPError("request exceeds frame limit")
        done = threading.Event()
        failed = []

        def write():
            try:
                self._proc.stdin.write(raw)
                self._proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                failed.append(True)
            finally:
                done.set()

        threading.Thread(target=write, daemon=True).start()
        deadline = time.monotonic() + self.timeout
        try:
            while not done.wait(0.025):
                if cancel_check:
                    cancel_check()
                if time.monotonic() >= deadline:
                    raise MCPError("server transport deadline exceeded")
            if failed:
                raise MCPError("server transport failed")
        except BaseException:
            if self._proc is not None:
                self._proc.kill()
            raise

    def _request(self, method, params=None, cancel_check=None):
        with self._lock:
            if cancel_check:
                cancel_check()
            self._next_id += 1
            request_id = self._next_id
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, cancel_check)
            except BaseException:
                self.close()
                raise
            deadline = time.monotonic() + self.timeout
            notices = 0
            try:
                while time.monotonic() < deadline:
                    if cancel_check:
                        cancel_check()
                    try:
                        frame = self._frames.get(timeout=0.025)
                    except queue.Empty:
                        continue
                    if isinstance(frame, Exception):
                        raise frame
                    if "method" in frame:
                        notices += 1
                        if notices > 32:
                            raise MCPError("notification limit exceeded")
                        if "id" in frame:
                            self._send({"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32601, "message": "Client capability unavailable"}})
                        continue
                    if frame.get("id") != request_id or type(frame.get("id")) is not int:
                        raise MCPError("response request identity mismatch")
                    if "error" in frame or not isinstance(frame.get("result"), dict):
                        raise MCPError("remote operation failed")
                    if cancel_check:
                        cancel_check()
                    return frame["result"]
                raise MCPError("server response deadline exceeded")
            except BaseException:
                self.close()
                raise

    def list_tools(self, *, cancel_check=None):
        self.start(cancel_check=cancel_check)
        result = self._request("tools/list", cancel_check=cancel_check)
        items = result.get("tools")
        if (not isinstance(items, list) or len(items) > 128 or result.get("nextCursor")
                or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                       or not isinstance(item.get("inputSchema"), dict) for item in items)
                or len({item["name"] for item in items}) != len(items)):
            raise MCPError("invalid or paginated tool inventory; narrow server scope")
        # Raw schema hash is computed before redaction, overriding any server
        # field of this name. Changed secret defaults must still invalidate a grant.
        return [{**_remote_data(item), "host_schema_hash": hashlib.sha256(_json({"name": item["name"], "inputSchema": item["inputSchema"]})).hexdigest()}
                for item in items]

    def call_tool(self, name: str, arguments: dict, *, cancel_check=None):
        if name not in self.allowed_tools or not isinstance(arguments, dict):
            raise MCPError("tool is outside explicit host grant")
        # Refresh immediately before each effectful call, fail closed on drift.
        current = next((item for item in self.list_tools(cancel_check=cancel_check) if item["name"] == name), None)
        if current is None or schema_hash(current) != self.allowed_tools[name]:
            raise MCPError("remote tool schema changed; review host grant")
        params = {"name": name, "arguments": _decode(_json(arguments))}
        if not callable(self.approval) or self.approval("tools/call", _decode(_json(params))) is not True:
            raise MCPError("operation approval required")
        result = _remote_data(self._request("tools/call", params, cancel_check))
        # Returned metadata cannot mint evidence, instructions, roles or grants.
        returned = {"data_only": True, "remote_result": result, "schema_hash": self.allowed_tools[name],
                    "remote_verified": False, "verification": {}}
        if result.get("isError") is True:
            returned["error"] = "remote tool reported an operation error"
        return returned

    def read_resource(self, uri: str, *, cancel_check=None):
        if uri not in self.allowed_resources:
            raise MCPError("resource is outside explicit host grant")
        self.start(cancel_check=cancel_check)
        params = {"uri": uri}
        if not callable(self.approval) or self.approval("resources/read", dict(params)) is not True:
            raise MCPError("operation approval required")
        return {"data_only": True, "remote_result": _remote_data(self._request("resources/read", params, cancel_check)), "verification": {}}

    def close(self):
        with self._lock:
            self._initialized = False
            if self._owned_job is not None:
                try:
                    self._owned_job.terminate()
                except OSError:
                    self._cleanup_unknown = True
                finally:
                    self._owned_job.close()
                    self._owned_job = None
            if self._proc is not None:
                if os.name != "nt" and self._proc.poll() is None:
                    _kill_tree(self._proc)
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                for stream in (self._proc.stdin, self._proc.stdout):
                    if stream:
                        stream.close()
            if self._temp is not None:
                self._temp.cleanup()
                self._temp = None
            if self._cleanup_unknown:
                raise MCPError("Windows process-tree cleanup could not be confirmed; effects remain unknown")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def register_mcp_tool(registry, client: MCPClient, *, name: str, remote_name: str, args_schema,
                      description: str, contract: ToolContract | None = None):
    """Lazy host registration; caller must also explicitly add alias to role tools.

    Use execute by default: a server's readOnlyHint never lowers host gates.
    Custom host argument validation runs before the remote operation approval.
    """
    contract = contract or ToolContract(effects=("execute",), idempotent=False)
    if remote_name not in client.allowed_tools:
        raise MCPError("missing reviewed host grant")

    class RemoteTool(Tool):
        def run(self, args, ctx):
            if Path(ctx.project_root).resolve() != client.project:
                return {"error": "MCP client project binding mismatch"}
            try:
                return client.call_tool(remote_name, args.model_dump(mode="json"), cancel_check=ctx.cancel_check)
            except (MCPError, TaskCancelled):
                # Conservatively retain external-effect uncertainty; no auto replay.
                return {"error": "MCP operation failed or cancelled", "side_effects_may_have_occurred": True}

    RemoteTool.name, RemoteTool.description = name, description
    RemoteTool.args_schema, RemoteTool.contract = args_schema, contract
    registry.register_lazy(name, description, args_schema, contract, RemoteTool)


class SourceService:
    """Offline, source-only host capability shared by MCP server and IDE bridge."""
    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        if not self.project.is_dir():
            raise SourceConflict("project directory required")
        self.project_id = hashlib.sha256(str(self.project).encode("utf-8")).hexdigest()
        self.ctx = SimpleNamespace(project_root=self.project, private_workspace=False, tree_ignore=None, respect_gitignore=True)

    def tree(self):
        paths = []
        visited = 0
        truncated = False
        for folder, dirs, files in os.walk(self.project, followlinks=False):
            visited += 1
            if visited > 4096:
                truncated = True
                break
            dirs[:] = sorted(d for d in dirs if (not d.startswith(".") or d.casefold() == ".github") and not (Path(folder) / d).is_symlink()
                              and not getattr(Path(folder) / d, "is_junction", lambda: False)())
            for name in sorted(files):
                visited += 1
                if visited > 4096 or len(paths) >= 512:
                    truncated = True
                    break
                rel = (Path(folder) / name).relative_to(self.project).as_posix()
                try:
                    admitted_path(self.ctx, rel, existing=True)
                except OSError:
                    continue
                paths.append(rel)
            if truncated:
                break
        return {"project_id": self.project_id, "paths": paths, "truncated": truncated, "data_only": True}

    def read(self, path: str, offset=0, limit=MAX_SOURCE_CHARS):
        if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 8 * 1024 * 1024 or not 1 <= limit <= MAX_SOURCE_CHARS:
            raise SourceConflict("invalid read range")
        target = admitted_path(self.ctx, path, existing=True)
        with target.open("rb") as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise SourceConflict("source exceeds bound")
        text = redact(raw.decode("utf-8"))
        if offset > len(text):
            raise SourceConflict("read offset exceeds source")
        end = min(len(text), offset + limit)
        return {"project_id": self.project_id, "path": path, "content": text[offset:end], "content_hash": digest(raw),
                "offset": offset, "next_offset": end if end < len(text) else None, "data_only": True,
                "read_complete": offset == 0 and end == len(text), "representation": "redacted"}


def serve_mcp(project: Path, input_stream=None, output_stream=None):
    """Run a read-only stdio server; never invoke a model, command or write tool."""
    import sys
    source = SourceService(project)
    reader = input_stream or sys.stdin.buffer
    writer = output_stream or sys.stdout.buffer
    initialized = False
    ready = False
    schemas = [
        {"name": "source_tree", "description": "List bounded admitted source paths", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "source_read", "description": "Read bounded redacted source text", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SOURCE_CHARS}}, "required": ["path"], "additionalProperties": False}},
    ]
    while True:
        raw = reader.readline(MAX_FRAME_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
            return
        request_id = None
        try:
            frame = _decode(raw)
            if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0" or not isinstance(frame.get("method"), str):
                raise MCPError("invalid request")
            request_id = frame.get("id")
            if request_id is not None and (type(request_id) not in (int, str) or len(str(request_id)) > 128):
                request_id = None
                raise MCPError("invalid request identity")
            method, params = frame["method"], frame.get("params", {})
            if not isinstance(params, dict):
                raise MCPError("invalid parameters")
            if method == "notifications/initialized" and "id" not in frame:
                ready = initialized
                continue
            if "id" not in frame:
                continue
            if method == "initialize" and not initialized:
                if params.get("protocolVersion") != PROTOCOL_VERSION:
                    raise MCPError("unsupported protocol version")
                initialized = True
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}, "resources": {}},
                          "serverInfo": {"name": "atomic-flow-source", "version": "1"}}
            elif not ready:
                raise MCPError("initialization required")
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": schemas}
            elif method == "tools/call":
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise MCPError("invalid arguments")
                if params.get("name") == "source_tree" and not arguments:
                    value = source.tree()
                elif params.get("name") == "source_read" and set(arguments) <= {"path", "offset", "limit"}:
                    value = source.read(**arguments)
                else:
                    raise MCPError("operation unavailable")
                result = {"content": [{"type": "text", "text": _json(value).decode("utf-8")}], "isError": False}
            elif method == "resources/list":
                result = {"resources": [{"uri": "atomic://source/" + quote(path, safe="/"), "name": path, "mimeType": "text/plain"}
                                        for path in source.tree()["paths"]]}
            elif method == "resources/read" and isinstance(params.get("uri"), str) and params["uri"].startswith("atomic://source/"):
                value = source.read(unquote(params["uri"][len("atomic://source/"):]))
                result = {"contents": [{"uri": params["uri"], "mimeType": "text/plain", "text": _json(value).decode("utf-8")}]}
            else:
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method unavailable"}}
                writer.write(_json(response) + b"\n")
                writer.flush()
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (MCPError, OSError, UnicodeError, ValueError, TypeError):
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid request or unavailable source"}}
        encoded = _json(response) + b"\n"
        if len(encoded) > MAX_FRAME_BYTES:
            return
        writer.write(encoded)
        writer.flush()
