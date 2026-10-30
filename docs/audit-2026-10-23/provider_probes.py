"""Offline audit probes: no model calls, keys, sockets or runtime modifications."""
from __future__ import annotations

import json
from unittest.mock import patch

from atomic_flow.backends import http
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import BackendConfig


def main():
    findings = []
    for label, cost, prices in [
        ("openrouter_numeric_nonzero_cost", 0.001, {}),
        ("openrouter_numeric_zero_cost", 0.0, {}),
        ("explicit_free_numeric_zero_cost", 0.0, {"price_in_per_mtok": 0, "price_out_per_mtok": 0}),
    ]:
        backend = OpenAICompatibleBackend(BackendConfig(
            type="openai-compatible", base_url="https://example.invalid/v1", model="audit-fixture", **prices))
        payload = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 12, "completion_tokens": 1, "cost": cost}}
        with patch("atomic_flow.backends.openai_compatible.post_json", return_value=payload):
            try:
                result = backend.chat([{"role": "user", "content": "synthetic"}])
                findings.append({"case": label, "outcome": "response", "cost_usd": result.cost_usd,
                                 "cost_source": result.cost_source})
            except Exception as exc:
                findings.append({"case": label, "outcome": "error", "type": type(exc).__name__,
                                 "cause": type(exc.__cause__).__name__, "message": str(exc)})

    clock = [0.0]
    attempted_at = []

    def exchange(*args):
        attempted_at.append(clock[0])
        if len(attempted_at) == 1:
            exc = http.HTTPStatusError(429, "synthetic quota refusal")
            exc.retry_after = "120"
            raise exc
        return {"ok": True}

    def sleep(seconds):
        clock[0] += max(seconds, 0.00001)

    with patch.object(http, "_exchange", side_effect=exchange), \
            patch.object(http.time, "monotonic", side_effect=lambda: clock[0]), \
            patch.object(http.time, "sleep", side_effect=sleep):
        http.post_json("https://example.invalid/v1", {}, {}, timeout=2, retries=1)
    findings.append({"case": "retry_after_120_seconds", "attempted_at_seconds": attempted_at,
                     "server_retry_after_seconds": 120})
    findings.append({"case": "payment_required_outcome", "http_status": 402,
                     "outcome": http.HTTPStatusError(402, "synthetic exhausted credit").request_outcome})
    print(json.dumps({"network_calls": 0, "model_calls": 0, "findings": findings}, indent=2))


if __name__ == "__main__":
    main()
