"""Evaluation-only observation of backend calls, independent of response parsing.

This does not charge the runtime ledger. Transport retries must be disabled; an
adapter's HTTP parameter negotiation is not observable at this boundary.
"""
from __future__ import annotations

import math
import time

from atomic_flow.errors import BudgetExceeded


def usage_summary(calls: list[dict]) -> dict:
    cost_complete = all(c.get("cost_usd") is not None for c in calls)
    token_complete = all(c.get("tokens_in") is not None and c.get("tokens_out") is not None for c in calls)
    known_cost = sum(c["cost_usd"] for c in calls if c.get("cost_usd") is not None)
    known_in = sum(c["tokens_in"] for c in calls if c.get("tokens_in") is not None)
    known_out = sum(c["tokens_out"] for c in calls if c.get("tokens_out") is not None)
    return {"chat_calls": len(calls), "failed_calls": sum(c["status"] == "error" for c in calls),
            "reported_cost_usd": known_cost if cost_complete else None, "known_cost_usd": known_cost,
            "cost_complete": cost_complete, "usage_complete": cost_complete and token_complete,
            "tokens_in": known_in if token_complete else None, "tokens_out": known_out if token_complete else None,
            "known_tokens_in": known_in, "known_tokens_out": known_out}


class EvaluationMeter:
    """Record responses before parsing; unknown spend blocks subsequent calls."""

    def __init__(self, backend, *, max_calls=None, max_reported_cost=None, per_trial_calls=None, on_update=None):
        for value in (max_calls, per_trial_calls):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("Positive evaluation call limits required")
        if max_reported_cost is not None and (not math.isfinite(max_reported_cost) or max_reported_cost <= 0):
            raise ValueError("Positive finite evaluation cost limit required")
        if backend.config.max_retries != 0:
            raise ValueError("Evaluation requires max_retries=0 so attempts cannot be hidden")
        self.backend, self.config, self.name = backend, backend.config, backend.name
        self.max_calls, self.max_reported_cost, self.per_trial_calls = max_calls, max_reported_cost, per_trial_calls
        self.calls: list[dict] = []
        self.trial_start = 0
        self.on_update = on_update

    def _checkpoint(self):
        if self.on_update is not None:
            self.on_update([dict(row) for row in self.calls])

    def __getattr__(self, name):
        # Preserve capability discovery: do not invent native transport support.
        value = getattr(self.backend, name)
        if name == "chat_tools":
            return lambda *args, **kwargs: self._call(value, *args, **kwargs)
        return value

    @property
    def config(self):
        return self.backend.config

    @config.setter
    def config(self, value):
        self.backend.config = value

    def chat(self, *args, **kwargs):
        return self._call(self.backend.chat, *args, **kwargs)

    def _call(self, operation, *args, **kwargs):
        if ((self.max_calls is not None and len(self.calls) >= self.max_calls)
                or (self.per_trial_calls is not None and len(self.calls) - self.trial_start >= self.per_trial_calls)):
            raise BudgetExceeded("Evaluation chat-call cap reached")
        if any(row.get("cost_usd") is None for row in self.calls):
            raise BudgetExceeded("Unknown previous evaluation spend; stopping further calls")
        if self.max_reported_cost is not None and sum(row["cost_usd"] for row in self.calls) >= self.max_reported_cost:
            raise BudgetExceeded("Reported evaluation cost threshold reached")
        row = {"attempt": len(self.calls) + 1, "cost_usd": None, "tokens_in": None, "tokens_out": None, "status": "in_flight"}
        self.calls.append(row)
        self._checkpoint()  # Intent before provider call; persistence failure prevents sending.
        start = time.monotonic()
        try:
            result = operation(*args, **kwargs)
            cost = result.cost_usd
            if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0):
                raise ValueError("Invalid reported cost")
            row.update(status="response", cost_usd=cost, truncated=result.truncated)
            for key in ("tokens_in", "tokens_out"):
                value = getattr(result, key, None)
                row[key] = value if type(value) is int and value >= 0 else None
            return result
        except BaseException as exc:
            row.update(status="error", error_type=type(exc).__name__)
            # Explicit not-sent/refused outcomes are known non-inference attempts.
            outcome = getattr(exc, "request_outcome", None)
            row["request_outcome"] = outcome if outcome in {"not_sent", "refused", "unknown"} else "unknown"
            if outcome in {"not_sent", "refused"}:
                row.update(cost_usd=0.0, tokens_in=0, tokens_out=0)
            raise
        finally:
            row["wall_seconds"] = round(time.monotonic() - start, 3)
            self._checkpoint()  # A later parsing error cannot erase received usage.
