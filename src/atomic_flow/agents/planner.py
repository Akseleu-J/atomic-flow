"""Planner: break a task into concrete, verifiable steps."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from atomic_flow.backends.base import LLMBackend
from atomic_flow.llm.orchestrator import parse_json
from atomic_flow.llm.schemas import StrictModel


VerifyMethod = Literal["py_compile", "pytest", "none"]


class PlanStep(BaseModel):
    description: str = Field(min_length=5, max_length=500,
                             description="One sentence describing the concrete change.")
    tools_needed: list[str] = Field(default_factory=list,
                                    description="Tool names this step will likely call.")
    expected_outcome: str = Field(default="", max_length=300,
                                  description="What should be true after this step succeeds.")
    verify_with: VerifyMethod = Field(
        default="py_compile",
        description="How to verify this step: py_compile (syntax), pytest (matching tests), none."
    )


class Plan(StrictModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    rationale: str = Field(default="", max_length=1000)


PLANNER_PROMPT = """\
You are the PLANNER. Break the developer's task into 2-12 concrete, verifiable steps.

RULES
- Each step must change code or run a check. Do NOT include "read X", "search for Y",
  "understand Z" — those are absorbed by the Answerer's tool loop automatically.
- Choose `verify_with` carefully:
    * `py_compile` — for edits that do NOT change behavior: docstrings, renames,
      type hints, comments, formatting, imports.
    * `pytest` — ONLY when the step changes logic AND the project has matching tests.
    * `none` — for pure report/summary steps.
- Steps execute sequentially. Step N sees the state produced by step N-1.
- Prefer smaller steps: one function per step, one rename per step.
- If a task is too broad ("refactor everything"), narrow it and state the narrowing
  in `rationale`.

TASK
{query}

CONTEXT
Project root: {project}
Files indexed: {n_files}
Top candidates for this task:
{candidates}

OUTPUT
Return ONE JSON object:
{{
  "steps": [
    {{"description": "...", "tools_needed": ["edit_node"], "expected_outcome": "...", "verify_with": "py_compile"}}
  ],
  "rationale": "one or two sentences explaining the breakdown"
}}
"""


def plan_task(
    query: str,
    *,
    project_root: Path,
    backend: LLMBackend,
    candidates: list[str],
    n_files: int,
) -> Plan:
    """Ask the model for a plan. Returns a validated Plan; on parse failure retries once."""
    prompt = PLANNER_PROMPT.format(
        query=query,
        project=project_root,
        n_files=n_files,
        candidates="\n".join(f"  - {c}" for c in candidates[:30]) or "  (none)",
    )
    messages = [
        {"role": "system", "content": "Reply with ONE JSON object. No prose."},
        {"role": "user", "content": prompt},
    ]
    last_error: Exception | None = None
    for attempt in range(2):
        response = backend.chat(messages, json_mode=True)
        try:
            return parse_json(response.content, Plan)
        except Exception as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": response.content or "{}"})
            messages.append({"role": "user", "content":
                f"Rejected: {exc}. Return ONLY one JSON object with a 'steps' array and 'rationale'."})
    raise last_error if last_error else RuntimeError("planner failed")


REPLAN_PROMPT = """\
You are the PLANNER. One step of an ongoing task failed. Replan the remaining work.

ORIGINAL TASK
{query}

COMPLETED STEPS
{completed}

FAILED STEP
  description: {failed_description}
  verify_with: {verify_with}
  error: {error}

REMAINING AFTER FAILURE
{remaining}

RULES
- Do NOT re-do completed steps.
- Do NOT repeat the failed step verbatim; the previous approach did not work.
- If the failure means the task is infeasible (missing dependency, ambiguous requirement),
  return exactly ONE step: description="STOP: <reason>", verify_with="none".
- Otherwise produce 1-6 concrete steps for the remaining work.

OUTPUT
Return one JSON object: {{"steps": [...], "rationale": "..."}}.
"""


def replan_task(
    query: str,
    *,
    completed: list[str],
    failed_description: str,
    verify_with: str,
    error: str,
    remaining: list[str],
    project_root: Path,
    backend: LLMBackend,
) -> Plan:
    prompt = REPLAN_PROMPT.format(
        query=query,
        completed="\n".join(f"  - {c}" for c in completed) or "  (none)",
        failed_description=failed_description,
        verify_with=verify_with,
        error=error[:600],
        remaining="\n".join(f"  - {r}" for r in remaining) or "  (none)",
    )
    messages = [
        {"role": "system", "content": "Reply with ONE JSON object. No prose."},
        {"role": "user", "content": prompt},
    ]
    last_error: Exception | None = None
    for attempt in range(2):
        response = backend.chat(messages, json_mode=True)
        try:
            return parse_json(response.content, Plan)
        except Exception as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": response.content or "{}"})
            messages.append({"role": "user", "content":
                f"Rejected: {exc}. Return ONLY one JSON object with 'steps' and 'rationale'."})
    raise last_error if last_error else RuntimeError("replan failed")
