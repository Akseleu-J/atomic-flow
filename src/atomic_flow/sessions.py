"""Bounded, redacted, append-only session observations, never executable replay.

A terminated line is a committed event. An interrupted final line is exposed as
untrusted tail bytes and blocks further appends until explicit recovery. A model
or tool request without a terminal event remains pending: reading never retries it.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atomic_flow.activation.redact import redact
from atomic_flow.config import user_dir
from atomic_flow.locking import exclusive_file
from atomic_flow.state import StateError

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 256 * 1024
MAX_SESSION_BYTES = 64 * 1024 * 1024
EVENT_TYPES = frozenset({
    "session_created", "user_input", "model_request", "model_response", "model_failed",
    "tool_request", "tool_result", "tool_effect_unknown", "question", "answer", "completed", "failed", "tail_recovered",
    "steering_applied", "response_discarded", "context_summary", "continuation",
    "dialogue_state", "snapshot_created", "rewind_applied", "fork_created",
})
_SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|authorization|api[_-]?key|(?:^|_)token(?:$|_))")


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("invalid session id: use 1-64 letters, digits, underscore or hyphen")
    return value


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        raise StateError("session payload nesting exceeds 16 levels")
    if isinstance(value, str):
        return redact(value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise StateError("session payload keys must be text")
        return {key: "[REDACTED]" if _SECRET_KEY.search(key) else _clean(item, depth + 1)
                for key, item in value.items()}
    raise StateError("session payload must contain finite JSON values only")


def _encode(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    events: list[dict]
    incomplete_tail: bool = False
    tail_bytes: int = 0

    @property
    def pending_call_ids(self) -> list[str]:
        pending = {}
        for event in self.events:
            call_id = event.get("call_id") or event["payload"].get("call_id")
            if call_id is None:
                continue
            if event["event_type"] in ("model_request", "tool_request"):
                pending[call_id] = True
            elif event["event_type"] in ("model_response", "model_failed", "tool_result"):
                pending.pop(call_id, None)
        return sorted(pending)

    @property
    def unresolved_call_ids(self) -> list[str]:
        """Closed transport observations do not establish settled charges/effects."""
        unresolved = set(self.pending_call_ids)
        for event in self.events:
            call_id = event.get("call_id") or event["payload"].get("call_id")
            if call_id is None:
                continue
            kind, outcome = event["event_type"], event.get("outcome")
            if kind == "tool_effect_unknown":
                unresolved.add(call_id)
            if kind == "model_failed" and outcome not in {"not_sent", "refused"}:
                unresolved.add(call_id)
            if kind == "model_response":
                cost = event["payload"].get("cost_usd")
                if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                    unresolved.add(call_id)
            if kind == "tool_result":
                payload = event["payload"]
                call = payload.get("call")
                compact = call.get("result_summary") if isinstance(call, dict) else None
                results = [item for item in (payload.get("result"), compact) if isinstance(item, dict)]
                uncertain_effects = any(
                    item.get("status") == "rollback_failed"
                    or (item.get("side_effects_may_have_occurred") is not None
                        and item.get("side_effects_may_have_occurred") is not False)
                    or item.get("outcome") in ("unknown", "pending")
                    for item in results
                )
                if outcome in {"unknown", "pending"} or uncertain_effects:
                    unresolved.add(call_id)
        return sorted(unresolved)


class SessionJournal:
    def __init__(self, session_id: str):
        self.session_id = _id(session_id)
        # Fixed prefix also avoids Windows reserved device names, e.g. CON.
        self.path = user_dir() / "sessions" / f"session_{self.session_id}.jsonl"

    @classmethod
    def create(cls, project: Path, session_id: str | None = None, *, metadata: dict | None = None) -> SessionJournal:
        journal = cls(session_id or uuid.uuid4().hex)
        journal.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with journal._lease():
            # Request IDs cannot silently merge unrelated runs, even completed ones.
            event = journal._event(1, "session_created", {"project": str(project.resolve()), "metadata": metadata or {}})
            with journal.path.open("xb") as handle:
                if os.name != "nt":
                    os.chmod(journal.path, 0o600)
                handle.write(_encode(event))
                handle.flush()
                os.fsync(handle.fileno())
        return journal

    @classmethod
    def open(cls, session_id: str) -> SessionJournal:
        journal = cls(session_id)
        journal.read()  # Validate committed records without modifying any bytes.
        return journal

    def _lease(self):
        return exclusive_file(self.path.with_suffix(".lock"), busy_message="session is being updated by another process")

    def _event(self, sequence: int, event_type: str, payload: dict, call_id: str | None = None,
               outcome: str | None = None) -> dict:
        if event_type not in EVENT_TYPES:
            raise StateError(f"unsupported session event type: {event_type}")
        if not isinstance(payload, dict):
            raise StateError("session event payload must be an object")
        if "call_id" in payload and (not isinstance(payload["call_id"], str)
                                      or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", payload["call_id"])):
            raise StateError("invalid session payload call_id")
        if call_id is not None and (not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", call_id)):
            raise StateError("invalid session call_id")
        if outcome is not None and outcome not in ("success", "error", "denied", "cancelled", "unknown", "pending", "refused", "not_sent"):
            raise StateError("invalid session outcome")
        event = {"schema_version": SCHEMA_VERSION, "session_id": self.session_id,
                 "event_id": uuid.uuid4().hex, "sequence": sequence, "timestamp": time.time(),
                 "event_type": event_type, "payload": _clean(payload)}
        if call_id is not None:
            event["call_id"] = call_id
        if outcome is not None:
            event["outcome"] = outcome
        if len(_encode(event)) > MAX_EVENT_BYTES:
            raise StateError(f"session event exceeds {MAX_EVENT_BYTES} bytes; store a bounded summary/reference")
        return event

    def append(self, event_type: str, payload: dict, *, call_id: str | None = None, outcome: str | None = None,
               expected_sequence: int | None = None) -> dict:
        with self._lease():
            snapshot = self.read()
            if expected_sequence is not None and len(snapshot.events) != expected_sequence:
                raise StateError("session changed before host checkpoint; inspect the latest observations")
            if snapshot.incomplete_tail:
                raise StateError("session has an interrupted tail; inspect it and call recover_tail() before appending; never replay pending calls")
            event = self._event(len(snapshot.events) + 1, event_type, payload, call_id, outcome)
            encoded = _encode(event)
            if self.path.stat().st_size + len(encoded) > MAX_SESSION_BYTES:
                raise StateError("session exceeds 64 MiB; start a new session")
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        return event

    def read(self) -> SessionSnapshot:
        if self.path.stat().st_size > MAX_SESSION_BYTES:
            raise StateError("session exceeds 64 MiB; inspect it offline")
        raw = self.path.read_bytes()
        complete_end = raw.rfind(b"\n") + 1
        events = []
        for sequence, line in enumerate(raw[:complete_end].splitlines(), 1):
            if len(line) + 1 > MAX_EVENT_BYTES:
                raise StateError(f"session event {sequence} exceeds size limit")
            try:
                event = json.loads(line)
                self._validate_event(event, sequence)
            except (ValueError, TypeError, UnicodeError, KeyError, AttributeError) as exc:
                raise StateError(f"invalid committed session event {sequence}; unsupported schema or corrupt data: update atomic-flow or restore known-good journal, do not replay actions") from exc
            events.append(event)
        if not events or events[0]["event_type"] != "session_created":
            raise StateError("session has no committed creation event")
        return SessionSnapshot(self.session_id, events, complete_end != len(raw), len(raw) - complete_end)

    def _validate_event(self, event: dict, sequence: int):
        if not isinstance(event, dict):
            raise StateError("expected event object")
        version = event.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise StateError("unsupported session schema version; update atomic-flow before opening")
        if event.get("session_id") != self.session_id or type(event.get("sequence")) is not int or event["sequence"] != sequence:
            raise StateError("session identity/sequence mismatch")
        if not isinstance(event.get("event_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", event["event_id"]):
            raise StateError("invalid event id")
        timestamp = event.get("timestamp")
        if type(timestamp) not in (float, int) or not math.isfinite(timestamp) or timestamp < 0:
            raise StateError("invalid event timestamp")
        # Same payload and metadata validation on writes and reads. This method
        # validates persisted data; it does not silently sanitize corrupted bytes.
        checked = self._event(sequence, event["event_type"], event["payload"], event.get("call_id"), event.get("outcome"))
        if checked["payload"] != event["payload"]:
            raise StateError("unredacted session payload")

    def recover_tail(self) -> Path | None:
        """Preserve and remove only uncommitted tail bytes; do not replay effects."""
        with self._lease():
            snapshot = self.read()
            if not snapshot.incomplete_tail:
                return None
            raw = self.path.read_bytes()
            split = len(raw) - snapshot.tail_bytes
            backup = self.path.with_suffix(f".interrupted-{uuid.uuid4().hex}.tail")
            with backup.open("xb") as handle:
                handle.write(raw[split:])
                handle.flush()
                os.fsync(handle.fileno())
            with self.path.open("r+b") as handle:
                handle.truncate(split)
                handle.flush()
                os.fsync(handle.fileno())
            event = self._event(len(snapshot.events) + 1, "tail_recovered", {"discarded_bytes": snapshot.tail_bytes, "backup": backup.name})
            with self.path.open("ab") as handle:
                handle.write(_encode(event))
                handle.flush()
                os.fsync(handle.fileno())
            return backup

    def export(self, path: Path) -> Path:
        snapshot = self.read()
        destination = Path(path)
        if destination.resolve() == self.path.resolve():
            raise StateError("export destination must differ from journal")
        data = {"schema_version": SCHEMA_VERSION, "session_id": self.session_id,
                "events": snapshot.events, "incomplete_tail": snapshot.incomplete_tail,
                "tail_bytes": snapshot.tail_bytes, "pending_call_ids": snapshot.pending_call_ids,
                "unresolved_call_ids": snapshot.unresolved_call_ids,
                "replay_supported": False}
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        return destination


def list_sessions(*, limit: int = 100) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("session list limit must be 1-1000")
    directory = user_dir() / "sessions"
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("session_*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        session_id = path.name[len("session_"):-len(".jsonl")]
        try:
            journal = SessionJournal(session_id)
            with path.open("rb") as handle:
                line = handle.readline(MAX_EVENT_BYTES + 1)
            if not line.endswith(b"\n") or len(line) > MAX_EVENT_BYTES:
                raise StateError("invalid creation record")
            first = json.loads(line)
            journal._validate_event(first, 1)
            if first["event_type"] != "session_created":
                raise StateError("missing creation record")
            out.append({"session_id": session_id, "created_at": first["timestamp"], "project": first["payload"]["project"]})
        except (ValueError, OSError, KeyError, UnicodeError):
            out.append({"session_id": session_id, "error": "invalid session; inspect before opening"})
    return out
