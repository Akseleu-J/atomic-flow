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

from atomic_flow.agents.effects import UnresolvedEffects
from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.runtime import ControlMessage, EventBuffer, RunControl

_RUN_SLOTS = threading.BoundedSemaphore(4)


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    code: str
    message: str
    request_outcome: Literal["not_sent", "refused", "completed", "unknown"] = "unknown"


class AnswerHandle:
    """Single-consumer event iterator plus independent retained result/terminal.

    Cancel/disconnect stops local waiting, never promises remote inference stop.
    This is a live handle, not a durable job or a replayable session.
    """
    def __init__(self, config, project: str | Path, backend, query: str, *, capacity=128,
                 approval_callback=None, question_callback=None, on_finished=None, workspace_session=None, lsp_client=None,
                 documentation_client=None, hooks=None, tool_registry=None, role_tools=None, **answer_options):
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
                                                question_callback=question_callback, workspace_session=workspace_session, lsp_client=lsp_client,
                                                documentation_client=documentation_client, hooks=hooks, tool_registry=tool_registry, role_tools=role_tools).answer(
                        query, request_id=self.run_id, **answer_options)
                    self.control.finish()
                    self.buffer.emit("run_completed", {"result": self._result.model_dump()}, outcome="success", terminal=True)
                except BaseException as exc:
                    self._exception = exc
                    cancelled = isinstance(exc, TaskCancelled)
                    outcome = getattr(exc, "request_outcome", "unknown")
                    message = ("Run cancelled" if cancelled else
                               "Tool effects unresolved; inspect external state before starting new work" if isinstance(exc, UnresolvedEffects) else
                               "Run failed; inspect local session diagnostics")
                    self.error = RunError(code=type(exc).__name__, message=message,
                                          request_outcome=outcome if outcome in {"not_sent", "refused", "completed", "unknown"} else "unknown")
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


def resume_answer(config, project: str | Path, backend, session_id: str, query: str, **options) -> AnswerHandle:
    """Start a fresh request from checked advisory state, never replay actions."""
    return start_answer(config, project, backend, query, resume_from=session_id, **options)


def _saved_task(task_id):
    from atomic_flow.agents.task import load
    from atomic_flow.agents.task_loop import TaskRunner
    from atomic_flow.config import load_config
    task = load(task_id)
    return task, TaskRunner(load_config(task.project), Path(task.project), None)


def review_task(task_id: str, *, allowed_paths: list[str] | None = None) -> dict:
    """Offline plan/scope digest; does not configure or call a model."""
    task, runner = _saved_task(task_id)
    return runner.review(task, allowed_paths=allowed_paths)


def approve_task(task_id: str, expected_digest: str, *, allowed_paths: list[str] | None = None):
    """Explicit host approval for exactly the reviewed digest, without execution."""
    task, runner = _saved_task(task_id)
    return runner.approve(task, expected_digest, allowed_paths=allowed_paths)


def revise_task(task_id: str, plan):
    from atomic_flow.agents.planner import Plan
    task, runner = _saved_task(task_id)
    parsed = plan if isinstance(plan, Plan) else Plan.model_validate(plan)
    return runner.revise(task, parsed)


def jobs(project: str | Path):
    """Offline project-bound durable job store, independent of live answer handles."""
    from atomic_flow.jobs import JobStore
    return JobStore(Path(project))


def connect_worker(project: str | Path, url: str, *, broker, grant_id: str, allow_loopback_http=False):
    """No network on construction; each operation resolves a scoped host grant."""
    from atomic_flow.remote import RemoteExecutor, ScopedWorkerTransport
    return RemoteExecutor(ScopedWorkerTransport(Path(project), url, broker, grant_id, allow_loopback_http))


def notebook(project: str | Path, path: str, **options):
    from atomic_flow.notebooks import NotebookDocument
    return NotebookDocument.load(Path(project), path, **options)


def workspace(workspace_id="default"):
    from atomic_flow.workspace import WorkspaceRegistry
    return WorkspaceRegistry(workspace_id)


def watch(project: str | Path, **options):
    from atomic_flow.watching import SourceWatcher
    return SourceWatcher(Path(project), **options)


def open_lsp(project: str | Path, command: list[str], *, approved=False, **options):
    """Explicit host executable approval; use as context manager to start/close."""
    from atomic_flow.lsp import LSPClient
    return LSPClient(Path(project), command, approved=approved, **options)


def documentation(allowed_hosts, *, approved=False, **options):
    from atomic_flow.documentation import DocumentationClient
    return DocumentationClient(allowed_hosts, approved=approved, **options)


def components():
    """Host-owned builtins, deferred until requested; no project import paths."""
    from importlib import import_module

    from atomic_flow.extensions import OptionalComponents
    catalog = OptionalComponents()
    for name, module, symbol in (("api", "atomic_flow.api.server", "create_app"),
        ("lsp", "atomic_flow.lsp", "LSPClient"), ("mcp", "atomic_flow.integrations.mcp", "MCPClient"),
        ("ide", "atomic_flow.integrations.ide", "create_ide_bridge"),
        ("documentation", "atomic_flow.documentation", "DocumentationClient"), ("workers", "atomic_flow.workers", "WorkerCoordinator")):
        catalog.register(name, lambda module=module, symbol=symbol: getattr(import_module(module), symbol), approved=True)
    return catalog


def worker_coordinator(project, parent_budget_backend, **options):
    from atomic_flow.workers import WorkerCoordinator
    return WorkerCoordinator(project, parent_budget_backend, **options)


def open_mcp(project, command, *, approved=False, **options):
    """Explicit trusted stdio process; context manager owns start and shutdown."""
    from atomic_flow.integrations.mcp import MCPClient
    return MCPClient(Path(project), command, approved=approved, **options)


def ide_bridge(project, *, token, allowed_origins, **options):
    """Return an unstarted, authenticated read-only loopback HTTP server."""
    from atomic_flow.integrations.ide import create_ide_bridge
    return create_ide_bridge(Path(project), token=token, allowed_origins=allowed_origins, **options)


def execute_workflow(definition, registry, context, *, expected_digest, approved=False, allowed_tools, role=""):
    from atomic_flow.workflows import run_workflow
    return run_workflow(definition, registry, context, allowed_tools=allowed_tools, role=role,
                        expected_digest=expected_digest, approved=approved)
