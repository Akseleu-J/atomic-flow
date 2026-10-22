"""OpenAI-compatible chat client (stdlib only)."""

from __future__ import annotations

import time
from typing import Any

from atomic_flow.backends.base import ChatResult, estimate_cost, optional_token_count
from atomic_flow.backends.http import HTTPStatusError, post_json
from atomic_flow.config import BackendConfig
from atomic_flow.errors import BackendError

__all__ = ["ChatResult", "OpenAICompatibleBackend"]


class OpenAICompatibleBackend:
    def __init__(self, config: BackendConfig, *, backend_name: str | None = None):
        self.config = config
        self.name = backend_name or config.name

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body: dict[str, Any] = {"model": model or self.config.model, "messages": messages}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.max_output_tokens:
            body["max_tokens"] = self.config.max_output_tokens
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        key = self.config.resolved_api_key()
        headers = {"Content-Type": "application/json", "User-Agent": "atomic-flow/0.2"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        started = time.monotonic()
        payload: dict[str, Any] | None = None
        # Servers differ in what they reject: drop the offending optional parameter and retry once each.
        for _ in range(3):
            try:
                payload = post_json(endpoint, body, headers, timeout=self.config.timeout_seconds,
                                    retries=self.config.max_retries, secrets=(key,))
                break
            except HTTPStatusError as exc:
                detail = exc.detail.casefold()
                dropped = next((p for p in ("response_format", "temperature") if exc.status in {400, 422} and p in detail and p in body), None)
                if not dropped:
                    raise
                body.pop(dropped)
        if payload is None:
            raise BackendError("LLM backend rejected the request parameters.")
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            usage = payload.get("usage") or {}
            tokens_in = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
            tokens_out = int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
            cost_block = usage.get("cost") or {}
            # Some proxies duplicate these fields at both levels. Nested values win.
            nano = sum(float(cost_block.get(k, usage.get(k)) or 0) for k in (
                "input_tokens_cost_nanodollars", "output_tokens_cost_nanodollars"))
            cost = usage.get("cost_usd", usage.get("total_cost_usd"))
            if cost is None and nano:
                cost = nano / 1_000_000_000
            reported_cost = cost is not None
            has_usage = (any(k in usage for k in ("prompt_tokens", "input_tokens"))
                         and any(k in usage for k in ("completion_tokens", "output_tokens")))
            free = self.config.price_in_per_mtok == self.config.price_out_per_mtok == 0
            cost = (float(cost) if cost is not None else
                    estimate_cost(self.config, tokens_in, tokens_out) if has_usage or free else None)
            return ChatResult(str(content or ""), tokens_in, tokens_out, cost, int((time.monotonic() - started) * 1000),
                              truncated=choice.get("finish_reason") == "length",
                              cache_read_tokens=optional_token_count((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
                              cost_source="provider" if reported_cost else "configured_estimate" if cost is not None else "unknown")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise BackendError("The LLM backend response did not contain choices[0].message.content.") from exc

    def check(self) -> tuple[bool, str]:
        try:
            result = self.chat([{"role": "user", "content": "Reply with OK."}])
        except BackendError as exc:
            return False, str(exc)
        return bool(result.content), "backend responded" if result.content else "backend returned an empty answer"
