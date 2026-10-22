"""Durable per-call task accounting, including planner and failed/cancelled runs.

Reservations use declared prices and a conservative byte-based token allowance.
They are admission estimates, not a guarantee about provider billing/tokenization.
"""
from __future__ import annotations

import copy
import json
import math
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from atomic_flow.agents.task import Task, save
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.errors import BackendError, BudgetExceeded


def check_accounting(task: Task):
    if not math.isfinite(task.budget_spent) or task.budget_spent < 0:
        raise BudgetExceeded("invalid saved task spend; review accounting before resuming")
    if any(event.get("status") != "completed" for event in task.budget_events):
        raise BudgetExceeded("unresolved model call; review provider usage before starting another task (no automatic replay)")
    try:
        known = [float(event["cost_usd"]) for event in task.budget_events]
        if any(not math.isfinite(cost) or cost < 0 for cost in known) or sum(known) > task.budget_spent + 1e-9:
            raise ValueError("inconsistent costs")
    except (KeyError, TypeError, ValueError) as exc:
        raise BudgetExceeded("inconsistent saved call accounting; review it before resuming") from exc


class TaskBudgetBackend:
    accounts_spend = True

    def __init__(self, backend, task: Task, config: AtomicConfig, cancel_check: Callable[[], None], *, phase="step"):
        self.backend = copy.copy(backend)
        self.task, self.settings, self.cancel_check, self.phase = task, config, cancel_check, phase
        original = getattr(backend, "config", BackendConfig())
        self.config = original.model_copy(update={
            "max_output_tokens": original.max_output_tokens or config.task.default_max_output_tokens,
            # A transport retry may be charged even if the previous response was lost.
            "max_retries": 0,
        })
        if self.backend is not None:
            self.backend.config = self.config
        self.name = getattr(backend, "name", "unconfigured")
        self.ledger = SpendLedger(config.budget.daily_limit_usd)
        self.journal = None
        if config.artifacts.save:
            from atomic_flow.sessions import SessionJournal
            try:
                self.journal = SessionJournal.create(Path(task.project), task.id, metadata={"kind": "task"})
            except FileExistsError:
                self.journal = SessionJournal.open(task.id)
            snapshot = self.journal.read()
            inputs = [event for event in snapshot.events if event["event_type"] == "user_input"]
            if not inputs:
                if any(event["event_type"] in {"model_request", "tool_request"} for event in snapshot.events):
                    raise BudgetExceeded("task session has calls without saved input; inspect state before continuing")
                self.journal.append("user_input", {"text": task.query})
            else:
                from atomic_flow.activation.redact import redact
                if any(event["payload"].get("text") != redact(task.query) for event in inputs):
                    raise BudgetExceeded("task session input does not match the saved task")

    def chat(self, messages, *, json_mode=False, model=None):
        return self._chat(messages, json_mode=json_mode, model=model)

    def chat_tools(self, messages, *, tools, model=None):
        if not self.settings.experimental.native_tools or not hasattr(self.backend, "chat_tools"):
            raise BackendError("Native tools are disabled or unavailable")
        return self._chat(messages, model=model, native_tools=tools)

    def _chat(self, messages, *, json_mode=False, model=None, native_tools=None):
        self.cancel_check()
        if self.backend is None:
            raise BackendError("No model backend configured")
        from atomic_flow.llm.request_budget import admit_request
        admit_request(messages, self.config, self.settings.conversation, tool_schemas=native_tools)
        check_accounting(self.task)
        if sum(not event.get("not_sent") for event in self.task.budget_events) >= self.settings.task.max_model_calls:
            raise BudgetExceeded("task model-call limit reached")
        if model and model != self.config.model:
            raise BudgetExceeded("task pricing is configured for a different model")
        enforce = self.settings.task.fail_on_budget_exceeded and self.settings.task.max_budget_usd > 0
        prices = (self.config.price_in_per_mtok, self.config.price_out_per_mtok)
        known_prices = all(p is not None and math.isfinite(p) and p >= 0 for p in prices)
        if (enforce or self.settings.budget.daily_limit_usd > 0) and not known_prices:
            raise BudgetExceeded("configure input/output prices before task calls; use explicit 0/0 only for a free backend")
        # Text-only messages. Byte count plus framing allowance intentionally overestimates
        # common tokenizers; unsupported provider framing/billing may still exceed it.
        input_allowance = len(json.dumps({"messages": messages, "tools": native_tools}, ensure_ascii=False).encode()) + 512 * len(messages) + 1024
        reservation = ((input_allowance * prices[0] + self.config.max_output_tokens * prices[1]) / 1_000_000
                       if known_prices else 0.0)
        if not math.isfinite(reservation) or reservation < 0:
            raise BudgetExceeded("invalid model-call reservation")
        if enforce and self.task.budget_spent + reservation > self.settings.task.max_budget_usd:
            raise BudgetExceeded("remaining task budget cannot cover the next model-call reservation")
        event = {"id": "call_" + uuid.uuid4().hex, "phase": self.phase, "status": "in_flight",
                 "reserved_usd": reservation, "created_at": time.time(), "backend": self.name,
                 "model": self.config.model, "input_allowance": input_allowance,
                 "max_output_tokens": self.config.max_output_tokens,
                 "price_in_per_mtok": prices[0], "price_out_per_mtok": prices[1]}
        self.task.budget_events.append(event)
        save(self.task)  # a crash after this point is never silently retried
        try:
            self.ledger.reserve(task_id=self.task.id, call_id=event["id"], backend=self.name,
                                model=self.config.model, reserved_usd=reservation)
        except (BudgetExceeded, OSError, ValueError) as exc:
            # No request was made: safe to resume after budget/storage is fixed.
            event.update(status="completed", cost_usd=0.0, not_sent=True)
            save(self.task)
            raise BudgetExceeded(f"model call not sent: {exc}") from exc
        try:
            self.cancel_check()
        except BaseException:
            event.update(status="completed", cost_usd=0.0, not_sent=True)
            save(self.task)
            self.ledger.record(task_id=self.task.id, call_id=event["id"], cost_usd=0.0, status="not_sent")
            raise
        try:
            if self.journal:
                self.journal.append("model_request", {"phase": self.phase, "messages": messages}, call_id=event["id"])
        except Exception:
            event.update(status="completed", cost_usd=0.0, not_sent=True)
            save(self.task)
            self.ledger.record(task_id=self.task.id, call_id=event["id"], cost_usd=0.0, status="not_sent")
            raise
        try:
            from atomic_flow.backends.cancellation import cancellation_scope
            with cancellation_scope(self.cancel_check):
                response = (self.backend.chat_tools(messages, tools=native_tools, model=model) if native_tools
                            else self.backend.chat(messages, json_mode=json_mode, model=model))
        except Exception as exc:
            outcome = getattr(exc, "request_outcome", "unknown")
            if outcome in {"not_sent", "refused"}:
                event.update(status="completed", cost_usd=0.0, not_sent=outcome == "not_sent", request_outcome=outcome)
                save(self.task)
                self.ledger.record(task_id=self.task.id, call_id=event["id"], cost_usd=0.0, status=outcome)
                if self.journal:
                    self.journal.append("model_failed", {"phase": self.phase, "error_type": type(exc).__name__},
                                        call_id=event["id"], outcome=outcome)
                raise
            event["status"] = "unknown"
            event["error_type"] = type(exc).__name__  # never store secrets from backend error text
            save(self.task)
            self.ledger.record(task_id=self.task.id, call_id=event["id"], backend=self.name,
                               model=self.config.model, cost_usd=None, reserved_usd=reservation, status="unknown")
            if self.journal:
                self.journal.append("model_failed", {"phase": self.phase, "error_type": type(exc).__name__},
                                    call_id=event["id"], outcome="unknown")
            from atomic_flow.agents.execution import TaskCancelled
            if isinstance(exc, TaskCancelled):
                raise
            raise BudgetExceeded("model request failed; charge is unknown, automatic retries are blocked") from exc
        cost = response.cost_usd
        if cost is None and known_prices and prices == (0, 0):
            cost = 0.0
            response.cost_usd = 0.0
        if cost is None or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            event["status"] = "unknown"
            save(self.task)
            self.ledger.record(task_id=self.task.id, call_id=event["id"], backend=self.name,
                               model=self.config.model, cost_usd=None, reserved_usd=reservation, status="unknown")
            if self.journal:
                self.journal.append("model_response", {"phase": self.phase, "content": response.content},
                                    call_id=event["id"], outcome="unknown")
            raise BudgetExceeded("model response has no valid cost; review usage before continuing")
        event.update(status="completed", cost_usd=cost, tokens_in=response.tokens_in,
                     tokens_out=response.tokens_out, finished_at=time.time(),
                     cache_read_tokens=response.cache_read_tokens, cache_write_tokens=response.cache_write_tokens,
                     cost_source=response.cost_source)
        self.task.budget_spent += cost
        save(self.task)  # includes costs even if parsing/verification/cancellation later fails
        self.ledger.record(task_id=self.task.id, call_id=event["id"], backend=self.name, model=self.config.model,
                           cost_usd=cost, tokens_in=response.tokens_in, tokens_out=response.tokens_out, status="completed")
        if self.journal:
            self.journal.append("model_response", {"phase": self.phase, "content": response.content,
                                "cost_usd": cost, "tokens_in": response.tokens_in, "tokens_out": response.tokens_out},
                                call_id=event["id"], outcome="success")
        self.cancel_check()
        if enforce and (self.task.budget_spent > self.settings.task.max_budget_usd or cost > reservation):
            raise BudgetExceeded("reported model cost exceeded its reservation; task paused before further calls/publication")
        return response
