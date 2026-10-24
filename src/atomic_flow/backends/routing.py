"""Host-configured bounded failover, with durable quota admission per attempt.

Only a confirmed HTTP 429 refusal is eligible for failover. Unknown submissions,
authorization errors and completed-but-malformed responses are never replayed.
The caller owns per-attempt money accounting; this layer cannot send directly.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import urlparse

from atomic_flow.backends.base import ResponseShapeError
from atomic_flow.backends.http import HTTPStatusError, TransportError, retry_after_seconds
from atomic_flow.config import user_dir
from atomic_flow.errors import ConfigurationError, ContextLimitError
from atomic_flow.llm.request_budget import admit_request
from atomic_flow.locking import exclusive_file


class QuotaUnavailable(TransportError):
    def __init__(self, message="all permitted routes are unavailable; wait for quota or reconcile pending attempts"):
        super().__init__(message, outcome="not_sent")


class RoutePolicyError(ResponseShapeError):
    """A completed, accounted response violates the selected route policy."""


class QuotaStore:
    """Local cooperating-process caps; other clients' usage is not inferred.

    Minute/day caps are local UTC fixed windows, not a mirror of provider quotas.
    Server Retry-After persists separately. In-flight admissions survive crashes.
    """

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else user_dir() / "provider-quotas"

    @contextmanager
    def state(self, scope, *, deadline=None, check=lambda: None):
        self.directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(scope.encode()).hexdigest()
        target = self.directory / (name + ".json")
        # Global blocking flock may wait indefinitely on Unix. Retry nonblocking
        # acquisition so route admission retains its deadline and cancellation.
        lock_deadline = time.monotonic() + 5 if deadline is None else deadline
        with ExitStack() as stack:
            while True:
                check()
                try:
                    stack.enter_context(exclusive_file(self.directory / (name + ".lock")))
                    break
                except ValueError as exc:
                    remaining = lock_deadline - time.monotonic()
                    if remaining <= 0:
                        raise QuotaUnavailable("provider quota lock deadline exceeded; request not sent") from exc
                    time.sleep(min(0.025, remaining))
            if target.exists() and target.stat().st_size > 1_048_576:
                raise ConfigurationError("provider quota state exceeds byte limit")
            try:
                data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {
                "version": 1, "pending": {}, "cooldown_until": 0, "minute": -1, "day": -1,
                "minute_requests": 0, "minute_tokens": 0, "day_requests": 0}
            except (ValueError, UnicodeError) as exc:
                raise ConfigurationError("invalid provider quota JSON; inspect before retry") from exc
            if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
                    or not isinstance(data.get("pending"), dict)
                    or len(data["pending"]) > 1000):
                raise ConfigurationError("invalid provider quota state; inspect before retry")
            for key in ("minute_requests", "minute_tokens", "day_requests", "minute", "day"):
                if type(data.get(key)) is not int or data[key] < (-1 if key in {"minute", "day"} else 0):
                    raise ConfigurationError("invalid provider quota counters")
            if type(data.get("cooldown_until")) not in (int, float) or not math.isfinite(data["cooldown_until"]) or data["cooldown_until"] < 0:
                raise ConfigurationError("invalid provider quota cooldown")
            for attempt, pending in data["pending"].items():
                if (not isinstance(attempt, str) or not 1 <= len(attempt) <= 128 or not isinstance(pending, dict)
                        or type(pending.get("created_at")) not in (int, float)
                        or not math.isfinite(pending["created_at"]) or pending["created_at"] < 0):
                    raise ConfigurationError("invalid pending provider quota attempt")
                for key in ("tokens", "minute", "day"):
                    if key in pending and (type(pending[key]) is not int or pending[key] < 0):
                        raise ConfigurationError("invalid pending provider quota reservation")
            yield data
            temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temp.open("w", encoding="utf-8") as handle:
                    json.dump(data, handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)

    def reserve(self, scope, config, attempt, tokens, *, deadline=None, check=lambda: None):
        with self.state(scope, deadline=deadline, check=check) as state:
            now = time.time()
            if deadline is not None and time.monotonic() >= deadline:
                raise QuotaUnavailable("provider admission deadline exceeded; request not sent")
            if state["cooldown_until"] > now or len(state["pending"]) >= config.max_concurrent_requests:
                raise QuotaUnavailable()
            for window, divisor, counters in (("minute", 60, ("minute_requests", "minute_tokens")),
                                               ("day", 86400, ("day_requests",))):
                stamp = int(now // divisor)
                # Clock rollback cannot create fresh capacity.
                if stamp > state[window]:
                    state[window] = stamp
                    for counter in counters:
                        state[counter] = 0
            for key, amount, limit in (("minute_requests", 1, config.requests_per_minute),
                                        ("day_requests", 1, config.requests_per_day),
                                        ("minute_tokens", tokens, config.tokens_per_minute)):
                if limit is not None and state[key] + amount > limit:
                    raise QuotaUnavailable()
            state["minute_requests"] += 1
            state["day_requests"] += 1
            state["minute_tokens"] += tokens
            state["pending"][attempt] = {"created_at": now, "tokens": tokens,
                                         "minute": state["minute"], "day": state["day"]}

    def finish(self, scope, attempt, *, known, cooldown=0, not_sent=False, deadline=None):
        with self.state(scope, deadline=deadline) as state:
            if known:
                pending = state["pending"].pop(attempt, None)
                if not_sent and pending:
                    # A refused request may consume provider quota; only an
                    # explicitly unsent attempt returns local window capacity.
                    if pending.get("minute") == state["minute"]:
                        state["minute_requests"] = max(0, state["minute_requests"] - 1)
                        state["minute_tokens"] = max(0, state["minute_tokens"] - pending.get("tokens", 0))
                    if pending.get("day") == state["day"]:
                        state["day_requests"] = max(0, state["day_requests"] - 1)
            state["cooldown_until"] = max(state["cooldown_until"], time.time() + cooldown)

    def reconcile(self, scope, attempt, *, evidence_sha256):
        """Host-only confirmation that this attempt is no longer executing.

        Does not settle money/effects or authorize model/tool replay.
        """
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("reconciliation requires evidence SHA-256")
        with self.state(scope) as state:
            previous = state.get("last_reconciliation")
            if attempt not in state["pending"]:
                if previous == {"attempt_id": attempt, "evidence_sha256": evidence_sha256}:
                    return
                raise ValueError("unknown quota attempt")
            state["pending"].pop(attempt)
            state["last_reconciliation"] = {"attempt_id": attempt, "evidence_sha256": evidence_sha256}


class RoutingBackend:
    def __init__(self, backends, settings, conversation, store=None):
        if not backends or len({b.name for b in backends}) != len(backends):
            raise ConfigurationError("routing needs unique explicitly configured backends")
        for backend in backends:
            if backend.config.tool_protocol != "text_json":
                raise ConfigurationError("routing v1 requires text_json on every route; native transcript migration is unsupported")
            if not backend.config.model or not backend.config.base_url:
                raise ConfigurationError("each route requires explicit model and endpoint")
        self.backends = tuple(backends)
        self.settings, self.conversation = settings, conversation
        self.store = store or QuotaStore()
        self.config, self.name = backends[0].config, "routing"

    def chat(self, *args, **kwargs):
        raise TransportError("routing requires an accounting owner; use TaskRunner/Orchestrator", outcome="not_sent")

    def run_attempts(self, invoke, messages, *, check=lambda: None, tools=None, model=None):
        if model is not None:
            raise ConfigurationError("model override is unsupported for a configured route chain")
        if tools:
            raise ConfigurationError("routing v1 supports text_json tool transport only")
        deadline = time.monotonic() + self.settings.timeout_seconds
        logical_id = "logical_" + uuid.uuid4().hex
        attempts = 0
        last_error = None
        for candidate in self.backends:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or attempts >= self.settings.max_attempts:
                break
            config = candidate.config
            prices = (config.price_in_per_mtok, config.price_out_per_mtok)
            if not all(p is not None and math.isfinite(p) and p >= 0 for p in prices):
                continue
            if self.settings.mode == "free_only" and (
                    prices != (0, 0) or config.free_until is None or config.free_until <= time.time()):
                continue
            try:
                admission = admit_request(messages, config, self.conversation)
            except ContextLimitError:
                continue
            scope = config.quota_scope or urlparse(config.base_url).netloc.lower()
            attempt = "route_" + uuid.uuid4().hex
            try:
                self.store.reserve(scope, config, attempt, admission.estimated_input_tokens + admission.output_reserve_tokens,
                                   deadline=deadline, check=check)
            except QuotaUnavailable as exc:
                last_error = exc
                continue
            attempts += 1
            selected = copy.copy(candidate)
            selected.route_attempt_id, selected.logical_call_id, selected.quota_scope = attempt, logical_id, scope
            selected.route_deadline = deadline
            invoke_started = False
            try:
                check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QuotaUnavailable("provider routing deadline exceeded; request not sent")
                if self.settings.mode == "free_only" and config.free_until <= time.time():
                    raise QuotaUnavailable("free tariff expired before submission; request not sent")
                selected.config = config.model_copy(update={"max_retries": 0,
                    "max_output_tokens": config.max_output_tokens or 4096,
                    "timeout_seconds": min(config.timeout_seconds, remaining)})
                invoke_started = True
                result = invoke(selected)
            except BaseException as exc:
                outcome = getattr(exc, "request_outcome", "unknown") if invoke_started else "not_sent"
                known = outcome in {"not_sent", "refused", "completed"}
                throttled = isinstance(exc, HTTPStatusError) and exc.status == 429 and outcome == "refused"
                delay = retry_after_seconds(getattr(exc, "retry_after", None)) if throttled else None
                try:
                    self.store.finish(scope, attempt, known=known, not_sent=outcome == "not_sent", deadline=deadline,
                                      cooldown=max(self.settings.cooldown_seconds, delay or 0) if throttled else 0)
                except Exception as cleanup_error:
                    # A storage/lock error must not relabel a submitted attempt
                    # as an unsent one, and must not authorize failover.
                    raise exc from cleanup_error
                if throttled:
                    last_error = exc
                    continue
                raise
            try:
                self.store.finish(scope, attempt, known=True, deadline=deadline)
            except Exception as exc:
                raise RoutePolicyError("quota completion could not persist; accounted response retained, inspect quota state", result) from exc
            if self.settings.mode == "free_only" and (type(result.cost_usd) not in (int, float) or result.cost_usd != 0):
                raise RoutePolicyError("free_only response did not confirm zero cost; accounted attempt paused, no failover", result)
            result.resolved_backend, result.resolved_model, result.attempt_id = selected.name, selected.config.model, attempt
            return result
        raise QuotaUnavailable() from last_error
