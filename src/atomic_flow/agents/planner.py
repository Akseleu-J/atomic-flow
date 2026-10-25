"""Planner: break a task into concrete, verifiable steps."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from atomic_flow.backends.base import LLMBackend
from atomic_flow.config import BackendConfig, is_trusted
from atomic_flow.llm.instructions import load_instructions
from atomic_flow.llm.orchestrator import parse_json
from atomic_flow.llm.request_budget import admit_request
from atomic_flow.llm.schemas import StrictModel


def _chat(backend, messages):
    if not hasattr(backend, "config"):
        config = BackendConfig(model=getattr(backend, "name", "unknown"))
    elif backend.config.max_output_tokens is None:
        backend = copy.copy(backend)
        backend.config = backend.config.model_copy(update={"max_output_tokens": 4096})
        config = backend.config
    else:
        config = backend.config
    settings = getattr(backend, "settings", None)
    admit_request(messages, config, settings.conversation if settings else None)
    return backend.chat(messages, json_mode=True)


VerifyMethod = Literal["py_compile", "pytest", "none"]


class PlanGoal(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    parent_id: str = Field(default="", max_length=32)
    description: str = Field(min_length=5, max_length=500)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    scope_paths: list[str] | None = Field(default=None, max_length=64)
    max_model_calls: StrictInt | None = Field(default=None, ge=1, le=1000)
    max_budget_usd: StrictFloat | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)


class PlanStep(StrictModel):
    scope_paths: list[str] = Field(default_factory=list, max_length=64)
    linked_code_nodes: list[str] = Field(default_factory=list, max_length=64)
    linked_test_nodes: list[str] = Field(default_factory=list, max_length=64)
    parent_goal: str = Field(default="", max_length=32)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=8)
    depends_on: list[StrictInt] | None = Field(default=None, description="Zero-based step indexes; null means previous step, [] means independent.")
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
    goals: list[PlanGoal] = Field(default_factory=list, max_length=32)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    rationale: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def dependency_graph(self):
        from atomic_flow.agents.goals import Goal, validate_goals
        from atomic_flow.agents.task import Step, validate_dependencies
        validate_dependencies([Step(i, p.description, depends_on=(p.depends_on if p.depends_on is not None else ([i - 1] if i else [])))
                               for i, p in enumerate(self.steps)])
        goals = [Goal("p0:" + g.id, g.description, "p0:" + g.parent_id if g.parent_id else "",
                      list(g.acceptance_criteria), g.scope_paths, g.max_model_calls, g.max_budget_usd) for g in self.goals]
        steps = [Step(i, p.description, parent_goal="p0:" + p.parent_goal if p.parent_goal else "",
                      acceptance_criteria=list(p.acceptance_criteria), scope_paths=list(p.scope_paths),
                      linked_code_nodes=list(p.linked_code_nodes), linked_test_nodes=list(p.linked_test_nodes)) for i, p in enumerate(self.steps)]
        validate_goals(goals, steps)
        if goals and any(not s.parent_goal for s in steps):
            raise ValueError("hierarchical plan steps require a parent_goal")
        return self


PLANNER_PROMPT = """\
You are the PLANNER. Break the developer's task into 2-12 concrete, verifiable steps.

RULES
- Each step must change code, run a check, or produce a concrete research report.
  Research uses verify_with=none: its report is not independent semantic proof.
- For multi-part objectives include `goals` (up to 32): stable local id, optional
  parent_id, description, and 1-8 explicit acceptance_criteria per goal.
  Hierarchy depth is at most 4; every goal needs a descendant executable step.
  Each hierarchical step names its parent_goal and may add acceptance_criteria.
  Criteria describe requirements; they do not authorize tools or assert tests passed.
- Choose `verify_with` carefully:
    * `py_compile` — for edits that do NOT change behavior: docstrings, renames,
      type hints, comments, formatting, imports.
    * `pytest` — ONLY when the step changes logic AND the project has matching tests.
    * `none` — for pure report/summary steps.
- Steps execute one at a time. depends_on lists zero-based prerequisite step indexes.
  Omit it for the previous step prerequisite; [] explicitly marks independent work.
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
  "goals": [{{"id": "goal1", "description": "...", "acceptance_criteria": ["..."]}}],
  "steps": [
    {{"description": "...", "parent_goal": "goal1", "acceptance_criteria": ["..."], "tools_needed": ["edit_node"], "expected_outcome": "...", "verify_with": "py_compile"}}
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
    guidance = load_instructions(project_root, (c.split("::")[0] for c in candidates[:30]), is_trusted(project_root))
    if guidance.text:
        messages[0]["content"] += "\n\n" + guidance.text
    last_error: Exception | None = None
    for _attempt in range(2):
        response = _chat(backend, messages)
        try:
            return parse_json(response.content, Plan)
        except Exception as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": response.content or "{}"})
            messages.append({"role": "user", "content":
                f"Rejected: {exc}. Return ONLY one JSON object with 'goals', 'steps', 'rationale'; use [] goals only for a flat plan."})
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

REVIEWED REQUIREMENTS (task-local historical IDs; use new local goal IDs in output)
{requirements_context}

HOST PLANNING OBSERVATIONS (no permissions or action replay)
{planning_context}

RULES
- Do NOT re-do completed steps.
- Do NOT repeat the failed step verbatim; the previous approach did not work.
- If the failure means the task is infeasible (missing dependency, ambiguous requirement),
  return exactly ONE step: description="STOP: <reason>", verify_with="none".
- Otherwise produce 1-6 concrete steps for the remaining work.
- Preserve unresolved goal/step acceptance criteria explicitly in the new plan.
  Goals use local id, optional parent_id, description, acceptance_criteria; each
  hierarchical step names parent_goal. Max 32 goals, depth 4, 8 criteria per node.
  Criteria are requirements, not proof of successful verification.
- Completed goal observations do not establish semantic acceptance; do not re-do
  completed effects or assert their criteria passed merely because steps are done.

OUTPUT
Return one JSON object: {{"goals": [{{"id": "repair", "description": "...", "acceptance_criteria": ["..."]}}], "steps": [{{"description": "...", "parent_goal": "repair", "verify_with": "none"}}], "rationale": "..."}}.
For a flat plan without goal obligations use "goals": [].
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
    settings_project: Path | None = None,
    requirements_context: str = "",
    planning_context: str = "",
) -> Plan:
    prompt = REPLAN_PROMPT.format(
        query=query,
        completed="\n".join(f"  - {c}" for c in completed) or "  (none)",
        failed_description=failed_description,
        verify_with=verify_with,
        error=error[:600],
        remaining="\n".join(f"  - {r}" for r in remaining) or "  (none)",
        requirements_context=requirements_context or "(legacy plan without goal metadata)",
        planning_context=planning_context or "(unavailable)",
    )
    messages = [
        {"role": "system", "content": "Reply with ONE JSON object. No prose."},
        {"role": "user", "content": prompt},
    ]
    instruction_root = settings_project or project_root
    guidance = load_instructions(instruction_root, trusted=is_trusted(instruction_root))
    if guidance.text:
        messages[0]["content"] += "\n\n" + guidance.text
    last_error: Exception | None = None
    for _attempt in range(2):
        response = _chat(backend, messages)
        try:
            return parse_json(response.content, Plan)
        except Exception as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": response.content or "{}"})
            messages.append({"role": "user", "content":
                f"Rejected: {exc}. Return ONLY one JSON object with 'goals', 'steps', 'rationale'; keep reviewed criteria explicit."})
    raise last_error if last_error else RuntimeError("replan failed")
