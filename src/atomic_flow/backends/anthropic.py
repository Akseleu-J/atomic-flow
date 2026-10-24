"""Native Anthropic Messages API adapter."""

from __future__ import annotations

import time

from atomic_flow.backends.base import ChatResult, ResponseShapeError
from atomic_flow.backends.http import TransportError, post_json
from atomic_flow.backends.usage import AnthropicCachePrices, anthropic_usage
from atomic_flow.config import BackendConfig


class AnthropicBackend:
    def __init__(self, config: BackendConfig, *, backend_name: str = "anthropic", cache_prices: AnthropicCachePrices | None = None):
        self.config = config
        self.name = backend_name
        self.cache_prices = cache_prices

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        started = time.monotonic()
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/messages") else base + "/messages"
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        conversation = [m for m in messages if m.get("role") != "system"]
        body: dict = {"model": model or self.config.model, "max_tokens": self.config.max_output_tokens or 4096,
                      "messages": conversation}
        if system:                                         # an empty `system` string is rejected by the API
            body["system"] = system
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        key = self.config.resolved_api_key()
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01", "User-Agent": "atomic-flow/0.2"}
        if key:
            headers["x-api-key"] = key
        remaining = min(self.config.timeout_seconds, getattr(self, "route_deadline", float("inf")) - time.monotonic())
        if remaining <= 0:
            raise TransportError("provider routing deadline exceeded; request not sent", outcome="not_sent")
        payload = post_json(endpoint, body, headers, timeout=remaining,
                            retries=self.config.max_retries, secrets=(key,))
        tokens_in, tokens_out, cost, source, cache_read, cache_write = anthropic_usage(
            payload, self.config, self.cache_prices, model=model)
        result = ChatResult("", tokens_in, tokens_out, cost,
                          int((time.monotonic() - started) * 1000), truncated=payload.get("stop_reason") == "max_tokens",
                          cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                          cost_source=source, resolved_backend=self.name, resolved_model=model or self.config.model)
        try:
            blocks = payload["content"]
            if not isinstance(blocks, list) or not blocks:
                raise ValueError("missing content blocks")
            texts = []
            for block in blocks:
                if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                    raise ValueError("invalid content block")
                if block["type"] == "text":
                    if not isinstance(block.get("text"), str):
                        raise ValueError("invalid text block")
                    texts.append(block["text"])
                elif block["type"] not in {"thinking", "redacted_thinking"}:
                    # Native/server tools are not declared by this text adapter.
                    raise ValueError("unsupported content block")
            if not texts:
                raise ValueError("no text content")
            result.content = "".join(texts)
            return result
        except (KeyError, ValueError, TypeError) as exc:
            raise ResponseShapeError("Unexpected Anthropic response content; received usage retained.", result) from exc
