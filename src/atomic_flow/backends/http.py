"""Bounded local model waits; uncertain provider outcomes are never retried."""
from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.backends.cancellation import current_check
from atomic_flow.errors import BackendError

RETRYABLE = {425, 429}
REFUSED = {400, 401, 403, 404, 405, 413, 415, 422, 425, 429}
_SLOTS = threading.BoundedSemaphore(4)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class TransportError(BackendError):
    def __init__(self, message: str, *, outcome="unknown"):
        super().__init__(message)
        self.request_outcome = outcome


class TransportCancelled(TaskCancelled):
    def __init__(self, outcome="unknown"):
        super().__init__("model wait cancelled; remote inference may continue" if outcome == "unknown"
                         else "model request cancelled before submission or after refusal")
        self.request_outcome = outcome


class HTTPStatusError(TransportError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"LLM backend returned HTTP {status}: {detail}",
                         outcome="refused" if status in REFUSED else "unknown")
        self.status, self.detail = status, detail
        self.retry_after = None


def retry_after_seconds(value, *, now=None):
    """Delta seconds or HTTP-date; never shorten a provider's cooldown."""
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            deadline = parsedate_to_datetime(str(value))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            delay = deadline.timestamp() - (datetime.now(UTC).timestamp() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


def _cancel(check, outcome):
    if check:
        try:
            check()
        except TaskCancelled as exc:
            raise TransportCancelled(outcome) from exc


def _exchange(request, timeout, check):
    _cancel(check, "not_sent")
    if not _SLOTS.acquire(blocking=False):
        raise TransportError("model transport workers are busy; request not sent", outcome="not_sent")
    ready = threading.Event()
    box = []

    def work():
        try:
            try:
                response = urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                # Read error bodies on the worker too: they can block.
                with exc:
                    detail = exc.read(1001).decode("utf-8", errors="replace")[:1000]
                error = HTTPStatusError(exc.code, detail)
                error.retry_after = exc.headers.get("Retry-After") if exc.headers else None
                raise error from exc
            with response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise TransportError("LLM response exceeds byte limit")
                box.append(json.loads(raw.decode("utf-8")))
        except BaseException as exc:
            box.append(exc)
        finally:
            ready.set()
            _SLOTS.release()

    # DNS/socket calls cannot always be interrupted. An abandoned daemon retains
    # its slot until it exits; saturation refuses new calls before send.
    thread = threading.Thread(target=work, daemon=True, name="atomic-http")
    try:
        _cancel(check, "not_sent")
        thread.start()
    except BaseException:
        _SLOTS.release()
        raise
    deadline = time.monotonic() + timeout
    while not ready.wait(0.025):
        _cancel(check, "unknown")
        if time.monotonic() >= deadline:
            raise TransportError("model wait deadline exceeded; provider outcome unknown")
    # A fully received response wins a simultaneous cancellation: caller accounts
    # its cost before the next cancellation boundary.
    result = box[0]
    if isinstance(result, BaseException):
        raise result
    if not isinstance(result, dict):
        raise TransportError("LLM backend returned a non-object JSON response")
    return result


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, timeout: float, retries: int,
              secrets: tuple[str, ...] = ()) -> dict[str, Any]:
    def redact(text: str) -> str:
        for secret in filter(None, secrets):
            text = text.replace(secret, "[REDACTED]")
        return text

    check = current_check()
    overall_deadline = time.monotonic() + timeout
    _cancel(check, "not_sent")
    try:
        data = json.dumps(body, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransportError("invalid model request JSON; request not sent", outcome="not_sent") from exc
    for attempt in range(retries + 1):
        _cancel(check, "not_sent")
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        except (ValueError, TypeError) as exc:
            raise TransportError("invalid model request URL/headers; request not sent", outcome="not_sent") from exc
        try:
            remaining = overall_deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError("model retry deadline exceeded; request not sent", outcome="not_sent")
            return _exchange(request, remaining, check)
        except HTTPStatusError as exc:
            exc.detail = redact(exc.detail)
            exc.args = (f"LLM backend returned HTTP {exc.status}: {exc.detail}",)
            if exc.status not in RETRYABLE or attempt >= retries:
                raise
            delay = retry_after_seconds(exc.retry_after)
            if delay is not None and time.monotonic() + delay >= overall_deadline:
                # Give the caller the original refusal/reset rather than retry early.
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"Could not reach LLM backend: {redact(str(exc))}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError("LLM backend returned invalid JSON") from exc
        delay = delay if delay is not None else min(4.0, 0.5 * 2 ** attempt) * (0.75 + random.random() / 2)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            _cancel(check, "refused")
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
    raise AssertionError("unreachable")
