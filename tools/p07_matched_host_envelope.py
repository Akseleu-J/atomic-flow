"""Canonical declarations for common matched-arm host inputs.

Pure data only: callers must independently establish that declarations match
host objects, callbacks, transport implementation, filesystem and checkpoint.
This is not runtime admission, an effect guard, a source lease or authorization
to send. Strategy, role prompts and task-runner execution remain separate.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from atomic_flow.project_resources import ProjectLimits
from tools.p07_matched_arm_contract import arm_projection
from tools.p07_public_task_packet import canonical_bytes

_FACTORY = object()
_SHA = re.compile(r"[0-9a-f]{64}")
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+")
_CALLBACKS = frozenset({"approval", "question", "cancel"})
_QUEUE_FIELDS = frozenset({"repo", "branch", "prefix", "implementation_sha256", "journal_sha256",
                           "checkpoint_sha256", "poll_ms", "wait_ms"})


def _sha(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ValueError("SHA256 declarations require lowercase hexadecimal")
    return value


def _path(value, *, repository=False):
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise ValueError("bounded plain queue identifier required")
    parts = value.split("/")
    if (repository and len(parts) != 2) or any(not _COMPONENT.fullmatch(x) or x in {".", ".."} for x in parts):
        raise ValueError("queue identifier has unsupported syntax")
    return value


def _callbacks(value):
    if type(value) is not dict or set(value) != _CALLBACKS:
        raise ValueError("exact approval, question and cancellation declarations required")
    result = {}
    for name in sorted(_CALLBACKS):
        entry = value[name]
        if type(entry) is not dict or type(entry.get("mode")) is not str:
            raise ValueError("callback declaration must be a plain mapping")
        if entry == {"mode": "absent"}:
            result[name] = {"mode": "absent"}
        elif set(entry) == {"mode", "implementation_sha256", "parameters_sha256"} and entry["mode"] == "registered":
            result[name] = {"mode": "registered", "implementation_sha256": _sha(entry["implementation_sha256"]),
                            "parameters_sha256": _sha(entry["parameters_sha256"])}
        else:
            raise ValueError("callback must be absent or a registered host declaration")
    return result


def _queue(value):
    if type(value) is not dict or set(value) != _QUEUE_FIELDS:
        raise ValueError("exact transport and journal declarations required")
    for name, maximum in (("poll_ms", 30_000), ("wait_ms", 600_000)):
        if type(value[name]) is not int or not 1 <= value[name] <= maximum:
            raise ValueError("queue timing requires bounded integral milliseconds")
    return {"repo": _path(value["repo"], repository=True), "branch": _path(value["branch"]),
            "prefix": _path(value["prefix"]), "implementation_sha256": _sha(value["implementation_sha256"]),
            "journal_sha256": _sha(value["journal_sha256"]), "checkpoint_sha256": _sha(value["checkpoint_sha256"]),
            "poll_ms": value["poll_ms"], "wait_ms": value["wait_ms"]}


@dataclass(frozen=True, slots=True, init=False)
class FrozenHostEnvelope:
    """Canonical bytes only; successful construction is not host admission."""
    canonical: bytes

    def __init__(self, raw, *, _factory=None):
        if _factory is not _FACTORY or type(raw) is not bytes:
            raise ValueError("use freeze_host_envelope for validated declarations")
        object.__setattr__(self, "canonical", raw)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical).hexdigest()

    def as_dict(self):
        return json.loads(self.canonical)


def freeze_host_envelope(manifest, task_id, *, resource_limits, callbacks, queue):
    """Build a common declaration from a frozen pair and caller-supplied data.

    Callback implementation/parameters and journal/checkpoint values are SHA256
    declarations only. Queue journal/checkpoint hashes identify the caller's
    declared common accounting domain, not an arm's private working path or
    mutable checkpoint contents. Any distinct per-arm journal roots must be
    bound by a later arm-specific host contract. No callable is inspected or
    authenticated, no path is opened, no remote authority is checked, and no
    runner is instantiated.
    """
    if resource_limits is not None and type(resource_limits) is not ProjectLimits:
        raise ValueError("resource limits must be the exact host-owned type or None")
    effective = ProjectLimits() if resource_limits is None else resource_limits
    # Reconstruct to revalidate even a frozen object modified with object.__setattr__.
    limits = asdict(ProjectLimits(**asdict(effective)))
    atomic = arm_projection(manifest, task_id, "atomic")
    flat = arm_projection(manifest, task_id, "flat")
    shared_keys = ("manifest_sha256", "task_id", "owner", "profile_sha256", "wire_config", "limits",
                   "arm_budget_usd", "task_budget_usd", "reserve_per_call_usd", "deadline_unix_ms",
                   "candidate_selection", "public_packet_sha256", "ordinary_admission_sha256")
    if any(atomic[key] != flat[key] for key in shared_keys):
        raise ValueError("frozen matched arms disagree on common declarations")
    data = {"schema_version": 1, "scope": "caller_declarations_only_no_admission",
            "common": {key: atomic[key] for key in shared_keys},
            "resource_limits": {"resolution": "default" if resource_limits is None else "explicit", "effective": limits},
            "callbacks": _callbacks(callbacks), "queue": _queue(queue)}
    return FrozenHostEnvelope(canonical_bytes(data), _factory=_FACTORY)


def validate_host_declaration(frozen, candidate):
    """Compare exact canonical declarations; it cannot inspect actual host state."""
    if type(frozen) is not FrozenHostEnvelope or type(candidate) is not dict:
        raise ValueError("frozen envelope and plain declaration mapping required")
    try:
        raw = canonical_bytes(candidate)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("candidate declaration is not canonical JSON data") from None
    if raw != frozen.canonical:
        raise ValueError("common host declaration differs from frozen bytes")
    return {"scope": "declaration_equality_only_no_admission", "envelope_sha256": frozen.sha256}
