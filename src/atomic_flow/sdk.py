"""Small in-process SDK. No API dependencies or implicit network/model selection.

Only built-in HTTP adapters support interruptible local waiting. A host-supplied
backend must cooperate with cancellation; arbitrary Python cannot be preempted.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.runtime import ControlMessage, EventBuffer, RunControl

_RUN_SLOTS = threading.BoundedSemaphore(4)


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    code: str
    message: str
    request_outcome: Literal["not_sent", "refused", "unknown"] = "unknown"


class AnswerHandle:
    """Single-consumer event iterator plus independent retained result/terminal.

    Cancel/disconnect stops local waiting, never promises remote inference stop.
    This is a live handle, not a durable job or a replayable session.
    """
    def __init__(self, config, project: str | Path, backend, query: str, *, capacity=128,
                 approval_callback=None, question_callback=None, on_finished=None, **answer_options):
        if not _RUN_SLOTS.acquire(blocking=False):
            raise ValueError("SDK run capacity reached")
        try:
            self.buffer = EventBuffer(capacity=capacity)
            self.control = RunControl()
            self.run_id = self.buffer.run_id
            self._done = threading.Event()
            self._consumer = threading.Lock()
            self._result = self._exception = self.error = None
            self.finished_at = None

            def work():
                try:
                    from atomic_flow.llm.orchestrator import Orchestrator
                    self.buffer.emit("run_started", {"project": str(Path(project).resolve())})
                    self._result = Orchestrator(config, project, backend, control=self.control,
                                                event_sink=self.buffer.emit, approval_callback=approval_callback,
                                                question_callback=question_callback).answer(
                        query, request_id=self.run_id, **answer_options)
                    self.control.finish()
                    self.buffer.emit("run_completed", {"result": self._result.model_dump()}, outcome="success", terminal=True)
                except BaseException as exc:
                    self._exception = exc
                    cancelled = isinstance(exc, TaskCancelled)
                    outcome = getattr(exc, "request_outcome", "unknown")
                    self.error = RunError(code=type(exc).__name__, message="Run cancelled" if cancelled else "Run failed; inspect local session diagnostics",
                                          request_outcome=outcome if outcome in {"not_sent", "refused", "unknown"} else "unknown")
                    self.buffer.emit("run_cancelled" if cancelled else "run_failed", {"error": self.error.model_dump()},
                                     outcome="cancelled" if cancelled else "error", terminal=True)
                finally:
                    self.control.close()
                    self.finished_at = time.monotonic()
                    self._done.set()
                    _RUN_SLOTS.release()
                    if on_finished:
                        on_finished()

            self._thread = threading.Thread(target=work, daemon=True, name="atomic-answer")
            self._thread.start()
        except BaseException:
            _RUN_SLOTS.release()
            raise

    @property
    def done(self):
        return self._done.is_set()

    def cancel(self):
        self.control.submit(ControlMessage(action="cancel"))

    def steer(self, text: str):
        self.control.submit(ControlMessage(action="steer", text=text))

    def events(self):
        if not self._consumer.acquire(blocking=False):
            raise ValueError("only one event consumer is supported")
        try:
            while True:
                event = self.buffer.next()
                if event is None:
                    return
                yield event
                if event.kind in {"run_completed", "run_failed", "run_cancelled"}:
                    return
        finally:
            self._consumer.release()

    def result(self, timeout: float | None = None):
        if not self._done.wait(timeout):
            raise TimeoutError("run has not completed")
        if self._exception is not None:
            raise self._exception
        return self._result


def start_answer(config, project: str | Path, backend, query: str, **options) -> AnswerHandle:
    return AnswerHandle(config, project, backend, query, **options)
