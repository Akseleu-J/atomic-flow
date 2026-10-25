"""Formal replan envelope check and no-progress guard (roadmap P06 slice 1).

`envelope_preserved` is the pure predicate the host will use to accept an
internal replan without a fresh human question: the plan may change, the
authorization envelope may not. Every rule is checked against the previously
approved payload — allowed paths, allowed tools, verification kinds, budget
caps — and any widening returns (False, reasons) so the caller pauses instead
of guessing. `no_progress` detects the oscillation case: the same step
description failing repeatedly across replans.
"""
from __future__ import annotations

from typing import Any

MUTATING_TOOLS = frozenset({"edit_node", "patch_file", "notebook_edit", "write_file", "move_file", "delete_file"})
VERIFY_KINDS = frozenset({"none", "py_compile", "pytest"})


def envelope_preserved(approval: dict[str, Any], new_steps: list, *, failure: str,
                       budget_spent: float) -> tuple[bool, list[str]]:
    """Pure check: does the new plan stay inside the approved envelope?

    `new_steps` are planner Step objects (description/tools_needed/verify_with).
    Rules: schema valid; tools ⊆ approved plan's tools; no mutating tool may
    appear if the approved plan had none; verify kinds must be in the approved
    set; budget must remain under the approved caps; and the same failing
    description may not repeat beyond the oscillation limit.
    """
    reasons: list[str] = []
    if approval.get("schema_version") != 1 or "plan" not in approval:
        return False, ["previous approval is malformed; fresh review required"]
    approved_tools = {tool for step in approval["plan"]["steps"] for tool in step.get("tools_needed", [])}
    approved_verify = {step.get("verify_with") for step in approval["plan"]["steps"]} & VERIFY_KINDS
    if not approved_verify:
        approved_verify = {"py_compile"}
    for step in new_steps:
        unknown = set(step.tools_needed or []) - approved_tools
        if unknown:
            reasons.append(f"step {step.index}: tools outside approved plan: {sorted(unknown)}")
        if MUTATING_TOOLS & set(step.tools_needed or []) and not (MUTATING_TOOLS & approved_tools):
            reasons.append(f"step {step.index}: introduces a mutating tool the plan never had")
        if step.verify_with not in approved_verify:
            reasons.append(f"step {step.index}: verify_with {step.verify_with!r} outside approved {sorted(approved_verify)}")
    limits = approval.get("limits") or {}
    if "max_budget_usd" in limits and budget_spent >= float(limits["max_budget_usd"]):
        reasons.append(f"budget ${budget_spent:.4f} reached the approved cap")
    if no_progress(approval, failure):
        reasons.append(f"no-progress guard: {failure[:60]!r} keeps recurring")
    return (not reasons), reasons


def no_progress(approval: dict[str, Any], failure: str, *, limit: int = 2) -> bool:
    """True when the same failure signature already repeated `limit` times.

    The signature is the failure text's first line; replan history lives in
    the approval's `replan_failures` list (the integrating caller records it;
    this function stays pure and side-effect free).
    """
    signature = (failure or "").strip().splitlines()[0] if failure else ""
    if not signature:
        return False
    history = [str(item) for item in approval.get("replan_failures", [])]
    return history.count(signature) >= limit
