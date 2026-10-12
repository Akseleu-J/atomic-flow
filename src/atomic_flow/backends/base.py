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


class LLMBackend(Protocol):
    name: str
    config: BackendConfig

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult: ...


def estimate_cost(config: BackendConfig, tokens_in: int, tokens_out: int) -> float | None:
    if config.price_in_per_mtok is None or config.price_out_per_mtok is None:
        return None
    return (tokens_in * config.price_in_per_mtok + tokens_out * config.price_out_per_mtok) / 1_000_000
