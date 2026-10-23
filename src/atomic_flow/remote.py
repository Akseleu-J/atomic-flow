"""Versioned, hash-bound Docker worker protocol; workers are trusted hosts.

Hashes detect mismatched bundles/results, not a dishonest worker or hardware
attestation. No automatic submit retry, source publication, or model calls.
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Protocol
from urllib import error, request
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.source import confined_path
from atomic_flow.agents.tools.file_paths import admitted_path, text_bytes
from atomic_flow.config import ExecutionConfig, user_dir
from atomic_flow.locking import exclusive_file
from atomic_flow.tree.defaults import DEFAULT_IGNORE, SOURCE_SUFFIXES

MAX_WIRE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "interrupted", "output_limit"})


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("invalid SHA-256")
    return value


def _image(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("worker image must be an immutable sha256 image ID")
    return value


def _source_path(rel: str):
    path = PurePosixPath(rel)
    if (not rel or path.is_absolute() or rel != path.as_posix() or "\\" in rel or ":" in rel
            or any(ord(char) < 32 for char in rel) or ".." in path.parts):
        raise ValueError("invalid bundle source path")
    for part in path.parts:
        if ((part.startswith(".") and part != ".github") or part.endswith((".", " "))
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                or any(fnmatch.fnmatch(part.casefold(), pattern.casefold()) for pattern in [*DEFAULT_IGNORE,
                       "credentials*", "secrets*", "*private_key*", "*.pem", "*.key", "*.enc", "*.p12", "*.pfx"])):
            raise ValueError("private or excluded bundle source path")
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        raise ValueError("unsupported bundle source type")


class BundleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=512)
    sha256: str
    content: str = Field(max_length=1_400_000)


class SourceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    request_id: str
    project_hash: str
    image: str
    profile: str = "checks"
    args: list[str] = Field(min_length=1, max_length=64)
    timeout: float = Field(default=120, gt=0, le=600)
    output_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)
    files: list[BundleFile] = Field(min_length=1, max_length=64)

    def checked_files(self) -> dict[str, bytes]:
        if self.version != 1 or not re.fullmatch(r"remote_[a-f0-9]{24}", self.request_id):
            raise ValueError("unsupported bundle version or invalid request ID")
        _hash(self.project_hash)
        _image(self.image)
        if self.profile not in {"checks", "read-only"}:
            raise ValueError("unsupported remote sandbox profile")
        if any(not isinstance(arg, str) or "\0" in arg or len(arg.encode()) > 8192 for arg in self.args):
            raise ValueError("invalid Python argument")
        result = {}
        for item in self.files:
            # Validate without consulting the worker's unrelated current directory.
            _source_path(item.path)
            if item.path.casefold() in {name.casefold() for name in result}:
                raise ValueError("duplicate or case-ambiguous bundle path")
            raw = base64.b64decode(item.content, validate=True)
            text_bytes(raw.decode("utf-8"))
            if sha256(raw) != _hash(item.sha256):
                raise ValueError("bundle source hash mismatch")
            result[item.path] = raw
        folded = {path.casefold() for path in result}
        if any(parent.as_posix().casefold() in folded for path in result for parent in PurePosixPath(path).parents if parent != PurePosixPath(".")):
            raise ValueError("bundle file/directory collision")
        if sum(map(len, result.values())) > MAX_SOURCE_BYTES or len(canonical(self.model_dump())) > MAX_WIRE_BYTES:
            raise ValueError("source bundle exceeds byte limit")
        return result

    @property
    def digest(self) -> str:
        return sha256(canonical(self.model_dump()))

    @property
    def source_hash(self) -> str:
        return sha256(canonical({item.path: item.sha256 for item in self.files}))


def build_bundle(project: Path, paths: list[str], args: list[str], *, image: str, profile="checks",
                 timeout=120, output_bytes=1_048_576) -> SourceBundle:
    """Explicit source selection only. Never collect credentials or application state."""
    project = Path(project).resolve()
    if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
        raise ValueError("select between 1 and 64 source files")
    ctx = SimpleNamespace(project_root=project, private_workspace=False, tree_ignore=[], respect_gitignore=True)
    files = []
    for rel in sorted(paths):
        raw = admitted_path(ctx, rel, existing=True).read_bytes()
        files.append(BundleFile(path=rel, sha256=sha256(raw), content=base64.b64encode(raw).decode("ascii")))
    bundle = SourceBundle(request_id="remote_" + uuid.uuid4().hex[:24], project_hash=sha256(str(project).encode()),
                          image=image, profile=profile, args=args, timeout=timeout, output_bytes=output_bytes, files=files)
    bundle.checked_files()
    # Detect a source change during bundle construction before sending anything.
    if any(sha256(admitted_path(ctx, item.path, existing=True).read_bytes()) != item.sha256 for item in files):
        raise ValueError("source changed while preparing remote bundle")
    return bundle


class WorkerTransport(Protocol):
    def call(self, action: str, payload: dict) -> dict: ...


class RemoteOutcomeUnknown(RuntimeError):
    """A request may have reached the worker. Reconcile by request ID; never replay."""


@dataclass(repr=False)
class HTTPWorkerTransport:
    url: str
    token: str = field(repr=False)
    allow_loopback_http: bool = False
    timeout: float = 30

    def __post_init__(self):
        parsed = urlsplit(self.url)
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback and self.allow_loopback_http)) or (
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("worker URL requires HTTPS; explicit loopback HTTP is for local validation")
        if len(self.token) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in self.token):
            raise ValueError("worker token must contain at least 32 printable non-space characters")
        if not 0 < self.timeout <= 120:
            raise ValueError("invalid worker transport timeout")
        self.url = self.url.rstrip("/")

    def call(self, action: str, payload: dict) -> dict:
        if action not in {"submit", "lookup", "status", "cancel", "output"}:
            raise ValueError("unsupported worker operation")
        raw = canonical(payload)
        if len(raw) > MAX_WIRE_BYTES:
            raise ValueError("worker request exceeds byte limit")
        req = request.Request(self.url + "/v1/" + action, data=raw,
                              headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        # Disallow redirects: a worker cannot redirect its credential to another host.
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=self.timeout) as response:
                raw = response.read(MAX_WIRE_BYTES + 1)
        except error.HTTPError as exc:
            if exc.code in {401, 403, 404, 413, 429} or (exc.code == 400 and action != "submit"):
                raise ValueError(f"worker refused request (HTTP {exc.code})") from None
            raise RemoteOutcomeUnknown("worker response uncertain; reconcile request ID before any new submit") from None
        except (OSError, error.URLError):
            raise RemoteOutcomeUnknown("worker response uncertain; reconcile request ID before any new submit") from None
        if len(raw) > MAX_WIRE_BYTES:
            raise RemoteOutcomeUnknown("oversized worker response; do not replay")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise RemoteOutcomeUnknown("invalid worker response; do not replay") from None


@dataclass
class ScopedWorkerTransport:
    """Resolve a process credential only after each broker operation gate."""
    project: Path
    url: str
    broker: object
    grant_id: str
    allow_loopback_http: bool = False

    def call(self, action: str, payload: dict) -> dict:
        def perform(value):
            try:
                return {"outcome": "received", "data": HTTPWorkerTransport(self.url, value.reveal(), self.allow_loopback_http).call(action, payload)}
            except RemoteOutcomeUnknown:
                return {"outcome": "unknown"}
            except ValueError:
                return {"outcome": "refused"}
        envelope = self.broker.use(self.grant_id, project=self.project, connector="remote-worker", operation=action, consumer=perform)
        if envelope["outcome"] == "unknown":
            raise RemoteOutcomeUnknown("worker response uncertain; reconcile request ID before any new submit")
        if envelope["outcome"] == "refused":
            raise ValueError("worker operation refused or transport configuration invalid")
        return envelope["data"]


class RemoteExecutor:
    """Transport is swappable; a receipt binds every observation to one request."""
    def __init__(self, transport: WorkerTransport):
        self.transport = transport

    @staticmethod
    def _check_receipt(receipt):
        if not isinstance(receipt, dict) or set(receipt) != {"version", "request_id", "bundle_hash", "source_hash", "image", "job_id"}:
            raise ValueError("invalid worker receipt")
        if receipt["version"] != 1 or not re.fullmatch(r"remote_[a-f0-9]{24}", receipt["request_id"]):
            raise ValueError("invalid worker receipt identity")
        _hash(receipt["bundle_hash"])
        _hash(receipt["source_hash"])
        _image(receipt["image"])
        if not isinstance(receipt["job_id"], str) or not re.fullmatch(r"job_[a-f0-9]{12,32}", receipt["job_id"]):
            raise ValueError("invalid worker job ID")

    def submit(self, bundle: SourceBundle) -> dict:
        bundle.checked_files()
        receipt = self.transport.call("submit", {"bundle": bundle.model_dump(), "bundle_hash": bundle.digest})
        try:
            self._check_receipt(receipt)
            if any(receipt[key] != value for key, value in {"request_id": bundle.request_id, "bundle_hash": bundle.digest,
                                                         "source_hash": bundle.source_hash, "image": bundle.image}.items()):
                raise ValueError("worker receipt does not match submitted source/image")
        except (ValueError, TypeError):
            raise RemoteOutcomeUnknown(f"worker receipt invalid; reconcile {bundle.request_id}; do not replay") from None
        return receipt

    def reconcile(self, bundle: SourceBundle) -> dict:
        """Read the request journal after an uncertain submit, without execution."""
        bundle.checked_files()
        receipt = self.transport.call("lookup", {"request_id": bundle.request_id, "bundle_hash": bundle.digest})
        self._check_receipt(receipt)
        if any(receipt[key] != value for key, value in {"request_id": bundle.request_id, "bundle_hash": bundle.digest,
                                                     "source_hash": bundle.source_hash, "image": bundle.image}.items()):
            raise ValueError("reconciled receipt does not match submitted bundle")
        return receipt

    def _observe(self, action: str, receipt: dict, **options) -> dict:
        self._check_receipt(receipt)
        result = self.transport.call(action, {"receipt": receipt, **options})
        if result.get("receipt") != receipt:
            raise ValueError("worker observation belongs to a different bundle")
        if action == "output":
            page = result.get("output", {})
            if result.get("output_hash") != sha256(canonical(page)):
                raise ValueError("worker output manifest mismatch")
        else:
            status = result.get("status", {})
            if result.get("result_hash") != sha256(canonical(status)):
                raise ValueError("worker result manifest mismatch")
            if status.get("id") != receipt["job_id"] or sha256(canonical(status.get("source_hashes"))) != receipt["source_hash"]:
                raise ValueError("worker result source/job identity mismatch")
        return result

    def status(self, receipt: dict) -> dict:
        return self._observe("status", receipt)

    def cancel(self, receipt: dict) -> dict:
        return self._observe("cancel", receipt)

    def output(self, receipt: dict, *, stream="stdout", cursor=None, limit=16384) -> dict:
        return self._observe("output", receipt, stream=stream, cursor=cursor, limit=limit)


def _verify_host(image: str):
    if sys.platform != "linux" or not shutil.which("docker"):
        raise ValueError("remote worker requires a Docker-capable Linux host")
    from atomic_flow.agents.execution import clean_environment
    check = subprocess.run([shutil.which("docker"), "image", "inspect", _image(image), "--format", "{{.Id}}"],
                           env=clean_environment(user_dir()), capture_output=True, text=True, timeout=15)
    if check.returncode or check.stdout.strip() != image:
        raise ValueError("pinned Docker image is unavailable or mismatched")


class RemoteWorker:
    """Single-owner trusted worker, with journaled request deduplication.

    A preparing request left by a crash is uncertain and never automatically
    resubmitted. Its durable job ID (if recorded) can be reconciled separately.
    """
    def __init__(self, root: Path, *, image: str, max_requests=32):
        _verify_host(image)
        self.root = Path(root).absolute()
        if self.root.is_symlink() or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in [self.root, *self.root.parents]):
            raise ValueError("worker root must not contain links")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.image = image
        if not isinstance(max_requests, int) or not 1 <= max_requests <= 128:
            raise ValueError("invalid worker request cap")
        self.max_requests = max_requests

    def _record(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"remote_[a-f0-9]{24}", request_id):
            raise ValueError("invalid worker request ID")
        return confined_path(self.root, request_id + "/request.json")

    @staticmethod
    def _save(path, value):
        temp = path.with_suffix(".tmp")
        with temp.open("wb") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)

    def _store(self, receipt):
        from atomic_flow.jobs import JobStore
        # The source's canonical project hash already isolates each worker/request;
        # avoid an unnecessary directory level on Windows with legacy path limits.
        return JobStore(self._record(receipt["request_id"]).parent / "source", private_workspace=True)

    def call(self, action: str, payload: dict) -> dict:
        if action == "lookup":
            if set(payload) != {"request_id", "bundle_hash"}:
                raise ValueError("invalid lookup envelope")
            record = json.loads(self._record(payload["request_id"]).read_bytes())
            if record["bundle_hash"] != _hash(payload["bundle_hash"]):
                raise ValueError("request lookup hash mismatch")
            if record["state"] != "submitted":
                raise RemoteOutcomeUnknown("interrupted submission requires manual host reconciliation")
            return record["receipt"]
        if action == "submit":
            if set(payload) != {"bundle", "bundle_hash"}:
                raise ValueError("invalid submit envelope")
            bundle = SourceBundle.model_validate(payload["bundle"])
            files = bundle.checked_files()
            if bundle.digest != payload["bundle_hash"] or bundle.image != self.image:
                raise ValueError("bundle or pinned image mismatch")
            path = self._record(bundle.request_id)
            with exclusive_file(self.root / "worker.lock"):
                if path.exists():
                    prior = json.loads(path.read_bytes())
                    if prior["bundle_hash"] != bundle.digest:
                        raise ValueError("request ID belongs to a different bundle")
                    if prior["state"] != "submitted":
                        raise ValueError("interrupted submission outcome unknown; manual reconciliation required")
                    return prior["receipt"]
                if len(list(self.root.glob("remote_*/request.json"))) >= self.max_requests:
                    raise ValueError("worker request retention capacity reached")
                path.parent.mkdir(mode=0o700)
                record = {"state": "preparing", "bundle_hash": bundle.digest}
                self._save(path, record)  # durable intent before any child execution
                source = path.parent / "source"
                source.mkdir(mode=0o700)
                for rel, raw in files.items():
                    target = confined_path(source, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                receipt = {"version": 1, "request_id": bundle.request_id, "bundle_hash": bundle.digest,
                           "source_hash": bundle.source_hash, "image": bundle.image, "job_id": "job_" + "0" * 12}
                job = self._store(receipt).start(bundle.args, config=ExecutionConfig(mode="docker", image=self.image, profile=bundle.profile),
                                                 timeout=bundle.timeout, output_bytes=bundle.output_bytes)
                if sha256(canonical(job["source_hashes"])) != bundle.source_hash:
                    self._store(receipt).cancel(job["id"])
                    raise ValueError("job snapshot differs from the submitted source")
                receipt["job_id"] = job["id"]
                record.update(state="submitted", receipt=receipt)
                self._save(path, record)
                return receipt
        if action not in {"status", "cancel", "output"}:
            raise ValueError("unsupported worker operation")
        receipt = payload.get("receipt")
        RemoteExecutor._check_receipt(receipt)
        record = json.loads(self._record(receipt["request_id"]).read_bytes())
        if record.get("receipt") != receipt:
            raise ValueError("request receipt mismatch")
        store = self._store(receipt)
        store.reconcile(receipt["job_id"])
        if action == "output":
            if set(payload) - {"receipt", "stream", "cursor", "limit"}:
                raise ValueError("unsupported output fields")
            page = store.output(receipt["job_id"], stream=payload.get("stream", "stdout"), cursor=payload.get("cursor"), limit=payload.get("limit", 16384))
            return {"receipt": receipt, "output": page, "output_hash": sha256(canonical(page))}
        if set(payload) != {"receipt"}:
            raise ValueError("unsupported request fields")
        status = store.cancel(receipt["job_id"]) if action == "cancel" else store.status(receipt["job_id"])
        return {"receipt": receipt, "status": status, "result_hash": sha256(canonical(status))}


def worker_server(worker: RemoteWorker, token: str, *, host="127.0.0.1", port=0):
    """Loopback server for a TLS reverse proxy; never directly expose plain HTTP."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    if host not in {"127.0.0.1", "localhost"} or len(token) < 32 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ValueError("worker requires loopback binding and a strong token")
    slots = __import__("threading").BoundedSemaphore(4)

    class BoundedServer(ThreadingHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(10)  # applies before header parsing/authentication
            return connection, address

        def process_request(self, connection, address):
            if not slots.acquire(blocking=False):
                try:
                    connection.sendall(b"HTTP/1.0 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
                finally:
                    self.shutdown_request(connection)
                return
            try:
                super().process_request(connection, address)
            except BaseException:
                slots.release()
                raise

        def process_request_thread(self, connection, address):
            try:
                super().process_request_thread(connection, address)
            finally:
                slots.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # no request/credential payload logging

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                self.send_error(401)
                return
            try:
                self.connection.settimeout(10)
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_WIRE_BYTES or self.headers.get("Transfer-Encoding"):
                    self.send_error(413)
                    return
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("incomplete request")
                action = self.path.removeprefix("/v1/")
                if self.path != "/v1/" + action or action not in {"submit", "lookup", "status", "cancel", "output"}:
                    self.send_error(404)
                    return
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise ValueError("invalid envelope")
                body = canonical(worker.call(action, payload))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError, KeyError):
                self.send_error(400, "Worker refused request; inspect private host state")
            except RemoteOutcomeUnknown:
                self.send_error(503, "Worker outcome unknown; inspect private host state")

    server = BoundedServer((host, port), Handler)
    server.daemon_threads = True
    return server
