"""Process-only credential grants for trusted host connector implementations.

This is an operation gate, not a sandbox for Python callbacks. A trusted connector
may use a value inside ``use``; no capability or value is given to model tools,
subprocess environments, prompts or durable grant records. A malicious host
callback can still copy/encode/exfiltrate a revealed value and must not be loaded.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class CredentialDenied(ValueError):
    """Grant is missing, revoked, expired or outside the requested scope."""


class CredentialUseError(RuntimeError):
    """A resolver/connector failed; details are suppressed to protect its value."""


def _label(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
        raise CredentialDenied("Invalid exact connector/operation name")
    return value


@dataclass(frozen=True)
class CredentialGrant:
    id: str
    project: str
    connector: str
    operations: tuple[str, ...]
    env_name: str
    expires_at: float

    def metadata(self) -> dict:
        """Only non-secret routing and authority metadata; never a resolved value."""
        return asdict(self)


class CredentialValue:
    """Short-lived value view. Do not retain the revealed string in connector code."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value: str | None = value

    def __repr__(self) -> str:
        return "CredentialValue(<redacted>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Credential values cannot be serialized")

    def reveal(self) -> str:
        if self._value is None:
            raise CredentialDenied("Credential view is no longer active")
        return self._value

    def _close(self) -> None:
        self._value = None


def _checked_result(result: Any, secret: str) -> Any:
    """Return plain bounded JSON data, refusing accidentally returned raw secrets."""
    remaining = 10000

    def visit(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise CredentialUseError("Connector result exceeds data limits")
        kind = type(value)
        if kind is str:
            if secret in value:
                raise CredentialUseError("Connector result contains credential material")
        elif kind in (type(None), bool, int):
            return
        elif kind is float:
            if not math.isfinite(value):
                raise CredentialUseError("Connector result must be finite JSON data")
        elif kind is list:
            for entry in value:
                visit(entry, depth + 1)
        elif kind is dict:
            for key, entry in value.items():
                if type(key) is not str:
                    raise CredentialUseError("Connector result keys must be strings")
                visit(key, depth + 1)
                visit(entry, depth + 1)
        else:
            raise CredentialUseError("Connector result must be plain JSON data")

    visit(result)
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise CredentialUseError("Connector result exceeds data limits")
        # Detach results from callback-owned mutable containers.
        return json.loads(encoded)
    except (ValueError, TypeError, OverflowError):
        raise CredentialUseError("Connector result must be bounded JSON data") from None


class CredentialBroker:
    """Volatile host grants; process restart revokes all authority by construction.

    Scopes are exact connector + operation + canonical project, checked before
    resolution and again immediately before callback entry. Revocation cannot
    reverse an operation already started by a trusted connector. Expiry uses the
    host clock; trusted callers control clock/resolver injection for tests/vaults.
    """

    def __init__(self, *, resolver: Callable[[str], str | None] | None = None,
                 clock: Callable[[], float] = time.time):
        self._resolver = resolver or os.environ.get
        self._clock = clock
        self._grants: dict[str, CredentialGrant] = {}
        self._revoked: set[str] = set()
        self._lock = threading.RLock()

    def __reduce_ex__(self, protocol):
        raise TypeError("Credential brokers cannot be serialized")

    def grant(self, *, project: str | Path, connector: str, operations: Iterable[str],
              env_name: str, approved: bool = False, ttl_seconds: float = 300) -> CredentialGrant:
        if approved is not True:
            raise CredentialDenied("Credential grant needs explicit host approval")
        connector = _label(connector)
        if isinstance(operations, str):
            raise CredentialDenied("Operations must be an explicit collection")
        operations = tuple(sorted({_label(operation) for operation in operations}))
        if not operations or len(operations) > 32:
            raise CredentialDenied("Grant requires 1 to 32 exact operations")
        if not isinstance(env_name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env_name) is None:
            raise CredentialDenied("Invalid credential variable name")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or not 0 < ttl_seconds <= 3600:
            raise CredentialDenied("Grant lifetime must be positive and at most one hour")
        canonical_project = str(Path(project).expanduser().resolve())
        with self._lock:
            now = self._clock()
            self._grants = {key: grant for key, grant in self._grants.items()
                            if grant.expires_at > now and key not in self._revoked}
            self._revoked.intersection_update(self._grants)
            if len(self._grants) >= 128:
                raise CredentialDenied("Too many active credential grants")
            grant = CredentialGrant("credential_" + uuid.uuid4().hex, canonical_project,
                                    connector, operations, env_name, now + ttl_seconds)
            self._grants[grant.id] = grant
            return grant

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            if grant_id not in self._grants:
                raise CredentialDenied("Unknown credential grant")
            self._revoked.add(grant_id)

    def _scope(self, grant_id: str, project: str, connector: str, operation: str) -> CredentialGrant:
        with self._lock:
            grant = self._grants.get(grant_id)
            if (grant is None or grant_id in self._revoked or self._clock() >= grant.expires_at
                    or grant.project != project or grant.connector != connector or operation not in grant.operations):
                raise CredentialDenied("Credential grant is unavailable for this operation/project")
            return grant

    def use(self, grant_id: str, *, project: str | Path, connector: str, operation: str,
            consumer: Callable[[CredentialValue], Any]) -> Any:
        project = str(Path(project).expanduser().resolve())
        grant = self._scope(grant_id, project, _label(connector), _label(operation))
        try:
            secret = self._resolver(grant.env_name)
        except Exception:
            raise CredentialUseError("Credential resolution failed") from None
        if type(secret) is not str or not secret or len(secret) > 65536:
            raise CredentialUseError("Credential is unavailable or invalid")
        self._scope(grant_id, project, connector, operation)
        view = CredentialValue(secret)
        try:
            try:
                result = consumer(view)
            except Exception:
                raise CredentialUseError("Credential operation failed; details suppressed") from None
            return _checked_result(result, secret)
        finally:
            view._close()
