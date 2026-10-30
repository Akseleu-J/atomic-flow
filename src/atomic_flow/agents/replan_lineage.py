"""Hierarchical replan lineage check (roadmap P06, ADR-0001 item 2/3).

Pure predicate: a replacement plan may drop NOTHING the owner obligated —
criteria coverage, scope narrowing, ancestor budget caps, required final
checks. A new subgoal namespace inherits obligations; model-text similarity
is never consulted. Ambiguity fails closed into (False, reasons) so the
caller pauses for fresh review instead of guessing.
"""
from __future__ import annotations

from typing import Any


def verification_lineage_kind(method: str, *, host_ordinary_test: bool = False) -> str:
    """Transient comparison only; persisted contracts keep their exact labels."""
    if type(host_ordinary_test) is not bool:
        raise ValueError("host ordinary-test binding must be explicit")
    return "ordinary_test" if host_ordinary_test and method in {"pytest", "ordinary_test"} else method


def lineage_preserved(obligations: dict[str, Any], new_steps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Check obligations against a normalized replacement plan.

    obligations:
      criteria: list[str]            acceptance criteria texts (goal level)
      scope_paths: list[str]|None    None inherits (not checked here); a list
                                     must stay covered by the plan's steps
      max_budget_usd: float|None     ancestor cap; the plan must not raise it
      required_checks: set[str]      final verification kinds that must remain
    new_steps: dicts with keys scope_paths (list|None), checks (list[str]),
      criteria (list[str] of criteria texts this step serves), budget_usd
      (float|None, step-local cap).
    """
    reasons: list[str] = []
    if not isinstance(obligations, dict) or not isinstance(new_steps, list) or not new_steps:
        return False, ["malformed lineage input; fresh review required"]

    covered_criteria: set[str] = set()
    for step in new_steps:
        covered_criteria.update(step.get("criteria") or [])

    for criterion in obligations.get("criteria") or []:
        if criterion not in covered_criteria:
            reasons.append(f"criterion lost by replan: {criterion[:60]!r}")

    scope = obligations.get("scope_paths")
    if scope is not None:
        allowed: set[str] = set(scope)
        for step in new_steps:
            step_scope = step.get("scope_paths")
            if step_scope is None:
                continue
            outside = set(step_scope) - allowed
            if outside:
                reasons.append(f"step writes outside obligated scope: {sorted(outside)}")
        # Scope is only narrowed, never widened: the union of explicit step
        # scopes plus inherited steps must stay inside the obligated set.
        # (A replan cannot ADD paths; absent step scope inherits the goal.)

    ancestor_cap = obligations.get("max_budget_usd")
    if ancestor_cap is not None:
        for step in new_steps:
            step_cap = step.get("budget_usd")
            if step_cap is not None and float(step_cap) > float(ancestor_cap):
                reasons.append(f"step budget {step_cap} exceeds ancestor cap {ancestor_cap}")

    required = set(obligations.get("required_checks") or ())
    if required:
        plan_checks: set[str] = set()
        for step in new_steps:
            plan_checks.update(step.get("checks") or ())
        missing = required - plan_checks
        if missing:
            reasons.append(f"required final checks absent from replan: {sorted(missing)}")

    return (not reasons), reasons
