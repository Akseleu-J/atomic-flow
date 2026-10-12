"""One retrying JSON POST used by every backend (the Anthropic adapter had no retries at all)."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from atomic_flow.errors import BackendError

RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}


class HTTPStatusError(BackendError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"LLM backend returned HTTP {status}: {detail}")
        self.status, self.detail = status, detail


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: float, retries: int,
              secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    def redact(text: str) -> str:
        for secret in filter(None, secrets):
            text = text.replace(secret, "[REDACTED]")
        return text

    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = redact(exc.read().decode("utf-8", errors="replace")[:1000])
            if exc.code not in RETRYABLE or attempt >= retries:
                raise HTTPStatusError(exc.code, detail) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(10.0, max(0.1, float(retry_after))) if retry_after else None
            except ValueError:
                delay = None
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise BackendError(f"Could not reach LLM backend at {url}: {redact(str(exc))}") from exc
            delay, last = None, exc
        except json.JSONDecodeError as exc:
            raise BackendError(f"LLM backend returned invalid JSON: {exc}") from exc
        time.sleep(delay if delay is not None else min(4.0, 0.5 * 2 ** attempt) * (0.75 + random.random() / 2))
    raise BackendError(f"LLM backend failed after retries: {last}")
