"""Bounded host observation receipts, never permission or semantic proof.

Receipts bind a completed observation to source-manifest and step-contract
digests. Host-declared source premises permit unrelated later source changes;
unknown premise coverage requires the conservative whole-manifest comparison.
A stale/missing receipt is diagnostic data: it does not authorize replay.
An unkeyed digest detects accidental changes, not a malicious host rewriting it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

MAX_RECEIPT_BYTES = 32_768
MAX_LINKS = 64
OUTCOMES = frozenset({"observed", "passed", "failed", "not_run", "unavailable"})
VERIFY_METHODS = frozenset({"none", "py_compile", "pytest", "ordinary_test"})
FIELDS = frozenset({"version", "task_id", "step_index", "request_id", "contract_sha256",
                    "before_manifest_sha256", "after_manifest_sha256", "approved_scope_sha256",
                    "graph_revision", "context_revision", "linked_code_nodes", "linked_test_nodes",
                    "verification", "tool_transcript_sha256", "observed_hashes", "digest"})


def _encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value) -> str:
    return hashlib.sha256(_encode(value)).hexdigest()


def _text(value, label, maximum=256):
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum or "\x00" in value:
        raise ValueError(f"invalid step evidence {label}")
    return value


def _links(value, label):
    if not isinstance(value, list) or len(value) > MAX_LINKS:
        raise ValueError(f"invalid step evidence {label}")
    result = [_text(item, label) for item in value]
    if any(not item for item in result) or len(set(result)) != len(result):
        raise ValueError(f"invalid step evidence {label}")
    return result


def _path(value):
    _text(value, "source path", 4096)
    path = PurePosixPath(value)
    if (not value or not path.parts or "\\" in value or ":" in value or path.is_absolute()
            or path.as_posix() != value or any(part in {".", ".."} for part in path.parts)):
        raise ValueError("invalid step evidence source path")
    return value


def manifest_digest(manifest: dict) -> str:
    """SHA-256 of a canonical path/content-digest manifest (legacy SHA-1 accepted)."""
    if not isinstance(manifest, dict) or len(manifest) > 100_000:
        raise ValueError("invalid step evidence source manifest")
    for path, digest in manifest.items():
        _path(path)
        if not isinstance(digest, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", digest):
            raise ValueError("invalid step evidence source digest")
    encoded = _encode(manifest)
    if len(encoded) > 16 * 1024 * 1024:
        raise ValueError("step evidence manifest exceeds byte limit")
    return hashlib.sha256(encoded).hexdigest()


def scope_digest(approved_scope) -> str:
    if not isinstance(approved_scope, (list, tuple, set, frozenset)) or len(approved_scope) > 100_000:
        raise ValueError("invalid step evidence approved scope")
    return _hash(sorted({_path(path) for path in approved_scope}))


def contract_digest(step) -> str:
    """Bind obligations and source links, excluding mutable execution state."""
    if type(step.index) is not int or step.index < 0:
        raise ValueError("invalid step evidence index")
    if step.verify_with not in VERIFY_METHODS:
        raise ValueError("invalid step evidence verification method")
    tools = _links(list(step.tools_needed), "tools")
    dependencies = step.depends_on
    if not isinstance(dependencies, list) or len(dependencies) > 1000 or any(type(i) is not int or i < 0 for i in dependencies):
        raise ValueError("invalid step evidence dependencies")
    criteria = getattr(step, "acceptance_criteria", [])
    if not isinstance(criteria, list) or len(criteria) > 64:
        raise ValueError("invalid step evidence acceptance criteria")
    payload = {"index": step.index, "description": _text(step.description, "description", 4096),
               "tools_needed": tools, "expected_outcome": _text(step.expected_outcome, "expected outcome", 4096),
               "verify_with": step.verify_with, "depends_on": dependencies,
               "parent_goal": _text(getattr(step, "parent_goal", ""), "parent goal"),
               "scope_paths": sorted(getattr(step, "scope_paths", [])),
               "acceptance_criteria": [_text(item, "acceptance criterion", 4096) for item in criteria],
               "linked_code_nodes": _links(getattr(step, "linked_code_nodes", []), "code links"),
               "linked_test_nodes": _links(getattr(step, "linked_test_nodes", []), "test links")}
    return _hash(payload)


def _transcript_digest(tool_calls) -> str:
    if not isinstance(tool_calls, (list, tuple)) or len(tool_calls) > 1000:
        raise ValueError("invalid step evidence tool transcript")
    digest = hashlib.sha256()
    for call in tool_calls:
        if not isinstance(call, dict):
            raise ValueError("invalid step evidence tool record")
        summary = call.get("result_summary", {})
        if not isinstance(summary, dict):
            raise ValueError("invalid step evidence tool summary")
        # Do not hash raw args, outputs, exception text, paths or user content.
        row = {"tool": _text(call.get("tool", ""), "tool name", 128)}
        for field in ("outcome", "status"):
            if field in summary:
                row[field] = _text(summary[field], "tool " + field, 64)
        if "exit_code" in summary:
            code = summary["exit_code"]
            if code is not None and (type(code) is not int or not -(2**32) <= code < 2**32):
                raise ValueError("invalid step evidence tool exit code")
            row["exit_code"] = code
        if "checks" in summary:
            row["checks"] = _links(summary["checks"], "tool checks")
        digest.update(_encode(row) + b"\n")
    return digest.hexdigest()


def build_receipt(task_id, step, *, before_manifest, after_manifest, approved_scope,
                  tool_calls=(), verification_outcome="observed", graph_revision="", context_revision="",
                  premise_paths=None) -> dict:
    """Construct an unsigned host observation. Outcome must come from host checks.

    ``premise_paths=None`` means dependency coverage is unknown. An explicit
    list is a trusted host declaration of source premises, not a graph proof.
    An empty list means the host observed no source dependency at all.
    """
    if premise_paths is not None:
        if not isinstance(premise_paths, (list, tuple, set, frozenset)) or len(premise_paths) > 128:
            raise ValueError("invalid step evidence premise paths")
        premise_paths = sorted({_path(path) for path in premise_paths})
    receipt = {"version": 1, "task_id": task_id, "step_index": step.index,
               "request_id": getattr(step, "request_id", ""), "contract_sha256": contract_digest(step),
               "before_manifest_sha256": manifest_digest(before_manifest),
               "after_manifest_sha256": manifest_digest(after_manifest),
               "approved_scope_sha256": scope_digest(approved_scope),
               "graph_revision": graph_revision, "context_revision": context_revision,
               "linked_code_nodes": list(getattr(step, "linked_code_nodes", [])),
               "linked_test_nodes": list(getattr(step, "linked_test_nodes", [])),
               "verification": {"method": step.verify_with, "outcome": verification_outcome},
               "observed_hashes": None if premise_paths is None else {path: after_manifest.get(path) for path in premise_paths},
               "tool_transcript_sha256": _transcript_digest(tool_calls)}
    receipt["digest"] = _hash(receipt)
    validate_receipt(receipt)
    return receipt


def validate_receipt(receipt) -> None:
    """Validate strict shape, bounds and digest; empty legacy receipts are allowed."""
    if not isinstance(receipt, dict):
        raise ValueError("invalid step evidence receipt")
    if not receipt:
        return
    if set(receipt) != FIELDS or type(receipt["version"]) is not int or receipt["version"] != 1:
        raise ValueError("invalid step evidence fields/version")
    if not isinstance(receipt["task_id"], str) or not re.fullmatch(r"task_[a-f0-9]{12}", receipt["task_id"]):
        raise ValueError("invalid step evidence task binding")
    if type(receipt["step_index"]) is not int or receipt["step_index"] < 0:
        raise ValueError("invalid step evidence index")
    request_id = _text(receipt["request_id"], "request id", 128)
    if request_id and not re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id):
        raise ValueError("invalid step evidence request id")
    for name in ("contract_sha256", "before_manifest_sha256", "after_manifest_sha256", "approved_scope_sha256",
                 "tool_transcript_sha256", "digest"):
        if not isinstance(receipt[name], str) or not re.fullmatch(r"[a-f0-9]{64}", receipt[name]):
            raise ValueError("invalid step evidence digest")
    for name in ("graph_revision", "context_revision"):
        _text(receipt[name], name)
    for name in ("linked_code_nodes", "linked_test_nodes"):
        _links(receipt[name], name)
    observed = receipt["observed_hashes"]
    if observed is not None:
        if not isinstance(observed, dict) or len(observed) > 128:
            raise ValueError("invalid step evidence observed hashes")
        for path, digest in observed.items():
            _path(path)
            if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", digest)):
                raise ValueError("invalid step evidence premise digest")
    verification = receipt["verification"]
    if (not isinstance(verification, dict) or set(verification) != {"method", "outcome"}
            or not isinstance(verification["method"], str) or verification["method"] not in VERIFY_METHODS
            or not isinstance(verification["outcome"], str) or verification["outcome"] not in OUTCOMES):
        raise ValueError("invalid step evidence verification observation")
    if len(_encode(receipt)) > MAX_RECEIPT_BYTES:
        raise ValueError("step evidence receipt exceeds byte limit")
    if receipt["digest"] != _hash({key: value for key, value in receipt.items() if key != "digest"}):
        raise ValueError("step evidence integrity mismatch")


def receipt_matches(receipt, step, manifest, *, task_id, approved_scope=None) -> tuple[bool, list[str]]:
    """Classify stale evidence without changing state or scheduling a replay."""
    try:
        validate_receipt(receipt)
        if not receipt:
            return False, ["missing legacy step evidence; no replay authorized"]
        reasons = []
        if receipt["task_id"] != task_id or receipt["step_index"] != step.index:
            reasons.append("task/step binding changed")
        if receipt["request_id"] != getattr(step, "request_id", ""):
            reasons.append("request binding changed")
        if receipt["contract_sha256"] != contract_digest(step):
            reasons.append("step contract changed")
        current_manifest_digest = manifest_digest(manifest)
        observed = receipt["observed_hashes"]
        if observed is None:
            if receipt["after_manifest_sha256"] != current_manifest_digest:
                reasons.append("source manifest changed")
        elif any(manifest.get(path) != expected for path, expected in observed.items()):
            reasons.append("observed source premises changed")
        if approved_scope is not None and receipt["approved_scope_sha256"] != scope_digest(approved_scope):
            reasons.append("approved scope changed")
        return not reasons, reasons
    except (AttributeError, TypeError, ValueError) as exc:
        return False, [f"invalid step evidence: {exc}; no replay authorized"]
