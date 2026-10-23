"""FastAPI endpoints for native clients and the OpenAI chat-completions shim."""

import asyncio
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from atomic_flow.backends.router import make_backend
from atomic_flow.config import load_config
from atomic_flow.errors import AtomicFlowError, BackendError, BudgetExceeded, ConfigurationError, TreeError
from atomic_flow.llm.orchestrator import Orchestrator, choose_mode
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.runtime import ControlMessage

Mode = Literal["simple", "medium", "hard"]
PLACEHOLDER_KEYS = {"replace-with-a-long-random-value", "changeme", "secret", "password"}
STATUS_FOR = {BackendError: 502, BudgetExceeded: 429, TreeError: 409, ConfigurationError: 400}


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=50_000)
    project: str = Field(default=".", max_length=4096)
    mode: Mode | None = None
    auto_mode: bool | None = None
    budget_tokens: int | None = Field(default=None, ge=256, le=200_000)
    backend: str = "auto"
    model: str | None = None
    role_prompts: dict[str, str] = Field(default_factory=dict)
    acknowledge_default_prompts: bool = False
    retrieval_query: str | None = Field(default=None, max_length=50_000)
    resume_from: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    memory_ids: list[str] = Field(default_factory=list, max_length=10)
    memory_query: str | None = Field(default=None, min_length=1, max_length=1000)


class ChatRequest(BaseModel):
    model: str = "atomic-medium"
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    stream: bool = False


def _text(content: Any) -> str:
    if isinstance(content, list):                              # OpenAI "content parts"
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def split_conversation(turns: list[tuple[str, str]]) -> tuple[str | None, str]:
    """Return (last user message, history before it)."""
    idx = max((i for i, (role, text) in enumerate(turns) if role == "user" and text.strip()), default=None)
    if idx is None:
        return None, ""
    history = "\n".join(f"{role.upper()}: {text}" for role, text in turns[max(0, idx - 6):idx])
    return turns[idx][1], history


def create_app():
    try:
        from fastapi import FastAPI, HTTPException, Request
        from starlette.responses import JSONResponse, StreamingResponse
    except ImportError as exc:
        raise RuntimeError("Install the API extra with `pip install atomic-flow[api]`.") from exc

    server_key = os.getenv("ATOMIC_FLOW_SERVER_API_KEY", "")
    if server_key and (len(server_key) < 16 or server_key.lower() in PLACEHOLDER_KEYS):
        raise RuntimeError("ATOMIC_FLOW_SERVER_API_KEY is a placeholder or shorter than 16 characters.")

    app = FastAPI(title="atomic_flow", version="0.2.0",
                  docs_url=None if server_key else "/docs",
                  redoc_url=None if server_key else "/redoc",
                  openapi_url=None if server_key else "/openapi.json")
    slots = threading.BoundedSemaphore(int(os.getenv("ATOMIC_FLOW_MAX_CONCURRENCY", "4")))
    runs = {}
    runs_lock = threading.Lock()
    streaming = set()

    def configured_project_root() -> Path:
        return Path(os.getenv("ATOMIC_FLOW_PROJECT_ROOT", Path.cwd())).expanduser().resolve()

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
            expected = os.getenv("ATOMIC_FLOW_SERVER_API_KEY", "")
            if expected:
                supplied = request.headers.get("authorization", "")
                supplied = supplied[7:].strip() if supplied.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
                # bytes: compare_digest raises TypeError (HTTP 500) on non-ASCII str input
                if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
                    return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "A valid API key is required."}},
                                        headers={"WWW-Authenticate": "Bearer"})
            elif os.getenv("ATOMIC_FLOW_REQUIRE_API_KEY", "").lower() in {"1", "true", "yes"}:
                return JSONResponse(status_code=503, content={"error": {"code": "server_misconfigured", "message": "API authentication is required but no server key is configured."}})
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "atomic_flow"}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": f"atomic-{m}", "object": "model", "owned_by": "atomic_flow"} for m in ("simple", "medium", "hard")]}

    def prepare(request: AnswerRequest):
        project = Path(request.project).expanduser().resolve()
        if not project.is_relative_to(configured_project_root()):
            raise HTTPException(403, detail={"error": "project_outside_root", "message": "Project must be inside ATOMIC_FLOW_PROJECT_ROOT."})
        if not project.is_dir():
            raise HTTPException(400, detail={"error": "invalid_project", "message": f"Project directory does not exist: {project}"})
        try:
            config = load_config(project)
            mode = choose_mode(request.query, config.mode, explicit_mode=request.mode,
                               auto_mode=config.auto_mode if request.auto_mode is None else request.auto_mode)
            prompts = PromptRegistry(project, request.role_prompts)
            if mode == "hard" and not request.acknowledge_default_prompts and any(
                    prompts.get(r) and prompts.sources[r] == "builtin" for r in prompts.roles_for_mode(mode)):
                raise HTTPException(409, detail={"error": "confirmation_required",
                                                 "message": "Hard mode with built-in prompts requires explicit acknowledgment.",
                                                 "action": "Set acknowledge_default_prompts=true or provide custom prompts for every role.",
                                                 "max_calls": 2 + config.max_attempts * 3})
            return project, config, mode
        except (AtomicFlowError, ValueError) as exc:
            status = next((code for cls, code in STATUS_FOR.items() if isinstance(exc, cls)), 400)
            raise HTTPException(status, detail={"error": type(exc).__name__.lower(), "message": str(exc)}) from exc

    def run_answer(request: AnswerRequest) -> dict:
        project, config, mode = prepare(request)
        try:
            if not slots.acquire(blocking=False):
                raise HTTPException(429, detail={"error": "busy", "message": "Too many concurrent requests."})
            try:
                backend = make_backend(config, request.backend, request.model)
                return Orchestrator(config, project, backend).answer(
                    request.query, mode=mode, budget_tokens=request.budget_tokens, role_prompts=request.role_prompts,
                    retrieval_query=request.retrieval_query, resume_from=request.resume_from,
                    memory_ids=request.memory_ids, memory_query=request.memory_query).model_dump()
            finally:
                slots.release()
        except (AtomicFlowError, ValueError) as exc:
            status = next((code for cls, code in STATUS_FOR.items() if isinstance(exc, cls)), 400)
            raise HTTPException(status, detail={"error": type(exc).__name__.lower(), "message": str(exc)}) from exc

    @app.post("/v1/answer")
    def answer(request: AnswerRequest):
        return run_answer(request)

    def find_run(run_id):
        with runs_lock:
            handle = runs.get(run_id)
            if handle is None or (handle.finished_at is not None and time.monotonic() - handle.finished_at > 600):
                runs.pop(run_id, None)
                raise HTTPException(404, detail={"error": "run_not_found"})
            return handle

    @app.post("/v1/runs", status_code=202)
    def start_run(request: AnswerRequest):
        from atomic_flow.sdk import start_answer
        project, config, mode = prepare(request)
        with runs_lock:
            expired = [key for key, value in runs.items() if value.finished_at is not None
                       and time.monotonic() - value.finished_at > 600]
            for key in expired:
                runs.pop(key)
            if len(runs) >= 32:
                oldest = next((key for key, value in runs.items() if value.done and key not in streaming), None)
                if oldest:
                    runs.pop(oldest)
                else:
                    raise HTTPException(429, detail={"error": "run_retention_full"})
            if not slots.acquire(blocking=False):
                raise HTTPException(429, detail={"error": "busy"})
            try:
                backend = make_backend(config, request.backend, request.model)
                handle = start_answer(config, project, backend, request.query, mode=mode,
                                      budget_tokens=request.budget_tokens, role_prompts=request.role_prompts,
                                      retrieval_query=request.retrieval_query, on_finished=slots.release,
                                      resume_from=request.resume_from, memory_ids=request.memory_ids,
                                      memory_query=request.memory_query)
            except Exception as exc:
                slots.release()
                raise HTTPException(400, detail={"error": type(exc).__name__.lower()}) from exc
            runs[handle.run_id] = handle
        return {"version": 1, "run_id": handle.run_id, "events_url": f"/v1/runs/{handle.run_id}/events",
                "control_url": f"/v1/runs/{handle.run_id}/control", "result_url": f"/v1/runs/{handle.run_id}"}

    @app.post("/v1/runs/{run_id}/control")
    def control_run(run_id: str, message: ControlMessage):
        handle = find_run(run_id)
        try:
            handle.control.submit(message)
        except ValueError as exc:
            raise HTTPException(409, detail={"error": "control_rejected", "message": str(exc)}) from exc
        return {"version": 1, "run_id": run_id, "status": "accepted", "applied": False}

    @app.get("/v1/runs/{run_id}")
    def run_result(run_id: str):
        from atomic_flow.sessions import _clean
        handle = find_run(run_id)
        if not handle.done:
            return JSONResponse(status_code=202, content={"version": 1, "run_id": run_id, "status": "running"})
        if handle.error:
            return {"version": 1, "run_id": run_id, "error": handle.error.model_dump()}
        return {"version": 1, "run_id": run_id, "result": _clean(handle.result().model_dump())}

    @app.get("/v1/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request):
        handle = find_run(run_id)
        with runs_lock:
            if run_id in streaming:
                raise HTTPException(409, detail={"error": "single_event_consumer"})
            streaming.add(run_id)

        async def events():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    event = await asyncio.to_thread(handle.buffer.next, 0.1)
                    if event is None:
                        if handle.buffer.terminal is not None:
                            # A reconnect after consumption gets the retained
                            # terminal, never replays model or tool calls.
                            event = handle.buffer.terminal
                        else:
                            yield ": keepalive\n\n"
                            continue
                    yield "id: " + str(event.sequence) + "\nevent: " + event.kind + "\ndata: " + json.dumps(
                        event.model_dump(), ensure_ascii=False) + "\n\n"
                    if event.kind in {"run_completed", "run_cancelled", "run_failed"}:
                        break
            finally:
                with runs_lock:
                    streaming.discard(run_id)
                if not handle.done:
                    try:
                        handle.cancel()
                    except ValueError:
                        pass  # completion raced disconnect

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/v1/chat/completions")
    def chat(request: ChatRequest):
        if request.stream:
            raise HTTPException(400, detail={"error": "streaming_not_supported", "message": "Streaming is not available in this release."})
        turns = [(str(m.get("role", "user")), _text(m.get("content"))) for m in request.messages
                 if m.get("role") in {"system", "developer", "user", "assistant"}]
        last_user, history = split_conversation(turns)
        if last_user is None:
            raise HTTPException(400, detail={"error": "no_user_message", "message": "At least one user message is required."})
        query = f"Conversation so far:\n{history}\n\nCurrent question: {last_user}" if history else last_user
        mode: Mode = "hard" if request.model.endswith("hard") else "simple" if request.model.endswith("simple") else "medium"
        # Retrieval uses ONLY the last user turn: BM25 over the whole transcript drowns the real question.
        data = run_answer(AnswerRequest(query=query, retrieval_query=last_user, project=str(configured_project_root()),
                                        mode=mode, acknowledge_default_prompts=True))   # picking atomic-hard IS the acknowledgment
        return {"id": data.get("ledger_id", "atomic-flow"), "object": "chat.completion", "created": int(time.time()),
                "model": data.get("model_used", request.model),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": data.get("text", "")}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": data.get("tokens_in", 0), "completion_tokens": data.get("tokens_out", 0),
                          "total_tokens": data.get("tokens_in", 0) + data.get("tokens_out", 0)}}

    return app


def __getattr__(name):
    if name == "app":
        return create_app()
    raise AttributeError(name)

