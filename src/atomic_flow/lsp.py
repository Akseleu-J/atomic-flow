"""Explicit trusted-host LSP 3.17 client; server output never authorizes edits.

Only UTF-16 positions, full document synchronization and existing-file textual
rename edits are supported. A server is trusted executable host code, not a
sandbox. No executable is discovered in repository configuration.
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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from atomic_flow.activation.redact import redact
from atomic_flow.agents import file_transaction
from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.source import SourceConflict, digest
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.config import user_dir

MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 512 * 1024
MAX_DOCUMENTS = 32
MAX_LOCATIONS = 256
MAX_EDITS = 512
MAX_RETAINED_BYTES = 8 * 1024 * 1024


class LSPError(RuntimeError):
    """Bounded protocol failure; server diagnostics/errors are not exception text."""


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LSPError("duplicate JSON-RPC object key")
        value[key] = item
    return value


def _transaction_storage(project: Path) -> Path:
    storage = (user_dir() / "lsp-transactions").absolute()
    if storage.resolve().is_relative_to(project):
        raise LSPError("LSP transaction state must be outside the source project")
    for parent in (storage, *storage.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise LSPError("LSP transaction state cannot use symlinks/junctions")
    return storage


def recover_lsp_edit(project: Path, edit_id: str, *, approved: bool = False) -> dict:
    """Explicit compare-before-restore of an interrupted local rename transaction.

    Foreign bytes are preserved. This restores source bytes, not any external
    effects of the trusted server process. Successful edits are not auto-undone.
    """
    if approved is not True or not isinstance(edit_id, str) or not re.fullmatch(r"fileedit_[a-f0-9]{12}", edit_id):
        raise LSPError("explicit host approval of valid LSP transaction recovery is required")
    project = Path(project).resolve()
    storage = _transaction_storage(project)
    folder = storage / edit_id
    try:
        state_file = folder / "file-transaction.json"
        if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)() or state_file.is_symlink():
            raise LSPError("linked LSP transaction is invalid")
        with state_file.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise LSPError("LSP transaction exceeds limit")
        state = json.loads(raw, object_pairs_hook=_unique_object)
        if state.get("project") != str(project) or state.get("status") not in {"writing", "verifying"}:
            raise LSPError("LSP transaction is not an interrupted edit for this project")
        changes = state.get("changes")
        if not isinstance(changes, dict) or not 1 <= len(changes) <= MAX_DOCUMENTS:
            raise LSPError("invalid LSP transaction path count")
        ctx = SimpleNamespace(project_root=project, private_workspace=False, tree_ignore=None, respect_gitignore=True)
        for rel, value in changes.items():
            admitted_path(ctx, rel)
            if not isinstance(value, dict) or not re.fullmatch(r"before-[0-9]+", value.get("backup", "")):
                raise LSPError("invalid LSP transaction backup")
            backup = folder / value["backup"]
            if backup.is_symlink() or (backup.exists() and backup.stat().st_size > MAX_DOCUMENT_BYTES):
                raise LSPError("invalid LSP transaction backup")
        result = file_transaction.undo(storage, project, edit_id, interrupted=True)
        if result is None:
            raise LSPError("LSP recovery conflict; foreign source bytes preserved")
        return {"status": "undone", "edit_id": edit_id, "paths": sorted(changes), "publication_performed": False}
    except (OSError, ValueError, TypeError, KeyError):
        raise LSPError("LSP transaction unavailable or invalid; source was not restored") from None


def _offset(text: str, position: dict) -> int:
    """Strict UTF-16 position conversion; reject half-surrogates and past-line offsets."""
    if not isinstance(position, dict) or set(position) != {"line", "character"}:
        raise LSPError("invalid UTF-16 position")
    line, units = position["line"], position["character"]
    if type(line) is not int or type(units) is not int or min(line, units) < 0:
        raise LSPError("invalid UTF-16 position")
    # LSP line endings are CRLF, CR or LF; Unicode paragraph separators
    # inside source strings do not begin a new protocol line.
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    if lines and lines[-1] == "" and len(lines) > 1 and not text.endswith(("\n", "\r")):
        lines.pop()
    if line >= len(lines):
        raise LSPError("position outside document")
    body = lines[line].rstrip("\r\n")
    consumed = 0
    for index, char in enumerate(body):
        if consumed == units:
            return sum(map(len, lines[:line])) + index
        consumed += 2 if ord(char) > 0xFFFF else 1
        if consumed > units:
            raise LSPError("position splits UTF-16 surrogate pair")
    if consumed == units:
        return sum(map(len, lines[:line])) + len(body)
    raise LSPError("position outside line")


def _range(text: str, value: dict) -> tuple[int, int]:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise LSPError("invalid edit range")
    start, end = _offset(text, value["start"]), _offset(text, value["end"])
    if start > end:
        raise LSPError("reversed edit range")
    return start, end


@dataclass(frozen=True)
class TextChange:
    path: str
    version: int
    before_hash: str
    after_hash: str
    before: bytes
    after: bytes


@dataclass(frozen=True)
class EditPreview:
    """Immutable, single-client preview. Bytes are host data, never model context."""

    digest: str
    changes: tuple[TextChange, ...]

    def summary(self) -> dict:
        return {"digest": self.digest, "changes": [
            {"path": c.path, "version": c.version, "before_hash": c.before_hash,
             "after_hash": c.after_hash, "before_bytes": len(c.before), "after_bytes": len(c.after)} for c in self.changes],
            "execution_performed": False, "approval_required": True}


class LSPClient:
    """One bounded host-owned server, one project, serialized explicit requests.

    ``approved=True`` authorizes exactly the supplied executable/argv. It does not
    authorize mutations, server requests, credentials, or arbitrary code actions.
    """

    def __init__(self, project: Path, command: list[str], *, approved: bool = False,
                 timeout: float = 10, cancel_check=None):
        if approved is not True:
            raise LSPError("explicit host approval of LSP command is required")
        if not isinstance(command, (list, tuple)) or not 1 <= len(command) <= 32 or any(
                not isinstance(arg, str) or not arg or len(arg) > 8192 or "\0" in arg for arg in command):
            raise LSPError("invalid explicit LSP command")
        # Absolute executable avoids cwd/PATH executable substitution. The host
        # must also trust any script/module argument it explicitly supplies.
        if not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
            raise LSPError("LSP executable must be an existing absolute host path")
        if not 0.05 <= timeout <= 120:
            raise LSPError("invalid LSP timeout")
        self.project = Path(project).resolve()
        if not self.project.is_dir():
            raise LSPError("LSP project must be a directory")
        self.command = tuple(command)
        self.timeout, self.cancel_check = timeout, cancel_check or (lambda: None)
        self._responses = queue.Queue(maxsize=16)
        self._writes = queue.Queue(maxsize=4)
        self._serial = threading.RLock()
        self._state_lock = threading.RLock()
        self._failure = threading.Event()
        self._documents: dict[str, dict] = {}
        self._diagnostics: dict[str, dict] = {}
        self._reads: dict[str, str] = {}
        self._previews: dict[str, EditPreview] = {}
        self._request_id = 0
        self._process = None
        self._home = None
        self._closed = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def operation(self, *, cancel_check=None):
        """Bind one host operation to its caller without leaking cancellation.

        Acquisition is interruptible even while another trusted host request
        owns this client. Nested calls reuse the request's serial RLock.
        """
        incoming = cancel_check or (lambda: None)
        while True:
            incoming()
            if self._serial.acquire(timeout=0.05):
                break
        original = self.cancel_check
        def combined():
            original()
            incoming()
        try:
            self.cancel_check = combined
            combined()
            yield self
        finally:
            self.cancel_check = original
            self._serial.release()

    def _path(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or "\0" in rel:
            raise LSPError("invalid project path")
        ctx = SimpleNamespace(project_root=self.project, private_workspace=False, tree_ignore=None, respect_gitignore=True)
        try:
            return admitted_path(ctx, rel)
        except (OSError, ValueError):
            raise LSPError("LSP path is private, ignored, or outside project") from None

    def _rel(self, uri: str) -> str:
        if not isinstance(uri, str) or len(uri) > 8192:
            raise LSPError("invalid document URI")
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise LSPError("only local project file URIs are supported")
        try:
            target = Path(url2pathname(parsed.path))
            rel = target.relative_to(self.project).as_posix()
        except ValueError:
            raise LSPError("document URI escapes project") from None
        # Canonical roundtrip catches encoded separators, aliases and traversal.
        admitted = self._path(rel)
        if admitted.as_uri() != uri:
            # URI spelling may differ in percent-escape case, but decoded path
            # must still be exactly the admitted non-resolved relative path.
            if unquote(admitted.as_uri()) != unquote(uri):
                raise LSPError("noncanonical document URI")
        return rel

    def _bytes(self, rel: str) -> bytes:
        path = self._path(rel)
        if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise LSPError("document missing or exceeds LSP source limit")
        with path.open("rb") as handle:
            data = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES or b"\0" in data:
            raise LSPError("document exceeds LSP source limit or is binary")
        try:
            data.decode("utf-8")
        except UnicodeError:
            raise LSPError("LSP document must be UTF-8") from None
        return data

    def start(self):
        with self._serial:
            if self._closed:
                raise LSPError("LSP client is closed")
            if self._process is not None:
                return self
            self.cancel_check()
            self._home = tempfile.TemporaryDirectory(prefix="atomic-lsp-")
            try:
                self._process = subprocess.Popen(list(self.command), cwd=self.project,
                    env=clean_environment(Path(self._home.name)), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
                threading.Thread(target=self._reader, daemon=True, name="atomic-lsp-reader").start()
                threading.Thread(target=self._writer, daemon=True, name="atomic-lsp-writer").start()
                result = self._request("initialize", {"processId": None, "rootUri": self.project.as_uri(),
                    "capabilities": {"general": {"positionEncodings": ["utf-16"]},
                        "workspace": {"applyEdit": False, "workspaceEdit": {"documentChanges": True, "failureHandling": "undo"}},
                        "textDocument": {"publishDiagnostics": {"versionSupport": True}}},
                    "workspaceFolders": [{"uri": self.project.as_uri(), "name": self.project.name}]})
                capabilities = result.get("capabilities", {}) if isinstance(result, dict) else {}
                if not isinstance(capabilities, dict) or capabilities.get("positionEncoding", "utf-16") != "utf-16":
                    raise LSPError("server did not negotiate UTF-16 positions")
                sync = capabilities.get("textDocumentSync", 0)
                sync_kind = sync.get("change", 0) if isinstance(sync, dict) else sync
                if sync_kind != 1 or (isinstance(sync, dict) and not sync.get("openClose")):
                    raise LSPError("server must support full open/change document synchronization")
                self._notify("initialized", {})
                return self
            except BaseException:
                self._terminate()
                raise

    def _send(self, message: dict):
        data = _json(message)
        if len(data) > MAX_FRAME_BYTES:
            raise LSPError("outgoing LSP frame exceeds limit")
        if self._failure.is_set() or self._closed:
            raise LSPError("LSP transport is unavailable")
        try:
            self._writes.put_nowait(b"Content-Length: " + str(len(data)).encode("ascii") + b"\r\n\r\n" + data)
        except queue.Full:
            self._failure.set()
            raise LSPError("LSP write capacity exceeded") from None

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _writer(self):
        try:
            while not self._failure.is_set():
                try:
                    frame = self._writes.get(timeout=0.1)
                except queue.Empty:
                    continue
                view = memoryview(frame)
                while view:
                    n = self._process.stdin.write(view)
                    if not n:
                        raise OSError
                    view = view[n:]
        except (OSError, ValueError):
            self._failure.set()

    def _reader(self):
        try:
            stream = self._process.stdout
            while not self._failure.is_set():
                header = bytearray()
                while not header.endswith(b"\r\n\r\n"):
                    char = stream.read(1)
                    if not char or len(header) >= 4096:
                        raise LSPError("invalid LSP header")
                    header.extend(char)
                headers = {}
                for line in bytes(header[:-4]).decode("ascii").split("\r\n"):
                    key, value = line.split(":", 1)
                    key = key.lower()
                    if key in headers:
                        raise LSPError("duplicate LSP header")
                    headers[key] = value.strip()
                length = int(headers["content-length"])
                if not 0 < length <= MAX_FRAME_BYTES:
                    raise LSPError("LSP frame exceeds limit")
                content_type = headers.get("content-type", "application/vscode-jsonrpc; charset=utf-8").lower()
                if "charset=" in content_type and content_type.split("charset=", 1)[1] not in {"utf8", "utf-8"}:
                    raise LSPError("unsupported LSP frame encoding")
                body = bytearray()
                while len(body) < length:
                    chunk = stream.read(length - len(body))
                    if not chunk:
                        raise LSPError("partial LSP frame")
                    body.extend(chunk)
                message = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise LSPError("invalid JSON-RPC message")
                if "method" in message:
                    if "id" in message:
                        # In particular workspace/applyEdit is never executed.
                        identity = message["id"]
                        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or len(str(identity)) > 128:
                            raise LSPError("invalid server request identity")
                        self._send({"jsonrpc": "2.0", "id": identity,
                                    "error": {"code": -32601, "message": "Host operation unavailable"}})
                    elif message["method"] == "textDocument/publishDiagnostics":
                        self._record_diagnostics(message.get("params"))
                    continue
                self._responses.put_nowait(message)
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError, LSPError, queue.Full):
            self._failure.set()

    def _request(self, method: str, params):
        self._request_id += 1
        identity = self._request_id
        self._send({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                self.cancel_check()
                if self._failure.is_set():
                    raise LSPError("LSP transport failed or frame limit exceeded")
                if time.monotonic() >= deadline:
                    raise LSPError("LSP request timed out")
                try:
                    response = self._responses.get(timeout=min(0.025, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if response.get("id") != identity or ("result" in response) == ("error" in response):
                    raise LSPError("unexpected LSP response")
                if "error" in response:
                    raise LSPError("LSP server refused request")
                return response["result"]
        except BaseException:
            try:
                self._notify("$/cancelRequest", {"id": identity})
            except LSPError:
                pass
            self._terminate()
            raise

    def _terminate(self):
        self._closed = True
        self._failure.set()
        proc = self._process
        if proc is not None:
            # The exact owned process is terminated; this is not a sandbox or a
            # guarantee about detached descendants created by trusted host code.
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()
        if self._home:
            self._home.cleanup()

    def close(self):
        with self._serial:
            if self._closed:
                return
            try:
                if self._process is not None:
                    self._request("shutdown", None)
                    self._notify("exit", None)
                    # Ensure exit notification is consumed before termination.
                    self._process.wait(timeout=min(self.timeout, 1))
            except (LSPError, OSError, subprocess.TimeoutExpired):
                pass
            finally:
                self._terminate()

    def _sync(self, rel: str) -> dict:
        self.start()
        data = self._bytes(rel)
        content_hash = digest(data)
        with self._state_lock:
            old = self._documents.get(rel)
            if old and old["content_hash"] == content_hash:
                return old.copy()
            if old is None and len(self._documents) >= MAX_DOCUMENTS:
                raise LSPError("LSP open document limit exceeded")
            version = old["version"] + 1 if old else 1
            if sum(len(value["text"].encode("utf-8")) for key, value in self._documents.items() if key != rel) + len(data) > MAX_RETAINED_BYTES:
                raise LSPError("LSP retained document byte limit exceeded")
            doc = {"path": rel, "content_hash": content_hash, "version": version, "text": data.decode("utf-8")}
            self._documents[rel] = doc
            self._diagnostics.pop(rel, None)
            self._reads.pop(rel, None)
            uri = self._path(rel).as_uri()
            if old:
                self._notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": version},
                                                        "contentChanges": [{"text": doc["text"]}]})
            else:
                language = "python" if rel.endswith((".py", ".pyi")) else "plaintext"
                self._notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": language,
                                                                       "version": version, "text": doc["text"]}})
            return doc.copy()

    def read_document(self, path: str) -> dict:
        """Explicit complete host read; preview generation alone gives no read proof."""
        with self._serial:
            doc = self._sync(path)
            self._reads[path] = doc["content_hash"]
            return doc

    def _position_params(self, path, line, character):
        doc = self._sync(path)
        position = {"line": line, "character": character}
        _offset(doc["text"], position)
        return doc, {"textDocument": {"uri": self._path(path).as_uri()}, "position": position}

    def _locations(self, value) -> list[dict]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        if len(values) > MAX_LOCATIONS:
            raise LSPError("LSP location count exceeds limit")
        result = []
        for item in values:
            if not isinstance(item, dict):
                raise LSPError("invalid LSP location")
            rel = self._rel(item.get("uri", item.get("targetUri")))
            data = self._bytes(rel)
            location_range = item.get("range", item.get("targetSelectionRange"))
            _range(data.decode("utf-8"), location_range)
            result.append({"path": rel, "range": location_range, "content_hash": digest(data), "trust": "server_data"})
        return result

    def definition(self, path: str, line: int, character: int) -> list[dict]:
        with self._serial:
            doc, params = self._position_params(path, line, character)
            result = self._locations(self._request("textDocument/definition", params))
            if digest(self._bytes(path)) != doc["content_hash"]:
                raise SourceConflict("source changed during LSP request")
            return result

    def references(self, path: str, line: int, character: int, *, include_declaration=True) -> list[dict]:
        with self._serial:
            doc, params = self._position_params(path, line, character)
            params["context"] = {"includeDeclaration": bool(include_declaration)}
            result = self._locations(self._request("textDocument/references", params))
            if digest(self._bytes(path)) != doc["content_hash"]:
                raise SourceConflict("source changed during LSP request")
            return result

    def _record_diagnostics(self, params):
        if not isinstance(params, dict):
            raise LSPError("invalid diagnostics notification")
        try:
            rel = self._rel(params.get("uri"))
        except LSPError:
            return  # Never fetch documents in response to server notifications.
        with self._state_lock:
            doc = self._documents.get(rel)
            if doc is None or type(params.get("version")) is not int or params["version"] != doc["version"]:
                return
            values = params.get("diagnostics")
            if not isinstance(values, list) or len(values) > MAX_LOCATIONS:
                raise LSPError("diagnostic count exceeds limit")
            diagnostics = []
            for item in values:
                if not isinstance(item, dict) or not isinstance(item.get("message"), str):
                    raise LSPError("invalid diagnostic")
                _range(doc["text"], item.get("range"))
                diagnostics.append({"range": item["range"], "message": redact(item["message"][:2048]),
                                    "severity": item.get("severity") if type(item.get("severity")) is int else None})
            self._diagnostics[rel] = {"path": rel, "version": doc["version"], "content_hash": doc["content_hash"],
                                      "diagnostics": diagnostics, "trust": "server_data"}

    def diagnostics(self, path: str) -> dict:
        """Latest versioned push only; no unversioned diagnostics are treated as current."""
        with self._serial:
            doc = self._sync(path)
            with self._state_lock:
                result = self._diagnostics.get(path)
                # A JSON roundtrip prevents callers mutating retained state.
                return json.loads(_json(result)) if result else {"path": path, "version": doc["version"],
                    "content_hash": doc["content_hash"], "diagnostics": [], "available": False, "trust": "server_data"}

    def preview_rename(self, path: str, line: int, character: int, new_name: str) -> EditPreview:
        with self._serial:
            if not isinstance(new_name, str) or not 1 <= len(new_name) <= 256 or any(c in new_name for c in "\r\n\0"):
                raise LSPError("invalid rename name")
            doc, params = self._position_params(path, line, character)
            # Only already synchronized files can receive a safe version-bound
            # edit. Hosts explicitly read other rename targets before requesting.
            snapshot = {rel: value.copy() for rel, value in self._documents.items()}
            params["newName"] = new_name
            edit = self._request("textDocument/rename", params)
            if digest(self._bytes(path)) != doc["content_hash"]:
                raise SourceConflict("source changed during rename request")
            return self._preview(edit, snapshot)

    def _preview(self, edit, snapshot) -> EditPreview:
        if not isinstance(edit, dict) or set(edit) - {"changes", "documentChanges"} or len(edit) != 1:
            raise LSPError("rename requires exactly one textual workspace edit form")
        entries = []
        if "changes" in edit:
            if not isinstance(edit["changes"], dict):
                raise LSPError("invalid workspace changes")
            entries = [(uri, None, changes) for uri, changes in edit["changes"].items()]
        else:
            if not isinstance(edit["documentChanges"], list):
                raise LSPError("invalid document changes")
            for item in edit["documentChanges"]:
                if not isinstance(item, dict) or set(item) != {"textDocument", "edits"}:
                    raise LSPError("resource operations and annotated edits are unsupported")
                identity = item["textDocument"]
                if not isinstance(identity, dict) or set(identity) != {"uri", "version"}:
                    raise LSPError("invalid versioned document edit")
                entries.append((identity["uri"], identity["version"], item["edits"]))
        if not 1 <= len(entries) <= MAX_DOCUMENTS:
            raise LSPError("rename path count exceeds limit")
        changes, seen, edit_count = [], set(), 0
        for uri, version, edits in entries:
            rel = self._rel(uri)
            if rel in seen:
                raise LSPError("duplicate rename target")
            seen.add(rel)
            old = snapshot.get(rel)
            if old is None:
                raise LSPError("read/synchronize all rename targets before requesting rename")
            if version is not None and (type(version) is not int or version != old["version"]):
                raise LSPError("stale versioned workspace edit")
            before = self._bytes(rel)
            if digest(before) != old["content_hash"]:
                raise SourceConflict("rename target changed during request")
            if not isinstance(edits, list) or not edits:
                raise LSPError("empty or invalid document edits")
            edit_count += len(edits)
            if edit_count > MAX_EDITS:
                raise LSPError("rename edit count exceeds limit")
            text = before.decode("utf-8")
            spans = []
            for item in edits:
                if not isinstance(item, dict) or set(item) != {"range", "newText"} or not isinstance(item["newText"], str):
                    raise LSPError("unsupported text edit")
                if len(item["newText"].encode("utf-8")) > MAX_DOCUMENT_BYTES or "\0" in item["newText"]:
                    raise LSPError("replacement text exceeds limit")
                start, end = _range(text, item["range"])
                spans.append((start, end, item["newText"]))
            spans.sort()
            for first, second in zip(spans, spans[1:], strict=False):
                # Same-position insert ordering is legal LSP but intentionally
                # unsupported here: previews require unambiguous distinct spans.
                if first[1] > second[0] or first[0] == second[0]:
                    raise LSPError("overlapping or duplicate text edits")
            for start, end, replacement in reversed(spans):
                text = text[:start] + replacement + text[end:]
            after = text.encode("utf-8")
            if len(after) > MAX_DOCUMENT_BYTES:
                raise LSPError("edited document exceeds limit")
            changes.append(TextChange(rel, old["version"], digest(before), digest(after), before, after))
        changes.sort(key=lambda change: change.path)
        fingerprint = hashlib.sha256(_json({"project": str(self.project), "changes": [
            {"path": c.path, "version": c.version, "before_hash": c.before_hash, "after_hash": c.after_hash}
            for c in changes]})).hexdigest()
        preview = EditPreview(fingerprint, tuple(changes))
        retained = sum(len(c.before) + len(c.after) for p in self._previews.values() for c in p.changes)
        if len(self._previews) >= 8 or retained + sum(len(c.before) + len(c.after) for c in changes) > MAX_RETAINED_BYTES:
            raise LSPError("pending rename preview limit exceeded; create a new client")
        self._previews[preview.digest] = preview
        return preview

    def apply_preview(self, preview: EditPreview, *, approved: bool = False,
                      expected_digest: str | None = None, context=None) -> dict:
        """Explicit recoverable multi-path apply, current complete read required.

        The host supplies either complete reads through this client, or a
        ToolContext with current full source coverage and authorized write scope.
        No server-supplied approval, command, or action is interpreted.
        """
        with self._serial:
            if approved is not True or expected_digest != preview.digest:
                raise LSPError("explicit approval of exact rename preview digest is required")
            if self._previews.get(preview.digest) is not preview:
                raise LSPError("rename preview does not belong to this client")
            before, after = {}, {}
            for change in preview.changes:
                current = self._documents.get(change.path)
                if not current or current["version"] != change.version or digest(self._bytes(change.path)) != change.before_hash:
                    raise SourceConflict("rename preview is stale")
                if context is not None:
                    if Path(context.project_root).resolve() != self.project or not context.edits_enabled:
                        raise LSPError("tool context cannot authorize this project edit")
                    if context.mutation_scope is not None and change.path not in context.mutation_scope:
                        raise LSPError("rename exceeds approved mutation scope")
                    if not context.read_coverage(change.path, change.before_hash)["read_complete"]:
                        raise LSPError("complete fresh source read required before rename")
                elif self._reads.get(change.path) != change.before_hash:
                    raise LSPError("complete fresh host read required before rename")
                if change.path.endswith((".py", ".pyi")):
                    try:
                        compile(change.after, change.path, "exec", dont_inherit=True)
                    except (SyntaxError, ValueError):
                        raise LSPError("rename replacement Python does not compile") from None
                before[change.path], after[change.path] = change.before, change.after
            storage = _transaction_storage(self.project)
            self.cancel_check()
            identity, state = file_transaction.begin(storage, self.project, before, after)
            def cancel():
                self.cancel_check()
                if context is not None:
                    context.cancel_check()
            try:
                file_transaction.apply(storage, self.project, identity, state, after, cancel)
                for change in preview.changes:
                    if digest(self._bytes(change.path)) != change.after_hash:
                        raise SourceConflict("rename result changed before refresh")
                if context is not None:
                    context.refresh()
                for change in preview.changes:
                    if digest(self._bytes(change.path)) != change.after_hash:
                        raise SourceConflict("rename result changed during refresh")
                file_transaction.finish(storage, identity, state)
            except BaseException as exc:
                restored = file_transaction.undo(storage, self.project, identity, interrupted=True)
                if context is not None and restored is not None:
                    context.refresh()
                if restored is None:
                    raise LSPError(f"rename rollback failed; inspect transaction {identity}; side effects may have occurred") from None
                raise LSPError(f"rename failed and source restored; transaction {identity}") from exc
            del self._previews[preview.digest]
            warning = None
            for change in preview.changes:
                self._reads.pop(change.path, None)
                with self._state_lock:
                    self._diagnostics.pop(change.path, None)
                try:
                    self._sync(change.path)
                except (LSPError, OSError):
                    # The durable local transaction already succeeded. A dead
                    # analyzer must not turn its outcome into an ambiguous edit.
                    warning = "source applied; LSP synchronization unavailable; restart client"
                    self._terminate()
                    break
            return {"status": "applied", "edit_id": identity, "preview_digest": preview.digest,
                    "paths": [change.path for change in preview.changes], "recoverable": True,
                    "publication_performed": False, "warning": warning}
