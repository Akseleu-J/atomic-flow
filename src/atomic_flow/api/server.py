"""FastAPI endpoints for native clients and the OpenAI chat-completions shim."""

from __future__ import annotations

import hmac
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
        from starlette.responses import JSONResponse
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

    def run_answer(request: AnswerRequest) -> dict:
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
            if not slots.acquire(blocking=False):
                raise HTTPException(429, detail={"error": "busy", "message": "Too many concurrent requests."})
            try:
                backend = make_backend(config, request.backend, request.model)
                return Orchestrator(config, project, backend).answer(
                    request.query, mode=mode, budget_tokens=request.budget_tokens, role_prompts=request.role_prompts,
                    retrieval_query=request.retrieval_query).model_dump()
            finally:
                slots.release()
        except (AtomicFlowError, ValueError) as exc:
            status = next((code for cls, code in STATUS_FOR.items() if isinstance(exc, cls)), 400)
            raise HTTPException(status, detail={"error": type(exc).__name__.lower(), "message": str(exc)}) from exc

    @app.post("/v1/answer")
    def answer(request: AnswerRequest):
        return run_answer(request)

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


app = create_app()
