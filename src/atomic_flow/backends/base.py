"""Shared backend contract: result type, Protocol, cost estimate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from atomic_flow.config import BackendConfig


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


def backend_capabilities(config: BackendConfig) -> dict:
    """Describe implemented transport, without guessing a remote model's support."""
    kind = config.type or config.name
    return {"backend_type": kind, "model": config.model, "tool_transport": "text_json",
            "native_tool_calls": False, "streaming": False, "json_schema": False,
            "json_object": "negotiated" if kind == "openai-compatible" else False,
            "cancellation": "timeout_only", "timeout_seconds": config.timeout_seconds,
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
