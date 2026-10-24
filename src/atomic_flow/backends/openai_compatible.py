"""OpenAI-compatible chat client (stdlib only)."""

from __future__ import annotations

import json
import time
from typing import Any

from atomic_flow.backends.base import ChatResult, NativeToolCall, ResponseShapeError
from atomic_flow.backends.http import HTTPStatusError, TransportError, post_json
from atomic_flow.backends.usage import openai_usage
from atomic_flow.config import BackendConfig
from atomic_flow.errors import BackendError

__all__ = ["ChatResult", "OpenAICompatibleBackend"]


class OpenAICompatibleBackend:
    def __init__(self, config: BackendConfig, *, backend_name: str | None = None):
        self.config = config
        self.name = backend_name or config.name

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        return self._chat(messages, json_mode=json_mode, model=model)

    def chat_tools(self, messages, *, tools: list[dict], model=None) -> ChatResult:
        if self.config.type == "kaggle":
            raise BackendError("Kaggle uses the text-JSON fallback; native support is not declared")
        return self._chat(messages, model=model, tools=tools)

    def _chat(self, messages, *, json_mode=False, model=None, tools=None) -> ChatResult:
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body: dict[str, Any] = {"model": model or self.config.model, "messages": messages}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.max_output_tokens:
            body["max_tokens"] = self.config.max_output_tokens
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = False
        key = self.config.resolved_api_key()
        headers = {"Content-Type": "application/json", "User-Agent": "atomic-flow/0.2"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        started = time.monotonic()
        deadline = min(started + self.config.timeout_seconds, getattr(self, "route_deadline", float("inf")))
        payload: dict[str, Any] | None = None
        # Servers differ in what they reject: drop the offending optional parameter and retry once each.
        for _ in range(3):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("model parameter negotiation deadline exceeded; request not sent", outcome="not_sent")
                payload = post_json(endpoint, body, headers, timeout=remaining,
                                    retries=self.config.max_retries, secrets=(key,))
                break
            except HTTPStatusError as exc:
                # Routing reserves and accounts each physical send. Hidden
                # parameter-negotiation retries would undercount route quotas.
                if hasattr(self, "route_attempt_id"):
                    raise
                detail = exc.detail.casefold()
                dropped = next((p for p in ("response_format", "temperature") if exc.status in {400, 422} and p in detail and p in body), None)
                if not dropped:
                    raise
                body.pop(dropped)
        if payload is None:
            raise BackendError("LLM backend rejected the request parameters.")
        tokens_in, tokens_out, cost, cost_source, cached = openai_usage(payload, self.config)
        usage_result = ChatResult("", tokens_in, tokens_out, cost, int((time.monotonic() - started) * 1000),
                                  cache_read_tokens=cached, cost_source=cost_source,
                                  resolved_backend=self.name, resolved_model=model or self.config.model)
        try:
            choice = payload["choices"][0]
            content = choice["message"].get("content")
            calls = []
            raw_calls = choice["message"].get("tool_calls") or []
            if raw_calls:
                if not tools or len(raw_calls) != 1:
                    raise ValueError("Only one declared native call per turn is supported")
                call = raw_calls[0]
                if call.get("type") != "function":
                    raise ValueError("Unsupported native tool call type")
                calls = [NativeToolCall(call["id"], call["function"]["name"], call["function"]["arguments"])]
                try:
                    arguments = json.loads(calls[0].arguments)
                except json.JSONDecodeError:
                    arguments = calls[0].arguments
                content = json.dumps({"tool": calls[0].name, "args": arguments})
            elif "content" not in choice["message"]:
                raise ValueError("Missing message content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            usage_result.content = str(content or "")
            usage_result.truncated = choice.get("finish_reason") == "length"
            usage_result.tool_calls = calls
            return usage_result
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ResponseShapeError("The LLM backend response did not contain choices[0].message.content.", usage_result) from exc

    def check(self) -> tuple[bool, str]:
        try:
            result = self.chat([{"role": "user", "content": "Reply with OK."}])
        except BackendError as exc:
            return False, str(exc)
        return bool(result.content), "backend responded" if result.content else "backend returned an empty answer"
