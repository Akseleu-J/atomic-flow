"""Native Anthropic Messages API adapter."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from atomic_flow.backends.openai_compatible import ChatResult
from atomic_flow.config import BackendConfig
from atomic_flow.errors import BackendError


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, config: BackendConfig):
        self.config = config

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, model: str | None = None) -> ChatResult:
        started = time.monotonic()
        base = self.config.base_url.rstrip("/")
        endpoint = base if base.endswith("/messages") else base + "/messages"
        system = "\n\n".join(item["content"] for item in messages if item.get("role") == "system")
        conversation = [item for item in messages if item.get("role") != "system"]
        body = {"model": model or self.config.model, "max_tokens": 4096,
                "messages": conversation, "system": system}
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                   "User-Agent": "atomic-flow/0.1"}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise BackendError(f"Anthropic returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise BackendError(f"Could not reach Anthropic at {endpoint}: {exc}") from exc
        content = "".join(item.get("text", "") for item in payload.get("content", []) if item.get("type") == "text")
        usage = payload.get("usage", {})
        return ChatResult(content, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), None,
                          int((time.monotonic() - started) * 1000))
