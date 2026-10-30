"""Bounded explicit-source pytest bridge, not an automatic TaskRunner executor.

Requests bind immutable source bytes, one fixed command and an immutable image.
Digests detect substitution; they do not authenticate or attest an untrusted
worker. Missing-test/skip semantics require an independent repository oracle.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from atomic_flow.locking import exclusive_file
from atomic_flow.remote import RemoteExecutor, RemoteOutcomeUnknown, SourceBundle, build_bundle, canonical, sha256

PYTEST_ARGS = ("-m", "pytest", "-q", "--no-header", "-x")


@dataclass(frozen=True)
class RemoteCheckRequest:
    """Byte-immutable host request over the existing 64-file/4-MiB envelope."""

    source_root: str
    bundle_bytes: bytes
    check: Literal["pytest"] = "pytest"

    def __post_init__(self):
        if self.check != "pytest" or type(self.bundle_bytes) is not bytes:
            raise ValueError("one fixed pytest command and immutable bundle bytes required")
        root = Path(self.source_root)
        if not root.is_absolute() or str(root.resolve()) != self.source_root:
            raise ValueError("canonical absolute source root required")
        bundle = self.bundle()
        if bundle.project_hash != sha256(self.source_root.encode()):
            raise ValueError("bundle belongs to a different project")
        if bundle.profile != "checks" or tuple(bundle.args) != PYTEST_ARGS:
            raise ValueError("remote check command/profile cannot be overridden")
        if canonical(bundle.model_dump()) != self.bundle_bytes:
            raise ValueError("canonical immutable bundle encoding required")

    def bundle(self) -> SourceBundle:
        bundle = SourceBundle.model_validate(json.loads(self.bundle_bytes))
        bundle.checked_files()
        return bundle

    @property
    def binding_digest(self) -> str:
        return sha256(canonical({"version": 1, "check": self.check, "source_root": self.source_root,
                                 "scope": "explicit_source_bundle", "bundle": json.loads(self.bundle_bytes)}))

    @classmethod
    def prepare(cls, source_root: Path, paths: list[str], *, image: str, timeout=120, output_bytes=1_048_576):
        root = Path(source_root).resolve()
        bundle = build_bundle(root, paths, list(PYTEST_ARGS), image=image, timeout=timeout, output_bytes=output_bytes)
        return cls(str(root), canonical(bundle.model_dump()))

    def source_is_current(self) -> bool:
        original = self.bundle()
        current = build_bundle(Path(self.source_root), [f.path for f in original.files], list(PYTEST_ARGS),
                               image=original.image, timeout=original.timeout, output_bytes=original.output_bytes)
        return current.source_hash == original.source_hash


@dataclass(frozen=True)
class RemoteCheckTicket:
    request_digest: str
    worker_receipt_bytes: bytes

    def receipt(self, request: RemoteCheckRequest) -> dict:
        if self.request_digest != request.binding_digest or type(self.worker_receipt_bytes) is not bytes:
            raise ValueError("check ticket belongs to a different immutable request")
        receipt = json.loads(self.worker_receipt_bytes)
        RemoteExecutor._check_receipt(receipt)
        bundle = request.bundle()
        if any(receipt[key] != value for key, value in {
                "request_id": bundle.request_id, "bundle_hash": bundle.digest,
                "source_hash": bundle.source_hash, "image": bundle.image}.items()):
            raise ValueError("check ticket source/image/command binding mismatch")
        return receipt


@dataclass(frozen=True)
class RemoteCheckResult:
    request_digest: str
    source_hash: str
    image: str
    worker_result_hash: str
    status: Literal["pending", "process_passed", "process_failed", "infrastructure_error"]
    returncode: int | None
    source_current: bool
    reason: str
    test_case_completeness: Literal["not_assessed"] = "not_assessed"
    semantic_correctness: Literal["not_assessed"] = "not_assessed"
    verified_success: Literal[False] = False

    def __post_init__(self):
        if (self.status not in {"pending", "process_passed", "process_failed", "infrastructure_error"}
                or type(self.source_current) is not bool
                or self.returncode is not None and type(self.returncode) is not int
                or self.verified_success is not False or self.test_case_completeness != "not_assessed"
                or self.semantic_correctness != "not_assessed"):
            raise ValueError("remote bridge cannot fabricate an oracle grade or malformed process result")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in (self.request_digest, self.source_hash, self.worker_result_hash)):
            raise ValueError("invalid remote result digest")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise ValueError("immutable worker image required")
        if (self.status == "process_passed" and (self.returncode != 0 or not self.source_current)
                or self.status == "process_failed" and (self.returncode in {0, None} or not self.source_current)):
            raise ValueError("process verdict and current source/exit status disagree")


class RemoteCheckAdapter:
    """Durable one-submit bridge; uncertainty holds new admission globally.

    This is intentionally not wired into config/run_python/TaskRunner. A caller
    explicitly prepares, submits and observes a request; reconciliation only
    performs the existing worker lookup, never a new execution.
    """

    def __init__(self, executor: RemoteExecutor, journal_root: Path):
        self.executor = executor
        self.root = Path(journal_root).absolute()
        if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
               for path in (self.root, *self.root.parents)):
            raise ValueError("check journal cannot contain links")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, request):
        return self.root / (request.binding_digest + ".json")

    @staticmethod
    def _save(path, value):
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _record(self, request):
        record = json.loads(self._path(request).read_bytes())
        if record.get("request_digest") != request.binding_digest or record.get("bundle_hash") != request.bundle().digest:
            raise ValueError("check journal/request binding mismatch")
        return record

    def _ticket(self, request, record):
        ticket = RemoteCheckTicket(request.binding_digest, canonical(record["worker_receipt"]))
        ticket.receipt(request)
        return ticket

    def submit(self, request: RemoteCheckRequest) -> RemoteCheckTicket:
        bundle = request.bundle()
        if not request.source_is_current():
            raise ValueError("source changed before remote check admission")
        with exclusive_file(self.root / "adapter.lock"):
            if self._path(request).exists():
                record = self._record(request)
                if record["state"] != "submitted":
                    raise RemoteOutcomeUnknown("check outcome uncertain; reconcile this request, do not resubmit")
                return self._ticket(request, record)
            for path in self.root.glob("*.json"):
                if json.loads(path.read_bytes()).get("state") != "submitted":
                    raise RemoteOutcomeUnknown("another check has an uncertain outcome; no new admission")
            record = {"version": 1, "state": "submitting", "request_digest": request.binding_digest,
                      "bundle_hash": bundle.digest, "source_hash": bundle.source_hash, "image": bundle.image}
            self._save(self._path(request), record)
            try:
                receipt = self.executor.submit(bundle)
            except BaseException:
                record["state"] = "outcome_unknown"
                self._save(self._path(request), record)
                raise
            record.update(state="reconciled_pending_observation", worker_receipt=receipt)
            self._save(self._path(request), record)
            return self._ticket(request, record)

    def reconcile(self, request: RemoteCheckRequest) -> RemoteCheckTicket:
        with exclusive_file(self.root / "adapter.lock"):
            record = self._record(request)
            # Only lookup existing request ID/bundle hash; never submit/replay.
            receipt = self.executor.reconcile(request.bundle())
            record.update(state="reconciled_pending_observation", worker_receipt=receipt)
            self._save(self._path(request), record)
            return self._ticket(request, record)

    def observe(self, request: RemoteCheckRequest, ticket: RemoteCheckTicket) -> RemoteCheckResult:
        receipt = ticket.receipt(request)
        with exclusive_file(self.root / "adapter.lock"):
            record = self._record(request)
            if record.get("worker_receipt") != receipt or record["state"] not in {"submitted", "reconciled_pending_observation"}:
                raise RemoteOutcomeUnknown("check needs reconciliation before observation")
            try:
                observation = self.executor.status(receipt)
                status = observation["status"]
                state = status.get("status")
                if state not in {"queued", "running", "succeeded", "failed", "cancelled", "timed_out", "interrupted", "output_limit"}:
                    raise ValueError("unrecognized check worker state")
                if type(status.get("effects_unknown")) is not bool or type(status.get("output_complete")) is not bool:
                    raise ValueError("worker omitted explicit effect/output semantics")
                if status["effects_unknown"]:
                    raise RemoteOutcomeUnknown("worker effects remain unknown; no further check admission")
                if state not in {"queued", "running"} and status.get("cleanup_confirmed") is not True:
                    raise RemoteOutcomeUnknown("terminal check cleanup is unconfirmed; no further check admission")
            except BaseException:
                record["state"] = "observation_unknown"
                self._save(self._path(request), record)
                raise
            record["state"] = "submitted"
            self._save(self._path(request), record)
            current = request.source_is_current()
            code = status.get("returncode")
            result, reason = "infrastructure_error", "worker did not prove a clean terminal check"
            if state in {"queued", "running"}:
                result, reason = "pending", "check execution has not completed"
            elif (state in {"succeeded", "failed"} and type(code) is int
                  and status.get("cleanup_confirmed") is True and status["output_complete"]
                  and status.get("output_expired") is False and type(status.get("discarded_bytes")) is int
                  and status["discarded_bytes"] == 0
                  and (state == "succeeded") == (code == 0)):
                result = "process_passed" if code == 0 else "process_failed"
                reason = "fixed pytest process completed; required-test and skip semantics are not assessed"
            if not current:
                result, reason = "infrastructure_error", "current source differs from the checked immutable source bundle"
            return RemoteCheckResult(request.binding_digest, request.bundle().source_hash, receipt["image"],
                observation["result_hash"], result, code if type(code) is int else None, current, reason)

    def __call__(self, request: RemoteCheckRequest) -> RemoteCheckResult:
        return self.observe(request, self.submit(request))
