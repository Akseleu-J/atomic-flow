"""Small OpenAI-compatible chat client using only the Python standard library."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from atomic_flow.config import BackendConfig
from atomic_flow.errors import BackendError


@dataclass
class ChatResult:
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0


class OpenAICompatibleBackend:
    def __init__(self, config: BackendConfig, *, backend_name: str | None = None):
        self.config = config
        self.name = backend_name or config.name

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body: dict[str, Any] = {"model": model or self.config.model, "messages": messages, "temperature": 0.1}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "User-Agent": "atomic-flow/0.1"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
        started = time.monotonic()
        last_error: Exception | None = None
        attempt = 0
        while attempt <= self.config.max_retries:
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                if self.config.api_key:
                    detail = detail.replace(self.config.api_key, "[REDACTED]")
                if json_mode and "response_format" in body and exc.code in {400, 422} and "response_format" in detail.casefold():
                    body.pop("response_format", None)
                    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
                    continue
                if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.config.max_retries:
                    raise BackendError(f"LLM backend returned HTTP {exc.code}: {detail}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(10.0, max(0.1, float(retry_after))) if retry_after else min(4.0, 0.5 * 2**attempt)
                except ValueError:
                    delay = min(4.0, 0.5 * 2**attempt)
                time.sleep(delay)
                last_error = exc
                attempt += 1
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self.config.max_retries:
                    raise BackendError(f"Could not reach LLM backend at {endpoint}: {exc}") from exc
                time.sleep(min(4.0, 0.5 * 2**attempt))
                last_error = exc
                attempt += 1
            except json.JSONDecodeError as exc:
                raise BackendError(f"LLM backend returned invalid JSON: {exc}") from exc
        if "payload" not in locals():
            raise BackendError(f"LLM backend failed after retries: {last_error}")
        try:
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            usage = payload.get("usage") or {}
            nanodollars = float(usage.get("input_tokens_cost_nanodollars") or 0) + float(usage.get("output_tokens_cost_nanodollars") or 0)
            cost = usage.get("cost_usd", usage.get("total_cost_usd"))
            if cost is None and nanodollars:
                cost = float(nanodollars) / 1_000_000_000
            return ChatResult(str(content or ""), int(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
                              int(usage.get("completion_tokens", usage.get("output_tokens", 0))),
                              float(cost) if cost is not None else None,
                              int((time.monotonic() - started) * 1000))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BackendError("The LLM backend response did not contain choices[0].message.content.") from exc

    def check(self) -> tuple[bool, str]:
        try:
            result = self.chat([{"role": "user", "content": "Reply with OK."}])
            return bool(result.content), "backend responded" if result.content else "backend returned an empty answer"
        except BackendError as exc:
            return False, str(exc)
