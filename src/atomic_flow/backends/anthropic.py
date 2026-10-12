"""Native Anthropic Messages API adapter."""

from __future__ import annotations

import time

from atomic_flow.backends.base import ChatResult, estimate_cost
from atomic_flow.backends.http import post_json
from atomic_flow.config import BackendConfig
from atomic_flow.errors import BackendError


class AnthropicBackend:
    def __init__(self, config: BackendConfig, *, backend_name: str = "anthropic"):
        self.config = config
        self.name = backend_name

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
        payload = post_json(endpoint, body, headers, timeout=self.config.timeout_seconds,
                            retries=self.config.max_retries, secrets=(key,))
        try:
            content = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
            usage = payload.get("usage", {})
            tokens_in, tokens_out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        except (AttributeError, TypeError, ValueError) as exc:
            raise BackendError("Unexpected Anthropic response shape.") from exc
        return ChatResult(content, tokens_in, tokens_out, estimate_cost(self.config, tokens_in, tokens_out),
                          int((time.monotonic() - started) * 1000), truncated=payload.get("stop_reason") == "max_tokens")
