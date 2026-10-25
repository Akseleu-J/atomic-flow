"""Opt-in, bounded internal replans; summaries never grant execution authority.

The predicate checks a host-created envelope. The runner must also enforce the
approved source/scope, current runtime binding, accounting and effect holds.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

MUTATING_TOOLS = frozenset({"edit_node", "patch_file", "notebook_edit", "create_file", "move_file", "delete_file"})
SOURCE_TOOLS = frozenset({"read_node", "find_callers", "search_tree", "search_text", "read_result",
                          "read_file", "find_files", "notebook_read"})
SUPPORTED_TOOLS = SOURCE_TOOLS | MUTATING_TOOLS | {"run_command", "ask_user"}
VERIFY_KINDS = {"none": 0, "py_compile": 1, "pytest": 2}
ENVELOPE_FIELDS = {"version", "allowed_tools", "required_verify", "max_budget_usd", "max_model_calls",
                   "max_replans", "runtime_digest", "replan_failures"}


def validate_envelope(value: Any) -> None:
    """Strict persisted shape; malformed, nonfinite or unbounded data is refused."""
    if not isinstance(value, dict) or set(value) != ENVELOPE_FIELDS:
        raise ValueError("invalid internal replan envelope fields")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid internal replan envelope version")
    tools = value["allowed_tools"]
    if (not isinstance(tools, list) or any(not isinstance(t, str) for t in tools)
            or tools != sorted(set(tools)) or not set(tools) <= SUPPORTED_TOOLS):
        raise ValueError("invalid internal replan allowed tools")
    if not isinstance(value["required_verify"], str) or value["required_verify"] not in VERIFY_KINDS:
        raise ValueError("invalid internal replan verification floor")
    budget = value["max_budget_usd"]
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("internal replans require a finite positive enforced budget")
    for name, maximum in (("max_model_calls", 1000), ("max_replans", 5)):
        if type(value[name]) is not int or not 1 <= value[name] <= maximum:
            raise ValueError(f"invalid internal replan {name}")
    if not isinstance(value["runtime_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["runtime_digest"]):
        raise ValueError("invalid internal replan runtime digest")
    history = value["replan_failures"]
    if (not isinstance(history, list) or len(history) > 5
            or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in history)):
        raise ValueError("invalid internal replan failure history")


def runtime_digest(config, project: Path, backend=None) -> str:
    """Bind declared runtime, trust and policies; never persist raw credentials."""
    from atomic_flow.config import is_trusted, user_dir

    settings = config.model_dump(mode="json")
    for entry in settings.get("backends", {}).values():
        entry.pop("api_key", None)
    selected = None
    if backend is not None:
        actual = getattr(backend, "config", None)
        if actual is None or not hasattr(actual, "model_dump") or hasattr(backend, "select_attempt"):
            raise ValueError("internal replans require a fixed configured backend")
        selected = actual.model_dump(mode="json", exclude={"api_key"})
        selected["max_output_tokens"] = selected.get("max_output_tokens") or config.task.default_max_output_tokens
        selected["max_retries"] = 0
        selected = {"name": getattr(backend, "name", ""), "config": selected}
    policies = []
    for path in (user_dir() / "policy.yaml", project / ".atomic_flow" / "policy.yaml"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("internal replan policy cannot be a link")
        if path.exists():
            with path.open("rb") as handle:
                data = handle.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("internal replan policy exceeds byte limit")
            policies.append(hashlib.sha256(data).hexdigest())
        else:
            policies.append(None)
    raw = {"config": settings, "backend": selected, "trusted": is_trusted(project), "policies": policies}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()


def make_envelope(task, config, project: Path, backend=None) -> dict:
    if not config.task.fail_on_budget_exceeded:
        raise ValueError("internal replans require enforced task budget")
    if config.routing.mode != "fixed":
        raise ValueError("internal replans require fixed backend routing in this version")
    steps = [step for step in task.steps if step.status != "superseded"]
    tools = set().union(*(set(step.tools_needed) for step in steps))
    if not tools <= SUPPORTED_TOOLS:
        raise ValueError("internal replans support built-in source/check tools only")
    if not steps or any(step.verify_with not in VERIFY_KINDS for step in steps):
        raise ValueError("internal replans need a valid verification obligation")
    value = {"version": 1, "allowed_tools": sorted(tools | SOURCE_TOOLS),
             "required_verify": max((step.verify_with for step in steps), key=VERIFY_KINDS.get),
             "max_budget_usd": config.task.max_budget_usd, "max_model_calls": config.task.max_model_calls,
             "max_replans": config.task.max_replans, "runtime_digest": runtime_digest(config, project, backend),
             "replan_failures": []}
    validate_envelope(value)
    return value


def failure_signature(failure: str) -> str:
    lines = (failure or "").strip().splitlines()
    return lines[0][:500] if lines else ""


def no_progress(approval: dict[str, Any], failure: str, *, limit: int = 2) -> bool:
    """Exact recurring first-line failures; the independent replan cap also applies."""
    signature = failure_signature(failure)
    if not signature:
        return False
    envelope = approval.get("internal_replan", approval)
    history = envelope.get("replan_failures", []) if isinstance(envelope, dict) else []
    return isinstance(history, list) and history.count(signature) >= limit


def envelope_preserved(approval: dict[str, Any], new_steps: list, *, failure: str,
                       budget_spent: float) -> tuple[bool, list[str]]:
    """Fail closed for legacy approval, tool widening or weaker verification.

    This is not a source/sandbox check. The runner validates source and runtime
    before calling it and applies its tool set to the actual dispatcher.
    """
    if not isinstance(approval, dict) or approval.get("schema_version") != 2:
        return False, ["internal replan was not explicitly approved; fresh review required"]
    try:
        envelope = approval["internal_replan"]
        validate_envelope(envelope)
    except (KeyError, TypeError, ValueError) as exc:
        return False, [f"invalid internal replan envelope: {exc}"]
    reasons = []
    if type(budget_spent) not in (int, float) or not math.isfinite(budget_spent) or budget_spent < 0:
        reasons.append("invalid budget accounting")
    elif budget_spent >= envelope["max_budget_usd"]:
        reasons.append("budget reached the approved cap")
    if not new_steps:
        reasons.append("replan has no verifiable steps")
    allowed = set(envelope["allowed_tools"])
    proposed_tools = {tool for step in new_steps for tool in (step.tools_needed or [])}
    if MUTATING_TOOLS & allowed and not MUTATING_TOOLS & proposed_tools:
        reasons.append("replan would drop the approved verified edit obligation")
    if "run_command" in allowed and "run_command" not in proposed_tools and not any(
            step.verify_with == "pytest" for step in new_steps):
        reasons.append("replan would drop the approved execution/check obligation")
    for step in new_steps:
        unknown = set(step.tools_needed or []) - allowed
        if unknown:
            reasons.append(f"step {step.index}: tools outside approved envelope: {sorted(unknown)}")
        if MUTATING_TOOLS & set(step.tools_needed or []) and not MUTATING_TOOLS & allowed:
            reasons.append(f"step {step.index}: introduces a mutating tool the plan never had")
        if VERIFY_KINDS.get(step.verify_with, -1) < VERIFY_KINDS[envelope["required_verify"]]:
            reasons.append(f"step {step.index}: verify_with would weaken the approved verification obligation")
        if step.verify_with == "pytest" and envelope["required_verify"] != "pytest" and "run_command" not in allowed:
            reasons.append(f"step {step.index}: pytest execution was not approved")
    if no_progress(approval, failure):
        reasons.append("no-progress guard: recurring failure requires review")
    return not reasons, reasons
