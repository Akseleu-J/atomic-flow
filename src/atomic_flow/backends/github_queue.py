"""Host-only P07 model data queue; credentials never enter the queue protocol.

The queue transports public benchmark prompts, not executable code. A separate
single-writer Windows broker admits and sends physical inference. This adapter
does not attest GitHub or its trusted writers and never retries model requests.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from urllib import error, parse, request

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.backends.base import ChatResult, ResponseShapeError
from atomic_flow.backends.cancellation import current_check
from atomic_flow.config import BackendConfig
from atomic_flow.durability import atomic_bytes
from atomic_flow.errors import BackendError
from atomic_flow.locking import exclusive_file

MAX_WIRE_BYTES = 1_048_576
MODEL = "anthropic/claude-sonnet-5@default"
OWNERS = frozenset({"akseley", "umirbayulgaisha"})
_CALL = re.compile(r"mq_[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_IDENTITY = {"campaign_id", "owner", "arm", "task_id", "framework_sha", "workflow_sha", "profile_sha256"}
_CONFIG = {"model", "max_input_tokens", "max_output_tokens", "temperature", "max_retries"}
_RESULT = {"content", "tokens_in", "tokens_out", "cost_usd", "latency_ms", "truncated", "cost_source",
           "resolved_backend", "resolved_model", "attempt_id"}


def canonical(value) -> bytes:
    def check(item):
        if item is None or type(item) is bool:
            return
        if type(item) is int and abs(item) <= 9007199254740991:
            return
        if type(item) is str:
            item.encode("utf-8", errors="strict")
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(k) is str and k.isascii() for k in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("wire data requires ASCII keys, valid UTF-8 and safe integers")
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def validate_request(value: dict) -> None:
    keys = {"schema_version", "kind", "call_id", "identity", "config", "messages", "messages_sha256", "request_sha256"}
    if (type(value) is not dict or set(value) != keys or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != "p07_model_request"
            or type(value["call_id"]) is not str or not _CALL.fullmatch(value["call_id"])):
        raise ValueError("invalid model queue request")
    identity = value["identity"]
    if type(identity) is not dict or set(identity) != _IDENTITY or type(identity.get("owner")) is not str or identity.get("owner") not in OWNERS:
        raise ValueError("invalid fixed model queue owner/identity")
    if any(type(identity[k]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity[k]) is None
           for k in ("campaign_id", "arm", "task_id")):
        raise ValueError("invalid model queue identity label")
    if any(type(identity[k]) is not str or re.fullmatch(r"[a-f0-9]{40}", identity[k]) is None
           for k in ("framework_sha", "workflow_sha")) or type(identity["profile_sha256"]) is not str or not _SHA.fullmatch(identity["profile_sha256"]):
        raise ValueError("unbound model queue runtime/profile")
    config = value["config"]
    if (type(config) is not dict or set(config) != _CONFIG or config["model"] != MODEL
            or type(config["max_retries"]) is not int or config["max_retries"] != 0
            or not _integer(config["max_input_tokens"], 1, 10_000_000)
            or not _integer(config["max_output_tokens"], 16, 200_000)):
        raise ValueError("invalid frozen model queue configuration")
    temperature = config["temperature"]
    if temperature is not None and (type(temperature) is not str or not re.fullmatch(r"(?:0|1)(?:\.[0-9]+)?|2(?:\.0+)?", temperature)):
        raise ValueError("invalid queue temperature")
    messages = value["messages"]
    if type(messages) is not list or not 1 <= len(messages) <= 256:
        raise ValueError("bounded text messages required")
    for message in messages:
        if (type(message) is not dict or set(message) != {"role", "content"}
                or message["role"] not in {"system", "user", "assistant"} or type(message["content"]) is not str):
            raise ValueError("only bounded text-json model messages are admitted")
    if (len(canonical(value)) > MAX_WIRE_BYTES or value["messages_sha256"] != digest(messages)
            or value["request_sha256"] != digest({k: v for k, v in value.items() if k != "request_sha256"})):
        raise ValueError("model queue request bounds/hash mismatch")


def response_packet(req: dict, *, state: str, result=None, error_code="none") -> dict:
    """Canonical response constructor for trusted scripted transports/tests."""
    value = {"schema_version": 1, "kind": "p07_model_response", "call_id": req["call_id"],
             "request_sha256": req["request_sha256"], "identity_sha256": digest(req["identity"]),
             "state": state, "result": result, "error_code": error_code}
    return {**value, "response_sha256": digest(value)}


class QueueOutcomeUnknown(BackendError):
    request_outcome = "unknown"


class QueueAdmissionRefused(BackendError):
    request_outcome = "not_sent"


def _result(value, req) -> ChatResult:
    if (type(value) is not dict or set(value) != _RESULT or type(value["content"]) is not str
            or len(value["content"].encode()) > MAX_WIRE_BYTES
            or not _integer(value["tokens_in"], 0, req["config"]["max_input_tokens"])
            or not _integer(value["tokens_out"], 0, req["config"]["max_output_tokens"])
            or not _integer(value["latency_ms"], 0, 86_400_000) or type(value["truncated"]) is not bool
            or value["resolved_model"] != MODEL or type(value["cost_source"]) is not str
            or len(value["cost_source"].encode("utf-8")) > 100 or value["resolved_backend"] != "kaggle_model_proxy"
            or value["attempt_id"] != req["call_id"]):
        raise ValueError("invalid bounded physical model result")
    cost = value["cost_usd"]
    if type(cost) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", cost) or len(cost) > 64:
        raise ValueError("complete finite reported cost required; unknown is not zero")
    amount = float(cost)
    if not math.isfinite(amount):
        raise ValueError("nonfinite physical model cost")
    return ChatResult(**{**value, "cost_usd": amount})


class GitHubQueueBackend:
    name = "p07_github_queue"

    def __init__(self, config: BackendConfig, transport, identity: dict, journal_root: Path, *,
                 cancel_check=None, poll_seconds=2.0, wait_seconds=None, transport_identity=None):
        if (config.model != MODEL or config.max_retries != 0 or config.tool_protocol != "text_json"
                or config.max_input_tokens is None or config.max_output_tokens is None
                or config.api_key or config.api_key_env or config.base_url):
            raise ValueError("fixed text-json queue config without provider credentials required")
        self.config = config.model_copy(deep=True)
        self.transport, self.identity = transport, json.loads(canonical(identity))
        self.transport_identity = json.loads(canonical(
            transport_identity if transport_identity is not None else getattr(transport, "binding", None)))
        if (type(self.transport_identity) is not dict or set(self.transport_identity) != {"repo", "branch", "prefix"}
                or any(type(v) is not str or not v for v in self.transport_identity.values())):
            raise ValueError("explicit fixed queue repo/branch/prefix binding required")
        self.name = "p07_github_queue:" + digest({"identity": self.identity, "queue": self.transport_identity})
        self.config.name = self.name
        self._config_snapshot = self.config.model_dump(mode="json")
        self._binding_snapshot = canonical({"identity": self.identity, "queue": self.transport_identity})
        probe = {"schema_version": 1, "kind": "p07_model_request", "call_id": "mq_" + "0" * 32,
                 "identity": self.identity, "config": self._wire_config(),
                 "messages": [{"role": "user", "content": "binding validation"}]}
        probe["messages_sha256"] = digest(probe["messages"])
        probe["request_sha256"] = digest(probe)
        validate_request(probe)
        self.root = Path(journal_root).absolute()
        self.cancel_check = cancel_check or (lambda: None)
        self.poll_seconds = float(poll_seconds)
        self.wait_seconds = float(config.timeout_seconds if wait_seconds is None else wait_seconds)
        if (not math.isfinite(self.poll_seconds) or not 0 < self.poll_seconds <= 30
                or not math.isfinite(self.wait_seconds) or not 0 < self.wait_seconds <= 600):
            raise ValueError("bounded queue polling/deadline required")
        self._safe_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def _wire_config(self):
        return {"model": self.config.model, "max_input_tokens": self.config.max_input_tokens,
                "max_output_tokens": self.config.max_output_tokens,
                "temperature": None if self.config.temperature is None else str(self.config.temperature),
                "max_retries": 0}

    def _verify_binding(self):
        if (self.config.model_dump(mode="json") != self._config_snapshot
                or canonical({"identity": self.identity, "queue": self.transport_identity}) != self._binding_snapshot
                or getattr(self.transport, "binding", self.transport_identity) != self.transport_identity):
            raise QueueAdmissionRefused("reviewed model queue binding changed")

    def _safe_root(self):
        if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (self.root, *self.root.parents)):
            raise ValueError("model queue journal may not contain links")

    def _write(self, call_id, value):
        self._safe_root()
        target = self.root / (call_id + ".json")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise ValueError("model queue journal record is linked")
        atomic_bytes(target, canonical(value))

    def _require_clear(self):
        self._safe_root()
        records = list(self.root.iterdir())
        if len(records) > 10_000:
            raise ValueError("model queue journal capacity exceeded")
        for path in records:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 3 * MAX_WIRE_BYTES:
                raise QueueOutcomeUnknown("model queue journal requires host reconciliation")
            try:
                record = json.loads(path.read_bytes())
            except (OSError, ValueError):
                raise QueueOutcomeUnknown("model queue journal requires host reconciliation") from None
            if type(record) is not dict or record.get("state") not in {"completed", "refused"}:
                raise QueueOutcomeUnknown("prior model queue attempt remains unresolved; never resubmit")

    def _accept(self, req, record, response):
        keys = {"schema_version", "kind", "call_id", "request_sha256", "identity_sha256", "state", "result", "error_code", "response_sha256"}
        if (type(response) is not dict or set(response) != keys or len(canonical(response)) > MAX_WIRE_BYTES
                or type(response["schema_version"]) is not int or response["schema_version"] != 1
                or response["kind"] != "p07_model_response" or response["call_id"] != req["call_id"]
                or response["request_sha256"] != req["request_sha256"]
                or response["identity_sha256"] != digest(req["identity"])
                or response["response_sha256"] != digest({k: v for k, v in response.items() if k != "response_sha256"})):
            raise QueueOutcomeUnknown("model queue response binding invalid; reconcile original attempt")
        state = response["state"]
        if state == "refused" and response["result"] is None and response["error_code"] == "admission_refused":
            record["state"] = "refused"
            record["response"] = response
            self._write(req["call_id"], record)
            raise QueueAdmissionRefused("broker refused inference before sending")
        if state != "completed" or response["error_code"] not in {"none", "response_shape"}:
            raise QueueOutcomeUnknown("broker inference or accounting outcome remains unknown")
        try:
            result = _result(response["result"], req)
        except (ValueError, TypeError):
            raise QueueOutcomeUnknown("invalid physical result; reconcile original attempt") from None
        record["response"] = response
        record["state"] = "completed"
        self._write(req["call_id"], record)
        if response["error_code"] == "response_shape":
            raise ResponseShapeError("model returned unusable content; physical usage retained", result)
        return result

    def _check(self):
        self.cancel_check()
        scoped = current_check()
        if scoped is not None:
            scoped()

    def chat(self, messages, *, json_mode=False, model=None):
        self._verify_binding()
        try:
            self._check()
        except TaskCancelled as exc:
            exc.request_outcome = "not_sent"
            raise
        self._safe_root()
        lock = self.root.parent / (self.root.name + ".admission.lock")
        if lock.is_symlink() or getattr(lock, "is_junction", lambda: False)():
            raise QueueAdmissionRefused("model queue admission lease is linked")
        with ExitStack() as stack:
            try:
                stack.enter_context(exclusive_file(lock))
            except ValueError:
                raise QueueAdmissionRefused("another model queue attempt holds the admission lease") from None
            return self._chat_locked(messages, json_mode=json_mode, model=model)

    def _chat_locked(self, messages, *, json_mode=False, model=None):
        self._verify_binding()
        if model is not None and model != self.config.model:
            raise QueueAdmissionRefused("queue model substitution refused")
        try:
            self._check()
        except TaskCancelled as exc:
            exc.request_outcome = "not_sent"
            raise
        self._require_clear()
        req = {"schema_version": 1, "kind": "p07_model_request", "call_id": "mq_" + uuid.uuid4().hex,
               "identity": self.identity, "config": self._wire_config(),
               "messages": messages, "messages_sha256": digest(messages)}
        req["request_sha256"] = digest(req)
        validate_request(req)
        record = {"state": "intent", "request": req}
        self._write(req["call_id"], record)
        try:
            self._check()
        except BaseException as exc:
            record["state"] = "refused"
            self._write(req["call_id"], record)
            if isinstance(exc, TaskCancelled):
                exc.request_outcome = "not_sent"
            raise
        try:
            self.transport.put_request(req["call_id"], req)
            record["state"] = "queued"
            self._write(req["call_id"], record)
            deadline = time.monotonic() + self.wait_seconds
            while True:
                self._check()
                response = self.transport.get_response(req["call_id"])
                if response is not None:
                    return self._accept(req, record, response)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QueueOutcomeUnknown("model queue wait timed out; reconcile, never resend")
                time.sleep(min(self.poll_seconds, remaining))
        except (QueueAdmissionRefused, ResponseShapeError):
            raise
        except BaseException:
            record["state"] = "unknown"
            self._write(req["call_id"], record)
            raise QueueOutcomeUnknown("model queue attempt unresolved; no automatic retry") from None

    def reconcile(self, call_id):
        """Observe only; caller still reconciles the original framework ledger."""
        if type(call_id) is not str or not _CALL.fullmatch(call_id):
            raise ValueError("invalid original model queue call ID")
        self._verify_binding()
        self._safe_root()
        path = self.root / (call_id + ".json")
        if path.is_symlink() or path.stat().st_size > 3 * MAX_WIRE_BYTES:
            raise ValueError("invalid model queue journal")
        record = json.loads(path.read_bytes())
        req = record["request"]
        validate_request(req)
        if req["call_id"] != call_id or req["identity"] != self.identity:
            raise ValueError("original model queue identity changed")
        response = self.transport.get_response(call_id)
        return None if response is None else self._accept(req, record, response)


class GitHubContentsQueueTransport:
    """Fixed JSON data branch using an ephemeral host GitHub token, no shell."""
    def __init__(self, repo, branch, token, *, prefix="p07-model-queue", timeout_seconds=30, api_client=None):
        if (type(repo) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo)
                or type(branch) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*", branch)
                or not branch.startswith(("codex/p07-model-queue", "p07-model-queue"))
                or any(p in {".", ".."} for p in branch.split("/"))
                or type(prefix) is not str or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix)
                or type(token) is not str or not token or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 60):
            raise ValueError("invalid fixed GitHub queue transport")
        self.repo, self.branch, self.prefix = repo, branch, prefix
        self._token, self.timeout = token, timeout_seconds
        self._api_client = api_client
        if api_client is not None and not callable(api_client):
            raise ValueError("injected GitHub API client must be callable")

    @property
    def binding(self):
        return {"repo": self.repo, "branch": self.branch, "prefix": self.prefix}

    def _api(self, method, path, body=None):
        if self._api_client is not None:
            return self._api_client(method, path, body)
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("GitHub queue redirects refused")
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        req = request.Request("https://api.github.com/repos/" + self.repo + "/contents/" + path,
                              None if body is None else canonical(body), method=method,
                              headers={"Authorization": "Bearer " + self._token, "Accept": "application/vnd.github+json",
                                       "Content-Type": "application/json", "User-Agent": "atomic-flow-p07-model-queue"})
        try:
            with opener.open(req, timeout=self.timeout) as response:
                raw = response.read(2 * MAX_WIRE_BYTES + 1)
            if len(raw) > 2 * MAX_WIRE_BYTES:
                raise ValueError("GitHub queue API response exceeded bound")
            return json.loads(raw)
        except error.HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            raise QueueOutcomeUnknown("GitHub queue request failed; reconcile by call ID") from None
        except (OSError, ValueError):
            raise QueueOutcomeUnknown("GitHub queue response uncertain; reconcile by call ID") from None

    def _get(self, kind, call_id):
        if type(call_id) is not str or not _CALL.fullmatch(call_id):
            raise ValueError("invalid model queue call ID")
        value = self._api("GET", f"{self.prefix}/{kind}/{call_id}.json?ref=" + parse.quote(self.branch, safe=""))
        if value is None:
            return None
        if type(value) is not dict or value.get("encoding") != "base64" or type(value.get("content")) is not str:
            raise QueueOutcomeUnknown("invalid GitHub queue file envelope")
        raw = base64.b64decode("".join(value["content"].split()), validate=True)
        if len(raw) > MAX_WIRE_BYTES:
            raise QueueOutcomeUnknown("GitHub queue file exceeded bound")
        return json.loads(raw)

    def put_request(self, call_id, value):
        validate_request(value)
        if call_id != value["call_id"]:
            raise ValueError("queue call ID mismatch")
        previous = self._get("requests", call_id)
        if previous is not None:
            if canonical(previous) != canonical(value):
                raise QueueOutcomeUnknown("immutable model queue request conflict")
            return
        self._api("PUT", f"{self.prefix}/requests/{call_id}.json", {
            "message": "[skip ci] p07 bounded model request " + call_id, "branch": self.branch,
            "content": base64.b64encode(canonical(value)).decode("ascii")})

    def get_response(self, call_id):
        return self._get("responses", call_id)
