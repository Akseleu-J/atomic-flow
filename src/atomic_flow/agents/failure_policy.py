"""Failure classification → stop/continue decision (roadmap P06 §7.4).

Pure table: no heuristics over model text. The caller supplies an explicit
failure kind plus durable facts; the function returns the mandated decision
and the obligations that accompany it. Anything unrecognized stops — new
failure kinds require an explicit contract change, not a default continue.
"""
from __future__ import annotations

from typing import Any

CONTINUE_REPAIR = "continue_bounded_repair"
REREAD_REVIEW = "reread_and_fresh_review"
RECONCILE_HOLD = "durable_hold_reconcile"
ASK_USER = "pause_user_decision"
STOP = "stop"

_ACTIONS = {
    "deterministic_test_failure": CONTINUE_REPAIR,
    "source_conflict": REREAD_REVIEW,
    "stale_approval_digest": REREAD_REVIEW,
    "missing_dependency": ASK_USER,          # environment path needs a grant
    "unknown_submission": RECONCILE_HOLD,
    "unknown_charge": RECONCILE_HOLD,
    "unknown_effect": RECONCILE_HOLD,
    "authority_expansion_requested": ASK_USER,
    "scope_violation_detected": STOP,
    "budget_exhausted": STOP,
    "deadline_reached": STOP,
    "no_progress": STOP,
    "cancel_requested": STOP,
}

_REPAIR_BUDGET_FRACTION = 0.25  # bounded repair may use a quarter of remaining budget


def classify(failure: dict[str, Any]) -> dict[str, Any]:
    """Map an explicit failure record to the mandated decision."""
    kind = failure.get("kind")
    if kind not in _ACTIONS:
        return {"action": STOP, "reason": f"unclassified failure kind: {kind!r}; contract change required"}
    action = _ACTIONS[kind]
    decision: dict[str, Any] = {"action": action, "kind": kind}
    if action == CONTINUE_REPAIR:
        remaining = failure.get("remaining_budget_usd")
        if remaining is None or float(remaining) <= 0:
            return {"action": STOP, "kind": kind, "reason": "no remaining budget for bounded repair"}
        decision["repair_budget_usd"] = round(float(remaining) * _REPAIR_BUDGET_FRACTION, 6)
        decision["requires"] = "envelope_preserved AND lineage_preserved AND fresh failing-check evidence"
    elif action == REREAD_REVIEW:
        decision["requires"] = "fresh source read + new approval digest; replan never relaxes preconditions"
    elif action == RECONCILE_HOLD:
        decision["requires"] = "host reconciliation before any retry/failover/publish; unknown stays unknown"
    elif action == ASK_USER:
        decision["requires"] = "explicit owner grant for the widened resource"
    else:
        decision["requires"] = "preserve state, costs and effects; terminal record"
    return decision
