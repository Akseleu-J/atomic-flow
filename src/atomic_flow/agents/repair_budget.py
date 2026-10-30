"""Durable repair ceiling, shared by every subsequent physical model attempt.

One repair episode per task: replans/resume never refresh its allowance. The
ceiling remains until task completion; independent work drains before activation.
Reservations are estimates; reported overspend is retained and blocks progress.
"""
from __future__ import annotations

import math
import re
import uuid

from atomic_flow.errors import BudgetExceeded


def validate_repair(state):
    if not isinstance(state, dict):
        raise ValueError("repair budget state must be an object")
    if not state:
        return
    if (set(state) != {"version", "id", "cap_usd", "start_spent_usd", "start_event", "failure_step"}
            or type(state["version"]) is not int or state["version"] != 1):
        raise ValueError("invalid repair budget fields/version")
    if not isinstance(state["id"], str) or not re.fullmatch(r"repair_[a-f0-9]{32}", state["id"]):
        raise ValueError("invalid repair budget identity")
    for key in ("cap_usd", "start_spent_usd"):
        if type(state[key]) not in (int, float) or not math.isfinite(state[key]) or state[key] < 0:
            raise ValueError("invalid repair budget amount")
    if state["cap_usd"] <= 0:
        raise ValueError("repair ceiling must be positive")
    if any(type(state[k]) is not int or state[k] < 0 for k in ("start_event", "failure_step")):
        raise ValueError("invalid repair budget event/step")


def check_repair_budget(task, reservation=0.0, *, known_prices=True):
    state = task.repair_budget
    try:
        validate_repair(state)
    except ValueError as exc:
        raise BudgetExceeded(str(exc)) from exc
    if not state:
        return
    if type(reservation) not in (int, float) or not math.isfinite(reservation) or reservation < 0:
        raise BudgetExceeded("invalid repair reservation")
    if not known_prices:
        raise BudgetExceeded("repair budget requires known prices")
    if state["start_event"] > len(task.budget_events) or task.budget_spent < state["start_spent_usd"]:
        raise BudgetExceeded("repair budget accounting moved backwards")
    events = task.budget_events[state["start_event"]:]
    if any(e.get("repair_id") != state["id"] for e in events):
        raise BudgetExceeded("repair budget attribution missing; no automatic reset")
    if any(e.get("status") != "completed" for e in events):
        raise BudgetExceeded("repair has an unresolved model call; reconcile before continuation")
    if any(type(e.get(k)) not in (int, float) or not math.isfinite(e[k]) or e[k] < 0
           for e in events for k in ("cost_usd", "reserved_usd")):
        raise BudgetExceeded("repair model-call cost/reservation is invalid")
    spent = sum(e["cost_usd"] for e in events)
    if abs(task.budget_spent - state["start_spent_usd"] - spent) > 1e-9:
        raise BudgetExceeded("repair budget accounting inconsistent")
    if any(not e.get("not_sent") and e["cost_usd"] > e["reserved_usd"] + 1e-12 for e in events):
        raise BudgetExceeded("repair reported cost exceeded reservation; inspect before continuation")
    if spent + reservation > state["cap_usd"] + 1e-12:
        raise BudgetExceeded("repair budget cannot cover next reservation or reported spend")


def begin_repair(task, cap, step_index):
    if not task.repair_budget:
        state = {"version": 1, "id": "repair_" + uuid.uuid4().hex, "cap_usd": cap,
                 "start_spent_usd": task.budget_spent, "start_event": len(task.budget_events),
                 "failure_step": step_index}
        validate_repair(state)
        task.repair_budget = state
    check_repair_budget(task)
