"""Version 1 local controls and bounded observations, never executable replay."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.sessions import _clean

PROTOCOL_VERSION = 1
MAX_EVENT_BYTES = 65536


class ControlMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    action: Literal["cancel", "steer"]
    text: str | None = Field(default=None, min_length=1, max_length=8000)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    run_id: str
    sequence: int
    kind: str
    timestamp: float
    call_id: str | None = None
    task_id: str | None = None
    outcome: str | None = None
    payload: dict = Field(default_factory=dict)
    dropped_before: int = 0


class RunControl:
    def __init__(self):
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._pending: deque[str] = deque()
        self._closed = False

    def submit(self, message: ControlMessage) -> None:
        with self._lock:
            if self._closed:
                raise ValueError("run has already finished")
            if message.action == "cancel":
                if message.text is not None:
                    raise ValueError("cancel does not accept text")
                self._cancel.set()
            else:
                if not message.text or not message.text.strip():
                    raise ValueError("steer requires nonempty text")
                if len(self._pending) >= 16:
                    raise ValueError("steering queue is full")
                self._pending.append(message.text)

    def check(self):
        if self._cancel.is_set():
            raise TaskCancelled("run cancellation requested")

    def drain(self) -> list[str]:
        with self._lock:
            self.check()
            messages = list(self._pending)
            self._pending.clear()
            return messages

    def boundary(self) -> bool:
        """Pending guidance invalidates a response. Later submissions apply after
        the current indivisible tool; approved arguments never change in place.
        """
        with self._lock:
            self.check()
            return bool(self._pending)

    def close(self):
        with self._lock:
            self._closed = True

    def finish(self):
        with self._lock:
            self.check()
            if self._pending:
                raise ValueError("run finished with unapplied steering")
            self._closed = True


class EventBuffer:
    def __init__(self, run_id: str | None = None, *, capacity: int = 128):
        if not 2 <= capacity <= 4096:
            raise ValueError("event capacity must be between 2 and 4096")
        self.run_id = run_id or "run_" + uuid.uuid4().hex
        self._capacity = capacity
        self._events: deque[RunEvent] = deque()
        self._condition = threading.Condition()
        self._sequence = self._dropped = 0
        self.terminal: RunEvent | None = None

    def emit(self, kind: str, payload: dict, *, call_id=None, task_id=None, outcome=None, terminal=False):
        safe = _clean(json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)))
        size = len(json.dumps(safe, ensure_ascii=False).encode())
        if size > MAX_EVENT_BYTES:
            safe = {"omitted": True, "reason": "event_payload_limit", "original_bytes": size}
        with self._condition:
            if self.terminal is not None:
                return
            self._sequence += 1
            if len(self._events) == self._capacity:
                self._events.popleft()
                self._dropped += 1
            event = RunEvent(run_id=self.run_id, sequence=self._sequence, kind=kind, timestamp=time.time(),
                             call_id=call_id, task_id=task_id, outcome=outcome, payload=safe)
            self._events.append(event)
            if terminal:
                self.terminal = event
            self._condition.notify_all()

    def next(self, timeout: float | None = None) -> RunEvent | None:
        with self._condition:
            self._condition.wait_for(lambda: self._events or self.terminal is not None, timeout)
            if not self._events:
                return None
            event = self._events.popleft()
            dropped, self._dropped = self._dropped, 0
            return event.model_copy(update={"dropped_before": dropped})
