"""FastAPI endpoints for native clients and the OpenAI chat-completions shim."""

from __future__ import annotations

from pathlib import Path
import hmac
import os
from typing import Any

from pydantic import BaseModel, Field

from atomic_flow.backends.router import make_backend
from atomic_flow.config import load_config
from atomic_flow.errors import AtomicFlowError, BackendError
from atomic_flow.llm.orchestrator import Orchestrator, choose_mode
from atomic_flow.llm.prompts import PromptRegistry


class AnswerRequest(BaseModel):
    query: str = Field(min_length=1, max_length=50_000)
    project: str = Field(default=".", max_length=4096)
    mode: str | None = None
    auto_mode: bool | None = None
    budget_tokens: int | None = Field(default=None, ge=256, le=200_000)
    backend: str = "auto"
    model: str | None = None
    role_prompts: dict[str, str] = Field(default_factory=dict)
    acknowledge_default_prompts: bool = False


class ChatRequest(BaseModel):
    model: str = "atomic-medium"
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    stream: bool = False


def create_app():
    try:
        from fastapi import FastAPI, HTTPException, Request
    except ImportError as exc:
        raise RuntimeError("Install the API extra with `pip install atomic-flow[api]`.") from exc

    app = FastAPI(title="atomic_flow", version="0.1.0")

    def configured_project_root() -> Path:
        return Path(os.getenv("ATOMIC_FLOW_PROJECT_ROOT", Path.cwd())).expanduser().resolve()

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        # Keep the health probe public; protect every endpoint that can trigger inference.
        if request.url.path.startswith("/v1/"):
            expected = os.getenv("ATOMIC_FLOW_SERVER_API_KEY", "")
            if expected:
                supplied = request.headers.get("authorization", "")
                if supplied.lower().startswith("bearer "):
                    supplied = supplied[7:].strip()
                else:
                    supplied = request.headers.get("x-api-key", "")
                if not hmac.compare_digest(supplied, expected):
                    from starlette.responses import JSONResponse
                    return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "A valid API key is required."}}, headers={"WWW-Authenticate": "Bearer"})
            elif os.getenv("ATOMIC_FLOW_REQUIRE_API_KEY", "").lower() in {"1", "true", "yes"}:
                from starlette.responses import JSONResponse
                return JSONResponse(status_code=503, content={"error": {"code": "server_misconfigured", "message": "API authentication is required but no server key is configured."}})
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "atomic_flow"}

    @app.post("/v1/answer")
    def answer(request: AnswerRequest):
        project = Path(request.project).expanduser().resolve()
        root = configured_project_root()
        if not project.is_relative_to(root):
            raise HTTPException(403, detail={"error": "project_outside_root", "message": "Project must be inside ATOMIC_FLOW_PROJECT_ROOT."})
        config = load_config(project)
        mode = choose_mode(request.query, config.mode, explicit_mode=request.mode,
                           auto_mode=config.auto_mode if request.auto_mode is None else request.auto_mode)
        if not project.is_dir():
            raise HTTPException(400, detail={"error": "invalid_project", "message": f"Project directory does not exist: {project}"})
        prompts = PromptRegistry(project, request.role_prompts)
        used_roles = prompts.roles_for_mode(mode) if mode in {"simple", "medium", "hard"} else []
        if mode == "hard" and not request.acknowledge_default_prompts and any(prompts.get(role) and prompts.sources[role] == "builtin" for role in used_roles):
            raise HTTPException(409, detail={"error": "confirmation_required", "message": "Hard mode with built-in prompts requires explicit acknowledgment.", "action": "Set acknowledge_default_prompts=true or provide custom prompts for every role.", "estimated_calls": config.max_attempts * 3 + 2})
        try:
            backend = make_backend(config, request.backend, request.model)
            result = Orchestrator(config, project, backend).answer(request.query, mode=mode, budget_tokens=request.budget_tokens, role_prompts=request.role_prompts)
            return result.model_dump()
        except (AtomicFlowError, ValueError) as exc:
            raise HTTPException(502 if isinstance(exc, BackendError) else 400, detail={"error": type(exc).__name__.lower(), "message": str(exc)}) from exc

    @app.post("/v1/chat/completions")
    def chat(request: ChatRequest):
        if request.stream:
            raise HTTPException(400, detail={"error": "streaming_not_supported", "message": "Streaming is not available in this release."})
        query = "\n\n".join(f"{item.get('role', 'user').upper()}: {item.get('content', '')}" for item in request.messages if item.get("role") in {"system", "developer", "user", "assistant"})
        mode = "hard" if request.model.endswith("hard") else "simple" if request.model.endswith("simple") else "medium"
        result = answer(AnswerRequest(query=query, project=str(configured_project_root()), mode=mode, acknowledge_default_prompts=(mode != "hard")))
        data = result if isinstance(result, dict) else result
        content = data.get("text", "")
        return {"id": data.get("ledger_id", "atomic-flow"), "object": "chat.completion", "created": 0,
                "model": data.get("model_used", request.model), "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": data.get("tokens_in", 0), "completion_tokens": data.get("tokens_out", 0), "total_tokens": data.get("tokens_in", 0) + data.get("tokens_out", 0)}}

    return app


app = create_app()
