"""Shared backend contract: result type, Protocol, cost estimate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from atomic_flow.config import BackendConfig


@dataclass(frozen=True)
class NativeToolCall:
    id: str
    name: str
    arguments: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not 1 <= len(self.id) <= 200 or not isinstance(self.name, str) or not 1 <= len(self.name) <= 100:
            raise ValueError("Invalid native tool call identity")
        if not isinstance(self.arguments, str) or len(self.arguments.encode()) > 1_000_000:
            raise ValueError("Native tool arguments must be bounded JSON text")

    def wire(self) -> dict:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class ChatResult:
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    truncated: bool = False          # provider stopped on the output-token limit -> JSON is probably cut
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_source: str = "unspecified"
    tool_calls: list[NativeToolCall] = field(default_factory=list)


def backend_capabilities(config: BackendConfig) -> dict:
    """Describe implemented transport, without guessing a remote model's support."""
    kind = config.type or config.name
    return {"backend_type": kind, "model": config.model, "tool_transport": config.tool_protocol,
            "native_tool_calls": "opt_in_adapter" if kind == "openai-compatible" else False,
            "native_requires_experimental_flag": True, "streaming": False, "json_schema": False,
            "json_object": "negotiated" if kind == "openai-compatible" else False,
            "cancellation": "local_wait_only_remote_outcome_unknown", "timeout_seconds": config.timeout_seconds,
            "context_window_tokens": config.context_window_tokens, "max_input_tokens": config.max_input_tokens,
            "max_output_tokens": config.max_output_tokens, "model_capabilities_verified": False,
            "cache_usage": "provider_reported_only", "cache_hits_guaranteed": False}


def optional_token_count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


class LLMBackend(Protocol):
    name: str
    config: BackendConfig

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult: ...


def estimate_cost(config: BackendConfig, tokens_in: int, tokens_out: int) -> float | None:
    if config.price_in_per_mtok is None or config.price_out_per_mtok is None:
        return None
    return (tokens_in * config.price_in_per_mtok + tokens_out * config.price_out_per_mtok) / 1_000_000
