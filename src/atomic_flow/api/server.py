"""FastAPI endpoints for native clients and the OpenAI chat-completions shim."""

import asyncio
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atomic_flow.agents.effects import UnresolvedEffects
from atomic_flow.backends.router import make_backend
from atomic_flow.config import load_config
from atomic_flow.errors import AtomicFlowError, BackendError, BudgetExceeded, ConfigurationError, TreeError
from atomic_flow.llm.orchestrator import Orchestrator, choose_mode
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.runtime import ControlMessage

Mode = Literal["simple", "medium", "hard"]
PLACEHOLDER_KEYS = {"replace-with-a-long-random-value", "changeme", "secret", "password"}
STATUS_FOR = {UnresolvedEffects: 409, BackendError: 502, BudgetExceeded: 429, TreeError: 409, ConfigurationError: 400}
MAX_BODY_BYTES = 1_048_576
MAX_TEXT_BYTES = 200_000
Text = Annotated[str, Field(strict=True, max_length=50_000)]


class StrictRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class AnswerRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=50_000)
    project: str = Field(default=".", max_length=4096)
    mode: Mode | None = None
    auto_mode: bool | None = None
    budget_tokens: int | None = Field(default=None, ge=256, le=200_000)
    backend: str = Field(default="auto", max_length=128)
    model: str | None = Field(default=None, max_length=256)
    role_prompts: dict[Annotated[str, Field(max_length=64)], Text] = Field(default_factory=dict, max_length=16)
    acknowledge_default_prompts: bool = False
    retrieval_query: str | None = Field(default=None, max_length=50_000)
    resume_from: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    memory_ids: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list, max_length=10)
    memory_query: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def aggregate_text(self):
        texts = [self.query, self.retrieval_query or "", *self.role_prompts.values()]
        if sum(len(text.encode("utf-8")) for text in texts) > MAX_TEXT_BYTES:
            raise ValueError("aggregate query and role prompt byte limit exceeded")
        return self


class TextPart(StrictRequest):
    type: Literal["text"]
    text: Text


class ChatMessage(StrictRequest):
    role: Literal["system", "developer", "user", "assistant"]
    content: Text | Annotated[list[TextPart], Field(max_length=100)]

    def text(self) -> str:
        return self.content if isinstance(self.content, str) else "".join(p.text for p in self.content)

    @model_validator(mode="after")
    def bounded_message(self):
        if len(self.text()) > 50_000:
            raise ValueError("combined message text limit exceeded")
        return self


class ChatRequest(StrictRequest):
    model: str = Field(default="atomic-medium", max_length=256)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    stream: bool = False

    @model_validator(mode="after")
    def bounded_transcript(self):
        if sum(len(m.text().encode("utf-8")) for m in self.messages) > MAX_TEXT_BYTES:
            raise ValueError("aggregate conversation byte limit exceeded")
        return self


class BoundedBodyMiddleware:
    """Bound the received body before JSON decoding, including chunked requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse

        async def reject(status, code):
            await JSONResponse({"error": {"code": code}}, status_code=status)(scope, receive, send)

        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    length = int(value)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    return await reject(400, "invalid_content_length")
                if length > MAX_BODY_BYTES:
                    return await reject(413, "body_too_large")
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > MAX_BODY_BYTES:
                return await reject(413, "body_too_large")
            chunks.append(body)
            if not message.get("more_body", False):
                break
        buffered = b"".join(chunks)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)


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

    project_root = Path(os.getenv("ATOMIC_FLOW_PROJECT_ROOT", Path.cwd())).expanduser().resolve()
    for variable in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        if os.getenv(variable, "1") != "1":
            raise RuntimeError("The live-run API supports exactly one worker per project.")

    @asynccontextmanager
    async def lifespan(app):
        from atomic_flow.locking import exclusive_file
        with exclusive_file(project_root / ".atomic_flow" / "api-service.lock",
                            busy_message="An API worker already owns this project; multi-worker mode is unsupported."):
            app.state.ready = True
            try:
                yield
            finally:
                app.state.ready = False

    app = FastAPI(title="atomic_flow", version="0.2.0", lifespan=lifespan,
                  docs_url=None if server_key else "/docs",
                  redoc_url=None if server_key else "/redoc",
                  openapi_url=None if server_key else "/openapi.json")
    app.state.ready = False
    app.add_middleware(BoundedBodyMiddleware)

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Never echo submitted prompts, credentials or large rejected bodies.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]}
            for error in exc.errors()[:16]]})
    slots = threading.BoundedSemaphore(int(os.getenv("ATOMIC_FLOW_MAX_CONCURRENCY", "4")))
    runs = {}
    runs_lock = threading.Lock()
    streaming = set()

    def configured_project_root() -> Path:
        return project_root

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

    @app.get("/ready")
    def ready():
        return JSONResponse(status_code=200 if app.state.ready else 503,
                            content={"ready": app.state.ready, "ownership": "single_process", "durable_runs": False})

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
        turns = [(m.role, m.text()) for m in request.messages]
        last_user, history = split_conversation(turns)
        if last_user is None:
            raise HTTPException(400, detail={"error": "no_user_message", "message": "At least one user message is required."})
        query = f"Conversation so far:\n{history}\n\nCurrent question: {last_user}" if history else last_user
        if len(query) > 50_000 or len(query.encode("utf-8")) + len(last_user.encode("utf-8")) > MAX_TEXT_BYTES:
            raise HTTPException(422, detail={"error": "composed_query_too_large"})
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

